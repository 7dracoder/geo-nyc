from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
from shapely.geometry import LineString, mapping

matplotlib.use("Agg")


def _load_depth_grid(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if not path.exists():
        raise FileNotFoundError(f"Depth field missing: {path}")
    arr = np.load(path)
    if "grid" not in arr:
        raise ValueError(f"Expected key `grid` in {path}")
    grid = arr["grid"].astype(np.float32)
    x = arr["x"].astype(np.float32) if "x" in arr else np.arange(grid.shape[1], dtype=np.float32)
    y = arr["y"].astype(np.float32) if "y" in arr else np.arange(grid.shape[0], dtype=np.float32)
    return grid, x, y


def _fill_nan_values(grid: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    mask = np.isfinite(grid)
    if mask.sum() == 0:
        raise ValueError("Depth grid contains no finite values.")
    if mask.all():
        return grid

    xx, yy = np.meshgrid(x, y)
    known_points = np.column_stack([xx[mask], yy[mask]])
    known_values = grid[mask]
    missing_points = np.column_stack([xx[~mask], yy[~mask]])

    filled = grid.copy()
    interpolated = griddata(known_points, known_values, missing_points, method="nearest")
    filled[~mask] = interpolated.astype(np.float32)
    return filled


def _build_contours_geojson(grid: np.ndarray, x: np.ndarray, y: np.ndarray, contour_path: Path) -> None:
    levels = np.linspace(float(np.nanmin(grid)), float(np.nanmax(grid)), 9)
    xx, yy = np.meshgrid(x, y)
    contour_set = plt.contour(xx, yy, grid, levels=levels)

    features = []
    for level_idx, level in enumerate(contour_set.levels):
        collection = contour_set.collections[level_idx]
        for path in collection.get_paths():
            vertices = path.vertices
            if len(vertices) < 2:
                continue
            geom = LineString([(float(v[0]), float(v[1])) for v in vertices])
            features.append(
                {
                    "type": "Feature",
                    "properties": {"value": float(level)},
                    "geometry": mapping(geom),
                }
            )

    geojson = {"type": "FeatureCollection", "features": features}
    contour_path.parent.mkdir(parents=True, exist_ok=True)
    contour_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
    plt.close()


def run(
    depth_npz: Path,
    depth_meta_json: Path,
    out_cost_npz: Path,
    out_meta_json: Path,
    out_contours_geojson: Path,
    sigma: float,
) -> None:
    grid, x, y = _load_depth_grid(depth_npz)
    filled = _fill_nan_values(grid, x, y)
    smoothed = gaussian_filter(filled, sigma=sigma).astype(np.float32)

    out_cost_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_cost_npz, grid=smoothed, x=x, y=y)
    _build_contours_geojson(smoothed, x, y, out_contours_geojson)

    if depth_meta_json.exists():
        meta = json.loads(depth_meta_json.read_text(encoding="utf-8"))
    else:
        meta = {}

    meta.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "source": meta.get("source", "kriging_only"),
            "smoothing_method": "gaussian_filter",
            "smoothing_sigma": sigma,
            "units": meta.get("units", "meters_below_surface"),
            "crs": meta.get("crs", "EPSG:4326"),
        }
    )
    out_meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build smoothed field and contours for optimization.")
    parser.add_argument("--depth-npz", default="data/fields/depth.npz")
    parser.add_argument("--depth-meta-json", default="data/fields/depth_meta.json")
    parser.add_argument("--out-cost-npz", default="data/fields/cost_grid.npz")
    parser.add_argument("--out-meta-json", default="data/fields/cost_raster_meta.json")
    parser.add_argument("--out-contours-geojson", default="web/public/layers/depth_contours.geojson")
    parser.add_argument("--sigma", type=float, default=1.6, help="Gaussian smoothing sigma.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run(
        depth_npz=Path(args.depth_npz),
        depth_meta_json=Path(args.depth_meta_json),
        out_cost_npz=Path(args.out_cost_npz),
        out_meta_json=Path(args.out_meta_json),
        out_contours_geojson=Path(args.out_contours_geojson),
        sigma=args.sigma,
    )
    print("Field and contours generated successfully.")
