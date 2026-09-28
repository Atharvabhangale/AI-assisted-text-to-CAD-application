"""Score a Stage 78 run against the pre-registered rule. Offline.

Re-grades a recorded run from its preserved raw bytes and applies
`decision_rule_78` to the result. Calls no model and changes nothing, so it
can be re-run after an instrument fix without spending anything -- which is
exactly what happened once in this stage: the first grader read an omitted
`axis` field as a wrong direction, when the language's default is +Z.

    PYTHONPATH=. python3 score78.py explore78.json
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, List

HERE = pathlib.Path(__file__).resolve().parent
STAGE77 = HERE.parent / "stage77-multibody-corpus"
for path in (HERE, STAGE77):
    sys.path.insert(0, str(path))

import decision_rule_78 as R                                     # noqa: E402
import evaluate78 as E78                                         # noqa: E402
import ground_truth77 as G77                                     # noqa: E402
import ground_truth78 as G78                                     # noqa: E402

#: The mechanism this stage exists to reduce. `assess_case` compares its
#: count between arms, and the rule requires it to at least halve.
MECHANISM_CODES = (E78.AXIS_AT_MAX_EXTENT, E78.AXIS_AT_MIN_EXTENT)


def regrade(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for attempt in record.get("attempts", []):
        case = attempt.get("case")
        if case not in G78.CASES:
            continue
        for turn in attempt["turns"]:
            raw = (turn.get("observation") or {}).get("raw_text")
            passed77 = not any(c in G77.FAILURE_CODES for c in turn["codes"])
            row = E78.score_turn(case, raw, passed77)
            row["attempt"] = attempt["attempt"]
            rows.append(row)
    return rows


def counts(rows: List[Dict[str, Any]], case: str):
    subset = [r for r in rows if r["case"] == case]
    passed = sum(1 for r in subset if r["stage78_strict"])
    mechanism = sum(1 for r in subset
                    if any(c in MECHANISM_CODES for c in r["codes"]))
    return passed, len(subset), mechanism


def main(argv: List[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = pathlib.Path(argv[0])
    is_confirmation = "--confirmation" in argv
    payload = json.loads(path.read_text())
    records = payload["records"]

    graded = {arm: regrade(rec) for arm, rec in records.items()}
    baseline = graded.get("S0-baseline")
    if baseline is None:
        print("no same-session baseline arm in this run; the rule requires "
              "one and refuses to score without it.")
        return 2

    print(f"{path.name}  ({'CONFIRMATION' if is_confirmation else 'exploratory'})")
    print(f"same-session baseline: S0-baseline, interleaved\n")
    print(f"{'arm':<24} {'case':<7} {'strict':>9} {'rate':>7} "
          f"{'mech':>5} {'improve':>8} {'p':>10}  verdict")

    per_arm: Dict[str, List[Dict[str, Any]]] = {}
    for arm, rows in graded.items():
        if arm == "S0-baseline":
            continue
        assessments = []
        for case in G78.CASES:
            cp, cn, cm = counts(rows, case)
            bp, bn, bm = counts(baseline, case)
            a = R.assess_case(case, cp, cn, bp, bn, cm, bm)
            assessments.append(a)
            flags = []
            if a["improved_enough"]:
                flags.append("improved")
            if a["significant"]:
                flags.append(f"p<{R.SIGNIFICANCE_ALPHA}")
            if a["mechanism_fell_enough"]:
                flags.append("mechanism halved")
            if a["regressed"]:
                flags.append("REGRESSED")
            print(f"{arm:<24} {case:<7} {cp:>4}/{cn:<4} {cp/cn:>7.3f} "
                  f"{cm:>5} {a['improvement']:>+8.3f} {a['p']:>10.4g}  "
                  f"{', '.join(flags) or '-'}")
        per_arm[arm] = assessments

    print(f"\nbaseline, for reference:")
    for case in G78.CASES:
        bp, bn, bm = counts(baseline, case)
        print(f"{'S0-baseline':<24} {case:<7} {bp:>4}/{bn:<4} {bp/bn:>7.3f} "
              f"{bm:>5}")

    print("\nverdict under the pre-registered rule:")
    for arm, assessments in per_arm.items():
        out = R.verdict(assessments, is_confirmation=is_confirmation)
        print(f"  {arm:<24} {out['verdict']}"
              + (f"   carried: {', '.join(out['carried'])}"
                 if out["carried"] else ""))
        for reason in out["reasons"]:
            print(f"      - {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
