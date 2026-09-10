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

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .plan import (
    AXES,
    BOX,
    CONSTRUCTIVE_TYPES,
    CONSUMING_TYPES,
    CYLINDER,
    FILLET,
    MODIFIER_TYPES,
    SELECT_AXIS_PARALLEL,
    SELECT_MODES,
    SELECTOR_AXES,
    SUBTRACT,
    THROUGH_HOLE,
    OperationPlan,
    PlanStatus,
    tools_of,
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

RULE_CODES: Tuple[str, ...] = (
    P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12, P13, P14,
    P15, P16, P17,
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

    # The solid set of Section B.4, simulated over the plan.
    #
    # `declared` is every operation id, with the index where it first
    # appeared -- used to tell "no such id" from "not yet". `live` is the
    # ids that actually NAME A SOLID, which is constructive ids only: a
    # modifier replaces its target in place and the result keeps the
    # target's id, so a modifier's own id never enters the solid set. That
    # single fact is what makes a hole-targeting-a-hole plan invalid.
    declared: Dict[str, int] = {}
    for index, operation in enumerate(plan.operations):
        declared.setdefault(operation.id, index)
    live: Dict[str, int] = {}
    consumed: Dict[str, int] = {}

    seen: Dict[str, int] = {}
    for index, operation in enumerate(plan.operations):
        where = f"operations[{index}]"

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
                declared, live, consumed, problems,
            )
        elif kind == SUBTRACT:
            _subtract(
                operation, index, where, declared, live, consumed, problems
            )
        elif kind == FILLET:
            # S16 is decidable from the document; E4 and E5 are not, and are
            # left to the engine.
            _positive(operation.radius, f"{where}.radius", problems)
            _selector(operation.edges, f"{where}.edges", problems)
            _reference(
                operation.target, f"{where}.target", operation.id, index,
                declared, live, consumed, problems,
            )
        else:
            # Unreachable through the parser, which rejects unknown types.
            # Kept so a plan built in code cannot bypass the check.
            problems.append(
                PlanProblem(P3, f"unknown operation type {kind!r}", where)
            )

        # Update the simulated solid set. A constructive operation adds a
        # solid named by its own id; a modifier adds nothing, because its
        # result keeps the target's id and the target is already live.
        if kind in CONSTRUCTIVE_TYPES:
            live.setdefault(operation.id, index)
        elif kind in CONSUMING_TYPES:
            # The history step, and the reason this stage exists. Every tool
            # this subtract legitimately used is gone from here on: a later
            # operation naming it gets P12, not a second chance. Only tools
            # that actually resolved are consumed -- consuming an unresolved
            # one would invent a second, misleading problem downstream.
            for tool in tools_of(operation):
                if tool in live and tool != operation.target:
                    live.pop(tool, None)
                    consumed[tool] = index

        # A subtract has no position: it is entirely references. Checked by
        # type rather than a defaulted `getattr`, so nothing in this package
        # looks an attribute up by a computed name.
        position = (
            None if kind in (SUBTRACT, FILLET) else operation.position
        )
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

    return PlanValidation(valid=not problems, problems=tuple(problems))


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
        declared, live, consumed, problems,
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
            tool, path, owner_id, index, declared, live, consumed, problems
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
) -> None:
    """Judge one reference against the simulated solid set.

    Used for a modifier's ``target`` and for each of a subtract's ``tools``:
    the question is identical in both places, so the answer comes from one
    implementation rather than two that could drift.

    Reported in order of specificity, and at most one problem per reference:
    telling a caller both "no such id" and "not a body" about the same
    reference is noise, and the first true statement is the useful one.
    """
    target = reference
    if not isinstance(target, str) or not target:
        # The parser requires a target, so this is only reachable from a
        # plan built in code. Checked anyway: the validator must not depend
        # on the parser having run.
        problems.append(
            PlanProblem(P8, "a reference must name a solid", path)
        )
        return

    if target == owner_id:
        problems.append(
            PlanProblem(
                P10,
                f"{target!r} refers to itself; a reference must point to an "
                "earlier operation",
                path,
            )
        )
        return

    if target not in declared:
        known = ", ".join(sorted(declared)) or "nothing"
        problems.append(
            PlanProblem(
                P9,
                f"{target!r} names no operation in the plan; declared: {known}",
                path,
            )
        )
        return

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
        return

    if target in consumed:
        problems.append(
            PlanProblem(
                P12,
                f"{target!r} was already consumed at operations["
                f"{consumed[target]}] and is no longer a solid",
                path,
            )
        )
        return

    if target not in live:
        # Declared, earlier, not consumed -- so it is a modifier's id. A
        # modifier's result keeps its TARGET's id, so its own id never names
        # a solid, and a hole cannot be drilled into a hole.
        problems.append(
            PlanProblem(
                P11,
                f"{target!r} does not name a solid: it is a modifier, whose "
                "result keeps its own target's id. Target the constructive "
                "operation instead",
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
    "RULE_CODES",
    "PlanProblem",
    "PlanValidation",
    "validate_plan",
]
