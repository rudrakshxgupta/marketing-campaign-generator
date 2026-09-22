"""FLUX.2 client, via the Black Forest Labs provider API on Foundry.

A second implementation of :class:`~app.foundry.image_client.ImageBackend`,
sitting alongside the MAI client. Nothing downstream knows which one is in use
-- the pipeline, the overlays, the logo compositing and the fidelity checks are
all model-agnostic by design, which is exactly the scenario this arrangement
was built for.

It differs from MAI in five ways that matter, and every one of them is a place
a copy-paste of the MAI client would have broken:

* **Different host.** ``<resource>.api.cognitive.microsoft.com``, not
  ``<resource>.services.ai.azure.com``.
* **Different auth header.** ``Authorization: Bearer <key>`` even when the key
  is an API key -- MAI uses ``api-key:``.
* **Model path in the URL**, and it is not the model id: ``FLUX.2-pro``
  deploys at ``flux-2-pro``.
* **4 MP output** against MAI's 1 MP. Instagram's largest format is 1.46 MP, so
  FLUX generates every format natively and nothing needs upscaling.
* **Editing is base64 JSON**, not multipart, and takes up to eight reference
  images.

One operational caveat from the docs, carried here deliberately: Foundry
provides **no built-in content filtering** for FLUX at deployment time. Whatever
safety gating this product needs has to be ours.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from app.config import Settings
from app.foundry.image_client import ImageResult, MaiError, MaiRateLimited
from app.foundry.ratelimit import TokenBucket, backoff_delay

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Model id -> URL path. The docs are explicit that these differ, and getting
#: it wrong produces a 404 that reads like a missing deployment.
#:
#: There is a second, undocumented trap here. The URL carries the *path*
#: (``flux-2-pro``), but the service resolves that back to the *model id* and
#: then looks for a deployment named after it -- the 404 body says
#: "The API deployment flux.2-pro does not exist", with a dot.
#:
#: So a deployment named ``flux-2-pro`` is never found, however correct the URL
#: is. **Name the deployment after the model id**: ``FLUX.2-pro``.
MODEL_PATHS = {
    "FLUX.2-pro": "flux-2-pro",
    "FLUX.2-flex": "flux-2-flex",
    "FLUX.1-Kontext-pro": "flux-kontext-pro",
    "FLUX-1.1-pro": "flux-pro-1.1",
}


def model_path(model: str) -> str:
    """URL path for a model id, tolerating a deployment named after the path."""
    if model in MODEL_PATHS:
        return MODEL_PATHS[model]
    if model in MODEL_PATHS.values():
        return model
    # Best effort: lowercase and swap separators, which is the pattern above.
    return model.lower().replace(".", "-").replace("_", "-")


class FluxImageClient:
    """Implements :class:`~app.foundry.image_client.ImageBackend`."""

    def __init__(
        self,
        settings: Settings,
        *,
        bucket: TokenBucket | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        # FLUX.2-pro allows 15 RPM at the lowest tier, well above MAI's 2, but
        # the configured value still wins so a tightened budget is respected.
        self._bucket = bucket or TokenBucket(settings.flux_rpm)
        self._client = client
        self._owns_client = client is None
        self._credential = None

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.request_timeout_s)
        return self._client

    async def _headers(self) -> dict[str, str]:
        # Bearer for both auth modes here -- unlike MAI, which wants the key in
        # an `api-key` header. Sending MAI's header shape gets a 401.
        if self._settings.foundry_api_key:
            token = self._settings.foundry_api_key
        else:
            if self._credential is None:
                from azure.identity.aio import DefaultAzureCredential

                self._credential = DefaultAzureCredential()
            got = await self._credential.get_token(
                "https://cognitiveservices.azure.com/.default"
            )
            token = got.token
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _url(self, model: str) -> str:
        return (
            f"{self._settings.flux_endpoint}/providers/blackforestlabs/v1/"
            f"{model_path(model)}?api-version=preview"
        )

    async def _post(self, model: str, body: dict) -> ImageResult:
        client = await self._http()
        waited = 0.0
        last_status: int | None = None

        for attempt in range(1, self._settings.max_attempts + 1):
            waited += await self._bucket.acquire()
            headers = await self._headers()

            try:
                response = await client.post(self._url(model), headers=headers, json=body)
            except httpx.RequestError as exc:
                if attempt == self._settings.max_attempts:
                    raise MaiError(f"network failure calling FLUX: {exc}") from exc
                delay = backoff_delay(attempt)
                await asyncio.sleep(delay)
                waited += delay
                continue

            if response.status_code == 200:
                png = await self._extract(response.json(), client)
                return ImageResult(
                    png=png, width=0, height=0,
                    deployment=model, attempts=attempt, waited_s=waited,
                )

            last_status = response.status_code
            detail = response.text[:400]

            if response.status_code not in RETRYABLE_STATUSES:
                raise MaiError(_explain(response.status_code, detail),
                               status=response.status_code)
            if response.status_code == 429:
                await self._bucket.drain()
            if attempt == self._settings.max_attempts:
                break

            delay = backoff_delay(attempt, retry_after=_retry_after(response))
            logger.warning("FLUX %d (attempt %d), retrying in %.1fs",
                           response.status_code, attempt, delay)
            await asyncio.sleep(delay)
            waited += delay

        if last_status == 429:
            raise MaiRateLimited(
                f"FLUX rate limited after {self._settings.max_attempts} attempts",
                status=429,
            )
        raise MaiError(f"FLUX failed after {self._settings.max_attempts} attempts "
                       f"(last status {last_status})", status=last_status)

    async def _extract(self, payload: dict, client: httpx.AsyncClient) -> bytes:
        """Pull image bytes out of a response.

        The API may return base64 *or* a URL depending on size and model, so
        both are handled rather than assuming whichever one showed up first in
        testing.
        """
        candidates: list = []
        if isinstance(payload.get("data"), list):
            candidates.extend(payload["data"])
        candidates.append(payload)
        if isinstance(payload.get("result"), dict):
            candidates.append(payload["result"])

        for item in candidates:
            if not isinstance(item, dict):
                continue
            for key in ("b64_json", "image", "image_base64", "sample_base64"):
                if item.get(key):
                    return base64.b64decode(item[key])
            for key in ("url", "sample", "image_url"):
                value = item.get(key)
                if isinstance(value, dict):
                    value = value.get("url")
                if isinstance(value, str) and value.startswith("http"):
                    fetched = await client.get(value)
                    fetched.raise_for_status()
                    return fetched.content

        raise MaiError(
            f"no image in FLUX response (keys: {sorted(payload)[:12]})"
        )

    # -- ImageBackend ------------------------------------------------------

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
        model = self._settings.flux_draft_model if draft else self._settings.flux_model
        body = {
            "model": model,
            "prompt": prompt,
            "width": width,
            "height": height,
            # PNG keeps the overlay compositing lossless. JPEG artifacts would
            # be baked in under the text before we ever draw it.
            "output_format": "png",
            "num_images": 1,
        }
        return await self._post(model, body)

    async def edit(
        self,
        *,
        prompt: str,
        image: bytes,
        mime: str = "image/png",
        draft: bool = False,
    ) -> ImageResult:
        model = self._settings.flux_draft_model if draft else self._settings.flux_model
        body = {
            "model": model,
            "prompt": prompt,
            "output_format": "png",
            # Reference images go inline as base64 here, not as multipart.
            "input_image": base64.b64encode(image).decode("ascii"),
        }
        return await self._post(model, body)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _explain(status: int, detail: str) -> str:
    hints = {
        400: "bad request -- check width/height are within the model's pixel "
             "budget and the body shape matches the BFL provider API",
        401: "unauthorized -- FLUX wants 'Authorization: Bearer <key>', not "
             "MAI's 'api-key' header",
        404: "not found -- the URL uses the model PATH, which differs from the "
             "model id (FLUX.2-pro deploys at flux-2-pro)",
    }
    hint = hints.get(status)
    return f"FLUX returned {status}" + (f" ({hint})" if hint else "") + f": {detail}"
