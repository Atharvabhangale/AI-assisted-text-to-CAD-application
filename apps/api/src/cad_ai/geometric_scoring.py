"""Turn kernel measurements into a verdict, by rules written as data.

**Measurement only, and no kernel.** The numbers come from
:mod:`cad_core.shape_comparison`, run in the isolated worker; this module reads
those plain-data facts and applies a case's rule (from
:mod:`cad_ai.corpus_v2`) to them. Nothing here builds, imports CadQuery or
OpenCascade, or knows which case it is judging -- a rule is interpreted the
same way for every case, so there is no per-case code to tune.

Two rule kinds:

``exact``
    The candidate solid must reproduce the reference solid: IoU (after
    aligning bounding-box minimum corners, in the rule's orientation mode) at
    least ``iou_min``; relative volume error at most ``volume_tolerance``;
    the reference's topology signature (genus, full-turn bore and boss
    diameters, set of surface types) reproduced; with ``position: fixed``, the
    as-built minimum corner within ``placement_tolerance_mm``; plus any extra
    property checks the rule lists.
``properties``
    Only the listed property checks, on the candidate alone.

Every check reports what it expected and what it found, so a failed case
says *why* rather than just failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_core.shape_comparison import (
    ORIENTATION_FIXED,
    ORIENTATION_FREE,
    ORIENTATION_MODES,
    CylindricalSurface,
    PairResult,
    ShapeFacts,
)

#: Exact rules: the default minimum IoU. A fully stated part leaves the model
#: nothing to choose, so the solid should match to kernel precision; 0.99
#: leaves room for a stated value rounded the way a person would (19.63 ->
#: 19.6, 9.525 -> 9.5) and nothing like a wrong feature. Measured on the
#: perturbations: the smallest real error any of them makes is far below it.
DEFAULT_IOU_MIN = 0.99

#: Exact rules: the default relative volume tolerance.
DEFAULT_VOLUME_TOLERANCE = 0.01

#: Exact rules with ``position: fixed``: how far (mm, per axis) the as-built
#: minimum corner may sit from the reference's.
DEFAULT_PLACEMENT_TOLERANCE_MM = 0.5

#: Two bore or boss diameters agree within the larger of these.
DIAMETER_ABSOLUTE_TOLERANCE_MM = 0.05
DIAMETER_RELATIVE_TOLERANCE = 0.01

RULE_KINDS: Tuple[str, ...] = ("exact", "properties")
POSITION_MODES: Tuple[str, ...] = ("fixed", "free")

#: Extent selectors a check may name.
EXTENT_SELECTORS: Tuple[str, ...] = (
    "x", "y", "z", "largest", "middle", "smallest", "largest_xy", "smallest_xy",
)

#: Axis filters for bore and boss checks.
AXIS_FILTERS: Tuple[str, ...] = ("X", "Y", "Z", "horizontal")

#: Check names and the keys each accepts.
CHECK_KEYS: Mapping[str, Tuple[str, ...]] = {
    "extent": ("axis", "min", "max"),
    "extent_ratio": ("numerator", "denominator", "min", "max"),
    "volume": ("min", "max"),
    "fill_ratio": ("min", "max"),
    "genus": ("min", "max"),
    "bores": ("min_count", "max_count", "diameter_min", "diameter_max", "axis"),
    "bosses": ("min_count", "max_count", "diameter_min", "diameter_max", "axis"),
    "faces": ("type", "min_count", "max_count"),
}

#: Verdict statuses.
PASSED = "passed"
FAILED = "failed"
#: The candidate validated but the engine would not build it (an E-rule).
#: A model failure, counted as failed.
NOT_BUILT = "not_built"
#: The instrument could not measure (worker crash, timeout, a reference that
#: did not build). Not a model result: excluded from every quality rate.
INSTRUMENT_ERROR = "instrument_error"


class RuleError(ValueError):
    """A corpus rule is malformed. Raised at load time, before any model call."""


# --- rule checking -------------------------------------------------------------


def _range_ok(raw: Mapping[str, Any], low: str, high: str, where: str) -> None:
    for key in (low, high):
        value = raw.get(key)
        if value is not None and not isinstance(value, (int, float)):
            raise RuleError(f"{where}: {key} must be a number or null")
    a, b = raw.get(low), raw.get(high)
    if a is not None and b is not None and a > b:
        raise RuleError(f"{where}: {low} exceeds {high}")


def validate_check(check: Mapping[str, Any], where: str) -> None:
    name = check.get("check")
    if name not in CHECK_KEYS:
        raise RuleError(f"{where}: unknown check {name!r}")
    unknown = set(check) - {"check"} - set(CHECK_KEYS[name])
    if unknown:
        raise RuleError(f"{where}: check {name!r} has unknown keys {sorted(unknown)}")
    if name == "extent":
        if check.get("axis") not in EXTENT_SELECTORS:
            raise RuleError(f"{where}: extent axis must be one of {EXTENT_SELECTORS}")
        _range_ok(check, "min", "max", where)
    elif name == "extent_ratio":
        for key in ("numerator", "denominator"):
            if check.get(key) not in EXTENT_SELECTORS:
                raise RuleError(f"{where}: {key} must be one of {EXTENT_SELECTORS}")
        _range_ok(check, "min", "max", where)
    elif name in ("volume", "fill_ratio", "genus"):
        _range_ok(check, "min", "max", where)
    elif name in ("bores", "bosses"):
        if not isinstance(check.get("min_count"), int):
            raise RuleError(f"{where}: {name} needs an integer min_count")
        _range_ok(check, "min_count", "max_count", where)
        _range_ok(check, "diameter_min", "diameter_max", where)
        if check.get("axis") is not None and check["axis"] not in AXIS_FILTERS:
            raise RuleError(f"{where}: {name} axis must be one of {AXIS_FILTERS}")
    elif name == "faces":
        if not isinstance(check.get("type"), str):
            raise RuleError(f"{where}: faces needs a surface type")
        _range_ok(check, "min_count", "max_count", where)


def validate_rule(rule: Mapping[str, Any], where: str = "rule") -> None:
    """Refuse a malformed rule. Corpus loading calls this for every case."""
    if not isinstance(rule, Mapping):
        raise RuleError(f"{where}: a rule must be an object")
    kind = rule.get("kind")
    if kind not in RULE_KINDS:
        raise RuleError(f"{where}: unknown rule kind {kind!r}")
    if rule.get("orientation") not in ORIENTATION_MODES:
        raise RuleError(f"{where}: orientation must be one of {ORIENTATION_MODES}")
    if kind == "exact":
        allowed = {"kind", "orientation", "position", "iou_min", "volume_tolerance",
                   "placement_tolerance_mm", "signature", "checks"}
        if rule.get("position", "free") not in POSITION_MODES:
            raise RuleError(f"{where}: position must be one of {POSITION_MODES}")
        iou_min = rule.get("iou_min", DEFAULT_IOU_MIN)
        if not isinstance(iou_min, (int, float)) or not 0 < iou_min <= 1:
            raise RuleError(f"{where}: iou_min must be in (0, 1]")
        tolerance = rule.get("volume_tolerance", DEFAULT_VOLUME_TOLERANCE)
        if not isinstance(tolerance, (int, float)) or tolerance < 0:
            raise RuleError(f"{where}: volume_tolerance must be >= 0")
    else:
        allowed = {"kind", "orientation", "checks"}
        if not rule.get("checks"):
            raise RuleError(f"{where}: a properties rule needs at least one check")
    unknown = set(rule) - allowed
    if unknown:
        raise RuleError(f"{where}: unknown rule keys {sorted(unknown)}")
    for index, check in enumerate(rule.get("checks", ())):
        if not isinstance(check, Mapping):
            raise RuleError(f"{where}: check {index} is not an object")
        validate_check(check, f"{where} check {index}")


def needs_reference(rule: Mapping[str, Any]) -> bool:
    return rule.get("kind") == "exact"


def orientations_for(rule: Mapping[str, Any]) -> Tuple[str, ...]:
    """The orientation modes to measure: the scored one, plus fixed and free
    as diagnostics, so a convention miss can be told from a wrong shape."""
    if not needs_reference(rule):
        return ()
    wanted = [rule["orientation"], ORIENTATION_FIXED, ORIENTATION_FREE]
    seen: List[str] = []
    for mode in wanted:
        if mode not in seen:
            seen.append(mode)
    return tuple(seen)


# --- verdicts ------------------------------------------------------------------


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    expected: str
    actual: Any

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "expected": self.expected, "actual": self.actual}


@dataclass(frozen=True)
class GeometryVerdict:
    """What the geometric rule concluded about one candidate."""

    status: str
    rule_kind: str
    orientation: str
    checks: Tuple[CheckOutcome, ...] = ()
    #: IoU in the rule's orientation mode (exact rules only).
    iou: Optional[float] = None
    iou_fixed: Optional[float] = None
    iou_free: Optional[float] = None
    volume_error: Optional[float] = None
    message: Optional[str] = None
    #: A summary of the candidate's measured facts, for diagnosis.
    candidate: Optional[Mapping[str, Any]] = None

    @property
    def passed(self) -> bool:
        return self.status == PASSED

    @property
    def measured(self) -> bool:
        """False only when the instrument, not the model, failed."""
        return self.status != INSTRUMENT_ERROR

    def failed_checks(self) -> Tuple[CheckOutcome, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "rule_kind": self.rule_kind,
            "orientation": self.orientation,
            "iou": self.iou,
            "iou_fixed": self.iou_fixed,
            "iou_free": self.iou_free,
            "volume_error": self.volume_error,
            "message": self.message,
            "checks": [check.to_dict() for check in self.checks],
            "candidate": dict(self.candidate) if self.candidate is not None else None,
        }


def _summary(facts: ShapeFacts) -> Dict[str, Any]:
    return {
        "volume": round(facts.volume, 6),
        "extents": [round(v, 6) for v in facts.extents],
        "minimum": [round(v, 6) for v in facts.minimum],
        "fill_ratio": round(facts.fill_ratio, 6),
        "genus": facts.genus,
        "solid_count": facts.solid_count,
        "bores": sorted(round(c.diameter, 4) for c in facts.bores()),
        "bosses": sorted(round(c.diameter, 4) for c in facts.bosses()),
        "face_types": dict(facts.face_types),
    }


def _extent(facts: ShapeFacts, selector: str) -> float:
    x, y, z = facts.extents
    ordered = sorted((x, y, z))
    return {
        "x": x, "y": y, "z": z,
        "largest": ordered[2], "middle": ordered[1], "smallest": ordered[0],
        "largest_xy": max(x, y), "smallest_xy": min(x, y),
    }[selector]


def _within(value: Optional[float], low: Optional[float], high: Optional[float]) -> bool:
    if value is None:
        return False
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


def _range_text(low: Any, high: Any) -> str:
    if low is not None and high is not None:
        return f"{low} .. {high}"
    if low is not None:
        return f">= {low}"
    if high is not None:
        return f"<= {high}"
    return "any"


def _axis_matches(surface: CylindricalSurface, wanted: Optional[str]) -> bool:
    if wanted is None:
        return True
    if wanted == "horizontal":
        return surface.axis in ("X", "Y")
    return surface.axis == wanted


def evaluate_check(check: Mapping[str, Any], facts: ShapeFacts) -> CheckOutcome:
    """Apply one property check to measured facts."""
    name = check["check"]
    if name == "extent":
        value = _extent(facts, check["axis"])
        return CheckOutcome(f"extent {check['axis']}", _within(value, check.get("min"), check.get("max")),
                            _range_text(check.get("min"), check.get("max")) + " mm", round(value, 4))
    if name == "extent_ratio":
        top = _extent(facts, check["numerator"])
        bottom = _extent(facts, check["denominator"])
        value = top / bottom if bottom > 0 else None
        return CheckOutcome(f"{check['numerator']} / {check['denominator']}",
                            _within(value, check.get("min"), check.get("max")),
                            _range_text(check.get("min"), check.get("max")),
                            None if value is None else round(value, 4))
    if name == "volume":
        return CheckOutcome("volume", _within(facts.volume, check.get("min"), check.get("max")),
                            _range_text(check.get("min"), check.get("max")) + " mm^3", round(facts.volume, 3))
    if name == "fill_ratio":
        return CheckOutcome("fill ratio", _within(facts.fill_ratio, check.get("min"), check.get("max")),
                            _range_text(check.get("min"), check.get("max")), round(facts.fill_ratio, 4))
    if name == "genus":
        return CheckOutcome("genus (through-holes)",
                            _within(facts.genus, check.get("min"), check.get("max")),
                            _range_text(check.get("min"), check.get("max")), facts.genus)
    if name in ("bores", "bosses"):
        surfaces = facts.bores() if name == "bores" else facts.bosses()
        matching = [
            s for s in surfaces
            if _within(s.diameter, check.get("diameter_min"), check.get("diameter_max"))
            and _axis_matches(s, check.get("axis"))
        ]
        label = f"{name} of {_range_text(check.get('diameter_min'), check.get('diameter_max'))} mm"
        if check.get("axis"):
            label += f" along {check['axis']}"
        return CheckOutcome(label, _within(len(matching), check.get("min_count"), check.get("max_count")),
                            "count " + _range_text(check.get("min_count"), check.get("max_count")),
                            len(matching))
    if name == "faces":
        count = int(facts.face_types.get(check["type"], 0))
        return CheckOutcome(f"{check['type']} faces",
                            _within(count, check.get("min_count"), check.get("max_count")),
                            "count " + _range_text(check.get("min_count"), check.get("max_count")), count)
    raise RuleError(f"unknown check {name!r}")  # pragma: no cover - validated at load


def _diameters_match(expected: Sequence[float], actual: Sequence[float]) -> bool:
    if len(expected) != len(actual):
        return False
    for a, b in zip(sorted(expected), sorted(actual)):
        tolerance = max(DIAMETER_ABSOLUTE_TOLERANCE_MM, DIAMETER_RELATIVE_TOLERANCE * a)
        if abs(a - b) > tolerance:
            return False
    return True


def _signature(candidate: ShapeFacts, reference: ShapeFacts) -> List[CheckOutcome]:
    outcomes: List[CheckOutcome] = []
    if reference.genus is not None:
        outcomes.append(CheckOutcome("genus (through-holes)", candidate.genus == reference.genus,
                                     str(reference.genus), candidate.genus))
    for name, pick in (("bores", ShapeFacts.bores), ("bosses", ShapeFacts.bosses)):
        want = sorted(round(s.diameter, 4) for s in pick(reference))
        got = sorted(round(s.diameter, 4) for s in pick(candidate))
        outcomes.append(CheckOutcome(f"{name} (full-turn diameters)", _diameters_match(want, got),
                                     str(want), got))
    want_types = sorted(reference.face_types)
    got_types = sorted(candidate.face_types)
    outcomes.append(CheckOutcome("surface types", want_types == got_types, str(want_types), got_types))
    return outcomes


def judge(rule: Mapping[str, Any], result: PairResult) -> GeometryVerdict:
    """Apply a rule to one measured pair. Pure: no kernel, no I/O."""
    kind = str(rule["kind"])
    orientation = str(rule["orientation"])
    if result.failure is not None and result.failure.side == "candidate":
        return GeometryVerdict(
            status=NOT_BUILT, rule_kind=kind, orientation=orientation,
            message=f"the candidate did not build ({result.failure.stage}): {result.failure.message}",
        )
    if result.failure is not None or result.candidate is None:
        message = result.failure.message if result.failure is not None else "no measurement"
        return GeometryVerdict(status=INSTRUMENT_ERROR, rule_kind=kind, orientation=orientation,
                               message=f"the instrument could not measure: {message}")
    facts = result.candidate
    checks: List[CheckOutcome] = [CheckOutcome("solid count", facts.solid_count == 1, "1", facts.solid_count)]
    iou = iou_fixed = iou_free = volume_error = None

    if kind == "exact":
        comparison = result.comparison
        if comparison is None:
            return GeometryVerdict(status=INSTRUMENT_ERROR, rule_kind=kind, orientation=orientation,
                                   message="an exact rule needs a comparison, and none was made",
                                   candidate=_summary(facts))
        iou = comparison.iou(orientation)
        iou_fixed = comparison.iou(ORIENTATION_FIXED)
        iou_free = comparison.iou(ORIENTATION_FREE)
        volume_error = comparison.volume_error
        iou_min = float(rule.get("iou_min", DEFAULT_IOU_MIN))
        tolerance = float(rule.get("volume_tolerance", DEFAULT_VOLUME_TOLERANCE))
        checks.append(CheckOutcome(f"IoU ({orientation})", iou is not None and iou >= iou_min,
                                   f">= {iou_min}", None if iou is None else round(iou, 6)))
        checks.append(CheckOutcome("volume error", volume_error <= tolerance,
                                   f"<= {tolerance:.2%}", f"{volume_error:.4%}"))
        if rule.get("position", "free") == "fixed":
            limit = float(rule.get("placement_tolerance_mm", DEFAULT_PLACEMENT_TOLERANCE_MM))
            offset = max(abs(v) for v in comparison.placement_offset)
            checks.append(CheckOutcome("placement (minimum corner)", offset <= limit,
                                       f"within {limit} mm of {list(comparison.reference.minimum)}",
                                       [round(v, 4) for v in facts.minimum]))
        if rule.get("signature", True):
            checks.extend(_signature(facts, comparison.reference))
    for check in rule.get("checks", ()):
        checks.append(evaluate_check(check, facts))

    passed = all(check.passed for check in checks)
    return GeometryVerdict(
        status=PASSED if passed else FAILED,
        rule_kind=kind,
        orientation=orientation,
        checks=tuple(checks),
        iou=iou,
        iou_fixed=iou_fixed,
        iou_free=iou_free,
        volume_error=volume_error,
        candidate=_summary(facts),
    )


def instrument_failure(rule: Mapping[str, Any], message: str) -> GeometryVerdict:
    """A verdict for a pair the instrument could not measure at all."""
    return GeometryVerdict(status=INSTRUMENT_ERROR, rule_kind=str(rule.get("kind")),
                           orientation=str(rule.get("orientation")), message=message)


__all__ = [
    "AXIS_FILTERS",
    "CHECK_KEYS",
    "DEFAULT_IOU_MIN",
    "DEFAULT_PLACEMENT_TOLERANCE_MM",
    "DEFAULT_VOLUME_TOLERANCE",
    "DIAMETER_ABSOLUTE_TOLERANCE_MM",
    "DIAMETER_RELATIVE_TOLERANCE",
    "EXTENT_SELECTORS",
    "FAILED",
    "INSTRUMENT_ERROR",
    "NOT_BUILT",
    "PASSED",
    "RULE_KINDS",
    "CheckOutcome",
    "GeometryVerdict",
    "RuleError",
    "evaluate_check",
    "instrument_failure",
    "judge",
    "needs_reference",
    "orientations_for",
    "validate_check",
    "validate_rule",
]
