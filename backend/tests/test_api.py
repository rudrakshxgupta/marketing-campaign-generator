"""API surface, exercised end to end against the mock image backend."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


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
        if payload["state"] in {"complete", "failed", "rate_limited"}:
            return payload
        await asyncio.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


async def test_healthz_reports_mock_mode(client: AsyncClient) -> None:
    payload = (await client.get("/healthz")).json()
    assert payload["ok"] is True
    assert payload["mock"] is True, "tests must never call the real 2 RPM endpoint"


async def test_meta_exposes_generation_sizes(client: AsyncClient) -> None:
    payload = (await client.get("/api/meta")).json()
    formats = {f["key"]: f for f in payload["formats"]}

    # The frontend reads sizes from here, so the dimension rules stay in one
    # place rather than being duplicated client-side.
    assert formats["portrait"]["target"] == [1080, 1350]
    assert formats["story"]["requires_crop"] is True
    assert formats["landscape"]["requires_crop"] is True
    for spec in formats.values():
        gen_w, gen_h = spec["generate"]
        assert gen_w >= 768 and gen_h >= 768
        assert gen_w * gen_h <= 1_048_576

    keys = {loc["key"] for loc in payload["locales"]}
    assert {"en", "hi", "hi-Latn", "mr", "bn", "ta", "te"} <= keys


async def test_generation_produces_many_variants_from_one_image_call(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/campaigns",
        json={
            "brief": "a glass bottle of coconut oil on dark wood, warm festive light",
            "formats": ["portrait"],
            "locales": ["en", "hi", "hi-Latn", "ta"],
            "occasion": "Diwali",
            "occasion_by_locale": {"bn": "Durga Puja"},
        },
    )
    assert response.status_code == 202
    created = response.json()
    assert created["image_calls_expected"] == 1
    assert created["deliverables_expected"] == 4

    job = await _await_job(client, created["job_id"])
    assert job["state"] == "complete", job.get("error")

    # The point of the architecture: four languages, one generation.
    assert job["image_calls"] == 1
    assert len(job["variants"]) == 4

    locales = {v["locale"] for v in job["variants"]}
    assert locales == {"en", "hi", "hi-Latn", "ta"}


async def test_prompt_forbids_text_and_reserves_space(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/api/campaigns",
            json={"brief": "a bottle on wood", "formats": ["square"], "locales": ["en"]},
        )
    ).json()
    job = await _await_job(client, created["job_id"])
    prompt = job["prompt"].lower()

    # These two clauses are what make the overlay architecture work; if either
    # is ever dropped the model will draw its own text into the reserved area.
    assert "no text" in prompt and "no lettering" in prompt
    assert "reserved for later graphic overlay" in prompt
    # Global prohibitions that exist for legal rather than aesthetic reasons.
    assert "no any map" in prompt or "no map" in prompt
    assert "no any national flag" in prompt or "no national flag" in prompt


async def test_machine_written_copy_is_flagged_for_review(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/api/campaigns",
            json={"brief": "a bottle", "formats": ["portrait"], "locales": ["ta"]},
        )
    ).json()
    job = await _await_job(client, created["job_id"])

    # Tamil has no OCR coverage for verification, so unreviewed copy must never
    # present itself as ready to publish.
    assert job["review_count"] == 1
    reasons = job["variants"][0]["review_reasons"]
    assert any("review" in reason for reason in reasons)


async def test_bundle_contains_images_and_copy(client: AsyncClient) -> None:
    import io
    import zipfile

    created = (
        await client.post(
            "/api/campaigns",
            json={"brief": "a bottle", "formats": ["portrait"], "locales": ["en", "hi"]},
        )
    ).json()
    job = await _await_job(client, created["job_id"])
    assert job["bundle"]

    response = await client.get(job["bundle"])
    assert response.status_code == 200

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert any(n.startswith("images/") and n.endswith(".png") for n in names)
        # A marketer needs the caption and hashtags, not just a picture.
        assert "copy/en.txt" in names and "copy/hi.txt" in names
        assert "REVIEW.txt" in names
        hindi = archive.read("copy/hi.txt").decode("utf-8")
        assert "शुद्धता" in hindi
        assert "एआई से बनाया गया" in hindi, "AI disclosure must survive into the caption"


async def test_unknown_format_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/campaigns", json={"brief": "x", "formats": ["billboard"]}
    )
    assert response.status_code == 400


async def test_unknown_locale_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/campaigns", json={"brief": "x", "locales": ["fr"]}
    )
    assert response.status_code == 400


async def test_missing_job_is_404(client: AsyncClient) -> None:
    assert (await client.get("/api/jobs/deadbeef")).status_code == 404
