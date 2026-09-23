# BrandlyAI — Social Media Content Generation in Multiple Languages

A marketer types **one line** — what they sell. They get back Instagram-ready
campaign creatives in **seven Indian languages**, on Microsoft AI Foundry.

Everything else — what the photograph shows, the proposition, the occasion, the
copy in each language — is worked out from that one line.

```
"cold-pressed coconut oil in a 500ml glass bottle"
        │
        ├─ 1 image call ──► one text-free photograph
        └─ 7 languages  ──► typeset and composited on top, at no extra cost
```

**Measured in the recorded demo: 1 image call → 7 finished deliverables.**

![Seven locales from a single generation](docs/images/language-proof.png)

*All seven locales from one image generation. Each auto-fitted to its own
script's metrics, all inside the Instagram safe zone.*

---

## The one idea

Generate **one text-free image**. Composite the logo and every language's text
deterministically on top.

Three verified facts force this:

1. **The image models support English only.** Indic scripts need complex
   shaping — reordering, conjuncts, matras — and diffusion models garble them
   in a uniquely dangerous way: the output looks plausible to an
   English-speaking operator and reads as illiterate to the audience.
2. **Generation is rate-limited and billed per call.** Seven languages across
   four formats as separate calls is neither affordable nor fast.
3. **Azure OCR cannot read 8 of the Indic scripts**, Bengali, Tamil and Telugu
   among them. For most of our languages we could not verify model-drawn text
   even if we wanted to. We do not ship what we cannot check.

So the seventh language costs **zero** model calls, the logo is byte-exact
rather than hallucinated, and QA for the text path becomes arithmetic instead
of inference.

---

## Running the prototype

Two processes. **With no Azure credentials the whole pipeline still runs** —
brief, typesetting, compositing, review, export — against placeholder imagery,
so this can be evaluated without a subscription.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python -m playwright install chromium
```

On Windows, `start.cmd` launches both servers in their own windows and opens
the browser. Or by hand:

Backend, from `backend/`:

```bash
../.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Frontend, from `frontend/`:

```bash
npm install && npm run dev
```

Open <http://localhost:5173>.

> Both are development servers and live only as long as their window. If the
> interface loads but nothing responds, the API window has been closed: the
> page is served by Vite and still appears, while every call behind it fails.

Run the tests (269, about 100 seconds):

```bash
.venv/Scripts/python -m pytest backend/tests -q
```

### Going live

Copy `.env.example` to `.env` and fill in your Foundry endpoint. `MAI_MOCK=0`
switches to real generation; `IMAGE_BACKEND=flux` selects FLUX.2-pro. See
[docs/setup.md](docs/setup.md).

---

## How it works

| Stage | What happens | Costs an image? |
|-------|--------------|-----------------|
| **Brief compiler** | A structured model call turns one line into a photograph description, a proposition, an occasion, and the region of the frame to keep clear | no |
| **Prompt renderer** | A *pure function* — not a model — builds the English prompt, so the no-text and reserved-space clauses can never be dropped | no |
| **Image generation** | One call. FLUX.2-pro, or MAI-Image when quota allows | **yes, once** |
| **Copy writer** | Every language authored from a shared strategy in one call | no |
| **Typesetter** | Headless Chromium (HarfBuzz + ICU) renders each language to a transparent PNG | no |
| **Compositor** | Base + text + logo, with safe zones and contrast enforced | no |

The only rate-limited step is one call per format. Everything after it is CPU
work that scales freely — which is what makes the seventh language free and the
seventh *format* cost a generation.

---

## What makes it more than a wrapper

**Transcreation, not translation.** Copy is authored in each language from a
language-neutral strategy. So the referent changes and not just the words:
Diwali for Hindi, **Durga Puja** for Bengali, **Pongal** for Tamil — from one
campaign. Translating finished English copy gets the words right and the
campaign wrong.

**Subject preservation.** Upload a photo of a real building and it must not be
reinvented: a building that gains a storey renders beautifully and advertises a
property that does not exist. "Use this exact image" keeps the subject
pixel-accurate and restages only the scene around it, then *measures* the
result against the original — outline overlap and detail density, with
architecture held to a stricter floor than food. Drift is flagged before
anyone publishes. See [docs/fidelity.md](docs/fidelity.md).

**The logo is never drawn.** A generated logo is a *wrong* logo. It is
composited from the brand's real file, sized to the mark rather than to the
file's transparent canvas, with the light or dark version chosen by measuring
the pixels behind it. No logo file? The brand name is typeset as a wordmark.

**Layout follows the picture.** The brief names a region to keep clear, and the
frame that comes back is then measured — edge energy and tonal spread across
eight candidate regions — so the copy lands where the image is actually calm,
and every creative in a batch does not share one layout.

**Spend is visible before it is spent.** The cost of each action is shown on
the button. A hard ceiling is enforced *before* the call and persists to disk.
Repeat prompts are served from a cache without touching the budget.

**Review is free.** The base image carries no text, so correcting copy is a
typesetting pass, not a regeneration — the creative updates as you type. That
matters most for Bengali, Tamil and Telugu, where OCR cannot verify the
rendering and a human reading it is the only check that exists.

---

## Layout

```
backend/app/
  main.py              FastAPI surface, job state, uploads
  pipeline.py          brief → image → N languages → export
  config.py            settings, .env loading, mock mode
  copy/                strategy, brief compiler, transcreation, blocklist
  foundry/             MAI + FLUX clients, spend guard, cache, mock
  imaging/             dimensions, safe zones, compositing, fidelity,
                       Chromium typesetter, typeface selection
backend/tests/         269 tests
frontend/src/          React UI with Instagram phone-frame preview
docs/                  architecture, constraints, API, typography, decisions
deliverables/          submission PDF
```

Full module-by-module map: [docs/code-map.md](docs/code-map.md).

---

## Status

**269 tests pass.** Running live on Microsoft AI Foundry: **FLUX.2-pro** for
imagery, **GPT-5-mini** for the brief and the copy, headless Chromium for
typesetting.

Built: the full text-to-campaign path, both reference-image modes, subject
fidelity measurement, seven locales with correct Indic shaping, five Instagram
formats, safe-zone enforcement, logo and wordmark compositing, the spend
guard and cache, the human review workflow, live re-typesetting, and the React
frontend.

Not built, and honest about it: Azure AI Content Safety at the checkpoints,
C2PA provenance and AI-disclosure labelling, the OCR zero-text gate, category
compliance gates for Indian advertising, bundled Noto fonts with golden
shaping tests, and a persistent job store — jobs currently live in memory and
do not survive a restart. These are tracked as issues and discussed in
[docs/roadmap.md](docs/roadmap.md).

### A note on the image model

The project was designed for **MAI-Image-2.6**. Azure's own errors established
that every Microsoft image model has a quota limit of **0** on this Azure for
Students subscription, in every region tried. FLUX.2-pro was the model with
quota, so the pipeline runs on it — both clients implement the same interface
and the rest of the system never learns which one is in use. That indirection
was in the design before it was needed, and is the reason switching cost one
configuration line. See [docs/decisions.md](docs/decisions.md).

---

## Documentation

📖 **[docs/](docs/)** — architecture, constraints, API reference, typography,
compliance, decisions, roadmap
📋 **[PROJECT_REPORT.md](PROJECT_REPORT.md)** — full status report
🔨 **[HOW_IT_WAS_BUILT.md](HOW_IT_WAS_BUILT.md)** — the making of it
🎬 **[docs/video-script.md](docs/video-script.md)** — narration for the demo video
