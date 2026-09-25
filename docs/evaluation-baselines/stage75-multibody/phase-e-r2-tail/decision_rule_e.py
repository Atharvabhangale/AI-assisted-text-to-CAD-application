"""Phase E's adoption rule, fixed BEFORE any arm text was drafted.

It does not restate Phase D's rule; it IMPORTS it. Phase D's eight clauses
are exactly what this brief asks for -- R2 improves materially, body naming
holds, zero-operation clarification holds, R1 and R3 hold, multi-body
creation holds, the single-body golden holds, no new target or body-identity
failures, and an exploratory pass is never an adoption -- and they were
committed in `209f6fc` before Phase D's arms existed. Re-typing them here
would be a second copy of an authority, which is the defect Phase C found in
`operations_permitted`: the corpus declared a rule and the grader kept its
own copy, they happened to agree, and nothing noticed when one moved.

ONE THING CHANGES, AND IT ONLY EVER TIGHTENS.

`MIN_N` rises from 16 to 32, because Phase D's confirmation read 13/16 and
the fresh 48-call sample read 34/48 -- the same quantity, but the smaller
sample's interval was wide enough to make the second look like a regression
when it is not. The brief asks for at least 32 confirmation calls and this
makes that a property of the rule rather than a thing to remember.

Every other threshold is Phase D's, unchanged and asserted to be so: ALPHA,
MIN_GAIN, REFUSAL_FLOOR, CREATION_FLOOR, SINGLE_BODY_FLOOR, NO_TRADE,
MUST_BE_PERFECT, IDENTITY_CODES, PLAN_CODES and PRIMARY_CASE. Raising a
sample-size floor cannot make an arm adoptable that would otherwise be
refused; lowering any other threshold could, and none is lowered.

WHAT THIS PHASE IS MEASURING, so the rule is read against the right thing.

The residual is not a rate of wording noise. Over 48 fresh calls on the
committed prompt every one of the 14 failures states the same DIAGNOSIS --
"the request does not say which body" -- and every one of the 34 successes
states the other -- "the request names a body this part does not have".
Naming is 48/48, operations 0/48, built 0/48, refused 48/48. So the only
quantity an arm may move is which of two rules the model applies, and the
only quantities it must not move are the four that are already perfect.

That is why clause 3 matters more here than it did in Phase D: an arm that
buys the diagnosis by costing the naming, the question, the empty operation
list or the refusal itself has traded a measured success for the thing it
was trying to fix.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Final, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phase-d-r2"))

import decision_rule_d as D                               # noqa: E402

#: Phase D's, unchanged. Imported rather than copied.
ALPHA: Final[float] = D.ALPHA
MIN_GAIN: Final[float] = D.MIN_GAIN
REFUSAL_FLOOR: Final[float] = D.REFUSAL_FLOOR
CREATION_FLOOR: Final[float] = D.CREATION_FLOOR
SINGLE_BODY_FLOOR: Final[float] = D.SINGLE_BODY_FLOOR
NO_TRADE = D.NO_TRADE
MUST_BE_PERFECT = D.MUST_BE_PERFECT
IDENTITY_CODES = D.IDENTITY_CODES
PLAN_CODES = D.PLAN_CODES
PRIMARY_CASE: Final[str] = D.PRIMARY_CASE
fisher_exact_two_sided = D.fisher_exact_two_sided

#: The one change, and it only tightens. Phase D's 16 would let a 13/16
#: reading stand as a confirmation; this phase measured the same quantity at
#: 34/48 and the brief asks for at least 32.
MIN_N: Final[int] = 32
assert MIN_N > D.MIN_N, "this rule may only raise the floor, never lower it"


def decide(arm: Mapping[str, Any], baseline: Mapping[str, Any],
           *, exploratory: bool) -> Dict[str, Any]:
    """Phase D's `decide`, with Phase E's larger sample floor.

    The floor is applied here rather than by editing Phase D's module,
    because Phase D's recorded verdicts must stay reproducible by the rule
    that produced them. `exploratory` keeps no default, for the reason
    Phase D gives: one forgotten keyword adopts an exploratory reading,
    which is the failure Stage 67 is the record of.
    """
    verdict = D.decide(arm, baseline, exploratory=exploratory)
    n = arm.get("r2", {}).get("n", 0)
    if n < MIN_N:
        verdict["adopt"] = False
        verdict["reasons"].append(
            f"R2 n={n} is below Phase E's MIN_N={MIN_N} "
            f"(Phase D's floor was {D.MIN_N}; this phase only raises it)")
    return verdict


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
