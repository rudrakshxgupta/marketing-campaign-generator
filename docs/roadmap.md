# Roadmap

29 issues: **11 built, 18 pending**. Built features are filed as *closed*
issues so the repo documents what exists alongside what is outstanding.

[All issues](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue) ·
[Pending only](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue+is%3Aopen) ·
[Built](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues?q=is%3Aissue+is%3Aclosed)

The backlog lives in [`.github/backlog.json`](../.github/backlog.json) with an
idempotent seeder. Add to the JSON and re-run rather than hand-editing on
GitHub:

```bash
.venv/Scripts/python .github/seed_issues.py --dry-run
```

---

## Phase 1 — vertical slice ✅ complete

| # | Feature |
|---|---|
| [#1](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/1) | Resolve Instagram sizes to legal MAI generation sizes |
| [#2](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/2) | Instagram safe zones as hard gates |
| [#3](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/3) | Composite the logo deterministically, never generate it |
| [#4](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/4) | Indic text via Chromium/HarfBuzz instead of Pillow |
| [#5](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/5) | Language as (language, script, register), Hinglish first-class |
| [#6](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/6) | Prompt built by a pure function, not an LLM |
| [#7](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/7) | MAI client with rate limiting and 429 backoff |
| [#8](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/8) | Mock backend so the pipeline builds without Azure |
| [#9](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/9) | Campaign pipeline: one generation, N variants |
| [#10](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/10) | Job-based API and campaign bundle export |
| [#11](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/11) | Transcreation copy layer (stub writer) |

121 tests pass. Image generation mocked by default.

---

## Phase 2 — live MAI

Turning a working prototype into something that talks to Azure.

| # | Feature | Notes |
|---|---|---|
| [#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29) | **Probe the open questions** | `blocked` — needs a deployment. **Start here** |
| [#19](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/19) | Reference-image path in the API | `priority: high` — half the stated input surface |
| [#18](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/18) | Persistent jobs, state machine, SSE | |
| [#28](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/28) | Brand kit CRUD, logo variants, colour extraction | |
| [#23](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/23) | Route drafts to MAI-Image-2.6-Flash | Depends on #29 |
| [#13](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/13) | OCR gate: assert the base contains no text | `priority: high` |

### Why #29 comes first

`MaiImageClient` is written and unit-tested but has **never touched the real
endpoint**. Six questions cannot be answered from documentation:

- Real generation latency for 2.6 vs 2.6-Flash — the entire interactive UX
  budget depends on it, and the docs give no figures
- Whether MAI output carries C2PA credentials (documented for Azure OpenAI
  image models, silent for MAI)
- Whether a custom content-filter configuration can attach to a
  `--model-format Microsoft` deployment
- Whether separate draft/final deployments get independent RPM buckets —
  **#23 rests entirely on this**
- That edits output geometry follows the input, which the mock assumes
- Azure Translator `transliterate` coverage for `ta` and `te`

**Also on day one: file the quota-increase request.** Priority goes to accounts
already using their allocation, so the clock starts when you begin generating,
not when you ask.

---

## Phase 3 — languages

Making the multilingual claim real rather than stubbed.

| # | Feature | Notes |
|---|---|---|
| [#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12) | Wire the live Foundry chat model for transcreation | `priority: high` — copy is currently canned |
| [#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20) | Bundle Noto fonts, pin versions, golden shaping tests in CI | `priority: high` |
| [#21](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/21) | Native-speaker review workflow | |

#20 is higher priority than it sounds: rendering currently falls back to
Windows' Nirmala UI, which is **not redistributable** and will not exist in a
Linux container. Until fonts are bundled and pinned, output is not reproducible
across machines.

---

## Phase 4 — compliance and formats

| # | Feature | Notes |
|---|---|---|
| [#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15) | C2PA provenance and AI-disclosure labelling | `priority: high` — **law since 20 Feb 2026** |
| [#14](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/14) | Content Safety at every checkpoint | `priority: high` |
| [#16](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/16) | Category compliance gates | |
| [#17](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/17) | Hard blocks: maps, flags, deities, public figures | |
| [#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22) | React frontend with phone-frame preview | |
| [#25](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/25) | Azure infrastructure as code | |
| [#24](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/24) | Economy mode: one 9:16 master, crop down | |
| [#26](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/26) | Festival calendar presets | |
| [#27](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/27) | Carousel and WhatsApp Status formats | |

---

## Suggested order

1. **[#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29)** — deploy and probe. Everything downstream is guesswork until the live endpoint has been touched, and the latency answer may change the UX design.
2. **[#12](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/12) + [#20](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/20)** — live transcreation and bundled fonts. Together these turn the multilingual story from demonstrated to real, and it is the actual differentiator.
3. **[#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15)** — AI labelling is a legal requirement, not a feature. Cheap to add now, awkward to retrofit.
4. **[#19](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/19)** — the reference-image path is half the product's stated input surface and currently unreachable from the API.
5. **[#22](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/22)** — a frontend, once the pipeline behind it is real.

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
