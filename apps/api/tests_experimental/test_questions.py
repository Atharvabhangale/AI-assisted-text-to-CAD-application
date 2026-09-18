"""Answering from evidence: what is measured, what is declared, what is not.

The rule under test is not "does it answer" but "does it say what the answer
is worth". A declared 8 mm hole and a measured 8 mm bore are different claims,
and reporting either as the other is how a drawing asserts something nobody
checked.
"""

from __future__ import annotations

import math
import unittest

from cad_experimental.normalize import read_request
from cad_experimental.questions import (
    CALCULATED,
    DECLARED,
    MEASURED,
    answer,
    hole_count,
    hole_diameters,
    removed_volume,
)

MEASUREMENT = {
    "size": [120.0, 60.0, 10.0], "volume": 69738.053,
    "face_count": 16, "edge_count": 36, "solid_count": 1, "is_valid": True,
}


def _plan(*script):
    plan = None
    for text in script:
        plan = read_request(text, plan).plan
    return plan


PLATE = _plan("make a 120 x 60 x 10 mm plate")
DRILLED = _plan("make a 120 x 60 x 10 mm plate",
                "put a 12 mm hole through the centre",
                "add four 6 mm holes at the corners")


class ProvenanceTests(unittest.TestCase):
    """Every answer says where its number came from."""

    def test_a_kernel_number_is_measured(self) -> None:
        for question in ("What is the overall size?", "What is the volume?",
                         "How many faces?"):
            with self.subTest(question=question):
                found = answer(DRILLED, MEASUREMENT, "freecad", question)
                self.assertIsNotNone(found, question)
                self.assertEqual(found.provenance, MEASURED)
                self.assertIn("freecad", found.source)

    def test_a_plan_number_is_declared_not_measured(self) -> None:
        """A hole's diameter is what was ASKED for. The kernel was told it."""
        found = answer(DRILLED, MEASUREMENT, "freecad",
                       "What diameter are the holes?")
        self.assertEqual(found.provenance, DECLARED)
        self.assertIn("plan", found.source)
        self.assertNotEqual(found.provenance, MEASURED)

    def test_arithmetic_is_calculated_and_shows_its_working(self) -> None:
        found = answer(DRILLED, MEASUREMENT, "freecad",
                       "How much material is removed?")
        self.assertEqual(found.provenance, CALCULATED)
        self.assertTrue(found.working)
        self.assertIn("pi", found.working)

    def test_every_answer_carries_a_known_provenance(self) -> None:
        questions = (
            "What is the overall size?", "How many holes are there?",
            "What diameter are the holes?", "How much material is removed?",
            "What would it weigh in aluminium?", "What is the volume?",
            "How many faces?", "Where is the centre?", "How was it made?",
            "Which operation created the holes?", "What engine built this?",
        )
        for question in questions:
            with self.subTest(question=question):
                found = answer(DRILLED, MEASUREMENT, "freecad", question)
                self.assertIsNotNone(found, question)
                self.assertIn(found.provenance, (MEASURED, DECLARED,
                                                 CALCULATED))


class CountingTests(unittest.TestCase):

    def test_a_pattern_count_includes_its_source(self) -> None:
        """Four patterned holes is four, not five. Off by one here is an
        off-by-one on the shop floor."""
        plan = _plan("make a 100 x 60 x 10 mm plate",
                     "put a 8 mm hole through the centre",
                     "repeat it four times on a 40 mm bolt circle")
        count, working = hole_count(plan)
        self.assertEqual(count, 4)
        self.assertIn("repeats", working)

    def test_plain_holes_are_counted_once_each(self) -> None:
        count, _ = hole_count(DRILLED)
        self.assertEqual(count, 5)

    def test_a_part_with_no_holes_says_so(self) -> None:
        found = answer(PLATE, MEASUREMENT, "cadquery",
                       "How many holes are there?")
        self.assertIn("no holes", found.text)

    def test_distinct_diameters_are_listed(self) -> None:
        self.assertEqual(hole_diameters(DRILLED), [6.0, 12.0])

    def test_removed_volume_is_the_closed_form(self) -> None:
        removed, _ = removed_volume(DRILLED)
        expected = math.pi * 6 ** 2 * 10 + 4 * math.pi * 3 ** 2 * 10
        self.assertAlmostEqual(removed, expected, places=6)

    def test_removed_volume_admits_what_it_leaves_out(self) -> None:
        """With an edge treatment present the figure is bores only, and the
        answer says so rather than implying completeness."""
        plan = _plan("make a 120 x 60 x 10 mm plate",
                     "put a 12 mm hole through the centre",
                     "round the outside vertical edges to 3 mm")
        found = answer(plan, MEASUREMENT, "freecad",
                       "how much material is removed?")
        self.assertIn("bores only", found.text)


class MassTests(unittest.TestCase):

    def test_a_named_material_is_used(self) -> None:
        found = answer(PLATE, MEASUREMENT, "freecad",
                       "what would it weigh in aluminium?")
        self.assertEqual(found.provenance, CALCULATED)
        self.assertIn("aluminium", found.text)
        grams = 69738.053 / 1000.0 * 2.70
        self.assertIn(f"{grams:.1f}", found.text)

    def test_a_stated_density_is_used(self) -> None:
        found = answer(PLATE, MEASUREMENT, "freecad",
                       "what is the mass at 1.2 g/cm3?")
        self.assertIn("1.2", found.text)

    def test_no_material_means_no_number(self) -> None:
        """Geometry cannot tell you what a part is made of, so a mass with no
        material named is a question, not an answer."""
        found = answer(PLATE, MEASUREMENT, "freecad", "how much does it weigh?")
        self.assertIn("needs a material", found.text)
        for digit in ("g)", "kg"):
            self.assertNotIn(digit, found.text)


class DecliningTests(unittest.TestCase):
    """What it refuses to answer is the point."""

    def test_an_instruction_is_not_a_question(self) -> None:
        for text in ("make it bigger", "add a hole", "round the edges to 2 mm"):
            with self.subTest(text=text):
                self.assertIsNone(answer(DRILLED, MEASUREMENT, "freecad", text))

    def test_a_question_it_cannot_answer_goes_to_the_model(self) -> None:
        for text in ("is this strong enough?",
                     "what material should I use?",
                     "will this fit an M8 bolt?",
                     "how long will it take to machine?"):
            with self.subTest(text=text):
                self.assertIsNone(answer(DRILLED, MEASUREMENT, "freecad", text))

    def test_no_plan_means_no_answer(self) -> None:
        self.assertIsNone(answer(None, MEASUREMENT, "freecad", "what size?"))

    def test_no_measurement_means_no_measured_answer(self) -> None:
        """It must not fall back to the plan's declared numbers and present
        them as a measurement."""
        self.assertIsNone(answer(DRILLED, {}, "freecad", "what is the volume?"))

    def test_a_malformed_plan_does_not_raise_at_the_person(self) -> None:
        for broken in ({"operations": "not a list"},
                       {"operations": [None, 42]},
                       {"operations": [{"type": "through_hole"}]}):
            with self.subTest(plan=broken):
                answer(broken, MEASUREMENT, "freecad", "how many holes?")

    def test_nothing_here_executes_anything(self) -> None:
        import cad_experimental.questions as module

        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("eval(", "exec(", "subprocess", "os.system",
                          "__import__", "shell=True"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_it_imports_no_vendor(self) -> None:
        import cad_experimental.questions as module

        with open(module.__file__, encoding="utf-8") as handle:
            imports = [ln.strip() for ln in handle
                       if ln.lstrip().startswith(("import ", "from "))]
        for line in imports:
            for forbidden in ("anthropic", "openai", "genai", "httpx",
                              "requests"):
                self.assertNotIn(forbidden, line.lower(), line)


if __name__ == "__main__":
    unittest.main()
