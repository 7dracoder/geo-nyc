"""Integration tests for the mesh runner fallback chain in :class:`RunService`."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from geo_nyc.config import Settings
from geo_nyc.modeling.constraints import GemPyInputs
from geo_nyc.modeling.runner import EngineName, MeshRunResult
from geo_nyc.modeling.synthetic_mesh import LayerMesh
from geo_nyc.runs.manifest import RunStatus
from geo_nyc.runs.run_service import RunService

# ---------------------------------------------------------------------------
# Tiny in-memory runner stubs
# ---------------------------------------------------------------------------


@dataclass
class _RecordingRunner:
    """A MeshRunner that records calls and either succeeds or fails on demand."""

    name: EngineName
    available: bool = True
    behaviour: str = "succeed"  # "succeed" | "fail" | "empty"
    calls: list[GemPyInputs] = field(default_factory=list)

    def is_available(self) -> bool:
        return self.available

    def run(self, inputs: GemPyInputs) -> MeshRunResult:
        self.calls.append(inputs)
        if self.behaviour == "fail":
            raise RuntimeError(f"{self.name} blew up by design")
        if self.behaviour == "empty":
            return MeshRunResult(engine=self.name, layers=[], duration_ms=1)

        # Synth a single trivial 4-vertex layer per formation so the
        # mesh exporter has something to chew on.
        import numpy as np

        layers: list[LayerMesh] = []
        for formation in inputs.formations:
            verts = np.array(
                [
                    [0.0, 0.0, formation.stratigraphic_order * -10.0],
                    [1.0, 0.0, formation.stratigraphic_order * -10.0],
                    [0.0, 1.0, formation.stratigraphic_order * -10.0],
                    [1.0, 1.0, formation.stratigraphic_order * -10.0],
                ],
                dtype=np.float64,
            )
            faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
            layers.append(
                LayerMesh(
                    surface_id=f"S_{formation.rock_id}",
                    name=formation.name,
                    rock_type=formation.rock_type,
                    color_hex=formation.color_hex or "#808080",
                    vertices=verts,
                    faces=faces,
                )
            )
        return MeshRunResult(
            engine=self.name,
            layers=layers,
            duration_ms=1,
            metadata={"called_at": time.time_ns()},
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_available_runner_wins(isolated_settings: Settings) -> None:
    primary = _RecordingRunner(name="gempy")
    secondary = _RecordingRunner(name="rbf")

    service = RunService(
        settings=isolated_settings,
        mesh_runners=[primary, secondary],
    )

    manifest = service.create_run()
    assert manifest.status is RunStatus.SUCCEEDED

    summary = manifest.mesh_summary
    assert summary is not None
    assert summary["engine"] == "gempy"
    assert summary["fallback_from"] == []
    assert primary.calls and not secondary.calls

    mesh = manifest.artifact_by_kind("mesh")
    assert mesh is not None
    assert mesh.metadata["engine"] == "gempy"


def test_unavailable_runner_skipped(isolated_settings: Settings) -> None:
    primary = _RecordingRunner(name="gempy", available=False)
    secondary = _RecordingRunner(name="rbf")

    service = RunService(
        settings=isolated_settings,
        mesh_runners=[primary, secondary],
    )
    manifest = service.create_run()
    summary = manifest.mesh_summary

    assert summary["engine"] == "rbf"
    assert summary["fallback_from"] == ["gempy"]
    assert summary["attempts"][0] == {
        "engine": "gempy",
        "skipped": True,
        "reason": "not_available",
    }
    assert not primary.calls
    assert secondary.calls


def test_runner_failure_falls_through(isolated_settings: Settings) -> None:
    primary = _RecordingRunner(name="gempy", behaviour="fail")
    secondary = _RecordingRunner(name="rbf", behaviour="fail")

    service = RunService(
        settings=isolated_settings,
        mesh_runners=[primary, secondary],
    )
    manifest = service.create_run()

    summary = manifest.mesh_summary
    assert summary is not None
    assert summary["engine"] == "synthetic"
    assert summary["fallback_from"] == ["gempy", "rbf"]
    fail_attempts = [a for a in summary["attempts"] if a.get("succeeded") is False]
    assert len(fail_attempts) == 2
    assert primary.calls and secondary.calls


def test_empty_runner_output_is_treated_as_failure(
    isolated_settings: Settings,
) -> None:
    primary = _RecordingRunner(name="gempy", behaviour="empty")
    secondary = _RecordingRunner(name="rbf")

    service = RunService(
        settings=isolated_settings,
        mesh_runners=[primary, secondary],
    )
    manifest = service.create_run()
    summary = manifest.mesh_summary
    assert summary["engine"] == "rbf"
    assert summary["fallback_from"] == ["gempy"]


def _engine_attempt(summary: dict[str, Any], name: str) -> dict[str, Any]:
    for attempt in summary["attempts"]:
        if attempt["engine"] == name:
            return attempt
    raise AssertionError(f"engine {name} not found in mesh summary")
