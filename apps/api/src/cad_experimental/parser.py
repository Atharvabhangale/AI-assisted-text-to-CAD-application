"""Untrusted model text in, typed operations out. The security boundary.

Everything this module reads is attacker-controlled in the threat model: a
model can be steered by the description it is given, so its reply is data of
unknown provenance and is treated as such.

The rules are the whole design:

* the payload is read with :func:`json.loads` -- never ``eval``, never
  ``exec``, never ``pickle``, never an import;
* every key is looked up in an explicit allow-list. Unknown operation types
  and unknown parameter names are **errors**, not fields to ignore;
* every value is converted to the one type it is allowed to be. A string
  where a number belongs is rejected, not coerced -- ``float("nan")``
  succeeds, and silently accepting it would put a NaN in the geometry;
* nothing is looked up by name on an object, so no attribute traversal, no
  ``__class__``/``__globals__`` walk and no dotted path can reach anything;
* nothing here opens a file, spawns a process or makes a request.

The result of parsing is only ever a plain frozen record. There is no code
path from a model's reply to executable code, because there is no code path
from *any* string to executable code in this module.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .plan import (
    AXES,
    BOX,
    CONSUMING_TYPES,
    CYLINDER,
    EXTRUDE,
    ID_PATTERN,
    MAX_TOOLS,
    MODIFIER_TYPES,
    OPERATION_FIELDS,
    OPERATION_TYPES,
    PARAMETERS,
    PROFILE_SOLID_TYPES,
    REVOLVE,
    SELECT_AXIS_PARALLEL,
    SELECT_MODES,
    SELECTOR_AXES,
    SELECTOR_FIELDS,
    SUBTRACT,
    TARGETED_TYPES,
    THROUGH_HOLE,
    CHAMFER,
    FILLET,
    SKETCH,
    BoxOperation,
    ChamferOperation,
    CylinderOperation,
    EdgeSelector,
    ExtrudeOperation,
    FilletOperation,
    Operation,
    OperationPlan,
    PlanStatus,
    Point,
    RevolveOperation,
    SketchOperation,
    SubtractOperation,
    ThroughHoleOperation,
)
from .sketch import (
    CONSTRAINT_FIELDS,
    CONSTRAINT_TYPES,
    DIMENSIONAL_TYPES,
    GEOMETRY_FIELDS,
    GEOMETRY_TYPES,
    MAX_CONSTRAINTS,
    MAX_GEOMETRY,
    PLANES,
    CIRCLE,
    COINCIDENT,
    LINE,
    RECTANGLE,
    Circle,
    Constraint,
    Geometry,
    Line,
    Point2D,
    PointHandle,
    Rectangle,
    SketchDefinition,
)

#: The keys a plan object may carry. Anything else is a rejection.
PLAN_KEYS = frozenset(
    {"status", "operations", "summary", "reason", "questions"}
)

#: The keys any operation may carry. Which of them a *particular* type may
#: carry is narrower -- see :data:`~cad_experimental.plan.OPERATION_FIELDS`.
#: ``target`` on a box is rejected as an unknown field for that type.
OPERATION_KEYS = frozenset({"id", "type", "target", "tools", "parameters"})

#: The keys a position may carry.
POINT_KEYS = frozenset({"x", "y", "z"})

#: An upper bound on how much text is even looked at. A model that returns a
#: megabyte has malfunctioned, and parsing it anyway is free denial of
#: service.
MAX_PAYLOAD_CHARS = 64_000

#: An upper bound on operations in one plan. This stage builds one solid;
#: a plan of ten thousand is a malfunction, not a part.
MAX_OPERATIONS = 32

_ID_RE = re.compile(ID_PATTERN)


class PlanParseError(Exception):
    """The model's reply is not a readable operation plan.

    Carries a caller-safe :attr:`message` and, separately, a :attr:`detail`
    that may quote the offending fragment. The detail is for a log and a
    measurement record; it is not put in an HTTP payload, because it echoes
    model output straight back to a caller.
    """

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


def parse_plan_text(text: str) -> OperationPlan:
    """Read raw model text as an operation plan.

    The one entry point a caller should use for provider output. Accepts the
    JSON object a model was asked for, and nothing more forgiving: no
    markdown fence stripping, no "find the first ``{``", no repair. A model
    that cannot follow the output contract has failed the case, and quietly
    fixing it up would measure the repair rather than the model.
    """
    if not isinstance(text, str):
        raise PlanParseError("the model returned no text")
    stripped = text.strip()
    if not stripped:
        raise PlanParseError("the model returned an empty response")
    if len(stripped) > MAX_PAYLOAD_CHARS:
        raise PlanParseError(
            "the model's response is too long to be a plan",
            detail=f"{len(stripped)} characters, limit {MAX_PAYLOAD_CHARS}",
        )
    try:
        payload = json.loads(stripped)
    except ValueError as exc:
        raise PlanParseError(
            "the model did not return a JSON operation plan",
            detail=str(exc),
        ) from exc
    return parse_plan(payload)


def parse_plan(payload: Any) -> OperationPlan:
    """Read an already-decoded payload as an operation plan."""
    mapping = _object(payload, "the plan")
    _reject_unknown(mapping, PLAN_KEYS, "the plan")

    status = _status(mapping.get("status"))
    summary = _text(mapping.get("summary", ""), "summary")
    reason = (
        _text(mapping["reason"], "reason") if "reason" in mapping else None
    )
    questions = _questions(mapping.get("questions", ()))

    raw_operations = mapping.get("operations", [])
    if not isinstance(raw_operations, list):
        raise PlanParseError("`operations` must be a list")
    if len(raw_operations) > MAX_OPERATIONS:
        raise PlanParseError(
            "the plan has more operations than this stage accepts",
            detail=f"{len(raw_operations)} operations, limit {MAX_OPERATIONS}",
        )

    operations: List[Operation] = [
        _operation(entry, index) for index, entry in enumerate(raw_operations)
    ]

    if status is not PlanStatus.GENERATED and operations:
        # A refusal that also ships geometry is self-contradictory. Keeping
        # the geometry would let a model smuggle a part past its own refusal.
        raise PlanParseError(
            f"a `{status.value}` plan must not carry operations",
            detail=f"{len(operations)} operations present",
        )

    return OperationPlan(
        status=status,
        operations=tuple(operations),
        summary=summary,
        reason=reason,
        questions=questions,
    )


# --- one operation ---------------------------------------------------------


def _operation(entry: Any, index: int) -> Operation:
    where = f"operation {index}"
    mapping = _object(entry, where)
    _reject_unknown(mapping, OPERATION_KEYS, where)

    for required in ("id", "type"):
        if required not in mapping:
            raise PlanParseError(f"{where} is missing `{required}`")

    identifier = _identifier(mapping["id"], where)
    kind = mapping["type"]
    if not isinstance(kind, str) or kind not in OPERATION_TYPES:
        # The single most important rejection in this module: an operation
        # type this stage does not implement never reaches the adapter.
        raise PlanParseError(
            f"{where} has an unknown type",
            detail=(
                f"{kind!r}; this stage implements only "
                f"{', '.join(OPERATION_TYPES)}"
            ),
        )

    # Narrow the operation-level keys to the ones this type may carry, so a
    # `target` on a box is an error rather than a field quietly ignored.
    _reject_unknown(mapping, OPERATION_FIELDS[kind], f"a {kind} operation")

    target: Optional[str] = None
    if kind in TARGETED_TYPES:
        if "target" not in mapping:
            acts_on = (
                "a profile" if kind in PROFILE_SOLID_TYPES
                else "an existing solid"
            )
            raise PlanParseError(
                f"{where} is missing `target`",
                detail=f"a {kind} acts on {acts_on} and must name it",
            )
        target = _identifier(mapping["target"], f"{where} target")

    if kind in CONSUMING_TYPES:
        assert target is not None
        return SubtractOperation(
            id=identifier,
            target=target,
            tools=_tools(mapping.get("tools"), where),
        )

    if "parameters" not in mapping:
        raise PlanParseError(f"{where} is missing `parameters`")
    parameters = _object(mapping["parameters"], f"{where} parameters")
    required_names, optional_names = PARAMETERS[kind]
    allowed = frozenset(required_names) | frozenset(optional_names)
    _reject_unknown(parameters, allowed, f"{where} parameters")
    for name in required_names:
        if name not in parameters:
            raise PlanParseError(f"{where} is missing parameter `{name}`")

    if kind == SKETCH:
        return SketchOperation(
            id=identifier,
            definition=_sketch(parameters, where),
        )

    if kind == FILLET:
        assert target is not None
        return FilletOperation(
            id=identifier,
            target=target,
            radius=_number(parameters["radius"], f"{where}.radius"),
            edges=_selector(parameters["edges"], where),
        )

    if kind == CHAMFER:
        assert target is not None
        return ChamferOperation(
            id=identifier,
            target=target,
            distance=_number(parameters["distance"], f"{where}.distance"),
            edges=_selector(parameters["edges"], where),
        )

    if kind == EXTRUDE:
        assert target is not None
        return ExtrudeOperation(
            id=identifier,
            target=target,
            distance=_number(parameters["distance"], f"{where}.distance"),
            # Optional: absent means the plane's positive normal. Read as
            # `None` rather than filled in here, so the default stays one
            # documented fact in one place instead of a value the parser
            # invents.
            direction=_optional_axis(parameters.get("direction"), where),
        )

    if kind == REVOLVE:
        assert target is not None
        return RevolveOperation(
            id=identifier,
            target=target,
            angle=_number(parameters["angle"], f"{where}.angle"),
            # Required, and signed: a partial revolve about `+Z` and about
            # `-Z` are mirror-image parts, so the sign is geometry.
            axis=_axis(parameters["axis"], where),
        )

    if kind == THROUGH_HOLE:
        assert target is not None
        position = _optional_point(parameters["position"], where)
        if position is None:
            # Unreachable: `position` is required above, and an explicit
            # `null` is rejected by `_optional_point`'s object check.
            raise PlanParseError(f"{where} has no position")
        return ThroughHoleOperation(
            id=identifier,
            target=target,
            diameter=_number(parameters["diameter"], f"{where}.diameter"),
            position=position,
            axis=_optional_axis(parameters.get("axis"), where),
        )

    if kind == BOX:
        return BoxOperation(
            id=identifier,
            x=_number(parameters["x"], f"{where}.x"),
            y=_number(parameters["y"], f"{where}.y"),
            z=_number(parameters["z"], f"{where}.z"),
            position=_optional_point(parameters.get("position"), where),
        )
    if kind == CYLINDER:
        return CylinderOperation(
            id=identifier,
            diameter=_number(parameters["diameter"], f"{where}.diameter"),
            height=_number(parameters["height"], f"{where}.height"),
            position=_optional_point(parameters.get("position"), where),
            axis=_optional_axis(parameters.get("axis"), where),
        )
    raise PlanParseError(f"{where} has an unknown type", detail=repr(kind))


# --- values ----------------------------------------------------------------


# --- the sketch reader -----------------------------------------------------
#
# Deep, but entirely mechanical: every field is looked up in an allow-list
# and converted to the one type it may be. There is no expression language
# and nothing here is ever evaluated.


def _sketch(parameters: Mapping[str, Any], where: str) -> SketchDefinition:
    """Read a sketch's parameters into typed records."""
    plane = parameters.get("plane")
    if not isinstance(plane, str) or plane not in PLANES:
        raise PlanParseError(
            f"{where} has an unknown sketch `plane`",
            detail=f"{plane!r}; expected one of {', '.join(PLANES)}",
        )

    raw_geometry = parameters.get("geometry")
    if not isinstance(raw_geometry, list) or not raw_geometry:
        raise PlanParseError(
            f"{where} `geometry` must be a non-empty list",
            detail="a sketch with no geometry describes nothing",
        )
    if len(raw_geometry) > MAX_GEOMETRY:
        raise PlanParseError(
            f"{where} has more geometry than this stage accepts",
            detail=f"{len(raw_geometry)} items, limit {MAX_GEOMETRY}",
        )
    geometry = tuple(
        _geometry(entry, f"{where} geometry[{index}]")
        for index, entry in enumerate(raw_geometry)
    )

    raw_constraints = parameters.get("constraints", [])
    if raw_constraints is None:
        raw_constraints = []
    if not isinstance(raw_constraints, list):
        raise PlanParseError(f"{where} `constraints` must be a list")
    if len(raw_constraints) > MAX_CONSTRAINTS:
        raise PlanParseError(
            f"{where} has more constraints than this stage accepts",
            detail=f"{len(raw_constraints)} items, limit {MAX_CONSTRAINTS}",
        )
    constraints = tuple(
        _constraint(entry, f"{where} constraints[{index}]")
        for index, entry in enumerate(raw_constraints)
    )
    return SketchDefinition(
        plane=plane, geometry=geometry, constraints=constraints
    )


def _geometry(entry: Any, where: str) -> Geometry:
    mapping = _object(entry, where)
    for required in ("id", "type"):
        if required not in mapping:
            raise PlanParseError(f"{where} is missing `{required}`")
    identifier = _identifier(mapping["id"], f"{where} id")
    kind = mapping["type"]
    if not isinstance(kind, str) or kind not in GEOMETRY_TYPES:
        raise PlanParseError(
            f"{where} has an unknown geometry type",
            detail=f"{kind!r}; this stage implements only "
                   f"{', '.join(GEOMETRY_TYPES)}",
        )
    _reject_unknown(mapping, GEOMETRY_FIELDS[kind], where)
    for field in GEOMETRY_FIELDS[kind]:
        if field not in mapping:
            raise PlanParseError(f"{where} is missing `{field}`")

    if kind == LINE:
        return Line(
            id=identifier,
            start=_point2d(mapping["start"], f"{where}.start"),
            end=_point2d(mapping["end"], f"{where}.end"),
        )
    if kind == CIRCLE:
        return Circle(
            id=identifier,
            centre=_point2d(mapping["centre"], f"{where}.centre"),
            radius=_number(mapping["radius"], f"{where}.radius"),
        )
    return Rectangle(
        id=identifier,
        corner=_point2d(mapping["corner"], f"{where}.corner"),
        width=_number(mapping["width"], f"{where}.width"),
        height=_number(mapping["height"], f"{where}.height"),
    )


def _constraint(entry: Any, where: str) -> Constraint:
    mapping = _object(entry, where)
    for required in ("id", "type"):
        if required not in mapping:
            raise PlanParseError(f"{where} is missing `{required}`")
    identifier = _identifier(mapping["id"], f"{where} id")
    kind = mapping["type"]
    if not isinstance(kind, str) or kind not in CONSTRAINT_TYPES:
        raise PlanParseError(
            f"{where} has an unknown constraint type",
            detail=f"{kind!r}; this stage implements only "
                   f"{', '.join(CONSTRAINT_TYPES)}",
        )
    _reject_unknown(mapping, CONSTRAINT_FIELDS[kind], where)
    for field in CONSTRAINT_FIELDS[kind]:
        if field not in mapping:
            raise PlanParseError(f"{where} is missing `{field}`")

    if kind == COINCIDENT:
        raw = mapping["points"]
        if not isinstance(raw, list) or len(raw) != 2:
            raise PlanParseError(
                f"{where} `points` must be exactly two point handles"
            )
        return Constraint(
            id=identifier,
            type=kind,
            points=tuple(
                _point_handle(handle, f"{where}.points[{index}]")
                for index, handle in enumerate(raw)
            ),
        )

    value = (
        _number(mapping["value"], f"{where}.value")
        if kind in DIMENSIONAL_TYPES
        else None
    )
    return Constraint(
        id=identifier,
        type=kind,
        geometry=_identifier(mapping["geometry"], f"{where} geometry"),
        value=value,
    )


def _point_handle(entry: Any, where: str) -> PointHandle:
    """A geometry id plus one enumerated point name. Never a dotted path."""
    mapping = _object(entry, where)
    _reject_unknown(mapping, ("geometry", "point"), where)
    for required in ("geometry", "point"):
        if required not in mapping:
            raise PlanParseError(f"{where} is missing `{required}`")
    name = mapping["point"]
    if not isinstance(name, str) or not name:
        raise PlanParseError(f"{where} `point` must be a name")
    # Which names are legal depends on the geometry's type, which only the
    # validator knows -- it has the whole sketch. Shape only here.
    return PointHandle(
        geometry=_identifier(mapping["geometry"], f"{where} geometry"),
        point=name,
    )


def _point2d(value: Any, where: str) -> Point2D:
    """A 2D point. Exactly x and y -- a sketch has no z."""
    mapping = _object(value, where)
    _reject_unknown(mapping, ("x", "y"), where)
    for axis in ("x", "y"):
        if axis not in mapping:
            raise PlanParseError(f"{where} is missing `{axis}`")
    return Point2D(
        x=_number(mapping["x"], f"{where}.x"),
        y=_number(mapping["y"], f"{where}.y"),
    )


def _selector(value: Any, where: str) -> EdgeSelector:
    """Read an edge selector. Shape only, and strictly (Section C.7).

    The selector is an **object**, never a bare string: ``"all"`` is not a
    selector and is rejected rather than helpfully interpreted. `axis` is
    required for ``axis_parallel`` and forbidden for ``all`` (rule S18), and
    the axis letters are **unsigned** -- ``"+Z"`` is an error, not a synonym
    for ``"Z"``.
    """
    if isinstance(value, str):
        # The single most likely malformed selector: worth its own message.
        raise PlanParseError(
            f"{where} `edges` must be an object, not a string",
            detail=f'got {value!r}; write {{"select": "all"}}',
        )
    mapping = _object(value, f"{where} edges")
    _reject_unknown(mapping, SELECTOR_FIELDS, f"{where} edges")

    if "select" not in mapping:
        raise PlanParseError(f"{where} edges is missing `select`")
    mode = mapping["select"]
    if not isinstance(mode, str) or mode not in SELECT_MODES:
        raise PlanParseError(
            f"{where} edges has an unknown `select`",
            detail=f"{mode!r}; expected one of {', '.join(SELECT_MODES)}",
        )

    axis = mapping.get("axis")
    if mode == SELECT_AXIS_PARALLEL:
        if axis is None:
            raise PlanParseError(
                f"{where} edges is missing `axis`",
                detail=(
                    f"`{SELECT_AXIS_PARALLEL}` needs an axis (rule S18); one "
                    f"of {', '.join(SELECTOR_AXES)}"
                ),
            )
        if not isinstance(axis, str) or axis not in SELECTOR_AXES:
            raise PlanParseError(
                f"{where} edges has an invalid `axis`",
                detail=(
                    f"{axis!r}; a selector axis is UNSIGNED -- one of "
                    f"{', '.join(SELECTOR_AXES)}, not a signed direction"
                ),
            )
        return EdgeSelector(select=mode, axis=axis)

    if axis is not None:
        raise PlanParseError(
            f"{where} edges must not carry `axis`",
            detail=f"`{mode}` selects every edge, so an axis means nothing",
        )
    return EdgeSelector(select=mode)


def _tools(value: Any, where: str) -> Tuple[str, ...]:
    """Read a subtract's tool list.

    Shape only. Whether each id resolves to an available solid is the
    validator's judgement, because it depends on everything before it in the
    plan; a list of well-formed ids that name nothing is a valid *shape* and
    an invalid *plan*.
    """
    if value is None:
        raise PlanParseError(
            f"{where} is missing `tools`",
            detail="a subtract must remove at least one solid (rule S14)",
        )
    if not isinstance(value, list):
        raise PlanParseError(f"{where} `tools` must be a list of ids")
    if not value:
        # S14. Caught here rather than left to the validator because an
        # empty list is a malformed subtract, not a bad reference.
        raise PlanParseError(
            f"{where} has an empty `tools` list",
            detail="a subtract must remove at least one solid (rule S14)",
        )
    if len(value) > MAX_TOOLS:
        raise PlanParseError(
            f"{where} lists more tools than this stage accepts",
            detail=f"{len(value)} tools, limit {MAX_TOOLS}",
        )
    return tuple(
        _identifier(entry, f"{where} tools[{index}]")
        for index, entry in enumerate(value)
    )


def _object(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PlanParseError(f"{where} must be a JSON object")
    for key in value:
        if not isinstance(key, str):
            raise PlanParseError(f"{where} has a non-string key")
    return value


def _reject_unknown(
    mapping: Mapping[str, Any], allowed: Any, where: str
) -> None:
    unknown = sorted(set(mapping) - set(allowed))
    if unknown:
        raise PlanParseError(
            f"{where} has unknown fields",
            detail=", ".join(unknown),
        )


def _status(value: Any) -> PlanStatus:
    if not isinstance(value, str):
        raise PlanParseError("the plan is missing a string `status`")
    for status in PlanStatus:
        if status.value == value:
            return status
    raise PlanParseError(
        "the plan has an unknown status",
        detail=f"{value!r}",
    )


def _identifier(value: Any, where: str) -> str:
    """An operation id or a reference to one. The same shape, per rule S8.

    Used for `target` as well as `id`: a reference that could not be a legal
    id could not name anything, so the check is the same one.
    """
    if not isinstance(value, str):
        raise PlanParseError(f"{where} is not a string")
    if not _ID_RE.match(value):
        raise PlanParseError(
            f"{where} is not a valid identifier",
            detail=f"{value!r} does not match {ID_PATTERN}",
        )
    return value


def _number(value: Any, where: str) -> float:
    # bool is an int in Python; True would silently become 1.0 here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanParseError(
            f"{where} must be a number",
            detail=f"got {type(value).__name__}",
        )
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise PlanParseError(f"{where} must be a finite number")
    return number


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise PlanParseError(f"`{where}` must be a string")
    return value.strip()


def _questions(value: Any) -> Tuple[str, ...]:
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list):
        raise PlanParseError("`questions` must be a list of strings")
    questions = []
    for index, entry in enumerate(value):
        if not isinstance(entry, str):
            raise PlanParseError(f"question {index} is not a string")
        text = entry.strip()
        if text:
            questions.append(text)
    return tuple(questions)


def _optional_point(value: Any, where: str) -> Optional[Point]:
    if value is None:
        return None
    mapping = _object(value, f"{where} position")
    _reject_unknown(mapping, POINT_KEYS, f"{where} position")
    for axis in ("x", "y", "z"):
        if axis not in mapping:
            raise PlanParseError(f"{where} position is missing `{axis}`")
    return Point(
        x=_number(mapping["x"], f"{where}.position.x"),
        y=_number(mapping["y"], f"{where}.position.y"),
        z=_number(mapping["z"], f"{where}.position.z"),
    )


def _optional_axis(value: Any, where: str) -> Optional[str]:
    if value is None:
        return None
    return _axis(value, where)


def _axis(value: Any, where: str) -> str:
    """A required signed principal axis. ``None`` is not one of them."""
    if not isinstance(value, str) or value not in AXES:
        raise PlanParseError(
            f"{where} has an invalid axis",
            detail=f"{value!r}; expected one of {', '.join(AXES)}",
        )
    return value


__all__ = [
    "MAX_OPERATIONS",
    "OPERATION_FIELDS",
    "MAX_PAYLOAD_CHARS",
    "OPERATION_KEYS",
    "PLAN_KEYS",
    "PlanParseError",
    "parse_plan",
    "parse_plan_text",
]
