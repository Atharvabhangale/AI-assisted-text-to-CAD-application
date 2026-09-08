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
* :mod:`cad_core.serialization` -- the canonical CAD document: the typed
  ``Part`` serialized to and from deterministic JSON, with a content hash.
  The document is the authoritative artifact; geometry, FeatureScript and
  every export are derived from it.

Importing this package does **not** load a geometry kernel. The local CAD
engine (:mod:`cad_core.local_cad`) and the edge selector
(:mod:`cad_core.edge_selection`) require CadQuery and are deliberately not
re-exported here.
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
from cad_core.serialization import (
    CANONICAL_ENCODING,
    HASH_ALGORITHM,
    CadDocumentError,
    DocumentParseError,
    DocumentValidationError,
    deserialize_part,
    load_part,
    part_from_json,
    part_hash,
    part_to_bytes,
    part_to_json,
    parts_equivalent,
    save_part,
    serialize_part,
)
from cad_core.validator import validate

__all__ = [
    "AdapterResult",
    "AXIS_VALUES",
    "Box",
    "CadDocumentError",
    "CANONICAL_ENCODING",
    "Chamfer",
    "check_geometric_rules",
    "CONSTRUCTIVE_TYPES",
    "Cylinder",
    "DEFAULT_AXIS",
    "deliver_part",
    "DeliveryReport",
    "deserialize_part",
    "DocumentParseError",
    "DocumentValidationError",
    "EDGE_SELECT_VALUES",
    "EdgeSelector",
    "Feature",
    "FEATURE_TYPES",
    "featurescript_for",
    "Fillet",
    "generate_featurescript",
    "GeneratedFeatureScript",
    "GEOMETRIC_RULES",
    "Handle",
    "HANDLE_KINDS",
    "HASH_ALGORITHM",
    "ID_PATTERN",
    "load_part",
    "MODIFIER_TYPES",
    "OnshapeAdapter",
    "OPERATIONS",
    "OperationStatus",
    "ORIGIN",
    "Part",
    "part_from_json",
    "part_hash",
    "part_to_bytes",
    "part_to_json",
    "parts_equivalent",
    "Position",
    "RecordingOnshapeAdapter",
    "save_part",
    "SCHEMA_VERSION",
    "SELECTOR_AXIS_VALUES",
    "serialize_part",
    "Size",
    "STATIC_RULES",
    "Subtract",
    "SUPPORTED_SCHEMA_MAJOR",
    "SUPPORTED_UNITS",
    "ThroughHole",
    "UnconfiguredOnshapeAdapter",
    "UnsupportedPartError",
    "validate",
    "ValidationError",
    "ValidationResult",
]
