"""Naming a body, and refusing when the name does not settle the question.

Stage 71 made several bodies real and left the product unable to EDIT one:
every reader asked for "the body" and, with two live, there was no such
thing, so they declined. Declining was right and it is not an answer.

Stage 72's whole content is one resolver -- `body_reference.resolve_body` --
and the four rules it applies. What matters about it is the shape of the
answer, not the matching:

  * it returns a body, or a REASON, and never a guess;
  * the request names a body by its **id**, which is the body's identity for
    life. Matching on the noun of the primitive it came from, or on a synonym
    table, was rejected: "the block" for a body named `plate` is a guess, and
    a wrong guess here edits the wrong body and reports success;
  * with one body live and no name in the request, the answer is that body --
    which is exactly what every plan did before Stage 71, unchanged.

The tests below are organised by what would go wrong, not by function.
"""

from __future__ import annotations

import json
import math
import unittest

from cad_experimental.body_reference import resolve_body
from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.history import plan_history
from cad_experimental.normalize import Reading, Refusal, read_request
from cad_experimental.parser import parse_plan_text
from cad_experimental.validation import validate_plan

TWO_BODIES = ("Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it "
              "as two separate bodies.")
ONE_BODY = "a 100 x 50 x 10 mm plate"

CUBE_SIDE = 40.0
PIN_DIAMETER, PIN_HEIGHT = 20.0, 30.0
CUBE_VOLUME = CUBE_SIDE ** 3
PIN_VOLUME = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_HEIGHT
TOLERANCE = 1e-6


def start(request: str) -> dict:
    reading = read_request(request)
    assert isinstance(reading, Reading), reading
    return reading.plan


def turn(plan: dict, request: str):
    """One conversational turn, exactly as the session takes it."""
    return read_request(request, plan)


def build(plan: dict, backend=None):
    parsed = parse_plan_text(json.dumps(plan))
    verdict = validate_plan(parsed)
    assert verdict.valid, [(p.code, p.message) for p in verdict.problems]
    return execute_plan(parsed, backend=backend or resolve_backend())


def measured(result, body_id: str):
    for body in result.bodies:
        if body.id == body_id:
            return body.measurement
    raise AssertionError(f"no body {body_id!r} in "
                         f"{[b.id for b in result.bodies]}")


class TheResolverAnswersOrRefusesTests(unittest.TestCase):
    """The four rules, and the two ways a name can fail to settle anything."""

    def setUp(self) -> None:
        self.two = start(TWO_BODIES)
        self.one = start(ONE_BODY)

    def test_a_named_live_body_is_the_answer(self) -> None:
        for name in ("cube", "cylinder"):
            with self.subTest(body=name):
                choice = resolve_body(f"make the {name} 50 mm wide", self.two)
                self.assertEqual(choice.body, name)
                self.assertTrue(choice.named)

    def test_one_live_body_needs_no_name(self) -> None:
        """The pre-Stage-71 behaviour, unchanged and asserted as such."""
        choice = resolve_body("make it 20 mm wider", self.one)
        self.assertEqual(choice.body, "body")
        self.assertFalse(choice.named)

    def test_several_bodies_and_no_name_refuses_and_lists_them(self) -> None:
        choice = resolve_body("make it 20 mm wider", self.two)
        self.assertIsNone(choice.body)
        self.assertIn("2 separate bodies", choice.reason)
        for name in ("cube", "cylinder"):
            self.assertIn(repr(name), choice.reason)

    def test_two_names_at_once_refuses(self) -> None:
        """One operation changes one body, so two names is not an answer."""
        choice = resolve_body("hole through the cube and the cylinder",
                              self.two)
        self.assertIsNone(choice.body)
        self.assertIn("more than one body", choice.reason)

    def test_a_consumed_body_says_what_took_it(self) -> None:
        """"No such body" would send someone looking for a typo."""
        fused = {"status": "generated", "summary": "fused", "operations": [
            {"id": "base", "type": "box",
             "parameters": {"x": 40, "y": 40, "z": 10}},
            {"id": "lid", "type": "box",
             "parameters": {"x": 40, "y": 40, "z": 10,
                            "position": {"x": 0, "y": 0, "z": 10}}},
            {"id": "fuse", "type": "union", "target": "base",
             "tools": ["lid"]},
        ]}
        choice = resolve_body("make the lid thicker", fused)
        self.assertIsNone(choice.body)
        self.assertIn("'fuse' consumed it", choice.reason)
        self.assertIn("'base'", choice.reason)

    def test_an_id_inside_a_longer_id_is_settled_by_the_text(self) -> None:
        """`plate` is a whole word inside `plate-2`, because `-` is not one.

        Settled by one span containing the other, never by preferring a
        longer name in general -- so it can never pick a body the request did
        not mention.
        """
        plan = {"status": "generated", "summary": "two plates", "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 40, "y": 40, "z": 5}},
            {"id": "plate-2", "type": "box",
             "parameters": {"x": 40, "y": 40, "z": 5,
                            "position": {"x": 0, "y": 0, "z": 20}}},
            {"id": "d1", "type": "part", "target": "plate"},
            {"id": "d2", "type": "part", "target": "plate-2"},
        ]}
        self.assertEqual(resolve_body("make plate-2 thicker", plan).body,
                         "plate-2")
        self.assertEqual(resolve_body("make plate thicker", plan).body,
                         "plate")

    def test_it_never_raises(self) -> None:
        for plan in (None, {"operations": "not a list"}, {}):
            with self.subTest(plan=plan):
                choice = resolve_body("make it wider", plan)
                self.assertIsNone(choice.body)
                self.assertTrue(choice.reason)


class TheReadersRefuseRatherThanGuessTests(unittest.TestCase):
    """A refusal is an answer; declining sends a read sentence to a model."""

    def setUp(self) -> None:
        self.two = start(TWO_BODIES)

    def test_an_edit_naming_no_body_is_refused_not_declined(self) -> None:
        answer = turn(self.two, "make it 20 mm wider")
        self.assertIsInstance(answer, Refusal)
        self.assertIn("separate bodies", answer.reason)

    def test_an_edit_naming_two_bodies_is_refused(self) -> None:
        answer = turn(self.two, "make the cube and the cylinder 20 mm wider")
        self.assertIsInstance(answer, Refusal)
        self.assertIn("more than one body", answer.reason)

    def test_a_sentence_that_is_nobody_s_still_declines(self) -> None:
        """The reader decides the VERB is its own before it asks which body.

        Folding the two together would let "not my sentence" reach the person
        as though it were a verdict about their part.
        """
        self.assertIsNone(turn(self.two, "what is the volume?"))


class NamingABodyTargetsItTests(unittest.TestCase):
    """The edits the slice exists for, read into the canonical plan."""

    def setUp(self) -> None:
        self.two = start(TWO_BODIES)

    def operations(self, reading):
        return {op["id"]: op for op in reading.plan["operations"]}

    def test_a_resize_edits_only_the_named_body(self) -> None:
        reading = turn(self.two, "make the cube 50 mm wide")
        self.assertIsInstance(reading, Reading)
        operations = self.operations(reading)
        self.assertEqual(operations["cube"]["parameters"]["x"], 50.0)
        before = {op["id"]: op for op in self.two["operations"]}
        self.assertEqual(operations["cylinder"], before["cylinder"])

    def test_a_bore_targets_the_named_body(self) -> None:
        reading = turn(self.two, "put a 6 mm hole through the cube")
        self.assertIsInstance(reading, Reading)
        bore = [op for op in reading.plan["operations"]
                if op["type"] == "through_hole"]
        self.assertEqual(len(bore), 1)
        self.assertEqual(bore[0]["target"], "cube")

    def test_the_bore_lands_in_THAT_body_not_between_them(self) -> None:
        """The envelope must be the body's, not every constructive solid's.

        Unscoped, the box around BOTH bodies runs x 0..70, so its centre is
        x = 35 -- and a hole "through the centre of the cube" would be bored
        15 mm off the cube's own centre, in a plan that still validates and
        still builds. This is the sharpest thing in the slice.
        """
        reading = turn(self.two, "put a 6 mm hole through the cube")
        bore = next(op for op in reading.plan["operations"]
                    if op["type"] == "through_hole")
        position = bore["parameters"]["position"]
        self.assertAlmostEqual(position["x"], CUBE_SIDE / 2.0, places=9)
        self.assertAlmostEqual(position["y"], CUBE_SIDE / 2.0, places=9)

    def test_a_named_body_is_a_locative_and_says_so(self) -> None:
        reading = turn(self.two, "put a 6 mm hole through the cube")
        self.assertTrue(any("centred on it" in note
                            for note in reading.assumptions), reading.assumptions)

    def test_the_plan_it_produces_validates(self) -> None:
        for request in ("make the cube 50 mm wide",
                        "put a 6 mm hole through the cube"):
            with self.subTest(request=request):
                reading = turn(self.two, request)
                parsed = parse_plan_text(json.dumps(reading.plan))
                self.assertTrue(validate_plan(parsed).valid)
                self.assertEqual(plan_history(parsed).declared_bodies,
                                 ("cube", "cylinder"))


class AnEditOfOneBodyLeavesTheOtherAloneTests(unittest.TestCase):
    """Isolation, through the READER path, decided by the kernel."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))
        self.two = start(TWO_BODIES)
        self.baseline = build(self.two)

    def assertUnchanged(self, result, body_id: str) -> None:
        was, now = measured(self.baseline, body_id), measured(result, body_id)
        self.assertAlmostEqual(now.volume, was.volume, delta=TOLERANCE)
        self.assertEqual((now.face_count, now.edge_count, now.solid_count),
                         (was.face_count, was.edge_count, was.solid_count))

    def test_the_baseline_is_the_two_closed_forms(self) -> None:
        self.assertAlmostEqual(measured(self.baseline, "cube").volume,
                               CUBE_VOLUME, delta=TOLERANCE)
        self.assertAlmostEqual(measured(self.baseline, "cylinder").volume,
                               PIN_VOLUME, delta=TOLERANCE)

    def test_resizing_the_cube_leaves_the_cylinder_identical(self) -> None:
        reading = turn(self.two, "make the cube 50 mm wide")
        result = build(reading.plan)
        self.assertUnchanged(result, "cylinder")
        self.assertAlmostEqual(measured(result, "cube").volume,
                               50.0 * CUBE_SIDE * CUBE_SIDE, delta=TOLERANCE)

    def test_boring_the_cube_leaves_the_cylinder_identical(self) -> None:
        reading = turn(self.two, "put a 6 mm hole through the cube")
        result = build(reading.plan)
        self.assertUnchanged(result, "cylinder")
        bored = measured(result, "cube")
        self.assertAlmostEqual(
            bored.volume, CUBE_VOLUME - math.pi * 3.0 ** 2 * CUBE_SIDE,
            delta=1e-4)

    def test_editing_each_body_in_turn_leaves_the_other_alone(self) -> None:
        """The whole conversation, and the property it must keep throughout."""
        first = turn(self.two, "make the cube 50 mm wide")
        after_a = build(first.plan)
        self.assertUnchanged(after_a, "cylinder")

        second = turn(first.plan, "put a 6 mm hole through the cylinder")
        self.assertIsInstance(second, Reading)
        after_b = build(second.plan)
        # The cube keeps the edit it was given and gains nothing from the
        # cylinder's.
        self.assertAlmostEqual(measured(after_b, "cube").volume,
                               measured(after_a, "cube").volume,
                               delta=TOLERANCE)
        self.assertLess(measured(after_b, "cylinder").volume,
                        measured(after_a, "cylinder").volume)

    def test_the_features_stay_in_their_own_body_s_history(self) -> None:
        reading = turn(self.two, "put a 6 mm hole through the cube")
        history = plan_history(parse_plan_text(json.dumps(reading.plan)))
        bore = next(op["id"] for op in reading.plan["operations"]
                    if op["type"] == "through_hole")
        self.assertIn(bore, history.body("cube").features)
        self.assertNotIn(bore, history.body("cylinder").features)


class AFailedSelectionNamesItsBodyTests(unittest.TestCase):
    """With two bodies, "matched no edge" is the first question a reader asks."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    def test_R1_says_which_body(self) -> None:
        plan = parse_plan_text(json.dumps({
            "status": "generated", "summary": "x", "operations": [
                {"id": "cube", "type": "box",
                 "parameters": {"x": 40, "y": 40, "z": 40}},
                {"id": "pin", "type": "cylinder",
                 "parameters": {"diameter": 20, "height": 30,
                                "position": {"x": 60, "y": 20, "z": 0}}},
                {"id": "round", "type": "fillet", "target": "cube",
                 "parameters": {"radius": 2,
                                "edges": {"select": "circular", "axis": "Z"}}},
                {"id": "d1", "type": "part", "target": "cube"},
                {"id": "d2", "type": "part", "target": "pin"},
            ]}))
        result = execute_plan(plan, backend=resolve_backend())
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, "R1")
        self.assertIn("on 'cube'", result.failure.message)


class AbsoluteAndRelativeAreDifferentSentencesTests(unittest.TestCase):
    """The defect the bare adjectives would have introduced, pinned.

    "20 mm WIDER" is a change of 20; "50 mm WIDE" is a width of 50. Same
    axis, opposite arithmetic. Without the comparative check, "make the cube
    50 mm wide" on a 40 mm cube read as 40 + 50 = 90.
    """

    def test_a_bare_adjective_states_the_finished_size(self) -> None:
        reading = turn(start(TWO_BODIES), "make the cube 50 mm wide")
        cube = next(op for op in reading.plan["operations"]
                    if op["id"] == "cube")
        self.assertEqual(cube["parameters"]["x"], 50.0)

    def test_a_comparative_states_a_change(self) -> None:
        reading = turn(start(TWO_BODIES), "make the cube 20 mm wider")
        cube = next(op for op in reading.plan["operations"]
                    if op["id"] == "cube")
        self.assertEqual(cube["parameters"]["x"], CUBE_SIDE + 20.0)

    def test_the_single_body_readings_are_what_they_were(self) -> None:
        one = start(ONE_BODY)
        grew = turn(one, "make it 20 mm wider")
        self.assertIn("grew X to 120 mm", grew.summary)
        absolute = turn(one, "set the width to 50 mm")
        self.assertIn("set X to 50 mm", absolute.summary)


class BothKernelsAgreeOnTheEditedBodiesTests(unittest.TestCase):

    def engines(self):
        found = []
        for name in ("cadquery", "freecad"):
            try:
                found.append(resolve_backend(name))
            except BackendUnavailable:
                continue
        return found

    def test_a_body_targeted_edit_measures_the_same_on_every_engine(self) -> None:
        engines = self.engines()
        if len(engines) < 2:
            self.skipTest("only one backend is available in this interpreter")
        reading = turn(start(TWO_BODIES), "put a 6 mm hole through the cube")
        seen = {}
        for engine in engines:
            result = build(reading.plan, backend=engine)
            self.assertTrue(result.succeeded, result.failure)
            seen[engine.name] = {
                body.id: (body.measurement.volume, body.measurement.face_count,
                          body.measurement.edge_count)
                for body in result.bodies
            }
        names = list(seen)
        self.assertEqual(seen[names[0]], seen[names[1]])


class TheResolverIsProviderNeutralTests(unittest.TestCase):

    def test_it_imports_no_vendor_sdk_and_no_kernel(self) -> None:
        import ast
        import pathlib

        import cad_experimental.body_reference as module
        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        self.assertEqual(
            imported, {"re", "dataclasses", "typing", "history", "parser",
                       "__future__"},
            "the resolver reached past the standard library, the parser and "
            "the history walk")


if __name__ == "__main__":
    unittest.main()
