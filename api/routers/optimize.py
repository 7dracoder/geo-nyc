from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["optimize"])

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS_DIR = REPO_ROOT / "data" / "fields"
DEFAULT_GRID_PATH = FIELDS_DIR / "cost_grid.npz"
DEFAULT_META_PATH = FIELDS_DIR / "cost_raster_meta.json"
FALLBACK_DEPTH_PATH = FIELDS_DIR / "depth.npz"


class OptimizeParams(BaseModel):
    d_min: float = Field(default=5.0, ge=0.0, description="Minimum depth in meters.")
    d_max: float = Field(default=60.0, gt=0.0, description="Maximum depth in meters.")
    c1: float = Field(default=0.02, ge=0.0, description="Drilling length weight.")
    c2: float = Field(default=1.00, ge=0.0, description="Risk proxy weight.")
    target_depth: float = Field(
        default=30.0,
        ge=0.0,
        description="Optional preferred depth target used as soft penalty.",
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
    grid: np.ndarray
    x: np.ndarray
    y: np.ndarray
    source: str

    class Config:
        arbitrary_types_allowed = True


def _load_grid_from_npz(path: Path) -> GridBundle:
    if not path.exists():
        raise FileNotFoundError(path)

    arr = np.load(path)
    if "grid" not in arr:
        raise ValueError(f"Missing `grid` key in {path}")

    grid = arr["grid"].astype(np.float32)
    x = arr["x"].astype(np.float32) if "x" in arr else np.arange(grid.shape[1], dtype=np.float32)
    y = arr["y"].astype(np.float32) if "y" in arr else np.arange(grid.shape[0], dtype=np.float32)
    return GridBundle(grid=grid, x=x, y=y, source=path.name)


@lru_cache(maxsize=1)
def load_cost_grid() -> GridBundle:
    if DEFAULT_GRID_PATH.exists():
        return _load_grid_from_npz(DEFAULT_GRID_PATH)
    if FALLBACK_DEPTH_PATH.exists():
        return _load_grid_from_npz(FALLBACK_DEPTH_PATH)
    raise FileNotFoundError("Neither cost_grid.npz nor depth.npz is present in data/fields")


def _extract_meta() -> dict[str, Any]:
    if DEFAULT_META_PATH.exists():
        with DEFAULT_META_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "crs": "EPSG:4326",
        "units": "meters_below_surface",
        "source": "stub",
    }


def _sample_risk_proxy(bundle: GridBundle, depth: float) -> float:
    # For demo reliability and speed, use the median of the precomputed grid as a
    # fast risk proxy and tie it to depth mismatch from local geology expectation.
    median_depth = float(np.nanmedian(bundle.grid))
    deviation = abs(depth - median_depth)
    return deviation / max(median_depth, 1.0)


@router.post("/optimize")
def optimize(payload: OptimizeRequest) -> dict[str, Any]:
    params = payload.params
    if params.d_min >= params.d_max:
        raise HTTPException(status_code=422, detail="`d_min` must be lower than `d_max`.")

    try:
        bundle = load_cost_grid()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    d_values = np.linspace(params.d_min, params.d_max, 200, dtype=np.float32)
    if payload.mode == "tunnel":
        risk_scale = 1.25
    else:
        risk_scale = 1.0

    # Deterministic 1D grid search: robust for demo and always sub-200ms.
    risk_terms = np.array([_sample_risk_proxy(bundle, float(d)) for d in d_values], dtype=np.float32)
    soft_target = np.abs(d_values - params.target_depth) / max(params.target_depth, 1.0)
    objective = (params.c1 * d_values) + (params.c2 * risk_scale * risk_terms) + (0.1 * soft_target)

    best_idx = int(np.argmin(objective))
    optimal_d = float(d_values[best_idx])
    best_objective = float(objective[best_idx])
    effective_cover = float(np.nanmedian(bundle.grid) - optimal_d)
    cover_threshold = params.cover_min + params.safety_margin
    constraints_ok = effective_cover >= cover_threshold

    meta = _extract_meta()
    return {
        "mode": payload.mode,
        "optimal_d": round(optimal_d, 3),
        "objective": round(best_objective, 6),
        "constraints_ok": constraints_ok,
        "diagnostics": {
            "evaluations": len(d_values),
            "grid_source": bundle.source,
            "median_grid_depth": float(np.nanmedian(bundle.grid)),
            "effective_cover": round(effective_cover, 3),
            "cover_threshold": round(cover_threshold, 3),
            "crs": meta.get("crs", "EPSG:4326"),
            "units": meta.get("units", "meters_below_surface"),
        },
    }


@router.post("/optimize/reload")
def reload_optimizer_grid() -> dict[str, str]:
    load_cost_grid.cache_clear()
    load_cost_grid()
    return {"status": "reloaded"}
