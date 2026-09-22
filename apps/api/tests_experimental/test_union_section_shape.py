"""What a post-union target can name, and the prompt shape that was measured.

Stage 70 measured the P11 residual on the EXPLICIT golden request over 288
live calls to `claude-haiku-4-5-20251001`, five arms, one variable each. The
record is `docs/evaluation-baselines/stage70-union-p11/`. **No prompt change
was adopted** -- an adoption rule fixed in code before the confirming runs
rejected all four candidates -- so what the stage leaves behind is this
module and a bounded number.

1. THE TARGET IS NEVER INVENTED, and the failure has exactly one shape.
   Over 144 baseline attempts (432 post-union targets) every target named
   either the surviving body or the union's OWN id. Not once did the model
   name a consumed tool, and not once an id absent from the plan. Stage 69
   recorded this as "every P11 attempt targets the product noun `enclosure`";
   `enclosure` is what the model NAMED THE UNION, so that is a token-level
   description of "targets the union's own id". The tests below say it
   relationally, so no future noun can make the description wrong again.

2. THE UNION SECTION'S ORDER IS LOAD-BEARING, and that was the stage's one
   significant result. Arm B3 moved the `fuse` mandate AHEAD of the
   carrier-naming paragraph -- a pure reordering, same length, same
   characters -- and P11 went from 8/144 to 11/32 with strict success
   135/144 -> 21/32 (Fisher exact, two sided, p = 0.0001). The committed
   order is not cosmetic.

3. THE FORBIDDEN-NOUN SENTENCE EARNS ITS PLACE. Arm B1 deleted it, keeping
   the positive rule and the `fuse` mandate. Union-id compliance went to
   96/96 -- and P11 went UP, to 7/96, every one of them on a union correctly
   named `fuse`. Removing the explanation of why the union's id names no
   solid bought the id and lost the semantics.

4. THE UNION ID IS A MARKER, NOT THE CAUSE. On the committed prompt the
   separation is perfect: P11 is 0/136 when the union is named `fuse` and
   8/8 when it is not. It is tempting to read that as "make it say `fuse`
   and P11 goes away", and B1 and B4 both falsify it -- each reached 96/96
   compliance and still carried P11 at 7/96 and 3/96. A perfect correlation
   over one prompt is not a mechanism.

MEASURED RESIDUAL, committed prompt `2026-09-18.5`, n = 144 fresh explicit
calls: P11 **8/144 = 5.6%**, 95% Clopper-Pearson CI **[2.4%, 10.7%]**;
strict success **135/144 = 93.8%**, CI [88.5%, 97.1%].
"""

from __future__ import annotations

import json
import unittest

from cad_experimental.parser import parse_plan_text
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

#: The union id the prompt mandates, READ FROM THE PROMPT so this module
#: cannot disagree with what the model is told.
MANDATE_MARKER = "Name the union itself `"


def mandated_union_id(text: str) -> str:
    return text.split(MANDATE_MARKER, 1)[1].split("`", 1)[0]


def two_block_plan(target: str) -> dict:
    """Two overlapping blocks, fused, then bored -- the smallest union case."""
    return {"status": "generated", "operations": [
        {"id": "a", "type": "box", "parameters": {"x": 10, "y": 10, "z": 10}},
        {"id": "b", "type": "box",
         "parameters": {"x": 10, "y": 10, "z": 10,
                        "position": {"x": 5, "y": 0, "z": 0}}},
        {"id": "fuse", "type": "union", "target": "a", "tools": ["b"]},
        {"id": "h", "type": "through_hole", "target": target,
         "parameters": {"diameter": 2, "axis": "+Z",
                        "position": {"x": 5, "y": 5, "z": 0}}},
    ]}


def verdict_for(target: str):
    return validate_plan(parse_plan_text(json.dumps(two_block_plan(target))))


class EveryTargetRoleGetsItsOwnVerdictTests(unittest.TestCase):
    """The five roles a post-union target can name, each decided by the
    validator rather than by a list written here."""

    def test_the_surviving_body_is_the_only_valid_answer(self) -> None:
        self.assertTrue(verdict_for("a").valid)

    def test_the_unions_own_id_is_P11(self) -> None:
        verdict = verdict_for("fuse")
        self.assertFalse(verdict.valid)
        self.assertEqual(sorted({p.code for p in verdict.problems}), ["P11"])

    def test_a_consumed_tool_is_P12_and_not_P11(self) -> None:
        """A different fact, and the codes must stay different.

        The model has never made this mistake in 288 measured calls, which is
        precisely why the code has to keep meaning what it means: if P12
        collapsed into P11 the measurement would stop being able to tell
        'named the union' from 'named something the union ate'.
        """
        verdict = verdict_for("b")
        self.assertFalse(verdict.valid)
        self.assertEqual(sorted({p.code for p in verdict.problems}), ["P12"])

    def test_an_id_absent_from_the_plan_is_neither(self) -> None:
        verdict = verdict_for("nowhere")
        self.assertFalse(verdict.valid)
        codes = {p.code for p in verdict.problems}
        self.assertNotIn("P11", codes)
        self.assertNotIn("P12", codes)

    def test_the_four_roles_do_not_share_a_verdict(self) -> None:
        """Derived from the validator, so it cannot go stale silently."""
        outcomes = {}
        for target in ("a", "fuse", "b", "nowhere"):
            verdict = verdict_for(target)
            outcomes[target] = (verdict.valid,
                                tuple(sorted({p.code for p in verdict.problems})))
        self.assertEqual(len(set(outcomes.values())), 4, outcomes)


class TheUnionSectionShapeThatWasMeasuredTests(unittest.TestCase):
    """The prompt structure Stage 70 measured, pinned where it was measured."""

    def setUp(self) -> None:
        self.text = system_prompt()
        self.section = self.text.split("## union", 1)[1].split("## fillet", 1)[0]

    def test_the_mandate_names_a_verb(self) -> None:
        self.assertEqual(mandated_union_id(self.text), "fuse")

    def test_the_naming_rule_comes_BEFORE_the_mandate(self) -> None:
        """Arm B3 reversed exactly this and regressed, p = 0.0001.

        A pure reordering -- same length, same characters -- took P11 from
        8/144 to 11/32 and strict success from 135/144 to 21/32. The order is
        not cosmetic, so it is pinned.
        """
        naming = self.section.index("Choose which piece will CARRY THE PART")
        mandate = self.section.index(MANDATE_MARKER)
        self.assertLess(
            naming, mandate,
            "the `fuse` mandate must come AFTER the carrier-naming rule; "
            "Stage 70's arm B3 measured the reverse order at P11 11/32 "
            "against a baseline of 8/144")

    def test_the_forbidden_product_nouns_are_still_named(self) -> None:
        """Arm B1 deleted this sentence and P11 went UP, to 7/96.

        Compliance with the `fuse` mandate went to 96/96 in that arm and the
        failure simply moved onto `fuse` itself, so the sentence is buying
        the semantics rather than the id.
        """
        for noun in ("`shell`", "`box`", "`enclosure`", "`assembly`"):
            self.assertIn(
                noun, self.section,
                f"the union section no longer names {noun} as a product noun "
                "to avoid; Stage 70's arm B1 measured that deletion at "
                "P11 7/96 against a baseline of 8/144")

    def test_the_mandate_explains_why_rather_than_only_commanding(self) -> None:
        """B1's result is that the explanation, not the instruction, carries
        the semantics -- so the reason has to stay attached to the rule."""
        self.assertIn("there is no solid called `fuse`", self.section)
        self.assertIn("its own id names", self.section)


class TheUnionIdIsAMarkerNotACauseTests(unittest.TestCase):
    """The reading Stage 70 nearly adopted, and the measurement that stops it.

    On the committed prompt P11 is 0/136 with a union named `fuse` and 8/8
    without -- a perfect separation. Two arms reached 96/96 compliance and
    still carried P11. This test pins the SEMANTIC half of the claim, which
    is what is actually true: the id a union carries has no bearing on
    whether targeting it is legal. Targeting the union is P11 whatever the
    union is called.
    """

    #: Names deliberately spanning the mandated verb, the three product nouns
    #: the prompt warns against, and one the model has never produced.
    UNION_IDS = ("fuse", "enclosure", "shell", "assembly", "join_1")

    def test_targeting_the_union_is_P11_whatever_it_is_named(self) -> None:
        # This assertion exists because a mutation that emptied the loop below
        # left the test passing. A test that proves nothing is worse than no
        # test, so it states how much it is about to prove.
        self.assertGreaterEqual(len(self.UNION_IDS), 5)
        self.assertIn("fuse", self.UNION_IDS)
        checked = 0
        for union_id in self.UNION_IDS:
            with self.subTest(union_id=union_id):
                plan = two_block_plan("a")
                for op in plan["operations"]:
                    if op["id"] == "fuse":
                        op["id"] = union_id
                plan["operations"][-1]["target"] = union_id
                verdict = validate_plan(parse_plan_text(json.dumps(plan)))
                self.assertFalse(verdict.valid)
                self.assertEqual(
                    sorted({p.code for p in verdict.problems}), ["P11"],
                    f"a union named {union_id!r} must be as untargetable as "
                    "one named `fuse`")
                checked += 1
        self.assertEqual(checked, len(self.UNION_IDS))

    def test_the_surviving_body_is_valid_whatever_the_union_is_named(self) -> None:
        for union_id in ("fuse", "enclosure", "shell"):
            with self.subTest(union_id=union_id):
                plan = two_block_plan("a")
                for op in plan["operations"]:
                    if op["id"] == "fuse":
                        op["id"] = union_id
                self.assertTrue(
                    validate_plan(parse_plan_text(json.dumps(plan))).valid)


if __name__ == "__main__":
    unittest.main()
