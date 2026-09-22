"""Stage 70 arena: one arm, N live calls on the EXPLICIT golden request.

Stage 68's `arena.py`, `evaluate.py` and `ground_truth.py` and Stage 69's
`axis_analysis.py` are imported and used EXACTLY as they are. The immutable
ground truth stays the sole source of truth for the strict criterion; this
stage adds `target_analysis` on top and writes only into its own directory.
"""
from __future__ import annotations

import argparse, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
STAGE68 = HERE.parent / "stage68-benchmark-disambiguation"
STAGE69 = HERE.parent / "stage69-bore-axis-centre"
sys.path.insert(0, str(STAGE68))
sys.path.insert(0, str(STAGE69))

import arena as stage68_arena          # noqa: E402  -- UNMODIFIED
import ground_truth as G               # noqa: E402  -- UNMODIFIED
import axis_analysis as AX             # noqa: E402  -- Stage 69, UNMODIFIED
import prompt_guard                    # noqa: E402  -- Stage 69, UNMODIFIED
import target_analysis as TA           # noqa: E402
import variants70 as V                 # noqa: E402

from cad_experimental import generation as gen               # noqa: E402
from cad_experimental import prompt as prompt_module         # noqa: E402
from cad_experimental.config import bridge_credential        # noqa: E402

PROTECTED = ("stage68-benchmark-disambiguation", "stage69-bore-axis-centre",
             "stage67-golden-spatial", "stage66-enclosure-layout",
             "stage65-union-target")


def run(calls: int, request_key: str = "explicit",
        variant: str = "B0-baseline") -> dict:
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
    if prompt_guard.identity() != committed:
        raise SystemExit("the committed prompt did not survive the arm")

    record["stage"] = 70
    record["arm"] = variant
    record["hypothesis"] = spec["hypothesis"]
    record["variant_changed"] = spec["changed"]
    record["variant_fingerprint"] = V.fingerprint(text)
    record["variant_characters"] = len(text)
    record["is_committed_prompt"] = text == V._baseline()
    record["committed_prompt"] = committed
    record["reuses_unmodified"] = [
        "stage68/arena.py", "stage68/evaluate.py", "stage68/ground_truth.py",
        "stage69/axis_analysis.py", "stage69/prompt_guard.py"]

    axis, targets = [], []
    for row in record["attempts"]:
        seen = row.get("observed")
        if seen:
            row["axis_analysis"] = AX.analyse(seen)
            axis.append(row["axis_analysis"])
        ops = row.get("operations")
        if ops:
            row["target_analysis"] = TA.analyse(ops, text)
            targets.append(row["target_analysis"])
    record["axis_summary"] = AX.summarise(axis)
    record["target_summary"] = TA.summarise(targets)
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calls", type=int, default=48)
    ap.add_argument("--request", default="explicit", choices=sorted(G.REQUESTS))
    ap.add_argument("--variant", default="B0-baseline", choices=sorted(V.VARIANTS))
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
    print(f"committed prompt {prompt_module.PROMPT_VERSION} "
          f"{prompt_module.prompt_fingerprint()[:16]}")

    record = run(args.calls, args.request, args.variant)
    s, t = record["summary"], record["target_summary"]
    print(f"\nSTAGE 70 arm '{args.variant}' on '{args.request}', "
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
    print(f"  union ids: {t['union_ids']}")
    print(f"  post-union target roles: {t['target_roles']}")
    print(f"  wrong target | union id mandated     : "
          f"{t['wrong_target_given_mandated_id'][0]}/"
          f"{t['wrong_target_given_mandated_id'][1]}")
    print(f"  wrong target | union id NOT mandated : "
          f"{t['wrong_target_given_other_id'][0]}/"
          f"{t['wrong_target_given_other_id'][1]}")

    out = pathlib.Path(args.out or (HERE / f"arm-{args.variant}.json"))
    if any(p in str(out.resolve()) for p in PROTECTED):
        print("refusing to write inside an earlier stage's directory"); return 2
    out.write_text(json.dumps(record, indent=1))
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
