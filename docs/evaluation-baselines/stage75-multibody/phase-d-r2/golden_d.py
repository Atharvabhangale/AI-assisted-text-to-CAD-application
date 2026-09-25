"""Phase D's single-body regression: Stage 68's golden EXPLICIT request.

The adoption rule's clause 6 says single-body behaviour must not regress,
and the multi-body corpus cannot answer that -- every case in it declares
two bodies. The instrument that CAN is Stage 68's, which measures the
golden hollow enclosure against an immutable ground truth: one solid, six
5 mm plates, envelope 40x20x20, 18 faces, 42 edges, volume
11492.035526276899.

Stage 68's `arena.py`, `evaluate.py` and `ground_truth.py` are imported and
used EXACTLY as they are, the way Stage 69's and Stage 70's runners do. This
file adds one thing: it patches in a Phase D arm for the life of one run,
and writes only into Phase D's own directory.

IT IS A CONTROLLED COMPARISON, NOT A COMPARISON WITH HISTORY. Stage 70's
recorded 44/48 was measured on prompt `2026-09-18.5`; the committed prompt
is `2026-09-24.2` and three phases of Stage 75 have moved it since. So the
control is run in the SAME session as the arm, and the older number is not
quoted as a baseline.

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    ARENA=../../docs/evaluation-baselines/stage75-multibody/phase-d-r2
    python3 $ARENA/golden_d.py --live --arm D0-baseline --calls 24 \\
        --out $ARENA/golden-D0.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE68 = HERE.parent.parent / "stage68-benchmark-disambiguation"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(STAGE68))

import arena as stage68_arena                          # noqa: E402 UNMODIFIED
import ground_truth as G68                             # noqa: E402 UNMODIFIED
import variants_d as V                                  # noqa: E402

from cad_experimental import generation as gen          # noqa: E402
from cad_experimental import prompt as prompt_module    # noqa: E402
from cad_experimental.config import bridge_credential   # noqa: E402

#: This file writes into Phase D's own directory and nowhere else -- an
#: ALLOWLIST of one. A hand-written blocklist of earlier stages is a copy
#: that goes stale: the first draft of this one omitted the three baselines
#: (`stage40-v1-vs-operation-plan`, `stage43-structured-output`,
#: `stage48-widened-schema`) that this branch's own invariants call
#: immutable and SHA-256 pinned.
WRITES_ONLY_INTO = HERE

#: The EXPLICIT request, never the ambiguous original. Stage 68 measured the
#: original at 0/8 strict and its own record says the two are never summed:
#: one measures comprehension of an under-specified spec, the other measures
#: CAD capability, and only the second is a regression signal.
REQUEST_KEY = "explicit"


def run(calls: int, arm: str) -> dict:
    text = V.VARIANTS[arm]()
    # The COMMITTED TEXT, read through the function the patch replaces.
    # `prompt_fingerprint()` hashes the module constant, which no patch
    # touches, so a before/after comparison on it compares a constant to
    # itself and can never fire -- a post-condition immune to the failure
    # it names.
    committed = (prompt_module.PROMPT_VERSION, prompt_module.system_prompt())

    original_prompt = prompt_module.system_prompt
    original_gen = gen.system_prompt
    prompt_module.system_prompt = lambda: text
    gen.system_prompt = lambda: text
    try:
        if gen.system_prompt() != text:
            raise SystemExit("the patch did not reach the live route")
        record = stage68_arena.run(REQUEST_KEY, calls)
    finally:
        prompt_module.system_prompt = original_prompt
        gen.system_prompt = original_gen
    if (prompt_module.PROMPT_VERSION,
            prompt_module.system_prompt()) != committed:
        raise SystemExit("the committed prompt did not survive the arm")

    record["phase"] = "D"
    record["arm"] = arm
    record["arm_fingerprint"] = V.fingerprint(text)
    record["arm_characters"] = len(text)
    record["is_committed_prompt"] = arm == "D0-baseline"
    record["reuses_unmodified"] = ["stage68/arena.py", "stage68/evaluate.py",
                                   "stage68/ground_truth.py"]
    return record


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", default="D0-baseline", choices=sorted(V.VARIANTS))
    ap.add_argument("--calls", type=int, default=24)
    ap.add_argument("--out", default=None)
    ap.add_argument("--live", action="store_true",
                    help="required; a credential's presence never starts a run")
    args = ap.parse_args(argv)

    if not args.live:
        print("refusing to run without --live")
        return 2
    if bridge_credential() is None:
        print("no credential in this process; nothing attempted")
        return 2

    text = V.VARIANTS[args.arm]()
    # An adopted arm renders the committed prompt, so running it by name
    # would record D0's numbers under the arm's name. See `arena75d`.
    if args.arm != "D0-baseline" and text == V.VARIANTS["D0-baseline"]():
        raise SystemExit(
            f"{args.arm} now renders the committed prompt byte for byte; it "
            "has been adopted.")
    print(f"arm {args.arm}: {V.fingerprint(text)[:16]} {len(text)} chars")
    print(f"request {REQUEST_KEY}: {G68.REQUESTS[REQUEST_KEY][:70]}...")
    print(f"{args.calls} live calls")

    record = run(args.calls, args.arm)
    summary = record["summary"]
    for key in ("STRICT_SUCCESS", "built", "thickness_correct",
                "plate_count_correct", "envelope_correct", "volume_correct"):
        print(f"  {key:22} {summary.get(key)}/{summary['calls']}")
    print(f"  failure_codes {summary.get('failure_codes')}")
    print(f"  p_codes       {summary.get('p_codes')}")

    if args.out:
        out = Path(args.out).resolve()
        if out.parent != WRITES_ONLY_INTO:
            raise SystemExit(
                f"refusing to write outside this phase's own directory: {out}")
        out.write_text(json.dumps(record, indent=1))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
