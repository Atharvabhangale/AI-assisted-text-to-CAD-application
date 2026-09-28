"""Stage 78: an axis is a centreline, an extent is a boundary.

Two things are guarded here and they fail for different reasons.

**The truth cannot come from the output.** `axis_truth` takes a case NAME,
the corpus module imports only `math` and `typing`, and the grader is handed
its landmarks rather than reading them off the plan it is judging. This is
Stage 67's defect made structurally impossible, and every corpus in this
project since has been built the same way.

**The criterion must actually bite.** A guard no wrong answer can trip is
worse than none, because it reports a rate. The tests below take an answer
that IS correct, change exactly one number, and require the grade to flip --
and to flip naming the right mechanism. Two of them encode defects that
really happened, in this stage, to this code: `evaluate78` read Stage 77's
informational `P:export_identity_unproven` as fatal, and it flagged an axis
merely SHARING a coordinate with a neighbour it stood clear of. The second
scored five correct Stage 77 calls as failures. Both were caught by the
offline re-grade before a single live call was spent, and both are mutants
in `mutation_test_78.py` so they cannot come back quietly.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import math
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
STAGE78 = REPO / "docs" / "evaluation-baselines" / "stage78-axis-extent"
STAGE77 = REPO / "docs" / "evaluation-baselines" / "stage77-multibody-corpus"


def _load(name: str, where: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, where / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


G78 = _load("ground_truth78", STAGE78)
E78 = _load("evaluate78", STAGE78)
R78 = _load("decision_rule_78", STAGE78)


def plan(operations) -> str:
    return json.dumps({"status": "generated", "summary": "t",
                       "operations": operations})


def pin(position_x=100.0, position_y=20.0):
    return {"id": "pin", "type": "cylinder",
            "parameters": {"diameter": 20.0, "height": 30.0,
                           "position": {"x": position_x, "y": position_y,
                                        "z": 0.0}}}


def bore(x=100.0, y=20.0, target="pin", axis="+Z"):
    return {"id": "hole_z", "type": "through_hole", "target": target,
            "parameters": {"diameter": 6.0, "axis": axis,
                           "position": {"x": x, "y": y, "z": 0.0}}}


CUBE = {"id": "cube", "type": "box",
        "parameters": {"x": 40.0, "y": 40.0, "z": 40.0}}
DECLS = [{"id": "b1", "type": "part", "target": "cube"},
         {"id": "b2", "type": "part", "target": "pin"}]


def ed02_correct() -> str:
    """The right answer: the bore sits on the pin's axis."""
    return plan([CUBE, pin(), bore(), *DECLS])


def cr06(plate_x=60.0, boss_axis_x=68.0, rod_axis_x=90.0) -> str:
    return plan([
        {"id": "plate", "type": "box",
         "parameters": {"x": plate_x, "y": 40.0, "z": 8.0}},
        {"id": "boss", "type": "cylinder",
         "parameters": {"diameter": 16.0, "height": 20.0,
                        "position": {"x": boss_axis_x, "y": 20.0, "z": 0.0}}},
        {"id": "rod", "type": "cylinder",
         "parameters": {"diameter": 10.0, "height": 40.0,
                        "position": {"x": rod_axis_x, "y": 20.0, "z": 0.0}}},
        {"id": "b1", "type": "part", "target": "plate"},
        {"id": "b2", "type": "part", "target": "boss"},
        {"id": "b3", "type": "part", "target": "rod"},
    ])


class TruthTests(unittest.TestCase):
    """The corpus is fixed, and it cannot see what the model said."""

    def test_axis_truth_takes_a_case_name_and_nothing_else(self) -> None:
        self.assertEqual(list(inspect.signature(G78.axis_truth).parameters),
                         ["case_name"])

    def test_the_corpus_imports_only_math_and_typing(self) -> None:
        tree = ast.parse((STAGE78 / "ground_truth78.py").read_text("utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"math", "typing"})

    def test_the_pinned_geometry_is_re_derived_here(self) -> None:
        """Recomputed independently rather than read back, so a typo in the
        corpus is a failure rather than a shared assumption."""
        truth = G78.axis_truth("ED-02")
        self.assertAlmostEqual(G78.PIN_AXIS_X, 100.0, places=9)
        self.assertAlmostEqual(G78.PIN_MIN_X, 90.0, places=9)
        self.assertAlmostEqual(G78.PIN_MAX_X, 110.0, places=9)
        self.assertAlmostEqual(
            G78.PIN_VOLUME, math.pi * 10.0 ** 2 * 30.0, places=9)
        self.assertAlmostEqual(
            G78.PIN_BORED_VOLUME,
            math.pi * 10.0 ** 2 * 30.0 - math.pi * 3.0 ** 2 * 30.0, places=9)
        # the number `ground_truth77` records Stage 72 as measuring
        self.assertAlmostEqual(G78.PIN_BORED_VOLUME, 8576.547944300135,
                               places=9)
        self.assertEqual(truth["feature_centreline"],
                         {"x": 100.0, "y": 20.0})

    def test_the_axis_is_not_the_extent(self) -> None:
        """The distinction the whole stage turns on, stated as an assertion
        rather than as prose."""
        self.assertNotEqual(G78.PIN_AXIS_X, G78.PIN_MIN_X)
        self.assertNotEqual(G78.PIN_AXIS_X, G78.PIN_MAX_X)
        self.assertAlmostEqual(
            G78.PIN_AXIS_X, (G78.PIN_MIN_X + G78.PIN_MAX_X) / 2.0, places=9)

    def test_cr06_pins_no_absolute_coordinate(self) -> None:
        """Its request states no placement, so none may be pinned. M4 and
        M8 were retired in Stage 75 for exactly this, and the lesson is not
        allowed to lapse."""
        truth = G78.axis_truth("CR-06")
        self.assertEqual(truth["kind"], "relational")
        self.assertTrue(truth["requires_pairwise_disjoint"])
        flat = json.dumps(truth)
        for forbidden in ("feature_centreline", "body_placement",
                          "body_min_extent", "body_max_extent"):
            self.assertNotIn(forbidden, flat,
                             f"CR-06 must not pin {forbidden}")

    def test_ed02_pins_absolutely_because_its_fixture_is_deterministic(self):
        self.assertEqual(G78.axis_truth("ED-02")["kind"], "absolute")

    def test_an_unknown_case_is_refused(self) -> None:
        with self.assertRaises(KeyError):
            G78.axis_truth("ED-99")


class NamingTests(unittest.TestCase):
    """`name_coordinate` is the measurement instrument."""

    MARKS = dict(axis=100.0, minimum=90.0, maximum=110.0, centre=100.0)

    def test_every_landmark_is_named(self) -> None:
        for observed, expected in ((100.0, G78.AT_AXIS),
                                   (90.0, G78.AT_MIN_EXTENT),
                                   (110.0, G78.AT_MAX_EXTENT),
                                   (0.0, G78.AT_ORIGIN),
                                   (57.3, G78.AT_OTHER)):
            self.assertEqual(G78.name_coordinate(observed, **self.MARKS),
                             expected, f"{observed}")

    def test_a_missing_coordinate_is_other_not_axis(self) -> None:
        """An absent number is not a correct one."""
        self.assertEqual(G78.name_coordinate(None, **self.MARKS),
                         G78.AT_OTHER)

    def test_the_landmarks_are_arguments_not_read_from_a_plan(self) -> None:
        """A caller cannot make the answer come out right by handing it the
        model's own numbers as the truth, because it has no access to them."""
        parameters = list(inspect.signature(G78.name_coordinate).parameters)
        self.assertEqual(parameters[0], "observed")
        for required in ("axis", "minimum", "maximum"):
            self.assertIn(required, parameters)


class Ed02Tests(unittest.TestCase):
    def test_the_correct_answer_passes(self) -> None:
        """Without this every mutation test below is vacuous."""
        row = E78.score_turn("ED-02", ed02_correct(), True)
        self.assertTrue(row["axis_ok"], row["checks"])
        self.assertTrue(row["stage78_strict"])
        self.assertEqual(row["codes"], [])

    def test_the_recorded_defect_fails_and_is_named(self) -> None:
        """THE measurement. 17 of 18 fresh failures and 11 of 11 Stage 77
        failures bored here: x = 110, the pin's outer face."""
        row = E78.score_turn("ED-02", plan([CUBE, pin(), bore(x=110.0),
                                            *DECLS]), False)
        self.assertFalse(row["axis_ok"])
        self.assertEqual(row["observation"]["bore_x_is"], G78.AT_MAX_EXTENT)
        self.assertIn(E78.AXIS_AT_MAX_EXTENT, row["codes"])

    def test_the_other_face_is_named_differently(self) -> None:
        row = E78.score_turn("ED-02", plan([CUBE, pin(), bore(x=90.0),
                                            *DECLS]), False)
        self.assertIn(E78.AXIS_AT_MIN_EXTENT, row["codes"])
        self.assertNotIn(E78.AXIS_AT_MAX_EXTENT, row["codes"])

    def test_the_y_axis_is_checked_too(self) -> None:
        """A bore right in x and wrong in y is still the wrong hole. A
        criterion that only looked at one axis would pass it."""
        row = E78.score_turn("ED-02", plan([CUBE, pin(), bore(y=30.0),
                                            *DECLS]), False)
        self.assertFalse(row["axis_ok"])
        self.assertEqual(row["observation"]["bore_x_is"], G78.AT_AXIS)
        self.assertEqual(row["observation"]["bore_y_is"], G78.AT_MAX_EXTENT)

    def test_moving_the_body_is_not_a_fix(self) -> None:
        """An arm could satisfy a naive centreline check by relocating the
        PIN onto the old bore instead of moving the bore onto the axis.
        That is a different part, and it is refused."""
        row = E78.score_turn(
            "ED-02",
            plan([CUBE, pin(position_x=110.0), bore(x=110.0), *DECLS]), False)
        self.assertFalse(row["axis_ok"])
        self.assertIs(row["checks"]["body_did_not_move"], False)
        self.assertIn(E78.BODY_MOVED, row["codes"])
        # And the landmark it was named against came from the CORPUS, not
        # from the cylinder the model wrote. With the pin restated at 110
        # an evaluator that took its landmarks from the plan would call
        # 110 "the axis"; the corpus says 110 is the max extent and 100 is
        # the axis, whatever the model claims. This is Stage 67's defect,
        # and only this assertion separates the two.
        self.assertEqual(row["observation"]["bore_x_is"], G78.AT_MAX_EXTENT)

    def test_a_bore_on_the_wrong_body_is_named(self) -> None:
        row = E78.score_turn("ED-02", plan([CUBE, pin(),
                                            bore(target="cube"), *DECLS]),
                             False)
        self.assertIn(E78.WRONG_TARGET, row["codes"])

    def test_an_omitted_axis_is_the_default_not_a_wrong_direction(self) -> None:
        """THE THIRD DEFECT THIS STAGE FOUND IN ITS OWN INSTRUMENT.

        `axis` is optional and the prompt states the default: "Omit it when
        the description does not say; the default is +Z." The first grader
        read an omitted axis as a wrong direction and scored two PERFECT
        ED-02 answers -- bored at x=100, y=20, the pin's own axis -- as
        `X5:wrong_direction`, in the arm it was about to recommend.
        """
        operations = [CUBE, pin(),
                      {"id": "hole_z", "type": "through_hole", "target": "pin",
                       "parameters": {"diameter": 6.0,
                                      "position": {"x": 100.0, "y": 20.0,
                                                   "z": 0.0}}},
                      *DECLS]
        row = E78.score_turn("ED-02", plan(operations), True)
        self.assertIsNone(row["observation"]["bore_axis_written"])
        self.assertEqual(row["observation"]["bore_axis"], "+Z")
        self.assertTrue(row["axis_ok"], row["checks"])
        self.assertNotIn(E78.WRONG_DIRECTION, row["codes"])

    def test_an_explicit_wrong_axis_is_still_named(self) -> None:
        """The default must not swallow a real wrong direction."""
        row = E78.score_turn("ED-02", plan([CUBE, pin(), bore(axis="+X"),
                                            *DECLS]), False)
        self.assertEqual(row["observation"]["bore_axis_written"], "+X")
        self.assertIn(E78.WRONG_DIRECTION, row["codes"])

    def test_a_bore_along_the_wrong_direction_is_named(self) -> None:
        row = E78.score_turn("ED-02", plan([CUBE, pin(), bore(axis="+X"),
                                            *DECLS]), False)
        self.assertIn(E78.WRONG_DIRECTION, row["codes"])

    def test_no_bore_at_all_is_not_an_axis_success(self) -> None:
        row = E78.score_turn("ED-02", plan([CUBE, pin(), *DECLS]), False)
        self.assertFalse(row["axis_ok"])
        self.assertIn(E78.AXIS_ABSENT, row["codes"])

    def test_unparseable_output_is_not_an_axis_success(self) -> None:
        for raw in (None, "", "not json", "```json\n{}\n```"):
            row = E78.score_turn("ED-02", raw, False)
            self.assertFalse(row["axis_ok"], repr(raw))


class Cr06Tests(unittest.TestCase):
    def test_the_correct_answer_passes(self) -> None:
        row = E78.score_turn("CR-06", cr06(), True)
        self.assertTrue(row["axis_ok"], row["checks"])
        self.assertEqual(row["codes"], [])

    def test_the_recorded_defect_fails_and_is_named(self) -> None:
        """6 of 6 fresh failures and 4 of 4 Stage 77 failures did this: the
        boss's AXIS on the plate's max-x face, so half of it lies inside."""
        row = E78.score_turn("CR-06", cr06(boss_axis_x=60.0), False)
        self.assertFalse(row["axis_ok"])
        self.assertIn(E78.AXIS_AT_MAX_EXTENT, row["codes"])
        self.assertTrue(row["observation"]["axes_on_a_neighbour_boundary"])

    def test_sharing_a_coordinate_with_a_body_it_clears_is_not_a_defect(self):
        """THE SECOND DEFECT THIS STAGE FOUND IN ITS OWN INSTRUMENT.

        Two bodies standing side by side in x legitimately share a y
        coordinate. The first draft flagged that as an axis-on-a-boundary
        and scored FIVE correct Stage 77 calls as failures. The finding is
        now a DIAGNOSIS of an actual overlap, not a criterion of its own.
        """
        # boss axis y = 0, which equals the plate's minimum y -- and the
        # boss stands clear of the plate in x, so nothing is wrong.
        raw = plan([
            {"id": "plate", "type": "box",
             "parameters": {"x": 60.0, "y": 40.0, "z": 8.0}},
            {"id": "boss", "type": "cylinder",
             "parameters": {"diameter": 16.0, "height": 20.0,
                            "position": {"x": 100.0, "y": 0.0, "z": 0.0}}},
            {"id": "rod", "type": "cylinder",
             "parameters": {"diameter": 10.0, "height": 40.0,
                            "position": {"x": 140.0, "y": 0.0, "z": 0.0}}},
            {"id": "b1", "type": "part", "target": "plate"},
            {"id": "b2", "type": "part", "target": "boss"},
            {"id": "b3", "type": "part", "target": "rod"},
        ])
        row = E78.score_turn("CR-06", raw, True)
        self.assertTrue(row["axis_ok"], row["checks"])
        self.assertEqual(row["observation"]["axes_on_a_neighbour_boundary"],
                         [])

    def test_bodies_merely_too_close_still_fail(self) -> None:
        """Overlap is the criterion; the axis-on-a-boundary finding only
        explains it. A cylinder that interferes without sitting exactly on
        a face must still fail."""
        row = E78.score_turn("CR-06", cr06(boss_axis_x=64.0), False)
        self.assertFalse(row["axis_ok"])
        self.assertIs(row["checks"]["bodies_disjoint"], False)

    def test_a_missing_body_is_caught(self) -> None:
        raw = plan([
            {"id": "plate", "type": "box",
             "parameters": {"x": 60.0, "y": 40.0, "z": 8.0}},
            {"id": "boss", "type": "cylinder",
             "parameters": {"diameter": 16.0, "height": 20.0,
                            "position": {"x": 100.0, "y": 20.0, "z": 0.0}}},
        ])
        row = E78.score_turn("CR-06", raw, False)
        self.assertFalse(row["axis_ok"])
        self.assertIs(row["checks"]["three_solids"], False)


class LayeringTests(unittest.TestCase):
    """Stage 78 is strictly stronger than Stage 77, never weaker."""

    def test_a_stage77_failure_can_never_be_a_stage78_success(self) -> None:
        row = E78.score_turn("ED-02", ed02_correct(), False)
        self.assertTrue(row["axis_ok"])
        self.assertFalse(row["stage78_strict"])
        self.assertIn(E78.STAGE77_FAILURE, row["codes"])

    def test_the_axis_criterion_alone_cannot_adopt(self) -> None:
        """An arm that satisfied the axis check while breaking something
        Stage 77 tests is caught, not rewarded."""
        row = E78.score_turn("CR-06", cr06(), False)
        self.assertTrue(row["axis_ok"])
        self.assertFalse(row["stage78_strict"])

    def test_the_two_cases_are_never_pooled(self) -> None:
        rows = [E78.score_turn("ED-02", ed02_correct(), True),
                E78.score_turn("CR-06", cr06(boss_axis_x=60.0), False)]
        summary = E78.summarise(rows)
        self.assertEqual(summary["per_case"]["ED-02"]["stage78_strict"], 1)
        self.assertEqual(summary["per_case"]["CR-06"]["stage78_strict"], 0)
        self.assertNotIn("combined_rate", summary)
        self.assertNotIn("overall", summary)

    def test_every_code_emitted_is_in_the_taxonomy(self) -> None:
        emitted = set()
        for raw, ok in ((ed02_correct(), True),
                        (plan([CUBE, pin(), bore(x=110.0), *DECLS]), False),
                        (plan([CUBE, pin(), bore(x=90.0), *DECLS]), False),
                        (plan([CUBE, pin(), *DECLS]), False),
                        (None, False)):
            emitted.update(E78.score_turn("ED-02", raw, ok)["codes"])
        for raw, ok in ((cr06(), True), (cr06(boss_axis_x=60.0), False)):
            emitted.update(E78.score_turn("CR-06", raw, ok)["codes"])
        self.assertTrue(emitted <= set(E78.AXIS_TAXONOMY),
                        emitted - set(E78.AXIS_TAXONOMY))


class Stage77IsUntouchedTests(unittest.TestCase):
    """Stage 77 is an immutable instrument and Stage 78 reuses it as-is."""

    def test_the_stage77_modules_are_not_imported_for_their_truth(self) -> None:
        """`evaluate78` may not reach into the Stage 77 corpus for an
        expectation -- it has its own, and mixing them would make it unclear
        which corpus a number belongs to."""
        tree = ast.parse((STAGE78 / "evaluate78.py").read_text("utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"json", "typing",
                                                  "ground_truth78"})

    def test_the_historical_numbers_are_recorded_not_recomputed(self) -> None:
        self.assertEqual(G78.stage77_baseline("ED-02"), (21, 32))
        self.assertEqual(G78.stage77_baseline("CR-06"), (28, 32))

    def test_the_stage77_prompt_identity_is_pinned(self) -> None:
        self.assertEqual(G78.STAGE77_PROMPT_VERSION, "2026-09-25.1")
        self.assertTrue(G78.STAGE77_PROMPT_FINGERPRINT.startswith(
            "f265d7d1e279e95a"))
        self.assertEqual(G78.STAGE77_PROMPT_CHARACTERS, 34036)


class RegradeTests(unittest.TestCase):
    """Stage 77's own definition of a pass, applied exactly.

    `P:export_identity_unproven` is attached to EVERY multi-body export and
    Stage 77 excludes it from `FAILURE_CODES` as informational. Reading it
    as fatal scores all 32 of CR-06's calls as losses, which is what the
    first draft of the Stage 78 regrade did.
    """

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(STAGE77))
        cls.R = _load("regrade78", STAGE78)

    def test_an_informational_code_is_not_a_failure(self) -> None:
        self.assertTrue(self.R.turn_passed_stage77(
            {"codes": ["P:export_identity_unproven"]}))

    def test_a_real_failure_code_is_a_failure(self) -> None:
        for code in ("F:wrong_placement", "D:wrong_target",
                     "G:wrong_dimension"):
            self.assertFalse(self.R.turn_passed_stage77({"codes": [code]}),
                             code)

    def test_a_clean_turn_passes(self) -> None:
        self.assertTrue(self.R.turn_passed_stage77({"codes": []}))

    def test_informational_beside_fatal_is_still_a_failure(self) -> None:
        self.assertFalse(self.R.turn_passed_stage77(
            {"codes": ["F:wrong_placement", "P:export_identity_unproven"]}))

    def test_the_regrade_reproduces_stage77_exactly(self) -> None:
        """THE GATE. If the Stage 78 criterion does not reproduce Stage
        77's recorded rate on Stage 77's own recorded bytes, it is
        measuring something else and no arm result is interpretable."""
        summary = self.R.regrade()["summary"]["per_case"]
        for case in ("ED-02", "CR-06"):
            want_pass, want_n = G78.stage77_baseline(case)
            self.assertEqual(summary[case]["stage77_strict"], want_pass, case)
            self.assertEqual(summary[case]["calls"], want_n, case)


class DecisionRuleTests(unittest.TestCase):
    """The rule is a rule: fixed, computable, and able to reject."""

    def test_fisher_matches_reference_values(self) -> None:
        for (a, b, c, d), want in (((1, 9, 11, 3), 0.0027594),
                                   ((3, 1, 1, 3), 0.4857143),
                                   ((10, 0, 0, 10), 1.0825e-05),
                                   ((8, 2, 5, 5), 0.3498),
                                   ((16, 16, 16, 16), 1.0)):
            got = R78.fisher_exact_two_sided(a, b, c, d)
            self.assertAlmostEqual(got, want, delta=max(1e-4, want * 0.02),
                                   msg=f"[{a},{b};{c},{d}]")

    def test_the_confirmation_n_can_actually_resolve_the_threshold(self) -> None:
        """The defect the rule's first draft had: at n = 32 it demanded
        +0.125 AND p < 0.05, which nothing below +0.250 can satisfy. The
        committed n must be able to detect the committed threshold."""
        n = R78.MIN_CONFIRMATION_CALLS_PER_CASE
        baseline = round(21 / 32 * n)
        candidate = round((21 / 32 + R78.MIN_ABSOLUTE_IMPROVEMENT) * n)
        p = R78.fisher_exact_two_sided(candidate, n - candidate,
                                       baseline, n - baseline)
        self.assertLess(p, R78.SIGNIFICANCE_ALPHA,
                        f"n={n} cannot resolve "
                        f"+{R78.MIN_ABSOLUTE_IMPROVEMENT} (p={p:.4g})")

    def test_exploration_does_not_claim_significance(self) -> None:
        self.assertFalse(R78.SIGNIFICANCE_REQUIRED_AT_EXPLORATION)

    def test_an_exploratory_win_cannot_adopt(self) -> None:
        good = R78.assess_case("ED-02", 30, 32, 14, 32, 1, 17)
        self.assertEqual(
            R78.verdict([good], is_confirmation=False)["verdict"],
            R78.INCONCLUSIVE)
        self.assertEqual(
            R78.verdict([good], is_confirmation=True)["verdict"], R78.ADOPT)

    def test_a_regression_elsewhere_rejects(self) -> None:
        good = R78.assess_case("ED-02", 30, 32, 14, 32, 1, 17)
        self.assertEqual(
            R78.verdict([good], regressions=["refusal"],
                        is_confirmation=True)["verdict"], R78.REJECT)

    def test_an_unmoved_mechanism_rejects_even_if_the_rate_rose(self) -> None:
        """A candidate that raised the strict rate while leaving the axis
        mechanism where it was improved something else, and this stage would
        not know what."""
        sneaky = R78.assess_case("ED-02", 30, 32, 14, 32, 16, 17)
        self.assertFalse(sneaky["mechanism_fell_enough"])
        self.assertEqual(
            R78.verdict([sneaky], is_confirmation=True)["verdict"],
            R78.REJECT)

    def test_a_small_improvement_rejects(self) -> None:
        small = R78.assess_case("ED-02", 17, 32, 14, 32, 14, 17)
        self.assertFalse(small["improved_enough"])
        self.assertEqual(
            R78.verdict([small], is_confirmation=True)["verdict"], R78.REJECT)


class ArmTests(unittest.TestCase):
    """Each arm changes ONE thing and touches nothing else."""

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(REPO / "apps" / "api" / "src"))
        sys.path.insert(0, str(REPO / "packages" / "cad-core" / "src"))
        cls.V = _load("variants78", STAGE78)
        cls.base = cls.V.text_for(cls.V.BASELINE)

    def test_no_arm_touches_anything_outside_its_block(self) -> None:
        anchor = self.V.ANCHOR
        tail = self.base[self.base.find(anchor):]
        head = self.base[:self.base.find(anchor)]
        for arm in self.V.VARIANTS:
            if arm == self.V.BASELINE:
                continue
            text = self.V.text_for(arm)
            self.assertTrue(text.endswith(tail), f"{arm} changed the tail")
            self.assertTrue(text.startswith(head), f"{arm} changed the head")
            self.assertEqual(text.count(anchor), 1)

    def test_the_order_control_is_a_pure_reordering(self) -> None:
        """C must carry exactly B's words. If it does not, a difference
        between them is not attributable to order."""
        import re

        def words(text: str):
            return sorted(re.findall(r"[A-Za-z0-9]+", text))

        self.assertEqual(words(self.V.EXAMPLE_B), words(self.V.EXAMPLE_C))
        self.assertNotEqual(self.V.EXAMPLE_B, self.V.EXAMPLE_C)
        self.assertEqual(len(self.V.text_for("S2-worked-example")),
                         len(self.V.text_for("S3-example-reordered")))

    def test_no_arm_uses_corpus_wording(self) -> None:
        """Putting a test's own numbers into the prompt would measure
        recall, not capability. Stage 66 declined to do that."""
        forbidden = ("6 mm", "60 x 40 x 8", "8576", '"x": 100', '"x": 60,')
        for arm in self.V.VARIANTS:
            if arm == self.V.BASELINE:
                continue
            block = self.V.text_for(arm)[len(self.base)
                                         - len(self.base):]  # whole text
            inserted = block.replace(self.base[:self.base.find(
                self.V.ANCHOR)], "")
            for token in forbidden:
                self.assertNotIn(
                    token, self.V.RULE_A + self.V.EXAMPLE_B + self.V.EXAMPLE_C,
                    f"{arm} reuses corpus wording {token!r}")

    def test_the_arm_example_shows_axis_differing_from_both_extents(self):
        """The whole point of arm B: the prompt has never shown a drilled
        body whose axis, minimum and maximum are three different numbers."""
        for block in (self.V.EXAMPLE_B, self.V.EXAMPLE_C):
            for number in ("58", "82", "70", "24"):
                self.assertIn(number, block)
        # 70 is the axis; 58 and 82 are the faces; they are distinct
        self.assertAlmostEqual(70.0 - 24.0 / 2, 58.0)
        self.assertAlmostEqual(70.0 + 24.0 / 2, 82.0)

    def test_an_adopted_arm_becomes_an_alias_of_the_baseline(self) -> None:
        """`_with` is idempotent: if the block is already in the committed
        prompt the arm returns the baseline, so the runner refuses to spend
        calls recording the baseline under the arm's name."""
        block = self.V.RULE_A
        already = self.V._insert_once(self.base, self.V.ANCHOR, block)
        original = self.V._baseline
        try:
            self.V._baseline = lambda: already
            self.assertEqual(self.V.a_rule_only(), already)
        finally:
            self.V._baseline = original


if __name__ == "__main__":
    unittest.main()
