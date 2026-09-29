"""Rebuild Stage 78's claimed successes on BOTH kernels. Offline.

Every call recorded as a Stage 78 strict success is rebuilt from the
model's own raw text and checked against the closed forms in
`ground_truth78` -- on CadQuery and on FreeCAD, independently. A rate is
a claim about geometry, and a claim about geometry that was never built
twice is a claim about one kernel's opinion.

Calls no model. Spends nothing.

    PYTHONPATH=. python3 verify78.py confirm78.json explore78.json
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict, List

HERE = pathlib.Path(__file__).resolve().parent
STAGE77 = HERE.parent / "stage77-multibody-corpus"
for path in (HERE, STAGE77):
    sys.path.insert(0, str(path))

import evaluate78 as E78                                          # noqa: E402
import ground_truth77 as G77                                      # noqa: E402
import ground_truth78 as G78                                      # noqa: E402

from cad_experimental.cad_backend import resolve_backend          # noqa: E402
from cad_experimental.executor import execute_plan                # noqa: E402
from cad_experimental.parser import parse_plan                    # noqa: E402
from cad_experimental.validation import validate_plan             # noqa: E402

TOLERANCE = G78.VOLUME_TOLERANCE


def _close(measured: float, truth: float) -> bool:
    if truth == 0.0:
        return abs(measured) <= TOLERANCE
    return abs(measured - truth) / abs(truth) <= TOLERANCE


def successes(paths: List[pathlib.Path]) -> List[Dict[str, Any]]:
    """Every turn a run recorded as a Stage 78 strict success."""
    out: List[Dict[str, Any]] = []
    for path in paths:
        payload = json.loads(path.read_text())
        for arm, record in payload["records"].items():
            for attempt in record.get("attempts", []):
                case = attempt.get("case")
                if case not in G78.CASES:
                    continue
                for turn in attempt["turns"]:
                    raw = (turn.get("observation") or {}).get("raw_text")
                    passed77 = not any(c in G77.FAILURE_CODES
                                       for c in turn["codes"])
                    row = E78.score_turn(case, raw, passed77,
                                         label=turn.get("label"))
                    if row.get("stage78_strict"):
                        out.append({"run": path.name, "arm": arm,
                                    "case": case, "raw": raw,
                                    "attempt": attempt["attempt"]})
    return out


def build(raw: str, backend) -> Dict[str, Any]:
    plan = parse_plan(json.loads(raw))
    verdict = validate_plan(plan)
    if not verdict.valid:
        return {"ok": False, "why": "invalid plan"}
    execution = execute_plan(plan, backend=backend)
    if not execution.succeeded:
        return {"ok": False, "why": execution.failure.message
                if execution.failure else "build failed"}
    return {"ok": True,
            "bodies": sorted((b.id, round(b.measurement.volume, 9),
                              b.measurement.face_count)
                             for b in execution.bodies)}


def expected_volumes(case: str) -> Dict[str, float]:
    if case == "ED-02":
        return {"cube": G78.CUBE_VOLUME, "pin": G78.PIN_BORED_VOLUME}
    return {}


def main(argv: List[str]) -> int:
    paths = [pathlib.Path(a) for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    rows = successes(paths)
    print(f"{len(rows)} claimed Stage 78 successes to rebuild, "
          f"from {', '.join(p.name for p in paths)}\n")

    built: Dict[str, Dict[int, Any]] = {}
    for engine in ("cadquery", "freecad"):
        os.environ["CAD_BACKEND"] = engine
        try:
            backend = resolve_backend()
        except Exception as exc:                       # noqa: BLE001
            print(f"{engine}: UNAVAILABLE ({exc}); skipped")
            continue
        built[engine] = {}
        agree = mismatch = failed = 0
        for index, row in enumerate(rows):
            result = build(row["raw"], backend)
            built[engine][index] = result
            if not result["ok"]:
                failed += 1
                continue
            wanted = expected_volumes(row["case"])
            if wanted:
                got = {i: v for i, v, _ in result["bodies"]}
                if (set(got) == set(wanted)
                        and all(_close(got[k], wanted[k]) for k in wanted)):
                    agree += 1
                else:
                    mismatch += 1
                    print(f"  MISMATCH {row['arm']}/{row['case']}"
                          f"#{row['attempt']}: {result['bodies']}")
            else:
                agree += 1
        print(f"{engine} {backend.version()}: {agree} matched the closed "
              f"form, {mismatch} mismatched, {failed} failed to build")

    if len(built) == 2:
        same = sum(1 for i in range(len(rows))
                   if built["cadquery"].get(i) == built["freecad"].get(i))
        print(f"\ncross-kernel: {same}/{len(rows)} bit-identical "
              f"body for body")
        if same != len(rows):
            print("  *** the two kernels DISAGREE on at least one part ***")
            return 1
    else:
        print("\nonly one kernel available; no cross-kernel claim is made.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
