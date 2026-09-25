"""Read Phase D's runs and apply the pre-registered rule. Calls no model.

It fetches counts out of recorded JSON and hands them to
`decision_rule_d.decide`. It has no criterion of its own: every threshold,
every test and every guard lives in the rule module, which was committed
before any arm's text existed. This file only counts.

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    python3 ../../docs/evaluation-baselines/stage75-multibody/\\
        phase-d-r2/analyse_d.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import classify_d as CD                                  # noqa: E402
import decision_rule_d as R                              # noqa: E402

#: The exploratory arms, in the order they were run. Each is R2 alone.
EXPLORATORY = (
    ("D1-missing-body-example", "arm-D1-explore.json"),
    ("D3-example-at-bodies-rule", "arm-D3-example-at-bodies-rule-explore.json"),
    ("D4-contrast-at-bodies-rule", "arm-D4-contrast-at-bodies-rule-explore.json"),
)

#: The confirmation battery: one control run and one arm run of each kind,
#: measured in the SAME session. An older number taken under a different
#: prompt is a different number, so nothing here is compared with history.
CONFIRMATION = {
    "control": {"refusal": "confirm-refusal-D0.json",
                "creation": "creation-D0.json",
                "golden": "golden-D0.json"},
    "arm": {"refusal": "confirm-refusal-D4.json",
            "creation": "creation-D4.json",
            "golden": "golden-D4.json"},
}


def wilson(hits: int, n: int, z: float = 1.96):
    """A 95% interval, so a small sample is never read as a point."""
    if not n:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _load(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _p_codes(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    """Plan-validation codes the model's own plans tripped."""
    counts: Dict[str, int] = {}
    for row in rows:
        for problem in (row["observation"].get("plan_problems") or ()):
            code = problem.get("code")
            if code:
                counts[code] = counts.get(code, 0) + 1
    return counts


def counts(refusal: Optional[dict], creation: Optional[dict],
           golden: Optional[dict], *, case: str = R.PRIMARY_CASE) -> Dict[str, Any]:
    """The count mapping `decide()` takes, and nothing else."""
    out: Dict[str, Any] = {
        "r2": {"strict": 0, "n": 0}, "r2_checks": {}, "siblings": {},
        "creation": {}, "single_body": {}, "identity_codes": {},
        "plan_codes": {}, "classes": {}, "sources": [],
    }
    plan_codes: Dict[str, int] = {}

    if refusal:
        out["sources"].append(refusal.get("arm", "?"))
        rows = refusal["attempts"]
        primary = [r for r in rows if r["case"] == case]
        out["r2"] = {"strict": sum(1 for r in primary if r["strict_success"]),
                     "n": len(primary)}
        out["r2_checks"] = {
            check: sum(1 for r in primary if (r.get("checks") or {}).get(check))
            for check in R.NO_TRADE
        }
        out["classes"] = CD.distribution(primary)
        for name, entry in refusal["summary"]["per_case"].items():
            if name != case:
                out["siblings"][name] = (entry["strict"], entry["calls"])
        for code, n in _p_codes(rows).items():
            plan_codes[code] = plan_codes.get(code, 0) + n

    if creation:
        out["sources"].append(creation.get("arm", "?"))
        rows = creation["attempts"]
        for name, entry in creation["summary"]["per_case"].items():
            out["creation"][name] = (entry["strict"], entry["calls"])
            for code, n in entry["codes"].items():
                if code in R.IDENTITY_CODES:
                    out["identity_codes"][code] = (
                        out["identity_codes"].get(code, 0) + n)
        for code, n in _p_codes(rows).items():
            plan_codes[code] = plan_codes.get(code, 0) + n

    if golden:
        out["sources"].append(golden.get("arm", "?"))
        summary = golden["summary"]
        out["single_body"] = {"strict": summary["STRICT_SUCCESS"],
                              "n": summary["calls"]}
        out["golden_guards"] = {
            key: summary.get(key) for key in
            ("built", "thickness_correct", "plate_count_correct",
             "envelope_correct", "volume_correct")
        }
        out["golden_codes"] = summary.get("failure_codes", {})
        for code, n in (summary.get("p_codes") or {}).items():
            plan_codes[code] = plan_codes.get(code, 0) + n

    out["plan_codes"] = plan_codes
    return out


def _fmt(hits: int, n: int) -> str:
    if not n:
        return "     --   "
    low, high = wilson(hits, n)
    return f"{hits:3}/{n:<3} {hits / n:6.1%}  [{low:.2f},{high:.2f}]"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=str(HERE))
    args = ap.parse_args(argv)
    here = Path(args.dir)

    baseline_file = _load(here / "baseline-d-r2.json")
    if baseline_file is None:
        print("no baseline-d-r2.json; run the Phase 1 baseline first")
        return 2
    baseline = counts(baseline_file, None, None)

    print("=== PHASE 1: the fresh R2 baseline, committed prompt ===")
    print(f"  strict        {_fmt(baseline['r2']['strict'], baseline['r2']['n'])}")
    for letter in CD.CLASS_ORDER:
        n = baseline["classes"].get(letter, 0)
        if n:
            print(f"  class {letter}       {_fmt(n, baseline['r2']['n'])}  "
                  f"{CD.CLASS_TEXT[letter]}")

    print("\n=== PHASE 3: the exploratory arms, R2 only, one variable each ===")
    header = f"  {'arm':30} {'chars':>6}  {'class A (= strict)':>28}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    print(f"  {'D0-baseline (committed)':30} {baseline_file.get('prompt_characters', 0):6}  "
          f"{_fmt(baseline['r2']['strict'], baseline['r2']['n'])}")
    explore: List[Dict[str, Any]] = []
    for name, filename in EXPLORATORY:
        record = _load(here / filename)
        if record is None:
            continue
        arm = counts(record, None, None)
        arm["name"] = name
        arm["chars"] = record.get("arm_characters", 0)
        explore.append(arm)
        print(f"  {name:30} {arm['chars']:6}  "
              f"{_fmt(arm['r2']['strict'], arm['r2']['n'])}")

    for arm in explore:
        verdict = R.decide(arm, baseline, exploratory=True)
        print(f"\n  {arm['name']}: ADOPT={verdict['adopt']}")
        for reason in verdict["reasons"]:
            print(f"    - {reason}")

    print("\n=== PHASE 4: the confirmation battery, one session ===")
    battery: Dict[str, Dict[str, Any]] = {}
    for role, files in CONFIRMATION.items():
        loaded = {kind: _load(here / name) for kind, name in files.items()}
        if not any(loaded.values()):
            print(f"  {role}: not run yet")
            continue
        battery[role] = counts(loaded["refusal"], loaded["creation"],
                               loaded["golden"])
    if len(battery) != 2:
        print("  (both the control and the arm are needed before the rule "
              "can be applied)")
        return 0

    control, arm = battery["control"], battery["arm"]
    print(f"  {'quantity':28} {'control':>26}   {'arm':>26}")
    print("  " + "-" * 82)
    rows = [("R2 strict", control["r2"], arm["r2"])]
    print(f"  {'R2 strict':28} {_fmt(control['r2']['strict'], control['r2']['n']):>26}"
          f"   {_fmt(arm['r2']['strict'], arm['r2']['n']):>26}")
    for check in R.NO_TRADE:
        print(f"  {'R2 ' + check:28} "
              f"{_fmt(control['r2_checks'].get(check, 0), control['r2']['n']):>26}"
              f"   {_fmt(arm['r2_checks'].get(check, 0), arm['r2']['n']):>26}")
    for name in sorted(set(control["siblings"]) | set(arm["siblings"])):
        c, a = control["siblings"].get(name), arm["siblings"].get(name)
        print(f"  {name + ' strict':28} {_fmt(*c) if c else '--':>26}"
              f"   {_fmt(*a) if a else '--':>26}")
    for name in sorted(set(control["creation"]) | set(arm["creation"])):
        c, a = control["creation"].get(name), arm["creation"].get(name)
        print(f"  {'creation ' + name:28} {_fmt(*c) if c else '--':>26}"
              f"   {_fmt(*a) if a else '--':>26}")
    if control["single_body"] and arm["single_body"]:
        print(f"  {'single-body golden':28} "
              f"{_fmt(control['single_body']['strict'], control['single_body']['n']):>26}"
              f"   {_fmt(arm['single_body']['strict'], arm['single_body']['n']):>26}")
        print(f"  {'  golden guards':28} {str(control.get('golden_guards')):>26}")
        print(f"  {'':28} {str(arm.get('golden_guards')):>26}")
    print(f"  {'identity codes':28} {str(control['identity_codes']):>26}"
          f"   {str(arm['identity_codes']):>26}")
    print(f"  {'plan codes':28} {str(control['plan_codes']):>26}"
          f"   {str(arm['plan_codes']):>26}")

    print("\n  classes on R2, confirmation:")
    for letter in CD.CLASS_ORDER:
        c, a = control["classes"].get(letter, 0), arm["classes"].get(letter, 0)
        if c or a:
            print(f"    {letter}  control {c:3}   arm {a:3}   "
                  f"{CD.CLASS_TEXT[letter]}")

    print("\n=== the PRE-REGISTERED rule, applied to the CONFIRMATION ===")
    print(f"(committed before any arm text existed: MIN_N={R.MIN_N}, "
          f"ALPHA={R.ALPHA}, MIN_GAIN={R.MIN_GAIN},\n"
          f" REFUSAL_FLOOR={R.REFUSAL_FLOOR}, CREATION_FLOOR={R.CREATION_FLOOR}, "
          f"SINGLE_BODY_FLOOR={R.SINGLE_BODY_FLOOR})\n")
    verdict = R.decide(arm, control, exploratory=False)
    print(f"  ADOPT: {verdict['adopt']}   p={verdict['p']:.6f}   "
          f"gain={verdict['gain']:+.3f}")
    for reason in verdict["reasons"]:
        print(f"    - {reason}")

    # R2 ONLY, and deliberately not a verdict. The Phase 1 baseline carries
    # no creation, sibling or single-body evidence, so the rule would report
    # REJECT on missing evidence rather than on anything the arm did -- and
    # a reader seeing two ADOPT lines disagree would have to work out why.
    # The comparison is worth printing; the verdict is not.
    p = R.fisher_exact_two_sided(
        arm["r2"]["strict"], arm["r2"]["n"] - arm["r2"]["strict"],
        baseline["r2"]["strict"], baseline["r2"]["n"] - baseline["r2"]["strict"])
    print("\n  R2 against the PHASE 1 baseline, for the record "
          "(not a verdict -- that run measured R2 alone):")
    print(f"    {arm['r2']['strict']}/{arm['r2']['n']} vs "
          f"{baseline['r2']['strict']}/{baseline['r2']['n']}, p={p:.8f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
