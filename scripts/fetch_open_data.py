from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import shape

BOROUGH_BOUNDARIES_URL = "https://data.cityofnewyork.us/resource/gthc-hcne.geojson?$limit=5000"
DEFAULT_BORO_CODES = [1, 2, 3]  # Manhattan, Bronx, Brooklyn
PROJECTED_CRS = "EPSG:32618"


@dataclass(frozen=True)
class LayerSpec:
    layer_id: str
    title: str
    layer_type: str
    source_url: str
    legend_color: str
    opacity: float


ADDITIONAL_LAYER_SPECS = [
    # Keep this list small for demo reliability. Any fetch failure is non-fatal.
    LayerSpec(
        layer_id="hurricane_evacuation_zones",
        title="Hurricane Evacuation Zones",
        layer_type="fill",
        source_url="https://data.cityofnewyork.us/resource/epne-qv9x.geojson?$limit=50000",
        legend_color="#4C78A8",
        opacity=0.35,
    ),
]


def _load_geojson_from_url(url: str) -> gpd.GeoDataFrame:
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    payload = response.json()
    features = payload.get("features", [])
    if not features:
        raise ValueError(f"No features returned by: {url}")
    geometries = [shape(feature["geometry"]) for feature in features if feature.get("geometry")]
    properties = [feature.get("properties", {}) for feature in features if feature.get("geometry")]
    return gpd.GeoDataFrame(properties, geometry=geometries, crs="EPSG:4326")


def _resolve_boro_code_column(df: gpd.GeoDataFrame) -> str:
    options = ("boro_code", "BoroCode", "boro_cd", "borocode", "BoroCD")
    for candidate in options:
        if candidate in df.columns:
            return candidate
    raise ValueError("Could not find borough code column in borough boundaries dataset.")


def _safe_simplify(input_df: gpd.GeoDataFrame, tolerance_m: float) -> gpd.GeoDataFrame:
    projected = input_df.to_crs(PROJECTED_CRS)
    projected["geometry"] = projected.geometry.simplify(tolerance=tolerance_m, preserve_topology=True)
    return projected.to_crs("EPSG:4326")


def _build_aoi(boro_codes: list[int]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    boroughs = _load_geojson_from_url(BOROUGH_BOUNDARIES_URL)
    boro_code_col = _resolve_boro_code_column(boroughs)
    filtered = boroughs[boroughs[boro_code_col].astype(int).isin(boro_codes)].copy()
    if filtered.empty:
        raise ValueError(f"No borough geometry found for boro codes: {boro_codes}")

    aoi = filtered.dissolve().reset_index(drop=True)
    aoi = _safe_simplify(aoi, tolerance_m=25.0)
    filtered = _safe_simplify(filtered, tolerance_m=15.0)
    return filtered, aoi


def _clip_layer_to_aoi(layer_df: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    layer_proj = layer_df.to_crs(PROJECTED_CRS)
    aoi_proj = aoi.to_crs(PROJECTED_CRS)
    clipped = gpd.clip(layer_proj, aoi_proj)
    if clipped.empty:
        return clipped.to_crs("EPSG:4326")

    clipped["geometry"] = clipped.geometry.simplify(tolerance=20.0, preserve_topology=True)
    return clipped.to_crs("EPSG:4326")


def _write_geojson(df: gpd.GeoDataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_file(output_path, driver="GeoJSON")


def _layer_entry(
    *,
    layer_id: str,
    title: str,
    layer_type: str,
    geojson_path: str,
    opacity: float,
    legend_color: str,
    source_url: str,
) -> dict[str, object]:
    return {
        "id": layer_id,
        "title": title,
        "type": layer_type,
        "geojson_path": geojson_path.replace("\\", "/"),
        "opacity": opacity,
        "legend_color": legend_color,
        "source_url": source_url,
        "download_date": datetime.now(UTC).date().isoformat(),
    }


def run(output_dir: Path, boro_codes: list[int]) -> Path:
    layers_dir = output_dir / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    boroughs, aoi = _build_aoi(boro_codes)
    boroughs_path = layers_dir / "boroughs.geojson"
    aoi_path = layers_dir / "aoi.geojson"
    _write_geojson(boroughs, boroughs_path)
    _write_geojson(aoi, aoi_path)

    layers_manifest: list[dict[str, object]] = [
        _layer_entry(
            layer_id="boroughs",
            title="NYC Borough Boundaries (AOI)",
            layer_type="line",
            geojson_path="/layers/boroughs.geojson",
            opacity=0.8,
            legend_color="#6B7280",
            source_url=BOROUGH_BOUNDARIES_URL,
        ),
        _layer_entry(
            layer_id="aoi",
            title="AOI Mask",
            layer_type="fill",
            geojson_path="/layers/aoi.geojson",
            opacity=0.15,
            legend_color="#D1D5DB",
            source_url=BOROUGH_BOUNDARIES_URL,
        ),
    ]

    for spec in ADDITIONAL_LAYER_SPECS:
        try:
            raw_layer = _load_geojson_from_url(spec.source_url)
            clipped = _clip_layer_to_aoi(raw_layer, aoi)
            if clipped.empty:
                continue
            output_path = layers_dir / f"{spec.layer_id}.geojson"
            _write_geojson(clipped, output_path)
            layers_manifest.append(
                _layer_entry(
                    layer_id=spec.layer_id,
                    title=spec.title,
                    layer_type=spec.layer_type,
                    geojson_path=f"/layers/{spec.layer_id}.geojson",
                    opacity=spec.opacity,
                    legend_color=spec.legend_color,
                    source_url=spec.source_url,
                )
            )
        except Exception as exc:  # pragma: no cover - demo resilience branch
            print(f"[warn] skipping layer '{spec.layer_id}': {exc}")

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "aoi_boro_codes": boro_codes,
        "aoi_crs": "EPSG:4326",
        "projected_processing_crs": PROJECTED_CRS,
        "layers": layers_manifest,
    }

    manifest_path = layers_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch and clip NYC Open Data layers.")
    parser.add_argument(
        "--output-dir",
        default="web/public",
        help="Directory that will receive the `layers/` folder.",
    )
    parser.add_argument(
        "--boro-codes",
        default="1,2,3",
        help="Comma-separated borough codes. Use 1,2,4 to swap Brooklyn for Queens.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    codes = [int(item.strip()) for item in args.boro_codes.split(",") if item.strip()]
    manifest_path = run(Path(args.output_dir), codes)
    print(f"Manifest written to: {manifest_path}")
