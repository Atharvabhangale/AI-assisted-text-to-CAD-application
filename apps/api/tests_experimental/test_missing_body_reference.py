"""Stage 75 Phase D: a request that NAMES a body which is not there.

Phase C left one clarification failure standing, and Phase D measured it:
corpus case R2 sends *"Make the bracket 10 mm taller."* to a two-body
fixture whose bodies are `block` and `rod`. On the committed prompt the
model declined 32/32, named BOTH bodies 32/32, asked a real question 32/32
and wrote no operations 32/32 -- and never once said `bracket`. Every one of
the 32 attempts was the same failure, and nothing else in the taxonomy fired.

It was answering a different question, correctly. The prompt's only worked
clarification rule is the PRONOUN case -- *"Make it 10 mm taller" with a
plate and a post standing names neither* -- and R2's request is that sentence
with a noun where the pronoun is. Nothing in 33759 characters addressed a
name that matches nothing.

WHAT THE THREE ARMS MEASURED, and why this module pins the shape it does:

  D1  a worked reply envelope for the case, appended to
      `# When to say needs_clarification`                     0/32
  D3  the SAME envelope text, appended to `# Several bodies`  0/32
  D4  a four-line prose CONTRAST, appended to `# Several bodies`
                                                             23/32

D1 and D3 append byte-identical text at two different sites and neither
moved a single call. So this is the first time in this project that an
example lost and prose won, and the reason is that the defect was never the
reply's SHAPE -- the model already produced a perfect envelope -- but which
CASE it thought it was in. An example of a different answer does not say
that; a sentence contrasting the two does.

D3 is therefore the control for D4, and a strong one: same section, MORE
added text (+381 against +277), zero effect. "Any addition at that site
helps" is ruled out by measurement rather than by argument.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGE75 = (HERE.parent.parent.parent / "docs" / "evaluation-baselines"
           / "stage75-multibody")
sys.path.insert(0, str(STAGE75))
sys.path.insert(0, str(STAGE75 / "phase-d-r2"))

from cad_experimental import prompt as prompt_module

import classify_d as CD
import decision_rule_d as R
import evaluate75 as EV
import ground_truth75 as G


def bodies_section(text: str) -> str:
    return text.split("# Several bodies", 1)[1].split("# Units", 1)[0]


def clarification_section(text: str) -> str:
    section = text.split("# When to say needs_clarification", 1)[1]
    return section.split("# What you never do", 1)[0]


#: The sentence that closes the PRONOUN rule. Everything after it in the
#: section is what Phase D added, so the split is a parse rather than a
#: match on the added text itself -- a test that looked for its own words
#: would pass on a prompt that said nothing else.
PRONOUN_RULE_END = "guessing is worse than asking."

#: A `part` declaration in the section's worked plan. The bodies the section
#: DECLARES are read out of it, so the test cannot disagree with the prompt
#: about which ids are bodies.
DECLARES = re.compile(r'"type": "part", "target": "([A-Za-z0-9_-]+)"')

#: A backticked id.
BACKTICKED = re.compile(r"`([^`\n]+)`")

#: A worked request the section quotes. The section shows one before Phase D
#: ("Make it 10 mm taller") and must show two after it. Parsed rather than
#: matched, so the test reads whatever the prompt actually shows.
WORKED_REQUEST = re.compile(r'"(Make [^"]+)"')


class TheSemanticRuleTests(unittest.TestCase):
    """The rule in the grader, where no prompt wording can argue with it."""

    #: A reply that declines, names both fixture bodies, asks a real
    #: question and writes nothing -- and never says the user's noun. This
    #: is, word for word, the shape all 32 baseline attempts took.
    GENERIC = dict(
        operations=[], outcome="needs_clarification",
        summary="the request does not say which body to change",
        questions=("This part has two bodies, `block` and `rod`. "
                   "Which one should be made 10 mm taller?",),
    )

    def _graded(self, case: str):
        return EV.grade(_observe(case, **self.GENERIC))

    def test_the_same_reply_is_right_for_R1_and_wrong_for_R2(self) -> None:
        """This is the whole of Phase D in one assertion.

        R1 is *"Make the body 10 mm taller."* -- it names neither body, so
        listing them and asking which IS the answer. R2 is *"Make the
        bracket 10 mm taller."* -- it NAMES one, and the name matches
        nothing, so the same reply leaves the thing the user actually said
        unanswered.

        One reply, two cases, two verdicts. If this ever passes for both,
        the corpus has stopped telling the two questions apart and every
        Phase D number becomes meaningless.
        """
        r1 = self._graded("R1")
        r2 = self._graded("R2")
        self.assertTrue(r1["strict_success"],
                        "naming both bodies IS the answer when the request "
                        "named neither")
        self.assertFalse(r2["strict_success"],
                         "the same reply ignores the name R2 actually used")
        self.assertIs(r2["checks"]["question_addressed_the_request"], False)
        # and it fails for that reason ALONE -- everything else is perfect.
        self.assertEqual(
            sorted(k for k, v in r2["checks"].items() if v is False),
            ["question_addressed_the_request"],
        )

    def test_only_R2_pins_the_users_own_noun(self) -> None:
        """A criterion on R1 or R3 would change what those cases measure.

        R1 and R3 are 16/16 on the committed prompt precisely because they
        do not carry it; adding one would turn a passing control into a
        second copy of R2 and the arm would have no baseline to move
        against.
        """
        carriers = [name for name in G.ACTIVE
                    if G.expected(name)["refusal_question_must_mention"]]
        self.assertEqual(carriers, ["R2"])

    def test_the_pinned_token_is_the_users_own_word(self) -> None:
        """Not a synonym, not a paraphrase, and not a body id.

        The token has to appear in R2's own request and must not be
        something the fixture declares, or the criterion would be asking the
        model to repeat a name the part actually has.
        """
        truth = G.expected("R2")
        tokens = tuple(truth["refusal_question_must_mention"])
        self.assertEqual(tokens, ("bracket",))
        for token in tokens:
            self.assertIn(token, truth["text"].lower())
            self.assertNotIn(token, [b.lower() for b in G.FIXTURE_BODIES])


class ThePromptDistinguishesTheTwoCasesTests(unittest.TestCase):
    """The adopted change, parsed out of the prompt rather than matched."""

    #: How much text the contrast must be. A mutation that emptied it would
    #: otherwise leave every slice below comparing empty strings and passing
    #: -- `test_union_section_shape` carries the same kind of floor, and its
    #: comment says it exists because a mutation that emptied a loop left the
    #: test green.
    MINIMUM_CONTRAST = 120

    def setUp(self) -> None:
        self.text = prompt_module.system_prompt()
        self.section = bodies_section(self.text)

    def _contrast(self) -> str:
        """Everything the section says AFTER the pronoun rule closes."""
        self.assertIn(PRONOUN_RULE_END, self.section,
                      "the pronoun rule must still be there -- R1 and R3 are "
                      "16/16 on it")
        tail = self.section.split(PRONOUN_RULE_END, 1)[1].strip()
        self.assertGreaterEqual(
            len(tail), self.MINIMUM_CONTRAST,
            "the section stops at the pronoun rule; the case where a request "
            "NAMES a body that is not there is not covered. Measured on such "
            "a section: R2 0/32, every attempt giving the pronoun answer.")
        return tail

    def _declared(self) -> set:
        """The bodies the section's own worked plan declares."""
        declared = set(DECLARES.findall(self.section))
        self.assertEqual(
            declared, {"plate", "post"},
            "the section's worked plan declares the bodies the contrast must "
            "be read against")
        return declared

    def _missing_name(self) -> str:
        """The one id the contrast names that is NOT a body.

        Read by subtraction, so the test cannot disagree with the prompt
        about which ids are bodies, and an example whose "missing" name is
        one of them leaves this set empty rather than passing.
        """
        named = set(BACKTICKED.findall(self._contrast()))
        missing = named - self._declared()
        self.assertEqual(
            len(missing), 1,
            f"the contrast must name exactly one id that is NOT a body; it "
            f"names {sorted(missing)}")
        return missing.pop()

    def test_the_section_shows_TWO_worked_requests(self) -> None:
        """One that names neither body, one that names a body not there.

        This is the rule and the measurement in one assertion. The section
        showed ONE worked request before Phase D, and R2 -- which is that
        request with a noun where the pronoun is -- got that request's
        answer on 32 of 32 live calls.
        """
        requests = WORKED_REQUEST.findall(self.section)
        self.assertEqual(
            len(requests), 2,
            f"the section must show both cases; it shows {requests}")
        pronoun, named = requests
        self.assertRegex(pronoun, r"\bit\b",
                         "the first worked request names no body")
        missing = self._missing_name()
        self.assertIn(
            missing, named.lower(),
            "the second worked request must NAME the id the rule then says "
            "matches nothing; otherwise the example and the rule are about "
            "different things")
        self.assertNotIn(missing, pronoun.lower())

    def test_the_pronoun_rule_comes_FIRST(self) -> None:
        """Order, pinned by index rather than by wording.

        Stage 70's B3 moved two paragraphs of the union section and nothing
        else -- same length, same characters -- and took strict success from
        135/144 to 21/32, p = 0.0001. Order is a live mechanism in a prompt,
        and this one was measured in the order it is committed in.
        """
        contrast = self._contrast()
        pronoun = self.section.index(PRONOUN_RULE_END)
        where = self.section.index(contrast[:40])
        self.assertLess(
            pronoun, where,
            "the case that names NEITHER body is stated first; the contrast "
            "is stated against it")

    def test_the_contrast_is_at_the_BODIES_rule(self) -> None:
        """Where the mis-firing rule already was -- the measured site.

        D1 put the same lesson in `# When to say needs_clarification` and
        moved nothing: 0/32, with replies identical to the baseline's. The
        model routes "which body" through `# Several bodies`, so that is
        where the distinction has to be.
        """
        contrast = self._contrast()
        self.assertIn(contrast[:40], self.section)
        self.assertNotIn(contrast[:40], clarification_section(self.text))

    def test_the_contrast_is_PROSE_not_a_second_reply_envelope(self) -> None:
        """D3 is the control that makes this a measurement, not a taste.

        D3 appended a worked reply envelope to THIS section -- 381
        characters against D4's 277, at the same place -- and scored 0/32.
        More text at the right site is not what worked; stating the
        distinction is. An envelope here would be the arm that was measured
        and rejected.
        """
        contrast = self._contrast()
        for envelope in ('"status"', '"questions"', '"summary"',
                         '"operations"'):
            self.assertNotIn(envelope, contrast)

    def test_the_contrast_lists_the_bodies_that_DO_exist(self) -> None:
        """The half the model already had right, and must keep.

        Naming both bodies was 32/32 on the baseline. An arm that taught the
        model to say the missing name and stopped it listing what exists
        would have traded one failure for another, which is what the
        adoption rule's no-trade clause refuses.
        """
        named = set(BACKTICKED.findall(self._contrast()))
        self.assertTrue(
            self._declared() <= named,
            f"the contrast names {sorted(named)} and must name every body "
            f"the section declares")

    def test_the_contrast_does_not_teach_to_the_test(self) -> None:
        """The corpus request must not be in the prompt, in any form.

        Stage 66 declined to tune a prompt towards its own test's reading
        and recorded why. The missing name here is `flange`, its worked
        request is "5 mm wider", and R2's is "the bracket 10 mm taller" -- a
        different noun, a different dimension and a different adjective.
        """
        lowered = self.text.lower()
        for case in ("R1", "R2", "R3"):
            self.assertNotIn(G.expected(case)["text"].lower().rstrip("."),
                             lowered,
                             f"{case}'s request text is in the prompt")
        missing = self._missing_name()
        for forbidden in tuple(b.lower() for b in G.FIXTURE_BODIES) + tuple(
                G.expected("R2")["refusal_question_must_mention"]):
            self.assertNotEqual(
                missing, forbidden,
                "the worked missing name must not be a word the corpus uses")


class TheTaxonomyTests(unittest.TestCase):
    """`classify_d` puts every attempt in exactly one class, in one order.

    The order is the taxonomy, and it is load-bearing in one place above
    all: a clarification carrying operations is REJECTED by the parser, so
    `plan` is None, `outcome_declared` is `invalid_model_output`, and every
    field the other classes read is empty. Test that row for "no usable
    output" first and the most informative failure in the corpus disappears
    into class F. That is the shape of the defect Phase C found in
    `emitted_no_operations` -- the validator's verdict standing in for the
    model's behaviour -- and these tests exist so it cannot come back.
    """

    def test_operations_beat_the_invalid_output_class(self) -> None:
        """E, not F, and this is the ordering that matters."""
        attempt = _attempt("R2", plan=False, outcome="invalid_model_output",
                           raw_text=SMUGGLED)
        self.assertEqual(attempt["observation"]["outcome_declared"],
                         "invalid_model_output")
        self.assertEqual(attempt["observation"]["operation_count"], 0,
                         "the PARSED plan is empty -- that is the trap")
        self.assertGreater(attempt["observation"]["model_operation_count"], 0,
                           "the MODEL wrote operations -- that is the fact")
        self.assertEqual(CD.classify(attempt), "E")

    def test_no_usable_output_is_F(self) -> None:
        attempt = _attempt("R2", plan=False, outcome="invalid_model_output")
        self.assertEqual(CD.classify(attempt), "F")

    def test_a_plan_instead_of_a_question_is_D(self) -> None:
        """It did not decline at all."""
        attempt = _attempt("R2", outcome="generated", summary="done",
                           questions=())
        self.assertEqual(CD.classify(attempt), "D")

    def test_declining_without_asking_is_D(self) -> None:
        """Half the Phase C baseline's clarifications did exactly this."""
        attempt = _attempt("R2", outcome="needs_clarification",
                           summary="ambiguous request", questions=())
        self.assertEqual(CD.classify(attempt), "D")

    def test_naming_one_body_is_C(self) -> None:
        attempt = _attempt("R2", outcome="needs_clarification",
                           summary="which one", questions=("Did you mean `block`?",))
        self.assertEqual(CD.classify(attempt), "C")

    def test_naming_both_without_the_noun_is_B(self) -> None:
        """The whole R2 baseline, 32 of 32."""
        attempt = _attempt("R2", outcome="needs_clarification",
                           summary="the request does not say which body",
                           questions=("This part has `block` and `rod`. Which?",))
        self.assertEqual(CD.classify(attempt), "B")

    def test_naming_both_AND_the_noun_is_A(self) -> None:
        attempt = _attempt(
            "R2", outcome="needs_clarification",
            summary="the request names a body this part does not have",
            questions=("There is no body called `bracket`. This part has "
                       "`block` and `rod`. Which did you mean?",))
        self.assertEqual(CD.classify(attempt), "A")
        self.assertTrue(attempt["strict_success"])

    def test_the_letter_never_outvotes_the_grader(self) -> None:
        """`cross_check` is what makes the taxonomy safe to report beside a
        rate: class A is a claim that every graded check passed, and
        `evaluate75.grade` is the authority on that."""
        good = _attempt(
            "R2", outcome="needs_clarification",
            summary="no body called `bracket`",
            questions=("This part has `block` and `rod`. Which did you mean?",))
        self.assertIsNone(CD.cross_check(good))
        # A row the grader failed must not be able to read as A.
        lying = dict(good)
        lying["strict_success"] = False
        self.assertIsNotNone(CD.cross_check(lying))

    def test_a_refusal_that_BUILT_something_is_not_correct(self) -> None:
        """Right in every wording respect, and it made a part anyway.

        `grade` fails such a row on `built_nothing`. Without a class for it
        the taxonomy would call it A and only `cross_check` would notice --
        as a bare string, with no letter to count it under.
        """
        attempt = _attempt(
            "R2", outcome="needs_clarification",
            summary="no body called `bracket`",
            questions=("This part has `block` and `rod`. Which did you mean?",),
            execution=_built("block", "rod"))
        self.assertGreater(attempt["observation"]["body_count"], 0)
        self.assertIs(attempt["checks"]["built_nothing"], False)
        self.assertEqual(CD.classify(attempt), "F")
        self.assertIsNone(CD.cross_check(attempt))

    def test_the_taxonomy_refuses_a_CREATION_case(self) -> None:
        """It describes refusals, and a creation case has none of what it
        reads: no bodies a clarification must name, no pinned noun. Every
        naming test is then vacuously true and a correct BUILD reads as
        `E` -- measured, on 46 of 48 creation attempts, before it raised.
        """
        attempt = _attempt("M1", outcome="generated", summary="two bodies")
        with self.assertRaises(ValueError):
            CD.classify(attempt)
        self.assertEqual(CD.refusal_rows([attempt]), [])
        self.assertEqual(set(CD.distribution([attempt]).values()), {0})

    def test_the_PHASE_B_record_re_reads_as_five_E_rows(self) -> None:
        """The fallback, checked against real recorded data.

        Phase B's baseline predates `model_operation_count`: not one of its
        24 refusal observations carries the key. Read with a `, 0` default
        the taxonomy would say the model wrote nothing on every row -- and
        Phase C measured, by hand, that it wrote a full four-operation
        sequence **five** times. `operations_written` re-derives the count
        from the raw answer, so the letter and Phase C's own count agree
        without either being told the other's number.
        """
        record = STAGE75 / "baseline-phase-b.json"
        if not record.is_file():                       # pragma: no cover
            self.skipTest("no Phase B baseline in this checkout")
        rows = CD.refusal_rows(
            json.loads(record.read_text(encoding="utf-8"))["attempts"])
        self.assertEqual(len(rows), 24)
        for row in rows:
            self.assertNotIn("model_operation_count", row["observation"],
                             "this file is the one that predates the key; a "
                             "row carrying it would make the test vacuous")
        self.assertEqual(CD.distribution(rows)["E"], 5)

    def test_a_provider_failure_is_not_the_models_behaviour(self) -> None:
        """`model_error` is the interpretation service failing.

        Calling it "did not decline" would put a transport failure in a
        class that describes what the model chose. The letter comes from
        `evaluate75.outcome_label`, so there is one authority on what a
        provider error is rather than a second list here.
        """
        attempt = _attempt("R2", plan=False, outcome="model_error",
                           error="the interpretation service is unavailable")
        self.assertEqual(EV.outcome_label(attempt["observation"]),
                         G.PROVIDER_ERROR)
        self.assertEqual(CD.classify(attempt), "F")

    def test_a_retired_case_is_dropped_rather_than_raising(self) -> None:
        """`expected()` refuses a retired case on purpose, so a run file
        carrying one must not become unreadable -- it must become a file
        with nothing in it for this taxonomy to describe."""
        attempt = {"case": "M6", "observation": {}, "checks": {},
                   "strict_success": False}
        self.assertEqual(CD.refusal_rows([attempt]), [])

    def test_every_class_is_in_the_distribution(self) -> None:
        """A class that fired zero times must still be reported as zero.

        A distribution that only lists what happened cannot show that
        nothing else did, which is the single most important fact in the
        Phase D baseline: B 32/32 and every other class exactly 0.
        """
        counts = CD.distribution([])
        self.assertEqual(sorted(counts), sorted(CD.CLASS_ORDER))
        self.assertEqual(set(counts.values()), {0})

    def test_the_recorded_baseline_classifies_the_way_it_was_reported(
            self) -> None:
        """The record and the instrument must still agree.

        The run files carry the distribution computed when they were
        written; re-deriving it here catches a later change to the taxonomy
        that would silently restate an old measurement.
        """
        record = STAGE75 / "phase-d-r2" / "baseline-d-r2.json"
        if not record.is_file():                       # pragma: no cover
            self.skipTest("no baseline in this checkout")
        rows = json.loads(record.read_text(encoding="utf-8"))["attempts"]
        self.assertEqual(len(rows), 32)
        counts = CD.distribution(rows)
        self.assertEqual(counts["B"], 32)
        self.assertEqual(sum(v for k, v in counts.items() if k != "B"), 0)
        self.assertEqual([d for d in (CD.cross_check(r) for r in rows) if d],
                         [])


class TheAdoptionRuleFailsClosedTests(unittest.TestCase):
    """Absent evidence is not evidence that nothing regressed.

    The rule was committed before any arm text existed (`209f6fc`) and no
    threshold in it has moved since: MIN_N, ALPHA, MIN_GAIN, the three
    floors, NO_TRADE, IDENTITY_CODES and PLAN_CODES are what they were. What
    changed, after an adversarial read of the module, is that four clauses
    could be satisfied by SILENCE -- a mapping with no `creation` key skipped
    clause 5 and still returned ADOPT. These tests pin the closed shape, and
    the confirmation's verdict is the same either way.
    """

    #: A passing arm and its control, as counts. Shaped like the real ones
    #: and deliberately small, so a clause that stops firing is visible.
    def _pair(self):
        control = {
            "r2": {"strict": 0, "n": 16},
            "r2_checks": {c: 16 for c in R.NO_TRADE},
            "siblings": {"R1": (16, 16), "R3": (16, 16)},
            "creation": {"M1": (8, 8), "N2": (6, 8)},
            "single_body": {"strict": 22, "n": 24},
            "identity_codes": {"D:wrong_target": 2},
            "plan_codes": {"P11": 2},
        }
        arm = {
            "r2": {"strict": 13, "n": 16},
            "r2_checks": {c: 16 for c in R.NO_TRADE},
            "siblings": {"R1": (16, 16), "R3": (15, 16)},
            "creation": {"M1": (8, 8), "N2": (8, 8)},
            "single_body": {"strict": 23, "n": 24},
            "identity_codes": {},
            "plan_codes": {"P11": 1},
        }
        return arm, control

    def test_the_shape_of_the_confirmation_adopts(self) -> None:
        """The control for every test below: this pair must pass."""
        arm, control = self._pair()
        self.assertTrue(R.decide(arm, control, exploratory=False)["adopt"])

    def test_missing_creation_evidence_REJECTS(self) -> None:
        arm, control = self._pair()
        arm.pop("creation")
        self.assertFalse(R.decide(arm, control, exploratory=False)["adopt"])

    def test_an_EMPTY_creation_mapping_REJECTS(self) -> None:
        """Present and empty is not the same as absent, and neither is
        evidence. A run that recorded a `creation` key and no cases would
        otherwise satisfy clause 5 by having nothing to check."""
        arm, control = self._pair()
        arm["creation"] = {}
        self.assertFalse(R.decide(arm, control, exploratory=False)["adopt"])
        arm, control = self._pair()
        control["single_body"] = {}
        self.assertFalse(R.decide(arm, control, exploratory=False)["adopt"])

    def test_an_arm_that_did_not_run_a_case_REJECTS(self) -> None:
        """Dropping the case you are worst at is not preserving it."""
        arm, control = self._pair()
        arm["creation"].pop("N2")
        verdict = R.decide(arm, control, exploratory=False)
        self.assertFalse(verdict["adopt"])
        self.assertTrue(any("N2" in r for r in verdict["reasons"]))

    def test_a_sibling_the_arm_skipped_REJECTS(self) -> None:
        arm, control = self._pair()
        arm["siblings"].pop("R3")
        self.assertFalse(R.decide(arm, control, exploratory=False)["adopt"])

    def test_identity_codes_are_compared_PER_CALL(self) -> None:
        """Halving the sample halves the count; it does not halve the rate.

        With counts, an arm that made 16 creation calls and hit
        `D:wrong_target` twice would read as equal to a control that hit it
        twice in 16 -- and better than one that hit it twice in 48.
        """
        arm, control = self._pair()
        arm["creation"] = {"M1": (4, 4), "N2": (4, 4)}      # 8 calls, not 16
        arm["identity_codes"] = {"D:wrong_target": 2}       # same COUNT
        verdict = R.decide(arm, control, exploratory=False)
        self.assertFalse(verdict["adopt"])
        self.assertTrue(any("D:wrong_target" in r for r in verdict["reasons"]))

    def test_a_regressed_sibling_REJECTS_however_good_R2_is(self) -> None:
        arm, control = self._pair()
        arm["r2"] = {"strict": 16, "n": 16}
        arm["siblings"]["R3"] = (10, 16)
        self.assertFalse(R.decide(arm, control, exploratory=False)["adopt"])

    def test_exploratory_has_no_default(self) -> None:
        """One forgotten keyword would adopt an exploratory reading, which
        is what Stage 67 had to revert."""
        arm, control = self._pair()
        with self.assertRaises(TypeError):
            R.decide(arm, control)
        self.assertFalse(R.decide(arm, control, exploratory=True)["adopt"])

    def test_the_thresholds_are_the_ones_that_were_committed(self) -> None:
        """Hardening a rule is allowed; moving its numbers afterwards is
        not. These are the values in `209f6fc`, before any arm was run."""
        self.assertEqual(R.MIN_N, 16)
        self.assertEqual(R.ALPHA, 0.05)
        self.assertEqual(R.MIN_GAIN, 0.25)
        self.assertEqual(R.REFUSAL_FLOOR, 0.875)
        self.assertEqual(R.CREATION_FLOOR, 0.875)
        self.assertEqual(R.SINGLE_BODY_FLOOR, 0.875)
        self.assertEqual(R.PRIMARY_CASE, "R2")
        self.assertEqual(R.NO_TRADE, (
            "refused", "named_the_bodies", "asked_a_question",
            "emitted_no_operations", "built_nothing"))
        self.assertEqual(R.MUST_BE_PERFECT, ("built_nothing",))
        self.assertEqual(R.PLAN_CODES, ("P11", "P12"))

    def test_fisher_is_right_where_it_matters(self) -> None:
        """Hand-checkable tables, including the two this phase turns on."""
        self.assertAlmostEqual(
            R.fisher_exact_two_sided(0, 32, 0, 32), 1.0, places=12)
        # 13/16 against 0/16 -- the confirmation.
        self.assertLess(R.fisher_exact_two_sided(13, 3, 0, 16), 1e-4)
        # a 2x2 with a known answer: Fisher's tea-tasting table.
        self.assertAlmostEqual(
            R.fisher_exact_two_sided(3, 1, 1, 3), 0.4857142857, places=8)
        self.assertAlmostEqual(
            R.fisher_exact_two_sided(4, 0, 0, 4), 0.02857142857, places=8)


class TheArmsAreWhatTheyClaimTests(unittest.TestCase):
    """The comparison only means anything if the arms differ as stated."""

    def _text(self, name):
        import variants_d as V
        return V.VARIANTS[name]()

    def test_D1_and_D3_append_BYTE_IDENTICAL_text(self) -> None:
        """Otherwise "the same example, moved" is not what was measured.

        Both are 0/32. That is a statement about PLACE only if the text is
        the same, so the two additions are compared here rather than
        assumed -- an arm whose text drifted would turn a controlled
        comparison into two unrelated readings.
        """
        base = self._text("D0-baseline")
        added = [t[len(base):] if t.startswith(base) else None
                 for t in (self._text("D1-missing-body-example"),
                           self._text("D3-example-at-bodies-rule"))]
        # Neither APPENDS at the end of the prompt -- they insert at two
        # different sites -- so compare the added characters by difference.
        import difflib
        d1, d3 = (self._text("D1-missing-body-example"),
                  self._text("D3-example-at-bodies-rule"))
        self.assertEqual(len(d1), len(d3))

        def added_lines(text):
            return tuple(line[1:] for line in difflib.ndiff(
                base.split("\n"), text.split("\n")) if line.startswith("+ "))

        self.assertEqual(added_lines(d1), added_lines(d3),
                         "D1 and D3 must add the same text in two places")
        self.assertTrue(added_lines(d1), "and it must add something")

    def test_the_adopted_arm_now_renders_the_committed_prompt(self) -> None:
        """D4 is the prompt. Its guard must make it idempotent, or a re-run
        would measure a prompt carrying the paragraph twice."""
        self.assertEqual(self._text("D4-contrast-at-bodies-rule"),
                         prompt_module.system_prompt())
        self.assertEqual(self._text("D4-contrast-at-bodies-rule"),
                         self._text("D0-baseline"))

    def test_the_D4_guard_survives_a_REFLOW(self) -> None:
        """Run the GUARD, not a copy of its predicate.

        A sentinel taken as "everything up to the first newline" encodes the
        wrap column, so re-wrapping the prompt -- which changes nothing it
        says -- would make the guard miss and the arm would append a second
        copy of its paragraph. This drives `d4_contrast_at_the_bodies_rule`
        against a reflowed baseline and asserts it adds nothing; asserting
        the predicate directly would pass under the defect.
        """
        import variants_d as V
        reflowed = prompt_module.system_prompt().replace(
            'A request that DOES name one is a different question. "Make '
            'the flange 5 mm\nwider"',
            'A request that DOES name one is a different\nquestion. "Make '
            'the flange 5 mm wider"')
        self.assertNotEqual(reflowed, prompt_module.system_prompt(),
                            "the reflow must actually change the text")
        original = V._baseline
        V._baseline = lambda: reflowed
        try:
            self.assertEqual(
                V.d4_contrast_at_the_bodies_rule(), reflowed,
                "the guard missed on a reflowed prompt and the arm would "
                "have appended its paragraph a second time")
        finally:
            V._baseline = original

    def test_the_recorded_adoption_identity_agrees_with_the_prompt(
            self) -> None:
        import variants_d as V
        self.assertEqual(V.ADOPTED_AS,
                         prompt_module.prompt_fingerprint()[:16])
        self.assertNotEqual(V.MEASURED_AGAINST, V.ADOPTED_AS,
                            "the arms were measured against the PREVIOUS "
                            "prompt; that is the fact the constant records")


class TheTriggerSentenceIsLoadBearingTests(unittest.TestCase):
    """Stage 75 Phase E, and the most expensive thing it learned.

    `# Several bodies` opens its first rule with a trigger clause. Phase E
    rewrote that clause and nothing else, twice, 48 live calls each:

        committed   "does not say WHICH body it means"   R2 34/48 = 70.8%
        E1          "uses no name at all"                R2 37/48 = 77.1%
                                                          p = 0.64, rejected
        E2          "leaves WHICH body unclear"          R2  9/48 = 18.8%
                                                          p < 1e-6, a
                                                          52-POINT COLLAPSE

    One sentence, six or thirteen characters different, and the case swings
    across fifty points. The committed wording is not decorative and it is
    not obviously improvable: the edit that should help helped by six points
    and did not reach significance, and the edit that merely rephrased the
    same idea destroyed it.

    So this class does not assert that the sentence is optimal -- nothing
    measured says that. It asserts that it is the one that was measured, so
    a future rewrite has to be a measurement rather than an edit.
    """

    #: The clause, and the two Phase E measured against it. Read from the
    #: variants module so the test and the experiment cannot disagree about
    #: what was sent.
    def _variants(self):
        sys.path.insert(0, str(STAGE75 / "phase-e-r2-tail"))
        import variants_e as V
        return V

    def test_the_committed_trigger_is_the_one_that_was_measured(self) -> None:
        V = self._variants()
        section = bodies_section(prompt_module.system_prompt())
        self.assertIn(V.TRIGGER, section,
                      "the trigger clause Phase E measured is not in the "
                      "prompt; whatever replaced it is unmeasured")

    def test_the_rejected_rewrites_are_NOT_in_the_prompt(self) -> None:
        """E1 was rejected by the pre-registered rule and E2 is a control
        that made the case 52 points worse. Either appearing in the
        committed prompt would mean an unmeasured or a measured-harmful
        text was adopted."""
        V = self._variants()
        text = prompt_module.system_prompt()
        self.assertNotIn(V.TRIGGER_NARROWED, text)
        self.assertNotIn(V.TRIGGER_REPHRASED, text)

    def test_the_two_rules_still_read_as_a_PAIR(self) -> None:
        """The first rule's trigger and the second's opening are what the
        model chooses between. Phase E's 14 failures all state the first
        rule's diagnosis; the 34 successes all state the second's. If one
        of the two ever goes missing there is nothing to choose between and
        the measurement stops describing the prompt."""
        section = bodies_section(prompt_module.system_prompt())
        first = section.index("do not choose one")
        second = section.index("A request that DOES name one")
        self.assertLess(first, second)

    def test_the_measured_rate_is_recorded_where_it_was_measured(
            self) -> None:
        """The number and the text that produced it live together.

        Phase D reported 13/16 on a sample whose interval was wide; Phase E
        measured the same quantity at 34/48 in one contiguous run, and
        13/16 against 34/48 is p = 0.52. The larger run is the number to
        carry, and this asserts the record still says so.
        """
        record = STAGE75 / "phase-e-r2-tail" / "residual-r2.json"
        if not record.is_file():                       # pragma: no cover
            self.skipTest("no Phase E residual in this checkout")
        data = json.loads(record.read_text(encoding="utf-8"))
        self.assertEqual(data["prompt_fingerprint"],
                         prompt_module.prompt_fingerprint(),
                         "the residual was measured on a different prompt "
                         "than the one committed")
        entry = data["summary"]["per_case"]["R2"]
        self.assertEqual((entry["strict"], entry["calls"]), (34, 48))


class TheAdoptedTextIsWhatWasMeasuredTests(unittest.TestCase):
    """The committed prompt must be the arm the confirmation ran on.

    Phase C's lesson, in its own words: verify against the RECORDED arm
    fingerprint, not against the generator that produced it -- the run file
    is evidence and the generator is code that can drift.
    """

    RECORD = STAGE75 / "phase-d-r2" / "confirm-refusal-D4.json"

    def test_the_prompt_is_byte_identical_to_the_confirmation_run(self) -> None:
        if not self.RECORD.is_file():          # pragma: no cover
            self.skipTest("no confirmation record in this checkout")
        record = json.loads(self.RECORD.read_text(encoding="utf-8"))
        self.assertEqual(record["arm"], "D4-contrast-at-bodies-rule")
        self.assertEqual(
            prompt_module.prompt_fingerprint(), record["arm_fingerprint"],
            "the committed prompt is not the text the confirmation measured")
        self.assertEqual(len(prompt_module.system_prompt()),
                         record["arm_characters"])


#: What the provider returns when the model attaches operations to a
#: `needs_clarification`. Read out of the evaluator's own test module rather
#: than retyped, so the two cannot drift apart about what that answer looks
#: like.
def _smuggled() -> str:
    from test_stage75_multibody_evaluator import SMUGGLED_RAW
    return SMUGGLED_RAW


SMUGGLED = _smuggled()


def _observe(case: str, raw_text=None, execution=None, **kwargs):
    """One observation, built the way the evaluator's own tests build one."""
    from test_stage75_multibody_evaluator import Generation, observe
    return observe(case, Generation(**kwargs), execution, raw_text=raw_text)


def _built(*ids):
    """An execution that produced bodies, for the one case that needs one."""
    from test_stage75_multibody_evaluator import Body, Execution, Measurement
    return Execution([Body(i, Measurement(1.0)) for i in ids])


def _attempt(case: str, raw_text=None, execution=None, **kwargs) -> dict:
    """One recorded attempt, in the shape `classify_d` reads.

    Built through `evaluate75.grade`, never hand-written, so a test about
    the taxonomy cannot quietly disagree with the grader about what the
    checks say.
    """
    observation = _observe(case, raw_text=raw_text, execution=execution,
                           **kwargs)
    graded = EV.grade(observation)
    return {"case": case, "observation": observation,
            "checks": graded["checks"],
            "strict_success": graded["strict_success"]}


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
