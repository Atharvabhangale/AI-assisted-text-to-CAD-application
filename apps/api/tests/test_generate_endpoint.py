"""Tests for ``POST /generate``: the natural-language entry point.

Three things these tests are about:

* **the route is thin** -- it interprets and stops. It runs no geometry, opens
  no cache, calls no exporter, and reuses the AI layer's existing result type
  rather than defining a second one;
* **every outcome is answered honestly** -- the five existing
  :class:`~cad_ai.generation.GenerationOutcome` values each map to one status,
  a clarification and a refusal are 200 answers rather than errors, and an
  invalid model answer is never repaired into a valid one;
* **nothing unsafe crosses** -- no credential, no provider diagnostic, no
  traceback, no path.

**No test here contacts a provider.** Every one drives the real route through
``StubTextToCadModel``, whose payloads were hand-written in
``provider_stubs.py``. Nothing in this file says anything about the quality of
any real model, and running the suite spends no quota and needs no key.

The end of the file follows a generated document into the **real** build
stack -- real validator, real kernel, real child process -- to prove the two
halves join: what ``/generate`` returns is exactly what ``/build`` accepts.
"""

from __future__ import annotations

import ast
import json
import logging
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi.testclient import TestClient

from cad_api.app import BUILD_PATH, GENERATE_PATH, create_app
from cad_api.config import ApiConfig
from cad_api.generation import MAX_DESCRIPTION_CHARACTERS, TextGenerator
from cad_api.schemas import GenerateBody
from cad_api.status import (
    BAD_GATEWAY_STATUS,
    GENERATION_STATUS,
    OK_STATUS,
    TRANSPORT_STATUS,
    UNAVAILABLE_STATUS,
    status_for_outcome,
)

from cad_ai.generation import GenerationOutcome, TextToCadService
from cad_core import validate
from cad_core.application_service import CadApplicationService

from provider_stubs import (
    BOX_DOCUMENT,
    CYLINDER_DOCUMENT,
    StubTextToCadModel,
    failing_stub,
    gemini_shaped_stub,
)

#: A description used throughout. Its wording never reaches a model here.
PLATE_TEXT = "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm thick."
CYLINDER_TEXT = "Create a cylinder 20 mm in diameter and 50 mm tall along +Z."

#: Text that must never appear in a ``/generate`` response body.
FORBIDDEN_TEXT: Tuple[str, ...] = (
    "Traceback",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "x-goog-api-key",
    "api_key",
    "apiKey",
    "authorization",
    "Bearer",
    "sk-ant",
    "REDACTED",
    "429 quota",
    "cadquery",
    "CadQuery",
    "OCP",
    "site-packages",
    "PYTHONPATH",
    "/tmp",
    "/usr/",
)


class GenerateTestCase(unittest.TestCase):
    """An app whose provider is a stub. Nothing here reaches a network."""

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()

    def client_with(self, model: Any) -> TestClient:
        """The real application, with one stub substituted for the provider.

        The stub is injected where the *provider* goes, so everything above it
        -- the generation service, the deserializer, the validator, the route
        and the status map -- is the real thing under test.
        """
        service = CadApplicationService.local(self.cache_root)
        app = create_app(service=service)
        app.state.generator = TextGenerator(
            service, generator=TextToCadService(model, service)
        )
        return TestClient(app)

    def generate(self, model: Any, text: str = PLATE_TEXT) -> Any:
        return self.client_with(model).post(GENERATE_PATH, json={"text": text})

    def assert_safe(self, response: Any) -> None:
        text = json.dumps(response.json())
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} crossed the boundary")
        self.assertNotIn(str(self.tmp), text)


# --- the five outcomes ------------------------------------------------------


class TestOutcomes(GenerateTestCase):
    def test_a_box_description_returns_a_generated_document(self) -> None:
        response = self.generate(StubTextToCadModel("valid_box"))
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "generated")
        self.assertIsNotNone(payload["document"])
        self.assertEqual(payload["document"]["units"], "mm")
        self.assertEqual(payload["document"]["features"][0]["type"], "box")
        self.assertTrue(payload["document_hash"])
        self.assert_safe(response)

    def test_a_cylinder_description_returns_a_generated_document(self) -> None:
        response = self.generate(
            StubTextToCadModel("valid_cylinder"), CYLINDER_TEXT
        )
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "generated")
        feature = payload["document"]["features"][0]
        self.assertEqual(feature["type"], "cylinder")
        self.assertEqual(feature["diameter"], 20.0)
        self.assertEqual(feature["height"], 50.0)
        # The omitted optional parameters were filled by the *specification's*
        # defaults during canonicalization, not by this route.
        self.assertEqual(feature["axis"], "+Z")
        self.assert_safe(response)

    def test_the_generated_document_passes_the_existing_validator(self) -> None:
        # The authoritative validator, run again here on what crossed the
        # wire: the route may not return anything it would reject.
        for shape in ("valid_box", "valid_cylinder"):
            with self.subTest(shape=shape):
                payload = self.generate(StubTextToCadModel(shape)).json()
                result = validate(payload["document"])
                self.assertTrue(result.valid, msg=str(result.errors))

    def test_a_clarification_is_an_answer_not_an_error(self) -> None:
        response = self.generate(StubTextToCadModel("needs_clarification"))
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "needs_clarification")
        self.assertIsNone(payload["document"])
        self.assertTrue(payload["questions"])
        self.assert_safe(response)

    def test_an_unsupported_request_is_an_answer_not_an_error(self) -> None:
        response = self.generate(StubTextToCadModel("unsupported"))
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "unsupported")
        self.assertIsNone(payload["document"])
        self.assertTrue(payload["issues"])
        self.assert_safe(response)

    def test_a_provider_failure_is_service_unavailable(self) -> None:
        response = self.generate(failing_stub())
        self.assertEqual(response.status_code, UNAVAILABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "model_error")
        self.assertIsNone(payload["document"])
        # The stub's detail names a quota error and a redacted key header.
        # Neither may appear in what the client receives.
        self.assert_safe(response)

    def test_a_missing_credential_is_service_unavailable(self) -> None:
        response = self.generate(failing_stub(unavailable=True))
        self.assertEqual(response.status_code, UNAVAILABLE_STATUS)
        self.assertEqual(response.json()["outcome"], "model_error")
        self.assert_safe(response)

    def test_an_unusable_model_answer_is_bad_gateway(self) -> None:
        for shape in ("prose", "malformed_json", "empty", "json_array", "null"):
            with self.subTest(shape=shape):
                response = self.generate(StubTextToCadModel(shape))
                self.assertEqual(response.status_code, BAD_GATEWAY_STATUS)
                payload = response.json()
                self.assertEqual(payload["outcome"], "invalid_model_output")
                self.assertIsNone(payload["document"])
                self.assert_safe(response)

    def test_invalid_cad_is_rejected_and_never_repaired(self) -> None:
        # The model returned a syntactically fine document whose CAD is
        # invalid (S10: a size must be positive). It must come back as a
        # failure carrying the rule -- not as a corrected document.
        response = self.generate(StubTextToCadModel("invalid_cad"))
        self.assertEqual(response.status_code, BAD_GATEWAY_STATUS)
        payload = response.json()
        self.assertEqual(payload["outcome"], "invalid_model_output")
        self.assertIsNone(payload["document"])
        self.assertIn("S10", payload["rule_codes"])
        self.assert_safe(response)

    def test_a_status_the_model_invents_is_not_honoured(self) -> None:
        response = self.generate(StubTextToCadModel("unknown_status"))
        self.assertEqual(response.status_code, BAD_GATEWAY_STATUS)
        self.assertEqual(response.json()["outcome"], "invalid_model_output")

    def test_every_outcome_has_exactly_one_status(self) -> None:
        for outcome in GenerationOutcome:
            with self.subTest(outcome=outcome):
                self.assertIn(outcome, GENERATION_STATUS)
                self.assertIn(status_for_outcome(outcome), (200, 502, 503))


# --- the request envelope ---------------------------------------------------


class TestRequestEnvelope(GenerateTestCase):
    def test_an_empty_description_is_refused_without_calling_the_model(
        self,
    ) -> None:
        model = StubTextToCadModel("valid_box")
        client = self.client_with(model)
        for text in ("", "   ", "\n\t"):
            with self.subTest(text=repr(text)):
                response = client.post(GENERATE_PATH, json={"text": text})
                self.assertEqual(response.status_code, TRANSPORT_STATUS)
        self.assertEqual(model.requests, [], msg="the model was called anyway")

    def test_an_unknown_field_is_refused(self) -> None:
        response = self.client_with(StubTextToCadModel()).post(
            GENERATE_PATH, json={"text": PLATE_TEXT, "document": {}}
        )
        self.assertEqual(response.status_code, TRANSPORT_STATUS)

    def test_a_missing_field_is_refused(self) -> None:
        response = self.client_with(StubTextToCadModel()).post(
            GENERATE_PATH, json={}
        )
        self.assertEqual(response.status_code, TRANSPORT_STATUS)

    def test_an_over_long_description_is_refused(self) -> None:
        model = StubTextToCadModel("valid_box")
        response = self.client_with(model).post(
            GENERATE_PATH, json={"text": "x" * (MAX_DESCRIPTION_CHARACTERS + 1)}
        )
        self.assertEqual(response.status_code, TRANSPORT_STATUS)
        self.assertEqual(model.requests, [])

    def test_the_description_reaches_the_model_verbatim(self) -> None:
        model = StubTextToCadModel("valid_box")
        self.client_with(model).post(GENERATE_PATH, json={"text": PLATE_TEXT})
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(model.requests[0].user_text, PLATE_TEXT)

    def test_one_request_makes_exactly_one_model_call(self) -> None:
        # No retry, anywhere: not on a provider failure, not on prose, not on
        # an invalid document.
        for shape in ("valid_box", "prose", "invalid_cad"):
            with self.subTest(shape=shape):
                model = StubTextToCadModel(shape)
                self.client_with(model).post(
                    GENERATE_PATH, json={"text": PLATE_TEXT}
                )
                self.assertEqual(len(model.requests), 1)

    def test_the_body_fields_match_the_declared_contract(self) -> None:
        self.assertEqual(
            tuple(GenerateBody.model_fields), ("text",)
        )


# --- secrecy ----------------------------------------------------------------


class TestNothingSensitiveCrosses(GenerateTestCase):
    def test_no_credential_appears_in_any_response(self) -> None:
        # A credential is set while every outcome is exercised, so a response
        # that echoed the environment would be caught.
        import os

        with unittest.mock.patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-not-a-real-key-000",
                "ANTHROPIC_API_KEY": "test-not-a-real-key-111",
            },
        ):
            for model in (
                StubTextToCadModel("valid_box"),
                StubTextToCadModel("needs_clarification"),
                StubTextToCadModel("unsupported"),
                StubTextToCadModel("prose"),
                failing_stub(),
            ):
                with self.subTest(model=model):
                    response = self.generate(model)
                    body = json.dumps(response.json())
                    self.assertNotIn("test-not-a-real-key-000", body)
                    self.assertNotIn("test-not-a-real-key-111", body)
                    self.assert_safe(response)

    def test_no_credential_appears_in_the_server_log(self) -> None:
        with self.assertLogs("cad_api", level="DEBUG") as captured:
            self.generate(failing_stub())
        logged = "\n".join(captured.output)
        for token in ("REDACTED", "x-goog-api-key", "429 quota", "sk-ant"):
            self.assertNotIn(token, logged, msg=f"{token!r} was logged")

    def test_the_development_diagnostic_never_reaches_the_client(self) -> None:
        # `detail` may quote the model's raw text. It is excluded from the
        # payload by the AI layer; this asserts the route did not add it back.
        response = self.generate(StubTextToCadModel("prose"))
        self.assertNotIn("detail", response.json())
        self.assertNotIn("Sure! Here is", json.dumps(response.json()))

    def test_the_response_carries_no_path_or_process_detail(self) -> None:
        forbidden_keys = {
            "path",
            "paths",
            "file_path",
            "cache_root",
            "traceback",
            "stderr",
            "stdout",
            "pid",
            "exit_code",
        }
        response = self.generate(StubTextToCadModel("valid_box"))
        for key in _walk_keys(response.json()):
            self.assertNotIn(key.lower(), forbidden_keys)


# --- the route is thin ------------------------------------------------------


class TestTheRouteIsThin(unittest.TestCase):
    def source(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[1] / "src" / "cad_api" / f"{name}.py"
        ).read_text(encoding="utf-8")

    def imports_of(self, source: str) -> List[str]:
        found: List[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def names_in(self, source: str) -> List[str]:
        names: List[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        return names

    def test_the_generation_module_imports_no_cad_kernel(self) -> None:
        imports = self.imports_of(self.source("generation"))
        for forbidden in (
            "cadquery",
            "OCP",
            "cad_core.local_cad",
            "cad_core.geometry",
            "cad_core.edge_selection",
        ):
            self.assertNotIn(forbidden, imports)

    def test_the_generation_module_imports_no_exporter(self) -> None:
        imports = self.imports_of(self.source("generation"))
        for forbidden in (
            "cad_core.step_export",
            "cad_core.iges_export",
            "cad_core.stl_export",
            "cad_core.render_model",
        ):
            self.assertNotIn(forbidden, imports)

    def test_the_generation_module_touches_no_cache_or_worker(self) -> None:
        source = self.source("generation")
        imports = self.imports_of(source)
        for forbidden in (
            "cad_core.local_build_cache",
            "cad_core.isolated_worker",
            "cad_core.isolated_execution",
        ):
            self.assertNotIn(forbidden, imports)
        names = self.names_in(source)
        for forbidden in ("get_or_build", "execute_isolated", "build_part"):
            self.assertNotIn(forbidden, names)

    def test_the_transport_names_no_provider_at_all(self) -> None:
        # Which vendor answers is the AI layer's business. The transport asks
        # `cad_ai.factory` and never names a provider, a model or an SDK --
        # so adding a third provider touches no file in `cad_api`.
        source = self.source("generation")
        lowered = source.lower()
        for forbidden in (
            "anthropic",
            "gemini",
            "google",
            "openai",
            "claude",
            "gpt-",
        ):
            self.assertNotIn(forbidden, lowered, msg=f"{forbidden} named")
        imports = self.imports_of(source)
        self.assertIn("cad_ai.factory", imports)

    def test_the_factory_imports_no_provider_sdk_at_module_level(self) -> None:
        # Each provider class is imported inside its own branch, so importing
        # the HTTP layer needs neither SDK installed.
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "cad_ai"
            / "factory.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        top_level: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.append(node.module)
        for forbidden in (
            "anthropic",
            "google",
            "google.genai",
            "cad_ai.gemini_provider",
            "cad_ai.anthropic_provider",
        ):
            self.assertNotIn(forbidden, top_level)

    def test_the_route_builds_nothing(self) -> None:
        # `/generate` never reaches the build path: asserted behaviourally by
        # giving the app a service whose build would explode if called.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "cache"
        root.mkdir()
        service = CadApplicationService.local(root)
        app = create_app(service=service)
        app.state.generator = TextGenerator(
            service,
            generator=TextToCadService(StubTextToCadModel("valid_box"), service),
        )
        with unittest.mock.patch.object(
            service, "build_document", side_effect=AssertionError("built!")
        ):
            response = TestClient(app).post(
                GENERATE_PATH, json={"text": PLATE_TEXT}
            )
        self.assertEqual(response.status_code, OK_STATUS)

    def test_the_ai_result_model_is_not_duplicated(self) -> None:
        # No second result dataclass, and no second outcome taxonomy.
        source = self.source("generation")
        self.assertNotIn("class AiGenerationResult", source)
        self.assertNotIn("class GenerationOutcome", source)
        self.assertIn("from cad_ai.generation import", source)


# --- generation joins the existing build pipeline ---------------------------


class TestGeneratedDocumentBuilds(GenerateTestCase):
    """The real stack: real validator, real kernel, real child process.

    This is the join the whole stage exists to make. What ``/generate``
    returns is fed to ``/build`` **unchanged**, exactly as the browser does
    it, and the geometry that comes out is measured.
    """

    def build(self, document: Dict[str, Any]) -> Dict[str, Any]:
        client = self.client_with(StubTextToCadModel("valid_box"))
        response = client.post(
            BUILD_PATH,
            json={
                "document": document,
                "outputs": ["geometry", "step", "iges", "stl", "render"],
            },
        )
        self.assertEqual(response.status_code, OK_STATUS, msg=response.text)
        return response.json()

    def generated(self, shape: str, text: str) -> Dict[str, Any]:
        payload = self.generate(StubTextToCadModel(shape), text).json()
        self.assertEqual(payload["outcome"], "generated")
        return payload["document"]

    def measurements(self, build: Dict[str, Any], kind: str) -> Dict[str, Any]:
        for artifact in build["artifacts"]:
            if artifact["kind"] == kind:
                return artifact["measurements"]
        self.fail(f"no {kind} artifact in the build")

    def test_a_generated_box_builds_to_one_real_solid(self) -> None:
        document = self.generated("valid_box", PLATE_TEXT)
        build = self.build(document)
        self.assertTrue(build["succeeded"])
        geometry = self.measurements(build, "geometry")
        self.assertEqual(geometry["solid_count"], 1)
        self.assertTrue(geometry["is_solid"])
        size = geometry["bounding_box"]["size"]
        self.assertAlmostEqual(size["x"], 100.0, places=6)
        self.assertAlmostEqual(size["y"], 60.0, places=6)
        self.assertAlmostEqual(size["z"], 10.0, places=6)
        self.assertAlmostEqual(geometry["volume_mm3"], 60000.0, places=3)

    def test_a_generated_cylinder_builds_with_the_right_extent(self) -> None:
        document = self.generated("valid_cylinder", CYLINDER_TEXT)
        build = self.build(document)
        self.assertTrue(build["succeeded"])
        geometry = self.measurements(build, "geometry")
        self.assertEqual(geometry["solid_count"], 1)
        size = geometry["bounding_box"]["size"]
        # 20 mm diameter, 50 mm tall, default +Z: the tall extent is Z.
        self.assertAlmostEqual(size["x"], 20.0, places=3)
        self.assertAlmostEqual(size["y"], 20.0, places=3)
        self.assertAlmostEqual(size["z"], 50.0, places=6)

    def test_a_generated_box_produces_a_render_model(self) -> None:
        build = self.build(self.generated("valid_box", PLATE_TEXT))
        render = self.measurements(build, "render")
        self.assertGreater(render["triangle_count"], 0)
        self.assertEqual(render["units"], "mm")
        self.assertEqual(render["coordinate_system"], "right_handed_z_up")
        # And it is retrievable by build key, the way the viewer fetches it.
        client = self.client_with(StubTextToCadModel("valid_box"))
        response = client.get(f"/builds/{build['build_key']}/render")
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertGreater(len(response.json()["triangles"]), 0)

    def test_a_generated_box_produces_downloadable_step_iges_and_stl(
        self,
    ) -> None:
        build = self.build(self.generated("valid_box", PLATE_TEXT))
        client = self.client_with(StubTextToCadModel("valid_box"))
        # STEP declares its standard in the first bytes; IGES and binary
        # STL do not begin with a fixed marker, so only their length and
        # deliverability are asserted.
        wanted = {"step": b"ISO-10303-21", "iges": b"", "stl": b""}
        for artifact in build["artifacts"]:
            if artifact["kind"] not in wanted:
                continue
            with self.subTest(kind=artifact["kind"]):
                self.assertEqual(artifact["storage"], "file")
                self.assertGreater(artifact["size_bytes"], 0)
                response = client.get(f"/artifacts/{artifact['logical_id']}")
                self.assertEqual(response.status_code, OK_STATUS)
                self.assertEqual(
                    len(response.content), artifact["size_bytes"]
                )
                head = wanted[artifact["kind"]]
                if head:
                    self.assertTrue(response.content.startswith(head))

    def test_the_document_is_built_exactly_as_generated(self) -> None:
        # Byte-for-byte the same document object: the client changes nothing,
        # and neither does the build route.
        document = self.generated("valid_box", PLATE_TEXT)
        build = self.build(document)
        validated = self.client_with(StubTextToCadModel("valid_box")).post(
            "/validate", json={"document": document}
        )
        self.assertEqual(
            validated.json()["document_hash"], build["document_hash"]
        )


# --- the provider a run actually used ---------------------------------------


class TestProviderIdentity(GenerateTestCase):
    def test_the_response_names_the_provider_and_model_but_no_secret(
        self,
    ) -> None:
        payload = self.generate(gemini_shaped_stub("valid_box")).json()
        metadata = payload["metadata"]
        self.assertEqual(metadata["provider"], "gemini")
        self.assertEqual(metadata["model"], "gemini-stub")
        self.assertTrue(metadata["prompt_version"])
        # Identity, not authentication: the metadata has no credential field.
        self.assertNotIn("api_key", metadata)
        self.assertNotIn("key", metadata)

    def test_the_same_route_serves_either_provider(self) -> None:
        for model in (
            StubTextToCadModel("valid_box", name="anthropic"),
            gemini_shaped_stub("valid_box"),
        ):
            with self.subTest(provider=model.name):
                payload = self.generate(model).json()
                self.assertEqual(payload["outcome"], "generated")
                self.assertEqual(payload["document"]["units"], "mm")


def _walk_keys(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _walk_keys(item)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
