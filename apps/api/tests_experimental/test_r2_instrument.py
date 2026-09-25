"""Stage 75 Phase E: the R2 grader, proved rather than assumed.

Phase E's job was to characterise a residual, and a residual is only worth
characterising if the instrument that measures it is sound. This module is
that proof, and it is deliberately separate from the taxonomy tests in
`test_missing_body_reference`: those pin what the classes MEAN, these pin
that the checks read what they claim to read.

Each test answers one question the brief asks, and each is written so that
a guard which stopped working would fail it rather than pass vacuously:

  * does `question_addressed_the_request` read the MODEL's words, or could
    the validator's sentence satisfy it?
  * does `named_the_bodies`?
  * can a refusal case pass without actually refusing?
  * is the operation count taken from the model's own answer?
  * can anything that is not a live model call be labelled MODEL_GENERATED?

The recorded Phase E sample is then read back through the same checks, so
the proof is not only about constructed inputs.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE75 = (HERE.parent.parent.parent / "docs" / "evaluation-baselines"
           / "stage75-multibody")
sys.path.insert(0, str(STAGE75))

import evaluate75 as EV
import ground_truth75 as G

from test_stage75_multibody_evaluator import Generation, observe

#: The recorded Phase E residual. 48 live calls on the committed prompt.
RESIDUAL = STAGE75 / "phase-e-r2-tail" / "residual-r2.json"

#: The token R2 pins, read from the corpus rather than retyped.
NOUN = G.expected("R2")["refusal_question_must_mention"][0]


def _graded(case, **kwargs):
    return EV.grade(observe(case, Generation(**kwargs), None))


class TheChecksReadTheModelsWordsTests(unittest.TestCase):
    """The validator's sentence must never satisfy a check about the model."""

    #: A reply that says NOTHING useful, paired with a system message that
    #: says everything. If a check reads the wrong field, this passes.
    SILENT = dict(operations=[], outcome="needs_clarification",
                  summary="ambiguous", questions=("which one?",))

    def test_the_noun_check_cannot_be_satisfied_by_the_SYSTEM(self) -> None:
        """The exact crossing Phase A made for `named_the_bodies`, applied
        to the criterion Phase D and Phase E turn on.

        Nothing in the repository pinned this for the noun check: Phase B's
        test covers the body names, and the two read the same text through
        the same helper, but "they happen to share a helper" is not a test.
        """
        crossed = _graded("R2", **dict(
            self.SILENT,
            error=f"the request names `{NOUN}`, which is not a live body"))
        self.assertIs(crossed["checks"]["question_addressed_the_request"],
                      False)
        self.assertFalse(crossed["strict_success"])

    def test_the_naming_check_cannot_be_satisfied_by_the_SYSTEM(self) -> None:
        bodies = " and ".join(G.FIXTURE_BODIES)
        crossed = _graded("R2", **dict(
            self.SILENT, error=f"the live bodies are {bodies}"))
        self.assertIs(crossed["checks"]["named_the_bodies"], False)

    def test_the_noun_check_IS_satisfied_by_the_model_saying_it(self) -> None:
        """The other half: a check that can never pass is not a check."""
        said = _graded("R2", operations=[], outcome="needs_clarification",
                       summary=f"this part has no body called `{NOUN}`",
                       questions=(f"`{NOUN}` matches nothing. The bodies are "
                                  f"`{G.FIXTURE_BODIES[0]}` and "
                                  f"`{G.FIXTURE_BODIES[1]}`. Which?",))
        self.assertIs(said["checks"]["question_addressed_the_request"], True)
        self.assertTrue(said["strict_success"])

    def test_the_noun_check_reads_the_QUESTION_as_well_as_the_summary(
            self) -> None:
        """Both are the model's own words and the contract makes `summary`
        required and `questions` optional, so a model that explains itself
        in either has said it. Asserted in both directions so a change that
        narrowed the check to one field is visible."""
        in_summary = _graded(
            "R2", operations=[], outcome="needs_clarification",
            summary=f"no body called `{NOUN}`",
            questions=(f"The bodies are `{G.FIXTURE_BODIES[0]}` and "
                       f"`{G.FIXTURE_BODIES[1]}`. Which?",))
        in_question = _graded(
            "R2", operations=[], outcome="needs_clarification",
            summary="this part has two bodies",
            questions=(f"There is no `{NOUN}`. `{G.FIXTURE_BODIES[0]}` or "
                       f"`{G.FIXTURE_BODIES[1]}`?",))
        for graded in (in_summary, in_question):
            self.assertIs(graded["checks"]["question_addressed_the_request"],
                          True)

    def test_model_words_never_reach_for_the_system_field(self) -> None:
        """Structural, so the behaviour above cannot be reintroduced by a
        helper that starts reading one more key."""
        source = (STAGE75 / "evaluate75.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        body = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_model_words")
        keys = {n.value for n in ast.walk(body)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertNotIn("system_error", keys)
        self.assertNotIn("error", keys)
        self.assertLessEqual({"summary", "plan_reason", "questions"}, keys)


class ARefusalCannotAccidentallyPassTests(unittest.TestCase):
    """Each way of not refusing, and each must be caught."""

    GOOD = dict(
        operations=[], outcome="needs_clarification",
        summary="the request names a body this part does not have",
        questions=("There is no body called `bracket`. This part has `block` "
                   "and `rod`. Which one?",))

    def test_the_good_reply_passes(self) -> None:
        """The control. Every test below changes ONE thing about it."""
        self.assertTrue(_graded("R2", **self.GOOD)["strict_success"])

    def test_a_plan_instead_of_a_question_fails(self) -> None:
        graded = _graded("R2", **dict(self.GOOD, outcome="generated"))
        self.assertIs(graded["checks"]["refused"], False)
        self.assertFalse(graded["strict_success"])

    def test_declining_with_no_question_fails(self) -> None:
        graded = _graded("R2", **dict(self.GOOD, questions=()))
        self.assertIs(graded["checks"]["asked_a_question"], False)
        self.assertFalse(graded["strict_success"])

    def test_naming_only_one_body_fails(self) -> None:
        graded = _graded("R2", **dict(
            self.GOOD,
            questions=("There is no `bracket`. Did you mean `block`?",)))
        self.assertIs(graded["checks"]["named_the_bodies"], False)
        self.assertFalse(graded["strict_success"])

    def test_carrying_operations_fails(self) -> None:
        """Read from the MODEL's raw answer, because the parser rejects such
        a plan and the PARSED count is 0 exactly when the model wrote most."""
        from test_stage75_multibody_evaluator import SMUGGLED_RAW
        observation = observe("R2", Generation(plan=False,
                                               outcome="invalid_model_output"),
                              None, raw_text=SMUGGLED_RAW)
        self.assertEqual(observation["operation_count"], 0)
        self.assertGreater(observation["model_operation_count"], 0)
        graded = EV.grade(observation)
        self.assertIs(graded["checks"]["emitted_no_operations"], False)
        self.assertFalse(graded["strict_success"])

    def test_building_geometry_fails(self) -> None:
        from test_stage75_multibody_evaluator import (
            Body, Execution, Measurement)
        built = Execution([Body("block", Measurement(27000.0))])
        graded = EV.grade(
            observe("R2", Generation(**self.GOOD), built))
        self.assertIs(graded["checks"]["built_nothing"], False)
        self.assertFalse(graded["strict_success"])


class NothingButALiveCallIsMODEL_GENERATEDTests(unittest.TestCase):
    """A deterministic answer must be unable to reach a quality number."""

    def test_the_label_fails_closed(self) -> None:
        for declared, expected in (
                ("generated", G.MODEL_GENERATED),
                ("needs_clarification", G.REFUSED),
                ("unsupported", G.REFUSED),
                ("model_error", G.PROVIDER_ERROR),
                ("invalid_model_output", G.PROVIDER_ERROR),
                (None, G.PROVIDER_ERROR),
                ("something_new", G.PROVIDER_ERROR)):
            with self.subTest(outcome=declared):
                self.assertEqual(
                    EV.outcome_label({"outcome_declared": declared}), expected)

    def test_only_one_label_counts_as_success(self) -> None:
        self.assertEqual(G.COUNTS_AS_SUCCESS, (G.MODEL_GENERATED,))
        for label in (G.DETERMINISTIC, G.FALLBACK, G.REFUSED,
                      G.PROVIDER_ERROR):
            self.assertNotIn(label, G.COUNTS_AS_SUCCESS)

    def test_the_generation_layer_has_no_deterministic_route(self) -> None:
        """Structural, and the reason a FALLBACK cannot occur in this arena.

        `OperationPlanService` is what the arena calls. If it could reach a
        local grammar, a run could record a deterministic answer with a live
        run's identity block and nothing downstream could tell. It imports
        the vendor-neutral provider boundary and its own plan modules, and
        nothing else.
        """
        source = (HERE.parent / "src" / "cad_experimental"
                  / "generation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        # EVERY form, because `from . import normalize` puts the module in
        # `names` and leaves `node.module` as None -- a walk that reads only
        # `node.module` misses the plainest way to add the route.
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                    imported.update(f"{node.module}.{a.name}"
                                    for a in node.names)
                else:
                    imported.update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        flat = {name.rsplit(".", 1)[-1] for name in imported}
        for forbidden in ("normalize", "intent", "local_plan_provider",
                          "local_intent_provider", "interpretation",
                          "body_reference", "questions"):
            self.assertNotIn(forbidden, flat)
        self.assertIn("cad_ai.provider", imported)


class TheRecordedResidualAgreesWithTheChecksTests(unittest.TestCase):
    """The proof applied to the data it was written for, not only to stubs."""

    @classmethod
    def setUpClass(cls) -> None:
        if not RESIDUAL.is_file():                     # pragma: no cover
            raise unittest.SkipTest("no Phase E residual in this checkout")
        cls.rows = json.loads(RESIDUAL.read_text(encoding="utf-8"))["attempts"]

    def test_the_sample_is_what_it_says_it_is(self) -> None:
        record = json.loads(RESIDUAL.read_text(encoding="utf-8"))
        self.assertTrue(record["is_live_model_result"])
        self.assertEqual(record["prompt_version"], "2026-09-25.1")
        self.assertEqual(record["schema_name"], "strict_selector_union_part")
        self.assertEqual(len(self.rows), 48)
        self.assertEqual({r["case"] for r in self.rows}, {"R2"})
        self.assertEqual({r["label"] for r in self.rows}, {G.REFUSED})

    def test_the_noun_check_partitions_the_sample_exactly(self) -> None:
        """No false negative and no false positive, on real recorded text.

        Every attempt that FAILS the check must not contain the token in any
        form, and every attempt that PASSES must contain it. A check that
        drifted from the text it reads would break this without any stub
        being able to show it.
        """
        for row in self.rows:
            said = EV._model_words(row["observation"])
            # RE-GRADED here, not read out of the file. The recorded boolean
            # is what the check said when the run was made; grading again is
            # what makes this a test of the CHECK rather than of the JSON.
            regraded = EV.grade(row["observation"])
            passed = regraded["checks"]["question_addressed_the_request"]
            with self.subTest(attempt=row["attempt"]):
                self.assertEqual(passed, NOUN in said)
                self.assertEqual(
                    passed, row["checks"]["question_addressed_the_request"],
                    "the grader no longer reproduces what was recorded")

    def test_every_attempt_refused_and_built_nothing(self) -> None:
        """The two safety checks, on the whole sample. If either could fail
        silently the residual would be a different problem than the one
        Phase E is characterising."""
        for row in self.rows:
            with self.subTest(attempt=row["attempt"]):
                self.assertIs(row["checks"]["refused"], True)
                self.assertIs(row["checks"]["built_nothing"], True)
                self.assertIs(row["checks"]["emitted_no_operations"], True)
                self.assertIs(row["checks"]["named_the_bodies"], True)

    def test_the_only_failing_check_is_the_noun(self) -> None:
        """Which is what makes the residual ONE mechanism rather than
        several. If a second check ever fails here, the characterisation in
        `phase-e-r2-tail/README.md` no longer describes the data."""
        failing = {name for row in self.rows
                   for name, value in row["checks"].items() if value is False}
        self.assertEqual(failing, {"question_addressed_the_request"})


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
