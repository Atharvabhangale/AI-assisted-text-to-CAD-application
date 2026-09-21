"""Prompt variants under test. Each is a pure function of the baseline.

A variant NEVER edits prompt.py. It returns a string, and the arena patches
`system_prompt` for the life of one arm. That keeps the committed prompt the
baseline until a variant has earned its place by measurement.
"""
from __future__ import annotations

import hashlib

import copy

from cad_experimental import plan as _plan
from cad_experimental.prompt import system_prompt as _baseline


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(text: str, anchor: str) -> None:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")


# --- the anchors both edits hang from -------------------------------------

_EXAMPLE_TAIL = (
    "A through_hole is the shorter way to say the same thing when the shape "
    "being\nremoved is a plain hole all the way through:"
)

_NAMING = (
    "Build the part in the order someone would actually make it, and give each\n"
    "operation an id that says what it is. Do not add an operation the description\n"
    "did not ask for."
)


# --- A1: show a union, with a hole after it ------------------------------

_UNION_EXAMPLE = """A part made of several pieces joined together follows the same shape, and the
same rule about ids. Two plates fused, then bored through:

  {"id": "base",  "type": "box", "parameters": {"x": 60, "y": 40, "z": 5}}
  {"id": "wall",  "type": "box", "parameters": {"x": 60, "y": 5, "z": 30,
                                              "position": {"x": 0, "y": 0,
                                                           "z": 5}}}
  {"id": "join",  "type": "union", "target": "base", "tools": ["wall"]}
  {"id": "bore",  "type": "through_hole", "target": "base",
   "parameters": {"diameter": 8, "position": {"x": 30, "y": 20, "z": 0}}}

Note what the last two do, because this is the mistake to avoid: `join`
targets `base` and consumes `wall`, so after `join` the only solid in the
part is `base`. `bore` therefore targets **`base`** -- NOT `join`. There is
no solid called `join`; a union leaves its target's id behind, exactly as a
subtract does. Writing `"target": "join"` names something that does not
exist and the plan is rejected.

"""


def _with_union_example() -> str:
    text = _baseline()
    _require(text, _EXAMPLE_TAIL)
    return text.replace(_EXAMPLE_TAIL, _UNION_EXAMPLE + _EXAMPLE_TAIL, 1)


# --- B1: name a modifier for its step, not its product -------------------

_NAMING_REPLACEMENT = """Build the part in the order someone would actually make it. Name a SOLID for
the thing it is -- `plate`, `base`, `shell`. Name a MODIFIER for the step it
performs -- `bore`, `join`, `break_edges` -- and never for the thing it
produces, because a modifier produces no new solid: it changes its target and
the target keeps its id. An id like `assembly` or `finished_part` on a union
invites you to write `"target": "assembly"` in the next operation, and there
is no such solid. Do not add an operation the description did not ask for."""


def _with_step_naming() -> str:
    text = _baseline()
    _require(text, _NAMING)
    return text.replace(_NAMING, _NAMING_REPLACEMENT, 1)


def _with_both() -> str:
    text = _with_union_example()
    _require(text, _NAMING)
    return text.replace(_NAMING, _NAMING_REPLACEMENT, 1)


VARIANTS = {
    "A0-baseline": {
        "hypothesis": "control -- the committed prompt, unchanged",
        "changed": "nothing",
        "text": _baseline,
    },
    "A1-worked-example": {
        "hypothesis": "HYPOTHESIS A: the prompt's only worked example uses "
                      "subtract, so the model has never been SHOWN a hole "
                      "after a union. Showing one fixes it.",
        "changed": "adds a union worked example with a through_hole after it, "
                   "targeting the union's TARGET, plus the note naming the "
                   "mistake. Nothing else.",
        "text": _with_union_example,
    },
    "B1-step-naming": {
        "hypothesis": "HYPOTHESIS B: the prompt says 'give each operation an "
                      "id that says what it is', which on a union produces a "
                      "product noun like `assembly` -- and an id that names "
                      "the product reads like the name of the resulting body. "
                      "Naming modifiers for their STEP removes the invitation.",
        "changed": "rewrites the one id-naming sentence. Adds no example.",
        "text": _with_step_naming,
    },
    "A1B1-both": {
        "hypothesis": "both changes together, in case each is necessary and "
                      "neither is sufficient",
        "changed": "the union worked example AND the step-naming rule",
        "text": _with_both,
    },
}


# --- C: say it in the SCHEMA, where the decoder reads it -----------------
#
# The measured gap: the provider schema carries ZERO descriptions. `id` and
# `target` are the same bare `$ref` to `identifier`, so at the moment the
# decoder writes a `target` the grammar tells it nothing at all, and the
# prompt's rule is 26,000 characters away. This puts the rule where the
# token is written.
#
# Cost, measured: `target` alone takes the encoding 3628 -> 4207, which is
# still under the largest size a live probe has ACCEPTED (4481). Annotating
# `id` as well reaches 4842, past the smallest measured REFUSAL (4551), so
# it is not attempted.

_TARGET_DESCRIPTION = (
    "The id of a LIVE SOLID. A modifier's id names no solid, so never name a "
    "subtract, union, fillet, chamfer, through_hole or pattern here -- name "
    "the solid it changed."
)


def _annotated_schema() -> dict:
    schema = copy.deepcopy(_plan.strict_selector_union_provider_schema())
    for branch in schema["properties"]["operations"]["items"]["anyOf"]:
        properties = branch["properties"]
        if "target" not in properties:
            continue
        existing = properties["target"]
        properties["target"] = (
            {"allOf": [existing], "description": _TARGET_DESCRIPTION}
            if "$ref" in existing
            else dict(existing, description=_TARGET_DESCRIPTION)
        )
    return schema


VARIANTS["C1-schema-target-description"] = {
    "hypothesis": "HYPOTHESIS C: the schema the decoder is constrained by "
                  "carries no descriptions at all, so `target` is written "
                  "with no nearby signal about what a target may be. Saying "
                  "it in the schema puts the rule where the token is.",
    "changed": "adds a `description` to every `target` in the provider "
               "schema. The PROMPT is the unmodified baseline. Nothing in "
               "the parser, validator, executor or backend changes.",
    "text": _baseline,
    "schema": _annotated_schema,
    "schema_name": "strict_selector_union + target description",
}

VARIANTS["C1B1-schema-and-naming"] = {
    "hypothesis": "the schema description plus the step-naming rule",
    "changed": "schema `target` description AND the id-naming sentence",
    "text": _with_step_naming,
    "schema": _annotated_schema,
    "schema_name": "strict_selector_union + target description",
}


# --- D: repair the CONTRADICTION inside the union section ----------------
#
# Found by re-reading the union section against the failing output. Its
# closing sentence reads:
#
#   "A hollow rectangular enclosure is six plates positioned to meet at
#    their edges and fused into one shell -- after which a `through_hole`
#    bores through the shell, not through a loose plate."
#
# That is almost verbatim the failing request, and it does three harmful
# things at once: it names the post-union solid with a fresh product noun
# ("the shell") that is not any operation's id, it targets the hole at that
# noun, and it EXPLICITLY WARNS AGAINST THE CORRECT ANSWER -- because
# `plate_long_1`, the union's target and the one legal target, is by name a
# loose plate.
#
# This is why A1 (adding a correct worked example) changed nothing: the
# example was arguing with a sentence that was left standing. "An operation
# is not one edit", again.
#
# The repair keeps the paragraph's real teaching -- fuse the pieces with one
# union -- and removes the contradiction, by making the surviving id
# something it is natural to name later: fuse INTO the piece that carries the
# part's name.

_CONTRADICTION = (
    "This is how a part made of several plates or blocks is built: create each\n"
    "piece as a `box` or `cylinder` where it belongs, then fuse them all with one\n"
    "union. A hollow rectangular enclosure is six plates positioned to meet at\n"
    "their edges and fused into one shell -- after which a `through_hole` bores\n"
    "through the shell, not through a loose plate."
)

_REPAIRED = (
    "This is how a part made of several plates or blocks is built: create each\n"
    "piece as a `box` or `cylinder` where it belongs, then fuse them all with one\n"
    "union.\n"
    "\n"
    "Choose which piece will CARRY THE PART, make it the union's `target`, and\n"
    "give it the part's own name -- so a hollow rectangular enclosure is six\n"
    "plates where the first is named `shell`, the other five are fused into it,\n"
    "and every later operation targets `shell`. That is not a convention, it is\n"
    "the rule: the union leaves its target's id behind and its own id names\n"
    "nothing, so `shell` is the only name the finished solid has. Naming the\n"
    "union `assembly` and then writing `\"target\": \"assembly\"` names a solid\n"
    "that does not exist."
)


def _with_repaired_union_section() -> str:
    text = _baseline()
    _require(text, _CONTRADICTION)
    return text.replace(_CONTRADICTION, _REPAIRED, 1)


def _with_everything() -> str:
    text = _with_repaired_union_section()
    _require(text, _EXAMPLE_TAIL)
    text = text.replace(_EXAMPLE_TAIL, _UNION_EXAMPLE + _EXAMPLE_TAIL, 1)
    _require(text, _NAMING)
    return text.replace(_NAMING, _NAMING_REPLACEMENT, 1)


VARIANTS["D1-repair-contradiction"] = {
    "hypothesis": "HYPOTHESIS D: the union section's own closing sentence "
                  "tells the model to bore 'through the shell, not through a "
                  "loose plate' -- naming a product noun that is no id and "
                  "warning against the one legal target. Repairing that "
                  "contradiction, and making the surviving id the part's "
                  "name, is the fix.",
    "changed": "rewrites ONE paragraph of the union section. No new example, "
               "no naming-rule change, no schema change.",
    "text": _with_repaired_union_section,
}

VARIANTS["D2-everything"] = {
    "hypothesis": "the repaired union section plus the worked example plus "
                  "the step-naming rule",
    "changed": "D1 + A1 + B1, prompt only",
    "text": _with_everything,
}


# --- E: stop the id from being nameable as a body ------------------------
#
# Thirty live calls of TELLING the model the rule failed: A1 (show a correct
# example) 0/5, B1 (change naming advice) 0/5, C1 (say it in the schema)
# 0/5, D1 (repair the contradicting sentence) 1/5 built but 0/5 correct, D2
# (all of them) 0/5. The rule is stated five separate times in the prompt and
# was violated on every attempt.
#
# So this arm stops describing and instead removes the affordance: it
# MANDATES a specific verb id for the union. `"target": "fuse"` does not read
# as a body the way `"target": "assembly"` does. Built on D1's repair,
# because D1 produced the only build of the whole investigation.

_FUSE_MANDATE = (
    "\n\nName the union itself `fuse` -- that exact word, always. It is an "
    "action,\nnot a thing, and that is the point: there is no solid called "
    "`fuse`, so a\nlater operation cannot be tempted to target it. If you "
    "catch yourself\nwriting `\"target\": \"fuse\"`, the answer you wanted "
    "is the union's own\n`target`."
)


def _with_fuse_mandate() -> str:
    text = _with_repaired_union_section()
    _require(text, _REPAIRED)
    return text.replace(_REPAIRED, _REPAIRED + _FUSE_MANDATE, 1)


VARIANTS["E1-mandate-verb-id"] = {
    "hypothesis": "HYPOTHESIS E: telling has failed 30/30. Remove the "
                  "affordance instead -- mandate the union's id be the verb "
                  "`fuse`, which does not read as a body.",
    "changed": "D1's repaired union paragraph plus a mandated union id. "
               "Prompt only.",
    "text": _with_fuse_mandate,
}

# --- diagnostic: is it the RULE or the COMPLEXITY? -----------------------
#
# Not a fix. Two plates and one hole, on the unmodified baseline prompt. If
# the model gets this right, the rule is understood and six plates is a
# complexity failure; if it fails this too, the rule itself is not reaching
# generation. Either answer changes what to do next, which is what makes it
# worth five calls.

VARIANTS["X1-two-plate-diagnostic"] = {
    "hypothesis": "DIAGNOSTIC, not a fix: does the model handle the SIMPLEST "
                  "possible post-union hole on the baseline prompt?",
    "changed": "nothing -- baseline prompt, baseline schema, simpler request",
    "text": _baseline,
    "request": ("Make a bracket from two plates: a 60 x 40 x 5 mm base plate, "
                "and a 60 x 5 x 30 mm wall standing on one long edge of it, "
                "joined into one solid, with an 8 mm hole through the centre "
                "of the base."),
    "expected_envelope": [60.0, 40.0, 35.0],
}


_BRACKET = ("Make a bracket from two plates: a 60 x 40 x 5 mm base plate, "
            "and a 60 x 5 x 30 mm wall standing on one long edge of it, "
            "joined into one solid, with an 8 mm hole through the centre "
            "of the base.")

VARIANTS["X1-two-plate-diagnostic"]["request"] = _BRACKET

VARIANTS["E1-simple-request"] = {
    "hypothesis": "E1's fix on a request whose GEOMETRY is simple. The "
                  "six-plate hollow box now fails on rule E1 (a hole misses "
                  "material) rather than on the union-target rule, which is "
                  "a different and harder problem. This isolates the rule "
                  "from the arrangement.",
    "changed": "E1's prompt, simpler request",
    "text": _with_fuse_mandate,
    "request": _BRACKET,
    "expected_envelope": [60.0, 40.0, 35.0],
}
