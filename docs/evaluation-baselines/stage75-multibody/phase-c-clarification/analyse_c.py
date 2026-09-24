"""Read Phase C's runs and apply the pre-registered rule. Calls no model.

It takes recorded JSON and `decision_rule_c.decide`, and prints what the rule
says. It has no criterion of its own: every threshold, every test and every
guard lives in `decision_rule_c`, which was committed before any arm was
built. This file only fetches counts and hands them over.

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    python3 ../../docs/evaluation-baselines/stage75-multibody/\\
        phase-c-clarification/analyse_c.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import decision_rule_c as R                            # noqa: E402


def wilson(hits: int, n: int, z: float = 1.96):
    """A 95% interval, so a small sample is never read as a point."""
    if not n:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def counts(path: Path) -> dict:
    """The count mapping `decide()` takes, and nothing else."""
    record = json.loads(path.read_text(encoding="utf-8"))
    summary = record["summary"]
    total = (summary.get("clarification") or {}).get("total") or {}
    rows = record["attempts"]
    n = len(rows)

    def checked(key: str) -> int:
        return sum(1 for r in rows if (r.get("checks") or {}).get(key))

    return {
        "name": record.get("arm", "C0-baseline"),
        "fingerprint": (record.get("arm_fingerprint")
                        or record.get("prompt_fingerprint", ""))[:16],
        "n": n,
        "strict": summary["per_group"]["refusal"]["strict"],
        "naming_both": total.get("naming", {}).get("both", 0),
        "naming_one": total.get("naming", {}).get("one", 0),
        "naming_zero": total.get("naming", {}).get("zero", 0),
        "operations_none": total.get("operations", {}).get("none", 0),
        "operations_some": total.get("operations", {}).get("some", 0),
        "asked": total.get("asked", 0),
        "refused": checked("refused"),
        "built_nothing": checked("built_nothing"),
        "per_case": {k: (v["strict"], v["calls"])
                     for k, v in summary["per_case"].items()},
        "labels": summary.get("labels", {}),
        # No creation case is run by an arm, so these are empty and the
        # rule's creation clause is satisfied vacuously BY THE ARM. The
        # regression is checked separately, against the committed creation
        # baseline, and the record says so rather than letting an empty dict
        # read as "creation was verified".
        "creation": {},
        "identity_codes": {},
    }


def main() -> int:
    baseline_path = HERE / "baseline-c.json"
    if not baseline_path.is_file():
        print("no baseline-c.json; run the baseline first")
        return 2
    baseline = counts(baseline_path)
    arms = [counts(p) for p in sorted(HERE.glob("arm-*.json"))]

    header = (f"{'arm':30} {'n':>3} {'strict':>9} {'A:both':>9} "
              f"{'A:one':>6} {'A:zero':>7} {'B:none':>9} {'B:some':>7} "
              f"{'asked':>7}")
    print(header)
    print("-" * len(header))
    for row in [baseline] + arms:
        n = row["n"]
        print(f"{row['name']:30} {n:3} {row['strict']:4}/{n:<4} "
              f"{row['naming_both']:4}/{n:<4} {row['naming_one']:6} "
              f"{row['naming_zero']:7} {row['operations_none']:4}/{n:<4} "
              f"{row['operations_some']:7} {row['asked']:7}")

    print("\nbaseline, with 95% intervals:")
    for key in ("strict", "naming_both", "operations_none", "asked"):
        low, high = wilson(baseline[key], baseline["n"])
        print(f"  {key:16} {baseline[key]:3}/{baseline['n']} = "
              f"{baseline[key] / baseline['n']:.3f}   CI {low:.3f}-{high:.3f}")

    print("\n=== the PRE-REGISTERED rule, applied ===")
    print(f"(committed before any arm was built: MIN_N={R.MIN_N}, "
          f"ALPHA={R.ALPHA}, CREATION_FLOOR={R.CREATION_FLOOR})\n")
    for arm in arms:
        verdict = R.decide(arm, baseline, exploratory=True)
        print(f"  {arm['name']}  ({arm['fingerprint']})")
        print(f"    ADOPT: {verdict['adopt']}   moved: {verdict['moved']}")
        for reason in verdict["reasons"]:
            print(f"      - {reason}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
