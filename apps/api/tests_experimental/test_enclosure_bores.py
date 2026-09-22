"""Three bores make six openings: the spatial rule the kernel decides.

Stage 67 measured the golden six-plate enclosure over 80 live calls on
`claude-haiku-4-5-20251001` across nine prompt arms. The full record is in
`docs/evaluation-baselines/stage67-golden-spatial/`. It adopted NO prompt
change: every arm that improved the spatial structure regressed something
else, and the best of them regressed the plate thickness significantly
(Fisher exact p = 0.0013). The prompt is still `2026-09-18.3`.

What the stage leaves behind is this module, and it pins two things.

1. THE SPATIAL RULE, in geometry. A through_hole passes all the way
   through, so ONE bore opens the two walls on its centreline: the
   canonical enclosure is three bores, not six. The kernel states it as
   12 planar faces (6 outer, 6 inner) plus two cylindrical faces per bore
   = 18 faces and 42 edges.

2. THAT THE SUCCESS CRITERION MUST INCLUDE THE PLATE THICKNESS. Stage 66
   scored a one-bore build "correct" because its criterion was envelope
   plus targeting. Stage 67 fixed that and then made the same class of
   mistake one level down: its own criterion derived the closed form from
   whatever thickness the model chose, so 16 parts built from 4 mm plate
   scored correct against a request that says 5. A criterion that grades a
   part against its own answer cannot fail it.
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

#: The canonical enclosure the golden request describes.
ENVELOPE = (40.0, 20.0, 20.0)
#: 6 outer + 6 inner planar faces, plus two cylindrical faces per bore.
FACES, EDGES = 18, 42


def _plate(identifier, size, position=(0.0, 0.0, 0.0)):
    return {"id": identifier, "type": "box",
            "parameters": {"x": size[0], "y": size[1], "z": size[2],
                           "position": {"x": position[0], "y": position[1],
                                        "z": position[2]}}}


def _bore(identifier, target, axis, position, diameter=8.0):
    return {"id": identifier, "type": "through_hole", "target": target,
            "parameters": {"diameter": diameter, "axis": axis,
                           "position": {"x": position[0], "y": position[1],
                                        "z": position[2]}}}


def enclosure(thickness: float, bores: int) -> dict:
    """The golden enclosure as a plan, with `bores` axial cuts."""
    t = thickness
    x, y, z = ENVELOPE
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
    axial = [
        _bore("hole_z", "base", "+Z", (x / 2, y / 2, 0.0)),
        _bore("hole_y", "base", "+Y", (x / 2, 0.0, z / 2)),
        _bore("hole_x", "base", "+X", (0.0, y / 2, z / 2)),
    ]
    operations.extend(axial[:bores])
    return {"status": "generated", "operations": operations}


def shell_volume(t: float) -> float:
    x, y, z = ENVELOPE
    return x * y * z - (x - 2 * t) * (y - 2 * t) * (z - 2 * t)


def build(plan: dict):
    backend = resolve_backend()
    parsed = parse_plan_text(json.dumps(plan))
    verdict = validate_plan(parsed)
    if not verdict.valid:
        raise AssertionError([(p.code, p.message) for p in verdict.problems])
    return execute_plan(parsed, backend=backend)


class TheSpatialRuleTests(unittest.TestCase):
    """One bore opens two walls, so three bores make the six openings."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    def test_three_bores_open_six_walls(self) -> None:
        """THE rule. Three axial cuts, six pierced walls, and the kernel agrees.

        18 faces is 12 planar plus two cylindrical per bore. If a future
        change made a bore open only one wall, the face count would drop and
        this fails.
        """
        execution = build(enclosure(4.0, bores=3))
        self.assertTrue(execution.succeeded,
                        execution.failure and execution.failure.message)
        m = execution.bodies[0].measurement
        self.assertEqual(m.solid_count, 1)
        self.assertEqual(m.face_count, FACES)
        self.assertEqual(m.edge_count, EDGES)
        self.assertEqual(
            tuple(round(hi - lo, 6) for lo, hi in zip(m.minimum, m.maximum)),
            ENVELOPE)
        self.assertEqual(m.face_count - 12, 6, "two cylindrical faces per bore")

    def test_the_volume_is_the_closed_form(self) -> None:
        """Measured against arithmetic, not against a remembered number.

        The three centrelines meet only inside the cavity, so no removed
        volume is counted twice.
        """
        t, r = 4.0, 4.0
        execution = build(enclosure(t, bores=3))
        expected = shell_volume(t) - 3 * (2 * math.pi * r * r * t)
        self.assertAlmostEqual(execution.bodies[0].measurement.volume,
                               expected, places=6)
        # The value eight live model-generated plans were verified against.
        self.assertAlmostEqual(expected, 10185.628421021519, places=6)

    def test_fewer_bores_is_a_different_part(self) -> None:
        """One bore is a valid, one-solid enclosure -- and the wrong answer.

        Stage 66's weaker criterion accepted exactly this. Two cylindrical
        faces instead of six.
        """
        m = build(enclosure(4.0, bores=1)).bodies[0].measurement
        self.assertEqual(m.solid_count, 1)
        self.assertNotEqual(m.face_count, FACES)
        self.assertEqual(m.face_count - 12, 2)

    def test_a_second_coaxial_bore_removes_nothing(self) -> None:
        """A duplicate cut on one centreline is refused, not silently ignored.

        Failure mode F of the Stage 67 taxonomy, seen on 9 live attempts.
        """
        plan = enclosure(4.0, bores=3)
        plan["operations"].append(
            _bore("hole_z_again", "base", "+Z", (20.0, 10.0, 0.0)))
        parsed = parse_plan_text(json.dumps(plan))
        self.assertTrue(validate_plan(parsed).valid,
                        "a duplicate bore is a GEOMETRIC error, not a plan error")
        execution = execute_plan(parsed, backend=resolve_backend())
        self.assertFalse(execution.succeeded,
                         "a cut that removes nothing must be reported")

    def test_an_unrotated_end_plate_is_not_a_wall(self) -> None:
        """Failure mode D: the square end plate written with t on the wrong axis.

        `20 x 20 x t` at x=0 and x=20 is two slabs lying in the floor, so the
        +X bore finds no material. This is the one failure mode still live on
        the committed prompt, pinned here as geometry so it stays legible.
        """
        plan = enclosure(4.0, bores=3)
        for op in plan["operations"]:
            if op["id"] in ("left", "right"):
                op["parameters"].update(x=20.0, y=20.0, z=4.0)
        plan["operations"][4]["parameters"]["position"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        plan["operations"][5]["parameters"]["position"] = {"x": 20.0, "y": 0.0, "z": 0.0}
        parsed = parse_plan_text(json.dumps(plan))
        self.assertTrue(validate_plan(parsed).valid)
        execution = execute_plan(parsed, backend=resolve_backend())
        self.assertFalse(execution.succeeded,
                         "an unrotated end plate leaves the +X bore no material")


class TheRequestedPartIsFiveMillimetrePlateTests(unittest.TestCase):
    """The golden request says `40*20*5` plates: the thickness is 5, not 4.

    Measured: on prompt 2026-09-18.3 the live model read the thickness as 5 on
    5 of 8 attempts; on the Stage 67 candidate it read 4 on 16 of 16. The
    candidate was rejected for exactly this, and these tests keep the
    distinction visible so no future criterion quietly grades a 4 mm part
    against a 4 mm closed form.
    """

    #: The established closed form for the golden request, from the
    #: deterministic reader and reproduced by a live model-generated plan.
    CANONICAL_VOLUME = 11492.035526276899

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    def test_the_requested_thickness_gives_the_established_closed_form(self) -> None:
        t, r = 5.0, 4.0
        expected = shell_volume(t) - 3 * (2 * math.pi * r * r * t)
        self.assertAlmostEqual(expected, self.CANONICAL_VOLUME, places=6)
        m = build(enclosure(t, bores=3)).bodies[0].measurement
        self.assertAlmostEqual(m.volume, self.CANONICAL_VOLUME, places=6)
        self.assertEqual((m.solid_count, m.face_count, m.edge_count),
                         (1, FACES, EDGES))

    def test_a_four_millimetre_part_is_topologically_identical(self) -> None:
        """Why the thickness must be graded separately, and cannot be inferred.

        A 4 mm enclosure is one solid with the same 18 faces, 42 edges and the
        same 40x20x20 envelope. Every structural check passes; only the volume
        distinguishes it, and only against the REQUESTED thickness.
        """
        four = build(enclosure(4.0, bores=3)).bodies[0].measurement
        five = build(enclosure(5.0, bores=3)).bodies[0].measurement
        self.assertEqual((four.solid_count, four.face_count, four.edge_count),
                         (five.solid_count, five.face_count, five.edge_count))
        self.assertEqual(
            tuple(round(hi - lo, 6) for lo, hi in zip(four.minimum, four.maximum)),
            tuple(round(hi - lo, 6) for lo, hi in zip(five.minimum, five.maximum)))
        self.assertNotAlmostEqual(four.volume, five.volume, places=3)
        self.assertAlmostEqual(four.volume, 10185.628421021519, places=6)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
