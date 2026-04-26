"""Integration tests for the fixture-mode run pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from geo_nyc.config import Settings
from geo_nyc.exceptions import RunNotFoundError
from geo_nyc.runs.manifest import RunStatus
from geo_nyc.runs.run_service import RunService


@pytest.fixture
def service(isolated_settings: Settings) -> RunService:
    return RunService(settings=isolated_settings)


def test_create_run_emits_full_artifact_set(
    service: RunService, isolated_settings: Settings
) -> None:
    manifest = service.create_run()
    assert manifest.status is RunStatus.SUCCEEDED
    assert manifest.error is None
    assert manifest.run_id.startswith("r_")
    assert manifest.duration_ms is not None and manifest.duration_ms >= 0

    kinds = {a.kind for a in manifest.artifacts}
    assert {
        "mesh",
        "field",
        "field_meta",
        "dsl",
        "extraction",
        "validation_report",
        "gempy_inputs",
    } <= kinds

    mesh = manifest.artifact_by_kind("mesh")
    assert mesh is not None
    assert mesh.bytes > 0
    assert mesh.url.endswith(f"/static/exports/{manifest.run_id}/{mesh.filename}")

    field = manifest.artifact_by_kind("field")
    assert field is not None
    assert field.url.endswith(f"/static/fields/{manifest.run_id}/{field.filename}")

    run_dir = isolated_settings.runs_dir / manifest.run_id
    assert (run_dir / "manifest.json").is_file()
    assert (run_dir / "validation_report.json").is_file()
    assert (run_dir / "dsl.txt").is_file()
    assert (run_dir / "extraction.json").is_file()
    assert (run_dir / "gempy_inputs.json").is_file()

    summary = manifest.gempy_inputs_summary
    assert summary is not None
    assert summary["succeeded"] is True
    assert summary["demo_ready"] is True
    assert summary["formation_count"] == 4
    assert summary["surface_point_count"] >= 8
    assert summary["orientation_count"] >= 4
    assert summary["formations_by_source"].get("fixture") == 4

    mesh_summary = manifest.mesh_summary
    assert mesh_summary is not None
    assert mesh_summary["engine"] in {"rbf", "gempy"}
    assert mesh_summary["layer_count"] == 4
    assert mesh_summary["vertex_count"] > 0
    assert mesh.metadata["engine"] == mesh_summary["engine"]

    mesh_path = isolated_settings.exports_dir / manifest.run_id / mesh.filename
    assert mesh_path.is_file()
    assert mesh_path.stat().st_size > 0
    assert mesh_path.read_bytes()[:4] == b"glTF"  # GLB magic header

    field_path = isolated_settings.fields_dir / manifest.run_id / field.filename
    assert field_path.is_file()
    with np.load(field_path) as data:
        assert {"grid", "x", "y"} <= set(data.files)
        assert data["grid"].ndim == 2
        assert data["grid"].dtype == np.float32
        assert data["grid"].min() >= 0.0
        ny, nx = data["grid"].shape
        assert data["x"].shape == (nx,)
        assert data["y"].shape == (ny,)

    field_meta = manifest.artifact_by_kind("field_meta")
    assert field_meta is not None
    field_meta_path = (
        isolated_settings.fields_dir / manifest.run_id / field_meta.filename
    )
    sidecar = json.loads(field_meta_path.read_text(encoding="utf-8"))
    assert sidecar["units"] == "meters_below_surface"
    assert sidecar["run_id"] == manifest.run_id
    assert sidecar["source"] in {"gempy", "rbf", "synthetic", "stub"}
    assert sidecar["projected_crs"] == sidecar["crs"]
    assert sidecar["geographic_crs"] == "EPSG:4326"
    # Demo fixture sits in EPSG:32618 (NYC). pyproj should populate
    # the WGS84 bbox; if pyproj couldn't load, tolerate null.
    if sidecar["bbox"] is not None:
        lon_min, lat_min, lon_max, lat_max = sidecar["bbox"]
        assert -75.0 < lon_min < lon_max < -73.0
        assert 40.0 < lat_min < lat_max < 41.5
    assert sidecar["resolution_m"] is not None and sidecar["resolution_m"] > 0
    assert sidecar["shape"] == list(field.metadata["shape"])

    field_summary = manifest.field_summary
    assert field_summary is not None
    assert field_summary["engine"] in {"gempy", "rbf", "synthetic", "stub"}
    assert field_summary["units"] == "meters_below_surface"
    assert field_summary["stats"]["min"] >= 0.0


def test_get_run_round_trips_manifest(service: RunService) -> None:
    created = service.create_run()
    fetched = service.get_run(created.run_id)
    assert fetched.run_id == created.run_id
    assert fetched.status is RunStatus.SUCCEEDED
    assert fetched.layer_summary == created.layer_summary


def test_get_run_falls_back_to_disk(
    service: RunService, isolated_settings: Settings
) -> None:
    created = service.create_run()
    service._purge_cache()  # type: ignore[attr-defined]
    fetched = service.get_run(created.run_id)
    assert fetched.run_id == created.run_id


def test_get_run_unknown_id_raises(service: RunService) -> None:
    with pytest.raises(RunNotFoundError):
        service.get_run("does-not-exist")


def test_failed_run_records_error_in_manifest(
    service: RunService,
    isolated_settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_fixtures = tmp_path / "bad-fixtures"
    bad_fixtures.mkdir()
    (bad_fixtures / "nyc_demo").mkdir()
    (bad_fixtures / "nyc_demo" / "extraction.json").write_text(
        json.dumps({"site": {}}), encoding="utf-8"
    )
    (bad_fixtures / "nyc_demo" / "dsl.txt").write_text("ROCK R1 [ name: \"Bad\" ]")

    # Force the service to use the broken fixture set.
    monkeypatch.setattr(
        type(isolated_settings),
        "fixtures_dir",
        property(lambda self: bad_fixtures),
    )

    manifest = service.create_run()
    assert manifest.status is RunStatus.FAILED
    assert manifest.error is not None
    failure_path = isolated_settings.runs_dir / manifest.run_id / "manifest.json"
    assert failure_path.is_file()
