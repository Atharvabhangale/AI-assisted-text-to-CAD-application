"""A union's own id never names a solid. The rule, and the prompt that teaches it.

Stage 65 measured a live model violating this rule on 5/5 attempts at the
golden six-plate request, and on 5/5 attempts at the simplest possible
two-plate request. Every failure was the same shape: the model gave the
`union` a product-sounding id (`assembly`, `box_assembly`, `shell`) and then
pointed every later `through_hole` at THAT id instead of at the union's
target -- the solid that actually survives.

These tests pin two separate things, and the separation is the point:

  1. the SEMANTIC RULE, in the validator -- a consumed or modifier id is not
     a future target, and P11 says so. This must keep firing. Nothing here
     may be relaxed to make a model pass;
  2. the PROMPT text that was measured to change the model's behaviour --
     because it was chosen by experiment and would otherwise be edited back
     out by someone who reads it as redundant prose.
"""

from __future__ import annotations

import unittest

from cad_experimental.parser import parse_plan
from cad_experimental.plan import TOOL_MODIFIER_TYPES
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import P11, P12, validate_plan


def box(identifier, x, y, z, px=0.0, py=0.0, pz=0.0):
    return {"id": identifier, "type": "box",
            "parameters": {"x": x, "y": y, "z": z,
                           "position": {"x": px, "y": py, "z": pz}}}


def hole(identifier, target, diameter=8.0, px=30.0, py=20.0, pz=0.0):
    return {"id": identifier, "type": "through_hole", "target": target,
            "parameters": {"diameter": diameter,
                           "position": {"x": px, "y": py, "z": pz}}}


def plan_of(*operations):
    return parse_plan({"status": "generated", "summary": "t",
                       "operations": list(operations)})


class AUnionIdIsNeverAFutureTargetTests(unittest.TestCase):
    """The rule itself. This is what must never be weakened."""

    BASE = (box("base", 60, 40, 5), box("wall", 60, 5, 30, py=40.0, pz=0.0))
    JOIN = {"id": "fuse", "type": "union", "target": "base",
            "tools": ["wall"]}

    def test_targeting_the_unions_own_id_is_P11(self) -> None:
        """The exact failure the live model produced, 24 times over."""
        verdict = validate_plan(plan_of(*self.BASE, self.JOIN,
                                        hole("bore", "fuse")))
        self.assertFalse(verdict.valid)
        self.assertIn(P11, {p.code for p in verdict.problems})

    def test_targeting_the_surviving_body_is_valid(self) -> None:
        """...and the correct plan, which differs only in that one word."""
        verdict = validate_plan(plan_of(*self.BASE, self.JOIN,
                                        hole("bore", "base")))
        self.assertTrue(verdict.valid,
                        [p.code for p in verdict.problems])

    def test_targeting_a_consumed_tool_is_refused(self) -> None:
        """The other half of the rule: a tool is gone once it is fused."""
        verdict = validate_plan(plan_of(*self.BASE, self.JOIN,
                                        hole("bore", "wall")))
        self.assertFalse(verdict.valid)
        self.assertTrue({P11, P12} & {p.code for p in verdict.problems},
                        [p.code for p in verdict.problems])

    def test_the_rule_is_the_same_for_every_tool_taking_operation(
        self,
    ) -> None:
        """`subtract` and `union` are the same shape and the same rule.

        Asserted over `TOOL_MODIFIER_TYPES` rather than over a hand-written
        pair, so a third tool-taking operation is covered the day it exists.
        """
        for kind in TOOL_MODIFIER_TYPES:
            with self.subTest(kind):
                operation = {"id": "step", "type": kind, "target": "base",
                             "tools": ["wall"]}
                verdict = validate_plan(
                    plan_of(*self.BASE, operation, hole("bore", "step")))
                self.assertFalse(verdict.valid, kind)
                self.assertIn(P11, {p.code for p in verdict.problems}, kind)

    def test_the_P11_message_names_the_remedy(self) -> None:
        """A message that only says "no" costs another round trip."""
        verdict = validate_plan(plan_of(*self.BASE, self.JOIN,
                                        hole("bore", "fuse")))
        message = next(p.message for p in verdict.problems if p.code == P11)
        self.assertIn("modifier", message)
        self.assertIn("the solid it changed", message)


class ThePromptTeachesItWhereItWasMeasuredToWorkTests(unittest.TestCase):
    """The prompt text Stage 65 chose by experiment, not by reading.

    Four candidate fixes were measured before this one and changed nothing:
    adding a correct worked example (0/5), rewriting the id-naming advice
    (0/5), putting the rule in the provider schema's `target` description
    (0/5), and repairing the contradicting sentence alone (0/5 correct).
    What moved the measurement was removing the contradiction AND mandating
    a verb id. Both halves are pinned because either alone was measured
    insufficient.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = system_prompt()

    def test_the_contradicting_sentence_is_gone(self) -> None:
        """It told the model to bore "through the shell, not through a loose
        plate" -- naming the post-union solid with a product noun that is no
        operation's id, and warning against the ONE legal target, since the
        union's target is by name a loose plate."""
        self.assertNotIn("not through a loose plate", self.text)
        self.assertNotIn("fused into one shell", self.text)

    def test_the_union_id_is_mandated_as_a_verb(self) -> None:
        """The measured lever. `"target": "fuse"` does not read as a body
        the way `"target": "assembly"` does, and the one attempt in the
        treatment arm that ignored this and named its union `shell`
        regressed to P11 immediately -- a controlled confirmation inside
        the run."""
        self.assertIn("Name the union itself `fuse`", self.text)
        self.assertIn("there is no solid called `fuse`", self.text)

    def test_the_surviving_id_is_given_the_parts_name(self) -> None:
        """The other half: the canonical form makes you name a six-plate
        shell by one plate's id, which is what the model was resisting.
        Naming that plate for the PART removes the friction instead of
        arguing with it."""
        self.assertIn("CARRY THE PART", self.text)

    def test_the_prompt_still_states_the_underlying_rule(self) -> None:
        """The wording changed; the rule did not."""
        self.assertIn("keeps the TARGET's id", self.text)


if __name__ == "__main__":
    unittest.main()
