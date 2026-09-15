"""Stage 48: the evaluation instrument's own tests.

These do not measure a model. They measure the measurer: that Stage 43 is
still exactly what it was, that Stage 48 is versioned separately from it,
that every fingerprint is deterministic, that the legacy subset really is
comparable, that a capability case cannot leak into a V1 rate, that a
provider failure stays apart from a model failure, that nothing strips a
fence or retries or repairs, that a baseline cannot be overwritten, and that
a recorded result carries everything needed to reproduce it.

Not one test here makes a network call, and a test that could reach a
provider is a bug in the test.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from cad_experimental import representation_comparison as stage40
from cad_experimental import stage43_structured_comparison as stage43
from cad_experimental import stage48_capability_evaluation as stage48
from cad_experimental import stage48_corpus as corpus
from cad_experimental.comparison_corpus import CASES_BY_ID as FROZEN_CASES
from cad_experimental.plan import executable_schema, provider_schema


# --- Stage 43 is untouched --------------------------------------------------


class Stage43IsUnchangedTests(unittest.TestCase):
    """Stage 48 exists so that Stage 43 need not move. Prove it did not."""

    def test_stage_43_still_sends_the_executable_schema(self) -> None:
        self.assertEqual(
            stage43.plan_schema_for_provider(),
            stage40.sanitise_schema(executable_schema()),
        )

    def test_stage_43_schema_fingerprint_is_still_the_recorded_one(
        self,
    ) -> None:
        """The fingerprint the Stage 43 baseline records, still produced.

        If this moves, a re-run of Stage 43 would no longer reproduce Stage
        43, and its recorded result would stop being attributable to the
        instrument that produced it.
        """
        facts = stage43.schema_facts(stage43.plan_schema_for_provider())
        self.assertEqual(facts["fingerprint"][:16], "54759d1e16cfe634")

    def test_stage_43_does_not_follow_the_widened_provider_schema(
        self,
    ) -> None:
        self.assertNotEqual(
            stage43.plan_schema_for_provider(),
            stage40.sanitise_schema(provider_schema()),
        )

    def test_stage_40_structured_output_flag_is_still_false(self) -> None:
        self.assertFalse(stage40.STRUCTURED_OUTPUT_ENABLED)

    def test_stage_43_still_runs_the_frozen_thirteen_cases(self) -> None:
        self.assertEqual(len(stage43.CASES), 13)

    def test_stage_48_imports_stage_43_rather_than_copying_it(self) -> None:
        """The provider limits are one number in one place, not two."""
        self.assertIs(
            stage48.PER_OBJECT_OPTIONAL_LIMIT,
            stage43.PER_OBJECT_OPTIONAL_LIMIT,
        )
        self.assertIs(stage48.schema_facts, stage43.schema_facts)


# --- Stage 48 is its own instrument ----------------------------------------


class SeparateInstrumentTests(unittest.TestCase):

    def test_the_stage_is_48(self) -> None:
        self.assertEqual(stage48.STAGE, 48)
        self.assertNotEqual(stage48.STAGE, stage43.STAGE)

    def test_the_result_kind_cannot_be_confused_with_stage_43(self) -> None:
        self.assertEqual(stage48.RESULT_KIND, "stage48-capability-evaluation")
        self.assertNotEqual(stage48.RESULT_KIND, stage43.RESULT_KIND)

    def test_the_corpus_is_versioned_separately(self) -> None:
        from cad_experimental.comparison_corpus import CORPUS_VERSION

        self.assertNotEqual(
            corpus.corpus_fingerprint(),
            __import__(
                "cad_experimental.comparison_corpus", fromlist=["x"]
            ).corpus_fingerprint(),
        )
        # Same version STRING, different instrument: the fingerprint is what
        # tells them apart, which is why a result records both.
        self.assertEqual(corpus.STAGE48_CORPUS_VERSION, "1.0.0")
        self.assertEqual(CORPUS_VERSION, "1.0.0")

    def test_the_default_plan_schema_is_the_widened_one(self) -> None:
        self.assertEqual(stage48.DEFAULT_PLAN_SCHEMA, "provider")
        self.assertEqual(
            stage48.plan_schema_for(),
            stage40.sanitise_schema(provider_schema()),
        )

    def test_the_widened_schema_admits_every_operation_type(self) -> None:
        from cad_experimental.plan import OPERATION_TYPES

        branches = (
            stage48.plan_schema_for()["properties"]["operations"]["items"]
            ["anyOf"]
        )
        named = set()
        for branch in branches:
            kind = branch["properties"]["type"]
            named.update(
                [kind["const"]] if "const" in kind else kind["enum"]
            )
        self.assertEqual(named, set(OPERATION_TYPES))

    def test_no_schema_variant_is_selected_automatically(self) -> None:
        """A caller names the schema; nothing falls back to another one."""
        with self.assertRaises(KeyError):
            stage48.plan_schema_for("whatever-compiles")

    def test_the_result_states_its_relationship_to_stage_43(self) -> None:
        self.assertIn("NOT a re-run", stage48.RELATIONSHIP_TO_STAGE_43)
        self.assertIn("frozen comparison", stage48.RELATIONSHIP_TO_STAGE_43)


# --- fingerprints -----------------------------------------------------------


class FingerprintTests(unittest.TestCase):

    def test_the_corpus_fingerprint_is_deterministic(self) -> None:
        self.assertEqual(
            corpus.corpus_fingerprint(), corpus.corpus_fingerprint()
        )
        self.assertEqual(len(corpus.corpus_fingerprint()), 64)

    def test_the_scoring_fingerprint_is_deterministic(self) -> None:
        self.assertEqual(
            stage48.scoring_fingerprint(), stage48.scoring_fingerprint()
        )
        self.assertEqual(len(stage48.scoring_fingerprint()), 64)

    def test_changing_a_metric_definition_changes_the_fingerprint(
        self,
    ) -> None:
        """The whole point of hashing the rules rather than the code."""
        before = stage48.scoring_fingerprint()
        original = dict(stage48.SCORING_RULES)
        try:
            stage48.SCORING_RULES = dict(  # type: ignore[assignment]
                original, build_success="anything the kernel did not refuse"
            )
            self.assertNotEqual(stage48.scoring_fingerprint(), before)
        finally:
            stage48.SCORING_RULES = original  # type: ignore[assignment]
        self.assertEqual(stage48.scoring_fingerprint(), before)

    def test_changing_a_tolerance_changes_the_fingerprint(self) -> None:
        before = stage48.scoring_fingerprint()
        original = dict(stage48.SCORING_TOLERANCES)
        try:
            stage48.SCORING_TOLERANCES = dict(  # type: ignore[assignment]
                original, volume_rtol=1e-2
            )
            self.assertNotEqual(stage48.scoring_fingerprint(), before)
        finally:
            stage48.SCORING_TOLERANCES = original  # type: ignore[assignment]

    def test_editing_a_case_changes_the_corpus_fingerprint(self) -> None:
        before = corpus.corpus_fingerprint()
        original = corpus.CASES
        try:
            corpus.CASES = original[:-1]  # type: ignore[assignment]
            self.assertNotEqual(corpus.corpus_fingerprint(), before)
        finally:
            corpus.CASES = original  # type: ignore[assignment]
        self.assertEqual(corpus.corpus_fingerprint(), before)

    def test_every_identity_a_run_needs_is_recorded(self) -> None:
        prints = stage48.fingerprints()
        self.assertEqual(
            set(prints),
            {"evaluation_version", "corpus_version", "corpus", "scoring",
             "model", "prompt", "schema"},
        )
        self.assertEqual(
            set(prints["model"]),
            {"model", "max_output_tokens", "timeout_seconds",
             "structured_output_enabled", "plan_schema_choice"},
        )
        for arm in (stage40.V1, stage40.PLAN):
            self.assertEqual(
                set(prints["prompt"][arm]),
                {"version", "fingerprint", "characters"},
            )
            self.assertEqual(len(prints["schema"][arm]), 64)

    def test_the_schema_choice_reaches_the_fingerprint(self) -> None:
        widened = stage48.fingerprints("provider")
        narrow = stage48.fingerprints("executable")
        self.assertNotEqual(
            widened["schema"][stage40.PLAN], narrow["schema"][stage40.PLAN]
        )
        self.assertEqual(
            narrow["model"]["plan_schema_choice"], "executable"
        )


# --- the legacy subset really is comparable --------------------------------


class LegacyComparabilityTests(unittest.TestCase):

    def test_every_legacy_case_is_a_frozen_stage_40_case(self) -> None:
        for item in corpus.cases_in(corpus.LEGACY):
            self.assertIn(item.identifier, FROZEN_CASES)

    def test_legacy_text_is_identical_to_the_frozen_corpus(self) -> None:
        """Not 'similar'. Identical, character for character."""
        for item in corpus.cases_in(corpus.LEGACY):
            self.assertEqual(item.text, FROZEN_CASES[item.identifier].text)

    def test_legacy_expectations_are_the_frozen_expectations(self) -> None:
        for item in corpus.cases_in(corpus.LEGACY):
            frozen = FROZEN_CASES[item.identifier]
            self.assertEqual(item.expect_plan, frozen.expect_plan)
            self.assertEqual(item.expect_v1, frozen.expect_v1)
            self.assertEqual(item.geometry, frozen.geometry)
            self.assertEqual(
                item.required_plan_operations,
                frozen.required_plan_operations,
            )
            self.assertEqual(
                item.required_v1_features, frozen.required_v1_features
            )

    def test_all_thirteen_stage_40_cases_are_present(self) -> None:
        self.assertEqual(
            {item.identifier for item in corpus.cases_in(corpus.LEGACY)},
            set(FROZEN_CASES),
        )

    def test_both_arms_answer_every_legacy_case(self) -> None:
        for item in corpus.cases_in(corpus.LEGACY):
            self.assertIsNotNone(item.expect_v1, item.identifier)

    def test_the_preflight_agrees_the_groups_are_comparable(self) -> None:
        check = stage48.preflight(geometry=False)
        self.assertTrue(check["groups"]["correct"])
        self.assertEqual(check["groups"]["legacy_text_drifted_from_stage_40"],
                         [])

    def test_drifted_legacy_text_is_detected(self) -> None:
        """The check is a real check, not a formality."""
        original = corpus.CASES
        drifted = corpus.CapabilityCase(
            identifier="01-plate-worded",
            group=corpus.LEGACY,
            category=corpus.A_PRIMITIVES,
            text="a different request entirely",
            expect_plan=corpus.EXPECT_BUILD,
            expect_v1=corpus.EXPECT_BUILD,
        )
        try:
            corpus.CASES = (drifted,) + tuple(  # type: ignore[assignment]
                item for item in original if item.identifier != drifted.identifier
            )
            check = stage48.preflight(geometry=False)
            self.assertFalse(check["groups"]["correct"])
            self.assertIn(
                "01-plate-worded",
                check["groups"]["legacy_text_drifted_from_stage_40"],
            )
        finally:
            corpus.CASES = original  # type: ignore[assignment]


# --- the capability group is categorised correctly -------------------------


class CapabilityGroupTests(unittest.TestCase):

    def test_no_capability_case_carries_a_v1_expectation(self) -> None:
        """A capability case must not be able to enter a V1 rate."""
        for item in corpus.cases_in(corpus.CAPABILITY):
            self.assertIsNone(item.expect_v1, item.identifier)

    def test_the_v1_runner_refuses_a_capability_case(self) -> None:
        item = corpus.case("E1-bolt-circle-radial")
        with self.assertRaises(ValueError) as raised:
            stage48.run_v1_attempt(object(), object(), item, 1)
        self.assertIn("no V1 expectation", str(raised.exception))

    def test_the_capability_group_reaches_beyond_stage_43(self) -> None:
        check = stage48.preflight(geometry=False)
        reached = set(check["groups"]["capabilities_beyond_stage_43"])
        self.assertTrue(
            {"sketch", "extrude", "revolve", "pattern", "straight",
             "circular"}.issubset(reached),
            reached,
        )

    def test_every_case_has_a_known_group_and_category(self) -> None:
        for item in corpus.CASES:
            self.assertIn(item.group, corpus.GROUPS, item.identifier)
            self.assertIn(item.category, corpus.CATEGORIES, item.identifier)

    def test_every_case_has_an_explicit_expected_class(self) -> None:
        for item in corpus.CASES:
            self.assertIn(
                item.expect_plan, corpus.EXPECTATIONS, item.identifier
            )

    def test_every_brief_category_is_exercised(self) -> None:
        for category in corpus.CATEGORIES:
            self.assertTrue(
                corpus.cases_in_category(category),
                f"no case in category {category}",
            )

    def test_a_buildable_case_has_geometry_and_a_refusal_does_not(
        self,
    ) -> None:
        for item in corpus.CASES:
            if item.expect_plan == corpus.EXPECT_BUILD:
                self.assertIsNotNone(item.geometry, item.identifier)
            else:
                self.assertIsNone(item.geometry, item.identifier)

    def test_case_identifiers_are_unique(self) -> None:
        self.assertEqual(len(corpus.CASE_IDS), len(set(corpus.CASE_IDS)))

    def test_the_corpus_is_compact(self) -> None:
        """A deliberate size, not an accident. Thirty cases, five attempts,
        two arms on thirteen of them: 43 calls an attempt, 215 in total."""
        self.assertEqual(len(corpus.CASES), 30)
        self.assertEqual(len(corpus.cases_in(corpus.LEGACY)), 13)
        self.assertEqual(len(corpus.cases_in(corpus.CAPABILITY)), 17)


# --- provider failure stays apart from model failure -----------------------


class SeparationOfFailuresTests(unittest.TestCase):

    def test_a_provider_error_is_its_own_category(self) -> None:
        self.assertNotIn(
            stage40.PROVIDER_ERROR, stage40.SUCCESS_CATEGORIES
        )
        for other in (
            stage40.PARSER_REJECTED, stage40.PLAN_VALIDATION_REJECTED,
            stage40.BUILD_FAILED, stage40.SEMANTICALLY_INCORRECT,
            stage40.CORRECT_UNSUPPORTED, stage48.WRONG_SELECTOR,
            stage48.INVENTED_MISSING_VALUE,
        ):
            self.assertNotEqual(stage40.PROVIDER_ERROR, other)

    def test_a_provider_error_leaves_every_denominator(self) -> None:
        answered = stage48.Stage48Record(
            case_id="x", group=corpus.LEGACY, category=corpus.A_PRIMITIVES,
            representation=stage40.PLAN, attempt=1,
            model_output_valid=True, build_success=True,
            semantically_correct=True, category_code=stage40.OK,
        )
        failed = stage48.Stage48Record(
            case_id="x", group=corpus.LEGACY, category=corpus.A_PRIMITIVES,
            representation=stage40.PLAN, attempt=2,
            category_code=stage40.PROVIDER_ERROR,
        )
        self.assertEqual(
            stage48._rate([answered, failed], "semantically_correct"), 1.0
        )
        self.assertIsNone(stage48._rate([failed], "semantically_correct"))

    def test_the_five_failure_kinds_are_never_conflated(self) -> None:
        """Provider failure, invalid output, invalid plan, build failure,
        semantic mismatch and correct refusal are six distinct codes."""
        codes = {
            stage40.PROVIDER_ERROR, stage40.PARSER_REJECTED,
            stage40.PLAN_VALIDATION_REJECTED, stage40.BUILD_FAILED,
            stage40.SEMANTICALLY_INCORRECT, stage40.CORRECT_UNSUPPORTED,
        }
        self.assertEqual(len(codes), 6)

    def test_correct_refusals_count_as_successes_and_wrong_ones_do_not(
        self,
    ) -> None:
        self.assertIn(stage40.CORRECT_UNSUPPORTED, stage40.SUCCESS_CATEGORIES)
        self.assertIn(
            stage40.CORRECT_VALID_UNEXECUTABLE, stage40.SUCCESS_CATEGORIES
        )
        self.assertNotIn(stage40.WRONGLY_REFUSED, stage40.SUCCESS_CATEGORIES)
        self.assertNotIn(stage48.WRONG_SELECTOR, stage40.SUCCESS_CATEGORIES)
        self.assertNotIn(
            stage48.INVENTED_MISSING_VALUE, stage40.SUCCESS_CATEGORIES
        )
        self.assertNotIn(
            stage48.WRONG_SELECTOR, stage48.STAGE48_SUCCESS_CATEGORIES
        )
        self.assertNotIn(
            stage48.INVENTED_MISSING_VALUE,
            stage48.STAGE48_SUCCESS_CATEGORIES,
        )

    def test_summarise_never_produces_one_number_over_both_groups(
        self,
    ) -> None:
        records = [
            stage48.Stage48Record(
                case_id=item.identifier, group=item.group,
                category=item.category, representation=stage40.PLAN,
                attempt=1, category_code=stage40.OK,
                semantically_correct=True,
            )
            for item in corpus.CASES
        ]
        summary = stage48.summarise(records, corpus.CASES)
        self.assertEqual(set(summary), {corpus.LEGACY, corpus.CAPABILITY})
        for group in summary.values():
            self.assertNotIn("overall", group)
            self.assertNotIn("combined", group)


# --- no fence stripping, no retry, no repair -------------------------------


class NoRepairTests(unittest.TestCase):

    SOURCES = (
        "stage48_capability_evaluation.py",
        "stage48_corpus.py",
    )

    def _source(self, name: str) -> str:
        import cad_experimental

        path = os.path.join(
            os.path.dirname(cad_experimental.__file__), name
        )
        with open(path, encoding="utf-8") as handle:
            return handle.read()

    def test_no_module_strips_a_markdown_fence(self) -> None:
        for name in self.SOURCES:
            body = self._source(name)
            for forbidden in (
                '.replace("```"', ".strip('`')", '.strip("`")',
                '.lstrip("`")', '.removeprefix("```")',
            ):
                self.assertNotIn(forbidden, body, f"{name}: {forbidden}")

    def test_no_module_retries_or_re_prompts(self) -> None:
        for name in self.SOURCES:
            body = self._source(name)
            for forbidden in ("for retry", "while retry", "max_retries",
                              "re_prompt", "reprompt", "backoff"):
                self.assertNotIn(forbidden, body, f"{name}: {forbidden}")

    def test_the_frozen_parsers_are_untouched(self) -> None:
        """Stage 48 uses the same parser everything else uses."""
        from cad_experimental import parser

        source = self._source("parser.py")
        self.assertNotIn("```", source)
        self.assertTrue(hasattr(parser, "parse_plan_text"))

    def test_one_call_per_attempt_and_no_more(self) -> None:
        """A stub that counts calls proves it, rather than a source scan."""
        stub = stage48.ReferencePlanStub()
        service = _service()
        item = corpus.case("B6-plate-subtract-cylinder")
        record = stage48.run_plan_attempt(stub, service, item, 1)
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(record.category_code, stage40.OK)

    def test_a_provider_error_is_not_retried(self) -> None:
        class Failing:
            name = "failing"

            def __init__(self) -> None:
                self.calls = 0

            @property
            def config(self):  # noqa: ANN201 - test double
                return None

            def generate(self, request):  # noqa: ANN001, ANN201
                from cad_ai.provider import ProviderError

                self.calls += 1
                raise ProviderError("the provider is down")

        failing = Failing()
        record = stage48.run_plan_attempt(
            failing, _service(), corpus.case("01-plate-worded"), 1
        )
        self.assertEqual(failing.calls, 1)
        self.assertEqual(record.category_code, stage40.PROVIDER_ERROR)
        self.assertFalse(record.model_output_valid)


# --- baselines cannot be overwritten ---------------------------------------


class BaselineProtectionTests(unittest.TestCase):

    def test_the_protected_directories_are_stage_40_and_stage_43(
        self,
    ) -> None:
        self.assertEqual(
            set(stage48.PROTECTED_BASELINE_DIRECTORIES),
            {"stage40-v1-vs-operation-plan", "stage43-structured-output"},
        )

    def test_the_result_directory_is_a_new_one(self) -> None:
        self.assertEqual(
            stage48.RESULT_DIRECTORY,
            "docs/evaluation-baselines/stage48-widened-schema",
        )
        self.assertFalse(
            stage48.writes_into_a_baseline(stage48.RESULT_DIRECTORY)
        )

    def test_a_path_inside_a_baseline_is_recognised(self) -> None:
        for path in (
            "docs/evaluation-baselines/stage40-v1-vs-operation-plan/x.json",
            "docs/evaluation-baselines/stage43-structured-output/run.json",
            "/tmp/stage43-structured-output/anything.json",
        ):
            self.assertTrue(stage48.writes_into_a_baseline(path), path)

    def test_writing_into_a_baseline_raises(self) -> None:
        target = os.path.join(
            tempfile.mkdtemp(), "stage43-structured-output", "run.json"
        )
        with self.assertRaises(stage48.BaselineMissing):
            stage48._write(target, {"anything": True})
        self.assertFalse(os.path.exists(target))

    def test_the_cli_refuses_such_a_path_before_doing_anything(self) -> None:
        code = stage48.main([
            "--check", "--out",
            "docs/evaluation-baselines/stage43-structured-output/x.json",
        ])
        self.assertEqual(code, 1)

    def test_the_committed_baselines_are_present_and_unchanged(self) -> None:
        check = stage48._baseline_check()
        self.assertTrue(check["intact"], check["files"])
        self.assertEqual(len(check["files"]), 7)

    def test_a_changed_baseline_stops_a_run(self) -> None:
        original = dict(stage48.BASELINE_DIGESTS)
        try:
            stage48.BASELINE_DIGESTS = dict(  # type: ignore[assignment]
                original,
                **{
                    "docs/evaluation-baselines/stage43-structured-output/"
                    "README.md": "0" * 64
                },
            )
            with self.assertRaises(stage48.BaselineMissing):
                stage48.run(live=False, model_factory=lambda: object())
        finally:
            stage48.BASELINE_DIGESTS = original  # type: ignore[assignment]


def _service():
    from cad_core.application_service import CadApplicationService

    return CadApplicationService.local(tempfile.mkdtemp())


# --- the benchmark never leaks into what the model sees --------------------


class NoAnswerKeyTests(unittest.TestCase):
    """A reference plan proves an expectation is reachable. It is not an
    answer key, and nothing the model sees may contain one."""

    def test_no_case_text_appears_in_either_prompt(self) -> None:
        from cad_ai.prompt import system_prompt as v1_prompt
        from cad_experimental.prompt import system_prompt as plan_prompt

        V1_PROMPT, PLAN_PROMPT = v1_prompt(), plan_prompt()
        for item in corpus.CASES:
            self.assertNotIn(item.text, PLAN_PROMPT, item.identifier)
            self.assertNotIn(item.text, V1_PROMPT, item.identifier)

    def test_no_reference_plan_appears_in_either_prompt(self) -> None:
        from cad_ai.prompt import system_prompt as v1_prompt
        from cad_experimental.prompt import system_prompt as plan_prompt

        V1_PROMPT, PLAN_PROMPT = v1_prompt(), plan_prompt()
        for item in corpus.CASES:
            if item.reference_plan is None:
                continue
            serialised = json.dumps(item.reference_plan)
            self.assertNotIn(serialised, PLAN_PROMPT, item.identifier)
            self.assertNotIn(serialised, V1_PROMPT, item.identifier)

    def test_a_live_request_carries_only_the_case_text(self) -> None:
        """What actually reaches the provider: the system prompt, the
        request text, the schema, and a token ceiling. Nothing else."""

        class Recording:
            name = "recording"

            def __init__(self) -> None:
                self.seen = []

            @property
            def config(self):  # noqa: ANN201 - test double
                return None

            def generate(self, request):  # noqa: ANN001, ANN201
                from cad_ai.provider import ModelResponse

                self.seen.append(request)
                return ModelResponse(
                    text=json.dumps({
                        "status": "unsupported", "summary": "no",
                        "operations": [], "reason": "not measuring here",
                    }),
                    provider="recording", model="recording",
                    structured_output=False, stop_reason="end_turn", usage={},
                )

        inner = Recording()
        model = stage48.Stage48Model(inner)
        item = corpus.case("F1-drilled-plate-round-corners")
        stage48.run_plan_attempt(model, _service(), item, 1)
        self.assertEqual(len(inner.seen), 1)
        request = inner.seen[0]
        self.assertEqual(request.user_text, item.text)
        self.assertEqual(
            request.max_output_tokens, stage40.SHARED_MAX_OUTPUT_TOKENS
        )
        self.assertEqual(request.output_schema, stage48.plan_schema_for())
        # The whole reference plan, and each operation's own parameters --
        # not the bare ids, which are ordinary words ("plate", "bore") that
        # the prompt uses in English and whose presence proves nothing.
        self.assertNotIn(
            json.dumps(item.reference_plan), request.system
        )
        for operation in item.reference_plan["operations"]:
            self.assertNotIn(json.dumps(operation), request.system)
            self.assertNotIn(json.dumps(operation), request.user_text)
        self.assertEqual(
            set(vars(request)) - {"system", "user_text", "output_schema",
                                  "max_output_tokens"},
            set(),
            "a request carries a field this test has not accounted for",
        )

    def test_the_corpus_is_not_reachable_from_production_code(self) -> None:
        """An instrument, not production logic. Nothing in the transport,
        the AI layer, the core or the experimental app imports it."""
        import cad_ai
        import cad_api
        import cad_core

        roots = [
            os.path.dirname(cad_api.__file__),
            os.path.dirname(cad_ai.__file__),
            os.path.dirname(cad_core.__file__),
        ]
        import cad_experimental

        roots.append(
            os.path.join(os.path.dirname(cad_experimental.__file__), "app.py")
        )
        for root in roots:
            paths = (
                [root] if root.endswith(".py")
                else [
                    os.path.join(root, name)
                    for name in os.listdir(root) if name.endswith(".py")
                ]
            )
            for path in paths:
                with open(path, encoding="utf-8") as handle:
                    self.assertNotIn("stage48", handle.read(), path)


# --- the preflight ----------------------------------------------------------


class PreflightTests(unittest.TestCase):
    """The offline checks Phase 7 asks for. All free, none call a model."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.check = stage48.preflight()

    def test_it_makes_no_model_call_and_reports_ready(self) -> None:
        self.assertTrue(self.check["ready"], self.check)

    def test_both_schemas_pass_every_measured_provider_limit(self) -> None:
        for arm, value in self.check["arms"].items():
            self.assertTrue(value["compilable_offline"], (arm, value))
            self.assertEqual(value["violations"], ())

    def test_the_plan_schema_is_within_the_optional_property_limit(
        self,
    ) -> None:
        facts = self.check["arms"][stage40.PLAN]["facts"]
        self.assertLessEqual(
            facts["optional_properties"], stage40.OPTIONAL_PROPERTY_LIMIT
        )

    def test_no_single_object_exceeds_the_per_object_limit(self) -> None:
        for value in self.check["arms"].values():
            self.assertLessEqual(
                value["facts"]["worst_object_optional_properties"],
                stage43.PER_OBJECT_OPTIONAL_LIMIT,
            )

    def test_no_prohibited_keyword_is_present(self) -> None:
        for value in self.check["arms"].values():
            self.assertEqual(value["facts"]["rejected_keywords_present"], [])

    def test_every_object_closes_additional_properties(self) -> None:
        for value in self.check["arms"].values():
            self.assertTrue(
                value["facts"]["every_object_closes_additional_properties"]
            )

    def test_no_schema_uses_one_of(self) -> None:
        for value in self.check["arms"].values():
            self.assertFalse(value["facts"]["uses_one_of"])

    def test_the_plan_schema_stays_within_eight_branches(self) -> None:
        """Stage 41 measured the ceiling. A ninth branch is refused."""
        self.assertLessEqual(self.check["arms"][stage40.PLAN]["branches"], 8)

    def test_sanitising_the_plan_schema_changes_nothing(self) -> None:
        """It has been clean since Stage 41; if that regressed, say so."""
        self.assertTrue(self.check["sanitising_is_identity_for_the_plan"])

    def test_every_buildable_expectation_agrees_with_the_kernel(
        self,
    ) -> None:
        reference = self.check["reference_geometry"]
        self.assertTrue(reference["correct"], reference["failed"])
        self.assertEqual(reference["failed"], [])
        self.assertGreaterEqual(reference["checked"], 19)

    def test_some_reference_plans_need_the_graph_executor(self) -> None:
        """The evidence that this corpus reaches past Stage 43's runner:
        a plan Stage 40's ``plan_to_document`` path cannot build at all."""
        self.assertGreaterEqual(
            len(self.check["reference_geometry"]["executed_by_graph"]), 5
        )

    def test_every_invalid_plan_is_refused_by_the_right_layer(self) -> None:
        invalid = self.check["invalid_plans"]
        self.assertTrue(invalid["correct"], invalid["failed"])
        self.assertEqual(invalid["checked"], len(corpus.INVALID_PLANS))

    def test_the_invalid_plans_cover_the_brief_s_four_kinds(self) -> None:
        kinds = {broken.kind for broken in corpus.INVALID_PLANS}
        self.assertTrue(
            {"bad reference", "incompatible reference", "ambiguous selector",
             "unsupported geometry semantics"}.issubset(kinds),
            kinds,
        )

    def test_the_fingerprints_are_carried_in_the_preflight(self) -> None:
        self.assertEqual(
            self.check["fingerprints"]["corpus"], corpus.corpus_fingerprint()
        )
        self.assertEqual(
            self.check["fingerprints"]["scoring"],
            stage48.scoring_fingerprint(),
        )

    def test_the_expected_output_path_is_reported(self) -> None:
        self.assertEqual(
            self.check["result_directory"], stage48.RESULT_DIRECTORY
        )

    def test_the_check_cli_is_free_and_succeeds(self) -> None:
        self.assertEqual(stage48.main(["--check", "--no-geometry-check"]), 0)

    def test_the_list_cli_is_free_and_succeeds(self) -> None:
        self.assertEqual(stage48.main(["--list"]), 0)


# --- a live run cannot start by accident -----------------------------------


class LiveGateTests(unittest.TestCase):

    def test_without_live_the_cli_refuses_and_spends_nothing(self) -> None:
        self.assertEqual(stage48.main([]), 2)

    def test_run_without_live_and_without_a_stub_raises(self) -> None:
        with self.assertRaises(stage40.CredentialUnavailable):
            stage48.run(live=False)

    def test_the_credential_variable_is_the_project_s_own(self) -> None:
        self.assertEqual(
            stage48.OPERATOR_KEY_VARIABLE, "CAD_ANTHROPIC_API_KEY"
        )

    def test_nothing_here_reads_a_credential_value(self) -> None:
        import cad_experimental

        path = os.path.join(
            os.path.dirname(cad_experimental.__file__),
            "stage48_capability_evaluation.py",
        )
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        self.assertNotIn("os.environ[", body)
        self.assertNotIn("getenv", body)


# --- the self-check ---------------------------------------------------------


class SelfCheckTests(unittest.TestCase):
    """The plumbing check. Its score is about this harness, never a model."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = stage48.self_check()

    def test_it_is_stamped_as_not_a_model_result(self) -> None:
        self.assertFalse(self.data["is_live_model_result"])
        self.assertIn("not a model", self.data["source"])

    def test_the_stub_announces_itself_as_local_development(self) -> None:
        self.assertTrue(stage48.ReferencePlanStub.is_local_development)
        self.assertFalse(
            hasattr(stage48.real_model, "is_local_development")
        )

    def test_every_reference_plan_scores_correct(self) -> None:
        """If a scoring rule broke, this is where it shows."""
        for group, summary in self.data["summary"].items():
            arm = summary[stage40.PLAN]
            self.assertEqual(
                arm["semantically_correct"], 1.0,
                f"{group}: {arm['categories']}",
            )
            self.assertEqual(arm["provider_errors"], 0)

    def test_the_self_check_runs_the_plan_arm_only(self) -> None:
        self.assertEqual(self.data["arms"], [stage40.PLAN])
        for group in self.data["summary"].values():
            self.assertEqual(group["arms"], [stage40.PLAN])

    def test_the_graph_executor_really_ran(self) -> None:
        executed = sum(
            summary[stage40.PLAN]["executed_by_graph"]
            for summary in self.data["summary"].values()
        )
        self.assertGreaterEqual(executed, 5)

    def test_the_selector_rate_is_scored_where_a_case_names_one(self) -> None:
        scored = sum(
            summary[stage40.PLAN]["selector_scored_attempts"]
            for summary in self.data["summary"].values()
        )
        self.assertGreaterEqual(scored, 6)

    def test_the_cli_self_check_is_free_and_succeeds(self) -> None:
        self.assertEqual(stage48.main(["--self-check"]), 0)


# --- scoring behaviour ------------------------------------------------------


class ScoringTests(unittest.TestCase):

    def setUp(self) -> None:
        self.service = _service()

    def _record(self, case_id: str, plan: dict):
        from cad_experimental.local_plan_provider import LocalPlanProvider

        return stage48.run_plan_attempt(
            LocalPlanProvider(plan=plan), self.service,
            corpus.case(case_id), 1,
        )

    def test_a_correct_semantic_selector_builds_and_scores_ok(self) -> None:
        """Stage 40's runner would have scored this SEMANTICALLY_INCORRECT:
        ``plan_to_document`` raises ``SelectorNotExpressible`` for it."""
        item = corpus.case("F1-drilled-plate-round-corners")
        record = self._record(item.identifier, dict(item.reference_plan))
        self.assertEqual(record.category_code, stage40.OK)
        self.assertTrue(record.executed_by_graph)
        self.assertTrue(record.semantically_correct)
        self.assertTrue(record.selector_correct)

    def test_that_very_plan_is_refused_by_the_v1_document_path(self) -> None:
        from cad_experimental.adapter import plan_to_document
        from cad_experimental.parser import parse_plan

        item = corpus.case("F1-drilled-plate-round-corners")
        plan = parse_plan(dict(item.reference_plan))
        with self.assertRaises(stage48.SelectorNotExpressible):
            plan_to_document(plan)

    def test_the_wrong_rim_end_is_caught_although_the_volume_matches(
        self,
    ) -> None:
        """The reason F2 and F3 both exist. Same volume, different part."""
        top = corpus.case("F2-hole-rim-chamfer-top")
        bottom = corpus.case("F3-hole-rim-chamfer-bottom")
        self.assertEqual(
            top.geometry.volume_mm3, bottom.geometry.volume_mm3
        )
        record = self._record(top.identifier, dict(bottom.reference_plan))
        self.assertEqual(record.category_code, stage48.WRONG_SELECTOR)
        self.assertFalse(record.semantically_correct)
        self.assertFalse(record.selector_correct)
        self.assertIn("position", record.detail or "")

    def test_a_missing_required_operation_is_caught(self) -> None:
        """Four hand-placed holes build the bolt circle and are not it."""
        item = corpus.case("E1-bolt-circle-radial")
        hand_written = {
            "status": "generated",
            "summary": "four holes written out",
            "operations": [
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
                {"id": "bore", "type": "through_hole", "target": "plate",
                 "parameters": {"diameter": 20.0,
                                "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            ] + [
                {"id": f"m{index}", "type": "through_hole", "target": "plate",
                 "parameters": {"diameter": 6.0,
                                "position": {"x": x, "y": y, "z": 0.0}}}
                for index, (x, y) in enumerate(
                    ((70.0, 30.0), (50.0, 50.0), (30.0, 30.0), (50.0, 10.0))
                )
            ],
        }
        record = self._record(item.identifier, hand_written)
        self.assertEqual(record.category_code, stage40.SEMANTICALLY_INCORRECT)
        self.assertIn("pattern", record.detail or "")

    def test_a_wrong_volume_is_caught_even_with_the_right_operations(
        self,
    ) -> None:
        wrong = {
            "status": "generated",
            "summary": "a plate of the wrong thickness",
            "operations": [
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 12.0}},
            ],
        }
        record = self._record("01-plate-worded", wrong)
        self.assertEqual(record.category_code, stage40.SEMANTICALLY_INCORRECT)
        self.assertIn("volume", record.detail or "")

    def test_inventing_a_missing_value_is_its_own_failure(self) -> None:
        invented = {
            "status": "generated",
            "summary": "a plate someone chose the size of",
            "operations": [
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            ],
        }
        record = self._record("I1-underspecified-plate", invented)
        self.assertEqual(
            record.category_code, stage48.INVENTED_MISSING_VALUE
        )
        self.assertNotEqual(record.category_code, stage40.WRONGLY_ANSWERED)

    def test_asking_where_asking_was_right_scores_correct(self) -> None:
        asked = {
            "status": "needs_clarification",
            "summary": "how big?",
            "operations": [],
            "questions": ["What are the plate's dimensions?"],
        }
        record = self._record("I1-underspecified-plate", asked)
        self.assertTrue(record.semantically_correct)
        self.assertEqual(
            record.category_code, stage48.CORRECT_CLARIFICATION
        )

    def test_a_correct_question_is_not_labelled_as_a_built_part(
        self,
    ) -> None:
        """``OK`` means "built the part that was asked for". A question is a
        success and is not that, so it gets its own code."""
        self.assertNotEqual(stage48.CORRECT_CLARIFICATION, stage40.OK)
        self.assertNotEqual(
            stage48.CORRECT_CLARIFICATION, stage40.CORRECT_UNSUPPORTED
        )
        self.assertIn(
            stage48.CORRECT_CLARIFICATION, stage48.STAGE48_SUCCESS_CATEGORIES
        )
        self.assertNotIn(
            stage48.CORRECT_CLARIFICATION, stage40.SUCCESS_CATEGORIES
        )

    def test_refusing_where_asking_was_right_does_not_score(self) -> None:
        refused = {
            "status": "unsupported",
            "summary": "no",
            "operations": [],
            "reason": "cannot be expressed",
        }
        record = self._record("I1-underspecified-plate", refused)
        self.assertFalse(record.semantically_correct)
        self.assertEqual(record.category_code, stage40.WRONGLY_REFUSED)

    def test_an_impossible_part_accepts_either_refusal_word(self) -> None:
        for word in ("unsupported", "needs_clarification"):
            refusal = {
                "status": word, "summary": "no such part", "operations": [],
                "reason": "the hole is wider than the stock",
                "questions": ["did you mean a smaller hole?"],
            }
            record = self._record("I3-hole-wider-than-plate", refusal)
            self.assertTrue(record.semantically_correct, word)

    def test_answering_an_impossible_part_with_a_plan_is_wrong(self) -> None:
        confident = {
            "status": "generated",
            "summary": "a plate with an enormous hole",
            "operations": [
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
                {"id": "bore", "type": "through_hole", "target": "plate",
                 "parameters": {"diameter": 200.0,
                                "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            ],
        }
        record = self._record("I3-hole-wider-than-plate", confident)
        self.assertFalse(record.semantically_correct)
        self.assertEqual(record.category_code, stage40.WRONGLY_ANSWERED)

    def test_a_profile_chain_is_correct_when_the_engine_refuses_it(
        self,
    ) -> None:
        """The Stage 44 finding, scored as Stage 44 said it should be."""
        profile = {
            "status": "generated",
            "summary": "a profile extruded",
            "operations": [
                {"id": "outline", "type": "sketch",
                 "parameters": {
                     "plane": "XY",
                     "geometry": [
                         {"id": "r", "type": "rectangle",
                          "corner": {"x": 0.0, "y": 0.0},
                          "width": 80.0, "height": 40.0},
                     ],
                 }},
                {"id": "body", "type": "extrude", "target": "outline",
                 "parameters": {"distance": 12.0}},
                {"id": "bore", "type": "through_hole", "target": "body",
                 "parameters": {"diameter": 10.0,
                                "position": {"x": 40.0, "y": 20.0, "z": 0.0}}},
            ],
        }
        record = self._record("D2-profile-extrude-hole", profile)
        self.assertEqual(
            record.category_code, stage40.CORRECT_VALID_UNEXECUTABLE
        )
        self.assertTrue(record.semantically_correct)

    def test_a_box_instead_of_a_profile_chain_is_not_correct(self) -> None:
        box_instead = {
            "status": "generated",
            "summary": "a box, which is the same solid and not the request",
            "operations": [
                {"id": "body", "type": "box",
                 "parameters": {"x": 80.0, "y": 40.0, "z": 12.0}},
                {"id": "bore", "type": "through_hole", "target": "body",
                 "parameters": {"diameter": 10.0,
                                "position": {"x": 40.0, "y": 20.0, "z": 0.0}}},
            ],
        }
        record = self._record("D2-profile-extrude-hole", box_instead)
        self.assertFalse(record.semantically_correct)
        self.assertEqual(record.category_code, stage40.SEMANTICALLY_INCORRECT)

    def test_no_rate_is_reached_by_a_computed_attribute_name(self) -> None:
        """Stage 40's rule: every flag is read by an explicit accessor."""
        self.assertEqual(
            set(stage48._STAGE_FLAGS),
            {"model_output_valid", "structure_valid", "cad_valid",
             "build_success", "render_success", "semantically_correct"},
        )
        with self.assertRaises(KeyError):
            stage48._rate([], "anything_else")


# --- result metadata is complete -------------------------------------------


class ResultMetadataTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = stage48.self_check()

    def test_the_result_names_its_stage_kind_and_version(self) -> None:
        self.assertEqual(self.data["stage"], 48)
        self.assertEqual(self.data["evaluation_version"],
                         stage48.STAGE48_VERSION)

    def test_the_result_carries_every_fingerprint(self) -> None:
        prints = self.data["fingerprints"]
        self.assertEqual(prints["corpus"], corpus.corpus_fingerprint())
        self.assertEqual(prints["scoring"], stage48.scoring_fingerprint())
        self.assertEqual(prints["model"]["model"], stage40.MODEL)
        self.assertIn("fingerprint", prints["prompt"][stage40.PLAN])

    def test_the_result_records_the_schema_choice(self) -> None:
        self.assertEqual(self.data["plan_schema_choice"], "provider")
        self.assertEqual(
            self.data["fingerprints"]["model"]["plan_schema_choice"],
            "provider",
        )

    def test_the_result_carries_the_preflight_it_passed(self) -> None:
        self.assertTrue(self.data["preflight"]["baselines"]["intact"])
        self.assertTrue(self.data["preflight"]["groups"]["correct"])

    def test_every_record_carries_its_group_and_category(self) -> None:
        for record in self.data["records"]:
            self.assertIn(record["group"], corpus.GROUPS)
            self.assertIn(record["category"], corpus.CATEGORIES)
            self.assertIn("executed_by_graph", record)
            self.assertIn("selector_correct", record)

    def test_the_result_is_json_serialisable(self) -> None:
        text = json.dumps(self.data, sort_keys=True)
        self.assertEqual(json.loads(text)["stage"], 48)

    def test_no_record_carries_a_kernel_object(self) -> None:
        """The standing rule: a shape never crosses a transport boundary."""
        text = json.dumps(self.data, sort_keys=True)
        for forbidden in ("Workplane", "TopoDS", "cadquery", "OCP."):
            self.assertNotIn(forbidden, text)

    def test_the_result_states_what_it_may_not_be_compared_with(
        self,
    ) -> None:
        self.assertIn(
            "NOT a re-run", self.data["relationship_to_stage_43"]
        )


# --- the baseline digest is line-ending independent, and nothing else -------


class BaselineDigestTests(unittest.TestCase):
    """The protection must survive a CRLF checkout and nothing weaker.

    ``core.autocrlf=true`` is the default on a Windows checkout and is set on
    this project's development machine, so git rewrites LF to CRLF on the way
    out. Hashing the working-tree bytes compared committed content against
    translated content and reported six intact baselines as modified. These
    tests pin both halves of the fix: the translation is tolerated, and
    everything else still fails.
    """

    CONTENT = b'{"run": 1, "note": "a baseline"}\nsecond line\nthird line\n'

    def digest(self, data: bytes) -> str:
        return stage48.committed_content_digest(data)

    def test_the_same_content_hashes_the_same_with_either_newline(
        self,
    ) -> None:
        crlf = self.CONTENT.replace(b"\n", b"\r\n")
        self.assertNotEqual(self.CONTENT, crlf)
        self.assertEqual(self.digest(self.CONTENT), self.digest(crlf))

    def test_changing_one_byte_changes_the_digest(self) -> None:
        modified = self.CONTENT.replace(b'"run": 1', b'"run": 2')
        self.assertNotEqual(self.digest(self.CONTENT), self.digest(modified))

    def test_adding_a_line_changes_the_digest(self) -> None:
        self.assertNotEqual(
            self.digest(self.CONTENT), self.digest(self.CONTENT + b"extra\n")
        )

    def test_removing_a_line_changes_the_digest(self) -> None:
        shorter = self.CONTENT.replace(b"second line\n", b"")
        self.assertNotEqual(self.digest(self.CONTENT), self.digest(shorter))

    def test_a_modification_is_caught_even_when_newlines_also_change(
        self,
    ) -> None:
        """Re-ending a file must not smuggle an edit past the check."""
        tampered = self.CONTENT.replace(b'"run": 1', b'"run": 999')
        self.assertNotEqual(
            self.digest(self.CONTENT),
            self.digest(tampered.replace(b"\n", b"\r\n")),
        )

    def test_the_digest_is_the_lf_form_so_it_matches_the_commit(self) -> None:
        import hashlib

        self.assertEqual(
            self.digest(self.CONTENT.replace(b"\n", b"\r\n")),
            hashlib.sha256(self.CONTENT).hexdigest(),
        )


class BaselineCheckTests(unittest.TestCase):
    """The same three outcomes, through the check the run actually calls."""

    def setUp(self) -> None:
        self.root = tempfile.mkdtemp()
        self.relative = next(iter(sorted(stage48.BASELINE_DIGESTS)))
        self.path = os.path.join(self.root, self.relative)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.content = b"a protected baseline\nwith two lines\n"
        self.digests = {
            self.relative: stage48.committed_content_digest(self.content)
        }

    def write(self, data: bytes) -> None:
        with open(self.path, "wb") as handle:
            handle.write(data)

    def check(self):
        original = stage48.BASELINE_DIGESTS
        try:
            stage48.BASELINE_DIGESTS = self.digests  # type: ignore[assignment]
            return stage48._baseline_check(repository_root=self.root)
        finally:
            stage48.BASELINE_DIGESTS = original  # type: ignore[assignment]

    def test_an_lf_checkout_is_accepted(self) -> None:
        self.write(self.content)
        result = self.check()
        self.assertTrue(result["intact"], result["files"])
        self.assertFalse(result["files"][self.relative]["crlf_on_disk"])

    def test_a_crlf_checkout_of_unchanged_content_is_accepted(self) -> None:
        """The false positive that blocked Stage 48. It must not return."""
        self.write(self.content.replace(b"\n", b"\r\n"))
        result = self.check()
        self.assertTrue(result["intact"], result["files"])
        self.assertTrue(result["files"][self.relative]["crlf_on_disk"])

    def test_a_modified_baseline_is_rejected(self) -> None:
        self.write(self.content.replace(b"two", b"ten"))
        result = self.check()
        self.assertFalse(result["intact"])
        self.assertFalse(result["files"][self.relative]["matches"])

    def test_a_modified_baseline_is_rejected_in_crlf_form_too(self) -> None:
        self.write(self.content.replace(b"two", b"ten").replace(b"\n", b"\r\n"))
        result = self.check()
        self.assertFalse(result["intact"])

    def test_a_missing_baseline_is_rejected(self) -> None:
        result = self.check()
        self.assertFalse(result["intact"])
        self.assertFalse(result["files"][self.relative]["present"])
        self.assertFalse(result["files"][self.relative]["matches"])

    def test_an_empty_file_is_rejected(self) -> None:
        self.write(b"")
        result = self.check()
        self.assertFalse(result["intact"])


class RealBaselinesStayProtectedTests(unittest.TestCase):
    """The committed Stage 40 and Stage 43 records, as they are on disk."""

    def test_all_seven_committed_baselines_are_intact(self) -> None:
        check = stage48._baseline_check()
        self.assertTrue(check["intact"], check["files"])
        self.assertEqual(len(check["files"]), 7)

    def test_both_protected_directories_are_covered(self) -> None:
        for directory in stage48.PROTECTED_BASELINE_DIRECTORIES:
            covered = [
                name for name in stage48.BASELINE_DIGESTS if directory in name
            ]
            self.assertTrue(covered, f"{directory} has no protected file")

    def test_stage_40_keeps_five_files_and_stage_43_two(self) -> None:
        def count(directory: str) -> int:
            return sum(
                1 for name in stage48.BASELINE_DIGESTS if directory in name
            )

        self.assertEqual(count("stage40-v1-vs-operation-plan"), 5)
        self.assertEqual(count("stage43-structured-output"), 2)

    def test_a_changed_digest_still_stops_a_run(self) -> None:
        """The guard is still armed after the hashing change."""
        original = dict(stage48.BASELINE_DIGESTS)
        try:
            stage48.BASELINE_DIGESTS = dict(  # type: ignore[assignment]
                original,
                **{
                    "docs/evaluation-baselines/stage43-structured-output/"
                    "README.md": "0" * 64
                },
            )
            with self.assertRaises(stage48.BaselineMissing):
                stage48.run(live=False, model_factory=lambda: object())
        finally:
            stage48.BASELINE_DIGESTS = original  # type: ignore[assignment]


# --- the diagnostic probe keeps evidence, and keeps secrets out ------------


class RedactionTests(unittest.TestCase):
    """Provider text is kept for diagnosis, but never a credential."""

    def test_an_anthropic_key_is_removed(self) -> None:
        text = "BadRequestError: auth sk-ant-api03-AAAABBBBCCCCDDDD failed"
        out = stage48.redact(text)
        self.assertNotIn("sk-ant-api03", out)
        self.assertIn("[redacted-api-key]", out)

    def test_an_authorization_header_is_removed(self) -> None:
        out = stage48.redact("x-api-key: abcd1234efgh5678 rejected")
        self.assertNotIn("abcd1234efgh5678", out)

    def test_a_bearer_token_is_removed(self) -> None:
        out = stage48.redact("Authorization: Bearer abcdefghijklmnop")
        self.assertNotIn("abcdefghijklmnop", out)

    def test_a_long_opaque_token_is_removed(self) -> None:
        token = "A" * 60
        self.assertNotIn(token, stage48.redact(f"trace {token} end"))

    def test_the_useful_part_of_the_message_survives(self) -> None:
        out = stage48.redact(
            "BadRequestError: schema is too complex to compile"
        )
        self.assertIn("too complex", out)
        self.assertIn("BadRequestError", out)

    def test_long_text_is_truncated(self) -> None:
        out = stage48.redact("word " * 2000)
        self.assertLessEqual(
            len(out), stage48.MAX_DETAIL_CHARACTERS + 32
        )


class DiagnosticShapeTests(unittest.TestCase):
    """The five calls vary one thing at a time, and only five are made."""

    def test_exactly_five_probes_are_defined(self) -> None:
        self.assertEqual(len(stage48.DIAGNOSTIC_PROBES), 5)

    def test_the_control_is_v1_and_the_rest_are_the_plan(self) -> None:
        representations = [p[0] for p in stage48.DIAGNOSTIC_PROBES]
        self.assertEqual(representations[0], stage48.V1)
        self.assertTrue(
            all(r == stage48.PLAN for r in representations[1:])
        )

    def test_both_plan_schemas_are_tried_on_both_shapes(self) -> None:
        pairs = {
            (choice, shape)
            for rep, choice, _, shape in stage48.DIAGNOSTIC_PROBES
            if rep == stage48.PLAN
        }
        self.assertEqual(
            pairs,
            {("provider", "box"), ("provider", "profile"),
             ("compact", "box"), ("compact", "profile")},
        )

    def test_the_error_class_is_read_from_the_detail(self) -> None:
        from cad_ai.provider import ProviderError, ProviderErrorKind

        error = ProviderError(
            "the interpretation service is unavailable",
            detail="BadRequestError: schema too complex",
            kind=ProviderErrorKind.INVALID_REQUEST,
        )
        self.assertEqual(stage48._error_class(error), "BadRequestError")


class DiagnosisReadingTests(unittest.TestCase):
    """The stated rule, pinned, so a result cannot be read into a wish."""

    def build(self, provider_ok: bool, compact_ok: bool,
              control_ok: bool = True, split_shapes: bool = False):
        probes = [{
            "representation": stage48.V1, "schema_choice": "executable",
            "request_shape": "box", "accepted": control_ok,
            "error_class": None if control_ok else "BadRequestError",
        }]
        for choice, ok in (("provider", provider_ok), ("compact", compact_ok)):
            for index, shape in enumerate(("box", "profile")):
                accepted = ok
                if split_shapes and choice == "provider":
                    accepted = index == 0
                probes.append({
                    "representation": stage48.PLAN, "schema_choice": choice,
                    "request_shape": shape, "accepted": accepted,
                    "error_class": None if accepted else "BadRequestError",
                })
        return stage48.diagnose_probe({"probes": probes})

    def test_provider_fails_and_compact_passes_supports_grammar_size(
        self,
    ) -> None:
        reading = self.build(provider_ok=False, compact_ok=True)
        self.assertEqual(
            reading["verdict"], "grammar_size_strongly_supported"
        )

    def test_both_failing_does_not_establish_grammar_size(self) -> None:
        reading = self.build(provider_ok=False, compact_ok=False)
        self.assertEqual(
            reading["verdict"], "grammar_size_not_established"
        )
        self.assertIn("NOT established", reading["conclusion"])

    def test_a_failing_control_blocks_every_schema_conclusion(self) -> None:
        reading = self.build(
            provider_ok=False, compact_ok=False, control_ok=False
        )
        self.assertEqual(reading["verdict"], "control_failed")

    def test_one_shape_failing_points_at_request_construction(self) -> None:
        reading = self.build(
            provider_ok=False, compact_ok=True, split_shapes=True
        )
        self.assertEqual(reading["verdict"], "shape_dependent")

    def test_everything_accepted_reproduces_no_rejection(self) -> None:
        reading = self.build(provider_ok=True, compact_ok=True)
        self.assertEqual(reading["verdict"], "no_rejection_reproduced")

    def test_a_stated_cause_outranks_the_shape_of_the_results(self) -> None:
        """Measured: both schemas refused, and the provider said why.

        The shape rule alone would call this 'not established'. The provider
        named the cause in every refusal, which is stronger evidence than an
        inference from which calls failed, so the stated cause wins.
        """
        probes = [
            {"representation": stage48.V1, "schema_choice": "executable",
             "request_shape": "box", "accepted": True},
        ]
        for choice in ("provider", "compact"):
            for shape in ("box", "profile"):
                probes.append({
                    "representation": stage48.PLAN,
                    "schema_choice": choice, "request_shape": shape,
                    "accepted": False, "error_class": "BadRequestError",
                    "detail": (
                        "BadRequestError: Error code: 400 - The compiled "
                        "grammar is too large, which would cause "
                        "performance issues."
                    ),
                })
        reading = stage48.diagnose_probe({"probes": probes})
        self.assertEqual(
            reading["verdict"], "grammar_size_stated_by_provider"
        )
        self.assertTrue(reading["cause_stated_by_provider"])

    def test_without_a_stated_cause_the_shape_rule_still_governs(
        self,
    ) -> None:
        """No message, both refused -- the honest answer stays 'unknown'."""
        reading = self.build(provider_ok=False, compact_ok=False)
        self.assertEqual(reading["verdict"], "grammar_size_not_established")
        self.assertFalse(reading["cause_stated_by_provider"])

    def test_a_stated_cause_does_not_override_a_failed_control(self) -> None:
        probes = [
            {"representation": stage48.V1, "schema_choice": "executable",
             "request_shape": "box", "accepted": False,
             "error_class": "BadRequestError", "detail": "x"},
            {"representation": stage48.PLAN, "schema_choice": "provider",
             "request_shape": "box", "accepted": False,
             "error_class": "BadRequestError",
             "detail": "The compiled grammar is too large"},
        ]
        reading = stage48.diagnose_probe({"probes": probes})
        self.assertEqual(reading["verdict"], "control_failed")


class DiagnosticIsNotABaselineTests(unittest.TestCase):
    """A diagnostic must never be mistaken for, or written over, a result."""

    def test_it_refuses_to_write_into_a_protected_baseline(self) -> None:
        for path in (
            "docs/evaluation-baselines/stage40-v1-vs-operation-plan/x.json",
            "docs/evaluation-baselines/stage43-structured-output/x.json",
        ):
            self.assertTrue(stage48.writes_into_a_baseline(path), path)

    def test_its_kind_is_distinct_from_every_result_kind(self) -> None:
        self.assertNotEqual(
            "stage48-provider-diagnostic-probe", stage48.RESULT_KIND
        )

    def test_the_cli_refuses_five_calls_without_the_flag(self) -> None:
        self.assertEqual(stage48.main([]), 2)


if __name__ == "__main__":
    unittest.main()
