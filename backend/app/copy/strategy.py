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

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

from app.copy.blocklist import sanitise
from app.copy.fidelity import SubjectKind, preservation_clause

logger = logging.getLogger(__name__)

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
    #: What the user actually typed, kept alongside the model's elaboration.
    #:
    #: It is the last rung of the refusal ladder. A compiled brief is two
    #: hundred words of the model's own vocabulary, and any one of them can be
    #: on a provider blocklist -- "goodyear welt" is a shoemaking term and a
    #: tyre trademark at the same time. The user's three words are a much
    #: smaller target, and a plain prompt built from them still produces the
    #: campaign they asked for.
    source_text: str = ""
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
#: lettering, so the instruction has to enumerate the forms it takes: "no
#: text" alone reliably still produces signage.
#:
#: Two words are deliberately absent. The clause used to end "no signage, no
#: watermark, no logo and no typography", and that phrasing was getting every
#: request refused on content-policy grounds -- for any product, which is what
#: made it look random. Removing a watermark from an image is a blocked
#: category at most providers, and a term-matching filter cannot tell a
#: request to *remove* one from a request never to *draw* one. "Logo" reads
#: the same way.
#:
#: Nothing is lost by saying it positively. The image is composited: the real
#: logo is placed from the user's own file afterwards, so the model was never
#: being asked about logos in the first place.
NO_TEXT_CLAUSE = (
    "This image is completely free of text: no lettering, no words, no "
    "numbers, no letterforms, no captions, no signage and no typographic "
    "elements anywhere in the frame."
)

#: What used to be appended to every prompt:
#:
#:     "The frame contains no any map, no any national flag, no any
#:      recognisable public figure, no any third-party brand or trademark."
#:
#: Two things wrong with it, beyond the grammar.
#:
#: It was the thing getting our prompts refused. A term blocklist matches
#: words and has no concept of negation, so a sentence naming four flagged
#: categories at once reads as a prompt *about* flags and trademarks. It went
#: on every request, so any product could trip it, and the message came back
#: as a bare 400 about pixel budgets.
#:
#: And it was weak prompting regardless. Diffusion models attend badly to
#: negations -- naming a thing raises its salience whatever word precedes it,
#: which is the same reason "don't think of an elephant" fails. Constraining
#: the frame positively excludes everything unnamed, including the four
#: categories, without naming any of them.
#:
#: The categories are still barred; that enforcement belongs on the text side,
#: where the brief compiler refuses to write them, and on output screening --
#: not in a string appended to the picture request.
EXCLUSIVITY_CLAUSE = (
    "Photograph only the subject and setting described above. Nothing else "
    "appears in the frame."
)

#: Terms a provider blocklist refuses, and what to say instead.
#:
#: "men's" is on Azure's BingBlockList. Not a phrase near it, not a
#: combination -- the bare word. Probed against the live filter: "casual
#: shoes on a wooden table" is accepted and "mens shoes on a wooden table" is
#: refused, same sentence otherwise. Presumably the list targets adult-search
#: phrasing, and a retail possessive collides with it.
#:
#: It is worth naming how bad that failure was. Every brief for menswear --
#: shoes, shirts, watches, a large slice of Indian D2C -- was refused, the
#: refusal arrived as an unexplained 400, and nothing in the product said
#: which of the two hundred words was at fault.
#:
#: Dropping the possessive costs almost nothing: the rest of the brief
#: ("full-grain tan leather, stitched welt, rounded toe") already describes
#: the shoe precisely. Keeping the campaign is worth more than keeping a word
#: the filter will not accept.
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


#: How far a prompt has been stripped back after a content-policy refusal.
#: Each step removes only negative phrasing -- the subject, the scene and the
#: reserved region are never touched, because losing those changes what the
#: campaign *is* rather than how it is worded.
FULL, NO_AUTHORED_CLAUSES, TERSE, BARE, PLAIN = 0, 1, 2, 3, 4


def render_prompt(
    brief: CreativeBrief,
    *,
    crop_safe: bool = False,
    reduce: int = FULL,
) -> str:
    """Turn a CreativeBrief into an English MAI prompt.

    MAI-Image-2.6 declares English as its only supported language, so the
    prompt is always English regardless of the campaign's target locales.

    ``crop_safe`` is for economy mode, where one tall master is cropped down to
    every other format. The subject then has to survive having its top and
    bottom removed, so the prompt asks for it to be centred with margin --
    otherwise the square crop decapitates it.

    ``reduce`` strips negative phrasing back after a content-policy refusal.
    A prompt blocklist matches terms and cannot read negation, so a brief that
    carefully says "no third-party sportswear logo" is refused for naming the
    thing it rules out. Dropping those clauses is safe -- nothing asked for
    them in the first place.

    What the ladder never touches is the subject, the scene, and the reserved
    region. Those decide what the campaign *is* and where the copy will fit;
    trading them away to get past a filter would return a different creative
    than the one briefed, which is worse than returning nothing.
    """
    space = brief.negative_space

    if reduce >= PLAIN and brief.source_text.strip():
        # Everything the model wrote has been refused. Fall back to the
        # user's own words plus the one clause the overlay depends on:
        # short, plain, and built from text a person typed rather than from
        # a vocabulary a model reached for.
        plain = sanitise(brief.source_text.strip().rstrip("."))[0]
        return sanitise(
            f"commercial product photography. {plain}. "
            f"Leave the {_REGION_PHRASE[space.region]} {space.coverage_pct}% of "
            f"the frame as clean, uncluttered {_FILL_PHRASE[space.fill]} with no "
            f"objects and no detail -- an area reserved for later graphic "
            f"overlay. This image is completely free of text."
        )[0]

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

    if reduce >= BARE:
        # Last rung. Describe the picture and the reserved region, and stop.
        # Every remaining sentence is a negation, and negations are what a
        # term blocklist reads as requests. A campaign generated from this is
        # still the briefed campaign -- it has simply lost the belt-and-braces
        # wording -- and returning it beats returning an error to someone
        # standing in front of an audience.
        return sanitise(" ".join(parts))[0]

    parts.append(f"Lighting: {brief.lighting}.")
    parts.append(f"Colour: {', '.join(brief.palette_names)}.")
    parts.append(f"Mood: {', '.join(brief.mood)}.")

    if brief.product_facts:
        parts.append(
            f"The product is exactly: {'; '.join(brief.product_facts)}. "
            f"Do not alter its shape, material, closure or proportions."
        )

    # Even at TERSE the no-text instruction survives in some form: a base with
    # lettering baked in defeats the overlay, so it is the one negative the
    # pipeline genuinely depends on.
    parts.append(
        "This image contains no text of any kind."
        if reduce >= TERSE
        else NO_TEXT_CLAUSE
    )
    parts.append(EXCLUSIVITY_CLAUSE)

    authored = () if reduce >= NO_AUTHORED_CLAUSES else tuple(brief.must_not_depict)
    if authored:
        # Rendered as written. The old code prefixed every entry with "no ",
        # which produced "no any map" when the entry already began with a
        # determiner -- read aloud, that is not a sentence, and a prompt the
        # author would not have written is one nobody is checking.
        parts.append("Exclude: " + "; ".join(authored) + ".")

    return sanitise(" ".join(parts))[0]


#: What to do with an uploaded photograph. Three intentions, because the two
#: we had were doing three jobs badly.
#:
#: "exact" and "enhance" both keep the user's actual subject and differ only
#: in what they are allowed to touch around and on it -- but collapsing them
#: into one control meant a seller wanting a cleaner version of their own
#: photo had to pick between an edit that only restaged the background and an
#: inspiration pass that invented a different product.
ReferenceMode = Literal["exact", "enhance", "inspiration"]

#: Restage what is around the subject; the subject itself does not move.
_EXACT_INTENT = (
    "Keep the photographed subject exactly as it is -- identical shape, "
    "proportions, materials, colours, markings and detail. Change only the "
    "surroundings: background, setting, lighting and atmosphere."
)

#: Improve the photograph itself. Still the same object -- a seller asking
#: for a better picture of their product is not asking for a different
#: product -- but the pixels *on* the subject may legitimately change, which
#: is why this mode measures against a softer floor.
_ENHANCE_INTENT = (
    "Improve this photograph while keeping the same subject: even, flattering "
    "light, accurate white balance, clean and uncluttered background, sharp "
    "focus and natural colour. Remove dust, glare, distracting clutter and "
    "background objects. Do not restyle, redesign or replace the subject, and "
    "do not slim, reshape or otherwise alter it."
)

INTENTS: dict[str, str] = {"exact": _EXACT_INTENT, "enhance": _ENHANCE_INTENT}

#: How much the fidelity floor relaxes when enhancement is the point.
#:
#: Enhancing legitimately moves pixels on the subject -- that is the request --
#: so holding it to the restaging floor would fail every successful enhance.
#: Small, though: the subject's outline and detail density are still checked
#: at full strength, and those are what catch a building gaining a storey.
ENHANCE_FLOOR_RELIEF = 0.06


def render_edit_prompt(
    instruction: str,
    *,
    preserve_subject: bool = True,
    subject_kind: SubjectKind = SubjectKind.GENERIC,
    mode: ReferenceMode = "exact",
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

    ``mode`` chooses between restaging the surroundings and improving the
    photograph. Both keep the subject; the clause that says so is identical,
    because "enhance" is exactly where a model is most tempted to quietly
    upgrade what it was given.
    """
    parts = []
    if intent := INTENTS.get(mode):
        parts.append(intent)
    if instruction.strip():
        parts.append(instruction.rstrip(". ") + ".")
    if preserve_subject:
        parts.append(preservation_clause(subject_kind))
    parts.append("Do not add any new text, logo or watermark anywhere in the image.")
    return " ".join(parts)
