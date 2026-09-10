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
    CYLINDER,
    ID_PATTERN,
    MODIFIER_TYPES,
    OPERATION_FIELDS,
    OPERATION_TYPES,
    PARAMETERS,
    THROUGH_HOLE,
    BoxOperation,
    CylinderOperation,
    Operation,
    OperationPlan,
    PlanStatus,
    Point,
    ThroughHoleOperation,
)

#: The keys a plan object may carry. Anything else is a rejection.
PLAN_KEYS = frozenset(
    {"status", "operations", "summary", "reason", "questions"}
)

#: The keys any operation may carry. Which of them a *particular* type may
#: carry is narrower -- see :data:`~cad_experimental.plan.OPERATION_FIELDS`.
#: ``target`` on a box is rejected as an unknown field for that type.
OPERATION_KEYS = frozenset({"id", "type", "target", "parameters"})

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

    for required in ("id", "type", "parameters"):
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
    if kind in MODIFIER_TYPES:
        if "target" not in mapping:
            raise PlanParseError(
                f"{where} is missing `target`",
                detail=f"a {kind} acts on an existing solid and must name it",
            )
        target = _identifier(mapping["target"], f"{where} target")

    parameters = _object(mapping["parameters"], f"{where} parameters")
    required_names, optional_names = PARAMETERS[kind]
    allowed = frozenset(required_names) | frozenset(optional_names)
    _reject_unknown(parameters, allowed, f"{where} parameters")
    for name in required_names:
        if name not in parameters:
            raise PlanParseError(f"{where} is missing parameter `{name}`")

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
