# Connecting real Foundry

The prototype runs fully in mock mode with no Azure account at all. This page
is only needed to switch on real image generation.

## Configuration

Copy `.env.example` to `.env` and set:

```ini
MAI_MOCK=0
FOUNDRY_ENDPOINT=https://<your-resource>.services.ai.azure.com
IMAGE_BACKEND=flux          # or "mai" if you have MAI image quota
FLUX_MODEL=FLUX.2-pro
MAI_TEXT_DEPLOYMENT=gpt-5-mini

MAI_DAILY_LIMIT=20          # hard ceilings, enforced before the call
MAI_TOTAL_LIMIT=30
```

`.env` is read at startup and is **not** loaded under pytest — the file names a
billed deployment, and a test run must not be able to spend anything.

The spend limits are re-read from the file on every call, so raising a cap
mid-session does not need a restart.

## Authentication

Entra ID via `DefaultAzureCredential`, which means your `az login`. No API key
is stored anywhere in the repository, and none should be used in production.

```bash
winget install --id Microsoft.AzureCLI -e   # not installed by default
```

Open a **new** terminal so `PATH` refreshes, then:

```bash
az login
```

## Provisioning from scratch

```powershell
./scripts/setup_foundry.ps1 -ResourceName <your-unique-name> -WriteEnv
```

The resource name becomes your endpoint hostname, so it must be globally
unique. The script checks prerequisites, creates the resource and project,
confirms the image model is actually offered to your subscription *before*
deploying, and writes your `.env`. Add `-WhatIf` to see what it would do
without creating anything.

## Verifying

```bash
cd backend && ../.venv/Scripts/python scripts/probe_foundry.py
```

Measures real generation latency, checks whether output carries C2PA
credentials, and confirms the edits endpoint's output geometry. It costs
**3 image calls** and says exactly how many it used.

## Deployment naming, which is a trap

FLUX's URL carries the model *path* (`flux-2-pro`), but the service resolves
that back to the model *id* and then looks for a deployment named after it. So
a deployment named `flux-2-pro` is never found, however correct the URL is.

**Name the deployment after the model id: `FLUX.2-pro`.**

The 404 body says "The API deployment flux.2-pro does not exist", with a dot,
which is the only clue.

FLUX also lives on a different host from MAI on the same resource:

| Model | Host |
|-------|------|
| MAI | `<resource>.services.ai.azure.com` |
| FLUX | `<resource>.cognitiveservices.azure.com` |

Microsoft's docs give the FLUX host as `<resource>.api.cognitive.microsoft.com`,
which does not resolve at all — it fails as a DNS error rather than a 404,
which sends you looking in entirely the wrong place.

## Quota

Every Microsoft image model (`MAI-Image-*`, `gpt-image-*`) reported a quota
limit of **0** on an Azure for Students subscription, in every region tried.
Azure's own error:

> the quota limit is 0 for quota Requests Per Minute - MAI-Image-2.6

FLUX.2-pro (15 RPM) and FLUX.2-flex (5 RPM) had quota, which is why
`IMAGE_BACKEND=flux` is the working default here.

If you want MAI, file the quota-increase request early —
[aka.ms/oai/stuquotarequest](https://aka.ms/oai/stuquotarequest). Priority goes
to accounts already using their allocation, so the clock starts when you begin
generating, not when you ask.

> ⚠️ MAI image models are **public preview**: no SLA, and Microsoft's own docs
> say not recommended for production. Every call is isolated behind one client
> module so the blast radius of an API change stays small.
