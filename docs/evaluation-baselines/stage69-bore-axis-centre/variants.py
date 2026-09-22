"""Stage 69 prompt variants. Each is a pure function of the committed baseline.

A variant NEVER edits prompt.py. It returns a string; the arena patches
`system_prompt` for the life of one arm. The committed prompt stays the
baseline until a variant has earned its place by measurement.

Baseline AT MEASUREMENT TIME: `2026-09-18.3` (Stage 66), fingerprint
c78aaad8eacf365e, 30481 chars. **A2 was adopted afterwards**, so against the
committed prompt today `_a2()` is a no-op and the live baseline is A2's own
text (`2026-09-18.5`, 8563c6fb821e022f). `check()` says which world it is in
rather than asserting a difference that adoption removed.

WHAT IS BEING VARIED, and why only this.

Phase 2 measured the residual over 32 pooled explicit attempts (96 bores,
192 across-axis components). Every wrong component is the SAME one:

    X bore, Y component   0/32        X bore, Z component   3/32
    Y bore, X component   0/32        Y bore, Z component   2/32
    Z bore, X component   0/32        Z bore, Y component   0/32

The model does not reuse the +Z triple (0/24 in this stage's baseline); it
zeroes **z**, and only z, on bores that do not run along Z. The along-axis
component is written as 0 on 94/96 bores, so the model has the "0 is safe
along the axis" half of the rule and over-applies it to the letter z.

The rule itself is already stated, correctly and twice, in the committed
prompt. Stages 65 and 66 both measured that PROSE ABOUT A RULE MOVES NOTHING
and that removing the affordance that invites the wrong answer moves it. So
the arms test affordances, not wording strength:

  A1  the table never pairs a LETTER with "may be 0"          (a deletion)
  A2  a worked example whose bore positions carry a NONZERO z (an example)
  A3  the same table, reordered so +X comes first             (a control)

A3 is the discriminator. If the defect is positional -- the first line a
reader meets pairs z with a zero -- reordering moves the error onto x. If the
error stays on z, the mechanism is the examples, not the table.
"""
from __future__ import annotations

import hashlib

from cad_experimental.prompt import system_prompt as _baseline


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: The baseline these arms were measured against, and the text A2 became.
MEASURED_AGAINST: str = "c78aaad8eacf365e"
ADOPTED: str = "8563c6fb821e022f"


def _replace_once(text: str, anchor: str, replacement: str) -> str:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


# The committed table, verbatim.
TABLE = (
    "              +Z hole -- x and y matter, z may be 0\n"
    "              +Y hole -- x and z matter, y may be 0\n"
    "              +X hole -- y and z matter, x may be 0\n"
    "            0 is only ever safe for the component along the axis.\n"
)

#: A1 -- the same three facts with no letter attached to a zero.
TABLE_NO_LETTER = (
    "              +Z hole -- x and y decide where it is\n"
    "              +Y hole -- x and z decide where it is\n"
    "              +X hole -- y and z decide where it is\n"
    "            The component along `axis` is the only one that may be 0.\n"
)

#: A3 -- byte-identical lines, reversed order. The control.
TABLE_REORDERED = (
    "              +X hole -- y and z matter, x may be 0\n"
    "              +Y hole -- x and z matter, y may be 0\n"
    "              +Z hole -- x and y matter, z may be 0\n"
    "            0 is only ever safe for the component along the axis.\n"
)

#: A2 -- a worked example in the prompt's OWN 60 x 30 x 30 numbers, never the
#: golden request's, so nothing here teaches to the test. Two of its three
#: bores carry a nonzero z, which no example in the committed prompt does.
CENTRE_ANCHOR = (
    "Through the centre of a\n"
    "            part means the MIDDLE of each of the other two extents.\n"
)
CENTRE_WITH_EXAMPLE = CENTRE_ANCHOR + (
    "            Through the centre of a 60 by 30 by 30 part, the three\n"
    "            bores are written:\n"
    '              +Z -- {"x": 30, "y": 15, "z": 0}\n'
    '              +Y -- {"x": 30, "y": 0,  "z": 15}\n'
    '              +X -- {"x": 0,  "y": 15, "z": 15}\n'
    "            Only the +Z bore has z at 0. The other two carry z at\n"
    "            the middle of the height, because for them z is one of\n"
    "            the two components that decide where the hole is.\n"
)


def _a1() -> str:
    return _replace_once(_baseline(), TABLE, TABLE_NO_LETTER)


def _a2() -> str:
    base = _baseline()
    if CENTRE_WITH_EXAMPLE in base:
        # A2 was adopted; the substitution has nothing left to do, and
        # inserting a second copy of the example would not be A2.
        return base
    return _replace_once(base, CENTRE_ANCHOR, CENTRE_WITH_EXAMPLE)


def _a3() -> str:
    return _replace_once(_baseline(), TABLE, TABLE_REORDERED)


VARIANTS: dict[str, dict] = {
    "A0-baseline": {
        "hypothesis": "control -- the committed prompt 2026-09-18.3, unchanged",
        "changed": "nothing",
        "text": _baseline,
    },
    "A1-no-letter": {
        "hypothesis": "the table pairs the letter z with 'may be 0'; removing "
                      "every letter-to-zero pairing removes the affordance",
        "changed": "the three-line axis table, rewritten; nothing added",
        "text": _a1,
    },
    "A2-worked-triples": {
        "hypothesis": "every hole position the prompt SHOWS has z at 0, "
                      "because every example bore runs along +Z; an example "
                      "carrying a nonzero z is what the model would imitate",
        "changed": "one worked example added, in the prompt's own 60x30x30 "
                   "numbers",
        "text": _a2,
    },
    "A3-order": {
        "hypothesis": "CONTROL for position, not a candidate fix: the same "
                      "three lines reordered so +X is first. If the defect is "
                      "primacy the error moves onto x; if it stays on z, the "
                      "table is not the mechanism",
        "changed": "the order of the three table lines, and nothing else",
        "text": _a3,
    },
}


def check() -> None:
    """Offline: every variant still builds, and the arms stay distinct."""
    base = _baseline()
    base_fp = fingerprint(base)
    adopted = base_fp.startswith(ADOPTED)
    print(f"  committed prompt {base_fp[:16]} "
          f"({'A2 ADOPTED' if adopted else 'pre-adoption baseline'})")
    seen: dict[str, str] = {}
    for name, spec in VARIANTS.items():
        text = spec["text"]()
        fp = fingerprint(text)
        if name == "A0-baseline":
            assert text == base, "the control must be byte-identical"
        elif name == "A2-worked-triples" and adopted:
            # A2 IS the committed prompt now; the substitution has nothing
            # left to substitute, and that is the correct post-adoption state.
            assert text == base, "A2 should now equal the committed prompt"
        else:
            assert text != base, f"{name} changed nothing"
        assert fp not in seen.values() or name == "A2-worked-triples", \
            f"{name} duplicates another arm"
        seen[name] = fp
        print(f"  {name:20s} {len(text):6d} chars  {fp[:16]}  "
              f"delta {len(text) - len(base):+d}")
    # A3 must be a pure reordering of whatever baseline it is applied to.
    a3 = _a3()
    assert len(a3) == len(base), "A3 must not change the length"
    assert sorted(a3) == sorted(base), "A3 must be a pure reordering"
    print("  A3 verified as a PURE REORDERING (same length, same characters)")


if __name__ == "__main__":
    check()
