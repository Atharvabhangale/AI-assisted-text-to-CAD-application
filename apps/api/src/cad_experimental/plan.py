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

from . import edge_semantics as _semantics

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

#: The only operation types this stage implements. Anything else is rejected
#: by the parser rather than passed along to be someone else's problem.
BOX = "box"
CYLINDER = "cylinder"
THROUGH_HOLE = "through_hole"
SUBTRACT = "subtract"
UNION = "union"
FILLET = "fillet"
CHAMFER = "chamfer"
SKETCH = "sketch"
EXTRUDE = "extrude"
REVOLVE = "revolve"
PATTERN = "pattern"
PART = "part"

#: The **geometry** vocabulary: every operation that makes, changes or
#: describes a shape. Eleven types.
#:
#: This tuple is what every provider encoding and the prompt are built from,
#: so what is in it is what a model can be told about and can say. A type
#: added here changes the schemas' fingerprints and the prompt's, and that is
#: correct -- they describe the geometry language.
OPERATION_TYPES: Tuple[str, ...] = (
    BOX, CYLINDER, THROUGH_HOLE, SUBTRACT, UNION, FILLET, CHAMFER, SKETCH,
    EXTRUDE, REVOLVE, PATTERN,
)

#: Operations that produce **no geometry at all** and instead declare
#: something about the result. ``part`` is the first, and today the only one.
#:
#: Deliberately its own tier rather than an twelfth entry in
#: :data:`OPERATION_TYPES`, and the reason is structural rather than
#: tidiness. Every schema variant, and the prompt's own type list, is built
#: from ``OPERATION_TYPES``; adding ``part`` there would have moved
#: `plan_schema`, `provider_schema`, `compact_provider_schema` and the
#: PROMPT fingerprint in one edit -- teaching a live model a grammar whose
#: semantics are not yet measured, which `docs/multi-body-design.md` refuses
#: outright ("no new model-facing grammar until the slice works
#: deterministically"). Keeping the tiers apart makes "no recorded
#: fingerprint moved" a property of the design instead of a thing to
#: remember, and `test_multi_body.py` asserts it.
DECLARATION_TYPES: Tuple[str, ...] = (PART,)

#: Everything a plan may contain, and so everything the parser accepts.
PLAN_TYPES: Tuple[str, ...] = OPERATION_TYPES + DECLARATION_TYPES

#: The most independent bodies one plan may declare. A cap for the same
#: reason a pattern's ``count`` is capped: this is an experiment in body
#: identity, not a production assembly tool, and an unbounded body count is
#: an unbounded number of kernel shapes held at once.
MAX_BODIES = 8

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

#: The operations that map **one to one** onto a V1 feature. The adapter
#: writes exactly one feature for each, with the same id.
#:
#: Kept as its own name because it is the schema Stages 41-43 sent: see
#: :func:`executable_schema`. A recorded measurement must stay attributable
#: to the instrument that produced it, so this tuple is frozen at those six
#: and :data:`EXECUTABLE_TYPES` is what grows.
V1_FEATURE_TYPES: Tuple[str, ...] = (
    BOX, CYLINDER, THROUGH_HOLE, SUBTRACT, FILLET, CHAMFER,
)

#: `union` is executable but is NOT a V1 feature: the V1 document has no
#: join, so a plan containing one has no document form and runs on the graph
#: executor. Kept out of `V1_FEATURE_TYPES` for exactly that reason.

#: Operations the adapter can translate at all. The rest are represented and
#: validated here and then refused with an explicit unsupported result --
#: never approximated. See `cad_experimental.sketch` for why.
#:
#: Wider than :data:`V1_FEATURE_TYPES` since ``pattern``, which is not a V1
#: feature and has no id of its own in the document: it EXPANDS into one
#: feature per instance. "Executable" is a question about the adapter, not
#: about how many features come out the other side.
EXECUTABLE_TYPES: Tuple[str, ...] = V1_FEATURE_TYPES + (PATTERN, UNION)

#: Everything the graph executor may carry: the geometry it builds, plus the
#: declarations that build none. Kept apart from :data:`EXECUTABLE_TYPES`
#: because that tuple answers "what can this engine BUILD", and a `part`
#: builds nothing -- folding it in would have made the name a lie and a test
#: that pins the executable set to the six V1 features plus `pattern` and
#: `union` would have had to be weakened to accommodate it.
BUILDABLE_TYPES: Tuple[str, ...] = EXECUTABLE_TYPES + DECLARATION_TYPES

#: Executable operations a V1 document can carry. ``pattern`` is here because
#: it EXPANDS into one V1 feature per instance, so the document never sees the
#: pattern itself; ``union`` is absent because V1 has no join to expand into.
V1_EXPRESSIBLE_TYPES: Tuple[str, ...] = V1_FEATURE_TYPES + (PATTERN,)

#: Executable operations with no document form at all, so a plan carrying one
#: must take the graph executor. Derived rather than listed, so an operation
#: added to :data:`EXECUTABLE_TYPES` and not to :data:`V1_EXPRESSIBLE_TYPES`
#: is routed correctly without a second edit anyone could forget.
#: Derived from :data:`BUILDABLE_TYPES` rather than
#: :data:`EXECUTABLE_TYPES`, so a declaration routes itself: `part` has no
#: V1 document form either -- V1 is single-body by rule S9 -- and naming it
#: here is what sends a multi-body plan to the executor, makes the adapter
#: refuse it with the right reason, and needs no second edit in either place.
EXECUTOR_ONLY_TYPES: Tuple[str, ...] = tuple(
    kind for kind in BUILDABLE_TYPES if kind not in V1_EXPRESSIBLE_TYPES
)

#: Operations a ``pattern`` may repeat.
#:
#: A repeated feature must be **positional** -- repetition is a rule about
#: where a feature goes, so a feature with no position has nothing to vary.
#: Today that is ``through_hole`` and nothing else: a fillet's selector is
#: not a position, a subtract's input is a reference, and a box or cylinder
#: would make a new body per instance, which is real multi-body work rather
#: than a pattern (see `docs/experimental-operation-plan.md`).
#:
#: A table rather than a predicate, so widening it is one line with one place
#: to audit.
PATTERNABLE_TYPES: Tuple[str, ...] = (THROUGH_HOLE,)

#: The two edge-selecting modifiers. They differ only in the name and
#: meaning of their one length: a fillet's ``radius`` rounds, a chamfer's
#: ``distance`` sets back on both adjoining faces.
EDGE_MODIFIER_TYPES: Tuple[str, ...] = (FILLET, CHAMFER)

#: The two operations whose whole input is references: a target and a list
#: of tools, and no ``parameters`` at all. Named for that SHAPE rather than
#: for what they do -- they also happen to be
#: :data:`CONSUMING_TYPES`, but the schema merges on shape, and an operation
#: that consumed its tools with a different shape must not join this group.
TOOL_MODIFIER_TYPES: Tuple[str, ...] = (SUBTRACT, UNION)

#: Operation types that share ONE schema branch, rather than one each.
#:
#: A provider that compiles a schema into a decoding grammar has a ceiling on
#: how many branches a union may hold, and Stage 41 measured this one at
#: **eight**: eight operation branches were accepted and a ninth was refused
#: even when stripped to a single field. Nine operation types therefore do
#: not fit one-per-branch, and Stage 43 paid for that -- the schema it sent
#: covered only the six executable types, so a grammar-constrained model
#: could not emit a ``sketch``, an ``extrude`` or a ``revolve`` at all, and
#: refusing was the only answer left to it.
#:
#: ``fillet`` and ``chamfer`` are the one pair that can share a branch
#: without describing anything new: they carry the same operation-level keys
#: and the same required ``edges`` selector, and differ only in the NAME of
#: their one length (:data:`EDGE_MODIFIER_LENGTH`). Merging them makes
#: ``radius`` and ``distance`` both optional *in the grammar*, which is the
#: whole cost -- and the parser still requires exactly the right one, from
#: :data:`PARAMETERS`, as it always has. Nothing else is loosened.
#:
#: ``subtract`` and ``union`` are the second such pair, and they merge for
#: **free**: their operation-level shape is not merely similar but
#: identical -- ``{id, type, target, tools}``, no ``parameters`` at all --
#: so the merged branch describes each of them exactly as its own branch
#: did. Nothing becomes optional, because there was nothing optional to
#: begin with.
#:
#: That pair had to be found. Adding ``union`` as an eleventh type took the
#: schema to **nine** branches, one past the measured ceiling, which would
#: have made the whole grammar un-compilable -- the Stage 43 failure again,
#: reached from the other direction. Eleven types, eight branches.
MERGED_SCHEMA_GROUPS: Tuple[Tuple[str, ...], ...] = (
    EDGE_MODIFIER_TYPES, PROFILE_SOLID_TYPES, TOOL_MODIFIER_TYPES,
)

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
    THROUGH_HOLE, SUBTRACT, UNION, FILLET, CHAMFER,
)

#: Modifiers that additionally **consume** solids: each id in ``tools`` is
#: removed from the solid set and can never be referenced again (Section
#: C.4). Only ``subtract`` does this, and it is the whole reason this stage
#: exists -- it is the first operation with history.
CONSUMING_TYPES: Tuple[str, ...] = (SUBTRACT, UNION)

#: Operations that repeat an earlier **feature**. Their own category, and
#: not a modifier: a modifier names the body it changes, and a pattern names
#: the feature it repeats -- one hop further from the geometry. Keeping it
#: separate is what lets :data:`TARGETED_TYPES` stay the set of operations
#: with a ``target``, and it preserves the invariant the tests check: every
#: operation type is in exactly one category.
REPEATING_TYPES: Tuple[str, ...] = (PATTERN,)

#: Every operation that carries a ``target``. The two groups differ in what
#: the target must BE -- a modifier's is a solid (P11), a profile-solid
#: operation's is a sketch (P23) -- but both must have one, so the parser
#: requires it from one place.
#: ``part`` is here because it names a solid, and for no other reason: it
#: is NOT a modifier (it changes nothing, so it never joins a body's feature
#: list) and NOT constructive (it creates nothing). Membership here is what
#: makes the parser require its ``target`` and what makes
#: :func:`cad_experimental.graph.expectation` judge that target as a solid,
#: with no special case in either.
TARGETED_TYPES: Tuple[str, ...] = (
    MODIFIER_TYPES + PROFILE_SOLID_TYPES + DECLARATION_TYPES
)

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
#: The selector vocabulary, imported from :mod:`cad_experimental.edge_semantics`
#: rather than restated. That module owns what a selector MEANS; this one
#: owns how a plan carries it, and a second spelling of the same four names
#: is one spelling too many.
#:
#: ``all`` and ``axis_parallel`` are Section C.7's own, unchanged: a plan
#: written before Stage 47 means exactly what it meant. ``straight`` and
#: ``circular`` are Stage 47's, and exist because a cylindrical face's
#: parameterisation seam is a genuine straight edge that no blend can take --
#: see :mod:`cad_experimental.edge_semantics` for the measurement.
SELECT_ALL = _semantics.SELECT_ALL
SELECT_AXIS_PARALLEL = _semantics.SELECT_AXIS_PARALLEL
SELECT_STRAIGHT = _semantics.SELECT_STRAIGHT
SELECT_CIRCULAR = _semantics.SELECT_CIRCULAR
SELECT_MODES: Tuple[str, ...] = _semantics.SELECT_MODES

#: Selector modes V1 itself can express, and so the only ones the adapter
#: can write into a CAD document. The other two are resolved by the
#: executor against the backend's own topology.
V1_SELECT_MODES: Tuple[str, ...] = (SELECT_ALL, SELECT_AXIS_PARALLEL)

#: Modes that must carry an axis, may carry one, and may carry a position.
AXIS_REQUIRED_MODES: Tuple[str, ...] = _semantics.AXIS_REQUIRED
AXIS_OPTIONAL_MODES: Tuple[str, ...] = _semantics.AXIS_OPTIONAL
POSITION_MODES: Tuple[str, ...] = _semantics.POSITION_MODES

#: Which end of the axis a selection is narrowed to. Extremal, not ordinal.
POSITION_TOP = _semantics.POSITION_TOP
POSITION_BOTTOM = _semantics.POSITION_BOTTOM
POSITIONS: Tuple[str, ...] = _semantics.POSITIONS

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

#: A pattern needs how many instances and where to put them. Its `source`
#: is an operation-level key, not a parameter, for the same reason a
#: modifier's `target` is: it is a reference, not a dimension.
PATTERN_REQUIRED: Tuple[str, ...] = ("count", "placement")
PATTERN_OPTIONAL: Tuple[str, ...] = ()

#: How a pattern places its instances. A discriminated sub-object rather
#: than a flat bag of optional fields: a linear pattern has no centre and a
#: radial one has no spacing, and a shape that admitted both would need every
#: field optional and could not say which combination is meant.
PATTERN_LINEAR = "linear"
PATTERN_RADIAL = "radial"
PATTERN_KINDS: Tuple[str, ...] = (PATTERN_LINEAR, PATTERN_RADIAL)

#: Fields per placement kind, required then optional. Read by the parser,
#: the validator and the schema, exactly as :data:`PARAMETERS` is.
PLACEMENT_FIELDS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    PATTERN_LINEAR: (("kind", "axis", "spacing"), ()),
    # `angle` is the step BETWEEN consecutive instances. Omitted it means a
    # full circle divided evenly -- `FULL_TURN / count` -- which is the
    # common case and the one a model most easily gets wrong by hand.
    PATTERN_RADIAL: (("kind", "axis", "centre"), ("angle",)),
}

#: The fewest instances a pattern may declare. One instance is the source
#: feature on its own, which is not a pattern; the plan should just say the
#: feature.
MIN_PATTERN_COUNT = 2

#: The most. A pattern expands into this many features, so it is bounded for
#: the same reason :data:`MAX_TOOLS` is: an unbounded expansion is free
#: denial of service against the kernel.
MAX_PATTERN_COUNT = 64

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
    # `union` is subtract's mirror and carries no parameters either: which
    # solids are joined is the whole of it.
    UNION: ((), ()),
    FILLET: (FILLET_REQUIRED, FILLET_OPTIONAL),
    CHAMFER: (CHAMFER_REQUIRED, CHAMFER_OPTIONAL),
    SKETCH: (SKETCH_REQUIRED, SKETCH_OPTIONAL),
    EXTRUDE: (EXTRUDE_REQUIRED, EXTRUDE_OPTIONAL),
    REVOLVE: (REVOLVE_REQUIRED, REVOLVE_OPTIONAL),
    PATTERN: (PATTERN_REQUIRED, PATTERN_OPTIONAL),
    # A `part` has no parameters and carries no `parameters` key at all,
    # exactly as `subtract` and `union` do not: it is entirely a reference.
    PART: ((), ()),
}

#: The keys an edge selector may carry. ``axis`` is present exactly when
#: ``select`` is ``axis_parallel`` (rule S18) -- not optional, and not
#: allowed otherwise.
SELECTOR_FIELDS: Tuple[str, ...] = ("select", "axis", "position")

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
    # The same shape, for the same reason.
    UNION: ("id", "type", "target", "tools"),
    FILLET: ("id", "type", "target", "parameters"),
    CHAMFER: ("id", "type", "target", "parameters"),
    SKETCH: ("id", "type", "parameters"),
    # `target` names a SKETCH here, not a solid. The key is the same because
    # the question is the same -- "which earlier operation does this act on"
    # -- and rule P23 decides which category the answer must be in.
    EXTRUDE: ("id", "type", "target", "parameters"),
    REVOLVE: ("id", "type", "target", "parameters"),
    # `source`, not `target`. Every other reference in this language names a
    # BODY or a PROFILE -- something geometry is made of. A pattern's names
    # a FEATURE: the operation whose effect is repeated. Reusing `target`
    # would have made that distinction invisible in the wire format and in
    # the schema, and the graph's reference roles exist precisely to keep it.
    PATTERN: ("id", "type", "source", "parameters"),
    # A declaration: one reference and nothing else. No `parameters`, so
    # there is exactly one shape for it rather than two.
    PART: ("id", "type", "target"),
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
    """Which edges of the target a fillet or chamfer acts on.

    Four selectors exist and no others:

    * ``{"select": "all"}`` -- every edge of the target solid;
    * ``{"select": "axis_parallel", "axis": "Z"}`` -- every straight edge
      parallel to that axis, **seams included**. Section C.7's own wording,
      kept unchanged so a plan written before Stage 47 still means what it
      meant;
    * ``{"select": "straight", "axis": "Z"}`` -- the same, **seams
      excluded**. What "the vertical corners" means to a person;
    * ``{"select": "circular", "axis": "Z", "position": "top"}`` -- circular
      edges about that axis, optionally at one end of it. A hole's rim.

    :attr:`axis` is **unsigned**, and a different vocabulary from a
    cylinder's signed axis on purpose. It is required for ``axis_parallel``
    and ``straight``, optional for ``circular``, and forbidden for ``all``.

    :attr:`position` narrows a ``circular`` selection to one end of its axis
    and is admissible nowhere else -- a position needs an axis to be measured
    along. It is **extremal, not ordinal**: ``top`` is every candidate at the
    greatest coordinate, so two holes through one plate both have a top rim
    and both are named. Narrowing to "the single highest" would have to pick
    between them, and picking silently is exactly what this layer must not do.

    Nothing here can name an edge index, a face, or a kernel query. The model
    never sees topology; it says what it means and
    :mod:`cad_experimental.edge_semantics` decides which edges that is.
    """

    select: str
    axis: Optional[str] = None
    position: Optional[str] = None

    def semantic(self) -> "_semantics.SemanticSelector":
        """This selector as the resolver's own type. One conversion, here."""
        return _semantics.SemanticSelector(
            select=self.select, axis=self.axis, position=self.position
        )

    @property
    def is_v1(self) -> bool:
        """Whether a V1 CAD document can carry this selector unchanged."""
        return self.select in V1_SELECT_MODES and self.position is None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"select": self.select}
        if self.axis is not None:
            payload["axis"] = self.axis
        if self.position is not None:
            payload["position"] = self.position
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
class LinearPlacement:
    """Instances evenly spaced along one signed principal direction.

    Instance ``k`` sits ``k * spacing`` from the source along ``axis``, for
    ``k`` from 1 to ``count - 1``. The source itself is instance 0 and is not
    moved: a pattern ADDS the repeats, it does not replace what it repeats.
    """

    KIND = PATTERN_LINEAR

    axis: str
    spacing: float

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.KIND, "axis": self.axis, "spacing": self.spacing}


@dataclass(frozen=True)
class RadialPlacement:
    """Instances turned about an axis through ``centre``.

    Instance ``k`` is the source rotated by ``k * angle`` degrees about the
    line through :attr:`centre` along :attr:`axis`, right-handed about the
    signed direction. The source is instance 0 and is not moved.

    :attr:`angle` is the step **between consecutive instances**, not the span
    they cover. Absent it is ``FULL_TURN / count`` -- instances spread evenly
    around a closed circle, which is the bolt-circle case and the one a
    division by hand most easily gets wrong.

    The axis is signed, and the sign is not decoration: the same centre,
    count and step about ``+Z`` and about ``-Z`` put the instances in mirrored
    places.
    """

    KIND = PATTERN_RADIAL

    axis: str
    centre: Point
    angle: Optional[float] = None

    def step(self, count: int) -> float:
        """The degrees between consecutive instances.

        The default lives here rather than in the parser, so "evenly spaced"
        is one documented fact in one place instead of a value some layer
        invents.
        """
        if self.angle is not None:
            return self.angle
        return FULL_TURN / count if count else FULL_TURN

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "kind": self.KIND,
            "axis": self.axis,
            "centre": self.centre.to_dict(),
        }
        if self.angle is not None:
            payload["angle"] = self.angle
        return payload


#: Either placement. A union rather than one type with everything optional.
Placement = Any


@dataclass(frozen=True)
class PatternOperation:
    """Repeats an earlier feature, ``count`` times including the original.

    The first operation in this language whose input is another
    **operation** rather than a body or a profile. That is the whole reason
    the feature graph tags its edges with a role: ``source`` is not a
    ``target``, and a layer that could not tell them apart would have to
    guess.

    Semantics, stated exactly:

    * ``count`` **includes the source**. A count of 4 means four features in
      total -- the source plus three repeats -- so "four mounting holes" is
      ``count: 4`` and not ``count: 3``;
    * the source is **not consumed and not moved**. It remains in the
      history as itself, and the pattern adds instances beside it;
    * a pattern **inherits the source's own semantics**. Repeating a
      modifier is a modifier: every instance acts on the same body the
      source acted on, the body keeps its id, and the pattern's own id names
      no solid. So the single-solid rule is untouched however many instances
      there are;
    * the instances exist as features only after the adapter expands them.
      Their ids are derived from this operation's id (see
      :func:`instance_id`), which is why those ids must not collide with an
      operation's.
    """

    TYPE = PATTERN

    id: str
    source: str
    count: int
    placement: Placement

    def parameters(self) -> Dict[str, Any]:
        return {"count": self.count, "placement": self.placement.to_dict()}


def instance_id(pattern_id: str, index: int) -> str:
    """The id of one derived instance, as the adapter will write it.

    Deterministic and derived from the pattern's own id, so the same plan
    always produces the same document and a downstream reader can tell which
    pattern a feature came from. Exposed rather than inlined because the
    validator has to check the same ids for collisions that the adapter will
    later write, and two spellings of this rule would be one spelling too
    many.

    ``index`` is the instance number, from 1: instance 0 is the source
    itself, which keeps its own id.
    """
    return f"{pattern_id}-{index}"


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
      unavailable to any later operation, which is what makes a plan have
      history. :class:`UnionOperation` consumes identically; the authority
      on which operations do is :data:`CONSUMING_TYPES`;
    * subtraction only. This never joins two solids -- ``union`` does that,
      and neither produces a new independent body: both keep the target's
      id. There is still no intersection.

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


@dataclass(frozen=True)
class UnionOperation:
    """Boolean union: every solid in ``tools`` is fused into ``target``.

    The counterpart of :class:`SubtractOperation`, and deliberately its exact
    shape -- same references, same consumption, same in-place result:

    * the tools are fused **in list order**;
    * the result replaces the target in place and keeps the **target's** id;
    * every tool solid is **consumed**, so a fused plate cannot be reused;
    * the result must be ONE connected solid. Fusing solids that do not
      touch would leave a disconnected body, which rule E3 already refuses.

    Why the language now has a join
    -------------------------------
    It had none, and that was a deliberate V1 restriction: a part that was
    only meaningful as several primitives joined was reported UNSUPPORTED
    rather than approximated. But an enclosure made of plates is an ordinary
    mechanical part that cannot be said any other way -- six boxes leave six
    solids, which rule S9 refuses. This is the smallest operation that closes
    that gap, and it introduces no new reference kind, no new placement and
    no backend-specific concept.
    """

    TYPE = UNION

    id: str
    target: str
    tools: Tuple[str, ...]

    def parameters(self) -> Dict[str, Any]:
        """No parameters. A union, like a subtract, is entirely references."""
        return {}


@dataclass(frozen=True)
class PartOperation:
    """Declares that a named live body is an intended body of the result.

    The first operation in this language that produces **no geometry**. It
    creates nothing, changes nothing and consumes nothing; it says something
    about what the finished result is supposed to contain.

    Why a declaration rather than inferring two bodies from two live solids
    -------------------------------------------------------------------
    Because inferring is the silent behaviour Stage 62 removed. Before that
    stage a plan that fused two boxes and left a third standing executed with
    ``succeeded=True``, and everything downstream kept ``bodies[0]`` -- so the
    third solid vanished from the render, the measurement and every export
    with nothing anywhere saying so.

    Two live bodies and no ``part`` is therefore still a **mistake**, and
    still fails with ``multiple_solids``. Two live bodies where both are
    declared is a **part with two bodies**. The difference is not guessed
    from the geometry; it is written in the plan, where a reader can see it.

    The result keeps the target's id, as every reference in this language
    does: a declaration does not rename a body and its own id names nothing.
    """

    TYPE = PART

    id: str
    target: str

    def parameters(self) -> Dict[str, Any]:
        """No parameters. A declaration is entirely a reference."""
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


def is_declaration(operation: Operation) -> bool:
    """True if the operation declares something and builds no geometry."""
    return operation_type(operation) in DECLARATION_TYPES


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
    source = getattr(operation, "source", None)
    if source is not None:
        payload["source"] = source
    # Whether this type carries a `parameters` key at all is
    # `OPERATION_FIELDS`' answer, not a guess from whether the dictionary
    # came back empty. The old test -- "write it unless there are tools" --
    # happened to be right for every type that existed, and was wrong the
    # moment a type arrived with neither parameters nor tools: a `part`
    # would have round-tripped as `{"parameters": {}}`, which its own entry
    # in `OPERATION_FIELDS` rejects, so `operation_to_dict` would have
    # produced plans this module's own parser refuses.
    if "parameters" in OPERATION_FIELDS.get(operation_type(operation), ()):
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


def _sketch_schema() -> Dict[str, Any]:
    """The sketch parameters, imported late to keep the module order simple."""
    from .sketch import sketch_schema

    return sketch_schema()


#: Names of the shared ``$defs`` the plan schema emits. Each appears many
#: times across the operation branches -- eleven in the faithful schema,
#: eight once the two mergeable pairs are merged; emitting one copy and
#: referencing it is what keeps the compiled grammar inside the provider's
#: size limit.
ID_DEF = "identifier"
AXIS_DEF = "signed_axis"
POINT3D_DEF = "point"
SELECTOR_DEF = "edge_selector"
PLACEMENT_DEF = "pattern_placement"


def _ref(name: str) -> Dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _selector_branches(
    selector_modes: Tuple[str, ...],
    *,
    require_circular_position: bool,
) -> List[Dict[str, Any]]:
    """One selector branch per mode, instead of one object for all of them.

    The flat selector object cannot say that ``axis`` belongs to some modes
    and ``position`` to exactly one -- as its own comment admits, that would
    condition a property on a sibling's value, which a JSON Schema object
    cannot express. A discriminated union can: each mode gets a branch
    carrying precisely its own fields, required where they are required.

    ``require_circular_position`` additionally makes a circular selector's
    ``position`` **mandatory**. That is a deliberate narrowing of the
    provider encoding and **not** of the language: the canonical form still
    permits a circular selector with no position, meaning both rims, and
    the parser still accepts it. What it buys is that a
    grammar-constrained model can no longer answer "the top rim" by
    omitting the end -- Stage 54 measured it doing exactly that, 4/4, while
    its own prose said "top rim".

    The cost is stated rather than hidden: under such an encoding a model
    cannot ask for both rims at once. No corpus case needs that, and a
    caller who does should choose an encoding without this flag.
    """
    shapes: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {}
    for mode in selector_modes:
        if mode == SELECT_ALL:
            shapes[mode] = ((), ())
        elif mode in POSITION_MODES:
            # `axis` stays optional for circular: omitting it means circular
            # edges about any axis, which is the language's own rule.
            shapes[mode] = (
                ("position",) if require_circular_position else (),
                ("axis",) if require_circular_position else
                ("axis", "position"),
            )
        else:
            shapes[mode] = (("axis",), ())
    branches: List[Dict[str, Any]] = []
    for mode, (required, optional) in shapes.items():
        properties: Dict[str, Any] = {
            "select": {"type": "string", "const": mode},
        }
        for name in required + optional:
            if name == "axis":
                properties["axis"] = {
                    "type": "string", "enum": list(SELECTOR_AXES),
                }
            elif name == "position":
                properties["position"] = {
                    "type": "string", "enum": list(POSITIONS),
                }
        branches.append({
            "type": "object",
            "properties": properties,
            "required": ["select"] + list(required),
            "additionalProperties": False,
        })
    return branches


def _plan_defs(
    selector_modes: Tuple[str, ...] = SELECT_MODES,
    *,
    selector_branches: bool = False,
    require_circular_position: bool = False,
) -> Dict[str, Any]:
    """The shared definitions the operation branches reference.

    ``selector_modes`` narrows the edge selector to what a particular schema
    can carry. :func:`executable_schema` passes :data:`V1_SELECT_MODES`,
    because a V1 CAD document carries exactly those two and because that
    schema is a **recorded instrument**: Stage 43's fingerprint must not move
    when the language gains a selector the document cannot hold.
    """
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
            "anyOf": _selector_branches(
                selector_modes,
                require_circular_position=require_circular_position,
            )
        } if selector_branches else {
            "type": "object",
            "properties": {
                "select": {"type": "string", "enum": list(selector_modes)},
                # Only a `circular` selector may carry this, which the
                # grammar cannot say -- it would have to condition one
                # property on a sibling's value. Rule P32 enforces it, as
                # the parser does. Absent entirely from a schema whose modes
                # cannot take one.
                **({"position": {"type": "string", "enum": list(POSITIONS)}}
                   if any(mode in POSITION_MODES for mode in selector_modes)
                   else {}),
                # Unsigned. Not the signed axis above -- Section C.7 is
                # explicit that parallelism has no direction.
                "axis": {"type": "string", "enum": list(SELECTOR_AXES)},
            },
            "required": ["select"],
            "additionalProperties": False,
        },
        PLACEMENT_DEF: {"anyOf": _placement_branches()},
        **sketch_defs(),
    }


def _placement_branches() -> List[Dict[str, Any]]:
    """One branch per placement kind, derived from :data:`PLACEMENT_FIELDS`.

    The same discriminated-union shape as the operation branches, for the
    same reason: a single object carrying every placement's fields would
    have to make them all optional and could not say which combination is
    meant.
    """
    shapes: Dict[str, Dict[str, Any]] = {
        "kind": {"type": "string"},
        "axis": _ref(AXIS_DEF),
        "spacing": {"type": "number"},
        "centre": _ref(POINT3D_DEF),
        "angle": {"type": "number"},
    }
    branches: List[Dict[str, Any]] = []
    for placement, (required, optional) in PLACEMENT_FIELDS.items():
        properties = {
            name: ({"const": placement} if name == "kind" else shapes[name])
            for name in (*required, *optional)
        }
        branches.append({
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        })
    return branches


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
        # No `minimum`: the structured-output API rejects that keyword
        # (Stage 41). The bound is rule P28's, enforced by the validator,
        # which is where it was always going to be enforced anyway.
        "count": {"type": "integer"},
        "placement": _ref(PLACEMENT_DEF),
        **_sketch_schema(),
    }


def _schema_groups(
    kinds: Tuple[str, ...],
    merged: Tuple[Tuple[str, ...], ...],
) -> List[Tuple[str, ...]]:
    """``kinds``, with the members of each :data:`MERGED_SCHEMA_GROUPS` group
    collapsed to one entry at the position of the group's first member.

    Order is preserved, every kind appears exactly once, and a group whose
    members are not all in ``kinds`` contributes only the members that are --
    so a narrower schema stays correct without a second code path.
    """
    groups: List[Tuple[str, ...]] = []
    placed: set = set()
    for kind in kinds:
        if kind in placed:
            continue
        group = next((g for g in merged if kind in g), (kind,))
        present = tuple(k for k in group if k in kinds)
        placed.update(present)
        groups.append(present)
    return groups


def _branch(
    group: Tuple[str, ...],
    parameters: Dict[str, Any],
    omit: Tuple[str, ...],
) -> Dict[str, Any]:
    """One schema branch describing every operation type in ``group``.

    A one-member group is discriminated by ``{"const": kind}``; a merged
    group by ``{"enum": [...]}``. Everything else is read out of
    :data:`OPERATION_FIELDS` and :data:`PARAMETERS`, so a branch cannot
    describe an operation the parser would refuse.

    For a merged group the parameter properties are the UNION of its members'
    parameters and the ``required`` list is their INTERSECTION -- the only
    sound answer, because the grammar cannot condition one property on the
    value of a sibling. That is the entire cost of merging, it is confined to
    the grammar, and the parser re-derives the exact per-type requirement
    from :data:`PARAMETERS` regardless.
    """
    shapes = {OPERATION_FIELDS[kind] for kind in group}
    if len(shapes) != 1:
        raise ValueError(
            "a merged schema group must share one operation-level shape; "
            f"{group!r} does not"
        )
    allowed = OPERATION_FIELDS[group[0]]

    properties: Dict[str, Any] = {
        "id": _ref(ID_DEF),
        "type": (
            {"const": group[0]} if len(group) == 1
            else {"enum": list(group)}
        ),
    }
    required = ["id", "type"]
    if "target" in allowed:
        properties["target"] = _ref(ID_DEF)
        required.append("target")
    if "tools" in allowed:
        properties["tools"] = {
            "type": "array", "minItems": 1, "items": _ref(ID_DEF),
        }
        required.append("tools")
    if "source" in allowed:
        properties["source"] = _ref(ID_DEF)
        required.append("source")

    if "parameters" in allowed:
        names: List[str] = []
        per_kind_required: List[Tuple[str, ...]] = []
        for kind in group:
            kind_required, kind_optional = PARAMETERS[kind]
            for name in (*kind_required, *kind_optional):
                if name not in omit and name not in names:
                    names.append(name)
            per_kind_required.append(
                tuple(n for n in kind_required if n not in omit)
            )
        properties["parameters"] = {
            "type": "object",
            "properties": {name: parameters[name] for name in names},
            "required": [
                name for name in names
                if all(name in one for one in per_kind_required)
            ],
            "additionalProperties": False,
        }
        required.append("parameters")

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _operation_branches(
    kinds: Tuple[str, ...] = OPERATION_TYPES,
    *,
    merged: Tuple[Tuple[str, ...], ...] = (),
    omit_parameters: Tuple[str, ...] = (),
) -> List[Dict[str, Any]]:
    """The schema branches for ``kinds``, discriminated by ``type``.

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

    Why some branches carry two types
    ---------------------------------
    ``merged`` names groups that share one branch, to fit the provider's
    eight-branch ceiling -- see :data:`MERGED_SCHEMA_GROUPS`. It is empty by
    default, so :func:`plan_schema` still describes one type per branch.

    **The wire format is unchanged.** This is the same
    ``{id, type, target?, tools?, parameters?}`` object the parser has always
    accepted, described precisely instead of loosely -- so the parser, the
    validator and the V1 adapter needed no change at all.
    """
    parameters = _parameter_schemas()
    return [
        _branch(group, parameters, omit_parameters)
        for group in _schema_groups(kinds, merged)
    ]



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


def _plan_document(
    kinds: Tuple[str, ...],
    *,
    merged: Tuple[Tuple[str, ...], ...] = (),
    omit_parameters: Tuple[str, ...] = (),
    selector_modes: Tuple[str, ...] = SELECT_MODES,
    selector_branches: bool = False,
    require_circular_position: bool = False,
) -> Dict[str, Any]:
    """The plan schema over exactly ``kinds``, with unused defs pruned."""
    return _prune_defs({
        "type": "object",
        "$defs": _plan_defs(
            selector_modes,
            selector_branches=selector_branches,
            require_circular_position=require_circular_position,
        ),
        "properties": {
            "status": {"type": "string", "enum": [s.value for s in PlanStatus]},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
            "operations": {
                "type": "array",
                "items": {
                    "anyOf": _operation_branches(
                        kinds,
                        merged=merged,
                        omit_parameters=omit_parameters,
                    )
                },
            },
        },
        "required": ["status", "operations", "summary"],
        "additionalProperties": False,
    })


def provider_schema() -> Dict[str, Any]:
    """The plan schema a structured-output provider is pointed at.

    Covers the **whole vocabulary** -- all **eleven** operation types,
    sketches, extrudes, revolves, ``pattern`` and ``union`` included -- in
    **eight branches**, by merging ``fillet`` with ``chamfer`` and
    ``subtract`` with ``union``. See :data:`MERGED_SCHEMA_GROUPS` for why
    those pairs and no others. Measured at 7360 inlined characters, which is
    above the proven refusal: this is the faithful description, not the
    encoding the live route sends.

    Why this is not the executable subset any more
    ----------------------------------------------
    Until Stage 44 this returned :func:`executable_schema`: the six types the
    CAD engine can build. That looked like a conservative choice and was in
    fact a silent one. Stage 43 ran with structured output on, so the model
    decoded against this grammar -- and a grammar with no ``sketch``,
    ``extrude`` or ``revolve`` branch cannot emit one. On both profile cases
    the model answered ``unsupported`` 5/5, which the run scored as the
    model's judgement. It was not: the representation had removed the answer
    from the model's reach, and no prompt could have put it back.

    A schema is what the model may SAY, not what the engine can BUILD. Those
    are different questions and conflating them made a measurement of the
    second look like a measurement of the first. The execution boundary still
    exists and is unchanged: a plan containing a profile operation parses,
    validates, and is then refused by
    :class:`cad_experimental.adapter.ExecutionUnsupported` -- explicitly,
    with the offending ids, and never approximated.

    What is still unverified
    ------------------------
    That the provider COMPILES this. Stage 41 measured the ceiling at eight
    branches, and this has eight -- but the eight it measured did not include
    ``sketch``, whose ``$defs`` are the largest part of the schema. Verifying
    that costs one live call and cannot be done without a credential, so it
    has not been done here. :func:`compact_provider_schema` is the smaller
    variant to try if this one is refused; nothing selects it automatically.
    """
    return _plan_document(OPERATION_TYPES, merged=MERGED_SCHEMA_GROUPS)


def compact_provider_schema() -> Dict[str, Any]:
    """:func:`provider_schema` without a sketch's optional ``constraints``.

    The same eight branches over the same nine types, about a fifth smaller,
    because dropping ``constraints`` drops the two largest shared
    definitions with it. Offered for one reason: if the provider refuses
    :func:`provider_schema` on grammar size, this is the next thing to try
    without giving up profile operations altogether.

    It costs the model nothing it needs to describe a solid. A sketch's
    constraints are CHECKED, never solved -- rule P21 requires a dimensional
    constraint to AGREE with the geometry it names -- so they can only
    restate what the geometry already says, or conflict with it. Geometry
    written at the size it means needs none.

    **Nothing selects this automatically.** A caller that asked for one
    schema and silently got another could not know what its numbers mean,
    which is the same reason :func:`cad_experimental.cad_backend.resolve_backend`
    never falls back to a different engine.
    """
    return _plan_document(
        OPERATION_TYPES,
        merged=MERGED_SCHEMA_GROUPS,
        omit_parameters=("constraints",),
    )


def executable_schema() -> Dict[str, Any]:
    """The plan schema over the six types the CAD engine can build.

    What :func:`provider_schema` returned in Stages 41-43, kept under its own
    name so the Stage 43 instrument stays exactly what it was: a run that
    reports this schema's fingerprint must have sent this schema.

    A grammar-constrained model pointed at this one **cannot express a
    profile at all**, so it is not a way to ask whether the model would
    choose one.

    Pinned to :data:`V1_FEATURE_TYPES` rather than to
    :data:`EXECUTABLE_TYPES`, which has since grown a ``pattern``. A recorded
    measurement must stay attributable to the instrument that produced it:
    re-running Stage 43 must reproduce Stage 43, fingerprint included.
    """
    return _plan_document(V1_FEATURE_TYPES, selector_modes=V1_SELECT_MODES)


def plan_schema() -> Dict[str, Any]:
    """A JSON Schema for the plan: one branch per operation type.

    The faithful description of the language, and the one the experimental
    API publishes. Every type is discriminated by its own ``const`` and
    carries exactly its own required parameters, so nothing here is loosened
    for a decoder's benefit -- which is also why it is nine branches and a
    provider will not compile it.

    Advisory only: :mod:`cad_experimental.parser` re-checks everything. A
    schema the provider honours simply means fewer wasted calls, never a
    reason to trust the payload.
    """
    return _plan_document(OPERATION_TYPES)


# --- provider encodings measured against the live compiler ------------------
#
# Stage 50/51 established, by real API calls, that the Anthropic
# structured-output compiler refuses this language's full grammar and that the
# binding constraint is TOTAL compiled size -- not sketches, and not any one
# operation. Measured, in ref-inlined characters:
#
#     3487  box, cylinder, sketch, extrude, revolve            ACCEPTED
#     4030  + through_hole, - revolve                          ACCEPTED
#     4121  box, cylinder, through_hole, sketch, extrude,
#           revolve                                            ACCEPTED
#     4481  + subtract                                         ACCEPTED
#     4551  the six solid types + sketch                       REFUSED
#     4698  nine types, no sketch at all                       REFUSED
#     6190  compact_provider_schema                            REFUSED
#     7351  provider_schema                                    REFUSED
#
# So the ceiling lies in (4481, 4551] and nothing narrower is known. Do not
# write it down as a number: it is a bound, it was not published, and it may
# move.
#
# Each function below returns one **encoding** of the operation plan. None of
# them is the language. The parser and the validator never see a schema and
# decide what a plan means regardless; an operation absent from an encoding is
# merely unsayable by a grammar-constrained decoder, not illegal. Choosing
# between them is always the caller's explicit act -- nothing here falls back.

#: The narrowing every profile encoding shares: a sketch's ``constraints`` are
#: omitted. They are CHECKED, never solved (rule P21 requires a dimensional
#: constraint to AGREE with the geometry it names), so they can only restate
#: what the geometry already says. Geometry written at the size it means needs
#: none, and dropping them is the single largest saving available.
_PROFILE_OMITTED: Tuple[str, ...] = ("constraints",)


def profile_provider_schema() -> Dict[str, Any]:
    """A whole profile pipeline: sketch, both consumers, two solids.

    **Measured ACCEPTED at 3487 inlined characters** -- the first
    sketch-carrying grammar this provider ever compiled.

    Expresses ``box``, ``cylinder``, ``sketch``, ``extrude`` and ``revolve``.
    Cannot express ``through_hole``, ``subtract``, ``fillet``, ``chamfer`` or
    ``pattern``: a request needing one of those cannot be answered under this
    encoding, and a refusal produced under it says nothing about the model.

    Prefer :func:`profile_union_provider_schema` unless the smallest possible
    grammar is the point; it carries two more operations and is also proven.
    """
    return _plan_document(
        ("box", "cylinder", "sketch", "extrude", "revolve"),
        merged=MERGED_SCHEMA_GROUPS,
        omit_parameters=_PROFILE_OMITTED,
        selector_modes=V1_SELECT_MODES,
    )


def profile_hole_provider_schema() -> Dict[str, Any]:
    """A profile pipeline with the corpus's most-used modifier.

    **Measured ACCEPTED at 4030 inlined characters.**

    Expresses ``box``, ``cylinder``, ``through_hole``, ``sketch`` and
    ``extrude``. Trades ``revolve`` away for ``through_hole``; kept as its own
    named encoding because it is separately proven and because a run recorded
    against it must stay reproducible.

    :func:`profile_union_provider_schema` carries both and is also proven, so
    this exists for the record rather than as a recommendation.
    """
    return _plan_document(
        ("box", "cylinder", "through_hole", "sketch", "extrude"),
        omit_parameters=_PROFILE_OMITTED,
        selector_modes=V1_SELECT_MODES,
    )


def profile_union_provider_schema() -> Dict[str, Any]:
    """Seven of the **eleven** operations, and the largest grammar known to
    compile.

    **Measured ACCEPTED at 4481 inlined characters**, against a refusal at
    4551 -- so this sits inside seventy characters of the ceiling and there
    is no room to add anything.

    Expresses ``box``, ``cylinder``, ``through_hole``, ``subtract``,
    ``sketch``, ``extrude`` and ``revolve``: every solid-building operation
    except the edge treatments, and the whole profile pipeline.

    Cannot express ``fillet``, ``chamfer`` or ``pattern``. That is not a
    judgement about their value -- adding the edge pair measures 4741, past
    the refusal at 4551, so the grammar simply has no room. A case needing a
    fillet must be measured under an encoding that has one, and scored as
    *not expressible here* rather than as anything the model did.

    **This is the encoding to reach for.** It dominates
    :func:`profile_provider_schema` and :func:`profile_hole_provider_schema`
    on capability and is proven on the same evidence.
    """
    return _plan_document(
        ("box", "cylinder", "through_hole", "subtract", "sketch", "extrude",
         "revolve"),
        merged=MERGED_SCHEMA_GROUPS,
        omit_parameters=_PROFILE_OMITTED,
        selector_modes=V1_SELECT_MODES,
    )


def strict_selector_provider_schema() -> Dict[str, Any]:
    """:func:`selector_provider_schema` with the end of a rim made structural.

    **Stage 55's hypothesis, as a schema.** Stage 54 established that the
    model understands "the top rim" -- its own prose says so -- and then
    omits ``position`` anyway, 4 attempts out of 4, under an encoding where
    the field is optional. Prompt guidance did not move it. The remaining
    explanation is the shape of the schema rather than the model's reading
    of the request, and this is the smallest change that tests it.

    The selector becomes a **discriminated union**, one branch per mode,
    instead of one object with every field optional. That alone expresses
    what the flat object could not: ``axis`` is required for ``straight``
    and ``axis_parallel``, absent for ``all``, optional for ``circular``.
    On top of that, the circular branch **requires** ``position``.

    What this narrows, stated plainly
    ---------------------------------
    The canonical language permits a circular selector with no position,
    meaning *both* rims, and the parser still accepts exactly that. This
    encoding cannot say it. A model pointed here must choose an end, so a
    request that genuinely means both rims is not expressible -- no corpus
    case needs one, and a caller who does should use
    :func:`selector_provider_schema` instead.

    Nothing decodes, translates or repairs: what the model emits under this
    grammar is already canonical wire format, and the parser is unchanged.
    """
    return _plan_document(
        V1_FEATURE_TYPES,
        merged=MERGED_SCHEMA_GROUPS,
        selector_modes=SELECT_MODES,
        selector_branches=True,
        require_circular_position=True,
    )


#: :data:`V1_FEATURE_TYPES` plus ``union``, for an encoding that must be able
#: to say "join these plates into one body".
#:
#: Not :data:`EXECUTABLE_TYPES`, which also carries ``pattern`` and costs a
#: sixth branch. This tuple is the smallest widening that closes the gap
#: below.
V1_FEATURE_TYPES_WITH_UNION: Tuple[str, ...] = V1_FEATURE_TYPES + (UNION,)


def strict_selector_union_provider_schema() -> Dict[str, Any]:
    """:func:`strict_selector_provider_schema`, able to say ``union``.

    **The gap this closes.** Prompt ``2026-09-17.1`` teaches ``union`` as the
    way a part made of several plates is built, and removed it from the
    UNSUPPORTED list. The live generation path, however, was still decoding
    against :func:`strict_selector_provider_schema`, whose five branches are
    built from :data:`V1_FEATURE_TYPES` -- and ``union`` is deliberately not
    in that tuple. So the prompt said "use a union" to a decoder that had no
    union branch to emit, and every multi-plate request came back a refusal
    that the run would have recorded as the model's judgement.

    **That is the Stage 44 defect exactly**, in a third place: a schema is
    what the model may SAY, and narrowing it to something the prompt
    contradicts removes an answer from the model's reach and then scores the
    model for not giving it. Stage 44 found it for ``sketch``, Stage 48 for
    ``pattern``, this for ``union``. When an operation is added, the
    **grammar the live path sends** is part of the operation, alongside the
    refusal list.

    **The cost is nine characters and no branch.** ``subtract`` and ``union``
    share an operation-level shape exactly -- a ``target`` and an ordered
    ``tools`` list -- so they merge into one branch the way ``fillet`` and
    ``chamfer`` already do:

    ============================== ======== ========
    encoding                       inlined  branches
    ============================== ======== ========
    ``strict_selector``                3619        5
    ``strict_selector_union``          3628        5
    ============================== ======== ========

    **Compilability is NOT measured.** 3628 sits below every size this
    project has measured ACCEPTED live (the highest is 4481) and far below
    the lowest measured REFUSED (4551), so it is very likely to compile --
    but "likely" is not "measured", and only a live call settles it. This
    encoding is therefore deliberately **absent from
    ``stage48.PROVEN_COMPILABLE``**, which means "measured accepted", not
    "expected to be accepted".

    :func:`strict_selector_provider_schema` is left byte-identical at 3619.
    It is a recorded measurement point, and a recorded measurement describes
    the object that was measured rather than whatever the name later points
    at.
    """
    return _plan_document(
        V1_FEATURE_TYPES_WITH_UNION,
        merged=MERGED_SCHEMA_GROUPS,
        selector_modes=SELECT_MODES,
        selector_branches=True,
        require_circular_position=True,
    )


#: The eight types a multi-body encoding must be able to say: the seven
#: :data:`V1_FEATURE_TYPES_WITH_UNION` plus the declaration itself.
#:
#: Written as its own tuple rather than assembled at the call site so that
#: the one place a provider encoding learns about `part` is greppable, and so
#: that :data:`OPERATION_TYPES` stays at eleven -- moving `part` into that
#: tuple would move nine recorded fingerprints and the prompt in one edit,
#: which is the whole reason :data:`DECLARATION_TYPES` exists.
V1_FEATURE_TYPES_WITH_UNION_AND_PART: Tuple[str, ...] = (
    V1_FEATURE_TYPES_WITH_UNION + (PART,)
)


def strict_selector_union_part_provider_schema() -> Dict[str, Any]:
    """:func:`strict_selector_union_provider_schema`, able to say ``part``.

    **The gap this closes.** Stages 71-74 built the whole deterministic
    multi-body slice -- declaration, body-local history, per-body
    measurement, body-by-name editing, a verified STEP assembly -- and every
    number recorded for it is DETERMINISTIC, because the live route decodes
    against :func:`strict_selector_union_provider_schema`, which has no
    ``part`` branch. A model cannot emit a token the grammar does not
    contain, so multi-body was unreachable from natural language and no
    measurement of it said anything about a model.

    **That is the Stage 44 defect in a fourth place.** A schema is what the
    model may SAY; the execution boundary is what the engine can BUILD.
    Stage 44 found it for ``sketch``, Stage 48 for ``pattern``, Stage 61 for
    ``union``, and this is ``part``. The rule the branch keeps re-learning:
    when an operation becomes reachable, the **grammar the live path sends**
    is part of the operation, alongside the prompt and the refusal list.

    **The cost is 246 characters and one branch.** ``part`` shares its
    operation-level shape with nothing -- ``{id, type, target}`` and no
    ``parameters`` at all (:data:`OPERATION_FIELDS`) -- so unlike ``union``
    it cannot merge into an existing branch and takes a sixth:

    ===================================== ======== ========
    encoding                               inlined  branches
    ===================================== ======== ========
    ``strict_selector``                        3619        5
    ``strict_selector_union``                  3628        5
    ``strict_selector_union_part``             3874        6
    ===================================== ======== ========

    **Branch count is not the constraint; total size is.** Stage 41's "eight
    branches accepted, a ninth refused" was superseded by Stage 51, whose
    data contains a six-branch grammar REFUSED at 4551 beside a six-branch
    grammar ACCEPTED at 4481. Against that bracket 3874 is 607 below the
    largest ever accepted and 677 below the smallest ever refused.

    **Compilability is NOT measured**, and this encoding is therefore
    deliberately absent from ``stage48.PROVEN_COMPILABLE``, which means
    "measured accepted" and not "expected to be accepted". One live call
    settles it and nothing else does.

    Every one of the nine recorded encodings is left byte-identical. A
    recorded measurement describes the object that was measured, not
    whatever the name later points at.
    """
    return _plan_document(
        V1_FEATURE_TYPES_WITH_UNION_AND_PART,
        merged=MERGED_SCHEMA_GROUPS,
        selector_modes=SELECT_MODES,
        selector_branches=True,
        require_circular_position=True,
    )


#: The selector modes a pattern-carrying encoding can afford. `axis_parallel`
#: is dropped from the ENCODING -- not from the language, not from the parser,
#: and not from any plan already written. It is the one mode that includes a
#: cylindrical face's parameterisation seam, so on any drilled part it is the
#: mode that FAILS (code R2); `straight` is the same selection with the seam
#: removed, which is what a person asking to "round the vertical edges" means.
#: Dropping it costs no reachable capability and buys the 188 characters that
#: let `pattern` exist at all.
PATTERN_SELECT_MODES: Tuple[str, ...] = (SELECT_ALL, SELECT_STRAIGHT, SELECT_CIRCULAR)


def pattern_provider_schema() -> Dict[str, Any]:
    """Every executable operation, `pattern` included, with strict selectors.

    **Measured 4454 inlined characters**, against a point measured ACCEPTED at
    4481 and one measured REFUSED at 4551. The whole vocabulary is refused
    outright; the executable types with all four selector modes is past the
    refusal too. This is the encoding that fits.

    It read **4445** until ``union`` joined :data:`EXECUTABLE_TYPES`, which
    this schema is built from -- nine characters, no extra branch, and still
    inside the accepted band. The figure drifted silently because no test
    pinned it; `test_provider_encodings` now does, so the next such change
    has to be deliberate.

    What it expresses that `strict_selector_provider_schema` cannot: the
    `pattern` operation -- a real, buildable, graph-native operation whose
    arithmetic lives in `cad_experimental.pattern` and which the executor has
    supported since Stage 46, but which no compilable encoding had ever been
    able to say.

    What it gives up, recorded rather than hidden: the `axis_parallel`
    selector mode. See :data:`PATTERN_SELECT_MODES` for why that costs nothing
    a caller can reach. `sketch`, `extrude` and `revolve` remain unsayable, as
    in every encoding that compiles -- the execution boundary refuses them
    anyway -- and a circular selector still must name its end.

    The canonical IR is untouched. This is an encoding of it: the parser and
    the validator accept exactly what they accepted before, including
    `axis_parallel` from a fixture, a saved plan or a hand-written request.
    """
    return _plan_document(
        EXECUTABLE_TYPES,
        merged=MERGED_SCHEMA_GROUPS,
        selector_modes=PATTERN_SELECT_MODES,
        selector_branches=True,
        require_circular_position=True,
    )


def selector_provider_schema() -> Dict[str, Any]:
    """The six solid operations, able to *name the edges they mean*.

    All of :data:`V1_FEATURE_TYPES` -- ``box``, ``cylinder``,
    ``through_hole``, ``subtract``, ``fillet``, ``chamfer`` -- with the
    **full** selector vocabulary: ``all``, ``axis_parallel``, ``straight``,
    ``circular``, and a circular selector's ``position`` (``top``/
    ``bottom``). ``fillet`` and ``chamfer`` share one branch, which is what
    pays for the wider selector.

    Why this and not a profile encoding
    -----------------------------------
    Stage 52 proposed adding selectors to :func:`profile_provider_schema`,
    estimating ~150 inlined characters. The estimate was not merely wrong,
    it was meaningless: **a profile encoding has nothing that selects an
    edge.** Only ``fillet`` and ``chamfer`` carry an ``edges`` selector, and
    a profile encoding has neither -- so ``_prune_defs`` drops the selector
    definition entirely and widening the modes changes not one byte. A
    selector-capable grammar must contain a selector-carrying operation.

    That is why this is built on the six solid types. Every one of the seven
    selector-dependent corpus cases needs a ``through_hole`` or a
    ``subtract`` as well as its edge treatment, so a profile-plus-selector
    encoding could not express any of them even once the selector was real.

    What it is for
    --------------
    Stage 52's central defect. Under an encoding offering only ``all`` and
    ``axis_parallel``, a model asked for a hole's top rim answered
    ``select: "all"`` -- chamfering every edge of the plate, which failed in
    the kernel and was recorded as a *build failure*. That is a schema limit
    wearing a model's mistake as a disguise. This grammar can say
    ``circular`` + ``top``, so the answer it gets back is the model's own.

    The selector vocabulary is the language's, unchanged: this widens what
    may be **said**, never what any mode **means**. The parser stays the
    authority, rule P32 still governs which modes may carry a ``position``,
    and the wire format is untouched.

    Cannot express ``sketch``, ``extrude``, ``revolve`` or ``pattern``.
    """
    return _plan_document(
        V1_FEATURE_TYPES,
        merged=MERGED_SCHEMA_GROUPS,
        selector_modes=SELECT_MODES,
    )


__all__ = [
    "POSITIONS",
    "POSITION_BOTTOM",
    "POSITION_TOP",
    "POSITION_MODES",
    "AXIS_OPTIONAL_MODES",
    "AXIS_REQUIRED_MODES",
    "V1_SELECT_MODES",
    "SELECT_CIRCULAR",
    "SELECT_STRAIGHT",
    "REPEATING_TYPES",
    "instance_id",
    "V1_EXPRESSIBLE_TYPES",
    "V1_FEATURE_TYPES",
    "V1_FEATURE_TYPES_WITH_UNION",
    "V1_FEATURE_TYPES_WITH_UNION_AND_PART",
    "RadialPlacement",
    "MIN_PATTERN_COUNT",
    "MAX_PATTERN_COUNT",
    "PLACEMENT_FIELDS",
    "PATTERN_RADIAL",
    "PATTERN_LINEAR",
    "PATTERN_KINDS",
    "PATTERNABLE_TYPES",
    "PATTERN",
    "PatternOperation",
    "LinearPlacement",
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
    "PATTERN_SELECT_MODES",
    "SELECT_MODES",
    "CHAMFER",
    "EDGE_MODIFIER_TYPES",
    "TOOL_MODIFIER_TYPES",
    "EXECUTABLE_TYPES",
    "BUILDABLE_TYPES",
    "DECLARATION_TYPES",
    "EXECUTOR_ONLY_TYPES",
    "MAX_BODIES",
    "PART",
    "PLAN_TYPES",
    "PartOperation",
    "is_declaration",
    "MERGED_SCHEMA_GROUPS",
    "PROFILE_TYPES",
    "SKETCH",
    "SketchOperation",
    "is_executable",
    "is_profile",
    "EDGE_MODIFIER_LENGTH",
    "ChamferOperation",
    "EdgeSelector",
    "FilletOperation",
    "SUBTRACT",
    "SubtractOperation",
    "UNION",
    "UnionOperation",
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
    "compact_provider_schema",
    "executable_schema",
    "profile_hole_provider_schema",
    "profile_provider_schema",
    "selector_provider_schema",
    "strict_selector_provider_schema",
    "strict_selector_union_part_provider_schema",
    "strict_selector_union_provider_schema",
    "profile_union_provider_schema",
    "pattern_provider_schema",
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
