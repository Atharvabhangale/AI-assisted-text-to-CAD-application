"""Stage 69 arena: widen the residual arm on the EXPLICIT request.

THE PROMPT IS NOT TOUCHED and neither is the evaluator. Stage 68's
`arena.py`, `evaluate.py` and `ground_truth.py` are imported and used exactly
as they are; this module adds `axis_analysis` on top and writes its own file
in its own directory. Stage 68's baselines are never opened for writing.

The only reason this stage exists is that one attempt in eight is not a rate
anyone can reason about. Phase 1 buys a denominator.
"""
from __future__ import annotations

import argparse, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
STAGE68 = HERE.parent / "stage68-benchmark-disambiguation"
sys.path.insert(0, str(STAGE68))

import arena as stage68_arena          # noqa: E402  -- UNMODIFIED
import ground_truth as G               # noqa: E402  -- UNMODIFIED
import prompt_guard                    # noqa: E402
import axis_analysis as AX             # noqa: E402
import variants as V                   # noqa: E402

from cad_experimental import generation as gen               # noqa: E402
from cad_experimental import prompt as prompt_module         # noqa: E402
from cad_experimental.config import bridge_credential        # noqa: E402


def run(calls: int, request_key: str = "explicit",
        variant: str = "A0-baseline") -> dict:
    """One arm. The variant patches `system_prompt` for this call only."""
    spec = V.VARIANTS[variant]
    text = spec["text"]()
    committed = prompt_guard.identity()

    original_prompt = prompt_module.system_prompt
    original_gen = gen.system_prompt
    prompt_module.system_prompt = lambda: text
    gen.system_prompt = lambda: text
    try:
        record = stage68_arena.run(request_key, calls)
    finally:
        prompt_module.system_prompt = original_prompt
        gen.system_prompt = original_gen

    restored = prompt_guard.identity()
    if restored != committed:
        raise SystemExit("the committed prompt did not survive the arm")
    record["stage"] = 69
    record["arm"] = variant
    record["hypothesis"] = spec["hypothesis"]
    record["variant_changed"] = spec["changed"]
    record["variant_fingerprint"] = V.fingerprint(text)
    record["variant_characters"] = len(text)
    record["is_committed_prompt"] = text == V._baseline()
    record["committed_prompt"] = committed
    record["prompt_file_unmodified"] = True
    record["reuses_stage68"] = ["arena.py", "evaluate.py", "ground_truth.py"]

    analyses = []
    for row in record["attempts"]:
        seen = row.get("observed")
        analysis = AX.analyse(seen) if seen else None
        row["axis_analysis"] = analysis
        if analysis is not None:
            analyses.append(analysis)
    record["axis_summary"] = AX.summarise(analyses)
    record["attempts_with_a_plan"] = len(analyses)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=24)
    ap.add_argument("--request", default="explicit", choices=sorted(G.REQUESTS))
    ap.add_argument("--variant", default="A0-baseline",
                    choices=sorted(V.VARIANTS))
    ap.add_argument("--out", default=None)
    ap.add_argument("--live", action="store_true",
                    help="required; a credential's presence never starts a run")
    args = ap.parse_args(argv)
    if not args.live:
        print("refusing to run without --live"); return 2
    came_from = bridge_credential()
    if came_from is None:
        print("no credential in this process; nothing attempted"); return 2
    print(f"credential from {came_from} (name only)")
    print(f"prompt {prompt_module.PROMPT_VERSION} "
          f"{prompt_module.prompt_fingerprint()[:16]} (UNCHANGED)")

    record = run(args.calls, args.request, args.variant)
    s, x = record["summary"], record["axis_summary"]
    print(f"\nSTAGE 69 arm '{args.variant}' on '{args.request}', "
          f"{s['calls']} live calls")
    print(f"  variant {record['variant_fingerprint'][:16]} "
          f"({record['variant_characters']} chars, "
          f"{'COMMITTED' if record['is_committed_prompt'] else 'patched in memory'})")
    print(f"  structured {s['structured_output']}/{s['calls']}  "
          f"valid {s['validated']}/{s['calls']}  built {s['built']}/{s['calls']}")
    print(f"  thickness {s['thickness_correct']}/{s['calls']}  "
          f"envelope {s['envelope_correct']}/{s['calls']}  "
          f"topology {s['topology_correct']}/{s['calls']}  "
          f"volume {s['volume_correct']}/{s['calls']}")
    print(f"  ==> STRICT SUCCESS {s['STRICT_SUCCESS']}/{s['calls']}")
    print(f"  failure codes: {s['failure_codes']}")
    print("  per-axis bores centred: " + "  ".join(
        f"{a}: {x['per_axis_centred'][a]['centred']}/{x['per_axis_centred'][a]['present']}"
        for a in G.BORE_AXES))
    print(f"  all bores centred {x['all_bores_centred']}/{x['calls']}   "
          f"cross-axis triple reuse {x['cross_axis_triple_reuse']}/{x['calls']}   "
          f"all three identical {x['all_three_triples_identical']}/{x['calls']}   "
          f"other bore error {x['other_bore_error']}/{x['calls']}")

    out = pathlib.Path(args.out or (HERE / f"arm-{args.variant}.json"))
    if "stage68-benchmark-disambiguation" in str(out.resolve()):
        print("refusing to write inside Stage 68's directory"); return 2
    out.write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
