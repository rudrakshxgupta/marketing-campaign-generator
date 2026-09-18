"""The reference-image path.

Two sub-modes that behave very differently, which is why they are surfaced
explicitly rather than inferred:

- "inspiration" measures the look and runs the ordinary text path, so output
  still obeys our layout, aspect ratio and safe zones.
- "edit" reproduces the uploaded image pixel-for-pixel, which is both a
  different creative result and a different legal exposure.
"""

from __future__ import annotations

import asyncio
import io

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

from app.copy.strategy import CreativeBrief
from app.imaging.style import apply_style, extract_style
from app.main import app


def swatch(colours: list[tuple[int, int, int]], size=(400, 500)) -> Image.Image:
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    band = size[1] // len(colours)
    for index, colour in enumerate(colours):
        draw.rectangle([0, index * band, size[0], (index + 1) * band], fill=colour)
    return image


def as_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


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


# --------------------------------------------------------------------------
# Style extraction
# --------------------------------------------------------------------------

def test_dark_and_bright_references_are_told_apart() -> None:
    dark = extract_style(swatch([(24, 16, 12), (48, 32, 20), (70, 48, 28)]))
    bright = extract_style(swatch([(244, 242, 236), (236, 232, 224), (250, 248, 244)]))

    assert dark.brightness == "dark"
    assert bright.brightness == "bright"

    # The lighting sentence is what actually reaches the prompt, and it must
    # read as dim for one and bright for the other.
    assert any(word in dark.describe_lighting() for word in ("low-key", "dim"))
    assert any(
        word in bright.describe_lighting() for word in ("bright", "airy", "high-key")
    )


def test_contrast_changes_the_lighting_description_not_just_brightness() -> None:
    # A uniformly dark image is flat ambient light; a dark image with a bright
    # highlight is dramatic. Both are "dark", and the prompt should say so
    # differently.
    flat = extract_style(swatch([(24, 16, 12), (30, 22, 16), (20, 14, 10)]))
    dramatic = extract_style(swatch([(10, 8, 6)] * 8 + [(250, 245, 235)]))

    assert flat.brightness == dramatic.brightness == "dark"
    assert flat.contrast == "low"
    assert dramatic.contrast == "high"
    assert flat.describe_lighting() != dramatic.describe_lighting()


def test_warm_and_cool_references_are_told_apart() -> None:
    warm = extract_style(swatch([(200, 140, 60), (180, 110, 40)]))
    cool = extract_style(swatch([(60, 110, 190), (40, 90, 170)]))
    assert warm.temperature == "warm"
    assert cool.temperature == "cool"


def test_palette_is_returned_as_words_not_hex() -> None:
    # Diffusion models respond to colour words far better than hex codes, so
    # the names are what reach the prompt and the hex is only for the UI.
    profile = extract_style(swatch([(196, 152, 68), (48, 32, 24)]))
    assert profile.palette_names
    assert all(isinstance(name, str) and " " in name or name.isalpha()
               for name in profile.palette_names)
    assert all(h.startswith("#") for h in profile.palette_hex)


def test_orientation_is_detected() -> None:
    assert extract_style(swatch([(10, 10, 10)], size=(400, 800))).orientation == "portrait"
    assert extract_style(swatch([(10, 10, 10)], size=(800, 400))).orientation == "landscape"
    assert extract_style(swatch([(10, 10, 10)], size=(500, 500))).orientation == "square"


def test_applying_a_style_never_copies_the_subject() -> None:
    """Extracting style is fine. Copying a silhouette makes it derivative."""
    brief = CreativeBrief(subject="a bottle of coconut oil")
    profile = extract_style(swatch([(196, 152, 68), (48, 32, 24)]))
    merged = apply_style(brief, profile)

    assert merged.subject == "a bottle of coconut oil", "the subject was overwritten"
    assert merged.source_mode == "style_transfer"
    assert any("silhouette" in item for item in merged.must_not_depict)
    assert any("logo" in item for item in merged.must_not_depict)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

async def test_uploading_a_reference_reports_what_was_read(client: AsyncClient) -> None:
    png = as_png(swatch([(30, 20, 14), (150, 110, 45), (196, 152, 68)]))
    response = await client.post(
        "/api/uploads/reference", files={"file": ("ref.png", png, "image/png")}
    )
    assert response.status_code == 200
    payload = response.json()

    # The user should see immediately what "inspiration" will carry across,
    # before spending anything.
    assert payload["reference_id"]
    assert payload["style"]["palette"]
    assert payload["style"]["brightness"] in {"dark", "balanced", "bright"}
    assert payload["style"]["lighting"]
    assert set(payload["modes"]) == {"inspiration", "edit"}


async def test_a_non_image_upload_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/uploads/reference",
        files={"file": ("notes.txt", b"this is not an image", "text/plain")},
    )
    assert response.status_code == 400


async def test_inspiration_mode_carries_the_palette_into_the_prompt(
    client: AsyncClient,
) -> None:
    png = as_png(swatch([(30, 20, 14), (150, 110, 45), (196, 152, 68)]))
    uploaded = (
        await client.post(
            "/api/uploads/reference", files={"file": ("ref.png", png, "image/png")}
        )
    ).json()

    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "a glass bottle of coconut oil",
                "formats": ["portrait"],
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
                "reference_mode": "inspiration",
            },
        )
    ).json()
    assert created["reference_mode"] == "inspiration"

    job = await _await_job(client, created["job_id"])
    assert job["state"] == "complete", job.get("error")

    prompt = job["prompt"].lower()
    # The look came across...
    assert any(name in prompt for name in uploaded["style"]["palette"])
    # ...and the subject is still the user's, not the reference's.
    assert "coconut oil" in prompt
    # ...and we told the model not to copy the reference itself.
    assert "silhouette" in prompt


async def test_edit_mode_requires_a_rights_confirmation(client: AsyncClient) -> None:
    png = as_png(swatch([(30, 20, 14), (150, 110, 45)]))
    uploaded = (
        await client.post(
            "/api/uploads/reference", files={"file": ("ref.png", png, "image/png")}
        )
    ).json()

    response = await client.post(
        "/api/campaigns",
        json={
            "brief": "a bottle",
            "formats": ["portrait"],
            "locales": ["en"],
            "reference_id": uploaded["reference_id"],
            "reference_mode": "edit",
            "rights_confirmed": False,
        },
    )
    assert response.status_code == 400
    assert "rights" in response.text.lower()


async def test_edit_mode_needs_a_reference(client: AsyncClient) -> None:
    response = await client.post(
        "/api/campaigns",
        json={"brief": "a bottle", "reference_mode": "edit", "rights_confirmed": True},
    )
    assert response.status_code == 400


async def test_edit_mode_runs_when_rights_are_confirmed(client: AsyncClient) -> None:
    png = as_png(swatch([(30, 20, 14), (150, 110, 45)]))
    uploaded = (
        await client.post(
            "/api/uploads/reference", files={"file": ("ref.png", png, "image/png")}
        )
    ).json()

    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "a bottle",
                "formats": ["portrait"],
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
                "reference_mode": "edit",
                "edit_instruction": "restage on a clean studio backdrop",
                "rights_confirmed": True,
            },
        )
    ).json()

    job = await _await_job(client, created["job_id"])
    assert job["state"] == "complete", job.get("error")
    assert len(job["variants"]) == 1


async def test_inspiration_is_the_default_mode(client: AsyncClient) -> None:
    # Defaulting to edit would silently produce derivative works from whatever
    # the user happened to upload.
    png = as_png(swatch([(30, 20, 14)]))
    uploaded = (
        await client.post(
            "/api/uploads/reference", files={"file": ("ref.png", png, "image/png")}
        )
    ).json()

    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "a bottle",
                "locales": ["en"],
                "reference_id": uploaded["reference_id"],
            },
        )
    ).json()
    assert created["reference_mode"] == "inspiration"


async def test_a_missing_reference_fails_the_job_cleanly(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/api/campaigns",
            json={
                "brief": "a bottle",
                "locales": ["en"],
                "reference_id": "doesnotexist",
            },
        )
    ).json()
    job = await _await_job(client, created["job_id"])
    assert job["state"] == "failed"
    assert "not found" in (job["error"] or "").lower()
