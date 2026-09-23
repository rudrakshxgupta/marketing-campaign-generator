# How this project was built

A record of *how* the system came to be shaped the way it is — the research
that set the constraints, the decisions that followed from them, and the things
that went wrong on the way.

[PROJECT_REPORT.md](PROJECT_REPORT.md) covers the current status. This covers
the making of it.

---

## 1. It started with three specialists

The brief was: a platform where a marketer supplies a reference image or a text
description, optionally a logo, and gets Instagram creatives in Indian
languages, built on Microsoft AI Foundry with MAI-Image-2.6.

Rather than design it alone, three research agents ran in parallel — an
**architect**, an **AI/ML expert**, and a **marketing consultant**. Each found
something that changed the design, and none of it was guessable from the brief.

| Specialist | Finding |
|---|---|
| Architect | The `/images/edits` endpoint accepts **no `width`/`height`**. Image-to-image geometry has to be handled by cropping on both sides of the call. |
| Marketing | **4:5 is Instagram's feed default**, not 1:1. The profile grid crops to 3:4. Meta unified the 9:16 safe zone in March 2026, leaving only **950×979** usable. |
| AI expert | MAI-Image-2.6 declares **`Languages: en`**, and Azure OCR covers **none** of Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi or Odia. |

Separately I computed the legal aspect band from the raw limits and found that
**Instagram's 9:16 story is illegal by one pixel** and **1.91:1 landscape is
unreachable at any size**.

That last one is the flavour of the whole project: the documentation is
accurate, and the traps are in how the rules *interact*.

---

## 2. Three facts collapsed the design space

The obvious build is: ask the model to draw the picture *and* the text. Three
findings ruled that out.

1. **The model only supports English.** Drawing Devanagari or Tamil is outside
   its supported envelope.
2. **2–12 requests per minute**, for the whole account. Seven languages × four
   formats as separate calls is not physically possible.
3. **Azure OCR can't read 8 of the Indic scripts.** We could not *verify*
   model-drawn text in most of our languages even if we wanted it.

The third is the one that turns judgement into rule: **we do not ship what we
cannot check.**

All three point the same way:

> **Generate ONE text-free image. Composite the logo and every language's text
> deterministically on top.**

Think of a printed poster. You don't reprint the background for every language;
you print it once and overlay the text.

That single decision cascaded into almost everything else. The logo became
byte-exact rather than hallucinated. Brand colour became exact rather than
approximate. QA for the text path became arithmetic rather than inference. And
the seventh language became free.

**Measured: 2 image calls → 14 finished deliverables.**

---

## 3. Build order, and why

Phase 1 was a deliberately thin vertical slice with a **mock image backend**.
There is no local emulator for MAI and the real thing allows 2 RPM, so without
a mock the pipeline could not be built at all before Azure existed — and the
test suite would have inherited a rate limit.

The mock enforces the *same dimension limits as the service*, so a sizing bug
fails in tests rather than in production. A test asserts mock mode stays on, so
the suite can never quietly start spending real quota.

Order within the slice was chosen by risk, not by dependency:

1. **Dimensions first** — everything else needs a legal canvas
2. **Safe zones** — they constrain where anything can go
3. **Logo compositing** — proves the deterministic-overlay thesis
4. **Indic typography** — the highest-risk component, done early on purpose
5. Pipeline, API, export

---

## 4. Four bugs worth remembering

Every one produced output that *looked* correct. That is the pattern.

### The safe-zone gate fired on its first real use

A logo anchored 5% from the canvas edge lands **under the feed caption row**.
The fix was not to move the logo — it was to anchor placement inside the *safe
rect* rather than the canvas, making compliance structural instead of
something checked afterwards.

### Tamil words broke mid-cluster

The tell was every locale returning **exactly the maximum font size**.
`overflow-wrap: break-word` meant overflow could never be detected, so the fit
search always "succeeded" at maximum — and long Tamil words were chopped across
lines, splitting consonants from their matras.

Fix: forbid breaking, so overflow becomes *detectable* and the fitter shrinks
instead. Tamil now fits at 79px where English sits at 108px, which is exactly
what its length factor predicts.

### The fit test was structurally broken

`block.scrollHeight` always exceeds `clientHeight` by a pixel or two, because
glyphs overshoot their line box. So *every* candidate size looked like a
failure and everything silently fell back to the minimum. It appeared to work
only when a CTA happened to be present, by coincidence of flex layout.

### Fidelity detection was exactly backwards

The first version compared whole frames. It **rejected a legitimate sky change
and passed a building gaining a floor.**

The reason is structural: restaging is *supposed* to change the background, so
the harmless edit moves most of the pixels while the dangerous one moves very
few. Whole-frame similarity ranks them the wrong way round.

Two further sub-bugs surfaced while fixing it — the structure threshold had to
come from the *original only* (or recolouring the sky reclassifies the
building), and it had to be `mean + 2σ` rather than a percentile (the subject
is usually a *minority* of the frame, so p75 landed at 4.5, well inside the
flat region, against 31.7 where the real edges are).

### And one where the test was wrong, not the code

An early assertion required every format to upscale. Landscape *downscales* —
1080×566 is fewer pixels than the 16:9 frame it's cropped from — which is a
quality win. The assertion now checks what actually matters: that upscaling
stays within a 1.45× budget.

---

## 5. Testing things that fail invisibly

The hard part was never ordinary correctness. It was the class of defect that
renders beautifully and is wrong.

**Proving the shaping engine is actually running.** Asserting that ink appeared
proves nothing — broken shaping produces ink too, in the right colour, at the
right size, in the right box. So the test measures a **ligated conjunct against
the same characters forced apart with a zero-width non-joiner**. A shaping
engine gives different widths; a naive glyph blitter gives identical ones.

**Asserting against rules, not numbers.** Hard-coding `912×1140` would pass
forever even if the resolver broke. Every format is checked against the raw
constraints instead — both sides ≥ 768, product ≤ 1,048,576, and above 99%
budget utilisation.

**A visual gate for what no assertion catches.** `scripts/language_proof.py`
renders one campaign in all seven locales from a single generation and builds a
contact sheet. A detached matra passes every automated check; it has to be
looked at.

269 tests, about 100 seconds.

---

## 6. The credit constraint reordered everything

Partway through, the situation changed: limited Azure credits, on a student
subscription, for a showcase.

That made the most valuable next work not a feature but **making sure a credit
is never spent twice**. Three things moved to the front:

- **A hard ceiling**, checked before the call and persisted to disk, so a retry
  loop cannot drain anything. Failed calls are never charged — a 429 costs
  nothing, so billing for it would make the ledger lie.
- **A cache**, because users re-click Generate constantly and each click was a
  fresh charge for a byte-identical result. Deliberately *not* semantic: two
  similar briefs do not mean the user wants the same picture.
- **Economy mode**, promoted from Phase 4. One tall master cropped down to
  every format: **4 formats went from 4 calls to 1.**

The composition order is the point: `cache → budget → client`, so a cache hit
consumes no budget.

The probe was later cut from 3 images to **1** — enough to answer connectivity,
dimensions, latency and C2PA, which is everything that could change the design.

---

## 7. Preserving real things

A late requirement, and a sharp one: if someone uploads a photo of a real
building, the model must **restage** it, not **redesign** it.

MAI's edit mode supports attribute changes, so left alone it will add a balcony
or turn four storeys into five. For a property listing that is not a bad render
— it is an advertisement for a building that does not exist, and the person
running the tool cannot see the problem.

Two mechanisms, because a prompt is advisory:

- A clause that **enumerates the actual failures** per subject kind. Generic
  wording does not work: a model told "keep the building the same" still
  changes the floor count, because it has no reason to think that is what "the
  same" means.
- A **measurement afterwards**, across eight subject kinds, with architecture
  and jewellery held strictest.

---

## 8. Environment problems that were not code

Two stand out because they cost real time and neither was a bug in the project.

**Windows Smart App Control blocked a DLL.** Mid-project, 36 tests started
failing with `DLL load failed while importing _greenlet`. The Code Integrity
event log pinned it exactly: Smart App Control refuses unsigned binaries, and
`_greenlet.cp314-win_amd64.pyd` is unsigned. Reinstalling didn't help — the
fresh copy is equally unsigned. Playwright imports greenlet unconditionally, so
there was no code workaround. Turning Smart App Control off fixed all 36.

**Azure needed MFA for writes but not reads.** Discovery worked fine on a
cached token; the deployment write triggered `AADSTS50076`. Read and write
carry different auth requirements on a managed tenant.

I also pushed a commit while tests were red — my own mistake, from chaining
commands so a failure didn't stop the push. The code was fine, but it should
not have gone out unverified.

---

## 9. What the tooling ended up being for

Three scripts exist purely to make the Azure step cheap and safe:

- **`setup_foundry.ps1`** provisions from scratch, and verifies MAI-Image-2.6
  is actually offered to the subscription *before* deploying — these models are
  preview and access varies.
- **`discover_foundry.ps1`** handles the case where a resource already exists.
  It answers the non-obvious question: MAI images run only in `southindia` and
  `uaenorth`, so a healthy resource in another region simply cannot run them —
  and that failure looks exactly like a typo in the deployment name.
- **`check_connection.py`** verifies auth and deployment with a **deliberately
  invalid request**, which the service rejects before doing any work. Zero
  images. Written because the real probe costs money, and a typo in the
  endpoint shouldn't cost an image to discover.

That last one earned its keep immediately: it proved the key and endpoint were
valid while revealing that no MAI deployment existed yet — without spending
anything.

---

## 10. Principles that held up

- **Verify, don't assume.** Almost every important number here was measured or
  read from a primary source. The ones I guessed at were the ones I got wrong.
- **A prompt is advisory.** Anything that matters gets checked afterwards: text
  presence, safe zones, contrast, subject fidelity.
- **Make correctness structural.** Anchoring the logo to the safe rect beats
  checking afterwards, because the check can be skipped and the geometry cannot.
- **Fail toward review, not toward silence.** A drifted subject or unreviewed
  copy is flagged, never quietly shipped.
- **Say what is not done.** Nothing has run against live Azure. The copy is
  canned. The fonts are borrowed. All three are in the README, because a status
  report that sounds finished is worse than useless.
