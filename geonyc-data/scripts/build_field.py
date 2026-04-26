from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import griddata
from scipy.ndimage import gaussian_filter
from shapely.geometry import LineString, Point, mapping
from shapely.ops import unary_union

matplotlib.use("Agg")
PROJECTED_CRS = "EPSG:32618"


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


def _load_meta(meta_path: Path) -> dict[str, object]:
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}


def _is_stub_source(meta: dict[str, object]) -> bool:
    source = str(meta.get("source", "")).lower()
    return source in {"stub", "synthetic", "analytic", "open_data_proxy"}


def _build_real_proxy_grid_from_layers(
    *,
    aoi_geojson: Path,
    hazard_geojson: Path,
    grid_resolution: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[float]]:
    if not aoi_geojson.exists():
        raise FileNotFoundError(f"AOI layer not found: {aoi_geojson}")
    if not hazard_geojson.exists():
        raise FileNotFoundError(f"Hazard layer not found: {hazard_geojson}")

    aoi = gpd.read_file(aoi_geojson).to_crs(PROJECTED_CRS)
    hazard = gpd.read_file(hazard_geojson).to_crs(PROJECTED_CRS)
    if aoi.empty:
        raise ValueError("AOI GeoJSON has no features.")
    if hazard.empty:
        raise ValueError("Hazard GeoJSON has no features.")

    aoi_union = unary_union(aoi.geometry.values)
    hazard_union = unary_union(hazard.geometry.values)

    minx, miny, maxx, maxy = aoi.total_bounds
    x_m = np.linspace(minx, maxx, grid_resolution, dtype=np.float32)
    y_m = np.linspace(miny, maxy, grid_resolution, dtype=np.float32)
    xx_m, yy_m = np.meshgrid(x_m, y_m)

    # Real-data proxy field: depth is shallower near hazard zones and deeper farther away.
    # Scale dynamically from observed AOI distances to avoid flat fields.
    grid_proxy = np.full((grid_resolution, grid_resolution), np.nan, dtype=np.float32)
    hazard_distances: list[float] = []
    boundary_distances: list[float] = []
    cell_points: list[tuple[int, int, Point]] = []
    aoi_boundary = aoi_union.boundary
    for row in range(grid_resolution):
        for col in range(grid_resolution):
            pt = Point(float(xx_m[row, col]), float(yy_m[row, col]))
            if not aoi_union.contains(pt):
                continue
            hazard_distances.append(float(pt.distance(hazard_union)))
            boundary_distances.append(float(pt.distance(aoi_boundary)))
            cell_points.append((row, col, pt))

    if not hazard_distances:
        raise ValueError("Proxy field generation failed: no valid AOI points.")

    hmin = float(np.min(hazard_distances))
    hmax = float(np.max(hazard_distances))
    bmin = float(np.min(boundary_distances))
    bmax = float(np.max(boundary_distances))
    hden = max(hmax - hmin, 1e-6)
    bden = max(bmax - bmin, 1e-6)

    for row, col, pt in cell_points:
        h_norm = (float(pt.distance(hazard_union)) - hmin) / hden
        b_norm = (float(pt.distance(aoi_boundary)) - bmin) / bden
        norm = (0.7 * h_norm) + (0.3 * b_norm)
        # Map observed distance rank to depth range used by optimizer.
        depth_proxy = 5.0 + (55.0 * norm)
        grid_proxy[row, col] = np.float32(depth_proxy)

    if np.isnan(grid_proxy).all():
        raise ValueError("Proxy field generation failed: no valid AOI points.")

    # Reproject grid axes to EPSG:4326 for frontend compatibility.
    aoi_ll = aoi.to_crs("EPSG:4326")
    minx_ll, miny_ll, maxx_ll, maxy_ll = aoi_ll.total_bounds
    x_ll = np.linspace(minx_ll, maxx_ll, grid_resolution, dtype=np.float32)
    y_ll = np.linspace(miny_ll, maxy_ll, grid_resolution, dtype=np.float32)
    bbox_ll = [float(minx_ll), float(miny_ll), float(maxx_ll), float(maxy_ll)]
    return grid_proxy, x_ll, y_ll, bbox_ll


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
    # Use allsegs to stay compatible across matplotlib contour object changes.
    for level_idx, level in enumerate(contour_set.levels):
        segments = contour_set.allsegs[level_idx]
        for seg in segments:
            if len(seg) < 2:
                continue
            geom = LineString([(float(v[0]), float(v[1])) for v in seg])
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


def _upsert_contour_layer(manifest_path: Path, contour_rel_path: str) -> None:
    if not manifest_path.exists():
        return

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    layers = payload.get("layers", [])
    if not isinstance(layers, list):
        return

    contour_entry = {
        "id": "depth_contours",
        "title": "Depth Proxy Contours",
        "type": "line",
        "geojson_path": contour_rel_path,
        "opacity": 0.8,
        "legend_color": "#B45309",
        "source_url": "generated_from_build_field",
        "download_date": datetime.now(UTC).date().isoformat(),
    }
    filtered = [layer for layer in layers if layer.get("id") != "depth_contours"]
    filtered.append(contour_entry)
    payload["layers"] = filtered
    payload["generated_at"] = datetime.now(UTC).isoformat()
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run(
    depth_npz: Path,
    depth_meta_json: Path,
    out_cost_npz: Path,
    out_meta_json: Path,
    out_contours_geojson: Path,
    layers_manifest_json: Path,
    aoi_geojson: Path,
    hazard_geojson: Path,
    grid_resolution: int,
    sigma: float,
    mirror_web_public_dir: Path | None = None,
) -> None:
    meta_in = _load_meta(depth_meta_json)
    use_proxy = (not depth_npz.exists()) or _is_stub_source(meta_in)

    if use_proxy:
        grid, x, y, bbox = _build_real_proxy_grid_from_layers(
            aoi_geojson=aoi_geojson,
            hazard_geojson=hazard_geojson,
            grid_resolution=grid_resolution,
        )
        meta_in = {
            "crs": "EPSG:4326",
            "bbox": bbox,
            "resolution_m": int(50),
            "units": "meters_proxy_depth",
            "source": "open_data_proxy",
        }
        depth_npz.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(depth_npz, grid=grid, x=x, y=y)
        depth_meta_json.write_text(json.dumps(meta_in, indent=2), encoding="utf-8")
    else:
        grid, x, y = _load_depth_grid(depth_npz)

    valid_mask = np.isfinite(grid).astype(np.uint8)
    filled = _fill_nan_values(grid, x, y)
    smoothed = gaussian_filter(filled, sigma=sigma).astype(np.float32)

    out_cost_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_cost_npz, grid=smoothed, x=x, y=y, mask=valid_mask)
    _build_contours_geojson(smoothed, x, y, out_contours_geojson)
    _upsert_contour_layer(layers_manifest_json, "/layers/depth_contours.geojson")
    if mirror_web_public_dir is not None:
        mirror_layers_dir = mirror_web_public_dir / "layers"
        mirror_layers_dir.mkdir(parents=True, exist_ok=True)
        if out_contours_geojson.exists():
            shutil.copy2(out_contours_geojson, mirror_layers_dir / out_contours_geojson.name)
        if layers_manifest_json.exists():
            shutil.copy2(layers_manifest_json, mirror_layers_dir / layers_manifest_json.name)

    meta_out = dict(meta_in)
    meta_out.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "source": meta_in.get("source", "open_data_proxy"),
            "smoothing_method": "gaussian_filter",
            "smoothing_sigma": sigma,
            "units": meta_in.get("units", "meters_proxy_depth"),
            "crs": meta_in.get("crs", "EPSG:4326"),
        }
    )
    out_meta_json.write_text(json.dumps(meta_out, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build smoothed field and contours for optimization.")
    parser.add_argument("--depth-npz", default="genyc_data/fields/depth.npz")
    parser.add_argument("--depth-meta-json", default="genyc_data/fields/depth_meta.json")
    parser.add_argument("--out-cost-npz", default="genyc_data/fields/cost_grid.npz")
    parser.add_argument("--out-meta-json", default="genyc_data/fields/cost_raster_meta.json")
    parser.add_argument("--out-contours-geojson", default="genyc_data/layers/depth_contours.geojson")
    parser.add_argument("--layers-manifest-json", default="genyc_data/layers/manifest.json")
    parser.add_argument("--aoi-geojson", default="genyc_data/layers/aoi.geojson")
    parser.add_argument("--hazard-geojson", default="genyc_data/layers/hurricane_evacuation_zones.geojson")
    parser.add_argument(
        "--mirror-web-public-dir",
        default="web/public",
        help="Optional directory to mirror generated manifest/contours for frontend static serving.",
    )
    parser.add_argument("--grid-resolution", type=int, default=160)
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
        layers_manifest_json=Path(args.layers_manifest_json),
        aoi_geojson=Path(args.aoi_geojson),
        hazard_geojson=Path(args.hazard_geojson),
        grid_resolution=args.grid_resolution,
        sigma=args.sigma,
        mirror_web_public_dir=Path(args.mirror_web_public_dir) if args.mirror_web_public_dir else None,
    )
    print("Field and contours generated successfully.")
