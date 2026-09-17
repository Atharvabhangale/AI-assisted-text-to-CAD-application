"""Answers about the part that is currently built.

Two kinds of answer, kept apart on purpose:

**MEASURED** -- a number the CAD engine produced for the build that actually
succeeded. Volume, bounding box, face and edge counts.

**CALCULATED** -- arithmetic this module did on measured values and on the
plan's own parameters, with the working shown. A calculated number is never
presented as something the engine measured.

Nothing here asks a model what a part is. If a question cannot be answered
from evidence and arithmetic, it returns nothing and the caller falls back to
the ordinary path -- which is the safe direction, because the cost of
returning nothing is one model call and the cost of guessing is a fabricated
engineering number.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

MEASURED = "measured"
CALCULATED = "calculated"


@dataclass(frozen=True)
class Finding:
    """One answer, and where its number came from."""

    label: str
    value: str
    kind: str
    working: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"label": self.label, "value": self.value, "kind": self.kind,
                "working": self.working}


def _operations(plan: Mapping[str, Any], kind: str) -> List[Mapping[str, Any]]:
    return [o for o in (plan.get("operations") or []) if o.get("type") == kind]


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def overview(plan: Mapping[str, Any],
             measurement: Mapping[str, Any]) -> List[Finding]:
    """What the engine measured, stated as measured."""
    found: List[Finding] = []
    size = measurement.get("size") or []
    if len(size) == 3 and all(isinstance(v, (int, float)) for v in size):
        found.append(Finding("Overall dimensions",
                             f"{size[0]:g} x {size[1]:g} x {size[2]:g} mm",
                             MEASURED))
    volume = _number(measurement.get("volume"))
    if volume is not None:
        found.append(Finding("Volume", f"{volume:.3f} mm3", MEASURED))
    for key, label in (("face_count", "Faces"), ("edge_count", "Edges"),
                       ("solid_count", "Solids")):
        value = measurement.get(key)
        if isinstance(value, int):
            found.append(Finding(label, str(value), MEASURED))
    return found


def material_removed(plan: Mapping[str, Any],
                     measurement: Mapping[str, Any]) -> List[Finding]:
    """How much material the holes take out, with the arithmetic shown.

    Calculated from the plan's own diameters and the measured thickness --
    the engine measures the finished solid, not what was removed, so this is
    arithmetic and is labelled as such. It assumes each hole passes through
    the full measured height and that holes do not overlap; both are stated.
    """
    holes = _operations(plan, "through_hole")
    size = measurement.get("size") or []
    if not holes or len(size) != 3:
        return []
    height = _number(size[2])
    if height is None:
        return []

    total = 0.0
    terms: List[str] = []
    for hole in holes:
        diameter = _number((hole.get("parameters") or {}).get("diameter"))
        if diameter is None:
            continue
        volume = math.pi * (diameter / 2.0) ** 2 * height
        total += volume
        terms.append(f"pi x ({diameter:g}/2)^2 x {height:g} = {volume:.3f}")
    if not terms:
        return []

    found = [Finding(
        "Material removed by holes", f"{total:.3f} mm3", CALCULATED,
        working=" ; ".join(terms) +
        f"  (total {total:.3f} mm3 over {len(terms)} hole(s); assumes each "
        f"hole passes through the full {height:g} mm and none overlap)")]

    solid = _number(measurement.get("volume"))
    if solid is not None and solid > 0:
        stock = solid + total
        found.append(Finding(
            "Removed as a fraction of stock", f"{100.0 * total / stock:.2f} %",
            CALCULATED,
            working=f"{total:.3f} / ({solid:.3f} + {total:.3f})"))
    return found


def feature_facts(plan: Mapping[str, Any]) -> List[Finding]:
    """Diameters, radii and distances, read straight off the plan.

    The plan is the authoritative description of the part, so a parameter
    read from it is a fact about the part rather than a calculation -- but it
    is the plan's number, not something the engine measured on the solid, so
    it is reported as calculated to keep the distinction honest.
    """
    found: List[Finding] = []
    for hole in _operations(plan, "through_hole"):
        diameter = _number((hole.get("parameters") or {}).get("diameter"))
        if diameter is not None:
            found.append(Finding(f"Hole {hole.get('id')} diameter",
                                 f"{diameter:g} mm", CALCULATED,
                                 working="from the operation plan"))
    for fillet in _operations(plan, "fillet"):
        radius = _number((fillet.get("parameters") or {}).get("radius"))
        if radius is not None:
            found.append(Finding(f"Fillet {fillet.get('id')} radius",
                                 f"R{radius:g}", CALCULATED,
                                 working="from the operation plan"))
    for chamfer in _operations(plan, "chamfer"):
        distance = _number((chamfer.get("parameters") or {}).get("distance"))
        if distance is not None:
            found.append(Finding(f"Chamfer {chamfer.get('id')} distance",
                                 f"{distance:g} mm", CALCULATED,
                                 working="from the operation plan"))
    return found


def fillet_vs_thickness(plan: Mapping[str, Any],
                        measurement: Mapping[str, Any]) -> List[Finding]:
    """Whether a fillet radius exceeds half the measured thickness.

    A comparison, not a rule: it reports the numbers and the verdict, and
    does not claim the part is wrong. Which radius is acceptable depends on
    material, load and process, none of which this system knows.
    """
    fillets = _operations(plan, "fillet")
    size = measurement.get("size") or []
    if not fillets or len(size) != 3:
        return []
    thickness = _number(size[2])
    if thickness is None:
        return []
    found: List[Finding] = []
    for fillet in fillets:
        radius = _number((fillet.get("parameters") or {}).get("radius"))
        if radius is None:
            continue
        half = thickness / 2.0
        verdict = "larger than" if radius > half else (
            "equal to" if abs(radius - half) < 1e-9 else "smaller than")
        found.append(Finding(
            f"Fillet {fillet.get('id')} vs half thickness",
            f"R{radius:g} is {verdict} {half:g} mm", CALCULATED,
            working=f"half of the measured {thickness:g} mm height = {half:g} mm"))
    return found


#: Question shapes this module can answer, each mapped to the analyses that
#: answer it. A question that matches nothing returns nothing.
_TOPICS: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("volume", "how big", "dimension", "dimensions", "size", "overall",
      "bounding", "thick", "thickness", "wide", "width", "long", "length",
      "faces", "edges", "measure", "measurement", "measurements"),
     ("overview",)),
    (("removed", "material removed", "waste", "how much material",
      "removed by"), ("removed",)),
    (("diameter", "radius", "hole size", "fillet", "chamfer"),
     ("features",)),
    (("half", "larger than", "bigger than", "compare", "ratio",
      "relative to"), ("fillet_check", "features")),
)


def analyse(plan: Mapping[str, Any], measurement: Mapping[str, Any],
            request: str) -> Optional[Dict[str, Any]]:
    """Answer an engineering question from evidence, or return ``None``.

    ``None`` means "not answerable here" and is the safe outcome: the caller
    then takes the ordinary path rather than receiving an invented number.
    """
    text = (request or "").lower()
    wanted: List[str] = []
    for words, analyses in _TOPICS:
        if any(word in text for word in words):
            wanted.extend(a for a in analyses if a not in wanted)
    if not wanted:
        return None

    findings: List[Finding] = []
    for name in wanted:
        if name == "overview":
            findings.extend(overview(plan, measurement))
        elif name == "removed":
            findings.extend(material_removed(plan, measurement))
        elif name == "features":
            findings.extend(feature_facts(plan))
        elif name == "fillet_check":
            findings.extend(fillet_vs_thickness(plan, measurement))
    if not findings:
        return None

    return {
        "findings": [f.to_dict() for f in findings],
        "measured": [f.to_dict() for f in findings if f.kind == MEASURED],
        "calculated": [f.to_dict() for f in findings if f.kind == CALCULATED],
    }


def report(plan: Mapping[str, Any],
           measurement: Mapping[str, Any]) -> Dict[str, Any]:
    """Everything this module can say about the current part."""
    findings = (overview(plan, measurement) + feature_facts(plan)
                + material_removed(plan, measurement)
                + fillet_vs_thickness(plan, measurement))
    return {
        "findings": [f.to_dict() for f in findings],
        "measured_count": sum(1 for f in findings if f.kind == MEASURED),
        "calculated_count": sum(1 for f in findings if f.kind == CALCULATED),
    }


def as_text(payload: Mapping[str, Any]) -> str:
    """The findings as a sentence or two, for the copilot to say."""
    lines: List[str] = []
    for finding in payload.get("findings", []):
        suffix = "" if finding["kind"] == MEASURED else " (calculated)"
        lines.append(f"{finding['label']}: {finding['value']}{suffix}")
    return " ".join(lines)


__all__ = ["CALCULATED", "MEASURED", "Finding", "analyse", "as_text",
           "feature_facts", "fillet_vs_thickness", "material_removed",
           "overview", "report"]
