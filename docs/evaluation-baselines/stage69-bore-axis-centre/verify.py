"""Stage 69 Phase 4: rebuild the claimed successes from RECORDED model output.

No model is called. Each attempt's raw text is re-parsed, re-validated and
re-executed on a named backend, and every number is compared against
`ground_truth`'s constants -- never against the plan's own choices.

Run it once per backend. Both must agree, and both must agree with the
closed form, or the claim does not stand.
"""
from __future__ import annotations

import argparse, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "stage68-benchmark-disambiguation"))

import ground_truth as G                              # noqa: E402
from cad_experimental.parser import parse_plan_text    # noqa: E402
from cad_experimental.validation import validate_plan  # noqa: E402
from cad_experimental.cad_backend import resolve_backend  # noqa: E402
from cad_experimental.executor import execute_plan     # noqa: E402


def verify(files: list[pathlib.Path]) -> dict:
    backend = resolve_backend()
    want = G.expected()
    rows, failures = [], []
    for path in files:
        record = json.loads(path.read_text())
        for row in record["attempts"]:
            if not row.get("STRICT_SUCCESS"):
                continue
            plan = parse_plan_text(row["raw_text"])
            verdict = validate_plan(plan)
            assert verdict.valid, f"{path.name} #{row['attempt']} no longer validates"
            execution = execute_plan(plan, backend=backend)
            assert execution.succeeded, f"{path.name} #{row['attempt']} no longer builds"
            m = execution.bodies[0].measurement
            envelope = tuple(round(hi - lo, 6)
                             for lo, hi in zip(m.minimum, m.maximum))
            ok = (m.solid_count == want["solid_count"]
                  and m.face_count == want["face_count"]
                  and m.edge_count == want["edge_count"]
                  and sorted(envelope) == sorted(want["envelope"])
                  and abs(m.volume - want["volume"]) <= G.VOLUME_TOLERANCE)
            rows.append({"file": path.name, "attempt": row["attempt"],
                         "volume": m.volume, "delta": abs(m.volume - want["volume"]),
                         "solids": m.solid_count, "faces": m.face_count,
                         "edges": m.edge_count, "envelope": list(envelope),
                         "matches_ground_truth": ok})
            if not ok:
                failures.append(rows[-1])
    return {"backend": backend.name, "backend_version": str(backend.version()),
            "expected": {"volume": want["volume"], "faces": want["face_count"],
                         "edges": want["edge_count"], "solids": want["solid_count"],
                         "envelope": list(want["envelope"])},
            "rebuilt": len(rows), "failures": failures, "rows": rows,
            "provenance": "MODEL_GENERATED (rebuilt from recorded output; "
                          "no model called)"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    result = verify([pathlib.Path(f) for f in args.files])
    volumes = sorted({round(r["volume"], 9) for r in result["rows"]})
    deltas = [r["delta"] for r in result["rows"]]
    print(f"backend {result['backend']} {result['backend_version']}")
    print(f"  rebuilt {result['rebuilt']} claimed successes")
    print(f"  distinct volumes: {volumes}")
    print(f"  expected        : {result['expected']['volume']}")
    print(f"  max |delta|     : {max(deltas):.3e}" if deltas else "  none")
    print(f"  mismatches      : {len(result['failures'])}")
    if args.out:
        pathlib.Path(args.out).write_text(json.dumps(result, indent=1))
        print(f"  written to {args.out}")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
