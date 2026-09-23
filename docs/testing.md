# Testing

```bash
cd backend && ../.venv/Scripts/python -m pytest -q
```

**121 tests, ~15 seconds.** Mock mode is on by default, and a test asserts it
stays on — the suite can never quietly start spending real MAI quota.

| File | Tests | Covers |
|---|---|---|
| `test_dimensions.py` | 34 | The MAI size rules and every Instagram format |
| `test_compose.py` | 43 | Contrast maths, logo placement, safe zones, mock end-to-end |
| `test_overlay.py` | 27 | Indic shaping, auto-fit, containment |
| `test_ratelimit.py` | 8 | Token bucket, drain, backoff |
| `test_api.py` | 9 | HTTP surface, job lifecycle, bundle contents |

---

## What the tests are actually defending against

Ordinary correctness tests are the easy part. The three below exist because the
corresponding failure produces **output that looks fine**.

### 1. Shaping that is not running

Asserting ink appeared proves nothing — broken shaping produces ink too, in the
right colour, at the right size, inside the right box.

So the test measures a **ligated conjunct against the same characters forced
apart with a zero-width non-joiner** (U+200C). A shaping engine produces
different widths; a naive glyph blitter produces identical ones.

```python
ligated_w = await renderer.measure("क्ष", locale="hi")
split_w   = await renderer.measure("क्‌ष", locale="hi")
assert ligated_w < split_w
```

Covers Devanagari (`क्ष`, `शुद्ध`), Bengali (`বিশুদ্ধ`) and Telugu (`స్వచ్ఛ`),
plus matra reordering via `कि` versus bare `क`.

If these ever pass by equality, every Indic headline the product ships is
malformed.

### 2. Dimensions asserted against the rules, not the numbers

Hard-coding `912×1140` would pass forever even if the resolver broke. So every
format is checked against the **raw constraints** — both sides ≥ 768, product
≤ 1,048,576 — plus budget utilisation above 99%, because leaving pixels unused
is a straight quality loss once everything is upscaled to 1080px.

The two traps get explicit tests: that exact 9:16 is illegal, and that 1.91:1
falls outside the generable band.

### 3. Safe-zone placement as a structural property

Logo placement is tested across **every format × every anchor**, asserting it
lands inside the safe rect. Because placement is derived from the safe rect
rather than the canvas edge, this is a structural property rather than a lucky
default — and the test would catch a regression to edge-anchoring immediately.

The story safe rect is pinned to the published spec: `950×979`, top at 269px,
bottom at 1248px.

---

## The visual gate

Some defects cannot be asserted. A detached matra renders, measures correctly,
and stays in its box.

```bash
cd backend && ../.venv/Scripts/python scripts/language_proof.py
```

Renders one campaign in all seven locales from a **single** generation and
writes a contact sheet to `storage/renders/proof_contact_sheet.png`.

```
base generated once at 912x1140 -> (1080, 1350)
  English   Latn  headline=108px (2 ln) sub= 59px cta= 45px logo=light
  Hindi     Deva  headline= 91px (1 ln) sub= 50px cta= 38px logo=light
  Hinglish  Latn  headline=108px (2 ln) sub= 59px cta= 45px logo=light
  Marathi   Deva  headline=115px (1 ln) sub= 63px cta= 48px logo=light
  Bengali   Beng  headline= 88px (2 ln) sub= 48px cta= 37px logo=light
  Tamil     Taml  headline= 79px (2 ln) sub= 43px cta= 33px logo=light
  Telugu    Telu  headline= 91px (2 ln) sub= 50px cta= 38px logo=light
```

**Look at the output.** Check that conjuncts are formed, matras are attached,
the Devanagari shirorekha is continuous, and no word is split across lines.

The sample strings are deliberately conjunct-heavy: शुद्ध (द्ध), त्योहार (त्य),
বিশুদ্ধতার (দ্ধ), স্বচ্ছ-style stacking in స్వచ్ఛత (చ్ఛ).

### Reading the numbers

Sizes **differing per locale is the healthy signal.** Tamil at 79px against
English at 108px is the length factor working — Tamil runs ~35% longer for the
same meaning.

**Every locale reporting the identical size is a red flag.** That was the
symptom of the auto-fit bug: all seven pinned at exactly 115px, the configured
maximum, because `overflow-wrap: break-word` made overflow undetectable. See
[typography.md](typography.md#two-bugs-this-went-through).

---

## Bugs the tests caught

Recorded because each is a category of mistake worth recognising again.

**The safe-zone gate fired on its first real use.** A logo anchored 5% from the
canvas edge lands *under* the feed caption row. Fixed by anchoring inside the
safe rect.

**Words broke mid-cluster in Tamil.** The tell was every locale returning the
maximum size.

**The fit test was structurally broken.** `block.scrollHeight` always exceeds
`clientHeight` by a pixel or two because glyphs overshoot their line box, so
every candidate size looked like a failure. It appeared to work only when a CTA
was present, by coincidence of flex layout.

**A test assumption that was wrong, not the code.** An early assertion required
every format to upscale. Landscape *downscales* — 1080×566 is fewer pixels than
the 16:9 frame it is cropped from — which is a quality win. The assertion now
checks what actually matters: that upscaling stays within a 1.45× budget.

---

## Not yet covered

- **Live MAI.** The FLUX path is exercised live regularly; `MaiImageClient` is
  unit-tested but has never touched a real MAI endpoint, because MAI image
  quota on this subscription is zero
  ([#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29)).
- **Golden-image shaping diffs.** The ZWNJ test proves shaping is *active*;
  goldens would catch the subtler case where shaping runs but the font changed
  underneath ([#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20)).
- **Cross-platform rendering.** Everything so far is Windows with Nirmala UI
  fallback. Linux with bundled Noto will produce different metrics.
- **Load behaviour.** The concurrency test covers the bucket, not a real queue
  under sustained pressure.
