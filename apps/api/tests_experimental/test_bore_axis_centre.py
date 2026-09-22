"""Which components of a bore's position matter, and which one is free.

Stage 69 measured this over 224 live calls to `claude-haiku-4-5-20251001`.
The record is `docs/evaluation-baselines/stage69-bore-axis-centre/`.

Stage 68 left exactly one failure standing on the EXPLICIT golden request and
read it as the +Z bore's position triple being copied to the other two axes.
Widening that arm to 24 fresh calls REFUTED that reading: cross-axis triple
reuse occurred 0/24. What the model actually does is narrower. Over 64 pooled
baseline attempts -- 192 across-axis components -- every one of the 9 wrong
components was the **z** of an +X or +Y bore:

    X bore, Y component   0/64        X bore, Z component   5/64
    Y bore, X component   0/64        Y bore, Z component   4/64
    Z bore, X component   0/64        Z bore, Y component   0/64

The along-axis component was written as 0 on 94 of 96 bores, so the model has
the "0 is safe along the axis" half of the rule and over-applies it to the
letter z.

FOUR ARMS OF 32 LIVE CALLS SEPARATED THE CAUSE, and two of them are negative
results worth as much as the fix:

  A1  delete every letter-to-zero pairing from the axis table  ->  E 4/32,
      WORSE than the baseline's 2/32. Prose about the rule moved nothing,
      for the third stage running.
  A3  the same three lines REORDERED so +X comes first -- a pure reordering,
      same length, same characters -- ->  E 4/32, and all 8 wrong components
      were still z. If the defect were primacy the error would have moved
      onto x. It did not, which REFUTES the table as the mechanism.
  A2  one worked example whose bores carry a NONZERO z  ->  E 0/32, and
      0/96 over the confirmation runs.

The cause was the missing EXAMPLE, not the missing rule: every hole position
the prompt showed had z at 0, because every example bore ran along +Z. This
is Stage 65's and Stage 66's mechanism found a third time -- what the model
imitates is what the prompt SHOWS, and a rule it states alongside a
contradicting example loses.

Adopted as `2026-09-18.5`: strict success 54/64 -> 91/96 (Fisher exact, two
sided, p = 0.049), with thickness 96/96 and plate count 96/96 -- no
regression anywhere. All 91 successes rebuilt from recorded output measure
11492.035526277 on CadQuery 2.8.0 AND FreeCAD 1.0.0, bit-identical, against
a closed form of 11492.035526276899.

This module pins the rule in GEOMETRY, where no prompt wording can argue with
it, and pins the prompt's worked example by parsing its arithmetic out of the
prompt rather than by matching a string.
"""

from __future__ import annotations

import json
import math
import re
import unittest

from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
from cad_experimental.executor import execute_plan
from cad_experimental.parser import parse_plan_text
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

#: The canonical enclosure the golden request describes.
ENVELOPE = (40.0, 20.0, 20.0)
THICKNESS = 8.0 / 8.0 * 5.0       # 5 mm, written so no literal drifts alone
DIAMETER = 8.0
#: 6 outer + 6 inner planar faces, plus two cylindrical faces per bore.
FACES, EDGES = 18, 42
AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}

#: The two components ACROSS a bore's own axis sit here. Derived from the
#: envelope, never from a plan.
CENTRE = tuple(e / 2.0 for e in ENVELOPE)


def _plate(identifier, size, position=(0.0, 0.0, 0.0)):
    return {"id": identifier, "type": "box",
            "parameters": {"x": size[0], "y": size[1], "z": size[2],
                           "position": {"x": position[0], "y": position[1],
                                        "z": position[2]}}}


def _bore(identifier, target, axis, position):
    return {"id": identifier, "type": "through_hole", "target": target,
            "parameters": {"diameter": DIAMETER, "axis": axis,
                           "position": {"x": position[0], "y": position[1],
                                        "z": position[2]}}}


def centred_triple(axis: str, along: float = 0.0) -> tuple[float, float, float]:
    """The position a bore along `axis` must carry to pass through the centre.

    The two components across the axis take the centre; the one along it is
    free, and `along` says what to write there.
    """
    i = AXIS_INDEX[axis[-1].upper()]
    return tuple(along if j == i else CENTRE[j] for j in range(3))


def enclosure(bores: dict[str, tuple[float, float, float]]) -> dict:
    """The golden enclosure, with each named bore at the given position."""
    t, (x, y, z) = THICKNESS, ENVELOPE
    operations = [
        _plate("base", (x, y, t)),
        _plate("lid", (x, y, t), (0.0, 0.0, z - t)),
        _plate("front", (x, t, z)),
        _plate("back", (x, t, z), (0.0, y - t, 0.0)),
        _plate("left", (t, y, z)),
        _plate("right", (t, y, z), (x - t, 0.0, 0.0)),
        {"id": "fuse", "type": "union", "target": "base",
         "tools": ["lid", "front", "back", "left", "right"]},
    ]
    for axis, position in bores.items():
        operations.append(_bore(f"hole_{axis.lower()}", "base",
                                "+" + axis, position))
    return {"status": "generated", "operations": operations}


def closed_form() -> float:
    x, y, z = ENVELOPE
    t, r = THICKNESS, DIAMETER / 2.0
    shell = x * y * z - (x - 2 * t) * (y - 2 * t) * (z - 2 * t)
    return shell - 3 * (2 * math.pi * r * r * t)


def build(plan: dict):
    parsed = parse_plan_text(json.dumps(plan))
    verdict = validate_plan(parsed)
    if not verdict.valid:
        raise AssertionError([(p.code, p.message) for p in verdict.problems])
    return execute_plan(parsed, backend=resolve_backend())


ALL_CENTRED = {axis: centred_triple(axis) for axis in ("Z", "Y", "X")}
#: The measured defect: z zeroed on every bore, whatever its axis. Correct
#: for +Z, wrong for +Y and +X.
Z_ZEROED = {axis: tuple(0.0 if j == 2 else v for j, v in enumerate(triple))
            for axis, triple in ALL_CENTRED.items()}


class TheAcrossAxisComponentsDecideTests(unittest.TestCase):
    """The kernel, not the prompt, is the authority on which components act."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    def test_the_centred_part_is_the_canonical_enclosure(self) -> None:
        result = build(enclosure(ALL_CENTRED))
        self.assertTrue(result.succeeded, result.failure)
        m = result.bodies[0].measurement
        self.assertEqual(m.solid_count, 1)
        self.assertEqual((m.face_count, m.edge_count), (FACES, EDGES))
        self.assertAlmostEqual(m.volume, closed_form(), places=6)

    def test_zeroing_z_on_every_bore_is_a_different_part(self) -> None:
        """The measured defect, stated in geometry rather than in prose.

        The +Y and +X centrelines drop into the bottom face plane, where they
        graze it instead of boring through the walls. The part still builds,
        which is exactly why a criterion that only asks "did it build?"
        cannot catch this.
        """
        result = build(enclosure(Z_ZEROED))
        self.assertTrue(result.succeeded, result.failure)
        m = result.bodies[0].measurement
        self.assertNotEqual((m.face_count, m.edge_count), (FACES, EDGES))
        self.assertGreater(abs(m.volume - closed_form()), 1.0)

    def test_the_along_axis_component_changes_nothing(self) -> None:
        """A through cut is unaffected by where along its own line it starts."""
        reference = build(enclosure(ALL_CENTRED)).bodies[0].measurement
        moved = {axis: centred_triple(axis, along=offset)
                 for axis, offset in (("Z", 13.0), ("Y", -7.5), ("X", 31.25))}
        self.assertNotEqual(moved, ALL_CENTRED)
        other = build(enclosure(moved)).bodies[0].measurement
        self.assertEqual((other.face_count, other.edge_count),
                         (reference.face_count, reference.edge_count))
        self.assertAlmostEqual(other.volume, reference.volume, places=6)

    def test_an_off_centre_bore_can_measure_identically(self) -> None:
        """Why the criterion must read the PLAN, not only the part.

        Shifting the +Z bore's x from 20 to 17 leaves the centreline inside
        the same two walls and clear of the other four, so the kernel removes
        the same material and reports the same topology. The hole is in the
        wrong place and NO measurement of the finished part says so.

        This is the geometric justification for the plan-level `bore_centred`
        check in Stage 68's evaluator. A criterion built only on volume,
        faces and edges would score this correct.
        """
        reference = build(enclosure(ALL_CENTRED)).bodies[0].measurement
        off = dict(ALL_CENTRED)
        off["Z"] = (17.0, CENTRE[1], 0.0)
        self.assertNotEqual(off["Z"], ALL_CENTRED["Z"])
        other = build(enclosure(off)).bodies[0].measurement
        self.assertEqual((other.face_count, other.edge_count),
                         (reference.face_count, reference.edge_count))
        self.assertAlmostEqual(other.volume, reference.volume, places=6)

    def test_an_across_axis_component_that_leaves_the_wall_changes_the_part(self):
        """The cases the kernel CAN decide, and the defect is one of them.

        Moving an across-axis component far enough that the centreline meets
        a different wall, or none, changes the topology. The measured defect
        -- z at 0 on an +X or +Y bore -- is this case: the centreline lands
        in the bottom face plane.
        """
        for axis, component, value in (("Z", 1, 7.0), ("Y", 2, 0.0),
                                       ("X", 2, 0.0)):
            with self.subTest(axis=axis, component="XYZ"[component]):
                bores = dict(ALL_CENTRED)
                triple = list(bores[axis])
                triple[component] = value
                bores[axis] = tuple(triple)
                result = build(enclosure(bores))
                unchanged = (
                    result.succeeded
                    and result.bodies[0].measurement.face_count == FACES
                    and abs(result.bodies[0].measurement.volume
                            - closed_form()) <= 1e-6)
                self.assertFalse(
                    unchanged,
                    f"the {axis} bore's {'XYZ'[component]} at {value} left "
                    f"the part unchanged")


class ThePromptShowsWhatItAsksForTests(unittest.TestCase):
    """The adopted fix, checked by arithmetic rather than by string match.

    A1 and A3 measured that the RULE's wording is not the mechanism. What
    moved the defect was an EXAMPLE carrying a nonzero z, so what this pins
    is that such an example is present and correct -- not the sentence around
    it.
    """

    #: `+Z -- {"x": 30, "y": 15, "z": 0}` and its two siblings.
    LINE = re.compile(r"^\s*([+-][XYZ]) -- (\{[^}]*\})\s*$", re.MULTILINE)
    SIZE = re.compile(r"Through the centre of a (\d+) by (\d+) by (\d+) part")

    def setUp(self) -> None:
        self.text = system_prompt()

    def _example(self):
        size = self.SIZE.search(self.text)
        self.assertIsNotNone(
            size, "the prompt states no worked part size for its bore example")
        extents = tuple(float(n) for n in size.groups())
        triples = {axis: json.loads(body)
                   for axis, body in self.LINE.findall(self.text)}
        return extents, triples

    def test_the_prompt_works_a_bore_position_example(self) -> None:
        extents, triples = self._example()
        self.assertEqual(sorted(triples), ["+X", "+Y", "+Z"],
                         "the example must cover all three axes")
        self.assertEqual(extents, (60.0, 30.0, 30.0))

    def test_the_examples_arithmetic_is_right(self) -> None:
        """Each shown triple must obey the rule the prompt states."""
        extents, triples = self._example()
        centre = tuple(e / 2.0 for e in extents)
        for axis, triple in triples.items():
            along = AXIS_INDEX[axis[-1]]
            for j, key in enumerate("xyz"):
                with self.subTest(axis=axis, component=key):
                    if j == along:
                        self.assertEqual(triple[key], 0,
                                         "the along-axis component is the one "
                                         "the prompt writes as 0")
                    else:
                        self.assertEqual(triple[key], centre[j],
                                         "an across-axis component must be "
                                         "the middle of that extent")

    def test_not_every_shown_bore_position_zeroes_z(self) -> None:
        """The defect, stated as a property of the prompt's own examples.

        Before `2026-09-18.5` every hole position the prompt showed had z at
        0, because every example bore ran along +Z, and the live model copied
        that onto +Y and +X bores 9 times in 64 attempts. At least one shown
        position must carry a nonzero z, or the example teaches the defect.
        """
        _, triples = self._example()
        self.assertTrue(
            any(triple["z"] != 0 for triple in triples.values()),
            "every bore position the prompt shows has z at 0 -- this is the "
            "Stage 69 defect, and no wording of the rule compensated for it")

    def test_the_rule_is_stated_as_well_as_shown(self) -> None:
        """A1 measured that the rule ALONE is not enough; it is still needed."""
        self.assertIn("0 is only ever safe for the component along the axis",
                      self.text)


if __name__ == "__main__":
    unittest.main()
