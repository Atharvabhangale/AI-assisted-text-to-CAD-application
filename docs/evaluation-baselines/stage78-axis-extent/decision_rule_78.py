"""Stage 78's adoption rule, fixed BEFORE any arm text exists.

Committed in its own commit, on a tree where `variants78.CANDIDATES` is the
empty tuple. That ordering is checkable after the fact -- `git show` the
commit that introduces this file and `variants78` has no candidate in it --
and it is the only thing that makes the rule mean anything. A rule written
after seeing an arm's wording is a description of that wording; a rule
written after seeing its score is not a rule at all.

Stage 70 committed its rule before its confirming runs and then REJECTED all
four of its candidates. Stage 75 Phase E committed its rule and then found
its control was the finding. Neither outcome was available to a stage that
decided what counted as success afterwards. The rule below is written
expecting to reject.

WHAT IS FIXED HERE
------------------
* the primary metric, and that it is the only one that can adopt;
* minimum n, for exploration and for confirmation;
* the minimum absolute improvement;
* the significance criterion, and the test that computes it;
* the regression gate, dimension by dimension;
* what counts as rejection.

WHAT IS DELIBERATELY NOT FIXED HERE
-----------------------------------
The wording of any arm. This file must be readable as a rule for an
experiment whose candidates have not been invented yet, and it is.
"""

from __future__ import annotations

import math
from typing import Dict, Final, Mapping, Optional, Sequence, Tuple

# ------------------------------------------------------------ the metric

#: THE primary metric, and the only one that may adopt anything.
#:
#: `stage78_strict` is `stage77_strict AND axis_ok`, so it is strictly
#: stronger than the metric Stage 77 reported and an arm cannot win by
#: trading a Stage 77 check away for an axis check.
PRIMARY_METRIC: Final[str] = "stage78_strict"

#: The two cases, scored SEPARATELY. A pooled rate is refused everywhere in
#: this stage: ED-02 is an edit and CR-06 a creation, their denominators
#: differ and their mechanisms differ in shape even though both are
#: axis-versus-extent. An arm that fixed one and broke the other would show
#: a flat pooled number.
CASES: Final[Tuple[str, ...]] = ("ED-02", "CR-06")

#: Secondary, reported always, NEVER sufficient to adopt on their own. A
#: candidate is not better because a volume came closer or a shape looked
#: more plausible.
SECONDARY_METRICS: Final[Tuple[str, ...]] = (
    "axis_at_max_extent", "axis_at_min_extent", "axis_elsewhere",
    "wrong_target", "wrong_direction", "body_moved", "no_feature_written",
)

# -------------------------------------------------------- sample sizes

#: Exploration: a cheap screen, not a test. 32 calls per arm per case.
MIN_EXPLORATORY_CALLS_PER_CASE: Final[int] = 32

#: Confirmation must be a FRESH sample. Exploratory calls never count
#: toward it and are never pooled with it: the arm that looked best out of
#: several was selected ON those calls, so reusing them would report a
#: selection effect as an effect of the prompt.
#:
#: 128, and the number is DERIVED, not chosen for comfort. Against a
#: baseline of 21/32 = 0.656, the smallest improvement a two-sided Fisher
#: exact test can resolve at alpha = 0.05 is:
#:
#:     n= 32  ->  +0.250      n= 96  ->  +0.146
#:     n= 48  ->  +0.208      n=128  ->  +0.125
#:     n= 64  ->  +0.172
#:
#: So at n = 32 the two clauses of this rule CONTRADICT each other: nothing
#: can satisfy "+0.125 or better" and "p < 0.05" at once, and a rule like
#: that rejects every real result for want of power while looking rigorous.
#: 128 is the smallest n at which the improvement this stage calls worth
#: adopting is also an improvement this stage can detect.
#:
#: Computed and recorded BEFORE any arm text existed; the table above is
#: reproducible from `fisher_exact_two_sided` in this file.
MIN_CONFIRMATION_CALLS_PER_CASE: Final[int] = 128

#: At the exploratory n the significance test is NOT a gate, because it
#: cannot be one -- see above. Exploration screens on the raw improvement
#: and on the mechanism falling; significance is required at confirmation,
#: where the sample supports it. An exploratory result is never reported as
#: significant and never adopts anything.
SIGNIFICANCE_REQUIRED_AT_EXPLORATION: Final[bool] = False

#: A same-session control is REQUIRED, not optional. Baseline and candidate
#: run interleaved in one process against one provider configuration, so a
#: drift in the provider between arms cannot be read as an effect of the
#: prompt. Stage 70 measured a pure reordering moving a rate and this is the
#: guard that came out of it.
SAME_SESSION_CONTROL_REQUIRED: Final[bool] = True

# ------------------------------------------------------- the thresholds

#: The improvement must be on the CASE THAT CARRIES THE DEFECT, and it must
#: be large enough to matter. ED-02 sits at 21/32 = 0.656 historically; a
#: candidate that cannot add four successes in 32 is not worth a prompt
#: change that every other request also has to carry.
MIN_ABSOLUTE_IMPROVEMENT: Final[float] = 0.125        # +4 in 32

#: Two-sided, because a prompt change can plausibly make things worse and a
#: rule that could only detect improvement would be measuring its own hope.
SIGNIFICANCE_ALPHA: Final[float] = 0.05
SIGNIFICANCE_TEST: Final[str] = "Fisher exact, two sided"

#: The axis mechanism must actually fall. An arm that raised the strict rate
#: while leaving `X1:axis_at_max_extent` where it was would have improved
#: something else, and this stage would not know what.
MIN_MECHANISM_REDUCTION: Final[float] = 0.50          # at least halved

# -------------------------------------------------------- the regression gate

#: Every dimension a candidate must PRESERVE. A fix for one thing that costs
#: another is not a fix. These are checked on a focused sample first; the
#: full corpus is only re-run if a candidate would otherwise qualify.
#:
#: The bound is on the LOWER end of each: a candidate may not drop a
#: preserved dimension by more than this, and may not drop a dimension that
#: was perfect to less than perfect at all.
MUST_PRESERVE: Final[Tuple[str, ...]] = (
    "multi_body_creation", "body_identity", "body_targeting",
    "edit_isolation", "thickness", "placement", "refusal", "measurement",
    "aggregate_measurement", "export", "single_body_golden",
)

#: Dimensions Stage 77 measured at 100 %. A candidate that breaks any of
#: them is rejected outright, whatever it does to the primary metric --
#: there is no trade here worth making.
PERFECT_AND_MUST_STAY_PERFECT: Final[Tuple[str, ...]] = (
    "refusal", "measurement", "aggregate_measurement", "export",
)

#: For the rest, the largest drop tolerated before a candidate is rejected.
MAX_TOLERATED_REGRESSION: Final[float] = 0.0625       # -2 in 32

# ------------------------------------------------------------- the verdict

ADOPT: Final[str] = "ADOPT"
REJECT: Final[str] = "REJECT"
INCONCLUSIVE: Final[str] = "INCONCLUSIVE"


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """p for the 2x2 table [[a, b], [c, d]]. No SciPy in this project.

    Rows are arms, columns are outcome: a = candidate successes, b =
    candidate failures, c = baseline successes, d = baseline failures.
    Two sided by the total-probability convention: sum the probability of
    every table no more likely than the observed one.
    """
    n = a + b + c + d
    if n == 0:
        return 1.0
    row1, row2 = a + b, c + d
    col1, col2 = a + c, b + d

    # The hypergeometric pmf for the count in cell (0,0), over every
    # feasible value of that cell.
    def pmf(x: int) -> float:
        if x < 0 or x > col1 or row1 - x < 0 or row1 - x > col2:
            return 0.0
        return math.exp(
            math.lgamma(row1 + 1) - math.lgamma(x + 1)
            - math.lgamma(row1 - x + 1)
            + math.lgamma(row2 + 1) - math.lgamma(col1 - x + 1)
            - math.lgamma(row2 - (col1 - x) + 1)
            - (math.lgamma(n + 1) - math.lgamma(col1 + 1)
               - math.lgamma(col2 + 1))
        )

    observed = pmf(a)
    lo = max(0, col1 - row2)
    hi = min(row1, col1)
    total = sum(pmf(x) for x in range(lo, hi + 1)
                if pmf(x) <= observed * (1 + 1e-9))
    return min(1.0, total)


def assess_case(
    case: str,
    candidate_pass: int, candidate_n: int,
    baseline_pass: int, baseline_n: int,
    candidate_mechanism: int, baseline_mechanism: int,
    *, required_n: Optional[int] = None,
) -> Dict[str, object]:
    """Apply the rule to ONE case. Reports, it does not decide alone."""
    cand_rate = candidate_pass / candidate_n if candidate_n else 0.0
    base_rate = baseline_pass / baseline_n if baseline_n else 0.0
    improvement = cand_rate - base_rate
    p = fisher_exact_two_sided(
        candidate_pass, candidate_n - candidate_pass,
        baseline_pass, baseline_n - baseline_pass)

    if baseline_mechanism:
        mechanism_drop = (baseline_mechanism - candidate_mechanism) / baseline_mechanism
    else:
        mechanism_drop = 0.0

    return {
        "case": case,
        "candidate": f"{candidate_pass}/{candidate_n}",
        "baseline": f"{baseline_pass}/{baseline_n}",
        "candidate_rate": cand_rate,
        "baseline_rate": base_rate,
        "improvement": improvement,
        "p": p,
        "mechanism_candidate": candidate_mechanism,
        "mechanism_baseline": baseline_mechanism,
        "mechanism_drop": mechanism_drop,
        #: The floor is on MEASURED calls, and at CONFIRMATION it is 128,
        #: not 32. The first implementation of this rule hardcoded the
        #: exploratory minimum here, so a confirmation run that lost half
        #: its calls to a provider rate limit reported ADOPT on 64 measured
        #: calls. The rule TEXT always said 128; only the code disagreed.
        "required_n": required_n or MIN_EXPLORATORY_CALLS_PER_CASE,
        "enough_n": (candidate_n >= (required_n
                                     or MIN_EXPLORATORY_CALLS_PER_CASE)
                     and baseline_n >= (required_n
                                        or MIN_EXPLORATORY_CALLS_PER_CASE)),
        "improved_enough": improvement >= MIN_ABSOLUTE_IMPROVEMENT,
        "significant": p < SIGNIFICANCE_ALPHA,
        "mechanism_fell_enough": mechanism_drop >= MIN_MECHANISM_REDUCTION,
        "regressed": improvement < -MAX_TOLERATED_REGRESSION,
    }


def verdict(
    per_case: Sequence[Mapping[str, object]],
    *,
    regressions: Sequence[str] = (),
    is_confirmation: bool = False,
) -> Dict[str, object]:
    """ADOPT, REJECT or INCONCLUSIVE, by the rule and nothing else.

    ADOPT requires, all of them:
      * at least one case improved by >= MIN_ABSOLUTE_IMPROVEMENT with
        p < SIGNIFICANCE_ALPHA, and the axis mechanism at least halved there;
      * NO case regressed by more than MAX_TOLERATED_REGRESSION;
      * no dimension in MUST_PRESERVE broken, and nothing in
        PERFECT_AND_MUST_STAY_PERFECT moved off 100 %;
      * the evidence is a FRESH confirmation sample.
    """
    reasons: list = []
    carried = [c for c in per_case
               if c["improved_enough"] and c["significant"]
               and c["mechanism_fell_enough"]]
    regressed = [c for c in per_case if c["regressed"]]

    if regressions:
        reasons.append(f"regression gate: {', '.join(regressions)}")
    if regressed:
        reasons.append("case regression: "
                       + ", ".join(str(c["case"]) for c in regressed))
    if not carried:
        reasons.append(
            "no case met improvement >= "
            f"{MIN_ABSOLUTE_IMPROVEMENT:.3f} with p < {SIGNIFICANCE_ALPHA} "
            "and the mechanism at least halved")
    thin = [c for c in per_case if not c["enough_n"]]
    if thin:
        reasons.append(
            "sample below the pre-registered minimum: "
            + ", ".join(f"{c['case']} {c['candidate']} "
                        f"(needs {c['required_n']} measured)" for c in thin))

    if reasons:
        # An under-powered sample is UNFINISHED, not a rejection: the
        # candidate has not been given the test the rule specifies. Saying
        # REJECT there would record a candidate as measured-and-failed when
        # it was never properly measured.
        only_thin = bool(thin) and all(
            r.startswith("sample below") for r in reasons)
        return {"verdict": INCONCLUSIVE if only_thin else REJECT,
                "reasons": reasons,
                "carried": [str(c["case"]) for c in carried]}
    if not is_confirmation:
        return {"verdict": INCONCLUSIVE,
                "reasons": ["qualified on EXPLORATORY calls; a fresh "
                            "confirmation sample is required before adoption"],
                "carried": [str(c["case"]) for c in carried]}
    return {"verdict": ADOPT, "reasons": [],
            "carried": [str(c["case"]) for c in carried]}


REJECTION_MEANS: Final[str] = (
    "The committed prompt is LEFT UNCHANGED and the arm is recorded as "
    "measured and rejected. A rejected arm is a result, not a failure: "
    "Stage 70 rejected all four of its candidates and the rejection is what "
    "bounded the residual. Nothing is adopted to avoid an empty stage."
)


__all__ = [
    "ADOPT", "CASES", "INCONCLUSIVE", "MAX_TOLERATED_REGRESSION",
    "MIN_ABSOLUTE_IMPROVEMENT", "MIN_CONFIRMATION_CALLS_PER_CASE",
    "MIN_EXPLORATORY_CALLS_PER_CASE", "MIN_MECHANISM_REDUCTION",
    "MUST_PRESERVE", "PERFECT_AND_MUST_STAY_PERFECT", "PRIMARY_METRIC",
    "REJECT", "REJECTION_MEANS", "SAME_SESSION_CONTROL_REQUIRED",
    "SECONDARY_METRICS", "SIGNIFICANCE_ALPHA", "SIGNIFICANCE_TEST",
    "assess_case", "fisher_exact_two_sided", "verdict",
]
