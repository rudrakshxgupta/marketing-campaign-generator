"""The MAI dimension rules are unforgiving and the failure mode is a 400 at
generation time, so every format is asserted against the raw constraints here
rather than against hard-coded numbers alone.
"""

from __future__ import annotations

import math

import pytest

from app.imaging.dimensions import (
    FORMATS,
    MAX_ASPECT,
    MAX_PIXELS,
    MIN_ASPECT,
    MIN_SIDE,
    DimensionError,
    is_legal,
    max_legal_dimensions,
    plan_for,
    resolve_dimensions,
)


@pytest.mark.parametrize("format_key", sorted(FORMATS))
def test_every_format_generates_legal_dimensions(format_key: str) -> None:
    plan = plan_for(format_key)
    assert plan.gen_w >= MIN_SIDE, f"{format_key}: width under floor"
    assert plan.gen_h >= MIN_SIDE, f"{format_key}: height under floor"
    assert plan.gen_w * plan.gen_h <= MAX_PIXELS, f"{format_key}: over pixel budget"
    assert is_legal(plan.gen_w, plan.gen_h)


@pytest.mark.parametrize("format_key", sorted(FORMATS))
def test_every_format_uses_most_of_the_pixel_budget(format_key: str) -> None:
    # Upscaling is the only way back to 1080px wide, so leaving pixels unused
    # is a straight quality loss. Anything under 99% means the search is wrong.
    plan = plan_for(format_key)
    utilisation = (plan.gen_w * plan.gen_h) / MAX_PIXELS
    assert utilisation > 0.99, f"{format_key}: only {utilisation:.1%} of budget used"


def test_square_hits_the_budget_exactly() -> None:
    plan = plan_for("square")
    assert (plan.gen_w, plan.gen_h) == (1024, 1024)
    assert plan.gen_w * plan.gen_h == MAX_PIXELS
    assert not plan.requires_crop


def test_story_needs_a_crop_because_exact_9_16_is_illegal() -> None:
    # 9:16 exactly would put a side at 767, one pixel under the floor.
    assert not is_legal(767, 1364)
    plan = plan_for("story")
    assert plan.gen_w == MIN_SIDE
    assert plan.requires_crop, "9:16 cannot be generated exactly"
    # The deviation should be tiny -- we are one pixel off, not a different shape.
    assert math.isclose(plan.gen_aspect, 9 / 16, rel_tol=0.001)


def test_landscape_is_not_generable_and_falls_back_to_16_9() -> None:
    target_aspect = 1080 / 566
    assert target_aspect > MAX_ASPECT, "1.91:1 should be out of band"
    plan = plan_for("landscape")
    assert plan.requires_crop
    assert math.isclose(plan.gen_aspect, MAX_ASPECT, rel_tol=0.001)


def test_portrait_and_grid_are_generable_without_shape_change() -> None:
    for key, expected in (("portrait", 4 / 5), ("grid", 3 / 4)):
        plan = plan_for(key)
        assert math.isclose(plan.gen_aspect, expected, rel_tol=0.002), key


def test_crop_box_produces_the_target_aspect() -> None:
    for key in FORMATS:
        plan = plan_for(key)
        left, top, right, bottom = plan.crop_box()
        assert 0 <= left < right <= plan.gen_w
        assert 0 <= top < bottom <= plan.gen_h
        cropped_aspect = (right - left) / (bottom - top)
        assert math.isclose(cropped_aspect, plan.target_aspect, rel_tol=0.005), key


def test_crop_box_is_the_full_frame_when_no_crop_is_needed() -> None:
    plan = plan_for("square")
    assert plan.crop_box() == (0, 0, plan.gen_w, plan.gen_h)


def test_upscaling_stays_within_a_quality_budget() -> None:
    # Upscaling is where the generated image gets soft, so cap how far we ever
    # stretch. Story is the worst case (768px wide -> 1080px) at ~1.41x.
    for key in FORMATS:
        plan = plan_for(key)
        assert plan.upscale_factor <= 1.45, (
            f"{key} upscales {plan.upscale_factor:.3f}x, above the quality budget"
        )


def test_landscape_downscales_because_delivery_is_small() -> None:
    # 1080x566 is fewer pixels than the 16:9 frame we crop from, so this format
    # loses nothing to interpolation. Worth pinning so nobody "fixes" it.
    plan = plan_for("landscape")
    assert plan.upscale_factor < 1.0
    assert plan.target_w * plan.target_h < plan.gen_w * plan.gen_h


@pytest.mark.parametrize("aspect", [0.6, 0.75, 0.8, 1.0, 1.25, 1.5, 1.7])
def test_in_band_aspects_are_matched_closely(aspect: float) -> None:
    width, height = max_legal_dimensions(aspect)
    assert is_legal(width, height)
    assert math.isclose(width / height, aspect, rel_tol=0.005)


@pytest.mark.parametrize("aspect", [0.4, 0.5, 1.9, 2.5])
def test_out_of_band_aspects_are_rejected_by_the_low_level_helper(aspect: float) -> None:
    with pytest.raises(DimensionError):
        max_legal_dimensions(aspect)


def test_out_of_band_targets_are_clamped_not_rejected_by_the_resolver() -> None:
    # A caller asking for a panorama should get a cropped plan, not an error.
    plan = resolve_dimensions(2000, 500)
    assert plan.requires_crop
    assert is_legal(plan.gen_w, plan.gen_h)


def test_aspect_band_matches_the_published_constraints() -> None:
    assert math.isclose(MIN_ASPECT, 768 / 1365, rel_tol=1e-9)
    assert math.isclose(MAX_ASPECT, 1365 / 768, rel_tol=1e-9)


@pytest.mark.parametrize("bad", [(0, 100), (100, 0), (-5, 100)])
def test_non_positive_targets_are_rejected(bad: tuple[int, int]) -> None:
    with pytest.raises(DimensionError):
        resolve_dimensions(*bad)
