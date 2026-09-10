"""Stage 33: the through_hole operation, and the reference rules it needs.

Geometry is asserted against the built B-rep, never against an impression:
volumes come from closed forms with an explicit relative tolerance, hole
counts from the kernel's face topology, and the openings in the mesh from
the mesh's own coordinates.
"""

from __future__ import annotations

import json
import math
import tempfile
import unittest

from cad_core.application_service import CadApplicationService

from cad_experimental import local_plan_provider as lpp
from cad_experimental.adapter import plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.parser import PlanParseError, parse_plan, parse_plan_text
from cad_experimental.plan import (
    CONSTRUCTIVE_TYPES,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    THROUGH_HOLE,
    OperationPlan,
    PlanStatus,
    Point,
    ThroughHoleOperation,
    is_constructive,
    is_modifier,
    operation_to_dict,
    plan_schema,
)
from cad_experimental.validation import (
    P2, P4, P5, P8, P9, P10, P11, P12, RULE_CODES, validate_plan,
)

#: Relative tolerance for kernel volumes. Never exact equality.
VOLUME_RTOL = 1e-9

#: How far a tessellated vertex may sit from the true surface. The render
#: model's linear deflection is 0.01 mm, so this is generous by 5x.
MESH_TOLERANCE_MM = 0.05


def plate(**overrides):
    operation = {"id": "plate", "type": "box",
                 "parameters": {"x": 100, "y": 60, "z": 10}}
    operation.update(overrides)
    return operation


def hole(identifier="hole1", target="plate", diameter=8,
         position=None, **parameters):
    values = {
        "diameter": diameter,
        "position": position or {"x": 10, "y": 10, "z": 0},
    }
    values.update(parameters)
    return {
        "id": identifier, "type": "through_hole",
        "target": target, "parameters": values,
    }


def generated(*operations):
    return json.dumps(
        {"status": "generated", "summary": "s", "operations": list(operations)}
    )


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_contains_through_hole(self):
        """This module owns through_hole only.

        The exact vocabulary is pinned once, in the newest stage's module,
        so adding an operation breaks one test rather than one per stage.
        """
        self.assertIn(THROUGH_HOLE, OPERATION_TYPES)

    def test_through_hole_is_a_modifier_not_constructive(self):
        self.assertIn(THROUGH_HOLE, MODIFIER_TYPES)
        self.assertNotIn(THROUGH_HOLE, CONSTRUCTIVE_TYPES)

    def test_no_unimplemented_operation_crept_in(self):
        """Operations no stage has implemented, and none silently should."""
        for absent in (
            "chamfer", "sketch", "extrude", "revolve", "sweep", "loft",
            "pattern", "mirror", "union", "assembly",
        ):
            self.assertNotIn(absent, OPERATION_TYPES)

    def test_the_schema_lists_through_hole_and_target(self):
        item = plan_schema()["properties"]["operations"]["items"]
        self.assertIn("through_hole", item["properties"]["type"]["enum"])
        self.assertIn("target", item["properties"])

    def test_the_reference_rule_codes_are_registered(self):
        for code in (P8, P9, P10, P11, P12):
            self.assertIn(code, RULE_CODES)


class ParseTests(unittest.TestCase):
    def test_a_hole_parses_with_target_at_the_operation_level(self):
        parsed = parse_plan_text(generated(plate(), hole()))
        operation = parsed.operations[1]
        self.assertIsInstance(operation, ThroughHoleOperation)
        self.assertEqual(operation.target, "plate")
        self.assertEqual(operation.diameter, 8.0)
        self.assertEqual(operation.position, Point(10.0, 10.0, 0.0))

    def test_axis_defaults_to_absent_not_invented(self):
        parsed = parse_plan_text(
            generated(plate(), {"id": "h", "type": "through_hole",
                                "target": "plate",
                                "parameters": {"diameter": 8,
                                               "position": {"x": 1, "y": 1, "z": 0}}})
        )
        self.assertIsNone(parsed.operations[1].axis)

    def test_every_axis_parses(self):
        for axis in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=axis):
                parsed = parse_plan_text(generated(plate(), hole(axis=axis)))
                self.assertEqual(parsed.operations[1].axis, axis)

    def test_a_hole_round_trips_with_its_target(self):
        parsed = parse_plan_text(generated(plate(), hole()))
        payload = operation_to_dict(parsed.operations[1])
        self.assertEqual(payload["target"], "plate")
        self.assertEqual(set(payload), {"id", "type", "target", "parameters"})

    def test_reparsing_a_round_trip_is_stable(self):
        first = parse_plan_text(generated(plate(), hole(axis="+Z")))
        again = parse_plan(json.loads(json.dumps(first.to_dict())))
        self.assertEqual(first, again)

    def test_position_is_required_for_a_hole(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(plate(), {"id": "h", "type": "through_hole",
                                    "target": "plate",
                                    "parameters": {"diameter": 8}})
            )
        self.assertIn("position", caught.exception.message)

    def test_a_missing_target_is_a_parse_error(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(plate(), {"id": "h", "type": "through_hole",
                                    "parameters": {"diameter": 8,
                                                   "position": {"x": 1, "y": 1, "z": 0}}})
            )
        self.assertIn("target", caught.exception.message)

    def test_a_target_on_a_constructive_operation_is_rejected(self):
        """`target` is meaningless on a box, so it is an unknown field."""
        for operation in (
            plate(target="something"),
            {"id": "c", "type": "cylinder", "target": "x",
             "parameters": {"diameter": 8, "height": 5}},
        ):
            with self.subTest(type=operation["type"]):
                with self.assertRaises(PlanParseError) as caught:
                    parse_plan_text(generated(operation))
                self.assertIn("unknown fields", caught.exception.message)

    def test_a_non_string_target_is_rejected(self):
        for value in (1, None, [], {}, True):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), hole(target=value)))

    def test_a_target_that_could_not_be_an_id_is_rejected(self):
        for value in ("1body", "a b", "../plate", "plate.x", ""):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), hole(target=value)))

    def test_unknown_hole_parameters_are_rejected(self):
        for extra in ("depth", "radius", "blind", "counterbore", "thread"):
            with self.subTest(parameter=extra):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), hole(**{extra: 5}))
                    )

    def test_an_unknown_operation_level_field_is_rejected(self):
        operation = hole()
        operation["through"] = True
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(plate(), operation))


class ReferenceValidationTests(unittest.TestCase):
    """The rules this stage exists to test."""

    def verdict(self, *operations):
        return validate_plan(parse_plan_text(generated(*operations)))

    def codes(self, *operations):
        return [problem.code for problem in self.verdict(*operations).problems]

    def test_a_hole_in_an_earlier_body_is_valid(self):
        self.assertTrue(self.verdict(plate(), hole()).valid)

    def test_four_holes_all_targeting_the_body_are_valid(self):
        holes = [
            hole(f"hole{index}", position={"x": x, "y": y, "z": 0})
            for index, (x, y) in enumerate(
                ((10, 10), (90, 10), (10, 50), (90, 50))
            )
        ]
        self.assertTrue(self.verdict(plate(), *holes).valid)

    def test_a_target_naming_nothing_is_P9(self):
        self.assertIn(P9, self.codes(plate(), hole(target="ghost")))

    def test_a_forward_reference_is_P10(self):
        codes = self.codes(
            plate(),
            hole(target="later"),
            {"id": "later", "type": "box", "parameters": {"x": 1, "y": 1, "z": 1}},
        )
        self.assertIn(P10, codes)

    def test_a_self_reference_is_P10(self):
        """A cycle cannot be longer than one hop: references point earlier."""
        codes = self.codes(plate(), hole("h1", target="h1"))
        self.assertIn(P10, codes)

    def test_targeting_another_hole_is_P11(self):
        """A modifier's id never names a solid (Section B.4)."""
        codes = self.codes(plate(), hole("h1"), hole("h2", target="h1"))
        self.assertIn(P11, codes)

    def test_the_P11_message_explains_where_to_point_instead(self):
        problems = self.verdict(
            plate(), hole("h1"), hole("h2", target="h1")
        ).problems
        message = next(p.message for p in problems if p.code == P11)
        self.assertIn("modifier", message)
        self.assertIn("constructive", message)

    def test_a_hole_as_the_first_operation_has_no_body_to_cut(self):
        self.assertIn(P9, self.codes(hole()))

    def test_a_duplicate_id_is_still_P2(self):
        codes = self.codes(
            plate(), hole("h1"), hole("h1", position={"x": 50, "y": 30, "z": 0})
        )
        self.assertIn(P2, codes)

    def test_a_non_positive_diameter_is_P4(self):
        for diameter in (0, -8, -0.5):
            with self.subTest(diameter=diameter):
                self.assertIn(P4, self.codes(plate(), hole(diameter=diameter)))

    def test_an_invalid_axis_built_in_code_is_P5(self):
        """The parser blocks this; the validator must not depend on that."""
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(plate())).operations[0],
                ThroughHoleOperation(
                    id="h", target="plate", diameter=8,
                    position=Point(1, 1, 0), axis="up",
                ),
            ),
        )
        self.assertIn(P5, [p.code for p in validate_plan(built).problems])

    def test_a_missing_target_built_in_code_is_P8(self):
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                ThroughHoleOperation(
                    id="h", target="", diameter=8, position=Point(1, 1, 0)
                ),
            ),
        )
        self.assertIn(P8, [p.code for p in validate_plan(built).problems])

    def test_only_one_problem_is_reported_per_bad_target(self):
        """Two verdicts about one reference is noise, not information."""
        problems = self.verdict(plate(), hole(target="ghost")).problems
        target_problems = [
            p for p in problems if p.where.endswith(".target")
        ]
        self.assertEqual(len(target_problems), 1)

    def test_a_through_hole_alone_can_never_trigger_the_consumed_rule(self):
        """P12 became reachable in Stage 34, but not through `through_hole`.

        Stage 33 asserted P12 was unreachable at all; `subtract` made it
        reachable, and that is tested in test_subtract.py. What remains true
        here is narrower and still worth pinning: a through_hole consumes
        nothing, so no plan built only from holes can trigger it.
        """
        holes = [hole(f"h{index}") for index in range(3)]
        for code in [p.code for p in self.verdict(plate(), *holes).problems]:
            self.assertNotEqual(code, P12)
        self.assertTrue(self.verdict(plate(), *holes).valid)


class AdapterTests(unittest.TestCase):
    def test_a_hole_becomes_a_v1_through_hole_feature(self):
        document = plan_to_document(parse_plan_text(generated(plate(), hole())))
        self.assertEqual(
            document["features"][1],
            {
                "id": "hole1",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8.0,
                "position": {"x": 10.0, "y": 10.0, "z": 0.0},
            },
        )

    def test_the_axis_is_carried_when_given(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), hole(axis="+X")))
        )
        self.assertEqual(document["features"][1]["axis"], "+X")

    def test_the_axis_is_omitted_when_absent_so_the_contract_defaults(self):
        document = plan_to_document(
            parse_plan_text(
                generated(plate(), {"id": "h", "type": "through_hole",
                                    "target": "plate",
                                    "parameters": {"diameter": 8,
                                                   "position": {"x": 1, "y": 1, "z": 0}}})
            )
        )
        self.assertNotIn("axis", document["features"][1])

    def test_position_is_always_written_for_a_hole(self):
        """Section C.3 requires it, so it is never left to a default."""
        document = plan_to_document(parse_plan_text(generated(plate(), hole())))
        self.assertIn("position", document["features"][1])

    def test_operation_order_is_preserved(self):
        holes = [hole(f"h{index}") for index in range(3)]
        document = plan_to_document(
            parse_plan_text(generated(plate(), *holes))
        )
        self.assertEqual(
            [feature["id"] for feature in document["features"]],
            ["plate", "h0", "h1", "h2"],
        )

    def test_the_adapter_renames_nothing(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), hole("bore", target="plate")))
        )
        self.assertEqual(document["features"][1]["id"], "bore")
        self.assertEqual(document["features"][1]["target"], "plate")


class GeometryTests(unittest.TestCase):
    """The built B-rep is the authority."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())
        cls.four = lpp.run_fixture("plate-four-holes")
        cls.one = lpp.run_fixture("plate-one-hole")
        cls.bored = lpp.run_fixture("cylinder-bored-d20-h50")

    def test_the_four_hole_plate_is_exactly_one_solid(self):
        self.assertTrue(self.four["built"])
        self.assertEqual(self.four["solid_count"], 1)
        self.assertTrue(self.four["is_solid"])

    def test_the_four_hole_plate_has_four_actual_holes(self):
        """Six planar faces plus one cylindrical face per hole."""
        self.assertEqual(self.four["face_count"], 10)
        self.assertTrue(self.four["face_count_matches"])
        self.assertEqual(self.four["expected_hole_count"], 4)

    def test_the_four_hole_volume_matches_the_closed_form(self):
        expected = 100.0 * 60.0 * 10.0 - 4.0 * math.pi * 4.0**2 * 10.0
        self.assertTrue(
            math.isclose(self.four["volume_mm3"], expected, rel_tol=VOLUME_RTOL),
            f"{self.four['volume_mm3']} != {expected}",
        )

    def test_drilling_does_not_change_the_outside_dimensions(self):
        self.assertTrue(self.four["bounding_box_matches"])
        self.assertEqual(
            self.four["bounding_box"], {"x": 100.0, "y": 60.0, "z": 10.0}
        )

    def test_each_hole_removes_the_same_material(self):
        """One hole and four holes differ by exactly three holes' worth."""
        one_hole = 100.0 * 60.0 * 10.0 - self.one["volume_mm3"]
        four_holes = 100.0 * 60.0 * 10.0 - self.four["volume_mm3"]
        self.assertTrue(
            math.isclose(four_holes, 4.0 * one_hole, rel_tol=VOLUME_RTOL)
        )

    def test_a_hole_in_a_cylinder_is_meaningful_and_builds(self):
        """Case 5: a coaxial bore makes a tube -- one connected solid."""
        self.assertTrue(self.bored["built"])
        self.assertEqual(self.bored["solid_count"], 1)
        expected = math.pi * (10.0**2 - 4.0**2) * 50.0
        self.assertTrue(
            math.isclose(self.bored["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )
        # Outer wall, inner wall, two annular ends.
        self.assertEqual(self.bored["face_count"], 4)

    def test_the_render_model_exists_for_every_drilled_fixture(self):
        for name, result in (
            ("one", self.one), ("four", self.four), ("bored", self.bored)
        ):
            with self.subTest(fixture=name):
                render = result["render_model"]
                self.assertIsNotNone(render)
                self.assertEqual(render["units"], "mm")
                self.assertGreater(render["triangles"], 12)

    def test_the_mesh_contains_real_openings_not_painted_ones(self):
        """The holes are absent material in the mesh, checked by coordinate.

        A faked hole would leave the top face tessellated straight across the
        opening. So: no top-face vertex lies inside a hole, every hole has a
        rim of vertices at its radius, and no top-face triangle's centroid
        falls within a hole.
        """
        plan = parse_plan(lpp.fixture_plan("plate-four-holes"))
        built = build_plan(self.service, plan, name="mesh-check")
        self.assertTrue(built.built)
        render = built.outcome.render_model
        self.assertIsNotNone(render)

        radius = 4.0
        centres = ((10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0))
        top = [v for v in render.vertices if abs(v[2] - 10.0) < 1e-9]
        self.assertTrue(top, "the mesh has no top face")

        for centre_x, centre_y in centres:
            with self.subTest(centre=(centre_x, centre_y)):
                distances = [
                    math.hypot(v[0] - centre_x, v[1] - centre_y) for v in top
                ]
                inside = [d for d in distances if d < radius - MESH_TOLERANCE_MM]
                self.assertEqual(inside, [], "a vertex lies inside the hole")
                rim = [
                    d for d in distances
                    if abs(d - radius) < MESH_TOLERANCE_MM
                ]
                self.assertGreater(len(rim), 8, "the hole has no rim")

        covered = 0
        for a, b, c in render.triangles:
            corners = (
                render.vertices[a], render.vertices[b], render.vertices[c]
            )
            if not all(abs(v[2] - 10.0) < 1e-9 for v in corners):
                continue
            centroid_x = sum(v[0] for v in corners) / 3.0
            centroid_y = sum(v[1] for v in corners) / 3.0
            for centre_x, centre_y in centres:
                if (
                    math.hypot(centroid_x - centre_x, centroid_y - centre_y)
                    < radius - MESH_TOLERANCE_MM
                ):
                    covered += 1
                    break
        self.assertEqual(covered, 0, "the mesh covers a hole")


class ExistingValidatorTests(unittest.TestCase):
    """The V1 validator stays authoritative and stays unmodified."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def test_the_converted_document_is_ordinary_valid_v1(self):
        document = plan_to_document(
            parse_plan(lpp.fixture_plan("plate-four-holes"))
        )
        self.assertTrue(self.service.validate_document(document).valid)

    def test_a_bad_reference_the_plan_validator_missed_would_still_fail(self):
        """Defence in depth: S6 is not delegated to the plan validator."""
        document = plan_to_document(parse_plan_text(generated(plate(), hole())))
        document["features"][1]["target"] = "ghost"
        validation = self.service.validate_document(document)
        self.assertFalse(validation.valid)

    def test_a_hole_that_misses_the_material_is_caught_by_the_engine(self):
        """E1 is geometric, so only the engine can judge it -- and does."""
        plan = parse_plan_text(
            generated(plate(), hole(position={"x": 500, "y": 500, "z": 0}))
        )
        self.assertTrue(validate_plan(plan).valid, "the plan itself is fine")
        built = build_plan(self.service, plan, name="missing-hole")
        self.assertFalse(built.built)


#: Payloads that happen to be legal identifiers under rule S8. They parse as
#: labels and are caught a layer later; the distinction is the point.
_ID_SHAPED = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


class SecurityTests(unittest.TestCase):
    """Stage 32's guarantees, extended over the new reference field."""

    CODE = (
        "import os", "exec('x')", "eval('1')", "subprocess.run(['ls'])",
        "open('/etc/passwd')", "__import__('os')", "__class__",
        "os.environ['ANTHROPIC_API_KEY']", "{{7*7}}", "../../etc/passwd",
        "lambda: 1",
    )

    def test_code_in_a_target_is_rejected_by_the_parser(self):
        """Anything that could not be an id cannot be a reference."""
        for payload in self.CODE:
            if _ID_SHAPED.match(payload):
                continue  # handled by the next test
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), hole(target=payload)))

    def test_an_id_shaped_dunder_target_is_rejected_by_the_validator(self):
        """`__class__` is a legal identifier (S8), so the parser accepts it.

        That is correct and safe: a target is only ever compared as a string
        against the declared ids, never used to look anything up, so there is
        no attribute traversal for it to reach. It is rejected all the same,
        one layer later, because it names no operation.
        """
        for payload in ("__class__", "__globals__", "os"):
            with self.subTest(payload=payload):
                parsed = parse_plan_text(
                    generated(plate(), hole(target=payload))
                )
                self.assertEqual(parsed.operations[1].target, payload)
                verdict = validate_plan(parsed)
                self.assertFalse(verdict.valid)
                self.assertIn(P9, [p.code for p in verdict.problems])

    def test_no_payload_in_a_target_is_ever_accepted_overall(self):
        """The property that matters: none of these produces a usable plan."""
        for payload in self.CODE:
            with self.subTest(payload=payload):
                try:
                    parsed = parse_plan_text(
                        generated(plate(), hole(target=payload))
                    )
                except PlanParseError:
                    continue
                self.assertFalse(validate_plan(parsed).valid)

    def test_code_as_a_hole_parameter_name_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), hole(**{payload: 1}))
                    )

    def test_code_as_a_diameter_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), hole(diameter=payload))
                    )

    def test_a_python_word_is_a_fine_id_when_it_is_only_an_id(self):
        """Harmless ids are not rejected for resembling Python.

        `exec` is a legal identifier under rule S8. It is rejected as a
        *type* or a *parameter name* -- structural positions -- and accepted
        as a label, because a label is never looked up or executed.
        """
        for identifier in ("exec", "eval", "os", "import_os", "lambda_", "_x"):
            with self.subTest(identifier=identifier):
                parsed = parse_plan_text(
                    generated(
                        plate(id=identifier),
                        hole("bore", target=identifier),
                    )
                )
                self.assertTrue(validate_plan(parsed).valid)
                self.assertEqual(parsed.operations[0].id, identifier)
                self.assertEqual(parsed.operations[1].target, identifier)

    def test_the_same_word_in_a_structural_position_is_rejected(self):
        for word in ("exec", "eval", "os"):
            with self.subTest(word=word):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated({"id": "b", "type": word, "parameters": {}})
                    )

    def test_a_markdown_block_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                "```json\n" + generated(plate(), hole()) + "\n```"
            )

    def test_prose_mixed_with_operations_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                "Here is your plate:\n" + generated(plate(), hole())
            )


class Stage32RegressionTests(unittest.TestCase):
    """Adding an operation must not have moved the three that worked."""

    @classmethod
    def setUpClass(cls):
        cls.results = {
            name: lpp.run_fixture(name)
            for name in (
                "box-100x60x10", "cylinder-d20-h50-z", "cylinder-d16-h30-x"
            )
        }

    def test_the_box_still_builds_to_the_same_numbers(self):
        result = self.results["box-100x60x10"]
        self.assertTrue(result["built"])
        self.assertEqual(result["solid_count"], 1)
        self.assertEqual(result["face_count"], 6)
        self.assertTrue(
            math.isclose(result["volume_mm3"], 60000.0, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(result["render_model"]["triangles"], 12)

    def test_the_z_cylinder_still_builds_to_the_same_numbers(self):
        result = self.results["cylinder-d20-h50-z"]
        self.assertTrue(result["built"])
        self.assertTrue(
            math.isclose(
                result["volume_mm3"], math.pi * 10.0**2 * 50.0,
                rel_tol=VOLUME_RTOL,
            )
        )

    def test_the_x_cylinder_is_still_reoriented(self):
        result = self.results["cylinder-d16-h30-x"]
        self.assertTrue(result["built"])
        self.assertEqual(
            result["bounding_box"], {"x": 30.0, "y": 16.0, "z": 16.0}
        )

    def test_a_plain_plan_still_needs_no_target(self):
        parsed = parse_plan_text(generated(plate()))
        self.assertTrue(validate_plan(parsed).valid)
        self.assertFalse(hasattr(parsed.operations[0], "target"))

    def test_constructive_and_modifier_are_distinguishable(self):
        parsed = parse_plan_text(generated(plate(), hole()))
        self.assertTrue(is_constructive(parsed.operations[0]))
        self.assertFalse(is_modifier(parsed.operations[0]))
        self.assertTrue(is_modifier(parsed.operations[1]))
        self.assertFalse(is_constructive(parsed.operations[1]))


if __name__ == "__main__":
    unittest.main()
