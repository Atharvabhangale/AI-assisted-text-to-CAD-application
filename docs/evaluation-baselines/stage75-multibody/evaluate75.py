"""Stage 75 multi-body evaluator: observe, grade, classify.

Why this exists rather than reusing Stage 68's arena
----------------------------------------------------
Stage 68's evaluator reads ``execution.bodies[0].measurement`` (arena.py:106).
On a one-body part that is correct and total. On a TWO-body part it silently
grades the part by its first body and reports a pass -- the exact failure
``ExecutionResult.part`` refuses to commit, which the executor calls "the
Stage 62 bug by another name". ``stage48_capability_evaluation._measure``
likewise returns a body count and nothing else when bodies != 1, and
``harness.py`` cannot score a graph-executed build at all.

So every existing instrument in the project would either mis-grade a
multi-body case or decline to grade it. None is reusable, and reusing one
would manufacture successes. This module grades **every body**, as an
unordered multiset, and never indexes ``bodies[0]``.

The three-way split is Stage 68's and is kept deliberately
-----------------------------------------------------------
* :func:`observe` -- read what happened. No judgement.
* :func:`grade` -- compare an observation to ``ground_truth75.expected(name)``.
* :func:`classify` -- name the failure from the taxonomy.

Truth flows one way. ``grade`` calls ``expected(case_name)``; it cannot be
handed a truth and it cannot build one from what it observed.

Strict success
--------------
A case counts only if EVERY applicable criterion holds (Stage 75 brief,
Phase 5). Total volume alone is never sufficient -- two bodies fused into one
have exactly the total volume of two bodies apart, so a volume-only grader
scores the single most important multi-body failure as a pass.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import ground_truth75 as G

# --------------------------------------------------------------- observing


def _measurement(body: Any) -> Dict[str, Any]:
    m = body.measurement
    return {
        "id": body.id,
        "features": list(body.features),
        "is_valid": bool(m.is_valid),
        "solid_count": int(m.solid_count),
        "volume": float(m.volume),
        "face_count": int(m.face_count),
        "edge_count": int(m.edge_count),
        "minimum": tuple(float(v) for v in m.minimum),
        "maximum": tuple(float(v) for v in m.maximum),
    }


def observe(
    *,
    case_name: str,
    generation: Any,
    execution: Any,
    schema_fingerprint: str,
    schema_name: str,
    prompt_version: str,
    prompt_fingerprint: str,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything that happened, as plain data. No judgement of any kind.

    ``execution`` may be ``None`` when nothing was built -- a refusal, an
    invalid plan, or a provider error. The observation still records what the
    model said, because a refusal is an answer and has to be gradeable.
    """
    plan = getattr(generation, "plan", None)
    operations = list(getattr(plan, "operations", ()) or ())

    declared: List[str] = []
    op_rows: List[Dict[str, Any]] = []
    for op in operations:
        kind = getattr(op, "TYPE", None)
        row = {"id": getattr(op, "id", None), "type": kind}
        target = getattr(op, "target", None)
        if target is not None:
            row["target"] = target
        op_rows.append(row)
        if kind == "part":
            declared.append(target)

    validation = getattr(generation, "plan_validation", None)
    bodies = list(getattr(execution, "bodies", ()) or ()) if execution else []

    return {
        "case": case_name,
        "model": G.MODEL,
        "schema_name": schema_name,
        "schema_fingerprint": schema_fingerprint,
        "prompt_version": prompt_version,
        "prompt_fingerprint": prompt_fingerprint,
        "outcome_declared": getattr(
            getattr(generation, "outcome", None), "value", None
        ),
        "structured_output": bool(
            getattr(getattr(generation, "metadata", None),
                    "structured_output", False)
        ),
        "plan_valid": bool(getattr(validation, "valid", False)),
        "plan_problems": [
            {"code": p.code, "where": p.where, "message": p.message}
            for p in (getattr(validation, "problems", ()) or ())
        ],
        "operations": op_rows,
        "operation_types": [r["type"] for r in op_rows],
        "operation_count": len(op_rows),
        "declared_bodies": declared,
        # --- what the MODEL said, read from where it actually lives --------
        #
        # Phase A read `generation.questions`. `PlanGenerationResult` has no
        # such attribute -- the model's own words are on `result.plan` --
        # so `getattr(..., ())` silently returned an empty tuple for all 64
        # calls, including four whose raw answer carried a populated
        # `questions` list. A default that hides a misspelt field is worse
        # than a crash: the run completed and reported 0/16.
        #
        # `None` and `[]` are now different facts. `None` means there is no
        # plan to have said anything; `[]` means the model had the field
        # available and left it empty. Phase A could not tell those apart.
        "has_plan": plan is not None,
        "questions": (
            list(getattr(plan, "questions", ()) or ()) if plan is not None
            else None
        ),
        "summary": getattr(plan, "summary", None) if plan is not None else None,
        "plan_reason": getattr(plan, "reason", None) if plan is not None else None,
        # --- what the SYSTEM said, kept apart -------------------------------
        #
        # `result.error` is the validator's or the provider's message, not
        # the model's. Phase A merged it into the refusal text under the key
        # `reason`, so a system sentence could have satisfied a check about
        # what the MODEL named. Separate keys, and only the model's words
        # are graded.
        "system_error": getattr(generation, "error", None),
        "executed": execution is not None,
        "execution_succeeded": bool(getattr(execution, "succeeded", False))
        if execution else False,
        "execution_failure": (
            execution.failure.to_dict()
            if execution is not None and getattr(execution, "failure", None)
            else None
        ),
        "bodies": [_measurement(b) for b in bodies],
        "body_count": len(bodies),
        "raw_text": raw_text,
    }


# ----------------------------------------------------------------- grading


def _close(measured: float, truth: float) -> bool:
    """Relative comparison against a closed form. Never equality."""
    if truth == 0.0:
        return abs(measured) <= G.VOLUME_TOLERANCE
    return abs(measured - truth) / abs(truth) <= G.VOLUME_TOLERANCE


def _match_multiset(
    measured: Sequence[float], truth: Sequence[float]
) -> bool:
    """Whether the measured volumes are the expected ones, in any order.

    Order is not meaning: which body the model declared first says nothing
    about the part. Greedy pairing is exact here because the expected volumes
    are far apart relative to the tolerance.
    """
    if len(measured) != len(truth):
        return False
    remaining = list(measured)
    for want in truth:
        hit = next((i for i, got in enumerate(remaining) if _close(got, want)),
                   None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _model_words(observation: Mapping[str, Any]) -> str:
    """Everything the MODEL said, lowercased, and nothing the system said.

    `summary` is required by the contract and `questions` is optional, so a
    model that explains itself in the summary has still said it. They are
    joined for the naming check and counted separately for the
    question-field check -- conflating those two facts is what Phase A did.
    """
    parts: List[str] = []
    for key in ("summary", "plan_reason"):
        value = observation.get(key)
        if value:
            parts.append(str(value))
    parts.extend(observation.get("questions") or ())
    return " ".join(parts).lower()


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    """Whether two bodies' bounding boxes intersect with real volume.

    Sound for disjointness and used for nothing finer. Touching faces are
    not an overlap, so a strict comparison on each axis is correct.
    """
    for axis in range(3):
        if a["maximum"][axis] <= b["minimum"][axis]:
            return False
        if b["maximum"][axis] <= a["minimum"][axis]:
            return False
    return True


def grade(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one observation against the immutable truth for its case.

    The truth is fetched BY NAME from :func:`ground_truth75.expected`. This
    function never constructs, adjusts or infers an expectation.
    """
    truth = G.expected(observation["case"])
    checks: Dict[str, Optional[bool]] = {}

    # 1. the provider produced usable output at all
    checks["provider_output_valid"] = (
        observation["outcome_declared"] is not None
        and observation["outcome_declared"] != "invalid_model_output"
    )

    if truth["group"] == G.REFUSAL:
        # A refusal is an answer, and must be the RIGHT answer: it must
        # decline, it must say which bodies exist, and it must not smuggle
        # geometry in alongside. A refusal that names nothing is a shrug; a
        # guess is worse than either; and a "clarification" carrying
        # operations is an edit wearing a question's label.
        declined = observation["outcome_declared"] in (
            "needs_clarification", "unsupported",
        )
        # ONLY the model's own words. `system_error` is the validator's
        # sentence and is deliberately not consulted -- Phase A merged the
        # two, so a system message could have satisfied a check about what
        # the model named.
        said = _model_words(observation)
        named = all(
            body.lower() in said for body in (truth["refusal_must_name"] or ())
        )
        checks["refused"] = declined
        checks["named_the_bodies"] = named
        checks["built_nothing"] = observation["body_count"] == 0
        # Reported separately from `named_the_bodies`: using the field the
        # contract provides is a different fact from saying the right thing
        # in prose, and Phase A could see neither.
        checks["asked_a_question"] = bool(observation["questions"])
        checks["emitted_no_operations"] = observation["operation_count"] == 0
        if truth["refusal_question_must_mention"]:
            checks["question_addressed_the_request"] = all(
                token.lower() in said
                for token in truth["refusal_question_must_mention"]
            )
        applicable = [v for v in checks.values() if v is not None]
        return {
            "case": observation["case"], "group": G.REFUSAL,
            "checks": checks, "strict_success": all(applicable),
        }

    # --- creation ---------------------------------------------------------
    checks["plan_valid"] = observation["plan_valid"]
    checks["built"] = observation["execution_succeeded"]

    bodies = observation["bodies"]
    checks["body_count"] = observation["body_count"] == truth["bodies"]

    # declaration: required exactly when more than one body stands (P34)
    declared = observation["declared_bodies"]
    if truth["declaration_required"]:
        checks["declared_every_body"] = (
            len(declared) == truth["bodies"]
            and len(set(declared)) == len(declared)
            and {b["id"] for b in bodies} == set(declared)
        )
    else:
        checks["declared_nothing_spurious"] = len(declared) == 0

    # ids, only where the request named them
    if truth["body_ids"] is not None:
        checks["body_ids"] = (
            {b["id"] for b in bodies} == set(truth["body_ids"])
        )

    # geometry as an unordered multiset
    if truth["volumes"] is not None:
        checks["volumes"] = _match_multiset(
            [b["volume"] for b in bodies], truth["volumes"]
        )

    # an edit must change one body and leave the other alone
    if truth["edited_body_volume"] is not None:
        vols = [b["volume"] for b in bodies]
        checks["edited_body"] = any(
            _close(v, truth["edited_body_volume"]) for v in vols
        )
        checks["other_body_untouched"] = any(
            _close(v, truth["unchanged_body_volume"]) for v in vols
        )

    if truth["disjoint"]:
        checks["bodies_disjoint"] = not any(
            _overlaps(a, b)
            for i, a in enumerate(bodies) for b in bodies[i + 1:]
        )

    topology = truth["topology"] or {}
    if "untouched_body_faces" in topology:
        want = topology["untouched_body_faces"]
        drilled_min = topology["drilled_body_min_faces"]
        faces = sorted(b["face_count"] for b in bodies)
        checks["untouched_body_intact"] = want in faces
        checks["drilled_body_has_the_hole"] = any(
            f >= drilled_min and f != want for f in faces
        ) or faces.count(want) < len(faces)
    if "bore_diameter" in topology:
        # The drilled body must be able to CONTAIN the hole the request
        # names. Checked on the bounding box: a solid with a bore of
        # diameter d through it must measure more than d across in the two
        # axes perpendicular to the bore, so at least two extents must
        # exceed it. No diameter is expected, only coherence.
        bore = topology["bore_diameter"]
        drilled = [b for b in bodies
                   if b["face_count"] >= topology["drilled_body_min_faces"]]
        checks["bore_fits_the_body"] = bool(drilled) and all(
            sum(1 for axis in range(3)
                if (b["maximum"][axis] - b["minimum"][axis]) > bore) >= 2
            for b in drilled
        )
    if topology.get("both_boxes"):
        checks["both_bodies_prismatic"] = all(
            b["face_count"] == G.BOX_FACES for b in bodies
        )
    if "extents_present" in topology:
        wanted = topology["extents_present"]
        extents = [
            tuple(round(b["maximum"][i] - b["minimum"][i], 6) for i in range(3))
            for b in bodies
        ]
        checks["stated_extents_present"] = all(
            any(any(math.isclose(e, w, rel_tol=1e-9) for e in triple)
                for triple in extents)
            for w in wanted
        )

    applicable = [v for v in checks.values() if v is not None]
    return {
        "case": observation["case"], "group": G.CREATION,
        "checks": checks,
        "strict_success": all(applicable),
    }


# -------------------------------------------------------------- classifying


def classify(
    observation: Mapping[str, Any], graded: Mapping[str, Any]
) -> Tuple[str, ...]:
    """Every failure code that applies. Empty for a strict success."""
    if graded["strict_success"]:
        return ()

    truth = G.expected(observation["case"])
    checks = graded["checks"]
    codes: List[str] = []

    if not checks.get("provider_output_valid", True):
        codes.append(G.K_INVALID_PLAN)
    if graded["group"] == G.REFUSAL:
        # `I` is "it did not decline, or it built something, or it smuggled
        # operations in" -- the refusal itself is wrong. `J` is "it declined
        # correctly but the clarification does not do its job" -- no names,
        # or the question field left empty. Phase A had one code for both
        # and could not tell a guess from a vague question.
        if (not checks.get("refused")
                or not checks.get("built_nothing")
                or not checks.get("emitted_no_operations")):
            codes.append(G.I_BAD_REFUSAL)
        if (not checks.get("named_the_bodies")
                or not checks.get("asked_a_question")
                or checks.get("question_addressed_the_request") is False):
            codes.append(G.J_BAD_CLARIFICATION)
        return tuple(dict.fromkeys(codes)) or (G.L_OTHER,)

    if not checks.get("plan_valid", True):
        codes.append(G.K_INVALID_PLAN)

    want, got = truth["bodies"], observation["body_count"]
    if not observation["execution_succeeded"]:
        # Nothing was built, so there is no body count to reason about and
        # no fusion to call unwanted. Phase A reached the `got < want` arm
        # here and, because a `union` was in the plan, reported
        # H:unwanted_fusion 8/8 for M8 -- where the real event was that the
        # build FAILED. A code for a shape that does not exist is noise.
        codes.append(G.K_INVALID_PLAN if observation["executed"] else G.L_OTHER)
    elif got < want:
        # One body where two were asked for, with a union in the plan, is a
        # fusion the request never asked for -- a different fact from a body
        # that was simply never created.
        codes.append(
            G.H_UNWANTED_FUSION if "union" in observation["operation_types"]
            else G.A_MISSING_BODY
        )
    elif got > want:
        codes.append(G.B_EXTRA_BODY)

    declared = observation["declared_bodies"]
    if len(declared) != len(set(declared)):
        codes.append(G.B_EXTRA_BODY)
    if checks.get("body_ids") is False:
        codes.append(G.C_WRONG_IDENTITY)
    if checks.get("other_body_untouched") is False:
        codes.append(G.E_CROSS_BODY_EDIT)
    if checks.get("edited_body") is False:
        codes.append(G.D_WRONG_TARGET)
    if checks.get("bodies_disjoint") is False:
        codes.append(G.F_WRONG_PLACEMENT)
    for name in ("volumes", "stated_extents_present", "both_bodies_prismatic",
                 "untouched_body_intact", "drilled_body_has_the_hole",
                 "bore_fits_the_body"):
        if checks.get(name) is False:
            codes.append(G.G_WRONG_DIMENSIONS)
            break
    if checks.get("declared_every_body") is False:
        codes.append(G.A_MISSING_BODY if got == want else G.L_OTHER)
    if checks.get("declared_nothing_spurious") is False:
        codes.append(G.B_EXTRA_BODY)

    return tuple(dict.fromkeys(codes)) or (G.L_OTHER,)


def outcome_label(observation: Mapping[str, Any]) -> str:
    """Stage 64's five labels. Only MODEL_GENERATED may score as success."""
    declared = observation["outcome_declared"]
    if declared is None:
        return G.PROVIDER_ERROR
    if declared in ("needs_clarification", "unsupported"):
        return G.REFUSED
    if declared == "invalid_model_output":
        return G.PROVIDER_ERROR
    return G.MODEL_GENERATED


def summarise(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Per-case and per-group rates, with the two groups never pooled."""
    per_case: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        entry = per_case.setdefault(
            row["case"], {"calls": 0, "strict": 0, "codes": {}, "labels": {}}
        )
        entry["calls"] += 1
        entry["strict"] += 1 if row["strict_success"] else 0
        for code in row.get("codes", ()):
            entry["codes"][code] = entry["codes"].get(code, 0) + 1
        label = row.get("label", G.L_OTHER)
        entry["labels"][label] = entry["labels"].get(label, 0) + 1
    for name, entry in per_case.items():
        entry["rate"] = entry["strict"] / entry["calls"] if entry["calls"] else 0.0
        entry["group"] = G.CASES_BY_NAME[name].group
    groups: Dict[str, Dict[str, int]] = {}
    for name, entry in per_case.items():
        bucket = groups.setdefault(entry["group"], {"calls": 0, "strict": 0})
        bucket["calls"] += entry["calls"]
        bucket["strict"] += entry["strict"]
    for bucket in groups.values():
        bucket["rate"] = (
            bucket["strict"] / bucket["calls"] if bucket["calls"] else 0.0
        )
    return {"per_case": per_case, "per_group": groups}


__all__ = ["classify", "grade", "observe", "outcome_label", "summarise"]
