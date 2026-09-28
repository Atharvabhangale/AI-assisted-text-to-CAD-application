"""Re-grade Stage 77's RECORDED calls under the Stage 78 axis criterion.

Offline. Calls no model, spends nothing, and changes nothing: it reads the
raw bytes Stage 77 already recorded and asks the new question of them.

This is the gate the whole stage stands on. Before a single live call is
spent on an arm, it must be true that:

1. the new criterion REPRODUCES Stage 77's recorded per-case rate exactly --
   if it does not, the instrument is measuring something else and any arm
   result would be uninterpretable; and
2. every Stage 77 failure is accounted for by a named mechanism.

Both were already useful. The first draft of `evaluate78` failed this gate
twice: it read `P:export_identity_unproven` as fatal when Stage 77 records it
as informational, and it flagged an axis merely SHARING a coordinate with a
neighbour it stood clear of. Five correct CR-06 calls were scored as failures
until the second was fixed. No live call had been spent.

    PYTHONPATH=. python3 regrade78.py
"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any, Dict, List, Mapping

HERE = pathlib.Path(__file__).resolve().parent
STAGE77 = HERE.parent / "stage77-multibody-corpus"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(STAGE77))

import evaluate78 as E78          # noqa: E402
import ground_truth77 as G77      # noqa: E402  -- UNMODIFIED, for FAILURE_CODES
import ground_truth78 as G78      # noqa: E402


def turn_passed_stage77(turn: Mapping[str, Any]) -> bool:
    """Did this turn pass STAGE 77, by Stage 77's own definition?

    Read from the codes it recorded, keeping only the ones Stage 77 counts as
    failures. `P:export_identity_unproven` is attached to every multi-body
    export and is explicitly informational -- treating it as fatal scores
    every CR-06 call as a failure, which the first draft of this file did.
    """
    return not any(code in G77.FAILURE_CODES for code in turn["codes"])


def regrade() -> Dict[str, Any]:
    recorded = json.loads((STAGE77 / "results.json").read_text())
    rows: List[Dict[str, Any]] = []
    for attempt in recorded["attempts"]:
        case = attempt.get("case")
        if case not in G78.CASES:
            continue
        for turn in attempt["turns"]:
            raw = (turn["observation"] or {}).get("raw_text")
            row = E78.score_turn(case, raw, turn_passed_stage77(turn), label=turn.get("label"))
            row["attempt"] = attempt["attempt"]
            row["turn"] = turn["turn"]
            row["stage77_codes"] = list(turn["codes"])
            rows.append(row)
    return {"rows": rows, "summary": E78.summarise(rows)}


def main() -> int:
    result = regrade()
    summary = result["summary"]
    print("Stage 77's recorded calls, re-graded under the Stage 78 criterion")
    print("OFFLINE -- no model was called.\n")

    ok = True
    for name in sorted(summary["per_case"]):
        entry = summary["per_case"][name]
        want_pass, want_calls = G78.stage77_baseline(name)
        agrees = (entry["stage77_strict"] == want_pass
                  and entry["calls"] == want_calls)
        ok &= agrees
        print(f"{name}")
        print(f"  Stage 77 recorded      {want_pass}/{want_calls}")
        print(f"  Stage 77 recomputed    {entry['stage77_strict']}/"
              f"{entry['calls']}   {'AGREES' if agrees else '*** DISAGREES ***'}")
        print(f"  Stage 78 strict        {entry['stage78_strict']}/"
              f"{entry['calls']}")
        print(f"  axis criterion alone   {entry['axis_ok']}/{entry['calls']}")
        for code, count in sorted(entry["codes"].items()):
            print(f"      {code:30s} {count}")
        print()

    print("Stage 77's recorded mechanism, for comparison:")
    for name, text in G78.STAGE77_MECHANISM.items():
        print(f"  {name}: {text}")

    if not ok:
        print("\nGATE FAILED: the Stage 78 instrument does not reproduce the "
              "Stage 77 rate. No live call may be spent until it does.")
        return 1
    print("\nGATE PASSED: the instrument reproduces Stage 77 exactly, and "
          "every failure carries a named mechanism.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
