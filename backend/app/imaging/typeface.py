"""Which typeface a campaign is set in.

Every creative came out in the same face, which is the typographic
equivalent of every creative coming out in the same layout: correct, and
instantly recognisable as machine-made. A jewellery advertisement and a
biryani advertisement do not use the same lettering, and a marketer looking
at a wall of output notices the sameness long before they notice anything
else.

So the face is chosen from what is being sold. Not at random -- randomness
produces a Didone on a street-food post and reads as a mistake -- but from
the conventions each category already follows.

**Latin only.** Indic locales keep their Noto faces regardless. Substituting
a display family for Devanagari or Tamil is how shaping breaks: the fallback
either lacks the conjunct glyphs entirely or has untested metrics, and the
result renders convincingly and reads as illiterate to the audience. The
whole architecture exists to avoid exactly that, so this never touches them.

Families are restricted to faces that ship with the platform. A webfont that
fails to load does not fail loudly -- it silently substitutes, and every
fitted size computed against it is then wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.copy.fidelity import SubjectKind


@dataclass(frozen=True)
class Typeface:
    """A Latin display face, with the weight it reads best at."""

    name: str
    stack: tuple[str, ...]
    #: Headline weight. A face with generous stroke contrast looks thin at
    #: 600 and coarse at 800, so this travels with the family.
    weight: int = 700
    #: Small caps suit a mark or a short label and ruin a sentence, so this
    #: is per-face and only ever applied to the call to action.
    cta_small_caps: bool = False


#: Editorial serif: high contrast, long ascenders. The convention for
#: property, jewellery and anything sold on provenance.
EDITORIAL = Typeface(
    "editorial",
    ("Constantia", "Georgia", "Cambria", "Times New Roman", "serif"),
    weight=700,
    cta_small_caps=True,
)

#: Humanist sans: open apertures, warm. Food and hospitality, where a
#: geometric face reads clinical.
WARM = Typeface(
    "warm", ("Corbel", "Candara", "Segoe UI", "Trebuchet MS", "sans-serif"), weight=700
)

#: Neutral grotesque. Product and general retail: gets out of the way of the
#: photograph, which is usually the point.
NEUTRAL = Typeface(
    "neutral", ("Inter", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"), weight=700
)

#: Old-style serif with modest contrast. Apparel and textiles, where the
#: editorial face competes with the fabric.
SOFT_SERIF = Typeface(
    "soft serif",
    ("Palatino Linotype", "Book Antiqua", "Georgia", "serif"),
    weight=600,
)

#: Tighter, squarer sans for vehicles and anything mechanical.
TECHNICAL = Typeface(
    "technical",
    ("Segoe UI Semibold", "Franklin Gothic Medium", "Segoe UI", "Arial", "sans-serif"),
    weight=700,
)

_BY_KIND: dict[SubjectKind, Typeface] = {
    SubjectKind.ARCHITECTURE: EDITORIAL,
    SubjectKind.JEWELLERY: EDITORIAL,
    SubjectKind.APPAREL: SOFT_SERIF,
    SubjectKind.FOOD: WARM,
    SubjectKind.VEHICLE: TECHNICAL,
    SubjectKind.PERSON: SOFT_SERIF,
    SubjectKind.PRODUCT: NEUTRAL,
    SubjectKind.GENERIC: NEUTRAL,
}

#: Mood words that override the category. A "playful" jewellery campaign is
#: not an editorial one, and the brief knows that before the category does.
_BY_MOOD: tuple[tuple[frozenset[str], Typeface], ...] = (
    (frozenset({"playful", "fun", "youthful", "vibrant", "bold"}), WARM),
    (frozenset({"luxury", "luxurious", "elegant", "refined", "premium"}), EDITORIAL),
    (frozenset({"technical", "precise", "engineered", "modern"}), TECHNICAL),
)


def typeface_for(kind: SubjectKind, mood: tuple[str, ...] = ()) -> Typeface:
    """Pick the face for this campaign.

    Mood wins over category when it says something the category does not:
    "playful" describes the advertisement, while "jewellery" only describes
    the object, and the advertisement is what is being set.
    """
    words = {word.strip().lower() for word in mood}
    for triggers, face in _BY_MOOD:
        if words & triggers:
            return face
    return _BY_KIND.get(kind, NEUTRAL)


_BY_NAME = {f.name: f for f in (EDITORIAL, WARM, NEUTRAL, SOFT_SERIF, TECHNICAL)}


def face_by_name(name: str) -> Typeface:
    """Look a face up by the name stored on a finished campaign.

    A review re-render has no brief to choose from, and silently falling back
    to the default would reset the typography of the creative being approved.
    """
    return _BY_NAME.get(name, NEUTRAL)
