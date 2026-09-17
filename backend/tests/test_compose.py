"""Compositing is the deterministic half of the product, so these assertions
are exact. If the logo drifts, the contrast maths slips, or a safe zone stops
being enforced, that is a bug rather than a quality regression.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.foundry.mock_client import MockImageClient
from app.imaging.compose import (
    MIN_CONTRAST,
    choose_variant,
    composite_logo,
    contrast_ratio,
    derive_variants,
    finalize_base,
    logo_box,
    patch_luminance,
    relative_luminance,
)
from app.imaging.dimensions import FORMATS, plan_for
from app.imaging.safezones import (
    SafeZoneViolation,
    assert_within,
    grid_bleed_band,
    safe_rect,
)


@pytest.fixture
def logo() -> Image.Image:
    image = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    # A shape with real transparency, so knockout derivation is exercised.
    for x in range(400):
        for y in range(120):
            if 10 < x < 390 and 10 < y < 110:
                image.putpixel((x, y), (220, 40, 60, 255))
    return image


def flat(colour: tuple[int, int, int], size: tuple[int, int]) -> Image.Image:
    return Image.new("RGB", size, colour)


# --------------------------------------------------------------------------
# Contrast maths
# --------------------------------------------------------------------------

def test_relative_luminance_matches_wcag_anchors() -> None:
    assert relative_luminance((0, 0, 0)) == pytest.approx(0.0, abs=1e-6)
    assert relative_luminance((255, 255, 255)) == pytest.approx(1.0, abs=1e-6)


def test_black_on_white_is_the_maximum_contrast_ratio() -> None:
    assert contrast_ratio((0, 0, 0), (255, 255, 255)) == pytest.approx(21.0, abs=0.01)


def test_patch_luminance_tracks_background_brightness() -> None:
    dark = flat((20, 20, 20), (100, 100))
    light = flat((240, 240, 240), (100, 100))
    box = (0, 0, 100, 100)
    assert patch_luminance(dark, box) < 0.1
    assert patch_luminance(light, box) > 0.8


# --------------------------------------------------------------------------
# Variant selection
# --------------------------------------------------------------------------

def test_dark_logo_is_chosen_on_a_light_background(logo: Image.Image) -> None:
    base = flat((245, 245, 245), (1080, 1350))
    variant, scrim, achieved = choose_variant(base, (0, 0, 200, 60), derive_variants(logo))
    assert variant == "dark"
    assert scrim == 0.0
    assert achieved >= MIN_CONTRAST


def test_light_logo_is_chosen_on_a_dark_background(logo: Image.Image) -> None:
    base = flat((18, 18, 24), (1080, 1350))
    variant, scrim, achieved = choose_variant(base, (0, 0, 200, 60), derive_variants(logo))
    assert variant == "light"
    assert scrim == 0.0
    assert achieved >= MIN_CONTRAST


def test_plain_mid_grey_still_clears_aa_with_a_dark_mark(logo: Image.Image) -> None:
    # Worth pinning: 50% grey looks like the hard case but is not. A dark mark
    # reaches ~4.8:1 against it, so no scrim should be added.
    base = flat((128, 128, 128), (1080, 1350))
    variant, scrim, achieved = choose_variant(base, (0, 0, 200, 60), derive_variants(logo))
    assert variant == "dark"
    assert scrim == 0.0
    assert achieved >= MIN_CONTRAST


def test_the_narrow_band_where_neither_knockout_works_triggers_a_scrim(
    logo: Image.Image,
) -> None:
    # Around luminance 0.19 both knockouts fall just under 4.5:1 -- white
    # reaches ~4.35 and black ~4.34. This is the band where a naive
    # implementation ships an illegible mark.
    base = flat((121, 121, 121), (1080, 1350))
    variant, scrim, achieved = choose_variant(base, (0, 0, 200, 60), derive_variants(logo))
    assert scrim > 0.0, "neither knockout clears AA here, so a scrim is required"
    assert achieved >= MIN_CONTRAST


def test_derive_variants_preserves_the_alpha_mask(logo: Image.Image) -> None:
    variants = derive_variants(logo)
    assert set(variants) == {"original", "light", "dark"}
    source_alpha = logo.convert("RGBA").getchannel("A").tobytes()
    for name in ("light", "dark"):
        assert variants[name].getchannel("A").tobytes() == source_alpha


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

@pytest.mark.parametrize("format_key", sorted(FORMATS))
@pytest.mark.parametrize(
    "anchor", ["top_left", "top_right", "bottom_left", "bottom_right", "bottom_center"]
)
def test_logo_lands_inside_the_safe_zone_for_every_format_and_anchor(
    format_key: str, anchor: str
) -> None:
    width, height = FORMATS[format_key]
    box = logo_box(
        canvas_w=width,
        canvas_h=height,
        logo_w=400,
        logo_h=120,
        anchor=anchor,  # type: ignore[arg-type]
        format_key=format_key,
    )
    # Should not raise -- placement is derived from the safe rect, so this is a
    # structural property, not a lucky default.
    assert_within(box, width=width, height=height, format_key=format_key, what="logo")


def test_story_safe_zone_matches_the_published_reels_spec() -> None:
    rect = safe_rect(1080, 1920, "story")
    # top 14%, bottom 35%, left/right 6% -> a usable box of 950x979.
    assert (rect.width, rect.height) == (950, 979)
    assert rect.top == 269
    assert rect.bottom == 1248


def test_safe_zone_violation_is_raised_not_warned() -> None:
    # Bottom-right corner of a story canvas is deep under the caption tray.
    with pytest.raises(SafeZoneViolation):
        assert_within(
            (1000, 1850, 1070, 1900),
            width=1080,
            height=1920,
            format_key="story",
            what="logo",
        )


def test_grid_bleed_band_is_the_centred_4_5_region_of_a_3_4_master() -> None:
    left, top, right, bottom = grid_bleed_band(1080, 1440)
    assert (right - left, bottom - top) == (1080, 1350)
    # Centred, so 45px is trimmed from the top and bottom.
    assert top == 45
    assert bottom == 1395


# --------------------------------------------------------------------------
# End to end through the mock backend
# --------------------------------------------------------------------------

@pytest.mark.parametrize("format_key", sorted(FORMATS))
async def test_mock_generation_composites_to_exact_delivery_size(
    format_key: str, logo: Image.Image
) -> None:
    plan = plan_for(format_key)
    client = MockImageClient()
    result = await client.generate(
        prompt="a glass bottle on dark wood, warm festive lighting",
        width=plan.gen_w,
        height=plan.gen_h,
    )

    base = finalize_base(result.png, plan)
    assert base.size == (plan.target_w, plan.target_h)

    anchor = "top_left" if format_key == "story" else "bottom_right"
    final, placement = composite_logo(
        base, logo, format_key=format_key, anchor=anchor  # type: ignore[arg-type]
    )
    assert final.size == (plan.target_w, plan.target_h)
    assert placement.contrast >= MIN_CONTRAST


async def test_mock_refuses_dimensions_the_service_would_reject() -> None:
    from app.foundry.image_client import MaiError

    client = MockImageClient()
    with pytest.raises(MaiError):
        await client.generate(prompt="x", width=700, height=1000)  # side under 768
    with pytest.raises(MaiError):
        await client.generate(prompt="x", width=1365, height=1365)  # over the budget


async def test_edit_output_follows_the_input_size() -> None:
    # The edits endpoint takes no width/height, so the mock must not let
    # callers depend on requesting one.
    client = MockImageClient()
    seed = await client.generate(prompt="seed", width=1024, height=1024)
    edited = await client.edit(prompt="restage on a studio backdrop", image=seed.png)
    assert (edited.width, edited.height) == (1024, 1024)
