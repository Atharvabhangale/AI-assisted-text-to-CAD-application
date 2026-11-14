"""Tests for the geometric comparison of built solids (``shape_comparison``).

Every expectation here is closed-form arithmetic on the requested geometry,
compared with an explicit tolerance -- never kernel output copied back in.
The standing case is the transposed plate (``A6`` in the evaluation corpus):
100 x 60 x 10 against 60 x 100 x 10 has the same volume, an IoU of 3/7 when
orientation is fixed, and an IoU of 1 when it is not.
"""

from __future__ import annotations

import json
import math
import unittest
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from cad_core.isolated_execution import IsolationOutcome, compare_shapes_isolated
from cad_core.shape_comparison import (
    IDENTITY,
    ORIENTATION_FIXED,
    ORIENTATION_FREE,
    ORIENTATION_MODES,
    ORIENTATION_UPRIGHT,
    ROTATIONS,
    ComparisonRequestError,
    PairResult,
    ShapeComparison,
    ShapeFacts,
    _iou_under,
    build_document_shape,
    compare_pair,
    compare_shapes,
    results_from_payload,
    rotations_for,
    run_comparison_request,
    shape_facts,
)

#: Relative tolerance for kernel volumes against closed-form arithmetic.
VOLUME_RTOL = 1e-6

#: Absolute tolerance for IoU values against closed-form fractions.
IOU_TOL = 1e-6

#: Absolute tolerance for lengths (mm).
LENGTH_TOL = 1e-6

ALL_MODES = list(ORIENTATION_MODES)


def p(x: float, y: float, z: float) -> Dict[str, float]:
    return {"x": x, "y": y, "z": z}


def box(id: str, size: Sequence[float], at: Sequence[float] = (0, 0, 0)) -> Dict[str, Any]:
    return {"id": id, "type": "box", "size": p(*size), "position": p(*at)}


def cyl(
    id: str,
    diameter: float,
    height: float,
    at: Sequence[float] = (0, 0, 0),
    axis: str = "+Z",
    top: Optional[float] = None,
    sides: int = 0,
) -> Dict[str, Any]:
    return {
        "id": id,
        "type": "cylinder",
        "diameter": diameter,
        "height": height,
        "position": p(*at),
        "axis": axis,
        "top_diameter": diameter if top is None else top,
        "sides": sides,
    }


def subtract(id: str, target: str, tools: Sequence[str], **pattern: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {"id": id, "type": "subtract", "target": target, "tools": list(tools)}
    feature.update(pattern)
    return feature


def union(id: str, target: str, tools: Sequence[str]) -> Dict[str, Any]:
    return {"id": id, "type": "union", "target": target, "tools": list(tools)}


def fillet(id: str, target: str, radius: float, axis: Optional[str] = None) -> Dict[str, Any]:
    edges = {"select": "axis_parallel", "axis": axis} if axis else {"select": "all"}
    return {"id": id, "type": "fillet", "target": target, "radius": radius, "edges": edges}


def doc(*features: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "1.2.0",
        "units": "mm",
        "name": "part",
        "features": [dict(f) for f in features],
    }


def hole(id: str, target: str, diameter: float, x: float, y: float, depth: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """A vertical through hole as a cylinder tool and a subtract."""
    return (
        cyl(f"{id}_tool", diameter, depth + 4, (x, y, -2)),
        subtract(id, target, [f"{id}_tool"]),
    )


PLATE = doc(box("plate", (100, 60, 10)))
PLATE_TRANSPOSED = doc(box("plate", (60, 100, 10)))


def facts_of(document: Mapping[str, Any]) -> ShapeFacts:
    return shape_facts(build_document_shape(document))


def corner_part(a: float, b: float, c: float, mirror_x: bool = False) -> Dict[str, Any]:
    """A 10 mm cube with arms of distinct lengths along X, Y and Z: chiral.

    Each arm starts 1 mm inside the cube so it joins by overlap. With
    ``mirror_x`` the X arm points along -X instead: the mirror image.
    """
    arm_x = box("ax", (a, 10, 10), (1 - a, 0, 0) if mirror_x else (9, 0, 0))
    return doc(
        box("core", (10, 10, 10)),
        arm_x,
        box("ay", (10, b, 10), (0, 9, 0)),
        box("az", (10, 10, c), (0, 0, 9)),
        union("join", "core", ["ax", "ay", "az"]),
    )


class TestRotations(unittest.TestCase):
    def test_there_are_24_distinct_proper_rotations_identity_first(self) -> None:
        self.assertEqual(len(ROTATIONS), 24)
        self.assertEqual(len({r.name for r in ROTATIONS}), 24)
        self.assertEqual(ROTATIONS[0], IDENTITY)
        self.assertEqual(IDENTITY.name, "+X+Y+Z")

    def test_each_mode_searches_the_right_rotations(self) -> None:
        self.assertEqual(rotations_for(ORIENTATION_FIXED), (IDENTITY,))
        upright = rotations_for(ORIENTATION_UPRIGHT)
        self.assertEqual(len(upright), 4)
        self.assertTrue(all(r.upright for r in upright))
        self.assertEqual(len(rotations_for(ORIENTATION_FREE)), 24)
        with self.assertRaises(ComparisonRequestError):
            rotations_for("mirrored")

    def test_a_rotated_box_is_the_permuted_box(self) -> None:
        quarter = next(r for r in ROTATIONS if r.name == "-Y+X+Z")
        low, high = quarter.apply_box((0, 0, 0), (100, 60, 10))
        self.assertEqual(low, (-60, 0, 0))
        self.assertEqual(high, (0, 100, 10))


class TestFacts(unittest.TestCase):
    def test_a_plate(self) -> None:
        facts = facts_of(PLATE)
        self.assertEqual(facts.solid_count, 1)
        self.assertAlmostEqual(facts.volume, 60000.0, delta=60000 * VOLUME_RTOL)
        self.assertAlmostEqual(facts.area, 2 * (6000 + 1000 + 600), delta=1e-6 * 15200)
        for got, want in zip(facts.extents, (100, 60, 10)):
            self.assertAlmostEqual(got, want, delta=LENGTH_TOL)
        self.assertEqual(facts.genus, 0)
        self.assertEqual(dict(facts.face_types), {"plane": 6})
        self.assertAlmostEqual(facts.fill_ratio, 1.0, delta=1e-9)

    def test_four_through_holes_are_genus_four_and_four_bores(self) -> None:
        features = [box("plate", (100, 60, 10))]
        for index, (x, y) in enumerate(((10, 10), (90, 10), (10, 50), (90, 50))):
            features.extend(hole(f"h{index}", "plate", 8, x, y, 10))
        facts = facts_of(doc(*features))
        self.assertEqual(facts.genus, 4)
        bores = facts.bores()
        self.assertEqual(len(bores), 4)
        for bore in bores:
            self.assertAlmostEqual(bore.diameter, 8.0, delta=LENGTH_TOL)
            self.assertEqual(bore.axis, "Z")
            self.assertAlmostEqual(bore.length, 10.0, delta=LENGTH_TOL)
        self.assertAlmostEqual(
            facts.volume, 60000 - 4 * math.pi * 16 * 10, delta=60000 * VOLUME_RTOL
        )

    def test_a_tube_has_a_bore_a_boss_and_one_hole(self) -> None:
        facts = facts_of(doc(cyl("tube", 40, 50), cyl("core", 20, 54, (0, 0, -2)),
                             subtract("cut", "tube", ["core"])))
        self.assertEqual(facts.genus, 1)
        self.assertEqual([round(b.diameter, 6) for b in facts.bores()], [20.0])
        self.assertEqual([round(b.diameter, 6) for b in facts.bosses()], [40.0])
        self.assertAlmostEqual(
            facts.volume, math.pi * (400 - 100) * 50, delta=1e-6 * 47124
        )

    def test_a_blind_pocket_is_a_bore_but_not_a_hole(self) -> None:
        facts = facts_of(doc(cyl("cup", 80, 95), cyl("cavity", 72, 95, (0, 0, 5)),
                             subtract("hollow", "cup", ["cavity"])))
        self.assertEqual(facts.genus, 0)
        bore = facts.bores()[0]
        self.assertAlmostEqual(bore.diameter, 72.0, delta=LENGTH_TOL)
        self.assertAlmostEqual(bore.length, 90.0, delta=LENGTH_TOL)
        self.assertLess(facts.fill_ratio, 0.4)

    def test_a_handle_makes_a_loop(self) -> None:
        facts = facts_of(doc(
            box("body", (60, 20, 80)),
            box("top", (30, 20, 10), (58, 0, 60)),
            box("side", (10, 20, 50), (80, 0, 15)),
            box("bottom", (30, 20, 10), (58, 0, 10)),
            union("join", "body", ["top", "side", "bottom"]),
        ))
        self.assertEqual(facts.genus, 1)

    def test_a_cone_and_a_hexagonal_prism(self) -> None:
        cone = facts_of(doc(cyl("cone", 40, 30, top=0)))
        self.assertEqual(cone.genus, 0)
        self.assertEqual(cone.face_types.get("cone"), 1)
        self.assertAlmostEqual(cone.volume, math.pi * 400 * 30 / 3, delta=1e-6 * 12566)
        hexagon = facts_of(doc(cyl("nut", 20, 8, sides=6)))
        self.assertEqual(hexagon.face_types, {"plane": 8})
        self.assertAlmostEqual(
            hexagon.volume, 1.5 * math.sqrt(3) * 100 * 8, delta=1e-6 * 2078
        )

    def test_a_fillet_is_not_a_full_turn_and_an_edge_notch_is_not_a_bore(self) -> None:
        rounded = facts_of(doc(box("b", (50, 50, 20)), fillet("f", "b", 5, "Z")))
        self.assertEqual(len(rounded.cylinders), 4)
        self.assertEqual(rounded.bores(), ())
        self.assertEqual(rounded.bosses(), ())
        for surface in rounded.cylinders:
            self.assertAlmostEqual(surface.coverage_degrees, 90.0, delta=1e-6)
        notched = facts_of(doc(box("b", (50, 50, 10)), *hole("h", "b", 10, 0, 25, 10)))
        self.assertEqual(notched.genus, 0)
        self.assertEqual(notched.bores(), ())
        self.assertAlmostEqual(notched.cylinders[0].coverage_degrees, 180.0, delta=1e-6)

    def test_a_bolt_circle_pattern(self) -> None:
        facts = facts_of(doc(
            cyl("flange", 100, 10),
            cyl("bolt", 10, 14, (40, 0, -2)),
            subtract("bolts", "flange", ["bolt"], pattern_count=6, pattern_axis="Z",
                     pattern_angle=60, pattern_step=0),
        ))
        self.assertEqual(facts.genus, 6)
        self.assertEqual(len(facts.bores()), 6)
        for bore in facts.bores():
            x, y, _ = bore.location
            self.assertAlmostEqual(math.hypot(x, y), 40.0, delta=1e-6)

    def test_facts_survive_plain_data(self) -> None:
        facts = facts_of(doc(cyl("tube", 40, 50), cyl("core", 20, 54, (0, 0, -2)),
                             subtract("cut", "tube", ["core"])))
        again = ShapeFacts.from_dict(json.loads(json.dumps(facts.to_dict())))
        self.assertEqual(again, facts)


class TestComparison(unittest.TestCase):
    def compare(self, candidate: Mapping[str, Any], reference: Mapping[str, Any],
                modes: Sequence[str] = ALL_MODES) -> ShapeComparison:
        result = compare_pair(candidate, reference, modes)
        self.assertIsNone(result.failure, msg=result.failure)
        assert result.comparison is not None
        return result.comparison

    def test_a_part_matches_itself_in_every_mode(self) -> None:
        comparison = self.compare(PLATE, PLATE)
        for mode in ALL_MODES:
            self.assertAlmostEqual(comparison.iou(mode), 1.0, delta=IOU_TOL)
        self.assertAlmostEqual(comparison.volume_error, 0.0, delta=1e-9)

    def test_the_transposed_plate_a6(self) -> None:
        """Same volume; IoU 3/7 with orientation fixed, 1 without."""
        comparison = self.compare(PLATE_TRANSPOSED, PLATE)
        self.assertAlmostEqual(comparison.volume_ratio, 1.0, delta=1e-9)
        # Aligned at the minimum corner: overlap 60 x 60 x 10 = 36000; union
        # 60000 + 60000 - 36000 = 84000.
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FIXED), 3 / 7, delta=IOU_TOL)
        self.assertEqual(comparison.overlaps[ORIENTATION_FIXED].rotation, "+X+Y+Z")
        self.assertAlmostEqual(comparison.iou(ORIENTATION_UPRIGHT), 1.0, delta=IOU_TOL)
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FREE), 1.0, delta=IOU_TOL)
        self.assertAlmostEqual(comparison.extent_differences[0], -40.0, delta=LENGTH_TOL)
        self.assertAlmostEqual(comparison.extent_differences[1], 40.0, delta=LENGTH_TOL)

    def test_a_plate_standing_on_edge_needs_free_orientation(self) -> None:
        standing = doc(box("plate", (100, 10, 60)))
        comparison = self.compare(standing, PLATE)
        self.assertLess(comparison.iou(ORIENTATION_UPRIGHT), 0.5)
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FREE), 1.0, delta=IOU_TOL)

    def test_placement_is_aligned_for_iou_and_reported_separately(self) -> None:
        moved = doc(box("plate", (100, 60, 10), (10, 20, 30)))
        comparison = self.compare(moved, PLATE, [ORIENTATION_FIXED])
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FIXED), 1.0, delta=IOU_TOL)
        for got, want in zip(comparison.placement_offset, (10, 20, 30)):
            self.assertAlmostEqual(got, want, delta=LENGTH_TOL)

    def test_half_a_plate(self) -> None:
        comparison = self.compare(doc(box("plate", (50, 60, 10))), PLATE, [ORIENTATION_FIXED])
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FIXED), 0.5, delta=IOU_TOL)
        self.assertAlmostEqual(comparison.volume_ratio, 0.5, delta=1e-9)

    def test_a_missing_hole_is_a_small_volume_change_but_a_topology_change(self) -> None:
        drilled = doc(box("plate", (100, 60, 10)), *hole("h", "plate", 8, 50, 30, 10))
        comparison = self.compare(PLATE, drilled, [ORIENTATION_FIXED])
        hole_volume = math.pi * 16 * 10
        drilled_volume = 60000 - hole_volume
        self.assertAlmostEqual(
            comparison.iou(ORIENTATION_FIXED), drilled_volume / 60000, delta=IOU_TOL
        )
        # Under one percent of volume -- which is why a volume or IoU
        # threshold alone cannot see a missing hole, and genus can.
        self.assertLess(comparison.volume_error, 0.01)
        self.assertEqual(comparison.candidate.genus, 0)
        self.assertEqual(comparison.reference.genus, 1)

    def test_the_free_search_finds_the_true_best_rotation(self) -> None:
        reference_doc = corner_part(40, 25, 15)
        # The same corner part built along other axes: X->Y, Y->Z, Z->X.
        turned_doc = doc(
            box("core", (10, 10, 10)),
            box("ax", (10, 40, 10), (0, 9, 0)),
            box("ay", (10, 10, 25), (0, 0, 9)),
            box("az", (15, 10, 10), (9, 0, 0)),
            union("join", "core", ["ax", "ay", "az"]),
        )
        comparison = self.compare(turned_doc, reference_doc)
        self.assertLess(comparison.iou(ORIENTATION_FIXED), 0.9)
        self.assertAlmostEqual(comparison.iou(ORIENTATION_FREE), 1.0, delta=IOU_TOL)
        # Exhaustively: no rotation beats the reported best.
        candidate = build_document_shape(turned_doc)
        reference = build_document_shape(reference_doc)
        cf, rf = shape_facts(candidate), shape_facts(reference)
        brute = max(_iou_under(candidate, cf, reference, rf, r)[0] for r in ROTATIONS)
        self.assertAlmostEqual(brute, comparison.iou(ORIENTATION_FREE), delta=IOU_TOL)
        self.assertLess(comparison.overlaps[ORIENTATION_FREE].rotations_evaluated, 24)

    def test_a_mirror_image_is_not_a_rotation(self) -> None:
        comparison = self.compare(corner_part(40, 25, 15, mirror_x=True),
                                  corner_part(40, 25, 15))
        self.assertAlmostEqual(comparison.volume_ratio, 1.0, delta=1e-9)
        self.assertLess(comparison.iou(ORIENTATION_FREE), 0.95)

    def test_the_comparison_is_deterministic(self) -> None:
        first = self.compare(PLATE_TRANSPOSED, PLATE).to_dict()
        second = self.compare(PLATE_TRANSPOSED, PLATE).to_dict()
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_a_comparison_survives_plain_data(self) -> None:
        comparison = self.compare(PLATE_TRANSPOSED, PLATE)
        again = ShapeComparison.from_dict(json.loads(json.dumps(comparison.to_dict())))
        self.assertEqual(again, comparison)


class TestRequests(unittest.TestCase):
    def test_an_invalid_candidate_is_a_result_not_an_error(self) -> None:
        bad = doc(box("plate", (100, 60, -10)))
        result = compare_pair(bad, PLATE)
        self.assertIsNotNone(result.failure)
        assert result.failure is not None
        self.assertEqual(result.failure.side, "candidate")
        self.assertEqual(result.failure.stage, "validation")
        self.assertTrue(result.failure.rule_codes)
        self.assertIsNone(result.comparison)

    def test_an_unbuildable_candidate_is_a_geometry_failure(self) -> None:
        floating = doc(box("a", (10, 10, 10)), box("b", (10, 10, 10), (50, 0, 0)),
                       union("join", "a", ["b"]))
        result = compare_pair(floating, PLATE)
        assert result.failure is not None
        self.assertEqual((result.failure.side, result.failure.stage), ("candidate", "geometry"))

    def test_a_candidate_alone_is_measured(self) -> None:
        result = compare_pair(PLATE, None)
        self.assertIsNone(result.failure)
        self.assertIsNone(result.comparison)
        assert result.candidate is not None
        self.assertAlmostEqual(result.candidate.volume, 60000, delta=0.06)

    def test_malformed_requests_are_refused_before_building(self) -> None:
        for payload in (
            None,
            {},
            {"pairs": "nope"},
            {"pairs": [], "extra": 1},
            {"pairs": [{"reference": PLATE}]},
            {"pairs": [{"candidate": PLATE, "colour": "red"}]},
            {"pairs": [{"candidate": PLATE, "orientations": ["sideways"]}]},
            {"pairs": [{"candidate": PLATE, "reference": "a plate"}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ComparisonRequestError):
                    run_comparison_request(payload)

    def test_a_request_round_trips_as_plain_data(self) -> None:
        answer = run_comparison_request(
            {"pairs": [{"candidate": PLATE_TRANSPOSED, "reference": PLATE,
                        "orientations": ALL_MODES}]}
        )
        results = results_from_payload(json.loads(json.dumps(answer)))
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], PairResult)
        assert results[0].comparison is not None
        self.assertAlmostEqual(results[0].comparison.iou(ORIENTATION_FIXED), 3 / 7, delta=IOU_TOL)
        with self.assertRaises(ComparisonRequestError):
            results_from_payload({"version": "0.0.0", "results": []})


class TestIsolatedComparison(unittest.TestCase):
    """The same comparison, run by the isolated worker in a child process."""

    def test_the_worker_compares_in_a_child_process(self) -> None:
        outcome = compare_shapes_isolated(
            [
                {"candidate": PLATE_TRANSPOSED, "reference": PLATE,
                 "orientations": [ORIENTATION_FIXED, ORIENTATION_FREE]},
                {"candidate": doc(box("plate", (100, 60, -1))), "reference": PLATE},
                {"candidate": PLATE, "reference": None},
            ],
            timeout_seconds=120,
        )
        self.assertTrue(outcome.succeeded, msg=outcome.execution.failure)
        self.assertTrue(outcome.execution.child_launched)
        first, second, third = outcome.results
        assert first.comparison is not None
        self.assertAlmostEqual(first.comparison.iou(ORIENTATION_FIXED), 3 / 7, delta=IOU_TOL)
        self.assertAlmostEqual(first.comparison.iou(ORIENTATION_FREE), 1.0, delta=IOU_TOL)
        assert second.failure is not None
        self.assertEqual(second.failure.side, "candidate")
        self.assertIsNotNone(third.candidate)

    def test_a_malformed_request_is_a_protocol_error(self) -> None:
        outcome = compare_shapes_isolated(
            [{"candidate": PLATE, "orientations": ["sideways"]}], timeout_seconds=60
        )
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.execution.outcome, IsolationOutcome.PROTOCOL_ERROR)
        self.assertEqual(outcome.results, ())


if __name__ == "__main__":
    unittest.main()
