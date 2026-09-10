"""The experimental build path, the HTTP app, and isolation from production."""

from __future__ import annotations

import ast
import json
import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from cad_core.application_service import CadApplicationService

from cad_experimental.app import (
    BUILD_PATH,
    GENERATE_PATH,
    HEALTH_PATH,
    VALIDATE_PATH,
    create_app,
)
from cad_experimental.build import build_plan
from cad_experimental.config import ExperimentalConfig
from cad_experimental.generation import OperationPlanService
from cad_experimental.parser import parse_plan_text

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
SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"


def service_for(case):
    root = tempfile.mkdtemp()
    case.addCleanup(lambda: None)
    return CadApplicationService.local(root)


class BuildTests(unittest.TestCase):
    """The plan reaches real geometry through the existing service."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.service = CadApplicationService.local(cls.root)

    def test_a_box_plan_builds_a_real_solid(self):
        result = build_plan(self.service, parse_plan_text(box_plan()))
        self.assertTrue(result.built)
        details = result.outcome.artifact("geometry").details
        self.assertEqual(details["solid_count"], 1)
        self.assertTrue(details["is_solid"])
        self.assertAlmostEqual(details["volume_mm3"], 100 * 60 * 10, places=6)

    def test_a_cylinder_plan_builds_a_real_solid(self):
        import math

        result = build_plan(self.service, parse_plan_text(cylinder_plan()))
        self.assertTrue(result.built)
        details = result.outcome.artifact("geometry").details
        self.assertEqual(details["solid_count"], 1)
        self.assertAlmostEqual(
            details["volume_mm3"], math.pi * 10.0**2 * 50.0, places=6
        )

    def test_the_cylinder_axis_reaches_the_geometry(self):
        """+X must actually orient the solid, not just survive parsing."""
        plan = parse_plan_text(
            json.dumps(
                {
                    "status": "generated",
                    "summary": "",
                    "operations": [
                        {
                            "id": "body",
                            "type": "cylinder",
                            "parameters": {
                                "diameter": 16,
                                "height": 30,
                                "axis": "+X",
                            },
                        }
                    ],
                }
            )
        )
        result = build_plan(self.service, plan)
        self.assertTrue(result.built)
        size = result.outcome.artifact("geometry").details["bounding_box"]["size"]
        self.assertAlmostEqual(size["x"], 30.0, places=6)
        self.assertAlmostEqual(size["y"], 16.0, places=6)
        self.assertAlmostEqual(size["z"], 16.0, places=6)

    def test_a_render_model_is_produced(self):
        result = build_plan(self.service, parse_plan_text(box_plan()))
        render = result.outcome.render_model
        self.assertIsNotNone(render)
        self.assertEqual(render.triangle_count(), 12)
        self.assertEqual(render.units, "mm")
        self.assertEqual(render.coordinate_system, "right_handed_z_up")

    def test_the_existing_validator_rejects_a_two_solid_plan(self):
        """A coherent plan can still be an invalid part -- and S9 says so.

        The experimental validator deliberately does not duplicate S9. This
        proves the real validator still catches it, which is the whole point
        of translating into the canonical document rather than around it.
        """
        plan = parse_plan_text(
            json.dumps(
                {
                    "status": "generated",
                    "summary": "",
                    "operations": [
                        {"id": "a", "type": "box",
                         "parameters": {"x": 10, "y": 10, "z": 10}},
                        {"id": "b", "type": "box",
                         "parameters": {"x": 5, "y": 5, "z": 5}},
                    ],
                }
            )
        )
        result = build_plan(self.service, plan)
        self.assertFalse(result.built)
        self.assertIn("S9", result.outcome.error.message)

    def test_a_refusal_builds_nothing(self):
        result = build_plan(self.service, parse_plan_text(unsupported_plan()))
        self.assertFalse(result.built)
        self.assertIsNone(result.outcome)
        self.assertIsNone(result.document)

    def test_the_build_is_reproducible(self):
        first = build_plan(self.service, parse_plan_text(box_plan()))
        second = build_plan(self.service, parse_plan_text(box_plan()))
        self.assertEqual(first.outcome.build_key, second.outcome.build_key)
        self.assertEqual(
            first.outcome.document_hash, second.outcome.document_hash
        )
        self.assertTrue(second.outcome.cache_hit)

    def test_the_document_is_a_plain_v1_document(self):
        """Whatever produced it, the artefact downstream is the usual one."""
        result = build_plan(self.service, parse_plan_text(box_plan()))
        validation = self.service.validate_document(result.document)
        self.assertTrue(validation.valid)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.service = CadApplicationService.local(cls.root)

    def client(self, model=None, *, with_service=True):
        planner = (
            OperationPlanService(model, CONFIG) if model is not None else None
        )
        app = create_app(
            config=CONFIG,
            planner=planner,
            service=self.service if with_service else None,
        )
        return TestClient(app)

    def test_health_reports_the_experiment(self):
        response = self.client().get(HEALTH_PATH)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["experiment"], "cad-operation-graph")
        self.assertIn("prompt_fingerprint", body)

    def test_health_reports_credential_presence_not_value(self):
        body = self.client().get(HEALTH_PATH).json()
        self.assertIsInstance(body["model_configured"], bool)
        self.assertNotIn("api_key", json.dumps(body).lower())

    def test_generate_returns_a_plan(self):
        response = self.client(StubModel(box_plan())).post(
            GENERATE_PATH, json={"text": "a plate 100 x 60 x 10 mm"}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "generated")
        self.assertEqual(len(body["operations"]), 1)
        self.assertEqual(body["operations"][0]["type"], "box")

    def test_generate_returns_the_requested_response_shape(self):
        body = self.client(StubModel(box_plan())).post(
            GENERATE_PATH, json={"text": "a plate"}
        ).json()
        for key in ("status", "operations", "summary"):
            self.assertIn(key, body)

    def test_unsupported_is_a_200_result_not_an_error(self):
        response = self.client(StubModel(unsupported_plan())).post(
            GENERATE_PATH, json={"text": "a sphere"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unsupported")

    def test_clarification_carries_questions(self):
        body = self.client(StubModel(clarification_plan())).post(
            GENERATE_PATH, json={"text": "a cylinder"}
        ).json()
        self.assertEqual(body["status"], "needs_clarification")
        self.assertTrue(body["questions"])

    def test_no_planner_is_503_not_a_crash(self):
        response = self.client().post(GENERATE_PATH, json={"text": "a plate"})
        self.assertEqual(response.status_code, 503)

    def test_a_provider_failure_is_503(self):
        response = self.client(FailingModel()).post(
            GENERATE_PATH, json={"text": "a plate"}
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error_kind"], "rate_limited")

    def test_raw_model_text_never_reaches_a_response(self):
        marker = "SENTINEL-RAW-MODEL-TEXT"
        response = self.client(StubModel(marker)).post(
            GENERATE_PATH, json={"text": "a plate"}
        )
        self.assertNotIn(marker, response.text)

    def test_a_provider_detail_never_reaches_a_response(self):
        response = self.client(FailingModel()).post(
            GENERATE_PATH, json={"text": "a plate"}
        )
        self.assertNotIn("sk-ant", response.text)

    def test_an_oversized_description_is_refused_by_the_envelope(self):
        response = self.client(ExplodingModel()).post(
            GENERATE_PATH, json={"text": "x" * 10_000}
        )
        self.assertEqual(response.status_code, 422)

    def test_a_malformed_body_never_echoes_itself(self):
        response = self.client().post(GENERATE_PATH, json={"wrong": 1})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("wrong", response.text)

    def test_validate_accepts_a_good_plan(self):
        plan = json.loads(box_plan())
        body = self.client().post(VALIDATE_PATH, json={"plan": plan}).json()
        self.assertTrue(body["valid"])
        self.assertTrue(body["parsed"])

    def test_validate_reports_an_unknown_operation(self):
        plan = {
            "status": "generated",
            "summary": "",
            "operations": [
                {"id": "b", "type": "sphere", "parameters": {"diameter": 1}}
            ],
        }
        body = self.client().post(VALIDATE_PATH, json={"plan": plan}).json()
        self.assertFalse(body["valid"])
        self.assertFalse(body["parsed"])

    def test_build_produces_a_render_model(self):
        response = self.client().post(
            BUILD_PATH, json={"plan": json.loads(box_plan())}
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["build"]["succeeded"])
        self.assertIn("render", body)
        self.assertIn("triangles", body["render"])

    def test_build_refuses_an_invalid_plan(self):
        plan = {
            "status": "generated",
            "summary": "",
            "operations": [
                {"id": "b", "type": "box",
                 "parameters": {"x": -1, "y": 1, "z": 1}}
            ],
        }
        response = self.client().post(BUILD_PATH, json={"plan": plan})
        self.assertEqual(response.status_code, 400)

    def test_build_refuses_a_refusal(self):
        response = self.client().post(
            BUILD_PATH, json={"plan": json.loads(unsupported_plan())}
        )
        self.assertEqual(response.status_code, 400)

    def test_the_app_serves_only_experimental_routes(self):
        """It must not have grown a copy of the production surface."""
        app = create_app(config=CONFIG, service=self.service)
        paths = {
            route.path
            for route in app.routes
            if getattr(route, "path", "").startswith("/")
        }
        for production in ("/validate", "/build", "/health", "/generate"):
            self.assertNotIn(production, paths)
        for path in (HEALTH_PATH, GENERATE_PATH, VALIDATE_PATH, BUILD_PATH):
            self.assertIn(path, paths)


class IsolationTests(unittest.TestCase):
    """The experiment must be invisible to the stable path."""

    def imports_of(self, package):
        found = set()
        for path in (SOURCE / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
        return found

    def test_production_packages_never_import_the_experiment(self):
        for package in ("cad_ai", "cad_api"):
            with self.subTest(package=package):
                imports = self.imports_of(package)
                self.assertFalse(
                    {name for name in imports if "cad_experimental" in name},
                    f"{package} imports the experimental package",
                )

    def test_cad_core_never_imports_the_experiment(self):
        root = SOURCE.parents[2] / "packages" / "cad-core" / "src"
        for path in (root / "cad_core").rglob("*.py"):
            self.assertNotIn(
                "cad_experimental", path.read_text(encoding="utf-8")
            )

    def test_the_experiment_adds_no_llm_dependency_to_cad_core(self):
        root = SOURCE.parents[2] / "packages" / "cad-core" / "src"
        for path in (root / "cad_core").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for sdk in ("import anthropic", "from anthropic", "google.genai"):
                self.assertNotIn(sdk, source)

    def test_the_experiment_imports_no_provider_sdk_itself(self):
        """It reuses the stable provider; it does not open its own client."""
        imports = self.imports_of("cad_experimental")
        for sdk in ("anthropic", "google", "google.genai"):
            self.assertNotIn(sdk, imports)

    def test_the_experiment_defines_no_second_cad_schema(self):
        """It translates into the one contract; it does not restate it.

        The check is on *definitions*, not mentions. Naming a rule the
        existing validator emits -- a fixture that expects `S9`, a message
        that cites S14 -- is a reference to the one contract and is exactly
        right. What would be wrong is this package declaring rule constants
        of its own, so that is what is forbidden: no assignment here may bind
        a name shaped like a specification rule code.
        """
        import re as _re

        rule_name = _re.compile(r"^[SE]\d+$")
        for path in (SOURCE / "cad_experimental").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else [node.target]
                )
                for target in targets:
                    name = getattr(target, "id", "")
                    self.assertFalse(
                        rule_name.match(name),
                        f"{path.name} defines rule constant {name}",
                    )

    def test_the_experiment_implements_no_v1_rule_text(self):
        """It must not copy the contract's rule wording into itself."""
        for path in (SOURCE / "cad_experimental").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            for phrase in (
                "schema_version is a valid version string",
                "No unknown fields anywhere in the document",
                "features is an array with at least one element",
            ):
                self.assertNotIn(phrase, source, path.name)


class ExecutionBoundaryTests(unittest.TestCase):
    """No module in the experiment can execute anything, by construction."""

    #: Bare builtins that execute a string. Never legitimate here.
    FORBIDDEN_BUILTINS = {
        "eval", "exec", "compile", "__import__", "execfile", "input",
    }
    #: Dangerous only as a method on something -- `subprocess.run`, not a
    #: local function that happens to be called `run`.
    FORBIDDEN_METHODS = {
        "system", "popen", "spawn", "fork", "run", "call", "check_output",
        "Popen", "load_module", "loads",
    }
    #: Modules whose methods are ordinary data handling.
    SAFE_OWNERS = {"re", "json", "math", "hashlib", "time", "argparse"}
    FORBIDDEN_MODULES = {
        "subprocess", "pickle", "shelve", "marshal", "ctypes", "socket",
        "importlib", "runpy", "pty", "commands", "requests", "urllib",
        "httpx", "http",
    }

    def modules(self):
        for path in sorted((SOURCE / "cad_experimental").rglob("*.py")):
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_no_module_calls_an_execution_primitive(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                function = node.func
                if isinstance(function, ast.Name):
                    if function.id in self.FORBIDDEN_BUILTINS:
                        self.fail(f"{path.name} calls {function.id}()")
                elif isinstance(function, ast.Attribute):
                    owner = getattr(function.value, "id", "")
                    if owner in self.SAFE_OWNERS:
                        continue
                    if function.attr in self.FORBIDDEN_METHODS:
                        self.fail(
                            f"{path.name} calls {owner}.{function.attr}()"
                        )

    def test_no_module_imports_an_execution_or_network_module(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in self.FORBIDDEN_MODULES:
                        self.fail(f"{path.name} imports {name}")

    def test_the_parser_and_adapter_touch_no_filesystem(self):
        """The two modules that read model output open nothing at all."""
        # `sketch.py` and `build.py` were added after this guard and were
        # not covered by it -- found by the Stage 39 audit. The two CLI
        # entry points (`harness.py`, `local_plan_provider.py`) DO open a
        # file, at a path a developer typed on the command line, and are
        # deliberately out of scope here: the rule is that nothing on the
        # model's data path touches the filesystem.
        for module in ("parser.py", "adapter.py", "plan.py", "validation.py",
                       "sketch.py", "build.py"):
            path = SOURCE / "cad_experimental" / module
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", None)
                    self.assertNotEqual(name, "open", f"{module} opens a file")

    def test_no_module_uses_dynamic_attribute_lookup_on_model_data(self):
        """`getattr` is used only with literal names, never a parsed string."""
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "getattr"
                    and len(node.args) >= 2
                    and not isinstance(node.args[1], ast.Constant)
                ):
                    self.fail(f"{path.name} does getattr with a computed name")


if __name__ == "__main__":
    unittest.main()
