"""Stage 78: grade the axis-versus-extent criterion, on top of Stage 77's.

Stage 77 already decides whether a turn is a strict success. It catches the
axis-versus-extent defect *indirectly*: a bore at the pin's outer edge cuts a
different amount of material, so the volume is wrong, so the turn fails. That
is enough to SCORE, but not enough to MEASURE -- it cannot say whether a
failure was this mechanism or some other wrong number, and it files the
eleven ED-02 failures under `D:wrong_target` and `G:wrong_dimension`, neither
of which names what happened.

This module adds the direct measurement. It reads the coordinate the model
actually wrote and names which landmark of the body it sits on: the axis, the
minimum extent, the maximum extent, the centre, the origin, or none of them.

The layering, and why it can only ever be stricter
---------------------------------------------------
    stage78_strict  ==  stage77_strict  AND  axis_ok

A Stage 78 success is a Stage 77 success that also satisfies the axis
criterion, so a case can never score HIGHER here than it did in Stage 77. An
arm that "improved" a case by breaking something Stage 77 checks would be
caught, not rewarded. `stage77_strict` is read from the recorded verdict; it
is never recomputed here and never overridden.

Truth never comes from the observation
---------------------------------------
Every landmark handed to `name_coordinate` comes from `ground_truth78`, which
takes a case NAME. This module never derives an expected coordinate from the
plan it is grading, and a mutation that made it do so is in the sweep.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import ground_truth78 as G78

#: The language's own default, stated in the prompt's `## through_hole`
#: and `## cylinder` sections: an omitted `axis` means +Z.
DEFAULT_AXIS: str = "+Z"

# ------------------------------------------------------------- reading a plan


def operations_of(raw_text: Optional[str]) -> List[Dict[str, Any]]:
    """The operations the model wrote, or `[]` if it wrote nothing usable.

    Deliberately tolerant of a bad payload and deliberately NOT tolerant of a
    markdown fence: neither parser in this project strips fences, and Stage
    77 measured 0/240 fenced, so a fence here would be a real change worth
    failing on rather than absorbing.
    """
    if not raw_text:
        return []
    try:
        payload = json.loads(raw_text)
    except (ValueError, TypeError):
        return []
    operations = payload.get("operations")
    return list(operations) if isinstance(operations, list) else []


def _position(operation: Mapping[str, Any]) -> Dict[str, float]:
    parameters = operation.get("parameters") or {}
    position = parameters.get("position") or {}
    return {k: float(v) for k, v in position.items()
            if isinstance(v, (int, float))}


# --------------------------------------------------------------- ED-02

def observe_ed02(raw_text: Optional[str]) -> Dict[str, Any]:
    """Where did the bore's centreline go, and what landmark is that?

    Pure observation: it records what the model wrote and names it against
    landmarks supplied by the truth. It reaches no verdict.
    """
    truth = G78.axis_truth("ED-02")
    landmarks = truth["body_landmarks"]
    operations = operations_of(raw_text)

    bore = next((o for o in operations
                 if o.get("type") == "through_hole"), None)
    cylinder = next((o for o in operations
                     if o.get("type") == "cylinder"), None)

    seen: Dict[str, Any] = {
        "case": "ED-02",
        "found_a_bore": bore is not None,
        "bore_target": (bore or {}).get("target"),
        #: The `axis` field is OPTIONAL and the prompt states its default:
        #: "Omit it when the description does not say; the default is +Z."
        #: An omitted axis is therefore a +Z bore and is CORRECT, not a
        #: wrong direction. Reading `None` as a failure scored two perfect
        #: ED-02 answers -- bored at x=100, y=20, the pin's own axis -- as
        #: `X5:wrong_direction`. That is a defect in this file, not in the
        #: model. Both the written value and the resolved one are kept, so
        #: a reader can see which the model actually put on the wire.
        "bore_axis_written": ((bore or {}).get("parameters") or {}).get("axis"),
        "bore_axis": (((bore or {}).get("parameters") or {}).get("axis")
                      or (DEFAULT_AXIS if bore is not None else None)),
        "bore_diameter": ((bore or {}).get("parameters") or {}).get("diameter"),
        #: What the model re-stated the cylinder's own placement as. Recorded
        #: because an arm could in principle "fix" the bore by MOVING THE
        #: BODY, which would satisfy a naive centreline check while producing
        #: a different part. `grade_ed02` requires the body to stay put.
        "cylinder_position": _position(cylinder) if cylinder else None,
    }

    for axis_letter in ("x", "y"):
        marks = landmarks[axis_letter]
        observed = _position(bore).get(axis_letter) if bore else None
        seen[f"bore_{axis_letter}"] = observed
        seen[f"bore_{axis_letter}_is"] = G78.name_coordinate(
            observed,
            axis=marks["axis"], minimum=marks["minimum"],
            maximum=marks["maximum"], centre=marks["centre"],
            tolerance=truth["coordinate_tolerance"],
        )
    return seen


def grade_ed02(seen: Mapping[str, Any]) -> Dict[str, Any]:
    """Is the bore on the pin's axis, in both axes across the bore?"""
    truth = G78.axis_truth("ED-02")
    tolerance = truth["coordinate_tolerance"]
    placement = truth["body_placement"]

    checks: Dict[str, bool] = {
        "bore_present": bool(seen["found_a_bore"]),
        "bore_targets_the_pin": seen["bore_target"] == truth["feature_body"],
        "bore_runs_along_the_body_axis":
            seen["bore_axis"] == truth["feature_axis_direction"],
        "bore_x_on_axis": seen["bore_x_is"] == G78.AT_AXIS,
        "bore_y_on_axis": seen["bore_y_is"] == G78.AT_AXIS,
    }

    # The body must not have been MOVED to make the bore look centred. A
    # cylinder re-stated at a different position is a different part, and a
    # centreline check that ignored this could be satisfied by relocating
    # the pin onto the old bore instead of moving the bore onto the axis.
    position = seen.get("cylinder_position")
    if position:
        checks["body_did_not_move"] = all(
            abs(position.get(k, placement[k]) - placement[k]) <= tolerance
            for k in ("x", "y"))

    return {
        "case": "ED-02",
        "checks": checks,
        "axis_ok": all(checks.values()),
    }


# --------------------------------------------------------------- CR-06

def observe_cr06(raw_text: Optional[str]) -> Dict[str, Any]:
    """Does any cylinder's axis sit on a neighbour's boundary?

    CR-06 states no absolute placement, so there is no coordinate to compare
    against. What the request DOES entail is that the bodies stand apart, and
    the recorded defect is specifically an axis placed ON a neighbour's face.
    This measures that relation and nothing finer.
    """
    truth = G78.axis_truth("CR-06")
    tolerance = truth["coordinate_tolerance"]
    operations = operations_of(raw_text)

    boxes, cylinders = [], []
    for operation in operations:
        kind = operation.get("type")
        parameters = operation.get("parameters") or {}
        position = _position(operation)
        if kind == "box":
            origin = (position.get("x", 0.0), position.get("y", 0.0),
                      position.get("z", 0.0))
            boxes.append({
                "id": operation.get("id"),
                "min": origin,
                "max": (origin[0] + float(parameters.get("x", 0.0)),
                        origin[1] + float(parameters.get("y", 0.0)),
                        origin[2] + float(parameters.get("z", 0.0))),
            })
        elif kind == "cylinder":
            radius = float(parameters.get("diameter", 0.0)) / 2.0
            base = (position.get("x", 0.0), position.get("y", 0.0),
                    position.get("z", 0.0))
            height = float(parameters.get("height", 0.0))
            cylinders.append({
                "id": operation.get("id"),
                "axis_xy": (base[0], base[1]),
                "radius": radius,
                "min": (base[0] - radius, base[1] - radius, base[2]),
                "max": (base[0] + radius, base[1] + radius, base[2] + height),
            })

    def overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        return all(a["min"][i] < b["max"][i] - tolerance
                   and b["min"][i] < a["max"][i] - tolerance
                   for i in range(3))

    solids = boxes + cylinders
    interpenetrating = [
        (a["id"], b["id"])
        for i, a in enumerate(solids) for b in solids[i + 1:]
        if overlaps(a, b)]

    #: For every cylinder that ACTUALLY INTERFERES with a neighbour, is its
    #: axis sitting on that neighbour's boundary? This is DIAGNOSTIC -- it
    #: names the mechanism behind an overlap; it is not itself the criterion.
    #:
    #: The interference precondition is not decoration, and leaving it out
    #: was a real defect in the first draft of this file: two bodies standing
    #: side by side in x legitimately share a y coordinate, so an unqualified
    #: "axis touches a neighbour's boundary" test fired on FIVE Stage 77
    #: calls that were correct, and the offline re-grade caught it before any
    #: live call was spent. Sharing a coordinate is not a defect; straddling
    #: a boundary is.
    on_a_boundary: List[Dict[str, Any]] = []
    for cylinder in cylinders:
        for other in (b for b in solids if b["id"] != cylinder["id"]):
            # The interference precondition, and the ONLY place it is
            # applied. An earlier draft also pre-filtered the cylinders by
            # a separate `interfering` set; the mutation sweep showed that
            # guard was redundant with this one -- no test could tell the
            # two apart -- so it is gone rather than left as decoration.
            if not overlaps(cylinder, other):
                continue
            for index, letter in enumerate("xy"):
                for edge in ("min", "max"):
                    if abs(cylinder["axis_xy"][index]
                           - other[edge][index]) <= tolerance:
                        on_a_boundary.append({
                            "cylinder": cylinder["id"], "neighbour": other["id"],
                            "axis": letter, "edge": edge,
                            "coordinate": cylinder["axis_xy"][index],
                        })

    return {
        "case": "CR-06",
        "body_count": len(solids),
        "cylinder_count": len(cylinders),
        "axes_on_a_neighbour_boundary": on_a_boundary,
        "interpenetrating_pairs": interpenetrating,
    }


def grade_cr06(seen: Mapping[str, Any]) -> Dict[str, Any]:
    """Three solids, standing apart. That is all the request entails.

    `axes_on_a_neighbour_boundary` is deliberately NOT a check here. It is
    the diagnosis of an overlap, not an independent requirement: a cylinder
    may legitimately share a coordinate with a body it stands clear of, and
    gating on that scored five correct Stage 77 calls as failures.
    """
    checks = {
        "three_solids": seen["body_count"] == G78.axis_truth("CR-06")["bodies"],
        "bodies_disjoint": not seen["interpenetrating_pairs"],
    }
    return {
        "case": "CR-06",
        "checks": checks,
        "axis_ok": all(checks.values()),
    }


# ----------------------------------------------------------- the two layered

OBSERVERS = {"ED-02": observe_ed02, "CR-06": observe_cr06}
GRADERS = {"ED-02": grade_ed02, "CR-06": grade_cr06}

#: The Stage 78 failure vocabulary. Narrow on purpose: this stage measures
#: ONE mechanism, and a code for anything else would invite reading a broad
#: conclusion out of a narrow experiment.
AXIS_AT_MAX_EXTENT = "X1:axis_at_max_extent"
AXIS_AT_MIN_EXTENT = "X2:axis_at_min_extent"
AXIS_ELSEWHERE = "X3:axis_elsewhere"
AXIS_ABSENT = "X4:no_feature_written"
WRONG_DIRECTION = "X5:wrong_direction"
WRONG_TARGET = "X6:wrong_target"
BODY_MOVED = "X7:body_moved_instead"
STAGE77_FAILURE = "X8:stage77_failure_not_axis"

AXIS_TAXONOMY: Tuple[str, ...] = (
    AXIS_AT_MAX_EXTENT, AXIS_AT_MIN_EXTENT, AXIS_ELSEWHERE, AXIS_ABSENT,
    WRONG_DIRECTION, WRONG_TARGET, BODY_MOVED, STAGE77_FAILURE,
)


def classify(seen: Mapping[str, Any], graded: Mapping[str, Any],
             stage77_strict: bool) -> Tuple[str, ...]:
    """Name the mechanism. Empty when the axis criterion is satisfied."""
    if graded["axis_ok"]:
        # Stage 78's criterion is met. If Stage 77 still failed the turn it
        # failed for some OTHER reason, and saying so keeps this stage from
        # claiming a fix it did not make.
        return () if stage77_strict else (STAGE77_FAILURE,)

    codes: List[str] = []
    checks = graded["checks"]
    if seen["case"] == "ED-02":
        if not checks["bore_present"]:
            codes.append(AXIS_ABSENT)
        else:
            for letter in ("x", "y"):
                named = seen[f"bore_{letter}_is"]
                if named == G78.AT_MAX_EXTENT:
                    codes.append(AXIS_AT_MAX_EXTENT)
                elif named == G78.AT_MIN_EXTENT:
                    codes.append(AXIS_AT_MIN_EXTENT)
                elif named != G78.AT_AXIS:
                    codes.append(AXIS_ELSEWHERE)
            if not checks["bore_targets_the_pin"]:
                codes.append(WRONG_TARGET)
            if not checks["bore_runs_along_the_body_axis"]:
                codes.append(WRONG_DIRECTION)
            if checks.get("body_did_not_move") is False:
                codes.append(BODY_MOVED)
    else:
        if seen["axes_on_a_neighbour_boundary"]:
            edges = {e["edge"] for e in seen["axes_on_a_neighbour_boundary"]}
            if "max" in edges:
                codes.append(AXIS_AT_MAX_EXTENT)
            if "min" in edges:
                codes.append(AXIS_AT_MIN_EXTENT)
        elif seen["interpenetrating_pairs"]:
            codes.append(AXIS_ELSEWHERE)
        elif not checks["three_solids"]:
            codes.append(AXIS_ABSENT)

    return tuple(dict.fromkeys(codes)) or (AXIS_ELSEWHERE,)


def score_turn(case_name: str, raw_text: Optional[str],
               stage77_strict: bool) -> Dict[str, Any]:
    """One turn, observed, graded and classified. Never raises."""
    seen = OBSERVERS[case_name](raw_text)
    graded = GRADERS[case_name](seen)
    codes = classify(seen, graded, stage77_strict)
    return {
        "case": case_name,
        "observation": seen,
        "checks": graded["checks"],
        "axis_ok": graded["axis_ok"],
        #: THE primary metric. Strictly stronger than Stage 77's, never weaker.
        "stage78_strict": bool(stage77_strict and graded["axis_ok"]),
        "stage77_strict": bool(stage77_strict),
        "codes": list(codes),
    }


def summarise(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-case rates. The two cases are reported apart and never pooled."""
    per_case: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entry = per_case.setdefault(row["case"], {
            "calls": 0, "stage78_strict": 0, "axis_ok": 0,
            "stage77_strict": 0, "codes": {}})
        entry["calls"] += 1
        entry["stage78_strict"] += 1 if row["stage78_strict"] else 0
        entry["axis_ok"] += 1 if row["axis_ok"] else 0
        entry["stage77_strict"] += 1 if row["stage77_strict"] else 0
        for code in row["codes"]:
            entry["codes"][code] = entry["codes"].get(code, 0) + 1
    for entry in per_case.values():
        calls = entry["calls"] or 1
        entry["rate"] = entry["stage78_strict"] / calls
        entry["axis_rate"] = entry["axis_ok"] / calls
    return {
        "per_case": per_case,
        #: Deliberately absent: a pooled rate over two cases with different
        #: denominators and different mechanisms would be a number about
        #: neither of them.
        "why_no_combined_rate": (
            "ED-02 and CR-06 are different groups (edit and creation) with "
            "different criteria; a pooled rate would describe neither"),
    }


__all__ = [
    "AXIS_ABSENT", "AXIS_AT_MAX_EXTENT", "AXIS_AT_MIN_EXTENT",
    "AXIS_ELSEWHERE", "AXIS_TAXONOMY", "BODY_MOVED", "GRADERS", "OBSERVERS",
    "STAGE77_FAILURE", "WRONG_DIRECTION", "WRONG_TARGET", "classify",
    "grade_cr06", "grade_ed02", "observe_cr06", "observe_ed02",
    "operations_of", "score_turn", "summarise",
]
