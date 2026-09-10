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

from .plan import AXES, BOX, CYLINDER, OperationPlan, PlanStatus

#: Plan rule codes. Prefixed ``P`` so they can never be mistaken in a log for
#: the specification's own ``S``/``E`` codes.
P1 = "P1"  # a generated plan has at least one operation
P2 = "P2"  # operation ids are unique
P3 = "P3"  # the operation type is implemented
P4 = "P4"  # dimensions are finite and strictly positive
P5 = "P5"  # the axis is one of the six signed principal directions
P6 = "P6"  # a position is three finite numbers
P7 = "P7"  # a non-generated plan explains itself

RULE_CODES: Tuple[str, ...] = (P1, P2, P3, P4, P5, P6, P7)


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
        else:
            # Unreachable through the parser, which rejects unknown types.
            # Kept so a plan built in code cannot bypass the check.
            problems.append(
                PlanProblem(P3, f"unknown operation type {kind!r}", where)
            )

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
    "P2",
    "P3",
    "P4",
    "P5",
    "P6",
    "P7",
    "RULE_CODES",
    "PlanProblem",
    "PlanValidation",
    "validate_plan",
]
