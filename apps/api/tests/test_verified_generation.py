"""Verified generation: the re-ask loop, its public record, and its inputs.

Covers what prompt ``2026-11-12.1`` and the verify-and-retry loop added:

* each model answer is validated **and built** through the existing
  application service, and the model is asked again -- within a bound --
  when either rejects it;
* every model call that answered is one public :class:`GenerationAttempt`;
* the response schema is all-required and small enough for structured output;
* every worked example embedded in the prompt actually builds;
* the configuration: the attempt limit, the effort setting, the default model
  and the fallback credential name;
* what the Anthropic provider sends: a cached system block, no sampling or
  thinking parameters, and a refusal reported as a refusal.

No test here contacts a provider. Models are scripted stubs; the Anthropic
request tests hand the provider a fake client that records its arguments.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence

from cad_ai import config as ai_config
from cad_ai.anthropic_provider import (
    ANSWER_MODE_VARIABLE,
    CACHE_CONTROL,
    AnthropicTextToCadModel,
    schema_for_api,
)
from cad_ai.config import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MODELS,
    FALLBACK_API_KEY_VARIABLE,
    AiConfig,
    AiConfigurationError,
    config_from_environment,
    credential_available,
    credential_variable,
    resolve_provider,
)
from cad_ai.generation import (
    NO_DOCUMENT,
    REFERENCE_MARKER,
    RETRY_MARKER,
    UNUSABLE_ANSWER,
    VERIFICATION_OUTPUTS,
    AttemptStage,
    GenerationOutcome,
    TextToCadService,
    compose_user_text,
)
from cad_ai.prompt import ANSWER_TOOL_NAME, EXAMPLES, system_prompt
from cad_ai.templates import TEMPLATE_MODULES, TEMPLATES, select_templates
from cad_ai.templates.engine import TEMPLATE as ENGINE_TEMPLATE
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
)
from cad_ai.specification import (
    RESPONSE_FIELDS,
    feature_groups,
    optional_property_counts,
    response_schema,
)
from cad_core.application_service import BuildDocumentRequest, CadApplicationService
from cad_core.model import SCHEMA_VERSION

REPOSITORY = Path(__file__).resolve().parents[3]


def _p(x: float, y: float, z: float) -> Dict[str, float]:
    return {"x": x, "y": y, "z": z}


def plate(*extra: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "units": "mm",
        "name": "plate",
        "features": [
            {"id": "plate", "type": "box", "size": _p(100, 60, 10)},
            *[dict(feature) for feature in extra],
        ],
    }


def hole_at(x: float, y: float, feature_id: str = "hole") -> Dict[str, Any]:
    return {
        "id": feature_id,
        "type": "through_hole",
        "target": "plate",
        "diameter": 8,
        "position": _p(x, y, 0),
        "axis": "+Z",
    }


#: Valid and buildable.
GOOD = plate(hole_at(50, 30))
#: Statically invalid: the hole names a solid that does not exist (S6).
STATIC_BAD = plate({**hole_at(50, 30), "target": "nothing"})
#: Statically valid, but the hole misses the material (E1) -- only a build can
#: tell.
GEOMETRY_BAD = plate(hole_at(200, 30))


def reply(
    status: str = "document",
    *,
    doc: Optional[Mapping[str, Any]] = None,
    assumptions: Sequence[str] = (),
    questions: Sequence[str] = (),
    issues: Sequence[str] = (),
    summary: str = "A plate.",
) -> Dict[str, Any]:
    """A complete answer in the all-required response shape."""
    return {
        "status": status,
        "plan": "working notes" if status == "document" else "",
        "assumptions": list(assumptions),
        "document": dict(doc) if doc is not None else None,
        "summary": summary,
        "questions": list(questions),
        "issues": list(issues),
    }


class ScriptedModel:
    """Answers each call with the next scripted item; raises a ProviderError item."""

    name = "scripted"

    def __init__(self, *script: Any, usage: Optional[Mapping[str, int]] = None) -> None:
        self._script: List[Any] = list(script)
        self._usage = dict(usage or {"input_tokens": 10, "output_tokens": 100})
        self.requests: List[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("the model was called more often than scripted")
        item = self._script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return ModelResponse(
            text=text,
            provider=self.name,
            model="scripted-model",
            structured_output=True,
            stop_reason="end_turn",
            usage=dict(self._usage),
        )


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        cache = Path(directory.name) / "cache"
        cache.mkdir()
        self.service = CadApplicationService.local(cache)

    def run_script(self, *script: Any, text: str = "a plate with a hole", **kwargs: Any):
        model = ScriptedModel(*script)
        result = TextToCadService(model, self.service, **kwargs).generate_cad_from_text(
            text
        )
        return result, model


class TestTheLoop(ServiceTestCase):
    def test_a_good_first_answer_is_one_accepted_attempt(self) -> None:
        result, model = self.run_script(reply(doc=GOOD, assumptions=["assumed mm"]))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(
            [attempt.to_dict() for attempt in result.attempts],
            [
                {
                    "number": 1,
                    "accepted": True,
                    "stage": None,
                    "rule_codes": [],
                    "issues": [],
                }
            ],
        )
        self.assertEqual(result.assumptions, ("assumed mm",))
        self.assertTrue(result.metadata.build_verified)
        self.assertEqual(result.metadata.model_calls, 1)

    def test_a_validator_rejection_is_re_asked_with_the_reasons(self) -> None:
        result, model = self.run_script(reply(doc=STATIC_BAD), reply(doc=GOOD))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        first, second = result.attempts
        self.assertFalse(first.accepted)
        self.assertIs(first.stage, AttemptStage.VALIDATION)
        self.assertIn("S6", first.rule_codes)
        self.assertTrue(first.issues)
        self.assertTrue(second.accepted)

        retry = model.requests[1].user_text
        self.assertTrue(retry.startswith("a plate with a hole\n"))
        self.assertIn(RETRY_MARKER, retry)
        self.assertIn("rule S6", retry)
        self.assertIn('"nothing"', retry)  # the rejected document is returned
        # The system prompt is byte-identical on every call, so it can be
        # cached; only the user text changes.
        self.assertEqual(model.requests[0].system, model.requests[1].system)
        self.assertEqual(model.requests[0].user_text, "a plate with a hole")

    def test_a_document_that_does_not_build_is_rejected_at_the_geometry_stage(
        self,
    ) -> None:
        result, model = self.run_script(reply(doc=GEOMETRY_BAD), reply(doc=GOOD))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        first = result.attempts[0]
        self.assertIs(first.stage, AttemptStage.GEOMETRY)
        self.assertIn("E1", first.rule_codes)
        self.assertIn("rule E1", model.requests[1].user_text)

    def test_an_unusable_answer_is_rejected_at_the_answer_stage(self) -> None:
        result, _ = self.run_script("Sure! Here is a plate.", reply(doc=GOOD))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        self.assertIs(result.attempts[0].stage, AttemptStage.ANSWER)
        self.assertEqual(result.attempts[0].issues, (UNUSABLE_ANSWER,))

    def test_a_document_status_without_a_document_is_re_asked(self) -> None:
        result, model = self.run_script(reply(doc=None), reply(doc=GOOD))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        self.assertIs(result.attempts[0].stage, AttemptStage.ANSWER)
        self.assertEqual(result.attempts[0].issues, (NO_DOCUMENT,))
        self.assertIn("`document`", model.requests[1].user_text)

    def test_the_loop_stops_at_the_limit_and_reports_every_attempt(self) -> None:
        result, model = self.run_script(
            reply(doc=STATIC_BAD), reply(doc=GEOMETRY_BAD), reply(doc=STATIC_BAD)
        )
        self.assertIs(result.outcome, GenerationOutcome.INVALID_MODEL_OUTPUT)
        self.assertEqual(len(model.requests), DEFAULT_MAX_ATTEMPTS)
        self.assertEqual(
            [attempt.stage for attempt in result.attempts],
            [AttemptStage.VALIDATION, AttemptStage.GEOMETRY, AttemptStage.VALIDATION],
        )
        self.assertFalse(any(attempt.accepted for attempt in result.attempts))
        # The top level repeats the last attempt's reasons.
        self.assertEqual(result.issues, result.attempts[-1].issues)
        self.assertEqual(result.rule_codes, result.attempts[-1].rule_codes)
        self.assertIsNone(result.candidate_document)
        self.assertEqual(result.assumptions, ())

    def test_one_attempt_means_one_call(self) -> None:
        result, model = self.run_script(reply(doc=STATIC_BAD), max_attempts=1)
        self.assertIs(result.outcome, GenerationOutcome.INVALID_MODEL_OUTPUT)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(len(result.attempts), 1)

    def test_a_provider_failure_is_never_re_asked(self) -> None:
        failure = ProviderError(
            "unavailable", detail="RateLimitError", kind=ProviderErrorKind.RATE_LIMITED
        )
        result, model = self.run_script(reply(doc=STATIC_BAD), failure)
        self.assertIs(result.outcome, GenerationOutcome.MODEL_ERROR)
        self.assertIs(result.error_kind, ProviderErrorKind.RATE_LIMITED)
        self.assertEqual(len(model.requests), 2)
        # The failed call is billed and counted, but is not an attempt record.
        self.assertEqual(result.metadata.model_calls, 2)
        self.assertEqual(len(result.attempts), 1)
        self.assertIs(result.attempts[0].stage, AttemptStage.VALIDATION)

    def test_a_clarification_is_final(self) -> None:
        result, model = self.run_script(
            reply("needs_clarification", questions=["What part?"])
        )
        self.assertIs(result.outcome, GenerationOutcome.NEEDS_CLARIFICATION)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result.questions, ("What part?",))
        (attempt,) = result.attempts
        self.assertFalse(attempt.accepted)
        self.assertIsNone(attempt.stage)

    def test_an_unsupported_answer_is_final(self) -> None:
        result, model = self.run_script(reply("unsupported", issues=["asks for code"]))
        self.assertIs(result.outcome, GenerationOutcome.UNSUPPORTED)
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(result.issues, ("asks for code",))

    def test_usage_is_summed_over_calls(self) -> None:
        model = ScriptedModel(
            reply(doc=STATIC_BAD),
            reply(doc=GOOD),
            usage={"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 50},
        )
        result = TextToCadService(model, self.service).generate_cad_from_text("x")
        self.assertEqual(
            dict(result.metadata.usage),
            {"input_tokens": 20, "output_tokens": 200, "cache_read_input_tokens": 100},
        )
        self.assertEqual(result.metadata.model_calls, 2)

    def test_assumptions_are_bounded_and_never_returned_without_a_document(self) -> None:
        many = [f"assumption {index}" for index in range(30)] + ["x" * 1000]
        result, _ = self.run_script(reply(doc=GOOD, assumptions=many))
        self.assertLessEqual(len(result.assumptions), 12)
        self.assertTrue(all(len(item) <= 300 for item in result.assumptions))

    def test_verification_can_be_switched_off(self) -> None:
        result, _ = self.run_script(reply(doc=GEOMETRY_BAD), verify_build=False)
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        self.assertFalse(result.metadata.build_verified)

    def test_the_public_payload_carries_the_contract_fields(self) -> None:
        result, _ = self.run_script(reply(doc=STATIC_BAD), reply(doc=GOOD))
        payload = result.to_dict()
        self.assertEqual(payload["outcome"], "generated")
        self.assertIsInstance(payload["assumptions"], list)
        self.assertEqual(
            set(payload["attempts"][0]),
            {"number", "accepted", "stage", "rule_codes", "issues"},
        )
        self.assertEqual(payload["attempts"][0]["stage"], "validation")
        self.assertNotIn("detail", payload)
        self.assertNotIn("plan", json.dumps(payload["metadata"]))


class TestVerificationBuild(ServiceTestCase):
    def test_the_verification_outputs_are_the_ones_the_web_page_builds(self) -> None:
        source = (REPOSITORY / "apps" / "web" / "src" / "api.ts").read_text(
            encoding="utf-8"
        )
        block = re.search(
            r"REQUESTED_OUTPUTS\s*=\s*\[(.*?)\]", source, flags=re.DOTALL
        )
        assert block is not None
        page = tuple(re.findall(r'"([a-z]+)"', block.group(1)))
        self.assertEqual(VERIFICATION_OUTPUTS, page)

    def test_the_pages_follow_up_build_is_a_cache_hit(self) -> None:
        result, _ = self.run_script(reply(doc=GOOD))
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        follow_up = self.service.build_document(
            BuildDocumentRequest(
                document=result.candidate_document, outputs=VERIFICATION_OUTPUTS
            )
        )
        self.assertTrue(follow_up.succeeded)
        self.assertTrue(follow_up.cache_hit)


class TestPromptExamples(ServiceTestCase):
    def test_every_example_is_a_complete_answer_in_schema_order(self) -> None:
        for example in EXAMPLES:
            with self.subTest(request=example["request"]):
                self.assertEqual(tuple(example["answer"]), RESPONSE_FIELDS)
                self.assertEqual(example["answer"]["status"], "document")
                self.assertTrue(example["answer"]["assumptions"])

    def test_every_example_document_validates_and_builds(self) -> None:
        for example in EXAMPLES:
            with self.subTest(request=example["request"]):
                document = example["answer"]["document"]
                validation = self.service.validate_document(document)
                self.assertTrue(validation.valid, validation.error)
                built = self.service.build_document(
                    BuildDocumentRequest(document=document, outputs=("geometry",))
                )
                self.assertTrue(built.succeeded, built.error)

    def test_every_example_appears_in_the_prompt(self) -> None:
        text = system_prompt()
        for example in EXAMPLES:
            self.assertIn(example["request"], text)
            self.assertIn(
                json.dumps(example["answer"], separators=(",", ":")), text
            )

    def test_the_examples_use_union(self) -> None:
        types = {
            feature["type"]
            for example in EXAMPLES
            for feature in example["answer"]["document"]["features"]
        }
        self.assertIn("union", types)


class TestEngineRecipe(ServiceTestCase):
    """The engine recipe the prompt teaches builds, as one solid.

    ``fixtures/detailed_engine.json`` was written by hand to that recipe
    before the recipe went into the prompt -- block, crankcase, oil pan,
    head, port tunnels, plug wells, valves as stacked cylinders, flywheel,
    pulley, oil filter, alternator and mounts -- so a change to the engine
    that breaks the construction the model is taught fails here.
    """

    def test_the_hand_built_engine_validates_and_builds(self) -> None:
        document = json.loads(
            (Path(__file__).parent / "fixtures" / "detailed_engine.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertGreaterEqual(len(document["features"]), 50)
        validation = self.service.validate_document(document)
        self.assertTrue(validation.valid, validation.error)
        built = self.service.build_document(
            BuildDocumentRequest(document=document, outputs=("geometry",))
        )
        self.assertTrue(built.succeeded, built.error)

    def test_the_hand_built_cutaway_engine_validates_and_builds(self) -> None:
        # ``fixtures/cutaway_engine.json``: the same engine with its front
        # half cut away and pistons, connecting rods and a crankshaft added,
        # held to the block by journals through the bulkheads -- the
        # internals recipe the prompt teaches.
        document = json.loads(
            (Path(__file__).parent / "fixtures" / "cutaway_engine.json").read_text(
                encoding="utf-8"
            )
        )
        ids = {feature["id"] for feature in document["features"]}
        for name in (
            "piston_1", "rod_1", "crank_pin_1", "journal_1", "cutaway",
            "intake_camshaft", "exhaust_lobe_4", "cam_tower_1",
            "intake_cam_pulley", "belt_top_run",
        ):
            self.assertIn(name, ids)
        validation = self.service.validate_document(document)
        self.assertTrue(validation.valid, validation.error)
        built = self.service.build_document(
            BuildDocumentRequest(document=document, outputs=("geometry",))
        )
        self.assertTrue(built.succeeded, built.error)

    def test_the_engine_template_is_the_reference_fixture(self) -> None:
        # The engine template is generated in cad_ai.templates.engine; the
        # fixture is the same engine written out. They must not drift apart.
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "cutaway_engine.json").read_text(
                encoding="utf-8"
            )
        )
        engine = ENGINE_TEMPLATE["answer"]["document"]
        self.assertEqual(engine["features"], fixture["features"])
        self.assertIn("## Reference designs", system_prompt())

    def test_the_prompt_carries_the_recipe(self) -> None:
        text = system_prompt()
        for phrase in (
            "### Recipe: an engine",
            "intake port",
            "exhaust port",
            "spark plug wells",
            "**valves**, two per cylinder",
            "starts 2 mm inside the face it attaches to",
            "**Internals (pistons, crankshaft, connecting rods)**",
            "adding any internal part, so the internals stay whole",
            "**camshafts** (overhead, one per valve row)",
            "**timing belt** at the end opposite the flywheel",
        ):
            self.assertIn(phrase, text, msg=phrase)


class TestTemplateLibrary(ServiceTestCase):
    def test_every_template_is_a_complete_answer_that_builds(self) -> None:
        names = set()
        for template in TEMPLATES:
            with self.subTest(template=template["name"]):
                self.assertNotIn(template["name"], names)
                names.add(template["name"])
                self.assertTrue(template["keywords"])
                self.assertTrue(all(k == k.lower() for k in template["keywords"]))
                answer = template["answer"]
                self.assertEqual(tuple(answer), RESPONSE_FIELDS)
                self.assertTrue(answer["assumptions"])
                validation = self.service.validate_document(answer["document"])
                self.assertTrue(validation.valid, validation.error)
                built = self.service.build_document(
                    BuildDocumentRequest(document=answer["document"], outputs=("geometry",))
                )
                self.assertTrue(built.succeeded, built.error)

    def test_every_template_selects_itself_from_its_own_request(self) -> None:
        for template in TEMPLATES:
            with self.subTest(template=template["name"]):
                chosen = [t["name"] for t in select_templates(template["request"])]
                self.assertEqual(chosen[:1], [template["name"]])

    def test_selection_is_by_whole_words_and_bounded(self) -> None:
        self.assertEqual([t["name"] for t in select_templates("an engine")], ["engine"])
        self.assertEqual(select_templates("a 100 x 60 x 10 mm plate"), ())
        self.assertEqual(select_templates("an engineer's square"), ())
        self.assertLessEqual(len(select_templates(" ".join(k for t in TEMPLATES for k in t["keywords"]))), 2)

    def test_an_exclusion_word_drops_a_template(self) -> None:
        # The rocket is not the car engine; a motor bracket is not a motor.
        def names(text: str) -> list:
            return [t["name"] for t in select_templates(text)]

        self.assertEqual(names("a rocket engine"), ["rocket"])
        self.assertEqual(names("a car engine"), ["engine"])
        self.assertEqual(names("a motor mount bracket"), [])
        self.assertEqual(names("a dc motor"), ["electric_motor"])
        self.assertEqual(names("a table lamp"), [])
        self.assertEqual(names("a coffee table"), ["table"])

    def test_no_template_is_attached_to_the_parts_the_prompt_already_teaches(self) -> None:
        for text in (
            "an L-bracket",
            "a mug with a rounded rim",
            "a gaming mouse",
            "a pipe flange with four bolt holes",
            "an M10 hex nut",
            "a pencil holder",
        ):
            with self.subTest(text=text):
                self.assertEqual(select_templates(text), ())

    def test_the_library_covers_more_than_engines(self) -> None:
        self.assertGreaterEqual(len(TEMPLATES), 10)
        self.assertEqual(len(TEMPLATE_MODULES), len(TEMPLATES))

    def test_the_description_comes_first_and_verbatim(self) -> None:
        self.assertEqual(compose_user_text("a plate", ()), "a plate")
        text = compose_user_text("An Engine!", select_templates("An Engine!"))
        self.assertTrue(text.startswith("An Engine!\n"))
        self.assertIn(REFERENCE_MARKER, text)
        self.assertIn(json.dumps(ENGINE_TEMPLATE["answer"], separators=(",", ":")), text)

    def test_generation_attaches_and_records_templates_and_keeps_them_on_a_retry(self) -> None:
        result, model = self.run_script(reply(doc=STATIC_BAD), reply(doc=GOOD), text="an engine")
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        self.assertEqual(result.metadata.templates, ("engine",))
        self.assertIn(REFERENCE_MARKER, model.requests[0].user_text)
        self.assertIn(REFERENCE_MARKER, model.requests[1].user_text)
        self.assertIn(RETRY_MARKER, model.requests[1].user_text)
        self.assertEqual(model.requests[0].system, model.requests[1].system)

    def test_no_template_means_the_text_is_unchanged(self) -> None:
        result, model = self.run_script(reply(doc=GOOD), text="a plate with a hole")
        self.assertEqual(model.requests[0].user_text, "a plate with a hole")
        self.assertEqual(result.metadata.templates, ())

    def test_templates_can_be_switched_off(self) -> None:
        _, model = self.run_script(reply(doc=GOOD), text="an engine", use_templates=False)
        self.assertEqual(model.requests[0].user_text, "an engine")


class TestThePolicy(unittest.TestCase):
    def test_the_prompt_assumes_instead_of_asking(self) -> None:
        text = system_prompt()
        self.assertIn("Always produce a part", text)
        self.assertIn("**No unit stated**: use millimetres", text)
        self.assertIn("convert to millimetres", text)
        self.assertIn("Never ask about units, sizes, positions or orientation", text)
        self.assertNotIn("There is no way to join two solids", text)

    def test_the_prompt_still_refuses_code(self) -> None:
        text = system_prompt()
        self.assertIn("Never output Python", text)
        self.assertIn('answer "unsupported"', text)


class TestResponseSchema(unittest.TestCase):
    def test_every_answer_field_is_required_and_in_order(self) -> None:
        schema = response_schema()
        self.assertEqual(tuple(schema["properties"]), RESPONSE_FIELDS)
        self.assertEqual(tuple(schema["required"]), RESPONSE_FIELDS)
        self.assertEqual(RESPONSE_FIELDS[:4], ("status", "plan", "assumptions", "document"))

    def test_the_api_schema_stays_inside_the_measured_limits(self) -> None:
        # Measured against the structured-output compiler: at most 24
        # optional properties in a schema and about 14 in one object, and a
        # grammar-size limit. With the schema 1.2.0 parameters, six branches
        # (or a nullable document) were refused live; five branches and a
        # non-nullable document compile (prompt 2026-11-13.7). There is
        # essentially no headroom: re-probe live before growing this schema.
        total, largest = optional_property_counts(schema_for_api(response_schema()))
        self.assertLessEqual(total, 4)
        self.assertLessEqual(largest, 2)
        document = response_schema()["properties"]["document"]
        self.assertNotIn("anyOf", document, "a nullable document no longer fits")
        branches = document["properties"]["features"]["items"]["anyOf"]
        self.assertLessEqual(len(branches), 5)
        titles = {branch["title"] for branch in branches}
        self.assertNotIn("through_hole", titles)
        cylinder = next(b for b in branches if b["title"] == "cylinder")
        self.assertIn("top_diameter", cylinder["required"])
        self.assertIn("sides", cylinder["required"])

    def test_subtract_and_union_share_a_branch(self) -> None:
        self.assertIn(("subtract", "union"), feature_groups())

    def test_every_object_forbids_additional_properties(self) -> None:
        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False, node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(schema_for_api(response_schema()))


class TestConfiguration(unittest.TestCase):
    def test_the_default_model_is_haiku_5_5(self) -> None:
        self.assertEqual(DEFAULT_MODELS["anthropic"], "claude-haiku-5-5")
        self.assertEqual(config_from_environment({}).model, "claude-haiku-5-5")

    def test_no_opus_model_is_named_in_the_ai_layer(self) -> None:
        source_root = REPOSITORY / "apps" / "api" / "src" / "cad_ai"
        for path in source_root.glob("*.py"):
            self.assertNotIn("opus", path.read_text(encoding="utf-8").lower(), path)

    def test_the_fallback_credential_name_is_honoured(self) -> None:
        environment = {FALLBACK_API_KEY_VARIABLE: "placeholder"}
        self.assertEqual(credential_variable("anthropic", environment), FALLBACK_API_KEY_VARIABLE)
        self.assertEqual(resolve_provider(environment), "anthropic")
        self.assertTrue(credential_available(environment, "anthropic"))

    def test_the_primary_credential_name_wins(self) -> None:
        environment = {
            "ANTHROPIC_API_KEY": "placeholder",
            FALLBACK_API_KEY_VARIABLE: "placeholder",
        }
        self.assertEqual(credential_variable("anthropic", environment), "ANTHROPIC_API_KEY")

    def test_a_blank_credential_is_absent(self) -> None:
        self.assertIsNone(credential_variable("anthropic", {FALLBACK_API_KEY_VARIABLE: "  "}))

    def test_the_attempt_limit_is_read_and_checked(self) -> None:
        self.assertEqual(config_from_environment({}).max_attempts, DEFAULT_MAX_ATTEMPTS)
        self.assertEqual(
            config_from_environment({"CAD_AI_MAX_ATTEMPTS": "2"}).max_attempts, 2
        )
        for bad in ("0", "-1", "two", "1.5"):
            with self.subTest(value=bad), self.assertRaises(AiConfigurationError):
                config_from_environment({"CAD_AI_MAX_ATTEMPTS": bad})

    def test_effort_is_optional_and_checked(self) -> None:
        self.assertIsNone(config_from_environment({}).effort)
        self.assertEqual(config_from_environment({"CAD_AI_EFFORT": "low"}).effort, "low")
        with self.assertRaises(AiConfigurationError):
            config_from_environment({"CAD_AI_EFFORT": "extreme"})

    def test_the_http_coordinator_passes_the_attempt_limit(self) -> None:
        from cad_api.generation import TextGenerator

        with tempfile.TemporaryDirectory() as directory:
            service = CadApplicationService.local(Path(directory))
            model = ScriptedModel(reply(doc=STATIC_BAD), reply(doc=STATIC_BAD))
            with mock.patch.dict(
                "os.environ", {"CAD_AI_MAX_ATTEMPTS": "2"}, clear=False
            ), mock.patch(
                "cad_api.generation.model_from_environment", return_value=model
            ):
                result = TextGenerator(service).generate("a plate")
        self.assertIs(result.outcome, GenerationOutcome.INVALID_MODEL_OUTPUT)
        self.assertEqual(len(model.requests), 2)


class _RecordingMessages:
    def __init__(self, message: Any) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._message = message

    def stream(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        message = self._message

        class _Stream:
            def __enter__(self) -> "_Stream":
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def get_final_message(self) -> Any:
                return message

        return _Stream()


def _message(text: str = '{"status": "unsupported"}', stop_reason: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        model="claude-haiku-5-5",
        usage=SimpleNamespace(
            input_tokens=12,
            output_tokens=34,
            cache_read_input_tokens=16000,
            cache_creation_input_tokens=0,
        ),
    )


def _tool_message(answer: Any, *, text: str = "") -> Any:
    blocks = []
    if text:
        blocks.append(SimpleNamespace(type="text", text=text))
    blocks.append(SimpleNamespace(type="tool_use", name=ANSWER_TOOL_NAME, input=answer, id="toolu_1"))
    message = _message()
    message.content = blocks
    message.stop_reason = "tool_use"
    return message


class TestAnthropicToolAnswer(unittest.TestCase):
    """The answer through one non-strict tool (default since 2026-11-13.8).

    Measured live: the response-format grammar reached the compiler's size
    limit at 2026-11-13.7; a non-strict tool is not compiled, and six live
    requests (drone, nut, steering wheel, flange, table, engine) were all
    generated, valid and built on the first call in this mode.
    """

    def provider(self, message: Any) -> Any:
        messages = _RecordingMessages(message)
        return AnthropicTextToCadModel(SimpleNamespace(messages=messages), AiConfig()), messages

    def request(self) -> ModelRequest:
        return ModelRequest(system="SYSTEM", user_text="a plate", output_schema=response_schema())

    def test_tool_mode_is_the_default(self) -> None:
        provider, _ = self.provider(_tool_message({}))
        self.assertEqual(provider.answer_mode, "tool")

    def test_one_tool_carries_the_answer_schema_and_no_response_format(self) -> None:
        provider, messages = self.provider(_tool_message({"status": "unsupported"}))
        provider.generate(self.request())
        (call,) = messages.calls
        (tool,) = call["tools"]
        self.assertEqual(tool["name"], ANSWER_TOOL_NAME)
        self.assertEqual(tool["input_schema"], schema_for_api(response_schema()))
        self.assertNotIn("strict", tool)  # non-strict: not compiled into a grammar
        self.assertEqual(call["tool_choice"], {"type": "auto"})
        self.assertNotIn("output_config", call)
        self.assertNotIn("thinking", call)

    def test_the_answer_is_the_tool_input_passed_on_unedited(self) -> None:
        answer = {"status": "unsupported", "plan": "", "issues": ["not a part"]}
        provider, _ = self.provider(_tool_message(answer, text="Here it is."))
        response = provider.generate(self.request())
        self.assertEqual(json.loads(response.text), answer)
        self.assertFalse(response.structured_output)
        self.assertEqual(response.stop_reason, "tool_use")

    def test_no_tool_call_falls_back_to_the_text(self) -> None:
        # The generation layer then reports the text unusable and re-asks;
        # the provider never guesses a document out of prose.
        provider, _ = self.provider(_message(text="I would build a plate."))
        self.assertEqual(provider.generate(self.request()).text, "I would build a plate.")

    def test_the_mode_is_chosen_by_the_environment(self) -> None:
        from cad_ai.provider import ProviderNotConfigured

        env = {"CAD_ANTHROPIC_API_KEY": "sk-test-placeholder"}
        with mock.patch.dict(os.environ, {**env, ANSWER_MODE_VARIABLE: "schema"}):
            self.assertEqual(AnthropicTextToCadModel.from_environment(AiConfig()).answer_mode, "schema")
        with mock.patch.dict(os.environ, {**env, ANSWER_MODE_VARIABLE: "grammar"}):
            with self.assertRaises(ProviderNotConfigured):
                AnthropicTextToCadModel.from_environment(AiConfig())

    def test_the_prompt_names_the_tool(self) -> None:
        self.assertIn(f"`{ANSWER_TOOL_NAME}` tool is offered", system_prompt().replace("\n", " "))


class TestAnthropicRequest(unittest.TestCase):
    def provider(self, message: Any, **config: Any):
        messages = _RecordingMessages(message)
        client = SimpleNamespace(messages=messages)
        return AnthropicTextToCadModel(client, AiConfig(**config), answer_mode="schema"), messages

    def request(self) -> ModelRequest:
        return ModelRequest(
            system="SYSTEM", user_text="a plate", output_schema=response_schema()
        )

    def test_the_system_prompt_is_one_cached_block(self) -> None:
        provider, messages = self.provider(_message())
        provider.generate(self.request())
        (call,) = messages.calls
        self.assertEqual(
            call["system"],
            [{"type": "text", "text": "SYSTEM", "cache_control": dict(CACHE_CONTROL)}],
        )
        self.assertNotIn("cache_control", call)

    def test_no_sampling_or_thinking_parameter_is_sent(self) -> None:
        provider, messages = self.provider(_message())
        provider.generate(self.request())
        (call,) = messages.calls
        for name in ("thinking", "temperature", "top_p", "top_k", "tools", "tool_choice"):
            self.assertNotIn(name, call)
        self.assertNotIn("effort", call["output_config"])
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")

    def test_effort_is_sent_only_when_configured(self) -> None:
        provider, messages = self.provider(_message(), effort="low")
        provider.generate(self.request())
        self.assertEqual(messages.calls[0]["output_config"]["effort"], "low")

    def test_cache_counters_are_reported(self) -> None:
        provider, _ = self.provider(_message())
        response = provider.generate(self.request())
        self.assertEqual(response.usage["cache_read_input_tokens"], 16000)
        self.assertEqual(response.usage["cache_creation_input_tokens"], 0)

    def test_a_refusal_is_a_refused_provider_error(self) -> None:
        provider, _ = self.provider(_message(text="", stop_reason="refusal"))
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.REFUSED)

    def test_the_fallback_credential_reaches_the_client(self) -> None:
        with mock.patch.dict(
            "os.environ", {FALLBACK_API_KEY_VARIABLE: "placeholder-key"}, clear=True
        ), mock.patch("anthropic.Anthropic") as client_class:
            AnthropicTextToCadModel.from_environment(AiConfig())
        self.assertEqual(client_class.call_args.kwargs["api_key"], "placeholder-key")


if __name__ == "__main__":
    unittest.main()
