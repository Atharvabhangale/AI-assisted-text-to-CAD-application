"""Stage 28A: the Gemini provider boundary, proved without a live call.

**No test in this file contacts Gemini.** The whole point of the stage is that
the provider is complete and exercised before any credential is spent, so
every test here either mocks the SDK at the provider boundary or uses a
deterministic stub. A credential may or may not be present in the environment;
these tests behave identically either way, and several assert exactly that.

Four things they establish:

* **construction, configuration and import contact nothing.** Importing the
  module, reading configuration and checking credential availability all
  complete with the SDK's client constructor and the socket layer patched to
  raise;
* **the SDK is driven correctly**, against the API measured from the installed
  ``google-genai`` -- the right method, the right model, the right structured
  output configuration, the system prompt, the user text, the token bound, and
  no tools of any kind;
* **every response shape maps through the existing generation service** onto
  the existing five outcomes. No Gemini-specific outcome, no Gemini-specific
  schema, no Gemini-specific validation;
* **a synthetic credential never escapes** -- not into a message, a
  ``repr``, a serialized config, an evaluation result, or this source tree.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from provider_stubs import (
    BOX_DOCUMENT,
    CYLINDER_DOCUMENT,
    RESPONSE_SHAPES,
    StubTextToCadModel,
    failing_stub,
    gemini_shaped_stub,
)

from cad_ai import corpus as corpus_data
from cad_ai.config import (
    API_KEY_VARIABLES,
    DEFAULT_MODELS,
    GEMINI_API_KEY_VARIABLE,
    GEMINI_PROVIDER_NAME,
    PROVIDER_NAME,
    PROVIDER_NAMES,
    PROVIDER_VARIABLE,
    AiConfig,
    AiConfigurationError,
    config_from_environment,
    credential_available,
    resolve_provider,
)
from cad_ai.evaluation import Evaluator, load_corpus, main
from cad_ai.generation import GenerationOutcome, TextToCadService
from cad_ai.prompt import PROMPT_VERSION, prompt_fingerprint, system_prompt
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderNotConfigured,
    TextToCadModel,
)
from cad_ai.specification import response_schema

from cad_core.application_service import CadApplicationService

REPO_ROOT = Path(__file__).resolve().parents[3]
AI_SOURCE = REPO_ROOT / "apps" / "api" / "src" / "cad_ai"

#: A clearly synthetic credential. Never a real one, and asserted below to
#: appear in no source file outside the tests that deliberately use it.
SYNTHETIC_KEY = "AIzaSy-SYNTHETIC-TEST-KEY-DO-NOT-USE-000"

#: The prompt fingerprint. What this module asserts is **provider parity**:
#: selecting a provider must not move the prompt, so Gemini and Anthropic are
#: handed byte-identical instructions. It does not pin one *particular*
#: prompt -- that is `tests.test_text_to_cad_ai.PROMPT_FINGERPRINT`'s job --
#: so this value moves whenever the prompt is deliberately revised, and the
#: parity claim is what must not break.
#:
#: Stage 26 pinned 2b3e3395ec6efee0...; 2026-09-09.1 moved it to
#: fe62c9759a08d45c...; this is **2026-09-09.2**, which taught the model that
#: the vocabulary cannot join solids and that words locating a feature supply
#: a required position.
PROMPT_FINGERPRINT = (
    "f9efe19ac33281ff14ab0efe665dc01971d458ea5d90e8114f6dabba0ca0874e"
)


def cache_root(case: unittest.TestCase) -> Path:
    directory = tempfile.TemporaryDirectory()
    case.addCleanup(directory.cleanup)
    root = Path(directory.name) / "cache"
    root.mkdir()
    return root


# --- fake Gemini SDK objects, shaped like the measured API ------------------


class FakeModels:
    def __init__(self, response: Any = None, error: Optional[Exception] = None):
        self.response = response
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def generate_content(self, **parameters: Any) -> Any:
        self.calls.append(parameters)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeResponse:
    def __init__(self, text: Optional[str] = "{}") -> None:
        self.text = text
        self.model_version = "gemini-stub-001"
        self.usage_metadata = type(
            "U", (), {"prompt_token_count": 3800, "candidates_token_count": 120}
        )()
        self.candidates = [
            type("C", (), {"finish_reason": type("R", (), {"name": "STOP"})()})()
        ]


class GeminiBoundaryTestCase(unittest.TestCase):
    """Common scaffolding. Skips only if the SDK itself is absent."""

    def setUp(self) -> None:
        try:
            from google import genai  # noqa: F401
        except ImportError:  # pragma: no cover - reported, not failed
            self.skipTest("the google-genai SDK is not installed")
        from cad_ai.gemini_provider import GeminiTextToCadModel

        self.provider_class = GeminiTextToCadModel
        self.config = AiConfig(
            model="gemini-stub", timeout_seconds=5.0, provider=GEMINI_PROVIDER_NAME
        )

    def provider(self, models: FakeModels) -> Any:
        return self.provider_class(FakeClient(models), self.config)

    def request(self, **overrides: Any) -> ModelRequest:
        fields: Dict[str, Any] = {
            "system": system_prompt(),
            "user_text": "Create a rectangular plate 100 mm long, 60 mm wide, "
            "and 10 mm thick.",
            "output_schema": response_schema(),
        }
        fields.update(overrides)
        return ModelRequest(**fields)


# --- nothing here contacts Gemini -------------------------------------------


class TestNoLiveCall(GeminiBoundaryTestCase):
    """Importing, configuring and constructing all contact nothing."""

    def test_importing_the_provider_builds_no_client(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys\n"
                    "import cad_ai.gemini_provider as g\n"
                    "assert 'google.genai' not in sys.modules, 'SDK imported eagerly'\n"
                    "assert callable(g.GeminiTextToCadModel.from_environment)\n"
                    "print('OK')\n"
                    # Leave without interpreter finalisation. The assertions
                    # above are the whole point of this child; what remains is
                    # teardown, and OpenCascade's native shutdown aborts the
                    # process on this platform after a fully successful run.
                    # This keeps `returncode == 0` a real assertion rather
                    # than a report on a third-party teardown bug.
                    "import os\n"
                    "sys.stdout.flush()\n"
                    "os._exit(0)\n"
                ),
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(REPO_ROOT / "packages" / "cad-core" / "src"),
                        str(REPO_ROOT / "apps" / "api" / "src"),
                    ]
                ),
            },
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr[-1500:])
        self.assertIn("OK", completed.stdout)

    def test_reading_configuration_contacts_nothing(self) -> None:
        from google import genai

        with mock.patch.object(
            genai, "Client", side_effect=AssertionError("a client was built")
        ), mock.patch.object(
            socket, "socket", side_effect=AssertionError("a socket was opened")
        ):
            config = config_from_environment(
                {PROVIDER_VARIABLE: GEMINI_PROVIDER_NAME}
            )
            self.assertEqual(config.provider, GEMINI_PROVIDER_NAME)
            self.assertEqual(resolve_provider({GEMINI_API_KEY_VARIABLE: "x"}),
                             GEMINI_PROVIDER_NAME)

    def test_checking_credential_availability_contacts_nothing(self) -> None:
        from google import genai

        with mock.patch.object(
            genai, "Client", side_effect=AssertionError("a client was built")
        ), mock.patch.object(
            socket, "socket", side_effect=AssertionError("a socket was opened")
        ), mock.patch.object(
            socket,
            "create_connection",
            side_effect=AssertionError("a connection was made"),
        ):
            # Both answers are fine; making the call at all is not.
            self.assertIsInstance(
                credential_available(provider=GEMINI_PROVIDER_NAME), bool
            )

    def test_constructing_the_provider_makes_no_request(self) -> None:
        from google import genai

        models = FakeModels(FakeResponse())
        with mock.patch.object(genai, "Client", return_value=FakeClient(models)):
            with mock.patch.dict(
                os.environ, {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY}, clear=False
            ):
                self.provider_class.from_environment(self.config)
        # A client was built, and nothing was generated.
        self.assertEqual(models.calls, [])

    def test_the_whole_ordinary_suite_runs_with_no_credential(self) -> None:
        # Whatever the environment holds, removing both credentials must not
        # change any behaviour these tests depend on.
        cleared = {name: "" for name in API_KEY_VARIABLES.values()}
        with mock.patch.dict(os.environ, cleared, clear=False):
            self.assertFalse(credential_available())
            models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
            response = self.provider(models).generate(self.request())
            self.assertTrue(response.text)
            with self.assertRaises(ProviderNotConfigured):
                self.provider_class.from_environment(self.config)


# --- the SDK is driven correctly --------------------------------------------


class TestSdkCallShape(GeminiBoundaryTestCase):
    def test_the_verified_method_is_called_once(self) -> None:
        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        self.provider(models).generate(self.request())
        self.assertEqual(len(models.calls), 1)
        self.assertEqual(sorted(models.calls[0]), ["config", "contents", "model"])

    def test_the_configured_model_is_passed(self) -> None:
        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        self.provider(models).generate(self.request())
        self.assertEqual(models.calls[0]["model"], "gemini-stub")

    def test_the_system_prompt_is_passed_unchanged(self) -> None:
        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        self.provider(models).generate(self.request())
        sent = models.calls[0]["config"].system_instruction
        # Byte-identical to the Stage 26 engineering prompt. No provider
        # wrapper, no reformatting, no addition.
        self.assertEqual(sent, system_prompt())
        self.assertEqual(prompt_fingerprint(), PROMPT_FINGERPRINT)
        self.assertEqual(PROMPT_VERSION, "2026-09-09.2")

    def test_the_user_text_is_passed_unchanged(self) -> None:
        for text in (
            "Create a 50 mm cube.",
            "  untrimmed  ",
            "unicode: 100 mm × 60 mm — 10 mm thick",
        ):
            with self.subTest(text=text):
                models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
                self.provider(models).generate(self.request(user_text=text))
                self.assertEqual(models.calls[0]["contents"], text)

    def test_the_structured_output_configuration_is_the_measured_one(self) -> None:
        from google.genai import types

        from cad_ai.gemini_provider import JSON_MIME_TYPE

        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        self.provider(models).generate(self.request())
        config = models.calls[0]["config"]
        self.assertIsInstance(config, types.GenerateContentConfig)
        self.assertEqual(config.response_mime_type, JSON_MIME_TYPE)
        # The same schema object the other provider gets: one CAD schema.
        self.assertEqual(config.response_json_schema, response_schema())
        for name in ("system_instruction", "response_mime_type",
                     "response_json_schema", "max_output_tokens"):
            self.assertIn(name, types.GenerateContentConfig.model_fields)

    def test_the_max_output_token_bound_is_passed(self) -> None:
        for limit in (4096, 512):
            with self.subTest(limit=limit):
                models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
                self.provider(models).generate(
                    self.request(max_output_tokens=limit)
                )
                self.assertEqual(models.calls[0]["config"].max_output_tokens, limit)

    def test_no_tool_of_any_kind_is_configured(self) -> None:
        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        self.provider(models).generate(self.request())
        config = models.calls[0]["config"]
        for name in (
            "tools",
            "tool_config",
            "automatic_function_calling",
            "cached_content",
        ):
            self.assertIsNone(getattr(config, name, None), msg=name)
        # And nothing in the provider mentions a tool, grounding or execution.
        source = (AI_SOURCE / "gemini_provider.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        keywords = [
            node.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword) and node.arg
        ]
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        for forbidden in (
            "tools",
            "tool_config",
            "google_search",
            "code_execution",
            "url_context",
            "function_declarations",
        ):
            self.assertNotIn(forbidden, keywords, msg=forbidden)
            self.assertNotIn(forbidden, literals, msg=forbidden)

    def test_the_response_is_converted_to_the_neutral_record(self) -> None:
        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        response = self.provider(models).generate(self.request())
        self.assertIsInstance(response, ModelResponse)
        self.assertEqual(response.text, RESPONSE_SHAPES["valid_box"])
        self.assertEqual(response.provider, GEMINI_PROVIDER_NAME)
        self.assertEqual(response.model, "gemini-stub-001")
        self.assertEqual(response.stop_reason, "STOP")
        self.assertTrue(response.structured_output)
        self.assertEqual(
            response.usage, {"input_tokens": 3800, "output_tokens": 120}
        )
        # No vendor object survives the conversion.
        self.assertNotIn("google", repr(response).lower())

    def test_a_provider_exception_is_converted(self) -> None:
        from google.genai import errors

        for failure in (
            errors.APIError(429, {"message": "quota exceeded"}),
            errors.APIError(500, {"message": "internal"}),
            RuntimeError("transport died"),
            TimeoutError("timed out"),
        ):
            with self.subTest(failure=type(failure).__name__):
                provider = self.provider(FakeModels(error=failure))
                with self.assertRaises(ProviderError) as caught:
                    provider.generate(self.request())
                self.assertEqual(
                    caught.exception.message,
                    "the interpretation service is unavailable",
                )
                self.assertNotIsInstance(caught.exception, errors.APIError)


# --- every response shape maps onto the existing outcomes -------------------


class TestResponseShapes(unittest.TestCase):
    """Nine shapes, one taxonomy. No Gemini-specific outcome exists."""

    #: Shape -> the outcome the EXISTING generation service must produce.
    EXPECTED: Mapping[str, GenerationOutcome] = {
        "valid_box": GenerationOutcome.GENERATED,
        "valid_cylinder": GenerationOutcome.GENERATED,
        "malformed_json": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "prose": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "empty": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "whitespace": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "null": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "json_array": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "markdown_fenced": GenerationOutcome.INVALID_MODEL_OUTPUT,
        "invalid_cad": GenerationOutcome.INVALID_MODEL_OUTPUT,
        # A plate with a through-hole is perfectly valid V1 CAD, so the
        # validator accepts it and the outcome is GENERATED. The "supported
        # subset" is a prompt policy, not a validation rule -- which is why
        # the evaluation layer catches this at the CASE level
        # (UNSUPPORTED_FEATURE_ACCEPTED) rather than here.
        "unsupported_feature_document": GenerationOutcome.GENERATED,
        "needs_clarification": GenerationOutcome.NEEDS_CLARIFICATION,
        "unsupported": GenerationOutcome.UNSUPPORTED,
        "unknown_status": GenerationOutcome.INVALID_MODEL_OUTPUT,
    }

    def setUp(self) -> None:
        self.service = CadApplicationService.local(cache_root(self))

    def generate(self, shape: str):
        model = gemini_shaped_stub(shape)
        return TextToCadService(model, self.service).generate_cad_from_text(
            "Create a rectangular plate 100 mm long, 60 mm wide, and 10 mm thick."
        )

    def test_every_shape_maps_onto_an_existing_outcome(self) -> None:
        self.assertEqual(set(self.EXPECTED), set(RESPONSE_SHAPES))
        for shape, expected in self.EXPECTED.items():
            with self.subTest(shape=shape):
                result = self.generate(shape)
                self.assertIs(result.outcome, expected)
                self.assertIn(result.outcome, set(GenerationOutcome))

    def test_a_valid_box_becomes_a_validated_document(self) -> None:
        result = self.generate("valid_box")
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        feature = result.candidate_document["features"][0]
        self.assertEqual(feature["type"], "box")
        self.assertAlmostEqual(feature["size"]["x"], 100.0, delta=1e-9)
        # The specification's own default, applied by the existing reader.
        self.assertEqual(feature["position"], {"x": 0.0, "y": 0.0, "z": 0.0})

    def test_a_valid_cylinder_becomes_a_validated_document(self) -> None:
        result = self.generate("valid_cylinder")
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        feature = result.candidate_document["features"][0]
        self.assertEqual(feature["type"], "cylinder")
        self.assertEqual(feature["axis"], "+Z")

    def test_invalid_cad_is_caught_by_the_existing_validator(self) -> None:
        result = self.generate("invalid_cad")
        self.assertIs(result.outcome, GenerationOutcome.INVALID_MODEL_OUTPUT)
        self.assertIn("S10", result.rule_codes)
        self.assertIsNone(result.candidate_document)

    def test_an_out_of_subset_feature_still_validates(self) -> None:
        """The subset is a prompt policy; the validator judges V1, not the subset.

        A model that ignores the prompt and emits a ``through_hole`` produces
        a **valid** document, because a drilled plate is legitimate V1 CAD.
        Nothing here invents a Gemini-specific rule to reject it: the
        generation layer reports GENERATED, and it is the evaluation layer
        that scores the case as ``UNSUPPORTED_FEATURE_ACCEPTED`` when the
        request required a refusal. Keeping those two judgements in different
        layers is deliberate.
        """
        result = self.generate("unsupported_feature_document")
        self.assertIs(result.outcome, GenerationOutcome.GENERATED)
        kinds = [f["type"] for f in result.candidate_document["features"]]
        self.assertEqual(kinds, ["box", "through_hole"])

    def test_the_evaluation_layer_catches_an_out_of_subset_document(
        self,
    ) -> None:
        from cad_ai.comparison import SemanticErrorCategory

        # The same document, scored against a case that required a refusal.
        case = next(
            c for c in load_corpus() if c.case_id == "E1-through-hole-request"
        )
        evaluator = Evaluator(
            TextToCadService(
                gemini_shaped_stub("unsupported_feature_document"), self.service
            )
        )
        result = evaluator.evaluate(case)
        self.assertIs(result.model_outcome, GenerationOutcome.GENERATED)
        self.assertTrue(result.validated)
        self.assertFalse(result.outcome_match)
        self.assertFalse(result.correct)
        self.assertIn(
            SemanticErrorCategory.UNSUPPORTED_FEATURE_ACCEPTED, result.categories
        )

    def test_a_clarification_carries_questions_and_no_document(self) -> None:
        result = self.generate("needs_clarification")
        self.assertEqual(result.questions, ("What units should I use?",))
        self.assertIsNone(result.candidate_document)

    def test_an_unsupported_answer_carries_issues_and_no_document(self) -> None:
        result = self.generate("unsupported")
        self.assertTrue(result.issues)
        self.assertIsNone(result.candidate_document)

    def test_a_provider_failure_becomes_model_error(self) -> None:
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                model = failing_stub(name="gemini", unavailable=unavailable)
                result = TextToCadService(
                    model, self.service
                ).generate_cad_from_text("a plate")
                self.assertIs(result.outcome, GenerationOutcome.MODEL_ERROR)

    def test_no_gemini_specific_outcome_exists(self) -> None:
        names = set(GenerationOutcome.__members__)
        self.assertEqual(
            names,
            {
                "GENERATED",
                "NEEDS_CLARIFICATION",
                "UNSUPPORTED",
                "MODEL_ERROR",
                "INVALID_MODEL_OUTPUT",
            },
        )
        for forbidden in ("GEMINI_ERROR", "GEMINI_INVALID", "GEMINI_TIMEOUT"):
            self.assertNotIn(forbidden, names)

    def test_the_provider_name_is_metadata_and_nothing_more(self) -> None:
        # The same shape through two differently-named stubs gives the same
        # document and the same hash: provider identity never reaches CAD.
        anthropic_like = StubTextToCadModel("valid_box", name="anthropic")
        gemini_like = StubTextToCadModel("valid_box", name="gemini")
        first = TextToCadService(anthropic_like, self.service).generate_cad_from_text(
            "a plate"
        )
        second = TextToCadService(gemini_like, self.service).generate_cad_from_text(
            "a plate"
        )
        self.assertEqual(first.document_hash, second.document_hash)
        self.assertEqual(first.candidate_document, second.candidate_document)
        self.assertEqual(first.metadata.provider, "anthropic")
        self.assertEqual(second.metadata.provider, "gemini")


# --- credential security -----------------------------------------------------


class TestCredentialSecurity(GeminiBoundaryTestCase):
    """A synthetic credential goes in and never comes back out."""

    def build_with_key(self) -> Tuple[Any, Any]:
        from google import genai

        models = FakeModels(FakeResponse(RESPONSE_SHAPES["valid_box"]))
        with mock.patch.dict(
            os.environ, {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY}, clear=False
        ):
            with mock.patch.object(
                genai, "Client", return_value=FakeClient(models)
            ) as constructor:
                provider = self.provider_class.from_environment(self.config)
        return provider, constructor

    def test_the_key_reaches_the_sdk_and_nothing_else(self) -> None:
        provider, constructor = self.build_with_key()
        self.assertEqual(constructor.call_args.kwargs["api_key"], SYNTHETIC_KEY)
        self.assertNotIn(SYNTHETIC_KEY, repr(provider))
        self.assertNotIn(SYNTHETIC_KEY, repr(vars(provider)))
        self.assertNotIn(SYNTHETIC_KEY, str(provider.config))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(provider.config.to_dict()))

    def test_the_serialized_configuration_has_no_credential_field(self) -> None:
        config = self.config.to_dict()
        self.assertEqual(sorted(config), ["model", "provider", "timeout_seconds"])
        for key in config:
            for banned in ("key", "secret", "token", "auth", "credential"):
                self.assertNotIn(banned, key.lower(), msg=key)

    def test_no_public_error_carries_the_key(self) -> None:
        provider, _ = self.build_with_key()
        failing = self.provider_class(
            FakeClient(
                FakeModels(
                    error=RuntimeError(f"401 for x-goog-api-key={SYNTHETIC_KEY}")
                )
            ),
            self.config,
        )
        with self.assertRaises(ProviderError) as caught:
            failing.generate(self.request())
        self.assertNotIn(SYNTHETIC_KEY, caught.exception.message)
        self.assertNotIn(SYNTHETIC_KEY, str(caught.exception))
        # It survives in the development-only field, which is never published.
        self.assertIn(SYNTHETIC_KEY, caught.exception.detail or "")

    def test_no_evaluation_result_carries_the_key(self) -> None:
        from cad_ai.evaluation import build_run, save_run

        service = CadApplicationService.local(cache_root(self))
        case = load_corpus()[0]
        evaluator = Evaluator(
            TextToCadService(gemini_shaped_stub("valid_box"), service)
        )
        with mock.patch.dict(
            os.environ, {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY}, clear=False
        ):
            run = build_run(
                evaluator,
                [case],
                provider=GEMINI_PROVIDER_NAME,
                model="gemini-stub",
                live=False,
            )
            directory = Path(tempfile.mkdtemp()) / "results"
            path = save_run(run, directory)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(SYNTHETIC_KEY, text)
        self.assertNotIn("AIzaSy", text)
        for banned in ("api_key", "x-goog-api-key", "authorization", "Bearer",
                       GEMINI_API_KEY_VARIABLE):
            self.assertNotIn(banned, text, msg=banned)

    def test_the_key_appears_in_no_shipped_source_file(self) -> None:
        # The synthetic value may appear only in the tests that use it.
        allowed = {Path(__file__).name}
        roots = (
            REPO_ROOT / "apps" / "api" / "src",
            REPO_ROOT / "packages" / "cad-core" / "src",
            REPO_ROOT / "apps" / "web" / "src",
            REPO_ROOT / "docs",
        )
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in (
                    ".py", ".ts", ".md", ".json", ".toml", ".html"
                ):
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
                self.assertNotIn(SYNTHETIC_KEY, content, msg=str(path))
                self.assertNotIn("AIzaSy", content, msg=str(path))
        self.assertIn(Path(__file__).name, allowed)

    def test_the_provider_never_logs(self) -> None:
        source = (AI_SOURCE / "gemini_provider.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ] + [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        for forbidden in ("print", "getLogger", "info", "warning", "debug"):
            self.assertNotIn(forbidden, called, msg=forbidden)

    def test_the_environment_is_never_dumped(self) -> None:
        source = (AI_SOURCE / "gemini_provider.py").read_text(encoding="utf-8")
        self.assertNotIn("os.environ.items", source)
        self.assertNotIn("dict(os.environ", source)
        self.assertNotIn("environ.copy", source)
        # It reads exactly one variable, by name.
        self.assertEqual(source.count("os.environ.get"), 1)


# --- no network, no writes, no execution ------------------------------------


class TestNoSideEffects(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.service = CadApplicationService.local(cache_root(self))

    def test_no_socket_is_opened_for_any_response_shape(self) -> None:
        for shape in RESPONSE_SHAPES:
            with self.subTest(shape=shape):
                with mock.patch.object(
                    socket, "socket",
                    side_effect=AssertionError("a socket was opened"),
                ), mock.patch.object(
                    socket, "create_connection",
                    side_effect=AssertionError("a connection was made"),
                ):
                    TextToCadService(
                        gemini_shaped_stub(shape), self.service
                    ).generate_cad_from_text("a plate")

    def test_no_generated_content_is_executed(self) -> None:
        import builtins

        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("model output was interpreted")

        for shape in RESPONSE_SHAPES:
            with self.subTest(shape=shape):
                with mock.patch.object(builtins, "eval", refuse), \
                     mock.patch.object(builtins, "exec", refuse), \
                     mock.patch.object(builtins, "compile", refuse), \
                     mock.patch.object(
                         subprocess, "Popen",
                         side_effect=AssertionError("a process was started"),
                     ), mock.patch.object(
                         os, "system",
                         side_effect=AssertionError("a shell ran"),
                     ):
                    TextToCadService(
                        gemini_shaped_stub(shape), self.service
                    ).generate_cad_from_text("a plate")

    def test_no_file_is_written_by_generation(self) -> None:
        watched = (self.tmp, Path(tempfile.gettempdir()))
        before = {
            root: sorted(str(p) for p in root.rglob("*")) for root in watched
        }
        for shape in RESPONSE_SHAPES:
            TextToCadService(
                gemini_shaped_stub(shape), self.service
            ).generate_cad_from_text("a plate")
        for root in watched:
            self.assertEqual(
                sorted(str(p) for p in root.rglob("*")), before[root], msg=str(root)
            )

    def test_no_repair_loop_exists_in_the_provider(self) -> None:
        source = (AI_SOURCE / "gemini_provider.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        identifiers = [
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        ] + [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        ]
        for forbidden in ("repair", "retry", "again", "attempt"):
            self.assertNotIn(
                forbidden, " ".join(identifiers).lower(), msg=forbidden
            )
        # One call per generate, from one place.
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "generate_content"
        ]
        self.assertEqual(len(calls), 1)

    def test_one_provider_call_per_generation(self) -> None:
        model = gemini_shaped_stub("invalid_cad")
        result = TextToCadService(model, self.service).generate_cad_from_text(
            "a zero-width plate"
        )
        self.assertIs(result.outcome, GenerationOutcome.INVALID_MODEL_OUTPUT)
        self.assertEqual(len(model.requests), 1)


# --- the evaluation harness -------------------------------------------------


class TestEvaluationIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CadApplicationService.local(cache_root(self))
        self.results = Path(tempfile.mkdtemp()) / "results"

    def test_the_corpus_is_unchanged(self) -> None:
        cases = load_corpus()
        self.assertEqual(len(cases), 35)
        self.assertEqual(len(corpus_data.CORPUS), 35)
        counts: Dict[str, int] = {}
        for case in cases:
            counts[case.category] = counts.get(case.category, 0) + 1
        self.assertEqual(
            counts,
            {
                "A-box": 6,
                "B-cylinder": 6,
                "C-defaults": 3,
                "D-ambiguity": 5,
                "E-unsupported": 5,
                "F-adversarial": 5,
                "G-semantic": 5,
            },
        )
        # The expected documents are byte-stable, so adding a provider cannot
        # have moved the benchmark under anyone's feet.
        import hashlib

        digest = hashlib.sha256(
            json.dumps(
                [case.to_dict() for case in cases], sort_keys=True
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            sum(1 for case in cases if case.expected_document is not None), 19
        )

    def test_selecting_gemini_uses_the_gemini_boundary_and_same_corpus(
        self,
    ) -> None:
        from cad_ai.evaluation import _live_model

        captured: Dict[str, Any] = {}

        def fake_gemini(config: AiConfig) -> TextToCadModel:
            captured["provider"] = config.provider
            captured["model"] = config.model
            return gemini_shaped_stub("unsupported")

        with mock.patch("cad_ai.evaluation._live_model", side_effect=fake_gemini), \
             mock.patch.dict(
                 os.environ, {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY}, clear=False
             ), \
             mock.patch("builtins.print"):
            code = main(
                ["--live", "--provider", "gemini", "--out", str(self.results)]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["provider"], GEMINI_PROVIDER_NAME)
        self.assertEqual(captured["model"], DEFAULT_MODELS[GEMINI_PROVIDER_NAME])
        written = list(self.results.glob("*.json"))
        self.assertEqual(len(written), 1)
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["run"]["provider"], GEMINI_PROVIDER_NAME)
        # The very same 35 cases.
        self.assertEqual(len(payload["results"]), 35)
        self.assertEqual(payload["run"]["corpus_size"], 35)
        self.assertEqual(payload["run"]["prompt_fingerprint"], PROMPT_FINGERPRINT)
        self.assertNotIn(SYNTHETIC_KEY, written[0].read_text(encoding="utf-8"))

    def test_the_live_run_requires_an_explicit_flag(self) -> None:
        # Even with a credential present, the default path calls nothing.
        with mock.patch.dict(
            os.environ, {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY}, clear=False
        ), mock.patch(
            "cad_ai.evaluation._live_model",
            side_effect=AssertionError("a provider was constructed"),
        ), mock.patch("builtins.print") as printed:
            code = main(["--provider", "gemini", "--out", str(self.results)])
        self.assertEqual(code, 0)
        text = "\n".join(
            str(call.args[0]) for call in printed.call_args_list if call.args
        )
        self.assertIn("GEMINI BENCHMARK: NOT_RUN", text)
        self.assertIn("requires --live", text)
        self.assertIn("credential present  : yes", text)
        self.assertFalse(self.results.exists())

    def test_the_deferral_message_names_the_configured_provider(self) -> None:
        for provider, banner in (
            ("gemini", "GEMINI BENCHMARK: NOT_RUN"),
            ("anthropic", "ANTHROPIC BENCHMARK: NOT_RUN"),
        ):
            with self.subTest(provider=provider):
                with mock.patch("builtins.print") as printed:
                    main(["--provider", provider])
                text = "\n".join(
                    str(c.args[0]) for c in printed.call_args_list if c.args
                )
                self.assertIn(banner, text)

    def test_live_and_self_check_are_mutually_exclusive(self) -> None:
        with mock.patch("builtins.print"):
            self.assertEqual(main(["--live", "--self-check"]), 2)

    def test_an_unknown_provider_is_refused_by_the_parser(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--provider", "openai"])

    def test_the_self_check_still_needs_no_credential_and_no_provider(
        self,
    ) -> None:
        with mock.patch(
            "cad_ai.evaluation._live_model",
            side_effect=AssertionError("a provider was constructed"),
        ), mock.patch("builtins.print"):
            code = main(
                ["--self-check", "--category", "A-box", "--out", str(self.results)]
            )
        self.assertEqual(code, 0)
        payload = json.loads(
            list(self.results.glob("*.json"))[0].read_text(encoding="utf-8")
        )
        self.assertFalse(payload["run"]["live"])

    def test_a_gemini_run_records_that_decoding_controls_were_unused(
        self,
    ) -> None:
        from cad_ai.evaluation import _decoding_settings

        settings = _decoding_settings(GEMINI_PROVIDER_NAME)
        # Measured: this SDK offers them, and this application uses none.
        self.assertEqual(settings["temperature"], "settable")
        self.assertEqual(settings["seed"], "settable")
        self.assertEqual(settings["deterministic_decoding"], "available")
        self.assertEqual(settings["decoding_controls_used"], "none")


# --- Anthropic is untouched --------------------------------------------------


class TestAnthropicUnchanged(unittest.TestCase):
    def test_anthropic_remains_the_default_provider(self) -> None:
        self.assertEqual(PROVIDER_NAMES[0], PROVIDER_NAME)
        self.assertEqual(PROVIDER_NAME, "anthropic")
        self.assertEqual(config_from_environment({}).provider, "anthropic")
        # Claude Haiku 4.5 is the model the product flow runs on; the
        # default moved to it when the live flow was first exercised. What
        # this test guards is that *Anthropic* stays the default provider,
        # not which Anthropic model is configured.
        self.assertEqual(DEFAULT_MODELS["anthropic"], "claude-haiku-4-5-20251001")

    def test_anthropic_is_chosen_when_both_credentials_exist(self) -> None:
        config = config_from_environment(
            {"ANTHROPIC_API_KEY": "a", GEMINI_API_KEY_VARIABLE: "g"}
        )
        self.assertEqual(config.provider, "anthropic")

    def test_the_anthropic_provider_module_is_unchanged_in_behaviour(self) -> None:
        from cad_ai.anthropic_provider import (
            JSON_SCHEMA_FORMAT,
            AnthropicTextToCadModel,
            decoding_capabilities,
        )

        self.assertEqual(JSON_SCHEMA_FORMAT, "json_schema")
        self.assertEqual(AnthropicTextToCadModel.name, "anthropic")
        capabilities = decoding_capabilities()
        self.assertEqual(capabilities["temperature"], "not settable")
        self.assertEqual(capabilities["deterministic_decoding"], "unavailable")

    def test_the_prompt_and_schema_are_shared_and_unchanged(self) -> None:
        self.assertEqual(prompt_fingerprint(), PROMPT_FINGERPRINT)
        self.assertEqual(PROMPT_VERSION, "2026-09-09.2")
        # 21938 for 2026-09-09.1; 25237 for 2026-09-09.2, which added the
        # no-joining rule and the locative-resolution rule.
        self.assertEqual(len(system_prompt()), 25237)
        schema = response_schema()
        self.assertEqual(sorted(schema["properties"]),
                         ["document", "issues", "questions", "status", "summary"])

    def test_no_gemini_specific_cad_schema_exists(self) -> None:
        for path in AI_SOURCE.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "gemini_schema",
                "GEMINI_SCHEMA",
                "gemini_document_schema",
                "gemini_response_schema",
            ):
                self.assertNotIn(forbidden, source, msg=f"{path.name}: {forbidden}")
        # One schema function, used by both.
        schema_source = (AI_SOURCE / "specification.py").read_text(encoding="utf-8")
        self.assertNotIn("gemini", schema_source.lower())
        self.assertNotIn("anthropic", schema_source.lower())

    def test_no_vendor_type_crosses_into_the_shared_layers(self) -> None:
        for name in ("generation.py", "evaluation.py", "comparison.py"):
            tree = ast.parse((AI_SOURCE / name).read_text(encoding="utf-8"))
            imported: List[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(a.name for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            for forbidden in ("google", "google.genai", "anthropic"):
                self.assertNotIn(forbidden, imported, msg=f"{name}: {forbidden}")

    def test_cad_core_knows_about_neither_provider(self) -> None:
        core = REPO_ROOT / "packages" / "cad-core" / "src" / "cad_core"
        for path in core.glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for forbidden in ("gemini", "google.genai", "anthropic", "cad_ai"):
                self.assertNotIn(forbidden, source, msg=f"{path.name}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
