"""The DETERMINISTIC parts Stage 76's observer is proven against.

Every plan here was written by hand. **No model was called to produce any of
them, and none of the numbers they produce says anything about a model.**
They exist for one reason: an observer that has never been run against a
real kernel is a hypothesis, and the brief's rule is that the observer must
be complete, mutation-tested and proven offline before a single live call is
spent on it.

WHY THE PLANS LIVE HERE AND NOT IN `ground_truth76`. A plan is a STIMULUS,
not an expectation. `ground_truth76` says what the part must measure and
what the file must contain; this module says what to build to get one. The
direction of dependence is one way and deliberate -- the fixtures read the
ids and dimensions out of the truth, so a rename cannot desynchronise them,
and the truth reads nothing from here.

For the broader live corpus the plans come from a MODEL instead, and the
truth is read the same way: by case name. That is the point of keeping them
apart.

This module also holds the CORRUPTED observations -- the traps. Each one is
a plausible-looking observation with exactly one thing wrong, built so that
a grader which takes a shortcut passes it. They are not cases and are never
scored; they are what proves the grader bites.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

import ground_truth76 as G


# ------------------------------------------------------------- the plans
#
# Dimensions and ids come from `ground_truth76`, never retyped.


def _box(op_id: str, x: float, y: float, z: float,
         position: Mapping[str, float] = None) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"x": x, "y": y, "z": z}
    if position:
        parameters["position"] = dict(position)
    return {"id": op_id, "type": "box", "parameters": parameters}


def _cylinder(op_id: str, diameter: float, height: float,
              position: Mapping[str, float] = None) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"diameter": diameter, "height": height}
    if position:
        parameters["position"] = dict(position)
    return {"id": op_id, "type": "cylinder", "parameters": parameters}


def _declare(op_id: str, body: str) -> Dict[str, Any]:
    return {"id": op_id, "type": "part", "target": body}


def _union(op_id: str, target: str, tools: Tuple[str, ...]) -> Dict[str, Any]:
    return {"id": op_id, "type": "union", "target": target,
            "tools": list(tools)}


#: X1 -- two disjoint bodies. The pin stands clear of the cube in x, so the
#: two share no volume and a fused part would measure their sum rather than
#: two separate numbers.
X1_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "cube-and-pin",
    "operations": [
        _box("cube", G.CUBE_EDGE, G.CUBE_EDGE, G.CUBE_EDGE),
        _cylinder("pin", G.PIN_DIAMETER, G.PIN_LENGTH,
                  {"x": 60.0, "y": 20.0, "z": 0.0}),
        _declare("b1", "cube"),
        _declare("b2", "pin"),
    ],
}

#: X2 -- THREE bodies, three different shapes, three different volumes.
X2_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "block-rod-shim",
    "operations": [
        _box("block", G.CUBE_EDGE, G.CUBE_EDGE, G.CUBE_EDGE),
        _cylinder("rod", G.PIN_DIAMETER, G.PIN_LENGTH,
                  {"x": 60.0, "y": 20.0, "z": 0.0}),
        _box("shim", G.SLAB_X, G.SLAB_Y, G.SLAB_Z,
             {"x": 100.0, "y": 0.0, "z": 0.0}),
        _declare("b1", "block"),
        _declare("b2", "rod"),
        _declare("b3", "shim"),
    ],
}

#: X3 -- one body, no declaration. The single-body regression.
X3_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "one-cube",
    "operations": [
        _box("cube", G.CUBE_EDGE, G.CUBE_EDGE, G.CUBE_EDGE),
    ],
}

#: X4 -- two live bodies and one CONSUMED. The boss stands ON the plate, so
#: the two meet on a face and the fuse is the sum exactly; `plate` keeps its
#: id through the union, which is what makes the consumed case reachable
#: with more than one body still standing.
X4_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "plate-boss-pin",
    "operations": [
        _box("plate", G.PLATE_X, G.PLATE_Y, G.PLATE_Z),
        _cylinder("boss", G.BOSS_DIAMETER, G.BOSS_HEIGHT,
                  {"x": G.PLATE_X / 2.0, "y": G.PLATE_Y / 2.0,
                   "z": G.PLATE_Z}),
        _union("fuse", "plate", ("boss",)),
        _cylinder("pin", G.PIN_DIAMETER, G.PIN_LENGTH,
                  {"x": 100.0, "y": 25.0, "z": 0.0}),
        _declare("b1", "plate"),
        _declare("b2", "pin"),
    ],
}

#: X5 -- the fusion control. Two OVERLAPPING boxes fused into one body, so
#: the result is neither box and is not their sum.
X5_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "pad-and-lug",
    "operations": [
        _box("pad", G.FUSE_A_EDGE, G.FUSE_A_EDGE, G.FUSE_A_EDGE),
        _box("lug", G.FUSE_B_EDGE, G.FUSE_B_EDGE, G.FUSE_B_EDGE,
             {"x": G.FUSE_B_ORIGIN_X, "y": 0.0, "z": 0.0}),
        _union("fuse", "pad", ("lug",)),
    ],
}

#: X6 -- two bodies of IDENTICAL dimension. Same shape, same size, same
#: volume to the last digit; only their ids and positions differ.
X6_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "two-identical-cubes",
    "operations": [
        _box("left", G.CUBE_EDGE, G.CUBE_EDGE, G.CUBE_EDGE),
        _box("right", G.CUBE_EDGE, G.CUBE_EDGE, G.CUBE_EDGE,
             {"x": 60.0, "y": 0.0, "z": 0.0}),
        _declare("b1", "left"),
        _declare("b2", "right"),
    ],
}

#: X7 -- THE PRODUCT PATH. Its plan is not written here: it is what the
#: deterministic reader makes of the sentence, which is the plan the session
#: route would build for a person typing it. Derived rather than copied so
#: the fixture cannot drift from the product; the TRUTH for the case stays
#: pinned in `ground_truth76` by name, so a change in the reader fails the
#: case instead of quietly redefining it.
X7_REQUEST: str = (
    "Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it "
    "as two separate bodies."
)


def _x7_plan() -> Dict[str, Any]:
    from cad_experimental import normalize
    reading = normalize.read_separate_bodies(X7_REQUEST, None)
    plan = getattr(reading, "plan", None)
    if not plan:
        raise RuntimeError(
            "the deterministic reader no longer reads X7's request; that is "
            "a product regression, not a fixture to repair here"
        )
    return dict(plan)


X7_PLAN: Dict[str, Any] = _x7_plan()

PLANS: Mapping[str, Dict[str, Any]] = {
    "X1": X1_PLAN,
    "X2": X2_PLAN,
    "X3": X3_PLAN,
    "X4": X4_PLAN,
    "X5": X5_PLAN,
    "X6": X6_PLAN,
    "X7": X7_PLAN,
}


def plan_for(case_name: str) -> Dict[str, Any]:
    """The deterministic plan that builds one case's part.

    Deliberately NOT part of `ground_truth76.expected`: this is the
    stimulus, and for the live corpus it comes from a model instead.
    """
    if case_name not in PLANS:
        raise KeyError(
            f"no fixture plan for {case_name!r}; known: {', '.join(PLANS)}"
        )
    return {"status": PLANS[case_name]["status"],
            "summary": PLANS[case_name]["summary"],
            "operations": [dict(op) for op in PLANS[case_name]["operations"]]}


# -------------------------------------------------------------- the traps
#
# Each corrupts ONE thing in an otherwise-correct observation. The grader
# must fail every one of them, and a grader that takes the named shortcut
# passes it -- which is what makes each trap a test rather than a decoration.


def _body(body_id: str, volume: float, *, faces: int = 6, edges: int = 12,
          low=(0.0, 0.0, 0.0), high=(40.0, 40.0, 40.0)) -> Dict[str, Any]:
    return {
        "id": body_id, "features": [body_id], "is_valid": True,
        "solid_count": 1, "volume": volume, "face_count": faces,
        "edge_count": edges, "minimum": tuple(low), "maximum": tuple(high),
    }


CUBE_BODY: Dict[str, Any] = _body("cube", G.CUBE_VOLUME)
PIN_BODY: Dict[str, Any] = _body(
    "pin", G.PIN_VOLUME, faces=3, edges=3,
    low=(50.0, 10.0, 0.0), high=(70.0, 30.0, 30.0),
)


def correct_measurement_rows() -> List[Dict[str, Any]]:
    """X1's probe rows, all correct. The base every trap is one edit from.

    **These are the real thing, not a plausible imitation.** Every field is
    what `observe76.measure` recorded on a genuine CadQuery 2.8.0 build of
    `X1_PLAN`, down to the wording of both refusals and the three decimals
    the product formats a volume to. `test_stage76_observation` re-observes
    that build and asserts the graded fields are identical, so a trap can
    never quietly become a test of something the product no longer does.
    """
    return [
        {"probe": "X1-named-cube", "kind": G.NAMED,
         "text": "What is the volume of the cube?",
         "outcome": G.ANSWERED, "about": "cube", "aggregate": False,
         "provenance": G.MEASURED, "value": 64000.0,
         "said": "cube: Volume 64000.000 mm3.", "label_is_prefix": True},
        {"probe": "X1-named-pin", "kind": G.NAMED,
         "text": "What is the volume of the pin?",
         "outcome": G.ANSWERED, "about": "pin", "aggregate": False,
         "provenance": G.MEASURED, "value": 9424.778,
         "said": "pin: Volume 9424.778 mm3.", "label_is_prefix": True},
        {"probe": "X1-unnamed", "kind": G.UNNAMED_SEVERAL,
         "text": "What is the volume?",
         "outcome": G.REFUSED, "about": None, "aggregate": False,
         "provenance": None, "value": None,
         "said": "this part has 2 separate bodies ('cube', 'pin'), and the "
                 "request does not say which one to measure. Name it and it "
                 "will be done",
         "label_is_prefix": None},
        {"probe": "X1-two-at-once", "kind": G.TWO_AT_ONCE,
         "text": "What is the volume of the cube and the pin?",
         "outcome": G.REFUSED, "about": None, "aggregate": False,
         "provenance": None, "value": None,
         "said": "this request names more than one body ('cube', 'pin'); one "
                 "operation measures one body, so say which",
         "label_is_prefix": None},
        {"probe": "X1-total", "kind": G.AGGREGATE_TOTAL,
         "text": "What is the total volume?",
         "outcome": G.ANSWERED, "about": None, "aggregate": True,
         "provenance": G.CALCULATED, "value": 73424.778,
         "said": "all 2 bodies: Volume 73424.778 mm3.",
         "label_is_prefix": True},
        {"probe": "X1-overall-size", "kind": G.AGGREGATE_SIZE,
         "text": "What is the overall size of the whole part?",
         "outcome": G.ANSWERED, "about": None, "aggregate": True,
         "provenance": G.ASSUMED, "value": 70.0,
         "said": "all 2 bodies: Overall 70 x 40 x 40 mm (X x Y x Z).",
         "label_is_prefix": True},
    ]


def _rows_with(**replacements: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = correct_measurement_rows()
    for probe, changes in replacements.items():
        for row in rows:
            if row["probe"] == probe.replace("_", "-"):
                row.update(changes)
    return rows


#: TRAP 1 -- the two bodies' VALUES are swapped between their ids. Every
#: number present is a number the part really has, and the multiset of
#: values is exactly right. Only an observer that reads which body each
#: answer was ABOUT can see it; one that pairs answers to bodies by value
#: reports a clean pass.
#: The two values are the OBSERVED ones exchanged, not closed forms
#: substituted: the multiset of numbers in the trap is byte-identical to the
#: multiset in a correct run, down to the three decimals the product rounds
#: to, so nothing but the pairing distinguishes them.
SWAPPED_VALUES: List[Dict[str, Any]] = _rows_with(
    X1_named_cube={"value": 9424.778,
                   "said": "cube: Volume 9424.778 mm3."},
    X1_named_pin={"value": 64000.0,
                  "said": "pin: Volume 64000.000 mm3."},
)

#: TRAP 2 -- the answers are correct but each is attributed to the OTHER
#: body. The values stay with the right numbers and the labels move, which
#: is the same defect seen from the other side.
SWAPPED_LABELS: List[Dict[str, Any]] = _rows_with(
    X1_named_cube={"about": "pin"},
    X1_named_pin={"about": "cube"},
)

#: TRAP 3 -- the rows in REVERSE order, everything else identical. A grader
#: that pairs the n-th row with the n-th expectation flips; one that keys on
#: the probe name cannot tell the difference, and must not.
REVERSED_ORDER: List[Dict[str, Any]] = list(reversed(correct_measurement_rows()))

#: TRAP 4 -- the ambiguous probe was ANSWERED instead of refused, about the
#: first body, with a perfectly real number. This is the failure the whole
#: measurement slice exists to prevent, and it looks like a success.
ANSWERED_THE_AMBIGUOUS: List[Dict[str, Any]] = _rows_with(
    X1_unnamed={"outcome": G.ANSWERED, "about": "cube",
                "provenance": G.MEASURED, "value": G.CUBE_VOLUME,
                "said": "cube: Volume 64000.000 mm3.",
                "label_is_prefix": True},
)

#: TRAP 5 -- the ambiguous probe DECLINED rather than refused. `None` falls
#: through to a model, which would answer from a plan it can read about a
#: part it cannot see. Distinct from trap 4 and separately fatal.
DECLINED_THE_AMBIGUOUS: List[Dict[str, Any]] = _rows_with(
    X1_unnamed={"outcome": G.DECLINED, "said": ""},
)

#: TRAP 6 -- the refusal names only ONE of the two bodies. It refused, so
#: nothing was guessed; it is still not an answer, because the person cannot
#: act on it.
REFUSAL_NAMES_ONE: List[Dict[str, Any]] = _rows_with(
    X1_unnamed={"said": "this part has separate bodies; did you mean "
                        "'cube'?"},
)

#: TRAP 7 -- the total is right and the SPLIT is wrong. Two bodies of equal
#: volume summing to exactly the right total. A grader that checks the total
#: as proof of per-body measurement passes it; the part is not the part.
_HALF: float = (G.CUBE_VOLUME + G.PIN_VOLUME) / 2.0
TOTAL_RIGHT_SPLIT_WRONG: List[Dict[str, Any]] = _rows_with(
    X1_named_cube={"value": _HALF},
    X1_named_pin={"value": _HALF},
)

#: TRAP 8 -- the aggregate total is reported as MEASURED. The number is
#: right to the last digit, and no kernel measured it: nothing ever put the
#: two bodies on a scale together.
TOTAL_CLAIMED_MEASURED: List[Dict[str, Any]] = _rows_with(
    X1_total={"provenance": G.MEASURED},
)

#: TRAP 9 -- the aggregate SIZE is reported as MEASURED rather than ASSUMED.
#: The box round two bodies standing apart contains the air between them.
SIZE_CLAIMED_MEASURED: List[Dict[str, Any]] = _rows_with(
    X1_overall_size={"provenance": G.MEASURED},
)

#: TRAP 10 -- a probe is simply MISSING from the rows. Five correct answers
#: out of six looks like a good run; the question that was not asked is the
#: one that would have failed.
MISSING_PROBE: List[Dict[str, Any]] = [
    row for row in correct_measurement_rows() if row["probe"] != "X1-unnamed"
]

MEASUREMENT_TRAPS: Mapping[str, List[Dict[str, Any]]] = {
    "swapped_values": SWAPPED_VALUES,
    "swapped_labels": SWAPPED_LABELS,
    "answered_the_ambiguous": ANSWERED_THE_AMBIGUOUS,
    "declined_the_ambiguous": DECLINED_THE_AMBIGUOUS,
    "refusal_names_one": REFUSAL_NAMES_ONE,
    "total_right_split_wrong": TOTAL_RIGHT_SPLIT_WRONG,
    "total_claimed_measured": TOTAL_CLAIMED_MEASURED,
    "size_claimed_measured": SIZE_CLAIMED_MEASURED,
    "missing_probe": MISSING_PROBE,
}

#: REVERSED_ORDER is held apart from the traps above: it must PASS. It is
#: the control that proves the grader keys on the probe's name rather than
#: on its position, and a grader that failed it would be wrong in the other
#: direction.
MEASUREMENT_CONTROLS: Mapping[str, List[Dict[str, Any]]] = {
    "reversed_order": REVERSED_ORDER,
}


def correct_export_row() -> Dict[str, Any]:
    """X1's export observation, correct. The base every export trap edits.

    Also real: a CadQuery 2.8.0 assembly write of `X1_PLAN`, read back and
    inspected. `product_names` carries the writer's own root product beside
    the two bodies, because that is what is in the file.
    """
    return {
        "case": "X1",
        "engine": "cadquery",
        "writer": "assembly",
        "asked_for": ["cube", "pin"],
        "body_count": 2,
        "writer_raised": False,
        "error": None,
        "wrote_file": True,
        "bytes": 22630,
        "readable": True,
        "solids_read": 2,
        "volumes_read": [63999.999999999985, 9424.777960769381],
        "names_found": ["cube", "pin"],
        "names_missing": [],
        "names_found_by_substring": ["cube", "pin"],
        "product_names": ["27d78890-b99f-11f1-8884-02fc00000001",
                          "cube", "pin"],
        "read_error": None,
        "identity_binding": G.IDENTITY_BINDING,
        "identity_binding_note": G.IDENTITY_BINDING_NOTE,
    }


def _export_with(**changes: Any) -> Dict[str, Any]:
    row = correct_export_row()
    row.update(changes)
    return row


#: The six rungs, each as an observation that must grade to exactly it.
EXPORT_TRAPS: Mapping[str, Dict[str, Any]] = {
    # A: the writer raised. Nothing was exported.
    G.LEVEL_A: _export_with(wrote_file=False, bytes=0, readable=False,
                            solids_read=None, volumes_read=None,
                            names_found=[], names_missing=["cube", "pin"],
                            error="the writer raised"),
    # B: bytes on disk that are not a readable STEP. "The file exists" is
    #    true and means nothing.
    G.LEVEL_B: _export_with(readable=False, solids_read=None,
                            volumes_read=None, names_found=[],
                            names_missing=["cube", "pin"],
                            error="the file could not be read back"),
    # C-empty: FreeCAD's `Part.export` handed raw shapes -- a well-formed
    #    1 640-byte STEP holding ZERO solids. Measured, not hypothetical.
    "C_empty": _export_with(bytes=1640, solids_read=0, volumes_read=[],
                            names_found=[], names_missing=["cube", "pin"]),
    # C-fused: two bodies in, ONE solid out. The bodies were fused by the
    #    writer, which is a join the plan never asked for.
    "C_fused": _export_with(solids_read=1,
                            volumes_read=[G.CUBE_VOLUME + G.PIN_VOLUME]),
    # D: the anonymous-product trap. Perfect geometry, and the identity is
    #    gone: handed a compound BOTH engines write a correct two-solid STEP
    #    whose products are the translator's own strings.
    G.LEVEL_D: _export_with(
        names_found=[], names_missing=["cube", "pin"],
        names_found_by_substring=[],
        product_names=["Open CASCADE STEP translator 7.9 1.1",
                       "Open CASCADE STEP translator 7.9 1.2"]),
    # D, and the reason the observer reads PRODUCT names rather than doing
    #    what the product does. The bodies here are called `part` and
    #    `SOLID`, which occur in every STEP file's boilerplate, so the
    #    substring scan `cad_backend.verify_assembly` performs finds BOTH in
    #    a file that names neither body. Measured on real two-body files
    #    from both engines, which also accept `'Open'` and `'cub'`.
    "D_substring_fooled": _export_with(
        asked_for=["part", "SOLID"],
        names_found=[], names_missing=["part", "SOLID"],
        names_found_by_substring=["part", "SOLID"],
        product_names=["Open CASCADE STEP translator 7.9 1.1",
                       "Open CASCADE STEP translator 7.9 1.2"]),
    # E: right count, right names, and a solid that is not the one built.
    G.LEVEL_E: _export_with(volumes_read=[G.CUBE_VOLUME, G.PIN_VOLUME * 2.0]),
    # F: everything, and no more claimed than that.
    G.LEVEL_F: correct_export_row(),
}

#: An export row that a shortcut would pass and the ladder must not:
#: the file exists, it is big, and it holds nothing.
FILE_EXISTS_BUT_EMPTY: Dict[str, Any] = EXPORT_TRAPS["C_empty"]


__all__ = [
    "ANSWERED_THE_AMBIGUOUS", "CUBE_BODY", "DECLINED_THE_AMBIGUOUS",
    "EXPORT_TRAPS", "FILE_EXISTS_BUT_EMPTY", "MEASUREMENT_CONTROLS",
    "MEASUREMENT_TRAPS", "MISSING_PROBE", "PLANS", "PIN_BODY",
    "REFUSAL_NAMES_ONE", "REVERSED_ORDER", "SIZE_CLAIMED_MEASURED",
    "SWAPPED_LABELS", "SWAPPED_VALUES", "TOTAL_CLAIMED_MEASURED",
    "TOTAL_RIGHT_SPLIT_WRONG", "X1_PLAN", "X2_PLAN", "X3_PLAN", "X4_PLAN",
    "X5_PLAN", "X6_PLAN", "X7_PLAN", "X7_REQUEST", "correct_export_row", "correct_measurement_rows", "plan_for",
]
