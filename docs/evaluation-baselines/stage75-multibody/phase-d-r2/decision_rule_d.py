"""Phase D's adoption rule, fixed BEFORE any arm text was drafted.

Stage 67 adopted an arm on one favourable reading and had to revert it, at
the cost of that stage's headline number. Stage 70 answered that by
committing its rule first, and the rule then rejected all four candidates --
including B4, whose first 32 calls came back 32/32. Phase C did the same and
its rule refused all three arms on their exploratory samples, adopting only
on a fresh confirmation. This file is that discipline for Phase D.

It takes **counts only**. There is no parameter an arm's wording, a
transcript, a plan or a model reply could enter by.

---

WHAT PHASE D IS MEASURING

The fresh R2 baseline is **0/32 strict**, and the classifier puts all 32 in
ONE class:

    B  declined, named both bodies, asked a real question, wrote no
       operations -- and never said `bracket`, the noun the user used.

So the remaining defect is not a refusal failure and not a naming failure.
Both of those are already at 32/32. It is a PRECISION failure: the model
answers the question the prompt showed it -- *"which of these did you
mean?"* -- when the user did not ask an ambiguous question at all. The user
named a body. It does not exist. Saying so is a different answer.

The primary quantity is therefore R2's own strict rate, which for R2 is
exactly the classifier's class A: R2 is the only case carrying
`refusal_question_must_mention`, and every other graded check on it is
already perfect.

---

WHAT MUST NOT MOVE, AND WHY EACH IS HERE

R2 sits inside a refusal section that Phase C only just made work, beside a
creation path that is the product. An arm that buys R2 by spending any of
them is not an improvement, and the brief says so in its own words. So the
rule carries five preservation clauses, each with a same-session control:

  * the OTHER refusal cases, R1 and R3. They share the section the arm
    edits. Phase C's CB/CA pair showed how strongly an envelope example
    steers the reply's SHAPE, so a second envelope is exactly the kind of
    edit that could pull R1's answer towards the wrong template.
  * the four refusal checks R2 already passes. A clarification that says
    `bracket` and stops naming the bodies has traded one failure for
    another.
  * multi-body CREATION, per case and as a group.
  * the body-identity codes C, D and E -- geometry landing in the wrong
    body is the failure this whole stage exists to keep at zero.
  * the plan-validation codes P11 and P12 -- a modifier targeting an
    operation's own id, or a consumed solid. Stage 70 measured P11 at 5.6 %
    on a one-body union and stopped there deliberately; an arm that raises
    it has moved a number four stages could not.

  * and SINGLE-BODY behaviour, which is measured by its own golden request
    rather than assumed from the multi-body corpus.

---

THE RULE

An arm is ADOPTED only if ALL of these hold.

 1. **Enough calls, and a fresh sample.** At least MIN_N calls on R2 in a
    confirmation run that is not the exploratory one.

 2. **R2 improves materially.** Its strict rate must exceed the baseline's
    at Fisher exact, two-sided, p < ALPHA, AND by at least MIN_GAIN in
    absolute rate. "Significant" and "material" are different claims and a
    rule that asks for only the first can adopt a two-point move on a large
    sample.

 3. **No trade inside R2.** Every check in NO_TRADE stays at the baseline
    rate or better, and every check in MUST_BE_PERFECT stays perfect.

 4. **The sibling refusal cases hold.** R1 and R3 each stay at or above
    REFUSAL_FLOOR of their same-session control rate.

 5. **Creation holds.** Every creation case stays at or above
    CREATION_FLOOR of its same-session control rate, and the group rate
    does not fall below it either.

 6. **Single-body holds.** The golden single-body request stays at or above
    SINGLE_BODY_FLOOR of its same-session control rate.

 7. **No new wrong-body or wrong-target errors.** No code in
    IDENTITY_CODES and no code in PLAN_CODES rises above its control count.

 8. **An exploratory pass is never an adoption.**

Otherwise the measured behaviour is DOCUMENTED and the prompt is left
unchanged. Five stages running have found that prose about a rule moves
nothing and that what the model imitates is what the prompt SHOWS; none of
that licenses adopting an arm the numbers do not carry.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Final, List, Mapping, Tuple

#: Calls on R2 in the confirmation run. Phase C used 16 per case and the
#: intervals were tight enough to separate 72/72 from 19/72 without
#: argument; the same floor applies here.
MIN_N: Final[int] = 16

ALPHA: Final[float] = 0.05

#: "Materially", as an absolute rise in R2's strict rate. Against a measured
#: baseline of 0/32 any significant arm clears this easily; it is here so
#: that a later, higher baseline cannot be beaten by a two-point move on a
#: large sample and called a fix.
MIN_GAIN: Final[float] = 0.25

#: A sibling refusal case, a creation case and the single-body golden may
#: each wobble by one call on a small sample without that being a
#: regression. Phase C's CREATION_FLOOR, for the same reason.
REFUSAL_FLOOR: Final[float] = 0.875
CREATION_FLOOR: Final[float] = 0.875
SINGLE_BODY_FLOOR: Final[float] = 0.875

#: The case Phase D is about. Everything else in this file exists to stop
#: it being bought with something.
PRIMARY_CASE: Final[str] = "R2"

#: The refusal checks R2 ALREADY passes 32/32 on the baseline. An arm that
#: teaches the model to say `bracket` and costs any of these has traded one
#: failure for another.
NO_TRADE: Final[Tuple[str, ...]] = (
    "refused", "named_the_bodies", "asked_a_question",
    "emitted_no_operations", "built_nothing",
)
MUST_BE_PERFECT: Final[Tuple[str, ...]] = ("built_nothing",)

#: Creation-side codes that mean geometry landed in the wrong body.
IDENTITY_CODES: Final[Tuple[str, ...]] = (
    "C:wrong_body_identity", "D:wrong_target", "E:cross_body_edit")

#: Plan-validation codes for a modifier pointed at something that is not a
#: live solid. P11 is Stage 70's measured residual; P12 is a consumed one.
PLAN_CODES: Final[Tuple[str, ...]] = ("P11", "P12")


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """P(table at least as extreme), 2x2, conditioning on both margins.

    The tolerance on "at least as extreme" is RELATIVE. An absolute epsilon
    sweeps in tables many orders of magnitude less likely than the observed
    one whenever the observed probability is itself tiny, which is exactly
    the regime an arm that works lands in.
    """
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
        if p <= observed * (1.0 + 1e-9):
            accumulated += p
    return accumulated


def _rate(hits: int, n: int) -> float:
    return hits / n if n else 0.0


def decide(arm: Mapping[str, Any], baseline: Mapping[str, Any],
           *, exploratory: bool) -> Dict[str, Any]:
    """ADOPT or REJECT, from counts alone.

    ``arm`` and ``baseline`` are count mappings:

        r2                {"strict": int, "n": int}
        r2_checks         {check_name: passes} over the same n
        siblings          {case: (strict, calls)} for R1 and R3
        creation          {case: (strict, calls)}
        single_body       {"strict": int, "n": int} or {} when not measured
        identity_codes    {code: count} over the creation group
        plan_codes        {code: count} over every attempt

    Nothing here takes a plan, a transcript, a prompt or a wording.

    IT FAILS CLOSED. Every preservation clause needs evidence, and evidence
    that is absent is not evidence that nothing regressed: a mapping with no
    `creation` key would otherwise skip clause 5 silently and still return
    ADOPT. `exploratory` has no default for the same reason -- one forgotten
    keyword would have adopted an exploratory reading, which is the failure
    Stage 67 is the record of.
    """
    reasons: List[str] = []
    verdict = True

    # 0. the evidence each clause needs must BE there
    for key in ("r2", "r2_checks", "siblings", "creation", "single_body",
                "identity_codes", "plan_codes"):
        if key not in arm or key not in baseline:
            verdict = False
            reasons.append(
                f"no {key} evidence on both sides; a clause cannot be "
                "satisfied by silence")
        elif key in ("siblings", "creation", "single_body") and not (
                arm.get(key) and baseline.get(key)):
            verdict = False
            reasons.append(f"{key} was not measured; clause skipped means "
                           "REJECTED, not passed")

    arm_r2, base_r2 = arm["r2"], baseline["r2"]

    # 1. enough calls
    if arm_r2["n"] < MIN_N:
        verdict = False
        reasons.append(f"R2 n={arm_r2['n']} is below MIN_N={MIN_N}")

    # 2. R2 improves, significantly AND materially
    p = fisher_exact_two_sided(
        arm_r2["strict"], arm_r2["n"] - arm_r2["strict"],
        base_r2["strict"], base_r2["n"] - base_r2["strict"])
    gain = _rate(arm_r2["strict"], arm_r2["n"]) - _rate(
        base_r2["strict"], base_r2["n"])
    reasons.append(
        f"R2 strict: {arm_r2['strict']}/{arm_r2['n']} vs "
        f"{base_r2['strict']}/{base_r2['n']}, p={p:.4f}, gain={gain:+.3f}")
    if p >= ALPHA:
        verdict = False
        reasons.append(f"R2 did not improve significantly (p={p:.4f})")
    if gain < MIN_GAIN:
        verdict = False
        reasons.append(f"R2 gain {gain:+.3f} is below MIN_GAIN={MIN_GAIN}")

    # 3. no trade inside R2
    for check in NO_TRADE:
        arm_checks = arm.get("r2_checks") or {}
        base_checks = baseline.get("r2_checks") or {}
        if check not in arm_checks or check not in base_checks:
            verdict = False
            reasons.append(f"R2 {check} was not counted on both sides")
            continue
        arm_hits, base_hits = arm_checks[check], base_checks[check]
        if _rate(arm_hits, arm_r2["n"]) < _rate(base_hits, base_r2["n"]):
            verdict = False
            reasons.append(
                f"R2 {check} regressed "
                f"{base_hits}/{base_r2['n']} -> {arm_hits}/{arm_r2['n']}")
        if check in MUST_BE_PERFECT and arm_hits != arm_r2["n"]:
            verdict = False
            reasons.append(
                f"R2 {check} must stay perfect; {arm_hits}/{arm_r2['n']}")

    # 4. the sibling refusal cases
    arm_siblings = arm.get("siblings") or {}
    base_siblings = baseline.get("siblings") or {}
    for case in sorted(set(arm_siblings) | set(base_siblings)):
        was, now = base_siblings.get(case), arm_siblings.get(case)
        if not was or not was[1]:
            verdict = False
            reasons.append(f"refusal {case} has no measured control")
            continue
        if not now or not now[1]:
            verdict = False
            reasons.append(
                f"refusal {case} is in the control and NOT in the arm; a "
                "case the arm did not run cannot be said to have held")
            continue
        floor = _rate(was[0], was[1]) * REFUSAL_FLOOR
        if _rate(now[0], now[1]) < floor:
            verdict = False
            reasons.append(
                f"refusal {case} fell {_rate(was[0], was[1]):.3f} -> "
                f"{_rate(now[0], now[1]):.3f}, below floor {floor:.3f}")

    # 5. creation, per case and as a group
    arm_creation = arm.get("creation") or {}
    base_creation = baseline.get("creation") or {}
    for case in sorted(set(arm_creation) | set(base_creation)):
        was, now = base_creation.get(case), arm_creation.get(case)
        if not was or not was[1]:
            verdict = False
            reasons.append(f"creation {case} has no measured control")
            continue
        if not now or not now[1]:
            verdict = False
            reasons.append(
                f"creation {case} is in the control and NOT in the arm; an "
                "arm that drops its worst case has not preserved it")
            continue
        floor = _rate(was[0], was[1]) * CREATION_FLOOR
        if _rate(now[0], now[1]) < floor:
            verdict = False
            reasons.append(
                f"creation {case} fell {_rate(was[0], was[1]):.3f} -> "
                f"{_rate(now[0], now[1]):.3f}, below floor {floor:.3f}")
    if arm_creation and base_creation:
        arm_group = (sum(s for s, _ in arm_creation.values()),
                     sum(c for _, c in arm_creation.values()))
        base_group = (sum(s for s, _ in base_creation.values()),
                      sum(c for _, c in base_creation.values()))
        floor = _rate(*base_group) * CREATION_FLOOR
        reasons.append(
            f"creation group: {arm_group[0]}/{arm_group[1]} vs "
            f"{base_group[0]}/{base_group[1]}")
        if _rate(*arm_group) < floor:
            verdict = False
            reasons.append(
                f"creation group fell below floor {floor:.3f}")

    # 6. the single-body golden
    arm_single = arm.get("single_body") or {}
    base_single = baseline.get("single_body") or {}
    if arm_single and base_single:
        floor = _rate(base_single["strict"], base_single["n"]) * SINGLE_BODY_FLOOR
        reasons.append(
            f"single-body golden: {arm_single['strict']}/{arm_single['n']} vs "
            f"{base_single['strict']}/{base_single['n']}")
        if _rate(arm_single["strict"], arm_single["n"]) < floor:
            verdict = False
            reasons.append(
                f"single-body golden fell below floor {floor:.3f}")

    # 7. wrong-body and wrong-target errors -- PER CALL, not per run.
    #
    # A raw count comparison is sound only when both sides made the same
    # number of calls. Halving the arm's creation sample halves its error
    # count, and a rule reading counts would call that an improvement.
    arm_calls = sum(c for _, c in arm_creation.values())
    base_calls = sum(c for _, c in base_creation.values())
    for group_key, codes in (("identity_codes", IDENTITY_CODES),
                             ("plan_codes", PLAN_CODES)):
        for code in codes:
            arm_count = (arm.get(group_key) or {}).get(code, 0)
            base_count = (baseline.get(group_key) or {}).get(code, 0)
            if not arm_calls or not base_calls:
                if arm_count > base_count:
                    verdict = False
                    reasons.append(f"{code} rose {base_count} -> {arm_count}")
                continue
            if _rate(arm_count, arm_calls) > _rate(base_count, base_calls):
                verdict = False
                reasons.append(
                    f"{code} rose {base_count}/{base_calls} -> "
                    f"{arm_count}/{arm_calls}")

    # 8. an exploratory pass is never an adoption
    if verdict and exploratory:
        verdict = False
        reasons.append(
            "passes on an EXPLORATORY sample; a fresh confirmation of at "
            f"least {MIN_N} calls on {PRIMARY_CASE} is required before "
            "adoption")

    return {"adopt": verdict, "p": p, "gain": gain, "reasons": reasons}


__all__ = [
    "ALPHA",
    "CREATION_FLOOR",
    "IDENTITY_CODES",
    "MIN_GAIN",
    "MIN_N",
    "MUST_BE_PERFECT",
    "NO_TRADE",
    "PLAN_CODES",
    "PRIMARY_CASE",
    "REFUSAL_FLOOR",
    "SINGLE_BODY_FLOOR",
    "decide",
    "fisher_exact_two_sided",
]
