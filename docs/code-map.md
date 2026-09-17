# Code map

```
backend/app/
  config.py                 settings; MAI_MOCK defaults on
  main.py                   FastAPI; generation is always a job
  pipeline.py               one generation -> N language variants
  foundry/
    image_client.py         the ONLY place that calls MAI
    mock_client.py          deterministic placeholder, same limits enforced
    ratelimit.py            token bucket + full-jitter backoff
  imaging/
    dimensions.py           the aspect table; owns all the size arithmetic
    safezones.py            Instagram safe zones, as hard gates
    compose.py              crop, upscale, contrast-aware logo placement
    overlay.py              Chromium text renderer
  copy/
    languages.py            (language, script, register) + per-script metrics
    strategy.py             CreativeBrief -> English prompt (pure function)
    transcreate.py          per-locale copy, authored not translated
  scripts/
    language_proof.py       renders all locales; the visual gate
```

---

## `imaging/dimensions.py`

Owns every piece of size arithmetic. Scattered across call sites it *will* be
got wrong — the rules are non-obvious and the failure is a 400 that costs a
request from a 2–12 RPM budget.

| Symbol | Purpose |
|---|---|
| `MIN_SIDE`, `MAX_PIXELS`, `MAX_SIDE` | 768, 1,048,576, 1365 |
| `MIN_ASPECT`, `MAX_ASPECT` | 0.5626 – 1.7773, the generable band |
| `is_legal(w, h)` | Exactly what MAI would accept |
| `max_legal_dimensions(aspect)` | Largest legal pair closest to an in-band aspect |
| `resolve_dimensions(w, h)` | Plans a generation; clamps out-of-band targets and flags the crop |
| `GenerationPlan.crop_box()` | Centre-crop box; full frame when no crop is needed, so callers apply it unconditionally |
| `FORMATS`, `plan_for(key)` | The five named Instagram formats |

The search walks heights downward from the theoretical maximum, keeping the
smallest ratio error and breaking ties toward more pixels. Exact ratios win
where they exist — portrait resolves to 912×1140 (exactly 4:5), grid to
885×1180 (exactly 3:4).

## `imaging/safezones.py`

`safe_rect(w, h, format)` returns the usable region; `assert_within()` raises
`SafeZoneViolation`. Violations raise rather than warn — a mark under the Reels
caption tray is invisible, and warnings get ignored at volume.

`grid_bleed_band()` returns the 4:5 region of a 3:4 master that survives as the
feed post.

## `imaging/compose.py`

`finalize_base()` crops then upscales (never the reverse — don't scale pixels
you are about to discard).

`composite_logo()` is the interesting one. `derive_variants()` builds light and
dark knockouts preserving the alpha mask; `logo_box()` anchors **inside the
safe rect** rather than the canvas edge; `choose_variant()` samples the
destination patch in linear light and picks by WCAG contrast, escalating to a
scrim if neither knockout clears 4.5:1.

`relative_luminance` / `contrast_ratio` implement WCAG properly, including the
sRGB→linear transfer function. Approximating it here produces marks that look
fine on the developer's monitor and fail on a phone in daylight.

## `imaging/overlay.py`

The Chromium renderer. See [typography.md](typography.md) for the full story.

`render()` returns `(transparent PNG, FitReport)`. `FitReport.needs_review` is
true when type fell below the script minimum or a word had to be broken.

`measure()` exists for testing: it returns the advance width of a string, which
is how the suite proves the shaping engine is actually running rather than
merely producing ink.

One browser is reused across renders — launch is hundreds of milliseconds, each
overlay is tens.

## `foundry/image_client.py`

The only module that talks to MAI, because these models are public preview and
the API surface may change.

`ImageBackend` is the protocol both the real client and the mock implement, so
every downstream stage is testable without a subscription.

Dimensions are pre-checked locally — a bad size fails here rather than costing
a request. `RETRYABLE_STATUSES` deliberately excludes 400/401/404: those are
our own bugs and retrying them burns quota. `_explain()` turns each into an
actionable message rather than echoing the body.

## `foundry/ratelimit.py`

`TokenBucket` with `acquire()` and `drain()`. Draining after a 429 is the
non-obvious part: the service has told us our accounting is optimistic, so we
surrender the burst allowance instead of spending it on further rejected
requests.

`backoff_delay()` is full-jitter exponential, honouring `Retry-After` as a
floor — the service knows its own reset window better than our guess does.

## `copy/languages.py`

`Locale` is the `(language, script, register)` triple; `ScriptMetrics` carries
line height, minimum legible size, the letter-spacing lock, and `length_factor`.

`char_budget(base_chars, locale)` scales an English budget for a target locale.
Copy authored to an English budget overflows in Tamil routinely; scaling at
authoring time is far cheaper than shrinking type at render time.

## `copy/strategy.py`

`CreativeBrief` describes the picture; `render_prompt()` turns it into an
English MAI prompt **as a pure function**, so the no-text clause and the
negative-space clause cannot be dropped by a model having a creative moment.

`NO_TEXT_CLAUSE` is exhaustive by necessity — "no text" alone still produces
signage and watermarks. `GLOBAL_PROHIBITIONS` covers maps, flags, public
figures and third-party marks, which are legal rather than aesthetic concerns.

`render_edit_prompt()` handles the image-to-image path, where the
product-fidelity clause is load-bearing: MAI's edit mode explicitly supports
*text updates*, so it will happily re-letter packaging it is shown.

## `copy/transcreate.py`

`CreativeStrategy` → `CopyPack`, one `LocaleCopy` per locale.

`build_prompt()` is the full transcreation instruction for a Foundry chat
model: strategy, brand voice, per-locale occasion, grapheme-cluster character
budgets, and the rule that numerals and words like *sale* / *offer* stay in
Latin even inside Indic copy.

`StubCopyWriter` returns realistic canned copy so the pipeline is exercised
honestly before the live model exists
([#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12)).
Its Bengali sample is written to **Pujo**, not Diwali — the substitution a
translator would never make.

Every locale carries a `back_translation` and starts `needs_review=True`.

## `pipeline.py`

`CampaignPipeline.run()` is the whole architectural argument in one function:
copy first (costs no quota), then **one** generation per format, then every
locale composited from that single base.

`TEXT_BOXES` and `LOGO_ANCHORS` are per-format because the story canvas puts
its UI at the bottom, so the mark goes high-left.

`export_bundle()` produces the ZIP, including `REVIEW.txt`.

## `main.py`

FastAPI. Jobs live in a module-level dict — honest for a single-user local
prototype, replaced by a persisted state machine in
[#18](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/18).

The renderer and image backend are created once in `lifespan` and shared; the
pipeline borrows them rather than owning them, so `aclose()` does not shut down
a browser other jobs are using.
