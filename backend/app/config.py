"""Application settings, read from the environment.

Everything here has a working default except the Foundry endpoint, so the app
boots and the whole pipeline runs in mock mode on a clean checkout.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Read ``.env`` into the environment, without overriding what is set.

    The file already existed, was documented, was gitignored -- and nothing
    read it. Settings came from ``os.environ`` alone, so the app ran in mock
    mode against MAI whenever it was launched from a shell that happened not
    to have the variables exported, while a ``.env`` sitting next to it said
    FLUX and live. A config file that silently does nothing is worse than no
    config file.

    Real environment variables win, so a deliberate ``MAI_MOCK=1 uvicorn ...``
    still overrides the file.

    Never under pytest. The file names a live deployment and turns mock mode
    off, so loading it would point the suite at a real, billed, rate-limited
    image model -- 194 tests against a 5-a-day quota. A test run must not be
    able to spend anything, and the safe default has to be structural rather
    than a variable somebody remembers to export.
    """
    if "pytest" in sys.modules:
        return
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(name, value)


_load_dotenv()


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

    # --- image backend -----------------------------------------------------
    #: Which image model to use: "mai" or "flux".
    #:
    #: Both are implemented behind the same protocol. MAI is the Microsoft
    #: model and stays the default; FLUX is selectable because free and student
    #: subscriptions get zero MAI image quota, and an unusable default is worse
    #: than a configurable one.
    image_backend: str = field(
        default_factory=lambda: os.environ.get("IMAGE_BACKEND", "mai").lower()
    )
    flux_model: str = field(
        default_factory=lambda: os.environ.get("FLUX_MODEL", "FLUX.2-pro")
    )
    flux_draft_model: str = field(
        default_factory=lambda: os.environ.get("FLUX_DRAFT_MODEL", "FLUX.2-flex")
    )
    #: FLUX.2-pro allows 15 RPM at the lowest tier, against MAI's 2.
    flux_rpm: float = field(
        default_factory=lambda: float(os.environ.get("FLUX_RPM", "10"))
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
    def resource_name(self) -> str:
        """Bare resource name, parsed from whichever endpoint form was given."""
        host = self.foundry_endpoint.split("//")[-1]
        return host.split(".")[0] if host else ""

    @property
    def flux_endpoint(self) -> str:
        """FLUX lives on a different host to MAI on the same resource.

        MAI:  <resource>.services.ai.azure.com
        FLUX: <resource>.cognitiveservices.azure.com

        The docs give the FLUX host as ``<resource>.api.cognitive.microsoft.com``,
        but a real AIServices resource reports ``.cognitiveservices.azure.com``
        as its Cognitive Services endpoint and the documented form does not
        resolve at all -- it fails as a DNS error rather than a 404, which
        looks like a network problem rather than a wrong hostname.

        So the default follows the resource, and ``FLUX_ENDPOINT`` overrides it
        if a given tenant really does use the documented host. Check the actual
        value with:

            az cognitiveservices account show -n <name> -g <rg> \\
                --query properties.endpoint
        """
        override = os.environ.get("FLUX_ENDPOINT", "").rstrip("/")
        if override:
            return override
        return f"https://{self.resource_name}.cognitiveservices.azure.com"

    @property
    def capabilities(self):
        from app.imaging.dimensions import CAPABILITIES, MAI

        return CAPABILITIES.get(self.image_backend, MAI)

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
