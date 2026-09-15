"""Validation for the experimental plan. Separate from the V1 validator.

``cad_core.validator`` is untouched and remains authoritative for CAD
documents. This module judges the *plan*, which is a different artefact with
a different shape, and it deliberately does not try to anticipate the V1
validator's verdict.

The division of labour matters, and is the point of the layering:

* **here**: is this a coherent plan? At least one operation, unique ids,
  known types, finite and positive dimensions, a legal axis, a real position.
* **there** (``cad_core.validator``, via the adapter): is the resulting CAD
  document valid? Rules S1-S20, including the single-solid rule S9 that this
  module does *not* enforce -- a two-box plan is a perfectly coherent plan and
  an invalid V1 part, and letting the real validator say so is the honest
  answer rather than a duplicated rule that could drift from it.

Nothing here repairs anything. A problem is reported, never fixed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .graph import REPEATABLE, feature_graph
from .history import walk
from .plan import (
    AXES,
    BOX,
    PARAMETERS,
    DEFAULT_AXIS,
    MAX_PATTERN_COUNT,
    MIN_PATTERN_COUNT,
    PATTERN,
    PATTERNABLE_TYPES,
    LinearPlacement,
    RadialPlacement,
    instance_id,
    CONSTRUCTIVE_TYPES,
    CHAMFER,
    CYLINDER,
    EDGE_MODIFIER_LENGTH,
    EDGE_MODIFIER_TYPES,
    EXTRUDE,
    FILLET,
    FULL_TURN,
    MODIFIER_TYPES,
    PROFILE_SOLID_TYPES,
    REVOLVE,
    SELECT_AXIS_PARALLEL,
    SELECT_MODES,
    SELECTOR_AXES,
    SKETCH,
    SUBTRACT,
    THROUGH_HOLE,
    OperationPlan,
    PlanStatus,
    tools_of,
)
from .sketch import (
    CIRCLE,
    PLANE_AXES,
    PLANE_NORMAL,
    CONSTRAINT_APPLIES_TO,
    DIMENSIONAL_TYPES,
    LENGTH,
    LINE,
    POINT_HANDLES,
    RADIUS,
    RECTANGLE,
)

#: Plan rule codes. Prefixed ``P`` so they can never be mistaken in a log for
#: the specification's own ``S``/``E`` codes.
P1 = "P1"  # a generated plan has at least one operation
P2 = "P2"  # operation ids are unique
P3 = "P3"  # the operation type is implemented
P4 = "P4"  # dimensions are finite and strictly positive
P5 = "P5"  # the axis is one of the six signed principal directions
P6 = "P6"  # a position is three finite numbers
P7 = "P7"  # a non-generated plan explains itself

# --- reference rules, added with the first modifier (through_hole) ---------
#
# These mirror the specification's S6/S7 for the *plan*, so a bad reference
# is caught before conversion rather than after. They do not replace S6/S7:
# the V1 validator still runs on the converted document and remains
# authoritative. Catching it here means a clearer message, sooner.
P8 = "P8"   # a modifier names a target
P9 = "P9"   # the target names an operation that exists in the plan
P10 = "P10"  # the target appears strictly earlier (no forward refs, no cycles)
P11 = "P11"  # the target is a constructive body, not another modifier
P12 = "P12"  # the reference has not been consumed

# --- consumption rules, added with the first consuming modifier (subtract) -
#
# P9-P12 already judge a reference; a subtract simply has more of them, and
# every tool goes through the same four checks as a target. What is new is
# the shape of the tool list itself, mirroring S14 and S15.
P13 = "P13"  # `tools` is a non-empty list of ids
P14 = "P14"  # a tool is neither the target nor a repeat of another tool

# --- selector rules, added with the first edge-selecting modifier (fillet) -
#
# These judge the selector as *data*: a shape and a vocabulary. They say
# nothing about geometry. Whether the selector matches an edge (E4) and
# whether the radius is admissible for every matched edge (E5) are the
# engine's to decide, and this layer deliberately does not guess -- see
# `docs/edge-selection.md` for why guessing would be wrong.
P15 = "P15"  # the selector is a supported mode
P16 = "P16"  # the selector's axis is present exactly when required (S18)
P17 = "P17"  # the selector's axis is one of the unsigned letters

# --- sketch rules, added with the sketch foundation ------------------------
#
# A sketch is validated, never solved. A dimensional constraint that
# disagrees with the geometry it names is reported as a conflict rather than
# used to move anything -- solving would hide the disagreement and would
# invent geometry the backend cannot execute anyway.
P18 = "P18"  # ids are unique within the sketch
P19 = "P19"  # a constraint names geometry that exists in this sketch
P20 = "P20"  # the point handle is a real point of that geometry
P21 = "P21"  # the constraint type applies to that geometry type
P22 = "P22"  # a dimensional constraint agrees with its geometry

# --- profile-to-solid rules, added with extrude and revolve ----------------
#
# These are the mirror of P11. A modifier's target must be a solid; an
# extrude's or revolve's target must be a PROFILE, and the two mistakes are
# opposite, so they get different codes and different advice.
P23 = "P23"  # the target names a sketch, not a solid and not a modifier
P24 = "P24"  # an extrude's direction is normal to the sketch's plane
P25 = "P25"  # a revolve's axis lies in the sketch's plane
P26 = "P26"  # a revolve's angle is in (0, 360]

# --- pattern rules, added with the first graph-native operation ------------
#
# A pattern's `source` is the first reference in this language that names an
# **operation** rather than a body or a profile, so it needs its own
# category rule rather than reusing P11's or P23's. P9, P10 and P12 apply to
# it unchanged: the question "does this name something declared, earlier and
# still available" is the same question whatever the answer must BE, and
# `_resolve` asks it once for every role.
P27 = "P27"  # the source names a feature this stage can repeat
P28 = "P28"  # the count is a whole number within its bounds
P29 = "P29"  # the placement is coherent, and expressible for that source
P30 = "P30"  # a derived instance id does not collide with an operation id

# --- graph rules -----------------------------------------------------------
#
# Reported against the plan rather than one operation, because a cycle is a
# property of the graph and blaming any single member of it would be a
# guess. Unreachable through the parser today -- a cycle needs a reference
# that is not strictly earlier, which is P10 -- and checked anyway, because
# the validator must not depend on the parser having run and because P10 is
# a rule a later stage may want to relax.
P31 = "P31"  # the dependency graph is acyclic

RULE_CODES: Tuple[str, ...] = (
    P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14,
    P15, P16, P17, P18, P19, P20, P21, P22,
    P23, P24, P25, P26, P27, P28, P29, P30, P31,
)


@dataclass(frozen=True)
class PlanProblem:
    """One reason a plan is not usable."""

    code: str
    message: str
    where: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message, "where": self.where}


@dataclass(frozen=True)
class PlanValidation:
    """The verdict on one plan."""

    valid: bool
    problems: Tuple[PlanProblem, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "valid": self.valid,
            "problems": [problem.to_dict() for problem in self.problems],
        }


def validate_plan(plan: OperationPlan) -> PlanValidation:
    """Judge a parsed plan. Never raises; a bad plan is an ordinary result."""
    problems: List[PlanProblem] = []

    if plan.status is PlanStatus.GENERATED:
        if not plan.operations:
            problems.append(
                PlanProblem(P1, "a generated plan has no operations")
            )
    else:
        # A refusal has to say something. An empty refusal is unusable to a
        # caller and indistinguishable from a malfunction.
        if not (plan.reason or plan.questions or plan.summary):
            problems.append(
                PlanProblem(
                    P7,
                    f"a `{plan.status.value}` plan gives no reason",
                )
            )

    # The solid set of Section B.4, walked over the plan.
    #
    # The walk itself lives in `cad_experimental.history` and is shared with
    # the dependency graph, so there is exactly one answer to what "live",
    # "consumed" and "a profile" mean. It yields the state as it stood
    # BEFORE each operation, which is precisely what a reference must be
    # judged against.
    #
    # `declared` is every operation id with the index where it first
    # appeared -- used to tell "no such id" from "not yet". `live` is the
    # ids that actually NAME A SOLID: a modifier replaces its target in
    # place and the result keeps the target's id, so a modifier's own id
    # never enters the solid set. That single fact is what makes a
    # hole-targeting-a-hole plan invalid.
    seen: Dict[str, int] = {}
    for index, operation, state in walk(plan.operations):
        where = f"operations[{index}]"
        declared = state.declared
        live = state.solids
        consumed = state.consumed
        profiles = state.profiles

        if operation.id in seen:
            problems.append(
                PlanProblem(
                    P2,
                    (
                        f"duplicate operation id {operation.id!r}, first used "
                        f"at operations[{seen[operation.id]}]"
                    ),
                    where,
                )
            )
        else:
            seen[operation.id] = index

        kind = getattr(operation, "TYPE", None)
        if kind == BOX:
            # Spelled out rather than looped with getattr: nothing in this
            # package looks an attribute up by a computed name, so that a
            # test can assert the absence of the pattern outright.
            _positive(operation.x, f"{where}.x", problems)
            _positive(operation.y, f"{where}.y", problems)
            _positive(operation.z, f"{where}.z", problems)
        elif kind == CYLINDER:
            _positive(operation.diameter, f"{where}.diameter", problems)
            _positive(operation.height, f"{where}.height", problems)
            if operation.axis is not None and operation.axis not in AXES:
                problems.append(
                    PlanProblem(
                        P5,
                        f"{operation.axis!r} is not one of {', '.join(AXES)}",
                        f"{where}.axis",
                    )
                )
        elif kind == THROUGH_HOLE:
            _positive(operation.diameter, f"{where}.diameter", problems)
            if operation.axis is not None and operation.axis not in AXES:
                problems.append(
                    PlanProblem(
                        P5,
                        f"{operation.axis!r} is not one of {', '.join(AXES)}",
                        f"{where}.axis",
                    )
                )
            _reference(
                operation.target, f"{where}.target", operation.id, index,
                declared, live, consumed, problems, profiles,
            )
        elif kind == SUBTRACT:
            _subtract(
                operation, index, where, declared, live, consumed, problems,
                profiles,
            )
        elif kind == PATTERN:
            _pattern(
                operation, index, where, plan.operations,
                declared, live, consumed, profiles, problems,
            )
        elif kind == SKETCH:
            _sketch(operation, where, problems)
        elif kind in PROFILE_SOLID_TYPES:
            _profile_solid(
                operation, kind, index, where, plan.operations,
                declared, live, consumed, problems, profiles,
            )
        elif kind in EDGE_MODIFIER_TYPES:
            # One branch for both edge modifiers: they differ only in the
            # name of their length. S16/S17 are decidable from the document;
            # E4 and E5 are not, and are left to the engine.
            length_name = EDGE_MODIFIER_LENGTH[kind]
            length = (
                operation.radius if kind == FILLET else operation.distance
            )
            _positive(length, f"{where}.{length_name}", problems)
            _selector(operation.edges, f"{where}.edges", problems)
            _reference(
                operation.target, f"{where}.target", operation.id, index,
                declared, live, consumed, problems, profiles,
            )
        else:
            # Unreachable through the parser, which rejects unknown types.
            # Kept so a plan built in code cannot bypass the check.
            problems.append(
                PlanProblem(P3, f"unknown operation type {kind!r}", where)
            )

        # The solid set is advanced by the walk, not here: a second copy of
        # Section B.4 in this function is exactly how the validator and the
        # dependency graph would come to disagree about what a subtract
        # consumed.

        # Not every operation has a position -- a subtract is entirely
        # references, a pattern carries its own placement. Which types do is
        # read from PARAMETERS, the parser's own table, rather than from a
        # list of exceptions that every new operation type has to be added
        # to. Still an explicit attribute read, not a computed one: nothing
        # in this package looks an attribute up by a name built at runtime.
        position = operation.position if _has_position(kind) else None
        if position is not None:
            for name, value in (
                ("x", position.x),
                ("y", position.y),
                ("z", position.z),
            ):
                if not _finite(value):
                    problems.append(
                        PlanProblem(
                            P6,
                            "a position component must be a finite number",
                            f"{where}.position.{name}",
                        )
                    )

    _acyclic(plan, problems)
    return PlanValidation(valid=not problems, problems=tuple(problems))


def _has_position(kind: object) -> bool:
    """Whether an operation of this type carries a ``position`` parameter."""
    if kind not in PARAMETERS:
        return False
    required, optional = PARAMETERS[kind]
    return "position" in required or "position" in optional


def _acyclic(plan: OperationPlan, problems: List[PlanProblem]) -> None:
    """Report any cycle in the dependency graph, naming its members.

    The graph, not this module, decides what a cycle is: one topological
    order, one definition. Reported once per cyclic group with the group
    spelled out, because "there is a cycle" is not a diagnostic an agent can
    act on and "a, b and c form a cycle" is.
    """
    for group in feature_graph(plan).cycles():
        problems.append(
            PlanProblem(
                P31,
                "these operations depend on each other and cannot be "
                f"ordered: {', '.join(repr(name) for name in group)}",
                "operations",
            )
        )


def _pattern(
    operation: object,
    index: int,
    where: str,
    operations: Tuple[object, ...],
    declared: Dict[str, int],
    live: Dict[str, int],
    consumed: Dict[str, int],
    profiles: Dict[str, int],
    problems: List[PlanProblem],
) -> None:
    """Judge one pattern: its source, its count, and its placement.

    The source goes through :func:`_resolve` like every other reference --
    declared, strictly earlier, not consumed -- and then through the one
    check that is a pattern's own: it must name a **feature this stage can
    repeat**, which is a different question from P11's "is it a solid" and
    P23's "is it a profile".

    The placement is judged as *data plus expressibility*. Whether the
    instances collide, or whether a hole still meets material at the fourth
    position, is E1's and the kernel's, exactly as for a single hole. What is
    decided here is whether every instance could be WRITTEN at all: a radial
    pattern of a hole whose axis is not parallel to the pattern axis would
    need a hole pointing in a direction V1 has no word for, and no amount of
    geometry would rescue it.
    """
    source = _resolve(
        operation.source, f"{where}.source", operation.id, index,
        declared, consumed, problems,
    )

    _count(operation.count, f"{where}.count", problems)
    _instance_ids(operation, where, declared, problems)

    if source is None:
        return

    # What may stand at the far end of a `source` edge is the graph's table,
    # so the rule and the graph's own reachability answer cannot diverge.
    kind = getattr(operations[declared[source]], "TYPE", None)
    if kind not in REPEATABLE:
        described = (
            "a solid" if source in live
            else "a profile" if source in profiles
            else f"a {kind}"
        )
        problems.append(
            PlanProblem(
                P27,
                f"{source!r} is {described}, which this stage cannot repeat. "
                f"A pattern repeats a positional feature: "
                f"{', '.join(PATTERNABLE_TYPES)}",
                f"{where}.source",
            )
        )
        return

    _placement(operation, operations[declared[source]], where, problems)


def _count(value: object, path: str, problems: List[PlanProblem]) -> None:
    """A whole number of instances, including the source, within bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(
            PlanProblem(P28, "a count must be a whole number", path)
        )
        return
    if value < MIN_PATTERN_COUNT:
        problems.append(
            PlanProblem(
                P28,
                f"a count of {value} repeats nothing: the count includes the "
                f"source feature, so the minimum is {MIN_PATTERN_COUNT}",
                path,
            )
        )
    elif value > MAX_PATTERN_COUNT:
        problems.append(
            PlanProblem(
                P28,
                f"a count of {value} exceeds the maximum of "
                f"{MAX_PATTERN_COUNT}; a pattern expands into one feature per "
                "instance",
                path,
            )
        )


def _instance_ids(
    operation: object,
    where: str,
    declared: Dict[str, int],
    problems: List[PlanProblem],
) -> None:
    """The ids the adapter will derive must not already be taken.

    Checked here, against the same :func:`~cad_experimental.plan.instance_id`
    the adapter will use, so a collision is a plan problem with a path rather
    than an adapter failure halfway through a translation.
    """
    if not isinstance(operation.count, int) or isinstance(operation.count, bool):
        return
    for index in range(1, min(operation.count, MAX_PATTERN_COUNT + 1)):
        derived = instance_id(operation.id, index)
        if derived in declared:
            problems.append(
                PlanProblem(
                    P30,
                    f"this pattern's instance {index} would be named "
                    f"{derived!r}, which operations[{declared[derived]}] "
                    "already uses. Rename one of them",
                    f"{where}.id",
                )
            )


def _placement(
    operation: object,
    source: object,
    where: str,
    problems: List[PlanProblem],
) -> None:
    """Judge a placement as data, and against the feature it will move."""
    placement = operation.placement
    path = f"{where}.placement"

    if isinstance(placement, LinearPlacement):
        _positive(placement.spacing, f"{path}.spacing", problems)
        return

    if not isinstance(placement, RadialPlacement):
        # Unreachable through the parser, which rejects an unknown kind.
        problems.append(
            PlanProblem(P29, "unknown placement", path)
        )
        return

    if placement.angle is not None:
        _step(placement.angle, f"{path}.angle", problems)

    for name, value in (
        ("x", placement.centre.x),
        ("y", placement.centre.y),
        ("z", placement.centre.z),
    ):
        if not _finite(value):
            problems.append(
                PlanProblem(
                    P6,
                    "a position component must be a finite number",
                    f"{path}.centre.{name}",
                )
            )

    # Expressibility, not geometry. Turning a hole about an axis it is not
    # parallel to would tilt it, and V1 has only the six signed principal
    # directions -- there is no word for the result, so the instance could
    # not be written down at all.
    source_axis = getattr(source, "axis", None) or DEFAULT_AXIS
    if placement.axis in AXES and source_axis[1:] != placement.axis[1:]:
        problems.append(
            PlanProblem(
                P29,
                f"a radial pattern about {placement.axis!r} would turn "
                f"{operation.source!r}, whose axis is {source_axis!r}, onto a "
                "direction this language cannot name. Revolve a feature about "
                "its own axis, or use a linear pattern",
                f"{path}.axis",
            )
        )


def _step(value: object, path: str, problems: List[PlanProblem]) -> None:
    """The turn between consecutive radial instances: finite, in (0, 360]."""
    if not _finite(value):
        problems.append(
            PlanProblem(P29, "an angle must be a finite number", path)
        )
        return
    if value <= 0:
        problems.append(
            PlanProblem(
                P29,
                f"a step of {value} puts every instance on top of the source; "
                "it must be > 0. The axis sign chooses the direction",
                path,
            )
        )
    elif value > FULL_TURN:
        problems.append(
            PlanProblem(
                P29,
                f"a step of {value} is more than a full turn; the maximum is "
                f"{FULL_TURN}",
                path,
            )
        )


def _sketch(
    operation: object, where: str, problems: List[PlanProblem]
) -> None:
    """Judge one sketch: ids, references, handles, types and agreement.

    Declarative, not solved. A ``length`` that disagrees with the line it
    names is a **conflict** (P22), reported rather than resolved -- a solver
    would hide the disagreement, and would invent geometry the backend
    cannot execute anyway.
    """
    definition = getattr(operation, "definition", None)
    geometry = tuple(getattr(definition, "geometry", ()) or ())
    constraints = tuple(getattr(definition, "constraints", ()) or ())

    # P18: one namespace inside a sketch, so a constraint id cannot shadow
    # a piece of geometry.
    seen: Dict[str, str] = {}
    for item in geometry:
        if item.id in seen:
            problems.append(
                PlanProblem(
                    P18,
                    f"duplicate id {item.id!r} inside the sketch",
                    f"{where}.geometry",
                )
            )
        seen[item.id] = "geometry"
    for constraint in constraints:
        if constraint.id in seen:
            problems.append(
                PlanProblem(
                    P18,
                    f"duplicate id {constraint.id!r} inside the sketch; it "
                    f"is already used by {seen[constraint.id]}",
                    f"{where}.constraints",
                )
            )
        seen[constraint.id] = "a constraint"

    types = {item.id: item.TYPE for item in geometry}
    by_id = {item.id: item for item in geometry}

    for item in geometry:
        # Spelled out per type rather than looped over field *names*: every
        # attribute read in this package is a literal, so no string that came
        # out of a model is ever used to reach an attribute.
        if item.TYPE == CIRCLE:
            _positive(
                item.radius, f"{where}.geometry[{item.id}].radius", problems
            )
        elif item.TYPE == RECTANGLE:
            _positive(
                item.width, f"{where}.geometry[{item.id}].width", problems
            )
            _positive(
                item.height, f"{where}.geometry[{item.id}].height", problems
            )
        elif item.TYPE == LINE and item.start == item.end:
            problems.append(
                PlanProblem(
                    P4,
                    "a line's start and end are the same point, so it has "
                    "no length",
                    f"{where}.geometry[{item.id}]",
                )
            )

    for index, constraint in enumerate(constraints):
        path = f"{where}.constraints[{index}]"

        if constraint.value is not None:
            _positive(constraint.value, f"{path}.value", problems)

        references = (
            [handle.geometry for handle in constraint.points]
            if constraint.points
            else ([constraint.geometry] if constraint.geometry else [])
        )
        for reference in references:
            if reference not in types:
                known = ", ".join(sorted(types)) or "nothing"
                problems.append(
                    PlanProblem(
                        P19,
                        f"{reference!r} names no geometry in this sketch; "
                        f"declared: {known}",
                        path,
                    )
                )

        for position, handle in enumerate(constraint.points):
            if handle.geometry not in types:
                continue  # already reported as P19
            allowed = POINT_HANDLES[types[handle.geometry]]
            if handle.point not in allowed:
                problems.append(
                    PlanProblem(
                        P20,
                        f"{handle.point!r} is not a point of a "
                        f"{types[handle.geometry]}; it has "
                        f"{', '.join(allowed)}",
                        f"{path}.points[{position}]",
                    )
                )

        expected = CONSTRAINT_APPLIES_TO.get(constraint.type)
        if expected is not None and constraint.geometry in types:
            actual = types[constraint.geometry]
            if actual not in expected:
                problems.append(
                    PlanProblem(
                        P21,
                        f"a `{constraint.type}` constraint applies to "
                        f"{' or '.join(expected)}, but "
                        f"{constraint.geometry!r} is a {actual}",
                        path,
                    )
                )

        # P22: agreement. Checked, never solved.
        if (
            constraint.type in DIMENSIONAL_TYPES
            and constraint.value is not None
            and constraint.geometry in by_id
        ):
            item = by_id[constraint.geometry]
            if constraint.type == LENGTH and item.TYPE == LINE:
                actual_length = math.hypot(
                    item.end.x - item.start.x, item.end.y - item.start.y
                )
                # A tolerance, not equality: the length is computed from the
                # endpoints, so a constraint of 50 on a line from (0,0) to
                # (30,40) must agree to floating-point accuracy, not to the
                # bit.
                if not math.isclose(
                    actual_length, constraint.value, rel_tol=1e-9
                ):
                    problems.append(
                        PlanProblem(
                            P22,
                            f"the constraint says {constraint.value}, but "
                            f"{constraint.geometry!r} measures "
                            f"{actual_length}. Constraints are checked, not "
                            "solved, so this is a conflict to fix in the plan",
                            path,
                        )
                    )
            elif constraint.type == RADIUS and item.TYPE == CIRCLE:
                if not math.isclose(
                    item.radius, constraint.value, rel_tol=1e-9
                ):
                    problems.append(
                        PlanProblem(
                            P22,
                            f"the constraint says {constraint.value}, but "
                            f"{constraint.geometry!r} has radius "
                            f"{item.radius}. Constraints are checked, not "
                            "solved, so this is a conflict to fix in the plan",
                            path,
                        )
                    )


def _selector(
    selector: object, path: str, problems: List[PlanProblem]
) -> None:
    """Judge an edge selector as data. Nothing geometric happens here.

    The parser already refuses a malformed selector, so from a parsed plan
    this is redundant -- deliberately. A plan built in code must not be able
    to reach the adapter with a selector the contract does not define.
    """
    mode = getattr(selector, "select", None)
    axis = getattr(selector, "axis", None)

    if not isinstance(mode, str) or mode not in SELECT_MODES:
        problems.append(
            PlanProblem(
                P15,
                f"{mode!r} is not a selector; expected one of "
                f"{', '.join(SELECT_MODES)}",
                f"{path}.select",
            )
        )
        return

    if mode == SELECT_AXIS_PARALLEL:
        if axis is None:
            problems.append(
                PlanProblem(
                    P16,
                    f"`{SELECT_AXIS_PARALLEL}` requires an axis",
                    f"{path}.axis",
                )
            )
        elif axis not in SELECTOR_AXES:
            problems.append(
                PlanProblem(
                    P17,
                    f"{axis!r} is not an unsigned selector axis; expected one "
                    f"of {', '.join(SELECTOR_AXES)}",
                    f"{path}.axis",
                )
            )
    elif axis is not None:
        problems.append(
            PlanProblem(
                P16,
                f"`{mode}` selects every edge, so an axis means nothing",
                f"{path}.axis",
            )
        )


def _subtract(
    operation: object,
    index: int,
    where: str,
    declared: Dict[str, int],
    live: Dict[str, int],
    consumed: Dict[str, int],
    problems: List[PlanProblem],
    profiles: Dict[str, int],
) -> None:
    """Judge one subtract: its target, its tool list, and every tool.

    The tool list's *shape* (rule S14) is the parser's job, so by the time a
    ``SubtractOperation`` exists ``tools`` is a non-empty tuple of
    well-formed ids. What is left for here is S15 -- no self-subtraction, no
    repeats -- and the same four reference checks the target gets, once per
    tool.
    """
    target = getattr(operation, "target", None)
    owner_id = getattr(operation, "id", "")
    _reference(
        target, f"{where}.target", owner_id, index,
        declared, live, consumed, problems, profiles,
    )

    tools = tools_of(operation)
    if not tools:
        # Unreachable through the parser, which rejects an empty list.
        # Checked so a plan built in code cannot bypass S14.
        problems.append(
            PlanProblem(
                P13,
                "a subtract must remove at least one solid",
                f"{where}.tools",
            )
        )
        return

    seen: Dict[str, int] = {}
    for position, tool in enumerate(tools):
        path = f"{where}.tools[{position}]"

        if tool == target:
            problems.append(
                PlanProblem(
                    P14,
                    f"{tool!r} is this subtract's own target; a solid cannot "
                    "be subtracted from itself",
                    path,
                )
            )
            continue
        if tool in seen:
            problems.append(
                PlanProblem(
                    P14,
                    f"{tool!r} is already listed at tools[{seen[tool]}]; a "
                    "tool is consumed by its first use and cannot be reused",
                    path,
                )
            )
            continue
        seen[tool] = position

        _reference(
            tool, path, owner_id, index, declared, live, consumed, problems,
            profiles,
        )


def _resolve(
    reference: object,
    path: str,
    owner_id: str,
    index: int,
    declared: Dict[str, int],
    consumed: Dict[str, int],
    problems: List[PlanProblem],
) -> Optional[str]:
    """The checks every reference shares, whatever it must point AT.

    Returns the reference when it names a declared, strictly-earlier,
    unconsumed operation, and ``None`` when it has already been reported.
    Shared by :func:`_reference` (whose target must be a solid) and
    :func:`_profile_solid` (whose target must be a profile) so the two
    cannot come to disagree about what "earlier" means.

    Reported in order of specificity, and at most one problem per reference:
    telling a caller both "no such id" and "wrong category" about the same
    reference is noise, and the first true statement is the useful one.
    """
    if not isinstance(reference, str) or not reference:
        # The parser requires a target, so this is only reachable from a
        # plan built in code. Checked anyway: the validator must not depend
        # on the parser having run.
        problems.append(
            PlanProblem(P8, "a reference must name an earlier operation", path)
        )
        return None
    target = reference

    if target == owner_id:
        problems.append(
            PlanProblem(
                P10,
                f"{target!r} refers to itself; a reference must point to an "
                "earlier operation",
                path,
            )
        )
        return None

    if target not in declared:
        known = ", ".join(sorted(declared)) or "nothing"
        problems.append(
            PlanProblem(
                P9,
                f"{target!r} names no operation in the plan; declared: {known}",
                path,
            )
        )
        return None

    if declared[target] > index:
        problems.append(
            PlanProblem(
                P10,
                f"{target!r} appears later in the plan (at operations["
                f"{declared[target]}]); a reference must point strictly "
                "earlier, so forward references and cycles are impossible",
                path,
            )
        )
        return None

    if target in consumed:
        problems.append(
            PlanProblem(
                P12,
                f"{target!r} was already consumed at operations["
                f"{consumed[target]}] and is no longer available",
                path,
            )
        )
        return None

    return target


def _profile_solid(
    operation: object,
    kind: str,
    index: int,
    where: str,
    operations: Tuple[object, ...],
    declared: Dict[str, int],
    live: Dict[str, int],
    consumed: Dict[str, int],
    problems: List[PlanProblem],
    profiles: Dict[str, int],
) -> None:
    """Judge an extrude or a revolve: its reference, then its direction.

    Two things are decidable here and are checked:

    * the target is a **profile** (P23) -- the mirror of P11, and the only
      reference in the language that must point at a sketch;
    * the direction or axis is compatible with that sketch's plane (P24,
      P25), because a plane fixes its own normal and its own two in-plane
      axes. This is arithmetic on an enumerated pair of names, not geometry.

    One thing is **not** checked, and is a known gap rather than an omission:
    whether a revolved profile crosses its own axis. That produces
    self-intersecting material, and deciding it needs the resolved 2D
    geometry measured against the axis line -- the kernel's kind of
    judgement, like E1-E5. There is no engine for this operation, so nothing
    here guesses at it.
    """
    target = _resolve(
        operation.target, f"{where}.target", operation.id, index,
        declared, consumed, problems,
    )

    if kind == EXTRUDE:
        _positive(operation.distance, f"{where}.distance", problems)
    else:
        _angle(operation.angle, f"{where}.angle", problems)
        if operation.axis not in AXES:
            problems.append(
                PlanProblem(
                    P5,
                    f"{operation.axis!r} is not one of {', '.join(AXES)}",
                    f"{where}.axis",
                )
            )

    if target is None:
        return

    if target not in profiles:
        # Declared, earlier, not consumed -- and not a profile. Which one it
        # is changes the advice, so the message says which.
        described = (
            "a solid" if target in live
            else "a modifier, whose result keeps its own target's id"
        )
        article = "an" if kind == EXTRUDE else "a"
        problems.append(
            PlanProblem(
                P23,
                f"{article} {kind} acts on a profile, but {target!r} is "
                f"{described}. Target a sketch instead",
                f"{where}.target",
            )
        )
        return

    plane = operations[profiles[target]].definition.plane

    if kind == EXTRUDE:
        # Absent means the plane's positive normal, which trivially agrees,
        # so only an explicit direction can be wrong.
        if operation.direction is None:
            return
        axis = operation.direction[1:]
        if axis != PLANE_NORMAL[plane]:
            problems.append(
                PlanProblem(
                    P24,
                    f"{operation.direction!r} is not normal to the {plane} "
                    f"plane of {target!r}; an extrusion runs along "
                    f"+{PLANE_NORMAL[plane]} or -{PLANE_NORMAL[plane]}",
                    f"{where}.direction",
                )
            )
        return

    axis = operation.axis[1:] if operation.axis in AXES else operation.axis
    if axis not in PLANE_AXES[plane]:
        allowed = ", ".join(
            f"{sign}{name}"
            for name in PLANE_AXES[plane]
            for sign in ("+", "-")
        )
        problems.append(
            PlanProblem(
                P25,
                f"{operation.axis!r} does not lie in the {plane} plane of "
                f"{target!r}, so revolving about it sweeps nothing; expected "
                f"one of {allowed}",
                f"{where}.axis",
            )
        )


def _angle(value: object, path: str, problems: List[PlanProblem]) -> None:
    """A revolve's sweep: a finite number in (0, 360] degrees."""
    if not _finite(value):
        problems.append(
            PlanProblem(P26, "an angle must be a finite number", path)
        )
        return
    if value <= 0:
        problems.append(
            PlanProblem(
                P26, f"an angle of {value} sweeps nothing; it must be > 0", path
            )
        )
        return
    if value > FULL_TURN:
        problems.append(
            PlanProblem(
                P26,
                f"an angle of {value} is more than a full turn, so the sweep "
                f"would overlap material it already made; the maximum is "
                f"{FULL_TURN}",
                path,
            )
        )


def _reference(
    reference: object,
    path: str,
    owner_id: str,
    index: int,
    declared: Dict[str, int],
    live: Dict[str, int],
    consumed: Dict[str, int],
    problems: List[PlanProblem],
    profiles: Dict[str, int],
) -> None:
    """Judge one reference against the simulated solid set.

    Used for a modifier's ``target`` and for each of a subtract's ``tools``:
    the question is identical in both places, so the answer comes from one
    implementation rather than two that could drift.

    Reported in order of specificity, and at most one problem per reference:
    telling a caller both "no such id" and "not a body" about the same
    reference is noise, and the first true statement is the useful one.
    """
    target = _resolve(
        reference, path, owner_id, index, declared, consumed, problems
    )
    if target is None:
        return

    if target not in live:
        # Declared, earlier, not consumed -- so it names something that is
        # not a solid. Which one it is changes the advice, so the message
        # says which.
        if target in profiles:
            problems.append(
                PlanProblem(
                    P11,
                    f"{target!r} is a sketch, which declares a profile and "
                    "not a solid. A solid operation cannot act on a profile",
                    path,
                )
            )
        else:
            problems.append(
                PlanProblem(
                    P11,
                    f"{target!r} does not name a solid: it is a modifier, "
                    "whose result keeps its own target's id. Target the "
                    "constructive operation instead",
                    path,
                )
            )


def _positive(value: float, where: str, problems: List[PlanProblem]) -> None:
    if not _finite(value):
        problems.append(
            PlanProblem(P4, "a dimension must be a finite number", where)
        )
    elif value <= 0:
        problems.append(
            PlanProblem(P4, f"a dimension must be positive; got {value}", where)
        )


def _finite(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


__all__ = [
    "P31",
    "P30",
    "P29",
    "P28",
    "P27",
    "P1",
    "P10",
    "P11",
    "P12",
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "P8",
    "P9",
    "P13",
    "P14",
    "P15",
    "P16",
    "P17",
    "P18",
    "P19",
    "P20",
    "P21",
    "P22",
    "P23",
    "P24",
    "P25",
    "P26",
    "RULE_CODES",
    "PlanProblem",
    "PlanValidation",
    "validate_plan",
]
