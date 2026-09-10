"""The local development provider: real path, no model, no credential."""

from __future__ import annotations

import ast
import io
import json
import math
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fastapi.testclient import TestClient

from cad_core.application_service import CadApplicationService

from cad_experimental import harness, local_plan_provider as lpp
from cad_experimental.app import create_app
from cad_experimental.config import ExperimentalConfig
from cad_experimental.generation import OperationPlanService, PlanOutcome

SOURCE = pathlib.Path(lpp.__file__).resolve()
CONFIG = ExperimentalConfig(model=lpp.MODEL_NAME, provider=lpp.PROVIDER_NAME)


class FixtureTests(unittest.TestCase):
    def test_the_three_required_fixtures_exist(self):
        for name in (
            "box-100x60x10", "cylinder-d20-h50-z", "cylinder-d16-h30-x",
        ):
            self.assertIn(name, lpp.FIXTURES)

    def test_every_fixture_is_a_parseable_valid_plan(self):
        from cad_experimental.parser import parse_plan
        from cad_experimental.validation import validate_plan

        for name in lpp.FIXTURE_NAMES:
            with self.subTest(fixture=name):
                plan = parse_plan(lpp.fixture_plan(name))
                self.assertTrue(validate_plan(plan).valid)

    def test_expected_volumes_are_computed_from_the_fixture(self):
        """Not transcribed: a typo in an expectation would hide a real error."""
        entry = lpp.fixture("cylinder-d20-h50-z")
        self.assertAlmostEqual(
            entry["expected_volume_mm3"], math.pi * 10.0**2 * 50.0, places=9
        )

    def test_an_unknown_fixture_is_refused(self):
        with self.assertRaises(KeyError):
            lpp.fixture("no-such-fixture")

    def test_a_fixture_cannot_be_mutated_through_the_accessor(self):
        first = lpp.fixture_plan("box-100x60x10")
        first["operations"][0]["parameters"]["x"] = 1
        self.assertEqual(
            lpp.fixture_plan("box-100x60x10")["operations"][0]["parameters"]["x"],
            100,
        )


class ProviderTests(unittest.TestCase):
    def test_it_satisfies_the_provider_boundary(self):
        from cad_ai.provider import TextToCadModel

        self.assertIsInstance(
            lpp.LocalPlanProvider(fixture_name="box-100x60x10"), TextToCadModel
        )

    def test_it_is_marked_as_local_development(self):
        self.assertTrue(
            lpp.LocalPlanProvider(fixture_name="box-100x60x10").is_local_development
        )

    def test_a_real_provider_carries_no_such_marker(self):
        """The marker must distinguish, so the real ones must lack it."""
        from cad_ai.anthropic_provider import AnthropicTextToCadModel
        from cad_ai.gemini_provider import GeminiTextToCadModel

        for provider in (AnthropicTextToCadModel, GeminiTextToCadModel):
            self.assertFalse(hasattr(provider, "is_local_development"))

    def test_it_names_no_vendor_and_no_model(self):
        response = lpp.LocalPlanProvider(fixture_name="box-100x60x10").generate(
            _request()
        )
        self.assertEqual(response.provider, "local-development")
        self.assertIn("not a model", response.model)
        for forbidden in ("claude", "haiku", "anthropic", "gemini"):
            self.assertNotIn(forbidden, response.provider.lower())
            self.assertNotIn(forbidden, response.model.lower())

    def test_it_never_claims_structured_output(self):
        """Nothing constrained the text, so claiming a schema would be false."""
        response = lpp.LocalPlanProvider(fixture_name="box-100x60x10").generate(
            _request()
        )
        self.assertFalse(response.structured_output)

    def test_it_ignores_the_prompt(self):
        """It does not interpret. Reacting to the text would imitate a model."""
        provider = lpp.LocalPlanProvider(fixture_name="box-100x60x10")
        first = provider.generate(_request("a cylinder 20 mm across")).text
        second = provider.generate(_request("a plate 100 mm long")).text
        self.assertEqual(first, second)

    def test_exactly_one_source_must_be_given(self):
        for kwargs in (
            {},
            {"plan": {"a": 1}, "fixture_name": "box-100x60x10"},
            {"plan": {"a": 1}, "raw_text": "x"},
        ):
            with self.subTest(kwargs=sorted(kwargs)):
                with self.assertRaises(ValueError):
                    lpp.LocalPlanProvider(**kwargs)

    def test_raw_text_reaches_the_real_parser_and_is_rejected(self):
        """The failure paths are exercised too, by the real parser."""
        service = OperationPlanService(
            lpp.LocalPlanProvider(raw_text="Sure! Here is a box."), CONFIG
        )
        result = service.generate("anything")
        self.assertIs(result.outcome, PlanOutcome.INVALID_MODEL_OUTPUT)

    def test_a_bad_plan_is_rejected_by_the_real_validator(self):
        service = OperationPlanService(
            lpp.LocalPlanProvider(
                plan={
                    "status": "generated",
                    "summary": "",
                    "operations": [
                        {"id": "b", "type": "box",
                         "parameters": {"x": -1, "y": 1, "z": 1}}
                    ],
                }
            ),
            CONFIG,
        )
        self.assertIs(
            service.generate("anything").outcome, PlanOutcome.INVALID_MODEL_OUTPUT
        )

    def test_an_unimplemented_operation_is_still_rejected(self):
        service = OperationPlanService(
            lpp.LocalPlanProvider(
                plan={
                    "status": "generated",
                    "summary": "",
                    "operations": [
                        {"id": "b", "type": "sphere", "parameters": {}}
                    ],
                }
            ),
            CONFIG,
        )
        self.assertIs(
            service.generate("anything").outcome, PlanOutcome.INVALID_MODEL_OUTPUT
        )


class StampTests(unittest.TestCase):
    def test_every_result_is_stamped(self):
        for name in lpp.FIXTURE_NAMES:
            with self.subTest(fixture=name):
                result = lpp.run_fixture(name, build=False)
                self.assertEqual(result["source"], "LOCAL_DEVELOPMENT_PLAN")
                self.assertFalse(result["is_live_model_result"])
                self.assertIn("NOT a Claude", result["note"])

    def test_no_result_names_a_real_model(self):
        for name in lpp.FIXTURE_NAMES:
            text = json.dumps(lpp.run_fixture(name, build=False)).lower()
            self.assertNotIn("haiku", text)
            self.assertNotIn("claude-", text)

    def test_the_report_says_the_comparison_is_pending(self):
        report = lpp.format_report(
            [lpp.run_fixture("box-100x60x10", build=False)]
        )
        self.assertIn("NOT a Claude/Anthropic result", report)
        self.assertIn("REAL HAIKU COMPARISON: STILL PENDING", report)


class GeometryTests(unittest.TestCase):
    """The point of the whole exercise: real B-rep, real RenderModel."""

    @classmethod
    def setUpClass(cls):
        cls.results = {
            name: lpp.run_fixture(name) for name in lpp.FIXTURE_NAMES
        }

    def test_every_fixture_builds_a_single_real_solid(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                self.assertTrue(result["built"], result.get("build_error"))
                self.assertEqual(result["solid_count"], 1)
                self.assertTrue(result["is_solid"])

    def test_every_fixture_passes_the_existing_validator(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                self.assertTrue(result["existing_validator_valid"])
                self.assertIsNotNone(result["document_hash"])

    def test_every_volume_matches_within_tolerance(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                self.assertTrue(
                    result["volume_matches"],
                    f"{result['volume_mm3']} != {result['expected_volume_mm3']}",
                )

    def test_every_bounding_box_matches(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                self.assertTrue(result["bounding_box_matches"])

    def test_the_x_axis_cylinder_is_actually_reoriented(self):
        box = self.results["cylinder-d16-h30-x"]["bounding_box"]
        self.assertAlmostEqual(box["x"], 30.0, places=6)
        self.assertAlmostEqual(box["y"], 16.0, places=6)
        self.assertAlmostEqual(box["z"], 16.0, places=6)

    def test_every_fixture_produces_a_render_model(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                render = result["render_model"]
                self.assertIsNotNone(render)
                self.assertGreater(render["triangles"], 0)
                self.assertEqual(render["units"], "mm")
                self.assertEqual(
                    render["coordinate_system"], "right_handed_z_up"
                )

    def test_the_box_tessellates_to_twelve_triangles(self):
        self.assertEqual(
            self.results["box-100x60x10"]["render_model"]["triangles"], 12
        )


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def client(self):
        return TestClient(
            create_app(config=CONFIG, planner=None, service=self.service)
        )

    def test_fixtures_are_listed_and_stamped(self):
        body = self.client().get("/experimental/local-plan/fixtures").json()
        self.assertEqual(body["source"], "LOCAL_DEVELOPMENT_PLAN")
        self.assertFalse(body["is_live_model_result"])
        self.assertEqual(len(body["fixtures"]), len(lpp.FIXTURE_NAMES))

    def test_a_fixture_builds_over_http(self):
        response = self.client().post(
            "/experimental/local-plan", json={"fixture": "box-100x60x10"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["parsed"])
        self.assertTrue(body["plan_valid"])
        self.assertTrue(body["built"])
        self.assertIn("render", body)
        self.assertEqual(body["source"], "LOCAL_DEVELOPMENT_PLAN")

    def test_a_supplied_plan_builds_over_http(self):
        plan = {
            "status": "generated",
            "summary": "from the brief",
            "operations": [
                {
                    "id": "body",
                    "type": "cylinder",
                    "parameters": {
                        "diameter": 80,
                        "height": 100,
                        "position": {"x": 0, "y": 0, "z": 0},
                        "axis": "+Z",
                    },
                }
            ],
        }
        body = self.client().post(
            "/experimental/local-plan", json={"plan": plan}
        ).json()
        self.assertTrue(body["built"])
        self.assertFalse(body["is_live_model_result"])

    def test_both_or_neither_is_refused(self):
        for payload in (
            {},
            {"fixture": "box-100x60x10", "plan": {"status": "generated"}},
        ):
            with self.subTest(payload=sorted(payload)):
                response = self.client().post(
                    "/experimental/local-plan", json=payload
                )
                self.assertEqual(response.status_code, 400)

    def test_an_unknown_fixture_is_refused_and_stamped(self):
        response = self.client().post(
            "/experimental/local-plan", json={"fixture": "nope"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["source"], "LOCAL_DEVELOPMENT_PLAN")

    def test_a_hostile_plan_is_rejected_by_the_real_parser(self):
        for operation in (
            {"id": "b", "type": "sphere", "parameters": {}},
            {"id": "b", "type": "box",
             "parameters": {"x": "__import__('os').system('id')", "y": 1, "z": 1}},
            {"id": "b", "type": "box",
             "parameters": {"x": 1, "y": 1, "z": 1, "exec": "1"}},
        ):
            with self.subTest(operation=operation["type"]):
                body = self.client().post(
                    "/experimental/local-plan",
                    json={
                        "plan": {
                            "status": "generated",
                            "summary": "",
                            "operations": [operation],
                        }
                    },
                ).json()
                self.assertFalse(body["parsed"])
                self.assertFalse(body["plan_valid"])

    def test_health_advertises_the_development_path_honestly(self):
        body = self.client().get("/experimental/health").json()
        self.assertTrue(body["local_development_plan_available"])
        self.assertEqual(
            body["local_development_label"], "LOCAL_DEVELOPMENT_PLAN"
        )

    def test_the_generate_route_still_needs_a_real_provider(self):
        """The development route must not become a back door to /generate."""
        response = self.client().post(
            "/experimental/generate-plan", json={"text": "a plate"}
        )
        self.assertEqual(response.status_code, 503)


class LiveHarnessUnchangedTests(unittest.TestCase):
    """The real comparison must stay real and stay pending."""

    def test_the_harness_does_not_import_the_local_provider(self):
        tree = ast.parse(
            pathlib.Path(harness.__file__).read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [
                    alias.name for alias in node.names
                ]
            for name in names:
                self.assertNotIn("local_plan_provider", name)
                self.assertNotIn("LocalPlanProvider", name)

    def test_live_still_requires_the_real_credential(self):
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True):
            with redirect_stdout(buffer):
                code = harness.main(["--live", "--no-build"])
        self.assertEqual(code, 1)
        output = buffer.getvalue()
        self.assertIn("NOT_RUN", output)
        self.assertIn("ANTHROPIC_API_KEY is not set", output)

    def test_live_does_not_fall_back_to_the_local_provider(self):
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True):
            with redirect_stdout(buffer):
                harness.main(["--live", "--no-build"])
        self.assertNotIn("LOCAL", buffer.getvalue().upper())


class SecurityTests(unittest.TestCase):
    """The provider reads nothing, opens nothing and executes nothing."""

    def tree(self):
        return ast.parse(SOURCE.read_text(encoding="utf-8"))

    def code_identifiers(self):
        """Every name the module executes against. Prose and text excluded.

        Docstrings, comments and printed messages are not behaviour. This
        module's docstring explains that ``--live`` still needs
        ``ANTHROPIC_API_KEY``, and its report tells the operator the same
        thing -- both are desirable, and a raw-text scan would flag them as
        a credential read. What would actually be needed to *read* a
        credential is a name: an identifier, an attribute, a keyword
        argument or a lookup key. That is what this returns.
        """
        pieces = []
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Name):
                pieces.append(node.id)
            elif isinstance(node, ast.Attribute):
                pieces.append(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                pieces.append(node.arg)
            elif isinstance(node, ast.arg):
                pieces.append(node.arg)
            elif isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.Constant
            ):
                # A dict/environ lookup key, e.g. environ["ANTHROPIC_API_KEY"].
                if isinstance(node.slice.value, str):
                    pieces.append(node.slice.value)
        return "\n".join(pieces)

    def test_it_reads_no_credential(self):
        """No credential is named anywhere the module could read one from.

        Read together with :meth:`test_it_reads_no_environment_variable_at_all`
        this is the whole property: the module makes no environment access,
        and no credential name is used as a lookup key or an attribute. A
        credential name inside a printed message is therefore inert -- there
        is nothing in this module that could turn it into a read.
        """
        names = self.code_identifiers()
        for token in (
            "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "api_key",
            "CAD_ANTHROPIC_API_KEY", "AUTH_TOKEN", "Authorization",
        ):
            self.assertNotIn(token, names, f"code looks up {token}")

    def test_a_credential_name_appears_only_in_prose_or_a_message(self):
        """Where ANTHROPIC_API_KEY does appear, it is text and nothing else."""
        occurrences = []
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "ANTHROPIC_API_KEY" in node.value:
                    occurrences.append(node.value)
        # It appears: in the module docstring, and in the report line that
        # tells the operator what is missing. Both are strings that are only
        # ever printed.
        self.assertTrue(occurrences)
        for text in occurrences:
            self.assertTrue(
                "STILL PENDING" in text or "live harness" in text,
                f"unexpected context for a credential name: {text[:80]!r}",
            )

    def test_it_reads_no_environment_variable_at_all(self):
        source = SOURCE.read_text(encoding="utf-8")
        for token in ("os.environ", "getenv"):
            self.assertNotIn(token, source, f"uses {token}")

    def test_it_imports_no_provider_sdk_and_no_network_module(self):
        for node in ast.walk(self.tree()):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                self.assertNotIn(
                    root,
                    {
                        "anthropic", "google", "socket", "http", "httpx",
                        "requests", "urllib", "subprocess", "pickle",
                        "importlib", "ctypes",
                    },
                    f"imports {name}",
                )

    def test_it_calls_no_execution_primitive(self):
        for node in ast.walk(self.tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(
                    node.func.id,
                    {"eval", "exec", "compile", "__import__"},
                    f"calls {node.func.id}",
                )

    def test_the_only_file_it_opens_is_a_plan_the_caller_named(self):
        """`--plan-file` is the one read, and it is an explicit argument."""
        opens = [
            node
            for node in ast.walk(self.tree())
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "open"
        ]
        self.assertEqual(len(opens), 1)

    def test_it_adds_no_retry_or_repair(self):
        """No retry, repair or re-prompt logic. Prose about it is fine."""
        code = self.code_identifiers().lower()
        for token in ("retry", "retries", "repair", "backoff", "reprompt"):
            self.assertNotIn(token, code, f"code implements {token}")

    def test_the_documentation_still_explains_the_credential_rule(self):
        """The scan above must not have been passed by deleting the docs.

        The module docstring is where a reader learns that the real live
        harness still requires the real credential. If that sentence ever
        disappears, this fails -- so the AST-based scan cannot be satisfied
        by silence.
        """
        doc = ast.get_docstring(self.tree()) or ""
        self.assertIn("ANTHROPIC_API_KEY", doc)
        self.assertIn("not a Claude result", doc)


def _request(text: str = "anything"):
    from cad_ai.provider import ModelRequest

    return ModelRequest(system="s", user_text=text)


if __name__ == "__main__":
    unittest.main()
