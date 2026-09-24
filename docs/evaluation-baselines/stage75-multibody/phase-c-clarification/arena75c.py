"""Phase C's arm runner: `arena75.py`, with one prompt arm patched in.

It adds NOTHING to the measurement. The corpus, the ground truth, the
observer, the grader, the failure codes, the labels and the refusal fixture
are all `arena75`'s and are imported, never copied -- a second copy of any of
them would be a second opinion about what a clarification is.

What it adds is exactly one thing: it swaps `system_prompt` for an arm's text
for the life of one run, and records WHICH text was used. Stage 70's
`arena70.py` did the same and the pattern is taken from it unchanged.

THE IDENTITY GUARD IS KEPT, AND NARROWED RATHER THAN DISABLED.
`arena75.check_identity()` refuses to run when the live route is not what the
corpus was built against. An arm changes the prompt ON PURPOSE, so the prompt
key alone is exempted -- and the arm's own fingerprint is written into the
record in its place. The model and the grammar are still checked exactly as
before, because an arm that also moved one of those would not be one variable.

USAGE

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    ARENA=../../docs/evaluation-baselines/stage75-multibody/phase-c-clarification

    python3 $ARENA/arena75c.py --list                 # offline
    python3 $ARENA/arena75c.py --arm CA-naming-example --check     # offline
    python3 $ARENA/arena75c.py --live --arm CA-naming-example \\
        --calls 16 --out $ARENA/arm-CA.json

`--live` is required, for the same reason it is required in `arena75`: a
credential's presence never starts a run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import arena75 as A                                   # noqa: E402
import ground_truth75 as G                            # noqa: E402
import variants_c as V                                # noqa: E402

from cad_experimental import generation as gen        # noqa: E402
from cad_experimental import prompt as prompt_module  # noqa: E402


def patched(arm: str):
    """Swap `system_prompt` for the arm's text, and put it back afterwards.

    Both module bindings are replaced: `prompt.system_prompt` is the source
    and `generation.system_prompt` is the name the live route actually calls.
    Patching one and not the other is a silent no-op -- the run completes and
    reports the baseline's numbers under the arm's name.
    """
    text = V.VARIANTS[arm]()
    original_prompt = prompt_module.system_prompt
    original_gen = gen.system_prompt

    class _Patch:
        def __enter__(self):
            prompt_module.system_prompt = lambda: text
            gen.system_prompt = lambda: text
            return text

        def __exit__(self, *exc):
            prompt_module.system_prompt = original_prompt
            gen.system_prompt = original_gen
            return False

    return _Patch()


def check_identity_for_arm() -> list:
    """Everything `arena75` checks, minus the prompt the arm is varying."""
    return [
        drift for drift in A.check_identity()
        if not drift.startswith("prompt_")
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", default="C0-baseline", choices=sorted(V.VARIANTS))
    ap.add_argument("--calls", type=int, default=16,
                    help="calls per case (default 16, the pre-registered MIN_N)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--list", action="store_true",
                    help="print every arm with its fingerprint. Calls nothing.")
    ap.add_argument("--check", action="store_true",
                    help="show what the arm changes. Calls nothing.")
    ap.add_argument("--group", default=G.REFUSAL,
                    choices=[G.CREATION, G.REFUSAL],
                    help="which group to run. Default: refusal, the group "
                         "Phase C is about. The CREATION group is how an "
                         "arm's regression clause is checked -- the rule's "
                         "creation test is vacuous for a run that never "
                         "touches a creation case.")
    ap.add_argument("--live", action="store_true",
                    help="REQUIRED to make real calls.")
    args = ap.parse_args(argv)

    base = V.VARIANTS["C0-baseline"]()
    if args.list:
        print(f"baseline {V.fingerprint(base)[:16]} {len(base)} chars\n")
        for name in sorted(V.VARIANTS):
            text = V.VARIANTS[name]()
            print(f"  {name:28} {V.fingerprint(text)[:16]} {len(text):6d} "
                  f"({len(text) - len(base):+d})")
        print("\nnothing was called.")
        return 0

    text = V.VARIANTS[args.arm]()
    if args.check:
        import difflib
        print(f"arm {args.arm}: {V.fingerprint(text)[:16]} {len(text)} chars "
              f"({len(text) - len(base):+d} from baseline)\n")
        for line in difflib.unified_diff(
                base.split("\n"), text.split("\n"),
                "baseline", args.arm, lineterm="", n=1):
            print("  " + line)
        drift = check_identity_for_arm()
        print("\nidentity (prompt excluded, it is what varies):",
              "MATCHES" if not drift else drift)
        print("nothing was called.")
        return 0

    if not args.live:
        print("refusing to run without --live: a credential's presence never "
              "starts a run.")
        return 2
    if A.bridge_credential() is None:
        print("no credential in this process; nothing attempted.")
        return 2

    drift = check_identity_for_arm()
    if drift:
        raise SystemExit(
            "the live route is not what this corpus was built against: "
            + "; ".join(drift))

    # ACTIVE only, and refusal only. A retired case is never run again --
    # `expected()` refuses it by name -- and an arm about clarification
    # has no business spending calls on the creation group.
    cases = [G.CASES_BY_NAME[n] for n in G.ACTIVE
             if G.CASES_BY_NAME[n].group == args.group]
    print(f"arm {args.arm}: {V.fingerprint(text)[:16]} {len(text)} chars "
          f"({len(text) - len(base):+d} from the committed prompt)")
    print(f"{len(cases)} {args.group} cases x {args.calls} calls = "
          f"{len(cases) * args.calls} live calls")

    with patched(args.arm) as patched_text:
        # Proof the patch took, recorded in the run rather than assumed: the
        # live route's own binding is read back here, not the arm's variable.
        if gen.system_prompt() != patched_text:
            raise SystemExit("the patch did not reach the live route")
        record = A.run(cases, args.calls)

    record["arm"] = args.arm
    record["arm_fingerprint"] = V.fingerprint(text)
    record["arm_characters"] = len(text)
    record["baseline_fingerprint"] = V.fingerprint(base)
    record["prompt_is_committed"] = args.arm == "C0-baseline"

    print(json.dumps(record["summary"].get("clarification", {}), indent=1))

    if args.out:
        out = Path(args.out)
        if any(part in A.PROTECTED for part in out.parts):
            raise SystemExit(f"refusing to write into an earlier baseline: {out}")
        out.write_text(json.dumps(record, indent=1))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
