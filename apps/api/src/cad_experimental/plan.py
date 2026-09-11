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
SUBTRACT = "subtract"
FILLET = "fillet"
CHAMFER = "chamfer"
SKETCH = "sketch"
EXTRUDE = "extrude"
REVOLVE = "revolve"
OPERATION_TYPES: Tuple[str, ...] = (
    BOX, CYLINDER, THROUGH_HOLE, SUBTRACT, FILLET, CHAMFER, SKETCH,
    EXTRUDE, REVOLVE,
)

#: Operations that declare a **profile** rather than a solid. A profile is
#: not a solid: a fillet cannot target one, a subtract cannot consume one,
#: and it does not count toward the single-solid rule.
PROFILE_TYPES: Tuple[str, ...] = (SKETCH,)

#: Operations that turn a **profile into a solid**. Their ``target`` names a
#: sketch rather than a solid -- the only place in the language where that is
#: true, and the reason they need their own reference rule (P23) instead of
#: the modifier rule (P11).
#:
#: They do **not** consume the profile. Extruding a sketch does not destroy
#: it in any CAD system, and two extrusions of one profile are legitimate,
#: so a profile stays referenceable after use. That is deliberately unlike
#: ``subtract``, whose tools are consumed.
PROFILE_SOLID_TYPES: Tuple[str, ...] = (EXTRUDE, REVOLVE)

#: Operations V1 can execute. The rest are represented and validated here
#: and then refused by the adapter with an explicit unsupported result --
#: never approximated. See `cad_experimental.sketch` for why.
EXECUTABLE_TYPES: Tuple[str, ...] = (
    BOX, CYLINDER, THROUGH_HOLE, SUBTRACT, FILLET, CHAMFER,
)

#: The two edge-selecting modifiers. They differ only in the name and
#: meaning of their one length: a fillet's ``radius`` rounds, a chamfer's
#: ``distance`` sets back on both adjoining faces.
EDGE_MODIFIER_TYPES: Tuple[str, ...] = (FILLET, CHAMFER)

#: Operations that add a solid to the solid set, named by their own id
#: (specification Section B.4).
CONSTRUCTIVE_TYPES: Tuple[str, ...] = (BOX, CYLINDER)

#: Every operation whose own id names a solid afterwards: the constructive
#: primitives, plus the two that build a solid from a profile. This, not
#: :data:`CONSTRUCTIVE_TYPES`, is what the simulated solid set grows by --
#: an extruded profile is a solid, and a later fillet can target it.
SOLID_DECLARING_TYPES: Tuple[str, ...] = (
    CONSTRUCTIVE_TYPES + PROFILE_SOLID_TYPES
)

#: Operations that act on an existing solid named by their ``target``. A
#: modifier replaces its target **in place** and the result keeps the
#: **target's** id -- the modifier's own id never names a solid. So four
#: holes in a plate all target the plate, and never each other.
MODIFIER_TYPES: Tuple[str, ...] = (
    THROUGH_HOLE, SUBTRACT, FILLET, CHAMFER,
)

#: Modifiers that additionally **consume** solids: each id in ``tools`` is
#: removed from the solid set and can never be referenced again (Section
#: C.4). Only ``subtract`` does this, and it is the whole reason this stage
#: exists -- it is the first operation with history.
CONSUMING_TYPES: Tuple[str, ...] = (SUBTRACT,)

#: Every operation that carries a ``target``. The two groups differ in what
#: the target must BE -- a modifier's is a solid (P11), a profile-solid
#: operation's is a sketch (P23) -- but both must have one, so the parser
#: requires it from one place.
TARGETED_TYPES: Tuple[str, ...] = MODIFIER_TYPES + PROFILE_SOLID_TYPES

#: The six signed principal directions, exactly as the V1 contract spells
#: them (Section A.4). No arbitrary vectors.
AXES: Tuple[str, ...] = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")

#: The contract's default axis for a cylinder (Section C.2).
DEFAULT_AXIS = "+Z"

#: Edge-selector axes are **unsigned**: parallelism has no direction
#: (Section C.7). Deliberately different from :data:`AXES` above, and the
#: contract says so in as many words -- a selector written ``"+Z"`` is an
#: error, not a synonym for ``"Z"``.
SELECTOR_AXES: Tuple[str, ...] = ("X", "Y", "Z")

#: The two deterministic selectors V1 provides, and no others. Persistent
#: named-topology selection is deferred to a later schema version.
SELECT_ALL = "all"
SELECT_AXIS_PARALLEL = "axis_parallel"
SELECT_MODES: Tuple[str, ...] = (SELECT_ALL, SELECT_AXIS_PARALLEL)

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

#: A fillet needs a radius and a selector, and has no defaults.
FILLET_REQUIRED: Tuple[str, ...] = ("radius", "edges")
FILLET_OPTIONAL: Tuple[str, ...] = ()

#: A chamfer is the same shape with a `distance` instead of a `radius`. V1
#: has only the symmetric, equal-distance chamfer: no angle, no asymmetry.
CHAMFER_REQUIRED: Tuple[str, ...] = ("distance", "edges")
CHAMFER_OPTIONAL: Tuple[str, ...] = ()

#: A sketch needs a plane and its geometry; constraints are optional,
#: because a sketch of fully-dimensioned geometry needs none.
SKETCH_REQUIRED: Tuple[str, ...] = ("plane", "geometry")
SKETCH_OPTIONAL: Tuple[str, ...] = ("constraints",)

#: An extrude needs a distance. ``direction`` is optional because there is a
#: defensible default -- the plane's POSITIVE normal -- and only the sign is
#: ever in question (rule P24 requires the axis to be that normal).
EXTRUDE_REQUIRED: Tuple[str, ...] = ("distance",)
EXTRUDE_OPTIONAL: Tuple[str, ...] = ("direction",)

#: A revolve needs both an angle and an axis, and ``axis`` is REQUIRED: a
#: profile on XY can be revolved about X or about Y, and those are different
#: parts. There is no defensible default, so defaulting one would be
#: inventing geometry -- the same reason a through_hole's position is
#: required.
REVOLVE_REQUIRED: Tuple[str, ...] = ("angle", "axis")
REVOLVE_OPTIONAL: Tuple[str, ...] = ()

#: A full revolution, in degrees. The angle is in (0, FULL_TURN]: zero
#: sweeps nothing and more than a full turn would overlap the material it
#: already made.
FULL_TURN = 360.0

#: Which length each edge modifier carries. One place, so the parser, the
#: validator and the adapter cannot disagree about it.
EDGE_MODIFIER_LENGTH: Dict[str, str] = {FILLET: "radius", CHAMFER: "distance"}

PARAMETERS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    BOX: (BOX_REQUIRED, BOX_OPTIONAL),
    CYLINDER: (CYLINDER_REQUIRED, CYLINDER_OPTIONAL),
    THROUGH_HOLE: (THROUGH_HOLE_REQUIRED, THROUGH_HOLE_OPTIONAL),
    # `subtract` has no parameters at all: its whole input is two
    # references. It therefore carries no `parameters` key, exactly as the
    # V1 feature carries no parameter fields -- see OPERATION_FIELDS.
    SUBTRACT: ((), ()),
    FILLET: (FILLET_REQUIRED, FILLET_OPTIONAL),
    CHAMFER: (CHAMFER_REQUIRED, CHAMFER_OPTIONAL),
    SKETCH: (SKETCH_REQUIRED, SKETCH_OPTIONAL),
    EXTRUDE: (EXTRUDE_REQUIRED, EXTRUDE_OPTIONAL),
    REVOLVE: (REVOLVE_REQUIRED, REVOLVE_OPTIONAL),
}

#: The keys an edge selector may carry. ``axis`` is present exactly when
#: ``select`` is ``axis_parallel`` (rule S18) -- not optional, and not
#: allowed otherwise.
SELECTOR_FIELDS: Tuple[str, ...] = ("select", "axis")

#: Which operation-level keys each type may carry, beside ``parameters``.
#: ``target`` belongs at the operation level, as it does in the V1 document,
#: rather than buried among the parameters: it is a reference, not a
#: dimension. A ``target`` on a box is an unknown field and is rejected.
OPERATION_FIELDS: Dict[str, Tuple[str, ...]] = {
    BOX: ("id", "type", "parameters"),
    CYLINDER: ("id", "type", "parameters"),
    THROUGH_HOLE: ("id", "type", "target", "parameters"),
    # No `parameters`: a subtract is entirely references. Supplying one --
    # even an empty one -- is an unknown field, so there is exactly one
    # shape for a subtract rather than two.
    SUBTRACT: ("id", "type", "target", "tools"),
    FILLET: ("id", "type", "target", "parameters"),
    CHAMFER: ("id", "type", "target", "parameters"),
    SKETCH: ("id", "type", "parameters"),
    # `target` names a SKETCH here, not a solid. The key is the same because
    # the question is the same -- "which earlier operation does this act on"
    # -- and rule P23 decides which category the answer must be in.
    EXTRUDE: ("id", "type", "target", "parameters"),
    REVOLVE: ("id", "type", "target", "parameters"),
}

#: The most tools one subtract may list. A part is not built from hundreds of
#: cutters, and an unbounded list is free denial of service.
MAX_TOOLS = 16

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


@dataclass(frozen=True)
class EdgeSelector:
    """Which edges of the target a fillet acts on (Section C.7).

    Two selectors exist and no others:

    * ``{"select": "all"}`` -- every edge of the target solid;
    * ``{"select": "axis_parallel", "axis": "Z"}`` -- every **straight** edge
      parallel to that axis. Circular edges never match, so filleting a
      drilled plate's vertical corners does not touch the hole rims.

    :attr:`axis` is **unsigned** and present exactly when :attr:`select` is
    ``axis_parallel``. It is a different vocabulary from a cylinder's signed
    axis on purpose, and the contract is explicit about that.
    """

    select: str
    axis: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"select": self.select}
        if self.axis is not None:
            payload["axis"] = self.axis
        return payload


@dataclass(frozen=True)
class FilletOperation:
    """Rounds selected edges of ``target`` with one constant radius.

    Section C.5 exactly:

    * every edge the selector matches is replaced by a constant-radius
      circular blend. Only constant radius exists in V1 -- no variable
      radius, no per-edge radius;
    * it is a modifier: the result replaces the target in place and keeps the
      **target's** id, so this operation's own id never names a solid;
    * ``radius > 0`` (rule S16) is decidable here. Whether the selector
      matches anything (E4) and whether the radius is admissible for every
      matched edge (E5) are **geometric**, and belong to the engine. This
      layer does not guess at either.
    """

    TYPE = FILLET

    id: str
    target: str
    radius: float
    edges: EdgeSelector

    def parameters(self) -> Dict[str, Any]:
        return {"radius": self.radius, "edges": self.edges.to_dict()}


@dataclass(frozen=True)
class SketchOperation:
    """A named 2D profile on a principal plane.

    Declares a **profile**, not a solid: nothing can fillet it, subtract it
    or count it toward the single-solid rule. Only a later operation that
    consumes a profile can turn it into geometry.

    It cannot be executed. See :mod:`cad_experimental.sketch` for why, and
    :class:`~cad_experimental.adapter.ExecutionUnsupported` for what the
    adapter says instead.
    """

    TYPE = SKETCH

    id: str
    definition: Any  # sketch.SketchDefinition

    def parameters(self) -> Dict[str, Any]:
        return self.definition.to_dict()


@dataclass(frozen=True)
class ExtrudeOperation:
    """Sweeps a profile along its plane's normal, making a solid.

    ``target`` names a **sketch**, not a solid -- the only reference in this
    language that points at a profile. The profile is not consumed: a second
    extrusion of the same sketch is legitimate.

    ``direction`` is a signed principal axis and must be the plane's normal
    (rule P24): ``"+Z"`` or ``"-Z"`` for a sketch on XY, and nothing else. It
    defaults to the plane's positive normal, which is the only defensible
    default -- the axis is fixed by the plane, so only the sign is a choice.

    It cannot be executed. See :mod:`cad_experimental.sketch` and
    :class:`~cad_experimental.adapter.ExecutionUnsupported`.
    """

    TYPE = EXTRUDE

    id: str
    target: str
    distance: float
    direction: Optional[str] = None

    def parameters(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"distance": self.distance}
        if self.direction is not None:
            payload["direction"] = self.direction
        return payload


@dataclass(frozen=True)
class RevolveOperation:
    """Sweeps a profile about an in-plane axis, making a solid.

    ``target`` names a sketch, as an extrude's does, and is likewise not
    consumed.

    ``axis`` is required and must be one of the two axes the sketch's plane
    spans (rule P25): revolving a profile about its own normal sweeps
    nothing. ``angle`` is in degrees, in (0, 360].

    **Not checked here:** whether the profile crosses the axis. A profile
    that straddles its axis of revolution produces self-intersecting
    material, and deciding that needs the 2D geometry resolved against the
    axis line -- which is the kernel's judgement, of the same kind as E1-E5.
    This layer does not guess at it, and there is no engine to ask, so it is
    recorded as a known gap rather than as a rule that exists.
    """

    TYPE = REVOLVE

    id: str
    target: str
    angle: float
    axis: str

    def parameters(self) -> Dict[str, Any]:
        return {"angle": self.angle, "axis": self.axis}


@dataclass(frozen=True)
class ChamferOperation:
    """Bevels selected edges of ``target`` by an equal setback.

    Section C.6 exactly: the setback is the same on **both** adjoining faces,
    so there is no angle parameter and no asymmetric form in V1.

    Structurally identical to :class:`FilletOperation` apart from the name of
    its length, and it shares the same selector, the same reference rules and
    the same division of labour: ``distance > 0`` (rule S17) is decidable
    here, while E4 and E5 belong to the engine.
    """

    TYPE = CHAMFER

    id: str
    target: str
    distance: float
    edges: EdgeSelector

    def parameters(self) -> Dict[str, Any]:
        return {"distance": self.distance, "edges": self.edges.to_dict()}


@dataclass(frozen=True)
class SubtractOperation:
    """Boolean subtraction: each solid in ``tools`` is removed from ``target``.

    Section C.4 exactly, and nothing more:

    * the tools are removed **in list order**;
    * the result replaces the target in place and keeps the **target's** id,
      as every modifier does (Section B.4);
    * every tool solid is **consumed** -- deleted from the solid set and
      unavailable to any later operation. This is the only operation in the
      language with that effect, and it is what makes a plan have history;
    * subtraction only. There is no union and no intersection in V1, so this
      never joins two solids and never produces a new independent body.

    ``tools`` is non-empty (rule S14), does not contain ``target``, and holds
    no duplicates (rule S15) -- a tool is consumed by its first use, so
    listing it twice could not mean anything.
    """

    TYPE = SUBTRACT

    id: str
    target: str
    tools: Tuple[str, ...]

    def parameters(self) -> Dict[str, Any]:
        """No parameters. A subtract is entirely references."""
        return {}


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


def is_profile(operation: Operation) -> bool:
    """True if the operation declares a profile rather than a solid."""
    return operation_type(operation) in PROFILE_TYPES


def is_profile_solid(operation: Operation) -> bool:
    """True if the operation turns a profile into a solid."""
    return operation_type(operation) in PROFILE_SOLID_TYPES


def declares_solid(operation: Operation) -> bool:
    """True if the operation's own id names a solid afterwards."""
    return operation_type(operation) in SOLID_DECLARING_TYPES


def is_executable(operation: Operation) -> bool:
    """True if V1 and the existing engine can actually build this."""
    return operation_type(operation) in EXECUTABLE_TYPES


def is_consuming(operation: Operation) -> bool:
    """True if the operation consumes the solids it references as tools."""
    return operation_type(operation) in CONSUMING_TYPES


def tools_of(operation: Operation) -> Tuple[str, ...]:
    """The tool references of a consuming operation; empty for the others."""
    return tuple(getattr(operation, "tools", ()) or ())


def operation_to_dict(operation: Operation) -> Dict[str, Any]:
    """Round-trip an operation back to its plan shape."""
    payload: Dict[str, Any] = {
        "id": operation.id,
        "type": operation_type(operation),
    }
    target = getattr(operation, "target", None)
    if target is not None:
        payload["target"] = target
    tools = getattr(operation, "tools", None)
    if tools is not None:
        payload["tools"] = list(tools)
    parameters = operation.parameters()
    # A subtract has none, and writing `"parameters": {}` would invent a
    # second valid shape for it.
    if parameters or not tools:
        payload["parameters"] = parameters
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


def _sketch_schema() -> Dict[str, Any]:
    """The sketch parameters, imported late to keep the module order simple."""
    from .sketch import sketch_schema

    return sketch_schema()


#: Names of the shared ``$defs`` the plan schema emits. Each appears many
#: times across the nine operation branches; emitting one copy and
#: referencing it is what keeps the compiled grammar inside the provider's
#: size limit.
ID_DEF = "identifier"
AXIS_DEF = "signed_axis"
POINT3D_DEF = "point"
SELECTOR_DEF = "edge_selector"


def _ref(name: str) -> Dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _plan_defs() -> Dict[str, Any]:
    """The shared definitions the operation branches reference."""
    from .sketch import sketch_defs

    return {
        ID_DEF: {"type": "string", "pattern": ID_PATTERN},
        AXIS_DEF: {"type": "string", "enum": list(AXES)},
        POINT3D_DEF: {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["x", "y", "z"],
            "additionalProperties": False,
        },
        SELECTOR_DEF: {
            "type": "object",
            "properties": {
                "select": {"type": "string", "enum": list(SELECT_MODES)},
                # Unsigned. Not the signed axis above -- Section C.7 is
                # explicit that parallelism has no direction.
                "axis": {"type": "string", "enum": list(SELECTOR_AXES)},
            },
            "required": ["select"],
            "additionalProperties": False,
        },
        **sketch_defs(),
    }


def _parameter_schemas() -> Dict[str, Any]:
    """The schema fragment for each parameter name, in one place.

    Written once so the nine operation branches cannot come to describe the
    same parameter differently.
    """
    return {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "z": {"type": "number"},
        "diameter": {"type": "number"},
        "height": {"type": "number"},
        "radius": {"type": "number"},
        # Shared by chamfer and extrude: both are one positive length.
        "distance": {"type": "number"},
        "angle": {"type": "number"},
        "axis": _ref(AXIS_DEF),
        "direction": _ref(AXIS_DEF),
        "position": _ref(POINT3D_DEF),
        "edges": _ref(SELECTOR_DEF),
        **_sketch_schema(),
    }


def _operation_branches(
    kinds: Tuple[str, ...] = OPERATION_TYPES,
) -> List[Dict[str, Any]]:
    """One schema branch per operation type, discriminated by ``type``.

    **Derived from** :data:`OPERATION_FIELDS` and :data:`PARAMETERS`, not
    retyped: the same two tables the parser reads. A branch therefore cannot
    describe an operation the parser would refuse, or omit one it requires,
    and a new operation type updates the schema automatically.

    Why a union rather than one object
    ----------------------------------
    Stage 40 measured the old shape against the live API: a single
    ``parameters`` object had to carry all fifteen parameter names of all
    nine operation types, every one optional, because which are required
    depends on the sibling ``type``. That came to **31 optional properties
    against a limit of 24**, and stripping bounds did not reduce it.

    Here each branch declares only its own parameters, and nearly all are
    required, because within one operation type they genuinely are.

    **The wire format is unchanged.** This is the same
    ``{id, type, target?, tools?, parameters?}`` object the parser has always
    accepted, described precisely instead of loosely -- so the parser, the
    validator and the V1 adapter needed no change at all.
    """
    parameters = _parameter_schemas()
    branches: List[Dict[str, Any]] = []
    for kind in kinds:
        properties: Dict[str, Any] = {
            "id": _ref(ID_DEF),
            "type": {"const": kind},
        }
        # Operation-level keys, from the parser's own table. Each is present
        # exactly for the types that may carry it, and each is required: a
        # `target` is not optional on an operation that has one.
        allowed = OPERATION_FIELDS[kind]
        required = ["id", "type"]
        if "target" in allowed:
            properties["target"] = _ref(ID_DEF)
            required.append("target")
        if "tools" in allowed:
            properties["tools"] = {
                "type": "array", "minItems": 1, "items": _ref(ID_DEF),
            }
            required.append("tools")

        required_names, optional_names = PARAMETERS[kind]
        if "parameters" in allowed:
            properties["parameters"] = {
                "type": "object",
                "properties": {
                    name: parameters[name]
                    for name in (*required_names, *optional_names)
                },
                "required": list(required_names),
                "additionalProperties": False,
            }
            required.append("parameters")

        branches.append({
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        })
    return branches


def _prune_defs(document: Dict[str, Any]) -> Dict[str, Any]:
    """Drop ``$defs`` entries nothing references.

    Measured against the live API and not obvious: an **unused** definition
    still costs compiled-grammar budget. A schema that dropped the sketch
    branch but kept the sketch definitions was refused as too large, and the
    same schema with the definitions pruned was accepted.
    """
    defs = document.get("$defs") or {}

    def referenced(node: Any, found: set) -> set:
        if isinstance(node, dict):
            target = node.get("$ref")
            if isinstance(target, str) and target.startswith("#/$defs/"):
                found.add(target.rsplit("/", 1)[-1])
            for value in node.values():
                referenced(value, found)
        elif isinstance(node, list):
            for value in node:
                referenced(value, found)
        return found

    keep: set = set()
    frontier = referenced(
        {k: v for k, v in document.items() if k != "$defs"}, set()
    )
    while frontier:
        name = frontier.pop()
        if name in keep or name not in defs:
            continue
        keep.add(name)
        frontier |= referenced(defs[name], set()) - keep

    pruned = dict(document)
    if keep:
        pruned["$defs"] = {k: v for k, v in defs.items() if k in keep}
    else:
        pruned.pop("$defs", None)
    return pruned


def _plan_document(kinds: Tuple[str, ...]) -> Dict[str, Any]:
    """The plan schema over exactly ``kinds``, with unused defs pruned."""
    return _prune_defs({
        "type": "object",
        "$defs": _plan_defs(),
        "properties": {
            "status": {"type": "string", "enum": [s.value for s in PlanStatus]},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
            "operations": {
                "type": "array",
                "items": {"anyOf": _operation_branches(kinds)},
            },
        },
        "required": ["status", "operations", "summary"],
        "additionalProperties": False,
    })


def provider_schema() -> Dict[str, Any]:
    """The plan schema a structured-output provider will actually compile.

    Identical in shape to :func:`plan_schema`, over the **executable** subset
    of the vocabulary -- the six V1 features -- and nothing else.

    Why a subset exists at all
    --------------------------
    Stage 41 measured Anthropic's structured-output limits directly. Three
    bind at once: at most 24 optional properties across the document and
    about 14 within any single object; a compiled-grammar size ceiling; and a
    separate complexity ceiling. Against those, the nine-operation schema is
    refused, and the binding constraint is ``sketch`` -- its ``geometry`` and
    ``constraints`` lists are arrays of typed sub-objects, and the eight
    other operations plus a sketch branch exceed the grammar budget even when
    the sketch branch is stripped to its plane. Eight branches were accepted;
    adding any ninth was not.

    The subset is therefore **exactly** :data:`EXECUTABLE_TYPES`, which is a
    line the project already draws: these are the operations the CAD engine
    can build. A plan the provider is constrained to produce is a plan that
    can become geometry.

    This removes nothing from the language. :func:`plan_schema` still
    describes all nine, the parser still accepts all nine, and a sketch plan
    supplied by any other route is validated and refused at the execution
    boundary exactly as before. What this says is narrower: which subset a
    grammar-constrained decoder can be pointed at today.
    """
    return _plan_document(EXECUTABLE_TYPES)


def plan_schema() -> Dict[str, Any]:
    """A JSON Schema for the plan, for providers that can constrain output.

    Advisory only: :mod:`cad_experimental.parser` re-checks everything. A
    schema the provider honours simply means fewer wasted calls, never a
    reason to trust the payload.

    Accepted by Anthropic structured output as of Stage 41. Three of that
    API's limits bind here at once -- at most 24 optional properties, a
    compiled-grammar size ceiling, and a separate complexity ceiling -- and
    the shape below is the one that satisfies all three while describing
    every one of the nine operations exactly. See :func:`_operation_branches`
    for the union, and :func:`_plan_defs` for why the shared definitions
    exist.
    """
    return _plan_document(OPERATION_TYPES)


__all__ = [
    "AXES",
    "BOX",
    "CONSTRUCTIVE_TYPES",
    "CONSUMING_TYPES",
    "FILLET",
    "MAX_TOOLS",
    "SELECTOR_AXES",
    "SELECTOR_FIELDS",
    "SELECT_ALL",
    "SELECT_AXIS_PARALLEL",
    "SELECT_MODES",
    "CHAMFER",
    "EXECUTABLE_TYPES",
    "PROFILE_TYPES",
    "SKETCH",
    "SketchOperation",
    "is_executable",
    "is_profile",
    "EDGE_MODIFIER_LENGTH",
    "EDGE_MODIFIER_TYPES",
    "ChamferOperation",
    "EdgeSelector",
    "FilletOperation",
    "SUBTRACT",
    "SubtractOperation",
    "is_consuming",
    "tools_of",
    "CYLINDER",
    "MODIFIER_TYPES",
    "EXTRUDE",
    "EXTRUDE_OPTIONAL",
    "EXTRUDE_REQUIRED",
    "ExtrudeOperation",
    "FULL_TURN",
    "AXIS_DEF",
    "ID_DEF",
    "OPERATION_FIELDS",
    "POINT3D_DEF",
    "SELECTOR_DEF",
    "provider_schema",
    "PROFILE_SOLID_TYPES",
    "REVOLVE",
    "REVOLVE_OPTIONAL",
    "REVOLVE_REQUIRED",
    "RevolveOperation",
    "SOLID_DECLARING_TYPES",
    "TARGETED_TYPES",
    "declares_solid",
    "is_profile_solid",
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
