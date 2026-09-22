"""Content-policy refusals.

A prompt blocklist matches terms and has no concept of negation. That makes it
hostile to exactly the thing a careful brief does: a clause written to keep a
trademark *out* of the picture reads to the filter as a prompt asking for one,
and the whole request is refused.

The refusal arrives as a plain 400, which this system used to report as "check
width/height are within the model's pixel budget" -- pointing whoever was
debugging it at the one part of the request that was provably correct.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.copy.brief_compiler import _scrub_prohibitions
from app.copy.strategy import BrandKit, CreativeBrief, render_prompt
from app.foundry.flux_client import _as_block
from app.foundry.image_client import MaiError, PromptBlocked
from app.main import RUNNING_STATES, app

BLOCK_BODY = (
    '{"error":{"code":"content_safety_violation","message":"Content violated '
    'RAI policy blocking criteria (BingBlockList_Prompt).","details":"Content '
    'violated RAI policy blocking criteria (BingBlockList_Prompt)."}}'
)


# --------------------------------------------------------------------------
# Telling a refusal apart from a malformed request
# --------------------------------------------------------------------------

def test_a_policy_refusal_is_not_reported_as_a_dimension_problem() -> None:
    blocked = _as_block(400, BLOCK_BODY)
    assert isinstance(blocked, PromptBlocked)
    assert blocked.policy == "BingBlockList_Prompt"
    assert blocked.code == "content_safety_violation"
    # The old message sent people to check width and height, which were fine.
    assert "width" not in str(blocked)
    assert "billed" in str(blocked)


def test_an_ordinary_bad_request_is_left_alone() -> None:
    # Only refusals get reclassified; a real 400 must keep its own hint.
    assert _as_block(400, '{"error":{"message":"width must be >= 768"}}') is None
    assert _as_block(404, BLOCK_BODY) is None


def test_a_truncated_body_is_still_recognised() -> None:
    """The body is clipped to 400 chars before it gets here, so it may not
    parse as JSON. The markers still identify what happened."""
    blocked = _as_block(400, BLOCK_BODY[:60])
    assert isinstance(blocked, PromptBlocked)


def test_prompt_blocked_is_a_mai_error() -> None:
    # Existing handlers that catch MaiError must not stop catching this.
    assert issubclass(PromptBlocked, MaiError)


# --------------------------------------------------------------------------
# Not writing the trigger in the first place
# --------------------------------------------------------------------------

def test_a_prohibition_naming_a_brand_is_dropped() -> None:
    kept = _scrub_prohibitions((
        "visible third-party branding",
        "any Nike swoosh on the midsole",
        "no Adidas three-stripe motif",
    ))
    assert kept == ("visible third-party branding",)


def test_ordinary_prohibitions_survive() -> None:
    # The scrub must not eat the clauses that do the actual work, including
    # the ones that legitimately start with a capital.
    items = (
        "the exact subject or silhouette of the reference",
        "any visible logo, wordmark or packaging text",
        "Any recognisable face from the reference",
    )
    assert _scrub_prohibitions(items) == items


def test_omitting_authored_prohibitions_keeps_every_clause_we_own() -> None:
    brief = CreativeBrief(
        subject="a pair of low-top leather sneakers",
        must_not_depict=("any Nike swoosh",),
    )
    reduced = render_prompt(brief, omit_authored_prohibitions=True)

    assert "Nike" not in reduced
    # The two clauses the rest of the pipeline depends on are not negotiable:
    # text in the base would defeat the overlay, and losing the reserved
    # region would put the logo on top of the subject.
    assert "no text" in reduced
    assert "reserved for later graphic overlay" in reduced
    # Our own fixed prohibitions stay -- they are not what tripped the filter.
    assert "no any map" in reduced


# --------------------------------------------------------------------------
# Recovery, end to end
# --------------------------------------------------------------------------

@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _await_job(client: AsyncClient, job_id: str, timeout: float = 120.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        payload = (await client.get(f"/api/jobs/{job_id}")).json()
        if payload["state"] not in RUNNING_STATES:
            return payload
        await asyncio.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish")


async def test_a_refused_prompt_is_retried_without_the_authored_clauses(
    tmp_path,
) -> None:
    """The recovery that matters: a free retry, because a refusal is not billed."""
    from app.copy.strategy import CreativeStrategy
    from app.foundry.mock_client import MockImageClient
    from app.pipeline import CampaignPipeline

    calls: list[str] = []
    inner = MockImageClient()

    class RefusesOnce:
        async def generate(self, *, prompt, width, height, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                raise PromptBlocked(
                    "refused", status=400, policy="BingBlockList_Prompt"
                )
            return await inner.generate(
                prompt=prompt, width=width, height=height, **kwargs
            )

        async def edit(self, **kwargs):
            return await inner.edit(**kwargs)

    pipeline = CampaignPipeline(RefusesOnce(), storage=tmp_path)
    try:
        result = await pipeline.run(
            campaign_id="retry",
            brief=CreativeBrief(
                subject="a pair of low-top leather sneakers",
                must_not_depict=("any Nike swoosh on the midsole",),
            ),
            strategy=CreativeStrategy(proposition="sneakers", benefit=""),
            brand=BrandKit(name="ACME"),
            formats=("portrait",),
            locales=("en",),
        )
    finally:
        await pipeline.aclose()

    assert len(calls) == 2, "the refusal was not retried"
    # The retry has to be a different prompt -- sending the same text again
    # would just be refused again, at the cost of another round trip.
    assert "Nike" in calls[0] and "Nike" not in calls[1]
    assert any("refused the first prompt" in note for note in result.notes), (
        "the campaign silently became a different creative than the one briefed"
    )


async def test_a_refusal_with_nothing_to_drop_is_not_retried(tmp_path) -> None:
    """Retrying an identical prompt is a wasted round trip, not a recovery."""
    from app.copy.strategy import CreativeStrategy
    from app.pipeline import CampaignPipeline

    calls: list[str] = []

    class AlwaysRefuses:
        async def generate(self, *, prompt, width, height, **kwargs):
            calls.append(prompt)
            raise PromptBlocked("refused", status=400)

        async def edit(self, **kwargs):
            raise AssertionError("not the path under test")

    pipeline = CampaignPipeline(AlwaysRefuses(), storage=tmp_path)
    try:
        with pytest.raises(PromptBlocked):
            await pipeline.run(
                campaign_id="noretry",
                brief=CreativeBrief(subject="a bottle"),  # no authored clauses
                strategy=CreativeStrategy(proposition="a bottle", benefit=""),
                brand=BrandKit(name="ACME"),
                formats=("portrait",),
                locales=("en",),
            )
    finally:
        await pipeline.aclose()

    assert len(calls) == 1


async def test_a_prompt_refused_twice_reports_a_block_not_a_crash(
    client: AsyncClient, monkeypatch
) -> None:
    async def always_blocked(self, *, prompt, width, height, **kwargs):
        raise PromptBlocked(
            "the image service refused the prompt on content-policy grounds "
            "[BingBlockList_Prompt]. Nothing was generated and nothing was billed.",
            status=400, policy="BingBlockList_Prompt",
        )

    from app.foundry.mock_client import MockImageClient

    monkeypatch.setattr(MockImageClient, "generate", always_blocked)

    created = (
        await client.post(
            "/api/campaigns",
            json={"product": "sneakers", "formats": ["portrait"], "locales": ["en"]},
        )
    ).json()
    job = await _await_job(client, created["job_id"])

    assert job["state"] == "blocked", "a refusal must not read as a generic failure"
    assert "content-policy" in job["error"]
    # The refused text is the whole point of the report.
    assert job["prompt"], "the prompt that was refused was thrown away"


async def test_blocked_is_terminal_for_a_polling_client(
    client: AsyncClient, monkeypatch
) -> None:
    """A state missing from the client's stop condition spins forever, and
    shows up to the user as 'nothing appeared' rather than as an error."""
    assert "blocked" not in RUNNING_STATES
