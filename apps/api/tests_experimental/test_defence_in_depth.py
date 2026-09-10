"""Stage 39: the plan rules that the parser makes unreachable.

Found by the Stage 39 audit: two rules -- `P3` (unknown operation type) and
`P6` (a non-finite position component) -- were raised by the validator and
named in no test in the whole experimental suite. Both are deliberately
unreachable through :func:`~cad_experimental.parser.parse_plan`, which
rejects an unknown type and a non-finite number before the validator ever
sees them. That is exactly why they were untested, and exactly why they need
testing: an unreachable rule is the one most likely to have quietly stopped
working.

The validator must not depend on the parser having run. Everything here
builds a plan **in code**, bypassing the parser on purpose, to check that the
second line of defence is really there.
"""

from __future__ import annotations

import unittest

from cad_experimental.plan import (
    OPERATION_TYPES,
    BoxOperation,
    CylinderOperation,
    OperationPlan,
    PlanStatus,
    Point,
)
from cad_experimental.validation import P3, P4, P6, validate_plan


def built(*operations):
    """A plan assembled in code, without the parser."""
    return OperationPlan(status=PlanStatus.GENERATED, operations=operations)


def codes(plan):
    return [problem.code for problem in validate_plan(plan).problems]


class UnknownTypeTests(unittest.TestCase):
    """P3: an operation type the validator does not know."""

    class Impostor:
        """Shaped like an operation, with a type that does not exist."""

        TYPE = "loft"
        id = "body"
        position = None

        def parameters(self):
            return {}

    def test_an_unknown_type_is_p3(self):
        problems = validate_plan(built(self.Impostor())).problems
        self.assertIn(P3, [p.code for p in problems])
        self.assertIn("loft", problems[0].message)

    def test_the_type_it_names_really_is_absent(self):
        """So this test cannot start passing for the wrong reason."""
        self.assertNotIn(self.Impostor.TYPE, OPERATION_TYPES)

    def test_a_missing_type_attribute_is_also_p3(self):
        class Nameless:
            id = "body"
            position = None

            def parameters(self):
                return {}

        self.assertIn(P3, codes(built(Nameless())))

    def test_an_unknown_type_does_not_suppress_other_problems(self):
        """A plan with one impostor and one real error reports both."""
        found = codes(built(
            self.Impostor(),
            BoxOperation(id="plate", x=0.0, y=60.0, z=10.0),
        ))
        self.assertIn(P3, found)
        self.assertIn(P4, found)

    def test_the_parser_makes_this_unreachable(self):
        """The first line of defence, confirmed to still be there."""
        from cad_experimental.parser import PlanParseError, parse_plan

        with self.assertRaises(PlanParseError):
            parse_plan({
                "status": "generated", "summary": "s",
                "operations": [{"id": "body", "type": "loft",
                                "parameters": {}}],
            })


class NonFinitePositionTests(unittest.TestCase):
    """P6: a position component that is not a finite number.

    A NaN reaching the kernel would be a silent corruption rather than an
    error, which is why this is checked twice.
    """

    def plan_with(self, x=0.0, y=0.0, z=0.0):
        return built(BoxOperation(
            id="plate", x=100.0, y=60.0, z=10.0,
            position=Point(x=x, y=y, z=z),
        ))

    def test_nan_in_any_component_is_p6(self):
        nan = float("nan")
        for component in ("x", "y", "z"):
            with self.subTest(component=component):
                self.assertIn(P6, codes(self.plan_with(**{component: nan})))

    def test_infinity_in_any_component_is_p6(self):
        for value in (float("inf"), float("-inf")):
            for component in ("x", "y", "z"):
                with self.subTest(component=component, value=value):
                    self.assertIn(
                        P6, codes(self.plan_with(**{component: value}))
                    )

    def test_every_bad_component_is_reported_separately(self):
        nan = float("nan")
        found = codes(self.plan_with(x=nan, y=nan, z=nan))
        self.assertEqual(found.count(P6), 3)

    def test_the_problem_names_the_component(self):
        problems = validate_plan(self.plan_with(y=float("nan"))).problems
        self.assertEqual(problems[0].where, "operations[0].position.y")

    def test_a_finite_position_is_fine_including_negatives_and_zero(self):
        """Only a *size* must be positive; a position may be anything finite."""
        self.assertEqual(codes(self.plan_with(x=-50.0, y=0.0, z=-1e6)), [])

    def test_a_cylinder_position_is_checked_too(self):
        self.assertIn(P6, codes(built(CylinderOperation(
            id="rod", diameter=20.0, height=50.0,
            position=Point(x=float("nan"), y=0.0, z=0.0),
        ))))

    def test_the_parser_makes_this_unreachable(self):
        from cad_experimental.parser import PlanParseError, parse_plan_text

        for literal in ("NaN", "Infinity", "-Infinity"):
            text = (
                '{"status": "generated", "summary": "s", "operations": ['
                '{"id": "plate", "type": "box", "parameters": '
                '{"x": 100, "y": 60, "z": 10, "position": '
                '{"x": ' + literal + ', "y": 0, "z": 0}}}]}'
            )
            with self.subTest(literal=literal):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)


if __name__ == "__main__":
    unittest.main()
