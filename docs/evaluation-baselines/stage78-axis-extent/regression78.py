"""Stage 78's regression gate: what a candidate must not break.

A fix for one thing that costs another is not a fix. Stage 77 measured
eighteen cases over nine geometric families and six separate denominators;
a prompt change lands on ALL of them, not only on the two this stage is
aiming at.

Focused first, by design. The full Stage 77 corpus is 240 live calls and
re-running it to screen a candidate that will probably be rejected is a
waste of real provider spend. This runs a SUBSET chosen to touch every
dimension in `decision_rule_78.MUST_PRESERVE` at least once, and the full
corpus is only re-run if a candidate would otherwise qualify.

Scored with STAGE 77's grader, unmodified. The Stage 78 axis criterion is
deliberately not applied here: `ground_truth78` has truth for ED-02 and
CR-06 only, and inventing an axis expectation for a case whose request
never mentions one is exactly the over-pinning that retired M4 and M8 in
Stage 75.

    PYTHONPATH=. python3 regression78.py --check
    PYTHONPATH=. python3 regression78.py --live --arm S2-worked-example \\
        --calls 8 --out regression-S2.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
STAGE77 = HERE.parent / "stage77-multibody-corpus"
STAGE76 = HERE.parent / "stage76-observation"
for path in (HERE, STAGE77, STAGE76):
    sys.path.insert(0, str(path))

from cad_experimental.config import bridge_credential                # noqa: E402

import arena77 as A77                                                # noqa: E402
import arena78 as A78                                                # noqa: E402
import ground_truth77 as G77                                         # noqa: E402
import variants78 as V                                               # noqa: E402

#: The focused sample. Each entry names the dimension it exists to protect,
#: so a reader can see the cover is deliberate rather than a handful of
#: cases someone liked. Every name in `MUST_PRESERVE` appears at least once.
#:
#: ED-02 and CR-06 are NOT here -- they are the experiment, not the control.
SAMPLE: Dict[str, str] = {
    "CR-01": "multi_body_creation, body_identity",
    "CR-05": "placement (the one creation case that pins coordinates)",
    "CR-07": "thickness, single_body_golden (a bored plate)",
    "CR-09": "multi_body_creation at four bodies",
    "ED-01": "body_targeting, edit_isolation (edits the FIRST body)",
    "ED-03": "edit_isolation over a two-turn chain",
    "ED-05": "body_targeting by name with no feature",
    "RF-01": "refusal",
    "RF-02": "refusal, bad_clarification",
}

#: Stage 77's recorded per-case strict rates for the sample, re-read from
#: `results.json` rather than recalled. HISTORICAL: the comparison that
#: decides the gate is against the same-session baseline arm, never these.
STAGE77_SAMPLE: Dict[str, str] = {
    "CR-01": "8/8", "CR-05": "8/8", "CR-07": "8/8", "CR-09": "8/8",
    "ED-01": "8/8", "ED-03": "8/8", "ED-05": "8/8",
    "RF-01": "8/8", "RF-02": "32/32",
}


def run(arm: str, cases: List[str], calls: int, batch: int) -> Dict[str, Any]:
    """Interleaved with the baseline, for the same reason arms are."""
    arms = [V.BASELINE, arm] if arm != V.BASELINE else [V.BASELINE]
    records: Dict[str, Any] = {}
    rounds = [batch] * (calls // batch)
    if calls % batch:
        rounds.append(calls % batch)
    for index, size in enumerate(rounds):
        for name in arms:
            print(f"  round {index + 1}/{len(rounds)}  {name}  {size}/case")
            with A78.patched(name) as text:
                if A78.gen.system_prompt() != text:
                    raise SystemExit("the patch did not reach the live route")
                part = A77.run(cases, size)
            if name not in records:
                records[name] = part
            else:
                offset = len(records[name]["attempts"])
                for attempt in part["attempts"]:
                    attempt["attempt"] += offset
                records[name]["attempts"].extend(part["attempts"])
    return records


def rates(record: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, Any] = {}
    for attempt in record["attempts"]:
        entry = out.setdefault(attempt["case"], [0, 0])
        entry[1] += 1
        entry[0] += 1 if attempt["strict_success"] else 0
    return {name: f"{passed}/{total}" for name, (passed, total)
            in sorted(out.items())}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default=None,
                        help="the candidate; the baseline is always run "
                             "beside it, interleaved")
    parser.add_argument("--calls", type=int, default=8)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--cases", default=",".join(SAMPLE))
    parser.add_argument("--out", default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = [c for c in cases if c not in G77.CASES_BY_NAME]
    if unknown:
        print(f"unknown case(s): {', '.join(unknown)}")
        return 2
    overlap = [c for c in cases if c in ("ED-02", "CR-06")]
    if overlap:
        print(f"refusing: {', '.join(overlap)} is the EXPERIMENT, not a "
              "control; a candidate cannot be its own regression sample.")
        return 2

    if args.check:
        print("focused regression sample, and the dimension each protects:")
        for name in cases:
            print(f"  {name}  Stage 77 {STAGE77_SAMPLE.get(name, '?'):>6}   "
                  f"{SAMPLE.get(name, '')}")
        print(f"\n{len(cases)} cases x {args.calls} calls x 2 arms = "
              f"{len(cases) * args.calls * 2} live calls")
        print("\nnothing was called.")
        return 0

    if not args.live:
        print("refusing to run without --live.")
        return 2
    if bridge_credential() is None:
        print("no credential in this process; nothing attempted.")
        return 2
    if not args.arm:
        print("--arm is required for a regression run.")
        return 2

    drift = A78.check_identity_for_arm() + A78.baseline_prompt_is_unmoved()
    if drift:
        raise SystemExit("identity drift: " + "; ".join(drift))

    records = run(args.arm, cases, args.calls, args.batch)
    print()
    base_rates = rates(records[V.BASELINE])
    arm_rates = rates(records[args.arm])
    print(f"{'case':<8} {'baseline':>10} {'candidate':>10}   verdict")
    regressed: List[str] = []
    for name in cases:
        b, c = base_rates.get(name, "-"), arm_rates.get(name, "-")
        bad = "-"
        if "/" in b and "/" in c:
            bp, bn = (int(x) for x in b.split("/"))
            cp, cn = (int(x) for x in c.split("/"))
            delta = cp / cn - bp / bn
            bad = "REGRESSED" if delta < -1e-9 else "ok"
            if delta < -1e-9:
                regressed.append(f"{name} {b}->{c}")
        print(f"{name:<8} {b:>10} {c:>10}   {bad}")
    print(f"\nregressions: {', '.join(regressed) if regressed else 'none'}")

    out = pathlib.Path(args.out or (HERE / f"regression-{args.arm}.json"))
    out.write_text(json.dumps({
        "stage": 78, "kind": "focused regression", "arm": args.arm,
        "cases": cases, "calls": args.calls,
        "sample_rationale": SAMPLE, "stage77_historical": STAGE77_SAMPLE,
        "baseline_rates": base_rates, "candidate_rates": arm_rates,
        "regressed": regressed, "records": records}, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
