"""The V1 ``through_hole`` modifier across the local CAD pipeline.

First stage with a multi-feature history, so this covers the solid-set
semantics of Section B.4 as well as the geometry of Section C.3 and the
geometric rules E1 and E3 -- then carries a drilled part through STEP, IGES,
STL and the render model.

Expected volumes are computed from geometry (box volume minus pi*r^2*t), never
read back from the kernel. Topology counts are measured and recorded as
backend observations, not treated as contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

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
    ThroughHole,
)
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl, read_stl

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres.
TOLERANCE_MM = 1e-6

#: Volume, in cubic millimetres. The drilled volume is irrational, so
#: agreement with the closed form is limited by double precision, not geometry.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Linear deflection used by STL export and the render model.
LINEAR_DEFLECTION_MM = 0.01

#: How far a tessellated vertex may deviate from the true surface.
MESH_TOLERANCE_MM = LINEAR_DEFLECTION_MM + TOLERANCE_MM

#: Unit-length check for normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

#: Alignment required between a hole-wall vertex normal and the exact inward
#: radial direction. A vertex normal averages adjacent facets, so it is only
#: approximately radial on a tessellated surface.
RADIAL_NORMAL_TOLERANCE = 1e-2

# --- the primary test geometry --------------------------------------------

PLATE_SIZE = (100.0, 60.0, 10.0)
PLATE_POSITION = (0.0, 0.0, 0.0)
HOLE_DIAMETER = 20.0
HOLE_RADIUS = HOLE_DIAMETER / 2.0
HOLE_CENTRE = (20.0, 20.0)

PLATE_VOLUME = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
#: Removed material: a cylinder of the hole radius through the 10 mm thickness.
REMOVED_VOLUME = math.pi * HOLE_RADIUS**2 * PLATE_SIZE[2]
EXPECTED_VOLUME = PLATE_VOLUME - REMOVED_VOLUME

EXPECTED_MINIMUM = (0.0, 0.0, 0.0)
EXPECTED_MAXIMUM = (100.0, 60.0, 10.0)


def plate_document(**overrides: Any) -> Dict[str, Any]:
    """A 100x60x10 plate at the origin with one 20 mm +Z hole at (20, 20)."""
    hole: Dict[str, Any] = {
        "id": "bore",
        "type": "through_hole",
        "target": "plate",
        "diameter": HOLE_DIAMETER,
        "position": {"x": HOLE_CENTRE[0], "y": HOLE_CENTRE[1], "z": 0},
        "axis": "+Z",
    }
    hole.update(overrides.pop("hole", {}))
    document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "drilled-plate",
        "features": [
            {
                "id": "plate",
                "type": "box",
                "size": {
                    "x": PLATE_SIZE[0],
                    "y": PLATE_SIZE[1],
                    "z": PLATE_SIZE[2],
                },
                "position": {
                    "x": PLATE_POSITION[0],
                    "y": PLATE_POSITION[1],
                    "z": PLATE_POSITION[2],
                },
            },
            hole,
        ],
    }
    document.update(overrides)
    return document


class ThroughHoleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def part_from(self, document: Dict[str, Any]) -> Part:
        result = validate(document)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def build(self, **overrides: Any) -> LocalCadResult:
        return build_part(self.part_from(plate_document(**overrides)))

    def assertTripleAlmostEqual(
        self, actual: Any, expected: tuple, delta: float = TOLERANCE_MM
    ) -> None:
        values = (
            (actual.x, actual.y, actual.z) if hasattr(actual, "x") else tuple(actual)
        )
        for axis, (got, want) in enumerate(zip(values, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- core geometry ---------------------------------------------------------


class TestPrimaryThroughHole(ThroughHoleTestCase):
    """plate 100x60x10 at origin, 20 mm hole at (20, 20), axis +Z."""

    def test_build_succeeds(self) -> None:
        self.assertIsInstance(self.build(), LocalCadResult)

    def test_result_is_a_solid(self) -> None:
        """A boolean returns a compound; the single solid must be unwrapped."""
        result = self.build()
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertTrue(result.is_solid())

    def test_result_is_valid(self) -> None:
        self.assertTrue(self.build().shape.isValid())

    def test_solid_count_is_one(self) -> None:
        self.assertEqual(self.build().solid_count(), 1)

    def test_volume_decreases_by_the_drilled_cylinder(self) -> None:
        """Expected value from geometry: plate volume minus pi*r^2*t."""
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

    def test_hole_passes_completely_through_the_thickness(self) -> None:
        """Both faces must be pierced, so the wall spans the full 10 mm.

        Checked by intersecting the result with a thin probe cylinder on the
        hole axis: if the hole were blind, material would remain somewhere
        along the thickness.
        """
        import cadquery as cq

        result = self.build()
        probe = cq.Solid.makeCylinder(
            HOLE_RADIUS * 0.5,
            PLATE_SIZE[2],
            pnt=cq.Vector(HOLE_CENTRE[0], HOLE_CENTRE[1], PLATE_POSITION[2]),
            dir=cq.Vector(0, 0, 1),
        )
        leftover = result.shape.intersect(probe)
        self.assertEqual(leftover.Solids(), [])

    def test_hole_diameter_is_as_specified(self) -> None:
        """Measured from the cylindrical wall face's own bounding box."""
        result = self.build()
        walls = [
            face
            for face in result.shape.Faces()
            if face.geomType() == "CYLINDER"
        ]
        self.assertEqual(len(walls), 1)
        wall_box = walls[0].BoundingBox()
        self.assertAlmostEqual(wall_box.xlen, HOLE_DIAMETER, delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall_box.ylen, HOLE_DIAMETER, delta=TOLERANCE_MM)
        self.assertAlmostEqual(wall_box.zlen, PLATE_SIZE[2], delta=TOLERANCE_MM)

    def test_hole_is_centred_on_the_specified_position(self) -> None:
        result = self.build()
        wall = [f for f in result.shape.Faces() if f.geomType() == "CYLINDER"][0]
        wall_box = wall.BoundingBox()
        self.assertAlmostEqual(
            (wall_box.xmin + wall_box.xmax) / 2, HOLE_CENTRE[0], delta=TOLERANCE_MM
        )
        self.assertAlmostEqual(
            (wall_box.ymin + wall_box.ymax) / 2, HOLE_CENTRE[1], delta=TOLERANCE_MM
        )

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        """Recorded, not contractual.

        A plain box is 6 faces / 12 edges / 8 vertices. Drilling one hole adds
        the cylindrical wall and its two rim circles: OpenCascade reports
        7 / 15 / 10. Nothing outside this test relies on those numbers.
        """
        plain = build_part(
            self.part_from(
                {
                    "schema_version": "1.0.0",
                    "units": "mm",
                    "name": "plain",
                    "features": [
                        {
                            "id": "plate",
                            "type": "box",
                            "size": {"x": 100, "y": 60, "z": 10},
                        }
                    ],
                }
            )
        ).shape
        drilled = self.build().shape
        self.assertEqual(
            (len(plain.Faces()), len(plain.Edges()), len(plain.Vertices())),
            (6, 12, 8),
        )
        self.assertEqual(
            (len(drilled.Faces()), len(drilled.Edges()), len(drilled.Vertices())),
            (7, 15, 10),
        )

    def test_repeated_builds_measure_identically(self) -> None:
        part = self.part_from(plate_document())
        measurements = set()
        for _ in range(5):
            result = build_part(part)
            box = result.bounding_box()
            measurements.add(
                (
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    result.solid_count(),
                    result.volume(),
                )
            )
        self.assertEqual(len(measurements), 1, msg=f"builds diverged: {measurements}")


# --- solid-set semantics (Section B.4) ------------------------------------


class TestSolidSetSemantics(ThroughHoleTestCase):
    def test_target_identity_is_preserved(self) -> None:
        """The surviving solid keeps the target's id, not the hole's."""
        self.assertEqual(self.build().feature_id, "plate")

    def test_the_modifier_id_never_names_a_solid(self) -> None:
        result = self.build()
        self.assertNotEqual(result.feature_id, "bore")

    def test_exactly_one_solid_remains(self) -> None:
        self.assertEqual(self.build().solid_count(), 1)

    def test_several_holes_all_target_the_same_solid(self) -> None:
        """Section B.4's own example: a plate keeps its id through four holes."""
        document = plate_document()
        for index, (x, y) in enumerate(((80.0, 20.0), (20.0, 40.0), (80.0, 40.0))):
            document["features"].append(
                {
                    "id": f"bore{index}",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": HOLE_DIAMETER,
                    "position": {"x": x, "y": y, "z": 0},
                }
            )
        result = build_part(self.part_from(document))
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(
            result.volume(),
            PLATE_VOLUME - 4 * REMOVED_VOLUME,
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_the_specification_example_plate_now_builds(self) -> None:
        """Section D's worked example: a plate with four 8 mm holes."""
        specification = Path(__file__).resolve().parents[3] / "docs" / "cad-specification.md"
        text = specification.read_text(encoding="utf-8")
        section = re.search(
            r"^## D\. Example specification$(.*?)^## E\.", text, re.MULTILINE | re.DOTALL
        )
        assert section is not None
        block = re.search(r"^```json$\n(.*?)^```$", section.group(1), re.MULTILINE | re.DOTALL)
        assert block is not None
        part = self.part_from(json.loads(block.group(1)))

        result = build_part(part)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertTrue(result.is_solid())
        expected = 100 * 60 * 10 - 4 * math.pi * 4.0**2 * 10
        self.assertAlmostEqual(result.volume(), expected, delta=VOLUME_TOLERANCE_MM3)

    def test_an_unresolvable_target_is_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                ThroughHole(
                    id="bore",
                    target="absent",
                    diameter=HOLE_DIAMETER,
                    position=Position(20.0, 20.0, 0.0),
                ),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("'absent'", message)
        self.assertIn("S6", message)

    def test_a_hole_cannot_target_another_holes_id(self) -> None:
        """A modifier id is not a solid, so targeting one must fail."""
        document = plate_document()
        document["features"].append(
            {
                "id": "second",
                "type": "through_hole",
                "target": "bore",
                "diameter": 5,
                "position": {"x": 50, "y": 30, "z": 0},
            }
        )
        # The validator already refuses this by rule S6.
        self.assertFalse(validate(document).valid)
        self.assertIn("S6", validate(document).rule_codes())


# --- all six axes ----------------------------------------------------------


class TestThroughHoleAxes(ThroughHoleTestCase):
    """A 60x60x60 cube at the origin, drilled 20 mm through its centre."""

    CUBE = 60.0
    CENTRE = 30.0

    def cube_document(self, axis: str, axial: float = 0.0) -> Dict[str, Any]:
        position = {"x": self.CENTRE, "y": self.CENTRE, "z": self.CENTRE}
        position["xyz"[("XYZ".index(axis[1]))]] = axial
        return {
            "schema_version": "1.0.0",
            "units": "mm",
            "name": "drilled-cube",
            "features": [
                {
                    "id": "block",
                    "type": "box",
                    "size": {"x": self.CUBE, "y": self.CUBE, "z": self.CUBE},
                },
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "block",
                    "diameter": HOLE_DIAMETER,
                    "position": position,
                    "axis": axis,
                },
            ],
        }

    def test_every_axis_drills_through(self) -> None:
        expected = self.CUBE**3 - math.pi * HOLE_RADIUS**2 * self.CUBE
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=axis):
                result = build_part(self.part_from(self.cube_document(axis)))
                self.assertTrue(result.is_solid())
                self.assertEqual(result.solid_count(), 1)
                self.assertAlmostEqual(
                    result.volume(), expected, delta=VOLUME_TOLERANCE_MM3
                )

    def test_bounding_box_is_unchanged_for_every_axis(self) -> None:
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=axis):
                box = build_part(
                    self.part_from(self.cube_document(axis))
                ).bounding_box()
                self.assertTripleAlmostEqual(box.minimum, (0.0, 0.0, 0.0))
                self.assertTripleAlmostEqual(
                    box.maximum, (self.CUBE, self.CUBE, self.CUBE)
                )

    def test_the_cut_runs_along_the_requested_axis(self) -> None:
        """The cylindrical wall spans the full cube along the hole axis only."""
        for axis in ("+X", "+Y", "+Z"):
            with self.subTest(axis=axis):
                shape = build_part(self.part_from(self.cube_document(axis))).shape
                wall = [f for f in shape.Faces() if f.geomType() == "CYLINDER"][0]
                wall_box = wall.BoundingBox()
                lengths = (wall_box.xlen, wall_box.ylen, wall_box.zlen)
                along = "XYZ".index(axis[1])
                for index, length in enumerate(lengths):
                    expected = self.CUBE if index == along else HOLE_DIAMETER
                    self.assertAlmostEqual(length, expected, delta=TOLERANCE_MM)

    def test_the_axial_component_of_position_does_not_affect_the_result(self) -> None:
        """Section C.3: only the two perpendicular components locate the hole.

        Two specifications differing solely in the axial coordinate must be
        geometrically equivalent.
        """
        for axis in ("+X", "+Y", "+Z"):
            with self.subTest(axis=axis):
                measurements = set()
                for axial in (-1000.0, 0.0, 17.5, 1000.0):
                    result = build_part(
                        self.part_from(self.cube_document(axis, axial=axial))
                    )
                    box = result.bounding_box()
                    measurements.add(
                        (
                            round(result.volume(), 9),
                            (box.minimum.x, box.minimum.y, box.minimum.z),
                            (box.maximum.x, box.maximum.y, box.maximum.z),
                        )
                    )
                self.assertEqual(len(measurements), 1)

    def test_opposite_axis_signs_cut_identically(self) -> None:
        """A measured consequence of the centreline being infinite.

        An infinite line through a point along '+Z' is the same line as along
        '-Z', so the two cut the same material. Unlike a cylinder, where the
        sign decides which way the solid extends.
        """
        for positive, negative in (("+X", "-X"), ("+Y", "-Y"), ("+Z", "-Z")):
            with self.subTest(pair=f"{positive}/{negative}"):
                up = build_part(self.part_from(self.cube_document(positive)))
                down = build_part(self.part_from(self.cube_document(negative)))
                self.assertAlmostEqual(
                    up.volume(), down.volume(), delta=VOLUME_TOLERANCE_MM3
                )
                for attribute in ("minimum", "maximum"):
                    a = getattr(up.bounding_box(), attribute)
                    b = getattr(down.bounding_box(), attribute)
                    self.assertTripleAlmostEqual(a, (b.x, b.y, b.z))


# --- E1: the centreline must intersect the target -------------------------


class TestRuleE1(ThroughHoleTestCase):
    def test_a_hole_through_the_material_succeeds(self) -> None:
        self.assertTrue(self.build().is_solid())

    def test_a_hole_that_just_misses_is_rejected(self) -> None:
        """Centreline just outside the plate: the wall would only graze it."""
        with self.assertRaises(GeometryOperationError) as caught:
            self.build(hole={"position": {"x": 20, "y": -0.5, "z": 0}})
        message = str(caught.exception)
        self.assertIn("E1", message)
        self.assertIn("does not intersect", message)

    def test_a_hole_far_outside_is_rejected(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build(hole={"position": {"x": 500, "y": 500, "z": 0}})
        self.assertIn("E1", str(caught.exception))

    def test_e1_is_not_treated_as_a_no_op(self) -> None:
        """The failure must be an error, never a silently unchanged solid."""
        for position in ({"x": 20, "y": -0.5, "z": 0}, {"x": -50, "y": -50, "z": 0}):
            with self.subTest(position=position):
                with self.assertRaises(GeometryOperationError):
                    self.build(hole={"position": position})

    def test_a_centreline_exactly_on_the_face_boundary(self) -> None:
        """Recorded behaviour for the degenerate on-edge case.

        A centreline lying exactly on the plate's y = 0 face is a boundary
        case; whichever way the kernel classifies it, the engine must either
        build one valid solid or refuse -- never return something broken.
        """
        try:
            result = self.build(hole={"position": {"x": 20, "y": 0, "z": 0}})
        except GeometryOperationError:
            return  # refused: acceptable and explicit
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)

    def test_the_error_names_the_feature_and_target(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build(hole={"position": {"x": 500, "y": 500, "z": 0}})
        message = str(caught.exception)
        self.assertIn("'bore'", message)
        self.assertIn("'plate'", message)


# --- E3: the result must stay one connected solid -------------------------


class TestRuleE3(ThroughHoleTestCase):
    def test_a_hole_that_splits_the_body_is_rejected(self) -> None:
        """A 40 mm hole across a 20 mm wide bar severs it in two.

        The bar spans y from 0 to 20; a hole of radius 20 centred at y = 10
        removes the full width over x in [30, 70], leaving two disconnected
        pieces. The kernel reports two solids and the engine must refuse.
        """
        document = {
            "schema_version": "1.0.0",
            "units": "mm",
            "name": "severed-bar",
            "features": [
                {"id": "bar", "type": "box", "size": {"x": 100, "y": 20, "z": 10}},
                {
                    "id": "slot",
                    "type": "through_hole",
                    "target": "bar",
                    "diameter": 40,
                    "position": {"x": 50, "y": 10, "z": 0},
                },
            ],
        }
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(self.part_from(document))
        message = str(caught.exception)
        self.assertIn("E3", message)
        self.assertIn("split", message)
        self.assertIn("'slot'", message)

    def test_the_split_is_a_measured_kernel_result_not_a_special_case(self) -> None:
        """The rejection follows from what the kernel actually returns."""
        import cadquery as cq

        bar = cq.Solid.makeBox(100, 20, 10, pnt=cq.Vector(0, 0, 0))
        cutter = cq.Solid.makeCylinder(
            20, 10 + 2 * 60, pnt=cq.Vector(50, 10, -60), dir=cq.Vector(0, 0, 1)
        )
        self.assertEqual(len(bar.cut(cutter).Solids()), 2)

    def test_a_hole_that_removes_everything_is_rejected(self) -> None:
        """A hole wider than the whole block leaves no material."""
        document = {
            "schema_version": "1.0.0",
            "units": "mm",
            "name": "consumed",
            "features": [
                {"id": "chip", "type": "box", "size": {"x": 10, "y": 10, "z": 5}},
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "chip",
                    "diameter": 200,
                    "position": {"x": 5, "y": 5, "z": 0},
                },
            ],
        }
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(self.part_from(document))
        self.assertIn("no material", str(caught.exception))


# --- boundary: what must never reach the kernel ---------------------------


class TestBoundaries(ThroughHoleTestCase):
    def test_invalid_diameter_never_reaches_the_kernel(self) -> None:
        for value in (0, -20):
            with self.subTest(diameter=value):
                result = validate(plate_document(hole={"diameter": value}))
                self.assertFalse(result.valid)
                self.assertIn("S13", result.rule_codes())
                self.assertIsNone(result.part)
                with self.assertRaises(TypeError):
                    build_part(result.part)  # type: ignore[arg-type]

    def test_invalid_axis_never_reaches_the_kernel(self) -> None:
        for value in ("Z", "+z", "up", None):
            with self.subTest(axis=value):
                result = validate(plate_document(hole={"axis": value}))
                self.assertFalse(result.valid)
                self.assertIn("S12", result.rule_codes())

    def test_a_missing_target_never_reaches_geometry(self) -> None:
        document = plate_document()
        del document["features"][1]["target"]
        result = validate(document)
        self.assertFalse(result.valid)
        self.assertIsNone(result.part)

    def test_an_unknown_axis_on_a_hand_built_part_is_refused(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                ThroughHole(
                    id="bore",
                    target="plate",
                    diameter=HOLE_DIAMETER,
                    position=Position(20.0, 20.0, 0.0),
                    axis="diagonal",
                ),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("axis", str(caught.exception))

    def test_a_malformed_short_axis_is_refused_not_crashed(self) -> None:
        """An axis string too short to have a sign must still be reported."""
        for axis in ("", "Z", "+"):
            part = Part(
                schema_version="1.0.0",
                units="mm",
                name="p",
                features=(
                    Box(id="plate", size=Size(*PLATE_SIZE)),
                    ThroughHole(
                        id="bore",
                        target="plate",
                        diameter=HOLE_DIAMETER,
                        position=Position(20.0, 20.0, 0.0),
                        axis=axis,
                    ),
                ),
            )
            with self.subTest(axis=axis):
                with self.assertRaises(UnsupportedGeometryError):
                    build_part(part)

    def test_still_unimplemented_features_remain_rejected(self) -> None:
        for feature in (
            Subtract(id="s", target="plate", tools=("tool",)),
            Fillet(id="f", target="plate", radius=2.0, edges=EdgeSelector(select="all")),
            Chamfer(
                id="c", target="plate", distance=1.0, edges=EdgeSelector(select="all")
            ),
        ):
            with self.subTest(feature=feature.TYPE):
                part = Part(
                    schema_version="1.0.0",
                    units="mm",
                    name="p",
                    features=(Box(id="plate", size=Size(*PLATE_SIZE)), feature),
                )
                with self.assertRaises(UnsupportedGeometryError) as caught:
                    build_part(part)
                self.assertIn(feature.TYPE, str(caught.exception))

    def test_two_constructive_features_remain_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="a", size=Size(10.0, 10.0, 10.0)),
                Cylinder(id="b", diameter=5.0, height=5.0),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("one constructive feature", str(caught.exception))

    def test_a_history_starting_with_a_modifier_is_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                ThroughHole(
                    id="bore",
                    target="plate",
                    diameter=HOLE_DIAMETER,
                    position=Position(20.0, 20.0, 0.0),
                ),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("constructive", str(caught.exception))

    def test_a_raw_dictionary_cannot_invoke_the_build_api(self) -> None:
        with self.assertRaises(TypeError):
            build_part(plate_document())  # type: ignore[arg-type]


# --- STEP ------------------------------------------------------------------


class TestDrilledStep(ThroughHoleTestCase):
    def test_step_round_trip(self) -> None:
        for extension in (".step", ".stp"):
            with self.subTest(extension=extension):
                imported = read_step(
                    export_step(self.build(), self.tmp / f"plate{extension}")
                )
                self.assertTrue(imported.is_solid())
                self.assertEqual(imported.solid_count(), 1)
                box = imported.bounding_box()
                self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
                self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)
                self.assertAlmostEqual(
                    imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
                )

    def test_step_preserves_the_hole_as_a_cylindrical_face(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "plate.step"))
        self.assertEqual(len(imported.shape.Faces()), 7)
        walls = [f for f in imported.shape.Faces() if f.geomType() == "CYLINDER"]
        self.assertEqual(len(walls), 1)

    def test_step_geometry_is_deterministic(self) -> None:
        source = self.build()
        measurements = set()
        for index in range(3):
            imported = read_step(export_step(source, self.tmp / f"p{index}.step"))
            box = imported.bounding_box()
            measurements.add(
                (
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- IGES ------------------------------------------------------------------


class TestDrilledIges(ThroughHoleTestCase):
    def test_iges_round_trip(self) -> None:
        for extension in (".igs", ".iges"):
            with self.subTest(extension=extension):
                imported = read_iges(
                    export_iges(self.build(), self.tmp / f"plate{extension}")
                )
                self.assertEqual(imported.shape_type(), "Solid")
                self.assertTrue(imported.is_solid())
                self.assertEqual(imported.solid_count(), 1)
                box = imported.bounding_box()
                self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
                self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_iges_preserves_the_drilled_volume(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "plate.igs"))
        self.assertTrue(imported.is_solid())
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_iges_face_count_is_recorded(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "plate.igs"))
        self.assertEqual(imported.face_count(), 7)

    def test_iges_geometry_is_deterministic(self) -> None:
        source = self.build()
        measurements = set()
        for index in range(3):
            imported = read_iges(export_iges(source, self.tmp / f"p{index}.igs"))
            box = imported.bounding_box()
            measurements.add(
                (
                    imported.shape_type(),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- STL -------------------------------------------------------------------


class TestDrilledStl(ThroughHoleTestCase):
    def test_stl_is_structurally_valid(self) -> None:
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "plate.stl"))
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        self.assertGreater(facts.declared_triangles, 0)

    def test_the_hole_wall_adds_triangles(self) -> None:
        """A drilled plate must need more triangles than a plain one."""
        plain_document = {
            "schema_version": "1.0.0",
            "units": "mm",
            "name": "plain",
            "features": [
                {"id": "plate", "type": "box", "size": {"x": 100, "y": 60, "z": 10}}
            ],
        }
        plain = read_stl(
            export_stl(build_part(self.part_from(plain_document)), self.tmp / "plain.stl")
        )
        drilled = read_stl(export_stl(self.build(), self.tmp / "drilled.stl"))
        self.assertEqual(plain.triangle_count(), 12)
        self.assertGreater(drilled.triangle_count(), plain.triangle_count())

    def test_mesh_bounds_approximate_the_brep(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "plate.stl"))
        box = mesh.bounding_box()
        self.assertTripleAlmostEqual(
            box.minimum, EXPECTED_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.maximum, EXPECTED_MAXIMUM, delta=MESH_TOLERANCE_MM
        )

    def test_the_hole_is_actually_present_in_the_mesh(self) -> None:
        """Vertices must appear on the hole wall, at the hole radius."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "plate.stl"))
        on_wall = [
            node
            for node in mesh.nodes()
            if abs(
                math.hypot(node[0] - HOLE_CENTRE[0], node[1] - HOLE_CENTRE[1])
                - HOLE_RADIUS
            )
            <= MESH_TOLERANCE_MM
        ]
        self.assertGreater(len(on_wall), 8)
        # and no vertex may sit strictly inside the hole
        for node in mesh.nodes():
            radial = math.hypot(node[0] - HOLE_CENTRE[0], node[1] - HOLE_CENTRE[1])
            self.assertGreaterEqual(radial, HOLE_RADIUS - MESH_TOLERANCE_MM - 1e-5)

    def test_all_mesh_vertices_are_finite(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "plate.stl"))
        for index, node in enumerate(mesh.nodes()):
            with self.subTest(node=index):
                for value in node:
                    self.assertTrue(math.isfinite(value))

    def test_repeated_stl_exports_remain_valid_and_equivalent(self) -> None:
        source = self.build()
        signatures = set()
        digests = set()
        for index in range(3):
            written = export_stl(source, self.tmp / f"p{index}.stl")
            self.assertTrue(binary_stl_facts(written).is_structurally_consistent)
            mesh = read_stl(written)
            signatures.add((mesh.triangle_count(), mesh.node_count()))
            digests.add(hashlib.sha256(written.read_bytes()).hexdigest())
        self.assertEqual(len(signatures), 1)
        self.assertEqual(len(digests), 1)  # measured: byte-identical


# --- render model ----------------------------------------------------------


class TestDrilledRenderModel(ThroughHoleTestCase):
    def model(self, **overrides: Any):
        return build_render_model(self.build(**overrides))

    def test_render_model_is_produced(self) -> None:
        model = self.model()
        self.assertEqual(model.feature_id, "plate")
        self.assertGreater(model.vertex_count(), 0)
        self.assertGreater(model.triangle_count(), 0)

    def test_triangles_exist_on_both_planar_and_cylindrical_surfaces(self) -> None:
        model = self.model()
        on_wall = 0
        on_face = 0
        for x, y, _ in model.vertices:
            radial = math.hypot(x - HOLE_CENTRE[0], y - HOLE_CENTRE[1])
            if abs(radial - HOLE_RADIUS) <= MESH_TOLERANCE_MM:
                on_wall += 1
            elif radial > HOLE_RADIUS + 1.0:
                on_face += 1
        self.assertGreater(on_wall, 8, msg="no vertices on the hole wall")
        self.assertGreater(on_face, 3, msg="no vertices on the planar faces")

    def test_normals_are_finite_and_unit_length(self) -> None:
        for index, (x, y, z) in enumerate(self.model().normals):
            with self.subTest(normal=index):
                for component in (x, y, z):
                    self.assertTrue(math.isfinite(component))
                self.assertAlmostEqual(
                    math.sqrt(x * x + y * y + z * z), 1.0, delta=NORMAL_TOLERANCE
                )

    def test_hole_wall_normals_point_into_the_hole(self) -> None:
        """The decisive orientation test for a subtractive feature.

        The hole wall faces the material, so its outward normal points *toward
        the hole axis* -- the opposite sense to a cylinder's outer wall.
        """
        model = self.model()
        sampled = 0
        distinct = set()
        for (x, y, _), normal in zip(model.vertices, model.normals):
            radial = math.hypot(x - HOLE_CENTRE[0], y - HOLE_CENTRE[1])
            if abs(radial - HOLE_RADIUS) > MESH_TOLERANCE_MM:
                continue
            if abs(normal[2]) > 0.5:
                continue  # a rim vertex shared with an end face
            inward = (
                -(x - HOLE_CENTRE[0]) / radial,
                -(y - HOLE_CENTRE[1]) / radial,
                0.0,
            )
            alignment = sum(normal[i] * inward[i] for i in range(3))
            self.assertGreater(alignment, 1.0 - RADIAL_NORMAL_TOLERANCE)
            self.assertAlmostEqual(normal[2], 0.0, delta=RADIAL_NORMAL_TOLERANCE)
            sampled += 1
            distinct.add(tuple(round(component, 6) for component in normal))
        self.assertGreater(sampled, 8)
        self.assertGreater(len(distinct), 8, msg="wall normals must vary around the hole")

    def test_outer_planar_normals_remain_outward(self) -> None:
        """Drilling must not flip the orientation of the outer faces."""
        model = self.model()
        top = [
            normal
            for (x, y, z), normal in zip(model.vertices, model.normals)
            if abs(z - PLATE_SIZE[2]) < MESH_TOLERANCE_MM and abs(normal[2]) > 0.5
        ]
        bottom = [
            normal
            for (x, y, z), normal in zip(model.vertices, model.normals)
            if abs(z - 0.0) < MESH_TOLERANCE_MM and abs(normal[2]) > 0.5
        ]
        self.assertGreater(len(top), 0)
        self.assertGreater(len(bottom), 0)
        for normal in top:
            self.assertAlmostEqual(normal[2], 1.0, delta=RADIAL_NORMAL_TOLERANCE)
        for normal in bottom:
            self.assertAlmostEqual(normal[2], -1.0, delta=RADIAL_NORMAL_TOLERANCE)

    def test_winding_is_outward_for_every_triangle(self) -> None:
        """Checked against the nearest solid material, not a global centre.

        A drilled plate is not star-shaped about its centroid -- the hole is
        hollow -- so outwardness is tested by stepping a short distance along
        the triangle's normal and confirming that point leaves the solid.
        """
        import cadquery as cq
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier
        from OCP.TopAbs import TopAbs_State

        result = self.build()
        model = build_render_model(result)
        step = 1e-3
        classifier = BRepClass3d_SolidClassifier(result.shape.wrapped)
        for position, (a, b, c) in enumerate(model.triangles):
            va, vb, vc = (model.vertices[i] for i in (a, b, c))
            ux, uy, uz = (vb[i] - va[i] for i in range(3))
            vx, vy, vz = (vc[i] - va[i] for i in range(3))
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length == 0.0:  # pragma: no cover - no degenerate triangles expected
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
        """A through-hole removes interior material, so bounds do not move.

        Render bounds come from the render vertices, and every outer face of
        this part is planar, so they land exactly on the envelope the V1
        semantics predict. That also means bounds are no evidence that a hole
        exists: a plain plate measures the same.
        """
        model = build_render_model(self.build())
        self.assertTripleAlmostEqual(
            model.bounds.minimum, EXPECTED_MINIMUM, delta=TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            model.bounds.maximum, EXPECTED_MAXIMUM, delta=TOLERANCE_MM
        )

    def test_the_brep_query_inflates_once_a_triangulation_exists(self) -> None:
        """Recorded kernel behaviour, and the reason bounds come from vertices.

        ``BoundingBox()`` bounds an attached triangulation with a gap rather
        than the analytic surface. Before meshing the plate measures exactly
        (0, 0, 0)-(100, 60, 10); after the render model has meshed it, the
        query loosens outward along the drilled axis by roughly the chord sag
        of the hole wall -- about 3.1e-3 mm here, far larger than the ~1e-7 mm
        seen for a plain box. The render bounds do not move, which is the
        point of deriving them from the triangles.
        """
        result = self.build()
        before = result.bounding_box()
        self.assertTripleAlmostEqual(before.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(before.maximum, EXPECTED_MAXIMUM)

        model = build_render_model(result)
        after = result.bounding_box()

        # The query may only loosen outward, and never by more than the mesh
        # can deviate from the true surface.
        for axis, (low, high) in enumerate(
            zip(EXPECTED_MINIMUM, EXPECTED_MAXIMUM)
        ):
            with self.subTest(axis="xyz"[axis]):
                got_low = (after.minimum.x, after.minimum.y, after.minimum.z)[axis]
                got_high = (after.maximum.x, after.maximum.y, after.maximum.z)[axis]
                self.assertLessEqual(got_low, low + TOLERANCE_MM)
                self.assertGreaterEqual(got_high, high - TOLERANCE_MM)
                self.assertGreaterEqual(got_low, low - MESH_TOLERANCE_MM)
                self.assertLessEqual(got_high, high + MESH_TOLERANCE_MM)

        # Along the drilled axis the loosening is measurable, not float noise.
        self.assertGreater(abs(after.minimum.z - before.minimum.z), 1e-4)
        # The render bounds stay exact regardless.
        self.assertTripleAlmostEqual(
            model.bounds.minimum, EXPECTED_MINIMUM, delta=TOLERANCE_MM
        )

    def test_render_model_serializes_and_is_deterministic(self) -> None:
        source = self.build()
        payloads = {
            json.dumps(build_render_model(source).to_dict(), sort_keys=True)
            for _ in range(4)
        }
        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            json.loads(next(iter(payloads)))["feature_id"], "plate"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
