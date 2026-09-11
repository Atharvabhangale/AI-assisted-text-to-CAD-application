"""Stage 37: the sketch foundation -- represented, validated, not executed.

A sketch is the first thing in this language that the backend **cannot
build**, and that makes the interesting property of these tests a negative
one: that the refusal is explicit, distinct from every other refusal, and
never quietly replaced with geometry.

So the tests are grouped around three claims:

* a sketch is represented precisely (parser, types, round trip);
* it is validated thoroughly (P18-P22, and P11 when someone treats a profile
  as a solid);
* it is refused explicitly (:class:`ExecutionUnsupported`, a distinct HTTP
  status, no document, no mesh, no artifact, no approximation).

Constraints are **checked, not solved**: several tests exist purely to prove
that a disagreement is reported rather than resolved, because a solver would
be indistinguishable from silent repair.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib
import tempfile
import unittest

from cad_core.application_service import CadApplicationService

from cad_experimental import local_plan_provider as lpp
from cad_experimental.adapter import (
    AdapterError,
    ExecutionUnsupported,
    plan_to_document,
)
from cad_experimental.build import build_plan
from cad_experimental.parser import PlanParseError, parse_plan, parse_plan_text
from cad_experimental.plan import (
    CONSTRUCTIVE_TYPES,
    EXECUTABLE_TYPES,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    PROFILE_TYPES,
    SKETCH,
    OperationPlan,
    PlanStatus,
    SketchOperation,
    is_constructive,
    is_executable,
    is_modifier,
    is_profile,
    operation_to_dict,
    plan_schema,
)
from cad_experimental.sketch import (
    CIRCLE,
    CONSTRAINT_APPLIES_TO,
    CONSTRAINT_TYPES,
    DIMENSIONAL_TYPES,
    GEOMETRY_TYPES,
    LINE,
    MAX_CONSTRAINTS,
    MAX_GEOMETRY,
    PLANES,
    POINT_HANDLES,
    RECTANGLE,
    Circle,
    Constraint,
    Line,
    Point2D,
    PointHandle,
    Rectangle,
    SketchDefinition,
    sketch_schema,
)
from cad_experimental.validation import (
    P4,
    P11,
    P18,
    P19,
    P20,
    P21,
    P22,
    RULE_CODES,
    validate_plan,
)

SOURCE = pathlib.Path(lpp.__file__).resolve().parent


# --- builders --------------------------------------------------------------


def line(identifier, x1, y1, x2, y2):
    return {"id": identifier, "type": "line",
            "start": {"x": x1, "y": y1}, "end": {"x": x2, "y": y2}}


def circle(identifier, x, y, radius):
    return {"id": identifier, "type": "circle",
            "centre": {"x": x, "y": y}, "radius": radius}


def rectangle(identifier, x, y, width, height):
    return {"id": identifier, "type": "rectangle",
            "corner": {"x": x, "y": y}, "width": width, "height": height}


def sketch(identifier="profile", plane="XY", geometry=None, constraints=None):
    parameters = {
        "plane": plane,
        "geometry": [rectangle("r1", 0, 0, 100, 60)]
        if geometry is None
        else geometry,
    }
    if constraints is not None:
        parameters["constraints"] = constraints
    return {"id": identifier, "type": "sketch", "parameters": parameters}


def plate(identifier="plate"):
    return {"id": identifier, "type": "box",
            "parameters": {"x": 100, "y": 60, "z": 10}}


def plan(*operations, status="generated"):
    return {"status": status, "summary": "a sketch", "operations": list(operations)}


def problems_of(payload):
    return validate_plan(parse_plan(payload)).problems


def codes_of(payload):
    return [problem.code for problem in problems_of(payload)]


# --- 1. the vocabulary -----------------------------------------------------


class SketchVocabularyTests(unittest.TestCase):
    """What the language now has, and what it deliberately still lacks."""

    def test_sketch_is_an_operation_type(self):
        self.assertIn(SKETCH, OPERATION_TYPES)

    def test_a_sketch_is_a_profile_and_nothing_else(self):
        """Not constructive, not a modifier: a fourth category of its own."""
        self.assertEqual(PROFILE_TYPES, (SKETCH,))
        self.assertNotIn(SKETCH, CONSTRUCTIVE_TYPES)
        self.assertNotIn(SKETCH, MODIFIER_TYPES)

    def test_the_categories_do_not_overlap(self):
        """Four categories since Stage 38 added the profile-solid pair.

        The invariant this asserts is unchanged -- every type is in exactly
        one category -- so the list of categories was extended rather than
        the assertion weakened.
        """
        from cad_experimental.plan import PROFILE_SOLID_TYPES

        for kind in OPERATION_TYPES:
            categories = [
                kind in CONSTRUCTIVE_TYPES,
                kind in MODIFIER_TYPES,
                kind in PROFILE_TYPES,
                kind in PROFILE_SOLID_TYPES,
            ]
            self.assertEqual(
                sum(categories), 1, f"{kind} is in {sum(categories)} categories"
            )

    def test_executable_types_are_exactly_the_six_v1_features(self):
        """The engine's vocabulary, not the language's -- they now differ."""
        self.assertEqual(
            set(EXECUTABLE_TYPES),
            {"box", "cylinder", "through_hole", "subtract", "fillet",
             "chamfer"},
        )
        self.assertNotIn(SKETCH, EXECUTABLE_TYPES)

    def test_the_language_is_now_larger_than_the_engine(self):
        """The whole point of this stage, stated as an assertion.

        Stage 37 could say the difference was exactly ``{sketch}``. Stage 38
        added extrude and revolve, so what remains true here is that a sketch
        is one of the unexecutable types and that the language is the larger
        set. The exact difference is pinned in Stage 38's own module.
        """
        self.assertGreater(len(OPERATION_TYPES), len(EXECUTABLE_TYPES))
        self.assertIn(SKETCH, set(OPERATION_TYPES) - set(EXECUTABLE_TYPES))

    def test_the_predicates_agree_with_the_tuples(self):
        """Measured on real operations: the predicates take an operation."""
        operations = parse_plan(plan(
            plate(),
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 8,
                            "position": {"x": 10, "y": 10, "z": 0}}},
            sketch(),
        )).operations
        for operation in operations:
            kind = operation.TYPE
            with self.subTest(kind=kind):
                self.assertEqual(
                    is_profile(operation), kind in PROFILE_TYPES
                )
                self.assertEqual(
                    is_executable(operation), kind in EXECUTABLE_TYPES
                )
                self.assertEqual(
                    is_constructive(operation), kind in CONSTRUCTIVE_TYPES
                )
                self.assertEqual(
                    is_modifier(operation), kind in MODIFIER_TYPES
                )
        # And the sketch, specifically, is a profile and not executable.
        self.assertTrue(is_profile(operations[2]))
        self.assertFalse(is_executable(operations[2]))

    def test_only_three_principal_planes(self):
        """An arbitrary plane needs a datum, and V1 has no datums."""
        self.assertEqual(PLANES, ("XY", "XZ", "YZ"))

    def test_the_geometry_vocabulary_is_three_primitives(self):
        self.assertEqual(GEOMETRY_TYPES, ("line", "circle", "rectangle"))

    def test_no_spline_arc_or_ellipse_exists(self):
        for absent in ("spline", "arc", "ellipse", "polyline", "bezier",
                       "nurbs", "slot"):
            self.assertNotIn(absent, GEOMETRY_TYPES)

    def test_every_constraint_type_that_names_geometry_says_which_type(self):
        for kind in CONSTRAINT_TYPES:
            if kind == "coincident":
                continue  # relates point handles, not one piece of geometry
            self.assertIn(kind, CONSTRAINT_APPLIES_TO)

    def test_dimensional_constraints_are_the_ones_with_a_value(self):
        self.assertEqual(set(DIMENSIONAL_TYPES), {"length", "radius"})

    def test_point_handles_are_enumerated_per_geometry_type(self):
        self.assertEqual(set(POINT_HANDLES), set(GEOMETRY_TYPES))
        self.assertEqual(POINT_HANDLES[LINE], ("start", "end"))
        self.assertEqual(POINT_HANDLES[CIRCLE], ("centre",))
        self.assertEqual(POINT_HANDLES[RECTANGLE], ("corner",))

    def test_the_new_rule_codes_are_registered(self):
        for code in (P18, P19, P20, P21, P22):
            self.assertIn(code, RULE_CODES)

    def test_the_plan_schema_advertises_the_sketch_parameters(self):
        schema = plan_schema()
        text = json.dumps(schema)
        for key in ("plane", "geometry", "constraints"):
            self.assertIn(key, text)

    def test_the_sketch_schema_requires_at_least_one_shape(self):
        schema = sketch_schema()
        self.assertEqual(schema["geometry"]["minItems"], 1)

    def test_the_upper_bounds_moved_to_the_parser(self):
        """`maxItems` left the schema at Stage 41 -- the structured-output
        API rejects it -- and the PARSER still enforces both limits, which is
        where an authoritative bound belongs. Nothing was weakened."""
        schema = sketch_schema()
        self.assertNotIn("maxItems", schema["geometry"])
        self.assertNotIn("maxItems", schema["constraints"])

        many = [circle(f"c{i}", i, 0, 1) for i in range(MAX_GEOMETRY + 1)]
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=many)))
        constraints = [
            {"id": f"k{i}", "type": "horizontal", "geometry": "l1"}
            for i in range(MAX_CONSTRAINTS + 1)
        ]
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[line("l1", 0, 0, 10, 0)],
                                   constraints=constraints)))

    def test_both_sketch_lists_are_unions_of_required_branches(self):
        """Which is why the fragment contributes no optional property."""
        schema = sketch_schema()
        for key, expected in (("geometry", GEOMETRY_TYPES),
                              ("constraints", CONSTRAINT_TYPES)):
            with self.subTest(key=key):
                self.assertIn("$ref", schema[key]["items"])


# --- 2. parsing ------------------------------------------------------------


class SketchParsingTests(unittest.TestCase):
    """The security boundary, extended to two new nested shapes."""

    def test_a_sketch_parses_into_typed_records(self):
        parsed = parse_plan(plan(sketch(geometry=[
            line("l1", 0, 0, 50, 0),
            circle("c1", 10, 10, 5),
            rectangle("r1", 0, 0, 20, 30),
        ])))
        operation = parsed.operations[0]
        self.assertIsInstance(operation, SketchOperation)
        definition = operation.definition
        self.assertIsInstance(definition, SketchDefinition)
        self.assertEqual(definition.plane, "XY")
        self.assertIsInstance(definition.geometry[0], Line)
        self.assertIsInstance(definition.geometry[1], Circle)
        self.assertIsInstance(definition.geometry[2], Rectangle)
        self.assertIsInstance(definition.geometry[0].start, Point2D)

    def test_constraints_parse_into_typed_records(self):
        parsed = parse_plan(plan(sketch(
            geometry=[line("l1", 0, 0, 50, 0), line("l2", 50, 0, 50, 30)],
            constraints=[
                {"id": "k1", "type": "horizontal", "geometry": "l1"},
                {"id": "k2", "type": "length", "geometry": "l1", "value": 50},
                {"id": "k3", "type": "coincident", "points": [
                    {"geometry": "l1", "point": "end"},
                    {"geometry": "l2", "point": "start"},
                ]},
            ],
        )))
        constraints = parsed.operations[0].definition.constraints
        self.assertEqual(len(constraints), 3)
        for constraint in constraints:
            self.assertIsInstance(constraint, Constraint)
        self.assertEqual(constraints[1].value, 50.0)
        self.assertIsInstance(constraints[2].points[0], PointHandle)
        self.assertEqual(constraints[2].points[0].geometry, "l1")
        self.assertEqual(constraints[2].points[0].point, "end")

    def test_constraints_are_optional(self):
        parsed = parse_plan(plan(sketch()))
        self.assertEqual(parsed.operations[0].definition.constraints, ())

    def test_an_empty_geometry_list_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[])))

    def test_an_unknown_plane_is_rejected(self):
        for bad in ("XA", "xy", "YX", "ZX", "top", "", "XYZ"):
            with self.subTest(plane=bad), self.assertRaises(PlanParseError):
                parse_plan(plan(sketch(plane=bad)))

    def test_an_unknown_geometry_type_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "s1", "type": "spline",
                 "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
            ])))

    def test_an_unknown_constraint_type_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(
                geometry=[line("l1", 0, 0, 10, 0)],
                constraints=[{"id": "k1", "type": "parallel",
                              "geometry": "l1"}],
            )))

    def test_an_unknown_field_on_geometry_is_rejected(self):
        """Rule P7's strictness, inside a sketch too."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": 0, "y": 0},
                 "radius": 5, "colour": "red"},
            ])))

    def test_a_missing_required_geometry_field_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": 0, "y": 0}},
            ])))

    def test_a_geometry_field_from_the_wrong_type_is_rejected(self):
        """A circle's `radius` on a line is an unknown field for a line."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "l1", "type": "line",
                 "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 0},
                 "radius": 5},
            ])))

    def test_a_point_has_exactly_x_and_y(self):
        """2D means 2D: a `z` on a sketch point is an unknown field."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "c1", "type": "circle",
                 "centre": {"x": 0, "y": 0, "z": 0}, "radius": 5},
            ])))

    def test_a_point_given_as_an_array_is_rejected(self):
        """The same deliberate choice the V1 contract makes for positions."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": [0, 0],
                 "radius": 5},
            ])))

    def test_a_numeric_string_is_never_coerced(self):
        for payload in (
            plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": 0, "y": 0},
                 "radius": "5"}])),
            plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": "0", "y": 0},
                 "radius": 5}])),
            plan(sketch(
                geometry=[line("l1", 0, 0, 50, 0)],
                constraints=[{"id": "k1", "type": "length",
                              "geometry": "l1", "value": "50"}])),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan(payload)

    def test_a_boolean_is_not_a_number(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": 0, "y": 0},
                 "radius": True}])))

    def test_nan_and_infinity_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            text = json.dumps(plan(sketch(geometry=[
                {"id": "c1", "type": "circle", "centre": {"x": 0, "y": 0},
                 "radius": 5}]))).replace('"radius": 5', f'"radius": {literal}')
            with self.subTest(literal=literal):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)

    def test_a_dotted_point_reference_is_rejected(self):
        """`"l1.end"` is not a handle. Every reference stays an identifier."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(
                geometry=[line("l1", 0, 0, 10, 0)],
                constraints=[{"id": "k1", "type": "coincident",
                              "points": ["l1.end", "l1.start"]}],
            )))

    def test_a_point_handle_needs_both_fields(self):
        for handle in ({"geometry": "l1"}, {"point": "end"},
                       {"geometry": "l1", "point": "end", "extra": 1}):
            with self.subTest(handle=handle):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(
                        geometry=[line("l1", 0, 0, 10, 0)],
                        constraints=[{"id": "k1", "type": "coincident",
                                      "points": [handle, handle]}],
                    )))

    def test_a_sketch_carries_no_target_and_no_tools(self):
        for extra in ({"target": "plate"}, {"tools": ["plate"]}):
            payload = plan(plate(), {**sketch(), **extra})
            with self.subTest(extra=extra):
                with self.assertRaises(PlanParseError):
                    parse_plan(payload)

    def test_too_much_geometry_is_rejected(self):
        many = [circle(f"c{index}", index, 0, 1)
                for index in range(MAX_GEOMETRY + 1)]
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=many)))

    def test_too_many_constraints_are_rejected(self):
        many = [{"id": f"k{index}", "type": "horizontal", "geometry": "l1"}
                for index in range(MAX_CONSTRAINTS + 1)]
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(geometry=[line("l1", 0, 0, 10, 0)],
                                   constraints=many)))

    def test_a_refusal_still_may_not_carry_a_sketch(self):
        """A sketch is geometry-shaped, so the refusal rule still applies."""
        for status in ("unsupported", "needs_clarification"):
            with self.subTest(status=status):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(), status=status))

    def test_a_parsed_sketch_round_trips_through_its_own_dict(self):
        payload = plan(sketch(
            plane="XZ",
            geometry=[line("l1", 0, 0, 50, 0), circle("c1", 5, 5, 2.5)],
            constraints=[
                {"id": "k1", "type": "horizontal", "geometry": "l1"},
                {"id": "k2", "type": "radius", "geometry": "c1", "value": 2.5},
            ],
        ))
        once = parse_plan(payload)
        twice = parse_plan({
            "status": "generated", "summary": "s",
            "operations": [operation_to_dict(op) for op in once.operations],
        })
        self.assertEqual(
            [operation_to_dict(op) for op in once.operations],
            [operation_to_dict(op) for op in twice.operations],
        )


# --- 3. plan validation ----------------------------------------------------


class SketchValidationTests(unittest.TestCase):
    """P18-P22, and what a profile is not."""

    def test_a_plain_profile_is_valid(self):
        self.assertEqual(codes_of(plan(sketch())), [])

    def test_a_fully_constrained_agreeing_sketch_is_valid(self):
        self.assertEqual(codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 50, 0), circle("c1", 10, 10, 5)],
            constraints=[
                {"id": "k1", "type": "horizontal", "geometry": "l1"},
                {"id": "k2", "type": "length", "geometry": "l1", "value": 50},
                {"id": "k3", "type": "radius", "geometry": "c1", "value": 5},
            ],
        ))), [])

    def test_p18_duplicate_geometry_ids(self):
        self.assertIn(P18, codes_of(plan(sketch(geometry=[
            line("l1", 0, 0, 10, 0), circle("l1", 5, 5, 2),
        ]))))

    def test_p18_a_constraint_may_not_shadow_geometry(self):
        """One namespace inside a sketch, so a constraint id is taken too."""
        self.assertIn(P18, codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0)],
            constraints=[{"id": "l1", "type": "horizontal",
                          "geometry": "l1"}],
        ))))

    def test_p18_duplicate_constraint_ids(self):
        self.assertIn(P18, codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0)],
            constraints=[
                {"id": "k1", "type": "horizontal", "geometry": "l1"},
                {"id": "k1", "type": "vertical", "geometry": "l1"},
            ],
        ))))

    def test_a_sketch_id_shares_the_plan_namespace(self):
        """P2: a sketch's own id is a plan id like any other."""
        codes = codes_of(plan(plate("shared"), sketch("shared")))
        self.assertIn("P2", codes)

    def test_two_sketches_may_reuse_ids_between_them(self):
        """Each sketch is its own namespace; only within one is it shared."""
        self.assertEqual(codes_of(plan(
            sketch("a", geometry=[line("l1", 0, 0, 10, 0)]),
            sketch("b", geometry=[line("l1", 0, 0, 10, 0)]),
        )), [])

    def test_p19_a_constraint_on_absent_geometry(self):
        problems = problems_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0)],
            constraints=[{"id": "k1", "type": "horizontal",
                          "geometry": "l2"}],
        )))
        self.assertEqual([p.code for p in problems], [P19])
        self.assertIn("l2", problems[0].message)

    def test_p19_a_coincidence_on_absent_geometry(self):
        self.assertIn(P19, codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0)],
            constraints=[{"id": "k1", "type": "coincident", "points": [
                {"geometry": "l1", "point": "end"},
                {"geometry": "nope", "point": "start"},
            ]}],
        ))))

    def test_p19_a_constraint_cannot_reach_another_sketch(self):
        """Sketches do not share a namespace, so this is a missing id."""
        self.assertIn(P19, codes_of(plan(
            sketch("a", geometry=[line("outside", 0, 0, 10, 0)]),
            sketch("b", geometry=[line("l1", 0, 0, 10, 0)],
                   constraints=[{"id": "k1", "type": "horizontal",
                                 "geometry": "outside"}]),
        )))

    def test_p19_a_constraint_cannot_name_a_plan_operation(self):
        self.assertIn(P19, codes_of(plan(
            plate(),
            sketch(geometry=[line("l1", 0, 0, 10, 0)],
                   constraints=[{"id": "k1", "type": "horizontal",
                                 "geometry": "plate"}]),
        )))

    def test_p20_a_circle_has_no_end_point(self):
        problems = problems_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0), circle("c1", 10, 0, 3)],
            constraints=[{"id": "k1", "type": "coincident", "points": [
                {"geometry": "l1", "point": "end"},
                {"geometry": "c1", "point": "end"},
            ]}],
        )))
        self.assertEqual([p.code for p in problems], [P20])
        self.assertIn("centre", problems[0].message)

    def test_p20_every_wrong_handle_is_reported(self):
        for kind, geometry, handle in (
            (LINE, line("g", 0, 0, 10, 0), "centre"),
            (CIRCLE, circle("g", 0, 0, 5), "start"),
            (RECTANGLE, rectangle("g", 0, 0, 10, 10), "end"),
        ):
            with self.subTest(kind=kind, handle=handle):
                self.assertIn(P20, codes_of(plan(sketch(
                    geometry=[geometry, line("other", 0, 0, 1, 0)],
                    constraints=[{"id": "k1", "type": "coincident", "points": [
                        {"geometry": "g", "point": handle},
                        {"geometry": "other", "point": "start"},
                    ]}],
                ))))

    def test_p20_accepts_every_declared_handle(self):
        for geometry, handles in (
            (line("g", 0, 0, 10, 0), POINT_HANDLES[LINE]),
            (circle("g", 0, 0, 5), POINT_HANDLES[CIRCLE]),
            (rectangle("g", 0, 0, 10, 10), POINT_HANDLES[RECTANGLE]),
        ):
            for handle in handles:
                with self.subTest(handle=handle):
                    self.assertEqual(codes_of(plan(sketch(
                        geometry=[geometry, line("other", 0, 0, 1, 0)],
                        constraints=[{"id": "k1", "type": "coincident",
                                      "points": [
                            {"geometry": "g", "point": handle},
                            {"geometry": "other", "point": "start"},
                        ]}],
                    ))), [])

    def test_p21_a_radius_constraint_on_a_line(self):
        problems = problems_of(plan(sketch(
            geometry=[line("l1", 0, 0, 10, 0)],
            constraints=[{"id": "k1", "type": "radius",
                          "geometry": "l1", "value": 5}],
        )))
        self.assertIn(P21, [p.code for p in problems])

    def test_p21_a_length_constraint_on_a_circle(self):
        self.assertIn(P21, codes_of(plan(sketch(
            geometry=[circle("c1", 0, 0, 5)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "c1", "value": 5}],
        ))))

    def test_p21_horizontal_and_vertical_apply_to_lines_only(self):
        for kind in ("horizontal", "vertical"):
            for geometry in (circle("g", 0, 0, 5),
                             rectangle("g", 0, 0, 10, 10)):
                with self.subTest(kind=kind, geometry=geometry["type"]):
                    self.assertIn(P21, codes_of(plan(sketch(
                        geometry=[geometry],
                        constraints=[{"id": "k1", "type": kind,
                                      "geometry": "g"}],
                    ))))

    def test_p21_matches_the_declared_table_exactly(self):
        """Measured against `CONSTRAINT_APPLIES_TO` rather than retyped."""
        samples = {
            LINE: line("g", 0, 0, 10, 0),
            CIRCLE: circle("g", 0, 0, 5),
            RECTANGLE: rectangle("g", 0, 0, 10, 10),
        }
        for kind, allowed in CONSTRAINT_APPLIES_TO.items():
            for geometry_type, geometry in samples.items():
                constraint = {"id": "k1", "type": kind, "geometry": "g"}
                if kind in DIMENSIONAL_TYPES:
                    # A value that agrees, so only P21 can fire.
                    constraint["value"] = 10 if kind == "length" else 5
                codes = codes_of(plan(sketch(
                    geometry=[geometry], constraints=[constraint],
                )))
                with self.subTest(constraint=kind, geometry=geometry_type):
                    self.assertEqual(
                        P21 in codes, geometry_type not in allowed
                    )

    def test_p22_a_length_that_disagrees_is_a_conflict(self):
        problems = problems_of(plan(sketch(
            geometry=[line("l1", 0, 0, 50, 0)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": 80}],
        )))
        self.assertEqual([p.code for p in problems], [P22])
        self.assertIn("50", problems[0].message)
        self.assertIn("80", problems[0].message)

    def test_p22_a_radius_that_disagrees_is_a_conflict(self):
        self.assertIn(P22, codes_of(plan(sketch(
            geometry=[circle("c1", 0, 0, 5)],
            constraints=[{"id": "k1", "type": "radius",
                          "geometry": "c1", "value": 7}],
        ))))

    def test_p22_measures_a_diagonal_line_correctly(self):
        """A 3-4-5 line is 5 long, so a length of 5 agrees and 4 does not."""
        agreeing = plan(sketch(
            geometry=[line("l1", 0, 0, 3, 4)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": 5}],
        ))
        self.assertEqual(codes_of(agreeing), [])
        disagreeing = plan(sketch(
            geometry=[line("l1", 0, 0, 3, 4)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": 4}],
        ))
        self.assertIn(P22, codes_of(disagreeing))

    def test_p22_uses_a_tolerance_and_not_exact_equality(self):
        """A length computed from endpoints must not need bit equality.

        A unit diagonal is 1.4142135623730951 long, which nobody writes. A
        value rounded well inside the tolerance is accepted; exact equality
        would reject it. The project's rule -- never compare kernel or derived
        floats exactly -- applies to this validator too.
        """
        length = math.hypot(1.0, 1.0)
        written = 1.41421356237  # 12 significant figures
        self.assertNotEqual(length, written)
        self.assertLess(abs(length - written) / length, 1e-9)
        self.assertEqual(codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 1, 1)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": written}],
        ))), [])

    def test_p22_tolerance_is_not_a_licence_for_a_wrong_value(self):
        """1e-9 relative, and no looser: 8 significant figures is rejected."""
        coarse = 1.41421356  # 1.7e-9 relative -- just outside
        self.assertGreater(
            abs(math.hypot(1.0, 1.0) - coarse) / math.hypot(1.0, 1.0), 1e-9
        )
        self.assertIn(P22, codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 1, 1)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": coarse}],
        ))))

    def test_the_validator_never_alters_the_plan(self):
        """The proof that constraints are checked and not solved."""
        payload = plan(sketch(
            geometry=[line("l1", 0, 0, 50, 0)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": 80}],
        ))
        parsed = parse_plan(payload)
        before = json.dumps(operation_to_dict(parsed.operations[0]),
                            sort_keys=True)
        validate_plan(parsed)
        after = json.dumps(operation_to_dict(parsed.operations[0]),
                           sort_keys=True)
        self.assertEqual(before, after)
        # And the line is still 50 long, not 80.
        item = parsed.operations[0].definition.geometry[0]
        self.assertEqual(item.end.x - item.start.x, 50.0)

    def test_p4_a_non_positive_length_inside_a_sketch(self):
        for geometry in (
            circle("c1", 0, 0, 0),
            circle("c1", 0, 0, -5),
            rectangle("r1", 0, 0, 0, 10),
            rectangle("r1", 0, 0, 10, -1),
        ):
            with self.subTest(geometry=geometry):
                self.assertIn(P4, codes_of(plan(sketch(geometry=[geometry]))))

    def test_p4_a_degenerate_line_has_no_length(self):
        self.assertIn(P4, codes_of(plan(sketch(
            geometry=[line("l1", 5, 5, 5, 5)],
        ))))

    def test_p4_a_dimensional_constraint_value_must_be_positive(self):
        self.assertIn(P4, codes_of(plan(sketch(
            geometry=[line("l1", 0, 0, 50, 0)],
            constraints=[{"id": "k1", "type": "length",
                          "geometry": "l1", "value": 0}],
        ))))

    def test_a_negative_coordinate_is_fine(self):
        """A position may be negative; only a *size* may not."""
        self.assertEqual(codes_of(plan(sketch(
            geometry=[line("l1", -50, -30, -10, -30),
                      circle("c1", -5, -5, 2)],
        ))), [])


class ProfileIsNotASolidTests(unittest.TestCase):
    """P11, seen from the new side: a sketch id names no solid."""

    def test_a_fillet_cannot_target_a_sketch(self):
        problems = problems_of(plan(
            sketch(),
            {"id": "round", "type": "fillet", "target": "profile",
             "parameters": {"radius": 2, "edges": {"select": "all"}}},
        ))
        codes = [p.code for p in problems]
        self.assertIn(P11, codes)
        self.assertIn("sketch", problems[codes.index(P11)].message)

    def test_a_chamfer_cannot_target_a_sketch(self):
        self.assertIn(P11, codes_of(plan(
            sketch(),
            {"id": "bevel", "type": "chamfer", "target": "profile",
             "parameters": {"distance": 2, "edges": {"select": "all"}}},
        )))

    def test_a_through_hole_cannot_target_a_sketch(self):
        self.assertIn(P11, codes_of(plan(
            sketch(),
            {"id": "hole", "type": "through_hole", "target": "profile",
             "parameters": {"diameter": 8,
                            "position": {"x": 0, "y": 0, "z": 0}}},
        )))

    def test_a_subtract_cannot_target_a_sketch(self):
        self.assertIn(P11, codes_of(plan(
            sketch(),
            plate(),
            {"id": "cut", "type": "subtract", "target": "profile",
             "tools": ["plate"]},
        )))

    def test_a_sketch_cannot_be_a_subtract_tool(self):
        self.assertIn(P11, codes_of(plan(
            plate(),
            sketch(),
            {"id": "cut", "type": "subtract", "target": "plate",
             "tools": ["profile"]},
        )))

    def test_the_message_says_profile_rather_than_modifier(self):
        """The two P11 cases are different mistakes and read differently."""
        as_profile = [
            p for p in problems_of(plan(
                sketch(),
                {"id": "round", "type": "fillet", "target": "profile",
                 "parameters": {"radius": 2, "edges": {"select": "all"}}},
            )) if p.code == P11
        ][0]
        as_modifier = [
            p for p in problems_of(plan(
                plate(),
                {"id": "round", "type": "fillet", "target": "plate",
                 "parameters": {"radius": 2, "edges": {"select": "all"}}},
                {"id": "bevel", "type": "chamfer", "target": "round",
                 "parameters": {"distance": 1, "edges": {"select": "all"}}},
            )) if p.code == P11
        ][0]
        self.assertIn("profile", as_profile.message)
        self.assertNotEqual(as_profile.message, as_modifier.message)

    def test_a_sketch_may_appear_anywhere_a_reference_allows(self):
        """A profile constrains nothing but its own references.

        There is no plan rule saying a plan must *begin* constructively --
        that is S15/S9's business, on the V1 document, and the plan layer
        deliberately does not duplicate it. So a leading sketch is plan-valid,
        and both orders below are fine.
        """
        self.assertEqual(codes_of(plan(sketch(), plate())), [])
        self.assertEqual(codes_of(plan(plate(), sketch())), [])

    def test_a_forward_reference_to_a_sketch_is_still_a_forward_reference(self):
        """P10 is unchanged: a target must appear strictly earlier."""
        self.assertIn("P10", codes_of(plan(
            {"id": "round", "type": "fillet", "target": "profile",
             "parameters": {"radius": 2, "edges": {"select": "all"}}},
            sketch(),
        )))

    def test_a_sketch_cannot_reference_itself(self):
        """A sketch has no target at all, so this is a parse failure."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan({**sketch(), "target": "profile"}))


# --- 4. the execution boundary --------------------------------------------


class ExecutionUnsupportedTests(unittest.TestCase):
    """The explicit refusal, and that it is not any other refusal."""

    def parsed(self, *operations):
        return parse_plan(plan(*operations))

    def test_the_adapter_refuses_a_sketch_explicitly(self):
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(self.parsed(sketch()))
        self.assertEqual(raised.exception.operation_types, (SKETCH,))
        self.assertEqual(raised.exception.operation_ids, ("profile",))

    def test_the_message_does_not_blame_the_plan(self):
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(self.parsed(sketch()))
        message = str(raised.exception)
        self.assertIn("the plan is valid", message)
        self.assertIn("sketch", message)

    def test_it_is_an_adapter_error_so_no_caller_can_miss_it(self):
        """A caller that only catches `AdapterError` still stops."""
        self.assertTrue(issubclass(ExecutionUnsupported, AdapterError))
        with self.assertRaises(AdapterError):
            plan_to_document(self.parsed(sketch()))

    def test_a_whole_plan_is_refused_not_partially_translated(self):
        """A buildable box beside a sketch does not become a document."""
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(self.parsed(plate(), sketch()))
        self.assertEqual(raised.exception.operation_ids, ("profile",))

    def test_every_offending_operation_is_named(self):
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(self.parsed(
                plate(), sketch("a"), sketch("b"),
            ))
        self.assertEqual(raised.exception.operation_ids, ("a", "b"))
        # The types are de-duplicated; the ids are not.
        self.assertEqual(raised.exception.operation_types, (SKETCH,))

    def test_the_six_executable_operations_still_translate(self):
        """The boundary refuses a sketch and nothing else."""
        document = plan_to_document(self.parsed(
            plate(),
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 8,
                            "position": {"x": 10, "y": 10, "z": 0}}},
            {"id": "round", "type": "fillet", "target": "plate",
             "parameters": {"radius": 2,
                            "edges": {"select": "axis_parallel",
                                      "axis": "Z"}}},
        ))
        self.assertEqual(len(document["features"]), 3)

    def test_a_refusal_is_still_a_refusal_and_not_unsupported_execution(self):
        """The pre-existing errors are unchanged and not reclassified."""
        for refusal in (PlanStatus.UNSUPPORTED, PlanStatus.NEEDS_CLARIFICATION):
            empty = OperationPlan(status=refusal, operations=())
            with self.subTest(status=refusal):
                with self.assertRaises(AdapterError) as raised:
                    plan_to_document(empty)
                self.assertNotIsInstance(
                    raised.exception, ExecutionUnsupported
                )


class BuildBoundaryTests(unittest.TestCase):
    """What the build layer reports, and what it does not produce."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def build(self, *operations):
        return build_plan(self.service, parse_plan(plan(*operations)))

    def test_an_unsupported_plan_is_reported_as_such(self):
        result = self.build(sketch())
        self.assertTrue(result.execution_unsupported)
        self.assertEqual(result.unsupported_types, (SKETCH,))
        self.assertEqual(result.unsupported_ids, ("profile",))

    def test_nothing_is_built_and_no_document_is_produced(self):
        result = self.build(sketch())
        self.assertFalse(result.built)
        self.assertIsNone(result.document)
        self.assertIsNone(result.outcome)

    def test_built_and_unsupported_are_never_both_true(self):
        for operations in ((sketch(),), (plate(), sketch()), (plate(),)):
            result = self.build(*operations)
            with self.subTest(operations=len(operations)):
                self.assertFalse(result.built and result.execution_unsupported)

    def test_a_buildable_plan_is_not_marked_unsupported(self):
        result = self.build(plate())
        self.assertTrue(result.built)
        self.assertFalse(result.execution_unsupported)
        self.assertEqual(result.unsupported_types, ())

    def test_an_invalid_document_is_not_reported_as_unsupported(self):
        """Two constructive solids: rejected by S9, which is a different no."""
        result = self.build(plate("a"), plate("b"))
        self.assertFalse(result.built)
        self.assertFalse(result.execution_unsupported)

    def test_a_geometric_failure_is_not_reported_as_unsupported(self):
        """A hole that misses the material fails E1, not this."""
        result = self.build(
            plate(),
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 8,
                            "position": {"x": 500, "y": 500, "z": 0}}},
        )
        self.assertFalse(result.built)
        self.assertFalse(result.execution_unsupported)

    def test_the_error_string_is_present_for_a_reader(self):
        """Prose as well as a flag, so a log is readable -- but the flag is
        what a caller branches on."""
        result = self.build(sketch())
        self.assertIsNotNone(result.error)
        self.assertIn("sketch", result.error)


# --- 5. the fixtures -------------------------------------------------------


class SketchFixtureTests(unittest.TestCase):
    """The recorded cases, run for real."""

    def sketch_fixtures(self):
        return [
            name for name in lpp.FIXTURE_NAMES
            if lpp.FIXTURES[name].get("expects") == lpp.EXECUTION_UNSUPPORTED
        ]

    def test_there_are_unsupported_execution_fixtures(self):
        self.assertGreaterEqual(len(self.sketch_fixtures()), 3)

    def test_each_one_is_valid_and_refused(self):
        for name in self.sketch_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=True)
                self.assertTrue(result["plan_valid"], name)
                self.assertTrue(result["execution_unsupported"], name)
                self.assertEqual(
                    result["v1_conversion"], lpp.EXECUTION_UNSUPPORTED
                )
                self.assertIsNone(result["v1_document"])
                self.assertFalse(result["built"])
                self.assertTrue(result["rejected_for_the_right_reason"], name)

    def test_no_unsupported_fixture_reports_geometry(self):
        """Not a volume, not a bounding box, not a build key, not a mesh."""
        for name in self.sketch_fixtures():
            result = lpp.run_fixture(name, build=True)
            for key in ("volume_mm3", "bounding_box", "build_key",
                        "solid_count", "face_count", "render"):
                with self.subTest(fixture=name, key=key):
                    self.assertNotIn(key, result)

    def test_the_rejection_fixtures_are_refused_for_their_stated_rule(self):
        for name in lpp.FIXTURE_NAMES:
            entry = lpp.FIXTURES[name]
            if "sketch" not in name or entry.get("expects") != lpp.PLAN_REJECTED:
                continue
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=False)
                self.assertTrue(result["rejected"], name)
                self.assertTrue(
                    result["rejected_for_the_right_reason"], name
                )

    def test_every_new_rule_has_a_fixture(self):
        """A rule with no fixture is a rule nobody has exercised."""
        covered = {
            code
            for name in lpp.FIXTURE_NAMES
            for code in lpp.FIXTURES[name].get("expected_codes", ())
        }
        for code in (P18, P19, P20, P21, P22):
            self.assertIn(code, covered)

    def test_the_stated_expectation_is_not_the_default(self):
        """`EXECUTION_UNSUPPORTED` is its own value, not an alias."""
        self.assertNotIn(
            lpp.EXECUTION_UNSUPPORTED,
            (lpp.BUILDS, lpp.PLAN_REJECTED, lpp.BUILD_REJECTED),
        )

    def test_the_building_fixtures_still_all_build(self):
        """A sketch fixture must not have leaked into the building set."""
        for name in lpp.building_fixtures():
            self.assertNotIn(
                "sketch", name, f"{name} is expected to build"
            )

    def test_no_fixture_is_expected_to_build_a_sketch(self):
        for name in lpp.FIXTURE_NAMES:
            entry = lpp.FIXTURES[name]
            has_sketch = any(
                operation["type"] == "sketch"
                for operation in entry["plan"]["operations"]
            )
            if has_sketch:
                with self.subTest(fixture=name):
                    self.assertNotEqual(
                        entry.get("expects", lpp.BUILDS), lpp.BUILDS
                    )


# --- 6. what must not have happened ---------------------------------------


class NoSecondCadPathTests(unittest.TestCase):
    """The sketch foundation added no geometry, no solver and no execution."""

    def modules(self):
        for path in sorted(SOURCE.glob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_the_sketch_module_imports_no_kernel(self):
        tree = ast.parse(
            (SOURCE / "sketch.py").read_text(encoding="utf-8")
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("cadquery", "OCP", "cad_core", "numpy"):
            self.assertNotIn(forbidden, imported)

    def test_the_sketch_module_imports_nothing_but_the_standard_library(self):
        tree = ast.parse((SOURCE / "sketch.py").read_text(encoding="utf-8"))
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertEqual(modules, {"__future__", "dataclasses", "typing"})

    def test_no_module_uses_dynamic_attribute_lookup(self):
        """Re-asserted here because `_sketch` reads geometry fields."""
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) in ("getattr", "hasattr")
                    and len(node.args) >= 2
                    and not isinstance(node.args[1], ast.Constant)
                ):
                    self.fail(f"{path.name} looks up a computed attribute")

    def test_nothing_executes_generated_content(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None)
                    self.assertNotIn(
                        name,
                        ("eval", "exec", "compile", "__import__"),
                        f"{path.name} calls {name}",
                    )

    def test_no_solver_appeared(self):
        """No iteration toward a solution anywhere in the sketch layer."""
        text = (SOURCE / "sketch.py").read_text(encoding="utf-8")
        lowered = text.lower()
        for word in ("scipy", "newton", "jacobian", "residual", "solve("):
            self.assertNotIn(word, lowered)

    def test_the_module_still_documents_why_a_sketch_is_not_executed(self):
        """So the narrower checks above cannot be satisfied by deleting the
        explanation."""
        text = (SOURCE / "sketch.py").read_text(encoding="utf-8")
        self.assertIn("cannot be built", text)
        self.assertIn("No constraint solver", text)

    def test_cad_core_was_not_modified_for_sketches(self):
        """The V1 validator must still reject a sketch outright."""
        import cad_core.model as model

        self.assertFalse(
            any("sketch" in name.lower() for name in dir(model))
        )

    def test_the_v1_validator_still_rejects_a_sketch_feature(self):
        """Defence in depth: even a hand-built document does not get through."""
        service = CadApplicationService.local(tempfile.mkdtemp())
        verdict = service.validate_document({
            "schema_version": "1.0.0",
            "units": "mm",
            "name": "hand-built",
            "features": [
                {"id": "profile", "type": "sketch", "plane": "XY"},
            ],
        })
        self.assertFalse(verdict.valid)

    def test_the_adapter_has_no_sketch_geometry_of_its_own(self):
        """No bounding-rectangle substitution, no extrude-by-one-millimetre."""
        text = (SOURCE / "adapter.py").read_text(encoding="utf-8")
        self.assertNotIn('"sketch"', text.replace("'sketch'", '"sketch"'))


# --- 7. over HTTP ----------------------------------------------------------


class HttpBoundaryTests(unittest.TestCase):
    """That the transport reports an unexecutable plan as its own answer."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from cad_experimental.app import (
            BUILD_PATH,
            LOCAL_PLAN_PATH,
            VALIDATE_PATH,
            create_app,
        )
        from cad_experimental.config import ExperimentalConfig

        cls.BUILD_PATH = BUILD_PATH
        cls.VALIDATE_PATH = VALIDATE_PATH
        cls.LOCAL_PLAN_PATH = LOCAL_PLAN_PATH
        cls.client = TestClient(create_app(
            config=ExperimentalConfig(model="stub-model"),
            service=CadApplicationService.local(tempfile.mkdtemp()),
        ))

    def test_validate_accepts_a_sketch(self):
        """The plan is valid, and the validate route says so plainly."""
        response = self.client.post(
            self.VALIDATE_PATH, json={"plan": plan(sketch())}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["problems"], [])

    def test_build_answers_501_and_not_400(self):
        """400 would say the plan is wrong. It is not."""
        response = self.client.post(
            self.BUILD_PATH, json={"plan": plan(sketch())}
        )
        self.assertEqual(response.status_code, 501)

    def test_the_body_names_what_cannot_be_executed(self):
        body = self.client.post(
            self.BUILD_PATH, json={"plan": plan(sketch())}
        ).json()
        self.assertTrue(body["execution_unsupported"])
        self.assertEqual(body["unsupported_types"], ["sketch"])
        self.assertEqual(body["unsupported_operations"], ["profile"])

    def test_the_body_carries_no_document_and_no_render(self):
        body = self.client.post(
            self.BUILD_PATH, json={"plan": plan(sketch())}
        ).json()
        for absent in ("document", "render", "build"):
            self.assertNotIn(absent, body)

    def test_an_invalid_plan_still_answers_400(self):
        """The pre-existing status mapping is untouched."""
        response = self.client.post(self.BUILD_PATH, json={"plan": plan(
            sketch(geometry=[line("l1", 0, 0, 50, 0)],
                   constraints=[{"id": "k1", "type": "length",
                                 "geometry": "l1", "value": 80}]),
        )})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            [p["code"] for p in response.json()["problems"]], [P22]
        )

    def test_a_buildable_plan_still_answers_200(self):
        response = self.client.post(
            self.BUILD_PATH, json={"plan": plan(plate())}
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("execution_unsupported", response.json())

    def test_the_local_route_reports_the_flag_on_a_200(self):
        """The fixture ran; it just had nowhere to go. Flag, not status."""
        response = self.client.post(
            self.LOCAL_PLAN_PATH,
            json={"fixture": "sketch-rectangle-profile", "build": True},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["plan_valid"])
        self.assertTrue(body["execution_unsupported"])
        self.assertFalse(body["built"])
        self.assertIsNone(body["v1_document"])
        self.assertNotIn("render", body)
        self.assertFalse(body["is_live_model_result"])

    def test_the_local_route_still_builds_a_buildable_fixture(self):
        body = self.client.post(
            self.LOCAL_PLAN_PATH,
            json={"fixture": "box-100x60x10", "build": True},
        ).json()
        self.assertTrue(body["built"])
        self.assertFalse(body["execution_unsupported"])


# --- 8. the prompt ---------------------------------------------------------


class SketchPromptTests(unittest.TestCase):
    """What the model is told, and what it is not promised."""

    @classmethod
    def setUpClass(cls):
        from cad_experimental.prompt import (
            PROMPT_VERSION,
            prompt_fingerprint,
            system_prompt,
        )

        cls.text = system_prompt()
        cls.version = PROMPT_VERSION
        cls.fingerprint = prompt_fingerprint()

    def test_the_sketch_section_exists(self):
        self.assertIn("## sketch", self.text)

    def test_the_prompt_says_a_sketch_cannot_be_built(self):
        self.assertIn("cannot be built", self.text)

    def test_the_prompt_says_constraints_are_not_solved(self):
        self.assertIn("CHECKED, NOT SOLVED", self.text)

    def test_the_prompt_no_longer_calls_a_sketch_unsupported(self):
        """The language has sketches now; saying otherwise would be false."""
        after = self.text.split("# When to say unsupported", 1)[1]
        self.assertNotIn("sketches", after[:800])

    def test_the_sweeps_beyond_this_stage_are_still_unsupported(self):
        """Was `test_extrusions_and_revolves_are_still_unsupported`.

        Extrude and revolve left the unsupported list at Stage 38 when they
        were built. The sweeps that remain unbuilt are still named.
        """
        after = self.text.split("# When to say unsupported", 1)[1]
        self.assertIn("sweeps", after)
        self.assertIn("lofts", after)
        self.assertIn("patterns", after)

    def test_every_plane_and_geometry_type_is_documented(self):
        for plane in PLANES:
            self.assertIn(f'"{plane}"', self.text)
        for kind in GEOMETRY_TYPES:
            self.assertIn(f'"{kind}"', self.text)
        for kind in CONSTRAINT_TYPES:
            self.assertIn(f'"{kind}"', self.text)

    def test_every_point_handle_is_documented(self):
        for handles in POINT_HANDLES.values():
            for handle in handles:
                self.assertIn(handle, self.text)

    def test_the_prompt_contains_no_unexpanded_placeholder(self):
        self.assertNotIn("{PlanStatus", self.text)
        self.assertNotIn("{'", self.text)

    def test_the_version_and_fingerprint_are_well_formed(self):
        """The exact version belongs to whichever stage last changed the
        prompt -- Stage 38's module pins it. Here: that both exist."""
        self.assertRegex(self.version, r"^\d{4}-\d{2}-\d{2}\.\d+$")
        self.assertEqual(len(self.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
