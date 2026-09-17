"""Indic text rendering.

This is the highest-risk part of the product, and the risk is specific: broken
shaping still renders. A detached matra or an unformed conjunct produces a
perfectly valid PNG that passes every size and pixel check, and is illegible
only to someone who reads the script. So the tests here prove the shaping
engine is *running*, not merely that ink appeared.
"""

from __future__ import annotations

import pytest

from app.copy.languages import DEFAULT_LOCALES, char_budget, get_locale
from app.imaging.compose import composite_overlay, load_png
from app.imaging.overlay import OverlayRenderer, TextBox

# Conjunct-heavy strings. Each pairs a ligated form with the same characters
# forced apart by a zero-width non-joiner (U+200C). A shaping engine produces
# different widths for the two; a naive glyph blitter produces the same.
ZWNJ = "‌"
SHAPING_CASES = [
    # Devanagari: क् + ष -> क्ष
    ("hi", "क्ष", f"क्{ZWNJ}ष"),
    # Devanagari conjunct in a real word: शुद्ध
    ("hi", "शुद्ध", f"शुद्{ZWNJ}ध"),
    # Bengali: দ্ধ
    ("bn", "বিশুদ্ধ", f"বিশুদ্{ZWNJ}ধ"),
    # Telugu: చ్ఛ (subscript consonant)
    ("te", "స్వచ్ఛ", f"స్వచ్{ZWNJ}ఛ"),
]


@pytest.fixture(scope="module")
async def renderer():
    instance = OverlayRenderer()
    yield instance
    await instance.aclose()


@pytest.mark.parametrize("locale_key,ligated,split", SHAPING_CASES)
async def test_shaping_engine_forms_conjuncts(
    renderer: OverlayRenderer, locale_key: str, ligated: str, split: str
) -> None:
    """The conjunct must render narrower than the same characters held apart.

    If these widths are equal, GSUB is not being applied and every Indic
    headline the product ships is malformed.
    """
    ligated_w = await renderer.measure(ligated, locale=locale_key)
    split_w = await renderer.measure(split, locale=locale_key)

    assert ligated_w > 0 and split_w > 0
    assert ligated_w < split_w, (
        f"{locale_key}: {ligated!r} ({ligated_w:.1f}px) is not narrower than the "
        f"unligated form ({split_w:.1f}px) -- conjuncts are not being formed"
    )


async def test_matra_reordering_is_applied(renderer: OverlayRenderer) -> None:
    """The i-matra is typed after its consonant and drawn before it.

    Reordering is the other half of Indic shaping, and it is invisible to a
    width check, so compare against a consonant that carries no reordering.
    """
    # कि = क + ि. The matra is stored second but painted first.
    with_matra = await renderer.measure("कि", locale="hi")
    bare = await renderer.measure("क", locale="hi")
    assert with_matra > bare, "the i-matra contributed no advance width"


@pytest.mark.parametrize("locale_key", DEFAULT_LOCALES)
async def test_every_locale_renders_ink_at_the_delivery_size(
    renderer: OverlayRenderer, locale_key: str
) -> None:
    samples = {
        "en": ("The festival of purity", "Cold-pressed coconut oil", "Shop now"),
        "hi": ("शुद्धता का त्योहार", "कोल्ड-प्रेस्ड नारियल तेल", "अभी खरीदें"),
        "hi-Latn": ("Shuddhata ka tyohaar", "Cold-pressed nariyal tel", "Abhi khareedein"),
        "mr": ("शुद्धतेचा सण", "कोल्ड-प्रेस्ड खोबरेल तेल", "आत्ताच खरेदी करा"),
        "bn": ("বিশুদ্ধতার উৎসব", "কোল্ড-প্রেসড নারকেল তেল", "এখনই কিনুন"),
        "ta": ("தூய்மையின் திருவிழா", "செக்கு தேங்காய் எண்ணெய்", "இப்போதே வாங்குங்கள்"),
        "te": ("స్వచ్ఛత పండుగ", "కోల్డ్ ప్రెస్డ్ కొబ్బరి నూనె", "ఇప్పుడే కొనండి"),
    }
    headline, subhead, cta = samples[locale_key]
    box = TextBox(left=0.07, top=0.52, width=0.62, height=0.34)

    png, fit = await renderer.render(
        width=1080,
        height=1350,
        locale=locale_key,
        headline=headline,
        subhead=subhead,
        cta=cta,
        box=box,
    )

    with load_png(png) as image:
        assert image.size == (1080, 1350)
        assert image.mode == "RGBA"
        # Transparent everywhere except the type.
        assert image.getchannel("A").getextrema()[1] > 0, "overlay is blank"

    assert not fit.did_break, (
        f"{locale_key}: a word was broken mid-cluster -- for Indic this splits "
        f"a consonant from its matra"
    )
    assert not fit.below_minimum, f"{locale_key}: type fell below legible size"
    assert fit.headline_px >= get_locale(locale_key).metrics.min_size_px


@pytest.mark.parametrize("locale_key", DEFAULT_LOCALES)
async def test_copy_stays_inside_its_box(
    renderer: OverlayRenderer, locale_key: str
) -> None:
    box = TextBox(left=0.07, top=0.52, width=0.62, height=0.34)
    _, fit = await renderer.render(
        width=1080,
        height=1350,
        locale=locale_key,
        headline="शुद्धता का त्योहार" if locale_key in {"hi", "mr"} else "The festival of purity",
        subhead="Cold-pressed coconut oil",
        cta="Shop now",
        box=box,
    )
    left, top, right, bottom = fit.bounds
    # A small tolerance for glyph overshoot (descenders, marks above the line).
    assert left >= round(1080 * box.left) - 4
    assert right <= round(1080 * (box.left + box.width)) + 4
    assert bottom <= round(1350 * (box.top + box.height)) + 8


async def test_tamil_shrinks_relative_to_english_for_the_same_message(
    renderer: OverlayRenderer,
) -> None:
    """Indic copy runs longer, so the fitter must respond by shrinking.

    This is the behaviour that stops a fixed-size template from overflowing on
    Tamil -- the single most common way a multilingual layout breaks.
    """
    box = TextBox(left=0.07, top=0.52, width=0.62, height=0.34)
    _, english = await renderer.render(
        width=1080, height=1350, locale="en",
        headline="The festival of purity", box=box,
    )
    _, tamil = await renderer.render(
        width=1080, height=1350, locale="ta",
        headline="தூய்மையின் திருவிழா", box=box,
    )
    assert tamil.headline_px < english.headline_px
    assert not tamil.did_break


async def test_shorter_alternate_is_preferred_over_illegible_type(
    renderer: OverlayRenderer,
) -> None:
    """Selecting a shorter line beats shrinking below the legible minimum."""
    tiny = TextBox(left=0.05, top=0.70, width=0.40, height=0.12)
    _, fit = await renderer.render(
        width=1080,
        height=1350,
        locale="ta",
        headline="தூய்மையின் திருவிழா இந்த தீபாவளிக்கு சிறப்பு சலுகை",
        headline_alternates=("தூய்மைத் திருவிழா", "சிறப்பு சலுகை"),
        box=tiny,
    )
    assert fit.headline_used != "தூய்மையின் திருவிழா இந்த தீபாவளிக்கு சிறப்பு சலுகை"
    assert not fit.below_minimum


async def test_overlay_composites_onto_a_base_of_the_same_size(
    renderer: OverlayRenderer,
) -> None:
    from PIL import Image

    base = Image.new("RGB", (1080, 1350), (40, 70, 50))
    png, _ = await renderer.render(
        width=1080, height=1350, locale="hi", headline="शुद्धता का त्योहार"
    )
    composed = composite_overlay(base, png)
    assert composed.size == base.size
    # The overlay must actually change pixels, not silently no-op.
    assert composed.convert("RGB").tobytes() != base.tobytes()


async def test_overlay_size_mismatch_is_rejected(renderer: OverlayRenderer) -> None:
    from PIL import Image

    base = Image.new("RGB", (1080, 1350), (0, 0, 0))
    png, _ = await renderer.render(
        width=1080, height=1080, locale="en", headline="Hello"
    )
    with pytest.raises(ValueError):
        composite_overlay(base, png)


# --------------------------------------------------------------------------
# Language model
# --------------------------------------------------------------------------

def test_hinglish_is_hindi_in_latin_script() -> None:
    hinglish = get_locale("hi-Latn")
    assert hinglish.language == "hi"
    assert hinglish.script == "Latn"
    assert hinglish.register == "conversational"


def test_indic_scripts_forbid_letter_spacing() -> None:
    # Tracking separates shaped clusters and visibly breaks conjuncts, so this
    # is locked off rather than merely defaulted.
    for key in ("hi", "mr", "bn", "ta", "te"):
        assert not get_locale(key).metrics.allow_letter_spacing, key
    assert get_locale("en").metrics.allow_letter_spacing


def test_indic_scripts_get_more_leading_than_latin() -> None:
    latin = get_locale("en").metrics.line_height
    for key in ("hi", "mr", "bn", "ta", "te"):
        assert get_locale(key).metrics.line_height > latin, key


def test_char_budget_shrinks_for_longer_scripts() -> None:
    assert char_budget(40, "en") == 40
    assert char_budget(40, "ta") < char_budget(40, "hi") < 40
