"""Stage 36: chamfer -- the second edge modifier, and the cheap one.

Chamfer is structurally identical to fillet with `distance` in place of
`radius`, so these tests concentrate on what could actually differ: the field
name, the contract's own rule (S17), and that nothing about the shared
selector, reference or E4/E5 behaviour changed.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest

from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
)

from cad_experimental import local_plan_provider as lpp
from cad_experimental.adapter import plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.parser import PlanParseError, parse_plan, parse_plan_text
from cad_experimental.plan import (
    CHAMFER,
    CONSUMING_TYPES,
    EDGE_MODIFIER_LENGTH,
    EDGE_MODIFIER_TYPES,
    FILLET,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    ChamferOperation,
    EdgeSelector,
    OperationPlan,
    PlanStatus,
    is_consuming,
    is_modifier,
    operation_to_dict,
)
from cad_experimental.validation import (
    P4, P10, P11, P12, P15, P16, P17, validate_plan,
)

VOLUME_RTOL = 1e-9

#: A chamfer at distance d removes a right triangle of area d^2/2 per unit
#: of edge length. Exact in decimal, unlike a fillet's quarter-circle.
BEVEL_2MM = 2.0**2 / 2.0

_MISSING = object()


def plate(identifier="plate"):
    return {"id": identifier, "type": "box",
            "parameters": {"x": 100, "y": 60, "z": 10}}


def rod(identifier="rod"):
    return {"id": identifier, "type": "cylinder",
            "parameters": {"diameter": 20, "height": 50, "axis": "+Z"}}


def axis_parallel(axis):
    return {"select": "axis_parallel", "axis": axis}


ALL = {"select": "all"}


def chamfer(
    identifier="bevel", target="plate", distance=2, edges=_MISSING, **extra
):
    parameters = {
        "distance": distance,
        "edges": axis_parallel("Z") if edges is _MISSING else edges,
    }
    parameters.update(extra.pop("parameters", {}))
    operation = {"id": identifier, "type": "chamfer",
                 "target": target, "parameters": parameters}
    operation.update(extra)
    return operation


def fillet(identifier="round", target="plate", radius=2, edges=None):
    return {"id": identifier, "type": "fillet", "target": target,
            "parameters": {"radius": radius,
                           "edges": edges or axis_parallel("Z")}}


def generated(*operations):
    return json.dumps(
        {"status": "generated", "summary": "s", "operations": list(operations)}
    )


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_contains_chamfer(self):
        self.assertIn(CHAMFER, OPERATION_TYPES)

    def test_chamfer_is_a_modifier_that_consumes_nothing(self):
        self.assertIn(CHAMFER, MODIFIER_TYPES)
        self.assertNotIn(CHAMFER, CONSUMING_TYPES)

    def test_the_two_edge_modifiers_are_grouped(self):
        self.assertEqual(set(EDGE_MODIFIER_TYPES), {FILLET, CHAMFER})

    def test_each_edge_modifier_names_its_own_length(self):
        self.assertEqual(
            EDGE_MODIFIER_LENGTH, {FILLET: "radius", CHAMFER: "distance"}
        )

    def test_no_unimplemented_operation_crept_in(self):
        for absent in (
            "sketch", "extrude", "revolve", "sweep", "loft", "pattern",
            "mirror", "union", "assembly",
        ):
            self.assertNotIn(absent, OPERATION_TYPES)


class ParseTests(unittest.TestCase):
    def test_a_chamfer_parses(self):
        parsed = parse_plan_text(generated(plate(), chamfer()))
        operation = parsed.operations[1]
        self.assertIsInstance(operation, ChamferOperation)
        self.assertEqual(operation.distance, 2.0)
        self.assertEqual(operation.edges, EdgeSelector("axis_parallel", "Z"))

    def test_a_chamfer_round_trips(self):
        parsed = parse_plan_text(generated(plate(), chamfer()))
        payload = operation_to_dict(parsed.operations[1])
        self.assertEqual(payload["parameters"]["distance"], 2.0)
        self.assertNotIn("radius", payload["parameters"])

    def test_reparsing_a_round_trip_is_stable(self):
        first = parse_plan_text(generated(plate(), chamfer(edges=ALL)))
        again = parse_plan(json.loads(json.dumps(first.to_dict())))
        self.assertEqual(first, again)

    def test_radius_is_not_a_chamfer_parameter(self):
        """The one confusion worth blocking loudly."""
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(
                    plate(),
                    {"id": "b", "type": "chamfer", "target": "plate",
                     "parameters": {"radius": 2, "edges": axis_parallel("Z")}},
                )
            )

    def test_distance_is_not_a_fillet_parameter(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(
                    plate(),
                    {"id": "f", "type": "fillet", "target": "plate",
                     "parameters": {"distance": 2,
                                    "edges": axis_parallel("Z")}},
                )
            )

    def test_the_shared_selector_rules_still_apply(self):
        for edges in (
            "all",
            {"select": "axis_parallel", "axis": "+Z"},
            {"select": "axis_parallel"},
            {"select": "all", "axis": "Z"},
            {"select": "named"},
            {"select": "all", "edge_ids": [1]},
        ):
            with self.subTest(edges=edges):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), chamfer(edges=edges)))

    def test_a_non_numeric_distance_is_rejected(self):
        for value in ("2", None, [2], True):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), chamfer(distance=value))
                    )

    def test_missing_distance_edges_or_target_is_rejected(self):
        for operation in (
            {"id": "b", "type": "chamfer", "target": "plate",
             "parameters": {"edges": axis_parallel("Z")}},
            {"id": "b", "type": "chamfer", "target": "plate",
             "parameters": {"distance": 2}},
            {"id": "b", "type": "chamfer",
             "parameters": {"distance": 2, "edges": axis_parallel("Z")}},
        ):
            with self.subTest(operation=sorted(operation)):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), operation))


class ValidationTests(unittest.TestCase):
    def codes(self, *operations):
        verdict = validate_plan(parse_plan_text(generated(*operations)))
        return [problem.code for problem in verdict.problems]

    def test_a_valid_chamfer_passes(self):
        self.assertEqual(self.codes(plate(), chamfer()), [])

    def test_the_all_selector_passes(self):
        self.assertEqual(self.codes(plate(), chamfer(edges=ALL)), [])

    def test_a_non_positive_distance_is_P4(self):
        for distance in (0, -2, -0.5):
            with self.subTest(distance=distance):
                self.assertIn(P4, self.codes(plate(), chamfer(distance=distance)))

    def test_the_P4_path_names_distance_not_radius(self):
        verdict = validate_plan(
            parse_plan_text(generated(plate(), chamfer(distance=0)))
        )
        where = next(p.where for p in verdict.problems if p.code == P4)
        self.assertTrue(where.endswith(".distance"), where)

    def test_a_future_target_is_P10(self):
        codes = self.codes(
            plate(), chamfer(target="later"),
            {"id": "later", "type": "box",
             "parameters": {"x": 1, "y": 1, "z": 1}},
        )
        self.assertIn(P10, codes)

    def test_a_modifier_target_is_P11(self):
        """Including a fillet's id -- both edge modifiers are modifiers."""
        self.assertIn(
            P11, self.codes(plate(), fillet(), chamfer(target="round"))
        )

    def test_a_chamfers_own_id_never_names_a_solid(self):
        self.assertIn(
            P11,
            self.codes(plate(), chamfer("first"), chamfer("second", target="first")),
        )

    def test_a_consumed_target_is_P12(self):
        codes = self.codes(
            plate(),
            {"id": "tool", "type": "cylinder",
             "parameters": {"diameter": 20, "height": 20,
                            "position": {"x": 50, "y": 30, "z": -5},
                            "axis": "+Z"}},
            {"id": "bore", "type": "subtract", "target": "plate",
             "tools": ["tool"]},
            chamfer(target="tool"),
        )
        self.assertIn(P12, codes)

    def test_the_target_survives_and_can_be_filleted_afterwards(self):
        self.assertEqual(
            self.codes(
                plate(), chamfer(), fillet(edges=axis_parallel("X"))
            ),
            [],
        )

    def test_selector_problems_built_in_code_are_P15_to_P17(self):
        base = parse_plan_text(generated(plate())).operations[0]
        for selector, expected in (
            (EdgeSelector("named"), P15),
            (EdgeSelector("axis_parallel"), P16),
            (EdgeSelector("all", "Z"), P16),
            (EdgeSelector("axis_parallel", "+Z"), P17),
        ):
            with self.subTest(selector=selector):
                built = OperationPlan(
                    status=PlanStatus.GENERATED,
                    operations=(
                        base,
                        ChamferOperation(
                            id="b", target="plate", distance=2, edges=selector
                        ),
                    ),
                )
                self.assertIn(
                    expected,
                    [p.code for p in validate_plan(built).problems],
                )

    def test_feasibility_is_not_predicted(self):
        """E4 and E5 remain the engine's, as for fillet."""
        for edges in (axis_parallel("X"), axis_parallel("Z")):
            with self.subTest(edges=edges):
                self.assertEqual(
                    self.codes(rod(), chamfer(target="rod", edges=edges)), []
                )


class AdapterTests(unittest.TestCase):
    def test_a_chamfer_becomes_a_v1_chamfer_feature(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), chamfer()))
        )
        self.assertEqual(
            document["features"][1],
            {"id": "bevel", "type": "chamfer", "target": "plate",
             "distance": 2.0,
             "edges": {"select": "axis_parallel", "axis": "Z"}},
        )

    def test_no_radius_reaches_the_document(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), chamfer()))
        )
        self.assertNotIn("radius", document["features"][1])

    def test_the_selector_passes_through_untouched(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), chamfer(edges=ALL)))
        )
        self.assertEqual(document["features"][1]["edges"], {"select": "all"})

    def test_a_fillet_and_a_chamfer_coexist_in_order(self):
        document = plan_to_document(
            parse_plan_text(
                generated(plate(), fillet(edges=axis_parallel("X")), chamfer())
            )
        )
        self.assertEqual(
            [(f["id"], f["type"]) for f in document["features"]],
            [("plate", "box"), ("round", "fillet"), ("bevel", "chamfer")],
        )


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())
        cls.box_z = lpp.run_fixture("chamfer-box-z")
        cls.box_x = lpp.run_fixture("chamfer-box-x")
        cls.box_all = lpp.run_fixture("chamfer-box-all")
        cls.drilled = lpp.run_fixture("chamfer-after-through-hole")

    def test_the_z_chamfer_volume_matches_the_closed_form(self):
        expected = 100.0 * 60.0 * 10.0 - 4.0 * 10.0 * BEVEL_2MM
        self.assertTrue(self.box_z["built"], self.box_z.get("build_error"))
        self.assertEqual(self.box_z["solid_count"], 1)
        self.assertTrue(
            math.isclose(self.box_z["volume_mm3"], expected, rel_tol=VOLUME_RTOL),
            f"{self.box_z['volume_mm3']} != {expected}",
        )

    def test_the_x_chamfer_volume_matches_the_closed_form(self):
        expected = 100.0 * 60.0 * 10.0 - 4.0 * 100.0 * BEVEL_2MM
        self.assertTrue(
            math.isclose(self.box_x["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )

    def test_a_chamfer_removes_more_than_a_fillet_of_the_same_size(self):
        """A bevel cuts the whole triangle; a blend leaves a quarter circle.

        d^2/2 = 2 against r^2(1 - pi/4) = 0.858 per unit length, so this is a
        real geometric relationship rather than a coincidence of one build.
        """
        fillet_result = lpp.run_fixture("fillet-box-z")
        self.assertLess(self.box_z["volume_mm3"], fillet_result["volume_mm3"])
        removed_bevel = 100.0 * 60.0 * 10.0 - self.box_z["volume_mm3"]
        removed_blend = 100.0 * 60.0 * 10.0 - fillet_result["volume_mm3"]
        self.assertTrue(
            math.isclose(
                removed_bevel / removed_blend,
                (2.0**2 / 2.0) / (2.0**2 * (1.0 - math.pi / 4.0)),
                rel_tol=1e-6,
            )
        )

    def test_bevelling_does_not_change_the_bounding_box(self):
        """Compared with a tolerance, never exactly.

        Bevelling all twelve edges leaves float noise in the kernel's own
        bounding box -- 60.00000000000001 rather than 60. That is a property
        of double-precision boolean geometry, not a defect, and asserting
        exact equality on a kernel-derived length would be the mistake.
        """
        for name, result in (
            ("z", self.box_z), ("x", self.box_x), ("all", self.box_all)
        ):
            with self.subTest(fixture=name):
                box = result["bounding_box"]
                for axis, expected in (
                    ("x", 100.0), ("y", 60.0), ("z", 10.0)
                ):
                    self.assertTrue(
                        math.isclose(box[axis], expected, rel_tol=1e-9),
                        f"{axis}: {box[axis]} != {expected}",
                    )

    def test_the_topology_gains_one_face_per_bevelled_edge(self):
        self.assertEqual(self.box_z["face_count"], 10)
        self.assertEqual(self.box_all["face_count"], 26)

    def test_a_chamfer_after_a_through_hole_builds(self):
        expected = (
            100.0 * 60.0 * 10.0
            - math.pi * 10.0**2 * 10.0
            - 4.0 * 100.0 * BEVEL_2MM
        )
        self.assertTrue(self.drilled["built"])
        self.assertEqual(self.drilled["solid_count"], 1)
        self.assertTrue(
            math.isclose(
                self.drilled["volume_mm3"], expected, rel_tol=VOLUME_RTOL
            )
        )
        # The cavity face survives: the rim is circular and never selected.
        self.assertEqual(self.drilled["face_count"], 11)

    def test_every_chamfer_fixture_produces_a_render_model(self):
        for name, result in (
            ("z", self.box_z), ("x", self.box_x), ("all", self.box_all),
            ("drilled", self.drilled),
        ):
            with self.subTest(fixture=name):
                render = result["render_model"]
                self.assertIsNotNone(render)
                self.assertEqual(render["units"], "mm")
                self.assertGreater(render["triangles"], 12)

    def test_the_plan_route_equals_the_cad_core_route(self):
        for fixture_name, edges in (
            ("chamfer-box-z", {"select": "axis_parallel", "axis": "Z"}),
            ("chamfer-box-all", {"select": "all"}),
        ):
            with self.subTest(fixture=fixture_name):
                direct = self.service.build_document(
                    BuildDocumentRequest.for_outputs(
                        {
                            "schema_version": "1.0.0", "units": "mm",
                            "name": "direct",
                            "features": [
                                {"id": "plate", "type": "box",
                                 "size": {"x": 100, "y": 60, "z": 10}},
                                {"id": "bevel", "type": "chamfer",
                                 "target": "plate", "distance": 2,
                                 "edges": edges},
                            ],
                        },
                        "geometry",
                    )
                )
                self.assertTrue(direct.succeeded)
                expected = direct.artifact("geometry").details

                plan = parse_plan(lpp.fixture_plan(fixture_name))
                built = build_plan(self.service, plan, name=f"p-{fixture_name}")
                self.assertTrue(built.built)
                actual = built.outcome.artifact("geometry").details

                self.assertTrue(
                    math.isclose(
                        actual["volume_mm3"], expected["volume_mm3"],
                        rel_tol=VOLUME_RTOL,
                    )
                )
                for field in (
                    "face_count", "edge_count", "solid_count", "bounding_box"
                ):
                    self.assertEqual(actual[field], expected[field], field)


class EngineAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def build(self, *operations):
        plan = parse_plan_text(generated(*operations))
        self.assertTrue(validate_plan(plan).valid)
        return build_plan(self.service, plan, name="engine")

    def message(self, built):
        outcome = built.outcome
        return (
            outcome.error.message if outcome and outcome.error
            else (built.error or "")
        )

    def test_a_selector_matching_nothing_is_E4(self):
        built = self.build(rod(), chamfer(target="rod", edges=axis_parallel("X")))
        self.assertFalse(built.built)
        self.assertIn("E4", self.message(built))

    def test_a_seam_only_selection_is_E5_not_E4(self):
        built = self.build(rod(), chamfer(target="rod", edges=axis_parallel("Z")))
        self.assertFalse(built.built)
        message = self.message(built)
        self.assertIn("E5", message)
        self.assertNotIn("E4", message)

    def test_a_matched_edge_is_never_silently_dropped(self):
        built = self.build(rod(), chamfer(target="rod", edges=ALL))
        self.assertFalse(built.built)
        self.assertIn("E5", self.message(built))
        self.assertIsNone(built.outcome.manifest)

    def test_an_inadmissible_distance_is_refused(self):
        built = self.build(plate(), chamfer(distance=200))
        self.assertFalse(built.built)


class SecurityTests(unittest.TestCase):
    CODE = (
        "import os", "exec('x')", "eval('1')", "subprocess.run(['ls'])",
        "open('/etc/passwd')", "__import__('os')", "__class__",
        "{{7*7}}", "../../etc/passwd", "lambda: 1",
    )

    def test_code_in_a_chamfer_field_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                for operation in (
                    chamfer(distance=payload),
                    chamfer(edges={"select": payload}),
                    chamfer(edges=axis_parallel(payload)),
                    chamfer(edges={"select": "all", payload: 1}),
                ):
                    with self.assertRaises(PlanParseError):
                        parse_plan_text(generated(plate(), operation))

    def test_no_payload_in_a_chamfer_target_is_ever_accepted(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                try:
                    parsed = parse_plan_text(
                        generated(plate(), chamfer(target=payload))
                    )
                except PlanParseError:
                    continue
                self.assertFalse(validate_plan(parsed).valid)

    def test_markdown_and_prose_are_still_rejected(self):
        for text in (
            "```json\n" + generated(plate(), chamfer()) + "\n```",
            "Here it is:\n" + generated(plate(), chamfer()),
        ):
            with self.subTest(text=text[:20]):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)


class RegressionTests(unittest.TestCase):
    """The immediately previous operations, re-measured."""

    def test_fillet_and_the_earlier_operations_are_unchanged(self):
        expectations = {
            "fillet-box-z": 100.0 * 60.0 * 10.0
            - 4.0 * 10.0 * 2.0**2 * (1.0 - math.pi / 4.0),
            "plate-four-holes": 100.0 * 60.0 * 10.0
            - 4.0 * math.pi * 4.0**2 * 10.0,
            "subtract-cube-bore": 50.0**3 - math.pi * 10.0**2 * 50.0,
            "box-100x60x10": 60000.0,
        }
        for name, expected in expectations.items():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name)
                self.assertTrue(result["built"])
                self.assertTrue(
                    math.isclose(
                        result["volume_mm3"], expected, rel_tol=VOLUME_RTOL
                    ),
                    f"{name}: {result['volume_mm3']} != {expected}",
                )

    def test_a_chamfer_is_a_modifier_that_consumes_nothing(self):
        parsed = parse_plan_text(generated(plate(), chamfer()))
        self.assertTrue(is_modifier(parsed.operations[1]))
        self.assertFalse(is_consuming(parsed.operations[1]))

    def test_every_rejection_fixture_is_still_refused(self):
        for name in lpp.rejecting_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name)
                self.assertTrue(
                    result["rejected_for_the_right_reason"],
                    f"{name}: expected {result['expected_codes']}, "
                    f"observed {result.get('observed_codes')}",
                )


if __name__ == "__main__":
    unittest.main()
