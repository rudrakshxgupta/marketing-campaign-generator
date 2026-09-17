"""The only place in the codebase that talks to MAI-Image-2.6.

MAI image models are in public preview: no SLA, and the API surface may change.
Keeping every call behind this one module keeps the blast radius of that small.

API shape (verified against Microsoft Learn):

    POST {endpoint}/mai/v1/images/generations   JSON
        {model, prompt, width, height, auto_aspect_ratio, web_grounding}
    POST {endpoint}/mai/v1/images/edits         multipart/form-data
        model, prompt, image=<JPEG|PNG>

    -> {"data": [{"b64_json": "<base64 PNG>"}]}

Note that `width`/`height` are parameters of the *generations* endpoint only.
The edits endpoint takes no dimensions, so callers must pre-crop the reference
to the target aspect and crop the response again afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.config import Settings
from app.foundry.ratelimit import TokenBucket, backoff_delay
from app.imaging.dimensions import is_legal

logger = logging.getLogger(__name__)

#: Statuses worth another attempt. 400/401/404 are our own bugs (bad
#: dimensions, bad credentials, wrong deployment name) and retrying them just
#: burns a quota we do not have.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class MaiError(RuntimeError):
    """A MAI call failed in a way the caller should surface."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MaiRateLimited(MaiError):
    """Retries were exhausted while the deployment kept returning 429."""


@dataclass(frozen=True)
class ImageResult:
    png: bytes
    width: int
    height: int
    deployment: str
    attempts: int
    waited_s: float


class ImageBackend(Protocol):
    """What the pipeline needs from an image generator.

    Implemented by both the real client and the mock, so every stage downstream
    is testable without a subscription or a 2 RPM quota.
    """

    async def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        draft: bool = False,
        auto_aspect_ratio: bool = False,
        web_grounding: bool = False,
    ) -> ImageResult: ...

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
    ) -> ImageResult: ...


def _decode(payload: dict) -> bytes:
    data = payload.get("data") or []
    for item in data:
        if "b64_json" in item:
            return base64.b64decode(item["b64_json"])
    raise MaiError(f"no image in response (keys: {sorted(payload)})")


class MaiImageClient:
    """Live MAI-Image-2.6 client with rate limiting and 429 backoff."""

    def __init__(
        self,
        settings: Settings,
        *,
        bucket: TokenBucket | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._bucket = bucket or TokenBucket(settings.mai_rpm)
        self._client = client
        self._owns_client = client is None
        self._token_provider = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._settings.request_timeout_s
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _headers(self) -> dict[str, str]:
        if self._settings.foundry_api_key:
            return {"api-key": self._settings.foundry_api_key}

        # Entra ID. Imported lazily so the mock path needs no azure-identity.
        if self._token_provider is None:
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
            self._token_provider = self._credential
        token = await self._token_provider.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        return {"Authorization": f"Bearer {token.token}"}

    def _deployment(self, draft: bool) -> str:
        return (
            self._settings.draft_deployment
            if draft
            else self._settings.image_deployment
        )

    async def _post(
        self,
        url: str,
        *,
        deployment: str,
        json: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
    ) -> ImageResult:
        client = await self._http()
        total_wait = 0.0
        last_status: int | None = None

        for attempt in range(1, self._settings.max_attempts + 1):
            total_wait += await self._bucket.acquire()
            headers = await self._headers()

            try:
                response = await client.post(
                    url, headers=headers, json=json, data=data, files=files
                )
            except httpx.RequestError as exc:
                if attempt == self._settings.max_attempts:
                    raise MaiError(f"network failure calling MAI: {exc}") from exc
                delay = backoff_delay(attempt)
                logger.warning(
                    "MAI network error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt, self._settings.max_attempts, delay, exc,
                )
                await asyncio.sleep(delay)
                total_wait += delay
                continue

            if response.status_code == 200:
                png = _decode(response.json())
                return ImageResult(
                    png=png,
                    width=0,  # filled in by the caller from the decoded image
                    height=0,
                    deployment=deployment,
                    attempts=attempt,
                    waited_s=total_wait,
                )

            last_status = response.status_code
            detail = response.text[:400]

            if response.status_code not in RETRYABLE_STATUSES:
                raise MaiError(
                    _explain(response.status_code, detail),
                    status=response.status_code,
                )

            if response.status_code == 429:
                # Our accounting was optimistic; surrender the burst allowance
                # rather than spending it on another rejected request.
                await self._bucket.drain()

            if attempt == self._settings.max_attempts:
                break

            retry_after = _retry_after(response)
            delay = backoff_delay(attempt, retry_after=retry_after)
            logger.warning(
                "MAI %d (attempt %d/%d), retrying in %.1fs",
                response.status_code, attempt, self._settings.max_attempts, delay,
            )
            await asyncio.sleep(delay)
            total_wait += delay

        if last_status == 429:
            raise MaiRateLimited(
                f"rate limited after {self._settings.max_attempts} attempts; "
                f"deployment {deployment} is at its RPM ceiling",
                status=429,
            )
        raise MaiError(
            f"MAI failed after {self._settings.max_attempts} attempts "
            f"(last status {last_status})",
            status=last_status,
        )

    async def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        draft: bool = False,
        auto_aspect_ratio: bool = False,
        web_grounding: bool = False,
    ) -> ImageResult:
        if not is_legal(width, height):
            # Catch this here rather than paying a round trip to learn it.
            raise MaiError(
                f"{width}x{height} violates MAI limits "
                f"(sides >= 768, product <= 1048576); "
                f"use imaging.dimensions.resolve_dimensions()"
            )

        deployment = self._deployment(draft)
        body = {
            "model": deployment,
            "prompt": prompt,
            "width": width,
            "height": height,
        }
        # 2.6-only flags; both off in production. auto_aspect_ratio would let
        # the model pick a ratio, which breaks logo placement, safe zones and
        # text boxes at once. web_grounding makes output non-reproducible.
        if auto_aspect_ratio:
            body["auto_aspect_ratio"] = True
        if web_grounding:
            body["web_grounding"] = True

        return await self._post(
            self._settings.generations_url, deployment=deployment, json=body
        )

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
    ) -> ImageResult:
        if mime not in ("image/png", "image/jpeg"):
            raise MaiError(f"edits accepts PNG or JPEG, got {mime}")

        deployment = self._deployment(draft)
        suffix = "png" if mime == "image/png" else "jpg"
        return await self._post(
            self._settings.edits_url,
            deployment=deployment,
            data={"model": deployment, "prompt": prompt},
            files={"image": (f"reference.{suffix}", image, mime)},
        )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _explain(status: int, detail: str) -> str:
    """Turn the documented failure modes into something actionable."""
    hints = {
        400: (
            "bad request -- usually width/height outside "
            "(>=768 per side, <=1048576 total)"
        ),
        401: "unauthorized -- regenerate the key, or check the Entra token scope "
             "is https://cognitiveservices.azure.com/.default",
        404: "not found -- check the deployment name and endpoint in the "
             "Foundry portal under Deployments",
    }
    hint = hints.get(status)
    return f"MAI returned {status}" + (f" ({hint})" if hint else "") + f": {detail}"
