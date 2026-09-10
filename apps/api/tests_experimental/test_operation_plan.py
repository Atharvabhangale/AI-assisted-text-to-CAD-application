"""The experimental operation-plan path: parser, validator, adapter, service."""

from __future__ import annotations

import json
import math
import unittest

from cad_ai.provider import ProviderErrorKind

from cad_experimental.adapter import AdapterError, plan_to_document
from cad_experimental.generation import (
    OperationPlanService,
    PlanOutcome,
)
from cad_experimental.config import ExperimentalConfig
from cad_experimental.parser import (
    MAX_OPERATIONS,
    MAX_PAYLOAD_CHARS,
    PlanParseError,
    parse_plan,
    parse_plan_text,
)
from cad_experimental.plan import (
    AXES,
    BoxOperation,
    CylinderOperation,
    OperationPlan,
    PlanStatus,
    Point,
    plan_schema,
)
from cad_experimental.validation import P1, P2, P4, P5, P7, validate_plan

from stubs import (
    ExplodingModel,
    FailingModel,
    StubModel,
    box_plan,
    clarification_plan,
    cylinder_plan,
    unsupported_plan,
)

CONFIG = ExperimentalConfig(model="stub-model")


def generated(*operations, **extra):
    payload = {"status": "generated", "summary": "s", "operations": list(operations)}
    payload.update(extra)
    return json.dumps(payload)


def box_op(**parameters):
    base = {"x": 100, "y": 60, "z": 10}
    base.update(parameters)
    return {"id": "body", "type": "box", "parameters": base}


def cylinder_op(**parameters):
    base = {"diameter": 20, "height": 50}
    base.update(parameters)
    return {"id": "body", "type": "cylinder", "parameters": base}


class ParseBoxTests(unittest.TestCase):
    def test_minimal_box(self):
        plan = parse_plan_text(box_plan())
        self.assertIs(plan.status, PlanStatus.GENERATED)
        self.assertEqual(len(plan.operations), 1)
        operation = plan.operations[0]
        self.assertIsInstance(operation, BoxOperation)
        self.assertEqual((operation.x, operation.y, operation.z), (100.0, 60.0, 10.0))

    def test_box_position_is_optional_and_stays_absent(self):
        """An omitted position is omitted, never materialised as the origin.

        The contract already defines the default; inventing ``{0,0,0}`` here
        would be this layer guessing geometry.
        """
        plan = parse_plan_text(box_plan())
        self.assertIsNone(plan.operations[0].position)

    def test_box_position_is_read_when_given(self):
        plan = parse_plan_text(
            generated(box_op(position={"x": 10, "y": 20, "z": 30}))
        )
        self.assertEqual(plan.operations[0].position, Point(10.0, 20.0, 30.0))

    def test_integers_become_floats(self):
        plan = parse_plan_text(box_plan())
        for value in (plan.operations[0].x, plan.operations[0].y):
            self.assertIsInstance(value, float)


class ParseCylinderTests(unittest.TestCase):
    def test_minimal_cylinder(self):
        plan = parse_plan_text(cylinder_plan())
        operation = plan.operations[0]
        self.assertIsInstance(operation, CylinderOperation)
        self.assertEqual(operation.diameter, 20.0)
        self.assertEqual(operation.height, 50.0)
        self.assertEqual(operation.axis, "+Z")

    def test_axis_is_optional(self):
        plan = parse_plan_text(generated(cylinder_op()))
        self.assertIsNone(plan.operations[0].axis)

    def test_every_documented_axis_parses(self):
        for axis in AXES:
            with self.subTest(axis=axis):
                plan = parse_plan_text(generated(cylinder_op(axis=axis)))
                self.assertEqual(plan.operations[0].axis, axis)

    def test_lowercase_axis_is_rejected_not_normalised(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(cylinder_op(axis="+z")))

    def test_radius_is_not_a_parameter(self):
        """`radius` is a plausible hallucination and must not be accepted."""
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(
                    {
                        "id": "body",
                        "type": "cylinder",
                        "parameters": {"radius": 10, "height": 50},
                    }
                )
            )
        self.assertIn("unknown fields", caught.exception.message)


class ParseRejectionTests(unittest.TestCase):
    def test_unknown_operation_type_is_rejected(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                generated(
                    {
                        "id": "body",
                        "type": "sphere",
                        "parameters": {"diameter": 20},
                    }
                )
            )
        self.assertIn("unknown type", caught.exception.message)

    def test_every_unimplemented_operation_is_rejected(self):
        for kind in (
            "sphere", "cone", "torus", "extrude", "revolve", "sweep", "loft",
            "hole", "through_hole", "subtract", "union", "fillet", "chamfer",
            "pattern", "mirror", "sketch", "shell", "assembly",
        ):
            with self.subTest(type=kind):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated({"id": "b", "type": kind, "parameters": {}})
                    )

    def test_unknown_plan_field_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(), python="print(1)"))

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(colour="red")))

    def test_unknown_operation_field_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated(
                    {
                        "id": "b",
                        "type": "box",
                        "parameters": {"x": 1, "y": 1, "z": 1},
                        "script": "import os",
                    }
                )
            )

    def test_missing_required_parameter_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(
                generated({"id": "b", "type": "box", "parameters": {"x": 1, "y": 1}})
            )

    def test_string_number_is_rejected_not_coerced(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(x="100")))

    def test_boolean_is_not_a_number(self):
        """`True` is an int in Python and would silently become 1.0."""
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(x=True)))

    def test_nan_and_infinity_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                text = (
                    '{"status":"generated","summary":"s","operations":'
                    '[{"id":"b","type":"box","parameters":'
                    '{"x":' + literal + ',"y":1,"z":1}}]}'
                )
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)

    def test_null_number_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(x=None)))

    def test_bad_identifier_is_rejected(self):
        for identifier in ("1body", "body!", "", "a b", "../x", "body.x"):
            with self.subTest(identifier=identifier):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            {
                                "id": identifier,
                                "type": "box",
                                "parameters": {"x": 1, "y": 1, "z": 1},
                            }
                        )
                    )

    def test_a_dunder_id_is_accepted_and_stays_inert(self):
        """`__class__` matches the contract's own id pattern (S8).

        Accepting it is correct, and safe for one reason only: an id is
        never used to look anything up. It is copied into the document as a
        feature id and compared as a string, so there is no attribute
        traversal for it to reach.
        """
        plan = parse_plan_text(
            generated(
                {
                    "id": "__class__",
                    "type": "box",
                    "parameters": {"x": 1, "y": 1, "z": 1},
                }
            )
        )
        operation = plan.operations[0]
        self.assertEqual(operation.id, "__class__")
        self.assertIsInstance(operation.id, str)
        document = plan_to_document(plan)
        self.assertEqual(document["features"][0]["id"], "__class__")

    def test_partial_position_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(position={"x": 1, "y": 2})))

    def test_position_array_form_is_rejected(self):
        """The contract rejects `[x, y, z]`; so does the plan."""
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(box_op(position=[1, 2, 3])))

    def test_unknown_status_is_rejected(self):
        for status in ("ok", "success", "GENERATED", "done", "error"):
            with self.subTest(status=status):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        json.dumps(
                            {"status": status, "summary": "", "operations": []}
                        )
                    )

    def test_refusal_carrying_operations_is_rejected(self):
        """A model must not smuggle geometry past its own refusal."""
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text(
                json.dumps(
                    {
                        "status": "unsupported",
                        "summary": "",
                        "reason": "no",
                        "operations": [box_op()],
                    }
                )
            )
        self.assertIn("must not carry operations", caught.exception.message)

    def test_prose_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text("Sure! Here is a box 100 by 60 by 10.")

    def test_markdown_fence_is_rejected_not_stripped(self):
        """Repairing the output would measure the repair, not the model."""
        with self.assertRaises(PlanParseError):
            parse_plan_text("```json\n" + box_plan() + "\n```")

    def test_empty_and_whitespace_are_rejected(self):
        for text in ("", "   ", "\n\t "):
            with self.subTest(text=repr(text)):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(text)

    def test_json_array_is_rejected(self):
        with self.assertRaises(PlanParseError):
            parse_plan_text("[]")

    def test_oversized_payload_is_refused_before_parsing(self):
        with self.assertRaises(PlanParseError) as caught:
            parse_plan_text("{" + "a" * (MAX_PAYLOAD_CHARS + 1))
        self.assertIn("too long", caught.exception.message)

    def test_too_many_operations_is_refused(self):
        operations = [
            {"id": f"b{i}", "type": "box", "parameters": {"x": 1, "y": 1, "z": 1}}
            for i in range(MAX_OPERATIONS + 1)
        ]
        with self.assertRaises(PlanParseError):
            parse_plan_text(generated(*operations))


class SecurityTests(unittest.TestCase):
    """The model's output is data. These prove it cannot become anything else."""

    CODE_PAYLOADS = (
        "import os",
        "__import__('os').system('id')",
        "exec('print(1)')",
        "eval('1+1')",
        "subprocess.run(['ls'])",
        "open('/etc/passwd').read()",
        "lambda: 1",
        "os.environ['ANTHROPIC_API_KEY']",
        "{{7*7}}",
        "../../etc/passwd",
        "__class__.__mro__[1].__subclasses__()",
    )
    """Strings a hostile description might steer a model into emitting.

    Every one is rejected wherever a type, a dimension or a parameter name
    is expected. They are not rejected because they "look like code" -- the
    parser has no such notion -- but because each field accepts exactly one
    thing and none of them accepts this.
    """

    def test_code_as_an_operation_type_is_rejected(self):
        for payload in self.CODE_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated({"id": "b", "type": payload, "parameters": {}})
                    )

    def test_code_as_a_dimension_is_rejected(self):
        for payload in self.CODE_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(generated(box_op(x=payload)))

    def test_code_as_a_parameter_name_is_rejected(self):
        for payload in self.CODE_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            {
                                "id": "b",
                                "type": "box",
                                "parameters": {
                                    "x": 1, "y": 1, "z": 1, payload: 1,
                                },
                            }
                        )
                    )

    def test_code_as_an_id_is_rejected(self):
        for payload in self.CODE_PAYLOADS:
            with self.subTest(payload=payload):
                with self.assertRaises(PlanParseError):
                    parse_plan_text(
                        generated(
                            {
                                "id": payload,
                                "type": "box",
                                "parameters": {"x": 1, "y": 1, "z": 1},
                            }
                        )
                    )

    def test_a_python_program_is_not_a_plan(self):
        program = (
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(100, 60, 10)\n"
        )
        with self.assertRaises(PlanParseError):
            parse_plan_text(program)

    def test_code_in_a_summary_stays_inert_text(self):
        """Text fields keep their text. They are never interpreted."""
        plan = parse_plan_text(
            generated(box_op(), summary="__import__('os').system('id')")
        )
        self.assertEqual(plan.summary, "__import__('os').system('id')")
        self.assertIsInstance(plan.summary, str)


class ValidationTests(unittest.TestCase):
    def test_a_good_plan_is_valid(self):
        self.assertTrue(validate_plan(parse_plan_text(box_plan())).valid)

    def test_generated_plan_with_no_operations_is_invalid(self):
        verdict = validate_plan(
            OperationPlan(status=PlanStatus.GENERATED, summary="s")
        )
        self.assertFalse(verdict.valid)
        self.assertEqual([p.code for p in verdict.problems], [P1])

    def test_duplicate_ids_are_invalid(self):
        plan = parse_plan_text(generated(box_op(), box_op()))
        verdict = validate_plan(plan)
        self.assertFalse(verdict.valid)
        self.assertIn(P2, [p.code for p in verdict.problems])

    def test_non_positive_dimensions_are_invalid(self):
        for value in (0, -1, -0.5):
            with self.subTest(value=value):
                plan = parse_plan_text(generated(box_op(x=value)))
                verdict = validate_plan(plan)
                self.assertFalse(verdict.valid)
                self.assertIn(P4, [p.code for p in verdict.problems])

    def test_non_positive_cylinder_parameters_are_invalid(self):
        for parameters in ({"diameter": 0}, {"height": -5}):
            with self.subTest(parameters=parameters):
                plan = parse_plan_text(generated(cylinder_op(**parameters)))
                self.assertFalse(validate_plan(plan).valid)

    def test_a_bad_axis_built_in_code_is_invalid(self):
        """The parser blocks this; the validator must not rely on that."""
        plan = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                CylinderOperation(id="b", diameter=1, height=1, axis="up"),
            ),
        )
        verdict = validate_plan(plan)
        self.assertFalse(verdict.valid)
        self.assertIn(P5, [p.code for p in verdict.problems])

    def test_a_silent_refusal_is_invalid(self):
        verdict = validate_plan(OperationPlan(status=PlanStatus.UNSUPPORTED))
        self.assertFalse(verdict.valid)
        self.assertEqual([p.code for p in verdict.problems], [P7])

    def test_an_explained_refusal_is_valid(self):
        plan = parse_plan_text(unsupported_plan())
        self.assertTrue(validate_plan(plan).valid)

    def test_negative_position_is_allowed(self):
        """Only dimensions must be positive; a position may be anywhere."""
        plan = parse_plan_text(
            generated(box_op(position={"x": -10, "y": -20, "z": -30}))
        )
        self.assertTrue(validate_plan(plan).valid)


class AdapterTests(unittest.TestCase):
    def test_box_becomes_a_v1_box_feature(self):
        document = plan_to_document(parse_plan_text(box_plan()))
        self.assertEqual(document["schema_version"], "1.0.0")
        self.assertEqual(document["units"], "mm")
        self.assertEqual(
            document["features"],
            [
                {
                    "id": "body",
                    "type": "box",
                    "size": {"x": 100.0, "y": 60.0, "z": 10.0},
                }
            ],
        )

    def test_cylinder_becomes_a_v1_cylinder_feature(self):
        document = plan_to_document(parse_plan_text(cylinder_plan()))
        self.assertEqual(
            document["features"],
            [
                {
                    "id": "body",
                    "type": "cylinder",
                    "diameter": 20.0,
                    "height": 50.0,
                    "axis": "+Z",
                }
            ],
        )

    def test_absent_position_is_absent_in_the_document(self):
        document = plan_to_document(parse_plan_text(box_plan()))
        self.assertNotIn("position", document["features"][0])

    def test_absent_axis_is_absent_in_the_document(self):
        document = plan_to_document(parse_plan_text(generated(cylinder_op())))
        self.assertNotIn("axis", document["features"][0])

    def test_position_is_carried_through(self):
        document = plan_to_document(
            parse_plan_text(generated(box_op(position={"x": 1, "y": 2, "z": 3})))
        )
        self.assertEqual(
            document["features"][0]["position"], {"x": 1.0, "y": 2.0, "z": 3.0}
        )

    def test_a_refusal_has_no_document(self):
        for text in (unsupported_plan(), clarification_plan()):
            with self.subTest(text=text):
                with self.assertRaises(AdapterError):
                    plan_to_document(parse_plan_text(text))

    def test_the_adapter_names_no_kernel(self):
        """The bridge translates. It must not reach for geometry itself."""
        import cad_experimental.adapter as module

        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("cadquery", "OCP", "import cad_core"):
            self.assertNotIn(forbidden, source)


class SchemaTests(unittest.TestCase):
    def test_the_schema_admits_a_valid_plan_shape(self):
        """`target` joined the shape in Stage 33, with through_hole."""
        schema = plan_schema()
        self.assertEqual(
            set(schema["properties"]["operations"]["items"]["properties"]),
            {"id", "type", "target", "tools", "parameters"},
        )

    def test_the_schema_lists_only_the_implemented_types(self):
        """Spelled out, not derived: a fourth type must be a deliberate edit."""
        schema = plan_schema()
        types = schema["properties"]["operations"]["items"]["properties"]["type"]
        self.assertEqual(
            set(types["enum"]),
            {"box", "cylinder", "through_hole", "subtract"},
        )

    def test_the_schema_lists_no_unimplemented_operation(self):
        schema = plan_schema()
        types = schema["properties"]["operations"]["items"]["properties"]["type"]
        for absent in (
            "fillet", "chamfer", "sketch", "extrude", "revolve",
            "sweep", "loft", "pattern", "mirror", "union", "assembly",
        ):
            self.assertNotIn(absent, types["enum"])

    def test_the_schema_forbids_extra_fields(self):
        schema = plan_schema()
        self.assertFalse(schema["additionalProperties"])
        item = schema["properties"]["operations"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertFalse(item["properties"]["parameters"]["additionalProperties"])


class ServiceTests(unittest.TestCase):
    def service(self, model):
        return OperationPlanService(model, CONFIG)

    def test_a_box_description_generates(self):
        model = StubModel(box_plan())
        result = self.service(model).generate("a plate 100 x 60 x 10 mm")
        self.assertIs(result.outcome, PlanOutcome.GENERATED)
        self.assertTrue(result.plan_validation.valid)
        self.assertEqual(len(result.plan.operations), 1)

    def test_the_prompt_and_schema_are_sent(self):
        model = StubModel(box_plan())
        self.service(model).generate("a plate")
        request = model.requests[0]
        self.assertIn("operation plan", request.system)
        self.assertIsNotNone(request.output_schema)

    def test_the_description_is_sent_unmodified(self):
        model = StubModel(box_plan())
        text = "Create a  plate 100mm  long."
        self.service(model).generate(text)
        self.assertEqual(model.requests[0].user_text, text.strip())

    def test_no_tools_are_offered_to_the_model(self):
        """The request record has nowhere to put a tool. Prove it stays so."""
        model = StubModel(box_plan())
        self.service(model).generate("a plate")
        request = model.requests[0]
        for attribute in ("tools", "tool_choice", "functions"):
            self.assertFalse(hasattr(request, attribute))

    def test_unsupported_is_reported_as_unsupported(self):
        result = self.service(StubModel(unsupported_plan())).generate("a sphere")
        self.assertIs(result.outcome, PlanOutcome.UNSUPPORTED)
        self.assertEqual(result.plan.operations, ())

    def test_clarification_is_reported_as_clarification(self):
        result = self.service(StubModel(clarification_plan())).generate("a cylinder")
        self.assertIs(result.outcome, PlanOutcome.NEEDS_CLARIFICATION)
        self.assertTrue(result.plan.questions)

    def test_unparseable_output_is_invalid_not_an_error(self):
        result = self.service(StubModel("not json")).generate("a plate")
        self.assertIs(result.outcome, PlanOutcome.INVALID_MODEL_OUTPUT)
        self.assertTrue(result.answered)

    def test_a_provider_failure_is_a_model_error(self):
        model = FailingModel(ProviderErrorKind.RATE_LIMITED)
        result = self.service(model).generate("a plate")
        self.assertIs(result.outcome, PlanOutcome.MODEL_ERROR)
        self.assertIs(result.error_kind, ProviderErrorKind.RATE_LIMITED)
        self.assertFalse(result.answered)

    def test_a_provider_failure_is_not_a_model_quality_failure(self):
        """`answered` is the line between reliability and quality."""
        self.assertFalse(
            self.service(FailingModel()).generate("a plate").answered
        )
        self.assertTrue(
            self.service(StubModel("nonsense")).generate("a plate").answered
        )

    def test_empty_input_never_calls_the_provider(self):
        for text in ("", "   ", None):
            with self.subTest(text=repr(text)):
                result = self.service(ExplodingModel()).generate(text)
                self.assertIs(result.outcome, PlanOutcome.NEEDS_CLARIFICATION)

    def test_a_model_claimed_status_cannot_be_model_error(self):
        for claimed in ("model_error", "invalid_model_output"):
            with self.subTest(claimed=claimed):
                text = json.dumps(
                    {"status": claimed, "summary": "", "operations": []}
                )
                result = self.service(StubModel(text)).generate("a plate")
                self.assertIs(result.outcome, PlanOutcome.INVALID_MODEL_OUTPUT)

    def test_raw_text_is_kept_for_measurement(self):
        result = self.service(StubModel("garbage")).generate("a plate")
        self.assertEqual(result.raw_text, "garbage")

    def test_raw_text_and_detail_are_excluded_from_the_default_record(self):
        result = self.service(StubModel("garbage")).generate("a plate")
        payload = result.to_dict()
        self.assertNotIn("raw_text", payload)
        self.assertNotIn("error_detail", payload)
        self.assertIn("raw_text", result.to_dict(include_raw=True))

    def test_a_provider_detail_never_reaches_the_default_record(self):
        result = self.service(FailingModel()).generate("a plate")
        self.assertNotIn("sk-ant", json.dumps(result.to_dict()))

    def test_metadata_records_the_prompt_identity(self):
        from cad_experimental.prompt import PROMPT_VERSION, prompt_fingerprint

        result = self.service(StubModel(box_plan())).generate("a plate")
        self.assertEqual(result.metadata.prompt_version, PROMPT_VERSION)
        self.assertEqual(
            result.metadata.prompt_fingerprint, prompt_fingerprint()
        )

    def test_no_retry_on_failure(self):
        model = FailingModel()
        self.service(model).generate("a plate")
        self.assertEqual(model.calls, 1)


if __name__ == "__main__":
    unittest.main()
