# BrandlyAI — 5-minute submission video

Narration for `en-IN-NeerjaNeural`, newscast style. Roughly 700 words, which
lands near 4:50 at a measured pace, leaving headroom under the five-minute cap.

Written to be *shown*, not read aloud over a feature list. Each section names a
problem first, because the interesting parts of this project are all decisions
forced by a constraint rather than features chosen from a menu.

| # | Slide | Approx. start |
|---|-------|---------------|
| 01 | Title | 0:00 |
| 02 | The problem | 0:18 |
| 03 | Three constraints | 0:42 |
| 04 | The architecture | 1:12 |
| 05 | Live: one line in | 1:42 |
| 06 | The compiled brief | 2:05 |
| 07 | Seven languages | 2:28 |
| 08 | Transcreation, not translation | 2:52 |
| 09 | Subject preservation | 3:16 |
| 10 | The logo is composited | 3:44 |
| 11 | Spend control | 4:06 |
| 12 | What it cost | 4:28 |
| 13 | Close | 4:44 |

---

## 01 — Title (0:00)

This is BrandlyAI. A marketer types one line — what they sell — and gets back
Instagram-ready campaign creatives in seven Indian languages. It runs on
Microsoft AI Foundry.

## 02 — The problem (0:18)

Indian small businesses advertise to an audience that does not read English.
The tools they have generate one English post. Translating it afterwards gets
the words right and the campaign wrong, because a Diwali line translated into
Bengali is still a Diwali line — when the campaign that audience responds to is
Durga Puja.

## 03 — Three constraints (0:42)

Three facts shaped everything.

The image models declare English as their only supported language. Indic
scripts need complex shaping — reordering, conjuncts, matras — and diffusion
models garble them in a uniquely dangerous way: the output looks plausible to
an English speaker and reads as illiterate to the audience.

Image generation is rate-limited and billed per call.

And Azure's OCR cannot read Bengali, Tamil or Telugu, so we could not even
verify model-drawn text automatically.

## 04 — The architecture (1:12)

All three point the same way. Generate one text-free image. Composite every
language on top of it, deterministically.

The logo becomes byte-exact instead of hallucinated. Indic typography becomes
correct instead of garbled. And the seventh language costs zero model calls.

## 05 — Live: one line in (1:42)

Here is the whole input. One field: what do you sell. Everything else — the
photograph, the message, the occasion, the copy in every language — is worked
out from it.

## 06 — The compiled brief (2:05)

A structured model call turns that line into a brief: what to photograph, the
proposition, the occasion, and which region of the frame to keep clear for the
copy. The user sees what the AI decided, rather than wondering where the
picture came from.

## 07 — Seven languages (2:28)

One image call, seven deliverables. English, Hindi, Hinglish, Marathi, Bengali,
Tamil and Telugu. The text is typeset in headless Chromium, which carries
HarfBuzz and ICU — so the conjuncts and matras are correct, which is the one
thing a Python imaging library would have got silently wrong.

## 08 — Transcreation, not translation (2:52)

The copy is authored in each language from a shared strategy, not translated
from English. Which means the referent can change and not just the words. Look
at the occasion line: Diwali for Hindi, Durga Puja for Bengali, Pongal for
Tamil. Same campaign, three correct festivals.

## 09 — Subject preservation (3:16)

If you upload a photograph of something real, it must not be reinvented. A
building that gains a storey renders beautifully and advertises a property that
does not exist.

So "use this exact image" keeps the subject pixel-accurate and restages only
what is around it — and the result is measured against the original. Floor
count, window positions, outline. If the subject drifts, the creative is
flagged before anyone publishes it.

## 10 — The logo is composited (3:44)

The logo is never drawn by the model. A generated logo is a wrong logo. It is
placed from the real file, sized to the mark itself, with the light or dark
version chosen by measuring the pixels behind it. If there is no logo file, the
brand name is typeset as a wordmark instead.

## 11 — Spend control (4:06)

Image quota is scarce, so the cost of every action is shown before it is taken.
A hard ceiling is enforced before the call and survives a restart. And because
the base image carries no text, editing the copy re-typesets it for free — the
picture updates as you type, with no generation at all.

## 12 — What it cost (4:28)

That is the point of the architecture. One image call produced seven finished
deliverables. Adding an eighth language would cost nothing.

## 13 — Close (4:44)

BrandlyAI. Two hundred and sixty-nine tests, running live on Foundry. Thank
you.
