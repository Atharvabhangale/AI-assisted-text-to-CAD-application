"""Stage 44: can a model actually reach a profile operation?

Stage 43 measured both representations with structured output on, and the
operation plan answered ``unsupported`` 5/5 on both profile cases. The run
scored that as the model's judgement. It was not one.

Two things stood between the model and a profile plan, and either alone was
enough:

1. **The grammar.** ``provider_schema()`` was the *executable* subset -- six
   operation types. A decoder constrained by it cannot emit a ``sketch``, an
   ``extrude`` or a ``revolve``, because no branch of the union admits one.
   Refusing was the only reachable answer, and no prompt could have changed
   that.
2. **The prompt.** It said "neither an extrude nor a revolve can be built"
   and "do not offer them as a way to make a part". The recorded output
   quotes that sentence back as the model's ``reason``.

The plan layer was innocent throughout: the parser, the validator (P23-P26)
and the adapter's explicit :class:`ExecutionUnsupported` all did exactly what
Stages 37-38 built them to do. Those are covered by ``test_sketch`` and
``test_extrude_revolve``; this module covers the **model-facing** path that
made them unreachable, so the pairing cannot silently come apart again.

Nothing here asserts that the provider *compiles* the new schema. That needs
one live call and a credential, and neither is available where this runs --
see ``docs/experimental-operation-plan.md``.
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Dict, List, Mapping

from stubs import StubModel

from cad_experimental.adapter import ExecutionUnsupported, plan_to_document
from cad_experimental.build import build_plan
from cad_experimental.config import ExperimentalConfig
from cad_experimental.generation import OperationPlanService, PlanOutcome
from cad_experimental.parser import parse_plan
from cad_experimental.plan import (
    EXECUTABLE_TYPES,
    EXTRUDE,
    OPERATION_TYPES,
    PROFILE_SOLID_TYPES,
    PROFILE_TYPES,
    REVOLVE,
    SKETCH,
    compact_provider_schema,
    executable_schema,
    provider_schema,
)
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

CONFIG = ExperimentalConfig(model="stub-model")

#: The two profile plans this module uses everywhere, as a model would send
#: them. Written once: a test that quietly used a different payload from the
#: one it claims to check would prove nothing.
RECTANGLE_PROFILE: Dict[str, Any] = {
    "id": "profile",
    "type": "sketch",
    "parameters": {
        "plane": "XY",
        "geometry": [
            {
                "id": "r1",
                "type": "rectangle",
                "corner": {"x": 0.0, "y": 0.0},
                "width": 80.0,
                "height": 40.0,
            }
        ],
    },
}

CIRCLE_PROFILE: Dict[str, Any] = {
    "id": "profile",
    "type": "sketch",
    "parameters": {
        "plane": "XZ",
        "geometry": [
            {
                "id": "c1",
                "type": "circle",
                "centre": {"x": 30.0, "y": 0.0},
                "radius": 5.0,
            }
        ],
    },
}

EXTRUDE_OF_PROFILE: Dict[str, Any] = {
    "id": "body",
    "type": "extrude",
    "target": "profile",
    "parameters": {"distance": 12.0},
}

REVOLVE_OF_PROFILE: Dict[str, Any] = {
    "id": "body",
    "type": "revolve",
    "target": "profile",
    "parameters": {"angle": 360.0, "axis": "+Z"},
}


def plan_payload(*operations: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "status": "generated",
        "summary": "a profile and the solid swept from it",
        "operations": [dict(operation) for operation in operations],
    }


def plan_text(*operations: Mapping[str, Any]) -> str:
    return json.dumps(plan_payload(*operations))


# --- a small structural matcher, so "the grammar admits it" is checked ------
#
# There is no jsonschema dependency here, and adding one to assert a schema
# the provider compiles differently would be its own kind of guess. This
# checks the one property that decides the Stage 43 failure: whether some
# branch of the union accepts an operation object exactly -- same required
# keys present, no key the branch does not declare, and the discriminating
# `type` matched. It deliberately does not check value types or formats.


def branch_admits(branch: Mapping[str, Any], operation: Mapping[str, Any]) -> bool:
    discriminator = branch["properties"]["type"]
    allowed_types = (
        [discriminator["const"]] if "const" in discriminator
        else list(discriminator["enum"])
    )
    if operation.get("type") not in allowed_types:
        return False
    properties = branch["properties"]
    if any(key not in properties for key in operation):
        return False
    if any(key not in operation for key in branch["required"]):
        return False
    parameters = operation.get("parameters")
    if parameters is None:
        return "parameters" not in properties
    declared = properties["parameters"]
    if any(key not in declared["properties"] for key in parameters):
        return False
    return all(key in parameters for key in declared["required"])


def admitting_branches(
    schema: Mapping[str, Any], operation: Mapping[str, Any]
) -> List[Mapping[str, Any]]:
    return [
        branch
        for branch in schema["properties"]["operations"]["items"]["anyOf"]
        if branch_admits(branch, operation)
    ]


class TheMatcherItselfTests(unittest.TestCase):
    """A matcher that said yes to everything would make this module vacuous."""

    def test_it_rejects_an_operation_with_an_unknown_key(self):
        operation = dict(EXTRUDE_OF_PROFILE, colour="red")
        self.assertEqual(admitting_branches(provider_schema(), operation), [])

    def test_it_rejects_an_operation_missing_a_required_parameter(self):
        """A box, not an extrude: Stage 46 merged extrude with revolve, and a
        merged branch requires only what both members require -- which for
        that pair is nothing. A box branch still requires its three sizes."""
        operation = {"id": "b", "type": "box", "parameters": {"x": 1.0}}
        self.assertEqual(admitting_branches(provider_schema(), operation), [])

    def test_it_rejects_a_target_on_a_type_that_takes_none(self):
        operation = {
            "id": "b", "type": "box", "target": "other",
            "parameters": {"x": 1.0, "y": 1.0, "z": 1.0},
        }
        self.assertEqual(admitting_branches(provider_schema(), operation), [])

    def test_it_accepts_an_ordinary_box(self):
        operation = {
            "id": "b", "type": "box",
            "parameters": {"x": 1.0, "y": 1.0, "z": 1.0},
        }
        self.assertEqual(len(admitting_branches(provider_schema(), operation)), 1)


# --- 1. the grammar the model decodes against ------------------------------


class TheGrammarAdmitsAProfileTests(unittest.TestCase):
    """The decisive half of the Stage 43 root cause."""

    def test_the_executable_schema_admitted_neither_profile_operation(self):
        """The Stage 43 instrument, unchanged, and why its 5/5 means nothing
        about what the model would have chosen."""
        for operation in (RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE,
                          CIRCLE_PROFILE, REVOLVE_OF_PROFILE):
            with self.subTest(type=operation["type"]):
                self.assertEqual(
                    admitting_branches(executable_schema(), operation), []
                )

    def test_the_provider_schema_admits_a_sketch_and_an_extrude(self):
        for operation in (RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE):
            with self.subTest(type=operation["type"]):
                self.assertEqual(
                    len(admitting_branches(provider_schema(), operation)), 1
                )

    def test_the_provider_schema_admits_a_sketch_and_a_revolve(self):
        for operation in (CIRCLE_PROFILE, REVOLVE_OF_PROFILE):
            with self.subTest(type=operation["type"]):
                self.assertEqual(
                    len(admitting_branches(provider_schema(), operation)), 1
                )

    def test_the_compact_variant_admits_them_too(self):
        """The fallback must not be a fallback to the Stage 43 behaviour: it
        gives up a sketch's optional constraints, never its profiles."""
        for operation in (RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE,
                          CIRCLE_PROFILE, REVOLVE_OF_PROFILE):
            with self.subTest(type=operation["type"]):
                self.assertEqual(
                    len(admitting_branches(compact_provider_schema(), operation)),
                    1,
                )

    def test_the_compact_variant_drops_only_the_constraints(self):
        with_constraints = dict(
            RECTANGLE_PROFILE,
            parameters=dict(RECTANGLE_PROFILE["parameters"], constraints=[]),
        )
        self.assertEqual(
            len(admitting_branches(provider_schema(), with_constraints)), 1
        )
        self.assertEqual(
            admitting_branches(compact_provider_schema(), with_constraints), []
        )

    def test_every_operation_type_is_reachable_in_the_provider_schema(self):
        """The invariant Stage 43 broke, stated in one test: a type the
        language has and the grammar does not is a type the model cannot
        choose, however the prompt is worded."""
        reachable = set()
        for branch in provider_schema()["properties"]["operations"]["items"]["anyOf"]:
            discriminator = branch["properties"]["type"]
            if "const" in discriminator:
                reachable.add(discriminator["const"])
            else:
                reachable.update(discriminator["enum"])
        self.assertEqual(reachable, set(OPERATION_TYPES))

    def test_the_gap_between_grammar_and_engine_is_now_only_the_engine(self):
        """Both facts at once, so neither can be mistaken for the other: the
        model may say all nine, and the engine still builds only six."""
        self.assertEqual(
            set(OPERATION_TYPES) - set(EXECUTABLE_TYPES),
            set(PROFILE_TYPES) | set(PROFILE_SOLID_TYPES),
        )


# --- 2. the prompt half ----------------------------------------------------


class ThePromptAgreesWithTheGrammarTests(unittest.TestCase):
    def setUp(self):
        self.text = system_prompt()

    def test_the_prompt_documents_exactly_the_reachable_types(self):
        """A prompt describing an operation the grammar forbids is the Stage
        43 failure in the other direction -- the model is told to produce
        something it will not be allowed to emit."""
        for kind in OPERATION_TYPES:
            with self.subTest(kind=kind):
                self.assertIn(f"## {kind}", self.text)

    def test_the_prompt_no_longer_tells_the_model_they_cannot_be_built(self):
        self.assertNotIn("cannot be built", self.text)

    def test_the_prompt_still_refuses_what_the_language_lacks(self):
        """Loosening the profile wording must not loosen the refusals: the
        negative corpus cases are refusals for a real reason."""
        after = self.text.split("# When to say unsupported", 1)[1]
        for absent in ("spheres", "cones", "tori", "lofts", "assemblies"):
            with self.subTest(absent=absent):
                self.assertIn(absent, after)
        self.assertIn("Do not approximate", after)


# --- 3. end to end: description in, profile plan out -----------------------


class AProfilePlanSurvivesTheWholePathTests(unittest.TestCase):
    """What Stage 43 could not reach, exercised end to end with a stub."""

    def service(self, text: str):
        self.model = StubModel(text)
        return OperationPlanService(self.model, CONFIG)

    def generated(self, *operations):
        service = self.service(plan_text(*operations))
        return service.generate("a profile, swept")

    def test_a_sketch_then_extrude_is_generated_and_valid(self):
        result = self.generated(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE)
        self.assertIs(result.outcome, PlanOutcome.GENERATED)
        self.assertTrue(result.plan_validation.valid,
                        result.plan_validation.to_dict())
        self.assertEqual([o.TYPE for o in result.plan.operations],
                         [SKETCH, EXTRUDE])

    def test_a_sketch_then_revolve_is_generated_and_valid(self):
        result = self.generated(CIRCLE_PROFILE, REVOLVE_OF_PROFILE)
        self.assertIs(result.outcome, PlanOutcome.GENERATED)
        self.assertTrue(result.plan_validation.valid,
                        result.plan_validation.to_dict())
        self.assertEqual([o.TYPE for o in result.plan.operations],
                         [SKETCH, REVOLVE])

    def test_the_service_hands_the_model_a_grammar_that_admits_the_answer(self):
        """The two halves joined: the schema the service actually sends must
        admit the plan the service is willing to accept back."""
        result = self.generated(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE)
        self.assertIs(result.outcome, PlanOutcome.GENERATED)
        schema = self.model.requests[0].output_schema
        self.assertIsNotNone(schema)
        for operation in (RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE):
            with self.subTest(type=operation["type"]):
                self.assertEqual(len(admitting_branches(schema, operation)), 1)

    def test_the_service_sends_the_stage_44_prompt(self):
        self.generated(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE)
        self.assertEqual(self.model.requests[0].system, system_prompt())


# --- 4. references, valid and invalid, through the same path ---------------


class ProfileReferencesTests(unittest.TestCase):
    """P23's two directions, reached the way a model reaches them."""

    def validation(self, *operations):
        return validate_plan(parse_plan(plan_payload(*operations)))

    def test_an_extrude_of_an_earlier_sketch_is_valid(self):
        self.assertTrue(
            self.validation(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE).valid
        )

    def test_a_revolve_of_an_earlier_sketch_is_valid(self):
        self.assertTrue(
            self.validation(CIRCLE_PROFILE, REVOLVE_OF_PROFILE).valid
        )

    def test_one_profile_may_feed_both_a_extrude_and_a_revolve(self):
        """A profile is not consumed, so two sweeps of one sketch are sound
        -- and the resulting two solids are S9's problem, not P23's."""
        result = self.validation(
            CIRCLE_PROFILE,
            dict(EXTRUDE_OF_PROFILE, id="solid_a"),
            dict(REVOLVE_OF_PROFILE, id="solid_b", parameters={
                "angle": 90.0, "axis": "+Z"}),
        )
        self.assertTrue(result.valid, result.to_dict())

    def test_an_extrude_of_a_solid_is_p23(self):
        result = self.validation(
            {"id": "plate", "type": "box",
             "parameters": {"x": 10.0, "y": 10.0, "z": 10.0}},
            dict(EXTRUDE_OF_PROFILE, target="plate"),
        )
        self.assertFalse(result.valid)
        self.assertIn("P23", [p.code for p in result.problems])

    def test_an_extrude_of_an_undeclared_id_is_p9(self):
        result = self.validation(dict(EXTRUDE_OF_PROFILE, target="nowhere"))
        self.assertFalse(result.valid)
        self.assertIn("P9", [p.code for p in result.problems])

    def test_a_forward_reference_to_a_sketch_is_p10(self):
        result = self.validation(EXTRUDE_OF_PROFILE, RECTANGLE_PROFILE)
        self.assertFalse(result.valid)
        self.assertIn("P10", [p.code for p in result.problems])

    def test_an_extrude_targeting_itself_is_p10(self):
        result = self.validation(
            RECTANGLE_PROFILE, dict(EXTRUDE_OF_PROFILE, target="body")
        )
        self.assertFalse(result.valid)
        self.assertIn("P10", [p.code for p in result.problems])

    def test_an_invalid_reference_never_reaches_the_adapter(self):
        """A bad plan must fail as a bad plan. Conflating it with the
        execution boundary would report the engine as behind when the plan
        is simply wrong."""
        payload = plan_payload(dict(EXTRUDE_OF_PROFILE, target="nowhere"))
        self.assertFalse(validate_plan(parse_plan(payload)).valid)


# --- 5. valid, and still explicitly unexecutable ---------------------------


class StillRefusedAtTheEngineTests(unittest.TestCase):
    """Widening the grammar must not widen what claims to be buildable."""

    def plan(self, *operations):
        return parse_plan(plan_payload(*operations))

    def test_an_extrude_plan_is_valid_and_unexecutable(self):
        plan = self.plan(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE)
        self.assertTrue(validate_plan(plan).valid)
        with self.assertRaises(ExecutionUnsupported) as caught:
            plan_to_document(plan)
        self.assertEqual(caught.exception.operation_types, (SKETCH, EXTRUDE))
        self.assertEqual(caught.exception.operation_ids, ("profile", "body"))

    def test_a_revolve_plan_is_valid_and_unexecutable(self):
        plan = self.plan(CIRCLE_PROFILE, REVOLVE_OF_PROFILE)
        self.assertTrue(validate_plan(plan).valid)
        with self.assertRaises(ExecutionUnsupported) as caught:
            plan_to_document(plan)
        self.assertEqual(caught.exception.operation_types, (SKETCH, REVOLVE))

    def test_building_reports_it_as_unsupported_and_builds_nothing(self):
        built = build_plan(None, self.plan(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE))
        self.assertTrue(built.execution_unsupported)
        self.assertFalse(built.built)
        self.assertIsNone(built.document)
        self.assertEqual(built.unsupported_types, (SKETCH, EXTRUDE))

    def test_the_service_never_calls_the_kernel_for_such_a_plan(self):
        """`build_plan` is given ``None`` for the application service above.
        That is the assertion: if the refusal were not decided before the
        service is touched, this would raise."""
        built = build_plan(None, self.plan(CIRCLE_PROFILE, REVOLVE_OF_PROFILE))
        self.assertTrue(built.execution_unsupported)

    def test_no_approximation_is_offered_anywhere_in_the_answer(self):
        built = build_plan(None, self.plan(RECTANGLE_PROFILE, EXTRUDE_OF_PROFILE))
        self.assertIsNone(built.outcome)
        self.assertNotIn("box", (built.error or "").lower())


if __name__ == "__main__":
    unittest.main()
