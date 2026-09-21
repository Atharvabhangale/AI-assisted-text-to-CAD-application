"""An enclosure is six plates ROTATED onto six faces, and three holes.

Stage 65 removed the P11 union-target failure and the golden six-plate
request then failed on rule E1 -- the hole's centreline does not intersect
the target. Stage 66 spent 35 live calls on that, one variable at a time,
and found four defects. Each test below pins one of them.

  1. THE PLATES WERE NEVER ROTATED. Every baseline attempt wrote all six
     plates with the thickness on Z, stacking slabs. A shell needs the same
     three numbers PERMUTED onto each face's normal.
  2. THE OUTER SIZE WAS INVENTED (heights of 25, 20 and 5 across three
     attempts) because nothing said the plate faces give it.
  3. HOLES WERE NAMED FOR WALLS, so `hole_front` and `hole_back` became two
     operations on ONE centreline and the second removed nothing.
  4. `z is conventionally 0` was stated for +Z only and carried to +X and
     +Y, dropping those centrelines into the bottom face plane.

And the finding that matters most, because the prompt caused it: with the
carrier plate named `shell` in the prompt's own enclosure sentence, the
model gave that id to the UNION and then targeted it -- P11 on 8 of 8 calls
where it did so. Renaming the carrier to `bottom` took P11 from 5/5 to 0/5
as a single variable, with nothing else changed.

These tests pin the PROMPT TEXT that was measured to work. It reads like
ordinary prose and would otherwise be tidied away by someone who has not
seen the numbers. Nothing here weakens a rule to let a model pass.
"""

from __future__ import annotations

import json
import math
import unittest

from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan_text
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan


def _flat(text: str) -> str:
    return " ".join(text.replace("**", "").split())


class ThePromptTeachesTheLayoutItWasMeasuredToNeedTests(unittest.TestCase):
    """The four rules, in the words that were measured."""

    def setUp(self) -> None:
        self.text = system_prompt()
        self.flat = _flat(self.text)

    def _says(self, phrase: str, why: str) -> None:
        self.assertTrue(_flat(phrase) in self.flat,
                        f"the prompt no longer says {phrase!r}: {why}")

    def test_a_plate_s_thickness_runs_along_its_face_normal(self) -> None:
        """Defect 1. Measured: baseline kept the thickness on Z 5/5."""
        self._says(
            "the thickness runs along the NORMAL of the face that plate lies "
            "on",
            "without it the model writes all six plates as X by Y by t and "
            "stacks six slabs, which is not an enclosure")
        self._says("the same numbers PERMUTE",
                   "the permutation IS the rule; naming it is what made the "
                   "model rotate the plates 5/5")

    def test_the_permutation_table_names_all_three_pairs(self) -> None:
        """A table the model can read off, not a sentence to reason from."""
        for pair, shape in (("bottom, top", "X by Y by t"),
                            ("front, back", "X by t by Z"),
                            ("left, right", "t by Y by Z")):
            self._says(f"{pair}     {shape}",
                       "the layout table is what the model copies")

    def test_a_facing_pair_sits_at_zero_and_at_extent_minus_t(self) -> None:
        """Placement, not just shape. `position` is the MINIMUM corner."""
        self._says("a facing pair sits at 0 and at (extent - t) along its "
                   "own axis",
                   "without the arithmetic the second plate of a pair has "
                   "nowhere to go")

    def test_the_plates_give_the_outer_size(self) -> None:
        """Defect 2. Measured: invented heights of 25, 20 and 5."""
        self._says("The plates also GIVE the outer size -- do not choose one",
                   "the model invented a height on 3 of 5 attempts")
        self._says("read it off, do not invent it",
                   "this is the sentence that answers 'the height is missing'")

    def test_one_through_hole_opens_a_facing_pair(self) -> None:
        """Defect 3. Six walls through the centre are THREE operations."""
        self._says("ONE through_hole opens every wall that lies on its "
                   "centreline",
                   "the model wrote one hole per wall, and the second of a "
                   "facing pair had nothing left to remove")
        self._says("is THREE operations, not six",
                   "the count is the measurable thing")

    def test_a_hole_is_named_for_its_line_not_for_a_wall(self) -> None:
        """Defect 3's mechanism. The NAMING drove the count.

        The one attempt that named its holes `hole_x`/`hole_y`/`hole_z`
        wrote three and built; the attempts that wrote `hole_front` and
        `hole_back` wrote six and failed. This is the same mechanism Stage
        65 measured on the union's id, found a second time.
        """
        self._says("Name a hole for the LINE it is drilled along, not for a "
                   "wall it opens",
                   "an id that names a wall produces one operation per wall")
        self._says("Writing `hole_front` and `hole_back` is how one hole "
                   "becomes two operations on the same centreline",
                   "naming the exact mistake is what made it stop")

    def test_the_zero_convention_is_stated_for_all_three_axes(self) -> None:
        """Defect 4. It was stated for +Z only and carried to every axis."""
        for line in ("+Z hole -- x and y matter, z may be 0",
                     "+Y hole -- x and z matter, y may be 0",
                     "+X hole -- y and z matter, x may be 0"):
            self._says(line, "one axis illustrated is one axis generalised")
        self._says("0 is only ever safe for the component along the axis",
                   "otherwise the centreline lands on the part's outside "
                   "face and the cut grazes it")

    def test_the_carrier_is_named_for_the_piece_it_is(self) -> None:
        """THE measured finding: the prompt supplied the noun that broke it.

        Union id `shell` -> the holes targeted `shell`, P11 8/8. Union id
        `fuse` -> the target was right 8/11. The prompt's own enclosure
        sentence used to name the carrier `shell`, and the model handed that
        id to the union instead. Renaming it took P11 5/5 -> 0/5 alone.
        """
        self._says("the first is named `bottom`",
                   "naming the carrier for the plate it is, not for the "
                   "product, took P11 from 5/5 to 0/5 as a single variable")
        self._says("Do not name that piece for the finished product",
                   "this is the rule the rename generalises to")

    def test_the_prompt_no_longer_hands_the_model_the_noun(self) -> None:
        """The other half: the word that caused it must be GONE from that role.

        A guard that only checks the replacement is present would pass with
        both sentences standing, which is the Stage 62 defect ("an operation
        is not one edit") in a new place.
        """
        self.assertNotIn("the first is named `shell`", self.text,
                         "the prompt still offers `shell` as the carrier's "
                         "name; that is the id the model gave the union, and "
                         "it targeted it on 8 of 8 calls")
        self._says("`shell`, `box`, `enclosure` and `assembly` all read like "
                   "names for the whole thing",
                   "the word survives as an example of what NOT to do, which "
                   "is the point")


class ThePromptsWorkedExampleIsARealPartTests(unittest.TestCase):
    """A prompt may never teach a plan the validator or the kernel rejects.

    The enclosure example is parsed straight out of the prompt text, exactly
    as a reader would copy it, then validated and built. If someone edits the
    example's arithmetic, this fails.
    """

    @staticmethod
    def _example_operations() -> list:
        text = system_prompt()
        start = text.index("A 60 by 30 by 30 enclosure")
        block = text[start:text.index("Read the sizes")]
        operations, depth, buffer = [], 0, ""
        for character in block:
            if character == "{":
                depth += 1
            if depth:
                buffer += character
            if character == "}":
                depth -= 1
                if depth == 0:
                    operations.append(json.loads(buffer))
                    buffer = ""
        return operations

    def test_the_example_is_six_plates_and_one_union(self) -> None:
        operations = self._example_operations()
        self.assertEqual(len(operations), 7)
        self.assertEqual(sum(1 for o in operations if o["type"] == "box"), 6)
        self.assertEqual(sum(1 for o in operations if o["type"] == "union"), 1)

    def test_only_the_bottom_and_the_lid_carry_the_thickness_on_z(self) -> None:
        """The example must SHOW the permutation, not merely assert it."""
        boxes = {o["id"]: o["parameters"]
                 for o in self._example_operations() if o["type"] == "box"}
        thickness = 6
        on_z = {name for name, p in boxes.items() if p["z"] == thickness}
        on_y = {name for name, p in boxes.items() if p["y"] == thickness}
        on_x = {name for name, p in boxes.items() if p["x"] == thickness}
        self.assertEqual(on_z, {"base", "lid"})
        self.assertEqual(on_y, {"front", "back"})
        self.assertEqual(on_x, {"left", "right"})

    def test_the_example_shares_no_number_with_a_plate_count(self) -> None:
        """Measured: an example saying '4 mm plate' was read as the thickness.

        The request under test says "(4)plates", where 4 is a COUNT, and the
        model built every plate 4 thick. An example's numbers are read as
        data, so the thickness here must not be a small integer that a
        request is likely to use as a count.
        """
        boxes = [o["parameters"] for o in self._example_operations()
                 if o["type"] == "box"]
        thickness = min(min(p["x"], p["y"], p["z"]) for p in boxes)
        self.assertEqual(thickness, 6)
        self.assertNotIn(thickness, (2, 3, 4, 5),
                         "a thickness that reads as a plate count is how the "
                         "example leaked 4 into the part")

    def test_the_example_validates(self) -> None:
        plan = parse_plan_text(json.dumps(
            {"status": "generated", "operations": self._example_operations()}))
        verdict = validate_plan(plan)
        self.assertTrue(verdict.valid,
                        [(p.code, p.message) for p in verdict.problems])

    def test_the_example_builds_to_its_closed_form(self) -> None:
        try:
            backend = resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))
        plan = parse_plan_text(json.dumps(
            {"status": "generated", "operations": self._example_operations()}))
        execution = execute_plan(plan, backend=backend)
        self.assertTrue(execution.succeeded,
                        execution.failure and execution.failure.message)
        measurement = execution.bodies[0].measurement
        self.assertEqual(measurement.solid_count, 1)
        outer, thickness = (60.0, 30.0, 30.0), 6.0
        expected = (
            outer[0] * outer[1] * outer[2]
            - (outer[0] - 2 * thickness) * (outer[1] - 2 * thickness)
            * (outer[2] - 2 * thickness))
        self.assertAlmostEqual(measurement.volume, expected, places=6)
        envelope = tuple(round(hi - lo, 6) for lo, hi
                         in zip(measurement.minimum, measurement.maximum))
        self.assertEqual(envelope, outer)


class TheGoldenEnclosureTheLiveModelProducedTests(unittest.TestCase):
    """The plan a live model returned, rebuilt deterministically.

    This is NOT a claim about the model -- it is a pin on the ENGINE, so that
    the one plan the live route has been measured to produce keeps building
    to the same number. The plan is recorded verbatim in
    `docs/evaluation-baselines/stage66-enclosure-layout/`
    (`arm-F7-name-the-line.json`, attempt 5) and is reproduced here so the
    test does not depend on a baseline file it must never edit.
    """

    PLAN = {
        "status": "generated",
        "operations": [
            {"id": "bottom", "type": "box",
             "parameters": {"x": 40, "y": 20, "z": 4}},
            {"id": "top", "type": "box",
             "parameters": {"x": 40, "y": 20, "z": 4,
                            "position": {"x": 0, "y": 0, "z": 16}}},
            {"id": "front", "type": "box",
             "parameters": {"x": 40, "y": 4, "z": 20}},
            {"id": "back", "type": "box",
             "parameters": {"x": 40, "y": 4, "z": 20,
                            "position": {"x": 0, "y": 16, "z": 0}}},
            {"id": "left", "type": "box",
             "parameters": {"x": 4, "y": 20, "z": 20}},
            {"id": "right", "type": "box",
             "parameters": {"x": 4, "y": 20, "z": 20,
                            "position": {"x": 36, "y": 0, "z": 0}}},
            {"id": "fuse", "type": "union", "target": "bottom",
             "tools": ["top", "front", "back", "left", "right"]},
            {"id": "hole_z", "type": "through_hole", "target": "bottom",
             "parameters": {"diameter": 8,
                            "position": {"x": 20, "y": 10, "z": 0},
                            "axis": "+Z"}},
            {"id": "hole_x", "type": "through_hole", "target": "bottom",
             "parameters": {"diameter": 8,
                            "position": {"x": 0, "y": 10, "z": 10},
                            "axis": "+X"}},
            {"id": "hole_y", "type": "through_hole", "target": "bottom",
             "parameters": {"diameter": 8,
                            "position": {"x": 20, "y": 0, "z": 10},
                            "axis": "+Y"}},
        ],
    }

    def test_every_hole_targets_the_surviving_body(self) -> None:
        """The union keeps its TARGET's id; `fuse` names nothing."""
        for operation in self.PLAN["operations"]:
            if operation["type"] == "through_hole":
                self.assertEqual(operation["target"], "bottom")

    def test_there_is_one_hole_per_axis(self) -> None:
        axes = sorted(o["parameters"]["axis"]
                      for o in self.PLAN["operations"]
                      if o["type"] == "through_hole")
        self.assertEqual(axes, ["+X", "+Y", "+Z"])

    def test_it_validates(self) -> None:
        verdict = validate_plan(parse_plan_text(json.dumps(self.PLAN)))
        self.assertTrue(verdict.valid,
                        [(p.code, p.message) for p in verdict.problems])

    def test_it_builds_to_its_closed_form(self) -> None:
        try:
            backend = resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))
        execution = execute_plan(
            parse_plan_text(json.dumps(self.PLAN)), backend=backend)
        self.assertTrue(execution.succeeded,
                        execution.failure and execution.failure.message)
        measurement = execution.bodies[0].measurement
        # 40x20x20 shell of 4 mm plate, less three d=8 holes, each boring a
        # facing pair of 4 mm walls. The three centrelines meet only inside
        # the cavity, so no removed volume is counted twice.
        thickness, radius = 4.0, 4.0
        shell = 40.0 * 20.0 * 20.0 - 32.0 * 12.0 * 12.0
        holes = 3 * (2 * math.pi * radius * radius * thickness)
        self.assertAlmostEqual(measurement.volume, shell - holes, places=6)
        self.assertEqual(measurement.solid_count, 1)
        self.assertEqual(measurement.face_count, 18)
        self.assertEqual(measurement.edge_count, 42)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
