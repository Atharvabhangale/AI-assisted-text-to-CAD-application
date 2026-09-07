"""Deterministic static validator for the V1 CAD specification.

Implements the static rules S1-S20 of ``docs/cad-specification.md``.  The
geometric rules E1-E5 are *not* implemented and are never reported here; see
:mod:`cad_core.geometry`.

The validator takes a parsed JSON document (the object produced by
``json.load``) and returns every violation it can determine.  It is strict by
construction: unknown fields and unknown feature types are rejected, never
ignored.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from cad_core.errors import ValidationError, ValidationResult
from cad_core.model import (
    AXIS_VALUES,
    COMMON_FEATURE_FIELDS,
    CONSTRUCTIVE_TYPES,
    DEFAULT_AXIS,
    EDGE_SELECT_VALUES,
    FEATURE_PARAMETERS,
    FEATURE_TYPES,
    ID_PATTERN,
    OPTIONAL_ROOT_FIELDS,
    ORIGIN,
    REQUIRED_ROOT_FIELDS,
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

_ID_RE = re.compile(ID_PATTERN)
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_VECTOR_KEYS = ("x", "y", "z")


# --- error accumulation -----------------------------------------------------


class _Errors:
    """Ordered, append-only collector of validation errors."""

    def __init__(self) -> None:
        self.items: List[ValidationError] = []

    def add(
        self,
        rule: str,
        message: str,
        *,
        path: Optional[str] = None,
        feature_id: Optional[str] = None,
        feature_index: Optional[int] = None,
    ) -> None:
        self.items.append(
            ValidationError(
                rule=rule,
                message=message,
                feature_id=feature_id,
                field_path=path,
                feature_index=feature_index,
            )
        )

    def __len__(self) -> int:
        return len(self.items)


@dataclass
class _Record:
    """What the per-feature pass learned about one entry of ``features``."""

    index: int
    raw: Any
    feature_id: Optional[str]
    feature_type: Optional[str]
    feature: Optional[Feature]
    id_is_valid: bool


# --- small typed predicates -------------------------------------------------


def _is_number(value: Any) -> bool:
    """True for a JSON number. ``bool`` is not a number, though it subclasses int."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def _describe(value: Any) -> str:
    """Name a value's JSON type for an error message."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


# --- public entry point -----------------------------------------------------


def validate(document: Any) -> ValidationResult:
    """Statically validate one CAD specification document.

    Args:
        document: A parsed JSON document, i.e. the value returned by
            ``json.load``/``json.loads``.  Not a JSON string.

    Returns:
        A :class:`~cad_core.errors.ValidationResult`.  Every violation the
        validator can determine is reported; it does not stop at the first
        error.  ``errors`` is ordered deterministically: root-level violations
        first, then per-feature violations in document order, then the
        cross-feature violations (id uniqueness, then references and feature
        order).  The same document always yields the same list.

        Only the static rules S1-S20 are checked.  Rules E1-E5 require geometry
        and are never reported.
    """
    errors = _Errors()

    if not isinstance(document, dict):
        errors.add(
            "S1",
            f"the document must be a single JSON object, got {_describe(document)}",
        )
        return ValidationResult(valid=False, errors=tuple(errors.items), part=None)

    _validate_root_fields(document, errors)
    schema_version = _validate_schema_version(document, errors)
    units = _validate_units(document, errors)
    name = _validate_name(document, errors)
    description = _validate_description(document, errors)

    records = _validate_features_list(document, errors)
    duplicate_ids = _validate_unique_ids(records, errors)
    _validate_order_and_references(records, errors, duplicate_ids=duplicate_ids)

    part = _build_part(schema_version, units, name, description, records, errors)
    return ValidationResult(valid=not errors.items, errors=tuple(errors.items), part=part)


# --- root object (Section B.1) ---------------------------------------------


def _validate_root_fields(document: Dict[str, Any], errors: _Errors) -> None:
    """S1: required root fields present. S3: no unknown root fields."""
    for field in REQUIRED_ROOT_FIELDS:
        if field not in document:
            errors.add("S1", f"the document is missing the required field {field!r}", path=field)

    allowed = set(REQUIRED_ROOT_FIELDS) | set(OPTIONAL_ROOT_FIELDS)
    for field in sorted(set(document) - allowed):
        errors.add("S3", f"unknown field {field!r} on the document", path=field)


def _validate_schema_version(document: Dict[str, Any], errors: _Errors) -> Optional[str]:
    """S4: valid MAJOR.MINOR.PATCH string whose major version is supported."""
    if "schema_version" not in document:
        return None
    value = document["schema_version"]
    if not isinstance(value, str):
        errors.add(
            "S4",
            f"'schema_version' must be a version string, got {_describe(value)}",
            path="schema_version",
        )
        return None
    match = _VERSION_RE.match(value)
    if match is None:
        errors.add(
            "S4",
            f"'schema_version' {value!r} is not a MAJOR.MINOR.PATCH version string",
            path="schema_version",
        )
        return None
    major = int(match.group(1))
    if major != SUPPORTED_SCHEMA_MAJOR:
        errors.add(
            "S4",
            f"schema major version {major} is not supported; this validator "
            f"implements major version {SUPPORTED_SCHEMA_MAJOR}",
            path="schema_version",
        )
        return None
    return value


def _validate_units(document: Dict[str, Any], errors: _Errors) -> Optional[str]:
    """S5: units is "mm"."""
    if "units" not in document:
        return None
    value = document["units"]
    if value not in SUPPORTED_UNITS or not isinstance(value, str):
        accepted = ", ".join(repr(unit) for unit in SUPPORTED_UNITS)
        errors.add(
            "S5",
            f"'units' must be one of {accepted} in V1; got {value!r}. V1 does "
            f"not convert other unit systems",
            path="units",
        )
        return None
    return value


def _validate_name(document: Dict[str, Any], errors: _Errors) -> Optional[str]:
    """S1: 'name' has its declared type."""
    if "name" not in document:
        return None
    value = document["name"]
    if not isinstance(value, str):
        errors.add("S1", f"'name' must be a string, got {_describe(value)}", path="name")
        return None
    return value


def _validate_description(document: Dict[str, Any], errors: _Errors) -> Optional[str]:
    """S1: optional 'description' has its declared type."""
    if "description" not in document:
        return None
    value = document["description"]
    if not isinstance(value, str):
        errors.add(
            "S1",
            f"'description' must be a string, got {_describe(value)}",
            path="description",
        )
        return None
    return value


# --- feature list (Section B.2) --------------------------------------------


def _validate_features_list(document: Dict[str, Any], errors: _Errors) -> List[_Record]:
    """S2: features is a non-empty array; then validate each entry."""
    if "features" not in document:
        return []
    raw_features = document["features"]
    if not isinstance(raw_features, list):
        errors.add(
            "S2",
            f"'features' must be an array, got {_describe(raw_features)}",
            path="features",
        )
        return []
    if not raw_features:
        errors.add("S2", "'features' must contain at least one feature", path="features")
        return []
    return [_validate_feature(index, raw, errors) for index, raw in enumerate(raw_features)]


def _validate_feature(index: int, raw: Any, errors: _Errors) -> _Record:
    """Validate one feature: structure, unknown fields, then its parameters."""
    path = f"features[{index}]"
    if not isinstance(raw, dict):
        errors.add(
            "S2",
            f"a feature must be an object, got {_describe(raw)}",
            path=path,
            feature_index=index,
        )
        return _Record(index, raw, None, None, None, False)

    feature_id, id_is_valid = _validate_feature_id(index, raw, errors)
    feature_type = _validate_feature_type(index, raw, feature_id, errors)
    if feature_type is None:
        return _Record(index, raw, feature_id, None, None, id_is_valid)

    required, optional = FEATURE_PARAMETERS[feature_type]
    allowed = set(COMMON_FEATURE_FIELDS) | set(required) | set(optional)
    for field in sorted(set(raw) - allowed):
        errors.add(
            "S3",
            f"unknown field {field!r} on a {feature_type} feature",
            path=f"{path}.{field}",
            feature_id=feature_id,
            feature_index=index,
        )

    missing = [field for field in required if field not in raw]
    for field in missing:
        errors.add(
            "S2",
            f"a {feature_type} feature requires the parameter {field!r}",
            path=f"{path}.{field}",
            feature_id=feature_id,
            feature_index=index,
        )

    feature = _validate_feature_parameters(index, raw, feature_id, feature_type, errors)
    if not id_is_valid or missing:
        feature = None
    return _Record(index, raw, feature_id, feature_type, feature, id_is_valid)


def _validate_feature_id(index: int, raw: Dict[str, Any], errors: _Errors) -> Tuple[Optional[str], bool]:
    """S2: 'id' is a required string. S8: it matches the id pattern.

    Returns the id (kept even when malformed, so later errors can name it) and
    whether it is usable as a solid name.
    """
    path = f"features[{index}].id"
    if "id" not in raw:
        errors.add("S2", "a feature requires a string 'id'", path=path, feature_index=index)
        return None, False
    value = raw["id"]
    if not isinstance(value, str):
        errors.add(
            "S2",
            f"a feature 'id' must be a string, got {_describe(value)}",
            path=path,
            feature_index=index,
        )
        return None, False
    if _ID_RE.match(value) is None:
        errors.add(
            "S8",
            f"feature id {value!r} must match {ID_PATTERN}",
            path=path,
            feature_id=value,
            feature_index=index,
        )
        return value, False
    return value, True


def _validate_feature_type(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[str]:
    """S2: 'type' is a required string. S3: it is one of the six V1 types."""
    path = f"features[{index}].type"
    if "type" not in raw:
        errors.add(
            "S2",
            "a feature requires a string 'type'",
            path=path,
            feature_id=feature_id,
            feature_index=index,
        )
        return None
    value = raw["type"]
    if not isinstance(value, str):
        errors.add(
            "S2",
            f"a feature 'type' must be a string, got {_describe(value)}",
            path=path,
            feature_id=feature_id,
            feature_index=index,
        )
        return None
    if value not in FEATURE_TYPES:
        supported = ", ".join(FEATURE_TYPES)
        errors.add(
            "S3",
            f"unknown feature type {value!r}; V1 supports only: {supported}",
            path=path,
            feature_id=feature_id,
            feature_index=index,
        )
        return None
    return value


# --- feature parameters (Section C) ----------------------------------------


def _validate_feature_parameters(
    index: int,
    raw: Dict[str, Any],
    feature_id: Optional[str],
    feature_type: str,
    errors: _Errors,
) -> Optional[Feature]:
    """Dispatch to the per-type parameter validator."""
    if feature_type == "box":
        return _validate_box(index, raw, feature_id, errors)
    if feature_type == "cylinder":
        return _validate_cylinder(index, raw, feature_id, errors)
    if feature_type == "through_hole":
        return _validate_through_hole(index, raw, feature_id, errors)
    if feature_type == "subtract":
        return _validate_subtract(index, raw, feature_id, errors)
    if feature_type == "fillet":
        return _validate_fillet(index, raw, feature_id, errors)
    if feature_type == "chamfer":
        return _validate_chamfer(index, raw, feature_id, errors)
    raise AssertionError(f"unhandled feature type {feature_type!r}")  # pragma: no cover


def _validate_box(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[Box]:
    """Section C.1. Rules S19 (size shape), S10 (positive size), S19 (position)."""
    context = _Context(index, feature_id, errors)
    size = _validate_size(raw.get("size"), "size", context) if "size" in raw else None
    position = _validate_optional_position(raw, context)
    if size is None or position is None or feature_id is None:
        return None
    return Box(id=feature_id, size=size, position=position)


def _validate_cylinder(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[Cylinder]:
    """Section C.2. Rules S20/S11 (diameter, height), S12 (axis), S19 (position)."""
    context = _Context(index, feature_id, errors)
    diameter = _validate_length(raw.get("diameter"), "diameter", "S11", context) if "diameter" in raw else None
    height = _validate_length(raw.get("height"), "height", "S11", context) if "height" in raw else None
    position = _validate_optional_position(raw, context)
    axis = _validate_optional_axis(raw, context)
    parts = (diameter, height, position, axis)
    if any(part is None for part in parts) or feature_id is None:
        return None
    return Cylinder(
        id=feature_id, diameter=diameter, height=height, position=position, axis=axis
    )


def _validate_through_hole(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[ThroughHole]:
    """Section C.3. Rules S20/S13 (diameter), S19 (position), S12 (axis), S2 (target)."""
    context = _Context(index, feature_id, errors)
    target = _validate_reference(raw.get("target"), "target", context) if "target" in raw else None
    diameter = _validate_length(raw.get("diameter"), "diameter", "S13", context) if "diameter" in raw else None
    position = (
        _validate_position(raw.get("position"), "position", context) if "position" in raw else None
    )
    axis = _validate_optional_axis(raw, context)
    parts = (target, diameter, position, axis)
    if any(part is None for part in parts) or feature_id is None:
        return None
    return ThroughHole(
        id=feature_id, target=target, diameter=diameter, position=position, axis=axis
    )


def _validate_subtract(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[Subtract]:
    """Section C.4. Rules S2 (target), S14 (tools array), S15 (tool identity)."""
    context = _Context(index, feature_id, errors)
    target = _validate_reference(raw.get("target"), "target", context) if "target" in raw else None
    tools = _validate_tools(raw.get("tools"), context) if "tools" in raw else None
    if tools is not None:
        _validate_tool_identity(target, tools, context)
    if target is None or tools is None or feature_id is None:
        return None
    return Subtract(id=feature_id, target=target, tools=tools)


def _validate_fillet(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[Fillet]:
    """Section C.5. Rules S2 (target), S20/S16 (radius), S18 (edges)."""
    context = _Context(index, feature_id, errors)
    target = _validate_reference(raw.get("target"), "target", context) if "target" in raw else None
    radius = _validate_length(raw.get("radius"), "radius", "S16", context) if "radius" in raw else None
    edges = _validate_edge_selector(raw.get("edges"), context) if "edges" in raw else None
    parts = (target, radius, edges)
    if any(part is None for part in parts) or feature_id is None:
        return None
    return Fillet(id=feature_id, target=target, radius=radius, edges=edges)


def _validate_chamfer(
    index: int, raw: Dict[str, Any], feature_id: Optional[str], errors: _Errors
) -> Optional[Chamfer]:
    """Section C.6. Rules S2 (target), S20/S17 (distance), S18 (edges)."""
    context = _Context(index, feature_id, errors)
    target = _validate_reference(raw.get("target"), "target", context) if "target" in raw else None
    distance = (
        _validate_length(raw.get("distance"), "distance", "S17", context) if "distance" in raw else None
    )
    edges = _validate_edge_selector(raw.get("edges"), context) if "edges" in raw else None
    parts = (target, distance, edges)
    if any(part is None for part in parts) or feature_id is None:
        return None
    return Chamfer(id=feature_id, target=target, distance=distance, edges=edges)


@dataclass(frozen=True)
class _Context:
    """Where a parameter lives, so every error can name it identically."""

    index: int
    feature_id: Optional[str]
    errors: _Errors

    def path(self, *parts: str) -> str:
        return "".join([f"features[{self.index}]"] + [f".{part}" for part in parts])

    def add(self, rule: str, message: str, path: str) -> None:
        self.errors.add(
            rule,
            message,
            path=path,
            feature_id=self.feature_id,
            feature_index=self.index,
        )


# --- parameter validators ---------------------------------------------------


def _validate_vector_object(value: Any, field: str, kind: str, context: _Context) -> Optional[Tuple[float, float, float]]:
    """S19: exactly the keys x, y, z, each a finite JSON number."""
    path = context.path(field)
    if not isinstance(value, dict):
        context.add(
            "S19",
            f"a {kind} must be an object with exactly the keys 'x', 'y' and 'z', "
            f"got {_describe(value)}; the array form is not valid",
            path,
        )
        return None

    ok = True
    for key in sorted(set(value) - set(_VECTOR_KEYS)):
        context.add("S19", f"unknown key {key!r} on a {kind}; only 'x', 'y' and 'z' are permitted", f"{path}.{key}")
        ok = False
    for key in _VECTOR_KEYS:
        if key not in value:
            context.add("S19", f"a {kind} requires the component {key!r}", f"{path}.{key}")
            ok = False
        elif not _is_number(value[key]):
            context.add(
                "S19",
                f"{kind} component {key!r} must be a number, got {_describe(value[key])}",
                f"{path}.{key}",
            )
            ok = False
        elif not math.isfinite(float(value[key])):
            context.add(
                "S19",
                f"{kind} component {key!r} must be a finite number, got {value[key]!r}",
                f"{path}.{key}",
            )
            ok = False
    if not ok:
        return None
    return (float(value["x"]), float(value["y"]), float(value["z"]))


def _validate_position(value: Any, field: str, context: _Context) -> Optional[Position]:
    """S19. Position components may be negative or zero (rule S20)."""
    components = _validate_vector_object(value, field, "position", context)
    if components is None:
        return None
    return Position(*components)


def _validate_optional_position(raw: Dict[str, Any], context: _Context) -> Optional[Position]:
    """An omitted optional position takes its documented default, the origin."""
    if "position" not in raw:
        return ORIGIN
    return _validate_position(raw["position"], "position", context)


def _validate_size(value: Any, field: str, context: _Context) -> Optional[Size]:
    """S19 for shape and finiteness, then S10: every component > 0."""
    components = _validate_vector_object(value, field, "size", context)
    if components is None:
        return None
    ok = True
    for key, component in zip(_VECTOR_KEYS, components):
        if not component > 0:
            context.add(
                "S10",
                f"size component {key!r} must be > 0, got {component!r}",
                context.path(field, key),
            )
            ok = False
    if not ok:
        return None
    return Size(*components)


def _validate_length(value: Any, field: str, positive_rule: str, context: _Context) -> Optional[float]:
    """S20 for type and finiteness, then ``positive_rule`` for value > 0."""
    path = context.path(field)
    if not _is_number(value):
        context.add("S20", f"{field!r} must be a number, got {_describe(value)}", path)
        return None
    if not math.isfinite(float(value)):
        context.add("S20", f"{field!r} must be a finite number, got {value!r}", path)
        return None
    number = float(value)
    if not number > 0:
        context.add(positive_rule, f"{field!r} must be > 0, got {number!r}", path)
        return None
    return number


def _validate_optional_axis(raw: Dict[str, Any], context: _Context) -> Optional[str]:
    """S12. An omitted axis takes its documented default, "+Z"."""
    if "axis" not in raw:
        return DEFAULT_AXIS
    value = raw["axis"]
    if not isinstance(value, str) or value not in AXIS_VALUES:
        permitted = ", ".join(repr(axis) for axis in AXIS_VALUES)
        context.add(
            "S12",
            f"'axis' must be one of {permitted}; got {value!r}",
            context.path("axis"),
        )
        return None
    return value


def _validate_reference(value: Any, field: str, context: _Context) -> Optional[str]:
    """S2: a reference field must be a string. Resolution is checked by S6/S7."""
    if not isinstance(value, str):
        context.add(
            "S2",
            f"{field!r} must be a feature id string, got {_describe(value)}",
            context.path(field),
        )
        return None
    return value


def _validate_tools(value: Any, context: _Context) -> Optional[Tuple[str, ...]]:
    """S14: a non-empty array. S2: every entry is a feature id string."""
    path = context.path("tools")
    if not isinstance(value, list):
        context.add("S14", f"'tools' must be a non-empty array, got {_describe(value)}", path)
        return None
    if not value:
        context.add("S14", "'tools' must be a non-empty array", path)
        return None
    ok = True
    for position, entry in enumerate(value):
        if not isinstance(entry, str):
            context.add(
                "S2",
                f"a 'tools' entry must be a feature id string, got {_describe(entry)}",
                f"{path}[{position}]",
            )
            ok = False
    if not ok:
        return None
    return tuple(value)


def _validate_tool_identity(target: Optional[str], tools: Tuple[str, ...], context: _Context) -> None:
    """S15: target absent from tools, and no duplicate ids in tools."""
    path = context.path("tools")
    seen: Dict[str, int] = {}
    for position, tool in enumerate(tools):
        if tool in seen:
            context.add(
                "S15",
                f"duplicate tool {tool!r} in 'tools' (already listed at index {seen[tool]})",
                f"{path}[{position}]",
            )
        else:
            seen[tool] = position
        if target is not None and tool == target:
            context.add(
                "S15",
                f"'target' {target!r} must not appear in 'tools'",
                f"{path}[{position}]",
            )


def _validate_edge_selector(value: Any, context: _Context) -> Optional[EdgeSelector]:
    """S18: the exact shape of an edge selector (Section C.7)."""
    path = context.path("edges")
    if not isinstance(value, dict):
        context.add(
            "S18",
            f"'edges' must be an edge selector object, got {_describe(value)}",
            path,
        )
        return None

    ok = True
    for key in sorted(set(value) - {"select", "axis"}):
        context.add("S18", f"unknown key {key!r} on an edge selector", f"{path}.{key}")
        ok = False

    if "select" not in value:
        context.add("S18", "an edge selector requires 'select'", f"{path}.select")
        return None
    select = value["select"]
    if not isinstance(select, str) or select not in EDGE_SELECT_VALUES:
        permitted = ", ".join(repr(kind) for kind in EDGE_SELECT_VALUES)
        context.add(
            "S18",
            f"'select' must be one of {permitted}; got {select!r}",
            f"{path}.select",
        )
        return None

    if select == "all":
        if "axis" in value:
            context.add(
                "S18",
                "'axis' must be absent when 'select' is 'all'",
                f"{path}.axis",
            )
            ok = False
        return EdgeSelector(select="all") if ok else None

    if "axis" not in value:
        context.add(
            "S18",
            "'axis' is required when 'select' is 'axis_parallel'",
            f"{path}.axis",
        )
        return None
    axis = value["axis"]
    if not isinstance(axis, str) or axis not in SELECTOR_AXIS_VALUES:
        permitted = ", ".join(repr(letter) for letter in SELECTOR_AXIS_VALUES)
        context.add(
            "S18",
            f"an edge selector 'axis' must be one of {permitted} (unsigned: "
            f"parallelism has no direction); got {axis!r}",
            f"{path}.axis",
        )
        return None
    return EdgeSelector(select="axis_parallel", axis=axis) if ok else None


# --- cross-feature rules (Sections B.3, B.4) -------------------------------


def _validate_unique_ids(records: List[_Record], errors: _Errors) -> bool:
    """S8: feature ids are unique within the part. Returns True if any repeat."""
    first_seen: Dict[str, int] = {}
    duplicates = False
    for record in records:
        if record.feature_id is None:
            continue
        if record.feature_id in first_seen:
            duplicates = True
            errors.add(
                "S8",
                f"duplicate feature id {record.feature_id!r} (already used by "
                f"features[{first_seen[record.feature_id]}])",
                path=f"features[{record.index}].id",
                feature_id=record.feature_id,
                feature_index=record.index,
            )
        else:
            first_seen[record.feature_id] = record.index
    return duplicates


def _validate_order_and_references(
    records: List[_Record], errors: _Errors, *, duplicate_ids: bool
) -> None:
    """S9 (feature order and final solid set), S6 and S7 (references).

    Simulates the solid set of Section B.4: a constructive feature adds a solid
    named by its own id, a modifier replaces its target in place and keeps the
    target's id, and a subtract additionally consumes its tool solids.
    """
    if not records:
        return

    first = records[0]
    if first.feature_type is not None and first.feature_type not in CONSTRUCTIVE_TYPES:
        constructive = " or ".join(CONSTRUCTIVE_TYPES)
        errors.add(
            "S9",
            f"the first feature must be constructive ({constructive}); got "
            f"{first.feature_type!r}",
            path="features[0].type",
            feature_id=first.feature_id,
            feature_index=first.index,
        )

    declared: Dict[str, int] = {}
    for record in records:
        if record.feature_id is not None and record.feature_id not in declared:
            declared[record.feature_id] = record.index

    live: Dict[str, int] = {}
    # The final solid-set count is only meaningful if every feature was
    # understood.  Where it is not, the count is suppressed rather than
    # reported as a second, misleading failure.
    reliable = not duplicate_ids
    for record in records:
        if record.feature_type is None or not record.id_is_valid or record.feature_id is None:
            reliable = False
            continue
        if record.feature_type in CONSTRUCTIVE_TYPES:
            live.setdefault(record.feature_id, record.index)
            continue

        raw = record.raw
        target = raw.get("target")
        if isinstance(target, str):
            if not _resolve_reference(target, "target", record, declared, live, errors):
                reliable = False
        else:
            reliable = False

        if record.feature_type == "subtract":
            reliable = _consume_tools(raw.get("tools"), record, declared, live, errors) and reliable

    if reliable and len(live) != 1:
        remaining = ", ".join(repr(solid) for solid in live) or "nothing"
        errors.add(
            "S9",
            f"after the last feature the solid set must contain exactly one "
            f"solid; it contains {len(live)} ({remaining})",
            path="features",
        )


def _consume_tools(
    tools: Any,
    record: _Record,
    declared: Dict[str, int],
    live: Dict[str, int],
    errors: _Errors,
) -> bool:
    """Resolve and consume a subtract's tool solids. Returns True if reliable."""
    if not isinstance(tools, list) or not tools or not all(isinstance(tool, str) for tool in tools):
        return False
    # A tool list that violates S15 has already been reported. Simulating it
    # would consume a solid twice, so the solid set is abandoned here rather
    # than used to derive a second, misleading S6 or S9.
    if len(set(tools)) != len(tools) or record.raw.get("target") in tools:
        return False
    reliable = True
    for position, tool in enumerate(tools):
        if _resolve_reference(tool, f"tools[{position}]", record, declared, live, errors):
            live.pop(tool, None)
        else:
            reliable = False
    return reliable


def _resolve_reference(
    reference: str,
    field: str,
    record: _Record,
    declared: Dict[str, int],
    live: Dict[str, int],
    errors: _Errors,
) -> bool:
    """S6/S7 for one reference. Returns True when it resolves to a live solid."""
    if reference in live:
        return True

    path = f"features[{record.index}].{field}"
    declared_at = declared.get(reference)
    if declared_at is not None and declared_at >= record.index:
        relation = "itself" if declared_at == record.index else f"features[{declared_at}]"
        errors.add(
            "S7",
            f"reference {reference!r} points to {relation}; a reference must "
            f"point to a feature that appears strictly earlier",
            path=path,
            feature_id=record.feature_id,
            feature_index=record.index,
        )
        return False

    if declared_at is None:
        errors.add(
            "S6",
            f"reference {reference!r} does not name any feature in the part",
            path=path,
            feature_id=record.feature_id,
            feature_index=record.index,
        )
        return False

    errors.add(
        "S6",
        f"reference {reference!r} is not a solid in the solid set at this "
        f"point: features[{declared_at}] is either a modifier, whose id never "
        f"names a solid, or a solid already consumed by a subtract",
        path=path,
        feature_id=record.feature_id,
        feature_index=record.index,
    )
    return False


# --- typed result -----------------------------------------------------------


def _build_part(
    schema_version: Optional[str],
    units: Optional[str],
    name: Optional[str],
    description: Optional[str],
    records: List[_Record],
    errors: _Errors,
) -> Optional[Part]:
    """Assemble the typed Part, but only for a document with no violations."""
    if errors.items:
        return None
    if schema_version is None or units is None or name is None:
        return None
    features = [record.feature for record in records]
    if not features or any(feature is None for feature in features):
        return None
    return Part(
        schema_version=schema_version,
        units=units,
        name=name,
        features=tuple(features),
        description=description,
    )
