"""Unit tests for :mod:`geo_nyc.modeling.field_export`."""

from __future__ import annotations

import json

import numpy as np
import pytest

from geo_nyc.exceptions import FieldExportError
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.modeling.field_export import export_field_to_npz
from geo_nyc.modeling.synthetic_field import ScalarField


def _make_field(*, with_mask: bool = False) -> ScalarField:
    extent = ModelExtent(
        x_min=580_000.0,
        x_max=583_000.0,
        y_min=4_507_000.0,
        y_max=4_510_000.0,
        z_min=-100.0,
        z_max=0.0,
    )
    nx, ny = 6, 4
    rng = np.random.default_rng(0)
    values = rng.uniform(low=2.0, high=42.0, size=(ny, nx)).astype(np.float32)
    mask = None
    if with_mask:
        mask = np.ones((ny, nx), dtype=np.uint8)
        mask[0, 0] = 0
    return ScalarField(
        name="depth_to_bedrock_m",
        units="meters_below_surface",
        crs="EPSG:32618",
        extent=extent,
        nx=nx,
        ny=ny,
        values=values,
        source="rbf",
        resolution_m=500.0,
        mask=mask,
    )


def test_export_writes_grid_x_y_keys(tmp_path) -> None:
    field = _make_field()
    npz_path, meta_path = export_field_to_npz(
        field, tmp_path / "depth.npz", run_id="r_test"
    )

    assert npz_path.is_file()
    assert meta_path.is_file()

    with np.load(npz_path) as data:
        assert set(data.files) == {"grid", "x", "y"}
        assert data["grid"].dtype == np.float32
        assert data["grid"].shape == (field.ny, field.nx)
        assert data["x"].shape == (field.nx,)
        assert data["y"].shape == (field.ny,)


def test_export_includes_mask_when_present(tmp_path) -> None:
    field = _make_field(with_mask=True)
    npz_path, _ = export_field_to_npz(
        field, tmp_path / "depth.npz", run_id="r_test"
    )

    with np.load(npz_path) as data:
        assert "mask" in data.files
        assert data["mask"].dtype == np.uint8
        assert data["mask"].shape == (field.ny, field.nx)
        assert data["mask"][0, 0] == 0


def test_sidecar_metadata_has_full_contract(tmp_path) -> None:
    field = _make_field()
    _, meta_path = export_field_to_npz(
        field,
        tmp_path / "depth.npz",
        run_id="r_20260101000000_abcdef12",
        extra_metadata={"engine": "rbf", "fallback_from": ["gempy"]},
    )

    sidecar = json.loads(meta_path.read_text(encoding="utf-8"))

    expected_keys = {
        "schema_version",
        "name",
        "units",
        "source",
        "run_id",
        "crs",
        "projected_crs",
        "geographic_crs",
        "bbox",
        "bbox_xy_m",
        "extent",
        "resolution_m",
        "nx",
        "ny",
        "shape",
        "dtype",
        "has_mask",
        "stats",
        "metadata",
    }
    assert expected_keys <= set(sidecar.keys())
    assert sidecar["schema_version"] == 2
    assert sidecar["units"] == "meters_below_surface"
    assert sidecar["source"] == "rbf"
    assert sidecar["run_id"] == "r_20260101000000_abcdef12"
    assert sidecar["geographic_crs"] == "EPSG:4326"
    assert sidecar["projected_crs"] == "EPSG:32618"
    assert sidecar["resolution_m"] == 500.0
    assert sidecar["shape"] == [field.ny, field.nx]
    assert sidecar["has_mask"] is False
    assert sidecar["metadata"] == {"engine": "rbf", "fallback_from": ["gempy"]}
    assert sidecar["stats"]["min"] >= 2.0


def test_sidecar_bbox_uses_pyproj_for_utm_18n(tmp_path) -> None:
    pytest.importorskip("pyproj")
    field = _make_field()
    _, meta_path = export_field_to_npz(field, tmp_path / "depth.npz", run_id="r")
    sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
    bbox = sidecar["bbox"]
    assert bbox is not None
    lon_min, lat_min, lon_max, lat_max = bbox
    assert -75.0 < lon_min < lon_max < -73.0
    assert 40.0 < lat_min < lat_max < 41.5


def test_sidecar_bbox_is_null_when_crs_unknown(tmp_path) -> None:
    extent = ModelExtent(
        x_min=0.0, x_max=10.0, y_min=0.0, y_max=10.0, z_min=-1.0, z_max=0.0,
    )
    field = ScalarField(
        name="depth",
        units="meters_below_surface",
        crs="not-a-real-crs",
        extent=extent,
        nx=4,
        ny=4,
        values=np.ones((4, 4), dtype=np.float32),
        source="stub",
        resolution_m=2.5,
    )
    _, meta_path = export_field_to_npz(field, tmp_path / "depth.npz", run_id="r")
    sidecar = json.loads(meta_path.read_text(encoding="utf-8"))
    assert sidecar["bbox"] is None
    assert sidecar["bbox_xy_m"]["x_min"] == 0.0


def test_export_rejects_non_npz_path(tmp_path) -> None:
    field = _make_field()
    with pytest.raises(FieldExportError):
        export_field_to_npz(field, tmp_path / "depth.bin")
