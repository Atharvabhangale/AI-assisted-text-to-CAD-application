"""The V1 ``subtract`` feature across the local CAD pipeline.

Generic boolean subtraction (Section C.4): one target, one or more tool solids,
applied in ``tools`` order, target replaced in place, tools consumed. This is
the first stage in which the solid set both grows and shrinks, so the solid-set
rules of Section B.4 and rule S9 get as much attention here as the geometry.

Expected volumes are computed from geometry -- closed forms in the test, never
read back from the kernel. Topology counts are measured and recorded as backend
observations, not treated as contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from cad_core import validate
from cad_core.iges_export import export_iges, read_iges
from cad_core.local_cad import (
    GeometryOperationError,
    LocalCadResult,
    UnsupportedGeometryError,
    build_part,
)
from cad_core.model import (
    Box,
    Chamfer,
    Cylinder,
    EdgeSelector,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
)
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl, read_stl

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres.
TOLERANCE_MM = 1e-6

#: Volume, in cubic millimetres.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Linear deflection used by STL export and the render model.
LINEAR_DEFLECTION_MM = 0.01

#: How far a tessellated vertex may deviate from the true surface.
MESH_TOLERANCE_MM = LINEAR_DEFLECTION_MM + TOLERANCE_MM

#: Unit-length check for normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

#: Alignment required between a cavity-wall vertex normal and the exact inward
#: radial direction. A vertex normal averages adjacent facets, so it is only
#: approximately radial on a tessellated surface.
RADIAL_NORMAL_TOLERANCE = 1e-2

# --- the primary test geometry ---------------------------------------------
#
# A 100x60x10 plate at the origin, and a 20 mm cylinder tool that overshoots
# the plate in Z (base at z = -5, height 20) so the cut goes right through.

PLATE_SIZE = (100.0, 60.0, 10.0)
TOOL_DIAMETER = 20.0
TOOL_RADIUS = TOOL_DIAMETER / 2.0
TOOL_CENTRE = (20.0, 20.0)

PLATE_VOLUME = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
#: Removed material: the tool overshoots in Z, so what is removed is a cylinder
#: of the tool radius through the plate's full 10 mm thickness -- not the
#: tool's own 20 mm volume.
REMOVED_VOLUME = math.pi * TOOL_RADIUS**2 * PLATE_SIZE[2]
EXPECTED_VOLUME = PLATE_VOLUME - REMOVED_VOLUME

EXPECTED_MINIMUM = (0.0, 0.0, 0.0)
EXPECTED_MAXIMUM = (100.0, 60.0, 10.0)


def plate_feature() -> Dict[str, Any]:
    return {
        "id": "plate",
        "type": "box",
        "size": {"x": PLATE_SIZE[0], "y": PLATE_SIZE[1], "z": PLATE_SIZE[2]},
        "position": {"x": 0, "y": 0, "z": 0},
    }


def cylinder_feature(
    identifier: str,
    diameter: float = TOOL_DIAMETER,
    centre: tuple = TOOL_CENTRE,
    base_z: float = -5.0,
    height: float = 20.0,
    axis: str = "+Z",
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "type": "cylinder",
        "diameter": diameter,
        "height": height,
        "position": {"x": centre[0], "y": centre[1], "z": base_z},
        "axis": axis,
    }


def document(features: List[Dict[str, Any]], name: str = "cut-plate") -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": name,
        "features": features,
    }


def primary_document() -> Dict[str, Any]:
    return document(
        [
            plate_feature(),
            cylinder_feature("tool"),
            {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
        ]
    )


class SubtractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def build_document(self, doc: Dict[str, Any]) -> LocalCadResult:
        return build_part(self.part_from(doc))

    def build(self) -> LocalCadResult:
        return self.build_document(primary_document())

    def assertTripleAlmostEqual(
        self, actual: Any, expected: tuple, delta: float = TOLERANCE_MM
    ) -> None:
        values = (
            (actual.x, actual.y, actual.z) if hasattr(actual, "x") else tuple(actual)
        )
        for axis, (got, want) in enumerate(zip(values, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- primary subtraction ---------------------------------------------------


class TestPrimarySubtract(SubtractTestCase):
    """plate 100x60x10, one 20 mm cylinder tool at (20, 20) overshooting in Z."""

    def test_build_succeeds(self) -> None:
        self.assertIsInstance(self.build(), LocalCadResult)

    def test_result_is_a_single_valid_solid(self) -> None:
        result = self.build()
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertTrue(result.is_solid())
        self.assertTrue(result.shape.isValid())
        self.assertEqual(result.solid_count(), 1)

    def test_target_identity_is_preserved(self) -> None:
        self.assertEqual(self.build().feature_id, "plate")

    def test_volume_decreases_by_the_swept_cylinder(self) -> None:
        """Reference value from geometry: plate volume minus pi*r^2*t."""
        result = self.build()
        self.assertAlmostEqual(
            result.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertAlmostEqual(
            PLATE_VOLUME - result.volume(), REMOVED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_bounding_box_is_unchanged(self) -> None:
        box = self.build().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_the_cavity_exists_as_a_real_cylindrical_face(self) -> None:
        """Topology evidence, not a volume difference.

        The cavity must be a cylindrical face of the tool's diameter spanning
        the plate's full thickness -- which also proves the cut went through
        rather than stopping inside.
        """
        result = self.build()
        walls = [
            face for face in result.shape.Faces() if face.geomType() == "CYLINDER"
        ]
        self.assertEqual(len(walls), 1)
        wall = walls[0].BoundingBox()
        self.assertAlmostEqual(wall.xlen, TOOL_DIAMETER, delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall.ylen, TOOL_DIAMETER, delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall.zlen, PLATE_SIZE[2], delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall.zmin, 0.0, delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall.zmax, PLATE_SIZE[2], delta=TOLERANCE_MM)

    def test_no_material_remains_on_the_tool_axis(self) -> None:
        """A probe cylinder inside the cavity must intersect nothing."""
        import cadquery as cq

        result = self.build()
        probe = cq.Solid.makeCylinder(
            TOOL_RADIUS * 0.5,
            PLATE_SIZE[2],
            pnt=cq.Vector(TOOL_CENTRE[0], TOOL_CENTRE[1], 0.0),
            dir=cq.Vector(0, 0, 1),
        )
        self.assertEqual(result.shape.intersect(probe).Solids(), [])

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        """Backend observation, recorded so a change is noticed.

        Six box planes plus one cylindrical wall; twelve box edges plus the
        cavity's two circles and the cylinder's seam; eight box corners plus
        two seam vertices.
        """
        result = self.build()
        self.assertEqual(len(result.shape.Faces()), 7)
        self.assertEqual(len(result.shape.Edges()), 15)
        self.assertEqual(len(result.shape.Vertices()), 10)

    def test_the_cut_is_not_merely_a_smaller_bounding_box(self) -> None:
        """The plate keeps its envelope; only interior material is gone."""
        plain = self.build_document(document([plate_feature()], name="plain"))
        cut = self.build()
        self.assertTripleAlmostEqual(
            cut.bounding_box().minimum,
            (
                plain.bounding_box().minimum.x,
                plain.bounding_box().minimum.y,
                plain.bounding_box().minimum.z,
            ),
        )
        self.assertLess(cut.volume(), plain.volume())
        self.assertGreater(len(cut.shape.Faces()), len(plain.shape.Faces()))


class TestSubtractAgreesWithThroughHole(SubtractTestCase):
    """A through-cylinder subtract and a through_hole must agree.

    Both routes now run through the same boolean machinery, so this is a
    regression guard on the Stage 10 refactor as much as a geometry check.
    """

    def through_hole_result(self) -> LocalCadResult:
        return self.build_document(
            document(
                [
                    plate_feature(),
                    {
                        "id": "bore",
                        "type": "through_hole",
                        "target": "plate",
                        "diameter": TOOL_DIAMETER,
                        "position": {"x": TOOL_CENTRE[0], "y": TOOL_CENTRE[1], "z": 0},
                        "axis": "+Z",
                    },
                ],
                name="drilled",
            )
        )

    def test_volumes_agree(self) -> None:
        self.assertAlmostEqual(
            self.build().volume(),
            self.through_hole_result().volume(),
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_topology_agrees(self) -> None:
        subtracted, drilled = self.build(), self.through_hole_result()
        for measure in ("Faces", "Edges", "Vertices"):
            with self.subTest(measure=measure):
                self.assertEqual(
                    len(getattr(subtracted.shape, measure)()),
                    len(getattr(drilled.shape, measure)()),
                )

    def test_both_keep_the_target_identity(self) -> None:
        self.assertEqual(self.build().feature_id, "plate")
        self.assertEqual(self.through_hole_result().feature_id, "plate")

    def test_the_two_routes_export_byte_identical_meshes(self) -> None:
        """The strongest available equivalence check.

        Both routes end in the same B-rep, so tessellating them produces the
        same binary STL byte for byte. A divergence in the shared boolean path
        would show up here even if volume and face counts still matched.
        """
        subtracted = export_stl(self.build(), self.tmp / "subtracted.stl")
        drilled = export_stl(self.through_hole_result(), self.tmp / "drilled.stl")
        self.assertEqual(
            hashlib.sha256(subtracted.read_bytes()).hexdigest(),
            hashlib.sha256(drilled.read_bytes()).hexdigest(),
        )


# --- multiple tools --------------------------------------------------------

SECOND_TOOL_DIAMETER = 10.0
SECOND_TOOL_CENTRE = (80.0, 40.0)
TWO_TOOL_VOLUME = (
    PLATE_VOLUME
    - REMOVED_VOLUME
    - math.pi * (SECOND_TOOL_DIAMETER / 2.0) ** 2 * PLATE_SIZE[2]
)


def two_tool_document(order: tuple = ("toolA", "toolB")) -> Dict[str, Any]:
    return document(
        [
            plate_feature(),
            cylinder_feature("toolA"),
            cylinder_feature(
                "toolB", diameter=SECOND_TOOL_DIAMETER, centre=SECOND_TOOL_CENTRE
            ),
            {
                "id": "cut",
                "type": "subtract",
                "target": "plate",
                "tools": list(order),
            },
        ],
        name="two-cuts",
    )


class TestMultipleTools(SubtractTestCase):
    def result(self, order: tuple = ("toolA", "toolB")) -> LocalCadResult:
        return self.build_document(two_tool_document(order))

    def test_one_solid_remains(self) -> None:
        result = self.result()
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)

    def test_target_identity_is_preserved(self) -> None:
        self.assertEqual(self.result().feature_id, "plate")

    def test_both_cuts_are_present_by_volume(self) -> None:
        self.assertAlmostEqual(
            self.result().volume(), TWO_TOOL_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_both_cuts_are_present_as_topology(self) -> None:
        """Two cylindrical faces, of the two requested diameters."""
        result = self.result()
        walls = sorted(
            (
                face
                for face in result.shape.Faces()
                if face.geomType() == "CYLINDER"
            ),
            key=lambda face: face.BoundingBox().xlen,
        )
        self.assertEqual(len(walls), 2)
        self.assertAlmostEqual(
            walls[0].BoundingBox().xlen, SECOND_TOOL_DIAMETER, delta=TOLERANCE_MM
        )
        self.assertAlmostEqual(
            walls[1].BoundingBox().xlen, TOOL_DIAMETER, delta=TOLERANCE_MM
        )

    def test_both_cavities_are_empty(self) -> None:
        import cadquery as cq

        result = self.build_document(two_tool_document())
        for centre, diameter in (
            (TOOL_CENTRE, TOOL_DIAMETER),
            (SECOND_TOOL_CENTRE, SECOND_TOOL_DIAMETER),
        ):
            probe = cq.Solid.makeCylinder(
                diameter / 4.0,
                PLATE_SIZE[2],
                pnt=cq.Vector(centre[0], centre[1], 0.0),
                dir=cq.Vector(0, 0, 1),
            )
            with self.subTest(centre=centre):
                self.assertEqual(result.shape.intersect(probe).Solids(), [])

    def test_bounding_box_is_unchanged(self) -> None:
        box = self.result().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        result = self.result()
        self.assertEqual(len(result.shape.Faces()), 8)
        self.assertEqual(len(result.shape.Edges()), 18)
        self.assertEqual(len(result.shape.Vertices()), 12)

    def test_both_tools_are_consumed(self) -> None:
        """Neither tool id survives: a later subtract cannot resolve them."""
        doc = two_tool_document()
        doc["features"].append(
            {"id": "again", "type": "subtract", "target": "plate", "tools": ["toolA"]}
        )
        self.assertIn("S6", validate(doc).rule_codes())

    def test_repeated_builds_measure_identically(self) -> None:
        measurements = set()
        for _ in range(5):
            result = self.result()
            box = result.bounding_box()
            measurements.add(
                (
                    round(result.volume(), 9),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    result.solid_count(),
                    len(result.shape.Faces()),
                    len(result.shape.Edges()),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- tool order ------------------------------------------------------------


def nested_document(order: tuple) -> Dict[str, Any]:
    """A 20 mm tool and a coaxial 10 mm tool inside it.

    Cutting the wide tool first leaves nothing for the narrow one to remove,
    so this pair is order-sensitive in *acceptance* while being
    order-insensitive in geometry.
    """
    return document(
        [
            plate_feature(),
            cylinder_feature("wide", diameter=TOOL_DIAMETER),
            cylinder_feature("narrow", diameter=TOOL_DIAMETER / 2.0),
            {
                "id": "cut",
                "type": "subtract",
                "target": "plate",
                "tools": list(order),
            },
        ],
        name="nested-tools",
    )


class TestToolOrder(SubtractTestCase):
    def test_geometry_is_order_independent_for_disjoint_tools(self) -> None:
        """Set algebra: A \\ (B u C) == (A \\ B) \\ C == (A \\ C) \\ B.

        Subtraction of a *set* of tools cannot depend on order, because the
        union of the tools is what is removed. Asserted here against the
        kernel so the claim is measured, not just argued.
        """
        forward = self.build_document(two_tool_document(("toolA", "toolB")))
        reverse = self.build_document(two_tool_document(("toolB", "toolA")))
        self.assertAlmostEqual(
            forward.volume(), reverse.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        for measure in ("Faces", "Edges", "Vertices"):
            with self.subTest(measure=measure):
                self.assertEqual(
                    len(getattr(forward.shape, measure)()),
                    len(getattr(reverse.shape, measure)()),
                )

    def test_one_shot_and_sequential_cutting_agree(self) -> None:
        """The engine cuts sequentially; the kernel also accepts many tools.

        Measured so that "in list order" is known to be a statement about
        error attribution rather than about geometry.
        """
        import cadquery as cq

        plate = cq.Solid.makeBox(*PLATE_SIZE, pnt=cq.Vector(0, 0, 0))
        first = cq.Solid.makeCylinder(
            TOOL_RADIUS, 20.0, pnt=cq.Vector(*TOOL_CENTRE, -5.0), dir=cq.Vector(0, 0, 1)
        )
        second = cq.Solid.makeCylinder(
            SECOND_TOOL_DIAMETER / 2.0,
            20.0,
            pnt=cq.Vector(*SECOND_TOOL_CENTRE, -5.0),
            dir=cq.Vector(0, 0, 1),
        )
        one_shot = plate.cut(first, second)
        sequential = plate.cut(first).cut(second)
        self.assertEqual(len(one_shot.Solids()), 1)
        self.assertAlmostEqual(
            one_shot.Volume(), sequential.Volume(), delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(one_shot.Faces()), len(sequential.Faces()))
        self.assertAlmostEqual(
            self.build_document(two_tool_document()).volume(),
            one_shot.Volume(),
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_order_is_observable_when_one_tool_shadows_another(self) -> None:
        """Wide-then-narrow is refused; narrow-then-wide builds.

        Evidence that the engine really applies tools in list order: the same
        three solids and the same final geometry, with opposite outcomes.
        """
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(nested_document(("wide", "narrow")))
        message = str(caught.exception)
        self.assertIn("'narrow'", message)
        self.assertIn("remove no material", message)

        built = self.build_document(nested_document(("narrow", "wide")))
        self.assertEqual(built.solid_count(), 1)
        self.assertAlmostEqual(
            built.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_the_shadowed_order_still_reaches_the_same_geometry(self) -> None:
        """narrow-then-wide equals the wide tool alone, to the tolerance."""
        nested = self.build_document(nested_document(("narrow", "wide")))
        single = self.build()
        self.assertAlmostEqual(
            nested.volume(), single.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(nested.shape.Faces()), len(single.shape.Faces()))


# --- solid-set semantics ---------------------------------------------------


class TestSolidSetSemantics(SubtractTestCase):
    def test_the_modifier_id_never_names_a_solid(self) -> None:
        self.assertNotEqual(self.build().feature_id, "cut")

    def test_the_tool_id_is_no_longer_a_solid(self) -> None:
        self.assertNotEqual(self.build().feature_id, "tool")

    def test_a_consumed_tool_cannot_be_reused(self) -> None:
        doc = primary_document()
        doc["features"].append(
            {"id": "again", "type": "subtract", "target": "plate", "tools": ["tool"]}
        )
        result = validate(doc)
        self.assertFalse(result.valid)
        self.assertIn("S6", result.rule_codes())

    def test_a_consumed_tool_cannot_be_a_later_target(self) -> None:
        doc = primary_document()
        doc["features"].append(
            {
                "id": "bore",
                "type": "through_hole",
                "target": "tool",
                "diameter": 4,
                "position": {"x": 20, "y": 20, "z": 0},
            }
        )
        self.assertIn("S6", validate(doc).rule_codes())

    def test_the_target_cannot_be_one_of_its_own_tools(self) -> None:
        doc = document(
            [
                plate_feature(),
                cylinder_feature("tool"),
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["plate", "tool"],
                },
            ]
        )
        result = validate(doc)
        self.assertFalse(result.valid)
        self.assertIn("S15", result.rule_codes())

    def test_duplicate_tools_are_rejected(self) -> None:
        doc = document(
            [
                plate_feature(),
                cylinder_feature("tool"),
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["tool", "tool"],
                },
            ]
        )
        result = validate(doc)
        self.assertFalse(result.valid)
        self.assertIn("S15", result.rule_codes())

    def test_an_empty_tool_list_is_rejected(self) -> None:
        doc = document(
            [
                plate_feature(),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": []},
            ]
        )
        result = validate(doc)
        self.assertFalse(result.valid)
        self.assertIn("S14", result.rule_codes())

    def test_the_final_solid_set_holds_exactly_one_solid(self) -> None:
        result = self.build()
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_a_tool_may_be_created_before_the_target_is_modified(self) -> None:
        """Ordering within the history is free as long as references resolve."""
        doc = document(
            [
                plate_feature(),
                cylinder_feature("tool"),
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 6,
                    "position": {"x": 70, "y": 30, "z": 0},
                },
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            ],
            name="hole-then-cut",
        )
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        expected = EXPECTED_VOLUME - math.pi * 3.0**2 * PLATE_SIZE[2]
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)


class TestUnusedSolid(SubtractTestCase):
    """A constructive solid that nothing consumes is an error (rule S9)."""

    def unused_document(self) -> Dict[str, Any]:
        return document(
            [
                plate_feature(),
                cylinder_feature("tool"),
                cylinder_feature(
                    "spare", diameter=6.0, centre=(70.0, 30.0), base_z=0.0, height=5.0
                ),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            ],
            name="orphan",
        )

    def test_the_validator_rejects_it(self) -> None:
        result = validate(self.unused_document())
        self.assertFalse(result.valid)
        self.assertIn("S9", result.rule_codes())

    def test_the_engine_also_refuses_it(self) -> None:
        """Defence in depth: a hand-built Part bypassing the validator."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="orphan",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Cylinder(
                    id="tool",
                    diameter=TOOL_DIAMETER,
                    height=20.0,
                    position=Position(TOOL_CENTRE[0], TOOL_CENTRE[1], -5.0),
                ),
                Cylinder(
                    id="spare",
                    diameter=6.0,
                    height=5.0,
                    position=Position(70.0, 30.0, 0.0),
                ),
                Subtract(id="cut", target="plate", tools=("tool",)),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S9", message)
        self.assertIn("'spare'", message)
        self.assertIn("'plate'", message)


# --- rule E2 ---------------------------------------------------------------


class TestRuleE2(SubtractTestCase):
    def swallowing_document(self) -> Dict[str, Any]:
        return document(
            [
                plate_feature(),
                {
                    "id": "swallow",
                    "type": "box",
                    "size": {"x": 200, "y": 200, "z": 200},
                    "position": {"x": -50, "y": -50, "z": -50},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["swallow"],
                },
            ],
            name="swallowed",
        )

    def test_removing_everything_is_rejected(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.swallowing_document())
        message = str(caught.exception)
        self.assertIn("E2", message)
        self.assertIn("no material", message)
        self.assertIn("'cut'", message)
        self.assertIn("'swallow'", message)

    def test_the_empty_result_is_a_measured_kernel_state(self) -> None:
        """The kernel returns a compound holding zero solids -- and a volume.

        ``Volume()`` still answers 0.0 rather than failing, which is why the
        engine classifies on the solid count and not on volume.
        """
        import cadquery as cq

        plate = cq.Solid.makeBox(*PLATE_SIZE, pnt=cq.Vector(0, 0, 0))
        swallow = cq.Solid.makeBox(200, 200, 200, pnt=cq.Vector(-50, -50, -50))
        empty = plate.cut(swallow)
        self.assertEqual(len(empty.Solids()), 0)
        self.assertEqual(len(empty.Faces()), 0)
        self.assertAlmostEqual(empty.Volume(), 0.0, delta=VOLUME_TOLERANCE_MM3)

    def test_no_partial_geometry_escapes(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(self.swallowing_document())

    def test_e2_is_reported_at_the_tool_that_emptied_the_body(self) -> None:
        """With several tools, the message names the one that did it."""
        doc = document(
            [
                plate_feature(),
                cylinder_feature("first"),
                {
                    "id": "swallow",
                    "type": "box",
                    "size": {"x": 200, "y": 200, "z": 200},
                    "position": {"x": -50, "y": -50, "z": -50},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["first", "swallow"],
                },
            ],
            name="emptied-second",
        )
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(doc)
        message = str(caught.exception)
        self.assertIn("E2", message)
        self.assertIn("'swallow'", message)
        self.assertNotIn("'first'", message)


# --- rule E3 ---------------------------------------------------------------

BAR_SIZE = (100.0, 20.0, 10.0)


class TestRuleE3(SubtractTestCase):
    def splitting_document(self) -> Dict[str, Any]:
        return document(
            [
                {
                    "id": "bar",
                    "type": "box",
                    "size": {"x": BAR_SIZE[0], "y": BAR_SIZE[1], "z": BAR_SIZE[2]},
                    "position": {"x": 0, "y": 0, "z": 0},
                },
                cylinder_feature("splitter", diameter=40.0, centre=(50.0, 10.0)),
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "bar",
                    "tools": ["splitter"],
                },
            ],
            name="severed",
        )

    def test_a_cut_that_splits_the_body_is_rejected(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.splitting_document())
        message = str(caught.exception)
        self.assertIn("E3", message)
        self.assertIn("split", message)
        self.assertIn("2", message)
        self.assertIn("'cut'", message)

    def test_the_split_is_a_measured_kernel_result(self) -> None:
        """The two pieces come from the kernel, not from a special case."""
        import cadquery as cq

        bar = cq.Solid.makeBox(*BAR_SIZE, pnt=cq.Vector(0, 0, 0))
        splitter = cq.Solid.makeCylinder(
            20.0, 20.0, pnt=cq.Vector(50, 10, -5), dir=cq.Vector(0, 0, 1)
        )
        pieces = bar.cut(splitter)
        self.assertEqual(len(pieces.Solids()), 2)

    def test_no_component_is_selected_from_a_split_result(self) -> None:
        """The error must not be a quiet "pick the biggest piece"."""
        with self.assertRaises(GeometryOperationError):
            self.build_document(self.splitting_document())

    def test_an_intermediate_split_repaired_by_a_later_tool_is_accepted(self) -> None:
        """E3 is a property of what the *feature* leaves (Section E.2).

        The first tool severs the bar; the second removes one of the two
        pieces, so the subtract as a whole leaves a single connected solid.
        The expected volume is derived analytically below, not from the kernel.
        """
        doc = document(
            [
                {
                    "id": "bar",
                    "type": "box",
                    "size": {"x": BAR_SIZE[0], "y": BAR_SIZE[1], "z": BAR_SIZE[2]},
                    "position": {"x": 0, "y": 0, "z": 0},
                },
                cylinder_feature("splitter", diameter=40.0, centre=(50.0, 10.0)),
                {
                    "id": "right",
                    "type": "box",
                    "size": {"x": 60, "y": 40, "z": 20},
                    "position": {"x": 55, "y": -10, "z": -5},
                },
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "bar",
                    "tools": ["splitter", "right"],
                },
            ],
            name="split-then-trimmed",
        )
        result = self.build_document(doc)
        self.assertEqual(result.solid_count(), 1)
        self.assertTrue(result.is_solid())

        # What survives is the bar left of x = 55, minus the part of the
        # splitter disc that lies in it. With u = x - 50 and v = y - 10, the
        # removed region is {|v| <= 10, -20 <= u <= 5, u^2 + v^2 <= 400}. The
        # full 20 mm strip is covered while |u| <= sqrt(300); beyond that the
        # circle is narrower, giving the integral term below.
        radius, half_width, right_limit = 20.0, 10.0, 5.0
        breakpoint_u = math.sqrt(radius**2 - half_width**2)

        def circular_area(upper: float, lower: float) -> float:
            def antiderivative(u: float) -> float:
                chord = 0.5 * u * math.sqrt(radius**2 - u**2)
                return chord + 0.5 * radius**2 * math.asin(u / radius)

            return antiderivative(upper) - antiderivative(lower)

        full_strip = 2 * half_width * (right_limit + breakpoint_u)
        removed_area = full_strip + 2 * circular_area(-breakpoint_u, -radius)
        expected = (
            (50.0 + right_limit) * BAR_SIZE[1] - removed_area
        ) * BAR_SIZE[2]
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_the_same_pair_in_the_other_order_also_builds(self) -> None:
        """Trim first, then cut: no intermediate split, same final solid."""
        features = [
            {
                "id": "bar",
                "type": "box",
                "size": {"x": BAR_SIZE[0], "y": BAR_SIZE[1], "z": BAR_SIZE[2]},
                "position": {"x": 0, "y": 0, "z": 0},
            },
            cylinder_feature("splitter", diameter=40.0, centre=(50.0, 10.0)),
            {
                "id": "right",
                "type": "box",
                "size": {"x": 60, "y": 40, "z": 20},
                "position": {"x": 55, "y": -10, "z": -5},
            },
        ]
        forward = self.build_document(
            document(
                features
                + [
                    {
                        "id": "cut",
                        "type": "subtract",
                        "target": "bar",
                        "tools": ["splitter", "right"],
                    }
                ],
                name="forward",
            )
        )
        reverse = self.build_document(
            document(
                features
                + [
                    {
                        "id": "cut",
                        "type": "subtract",
                        "target": "bar",
                        "tools": ["right", "splitter"],
                    }
                ],
                name="reverse",
            )
        )
        self.assertAlmostEqual(
            forward.volume(), reverse.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(forward.solid_count(), reverse.solid_count())


# --- the unclassified no-op case -------------------------------------------


class TestNonOverlappingTool(SubtractTestCase):
    """V1 gives no rule for a tool that removes nothing; the engine refuses.

    Section C.4 lists only E2 and E3 for ``subtract``. E1 (a through_hole that
    misses) and E4 (a selector that matches nothing) show the specification
    treats a no-op modifier as an error wherever it says so -- but it does not
    say so here. Refusing is the narrowest reading, and no rule code is
    claimed for it.
    """

    def disjoint_document(self) -> Dict[str, Any]:
        return document(
            [
                plate_feature(),
                cylinder_feature("far", diameter=10.0, centre=(500.0, 500.0)),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["far"]},
            ],
            name="disjoint",
        )

    def test_a_disjoint_tool_is_refused(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.disjoint_document())
        message = str(caught.exception)
        self.assertIn("remove no material", message)
        self.assertIn("'far'", message)
        self.assertIn("'cut'", message)

    def test_the_refusal_claims_no_rule_code(self) -> None:
        """No E-rule covers this, so the message must not pretend one does."""
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.disjoint_document())
        message = str(caught.exception)
        self.assertIn("does not classify", message)
        for invented in ("rule E1", "rule E2", "rule E3", "rule E4", "rule E5"):
            with self.subTest(invented=invented):
                self.assertNotIn(invented, message)

    def test_the_kernel_would_have_accepted_it_silently(self) -> None:
        """Why refusing matters: nothing downstream would notice.

        A disjoint cut returns the target's volume to the last bit, so a
        silent no-op would be invisible in every measurement this project
        makes.
        """
        import cadquery as cq

        plate = cq.Solid.makeBox(*PLATE_SIZE, pnt=cq.Vector(0, 0, 0))
        far = cq.Solid.makeCylinder(
            5.0, 10.0, pnt=cq.Vector(500, 500, 0), dir=cq.Vector(0, 0, 1)
        )
        unchanged = plate.cut(far)
        self.assertEqual(len(unchanged.Solids()), 1)
        self.assertEqual(unchanged.Volume(), plate.Volume())
        self.assertEqual(len(unchanged.Faces()), len(plate.Faces()))

    def test_a_touching_tool_is_refused_too(self) -> None:
        """And here a no-op would not even be invisible: topology changes.

        A cylinder tangent to the +X face removes no volume, yet the kernel
        still imprints the contact -- the box comes back with a seventh face.
        Accepting no-ops would let a tool that means nothing geometrically
        change the result.
        """
        import cadquery as cq

        plate = cq.Solid.makeBox(*PLATE_SIZE, pnt=cq.Vector(0, 0, 0))
        tangent = cq.Solid.makeCylinder(
            10.0, 20.0, pnt=cq.Vector(110, 30, -5), dir=cq.Vector(0, 0, 1)
        )
        imprinted = plate.cut(tangent)
        self.assertEqual(imprinted.Volume(), plate.Volume())
        self.assertEqual(len(imprinted.Faces()), 7)
        self.assertEqual(len(plate.Faces()), 6)
        self.assertEqual(len(plate.intersect(tangent).Solids()), 0)

        doc = document(
            [
                plate_feature(),
                cylinder_feature("tangent", diameter=20.0, centre=(110.0, 30.0)),
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["tangent"],
                },
            ],
            name="tangent",
        )
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(doc)
        self.assertIn("remove no material", str(caught.exception))

    def test_overlap_is_decided_by_the_kernel(self) -> None:
        """A genuinely overlapping tool yields a solid intersection."""
        import cadquery as cq

        plate = cq.Solid.makeBox(*PLATE_SIZE, pnt=cq.Vector(0, 0, 0))
        tool = cq.Solid.makeCylinder(
            TOOL_RADIUS, 20.0, pnt=cq.Vector(*TOOL_CENTRE, -5.0), dir=cq.Vector(0, 0, 1)
        )
        common = plate.intersect(tool)
        self.assertEqual(len(common.Solids()), 1)
        self.assertAlmostEqual(
            common.Volume(), REMOVED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )


# --- defensive handling of inconsistent typed input ------------------------


class TestEvaluatorDefences(SubtractTestCase):
    """The validator guarantees S6/S14/S15; the evaluator still checks.

    None of these parts can come from ``validate()``. They are built by hand
    to prove the evaluator reports them instead of crashing or producing
    nonsense geometry.
    """

    def hand_built(self, subtract: Subtract, extra: tuple = ()) -> Part:
        return Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(Box(id="plate", size=Size(*PLATE_SIZE)),)
            + extra
            + (subtract,),
        )

    def tool(self, identifier: str = "tool") -> Cylinder:
        return Cylinder(
            id=identifier,
            diameter=TOOL_DIAMETER,
            height=20.0,
            position=Position(TOOL_CENTRE[0], TOOL_CENTRE[1], -5.0),
        )

    def test_an_unresolvable_target_is_reported(self) -> None:
        part = self.hand_built(
            Subtract(id="cut", target="absent", tools=("tool",)),
            extra=(self.tool(),),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S6", message)
        self.assertIn("'absent'", message)

    def test_an_unresolvable_tool_is_reported(self) -> None:
        part = self.hand_built(Subtract(id="cut", target="plate", tools=("ghost",)))
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S6", message)
        self.assertIn("'ghost'", message)

    def test_an_empty_tool_list_is_reported(self) -> None:
        part = self.hand_built(Subtract(id="cut", target="plate", tools=()))
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        self.assertIn("S14", str(caught.exception))

    def test_the_target_listed_as_its_own_tool_is_reported(self) -> None:
        part = self.hand_built(
            Subtract(id="cut", target="plate", tools=("plate",)),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        self.assertIn("S15", str(caught.exception))

    def test_a_duplicated_tool_is_reported(self) -> None:
        part = self.hand_built(
            Subtract(id="cut", target="plate", tools=("tool", "tool")),
            extra=(self.tool(),),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S15", message)
        self.assertIn("twice", message)

    def test_a_failed_subtract_consumes_nothing(self) -> None:
        """A refusal must not leave the solid set half-modified.

        The engine is asked for a two-tool subtract whose second tool is
        unresolvable. Nothing is written back, so the failure is reported
        against the subtract rather than surfacing later as a rule S9 error
        about a solid set that was quietly mutated.
        """
        part = self.hand_built(
            Subtract(id="cut", target="plate", tools=("tool", "ghost")),
            extra=(self.tool(),),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("'ghost'", message)
        self.assertIn("S6", message)
        self.assertNotIn("S9", message)

    def test_a_raw_dictionary_cannot_invoke_the_build_api(self) -> None:
        with self.assertRaises(TypeError):
            build_part(primary_document())  # type: ignore[arg-type]

    def test_a_fillet_after_a_subtract_now_builds(self) -> None:
        """Stage 13 added 'fillet', which sees whatever the subtract left."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", position=Position(0.0, 0.0, 0.0), size=Size(*PLATE_SIZE)),
                self.tool(),
                Subtract(id="cut", target="plate", tools=("tool",)),
                Fillet(
                    id="round",
                    target="plate",
                    radius=2.0,
                    edges=EdgeSelector(select="axis_parallel", axis="Z"),
                ),
            ),
        )
        result = build_part(part)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_a_chamfer_after_a_subtract_now_builds(self) -> None:
        """Stage 14 implemented 'chamfer', the last V1 feature."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", position=Position(0.0, 0.0, 0.0), size=Size(*PLATE_SIZE)),
                self.tool(),
                Subtract(id="cut", target="plate", tools=("tool",)),
                Chamfer(
                    id="bevel",
                    target="plate",
                    distance=2.0,
                    edges=EdgeSelector(select="axis_parallel", axis="X"),
                ),
            ),
        )
        result = build_part(part)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")


# --- STEP ------------------------------------------------------------------


class TestSubtractStep(SubtractTestCase):
    def test_step_round_trip(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "cut.step"))
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_step_preserves_the_cavity_as_topology(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "cut.step"))
        walls = [
            face
            for face in imported.shape.Faces()
            if face.geomType() == "CYLINDER"
        ]
        self.assertEqual(len(walls), 1)
        self.assertEqual(len(imported.shape.Faces()), 7)
        self.assertEqual(len(imported.shape.Edges()), 15)
        self.assertEqual(len(imported.shape.Vertices()), 10)

    def test_step_round_trips_are_geometrically_deterministic(self) -> None:
        measurements = set()
        digests = set()
        for index in range(3):
            written = export_step(self.build(), self.tmp / f"s{index}.step")
            imported = read_step(written)
            box = imported.bounding_box()
            measurements.add(
                (
                    imported.solid_count(),
                    round(imported.volume(), 6),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    len(imported.shape.Faces()),
                )
            )
            digests.add(hashlib.sha256(written.read_bytes()).hexdigest())
        self.assertEqual(len(measurements), 1)
        # Measured: STEP bytes differ per export (header timestamp and an
        # incrementing translator instance number), so byte identity is not
        # asserted -- geometric identity is.
        self.assertEqual(len(digests), 3)


# --- IGES ------------------------------------------------------------------


class TestSubtractIges(SubtractTestCase):
    def test_iges_round_trip(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "cut.igs"))
        self.assertEqual(imported.shape_type(), "Solid")
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_iges_preserves_the_measured_topology(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "cut.igs"))
        self.assertEqual(imported.face_count(), 7)
        self.assertEqual(imported.edge_count(), 15)
        self.assertEqual(imported.vertex_count(), 10)

    def test_iges_round_trips_are_geometrically_deterministic(self) -> None:
        measurements = set()
        for index in range(3):
            imported = read_iges(export_iges(self.build(), self.tmp / f"i{index}.igs"))
            box = imported.bounding_box()
            measurements.add(
                (
                    imported.shape_type(),
                    imported.solid_count(),
                    round(imported.volume(), 6),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    imported.face_count(),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- STL -------------------------------------------------------------------


class TestSubtractStl(SubtractTestCase):
    def test_stl_is_structurally_valid(self) -> None:
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "cut.stl"))
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        self.assertGreater(facts.declared_triangles, 0)

    def test_the_cavity_adds_triangles(self) -> None:
        plain = read_stl(
            export_stl(
                self.build_document(document([plate_feature()], name="plain")),
                self.tmp / "plain.stl",
            )
        )
        cut = read_stl(export_stl(self.build(), self.tmp / "cut.stl"))
        self.assertEqual(plain.triangle_count(), 12)
        self.assertGreater(cut.triangle_count(), plain.triangle_count())

    def test_mesh_bounds_approximate_the_brep(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "cut.stl"))
        box = mesh.bounding_box()
        self.assertTripleAlmostEqual(
            box.minimum, EXPECTED_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.maximum, EXPECTED_MAXIMUM, delta=MESH_TOLERANCE_MM
        )

    def test_the_cylindrical_cut_is_represented_in_the_mesh(self) -> None:
        """Nodes must sit on the cavity wall, and none inside the cavity."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "cut.stl"))
        on_wall = [
            node
            for node in mesh.nodes()
            if abs(
                math.hypot(node[0] - TOOL_CENTRE[0], node[1] - TOOL_CENTRE[1])
                - TOOL_RADIUS
            )
            <= MESH_TOLERANCE_MM
        ]
        self.assertGreater(len(on_wall), 8)
        for node in mesh.nodes():
            radial = math.hypot(node[0] - TOOL_CENTRE[0], node[1] - TOOL_CENTRE[1])
            self.assertGreaterEqual(radial, TOOL_RADIUS - MESH_TOLERANCE_MM)

    def test_repeated_stl_exports_are_equivalent(self) -> None:
        signatures = set()
        digests = set()
        for index in range(3):
            written = export_stl(self.build(), self.tmp / f"m{index}.stl")
            self.assertTrue(binary_stl_facts(written).is_structurally_consistent)
            mesh = read_stl(written)
            signatures.add((mesh.triangle_count(), mesh.node_count()))
            digests.add(hashlib.sha256(written.read_bytes()).hexdigest())
        self.assertEqual(len(signatures), 1)
        self.assertEqual(len(digests), 1)  # measured: byte-identical


# --- render model ----------------------------------------------------------


class TestSubtractRenderModel(SubtractTestCase):
    def model(self):
        return build_render_model(self.build())

    def test_render_model_is_produced(self) -> None:
        model = self.model()
        self.assertEqual(model.feature_id, "plate")
        self.assertGreater(model.vertex_count(), 0)
        self.assertGreater(model.triangle_count(), 0)

    def test_normals_are_finite_and_unit_length(self) -> None:
        for index, normal in enumerate(self.model().normals):
            with self.subTest(normal=index):
                for component in normal:
                    self.assertTrue(math.isfinite(component))
                length = math.sqrt(sum(component**2 for component in normal))
                self.assertAlmostEqual(length, 1.0, delta=NORMAL_TOLERANCE)

    def test_cavity_wall_normals_point_into_the_cavity(self) -> None:
        """A surface normal points out of the material.

        On an internal cylindrical surface that direction is towards the tool
        axis, so "outward" means inward here. Vertices on the cavity's top and
        bottom rings belong to the planar faces and carry +/-Z normals, so they
        are separated out by their Z component rather than assumed away.
        """
        model = self.model()
        wall, rings = [], []
        for vertex, normal in zip(model.vertices, model.normals):
            radial = math.hypot(
                vertex[0] - TOOL_CENTRE[0], vertex[1] - TOOL_CENTRE[1]
            )
            if abs(radial - TOOL_RADIUS) > MESH_TOLERANCE_MM:
                continue
            outward = (
                (vertex[0] - TOOL_CENTRE[0]) / radial,
                (vertex[1] - TOOL_CENTRE[1]) / radial,
            )
            dot = outward[0] * normal[0] + outward[1] * normal[1]
            (rings if abs(normal[2]) > 0.5 else wall).append(dot)

        self.assertGreater(len(wall), 0)
        self.assertGreater(len(rings), 0)
        for dot in wall:
            self.assertLess(dot, -1.0 + RADIAL_NORMAL_TOLERANCE)
        for dot in rings:
            self.assertAlmostEqual(dot, 0.0, delta=RADIAL_NORMAL_TOLERANCE)

    def test_outer_planar_normals_remain_outward(self) -> None:
        model = self.model()
        expectations = {
            0: ((0.0, 0.0, -1.0), lambda v: abs(v[2]) < TOLERANCE_MM),
            1: ((0.0, 0.0, 1.0), lambda v: abs(v[2] - PLATE_SIZE[2]) < TOLERANCE_MM),
            2: ((-1.0, 0.0, 0.0), lambda v: abs(v[0]) < TOLERANCE_MM),
            3: ((1.0, 0.0, 0.0), lambda v: abs(v[0] - PLATE_SIZE[0]) < TOLERANCE_MM),
        }
        for key, (expected, predicate) in expectations.items():
            matches = [
                normal
                for vertex, normal in zip(model.vertices, model.normals)
                if predicate(vertex)
                and abs(
                    sum(a * b for a, b in zip(normal, expected)) - 1.0
                )
                < NORMAL_TOLERANCE
            ]
            with self.subTest(face=key):
                self.assertGreater(len(matches), 0)

    def test_winding_is_outward_for_every_triangle(self) -> None:
        """Verified against the solid itself, not a centroid heuristic.

        A plate with a cavity is not star-shaped, so outwardness is tested by
        stepping along each triangle's normal and classifying that point with
        the kernel.
        """
        import cadquery as cq
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_State

        result = self.build()
        model = build_render_model(result)
        classifier = BRepClass3d_SolidClassifier(result.shape.wrapped)
        step = 1e-3
        for position, (a, b, c) in enumerate(model.triangles):
            va, vb, vc = (model.vertices[i] for i in (a, b, c))
            ux, uy, uz = (vb[i] - va[i] for i in range(3))
            vx, vy, vz = (vc[i] - va[i] for i in range(3))
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length == 0.0:  # pragma: no cover - none expected
                continue
            centroid = [(va[i] + vb[i] + vc[i]) / 3 for i in range(3)]
            outside = cq.Vector(
                centroid[0] + step * nx / length,
                centroid[1] + step * ny / length,
                centroid[2] + step * nz / length,
            )
            classifier.Perform(outside.toPnt(), 1e-7)
            with self.subTest(triangle=position):
                self.assertEqual(classifier.State(), TopAbs_State.TopAbs_OUT)

    def test_render_bounds_are_the_specification_envelope(self) -> None:
        model = self.model()
        self.assertTripleAlmostEqual(
            model.bounds.minimum, EXPECTED_MINIMUM, delta=TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            model.bounds.maximum, EXPECTED_MAXIMUM, delta=TOLERANCE_MM
        )

    def test_render_model_serializes_and_is_deterministic(self) -> None:
        payloads = {
            json.dumps(build_render_model(self.build()).to_dict(), sort_keys=True)
            for _ in range(3)
        }
        self.assertEqual(len(payloads), 1)
        self.assertEqual(json.loads(next(iter(payloads)))["feature_id"], "plate")


# --- determinism of the build itself ---------------------------------------


class TestDeterminism(SubtractTestCase):
    def test_repeated_builds_measure_identically(self) -> None:
        measurements = set()
        for _ in range(5):
            result = self.build()
            box = result.bounding_box()
            measurements.add(
                (
                    round(result.volume(), 9),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    result.solid_count(),
                    len(result.shape.Faces()),
                    len(result.shape.Edges()),
                    len(result.shape.Vertices()),
                )
            )
        self.assertEqual(len(measurements), 1)

    def test_each_build_returns_a_fresh_object(self) -> None:
        self.assertIsNot(self.build(), self.build())
        self.assertIsNot(self.build().shape, self.build().shape)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
