# Subject preservation

When a user uploads a photo of something they actually sell — a building, a
product, a saree — the model must **restage** it, not **redesign** it.

This is not a quality preference. MAI's edit mode explicitly supports *text
updates* and attribute changes, so left to itself it will re-letter a label,
add a balcony, or turn a four-storey building into a five-storey one.

For a property listing that is not a bad render. It is an advertisement for a
building that does not exist — and the person running the tool cannot see the
problem, because the output looks excellent.

---

## Two mechanisms, because a prompt is advisory

### 1. A clause that names the actual failures

Generic wording does not work. A model told *"keep the building the same"* will
still change the floor count, because it has no reason to think that is what
"the same" means.

So the clause enumerates, per subject kind, the features that actually get
altered:

| Kind | Pinned |
|---|---|
| **architecture** | floor count · every window, door and balcony · roofline · façade material · structural layout and footprint · railings, columns, parapets · signage and unit numbers |
| **product** | shape and proportions · material and finish · closure or cap · **label artwork and printed text** · colour |
| **jewellery** | design and setting · **number and placement of every stone** · metal colour · chain and clasp · scale on the wearer |
| **apparel** | cut and drape · fabric and weave · print, embroidery and border placement · how it is worn |
| **food** | visible ingredients · portion and arrangement · colour and doneness · serving vessel |
| **vehicle** | body shape and panel lines · wheel count and design · badging and number plate |
| **person** | face and identity · **skin tone** · hair and cultural markers · proportions |

Architecture gets an extra sentence, because it is the one that causes real
harm:

> *This is a real, existing property and must remain recognisable as the same
> building. Do not invent, beautify or modernise any architectural feature.*

Person gets one too — *do not beautify, slim, lighten or otherwise retouch* —
because silent retouching is a representation problem, not a styling choice.

### 2. A measurement afterwards

The clause asks. This checks whether the model listened.

---

## Why it cannot be whole-frame similarity

The first implementation compared the two frames directly. It was **exactly
backwards**, and the test caught it:

```
case                       verdict
identical                  PASS
sky recoloured (legit)     REJECT   ← wrong
ONE EXTRA FLOOR            PASS     ← wrong
```

Restaging is *supposed* to change the background. Swapping a plain sky for a
studio backdrop moves most of the pixels in the frame while leaving the subject
untouched. Adding a storey moves very few pixels and destroys the listing's
accuracy.

Compared across the whole frame, **the harmless edit scores worse than the
dangerous one.**

So the subject is located first — it is the structured part of the image; sky,
seamless backdrops and plain walls are flat — and three things are measured
about it.

| Signal | Catches |
|---|---|
| **Similarity inside the subject** | redrawing, relabelling, material and colour changes |
| **Change in the subject's extent** | a building gaining a storey, which grows upward into what used to be sky |
| **Change in detail density** | windows, balconies or stones added without the outline moving |

Results on synthetic buildings:

| Case | Similarity | Extent | Detail | Verdict |
|---|---|---|---|---|
| identical | 1.000 | 1.00 | +0.0% | ✅ pass |
| sky recoloured *(legit)* | 1.000 | 1.00 | +0.2% | ✅ pass |
| bokeh background *(legit)* | 1.000 | 1.00 | +0.1% | ✅ pass |
| **one extra floor** | 1.000 | 0.78 | +0.0% | ❌ reject |
| **extra window per floor** | 1.000 | 0.77 | +0.0% | ❌ reject |
| **façade material changed** | −0.092 | 0.74 | −87.2% | ❌ reject |

Note the extra floor scores **1.000 on similarity** — the original building is
untouched. Only the extent signal catches it. That is why one measure is not
enough.

---

## Locating the subject

Two details here are load-bearing, and both were bugs first.

**The threshold comes from the original only.** Deriving it separately per
image means recolouring the sky moves that frame's statistics, which moves its
threshold, which silently reclassifies the building — and the subject appears
to have moved when nothing about it changed.

**It is `mean + 2σ`, not a percentile.** In a typical photograph the subject is
a *minority* of the frame; sky, backdrop and ground are flat. Any percentile
below about 0.9 lands inside the flat majority and the mask balloons to include
sensor noise. Measured on the test image: the 75th percentile was **4.5** —
well inside the flat region — while `mean + 2σ` was **31.7**, which is where
the real window edges are.

The outermost ring of windows is excluded, because `FIND_EDGES` leaves a bright
rim at the frame border that would otherwise read as structure all the way
round.

Pure Pillow, no numpy, on a 256×256 downsample. Milliseconds.

---

## Thresholds

```
architecture  0.92     jewellery  0.92     person  0.90
product       0.88     vehicle    0.88
apparel       0.85     generic    0.85     food    0.82
```

Architecture and jewellery are strictest. Misstating a property or adding a
stone to a necklace is a legal exposure; a slightly different plate of food is
not.

Extent overlap must stay above **0.90** and detail density within **±15%**,
regardless of kind.

---

## Using it

`subject_kind` is inferred from the brief when not set — *"3 BHK apartment in
Pune"* → `architecture`, *"gold necklace with diamond pendant"* → `jewellery`.

**The inference is a UI convenience, never the only safeguard.** An explicit
`subject_kind` always wins, and preservation defaults **on**: someone uploading
a photo of a real building is asking for it to be restaged, not reimagined.

```json
{
  "brief": "our 3 BHK apartment project in Pune",
  "reference_id": "9f8e7d6c5b4a",
  "reference_mode": "edit",
  "edit_instruction": "restage on a bright morning sky",
  "rights_confirmed": true,
  "subject_kind": "architecture",
  "preserve_subject": true
}
```

The job result carries the verdict:

```json
"fidelity": {
  "subject_kind": "architecture",
  "passed": false,
  "similarity": 0.9981,
  "extent_overlap": 0.78,
  "detail_change": 0.0021,
  "reason": "the subject's outline moved or resized (overlap 0.78)"
}
```

**A failed check marks every variant for review.** It does not silently discard
the result — the marketer may look and decide the change is acceptable — but a
drifted subject can never present itself as ready to publish.

---

## What this does not do

- **It cannot tell you *what* changed**, only that something did. "The outline
  moved" does not say "a floor was added". A human still looks.
- **It has not been tested against real MAI output**, only synthetic buildings.
  The thresholds are reasoned, not yet calibrated. Expect to tune them once
  [#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29)
  runs.
- **It runs only on the edit path.** Text-to-image has no original to preserve.
- **A deliberately dramatic restaging may trip it** — moving a building from
  daylight to night changes its façade tone. That surfaces as a review flag
  rather than a rejection, which is the right failure direction.
