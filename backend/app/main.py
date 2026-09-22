"""FastAPI surface for the campaign generator.

Generation is never synchronous. MAI allows 2-12 requests per minute and a
single render takes tens of seconds, so a request that waited for an image
would time out behind almost any proxy. Every generation is a job; the client
polls.
"""

from __future__ import annotations

import asyncio
import io
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
from app.copy.fidelity import SubjectKind, detect_kind
from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy, NegativeSpace
from app.foundry.budget import BudgetedImageBackend, BudgetExceeded
from app.foundry.cache import CachingImageBackend
from app.foundry.image_client import MaiImageClient, MaiRateLimited
from app.copy.transcreate import FoundryCopyWriter, StubCopyWriter
from app.foundry.mock_client import MockImageClient
from app.foundry.text_client import FoundryTextClient
from app.imaging.dimensions import FORMATS
from app.imaging.overlay import OverlayRenderer
from app.imaging.style import apply_style, extract_style
from app.pipeline import CampaignPipeline, CampaignResult, export_bundle

logger = logging.getLogger(__name__)

JobState = Literal[
    "queued", "generating", "compositing", "complete",
    "failed", "rate_limited", "budget_exceeded",
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

#: In-flight job tasks, so shutdown can wait for them rather than pulling the
#: renderer and HTTP client out from under a job that is still writing.
RUNNING: set[asyncio.Task] = set()


def _spawn(job: Job, request: GenerateRequest) -> None:
    task = asyncio.create_task(_run_job(job, request))
    RUNNING.add(task)
    task.add_done_callback(RUNNING.discard)


async def _drain(timeout: float = 30.0) -> None:
    """Let running jobs finish, then cancel whatever is left.

    Without this, closing the browser while a job is mid-render leaves the job
    awaiting a page that will never respond.
    """
    if not RUNNING:
        return
    pending = set(RUNNING)
    done, still_running = await asyncio.wait(pending, timeout=timeout)
    for task in still_running:
        task.cancel()
    if still_running:
        await asyncio.gather(*still_running, return_exceptions=True)


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
    #: Generate one tall master and crop every other format out of it, turning
    #: N generations into one. Costs some sharpness and composition control,
    #: so it is opt-in rather than a silent default.
    economy: bool = False

    # --- reference image ---------------------------------------------------
    #: From POST /api/uploads/reference.
    reference_id: str | None = None
    #: "inspiration" matches the look and builds a fresh image around your
    #: subject. "edit" keeps the actual photo and changes what you describe.
    #:
    #: Default to inspiration: edit mode derives the output pixel-for-pixel
    #: from the input, so if the user uploaded someone else's ad it produces a
    #: derivative work. It also inherits the input's composition, which means
    #: our safe zones are no longer guaranteed.
    reference_mode: Literal["inspiration", "edit"] = "inspiration"
    #: What to change, for edit mode.
    edit_instruction: str = ""
    #: Required for edit mode. The user asserting they may use this image.
    rights_confirmed: bool = False

    # --- subject preservation ----------------------------------------------
    #: What is being photographed. Left unset it is inferred from the brief.
    #: Drives how specifically the prompt forbids changes, and how strictly the
    #: result is checked afterwards.
    subject_kind: SubjectKind | None = None
    #: Whether the subject must survive the edit untouched.
    #:
    #: On by default, and it matters most for real estate: a diffusion model
    #: will happily give a building an extra storey or move its windows, which
    #: for a property listing is not a render flaw but an advertisement for a
    #: building that does not exist. Turn it off only when the subject is meant
    #: to be reimagined.
    preserve_subject: bool = True


def build_image_backend(settings) -> tuple[object, object | None, object | None]:
    """Compose the image backend stack.

    Ordering matters and is the whole point:

        cache -> budget -> client

    The cache sits outermost so a repeat request is served from disk *without*
    consuming budget. The budget guard sits above the client so a retry loop
    cannot quietly spend a month of credits.

    Returns (backend, budget, cache) so the API can report spend.
    """
    base = MockImageClient() if settings.mock else MaiImageClient(settings)

    if settings.mock:
        # No spend to guard and no benefit to caching placeholders.
        return base, None, None

    budget = BudgetedImageBackend(
        base,
        storage_root=settings.storage_root,
        daily_limit=settings.daily_limit,
        total_limit=settings.total_limit,
    )
    cache = CachingImageBackend(
        budget, root=settings.cache_root, enabled=settings.cache_enabled
    )
    return cache, budget, cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.renderer = OverlayRenderer()
    app.state.images, app.state.budget, app.state.cache = build_image_backend(settings)

    # Copy is written by a live model whenever one is reachable, even while
    # images are mocked. Text quota is separate from image quota and vastly
    # cheaper, so there is no reason to fake copy just because images are
    # unavailable.
    if settings.foundry_endpoint:
        app.state.text = FoundryTextClient(settings)
        app.state.writer = FoundryCopyWriter(app.state.text)
        logger.warning("copy: live model '%s'", settings.text_deployment)
    else:
        app.state.text = None
        app.state.writer = StubCopyWriter()
        logger.warning("copy: stub (no FOUNDRY_ENDPOINT)")

    if settings.mock:
        logger.warning(
            "MAI_MOCK is on: images are placeholders, no Foundry calls are made"
        )
    else:
        logger.warning(
            "LIVE Foundry: %d of %d calls left today, %d of %d total",
            app.state.budget.remaining_today, settings.daily_limit,
            app.state.budget.remaining_total, settings.total_limit,
        )
    yield

    # Wait for in-flight jobs before tearing down the resources they are using.
    await _drain()
    await app.state.renderer.aclose()
    if getattr(app.state, "text", None) is not None:
        await app.state.text.aclose()
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


@app.get("/api/usage")
async def usage() -> dict:
    """What has been spent, and what is left.

    Worth checking before a big run: `deliverables_expected` in a campaign
    response tells you the output, `image_calls_expected` tells you the cost.
    """
    settings = get_settings()
    budget = getattr(app.state, "budget", None)
    cache = getattr(app.state, "cache", None)

    if budget is None:
        return {
            "mock": True,
            "detail": "mock mode - no calls are billed and nothing is counted",
        }

    return {
        "mock": False,
        "today": {
            "used": budget.usage.today,
            "limit": budget.daily_limit,
            "remaining": budget.remaining_today,
        },
        "total": {
            "used": budget.usage.total,
            "limit": budget.total_limit,
            "remaining": budget.remaining_total,
        },
        "cache": {
            "enabled": settings.cache_enabled,
            "hits": cache.stats.hits if cache else 0,
            "misses": cache.stats.misses if cache else 0,
            "calls_saved": cache.stats.calls_saved if cache else 0,
        },
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
                "bcp47": loc.bcp47,
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

    if request.reference_mode == "edit":
        if not request.reference_id:
            raise HTTPException(400, "edit mode needs a reference_id")
        # Edit mode reproduces the uploaded image pixel-for-pixel, so the user
        # has to assert they may use it. This is not boilerplate.
        if not request.rights_confirmed:
            raise HTTPException(
                400,
                "edit mode requires rights_confirmed: you must have the rights "
                "to the image you uploaded",
            )

    job = Job(id=uuid.uuid4().hex[:12])
    JOBS[job.id] = job
    _spawn(job, request)

    # One generation per format, or exactly one in economy mode. Every locale
    # reuses whichever base it lands on.
    expected_calls = (
        1 if (request.economy and len(request.formats) > 1) else len(request.formats)
    )
    return {
        "job_id": job.id,
        "state": job.state,
        "image_calls_expected": expected_calls,
        "deliverables_expected": len(request.formats) * len(request.locales),
        "economy": request.economy,
        "reference_mode": request.reference_mode if request.reference_id else None,
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
        payload["calls_saved"] = job.result.calls_saved
        payload["economy"] = job.result.economy
        if job.result.fidelity is not None:
            report = job.result.fidelity
            payload["fidelity"] = {
                "subject_kind": report.kind.value,
                "passed": report.passed,
                "similarity": round(report.similarity, 4),
                "extent_overlap": round(report.extent_iou, 3),
                "detail_change": round(report.density_delta, 4),
                "summary": report.describe(),
                "reason": report.reason,
            }
        payload["master_format"] = job.result.master_format
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
        if job.result.copy_pack is not None:
            payload["copy"] = {
                key: {
                    "headline": c.headline,
                    "subhead": c.subhead,
                    "cta": c.cta,
                    "caption": c.caption,
                    "hashtags": list(c.hashtags),
                    "alt_text": c.alt_text,
                    "back_translation": c.back_translation,
                    "needs_review": c.needs_review,
                }
                for key, c in job.result.copy_pack.by_locale.items()
            }
    if job.bundle is not None:
        payload["bundle"] = f"/api/jobs/{job.id}/bundle"
    return payload


@app.get("/api/jobs/{job_id}/image/{filename}")
async def job_image(job_id: str, filename: str) -> FileResponse:
    """Serve one rendered variant.

    The filename is confined to this job's own render directory and resolved
    before use, so a traversal attempt cannot reach outside it.
    """
    settings = get_settings()
    directory = (settings.storage_root / "renders" / job_id).resolve()
    path = (directory / filename).resolve()
    if not str(path).startswith(str(directory)) or not path.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="image/png")


@app.get("/api/jobs/{job_id}/bundle")
async def download_bundle(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None or job.bundle is None:
        raise HTTPException(404, "no bundle for this job")
    return FileResponse(
        job.bundle, media_type="application/zip", filename=f"campaign-{job_id}.zip"
    )


@app.post("/api/uploads/reference")
async def upload_reference(file: UploadFile) -> dict:
    """Store a reference image and report what we can read from it.

    The style is measured here, locally, with no model call — so the user sees
    immediately what "use as inspiration" is actually going to carry across.
    """
    settings = get_settings()
    data = await file.read()
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            prepared = image.convert("RGB")
    except Exception as exc:
        raise HTTPException(400, f"not a readable image: {exc}") from exc

    reference_id = uuid.uuid4().hex[:12]
    path = settings.storage_root / "uploads" / f"ref_{reference_id}.png"
    # Re-encoded, which normalises the format and strips EXIF or any embedded
    # payload from a file we did not create.
    prepared.save(path, format="PNG")

    profile = extract_style(prepared)
    return {
        "reference_id": reference_id,
        "width": prepared.width,
        "height": prepared.height,
        "orientation": profile.orientation,
        "style": {
            "palette": list(profile.palette_names),
            "palette_hex": list(profile.palette_hex),
            "brightness": profile.brightness,
            "contrast": profile.contrast,
            "saturation": profile.saturation,
            "temperature": profile.temperature,
            "lighting": profile.describe_lighting(),
            "mood": list(profile.describe_mood()),
        },
        "modes": {
            "inspiration": "Match this look, build a fresh image around your subject.",
            "edit": "Keep this exact photo and change what you describe. "
                    "Requires rights_confirmed.",
        },
    }


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
        writer=app.state.writer,
        storage=settings.storage_root,
    )
    try:
        job.state = "generating"
        job.logs.append(
            f"one generation per format ({len(request.formats)}), "
            f"{len(request.locales)} locales composited from each"
        )

        # Inferred from the user's own words when not set explicitly. The
        # inference is a convenience for the UI, never the only thing standing
        # between a model and a misrepresented building -- an explicit
        # subject_kind always wins.
        subject_kind = request.subject_kind or detect_kind(request.brief)
        brief = CreativeBrief(
            subject=request.brief,
            negative_space=NegativeSpace(region="bottom_left", coverage_pct=32),
            subject_kind=subject_kind,
            preserve_subject=request.preserve_subject,
        )
        if request.reference_id and request.reference_mode == "edit":
            job.logs.append(
                f"preserving the {subject_kind.value} subject"
                if request.preserve_subject
                else "subject preservation OFF - the subject may be redesigned"
            )
        strategy = CreativeStrategy(
            proposition=request.proposition,
            benefit=request.benefit,
            occasion=request.occasion,
            occasion_by_locale=request.occasion_by_locale,
            facts=tuple(request.facts),
        )
        brand = BrandKit(name=request.brand_name, mandatory_line=request.mandatory_line)

        # Reference image, if one was uploaded.
        reference_bytes: bytes | None = None
        if request.reference_id:
            ref_path = (
                settings.storage_root / "uploads" / f"ref_{request.reference_id}.png"
            )
            if not ref_path.is_file():
                raise FileNotFoundError(f"reference {request.reference_id} not found")

            if request.reference_mode == "inspiration":
                # Measure the look and fold it into the brief, then run the
                # ordinary text path. The output then obeys our layout, aspect
                # ratio and safe zones, which edit mode cannot guarantee.
                with Image.open(ref_path) as reference:
                    brief = apply_style(brief, extract_style(reference))
                job.logs.append(
                    f"reference used as inspiration: {', '.join(brief.palette_names)}"
                )
            else:
                reference_bytes = ref_path.read_bytes()
                job.logs.append("reference edited directly; rights confirmed by user")

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
            economy=request.economy,
            reference=reference_bytes,
            edit_instruction=request.edit_instruction,
        )

        if result.fidelity is not None and not result.fidelity.passed:
            reason = f"subject fidelity failed: {result.fidelity.reason}"
            job.logs.append(reason)
            for variant in result.variants:
                variant.needs_review = True
                variant.review_reasons = variant.review_reasons + (reason,)

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

    except BudgetExceeded as exc:
        # Distinct from a failure: nothing is broken, we deliberately stopped
        # short of spending more credit.
        job.state = "budget_exceeded"
        job.error = str(exc)
        job.logs.append("refused before billing; no credit was spent")
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
