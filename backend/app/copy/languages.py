"""Languages, scripts, and the typographic metrics each script needs.

Language is not one dropdown. On Indian Instagram a large share of real
marketing copy is Romanised Hindi ("Hinglish") rather than Devanagari, because
many people speak Hindi fluently but read Devanagari slowly. Romanised output
is therefore a first-class variant, not a fallback, and the model is a triple:

    (language, script, register)

Hinglish is simply ``hi`` + ``Latn`` + ``conversational``.

The per-script metrics here are not cosmetic. Latin leading applied to
Devanagari collides matras with the line above; letter-spacing applied to any
Indic script pulls shaped clusters apart and visibly breaks conjuncts. These
are enforced in the renderer rather than left to a template author.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScriptMetrics:
    code: str
    name: str
    #: CSS font stack. Noto faces are SIL OFL, so they can ship with the app.
    font_stack: tuple[str, ...]
    #: Minimum line-height. Indic scripts carry marks above and below the base
    #: glyph, so Latin's ~1.2 clips them.
    line_height: float
    #: Smallest legible size on a 1080px-wide canvas, viewed on a phone.
    min_size_px: int
    #: Indic shaping is destroyed by tracking. Locked off, not merely defaulted.
    allow_letter_spacing: bool = False
    rtl: bool = False
    #: Indic text runs longer than English for the same meaning, so copy
    #: budgets have to be scaled per script.
    length_factor: float = 1.0


LATIN = ScriptMetrics(
    code="Latn",
    name="Latin",
    font_stack=("Inter", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"),
    line_height=1.2,
    min_size_px=28,
    allow_letter_spacing=True,
    length_factor=1.0,
)

DEVANAGARI = ScriptMetrics(
    code="Deva",
    name="Devanagari",
    font_stack=("Noto Sans Devanagari", "Nirmala UI", "sans-serif"),
    line_height=1.5,
    min_size_px=34,
    length_factor=1.18,
)

BENGALI = ScriptMetrics(
    code="Beng",
    name="Bengali",
    font_stack=("Noto Sans Bengali", "Nirmala UI", "sans-serif"),
    line_height=1.5,
    min_size_px=34,
    length_factor=1.20,
)

TAMIL = ScriptMetrics(
    code="Taml",
    name="Tamil",
    font_stack=("Noto Sans Tamil", "Nirmala UI", "sans-serif"),
    line_height=1.45,
    min_size_px=36,
    # Tamil needs a larger optical size than Latin at the same nominal size,
    # and runs very long -- it is the usual culprit when a layout overflows.
    length_factor=1.35,
)

TELUGU = ScriptMetrics(
    code="Telu",
    name="Telugu",
    font_stack=("Noto Sans Telugu", "Nirmala UI", "sans-serif"),
    line_height=1.45,
    min_size_px=36,
    length_factor=1.30,
)

SCRIPTS: dict[str, ScriptMetrics] = {
    s.code: s for s in (LATIN, DEVANAGARI, BENGALI, TAMIL, TELUGU)
}


@dataclass(frozen=True)
class Locale:
    """One deliverable language variant."""

    key: str
    language: str
    script: str
    register: str
    label: str
    #: BCP-47 tag for the ``lang`` attribute. Drives the browser's `locl`
    #: lookups -- Marathi wants different letterforms for some Devanagari
    #: glyphs than Hindi does, and the browser only knows that if we say so.
    bcp47: str
    native_name: str = ""

    @property
    def metrics(self) -> ScriptMetrics:
        return SCRIPTS[self.script]


#: v1 languages. Hinglish earns its place on volume, not completeness.
LOCALES: dict[str, Locale] = {
    loc.key: loc
    for loc in (
        Locale("en", "en", "Latn", "neutral", "English", "en-IN", "English"),
        Locale("hi", "hi", "Deva", "neutral", "Hindi", "hi-IN", "हिन्दी"),
        Locale(
            "hi-Latn",
            "hi",
            "Latn",
            "conversational",
            "Hinglish",
            "hi-Latn",
            "Hinglish",
        ),
        Locale("mr", "mr", "Deva", "neutral", "Marathi", "mr-IN", "मराठी"),
        Locale("bn", "bn", "Beng", "neutral", "Bengali", "bn-IN", "বাংলা"),
        Locale("ta", "ta", "Taml", "neutral", "Tamil", "ta-IN", "தமிழ்"),
        Locale("te", "te", "Telu", "neutral", "Telugu", "te-IN", "తెలుగు"),
    )
}

DEFAULT_LOCALES: tuple[str, ...] = ("en", "hi", "hi-Latn", "mr", "bn", "ta", "te")


def get_locale(key: str) -> Locale:
    try:
        return LOCALES[key]
    except KeyError:
        raise ValueError(
            f"unknown locale {key!r}; expected one of {sorted(LOCALES)}"
        ) from None


def char_budget(base_chars: int, locale_key: str) -> int:
    """Scale an English character budget for a target locale.

    Copy authored to an English budget overflows in Tamil roughly a third of
    the time; scaling the budget at authoring time is much cheaper than
    shrinking type at render time.
    """
    return max(1, round(base_chars / get_locale(locale_key).metrics.length_factor))


@dataclass
class CopyBlock:
    """One slot of copy in one locale."""

    headline: str
    subhead: str = ""
    cta: str = ""
    #: Shorter alternates, so auto-fit becomes a selection problem rather than
    #: shrink-until-illegible.
    headline_alternates: list[str] = field(default_factory=list)
