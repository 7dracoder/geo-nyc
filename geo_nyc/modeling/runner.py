"""Pluggable mesh runner abstraction.

The run service pipes :class:`GemPyInputs` (Phase 7) into a *runner*,
which is anything that can turn those inputs into a list of
:class:`LayerMesh` ready for ``export_layers_to_gltf``. We keep three
implementations:

* :class:`GemPyRunner`  — full GemPy pipeline (heavy, optional extra).
* :class:`RBFRunner`    — scipy RBFInterpolator (default real path).
* :class:`SyntheticRunner` — original fixture-style horizontal slabs.

A runner is just a callable with a name and an availability check, so
the run service can probe each one in priority order, log which path
ran, and gracefully degrade. We pass back a :class:`MeshRunResult`
rather than a bare list because the manifest needs to record
provenance (engine, fallback reason, timing).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from geo_nyc.modeling.constraints import GemPyInputs
from geo_nyc.modeling.synthetic_mesh import LayerMesh

EngineName = str  # one of: "gempy", "rbf", "synthetic"


@dataclass(frozen=True, slots=True)
class MeshRunResult:
    """Output of one mesh runner invocation, plus provenance metadata."""

    engine: EngineName
    layers: list[LayerMesh]
    duration_ms: int
    metadata: dict[str, Any] = field(default_factory=dict)
    fallback_from: tuple[EngineName, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.layers


@runtime_checkable
class MeshRunner(Protocol):
    """Anything that can turn :class:`GemPyInputs` into layer meshes."""

    name: EngineName

    def is_available(self) -> bool:
        """Cheap probe: return ``True`` only if a real run will succeed."""

    def run(self, inputs: GemPyInputs) -> MeshRunResult:
        """Compute meshes; raise on hard failure."""


@dataclass(frozen=True, slots=True)
class _Timer:
    """Tiny context-managed wall clock so each runner reports duration."""

    start_ns: int

    @classmethod
    def start(cls) -> _Timer:
        return cls(time.perf_counter_ns())

    @property
    def elapsed_ms(self) -> int:
        return int((time.perf_counter_ns() - self.start_ns) / 1_000_000)


__all__ = [
    "EngineName",
    "MeshRunResult",
    "MeshRunner",
    "_Timer",
]
