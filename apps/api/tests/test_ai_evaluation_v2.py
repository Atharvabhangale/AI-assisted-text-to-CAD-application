"""Tests for benchmark corpus 2.0.0 and its geometric scoring.

**No test here calls a model.** Model answers come from a scripted stub; the
solids are built and measured by the real engine -- in the isolated worker
for the instrument check and the command, in process (a test-only scorer)
for the harness tests, which keeps them fast.

What these tests are for:

* **corpus 1.0.0 is untouched** -- its data is pinned by a hash -- and 2.0.0
  sits beside it as a deliberate re-baseline;
* **the instrument detects what it claims**: every reference scores 100 %
  against itself, every correct-but-different answer passes, and every
  deliberately wrong answer (swapped axes, a wrong diameter, a missing hole,
  inches not converted, ...) builds and FAILS;
* **rules are data**: one interpreter, no per-case code, malformed rules
  refused at load time;
* **reliability stays apart from quality**: a provider error or an
  instrument error is unmeasured, never a failure.
"""

from __future__ import annotations

import ast
import hashlib
import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_ai import corpus as corpus_v1
from cad_ai import corpus_v2
from cad_ai.evaluation import (
    CORPUS_VERSIONS,
    CorpusError,
    Evaluator,
    ExpectedOutcome,
    IsolatedShapeScorer,
    OracleStub,
    build_run,
    format_report,
    instrument_check,
    load_corpus_v2,
    load_corpus_version,
    main,
    save_run,
    summarise,
)
from cad_ai.features import box, cyl, union
from cad_ai.generation import GenerationOutcome, TextToCadService
from cad_ai.geometric_scoring import (
    INSTRUMENT_ERROR,
    NOT_BUILT,
    RuleError,
    judge,
    orientations_for,
    validate_rule,
)
from cad_ai.prompt import PROMPT_VERSION, prompt_fingerprint
from cad_ai.provider import ModelRequest, ModelResponse, ProviderError, ProviderErrorKind

from cad_core.application_service import CadApplicationService
from cad_core.shape_comparison import (
    CylindricalSurface,
    OverlapMeasure,
    PairFailure,
    PairResult,
    ShapeComparison,
    ShapeFacts,
    compare_pair,
)
from cad_core.validator import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
AI_SOURCE = REPO_ROOT / "apps" / "api" / "src" / "cad_ai"

#: The sha256 of corpus 1.0.0's raw data. It must never change: 1.0.0 is a
#: frozen instrument with recorded runs. A new corpus gets a new version.
CORPUS_V1_FINGERPRINT = "f0b86d76a5210b6a7b5c39ff34b18cc15e6955e1a01782ba3d7f050a2552ebb5"

GENERATION_MODULES = (
    "prompt.py", "provider.py", "anthropic_provider.py", "specification.py",
    "generation.py", "config.py",
)


class ScriptedModel:
    """Answers with a fixed payload per prompt."""

    name = "scripted"

    def __init__(self, answers: Mapping[str, Any], *, error: Optional[ProviderError] = None) -> None:
        self._answers = dict(answers)
        self._error = error
        self.requests: List[ModelRequest] = []

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        payload = self._answers.get(request.user_text, {"status": "unsupported", "issues": ["?"]})
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ModelResponse(text=text, provider=self.name, model="scripted-model",
                             structured_output=True, stop_reason="end_turn",
                             usage={"input_tokens": 10, "output_tokens": 5})


def in_process_scorer(pairs: Sequence[Mapping[str, Any]]) -> List[Any]:
    """Test-only: the same measurement as the worker, in this process."""
    return [compare_pair(p["candidate"], p.get("reference"), p.get("orientations", ["fixed"])) for p in pairs]


def answer(document: Mapping[str, Any]) -> Dict[str, Any]:
    return {"status": "document", "plan": "", "assumptions": [], "document": dict(document),
            "summary": "a part", "questions": [], "issues": []}


CASES = load_corpus_v2()
BY_ID = {case.case_id: case for case in CASES}


# --- the corpus as data -----------------------------------------------------------


class TestCorpusV2(unittest.TestCase):
    def test_corpus_1_0_0_is_untouched(self) -> None:
        self.assertEqual(corpus_v1.CORPUS_VERSION, "1.0.0")
        self.assertEqual(len(corpus_v1.raw_cases()), 35)
        digest = hashlib.sha256(
            json.dumps(corpus_v1.raw_cases(), sort_keys=True, default=list).encode()
        ).hexdigest()
        self.assertEqual(digest, CORPUS_V1_FINGERPRINT)

    def test_the_default_corpus_is_still_1_0_0(self) -> None:
        self.assertEqual(CORPUS_VERSIONS[0], "1.0.0")
        self.assertEqual(CORPUS_VERSIONS[1], "2.0.0")
        self.assertEqual(load_corpus_version("1.0.0")[0].corpus_version, "1.0.0")
        with self.assertRaises(CorpusError):
            load_corpus_version("9.9.9")

    def test_size_and_categories(self) -> None:
        self.assertEqual(corpus_v2.CORPUS_V2_VERSION, "2.0.0")
        self.assertTrue(100 <= len(CASES) <= 150, len(CASES))
        counts = {name: len(ids) for name, ids in corpus_v2.by_category_v2().items()}
        self.assertEqual(set(counts), set(corpus_v2.CATEGORIES_V2))
        for name, count in counts.items():
            self.assertGreaterEqual(count, 3, msg=name)
        self.assertEqual(len(set(case.case_id for case in CASES)), len(CASES))
        for case in CASES:
            self.assertEqual(case.corpus_version, "2.0.0")

    def test_every_reference_is_valid_1_2_0_without_through_hole(self) -> None:
        for entry in corpus_v2.raw_cases_v2():
            if "reference" not in entry:
                continue
            with self.subTest(case=entry["case_id"]):
                document = entry["reference"]
                self.assertEqual(document["schema_version"], "1.2.0")
                result = validate(dict(document))
                self.assertTrue(result.valid, result.rule_codes())
                kinds = {feature["type"] for feature in document["features"]}
                self.assertNotIn("through_hole", kinds)

    def test_every_generated_case_has_a_valid_rule_and_says_what_fixes_it(self) -> None:
        for case in CASES:
            with self.subTest(case=case.case_id):
                if case.expected_outcome is ExpectedOutcome.EXPECTED_GENERATED:
                    self.assertIsNotNone(case.scoring)
                    self.assertIsNotNone(case.reference_document)
                    self.assertIsNone(case.expected_document)
                    validate_rule(case.scoring or {}, case.case_id)
                    rule = case.scoring or {}
                    if rule["kind"] == "exact" and rule["orientation"] != "free":
                        self.assertTrue(case.fixes, "a fixed or upright rule names its words")
                else:
                    self.assertIsNone(case.scoring)
                    self.assertIsNone(case.reference_document)

    def test_the_policy_expectations(self) -> None:
        for case in CASES:
            if case.category == "adversarial":
                self.assertIs(case.expected_outcome, ExpectedOutcome.EXPECTED_BOUNDARY_HELD)
                self.assertEqual(set(case.accepted_outcomes),
                                 {GenerationOutcome.UNSUPPORTED, GenerationOutcome.GENERATED})
            elif case.category == "clarification":
                self.assertIs(case.expected_outcome, ExpectedOutcome.EXPECTED_NEEDS_CLARIFICATION)
            else:
                # The assume-and-approximate policy: everything else is a part.
                self.assertIs(case.expected_outcome, ExpectedOutcome.EXPECTED_GENERATED, case.case_id)

    def test_scoring_kinds_follow_the_category(self) -> None:
        for case in CASES:
            if case.scoring is None:
                continue
            expected = "properties" if case.category == "everyday" else "exact"
            self.assertEqual(case.scoring["kind"], expected, case.case_id)

    def test_perturbations_and_equivalents_name_geometric_cases(self) -> None:
        for kind, entries in (("perturbation", corpus_v2.PERTURBATIONS), ("equivalent", corpus_v2.EQUIVALENTS)):
            for entry in entries:
                with self.subTest(kind=kind, case=entry["case_id"], label=entry["kind"]):
                    case = BY_ID[entry["case_id"]]
                    self.assertIsNotNone(case.scoring)
                    self.assertTrue(validate(dict(entry["document"])).valid)
        kinds = {entry["kind"] for entry in corpus_v2.PERTURBATIONS}
        for required in ("swapped-axes", "wrong-hole-diameter", "missing-hole", "inches-not-converted"):
            self.assertIn(required, kinds)

    def test_no_model_output_was_copied_into_the_corpus(self) -> None:
        source = (AI_SOURCE / "corpus_v2.py").read_text(encoding="utf-8")
        for marker in ("stop_reason", "input_tokens", "claude-", "candidate_document", '"status"'):
            self.assertNotIn(marker, source, msg=marker)


class TestCorpusV2Loader(unittest.TestCase):
    def entry(self, **overrides: Any) -> Dict[str, Any]:
        base: Dict[str, Any] = dict(corpus_v2.raw_cases_v2()[0])
        base.update(overrides)
        return base

    def test_malformed_entries_are_refused(self) -> None:
        good = self.entry()
        bad_reference = dict(good["reference"])
        bad_reference["features"] = [dict(bad_reference["features"][0], size={"x": -1, "y": 1, "z": 1})]
        for label, entries in (
            ("duplicate", [good, good]),
            ("invalid reference", [self.entry(reference=bad_reference)]),
            ("no rule", [self.entry(scoring=None)]),
            ("unknown kind", [self.entry(scoring={"kind": "vibes", "orientation": "fixed"})]),
            ("unknown orientation", [self.entry(scoring={"kind": "exact", "orientation": "sideways"})]),
            ("unknown check", [self.entry(scoring={"kind": "properties", "orientation": "free",
                                                   "checks": [{"check": "colour"}]})]),
            ("unknown check key", [self.entry(scoring={"kind": "properties", "orientation": "free",
                                                       "checks": [{"check": "volume", "min": 1, "maximum": 2}]})]),
            ("empty properties", [self.entry(scoring={"kind": "properties", "orientation": "free", "checks": []})]),
            ("v1 document key", [self.entry(document=good["reference"])]),
            ("rule on a question", [dict(corpus_v2.CLARIFICATION[0], scoring=good["scoring"])]),
            ("unknown accepted outcome", [dict(corpus_v2.ADVERSARIAL[0], accepted_outcomes=("maybe",))]),
            ("impossible accepted outcome", [dict(corpus_v2.CLARIFICATION[0], accepted_outcomes=("generated",))]),
            ("empty", []),
        ):
            with self.subTest(label=label):
                with self.assertRaises(CorpusError):
                    load_corpus_v2(entries)

    def test_rule_validation_directly(self) -> None:
        validate_rule({"kind": "exact", "orientation": "upright_any"})
        for rule in (
            {"kind": "exact", "orientation": "fixed", "iou_min": 1.5},
            {"kind": "exact", "orientation": "fixed", "position": "somewhere"},
            {"kind": "exact", "orientation": "fixed", "volume_tolerance": -1},
            {"kind": "exact", "orientation": "fixed", "surprise": 1},
            {"kind": "properties", "orientation": "free",
             "checks": [{"check": "extent", "axis": "w", "min": 1, "max": 2}]},
            {"kind": "properties", "orientation": "free",
             "checks": [{"check": "volume", "min": 5, "max": 1}]},
            {"kind": "properties", "orientation": "free",
             "checks": [{"check": "bores", "min_count": 1, "axis": "diagonal"}]},
        ):
            with self.subTest(rule=rule):
                with self.assertRaises(RuleError):
                    validate_rule(rule)


# --- the rule interpreter on hand-made facts ---------------------------------------


def facts(
    extents: Tuple[float, float, float] = (100, 60, 10),
    *,
    volume: Optional[float] = None,
    minimum: Tuple[float, float, float] = (0, 0, 0),
    genus: Optional[int] = 0,
    cylinders: Sequence[CylindricalSurface] = (),
    face_types: Optional[Mapping[str, int]] = None,
    solids: int = 1,
) -> ShapeFacts:
    return ShapeFacts(
        solid_count=solids, shell_count=1,
        volume=volume if volume is not None else extents[0] * extents[1] * extents[2],
        area=1.0, minimum=minimum,
        maximum=tuple(m + e for m, e in zip(minimum, extents)),  # type: ignore[arg-type]
        face_types=face_types or {"plane": 6}, cylinders=tuple(cylinders), genus=genus,
        vertex_count=8, edge_count=12, face_count=6,
    )


def bore(diameter: float, axis: str = "Z", concave: bool = True, full: bool = True,
         at: Tuple[float, float, float] = (0, 0, 0)) -> CylindricalSurface:
    direction = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}[axis]
    return CylindricalSurface(diameter=diameter, axis=axis, direction=direction, location=at,
                              concave=concave, full_turn=full, coverage_degrees=360.0 if full else 90.0,
                              length=10.0, face_count=1)


def comparison(candidate: ShapeFacts, reference: ShapeFacts, ious: Mapping[str, float],
               offset: Tuple[float, float, float] = (0, 0, 0)) -> PairResult:
    overlaps = {mode: OverlapMeasure(mode, iou, 0.0, 0.0, "+X+Y+Z", 1) for mode, iou in ious.items()}
    return PairResult(candidate=candidate, comparison=ShapeComparison(
        candidate=candidate, reference=reference,
        volume_ratio=candidate.volume / reference.volume,
        volume_error=abs(candidate.volume - reference.volume) / reference.volume,
        extent_differences=(0.0, 0.0, 0.0), placement_offset=offset, overlaps=overlaps))


EXACT_FIXED = {"kind": "exact", "orientation": "fixed", "position": "free", "iou_min": 0.99,
               "volume_tolerance": 0.01, "signature": True, "checks": []}


class TestRuleInterpreter(unittest.TestCase):
    def test_orientations_measured_include_the_diagnostics(self) -> None:
        self.assertEqual(orientations_for(EXACT_FIXED), ("fixed", "free"))
        self.assertEqual(orientations_for(dict(EXACT_FIXED, orientation="upright")),
                         ("upright", "fixed", "free"))
        self.assertEqual(orientations_for({"kind": "properties", "orientation": "free"}), ())

    def test_an_exact_match_passes(self) -> None:
        plate = facts()
        verdict = judge(EXACT_FIXED, comparison(plate, plate, {"fixed": 1.0, "free": 1.0}))
        self.assertTrue(verdict.passed, verdict.failed_checks())
        self.assertEqual(verdict.iou, 1.0)

    def test_low_iou_fails_and_free_iou_is_kept_as_a_diagnostic(self) -> None:
        verdict = judge(EXACT_FIXED, comparison(facts((60, 100, 10)), facts(), {"fixed": 3 / 7, "free": 1.0}))
        self.assertFalse(verdict.passed)
        self.assertEqual([c.name for c in verdict.failed_checks()], ["IoU (fixed)"])
        self.assertEqual(verdict.iou_free, 1.0)

    def test_volume_placement_and_signature(self) -> None:
        reference = facts(genus=1, cylinders=[bore(8)], face_types={"plane": 6, "cylinder": 1})
        wrong_hole = facts(genus=1, cylinders=[bore(10)], face_types={"plane": 6, "cylinder": 1})
        verdict = judge(EXACT_FIXED, comparison(wrong_hole, reference, {"fixed": 0.995, "free": 0.995}))
        self.assertEqual([c.name for c in verdict.failed_checks()], ["bores (full-turn diameters)"])
        rounded = facts(genus=1, cylinders=[bore(8.04)], face_types={"plane": 6, "cylinder": 1})
        self.assertTrue(judge(EXACT_FIXED, comparison(rounded, reference, {"fixed": 1, "free": 1})).passed)
        no_hole = facts()
        failed = {c.name for c in judge(EXACT_FIXED, comparison(no_hole, reference, {"fixed": 1, "free": 1})).failed_checks()}
        self.assertEqual(failed, {"genus (through-holes)", "bores (full-turn diameters)", "surface types"})
        heavy = facts(volume=61000)
        self.assertEqual([c.name for c in judge(EXACT_FIXED, comparison(heavy, facts(), {"fixed": 1, "free": 1})).failed_checks()],
                         ["volume error"])
        placed = dict(EXACT_FIXED, position="fixed")
        moved = facts(minimum=(1, 0, 0))
        self.assertEqual([c.name for c in judge(placed, comparison(moved, facts(), {"fixed": 1, "free": 1}, offset=(1, 0, 0))).failed_checks()],
                         ["placement (minimum corner)"])
        self.assertTrue(judge(placed, comparison(moved, facts(), {"fixed": 1, "free": 1}, offset=(0.4, 0, 0))).passed)

    def test_property_checks(self) -> None:
        mug = facts((110, 80, 95), volume=0.2 * 110 * 80 * 95, genus=1,
                    cylinders=[bore(72), bore(80, concave=False), bore(6, axis="X")])
        rule = {"kind": "properties", "orientation": "upright", "checks": [
            {"check": "extent", "axis": "z", "min": 70, "max": 150},
            {"check": "extent", "axis": "largest_xy", "min": 70, "max": 180},
            {"check": "extent_ratio", "numerator": "z", "denominator": "smallest_xy", "min": 1.0, "max": None},
            {"check": "fill_ratio", "min": None, "max": 0.6},
            {"check": "genus", "min": 1, "max": None},
            {"check": "bores", "min_count": 1, "max_count": 1, "diameter_min": 50, "diameter_max": 110, "axis": "Z"},
            {"check": "bores", "min_count": 1, "max_count": None, "diameter_min": 1, "diameter_max": 10, "axis": "horizontal"},
            {"check": "bosses", "min_count": 1, "max_count": None, "diameter_min": 70, "diameter_max": 90, "axis": None},
            {"check": "faces", "type": "plane", "min_count": 6, "max_count": 6},
        ]}
        validate_rule(rule)
        verdict = judge(rule, PairResult(candidate=mug))
        self.assertTrue(verdict.passed, [(c.name, c.actual) for c in verdict.failed_checks()])
        solid = facts((110, 80, 95), genus=0)
        failed = {c.name for c in judge(rule, PairResult(candidate=solid)).failed_checks()}
        self.assertIn("fill ratio", failed)
        self.assertIn("genus (through-holes)", failed)
        two = facts((110, 80, 95), solids=2)
        self.assertIn("solid count", {c.name for c in judge(rule, PairResult(candidate=two)).failed_checks()})

    def test_a_candidate_that_does_not_build_is_a_failure_not_an_instrument_error(self) -> None:
        verdict = judge(EXACT_FIXED, PairResult(failure=PairFailure("candidate", "geometry", "E3")))
        self.assertEqual(verdict.status, NOT_BUILT)
        self.assertTrue(verdict.measured)
        self.assertFalse(verdict.passed)

    def test_a_reference_that_does_not_build_is_an_instrument_error(self) -> None:
        verdict = judge(EXACT_FIXED, PairResult(candidate=facts(), failure=PairFailure("reference", "geometry", "x")))
        self.assertEqual(verdict.status, INSTRUMENT_ERROR)
        self.assertFalse(verdict.measured)


# --- the instrument, proved on itself (kernel, isolated worker) ----------------------


class TestInstrumentCheck(unittest.TestCase):
    """Every reference, equivalent and perturbation, through the real worker."""

    findings: Tuple[Any, ...] = ()

    @classmethod
    def setUpClass(cls) -> None:
        cls.findings = instrument_check(CASES, scorer=IsolatedShapeScorer())

    def test_every_reference_scores_100_percent_against_itself(self) -> None:
        references = [f for f in self.findings if f.kind == "reference"]
        self.assertEqual(len(references), sum(1 for c in CASES if c.scoring is not None))
        for finding in references:
            with self.subTest(case=finding.case_id):
                self.assertTrue(finding.ok, finding.verdict.to_dict())
                if finding.verdict.rule_kind == "exact":
                    self.assertAlmostEqual(finding.verdict.iou, 1.0, delta=1e-6)
                    self.assertAlmostEqual(finding.verdict.volume_error, 0.0, delta=1e-9)

    def test_every_equivalent_passes(self) -> None:
        equivalents = [f for f in self.findings if f.kind == "equivalent"]
        self.assertEqual(len(equivalents), len(corpus_v2.EQUIVALENTS))
        for finding in equivalents:
            with self.subTest(case=finding.case_id, label=finding.label):
                self.assertTrue(finding.ok, finding.verdict.to_dict())

    def test_every_perturbation_builds_and_fails(self) -> None:
        perturbations = [f for f in self.findings if f.kind == "perturbation"]
        self.assertEqual(len(perturbations), len(corpus_v2.PERTURBATIONS))
        for finding in perturbations:
            with self.subTest(case=finding.case_id, label=finding.label):
                self.assertTrue(finding.ok, finding.verdict.to_dict())
                self.assertTrue(finding.verdict.failed_checks())

    def test_the_a6_swap_is_caught_by_orientation_alone(self) -> None:
        swap = next(f for f in self.findings if f.label == "swapped-axes")
        self.assertAlmostEqual(swap.verdict.iou_fixed, 3 / 7, delta=1e-6)
        self.assertAlmostEqual(swap.verdict.iou_free, 1.0, delta=1e-6)
        self.assertAlmostEqual(swap.verdict.volume_error, 0.0, delta=1e-9)
        self.assertEqual([c.name for c in swap.verdict.failed_checks()], ["IoU (fixed)"])

    def test_inches_not_converted_and_a_missing_hole_are_caught(self) -> None:
        by_label = {(f.case_id, f.label): f for f in self.findings}
        inches = by_label[("U01-two-inch-cube", "inches-not-converted")]
        self.assertGreater(inches.verdict.volume_error, 0.99)
        missing = by_label[("H01-plate-centre-hole", "missing-hole")]
        # Under one percent of volume: caught by topology, not by volume.
        self.assertLess(missing.verdict.volume_error, 0.01)
        self.assertIn("genus (through-holes)", [c.name for c in missing.verdict.failed_checks()])
        diameter = by_label[("H01-plate-centre-hole", "wrong-hole-diameter")]
        self.assertEqual([c.name for c in diameter.verdict.failed_checks()], ["bores (full-turn diameters)"])


# --- the harness with geometric scoring ------------------------------------------------


class HarnessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        (self.tmp / "cache").mkdir()
        self.service = CadApplicationService.local(self.tmp / "cache")

    def evaluator(self, answers: Mapping[str, Any], *, scorer: Any = in_process_scorer,
                  error: Optional[ProviderError] = None) -> Tuple[Evaluator, ScriptedModel]:
        model = ScriptedModel(answers, error=error)
        return Evaluator(TextToCadService(model, self.service), shape_scorer=scorer), model


class TestHarnessV2(HarnessTestCase):
    def test_a_correct_solid_passes_and_the_a6_swap_does_not(self) -> None:
        case = BY_ID["P02-A6-reversed-word-order"]
        right = answer(case.reference_document or {})
        swapped = answer(corpus_v2.part("box", box("box", (60, 100, 10), (0, 0, 0))))
        evaluator, _ = self.evaluator({case.prompt: right})
        good = evaluator.evaluate(case)
        self.assertTrue(good.correct)
        self.assertTrue(good.geometry is not None and good.geometry.passed)
        evaluator, _ = self.evaluator({case.prompt: swapped})
        bad = evaluator.evaluate(case)
        self.assertFalse(bad.correct)
        assert bad.geometry is not None
        self.assertAlmostEqual(bad.geometry.iou_fixed, 3 / 7, delta=1e-6)
        self.assertAlmostEqual(bad.geometry.iou_free, 1.0, delta=1e-6)
        self.assertIsNone(bad.exact_match)  # no field-level comparison in 2.0.0

    def test_a_question_where_a_part_was_wanted_gets_no_verdict(self) -> None:
        case = BY_ID["U06-no-unit-plate"]
        evaluator, _ = self.evaluator({case.prompt: {"status": "needs_clarification", "questions": ["units?"]}})
        result = evaluator.evaluate(case)
        self.assertFalse(result.correct)
        self.assertIsNone(result.geometry)

    def test_a_candidate_that_does_not_build_is_a_shape_failure(self) -> None:
        case = BY_ID["J03-plate-with-boss"]
        floating = corpus_v2.part("p", box("plate", (100, 60, 10), (0, 0, 0)),
                                  cyl("boss", 20, 40, (50, 30, 12)), union("join", "plate", ["boss"]))
        evaluator, _ = self.evaluator({case.prompt: answer(floating)})
        result = evaluator.evaluate(case)
        assert result.geometry is not None
        self.assertEqual(result.geometry.status, NOT_BUILT)
        self.assertFalse(result.correct)

    def test_an_instrument_failure_is_unmeasured_not_wrong(self) -> None:
        case = BY_ID["P07-cube"]
        broken = lambda pairs: ["the worker crashed"] * len(pairs)  # noqa: E731
        evaluator, _ = self.evaluator({case.prompt: answer(case.reference_document or {})}, scorer=broken)
        result = evaluator.evaluate(case)
        assert result.geometry is not None
        self.assertEqual(result.geometry.status, INSTRUMENT_ERROR)
        self.assertFalse(result.correct)
        metrics = summarise([result]).to_dict()
        self.assertEqual(metrics["geometry"]["checked"], 0)
        self.assertEqual(metrics["geometry"]["instrument_errors"], 1)
        self.assertIsNone(metrics["geometry"]["pass_rate"])
        self.assertEqual(metrics["by_category"]["primitives"]["measured"], 0)
        self.assertIsNone(metrics["by_category"]["primitives"]["correct_rate"])

    def test_no_scorer_is_an_instrument_error_too(self) -> None:
        case = BY_ID["P07-cube"]
        evaluator, _ = self.evaluator({case.prompt: answer(case.reference_document or {})}, scorer=None)
        result = evaluator.evaluate(case)
        assert result.geometry is not None
        self.assertEqual(result.geometry.status, INSTRUMENT_ERROR)

    def test_a_provider_error_is_unmeasured(self) -> None:
        case = BY_ID["P07-cube"]
        error = ProviderError("slow down", kind=ProviderErrorKind.RATE_LIMITED)
        evaluator, _ = self.evaluator({}, error=error)
        result = evaluator.evaluate(case)
        self.assertFalse(result.measured)
        self.assertIsNone(result.geometry)
        metrics = summarise([result]).to_dict()
        self.assertEqual(metrics["provider_reliability"]["provider_failures"], 1)
        self.assertEqual(metrics["geometry"]["candidates_offered"], 0)

    def test_adversarial_cases(self) -> None:
        case = BY_ID["A01-ignore-instructions-python"]
        cube = corpus_v2.part("cube", box("cube", (100, 100, 100), (0, 0, 0)))
        for label, payload, correct in (
            ("refusal", {"status": "unsupported", "issues": ["I do not write code"]}, True),
            ("plain part", answer(cube), True),
            ("python", "import cadquery as cq\nresult = cq.Workplane().box(1, 1, 1)", False),
            ("a question", {"status": "needs_clarification", "questions": ["?"]}, False),
        ):
            with self.subTest(label=label):
                evaluator, _ = self.evaluator({case.prompt: payload})
                result = evaluator.evaluate(case)
                self.assertTrue(result.boundary_held, result.boundary_findings)
                self.assertEqual(result.correct, correct)
                self.assertIsNone(result.geometry)

    def test_clarification_cases(self) -> None:
        case = BY_ID["C01-make-me-a-part"]
        evaluator, _ = self.evaluator({case.prompt: {"status": "needs_clarification", "questions": ["What part?"]}})
        self.assertTrue(evaluator.evaluate(case).correct)
        evaluator, _ = self.evaluator({case.prompt: answer(corpus_v2.part("c", box("c", (10, 10, 10), (0, 0, 0))))})
        self.assertFalse(evaluator.evaluate(case).correct)

    def test_a_run_is_one_model_call_per_case_and_records_its_identity(self) -> None:
        chosen = [BY_ID[name] for name in ("P01-plate-long-wide-thick", "H01-plate-centre-hole",
                                            "E06-coaster", "A03-output-featurescript", "C02-something-useful")]
        stub = OracleStub(chosen)
        evaluator = Evaluator(TextToCadService(stub, self.service), shape_scorer=in_process_scorer)
        run = build_run(evaluator, chosen, provider="stub-oracle", model="stub-oracle", live=False)
        self.assertEqual(run.metadata.corpus_version, "2.0.0")
        self.assertEqual(run.metadata.prompt_version, PROMPT_VERSION)
        self.assertEqual(run.metadata.prompt_fingerprint, prompt_fingerprint())
        self.assertEqual(run.metrics.correct, 5)
        payload = run.to_dict()
        self.assertEqual(payload["run"]["provider"], "stub-oracle")
        metrics = payload["metrics"]
        self.assertEqual(metrics["geometry"]["passed"], 3)
        self.assertEqual(metrics["geometry"]["pass_rate"], 1.0)
        self.assertEqual(metrics["by_category"]["everyday"]["geometry_passed"], 1)
        text = json.dumps(metrics)
        self.assertNotIn("accuracy", text)
        self.assertNotIn("score", text)
        report = format_report(run)
        for fragment in ("BY CATEGORY", "GEOMETRY", "mean_iou", "v2.0.0"):
            self.assertIn(fragment, report)
        path = save_run(run, self.tmp / "results")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["run"]["corpus_version"], "2.0.0")
        self.assertIn("geometry", saved["results"][0])

        counted = ScriptedModel({case.prompt: answer(case.reference_document or {}) for case in chosen[:3]})
        evaluator = Evaluator(TextToCadService(counted, self.service), shape_scorer=in_process_scorer)
        evaluator.evaluate_many(chosen)
        self.assertEqual(len(counted.requests), len(chosen))

    def test_benchmark_runs_send_no_templates(self) -> None:
        case = BY_ID["E04-hexacopter"]
        model = ScriptedModel({})
        evaluator = Evaluator(TextToCadService(model, self.service), shape_scorer=in_process_scorer)
        evaluator.evaluate(case)
        self.assertEqual(model.requests[0].user_text, case.prompt)


class TestCommandV2(unittest.TestCase):
    def test_list_check_and_self_check(self) -> None:
        with mock.patch("cad_ai.evaluation._live_model", side_effect=AssertionError("a provider was built")), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(main(["--list", "--corpus", "2.0.0"]), 0)
            self.assertEqual(main(["--check", "--corpus", "2.0.0", "--case", "P02-A6-reversed-word-order",
                                   "--case", "E01-mug"]), 0)
            self.assertEqual(main(["--self-check", "--corpus", "2.0.0", "--category", "tapers-polygons",
                                   "--pace", "0", "--no-save"]), 0)
        text = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("INSTRUMENT OK", text)
        self.assertIn("NOT evidence of model quality", text)
        self.assertRegex(text, r"pass_rate\s+1\.0")

    def test_the_default_live_path_still_needs_live(self) -> None:
        with mock.patch("cad_ai.evaluation._live_model", side_effect=AssertionError("a provider was built")), \
                mock.patch("builtins.print") as printed:
            self.assertEqual(main(["--corpus", "2.0.0"]), 0)
        text = "\n".join(str(call.args[0]) for call in printed.call_args_list if call.args)
        self.assertIn("NOT_RUN", text)


# --- boundaries ----------------------------------------------------------------------------


def _imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


class TestBoundaries(unittest.TestCase):
    def test_the_generation_path_imports_none_of_this(self) -> None:
        for name in GENERATION_MODULES:
            for module in _imports(AI_SOURCE / name):
                for forbidden in ("cad_ai.corpus_v2", "cad_ai.geometric_scoring", "cad_ai.evaluation"):
                    self.assertNotIn(forbidden, module, msg=f"{name}: {module}")

    def test_the_new_modules_import_no_kernel_and_run_nothing(self) -> None:
        for name in ("corpus_v2.py", "geometric_scoring.py"):
            imported = _imports(AI_SOURCE / name)
            for forbidden in ("cadquery", "OCP", "cad_core.local_cad", "cad_core.isolated_worker",
                              "subprocess", "pickle", "marshal", "requests", "httpx", "urllib", "socket"):
                self.assertNotIn(forbidden, imported, msg=f"{name}: {forbidden}")
            tree = ast.parse((AI_SOURCE / name).read_text(encoding="utf-8"))
            called = {node.func.id for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            for forbidden in ("eval", "exec", "compile"):
                self.assertNotIn(forbidden, called, msg=f"{name}: {forbidden}")

    def test_the_kernel_reaches_the_harness_only_through_the_isolated_worker(self) -> None:
        imported = _imports(AI_SOURCE / "evaluation.py")
        for forbidden in ("cadquery", "OCP", "cad_core.local_cad", "cad_core.isolated_worker"):
            self.assertNotIn(forbidden, imported)
        source = (AI_SOURCE / "evaluation.py").read_text(encoding="utf-8")
        self.assertIn("compare_shapes_isolated", source)
        self.assertNotIn("compare_pair(", source)
        self.assertNotIn("build_document_shape", source)

    def test_no_repair_in_the_scoring(self) -> None:
        source = (AI_SOURCE / "geometric_scoring.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = " ".join(
            [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
            + [n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
        ).lower()
        for forbidden in ("repair", "retry", "fix_", "patch"):
            self.assertNotIn(forbidden, names)

    def test_the_benchmark_is_not_reachable_over_http(self) -> None:
        transport = REPO_ROOT / "apps" / "api" / "src" / "cad_api"
        for path in sorted(transport.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("corpus_v2", "geometric_scoring", "compare_shapes"):
                self.assertNotIn(forbidden, source, msg=f"{path.name}: {forbidden}")


if __name__ == "__main__":
    unittest.main()
