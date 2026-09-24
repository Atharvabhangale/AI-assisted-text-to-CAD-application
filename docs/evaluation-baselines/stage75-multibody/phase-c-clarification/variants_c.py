"""Phase C prompt arms. Each is a pure function of the committed baseline.

An arm NEVER edits `prompt.py`. It returns a string, and `arena75c.py`
patches `system_prompt` for the life of one run. Stage 70's `variants70.py`
established the pattern and this follows it exactly.

Baseline: `2026-09-24.1`, fingerprint `c0c4a1be0d23052f`, 33407 characters.

---

WHAT IS BEING VARIED, AND WHY ONLY THIS

Phase B measured the clarification cases at 7/24 strict. Re-read with Phase
C's corrected metrics, over the same 24 recorded attempts:

    named BOTH bodies       9/24        operations: none  19/24
    named ONE body          3/24        operations: some   5/24
    named NEITHER          12/24        asked a question  12/24

Two things stand out, and only one of them is what the brief predicted.

**Half the clarifications ask nothing.** Twelve of twenty-four declared
`needs_clarification` and left `questions` empty, putting a vague sentence
in `summary` instead -- *"ambiguous request"*, *"the request is ambiguous
about which body to modify"*. A clarification that asks nothing is not a
question; it is a shrug with a status field.

**And the prompt has never shown the model a reply.** Measured on the
committed text: the string `"status"` occurs EXACTLY ONCE in 33407
characters, and so do `"questions"`, `"summary"` and `"operations"` -- all
four only in the bare field list under `# What to reply`. Every one of the
prompt's ~20 JSON blocks is a bare operation object. **The model has never
been shown a complete reply envelope of any status**, so it has no example
of what a clarification looks like, only a field list.

Four stages running have now found the same mechanism: what the model
imitates is what the prompt SHOWS, and a rule stated without an example
loses (Stages 65, 66, 69 for the geometry; Stage 70 measured four prose arms
moving nothing). That mechanism has never been applied to the reply envelope.

There is also a measured ASYMMETRY inside the prompt itself. The
`# When to say unsupported` section carries its own inline reminder --
*"-- with an empty operations list --"* -- inside its own opening sentence.
The `# When to say needs_clarification` section carries no such clause, and
does not mention bodies at all. The rule that operations must be empty
unless the status is `generated` is stated once, in the field list, 8271
characters earlier.

---

THE ARMS, one variable each

  CA  naming worked example -- a complete reply envelope whose `questions`
      NAMES BOTH bodies.
  CB  no-operations worked example -- the SAME envelope, in the same place,
      whose `questions` names NEITHER body. CA and CB differ in exactly one
      string: the question text. So CB against the baseline isolates "the
      model has now seen an envelope with `operations: []`", and CA against
      CB isolates "the example's question names the bodies".
  CC  compact structural rule -- the `unsupported` section's own clause,
      copied verbatim into the clarification section's opening sentence. No
      example, no new noun: the clean prose arm, and the negative control
      four prior stages predict will do nothing.
  CD  combined.

**A NOTE ON CD, and a deliberate deviation from the brief.** The brief asks
for "D -- COMBINED A+B". Under the design above that is degenerate: CA's
envelope already contains CB's `operations: []`, so CA+CB *is* CA. The two
mechanisms that can genuinely be combined are the EXAMPLE and the PROSE
CLAUSE, so CD is **CA + CC**. This is written down here, before any arm was
run, rather than decided after seeing which pair looked better.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Dict

from cad_experimental.prompt import system_prompt as _baseline

#: The baseline these arms were measured against.
#: The prompt these arms were measured AGAINST -- the pre-adoption one.
#: Kept as the historical fact it is; `_baseline()` now returns the
#: ADOPTED text, which is CA, and the guards above make that safe.
MEASURED_AGAINST: str = "c0c4a1be0d23052f"
ADOPTED_AS: str = "90ebab2c38d615fb"
MEASURED_CHARACTERS: int = 33407


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_once(text: str, anchor: str, replacement: str) -> str:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


# --- the anchors, verbatim from the rendered baseline ------------------------

#: The clarification section's opening sentence.
CLARIFY_OPENING = (
    'Say "needs_clarification" when a required parameter is\n'
    "genuinely not in the description and has no default -- a cylinder with no\n"
    "diameter, a box with only two dimensions, or a length with no unit given."
)

#: The same sentence carrying the clause the `unsupported` section already
#: carries. Copied from that section rather than composed, so the two read
#: identically and the arm varies placement, not wording.
CLARIFY_OPENING_WITH_CLAUSE = (
    'Say "needs_clarification" -- with an empty operations list -- when a\n'
    "required parameter is genuinely not in the description and has no default\n"
    "-- a cylinder with no diameter, a box with only two dimensions, or a\n"
    "length with no unit given."
)

#: The worked example, with one blank left for the question. Placed at the END
#: of the clarification section so nothing above it moves: Stage 70's B3
#: showed that reordering a section is itself a large effect, so an arm that
#: appended text and an arm that moved text would not be one variable.
_EXAMPLE = """

This is the WHOLE reply, and nothing else goes in it:

  {{"status": "needs_clarification",
   "summary": "the request does not say which body to change",
   "questions": [{question}],
   "operations": []}}

`operations` is empty because nothing was built. You are asking, not
building."""

#: CA's question: it names both bodies of the part it is asking about.
QUESTION_NAMING = (
    '"This part has two bodies, `plate` and `post`. '
    'Which one should be changed?"'
)

#: CB's question: the same shape, naming neither. The ONLY difference
#: between CA and CB.
QUESTION_PLAIN = '"Which body should be changed?"'

#: Where the example is appended -- the last sentence of the clarification
#: section, which ends the section.
CLARIFY_TAIL = (
    "one -- \"the top edge\", \"the long edges\" -- it is not silent, and you write\n"
    "the selector that says so. See the selector rules above."
)


#: The line the example opens with. Its presence means an example is already
#: in the committed prompt -- which it is, since CA was adopted.
EXAMPLE_MARKER = "This is the WHOLE reply, and nothing else goes in it:"


def _with_example(question: str) -> str:
    """Append the worked reply envelope, or return the baseline unchanged.

    IDEMPOTENT, and that matters after adoption. CA is now the committed
    prompt, so `_baseline()` already contains an example and a second
    `_replace_once` would append a second one -- an arm silently measuring a
    prompt with the example TWICE. Stage 70's `_b1` guards the same way for
    the same reason.
    """
    base = _baseline()
    if EXAMPLE_MARKER in base:
        return base
    return _replace_once(
        base, CLARIFY_TAIL,
        CLARIFY_TAIL + _EXAMPLE.format(question=question))


def ca_naming_example() -> str:
    """A reply envelope whose question names both bodies."""
    return _with_example(QUESTION_NAMING)


def cb_no_operations_example() -> str:
    """The same envelope, naming neither body."""
    return _with_example(QUESTION_PLAIN)


def cc_structural_rule() -> str:
    """The `unsupported` section's own clause, in the clarification section."""
    base = _baseline()
    if CLARIFY_OPENING not in base:          # already adopted
        return base
    return _replace_once(base, CLARIFY_OPENING, CLARIFY_OPENING_WITH_CLAUSE)


def cd_combined() -> str:
    """CA and CC together. See the note on CD in the module docstring.

    Idempotent on both halves, for the reason `_with_example` gives.
    """
    text = _baseline()
    if CLARIFY_OPENING in text:
        text = _replace_once(text, CLARIFY_OPENING, CLARIFY_OPENING_WITH_CLAUSE)
    if EXAMPLE_MARKER not in text:
        text = _replace_once(
            text, CLARIFY_TAIL,
            CLARIFY_TAIL + _EXAMPLE.format(question=QUESTION_NAMING))
    return text


VARIANTS: Dict[str, Callable[[], str]] = {
    "C0-baseline": _baseline,
    "CA-naming-example": ca_naming_example,
    "CB-no-operations-example": cb_no_operations_example,
    "CC-structural-rule": cc_structural_rule,
    "CD-combined": cd_combined,
}


__all__ = [
    "MEASURED_AGAINST",
    "MEASURED_CHARACTERS",
    "VARIANTS",
    "fingerprint",
]
