"""Per-body measurement: which body a question is about, and refusing to guess.

Stage 73, step 4 of `docs/multi-body-design.md`. Stage 71 made several bodies
real and Stage 72 made them addressable by name for an EDIT. What was still
missing is the other half of the conversation: a part with two bodies has two
volumes, two envelopes and two sets of holes, and every answerer in
`questions.py` was written for a part that has one of each.

THE THREE FACTS THAT SHAPE EVERY TEST BELOW.

1. **A question that does not say which body is REFUSED, not answered.** And
   refusing is not the same as declining. `questions.answer` returning `None`
   means "not a question this module answers", and the request falls through
   to the model, which is safe. A question this module RECOGNISES but cannot
   answer without picking a body must not fall through the same way -- the
   model can read the plan but has never seen the part, so it would answer
   "the volume is 64000" about a part that has two bodies, and nothing
   downstream could tell that apart from a right answer. So the two travel
   differently: `None` falls through, `QuestionRefused` reaches the person.

2. **The single-body part takes no new path and says the same words.** The
   scope resolution returns the caller's own arguments untouched when there
   is at most one body, so a one-body part cannot be affected by any of this.
   `ASingleBodyPartIsUntouchedTests` is what would catch its loss, and it
   compares the answers character for character rather than merely asserting
   that some answer came back.

3. **An aggregate is CALCULATED, and the aggregate ENVELOPE is ASSUMED.**
   Volume, faces and edges genuinely add: separate bodies do not touch, so
   each contributes its own. The overall size does not add -- it is the box
   containing every body, and that box also contains the empty space between
   them. No kernel measured that box and no operation declared it, so it is
   the one number in this module that is neither. Reporting it as MEASURED
   would assert the part fills it.
"""

from __future__ import annotations

import json
import math
import unittest

from cad_experimental.body_reference import resolve_body
from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan_text
from cad_experimental.questions import (
    ASSUMED,
    CALCULATED,
    DECLARED,
    MEASURED,
    PROVENANCE,
    Answer,
    QuestionRefused,
    answer,
    scope_for,
    scope_of_body,
)
from cad_experimental.validation import validate_plan

PART = "part"

# --- the canonical two-body example, and its closed forms -------------------
#
# The same 40 mm cube and 20 x 30 pin Stage 71 built, with the same closed
# forms, so a number here can be compared with a number there.

CUBE_SIDE = 40.0
PIN_DIAMETER, PIN_HEIGHT = 20.0, 30.0
GAP = 10.0

CUBE_VOLUME = CUBE_SIDE ** 3
PIN_VOLUME = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_HEIGHT
TOLERANCE = 1e-6


def cube(identifier: str = "cube", side: float = CUBE_SIDE) -> dict:
    return {"id": identifier, "type": "box",
            "parameters": {"x": side, "y": side, "z": side}}


def pin(identifier: str = "pin") -> dict:
    return {"id": identifier, "type": "cylinder",
            "parameters": {"diameter": PIN_DIAMETER, "height": PIN_HEIGHT,
                           "position": {"x": CUBE_SIDE + GAP
                                             + PIN_DIAMETER / 2.0,
                                        "y": CUBE_SIDE / 2.0, "z": 0}}}


def bore(identifier: str, target: str, diameter: float) -> dict:
    return {"id": identifier, "type": "through_hole", "target": target,
            "parameters": {"diameter": diameter,
                           "position": {"x": CUBE_SIDE / 2.0,
                                        "y": CUBE_SIDE / 2.0, "z": 0},
                           "axis": "+Z"}}


def declare(identifier: str, target: str) -> dict:
    return {"id": identifier, "type": PART, "target": target}


def plan_of(*operations: dict, summary: str = "two bodies") -> dict:
    return {"status": "generated", "summary": summary,
            "operations": list(operations)}


def parsed(*operations: dict):
    return parse_plan_text(json.dumps(plan_of(*operations)))


def two_bodies() -> tuple:
    return (cube(), pin(), declare("body_cube", "cube"),
            declare("body_pin", "pin"))


def backend_or_skip():
    try:
        engine = resolve_backend()
    except BackendUnavailable as exc:  # pragma: no cover - environment
        raise unittest.SkipTest(str(exc))
    if not engine.available():  # pragma: no cover - environment
        raise unittest.SkipTest(f"{engine.name} is not available here")
    return engine


def built(*operations: dict):
    """Build a plan and hand back (plan dict, per-body measurements)."""
    plan = parsed(*operations)
    verdict = validate_plan(plan)
    assert verdict.valid, [(p.code, p.message) for p in verdict.problems]
    result = execute_plan(plan, backend=backend_or_skip())
    assert result.succeeded, result.failure
    measured = {body.id: body.measurement.to_dict() for body in result.bodies}
    return plan.to_dict(), measured


class ASingleBodyPartIsUntouchedTests(unittest.TestCase):
    """The one-body part must not be able to notice that Stage 73 happened.

    Every answer is compared CHARACTER FOR CHARACTER against the same
    question asked the pre-Stage-73 way -- with no `bodies` argument at all.
    Asserting merely that an answer came back would pass if the wording had
    quietly gained a body prefix, and that prefix is exactly what a session
    transcript and the browser both display.
    """

    QUESTIONS = (
        "What is the volume?",
        "What is the overall size?",
        "How many holes are there?",
        "How many faces does it have?",
        "What diameter are the holes?",
        "How much material is removed?",
    )

    def setUp(self) -> None:
        self.plan, self.measured = built(
            cube("base"), bore("h1", "base", 8.0))
        self.assertEqual(list(self.measured), ["base"])
        self.one = self.measured["base"]

    def test_every_answer_is_character_for_character_what_it_was(self) -> None:
        checked = 0
        for question in self.QUESTIONS:
            before = answer(self.plan, self.one, "cadquery", question)
            after = answer(self.plan, self.one, "cadquery", question,
                           bodies=self.measured)
            self.assertIsNotNone(before, f"{question!r} answered nothing; the "
                                         "question is no longer a probe")
            self.assertIsNotNone(after, question)
            self.assertEqual(before.text, after.text, question)
            self.assertEqual(before.provenance, after.provenance, question)
            self.assertEqual(before.source, after.source, question)
            self.assertEqual(before.working, after.working, question)
            checked += 1
        # The loop must have run. A mutation that empties QUESTIONS would
        # otherwise leave this test passing while proving nothing -- which is
        # a mistake this project has already made once, in Stage 70.
        self.assertEqual(checked, len(self.QUESTIONS))
        self.assertGreaterEqual(checked, 6)

    def test_no_body_prefix_reaches_a_single_body_answer(self) -> None:
        """The label is empty for one body, so nothing is prefixed."""
        found = answer(self.plan, self.one, "cadquery", "What is the volume?",
                       bodies=self.measured)
        self.assertTrue(found.text.startswith("Volume "), found.text)
        self.assertNotIn("base:", found.text)

    def test_a_single_body_question_is_never_refused(self) -> None:
        for question in self.QUESTIONS:
            with self.subTest(question):
                answer(self.plan, self.one, "cadquery", question,
                       bodies=self.measured)


class ANamedBodyIsMeasuredTests(unittest.TestCase):
    """Naming a body answers about THAT body, from that body's measurement."""

    def setUp(self) -> None:
        self.plan, self.measured = built(*two_bodies())

    def test_the_volume_of_a_named_body_is_that_body(self) -> None:
        found = answer(self.plan, {}, "cadquery",
                       "What is the volume of the pin?", bodies=self.measured)
        self.assertEqual(found.provenance, MEASURED)
        self.assertTrue(found.text.startswith("pin: "), found.text)
        self.assertAlmostEqual(
            float(found.text.split("Volume ")[1].split(" ")[0]),
            PIN_VOLUME, places=3)

    def test_the_other_body_answers_differently(self) -> None:
        """The real test of scoping: two questions, two different numbers."""
        pin_answer = answer(self.plan, {}, "cadquery",
                            "What is the volume of the pin?",
                            bodies=self.measured)
        cube_answer = answer(self.plan, {}, "cadquery",
                             "What is the volume of the cube?",
                             bodies=self.measured)
        self.assertNotEqual(pin_answer.text, cube_answer.text)
        self.assertAlmostEqual(
            float(cube_answer.text.split("Volume ")[1].split(" ")[0]),
            CUBE_VOLUME, places=3)

    def test_the_answer_says_which_body_it_is_about(self) -> None:
        found = answer(self.plan, {}, "cadquery", "How large is the cube?",
                       bodies=self.measured)
        self.assertTrue(found.text.startswith("cube: "), found.text)
        self.assertIn("cube", found.source)

    def test_a_named_body_size_is_that_body_not_the_pair(self) -> None:
        """The sharpest number here.

        The two bodies span x 0..70 together; the cube alone spans 0..40. An
        implementation that scoped the WORDS but not the MEASUREMENT would
        answer "70 x 40 x 40" under a heading that says `cube`, which is
        worse than refusing because it looks like an answer.
        """
        found = answer(self.plan, {}, "cadquery",
                       "What are the dimensions of the cube?",
                       bodies=self.measured)
        self.assertIn("40 x 40 x 40", found.text)
        self.assertNotIn("70", found.text)

    def test_the_plan_is_scoped_too_not_only_the_measurement(self) -> None:
        """A question about holes must count THAT body's holes.

        The cube is bored and the pin is not. Counting over the whole plan
        would report one hole for the pin as well, from an operation that
        never touched it.
        """
        plan, measured = built(cube(), pin(), bore("h1", "cube", 8.0),
                               declare("body_cube", "cube"),
                               declare("body_pin", "pin"))
        in_cube = answer(plan, {}, "cadquery",
                         "How many holes are in the cube?", bodies=measured)
        in_pin = answer(plan, {}, "cadquery",
                        "How many holes are in the pin?", bodies=measured)
        self.assertIn("1 hole", in_cube.text)
        self.assertIn("no holes", in_pin.text.lower())


class AQuestionThatNamesNoBodyIsRefusedTests(unittest.TestCase):
    """Refused, and refused in a way the model never gets to answer instead."""

    def setUp(self) -> None:
        self.plan, self.measured = built(*two_bodies())

    def test_the_volume_of_a_two_body_part_is_refused(self) -> None:
        with self.assertRaises(QuestionRefused) as caught:
            answer(self.plan, {}, "cadquery", "What is the volume?",
                   bodies=self.measured)
        self.assertIn("cube", str(caught.exception))
        self.assertIn("pin", str(caught.exception))

    def test_the_refusal_says_measure_not_change(self) -> None:
        """An edit and a question refuse for the same reason, differently.

        "Say which one to change" in front of someone who asked what the
        volume is reads as a refusal to answer rather than as a request to
        name a body.
        """
        with self.assertRaises(QuestionRefused) as caught:
            answer(self.plan, {}, "cadquery", "What is the size?",
                   bodies=self.measured)
        self.assertIn("measure", str(caught.exception))
        self.assertNotIn("to change", str(caught.exception))

    def test_refusing_is_not_declining(self) -> None:
        """`None` and a refusal must not be reachable by the same question.

        `None` falls through to the model. If an ambiguous question returned
        `None`, the model would answer it -- from the plan, about a part it
        cannot see -- and the whole guard would be decorative.
        """
        with self.assertRaises(QuestionRefused):
            answer(self.plan, {}, "cadquery", "What is the volume?",
                   bodies=self.measured)
        # And something that is genuinely not a question still declines.
        self.assertIsNone(
            answer(self.plan, {}, "cadquery", "make the cube wider",
                   bodies=self.measured))

    def test_a_non_question_is_never_refused(self) -> None:
        """Scoping happens AFTER the "is this a question" gate, deliberately.

        "Make the cube wider" is an edit. Refusing it here would take it away
        from the edit readers, which DO handle it, and the person would be
        told to name a body they had already named.
        """
        for sentence in ("make the cube 50 mm wide",
                         "put a 6 mm hole through the pin",
                         "undo that"):
            with self.subTest(sentence):
                self.assertIsNone(
                    answer(self.plan, {}, "cadquery", sentence,
                           bodies=self.measured))

    def test_naming_two_bodies_at_once_is_refused(self) -> None:
        with self.assertRaises(QuestionRefused):
            answer(self.plan, {}, "cadquery",
                   "What is the volume of the cube and the pin?",
                   bodies=self.measured)

    def test_a_body_that_was_consumed_is_refused_and_says_so(self) -> None:
        """Naming a body that a union took is a different answer from "no"."""
        plan, measured = built(
            cube(), cube("lid", 20.0),
            {"id": "fuse", "type": "union", "target": "cube",
             "tools": ["lid"]},
            pin(), declare("body_cube", "cube"), declare("body_pin", "pin"))
        with self.assertRaises(QuestionRefused) as caught:
            answer(plan, {}, "cadquery", "What is the volume of the lid?",
                   bodies=measured)
        self.assertIn("lid", str(caught.exception))
        self.assertIn("fuse", str(caught.exception))


class AnAggregateIsExplicitTests(unittest.TestCase):
    """"Total" is a different question, and its numbers are labelled so."""

    def setUp(self) -> None:
        self.plan, self.measured = built(*two_bodies())

    def test_the_total_volume_is_the_sum_and_says_it_is_calculated(self) -> None:
        found = answer(self.plan, {}, "cadquery", "What is the total volume?",
                       bodies=self.measured)
        self.assertEqual(found.provenance, CALCULATED)
        self.assertAlmostEqual(
            float(found.text.split("Volume ")[1].split(" ")[0]),
            CUBE_VOLUME + PIN_VOLUME, places=3)
        self.assertIn("every body", found.source)

    def test_the_total_volume_is_never_reported_as_measured(self) -> None:
        """No kernel measured 73424.778; it was added up here."""
        found = answer(self.plan, {}, "cadquery",
                       "What is the combined volume?", bodies=self.measured)
        self.assertNotEqual(found.provenance, MEASURED)

    def test_the_combined_envelope_is_ASSUMED(self) -> None:
        """The one ASSUMED number in the project, and why it exists.

        The box around a 40 mm cube and a pin standing 10 mm away runs
        0..70 in x. The part does not fill it -- there is a 10 mm gap of air
        in the middle that nothing measured and no operation declared.
        Calling that "the overall size, MEASURED" asserts a solid that is not
        there.
        """
        found = answer(self.plan, {}, "cadquery", "What is the total size?",
                       bodies=self.measured)
        self.assertEqual(found.provenance, ASSUMED)
        self.assertIn("70 x 40 x 40", found.text)
        self.assertIn("between them", found.source)

    def test_ASSUMED_is_in_the_vocabulary_and_is_its_own_thing(self) -> None:
        self.assertIn(ASSUMED, PROVENANCE)
        self.assertEqual(len(set(PROVENANCE)), 4)
        self.assertNotIn(ASSUMED, (MEASURED, DECLARED, CALCULATED))

    def test_the_aggregate_CENTRE_is_ASSUMED_too(self) -> None:
        """The centre of the combined box lies in the AIR between the bodies.

        The pair spans x 0..70, so the box centre is x = 35 -- which on this
        part is the 10 mm gap, and is inside neither body. It is the same
        defect as the envelope and gets the same label, and the downgrade
        keys on WHICH ANSWERER produced it rather than on matching words in
        its text, so rewording an answer cannot silently restore MEASURED.
        """
        found = answer(self.plan, {}, "cadquery",
                       "Where is the centre of the whole part?",
                       bodies=self.measured)
        self.assertEqual(found.provenance, ASSUMED)
        self.assertIn("between them", found.source)

    def test_a_count_over_two_bodies_does_not_say_the_solid(self) -> None:
        """"The solid has 9 faces" is wrong in front of two bodies."""
        found = answer(self.plan, {}, "cadquery",
                       "How many faces are there in all?",
                       bodies=self.measured)
        self.assertNotIn("The solid has", found.text)
        self.assertIn("The bodies have", found.text)
        self.assertEqual(found.provenance, CALCULATED)

    def test_an_aggregate_question_is_not_refused(self) -> None:
        """"Total" says which bodies: all of them. That is an answer."""
        for question in ("What is the total volume?",
                         "What is the combined volume?",
                         "What is the volume of all the bodies?"):
            with self.subTest(question):
                self.assertIsNotNone(
                    answer(self.plan, {}, "cadquery", question,
                           bodies=self.measured))


class TheScopeIsBuiltFromTheOneResolverTests(unittest.TestCase):
    """No second opinion about what "the cube" means."""

    def setUp(self) -> None:
        self.plan, self.measured = built(*two_bodies())

    def test_scope_for_agrees_with_resolve_body(self) -> None:
        scope = scope_for("the volume of the pin", self.plan, {},
                          self.measured)
        choice = resolve_body("the volume of the pin", self.plan)
        self.assertEqual(scope.label, choice.body)

    def test_scope_of_body_does_not_read_the_text_at_all(self) -> None:
        """A body called `total` must not be claimed by the aggregate words.

        `scope_of_body` exists precisely so that reporting every body in turn
        cannot go through the sentence reader, where a body id that happens
        to be an aggregate word would take a different path.
        """
        scope = scope_of_body(self.plan, self.measured, "pin")
        self.assertEqual(scope.label, "pin")
        self.assertFalse(scope.aggregate)
        self.assertAlmostEqual(scope.measurement["volume"], PIN_VOLUME,
                               delta=TOLERANCE)

    def test_a_body_the_build_did_not_measure_is_refused(self) -> None:
        """Resolved to a real body, with no numbers for it. Refuse.

        The alternative is the one thing this module must never do: answer
        from some OTHER body's numbers under this body's name.
        """
        with self.assertRaises(QuestionRefused) as caught:
            scope_for("the volume of the pin", self.plan, {},
                      {"cube": self.measured["cube"], "spare": {}})
        self.assertIn("pin", str(caught.exception))

    def test_a_body_measured_as_nothing_declines_rather_than_inventing(self) -> None:
        """Present but empty is a different case, and it DECLINES.

        A body whose measurement is an empty mapping has no volume and no
        envelope to report, so the measured answerers return `None` and the
        request falls through -- which is the safe direction. It is not
        refused, because the question is perfectly clear: it named one body
        and got one body. What is missing is evidence, not the body.

        The plan-derived answers still work, correctly scoped, which is why
        falling through is better here than refusing: the hole count below is
        DECLARED and does not need the kernel at all.
        """
        bodies = {"cube": self.measured["cube"], "pin": {}}
        scope = scope_for("the volume of the pin", self.plan, {}, bodies)
        self.assertEqual(scope.label, "pin")
        self.assertEqual(dict(scope.measurement), {})
        self.assertIsNone(
            answer(self.plan, {}, "cadquery",
                   "What is the volume of the pin?", bodies=bodies))
        counted = answer(self.plan, {}, "cadquery",
                         "How many holes are in the pin?", bodies=bodies)
        self.assertIsNotNone(counted)
        self.assertEqual(counted.provenance, DECLARED)


class TheKernelAgreesOnBothEnginesTests(unittest.TestCase):
    """The per-body numbers are the kernel's, and both kernels say the same."""

    def test_both_backends_measure_each_body_identically(self) -> None:
        engines = []
        for name in ("cadquery", "freecad"):
            try:
                engine = resolve_backend(name)
            except BackendUnavailable:  # pragma: no cover - environment
                continue
            if engine.available():
                engines.append(engine)
        if len(engines) < 2:  # pragma: no cover - environment
            raise unittest.SkipTest("both engines are needed for parity")

        plan = parsed(*two_bodies())
        readings = []
        for engine in engines:
            result = execute_plan(plan, backend=engine)
            self.assertTrue(result.succeeded, engine.name)
            readings.append({b.id: b.measurement.to_dict()
                             for b in result.bodies})

        first, second = readings
        self.assertEqual(set(first), set(second))
        for body in first:
            for key in ("volume", "face_count", "edge_count", "solid_count"):
                self.assertAlmostEqual(first[body][key], second[body][key],
                                       delta=TOLERANCE,
                                       msg=f"{body}.{key} differs by engine")

    def test_each_body_matches_its_closed_form(self) -> None:
        _, measured = built(*two_bodies())
        self.assertAlmostEqual(measured["cube"]["volume"], CUBE_VOLUME,
                               delta=1e-6)
        self.assertAlmostEqual(measured["pin"]["volume"], PIN_VOLUME,
                               delta=1e-6)

    def test_the_bodies_are_not_fused(self) -> None:
        """The total is the sum of the closed forms, so nothing joined."""
        _, measured = built(*two_bodies())
        total = sum(m["volume"] for m in measured.values())
        self.assertAlmostEqual(total, CUBE_VOLUME + PIN_VOLUME, delta=1e-6)
        for body in measured.values():
            self.assertEqual(body["solid_count"], 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
