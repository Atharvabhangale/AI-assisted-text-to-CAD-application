"""The DETERMINISTIC parts an edit or refusal case starts from, and the traps.

**No model produced any of this.** Every plan here is a literal written by
hand, and `arena77` builds it and asserts the result against
`ground_truth77.FIXTURE_BODIES` before a single live call is made against
it. Stage 75 Phase A recorded setup failures as refusal failures; a case
that starts from a model-made part measures two things at once and can only
be read as one.

WHY THE PLANS ARE HERE AND NOT IN THE TRUTH. A fixture plan is a STIMULUS.
`ground_truth77` says what the fixture must MEASURE; this says what to build
to get one. The dependence runs one way and the ids and dimensions come out
of the truth, so a rename cannot desynchronise them.

This module also holds the CORRUPTED observations -- the traps. Each is a
plausible-looking record with exactly one thing wrong, chosen so that a
grader taking a particular shortcut passes it. They are not cases, are never
scored, and exist to prove the grader bites.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

import ground_truth77 as G


# ---------------------------------------------------------- plan helpers


def box(op_id: str, x: float, y: float, z: float,
        position: Mapping[str, float] = None) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"x": x, "y": y, "z": z}
    if position:
        parameters["position"] = dict(position)
    return {"id": op_id, "type": "box", "parameters": parameters}


def cylinder(op_id: str, diameter: float, height: float,
             position: Mapping[str, float] = None) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"diameter": diameter, "height": height}
    if position:
        parameters["position"] = dict(position)
    return {"id": op_id, "type": "cylinder", "parameters": parameters}


def declare(op_id: str, body: str) -> Dict[str, Any]:
    return {"id": op_id, "type": "part", "target": body}


# --------------------------------------------------------- the fixtures

#: Two bodies, standing clear of each other in X. The edit cases and the
#: two-body refusals start here.
CUBE_PIN_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "a 40 mm cube and a 20 mm cylinder 30 mm long beside it",
    "operations": [
        box("cube", 40.0, 40.0, 40.0),
        # x = 100, NOT 60. The edit chain grows the cube to 60 mm along X,
        # and at x = 60 the pin spans 50..70 -- so the cube would run into
        # it and "leave the cylinder unchanged" could not be obeyed and
        # leave a disjoint part at the same time. Pinning DISJOINT on a
        # request that never says to move the pin would have been exactly
        # the unstated assumption this corpus forbids. The preflight caught
        # it before a single live call, which is what the preflight is for.
        cylinder("pin", 20.0, 30.0, {"x": 100.0, "y": 20.0, "z": 0.0}),
        declare("body_cube", "cube"),
        declare("body_pin", "pin"),
    ],
}

#: Three bodies of three shapes. The refusals that must generalise past two
#: start here -- a refusal naming two of three is not an answer either.
TRIO_PLAN: Dict[str, Any] = {
    "status": "generated",
    "summary": "a plate, a boss beside it and a rod beside that",
    "operations": [
        box("plate", 60.0, 40.0, 8.0),
        cylinder("boss", 16.0, 20.0, {"x": 80.0, "y": 20.0, "z": 0.0}),
        cylinder("rod", 10.0, 40.0, {"x": 120.0, "y": 20.0, "z": 0.0}),
        declare("body_plate", "plate"),
        declare("body_boss", "boss"),
        declare("body_rod", "rod"),
    ],
}

PLANS: Mapping[str, Dict[str, Any]] = {
    G.FIXTURE_CUBE_PIN: CUBE_PIN_PLAN,
    G.FIXTURE_TRIO: TRIO_PLAN,
}

#: What each fixture is called in front of a person. Fed to the session as
#: the revision summary, so `revision_context` describes the part the model
#: is being asked to modify.
SUMMARIES: Mapping[str, str] = {
    G.FIXTURE_CUBE_PIN: CUBE_PIN_PLAN["summary"],
    G.FIXTURE_TRIO: TRIO_PLAN["summary"],
}


def plan_for(fixture: str) -> Dict[str, Any]:
    """A fresh copy of one fixture's plan. Never the shared object.

    Copied because a session commits the plan it is given and the arena
    runs many attempts against the same fixture; a shared mutable plan
    would let one attempt's revision reach the next one's starting point.
    """
    if fixture not in PLANS:
        raise KeyError(
            f"no fixture plan for {fixture!r}; known: {', '.join(PLANS)}")
    source = PLANS[fixture]
    return {
        "status": source["status"],
        "summary": source["summary"],
        "operations": [dict(op) for op in source["operations"]],
    }


# ============================================================== the traps
#
# Each corrupts ONE thing in an otherwise-correct observation.


def _body(body_id: str, volume: float, *, faces: int = G.BOX_FACES,
          edges: int = 12, low=(0.0, 0.0, 0.0),
          high=(40.0, 40.0, 40.0)) -> Dict[str, Any]:
    return {
        "id": body_id, "features": [body_id], "is_valid": True,
        "solid_count": 1, "volume": volume, "face_count": faces,
        "edge_count": edges, "minimum": list(low), "maximum": list(high),
    }


def correct_creation_observation() -> Dict[str, Any]:
    """CR-01's observation, correct. The base every creation trap edits."""
    return {
        "case": "CR-01",
        "turn": 0,
        "group": G.CREATION,
        "source": G.MODEL_GENERATED,
        "outcome_declared": "generated",
        "structured_output": True,
        "fenced": False,
        "plan_valid": True,
        "plan_problems": [],
        "operation_count": 4,
        "model_operation_count": 4,
        "declared_bodies": ["cube", "pin"],
        "questions": [],
        "summary": "two separate bodies",
        "plan_reason": None,
        "system_error": None,
        "executed": True,
        "execution_succeeded": True,
        "execution_failure": None,
        "bodies": [
            _body("cube", G.CUBE_40),
            _body("pin", G.PIN_20x30, faces=G.CYLINDER_FACES, edges=3,
                  low=(50.0, 10.0, 0.0), high=(70.0, 30.0, 30.0)),
        ],
        "body_count": 2,
        "raw_text": '{"status": "generated", "operations": []}',
    }


def _creation_with(**changes: Any) -> Dict[str, Any]:
    row = correct_creation_observation()
    row.update(changes)
    return row


#: A body simply absent. One correct body out of two is not half a part.
MISSING_BODY: Dict[str, Any] = _creation_with(
    bodies=[_body("cube", G.CUBE_40)], body_count=1,
    declared_bodies=["cube"])

#: A third body nobody asked for, at a plausible size.
EXTRA_BODY: Dict[str, Any] = _creation_with(
    bodies=correct_creation_observation()["bodies"] + [
        _body("spacer", G.CUBE_20, low=(100.0, 0.0, 0.0),
              high=(120.0, 20.0, 20.0))],
    body_count=3, declared_bodies=["cube", "pin", "spacer"])

#: FUSED: one body carrying exactly the total of the two apart. Every
#: total-based check passes and the part is not the part.
UNWANTED_FUSION: Dict[str, Any] = _creation_with(
    bodies=[_body("part", G.CUBE_40 + G.PIN_20x30,
                  low=(0.0, 0.0, 0.0), high=(70.0, 40.0, 40.0))],
    body_count=1, declared_bodies=[])

#: Two bodies that OVERLAP where the request said "beside it". The volumes
#: are right; the part is one interpenetrating lump.
NOT_DISJOINT: Dict[str, Any] = _creation_with(
    bodies=[
        _body("cube", G.CUBE_40),
        _body("pin", G.PIN_20x30, faces=G.CYLINDER_FACES, edges=3,
              low=(10.0, 10.0, 0.0), high=(30.0, 30.0, 30.0)),
    ])

#: The right multiset of volumes, and the cylinder's is on the cube. Only a
#: check that reads WHICH body carries which number can see it -- and on a
#: case whose two bodies differ, a per-body check can.
SWAPPED_BODIES: Dict[str, Any] = _creation_with(
    bodies=[
        _body("cube", G.PIN_20x30),
        _body("pin", G.CUBE_40, faces=G.CYLINDER_FACES, edges=3,
              low=(50.0, 10.0, 0.0), high=(70.0, 30.0, 30.0)),
    ])

#: Two bodies standing, and neither declared. Stage 71: a leftover solid and
#: a declared body are different facts, and inferring the second from the
#: first is the silent behaviour Stage 62 removed.
#:
#: MEASURED, not imagined. The validator ACCEPTS such a plan -- it has no
#: problem to report -- and the EXECUTOR refuses it with `multiple_solids`
#: while still returning the body list. So the realistic record is exactly
#: this: two real bodies, none declared, and `execution_succeeded` false.
UNDECLARED: Dict[str, Any] = _creation_with(
    declared_bodies=[], execution_succeeded=False,
    execution_failure={
        "code": "multiple_solids", "operation": "pin",
        "message": "the plan leaves 2 separate solids ('cube', 'pin'); a "
                   "part is exactly one, unless the plan declares otherwise"})

#: The plan did not validate, and a body list came back anyway.
INVALID_PLAN: Dict[str, Any] = _creation_with(
    plan_valid=False, plan_problems=[{"code": "P11", "where": "bore",
                                      "message": "target is an operation"}])

#: NOT a model result: a deterministic grammar answered. Every geometry
#: check passes and the run learns nothing about a model.
DETERMINISTIC_SUCCESS: Dict[str, Any] = _creation_with(
    source=G.DETERMINISTIC)

#: A fallback: the model was asked, was not usable, and a grammar answered.
FALLBACK_SUCCESS: Dict[str, Any] = _creation_with(source=G.FALLBACK)

#: THE RIGHT TOTAL AND THE WRONG SPLIT, at the right BODY COUNT. Two bodies
#: of equal volume summing to exactly what the part should measure. Every
#: total-based check passes, the count is right, the declaration is right,
#: and the bodies are disjoint -- only a per-body comparison can see it.
#:
#: Distinct from `unwanted_fusion`, which has ONE body and so is caught on
#: the count whatever the volume check does. That is why this trap exists:
#: a mutation sweep showed the total-versus-split guard surviving, because
#: the only trap aimed at it failed for a different reason.
_HALF: float = (G.CUBE_40 + G.PIN_20x30) / 2.0
SPLIT_WRONG: Dict[str, Any] = _creation_with(bodies=[
    _body("cube", _HALF),
    _body("pin", _HALF, faces=G.CYLINDER_FACES, edges=3,
          low=(90.0, 10.0, 0.0), high=(110.0, 30.0, 30.0)),
])

#: THE RIGHT BODIES IN THE WRONG PLACE. Graded against CR-09, whose request
#: states both corners: the volumes, the count, the declaration and the
#: disjointness are all right and the part is not where it was asked for.
WRONG_PLACEMENT: Dict[str, Any] = _creation_with(
    case="CR-09",
    declared_bodies=["a", "b"],
    bodies=[
        _body("a", G.PLATE_50x40x10, low=(0.0, 0.0, 0.0),
              high=(50.0, 40.0, 10.0)),
        # 20 mm cube of the right size, at x = 200 instead of x = 70.
        _body("b", G.CUBE_20, low=(200.0, 0.0, 0.0),
              high=(220.0, 20.0, 20.0)),
    ])

#: The same defect on the OTHER placement branch. CR-09 states coordinates
#: but not which id gets which, so its boxes are matched as an unordered
#: set; CR-05 names the ids AND places them, so its are matched per id.
#: Two branches, two traps -- a sweep showed a mutant surviving because the
#: only placement trap exercised the branch it did not touch.
WRONG_PLACEMENT_NAMED: Dict[str, Any] = _creation_with(
    case="CR-05",
    declared_bodies=["left", "right"],
    bodies=[
        _body("left", G.CUBE_30, low=(0.0, 0.0, 0.0),
              high=(30.0, 30.0, 30.0)),
        # The right cube, the right size, 40 mm further out than asked.
        _body("right", G.CUBE_30, low=(100.0, 0.0, 0.0),
              high=(130.0, 30.0, 30.0)),
    ])

CREATION_TRAPS: Mapping[str, Dict[str, Any]] = {
    "split_wrong": SPLIT_WRONG,
    "wrong_placement": WRONG_PLACEMENT,
    "wrong_placement_named": WRONG_PLACEMENT_NAMED,
    "missing_body": MISSING_BODY,
    "extra_body": EXTRA_BODY,
    "unwanted_fusion": UNWANTED_FUSION,
    "not_disjoint": NOT_DISJOINT,
    "swapped_bodies": SWAPPED_BODIES,
    "undeclared": UNDECLARED,
    "invalid_plan": INVALID_PLAN,
    "deterministic_success": DETERMINISTIC_SUCCESS,
    "fallback_success": FALLBACK_SUCCESS,
}


def correct_edit_observation() -> Dict[str, Any]:
    """ED-01's observation, correct: the cube grew, the pin did not."""
    row = correct_creation_observation()
    row.update({
        "case": "ED-01", "group": G.EDIT,
        "bodies": [
            _body("cube", G.CUBE_50x40x40, high=(50.0, 40.0, 40.0)),
            _body("pin", G.PIN_20x30, faces=G.CYLINDER_FACES, edges=3,
                  low=(60.0, 10.0, 0.0), high=(80.0, 30.0, 30.0)),
        ],
    })
    return row


def _edit_with(bodies) -> Dict[str, Any]:
    row = correct_edit_observation()
    row["bodies"] = bodies
    row["body_count"] = len(bodies)
    return row


#: CROSS-BODY EDIT: the named body changed AND the other one moved with it.
#: The requested change is present, which is what makes it look right.
CROSS_BODY_EDIT: Dict[str, Any] = _edit_with([
    _body("cube", G.CUBE_50x40x40, high=(50.0, 40.0, 40.0)),
    _body("pin", G.PIN_20x50, faces=G.CYLINDER_FACES, edges=3,
          low=(60.0, 10.0, 0.0), high=(80.0, 30.0, 50.0)),
])

#: WRONG TARGET: the OTHER body was edited to the requested size, and the
#: named one was left alone. Both volumes exist in the part; neither is
#: where it belongs.
WRONG_TARGET: Dict[str, Any] = _edit_with([
    _body("cube", G.CUBE_40),
    _body("pin", G.CUBE_50x40x40, faces=G.CYLINDER_FACES, edges=3,
          low=(60.0, 10.0, 0.0), high=(110.0, 50.0, 40.0)),
])

#: NOTHING HAPPENED: the model returned the fixture unchanged. Every body is
#: a real body at a real volume and the request was ignored.
NO_CHANGE: Dict[str, Any] = _edit_with([
    _body("cube", G.CUBE_40),
    _body("pin", G.PIN_20x30, faces=G.CYLINDER_FACES, edges=3,
          low=(60.0, 10.0, 0.0), high=(80.0, 30.0, 30.0)),
])

EDIT_TRAPS: Mapping[str, Dict[str, Any]] = {
    "cross_body_edit": CROSS_BODY_EDIT,
    "wrong_target": WRONG_TARGET,
    "no_change": NO_CHANGE,
}


def correct_refusal_observation() -> Dict[str, Any]:
    """RF-01's observation, correct: declined, named both, asked, built
    nothing."""
    return {
        "case": "RF-01",
        "turn": 0,
        "group": G.REFUSAL,
        "source": G.MODEL_GENERATED,
        "outcome_declared": "needs_clarification",
        "structured_output": True,
        "fenced": False,
        "plan_valid": False,
        "plan_problems": [],
        "operation_count": 0,
        "model_operation_count": 0,
        "declared_bodies": [],
        "questions": [
            "This part has two separate bodies, cube and pin. Which one "
            "should be 20 mm taller?"],
        "summary": "the request does not say which body it means",
        "plan_reason": None,
        "system_error": (
            "this part has 2 separate bodies ('cube', 'pin'), and the "
            "request does not say which one to change"),
        "executed": False,
        "execution_succeeded": False,
        "execution_failure": None,
        "bodies": [],
        "body_count": 0,
        "raw_text": '{"status": "needs_clarification", "operations": []}',
    }


def _refusal_with(**changes: Any) -> Dict[str, Any]:
    row = correct_refusal_observation()
    row.update(changes)
    return row


#: FAKE REFUSAL: it did not decline at all -- it guessed, and built.
DID_NOT_REFUSE: Dict[str, Any] = _refusal_with(
    outcome_declared="generated", plan_valid=True, operation_count=4,
    model_operation_count=4, executed=True, execution_succeeded=True,
    declared_bodies=["cube", "pin"], questions=[],
    bodies=correct_creation_observation()["bodies"], body_count=2)

#: FAKE CLARIFICATION: `needs_clarification` with an EMPTY questions list
#: and a summary that says nothing. Stage 75 Phase C measured half the
#: baseline clarifications doing exactly this.
ASKED_NOTHING: Dict[str, Any] = _refusal_with(
    questions=[], summary="ambiguous request")

#: NAMED ONE BODY. It refused, so nothing was guessed. It is still not an
#: answer: the person cannot act on it.
NAMED_ONE: Dict[str, Any] = _refusal_with(
    questions=["Which part of it should be taller?"],
    summary="the cube could be taller")

#: THE SYSTEM SAID IT, NOT THE MODEL. Every body name appears -- in
#: `system_error`, the validator's own sentence. Phase A merged the two, so
#: a system message could satisfy a check about what the MODEL named.
ONLY_THE_SYSTEM_NAMED_THEM: Dict[str, Any] = _refusal_with(
    questions=["Which one?"], summary="ambiguous")

#: A QUESTION CARRYING GEOMETRY: `needs_clarification` with a full
#: operations list. The parser rejects it, so the PARSED count is zero
#: exactly when the model emitted the most -- which is why the model's own
#: count is what is read.
CLARIFICATION_WITH_OPERATIONS: Dict[str, Any] = _refusal_with(
    model_operation_count=4, operation_count=0,
    raw_text='{"status": "needs_clarification", "operations": ['
             '{"id": "cube", "type": "box"}, {"id": "pin", "type": "cylinder"},'
             '{"id": "b1", "type": "part"}, {"id": "b2", "type": "part"}]}')

#: BUILT SOMETHING ANYWAY, while declining in words.
REFUSED_BUT_BUILT: Dict[str, Any] = _refusal_with(
    executed=True, execution_succeeded=True,
    bodies=correct_creation_observation()["bodies"], body_count=2)

REFUSAL_TRAPS: Mapping[str, Dict[str, Any]] = {
    "did_not_refuse": DID_NOT_REFUSE,
    "asked_nothing": ASKED_NOTHING,
    "named_one": NAMED_ONE,
    "only_the_system_named_them": ONLY_THE_SYSTEM_NAMED_THEM,
    "clarification_with_operations": CLARIFICATION_WITH_OPERATIONS,
    "refused_but_built": REFUSED_BUT_BUILT,
}


__all__ = [
    "CREATION_TRAPS", "CUBE_PIN_PLAN", "EDIT_TRAPS", "PLANS",
    "REFUSAL_TRAPS", "SUMMARIES", "TRIO_PLAN", "box",
    "correct_creation_observation", "correct_edit_observation",
    "correct_refusal_observation", "cylinder", "declare", "plan_for",
]
