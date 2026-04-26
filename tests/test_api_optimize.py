"""Smoke tests for the merged Part 3 ``/api/optimize`` endpoint."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from geo_nyc.config import Settings, get_settings, reset_settings_cache


@pytest.fixture
def client_with_cost_grid(
    isolated_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[TestClient]:
    """Stage a tiny synthetic cost_grid.npz and point settings at it."""

    fields_dir = tmp_path / "data_layer" / "fields"
    fields_dir.mkdir(parents=True)

    # 16x16 deterministic grid in [10, 60] meters with a stripe of NaNs to
    # exercise the mask / valid-values path.
    rng = np.random.default_rng(seed=42)
    grid = rng.uniform(low=10.0, high=60.0, size=(16, 16)).astype(np.float32)
    grid[0, :] = np.nan
    mask = np.isfinite(grid).astype(np.uint8)
    x = np.linspace(-74.08, -73.77, 16, dtype=np.float32)
    y = np.linspace(40.56, 40.93, 16, dtype=np.float32)
    np.savez_compressed(fields_dir / "cost_grid.npz", grid=grid, x=x, y=y, mask=mask)

    meta = {
        "crs": "EPSG:4326",
        "bbox": [-74.08, 40.56, -73.77, 40.93],
        "resolution_m": 50,
        "units": "meters_below_surface",
        "source": "gempy",
    }
    (fields_dir / "cost_raster_meta.json").write_text(json.dumps(meta), encoding="utf-8")

    monkeypatch.setenv("GEO_NYC_DATA_LAYER_DIR", str(tmp_path / "data_layer"))
    reset_settings_cache()
    get_settings()

    # Clear the lru_cache so each test gets a fresh load against tmp_path.
    from api.routers import optimize as optimize_router

    optimize_router._cached_grid.cache_clear()

    from api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


def test_optimize_geothermal_returns_valid_response(
    client_with_cost_grid: TestClient,
) -> None:
    payload = {"mode": "geothermal", "params": {"d_min": 8, "d_max": 55}}
    response = client_with_cost_grid.post("/api/optimize", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "geothermal"
    assert 8.0 <= body["optimal_d"] <= 55.0
    assert isinstance(body["objective"], float)
    assert isinstance(body["constraints_ok"], bool)
    assert "diagnostics" in body
    diag = body["diagnostics"]
    assert diag["evaluations"] == 200
    assert diag["units"] == "meters_below_surface"
    assert diag["grid_source"] == "cost_grid.npz"


def test_optimize_tunnel_accepts_lateral_offset(
    client_with_cost_grid: TestClient,
) -> None:
    """Frontend sends `lateral_m`; the API must accept and ignore it."""

    payload = {
        "mode": "tunnel",
        "params": {"d_min": 8, "d_max": 55, "lateral_m": 12},
    }
    response = client_with_cost_grid.post("/api/optimize", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "tunnel"
    assert 8.0 <= body["optimal_d"] <= 55.0


def test_optimize_rejects_invalid_depth_range(
    client_with_cost_grid: TestClient,
) -> None:
    payload = {"mode": "geothermal", "params": {"d_min": 60, "d_max": 60}}
    response = client_with_cost_grid.post("/api/optimize", json=payload)
    assert response.status_code == 422
    assert "d_min" in response.json()["detail"].lower()


def test_optimize_503_when_grid_missing(
    isolated_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setenv("GEO_NYC_DATA_LAYER_DIR", str(empty_dir))
    reset_settings_cache()
    get_settings()

    from api.routers import optimize as optimize_router

    optimize_router._cached_grid.cache_clear()

    from api.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.post(
            "/api/optimize",
            json={"mode": "geothermal", "params": {"d_min": 8, "d_max": 55}},
        )
        assert response.status_code == 503
        assert "cost_grid" in response.json()["detail"]


def test_optimize_reload_invalidates_cache(client_with_cost_grid: TestClient) -> None:
    response = client_with_cost_grid.post("/api/optimize/reload")
    assert response.status_code == 200
    assert response.json() == {"status": "reloaded"}
