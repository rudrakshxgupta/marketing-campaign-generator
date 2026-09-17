# Architecture

## The central decision

> **Generate one text-free image. Composite the logo and every language's text
> deterministically on top.**

Three independently verified facts ([constraints.md](constraints.md)) converge
on this:

1. **MAI-Image-2.6 declares `Languages: en`.** Asking it to draw Devanagari or
   Tamil is outside its supported envelope.
2. **The model allows 2–12 RPM.** Six languages across four formats as separate
   calls is not physically possible — at tier 1 that is twelve minutes of the
   entire tenant's quota for one campaign.
3. **Azure OCR cannot extract 8 of the Indic scripts.** For most of our
   languages we could not verify model-drawn text even if we wanted it.

Any one of these would justify the design. Together they make it the only
sensible one.

### What it buys

| | Model draws the text | Composited overlay |
|---|---|---|
| 7 languages × 2 formats | 14 model calls | **2 model calls** |
| Indic shaping | garbled, plausibly | correct, by construction |
| Brand logo | hallucinated letterforms | byte-exact |
| Brand colour | approximate | exact |
| Text QA | inference (and impossible for 5 of 7 languages) | **arithmetic** |
| Fixing a typo | full regeneration | re-render, ~200ms, no quota |

That last row matters more than it looks. A marketer changes copy constantly;
in a model-drawn design every edit costs a rate-limited call.

**Measured on the demo campaign: 2 image calls → 14 deliverables.**

---

## Pipeline

```
 brief / reference image
        │
   [1]  ├─ safety in            Content Safety + Prompt Shields      ← pending #14
        │
   [2]  ├─ Brief Compiler       ──► CreativeBrief (structured)
        │
   [3]  ├─ Prompt Renderer      ──► English prompt   PURE FUNCTION, no LLM
        │
   [4]  ├─ MAI-Image-2.6        ──► TEXT-FREE base PNG
        │                            ▲
        │                            └── the ONLY rate-limited hop
   [5]  ├─ crop + upscale       Lanczos, to exact Instagram size
        │
   [6]  ├─ Copy Writer          ──► CopyPack, all locales, one call
        │
        ├──────────────── per locale, no image quota ─────────────────┐
   [7]  │  Chromium             ──► transparent text overlay          │
   [8]  │  Pillow               ──► base + scrim + overlay + LOGO     │
   [9]  │  QA gates             contrast, safe zones, fit, review     │
        └─────────────────────────────────────────────────────────────┘
        │
  [10]  └─ export bundle        images + caption + hashtags + alt text
```

Steps 6–9 repeat per language and cost **no image-model calls**. Step 4 repeats
per *format*, which is why adding a language is free and adding an aspect ratio
is not.

### Ordering choices that matter

**Copy is generated before the image.** It costs no quota, so a failure there
should not burn a generation — and having it ready lets the UI show real
progress while the image is still queued behind the limiter.

**Crop before upscale.** Never scale pixels you are about to discard.

**Upscale before compositing.** Only the photographic base is interpolated;
text and logo are composited at native 1080px and stay crisp.

---

## Why step 3 is not an LLM

The LLM produces structured *semantics* (a `CreativeBrief`). A pure function
turns semantics into prompt text.

This gives diffable prompt versions, reproducible renders from a stored brief,
and — most importantly — a place to enforce clauses that must **never** be
dropped by a model having a creative moment:

**The no-text clause, enumerated.** Diffusion models trained on advertising
imagery have a strong prior to add lettering; "no text" alone reliably still
produces signage and watermarks. The clause names *text, lettering, words,
numbers, letterforms, signage, watermark, logo and typography*.

**Reserved negative space**, so the logo and copy have somewhere to land:

> *Leave the lower-left 32% of the frame as clean, uncluttered smooth soft
> gradient with no objects, no detail and no texture — an area reserved for
> later graphic overlay. The subject must not extend into it.*

**Global prohibitions**, which exist for legal rather than aesthetic reasons:
any map (a diffusion-drawn map of India gets the boundaries wrong), any
national flag, any recognisable public figure, any third-party trademark.

---

## Two 2.6-only flags, both off

| Flag | Default | Why |
|---|---|---|
| `auto_aspect_ratio` | **off** | Lets the model choose the output ratio, which breaks logo placement, safe zones and text boxes simultaneously. Our overlay geometry is locked to a known canvas. |
| `web_grounding` | **off** | Adds a Bing retrieval to the latency budget, makes output non-reproducible over time (a stored brief will not re-render identically), and increases the trademark and public-figure surface the docs warn about. |

Both are worth exposing in an "explore" mode where no overlay is applied. Neither
belongs in a production render.

---

## The logo

A diffusion model produces a *plausible* logo, which is a wrong logo — subtly
wrong letterforms that still ship. Worse, describing a mark in a prompt is
asking the model to reproduce a trademark, one of the risks Microsoft's own
responsible-AI notes name for these models.

So the logo is **never** described to MAI, and never passed through an edit
pass. The model's only job regarding it is to leave the room.

Placement is **verified, not searched**. The brief already declares the
reserved region — that is the contract — so the compositor measures rather than
hunts:

1. Compute the destination box, anchored **inside the safe rect** rather than
   the canvas edge, so compliance is structural.
2. Sample mean luminance of that patch in linear light, per WCAG.
3. Choose the light or dark knockout by contrast.
4. If neither clears 4.5:1, escalate to a scrim. A scrim treats the
   *background*; a drop shadow on the mark is forbidden by most brand
   guidelines.

Never rotates, recolours, or non-uniformly scales.

> 50% grey looks like the hard case but is not — a dark mark reaches ~4.8:1
> against it. The genuinely problematic band is narrow, around luminance 0.19,
> where white reaches ~4.35 and black ~4.34. Covered by a test.

The same logic applies to the **product**: never generated, always composited
from the seller's real photo. A generated saree is not the saree she sells.

---

## Languages as a triple

Language is not one dropdown. It is:

```
(language, script, register)
```

On Indian Instagram a large share of real marketing copy is **Romanised Hindi**
rather than Devanagari — many people speak Hindi fluently but read Devanagari
slowly. So Hinglish is `hi` + `Latn` + `conversational`, a first-class variant
rather than a fallback, and likely the second-highest-volume output.

Both the native-script and Latin variants are generated side by side. Text is
cheap; only images are rate-limited.

### Transcreation, not translation

Copy is authored per language from a shared `CreativeStrategy`, never
translated from the English output.

Translating finished English copy produces text that is accurate and dead — and
worse, it carries the wrong cultural referent. A Diwali line translated into
Bengali is still a Diwali line, when the campaign that audience responds to is
**Durga Puja**. `occasion_by_locale` makes that substitution explicit.

Each locale requests **three headline length variants**, so the typesetter
*selects* a line that fits rather than shrinking type below legibility. Indic
runs 15–40% longer than English for the same meaning; Tamil (factor 1.35) is
routinely the one that overflows.

---

## Reference-image path

Two sub-modes that behave very differently, so they are surfaced explicitly:

**"Use as inspiration"** *(default)* — a vision model extracts a style profile
(lighting, palette, surface, camera, prop density), then the **text** path runs.
Output obeys our layout, aspect ratio and safe zones.

**"Edit this image"** — goes to `/mai/v1/images/edits` directly. Requires an
explicit rights attestation.

Default to inspiration and require an affirmative signal for edit mode, because
the legal exposure differs: edit mode derives the output pixel-for-pixel from
the input, so if the user uploaded a competitor's ad it produces a derivative
work. Edit mode also inherits the input's composition, meaning safe zones are
not guaranteed.

Since the edits endpoint takes no dimensions, geometry is handled on both sides
of the call — cover-crop going in, cover-crop coming out.

> MAI's edit mode explicitly supports *text updates*, which means it will
> happily re-letter packaging it is shown. The "keep my product, restage the
> scene" flow forbids that in as many words.

---

## Jobs, not requests

Generation is never synchronous. The model allows 2–12 RPM and a render takes
tens of seconds, so a request that waited would time out behind almost any
proxy — and Azure Container Apps caps HTTP requests at 240s regardless.

Current implementation is an in-process task with a module-level job store,
which is honest for a single-user local prototype. The production shape
([#18](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/18))
is a persisted state machine with admission control and SSE layered over
polling — with **polling as the source of truth**, because SSE cannot survive
the ingress timeout.

### Rate limiting

A token bucket, defaulting to 2 RPM. Single worker, so an in-process bucket is
provably correct; the same interface can be backed by Redis when that stops
being true.

On 429 the bucket is **drained**: the service has told us our accounting is
optimistic, so we surrender the burst allowance rather than spending it on
further rejected requests. Backoff is full-jitter exponential, honouring
`Retry-After` as a floor.

400, 401 and 404 are **not retried** — they are our own bugs (bad dimensions,
bad credentials, wrong deployment name), and retrying them burns quota we do
not have.

---

## Mock mode

`MAI_MOCK` defaults to **on**.

There is no local emulator for MAI, and the real deployment allows 2–12 RPM.
Without a mock the pipeline could not be built before Azure existed, and the
test suite would inherit a rate limit.

The mock honours the parts of the contract the pipeline depends on: returns a
PNG, at exactly the requested dimensions, and **refuses dimensions MAI would
reject** — so a dimension bug fails in tests rather than in production. Its
`edit()` ignores any requested size and follows the input, mirroring the real
endpoint, so callers cannot depend on behaviour that does not exist.

A test asserts mock mode stays on, so the suite can never quietly start
spending real quota.

---

## What this architecture will not do

Stated plainly, because these are inherent rather than unfinished:

- **No Indic text inside the photograph.** The overlay sits *on top of* the
  image. It cannot put Tamil words on a curved shop awning in the scene. This
  is the sharpest limitation of the design.
- **Every delivered image is upscaled** (×1.055 to ×1.41). Fine for Instagram,
  visible in print. This product is for Instagram.
- **No character consistency across a series.** A brand mascot across five
  posts will drift; 2.6 is not documented for character consistency.
- **No Pantone-exact product colour** in the generated photograph. The overlay
  layer is exact; the imagery is approximate.
- **Throughput is quota-bound.** Under load this is a queue, not an instant
  tool. The UX has to be designed around that rather than retrofitted.
