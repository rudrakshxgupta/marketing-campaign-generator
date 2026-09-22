# Documentation

Instagram campaign creatives from a brief or a reference image, in Indian
languages, built on Microsoft AI Foundry with **MAI-Image-2.6**.

## Start here

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | How the system works and why it is shaped this way |
| [constraints.md](constraints.md) | The verified platform facts every design choice rests on |
| [typography.md](typography.md) | Indic text rendering — the highest-risk component |
| [fidelity.md](fidelity.md) | Keeping a real building or product unchanged through an edit |
| [api.md](api.md) | HTTP surface, request/response shapes, configuration |
| [code-map.md](code-map.md) | Module-by-module reference |
| [compliance.md](compliance.md) | Indian advertising law, content safety, provenance |
| [testing.md](testing.md) | How to verify the system, including by eye |
| [decisions.md](decisions.md) | Decision records — what was chosen, and what it cost |
| [roadmap.md](roadmap.md) | What is built, what is not, mapped to issues |

## The one idea

Generate **one text-free image**. Composite the logo and every language's text
deterministically on top.

```
brief ──► CreativeBrief ──► English prompt ──► MAI-Image-2.6 ──► text-free base
                                                                      │
                            ┌─────────────────────────────────────────┤
                            ▼                    ▼                    ▼
                      en overlay           hi overlay           ta overlay   …
                            │                    │                    │
                            ▼                    ▼                    ▼
                      + real logo          + real logo          + real logo
                            │                    │                    │
                         post-ready          post-ready          post-ready
```

Only the generation step is rate-limited. Everything after it is deterministic
CPU work, which is why the seventh language costs **zero** model calls.

**Measured: 2 image calls → 14 deliverables.**

Three verified facts force this design, each covered in
[constraints.md](constraints.md):

1. MAI-Image-2.6 declares `Languages: en` — it should not be drawing Indic
   script at all.
2. The model allows 2–12 requests per minute — per-language generation is not
   physically possible.
3. Azure OCR cannot extract 8 of the Indic scripts — we could not verify
   model-drawn text in most of our languages even if we wanted it.

## Status

Phase 1 is complete and tested: **121 tests pass**, image generation mocked by
default. See [roadmap.md](roadmap.md) for what is outstanding.

> **MAI image models are public preview** — no SLA, and Microsoft's own
> documentation says not recommended for production workloads. That is a
> business risk to accept deliberately, not something the architecture can
> engineer away. Every call is isolated in one module so the blast radius of an
> API change stays small.
