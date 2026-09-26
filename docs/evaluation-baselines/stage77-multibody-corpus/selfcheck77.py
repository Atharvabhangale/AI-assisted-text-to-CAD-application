"""The offline preflight: every expectation checked against the real kernel.

**This calls no model**, and it is the most valuable thing in the stage.
Every creation and edit turn has a DEVELOPER-WRITTEN reference plan here.
The preflight builds each one, observes it through the same observer the
live run uses, grades it through the same grader, and requires a strict
success.

Two things that buys, both of which a stage has paid for by missing them:

1. **The expectations are proved to agree with the kernel before a model is
   blamed for disagreeing with them.** A closed form that is wrong by a
   millimetre makes a correct model look broken, and the run would record
   it as a model failure.
2. **The grader is proved able to say YES.** Every trap test asserts that
   something FAILS; without a control that passes, a grader which rejects
   everything satisfies all of them.

The reference plans are not a corpus and are never scored as one. Every
result they produce is `DETERMINISTIC` and says nothing whatever about a
model -- `evaluate77.grade_turn` would refuse them anyway, because its
first check is that the source is `MODEL_GENERATED`.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan
from cad_experimental.validation import validate_plan

import evaluate77 as EV
import fixtures77 as F
import ground_truth77 as G
import observe76 as O76

box, cyl, part = F.box, F.cylinder, F.declare


def _plan(summary: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"status": "generated", "summary": summary,
            "operations": operations}


def _bore(op_id: str, target: str, diameter: float,
          position: Mapping[str, float], axis: str = "+Z") -> Dict[str, Any]:
    return {"id": op_id, "type": "through_hole", "target": target,
            "parameters": {"diameter": diameter, "position": dict(position),
                           "axis": axis}}


#: One reference plan per turn. Keyed by (case, turn index).
REFERENCE: Mapping[Tuple[str, int], Dict[str, Any]] = {
    ("CR-01", 0): _plan("cube and pin", [
        box("cube", 40, 40, 40),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        part("b1", "cube"), part("b2", "pin")]),
    ("CR-02", 0): _plan("base and post", [
        box("base", 40, 40, 40),
        cyl("post", 20, 30, {"x": 60, "y": 20, "z": 0}),
        part("b1", "base"), part("b2", "post")]),
    ("CR-03", 0): _plan("two cylinders", [
        cyl("big", 30, 20, {"x": 20, "y": 20, "z": 0}),
        cyl("small", 10, 50, {"x": 60, "y": 20, "z": 0}),
        part("b1", "big"), part("b2", "small")]),
    ("CR-04", 0): _plan("plate and cube", [
        box("plate", 50, 40, 10),
        box("cube", 20, 20, 20, {"x": 70, "y": 0, "z": 0}),
        part("b1", "plate"), part("b2", "cube")]),
    ("CR-05", 0): _plan("two identical cubes", [
        box("left", 30, 30, 30),
        box("right", 30, 30, 30, {"x": 60, "y": 0, "z": 0}),
        part("b1", "left"), part("b2", "right")]),
    ("CR-06", 0): _plan("plate, boss, rod", [
        box("plate", 60, 40, 8),
        cyl("boss", 16, 20, {"x": 80, "y": 20, "z": 0}),
        cyl("rod", 10, 40, {"x": 120, "y": 20, "z": 0}),
        part("b1", "plate"), part("b2", "boss"), part("b3", "rod")]),
    ("CR-07", 0): _plan("bored plate and cube", [
        box("plate", 50, 50, 10),
        _bore("hole", "plate", 8, {"x": 25, "y": 25, "z": 0}),
        box("cube", 20, 20, 20, {"x": 70, "y": 0, "z": 0}),
        part("b1", "plate"), part("b2", "cube")]),
    ("CR-08", 0): _plan("one fused body", [
        box("pad", 40, 40, 40),
        box("lug", 20, 20, 20, {"x": 30, "y": 0, "z": 0}),
        {"id": "fuse", "type": "union", "target": "pad", "tools": ["lug"]}]),
    ("CR-09", 0): _plan("two placed boxes", [
        box("a", 50, 40, 10),
        box("b", 20, 20, 20, {"x": 70, "y": 0, "z": 0}),
        part("b1", "a"), part("b2", "b")]),
    ("CR-10", 0): _plan("five cubes", [
        box("c0", 10, 10, 10),
        box("c1", 10, 10, 10, {"x": 20, "y": 0, "z": 0}),
        box("c2", 10, 10, 10, {"x": 40, "y": 0, "z": 0}),
        box("c3", 10, 10, 10, {"x": 60, "y": 0, "z": 0}),
        box("c4", 10, 10, 10, {"x": 80, "y": 0, "z": 0}),
        part("d0", "c0"), part("d1", "c1"), part("d2", "c2"),
        part("d3", "c3"), part("d4", "c4")]),

    # --- the edits: the fixture's plan, revised ------------------------
    ("ED-01", 0): _plan("the cube is 50 along X", [
        box("cube", 50, 40, 40),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-02", 0): _plan("the cylinder is bored", [
        box("cube", 40, 40, 40),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        _bore("bore", "pin", 6, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-05", 0): _plan("the cylinder is 50 long", [
        box("cube", 40, 40, 40),
        cyl("pin", 20, 50, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-03", 0): _plan("the cube is 60 along X", [
        box("cube", 60, 40, 40),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-03", 1): _plan("and bored through Z", [
        box("cube", 60, 40, 40),
        _bore("bore", "cube", 10, {"x": 30, "y": 20, "z": 0}),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-04", 0): _plan("the cube is 60 along X", [
        box("cube", 60, 40, 40),
        cyl("pin", 20, 30, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-04", 1): _plan("and the cylinder is 50 long", [
        box("cube", 60, 40, 40),
        cyl("pin", 20, 50, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
    ("ED-04", 2): _plan("and the cube is bored", [
        box("cube", 60, 40, 40),
        _bore("bore", "cube", 10, {"x": 30, "y": 20, "z": 0}),
        cyl("pin", 20, 50, {"x": 100, "y": 20, "z": 0}),
        part("body_cube", "cube"), part("body_pin", "pin")]),
}


class _Plan:
    """The shape `evaluate77.observe` reads a generation result through.

    Deliberately a stand-in rather than a real `PlanGenerationResult`: the
    preflight is about the EXPECTATIONS and the GRADER, and building a
    provider result to test a closed form would test the provider.
    """

    def __init__(self, parsed):
        self.operations = parsed.operations
        self.summary = "reference plan"
        self.reason = None
        self.questions = []


class _Outcome:
    value = "generated"


class _Metadata:
    structured_output = True


class _Generation:
    def __init__(self, parsed, verdict):
        self.plan = _Plan(parsed)
        self.plan_validation = verdict
        self.outcome = _Outcome()
        self.metadata = _Metadata()
        self.error = None
        self.raw_text = None


def check_turn(case_name: str, index: int, engine) -> Dict[str, Any]:
    """Build one reference turn, observe it, and grade it."""
    reference = REFERENCE.get((case_name, index))
    if reference is None:
        return {"case": case_name, "turn": index, "ok": False,
                "why": "no reference plan"}
    parsed = parse_plan(dict(reference))
    verdict = validate_plan(parsed)
    execution = execute_plan(parsed, part_name="stage77", backend=engine) \
        if verdict.valid else None

    observation = EV.observe(
        case_name=case_name, turn_index=index,
        group=G.expected(case_name)["group"],
        # The honest label, and the reason this preflight can never be
        # mistaken for a result: `grade_turn`'s first check is the source,
        # so every row below FAILS on it by construction.
        source=G.DETERMINISTIC,
        generation=_Generation(parsed, verdict), execution=execution,
        raw_text=None)

    graded = EV.grade_turn(observation)
    # Every check EXCEPT the source one, which a deterministic plan cannot
    # satisfy and is not being asked to. This is the only place that
    # distinction is made, and it is made explicitly rather than by
    # pretending the reference plan came from a model.
    checks = {k: v for k, v in graded["checks"].items()
              if k != "model_generated"}
    geometry_ok = all(v for v in checks.values() if v is not None)

    row: Dict[str, Any] = {
        "case": case_name, "turn": index, "ok": geometry_ok,
        "plan_valid": verdict.valid,
        "problems": sorted({p.code for p in verdict.problems}),
        "failed": [k for k, v in checks.items() if v is False],
        "bodies": {b["id"]: b["volume"] for b in observation["bodies"]},
    }

    case = G.expected(case_name)
    if case["measure"] and observation["execution_succeeded"]:
        rows = O76.measure_probes(EV.probes_for(observation),
                                  dict(reference), execution,
                                  backend_name=engine.name)
        measurement = EV.grade_measurement(observation, rows)
        aggregate = EV.grade_aggregate(observation, rows)
        row["measurement"] = measurement.get("passed")
        row["aggregate"] = aggregate.get("passed")
        row["ok"] = row["ok"] and measurement.get("passed", True) \
            and aggregate.get("passed", True)
        if not measurement.get("passed", True):
            row["measurement_failed"] = [
                k for k, v in measurement["checks"].items() if v is False]
        if not aggregate.get("passed", True):
            row["aggregate_failed"] = [
                k for k, v in aggregate["checks"].items() if v is False]
    if case["export"] and observation["execution_succeeded"]:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            export_row = O76.export(case_name, execution, engine,
                                    Path(folder),
                                    filename=f"{case_name}-{index}.step")
            export = EV.grade_export(observation, export_row)
        row["export"] = export.get("passed")
        row["export_level"] = export.get("level")
        row["ok"] = row["ok"] and export.get("passed", True)
        if not export.get("passed", True):
            row["export_failed"] = [
                k for k, v in export["checks"].items() if v is False]
    return row


def run(engine_name: Optional[str] = None) -> List[Dict[str, Any]]:
    engine = resolve_backend(engine_name)
    rows: List[Dict[str, Any]] = []
    for case_name in G.ACTIVE:
        case = G.expected(case_name)
        if case["group"] == G.REFUSAL:
            # A refusal case has no reference plan on purpose: the right
            # answer is a question, and a plan for it would be the failure
            # the case exists to catch.
            continue
        for index in range(case["calls"]):
            rows.append(check_turn(case_name, index, engine))
    return rows


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=list(G.ENGINES))
    args = parser.parse_args(argv)

    engines = [args.engine] if args.engine else list(G.ENGINES)
    worst = 0
    for name in engines:
        try:
            rows = run(name)
        except BackendUnavailable as exc:
            print(f"=== {name}: UNAVAILABLE -- {exc}")
            continue
        good = sum(1 for r in rows if r["ok"])
        print(f"=== {name}: {good}/{len(rows)} reference turns agree with "
              "the kernel and pass the grader")
        for row in rows:
            mark = "ok  " if row["ok"] else "FAIL"
            extra = ""
            for key in ("failed", "measurement_failed", "aggregate_failed",
                        "export_failed"):
                if row.get(key):
                    extra += f" {key}={row[key]}"
            if row.get("problems"):
                extra += f" problems={row['problems']}"
            print(f"  {mark} {row['case']}#{row['turn']:<2}"
                  f" bodies={row['bodies']}{extra}")
        if good != len(rows):
            worst = 1
    return worst


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
