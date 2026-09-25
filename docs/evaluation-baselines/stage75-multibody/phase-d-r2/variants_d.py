"""Phase D prompt arms. Each is a pure function of the committed prompt.

An arm NEVER edits `prompt.py`. It returns a string, and `arena75d.py`
patches `system_prompt` for the life of one run. Stage 70's `variants70.py`
established the pattern and Phase C's `variants_c.py` followed it; this
follows both.

Baseline: `2026-09-24.2`, fingerprint `90ebab2c38d615fb`, 33759 characters.

---

WHAT IS BEING VARIED, AND WHY ONLY THIS

The fresh R2 baseline is 0/32 and every one of the 32 attempts is the SAME
failure. `classify_d` puts all 32 in class B: the model declines, names both
bodies, asks a real question, writes no operations -- and never says
`bracket`, the word the user used. Nothing else in the taxonomy fired once.

The prompt explains it exactly. Measured on the committed text:

  * the ONLY worked clarification is the AMBIGUOUS-reference case. Its
    question is *"This part has two bodies, `plate` and `post`. Which one
    should be changed?"* -- the answer to a request that named NEITHER;
  * the closing rule of `# Several bodies` is about the same case:
    *"Make it 10 mm taller" with a plate and a post standing names neither*.
    That is a PRONOUN;
  * and nothing in 33759 characters addresses a request that names a body
    which does not exist. Searched for `nonexistent`, `no such`, `unknown`,
    `not present`, `no solid named`, `matched nothing`, `no body`: zero
    relevant hits. The one occurrence of "does not exist" is about a
    plan-internal target id, in the union section.

So R2 falls through to the pronoun rule and answers the question that rule
teaches. It is answering correctly -- a different question.

Five stages have now measured the same mechanism: what the model imitates is
what the prompt SHOWS, and a rule stated without an example loses (65, 66,
69 for the geometry; 70's four prose arms; Phase C for the reply envelope).
The prompt has never shown this reply.

---

THE ARMS

  D1  missing-body example -- a second reply envelope, appended AFTER the
      existing one so nothing above it moves, for the case where the
      request names a body that is not there. Its question says the name
      matched nothing and then lists the bodies that exist.

  D2  CONTROL, not a candidate. The SAME envelope in the SAME place, whose
      question lists the bodies and asks which was meant WITHOUT saying the
      missing name. D1 and D2 differ in exactly one string: the question
      text. So D1 against the baseline measures "a second envelope, for
      this case, exists", and D1 against D2 measures "the example ECHOES
      the name that matched nothing".

      It is run only if D1 moves R2, and it is run to find out WHY, not to
      be adopted. Phase C's CB was the same shape and produced that stage's
      sharpest result: it showed the naming WORDING was not the active
      ingredient, which no amount of reading the two texts could have said.

**The example's nouns are the PROMPT's own.** `plate` and `post` are what
both existing examples use, and the missing name is `flange` -- a word that
appears in no corpus case. R2's own nouns (`block`, `rod`, `bracket`) are
deliberately NOT used: an example built from the test's own words is
teaching to the test, and Stage 66 declined to do it for exactly this
reason.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict

from cad_experimental.prompt import system_prompt as _baseline

#: The prompt these arms were measured AGAINST -- the pre-adoption one.
#: Kept as the historical fact it is; `_baseline()` now returns the ADOPTED
#: text, which is D4, and the idempotency guards make that safe.
MEASURED_AGAINST: str = "90ebab2c38d615fb"
MEASURED_CHARACTERS: int = 33759
ADOPTED_AS: str = "f265d7d1e279e95a"


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalised(text: str) -> str:
    """Whitespace collapsed, so a guard cannot depend on the wrap column.

    `CONTRAST` is a wrapped paragraph. A sentinel taken as "everything up to
    the first newline" encodes where the line happens to break, so a pure
    reflow of the committed prompt -- which changes nothing it says -- would
    make the guard miss and the arm would append the paragraph a second
    time. Comparing on collapsed whitespace makes the sentinel a sentence.
    """
    return " ".join(text.split())


def _replace_once(text: str, anchor: str, replacement: str) -> str:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


# --- the anchor, verbatim from BOTH the source and the render ----------------

#: The last paragraph of the clarification section. Chosen because it is
#: plain prose -- no `{`, `}` and no status word -- so it is byte-identical
#: in `prompt.py`'s f-string and in the rendered text, and an edit written
#: here can be applied to the source unchanged. It occurs exactly once.
#:
#: Appending AFTER it puts the new example at the very END of the section,
#: so every character above is where it was. Stage 70's B3 measured that
#: reordering a section is itself a large effect (strict 135/144 -> 21/32,
#: p = 0.0001), so an arm that appended text and an arm that moved text
#: would not be one variable.
CLARIFY_TAIL = (
    "`operations` is empty because nothing was built. You are asking, not\n"
    "building."
)

#: The example, with one blank left for the question.
_EXAMPLE = """

A name that matches no body is not the same question. `flange` is not one of
this part's bodies, so say that, and list the bodies there are:

  {{"status": "needs_clarification",
   "summary": "this part has no body called `flange`",
   "questions": [{question}],
   "operations": []}}"""

#: D1's question: it says the name matched nothing, then lists what exists.
QUESTION_NAMES_THE_MISS = (
    '"There is no body called `flange`. This part\'s bodies are `plate` and '
    '`post`. Which of them did you mean?"'
)

#: D2's question: the same shape, never saying `flange`. The ONLY difference
#: between D1 and D2.
QUESTION_WITHOUT_THE_MISS = (
    '"This part\'s bodies are `plate` and `post`. Which of them did you mean?"'
)

#: The line the example opens with. Its presence means the example is
#: already in the committed prompt, which it will be if D1 is adopted.
EXAMPLE_MARKER = "A name that matches no body is not the same question."


def _with_example(question: str) -> str:
    """Append the missing-body envelope, or return the baseline unchanged.

    IDEMPOTENT, and that matters after adoption: `_baseline()` would then
    already contain the example and a second `_replace_once` would append a
    second copy -- an arm silently measuring a prompt carrying the example
    TWICE. Phase C's `_with_example` and Stage 70's `_b1` guard the same way
    for the same reason.
    """
    base = _baseline()
    if EXAMPLE_MARKER in base:
        return base
    return _replace_once(
        base, CLARIFY_TAIL,
        CLARIFY_TAIL + _EXAMPLE.format(question=question))


def d1_missing_body_example() -> str:
    """A reply envelope whose question says the name matched nothing."""
    return _with_example(QUESTION_NAMES_THE_MISS)


def d2_control_without_the_miss() -> str:
    """The same envelope, never saying the missing name. A CONTROL."""
    return _with_example(QUESTION_WITHOUT_THE_MISS)


# --- the second site, added after D1 was measured at 0/32 -------------------
#
# D1 put the example at the end of `# When to say needs_clarification` and
# moved NOTHING: 32/32 still class B, and the replies are the same template
# as the baseline's, word for word. So the example did not land, and reading
# the prompt says why.
#
# `# Several bodies` closes with a worked sentence of its OWN:
#
#     When a later request does not say WHICH body it means, do not choose
#     one. Say "needs_clarification" and ask, listing the bodies by name.
#     "Make it 10 mm taller" with a plate and a post standing names neither,
#     and guessing is worse than asking.
#
# R2's request is *"Make the bracket 10 mm taller."* -- that sentence with a
# noun where the pronoun is. The prompt is not silent about R2; it teaches
# an answer for it, and the model gives exactly the answer prescribed:
# "listing the bodies by name". Every reply in both runs is that answer.
#
# This is Stage 65's and Stage 66's mechanism, a third time: the failure is
# an AFFORDANCE the prompt supplies, not a gap in it, and Stage 65 fixed its
# own by DELETING the sentence that invited the wrong answer rather than by
# adding a rule elsewhere. Here the sentence cannot simply be deleted -- it
# is the correct answer for R1 and R3, which are 24/24 on it -- so the two
# arms below put the distinction at the site where the mis-firing rule
# already is.

#: The closing paragraph of `# Several bodies`. Plain prose in both the
#: source f-string and the render (no `{`, `}` or status word), and it
#: occurs exactly once in each.
BODIES_TAIL = (
    '"Make it 10 mm taller" with a plate and a post standing names neither, and\n'
    "guessing is worse than asking."
)


def d3_example_at_the_bodies_rule() -> str:
    """D1's example, at the site of the rule that mis-fires.

    ONE variable against D1: the location. The text appended is
    byte-identical to D1's -- `_EXAMPLE` formatted with the same question --
    so a difference between them is a difference of place and nothing else.
    """
    base = _baseline()
    if EXAMPLE_MARKER in base:
        return base
    return _replace_once(
        base, BODIES_TAIL,
        BODIES_TAIL + _EXAMPLE.format(question=QUESTION_NAMES_THE_MISS))


#: D4's contrast. It is deliberately NOT R2's sentence with the noun swapped:
#: "5 mm wider" rather than "10 mm taller", so the prompt does not carry a
#: near-copy of a corpus request. Stage 66 declined to tune a prompt towards
#: its own test's wording and the same rule applies here.
CONTRAST = """

A request that DOES name one is a different question. "Make the flange 5 mm
wider", with `plate` and `post` standing, names a body this part does not
have. Say that `flange` matches nothing, and then list the bodies there are.
Do not answer it as though it had named neither."""


#: The first SENTENCE of the contrast, not its first LINE. See `_normalised`.
CONTRAST_MARKER = "A request that DOES name one is a different question."


def d4_contrast_at_the_bodies_rule() -> str:
    """The distinction stated where the mis-firing rule already is.

    No envelope, no second example: the one thing it adds is that the
    section now covers TWO cases instead of one, at the point where it
    already covers the first. Against D3 it varies the FORM -- prose beside
    the existing prose, rather than a worked reply -- at the same site.

    Idempotent, and wrap-insensitive: this is the committed prompt since
    Phase D adopted it, so a guard that missed would silently measure a
    prompt carrying the paragraph TWICE.
    """
    base = _baseline()
    if _normalised(CONTRAST_MARKER) in _normalised(base):
        return base
    return _replace_once(base, BODIES_TAIL, BODIES_TAIL + CONTRAST)


VARIANTS: Dict[str, Callable[[], str]] = {
    "D0-baseline": _baseline,
    "D1-missing-body-example": d1_missing_body_example,
    "D2-control-no-miss": d2_control_without_the_miss,
    "D3-example-at-bodies-rule": d3_example_at_the_bodies_rule,
    "D4-contrast-at-bodies-rule": d4_contrast_at_the_bodies_rule,
}


__all__ = [
    "ADOPTED_AS",
    "CONTRAST_MARKER",
    "EXAMPLE_MARKER",
    "MEASURED_AGAINST",
    "MEASURED_CHARACTERS",
    "VARIANTS",
    "fingerprint",
]
