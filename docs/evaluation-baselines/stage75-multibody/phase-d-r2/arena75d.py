"""Phase D's arm runner: `arena75.py`, with one prompt arm patched in.

It adds NOTHING to the measurement. The corpus, the ground truth, the
observer, the grader, the failure codes, the labels and the deterministic
refusal fixture are all `arena75`'s and are imported, never copied. Phase
C's `arena75c.py` is the module this is taken from; the one thing it adds
is a per-CASE selector, so a single case can be measured on its own without
spending calls on the rest of its group.

THE IDENTITY GUARD IS KEPT, AND NARROWED RATHER THAN DISABLED.
`arena75.check_identity()` refuses to run when the live route is not what
the corpus was built against. An arm changes the prompt ON PURPOSE, so the
prompt keys alone are exempted -- and the arm's own fingerprint is written
into the record in their place. The model and the grammar are still checked
exactly as before, because an arm that also moved one of those would not be
one variable.

USAGE

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    ARENA=../../docs/evaluation-baselines/stage75-multibody/phase-d-r2

    python3 $ARENA/arena75d.py --list                       # offline
    python3 $ARENA/arena75d.py --arm D1-missing-body-example --check
    python3 $ARENA/arena75d.py --live --arm D1-missing-body-example \\
        --case R2 --calls 32 --out $ARENA/arm-D1.json

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
import classify_d as CD                                # noqa: E402
import ground_truth75 as G                            # noqa: E402
import variants_d as V                                 # noqa: E402

from cad_experimental import generation as gen        # noqa: E402
from cad_experimental import prompt as prompt_module  # noqa: E402

#: This phase writes into its own directory and nowhere else -- an
#: ALLOWLIST of one, rather than a blocklist. `arena75.PROTECTED` is every
#: sibling DIRECTORY of `stage75-multibody`, which by construction protects
#: neither Phase A's and Phase B's records (loose files in
#: `stage75-multibody/`) nor the frozen instruments beside them.
WRITES_ONLY_INTO = "phase-d-r2"


def patched(arm: str):
    """Swap `system_prompt` for the arm's text, and put it back afterwards.

    Both module bindings are replaced: `prompt.system_prompt` is the source
    and `generation.system_prompt` is the name the live route actually
    calls, because `generation` imports it by name at module scope.
    Patching one and not the other is a silent no-op -- the run completes
    and reports the baseline's numbers under the arm's name.
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


def record_the_arms_identity(record: dict, text: str) -> dict:
    """Make the record's identity block describe ONE text: the arm's.

    `arena75.identity()` reads `prompt.prompt_fingerprint()`, which hashes
    the module CONSTANT that no patch touches, while `prompt_characters`
    reads `system_prompt()`, which the patch does replace. Left alone, the
    block names the committed prompt's fingerprint beside the arm's length.

    The fix is here and not in `patched()`. Patching `prompt_fingerprint`
    itself was tried and is wrong: `arena75.run` calls `check_identity()`
    INSIDE the patched context to refuse a run whose live route is not what
    the corpus was built against, so a patched fingerprint makes every arm
    look like drift and the run exits before its first call. The guard is
    doing its job; the record is what needed correcting, so the record is
    what this corrects -- after the run, from the arm's own text.
    """
    import hashlib
    record["prompt_fingerprint_of_committed_prompt"] = \
        record.get("prompt_fingerprint")
    record["prompt_fingerprint"] = hashlib.sha256(
        text.encode("utf-8")).hexdigest()
    record["prompt_characters"] = len(text)
    record["identity_note"] = (
        "`prompt_fingerprint` and `prompt_characters` describe the ARM's "
        "text -- what was actually sent. `prompt_version` is left as "
        "recorded: an arm has no version of its own and that field names "
        "the version it was patched over, whose fingerprint is kept beside "
        "it as `prompt_fingerprint_of_committed_prompt`.")
    return record


def check_identity_for_arm() -> list:
    """Everything `arena75` checks, minus the prompt the arm is varying."""
    return [
        drift for drift in A.check_identity()
        if not drift.startswith("prompt_")
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arm", default="D0-baseline", choices=sorted(V.VARIANTS))
    ap.add_argument("--case", action="append", default=None,
                    choices=sorted(G.CASES_BY_NAME),
                    help="one case; repeatable. Default: every ACTIVE case "
                         "in --group")
    ap.add_argument("--group", default=None,
                    choices=[G.CREATION, G.REFUSAL],
                    help="restrict to one group. The two are never pooled.")
    ap.add_argument("--calls", type=int, default=16,
                    help="calls per case (default 16, the pre-registered "
                         "MIN_N)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--list", action="store_true",
                    help="print every arm with its fingerprint. Calls "
                         "nothing.")
    ap.add_argument("--check", action="store_true",
                    help="show what the arm changes. Calls nothing.")
    ap.add_argument("--live", action="store_true",
                    help="REQUIRED to make real calls.")
    args = ap.parse_args(argv)

    base = V.VARIANTS["D0-baseline"]()
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
                "baseline", args.arm, lineterm="", n=2):
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

    # ACTIVE only. A retired case is never run again -- `expected()` refuses
    # it by name -- and naming one here is an error rather than a silent
    # skip, exactly as in `arena75.main`.
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

    # An adopted arm's idempotency guard makes it return the baseline. That
    # is correct and deliberate -- and it means a run of that arm would spend
    # live calls measuring D0 under the arm's NAME. Refuse it by comparing
    # the TEXT, not the name.
    if args.arm != "D0-baseline" and text == base:
        raise SystemExit(
            f"{args.arm} now renders the committed prompt byte for byte; it "
            "has been adopted. Running it would record D0's numbers under "
            "its name.")
    print(f"arm {args.arm}: {V.fingerprint(text)[:16]} {len(text)} chars "
          f"({len(text) - len(base):+d} from the committed prompt)")
    print(f"{len(cases)} case(s) x {args.calls} calls = "
          f"{len(cases) * args.calls} live calls: "
          f"{', '.join(c.name for c in cases)}")

    with patched(args.arm) as patched_text:
        # Proof the patch took, recorded in the run rather than assumed: the
        # LIVE route's own binding is read back here, not the arm's local.
        if gen.system_prompt() != patched_text:
            raise SystemExit("the patch did not reach the live route")
        record = A.run(cases, args.calls)

    record_the_arms_identity(record, text)
    record["arm"] = args.arm
    record["arm_fingerprint"] = V.fingerprint(text)
    record["arm_characters"] = len(text)
    record["baseline_fingerprint"] = V.fingerprint(base)
    # From the text, never from the name: an adopted arm IS the
    # committed prompt whatever it is called.
    record["prompt_is_committed"] = text == base
    # The taxonomy, computed once and stored beside the run so a reader does
    # not have to re-derive it -- and so a later re-classification can be
    # compared against what this run said at the time.
    #
    # REFUSAL rows only. The taxonomy describes refusals: a creation case
    # has no bodies a clarification must name and no pinned noun, so every
    # naming test is vacuously true and a correct BUILD reads as `E`. A
    # creation run therefore records no classes rather than six wrong ones.
    graded = CD.refusal_rows(record["attempts"])
    if graded:
        record["classes"] = CD.distribution(graded)
        record["classes_per_case"] = {
            case.name: CD.distribution(
                [a for a in graded if a["case"] == case.name])
            for case in cases if case.group == G.REFUSAL
        }
        print("\nclasses (every refusal attempt in exactly one):")
        for letter in CD.CLASS_ORDER:
            count = record["classes"][letter]
            if count:
                print(f"  {letter}  {count:3}  {CD.CLASS_TEXT[letter]}")
        divergences = [d for d in (CD.cross_check(a) for a in graded) if d]
        print(f"cross-check against grade(): "
              f"{'AGREES' if not divergences else divergences}")
    else:
        record["classes_note"] = (
            "no refusal case in this run; the A-F taxonomy describes "
            "refusals only")
        print("\nno refusal case in this run; the A-F taxonomy does not "
              "apply and none is recorded")

    if args.out:
        out = Path(args.out).resolve()
        if out.parent != HERE:
            raise SystemExit(
                f"refusing to write outside this phase's own directory: "
                f"{out}\n  Phase A's and Phase B's records are LOOSE FILES in "
                f"{HERE.parent}, not a subdirectory, so a blocklist of "
                "directories does not protect them -- and neither are the "
                "frozen instruments beside them. An allowlist of one "
                "directory protects every one of them.")
        out.write_text(json.dumps(record, indent=1))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
