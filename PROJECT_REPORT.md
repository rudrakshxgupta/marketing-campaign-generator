# Project Report — Marketing Campaign Generator

**Status as of 18 September 2026**

A platform that turns a brief or a reference image into Instagram-ready
campaign creatives in Indian languages, built on Microsoft AI Foundry with
**MAI-Image-2.6**.

| | |
|---|---|
| Repository | [rudrakshxgupta/marketing-campaign-generator](https://github.com/rudrakshxgupta/marketing-campaign-generator) (private) |
| Tests | **179 passing**, ~36 seconds |
| Code | 3,246 lines across 20 modules · 1,169 lines of tests |
| Issues | **40 total — 21 built, 19 pending** |
| Phase 1 | ✅ Complete |
| Phase 2 | 9 of 14 — everything buildable without Azure is built |
| Azure | **Not yet connected.** Nothing has run against the live service. |

---

## 1. What this product is

A marketer types *"a bottle of coconut oil for Diwali"*, optionally uploads a
reference photo and their logo, and receives finished Instagram posts —
image, caption, hashtags, alt text — in seven language variants, ready to
publish.

**The unit of delivery is a campaign bundle, not an image.** Shipping a single
image download would be a demo; a marketer needs everything required to
actually post.

---

## 2. The central design decision

Early research turned up three facts that, together, ruled out the obvious
approach of having the AI draw the text into the picture.

1. **MAI-Image-2.6 declares `Languages: en`.** English prompts only. Drawing
   Devanagari or Tamil is outside its supported envelope.
2. **The model allows 2–12 requests per minute** for the whole account. Seven
   languages across four formats as separate calls is not physically possible.
3. **Azure OCR cannot read 8 of the Indic scripts** — including Bengali, Tamil
   and Telugu. We could not *verify* model-drawn text in most of our languages
   even if we wanted it.

That third point is what turns a judgement call into a rule: **we do not ship
what we cannot check.**

So the architecture is:

> **Generate ONE text-free image. Composite the logo and every language's text
> deterministically on top.**

```
brief ──► CreativeBrief ──► English prompt ──► MAI-Image-2.6 ──► text-free base
                                                                      │
                            ┌─────────────────────────────────────────┤
                            ▼                    ▼                    ▼
                      en overlay           hi overlay           ta overlay   …
                            │                    │                    │
                            ▼                    ▼                    ▼
                      + real logo          + real logo          + real logo
                            │                    │                    │
                         post-ready          post-ready          post-ready
```

### What it buys

| | Model draws the text | Our approach |
|---|---|---|
| 7 languages × 2 formats | 14 model calls | **2 model calls** |
| Indic spelling | garbled, plausibly | correct by construction |
| Brand logo | hallucinated letterforms | byte-exact |
| Brand colour | approximate | exact |
| Text QA | inference — and impossible for 5 of 7 languages | **arithmetic** |
| Fixing a typo | full regeneration | re-render, ~200ms, no quota |

**Measured: 2 image calls produced 14 finished deliverables.**

The last row matters more than it looks. Marketers change copy constantly. In a
model-drawn design every edit costs a rate-limited call.

---

## 3. What has been built

### 3.1 Image sizing — harder than it looks

MAI constrains every request to `width ≥ 768`, `height ≥ 768`,
`width × height ≤ 1,048,576`. Those three rules interact badly:

- **Instagram's 9:16 story is illegal by one pixel.** The largest legal pair at
  exactly 0.5625 is 767×1364 — one pixel under the floor. Usable pair is
  768×1365, cropped afterwards.
- **1.91:1 landscape cannot be generated at all.** It would need a 740px side.
  We generate 16:9 and crop.

The legal aspect band is **0.5626 – 1.7773**.

| Format | Instagram | Generate at | Budget used | Crop |
|---|---|---|---|---|
| portrait *(feed default)* | 1080×1350 | 912×1140 | 99.2% | no |
| grid *(profile crop)* | 1080×1440 | 885×1180 | 99.6% | no |
| story / reel | 1080×1920 | 768×1365 | 100% | **yes** |
| square | 1080×1080 | 1024×1024 | 100% | no |
| landscape | 1080×566 | 1365×768 | 100% | **yes** |

All of it lives in one module. Scattered across call sites it would be got
wrong, and the failure is a 400 that costs a request from a tiny quota.

### 3.2 Indic typography — the highest-risk component

**The risk is specific: broken shaping still renders.** A detached matra or an
unformed conjunct produces a perfectly valid PNG that passes every size and
pixel check, and is illegible only to someone who reads the script.

`PIL.features.check("raqm")` is **`False`** on the development machine, so
Pillow's `ImageDraw.text()` would silently emit unreordered, disconnected
glyphs. Text therefore goes through **headless Chromium**, which carries
HarfBuzz and ICU and is continuously tested against these scripts by a browser
vendor.

Locked in code, not left to templates:

- `letter-spacing: 0` for every non-Latin script — tracking pulls shaped
  clusters apart and visibly breaks conjuncts
- `font-synthesis: none` — faux-bold smears conjuncts
- Per-script line height (Devanagari 1.5, Tamil 1.45) — Latin's 1.2 clips marks
- Words are **never** broken; the fitter shrinks type instead, because breaking
  an Indic word splits a consonant from its matra

**Proof that shaping is actually running**, not merely producing ink: the tests
measure a ligated conjunct against the same characters forced apart with a
zero-width non-joiner. A shaping engine gives different widths; a naive glyph
blitter gives identical ones. Covers `क्ष`, `शुद्ध`, `বিশুদ্ধ`, `స్వచ్ఛ`, plus
matra reordering.

### 3.3 Languages as a triple

Language is modelled as **`(language, script, register)`**, not one dropdown.

That makes **Hinglish** — `hi` + `Latn` + `conversational` — a first-class
variant. A large share of real Indian Instagram copy is Romanised Hindi, because
many people speak Hindi fluently but read Devanagari slowly.

**Copy is transcreated, not translated.** Each language is authored from a
shared strategy, never from the English output. That lets the *cultural
referent* change and not just the words — a Diwali line becomes a **Durga Puja**
line for Bengali, which is the correct campaign rather than the correct
translation.

v1 locales: English, Hindi, Hinglish, Marathi, Bengali, Tamil, Telugu.

### 3.4 Logo and safe zones

The logo is **never** described to the model. A diffusion model produces a
*plausible* logo, which is a wrong logo — and describing a mark in a prompt is
asking the model to reproduce a trademark.

Instead it is composited from the real file, anchored **inside the Instagram
safe rect** rather than the canvas edge, with the light or dark knockout chosen
by measuring the background luminance and escalating to a scrim if neither
clears WCAG 4.5:1.

Meta unified the 9:16 safe zone in March 2026: **top 14%, bottom 35% for Reels,
left/right 6%** — a usable box of only **950×979**. Violations raise, they do
not warn. Content outside that rect is not "slightly cropped", it is invisible.

### 3.5 Spend controls

Added after the credit constraint became clear, and ahead of schedule.

| Control | Effect |
|---|---|
| **Hard ceiling** | 25/day, 200 total. Checked *before* the call, persisted to disk, survives restarts. A retry loop cannot drain anything. |
| **Failed calls are free** | A 429 or network error is never charged — charging for it would make the ledger lie. |
| **Cache** | Byte-identical repeats served from disk. Re-clicking Generate costs nothing, ever. |
| **Economy mode** | One tall master cropped down to every format. Measured: **4 formats went from 4 calls to 1.** |

Composed as `cache → budget → client`, so a cache hit consumes no budget.
`GET /api/usage` reports spend, headroom and calls saved.

### 3.6 Reference image, both modes

- **"Use as inspiration"** *(default)* — the style is measured **locally**
  (palette, brightness, contrast, temperature, orientation). No model call, no
  quota, no latency. Output still obeys our layout and safe zones. The palette
  reaches the prompt as **colour words, not hex** — diffusion models respond to
  "deep saffron" far better than to `#FF6B00`.
- **"Edit this image"** — reproduces the upload pixel-for-pixel, so it requires
  an explicit rights confirmation. Defaulting to this would quietly turn
  whatever a user uploaded into a derivative work.

Style extraction never touches the subject; the reference's own silhouette,
marks and faces go into `must_not_depict`. That separation is the line between
"in the style of" and a copy.

### 3.7 Subject preservation — real estate and real products

When a user uploads a photo of something they actually sell, the model must
**restage** it, not **redesign** it. MAI's edit mode supports attribute changes,
so left alone it will add a balcony or turn a four-storey building into a
five-storey one. For a property listing that is not a bad render — it is an
advertisement for a building that does not exist.

Two mechanisms, because a prompt is advisory:

1. **A clause naming the actual failures**, per subject kind. For architecture:
   floor count, every window and balcony, roofline, façade material, structural
   layout. Generic "keep it the same" does not work — a model has no reason to
   think floor count is what "the same" means.
2. **A measurement afterwards** that checks whether it listened.

The measurement cannot be whole-frame similarity. Restaging is *supposed* to
change the background, so a harmless sky swap moves more pixels than a
dangerous extra storey. The first implementation got this exactly backwards and
the test caught it. The subject is now located first, then three things are
measured: similarity inside it, change in its extent, and change in detail
density.

| Case | Verdict |
|---|---|
| sky recoloured, bokeh added | ✅ pass |
| one extra floor · extra window · façade changed | ❌ reject |

The extra floor scores **1.000 on similarity** — only the extent signal catches
it. One measure would not be enough.

Eight subject kinds; architecture and jewellery are held strictest. Details in
[docs/fidelity.md](docs/fidelity.md).

### 3.8 Test coverage

| File | Tests | Covers |
|---|---|---|
| `test_compose.py` | 43 | Contrast maths, logo placement, safe zones |
| `test_dimensions.py` | 34 | MAI size rules, every Instagram format |
| `test_overlay.py` | 27 | Indic shaping, auto-fit, containment |
| `test_spend.py` | 20 | Cache, budget ceiling, economy mode |
| `test_reference.py` | 14 | Style extraction, both reference modes |
| `test_fidelity.py` | 24 | Subject preservation, all eight kinds |
| `test_api.py` | 9 | HTTP surface, job lifecycle, bundle |
| `test_ratelimit.py` | 8 | Token bucket, drain, backoff |

Mock mode is on by default and a test asserts it stays on — the suite can never
quietly start spending real quota.

---

## 4. Bugs found and fixed

Recorded because each produced output that *looked* correct.

**Tamil words broke mid-cluster.** The tell was every locale returning exactly
the maximum font size. `overflow-wrap: break-word` meant overflow could never be
detected, so the fit search always "succeeded" at maximum and long Tamil words
were chopped across lines. Now words never break — the fitter shrinks instead.

**The fit test was structurally broken.** `block.scrollHeight` always exceeds
`clientHeight` by a pixel or two because glyphs overshoot their line box, so
*every* candidate size looked like a failure and everything silently fell back
to the minimum. It appeared to work only when a CTA was present, by coincidence
of flex layout.

**The safe-zone gate fired on its first real use** — a logo anchored 5% from
the canvas edge lands under the feed caption row. Placement now derives from
the safe rect, making compliance structural.

**Background jobs raced with shutdown.** Tasks were fire-and-forget, so closing
the browser could happen while a job was still rendering into it — an
indefinite hang, not an error. Found by a test that hung the entire suite; jobs
are now drained, then cancelled after a timeout.

**One case where the test was wrong, not the code.** An assertion required every
format to upscale. Landscape *downscales* — 1080×566 is fewer pixels than the
16:9 frame it is cropped from — which is a quality win. The assertion now checks
what actually matters: that upscaling stays within a 1.45× budget.

---

## 5. What is NOT built

Stated plainly, because some of it is load-bearing.

**Nothing has run against Azure.** `MaiImageClient` is written and
unit-tested. Since this was written the system has run live many times: FLUX.2-pro for imagery and GPT-5-mini for the brief and copy, with the
coloured placeholder.

**The copy is canned.** Hindi and Tamil text in the demos is hand-written
sample text, not model output. The transcreation prompt is written; the model
is not wired up.

**Fonts are borrowed.** Rendering falls back to Windows' Nirmala UI, which we
are **not licensed to redistribute** and which will not exist on a Linux
server. Output is currently not reproducible off the development machine.

**No frontend.** A working backend with no screen. Everything goes through
`curl` or the FastAPI docs page.

**No safety layer.** Nothing blocks inappropriate content, maps of India (the
model gets the borders wrong, which is a legal problem), the national flag, or
public-figure likenesses.

**No AI-content label beyond the caption.** Indian law has required visual
AI content to be labelled since 20 February 2026. A per-language caption line
is implemented; the embedded C2PA certificate is not.

---

## 6. Outstanding work

18 open issues. Six need Azure; twelve do not.

### Needs a live deployment

| # | Item | Priority |
|---|---|---|
| [#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29) | Probe the open questions | **blocked, start here** |
| [#13](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/13) | OCR gate — assert the base has no text | high |
| [#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12) | Live chat model for transcreation | high |
| [#14](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/14) | Content Safety at every checkpoint | high |
| [#23](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/23) | Route drafts to MAI-Image-2.6-Flash | |
| [#25](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/25) | Azure infrastructure as code | |

### Buildable today

| # | Item | Priority |
|---|---|---|
| [#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20) | Bundle Noto fonts, pin versions, golden tests | **high** |
| [#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15) | C2PA provenance and AI-disclosure label | **high** |
| [#18](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/18) | Persistent job store, state machine, SSE | |
| [#28](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/28) | Brand kit CRUD, colour extraction | |
| [#21](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/21) | Native-speaker review workflow | |
| [#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22) | React frontend with phone-frame preview | |
| [#16](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/16) | Category compliance gates | |
| [#17](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/17) | Hard blocks: maps, flags, deities | |
| [#26](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/26) | Festival calendar presets | |
| [#27](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/27) | Carousel and WhatsApp Status formats | |
| [#35](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/35) | Vision-model style upgrade | |
| [#36](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/36) | Pre-select reference mode from phrasing | |

---

## 7. The plan from here

### Step 1 — Connect Azure and run the probe · 3 images, ~2 minutes

```bash
winget install --id Microsoft.AzureCLI -e     # then open a NEW terminal
az login
./scripts/setup_foundry.ps1 -ResourceName <globally-unique-name> -WriteEnv
cd backend && ../.venv/Scripts/python scripts/probe_foundry.py
```

The setup script verifies MAI-Image-2.6 is actually available to the
subscription *before* deploying anything, and supports `-WhatIf`. Deploying
costs nothing by itself — billing is per image generated.

**Why this is first.** Six questions cannot be answered from documentation, and
each one could change downstream design:

- Real generation latency — decides whether the UI waits on screen or notifies.
  The docs give no figures at all.
- Whether MAI output already carries a C2PA certificate — decides how much
  compliance work [#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15) is.
- Whether the edit endpoint's output geometry follows its input — the entire
  reference-image path assumes it does.
- Whether separate deployments get independent RPM buckets —
  [#23](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/23)
  rests on this.

Three credits to de-risk all of it is a good trade. The expensive mistake is
building a frontend and a compliance layer on assumptions that turn out wrong.

**Also on day one: file the quota-increase request** at
[aka.ms/oai/stuquotarequest](https://aka.ms/oai/stuquotarequest). Priority goes
to accounts already using their allocation, so the clock starts when you begin
generating — not when you ask.

### Step 2 — Bundle the fonts ([#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20))

Needs nothing from Azure. Fixes a licensing problem *and* a reproducibility
one. Versions must be pinned and hashed — a silent Noto upgrade changes line
metrics and reflows every historical creative.

### Step 3 — Live transcreation ([#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12))

With Step 2 this turns the multilingual story from demonstrated to real. It is
the actual differentiator; every global tool does Indic typography badly because
their India revenue does not justify the work.

### Step 4 — AI labelling ([#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15))

A legal requirement, not a feature. Cheap now, awkward to retrofit.

### Step 5 — Frontend ([#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22))

Once the pipeline behind it is real. Must include a **phone-frame preview with
safe-zone guides** — marketers will not trust an output they cannot see framed
in an actual Instagram UI.

---

## 8. Risks

| Risk | Standing |
|---|---|
| **MAI is public preview** — no SLA, Microsoft's own docs say not recommended for production | **Open.** A business risk to accept deliberately. Mitigated only in blast radius: every call is isolated in one module. |
| Credits run out | **Mitigated.** Hard ceiling, cache, economy mode. `GET /api/usage` shows headroom. |
| 2 RPM makes the product feel broken | **Mitigated architecturally.** One generation serves every language. Queue UX still to build. |
| Model draws text despite instruction | **Partly.** Prompt forbids it exhaustively; the OCR gate ([#13](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/13)) that catches failures is not built. |
| Indic rendering defects ship silently | **Mitigated.** Shaping proven active by test; golden-image CI still pending ([#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20)). |
| Output not reproducible off this machine | **Open.** Fonts are borrowed from Windows. [#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20). |
| Legal exposure on AI labelling | **Partly.** Caption line implemented; embedded provenance is not. [#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15). |

---

## 9. Known limitations

Inherent to the design rather than unfinished:

- **No Indic text inside the photograph.** The overlay sits *on top of* the
  image. It cannot put Tamil words on a curved shop awning in the scene. This is
  the sharpest limitation of the whole approach and belongs in the FAQ.
- **Every delivered image is upscaled** (×1.055 to ×1.41). Fine for Instagram,
  visible in print. This product is for Instagram.
- **No character consistency across a series.** A brand mascot across five posts
  will drift; 2.6 is not documented for character consistency.
- **No Pantone-exact product colour** in generated imagery. The overlay layer is
  exact; the photograph is approximate.
- **Brand fonts are substituted for Indic scripts.** Almost no custom typeface
  covers Devanagari. The brand's Latin identity is preserved; their Indic
  identity becomes Noto. Tell brands up front rather than letting them discover
  it.
- **Throughput is quota-bound.** Under load this is a queue, not an instant
  tool.

---

## 10. Documentation

| Document | Covers |
|---|---|
| [README.md](README.md) | Quick start, Azure setup, spend controls |
| [docs/architecture.md](docs/architecture.md) | How the system works and why |
| [docs/constraints.md](docs/constraints.md) | Every verified platform fact, with sources |
| [docs/typography.md](docs/typography.md) | Indic rendering — the risky part |
| [docs/fidelity.md](docs/fidelity.md) | Keeping a real subject unchanged through an edit |
| [docs/api.md](docs/api.md) | HTTP surface and configuration |
| [docs/code-map.md](docs/code-map.md) | Module-by-module reference |
| [docs/compliance.md](docs/compliance.md) | Indian advertising law, safety, provenance |
| [docs/testing.md](docs/testing.md) | Including what must be checked by eye |
| [docs/decisions.md](docs/decisions.md) | 13 decision records |
| [docs/roadmap.md](docs/roadmap.md) | Built vs pending, mapped to issues |
| [HOW_IT_WAS_BUILT.md](HOW_IT_WAS_BUILT.md) | The making of it — research, decisions, bugs |

The backlog lives in [`.github/backlog.json`](.github/backlog.json) with an
idempotent seeder — add to the JSON and re-run rather than hand-editing on
GitHub.
