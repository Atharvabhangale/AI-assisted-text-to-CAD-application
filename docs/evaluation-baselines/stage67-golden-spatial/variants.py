"""Stage 67 prompt variants. Each is a pure function of the committed baseline.

A variant NEVER edits prompt.py. It returns a string, and the arena patches
`system_prompt` for the life of one arm. The committed prompt stays the
baseline until a variant has earned its place by measurement.

Baseline: `2026-09-18.3` (Stage 66), fingerprint c78aaad8eacf365e, 30481 chars.
"""
from __future__ import annotations

import hashlib

from cad_experimental.prompt import system_prompt as _baseline


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(text: str, anchor: str) -> None:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:70]!r}")


VARIANTS: dict[str, dict] = {
    "G0-baseline": {
        "hypothesis": "control -- the committed prompt 2026-09-18.3, unchanged",
        "changed": "nothing",
        "text": _baseline,
    },
}


# =========================================================================
# MEASURED on the 8-call G0 baseline (arm-G0-baseline.json): 2/8 spatially
# correct. Failure mode A ("one hole per wall"), which the Stage 67 brief
# expected to dominate, occurred ZERO times: the model wrote 3 bores on 6/8
# and 6 plates on 7/8. What actually goes wrong is WHERE THE FAR PLATE OF A
# FACING PAIR GOES, and it goes wrong in two distinguishable ways.
#
#   #   t   top z   correct   envelope      what happened
#   1   5   35      35        40x20x40      self-consistent at height 40; P11 (union id `assembly`)
#   2   4    1      16        40x20x5       read "5" as the BOX height, so extent-t = 1  -> slab
#   3   5    5      15        40x20x20      top at z = t, not extent - t -> internal shelf
#   4   4    1      16        (E3 fail)     same as #2
#   5   5   20      15        40x20x25      top at z = extent, not extent - t
#   6   4   16      16        40x20x20      CORRECT
#   7   5   15      15        40x20x20      CORRECT
#   8   5   15      15        (E1 fail)     invented y=60, 5 plates, 2 coaxial Z bores
#
# ROOT CAUSE 1 -- THE BOX HEIGHT IS UNDER-DETERMINED BY THE PROMPT'S OWN RULE.
# The envelope paragraph derives the third extent as "the 50 from the end,
# which is the only number the bottom did not already give". For this request
# the bottom is 40 x 20 and the ends are 20 x 20: BOTH of the end's numbers
# are already given by the bottom, so that reasoning yields no height at all.
# The model then picks one -- 40 (#1), 5 (#2, #4), 25 (#5) or 20 (#3, #6, #7).
# The rule is not wrong, it is UNDER-DETERMINED exactly when a face is square
# or shares both dimensions with its neighbour.
#
# ROOT CAUSE 2 -- THE FAR PLATE'S OFFSET. Even with the height right, the far
# plate of the pair lands at t (#3) or at extent (#5) instead of extent - t.
# The rule is stated once, inside a longer sentence, and never names the two
# wrong answers.

_ENVELOPE_RULE = (
    "The plates also GIVE the outer size -- do not choose one. A plate's two\n"
    "non-thickness dimensions are the face it covers, and that face is a face of\n"
    "the box. Plates of 90 by 70 for the bottom and the top, and 70 by 50 for the\n"
    "ends, describe a box 90 by 70 by 50: the 90 and the 70 from the bottom, and\n"
    "the 50 from the end, which is the only number the bottom did not already\n"
    "give. If a height seems to be missing, it is in one of the other plates --\n"
    "read it off, do not invent it."
)

_ENVELOPE_RULE_V2 = (
    "The plates also GIVE the outer size -- do not choose one. A plate's two\n"
    "non-thickness dimensions are the two extents of the face it covers, so\n"
    "read each extent off the plate that shows it:\n"
    "\n"
    "  the bottom or top plate  gives the LENGTH and the WIDTH\n"
    "  the front or back plate  gives the LENGTH and the HEIGHT\n"
    "  the end (left or right)  gives the WIDTH  and the HEIGHT\n"
    "\n"
    "Plates of 90 by 70 for the bottom and 70 by 50 for the ends describe a box\n"
    "90 by 70 by 50. Take the height from the end plate BECAUSE IT IS AN END\n"
    "PLATE, not because 50 is a number you had not seen yet: two faces of a box\n"
    "often share a dimension, and an end plate whose numbers both already appear\n"
    "in the bottom still gives the height. Ends of 70 by 70 on the same bottom\n"
    "describe a box 90 by 70 by 70. A height is never missing and is never\n"
    "yours to choose -- some plate is standing in it."
)

_PAIR_RULE = (
    "`position` is the piece's MINIMUM corner, so a facing pair sits at 0 and at\n"
    "(extent - t) along its own axis, and at 0 on the other two."
)

_PAIR_RULE_V2 = (
    "`position` is the piece's MINIMUM corner, so a facing pair sits at 0 and at\n"
    "(extent - t) along its own axis, and at 0 on the other two. The second\n"
    "plate of the pair goes at `extent - t` -- never at `t`, which would stand\n"
    "it against its partner inside the box, and never at `extent`, which would\n"
    "float it just outside. A plate 4 thick closing a box 30 tall starts at 26."
)


def _with_envelope_rule() -> str:
    text = _baseline()
    _require(text, _ENVELOPE_RULE)
    return text.replace(_ENVELOPE_RULE, _ENVELOPE_RULE_V2, 1)


def _with_pair_rule() -> str:
    text = _baseline()
    _require(text, _PAIR_RULE)
    return text.replace(_PAIR_RULE, _PAIR_RULE_V2, 1)


def _with_both() -> str:
    text = _with_envelope_rule()
    _require(text, _PAIR_RULE)
    return text.replace(_PAIR_RULE, _PAIR_RULE_V2, 1)


VARIANTS["H1-envelope-from-the-face"] = {
    "hypothesis": "HYPOTHESIS 1: the height is under-determined. The prompt "
                  "derives the third extent as 'the only number the bottom "
                  "did not already give', which yields nothing when the end "
                  "plate is 20x20 against a 40x20 bottom. Naming WHICH PLATE "
                  "gives which extent resolves it structurally.",
    "changed": "rewrites the envelope-derivation paragraph only. No change to "
               "the facing-pair sentence, the permutation table or the "
               "worked example.",
    "text": _with_envelope_rule,
}

VARIANTS["H2-far-plate-offset"] = {
    "hypothesis": "HYPOTHESIS 2: the far plate lands at t or at extent "
                  "instead of extent - t (attempts 3 and 5). Naming the two "
                  "wrong answers fixes it.",
    "changed": "extends the facing-pair sentence only. No envelope change.",
    "text": _with_pair_rule,
}

VARIANTS["H3-both"] = {
    "hypothesis": "both root causes are present and independent, so this is "
                  "the arm that can reach 5/5. H1 and H2 are its controls.",
    "changed": "H1 + H2, prompt only",
    "text": _with_both,
}


# =========================================================================
# H3 MEASURED (8 calls): six plates 8/8, three bores 8/8 -- the layout fix
# WORKED completely, and failure modes D and A vanished from the structure.
# But P11 went 1/8 -> 4/8 and validation 7/8 -> 4/8.
#
# The union ids show the mechanism, and it is not Stage 65's:
#   H3 #1, #3 named the union `fuse` CORRECTLY and then wrote
#   "target": "fuse" anyway -- the precise thing the mandate's last sentence
#   warns about. #2 and #8 fell back to the product noun `assembled`.
#
# In the baseline that same mandate held 7/8. The one thing H1+H2 changed
# about it is DISTANCE: the Stage 66 layout block already sat between the
# mandate and the end of the `## union` section, and H1+H2 made that block
# 612 characters longer, so the rule is further from the point where the
# model writes a hole's target.
#
# H4 therefore changes ONE thing against H3: it moves the `fuse` mandate to
# the END of the union section, after the layout block, so the last thing
# read before `## fillet` is the rule about what a hole may target. No
# wording changes.

_FUSE_MANDATE = (
    "Name the union itself `fuse` -- that exact word, always. It is an action,\n"
    "not a thing, and that is the point: there is no solid called `fuse`, so a\n"
    "later operation cannot be tempted to target it. If you catch yourself\n"
    "writing `\"target\": \"fuse\"`, the answer you wanted is the union's own\n"
    "`target`."
)


def _with_mandate_last() -> str:
    """H3, with the `fuse` mandate relocated to the end of `## union`."""
    text = _with_both()
    _require(text, _FUSE_MANDATE)
    _require(text, "\n\n## fillet\n")
    # Lift the mandate out of its current position...
    text = text.replace(_FUSE_MANDATE + "\n\n", "", 1)
    # ...and put it back as the last paragraph of the union section.
    return text.replace("\n\n## fillet\n",
                        "\n\n" + _FUSE_MANDATE + "\n\n## fillet\n", 1)


VARIANTS["H4-mandate-last"] = {
    "hypothesis": "HYPOTHESIS 4: H3's layout fix is right and its P11 "
                  "regression is a DISTANCE effect -- the `fuse` mandate is "
                  "now 612 characters further from the end of the union "
                  "section. Moving it last, unchanged, should keep H3's "
                  "structure win and restore the baseline's targeting.",
    "changed": "H3 with the `fuse` mandate relocated to the end of the union "
               "section. Identical wording; position only.",
    "text": _with_mandate_last,
}


# =========================================================================
# THE MEASUREMENT THAT DECIDES H5, across five arms and 40 live calls:
#
#   arm               union section chars   P11/8   plates 6/8   bores 3/8   CORRECT
#   G0-baseline                      4550       1            7           6         2
#   H2-far-plate                     4784       3            6           5         2
#   H1-envelope                      4928       3            7           4         2
#   H4-mandate-last                  5162       3            6           5         2
#   H3-both                          5162       4            8           8         3
#
# P11 rises monotonically with the LENGTH OF THE `## union` SECTION, and it
# does so whether the added text is about envelopes (H1), offsets (H2), both
# (H3) or merely reordered (H4). Meanwhile H3 is the only arm that fixed the
# geometry: six plates 8/8 and three bores 8/8, with failure modes A and D
# gone from the structure entirely.
#
# So the two rules are in conflict only because they share a section. The
# layout rules are not about `union` at all -- they are about WHERE A BOX
# GOES, and `## box` is where a reader looks for that. H5 moves the whole
# layout block, H1's and H2's improvements included, out of `## union` and
# into `## box`. Not one word of the layout text changes. The union section
# does not merely return to its baseline LENGTH -- it becomes SHORTER than
# the baseline's (2259 against 4550), because Stage 66 had appended that
# block inside `## union` in the first place. By the measured trend that is
# the most favourable point yet tested for P11, and it is the reason H5 is
# worth calls rather than a tidy-up.

_LAYOUT_START = "\n\nLaying out an enclosure, or any part whose pieces sit on different faces:"
_BOX_SECTION_END = (
    '  position  optional, {"x": n, "y": n, "z": n}. The MINIMUM corner of the\n'
    "            box. Omit it when the description does not say where the box is.\n"
)


def _with_layout_in_box_section() -> str:
    """H3's text, with the layout block relocated from `## union` to `## box`."""
    text = _with_both()
    start = text.index(_LAYOUT_START)
    end = text.index("\n\n## fillet\n", start)
    block = text[start:end]
    # Remove it from the union section...
    text = text[:start] + text[end:]
    # ...and place it where a reader looks for box placement.
    _require(text, _BOX_SECTION_END)
    return text.replace(_BOX_SECTION_END, _BOX_SECTION_END + block.lstrip("\n") + "\n", 1)


VARIANTS["H5-layout-in-box-section"] = {
    "hypothesis": "HYPOTHESIS 5: H3's geometry win and H3's P11 regression "
                  "are separable, because the regression tracks the LENGTH "
                  "of the union section (measured: 4550->1, 4784->3, "
                  "4928->3, 5162->4) rather than anything the added text "
                  "says. Moving the layout block into `## box` restores the "
                  "union section byte-for-byte and should keep 8/8 plates "
                  "and 8/8 bores.",
    "changed": "H3 with the layout block relocated from `## union` to "
               "`## box`. No wording changes anywhere.",
    "text": _with_layout_in_box_section,
}


# =========================================================================
# H5 MEASURED: P11 fell to 1/8 -- the length trend holds -- but the geometry
# collapsed with it (six plates 4/8, three bores 4/8, correct 1/8). The
# layout rule only does its work while the model is assembling plates, and
# `## box` is not where that happens. So the trade-off is real and the
# question becomes: can the union section hold the RULES without the LENGTH?
#
# H3's layout block is 2903 characters, of which the worked 60x30x30 example
# and its "Read the sizes" gloss are the largest single chunk and the least
# rule-bearing part -- and Stage 66 already measured that an example's
# numbers leak into the part. H6 therefore keeps every RULE (the permutation
# table, the facing-pair offset with its two named wrong answers, and the
# envelope-from-the-face table) and drops the example.

def _with_compact_layout() -> str:
    """H3's rules, in `## union`, without the worked example."""
    text = _with_both()
    start = text.index("\n\nA 60 by 30 by 30 enclosure from 6 mm plate is exactly this:")
    end = text.index("\n\n## fillet\n", start)
    return text[:start] + text[end:]


VARIANTS["H6-rules-without-example"] = {
    "hypothesis": "HYPOTHESIS 6: P11 tracks the LENGTH of the union section "
                  "(measured 2259->1, 4550->1, 4784->3, 4928->3, 5162->4) "
                  "and the geometry tracks the RULES being in that section "
                  "(H5). Keeping H3's rules and dropping its worked example "
                  "should buy both.",
    "changed": "H3 with the worked 60x30x30 example and its gloss removed. "
               "Every rule H3 states is kept, word for word, in place.",
    "text": _with_compact_layout,
}


# =========================================================================
# H6 MEASURED, and it REFUTES the length reading H5 was built on: H6's union
# section is 4111 characters -- SHORTER than the baseline's 4550 -- and it
# produced the worst P11 of any arm, 6/8, with duplicate coaxial cuts on 4/8
# and 0/8 correct. Union-section length does not drive P11. Across H1-H5 the
# apparent monotone trend was noise at n=8 (1/8 vs 3/8 is not a real
# difference on eight calls); H6 is the arm that shows it.
#
# What H6 DOES establish is that the worked example is load-bearing. It is
# the only place the model is shown a complete, correct union with a correct
# `target`, and removing it cost six P11s.
#
# And reading the example against the failures names the real gap: THE
# EXAMPLE STOPS AT THE UNION. It shows six plates and a `fuse`, and then
# nothing. The model has never been shown an enclosure plan that CONTINUES
# INTO ITS HOLES -- which is precisely where every remaining failure lives:
# the bore count (six instead of three), the duplicate coaxial cut, and the
# hole's target (`fuse` instead of the union's target).
#
# H7 completes the example. Diameter 10 is chosen because the request says
# 8: Stage 66 measured that an example's numbers are read as data, so the
# example must not offer a diameter the request already fixes.

_EXAMPLE_TAIL = (
    '  {"id": "fuse",  "type": "union", "target": "base",\n'
    '   "tools": ["lid", "front", "back", "left", "right"]}\n'
)

_EXAMPLE_WITH_BORES = _EXAMPLE_TAIL + (
    '  {"id": "hole_z", "type": "through_hole", "target": "base",\n'
    '   "parameters": {"diameter": 10, "axis": "+Z",\n'
    '                  "position": {"x": 30, "y": 15, "z": 0}}}\n'
    '  {"id": "hole_y", "type": "through_hole", "target": "base",\n'
    '   "parameters": {"diameter": 10, "axis": "+Y",\n'
    '                  "position": {"x": 30, "y": 0, "z": 15}}}\n'
    '  {"id": "hole_x", "type": "through_hole", "target": "base",\n'
    '   "parameters": {"diameter": 10, "axis": "+X",\n'
    '                  "position": {"x": 0, "y": 15, "z": 15}}}\n'
)

_READ_THE_SIZES = "Read the sizes: only `base` and `lid` carry the 6 on Z."

_READ_THE_SIZES_V2 = (
    "Read the holes first: THREE of them for six pierced walls, one along "
    "each\naxis, every one targeting `base` -- the union's target, because "
    "`fuse` names\nno solid. Each hole's two components across its own axis "
    "sit at the middle\nof the part; the third is free.\n"
    "\n"
    "Read the sizes: only `base` and `lid` carry the 6 on Z."
)


def _with_complete_example() -> str:
    """H3, with the worked example continued into its three bores."""
    text = _with_both()
    _require(text, _EXAMPLE_TAIL)
    text = text.replace(_EXAMPLE_TAIL, _EXAMPLE_WITH_BORES, 1)
    _require(text, _READ_THE_SIZES)
    return text.replace(_READ_THE_SIZES, _READ_THE_SIZES_V2, 1)


VARIANTS["H7-example-through-the-holes"] = {
    "hypothesis": "HYPOTHESIS 7: H6 proved the worked example is "
                  "load-bearing, and the example STOPS AT THE UNION. Every "
                  "remaining failure -- bore count, coaxial duplicates, and "
                  "the `fuse` target -- lives in the part of the plan the "
                  "example never shows. Continuing it through its three "
                  "bores should address all three at once.",
    "changed": "H3 with three through_holes added to the worked example and "
               "a two-sentence gloss. No rule is reworded.",
    "text": _with_complete_example,
}


# =========================================================================
# H7 ADOPTED as prompt 2026-09-18.4 (f65a78d45c2b2f73, 31856 chars), and a
# fresh 8-call confirmation on the committed prompt reproduced it exactly:
# 4/8 spatially correct, P11 0/8, validated 8/8, six plates 8/8.
#
# `_baseline()` therefore now returns 2026-09-18.4, and `G0-baseline` is the
# committed prompt from here on.
#
# The 16 calls on .4 leave ONE failure mode, and it is precise. Comparing a
# success and a failure from the confirmation run:
#
#   success  left/right =  4 x 20 x 20  at x=0 and x=36   -- thickness on X
#   failure  left/right = 20 x 20 x  4  at x=0 and x=20   -- thickness on Z
#
# The end plates are not rotated. Written 20 x 20 x 4 they are two flat slabs
# lying in the floor, so the +X bore's centreline finds no material and the
# build fails E1. The permutation table already says `left, right  t by Y by
# Z`, but the end face here is SQUARE -- 20 by 20 -- and a square face reads
# like a size to copy down as given. It is the same degenerate case that
# defeated the envelope rule, appearing a second time in the permutation.

_TABLE = (
    "  bottom, top     X by Y by t\n"
    "  front, back     X by t by Z\n"
    "  left, right     t by Y by Z\n"
)

_TABLE_V2 = _TABLE + (
    "\n"
    "A SQUARE face does not change this. An end plate whose face is 70 by 70\n"
    "is still written `t` by 70 by 70, never 70 by 70 by `t`: the two equal\n"
    "numbers are the FACE, and the thickness is the third number, which goes\n"
    "on that face's own normal wherever the table puts it. A plate written\n"
    "with its thickness on the wrong axis is not a wall, it is a slab lying\n"
    "in the floor, and a bore across it will find no material.\n"
)


def _with_square_face_rule() -> str:
    text = _baseline()
    _require(text, _TABLE)
    return text.replace(_TABLE, _TABLE_V2, 1)


VARIANTS["H8-square-face"] = {
    "hypothesis": "HYPOTHESIS 8: the one failure left on 2026-09-18.4 is the "
                  "SQUARE end plate written 20x20xt instead of tx20x20. The "
                  "permutation table states the rule but a square face reads "
                  "like a size to copy. Saying that a square face changes "
                  "nothing should close it.",
    "changed": "adds a square-face note to the permutation table. Nothing "
               "else; the example, the envelope rule and the offset rule are "
               "untouched.",
    "text": _with_square_face_rule,
}
