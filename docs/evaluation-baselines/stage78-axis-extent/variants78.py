"""Stage 78 prompt arms: the text each one renders, and nothing else.

Stage 70's pattern, followed by Stage 75 Phases D and E and followed here: an
arm is a FUNCTION that returns a whole prompt string, and `arena78` patches
`system_prompt` for the life of one run. The committed prompt is never
edited on disk by an experiment; adoption is a separate, deliberate act.

**This file starts with the baseline and nothing else.** The candidate arms
are added only after `decision_rule_78.py` is committed, so the rule cannot
have been written around a candidate's wording. That ordering is the whole
reason the rule means anything, and it is enforced here by construction
rather than by intention: `python3 -c "import variants78; print(variants78.
CANDIDATES)"` on the commit that introduces the rule returns an empty tuple.

No corpus wording may appear in an arm. ED-02's request is "Put a 6 mm
diameter hole all the way through the cylinder along its axis." and CR-06's
names a 60 x 40 x 8 plate; an arm that used those numbers or that phrasing
would be putting the test's own words into the prompt. Stage 66 declined to
do that and recorded why.
"""

from __future__ import annotations

import hashlib
from typing import Callable, Dict, Tuple

from cad_experimental.prompt import system_prompt as _baseline

#: The prompt these arms are measured AGAINST -- the Stage 77 committed text.
MEASURED_AGAINST: str = "f265d7d1e279e95a"
MEASURED_CHARACTERS: int = 34036


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalised(text: str) -> str:
    """Whitespace collapsed, so a guard cannot depend on the wrap column."""
    return " ".join(text.split())


def _insert_once(text: str, anchor: str, addition: str) -> str:
    """Put `addition` immediately before `anchor`, which must be unique.

    Raises rather than guessing if the anchor has moved or repeated: an arm
    that silently inserted in the wrong place would measure something other
    than what it claims to.
    """
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, addition + anchor)


BASELINE: str = "S0-baseline"

#: Filled in by the commit that follows the decision rule. Empty here, on
#: purpose -- see the module docstring.
CANDIDATES: Tuple[str, ...] = ()
CONTROLS: Tuple[str, ...] = ()

VARIANTS: Dict[str, Callable[[], str]] = {
    BASELINE: _baseline,
}


def text_for(arm: str) -> str:
    if arm not in VARIANTS:
        raise KeyError(f"unknown arm {arm!r}; known: {', '.join(VARIANTS)}")
    return VARIANTS[arm]()


def role_of(arm: str) -> str:
    if arm == BASELINE:
        return "baseline"
    if arm in CANDIDATES:
        return "CANDIDATE"
    if arm in CONTROLS:
        return "CONTROL"
    return "unclassified"


__all__ = [
    "BASELINE", "CANDIDATES", "CONTROLS", "MEASURED_AGAINST",
    "MEASURED_CHARACTERS", "VARIANTS", "fingerprint", "role_of", "text_for",
    "_insert_once", "_normalised",
]
