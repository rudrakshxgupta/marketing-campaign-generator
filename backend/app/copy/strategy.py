"""The language-neutral creative strategy, and the brief that drives the image.

Copy is written from a strategy, not translated from English. Translating
finished English marketing copy produces text that is accurate and dead -- and
worse, it carries the wrong cultural referent: a Diwali line translated into
Bengali is still a Diwali line, when the campaign that audience responds to is
Durga Puja. Authoring each language from a shared strategy lets the referent
change, not just the words.

The CreativeBrief is the other half: a structured description of the *picture*,
which a pure function turns into an English MAI prompt. Keeping prompt
construction deterministic means the no-text and negative-space clauses can
never be dropped by a model having a creative moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.copy.fidelity import SubjectKind, preservation_clause

Register = Literal["formal", "neutral", "conversational"]
InputMode = Literal["text", "style_transfer", "direct_edit"]


@dataclass
class BrandKit:
    """The minimum viable brand kit.

    Deliberately small: onboarding that takes more than about ninety seconds is
    where users drop out before they have seen a single result.
    """

    name: str
    primary_hex: str = "#D4A03C"
    secondary_hex: str = "#1A0E08"
    accent_hex: str = "#D4A03C"
    tone: tuple[str, ...] = ("warm", "premium", "trustworthy")
    logo_path: str | None = None
    #: Category disclaimers, licence numbers, mandatory T&C line.
    mandatory_line: str = ""
    #: Visual prohibitions specific to this brand.
    do_not_show: tuple[str, ...] = ()


@dataclass
class CreativeStrategy:
    """What the campaign is trying to do, independent of any language."""

    proposition: str
    benefit: str
    objective: Literal["awareness", "traffic", "conversion"] = "awareness"
    register: Register = "neutral"
    urgency: str = ""
    cta_intent: str = "shop"
    #: Facts the copy may state. Nothing outside this list may be invented --
    #: prices, dates and offers are never paraphrased by a model.
    facts: tuple[str, ...] = ()
    occasion: str = ""
    #: Per-locale occasion overrides, because the right festival differs by
    #: region even within one campaign.
    occasion_by_locale: dict[str, str] = field(default_factory=dict)

    def occasion_for(self, locale_key: str) -> str:
        return self.occasion_by_locale.get(locale_key, self.occasion)


@dataclass
class NegativeSpace:
    """The region reserved for the text overlay and the logo."""

    region: Literal[
        "top", "bottom", "left", "right",
        "top_left", "top_right", "bottom_left", "bottom_right",
    ] = "bottom_left"
    coverage_pct: int = 32
    fill: Literal[
        "solid_color", "soft_gradient", "blurred_background", "clean_surface"
    ] = "soft_gradient"


@dataclass
class CreativeBrief:
    """Structured description of the image. Never sent to MAI directly."""

    subject: str
    scene: str = ""
    product_facts: tuple[str, ...] = ()
    framing: str = "rule of thirds"
    camera: str = "50mm, eye level"
    lighting: str = "soft diffused key from the left, gentle shadows"
    palette_names: tuple[str, ...] = ("warm gold", "deep brown", "soft cream")
    mood: tuple[str, ...] = ("premium", "warm")
    visual_style: str = "commercial product photography"
    negative_space: NegativeSpace = field(default_factory=NegativeSpace)
    must_not_depict: tuple[str, ...] = ()
    source_mode: InputMode = "text"
    #: What is being photographed. Drives the preservation clause on the edit
    #: path and the strictness of the fidelity check afterwards.
    subject_kind: SubjectKind = SubjectKind.GENERIC
    #: Whether the subject must survive the edit untouched. On by default:
    #: someone uploading a photo of a real building or a real product is
    #: asking for it to be restaged, not redesigned.
    preserve_subject: bool = True


# --------------------------------------------------------------------------
# Prompt rendering -- a pure function, deliberately not an LLM
# --------------------------------------------------------------------------

#: Diffusion models trained on advertising imagery have a strong prior to add
#: lettering. The prohibition has to enumerate the forms it takes, because
#: "no text" alone reliably still produces signage and watermarks.
NO_TEXT_CLAUSE = (
    "There is no text, no lettering, no words, no numbers, no letterforms, "
    "no signage, no watermark, no logo and no typography anywhere in this image."
)

#: Things the model must never be asked to draw, in any campaign. A map of
#: India rendered by a diffusion model gets the boundaries wrong, which is a
#: legal problem rather than a quality one; the flag is governed by the Flag
#: Code; likenesses of real people are prohibited outright.
GLOBAL_PROHIBITIONS = (
    "any map",
    "any national flag",
    "any recognisable public figure",
    "any third-party brand or trademark",
)

_REGION_PHRASE = {
    "top": "top",
    "bottom": "bottom",
    "left": "left",
    "right": "right",
    "top_left": "upper-left",
    "top_right": "upper-right",
    "bottom_left": "lower-left",
    "bottom_right": "lower-right",
}

_FILL_PHRASE = {
    "solid_color": "flat, evenly lit colour",
    "soft_gradient": "smooth soft gradient",
    "blurred_background": "softly blurred background",
    "clean_surface": "clean, evenly lit surface",
}


def render_prompt(brief: CreativeBrief, *, crop_safe: bool = False) -> str:
    """Turn a CreativeBrief into an English MAI prompt.

    MAI-Image-2.6 declares English as its only supported language, so the
    prompt is always English regardless of the campaign's target locales.

    ``crop_safe`` is for economy mode, where one tall master is cropped down to
    every other format. The subject then has to survive having its top and
    bottom removed, so the prompt asks for it to be centred with margin --
    otherwise the square crop decapitates it.
    """
    space = brief.negative_space
    parts = [
        f"{brief.visual_style}. {brief.subject}.",
    ]
    if brief.scene:
        parts.append(f"{brief.scene}.")

    parts.append(f"Composition: {brief.framing}, {brief.camera}.")

    if crop_safe:
        parts.append(
            "Keep the subject centred within the middle half of the frame "
            "vertically, fully inside the frame with generous margin above and "
            "below, so the image can be cropped to a shorter shape without "
            "cutting the subject."
        )

    # Lead with the reserved area. The logo and every language's copy land
    # here, composited afterwards, so the model's only job regarding them is to
    # leave the room.
    parts.append(
        f"Leave the {_REGION_PHRASE[space.region]} {space.coverage_pct}% of the "
        f"frame as clean, uncluttered {_FILL_PHRASE[space.fill]} with no objects, "
        f"no detail and no texture -- an area reserved for later graphic overlay. "
        f"The subject must not extend into it."
    )

    parts.append(f"Lighting: {brief.lighting}.")
    parts.append(f"Colour: {', '.join(brief.palette_names)}.")
    parts.append(f"Mood: {', '.join(brief.mood)}.")

    if brief.product_facts:
        parts.append(
            f"The product is exactly: {'; '.join(brief.product_facts)}. "
            f"Do not alter its shape, material, closure or proportions."
        )

    parts.append(NO_TEXT_CLAUSE)

    prohibited = tuple(brief.must_not_depict) + GLOBAL_PROHIBITIONS
    parts.append(
        "The frame contains " + ", ".join(f"no {item}" for item in prohibited) + "."
    )

    return " ".join(parts)


def render_edit_prompt(
    instruction: str,
    *,
    preserve_subject: bool = True,
    subject_kind: SubjectKind = SubjectKind.GENERIC,
) -> str:
    """Prompt for the image-to-image path.

    MAI's edit mode explicitly supports *text updates* and attribute changes,
    which means left to itself it will happily re-letter packaging it is shown,
    or "improve" a building into one the buyer will never find.

    The preservation clause is per subject kind because generic wording is too
    weak. A model told only "keep the building the same" will still quietly
    change the floor count -- it has no reason to think that is what "the same"
    means. :mod:`app.copy.fidelity` enumerates the failures that actually
    happen for each kind of subject.
    """
    parts = [instruction.rstrip(". ") + "."]
    if preserve_subject:
        parts.append(preservation_clause(subject_kind))
    parts.append("Do not add any new text, logo or watermark anywhere in the image.")
    return " ".join(parts)
