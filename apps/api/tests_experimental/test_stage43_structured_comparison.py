"""Stage 43: the structured-output comparison, verified offline.

Nothing here calls a model. What is under test is that this stage is the
Stage 40 comparison with **one** difference -- the API constrains the output
format -- and that it borrows every judgement rather than making its own.

The properties asserted are the ones that would silently invalidate a result
if they broke: that the frozen instrument is untouched, that both arms are
constrained by their own schema, that no fence stripping or repair was
introduced on either side, and that a Stage 43 result can never be mistaken
for the Stage 40 baseline.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from cad_ai.provider import ModelRequest, ModelResponse

from cad_experimental import representation_comparison as rc
from cad_experimental import stage43_structured_comparison as s43

SOURCE = pathlib.Path(s43.__file__).resolve()
FROZEN_SOURCE = pathlib.Path(rc.__file__).resolve()


class RecordingModel:
    """An inner model that records the request it was handed."""

    name = "recording"

    def __init__(self) -> None:
        self.requests = []

    @property
    def config(self):  # pragma: no cover - never read in these tests
        return None

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            text="{}", provider="recording", model="recording",
            structured_output=True,
        )


class SeparationFromStage40Tests(unittest.TestCase):
    """Stage 43 must not disturb the instrument it is compared against."""

    def test_the_frozen_stage_40_flag_is_still_false(self) -> None:
        """The baseline stays reproducible only if its own state is intact."""
        self.assertFalse(rc.STRUCTURED_OUTPUT_ENABLED)

    def test_this_stage_turns_structured_output_on(self) -> None:
        self.assertTrue(s43.STRUCTURED_OUTPUT_ENABLED)

    def test_the_result_is_labelled_as_its_own_stage(self) -> None:
        state = s43.stage43_state()
        self.assertEqual(state["stage"], 43)
        self.assertEqual(state["kind"], s43.RESULT_KIND)
        self.assertTrue(state["structured_output_enabled"])

    def test_the_stage_40_state_is_embedded_unedited(self) -> None:
        """Provenance: the baseline's own record travels with the result."""
        state = s43.stage43_state()
        self.assertEqual(
            state["stage_40_frozen_state"]["structured_output_enabled"], False
        )
        self.assertEqual(
            state["stage_40_frozen_state"], rc.frozen_state()
        )

    def test_the_model_is_the_same_pinned_model(self) -> None:
        self.assertEqual(s43.stage43_state()["stage_40_frozen_state"]["model"],
                         rc.MODEL)


class SameInstrumentTests(unittest.TestCase):
    """Everything but the transport is borrowed, not re-implemented."""

    def test_the_cases_are_the_frozen_corpus(self) -> None:
        self.assertIs(s43.CASES, rc.CASES)

    def test_the_token_ceiling_is_the_shared_one(self) -> None:
        self.assertEqual(
            s43.SHARED_MAX_OUTPUT_TOKENS, rc.SHARED_MAX_OUTPUT_TOKENS
        )

    def test_it_defines_no_scoring_of_its_own(self) -> None:
        """No category, threshold or tolerance may originate here."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        assigned = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for forbidden in ("VOLUME_RTOL", "BOUNDING_BOX_ATOL",
                          "SUCCESS_CATEGORIES", "EXPECT_BUILD"):
            self.assertNotIn(forbidden, assigned)

    def test_it_calls_the_frozen_run(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("from .representation_comparison import", source)
        self.assertIn("run(", source)


class NoRepairTests(unittest.TestCase):
    """The measurement decision both arms share: nothing is repaired."""

    #: Reading whether a fence arrived is the measurement this stage exists
    #: to make. Removing one would be repair. Only the first is allowed.
    DETECTION_ONLY = ("startswith", "endswith", "count", "find")
    REPAIRING = ("replace", "strip", "lstrip", "rstrip", "split",
                 "removeprefix", "removesuffix", "partition")

    def test_a_fence_is_only_ever_detected_never_removed(self) -> None:
        """The fence is a metric here, not something to be undone.

        Stage 40's whole result was decided by fences, so this module must be
        able to *report* one. It must never take one off: doing so would
        repair the model's output and make the two stages incomparable.
        """
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            mentions_fence = any(
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and "```" in argument.value
                for argument in node.args
            )
            if mentions_fence:
                self.assertNotIn(
                    node.func.attr, self.REPAIRING,
                    f"{node.func.attr}() on a fence literal is repair, "
                    "not measurement",
                )
                self.assertIn(
                    node.func.attr, self.DETECTION_ONLY,
                    f"unexpected fence operation {node.func.attr}()",
                )

    def test_no_repairing_operation_is_called(self) -> None:
        """No stripping, substitution or retry on either arm."""
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                called.add(node.attr)
        for forbidden in ("removeprefix", "removesuffix", "sub", "findall"):
            self.assertNotIn(
                forbidden, called,
                f"{forbidden!r} suggests model output is being rewritten",
            )

    def test_the_frozen_parsers_are_untouched(self) -> None:
        """Stage 43 must not have loosened either parser to get a score."""
        frozen = FROZEN_SOURCE.read_text(encoding="utf-8")
        self.assertIn("sanitise_schema", frozen)
        self.assertFalse(rc.STRUCTURED_OUTPUT_ENABLED)


class SchemaTests(unittest.TestCase):
    """Both arms must actually be compilable, and measurably so."""

    def test_both_arms_pass_the_offline_preflight(self) -> None:
        data = s43.preflight()
        self.assertTrue(data["both_arms_compilable_offline"], data)

    def test_neither_schema_uses_a_rejected_keyword(self) -> None:
        for arm in s43.ARMS:
            facts = s43.schema_facts(s43.schema_for(arm))
            self.assertEqual(facts["rejected_keywords_present"], [], arm)

    def test_both_schemas_are_within_the_document_limit(self) -> None:
        for arm in s43.ARMS:
            facts = s43.schema_facts(s43.schema_for(arm))
            self.assertLessEqual(
                facts["optional_properties"], rc.OPTIONAL_PROPERTY_LIMIT, arm
            )

    def test_no_single_object_exceeds_the_per_object_limit(self) -> None:
        for arm in s43.ARMS:
            facts = s43.schema_facts(s43.schema_for(arm))
            self.assertLessEqual(
                facts["worst_object_optional_properties"],
                s43.PER_OBJECT_OPTIONAL_LIMIT,
                arm,
            )

    def test_sanitising_removes_no_property_from_either_arm(self) -> None:
        """Bounds may be dropped for the compiler; shape may not."""
        from cad_ai.specification import response_schema

        from cad_experimental.plan import provider_schema

        def names(schema, out=None):
            out = [] if out is None else out
            if isinstance(schema, dict):
                properties = schema.get("properties")
                if isinstance(properties, dict):
                    out.extend(properties)
                for value in schema.values():
                    names(value, out)
            elif isinstance(schema, list):
                for value in schema:
                    names(value, out)
            return out

        for raw in (response_schema(), provider_schema()):
            self.assertEqual(
                sorted(names(raw)),
                sorted(names(rc.sanitise_schema(raw))),
            )


class StructuredModelTests(unittest.TestCase):
    """The schema must reach the provider, on both arms, every time."""

    def test_each_arm_is_given_its_own_schema(self) -> None:
        inner = RecordingModel()
        model = s43.StructuredModel(inner)
        prompts = s43._system_prompts()
        for arm in s43.ARMS:
            model.generate(
                ModelRequest(system=prompts[arm], user_text="x",
                             output_schema=None, max_output_tokens=100)
            )
        self.assertEqual(len(inner.requests), 2)
        for request, arm in zip(inner.requests, s43.ARMS):
            self.assertIsNotNone(request.output_schema)
            self.assertEqual(request.output_schema, s43.schema_for(arm))

    def test_both_arms_are_counted(self) -> None:
        inner = RecordingModel()
        model = s43.StructuredModel(inner)
        prompts = s43._system_prompts()
        for arm in s43.ARMS:
            model.generate(
                ModelRequest(system=prompts[arm], user_text="x",
                             output_schema=None, max_output_tokens=100)
            )
        self.assertEqual(model.attached, {arm: 1 for arm in s43.ARMS})

    def test_an_unknown_prompt_raises_rather_than_guesses(self) -> None:
        """Attaching the wrong grammar would corrupt a measurement."""
        model = s43.StructuredModel(RecordingModel())
        with self.assertRaises(ValueError):
            model.generate(
                ModelRequest(system="not either prompt", user_text="x",
                             output_schema=None, max_output_tokens=100)
            )

    def test_it_changes_nothing_but_the_schema(self) -> None:
        inner = RecordingModel()
        model = s43.StructuredModel(inner)
        prompts = s43._system_prompts()
        model.generate(
            ModelRequest(system=prompts[s43.V1], user_text="the text",
                         output_schema=None, max_output_tokens=321)
        )
        sent = inner.requests[0]
        self.assertEqual(sent.system, prompts[s43.V1])
        self.assertEqual(sent.user_text, "the text")
        self.assertEqual(sent.max_output_tokens, 321)


class CliTests(unittest.TestCase):
    """A paid run is never begun by accident."""

    def test_without_live_it_refuses_and_spends_nothing(self) -> None:
        self.assertEqual(s43.main([]), 2)

    def test_check_is_free_and_succeeds(self) -> None:
        self.assertEqual(s43.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
