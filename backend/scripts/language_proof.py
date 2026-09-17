"""Render one campaign in every v1 locale and build a contact sheet.

This is the visual gate for the riskiest part of the product. Indic shaping
failures are not caught by assertions -- a detached matra or an unformed
conjunct still renders, still passes a pixel-count check, and is only obvious
to someone who reads the script. So we render the real strings, at real sizes,
and look at them.

    python scripts/language_proof.py

The sample copy is deliberately conjunct-heavy: शुद्ध (द्ध), त्योहार (त्य),
বিশুদ্ধতার (দ্ধ), స్వచ్ఛత (చ్ఛ). If the shaping engine is wrong these are the
first things to break.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw  # noqa: E402

from app.copy.languages import DEFAULT_LOCALES, get_locale  # noqa: E402
from app.foundry.mock_client import MockImageClient  # noqa: E402
from app.imaging.compose import (  # noqa: E402
    composite_logo,
    composite_overlay,
    finalize_base,
    to_png,
)
from app.imaging.dimensions import plan_for  # noqa: E402
from app.imaging.overlay import OverlayRenderer, TextBox  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "storage" / "renders"

# One campaign, seven locales. Written per language rather than translated --
# note the Bengali line refers to the festival a Bengali audience actually
# celebrates, which is the point of transcreation.
COPY: dict[str, tuple[str, str, str]] = {
    "en": (
        "The festival of purity",
        "Cold-pressed coconut oil",
        "Shop now",
    ),
    "hi": (
        "शुद्धता का त्योहार",
        "कोल्ड-प्रेस्ड नारियल तेल",
        "अभी खरीदें",
    ),
    "hi-Latn": (
        "Shuddhata ka tyohaar",
        "Cold-pressed nariyal tel",
        "Abhi khareedein",
    ),
    "mr": (
        "शुद्धतेचा सण",
        "कोल्ड-प्रेस्ड खोबरेल तेल",
        "आत्ताच खरेदी करा",
    ),
    "bn": (
        "বিশুদ্ধতার উৎসব",
        "কোল্ড-প্রেসড নারকেল তেল",
        "এখনই কিনুন",
    ),
    "ta": (
        "தூய்மையின் திருவிழா",
        "செக்கு தேங்காய் எண்ணெய்",
        "இப்போதே வாங்குங்கள்",
    ),
    "te": (
        "స్వచ్ఛత పండుగ",
        "కోల్డ్ ప్రెస్డ్ కొబ్బరి నూనె",
        "ఇప్పుడే కొనండి",
    ),
}

PROMPT = (
    "Commercial product photography. A clear glass bottle of cold-pressed "
    "coconut oil with a matte gold cap on dark walnut, warm bokeh behind. "
    "Leave the lower left 32% as clean soft gradient reserved for overlay. "
    "There is no text, no lettering, no words anywhere in this image."
)


def fake_logo() -> Image.Image:
    image = Image.new("RGBA", (420, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 20, 80, 100), fill=(255, 255, 255, 255))
    draw.rectangle((100, 45, 420, 75), fill=(255, 255, 255, 255))
    return image


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    plan = plan_for("portrait")
    client = MockImageClient()
    renderer = OverlayRenderer()
    logo = fake_logo()

    # One generation. Every locale reuses it -- this is the whole economic
    # argument for the architecture under a 2 RPM ceiling.
    result = await client.generate(
        prompt=PROMPT, width=plan.gen_w, height=plan.gen_h
    )
    base = finalize_base(result.png, plan)
    print(f"base generated once at {plan.gen_w}x{plan.gen_h} -> {base.size}")

    tiles: list[Image.Image] = []
    try:
        for key in DEFAULT_LOCALES:
            locale = get_locale(key)
            headline, subhead, cta = COPY[key]
            overlay_png, fit = await renderer.render(
                width=base.width,
                height=base.height,
                locale=locale,
                headline=headline,
                subhead=subhead,
                cta=cta,
                box=TextBox(left=0.07, top=0.52, width=0.62, height=0.34),
            )
            composed = composite_overlay(base, overlay_png)
            final, placement = composite_logo(
                composed.convert("RGB"), logo, format_key="portrait"
            )
            path = OUT / f"proof_{key}.png"
            path.write_bytes(to_png(final))
            flags = []
            if fit.below_minimum:
                flags.append("BELOW MIN SIZE")
            if fit.did_break:
                flags.append("WORD BROKEN")
            flag = ("  <-- " + ", ".join(flags)) if flags else ""
            print(
                f"  {locale.label:<9} {locale.script}  headline={fit.headline_px:>3}px "
                f"({fit.headline_lines} ln) sub={fit.subhead_px:>3}px "
                f"cta={fit.cta_px:>3}px logo={placement.variant}{flag}"
            )
            tiles.append(final.convert("RGB"))
    finally:
        await renderer.aclose()

    # Contact sheet, scaled down so all seven fit on screen at once.
    scale = 0.26
    tw, th = int(base.width * scale), int(base.height * scale)
    sheet = Image.new("RGB", (tw * len(tiles), th), (12, 12, 14))
    for index, tile in enumerate(tiles):
        sheet.paste(tile.resize((tw, th), Image.LANCZOS), (index * tw, 0))
    sheet_path = OUT / "proof_contact_sheet.png"
    sheet.save(sheet_path)
    print(f"\ncontact sheet: {sheet_path}")


if __name__ == "__main__":
    asyncio.run(main())
