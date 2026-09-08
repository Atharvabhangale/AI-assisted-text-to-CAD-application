"""Unit tests for the deterministic edge-selection layer (Section C.7).

This layer answers "which edges does this selector name?" and nothing else, so
nothing here asserts that geometry changed -- several tests assert the
opposite. Edge counts are measured from the kernel first and then pinned as
backend observations; curve types are read through the same kernel API the
selector uses, never inferred from coordinates.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any, Dict, List, Tuple

from cad_core import validate
from cad_core.edge_selection import (
    ANGULAR_TOLERANCE_RAD,
    EdgeSelectionError,
    UnsupportedSelectorError,
    edge_curve_type,
    is_straight_edge,
    line_direction,
    select_edges,
)
from cad_core.local_cad import build_part
from cad_core.model import EdgeSelector, Part

# --- explicit tolerances ---------------------------------------------------

#: Kernel-derived lengths, in millimetres, matching the local CAD engine.
TOLERANCE_MM = 1e-6

#: Volume, in cubic millimetres.
VOLUME_TOLERANCE_MM3 = 1e-6

#: Direction components, dimensionless. Every axis-parallel direction in these
#: geometries was measured to come back exact, so this is headroom.
DIRECTION_TOLERANCE = 1e-9

ALL = EdgeSelector(select="all")
AXIS = {axis: EdgeSelector(select="axis_parallel", axis=axis) for axis in "XYZ"}

PLATE_SIZE = (100.0, 60.0, 10.0)


def document(features: List[Dict[str, Any]], name: str = "p") -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": name,
        "features": features,
    }


def plate_feature() -> Dict[str, Any]:
    return {
        "id": "plate",
        "type": "box",
        "size": {"x": PLATE_SIZE[0], "y": PLATE_SIZE[1], "z": PLATE_SIZE[2]},
        "position": {"x": 0, "y": 0, "z": 0},
    }


def hole_feature(
    identifier: str, x: float, y: float, diameter: float = 20.0
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "type": "through_hole",
        "target": "plate",
        "diameter": diameter,
        "position": {"x": x, "y": y, "z": 0},
        "axis": "+Z",
    }


def cylinder_feature(
    identifier: str,
    diameter: float = 20.0,
    height: float = 50.0,
    position: tuple = (10.0, 20.0, 30.0),
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


class EdgeSelectionTestCase(unittest.TestCase):
    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def shape_for(self, features: List[Dict[str, Any]], name: str = "p") -> Any:
        return build_part(self.part_from(document(features, name))).shape

    def box(self) -> Any:
        return self.shape_for([plate_feature()], "box")

    def cylinder(self, **overrides: Any) -> Any:
        return self.shape_for([cylinder_feature("pin", **overrides)], "cylinder")

    def drilled(self) -> Any:
        return self.shape_for(
            [plate_feature(), hole_feature("bore", 20.0, 20.0)], "drilled"
        )

    def two_hole(self) -> Any:
        return self.shape_for(
            [
                plate_feature(),
                hole_feature("b1", 20.0, 20.0),
                hole_feature("b2", 80.0, 40.0, diameter=10.0),
            ],
            "two-hole",
        )

    def two_tool_subtract(self) -> Any:
        return self.shape_for(
            [
                plate_feature(),
                cylinder_feature(
                    "toolA", diameter=20.0, height=20.0, position=(20.0, 20.0, -5.0)
                ),
                cylinder_feature(
                    "toolB", diameter=10.0, height=20.0, position=(80.0, 40.0, -5.0)
                ),
                {
                    "id": "cut",
                    "type": "subtract",
                    "target": "plate",
                    "tools": ["toolA", "toolB"],
                },
            ],
            "two-tool",
        )

    def curve_types(self, shape: Any) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for edge in shape.Edges():
            name = edge_curve_type(edge)
            counts[name] = counts.get(name, 0) + 1
        return counts

    def selector_counts(self, shape: Any) -> Dict[str, int]:
        return {axis: len(select_edges(shape, AXIS[axis])) for axis in "XYZ"}

    def edge_signature(self, shape: Any) -> Tuple:
        """A stable description of a shape's edge list, order included.

        Uses the kernel's curve type plus endpoint coordinates and length. Not
        an identity mechanism -- identity is tested with the kernel's own
        ``IsSame`` and shape map elsewhere -- just a fingerprint for detecting
        reordering or mutation.
        """
        return tuple(
            (
                edge_curve_type(edge),
                tuple(round(value, 9) for value in edge.startPoint().toTuple()),
                tuple(round(value, 9) for value in edge.endPoint().toTuple()),
                round(edge.Length(), 9),
            )
            for edge in shape.Edges()
        )


# --- 1 & 2: the box --------------------------------------------------------


class TestBox(EdgeSelectionTestCase):
    """A 100x60x10 box: 12 straight edges, four along each axis."""

    def test_the_kernel_reports_twelve_line_edges(self) -> None:
        self.assertEqual(self.curve_types(self.box()), {"GeomAbs_Line": 12})

    def test_select_all_returns_every_edge(self) -> None:
        shape = self.box()
        selected = select_edges(shape, ALL)
        self.assertEqual(len(selected), 12)
        self.assertEqual(len(selected), len(shape.Edges()))

    def test_each_axis_selects_four_edges(self) -> None:
        self.assertEqual(self.selector_counts(self.box()), {"X": 4, "Y": 4, "Z": 4})

    def test_the_three_axis_selections_partition_the_edges(self) -> None:
        """Every edge belongs to exactly one axis: 4 + 4 + 4 = 12.

        Checked by topological identity, not by counting alone, so an edge
        appearing under two axes would be caught.
        """
        shape = self.box()
        seen: List[Any] = []
        for axis in "XYZ":
            for edge in select_edges(shape, AXIS[axis]):
                self.assertFalse(
                    any(edge.wrapped.IsSame(other) for other in seen),
                    msg=f"an edge matched more than one axis (at {axis})",
                )
                seen.append(edge.wrapped)
        self.assertEqual(len(seen), len(shape.Edges()))

    def test_selected_directions_really_are_axis_parallel(self) -> None:
        expectations = {"X": 0, "Y": 1, "Z": 2}
        shape = self.box()
        for axis, index in expectations.items():
            for position, edge in enumerate(select_edges(shape, AXIS[axis])):
                direction = line_direction(edge)
                with self.subTest(axis=axis, edge=position):
                    self.assertAlmostEqual(
                        abs(direction[index]), 1.0, delta=DIRECTION_TOLERANCE
                    )
                    for other in range(3):
                        if other != index:
                            self.assertAlmostEqual(
                                direction[other], 0.0, delta=DIRECTION_TOLERANCE
                            )

    def test_edge_lengths_match_the_axis_asked_for(self) -> None:
        """A further check that the right edges came back, from geometry.

        The box has distinct extents on all three axes, so length alone
        identifies the direction.
        """
        shape = self.box()
        for axis, expected in zip("XYZ", PLATE_SIZE):
            for edge in select_edges(shape, AXIS[axis]):
                with self.subTest(axis=axis):
                    self.assertAlmostEqual(
                        edge.Length(), expected, delta=TOLERANCE_MM
                    )

    def test_nothing_perpendicular_is_selected(self) -> None:
        shape = self.box()
        for axis, expected in zip("XYZ", PLATE_SIZE):
            others = [size for size in PLATE_SIZE if size != expected]
            for edge in select_edges(shape, AXIS[axis]):
                with self.subTest(axis=axis):
                    for wrong in others:
                        self.assertNotAlmostEqual(
                            edge.Length(), wrong, delta=TOLERANCE_MM
                        )


# --- 3 & 4: the cylinder ---------------------------------------------------


class TestCylinder(EdgeSelectionTestCase):
    """The critical case: a cylinder is mostly *not* straight edges.

    Measured kernel topology for a full cylinder is three edges -- two circles
    (the caps) and one straight seam along the axis. The seam is a genuine
    ``GeomAbs_Line``, so the specification's "every straight edge parallel to
    the axis" does match it. That is recorded rather than special-cased.
    """

    def test_the_kernel_reports_two_circles_and_one_line(self) -> None:
        self.assertEqual(
            self.curve_types(self.cylinder()),
            {"GeomAbs_Circle": 2, "GeomAbs_Line": 1},
        )

    def test_select_all_returns_all_three_edges(self) -> None:
        shape = self.cylinder()
        self.assertEqual(len(select_edges(shape, ALL)), 3)
        self.assertEqual(len(select_edges(shape, ALL)), len(shape.Edges()))

    def test_no_circular_edge_is_ever_axis_parallel(self) -> None:
        shape = self.cylinder()
        circles = [
            edge for edge in shape.Edges() if edge_curve_type(edge) == "GeomAbs_Circle"
        ]
        self.assertEqual(len(circles), 2)
        for axis in "XYZ":
            selected = select_edges(shape, AXIS[axis])
            for circle in circles:
                with self.subTest(axis=axis):
                    self.assertFalse(
                        any(circle.wrapped.IsSame(e.wrapped) for e in selected)
                    )

    def test_the_axis_selectors_find_only_the_seam(self) -> None:
        """A +Z cylinder: X and Y match nothing, Z matches the seam."""
        self.assertEqual(
            self.selector_counts(self.cylinder()), {"X": 0, "Y": 0, "Z": 1}
        )

    def test_the_seam_runs_along_the_cylinder_axis(self) -> None:
        shape = self.cylinder()
        (seam,) = select_edges(shape, AXIS["Z"])
        self.assertTrue(is_straight_edge(seam))
        self.assertAlmostEqual(seam.Length(), 50.0, delta=TOLERANCE_MM)
        self.assertTripleParallelToZ(line_direction(seam))

    def assertTripleParallelToZ(self, direction: tuple) -> None:
        self.assertAlmostEqual(abs(direction[2]), 1.0, delta=DIRECTION_TOLERANCE)
        self.assertAlmostEqual(direction[0], 0.0, delta=DIRECTION_TOLERANCE)
        self.assertAlmostEqual(direction[1], 0.0, delta=DIRECTION_TOLERANCE)

    def test_the_seam_follows_whichever_axis_the_cylinder_uses(self) -> None:
        expectations = {
            "+X": {"X": 1, "Y": 0, "Z": 0},
            "-X": {"X": 1, "Y": 0, "Z": 0},
            "+Y": {"X": 0, "Y": 1, "Z": 0},
            "-Y": {"X": 0, "Y": 1, "Z": 0},
            "+Z": {"X": 0, "Y": 0, "Z": 1},
            "-Z": {"X": 0, "Y": 0, "Z": 1},
        }
        for axis, expected in expectations.items():
            with self.subTest(axis=axis):
                self.assertEqual(
                    self.selector_counts(self.cylinder(axis=axis)), expected
                )

    def test_the_signed_cylinder_axis_does_not_leak_into_the_selector(self) -> None:
        """A -Z cylinder is selected by "Z", not by any signed form."""
        shape = self.cylinder(axis="-Z")
        self.assertEqual(len(select_edges(shape, AXIS["Z"])), 1)
        with self.assertRaises(UnsupportedSelectorError):
            select_edges(shape, EdgeSelector(select="axis_parallel", axis="-Z"))


# --- 5 & 6: the drilled plate ----------------------------------------------


class TestDrilledPlate(EdgeSelectionTestCase):
    """Stage 10's plate with one 20 mm through-hole."""

    def test_the_kernel_reports_thirteen_lines_and_two_circles(self) -> None:
        self.assertEqual(
            self.curve_types(self.drilled()),
            {"GeomAbs_Line": 13, "GeomAbs_Circle": 2},
        )

    def test_select_all_returns_all_fifteen_edges(self) -> None:
        shape = self.drilled()
        self.assertEqual(len(select_edges(shape, ALL)), 15)
        self.assertEqual(len(select_edges(shape, ALL)), len(shape.Edges()))

    def test_axis_selector_counts(self) -> None:
        """X and Y are the four outer edges each; Z is four corners plus the
        cavity's seam, which is a straight edge the specification does match.
        """
        self.assertEqual(self.selector_counts(self.drilled()), {"X": 4, "Y": 4, "Z": 5})

    def test_the_hole_rims_are_never_selected(self) -> None:
        """Exactly the specification's example: filleting the vertical corners
        must not touch the rims.
        """
        shape = self.drilled()
        rims = [
            edge for edge in shape.Edges() if edge_curve_type(edge) == "GeomAbs_Circle"
        ]
        self.assertEqual(len(rims), 2)
        for axis in "XYZ":
            selected = select_edges(shape, AXIS[axis])
            for rim in rims:
                with self.subTest(axis=axis):
                    self.assertFalse(
                        any(rim.wrapped.IsSame(e.wrapped) for e in selected)
                    )

    def test_the_outer_edges_are_classified_by_direction(self) -> None:
        """Four outer edges per axis, identified by position not by length.

        Length alone would not do it for Z: the cavity seam is also 10 mm
        long, so outer edges are picked out by being away from the hole axis.
        """
        shape = self.drilled()
        for axis, expected_length in zip("XYZ", PLATE_SIZE):
            outer = [
                edge
                for edge in select_edges(shape, AXIS[axis])
                if math.hypot(
                    edge.startPoint().x - 20.0, edge.startPoint().y - 20.0
                )
                > 11.0
            ]
            with self.subTest(axis=axis):
                self.assertEqual(len(outer), 4)
                for edge in outer:
                    self.assertAlmostEqual(
                        edge.Length(), expected_length, delta=TOLERANCE_MM
                    )

    def test_the_extra_z_edge_is_the_cavity_seam(self) -> None:
        """Measured, and documented as a finding for the fillet stage.

        The fifth Z edge is 10 mm long and sits on the hole wall at radius 10
        from the hole axis -- it is the cylindrical face's parameterisation
        seam, not an outer corner.
        """
        shape = self.drilled()
        selected = select_edges(shape, AXIS["Z"])
        self.assertEqual(len(selected), 5)
        on_wall = [
            edge
            for edge in selected
            if abs(
                math.hypot(
                    edge.startPoint().x - 20.0, edge.startPoint().y - 20.0
                )
                - 10.0
            )
            <= TOLERANCE_MM
        ]
        self.assertEqual(len(on_wall), 1)
        self.assertAlmostEqual(on_wall[0].Length(), PLATE_SIZE[2], delta=TOLERANCE_MM)
        self.assertTrue(is_straight_edge(on_wall[0]))


# --- 7: several holes ------------------------------------------------------


class TestMultipleCavities(EdgeSelectionTestCase):
    def test_two_holes_report_four_circles(self) -> None:
        self.assertEqual(
            self.curve_types(self.two_hole()),
            {"GeomAbs_Line": 14, "GeomAbs_Circle": 4},
        )

    def test_select_all_covers_both_cavities(self) -> None:
        shape = self.two_hole()
        self.assertEqual(len(select_edges(shape, ALL)), 18)
        self.assertEqual(len(select_edges(shape, ALL)), len(shape.Edges()))

    def test_axis_selector_counts(self) -> None:
        """Z gains one seam per cavity: four corners plus two seams."""
        self.assertEqual(
            self.selector_counts(self.two_hole()), {"X": 4, "Y": 4, "Z": 6}
        )

    def test_all_four_rims_are_excluded(self) -> None:
        shape = self.two_hole()
        rims = [
            edge for edge in shape.Edges() if edge_curve_type(edge) == "GeomAbs_Circle"
        ]
        self.assertEqual(len(rims), 4)
        selected = [
            edge for axis in "XYZ" for edge in select_edges(shape, AXIS[axis])
        ]
        for index, rim in enumerate(rims):
            with self.subTest(rim=index):
                self.assertFalse(
                    any(rim.wrapped.IsSame(e.wrapped) for e in selected)
                )

    def test_both_cavity_seams_are_present_and_distinct(self) -> None:
        shape = self.two_hole()
        seams = [
            edge
            for edge in select_edges(shape, AXIS["Z"])
            if min(
                math.hypot(edge.startPoint().x - cx, edge.startPoint().y - cy)
                for cx, cy in ((20.0, 20.0), (80.0, 40.0))
            )
            < 11.0
        ]
        self.assertEqual(len(seams), 2)
        self.assertFalse(seams[0].wrapped.IsSame(seams[1].wrapped))

    def test_the_outer_edges_are_still_correctly_classified(self) -> None:
        shape = self.two_hole()
        centres = ((20.0, 20.0), (80.0, 40.0))
        for axis, expected_length in zip("XYZ", PLATE_SIZE):
            outer = [
                edge
                for edge in select_edges(shape, AXIS[axis])
                if min(
                    math.hypot(edge.startPoint().x - cx, edge.startPoint().y - cy)
                    for cx, cy in centres
                )
                > 11.0
            ]
            with self.subTest(axis=axis):
                self.assertEqual(len(outer), 4)
                for edge in outer:
                    self.assertAlmostEqual(
                        edge.Length(), expected_length, delta=TOLERANCE_MM
                    )


class TestMultiToolSubtract(EdgeSelectionTestCase):
    """Selection runs on the final B-rep, whatever produced it."""

    def test_the_subtract_route_matches_the_through_hole_route(self) -> None:
        subtracted = self.two_tool_subtract()
        drilled = self.two_hole()
        self.assertEqual(self.curve_types(subtracted), self.curve_types(drilled))
        self.assertEqual(
            self.selector_counts(subtracted), self.selector_counts(drilled)
        )
        self.assertEqual(
            len(select_edges(subtracted, ALL)), len(select_edges(drilled, ALL))
        )

    def test_axis_selector_counts(self) -> None:
        self.assertEqual(
            self.selector_counts(self.two_tool_subtract()), {"X": 4, "Y": 4, "Z": 6}
        )

    def test_both_cavities_remain_represented(self) -> None:
        shape = self.two_tool_subtract()
        circles = [
            edge for edge in shape.Edges() if edge_curve_type(edge) == "GeomAbs_Circle"
        ]
        self.assertEqual(len(circles), 4)
        radii = sorted({round(edge.Length() / (2 * math.pi), 6) for edge in circles})
        self.assertEqual(radii, [5.0, 10.0])

    def test_rims_stay_excluded_from_axis_parallel(self) -> None:
        shape = self.two_tool_subtract()
        for axis in "XYZ":
            for edge in select_edges(shape, AXIS[axis]):
                with self.subTest(axis=axis):
                    self.assertTrue(is_straight_edge(edge))


# --- 8: unsigned axis behaviour --------------------------------------------


class TestUnsignedAxis(EdgeSelectionTestCase):
    """+axis and -axis are the same thing for this selector."""

    def compound_of(self, *edges: Any) -> Any:
        import cadquery as cq

        return cq.Compound.makeCompound(list(edges))

    def test_opposite_line_directions_both_match(self) -> None:
        import cadquery as cq

        up = cq.Edge.makeLine(cq.Vector(0, 0, 0), cq.Vector(0, 0, 10))
        down = cq.Edge.makeLine(cq.Vector(5, 0, 10), cq.Vector(5, 0, 0))
        self.assertAlmostEqual(line_direction(up)[2], 1.0, delta=DIRECTION_TOLERANCE)
        self.assertAlmostEqual(line_direction(down)[2], -1.0, delta=DIRECTION_TOLERANCE)

        shape = self.compound_of(up, down)
        self.assertEqual(len(select_edges(shape, AXIS["Z"])), 2)
        self.assertEqual(len(select_edges(shape, AXIS["X"])), 0)
        self.assertEqual(len(select_edges(shape, AXIS["Y"])), 0)

    def test_a_diagonal_edge_matches_no_axis(self) -> None:
        import cadquery as cq

        diagonal = cq.Edge.makeLine(cq.Vector(0, 0, 0), cq.Vector(1, 1, 0))
        shape = self.compound_of(diagonal)
        self.assertTrue(is_straight_edge(diagonal))
        for axis in "XYZ":
            with self.subTest(axis=axis):
                self.assertEqual(len(select_edges(shape, AXIS[axis])), 0)
        self.assertEqual(len(select_edges(shape, ALL)), 1)

    def test_a_box_at_negative_coordinates_selects_the_same_counts(self) -> None:
        """Placement must not affect classification."""
        negative = self.shape_for(
            [
                {
                    "id": "plate",
                    "type": "box",
                    "size": {
                        "x": PLATE_SIZE[0],
                        "y": PLATE_SIZE[1],
                        "z": PLATE_SIZE[2],
                    },
                    "position": {"x": -200, "y": -50, "z": -7},
                }
            ],
            "negative-box",
        )
        self.assertEqual(self.selector_counts(negative), {"X": 4, "Y": 4, "Z": 4})

    def test_direction_is_independent_of_topological_orientation(self) -> None:
        """A REVERSED edge reports its line's direction, not a flipped one.

        This is why parallelism has to be tested unsigned even for a plain
        box: the kernel does not encode the traversal sense in the geometry.
        """
        from OCP.TopAbs import TopAbs_Orientation

        shape = self.box()
        reversed_lines = [
            edge
            for edge in shape.Edges()
            if edge.wrapped.Orientation() == TopAbs_Orientation.TopAbs_REVERSED
        ]
        self.assertGreater(len(reversed_lines), 0)
        for edge in reversed_lines:
            direction = line_direction(edge)
            with self.subTest(length=round(edge.Length(), 3)):
                self.assertAlmostEqual(
                    max(abs(component) for component in direction),
                    1.0,
                    delta=DIRECTION_TOLERANCE,
                )


# --- 9 & 10: curved edges and empty results --------------------------------


class TestCurvedEdgesAndEmptyResults(EdgeSelectionTestCase):
    def test_curve_type_is_read_from_the_kernel(self) -> None:
        import cadquery as cq

        circle = cq.Edge.makeCircle(5.0)
        self.assertEqual(edge_curve_type(circle), "GeomAbs_Circle")
        self.assertFalse(is_straight_edge(circle))

    def test_a_curved_edge_has_no_line_direction(self) -> None:
        import cadquery as cq

        with self.assertRaises(EdgeSelectionError) as caught:
            line_direction(cq.Edge.makeCircle(5.0))
        self.assertIn("GeomAbs_Circle", str(caught.exception))

    def test_an_empty_match_is_an_empty_tuple_not_an_error(self) -> None:
        """E4 belongs to fillet/chamfer, not to this layer."""
        shape = self.cylinder()
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                selected = select_edges(shape, AXIS[axis])
                self.assertEqual(selected, ())
                self.assertIsInstance(selected, tuple)

    def test_an_empty_match_still_exposes_a_count_for_a_future_e4_check(self) -> None:
        self.assertEqual(len(select_edges(self.cylinder(), AXIS["X"])), 0)
        self.assertEqual(len(select_edges(self.cylinder(), AXIS["Z"])), 1)

    def test_select_all_is_never_empty_for_a_solid(self) -> None:
        for name, shape in (
            ("box", self.box()),
            ("cylinder", self.cylinder()),
            ("drilled", self.drilled()),
            ("two-hole", self.two_hole()),
        ):
            with self.subTest(shape=name):
                self.assertGreater(len(select_edges(shape, ALL)), 0)


# --- 11 & 15: invalid input ------------------------------------------------


class TestInputRejection(EdgeSelectionTestCase):
    def test_a_raw_dictionary_is_not_the_primary_api(self) -> None:
        shape = self.box()
        for raw in (
            {"select": "all"},
            {"select": "axis_parallel", "axis": "X"},
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(TypeError) as caught:
                    select_edges(shape, raw)  # type: ignore[arg-type]
                self.assertIn("EdgeSelector", str(caught.exception))

    def test_a_non_shape_is_rejected(self) -> None:
        for candidate in (None, "solid", 42, {"shape": "box"}, [1, 2, 3]):
            with self.subTest(candidate=type(candidate).__name__):
                with self.assertRaises(TypeError):
                    select_edges(candidate, ALL)  # type: ignore[arg-type]

    def test_a_local_cad_result_is_not_accepted_in_place_of_a_shape(self) -> None:
        """The layer takes a kernel shape, so the mistake is reported."""
        result = build_part(self.part_from(document([plate_feature()])))
        with self.assertRaises(TypeError) as caught:
            select_edges(result, ALL)  # type: ignore[arg-type]
        self.assertIn("LocalCadResult", str(caught.exception))
        # ... and the shape it carries is what works.
        self.assertEqual(len(select_edges(result.shape, ALL)), 12)

    def test_an_unknown_select_value_is_rejected(self) -> None:
        shape = self.box()
        for value in ("face", "vertical", "ALL", "", "axis parallel"):
            with self.subTest(select=value):
                with self.assertRaises(UnsupportedSelectorError):
                    select_edges(shape, EdgeSelector(select=value))

    def test_an_unknown_axis_is_rejected(self) -> None:
        shape = self.box()
        for value in ("W", "x", "XY", "", "0"):
            with self.subTest(axis=value):
                with self.assertRaises(UnsupportedSelectorError):
                    select_edges(
                        shape, EdgeSelector(select="axis_parallel", axis=value)
                    )

    def test_the_signed_axis_forms_are_rejected(self) -> None:
        """C.7's letters are deliberately different from C.2's signed forms."""
        shape = self.box()
        for value in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=value):
                with self.assertRaises(UnsupportedSelectorError) as caught:
                    select_edges(
                        shape, EdgeSelector(select="axis_parallel", axis=value)
                    )
                self.assertIn("unsigned", str(caught.exception))

    def test_a_missing_axis_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedSelectorError) as caught:
            select_edges(self.box(), EdgeSelector(select="axis_parallel"))
        self.assertIn("S18", str(caught.exception))

    def test_an_axis_on_select_all_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedSelectorError) as caught:
            select_edges(self.box(), EdgeSelector(select="all", axis="X"))
        self.assertIn("S18", str(caught.exception))

    def test_the_validator_rejects_the_same_selectors(self) -> None:
        """The static rule and the evaluator agree, without duplication.

        S18 is the validator's job; the checks above exist for hand-built
        input. These documents can never produce a Part at all.
        """
        for selector, expected in (
            ({"select": "axis_parallel"}, "S18"),
            ({"select": "all", "axis": "X"}, "S18"),
            ({"select": "axis_parallel", "axis": "+X"}, "S18"),
            ({"select": "sideways"}, "S18"),
        ):
            doc = document(
                [
                    plate_feature(),
                    {
                        "id": "round",
                        "type": "fillet",
                        "target": "plate",
                        "radius": 2,
                        "edges": selector,
                    },
                ]
            )
            with self.subTest(selector=selector):
                self.assertIn(expected, validate(doc).rule_codes())

    def test_a_non_edge_is_rejected_by_the_helpers(self) -> None:
        shape = self.box()
        for helper in (edge_curve_type, is_straight_edge, line_direction):
            with self.subTest(helper=helper.__name__):
                with self.assertRaises(TypeError):
                    helper(shape)


# --- 12: determinism -------------------------------------------------------


class TestDeterminism(EdgeSelectionTestCase):
    def geometries(self):
        return (
            ("box", self.box()),
            ("cylinder", self.cylinder()),
            ("drilled", self.drilled()),
            ("two-hole", self.two_hole()),
            ("two-tool", self.two_tool_subtract()),
        )

    def test_repeated_selection_on_one_shape_is_identical(self) -> None:
        for name, shape in self.geometries():
            for selector_name, selector in [("all", ALL)] + [
                (axis, AXIS[axis]) for axis in "XYZ"
            ]:
                signatures = set()
                for _ in range(5):
                    signatures.add(
                        tuple(
                            (
                                edge_curve_type(edge),
                                tuple(
                                    round(value, 9)
                                    for value in edge.startPoint().toTuple()
                                ),
                                round(edge.Length(), 9),
                            )
                            for edge in select_edges(shape, selector)
                        )
                    )
                with self.subTest(shape=name, selector=selector_name):
                    self.assertEqual(len(signatures), 1)

    def test_repeated_builds_produce_the_same_edge_order(self) -> None:
        """Measured: the kernel's Edges() order survives a rebuild."""
        signatures = {
            self.edge_signature(self.two_hole()) for _ in range(4)
        }
        self.assertEqual(len(signatures), 1)

    def test_selection_order_follows_the_shapes_own_edge_order(self) -> None:
        """No re-sorting: the result is a filtered view of ``Edges()``.

        Asserted by topological identity, position by position.
        """
        for name, shape in self.geometries():
            edges = shape.Edges()
            with self.subTest(shape=name):
                self.assertTrue(
                    all(
                        selected.wrapped.IsSame(original.wrapped)
                        for selected, original in zip(select_edges(shape, ALL), edges)
                    )
                )
                for axis in "XYZ":
                    selected = select_edges(shape, AXIS[axis])
                    positions = [
                        next(
                            index
                            for index, original in enumerate(edges)
                            if original.wrapped.IsSame(edge.wrapped)
                        )
                        for edge in selected
                    ]
                    self.assertEqual(positions, sorted(positions))

    def test_the_selection_is_json_serializable_as_a_measurement(self) -> None:
        """A stable fingerprint, so a reordering regression is visible."""
        shape = self.two_hole()
        payload = json.dumps(
            [
                [
                    edge_curve_type(edge),
                    [round(value, 9) for value in edge.startPoint().toTuple()],
                    round(edge.Length(), 9),
                ]
                for edge in select_edges(shape, AXIS["Z"])
            ]
        )
        self.assertEqual(payload, json.dumps(json.loads(payload)))
        self.assertEqual(len(json.loads(payload)), 6)


# --- 13 & 14: safety and identity ------------------------------------------


class TestNoMutation(EdgeSelectionTestCase):
    def test_selection_does_not_change_the_shape(self) -> None:
        shape = self.two_hole()
        before = (
            self.edge_signature(shape),
            round(shape.Volume(), 9),
            len(shape.Faces()),
            len(shape.Edges()),
            len(shape.Vertices()),
            shape.ShapeType(),
            shape.isValid(),
        )
        for selector in [ALL] + [AXIS[axis] for axis in "XYZ"]:
            select_edges(shape, selector)
        after = (
            self.edge_signature(shape),
            round(shape.Volume(), 9),
            len(shape.Faces()),
            len(shape.Edges()),
            len(shape.Vertices()),
            shape.ShapeType(),
            shape.isValid(),
        )
        self.assertEqual(before, after)

    def test_the_shape_stays_a_valid_solid(self) -> None:
        shape = self.drilled()
        select_edges(shape, ALL)
        select_edges(shape, AXIS["Z"])
        self.assertEqual(shape.ShapeType(), "Solid")
        self.assertTrue(shape.isValid())
        self.assertAlmostEqual(
            shape.Volume(),
            100 * 60 * 10 - math.pi * 10**2 * 10,
            delta=VOLUME_TOLERANCE_MM3,
        )

    def test_selected_edges_belong_to_the_source_shape(self) -> None:
        """Topological containment from the kernel, not coordinate comparison.

        ``TopExp.MapShapes_s`` builds the kernel's own map of the shape's
        edges, and ``Contains`` answers by topological identity -- which is
        what is needed, because distinct edges can share vertices and
        coordinates.
        """
        from OCP.TopAbs import TopAbs_ShapeEnum
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape

        for name, shape in (
            ("box", self.box()),
            ("cylinder", self.cylinder()),
            ("drilled", self.drilled()),
            ("two-hole", self.two_hole()),
        ):
            edge_map = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(shape.wrapped, TopAbs_ShapeEnum.TopAbs_EDGE, edge_map)
            with self.subTest(shape=name):
                self.assertEqual(edge_map.Extent(), len(shape.Edges()))
                for selector in [ALL] + [AXIS[axis] for axis in "XYZ"]:
                    for edge in select_edges(shape, selector):
                        self.assertTrue(edge_map.Contains(edge.wrapped))

    def test_a_foreign_edge_is_not_contained(self) -> None:
        """Proof the containment test above can actually fail."""
        import cadquery as cq
        from OCP.TopAbs import TopAbs_ShapeEnum
        from OCP.TopExp import TopExp
        from OCP.TopTools import TopTools_IndexedMapOfShape

        shape = self.box()
        edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape.wrapped, TopAbs_ShapeEnum.TopAbs_EDGE, edge_map)
        foreign = cq.Solid.makeBox(1, 1, 1).Edges()[0]
        self.assertFalse(edge_map.Contains(foreign.wrapped))

    def test_returned_edges_are_the_shapes_own_objects(self) -> None:
        """Not copies with equal coordinates: the same topology."""
        shape = self.box()
        originals = shape.Edges()
        for edge in select_edges(shape, ALL):
            self.assertTrue(
                any(edge.wrapped.IsSame(original.wrapped) for original in originals)
            )


# --- package boundary ------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    from pathlib import Path as _Path

    ROOT = _Path(__file__).resolve().parents[1] / "src" / "cad_core"

    def imported_modules(self, filename: str) -> set:
        import ast

        tree = ast.parse((self.ROOT / filename).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_the_selector_layer_is_not_imported_upstream(self) -> None:
        """Validator, model, FeatureScript and exporters stay independent."""
        for module in (
            "model.py",
            "validator.py",
            "featurescript.py",
            "step_export.py",
            "iges_export.py",
            "stl_export.py",
            "render_model.py",
        ):
            with self.subTest(module=module):
                self.assertNotIn(
                    "cad_core.edge_selection", self.imported_modules(module)
                )

    def test_the_selector_layer_does_not_import_the_engine_or_exporters(self) -> None:
        forbidden = {
            "cad_core.local_cad",
            "cad_core.validator",
            "cad_core.featurescript",
            "cad_core.step_export",
            "cad_core.iges_export",
            "cad_core.stl_export",
            "cad_core.render_model",
        }
        self.assertEqual(
            forbidden & self.imported_modules("edge_selection.py"), set()
        )

    def test_it_is_not_re_exported_from_the_package_root(self) -> None:
        """CadQuery stays optional, as for local_cad.

        Checked from ``__init__.py``'s own imports rather than with
        ``hasattr``: importing the submodule anywhere binds it as a package
        attribute, so ``hasattr`` would say yes for the wrong reason.
        """
        import cad_core

        self.assertNotIn(
            "cad_core.edge_selection", self.imported_modules("__init__.py")
        )
        self.assertNotIn("edge_selection", self.imported_modules("__init__.py"))
        self.assertFalse(hasattr(cad_core, "select_edges"))
        self.assertNotIn("select_edges", getattr(cad_core, "__all__", ()))

    def test_the_tolerance_comes_from_the_kernel(self) -> None:
        from OCP.Precision import Precision

        self.assertEqual(ANGULAR_TOLERANCE_RAD, Precision.Angular_s())
        self.assertEqual(ANGULAR_TOLERANCE_RAD, 1e-12)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
