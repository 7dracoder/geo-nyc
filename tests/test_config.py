"""Settings module tests."""

from __future__ import annotations

import pytest

from geo_nyc.config import Settings, get_settings, reset_settings_cache


def test_defaults_are_local_only(isolated_settings: Settings) -> None:
    settings = get_settings()
    assert settings.llm_provider == "ollama"
    assert settings.ollama_base_url.startswith("http://")
    assert settings.cors_origin_list  # non-empty


def test_default_cors_includes_vercel_prod_and_localhost(
    isolated_settings: Settings,
) -> None:
    """The default CORS list must work both for local Next.js dev
    servers and for the public Vercel deployment, otherwise the live
    demo will fail with an opaque CORS error."""

    origins = isolated_settings.cors_origin_list
    assert "http://localhost:3000" in origins
    assert "https://geo-nyc.vercel.app" in origins


def test_default_cors_origin_regex_matches_vercel_previews(
    isolated_settings: Settings,
) -> None:
    """Vercel preview URLs change on every push; the regex has to
    accept them so we don't have to redeploy the backend each time."""

    import re

    pattern = isolated_settings.cors_origin_regex
    assert pattern is not None
    rx = re.compile(pattern)

    # Production + a sampling of preview URL shapes Vercel actually emits.
    matching = [
        "https://geo-nyc.vercel.app",
        "https://geo-nyc-git-main-soma.vercel.app",
        "https://geo-nyc-abc123def.vercel.app",
        "https://geo-nyc-feature-foo-username.vercel.app",
    ]
    for origin in matching:
        assert rx.fullmatch(origin), f"regex should match {origin!r}"

    not_matching = [
        "http://geo-nyc.vercel.app",  # http, not https
        "https://geo-nyc.vercel.dev",  # wrong TLD
        "https://evil.com",
        "https://geo-nyc.vercel.app.evil.com",
    ]
    for origin in not_matching:
        assert not rx.fullmatch(origin), f"regex should NOT match {origin!r}"


def test_cors_origins_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_NYC_CORS_ORIGINS", "http://a.test , http://b.test, ,")
    reset_settings_cache()
    settings = get_settings()
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]
    reset_settings_cache()


def test_paths_resolve_to_absolute(isolated_settings: Settings) -> None:
    for path in isolated_settings.all_storage_dirs():
        assert path.is_absolute()
        assert path.exists(), f"{path} should be created by ensure_directories()"


def test_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEO_NYC_OLLAMA_BASE_URL", "http://localhost:11434/")
    monkeypatch.setenv("GEO_NYC_PUBLIC_BASE_URL", "http://localhost:8000///")
    reset_settings_cache()
    settings = get_settings()
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.public_base_url == "http://localhost:8000"
    reset_settings_cache()


def test_effective_fast_model_falls_back(isolated_settings: Settings) -> None:
    assert isolated_settings.effective_fast_model == isolated_settings.ollama_model
