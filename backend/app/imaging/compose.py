"""Deterministic image composition: crop, upscale, logo.

The logo is composited here from the brand's real file and is never described
to the image model. A diffusion model produces a *plausible* logo, which is a
wrong logo -- subtly wrong letterforms that still ship -- and describing a mark
in a prompt is asking the model to reproduce a trademark, which is one of the
risks Microsoft's own responsible-AI notes call out for these models.

Compositing instead makes the mark byte-exact, instant, free, and re-renderable
the moment the brand updates its logo.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, ImageDraw

from app.imaging.dimensions import GenerationPlan
from app.imaging.safezones import assert_within, safe_rect

Anchor = Literal[
    "top_left", "top_right", "bottom_left", "bottom_right", "bottom_center"
]

#: Above this mean luminance the background is light, so the mark must be dark.
LIGHT_BACKGROUND = 0.62
#: Below this it is dark, so the mark must be light.
DARK_BACKGROUND = 0.38
#: WCAG AA for graphical objects. Below this we add a scrim.
MIN_CONTRAST = 4.5


def load_png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def finalize_base(png: bytes, plan: GenerationPlan) -> Image.Image:
    """Crop the generated frame to the delivery aspect, then scale to size.

    Order matters: crop first so we never scale pixels we are about to discard.
    """
    with load_png(png) as raw:
        image = raw.convert("RGB")

    if (image.width, image.height) != (plan.gen_w, plan.gen_h):
        raise ValueError(
            f"generated image is {image.width}x{image.height}, "
            f"expected {plan.gen_w}x{plan.gen_h}"
        )

    image = image.crop(plan.crop_box())
    if (image.width, image.height) != (plan.target_w, plan.target_h):
        # Lanczos: the story format is the worst case at ~1.41x, where cheaper
        # filters visibly soften edges.
        image = image.resize((plan.target_w, plan.target_h), Image.LANCZOS)
    return image


# --------------------------------------------------------------------------
# Luminance and contrast
# --------------------------------------------------------------------------

def _linearize(channel: float) -> float:
    """sRGB -> linear light, per WCAG."""
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_linearize(c / 255.0) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _patch_luminances(
    image: Image.Image, box: tuple[int, int, int, int]
) -> list[float]:
    patch = image.crop(box).convert("RGB")
    # Downsample before measuring; we want the perceived tone of the area, not
    # a pixel-exact statistic. BOX averaging is what makes a single bright
    # speck stop mattering while a bright *region* still does.
    patch = patch.resize((16, 16), Image.BOX)
    raw = patch.tobytes()
    return [
        relative_luminance((raw[i], raw[i + 1], raw[i + 2]))
        for i in range(0, len(raw), 3)
    ]


def patch_luminance(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    """Mean relative luminance of a region, used to report what was behind."""
    values = _patch_luminances(image, box)
    return sum(values) / len(values)


def patch_extremes(
    image: Image.Image, box: tuple[int, int, int, int], *, tail: float = 0.1
) -> tuple[float, float]:
    """The dark and bright ends of a region, ignoring the extreme tails.

    The mean is the wrong statistic for choosing ink, and it fails in the
    direction that hurts. A real placement here measured a mean luminance of
    0.14 -- comfortably "dark", so white was chosen and scored 5.46:1 against
    it. The patch actually ran from grey 0 to grey 234: the mark was white on
    near-white concrete for a third of its width, and the 5.46 was computed
    against a tone that appeared nowhere in the image.

    Contrast has to hold everywhere the mark sits, so both ends are returned
    and the caller scores against whichever one is hostile to its ink. The
    tails are trimmed so one stray pixel cannot force a scrim onto an
    otherwise clean background.
    """
    values = sorted(_patch_luminances(image, box))
    index = max(0, min(len(values) - 1, round(len(values) * tail)))
    return values[index], values[len(values) - 1 - index]


# --------------------------------------------------------------------------
# Logo
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LogoPlacement:
    box: tuple[int, int, int, int]
    variant: str
    scrim_alpha: float
    background_luminance: float
    contrast: float


def trim_to_ink(logo: Image.Image, *, threshold: int = 8) -> Image.Image:
    """Crop a logo to its visible mark, discarding transparent margin.

    Exported brand assets routinely carry a large transparent border -- one
    real upload here was 1536x1024 with the mark filling 67% of the width and
    8.5% of the pixels. Sizing the placement from the *file* rather than the
    mark then silently shrinks the brand by a third, and it reads on the
    finished creative as "the logo didn't work", not as a sizing bug.

    Trimming first makes ``width_pct`` mean what it says regardless of how the
    file was exported.
    """
    logo = logo.convert("RGBA")
    alpha = logo.getchannel("A")
    # point() rather than a raw getbbox() so near-transparent halo pixels --
    # normal around antialiased edges -- do not count as ink and defeat the
    # trim entirely.
    box = alpha.point(lambda v: 255 if v > threshold else 0).getbbox()
    if box is None:
        # Fully transparent. Nothing to trim to, and cropping to an empty box
        # would raise; let the caller deal with an invisible logo.
        return logo
    return logo.crop(box)


def derive_variants(logo: Image.Image) -> dict[str, Image.Image]:
    """Original plus knockout mono versions.

    Most brands supply one file. A white and a black knockout cover the cases
    where the original would disappear into the background.
    """
    logo = trim_to_ink(logo)
    alpha = logo.getchannel("A")

    def knockout(colour: tuple[int, int, int]) -> Image.Image:
        solid = Image.new("RGBA", logo.size, (*colour, 255))
        solid.putalpha(alpha)
        return solid

    return {
        "original": logo,
        "light": knockout((255, 255, 255)),
        "dark": knockout((17, 17, 17)),
    }


def logo_box(
    *,
    canvas_w: int,
    canvas_h: int,
    logo_w: int,
    logo_h: int,
    anchor: Anchor,
    format_key: str,
    width_pct: float = 0.12,
    padding_pct: float = 0.02,
    max_height_pct: float = 0.14,
) -> tuple[int, int, int, int]:
    """Where the logo goes, in absolute pixels.

    Anchored inside the *safe rect*, not the canvas. Anchoring to the canvas
    edge looks right in a design tool and then lands under the Reels caption
    tray or the feed handle row. Deriving the position from the safe rect makes
    compliance structural rather than something we check afterwards.

    Size is a fraction of canvas width so the mark reads consistently across
    formats; padding is a fraction of the short edge, applied inward from the
    safe rect.

    Width alone is not enough once a mark can be a *wordmark*. "Kesari Silks"
    typesets at roughly 5.5:1, so 12% of a 1080px canvas is 130px wide and
    eleven pixels tall -- present, correctly placed, and completely
    illegible. Both constraints are computed and the tighter one wins, which
    leaves square-ish logos exactly where they were (a 1:1 mark at 12% of
    width is under 10% of height) and lets a long name be sized by its
    height instead.
    """
    by_width = max(1, round(canvas_w * width_pct)) / logo_w
    by_height = max(1, round(canvas_h * max_height_pct)) / logo_h
    scale = min(by_width, by_height)

    target_w = max(1, round(logo_w * scale))
    target_h = max(1, round(logo_h * scale))

    safe = safe_rect(canvas_w, canvas_h, format_key)
    pad = round(min(canvas_w, canvas_h) * padding_pct)

    if anchor.endswith("left"):
        left = safe.left + pad
    elif anchor.endswith("center"):
        left = safe.left + (safe.width - target_w) // 2
    else:
        left = safe.right - target_w - pad

    top = safe.top + pad if anchor.startswith("top") else safe.bottom - target_h - pad
    return (left, top, left + target_w, top + target_h)


def choose_variant(
    base: Image.Image, box: tuple[int, int, int, int], variants: dict[str, Image.Image]
) -> tuple[str, float, float]:
    """Pick the logo variant with the best contrast against what is behind it.

    Scored against the worst tone the mark actually lands on rather than the
    patch average -- see :func:`patch_extremes` for why the average is not
    merely imprecise but wrong in the direction that produces invisible logos.

    Returns (variant name, scrim alpha, achieved contrast).
    """
    darkest, brightest = patch_extremes(base, box)

    candidates = {
        "dark": (17, 17, 17),
        "light": (255, 255, 255),
    }
    # White ink is threatened by the bright end, black ink by the dark end.
    hostile = {
        "light": _luminance_to_rgb(brightest),
        "dark": _luminance_to_rgb(darkest),
    }
    scored = {
        name: contrast_ratio(ink, hostile[name]) for name, ink in candidates.items()
    }
    variant = max(scored, key=scored.__getitem__)
    achieved = scored[variant]

    if achieved >= MIN_CONTRAST:
        return variant, 0.0, achieved

    # Mid-tone or busy background: darken (or lighten) behind the mark until it
    # clears AA. A scrim treats the *background*, unlike a drop shadow, which
    # most brand guidelines forbid on the mark itself.
    for alpha in (0.35, 0.5, 0.65, 0.8):
        if variant == "light":
            scrimmed = _blend(hostile["light"], (0, 0, 0), alpha)
        else:
            scrimmed = _blend(hostile["dark"], (255, 255, 255), alpha)
        achieved = contrast_ratio(candidates[variant], scrimmed)
        if achieved >= MIN_CONTRAST:
            return variant, alpha, achieved

    return variant, 0.8, achieved


def _luminance_to_rgb(luminance: float) -> tuple[float, float, float]:
    """A grey of equivalent relative luminance, for contrast maths."""
    # Invert the sRGB transfer function for a neutral grey.
    if luminance <= 0.0031308:
        channel = luminance * 12.92
    else:
        channel = 1.055 * (luminance ** (1 / 2.4)) - 0.055
    value = max(0.0, min(255.0, channel * 255.0))
    return (value, value, value)


def _blend(
    base: tuple[float, float, float], over: tuple[float, float, float], alpha: float
) -> tuple[float, float, float]:
    return tuple(b * (1 - alpha) + o * alpha for b, o in zip(base, over))


def composite_logo(
    base: Image.Image,
    logo: Image.Image,
    *,
    format_key: str,
    anchor: Anchor = "bottom_right",
    width_pct: float = 0.12,
    padding_pct: float = 0.02,
    max_height_pct: float = 0.14,
    clear_space_ratio: float = 0.25,
    enforce_safe_zone: bool = True,
) -> tuple[Image.Image, LogoPlacement]:
    """Place the brand mark, choosing its variant from what sits behind it."""
    canvas = base.convert("RGBA")
    variants = derive_variants(logo)
    source = variants["original"]

    box = logo_box(
        canvas_w=canvas.width,
        canvas_h=canvas.height,
        logo_w=source.width,
        logo_h=source.height,
        anchor=anchor,
        format_key=format_key,
        width_pct=width_pct,
        padding_pct=padding_pct,
        max_height_pct=max_height_pct,
    )

    if enforce_safe_zone:
        assert_within(
            box,
            width=canvas.width,
            height=canvas.height,
            format_key=format_key,
            what="logo",
        )

    variant, scrim_alpha, achieved = choose_variant(canvas, box, variants)
    luminance = patch_luminance(canvas, box)

    if scrim_alpha > 0:
        canvas = _apply_scrim(canvas, box, variant, scrim_alpha, clear_space_ratio)

    mark = variants[variant].resize(
        (box[2] - box[0], box[3] - box[1]), Image.LANCZOS
    )
    canvas.alpha_composite(mark, (box[0], box[1]))

    return canvas, LogoPlacement(
        box=box,
        variant=variant,
        scrim_alpha=scrim_alpha,
        background_luminance=luminance,
        contrast=achieved,
    )


def _apply_scrim(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    variant: str,
    alpha: float,
    clear_space_ratio: float,
) -> Image.Image:
    """Soften the area behind the mark so it clears the contrast floor."""
    left, top, right, bottom = box
    pad = round((bottom - top) * clear_space_ratio)
    region = (
        max(0, left - pad),
        max(0, top - pad),
        min(canvas.width, right + pad),
        min(canvas.height, bottom + pad),
    )

    colour = (0, 0, 0) if variant == "light" else (255, 255, 255)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius = max(4, (region[3] - region[1]) // 6)
    draw.rounded_rectangle(
        region, radius=radius, fill=(*colour, int(alpha * 255))
    )
    return Image.alpha_composite(canvas, layer)


def composite_overlay(base: Image.Image, overlay_png: bytes) -> Image.Image:
    """Lay a transparent text overlay (rendered by Chromium) over the base."""
    with load_png(overlay_png) as raw:
        overlay = raw.convert("RGBA")
    if overlay.size != base.size:
        raise ValueError(
            f"overlay is {overlay.size}, base is {base.size}; "
            "the overlay must be rendered at the delivery canvas size"
        )
    return Image.alpha_composite(base.convert("RGBA"), overlay)


# --------------------------------------------------------------------------
# Where the copy can actually go
# --------------------------------------------------------------------------

#: The eight regions a brief may reserve, and that the typesetter can use.
TEXT_REGIONS: tuple[str, ...] = (
    "top_left", "top", "top_right",
    "left", "right",
    "bottom_left", "bottom", "bottom_right",
)


def region_busyness(image: Image.Image, region: str) -> float:
    """How much detail sits in one region, 0 (flat) to 1 (busy).

    Edge energy and tonal spread, combined. Either alone is misleading: a
    smooth steep gradient has almost no edges and still swallows text, and a
    finely textured wall has plenty of edges while staying tonally flat
    enough to read against.
    """
    from PIL import ImageFilter

    thirds = {
        "top": (0.0, 0.0, 1.0, 0.42),
        "bottom": (0.0, 0.58, 1.0, 1.0),
        "left": (0.0, 0.0, 0.45, 1.0),
        "right": (0.55, 0.0, 1.0, 1.0),
        "top_left": (0.0, 0.0, 0.55, 0.45),
        "top_right": (0.45, 0.0, 1.0, 0.45),
        "bottom_left": (0.0, 0.55, 0.55, 1.0),
        "bottom_right": (0.45, 0.55, 1.0, 1.0),
    }
    left, top, right, bottom = thirds.get(region, thirds["bottom_left"])
    box = (
        round(left * image.width), round(top * image.height),
        round(right * image.width), round(bottom * image.height),
    )

    # Downsampled first: we want the tone of the area as a reader perceives
    # it, not per-pixel sensor noise, which would score every photograph as
    # uniformly busy.
    patch = image.crop(box).convert("L").resize((48, 48), Image.LANCZOS)
    edges = patch.filter(ImageFilter.FIND_EDGES).tobytes()
    edge_energy = sum(edges) / (len(edges) * 255)

    values = patch.tobytes()
    mean = sum(values) / len(values)
    spread = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 / 128

    return min(1.0, edge_energy * 2.2 + spread * 0.8)


def best_text_region(
    image: Image.Image, *, prefer: str = "bottom_left", loyalty: float = 0.12
) -> tuple[str, float]:
    """Choose the calmest region of this particular image for the copy.

    The brief already names a region and asks the model to keep it clean, but
    asking is not the same as getting: diffusion models put the subject where
    they like, and a headline typeset into whatever the brief *hoped* would be
    empty lands on the product about as often as not.

    Measuring the frame that came back settles it per image, which is also
    what stops every creative in a batch sharing one layout.

    ``loyalty`` is a handicap in the brief's favour. The reserved region was
    briefed, prompted for and usually delivered, so it should not lose to a
    marginally calmer corner -- only to a clearly calmer one.
    """
    scored = {region: region_busyness(image, region) for region in TEXT_REGIONS}
    if prefer in scored:
        scored[prefer] = max(0.0, scored[prefer] - loyalty)
    best = min(scored, key=scored.__getitem__)
    return best, scored[best]
