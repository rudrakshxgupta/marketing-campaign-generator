"""Subject preservation.

When a user uploads a photo of something they actually sell, the model must
restage it, not redesign it.

Real estate is the sharpest case and drives the thresholds: a building that
gains a storey or loses a window renders beautifully and advertises a property
that does not exist. That is a legal exposure, not a quality complaint, and it
is invisible to whoever is running the tool.
"""

from __future__ import annotations

import asyncio
import io
import random

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

from app.copy.fidelity import (
    FIDELITY_FLOOR,
    SubjectKind,
    detect_kind,
    preservation_clause,
)
from app.copy.strategy import CreativeBrief, render_edit_prompt
from app.imaging.fidelity import compare, structure_mask, structure_threshold
from app.imaging.fidelity import _grey, _window_edge_energy
from app.main import app


def building(
    floors: int = 4,
    windows: int = 3,
    sky: tuple[int, int, int] = (150, 180, 210),
    facade: tuple[int, int, int] = (196, 186, 170),
    bokeh: bool = False,
) -> Image.Image:
    """A crude but structurally honest building.

    Floors and windows are the features that matter: both are hard edges, and
    both are what a model silently changes.
    """
    image = Image.new("RGB", (600, 800), sky)
    draw = ImageDraw.Draw(image)

    if bokeh:
        random.seed(1)
        for _ in range(40):
            x, y = random.randint(0, 600), random.randint(0, 280)
            draw.ellipse(
                [x, y, x + 26, y + 26],
                fill=tuple(min(255, c + d) for c, d in zip(sky, (18, 12, 6))),
            )

    draw.rectangle([100, 780 - floors * 140, 500, 780], fill=facade)
    for floor in range(floors):
        y = 770 - (floor + 1) * 140 + 30
        for window in range(windows):
            x = 130 + window * 115
            draw.rectangle([x, y, x + 72, y + 80], fill=(70, 90, 110))
    draw.rectangle([0, 780, 600, 800], fill=(120, 120, 115))
    return image


def as_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Detection of changes that matter
# --------------------------------------------------------------------------

def test_an_unchanged_subject_passes() -> None:
    report = compare(building(), building(), SubjectKind.ARCHITECTURE)
    assert report.passed
    assert report.similarity == pytest.approx(1.0, abs=0.001)


def test_restaging_the_background_is_allowed() -> None:
    """The point of the edit path. Changing the sky must not be a violation.

    This is why fidelity cannot be whole-frame similarity: recolouring the sky
    moves most of the pixels in the frame while leaving the building alone.
    """
    original = building()
    for name, restaged in [
        ("sky recoloured", building(sky=(250, 205, 150))),
        ("bokeh added", building(sky=(120, 150, 190), bokeh=True)),
    ]:
        report = compare(original, restaged, SubjectKind.ARCHITECTURE)
        assert report.passed, f"{name} was wrongly rejected: {report.reason}"


def test_an_extra_floor_is_caught() -> None:
    """The failure this whole feature exists for."""
    report = compare(building(floors=4), building(floors=5), SubjectKind.ARCHITECTURE)
    assert not report.passed
    assert "outline" in report.reason


def test_an_extra_window_is_caught() -> None:
    report = compare(building(windows=3), building(windows=4), SubjectKind.ARCHITECTURE)
    assert not report.passed


def test_a_changed_facade_is_caught() -> None:
    report = compare(
        building(facade=(196, 186, 170)),
        building(facade=(90, 70, 60)),
        SubjectKind.ARCHITECTURE,
    )
    assert not report.passed
    assert "subject itself changed" in report.reason


def test_the_structure_threshold_comes_from_the_original_only() -> None:
    """Deriving it per image would be a bug.

    Recolouring the sky moves that frame's statistics, which would move its
    threshold, which would silently reclassify the building -- and the subject
    would appear to have moved when nothing about it changed.
    """
    original, restaged = building(), building(sky=(250, 205, 150))
    threshold = structure_threshold(_window_edge_energy(_grey(original)))

    mask_a, _, _ = structure_mask(_grey(original), threshold)
    mask_b, _, _ = structure_mask(_grey(restaged), threshold)

    # Same building, same definition of structure -> essentially the same mask.
    overlap = len(mask_a & mask_b) / max(1, len(mask_a | mask_b))
    assert overlap > 0.95, "the background change disturbed the subject mask"


def test_architecture_is_held_to_a_stricter_standard() -> None:
    # Misstating a property is a legal exposure; a slightly different plate of
    # food is not.
    assert FIDELITY_FLOOR[SubjectKind.ARCHITECTURE] > FIDELITY_FLOOR[SubjectKind.FOOD]
    assert FIDELITY_FLOOR[SubjectKind.JEWELLERY] >= 0.92


# --------------------------------------------------------------------------
# The prompt side
# --------------------------------------------------------------------------

def test_the_architecture_clause_names_what_models_actually_get_wrong() -> None:
    """Generic wording is too weak.

    A model told only "keep the building the same" still changes the floor
    count, because it has no reason to think that is what "the same" means.
    """
    clause = preservation_clause(SubjectKind.ARCHITECTURE).lower()
    for feature in ("floors", "window", "balcon", "roofline", "facade", "proportions"):
        assert feature in clause, f"clause does not pin {feature}"
    assert "real, existing property" in clause


def test_each_subject_kind_pins_its_own_failure_modes() -> None:
    assert "label" in preservation_clause(SubjectKind.PRODUCT).lower()
    assert "stone" in preservation_clause(SubjectKind.JEWELLERY).lower()
    assert "drape" in preservation_clause(SubjectKind.APPAREL).lower()
    assert "ingredient" in preservation_clause(SubjectKind.FOOD).lower()
    # A person must not be quietly retouched.
    person = preservation_clause(SubjectKind.PERSON).lower()
    assert "skin tone" in person and "lighten" in person


def test_the_edit_prompt_carries_the_clause() -> None:
    prompt = render_edit_prompt(
        "put it on a bright morning street",
        subject_kind=SubjectKind.ARCHITECTURE,
    ).lower()
    assert "number of floors" in prompt
    assert "do not add any new text" in prompt


def test_preservation_can_be_turned_off_deliberately() -> None:
    prompt = render_edit_prompt("reimagine this", preserve_subject=False).lower()
    assert "number of floors" not in prompt


@pytest.mark.parametrize(
    "text,expected",
    [
        ("3 BHK apartment in Pune with balcony", SubjectKind.ARCHITECTURE),
        ("our new villa project, ready to move", SubjectKind.ARCHITECTURE),
        ("storefront of our showroom", SubjectKind.ARCHITECTURE),
        ("gold necklace with diamond pendant", SubjectKind.JEWELLERY),
        ("kanjivaram saree with zari border", SubjectKind.APPAREL),
        ("500ml glass bottle of coconut oil", SubjectKind.PRODUCT),
        ("hyderabadi biryani in a copper handi", SubjectKind.FOOD),
        ("a sunset over mountains", SubjectKind.GENERIC),
    ],
)
def test_subject_kind_is_inferred_from_the_brief(text: str, expected: SubjectKind) -> None:
    assert detect_kind(text) == expected


def test_preservation_defaults_on() -> None:
    # Someone uploading a photo of a real building is asking for it to be
    # restaged, not redesigned. The safe default is the one that preserves.
    assert CreativeBrief(subject="x").preserve_subject is True


# --------------------------------------------------------------------------
# Through the API
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
        if payload["state"] in {"complete", "failed", "rate_limited", "budget_exceeded"}:
            return payload
        await asyncio.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish")


async def test_a_real_estate_edit_preserves_the_building(client: AsyncClient) -> None:
    uploaded = (
        await client.post(
            "/api/uploads/reference",
            files={"file": ("site.png", as_png(building()), "image/png")},
        )
    ).json()

    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "our 3 BHK apartment project in Pune",
                "formats": ["portrait"],
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
                "reference_mode": "edit",
                "edit_instruction": "restage on a bright morning sky",
                "rights_confirmed": True,
            },
        )
    ).json()

    job = await _await_job(client, created["job_id"])
    assert job["state"] == "complete", job.get("error")

    # The brief said "apartment", so architecture rules should have applied
    # without the user having to say so.
    assert job["fidelity"]["subject_kind"] == "architecture"
    assert "preserving the architecture subject" in " ".join(job["logs"])


async def test_an_explicit_subject_kind_overrides_the_guess(client: AsyncClient) -> None:
    uploaded = (
        await client.post(
            "/api/uploads/reference",
            files={"file": ("x.png", as_png(building()), "image/png")},
        )
    ).json()

    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "a sunset over mountains",  # would infer "generic"
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
                "reference_mode": "edit",
                "rights_confirmed": True,
                "subject_kind": "architecture",
            },
        )
    ).json()
    job = await _await_job(client, created["job_id"])
    assert job["fidelity"]["subject_kind"] == "architecture"


async def test_a_failed_fidelity_check_reaches_the_review_queue(
    client: AsyncClient, monkeypatch
) -> None:
    """A drifted subject must never present itself as ready to publish."""
    from app.imaging import fidelity as fidelity_module
    from app.imaging.fidelity import FidelityReport

    def always_fail(before, after, kind=SubjectKind.GENERIC, **kwargs):
        return FidelityReport(
            kind=kind, similarity=0.4, extent_iou=0.5, density_delta=0.9,
            floor=0.92, passed=False, reason="the subject's outline moved",
        )

    monkeypatch.setattr("app.pipeline.compare", always_fail)

    uploaded = (
        await client.post(
            "/api/uploads/reference",
            files={"file": ("x.png", as_png(building()), "image/png")},
        )
    ).json()
    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "our villa project",
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
                "reference_mode": "edit",
                "rights_confirmed": True,
            },
        )
    ).json()

    job = await _await_job(client, created["job_id"])
    assert job["state"] == "complete"
    assert job["fidelity"]["passed"] is False
    assert job["review_count"] == len(job["variants"])
    assert any(
        "fidelity failed" in reason
        for variant in job["variants"]
        for reason in variant["review_reasons"]
    )


async def test_the_text_path_has_no_fidelity_report(client: AsyncClient) -> None:
    # Nothing was preserved because nothing was supplied.
    created = (
        await client.post(
            "/api/campaigns",
            json={"brief": "a glass bottle", "locales": ["en"]},
        )
    ).json()
    job = await _await_job(client, created["job_id"])
    assert "fidelity" not in job
