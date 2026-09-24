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
from app.copy.languages import DEFAULT_LOCALES, LOCALES, get_locale
from app.copy.brief_compiler import compile_brief, fallback_brief
from app.copy.fidelity import SubjectKind, detect_kind
from app.copy.strategy import BrandKit, CreativeBrief, CreativeStrategy, NegativeSpace
from app.foundry.budget import BudgetExceeded
from app.foundry.factory import build_image_backend
from app.foundry.image_client import MaiRateLimited, PromptBlocked
from app.copy.transcreate import FoundryCopyWriter, StubCopyWriter
from app.foundry.text_client import FoundryTextClient
from app.imaging.dimensions import FORMATS
from app.imaging.overlay import OverlayRenderer
from app.imaging.style import apply_style, extract_style, merge_styles
from app.pipeline import CampaignPipeline, CampaignResult, export_bundle

logger = logging.getLogger(__name__)

JobState = Literal[
    "queued", "compiling", "generating", "compositing", "complete",
    "failed", "rate_limited", "budget_exceeded", "blocked",
]

#: States a job can still move out of. Everything else is terminal.
#:
#: Expressed as the *running* set rather than the finished one so that adding
#: a new way to stop cannot leave a client polling forever -- the failure mode
#: of an allow-list of endings is a spinner that never resolves, and it shows
#: up as "nothing appeared" rather than as an error anyone can act on.
RUNNING_STATES = frozenset({"queued", "compiling", "generating", "compositing"})


@dataclass
class Job:
    id: str
    state: JobState = "queued"
    progress: int = 0
    error: str | None = None
    result: CampaignResult | None = None
    bundle: Path | None = None
    logs: list[str] = field(default_factory=list)
    #: Kept so a review edit can re-render with the same brand and logo it was
    #: built with, rather than defaults that would quietly change the design.
    brand: BrandKit | None = None
    logo_path: Path | None = None
    #: What the compiler turned the product name into, so the user can see and
    #: override it rather than wondering where the picture came from.
    brief_summary: dict | None = None
    #: The prompt that was sent, kept even when the run failed. A refused
    #: prompt is the one worth reading.
    prompt: str | None = None


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
    #: The only thing the user has to supply: what they sell.
    #:
    #: Everything else -- what the photograph shows, the proposition, the
    #: benefit, the occasion -- is compiled from this by the model. Asking a
    #: marketer to write an image prompt is asking them to do the part they
    #: are worst at.
    product: str = Field(..., description="What you sell, in plain words")

    brand_name: str = "ACME"
    mandatory_line: str = ""

    #: The ONLY claims the copy may state. Nothing outside this list is ever
    #: invented -- a model that helpfully adds "50% off" has written a false
    #: advertisement.
    facts: list[str] = Field(default_factory=list)

    #: Optional overrides. Left empty, the model decides.
    occasion: str = ""
    formats: list[str] = Field(default_factory=lambda: ["portrait"])
    locales: list[str] = Field(default_factory=lambda: list(DEFAULT_LOCALES))
    #: Per-locale occasion overrides. Usually left empty -- the compiler sets
    #: these, because the right festival differs by region and it knows that.
    occasion_by_locale: dict[str, str] = Field(default_factory=dict)
    #: Generate one tall master and crop every other format out of it, turning
    #: N generations into one. Costs some sharpness and composition control,
    #: so it is opt-in rather than a silent default.
    economy: bool = False

    # --- brand assets ------------------------------------------------------
    #: From POST /api/uploads/logo.
    #:
    #: Required to get the logo you just uploaded. The previous code took
    #: whichever ``logo_*.png`` sorted first on disk, which is stable, wrong,
    #: and silently wrong: with two uploads in the storage directory the
    #: campaign gets a random one of them and nothing reports a problem.
    logo_id: str | None = None

    # --- reference images --------------------------------------------------
    #: From POST /api/uploads/reference. Kept for single-reference callers.
    reference_id: str | None = None
    #: Several references, used as a moodboard. One photograph pins a look
    #: only by accident -- what a person means by "this kind of thing" is the
    #: qualities several images share, and a set makes that explicit.
    #:
    #: In edit mode the first is the subject and the rest are context.
    reference_ids: list[str] = Field(default_factory=list)
    #: What to do with the uploaded photograph.
    #:
    #: - "exact": keep the subject pixel-faithful and restage what is around
    #:   it -- sky, background, setting, light.
    #: - "enhance": improve the photograph itself (light, clarity, clutter)
    #:   while keeping the same subject.
    #: - "inspiration": borrow only the look and generate a fresh image. The
    #:   subject will NOT be the user's.
    #:
    #: "edit" is accepted as the old name for "exact".
    #:
    #: Default to inspiration: the other two derive the output from the input,
    #: so if the user uploaded someone else's photograph they produce a
    #: derivative work. They also inherit the input's composition, which means
    #: our safe zones are no longer guaranteed.
    reference_mode: Literal["inspiration", "exact", "enhance", "edit"] = "inspiration"
    #: What to change, for edit mode.
    edit_instruction: str = ""
    #: Required for edit mode. The user asserting they may use this image.
    rights_confirmed: bool = False

    @property
    def uses_the_actual_photo(self) -> bool:
        """Whether the output is derived from the upload.

        Both "exact" and "enhance" reproduce the user's photograph, so both
        need the rights attestation and both get a fidelity check. Only
        "inspiration" generates something new.
        """
        return self.resolved_mode in ("exact", "enhance")

    @property
    def resolved_mode(self) -> str:
        """The mode with the legacy name folded in."""
        return "exact" if self.reference_mode == "edit" else self.reference_mode

    @property
    def all_reference_ids(self) -> list[str]:
        """Every reference, in order, however it was supplied.

        Callers that only know about the single field keep working, and
        nothing downstream has to remember there are two ways to say this.
        Deduplicated: the same image sent twice is a wasted slot, and in edit
        mode it would weight that reference twice over.
        """
        ordered = ([self.reference_id] if self.reference_id else []) + self.reference_ids
        return list(dict.fromkeys(i for i in ordered if i))

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




@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.renderer = OverlayRenderer()
    backend = build_image_backend(settings)
    app.state.images = backend.images
    app.state.budget = backend.budget
    app.state.cache = backend.cache

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
        "image_backend": settings.image_backend,
        "image_model": (
            settings.flux_model if settings.image_backend == "flux"
            else settings.image_deployment
        ),
        "max_pixels": settings.capabilities.max_pixels,
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

    # Re-read the ledger and the configured ceilings before reporting. The
    # spend guard refreshes both on its own, but only when a call is actually
    # made -- so a limit raised in .env showed the old number here until the
    # next generation, and this is the figure the interface puts on screen.
    budget._reload_limits()
    budget.usage = type(budget.usage).load(budget._ledger)

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


@app.get("/api/classify")
async def classify(product: str = "") -> dict:
    """What kind of subject the product text describes.

    Pure local keyword matching -- no model, no cost, safe to call on every
    keystroke. It exists so the UI can warn *before* an image is spent, and
    the warning it enables is the important one: "inspiration" mode regenerates
    from scratch, so uploading a photo of a real building and leaving the
    default selected produces an advertisement for a property that does not
    exist. That is a legal exposure, and nothing in the old UI said so.

    Advisory only. The pipeline runs :func:`detect_kind` again and takes the
    stricter of its own answer and the model's.
    """
    from app.copy.fidelity import FIDELITY_FLOOR, SubjectKind, detect_kind

    kind = detect_kind(product)
    return {
        "kind": kind.value,
        "fidelity_floor": FIDELITY_FLOOR[kind],
        # Kinds where an invented subject misrepresents something real, rather
        # than merely looking different from what the seller had in mind.
        "prefer_exact": kind in {
            SubjectKind.ARCHITECTURE,
            SubjectKind.JEWELLERY,
            SubjectKind.VEHICLE,
            SubjectKind.PERSON,
        },
    }


@app.get("/api/meta")
async def meta() -> dict:
    """Formats and locales, including the generation size each format needs.

    The frontend renders from this rather than hard-coding sizes, so the
    dimension rules live in exactly one place.
    """
    from app.imaging.dimensions import plan_for

    caps = get_settings().capabilities
    return {
        "backend": {"name": caps.name, "max_pixels": caps.max_pixels},
        "formats": [
            {
                "key": key,
                "target": [plan.target_w, plan.target_h],
                "generate": [plan.gen_w, plan.gen_h],
                "requires_crop": plan.requires_crop,
                "upscale": round(plan.upscale_factor, 3),
            }
            for key, plan in ((k, plan_for(k, caps)) for k in FORMATS)
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

    if request.uses_the_actual_photo:
        if not request.all_reference_ids:
            raise HTTPException(
                400, f"{request.resolved_mode} mode needs a reference_id"
            )
        # Edit mode reproduces the uploaded image pixel-for-pixel, so the user
        # has to assert they may use it. This is not boilerplate.
        if not request.rights_confirmed:
            raise HTTPException(
                400,
                f"{request.resolved_mode} mode requires rights_confirmed: you "
                f"must have the rights to the image you uploaded",
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
        "reference_mode": (
            request.resolved_mode if request.all_reference_ids else None
        ),
        "references": len(request.all_reference_ids),
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
    if job.prompt:
        payload["prompt"] = job.prompt
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
        payload["notes"] = list(job.result.notes)
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
        payload["brief_summary"] = job.brief_summary
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
                    "label": get_locale(key).label,
                    "bcp47": get_locale(key).bcp47,
                }
                for key, c in job.result.copy_pack.by_locale.items()
            }
    if job.bundle is not None:
        payload["bundle"] = f"/api/jobs/{job.id}/bundle"
    return payload


class ReviewRequest(BaseModel):
    """A human correcting or approving one locale's copy."""

    headline: str | None = None
    subhead: str | None = None
    cta: str | None = None
    caption: str | None = None
    hashtags: list[str] | None = None
    #: Approving clears the review flag. Approved copy is never regenerated.
    approve: bool = True


@app.patch("/api/jobs/{job_id}/copy/{locale}")
async def review_copy(job_id: str, locale: str, request: ReviewRequest) -> dict:
    """Edit and/or approve one locale, then re-render it.

    Costs **no image quota**: the base image is text-free, so corrected copy is
    re-typeset onto the image already on disk. That is what makes review
    practical -- if a fixed typo cost a rate-limited generation, nobody would
    fix typos.

    This matters most for Bengali, Tamil and Telugu, where Azure OCR cannot
    verify rendered text at all. For those scripts a human approving what they
    can read is the only check there is.
    """
    job = JOBS.get(job_id)
    if job is None or job.result is None or job.result.copy_pack is None:
        raise HTTPException(404, "no such campaign")
    if locale not in job.result.copy_pack.by_locale:
        raise HTTPException(404, f"campaign has no copy for {locale!r}")

    copy = job.result.copy_pack.by_locale[locale]
    for field_name in ("headline", "subhead", "cta", "caption"):
        value = getattr(request, field_name)
        if value is not None:
            setattr(copy, field_name, value)
    if request.hashtags is not None:
        copy.hashtags = tuple(request.hashtags)
    copy.needs_review = not request.approve

    settings = get_settings()
    logo = None
    if job.logo_path and Path(job.logo_path).is_file():
        logo = Image.open(job.logo_path).convert("RGBA")

    pipeline = CampaignPipeline(
        app.state.images,
        renderer=app.state.renderer,
        writer=app.state.writer,
        storage=settings.storage_root,
        capabilities=settings.capabilities,
    )
    try:
        await pipeline.recompose(
            job.result,
            locale_key=locale,
            brand=job.brand or BrandKit(name="ACME"),
            logo=logo,
        )
    finally:
        await pipeline.aclose()

    # The bundle is now stale, so rebuild it with the approved copy.
    job.bundle = export_bundle(
        job.result, settings.storage_root / "exports" / f"{job.id}.zip"
    )
    job.logs.append(
        f"{locale} {'approved' if request.approve else 'edited'} and re-rendered "
        f"(no image call)"
    )

    return {
        "locale": locale,
        "approved": request.approve,
        "needs_review": copy.needs_review,
        "image_calls_used": 0,
        "review_count": len(job.result.review_queue),
    }


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
        capabilities=settings.capabilities,
    )
    try:
        brand = BrandKit(name=request.brand_name, mandatory_line=request.mandatory_line)

        # 1. The reference image, if any. Read first, because its measured look
        #    feeds the compiler -- the brief is written *around* the reference
        #    rather than patched afterwards.
        reference_bytes: bytes | None = None
        extra_reference_bytes: list[bytes] = []
        style_hint: dict | None = None
        ref_paths: list[Path] = []
        for ref_id in request.all_reference_ids:
            candidate = settings.storage_root / "uploads" / f"ref_{ref_id}.png"
            if not candidate.is_file():
                raise FileNotFoundError(f"reference {ref_id} not found")
            ref_paths.append(candidate)

        if ref_paths:
            ref_path = ref_paths[0]

            if request.resolved_mode == "inspiration":
                profiles = []
                for path in ref_paths:
                    with Image.open(path) as reference:
                        profiles.append(extract_style(reference))
                profile = merge_styles(profiles)
                if len(profiles) > 1:
                    job.logs.append(
                        f"{len(profiles)} references merged into one look"
                    )
                style_hint = {
                    "palette": list(profile.palette_names),
                    "brightness": profile.brightness,
                    "contrast": profile.contrast,
                    "temperature": profile.temperature,
                    "lighting": profile.describe_lighting(),
                    "mood": list(profile.describe_mood()),
                }
                job.logs.append(f"reference look: {', '.join(profile.palette_names)}")
            else:
                reference_bytes = ref_path.read_bytes()
                # The first upload is the subject; the rest are context the
                # model may restage *around* it. Order matters here in a way
                # it does not for inspiration, so it is stated in the log
                # rather than left for someone to infer from the output.
                extra_reference_bytes = [p.read_bytes() for p in ref_paths[1:]]
                job.logs.append(
                    "reference edited directly; rights confirmed by user"
                    + (
                        f" ({len(extra_reference_bytes)} further reference(s) "
                        f"as context)"
                        if extra_reference_bytes
                        else ""
                    )
                )

        # 2. Compile the brief. The user gave a product name; this turns it
        #    into a photograph, a proposition and an occasion.
        job.state = "compiling"
        job.logs.append(f"compiling a brief from: {request.product!r}")

        if app.state.text is not None:
            compiled = await compile_brief(
                app.state.text,
                product=request.product,
                brand=brand,
                facts=tuple(request.facts),
                style_hint=style_hint,
            )
        else:
            # No text model reachable. Degrade rather than fail: the pipeline
            # still needs a reserved region and a subject kind, and neither
            # requires a model.
            compiled = fallback_brief(
                request.product, brand, tuple(request.facts), style_hint
            )
            job.logs.append("no text model - using a fallback brief")

        brief, strategy = compiled.brief, compiled.strategy
        job.brief_summary = compiled.summary

        # An explicit subject_kind always wins over the compiler's guess -- the
        # inference is a convenience, never the only thing standing between a
        # model and a misrepresented building.
        if request.subject_kind:
            brief.subject_kind = request.subject_kind
        brief.preserve_subject = request.preserve_subject

        if request.occasion:
            strategy.occasion = request.occasion
        if request.occasion_by_locale:
            strategy.occasion_by_locale.update(request.occasion_by_locale)

        job.logs.append(f"subject: {brief.subject[:90]}")
        job.logs.append(
            f"occasion: {strategy.occasion or '(none)'}"
            + (
                f"  overrides: {strategy.occasion_by_locale}"
                if strategy.occasion_by_locale
                else ""
            )
        )
        if request.uses_the_actual_photo:
            job.logs.append(
                f"preserving the {brief.subject_kind.value} subject"
                if request.preserve_subject
                else "subject preservation OFF - the subject may be redesigned"
            )

        logo_path = None
        if request.logo_id:
            # Named, not guessed. The id is generated server-side and used as
            # a single path segment, so a traversal attempt cannot escape the
            # uploads directory.
            candidate = (
                settings.storage_root / "uploads" / f"logo_{request.logo_id}.png"
            ).resolve()
            uploads = (settings.storage_root / "uploads").resolve()
            if str(candidate).startswith(str(uploads)) and candidate.is_file():
                logo_path = candidate
            else:
                # Loud, because a silently missing logo is exactly the failure
                # that gets noticed only after the campaign is published.
                job.logs.append(f"logo {request.logo_id} not found - none applied")
        logo = Image.open(logo_path).convert("RGBA") if logo_path else None
        if logo is not None:
            job.logs.append(f"logo: {logo.width}x{logo.height}, composited from file")

        job.state = "generating"
        job.logs.append(
            f"one generation per format ({len(request.formats)}), "
            f"{len(request.locales)} locales composited from each"
        )

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
            extra_references=tuple(extra_reference_bytes),
            reference_mode=request.resolved_mode,
            edit_instruction=request.edit_instruction,
        )

        job.prompt = result.prompt
        for note in result.notes:
            job.logs.append(note)

        if result.fidelity is not None and not result.fidelity.passed:
            reason = f"subject fidelity failed: {result.fidelity.reason}"
            job.logs.append(reason)
            for variant in result.variants:
                variant.needs_review = True
                variant.review_reasons = variant.review_reasons + (reason,)

        job.brand = brand
        job.logo_path = logo_path
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
    except PromptBlocked as exc:
        # Its own state because the remedy is different from every other
        # failure: nothing is broken and nothing was billed, but a phrase in
        # the generated brief has to change before this will ever succeed.
        job.state = "blocked"
        job.error = str(exc)
        job.logs.append(
            "the image service refused the prompt; no image was billed"
        )
        job.logs.append(
            "try naming the product more plainly, or edit the brief's wording"
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the client
        logger.exception("job %s failed", job.id)
        job.state = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        # Whatever happened, keep the prompt. Debugging a refusal without the
        # text that was refused is guesswork.
        job.prompt = job.prompt or pipeline.last_prompt or None
        await pipeline.aclose()
