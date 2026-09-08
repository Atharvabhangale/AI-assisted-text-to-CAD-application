"""The V1 ``chamfer`` modifier across the local CAD pipeline.

Symmetric constant-distance bevelling of selected edges (Section C.6), built on
the Stage 12 selector layer. The last V1 geometry feature.

Success is never inferred from an absent exception: the symmetric setback is
proved from the chamfer faces' own plane geometry and vertex positions, and the
volumes are checked against closed forms derived in this file.
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
    Part,
    Position,
    Size,
)
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts, export_stl, read_stl

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres. Same basis as the local engine.
TOLERANCE_MM = 1e-6

#: Setback distances read back from chamfer-face vertex positions, in
#: millimetres. Measured to come back exact, so this is headroom.
SETBACK_TOLERANCE_MM = 1e-9

#: Volume, in cubic millimetres.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Linear deflection used by STL export and the render model.
LINEAR_DEFLECTION_MM = 0.01

#: How far a tessellated vertex may deviate from the true surface.
MESH_TOLERANCE_MM = LINEAR_DEFLECTION_MM + TOLERANCE_MM

#: Unit-length and direction checks for normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

#: Agreement between a rendered triangle normal and the exact chamfer-plane
#: normal. A chamfer face is planar, so this can be tight.
PLANE_NORMAL_TOLERANCE = 1e-6

# --- the primary test geometry ---------------------------------------------

PLATE_SIZE = (100.0, 60.0, 10.0)
PLATE_VOLUME = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
CHAMFER_DISTANCE = 2.0

#: Bevelling one convex vertical corner of a prism removes a right-triangular
#: prism of leg ``d``: area d^2/2 over the full thickness. Four corners.
CORNER_LOSS = 4.0 * (CHAMFER_DISTANCE**2 / 2.0) * PLATE_SIZE[2]
EXPECTED_Z_VOLUME = PLATE_VOLUME - CORNER_LOSS

#: Where each chamfer plane meets the two faces it bevels: ``d`` from the
#: corner along each axis.
EXPECTED_SETBACK_CORNERS = (
    ((0.0, CHAMFER_DISTANCE), (CHAMFER_DISTANCE, 0.0)),
    ((0.0, 60.0 - CHAMFER_DISTANCE), (CHAMFER_DISTANCE, 60.0)),
    ((100.0 - CHAMFER_DISTANCE, 0.0), (100.0, CHAMFER_DISTANCE)),
    ((100.0 - CHAMFER_DISTANCE, 60.0), (100.0, 60.0 - CHAMFER_DISTANCE)),
)

DIAGONAL = math.sqrt(0.5)

EXPECTED_MINIMUM = (0.0, 0.0, 0.0)
EXPECTED_MAXIMUM = (100.0, 60.0, 10.0)

HOLE_DIAMETER = 20.0
HOLE_CENTRE = (20.0, 20.0)
DRILLED_VOLUME = PLATE_VOLUME - math.pi * (HOLE_DIAMETER / 2.0) ** 2 * PLATE_SIZE[2]

THIN_SIZE = (100.0, 60.0, 3.0)


def chamfered_box_volume(size: Tuple[float, float, float], distance: float) -> float:
    """Exact volume of a box with every edge bevelled at ``distance``.

    Derived from the measured face geometry, not fitted to the kernel. The
    solid is the box minus twelve wedges, one per edge, each of triangular
    cross-section ``d^2/2``; the wedges overlap near every vertex, and OCC also
    closes each vertex with a flat triangle on the plane ``x+y+z = 2d`` (in
    corner-local coordinates), which removes a further tetrahedron.

    Inclusion-exclusion over one corner, with ``a, b, c`` the box dimensions:

    * twelve wedges                      ``2 d^2 (a + b + c)``
    * minus three pairwise overlaps each  ``d^3 / 3``  (8 corners)
    * plus the triple overlap             ``d^3 / 4``  (8 corners)
    * plus the corner tetrahedron         ``d^3 / 12`` (8 corners)

    which collapses to ``2 d^2 (a+b+c) - 8 d^3 + 2 d^3 + (2/3) d^3``.
    """
    a, b, c = size
    removed = (
        2.0 * distance**2 * (a + b + c) - (16.0 / 3.0) * distance**3
    )
    return a * b * c - removed


def document(
    features: List[Dict[str, Any]], name: str = "chamfered-plate"
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


def chamfer_feature(
    distance: float = CHAMFER_DISTANCE,
    select: str = "axis_parallel",
    axis: str = "Z",
    target: str = "plate",
) -> Dict[str, Any]:
    edges: Dict[str, Any] = {"select": select}
    if select == "axis_parallel":
        edges["axis"] = axis
    return {
        "id": "bevel",
        "type": "chamfer",
        "target": target,
        "distance": distance,
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


class ChamferTestCase(unittest.TestCase):
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
        """The primary case: 100x60x10 plate, d=2 on the vertical edges."""
        return self.build_document(document([plate_feature(), chamfer_feature()]))

    def plain_plate(
        self, size: Tuple[float, float, float] = PLATE_SIZE
    ) -> LocalCadResult:
        return self.build_document(document([plate_feature(size)], "plain"))

    def drilled(self) -> LocalCadResult:
        return self.build_document(
            document([plate_feature(), hole_feature()], "drilled")
        )

    def surface_census(self, shape: Any) -> Dict[str, int]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        counts: Dict[str, int] = {}
        for face in shape.Faces():
            name = str(BRepAdaptor_Surface(face.wrapped).GetType()).rsplit(".", 1)[-1]
            counts[name] = counts.get(name, 0) + 1
        return counts

    def planar_faces(self, shape: Any) -> List[Any]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_SurfaceType

        return [
            face
            for face in shape.Faces()
            if BRepAdaptor_Surface(face.wrapped).GetType()
            == GeomAbs_SurfaceType.GeomAbs_Plane
        ]

    def plane_normal(self, face: Any) -> Tuple[float, float, float]:
        from OCP.BRepAdaptor import BRepAdaptor_Surface

        direction = BRepAdaptor_Surface(face.wrapped).Plane().Axis().Direction()
        return (direction.X(), direction.Y(), direction.Z())

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


# --- 1: primary box chamfer ------------------------------------------------


class TestPrimaryChamfer(ChamferTestCase):
    """plate 100x60x10, distance 2, edges = axis_parallel Z."""

    def test_the_selector_matches_the_four_vertical_edges(self) -> None:
        selected = select_edges(
            self.plain_plate().shape, EdgeSelector(select="axis_parallel", axis="Z")
        )
        self.assertEqual(len(selected), 4)

    def test_build_succeeds(self) -> None:
        self.assertIsInstance(self.build(), LocalCadResult)

    def test_result_is_a_single_valid_solid(self) -> None:
        result = self.build()
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertTrue(result.is_solid())
        self.assertTrue(result.shape.isValid())
        self.assertEqual(result.solid_count(), 1)

    def test_volume_matches_the_analytic_corner_loss(self) -> None:
        result = self.build()
        self.assertAlmostEqual(
            result.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertAlmostEqual(
            PLATE_VOLUME - result.volume(), CORNER_LOSS, delta=VOLUME_TOLERANCE_MM3
        )

    def test_volume_decreases_from_the_unbevelled_box(self) -> None:
        self.assertLess(self.build().volume(), self.plain_plate().volume())

    def test_bounding_box_extents_are_unchanged(self) -> None:
        box = self.build().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_every_face_is_planar(self) -> None:
        """A chamfer introduces flat bevels, never curved surfaces."""
        self.assertEqual(
            self.surface_census(self.build().shape), {"GeomAbs_Plane": 10}
        )

    def test_four_chamfer_planes_exist_at_45_degrees(self) -> None:
        """Real surface geometry: the bevel planes' own normals.

        A symmetric chamfer between two perpendicular faces bisects them, so
        each bevel normal is (+/-1, +/-1, 0)/sqrt(2) -- one per corner, all
        four sign combinations.
        """
        result = self.build()
        bevels = [
            self.plane_normal(face)
            for face in self.planar_faces(result.shape)
            if abs(abs(self.plane_normal(face)[0]) - DIAGONAL) < PLANE_NORMAL_TOLERANCE
        ]
        self.assertEqual(len(bevels), 4)
        signs = set()
        for normal in bevels:
            self.assertAlmostEqual(abs(normal[0]), DIAGONAL, delta=NORMAL_TOLERANCE)
            self.assertAlmostEqual(abs(normal[1]), DIAGONAL, delta=NORMAL_TOLERANCE)
            self.assertAlmostEqual(normal[2], 0.0, delta=NORMAL_TOLERANCE)
            signs.add(
                (int(math.copysign(1, normal[0])), int(math.copysign(1, normal[1])))
            )
        self.assertEqual(signs, {(1, 1), (1, -1), (-1, 1), (-1, -1)})

    def test_the_setback_is_symmetric_and_equals_the_distance(self) -> None:
        """The whole point of C.6, proved from the bevel faces' vertices.

        Each bevel is a rectangle whose two long edges sit exactly ``d`` from
        the original corner along each of the two axes -- equal setback on
        both adjoining faces.
        """
        result = self.build()
        bevels = [
            face
            for face in self.planar_faces(result.shape)
            if abs(abs(self.plane_normal(face)[0]) - DIAGONAL) < PLANE_NORMAL_TOLERANCE
        ]
        found = set()
        for face in bevels:
            corners = {
                (round(vertex.X, 9), round(vertex.Y, 9)) for vertex in face.Vertices()
            }
            self.assertEqual(len(corners), 2)
            found.add(tuple(sorted(corners)))
        self.assertEqual(
            found, {tuple(sorted(pair)) for pair in EXPECTED_SETBACK_CORNERS}
        )

    def test_the_bevel_face_area_matches_the_setback(self) -> None:
        """Hypotenuse d*sqrt(2) times the 10 mm thickness."""
        result = self.build()
        expected = CHAMFER_DISTANCE * math.sqrt(2.0) * PLATE_SIZE[2]
        for face in self.planar_faces(result.shape):
            normal = self.plane_normal(face)
            if abs(abs(normal[0]) - DIAGONAL) < PLANE_NORMAL_TOLERANCE:
                with self.subTest(normal=tuple(round(c, 6) for c in normal)):
                    self.assertAlmostEqual(face.Area(), expected, delta=TOLERANCE_MM)

    def test_the_sharp_corner_material_is_actually_gone(self) -> None:
        from OCP.TopAbs import TopAbs_State

        plain = self.plain_plate().shape
        bevelled = self.build().shape
        for corner in (
            (0.1, 0.1, 5.0),
            (99.9, 0.1, 5.0),
            (0.1, 59.9, 5.0),
            (99.9, 59.9, 5.0),
        ):
            with self.subTest(corner=corner):
                self.assertEqual(self.classify(plain, corner), TopAbs_State.TopAbs_IN)
                self.assertEqual(
                    self.classify(bevelled, corner), TopAbs_State.TopAbs_OUT
                )

    def test_the_faces_themselves_are_not_moved(self) -> None:
        from OCP.TopAbs import TopAbs_State

        bevelled = self.build().shape
        for point in ((0.1, 30.0, 5.0), (50.0, 0.1, 5.0), (50.0, 30.0, 0.1)):
            with self.subTest(point=point):
                self.assertEqual(
                    self.classify(bevelled, point), TopAbs_State.TopAbs_IN
                )

    def test_topology_is_a_measured_kernel_observation(self) -> None:
        result = self.build()
        self.assertEqual(len(result.shape.Faces()), 10)
        self.assertEqual(len(result.shape.Edges()), 24)
        self.assertEqual(len(result.shape.Vertices()), 16)

    def test_the_kernel_confirms_the_contours_are_symmetric(self) -> None:
        """``IsSymetric`` and ``GetDist`` read straight off the builder."""
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.plain_plate().shape
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(CHAMFER_DISTANCE, edge.wrapped)
        self.assertEqual(builder.NbContours(), 4)
        for contour in range(1, builder.NbContours() + 1):
            with self.subTest(contour=contour):
                self.assertTrue(builder.IsSymetric(contour))
                self.assertAlmostEqual(
                    builder.GetDist(contour)[0],
                    CHAMFER_DISTANCE,
                    delta=SETBACK_TOLERANCE_MM,
                )

    def test_a_larger_admissible_distance_also_works(self) -> None:
        """Measured bracket for this geometry, not a rule: 29 works, 30 fails."""
        result = self.build_document(
            document([plate_feature(), chamfer_feature(distance=29.0)], "wide")
        )
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(
            result.volume(),
            PLATE_VOLUME - 4.0 * (29.0**2 / 2.0) * PLATE_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )


# --- 2: all edges ----------------------------------------------------------


class TestAllEdgesChamfer(ChamferTestCase):
    def all_edges(self, distance: float = CHAMFER_DISTANCE) -> LocalCadResult:
        return self.build_document(
            document(
                [plate_feature(), chamfer_feature(distance=distance, select="all")],
                "bevelled-box",
            )
        )

    def test_the_selector_matches_all_twelve_edges(self) -> None:
        self.assertEqual(
            len(select_edges(self.plain_plate().shape, EdgeSelector(select="all"))), 12
        )

    def test_an_admissible_distance_succeeds(self) -> None:
        result = self.all_edges()
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")

    def test_every_edge_and_corner_was_handled(self) -> None:
        """6 original + 12 bevels + 8 corner triangles = 26 planar faces."""
        result = self.all_edges()
        self.assertEqual(self.surface_census(result.shape), {"GeomAbs_Plane": 26})
        self.assertEqual(len(result.shape.Faces()), 26)
        self.assertEqual(len(result.shape.Edges()), 48)
        self.assertEqual(len(result.shape.Vertices()), 24)

    def test_no_selected_edge_was_silently_skipped(self) -> None:
        """Coverage from the builder's own contour tables."""
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.plain_plate().shape
        edges = select_edges(shape, EdgeSelector(select="all"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(CHAMFER_DISTANCE, edge.wrapped)
        taken = [
            builder.Edge(contour, position)
            for contour in range(1, builder.NbContours() + 1)
            for position in range(1, builder.NbEdges(contour) + 1)
        ]
        self.assertEqual(len(taken), len(edges))
        for edge in edges:
            self.assertTrue(any(edge.wrapped.IsSame(other) for other in taken))

    def test_the_corner_triangles_lie_on_the_expected_plane(self) -> None:
        """Each vertex is closed by a plane normal to (1,1,1)/sqrt(3).

        Measured, and the fact the volume closed form is derived from.
        """
        result = self.all_edges()
        corner_normal = 1.0 / math.sqrt(3.0)
        triangles = [
            face
            for face in self.planar_faces(result.shape)
            if all(
                abs(abs(component) - corner_normal) < PLANE_NORMAL_TOLERANCE
                for component in self.plane_normal(face)
            )
        ]
        self.assertEqual(len(triangles), 8)
        origin_corner = [
            face
            for face in triangles
            if all(
                vertex.X < 5.0 and vertex.Y < 5.0 and vertex.Z < 5.0
                for vertex in face.Vertices()
            )
        ]
        self.assertEqual(len(origin_corner), 1)
        corners = {
            (round(v.X, 9), round(v.Y, 9), round(v.Z, 9))
            for v in origin_corner[0].Vertices()
        }
        d = CHAMFER_DISTANCE
        self.assertEqual(corners, {(0.0, d, d), (d, 0.0, d), (d, d, 0.0)})

    def test_volume_matches_the_derived_closed_form(self) -> None:
        for distance in (0.5, 1.0, 2.0, 3.0, 4.0):
            with self.subTest(distance=distance):
                result = self.all_edges(distance=distance)
                self.assertAlmostEqual(
                    result.volume(),
                    chamfered_box_volume(PLATE_SIZE, distance),
                    delta=VOLUME_TOLERANCE_MM3,
                )
                self.assertLess(result.volume(), PLATE_VOLUME)

    def test_all_eight_corners_are_gone(self) -> None:
        from OCP.TopAbs import TopAbs_State

        bevelled = self.all_edges().shape
        for x in (0.1, 99.9):
            for y in (0.1, 59.9):
                for z in (0.1, 9.9):
                    with self.subTest(corner=(x, y, z)):
                        self.assertEqual(
                            self.classify(bevelled, (x, y, z)), TopAbs_State.TopAbs_OUT
                        )

    def test_bounding_box_extents_are_unchanged(self) -> None:
        box = self.all_edges().bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)


# --- 3: rule E4 ------------------------------------------------------------


class TestRuleE4(ChamferTestCase):
    """A selector that matches nothing, without inventing an edge.

    A ``+Z`` cylinder has no straight edge parallel to X or Y, measured in
    Stage 12.
    """

    def empty_document(self, axis: str = "X") -> Dict[str, Any]:
        return document(
            [
                cylinder_feature(),
                chamfer_feature(distance=1.0, axis=axis, target="pin"),
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
                self.assertIn("'bevel'", message)
                self.assertIn("'pin'", message)

    def test_e4_does_not_claim_another_rule(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.empty_document())
        message = str(caught.exception)
        for other in ("E5", "E3", "E2", "E1"):
            with self.subTest(other=other):
                self.assertNotIn(other, message)

    def test_no_geometry_escapes_an_e4_failure(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(self.empty_document())


# --- 4 & 5: rule E5 --------------------------------------------------------


class TestRuleE5(ChamferTestCase):
    """Admissibility, from measured kernel behaviour.

    Chamfer's failure modes are **not** the same as fillet's, which is why
    they were measured rather than assumed:

    * an over-large distance gives ``IsDone() == False`` -- and, unlike the
      fillet builder, never a "done but invalid" result at any distance tried;
    * an edge the kernel considers unsuitable is *silently ignored* by
      ``Add``, so coverage has to be checked explicitly;
    * a selection made entirely of unsuitable edges makes ``Build()`` raise
      ``Standard_Failure`` with "There are no suitable edges for chamfer or
      fillet".
    """

    def test_a_clearly_admissible_distance_succeeds(self) -> None:
        result = self.build_document(
            document([plate_feature(), chamfer_feature(distance=0.5)], "small")
        )
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(
            result.volume(),
            PLATE_VOLUME - 4.0 * (0.5**2 / 2.0) * PLATE_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_a_clearly_too_large_distance_is_rejected(self) -> None:
        for distance in (30.0, 31.0, 60.0):
            with self.subTest(distance=distance):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(
                        document(
                            [plate_feature(), chamfer_feature(distance=distance)],
                            "huge",
                        )
                    )
                message = str(caught.exception)
                self.assertIn("E5", message)
                self.assertIn("not admissible", message)

    def test_an_over_large_all_edges_distance_is_rejected(self) -> None:
        """Half the plate thickness: measured to fail."""
        for distance in (5.0, 6.0):
            with self.subTest(distance=distance):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(
                        document(
                            [
                                plate_feature(),
                                chamfer_feature(distance=distance, select="all"),
                            ],
                            "huge-all",
                        )
                    )
                self.assertIn("E5", str(caught.exception))

    def test_the_over_large_case_is_a_not_done_kernel_state(self) -> None:
        """Recorded difference from fillet: no done-but-invalid regime here.

        The fillet builder returns a valid-looking but broken solid above a
        threshold. Every over-large chamfer distance tried reports
        ``IsDone() == False`` instead.
        """
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.plain_plate().shape
        edges = select_edges(shape, EdgeSelector(select="all"))
        for distance in (5.0, 5.01, 6.0, 8.0):
            builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
            for edge in edges:
                builder.Add(distance, edge.wrapped)
            builder.Build()
            with self.subTest(distance=distance):
                self.assertFalse(builder.IsDone())

    def test_a_non_positive_distance_never_reaches_the_kernel(self) -> None:
        for distance in (0, -1, -0.5):
            with self.subTest(distance=distance):
                result = validate(
                    document(
                        [plate_feature(), chamfer_feature(distance=distance)], "bad"
                    )
                )
                self.assertFalse(result.valid)
                self.assertIn("S17", result.rule_codes())

        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Chamfer(
                    id="bevel",
                    target="plate",
                    distance=0.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        self.assertIn("S17", str(caught.exception))

    def test_no_malformed_geometry_escapes(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document([plate_feature(), chamfer_feature(distance=30.0)], "huge")
            )


# --- 6: the mixed-edge case ------------------------------------------------


class TestMixedEdges(ChamferTestCase):
    """One admissible subset and one inadmissible, in a single selector.

    On a 100x60x3 plate at distance 2 the vertical edges are admissible and
    the horizontal ones are not -- each established separately, so the
    combined case is a genuine mix rather than an assumed one.
    """

    def thin(self, **overrides: Any) -> Dict[str, Any]:
        return document(
            [plate_feature(THIN_SIZE), chamfer_feature(**overrides)], "thin"
        )

    def test_the_admissible_subset_succeeds_alone(self) -> None:
        result = self.build_document(self.thin(distance=2.0, axis="Z"))
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(
            result.volume(),
            THIN_SIZE[0] * THIN_SIZE[1] * THIN_SIZE[2]
            - 4.0 * (2.0**2 / 2.0) * THIN_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_the_inadmissible_subset_fails_alone(self) -> None:
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(self.thin(distance=2.0, axis=axis))
                self.assertIn("E5", str(caught.exception))

    def test_the_combined_selection_fails_as_a_whole(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(self.thin(distance=2.0, select="all"))
        self.assertIn("E5", str(caught.exception))

    def test_no_subset_succeeds_and_no_partial_geometry_escapes(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(self.thin(distance=2.0, select="all"))
        # The plain thin plate still builds, unbevelled.
        plain = self.plain_plate(THIN_SIZE)
        self.assertEqual(self.surface_census(plain.shape), {"GeomAbs_Plane": 6})
        self.assertAlmostEqual(
            plain.volume(),
            THIN_SIZE[0] * THIN_SIZE[1] * THIN_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_a_smaller_distance_makes_the_same_selection_admissible(self) -> None:
        result = self.build_document(self.thin(distance=1.0, select="all"))
        self.assertTrue(result.is_solid())
        self.assertAlmostEqual(
            result.volume(),
            chamfered_box_volume(THIN_SIZE, 1.0),
            delta=VOLUME_TOLERANCE_MM3,
        )


# --- 7: the cylinder seam --------------------------------------------------


class TestCylinderSeam(ChamferTestCase):
    """The Stage 12 seam finding, carried into a chamfer.

    Measured outcome: the kernel will not bevel a parameterisation seam at
    all. ``Add`` accepts the edge and then quietly builds no contour for it,
    so the coverage check is what catches it -- and it is reported as E5,
    because no distance makes that edge bevellable.
    """

    def pin(self) -> LocalCadResult:
        return self.build_document(document([cylinder_feature()], "pin"))

    def test_the_selector_still_matches_only_the_seam(self) -> None:
        from cad_core.edge_selection import edge_curve_type

        selected = select_edges(
            self.pin().shape, EdgeSelector(select="axis_parallel", axis="Z")
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(edge_curve_type(selected[0]), "GeomAbs_Line")

    def test_the_kernel_builds_no_contour_for_the_seam(self) -> None:
        """The measurement behind the rule, taken first-hand."""
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.pin().shape
        seam = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        builder.Add(1.0, seam[0].wrapped)
        self.assertEqual(builder.NbContours(), 0)

    def test_the_kernel_message_names_the_reason(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.pin().shape
        seam = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        builder.Add(1.0, seam[0].wrapped)
        with self.assertRaises(Exception) as caught:
            builder.Build()
        self.assertEqual(type(caught.exception).__name__, "Standard_Failure")
        self.assertIn("no suitable edges", str(caught.exception))

    def test_chamfering_the_seam_is_refused(self) -> None:
        for distance in (0.5, 2.0):
            with self.subTest(distance=distance):
                with self.assertRaises(GeometryOperationError) as caught:
                    self.build_document(
                        document(
                            [
                                cylinder_feature(),
                                chamfer_feature(
                                    distance=distance, axis="Z", target="pin"
                                ),
                            ],
                            "seam",
                        )
                    )
                message = str(caught.exception)
                self.assertIn("E5", message)
                self.assertIn("unsuitable", message)

    def test_select_all_on_a_cylinder_is_also_refused(self) -> None:
        """Because the seam is in that selection and would be dropped.

        The kernel alone would succeed here -- it bevels the two rims into
        conical faces and ignores the seam. The engine refuses because
        ignoring a matched edge is exactly what it must not do silently. This
        is a deliberate consequence of that rule, recorded rather than
        smoothed over.
        """
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document(
                    [
                        cylinder_feature(),
                        chamfer_feature(distance=1.0, select="all", target="pin"),
                    ],
                    "rounded-pin",
                )
            )
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("1 of the 3", message)

    def test_the_kernel_would_have_succeeded_on_its_own(self) -> None:
        """Recorded so the cost of the strict rule is visible, not hidden."""
        import cadquery as cq
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.pin().shape
        edges = select_edges(shape, EdgeSelector(select="all"))
        self.assertEqual(len(edges), 3)
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(1.0, edge.wrapped)
        self.assertEqual(builder.NbContours(), 2)  # the seam contributed none
        builder.Build()
        self.assertTrue(builder.IsDone())
        result = cq.Shape.cast(builder.Shape())
        self.assertEqual(len(result.Solids()), 1)
        self.assertTrue(result.isValid())
        self.assertEqual(self.surface_census(result), {
            "GeomAbs_Cylinder": 1,
            "GeomAbs_Cone": 2,
            "GeomAbs_Plane": 2,
        })


# --- 8: the drilled plate --------------------------------------------------


class TestDrilledPlate(ChamferTestCase):
    """100x60x10 plate with one Ø20 through-hole.

    The cavity's parameterisation seam is matched by ``axis_parallel Z`` and
    by ``all``, and the kernel will not bevel it -- so both of those
    selections are refused, while ``axis_parallel X`` and ``Y`` work. The
    selector output is never filtered by hand.
    """

    def test_the_selector_matches_four_corners_and_the_cavity_seam(self) -> None:
        self.assertEqual(
            len(
                select_edges(
                    self.drilled().shape,
                    EdgeSelector(select="axis_parallel", axis="Z"),
                )
            ),
            5,
        )

    def test_the_seam_is_the_edge_the_kernel_will_not_bevel(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.drilled().shape
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(2.0, edge.wrapped)
        taken = [
            builder.Edge(contour, position)
            for contour in range(1, builder.NbContours() + 1)
            for position in range(1, builder.NbEdges(contour) + 1)
        ]
        self.assertEqual(len(taken), 4)
        dropped = [
            edge
            for edge in edges
            if not any(edge.wrapped.IsSame(other) for other in taken)
        ]
        self.assertEqual(len(dropped), 1)
        # ... and it is the one on the hole wall.
        self.assertAlmostEqual(
            math.hypot(
                dropped[0].startPoint().x - HOLE_CENTRE[0],
                dropped[0].startPoint().y - HOLE_CENTRE[1],
            ),
            HOLE_DIAMETER / 2.0,
            delta=TOLERANCE_MM,
        )

    def test_the_vertical_selection_is_refused(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document(
                    [plate_feature(), hole_feature(), chamfer_feature()],
                    "drilled-bevel",
                )
            )
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("1 of the 5", message)

    def test_select_all_is_refused_for_the_same_reason(self) -> None:
        with self.assertRaises(GeometryOperationError) as caught:
            self.build_document(
                document(
                    [
                        plate_feature(),
                        hole_feature(),
                        chamfer_feature(distance=1.0, select="all"),
                    ],
                    "drilled-all",
                )
            )
        message = str(caught.exception)
        self.assertIn("E5", message)
        self.assertIn("1 of the 15", message)

    def test_the_horizontal_selections_succeed(self) -> None:
        """No seam is parallel to X or Y, so coverage is complete."""
        for axis, length in (("X", PLATE_SIZE[0]), ("Y", PLATE_SIZE[1])):
            with self.subTest(axis=axis):
                result = self.build_document(
                    document(
                        [
                            plate_feature(),
                            hole_feature(),
                            chamfer_feature(distance=2.0, axis=axis),
                        ],
                        f"drilled-{axis}",
                    )
                )
                self.assertTrue(result.is_solid())
                self.assertEqual(result.solid_count(), 1)
                self.assertEqual(result.feature_id, "plate")
                self.assertAlmostEqual(
                    result.volume(),
                    DRILLED_VOLUME - 4.0 * (2.0**2 / 2.0) * length,
                    delta=VOLUME_TOLERANCE_MM3,
                )

    def test_the_hole_survives_a_horizontal_chamfer(self) -> None:
        from OCP.TopAbs import TopAbs_State

        result = self.build_document(
            document(
                [plate_feature(), hole_feature(), chamfer_feature(axis="X")],
                "drilled-x",
            )
        )
        self.assertEqual(
            self.surface_census(result.shape),
            {"GeomAbs_Plane": 10, "GeomAbs_Cylinder": 1},
        )
        for z in (0.1, 5.0, 9.9):
            with self.subTest(z=z):
                self.assertEqual(
                    self.classify(result.shape, (HOLE_CENTRE[0], HOLE_CENTRE[1], z)),
                    TopAbs_State.TopAbs_OUT,
                )

    def test_the_selector_output_was_not_filtered(self) -> None:
        """The engine passes exactly what the selector returned."""
        shape = self.drilled().shape
        for selector, expected in (
            (EdgeSelector(select="axis_parallel", axis="Z"), 5),
            (EdgeSelector(select="all"), 15),
            (EdgeSelector(select="axis_parallel", axis="X"), 4),
        ):
            with self.subTest(selector=selector):
                self.assertEqual(len(select_edges(shape, selector)), expected)


# --- 9: target replacement and history ------------------------------------


class TestTargetReplacement(ChamferTestCase):
    def test_target_identity_survives(self) -> None:
        self.assertEqual(self.build().feature_id, "plate")

    def test_the_modifier_id_never_names_a_solid(self) -> None:
        self.assertNotEqual(self.build().feature_id, "bevel")

    def test_the_targets_position_in_the_set_survives(self) -> None:
        """A tool created before the chamfer must still be consumable.

        If the chamfer appended a new entry instead of replacing the target,
        the surviving solid's id would change and rule S9 would trip.
        """
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
                    chamfer_feature(axis="X"),
                ],
                "cut-then-bevel",
            )
        )
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)

    def test_an_unresolvable_target_is_reported(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="hand-built",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Chamfer(
                    id="bevel",
                    target="absent",
                    distance=2.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S6", message)
        self.assertIn("'absent'", message)

    def test_a_chamfer_cannot_target_a_consumed_tool(self) -> None:
        doc = document(
            [
                plate_feature(),
                cylinder_feature(
                    "tool", diameter=20.0, height=20.0, position=(20.0, 20.0, -5.0)
                ),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
                chamfer_feature(target="tool"),
            ],
            "consumed",
        )
        self.assertIn("S6", validate(doc).rule_codes())


class TestFeatureHistory(ChamferTestCase):
    """Reuses the existing ordered evaluator; no second history engine."""

    def test_box_then_chamfer(self) -> None:
        result = self.build()
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)

    def test_box_then_through_hole_then_chamfer(self) -> None:
        result = self.build_document(
            document(
                [plate_feature(), hole_feature(), chamfer_feature(axis="X")],
                "hole-then-bevel",
            )
        )
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertAlmostEqual(
            result.volume(),
            DRILLED_VOLUME - 4.0 * (CHAMFER_DISTANCE**2 / 2.0) * PLATE_SIZE[0],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_box_then_subtract_then_chamfer(self) -> None:
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
                    chamfer_feature(axis="X"),
                ],
                "subtract-then-bevel",
            )
        )
        self.assertEqual(result.feature_id, "plate")
        self.assertAlmostEqual(
            result.volume(),
            DRILLED_VOLUME - 4.0 * (CHAMFER_DISTANCE**2 / 2.0) * PLATE_SIZE[0],
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_two_chamfers_in_sequence(self) -> None:
        """Each replaces the target, so the second sees the first."""
        doc = document([plate_feature(), chamfer_feature()], "twice")
        doc["features"].append(
            {
                "id": "bevel2",
                "type": "chamfer",
                "target": "plate",
                "distance": 1.0,
                "edges": {"select": "axis_parallel", "axis": "X"},
            }
        )
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertLess(result.volume(), EXPECTED_Z_VOLUME)

    def test_a_fillet_then_a_chamfer(self) -> None:
        """Both modifiers in one history, on disjoint selections."""
        doc = document(
            [
                plate_feature(),
                {
                    "id": "round",
                    "type": "fillet",
                    "target": "plate",
                    "radius": 2.0,
                    "edges": {"select": "axis_parallel", "axis": "Z"},
                },
                chamfer_feature(distance=1.0, axis="X"),
            ],
            "round-then-bevel",
        )
        result = self.build_document(doc)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(result.solid_count(), 1)
        self.assertTrue(result.is_solid())


# --- 10: failure atomicity -------------------------------------------------


class TestFailureAtomicity(ChamferTestCase):
    """Established for chamfer, not inherited from fillet."""

    def test_the_source_shape_is_unchanged_by_a_not_done_build(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.plain_plate().shape
        before = self.fingerprint(shape)
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(30.0, edge.wrapped)
        builder.Build()
        self.assertFalse(builder.IsDone())
        # The engine never asks a not-done builder for its shape.
        self.assertEqual(before, self.fingerprint(shape))

    def test_the_source_shape_is_unchanged_by_a_raising_build(self) -> None:
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.build_document(document([cylinder_feature()], "pin")).shape
        before = self.fingerprint(shape)
        seam = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        builder.Add(1.0, seam[0].wrapped)
        with self.assertRaises(Exception):
            builder.Build()
        self.assertEqual(before, self.fingerprint(shape))

    def test_the_source_shape_is_unchanged_by_a_successful_build(self) -> None:
        import cadquery as cq
        from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer

        shape = self.plain_plate().shape
        before = self.fingerprint(shape)
        edges = select_edges(shape, EdgeSelector(select="axis_parallel", axis="Z"))
        builder = BRepFilletAPI_MakeChamfer(shape.wrapped)
        for edge in edges:
            builder.Add(CHAMFER_DISTANCE, edge.wrapped)
        builder.Build()
        self.assertTrue(builder.IsDone())
        cq.Shape.cast(builder.Shape())
        self.assertEqual(before, self.fingerprint(shape))

    def test_the_engine_still_works_after_a_failed_chamfer(self) -> None:
        """Kernel state stays healthy across interleaved failures.

        The fillet builder was measured to corrupt kernel state when a
        not-done builder is asked for its shape. That hazard was **not**
        reproduced for the chamfer builder, but the same discipline applies
        and this guards it either way.
        """
        for _ in range(3):
            with self.assertRaises(GeometryOperationError):
                self.build_document(
                    document(
                        [plate_feature(), chamfer_feature(distance=30.0)], "huge"
                    )
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
                    [plate_feature(THIN_SIZE), chamfer_feature(select="all")], "mixed"
                )
            )
        self.assertEqual(reference, self.fingerprint(self.plain_plate().shape))

    def test_a_failed_chamfer_after_a_hole_leaves_the_hole_intact(self) -> None:
        with self.assertRaises(GeometryOperationError):
            self.build_document(
                document(
                    [
                        plate_feature(),
                        hole_feature(),
                        chamfer_feature(distance=40.0, axis="X"),
                    ],
                    "bad-bevel",
                )
            )
        drilled = self.drilled()
        self.assertAlmostEqual(
            drilled.volume(), DRILLED_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(len(drilled.shape.Faces()), 7)


# --- 12: STEP --------------------------------------------------------------


class TestChamferStep(ChamferTestCase):
    def test_step_round_trip(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "c.step"))
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.solid_count(), 1)
        self.assertAlmostEqual(
            imported.volume(), EXPECTED_Z_VOLUME, delta=VOLUME_TOLERANCE_MM3
        )
        box = imported.bounding_box()
        self.assertTripleAlmostEqual(box.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(box.maximum, EXPECTED_MAXIMUM)

    def test_step_preserves_the_bevel_faces(self) -> None:
        imported = read_step(export_step(self.build(), self.tmp / "c.step"))
        self.assertEqual(self.surface_census(imported.shape), {"GeomAbs_Plane": 10})
        self.assertEqual(len(imported.shape.Faces()), 10)
        self.assertEqual(len(imported.shape.Edges()), 24)
        self.assertEqual(len(imported.shape.Vertices()), 16)
        bevels = [
            self.plane_normal(face)
            for face in self.planar_faces(imported.shape)
            if abs(abs(self.plane_normal(face)[0]) - DIAGONAL) < PLANE_NORMAL_TOLERANCE
        ]
        self.assertEqual(len(bevels), 4)

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
        # Measured: STEP bytes differ per export.
        self.assertEqual(len(digests), 3)


# --- IGES ------------------------------------------------------------------


class TestChamferIges(ChamferTestCase):
    def test_iges_round_trip(self) -> None:
        imported = read_iges(export_iges(self.build(), self.tmp / "c.igs"))
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
        imported = read_iges(export_iges(self.build(), self.tmp / "c.igs"))
        self.assertEqual(imported.face_count(), 10)
        self.assertEqual(imported.edge_count(), 24)
        self.assertEqual(imported.vertex_count(), 16)
        self.assertEqual(self.surface_census(imported.shape), {"GeomAbs_Plane": 10})

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


class TestChamferStl(ChamferTestCase):
    def test_stl_is_structurally_valid(self) -> None:
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "c.stl"))
        self.assertTrue(facts.is_structurally_consistent)
        self.assertFalse(facts.looks_ascii)
        self.assertGreater(facts.declared_triangles, 0)

    def test_the_bevels_add_triangles(self) -> None:
        plain = read_stl(export_stl(self.plain_plate(), self.tmp / "plain.stl"))
        bevelled = read_stl(export_stl(self.build(), self.tmp / "c.stl"))
        self.assertEqual(plain.triangle_count(), 12)
        self.assertEqual(bevelled.triangle_count(), 28)
        self.assertGreater(bevelled.triangle_count(), plain.triangle_count())

    def test_the_mesh_is_small_because_everything_is_planar(self) -> None:
        """Recorded contrast with the fillet's 524 triangles.

        A chamfer needs no tessellation of curvature, so the whole solid is
        28 triangles -- 10 planar faces, each two triangles, plus the extra
        needed for the non-rectangular ones.
        """
        facts = binary_stl_facts(export_stl(self.build(), self.tmp / "c.stl"))
        self.assertEqual(facts.declared_triangles, 28)
        self.assertEqual(facts.file_size, 84 + 50 * 28)

    def test_mesh_bounds_approximate_the_brep(self) -> None:
        mesh = read_stl(export_stl(self.build(), self.tmp / "c.stl"))
        box = mesh.bounding_box()
        self.assertTripleAlmostEqual(
            box.minimum, EXPECTED_MINIMUM, delta=MESH_TOLERANCE_MM
        )
        self.assertTripleAlmostEqual(
            box.maximum, EXPECTED_MAXIMUM, delta=MESH_TOLERANCE_MM
        )

    def test_the_bevel_geometry_is_present_in_the_mesh(self) -> None:
        """Nodes exactly at the setback positions, and none beyond them."""
        mesh = read_stl(export_stl(self.build(), self.tmp / "c.stl"))
        nodes = mesh.nodes()
        for pair in EXPECTED_SETBACK_CORNERS:
            for x, y in pair:
                matches = [
                    node
                    for node in nodes
                    if abs(node[0] - x) <= MESH_TOLERANCE_MM
                    and abs(node[1] - y) <= MESH_TOLERANCE_MM
                ]
                with self.subTest(point=(x, y)):
                    self.assertGreaterEqual(len(matches), 2)  # bottom and top

        # No node survives at an original sharp corner.
        for corner in ((0.0, 0.0), (0.0, 60.0), (100.0, 0.0), (100.0, 60.0)):
            for node in nodes:
                with self.subTest(corner=corner):
                    self.assertFalse(
                        abs(node[0] - corner[0]) <= MESH_TOLERANCE_MM
                        and abs(node[1] - corner[1]) <= MESH_TOLERANCE_MM
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


class TestChamferRenderModel(ChamferTestCase):
    def model(self):
        return build_render_model(self.build())

    def test_render_model_is_produced(self) -> None:
        model = self.model()
        self.assertEqual(model.feature_id, "plate")
        self.assertEqual(model.triangle_count(), 28)
        self.assertGreater(model.vertex_count(), 0)

    def test_normals_are_finite_and_unit_length(self) -> None:
        for index, normal in enumerate(self.model().normals):
            with self.subTest(normal=index):
                for component in normal:
                    self.assertTrue(math.isfinite(component))
                length = math.sqrt(sum(component**2 for component in normal))
                self.assertAlmostEqual(length, 1.0, delta=NORMAL_TOLERANCE)

    def test_exactly_ten_distinct_normals_appear(self) -> None:
        """Six original face normals plus four bevel normals.

        A chamfered box is entirely planar, so every vertex normal must be
        one of exactly ten directions -- there is nothing curved to smooth
        across.
        """
        model = self.model()
        distinct = {tuple(round(c, 6) for c in normal) for normal in model.normals}
        self.assertEqual(len(distinct), 10)

    def test_the_bevel_normals_bisect_the_faces_they_join(self) -> None:
        """Measured from the rendered normals, not from a canned formula."""
        model = self.model()
        bevel_normals = {
            tuple(round(c, 6) for c in normal)
            for normal in model.normals
            if abs(abs(normal[0]) - DIAGONAL) < PLANE_NORMAL_TOLERANCE
        }
        self.assertEqual(len(bevel_normals), 4)
        for normal in bevel_normals:
            self.assertAlmostEqual(abs(normal[0]), DIAGONAL, delta=1e-5)
            self.assertAlmostEqual(abs(normal[1]), DIAGONAL, delta=1e-5)
            self.assertAlmostEqual(normal[2], 0.0, delta=1e-5)

    def test_the_bevel_normals_match_the_kernel_face_normals(self) -> None:
        """Cross-check: rendered normals against the B-rep's own planes.

        Compared **up to sign**: a plane's own axis direction follows the
        surface's parameterisation, which is opposite to the outward direction
        for a ``TopAbs_REVERSED`` face, while a render normal always points out
        of the material. The axis the plane lies on is the shared fact.
        """
        result = self.build()
        model = build_render_model(result)
        kernel = {
            tuple(round(abs(c), 6) for c in self.plane_normal(face))
            for face in self.planar_faces(result.shape)
        }
        rendered = {
            tuple(round(abs(c), 6) for c in normal) for normal in model.normals
        }
        self.assertEqual(rendered, kernel)

    def test_the_outer_face_normals_are_exact(self) -> None:
        model = self.model()
        rendered = {tuple(round(c, 6) for c in normal) for normal in model.normals}
        for expected in (
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
            (1.0, -0.0, 0.0),
            (-1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (-0.0, -1.0, 0.0),
        ):
            with self.subTest(expected=expected):
                self.assertTrue(
                    any(
                        all(
                            abs(a - b) < PLANE_NORMAL_TOLERANCE
                            for a, b in zip(candidate, expected)
                        )
                        for candidate in rendered
                    )
                )

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

    def test_render_model_serializes_and_is_deterministic(self) -> None:
        payloads = {
            json.dumps(build_render_model(self.build()).to_dict(), sort_keys=True)
            for _ in range(3)
        }
        self.assertEqual(len(payloads), 1)
        self.assertEqual(json.loads(next(iter(payloads)))["feature_id"], "plate")


# --- 11: determinism -------------------------------------------------------


class TestDeterminism(ChamferTestCase):
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


# --- 13 & 14: input boundary and remaining scope ---------------------------


class TestInputBoundary(ChamferTestCase):
    def test_a_raw_dictionary_cannot_invoke_the_build_api(self) -> None:
        doc = document([plate_feature(), chamfer_feature()])
        with self.assertRaises(TypeError):
            build_part(doc)  # type: ignore[arg-type]

    def test_the_validator_rejects_a_malformed_selector(self) -> None:
        for selector in (
            {"select": "axis_parallel"},
            {"select": "all", "axis": "X"},
            {"select": "axis_parallel", "axis": "+Z"},
            {"select": "sideways"},
        ):
            doc = document(
                [
                    plate_feature(),
                    {
                        "id": "bevel",
                        "type": "chamfer",
                        "target": "plate",
                        "distance": 2,
                        "edges": selector,
                    },
                ]
            )
            with self.subTest(selector=selector):
                self.assertIn("S18", validate(doc).rule_codes())

    def test_unsupported_units_are_still_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="in",
            name="p",
            features=(
                Box(id="plate", size=Size(*PLATE_SIZE)),
                Chamfer(
                    id="bevel",
                    target="plate",
                    distance=2.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError):
            build_part(part)

    def test_a_history_starting_with_a_modifier_is_still_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Chamfer(
                    id="bevel",
                    target="plate",
                    distance=2.0,
                    edges=EdgeSelector(select="all"),
                ),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("constructive", str(caught.exception))

    def test_every_v1_feature_type_is_now_built(self) -> None:
        """The whole V1 feature set evaluates: nothing is left unimplemented.

        One document exercising all six types, ending in a single solid.
        """
        hole = hole_feature()
        # Somewhere the subtract has not already removed material.
        hole["position"] = {"x": 80, "y": 40, "z": 0}
        hole["diameter"] = 10
        doc = document(
            [
                plate_feature(),
                cylinder_feature(
                    "tool", diameter=20.0, height=20.0, position=(20.0, 20.0, -5.0)
                ),
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
                hole,
                # Chamfer before fillet: measured to matter. The reverse order
                # fails E5 on this body, which is recorded in
                # docs/local-cad-engine.md rather than worked around silently.
                chamfer_feature(distance=1.0, axis="X"),
                {
                    "id": "round",
                    "type": "fillet",
                    "target": "plate",
                    "radius": 1.0,
                    "edges": {"select": "axis_parallel", "axis": "Y"},
                },
            ],
            "all-six",
        )
        result = self.build_document(doc)
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")
        self.assertEqual(
            {feature.TYPE for feature in self.part_from(doc).features},
            {"box", "cylinder", "subtract", "through_hole", "fillet", "chamfer"},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
