# Compliance and responsible AI

This page describes what the product must do to be publishable in India. Some
of it is **law in force**; some is standards-body guidance still in draft. The
distinction is marked throughout.

> **Position the product as: "we catch the obvious problems and tell you what
> to check."** A checklist plus an audit log, not a legal opinion. That is both
> honest and sufficient. Have Indian advertising counsel review the rules table
> before launch.

Implementation status is tracked in
[#14](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/14)–[#17](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/17).

---

## AI labelling — law, in force

**MeitY's IT (Intermediary Guidelines and Digital Media Ethics Code) Amendment
Rules, 2026** — notified 10 Feb 2026, **in force 20 Feb 2026**.

- Defines **Synthetically Generated Information (SGI)**: content artificially
  created or altered such that it appears authentic. **AI-generated *text* is
  excluded.** Images are not.
- Non-prohibited SGI must be **clearly and prominently labelled**. The proposed
  10%-of-area minimum watermark size was **dropped** from the final rules.
- Provenance metadata must be embedded **where feasible**.
- Significant social media intermediaries — Instagram qualifies — must require
  users to **declare AI content at upload** and verify by automated means.

### What the product does

**Implemented** — a per-language AI-disclosure line is part of every caption:

| Locale | Line |
|---|---|
| `en` | Created using AI. |
| `hi` | एआई से बनाया गया. |
| `hi-Latn` | AI se banaya gaya. |
| `mr` | एआयने तयार केले. |
| `bn` | এআই দিয়ে তৈরি। |
| `ta` | AI மூலம் உருவாக்கப்பட்டது. |
| `te` | AI ద్వారా రూపొందించబడింది. |

**Platforms strip metadata on upload, so the caption is the only disclosure
channel that reliably reaches a viewer.** That is why it lives in the copy
layer rather than only in the file.

**Pending** ([#15](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/15)):

- **C2PA manifest** attached at the compositing step, signed with our own
  certificate. Compositing modifies pixels, so any manifest inherited from the
  model is invalidated regardless.
- **IPTC** `DigitalSourceType = trainedAlgorithmicMedia` plus XMP — far more
  platforms read IPTC than parse C2PA.
- **On-creative badge**, positionable and brand-styled. Default **on** for
  photorealistic human imagery and regulated categories, **off** for abstract
  or typographic work.
- **Server-side lineage** regardless: brief hash, model and version, prompt,
  safety verdicts, font versions, brand-kit version. Provenance you control
  beats provenance you hope survives a re-encode.
- Tell the user Instagram will ask them to declare AI content, and that the
  honest answer is yes. Marketers hate surprises more than they hate labels.

> Whether MAI output already carries Content Credentials is **undocumented** —
> it is documented for Azure OpenAI image models and silent for MAI. Probe with
> `c2patool` before claiming it does
> ([#29](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/29)).

---

## ASCI AI-labelling tiers — draft

ASCI published *Draft Guidelines for Responsible Labelling of AI-Generated
Content in Advertising* on 8 May 2026; consultation closed 13 June 2026. Still
draft as of September 2026 — **keep these as updatable configuration, not
code**.

The risk tiers map cleanly onto product behaviour:

| Tier | Covers | Product behaviour |
|---|---|---|
| **High — prohibited** | False endorsements, deepfakes, unauthorised likenesses, fabricated authority figures (their example: an AI-generated doctor promoting a supplement) | **Hard block. A label does not cure these.** |
| **Medium — must disclose** | AI use that could materially influence a purchase, e.g. a synthetic person demonstrating a product | Label defaults **on** |
| **Low — no label** | Colour correction, background effects, obviously unreal effects | Label defaults **off** |

---

## Hard blocks

Never generated, no override
([#17](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/17)):

- **Maps of India.** The model will render an incorrect boundary for
  J&K/Ladakh/Arunachal. Publishing one is a legal and PR problem, not a quality
  one. Substitute a single vetted vector asset.
- **The national flag.** Governed by the Flag Code — no distortion, no use as
  drapery, no placement on disposable items, correct saffron-on-top
  orientation. This goes wrong around 15 August and 26 January.
- **Deities on products, footwear, apparel, floor surfaces or discarded
  packaging**, or adjacent to alcohol, tobacco, beef or leather.
- **Public figures, celebrity likenesses, fabricated authority figures.**
- **Skin-lightening or "fairness" claims**, in any language.

Currently these appear in `GLOBAL_PROHIBITIONS` as prompt text. A prompt is
advisory — they need NER on the input brief and a vision check on the output
too.

### Gate with confirmation

- **Deity imagery in a festive context.** Lakshmi or Ganesha at Diwali is
  normal and widely used — but must be respectful, not stylised into a mascot,
  and not for a restricted category. A blanket block would be wrong, since
  festival creative is the core use case.
- Religious attire and ritual depiction — fine when respectful, easily wrong
  when decorative.
- Anything targeting children.

### Representation defaults

Unprompted, the model will produce a light-skinned, thin, North-Indian-coded
22-year-old woman every time. Expose a skin-tone control **defaulting to a
realistic Indian distribution, not the pale end**, and flag gendered-role
defaults in the prompt-expansion layer. A Kerala jewellery ad and a Punjab agri
ad should not use the same face.

---

## Category gates

[#16](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/16)

| Category | Rule | Behaviour |
|---|---|---|
| **Food & beverage** | FSS Act 2006 + Advertising and Claims Regulations 2018. FSSAI actively enforcing against "100% pure/natural", "healthy", "organic" claims. Penalties up to ₹10 lakh; brand owners *and* publishers liable | FSSAI licence field; flag unsubstantiated superlatives **in every language** — "100% शुद्ध" must be caught too |
| **Financial** | SEBI draft Common Advertisement Code would treat AI avatars as celebrities. Plus RBI rules for lending and deposit ads | Mandatory risk disclaimer; block returns and guaranteed-profit claims; block synthetic-human endorsement of specific products |
| **Alcohol & tobacco** | Direct advertising banned (COTPA; state excise). Surrogate advertising prohibited | **Block outright in v1.** The framework is genuinely contradictory across instruments and not worth the exposure |
| **Pharma / health** | Drugs & Magic Remedies Act; Drugs & Cosmetics Act | Block disease-cure claims; block synthetic doctors, lab coats, stethoscopes |
| **Real estate** | RERA registration number mandatory in every advertisement | Mandatory field; auto-placed fine-print band |
| **Coaching / education** | CCPA Coaching Guidelines 2024 — no unsubstantiated success rates | Flag rank and selection claims; require consent for student imagery |

General exposure: **Consumer Protection Act 2019 / CCPA** penalties up to ₹10
lakh first offence and ₹50 lakh repeat, plus endorser bans. **DPDP Act 2023**
applies to uploaded reference images containing identifiable people — consent
capture belongs in the upload flow, not only the terms of service.

---

## Content Safety

[#14](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/14)

Four checkpoints, because **the raw model output is not the artifact**:

1. **Input brief** — Analyze Text + Prompt Shields + blocklists
2. **Uploaded reference image** — Analyze Image, *plus* OCR of any text in it
   fed to Prompt Shields as `documents[]`
3. **Raw MAI output** — Analyze Image; discard on block, never persist
4. **Final composited creative** — Analyze Image again

Checkpoint 4 is the one teams forget. Compositing changes meaning: benign
imagery plus a headline can become a violating creative. Moderate what ships.

### The injection vector

A user uploads a reference image **containing text**: *"Ignore previous
instructions and…"*. The vision model reads it, and the OCR'd text flows into
the prompt-expansion context. That is textbook indirect prompt injection.

Three defences: OCR the upload and pass the extracted text to Prompt Shields as
`documents[]` (exactly what that array is for); constrain the vision model to a
JSON schema with no field in which instruction text can travel; and treat any
instructions found in content as data, never as instructions.

**Never auto-sanitise-and-retry an injection.** That trains users to iterate on
jailbreaks.

### A weakness to design around

Azure AI Content Safety is optimised for nine languages and merely "functional"
in many more. **For every one of our target languages except English, severity
scoring is less reliable.**

Mitigation: moderate both the native-script copy *and* its English
back-translation, taking the max severity. This is a second, unrelated reason
the back-translation exists.

Expect false positives too — over-blocking legitimate festival and category
imagery is a real product killer in this market. Measure the false-positive
rate, allow per-tenant thresholds within policy, and never fail closed without
telling the user which category and severity triggered it.

---

## Text verification, and its limits

Azure Vision Read supports Latin, Cyrillic, Arabic and Devanagari. It does
**not** support Bengali, Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi
or Odia.

So for most of our languages there is no automated way to verify rendered text.
Two consequences:

1. **The model never draws those scripts.** This is the operational rule behind
   the whole overlay architecture — we cannot ship what we cannot check.
2. **The honest answer** to *"how do you know the Tamil is right?"* is
   deterministic font rendering plus native-speaker review of a sample. Not
   "we check it automatically."

Which is why the review workflow
([#21](https://github.com/rudrakshxgupta/marketing-campaign-generator/issues/21))
is not optional polish. Every machine-written locale ships
`needs_review=True` and the bundle carries a `REVIEW.txt`; the English
back-translation lets a reviewer who cannot read the script still check
meaning.

**Never report a quality average across languages** — it hides the case where
one language is failing badly.
