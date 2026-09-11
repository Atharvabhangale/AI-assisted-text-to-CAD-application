"""Stage 38: extrude and revolve -- the first solids made from a profile.

These two operations are the reason the sketch foundation exists, and they
are the first references in this language that point at a *profile* rather
than a solid. That inverts rule P11 into P23, and it adds two rules that come
straight out of a plane's own definition: an extrusion runs along the plane's
normal (P24) and a revolution turns about an axis the plane spans (P25).

Like a sketch, neither can be built, and the Stage 37 boundary needed no
change to say so -- a fact these tests assert rather than assume.

What is deliberately NOT here: any check for a profile crossing its own axis
of revolution. That is the kernel's kind of judgement, there is no engine for
this operation, and guessing at it would be inventing geometric semantics. A
test below pins it as a known gap so it cannot be quietly forgotten.
"""

from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

from cad_core.application_service import CadApplicationService

from cad_experimental import local_plan_provider as lpp
from cad_experimental.adapter import ExecutionUnsupported, plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.parser import PlanParseError, parse_plan, parse_plan_text
from cad_experimental.plan import (
    AXES,
    CONSTRUCTIVE_TYPES,
    CONSUMING_TYPES,
    EXECUTABLE_TYPES,
    EXTRUDE,
    FULL_TURN,
    MODIFIER_TYPES,
    OPERATION_TYPES,
    PROFILE_SOLID_TYPES,
    PROFILE_TYPES,
    REVOLVE,
    SOLID_DECLARING_TYPES,
    TARGETED_TYPES,
    ExtrudeOperation,
    RevolveOperation,
    declares_solid,
    is_consuming,
    is_profile_solid,
    operation_to_dict,
    plan_schema,
)
from cad_experimental.sketch import PLANE_AXES, PLANE_NORMAL, PLANES
from cad_experimental.validation import (
    P5,
    P9,
    P10,
    P11,
    P12,
    P23,
    P24,
    P25,
    P26,
    RULE_CODES,
    validate_plan,
)

SOURCE = pathlib.Path(lpp.__file__).resolve().parent

_MISSING = object()


# --- builders --------------------------------------------------------------


def rectangle(identifier="r1", x=0, y=0, width=100, height=60):
    return {"id": identifier, "type": "rectangle",
            "corner": {"x": x, "y": y}, "width": width, "height": height}


def sketch(identifier="profile", plane="XY", geometry=None):
    return {
        "id": identifier, "type": "sketch",
        "parameters": {
            "plane": plane,
            "geometry": [rectangle()] if geometry is None else geometry,
        },
    }


def extrude(identifier="body", target="profile", distance=10,
            direction=_MISSING):
    parameters = {"distance": distance}
    if direction is not _MISSING:
        parameters["direction"] = direction
    return {"id": identifier, "type": "extrude", "target": target,
            "parameters": parameters}


def revolve(identifier="body", target="profile", angle=360, axis="+X"):
    return {"id": identifier, "type": "revolve", "target": target,
            "parameters": {"angle": angle, "axis": axis}}


def plate(identifier="plate"):
    return {"id": identifier, "type": "box",
            "parameters": {"x": 100, "y": 60, "z": 10}}


def fillet(identifier="round", target="body", radius=2):
    return {"id": identifier, "type": "fillet", "target": target,
            "parameters": {"radius": radius,
                           "edges": {"select": "axis_parallel", "axis": "Z"}}}


def plan(*operations, status="generated"):
    return {"status": status, "summary": "a profile solid",
            "operations": list(operations)}


def problems_of(payload):
    return validate_plan(parse_plan(payload)).problems


def codes_of(payload):
    return [problem.code for problem in problems_of(payload)]


# --- 1. the vocabulary -----------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_both_operations_exist(self):
        self.assertIn(EXTRUDE, OPERATION_TYPES)
        self.assertIn(REVOLVE, OPERATION_TYPES)

    def test_they_form_their_own_category(self):
        """Not constructive, not modifiers, not profiles: a fourth kind."""
        self.assertEqual(PROFILE_SOLID_TYPES, (EXTRUDE, REVOLVE))
        for kind in PROFILE_SOLID_TYPES:
            self.assertNotIn(kind, CONSTRUCTIVE_TYPES)
            self.assertNotIn(kind, MODIFIER_TYPES)
            self.assertNotIn(kind, PROFILE_TYPES)

    def test_every_type_is_in_exactly_one_category(self):
        for kind in OPERATION_TYPES:
            categories = [
                kind in CONSTRUCTIVE_TYPES,
                kind in MODIFIER_TYPES,
                kind in PROFILE_TYPES,
                kind in PROFILE_SOLID_TYPES,
            ]
            with self.subTest(kind=kind):
                self.assertEqual(sum(categories), 1)

    def test_their_own_ids_name_solids(self):
        """The reason a fillet can target an extrusion."""
        self.assertEqual(
            SOLID_DECLARING_TYPES, CONSTRUCTIVE_TYPES + PROFILE_SOLID_TYPES
        )
        for kind in PROFILE_SOLID_TYPES:
            self.assertIn(kind, SOLID_DECLARING_TYPES)

    def test_neither_consumes_its_profile(self):
        """Unlike a subtract's tools. Extruding a sketch does not destroy it."""
        for kind in PROFILE_SOLID_TYPES:
            self.assertNotIn(kind, CONSUMING_TYPES)
        self.assertEqual(CONSUMING_TYPES, ("subtract",))

    def test_neither_is_executable(self):
        for kind in PROFILE_SOLID_TYPES:
            self.assertNotIn(kind, EXECUTABLE_TYPES)

    def test_the_gap_between_language_and_engine_is_now_three(self):
        self.assertEqual(
            set(OPERATION_TYPES) - set(EXECUTABLE_TYPES),
            {"sketch", EXTRUDE, REVOLVE},
        )

    def test_both_carry_a_target(self):
        self.assertEqual(
            set(TARGETED_TYPES),
            set(MODIFIER_TYPES) | set(PROFILE_SOLID_TYPES),
        )

    def test_the_predicates_agree(self):
        operations = parse_plan(plan(
            sketch(), extrude(), revolve("turned", angle=90, axis="+X"),
        )).operations
        self.assertFalse(is_profile_solid(operations[0]))
        self.assertTrue(is_profile_solid(operations[1]))
        self.assertTrue(is_profile_solid(operations[2]))
        self.assertFalse(declares_solid(operations[0]))
        self.assertTrue(declares_solid(operations[1]))
        for operation in operations:
            self.assertFalse(is_consuming(operation))

    def test_a_full_turn_is_360_degrees(self):
        self.assertEqual(FULL_TURN, 360.0)

    def test_the_new_rule_codes_are_registered(self):
        for code in (P23, P24, P25, P26):
            self.assertIn(code, RULE_CODES)

    def test_the_plane_tables_are_consistent_with_the_plane_names(self):
        """Derived facts, checked against the names rather than retyped."""
        for plane in PLANES:
            with self.subTest(plane=plane):
                self.assertEqual(set(PLANE_AXES[plane]), set(plane))
                self.assertNotIn(PLANE_NORMAL[plane], PLANE_AXES[plane])
                self.assertEqual(
                    set(PLANE_AXES[plane]) | {PLANE_NORMAL[plane]},
                    {"X", "Y", "Z"},
                )

    def branch(self, kind):
        return next(
            b for b in
            plan_schema()["properties"]["operations"]["items"]["anyOf"]
            if b["properties"]["type"]["const"] == kind
        )

    def test_the_schema_lists_both_and_their_parameters(self):
        """Stage 41: one branch each, with their own required parameters."""
        extrude = self.branch(EXTRUDE)["properties"]["parameters"]
        self.assertEqual(set(extrude["required"]), {"distance"})
        self.assertIn("direction", extrude["properties"])

        revolve = self.branch(REVOLVE)["properties"]["parameters"]
        self.assertEqual(set(revolve["required"]), {"angle", "axis"})

    def test_the_angle_bound_moved_to_the_validator(self):
        """`maximum` left the schema at Stage 41 -- the structured-output API
        rejects it -- and P26 still enforces (0, 360]. Nothing was weakened."""
        parameters = self.branch(REVOLVE)["properties"]["parameters"]
        self.assertNotIn("maximum", parameters["properties"]["angle"])
        for bad in (0, -90, FULL_TURN + 1):
            with self.subTest(angle=bad):
                self.assertIn(P26, codes_of(plan(
                    sketch("profile", "XZ"), revolve(angle=bad),
                )))
        self.assertEqual(codes_of(plan(
            sketch("profile", "XZ"), revolve(angle=FULL_TURN),
        )), [])

    def test_no_further_operation_crept_in(self):
        for absent in ("sweep", "loft", "pattern", "mirror", "union",
                       "intersect", "assembly", "shell", "rib", "thread"):
            self.assertNotIn(absent, OPERATION_TYPES)


# --- 2. parsing ------------------------------------------------------------


class ParseTests(unittest.TestCase):
    def test_an_extrude_parses(self):
        operation = parse_plan(plan(sketch(), extrude())).operations[1]
        self.assertIsInstance(operation, ExtrudeOperation)
        self.assertEqual(operation.target, "profile")
        self.assertEqual(operation.distance, 10.0)
        self.assertIsNone(operation.direction)

    def test_an_explicit_direction_parses(self):
        operation = parse_plan(
            plan(sketch(), extrude(direction="-Z"))
        ).operations[1]
        self.assertEqual(operation.direction, "-Z")

    def test_a_revolve_parses(self):
        operation = parse_plan(
            plan(sketch(), revolve(angle=90, axis="-Y"))
        ).operations[1]
        self.assertIsInstance(operation, RevolveOperation)
        self.assertEqual(operation.angle, 90.0)
        self.assertEqual(operation.axis, "-Y")

    def test_an_extrude_needs_a_target(self):
        with self.assertRaises(PlanParseError) as raised:
            parse_plan(plan(sketch(), {
                "id": "body", "type": "extrude",
                "parameters": {"distance": 10},
            }))
        self.assertIn("profile", str(raised.exception.detail))

    def test_an_extrude_needs_a_distance(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(), {
                "id": "body", "type": "extrude", "target": "profile",
                "parameters": {},
            }))

    def test_a_revolve_needs_both_an_angle_and_an_axis(self):
        for parameters in ({"angle": 90}, {"axis": "+X"}, {}):
            with self.subTest(parameters=parameters):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(), {
                        "id": "body", "type": "revolve", "target": "profile",
                        "parameters": parameters,
                    }))

    def test_a_revolve_axis_may_not_be_null(self):
        """Required means required: an explicit null is not an omission."""
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(), {
                "id": "body", "type": "revolve", "target": "profile",
                "parameters": {"angle": 90, "axis": None},
            }))

    def test_an_unsigned_axis_is_rejected(self):
        """A revolve axis is signed, unlike an edge selector's (Section C.7)."""
        for axis in ("X", "Y", "Z", "x", "+x"):
            with self.subTest(axis=axis):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(), revolve(axis=axis)))

    def test_an_unsigned_direction_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(), extrude(direction="Z")))

    def test_an_arbitrary_direction_vector_is_rejected(self):
        for direction in ([0, 0, 1], {"x": 0, "y": 0, "z": 1}, "0,0,1"):
            with self.subTest(direction=direction):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(), extrude(direction=direction)))

    def test_a_numeric_string_is_never_coerced(self):
        for payload in (
            plan(sketch(), extrude(distance="10")),
            plan(sketch(), revolve(angle="360")),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan(payload)

    def test_a_boolean_is_not_a_number(self):
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(), extrude(distance=True)))

    def test_nan_and_infinity_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            text = json.dumps(plan(sketch(), extrude())).replace(
                '"distance": 10', f'"distance": {literal}'
            )
            with self.subTest(literal=literal):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)

    def test_an_unknown_parameter_is_rejected(self):
        for payload in (
            plan(sketch(), {"id": "b", "type": "extrude", "target": "profile",
                            "parameters": {"distance": 10, "angle": 90}}),
            plan(sketch(), {"id": "b", "type": "revolve", "target": "profile",
                            "parameters": {"angle": 90, "axis": "+X",
                                           "distance": 10}}),
            plan(sketch(), {"id": "b", "type": "extrude", "target": "profile",
                            "parameters": {"distance": 10, "taper": 5}}),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan(payload)

    def test_neither_carries_tools_or_a_position(self):
        for extra in ({"tools": ["profile"]},):
            payload = plan(sketch(), {**extrude(), **extra})
            with self.subTest(extra=extra):
                with self.assertRaises(PlanParseError):
                    parse_plan(payload)
        with self.assertRaises(PlanParseError):
            parse_plan(plan(sketch(), {
                "id": "b", "type": "extrude", "target": "profile",
                "parameters": {"distance": 10,
                               "position": {"x": 0, "y": 0, "z": 0}},
            }))

    def test_a_refusal_may_not_carry_either(self):
        for status in ("unsupported", "needs_clarification"):
            with self.subTest(status=status):
                with self.assertRaises(PlanParseError):
                    parse_plan(plan(sketch(), extrude(), status=status))

    def test_both_round_trip_through_their_own_dicts(self):
        payload = plan(
            sketch("a", "XZ"),
            extrude("thin", "a", 5, "+Y"),
            extrude("thick", "a", 40),
            revolve("turned", "a", 90, "+Z"),
        )
        once = parse_plan(payload)
        twice = parse_plan({
            "status": "generated", "summary": "s",
            "operations": [operation_to_dict(o) for o in once.operations],
        })
        self.assertEqual(
            [operation_to_dict(o) for o in once.operations],
            [operation_to_dict(o) for o in twice.operations],
        )

    def test_an_absent_direction_stays_absent_after_a_round_trip(self):
        """The parser does not fill in the default -- the validator knows it."""
        payload = operation_to_dict(
            parse_plan(plan(sketch(), extrude())).operations[1]
        )
        self.assertNotIn("direction", payload["parameters"])


# --- 3. the reference rule (P23) ------------------------------------------


class ProfileReferenceTests(unittest.TestCase):
    """P23: the mirror of P11. The target must be a sketch."""

    def test_an_extrude_of_a_sketch_is_valid(self):
        self.assertEqual(codes_of(plan(sketch(), extrude())), [])

    def test_a_revolve_of_a_sketch_is_valid(self):
        self.assertEqual(
            codes_of(plan(sketch("profile", "XZ"), revolve(axis="+Z"))), []
        )

    def test_a_solid_target_is_p23(self):
        problems = problems_of(plan(plate(), extrude(target="plate")))
        self.assertEqual([p.code for p in problems], [P23])
        self.assertIn("is a solid", problems[0].message)
        self.assertIn("Target a sketch", problems[0].message)

    def test_a_modifier_target_is_p23_with_a_different_reason(self):
        problems = [
            p for p in problems_of(plan(
                plate(), fillet("round", "plate"),
                revolve(target="round", angle=90, axis="+X"),
            )) if p.code == P23
        ]
        self.assertEqual(len(problems), 1)
        self.assertIn("modifier", problems[0].message)

    def test_the_two_categories_get_different_messages(self):
        as_solid = [p for p in problems_of(plan(plate(), extrude(
            target="plate"))) if p.code == P23][0]
        as_modifier = [p for p in problems_of(plan(
            plate(), fillet("round", "plate"), extrude(target="round"),
        )) if p.code == P23][0]
        self.assertNotEqual(as_solid.message, as_modifier.message)

    def test_p11_and_p23_are_opposite_and_both_fire(self):
        """A modifier on a profile is P11; a profile-solid on a solid is P23."""
        self.assertIn(P11, codes_of(plan(sketch(), fillet(target="profile"))))
        self.assertIn(P23, codes_of(plan(plate(), extrude(target="plate"))))

    def test_a_missing_target_is_p9(self):
        problems = problems_of(plan(sketch(), extrude(target="nope")))
        self.assertEqual([p.code for p in problems], [P9])

    def test_a_self_reference_is_p10(self):
        self.assertIn(P10, codes_of(plan(sketch(), extrude("body", "body"))))

    def test_a_forward_reference_is_p10(self):
        problems = problems_of(plan(extrude(), sketch()))
        self.assertEqual([p.code for p in problems], [P10])
        self.assertIn("strictly", problems[0].message)

    def test_a_consumed_target_is_p12_and_not_p23(self):
        """The more specific fact wins: it *was* a solid, and is gone."""
        problems = problems_of(plan(
            plate(),
            {"id": "tool", "type": "cylinder",
             "parameters": {"diameter": 20, "height": 10}},
            {"id": "cut", "type": "subtract", "target": "plate",
             "tools": ["tool"]},
            extrude(target="tool"),
        ))
        codes = [p.code for p in problems]
        self.assertIn(P12, codes)
        self.assertNotIn(P23, codes)

    def test_an_extruded_solid_can_be_modified(self):
        """The extrusion's own id names a solid, so a fillet may target it."""
        self.assertEqual(codes_of(plan(sketch(), extrude(), fillet())), [])

    def test_an_extruded_solid_can_be_drilled(self):
        self.assertEqual(codes_of(plan(sketch(), extrude(), {
            "id": "hole", "type": "through_hole", "target": "body",
            "parameters": {"diameter": 8,
                           "position": {"x": 10, "y": 10, "z": 0}},
        })), [])

    def test_an_extruded_solid_can_be_a_subtract_tool(self):
        self.assertEqual(codes_of(plan(
            plate(), sketch(), extrude(),
            {"id": "cut", "type": "subtract", "target": "plate",
             "tools": ["body"]},
        )), [])

    def test_one_profile_may_be_extruded_twice(self):
        """It is not consumed. This is the difference from a subtract's tools."""
        self.assertEqual(codes_of(plan(
            sketch(), extrude("thin", "profile", 5),
            extrude("thick", "profile", 40),
        )), [])

    def test_a_profile_may_be_both_extruded_and_revolved(self):
        self.assertEqual(codes_of(plan(
            sketch("profile", "XZ"),
            extrude("swept", "profile", 5, "+Y"),
            revolve("turned", "profile", 180, "+Z"),
        )), [])

    def test_an_extrusion_cannot_be_extruded(self):
        """It is a solid now, not a profile."""
        self.assertIn(P23, codes_of(plan(
            sketch(), extrude(), extrude("again", "body", 5),
        )))


# --- 4. plane compatibility (P24, P25) ------------------------------------


class DirectionTests(unittest.TestCase):
    """P24: an extrusion runs along its plane's normal, and along no other."""

    def test_the_positive_normal_is_valid_on_every_plane(self):
        for plane in PLANES:
            direction = f"+{PLANE_NORMAL[plane]}"
            with self.subTest(plane=plane, direction=direction):
                self.assertEqual(codes_of(plan(
                    sketch("profile", plane), extrude(direction=direction),
                )), [])

    def test_the_negative_normal_is_valid_on_every_plane(self):
        for plane in PLANES:
            direction = f"-{PLANE_NORMAL[plane]}"
            with self.subTest(plane=plane, direction=direction):
                self.assertEqual(codes_of(plan(
                    sketch("profile", plane), extrude(direction=direction),
                )), [])

    def test_an_in_plane_direction_is_p24_on_every_plane(self):
        for plane in PLANES:
            for axis in PLANE_AXES[plane]:
                for sign in ("+", "-"):
                    direction = f"{sign}{axis}"
                    with self.subTest(plane=plane, direction=direction):
                        self.assertIn(P24, codes_of(plan(
                            sketch("profile", plane),
                            extrude(direction=direction),
                        )))

    def test_the_rule_is_exactly_the_plane_normal(self):
        """Measured against the table, so neither can drift from the other."""
        for plane in PLANES:
            for axis in ("X", "Y", "Z"):
                for sign in ("+", "-"):
                    direction = f"{sign}{axis}"
                    codes = codes_of(plan(
                        sketch("profile", plane), extrude(direction=direction),
                    ))
                    with self.subTest(plane=plane, direction=direction):
                        self.assertEqual(
                            P24 in codes, axis != PLANE_NORMAL[plane]
                        )

    def test_the_message_names_both_admissible_directions(self):
        problems = [p for p in problems_of(plan(
            sketch("profile", "XZ"), extrude(direction="+X"),
        )) if p.code == P24]
        self.assertIn("+Y", problems[0].message)
        self.assertIn("-Y", problems[0].message)
        self.assertIn("XZ", problems[0].message)

    def test_an_omitted_direction_is_never_p24(self):
        """The default is the plane's positive normal, which always agrees."""
        for plane in PLANES:
            with self.subTest(plane=plane):
                self.assertEqual(
                    codes_of(plan(sketch("profile", plane), extrude())), []
                )

    def test_the_direction_is_checked_against_the_named_sketch(self):
        """Not against the first sketch, or a default -- the one referenced."""
        payload = plan(
            sketch("flat", "XY"), sketch("upright", "XZ"),
            extrude("body", "upright", 10, "+Y"),
        )
        self.assertEqual(codes_of(payload), [])
        wrong = plan(
            sketch("flat", "XY"), sketch("upright", "XZ"),
            extrude("body", "upright", 10, "+Z"),
        )
        self.assertIn(P24, codes_of(wrong))


class RevolveAxisTests(unittest.TestCase):
    """P25: a revolution turns about an axis its plane spans."""

    def test_every_in_plane_axis_is_valid(self):
        for plane in PLANES:
            for axis in PLANE_AXES[plane]:
                for sign in ("+", "-"):
                    signed = f"{sign}{axis}"
                    with self.subTest(plane=plane, axis=signed):
                        self.assertEqual(codes_of(plan(
                            sketch("profile", plane), revolve(axis=signed),
                        )), [])

    def test_the_plane_normal_is_p25(self):
        """Revolving a profile about its own normal sweeps nothing."""
        for plane in PLANES:
            for sign in ("+", "-"):
                axis = f"{sign}{PLANE_NORMAL[plane]}"
                with self.subTest(plane=plane, axis=axis):
                    self.assertIn(P25, codes_of(plan(
                        sketch("profile", plane), revolve(axis=axis),
                    )))

    def test_the_rule_is_exactly_the_in_plane_pair(self):
        for plane in PLANES:
            for axis in ("X", "Y", "Z"):
                for sign in ("+", "-"):
                    signed = f"{sign}{axis}"
                    codes = codes_of(plan(
                        sketch("profile", plane), revolve(axis=signed),
                    ))
                    with self.subTest(plane=plane, axis=signed):
                        self.assertEqual(
                            P25 in codes, axis not in PLANE_AXES[plane]
                        )

    def test_p24_and_p25_are_complementary(self):
        """Exactly the axes an extrusion may use are the ones a revolve may
        not, and vice versa -- which is what "normal" and "in-plane" mean."""
        for plane in PLANES:
            for axis in ("X", "Y", "Z"):
                signed = f"+{axis}"
                extrude_ok = P24 not in codes_of(plan(
                    sketch("profile", plane), extrude(direction=signed)))
                revolve_ok = P25 not in codes_of(plan(
                    sketch("profile", plane), revolve(axis=signed)))
                with self.subTest(plane=plane, axis=signed):
                    self.assertNotEqual(extrude_ok, revolve_ok)

    def test_the_message_names_the_admissible_axes(self):
        problems = [p for p in problems_of(plan(
            sketch("profile", "XY"), revolve(axis="+Z"),
        )) if p.code == P25]
        for expected in ("+X", "-X", "+Y", "-Y", "XY"):
            self.assertIn(expected, problems[0].message)

    def test_an_axis_outside_the_six_is_p5_not_p25(self):
        """Built in code, since the parser rejects it: a different mistake."""
        from cad_experimental.plan import OperationPlan, PlanStatus, \
            RevolveOperation as R, SketchOperation
        from cad_experimental.sketch import Point2D, Rectangle, \
            SketchDefinition

        built = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                SketchOperation(
                    id="profile",
                    definition=SketchDefinition(
                        plane="XY",
                        geometry=(Rectangle("r1", Point2D(0, 0), 100, 60),),
                        constraints=(),
                    ),
                ),
                R(id="body", target="profile", angle=90.0, axis="diagonal"),
            ),
        )
        codes = [p.code for p in validate_plan(built).problems]
        self.assertIn(P5, codes)


class AngleTests(unittest.TestCase):
    """P26: a sweep in (0, 360] degrees."""

    def test_a_full_turn_is_valid(self):
        self.assertEqual(
            codes_of(plan(sketch("profile", "XZ"), revolve(angle=360))), []
        )

    def test_ordinary_angles_are_valid(self):
        for angle in (0.5, 1, 45, 90, 180, 270, 359.999, 360):
            with self.subTest(angle=angle):
                self.assertEqual(codes_of(plan(
                    sketch("profile", "XZ"), revolve(angle=angle),
                )), [])

    def test_zero_is_p26(self):
        problems = [p for p in problems_of(plan(
            sketch("profile", "XZ"), revolve(angle=0),
        )) if p.code == P26]
        self.assertIn("sweeps nothing", problems[0].message)

    def test_a_negative_angle_is_p26(self):
        self.assertIn(P26, codes_of(plan(
            sketch("profile", "XZ"), revolve(angle=-90),
        )))

    def test_more_than_a_full_turn_is_p26(self):
        for angle in (360.001, 361, 540, 720):
            with self.subTest(angle=angle):
                self.assertIn(P26, codes_of(plan(
                    sketch("profile", "XZ"), revolve(angle=angle),
                )))

    def test_the_boundary_is_inclusive_at_360_and_exclusive_at_0(self):
        self.assertEqual(
            codes_of(plan(sketch("profile", "XZ"), revolve(angle=FULL_TURN))),
            [],
        )
        self.assertIn(P26, codes_of(plan(
            sketch("profile", "XZ"), revolve(angle=0.0),
        )))

    def test_a_non_finite_angle_is_p26(self):
        """Built in code: the parser rejects NaN before this is reachable."""
        from cad_experimental.plan import OperationPlan, PlanStatus, \
            RevolveOperation as R, SketchOperation
        from cad_experimental.sketch import Point2D, Rectangle, \
            SketchDefinition

        for angle in (float("nan"), float("inf")):
            built = OperationPlan(
                status=PlanStatus.GENERATED,
                operations=(
                    SketchOperation(
                        id="profile",
                        definition=SketchDefinition(
                            plane="XZ",
                            geometry=(Rectangle("r1", Point2D(10, 0), 20, 50),),
                            constraints=(),
                        ),
                    ),
                    R(id="body", target="profile", angle=angle, axis="+Z"),
                ),
            )
            with self.subTest(angle=angle):
                self.assertIn(
                    P26, [p.code for p in validate_plan(built).problems]
                )


class KnownGapTests(unittest.TestCase):
    """What this layer does NOT decide, recorded so it stays visible."""

    def test_a_profile_crossing_its_axis_is_not_rejected(self):
        """A rectangle spanning x = -20..20 revolved about +Z straddles the
        axis, which would self-intersect. Nothing here decides that: it needs
        the resolved 2D geometry measured against the axis line, which is the
        kernel's kind of judgement (E1-E5), and there is no engine for this
        operation. Accepted as plan-valid, and it still cannot be built.
        """
        codes = codes_of(plan(
            sketch("profile", "XZ",
                   [rectangle("r1", -20, 0, 40, 50)]),
            revolve(axis="+Z"),
        ))
        self.assertEqual(codes, [])

    def test_the_gap_is_documented_where_the_record_belongs(self):
        """So the test above cannot be read as an oversight."""
        text = (SOURCE / "plan.py").read_text(encoding="utf-8")
        self.assertIn("crosses the axis", text)
        self.assertIn("Not checked here", text)


# --- 5. the execution boundary --------------------------------------------


class ExecutionBoundaryTests(unittest.TestCase):
    """The Stage 37 boundary, unchanged, now covering two more operations."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def build(self, *operations):
        return build_plan(self.service, parse_plan(plan(*operations)))

    def test_an_extrude_is_refused_explicitly(self):
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(parse_plan(plan(sketch(), extrude())))
        self.assertEqual(
            raised.exception.operation_types, ("sketch", EXTRUDE)
        )
        self.assertEqual(raised.exception.operation_ids, ("profile", "body"))

    def test_a_revolve_is_refused_explicitly(self):
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(parse_plan(plan(
                sketch("profile", "XZ"), revolve(axis="+Z"),
            )))
        self.assertEqual(
            raised.exception.operation_types, ("sketch", REVOLVE)
        )

    def test_the_executable_operations_in_the_plan_are_not_blamed(self):
        """A fillet on an extrusion is buildable in principle; the extrude is
        what this backend cannot do, and only it is named."""
        with self.assertRaises(ExecutionUnsupported) as raised:
            plan_to_document(parse_plan(plan(sketch(), extrude(), fillet())))
        self.assertNotIn("fillet", raised.exception.operation_types)
        self.assertNotIn("round", raised.exception.operation_ids)

    def test_nothing_is_built_and_no_document_appears(self):
        for operations in (
            (sketch(), extrude()),
            (sketch("profile", "XZ"), revolve(axis="+Z")),
            (sketch(), extrude(), fillet()),
        ):
            result = self.build(*operations)
            with self.subTest(operations=operations[-1]["type"]):
                self.assertTrue(result.execution_unsupported)
                self.assertFalse(result.built)
                self.assertIsNone(result.document)
                self.assertIsNone(result.outcome)

    def test_the_offending_types_are_reported(self):
        result = self.build(sketch(), extrude())
        self.assertEqual(result.unsupported_types, ("sketch", EXTRUDE))
        self.assertEqual(result.unsupported_ids, ("profile", "body"))

    def test_a_buildable_plan_is_still_built(self):
        result = self.build(plate())
        self.assertTrue(result.built)
        self.assertFalse(result.execution_unsupported)

    def test_the_boundary_needed_no_new_list(self):
        """It is derived from EXECUTABLE_TYPES, so a new operation is refused
        by default rather than by remembering to add it somewhere."""
        text = (SOURCE / "adapter.py").read_text(encoding="utf-8")
        self.assertIn("EXECUTABLE_TYPES", text)
        for kind in PROFILE_SOLID_TYPES:
            self.assertNotIn(f'"{kind}"', text)
            self.assertNotIn(f"'{kind}'", text)

    def test_no_approximation_appeared_anywhere(self):
        """No extrude-as-a-box, no revolve-as-a-cylinder."""
        for module in ("adapter.py", "build.py"):
            text = (SOURCE / module).read_text(encoding="utf-8")
            lowered = text.lower()
            for word in ("approximate", "fallback", "substitute", "as_box"):
                self.assertNotIn(f"{word} ", lowered.replace("_", " "))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from cad_experimental.app import BUILD_PATH, VALIDATE_PATH, create_app
        from cad_experimental.config import ExperimentalConfig

        cls.BUILD_PATH = BUILD_PATH
        cls.VALIDATE_PATH = VALIDATE_PATH
        cls.client = TestClient(create_app(
            config=ExperimentalConfig(model="stub-model"),
            service=CadApplicationService.local(tempfile.mkdtemp()),
        ))

    def test_validate_accepts_both(self):
        for operations in (
            (sketch(), extrude()),
            (sketch("profile", "XZ"), revolve(axis="+Z")),
        ):
            body = self.client.post(
                self.VALIDATE_PATH, json={"plan": plan(*operations)}
            ).json()
            with self.subTest(operations=operations[-1]["type"]):
                self.assertTrue(body["valid"])

    def test_build_answers_501(self):
        response = self.client.post(
            self.BUILD_PATH, json={"plan": plan(sketch(), extrude())}
        )
        self.assertEqual(response.status_code, 501)
        body = response.json()
        self.assertTrue(body["execution_unsupported"])
        self.assertEqual(body["unsupported_types"], ["sketch", EXTRUDE])

    def test_an_invalid_plane_direction_answers_400(self):
        response = self.client.post(self.BUILD_PATH, json={
            "plan": plan(sketch(), extrude(direction="+X")),
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            [p["code"] for p in response.json()["problems"]], [P24]
        )


# --- 6. the fixtures -------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def new_fixtures(self):
        return [
            name for name in lpp.FIXTURE_NAMES
            if "extrude" in name or "revolve" in name
        ]

    def test_there_are_fixtures_for_both_operations(self):
        names = self.new_fixtures()
        self.assertTrue(any("extrude" in name for name in names))
        self.assertTrue(any("revolve" in name for name in names))

    def test_each_one_lands_where_it_says(self):
        for name in self.new_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=True)
                self.assertTrue(result["rejected"], name)
                self.assertTrue(
                    result["rejected_for_the_right_reason"], name
                )

    def test_the_valid_ones_are_valid_and_unexecutable(self):
        for name in self.new_fixtures():
            entry = lpp.FIXTURES[name]
            if entry.get("expects") != lpp.EXECUTION_UNSUPPORTED:
                continue
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=True)
                self.assertTrue(result["plan_valid"], name)
                self.assertTrue(result["execution_unsupported"], name)
                self.assertIsNone(result["v1_document"])
                self.assertFalse(result["built"])

    def test_none_of_them_reports_geometry(self):
        for name in self.new_fixtures():
            result = lpp.run_fixture(name, build=True)
            for key in ("volume_mm3", "bounding_box", "build_key",
                        "solid_count", "face_count", "render"):
                with self.subTest(fixture=name, key=key):
                    self.assertNotIn(key, result)

    def test_every_new_rule_has_a_fixture(self):
        covered = {
            code
            for name in lpp.FIXTURE_NAMES
            for code in lpp.FIXTURES[name].get("expected_codes", ())
        }
        for code in (P23, P24, P25, P26):
            self.assertIn(code, covered)

    def test_no_fixture_expects_either_to_build(self):
        for name in lpp.FIXTURE_NAMES:
            entry = lpp.FIXTURES[name]
            has_new = any(
                operation["type"] in PROFILE_SOLID_TYPES
                for operation in entry["plan"]["operations"]
            )
            if has_new:
                with self.subTest(fixture=name):
                    self.assertNotEqual(
                        entry.get("expects", lpp.BUILDS), lpp.BUILDS
                    )

    def test_the_building_fixtures_still_all_build(self):
        for name in lpp.building_fixtures():
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=True)
                self.assertTrue(result["built"], name)
                self.assertFalse(result["execution_unsupported"], name)


# --- 7. nothing else changed ----------------------------------------------


class NoSecondCadPathTests(unittest.TestCase):
    def modules(self):
        for path in sorted(SOURCE.glob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_nothing_executes_generated_content(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None)
                    self.assertNotIn(
                        name, ("eval", "exec", "compile", "__import__"),
                        f"{path.name} calls {name}",
                    )

    def test_no_module_looks_up_a_computed_attribute(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) in ("getattr", "hasattr")
                    and len(node.args) >= 2
                    and not isinstance(node.args[1], ast.Constant)
                ):
                    self.fail(f"{path.name} looks up a computed attribute")

    def test_no_module_imports_a_kernel_outside_the_build_layer(self):
        allowed = {"build.py", "app.py", "local_plan_provider.py"}
        for path, tree in self.modules():
            if path.name in allowed:
                continue
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(module=path.name):
                for forbidden in ("cadquery", "OCP"):
                    self.assertNotIn(forbidden, imported)

    def test_cad_core_still_knows_nothing_of_these_operations(self):
        import cad_core.model as model

        for absent in ("extrude", "revolve", "sketch"):
            self.assertFalse(
                any(absent in name.lower() for name in dir(model)),
                absent,
            )

    def test_the_v1_validator_still_rejects_both_as_features(self):
        service = CadApplicationService.local(tempfile.mkdtemp())
        for kind in PROFILE_SOLID_TYPES:
            verdict = service.validate_document({
                "schema_version": "1.0.0", "units": "mm", "name": "hand-built",
                "features": [{"id": "body", "type": kind, "distance": 10}],
            })
            with self.subTest(kind=kind):
                self.assertFalse(verdict.valid)


# --- 8. the prompt ---------------------------------------------------------


class PromptTests(unittest.TestCase):
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

    def test_both_sections_exist(self):
        self.assertIn("## extrude", self.text)
        self.assertIn("## revolve", self.text)

    def test_the_prompt_says_neither_can_be_built(self):
        section = self.text.split("## extrude", 1)[1].split("There are no", 1)[0]
        self.assertIn(
            "neither an extrude nor a revolve can be built", section
        )
        self.assertIn("reported as unexecutable", section)

    def test_the_prompt_says_the_target_is_a_sketch(self):
        section = self.text.split("## extrude", 1)[1]
        self.assertIn("SKETCH", section)

    def test_the_prompt_states_the_plane_rules(self):
        section = self.text.split("## extrude", 1)[1]
        self.assertIn("NORMAL", section)
        self.assertIn("SPANS", section)

    def test_the_prompt_says_a_revolve_axis_is_required(self):
        section = self.text.split("## revolve", 1)[1]
        self.assertIn("REQUIRED", section)
        self.assertIn("no default", section)

    def test_the_prompt_says_a_profile_is_not_consumed(self):
        self.assertIn("not consumed", self.text)

    def test_the_prompt_no_longer_calls_them_unsupported(self):
        after = self.text.split("# When to say unsupported", 1)[1]
        self.assertNotIn("extrusions", after)
        self.assertNotIn("revolves", after)

    def test_sweeps_and_lofts_are_still_unsupported(self):
        after = self.text.split("# When to say unsupported", 1)[1]
        self.assertIn("sweeps", after)
        self.assertIn("lofts", after)

    def test_the_prompt_discourages_using_them_where_a_box_would_do(self):
        self.assertIn("is a `box`", self.text)

    def test_the_prompt_contains_no_unexpanded_placeholder(self):
        self.assertNotIn("{PlanStatus", self.text)
        self.assertNotIn("{'", self.text)

    def test_the_version_moved_with_the_vocabulary(self):
        self.assertEqual(self.version, "2026-09-10.7")
        self.assertEqual(len(self.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
