"""Write :class:`LayerMesh` lists to glTF / glb files.

trimesh's built-in glTF exporter handles vertex / index packing,
per-vertex colour attributes, and the binary buffer layout for us.

We canonicalise on the **.glb** binary form for run artifacts because
it is a single file, embeds buffers inline, and is what Three.js's
``GLTFLoader`` consumes directly without extra HTTP round-trips. We
still accept ``.gltf`` for callers who explicitly want the JSON
manifest form (in which case trimesh produces a multi-asset bundle and
we write each asset alongside the requested path).

The exporter also performs two display-only transforms before writing:

1. **+Y up rotation.** Layer meshes are produced in geo-engineering
   convention (``+Z`` up, depth grows negative). glTF / Three.js use
   ``+Y`` up by default, so we swap axes once at export time. Layers
   then stack vertically in the dock instead of pointing into the
   camera.
2. **Vertical exaggeration.** NYC subsurface depth (~200 m) is tiny
   next to the AOI footprint (~1 km), so without exaggeration the
   stack collapses into a sliver when the dock auto-fits the bounding
   box. A ``5×`` default makes the column readable while still being
   honest about which axis is up.

Both transforms only touch the geometry written to the GLB; the
``extent_*`` scene metadata stays in original model coordinates so
downstream consumers (field grid, manifest) keep their numeric
contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh

from geo_nyc.exceptions import MeshExportError
from geo_nyc.modeling.extent import ModelExtent
from geo_nyc.modeling.synthetic_mesh import LayerMesh

# Default vertical exaggeration applied to the GLB so the layered
# stack reads visually in the dock. Picked empirically: with the
# fixture extent (1000 m × 1000 m × 200 m) a ``5×`` exaggeration makes
# all three axes roughly the same length once the dock auto-fits the
# bounding box.
_DEFAULT_VERTICAL_EXAGGERATION = 5.0


def export_layers_to_gltf(
    layers: list[LayerMesh],
    output_path: Path,
    *,
    extent: ModelExtent | None = None,
    vertical_exaggeration: float = _DEFAULT_VERTICAL_EXAGGERATION,
    swap_to_y_up: bool = True,
) -> Path:
    """Export ``layers`` to a single ``.gltf`` (or ``.glb``) scene.

    Each layer becomes a named scene node so the frontend can toggle
    surfaces independently. Per-vertex colours encode the layer's hex
    colour, so the file is fully self-contained — no external materials.
    """

    if not layers:
        raise MeshExportError("No layers provided; cannot export an empty mesh.")
    if vertical_exaggeration <= 0.0:
        raise MeshExportError(
            f"vertical_exaggeration must be > 0, got {vertical_exaggeration!r}"
        )

    output_path = Path(output_path)
    suffix = output_path.suffix.lower()
    if suffix not in {".gltf", ".glb"}:
        raise MeshExportError(
            f"Unsupported mesh extension '{suffix}'. Use .gltf or .glb."
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    scene = trimesh.Scene()
    for layer in layers:
        gltf_vertices = _model_to_gltf_coords(
            np.asarray(layer.vertices, dtype=np.float64),
            vertical_exaggeration=vertical_exaggeration,
            swap_to_y_up=swap_to_y_up,
        )
        mesh = trimesh.Trimesh(
            vertices=gltf_vertices,
            faces=np.asarray(layer.faces, dtype=np.int32),
            vertex_colors=np.tile(
                _hex_to_rgba(layer.color_hex),
                (layer.vertex_count(), 1),
            ),
            process=False,
        )
        mesh.metadata.update(
            {
                "surface_id": layer.surface_id,
                "name": layer.name,
                "rock_type": layer.rock_type,
                "color_hex": layer.color_hex,
            }
        )
        scene.add_geometry(mesh, node_name=layer.surface_id, geom_name=layer.surface_id)

    if extent is not None:
        scene.metadata.update(
            {
                "extent_x_min": extent.x_min,
                "extent_x_max": extent.x_max,
                "extent_y_min": extent.y_min,
                "extent_y_max": extent.y_max,
                "extent_z_min": extent.z_min,
                "extent_z_max": extent.z_max,
                "vertical_exaggeration": float(vertical_exaggeration),
                "up_axis": "Y" if swap_to_y_up else "Z",
            }
        )

    try:
        if suffix == ".glb":
            data = scene.export(file_type="glb")
            if not isinstance(data, (bytes, bytearray)):
                raise MeshExportError(
                    f"Expected bytes from glb export, got {type(data)!r}"
                )
            output_path.write_bytes(bytes(data))
        else:
            # .gltf: trimesh returns a dict { "scene.gltf": bytes,
            # "scene.bin": bytes, ... }. We rewrite filenames to share
            # the user-requested stem so multiple runs don't collide
            # when emitted into the same directory.
            assets = scene.export(file_type="gltf")
            if not isinstance(assets, dict):
                raise MeshExportError(
                    f"Expected dict from gltf export, got {type(assets)!r}"
                )
            stem = output_path.stem
            manifest_path: Path | None = None
            renamed: dict[str, str] = {}
            for old_name in assets:
                new_name = old_name.replace("scene", stem, 1) if "scene" in old_name else old_name
                renamed[old_name] = new_name
            for old_name, blob in assets.items():
                new_name = renamed[old_name]
                target = output_path.parent / new_name
                if new_name.endswith(".gltf"):
                    target = output_path
                    blob = _patch_gltf_buffer_uris(blob, renamed)
                    manifest_path = target
                target.write_bytes(blob)
            if manifest_path is None:
                raise MeshExportError(
                    "trimesh did not produce a .gltf manifest as expected."
                )
    except MeshExportError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise MeshExportError(f"trimesh failed to export scene: {exc}") from exc

    return output_path


def _model_to_gltf_coords(
    vertices: np.ndarray,
    *,
    vertical_exaggeration: float,
    swap_to_y_up: bool,
) -> np.ndarray:
    """Transform model-coord vertices for storage in the GLB.

    Model coords use ``+Z`` up (depth grows negative). When
    ``swap_to_y_up`` is True we rotate -90° around the X axis so the
    geological stack lines up with glTF's ``+Y`` up convention:

    ``(x, y, z)_model  →  (x, z * v_exag, -y)_gltf``

    The optional vertical exaggeration scales the new ``y`` (up) axis
    so a thin subsurface column stays readable when the dock auto-fits
    the bounding box.
    """

    if vertices.size == 0:
        return vertices.astype(np.float64, copy=False)

    if not swap_to_y_up:
        out = vertices.astype(np.float64, copy=True)
        out[:, 2] *= vertical_exaggeration
        return out

    out = np.empty_like(vertices, dtype=np.float64)
    out[:, 0] = vertices[:, 0]
    out[:, 1] = vertices[:, 2] * vertical_exaggeration
    out[:, 2] = -vertices[:, 1]
    return out


def _hex_to_rgba(value: str) -> np.ndarray:
    """Convert ``#RRGGBB`` (or ``#RRGGBBAA``) to ``[r, g, b, a]`` uint8."""

    s = value.lstrip("#")
    if len(s) == 6:
        r, g, b = (int(s[i : i + 2], 16) for i in (0, 2, 4))
        return np.array([r, g, b, 255], dtype=np.uint8)
    if len(s) == 8:
        r, g, b, a = (int(s[i : i + 2], 16) for i in (0, 2, 4, 6))
        return np.array([r, g, b, a], dtype=np.uint8)
    raise MeshExportError(f"Unsupported colour string: {value!r}")


def _patch_gltf_buffer_uris(blob: bytes, renamed: dict[str, str]) -> bytes:
    """Update buffer / image ``uri`` fields after we rename sidecar files."""

    try:
        manifest = json.loads(blob.decode("utf-8"))
    except Exception:
        return blob

    def remap(uri: str | None) -> str | None:
        if uri is None:
            return None
        return renamed.get(uri, uri)

    for key in ("buffers", "images"):
        for entry in manifest.get(key, []):
            new = remap(entry.get("uri"))
            if new is not None:
                entry["uri"] = new

    return json.dumps(manifest, separators=(",", ":")).encode("utf-8")


__all__ = ["export_layers_to_gltf"]
