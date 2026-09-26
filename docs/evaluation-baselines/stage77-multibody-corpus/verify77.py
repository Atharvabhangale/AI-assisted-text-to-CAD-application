"""Rebuild every claimed MODEL_GENERATED success, on both kernels.

    python3 verify77.py baseline.json [widened.json ...]

A verdict recorded by the arena is the arena's word. This takes the model's
own RAW ANSWER out of the record, parses it again, builds it again on
CadQuery 2.8.0 and again on FreeCAD 1.0.0, and compares what comes out
against the closed forms in `ground_truth77` -- never against what the
arena said, and never against the model.

**No model is called.** Nothing here can change a verdict; it can only
agree or disagree with one, and a disagreement is a defect in the arena
rather than a new result.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List

from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import PlanParseError, parse_plan
from cad_experimental.validation import validate_plan

import evaluate77 as EV
import ground_truth77 as G


def _rebuild(raw_text: str, engine) -> Dict[str, Any]:
    """Build the model's own answer again, from its own text."""
    try:
        answer = json.loads(raw_text)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "why": f"the recorded answer is not JSON: {exc}"}
    try:
        parsed = parse_plan(answer)
    except PlanParseError as exc:
        return {"ok": False, "why": f"it no longer parses: {exc}"}
    verdict = validate_plan(parsed)
    if not verdict.valid:
        return {"ok": False,
                "why": "it no longer validates: "
                       + ", ".join(sorted({p.code for p in verdict.problems}))}
    result = execute_plan(parsed, part_name="verify77", backend=engine)
    if not result.succeeded:
        return {"ok": False,
                "why": "it no longer builds: "
                       + (result.failure.message if result.failure else "?")}
    return {
        "ok": True,
        "bodies": {b.id: {"volume": b.measurement.volume,
                          "faces": b.measurement.face_count,
                          "edges": b.measurement.edge_count,
                          "solids": b.measurement.solid_count}
                   for b in result.bodies},
        "declared": list(result.declared),
    }


def verify(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    engines = {}
    for name in G.ENGINES:
        try:
            engines[name] = resolve_backend(name)
        except BackendUnavailable as exc:
            engines[name] = None
            print(f"  {name}: UNAVAILABLE -- {exc}")

    rows: List[Dict[str, Any]] = []
    for record in records:
        for attempt in record["attempts"]:
            if attempt["case"] in G.RETIRED:
                # A retired case's attempts stay in the record verbatim and
                # are scored by nothing, here as everywhere else.
                continue
            case = G.expected(attempt["case"])
            if case["group"] == G.REFUSAL:
                continue          # nothing was built, by design
            for turn in attempt["turns"]:
                if "not_run" in turn:
                    continue
                if not turn["verdict"]["strict_success"]:
                    continue
                raw = turn["observation"].get("raw_text")
                if not raw:
                    continue
                expected = G.expected_turn(attempt["case"], turn["turn"])
                row: Dict[str, Any] = {
                    "case": attempt["case"], "attempt": attempt["attempt"],
                    "turn": turn["turn"], "engines": {},
                }
                for name, engine in engines.items():
                    if engine is None:
                        continue
                    built = _rebuild(raw, engine)
                    if not built["ok"]:
                        row["engines"][name] = built
                        continue
                    volumes = [b["volume"] for b in built["bodies"].values()]
                    built["matches_closed_form"] = (
                        len(built["bodies"]) == expected["bodies"]
                        and EV._match_multiset(volumes, expected["volumes"],
                                               G.VOLUME_TOLERANCE))
                    row["engines"][name] = built
                # Both engines, body for body, to the last digit they share.
                names = [n for n, e in engines.items() if e is not None]
                if len(names) > 1:
                    first, second = names[0], names[1]
                    a = row["engines"].get(first, {}).get("bodies") or {}
                    b = row["engines"].get(second, {}).get("bodies") or {}
                    row["engines_agree"] = (
                        set(a) == set(b)
                        and all(abs(a[k]["volume"] - b[k]["volume"])
                                <= abs(a[k]["volume"]) * G.VOLUME_TOLERANCE
                                for k in a)
                        and all(a[k]["faces"] == b[k]["faces"] for k in a)
                        and all(a[k]["edges"] == b[k]["edges"] for k in a))
                rows.append(row)

    total = len(rows)
    per_engine = {
        name: sum(1 for r in rows
                  if r["engines"].get(name, {}).get("matches_closed_form"))
        for name in engines if engines[name] is not None
    }
    agree = sum(1 for r in rows if r.get("engines_agree"))
    return {"rebuilt": total, "per_engine": per_engine,
            "engines_agree": agree, "rows": rows}


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    records = [json.loads(open(p).read()) for p in args.records]
    for record in records:
        if not record.get("is_live_model_result"):
            raise SystemExit(
                f"{record.get('stage')}: not a live record; there is nothing "
                "here to verify")
    report = verify(records)
    print(f"rebuilt {report['rebuilt']} claimed successes from the model's "
          "own recorded answers")
    for name, good in sorted(report["per_engine"].items()):
        print(f"   {name:9} {good}/{report['rebuilt']} match the closed form")
    print(f"   both engines agree body for body: "
          f"{report['engines_agree']}/{report['rebuilt']}")
    bad = [r for r in report["rows"]
           if not all(e.get("matches_closed_form")
                      for e in r["engines"].values())]
    for row in bad:
        print(f"   MISMATCH {row['case']}#{row['attempt']}.{row['turn']}: "
              f"{ {k: v.get('why') or v.get('bodies') for k, v in row['engines'].items()} }")
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
        print(f"wrote {args.out}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
