"""Chat-completions client for the Foundry text deployment.

Separate from the image client on purpose. Text quota is measured in hundreds
of thousands of tokens while image quota is measured in single requests per
minute, so the two must never share a limiter or a budget -- a burst of copy
generation cannot be allowed to starve an image render, and a text failure
must not consume an image credit.

Text is also orders of magnitude cheaper. A full campaign's copy across seven
languages is a few thousand tokens; a single image costs more than hundreds of
those. That asymmetry is why copy is generated *first* in the pipeline: it
costs almost nothing, so a failure there should never waste an image.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

#: Retry on transient statuses only. 400/401/404 are our own bugs -- a bad
#: schema, bad credentials, a wrong deployment name -- and retrying them just
#: burns time.
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


class TextError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class TextResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    attempts: int = 1

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class FoundryTextClient:
    """Minimal chat-completions client with JSON-schema structured output."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 4,
    ) -> None:
        self._settings = settings
        self._client = client
        self._owns_client = client is None
        self._max_attempts = max_attempts
        self._credential = None
        #: Running total, so a campaign can report what its copy actually cost.
        self.tokens_used = 0

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._settings.request_timeout_s)
        return self._client

    async def _headers(self) -> dict[str, str]:
        if self._settings.foundry_api_key:
            return {
                "api-key": self._settings.foundry_api_key,
                "Content-Type": "application/json",
            }
        # Entra ID, same scope as the image client. Imported lazily so the
        # stub path needs no azure-identity.
        if self._credential is None:
            from azure.identity.aio import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        token = await self._credential.get_token(
            "https://cognitiveservices.azure.com/.default"
        )
        return {
            "Authorization": f"Bearer {token.token}",
            "Content-Type": "application/json",
        }

    @property
    def url(self) -> str:
        return (
            f"{self._settings.foundry_endpoint}/openai/deployments/"
            f"{self._settings.text_deployment}/chat/completions"
            f"?api-version={self._settings.text_api_version}"
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict | None = None,
        schema_name: str = "response",
        max_tokens: int = 16000,
        reasoning_effort: str | None = "low",
    ) -> TextResult:
        """One chat completion, optionally constrained to a JSON schema.

        ``schema`` uses structured outputs rather than "please return JSON",
        because a model that returns *almost* valid JSON is worse than one that
        fails loudly -- the pipeline downstream would parse garbage.

        The token budget is deliberately generous. Reasoning models spend part
        of ``max_completion_tokens`` on hidden reasoning *before* emitting
        anything, so running out mid-reasoning returns HTTP 200,
        ``finish_reason: stop`` and **empty content** -- a success response with
        nothing in it. Seven locales at a tight budget hit exactly that.

        ``reasoning_effort`` is capped for the same reason: writing ad copy
        does not need deep deliberation, and every reasoning token is one not
        spent on output.
        """
        body: dict = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_completion_tokens": max_tokens,
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            }

        client = await self._http()
        last_status: int | None = None

        for attempt in range(1, self._max_attempts + 1):
            headers = await self._headers()
            try:
                response = await client.post(self.url, headers=headers, json=body)
            except httpx.RequestError as exc:
                if attempt == self._max_attempts:
                    raise TextError(f"network failure calling the text model: {exc}") from exc
                await asyncio.sleep(_backoff(attempt))
                continue

            if response.status_code == 200:
                payload = response.json()
                choice = (payload.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                usage = payload.get("usage") or {}
                result = TextResult(
                    content=content,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    attempts=attempt,
                )
                self.tokens_used += result.total_tokens
                return result

            last_status = response.status_code
            detail = response.text[:400]

            if response.status_code not in RETRYABLE:
                raise TextError(_explain(response.status_code, detail),
                                status=response.status_code)
            if attempt == self._max_attempts:
                break

            delay = _backoff(attempt, retry_after=_retry_after(response))
            logger.warning(
                "text model %d (attempt %d/%d), retrying in %.1fs",
                response.status_code, attempt, self._max_attempts, delay,
            )
            await asyncio.sleep(delay)

        raise TextError(
            f"text model failed after {self._max_attempts} attempts "
            f"(last status {last_status})",
            status=last_status,
        )

    async def complete_json(self, **kwargs) -> dict:
        """As :meth:`complete`, but parsed.

        A model that returns almost-valid JSON must fail here rather than
        halfway through the pipeline, where the error would point at the wrong
        thing entirely.
        """
        result = await self.complete(**kwargs)
        if not result.content.strip():
            raise TextError(
                "model returned empty content. On a reasoning model this "
                "usually means the token budget was exhausted before any "
                f"output was emitted ({result.completion_tokens} completion "
                "tokens used). Raise max_tokens or lower reasoning_effort."
            )
        try:
            return json.loads(result.content)
        except json.JSONDecodeError as exc:
            preview = result.content[:200].replace("\n", " ")
            raise TextError(
                f"model did not return valid JSON: {exc}. Starts: {preview!r}"
            ) from exc


def _backoff(attempt: int, *, retry_after: float | None = None) -> float:
    """Full-jitter exponential backoff, with Retry-After as a floor."""
    ceiling = min(30.0, 2.0 * (2 ** (attempt - 1)))
    delay = random.uniform(0.0, ceiling)
    if retry_after is not None:
        delay = max(delay, retry_after)
    return min(delay, 60.0)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _explain(status: int, detail: str) -> str:
    hints = {
        400: "bad request -- usually an invalid JSON schema or an unsupported "
             "parameter for this model",
        401: "unauthorized -- check the key, or that az login has Cognitive "
             "Services User on the resource",
        404: "not found -- check MAI_TEXT_DEPLOYMENT matches a deployment name "
             "in the Foundry portal",
    }
    hint = hints.get(status)
    return f"text model returned {status}" + (f" ({hint})" if hint else "") + f": {detail}"
