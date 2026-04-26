from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy.interpolate import griddata

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_PATH = REPO_ROOT / "temp_geo_lm"
if str(UPSTREAM_PATH) not in sys.path:
    sys.path.insert(0, str(UPSTREAM_PATH))

from geo_lm.gempy.builder import build_and_compute
from geo_lm.gempy.config import ModelExtent
from geo_lm.gempy.spatial import RuleBasedSpatialGenerator
from geo_lm.gempy.transformer import DSLToGemPyTransformer
from geo_lm.parsers.dsl import parse_and_validate


DSL_TEXT = """
ROCK R1 [ name: "Manhattan Schist"; type: metamorphic; age: 450Ma ]
ROCK R2 [ name: "Inwood Marble"; type: metamorphic; age: 430Ma ]
ROCK R3 [ name: "Fordham Gneiss"; type: metamorphic; age: 500Ma ]

DEPOSITION D1 [ rock: R3 ]
DEPOSITION D2 [ rock: R1; after: D1 ]
DEPOSITION D3 [ rock: R2; after: D2 ]
"""


def _read_target_bbox(depth_meta_path: Path) -> list[float]:
    if depth_meta_path.exists():
        payload = json.loads(depth_meta_path.read_text(encoding="utf-8"))
        bbox = payload.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            return [float(v) for v in bbox]
    # Fallback AOI for Manhattan-Bronx-Brooklyn demo box.
    return [-74.08, 40.56, -73.77, 40.93]


def _collect_vertices(geo_model) -> np.ndarray:
    vertices: list[np.ndarray] = []
    for group in geo_model.structural_frame.structural_groups:
        for element in group.elements:
            if element.vertices is not None and len(element.vertices) > 0:
                vertices.append(np.asarray(element.vertices, dtype=np.float32))
    if not vertices:
        raise RuntimeError("No GemPy vertices were generated from the model.")
    return np.vstack(vertices)


def _surface_depth_grid(vertices: np.ndarray, bbox: list[float], resolution: int = 160):
    # Collapse duplicate XY samples to the shallowest bedrock candidate (highest z).
    xy_to_z: dict[tuple[float, float], float] = {}
    for x, y, z in vertices:
        key = (round(float(x), 4), round(float(y), 4))
        existing = xy_to_z.get(key)
        if existing is None or z > existing:
            xy_to_z[key] = float(z)

    sample_xy = np.array(list(xy_to_z.keys()), dtype=np.float32)
    sample_z = np.array(list(xy_to_z.values()), dtype=np.float32)
    x = np.linspace(bbox[0], bbox[2], resolution, dtype=np.float32)
    y = np.linspace(bbox[1], bbox[3], resolution, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)

    # Build depth surface from GemPy-derived z values.
    z_linear = griddata(sample_xy, sample_z, (xx, yy), method="linear")
    z_nearest = griddata(sample_xy, sample_z, (xx, yy), method="nearest")
    z_surface = np.where(np.isfinite(z_linear), z_linear, z_nearest).astype(np.float32)
    depth_grid = np.maximum(0.0, -z_surface).astype(np.float32)
    return depth_grid, x, y


def run() -> None:
    out_dir = REPO_ROOT / "data" / "fields"
    out_dir.mkdir(parents=True, exist_ok=True)

    depth_meta_path = out_dir / "depth_meta.json"
    bbox = _read_target_bbox(depth_meta_path)

    program, parse_result = parse_and_validate(DSL_TEXT)
    if not parse_result.is_valid:
        raise RuntimeError(f"GemPy DSL parse failed: {parse_result.errors}")

    transformer = DSLToGemPyTransformer()
    extent = ModelExtent(
        x_min=bbox[0],
        x_max=bbox[2],
        y_min=bbox[1],
        y_max=bbox[3],
        z_min=-120.0,
        z_max=0.0,
    )
    config = transformer.transform(program, name="GemPyDepthModel", extent=extent)

    generator = RuleBasedSpatialGenerator(points_per_surface=24, seed=42)
    model_data = generator.generate(config)
    geo_model = build_and_compute(model_data)

    vertices = _collect_vertices(geo_model)
    depth_grid, x, y = _surface_depth_grid(vertices, bbox=bbox, resolution=160)

    np.savez_compressed(out_dir / "depth.npz", grid=depth_grid, x=x, y=y)

    meta = {
        "crs": "EPSG:4326",
        "bbox": bbox,
        "resolution_m": 50,
        "units": "meters_below_surface",
        "source": "gempy",
        "generated_at": datetime.now(UTC).isoformat(),
        "method": "geo-lm gempy model -> mesh vertices -> depth interpolation",
    }
    depth_meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("Generated GemPy depth field:", out_dir / "depth.npz")


if __name__ == "__main__":
    run()
