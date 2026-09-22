"""Resolve Instagram delivery sizes to legal MAI-Image-2.6 generation sizes.

MAI-Image-2.6 constrains every request to:

    width  >= 768
    height >= 768
    width * height <= 1_048_576

Those three rules interact in a way that is easy to get wrong, so all of the
arithmetic lives here and nowhere else.

Two consequences are worth stating explicitly, because both look fine until you
try them:

* Instagram's story format is 9:16 = 0.5625. The largest legal pair at that
  exact ratio is 767x1364 -- the width is one pixel under the floor, so it is
  rejected with a 400. The usable pair is 768x1365 (0.56264), which we then
  crop to exact 9:16.
* Instagram's landscape format is 1.91:1. Reaching it with a height >= 768
  would need a width of 1467, which busts the pixel budget. It is not
  generable at all; we generate 16:9 and crop.

`width`/`height` are parameters of the *generations* endpoint only. The edits
endpoint accepts no dimensions, so image-to-image output geometry is handled by
cropping the response, never by asking the API.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_SIDE = 768
MAX_PIXELS = 1_048_576

#: Longest a side may be while the other side still clears ``MIN_SIDE``.
MAX_SIDE = MAX_PIXELS // MIN_SIDE  # 1365

#: The band of aspect ratios (w/h) that MAI can actually produce.
MIN_ASPECT = MIN_SIDE / MAX_SIDE  # 0.5626
MAX_ASPECT = MAX_SIDE / MIN_SIDE  # 1.7773


@dataclass(frozen=True)
class Capabilities:
    """What one image model will accept.

    The pixel budget is the single most consequential difference between
    backends, so it is data rather than constants scattered through the code.
    MAI's 1 MP forces every Instagram format through a crop-and-upscale;
    FLUX's 4 MP clears the largest format (1.46 MP) outright, so the same
    campaign needs no interpolation at all.
    """

    name: str
    min_side: int
    max_pixels: int
    #: Diffusion models commonly want dimensions on a grid. Generating slightly
    #: off-target and cropping back is cheap; a 400 is not.
    multiple_of: int = 1

    @property
    def max_side(self) -> int:
        return self.max_pixels // self.min_side

    @property
    def min_aspect(self) -> float:
        return self.min_side / self.max_side

    @property
    def max_aspect(self) -> float:
        return self.max_side / self.min_side

    def accepts(self, width: int, height: int) -> bool:
        return (
            width >= self.min_side
            and height >= self.min_side
            and width * height <= self.max_pixels
        )


#: MAI-Image-2.x. Every Instagram format needs an upscale, and two of them
#: cannot be generated at their true aspect at all.
MAI = Capabilities(name="MAI-Image-2.6", min_side=768, max_pixels=1_048_576)

#: FLUX.2 [pro] and [flex]. 4 MP covers every Instagram size natively, so
#: nothing is upscaled and the story format stops being a special case.
FLUX2 = Capabilities(
    name="FLUX.2", min_side=256, max_pixels=4_000_000, multiple_of=32
)

CAPABILITIES = {"mai": MAI, "flux": FLUX2}


class DimensionError(ValueError):
    """Raised when a requested delivery size cannot be satisfied at all."""


@dataclass(frozen=True)
class GenerationPlan:
    """How to get from a MAI request to a delivery-ready canvas."""

    target_w: int
    target_h: int
    gen_w: int
    gen_h: int
    requires_crop: bool

    @property
    def target_aspect(self) -> float:
        return self.target_w / self.target_h

    @property
    def gen_aspect(self) -> float:
        return self.gen_w / self.gen_h

    @property
    def upscale_factor(self) -> float:
        """Linear scale from the cropped generation to the delivery canvas."""
        if self.requires_crop:
            # After cropping to the target aspect, one dimension is preserved
            # and the other is reduced; the preserved one sets the scale.
            if self.gen_aspect > self.target_aspect:
                return self.target_h / self.gen_h
            return self.target_w / self.gen_w
        return self.target_w / self.gen_w

    def crop_box(self) -> tuple[int, int, int, int]:
        """Centre-crop box (left, top, right, bottom) over the generated image.

        Returns the full frame when no crop is needed, so callers can apply it
        unconditionally.
        """
        if not self.requires_crop:
            return (0, 0, self.gen_w, self.gen_h)

        if self.gen_aspect > self.target_aspect:
            # Too wide: trim the sides.
            new_w = round(self.gen_h * self.target_aspect)
            offset = (self.gen_w - new_w) // 2
            return (offset, 0, offset + new_w, self.gen_h)

        # Too tall: trim top and bottom.
        new_h = round(self.gen_w / self.target_aspect)
        offset = (self.gen_h - new_h) // 2
        return (0, offset, self.gen_w, offset + new_h)


def is_legal(width: int, height: int) -> bool:
    """Whether MAI would accept this exact pair."""
    return (
        width >= MIN_SIDE
        and height >= MIN_SIDE
        and width * height <= MAX_PIXELS
    )


def max_legal_dimensions(aspect: float) -> tuple[int, int]:
    """Largest legal (width, height) whose ratio is as close to ``aspect`` as possible.

    ``aspect`` must already lie inside [MIN_ASPECT, MAX_ASPECT]; callers that
    accept arbitrary targets should go through :func:`resolve_dimensions`,
    which clamps and flags the crop.
    """
    if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
        raise DimensionError(
            f"aspect {aspect:.4f} is outside the generable band "
            f"[{MIN_ASPECT:.4f}, {MAX_ASPECT:.4f}]"
        )

    # Start from the tallest height this aspect could possibly use and walk
    # down. The first candidates are the largest by area, so we keep the one
    # with the smallest ratio error and break ties toward more pixels.
    h_start = min(MAX_SIDE, int(math.isqrt(int(MAX_PIXELS / aspect))) + 1)

    best: tuple[float, int, int, int] | None = None
    for height in range(h_start, MIN_SIDE - 1, -1):
        width = round(height * aspect)
        if not is_legal(width, height):
            continue
        error = abs(width / height - aspect) / aspect
        candidate = (error, -(width * height), width, height)
        if best is None or candidate < best:
            best = candidate
        # An exact hit cannot be improved on, and area only falls from here.
        if error == 0.0:
            break

    if best is None:  # pragma: no cover - unreachable for in-band aspects
        raise DimensionError(f"no legal dimensions for aspect {aspect:.4f}")

    return best[2], best[3]


def _round_to(value: int, grid: int) -> int:
    return value if grid <= 1 else max(grid, round(value / grid) * grid)


def resolve_dimensions(
    target_w: int, target_h: int, caps: Capabilities = MAI
) -> GenerationPlan:
    """Plan a generation for a desired delivery size.

    When the delivery aspect is generable we match it and only upscale. When it
    is not -- 1.91:1 landscape, or 9:16 by a single pixel on MAI -- we generate
    at the nearest legal aspect and mark the plan for cropping.

    With a large enough pixel budget the whole problem disappears: if the
    target fits outright we generate it exactly, and there is nothing to crop
    or upscale.
    """
    if target_w <= 0 or target_h <= 0:
        raise DimensionError("target dimensions must be positive")

    # Best case: the model can just produce what we want.
    if caps.accepts(target_w, target_h):
        snapped_w = _round_to(target_w, caps.multiple_of)
        snapped_h = _round_to(target_h, caps.multiple_of)
        if caps.accepts(snapped_w, snapped_h):
            return GenerationPlan(
                target_w=target_w,
                target_h=target_h,
                gen_w=snapped_w,
                gen_h=snapped_h,
                # A grid snap moves the aspect slightly, so crop back to exact.
                requires_crop=(snapped_w, snapped_h) != (target_w, target_h),
            )

    target_aspect = target_w / target_h
    clamped = min(max(target_aspect, MIN_ASPECT), MAX_ASPECT)

    gen_w, gen_h = max_legal_dimensions(clamped)

    # A crop is needed whenever the generated frame does not already carry the
    # delivery aspect -- either because we clamped, or because the best legal
    # pair lands slightly off the requested ratio.
    gen_aspect = gen_w / gen_h
    requires_crop = abs(gen_aspect - target_aspect) > 1e-9

    plan = GenerationPlan(
        target_w=target_w,
        target_h=target_h,
        gen_w=gen_w,
        gen_h=gen_h,
        requires_crop=requires_crop,
    )
    assert is_legal(plan.gen_w, plan.gen_h), "resolver produced an illegal pair"
    return plan


#: Instagram delivery formats. 4:5 is the feed default; the profile grid crops
#: to 3:4, so a 4:5 post is what people see in feed and 3:4 is what they see on
#: the profile page.
FORMATS: dict[str, tuple[int, int]] = {
    "portrait": (1080, 1350),   # 4:5   feed default
    "grid": (1080, 1440),       # 3:4   profile grid crop
    "story": (1080, 1920),      # 9:16  story / reel
    "square": (1080, 1080),     # 1:1   cross-post
    "landscape": (1080, 566),   # 1.91:1 not generable directly
}


def plan_for(format_key: str, caps: Capabilities = MAI) -> GenerationPlan:
    """Resolve one of the named Instagram formats."""
    try:
        target_w, target_h = FORMATS[format_key]
    except KeyError:
        raise DimensionError(
            f"unknown format {format_key!r}; expected one of {sorted(FORMATS)}"
        ) from None
    return resolve_dimensions(target_w, target_h, caps)
