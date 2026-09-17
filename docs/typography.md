# Indic typography

**This is the highest-risk component in the project, and the risk is specific:
broken shaping still renders.**

A detached matra or an unformed conjunct produces a perfectly valid PNG. It
passes every size check, every pixel check, every automated test you would
naturally write. It is illegible only to someone who reads the script — which,
for an English-speaking operator shipping Tamil creative, means it reaches the
audience.

That asymmetry is why this component gets its own document and its own class of
test.

---

## Why not Pillow

Correct Indic rendering is not "draw glyphs left to right". It requires:

- **Reordering** — the i-matra (ि) is typed *after* its consonant and painted
  *before* it. `कि` is stored `क` + `ि` and drawn `ि` + `क`.
- **Conjunct formation** — `क` + virama + `ष` → `क्ष`, driven by the font's
  GSUB tables. Hundreds of ligatures per script.
- **Above- and below-base marks** — Telugu and Kannada stack subscript
  consonants beneath the base glyph.
- **Reph and rakar positioning** in Devanagari.
- **Line breaking** without reliable word boundaries in long agglutinative
  Tamil and Malayalam compounds.

Pillow does none of this unless it was compiled against **libraqm** (which
wraps HarfBuzz and FriBiDi).

On this machine:

```
>>> from PIL import features
>>> features.check("raqm")
False
```

So `ImageDraw.text()` would silently emit unreordered, disconnected glyphs.
**Pillow is used for pixels only** — crop, resize, alpha composite. Never for
text layout.

---

## Why Chromium

Headless Chromium already carries HarfBuzz and ICU, is continuously tested
against these scripts by a browser vendor, and adds things we would otherwise
hand-roll: CSS line breaking, bidi, `lang`-driven `locl` lookups, and an
in-page auto-fit loop that measures what will actually be painted.

The alternative — `uharfbuzz` + `freetype-py` — means re-implementing line
breaking and bidi. We would get Malayalam wrong.

A second benefit: the overlay is authored as HTML/CSS, so the React frontend
can preview using the **same template**. One renderer to keep correct, not two.

```
OverlayRenderer.render()
  ├─ build HTML from the template + per-script metrics
  ├─ page.set_content()
  ├─ register bundled @font-face faces
  ├─ page.evaluate(fit script)      ← binary search, in-page measurement
  └─ page.screenshot(omit_background=True)  ──► transparent PNG
```

One browser is reused across renders. Launch costs hundreds of milliseconds;
each subsequent overlay is tens. That ratio is what makes "one base image, many
languages" cheap.

---

## Rules enforced in code

These are not template defaults. They are locked, because every one of them is
a mistake a well-meaning designer will otherwise make.

| Rule | Why |
|---|---|
| `letter-spacing: 0` for all non-Latin | Positive tracking inserts gaps *between shaped glyph components*, visibly breaking conjuncts and detaching matras |
| `font-synthesis: none` | Faux-bold smears conjuncts; faux-italic destroys cursive scripts |
| Per-script `line-height` | Latin's 1.2 clips marks that sit above and below the base glyph |
| `overflow-wrap: normal`, `hyphens: none` | Breaking an Indic word splits a consonant from its matra |
| Truncate on grapheme clusters | Cutting by code point severs a consonant from its vowel sign |

### Per-script metrics

| Locale | Label | Native | Script | Register | BCP-47 | Line height | Min size | Length factor |
|---|---|---|---|---|---|---|---|---|
| `en` | English | English | Latn | neutral | `en-IN` | 1.2 | 28px | 1.0 |
| `hi` | Hindi | हिन्दी | Deva | neutral | `hi-IN` | 1.5 | 34px | 1.18 |
| `hi-Latn` | Hinglish | Hinglish | Latn | conversational | `hi-Latn` | 1.2 | 28px | 1.0 |
| `mr` | Marathi | मराठी | Deva | neutral | `mr-IN` | 1.5 | 34px | 1.18 |
| `bn` | Bengali | বাংলা | Beng | neutral | `bn-IN` | 1.5 | 34px | 1.20 |
| `ta` | Tamil | தமிழ் | Taml | neutral | `ta-IN` | 1.45 | 36px | **1.35** |
| `te` | Telugu | తెలుగు | Telu | neutral | `te-IN` | 1.45 | 36px | 1.30 |

**Min size** is the smallest legible size on a 1080px canvas viewed on a phone.
Tamil and Telugu need a larger optical size than Latin at the same nominal
value.

**Length factor** scales English character budgets. Tamil at 1.35 is routinely
the language that overflows a layout built to English proportions — which is
why budgets are scaled at *authoring* time rather than discovered at render
time.

The `bcp47` tag is set as the `lang` attribute, which drives the browser's
`locl` lookups. Marathi wants different Devanagari letterforms than Hindi for
some glyphs, and the browser only knows that if we say so.

---

## Auto-fit

A binary search for the largest size that fits, run **in-page** so the
browser's own shaping and line breaking decide what "fits".

```
for each headline candidate (preferred, shorter, shortest):
    binary search size in [min_size, max_size]:
        fits = headline lines <= max_lines
           and no horizontal overflow
           and subhead within its line budget
           and total content height <= box height
    if the best size clears the script minimum: take it
```

**Selecting a shorter line beats shrinking type below legibility**, which is
why the copy writer returns three length variants per headline.

Headline, subhead and CTA are sized **together**. Fitting the headline alone
and then adding a subhead underneath is how a layout ends up overflowing.

### Two bugs this went through

Both are recorded because each produced plausible-looking output.

**1. Words broke mid-cluster.** `overflow-wrap: break-word` meant `scrollWidth`
could never exceed `clientWidth`, so the fit test always reported success at
the maximum size. Every locale returned exactly 115px — the tell. Tamil was
being chopped as `தூய்மை / யின்`, splitting a word across lines mid-cluster.

Fix: forbid breaking, so overflow becomes *detectable* and the search shrinks
instead. Tamil now fits at 79px, unbroken. Breaking survives only as a last
resort, and sets a `did_break` flag that routes the variant to review.

**2. The fit test was structurally broken.** `block.scrollHeight` is always
1–7px greater than `clientHeight`, because glyphs overshoot their line box. So
*every* candidate size looked like a failure and everything fell back to the
minimum. It appeared to work only when a CTA was present, by coincidence of
flex layout.

Fix: measure the real stacked height of the visible slots rather than relying
on `scrollHeight`.

---

## Testing that shaping is *active*

Asserting that ink appeared proves nothing — broken shaping produces ink too.

The test measures a **ligated conjunct against the same characters forced apart
with a zero-width non-joiner** (U+200C). A shaping engine produces different
widths for the two; a naive glyph blitter produces identical ones.

| Locale | Ligated | Forced apart |
|---|---|---|
| Hindi | `क्ष` | `क्` + ZWNJ + `ष` |
| Hindi | `शुद्ध` | `शुद्` + ZWNJ + `ध` |
| Bengali | `বিশুদ্ধ` | `বিশুদ্` + ZWNJ + `ধ` |
| Telugu | `స్వచ్ఛ` | `స్వచ్` + ZWNJ + `ఛ` |

A companion test covers **matra reordering** by checking that `कि` has greater
advance width than bare `क`.

If these ever pass by equality, GSUB is not running and every Indic headline
the product ships is malformed.

### The visual gate

```bash
cd backend && ../.venv/Scripts/python scripts/language_proof.py
```

Renders one campaign in all seven locales from a single generation and builds a
contact sheet. **This step has to be checked by eye** — it is precisely the
class of defect no assertion catches.

---

## Fonts

Currently rendering falls back to Windows' **Nirmala UI**, which covers every
v1 script and is fine for development. It is **not redistributable** and will
not exist in a Linux container.

Production bundles **Noto Sans Devanagari / Bengali / Tamil / Telugu** (SIL
OFL) from `fonts/`, loaded via `@font-face` at render time.

**Versions must be pinned and hashed.** A silent Noto upgrade changes line
metrics and reflows every historical creative. `render_engine_version` is
stamped on each variant so a Chromium upgrade is detectable too.

Tracked as
[#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20).

### A concession to be explicit about

Almost no brand's custom Latin typeface covers Devanagari, let alone Odia. The
policy is: brand font for Latin, mapped Noto family for the script. The brand's
Latin identity is preserved; their Indic identity becomes Noto.

That is a real concession, and brands with strong typographic identity will
notice. Tell them up front rather than letting them discover it.

---

## Not yet handled

**Urdu (Nastaliq)** is deferred. It needs RTL layout, a line-height of ~2.0
because of its sloped baseline, and layout mirroring decisions that are genuine
design judgements — mirror the text block, but never the logo (a trademark
violation) or the photograph (a defect). `ScriptMetrics` already carries an
`rtl` flag and the renderer sets `dir`, so the hook exists.

**Diegetic text** — words physically inside the scene, on a shop sign or
packaging — is out of scope. The overlay sits on top of the image and cannot
put Tamil on a curved awning. This is the sharpest limitation of the whole
design and belongs in the FAQ, not the small print.
