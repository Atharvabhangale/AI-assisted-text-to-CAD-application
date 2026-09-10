"""The operation plan: the whole experimental representation, in one file.

Deliberately tiny. Two operations, a handful of parameters each, and a plan
that is a status plus an ordered list. It is **not** the V1 document with
different field names: a box carries flat ``x``/``y``/``z`` rather than a
nested ``size`` object, precisely so the experiment measures a genuinely
different shape rather than a rename.

These are typed, frozen records. Untrusted JSON never becomes one of these
without going through :mod:`cad_experimental.parser`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

#: The only operation types this stage implements. Anything else is rejected
#: by the parser rather than passed along to be someone else's problem.
BOX = "box"
CYLINDER = "cylinder"
THROUGH_HOLE = "through_hole"
OPERATION_TYPES: Tuple[str, ...] = (BOX, CYLINDER, THROUGH_HOLE)

#: Operations that add a solid to the solid set, named by their own id
#: (specification Section B.4).
CONSTRUCTIVE_TYPES: Tuple[str, ...] = (BOX, CYLINDER)

#: Operations that act on an existing solid named by their ``target``. A
#: modifier replaces its target **in place** and the result keeps the
#: **target's** id -- the modifier's own id never names a solid. So four
#: holes in a plate all target the plate, and never each other.
MODIFIER_TYPES: Tuple[str, ...] = (THROUGH_HOLE,)

#: The six signed principal directions, exactly as the V1 contract spells
#: them (Section A.4). No arbitrary vectors.
AXES: Tuple[str, ...] = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")

#: The contract's default axis for a cylinder (Section C.2).
DEFAULT_AXIS = "+Z"

#: V1 is millimetres only (rule S5). The plan carries no unit field at all:
#: a unit the model could get wrong is a unit the model can get wrong.
UNITS = "mm"

#: Parameter names, per operation type. The parser accepts these and nothing
#: else -- an unknown key is an error, never ignored.
BOX_REQUIRED: Tuple[str, ...] = ("x", "y", "z")
BOX_OPTIONAL: Tuple[str, ...] = ("position",)
CYLINDER_REQUIRED: Tuple[str, ...] = ("diameter", "height")
CYLINDER_OPTIONAL: Tuple[str, ...] = ("position", "axis")

#: ``position`` is REQUIRED for a through_hole, unlike box and cylinder: a
#: hole has no defaulted location, so omitting it would be inventing one
#: (specification Section C.3).
THROUGH_HOLE_REQUIRED: Tuple[str, ...] = ("diameter", "position")
THROUGH_HOLE_OPTIONAL: Tuple[str, ...] = ("axis",)

PARAMETERS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    BOX: (BOX_REQUIRED, BOX_OPTIONAL),
    CYLINDER: (CYLINDER_REQUIRED, CYLINDER_OPTIONAL),
    THROUGH_HOLE: (THROUGH_HOLE_REQUIRED, THROUGH_HOLE_OPTIONAL),
}

#: Which operation-level keys each type may carry, beside ``parameters``.
#: ``target`` belongs at the operation level, as it does in the V1 document,
#: rather than buried among the parameters: it is a reference, not a
#: dimension. A ``target`` on a box is an unknown field and is rejected.
OPERATION_FIELDS: Dict[str, Tuple[str, ...]] = {
    BOX: ("id", "type", "parameters"),
    CYLINDER: ("id", "type", "parameters"),
    THROUGH_HOLE: ("id", "type", "target", "parameters"),
}

#: Operation ids: the same shape the V1 contract requires of feature ids
#: (rule S8), so an id that survives here survives there too.
ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]*$"


class PlanStatus(Enum):
    """What the model says it did. Three answers, and no fourth.

    Note what is absent: there is no ``error`` status. A model cannot report
    its own transport failure, and a model that says "ok" has not thereby
    made its output valid -- both of those are the caller's judgement, made
    in :mod:`cad_experimental.generation`, never the model's.
    """

    #: A plan is offered. The only status that carries operations.
    GENERATED = "generated"

    #: The request needs something this stage does not implement.
    UNSUPPORTED = "unsupported"

    #: Required information is missing and guessing it would be inventing
    #: geometry. Carries a question instead of operations.
    NEEDS_CLARIFICATION = "needs_clarification"


@dataclass(frozen=True)
class Point:
    """A point in the part coordinate system, in millimetres."""

    x: float
    y: float
    z: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True)
class BoxOperation:
    """An axis-aligned box. ``position`` is the minimum corner, as in V1.

    The extents are flat parameters rather than a nested ``size``: that is
    the one deliberate shape difference from the V1 document, and the thing
    the experiment is measuring.
    """

    TYPE = BOX

    id: str
    x: float
    y: float
    z: float
    position: Optional[Point] = None

    def parameters(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {"x": self.x, "y": self.y, "z": self.z}
        if self.position is not None:
            values["position"] = self.position.to_dict()
        return values


@dataclass(frozen=True)
class CylinderOperation:
    """A right circular cylinder. ``position`` is the centre of the base."""

    TYPE = CYLINDER

    id: str
    diameter: float
    height: float
    position: Optional[Point] = None
    axis: Optional[str] = None

    def parameters(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {
            "diameter": self.diameter,
            "height": self.height,
        }
        if self.position is not None:
            values["position"] = self.position.to_dict()
        if self.axis is not None:
            values["axis"] = self.axis
        return values


@dataclass(frozen=True)
class ThroughHoleOperation:
    """A cylindrical cut passing completely through ``target``.

    Follows Section C.3 exactly, and invents nothing:

    * the centreline is the infinite line through ``position`` along
      ``axis``, so the cut always emerges on both sides and there is no
      depth parameter;
    * because the cut is unbounded along the axis, the component of
      ``position`` **along** ``axis`` has no effect -- only the two
      perpendicular components locate the hole. ``z: 0`` for a ``+Z`` hole
      is conventional, not meaningful;
    * ``position`` is required. A hole has no default location.

    ``target`` names the solid to cut. Per Section B.4 the result keeps the
    target's id, so this operation's own ``id`` is a label for traceability
    and never names a solid.
    """

    TYPE = THROUGH_HOLE

    id: str
    target: str
    diameter: float
    position: Point
    axis: Optional[str] = None

    def parameters(self) -> Dict[str, Any]:
        values: Dict[str, Any] = {
            "diameter": self.diameter,
            "position": self.position.to_dict(),
        }
        if self.axis is not None:
            values["axis"] = self.axis
        return values


#: A parsed operation. A union of exactly the implemented types.
Operation = Any  # BoxOperation | CylinderOperation (3.9-compatible)


def operation_type(operation: Operation) -> str:
    """The plan ``type`` string for a typed operation."""
    return operation.TYPE


def is_constructive(operation: Operation) -> bool:
    """True if the operation adds a solid named by its own id."""
    return operation_type(operation) in CONSTRUCTIVE_TYPES


def is_modifier(operation: Operation) -> bool:
    """True if the operation acts on a solid named by its ``target``."""
    return operation_type(operation) in MODIFIER_TYPES


def operation_to_dict(operation: Operation) -> Dict[str, Any]:
    """Round-trip an operation back to its plan shape."""
    payload: Dict[str, Any] = {
        "id": operation.id,
        "type": operation_type(operation),
    }
    target = getattr(operation, "target", None)
    if target is not None:
        payload["target"] = target
    payload["parameters"] = operation.parameters()
    return payload


@dataclass(frozen=True)
class OperationPlan:
    """A status, and the operations that go with it.

    ``operations`` is empty for every status but
    :attr:`PlanStatus.GENERATED` -- an unsupported request that also carried
    geometry would be a contradiction, and the parser refuses it rather than
    quietly keeping the geometry.
    """

    status: PlanStatus
    operations: Tuple[Operation, ...] = ()
    summary: str = ""
    reason: Optional[str] = None
    questions: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": self.status.value,
            "operations": [operation_to_dict(op) for op in self.operations],
            "summary": self.summary,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.questions:
            payload["questions"] = list(self.questions)
        return payload


def plan_schema() -> Dict[str, Any]:
    """A JSON Schema for the plan, for providers that can constrain output.

    Advisory only: :mod:`cad_experimental.parser` re-checks everything. A
    schema the provider honours simply means fewer wasted calls, never a
    reason to trust the payload.
    """
    point = {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": [s.value for s in PlanStatus]},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "pattern": ID_PATTERN},
                        "type": {"type": "string", "enum": list(OPERATION_TYPES)},
                        # A reference to an earlier constructive operation.
                        # Required for through_hole, forbidden for the two
                        # constructive types -- a constraint the parser
                        # enforces per type, which JSON Schema cannot express
                        # here without a conditional the model would have to
                        # reason about.
                        "target": {"type": "string", "pattern": ID_PATTERN},
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "z": {"type": "number"},
                                "diameter": {"type": "number"},
                                "height": {"type": "number"},
                                "axis": {"type": "string", "enum": list(AXES)},
                                "position": point,
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["id", "type", "parameters"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["status", "operations", "summary"],
        "additionalProperties": False,
    }


__all__ = [
    "AXES",
    "BOX",
    "CONSTRUCTIVE_TYPES",
    "CYLINDER",
    "MODIFIER_TYPES",
    "OPERATION_FIELDS",
    "THROUGH_HOLE",
    "ThroughHoleOperation",
    "is_constructive",
    "is_modifier",
    "DEFAULT_AXIS",
    "ID_PATTERN",
    "OPERATION_TYPES",
    "PARAMETERS",
    "UNITS",
    "BoxOperation",
    "CylinderOperation",
    "Operation",
    "OperationPlan",
    "PlanStatus",
    "Point",
    "operation_to_dict",
    "operation_type",
    "plan_schema",
]
