"""Phase E's arm and its control. Each is a pure function of the prompt.

An arm NEVER edits `prompt.py`. It returns a string and the runner patches
`system_prompt` for the life of one run -- Stage 70's pattern, followed by
Phase C and Phase D.

Baseline: `2026-09-25.1`, fingerprint `f265d7d1e279e95a`, 34036 characters.

---

WHAT IS BEING VARIED, AND WHY ONLY THIS

48 fresh calls on the committed prompt put R2 at 34/48, and the 14 failures
are one mechanism rather than noise. Every reply, pass or fail, refuses,
asks, names both bodies and writes no operations; what differs is the
DIAGNOSIS it states, and the split is total:

    FAIL  14/14   "the request does not say / specify / name which body"
    PASS  34/34   "the request names a body this part does not have"

Nine of the fourteen are byte-identical to each other. Not one mentions
`bracket` in any form. So the reply's SHAPE is not the problem -- it is
already right in both templates -- and an example of a reply cannot help.
Phase D measured that directly: a worked reply envelope moved 0 of 64 across
two sites, and four lines of prose moved it to 23/32.

What the model gets wrong is WHICH OF TWO RULES applies. And the prompt
hands it the failing sentence: `# Several bodies` opens its first rule with

    When a later request does not say WHICH body it means, do not choose one.

which is what all fourteen failures write back, nearly verbatim. A request
naming a body that does not exist DOES fail to say which body it means, read
literally -- so both rules' triggers match R2, and the first is stated
first.

This is Stage 65's and Stage 66's mechanism a fourth time: the failure is an
AFFORDANCE the prompt supplies, not a gap in it, and what moved those stages
was REMOVING the affordance rather than adding prose against it.

---

THE ARM, and the control that tells the two explanations apart

  E1  the trigger, narrowed. One sentence, one clause: "does not say WHICH
      body it means" becomes "uses no name at all". Nothing is added, moved
      or exemplified, and the second rule's opening -- "A request that DOES
      name one is a different question" -- becomes its exact complement, so
      the pair now reads as a dichotomy.

  E2  CONTROL, never a candidate. The same sentence rewritten to a
      DIFFERENT phrase that still describes R2: "leaves WHICH body unclear".
      Same edit site, same sentence, comparable length -- and the affordance
      intact. E1 against the baseline measures "the trigger was narrowed";
      E1 against E2 measures that NARROWING it is what mattered rather than
      touching it at all.

      Phase D's D3 was the same shape and produced that stage's sharpest
      result: it ruled out "any change at that site helps" by measurement
      instead of by argument. The rule is never applied to E2 as an
      adoption question.

**No corpus wording is used.** R1's request is "Make the body 10 mm taller."
and R3's is "Put a hole through it.", so an arm that listed "the body" or
"it" as examples of naming nothing would be putting the test's own words in
the prompt. Stage 66 declined to do that and recorded why.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict

from cad_experimental.prompt import system_prompt as _baseline

#: The prompt these were measured AGAINST.
MEASURED_AGAINST: str = "f265d7d1e279e95a"
MEASURED_CHARACTERS: int = 34036


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalised(text: str) -> str:
    """Whitespace collapsed, so a guard cannot depend on the wrap column."""
    return " ".join(text.split())


def _replace_once(text: str, anchor: str, replacement: str) -> str:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


#: The first rule's opening clause, verbatim in both the source f-string and
#: the rendered prompt: plain prose, no braces, no status word.
TRIGGER = "When a later request does not say WHICH body it means,"

#: E1's replacement. The complement of the second rule's own opening.
TRIGGER_NARROWED = "When a later request uses no name at all,"

#: E2's replacement. A different phrase that still describes R2, so the
#: affordance survives the edit. THIS IS THE CONTROL.
TRIGGER_REPHRASED = "When a later request leaves WHICH body unclear,"


def _swap(replacement: str) -> str:
    """Replace the trigger clause, or return the prompt unchanged.

    IDEMPOTENT, and wrap-insensitive. If an arm is adopted the committed
    prompt no longer carries `TRIGGER`, and a second `_replace_once` would
    raise rather than quietly measure the wrong text -- but an arm that
    returns the baseline is the honest answer, and the runner refuses to
    spend calls on an arm that has become an alias of it.
    """
    base = _baseline()
    if _normalised(TRIGGER) not in _normalised(base):
        return base
    return _replace_once(base, TRIGGER, replacement)


def e1_trigger_narrowed() -> str:
    """The arm: the first rule stops describing a request that names one."""
    return _swap(TRIGGER_NARROWED)


def e2_control_trigger_rephrased() -> str:
    """The CONTROL: the same sentence touched, the affordance intact."""
    return _swap(TRIGGER_REPHRASED)


VARIANTS: Dict[str, Callable[[], str]] = {
    "E0-baseline": _baseline,
    "E1-trigger-narrowed": e1_trigger_narrowed,
    "E2-control-rephrased": e2_control_trigger_rephrased,
}

#: Which entries are candidates for adoption. `E2` is a control and the
#: adoption rule is never asked about it.
CANDIDATES: tuple = ("E1-trigger-narrowed",)


__all__ = [
    "CANDIDATES",
    "MEASURED_AGAINST",
    "MEASURED_CHARACTERS",
    "TRIGGER",
    "VARIANTS",
    "fingerprint",
]
