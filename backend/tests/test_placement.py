"""Where the copy and the mark land.

The prompt reserves a named region of the frame and tells the model to keep
it clean. For a long time the typesetter then ignored that and wrote in the
same fixed corner for every campaign, so the copy landed on the busiest part
of the picture while the deliberately empty area sat unused -- and every
creative came out looking like the last one.
"""

from __future__ import annotations

import pytest

from app.imaging.safezones import INSETS_BY_FORMAT, FEED_INSETS
from app.imaging.style import StyleProfile, merge_styles
from app.pipeline import TEXT_BOXES, logo_anchor_for, text_box_for

REGIONS = [
    "top", "bottom", "left", "right",
    "top_left", "top_right", "bottom_left", "bottom_right",
]


@pytest.mark.parametrize("format_key", sorted(TEXT_BOXES))
@pytest.mark.parametrize("region", REGIONS)
def test_the_copy_block_stays_inside_the_safe_area(format_key, region) -> None:
    box = text_box_for(format_key, region)
    insets = INSETS_BY_FORMAT.get(format_key, FEED_INSETS)

    assert box.left >= insets.left - 1e-9
    assert box.top >= insets.top - 1e-9
    assert box.left + box.width <= 1 - insets.right + 1e-9
    assert box.top + box.height <= 1 - insets.bottom + 1e-9


@pytest.mark.parametrize("region", REGIONS)
def test_the_block_actually_moves(region) -> None:
    """A table lookup that returned the same box for every region would pass
    the safe-zone test above and change nothing at all."""
    placed = text_box_for("portrait", region)
    if region.startswith("top"):
        assert placed.top < 0.4, "a top region put the copy low in the frame"
    if region.startswith("bottom"):
        assert placed.top > 0.4
    if region.endswith("right"):
        assert placed.left > 0.2, "a right region put the copy on the left"
    if region.endswith("left"):
        assert placed.left < 0.2


def test_every_region_is_distinct_in_at_least_one_axis() -> None:
    placements = {r: text_box_for("portrait", r) for r in REGIONS}
    corners = {(round(b.left, 3), round(b.top, 3)) for b in placements.values()}
    assert len(corners) >= 6, f"regions collapsed onto {len(corners)} positions"


def test_copy_pinned_high_reads_as_a_caption_and_low_as_a_headline() -> None:
    assert text_box_for("portrait", "top_left").justify == "flex-start"
    assert text_box_for("portrait", "bottom_left").justify == "flex-end"


def test_an_unknown_region_falls_back_rather_than_raising() -> None:
    # The region comes from a model, so it can be anything.
    box = text_box_for("portrait", "middle_of_nowhere")
    assert 0 <= box.left < 1 and 0 <= box.top < 1


# --------------------------------------------------------------------------
# The logo has to get out of the way
# --------------------------------------------------------------------------

@pytest.mark.parametrize("region", REGIONS)
def test_the_logo_never_shares_a_corner_with_the_copy(region) -> None:
    """Once the copy follows the brief, a fixed logo anchor is a collision.

    A brief reserving the lower-right puts the headline exactly where the
    mark used to go.
    """
    box = text_box_for("portrait", region)
    anchor = logo_anchor_for("portrait", box)

    copy_is_low = box.top + box.height / 2 > 0.5
    copy_is_left = box.left + box.width / 2 < 0.5
    assert anchor.startswith("top" if copy_is_low else "bottom")
    assert anchor.endswith("right" if copy_is_left else "left")


def test_story_keeps_its_fixed_anchor() -> None:
    # Its usable band is narrow enough that top-left is the only placement
    # clearing both the caption tray and the copy.
    for region in REGIONS:
        assert logo_anchor_for("story", text_box_for("story", region)) == "top_left"


# --------------------------------------------------------------------------
# Several references
# --------------------------------------------------------------------------

def test_merging_references_unions_colour_and_votes_on_the_rest() -> None:
    warm = StyleProfile(palette_names=("deep saffron",), palette_hex=("#e8850a",),
                        temperature="warm", brightness="bright")
    cool = StyleProfile(palette_names=("royal blue",), palette_hex=("#2b4fa8",),
                        temperature="cool", brightness="bright")
    warm2 = StyleProfile(palette_names=("warm cream",), palette_hex=("#f3e6cc",),
                         temperature="warm", brightness="dark")

    merged = merge_styles([warm, cool, warm2])
    # Colour is the one thing a reference set genuinely adds up to.
    assert set(merged.palette_names) == {"deep saffron", "royal blue", "warm cream"}
    # Everything else is a majority, so one odd reference cannot swing it.
    assert merged.temperature == "warm"
    assert merged.brightness == "bright"


def test_merging_one_reference_changes_nothing() -> None:
    only = StyleProfile(palette_names=("deep saffron",), temperature="warm")
    assert merge_styles([only]) is only


def test_a_tie_goes_to_the_first_reference() -> None:
    # People put the closest match first.
    a = StyleProfile(temperature="warm")
    b = StyleProfile(temperature="cool")
    assert merge_styles([a, b]).temperature == "warm"
    assert merge_styles([b, a]).temperature == "cool"


def test_the_merged_palette_is_capped() -> None:
    """A prompt listing nine colours describes nothing."""
    many = [
        StyleProfile(palette_names=(f"colour {i}",), palette_hex=(f"#00000{i}",))
        for i in range(9)
    ]
    assert len(merge_styles(many).palette_names) <= 5


def test_every_reference_contributes_before_any_contributes_twice() -> None:
    first = StyleProfile(palette_names=("a1", "a2", "a3"), palette_hex=("#1", "#2", "#3"))
    second = StyleProfile(palette_names=("b1",), palette_hex=("#4",))
    merged = merge_styles([first, second])
    # Round-robin, so the second reference is not crowded out by the first.
    assert "b1" in merged.palette_names
    assert merged.palette_names[:2] == ("a1", "b1")


# --------------------------------------------------------------------------
# Mock mode has to stand in for the model that is actually configured
# --------------------------------------------------------------------------

async def test_the_mock_accepts_what_the_configured_backend_accepts() -> None:
    """Otherwise mock mode cannot exercise the backend we ship on.

    The mock enforced MAI's 1 MP budget unconditionally. With FLUX selected
    the planner sizes a portrait at 1088x1344 -- legal for FLUX's 4 MP -- and
    the mock rejected its own planner's output, so every offline run of the
    real configuration failed on a limit that does not apply to it.
    """
    from app.foundry.mock_client import MockImageClient
    from app.imaging.dimensions import FLUX2, MAI, plan_for

    plan = plan_for("portrait", FLUX2)
    assert plan.gen_w * plan.gen_h > MAI.max_pixels, (
        "this test is only meaningful while FLUX plans past MAI's budget"
    )

    flux_mock = MockImageClient(caps=FLUX2)
    result = await flux_mock.generate(
        prompt="x", width=plan.gen_w, height=plan.gen_h
    )
    assert result.png

    # And it still refuses what the configured model would refuse.
    with pytest.raises(Exception):
        await MockImageClient(caps=MAI).generate(
            prompt="x", width=plan.gen_w, height=plan.gen_h
        )
