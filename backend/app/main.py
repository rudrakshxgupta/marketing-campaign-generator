"""FastAPI surface for the campaign generator.

Generation is never synchronous. MAI allows 2-12 requests per minute and a
single render takes tens of seconds, so a request that waited for an image
would time out behind almost any proxy. Every generation is a job; the client
polls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field

from app.config import get_settings
from app.copy.languages import DEFAULT_LOCALES, LOCALES
from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy, NegativeSpace
from app.foundry.image_client import MaiImageClient, MaiRateLimited
from app.foundry.mock_client import MockImageClient
from app.imaging.dimensions import FORMATS
from app.imaging.overlay import OverlayRenderer
from app.pipeline import CampaignPipeline, CampaignResult, export_bundle

logger = logging.getLogger(__name__)

JobState = Literal[
    "queued", "generating", "compositing", "complete", "failed", "rate_limited"
]


@dataclass
class Job:
    id: str
    state: JobState = "queued"
    progress: int = 0
    error: str | None = None
    result: CampaignResult | None = None
    bundle: Path | None = None
    logs: list[str] = field(default_factory=list)


JOBS: dict[str, Job] = {}


class GenerateRequest(BaseModel):
    brief: str = Field(..., description="What the picture should show")
    proposition: str = "Purity you can taste"
    benefit: str = "Cold-pressed, nothing added"
    brand_name: str = "ACME"
    occasion: str = ""
    mandatory_line: str = ""
    formats: list[str] = Field(default_factory=lambda: ["portrait"])
    locales: list[str] = Field(default_factory=lambda: list(DEFAULT_LOCALES))
    facts: list[str] = Field(default_factory=list)
    #: Per-locale occasion overrides. The right festival differs by region --
    #: a Bengali audience's gifting peak is Durga Puja, not Diwali.
    occasion_by_locale: dict[str, str] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.renderer = OverlayRenderer()
    app.state.images = (
        MockImageClient() if settings.mock else MaiImageClient(settings)
    )
    if settings.mock:
        logger.warning(
            "MAI_MOCK is on: images are placeholders, no Foundry calls are made"
        )
    yield
    await app.state.renderer.aclose()
    if hasattr(app.state.images, "aclose"):
        await app.state.images.aclose()


app = FastAPI(title="Marketing Campaign Generator", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    settings = get_settings()
    return {
        "ok": True,
        "mock": settings.mock,
        "image_deployment": settings.image_deployment,
        "mai_rpm": settings.mai_rpm,
    }


@app.get("/api/meta")
async def meta() -> dict:
    """Formats and locales, including the generation size each format needs.

    The frontend renders from this rather than hard-coding sizes, so the
    dimension rules live in exactly one place.
    """
    from app.imaging.dimensions import plan_for

    return {
        "formats": [
            {
                "key": key,
                "target": [plan.target_w, plan.target_h],
                "generate": [plan.gen_w, plan.gen_h],
                "requires_crop": plan.requires_crop,
            }
            for key, plan in ((k, plan_for(k)) for k in FORMATS)
        ],
        "locales": [
            {
                "key": loc.key,
                "label": loc.label,
                "native": loc.native_name,
                "language": loc.language,
                "script": loc.script,
                "register": loc.register,
            }
            for loc in LOCALES.values()
        ],
    }


@app.post("/api/campaigns", status_code=202)
async def create_campaign(request: GenerateRequest) -> dict:
    unknown_formats = set(request.formats) - set(FORMATS)
    if unknown_formats:
        raise HTTPException(400, f"unknown formats: {sorted(unknown_formats)}")
    unknown_locales = set(request.locales) - set(LOCALES)
    if unknown_locales:
        raise HTTPException(400, f"unknown locales: {sorted(unknown_locales)}")

    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    asyncio.create_task(_run_job(job, request))

    # One generation per format; every locale reuses it.
    return {
        "job_id": job.id,
        "state": job.state,
        "image_calls_expected": len(request.formats),
        "deliverables_expected": len(request.formats) * len(request.locales),
        "poll": f"/api/jobs/{job.id}",
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")

    payload: dict = {
        "job_id": job.id,
        "state": job.state,
        "progress": job.progress,
        "error": job.error,
        "logs": job.logs[-20:],
    }
    if job.result is not None:
        payload["image_calls"] = job.result.image_calls
        payload["prompt"] = job.result.prompt
        payload["variants"] = [
            {
                "locale": v.locale,
                "format": v.format_key,
                "file": v.path.name,
                "headline_px": v.fit.headline_px,
                "headline_used": v.fit.headline_used,
                "logo_variant": v.logo_variant,
                "needs_review": v.needs_review,
                "review_reasons": list(v.review_reasons),
            }
            for v in job.result.variants
        ]
        payload["review_count"] = len(job.result.review_queue)
    if job.bundle is not None:
        payload["bundle"] = f"/api/jobs/{job.id}/bundle"
    return payload


@app.get("/api/jobs/{job_id}/bundle")
async def download_bundle(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or job.bundle is None:
        raise HTTPException(404, "no bundle for this job")
    return FileResponse(
        job.bundle, media_type="application/zip", filename=f"campaign-{job_id}.zip"
    )


@app.post("/api/uploads/logo")
async def upload_logo(file: UploadFile) -> dict:
    """Store a brand logo.

    Re-encoded on the way in, which normalises the format and strips any EXIF
    or embedded payload from a file we did not create.
    """
    settings = get_settings()
    data = await file.read()
    try:
        with Image.open(__import__("io").BytesIO(data)) as image:
            image = image.convert("RGBA")
    except Exception as exc:
        raise HTTPException(400, f"not a readable image: {exc}") from exc

    logo_id = uuid.uuid4().hex[:12]
    path = settings.storage_root / "uploads" / f"logo_{logo_id}.png"
    image.save(path, format="PNG")
    return {"logo_id": logo_id, "width": image.width, "height": image.height}


async def _run_job(job: Job, request: GenerateRequest) -> None:
    settings = get_settings()
    pipeline = CampaignPipeline(
        app.state.images,
        renderer=app.state.renderer,
        storage=settings.storage_root,
    )
    try:
        job.state = "generating"
        job.logs.append(
            f"one generation per format ({len(request.formats)}), "
            f"{len(request.locales)} locales composited from each"
        )

        brief = CreativeBrief(
            subject=request.brief,
            negative_space=NegativeSpace(region="bottom_left", coverage_pct=32),
        )
        strategy = CreativeStrategy(
            proposition=request.proposition,
            benefit=request.benefit,
            occasion=request.occasion,
            occasion_by_locale=request.occasion_by_locale,
            facts=tuple(request.facts),
        )
        brand = BrandKit(name=request.brand_name, mandatory_line=request.mandatory_line)

        logo_path = next(
            iter(sorted((settings.storage_root / "uploads").glob("logo_*.png"))), None
        )
        logo = Image.open(logo_path).convert("RGBA") if logo_path else None

        job.state = "compositing"
        job.progress = 40
        result = await pipeline.run(
            campaign_id=job.id,
            brief=brief,
            strategy=strategy,
            brand=brand,
            logo=logo,
            formats=tuple(request.formats),
            locales=tuple(request.locales),
        )

        job.result = result
        job.bundle = export_bundle(
            result, settings.storage_root / "exports" / f"{job.id}.zip"
        )
        job.progress = 100
        job.state = "complete"
        job.logs.append(
            f"{result.image_calls} image call(s) -> {len(result.variants)} deliverables"
        )
        if result.review_queue:
            job.logs.append(f"{len(result.review_queue)} item(s) need review")

    except MaiRateLimited as exc:
        job.state = "rate_limited"
        job.error = str(exc)
        job.logs.append("MAI is at its RPM ceiling; retry shortly")
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        logger.exception("job %s failed", job.id)
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        await pipeline.aclose()
