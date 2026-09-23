"""A brand name standing in for a logo file.

Most small sellers have a name long before they have an asset. Leaving the
corner empty makes a finished creative look unfinished, and asking the image
model to letter the name produces a *plausible* wordmark -- which is to say
the wrong one, in subtly wrong letterforms that still ship.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy
from app.foundry.mock_client import MockImageClient
from app.imaging.compose import derive_variants, logo_box
from app.pipeline import CampaignPipeline


def wide_mark(w: int = 800, h: int = 120) -> Image.Image:
    """Roughly the aspect a typeset name comes out at."""
    mark = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for x in range(10, w - 10):
        for y in range(20, h - 20):
            mark.putpixel((x, y), (255, 255, 255, 255))
    return mark


def square_mark(side: int = 300) -> Image.Image:
    mark = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    for x in range(20, side - 20):
        for y in range(20, side - 20):
            mark.putpixel((x, y), (255, 255, 255, 255))
    return mark


# --------------------------------------------------------------------------
# Sizing
# --------------------------------------------------------------------------

def test_a_wide_wordmark_is_sized_by_height_not_width() -> None:
    """Width alone collapses a long name into a hairline.

    "Kesari Silks" typesets at about 5.5:1. At 12% of a 1080px canvas that is
    130px wide and eleven pixels tall -- present, correctly placed inside the
    safe zone, and completely illegible.
    """
    mark = derive_variants(wide_mark())["original"]
    box = logo_box(
        canvas_w=1080, canvas_h=1350, logo_w=mark.width, logo_h=mark.height,
        anchor="bottom_right", format_key="portrait",
        width_pct=0.42, max_height_pct=0.038,
    )
    height = box[3] - box[1]
    assert height >= 30, f"wordmark only {height}px tall, unreadable"
    assert box[2] - box[0] <= 1080 * 0.45


def test_a_square_logo_keeps_its_existing_size() -> None:
    # The height cap must not quietly resize every mark that already worked.
    mark = derive_variants(square_mark())["original"]
    box = logo_box(
        canvas_w=1080, canvas_h=1350, logo_w=mark.width, logo_h=mark.height,
        anchor="bottom_right", format_key="portrait",
    )
    assert box[2] - box[0] == pytest.approx(round(1080 * 0.12), abs=2)


# --------------------------------------------------------------------------
# Through the pipeline
# --------------------------------------------------------------------------

async def _run(tmp_path, brand: BrandKit, logo: Image.Image | None = None):
    pipeline = CampaignPipeline(MockImageClient(), storage=tmp_path)
    try:
        return await pipeline.run(
            campaign_id="wm",
            brief=CreativeBrief(subject="a glass bottle", source_text="a bottle"),
            strategy=CreativeStrategy(proposition="a bottle", benefit=""),
            brand=brand,
            logo=logo,
            formats=("portrait",),
            locales=("en",),
        )
    finally:
        await pipeline.aclose()


async def test_a_brand_name_becomes_a_mark_when_no_logo_was_uploaded(tmp_path) -> None:
    result = await _run(tmp_path, BrandKit(name="Kesari Silks"))
    variant = result.variants[0]
    assert variant.logo_variant != "none", "the corner was left empty"
    assert "wordmark" in variant.logo_variant
    # It goes through the same placement machinery as a real mark, so it
    # inherits the contrast gate rather than merely being drawn somewhere.
    assert variant.logo_contrast > 0


async def test_an_uploaded_logo_wins_over_the_name(tmp_path) -> None:
    result = await _run(tmp_path, BrandKit(name="Kesari Silks"), logo=square_mark())
    variant = result.variants[0]
    assert variant.logo_variant != "none"
    assert "wordmark" not in variant.logo_variant, (
        "a typeset name replaced the brand's own file"
    )


async def test_no_logo_and_no_name_leaves_the_corner_alone(tmp_path) -> None:
    result = await _run(tmp_path, BrandKit(name="   "))
    assert result.variants[0].logo_variant == "none"


async def test_the_mark_is_typeset_once_per_campaign(tmp_path) -> None:
    """Seven locales composite the same mark seven times.

    Re-rendering it in Chromium each time pays the cost seven times over for
    a byte-identical result.
    """
    pipeline = CampaignPipeline(MockImageClient(), storage=tmp_path)
    calls: list[str] = []
    real = pipeline._renderer.wordmark

    async def counted(text, **kwargs):
        calls.append(text)
        return await real(text, **kwargs)

    pipeline._renderer.wordmark = counted
    try:
        await pipeline.run(
            campaign_id="cache",
            brief=CreativeBrief(subject="a glass bottle"),
            strategy=CreativeStrategy(proposition="a bottle", benefit=""),
            brand=BrandKit(name="Kesari Silks"),
            formats=("portrait",),
            locales=("en", "hi", "ta"),
        )
    finally:
        await pipeline.aclose()

    assert len(calls) == 1, f"typeset the same mark {len(calls)} times"
