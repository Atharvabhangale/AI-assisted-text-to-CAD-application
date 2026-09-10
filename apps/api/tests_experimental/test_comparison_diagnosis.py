"""Stage 40: the failure diagnosis. Tested so its numbers can be trusted.

The diagnosis is a counterfactual -- what each representation would have
scored under a fence-tolerant transport -- and it is only worth reading if it
uses the *same* frozen machinery as the scored run and adds exactly one piece
of leniency. These tests hold it to that.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from cad_experimental import comparison_diagnosis as cd
from cad_experimental import representation_comparison as rc
from cad_experimental.comparison_corpus import case

SOURCE = pathlib.Path(cd.__file__).resolve().parent


def fenced(payload):
    return "```json\n" + json.dumps(payload) + "\n```"


V1_OK = {
    "status": "document", "summary": "a plate",
    "document": {
        "schema_version": "1.0.0", "units": "mm", "name": "plate",
        "features": [{"id": "plate", "type": "box",
                      "size": {"x": 100, "y": 60, "z": 10}}],
    },
}
PLAN_OK = {
    "status": "generated", "summary": "a plate",
    "operations": [{"id": "plate", "type": "box",
                    "parameters": {"x": 100, "y": 60, "z": 10}}],
}


def record(representation, case_id, raw, attempt=1):
    return {
        "representation": representation, "case_id": case_id,
        "attempt": attempt, "raw_text": raw,
    }


class ExtractionTests(unittest.TestCase):
    def test_a_fenced_object_is_found(self):
        self.assertEqual(cd.extract_json(fenced({"a": 1})), {"a": 1})

    def test_a_bare_object_is_found(self):
        self.assertEqual(cd.extract_json('{"a": 1}'), {"a": 1})

    def test_a_fence_with_trailing_prose_is_found(self):
        text = fenced({"a": 1}) + "\n\n**Summary:** it is a thing."
        self.assertEqual(cd.extract_json(text), {"a": 1})

    def test_prose_only_yields_nothing(self):
        self.assertIsNone(cd.extract_json("I need to clarify one thing."))

    def test_malformed_json_is_not_repaired(self):
        self.assertIsNone(cd.extract_json("```json\n{oops\n```"))
        self.assertIsNone(cd.extract_json("{not json}"))

    def test_empty_input_yields_nothing(self):
        self.assertIsNone(cd.extract_json(None))
        self.assertIsNone(cd.extract_json(""))

    def test_the_shape_classifier_names_what_was_seen(self):
        self.assertEqual(cd.shape_of(fenced({"a": 1})), "fenced json")
        self.assertEqual(
            cd.shape_of(fenced({"a": 1}) + "\nAnd here is why."),
            "fenced json + prose",
        )
        self.assertEqual(cd.shape_of('{"a": 1}'), "bare json")
        self.assertEqual(cd.shape_of("I cannot do that."), "prose only")
        self.assertEqual(cd.shape_of(""), "empty")


class DiagnosisTests(unittest.TestCase):
    def test_a_correct_fenced_answer_diagnoses_as_correct_on_both_arms(self):
        data = cd.diagnose([
            record(rc.V1, "01-plate-worded", fenced(V1_OK)),
            record(rc.PLAN, "01-plate-worded", fenced(PLAN_OK)),
        ])
        for representation in (rc.V1, rc.PLAN):
            with self.subTest(representation=representation):
                self.assertEqual(data["summary"][representation]["correct"], 1)
                self.assertEqual(data["summary"][representation]["rate"], 1.0)

    def test_prose_only_is_named_as_such(self):
        data = cd.diagnose([
            record(rc.V1, "01-plate-worded", "I need to clarify the units."),
        ])
        self.assertEqual(
            data["summary"][rc.V1]["verdicts"], {"no JSON block at all": 1}
        )

    def test_a_missing_v1_envelope_is_named_as_such(self):
        """The single largest V1 failure in the live run."""
        data = cd.diagnose([
            record(rc.V1, "01-plate-worded", fenced(V1_OK["document"])),
        ])
        self.assertIn(
            "JSON, but no `status` envelope", data["summary"][rc.V1]["verdicts"]
        )

    def test_a_wrong_dimension_is_not_diagnosed_as_correct(self):
        wrong = json.loads(json.dumps(V1_OK))
        wrong["document"]["features"][0]["size"]["z"] = 20
        data = cd.diagnose([record(rc.V1, "01-plate-worded", fenced(wrong))])
        self.assertEqual(data["summary"][rc.V1]["correct"], 0)

    def test_a_swapped_dimension_is_not_diagnosed_as_correct(self):
        """Same volume, wrong part. The diagnosis uses the frozen check."""
        swapped = json.loads(json.dumps(V1_OK))
        swapped["document"]["features"][0]["size"] = {"x": 60, "y": 100, "z": 10}
        data = cd.diagnose([record(rc.V1, "01-plate-worded", fenced(swapped))])
        self.assertEqual(data["summary"][rc.V1]["correct"], 0)

    def test_a_correct_refusal_diagnoses_as_correct(self):
        data = cd.diagnose([
            record(rc.V1, "11-sphere", fenced({"status": "unsupported"})),
            record(rc.PLAN, "11-sphere", fenced({
                "status": "unsupported", "summary": "no sphere",
                "reason": "no sphere", "operations": []})),
        ])
        for representation in (rc.V1, rc.PLAN):
            self.assertEqual(data["summary"][representation]["correct"], 1)

    def test_refusing_a_buildable_case_diagnoses_as_wrongly_refused(self):
        data = cd.diagnose([
            record(rc.PLAN, "01-plate-worded", fenced({
                "status": "unsupported", "summary": "no", "reason": "no",
                "operations": []})),
        ])
        self.assertIn("wrongly refused", data["summary"][rc.PLAN]["verdicts"])

    def test_an_explicit_null_reason_is_still_a_parse_rejection(self):
        """The plan arm's real failure on case 9, reproduced.

        The frozen parser refuses an explicit null where a string belongs.
        The diagnosis must not repair that -- it adds fence tolerance and
        nothing else.
        """
        payload = dict(PLAN_OK, reason=None)
        data = cd.diagnose([record(rc.PLAN, "01-plate-worded", fenced(payload))])
        verdicts = data["summary"][rc.PLAN]["verdicts"]
        self.assertTrue(
            any("parser rejected" in key for key in verdicts), verdicts
        )
        self.assertEqual(data["summary"][rc.PLAN]["correct"], 0)

    def test_the_per_case_table_covers_every_case(self):
        from cad_experimental.comparison_corpus import CASE_IDS

        data = cd.diagnose([record(rc.V1, "01-plate-worded", fenced(V1_OK))])
        self.assertEqual(set(data["per_case"]), set(CASE_IDS))

    def test_the_report_says_it_is_not_the_measured_result(self):
        data = cd.diagnose([record(rc.V1, "01-plate-worded", fenced(V1_OK))])
        text = cd.format_diagnosis(data)
        self.assertIn("NOT the measured result", text)
        self.assertIn("counterfactual", text)
        self.assertIn("DIAGNOSTIC ONLY", data["note"])

    def test_the_result_is_json_serialisable(self):
        data = cd.diagnose([record(rc.V1, "01-plate-worded", fenced(V1_OK))])
        self.assertIsInstance(json.dumps(data, sort_keys=True), str)


class DisciplineTests(unittest.TestCase):
    """The diagnosis must reuse the frozen machinery, not reimplement it."""

    def test_it_uses_the_scored_run_s_geometry_check(self):
        source = (SOURCE / "comparison_diagnosis.py").read_text()
        self.assertIn("_geometry_matches", source)
        self.assertEqual(source.count("def _geometry_matches"), 0)

    def test_it_uses_the_frozen_parser_and_validator(self):
        source = (SOURCE / "comparison_diagnosis.py").read_text()
        for name in ("parse_plan", "validate_plan", "plan_to_document"):
            self.assertIn(name, source)
        self.assertEqual(source.count("def parse_plan"), 0)
        self.assertEqual(source.count("def validate_plan"), 0)

    def test_it_defines_no_expectation_of_its_own(self):
        """Expectations live in the corpus, which the diagnosis only reads."""
        source = (SOURCE / "comparison_diagnosis.py").read_text()
        self.assertNotIn("volume_mm3 =", source)
        self.assertNotIn("VOLUME_RTOL =", source)

    def test_it_adds_exactly_one_piece_of_leniency(self):
        """A fence, and nothing else. No key renaming, no defaulting."""
        source = (SOURCE / "comparison_diagnosis.py").read_text()
        for forbidden in ("setdefault", ".replace(", "lower()"):
            self.assertNotIn(forbidden, source)

    def test_it_makes_no_model_call(self):
        source = (SOURCE / "comparison_diagnosis.py").read_text()
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("anthropic", imported)
        self.assertNotIn("generation", imported)

    def test_nothing_executes_model_output(self):
        tree = ast.parse((SOURCE / "comparison_diagnosis.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.assertNotIn(
                    getattr(node.func, "id", None),
                    ("eval", "exec", "compile", "__import__"),
                )


if __name__ == "__main__":
    unittest.main()
