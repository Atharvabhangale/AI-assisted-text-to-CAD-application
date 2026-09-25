"""Phase D's failure taxonomy for the refusal cases. Calls no model.

It reads a recorded run and puts every attempt in EXACTLY ONE of the six
classes the brief names. It computes nothing new about the model: every
input is a field the observer already recorded, so the same run file can be
re-classified without spending a call, and a classification can be checked
against the raw answer it came from.

    A  correct refusal, correct body names, and the request addressed
    B  correct refusal, names the bodies, but IGNORES the requested noun
    C  correct refusal, but the bodies are wrong or incomplete
    D  no clarification -- it did not decline, or it declined without asking
    E  operations emitted alongside the refusal
    F  anything else, including output the provider never returned

---

WHY THE ORDER IS WHAT IT IS, AND WHY THAT IS NOT COSMETIC

The classes overlap, so an order is a claim about which failure matters.

**E comes first, ahead of the invalid-output class.** A clarification
carrying operations is rejected by the parser, so `plan` is `None`, and
every downstream field the other classes read -- `questions`, `summary`,
`outcome_declared` -- is empty or `invalid_model_output`. Classify on those
first and every E row lands in D or F and the most informative failure in
the corpus disappears. This is the same shape as the defect Phase C found in
`emitted_no_operations`: the validator's verdict standing in for the
model's behaviour.

**D comes before C, and C before B.** A reply that never asked is not a
worse-worded question; a reply naming the wrong bodies is not a question
with a missing noun. Each class assumes the ones above it did not fire, so
each row's letter names the FIRST thing that went wrong, reading from the
outside in.

**A is not "strict success".** `evaluate75.grade` decides strict success and
this module never second-guesses it; `cross_check` asserts the two agree, so
a divergence is a bug in one of them rather than a matter of taste.

---

WHAT IS GRADED AND WHAT IS ONLY REPORTED

The noun test is `ground_truth75`'s own -- `refusal_question_must_mention`,
read through `evaluate75.grade`. This module does not restate it, does not
soften it and does not add a second one: the expectation predates Phase D
and is not edited after a score is seen.

Alongside the letter, `describe()` reports facts that no check grades --
whether the reply invented a body id, how many of the expected ids it named,
how many questions it asked. They exist because "named one" and "named none"
are different failures, and a single boolean cannot say which happened.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import evaluate75 as EV                                       # noqa: E402
import ground_truth75 as G                                    # noqa: E402

#: The six classes, in the order they are tested. The order is the taxonomy.
CLASSES: Tuple[Tuple[str, str], ...] = (
    ("E", "operations emitted alongside the refusal"),
    ("F", "no usable answer from the provider"),
    ("D", "no clarification -- did not decline, or declined without asking"),
    ("C", "declined, but the bodies named are wrong or incomplete"),
    ("B", "declined and named the bodies, but ignored the requested noun"),
    ("A", "declined, named the bodies, and addressed the request"),
)

CLASS_ORDER: Tuple[str, ...] = tuple(letter for letter, _ in CLASSES)
CLASS_TEXT: Mapping[str, str] = {letter: text for letter, text in CLASSES}

#: A backticked token in the model's own words. Used ONLY to report whether
#: a reply invented an id; nothing is graded on it.
_BACKTICKED = re.compile(r"`([^`]+)`")


def _model_words(observation: Mapping[str, Any]) -> str:
    """The model's own words, exactly as the grader reads them.

    Imported behaviour, not a copy: a second definition of "what the model
    said" is a second opinion, and this project has paid for one of those
    already (Phase C, `operations_permitted`).
    """
    return EV._model_words(observation)


def classify(attempt: Mapping[str, Any]) -> str:
    """The one class this attempt belongs to. See the module docstring."""
    observation = attempt["observation"]
    truth = G.expected(attempt["case"])
    wanted = tuple(truth["refusal_must_name"] or ())
    said = _model_words(observation)

    # E -- and it MUST be first. See the module docstring.
    if observation.get("model_operation_count", 0) > 0:
        return "E"

    declared = observation.get("outcome_declared")
    if declared is None or declared == "invalid_model_output":
        return "F"

    if declared not in ("needs_clarification", "unsupported"):
        return "D"
    if not observation.get("questions"):
        return "D"

    if not all(body.lower() in said for body in wanted):
        return "C"

    must_mention = tuple(truth["refusal_question_must_mention"] or ())
    if must_mention and not all(token.lower() in said for token in must_mention):
        return "B"
    return "A"


def describe(attempt: Mapping[str, Any]) -> Dict[str, Any]:
    """The letter, plus the facts no single boolean can carry."""
    observation = attempt["observation"]
    truth = G.expected(attempt["case"])
    wanted = tuple(truth["refusal_must_name"] or ())
    said = _model_words(observation)
    questions = observation.get("questions")
    invented = sorted({
        token.strip().lower()
        for text in ([observation.get("summary") or ""] + list(questions or ()))
        for token in _BACKTICKED.findall(text)
        if token.strip().lower() not in {b.lower() for b in wanted}
    })
    return {
        "case": attempt["case"],
        "attempt": attempt.get("attempt"),
        "klass": classify(attempt),
        "outcome": observation.get("outcome_declared"),
        "bodies_named": sum(1 for b in wanted if b.lower() in said),
        "bodies_expected": len(wanted),
        "invented_ids": invented,
        "questions_asked": len(questions or ()),
        "operations_written": observation.get("model_operation_count"),
        "built_bodies": observation.get("body_count"),
        "executed": observation.get("executed"),
        "noun_mentioned": all(
            token.lower() in said
            for token in (truth["refusal_question_must_mention"] or ())
        ) if truth["refusal_question_must_mention"] else None,
        "summary": observation.get("summary"),
        "questions": list(questions or ()) if questions is not None else None,
    }


def cross_check(attempt: Mapping[str, Any]) -> Optional[str]:
    """`None` when the letter agrees with the grader, else what differs.

    Class A is a claim that every graded check passed. `evaluate75.grade` is
    the authority on that, so the two must never disagree; if they do, this
    says so rather than letting the taxonomy quietly outvote the grader.
    """
    letter = classify(attempt)
    strict = bool(attempt.get("strict_success"))
    if letter == "A" and not strict:
        failed = sorted(k for k, v in (attempt.get("checks") or {}).items()
                        if v is False)
        return f"classified A but grade() failed: {failed}"
    if letter != "A" and strict:
        return f"classified {letter} but grade() called it a strict success"
    return None


def distribution(attempts: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    """Counts per class, every class present, in taxonomy order."""
    counts = {letter: 0 for letter in CLASS_ORDER}
    for attempt in attempts:
        counts[classify(attempt)] += 1
    return counts


def load(path: Path, case: Optional[str] = None) -> List[Mapping[str, Any]]:
    record = json.loads(path.read_text(encoding="utf-8"))
    rows = record["attempts"]
    if case:
        rows = [r for r in rows if r["case"] == case]
    return rows


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", nargs="+", help="recorded run JSON files")
    ap.add_argument("--case", default=None, help="one case, e.g. R2")
    ap.add_argument("--quotes", type=int, default=0,
                    help="print this many distinct model replies per class")
    args = ap.parse_args(argv)

    for name in args.runs:
        path = Path(name)
        rows = load(path, args.case)
        if not rows:
            print(f"{path.name}: no attempts{' for ' + args.case if args.case else ''}")
            continue
        counts = distribution(rows)
        total = len(rows)
        arm = json.loads(path.read_text(encoding="utf-8")).get("arm", "C0-baseline")
        print(f"\n=== {path.name}  arm={arm}  "
              f"{args.case or 'all cases'}  n={total} ===")
        for letter in CLASS_ORDER:
            n = counts[letter]
            bar = "#" * round(40 * n / total) if total else ""
            print(f"  {letter}  {n:3}/{total:<3} {n / total:6.1%}  "
                  f"{CLASS_TEXT[letter]}")
            if bar:
                print(f"       {bar}")
        divergences = [d for d in (cross_check(r) for r in rows) if d]
        print(f"  cross-check against grade(): "
              f"{'AGREES on all ' + str(total) if not divergences else divergences}")

        named = {}
        for row in rows:
            d = describe(row)
            named.setdefault(d["bodies_named"], 0)
            named[d["bodies_named"]] += 1
        print(f"  bodies named (of {describe(rows[0])['bodies_expected']}): "
              + ", ".join(f"{k}->{v}" for k, v in sorted(named.items())))
        invented = sorted({i for row in rows for i in describe(row)["invented_ids"]})
        print(f"  ids named that are not bodies: {invented or 'none'}")

        if args.quotes:
            for letter in CLASS_ORDER:
                seen: List[str] = []
                for row in rows:
                    if classify(row) != letter:
                        continue
                    d = describe(row)
                    text = json.dumps(d["questions"] or d["summary"])
                    if text not in seen:
                        seen.append(text)
                    if len(seen) >= args.quotes:
                        break
                for text in seen:
                    print(f"    [{letter}] {text[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
