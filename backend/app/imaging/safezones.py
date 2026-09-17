"""Instagram safe zones.

Meta unified the 9:16 safe zone across Instagram/Facebook Stories and Reels in
March 2026 and moved it from fixed pixels to percentages, because there are too
many live phone aspect ratios for fixed margins to work.

Everything outside the returned rect is liable to be covered by platform UI --
the profile row and progress bars at the top, the caption tray, audio credit,
follow button and engagement rail at the bottom, the action rail on the right.
Content placed there is not "slightly cropped", it is invisible.

The 9:16 numbers are deliberately the *Reels* ones. Designing one asset to the
Reels-safe box means it also works as a Story; designing to the Story box and
posting it as a Reel loses the bottom 15%.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Insets:
    """Fractions of the canvas reserved at each edge."""

    top: float
    right: float
    bottom: float
    left: float


#: Reels/Stories: the bottom third is consumed by the caption tray and controls.
STORY_INSETS = Insets(top=0.14, right=0.06, bottom=0.35, left=0.06)

#: Feed posts have no platform overlay, but the handle and caption sit directly
#: beneath the image, so copy hard against the bottom edge reads as clutter.
FEED_INSETS = Insets(top=0.06, right=0.06, bottom=0.08, left=0.06)

#: Slide 1 of a carousel carries the swipe affordance and slide-count pill.
CAROUSEL_FIRST_SLIDE_INSETS = Insets(top=0.06, right=0.08, bottom=0.08, left=0.06)

INSETS_BY_FORMAT: dict[str, Insets] = {
    "story": STORY_INSETS,
    "portrait": FEED_INSETS,
    "grid": FEED_INSETS,
    "square": FEED_INSETS,
    "landscape": FEED_INSETS,
}


@dataclass(frozen=True)
class SafeRect:
    """Usable region in absolute pixels: (left, top, right, bottom)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, box: tuple[int, int, int, int]) -> bool:
        left, top, right, bottom = box
        return (
            left >= self.left
            and top >= self.top
            and right <= self.right
            and bottom <= self.bottom
        )


class SafeZoneViolation(ValueError):
    """Brand-critical content was placed where the platform will cover it."""


def safe_rect(width: int, height: int, format_key: str) -> SafeRect:
    """Usable rect for a delivery canvas."""
    insets = INSETS_BY_FORMAT.get(format_key, FEED_INSETS)
    return SafeRect(
        left=round(width * insets.left),
        top=round(height * insets.top),
        right=round(width * (1.0 - insets.right)),
        bottom=round(height * (1.0 - insets.bottom)),
    )


def assert_within(
    box: tuple[int, int, int, int],
    *,
    width: int,
    height: int,
    format_key: str,
    what: str = "element",
) -> None:
    """Hard gate. Raise if ``box`` strays outside the safe rect.

    This is deliberately an exception rather than a warning: an export with the
    logo under the Reels caption tray is not a lesser export, it is a broken
    one, and warnings get ignored at volume.
    """
    rect = safe_rect(width, height, format_key)
    if not rect.contains(box):
        raise SafeZoneViolation(
            f"{what} at {box} falls outside the {format_key} safe zone "
            f"({rect.left},{rect.top})-({rect.right},{rect.bottom}). "
            f"Platform UI would cover it."
        )


def grid_bleed_band(width: int, height: int) -> tuple[int, int, int, int]:
    """The 4:5 region of a 3:4 master that survives as the feed post.

    Instagram's profile grid crops to 3:4 while the feed shows 4:5, so the
    working method is to compose on 1080x1440 and keep everything critical
    inside the centred 1080x1350 band. Getting this wrong is why AI-made posts
    so often look decapitated on a profile page.
    """
    feed_height = round(width * 5 / 4)
    if feed_height >= height:
        return (0, 0, width, height)
    offset = (height - feed_height) // 2
    return (0, offset, width, offset + feed_height)
