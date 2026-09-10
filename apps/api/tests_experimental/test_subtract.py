"""Stage 34: the subtract operation, and the history semantics it introduces.

`subtract` is the first operation that *consumes*: a tool solid is gone from
the solid set after its first use. That makes an id's meaning depend on where
in the plan you are, which is what these tests exercise.

Geometry is asserted against the built B-rep. Volumes come from closed forms
with an explicit relative tolerance; the cavity is checked in the mesh's own
coordinates, never inferred from a face or triangle count.
"""

from __future__ import annotations

import json
import math
import re
import tempfile
import unittest

from cad_core.application_service import CadApplicationService

from cad_experimental import local_plan_provider as lpp
from cad_experimental.adapter import plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.parser import MAX_TOOLS, PlanParseError, parse_plan, parse_plan_text
from cad_experimental.plan import (
    CONSTRUCTIVE_TYPES,
    CONSUMING_TYPES,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    SUBTRACT,
    OperationPlan,
    PlanStatus,
    SubtractOperation,
    is_consuming,
    is_modifier,
    operation_to_dict,
    plan_schema,
    tools_of,
)
from cad_experimental.validation import (
    P2, P9, P10, P11, P12, P13, P14, RULE_CODES, validate_plan,
)

VOLUME_RTOL = 1e-9
MESH_TOLERANCE_MM = 0.05

#: Payloads that are legal identifiers under rule S8. They parse as labels
#: and are caught by the reference rules a layer later.
_ID_SHAPED = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def box(identifier="body", x=50, y=50, z=50, **extra):
    parameters = {"x": x, "y": y, "z": z}
    parameters.update(extra)
    return {"id": identifier, "type": "box", "parameters": parameters}


def cylinder(identifier="tool", diameter=20, height=50, **extra):
    parameters = {"diameter": diameter, "height": height}
    parameters.setdefault("position", {"x": 25, "y": 25, "z": 0})
    parameters.setdefault("axis", "+Z")
    parameters.update(extra)
    return {"id": identifier, "type": "cylinder", "parameters": parameters}


def hole(identifier="h", target="body"):
    return {
        "id": identifier, "type": "through_hole", "target": target,
        "parameters": {"diameter": 4, "position": {"x": 5, "y": 5, "z": 0}},
    }


def subtract(identifier="cut", target="body", tools=("tool",), **extra):
    operation = {
        "id": identifier, "type": "subtract",
        "target": target, "tools": list(tools),
    }
    operation.update(extra)
    return operation


def generated(*operations):
    return json.dumps(
        {"status": "generated", "summary": "s", "operations": list(operations)}
    )


def valid_plan():
    return generated(box(), cylinder(), subtract())


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_contains_subtract(self):
        """This module owns subtract only; the exact set is pinned once."""
        self.assertIn(SUBTRACT, OPERATION_TYPES)

    def test_subtract_is_a_consuming_modifier(self):
        self.assertIn(SUBTRACT, MODIFIER_TYPES)
        self.assertIn(SUBTRACT, CONSUMING_TYPES)
        self.assertNotIn(SUBTRACT, CONSTRUCTIVE_TYPES)

    def test_subtract_is_the_only_consuming_operation(self):
        self.assertEqual(CONSUMING_TYPES, (SUBTRACT,))

    def test_no_unimplemented_operation_crept_in(self):
        for absent in (
            "chamfer", "sketch", "extrude", "revolve", "sweep",
            "loft", "pattern", "mirror", "union", "intersect", "assembly",
            "joint", "drawing", "material",
        ):
            self.assertNotIn(absent, OPERATION_TYPES)

    def test_the_new_rule_codes_are_registered(self):
        for code in (P13, P14):
            self.assertIn(code, RULE_CODES)

    def test_the_schema_describes_tools(self):
        item = plan_schema()["properties"]["operations"]["items"]
        self.assertIn("tools", item["properties"])
        tools = item["properties"]["tools"]
        self.assertEqual(tools["type"], "array")
        self.assertEqual(tools["minItems"], 1)
        self.assertEqual(tools["items"]["type"], "string")

    def test_the_schema_no_longer_requires_parameters(self):
        """A subtract has none, so requiring one would forbid a valid plan."""
        item = plan_schema()["properties"]["operations"]["items"]
        self.assertEqual(set(item["required"]), {"id", "type"})


class ParseTests(unittest.TestCase):
    def test_a_subtract_parses(self):
        parsed = parse_plan_text(valid_plan())
        operation = parsed.operations[2]
        self.assertIsInstance(operation, SubtractOperation)
        self.assertEqual(operation.target, "body")
        self.assertEqual(operation.tools, ("tool",))

    def test_tools_keep_their_order(self):
        """Section C.4 removes tools in list order, so order is geometry."""
        parsed = parse_plan_text(
            generated(
                box(), cylinder("a"), cylinder("b"), cylinder("c"),
                subtract(tools=("c", "a", "b")),
            )
        )
        self.assertEqual(parsed.operations[4].tools, ("c", "a", "b"))

    def test_a_subtract_has_no_parameters(self):
        parsed = parse_plan_text(valid_plan())
        self.assertEqual(parsed.operations[2].parameters(), {})

    def test_a_subtract_round_trips_without_a_parameters_key(self):
        parsed = parse_plan_text(valid_plan())
        payload = operation_to_dict(parsed.operations[2])
        self.assertEqual(set(payload), {"id", "type", "target", "tools"})
        self.assertEqual(payload["tools"], ["tool"])

    def test_reparsing_a_round_trip_is_stable(self):
        first = parse_plan_text(valid_plan())
        again = parse_plan(json.loads(json.dumps(first.to_dict())))
        self.assertEqual(first, again)

    def test_a_parameters_key_on_a_subtract_is_rejected(self):
        """One shape for a subtract, not two -- even an empty one is wrong."""
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(box(), cylinder(), subtract(parameters={}))
            )
        self.assertIn("unknown fields", caught.exception.message)

    def test_missing_tools_is_a_parse_error(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(
                    box(), cylinder(),
                    {"id": "cut", "type": "subtract", "target": "body"},
                )
            )
        self.assertIn("tools", caught.exception.message)

    def test_an_empty_tools_list_is_a_parse_error(self):
        """Rule S14. A malformed subtract, not a bad reference."""
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(generated(box(), cylinder(), subtract(tools=())))
        self.assertIn("empty", caught.exception.message)

    def test_tools_must_be_a_list(self):
        for value in ("tool", 5, {"a": 1}, True):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            box(), cylinder(),
                            {"id": "cut", "type": "subtract",
                             "target": "body", "tools": value},
                        )
                    )

    def test_a_missing_target_is_a_parse_error(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(
                    box(), cylinder(),
                    {"id": "cut", "type": "subtract", "tools": ["tool"]},
                )
            )

    def test_a_non_string_tool_is_rejected(self):
        for value in (1, None, [], {}, True):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(box(), cylinder(), subtract(tools=(value,)))
                    )

    def test_a_tool_that_could_not_be_an_id_is_rejected(self):
        for value in ("1tool", "a b", "../tool", "tool.x", ""):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(box(), cylinder(), subtract(tools=(value,)))
                    )

    def test_too_many_tools_is_refused(self):
        tools = [f"t{index}" for index in range(MAX_TOOLS + 1)]
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box(), subtract(tools=tools)))

    def test_tools_on_a_non_subtract_are_rejected(self):
        for operation in (
            box(tools=["x"]),
            {"id": "h", "type": "through_hole", "target": "body",
             "tools": ["x"],
             "parameters": {"diameter": 4,
                            "position": {"x": 1, "y": 1, "z": 0}}},
        ):
            with self.subTest(type=operation["type"]):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(box(), operation))


class HistoryTests(unittest.TestCase):
    """The solid set changes as the plan is read. That is the point."""

    def verdict(self, *operations):
        return validate_plan(parse_plan_text(generated(*operations)))

    def codes(self, *operations):
        return [problem.code for problem in self.verdict(*operations).problems]

    def test_a_simple_subtract_is_valid(self):
        self.assertTrue(self.verdict(box(), cylinder(), subtract()).valid)

    def test_two_tools_in_one_subtract_are_valid(self):
        self.assertTrue(
            self.verdict(
                box(), cylinder("a"), cylinder("b"),
                subtract(tools=("a", "b")),
            ).valid
        )

    def test_a_consumed_tool_cannot_be_reused_as_a_tool(self):
        """The history rule: P12, and it is now reachable."""
        codes = self.codes(
            box(), cylinder("a"),
            subtract("first", tools=("a",)),
            subtract("second", tools=("a",)),
        )
        self.assertIn(P12, codes)

    def test_a_consumed_tool_cannot_be_targeted_later(self):
        codes = self.codes(
            box(), cylinder("a"),
            subtract("cut", tools=("a",)),
            hole("h", target="a"),
        )
        self.assertIn(P12, codes)

    def test_the_p12_message_names_where_it_was_consumed(self):
        problems = self.verdict(
            box(), cylinder("a"),
            subtract("first", tools=("a",)),
            subtract("second", tools=("a",)),
        ).problems
        message = next(p.message for p in problems if p.code == P12)
        self.assertIn("consumed at operations[2]", message)

    def test_the_target_survives_and_can_be_used_again(self):
        """A subtract replaces its target in place; the target lives on."""
        self.assertTrue(
            self.verdict(
                box(), cylinder("a"),
                subtract("cut", tools=("a",)),
                hole("h", target="body"),
            ).valid
        )

    def test_two_subtracts_on_the_same_target_are_valid(self):
        self.assertTrue(
            self.verdict(
                box(), cylinder("a"), cylinder("b"),
                subtract("first", tools=("a",)),
                subtract("second", tools=("b",)),
            ).valid
        )

    def test_a_subtract_id_never_names_a_solid(self):
        codes = self.codes(
            box(), cylinder("a"), cylinder("b"),
            subtract("cut", tools=("a",)),
            subtract("second", target="cut", tools=("b",)),
        )
        self.assertIn(P11, codes)

    def test_a_subtract_id_cannot_be_a_tool(self):
        codes = self.codes(
            box(), cylinder("a"), cylinder("b"),
            subtract("cut", tools=("a",)),
            subtract("second", tools=("cut",)),
        )
        self.assertIn(P11, codes)

    def test_a_nonexistent_tool_is_P9(self):
        self.assertIn(P9, self.codes(box(), subtract(tools=("ghost",))))

    def test_a_future_tool_is_P10(self):
        codes = self.codes(box(), subtract(tools=("tool",)), cylinder())
        self.assertIn(P10, codes)

    def test_a_modifier_as_a_tool_is_P11(self):
        codes = self.codes(
            box(), cylinder(), hole("h"), subtract(tools=("h",))
        )
        self.assertIn(P11, codes)

    def test_a_modifier_as_a_target_is_P11(self):
        codes = self.codes(
            box(), cylinder(), hole("h"), subtract(target="h")
        )
        self.assertIn(P11, codes)

    def test_a_duplicate_tool_is_P14(self):
        codes = self.codes(box(), cylinder(), subtract(tools=("tool", "tool")))
        self.assertIn(P14, codes)

    def test_the_target_inside_tools_is_P14(self):
        codes = self.codes(box(), cylinder(), subtract(tools=("body",)))
        self.assertIn(P14, codes)

    def test_a_self_referencing_subtract_is_P10(self):
        codes = self.codes(box(), cylinder(), subtract(target="cut"))
        self.assertIn(P10, codes)

    def test_a_duplicate_id_is_still_P2(self):
        codes = self.codes(
            box(), cylinder("a"), cylinder("b"),
            subtract("cut", tools=("a",)),
            subtract("cut", tools=("b",)),
        )
        self.assertIn(P2, codes)

    def test_an_empty_tool_list_built_in_code_is_P13(self):
        """The parser blocks this; the validator must not rely on that."""
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(box())).operations[0],
                SubtractOperation(id="cut", target="body", tools=()),
            ),
        )
        self.assertIn(P13, [p.code for p in validate_plan(built).problems])

    def test_an_unresolved_tool_is_not_treated_as_consumed(self):
        """One problem, not two: a bogus tool must not poison later checks."""
        verdict = self.verdict(
            box(), cylinder("a"),
            subtract("first", tools=("ghost",)),
            subtract("second", tools=("a",)),
        )
        codes = [p.code for p in verdict.problems]
        self.assertEqual(codes.count(P9), 1)
        self.assertNotIn(P12, codes)

    def test_a_target_consumed_by_an_earlier_subtract_is_P12(self):
        codes = self.codes(
            box("first"), box("second", x=10, y=10, z=10), cylinder("a"),
            subtract("cut", target="first", tools=("second",)),
            subtract("again", target="second", tools=("a",)),
        )
        self.assertIn(P12, codes)


class AdapterTests(unittest.TestCase):
    def test_a_subtract_becomes_a_v1_subtract_feature(self):
        document = plan_to_document(parse_plan_text(valid_plan()))
        self.assertEqual(
            document["features"][2],
            {"id": "cut", "type": "subtract",
             "target": "body", "tools": ["tool"]},
        )

    def test_tool_order_is_preserved_through_conversion(self):
        parsed = parse_plan_text(
            generated(
                box(), cylinder("a"), cylinder("b"), cylinder("c"),
                subtract(tools=("c", "a", "b")),
            )
        )
        document = plan_to_document(parsed)
        self.assertEqual(document["features"][4]["tools"], ["c", "a", "b"])

    def test_operation_order_is_preserved(self):
        parsed = parse_plan_text(
            generated(box(), cylinder("a"), cylinder("b"),
                      subtract(tools=("a", "b")))
        )
        document = plan_to_document(parsed)
        self.assertEqual(
            [feature["id"] for feature in document["features"]],
            ["body", "a", "b", "cut"],
        )

    def test_no_parameters_key_reaches_the_document(self):
        document = plan_to_document(parse_plan_text(valid_plan()))
        self.assertNotIn("parameters", document["features"][2])

    def test_ids_are_never_renamed(self):
        parsed = parse_plan_text(
            generated(box("plate"), cylinder("cutter"),
                      subtract("op", target="plate", tools=("cutter",)))
        )
        document = plan_to_document(parsed)
        self.assertEqual(document["features"][2]["target"], "plate")
        self.assertEqual(document["features"][2]["tools"], ["cutter"])
        self.assertEqual(document["features"][2]["id"], "op")


class GeometryTests(unittest.TestCase):
    """The built B-rep is the authority."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())
        cls.cube = lpp.run_fixture("subtract-cube-bore")
        cls.plate = lpp.run_fixture("subtract-plate-bore")
        cls.two = lpp.run_fixture("subtract-two-tools")

    def test_the_cube_bore_is_exactly_one_solid(self):
        self.assertTrue(self.cube["built"], self.cube.get("build_error"))
        self.assertEqual(self.cube["solid_count"], 1)
        self.assertTrue(self.cube["is_solid"])

    def test_the_cube_bore_volume_matches_the_closed_form(self):
        expected = 50.0**3 - math.pi * 10.0**2 * 50.0
        self.assertTrue(
            math.isclose(self.cube["volume_mm3"], expected, rel_tol=VOLUME_RTOL),
            f"{self.cube['volume_mm3']} != {expected}",
        )

    def test_the_cube_keeps_its_outside_dimensions(self):
        self.assertEqual(
            self.cube["bounding_box"], {"x": 50.0, "y": 50.0, "z": 50.0}
        )
        self.assertTrue(self.cube["bounding_box_matches"])

    def test_material_was_actually_removed(self):
        """Not merely "succeeded": less material than the solid started with."""
        self.assertLess(self.cube["volume_mm3"], 50.0**3)
        removed = 50.0**3 - self.cube["volume_mm3"]
        self.assertTrue(
            math.isclose(removed, math.pi * 10.0**2 * 50.0, rel_tol=VOLUME_RTOL)
        )

    def test_the_plate_bore_matches_the_closed_form(self):
        expected = 100.0 * 60.0 * 10.0 - math.pi * 10.0**2 * 10.0
        self.assertTrue(
            math.isclose(self.plate["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(self.plate["solid_count"], 1)

    def test_two_tools_remove_both_volumes(self):
        expected = 100.0 * 60.0 * 10.0 - 2.0 * math.pi * 8.0**2 * 10.0
        self.assertTrue(
            math.isclose(self.two["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(self.two["solid_count"], 1)

    def test_a_subtract_produces_the_same_geometry_as_a_through_hole(self):
        """Two routes to one cavity, cross-checked against each other.

        A d20 bore through the plate by `subtract` and the same bore as a
        `through_hole` are different plans and must be the same solid. This
        catches an adapter that quietly changed the semantics of either.
        """
        as_subtract = self.plate["volume_mm3"]
        as_hole = parse_plan_text(
            generated(
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100, "y": 60, "z": 10}},
                {"id": "h", "type": "through_hole", "target": "plate",
                 "parameters": {"diameter": 20,
                                "position": {"x": 50, "y": 30, "z": 0}}},
            )
        )
        built = build_plan(self.service, as_hole, name="cross-check")
        self.assertTrue(built.built)
        hole_volume = built.outcome.artifact("geometry").details["volume_mm3"]
        self.assertTrue(
            math.isclose(as_subtract, hole_volume, rel_tol=VOLUME_RTOL),
            f"subtract {as_subtract} != through_hole {hole_volume}",
        )

    def test_every_subtract_fixture_produces_a_render_model(self):
        for name, result in (
            ("cube", self.cube), ("plate", self.plate), ("two", self.two)
        ):
            with self.subTest(fixture=name):
                render = result["render_model"]
                self.assertIsNotNone(render)
                self.assertEqual(render["units"], "mm")
                self.assertEqual(
                    render["coordinate_system"], "right_handed_z_up"
                )
                self.assertGreater(render["triangles"], 12)

    def test_the_mesh_shows_the_cavity_not_a_flat_face(self):
        """Checked in the mesh's own coordinates, not by counting triangles.

        The plate is cut by a d20 tool at (50, 30). On the top face there
        must be no vertex inside that circle, a rim of vertices on it, and no
        triangle whose centroid falls within it.
        """
        plan = parse_plan(lpp.fixture_plan("subtract-plate-bore"))
        built = build_plan(self.service, plan, name="cavity-check")
        self.assertTrue(built.built)
        render = built.outcome.render_model
        self.assertIsNotNone(render)

        radius, centre_x, centre_y = 10.0, 50.0, 30.0
        top = [v for v in render.vertices if abs(v[2] - 10.0) < 1e-9]
        self.assertTrue(top, "the mesh has no top face")

        distances = [
            math.hypot(v[0] - centre_x, v[1] - centre_y) for v in top
        ]
        self.assertEqual(
            [d for d in distances if d < radius - MESH_TOLERANCE_MM],
            [],
            "a vertex lies inside the cavity",
        )
        rim = [d for d in distances if abs(d - radius) < MESH_TOLERANCE_MM]
        self.assertGreater(len(rim), 8, "the cavity has no rim")

        covered = 0
        for a, b, c in render.triangles:
            corners = (
                render.vertices[a], render.vertices[b], render.vertices[c]
            )
            if not all(abs(v[2] - 10.0) < 1e-9 for v in corners):
                continue
            centroid_x = sum(v[0] for v in corners) / 3.0
            centroid_y = sum(v[1] for v in corners) / 3.0
            if (
                math.hypot(centroid_x - centre_x, centroid_y - centre_y)
                < radius - MESH_TOLERANCE_MM
            ):
                covered += 1
        self.assertEqual(covered, 0, "the mesh covers the cavity")


class ExistingValidatorTests(unittest.TestCase):
    """The V1 validator stays authoritative and stays unmodified."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def test_the_converted_document_is_ordinary_valid_v1(self):
        for name in ("subtract-cube-bore", "subtract-two-tools"):
            with self.subTest(fixture=name):
                document = plan_to_document(parse_plan(lpp.fixture_plan(name)))
                self.assertTrue(
                    self.service.validate_document(document).valid
                )

    def test_two_unconsumed_solids_are_rejected_by_S9(self):
        """The plan layer deliberately does not duplicate the S9 rule."""
        plan = parse_plan_text(
            generated(box(), cylinder("spare",
                                      position={"x": 200, "y": 200, "z": 0}))
        )
        self.assertTrue(validate_plan(plan).valid, "the plan itself is fine")
        built = build_plan(self.service, plan, name="two-solids")
        self.assertFalse(built.built)
        self.assertIn("S9", built.outcome.error.message)

    def test_a_bad_tool_the_plan_validator_missed_would_still_fail(self):
        """Defence in depth: S6 is not delegated to the plan validator."""
        document = plan_to_document(parse_plan_text(valid_plan()))
        document["features"][2]["tools"] = ["ghost"]
        self.assertFalse(self.service.validate_document(document).valid)

    def test_a_duplicate_tool_the_plan_validator_missed_would_still_fail(self):
        document = plan_to_document(parse_plan_text(valid_plan()))
        document["features"][2]["tools"] = ["tool", "tool"]
        self.assertFalse(self.service.validate_document(document).valid)

    def test_a_tool_that_removes_nothing_is_refused_by_the_engine(self):
        """Not a rule code -- the engine refuses it, and that is enough.

        Section C.4 lists only E2 and E3, so a tool that misses the target
        has no rule of its own. The plan is well formed; the build fails.
        """
        plan = parse_plan_text(
            generated(
                box(),
                cylinder("tool", position={"x": 500, "y": 500, "z": 0}),
                subtract(),
            )
        )
        self.assertTrue(validate_plan(plan).valid)
        built = build_plan(self.service, plan, name="missing-tool")
        self.assertFalse(built.built)


class FixtureTests(unittest.TestCase):
    def test_the_rejection_fixtures_are_all_rejected_for_their_own_reason(self):
        for name in lpp.rejecting_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name)
                self.assertTrue(result["rejected"], name)
                self.assertTrue(
                    result["rejected_for_the_right_reason"],
                    f"{name}: expected {result['expected_codes']}, "
                    f"observed {result.get('observed_codes')}",
                )

    def test_every_building_fixture_still_builds(self):
        for name in lpp.building_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name)
                self.assertTrue(result["built"], result.get("build_error"))
                self.assertTrue(result["bounding_box_matches"])
                self.assertTrue(result["face_count_matches"])
                # `None` means the fixture has no closed form and is
                # cross-checked against cad-core in the tests instead.
                # `False` is still a real failure.
                if result["volume_matches"] is None:
                    self.assertEqual(
                        lpp.fixture(name)["expected_volume_mm3"],
                        lpp.CROSS_CHECKED,
                    )
                else:
                    self.assertTrue(result["volume_matches"])

    def test_the_two_kinds_of_fixture_are_disjoint_and_complete(self):
        self.assertEqual(
            set(lpp.building_fixtures()) | set(lpp.rejecting_fixtures()),
            set(lpp.FIXTURE_NAMES),
        )
        self.assertEqual(
            set(lpp.building_fixtures()) & set(lpp.rejecting_fixtures()), set()
        )

    def test_a_rejection_fixture_names_the_rule_it_tests(self):
        for name in lpp.rejecting_fixtures():
            with self.subTest(fixture=name):
                self.assertTrue(lpp.fixture(name)["expected_codes"])


class SecurityTests(unittest.TestCase):
    """Stage 32/33 guarantees, extended over `tools`."""

    CODE = (
        "import os", "exec('x')", "eval('1')", "subprocess.run(['ls'])",
        "open('/etc/passwd')", "__import__('os')", "__class__",
        "__class__.__mro__[1].__subclasses__()",
        "os.environ['ANTHROPIC_API_KEY']", "{{7*7}}", "../../etc/passwd",
        "lambda: 1",
    )

    def test_code_in_a_tool_is_rejected_by_the_parser(self):
        for payload in self.CODE:
            if _ID_SHAPED.match(payload):
                continue  # a legal id; covered below
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(box(), cylinder(), subtract(tools=(payload,)))
                    )

    def test_no_payload_in_a_tool_is_ever_accepted_overall(self):
        """The property that matters: none produces a usable plan."""
        for payload in self.CODE:
            with self.subTest(payload=payload):
                try:
                    parsed = parse_plan_text(
                        generated(box(), cylinder(), subtract(tools=(payload,)))
                    )
                except PlanParseError:
                    continue
                self.assertFalse(validate_plan(parsed).valid)

    def test_an_id_shaped_dunder_tool_is_rejected_by_the_validator(self):
        """A tool is compared as a string, never used to look anything up."""
        for payload in ("__class__", "__globals__", "os"):
            with self.subTest(payload=payload):
                parsed = parse_plan_text(
                    generated(box(), cylinder(), subtract(tools=(payload,)))
                )
                self.assertEqual(parsed.operations[2].tools, (payload,))
                verdict = validate_plan(parsed)
                self.assertFalse(verdict.valid)
                self.assertIn(P9, [p.code for p in verdict.problems])

    def test_code_in_a_target_is_still_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                try:
                    parsed = parse_plan_text(
                        generated(box(), cylinder(), subtract(target=payload))
                    )
                except PlanParseError:
                    continue
                self.assertFalse(validate_plan(parsed).valid)

    def test_a_python_word_is_a_fine_id_and_a_fine_tool(self):
        """Harmless ids are not rejected for resembling Python.

        `exec` is a legal identifier under rule S8, and a tool id is only
        ever compared as a string. So it works as a label, and is still
        refused as a type or a parameter name.
        """
        for identifier in ("exec", "eval", "os", "import_os", "lambda_", "_x"):
            with self.subTest(identifier=identifier):
                parsed = parse_plan_text(
                    generated(
                        box(),
                        cylinder(identifier),
                        subtract(tools=(identifier,)),
                    )
                )
                self.assertTrue(validate_plan(parsed).valid)
                self.assertEqual(parsed.operations[2].tools, (identifier,))

    def test_the_same_word_as_an_operation_type_is_rejected(self):
        for word in ("exec", "eval", "os", "subtract_all"):
            with self.subTest(word=word):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated({"id": "b", "type": word, "parameters": {}})
                    )

    def test_a_markdown_block_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text("```json\n" + valid_plan() + "\n```")

    def test_prose_mixed_with_the_plan_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text("Here is your part:\n" + valid_plan())


class Stage32And33RegressionTests(unittest.TestCase):
    """Adding subtract must not have moved anything that already worked."""

    @classmethod
    def setUpClass(cls):
        cls.results = {
            name: lpp.run_fixture(name)
            for name in (
                "box-100x60x10", "cylinder-d20-h50-z", "cylinder-d16-h30-x",
                "plate-one-hole", "plate-four-holes",
            )
        }

    def test_the_box_is_unchanged(self):
        result = self.results["box-100x60x10"]
        self.assertTrue(result["built"])
        self.assertEqual(result["face_count"], 6)
        self.assertTrue(
            math.isclose(result["volume_mm3"], 60000.0, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(result["render_model"]["triangles"], 12)

    def test_the_z_cylinder_is_unchanged(self):
        result = self.results["cylinder-d20-h50-z"]
        self.assertTrue(
            math.isclose(
                result["volume_mm3"], math.pi * 10.0**2 * 50.0,
                rel_tol=VOLUME_RTOL,
            )
        )

    def test_the_x_cylinder_is_still_reoriented(self):
        self.assertEqual(
            self.results["cylinder-d16-h30-x"]["bounding_box"],
            {"x": 30.0, "y": 16.0, "z": 16.0},
        )

    def test_the_single_hole_plate_is_unchanged(self):
        result = self.results["plate-one-hole"]
        expected = 100.0 * 60.0 * 10.0 - math.pi * 4.0**2 * 10.0
        self.assertTrue(
            math.isclose(result["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(result["face_count"], 7)

    def test_the_four_hole_plate_is_unchanged(self):
        result = self.results["plate-four-holes"]
        expected = 100.0 * 60.0 * 10.0 - 4.0 * math.pi * 4.0**2 * 10.0
        self.assertTrue(
            math.isclose(result["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(result["face_count"], 10)
        self.assertEqual(result["solid_count"], 1)

    def test_a_plan_without_a_subtract_still_needs_no_tools(self):
        parsed = parse_plan_text(generated(box()))
        self.assertTrue(validate_plan(parsed).valid)
        self.assertEqual(tools_of(parsed.operations[0]), ())

    def test_the_three_kinds_of_operation_are_distinguishable(self):
        parsed = parse_plan_text(valid_plan())
        body, tool, cut = parsed.operations
        self.assertFalse(is_modifier(body))
        self.assertFalse(is_consuming(body))
        self.assertFalse(is_modifier(tool))
        self.assertTrue(is_modifier(cut))
        self.assertTrue(is_consuming(cut))

    def test_a_through_hole_is_a_modifier_but_consumes_nothing(self):
        parsed = parse_plan_text(generated(box(), hole("h")))
        self.assertTrue(is_modifier(parsed.operations[1]))
        self.assertFalse(is_consuming(parsed.operations[1]))


if __name__ == "__main__":
    unittest.main()
