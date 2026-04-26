"""Synthetic, GemPy-free mesh generator.

Used by Phase 11 fixture mode and as a fallback whenever GemPy isn't
available. Each layer's *top* surface becomes a triangulated mesh; the
combination plays the role of GemPy's marching-cubes output for the
demo.

The geometry is intentionally simple:

* horizontal layered stratigraphy stacked between ``z_min`` and ``z_max``;
* a soft cosine "buried valley" depression on the bedrock surface so it
  looks like real subsurface data, not a perfectly flat plane;
* upper layers parallel-offset above the bedrock so layer thicknesses
  stay positive.

This gives the frontend something visually meaningful to render before
GemPy is wired in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from geo_nyc.modeling.extent import GridResolution, ModelExtent
from geo_nyc.parsers.dsl.ast import (
    DepositionEvent,
    IntrusionEvent,
    Program,
)

# Default colours by rock type when the DSL doesn't carry one.
_ROCK_TYPE_DEFAULT_COLOR: dict[str, str] = {
    "sedimentary": "#D2B48C",
    "volcanic": "#2F4F4F",
    "intrusive": "#808080",
    "metamorphic": "#5C4A3A",
}


@dataclass(frozen=True, slots=True)
class LayerMesh:
    """A single triangulated surface ready for mesh export."""

    surface_id: str
    name: str
    rock_type: str
    color_hex: str
    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int32

    def vertex_count(self) -> int:
        return int(self.vertices.shape[0])

    def face_count(self) -> int:
        return int(self.faces.shape[0])


def build_synthetic_layers(
    program: Program,
    extent: ModelExtent,
    *,
    resolution: GridResolution | None = None,
    color_overrides: dict[str, str] | None = None,
    bedrock_relief_m: float = 8.0,
    seed: int | None = 42,
) -> list[LayerMesh]:
    """Build one :class:`LayerMesh` per rock-producing event.

    Surfaces are emitted in stratigraphic order, oldest (deepest) first
    so the frontend can iterate the list and trust z-ordering. Erosion
    events are ignored at the geometry level (their effect on layer
    thickness already manifests via the bedrock relief).
    """

    rng = np.random.default_rng(seed)
    res = resolution or GridResolution()
    color_overrides = dict(color_overrides or {})

    rock_lookup = {r.id: r for r in program.rocks}
    rock_events = _stratigraphic_order(program)
    if not rock_events:
        return []

    n_layers = len(rock_events)
    layer_thickness = extent.depth / max(n_layers, 1)

    # Pre-compute the (x, y) grid once; the only thing that changes per
    # layer is z.
    xs = np.linspace(extent.x_min, extent.x_max, res.nx)
    ys = np.linspace(extent.y_min, extent.y_max, res.ny)
    xv, yv = np.meshgrid(xs, ys, indexing="xy")  # (ny, nx)
    relief = _bedrock_relief(xv, yv, extent, amplitude=bedrock_relief_m, rng=rng)

    layers: list[LayerMesh] = []
    # Floor of the deepest slab is the extent floor.
    prev_top_z: np.ndarray = np.full_like(xv, extent.z_min)
    for index, event in enumerate(rock_events):
        rock = rock_lookup.get(_rock_id_of(event))
        if rock is None:
            # The validator would have flagged this; defensive skip.
            continue
        color = color_overrides.get(rock.id) or _ROCK_TYPE_DEFAULT_COLOR.get(
            rock.rock_type.value, "#808080"
        )

        # z grows from extent.z_min (oldest, deepest) up to extent.z_max.
        base_z = extent.z_min + (index + 1) * layer_thickness
        # Add the same soft relief to every layer so they stay parallel
        # (a uniform relief means uniform thickness — clean visual).
        top_z = base_z + relief
        # Top layer sits *exactly* at the ground surface so the user can
        # tell where the surface is.
        if index == n_layers - 1:
            top_z = np.full_like(top_z, extent.z_max)
        # Younger surface should never dip below the older floor it
        # rests on — clamp before we feed it to the slab builder.
        top_z = np.maximum(top_z, prev_top_z + 0.05)

        vertices, faces = grid_slab_to_mesh(xv, yv, top_z, prev_top_z)
        layers.append(
            LayerMesh(
                surface_id=event.id,
                name=rock.name or rock.id,
                rock_type=rock.rock_type.value,
                color_hex=color,
                vertices=vertices,
                faces=faces,
            )
        )
        prev_top_z = top_z
    return layers


# --- helpers -------------------------------------------------------------


def _rock_id_of(event: DepositionEvent | IntrusionEvent) -> str:
    return event.rock_id


def _stratigraphic_order(program: Program) -> list[DepositionEvent | IntrusionEvent]:
    """Return rock-producing events sorted oldest-first.

    Strategy: events with absolute time sort by ``to_ma()`` (descending,
    older first). Events without absolute time fall back to declaration
    order, but always after dated events so we never put an undated unit
    deeper than a known-old one.
    """

    from geo_nyc.parsers.dsl.ast import AbsoluteTime  # local to avoid cycle

    rock_events: list[DepositionEvent | IntrusionEvent] = [
        *program.depositions,
        *program.intrusions,
    ]

    def sort_key(event: DepositionEvent | IntrusionEvent) -> tuple[int, float, int]:
        time = event.time
        if isinstance(time, AbsoluteTime):
            # Older = larger Ma value → we want older first (deepest), so
            # negate to make sort ascending.
            return (0, -time.to_ma(), 0)
        # Fallback: events with no absolute time sit on top of dated
        # ones, ordered by declaration index.
        return (1, 0.0, _declaration_index(program, event))

    return sorted(rock_events, key=sort_key)


def _declaration_index(program: Program, event: DepositionEvent | IntrusionEvent) -> int:
    for i, e in enumerate(program.all_events):
        if e.id == event.id:
            return i
    return 0  # pragma: no cover


def _bedrock_relief(
    xv: np.ndarray,
    yv: np.ndarray,
    extent: ModelExtent,
    *,
    amplitude: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """A smooth, slightly randomised cosine bowl + small noise.

    The bowl is centred on the extent and decays radially. A tiny dose
    of low-frequency noise prevents the surface from looking
    artificially perfect on screen.
    """

    cx, cy = extent.center_xy
    rx = (xv - cx) / max(extent.width / 2.0, 1e-6)
    ry = (yv - cy) / max(extent.height / 2.0, 1e-6)
    radius = np.sqrt(rx * rx + ry * ry)
    bowl = -amplitude * np.cos(np.minimum(radius, 1.0) * math.pi / 2.0)

    noise = rng.standard_normal(xv.shape) * (amplitude * 0.05)
    return bowl + noise


def grid_slab_to_mesh(
    xv: np.ndarray,
    yv: np.ndarray,
    top_z: np.ndarray,
    bottom_z: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a closed-prism mesh for one formation's slab.

    A slab is a "stratigraphic block": top surface (sampled from
    ``top_z``) + parallel bottom surface (sampled from ``bottom_z``) +
    four side walls knitting the two rims together. The result is a
    watertight triangulated solid the GLB pipeline can render with
    consistent normals (and which actually has *thickness* in z, so
    layers don't collapse into a paper-thin sheet when the mesh is
    auto-fit to a bounding box in three.js).

    All four input arrays must share shape ``(ny, nx)``.
    """

    if xv.shape != yv.shape or xv.shape != top_z.shape or xv.shape != bottom_z.shape:
        raise ValueError(
            "grid_slab_to_mesh requires xv, yv, top_z and bottom_z to share shape; "
            f"got xv={xv.shape}, yv={yv.shape}, top_z={top_z.shape}, bottom_z={bottom_z.shape}"
        )

    ny, nx = xv.shape
    n_grid = nx * ny

    top_verts = np.column_stack(
        [xv.ravel(), yv.ravel(), top_z.ravel()]
    ).astype(np.float64, copy=False)
    bot_verts = np.column_stack(
        [xv.ravel(), yv.ravel(), bottom_z.ravel()]
    ).astype(np.float64, copy=False)
    vertices = np.vstack([top_verts, bot_verts])

    cells = (ny - 1) * (nx - 1)
    n_top = cells * 2
    n_bot = cells * 2
    n_side = ((nx - 1) + (ny - 1)) * 2 * 2  # two walls per axis, 2 tris per cell
    faces = np.empty((n_top + n_bot + n_side, 3), dtype=np.int32)

    fi = 0
    for j in range(ny - 1):
        for i in range(nx - 1):
            v00 = j * nx + i
            v10 = j * nx + (i + 1)
            v01 = (j + 1) * nx + i
            v11 = (j + 1) * nx + (i + 1)
            # Top: CCW when viewed from +z (outward normal up).
            faces[fi] = (v00, v10, v11)
            faces[fi + 1] = (v00, v11, v01)
            fi += 2

    bot_offset = n_grid
    for j in range(ny - 1):
        for i in range(nx - 1):
            v00 = bot_offset + j * nx + i
            v10 = bot_offset + j * nx + (i + 1)
            v01 = bot_offset + (j + 1) * nx + i
            v11 = bot_offset + (j + 1) * nx + (i + 1)
            # Bottom: reverse winding so the outward normal points down.
            faces[fi] = (v00, v11, v10)
            faces[fi + 1] = (v00, v01, v11)
            fi += 2

    # South wall (j = 0): outward normal points -y.
    j = 0
    for i in range(nx - 1):
        t0 = j * nx + i
        t1 = j * nx + (i + 1)
        b0 = bot_offset + j * nx + i
        b1 = bot_offset + j * nx + (i + 1)
        faces[fi] = (t0, b0, b1)
        faces[fi + 1] = (t0, b1, t1)
        fi += 2

    # North wall (j = ny - 1): outward normal points +y.
    j = ny - 1
    for i in range(nx - 1):
        t0 = j * nx + i
        t1 = j * nx + (i + 1)
        b0 = bot_offset + j * nx + i
        b1 = bot_offset + j * nx + (i + 1)
        faces[fi] = (t0, t1, b1)
        faces[fi + 1] = (t0, b1, b0)
        fi += 2

    # West wall (i = 0): outward normal points -x.
    i = 0
    for j in range(ny - 1):
        t0 = j * nx + i
        t1 = (j + 1) * nx + i
        b0 = bot_offset + j * nx + i
        b1 = bot_offset + (j + 1) * nx + i
        faces[fi] = (t0, t1, b1)
        faces[fi + 1] = (t0, b1, b0)
        fi += 2

    # East wall (i = nx - 1): outward normal points +x.
    i = nx - 1
    for j in range(ny - 1):
        t0 = j * nx + i
        t1 = (j + 1) * nx + i
        b0 = bot_offset + j * nx + i
        b1 = bot_offset + (j + 1) * nx + i
        faces[fi] = (t0, b0, b1)
        faces[fi + 1] = (t0, b1, t1)
        fi += 2

    return vertices, faces


__all__ = [
    "GridResolution",
    "LayerMesh",
    "ModelExtent",
    "build_synthetic_layers",
    "grid_slab_to_mesh",
]
