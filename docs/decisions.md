# Decision records

What was chosen, why, and — where it matters — what it cost. Recorded because
several of these look arbitrary from the outside and are expensive to
rediscover.

---

## ADR-1 — One text-free generation, N composited variants

**Decision.** MAI generates a single image per format containing no text and no
logo. Every language variant, the logo, and all copy are composited
deterministically afterwards.

**Why.** Three independently verified facts, any one of which would justify it:
MAI declares `Languages: en`; the model allows 2–12 RPM; Azure OCR cannot
extract 8 of the Indic scripts, so model-drawn text in most of our languages
could not be verified even in principle.

**Cost.** No *diegetic* text — words physically inside the scene, on a shop
sign or packaging. The overlay sits on top of the image and cannot put Tamil on
a curved awning. This is the sharpest limitation of the design and belongs in
the FAQ.

**Status.** Implemented. Measured 2 image calls → 14 deliverables.

---

## ADR-2 — Headless Chromium for text, not Pillow

**Decision.** Text overlays are rendered as HTML/CSS by headless Chromium and
screenshotted with a transparent background. Pillow handles pixels only.

**Why.** `PIL.features.check("raqm")` is `False` here, so `ImageDraw.text()`
would silently emit unreordered, disconnected glyphs. The alternative —
`uharfbuzz` + `freetype-py` — means re-implementing line breaking and bidi, and
we would get Malayalam wrong. Chromium already carries HarfBuzz and ICU and is
continuously tested against these scripts by a browser vendor.

**Cost.** A ~150MB Chromium dependency, and a browser process to manage.
Accepted: it is the only path to correct Indic shaping without hand-rolling a
shaping stack.

**Bonus.** The overlay is HTML, so the frontend can preview with the *same*
template. One renderer to keep correct rather than two that drift.

---

## ADR-3 — The logo is composited, never generated

**Decision.** The brand mark is never described to MAI, never passed through an
edit pass. It is composited from the brand's real file.

**Why.** A diffusion model produces a *plausible* logo, which is a wrong logo —
subtly wrong letterforms that still ship, which is worse than an obviously
wrong one. And describing a mark in a prompt is asking the model to reproduce a
trademark, one of the risks Microsoft's own responsible-AI notes name.

**Consequence.** Placement is *verified*, not searched — the brief already
declares the reserved region, so the compositor measures rather than hunts.
Variant selection is contrast-driven in linear light, escalating to a scrim
rather than a drop shadow, because most brand guidelines forbid effects on the
mark.

**Same reasoning applies to** the product (composite the seller's real photo),
maps of India, and the national flag.

---

## ADR-4 — Prompt construction is a pure function, not an LLM

**Decision.** An LLM produces a structured `CreativeBrief`. A pure function
turns it into prompt text.

**Why.** The no-text clause and the negative-space clause are load-bearing: if
either is dropped, the model draws its own text into the area reserved for the
overlay. A model asked to "write a good prompt" will eventually drop them.

**Also buys.** Diffable prompt-template versions, reproducible renders from a
stored brief, and a place to assert that no third-party brand token survived
into the string.

---

## ADR-5 — All size arithmetic in one module

**Decision.** `imaging/dimensions.py` owns every size calculation. Nothing else
computes a width or height.

**Why.** The rules are non-obvious and interact: exact 9:16 computes to a 767px
side and is rejected; 1.91:1 is unreachable at any size. The failure mode is a
400 that costs a request from a 2–12 RPM budget. Scattered across call sites,
this will be got wrong.

**Enforced by.** Tests that assert against the raw constraints rather than
hard-coded numbers, so the resolver cannot drift.

---

## ADR-6 — Safe zones raise, they do not warn

**Decision.** Content placed outside the Instagram safe rect raises
`SafeZoneViolation`.

**Why.** Content under the Reels caption tray is not "slightly cropped", it is
invisible. And warnings get ignored at volume — the whole point of an automated
pipeline is that nobody is inspecting each asset.

**Consequence.** Logo placement is derived from the safe rect rather than the
canvas edge, making compliance structural. The gate fired on its first real use
and caught exactly this.

---

## ADR-7 — Language is a triple, and Hinglish is first-class

**Decision.** `(language, script, register)`, not a single dropdown. Hinglish
is `hi` + `Latn` + `conversational`.

**Why.** A large share of real Indian Instagram marketing copy is Romanised
Hindi rather than Devanagari — many people speak Hindi fluently but read
Devanagari slowly, and Devanagari reads formal or dated for a fashion brand.
Romanised output is likely the second-highest-volume variant.

**Consequence.** Both script variants are generated side by side and the
marketer picks. Text is cheap; only images are rate-limited.

---

## ADR-8 — Copy is transcreated, never translated

**Decision.** Each locale is authored from a shared `CreativeStrategy`. English
output is never used as a source text.

**Why.** Translating finished copy produces text that is accurate and dead —
and carries the wrong cultural referent. A Diwali line translated into Bengali
is still a Diwali line, when the campaign that audience responds to is Durga
Puja. Authoring per language lets the *occasion* change, not just the words.

**Consequence.** `occasion_by_locale` is a first-class field. Three headline
length variants are requested per locale, so the typesetter selects a line that
fits rather than shrinking type below legibility.

---

## ADR-9 — Mock mode on by default

**Decision.** `MAI_MOCK` defaults to on. The mock enforces the same dimension
limits as the service.

**Why.** There is no local emulator for MAI and the real deployment allows 2–12
RPM. Without this the pipeline could not be built before Azure existed, and the
test suite would inherit a rate limit.

**Consequence.** A dimension bug fails in tests rather than in production. A
test asserts mock mode stays on, so the suite cannot quietly start spending
quota. Keep the mock permanently — it is also a degraded mode and a kill-switch
target.

---

## ADR-10 — Generation is always a job

**Decision.** `POST /api/campaigns` returns `202` and a job id. Never a
synchronous image.

**Why.** 2–12 RPM plus tens of seconds per render means a waiting request times
out behind almost any proxy — and Azure Container Apps caps HTTP requests at
240s regardless.

**Consequence.** When SSE is added it will be a latency optimisation layered
over polling, never the source of truth, because it cannot survive the ingress
timeout either.

---

## ADR-11 — `auto_aspect_ratio` and `web_grounding` off in production

**Decision.** Both 2.6-only flags default off.

**Why.** `auto_aspect_ratio` lets the model choose the output ratio, which
breaks logo placement, safe zones and text boxes simultaneously — our overlay
geometry is locked to a known canvas. `web_grounding` adds a Bing retrieval to
the latency budget, makes output non-reproducible over time, and increases the
trademark and public-figure surface the docs warn about.

**Where they belong.** An "explore" mode with no overlay applied, where the
user is fishing for a concept.

---

## ADR-12 — Python 3.14, not the planned 3.12 pin

**Decision.** Run on the installed 3.14.2.

**Why.** The plan pinned 3.12 anticipating wheel gaps. Every dependency —
FastAPI, Pydantic, SQLAlchemy, Pillow, Playwright, azure-identity — resolved
cleanly on 3.14, so the pin would have been cost without benefit.

**Revisit if** a future dependency lacks a 3.14 wheel.

---

## ADR-13 — Per-format generation in v1, economy mode later

**Decision.** Each requested format costs its own generation. The "one 9:16
master cropped down to everything else" optimisation is deferred to a
user-visible toggle.

**Why.** Cropping a tall image to any wider ratio is free, so a 9:16 master
could yield 3:4, 4:5 and 1:1 at zero extra calls — turning four generations
into one. But the master is 768px wide, so a 4:5 crop upscales ×1.41 instead of
×1.18, and composition control is weaker because the subject must sit in a band
that survives every crop.

At prototype volume, quality wins. At production volume the economics flip,
which is why it becomes a toggle rather than a silent default
([#24](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/24)).
