"""Stage 35: the fillet operation, and the edge-selection semantics it needs.

`fillet` is the first operation that names *edges*, and the first whose
feasibility the plan layer cannot judge. Two rules belong to the engine and
are deliberately not guessed at here:

* **E4** -- the selector matched no edge;
* **E5** -- the selector matched edges the kernel will not blend, so the
  whole feature fails. Matched edges are never quietly dropped.

`docs/edge-selection.md` records that a selector may legitimately match an
edge a fillet cannot accept: a parameterisation seam. Nothing in the plan
layer special-cases that, and these tests hold it to that.
"""

from __future__ import annotations

import json
import math
import re
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
    CONSTRUCTIVE_TYPES,
    CONSUMING_TYPES,
    FILLET,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    SELECT_MODES,
    SELECTOR_AXES,
    AXES,
    EdgeSelector,
    FilletOperation,
    OperationPlan,
    PlanStatus,
    is_consuming,
    is_modifier,
    operation_to_dict,
    plan_schema,
)
from cad_experimental.validation import (
    P4, P9, P10, P11, P12, P15, P16, P17, RULE_CODES, validate_plan,
)

VOLUME_RTOL = 1e-9

#: A vertical corner blended at radius r removes r^2(1 - pi/4) in section.
CORNER_2MM = 2.0**2 * (1.0 - math.pi / 4.0)

_ID_SHAPED = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


def plate(identifier="plate"):
    return {
        "id": identifier, "type": "box",
        "parameters": {"x": 100, "y": 60, "z": 10},
    }


def rod(identifier="rod", axis="+Z"):
    return {
        "id": identifier, "type": "cylinder",
        "parameters": {"diameter": 20, "height": 50, "axis": axis},
    }


def axis_parallel(axis):
    return {"select": "axis_parallel", "axis": axis}


ALL = {"select": "all"}

#: Distinguishes "not given" from "given as something falsy". Without it,
#: `edges=None` and `edges=""` would silently become the default selector and
#: the test would assert nothing.
_MISSING = object()


def fillet(
    identifier="round", target="plate", radius=2, edges=_MISSING, **extra
):
    parameters = {
        "radius": radius,
        "edges": axis_parallel("Z") if edges is _MISSING else edges,
    }
    parameters.update(extra.pop("parameters", {}))
    operation = {
        "id": identifier, "type": "fillet",
        "target": target, "parameters": parameters,
    }
    operation.update(extra)
    return operation


def generated(*operations):
    return json.dumps(
        {"status": "generated", "summary": "s", "operations": list(operations)}
    )


class VocabularyTests(unittest.TestCase):
    def test_fillet_is_still_in_the_vocabulary(self):
        """Was `test_exactly_five_operations_exist` at Stage 35.

        Narrowed rather than deleted when Stage 36 added chamfer and Stage 37
        added sketch: what this class is entitled to assert is that fillet is
        there and unchanged, not how many operations exist in total. The
        current total is pinned once, in the stage that last changed it.
        """
        self.assertIn(FILLET, OPERATION_TYPES)

    def test_fillet_is_a_modifier_that_consumes_nothing(self):
        self.assertIn(FILLET, MODIFIER_TYPES)
        self.assertNotIn(FILLET, CONSUMING_TYPES)
        self.assertNotIn(FILLET, CONSTRUCTIVE_TYPES)

    def test_no_further_operation_crept_in(self):
        """`chamfer` (36), `sketch` (37), `extrude` and `revolve` (38) left
        this list when they were built.

        Everything still here is genuinely absent, so the guard still bites.
        """
        for absent in (
            "sweep", "loft",
            "pattern", "mirror", "union", "assembly", "joint", "drawing",
            "material",
        ):
            self.assertNotIn(absent, OPERATION_TYPES)

    def test_the_new_rule_codes_are_registered(self):
        for code in (P15, P16, P17):
            self.assertIn(code, RULE_CODES)

    def test_selector_axes_are_unsigned_and_distinct_from_cylinder_axes(self):
        """Section C.7 says so in as many words, so it is pinned here."""
        self.assertEqual(SELECTOR_AXES, ("X", "Y", "Z"))
        for axis in SELECTOR_AXES:
            self.assertNotIn(axis, AXES)
        for signed in AXES:
            self.assertNotIn(signed, SELECTOR_AXES)

    def test_only_two_selectors_exist(self):
        self.assertEqual(set(SELECT_MODES), {"all", "axis_parallel"})

    def test_the_schema_describes_the_selector_object(self):
        """Stage 41: per-type branches, and the selector lives in `$defs`."""
        schema = plan_schema()
        branch = next(
            b for b in schema["properties"]["operations"]["items"]["anyOf"]
            if b["properties"]["type"]["const"] == FILLET
        )
        parameters = branch["properties"]["parameters"]
        self.assertIn("radius", parameters["properties"])
        # Both are REQUIRED on a fillet now, not merely available.
        self.assertEqual(set(parameters["required"]), {"radius", "edges"})

        reference = parameters["properties"]["edges"]["$ref"]
        edges = schema["$defs"][reference.rsplit("/", 1)[-1]]
        self.assertEqual(edges["type"], "object")
        self.assertEqual(set(edges["properties"]["select"]["enum"]),
                         {"all", "axis_parallel"})
        self.assertEqual(set(edges["properties"]["axis"]["enum"]),
                         {"X", "Y", "Z"})
        self.assertFalse(edges["additionalProperties"])


class ParseTests(unittest.TestCase):
    def test_a_fillet_parses(self):
        parsed = parse_plan_text(generated(plate(), fillet()))
        operation = parsed.operations[1]
        self.assertIsInstance(operation, FilletOperation)
        self.assertEqual(operation.target, "plate")
        self.assertEqual(operation.radius, 2.0)
        self.assertEqual(operation.edges, EdgeSelector("axis_parallel", "Z"))

    def test_the_all_selector_parses_without_an_axis(self):
        parsed = parse_plan_text(generated(plate(), fillet(edges=ALL)))
        self.assertEqual(parsed.operations[1].edges, EdgeSelector("all"))
        self.assertIsNone(parsed.operations[1].edges.axis)

    def test_every_unsigned_axis_parses(self):
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                parsed = parse_plan_text(
                    generated(plate(), fillet(edges=axis_parallel(axis)))
                )
                self.assertEqual(parsed.operations[1].edges.axis, axis)

    def test_a_fillet_round_trips(self):
        parsed = parse_plan_text(generated(plate(), fillet()))
        payload = operation_to_dict(parsed.operations[1])
        self.assertEqual(set(payload), {"id", "type", "target", "parameters"})
        self.assertEqual(
            payload["parameters"]["edges"],
            {"select": "axis_parallel", "axis": "Z"},
        )

    def test_reparsing_a_round_trip_is_stable(self):
        first = parse_plan_text(generated(plate(), fillet(edges=ALL)))
        again = parse_plan(json.loads(json.dumps(first.to_dict())))
        self.assertEqual(first, again)

    def test_a_bare_string_selector_is_rejected(self):
        """`"all"` is not a selector. V1 requires an object."""
        for value in ("all", "axis_parallel", "Z"):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError) as caught:
                    parse_plan_text(generated(plate(), fillet(edges=value)))
                self.assertIn("must be an object", caught.exception.message)

    def test_a_signed_selector_axis_is_rejected(self):
        """The one difference a model is most likely to get wrong."""
        for signed in ("+X", "-X", "+Y", "-Y", "+Z", "-Z"):
            with self.subTest(axis=signed):
                with self.assertRaises(PlanParseError) as caught:
                    parse_plan_text(
                        generated(plate(), fillet(edges=axis_parallel(signed)))
                    )
                self.assertIn("invalid `axis`", caught.exception.message)
                self.assertIn("UNSIGNED", caught.exception.detail or "")

    def test_a_lowercase_selector_axis_is_rejected(self):
        for axis in ("x", "y", "z"):
            with self.subTest(axis=axis):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), fillet(edges=axis_parallel(axis)))
                    )

    def test_an_unknown_selector_axis_is_rejected(self):
        for axis in ("W", "XY", "1", "all"):
            with self.subTest(axis=axis):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), fillet(edges=axis_parallel(axis)))
                    )

    def test_axis_parallel_without_an_axis_is_rejected(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(plate(), fillet(edges={"select": "axis_parallel"}))
            )
        self.assertIn("missing `axis`", caught.exception.message)

    def test_all_with_an_axis_is_rejected(self):
        """Rule S18 both ways: absent for `all`, present for the other."""
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(plate(), fillet(edges={"select": "all", "axis": "Z"}))
            )
        self.assertIn("must not carry `axis`", caught.exception.message)

    def test_an_unknown_select_mode_is_rejected(self):
        for mode in ("vertical", "ALL", "edges", "named", "tangent"):
            with self.subTest(mode=mode):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), fillet(edges={"select": mode}))
                    )

    def test_an_unknown_selector_field_is_rejected(self):
        for extra in ("edge_ids", "ids", "indices", "tags", "filter"):
            with self.subTest(field=extra):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            plate(),
                            fillet(edges={"select": "all", extra: [1]}),
                        )
                    )

    def test_a_selector_missing_select_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(plate(), fillet(edges={"axis": "Z"})))

    def test_a_non_object_selector_is_rejected(self):
        for value in (5, None, [1, 2], True, ""):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), fillet(edges=value)))

    def test_a_non_numeric_radius_is_rejected(self):
        for value in ("2", None, [2], True, {"mm": 2}):
            with self.subTest(value=value):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), fillet(radius=value)))

    def test_a_missing_radius_or_edges_is_rejected(self):
        for parameters in ({"edges": axis_parallel("Z")}, {"radius": 2}):
            with self.subTest(parameters=sorted(parameters)):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            plate(),
                            {"id": "round", "type": "fillet",
                             "target": "plate", "parameters": parameters},
                        )
                    )

    def test_a_missing_target_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(
                    plate(),
                    {"id": "round", "type": "fillet",
                     "parameters": {"radius": 2, "edges": axis_parallel("Z")}},
                )
            )

    def test_an_unknown_fillet_parameter_is_rejected(self):
        for extra in ("distance", "setback", "variable", "per_edge"):
            with self.subTest(parameter=extra):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            plate(),
                            fillet(parameters={extra: 1}),
                        )
                    )

    def test_tools_on_a_fillet_are_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(plate(), fillet(tools=["plate"]))
            )


class ValidationTests(unittest.TestCase):
    def verdict(self, *operations):
        return validate_plan(parse_plan_text(generated(*operations)))

    def codes(self, *operations):
        return [problem.code for problem in self.verdict(*operations).problems]

    def test_a_valid_fillet_passes(self):
        self.assertTrue(self.verdict(plate(), fillet()).valid)

    def test_the_all_selector_passes(self):
        self.assertTrue(self.verdict(plate(), fillet(edges=ALL)).valid)

    def test_a_non_positive_radius_is_P4(self):
        for radius in (0, -2, -0.5):
            with self.subTest(radius=radius):
                self.assertIn(P4, self.codes(plate(), fillet(radius=radius)))

    def test_a_future_target_is_P10(self):
        codes = self.codes(
            plate(), fillet(target="later"),
            {"id": "later", "type": "box",
             "parameters": {"x": 1, "y": 1, "z": 1}},
        )
        self.assertIn(P10, codes)

    def test_a_modifier_target_is_P11(self):
        codes = self.codes(
            plate(),
            {"id": "h", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 8,
                            "position": {"x": 10, "y": 10, "z": 0}}},
            fillet(target="h"),
        )
        self.assertIn(P11, codes)

    def test_a_fillets_own_id_never_names_a_solid(self):
        codes = self.codes(
            plate(), fillet("first"), fillet("second", target="first")
        )
        self.assertIn(P11, codes)

    def test_a_consumed_target_is_P12(self):
        codes = self.codes(
            plate(),
            {"id": "tool", "type": "cylinder",
             "parameters": {"diameter": 20, "height": 20,
                            "position": {"x": 50, "y": 30, "z": -5},
                            "axis": "+Z"}},
            {"id": "bore", "type": "subtract",
             "target": "plate", "tools": ["tool"]},
            fillet(target="tool"),
        )
        self.assertIn(P12, codes)

    def test_the_target_survives_a_fillet_and_can_be_used_again(self):
        """A fillet replaces its target in place; the target lives on."""
        self.assertTrue(
            self.verdict(
                plate(), fillet("first"),
                fillet("second", edges=axis_parallel("X")),
            ).valid
        )

    def test_a_bad_selector_built_in_code_is_P15(self):
        """The parser blocks these; the validator must not rely on that."""
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(plate())).operations[0],
                FilletOperation(
                    id="round", target="plate", radius=2,
                    edges=EdgeSelector(select="named"),
                ),
            ),
        )
        self.assertIn(P15, [p.code for p in validate_plan(built).problems])

    def test_a_missing_selector_axis_built_in_code_is_P16(self):
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(plate())).operations[0],
                FilletOperation(
                    id="round", target="plate", radius=2,
                    edges=EdgeSelector(select="axis_parallel"),
                ),
            ),
        )
        self.assertIn(P16, [p.code for p in validate_plan(built).problems])

    def test_an_axis_on_all_built_in_code_is_P16(self):
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(plate())).operations[0],
                FilletOperation(
                    id="round", target="plate", radius=2,
                    edges=EdgeSelector(select="all", axis="Z"),
                ),
            ),
        )
        self.assertIn(P16, [p.code for p in validate_plan(built).problems])

    def test_a_signed_selector_axis_built_in_code_is_P17(self):
        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                parse_plan_text(generated(plate())).operations[0],
                FilletOperation(
                    id="round", target="plate", radius=2,
                    edges=EdgeSelector(select="axis_parallel", axis="+Z"),
                ),
            ),
        )
        self.assertIn(P17, [p.code for p in validate_plan(built).problems])

    def test_the_plan_validator_does_not_predict_feasibility(self):
        """E4 and E5 are geometric. A plan that will fail is still a plan.

        Both of these validate cleanly and then fail at the engine -- which
        is the correct division, and is asserted rather than assumed.
        """
        for edges in (axis_parallel("X"), axis_parallel("Z")):
            with self.subTest(edges=edges):
                self.assertTrue(
                    self.verdict(rod(), fillet(target="rod", edges=edges)).valid
                )


class AdapterTests(unittest.TestCase):
    def test_a_fillet_becomes_a_v1_fillet_feature(self):
        document = plan_to_document(parse_plan_text(generated(plate(), fillet())))
        self.assertEqual(
            document["features"][1],
            {
                "id": "round", "type": "fillet", "target": "plate",
                "radius": 2.0,
                "edges": {"select": "axis_parallel", "axis": "Z"},
            },
        )

    def test_the_all_selector_carries_no_axis_into_the_document(self):
        document = plan_to_document(
            parse_plan_text(generated(plate(), fillet(edges=ALL)))
        )
        self.assertEqual(document["features"][1]["edges"], {"select": "all"})

    def test_the_selector_passes_through_untouched(self):
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                document = plan_to_document(
                    parse_plan_text(
                        generated(plate(), fillet(edges=axis_parallel(axis)))
                    )
                )
                self.assertEqual(
                    document["features"][1]["edges"],
                    {"select": "axis_parallel", "axis": axis},
                )

    def test_the_adapter_contains_no_selector_logic(self):
        """Selection is `cad_core.edge_selection`'s. Not reimplemented here.

        Scanned from the AST, not the text: the adapter's comments explain
        that it deliberately does *not* special-case a parameterisation
        seam, and a raw-text scan would flag that explanation as the very
        thing it rules out.
        """
        import ast

        import cad_experimental.adapter as module

        with open(module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.name)
        for forbidden in (
            "GeomAbs", "is_straight_edge", "line_direction", "select_edges",
            "edge_selection", "BRepFilletAPI",
        ):
            self.assertNotIn(forbidden, names)

    def test_operation_order_is_preserved(self):
        document = plan_to_document(
            parse_plan_text(
                generated(plate(), fillet("a"),
                          fillet("b", edges=axis_parallel("X")))
            )
        )
        self.assertEqual(
            [feature["id"] for feature in document["features"]],
            ["plate", "a", "b"],
        )


class GeometryTests(unittest.TestCase):
    """The built B-rep is the authority."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())
        cls.box_z = lpp.run_fixture("fillet-box-z")
        cls.box_x = lpp.run_fixture("fillet-box-x")
        cls.box_all = lpp.run_fixture("fillet-box-all")
        cls.drilled = lpp.run_fixture("fillet-after-through-hole")
        cls.cut = lpp.run_fixture("fillet-after-subtract")

    def test_the_z_fillet_is_one_solid(self):
        self.assertTrue(self.box_z["built"], self.box_z.get("build_error"))
        self.assertEqual(self.box_z["solid_count"], 1)
        self.assertTrue(self.box_z["is_solid"])

    def test_the_z_fillet_volume_matches_the_closed_form(self):
        """60000 minus four corner blends, each over the 10 mm height."""
        expected = 100.0 * 60.0 * 10.0 - 4.0 * 10.0 * CORNER_2MM
        self.assertTrue(
            math.isclose(self.box_z["volume_mm3"], expected, rel_tol=VOLUME_RTOL),
            f"{self.box_z['volume_mm3']} != {expected}",
        )

    def test_the_x_fillet_volume_matches_the_closed_form(self):
        expected = 100.0 * 60.0 * 10.0 - 4.0 * 100.0 * CORNER_2MM
        self.assertTrue(
            math.isclose(self.box_x["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )

    def test_filleting_does_not_change_the_bounding_box(self):
        """A blend removes material inside the silhouette, never outside it."""
        for name, result in (
            ("z", self.box_z), ("x", self.box_x), ("all", self.box_all)
        ):
            with self.subTest(fixture=name):
                self.assertEqual(
                    result["bounding_box"],
                    {"x": 100.0, "y": 60.0, "z": 10.0},
                )

    def test_material_was_removed_not_added(self):
        for name, result in (
            ("z", self.box_z), ("x", self.box_x), ("all", self.box_all)
        ):
            with self.subTest(fixture=name):
                self.assertLess(result["volume_mm3"], 100.0 * 60.0 * 10.0)

    def test_the_topology_gains_one_face_per_blended_edge(self):
        """Four Z edges blended: six planar faces become ten."""
        self.assertEqual(self.box_z["face_count"], 10)
        self.assertEqual(self.box_z["edge_count"], 24)
        self.assertEqual(self.box_x["face_count"], 10)

    def test_the_all_selector_is_permitted_on_a_plain_box(self):
        """Measured, not assumed: on a plain box every edge is blendable."""
        self.assertTrue(self.box_all["built"], self.box_all.get("build_error"))
        self.assertEqual(self.box_all["solid_count"], 1)
        self.assertEqual(self.box_all["face_count"], 26)

    def test_the_all_fillet_removes_more_than_a_single_axis_fillet(self):
        self.assertLess(self.box_all["volume_mm3"], self.box_z["volume_mm3"])

    def test_a_fillet_after_a_through_hole_builds(self):
        expected = (
            100.0 * 60.0 * 10.0
            - math.pi * 10.0**2 * 10.0
            - 4.0 * 100.0 * CORNER_2MM
        )
        self.assertTrue(self.drilled["built"])
        self.assertEqual(self.drilled["solid_count"], 1)
        self.assertTrue(
            math.isclose(
                self.drilled["volume_mm3"], expected, rel_tol=VOLUME_RTOL
            )
        )

    def test_a_fillet_after_a_subtract_builds(self):
        expected = (
            100.0 * 60.0 * 10.0
            - math.pi * 10.0**2 * 10.0
            - 4.0 * 60.0 * CORNER_2MM
        )
        self.assertTrue(self.cut["built"])
        self.assertEqual(self.cut["solid_count"], 1)
        self.assertTrue(
            math.isclose(self.cut["volume_mm3"], expected, rel_tol=VOLUME_RTOL)
        )

    def test_the_hole_rim_survives_an_axis_fillet(self):
        """The specification's own example: rims are circles, never selected.

        The drilled plate keeps its cavity -- one more face than a plain
        filleted plate -- so the blend touched the corners and not the rim.
        """
        self.assertEqual(self.drilled["face_count"], 11)
        self.assertEqual(self.box_x["face_count"], 10)

    def test_the_plan_route_equals_the_cad_core_route(self):
        """The adapter must produce the same geometry as a hand-built document.

        This is the check that the plan layer adds nothing and loses nothing:
        the same part expressed as an operation plan and as a canonical V1
        document must be the same solid, to the kernel's own precision.
        """
        for fixture_name, features in (
            (
                "fillet-box-z",
                [
                    {"id": "plate", "type": "box",
                     "size": {"x": 100, "y": 60, "z": 10}},
                    {"id": "round", "type": "fillet", "target": "plate",
                     "radius": 2,
                     "edges": {"select": "axis_parallel", "axis": "Z"}},
                ],
            ),
            (
                "fillet-box-all",
                [
                    {"id": "plate", "type": "box",
                     "size": {"x": 100, "y": 60, "z": 10}},
                    {"id": "round", "type": "fillet", "target": "plate",
                     "radius": 2, "edges": {"select": "all"}},
                ],
            ),
        ):
            with self.subTest(fixture=fixture_name):
                direct = self.service.build_document(
                    BuildDocumentRequest.for_outputs(
                        {
                            "schema_version": "1.0.0", "units": "mm",
                            "name": "direct", "features": features,
                        },
                        "geometry",
                    )
                )
                self.assertTrue(direct.succeeded)
                expected = direct.artifact("geometry").details

                plan = parse_plan(lpp.fixture_plan(fixture_name))
                built = build_plan(
                    self.service, plan, name=f"plan-{fixture_name}"
                )
                self.assertTrue(built.built)
                actual = built.outcome.artifact("geometry").details

                self.assertTrue(
                    math.isclose(
                        actual["volume_mm3"], expected["volume_mm3"],
                        rel_tol=VOLUME_RTOL,
                    ),
                    f"{actual['volume_mm3']} != {expected['volume_mm3']}",
                )
                self.assertEqual(
                    actual["face_count"], expected["face_count"]
                )
                self.assertEqual(
                    actual["edge_count"], expected["edge_count"]
                )
                self.assertEqual(
                    actual["solid_count"], expected["solid_count"]
                )
                self.assertEqual(
                    actual["bounding_box"], expected["bounding_box"]
                )


class RenderModelTests(unittest.TestCase):
    """The blended surface must actually be in the mesh."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def render_for(self, fixture_name):
        plan = parse_plan(lpp.fixture_plan(fixture_name))
        built = build_plan(self.service, plan, name=f"mesh-{fixture_name}")
        self.assertTrue(built.built, built.outcome.error if built.outcome else "")
        render = built.outcome.render_model
        self.assertIsNotNone(render)
        return render

    def test_every_fillet_fixture_produces_a_render_model(self):
        for name in (
            "fillet-box-z", "fillet-box-x", "fillet-box-all",
            "fillet-after-through-hole", "fillet-after-subtract",
        ):
            with self.subTest(fixture=name):
                render = self.render_for(name)
                self.assertEqual(render.units, "mm")
                self.assertEqual(render.coordinate_system, "right_handed_z_up")
                self.assertGreater(render.triangle_count(), 12)

    def test_a_filleted_box_needs_more_triangles_than_a_plain_one(self):
        """A plain box is 12 triangles; curvature cannot be free."""
        self.assertGreater(self.render_for("fillet-box-z").triangle_count(), 12)

    def test_the_blend_surface_appears_in_the_mesh(self):
        """Vertices on the corner arcs, at the blend radius from its centre.

        The Z fillet rounds the four vertical corners at radius 2, so each
        blend's surface lies on a cylinder of radius 2 whose axis is 2 mm in
        from both faces. Checked by coordinate, not by counting.
        """
        render = self.render_for("fillet-box-z")
        radius = 2.0
        # Arc centres for the four corners of a 100 x 60 plate.
        centres = (
            (2.0, 2.0), (98.0, 2.0), (2.0, 58.0), (98.0, 58.0),
        )
        tolerance = 0.02

        for centre_x, centre_y in centres:
            with self.subTest(centre=(centre_x, centre_y)):
                on_arc = [
                    v for v in render.vertices
                    if abs(
                        math.hypot(v[0] - centre_x, v[1] - centre_y) - radius
                    ) < tolerance
                    # ...and actually in the corner quadrant, not merely at
                    # that distance somewhere along a flank.
                    and min(v[0], 100.0 - v[0]) < radius + tolerance
                    and min(v[1], 60.0 - v[1]) < radius + tolerance
                ]
                self.assertGreater(
                    len(on_arc), 4, "no blend surface at this corner"
                )

    def test_no_vertex_lies_outside_the_blended_silhouette(self):
        """The corner material is gone: nothing remains in the sharp corner."""
        render = self.render_for("fillet-box-z")
        radius = 2.0
        for centre_x, centre_y in (
            (2.0, 2.0), (98.0, 2.0), (2.0, 58.0), (98.0, 58.0)
        ):
            for vertex in render.vertices:
                in_corner = (
                    min(vertex[0], 100.0 - vertex[0]) < radius - 0.02
                    and min(vertex[1], 60.0 - vertex[1]) < radius - 0.02
                )
                if not in_corner:
                    continue
                distance = math.hypot(
                    vertex[0] - centre_x, vertex[1] - centre_y
                )
                self.assertGreaterEqual(
                    distance, radius - 0.02,
                    f"vertex {vertex} sits inside the blend",
                )

    def test_the_curved_normals_are_present_and_finite(self):
        """A blend needs its own normals, and they must be usable numbers."""
        render = self.render_for("fillet-box-z")
        self.assertEqual(len(render.normals), len(render.vertices))

        directions = set()
        for normal in render.normals:
            for component in normal:
                self.assertTrue(
                    math.isfinite(component), f"non-finite normal {normal}"
                )
            length = math.sqrt(sum(c * c for c in normal))
            self.assertAlmostEqual(length, 1.0, places=6)
            directions.add(tuple(round(c, 4) for c in normal))

        # A plain box has exactly six distinct normals. A filleted one must
        # have many more, because the blend surface is curved.
        self.assertGreater(len(directions), 6)


class EngineAuthorityTests(unittest.TestCase):
    """E4 and E5 belong to the engine, and stay honest."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def build(self, *operations):
        plan = parse_plan_text(generated(*operations))
        self.assertTrue(validate_plan(plan).valid, "the plan itself is fine")
        return build_plan(self.service, plan, name="engine")

    def message(self, built):
        outcome = built.outcome
        return (
            outcome.error.message if outcome and outcome.error
            else (built.error or "")
        )

    def test_a_selector_matching_nothing_is_E4(self):
        """axis_parallel X on a +Z cylinder matches no edge."""
        built = self.build(rod(), fillet(target="rod", edges=axis_parallel("X")))
        self.assertFalse(built.built)
        self.assertIn("E4", self.message(built))

    def test_a_selector_matching_only_a_seam_is_E5_not_E4(self):
        """One edge matched, so it is not E4 -- and the kernel refuses it.

        `docs/edge-selection.md` records this: a cylinder's only straight
        edge is its parameterisation seam, and a fillet cannot blend it.
        """
        built = self.build(rod(), fillet(target="rod", edges=axis_parallel("Z")))
        self.assertFalse(built.built)
        message = self.message(built)
        self.assertIn("E5", message)
        self.assertNotIn("E4", message)

    def test_a_matched_edge_is_never_silently_dropped(self):
        """The whole feature fails rather than blending a subset."""
        built = self.build(rod(), fillet(target="rod", edges=axis_parallel("Z")))
        self.assertFalse(built.built)
        self.assertIsNone(built.outcome.manifest)

    def test_all_on_a_bare_cylinder_is_also_E5(self):
        """Measured, and worth recording: it includes the seam.

        `docs/edge-selection.md` notes Stage 13 finding that `all` on a
        cylinder succeeded. Since Stage 14.1 required complete edge
        coverage, it no longer does -- the seam is one of the three edges,
        and an unaccepted edge now fails the feature. Asserted here so the
        experimental layer's documentation stays true to the engine.
        """
        built = self.build(rod(), fillet(target="rod", edges={"select": "all"}))
        self.assertFalse(built.built)
        self.assertIn("E5", self.message(built))

    def test_a_drilled_plate_z_selection_is_E5_because_of_the_cavity_seam(self):
        built = self.build(
            plate(),
            {"id": "h", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20,
                            "position": {"x": 50, "y": 30, "z": 0}}},
            fillet(edges=axis_parallel("Z")),
        )
        self.assertFalse(built.built)
        self.assertIn("E5", self.message(built))

    def test_the_same_plate_x_and_y_selections_are_fine(self):
        """The seam is Z-parallel, so X and Y selections avoid it."""
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                built = self.build(
                    plate(),
                    {"id": "h", "type": "through_hole", "target": "plate",
                     "parameters": {"diameter": 20,
                                    "position": {"x": 50, "y": 30, "z": 0}}},
                    fillet(edges=axis_parallel(axis)),
                )
                self.assertTrue(built.built)

    def test_an_inadmissible_radius_is_refused(self):
        """A radius larger than the material cannot be blended."""
        built = self.build(plate(), fillet(radius=200))
        self.assertFalse(built.built)

    def test_the_plan_layer_does_not_special_case_the_seam(self):
        """No module in the package *implements* edge geometry.

        Names only. The package's prose discusses seams deliberately -- that
        is how a reader learns the E5 behaviour is honest rather than
        accidental -- so what is forbidden is calling or importing the
        selector machinery, not mentioning it.
        """
        import ast
        import pathlib

        import cad_experimental

        root = pathlib.Path(cad_experimental.__file__).resolve().parent
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        names.add(alias.name)
                    if isinstance(node, ast.ImportFrom) and node.module:
                        names.add(node.module)
            for forbidden in (
                "GeomAbs", "select_edges", "is_straight_edge",
                "line_direction", "BRepFilletAPI", "edge_selection",
            ):
                self.assertNotIn(forbidden, names, path.name)


class SecurityTests(unittest.TestCase):
    """Stage 32-34 guarantees, extended over the selector."""

    CODE = (
        "import os", "exec('x')", "eval('1')", "subprocess.run(['ls'])",
        "open('/etc/passwd')", "__import__('os')", "__class__",
        "__class__.__mro__[1].__subclasses__()",
        "os.environ['ANTHROPIC_API_KEY']", "{{7*7}}", "../../etc/passwd",
        "lambda: 1",
    )

    def test_code_as_a_select_mode_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), fillet(edges={"select": payload}))
                    )

    def test_code_as_a_selector_axis_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(plate(), fillet(edges=axis_parallel(payload)))
                    )

    def test_code_as_a_selector_field_name_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            plate(),
                            fillet(edges={"select": "all", payload: 1}),
                        )
                    )

    def test_code_as_a_radius_is_rejected(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(plate(), fillet(radius=payload)))

    def test_no_payload_in_a_fillet_target_is_ever_accepted(self):
        for payload in self.CODE:
            with self.subTest(payload=payload):
                try:
                    parsed = parse_plan_text(
                        generated(plate(), fillet(target=payload))
                    )
                except PlanParseError:
                    continue
                self.assertFalse(validate_plan(parsed).valid)

    def test_a_python_word_is_a_fine_id_and_a_fine_fillet_target(self):
        for identifier in ("exec", "eval", "os", "import_os", "_x"):
            with self.subTest(identifier=identifier):
                parsed = parse_plan_text(
                    generated(plate(identifier), fillet(target=identifier))
                )
                self.assertTrue(validate_plan(parsed).valid)

    def test_a_markdown_block_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                "```json\n" + generated(plate(), fillet()) + "\n```"
            )

    def test_prose_mixed_with_the_plan_is_still_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text("Here it is:\n" + generated(plate(), fillet()))


class RegressionTests(unittest.TestCase):
    """Stages 32-34 must still work exactly as they did."""

    @classmethod
    def setUpClass(cls):
        cls.results = {
            name: lpp.run_fixture(name)
            for name in (
                "box-100x60x10", "cylinder-d20-h50-z", "cylinder-d16-h30-x",
                "plate-one-hole", "plate-four-holes",
                "subtract-cube-bore", "subtract-two-tools",
            )
        }

    def test_the_box_is_unchanged(self):
        result = self.results["box-100x60x10"]
        self.assertEqual(result["face_count"], 6)
        self.assertTrue(
            math.isclose(result["volume_mm3"], 60000.0, rel_tol=VOLUME_RTOL)
        )
        self.assertEqual(result["render_model"]["triangles"], 12)

    def test_the_cylinders_are_unchanged(self):
        self.assertTrue(
            math.isclose(
                self.results["cylinder-d20-h50-z"]["volume_mm3"],
                math.pi * 10.0**2 * 50.0, rel_tol=VOLUME_RTOL,
            )
        )
        self.assertEqual(
            self.results["cylinder-d16-h30-x"]["bounding_box"],
            {"x": 30.0, "y": 16.0, "z": 16.0},
        )

    def test_the_hole_fixtures_are_unchanged(self):
        one = self.results["plate-one-hole"]
        four = self.results["plate-four-holes"]
        self.assertEqual(one["face_count"], 7)
        self.assertEqual(four["face_count"], 10)
        self.assertTrue(
            math.isclose(
                four["volume_mm3"],
                100.0 * 60.0 * 10.0 - 4.0 * math.pi * 4.0**2 * 10.0,
                rel_tol=VOLUME_RTOL,
            )
        )

    def test_the_subtract_fixtures_are_unchanged(self):
        cube = self.results["subtract-cube-bore"]
        two = self.results["subtract-two-tools"]
        self.assertTrue(
            math.isclose(
                cube["volume_mm3"], 50.0**3 - math.pi * 10.0**2 * 50.0,
                rel_tol=VOLUME_RTOL,
            )
        )
        self.assertTrue(
            math.isclose(
                two["volume_mm3"],
                100.0 * 60.0 * 10.0 - 2.0 * math.pi * 8.0**2 * 10.0,
                rel_tol=VOLUME_RTOL,
            )
        )

    def test_a_fillet_consumes_nothing_and_is_a_modifier(self):
        parsed = parse_plan_text(generated(plate(), fillet()))
        self.assertTrue(is_modifier(parsed.operations[1]))
        self.assertFalse(is_consuming(parsed.operations[1]))

    def test_every_rejection_fixture_is_still_refused_for_its_reason(self):
        for name in lpp.rejecting_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name)
                self.assertTrue(result["rejected"], name)
                self.assertTrue(
                    result["rejected_for_the_right_reason"],
                    f"{name}: expected {result['expected_codes']}, "
                    f"observed {result.get('observed_codes')}",
                )


if __name__ == "__main__":
    unittest.main()
