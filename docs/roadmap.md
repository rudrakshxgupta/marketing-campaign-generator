# Roadmap

36 issues: **18 built, 18 pending**. Built features are filed as *closed*
issues so the repo documents what exists alongside what is outstanding.

[All issues](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue) ·
[Pending only](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue+is%3Aopen) ·
[Built](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue+is%3Aclosed)

The backlog lives in [`.github/backlog.json`](../.github/backlog.json) with an
idempotent seeder. Add to the JSON and re-run rather than hand-editing on
GitHub — it skips anything that already exists.

```bash
.venv/Scripts/python .github/seed_issues.py
```

---

## Phase 1 — vertical slice ✅ complete

Brief → composited deliverable, mock image backend. 11 issues, all closed:
the dimension resolver, safe zones as hard gates, contrast-aware logo
compositing, the Chromium/HarfBuzz text renderer, the language triple, the
pure-function prompt builder, the MAI client, the mock backend, the campaign
pipeline, the job API, and the transcreation layer.

## Phase 2 — live MAI · 6 of 11 done

Everything that can be built without Azure is built. What remains needs a
deployment.

**Done**

| # | Feature |
|---|---|
| [#30](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/30) | Image cache — a repeat request is never paid for twice |
| [#31](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/31) | Hard spend ceiling, enforced before the call and persisted |
| [#32](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/32) | Foundry provisioning and probe tooling |
| [#19](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/19) | Reference-image path, both sub-modes |
| [#33](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/33) | Local style extraction, no model call |
| [#34](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/34) | Drain in-flight jobs on shutdown |

**Pending**

| # | Feature | Notes |
|---|---|---|
| [#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29) | **Probe against a live deployment** | `blocked` — needs Azure. **Start here** |
| [#13](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/13) | OCR gate: assert the base contains no text | `priority: high`, needs Azure Vision |
| [#18](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/18) | Persistent job store, state machine, SSE | local work |
| [#28](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/28) | Brand kit CRUD, logo variants, colour extraction | local work |
| [#23](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/23) | Route drafts to MAI-Image-2.6-Flash | depends on #29 |

### Why #29 comes first

`MaiImageClient` is written and unit-tested but has **never touched the real
endpoint**. Six questions cannot be answered from documentation:

- Real generation latency for 2.6 vs Flash — the entire interactive UX budget
  depends on it, and the docs give no figures
- Whether MAI output carries C2PA credentials (documented for Azure OpenAI
  image models, silent for MAI)
- Whether a custom content-filter configuration can attach to a
  `--model-format Microsoft` deployment
- Whether separate deployments get independent RPM buckets — **#23 rests
  entirely on this**
- That edits output geometry follows the input, which the mock assumes
- Azure Translator `transliterate` coverage for `ta` and `te`

The probe costs **3 image calls** and answers all of it in one run. The
expensive mistake is not three credits — it is building a frontend and a
compliance layer on assumptions that turn out wrong.

**Also on day one: file the quota-increase request.** Priority goes to accounts
already using their allocation, so the clock starts when you begin generating.

## Phase 3 — languages · 0 of 4 done

| # | Feature | Notes |
|---|---|---|
| [#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20) | Bundle Noto fonts, pin versions, golden shaping tests | `priority: high` |
| [#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12) | Live Foundry chat model for transcreation | `priority: high` — copy is canned |
| [#21](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/21) | Native-speaker review workflow | |
| [#35](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/35) | Vision-model style extraction upgrade | optional |

**[#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20)
is the highest-value remaining offline work.** Rendering currently falls back to
Windows' Nirmala UI, which is **not redistributable** and will not exist in a
Linux container. Until fonts are bundled and pinned, output is not reproducible
anywhere but the machine it was built on.

## Phase 4 — compliance and formats · 1 of 10 done

**Done**: [#24](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/24)
economy mode — 4 formats for 1 image call, promoted early because the credit
constraint changed its value.

| # | Feature | Notes |
|---|---|---|
| [#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15) | C2PA provenance and AI-disclosure labelling | `priority: high` — **law since 20 Feb 2026** |
| [#14](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/14) | Content Safety at every checkpoint | `priority: high` |
| [#16](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/16) | Category compliance gates | |
| [#17](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/17) | Hard blocks: maps, flags, deities, public figures | |
| [#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22) | React frontend with phone-frame preview | |
| [#25](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/25) | Azure infrastructure as code | |
| [#26](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/26) | Festival calendar presets | |
| [#27](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/27) | Carousel and WhatsApp Status formats | |
| [#36](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/36) | Pre-select the reference mode from phrasing | |

---

## Suggested order

1. **[#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29)** — the probe. 3 images, ~2 minutes, and everything downstream stops being guesswork.
2. **[#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20)** — bundle the fonts. Needs nothing from Azure and fixes a licensing problem as well as a reproducibility one.
3. **[#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12)** — live transcreation. With #20 this turns the multilingual story from demonstrated to real, and it is the actual differentiator.
4. **[#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15)** — AI labelling is a legal requirement. Cheap now, awkward to retrofit.
5. **[#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22)** — a frontend, once the pipeline behind it is real.

---

## Spend controls, and why they came first

The credit constraint reordered the roadmap. Three things were pulled forward
so that a first Azure session is bounded rather than open-ended:

- **[#31](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/31)** — 25/day and 200 total, enforced before the call, persisted to disk. A retry loop cannot drain anything. Failed calls are not charged.
- **[#30](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/30)** — identical requests come from disk. Re-clicking Generate is free.
- **[#24](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/24)** — economy mode. Measured: 4 formats went from 4 calls to 1.

`GET /api/usage` reports spend, headroom, and calls saved.

---

## Deliberately out of scope

Named so they stay decisions rather than drift:

- **Video and Reels generation.** Different model, different cost, different
  failure modes. It would eat the entire roadmap.
- **A general-purpose editor.** Loses to Canva, and dilutes the brand-lock
  value proposition.
- **All 22 scheduled languages.** Roughly four times the typography and QA cost
  for a small fraction of reach.
- **Product image generation.** Composite the seller's real product or do not
  ship the feature.
- **Auto-publishing without human review.** A brand-safety and regulatory
  hazard, and for the scripts with no OCR coverage there is no safety net at
  all.
