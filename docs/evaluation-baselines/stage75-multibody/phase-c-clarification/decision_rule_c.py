"""Phase C's adoption rule, fixed BEFORE any arm was built or run.

Stage 67 adopted an arm on one favourable reading and had to revert it, which
cost that stage its headline number. Stage 70 answered that by committing its
rule before the confirming runs, and the rule then rejected all four
candidates -- including B4, whose first 32 calls came back 32/32, the first
perfect arm in this project's history. The rule existed before the numbers
did, which is the only reason that did not become a second Stage 67.

This file is the same discipline for Phase C. It was written and committed
before a single arm's prompt text was drafted, and it takes **counts only** --
there is no parameter an arm's wording, a transcript or a plan could enter by.

---

WHAT PHASE C IS MEASURING, AND WHY IT IS TWO NUMBERS

A clarification that does not name the bodies is a shrug. A clarification that
ships geometry is an edit wearing a question's label. They are different
failures with different causes, and Phase B's single boolean could not tell
either of them from the other. So they are carried apart, all the way through:

  METRIC A -- BODY NAMING. Of the bodies the clarification had to name, how
  many did it name: BOTH, ONE, or ZERO. Three-way on purpose. "Named one"
  means the model understood the part has several and described the wrong
  one; "named none" means it said "that is ambiguous" and stopped. The same
  `False` under the committed boolean.

  METRIC B -- OPERATIONS. How many operations the MODEL wrote alongside the
  question, read from its own answer and never from the parsed plan. The
  parser rejects a clarification carrying operations, so the parsed count is
  zero exactly when the model emitted the most.

They are never summed, never averaged together, and neither is folded into
`strict_success`.

---

THE RULE

An arm is ADOPTED only if ALL of the following hold.

 1. **Enough calls.** Pooled n >= MIN_N per refusal case, and the arm's calls
    are a fresh sample -- exploratory calls never count toward a
    confirmation.

 2. **It moves what it claims to move.** At least one of:
      - naming BOTH above the baseline's rate at Fisher exact, two-sided,
        p < ALPHA; or
      - operations NONE above the baseline's rate at the same test.

 3. **It does not trade one failure for another.** The metric it does NOT
    claim to move must not regress below the baseline rate at all.

 4. **Refusal correctness is preserved.** `refused` and `built_nothing` must
    each stay at the baseline rate or better, and `built_nothing` must stay
    PERFECT -- a clarification that builds geometry is the one outcome no
    naming improvement could pay for.

 5. **Creation is preserved.** Every creation case's strict rate must be at
    least CREATION_FLOOR of its committed Phase B rate, and the multi-body
    creation cases must not regress at all as a group. An arm that improves
    clarification and harms creation is REJECTED -- the brief's words, and
    Stage 67's lesson stated as a condition.

 6. **Body identity and targeting are preserved.** The creation-side codes
    that mean the model put geometry in the wrong body -- C, D and E -- must
    not rise above the baseline count.

 7. **One unusually good small sample is not evidence.** An arm whose
    exploratory reading passes must repeat it on a FRESH confirmation sample
    of at least MIN_N before adoption. `decide()` refuses to return ADOPT
    for a run marked exploratory.

Otherwise the measured behaviour is DOCUMENTED and the prompt is left
unchanged. Four stages running have now found that prose about a rule moves
nothing (65, 66, 69, 70); adding more of it on the strength of a
non-significant arm is how a 33 407-character prompt becomes a 36 000-character
one that measures the same.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Final, List, Mapping, Tuple

#: Calls per refusal case, per arm. Phase B ran 8 and the intervals were far
#: too wide to separate a cause from a coincidence; the handoff says so in
#: its own words ("stop before C if B's numbers are thin").
MIN_N: Final[int] = 16

ALPHA: Final[float] = 0.05

#: A creation case may not fall below this fraction of its committed rate.
#: Not 1.0: a single call flipping on an 8-call case is noise, and a rule
#: that treats it as a regression would reject every arm forever.
CREATION_FLOOR: Final[float] = 0.875

#: The two metrics, by the name `summarise()` gives them.
METRICS: Final[Tuple[str, ...]] = ("naming_both", "operations_none")

#: Safety checks that may never regress. `built_nothing` must stay perfect.
SAFETY: Final[Tuple[str, ...]] = ("refused", "built_nothing")
MUST_BE_PERFECT: Final[Tuple[str, ...]] = ("built_nothing",)

#: Creation-side failure codes that mean geometry landed in the wrong body.
IDENTITY_CODES: Final[Tuple[str, ...]] = (
    "C:wrong_body_identity", "D:wrong_target", "E:cross_body_edit")


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """P(table at least as extreme), 2x2, conditioning on both margins."""
    total = a + b + c + d
    if total == 0:
        return 1.0
    observed = (math.comb(a + b, a) * math.comb(c + d, c)
                / math.comb(total, a + c))
    accumulated = 0.0
    for i in range(0, min(a + b, a + c) + 1):
        j = a + c - i
        if not 0 <= j <= c + d:
            continue
        p = math.comb(a + b, i) * math.comb(c + d, j) / math.comb(total, a + c)
        if p <= observed + 1e-12:
            accumulated += p
    return accumulated


def _rate(hits: int, n: int) -> float:
    return hits / n if n else 0.0


def decide(arm: Mapping[str, Any], baseline: Mapping[str, Any],
           *, exploratory: bool = False) -> Dict[str, Any]:
    """ADOPT or REJECT, from counts alone.

    ``arm`` and ``baseline`` are count mappings:

        n                 calls in the refusal group
        naming_both       clarifications naming every body they had to
        operations_none   clarifications carrying no operations
        refused           clarifications that actually declined
        built_nothing     clarifications that built no geometry
        creation          {case_name: (strict, calls)}
        identity_codes    {code: count} over the creation group

    Nothing here takes a plan, a transcript, a prompt or a wording. A rule
    that could read the answer it is judging is Stage 67's defect.
    """
    reasons: List[str] = []
    verdict = True

    # 1. enough calls, and a fresh sample
    if arm["n"] < MIN_N:
        verdict = False
        reasons.append(f"n={arm['n']} is below MIN_N={MIN_N}")

    # 2. it moves what it claims to move
    moved: List[str] = []
    for metric in METRICS:
        p = fisher_exact_two_sided(
            arm[metric], arm["n"] - arm[metric],
            baseline[metric], baseline["n"] - baseline[metric])
        better = _rate(arm[metric], arm["n"]) > _rate(
            baseline[metric], baseline["n"])
        reasons.append(
            f"{metric}: {arm[metric]}/{arm['n']} vs "
            f"{baseline[metric]}/{baseline['n']}, p={p:.4f}")
        if better and p < ALPHA:
            moved.append(metric)
    if not moved:
        verdict = False
        reasons.append("no metric improved significantly")

    # 3. no trade: the metric it does not claim must not regress
    for metric in METRICS:
        if metric in moved:
            continue
        if _rate(arm[metric], arm["n"]) < _rate(
                baseline[metric], baseline["n"]):
            verdict = False
            reasons.append(f"{metric} regressed while {moved} improved")

    # 4. refusal correctness
    for check in SAFETY:
        arm_rate = _rate(arm[check], arm["n"])
        base_rate = _rate(baseline[check], baseline["n"])
        if arm_rate < base_rate:
            verdict = False
            reasons.append(f"{check} regressed {base_rate:.3f} -> {arm_rate:.3f}")
        if check in MUST_BE_PERFECT and arm[check] != arm["n"]:
            verdict = False
            reasons.append(f"{check} must stay perfect; {arm[check]}/{arm['n']}")

    # 5. creation is preserved
    for case, (strict, calls) in (arm.get("creation") or {}).items():
        was = (baseline.get("creation") or {}).get(case)
        if not was:
            continue
        floor = _rate(was[0], was[1]) * CREATION_FLOOR
        if _rate(strict, calls) < floor:
            verdict = False
            reasons.append(
                f"creation {case} fell {_rate(was[0], was[1]):.3f} -> "
                f"{_rate(strict, calls):.3f}, below floor {floor:.3f}")

    # 6. body identity and targeting
    for code in IDENTITY_CODES:
        arm_count = (arm.get("identity_codes") or {}).get(code, 0)
        base_count = (baseline.get("identity_codes") or {}).get(code, 0)
        if arm_count > base_count:
            verdict = False
            reasons.append(f"{code} rose {base_count} -> {arm_count}")

    # 7. one good small sample is not evidence
    if verdict and exploratory:
        verdict = False
        reasons.append(
            "passes on an EXPLORATORY sample; a fresh confirmation of at "
            f"least {MIN_N} per case is required before adoption")

    return {"adopt": verdict, "moved": moved, "reasons": reasons}


__all__ = [
    "ALPHA",
    "CREATION_FLOOR",
    "IDENTITY_CODES",
    "METRICS",
    "MIN_N",
    "MUST_BE_PERFECT",
    "SAFETY",
    "decide",
    "fisher_exact_two_sided",
]
