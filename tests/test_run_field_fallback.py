"""Integration tests for :meth:`RunService._compute_field` fallback behaviour.

The mesh runner cascade is exercised in ``test_run_mesh_runners.py``;
here we focus on the *field* layer, which has its own independent
priority order:

1. RBF / GemPy from ``GemPyInputs.surface_points`` (when present).
2. Resampling the mesh layers (when surface points are unavailable).
3. Deterministic stub.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from geo_nyc.config import Settings
from geo_nyc.modeling.constraints import GemPyInputs
from geo_nyc.runs.run_service import RunService


@pytest.fixture
def service(isolated_settings: Settings) -> RunService:
    return RunService(settings=isolated_settings)


def _read_sidecar(field_dir: Path, run_id: str, filename: str) -> dict:
    return json.loads(
        (field_dir / run_id / filename).read_text(encoding="utf-8")
    )


def test_default_run_uses_rbf_field_from_inputs(
    service: RunService, isolated_settings: Settings
) -> None:
    manifest = service.create_run()

    field_summary = manifest.field_summary
    assert field_summary is not None
    assert field_summary["engine"] == "rbf"
    assert field_summary["fallback_from"] == []
    assert field_summary["units"] == "meters_below_surface"
    assert any(
        a["via"] == "gempy_inputs" and a["succeeded"]
        for a in field_summary["attempts"]
    )

    meta = manifest.artifact_by_kind("field_meta")
    assert meta is not None
    sidecar = _read_sidecar(
        isolated_settings.fields_dir, manifest.run_id, meta.filename
    )
    assert sidecar["source"] == "rbf"
    assert sidecar["metadata"]["mesh_engine"] in {"rbf", "gempy"}
    assert sidecar["run_id"] == manifest.run_id


def test_field_falls_back_to_layers_when_inputs_have_no_anchors(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the gempy_inputs.json on disk has no surface_points, the
    field builder must skip the RBF-from-inputs path and resample the
    mesh layers instead."""

    service = RunService(settings=isolated_settings)
    real_reload = service._reload_gempy_inputs

    def stripped(run_dir: Path) -> GemPyInputs | None:
        inputs = real_reload(run_dir)
        if inputs is None:
            return None
        return inputs.model_copy(update={"surface_points": []})

    monkeypatch.setattr(service, "_reload_gempy_inputs", stripped)

    manifest = service.create_run()

    field_summary = manifest.field_summary
    assert field_summary is not None
    # No anchors -> first stage is skipped; we should land on the
    # mesh-layer path. The resulting source label tracks the mesh
    # engine (rbf / gempy / synthetic).
    assert field_summary["engine"] in {"rbf", "gempy", "synthetic"}
    assert any(a["via"] == "mesh_layers" for a in field_summary["attempts"])


def test_field_falls_back_to_stub_when_layers_path_fails(
    isolated_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both inputs and layers fail -> stub must take over so the run
    still produces a usable field."""

    service = RunService(settings=isolated_settings)

    monkeypatch.setattr(service, "_reload_gempy_inputs", lambda run_dir: None)

    from geo_nyc.runs import run_service as run_service_module

    def boom(*args, **kwargs):
        raise RuntimeError("layers path is broken in this test")

    monkeypatch.setattr(
        run_service_module,
        "build_depth_to_bedrock_field",
        boom,
    )

    manifest = service.create_run()

    field_summary = manifest.field_summary
    assert field_summary is not None
    assert field_summary["engine"] == "stub"
    assert any(a["via"] == "stub" and a["succeeded"] for a in field_summary["attempts"])
    assert "synthetic" in field_summary["fallback_from"]

    meta = manifest.artifact_by_kind("field_meta")
    assert meta is not None
    sidecar = _read_sidecar(
        isolated_settings.fields_dir, manifest.run_id, meta.filename
    )
    assert sidecar["source"] == "stub"
    npz = isolated_settings.fields_dir / manifest.run_id / "depth_to_bedrock.npz"
    with np.load(npz) as data:
        # Stub still respects the contract.
        assert {"grid", "x", "y"} <= set(data.files)
        assert data["grid"].min() >= 0.0
