"""Stage 78 arena: ED-02 and CR-06, one prompt arm at a time.

A thin wrapper over `arena77.run`, which is reused UNMODIFIED. Stage 78
changes exactly one thing about a run -- which prompt text the live route
sends -- and measures the same two cases with the same fixtures, the same
encoding, the same model and the same Stage 77 grader, plus the Stage 78
axis criterion layered on top.

What is held constant, and what is allowed to move
---------------------------------------------------
`arena77.check_identity` refuses any drift including the prompt, which is
correct for a baseline corpus and wrong for an experiment whose whole point
is to change the prompt. `check_identity_for_arm` keeps every other identity
locked -- model, provider, encoding name, size and fingerprint -- and lets
the prompt differ, recording exactly what it was. A run under a different
ENCODING or a different MODEL is refused, as it should be.

Same-session control
---------------------
`--arm` may be given more than once. The arms then run interleaved, attempt
by attempt, inside ONE process against ONE provider configuration, so a
drift in the provider between arms cannot be mistaken for an effect of the
prompt. This is the control Stage 70 added after a reordering alone moved a
rate, and it is the default here rather than an option.

    PYTHONPATH=. python3 arena78.py --check
    PYTHONPATH=. python3 arena78.py --live --arm S0-baseline --calls 32 \
        --out baseline78.json
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
from typing import Any, Dict, Iterator, List, Optional

HERE = pathlib.Path(__file__).resolve().parent
STAGE77 = HERE.parent / "stage77-multibody-corpus"
STAGE76 = HERE.parent / "stage76-observation"
for path in (HERE, STAGE77, STAGE76):
    sys.path.insert(0, str(path))

from cad_experimental import generation as gen                   # noqa: E402
from cad_experimental import prompt as prompt_module             # noqa: E402
from cad_experimental.config import (                            # noqa: E402
    DEFAULT_MODEL, PROVIDER_NAME, bridge_credential,
)
from cad_experimental.generation import (                        # noqa: E402
    PLAN_SCHEMA_FINGERPRINT, PLAN_SCHEMA_INLINED, PLAN_SCHEMA_NAME,
)

import arena77 as A77                                            # noqa: E402
import evaluate78 as E78                                         # noqa: E402
import ground_truth77 as G77                                     # noqa: E402
import ground_truth78 as G78                                     # noqa: E402
import variants78 as V                                           # noqa: E402

#: Every earlier baseline directory. Stage 78 writes into its own only.
PROTECTED = tuple(
    p.name for p in HERE.parent.iterdir()
    if p.is_dir() and p.name != HERE.name)


def check_identity_for_arm() -> List[str]:
    """Drift that invalidates an ARM. The prompt is allowed to differ."""
    want = {
        "model": (G78.MODEL, DEFAULT_MODEL),
        "provider": ("anthropic", PROVIDER_NAME),
        "schema_name": (G78.ENCODING_NAME, PLAN_SCHEMA_NAME),
        "schema_fingerprint": (G78.ENCODING_FINGERPRINT,
                               PLAN_SCHEMA_FINGERPRINT),
    }
    return [f"{key}: expected {expected!r}, live {actual!r}"
            for key, (expected, actual) in want.items() if expected != actual]


def baseline_prompt_is_unmoved() -> List[str]:
    """Has the COMMITTED prompt changed since Stage 77 recorded it?

    Separate from the arm check on purpose. An arm patches the prompt in
    memory; if the file on disk has also changed, every arm is measured
    against a different baseline than the one the historical numbers came
    from, and the comparison is void.
    """
    drift = []
    if prompt_module.PROMPT_VERSION != G78.STAGE77_PROMPT_VERSION:
        drift.append(f"prompt_version: Stage 77 "
                     f"{G78.STAGE77_PROMPT_VERSION!r}, now "
                     f"{prompt_module.PROMPT_VERSION!r}")
    live = prompt_module.prompt_fingerprint()
    if live != G78.STAGE77_PROMPT_FINGERPRINT:
        drift.append(f"prompt_fingerprint: Stage 77 "
                     f"{G78.STAGE77_PROMPT_FINGERPRINT[:16]}, now {live[:16]}")
    return drift


@contextlib.contextmanager
def patched(arm: str) -> Iterator[str]:
    """Send `arm`'s text for the life of the block, then put it back.

    Both module references are patched: `prompt_module.system_prompt` and
    the name `generation` bound at import. Patching only one leaves the live
    route sending the committed prompt while the record claims an arm ran --
    a silent, total invalidation, which is why the caller asserts the patch
    reached `gen.system_prompt()` before spending a call.
    """
    text = V.text_for(arm)
    original_prompt = prompt_module.system_prompt
    original_gen = gen.system_prompt
    # `arena77.run` re-checks identity itself, and its check refuses ANY
    # prompt drift -- correct for a baseline corpus, and exactly wrong for
    # an experiment whose one variable is the prompt. The arm-aware check
    # is SUBSTITUTED, not removed: model, provider, encoding name and
    # encoding fingerprint are all still refused if they move, and
    # `baseline_prompt_is_unmoved` has already asserted that the COMMITTED
    # prompt is the one Stage 77 measured. Only the arm's own edit is let
    # through, which is the thing being measured.
    original_check = A77.check_identity
    prompt_module.system_prompt = lambda: text
    gen.system_prompt = lambda: text
    A77.check_identity = check_identity_for_arm
    try:
        yield text
    finally:
        prompt_module.system_prompt = original_prompt
        gen.system_prompt = original_gen
        A77.check_identity = original_check


def _score_stage78(record: Dict[str, Any]) -> Dict[str, Any]:
    """Layer the axis criterion over the Stage 77 verdict already recorded."""
    rows: List[Dict[str, Any]] = []
    for attempt in record.get("attempts", []):
        case = attempt.get("case")
        if case not in G78.CASES:
            continue
        for turn in attempt["turns"]:
            raw = (turn.get("observation") or {}).get("raw_text")
            passed77 = not any(c in G77.FAILURE_CODES for c in turn["codes"])
            row = E78.score_turn(case, raw, passed77)
            row["attempt"] = attempt["attempt"]
            row["turn"] = turn["turn"]
            row["stage77_codes"] = list(turn["codes"])
            rows.append(row)
    return {"rows": rows, "summary": E78.summarise(rows)}


def run_arm(arm: str, cases: List[str], calls: int) -> Dict[str, Any]:
    with patched(arm) as text:
        if gen.system_prompt() != text:
            raise SystemExit("the patch did not reach the live route")
        if prompt_module.system_prompt() != text:
            raise SystemExit("the patch did not reach the prompt module")
        record = A77.run(cases, calls)

    base = V.text_for(V.BASELINE)
    record["stage"] = 78
    record["arm"] = arm
    record["arm_role"] = V.role_of(arm)
    record["arm_fingerprint"] = V.fingerprint(text)
    record["arm_characters"] = len(text)
    record["arm_delta_characters"] = len(text) - len(base)
    record["prompt_is_committed"] = text == base
    record["baseline_fingerprint"] = V.fingerprint(base)
    record["stage78"] = _score_stage78(record)
    return record


def _print_summary(record: Dict[str, Any]) -> None:
    summary = record["stage78"]["summary"]
    print(f"\narm {record['arm']} ({record['arm_role']}): "
          f"{record['arm_fingerprint'][:16]} {record['arm_characters']} chars "
          f"({record['arm_delta_characters']:+d} from the committed prompt)")
    for name in sorted(summary["per_case"]):
        entry = summary["per_case"][name]
        print(f"  {name}: stage78 {entry['stage78_strict']}/{entry['calls']} "
              f"({entry['rate']:.3f})   stage77 "
              f"{entry['stage77_strict']}/{entry['calls']}")
        for code, count in sorted(entry["codes"].items()):
            print(f"      {code:30s} {count}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", default=None,
                        help="repeatable; several arms run INTERLEAVED in one "
                             "session as a same-session control")
    parser.add_argument("--calls", type=int, default=32,
                        help="live calls per case per arm")
    parser.add_argument("--cases", default=",".join(G78.CASES))
    parser.add_argument("--batch", type=int, default=8,
                        help="calls per case per arm per round; arms are "
                             "interleaved round by round")
    parser.add_argument("--out", default=None)
    parser.add_argument("--check", action="store_true",
                        help="offline: print identity and the arms, call "
                             "nothing")
    parser.add_argument("--live", action="store_true",
                        help="required; a credential's presence never starts "
                             "a run")
    args = parser.parse_args(argv)

    arms = args.arm or [V.BASELINE]
    cases = [c.strip() for c in args.cases.split(",") if c.strip()]
    unknown = [c for c in cases if c not in G78.CASES]
    if unknown:
        print(f"Stage 78 measures only {', '.join(G78.CASES)}; "
              f"refusing: {', '.join(unknown)}")
        return 2

    if args.check:
        print("identity the ARM must preserve (prompt excluded by design):")
        drift = check_identity_for_arm()
        for line in drift or ["  (all four match)"]:
            print(f"  {line}")
        print("\nthe COMMITTED prompt, which every arm is measured against:")
        moved = baseline_prompt_is_unmoved()
        for line in moved or [f"  unmoved since Stage 77: "
                              f"{G78.STAGE77_PROMPT_VERSION} "
                              f"{G78.STAGE77_PROMPT_FINGERPRINT[:16]} "
                              f"{G78.STAGE77_PROMPT_CHARACTERS} chars"]:
            print(f"  {line}")
        print(f"\narms registered: {', '.join(sorted(V.VARIANTS))}")
        print(f"  candidates: {V.CANDIDATES or '(none yet)'}")
        print(f"  controls:   {V.CONTROLS or '(none yet)'}")
        base = V.text_for(V.BASELINE)
        for arm in sorted(V.VARIANTS):
            text = V.text_for(arm)
            print(f"    {arm:28s} {V.fingerprint(text)[:16]} {len(text):6d} "
                  f"({len(text) - len(base):+d})")
        print(f"\ncases: {', '.join(cases)}")
        print(f"Stage 77 historical: " + "; ".join(
            f"{c} {G78.stage77_baseline(c)[0]}/{G78.stage77_baseline(c)[1]}"
            for c in cases))
        print("\nnothing was called.")
        return 0 if not (drift or moved) else 1

    if not args.live:
        print("refusing to run without --live: a credential's presence never "
              "starts a run.")
        return 2
    if bridge_credential() is None:
        print("no credential in this process; nothing attempted.")
        return 2

    drift = check_identity_for_arm()
    if drift:
        raise SystemExit("the live route is not what Stage 78 measures "
                         "against: " + "; ".join(drift))
    moved = baseline_prompt_is_unmoved()
    if moved:
        raise SystemExit(
            "the COMMITTED prompt has changed since Stage 77 recorded it, so "
            "no arm is comparable to the historical baseline: "
            + "; ".join(moved))

    base = V.text_for(V.BASELINE)
    for arm in arms:
        if arm != V.BASELINE and V.text_for(arm) == base:
            raise SystemExit(
                f"{arm} renders the committed prompt byte for byte; running "
                "it would record the baseline's numbers under its name.")

    print(f"credential bridged. arms: {', '.join(arms)}")
    print(f"{len(cases)} case(s) x {args.calls} calls x {len(arms)} arm(s) = "
          f"{len(cases) * args.calls * len(arms)} live calls")

    # INTERLEAVED, in rounds, not one arm then the next. Every arm gets a
    # batch, then every arm gets the next batch, so a drift in the provider
    # part-way through the session lands on all arms alike instead of on
    # whichever happened to run last. The fresh baseline for this stage came
    # back 0.219 below its Stage 77 number on the SAME prompt, which is the
    # size of swing this ordering exists to keep out of an arm's column.
    batch = max(1, min(args.batch, args.calls))
    rounds = [batch] * (args.calls // batch)
    if args.calls % batch:
        rounds.append(args.calls % batch)

    records: Dict[str, Any] = {}
    for index, size in enumerate(rounds):
        for arm in arms:
            print(f"  round {index + 1}/{len(rounds)}  arm {arm}  "
                  f"{size} call(s) per case")
            part = run_arm(arm, cases, size)
            if arm not in records:
                records[arm] = part
            else:
                offset = len(records[arm]["attempts"])
                for attempt in part["attempts"]:
                    attempt["attempt"] += offset
                records[arm]["attempts"].extend(part["attempts"])
                records[arm]["stage78"] = _score_stage78(records[arm])
            records[arm]["rounds"] = index + 1
            records[arm]["interleaved"] = True
    for arm in arms:
        _print_summary(records[arm])

    out = pathlib.Path(args.out or (HERE / "arena78-run.json"))
    if any(part in PROTECTED for part in out.parts):
        raise SystemExit(f"refusing to write into an earlier baseline: {out}")
    out.write_text(json.dumps(
        {"stage": 78, "arms": arms, "cases": cases, "calls": args.calls,
         "records": records}, indent=1))
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
