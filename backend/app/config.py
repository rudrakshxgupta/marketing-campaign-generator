"""Application settings, read from the environment.

Everything here has a working default except the Foundry endpoint, so the app
boots and the whole pipeline runs in mock mode on a clean checkout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # --- Foundry -----------------------------------------------------------
    #: e.g. https://mcg-foundry.services.ai.azure.com  (no trailing slash)
    foundry_endpoint: str = field(
        default_factory=lambda: os.environ.get("FOUNDRY_ENDPOINT", "").rstrip("/")
    )
    #: API key auth. Leave unset to use Entra ID via DefaultAzureCredential.
    foundry_api_key: str | None = field(
        default_factory=lambda: os.environ.get("FOUNDRY_API_KEY") or None
    )
    #: Deployment names, not model names.
    image_deployment: str = field(
        default_factory=lambda: os.environ.get("MAI_IMAGE_DEPLOYMENT", "mai-image-26")
    )
    #: MAI-Image-2.6-Flash gets its own RPM bucket, so drafts routed here do
    #: not starve final renders.
    draft_deployment: str = field(
        default_factory=lambda: os.environ.get(
            "MAI_DRAFT_DEPLOYMENT", "mai-image-26-flash"
        )
    )
    #: Chat deployment for copy and transcreation. A separate deployment from
    #: the image models, with its own quota -- text is measured in hundreds of
    #: thousands of tokens, images in requests per minute, and a burst of copy
    #: must never starve an image render.
    text_deployment: str = field(
        default_factory=lambda: os.environ.get("MAI_TEXT_DEPLOYMENT", "gpt-5-mini")
    )
    text_api_version: str = field(
        default_factory=lambda: os.environ.get("MAI_TEXT_API_VERSION", "2024-12-01-preview")
    )

    # --- Rate limiting -----------------------------------------------------
    #: MAI Global Standard allows 2-12 RPM by tier. Default to the safest.
    mai_rpm: float = field(
        default_factory=lambda: float(os.environ.get("MAI_RPM", "2"))
    )
    max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("MAI_MAX_ATTEMPTS", "5"))
    )
    request_timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("MAI_TIMEOUT_S", "180"))
    )

    # --- Modes -------------------------------------------------------------
    #: Return deterministic placeholder images instead of calling Foundry. The
    #: entire pipeline is buildable and testable without an Azure subscription,
    #: and the test suite never depends on a 2 RPM quota.
    mock: bool = field(default_factory=lambda: _flag("MAI_MOCK", True))

    # --- Spend controls ----------------------------------------------------
    #: Serve byte-identical repeat requests from disk. Users re-click
    #: "Generate" constantly; without this each click is a fresh charge.
    cache_enabled: bool = field(default_factory=lambda: _flag("MAI_CACHE", True))

    #: Hard ceilings, enforced before the call and persisted across restarts.
    #: Conservative by default: easier to raise a limit than to un-spend a credit.
    daily_limit: int = field(
        default_factory=lambda: int(os.environ.get("MAI_DAILY_LIMIT", "25"))
    )
    total_limit: int = field(
        default_factory=lambda: int(os.environ.get("MAI_TOTAL_LIMIT", "200"))
    )

    # --- Storage -----------------------------------------------------------
    storage_root: Path = field(
        default_factory=lambda: Path(
            os.environ.get("STORAGE_ROOT", PROJECT_ROOT / "storage")
        )
    )

    def __post_init__(self) -> None:
        if not self.mock and not self.foundry_endpoint:
            raise RuntimeError(
                "FOUNDRY_ENDPOINT must be set when MAI_MOCK is off. "
                "Set MAI_MOCK=1 to run without Azure."
            )
        for sub in ("uploads", "base", "renders", "exports", "cache"):
            (self.storage_root / sub).mkdir(parents=True, exist_ok=True)

    @property
    def cache_root(self) -> Path:
        return self.storage_root / "cache"

    @property
    def generations_url(self) -> str:
        return f"{self.foundry_endpoint}/mai/v1/images/generations"

    @property
    def edits_url(self) -> str:
        return f"{self.foundry_endpoint}/mai/v1/images/edits"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Drop the cached settings so tests can re-read the environment."""
    global _settings
    _settings = None
