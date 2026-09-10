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
    CYLINDER,
    MODIFIER_TYPES,
    THROUGH_HOLE,
    OperationPlan,
    PlanStatus,
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
P12 = "P12"  # the target has not been consumed

RULE_CODES: Tuple[str, ...] = (
    P1, P2, P3, P4, P5, P6, P7, P8, P9, P10, P11, P12,
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
                operation, index, where, declared, live, consumed, problems
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

        position = operation.position
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


def _reference(
    operation: object,
    index: int,
    where: str,
    declared: Dict[str, int],
    live: Dict[str, int],
    consumed: Dict[str, int],
    problems: List[PlanProblem],
) -> None:
    """Judge one modifier's ``target`` against the simulated solid set.

    Reported in order of specificity, and at most one problem per target:
    telling a caller both "no such id" and "not a body" about the same
    reference is noise, and the first true statement is the useful one.
    """
    target = getattr(operation, "target", None)
    if not isinstance(target, str) or not target:
        # The parser requires a target, so this is only reachable from a
        # plan built in code. Checked anyway: the validator must not depend
        # on the parser having run.
        problems.append(
            PlanProblem(P8, "a modifier must name a `target`", f"{where}.target")
        )
        return

    if target == operation.id:
        problems.append(
            PlanProblem(
                P10,
                f"{target!r} refers to itself; a reference must point to an "
                "earlier operation",
                f"{where}.target",
            )
        )
        return

    if target not in declared:
        known = ", ".join(sorted(declared)) or "nothing"
        problems.append(
            PlanProblem(
                P9,
                f"{target!r} names no operation in the plan; declared: {known}",
                f"{where}.target",
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
                f"{where}.target",
            )
        )
        return

    if target in consumed:
        problems.append(
            PlanProblem(
                P12,
                f"{target!r} was already consumed at operations["
                f"{consumed[target]}] and is no longer a solid",
                f"{where}.target",
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
                f"{where}.target",
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
    "RULE_CODES",
    "PlanProblem",
    "PlanValidation",
    "validate_plan",
]
