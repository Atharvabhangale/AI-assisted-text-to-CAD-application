"""Run Stage 76's observer over the deterministic fixtures. Calls NO model.

    python -m run76 --engine cadquery --out offline-cadquery.json
    python -m run76 --engine freecad  --out offline-freecad.json
    python -m run76 --check                 # both engines, no file written

**Everything this produces is `DETERMINISTIC`.** Five parts built from plans
written by hand, measured and exported by a real kernel, with no model in
the room. That is a statement about the OBSERVER and says nothing whatever
about a model, and :func:`observe76.observation` refuses to label it
otherwise.

Its job is the gate the brief sets: the observer must be complete,
mutation-tested and proven offline before a single live call is spent on it.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan
from cad_experimental.validation import validate_plan

import evaluate76 as EV
import fixtures76 as F
import ground_truth76 as G
import observe76 as O

HERE = Path(__file__).resolve().parent


def run_case(case_name: str, engine: Any, folder: Path) -> Dict[str, Any]:
    """Build, measure and export one fixture case on one engine."""
    plan_dict = F.plan_for(case_name)
    plan = parse_plan(plan_dict)
    report = validate_plan(plan)
    execution = execute_plan(plan, part_name="stage76", backend=engine)

    measurement = O.measure(case_name, plan_dict, execution,
                            backend_name=engine.name)
    export = O.export(case_name, execution, engine, folder,
                      filename=f"{case_name}-{engine.name}.step")
    observation = O.observation(
        case_name=case_name,
        # The one label an offline run may carry. `observation` raises on
        # any attempt to say MODEL_GENERATED without model output.
        source=G.SOURCE_DETERMINISTIC,
        model_output=None,
        geometry_section=O.geometry(execution),
        measurement_section=measurement,
        export_section=export,
        note=("a hand-written fixture plan built by a real kernel; no model "
              "was called and none was configured for this run"),
    )
    observation["plan_valid"] = bool(report.valid)
    observation["plan_problems"] = [p.code for p in report.problems]
    return observation


def run(engine_name: str) -> Dict[str, Any]:
    engine = resolve_backend(engine_name)
    observations: List[Dict[str, Any]] = []
    verdicts: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as folder:
        for case_name in G.ACTIVE:
            observation = run_case(case_name, engine, Path(folder))
            observations.append(observation)
            verdicts.append(EV.grade(observation))
        # Cross-reading: a STEP only one engine can read is not an
        # interchange file. Each written file is offered to the OTHER engine.
        crossed: List[Dict[str, Any]] = []
        for other_name in G.ENGINES:
            if other_name == engine_name:
                continue
            try:
                other = resolve_backend(other_name)
            except BackendUnavailable as exc:
                crossed.append({"engine": other_name, "unavailable": str(exc)})
                continue
            for observation in observations:
                written = Path(folder) / (
                    f"{observation['case']}-{engine.name}.step")
                if not written.exists():
                    continue
                row = O.cross_read(written, other)
                row["case"] = observation["case"]
                row["written_by"] = engine.name
                crossed.append(row)
    return {
        "engine": engine.name,
        "source": G.SOURCE_DETERMINISTIC,
        "is_live_model_result": False,
        "cases": G.ACTIVE,
        "observations": observations,
        "verdicts": verdicts,
        "summary": EV.summarise(verdicts),
        "cross_reads": crossed,
    }


def _print(record: Dict[str, Any]) -> bool:
    print(f"=== {record['engine']} ({record['source']}) ===")
    ok = True
    for verdict in record["verdicts"]:
        geometry = verdict["geometry"]["passed"]
        measurement = verdict["measurement"]["passed"]
        export = verdict["export"]
        print(f"  {verdict['case']:<4} geometry={geometry!s:<5} "
              f"measurement={measurement!s:<5} export={export['level']:<22} "
              f"identity={export['identity_state']}")
        if not verdict["passed"]:
            ok = False
            for name, probe in verdict["measurement"]["probes"].items():
                if not probe["passed"]:
                    failed = [k for k, v in probe["checks"].items()
                              if v is not True]
                    print(f"        FAILED {name}: {failed}")
            if not geometry:
                print("        geometry:", verdict["geometry"]["checks"])
            if not export["passed"]:
                print("        export:", export["level_means"])
    summary = record["summary"]
    print(f"  -- geometry {summary['geometry']['passed']}/"
          f"{summary['geometry']['of']}"
          f"  measurement {summary['measurement']['passed']}/"
          f"{summary['measurement']['of']}"
          f"  export {summary['export']['passed']}/{summary['export']['of']}"
          f"  (no combined rate, on purpose)")
    for row in record["cross_reads"]:
        if "unavailable" in row:
            print(f"  cross-read {row['engine']}: UNAVAILABLE")
        else:
            print(f"  cross-read {row['case']} written by "
                  f"{row['written_by']} read by {row['engine']}: "
                  f"solids={row['solids_read']} volumes={row['volumes_read']}")
    return ok


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=list(G.ENGINES))
    parser.add_argument("--check", action="store_true",
                        help="run every available engine, write nothing")
    parser.add_argument("--out", help="where to write the record")
    args = parser.parse_args(argv)

    engines = list(G.ENGINES) if args.check or not args.engine else [args.engine]
    ok = True
    for name in engines:
        try:
            record = run(name)
        except BackendUnavailable as exc:
            print(f"=== {name}: UNAVAILABLE -- {exc}")
            continue
        ok = _print(record) and ok
        if args.out and len(engines) == 1:
            Path(args.out).write_text(json.dumps(record, indent=2,
                                                 sort_keys=True))
            print(f"  wrote {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
