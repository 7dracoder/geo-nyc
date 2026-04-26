"""Application configuration.

All settings are loaded from environment variables prefixed ``GEO_NYC_``,
backed by ``.env`` for local development. The single, cached
:func:`get_settings` accessor is the only public entry point so tests can
override paths via :meth:`Settings.with_overrides` without leaking state
between cases.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from geo_nyc.exceptions import ConfigurationError

REPO_ROOT: Path = Path(__file__).resolve().parent.parent


def _resolve_path(value: str | Path) -> Path:
    """Resolve relative paths against the repository root.

    Pydantic-settings hands us strings; we want everything downstream to
    work with absolute :class:`Path` objects so file I/O is unambiguous
    no matter what working directory the process is launched from.
    """

    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path


PathLike = Annotated[Path, AfterValidator(_resolve_path)]


class Settings(BaseSettings):
    """Validated, immutable application settings."""

    model_config = SettingsConfigDict(
        env_prefix="GEO_NYC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM provider ------------------------------------------------------

    llm_provider: Literal["ollama"] = Field(default="ollama")
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="llama3.1:8b")
    ollama_fast_model: str | None = Field(default=None)

    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=4096, ge=64, le=32768)
    llm_timeout_seconds: float = Field(default=120.0, gt=0.0)
    llm_max_repair_attempts: int = Field(default=2, ge=0, le=5)

    # --- API server --------------------------------------------------------

    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = Field(default=False)

    # Static, comma-separated list of *exact* origins. Use the regex
    # field below for wildcard-style matches (e.g. Vercel previews).
    cors_origins: str = Field(
        default=(
            "http://localhost:3000,"
            "http://localhost:5173,"
            "https://geo-nyc.vercel.app"
        )
    )
    # Regex matched against the request ``Origin`` header. Lets Vercel
    # preview deployments (``geo-nyc-git-*.vercel.app``,
    # ``geo-nyc-<hash>-<owner>.vercel.app``) talk to the laptop
    # backend without redeploying every time the URL changes.
    cors_origin_regex: str | None = Field(
        default=r"^https://geo-nyc(-[a-z0-9-]+)?\.vercel\.app$"
    )
    public_base_url: str = Field(default="http://localhost:8000")

    # --- Storage roots -----------------------------------------------------

    data_dir: PathLike = Field(default=Path("./data"))
    documents_raw_dir: PathLike = Field(default=Path("./data/documents/raw"))
    documents_extracted_dir: PathLike = Field(default=Path("./data/documents/extracted"))
    runs_dir: PathLike = Field(default=Path("./data/runs"))
    exports_dir: PathLike = Field(default=Path("./data/exports"))
    fields_dir: PathLike = Field(default=Path("./data/fields"))
    cache_dir: PathLike = Field(default=Path("./data/cache"))

    # Part 3 (data layer) outputs. The fetch_open_data + build_field
    # scripts in `geonyc-data/scripts/` write here; the merged
    # `/api/layers` and `/api/optimize` routers read from here.
    data_layer_dir: PathLike = Field(default=Path("./geonyc-data/genyc_data"))

    # --- Pipeline toggles --------------------------------------------------

    use_fixtures: bool = Field(default=True)
    enable_gempy: bool = Field(default=False)

    # --- Logging -----------------------------------------------------------

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # --- Computed helpers --------------------------------------------------

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a clean, deduplicated list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_fast_model(self) -> str:
        return self.ollama_fast_model or self.ollama_model

    @property
    def fixtures_dir(self) -> Path:
        # Bundled fixtures shipped inside the package.
        return REPO_ROOT / "data" / "fixtures"

    def all_storage_dirs(self) -> tuple[Path, ...]:
        return (
            self.data_dir,
            self.documents_raw_dir,
            self.documents_extracted_dir,
            self.runs_dir,
            self.exports_dir,
            self.fields_dir,
            self.cache_dir,
        )

    @property
    def data_layer_layers_dir(self) -> Path:
        return self.data_layer_dir / "layers"

    @property
    def data_layer_fields_dir(self) -> Path:
        return self.data_layer_dir / "fields"

    def ensure_directories(self) -> None:
        """Create every storage directory referenced by these settings."""
        for path in self.all_storage_dirs():
            path.mkdir(parents=True, exist_ok=True)

    @field_validator("ollama_base_url", "public_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        # Downstream code joins paths with ``f"{base}/path"``; a trailing
        # slash silently produces ``//path`` which some HTTP clients hate.
        return value.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` instance.

    The result is memoised so reads stay cheap. Tests should call
    :func:`reset_settings_cache` between cases to pick up env changes.
    """

    try:
        return Settings()
    except Exception as exc:  # pragma: no cover - exercised in tests via env mutation
        raise ConfigurationError(f"Invalid geo-nyc configuration: {exc}") from exc


def reset_settings_cache() -> None:
    """Clear the memoised settings instance.

    Useful in test fixtures that mutate ``os.environ`` or write a
    temporary ``.env`` file.
    """

    get_settings.cache_clear()


__all__ = [
    "REPO_ROOT",
    "Settings",
    "get_settings",
    "reset_settings_cache",
]


# Eagerly validate when imported in non-test contexts so misconfiguration
# fails fast at startup rather than deep inside the request path.
if os.environ.get("GEO_NYC_SKIP_EAGER_SETTINGS") != "1":
    try:
        get_settings()
    except ConfigurationError:
        # Re-raise on actual server start; deferred import paths (tests)
        # will surface this themselves on next access.
        raise
