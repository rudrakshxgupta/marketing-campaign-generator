# Marketing Campaign Generator

Instagram campaign creatives from a brief or a reference image, in Indian
languages, built on Microsoft AI Foundry with **MAI-Image-2.6**.

## The one idea

Generate **one text-free image**. Composite the logo and every language's text
deterministically on top.

Three verified facts force this:

1. **MAI-Image-2.6 declares `Languages: en`.** Asking it to draw Devanagari or
   Tamil is outside its supported envelope, and Indic scripts need complex
   shaping the model will garble in a uniquely dangerous way — the output looks
   plausible to an English-speaking operator and illiterate to the audience.
2. **The model allows 2–12 requests per minute.** Six languages across four
   formats as separate calls is not physically possible.
3. **Azure OCR cannot extract Bengali, Tamil, Telugu, Kannada, Malayalam,
   Gujarati, Gurmukhi or Odia.** For most of our languages we could not verify
   model-drawn text even if we wanted it. We do not ship what we cannot check.

So the seventh language costs **zero** model calls, the logo is byte-exact
rather than hallucinated, and QA for the text path is arithmetic instead of
inference.

Measured on the demo campaign: **2 image calls → 14 deliverables.**

## Quick start (no Azure needed)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python -m playwright install chromium
```

`MAI_MOCK` defaults to on, so the whole pipeline runs without a subscription
and the test suite never depends on a 2 RPM quota.

```bash
cd backend && ../.venv/Scripts/python -m pytest -q
```

Render one campaign in all seven locales and build a contact sheet — this is
the visual gate for the riskiest part of the product:

```bash
cd backend && ../.venv/Scripts/python scripts/language_proof.py
```

Run the API:

```bash
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --reload
```

```bash
curl -X POST localhost:8000/api/campaigns -H "content-type: application/json" -d "{\"brief\":\"a glass bottle of coconut oil on dark walnut, warm festive bokeh\",\"formats\":[\"portrait\",\"story\"],\"occasion\":\"Diwali\",\"occasion_by_locale\":{\"bn\":\"Durga Puja\"}}"
```

Then poll `/api/jobs/{id}` and download `/api/jobs/{id}/bundle`.

## Connecting real Foundry

`az` is not installed by default — install the Azure CLI first. MAI-Image-2.6
is available in **`southindia`** (and `uaenorth`), which suits an India-focused
product.

```bash
az login
az group create --name mcg-rg --location southindia

az cognitiveservices account create \
  --name mcg-foundry --resource-group mcg-rg \
  --kind AIServices --sku S0 --location southindia \
  --custom-domain mcg-foundry --assign-identity --allow-project-management true

az cognitiveservices account project create \
  --name mcg-foundry --resource-group mcg-rg \
  --project-name mcg-project --location southindia

az cognitiveservices account deployment create \
  --name mcg-foundry --resource-group mcg-rg \
  --deployment-name mai-image-26 \
  --model-name "MAI-Image-2.6" --model-format Microsoft \
  --model-version 2026-07-31 --sku-name GlobalStandard --sku-capacity 1
```

`--custom-domain` must be globally unique. Confirm what your subscription can
actually deploy with `az cognitiveservices account list-models` before trusting
the version string. Deploy `MAI-Image-2.6-Flash` too — it gets its own RPM
bucket, so routing drafts to it roughly doubles usable throughput.

Then:

```
MAI_MOCK=0
FOUNDRY_ENDPOINT=https://mcg-foundry.services.ai.azure.com
MAI_IMAGE_DEPLOYMENT=mai-image-26
MAI_RPM=2
```

Auth uses Entra ID via `DefaultAzureCredential` unless `FOUNDRY_API_KEY` is
set. Prefer Entra — there is then no key to leak or rotate.

> **MAI image models are public preview**: no SLA, and Microsoft's own docs say
> not recommended for production workloads. That is a business risk to accept
> explicitly. Every call is isolated in `app/foundry/image_client.py` so the
> blast radius of an API change stays small.

## Layout

```
backend/app/
  config.py                 env, MAI_MOCK, MAI_RPM
  main.py                   FastAPI; generation is always a job, never sync
  pipeline.py               one generation -> N language variants
  foundry/
    image_client.py         the ONLY place that calls MAI
    mock_client.py          deterministic placeholder, enforces the same limits
    ratelimit.py            token bucket + 429 backoff
  imaging/
    dimensions.py           the aspect table (see below)
    safezones.py            Instagram safe zones, as hard gates
    compose.py              crop, upscale, contrast-aware logo placement
    overlay.py              Chromium text renderer
  copy/
    languages.py            (language, script, register) + per-script metrics
    strategy.py             CreativeBrief -> English prompt (a pure function)
    transcreate.py          per-locale copy, authored not translated
```

## Things that are easy to get wrong

**Dimensions.** `width ≥ 768`, `height ≥ 768`, `width × height ≤ 1,048,576`.
Exact 9:16 computes to a 767px side and is **rejected**; 1.91:1 landscape needs
a 740px side and is **unreachable**. Both are generated at the nearest legal
aspect and cropped. All of this lives in `resolve_dimensions()` — scattered, it
will be got wrong.

| Format | Instagram | Generate at | Crop |
|---|---|---|---|
| portrait (feed default) | 1080×1350 | 912×1140 | no |
| grid (profile crop) | 1080×1440 | 885×1180 | no |
| story / reel | 1080×1920 | 768×1365 | yes |
| square | 1080×1080 | 1024×1024 | no |
| landscape | 1080×566 | 1365×768 | yes |

`width`/`height` are parameters of the **generations** endpoint only. The edits
endpoint takes no dimensions, so the reference is cover-cropped before upload
and the response cropped again after.

**Text rendering.** `PIL.features.check("raqm")` is `False` here, so Pillow
cannot shape Indic scripts — `ImageDraw.text` would silently emit unreordered,
disconnected glyphs. Text goes through Chromium, which brings HarfBuzz and ICU.
Pillow is used for pixels only.

Enforced in code, not left to templates: `letter-spacing: 0` for every non-Latin
script (tracking pulls shaped clusters apart and breaks conjuncts), no
synthesised bold, per-script line-height, and words are never broken — the
fitter shrinks type instead, because breaking an Indic word splits a consonant
from its matra.

**Safe zones.** Meta unified the 9:16 safe zone in March 2026: top 14%, bottom
35% for Reels, left/right 6% — a usable box of **950×979**. These raise
`SafeZoneViolation`, they do not warn. The logo is anchored inside the safe rect
rather than the canvas edge, so compliance is structural.

**The logo is never described to the model.** A diffusion model produces a
plausible logo, which is a wrong logo — and describing a mark in a prompt asks
the model to reproduce a trademark. Same for the product, maps of India, and the
flag: all composited from real assets, all listed in the prompt's prohibitions.

**Copy is transcreated, not translated.** Each language is authored from a
shared strategy, so the cultural referent can change and not just the words —
a Diwali line becomes a *Pujo* line for Bengali, which is the correct campaign
rather than the correct translation.

## Status

Phase 1 (vertical slice) is complete and tested: dimensions, safe zones, logo
compositing, the Chromium text pipeline in seven locales, the campaign
pipeline, the job API, and bundle export. 113 tests pass.

Not yet built: the live Foundry text model (copy comes from a stub), Azure AI
Content Safety, C2PA provenance, the OCR zero-text gate, category compliance
gates, and the React frontend.
