"""Cylinder support across the whole local CAD pipeline.

One place for the V1 cylinder primitive: the local B-rep engine, then STEP,
IGES, STL and the render model. Kept together so the pipeline is exercised
end to end for curved geometry, which nothing before this stage did.

Every expected bounding box below is derived by hand from the specification's
Section C.2 semantics -- base centre at ``position``, height along the signed
axis, radial extent ``diameter / 2`` in the two perpendicular axes -- and the
expected volume from pi*r^2*h, never from the kernel.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Tuple

from cad_core import validate
from cad_core.iges_export import export_iges, read_iges
from cad_core.local_cad import (
    AXIS_DIRECTIONS,
    LocalCadResult,
    UnsupportedGeometryError,
    build_part,
)
from cad_core.model import (
    AXIS_VALUES,
    Chamfer,
    Cylinder,
    EdgeSelector,
    Fillet,
    Part,
    Position,
    Subtract,
    ThroughHole,
)
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl, read_stl

# --- tolerances, all explicit ---------------------------------------------

#: Kernel-derived lengths, in millimetres. Same value the rest of the suite
#: uses for exact B-rep measurements.
TOLERANCE_MM = 1e-6

#: Volume, in cubic millimetres. Looser than the length tolerance because a
#: cylinder's volume is irrational: the kernel integrates the analytic surface,
#: so agreement with pi*r^2*h is limited by double precision on a value of
#: order 1.6e4, not by geometry.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Linear deflection used by the STL exporter and the render model.
LINEAR_DEFLECTION_MM = 0.01

#: How far a tessellated vertex may sit inside the true surface, plus kernel
#: noise. For a curved surface this is the real, load-bearing tolerance: a
#: chord across the cylinder's circumference lies inside the arc.
MESH_TOLERANCE_MM = LINEAR_DEFLECTION_MM + TOLERANCE_MM

#: Unit-length check for normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

#: How far a tessellation vertex may sit outside the exact radius, in mm. The
#: mesh is nominally inscribed -- chords lie inside the arc -- but vertices are
#: placed on the analytic surface with numerical slop: the worst case measured
#: is 9.13e-7 mm outside. This bound allows that with margin rather than
#: sitting a hair under it.
INSCRIBED_SLOP_MM = 1e-5

#: How closely a curved-surface vertex normal must align with the exact radial
#: direction. A vertex normal is the average of its adjacent facet normals, so
#: on a tessellated cylinder it is only approximately radial; the bound is
#: generous enough for that averaging and tight enough to fail a wrong axis.
RADIAL_NORMAL_TOLERANCE = 1e-2

# --- the primary cylinder --------------------------------------------------

DIAMETER = 20.0
HEIGHT = 50.0
RADIUS = DIAMETER / 2.0
BASE_CENTRE = (10.0, 20.0, 30.0)

#: pi * r^2 * h, computed here rather than read from the kernel.
EXPECTED_VOLUME = math.pi * RADIUS**2 * HEIGHT

#: Hand-derived bounds per axis: base centre at BASE_CENTRE, height along the
#: signed axis, radius in each perpendicular axis.
EXPECTED_BOUNDS: Dict[str, Tuple[Tuple[float, float, float], Tuple[float, float, float]]] = {
    "+X": ((10.0, 10.0, 20.0), (60.0, 30.0, 40.0)),
    "-X": ((-40.0, 10.0, 20.0), (10.0, 30.0, 40.0)),
    "+Y": ((0.0, 20.0, 20.0), (20.0, 70.0, 40.0)),
    "-Y": ((0.0, -30.0, 20.0), (20.0, 20.0, 40.0)),
    "+Z": ((0.0, 10.0, 30.0), (20.0, 30.0, 80.0)),
    "-Z": ((0.0, 10.0, -20.0), (20.0, 30.0, 30.0)),
}

PRIMARY_MINIMUM, PRIMARY_MAXIMUM = EXPECTED_BOUNDS["+Z"]
PRIMARY_DIMENSIONS = (20.0, 20.0, 50.0)


def cylinder_document(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "pin",
        "type": "cylinder",
        "diameter": DIAMETER,
        "height": HEIGHT,
        "position": {"x": BASE_CENTRE[0], "y": BASE_CENTRE[1], "z": BASE_CENTRE[2]},
        "axis": "+Z",
    }
    feature.update(overrides.pop("feature", {}))
    document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "pin-20x50",
        "features": [feature],
    }
    document.update(overrides)
    return document


class CylinderTestCase(unittest.TestCase):
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
        return build_part(self.part_from(cylinder_document(**overrides)))

    def assertTripleAlmostEqual(
        self, actual: Any, expected: tuple, delta: float = TOLERANCE_MM
    ) -> None:
        values = (
            (actual.x, actual.y, actual.z) if hasattr(actual, "x") else tuple(actual)
        )
        for axis, (got, want) in enumerate(zip(values, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- local CAD engine ------------------------------------------------------


class TestLocalCylinder(CylinderTestCase):
    """diameter 20, height 50, base centre (10, 20, 30), axis +Z."""

    def test_build_succeeds(self) -> None:
        result = self.build()
        self.assertIsInstance(result, LocalCadResult)
        self.assertEqual(result.part_name, "pin-20x50")
        self.assertEqual(result.feature_id, "pin")

    def test_shape_is_a_valid_solid(self) -> None:
        result = self.build()
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertTrue(result.shape.isValid())
        self.assertTrue(result.is_solid())

    def test_solid_count_is_one(self) -> None:
        self.assertEqual(self.build().solid_count(), 1)

    def test_minimum_corner(self) -> None:
        self.assertTripleAlmostEqual(
            self.build().bounding_box().minimum, PRIMARY_MINIMUM
        )

    def test_maximum_corner(self) -> None:
        self.assertTripleAlmostEqual(
            self.build().bounding_box().maximum, PRIMARY_MAXIMUM
        )

    def test_dimensions(self) -> None:
        self.assertTripleAlmostEqual(
            self.build().bounding_box().size, PRIMARY_DIMENSIONS
        )

    def test_volume_matches_pi_r_squared_h(self) -> None:
        """Expected value from mathematics, not from the kernel."""
        self.assertAlmostEqual(
            self.build().volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_base_centre_is_the_specified_position(self) -> None:
        """The base circle is centred on position, not offset or centred."""
        box = self.build().bounding_box()
        self.assertAlmostEqual(
            (box.minimum.x + box.maximum.x) / 2, BASE_CENTRE[0], delta=TOLERANCE_MM
        )
        self.assertAlmostEqual(
            (box.minimum.y + box.maximum.y) / 2, BASE_CENTRE[1], delta=TOLERANCE_MM
        )
        self.assertAlmostEqual(box.minimum.z, BASE_CENTRE[2], delta=TOLERANCE_MM)

    def test_radius_is_half_the_diameter(self) -> None:
        box = self.build().bounding_box()
        self.assertAlmostEqual(box.size.x / 2, RADIUS, delta=TOLERANCE_MM)
        self.assertAlmostEqual(box.size.y / 2, RADIUS, delta=TOLERANCE_MM)

    def test_default_axis_is_plus_z(self) -> None:
        """An omitted axis must behave exactly like an explicit '+Z'."""
        document = cylinder_document()
        del document["features"][0]["axis"]
        implicit = build_part(self.part_from(document)).bounding_box()
        self.assertTripleAlmostEqual(implicit.minimum, PRIMARY_MINIMUM)
        self.assertTripleAlmostEqual(implicit.maximum, PRIMARY_MAXIMUM)

    def test_topology_is_a_kernel_observation_not_a_contract(self) -> None:
        """Measured, and recorded as backend-specific.

        OpenCascade builds a full cylinder as three faces (the side plus two
        caps), three edges (a seam plus two circles) and two vertices. This is
        a property of the kernel, not of the neutral CAD specification, and no
        other stage relies on it.
        """
        shape = self.build().shape
        self.assertEqual(len(shape.Faces()), 3)
        self.assertEqual(len(shape.Edges()), 3)
        self.assertEqual(len(shape.Vertices()), 2)

    def test_repeated_builds_measure_identically(self) -> None:
        part = self.part_from(cylinder_document())
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


# --- all six axes ----------------------------------------------------------


class TestCylinderAxes(CylinderTestCase):
    def test_axis_direction_table_matches_the_specification(self) -> None:
        self.assertEqual(sorted(AXIS_DIRECTIONS), sorted(AXIS_VALUES))
        self.assertEqual(AXIS_DIRECTIONS["+Z"], (0.0, 0.0, 1.0))
        self.assertEqual(AXIS_DIRECTIONS["-Z"], (0.0, 0.0, -1.0))

    def test_bounds_for_each_axis(self) -> None:
        for axis, (minimum, maximum) in EXPECTED_BOUNDS.items():
            with self.subTest(axis=axis):
                box = self.build(feature={"axis": axis}).bounding_box()
                self.assertTripleAlmostEqual(box.minimum, minimum)
                self.assertTripleAlmostEqual(box.maximum, maximum)

    def test_every_axis_yields_a_valid_solid_of_the_right_volume(self) -> None:
        for axis in AXIS_VALUES:
            with self.subTest(axis=axis):
                result = self.build(feature={"axis": axis})
                self.assertTrue(result.is_solid())
                self.assertEqual(result.solid_count(), 1)
                self.assertAlmostEqual(
                    result.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
                )

    def test_base_centre_is_on_the_boundary_for_every_axis(self) -> None:
        """Whichever way it extends, the base circle sits at position."""
        for axis in AXIS_VALUES:
            with self.subTest(axis=axis):
                box = self.build(feature={"axis": axis}).bounding_box()
                index = "XYZ".index(axis[1])
                base = BASE_CENTRE[index]
                low = (box.minimum.x, box.minimum.y, box.minimum.z)[index]
                high = (box.maximum.x, box.maximum.y, box.maximum.z)[index]
                if axis[0] == "+":
                    self.assertAlmostEqual(low, base, delta=TOLERANCE_MM)
                    self.assertAlmostEqual(high, base + HEIGHT, delta=TOLERANCE_MM)
                else:
                    self.assertAlmostEqual(high, base, delta=TOLERANCE_MM)
                    self.assertAlmostEqual(low, base - HEIGHT, delta=TOLERANCE_MM)

    def test_negative_axes_extend_in_the_negative_direction(self) -> None:
        """The sign is honoured, not normalised away."""
        for axis, index in (("-X", 0), ("-Y", 1), ("-Z", 2)):
            with self.subTest(axis=axis):
                box = self.build(feature={"axis": axis}).bounding_box()
                low = (box.minimum.x, box.minimum.y, box.minimum.z)[index]
                high = (box.maximum.x, box.maximum.y, box.maximum.z)[index]
                self.assertAlmostEqual(
                    low, BASE_CENTRE[index] - HEIGHT, delta=TOLERANCE_MM
                )
                self.assertAlmostEqual(high, BASE_CENTRE[index], delta=TOLERANCE_MM)

    def test_opposite_axes_are_mirror_images(self) -> None:
        for positive, negative, index in (("+X", "-X", 0), ("+Y", "-Y", 1), ("+Z", "-Z", 2)):
            with self.subTest(pair=f"{positive}/{negative}"):
                up = self.build(feature={"axis": positive}).bounding_box()
                down = self.build(feature={"axis": negative}).bounding_box()
                base = BASE_CENTRE[index]
                up_high = (up.maximum.x, up.maximum.y, up.maximum.z)[index]
                down_low = (down.minimum.x, down.minimum.y, down.minimum.z)[index]
                self.assertAlmostEqual(
                    up_high - base, base - down_low, delta=TOLERANCE_MM
                )

    def test_radial_extent_is_in_the_two_perpendicular_axes(self) -> None:
        for axis in AXIS_VALUES:
            with self.subTest(axis=axis):
                box = self.build(feature={"axis": axis}).bounding_box()
                sizes = (box.size.x, box.size.y, box.size.z)
                along = "XYZ".index(axis[1])
                for index, size in enumerate(sizes):
                    expected = HEIGHT if index == along else DIAMETER
                    self.assertAlmostEqual(size, expected, delta=TOLERANCE_MM)


# --- STEP ------------------------------------------------------------------


class TestCylinderStep(CylinderTestCase):
    def test_step_round_trip_preserves_the_solid(self) -> None:
        for extension in (".step", ".stp"):
            with self.subTest(extension=extension):
                written = export_step(self.build(), self.tmp / f"pin{extension}")
                imported = read_step(written)
                self.assertTrue(imported.is_solid())
                self.assertEqual(imported.shape.ShapeType(), "Solid")
                self.assertEqual(imported.solid_count(), 1)

    def test_step_round_trip_preserves_geometry(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "pin.step"))
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, PRIMARY_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, PRIMARY_MAXIMUM)
        self.assertTripleAlmostEqual(box.size, PRIMARY_DIMENSIONS)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_step_preserves_curved_topology(self) -> None:
        """The cylindrical face survives as a surface, not as facets."""
        imported = read_step(export_step(self.build(), self.tmp / "pin.step"))
        self.assertEqual(len(imported.shape.Faces()), 3)

    def test_step_round_trip_for_every_axis(self) -> None:
        for axis, (minimum, maximum) in EXPECTED_BOUNDS.items():
            with self.subTest(axis=axis):
                result = self.build(feature={"axis": axis})
                imported = read_step(export_step(result, self.tmp / f"pin-{axis[1]}{axis[0]}.step"))
                self.assertTrue(imported.is_solid())
                self.assertTripleAlmostEqual(imported.bounding_box().minimum, minimum)
                self.assertTripleAlmostEqual(imported.bounding_box().maximum, maximum)

    def test_step_round_trip_is_geometrically_deterministic(self) -> None:
        source = self.build()
        measurements = set()
        for index in range(3):
            imported = read_step(export_step(source, self.tmp / f"pin-{index}.step"))
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


class TestCylinderIges(CylinderTestCase):
    def test_iges_round_trip_preserves_the_solid(self) -> None:
        """BRep mode preserved a solid for a box; measured again for a curve."""
        for extension in (".igs", ".iges"):
            with self.subTest(extension=extension):
                imported = read_iges(
                    export_iges(self.build(), self.tmp / f"pin{extension}")
                )
                self.assertEqual(imported.shape_type(), "Solid")
                self.assertTrue(imported.is_solid())
                self.assertEqual(imported.solid_count(), 1)

    def test_iges_round_trip_preserves_geometry(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "pin.igs"))
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, PRIMARY_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, PRIMARY_MAXIMUM)
        self.assertTripleAlmostEqual(box.size, PRIMARY_DIMENSIONS)

    def test_iges_volume_matches_within_tolerance(self) -> None:
        """Only meaningful because the imported shape is a solid."""
        imported = read_iges(export_iges(self.build(), self.tmp / "pin.igs"))
        self.assertTrue(imported.is_solid())
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )

    def test_iges_face_count_is_recorded(self) -> None:
        """A kernel observation, not a contract: IGES may split the seam."""
        imported = read_iges(export_iges(self.build(), self.tmp / "pin.igs"))
        self.assertGreaterEqual(imported.face_count(), 3)

    def test_iges_round_trip_for_every_axis(self) -> None:
        for axis, (minimum, maximum) in EXPECTED_BOUNDS.items():
            with self.subTest(axis=axis):
                result = self.build(feature={"axis": axis})
                imported = read_iges(
                    export_iges(result, self.tmp / f"pin-{axis[1]}{axis[0]}.igs")
                )
                self.assertTrue(imported.is_solid())
                self.assertTripleAlmostEqual(imported.bounding_box().minimum, minimum)
                self.assertTripleAlmostEqual(imported.bounding_box().maximum, maximum)

    def test_iges_round_trip_is_geometrically_deterministic(self) -> None:
        source = self.build()
        measurements = set()
        for index in range(3):
            imported = read_iges(export_iges(source, self.tmp / f"pin-{index}.igs"))
            box = imported.bounding_box()
            measurements.add(
                (
                    imported.shape_type(),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- STL -------------------------------------------------------------------


class TestCylinderStl(CylinderTestCase):
    def test_stl_export_is_structurally_valid(self) -> None:
        written = export_stl(self.build(), self.tmp / "pin.stl")
        facts = binary_stl_facts(written)
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        self.assertGreater(facts.declared_triangles, 0)

    def test_curved_surface_needs_many_more_triangles_than_a_box(self) -> None:
        """A cylinder is the first geometry where tessellation density matters."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "pin.stl"))
        self.assertGreater(mesh.triangle_count(), 12)

    def test_mesh_vertices_are_finite_and_inside_the_envelope(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "pin.stl"))
        for index, (x, y, z) in enumerate(mesh.nodes()):
            with self.subTest(node=index):
                for value in (x, y, z):
                    self.assertTrue(math.isfinite(value))
                for value, low, high in (
                    (x, PRIMARY_MINIMUM[0], PRIMARY_MAXIMUM[0]),
                    (y, PRIMARY_MINIMUM[1], PRIMARY_MAXIMUM[1]),
                    (z, PRIMARY_MINIMUM[2], PRIMARY_MAXIMUM[2]),
                ):
                    self.assertGreaterEqual(value, low - MESH_TOLERANCE_MM)
                    self.assertLessEqual(value, high + MESH_TOLERANCE_MM)

    def test_mesh_bounds_approximate_the_brep_bounds(self) -> None:
        """A chord lies inside the arc, so the mesh may be slightly smaller."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "pin.stl"))
        box = mesh.bounding_box()
        self.assertTripleAlmostEqual(
            box.minimum, PRIMARY_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.maximum, PRIMARY_MAXIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.size, PRIMARY_DIMENSIONS, delta=2 * MESH_TOLERANCE_MM
        )

    def test_the_mesh_is_inscribed_in_the_true_cylinder(self) -> None:
        """Every vertex must lie on or inside the exact radius."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "pin.stl"))
        for index, (x, y, _) in enumerate(mesh.nodes()):
            with self.subTest(node=index):
                radial = math.hypot(x - BASE_CENTRE[0], y - BASE_CENTRE[1])
                self.assertLessEqual(radial, RADIUS + INSCRIBED_SLOP_MM)

    def test_repeated_exports_remain_valid_and_equivalent(self) -> None:
        source = self.build()
        signatures = set()
        for index in range(3):
            written = export_stl(source, self.tmp / f"pin-{index}.stl")
            mesh = read_stl(written)
            box = mesh.bounding_box()
            self.assertTrue(
                binary_stl_facts(written).is_structurally_consistent
            )
            signatures.add(
                (
                    mesh.triangle_count(),
                    mesh.node_count(),
                    (round(box.minimum.x, 9), round(box.minimum.y, 9)),
                    (round(box.maximum.x, 9), round(box.maximum.y, 9)),
                )
            )
        self.assertEqual(len(signatures), 1)

    def test_a_coarser_tolerance_reduces_the_triangle_count(self) -> None:
        """Unlike a box, a cylinder genuinely responds to the deflection."""
        fine = read_stl(
            export_stl(self.build(), self.tmp / "fine.stl", tolerance=0.005)
        )
        coarse = read_stl(
            export_stl(self.build(), self.tmp / "coarse.stl", tolerance=0.5)
        )
        self.assertGreater(fine.triangle_count(), coarse.triangle_count())


# --- render model ----------------------------------------------------------


class TestCylinderRenderModel(CylinderTestCase):
    def model(self, **overrides: Any):
        return build_render_model(self.build(**overrides))

    def test_render_model_is_produced(self) -> None:
        model = self.model()
        self.assertEqual(model.part_name, "pin-20x50")
        self.assertEqual(model.feature_id, "pin")
        self.assertEqual(model.units, "mm")

    def test_counts_are_positive(self) -> None:
        model = self.model()
        self.assertGreater(model.vertex_count(), 0)
        self.assertGreater(model.triangle_count(), 0)
        self.assertEqual(len(model.normals), model.vertex_count())

    def test_curved_surface_yields_more_triangles_than_a_box(self) -> None:
        self.assertGreater(self.model().triangle_count(), 12)

    def test_every_triangle_index_is_valid(self) -> None:
        model = self.model()
        limit = model.vertex_count()
        for position, triangle in enumerate(model.triangles):
            with self.subTest(triangle=position):
                self.assertEqual(len(set(triangle)), 3)
                for index in triangle:
                    self.assertGreaterEqual(index, 0)
                    self.assertLess(index, limit)

    def test_bounds_approximate_the_true_cylinder(self) -> None:
        bounds = self.model().bounds
        self.assertTripleAlmostEqual(
            bounds.minimum, PRIMARY_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            bounds.maximum, PRIMARY_MAXIMUM, delta=MESH_TOLERANCE_MM
        )

    def test_normals_are_finite_and_unit_length(self) -> None:
        for index, (x, y, z) in enumerate(self.model().normals):
            with self.subTest(normal=index):
                for component in (x, y, z):
                    self.assertTrue(math.isfinite(component))
                self.assertAlmostEqual(
                    math.sqrt(x * x + y * y + z * z), 1.0, delta=NORMAL_TOLERANCE
                )

    def test_winding_yields_outward_normals(self) -> None:
        """Centroid of the cylinder is the midpoint of its axis."""
        model = self.model()
        centre = (BASE_CENTRE[0], BASE_CENTRE[1], BASE_CENTRE[2] + HEIGHT / 2)
        for position, (a, b, c) in enumerate(model.triangles):
            with self.subTest(triangle=position):
                va, vb, vc = (model.vertices[i] for i in (a, b, c))
                ux, uy, uz = (vb[i] - va[i] for i in range(3))
                vx, vy, vz = (vc[i] - va[i] for i in range(3))
                nx = uy * vz - uz * vy
                ny = uz * vx - ux * vz
                nz = ux * vy - uy * vx
                centroid = tuple((va[i] + vb[i] + vc[i]) / 3 for i in range(3))
                outward = tuple(centroid[i] - centre[i] for i in range(3))
                self.assertGreater(
                    nx * outward[0] + ny * outward[1] + nz * outward[2], 0.0
                )

    def test_side_normals_point_radially_outward_and_vary(self) -> None:
        """The first test that distinguishes curved from planar normals.

        A vertex on the cylindrical side must have a normal pointing away from
        the axis, with no Z component. Crucially the direction differs around
        the circumference -- a single expected normal would be wrong.
        """
        model = self.model()
        radial_normals = []
        for (x, y, z), normal in zip(model.vertices, model.normals):
            radial = math.hypot(x - BASE_CENTRE[0], y - BASE_CENTRE[1])
            if abs(radial - RADIUS) > MESH_TOLERANCE_MM:
                continue  # not on the curved surface
            if abs(normal[2]) > 0.5:
                continue  # an end-cap vertex on the rim
            expected = (
                (x - BASE_CENTRE[0]) / radial,
                (y - BASE_CENTRE[1]) / radial,
                0.0,
            )
            alignment = sum(normal[i] * expected[i] for i in range(3))
            self.assertGreater(alignment, 1.0 - RADIAL_NORMAL_TOLERANCE)
            self.assertAlmostEqual(normal[2], 0.0, delta=RADIAL_NORMAL_TOLERANCE)
            radial_normals.append(tuple(round(component, 6) for component in normal))

        self.assertGreater(len(radial_normals), 8, msg="too few side vertices sampled")
        self.assertGreater(
            len(set(radial_normals)), 8, msg="side normals must vary around the circumference"
        )

    def test_end_cap_normals_point_along_the_axis(self) -> None:
        model = self.model()
        bottom = [
            normal
            for (_, _, z), normal in zip(model.vertices, model.normals)
            if abs(z - BASE_CENTRE[2]) < MESH_TOLERANCE_MM and abs(normal[2]) > 0.5
        ]
        top = [
            normal
            for (_, _, z), normal in zip(model.vertices, model.normals)
            if abs(z - (BASE_CENTRE[2] + HEIGHT)) < MESH_TOLERANCE_MM
            and abs(normal[2]) > 0.5
        ]
        self.assertGreater(len(bottom), 0)
        self.assertGreater(len(top), 0)
        for normal in bottom:
            self.assertAlmostEqual(normal[2], -1.0, delta=RADIAL_NORMAL_TOLERANCE)
        for normal in top:
            self.assertAlmostEqual(normal[2], 1.0, delta=RADIAL_NORMAL_TOLERANCE)

    def test_serializes_to_json(self) -> None:
        data = self.model().to_dict()
        text = json.dumps(data)
        self.assertEqual(json.loads(text), data)

    def test_serialized_model_is_byte_deterministic(self) -> None:
        source = self.build()
        payloads = {
            json.dumps(build_render_model(source).to_dict(), sort_keys=True)
            for _ in range(4)
        }
        self.assertEqual(len(payloads), 1)

    def test_render_model_for_every_axis(self) -> None:
        for axis, (minimum, maximum) in EXPECTED_BOUNDS.items():
            with self.subTest(axis=axis):
                model = self.model(feature={"axis": axis})
                self.assertGreater(model.triangle_count(), 12)
                self.assertTripleAlmostEqual(
                    model.bounds.minimum, minimum, delta=MESH_TOLERANCE_MM
                )
                self.assertTripleAlmostEqual(
                    model.bounds.maximum, maximum, delta=MESH_TOLERANCE_MM
                )


# --- invalid and unsupported input ----------------------------------------


class TestCylinderValidationBoundary(CylinderTestCase):
    def test_zero_and_negative_diameter_remain_invalid(self) -> None:
        for value in (0, -20, -0.5):
            with self.subTest(diameter=value):
                result = validate(cylinder_document(feature={"diameter": value}))
                self.assertFalse(result.valid)
                self.assertIn("S11", result.rule_codes())
                self.assertIsNone(result.part)

    def test_zero_and_negative_height_remain_invalid(self) -> None:
        for value in (0, -50):
            with self.subTest(height=value):
                result = validate(cylinder_document(feature={"height": value}))
                self.assertFalse(result.valid)
                self.assertIn("S11", result.rule_codes())

    def test_invalid_axis_remains_invalid(self) -> None:
        for value in ("Z", "+z", "up", "", None, 0):
            with self.subTest(axis=value):
                result = validate(cylinder_document(feature={"axis": value}))
                self.assertFalse(result.valid)
                self.assertIn("S12", result.rule_codes())

    def test_an_invalid_cylinder_cannot_reach_the_engine(self) -> None:
        result = validate(cylinder_document(feature={"diameter": 0}))
        self.assertIsNone(result.part)
        with self.assertRaises(TypeError):
            build_part(result.part)  # type: ignore[arg-type]

    def test_an_unknown_axis_on_a_hand_built_part_is_rejected(self) -> None:
        """Defence in depth: the engine will not guess at an axis."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Cylinder(id="c", diameter=20.0, height=50.0, axis="sideways"),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("axis", str(caught.exception))

    def test_still_unsupported_features_remain_rejected(self) -> None:
        unsupported = (
            ThroughHole(
                id="h", target="pin", diameter=8.0, position=Position(0.0, 0.0, 0.0)
            ),
            Subtract(id="s", target="pin", tools=("tool",)),
            Fillet(id="f", target="pin", radius=2.0, edges=EdgeSelector(select="all")),
            Chamfer(
                id="c", target="pin", distance=1.0, edges=EdgeSelector(select="all")
            ),
        )
        for feature in unsupported:
            with self.subTest(feature=feature.TYPE):
                part = Part(
                    schema_version="1.0.0",
                    units="mm",
                    name="p",
                    features=(feature,),
                )
                with self.assertRaises(UnsupportedGeometryError) as caught:
                    build_part(part)
                self.assertIn(feature.TYPE, str(caught.exception))

    def test_multiple_feature_histories_remain_rejected(self) -> None:
        """A cylinder plus a through-hole is a valid V1 part, but not buildable.

        Two constructive features would leave two solids and fail rule S9, so
        the valid multi-feature shape to test with is a modifier on the
        cylinder -- which the engine must still refuse.
        """
        document = cylinder_document()
        document["features"].append(
            {
                "id": "bore",
                "type": "through_hole",
                "target": "pin",
                "diameter": 8,
                "position": {"x": 10, "y": 20, "z": 30},
            }
        )
        part = self.part_from(document)  # valid specification
        self.assertEqual(len(part.features), 2)
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("exactly one feature", str(caught.exception))

    def test_the_engine_still_refuses_a_raw_dictionary(self) -> None:
        with self.assertRaises(TypeError):
            build_part(cylinder_document())  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
