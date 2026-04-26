"""Unit tests for :mod:`geo_nyc.modeling.mesh_export`.

The frontend's only window into the geological model is the ``.glb``
file we serve from ``/static/exports``. If trimesh's exporter ever
changes the magic header, drops vertex colours, or breaks the multi-
file ``.gltf`` path, the demo silently regresses to a blank scene.
These tests pin the disk contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from geo_nyc.exceptions import MeshExportError
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.modeling.mesh_export import export_layers_to_gltf
from geo_nyc.modeling.synthetic_mesh import LayerMesh


def _square_layer(*, surface_id: str, z: float, color: str) -> LayerMesh:
    """Two-triangle quad with deterministic vertex/face counts."""

    vertices = np.array(
        [
            [0.0, 0.0, z],
            [1.0, 0.0, z],
            [1.0, 1.0, z],
            [0.0, 1.0, z],
        ],
        dtype=np.float64,
    )
    faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
    return LayerMesh(
        surface_id=surface_id,
        name=surface_id,
        rock_type="metamorphic",
        color_hex=color,
        vertices=vertices,
        faces=faces,
    )


def _two_layers() -> list[LayerMesh]:
    return [
        _square_layer(surface_id="S_BEDROCK", z=-50.0, color="#5C4A3A"),
        _square_layer(surface_id="S_FILL", z=0.0, color="#D2B48C"),
    ]


def test_glb_export_writes_magic_header_and_loads_back(tmp_path: Path) -> None:
    out = export_layers_to_gltf(_two_layers(), tmp_path / "model.glb")

    assert out.exists()
    assert out.suffix == ".glb"
    blob = out.read_bytes()
    assert blob[:4] == b"glTF", "GLB binary must start with the glTF magic header"

    # Round-trip: trimesh reads it back into a Scene with our two
    # named geometries.
    scene = trimesh.load(out, force="scene")
    assert isinstance(scene, trimesh.Scene)
    geom_names = set(scene.geometry.keys())
    assert {"S_BEDROCK", "S_FILL"} <= geom_names

    total_vertices = sum(g.vertices.shape[0] for g in scene.geometry.values())
    total_faces = sum(g.faces.shape[0] for g in scene.geometry.values())
    assert total_vertices == 8  # 4 verts per layer * 2 layers
    assert total_faces == 4  # 2 tris per layer * 2 layers


def test_glb_export_preserves_vertex_colors(tmp_path: Path) -> None:
    out = export_layers_to_gltf(_two_layers(), tmp_path / "model.glb")

    scene = trimesh.load(out, force="scene")
    bedrock = scene.geometry["S_BEDROCK"]
    fill = scene.geometry["S_FILL"]

    # The exporter writes per-vertex RGBA from each layer's hex string.
    bedrock_color = bedrock.visual.vertex_colors[0]
    fill_color = fill.visual.vertex_colors[0]
    assert tuple(bedrock_color[:3]) == (0x5C, 0x4A, 0x3A)
    assert tuple(fill_color[:3]) == (0xD2, 0xB4, 0x8C)
    # All four bedrock vertices must share the same colour.
    assert np.all(bedrock.visual.vertex_colors == bedrock_color)


def test_glb_export_embeds_extent_metadata(tmp_path: Path) -> None:
    extent = ModelExtent(
        x_min=0.0, x_max=1.0, y_min=0.0, y_max=1.0,
        z_min=-50.0, z_max=0.0,
    )
    out = export_layers_to_gltf(
        _two_layers(), tmp_path / "model.glb", extent=extent
    )

    scene = trimesh.load(out, force="scene")
    md = scene.metadata
    assert md.get("extent_z_min") == -50.0
    assert md.get("extent_z_max") == 0.0
    assert md.get("extent_x_max") == 1.0


def test_gltf_multi_file_export_writes_consistent_uris(tmp_path: Path) -> None:
    out = export_layers_to_gltf(_two_layers(), tmp_path / "demo.gltf")

    assert out.suffix == ".gltf"
    manifest = json.loads(out.read_text(encoding="utf-8"))

    siblings = sorted(p.name for p in tmp_path.iterdir())
    # Every buffer/image URI in the manifest must point at a sibling
    # file we actually wrote — otherwise the frontend GLTFLoader 404s
    # the moment it follows the reference.
    for entry in manifest.get("buffers", []) + manifest.get("images", []):
        uri = entry.get("uri")
        if uri is None:
            continue
        assert uri in siblings, (
            f"manifest references {uri!r} but only {siblings!r} are on disk"
        )


def test_export_rejects_unsupported_extension(tmp_path: Path) -> None:
    with pytest.raises(MeshExportError, match="Unsupported mesh extension"):
        export_layers_to_gltf(_two_layers(), tmp_path / "model.obj")


def test_export_rejects_empty_layer_list(tmp_path: Path) -> None:
    with pytest.raises(MeshExportError, match="empty mesh"):
        export_layers_to_gltf([], tmp_path / "model.glb")


def test_export_rejects_unsupported_color_string(tmp_path: Path) -> None:
    bad = LayerMesh(
        surface_id="S_BAD",
        name="bad",
        rock_type="metamorphic",
        color_hex="rgb(10,20,30)",  # not a #RRGGBB / #RRGGBBAA
        vertices=np.zeros((3, 3), dtype=np.float64),
        faces=np.array([[0, 1, 2]], dtype=np.int32),
    )
    with pytest.raises(MeshExportError, match="Unsupported colour string"):
        export_layers_to_gltf([bad], tmp_path / "model.glb")
