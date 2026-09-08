"""The V1 ``fillet`` modifier across the local CAD pipeline.

Constant-radius rounding of selected edges (Section C.5), built on the Stage 12
selector layer. Nothing here re-implements selection, and success is never
inferred from an absent exception: every passing case is checked against the
kernel's own surface types, radii, axes and point classification.

Volume references are analytic where a closed form exists -- the corner-blend
volume for the axis-parallel case, and the Minkowski form of a rounded box for
the all-edges case -- never read back from the kernel.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cad_core import validate
from cad_core.edge_selection import select_edges
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
    EdgeSelector,
    Fillet,
    Part,
    Position,
    Size,
)
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl, read_stl

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres.
TOLERANCE_MM = 1e-6

#: Blend radii read back from a kernel surface, in millimetres. Measured to
#: come back exact (2.0 to twelve decimals), so this is headroom.
RADIUS_TOLERANCE_MM = 1e-9

#: Volume, in cubic millimetres. The analytic references are irrational, so
#: agreement is limited by double precision.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Linear deflection used by STL export and the render model.
LINEAR_DEFLECTION_MM = 0.01

#: How far a tessellated vertex may deviate from the true surface.
MESH_TOLERANCE_MM = LINEAR_DEFLECTION_MM + TOLERANCE_MM

#: Unit-length check for normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

#: Alignment between a blend-face vertex normal and the exact radial
#: direction. A vertex normal averages adjacent facets, so it is only
#: approximately radial on a tessellated surface.
RADIAL_NORMAL_TOLERANCE = 1e-2

# --- the primary test geometry ---------------------------------------------

PLATE_SIZE = (100.0, 60.0, 10.0)
PLATE_VOLUME = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
FILLET_RADIUS = 2.0

#: Rounding one convex vertical corner of a prism removes the material between
#: the square corner and the quarter-cylinder: (r^2 - pi*r^2/4) per unit
#: height. Four corners over the full 10 mm thickness.
CORNER_LOSS = (
    4.0 * (FILLET_RADIUS**2 - math.pi * FILLET_RADIUS**2 / 4.0) * PLATE_SIZE[2]
)
EXPECTED_Z_VOLUME = PLATE_VOLUME - CORNER_LOSS

#: The four blend axes: each corner's arc centre sits r inside the corner.
EXPECTED_BLEND_AXES = (
    (2.0, 2.0),
    (2.0, 58.0),
    (98.0, 2.0),
    (98.0, 58.0),
)

EXPECTED_MINIMUM = (0.0, 0.0, 0.0)
EXPECTED_MAXIMUM = (100.0, 60.0, 10.0)

HOLE_DIAMETER = 20.0
HOLE_CENTRE = (20.0, 20.0)
DRILLED_VOLUME = PLATE_VOLUME - math.pi * (HOLE_DIAMETER / 2.0) ** 2 * PLATE_SIZE[2]


def rounded_box_volume(size: Tuple[float, float, float], radius: float) -> float:
    """Exact volume of a box with every edge and corner rounded at ``radius``.

    The solid is the Minkowski sum of the shrunken box with a ball of that
    radius, so ``V = pqs + 2r(pq+qs+ps) + pi*r^2*(p+q+s) + (4/3)*pi*r^3``
    where ``p, q, s`` are the dimensions reduced by ``2r``. Derived here, not
    read from the kernel.
    """
    p, q, s = (extent - 2.0 * radius for extent in size)
    return (
        p * q * s
        + 2.0 * radius * (p * q + q * s + p * s)
        + math.pi * radius**2 * (p + q + s)
        + (4.0 / 3.0) * math.pi * radius**3
    )


def document(
    features: List[Dict[str, Any]], name: str = "filleted-plate"
) -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": name,
        "features": features,
    }


def plate_feature(size: Tuple[float, float, float] = PLATE_SIZE) -> Dict[str, Any]:
    return {
        "id": "plate",
        "type": "box",
        "size": {"x": size[0], "y": size[1], "z": size[2]},
        "position": {"x": 0, "y": 0, "z": 0},
    }


def hole_feature() -> Dict[str, Any]:
    return {
        "id": "bore",
        "type": "through_hole",
        "target": "plate",
        "diameter": HOLE_DIAMETER,
        "position": {"x": HOLE_CENTRE[0], "y": HOLE_CENTRE[1], "z": 0},
        "axis": "+Z",
    }


def fillet_feature(
    radius: float = FILLET_RADIUS,
    select: str = "axis_parallel",
    axis: str = "Z",
    target: str = "plate",
) -> Dict[str, Any]:
    edges: Dict[str, Any] = {"select": select}
    if select == "axis_parallel":
        edges["axis"] = axis
    return {
        "id": "round",
        "type": "fillet",
        "target": target,
        "radius": radius,
        "edges": edges,
    }


def cylinder_feature(
    identifier: str = "pin",
    diameter: float = 20.0,
    height: float = 50.0,
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis: str = "+Z",
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "type": "cylinder",
        "diameter": diameter,
        "height": height,
        "position": {"x": position[0], "y": position[1], "z": position[2]},
        "axis": axis,
    }


class FilletTestCase(unittest.TestCase):
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
        """The primary case: 100x60x10 plate, r=2 on the vertical edges."""
        return self.build_document(document([plate_feature(), fillet_feature()]))

    def plain_plate(self) -> LocalCadResult:
        return self.build_document(document([plate_feature()], "plain"))

    def surface_census(self, shape: Any) -> Dict[str, int]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        counts: Dict[str, int] = {}
        for face in shape.Faces():
            name = str(BRepAdaptor_Surface(face.wrapped).GetType()).rsplit(".", 1)[-1]
            counts[name] = counts.get(name, 0) + 1
        return counts

    def cylindrical_faces(self, shape: Any) -> List[Any]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        return [
            BRepAdaptor_Surface(face.wrapped)
            for face in shape.Faces()
            if BRepAdaptor_Surface(face.wrapped).GetType()
            == GeomAbs_SurfaceType.GeomAbs_Cylinder
        ]

    def classify(self, shape: Any, point: Tuple[float, float, float]) -> Any:
        import cadquery as cq
        from OCP.BRepClass3d import BRepClass3d_SolidClassifier

        classifier = BRepClass3d_SolidClassifier(shape.wrapped)
        classifier.Perform(cq.Vector(*point).toPnt(), 1e-7)
        return classifier.State()

    def fingerprint(self, shape: Any) -> Tuple:
        return (
            shape.ShapeType(),
            round(shape.Volume(), 9),
            len(shape.Faces()),
            len(shape.Edges()),
            len(shape.Vertices()),
            shape.isValid(),
            tuple(
                (
                    round(edge.Length(), 9),
                    tuple(round(v, 9) for v in edge.startPoint().toTuple()),
                )
                for edge in shape.Edges()
            ),
        )

    def assertTripleAlmostEqual(
        self, actual: Any, expected: tuple, delta: float = TOLERANCE_MM
    ) -> None:
        values = (
            (actual.x, actual.y, actual.z) if hasattr(actual, "x") else tuple(actual)
        )
        for axis, (got, want) in enumerate(zip(values, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- primary case ----------------------------------------------------------


class TestPrimaryFillet(FilletTestCase):
    """plate 100x60x10, radius 2, edges = axis_parallel Z."""

    def test_the_selector_matches_the_four_vertical_edges(self) -> None:
        """Established through the selector layer, not assumed."""
        shape = self.plain_plate().shape
        selected = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        self.assertEqual(len(selected), 4)
        for edge in selected:
            self.assertAlmostEqual(edge.Length(), PLATE_SIZE[2], delta=TOLERANCE_MM)

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

    def test_the_modifier_id_never_names_a_solid(self) -> None:
        self.assertNotEqual(self.build().feature_id, "round")

    def test_volume_matches_the_analytic_corner_loss(self) -> None:
        result = self.build()
        self.assertAlmostEqual(
            result.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertAlmostEqual(
            PLATE_VOLUME - result.volume(), CORNER_LOSS, delta=VOLUME_TOLERANCE_MM3
        )

    def test_volume_decreases_from_the_unfilleted_box(self) -> None:
        self.assertLess(self.build().volume(), self.plain_plate().volume())

    def test_bounding_box_extents_are_unchanged(self) -> None:
        box = self.build().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_the_four_corners_became_cylindrical_faces(self) -> None:
        """Real surface geometry: type, radius and axis all from the kernel."""
        result = self.build()
        self.assertEqual(
            self.surface_census(result.shape),
            {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 4},
        )
        blends = self.cylindrical_faces(result.shape)
        self.assertEqual(len(blends), 4)
        seen = set()
        for surface in blends:
            cylinder = surface.Cylinder()
            self.assertAlmostEqual(
                cylinder.Radius(), FILLET_RADIUS, delta=RADIUS_TOLERANCE_MM
            )
            direction = cylinder.Axis().Direction()
            self.assertAlmostEqual(abs(direction.Z()), 1.0, delta=RADIUS_TOLERANCE_MM)
            self.assertAlmostEqual(direction.X(), 0.0, delta=RADIUS_TOLERANCE_MM)
            self.assertAlmostEqual(direction.Y(), 0.0, delta=RADIUS_TOLERANCE_MM)
            location = cylinder.Axis().Location()
            seen.add((round(location.X(), 6), round(location.Y(), 6)))
        self.assertEqual(seen, set(EXPECTED_BLEND_AXES))

    def test_the_sharp_corner_material_is_actually_gone(self) -> None:
        """Point classification: inside the box before, outside after."""
        from OCP.TopAbs import TopAbs_State

        plain = self.plain_plate().shape
        rounded = self.build().shape
        corners = (
            (0.1, 0.1, 5.0),
            (99.9, 0.1, 5.0),
            (0.1, 59.9, 5.0),
            (99.9, 59.9, 5.0),
        )
        for corner in corners:
            with self.subTest(corner=corner):
                self.assertEqual(self.classify(plain, corner), TopAbs_State.TopAbs_IN)
                self.assertEqual(
                    self.classify(rounded, corner), TopAbs_State.TopAbs_OUT
                )

    def test_the_faces_themselves_are_not_moved(self) -> None:
        """Only the corners change: a point just inside a face stays inside."""
        from OCP.TopAbs import TopAbs_State

        rounded = self.build().shape
        for point in ((0.1, 30.0, 5.0), (50.0, 0.1, 5.0), (50.0, 30.0, 0.1)):
            with self.subTest(point=point):
                self.assertEqual(self.classify(rounded, point), TopAbs_State.TopAbs_IN)

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        result = self.build()
        self.assertEqual(len(result.shape.Faces()), 10)
        self.assertEqual(len(result.shape.Edges()), 24)
        self.assertEqual(len(result.shape.Vertices()), 16)

    def test_a_larger_admissible_radius_also_works(self) -> None:
        """Measured bracket for this geometry, not a rule: 29 works, 30 does not."""
        result = self.build_document(
            document([plate_feature(), fillet_feature(radius=29.0)], "wide")
        )
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(len(self.cylindrical_faces(result.shape)), 4)


# --- select all ------------------------------------------------------------


class TestAllEdgesFillet(FilletTestCase):
    def all_edges(self, radius: float = FILLET_RADIUS) -> LocalCadResult:
        return self.build_document(
            document(
                [plate_feature(), fillet_feature(radius=radius, select="all")],
                "rounded-box",
            )
        )

    def test_the_selector_matches_all_twelve_edges(self) -> None:
        shape = self.plain_plate().shape
        self.assertEqual(len(select_edges(shape, EdgeSelector(select="all"))), 12)

    def test_an_admissible_radius_succeeds(self) -> None:
        result = self.all_edges()
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_every_edge_and_corner_was_handled(self) -> None:
        """Twelve edge blends and eight vertex blends, from surface types.

        This is what "all selected edges are handled" looks like in topology:
        one cylinder per edge and one sphere per corner, all at the requested
        radius.
        """
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        result = self.all_edges()
        self.assertEqual(
            self.surface_census(result.shape),
            {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 12, "GeomAbs_Sphere": 8},
        )
        for face in result.shape.Faces():
            surface = BRepAdaptor_Surface(face.wrapped)
            kind = surface.GetType()
            if kind == GeomAbs_SurfaceType.GeomAbs_Cylinder:
                self.assertAlmostEqual(
                    surface.Cylinder().Radius(),
                    FILLET_RADIUS,
                    delta=RADIUS_TOLERANCE_MM,
                )
            elif kind == GeomAbs_SurfaceType.GeomAbs_Sphere:
                self.assertAlmostEqual(
                    surface.Sphere().Radius(),
                    FILLET_RADIUS,
                    delta=RADIUS_TOLERANCE_MM,
                )

    def test_volume_matches_the_rounded_box_closed_form(self) -> None:
        """Minkowski reference, derived in this file and measured exact."""
        result = self.all_edges()
        self.assertAlmostEqual(
            result.volume(),
            rounded_box_volume(PLATE_SIZE, FILLET_RADIUS),
            delta=VOLUME_TOLERANCE_MM3,
        )
        self.assertLess(result.volume(), PLATE_VOLUME)

    def test_all_eight_corners_are_gone(self) -> None:
        from OCP.TopAbs import TopAbs_State

        rounded = self.all_edges().shape
        for x in (0.1, 99.9):
            for y in (0.1, 59.9):
                for z in (0.1, 9.9):
                    with self.subTest(corner=(x, y, z)):
                        self.assertEqual(
                            self.classify(rounded, (x, y, z)), TopAbs_State.TopAbs_OUT
                        )

    def test_bounding_box_extents_are_unchanged(self) -> None:
        box = self.all_edges().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        result = self.all_edges()
        self.assertEqual(len(result.shape.Faces()), 26)
        self.assertEqual(len(result.shape.Edges()), 48)
        self.assertEqual(len(result.shape.Vertices()), 24)

    def test_an_over_large_radius_is_rejected(self) -> None:
        """Half the plate thickness: measured to fail, and reported as E5."""
        with self.assertRaises(GeometryOperationError) as caught:
            self.all_edges(radius=6.0)
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("'round'", message)


# --- rule E4 ---------------------------------------------------------------


class TestRuleE4(FilletTestCase):
    """A selector that matches nothing, without inventing a fake edge.

    A ``+Z`` cylinder has no straight edge parallel to X or Y -- measured in
    Stage 12 -- so an ``axis_parallel X`` selector on one genuinely matches
    zero edges.
    """

    def empty_document(self, axis: str = "X") -> Dict[str, Any]:
        return document(
            [
                cylinder_feature(),
                fillet_feature(radius=1.0, axis=axis, target="pin"),
            ],
            "no-match",
        )

    def test_the_selector_really_matches_nothing(self) -> None:
        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                self.assertEqual(
                    select_edges(
                        shape, EdgeSelector(select="axis_parallel", axis=axis)
                    ),
                    (),
                )

    def test_a_zero_match_selector_reports_e4(self) -> None:
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(self.empty_document(axis))
                message = str(caught.exception)
                self.assertIn("E4", message)
                self.assertIn("'round'", message)
                self.assertIn("'pin'", message)

    def test_no_geometry_escapes_an_e4_failure(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(self.empty_document())

    def test_e4_does_not_claim_another_rule(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.empty_document())
        message = str(caught.exception)
        for other in ("E5", "E3", "E2", "E1"):
            with self.subTest(other=other):
                self.assertNotIn(other, message)

    def test_the_selector_layer_itself_does_not_raise_e4(self) -> None:
        """The rule belongs to the modifier, which can name its own feature."""
        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        self.assertEqual(
            select_edges(shape, EdgeSelector(select="axis_parallel", axis="X")), ()
        )


# --- rule E5 ---------------------------------------------------------------

THIN_SIZE = (100.0, 60.0, 3.0)


class TestRuleE5(FilletTestCase):
    """Admissibility, established from measured kernel behaviour.

    Three distinct failure modes were measured, and all three are reported as
    E5:

    1. ``Build()`` raises ``Standard_Failure`` -- filleting a cylinder's seam.
    2. ``IsDone()`` is false -- radius 30 on the plate's vertical edges.
    3. ``IsDone()`` is true but the result fails the kernel's own validity
       analysis -- radius 6 on all twelve edges, which comes back with a
       *larger* volume than the original.

    The third is why ``IsDone()`` alone is not the test.
    """

    def thin_plate(self) -> LocalCadResult:
        return self.build_document(document([plate_feature(THIN_SIZE)], "thin"))

    def test_a_clearly_admissible_radius_succeeds(self) -> None:
        result = self.build_document(
            document([plate_feature(), fillet_feature(radius=0.5)], "small")
        )
        self.assertTrue(result.is_solid())
        self.assertEqual(len(self.cylindrical_faces(result.shape)), 4)

    def test_a_clearly_too_large_radius_is_rejected(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document([plate_feature(), fillet_feature(radius=30.0)], "huge")
            )
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("not admissible", message)

    def test_the_not_done_case_reports_kernel_diagnostics(self) -> None:
        """The message carries the kernel's own faulty-contour counts."""
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document([plate_feature(), fillet_feature(radius=30.0)], "huge")
            )
        message = str(caught.exception)
        self.assertIn("faulty", message)
        self.assertIn("contour", message)

    def test_the_done_but_invalid_case_is_also_e5(self) -> None:
        """Radius 6 on all edges: the kernel completes and hands back rubbish.

        Measured: ``IsDone()`` is true, one solid comes back, and its volume
        exceeds the original box. Only the validity analysis catches it.
        """
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document(
                    [plate_feature(), fillet_feature(radius=6.0, select="all")],
                    "invalid",
                )
            )
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("validity", message)

    def test_the_done_but_invalid_case_really_is_what_it_claims(self) -> None:
        """Direct kernel measurement behind the previous test.

        Driven through the OCC builder so the observation is recorded rather
        than asserted second-hand: done, one solid, invalid, volume up.
        """
        import cadquery as cq
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        shape = self.plain_plate().shape
        edges = select_edges(shape, EdgeSelector(select="all"))
        builder = BRepFilletAPI_MakeFillet(shape.wrapped)
        for edge in edges:
            builder.Add(6.0, edge.wrapped)
        builder.Build()
        self.assertTrue(builder.IsDone())
        result = cq.Shape.cast(builder.Shape())
        self.assertEqual(len(result.Solids()), 1)
        self.assertFalse(result.isValid())
        self.assertGreater(result.Volume(), PLATE_VOLUME)

    def test_a_mixed_selection_fails_as_a_whole(self) -> None:
        """One admissible edge set, one inadmissible, in one selector.

        On a 100x60x3 plate at radius 2 the vertical edges are admissible and
        the horizontal ones are not -- each established separately below. The
        combined ``all`` selector must therefore fail, and must not quietly
        round only the vertical corners.
        """
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document(
                    [
                        plate_feature(THIN_SIZE),
                        fillet_feature(radius=2.0, select="all"),
                    ],
                    "mixed",
                )
            )
        self.assertIn("E5", str(caught.exception))

    def test_the_admissible_half_of_the_mixed_case_succeeds_alone(self) -> None:
        result = self.build_document(
            document(
                [plate_feature(THIN_SIZE), fillet_feature(radius=2.0, axis="Z")],
                "thin-vertical",
            )
        )
        self.assertTrue(result.is_solid())
        self.assertEqual(len(self.cylindrical_faces(result.shape)), 4)

    def test_the_inadmissible_half_of_the_mixed_case_fails_alone(self) -> None:
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(
                        document(
                            [
                                plate_feature(THIN_SIZE),
                                fillet_feature(radius=2.0, axis=axis),
                            ],
                            "thin-horizontal",
                        )
                    )
                self.assertIn("E5", str(caught.exception))

    def test_the_mixed_case_does_not_partially_round_the_thin_plate(self) -> None:
        """No subset result is reachable: the whole feature produced nothing."""
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document(
                    [
                        plate_feature(THIN_SIZE),
                        fillet_feature(radius=2.0, select="all"),
                    ],
                    "mixed",
                )
            )
        # The plain thin plate still builds, unrounded and unchanged.
        plain = self.thin_plate()
        self.assertEqual(self.surface_census(plain.shape), {"GeomAbs_Plane": 6})
        self.assertAlmostEqual(
            plain.volume(),
            THIN_SIZE[0] * THIN_SIZE[1] * THIN_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_a_non_positive_radius_never_reaches_the_kernel(self) -> None:
        """The validator stops it first (S16); the engine also refuses."""
        for radius in (0, -1, -0.5):
            with self.subTest(radius=radius):
                result = validate(
                    document([plate_feature(), fillet_feature(radius=radius)], "bad")
                )
                self.assertFalse(result.valid)
                self.assertIn("S16", result.rule_codes())

        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Fillet(
                    id="round",
                    target="plate",
                    radius=0.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        self.assertIn("S16", str(caught.exception))


# --- the cylinder seam -----------------------------------------------------


class TestCylinderSeam(FilletTestCase):
    """Stage 12's finding, carried into a real operation.

    ``axis_parallel Z`` on a cylinder matches the cylindrical face's
    parameterisation seam, which is a genuine straight edge. What the kernel
    does with it was measured, not predicted.
    """

    def test_the_selector_still_matches_only_the_seam(self) -> None:
        """Selector behaviour is unchanged by this stage."""
        from cad_core.edge_selection import edge_curve_type

        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        selected = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        self.assertEqual(len(selected), 1)
        self.assertEqual(edge_curve_type(selected[0]), "GeomAbs_Line")

    def test_filleting_the_seam_alone_is_refused(self) -> None:
        """Measured outcome: the kernel raises, and it is reported as E5.

        ``BRepFilletAPI_MakeFillet.Build()`` throws ``Standard_Failure`` for
        this contour at every radius tried (0.5 and 2.0 mm). The engine does
        not swallow it and does not return the unfilleted cylinder.
        """
        for radius in (0.5, 2.0):
            with self.subTest(radius=radius):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(
                        document(
                            [
                                cylinder_feature(),
                                fillet_feature(radius=radius, axis="Z", target="pin"),
                            ],
                            "seam",
                        )
                    )
                message = str(caught.exception)
                self.assertIn("E5", message)
                self.assertIn("refused", message)

    def test_the_raise_is_a_kernel_measurement(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        seam = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeFillet(shape.wrapped)
        builder.Add(1.0, seam[0].wrapped)
        with self.assertRaises(Exception) as caught:
            builder.Build()
        self.assertEqual(type(caught.exception).__name__, "Standard_Failure")

    def test_select_all_on_a_cylinder_does_succeed(self) -> None:
        """Measured, and initially surprising: the seam is in this selection.

        With both circular rims included the blend resolves, giving a cylinder
        with two rounded rims: 5 faces, one valid solid. Recorded as an
        observation about the kernel, not a rule.
        """
        result = self.build_document(
            document(
                [
                    cylinder_feature(),
                    fillet_feature(radius=1.0, select="all", target="pin"),
                ],
                "rounded-pin",
            )
        )
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(len(result.shape.Faces()), 5)
        self.assertLess(result.volume(), math.pi * 10.0**2 * 50.0)
        self.assertEqual(result.feature_id, "pin")


# --- the drilled plate -----------------------------------------------------


class TestDrilledPlate(FilletTestCase):
    """100x60x10 plate with one 20 mm through-hole, then filleted."""

    def drilled(self) -> LocalCadResult:
        return self.build_document(
            document([plate_feature(), hole_feature()], "drilled")
        )

    def filleted(
        self, radius: float = FILLET_RADIUS, **overrides: Any
    ) -> LocalCadResult:
        return self.build_document(
            document(
                [
                    plate_feature(),
                    hole_feature(),
                    fillet_feature(radius=radius, **overrides),
                ],
                "drilled-filleted",
            )
        )

    def test_the_selector_matches_four_corners_and_the_cavity_seam(self) -> None:
        selected = select_edges(
            self.drilled().shape, EdgeSelector(select="axis_parallel", axis="Z")
        )
        self.assertEqual(len(selected), 5)

    def test_the_vertical_fillet_succeeds_with_the_seam_included(self) -> None:
        """The seam is not excluded from the selector, and the blend works."""
        result = self.filleted()
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_volume_shows_the_seam_blend_removed_nothing(self) -> None:
        """Measured and then explained, not the other way round.

        The result equals the drilled volume minus exactly the four corner
        blends -- so blending the cavity seam changed no material. That is
        consistent with the cylindrical face being smooth across its own
        parameterisation seam.
        """
        result = self.filleted()
        self.assertAlmostEqual(
            result.volume(), DRILLED_VOLUME - CORNER_LOSS, delta=VOLUME_TOLERANCE_MM3
        )

    def test_topology_shows_the_seam_blend_added_no_face(self) -> None:
        """7 faces + 4 corner blends = 11. The seam contributed none."""
        drilled, filleted = self.drilled(), self.filleted()
        self.assertEqual(len(drilled.shape.Faces()), 7)
        self.assertEqual(len(filleted.shape.Faces()), 11)
        self.assertEqual(len(filleted.shape.Edges()), 27)
        self.assertEqual(len(filleted.shape.Vertices()), 18)
        self.assertEqual(
            self.surface_census(filleted.shape),
            {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 5},
        )

    def test_the_hole_survives_the_fillet(self) -> None:
        """The cavity is still there, still 20 mm, still through."""
        import cadquery as cq
        from OCP.TopAbs import TopAbs_State

        result = self.filleted()
        walls = [
            surface
            for surface in self.cylindrical_faces(result.shape)
            if abs(surface.Cylinder().Radius() - HOLE_DIAMETER / 2.0) <= TOLERANCE_MM
        ]
        self.assertEqual(len(walls), 1)
        for z in (0.1, 5.0, 9.9):
            with self.subTest(z=z):
                self.assertEqual(
                    self.classify(result.shape, (HOLE_CENTRE[0], HOLE_CENTRE[1], z)),
                    TopAbs_State.TopAbs_OUT,
                )

    def test_the_corners_are_rounded_at_the_requested_radius(self) -> None:
        result = self.filleted()
        blends = [
            surface
            for surface in self.cylindrical_faces(result.shape)
            if abs(surface.Cylinder().Radius() - FILLET_RADIUS) <= RADIUS_TOLERANCE_MM
        ]
        self.assertEqual(len(blends), 4)
        seen = {
            (
                round(surface.Cylinder().Axis().Location().X(), 6),
                round(surface.Cylinder().Axis().Location().Y(), 6),
            )
            for surface in blends
        }
        self.assertEqual(seen, set(EXPECTED_BLEND_AXES))

    def test_select_all_with_a_small_radius_succeeds(self) -> None:
        result = self.filleted(radius=1.0, select="all")
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(len(result.shape.Faces()), 29)
        self.assertEqual(len(result.shape.Edges()), 55)
        self.assertEqual(len(result.shape.Vertices()), 28)
        self.assertLess(result.volume(), DRILLED_VOLUME)

    def test_select_all_rounds_the_hole_rims_too(self) -> None:
        """With ``all`` the rims *are* selected, and they are toroidal blends."""
        result = self.filleted(radius=1.0, select="all")
        census = self.surface_census(result.shape)
        self.assertEqual(census.get("GeomAbs_Torus"), 2)
        self.assertGreater(census.get("GeomAbs_Cylinder", 0), 0)

    def test_the_horizontal_selectors_also_work(self) -> None:
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                result = self.filleted(radius=2.0, axis=axis)
                self.assertTrue(result.is_solid())
                self.assertEqual(result.solid_count(), 1)

    def test_bounding_box_extents_are_unchanged(self) -> None:
        box = self.filleted().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)


# --- feature history -------------------------------------------------------


class TestFeatureHistory(FilletTestCase):
    def test_box_then_fillet(self) -> None:
        result = self.build()
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)

    def test_box_then_through_hole_then_fillet(self) -> None:
        result = self.build_document(
            document(
                [plate_feature(), hole_feature(), fillet_feature()], "hole-then-round"
            )
        )
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertAlmostEqual(
            result.volume(), DRILLED_VOLUME - CORNER_LOSS, delta=VOLUME_TOLERANCE_MM3
        )

    def test_box_then_subtract_then_fillet(self) -> None:
        """The fillet sees the final solid, whatever produced it."""
        result = self.build_document(
            document(
                [
                    plate_feature(),
                    cylinder_feature(
                        "tool", diameter=20.0, height=20.0, position=(20.0, 20.0, -5.0)
                    ),
                    {
                        "id": "cut",
                        "type": "subtract",
                        "target": "plate",
                        "tools": ["tool"],
                    },
                    fillet_feature(),
                ],
                "subtract-then-round",
            )
        )
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertAlmostEqual(
            result.volume(), DRILLED_VOLUME - CORNER_LOSS, delta=VOLUME_TOLERANCE_MM3
        )

    def test_two_fillets_in_sequence(self) -> None:
        """Each replaces the target in place, so the second sees the first."""
        doc = document([plate_feature(), fillet_feature()], "twice")
        doc["features"].append(
            {
                "id": "round2",
                "type": "fillet",
                "target": "plate",
                "radius": 1.0,
                "edges": {"select": "axis_parallel", "axis": "X"},
            }
        )
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertLess(result.volume(), EXPECTED_Z_VOLUME)

    def test_a_fillet_cannot_target_a_consumed_tool(self) -> None:
        doc = document(
            [
                plate_feature(),
                cylinder_feature(
                    "tool", diameter=20.0, height=20.0, position=(20.0, 20.0, -5.0)
                ),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
                fillet_feature(target="tool"),
            ],
            "consumed",
        )
        self.assertIn("S6", validate(doc).rule_codes())

    def test_an_unresolvable_target_is_reported_by_the_engine(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Fillet(
                    id="round",
                    target="absent",
                    radius=2.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S6", message)
        self.assertIn("'absent'", message)

    def test_a_chamfer_after_a_fillet_now_builds(self) -> None:
        """Stage 14 implemented 'chamfer', completing the V1 feature set.

        The two modifiers are applied to disjoint selections; the reverse
        order (fillet Y then chamfer X) was measured to fail E5 on this body.
        """
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Chamfer(
                    id="bevel",
                    target="plate",
                    distance=1.0,
                    edges=EdgeSelector(select="axis_parallel", axis="X"),
                ),
                Fillet(
                    id="round",
                    target="plate",
                    radius=1.0,
                    edges=EdgeSelector(select="axis_parallel", axis="Y"),
                ),
            ),
        )
        result = build_part(part)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_a_raw_dictionary_cannot_invoke_the_build_api(self) -> None:
        with self.assertRaises(TypeError):
            doc = document([plate_feature(), fillet_feature()])
            build_part(doc)  # type: ignore[arg-type]


# --- failure atomicity -----------------------------------------------------


class TestFailureAtomicity(FilletTestCase):
    """A failed fillet must leave nothing behind and corrupt nothing."""

    def test_the_source_shape_is_unchanged_by_a_not_done_build(self) -> None:
        """Measured at the kernel level, not assumed of CadQuery."""
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        shape = self.plain_plate().shape
        before = self.fingerprint(shape)
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeFillet(shape.wrapped)
        for edge in edges:
            builder.Add(30.0, edge.wrapped)
        builder.Build()
        self.assertFalse(builder.IsDone())
        # Deliberately never asking for builder.Shape() here -- see the module
        # docstring in local_cad: doing so on a not-done builder corrupts
        # kernel state for later fillets in the same process.
        self.assertEqual(before, self.fingerprint(shape))

    def test_the_source_shape_is_unchanged_by_a_raising_build(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        before = self.fingerprint(shape)
        seam = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeFillet(shape.wrapped)
        builder.Add(1.0, seam[0].wrapped)
        with self.assertRaises(Exception):
            builder.Build()
        self.assertEqual(before, self.fingerprint(shape))

    def test_the_source_shape_is_unchanged_by_a_successful_build(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet

        shape = self.plain_plate().shape
        before = self.fingerprint(shape)
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeFillet(shape.wrapped)
        for edge in edges:
            builder.Add(FILLET_RADIUS, edge.wrapped)
        builder.Build()
        self.assertTrue(builder.IsDone())
        import cadquery as cq

        cq.Shape.cast(builder.Shape())
        self.assertEqual(before, self.fingerprint(shape))

    def test_a_failed_fillet_produces_no_result_object(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document([plate_feature(), fillet_feature(radius=30.0)], "huge")
            )

    def test_the_engine_still_works_after_a_failed_fillet(self) -> None:
        """Regression guard for a measured kernel crash hazard.

        Asking a not-done ``BRepFilletAPI_MakeFillet`` for its shape raises
        and leaves state that segfaults a later fillet in the same process.
        The engine never does that, so a failed fillet must be followed by a
        working one -- several times over, interleaved.
        """
        for _ in range(3):
            with self.assertRaises(GeometryOperationError):
                self.build_document(
                    document([plate_feature(), fillet_feature(radius=30.0)], "huge")
                )
            result = self.build()
            self.assertTrue(result.is_solid())
            self.assertAlmostEqual(
                result.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
            )

    def test_rebuilding_after_a_failure_gives_the_original_geometry(self) -> None:
        reference = self.fingerprint(self.plain_plate().shape)
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document(
                    [
                        plate_feature(THIN_SIZE),
                        fillet_feature(radius=2.0, select="all"),
                    ],
                    "mixed",
                )
            )
        self.assertEqual(reference, self.fingerprint(self.plain_plate().shape))

    def test_a_failed_fillet_after_a_hole_leaves_the_hole_intact(self) -> None:
        """The earlier features are re-evaluated from scratch, so nothing sticks."""
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document(
                    [plate_feature(), hole_feature(), fillet_feature(radius=40.0)],
                    "bad-round",
                )
            )
        drilled = self.build_document(
            document([plate_feature(), hole_feature()], "drilled")
        )
        self.assertAlmostEqual(
            drilled.volume(), DRILLED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(drilled.shape.Faces()), 7)


# --- STEP ------------------------------------------------------------------


class TestFilletStep(FilletTestCase):
    def test_step_round_trip(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "f.step"))
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_step_preserves_the_blend_surfaces(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "f.step"))
        self.assertEqual(
            self.surface_census(imported.shape),
            {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 4},
        )
        self.assertEqual(len(imported.shape.Faces()), 10)
        self.assertEqual(len(imported.shape.Edges()), 24)
        self.assertEqual(len(imported.shape.Vertices()), 16)
        for surface in self.cylindrical_faces(imported.shape):
            self.assertAlmostEqual(
                surface.Cylinder().Radius(), FILLET_RADIUS, delta=RADIUS_TOLERANCE_MM
            )

    def test_step_round_trips_are_geometrically_deterministic(self) -> None:
        measurements = set()
        digests = set()
        for index in range(3):
            written = export_step(self.build(), self.tmp / f"s{index}.step")
            imported = read_step(written)
            measurements.add(
                (
                    imported.solid_count(),
                    round(imported.volume(), 6),
                    len(imported.shape.Faces()),
                )
            )
            digests.add(hashlib.sha256(written.read_bytes()).hexdigest())
        self.assertEqual(len(measurements), 1)
        # Measured: STEP bytes differ per export (header timestamp and an
        # incrementing translator instance number).
        self.assertEqual(len(digests), 3)


# --- IGES ------------------------------------------------------------------


class TestFilletIges(FilletTestCase):
    def test_iges_round_trip(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "f.igs"))
        self.assertEqual(imported.shape_type(), "Solid")
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_iges_preserves_the_measured_topology(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "f.igs"))
        self.assertEqual(imported.face_count(), 10)
        self.assertEqual(imported.edge_count(), 24)
        self.assertEqual(imported.vertex_count(), 16)
        self.assertEqual(
            self.surface_census(imported.shape),
            {"GeomAbs_Plane": 6, "GeomAbs_Cylinder": 4},
        )

    def test_iges_round_trips_are_geometrically_deterministic(self) -> None:
        measurements = set()
        for index in range(3):
            imported = read_iges(export_iges(self.build(), self.tmp / f"i{index}.igs"))
            measurements.add(
                (
                    imported.shape_type(),
                    imported.solid_count(),
                    round(imported.volume(), 6),
                    imported.face_count(),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- STL -------------------------------------------------------------------


class TestFilletStl(FilletTestCase):
    def test_stl_is_structurally_valid(self) -> None:
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "f.stl"))
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        self.assertGreater(facts.declared_triangles, 0)

    def test_the_blends_add_triangles(self) -> None:
        plain = read_stl(export_stl(self.plain_plate(), self.tmp / "plain.stl"))
        rounded = read_stl(export_stl(self.build(), self.tmp / "f.stl"))
        self.assertEqual(plain.triangle_count(), 12)
        self.assertGreater(rounded.triangle_count(), plain.triangle_count())

    def test_mesh_bounds_approximate_the_brep(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "f.stl"))
        box = mesh.bounding_box()
        self.assertTripleAlmostEqual(
            box.minimum, EXPECTED_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.maximum, EXPECTED_MAXIMUM, delta=MESH_TOLERANCE_MM
        )

    def test_the_rounded_corners_are_represented_in_the_mesh(self) -> None:
        """No node in the removed wedge, and nodes on each blend arc."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "f.stl"))
        nodes = mesh.nodes()
        for centre in EXPECTED_BLEND_AXES:
            on_arc = [
                node
                for node in nodes
                if abs(
                    math.hypot(node[0] - centre[0], node[1] - centre[1])
                    - FILLET_RADIUS
                )
                <= MESH_TOLERANCE_MM
            ]
            with self.subTest(centre=centre):
                self.assertGreater(len(on_arc), 2)

        # The old sharp corners are gone: no node sits in the wedge outside
        # the blend arc.
        for corner, centre in zip(
            ((0.0, 0.0), (0.0, 60.0), (100.0, 0.0), (100.0, 60.0)),
            ((2.0, 2.0), (2.0, 58.0), (98.0, 2.0), (98.0, 58.0)),
        ):
            for node in nodes:
                inside_wedge = (
                    abs(node[0] - corner[0]) < FILLET_RADIUS - MESH_TOLERANCE_MM
                    and abs(node[1] - corner[1]) < FILLET_RADIUS - MESH_TOLERANCE_MM
                )
                if inside_wedge:
                    with self.subTest(corner=corner):
                        self.assertGreaterEqual(
                            math.hypot(node[0] - centre[0], node[1] - centre[1]),
                            FILLET_RADIUS - MESH_TOLERANCE_MM,
                        )

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


class TestFilletRenderModel(FilletTestCase):
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

    def test_blend_normals_point_outward_unlike_a_cavity(self) -> None:
        """A convex blend faces away from its arc centre.

        The mirror image of the drilled plate's hole wall, whose normals point
        at the hole axis: a fillet is convex, so its normals point away from
        the blend axis. Both are "out of the material".
        """
        model = self.model()
        found = 0
        for vertex, normal in zip(model.vertices, model.normals):
            for centre in EXPECTED_BLEND_AXES:
                radial = math.hypot(vertex[0] - centre[0], vertex[1] - centre[1])
                if abs(radial - FILLET_RADIUS) > TOLERANCE_MM:
                    continue
                if abs(normal[2]) > 0.5:  # a cap vertex on the blend arc
                    continue
                outward = (
                    (vertex[0] - centre[0]) / radial,
                    (vertex[1] - centre[1]) / radial,
                )
                dot = outward[0] * normal[0] + outward[1] * normal[1]
                found += 1
                self.assertGreater(dot, 1.0 - RADIAL_NORMAL_TOLERANCE)
        self.assertGreater(found, 0)

    def test_outer_planar_normals_remain_outward(self) -> None:
        model = self.model()
        expectations = (
            ((0.0, 0.0, -1.0), lambda v: abs(v[2]) < TOLERANCE_MM),
            ((0.0, 0.0, 1.0), lambda v: abs(v[2] - PLATE_SIZE[2]) < TOLERANCE_MM),
            ((-1.0, 0.0, 0.0), lambda v: abs(v[0]) < TOLERANCE_MM),
            ((1.0, 0.0, 0.0), lambda v: abs(v[0] - PLATE_SIZE[0]) < TOLERANCE_MM),
        )
        for index, (expected, predicate) in enumerate(expectations):
            matches = [
                normal
                for vertex, normal in zip(model.vertices, model.normals)
                if predicate(vertex)
                and abs(sum(a * b for a, b in zip(normal, expected)) - 1.0)
                < NORMAL_TOLERANCE
            ]
            with self.subTest(face=index):
                self.assertGreater(len(matches), 0)

    def test_winding_is_outward_for_every_triangle(self) -> None:
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

    def test_the_blend_surface_is_actually_curved_in_the_mesh(self) -> None:
        """Distinct normals across the blend, not one flat facet normal."""
        model = self.model()
        blend_normals = set()
        for vertex, normal in zip(model.vertices, model.normals):
            for centre in EXPECTED_BLEND_AXES:
                radial = math.hypot(vertex[0] - centre[0], vertex[1] - centre[1])
                if abs(radial - FILLET_RADIUS) <= TOLERANCE_MM and abs(normal[2]) < 0.5:
                    blend_normals.add(tuple(round(c, 9) for c in normal))
        self.assertGreater(len(blend_normals), 4)

    def test_render_model_serializes_and_is_deterministic(self) -> None:
        payloads = {
            json.dumps(build_render_model(self.build()).to_dict(), sort_keys=True)
            for _ in range(3)
        }
        self.assertEqual(len(payloads), 1)
        self.assertEqual(json.loads(next(iter(payloads)))["feature_id"], "plate")


# --- determinism -----------------------------------------------------------


class TestDeterminism(FilletTestCase):
    def test_repeated_builds_measure_identically(self) -> None:
        measurements = set()
        for _ in range(5):
            result = self.build()
            box = result.bounding_box()
            measurements.add(
                (
                    round(result.volume(), 9),
                    tuple(
                        round(value, 9)
                        for value in (box.minimum.x, box.minimum.y, box.minimum.z)
                    ),
                    tuple(
                        round(value, 9)
                        for value in (box.maximum.x, box.maximum.y, box.maximum.z)
                    ),
                    result.solid_count(),
                    len(result.shape.Faces()),
                    len(result.shape.Edges()),
                    len(result.shape.Vertices()),
                )
            )
        self.assertEqual(len(measurements), 1)

    def test_the_selected_edge_order_is_stable_across_builds(self) -> None:
        """The fillet is only deterministic if the selection is."""
        signatures = set()
        for _ in range(4):
            shape = self.plain_plate().shape
            signatures.add(
                tuple(
                    (
                        round(edge.Length(), 9),
                        tuple(round(v, 9) for v in edge.startPoint().toTuple()),
                    )
                    for edge in select_edges(
                        shape, EdgeSelector(select="axis_parallel", axis="Z")
                    )
                )
            )
        self.assertEqual(len(signatures), 1)

    def test_each_build_returns_a_fresh_object(self) -> None:
        self.assertIsNot(self.build(), self.build())
        self.assertIsNot(self.build().shape, self.build().shape)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
