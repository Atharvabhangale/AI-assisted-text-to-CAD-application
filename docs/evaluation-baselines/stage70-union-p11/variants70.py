"""Stage 70 prompt variants. Each is a pure function of the committed baseline.

A variant NEVER edits prompt.py. It returns a string; the arena patches
`system_prompt` for the life of one arm.

Baseline: `2026-09-18.5` (Stage 69), fingerprint 8563c6fb821e022f, 30917
characters.

WHAT IS BEING VARIED, and why only this.

Over 224 recorded EXPLICIT-request attempts (Stages 68 and 69) the P11
residual separates PERFECTLY on one thing, and it is not the target:

    union named `fuse`          P11   0/212
    union named anything else   P11  12/12     (enclosure 11, shell 1)

and no post-union target in any of those attempts ever named a consumed tool
or an id absent from the plan. Every wrong target is the union's OWN id. So
the model is not inventing a noun to target: it names the union after the
product, then targets the name it just wrote. **P11 on this request is an
id-naming failure, and the naming rule is the only thing worth varying.**

That contingency is correlational -- it is what the model did, over runs that
varied something else -- so these arms test it causally, one variable each:

  B1  DELETE the sentence that lists the forbidden product nouns, keeping
      the positive rule and the `fuse` mandate. The committed prompt writes
      `shell`, `box`, `enclosure` and `assembly` into a paragraph about
      naming, and then demonstrates naming a UNION `assembly`. Stages 65, 66
      and 69 each measured that removing the noun which invites the wrong
      answer moves the rate and that prose about the rule does not.
  B2  ADD a check restating that a post-union target is the union's own
      `target`. This introduces NO new noun, so it is the clean prose arm --
      the negative control the three prior stages predict will do nothing.
  B3  REORDER so the `fuse` mandate comes BEFORE the naming paragraph. A pure
      reordering: same characters, same length. Tests position alone.

B3 was expected to be inert and was the single largest effect of the stage --
P11 11/32 against a baseline of 8/144, p < 0.0001 -- so ORDER is a live
mechanism here, unlike Stage 69 where the same kind of control moved nothing.
That earns the symmetric test:

  B4  REORDER so the mandate comes LAST in the union section, after the
      layout block and its worked example. Also a pure reordering. If
      recency is what B3 disturbed, moving the mandate later should help; if
      it does not, recency is not the story and the current order is simply
      the one that works.
"""
from __future__ import annotations

import hashlib

from cad_experimental.prompt import system_prompt as _baseline

#: The baseline these arms were measured against.
MEASURED_AGAINST: str = "8563c6fb821e022f"


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _replace_once(text: str, anchor: str, replacement: str) -> str:
    if text.count(anchor) != 1:
        raise AssertionError(
            f"anchor appears {text.count(anchor)} times: {anchor[:60]!r}")
    return text.replace(anchor, replacement)


#: The sentence that names the four product nouns, verbatim.
FORBIDDEN_NOUNS = (
    " Do not name\n"
    "that piece for the finished product: `shell`, `box`, `enclosure` and\n"
    "`assembly` all read like names for the whole thing, and the whole thing is\n"
    "what the union makes, not what it starts from. That is not a convention, it is\n"
    "the rule: the union leaves its target's id behind and its own id names\n"
    "nothing, so `shell` is the only name the finished solid has. Naming the\n"
    "union `assembly` and then writing `\"target\": \"assembly\"` names a solid\n"
    "that does not exist."
)

#: What B1 keeps: the same rule, stated without naming a single product noun.
NO_NOUNS = (
    " Do not name\n"
    "that piece for the finished product. The union leaves its target's id\n"
    "behind and its own id names nothing, so the piece you chose is the only\n"
    "name the finished solid has."
)

#: The `fuse` mandate, verbatim.
FUSE_MANDATE = (
    "Name the union itself `fuse` -- that exact word, always. It is an action,\n"
    "not a thing, and that is the point: there is no solid called `fuse`, so a\n"
    "later operation cannot be tempted to target it. If you catch yourself\n"
    "writing `\"target\": \"fuse\"`, the answer you wanted is the union's own\n"
    "`target`."
)

#: B2's addition. Names no solid and no product noun: pure restatement.
FUSE_MANDATE_WITH_CHECK = FUSE_MANDATE + (
    " Before you write each operation that comes after the\n"
    "union, read its `target` back: it must be the very id you gave the union\n"
    "as ITS `target`. If it is the union's own id instead, it is wrong, and no\n"
    "solid by that name exists."
)

#: The whole of the layout block, from the mandate's end to the section end.
LAYOUT_TAIL_END = (
    "Read the sizes: only `base` and `lid` carry the 6 on Z. `front` and `back`\n"
    "carry it on Y, `left` and `right` on X. Those six numbers are this example's\n"
    "own -- take yours from the request, never from here."
)

CARRIER_RULE = (
    "Choose which piece will CARRY THE PART, make it the union's `target`, and\n"
    "give it the plain name of the piece it is -- so a hollow rectangular\n"
    "enclosure is six plates where the first is named `bottom`, the other five\n"
    "are fused into it, and every later operation targets `bottom`."
)


def _b1() -> str:
    base = _baseline()
    if FORBIDDEN_NOUNS not in base:      # adopted; nothing left to delete
        return base
    return _replace_once(base, FORBIDDEN_NOUNS, NO_NOUNS)


def _b2() -> str:
    base = _baseline()
    if FUSE_MANDATE_WITH_CHECK in base:
        return base
    return _replace_once(base, FUSE_MANDATE, FUSE_MANDATE_WITH_CHECK)


def _b3() -> str:
    """Pure reordering: the `fuse` mandate moves ahead of the naming rule."""
    base = _baseline()
    naming = CARRIER_RULE + FORBIDDEN_NOUNS
    block = naming + "\n\n" + FUSE_MANDATE
    swapped = FUSE_MANDATE + "\n\n" + naming
    if block not in base:                # the baseline already has it swapped
        return base
    return _replace_once(base, block, swapped)


def _b4() -> str:
    """Pure reordering: the `fuse` mandate moves to the end of the section."""
    base = _baseline()
    start = base.find(FUSE_MANDATE)
    if start < 0:
        return base
    end = base.find(LAYOUT_TAIL_END)
    if end < 0 or end < start:
        return base
    end += len(LAYOUT_TAIL_END)
    # cut the mandate plus its trailing blank line, re-insert before the
    # section's closing blank line. Nothing else is touched.
    block = base[start:end]
    assert block.startswith(FUSE_MANDATE)
    tail = block[len(FUSE_MANDATE) + 2:]          # skip the blank line
    return base[:start] + tail + "\n\n" + FUSE_MANDATE + base[end:]


VARIANTS: dict[str, dict] = {
    "B0-baseline": {
        "hypothesis": "control -- the committed prompt 2026-09-18.5, unchanged",
        "changed": "nothing",
        "text": _baseline,
    },
    "B1-no-product-nouns": {
        "hypothesis": "the prompt writes `shell`, `box`, `enclosure` and "
                      "`assembly` into a paragraph about naming and then shows "
                      "a union named `assembly`; removing the nouns removes "
                      "the affordance",
        "changed": "the forbidden-noun sentence deleted; the rule kept, stated "
                   "without naming any product noun",
        "text": _b1,
    },
    "B2-read-it-back": {
        "hypothesis": "NEGATIVE CONTROL: a stronger statement of the rule, "
                      "introducing no new noun. Stages 65, 66 and 69 each "
                      "measured that prose about the rule moves nothing",
        "changed": "one check appended to the `fuse` mandate",
        "text": _b2,
    },
    "B4-mandate-last": {
        "hypothesis": "B3 showed order is a live mechanism. If recency is what "
                      "it disturbed, moving the mandate to the END of the union "
                      "section -- after the layout block and its worked example "
                      "-- should help",
        "changed": "the position of the `fuse` mandate within its own section, "
                   "and nothing else",
        "text": _b4,
    },
    "B3-mandate-first": {
        "hypothesis": "CONTROL for position: the `fuse` mandate moved ahead of "
                      "the naming paragraph. A pure reordering, so a change "
                      "here is order and nothing else",
        "changed": "the order of two paragraphs, and nothing else",
        "text": _b3,
    },
}


def check() -> None:
    base = _baseline()
    base_fp = fingerprint(base)
    print(f"  committed prompt {base_fp[:16]} ({len(base)} chars)")
    seen: dict[str, str] = {}
    for name, spec in VARIANTS.items():
        text = spec["text"]()
        fp = fingerprint(text)
        if name == "B0-baseline":
            assert text == base, "the control must be byte-identical"
        else:
            adopted = fp == base_fp
            assert not adopted or name != "B0-baseline"
            if not adopted:
                assert fp not in seen.values(), f"{name} duplicates another arm"
        seen[name] = fp
        print(f"  {name:22s} {len(text):6d} chars  {fp[:16]}  "
              f"delta {len(text) - len(base):+d}"
              f"{'   [ADOPTED -- no-op against this baseline]' if fp == base_fp and name != 'B0-baseline' else ''}")
    for name, fn in (("B3", _b3), ("B4", _b4)):
        text = fn()
        if fingerprint(text) == base_fp:
            continue
        assert len(text) == len(base), f"{name} must not change the length"
        assert sorted(text) == sorted(base), f"{name} must be a pure reordering"
        print(f"  {name} verified as a PURE REORDERING "
              f"(same length, same characters)")
    b1 = _b1()
    if fingerprint(b1) != base_fp:
        assert FORBIDDEN_NOUNS not in b1, "B1 did not remove the sentence"
        section = b1.split("## union", 1)[1].split("## fillet", 1)[0]
        # `shell` and `assembly` appear NOWHERE else in this section; `box`
        # and `enclosure` do, legitimately ("an earlier box or cylinder",
        # "a hollow rectangular enclosure is six plates"), so only the two
        # that the sentence alone contributed are asserted gone.
        for noun in ("`shell`", "`assembly`"):
            assert noun not in section, \
                f"B1 still writes {noun} in the union section"
        assert "plain name of the piece it is" in section, \
            "B1 must keep the positive naming rule"
        assert FUSE_MANDATE in b1, "B1 must keep the `fuse` mandate"
        print("  B1 verified: the rule and the mandate kept, the product "
              "nouns gone")


if __name__ == "__main__":
    check()
