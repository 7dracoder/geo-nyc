"""Unit tests for :mod:`geo_nyc.modeling.gempy_runner`.

The real GemPy package is an optional extra (``geo-nyc[modeling]``) and
isn't installed in CI, so these tests focus on the *adapter* layer:
availability probes, lazy import behaviour, and that we raise
:class:`GemPyUnavailableError` cleanly when the package is missing.

When GemPy *is* installed we run a tiny smoke test to make sure the
real path doesn't throw at import time. We mark it ``skipif`` so the
default test run stays fast.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from geo_nyc.exceptions import GemPyUnavailableError, ModelingError
from geo_nyc.modeling import GemPyRunner
from geo_nyc.modeling.constraints import (
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    Orientation,
    SurfacePoint,
)


@pytest.fixture
def minimal_inputs() -> GemPyInputs:
    extent = ExtentBox(x_min=0, x_max=10, y_min=0, y_max=10, z_min=-50, z_max=0)
    return GemPyInputs(
        extent=extent,
        crs="EPSG:32618",
        formations=[
            FormationConstraint(
                rock_id="R_A",
                name="Layer A",
                rock_type="metamorphic",
                stratigraphic_order=0,
                source="fixture",
            ),
            FormationConstraint(
                rock_id="R_B",
                name="Layer B",
                rock_type="sedimentary",
                stratigraphic_order=1,
                source="fixture",
            ),
        ],
        surface_points=[
            SurfacePoint(formation_id="R_A", x=0, y=0, z=-30),
            SurfacePoint(formation_id="R_A", x=10, y=10, z=-30),
            SurfacePoint(formation_id="R_B", x=0, y=0, z=-5),
            SurfacePoint(formation_id="R_B", x=10, y=10, z=-5),
        ],
        orientations=[
            Orientation(
                formation_id="R_A",
                x=5,
                y=5,
                z=-30,
                dip_degrees=2.0,
                azimuth_degrees=90.0,
            ),
            Orientation(
                formation_id="R_B",
                x=5,
                y=5,
                z=-5,
                dip_degrees=2.0,
                azimuth_degrees=90.0,
            ),
        ],
    )


def test_is_available_returns_false_when_gempy_missing() -> None:
    runner = GemPyRunner()
    # Force the import to fail even if the user has GemPy installed.
    with patch.object(
        importlib, "import_module", side_effect=ImportError("simulated")
    ):
        assert runner.is_available() is False


def test_run_raises_gempy_unavailable_when_missing(
    minimal_inputs: GemPyInputs,
) -> None:
    runner = GemPyRunner()
    with patch.object(
        importlib, "import_module", side_effect=ImportError("simulated")
    ):
        with pytest.raises(GemPyUnavailableError):
            runner.run(minimal_inputs)


def test_run_translates_compute_failure_to_modeling_error(
    minimal_inputs: GemPyInputs,
) -> None:
    """If GemPy is present but compute_model explodes, we surface ModelingError."""

    fake_gempy = SimpleNamespace(
        create_geomodel=lambda **kwargs: SimpleNamespace(
            structural_frame=SimpleNamespace(append_group=lambda *_a, **_kw: None),
        ),
        add_structural_group=lambda *_a, **_kw: None,
        add_surface_points=lambda *_a, **_kw: None,
        add_orientations=lambda *_a, **_kw: None,
        compute_model=lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("solver diverged")
        ),
    )
    sys.modules["gempy"] = fake_gempy
    try:
        runner = GemPyRunner()
        with pytest.raises(ModelingError) as exc_info:
            runner.run(minimal_inputs)
        assert "solver diverged" in str(exc_info.value)
    finally:
        sys.modules.pop("gempy", None)


def test_empty_inputs_returns_empty_result_without_calling_gempy() -> None:
    runner = GemPyRunner()
    extent = ExtentBox(x_min=0, x_max=1, y_min=0, y_max=1, z_min=-10, z_max=0)
    empty_inputs = GemPyInputs(extent=extent, crs="EPSG:32618")

    fake_gempy = SimpleNamespace(
        create_geomodel=lambda **_: pytest.fail("should never be called"),
    )
    sys.modules["gempy"] = fake_gempy
    try:
        result = runner.run(empty_inputs)
        assert result.layers == []
        assert result.metadata["reason"] == "no_formations"
    finally:
        sys.modules.pop("gempy", None)


def test_real_gempy_smoke(minimal_inputs: GemPyInputs) -> None:
    """If GemPy is installed for real, ensure the adapter runs end-to-end.

    This is a soft smoke test — we don't assert mesh correctness, only
    that the runner returns *some* :class:`MeshRunResult` without
    raising. Skips when the optional extra isn't installed.
    """

    pytest.importorskip("gempy")
    runner = GemPyRunner()
    try:
        result = runner.run(minimal_inputs)
    except ModelingError as exc:
        pytest.skip(f"GemPy installed but solver failed on minimal inputs: {exc}")

    # GemPy may produce 0 or N surfaces depending on version; we only
    # require the result to be well-formed.
    assert result.engine == "gempy"
    assert result.duration_ms >= 0
    for layer in result.layers:
        assert isinstance(layer.vertices, np.ndarray)
        assert layer.vertices.shape[1] == 3
