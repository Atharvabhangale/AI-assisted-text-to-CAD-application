"""Stage 40: the comparison harness, verified offline before it spends money.

Nothing here calls a model. A stub answers both arms with canned output, so
that what is under test is the *instrument*: that both representations are
driven through the same ladder, that a correct answer is recorded as correct,
that each kind of failure lands in the right category, and that neither arm
gets an advantage the other does not.

The fairness properties are asserted, not assumed -- equal attempts, an equal
token ceiling, no retry on either side, and provider errors kept out of every
quality denominator.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib
import unittest

from cad_ai.provider import ModelResponse, ProviderError, ProviderErrorKind

from cad_experimental import representation_comparison as rc
from cad_experimental.comparison_corpus import (
    CASES,
    CASE_IDS,
    EXPECT_BUILD,
    EXPECT_UNSUPPORTED,
    EXPECT_VALID_UNEXECUTABLE,
    case,
    corpus_fingerprint,
)

SOURCE = pathlib.Path(rc.__file__).resolve().parent


# --- canned answers --------------------------------------------------------


def v1_document(*features, name="part"):
    """A bare canonical V1 document."""
    return {
        "schema_version": "1.0.0", "units": "mm", "name": name,
        "features": list(features),
    }


def v1_reply(*features, name="part"):
    """The response ENVELOPE the production V1 prompt asks the model for.

    Measured from `cad_ai.specification.response_schema()`, whose top level is
    `{status, document, summary, issues, questions}` -- a bare document is not
    a valid reply, and the production parser is right to refuse one.

    Note the status word: V1 says **"document"**, the operation plan says
    "generated". The two frozen envelopes genuinely differ here, which is one
    of the things this comparison measures rather than smooths over.
    """
    return {
        "status": "document",
        "summary": "a part",
        "document": v1_document(*features, name=name),
    }


V1_PLATE = v1_reply({
    "id": "plate", "type": "box", "size": {"x": 100, "y": 60, "z": 10},
})
V1_UNSUPPORTED = {
    "status": "unsupported", "summary": "no sphere",
    "issues": ["a sphere is not in the V1 feature set"],
}

PLAN_PLATE = {
    "status": "generated", "summary": "a plate",
    "operations": [{"id": "plate", "type": "box",
                    "parameters": {"x": 100, "y": 60, "z": 10}}],
}
PLAN_UNSUPPORTED = {
    "status": "unsupported", "summary": "no sphere", "reason": "no sphere",
    "operations": [],
}
PLAN_SKETCH_EXTRUDE = {
    "status": "generated", "summary": "a profile, extruded",
    "operations": [
        {"id": "profile", "type": "sketch", "parameters": {
            "plane": "XY",
            "geometry": [{"id": "r1", "type": "rectangle",
                          "corner": {"x": 0, "y": 0},
                          "width": 100, "height": 60}]}},
        {"id": "body", "type": "extrude", "target": "profile",
         "parameters": {"distance": 10}},
    ],
}


class StubModel:
    """Answers whichever representation the request's schema asks for.

    Records every request it sees, so a test can assert what the two arms
    actually sent -- which is the only way to check they were treated alike.
    """

    name = "stub"

    def __init__(self, v1_payload, plan_payload, *, raise_error=False):
        self.v1_payload = v1_payload
        self.plan_payload = plan_payload
        self.raise_error = raise_error
        self.requests = []

    @property
    def config(self):
        return None

    def generate(self, request):
        self.requests.append(request)
        if self.raise_error:
            raise ProviderError(
                "unavailable", detail="stub", kind=ProviderErrorKind.RATE_LIMITED
            )
        # Told apart by the SYSTEM PROMPT, not the schema: structured output
        # is disabled for both arms, so `output_schema` is None on both and a
        # schema-based discriminator would silently answer every call as V1.
        from cad_experimental.prompt import system_prompt as plan_prompt

        is_plan = request.system == plan_prompt()
        payload = self.plan_payload if is_plan else self.v1_payload
        return ModelResponse(
            text=json.dumps(payload), provider="stub",
            model=rc.MODEL, structured_output=True, stop_reason="end_turn",
            usage={"input_tokens": 100, "output_tokens": 50},
        )


def run_offline(v1_payload, plan_payload, *, cases=None, attempts=1,
                raise_error=False):
    stub = StubModel(v1_payload, plan_payload, raise_error=raise_error)
    data = rc.run(
        live=False, attempts=attempts,
        cases=cases if cases is not None else [case("01-plate-worded")],
        model_factory=lambda: stub,
    )
    return data, stub


def records_of(data, representation):
    return [r for r in data["records"] if r["representation"] == representation]


# --- 1. the corpus is a frozen instrument ---------------------------------


class CorpusTests(unittest.TestCase):
    def test_there_are_thirteen_cases(self):
        self.assertEqual(len(CASES), 13)

    def test_every_case_id_is_unique(self):
        self.assertEqual(len(set(CASE_IDS)), len(CASE_IDS))

    def test_the_fingerprint_is_stable(self):
        self.assertEqual(corpus_fingerprint(), corpus_fingerprint())

    def test_the_fingerprint_covers_the_expectations(self):
        """An edit to an expectation must change the fingerprint, so that
        editing the corpus after a run is detectable."""
        before = corpus_fingerprint()
        original = CASES[0].geometry.volume_mm3
        object.__setattr__(CASES[0].geometry, "volume_mm3", original + 1.0)
        try:
            self.assertNotEqual(corpus_fingerprint(), before)
        finally:
            object.__setattr__(CASES[0].geometry, "volume_mm3", original)
        self.assertEqual(corpus_fingerprint(), before)

    def test_every_expectation_is_one_of_the_three(self):
        for item in CASES:
            with self.subTest(case=item.identifier):
                self.assertIn(
                    item.expect_v1,
                    (EXPECT_BUILD, EXPECT_UNSUPPORTED,
                     EXPECT_VALID_UNEXECUTABLE),
                )
                self.assertIn(
                    item.expect_plan,
                    (EXPECT_BUILD, EXPECT_UNSUPPORTED,
                     EXPECT_VALID_UNEXECUTABLE),
                )

    def test_v1_is_never_expected_to_produce_an_unexecutable_answer(self):
        """Only the plan language can express something it cannot build."""
        for item in CASES:
            self.assertNotEqual(item.expect_v1, EXPECT_VALID_UNEXECUTABLE)

    def test_a_buildable_case_always_has_expected_geometry(self):
        for item in CASES:
            with self.subTest(case=item.identifier):
                if EXPECT_BUILD in (item.expect_v1, item.expect_plan):
                    self.assertIsNotNone(item.geometry)

    def test_a_refusal_case_has_no_expected_geometry(self):
        for item in CASES:
            if item.expect_v1 == item.expect_plan == EXPECT_UNSUPPORTED:
                with self.subTest(case=item.identifier):
                    self.assertIsNone(item.geometry)

    def test_the_expectations_differ_only_on_the_sketch_cases(self):
        differing = {
            item.identifier for item in CASES
            if item.expect_v1 != item.expect_plan
        }
        self.assertEqual(
            differing, {"09-profile-extrude", "10-profile-revolve"}
        )

    def test_the_closed_forms_are_computed_not_transcribed(self):
        """Each expected volume must equal its own formula."""
        plate = 100.0 * 60.0 * 10.0
        self.assertAlmostEqual(
            case("01-plate-worded").geometry.volume_mm3, plate, places=9
        )
        self.assertAlmostEqual(
            case("02-cylinder-axis").geometry.volume_mm3,
            math.pi * 100.0 * 50.0, places=9,
        )
        self.assertAlmostEqual(
            case("04-plate-centre-hole").geometry.volume_mm3,
            plate - math.pi * 100.0 * 10.0, places=9,
        )
        self.assertAlmostEqual(
            case("06-cube-bore").geometry.volume_mm3,
            50.0**3 - math.pi * 100.0 * 50.0, places=9,
        )
        self.assertAlmostEqual(
            case("07-plate-chamfer").geometry.volume_mm3,
            plate - 4.0 * 10.0 * 2.0, places=9,
        )
        self.assertAlmostEqual(
            case("08-plate-fillet").geometry.volume_mm3,
            plate - 4.0 * 10.0 * 4.0 * (1.0 - math.pi / 4.0), places=9,
        )

    def test_the_two_plate_cases_expect_the_same_solid(self):
        """Cases 1 and 3 are the same part, worded differently."""
        self.assertEqual(
            case("01-plate-worded").geometry.to_dict(),
            case("03-box-dimensional").geometry.to_dict(),
        )

    def test_case_six_pins_no_operation_type(self):
        """Drilling and subtracting are both that part."""
        self.assertEqual(case("06-cube-bore").required_v1_features, ())
        self.assertEqual(case("06-cube-bore").required_plan_operations, ())


# --- 2. both arms actually run --------------------------------------------


class BothArmsRunTests(unittest.TestCase):
    def test_a_correct_answer_scores_correct_on_both_arms(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertTrue(record["model_output_valid"])
                self.assertTrue(record["structure_valid"])
                self.assertTrue(record["cad_valid"])
                self.assertTrue(record["build_success"])
                self.assertTrue(record["render_success"])
                self.assertTrue(record["semantically_correct"])
                self.assertEqual(record["category"], rc.OK)

    def test_both_arms_reach_real_geometry(self):
        """Not a mock: the kernel built it and reported a volume."""
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertAlmostEqual(
                    record["volume_mm3"], 60000.0, places=6
                )
                self.assertEqual(record["solid_count"], 1)
                self.assertEqual(record["bounding_box"]["x"], 100.0)
                self.assertGreater(record["triangle_count"], 0)

    def test_both_arms_produce_identical_geometry_for_the_same_part(self):
        """The plan route and the V1 route are the same code below the
        adapter, and this is the check that says so on live data."""
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        v1 = records_of(data, rc.V1)[0]
        plan = records_of(data, rc.PLAN)[0]
        self.assertTrue(math.isclose(
            v1["volume_mm3"], plan["volume_mm3"], rel_tol=1e-12
        ))
        self.assertEqual(v1["bounding_box"], plan["bounding_box"])
        self.assertEqual(v1["solid_count"], plan["solid_count"])

    def test_every_case_runs_on_both_arms(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE, cases=list(CASES))
        for item in CASES:
            for representation in rc.REPRESENTATIONS:
                mine = [
                    r for r in data["records"]
                    if r["case_id"] == item.identifier
                    and r["representation"] == representation
                ]
                with self.subTest(case=item.identifier,
                                  representation=representation):
                    self.assertEqual(len(mine), 1)

    def test_latency_is_recorded_for_both(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertIsNotNone(record["latency_seconds"])
                self.assertGreaterEqual(record["latency_seconds"], 0.0)

    def test_raw_output_is_recorded_for_both(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertTrue(record["raw_text"])

    def test_usage_is_recorded_for_both(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            self.assertEqual(record["usage"]["input_tokens"], 100)


# --- 3. fairness ----------------------------------------------------------


class FairnessTests(unittest.TestCase):
    def test_both_arms_get_the_same_number_of_attempts(self):
        data, _ = run_offline(
            V1_PLATE, PLAN_PLATE, cases=list(CASES), attempts=3
        )
        counts = {
            representation: len(records_of(data, representation))
            for representation in rc.REPRESENTATIONS
        }
        self.assertEqual(counts[rc.V1], counts[rc.PLAN])
        self.assertEqual(counts[rc.V1], 3 * len(CASES))

    def test_both_arms_get_the_same_output_token_ceiling(self):
        """The one asymmetry that existed, equalised in one visible place."""
        _, stub = run_offline(V1_PLATE, PLAN_PLATE)
        ceilings = {r.max_output_tokens for r in stub.requests}
        self.assertEqual(ceilings, {rc.SHARED_MAX_OUTPUT_TOKENS})
        self.assertEqual(len(stub.requests), 2)

    def test_the_two_services_would_otherwise_have_differed(self):
        """Proof the equalisation was necessary, not decorative."""
        from cad_ai.generation import MAX_OUTPUT_TOKENS as V1_MAX
        from cad_experimental.config import MAX_OUTPUT_TOKENS as PLAN_MAX

        self.assertNotEqual(V1_MAX, PLAN_MAX)
        self.assertEqual(rc.SHARED_MAX_OUTPUT_TOKENS, max(V1_MAX, PLAN_MAX))

    def test_each_arm_receives_its_own_prompt_and_schema_unmodified(self):
        from cad_ai.prompt import system_prompt as v1_prompt
        from cad_experimental.prompt import system_prompt as plan_prompt

        _, stub = run_offline(V1_PLATE, PLAN_PLATE)
        systems = {r.system for r in stub.requests}
        self.assertIn(v1_prompt(), systems)
        self.assertIn(plan_prompt(), systems)

    def test_the_user_text_is_sent_verbatim_on_both_arms(self):
        item = case("04-plate-centre-hole")
        _, stub = run_offline(V1_PLATE, PLAN_PLATE, cases=[item])
        for request in stub.requests:
            self.assertEqual(request.user_text, item.text)

    def test_one_model_call_per_attempt_per_arm_and_no_retry(self):
        _, stub = run_offline(V1_PLATE, PLAN_PLATE, cases=list(CASES))
        self.assertEqual(len(stub.requests), 2 * len(CASES))

    def test_a_provider_error_is_not_retried_on_either_arm(self):
        _, stub = run_offline(
            V1_PLATE, PLAN_PLATE, cases=list(CASES), raise_error=True
        )
        self.assertEqual(len(stub.requests), 2 * len(CASES))

    def test_a_provider_error_stays_out_of_every_quality_denominator(self):
        data, _ = run_offline(
            V1_PLATE, PLAN_PLATE, cases=list(CASES), raise_error=True
        )
        for representation in rc.REPRESENTATIONS:
            summary = data["summary"][representation]
            with self.subTest(representation=representation):
                self.assertEqual(summary["answered"], 0)
                self.assertEqual(summary["provider_errors"], len(CASES))
                # No answers means no rate at all -- not a rate of zero.
                self.assertIsNone(summary["semantically_correct"])
                self.assertIsNone(summary["build_success"])

    def test_a_provider_error_is_categorised_as_the_provider_s(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE, raise_error=True)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            self.assertEqual(record["category"], rc.PROVIDER_ERROR)
            self.assertFalse(record["model_output_valid"])

    def test_the_scoring_function_is_shared_by_both_arms(self):
        """One implementation, so the two arms cannot be scored differently."""
        source = (SOURCE / "representation_comparison.py").read_text()
        tree = ast.parse(source)
        callers = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and getattr(inner.func, "id", None) == "_score_geometry"):
                    callers.add(node.name)
        self.assertEqual(callers, {"run_v1_attempt", "run_plan_attempt"})

    def test_the_geometry_check_is_shared_by_both_arms(self):
        source = (SOURCE / "representation_comparison.py").read_text()
        self.assertEqual(source.count("def _geometry_matches("), 1)
        self.assertEqual(source.count("def _build_and_measure("), 1)


# --- 4. failure taxonomy --------------------------------------------------


class TaxonomyTests(unittest.TestCase):
    def test_unparseable_output_is_a_parser_rejection_on_both_arms(self):
        class Garbage(StubModel):
            def generate(self, request):
                self.requests.append(request)
                return ModelResponse(
                    text="Here is your part! ```json {oops", provider="stub",
                    model=rc.MODEL, structured_output=True,
                )

        stub = Garbage(None, None)
        data = rc.run(
            live=False, attempts=1, cases=[case("01-plate-worded")],
            model_factory=lambda: stub,
        )
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertTrue(record["model_output_valid"])
                self.assertFalse(record["structure_valid"])
                self.assertEqual(record["category"], rc.PARSER_REJECTED)

    def test_a_plan_that_breaks_a_rule_is_a_plan_validation_rejection(self):
        forward = {
            "status": "generated", "summary": "a forward reference",
            "operations": [
                {"id": "hole", "type": "through_hole", "target": "plate",
                 "parameters": {"diameter": 8,
                                "position": {"x": 10, "y": 10, "z": 0}}},
                {"id": "plate", "type": "box",
                 "parameters": {"x": 100, "y": 60, "z": 10}},
            ],
        }
        data, _ = run_offline(V1_PLATE, forward)
        record = records_of(data, rc.PLAN)[0]
        self.assertTrue(record["structure_valid"])
        self.assertFalse(record["cad_valid"])
        self.assertEqual(record["category"], rc.PLAN_VALIDATION_REJECTED)
        self.assertIn("P10", record["detail"])

    def test_an_invalid_document_is_a_v1_validation_rejection(self):
        two_solids = v1_reply(
            {"id": "a", "type": "box", "size": {"x": 10, "y": 10, "z": 10}},
            {"id": "b", "type": "box", "size": {"x": 10, "y": 10, "z": 10}},
        )
        two_plan = {
            "status": "generated", "summary": "two solids",
            "operations": [
                {"id": "a", "type": "box",
                 "parameters": {"x": 10, "y": 10, "z": 10}},
                {"id": "b", "type": "box",
                 "parameters": {"x": 10, "y": 10, "z": 10}},
            ],
        }
        data, _ = run_offline(two_solids, two_plan)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertEqual(
                    record["category"], rc.V1_VALIDATION_REJECTED
                )
                self.assertFalse(record["build_success"])

    def test_right_shape_wrong_size_is_semantically_incorrect(self):
        wrong = v1_reply({
            "id": "plate", "type": "box",
            "size": {"x": 100, "y": 60, "z": 20},
        })
        wrong_plan = {
            "status": "generated", "summary": "too thick",
            "operations": [{"id": "plate", "type": "box",
                            "parameters": {"x": 100, "y": 60, "z": 20}}],
        }
        data, _ = run_offline(wrong, wrong_plan)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertTrue(record["build_success"])
                self.assertTrue(record["render_success"])
                self.assertFalse(record["semantically_correct"])
                self.assertEqual(record["category"], rc.SEMANTICALLY_INCORRECT)

    def test_a_swapped_dimension_is_caught_even_at_equal_volume(self):
        """The A6 failure mode: valid, buildable, same volume, still wrong."""
        swapped = v1_reply({
            "id": "plate", "type": "box",
            "size": {"x": 60, "y": 100, "z": 10},
        })
        swapped_plan = {
            "status": "generated", "summary": "x and y swapped",
            "operations": [{"id": "plate", "type": "box",
                            "parameters": {"x": 60, "y": 100, "z": 10}}],
        }
        data, _ = run_offline(swapped, swapped_plan)
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertAlmostEqual(record["volume_mm3"], 60000.0, places=6)
                self.assertFalse(record["semantically_correct"])
                self.assertIn("bounding box", record["detail"])

    def test_a_correct_refusal_is_a_success_on_both_arms(self):
        data, _ = run_offline(
            V1_UNSUPPORTED, PLAN_UNSUPPORTED, cases=[case("11-sphere")]
        )
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertTrue(record["semantically_correct"])
                self.assertEqual(record["category"], rc.CORRECT_UNSUPPORTED)
                self.assertIn(record["category"], rc.SUCCESS_CATEGORIES)

    def test_refusing_a_buildable_request_is_a_failure_on_both_arms(self):
        data, _ = run_offline(
            V1_UNSUPPORTED, PLAN_UNSUPPORTED, cases=[case("01-plate-worded")]
        )
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertFalse(record["semantically_correct"])
                self.assertEqual(record["category"], rc.WRONGLY_REFUSED)

    def test_answering_an_unsupported_request_is_a_failure_on_both_arms(self):
        data, _ = run_offline(
            V1_PLATE, PLAN_PLATE, cases=[case("11-sphere")]
        )
        for representation in rc.REPRESENTATIONS:
            record = records_of(data, representation)[0]
            with self.subTest(representation=representation):
                self.assertFalse(record["semantically_correct"])
                self.assertEqual(record["category"], rc.WRONGLY_ANSWERED)

    def test_every_category_is_declared(self):
        source = (SOURCE / "representation_comparison.py").read_text()
        for name in (
            rc.OK, rc.CORRECT_UNSUPPORTED, rc.CORRECT_VALID_UNEXECUTABLE,
            rc.MODEL_OUTPUT_INVALID, rc.PARSER_REJECTED,
            rc.PLAN_VALIDATION_REJECTED, rc.V1_VALIDATION_REJECTED,
            rc.BUILD_FAILED, rc.RENDERMODEL_FAILED,
            rc.SEMANTICALLY_INCORRECT, rc.PROVIDER_ERROR,
        ):
            self.assertIn(f'"{name}"', source)


# --- 5. the sketch cases, where the right answers differ ------------------


class SketchCaseTests(unittest.TestCase):
    def test_a_valid_unexecutable_plan_is_correct_for_the_plan_arm(self):
        data, _ = run_offline(
            V1_UNSUPPORTED, PLAN_SKETCH_EXTRUDE,
            cases=[case("09-profile-extrude")],
        )
        record = records_of(data, rc.PLAN)[0]
        self.assertTrue(record["semantically_correct"])
        self.assertEqual(record["category"], rc.CORRECT_VALID_UNEXECUTABLE)
        self.assertFalse(record["build_success"])
        self.assertEqual(
            tuple(record["operations"]), ("sketch", "extrude")
        )

    def test_refusing_is_correct_for_the_v1_arm_on_the_same_case(self):
        data, _ = run_offline(
            V1_UNSUPPORTED, PLAN_SKETCH_EXTRUDE,
            cases=[case("09-profile-extrude")],
        )
        record = records_of(data, rc.V1)[0]
        self.assertTrue(record["semantically_correct"])
        self.assertEqual(record["category"], rc.CORRECT_UNSUPPORTED)

    def test_a_box_instead_of_a_profile_is_incorrect_for_the_plan_arm(self):
        """Buildable, the right solid, and not what was asked for."""
        data, _ = run_offline(
            V1_UNSUPPORTED, PLAN_PLATE, cases=[case("09-profile-extrude")]
        )
        record = records_of(data, rc.PLAN)[0]
        self.assertFalse(record["semantically_correct"])
        self.assertEqual(record["category"], rc.SEMANTICALLY_INCORRECT)
        self.assertIn("sketch", record["detail"])

    def test_a_sketch_without_the_extrude_is_incorrect(self):
        sketch_only = {
            "status": "generated", "summary": "just a profile",
            "operations": [PLAN_SKETCH_EXTRUDE["operations"][0]],
        }
        data, _ = run_offline(
            V1_UNSUPPORTED, sketch_only, cases=[case("09-profile-extrude")]
        )
        record = records_of(data, rc.PLAN)[0]
        self.assertFalse(record["semantically_correct"])
        self.assertIn("extrude", record["detail"])

    def test_unsupported_rate_is_scored_per_representation(self):
        """V1 has five refusal cases, the plan has three: the sketch cases
        are a refusal for one arm and an answer for the other."""
        v1_refusals = {
            c.identifier for c in CASES if c.expect_v1 == EXPECT_UNSUPPORTED
        }
        plan_refusals = {
            c.identifier for c in CASES if c.expect_plan == EXPECT_UNSUPPORTED
        }
        self.assertEqual(len(v1_refusals), 5)
        self.assertEqual(len(plan_refusals), 3)
        self.assertTrue(plan_refusals < v1_refusals)


# --- 6. reporting ---------------------------------------------------------


class ReportTests(unittest.TestCase):
    def test_the_report_names_both_representations_and_every_metric(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE, cases=list(CASES))
        text = rc.format_report(data)
        for expected in (
            "V1 JSON", "Operation Plan", "Model output valid",
            "Parse/structure valid", "CAD validation valid", "Build success",
            "Semantic correctness", "RenderModel success",
            "Correct unsupported", "Prompt characters", "Mean latency",
        ):
            self.assertIn(expected, text)

    def test_the_report_records_the_frozen_instrument(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE)
        text = rc.format_report(data)
        self.assertIn(rc.MODEL, text)
        self.assertIn(corpus_fingerprint()[:16], text)

    def test_the_frozen_state_pins_both_prompt_fingerprints(self):
        from cad_ai.prompt import prompt_fingerprint as v1_fp
        from cad_experimental.prompt import prompt_fingerprint as plan_fp

        frozen = rc.frozen_state()
        self.assertEqual(frozen[rc.V1]["prompt_fingerprint"], v1_fp())
        self.assertEqual(frozen[rc.PLAN]["prompt_fingerprint"], plan_fp())

    def test_prompt_sizes_are_recorded_for_both(self):
        sizes = rc.prompt_sizes()
        self.assertGreater(sizes[rc.V1], 0)
        self.assertGreater(sizes[rc.PLAN], 0)

    def test_the_result_is_json_serialisable(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE, cases=list(CASES))
        self.assertIsInstance(json.dumps(data, sort_keys=True), str)


# --- 7. safety ------------------------------------------------------------


class SafetyTests(unittest.TestCase):
    def test_a_live_run_needs_an_explicit_flag(self):
        """A present credential must never by itself begin a paid run."""
        with self.assertRaises(rc.CredentialUnavailable):
            rc.run(live=False, attempts=1)

    def test_the_cli_refuses_without_live(self):
        self.assertEqual(rc.main([]), 2)

    def test_no_credential_value_is_ever_recorded(self):
        data, _ = run_offline(V1_PLATE, PLAN_PLATE, cases=list(CASES))
        text = json.dumps(data)
        self.assertNotIn("sk-ant", text)
        self.assertNotIn(rc.OPERATOR_KEY_VARIABLE + "=", text)

    def test_the_module_never_prints_or_returns_the_key(self):
        """`bridge_credential` returns None and nothing else reads the value."""
        source = (SOURCE / "representation_comparison.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            reads_key = any(
                isinstance(inner, ast.Constant)
                and inner.value == rc.OPERATOR_KEY_VARIABLE
                for inner in ast.walk(node)
            )
            if reads_key:
                self.assertIn(
                    node.name, ("credential_present", "bridge_credential"),
                    f"{node.name} reads the credential variable",
                )

    def test_credential_presence_is_a_boolean_not_a_value(self):
        self.assertIsInstance(rc.credential_present(), bool)

    def test_nothing_executes_model_output(self):
        source = (SOURCE / "representation_comparison.py").read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None)
                self.assertNotIn(
                    name, ("eval", "exec", "compile", "__import__")
                )

    def test_no_other_provider_can_be_substituted(self):
        """Gemini is never reachable from this harness."""
        source = (SOURCE / "representation_comparison.py").read_text()
        self.assertNotIn("gemini", source.lower())

    def test_the_model_is_pinned_not_read_from_the_environment(self):
        source = (SOURCE / "representation_comparison.py").read_text()
        self.assertIn(f'MODEL = "{rc.MODEL}"', source)
        self.assertEqual(rc.MODEL, "claude-haiku-4-5-20251001")



# --- 8. schema compatibility, measured against the API's own rules --------


class SchemaCompatibilityTests(unittest.TestCase):
    """Why structured output is off for both arms.

    The numbers here are the reason, and they are computed from the frozen
    schemas rather than transcribed, so they cannot drift from the claim.
    """

    def test_the_plan_schema_exceeds_the_api_optional_property_limit(self):
        from cad_experimental.plan import plan_schema

        count = len(rc.optional_properties(plan_schema()))
        self.assertGreater(count, rc.OPTIONAL_PROPERTY_LIMIT)

    def test_the_v1_schema_is_within_the_limit(self):
        from cad_ai.specification import response_schema

        count = len(rc.optional_properties(response_schema()))
        self.assertLessEqual(count, rc.OPTIONAL_PROPERTY_LIMIT)

    def test_the_cause_is_the_flat_parameters_object(self):
        """All nine operation types share one optional-keyed parameters bag."""
        from cad_experimental.plan import OPERATION_TYPES, plan_schema

        parameters = (
            plan_schema()["properties"]["operations"]["items"]
            ["properties"]["parameters"]
        )
        self.assertGreaterEqual(
            len(parameters["properties"]), len(OPERATION_TYPES)
        )
        self.assertFalse(parameters["additionalProperties"])

    def test_sanitising_does_not_rescue_the_plan_schema(self):
        """Stripping bounds cannot reduce the optional-property count."""
        from cad_experimental.plan import plan_schema

        before = len(rc.optional_properties(plan_schema()))
        after = len(rc.optional_properties(rc.sanitise_schema(plan_schema())))
        self.assertEqual(before, after)
        self.assertGreater(after, rc.OPTIONAL_PROPERTY_LIMIT)

    def test_sanitising_removes_only_bounds(self):
        """Never a type, an enum, a required list or additionalProperties."""
        schema = {
            "type": "object",
            "properties": {"n": {"type": "number", "exclusiveMinimum": 0,
                                 "maximum": 10}},
            "required": ["n"],
            "additionalProperties": False,
            "items": {"type": "array", "minItems": 2, "maxItems": 9},
        }
        out = rc.sanitise_schema(schema)
        self.assertEqual(out["type"], "object")
        self.assertEqual(out["required"], ["n"])
        self.assertFalse(out["additionalProperties"])
        self.assertEqual(out["properties"]["n"], {"type": "number"})
        self.assertEqual(out["items"], {"type": "array", "minItems": 1})

    def test_structured_output_is_off_for_both_arms_or_neither(self):
        """One switch, so it cannot be on for one representation only."""
        source = (SOURCE / "representation_comparison.py").read_text()
        self.assertEqual(source.count("STRUCTURED_OUTPUT_ENABLED ="), 1)
        self.assertFalse(rc.STRUCTURED_OUTPUT_ENABLED)

    def test_no_schema_is_sent_while_structured_output_is_off(self):
        _, stub = run_offline(V1_PLATE, PLAN_PLATE)
        for request in stub.requests:
            self.assertIsNone(request.output_schema)

    def test_the_frozen_state_records_the_reason(self):
        frozen = rc.frozen_state()
        self.assertFalse(frozen["structured_output_enabled"])
        self.assertEqual(
            frozen["optional_property_limit"], rc.OPTIONAL_PROPERTY_LIMIT
        )
        self.assertGreater(
            frozen["optional_properties"][rc.PLAN],
            frozen["optional_properties"][rc.V1],
        )

if __name__ == "__main__":
    unittest.main()
