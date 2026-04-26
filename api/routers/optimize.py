"""Optimization endpoint.

Performs a fast 1D grid-search over depth using a precomputed cost
field (``cost_grid.npz``) produced by ``geonyc-data/scripts/build_field.py``.
The grid is loaded once and cached; ``POST /api/optimize/reload``
clears the cache after a fresh build.

The frontend's ``WhatIfPanel`` posts the minimum schema
(``mode``, ``params.{d_min,d_max,lateral_m?}``); the additional
optimizer knobs (``c1``, ``c2``, ``target_depth``, ``cover_min``,
``safety_margin``) are accepted as optional overrides for power-users
of the API.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from geo_nyc.config import get_settings
from geo_nyc.logging import get_logger

router = APIRouter(tags=["optimize"])

log = get_logger(__name__)


class OptimizeParams(BaseModel):
    # ``extra="ignore"`` lets the frontend send keys we don't model
    # here (e.g. ``lateral_m``) without 422-ing the request.
    model_config = ConfigDict(extra="ignore")

    d_min: float = Field(default=5.0, ge=0.0, description="Minimum depth in meters.")
    d_max: float = Field(default=60.0, gt=0.0, description="Maximum depth in meters.")
    c1: float = Field(default=0.02, ge=0.0, description="Drilling-length weight.")
    c2: float = Field(default=1.00, ge=0.0, description="Risk-proxy weight.")
    target_depth: float = Field(
        default=30.0,
        ge=0.0,
        description="Soft-penalty preferred depth target.",
    )
    cover_min: float = Field(
        default=8.0,
        ge=0.0,
        description="Minimum rock cover threshold.",
    )
    safety_margin: float = Field(
        default=2.0,
        ge=0.0,
        description="Extra buffer above minimum rock cover.",
    )


class OptimizeRequest(BaseModel):
    mode: Literal["geothermal", "tunnel"] = "geothermal"
    params: OptimizeParams = Field(default_factory=OptimizeParams)


class GridBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    grid: np.ndarray
    x: np.ndarray
    y: np.ndarray
    mask: np.ndarray | None = None
    source: str


def _load_grid_from_npz(path: Path) -> GridBundle:
    arr = np.load(path)
    if "grid" not in arr:
        raise ValueError(f"Missing `grid` key in {path}")

    grid = arr["grid"].astype(np.float32)
    x = arr["x"].astype(np.float32) if "x" in arr else np.arange(grid.shape[1], dtype=np.float32)
    y = arr["y"].astype(np.float32) if "y" in arr else np.arange(grid.shape[0], dtype=np.float32)
    mask = arr["mask"].astype(np.uint8) if "mask" in arr else None
    return GridBundle(grid=grid, x=x, y=y, mask=mask, source=path.name)


def _valid_grid_values(bundle: GridBundle) -> np.ndarray:
    if bundle.mask is not None and bundle.mask.shape == bundle.grid.shape:
        values = bundle.grid[bundle.mask > 0]
    else:
        values = bundle.grid[np.isfinite(bundle.grid)]
    if values.size == 0:
        values = bundle.grid[np.isfinite(bundle.grid)]
    return values.astype(np.float32)


@lru_cache(maxsize=1)
def _cached_grid(fields_dir_str: str) -> GridBundle:
    fields_dir = Path(fields_dir_str)
    cost_path = fields_dir / "cost_grid.npz"
    depth_path = fields_dir / "depth.npz"
    if cost_path.exists():
        return _load_grid_from_npz(cost_path)
    if depth_path.exists():
        return _load_grid_from_npz(depth_path)
    raise FileNotFoundError(
        f"Neither cost_grid.npz nor depth.npz is present in {fields_dir}. "
        "Run `python geonyc-data/scripts/build_field.py` first."
    )


def _load_cost_grid() -> GridBundle:
    settings = get_settings()
    return _cached_grid(str(settings.data_layer_fields_dir))


def _meta() -> dict[str, Any]:
    settings = get_settings()
    meta_path = settings.data_layer_fields_dir / "cost_raster_meta.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("cost_raster_meta.json is not valid JSON; using defaults")
    return {
        "crs": "EPSG:4326",
        "units": "meters_below_surface",
        "source": "unknown",
    }


def _sample_risk_proxy(bundle: GridBundle, depth: float) -> float:
    values = _valid_grid_values(bundle)
    median_depth = float(np.nanmedian(values))
    deviation = abs(depth - median_depth)
    return deviation / max(median_depth, 1.0)


@router.post("/optimize")
def optimize(payload: OptimizeRequest) -> dict[str, Any]:
    params = payload.params
    if params.d_min >= params.d_max:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="`d_min` must be lower than `d_max`.",
        )

    try:
        bundle = _load_cost_grid()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    d_values = np.linspace(params.d_min, params.d_max, 200, dtype=np.float32)
    risk_scale = 1.25 if payload.mode == "tunnel" else 1.0

    meta = _meta()
    units = str(meta.get("units", "meters_below_surface"))
    if units == "meters_below_surface":
        reference_depth = params.target_depth
        risk_terms = np.array(
            [_sample_risk_proxy(bundle, float(d)) for d in d_values], dtype=np.float32
        )
        soft_target = np.abs(d_values - reference_depth) / max(reference_depth, 1.0)
        objective = (
            (params.c1 * d_values)
            + (params.c2 * risk_scale * risk_terms)
            + (0.1 * soft_target)
        )
    else:
        # Proxy-field path: steer the optimizer by the field's distribution
        # so the result stays informative even without GemPy depth.
        values = _valid_grid_values(bundle)
        reference_depth = float(np.nanpercentile(values, 75))
        risk_terms = np.abs(d_values - reference_depth) / max(reference_depth, 1.0)
        soft_target = np.abs(d_values - reference_depth) / max(reference_depth, 1.0)
        objective = (
            (params.c1 * d_values)
            + (params.c2 * risk_scale * risk_terms)
            + (0.08 * soft_target)
        )

    best_idx = int(np.argmin(objective))
    optimal_d = float(d_values[best_idx])
    best_objective = float(objective[best_idx])

    valid_values = _valid_grid_values(bundle)
    effective_cover = float(np.nanmedian(valid_values) - optimal_d)
    cover_threshold = params.cover_min + params.safety_margin
    if units == "meters_below_surface":
        constraints_ok = effective_cover >= cover_threshold
    else:
        constraints_ok = params.d_min <= optimal_d <= params.d_max

    return {
        "mode": payload.mode,
        "optimal_d": round(optimal_d, 3),
        "objective": round(best_objective, 6),
        "constraints_ok": constraints_ok,
        "diagnostics": {
            "evaluations": int(d_values.size),
            "grid_source": bundle.source,
            "median_grid_depth": float(np.nanmedian(valid_values)),
            "effective_cover": round(effective_cover, 3),
            "cover_threshold": round(cover_threshold, 3),
            "crs": meta.get("crs", "EPSG:4326"),
            "units": units,
            "field_source": meta.get("source", "unknown"),
            "reference_depth": round(reference_depth, 3),
        },
    }


@router.post("/optimize/reload")
def reload_optimizer_grid() -> dict[str, str]:
    _cached_grid.cache_clear()
    _load_cost_grid()
    return {"status": "reloaded"}
