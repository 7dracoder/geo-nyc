"""Shared pytest fixtures.

We do *not* read or write real `.env` files during tests. Instead the
``isolated_settings`` fixture rewrites every storage path to a fresh
``tmp_path`` directory so tests can scribble freely.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

# We must clear the lru_cache on every test that mutates env vars; do so
# centrally to keep test bodies short.
from geo_nyc.config import REPO_ROOT, Settings, get_settings, reset_settings_cache
from geo_nyc.documents import reset_document_service
from geo_nyc.runs.run_service import reset_run_service


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Settings]:
    """Yield a Settings instance whose paths point at ``tmp_path``."""

    overrides = {
        "GEO_NYC_DATA_DIR": str(tmp_path / "data"),
        "GEO_NYC_DOCUMENTS_RAW_DIR": str(tmp_path / "data/documents/raw"),
        "GEO_NYC_DOCUMENTS_EXTRACTED_DIR": str(tmp_path / "data/documents/extracted"),
        "GEO_NYC_RUNS_DIR": str(tmp_path / "data/runs"),
        "GEO_NYC_EXPORTS_DIR": str(tmp_path / "data/exports"),
        "GEO_NYC_FIELDS_DIR": str(tmp_path / "data/fields"),
        "GEO_NYC_CACHE_DIR": str(tmp_path / "data/cache"),
        "GEO_NYC_USE_FIXTURES": "true",
        "GEO_NYC_ENABLE_GEMPY": "false",
        "GEO_NYC_OLLAMA_BASE_URL": "http://localhost:11434",
        "GEO_NYC_PUBLIC_BASE_URL": "http://localhost:8000",
        "GEO_NYC_LOG_LEVEL": "WARNING",
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    reset_settings_cache()
    reset_run_service()
    reset_document_service()
    settings = get_settings()
    settings.ensure_directories()
    try:
        yield settings
    finally:
        reset_settings_cache()
        reset_run_service()
        reset_document_service()


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
