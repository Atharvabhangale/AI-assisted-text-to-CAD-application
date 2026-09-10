"""The harness, and the live path's wiring -- proven without a live call."""

from __future__ import annotations

import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from cad_core.application_service import CadApplicationService

from cad_experimental import harness
from cad_experimental.config import DEFAULT_MODEL, ExperimentalConfig
from cad_experimental.generation import OperationPlanService, PlanOutcome

from stubs import FailingModel, StubModel, box_plan


class CaseTests(unittest.TestCase):
    def test_the_five_required_cases_are_present(self):
        self.assertEqual(len(harness.CASES), 5)

    def test_the_prompts_are_the_ones_asked_for(self):
        prompts = [case.text for case in harness.CASES]
        self.assertIn(
            "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm "
            "thick.",
            prompts,
        )
        self.assertIn(
            "Create a cylinder 20 mm in diameter and 50 mm tall along the +Z "
            "axis.",
            prompts,
        )
        self.assertIn("Create a 100 mm by 60 mm by 10 mm box.", prompts)
        self.assertIn("Create a sphere with a 20 mm diameter.", prompts)
        self.assertIn(
            "Create a cylinder and a box joined together.", prompts
        )

    def test_the_two_refusal_cases_expect_unsupported(self):
        by_id = {case.id: case for case in harness.CASES}
        for case_id in ("4-sphere-unsupported", "5-join-unsupported"):
            self.assertIs(
                by_id[case_id].expectation.outcome, PlanOutcome.UNSUPPORTED
            )

    def test_expected_volumes_are_computed_not_transcribed(self):
        by_id = {case.id: case for case in harness.CASES}
        self.assertAlmostEqual(
            by_id["1-box-plate"].expectation.volume_mm3, 60000.0, places=9
        )
        self.assertAlmostEqual(
            by_id["2-cylinder-axis"].expectation.volume_mm3,
            math.pi * 100.0 * 50.0,
            places=9,
        )


class RunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def test_the_self_check_stub_answers_every_case(self):
        planner = OperationPlanService(
            harness._CorpusStub(), ExperimentalConfig(model="corpus-stub")
        )
        data = harness.run(planner, self.service, live=False)
        totals = data["totals"]
        self.assertEqual(totals["cases"], 5)
        self.assertEqual(totals["answered"], 5)
        self.assertEqual(totals["outcome_match"], 5)
        self.assertEqual(totals["semantically_correct"], 5)
        self.assertEqual(totals["built"], 3)

    def test_every_built_case_has_the_expected_volume(self):
        planner = OperationPlanService(
            harness._CorpusStub(), ExperimentalConfig(model="corpus-stub")
        )
        data = harness.run(planner, self.service, live=False)
        built = [r for r in data["results"] if r["built"]]
        self.assertEqual(len(built), 3)
        for entry in built:
            self.assertTrue(entry["volume_matches"], entry["case_id"])
            self.assertIsNotNone(entry["render_triangles"])

    def test_a_provider_failure_is_unanswered_not_incorrect(self):
        planner = OperationPlanService(FailingModel(), ExperimentalConfig())
        data = harness.run(planner, self.service, live=False)
        totals = data["totals"]
        self.assertEqual(totals["answered"], 0)
        self.assertEqual(totals["unanswered"], 5)
        # The crucial separation: nothing counted as a wrong answer.
        self.assertEqual(totals["semantically_correct"], 0)
        self.assertEqual(totals["outcome_match"], 0)
        for entry in data["results"]:
            self.assertEqual(entry["provider_error_kind"], "rate_limited")

    def test_a_wrong_but_valid_plan_is_caught_as_semantically_wrong(self):
        """Valid geometry is not correct geometry.

        The model returns a well-formed, buildable box with the extents
        swapped. It parses, it validates, it builds -- and it is the wrong
        part. The harness must say so.
        """
        swapped = json.dumps(
            {
                "status": "generated",
                "summary": "a plate",
                "operations": [
                    {
                        "id": "body",
                        "type": "box",
                        "parameters": {"x": 60, "y": 100, "z": 10},
                    }
                ],
            }
        )
        planner = OperationPlanService(StubModel(swapped), ExperimentalConfig())
        result = harness.run_case(harness.CASES[0], planner, self.service)
        self.assertTrue(result.parsed)
        self.assertTrue(result.plan_valid)
        self.assertTrue(result.built)
        self.assertFalse(result.semantically_correct)
        # Identical volume, wrong part -- the volume check cannot catch this.
        self.assertTrue(result.volume_matches)
        self.assertTrue(any("expected size" in n for n in result.notes))

    def test_the_run_records_the_prompt_identity(self):
        from cad_experimental.prompt import PROMPT_VERSION

        planner = OperationPlanService(StubModel(box_plan()), ExperimentalConfig())
        data = harness.run(planner, None, live=False)
        self.assertEqual(data["prompt_version"], PROMPT_VERSION)
        self.assertFalse(data["live"])

    def test_the_report_says_a_stub_run_is_not_model_quality(self):
        planner = OperationPlanService(StubModel(box_plan()), ExperimentalConfig())
        report = harness.format_report(harness.run(planner, None, live=False))
        self.assertIn("Says nothing about real model quality", report)


class LiveGateTests(unittest.TestCase):
    """A credential must never be enough on its own to spend money."""

    def run_main(self, argv, environ):
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", environ, clear=True):
            with redirect_stdout(buffer):
                code = harness.main(argv)
        return code, buffer.getvalue()

    def test_without_live_nothing_is_attempted(self):
        code, output = self.run_main(
            [], {"ANTHROPIC_API_KEY": "unused-because-live-is-required"}
        )
        self.assertEqual(code, 0)
        self.assertIn("NOT_RUN", output)

    def test_live_without_a_credential_reports_not_run(self):
        code, output = self.run_main(["--live", "--no-build"], {})
        self.assertEqual(code, 1)
        self.assertIn("NOT_RUN", output)
        self.assertIn("ANTHROPIC_API_KEY is not set", output)

    def test_live_without_a_credential_substitutes_nothing(self):
        """A Gemini key present must not be borrowed for this run."""
        code, output = self.run_main(
            ["--live", "--no-build"], {"GEMINI_API_KEY": "not-for-this"}
        )
        self.assertEqual(code, 1)
        self.assertIn("NOT_RUN", output)
        self.assertNotIn("not-for-this", output)

    def test_no_credential_value_is_ever_printed(self):
        secret = "sk-ant-SENTINEL-NEVER-PRINT"
        _, output = self.run_main([], {"ANTHROPIC_API_KEY": secret})
        self.assertNotIn(secret, output)

    def test_the_live_path_pins_the_experimental_model(self):
        """The provider is built with Haiku 4.5, not the production default.

        Proven by intercepting the provider constructor, so the assertion
        holds without a credential and without a call.
        """
        captured = {}

        class Recorded:
            @classmethod
            def from_environment(cls, config):
                captured["config"] = config
                raise SystemExit(0)  # stop before any request

        with mock.patch.dict(
            "os.environ", {"ANTHROPIC_API_KEY": "present"}, clear=True
        ):
            with mock.patch(
                "cad_ai.anthropic_provider.AnthropicTextToCadModel", Recorded
            ):
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        harness.main(["--live", "--no-build"])

        self.assertEqual(captured["config"].model, DEFAULT_MODEL)
        self.assertEqual(captured["config"].model, "claude-haiku-4-5-20251001")
        self.assertEqual(captured["config"].provider, "anthropic")

    def test_an_override_reaches_the_provider(self):
        captured = {}

        class Recorded:
            @classmethod
            def from_environment(cls, config):
                captured["config"] = config
                raise SystemExit(0)

        with mock.patch.dict(
            "os.environ",
            {
                "ANTHROPIC_API_KEY": "present",
                "CAD_EXPERIMENTAL_MODEL": "claude-haiku-4-5",
            },
            clear=True,
        ):
            with mock.patch(
                "cad_ai.anthropic_provider.AnthropicTextToCadModel", Recorded
            ):
                with self.assertRaises(SystemExit):
                    with redirect_stdout(io.StringIO()):
                        harness.main(["--live", "--no-build"])

        self.assertEqual(captured["config"].model, "claude-haiku-4-5")

    def test_a_production_override_does_not_reach_the_experiment(self):
        """`CAD_AI_MODEL` belongs to the stable path and must not leak in."""
        with mock.patch.dict(
            "os.environ", {"CAD_AI_MODEL": "some-other-model"}, clear=True
        ):
            from cad_experimental.config import config_from_environment

            self.assertEqual(config_from_environment().model, DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
