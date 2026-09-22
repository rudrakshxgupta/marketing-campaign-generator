"""Keep the real thing real.

When a user uploads a photo of something they actually sell -- a building, a
product, a saree -- the model must restage it, not redesign it.

This is not a quality preference. MAI's edit mode explicitly supports *text
updates* and attribute changes, which means left to itself it will cheerfully
re-letter a label, add a balcony, or change a four-storey building into a
five-storey one. For a property listing or a product ad that is not a bad
render; it is advertising something that does not exist.

Two mechanisms, because a prompt is advisory:

1. A preservation clause naming, specifically, what must not change. Generic
   "keep it the same" is too weak -- the clause has to enumerate the features
   the model actually gets wrong for that kind of subject.
2. A measured check afterwards (:mod:`app.imaging.fidelity`) that compares the
   result against the input and refuses to ship a drifted subject.
"""

from __future__ import annotations

from enum import Enum


class SubjectKind(str, Enum):
    """What is being photographed, and therefore what must be preserved."""

    ARCHITECTURE = "architecture"
    PRODUCT = "product"
    FOOD = "food"
    APPAREL = "apparel"
    JEWELLERY = "jewellery"
    VEHICLE = "vehicle"
    PERSON = "person"
    GENERIC = "generic"


#: What the model must not alter, per subject kind.
#:
#: Each list enumerates the failures that actually happen for that subject --
#: a model told only "keep the building the same" will still quietly change the
#: floor count, because it has no reason to think that is what "the same" means.
_PRESERVE: dict[SubjectKind, tuple[str, ...]] = {
    SubjectKind.ARCHITECTURE: (
        "the number of floors and storeys",
        "the number, size, shape and position of every window, door and balcony",
        "the roofline and roof shape",
        "the facade material, texture and colour",
        "the structural layout, proportions and footprint",
        "railings, columns, staircases and parapets",
        "signage, unit numbers and any lettering on the building",
        "the surrounding built context and boundary walls",
    ),
    SubjectKind.PRODUCT: (
        "the shape, silhouette and proportions",
        "the material, finish and surface texture",
        "the closure, cap, lid or seal",
        "the label, artwork, printed text and every marking on the packaging",
        "the colour of the product and its packaging",
    ),
    SubjectKind.FOOD: (
        "the ingredients visible in the dish",
        "the portion size and arrangement",
        "the colour, texture and doneness",
        "the serving vessel",
    ),
    SubjectKind.APPAREL: (
        "the garment's cut, drape and silhouette",
        "the fabric, weave and texture",
        "the print, embroidery, border and motif placement",
        "the colour and colour placement",
        "how the garment is draped or worn",
    ),
    SubjectKind.JEWELLERY: (
        "the design, setting and arrangement of every stone",
        "the number and placement of stones and beads",
        "the metal colour and finish",
        "the chain, clasp and link structure",
        "the proportions and scale relative to the wearer",
    ),
    SubjectKind.VEHICLE: (
        "the body shape, panel lines and proportions",
        "the wheel design and count",
        "the badging, model lettering and number plate",
        "the colour and trim",
    ),
    SubjectKind.PERSON: (
        "the person's face, features and identity",
        "skin tone",
        "hair, and any religious or cultural markers being worn",
        "body proportions",
    ),
    SubjectKind.GENERIC: (
        "the subject's shape, proportions and identifying features",
        "any text, marking or lettering on the subject",
        "colour and material",
    ),
}

#: How closely the result must match the input for each kind, as a structural
#: similarity floor. Architecture is strictest: a property advertisement that
#: misstates a building is a legal exposure, not a design flaw.
FIDELITY_FLOOR: dict[SubjectKind, float] = {
    SubjectKind.ARCHITECTURE: 0.92,
    SubjectKind.JEWELLERY: 0.92,
    SubjectKind.PRODUCT: 0.88,
    SubjectKind.VEHICLE: 0.88,
    SubjectKind.APPAREL: 0.85,
    SubjectKind.PERSON: 0.90,
    SubjectKind.FOOD: 0.82,
    SubjectKind.GENERIC: 0.85,
}

_SUBJECT_NOUN: dict[SubjectKind, str] = {
    SubjectKind.ARCHITECTURE: "building and property",
    SubjectKind.PRODUCT: "product",
    SubjectKind.FOOD: "dish",
    SubjectKind.APPAREL: "garment",
    SubjectKind.JEWELLERY: "jewellery piece",
    SubjectKind.VEHICLE: "vehicle",
    SubjectKind.PERSON: "person",
    SubjectKind.GENERIC: "subject",
}


def preservation_clause(kind: SubjectKind) -> str:
    """The prompt text that pins the subject.

    Deliberately long and specific. The enumerated list is the part that works;
    "keep it the same" on its own does not.
    """
    noun = _SUBJECT_NOUN[kind]
    items = _PRESERVE[kind]

    clause = (
        f"Keep the {noun} exactly as photographed, completely unchanged. "
        f"Preserve identically: {'; '.join(items)}. "
        f"Do not add, remove, resize, reposition or redesign any part of the "
        f"{noun}. Do not redraw, re-letter or re-render any text, label or "
        f"marking on it. Change only the surrounding scene, lighting and "
        f"background."
    )

    if kind is SubjectKind.ARCHITECTURE:
        # The single most common and most damaging failure: a model that
        # "improves" a building into one the buyer will never find.
        clause += (
            " This is a real, existing property and must remain recognisable as "
            "the same building. Do not invent, beautify or modernise any "
            "architectural feature."
        )
    elif kind is SubjectKind.PERSON:
        clause += (
            " Do not alter, beautify, slim, lighten or otherwise retouch the "
            "person."
        )

    return clause


def detect_kind(text: str) -> SubjectKind:
    """Best guess at the subject from the user's own words.

    A convenience for pre-selecting the control in the UI. It must never be the
    only thing standing between a model and a misrepresented building, so the
    caller always keeps an explicit override.
    """
    lowered = text.lower()
    signals: list[tuple[SubjectKind, tuple[str, ...]]] = [
        (SubjectKind.ARCHITECTURE, (
            "apartment", "flat", "villa", "bungalow", "property", "building",
            "real estate", "realty", "house", "home", "plot", "tower",
            "residence", "facade", "façade", "interior", "bhk", "penthouse",
            "showroom", "office space", "storefront", "shop front",
        )),
        (SubjectKind.JEWELLERY, (
            "jewellery", "jewelry", "necklace", "earring", "bangle", "ring",
            "pendant", "gold", "diamond", "mangalsutra", "jhumka",
        )),
        (SubjectKind.APPAREL, (
            "saree", "sari", "kurta", "lehenga", "dress", "shirt", "apparel",
            "clothing", "fabric", "garment", "dupatta", "sherwani",
        )),
        (SubjectKind.VEHICLE, (
            "car", "bike", "scooter", "vehicle", "motorcycle", "truck", "auto",
        )),
        (SubjectKind.FOOD, (
            "biryani", "food", "dish", "snack", "sweet", "mithai", "cake",
            "meal", "thali", "restaurant", "bakery",
        )),
        (SubjectKind.PERSON, ("model", "portrait", "person", "founder", "team")),
        (SubjectKind.PRODUCT, (
            "bottle", "jar", "packet", "pack", "box", "tube", "product",
            "packaging", "container", "sachet",
        )),
    ]
    for kind, words in signals:
        if any(word in lowered for word in words):
            return kind
    return SubjectKind.GENERIC
