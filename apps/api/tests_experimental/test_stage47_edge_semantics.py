"""Stage 47: semantic edge selection, and the seam.

The failure this stage exists for: on a drilled plate, OpenCascade represents
the hole's cylindrical face with a **parameterisation seam** -- a genuine
straight edge along the hole axis, the same length and direction as an outer
corner. `axis_parallel Z` therefore matched it, no blend can take it, and the
most ordinary mechanical chain there is (drill a plate, break its corners)
could not be built.

The seam is topologically different and that is the whole fix. Most of this
module needs no kernel at all: :mod:`cad_experimental.edge_semantics` works on
plain :class:`EdgeFacts`, so every rule can be exercised on hand-written facts
and only the execution class builds solids.
"""

from __future__ import annotations

import ast
import math
import pathlib
import unittest

from cad_experimental import edge_semantics as ES
from cad_experimental.adapter import SelectorNotExpressible, plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.executor import execute_plan, plan_needs_executor
from cad_experimental.parser import PlanParseError, parse_plan
from cad_experimental.plan import EdgeSelector, SELECT_MODES, V1_SELECT_MODES
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "cad_experimental"

#: Kernel volumes are compared to closed forms with a relative tolerance,
#: never for equality.
VOLUME_TOLERANCE = 1e-6


# --- hand-written facts, so the rules can be tested without a kernel --------
#
# Taken from a real measurement of a 100 x 60 x 10 plate with a d20 hole at
# (50, 30): 15 edges, of which two are the rims and one is the seam. The
# indices are the backend's own handles and are deliberately not consecutive
# here, to prove nothing depends on them being so.

SEAM = ES.EdgeFacts(
    index=14, curve=ES.LINE, is_seam=True, direction=(0.0, 0.0, 1.0),
    midpoint=(60.0, 30.0, 5.0), length=10.0, adjacent=(ES.CYLINDER,),
)
BOTTOM_RIM = ES.EdgeFacts(
    index=13, curve=ES.CIRCLE, centre=(50.0, 30.0, 0.0),
    normal=(0.0, 0.0, 1.0), radius=10.0,
    adjacent=(ES.CYLINDER, ES.PLANE),
)
TOP_RIM = ES.EdgeFacts(
    index=9, curve=ES.CIRCLE, centre=(50.0, 30.0, 10.0),
    # Measured: the top rim's normal points the other way. A rim is found by
    # its axis being PARALLEL to the selector's, unsigned -- reading the sign
    # would have made `top` depend on which way the kernel wrote the circle.
    normal=(0.0, 0.0, -1.0), radius=10.0,
    adjacent=(ES.CYLINDER, ES.PLANE),
)
CORNERS = tuple(
    ES.EdgeFacts(index=i, curve=ES.LINE, direction=(0.0, 0.0, 1.0),
                 midpoint=(x, y, 5.0), length=10.0,
                 adjacent=(ES.PLANE,))
    for i, (x, y) in enumerate([(0.0, 0.0), (100.0, 0.0),
                                (100.0, 60.0), (0.0, 60.0)])
)
LONG_EDGES = tuple(
    ES.EdgeFacts(index=4 + i, curve=ES.LINE, direction=(1.0, 0.0, 0.0),
                 midpoint=(50.0, y, z), length=100.0, adjacent=(ES.PLANE,))
    for i, (y, z) in enumerate([(0.0, 0.0), (0.0, 10.0),
                                (60.0, 0.0), (60.0, 10.0)])
)
DRILLED_PLATE = CORNERS + LONG_EDGES + (BOTTOM_RIM, TOP_RIM, SEAM)


def selector(select, axis=None, position=None):
    return ES.SemanticSelector(select=select, axis=axis, position=position)


# --- 1. the vocabulary ------------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_four_selector_modes_and_no_more(self):
        self.assertEqual(
            SELECT_MODES, ("all", "axis_parallel", "straight", "circular")
        )

    def test_the_two_legacy_modes_are_the_ones_v1_can_carry(self):
        self.assertEqual(V1_SELECT_MODES, ("all", "axis_parallel"))

    def test_the_plan_and_the_resolver_share_one_vocabulary(self):
        """Not two spellings of four names."""
        self.assertEqual(SELECT_MODES, ES.SELECT_MODES)

    def test_a_position_is_admissible_only_where_an_axis_is(self):
        self.assertEqual(ES.POSITION_MODES, (ES.SELECT_CIRCULAR,))
        for mode in ES.POSITION_MODES:
            self.assertIn(mode, ES.AXIS_REQUIRED + ES.AXIS_OPTIONAL)

    def test_the_selector_says_whether_v1_can_carry_it(self):
        self.assertTrue(EdgeSelector("all").is_v1)
        self.assertTrue(EdgeSelector("axis_parallel", "Z").is_v1)
        self.assertFalse(EdgeSelector("straight", "Z").is_v1)
        self.assertFalse(EdgeSelector("circular", "Z").is_v1)
        self.assertFalse(EdgeSelector("circular", "Z", "top").is_v1)


# --- 2. resolution, with no kernel anywhere --------------------------------


class ResolutionTests(unittest.TestCase):
    def indices(self, select, axis=None, position=None, facts=DRILLED_PLATE):
        return ES.resolve(selector(select, axis, position), facts).indices

    def test_straight_finds_the_corners_and_not_the_seam(self):
        self.assertEqual(self.indices("straight", "Z"), (0, 3, 1, 2))
        self.assertNotIn(SEAM.index, self.indices("straight", "Z"))

    def test_axis_parallel_still_finds_the_seam(self):
        """Unchanged on purpose: a plan written before this stage means what
        it meant. The seam is reported, not silently dropped."""
        resolution = ES.resolve(selector("axis_parallel", "Z"), DRILLED_PLATE)
        self.assertEqual(resolution.code, ES.R2)
        self.assertEqual(resolution.seams, (SEAM.index,))

    def test_circular_finds_both_rims(self):
        self.assertEqual(self.indices("circular", "Z"),
                         (BOTTOM_RIM.index, TOP_RIM.index))

    def test_circular_can_never_name_a_seam(self):
        """A seam is a line and a rim is a circle. The exclusion is by curve
        type, not by a rule that could be forgotten."""
        for axis in (None, "X", "Y", "Z"):
            with self.subTest(axis=axis):
                self.assertNotIn(SEAM.index, self.indices("circular", axis))

    def test_top_and_bottom_name_different_rims(self):
        self.assertEqual(self.indices("circular", "Z", "top"), (TOP_RIM.index,))
        self.assertEqual(self.indices("circular", "Z", "bottom"),
                         (BOTTOM_RIM.index,))

    def test_position_reads_the_centre_and_not_the_normal(self):
        """The measured rims have opposite normals. Reading the sign would
        make `top` depend on which way the kernel wrote the circle."""
        self.assertEqual(TOP_RIM.normal, (0.0, 0.0, -1.0))
        self.assertEqual(BOTTOM_RIM.normal, (0.0, 0.0, 1.0))
        self.assertEqual(self.indices("circular", "Z", "top"), (TOP_RIM.index,))

    def test_an_axis_less_circular_selector_takes_every_circle(self):
        self.assertEqual(self.indices("circular"),
                         (BOTTOM_RIM.index, TOP_RIM.index))

    def test_all_reports_the_seam_rather_than_dropping_it(self):
        resolution = ES.resolve(selector("all"), DRILLED_PLATE)
        self.assertEqual(resolution.code, ES.R2)
        self.assertEqual(len(resolution.candidates), len(DRILLED_PLATE))

    def test_nothing_matched_is_r1_and_says_what_the_solid_has(self):
        resolution = ES.resolve(selector("circular", "X"), DRILLED_PLATE)
        self.assertEqual(resolution.code, ES.R1)
        self.assertIn("2 circle", resolution.message)
        self.assertIn("seam", resolution.message)

    def test_an_unknown_mode_resolves_to_nothing_rather_than_raising(self):
        resolution = ES.resolve(selector("wishful"), DRILLED_PLATE)
        self.assertEqual(resolution.code, ES.R1)
        self.assertEqual(resolution.indices, ())

    def test_resolution_never_raises_on_empty_input(self):
        for mode in SELECT_MODES:
            with self.subTest(mode=mode):
                axis = "Z" if mode in ES.AXIS_REQUIRED else None
                self.assertEqual(ES.resolve(selector(mode, axis), ()).code,
                                 ES.R1)

    def test_a_failed_resolution_still_reports_its_candidates(self):
        resolution = ES.resolve(selector("axis_parallel", "Z"), DRILLED_PLATE)
        self.assertFalse(resolution.ok)
        self.assertEqual(len(resolution.candidates), 5)
        self.assertEqual(resolution.indices, ())


class AmbiguityTests(unittest.TestCase):
    """No silent guessing when a filter cannot separate the candidates."""

    def test_a_position_that_cannot_separate_is_r3(self):
        """Two rims at one level: `top` and `bottom` name the same edges, so
        the caller believes they narrowed the selection and did not."""
        flat = (
            ES.EdgeFacts(index=1, curve=ES.CIRCLE, centre=(0.0, 0.0, 5.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=2, curve=ES.CIRCLE, centre=(20.0, 0.0, 5.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
        )
        resolution = ES.resolve(selector("circular", "Z", "top"), flat)
        self.assertEqual(resolution.code, ES.R3)
        self.assertIn("same Z level", resolution.message)
        self.assertEqual(resolution.candidates, (1, 2))

    def test_position_is_extremal_and_not_ordinal(self):
        """Two holes through one plate both have a top rim, and `top` names
        both. Narrowing to one would have to pick, and picking is what this
        layer must not do."""
        two_holes = (
            ES.EdgeFacts(index=1, curve=ES.CIRCLE, centre=(10.0, 0.0, 0.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=2, curve=ES.CIRCLE, centre=(10.0, 0.0, 10.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=3, curve=ES.CIRCLE, centre=(40.0, 0.0, 0.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=4, curve=ES.CIRCLE, centre=(40.0, 0.0, 10.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
        )
        resolution = ES.resolve(selector("circular", "Z", "top"), two_holes)
        self.assertTrue(resolution.ok)
        self.assertEqual(resolution.indices, (2, 4))

    def test_levels_are_grouped_with_a_tolerance_not_by_equality(self):
        nearly = (
            ES.EdgeFacts(index=1, curve=ES.CIRCLE, centre=(0.0, 0.0, 10.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=2, curve=ES.CIRCLE,
                         centre=(5.0, 0.0, 10.0 + ES.LEVEL_TOLERANCE / 2.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
            ES.EdgeFacts(index=3, curve=ES.CIRCLE, centre=(0.0, 0.0, 0.0),
                         normal=(0.0, 0.0, 1.0), radius=3.0),
        )
        self.assertEqual(
            ES.resolve(selector("circular", "Z", "top"), nearly).indices,
            (1, 2),
        )


class DeterminismTests(unittest.TestCase):
    """The order is geometric. The kernel's index only settles ties."""

    def test_the_order_does_not_depend_on_the_backends_enumeration(self):
        shuffled = tuple(reversed(DRILLED_PLATE))
        self.assertEqual(
            ES.resolve(selector("circular", "Z"), DRILLED_PLATE).indices,
            ES.resolve(selector("circular", "Z"), shuffled).indices,
        )

    def test_circles_order_along_the_axis_first(self):
        self.assertEqual(
            ES.resolve(selector("circular", "Z"), DRILLED_PLATE).indices,
            (BOTTOM_RIM.index, TOP_RIM.index),
        )

    def test_the_index_is_only_the_final_tie_break(self):
        same = (
            ES.EdgeFacts(index=7, curve=ES.CIRCLE, centre=(0.0, 0.0, 0.0),
                         normal=(0.0, 0.0, 1.0), radius=1.0),
            ES.EdgeFacts(index=2, curve=ES.CIRCLE, centre=(0.0, 0.0, 0.0),
                         normal=(0.0, 0.0, 1.0), radius=1.0),
        )
        self.assertEqual(ES.resolve(selector("circular", "Z"), same).indices,
                         (2, 7))

    def test_resolution_is_repeatable(self):
        first = ES.resolve(selector("straight", "Z"), DRILLED_PLATE)
        second = ES.resolve(selector("straight", "Z"), DRILLED_PLATE)
        self.assertEqual(first.indices, second.indices)


class ParallelismTests(unittest.TestCase):
    def test_parallelism_is_unsigned(self):
        self.assertTrue(ES.is_parallel((0.0, 0.0, 1.0), "Z"))
        self.assertTrue(ES.is_parallel((0.0, 0.0, -1.0), "Z"))

    def test_parallelism_uses_a_tolerance_and_never_equality(self):
        tilted = (0.0, ES.PARALLEL_TOLERANCE / 10.0, 1.0)
        self.assertTrue(ES.is_parallel(tilted, "Z"))
        self.assertFalse(ES.is_parallel((0.0, 0.1, 0.995), "Z"))

    def test_a_missing_direction_is_not_parallel_to_anything(self):
        self.assertFalse(ES.is_parallel(None, "Z"))


# --- 3. the IR: parsing and validation -------------------------------------


class SelectorParsingTests(unittest.TestCase):
    def parse(self, edges):
        payload = {"status": "generated", "summary": "s", "operations": [
            {"id": "b", "type": "box",
             "parameters": {"x": 10.0, "y": 10.0, "z": 10.0}},
            {"id": "e", "type": "fillet", "target": "b",
             "parameters": {"radius": 1.0, "edges": edges}}]}
        return parse_plan(payload).operations[1].edges

    def test_every_mode_parses(self):
        self.assertEqual(self.parse({"select": "all"}).select, "all")
        self.assertEqual(self.parse({"select": "straight", "axis": "Z"}).axis,
                         "Z")
        self.assertEqual(self.parse({"select": "circular"}).axis, None)
        self.assertEqual(
            self.parse({"select": "circular", "axis": "Z",
                        "position": "top"}).position, "top")

    def test_a_straight_selector_needs_an_axis(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "straight"})

    def test_all_may_not_carry_an_axis(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "all", "axis": "Z"})

    def test_a_position_needs_a_circular_selector(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "straight", "axis": "Z", "position": "top"})

    def test_a_position_needs_an_axis(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "circular", "position": "top"})

    def test_an_unknown_position_is_rejected(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "circular", "axis": "Z",
                        "position": "middle"})

    def test_a_signed_axis_is_still_an_error(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "circular", "axis": "+Z"})

    def test_an_unknown_key_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            self.parse({"select": "circular", "axis": "Z", "nearest": True})

    def test_a_selector_round_trips(self):
        payload = {"select": "circular", "axis": "Z", "position": "bottom"}
        self.assertEqual(self.parse(payload).to_dict(), payload)

    def test_an_absent_position_stays_absent(self):
        self.assertEqual(self.parse({"select": "circular", "axis": "Z"}).to_dict(),
                         {"select": "circular", "axis": "Z"})


class SelectorValidationTests(unittest.TestCase):
    """The validator must refuse what the parser refuses, for plans built in
    code that never went through the parser."""

    def codes(self, selector_object):
        from cad_experimental.plan import (
            BoxOperation, FilletOperation, OperationPlan, PlanStatus,
        )

        plan = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                BoxOperation(id="b", x=1.0, y=1.0, z=1.0),
                FilletOperation(id="e", target="b", radius=1.0,
                                edges=selector_object),
            ),
            summary="s",
        )
        return [p.code for p in validate_plan(plan).problems]

    def test_every_well_formed_selector_passes(self):
        for good in (
            EdgeSelector("all"),
            EdgeSelector("axis_parallel", "Z"),
            EdgeSelector("straight", "Z"),
            EdgeSelector("circular"),
            EdgeSelector("circular", "Z"),
            EdgeSelector("circular", "Z", "top"),
        ):
            with self.subTest(selector=good.to_dict()):
                self.assertEqual(self.codes(good), [])

    def test_an_unknown_mode_is_p15(self):
        self.assertIn("P15", self.codes(EdgeSelector("nearest")))

    def test_a_missing_axis_is_p16(self):
        self.assertIn("P16", self.codes(EdgeSelector("straight")))

    def test_an_axis_on_all_is_p16(self):
        self.assertIn("P16", self.codes(EdgeSelector("all", "Z")))

    def test_a_signed_axis_is_p17(self):
        self.assertIn("P17", self.codes(EdgeSelector("straight", "+Z")))

    def test_a_position_on_the_wrong_mode_is_p32(self):
        self.assertIn("P32", self.codes(EdgeSelector("straight", "Z", "top")))

    def test_a_position_without_an_axis_is_p32(self):
        self.assertIn("P32", self.codes(EdgeSelector("circular", None, "top")))

    def test_an_unknown_position_is_p32(self):
        self.assertIn("P32", self.codes(EdgeSelector("circular", "Z", "side")))


# --- 4. the boundary: what a V1 document can carry -------------------------


class DocumentBoundaryTests(unittest.TestCase):
    def plan(self, edges):
        return parse_plan({"status": "generated", "summary": "s", "operations": [
            {"id": "b", "type": "box",
             "parameters": {"x": 10.0, "y": 10.0, "z": 10.0}},
            {"id": "e", "type": "fillet", "target": "b",
             "parameters": {"radius": 1.0, "edges": edges}}]})

    def test_a_legacy_selector_still_becomes_a_v1_document(self):
        document = plan_to_document(
            self.plan({"select": "axis_parallel", "axis": "Z"}))
        self.assertEqual(document["features"][1]["edges"],
                         {"select": "axis_parallel", "axis": "Z"})

    def test_a_semantic_selector_is_refused_rather_than_approximated(self):
        """`straight Z` is `axis_parallel Z` minus the seam. Writing the
        nearest thing would be the silent substitution this project forbids."""
        with self.assertRaises(SelectorNotExpressible) as caught:
            plan_to_document(self.plan({"select": "straight", "axis": "Z"}))
        self.assertEqual(caught.exception.operation_ids, ("e",))

    def test_the_refusal_names_the_operations(self):
        with self.assertRaises(SelectorNotExpressible) as caught:
            plan_to_document(self.plan({"select": "circular", "axis": "Z"}))
        self.assertIn("'e'", str(caught.exception))

    def test_the_path_is_chosen_before_anything_is_built(self):
        self.assertFalse(plan_needs_executor(
            self.plan({"select": "axis_parallel", "axis": "Z"})))
        self.assertTrue(plan_needs_executor(
            self.plan({"select": "circular", "axis": "Z"})))


# --- 5. execution: the chains the stage exists for -------------------------


def _plate(x=100.0, y=60.0, z=10.0):
    return {"id": "plate", "type": "box",
            "parameters": {"x": x, "y": y, "z": z}}


def _bore(identifier="bore", diameter=20.0, x=50.0, y=30.0):
    return {"id": identifier, "type": "through_hole", "target": "plate",
            "parameters": {"diameter": diameter,
                           "position": {"x": x, "y": y, "z": 0.0}}}


def _fillet(edges, radius=2.0, identifier="edge"):
    return {"id": identifier, "type": "fillet", "target": "plate",
            "parameters": {"radius": radius, "edges": edges}}


def _chamfer(edges, distance=1.5, identifier="edge"):
    return {"id": identifier, "type": "chamfer", "target": "plate",
            "parameters": {"distance": distance, "edges": edges}}


def _plan(*operations):
    return parse_plan({"status": "generated", "summary": "s",
                       "operations": [dict(o) for o in operations]})


PLAIN_PLATE = 100.0 * 60.0 * 10.0
BORE = math.pi * 10.0 ** 2 * 10.0


class ExecutionTests(unittest.TestCase):
    """Real solids. Volumes against closed forms, with a tolerance."""

    def run_plan(self, *operations):
        plan = _plan(*operations)
        verdict = validate_plan(plan)
        self.assertTrue(verdict.valid, verdict.to_dict())
        return execute_plan(plan)

    def built(self, *operations):
        result = self.run_plan(*operations)
        self.assertTrue(
            result.succeeded,
            result.failure.to_dict() if result.failure else "",
        )
        return result

    def close(self, measured, expected):
        self.assertLess(abs(measured - expected) / expected, VOLUME_TOLERANCE)

    def test_1_fillet_a_hole_rim(self):
        result = self.built(_plate(), _bore(),
                            _fillet({"select": "circular", "axis": "Z"}))
        body = result.bodies[0]
        self.assertEqual(body.measurement.solid_count, 1)
        self.assertLess(body.measurement.volume, PLAIN_PLATE - BORE)
        self.assertEqual(len(result.selections["edge"].indices), 2)

    def test_2_chamfer_a_hole_rim_to_its_closed_form(self):
        """A 45-degree chamfer of distance d on a rim of radius R removes a
        conical ring; by Pappus its volume is 2*pi*(R + d/3) * d^2/2."""
        distance, radius = 1.5, 10.0
        result = self.built(_plate(), _bore(),
                            _chamfer({"select": "circular", "axis": "Z"},
                                     distance))
        ring = 2.0 * math.pi * (radius + distance / 3.0) * distance ** 2 / 2.0
        self.close(result.bodies[0].measurement.volume,
                   PLAIN_PLATE - BORE - 2.0 * ring)

    def test_2b_chamfering_one_rim_removes_exactly_one_ring(self):
        distance, radius = 1.5, 10.0
        result = self.built(
            _plate(), _bore(),
            _chamfer({"select": "circular", "axis": "Z", "position": "top"},
                     distance))
        ring = 2.0 * math.pi * (radius + distance / 3.0) * distance ** 2 / 2.0
        self.close(result.bodies[0].measurement.volume,
                   PLAIN_PLATE - BORE - ring)

    def test_3_top_and_bottom_rims_are_different_edges(self):
        top = self.built(
            _plate(), _bore(),
            _fillet({"select": "circular", "axis": "Z", "position": "top"}))
        bottom = self.built(
            _plate(), _bore(),
            _fillet({"select": "circular", "axis": "Z", "position": "bottom"}))
        self.assertNotEqual(top.selections["edge"].indices,
                            bottom.selections["edge"].indices)
        self.assertEqual(len(top.selections["edge"].indices), 1)
        # The plate is symmetric about its mid-plane, so the two removals are
        # equal. That they are equal is the check that neither picked two.
        self.close(top.bodies[0].measurement.volume,
                   bottom.bodies[0].measurement.volume)

    def test_4_the_outer_corners_stay_distinguishable_from_the_rim(self):
        corners = self.built(_plate(), _bore(),
                             _fillet({"select": "straight", "axis": "Z"}, 3.0))
        rim = self.built(_plate(), _bore(),
                         _fillet({"select": "circular", "axis": "Z"}, 2.0))
        self.assertEqual(len(corners.selections["edge"].indices), 4)
        self.assertEqual(len(rim.selections["edge"].indices), 2)
        self.assertEqual(
            set(corners.selections["edge"].indices)
            & set(rim.selections["edge"].indices),
            set(),
        )

    def test_5_the_seam_is_never_selected_by_a_rim_or_straight_selector(self):
        from cad_experimental.cadquery_backend import CadQueryBackend

        backend = CadQueryBackend()
        shape = backend.through_hole(
            backend.create_box((100.0, 60.0, 10.0)), 20.0, (50.0, 30.0, 0.0),
            "+Z")
        facts = backend.describe_edges(shape)
        seams = {fact.index for fact in facts if fact.is_seam}
        self.assertEqual(len(seams), 1, "the drilled plate has exactly one seam")
        for select, axis in (("straight", "Z"), ("circular", "Z"),
                             ("circular", None)):
            with self.subTest(select=select):
                chosen = set(ES.resolve(selector(select, axis), facts).indices)
                self.assertEqual(chosen & seams, set())

    def test_5b_the_legacy_selector_reports_the_seam_by_its_own_code(self):
        """Not a generic kernel failure: a named, actionable diagnostic."""
        result = self.run_plan(
            _plate(), _bore(),
            _fillet({"select": "axis_parallel", "axis": "Z"}, 3.0))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, ES.R2)
        self.assertEqual(result.failure.operation, "edge")
        self.assertIn("straight", result.failure.message)

    def test_6_several_holes_resolve_deterministically(self):
        result = self.built(
            _plate(), _bore("a", 10.0, 20.0, 30.0), _bore("b", 10.0, 80.0, 30.0),
            _fillet({"select": "circular", "axis": "Z", "position": "top"},
                    1.5))
        chosen = result.selections["edge"].indices
        self.assertEqual(len(chosen), 2, "one top rim per hole")
        self.assertEqual(chosen, tuple(sorted(chosen, key=lambda i: i)) or chosen)
        again = self.built(
            _plate(), _bore("a", 10.0, 20.0, 30.0), _bore("b", 10.0, 80.0, 30.0),
            _fillet({"select": "circular", "axis": "Z", "position": "top"},
                    1.5))
        self.assertEqual(chosen, again.selections["edge"].indices)

    def test_7_a_pattern_s_rims_are_selectable_downstream(self):
        """Stage 46's pattern, then Stage 47's selector on what it made."""
        result = self.built(
            _plate(100.0, 100.0, 10.0),
            _bore("mount", 8.0, 85.0, 50.0),
            {"id": "mounts", "type": "pattern", "source": "mount",
             "parameters": {"count": 4, "placement": {
                 "kind": "radial", "axis": "+Z",
                 "centre": {"x": 50.0, "y": 50.0, "z": 0.0}}}},
            _chamfer({"select": "circular", "axis": "Z", "position": "top"},
                     1.0),
        )
        self.assertEqual(len(result.selections["edge"].indices), 4)
        self.assertEqual(result.bodies[0].measurement.solid_count, 1)
        distance, radius = 1.0, 4.0
        ring = 2.0 * math.pi * (radius + distance / 3.0) * distance ** 2 / 2.0
        self.close(
            result.bodies[0].measurement.volume,
            100.0 * 100.0 * 10.0 - 4.0 * math.pi * 16.0 * 10.0 - 4.0 * ring,
        )

    def test_a_selector_matching_nothing_is_r1_and_builds_nothing(self):
        result = self.run_plan(_plate(), _bore(),
                               _fillet({"select": "circular", "axis": "X"}))
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, ES.R1)
        self.assertEqual(result.bodies, ())


class ExecutorTests(unittest.TestCase):
    """The executor itself: order, state, and refusals."""

    def test_it_walks_the_graphs_order(self):
        result = execute_plan(_plan(
            _plate(), _bore(), _fillet({"select": "circular", "axis": "Z"})))
        self.assertEqual(result.order, ("plate", "bore", "edge"))

    def test_it_reuses_the_one_history_for_consumption(self):
        result = execute_plan(_plan(
            _plate(),
            {"id": "tool", "type": "cylinder",
             "parameters": {"diameter": 10.0, "height": 40.0,
                            "position": {"x": 20.0, "y": 30.0, "z": -5.0}}},
            {"id": "cut", "type": "subtract", "target": "plate",
             "tools": ["tool"]},
            _fillet({"select": "circular", "axis": "Z"}, 1.0),
        ))
        self.assertTrue(result.succeeded, result.failure)
        self.assertEqual([body.id for body in result.bodies], ["plate"])

    def test_a_refusal_has_no_geometry(self):
        plan = parse_plan({"status": "unsupported", "summary": "no",
                           "reason": "no", "operations": []})
        result = execute_plan(plan)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, "not_generated")

    def test_an_unexecutable_operation_stops_before_any_geometry(self):
        plan = _plan(
            {"id": "profile", "type": "sketch", "parameters": {
                "plane": "XY", "geometry": [
                    {"id": "r1", "type": "rectangle",
                     "corner": {"x": 0.0, "y": 0.0},
                     "width": 10.0, "height": 10.0}]}},
            {"id": "solid", "type": "extrude", "target": "profile",
             "parameters": {"distance": 5.0}},
        )
        result = execute_plan(plan)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, "unsupported")
        self.assertIn("sketch", result.failure.message)

    def test_the_result_is_json_serialisable_and_holds_no_shape(self):
        import json

        result = execute_plan(_plan(
            _plate(), _bore(), _fillet({"select": "circular", "axis": "Z"})))
        payload = result.to_dict()
        self.assertIsInstance(json.dumps(payload), str)
        self.assertNotIn("shapes", payload)


class BuildRoutingTests(unittest.TestCase):
    """`build_plan` picks a path explicitly and says which it took."""

    @classmethod
    def setUpClass(cls):
        import tempfile

        from cad_core.application_service import CadApplicationService

        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def test_a_legacy_plan_still_goes_through_the_v1_document(self):
        result = build_plan(self.service, _plan(_plate(), _bore()))
        self.assertFalse(result.executed)
        self.assertIsNotNone(result.document)
        self.assertTrue(result.built)

    def test_a_semantic_plan_goes_through_the_executor(self):
        result = build_plan(self.service, _plan(
            _plate(), _bore(),
            _chamfer({"select": "circular", "axis": "Z"}, 1.5)))
        self.assertTrue(result.executed)
        self.assertIsNone(result.document)
        self.assertTrue(result.built)

    def test_a_failed_semantic_build_reports_the_resolution_code(self):
        result = build_plan(self.service, _plan(
            _plate(), _bore(),
            _fillet({"select": "circular", "axis": "X"})))
        self.assertTrue(result.executed)
        self.assertFalse(result.built)
        self.assertIn(ES.R1, result.error)


# --- 6. the boundaries hold ------------------------------------------------


class BackendIndependenceTests(unittest.TestCase):
    def imports_of(self, name):
        tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_the_semantics_layer_imports_nothing_at_all(self):
        """No kernel, no backend, no cad_core -- and in fact no project
        module either. It is arithmetic on plain records."""
        self.assertEqual(
            {name for name in self.imports_of("edge_semantics.py")
             if not name.startswith(("dataclasses", "typing", "__future__"))},
            set(),
        )

    def test_the_executor_imports_no_kernel(self):
        imported = self.imports_of("executor.py")
        for forbidden in ("cadquery", "OCP", "FreeCAD", "Part",
                          "cad_experimental.cadquery_backend",
                          "cad_experimental.freecad_backend"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_occ_topology_is_read_in_exactly_one_place(self):
        """`describe_edges` is the whole of it. A second reader would be a
        second answer to what a seam is."""
        source = (SOURCE / "cadquery_backend.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("IsReallyClosed_s("), 1)
        # Checked on imports, not on prose: `edge_semantics` names the
        # kernel predicate in its docstring, because recording WHY a seam is
        # a seam is the point of that docstring. What it must not do is
        # import one.
        for name in ("edge_semantics.py", "executor.py", "plan.py",
                     "validation.py", "parser.py", "adapter.py", "graph.py"):
            with self.subTest(name=name):
                imported = self.imports_of(name)
                self.assertFalse(
                    [module for module in imported
                     if module.split(".")[0] in ("OCP", "cadquery")],
                    imported,
                )

    def test_no_kernel_selector_syntax_reaches_the_ir(self):
        """CadQuery string selectors (`>Z`, `|Z`, `%CIRCLE`) must not appear
        anywhere the plan can carry."""
        for name in ("plan.py", "parser.py", "edge_semantics.py"):
            text = (SOURCE / name).read_text(encoding="utf-8")
            for syntax in ('">Z"', '"|Z"', '"%CIRCLE"', "Workplane"):
                with self.subTest(name=name, syntax=syntax):
                    self.assertNotIn(syntax, text)

    def test_the_second_backend_is_honest_about_not_implementing_this(self):
        """FreeCAD inherits `describe_edges` and therefore raises rather than
        answering wrongly. Not implementing it is a gap, not a fallback."""
        from cad_experimental import freecad_backend

        self.assertFalse("describe_edges" in vars(
            freecad_backend.FreeCadBackend))


class ExistingSelectorCompatibilityTests(unittest.TestCase):
    """Plans written before this stage must mean exactly what they meant."""

    def test_the_two_v1_selectors_are_unchanged_in_the_ir(self):
        for payload in ({"select": "all"},
                        {"select": "axis_parallel", "axis": "Z"}):
            with self.subTest(payload=payload):
                plan = parse_plan({
                    "status": "generated", "summary": "s", "operations": [
                        {"id": "b", "type": "box",
                         "parameters": {"x": 1.0, "y": 1.0, "z": 1.0}},
                        {"id": "e", "type": "fillet", "target": "b",
                         "parameters": {"radius": 0.1, "edges": payload}}]})
                self.assertEqual(plan.operations[1].edges.to_dict(), payload)
                self.assertTrue(plan.operations[1].edges.is_v1)

    def test_axis_parallel_still_includes_the_seam(self):
        """The legacy meaning is preserved, not quietly improved."""
        resolution = ES.resolve(selector("axis_parallel", "Z"), DRILLED_PLATE)
        self.assertIn(SEAM.index, resolution.seams)

    def test_a_box_fillet_works_on_both_paths(self):
        block = {"id": "plate", "type": "box",
                 "parameters": {"x": 20.0, "y": 20.0, "z": 20.0}}
        legacy = execute_plan(_plan(
            block, _fillet({"select": "axis_parallel", "axis": "Z"}, 2.0)))
        semantic = execute_plan(_plan(
            block, _fillet({"select": "straight", "axis": "Z"}, 2.0)))
        self.assertTrue(legacy.succeeded, legacy.failure)
        self.assertTrue(semantic.succeeded, semantic.failure)
        self.assertAlmostEqual(legacy.bodies[0].measurement.volume,
                               semantic.bodies[0].measurement.volume,
                               places=6)


class ThePromptTeachesTheSelectorsTests(unittest.TestCase):
    def setUp(self):
        self.text = system_prompt()

    def test_every_mode_is_documented(self):
        for mode in SELECT_MODES:
            with self.subTest(mode=mode):
                self.assertIn(f'"select": "{mode}"', self.text)

    def test_it_names_the_rim_case(self):
        self.assertIn("RIM", self.text)
        self.assertIn('"position": "top"', self.text)

    def test_it_warns_about_the_seam_without_kernel_jargon(self):
        """The model is told a seam exists and which selector avoids it. It
        is never told whose seam, or what an OCC face is."""
        section = self.text.split("The edge selector is an object", 1)[1]
        section = section.split("## chamfer", 1)[0]
        self.assertIn("SEAM", section)
        for jargon in ("OCC", "OpenCascade", "CadQuery", "BRep", "TopoDS",
                       "parameterisation"):
            with self.subTest(jargon=jargon):
                self.assertNotIn(jargon, section)

    def test_it_prefers_straight_over_the_legacy_selector(self):
        self.assertIn("prefer `straight`", self.text)

    def test_it_does_not_hard_code_a_benchmark_case(self):
        for case_text in ("Create a rectangular 100 mm by 60 mm profile",
                          "four-cylinder engine"):
            self.assertNotIn(case_text, self.text)


if __name__ == "__main__":
    unittest.main()
