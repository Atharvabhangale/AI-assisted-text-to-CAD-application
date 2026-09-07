"""cad-core: the V1 CAD specification contract, typed and statically validated.

This package implements ``docs/cad-specification.md`` schema version 1.0.0:

* :mod:`cad_core.model` -- the typed representation of a specification document.
* :mod:`cad_core.validator` -- the deterministic static validator (rules S1-S20).
* :mod:`cad_core.errors` -- the structured error and result records.
* :mod:`cad_core.rules` -- rule codes and their requirement text.
* :mod:`cad_core.geometry` -- the boundary for the geometric rules E1-E5, which
  are **not implemented**: no geometry is produced anywhere in this package.
* :mod:`cad_core.featurescript` -- generates Onshape FeatureScript source text
  for the supported subset (a single box). It does not talk to Onshape.
* :mod:`cad_core.onshape_adapter` -- the boundary the application will
  eventually deliver FeatureScript through, plus :mod:`cad_core.onshape_fakes`
  for the implementations that exist today. **Neither contacts Onshape.**
"""

from cad_core.errors import ValidationError, ValidationResult
from cad_core.featurescript import UnsupportedPartError, generate_featurescript
from cad_core.geometry import GEOMETRIC_RULES, check_geometric_rules
from cad_core.onshape_adapter import (
    HANDLE_KINDS,
    OPERATIONS,
    AdapterResult,
    DeliveryReport,
    GeneratedFeatureScript,
    Handle,
    OnshapeAdapter,
    OperationStatus,
    deliver_part,
    featurescript_for,
)
from cad_core.onshape_fakes import RecordingOnshapeAdapter, UnconfiguredOnshapeAdapter
from cad_core.model import (
    AXIS_VALUES,
    CONSTRUCTIVE_TYPES,
    DEFAULT_AXIS,
    EDGE_SELECT_VALUES,
    FEATURE_TYPES,
    ID_PATTERN,
    MODIFIER_TYPES,
    ORIGIN,
    SCHEMA_VERSION,
    SELECTOR_AXIS_VALUES,
    SUPPORTED_SCHEMA_MAJOR,
    SUPPORTED_UNITS,
    Box,
    Chamfer,
    Cylinder,
    EdgeSelector,
    Feature,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
    ThroughHole,
)
from cad_core.rules import STATIC_RULES
from cad_core.validator import validate

__all__ = [
    "AXIS_VALUES",
    "AdapterResult",
    "Box",
    "CONSTRUCTIVE_TYPES",
    "Chamfer",
    "Cylinder",
    "DEFAULT_AXIS",
    "DeliveryReport",
    "EDGE_SELECT_VALUES",
    "EdgeSelector",
    "FEATURE_TYPES",
    "Feature",
    "Fillet",
    "GEOMETRIC_RULES",
    "GeneratedFeatureScript",
    "HANDLE_KINDS",
    "Handle",
    "ID_PATTERN",
    "MODIFIER_TYPES",
    "OPERATIONS",
    "ORIGIN",
    "OnshapeAdapter",
    "OperationStatus",
    "Part",
    "Position",
    "RecordingOnshapeAdapter",
    "SCHEMA_VERSION",
    "SELECTOR_AXIS_VALUES",
    "STATIC_RULES",
    "SUPPORTED_SCHEMA_MAJOR",
    "SUPPORTED_UNITS",
    "Size",
    "Subtract",
    "ThroughHole",
    "UnconfiguredOnshapeAdapter",
    "UnsupportedPartError",
    "ValidationError",
    "ValidationResult",
    "check_geometric_rules",
    "deliver_part",
    "featurescript_for",
    "generate_featurescript",
    "validate",
]
