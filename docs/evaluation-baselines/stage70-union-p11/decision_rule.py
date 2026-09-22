"""Stage 70's adoption rule, fixed BEFORE the confirming runs were made.

Stage 67 adopted an arm on one favourable reading and had to revert it; Stage
68 found its own criterion grading a part against its own answer. The pattern
both share is a criterion settled AFTER the numbers were in. This file exists
so that cannot happen here: it was written and committed before the B1
confirmation and the B4 arm were run, and it takes counts only.

THE RULE.

An arm is ADOPTED only if all of the following hold.

 1. Its pooled n on the EXPLICIT golden request is at least MIN_N.
 2. Its P11 rate is below the committed baseline's at Fisher exact,
    two-sided, p < ALPHA.
 3. No guard regresses: thickness, plate count, envelope, bore axes and
    strict success must each be at least the baseline RATE, and thickness
    and plate count must stay perfect.

Otherwise the measured residual is DOCUMENTED and the stage stops. A rate
that cannot be shown to differ from the baseline is not an improvement, and
prompt text added on the strength of one is how a prompt bloats.
"""
from __future__ import annotations

import math
from typing import Final

MIN_N: Final[int] = 64
ALPHA: Final[float] = 0.05

#: Guards that may never regress, and the two that must stay perfect.
GUARDS: Final[tuple[str, ...]] = (
    "thickness", "plates", "envelope", "bore_axes", "strict")
MUST_BE_PERFECT: Final[tuple[str, ...]] = ("thickness", "plates")


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


def decide(arm: dict, baseline: dict) -> dict:
    """`arm` and `baseline` are count dicts: n, P11, and each guard."""
    reasons: list[str] = []
    p = fisher_exact_two_sided(arm["P11"], arm["n"] - arm["P11"],
                               baseline["P11"], baseline["n"] - baseline["P11"])
    if arm["n"] < MIN_N:
        reasons.append(f"n = {arm['n']} is below the minimum {MIN_N}")
    if arm["P11"] / arm["n"] >= baseline["P11"] / baseline["n"]:
        reasons.append("P11 is not lower than the baseline's")
    if p >= ALPHA:
        reasons.append(f"the P11 difference is not significant (p = {p:.4f})")
    for guard in GUARDS:
        if guard not in arm or guard not in baseline:
            continue
        if arm[guard] / arm["n"] < baseline[guard] / baseline["n"] - 1e-12:
            reasons.append(f"{guard} regressed "
                           f"({arm[guard]}/{arm['n']} vs "
                           f"{baseline[guard]}/{baseline['n']})")
    for guard in MUST_BE_PERFECT:
        if guard in arm and arm[guard] != arm["n"]:
            reasons.append(f"{guard} is not perfect ({arm[guard]}/{arm['n']})")
    return {"adopt": not reasons, "p_value": p, "reasons": reasons}
