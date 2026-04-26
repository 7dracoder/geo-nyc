"""Persist :class:`ScalarField` instances to disk for Part 3 to consume.

The on-disk shape is the contract between Part 2 (extraction +
modeling) and Part 3 (GIS / ML / optim). Keep it stable.

Disk layout
-----------

``<field_path>.npz`` (NumPy compressed archive)::

    grid : float32[ny, nx]   # the actual scalar values
    x    : float32[nx]       # cell centres along x (projected metres)
    y    : float32[ny]       # cell centres along y (projected metres)
    mask : uint8[ny, nx]     # OPTIONAL — 1 = valid cell, 0 = NaN

``<field_path>.json`` (sidecar, human-readable)::

    {
      "name": "depth_to_bedrock_m",
      "units": "meters_below_surface",
      "source": "rbf",
      "run_id": "r_20260101000000_abcdef12",
      "crs": "EPSG:32618",
      "projected_crs": "EPSG:32618",
      "geographic_crs": "EPSG:4326",
      "bbox": [-74.05, 40.48, -73.70, 40.93],
      "bbox_xy_m": {"x_min": ..., "x_max": ..., "y_min": ..., "y_max": ...},
      "extent": {"x_min": ..., "z_max": ...},
      "resolution_m": 100.0,
      "shape": [ny, nx],
      "dtype": "float32",
      "stats": {"min": ..., "max": ..., "mean": ...},
      "has_mask": false,
      "schema_version": 2
    }

``bbox`` is in WGS84 (EPSG:4326) lon/lat order, matching the
TileLayer / mapbox / deck.gl conventions Part 3 needs. We convert
from the projected CRS via :mod:`pyproj`; if that conversion fails
we leave ``bbox`` as ``null`` and keep ``bbox_xy_m`` populated.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from geo_nyc.exceptions import FieldExportError
from geo_nyc.logging import get_logger
from geo_nyc.modeling.synthetic_field import ScalarField

_LOG = get_logger(__name__)
_SCHEMA_VERSION = 2
_GEOGRAPHIC_CRS = "EPSG:4326"


def export_field_to_npz(
    field: ScalarField,
    output_path: Path,
    *,
    run_id: str | None = None,
    extra_metadata: dict[str, object] | None = None,
) -> tuple[Path, Path]:
    """Write the field as ``output_path`` (``.npz``) plus a sidecar JSON.

    Parameters
    ----------
    field:
        The scalar field to persist. ``field.crs`` is taken as the
        projected CRS in the sidecar.
    output_path:
        Destination ``.npz`` path. The sidecar is co-located with a
        ``.json`` suffix.
    run_id:
        Run identifier (e.g. ``r_20260101000000_abcdef12``) included
        verbatim in the sidecar so consumers can correlate the field
        with the ``manifest.json`` that produced it.
    extra_metadata:
        Optional free-form dict merged into the sidecar under the
        ``metadata`` key. Reserved for runner-specific provenance
        (e.g. ``{"engine": "rbf", "fallback_from": ["gempy"]}``).

    Returns the ``(npz_path, meta_path)`` tuple.
    """

    output_path = Path(output_path)
    if output_path.suffix.lower() != ".npz":
        raise FieldExportError(
            f"Field path must end in .npz, got {output_path.suffix!r}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    npz_payload: dict[str, np.ndarray] = {
        "grid": field.values.astype(np.float32, copy=False),
        "x": field.x_coords.astype(np.float32, copy=False),
        "y": field.y_coords.astype(np.float32, copy=False),
    }
    if field.mask is not None:
        npz_payload["mask"] = field.mask.astype(np.uint8, copy=False)

    try:
        np.savez_compressed(output_path, **npz_payload)
    except Exception as exc:  # pragma: no cover - defensive
        raise FieldExportError(
            f"Could not write field npz to {output_path}: {exc}"
        ) from exc

    bbox_lonlat = _project_bbox_to_lonlat(
        field.crs,
        x_min=field.extent.x_min,
        x_max=field.extent.x_max,
        y_min=field.extent.y_min,
        y_max=field.extent.y_max,
    )
    meta: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "name": field.name,
        "units": field.units,
        "source": field.source,
        "run_id": run_id,
        "crs": field.crs,
        "projected_crs": field.crs,
        "geographic_crs": _GEOGRAPHIC_CRS,
        "bbox": list(bbox_lonlat) if bbox_lonlat else None,
        "bbox_xy_m": {
            "x_min": float(field.extent.x_min),
            "x_max": float(field.extent.x_max),
            "y_min": float(field.extent.y_min),
            "y_max": float(field.extent.y_max),
        },
        "extent": asdict(field.extent),
        "resolution_m": field.resolution_m,
        "nx": field.nx,
        "ny": field.ny,
        "shape": list(field.values.shape),
        "dtype": "float32",
        "has_mask": field.has_mask,
        "stats": field.stats(),
    }
    if extra_metadata:
        meta["metadata"] = dict(extra_metadata)

    meta_path = output_path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return output_path, meta_path


def _project_bbox_to_lonlat(
    crs: str, *, x_min: float, x_max: float, y_min: float, y_max: float
) -> tuple[float, float, float, float] | None:
    """Convert a projected bbox to ``(lon_min, lat_min, lon_max, lat_max)``.

    Returns ``None`` if the projection can't be performed (e.g. invalid
    CRS, identity transform, or pyproj missing). The sidecar simply
    reports ``bbox: null`` in that case so downstream consumers can
    fall back to ``bbox_xy_m``.
    """

    try:
        from pyproj import Transformer
    except ImportError:  # pragma: no cover - pyproj is a hard dep
        return None

    try:
        transformer = Transformer.from_crs(crs, _GEOGRAPHIC_CRS, always_xy=True)
    except Exception as exc:
        _LOG.warning(
            "field_bbox_projection_failed",
            extra={"crs": crs, "error": str(exc)},
        )
        return None

    try:
        # Sample the four corners + midpoints so we capture any
        # rotation / curvature induced by the projection (the AABB of
        # the lon/lat polygon is *not* the projection of the
        # projected-AABB's corners).
        xs = np.array([x_min, x_max, x_max, x_min, (x_min + x_max) / 2.0])
        ys = np.array([y_min, y_min, y_max, y_max, (y_min + y_max) / 2.0])
        lon, lat = transformer.transform(xs, ys)
    except Exception as exc:
        _LOG.warning(
            "field_bbox_transform_failed",
            extra={"crs": crs, "error": str(exc)},
        )
        return None

    return (
        round(float(np.min(lon)), 6),
        round(float(np.min(lat)), 6),
        round(float(np.max(lon)), 6),
        round(float(np.max(lat)), 6),
    )


__all__ = ["export_field_to_npz"]
