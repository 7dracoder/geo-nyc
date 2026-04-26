"""3D / scalar-field modeling layer (synthetic + GemPy variants)."""

from geo_nyc.modeling.constraint_builder import ConstraintBuilder
from geo_nyc.modeling.constraints import (
    ConstraintSource,
    ExtentBox,
    FormationConstraint,
    GemPyInputs,
    GridResolution3D,
    Orientation,
    SurfacePoint,
)
from geo_nyc.modeling.extent import GridResolution, ModelExtent
from geo_nyc.modeling.field_builder import (
    FieldBuilderConfig,
    build_depth_to_bedrock_field_from_inputs,
    build_stub_depth_field,
)
from geo_nyc.modeling.field_export import export_field_to_npz
from geo_nyc.modeling.gempy_runner import GemPyRunner, GemPyRunnerConfig
from geo_nyc.modeling.rbf_runner import RBFRunner, RBFRunnerConfig
from geo_nyc.modeling.runner import EngineName, MeshRunner, MeshRunResult
from geo_nyc.modeling.synthetic_field import (
    FieldSource,
    ScalarField,
    build_depth_to_bedrock_field,
)

__all__ = [
    "ConstraintBuilder",
    "ConstraintSource",
    "EngineName",
    "ExtentBox",
    "FieldBuilderConfig",
    "FieldSource",
    "FormationConstraint",
    "GemPyInputs",
    "GemPyRunner",
    "GemPyRunnerConfig",
    "GridResolution",
    "GridResolution3D",
    "MeshRunResult",
    "MeshRunner",
    "ModelExtent",
    "Orientation",
    "RBFRunner",
    "RBFRunnerConfig",
    "ScalarField",
    "SurfacePoint",
    "build_depth_to_bedrock_field",
    "build_depth_to_bedrock_field_from_inputs",
    "build_stub_depth_field",
    "export_field_to_npz",
]
