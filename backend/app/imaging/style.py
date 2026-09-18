"""Extract a style profile from a reference image, locally.

When a user says "make something like this", what they usually mean is the
*look* -- the palette, the lighting, whether it is bright and airy or dark and
moody -- not the literal subject.

All of that can be measured from the pixels. No model call, no quota, no
latency. A vision model would add richer description (camera angle, prop
density, styling), but the expensive part is not the part that matters most,
and this works today.

The output feeds the prompt as **colour words, not hex codes**. Diffusion
models respond to "deep saffron" far better than to "#FF6B00".

Deliberately does not extract the subject. Copying the silhouette of an image
a user uploaded is how you turn "inspired by" into a derivative work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from app.imaging.compose import relative_luminance

#: Colour vocabulary chosen for marketing imagery rather than completeness --
#: these are words a photographer or an art director would actually use, which
#: is also what the model was trained on.
NAMED_COLOURS: dict[str, tuple[int, int, int]] = {
    "deep espresso brown": (48, 32, 24),
    "warm walnut": (92, 62, 40),
    "warm antique gold": (196, 152, 68),
    "bright marigold": (242, 168, 40),
    "deep saffron": (232, 122, 32),
    "terracotta": (188, 96, 66),
    "crimson red": (176, 40, 48),
    "deep maroon": (110, 28, 40),
    "dusty rose": (208, 150, 148),
    "soft cream": (240, 228, 200),
    "bone white": (244, 242, 236),
    "cool grey": (150, 152, 156),
    "charcoal": (52, 54, 58),
    "near black": (18, 18, 20),
    "pale sage": (196, 206, 184),
    "deep forest green": (44, 78, 56),
    "emerald": (32, 132, 96),
    "teal": (36, 122, 130),
    "sky blue": (140, 186, 224),
    "deep navy": (28, 44, 82),
    "royal blue": (44, 74, 168),
    "lavender": (184, 168, 212),
    "deep plum": (84, 44, 78),
    "sand": (216, 194, 158),
    "olive": (128, 122, 62),
    "copper": (176, 112, 68),
    "silver": (196, 198, 202),
}


@dataclass
class StyleProfile:
    palette_names: tuple[str, ...] = ()
    palette_hex: tuple[str, ...] = ()
    brightness: str = "balanced"      # dark | balanced | bright
    contrast: str = "medium"          # low | medium | high
    saturation: str = "natural"       # muted | natural | vivid
    temperature: str = "neutral"      # cool | neutral | warm
    orientation: str = "square"       # portrait | square | landscape
    #: Things never to reproduce from the reference. Extracting style is fine;
    #: copying a silhouette or a visible mark is not.
    do_not_copy: tuple[str, ...] = field(
        default_factory=lambda: (
            "the exact subject or silhouette of the reference",
            "any visible logo, wordmark or packaging text",
            "any recognisable face from the reference",
        )
    )

    def describe_lighting(self) -> str:
        key = {
            ("dark", "high"): "dramatic low-key light with strong directional falloff",
            ("dark", "medium"): "moody low-key light with soft shadows",
            ("dark", "low"): "even, dim ambient light",
            ("balanced", "high"): "directional key light with defined shadows",
            ("balanced", "medium"): "soft diffused key light, gentle shadows",
            ("balanced", "low"): "flat, evenly diffused light",
            ("bright", "high"): "bright daylight with crisp shadows",
            ("bright", "medium"): "bright, airy diffused light",
            ("bright", "low"): "high-key light, almost shadowless",
        }
        return key.get((self.brightness, self.contrast), "soft diffused light")

    def describe_mood(self) -> tuple[str, ...]:
        words: list[str] = []
        words.append({"dark": "moody", "bright": "airy", "balanced": "natural"}[self.brightness])
        words.append({"warm": "warm", "cool": "cool", "neutral": "neutral"}[self.temperature])
        if self.saturation == "vivid":
            words.append("bold")
        elif self.saturation == "muted":
            words.append("understated")
        return tuple(words)


def _nearest_name(rgb: tuple[int, int, int]) -> str:
    """Closest colour word, weighted for perceived difference.

    Plain RGB distance calls navy and forest green neighbours. Weighting the
    channels roughly by luminance contribution is cheap and much closer to how
    the eye ranks them.
    """
    best, best_distance = "", float("inf")
    for name, reference in NAMED_COLOURS.items():
        distance = (
            2.0 * (rgb[0] - reference[0]) ** 2
            + 4.0 * (rgb[1] - reference[1]) ** 2
            + 3.0 * (rgb[2] - reference[2]) ** 2
        )
        if distance < best_distance:
            best, best_distance = name, distance
    return best


def extract_style(image: Image.Image, *, max_colours: int = 4) -> StyleProfile:
    """Measure the look of a reference image."""
    rgb = image.convert("RGB")

    # Small enough to be fast, large enough that a small bright accent still
    # survives into the palette.
    thumb = rgb.resize((96, 96), Image.LANCZOS)

    # --- palette ---------------------------------------------------------
    # Pillow's own quantiser is a perfectly good k-means substitute here and
    # avoids pulling in numpy for one call.
    quantised = thumb.quantize(colors=max_colours, method=Image.Quantize.FASTOCTREE)
    palette = quantised.getpalette() or []
    counts = sorted(quantised.getcolors() or [], reverse=True)

    names: list[str] = []
    hexes: list[str] = []
    for _, index in counts[:max_colours]:
        colour = tuple(palette[index * 3 : index * 3 + 3])
        if len(colour) != 3:
            continue
        name = _nearest_name(colour)  # type: ignore[arg-type]
        if name not in names:
            names.append(name)
            hexes.append("#{:02X}{:02X}{:02X}".format(*colour))

    # --- tone ------------------------------------------------------------
    raw = thumb.tobytes()
    pixels = [
        (raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)
    ]
    luminances = [relative_luminance(p) for p in pixels]
    mean_luma = sum(luminances) / len(luminances)

    ordered = sorted(luminances)
    p10 = ordered[len(ordered) // 10]
    p90 = ordered[-max(1, len(ordered) // 10)]
    spread = p90 - p10

    brightness = "dark" if mean_luma < 0.22 else "bright" if mean_luma > 0.55 else "balanced"
    contrast = "low" if spread < 0.25 else "high" if spread > 0.6 else "medium"

    # --- saturation and temperature --------------------------------------
    saturations, warmth = [], 0.0
    for r, g, b in pixels:
        high, low = max(r, g, b), min(r, g, b)
        saturations.append((high - low) / high if high else 0.0)
        warmth += (r - b)
    mean_saturation = sum(saturations) / len(saturations)
    mean_warmth = warmth / len(pixels)

    saturation = (
        "muted" if mean_saturation < 0.18
        else "vivid" if mean_saturation > 0.45
        else "natural"
    )
    temperature = "warm" if mean_warmth > 12 else "cool" if mean_warmth < -12 else "neutral"

    # --- shape -----------------------------------------------------------
    ratio = rgb.width / rgb.height
    orientation = "portrait" if ratio < 0.9 else "landscape" if ratio > 1.1 else "square"

    return StyleProfile(
        palette_names=tuple(names),
        palette_hex=tuple(hexes),
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        temperature=temperature,
        orientation=orientation,
    )


def apply_style(brief, profile: StyleProfile):
    """Merge a style profile into a CreativeBrief.

    Style fields only. The subject stays whatever the user asked for -- that
    separation is what makes this "in the style of" rather than a copy.
    """
    if profile.palette_names:
        brief.palette_names = profile.palette_names
    brief.lighting = profile.describe_lighting()
    brief.mood = profile.describe_mood()
    brief.must_not_depict = tuple(brief.must_not_depict) + profile.do_not_copy
    brief.source_mode = "style_transfer"
    return brief
