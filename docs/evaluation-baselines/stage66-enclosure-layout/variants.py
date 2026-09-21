"""Stage 66 prompt variants. Each is a pure function of the committed baseline.

A variant NEVER edits prompt.py. It returns a string, and the arena patches
`system_prompt` for the life of one arm. The committed prompt stays the
baseline until a variant has earned its place by measurement.

The baseline here is `2026-09-18.2`, the prompt Stage 65 earned. Stage 65's
arms are NOT re-run and NOT edited; `arena.py` in this directory is a
byte-for-byte copy of Stage 65's, so the instrument is the same one.

WHAT IS BEING MEASURED
----------------------
Stage 65 removed the P11 union-target failure on the golden six-plate
request (5/5 -> 1/5) and the failures moved to rule E1 -- "the hole's
centreline does not intersect the target". Reading the five recorded plans
shows two defects, each present on 5/5 attempts:

1. THE PLATES ARE NEVER ROTATED. Every attempt writes all six plates with
   the thickness on Z (`40 x 20 x 5`), so they stack into slabs instead of
   standing on six faces. A shell needs `40 x 5 x 20` and `5 x 20 x 20` --
   the same three numbers, permuted.

2. SIX HOLES INSTEAD OF THREE. Every attempt writes one hole per plate. A
   through_hole goes all the way through, so one hole along an axis already
   bores both walls it meets.

The prompt names "six plates" and supplies NO layout arithmetic at all.
That is the same gap this project already closed once, on the stable
branch: a locative is an answer, and the prompt has to give the arithmetic.
"""
from __future__ import annotations

import hashlib

from cad_experimental.prompt import system_prompt as _baseline


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(text: str, anchor: str) -> None:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")


# --- the anchors both edits hang from -------------------------------------

_UNION_TAIL = (
    "Name the union itself `fuse` -- that exact word, always. It is an "
    "action,\nnot a thing, and that is the point: there is no solid called "
    "`fuse`, so a\nlater operation cannot be tempted to target it. If you "
    "catch yourself\nwriting `\"target\": \"fuse\"`, the answer you wanted "
    "is the union's own\n`target`."
)

_HOLE_TAIL = (
    "A hole always targets the SOLID it cuts, never another hole. Four holes "
    "in one\nplate all use the same `target` -- the plate's id. Drilling into "
    "a hole is not\na thing, and a plan that does it is invalid."
)


# --- F1: the arithmetic an enclosure needs -------------------------------
#
# Deliberately worked in DIFFERENT numbers from the request being measured
# (60 x 30 x 30 from 4 mm plate, against a 40 x 20 x 20 from 5 mm plate), so
# the arm measures whether the RULE generalises rather than whether the model
# can copy an example. Teaching to the test would make the number meaningless.

_LAYOUT = """

Laying out an enclosure, or any part whose pieces sit on different faces:

A plate has a THICKNESS and two other dimensions, and the thickness runs
along the NORMAL of the face that plate lies on. One plate size used on six
faces is therefore written three different ways -- the same numbers PERMUTE.
For a box of outer size X by Y by Z built from plate of thickness t:

  bottom, top     X by Y by t
  front, back     X by t by Z
  left, right     t by Y by Z

`position` is the piece's MINIMUM corner, so a facing pair sits at 0 and at
(extent - t) along its own axis, and at 0 on the other two. Writing all six
plates with the thickness on Z stacks six slabs on top of one another. That
is not an enclosure, and a hole through it will not find the walls.

A 60 by 30 by 30 enclosure from 4 mm plate is exactly this:

  {"id": "shell", "type": "box", "parameters": {"x": 60, "y": 30, "z": 4}}
  {"id": "lid",   "type": "box", "parameters": {"x": 60, "y": 30, "z": 4,
                                    "position": {"x": 0, "y": 0, "z": 26}}}
  {"id": "front", "type": "box", "parameters": {"x": 60, "y": 4, "z": 30}}
  {"id": "back",  "type": "box", "parameters": {"x": 60, "y": 4, "z": 30,
                                    "position": {"x": 0, "y": 26, "z": 0}}}
  {"id": "left",  "type": "box", "parameters": {"x": 4, "y": 30, "z": 30}}
  {"id": "right", "type": "box", "parameters": {"x": 4, "y": 30, "z": 30,
                                    "position": {"x": 56, "y": 0, "z": 0}}}
  {"id": "fuse",  "type": "union", "target": "shell",
   "tools": ["lid", "front", "back", "left", "right"]}

Read the sizes: only `shell` and `lid` carry the 4 on Z. `front` and `back`
carry it on Y, `left` and `right` on X. The outer size is 60 by 30 by 30 --
the plates are the walls of that box, not layers stacked inside it."""


def _with_layout() -> str:
    text = _baseline()
    _require(text, _UNION_TAIL)
    return text.replace(_UNION_TAIL, _UNION_TAIL + _LAYOUT, 1)


# --- F2: one hole per axis, not one per plate ----------------------------

_ONE_PER_AXIS = """

Because the cut goes all the way through, ONE through_hole bores every wall
that lies on its centreline -- the near one and the far one. A hollow box
with a hole through the centre of each of its six plates is THREE
operations, not six: one along +X, one along +Y, one along +Z. Each of them
opens two facing plates at once.

A second hole on the same centreline has nothing left to remove, and the
engine refuses a cut that removes nothing. Count the LINES a hole is drilled
along, not the walls it passes through."""


def _with_one_per_axis() -> str:
    text = _baseline()
    _require(text, _HOLE_TAIL)
    return text.replace(_HOLE_TAIL, _HOLE_TAIL + _ONE_PER_AXIS, 1)


def _with_both() -> str:
    text = _with_layout()
    _require(text, _HOLE_TAIL)
    return text.replace(_HOLE_TAIL, _HOLE_TAIL + _ONE_PER_AXIS, 1)


VARIANTS = {
    "F0-baseline": {
        "hypothesis": "control -- the committed prompt 2026-09-18.2, "
                      "unchanged. Stage 65 measured this at 0/5 built; this "
                      "arm re-anchors that under THIS stage's run.",
        "changed": "nothing",
        "text": _baseline,
    },
    "F1-enclosure-arithmetic": {
        "hypothesis": "HYPOTHESIS F1: the prompt says an enclosure is six "
                      "plates and never says WHERE they go. The model keeps "
                      "the thickness on Z for all six and stacks slabs. "
                      "Giving the permutation rule and the facing-pair "
                      "arithmetic fixes the envelope.",
        "changed": "appends a layout section to `union`. The worked example "
                   "uses DIFFERENT numbers from the request under test, so "
                   "this measures the rule generalising, not copying.",
        "text": _with_layout,
    },
    "F2-one-hole-per-axis": {
        "hypothesis": "HYPOTHESIS F2: the model writes one hole per plate "
                      "because nothing says a through_hole already opens "
                      "both walls on its line. Saying so turns six holes "
                      "into three.",
        "changed": "appends two paragraphs to `through_hole`. No layout "
                   "change.",
        "text": _with_one_per_axis,
    },
    "F3-both": {
        "hypothesis": "both defects are present on 5/5 attempts and each "
                      "alone is expected to leave the build failing, so this "
                      "is the arm that can actually build. F1 and F2 are its "
                      "controls, not redundant runs.",
        "changed": "F1 + F2, prompt only",
        "text": _with_both,
    },
}


# =========================================================================
# Second round. Three things were MEASURED in the first 20 calls, and each
# one changes the next arm:
#
# 1. F1's permutation rule WORKED: the plates were rotated correctly on
#    every attempt (40x20x4 / 40x4x25 / 4x20x25) where the baseline had put
#    the thickness on Z 5/5. The rule generalised from an example in
#    different numbers.
#
# 2. F1's example LEAKED ITS THICKNESS. It said "4 mm plate", and the
#    request says "(4)plates" -- where 4 is a COUNT. Every F1 attempt built
#    plates 4 thick instead of 5. An example's numbers are read as data, so
#    they must not collide with the request's. Thickness is now 6, which
#    appears nowhere in the request (40, 20, 5, 4, 2, 8).
#
# 3. THE OUTER SIZE IS BEING INVENTED. F1 produced heights of 25, 20 and 5
#    on three attempts. Nothing in the prompt says the plate faces GIVE the
#    envelope, so the model picks one.
#
# And the sharpest finding of the round, across all 20 calls:
#
#    union id `shell` -> the holes target `shell`  -- P11, 8 times out of 8
#    union id `fuse`  -> the target is right        -- 8 times out of 11
#
# The prompt hands the model that noun itself: its enclosure sentence reads
# "six plates where the first is named `shell`". On a hollow-enclosure
# request the model gives `shell` to the union rather than to the carrying
# plate, and then targets it. That is Stage 65's defect surviving in the
# one sentence that names an enclosure -- and F2 made it worse by opening
# with "A hollow box with a hole through..." next to the hole rule.

_LAYOUT_V2 = """

Laying out an enclosure, or any part whose pieces sit on different faces:

A plate has a THICKNESS and two other dimensions, and the thickness runs
along the NORMAL of the face that plate lies on. One plate size used on six
faces is therefore written three different ways -- the same numbers PERMUTE.
For a box of outer size X by Y by Z built from plate of thickness t:

  bottom, top     X by Y by t
  front, back     X by t by Z
  left, right     t by Y by Z

`position` is the piece's MINIMUM corner, so a facing pair sits at 0 and at
(extent - t) along its own axis, and at 0 on the other two. Writing all six
plates with the thickness on Z stacks six slabs on top of one another. That
is not an enclosure, and a hole through it will not find the walls.

The plates also GIVE the outer size -- do not choose one. A plate's two
non-thickness dimensions are the face it covers, and that face is a face of
the box. Plates of 90 by 70 for the bottom and the top, and 70 by 50 for the
ends, describe a box 90 by 70 by 50: the 90 and the 70 from the bottom, and
the 50 from the end, which is the only number the bottom did not already
give. If a height seems to be missing, it is in one of the other plates --
read it off, do not invent it.

A 60 by 30 by 30 enclosure from 6 mm plate is exactly this:

  {"id": "base",  "type": "box", "parameters": {"x": 60, "y": 30, "z": 6}}
  {"id": "lid",   "type": "box", "parameters": {"x": 60, "y": 30, "z": 6,
                                    "position": {"x": 0, "y": 0, "z": 24}}}
  {"id": "front", "type": "box", "parameters": {"x": 60, "y": 6, "z": 30}}
  {"id": "back",  "type": "box", "parameters": {"x": 60, "y": 6, "z": 30,
                                    "position": {"x": 0, "y": 24, "z": 0}}}
  {"id": "left",  "type": "box", "parameters": {"x": 6, "y": 30, "z": 30}}
  {"id": "right", "type": "box", "parameters": {"x": 6, "y": 30, "z": 30,
                                    "position": {"x": 54, "y": 0, "z": 0}}}
  {"id": "fuse",  "type": "union", "target": "base",
   "tools": ["lid", "front", "back", "left", "right"]}

Read the sizes: only `base` and `lid` carry the 6 on Z. `front` and `back`
carry it on Y, `left` and `right` on X. Those six numbers are this example's
own -- take yours from the request, never from here."""


def _with_layout_v2() -> str:
    text = _baseline()
    _require(text, _UNION_TAIL)
    return text.replace(_UNION_TAIL, _UNION_TAIL + _LAYOUT_V2, 1)


# --- the hole rule again, with the product noun removed ------------------

_ONE_PER_AXIS_V2 = """

Because the cut goes all the way through, ONE through_hole opens every wall
that lies on its centreline -- the near wall and the far wall. Boring
through the centre of all six walls of a closed box is THREE operations, not
six: one along +X, one along +Y, one along +Z, each opening a facing pair.

A second hole on the same centreline has nothing left to remove, and the
engine refuses a cut that removes nothing. Count the LINES drilled, not the
walls crossed."""


# --- and the noun the prompt hands over ----------------------------------

_SHELL_SENTENCE = (
    "give it the part's own name -- so a hollow rectangular enclosure is six\n"
    "plates where the first is named `shell`, the other five are fused into "
    "it,\nand every later operation targets `shell`."
)

_PLATE_SENTENCE = (
    "give it the plain name of the piece it is -- so a hollow rectangular\n"
    "enclosure is six plates where the first is named `bottom`, the other "
    "five\nare fused into it, and every later operation targets `bottom`. "
    "Do not name\nthat piece for the finished product: `shell`, `box`, "
    "`enclosure` and\n`assembly` all read like names for the whole thing, "
    "and the whole thing is\nwhat the union makes, not what it starts from."
)


def _with_plate_carrier() -> str:
    text = _baseline()
    _require(text, _SHELL_SENTENCE)
    return text.replace(_SHELL_SENTENCE, _PLATE_SENTENCE, 1)


def _with_everything() -> str:
    text = _with_layout_v2()
    _require(text, _HOLE_TAIL)
    text = text.replace(_HOLE_TAIL, _HOLE_TAIL + _ONE_PER_AXIS_V2, 1)
    _require(text, _SHELL_SENTENCE)
    return text.replace(_SHELL_SENTENCE, _PLATE_SENTENCE, 1)


VARIANTS["F4-layout-corrected"] = {
    "hypothesis": "F1's permutation rule worked but its example leaked the "
                  "thickness 4 into a request whose 4 is a COUNT, and "
                  "nothing told the model the plate faces GIVE the outer "
                  "size. Fix both and the envelope should come out right.",
    "changed": "F1's layout section with a non-colliding thickness (6) and "
               "an envelope-derivation rule. Prompt only.",
    "text": _with_layout_v2,
}

VARIANTS["F5-carrier-is-a-plate"] = {
    "hypothesis": "MEASURED over 20 calls: union id `shell` -> P11 8/8; "
                  "union id `fuse` -> right 8/11. The prompt supplies the "
                  "noun, in the one sentence that describes an enclosure. "
                  "Naming the carrier for the plate it is should remove it.",
    "changed": "rewrites that ONE sentence. No layout change, no hole "
               "change.",
    "text": _with_plate_carrier,
}

VARIANTS["F6-all-three"] = {
    "hypothesis": "the corrected layout, the de-nouned hole rule and the "
                  "plate carrier together -- the arm that can actually "
                  "build. F4 and F5 are its controls.",
    "changed": "F4 + F2-corrected + F5, prompt only",
    "text": _with_everything,
}


# =========================================================================
# Third round. F6 BUILT the golden request once -- the first time the live
# model has ever produced this part -- and reading all five attempts names
# the two defects that are left, both in the same place.
#
# 1. SIX HOLES, NOT THREE, on 3 of 5 attempts. The rule is stated and
#    ignored, and the reason is visible in the ids: the model writes
#    `hole_front` AND `hole_back`, two names for two walls that share one
#    centreline. The one attempt that named them `hole_x`/`hole_y`/`hole_z`
#    wrote three and built. The NAMING drives the count, exactly as the
#    union's id drove its target -- so name the hole for its LINE.
#
# 2. `z: 0` ON +X AND +Y HOLES, on the attempt that did build. The prompt
#    illustrates the convention for one axis only -- "for a +Z hole only x
#    and y matter, and z is conventionally 0" -- and the model carries "z is
#    0" to every axis. For a +Y hole that drops the centreline into the
#    bottom face plane, which grazes instead of boring. That is why the one
#    build came out at 9843.95 mm3 with 31 faces: a valid, one-solid
#    enclosure of exactly the right envelope that is NOT the part asked for.

_POSITION_BULLET = (
    '  position  REQUIRED, {"x": n, "y": n, "z": n}. A point on the hole\'s\n'
    "            centreline. Because the cut goes all the way through, the\n"
    "            component along `axis` has no effect: for a +Z hole only x "
    "and y\n            matter, and z is conventionally 0."
)

_POSITION_BULLET_V2 = (
    '  position  REQUIRED, {"x": n, "y": n, "z": n}. A point on the hole\'s\n'
    "            centreline. The component ALONG `axis` has no effect,\n"
    "            because the cut goes all the way through. The OTHER TWO\n"
    "            decide where the hole is, and they must put the centreline\n"
    "            INSIDE the material:\n"
    "              +Z hole -- x and y matter, z may be 0\n"
    "              +Y hole -- x and z matter, y may be 0\n"
    "              +X hole -- y and z matter, x may be 0\n"
    "            0 is only ever safe for the component along the axis.\n"
    "            Using it for one of the other two puts the centreline on\n"
    "            the outside face of the part, where the cut grazes the\n"
    "            surface or misses it altogether. Through the centre of a\n"
    "            part means the MIDDLE of each of the other two extents."
)

_NAME_THE_LINE = """

Name a hole for the LINE it is drilled along, not for a wall it opens:
`hole_x`, `hole_y`, `hole_z`. Writing `hole_front` and `hole_back` is how
one hole becomes two operations on the same centreline, and the second has
nothing left to remove."""


def _with_hole_geometry() -> str:
    text = _with_everything()
    _require(text, _POSITION_BULLET)
    text = text.replace(_POSITION_BULLET, _POSITION_BULLET_V2, 1)
    _require(text, _ONE_PER_AXIS_V2)
    return text.replace(_ONE_PER_AXIS_V2, _ONE_PER_AXIS_V2 + _NAME_THE_LINE, 1)


VARIANTS["F7-name-the-line"] = {
    "hypothesis": "F6 built 1/5. Its two remaining defects are both in the "
                  "through_hole section: holes named for WALLS come in "
                  "pairs on one centreline, and `z is conventionally 0` is "
                  "stated for +Z only and carried to every axis. Name the "
                  "hole for its line, and state the convention for all "
                  "three axes.",
    "changed": "F6 plus a rewritten `position` bullet and a naming rule. "
               "Prompt only.",
    "text": _with_hole_geometry,
}
