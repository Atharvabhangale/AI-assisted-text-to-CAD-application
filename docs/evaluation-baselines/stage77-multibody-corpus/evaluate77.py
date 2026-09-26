"""Observing and grading Stage 77's broader live multi-body corpus.

The ONLY module here that reads :func:`ground_truth77.expected`. The runner
reaches the requests through :func:`ground_truth77.requests_to_send`, a
narrowed view that carries no expectation at all.

WHAT IT REUSES RATHER THAN REBUILDS, because a second copy of a rule is a
second opinion about what the rule is:

* the EXPORT LADDER is `evaluate76.export_level` -- six rungs, formed from
  the artefact, mutation-tested 28/28 at Stage 76. Stage 77 builds a
  truth-shaped mapping for it and never re-implements a rung.
* the MEASUREMENT OBSERVER is `observe76.measure_probes`, which asks the
  product's own `questions.answer` and reads which body an answer is about
  from the product's own `questions.scope_for`.
* the vocabularies -- the probe kinds, the provenances, the rung names --
  come from `ground_truth76` rather than being spelled again here.

WHAT IT REFUSES TO DO:

* **Pool anything.** Creation, edit, refusal, measurement, aggregate and
  export get six denominators. A model that refuses everything must never
  look good, and :func:`summarise` produces no combined number.
* **Blame a surface for a part that never built.** Measurement and export
  are POST-CONDITIONS. When the build failed there is nothing to measure or
  export, and the verdict is `NOT_ASSESSED` with the reason -- never a
  measurement failure. The Stage 76 handoff names this as the thing the
  join between a `PlanGenerationResult` and the observer must get right.
* **Count a deterministic answer as a model result.** Only
  `MODEL_GENERATED` is evidence about a model, and a source that is not
  `MODEL_GENERATED` fails every strict verdict outright.
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import evaluate76 as EV76
import ground_truth76 as G76
import ground_truth77 as G
import observe76 as O76

#: Why a post-condition has no verdict. Distinct from failing one.
NOT_ASSESSED: str = "NOT_ASSESSED"


# ------------------------------------------------------------- comparing


def _close(measured: Optional[float], truth: float,
           tolerance: float = G.VOLUME_TOLERANCE) -> bool:
    """Relative comparison against a closed form. Never equality."""
    if measured is None:
        return False
    if truth == 0.0:
        return abs(measured) <= tolerance
    return abs(measured - truth) / abs(truth) <= tolerance


def _match_multiset(measured: Sequence[float], truth: Sequence[float],
                    tolerance: float = G.VOLUME_TOLERANCE) -> bool:
    """Whether the measured volumes are the expected ones, in ANY order.

    Which body the model declared first says nothing about the part. Greedy
    pairing is exact when the expected values are far apart relative to the
    tolerance, and correct anyway when two of them are equal.
    """
    if len(measured) != len(truth):
        return False
    remaining = list(measured)
    for want in truth:
        hit = next((i for i, got in enumerate(remaining)
                    if _close(got, want, tolerance)), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def _box_matches(body: Mapping[str, Any], low, high,
                 tolerance: float = G.PLACEMENT_TOLERANCE) -> bool:
    return all(
        math.isclose(body["minimum"][i], low[i], abs_tol=tolerance)
        and math.isclose(body["maximum"][i], high[i], abs_tol=tolerance)
        for i in range(3)
    )


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


def _by_id(bodies: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    """The bodies keyed by ID. Never indexed by position, anywhere."""
    return {str(b["id"]): b for b in bodies}


def _model_words(observation: Mapping[str, Any]) -> str:
    """Everything the MODEL said, lowercased, and nothing the system said.

    `system_error` is the validator's or the provider's sentence, not the
    model's. Stage 75 Phase A merged the two, so a system message could
    satisfy a check about what the MODEL named -- and on a refusal the
    system's own message names every body, which would have made that check
    pass on every attempt.
    """
    parts: List[str] = []
    for key in ("summary", "plan_reason"):
        value = observation.get(key)
        if value:
            parts.append(str(value))
    parts.extend(observation.get("questions") or ())
    return " ".join(parts).lower()


def _operations_the_model_wrote(raw_text: Optional[str]) -> Optional[int]:
    """How many operations the MODEL put in its answer, before any parsing.

    A clarification carrying operations never becomes a parsed plan -- the
    parser rejects it -- so a check reading the PARSED count reports "the
    model emitted no operations" precisely when it emitted the most.
    Stage 75 Phase C measured that: the metric was `False` 0 times while the
    model shipped operations 5 times. **Absent is not zero**: `None` when
    there is no raw text to read.
    """
    if not raw_text:
        return None
    try:
        answer = json.loads(raw_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(answer, Mapping):
        return None
    operations = answer.get("operations")
    return len(operations) if isinstance(operations, list) else 0


# ------------------------------------------------------------- observing


def observe(
    *,
    case_name: str,
    turn_index: int,
    group: str,
    source: str,
    generation: Any,
    execution: Any,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Everything that happened on one turn, as plain data. No judgement.

    ``execution`` may be ``None`` when nothing was built -- a refusal, an
    invalid plan, or a provider error. The observation still records what
    the model said, because a refusal is an answer and has to be gradeable.
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
    written = _operations_the_model_wrote(raw_text)

    return {
        "case": case_name,
        "turn": turn_index,
        "group": group,
        "source": source,
        "is_live_model_result": source in G.COUNTS_AS_MODEL_EVIDENCE,
        "outcome_declared": getattr(
            getattr(generation, "outcome", None), "value", None),
        "structured_output": bool(getattr(
            getattr(generation, "metadata", None), "structured_output",
            False)),
        "fenced": bool(raw_text and raw_text.lstrip().startswith("```")),
        "plan_valid": bool(getattr(validation, "valid", False)),
        "plan_problems": [
            {"code": p.code, "where": p.where, "message": p.message}
            for p in (getattr(validation, "problems", ()) or ())
        ],
        "operations": op_rows,
        "operation_count": len(op_rows),
        # What the MODEL wrote, beside what PARSED. Two different facts.
        "model_operation_count": (
            written if written is not None else len(op_rows)),
        "declared_bodies": declared,
        # --- the model's own words, from where they actually live --------
        "has_plan": plan is not None,
        "questions": (list(getattr(plan, "questions", ()) or ())
                      if plan is not None else None),
        "summary": getattr(plan, "summary", None) if plan is not None else None,
        "plan_reason": getattr(plan, "reason", None) if plan is not None else None,
        # --- what the SYSTEM said, kept apart and never graded -----------
        "system_error": getattr(generation, "error", None),
        "executed": execution is not None,
        "execution_succeeded": bool(getattr(execution, "succeeded", False))
        if execution else False,
        "execution_failure": (
            execution.failure.to_dict()
            if execution is not None and getattr(execution, "failure", None)
            else None),
        "bodies": [
            {
                "id": b.id,
                "features": list(b.features),
                "is_valid": bool(b.measurement.is_valid),
                "solid_count": int(b.measurement.solid_count),
                "volume": float(b.measurement.volume),
                "face_count": int(b.measurement.face_count),
                "edge_count": int(b.measurement.edge_count),
                "minimum": [float(v) for v in b.measurement.minimum],
                "maximum": [float(v) for v in b.measurement.maximum],
            }
            for b in bodies
        ],
        "body_count": len(bodies),
        "raw_text": raw_text,
    }


def probes_for(observation: Mapping[str, Any]) -> Tuple[Dict[str, str], ...]:
    """The measurement probes for a part the MODEL named.

    Stage 76's probes are pinned in its truth because its plans are
    hand-written and its ids are known before anything runs. Here the model
    chooses the ids, so the probe TEXTS cannot exist before the part does --
    they are derived from the bodies that were actually built.

    That makes the per-body probe an ATTRIBUTION check rather than a value
    check: the answer about body X must be X's own measurement. It is
    combined with the creation verdict, which has already compared the whole
    multiset against the closed forms, so the pair says both "the part is
    right" and "the surface attributes it right". Where the REQUEST named
    the ids, :func:`grade_measurement` additionally checks the value against
    the pinned closed form, which is a truth check.
    """
    ids = [str(b["id"]) for b in observation.get("bodies") or ()]
    probes: List[Dict[str, str]] = []
    for body in ids:
        probes.append({
            "name": f"body:{body}",
            "kind": G76.NAMED,
            "text": f"What is the volume of the {body}?",
        })
    if len(ids) > 1:
        probes.append({
            "name": "ambiguous",
            "kind": G76.UNNAMED_SEVERAL,
            "text": "What is the volume?",
        })
        probes.append({
            "name": "total",
            "kind": G76.AGGREGATE_TOTAL,
            "text": "What is the total volume?",
        })
    return tuple(probes)


# ------------------------------------------------------- grading a turn


def _creation_or_edit_checks(
    observation: Mapping[str, Any], truth: Mapping[str, Any],
    turn: Mapping[str, Any], case: Mapping[str, Any],
) -> Dict[str, Optional[bool]]:
    checks: Dict[str, Optional[bool]] = {}
    bodies = list(observation["bodies"])
    by_id = _by_id(bodies)
    tolerance = case["volume_tolerance"]

    checks["plan_valid"] = observation["plan_valid"]
    checks["built"] = observation["execution_succeeded"]
    checks["body_count"] = observation["body_count"] == turn["bodies"]

    declared = list(observation["declared_bodies"])
    if turn["declaration_required"]:
        checks["declared_every_body"] = (
            len(declared) == turn["bodies"]
            and len(set(declared)) == len(declared)
            and set(by_id) == set(declared)
        )
    else:
        # ONE body means no `part` at all. The control that catches the
        # predictable way a multi-body prompt goes wrong.
        checks["declared_nothing_spurious"] = len(declared) == 0

    if turn["body_ids"] is not None:
        checks["body_ids"] = set(by_id) == set(turn["body_ids"])

    if turn["volumes"] is not None:
        checks["volumes"] = _match_multiset(
            [b["volume"] for b in bodies], turn["volumes"], tolerance)

    # Per-id, where the request said which id is which. A multiset check
    # passes on two bodies whose numbers are exchanged; this does not.
    if turn["body_volumes"]:
        checks["body_volumes"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["body_volumes"].items()
        )

    if turn["placements"]:
        places = turn["placements"]
        if "__any__" in places:
            # The request stated the coordinates but not which id gets
            # which, so the boxes are matched as an unordered SET.
            wanted = list(places["__any__"])
            remaining = list(bodies)
            matched = True
            for low, high in wanted:
                hit = next((i for i, b in enumerate(remaining)
                            if _box_matches(b, low, high,
                                            case["placement_tolerance"])),
                           None)
                if hit is None:
                    matched = False
                    break
                remaining.pop(hit)
            checks["placements"] = matched and not remaining
        else:
            checks["placements"] = all(
                body in by_id
                and _box_matches(by_id[body], low, high,
                                 case["placement_tolerance"])
                for body, (low, high) in places.items()
            )

    if turn["disjoint"]:
        checks["bodies_disjoint"] = not any(
            _overlaps(a, b)
            for i, a in enumerate(bodies) for b in bodies[i + 1:])

    # --- the edit halves, and the second is the point --------------------
    if turn["changed"]:
        checks["edit_target_changed"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["changed"].items())
    if turn["unchanged"]:
        checks["other_bodies_untouched"] = all(
            body in by_id and _close(by_id[body]["volume"], volume, tolerance)
            for body, volume in turn["unchanged"].items())
    if case["fixture"]:
        # An edit keeps the part's bodies. A model that renamed one has
        # produced a different part, and every volume check could still
        # pass -- `revision_context` asks it explicitly to keep the ids.
        checks["body_ids_preserved"] = (
            set(by_id) == set(G.FIXTURE_BODIES[case["fixture"]]))

    topology = turn["topology"] or {}
    if "untouched_body_faces" in topology:
        want = topology["untouched_body_faces"]
        checks["untouched_body_intact"] = any(
            b["face_count"] == want for b in bodies)
    if "drilled_body_min_faces" in topology:
        least = topology["drilled_body_min_faces"]
        checks["drilled_body_has_the_hole"] = any(
            b["face_count"] >= least for b in bodies)
    if "bore_diameter" in topology:
        # The drilled body must be able to CONTAIN the hole the request
        # names: at least two of its extents must exceed the diameter. No
        # diameter is expected, only coherence.
        bore = topology["bore_diameter"]
        least = topology.get("drilled_body_min_faces", G.BOX_FACES + 1)
        drilled = [b for b in bodies if b["face_count"] >= least]
        checks["bore_fits_the_body"] = bool(drilled) and all(
            sum(1 for axis in range(3)
                if (b["maximum"][axis] - b["minimum"][axis]) > bore) >= 2
            for b in drilled)
    if topology.get("both_boxes"):
        checks["both_bodies_prismatic"] = all(
            b["face_count"] == G.BOX_FACES for b in bodies)
    if topology.get("both_round"):
        checks["both_bodies_round"] = all(
            b["face_count"] == G.CYLINDER_FACES for b in bodies)
    return checks


def _refusal_checks(
    observation: Mapping[str, Any], case: Mapping[str, Any],
) -> Dict[str, Optional[bool]]:
    checks: Dict[str, Optional[bool]] = {}
    said = _model_words(observation)

    checks["refused"] = observation["outcome_declared"] in (
        "needs_clarification", "unsupported")
    checks["named_the_bodies"] = all(
        body.lower() in said for body in case["refusal_must_name"])
    checks["asked_a_question"] = bool(observation["questions"])
    # Read from the MODEL's answer, not the parsed plan, and asked of the
    # TRUTH rather than hard-coded: `operations_permitted` sat on every
    # Stage 75 refusal case for a whole phase with nothing reading it.
    written = observation.get("model_operation_count",
                              observation["operation_count"])
    checks["emitted_no_operations"] = (
        None if case["operations_permitted"] else written == 0)
    checks["built_nothing"] = observation["body_count"] == 0
    if case["refusal_must_mention"]:
        checks["addressed_the_request"] = all(
            token.lower() in said for token in case["refusal_must_mention"])
    return checks


def grade_turn(observation: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one turn against the immutable truth for its case and index.

    The truth is fetched BY NAME and INDEX. This function never constructs,
    adjusts or infers an expectation.
    """
    case = G.expected(observation["case"])
    turn = G.expected_turn(observation["case"], observation["turn"])

    checks: Dict[str, Optional[bool]] = {}
    # A source that is not MODEL_GENERATED fails outright. A deterministic
    # grammar answering is a perfectly good product outcome and is not
    # evidence about a model; letting one score would make the corpus
    # unable to tell the two apart afterwards.
    checks["model_generated"] = (
        observation["source"] in G.COUNTS_AS_MODEL_EVIDENCE)
    checks["provider_output_valid"] = (
        observation["outcome_declared"] is not None
        and observation["outcome_declared"] != "invalid_model_output")
    checks["structured_output"] = bool(observation["structured_output"])
    checks["not_fenced"] = not observation["fenced"]

    if case["group"] == G.REFUSAL:
        checks.update(_refusal_checks(observation, case))
        metrics = {
            "bodies_expected": len(case["refusal_must_name"]),
            "bodies_named": sum(
                1 for body in case["refusal_must_name"]
                if body.lower() in _model_words(observation)),
            "operations_written": observation.get(
                "model_operation_count", observation["operation_count"]),
            "questions_asked": len(observation.get("questions") or ()),
        }
    else:
        checks.update(_creation_or_edit_checks(observation, case, turn, case))
        metrics = {
            "bodies_expected": turn["bodies"],
            "bodies_built": observation["body_count"],
            "bodies_declared": len(observation["declared_bodies"]),
        }

    applicable = [v for v in checks.values() if v is not None]
    return {
        "case": observation["case"],
        "turn": observation["turn"],
        "group": case["group"],
        "family": case["family"],
        "dimensions": case["dimensions"],
        "checks": checks,
        # REPORTED, never graded. "named one body" and "named none" are
        # different failures with different causes, and collapsing them into
        # the one boolean is what hid the difference in Stage 75 Phase B.
        "metrics": metrics,
        "strict_success": all(applicable),
    }


# ----------------------------------------------- the post-conditions


def grade_measurement(
    observation: Mapping[str, Any],
    rows: Optional[Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Score the measurement probes asked of the part the model built.

    `NOT_ASSESSED` when nothing was built. A part that never existed cannot
    fail a measurement, and recording it as one would put a measurement
    rate underneath a build rate and make the two impossible to read apart.
    """
    case = G.expected(observation["case"])
    turn = G.expected_turn(observation["case"], observation["turn"])
    if not case["measure"]:
        return {"verdict": NOT_ASSESSED, "why": "this case runs no probes"}
    if not observation["execution_succeeded"] or not observation["bodies"]:
        return {"verdict": NOT_ASSESSED,
                "why": "nothing was built, so there was nothing to measure"}
    if rows is None:
        return {"verdict": NOT_ASSESSED, "why": "the probes were not run"}

    by_probe = {str(r.get("probe")): r for r in rows}
    by_id = _by_id(observation["bodies"])
    tolerance = case["volume_tolerance"]
    pinned = turn["body_volumes"] or {}

    checks: Dict[str, Optional[bool]] = {}
    attribution: Dict[str, bool] = {}
    for body, measured in by_id.items():
        row = by_probe.get(f"body:{body}")
        if row is None:
            attribution[body] = False
            continue
        # ATTRIBUTION: answered, about THIS body, said so, MEASURED, and
        # carrying THIS body's own number. Never the number alone -- on
        # CR-05 both bodies measure 27000 and the value cannot tell them
        # apart even in principle.
        attribution[body] = (
            row.get("outcome") == G76.ANSWERED
            and row.get("about") == body
            and row.get("label_is_prefix") is True
            and row.get("provenance") == G76.MEASURED
            and _close(row.get("value"), measured["volume"], tolerance)
        )
    checks["every_body_answered_about_itself"] = all(attribution.values())

    # And where the REQUEST named the ids, the value against the CLOSED
    # FORM rather than against the kernel's own answer. A truth check.
    if pinned:
        checks["named_body_values_match_truth"] = all(
            (by_probe.get(f"body:{body}") or {}).get("outcome") == G76.ANSWERED
            and _close((by_probe.get(f"body:{body}") or {}).get("value"),
                       volume, tolerance)
            for body, volume in pinned.items())

    ambiguous = by_probe.get("ambiguous")
    if ambiguous is not None:
        said = str(ambiguous.get("said") or "").lower()
        checks["ambiguous_question_refused"] = (
            ambiguous.get("outcome") == G76.REFUSED)
        checks["refusal_named_every_body"] = all(
            body.lower() in said for body in by_id)
    return {
        "verdict": "assessed",
        "checks": checks,
        "attribution": attribution,
        "passed": all(v for v in checks.values() if v is not None),
    }


def grade_aggregate(
    observation: Mapping[str, Any],
    rows: Optional[Sequence[Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Score the aggregate probe. Its own denominator, deliberately.

    A total is a different claim from a per-body measurement: nothing put
    the bodies on a scale together, so it must come back `CALCULATED` and
    never `MEASURED`.
    """
    case = G.expected(observation["case"])
    if not case["measure"]:
        return {"verdict": NOT_ASSESSED, "why": "this case runs no probes"}
    if not observation["execution_succeeded"] or not observation["bodies"]:
        return {"verdict": NOT_ASSESSED,
                "why": "nothing was built, so there was nothing to total"}
    if rows is None:
        return {"verdict": NOT_ASSESSED, "why": "the probes were not run"}
    row = next((r for r in rows if str(r.get("probe")) == "total"), None)
    if row is None:
        return {"verdict": NOT_ASSESSED,
                "why": "a single-body part has no aggregate to ask about"}

    total = sum(float(b["volume"]) for b in observation["bodies"])
    checks = {
        "answered": row.get("outcome") == G76.ANSWERED,
        "marked_calculated": row.get("provenance") == G76.CALCULATED,
        "value_is_the_sum": _close(row.get("value"), total,
                                   case["volume_tolerance"]),
        # A single body's number standing in for the total is the
        # substitution this check exists to catch.
        "not_one_body_substituted": not any(
            _close(row.get("value"), float(b["volume"]),
                   case["volume_tolerance"])
            for b in observation["bodies"]
        ) if len(observation["bodies"]) > 1 else None,
    }
    applicable = [v for v in checks.values() if v is not None]
    return {"verdict": "assessed", "checks": checks,
            "expected_total": total, "passed": all(applicable)}


def grade_export(
    observation: Mapping[str, Any],
    row: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Score the STEP export of the part the model built.

    The RUNG comes from `evaluate76.export_level`, unchanged -- six rungs,
    formed from the artefact rather than the writer's own verdict,
    mutation-tested at Stage 76. Stage 77 supplies a truth-shaped mapping
    and re-implements nothing.

    ``export_names`` is the ids the EXECUTOR produced, not the ids truth
    pins, because for most of this corpus the model chooses them. That
    makes the name rung a CONSISTENCY check -- every body in the part
    reached the file under its own name -- which is exactly what catches
    the anonymous-product failure both engines produce when handed a
    compound. Where the request named the ids, `names_match_truth` is
    checked on top, and that one IS a truth check.
    """
    case = G.expected(observation["case"])
    turn = G.expected_turn(observation["case"], observation["turn"])
    if not case["export"]:
        return {"verdict": NOT_ASSESSED, "why": "this case exports nothing"}
    if not observation["execution_succeeded"] or not observation["bodies"]:
        return {"verdict": NOT_ASSESSED,
                "why": "nothing was built, so there was nothing to export"}
    if row is None:
        return {"verdict": NOT_ASSESSED, "why": "the export was not run"}

    ids = [str(b["id"]) for b in observation["bodies"]]
    single = len(ids) == 1
    truth76 = {
        "export_solids": len(ids),
        # The single-body writer puts NO body name in the file -- measured
        # on both engines at Stage 76 -- so a single-body case pins none
        # and its top rung is a weaker claim, which the verdict says.
        "export_names": () if single else tuple(ids),
        "export_volumes": tuple(float(b["volume"])
                                for b in observation["bodies"]),
        "volume_tolerance": case["volume_tolerance"],
        "export_level": G76.LEVEL_F,
        "single_body_writer": single,
        "export_identity": (G76.IDENTITY_NOT_WRITTEN if single
                            else G76.IDENTITY_UNPROVEN),
    }
    level = EV76.export_level({"export": dict(row)}, truth76)
    reached = G76.EXPORT_LEVELS.index(level)

    checks: Dict[str, Optional[bool]] = {
        "reached_the_top_rung": reached >= G76.EXPORT_LEVELS.index(G76.LEVEL_F),
        "writer": (row.get("writer") == "single") == single,
    }
    if case["export_names_pinned"] and turn["body_ids"]:
        found = set(row.get("names_found") or ())
        checks["names_match_truth"] = set(turn["body_ids"]) <= found
    return {
        "verdict": "assessed",
        "level": level,
        "level_means": G76.EXPORT_LEVEL_MEANING[level],
        "checks": checks,
        # Carried on EVERY export verdict so no reader of a recorded run can
        # take the top rung for more than it is. Never counted as a failure.
        "identity_state": truth76["export_identity"],
        "identity_note": G76.IDENTITY_BINDING_NOTE if not single else (
            "this writer puts no body name in the file at all, so nothing "
            "here is evidence about identity in either direction"),
        "passed": all(v for v in checks.values() if v is not None),
    }


# ------------------------------------------------------------ labelling


def outcome_label(observation: Mapping[str, Any]) -> str:
    """Which of the five this attempt is. Fails CLOSED on anything else.

    Stage 75 Phase C found `outcome_label` naming every unrecognised
    outcome `MODEL_GENERATED` by falling through, so `model_error` was
    labelled a model result.
    """
    source = observation.get("source")
    if source not in G.OUTCOMES:
        return G.PROVIDER_ERROR
    if source != G.MODEL_GENERATED:
        return source
    declared = observation.get("outcome_declared")
    if declared in (None, "model_error"):
        return G.PROVIDER_ERROR
    return G.MODEL_GENERATED


# --------------------------------------------------------- classifying


def classify(
    observation: Mapping[str, Any],
    graded: Mapping[str, Any],
    measurement: Optional[Mapping[str, Any]] = None,
    aggregate: Optional[Mapping[str, Any]] = None,
    export: Optional[Mapping[str, Any]] = None,
) -> Tuple[str, ...]:
    """Every failure code that applies. Empty for a clean turn.

    `P:export_identity_unproven` is attached to every multi-body export and
    is INFORMATIONAL: it records an honest limit, not a defect, and
    `G.FAILURE_CODES` excludes it from every failure count.
    """
    codes: List[str] = []
    case = G.expected(observation["case"])
    turn = G.expected_turn(observation["case"], observation["turn"])
    checks = graded["checks"]

    if case["group"] == G.REFUSAL:
        if checks.get("refused") is False or checks.get("built_nothing") is False:
            codes.append(G.J_BAD_REFUSAL)
        for name in ("named_the_bodies", "asked_a_question",
                     "emitted_no_operations", "addressed_the_request"):
            if checks.get(name) is False:
                codes.append(G.K_BAD_CLARIFICATION)
                break
    else:
        # A plan the VALIDATOR rejected, and a plan the EXECUTOR refused
        # as not being a legal part, are both "the plan was not a legal
        # plan" and share the one code. Measured: two solids with no `part`
        # passes the validator with no problems at all and is refused by
        # `executor.finished` with `multiple_solids`, which is why reading
        # only `plan_valid` would leave that failure unnamed.
        if (checks.get("plan_valid") is False
                or checks.get("declared_every_body") is False
                or checks.get("declared_nothing_spurious") is False):
            codes.append(G.I_INVALID_PLAN)
        built = observation["body_count"]
        wanted = turn["bodies"]
        if built < wanted:
            # One body where several were asked for, carrying their total,
            # is a FUSION rather than a missing body -- a different cause
            # and a different fix.
            total = sum(float(b["volume"]) for b in observation["bodies"])
            fused = (built == 1 and turn["volumes"] is not None
                     and _close(total, sum(turn["volumes"]),
                                case["volume_tolerance"]))
            codes.append(G.H_UNWANTED_FUSION if fused else G.A_MISSING_BODY)
        elif built > wanted:
            codes.append(G.B_EXTRA_BODY)
        if checks.get("body_ids") is False or \
                checks.get("body_ids_preserved") is False:
            codes.append(G.C_WRONG_IDENTITY)
        if checks.get("edit_target_changed") is False:
            codes.append(G.D_WRONG_TARGET)
        if checks.get("other_bodies_untouched") is False:
            codes.append(G.E_CROSS_BODY_EDIT)
        if checks.get("placements") is False:
            codes.append(G.F_WRONG_PLACEMENT)
        for name in ("volumes", "body_volumes", "bore_fits_the_body",
                     "untouched_body_intact", "drilled_body_has_the_hole",
                     "both_bodies_prismatic", "both_bodies_round"):
            if checks.get(name) is False:
                codes.append(G.G_WRONG_DIMENSION)
                break
        # Bodies that interpenetrate have been merged in space, which is
        # a fusion the request never asked for. Over-declaring is NOT a
        # fusion and belongs with the plan-legality failures above.
        if checks.get("bodies_disjoint") is False:
            codes.append(G.H_UNWANTED_FUSION)

    if measurement and measurement.get("verdict") == "assessed" \
            and not measurement.get("passed"):
        codes.append(G.L_MEASUREMENT_ATTRIBUTION)
    if aggregate and aggregate.get("verdict") == "assessed" \
            and not aggregate.get("passed"):
        codes.append(G.M_AGGREGATE_ERROR)
    if export and export.get("verdict") == "assessed":
        if export.get("identity_state") == G76.IDENTITY_UNPROVEN:
            codes.append(G.P_EXPORT_IDENTITY_UNPROVEN)
        if not export.get("passed"):
            level = export.get("level")
            if level in (G76.LEVEL_A, G76.LEVEL_B, G76.LEVEL_C, G76.LEVEL_D):
                codes.append(G.N_EXPORT_OMISSION)
            else:
                codes.append(G.O_EXPORT_GEOMETRY)

    real = [c for c in codes if c in G.FAILURE_CODES]
    if not graded["strict_success"] and not real:
        codes.append(G.Q_OTHER)
    # Stable order, and each code once.
    return tuple(c for c in G.TAXONOMY if c in set(codes))


# ------------------------------------------------------------ summarising


def _rate(rows: Sequence[Mapping[str, Any]], key: str = "strict_success"):
    return {"passed": sum(1 for r in rows if r.get(key)), "of": len(rows)}


def summarise(attempts: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Six denominators, and deliberately no combined number.

    Creation, edit, refusal, measurement, aggregate and export are six
    different quantities. A rate that mixes a refusal rate with a build
    rate is how a model that refuses everything scores well, and a rate
    that mixes a measurement rate with a build rate hides which of the two
    failed. Stage 75's `summarise` refuses a combined number for the same
    reason and so does Stage 76's.
    """
    turns = [t for a in attempts for t in a["turns"]]
    by_group: Dict[str, List[Mapping[str, Any]]] = {}
    for turn in turns:
        by_group.setdefault(turn["verdict"]["group"], []).append(turn)

    def _post(name: str):
        rows = [t[name] for t in turns
                if t.get(name) and t[name].get("verdict") == "assessed"]
        skipped = sum(1 for t in turns
                      if t.get(name) and t[name].get("verdict") == NOT_ASSESSED)
        return {"passed": sum(1 for r in rows if r.get("passed")),
                "of": len(rows), "not_assessed": skipped}

    # An EDIT case is only a success when EVERY turn of it is. Reported
    # beside the per-turn rate rather than instead of it: a chain that fails
    # on its third turn and a chain that fails on its first are the same
    # per-attempt number and very different findings.
    edit_attempts = [a for a in attempts
                     if G.expected(a["case"])["group"] == G.EDIT]
    codes: Dict[str, int] = {code: 0 for code in G.TAXONOMY}
    for turn in turns:
        for code in turn.get("codes", ()):
            codes[code] = codes.get(code, 0) + 1

    per_case: Dict[str, Any] = {}
    for attempt in attempts:
        entry = per_case.setdefault(attempt["case"], {"passed": 0, "of": 0})
        entry["of"] += 1
        entry["passed"] += 1 if attempt["strict_success"] else 0

    per_dimension: Dict[str, Any] = {}
    for attempt in attempts:
        for dimension in G.expected(attempt["case"])["dimensions"]:
            entry = per_dimension.setdefault(
                dimension, {"passed": 0, "of": 0,
                            "what": G.DIMENSIONS[dimension]})
            entry["of"] += 1
            entry["passed"] += 1 if attempt["strict_success"] else 0

    per_family: Dict[str, Any] = {}
    for attempt in attempts:
        family = G.expected(attempt["case"])["family"]
        entry = per_family.setdefault(family, {"passed": 0, "of": 0})
        entry["of"] += 1
        entry["passed"] += 1 if attempt["strict_success"] else 0

    return {
        "attempts": len(attempts),
        "live_calls": len(turns),
        "live_model_results": sum(
            1 for t in turns
            if t["observation"].get("is_live_model_result")),
        # --- the three groups, never pooled -----------------------------
        "creation": _rate([t["verdict"] for t in by_group.get(G.CREATION, ())]),
        "edit_turns": _rate([t["verdict"] for t in by_group.get(G.EDIT, ())]),
        "edit_chains": {"passed": sum(1 for a in edit_attempts
                                      if a["strict_success"]),
                        "of": len(edit_attempts)},
        "refusal": _rate([t["verdict"] for t in by_group.get(G.REFUSAL, ())]),
        # --- the three post-conditions, never pooled with the above -----
        "measurement": _post("measurement"),
        "aggregate": _post("aggregate"),
        "export": _post("export"),
        "export_levels": {
            level: sum(1 for t in turns
                       if (t.get("export") or {}).get("level") == level)
            for level in G76.EXPORT_LEVELS
        },
        "structured_output": _rate(
            [{"strict_success": t["observation"]["structured_output"]}
             for t in turns]),
        "fenced": sum(1 for t in turns if t["observation"]["fenced"]),
        "codes": codes,
        "per_case": per_case,
        "per_dimension": per_dimension,
        "per_family": per_family,
        "combined_rate": None,
        "why_no_combined_rate": (
            "creation, edit, refusal, measurement, aggregate and export are "
            "six different quantities. Pooling a refusal rate with a build "
            "rate is how a model that refuses everything scores well"),
    }


__all__ = [
    "NOT_ASSESSED", "classify", "grade_aggregate", "grade_export",
    "grade_measurement", "grade_turn", "observe", "outcome_label",
    "probes_for", "summarise",
]
