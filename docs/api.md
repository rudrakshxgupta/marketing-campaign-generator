# API reference

Base URL in development: `http://localhost:8000`

```bash
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --reload
```

Interactive docs at `/docs` (FastAPI generates them from the Pydantic models).

**Generation is never synchronous.** MAI allows 2–12 requests per minute and a
render takes tens of seconds, so `POST /api/campaigns` returns `202` with a job
id and the client polls.

---

## `GET /healthz`

```json
{ "ok": true, "mock": true, "image_deployment": "mai-image-26", "mai_rpm": 2.0 }
```

`mock: true` means no Foundry calls are being made. Check this before wondering
why the images look like placeholders.

---

## `GET /api/meta`

Formats and locales, including the generation size each format requires. **The
frontend should read dimensions from here** rather than duplicating the rules
client-side — that is the whole reason the endpoint exists.

```json
{
  "formats": [
    { "key": "portrait",  "target": [1080,1350], "generate": [912,1140],  "requires_crop": false },
    { "key": "grid",      "target": [1080,1440], "generate": [885,1180],  "requires_crop": false },
    { "key": "story",     "target": [1080,1920], "generate": [768,1365],  "requires_crop": true  },
    { "key": "square",    "target": [1080,1080], "generate": [1024,1024], "requires_crop": false },
    { "key": "landscape", "target": [1080,566],  "generate": [1365,768],  "requires_crop": true  }
  ],
  "locales": [
    { "key": "en",      "label": "English",  "native": "English", "language": "en", "script": "Latn", "register": "neutral" },
    { "key": "hi",      "label": "Hindi",    "native": "हिन्दी",   "language": "hi", "script": "Deva", "register": "neutral" },
    { "key": "hi-Latn", "label": "Hinglish", "native": "Hinglish","language": "hi", "script": "Latn", "register": "conversational" }
  ]
}
```

Note that `hi` and `hi-Latn` are the same *language* in different scripts. The
UI should present Hinglish as a one-click preset, not bury it in a settings
panel — see [architecture.md](architecture.md#languages-as-a-triple).

---

## `POST /api/campaigns` → `202`

```json
{
  "brief": "a clear glass bottle of cold-pressed coconut oil on dark walnut, warm bokeh behind",
  "proposition": "Purity you can taste",
  "benefit": "Cold-pressed, nothing added",
  "brand_name": "ACME",
  "occasion": "Diwali",
  "occasion_by_locale": { "bn": "Durga Puja" },
  "mandatory_line": "T&C apply.",
  "formats": ["portrait", "story"],
  "locales": ["en", "hi", "hi-Latn", "mr", "bn", "ta", "te"],
  "facts": ["30% off", "free delivery over 999"]
}
```

| Field | Default | Notes |
|---|---|---|
| `brief` | *required* | What the **picture** shows. Not the caption. |
| `proposition`, `benefit` | sample text | Drive the copy, not the image |
| `occasion` | `""` | Campaign occasion |
| `occasion_by_locale` | `{}` | **Per-locale override.** A Bengali audience's gifting peak is Durga Puja, not Diwali — this is how the referent changes rather than just the words |
| `formats` | `["portrait"]` | One model call each, or **one total** with `economy` |
| `economy` | `false` | Generate one tall master and crop every other format out of it. Trades sharpness for calls |
| `reference_id` | `null` | From `POST /api/uploads/reference` |
| `reference_mode` | `"inspiration"` | `inspiration` matches the look; `edit` keeps the actual photo |
| `rights_confirmed` | `false` | **Required for `edit`** — that mode reproduces the upload pixel-for-pixel |
| `locales` | all 7 | **Free** — composited from the same base |
| `facts` | `[]` | The only claims copy may state. Nothing outside this list may be invented |

Response:

```json
{
  "job_id": "a1b2c3d4e5f6",
  "state": "queued",
  "image_calls_expected": 2,
  "deliverables_expected": 14,
  "poll": "/api/jobs/a1b2c3d4e5f6"
}
```

`image_calls_expected` is `len(formats)` and `deliverables_expected` is
`formats × locales`. The gap between those two numbers is the architecture.

Errors: `400` for an unknown format or locale (listing what it did not
recognise), for `edit` mode without a `reference_id`, and for `edit` mode
without `rights_confirmed`.

---

## `POST /api/uploads/reference`

`multipart/form-data`, field `file`. Returns the style read from the image —
**measured locally, no model call, no quota** — so the user can see what
"inspiration" will carry across before spending anything.

```json
{
  "reference_id": "9f8e7d6c5b4a",
  "orientation": "portrait",
  "style": {
    "palette": ["deep espresso brown", "warm antique gold", "olive"],
    "palette_hex": ["#302014", "#C49844", "#807A3E"],
    "brightness": "dark", "contrast": "medium",
    "saturation": "vivid", "temperature": "warm",
    "lighting": "moody low-key light with soft shadows",
    "mood": ["moody", "warm", "bold"]
  }
}
```

The palette reaches the prompt as **colour words, not hex** — diffusion models
respond to "deep saffron" far better than to `#FF6B00`.

### The two modes

**`inspiration`** (default) folds the measured style into the brief and runs
the ordinary text path. Output still obeys our layout, aspect ratio and safe
zones. The subject stays whatever the user asked for, and the reference's own
silhouette, marks and faces go into `must_not_depict` — that separation is the
line between "in the style of" and a copy.

**`edit`** sends the upload to `/mai/v1/images/edits` directly. It reproduces
the image pixel-for-pixel, so it requires `rights_confirmed: true`. Defaulting
to it would quietly turn whatever a user uploaded into a derivative work.

---

## `GET /api/usage`

```json
{
  "mock": false,
  "today": {"used": 3, "limit": 25, "remaining": 22},
  "total": {"used": 3, "limit": 200, "remaining": 197},
  "cache": {"enabled": true, "hits": 4, "misses": 3, "calls_saved": 4}
}
```

Worth checking before a big run. In a campaign response,
`deliverables_expected` tells you the output and `image_calls_expected` tells
you the cost.

---

## `GET /api/jobs/{id}`

```json
{
  "job_id": "a1b2c3d4e5f6",
  "state": "complete",
  "progress": 100,
  "error": null,
  "image_calls": 2,
  "prompt": "commercial product photography. …There is no text, no lettering…",
  "variants": [
    {
      "locale": "ta",
      "format": "portrait",
      "file": "portrait_ta.png",
      "headline_px": 79,
      "headline_used": "தூய்மையின் திருவிழா",
      "logo_variant": "light",
      "needs_review": true,
      "review_reasons": ["machine-written copy has not been reviewed"]
    }
  ],
  "review_count": 14,
  "bundle": "/api/jobs/a1b2c3d4e5f6/bundle",
  "logs": ["one generation per format (2), 7 locales composited from each"]
}
```

States: `queued`, `generating`, `compositing`, `complete`, `failed`,
`rate_limited`, `budget_exceeded`.

The last two are distinct from `failed` on purpose. `rate_limited` means the
deployment is at its RPM ceiling and retrying later will work.
`budget_exceeded` means we refused *before* billing — nothing is broken, and no
credit was spent.

`headline_used` may differ from the requested headline: the auto-fitter selects
a shorter alternate rather than shrinking type below the legible minimum for
the script.

### `review_reasons`

Every variant carries them. Possible values:

- `machine-written copy has not been reviewed` — always set until a human
  approves. For Bengali, Tamil and Telugu this flag is the **only** gate
  between a model and a published asset, because Azure OCR cannot verify those
  scripts.
- `a word was broken mid-cluster` — for Indic this splits a consonant from its
  matra. Always a defect.
- `type fell below the legible minimum for this script`
- `logo placement rejected: …` — a safe-zone violation

---

## `GET /api/jobs/{id}/bundle`

A ZIP. **The deliverable is a campaign bundle, not an image** — a marketer
needs the creative *plus* everything required to actually post it.

```
images/portrait_en.png, portrait_hi.png, story_ta.png, …
copy/en.txt, hi.txt, ta.txt, …    headline, subhead, CTA, caption,
                                   hashtags, alt text, English back-translation
REVIEW.txt                         everything not ready to publish, with reasons
```

Each `copy/*.txt` carries the AI-disclosure line in that language. Platforms
strip metadata on upload, so the caption is the only disclosure channel that
reliably reaches a viewer — see [compliance.md](compliance.md).

---

## `POST /api/uploads/logo`

`multipart/form-data`, field `file`.

```json
{ "logo_id": "9f8e7d6c5b4a", "width": 420, "height": 120 }
```

Re-encoded to PNG on the way in, which normalises the format and strips EXIF or
any embedded payload from a file we did not create. Transparency is preserved —
the light and dark knockout variants are derived from the alpha mask.

> Current limitation: uploads are not associated with a brand, and the pipeline
> picks the first logo it finds. Tracked as
> [#28](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/28).

---

## Configuration

Copy `.env.example` to `.env`.

| Variable | Default | Notes |
|---|---|---|
| `MAI_MOCK` | `1` | **On by default.** No Foundry calls, deterministic placeholders |
| `FOUNDRY_ENDPOINT` | — | `https://<resource>.services.ai.azure.com`, no trailing slash. Required when mock is off |
| `FOUNDRY_API_KEY` | — | Leave unset to use Entra ID via `DefaultAzureCredential` |
| `MAI_IMAGE_DEPLOYMENT` | `mai-image-26` | Deployment name, **not** model name |
| `MAI_DRAFT_DEPLOYMENT` | `mai-image-26-flash` | Separate deployment → separate RPM bucket |
| `MAI_RPM` | `2` | Token bucket rate. Match your tier |
| `MAI_CACHE` | `1` | Serve byte-identical repeats from disk |
| `MAI_DAILY_LIMIT` | `25` | Hard ceiling, enforced before the call |
| `MAI_TOTAL_LIMIT` | `200` | Hard lifetime ceiling |
| `MAI_MAX_ATTEMPTS` | `5` | Retries on 429/5xx |
| `MAI_TIMEOUT_S` | `180` | Per-request |
| `STORAGE_ROOT` | `./storage` | Uploads, bases, renders, exports |

Prefer Entra ID over an API key in production — there is then no key to leak or
rotate.

Setting `MAI_MOCK=0` without `FOUNDRY_ENDPOINT` raises at startup rather than
failing on the first generation.
