"""Phase E's characterisation of the R2 residual. Calls no model.

It reads a recorded run and answers, per attempt, the eight questions the
brief asks -- refused, questions field present, at least one question, both
bodies named, the user's invalid noun addressed, operations emitted, built
anything, and the outcome label -- and then groups the FAILING replies by
their exact text, because a residual is a mechanism only if the failures
look alike.

Everything it prints is derived from `evaluate75` and `ground_truth75`. It
defines no criterion of its own: a second opinion about what "addressed the
request" means is exactly what this phase exists to avoid.

    cd apps/api
    export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
    python3 ../../docs/evaluation-baselines/stage75-multibody/\\
        phase-e-r2-tail/characterise_e.py residual-r2.json
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "phase-d-r2"))

import classify_d as CD                                   # noqa: E402
import evaluate75 as EV                                   # noqa: E402
import ground_truth75 as G                                # noqa: E402

#: The brief's six candidate mechanisms, in its own words.
MECHANISMS = (
    ("A", "stochastic wording variation"),
    ("B", "failure to mention the requested noun"),
    ("C", "failure to distinguish 'requested noun does not exist'"),
    ("D", "body-name resolution issue"),
    ("E", "question-content issue"),
    ("F", "something else"),
)


def wilson(hits: int, n: int, z: float = 1.96):
    if not n:
        return (0.0, 0.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def per_attempt(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The eight facts the brief asks to be recorded separately."""
    observation = row["observation"]
    truth = G.expected(row["case"])
    wanted = tuple(truth["refusal_must_name"] or ())
    said = EV._model_words(observation)
    questions = observation.get("questions")
    return {
        "attempt": row.get("attempt"),
        "refused": row["checks"].get("refused"),
        "questions_field_present": questions is not None,
        "asked_at_least_one": bool(questions),
        "both_bodies_named": row["checks"].get("named_the_bodies"),
        "noun_addressed": row["checks"].get("question_addressed_the_request"),
        "operations_written": observation.get("model_operation_count"),
        "built_bodies": observation.get("body_count"),
        "outcome": observation.get("outcome_declared"),
        "label": row.get("label"),
        "summary": observation.get("summary"),
        "reason": observation.get("plan_reason"),
        "questions": list(questions or ()) if questions is not None else None,
        "class": CD.classify(row),
        "strict": bool(row["strict_success"]),
        "said_contains_noun": all(
            t.lower() in said
            for t in (truth["refusal_question_must_mention"] or ())),
    }


def _shape(entry: Mapping[str, Any]) -> tuple:
    return (entry["summary"], tuple(entry["questions"] or ()))


def report(path: Path, case: str = "R2", quotes: int = 0) -> Dict[str, Any]:
    record = json.loads(path.read_text(encoding="utf-8"))
    rows = [r for r in record["attempts"] if r["case"] == case]
    entries = [per_attempt(r) for r in rows]
    n = len(entries)

    print(f"=== {path.name}  case {case}  n={n} ===")
    print(f"  prompt {record.get('prompt_version')} "
          f"{str(record.get('prompt_fingerprint'))[:16]} "
          f"{record.get('prompt_characters')} chars")
    print(f"  schema {record.get('schema_name')} "
          f"{record.get('schema_inlined')}")
    print(f"  live   {record.get('is_live_model_result')}  "
          f"credential from {record.get('credential_from')} (name only)")

    print("\n  per-attempt facts, counted:")
    for key in ("refused", "questions_field_present", "asked_at_least_one",
                "both_bodies_named", "noun_addressed", "strict"):
        hits = sum(1 for e in entries if e[key])
        low, high = wilson(hits, n)
        print(f"    {key:24} {hits:3}/{n:<3} {hits / n:6.1%}  "
              f"[{low:.2f},{high:.2f}]")
    wrote = sum(1 for e in entries if (e["operations_written"] or 0) > 0)
    built = sum(1 for e in entries if (e["built_bodies"] or 0) > 0)
    print(f"    {'emitted operations':24} {wrote:3}/{n}")
    print(f"    {'built anything':24} {built:3}/{n}")
    print(f"    labels                   "
          f"{dict(collections.Counter(e['label'] for e in entries))}")

    print("\n  taxonomy (`classify_d`, every attempt in exactly one):")
    for letter in CD.CLASS_ORDER:
        count = sum(1 for e in entries if e["class"] == letter)
        if count:
            print(f"    {letter}  {count:3}/{n}  {CD.CLASS_TEXT[letter]}")
    divergences = [d for d in (CD.cross_check(r) for r in rows) if d]
    print(f"    cross-check vs grade(): "
          f"{'AGREES on all ' + str(n) if not divergences else divergences}")

    failures = [e for e in entries if not e["strict"]]
    successes = [e for e in entries if e["strict"]]
    print(f"\n  the {len(failures)} FAILING replies, grouped by exact text:")
    for shape, count in collections.Counter(
            _shape(e) for e in failures).most_common():
        summary, questions = shape
        print(f"    x{count}")
        print(f"      summary: {summary}")
        for q in questions:
            print(f"      Q      : {q}")

    if quotes:
        print(f"\n  {min(quotes, len(successes))} PASSING replies, for contrast:")
        for shape, count in collections.Counter(
                _shape(e) for e in successes).most_common(quotes):
            summary, questions = shape
            print(f"    x{count}")
            print(f"      summary: {summary}")
            for q in questions:
                print(f"      Q      : {q}")

    # --- the discriminator, counted rather than asserted -------------------
    #
    # The two answers differ in their DIAGNOSIS of the request, which the
    # summary states in one sentence. Counting how many failures share one
    # diagnosis and how many successes share the other is what separates a
    # mechanism from wording noise.
    print("\n  summary sentences, failures vs successes:")
    for label, group in (("FAIL", failures), ("PASS", successes)):
        counts = collections.Counter(e["summary"] for e in group)
        for text, count in counts.most_common(4):
            print(f"    {label} x{count:<3} {text}")

    return {
        "n": n,
        "entries": entries,
        "strict": len(successes),
        "distinct_failure_texts": len({_shape(e) for e in failures}),
        "failures_mentioning_the_noun": sum(
            1 for e in failures if e["said_contains_noun"]),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--case", default="R2")
    ap.add_argument("--quotes", type=int, default=4)
    args = ap.parse_args(argv)
    for name in args.runs:
        path = Path(name)
        if not path.is_absolute():
            path = HERE / name if (HERE / name).is_file() else path
        report(path, args.case, args.quotes)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
