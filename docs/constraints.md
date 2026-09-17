# Verified constraints

Every non-obvious design choice in this project traces back to something on
this page. All of it was verified against primary sources or measured on the
machine, not assumed.

---

## MAI-Image-2.6

Source: [Deploy and use MAI image models in Microsoft Foundry](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/use-foundry-models-mai-image)

| Item | Value |
|---|---|
| Model | `MAI-Image-2.6`, version `2026-07-31` |
| Deployment | `--model-format Microsoft`, SKU `GlobalStandard` |
| Text-to-image | `POST {endpoint}/mai/v1/images/generations` (JSON) |
| Image-to-image | `POST {endpoint}/mai/v1/images/edits` (multipart/form-data) |
| Auth | `api-key` header, or Entra bearer scoped `https://cognitiveservices.azure.com/.default` |
| Response | `{"data":[{"b64_json":"<base64 PNG>"}]}` — always PNG, one image |
| Prompt limit | 32,000 tokens |
| Rate limit | **2–12 RPM** by tier; `429` on exceed |
| Region | **`southindia`**, `uaenorth` |
| **Languages** | **`en` only** |
| Status | **Public preview.** No SLA. |

### Parameter scope — the trap

| Parameter | generations | edits |
|---|---|---|
| `model`, `prompt` | ✅ | ✅ |
| `image` | — | ✅ |
| **`width`, `height`** | ✅ | **❌** |
| `auto_aspect_ratio`, `web_grounding` | ✅ (2.6 only) | ✅ (2.6 only) |

**The edits endpoint accepts no dimensions.** Output geometry for the
image-to-image path is therefore handled entirely by cropping — the reference
is cover-cropped to the target aspect before upload, and the response is
cropped again after. Never by asking the API.

There is also **no negative-prompt parameter**. Avoidances have to be written
into the prompt body as enumerated absences.

### `Languages: en`

The model card lists English as the only supported language. This is the single
most consequential fact in the project: it means asking MAI to render Hindi,
Tamil, Telugu, Bengali or Marathi text inside an image is outside its supported
envelope, regardless of how good its Latin text rendering is.

### Rate limit

Global Standard tiers 0–6 give **2, 4, 6, 8, 10, 12** RPM. At tier 1 that is
one request every 30 seconds for the entire tenant.

This is the binding constraint on the whole product. It is why the architecture
generates once and composites many times, and why generation is always a job
rather than a synchronous request.

---

## Derived dimension rules

The three limits — `width >= 768`, `height >= 768`, `width × height <= 1,048,576` —
interact in ways that are easy to get wrong.

Longest a side may be while the other still clears the floor: `1,048,576 ÷ 768 = 1365`.

**Legal aspect band (w/h): 0.5626 – 1.7773.** Anything outside it is not
generable at any size.

| Format | Instagram | Generate at | Pixels | Budget used | Crop | Scale | Safe box |
|---|---|---|---|---|---|---|---|
| portrait *(feed default)* | 1080×1350 | **912×1140** | 1,039,680 | 99.2% | no | ×1.184 | 950×1161 |
| grid *(profile crop)* | 1080×1440 | **885×1180** | 1,044,300 | 99.6% | no | ×1.220 | 950×1239 |
| story / reel | 1080×1920 | **768×1365** | 1,048,320 | 100.0% | **yes** | ×1.407 | 950×979 |
| square | 1080×1080 | **1024×1024** | 1,048,576 | 100.0% | no | ×1.055 | 950×929 |
| landscape | 1080×566 | **1365×768** | 1,048,320 | 100.0% | **yes** | ×0.791 | 950×487 |

### Two traps

**9:16 is illegal by one pixel.** Instagram's story format is exactly 0.5625.
The largest legal pair at that ratio is 767×1364 — the width is one pixel under
the floor, so the request is rejected with a 400. The usable pair is 768×1365
(0.56264), cropped to exact 9:16 afterwards.

**1.91:1 landscape is unreachable.** Reaching it with a height ≥ 768 would need
a width of 1467, which busts the pixel budget. It cannot be generated at any
size. We generate 16:9 and crop.

### Landscape downscales, and that is fine

1080×566 is 611k pixels — fewer than the 16:9 frame it is cropped from. It is
the only format that loses nothing to interpolation. Pinned by a test so nobody
"fixes" it.

All of this lives in `backend/app/imaging/dimensions.py`. Scattered across call
sites, it will be got wrong.

---

## Instagram formats and safe zones

Verified September 2026.

**4:5 (1080×1350) is the feed default, not 1:1.** The profile grid crops to
**3:4 (1080×1440)**. The working method is to compose on a 3:4 master and keep
everything critical inside the centred 1080×1350 band — 45px of bleed top and
bottom. Getting this wrong is why AI-made posts so often look decapitated on a
profile page. Implemented as `grid_bleed_band()`.

### Safe zone insets

Meta unified the 9:16 safe zone across Instagram and Facebook Stories and Reels
in **March 2026**, moving from fixed pixels to percentages — there are too many
live phone aspect ratios for fixed margins to work.

| Format | Top | Right | Bottom | Left |
|---|---|---|---|---|
| story / reel | **14%** | 6% | **35%** | 6% |
| portrait, grid, square, landscape | 6% | 6% | 8% | 6% |

At 1080×1920 that leaves a usable box of **950×979** — less than half the
canvas.

The 9:16 figures are deliberately the **Reels** ones. An asset designed to the
Reels-safe box also works as a Story; one designed to the Story box loses its
bottom 15% when posted as a Reel.

Content outside the rect is not "slightly cropped" — it is covered by the
caption tray, audio credit, follow button and engagement rail, and is
**invisible**. So violations raise `SafeZoneViolation` rather than warning.
Warnings get ignored at volume.

---

## Azure Vision Read OCR coverage

Printed-text extraction supports Latin, Cyrillic, Arabic and **Devanagari**
script languages.

| Language | Script | OCR extraction |
|---|---|---|
| English | Latin | ✅ |
| Hindi, Marathi | Devanagari | ✅ |
| **Bengali** | Bengali | ❌ |
| **Tamil** | Tamil | ❌ |
| **Telugu** | Telugu | ❌ |
| Kannada, Malayalam, Gujarati, Gurmukhi, Odia | — | ❌ |

**For most of our target languages there is no automated way to verify that
rendered text is correct.** This turns "diffusion probably garbles Indic" from
a judgement call into an operational rule: we cannot ship what we cannot check,
so the model never draws these scripts.

It also means the honest answer to *"how do you know the Tamil is right?"* is
**deterministic font rendering plus native-speaker review of a sample** — not
"we check it automatically."

---

## Local environment

Measured on the development machine.

| Item | Value | Consequence |
|---|---|---|
| Python | 3.14.2 | All dependencies resolved; no wheel gaps, so the planned 3.12 pin was unnecessary |
| Node | v26.7.0 | Available for the frontend |
| `az` CLI | **not installed** | Setup docs must say so |
| `PIL.features.check("raqm")` | **`False`** | **Pillow cannot shape Indic scripts here** |
| Windows Indic font | `Nirmala.ttc` | Covers every v1 script, but **not redistributable** |

The RAQM result is decisive. Without libraqm, `ImageDraw.text()` silently emits
unreordered, disconnected glyphs — a failure that produces a valid PNG and is
invisible to anyone who does not read the script. Pillow is used for pixels
only; text goes through Chromium. See [typography.md](typography.md).

---

## Indian AI-content law

**MeitY's IT (Intermediary Guidelines and Digital Media Ethics Code) Amendment
Rules, 2026** — notified 10 Feb 2026, in force **20 Feb 2026**.

- Introduces **Synthetically Generated Information (SGI)**: content
  artificially created or altered such that it appears authentic. **AI-generated
  *text* is excluded**; visual content is not.
- Non-prohibited SGI must be **clearly and prominently labelled**. The proposed
  10%-of-area minimum watermark size was **dropped** from the final rules.
- Provenance metadata must be embedded **where feasible**.
- Significant social media intermediaries — Instagram qualifies — must require
  users to **declare AI content at upload** and verify that declaration by
  automated means.

Because platforms strip metadata on upload, **the caption line is the only
disclosure channel that reliably reaches a viewer**. Implemented per-language
in `transcreate.py`.

See [compliance.md](compliance.md) for the advertising-standards layer.
