"""Phase E's arm runner: Phase D's runner, with Phase E's arms.

It copies nothing. `arena75d` holds the patching, the identity guard and the
record assembly; `arena75` holds the corpus, the fixture, the observer, the
grader and the labels; and this file supplies the arm registry and its own
output directory. A second copy of any of them would be a second opinion
about what an arm run is, and Phase C paid for one of those already.

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    ARENA=../../docs/evaluation-baselines/stage75-multibody/phase-e-r2-tail

    python3 $ARENA/arena75e.py --list                         # offline
    python3 $ARENA/arena75e.py --arm E1-trigger-narrowed --check
    python3 $ARENA/arena75e.py --live --arm E1-trigger-narrowed \\
        --case R2 --calls 48 --out $ARENA/arm-E1.json

`--live` is required: a credential's presence never starts a run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "phase-d-r2"))

import arena75 as A                                    # noqa: E402
import arena75d as AD                                  # noqa: E402
import classify_d as CD                                # noqa: E402
import ground_truth75 as G                             # noqa: E402
import variants_e as V                                 # noqa: E402

from cad_experimental import generation as gen         # noqa: E402

# The runner reads its arm registry and its output directory from module
# globals, so Phase E supplies its own without editing Phase D's file --
# whose recorded runs must stay reproducible by the code that made them.
AD.V = V
AD.HERE = HERE

#: The baseline arm's name, for the alias guard. Phase D's guard compares
#: against its own baseline NAME; Phase E's baseline is called something
#: else, so the comparison is made here on the TEXT, which is what the guard
#: was really about.
BASELINE = "E0-baseline"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", default=BASELINE, choices=sorted(V.VARIANTS))
    ap.add_argument("--case", action="append", default=None,
                    choices=sorted(G.CASES_BY_NAME))
    ap.add_argument("--group", default=None,
                    choices=[G.CREATION, G.REFUSAL])
    ap.add_argument("--calls", type=int, default=32,
                    help="calls per case (default 32, Phase E's MIN_N)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--live", action="store_true",
                    help="REQUIRED to make real calls.")
    args = ap.parse_args(argv)

    base = V.VARIANTS[BASELINE]()
    text = V.VARIANTS[args.arm]()

    if args.list:
        print(f"baseline {V.fingerprint(base)[:16]} {len(base)} chars\n")
        for name in sorted(V.VARIANTS):
            body = V.VARIANTS[name]()
            role = ("baseline" if name == BASELINE
                    else "CANDIDATE" if name in V.CANDIDATES else "control")
            print(f"  {name:24} {role:9} {V.fingerprint(body)[:16]} "
                  f"{len(body):6d} ({len(body) - len(base):+d})")
        print("\nnothing was called.")
        return 0

    if args.check:
        import difflib
        print(f"arm {args.arm}: {V.fingerprint(text)[:16]} {len(text)} chars "
              f"({len(text) - len(base):+d} from the committed prompt)\n")
        for line in difflib.unified_diff(
                base.split("\n"), text.split("\n"),
                "baseline", args.arm, lineterm="", n=3):
            print("  " + line)
        drift = AD.check_identity_for_arm()
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

    drift = AD.check_identity_for_arm()
    if drift:
        raise SystemExit(
            "the live route is not what this corpus was built against: "
            + "; ".join(drift))

    # An adopted arm renders the committed prompt, so running it by NAME
    # would record the baseline's numbers under the arm's name. Compared on
    # the TEXT, which is what the guard is really about.
    if args.arm != BASELINE and text == base:
        raise SystemExit(
            f"{args.arm} now renders the committed prompt byte for byte; it "
            "has been adopted. Running it would record the baseline's "
            "numbers under its name.")

    if args.case:
        retired = [n for n in args.case if n in G.RETIRED]
        if retired:
            print("refusing to run retired cases: " + ", ".join(retired))
            return 2
        cases = [G.CASES_BY_NAME[n] for n in args.case]
    else:
        cases = [G.CASES_BY_NAME[n] for n in G.ACTIVE]
    if args.group:
        cases = [c for c in cases if c.group == args.group]
    if not cases:
        print("no cases selected")
        return 2

    role = ("baseline" if args.arm == BASELINE
            else "CANDIDATE" if args.arm in V.CANDIDATES else "CONTROL")
    print(f"arm {args.arm} ({role}): {V.fingerprint(text)[:16]} {len(text)} "
          f"chars ({len(text) - len(base):+d} from the committed prompt)")
    print(f"{len(cases)} case(s) x {args.calls} calls = "
          f"{len(cases) * args.calls} live calls: "
          f"{', '.join(c.name for c in cases)}")

    with AD.patched(args.arm) as patched_text:
        if gen.system_prompt() != patched_text:
            raise SystemExit("the patch did not reach the live route")
        record = A.run(cases, args.calls)

    AD.record_the_arms_identity(record, text)
    record["phase"] = "E"
    record["arm"] = args.arm
    record["arm_role"] = role
    record["arm_fingerprint"] = V.fingerprint(text)
    record["arm_characters"] = len(text)
    record["baseline_fingerprint"] = V.fingerprint(base)
    record["prompt_is_committed"] = text == base
    graded = CD.refusal_rows(record["attempts"])
    if graded:
        record["classes"] = CD.distribution(graded)
        record["classes_per_case"] = {
            name: CD.distribution([a for a in graded if a["case"] == name])
            for name in sorted({a["case"] for a in graded})}
        print("\nclasses (every refusal attempt in exactly one):")
        for letter in CD.CLASS_ORDER:
            count = record["classes"][letter]
            if count:
                print(f"  {letter}  {count:3}  {CD.CLASS_TEXT[letter]}")
        divergences = [d for d in (CD.cross_check(a) for a in graded) if d]
        print(f"cross-check against grade(): "
              f"{'AGREES' if not divergences else divergences}")
    else:
        record["classes_note"] = ("no refusal case in this run; the A-F "
                                  "taxonomy describes refusals only")

    if args.out:
        out = Path(args.out).resolve()
        if out.parent != HERE:
            raise SystemExit(
                f"refusing to write outside this phase's own directory: {out}")
        out.write_text(json.dumps(record, indent=1))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
