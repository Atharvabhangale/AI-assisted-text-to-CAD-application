"""Re-grade the recorded runs offline. **No model is called.**

    python3 regrade77.py baseline.json widened.json --out results.json

Two things happened after the runs were recorded, and neither is a new
measurement:

1. **RF-04 was RETIRED.** Its expectation was wrong and the model was right
   on all eight attempts -- see its retirement note. The eight attempts stay
   in `baseline.json` verbatim and are excluded from every denominator here.
   A corpus that deletes its mistakes cannot be audited, and one that edits
   them after seeing a score is not measuring anything.
2. **One taxonomy code was corrected.** Bodies that interpenetrate were
   filed as `H:unwanted_fusion` and are `F:wrong_placement`: they are
   separate solids at their own correct volumes standing in the wrong
   place, and nothing was fused. **No verdict moves under this** -- the
   classifier does not decide pass or fail -- and this script proves that
   by reporting how many did.

Stage 75 Phase C set the pattern: when an instrument defect is found, fix
the instrument and re-grade the PRESERVED raw data rather than re-calling.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Dict, List

import evaluate77 as EV
import ground_truth77 as G


def wilson(passed: int, total: int, z: float = 1.96):
    if total == 0:
        return (0.0, 0.0)
    hat = passed / total
    denominator = 1 + z * z / total
    centre = (hat + z * z / (2 * total)) / denominator
    half = z * math.sqrt(hat * (1 - hat) / total
                         + z * z / (4 * total * total)) / denominator
    return (round(100 * (centre - half), 1), round(100 * (centre + half), 1))


def regrade(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    kept: List[Dict[str, Any]] = []
    retired: Dict[str, int] = {}
    moved = 0
    recoded = 0

    for record in records:
        for attempt in record["attempts"]:
            if attempt["case"] in G.RETIRED:
                retired[attempt["case"]] = retired.get(attempt["case"], 0) + 1
                continue
            for turn in attempt["turns"]:
                if "not_run" in turn:
                    continue
                observation = turn["observation"]
                fresh = EV.grade_turn(observation)
                if fresh["strict_success"] != turn["verdict"]["strict_success"]:
                    moved += 1
                codes = list(EV.classify(
                    observation, fresh, turn.get("measurement"),
                    turn.get("aggregate"), turn.get("export")))
                if codes != list(turn.get("codes") or ()):
                    recoded += 1
                turn["verdict"] = fresh
                turn["codes"] = codes
            attempt["strict_success"] = all(
                t["verdict"]["strict_success"] for t in attempt["turns"])
            kept.append(attempt)

    summary = EV.summarise(kept)
    for key in ("creation", "edit_turns", "edit_chains", "refusal",
                "measurement", "aggregate", "export"):
        row = summary[key]
        row["ci95"] = wilson(row["passed"], row["of"])
    for row in summary["per_case"].values():
        row["ci95"] = wilson(row["passed"], row["of"])
    return {
        "regraded": True,
        "regraded_note": (
            "offline re-grade of preserved raw answers; NO model was called. "
            "RF-04 is retired and excluded from every denominator, and "
            "`bodies_disjoint` now classifies as F:wrong_placement rather "
            "than H:unwanted_fusion"),
        "verdicts_that_moved": moved,
        "attempts_whose_codes_changed": recoded,
        "retired_attempts_excluded": retired,
        "attempts": kept,
        "summary": summary,
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("records", nargs="+")
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    records = [json.loads(open(p).read()) for p in args.records]
    for record in records:
        if not record.get("is_live_model_result"):
            raise SystemExit("not a live record")
    report = regrade(records)
    summary = report["summary"]

    print(f"re-graded {summary['live_calls']} preserved live calls; "
          f"verdicts that MOVED: {report['verdicts_that_moved']}; "
          f"attempts recoded: {report['attempts_whose_codes_changed']}")
    print(f"retired and excluded: {report['retired_attempts_excluded']}")
    print()
    for key in ("creation", "edit_turns", "edit_chains", "refusal",
                "measurement", "aggregate", "export"):
        row = summary[key]
        extra = (f"  not_assessed={row['not_assessed']}"
                 if "not_assessed" in row else "")
        rate = (f"{100 * row['passed'] / row['of']:.1f}%"
                if row["of"] else "n/a")
        print(f"   {key:13} {row['passed']:3}/{row['of']:<3} {rate:>6}"
              f"  95% CI {row['ci95']}{extra}")
    print()
    print("   per case:")
    for name, row in sorted(summary["per_case"].items()):
        flag = "" if row["passed"] == row["of"] else "   <<<"
        print(f"      {name:7} {row['passed']:2}/{row['of']:<2} "
              f"CI {row['ci95']}{flag}")
    print()
    print("   failure codes:", {k: v for k, v in summary["codes"].items() if v})
    print("   export levels:",
          {k: v for k, v in summary["export_levels"].items() if v})
    print("   combined rate:", summary["combined_rate"])

    if args.out:
        with open(args.out, "w") as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
