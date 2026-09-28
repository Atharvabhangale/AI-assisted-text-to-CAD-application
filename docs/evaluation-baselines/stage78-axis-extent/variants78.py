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

# ------------------------------------------------------------- the anchor
#
# Every arm inserts immediately BEFORE `## through_hole`, which is the end
# of the `## cylinder` section and appears exactly once in the rendered
# prompt. Nothing else is touched: no other section is edited, reordered or
# deleted, so the only difference between any two arms is the block below.
#
# The cylinder section is the right place because the defect is about a
# CYLINDER's position. The prompt already says, once, that a cylinder's
# position is "The CENTRE of the base circle" and that a box's is "The
# MINIMUM corner" -- and then every worked example in 34036 characters
# places the drilled body at the origin, where a centre and a boundary are
# the same arithmetic. The one body that IS offset (a post at x=80) is
# never bored, so the difference is never made visible.

ANCHOR: str = "## through_hole"

#: No corpus wording appears in any arm. ED-02 asks for a 6 mm hole through
#: a d20 x 30 cylinder at x=100; CR-06 names a 60 x 40 x 8 plate. The arms
#: below use a d24 x 40 shaft at (70, 15) bored 8 mm, which shares no
#: number and no phrasing with either. Stage 66 declined to put a test's own
#: words into the prompt and recorded why.

# --- A: the rule, stated. No example. ---------------------------------
RULE_A: str = '''A cylinder's `position` is a point on its CENTRELINE, not a point on its
surface. The material reaches half the diameter to either side, so a
cylinder of diameter d whose position is x is solid from x - d/2 to
x + d/2. Those two numbers are BOUNDARIES. Neither of them is the axis,
and neither of them is where a hole along the axis goes: that hole's
position is the cylinder's own position, unchanged. A boundary is where
the material stops; an axis is the line it is arranged around.

'''

# --- B: one worked example. A body away from the origin. --------------
#
# The point of this arm, and the only reason it might work where prose
# does not: it is the first place in the prompt where a drilled body's
# axis, its minimum extent and its maximum extent are three DIFFERENT
# numbers, all four written down together.
EXAMPLE_B: str = '''A shaft standing away from the origin, bored along its own axis:

  {"id": "shaft", "type": "cylinder",
    "parameters": {"diameter": 24, "height": 40,
                  "position": {"x": 70, "y": 15, "z": 0}}}
  {"id": "hole_z", "type": "through_hole", "target": "shaft",
    "parameters": {"diameter": 8, "position": {"x": 70, "y": 15, "z": 0},
                  "axis": "+Z"}}

Read the numbers. The shaft's material runs from 58 to 82 in x and from
3 to 27 in y. Its axis is at 70 and 15. Those are four different numbers
and only the last pair is the axis -- 58, 82, 3 and 27 are faces. The bore
carries 70 and 15, the same pair the cylinder carries, because that pair
IS the centreline. Moving the shaft moves the bore with it: they are the
same two numbers, always.

'''

# --- C: B's material, reordered. THE CONTROL. -------------------------
#
# Identical semantic content to B -- same numbers, same sentences, same
# example -- with the narration BEFORE the code instead of after. Stage 70
# measured a PURE REORDERING regressing at p = 0.0001, so order is
# load-bearing in this prompt and a candidate that works must be shown to
# work because of what it says and not merely because of where it sits.
EXAMPLE_C: str = '''A shaft standing away from the origin, bored along its own axis.

Read the numbers. The shaft's material runs from 58 to 82 in x and from
3 to 27 in y. Its axis is at 70 and 15. Those are four different numbers
and only the last pair is the axis -- 58, 82, 3 and 27 are faces. The bore
carries 70 and 15, the same pair the cylinder carries, because that pair
IS the centreline. Moving the shaft moves the bore with it: they are the
same two numbers, always.

  {"id": "shaft", "type": "cylinder",
    "parameters": {"diameter": 24, "height": 40,
                  "position": {"x": 70, "y": 15, "z": 0}}}
  {"id": "hole_z", "type": "through_hole", "target": "shaft",
    "parameters": {"diameter": 8, "position": {"x": 70, "y": 15, "z": 0},
                  "axis": "+Z"}}

'''


def _with(block: str) -> str:
    """The committed prompt with `block` inserted before the anchor.

    IDEMPOTENT in the sense that matters: if the block is already present
    the prompt is returned unchanged rather than doubled, so an adopted arm
    becomes an alias of the baseline and `arena78` refuses to spend calls
    on it instead of quietly recording the baseline under its name.
    """
    base = _baseline()
    if _normalised(block) in _normalised(base):
        return base
    return _insert_once(base, ANCHOR, block)


def a_rule_only() -> str:
    """A: the semantic rule, stated in prose. No example."""
    return _with(RULE_A)


def b_worked_example() -> str:
    """B: one worked example of a translated body bored on its axis."""
    return _with(EXAMPLE_B)


def c_example_reordered() -> str:
    """C: B's material, narration first. THE ORDER CONTROL."""
    return _with(EXAMPLE_C)


CANDIDATES: Tuple[str, ...] = ("S1-rule-only", "S2-worked-example")
CONTROLS: Tuple[str, ...] = ("S3-example-reordered",)

VARIANTS: Dict[str, Callable[[], str]] = {
    BASELINE: _baseline,
    "S1-rule-only": a_rule_only,
    "S2-worked-example": b_worked_example,
    "S3-example-reordered": c_example_reordered,
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
