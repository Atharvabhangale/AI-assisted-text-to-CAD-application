"""Stage 77: the broader live multi-body corpus, and the shortcuts it forbids.

Stage 75's corpus is nine cases against ONE two-body shape, and its own
design note says why 47/48 creation is not general reliability: four of its
six creation cases are the same part, nothing above two bodies has ever been
asked of a model, and no creation case exercises a downstream surface. This
corpus answers all three, and this suite is what stops it flattering itself.

**Nothing here calls a model.** Every observation below is either a
hand-written record or a real kernel build of a developer-written reference
plan, and `evaluate77.grade_turn`'s FIRST check is that the source is
`MODEL_GENERATED` -- so a deterministic run cannot score even by accident.

THE SHORTCUTS, each of which passes the obvious test:

* grading a multi-body part by its FIRST body
* accepting a correct TOTAL as proof of a correct split
* not noticing a body that simply is not there
* a refusal that did not refuse, and a clarification that asked nothing
* identity taken from ORDER rather than from the id
* a DETERMINISTIC or FALLBACK answer counted as a model success

Each has a trap below whose only flaw is the named one, and each trap is one
edit away from a record that passes.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
BASE = REPO / "docs" / "evaluation-baselines"
STAGE76 = BASE / "stage76-observation"
STAGE77 = BASE / "stage77-multibody-corpus"


def _load(name: str, folder: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Stage 76 first: Stage 77's evaluator imports it.
G76 = _load("ground_truth76", STAGE76)
O76 = _load("observe76", STAGE76)
EV76 = _load("evaluate76", STAGE76)
G = _load("ground_truth77", STAGE77)
F = _load("fixtures77", STAGE77)
EV = _load("evaluate77", STAGE77)
G75 = _load("ground_truth75", BASE / "stage75-multibody")


def _tree(name: str, folder: pathlib.Path = STAGE77) -> ast.Module:
    return ast.parse((folder / f"{name}.py").read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module):
    """Every module name imported, flattened to its last segment.

    Collects `ImportFrom` nodes whose `module` is `None` too -- the
    `from . import x` form, which an earlier sweep lost a mutant to.
    """
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
                names.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
                names.add(node.module.split(".")[-1])
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
    return names


# ------------------------------------------------- the corpus is broad


class TheCorpusIsActuallyBroaderTests(unittest.TestCase):
    """The whole reason the stage exists. Each assertion here is a fact
    about Stage 75's corpus that this one had to change."""

    def test_every_dimension_is_exercised(self) -> None:
        covered = {d for name in G.ACTIVE
                   for d in G.expected(name)["dimensions"]}
        self.assertEqual(set(G.DIMENSIONS) - covered, set(),
                         "a corpus that quietly drops a dimension measures "
                         "fewer than it claims")

    def test_the_geometric_families_are_not_all_one_shape(self) -> None:
        """Four of Stage 75's six creation cases build the same part."""
        families = {G.expected(n)["family"] for n in G.CREATION_CASES}
        self.assertGreaterEqual(len(families), 6, sorted(families))
        repeats = sum(1 for n in G.CREATION_CASES
                      if G.expected(n)["family"] == "box+cylinder")
        self.assertLessEqual(
            repeats, 2,
            "the box-and-cylinder family must not dominate again")

    def test_something_declares_more_than_two_bodies(self) -> None:
        """`MAX_BODIES` is 8 and Stage 75 never asked for more than two."""
        most = max(G.expected_turn(n, 0)["bodies"] or 0
                   for n in G.CREATION_CASES)
        self.assertGreaterEqual(most, 5)
        self.assertLessEqual(most, 8, "MAX_BODIES is 8")

    def test_a_case_has_two_bodies_of_identical_dimension(self) -> None:
        """Where value cannot disambiguate them even in principle."""
        turn = G.expected_turn("CR-05", 0)
        self.assertEqual(turn["volumes"][0], turn["volumes"][1])
        self.assertIsNotNone(turn["body_ids"])
        # And the PAIRED volumes too, which is the field the grader reads.
        # A sweep caught this: giving the two cubes different sizes in
        # `body_volumes` left the multiset check above untouched.
        paired = turn["body_volumes"]
        self.assertIsNotNone(paired)
        self.assertEqual(len(set(paired.values())), 1,
                         "the two bodies must be identical in the field the "
                         "grader actually compares")

    def test_creation_cases_exercise_the_downstream_surfaces(self) -> None:
        """Stage 75: 'No creation case exercises a downstream surface.'"""
        measured = [n for n in G.CREATION_CASES if G.expected(n)["measure"]]
        exported = [n for n in G.CREATION_CASES if G.expected(n)["export"]]
        self.assertGreaterEqual(len(measured), 8)
        self.assertEqual(len(exported), len(G.CREATION_CASES))

    def test_the_edit_chains_are_long_enough_to_lose_an_edit(self) -> None:
        self.assertEqual(G.expected("ED-04")["calls"], 3)
        self.assertEqual(G.expected("ED-03")["calls"], 2)

    def test_a_refusal_case_generalises_past_two_bodies(self) -> None:
        three = [n for n in G.REFUSAL_CASES
                 if len(G.expected(n)["refusal_must_name"]) >= 3]
        self.assertTrue(three, "a refusal naming two of three is not an "
                               "answer either")


# --------------------------------------------- truth cannot come from output


class TheTruthCannotComeFromOutputTests(unittest.TestCase):
    """Stage 67 derived its expected thickness from the model's own plan."""

    def test_expected_takes_a_name_and_nothing_else(self) -> None:
        signature = inspect.signature(G.expected)
        self.assertEqual(list(signature.parameters), ["case_name"])
        self.assertEqual(signature.parameters["case_name"].annotation, "str")

    def test_expected_turn_takes_a_name_and_an_integer(self) -> None:
        signature = inspect.signature(G.expected_turn)
        self.assertEqual(list(signature.parameters), ["case_name", "index"])
        self.assertEqual(signature.parameters["index"].annotation, "int")

    def test_the_truth_module_imports_nothing_it_judges(self) -> None:
        allowed = {"math", "typing", "__future__", "annotations", "Final",
                   "Mapping", "Optional", "Tuple"}
        self.assertEqual(_imported_names(_tree("ground_truth77")) - allowed,
                         set())

    def test_the_runner_reaches_requests_through_the_narrowed_view(self):
        """`requests_to_send` carries the text and no expectation. A runner
        that could read the expected answer could record it."""
        for name in G.ACTIVE:
            sent = G.requests_to_send(name)
            self.assertEqual(len(sent), G.expected(name)["calls"])
            for text in sent:
                self.assertIsInstance(text, str)

    def test_every_volume_is_a_closed_form(self) -> None:
        import math
        self.assertEqual(G.CUBE_40, 40.0 ** 3)
        self.assertEqual(G.CUBE_30, 30.0 ** 3)
        self.assertEqual(G.PIN_20x30, math.pi * 100.0 * 30.0)
        self.assertEqual(G.FUSED, G.CUBE_40 + G.CUBE_20 - G.FUSE_OVERLAP)
        self.assertLess(G.FUSED, G.CUBE_40 + G.CUBE_20,
                        "a fusion control whose volume is the sum is not a "
                        "control")

    def test_the_outcome_labels_agree_with_the_project(self) -> None:
        self.assertEqual(G.OUTCOMES, G75.OUTCOMES)
        self.assertEqual(G.COUNTS_AS_MODEL_EVIDENCE, G75.COUNTS_AS_SUCCESS)

    def test_a_retired_case_is_never_scored(self) -> None:
        for name in G.RETIRED:
            with self.assertRaises(ValueError):
                G.expected(name)

    def test_placements_are_pinned_only_where_the_request_states_them(self):
        """A request that says 'beside it' gets DISJOINTNESS. Pinning a
        coordinate nobody gave is grading the model against a number it was
        never told."""
        for name in G.CREATION_CASES:
            turn = G.expected_turn(name, 0)
            if turn["placements"] is None:
                continue
            request = turn["request"]
            self.assertTrue(
                "(" in request or "x = " in request,
                f"{name} pins placements but its request states no "
                f"coordinates: {request!r}")


# -------------------------------------------- the corpus cannot be malformed


class TheCorpusRefusesToBeMalformedTests(unittest.TestCase):
    """Constructor guards, driven rather than restated."""

    def test_a_creation_case_may_not_carry_a_fixture(self) -> None:
        with self.assertRaises(ValueError):
            G.Case("X", G.CREATION, family="f", dimensions=("A",),
                   fixture=G.FIXTURE_CUBE_PIN,
                   turns=(G.Turn("r", bodies=1),))

    def test_an_edit_case_must_start_from_a_deterministic_fixture(self):
        with self.assertRaises(ValueError):
            G.Case("X", G.EDIT, family="f", dimensions=("D",),
                   turns=(G.Turn("r", bodies=1),))

    def test_a_refusal_may_not_permit_operations(self) -> None:
        with self.assertRaises(ValueError):
            G.Case("X", G.REFUSAL, family="f", dimensions=("O",),
                   fixture=G.FIXTURE_CUBE_PIN, refusal_must_name=("a",),
                   operations_permitted=True, turns=(G.Turn("r"),))

    def test_a_refusal_must_pin_what_it_names(self) -> None:
        with self.assertRaises(ValueError):
            G.Case("X", G.REFUSAL, family="f", dimensions=("O",),
                   fixture=G.FIXTURE_CUBE_PIN, operations_permitted=False,
                   turns=(G.Turn("r"),))

    def test_export_names_cannot_be_pinned_where_the_model_chooses_them(self):
        with self.assertRaises(ValueError):
            G.Case("X", G.CREATION, family="f", dimensions=("A",),
                   export=True, export_names_pinned=True,
                   turns=(G.Turn("r", bodies=2),))

    def test_paired_volumes_must_cover_the_pinned_ids(self) -> None:
        with self.assertRaises(ValueError):
            G.Turn("r", bodies=2, body_ids=("a", "b"),
                   body_volumes={"a": 1.0})


# ------------------------------------------------------------ the traps


class TheGraderBitesTests(unittest.TestCase):
    """Every record here is one edit from a passing one."""

    def _grade(self, observation):
        return EV.grade_turn(observation)

    def test_the_control_passes(self) -> None:
        """Without this, every assertion below could be passing because the
        grader rejects everything."""
        verdict = self._grade(F.correct_creation_observation())
        self.assertTrue(verdict["strict_success"],
                        [k for k, v in verdict["checks"].items()
                         if v is False])
        self.assertTrue(
            self._grade(F.correct_edit_observation())["strict_success"])
        self.assertTrue(
            self._grade(F.correct_refusal_observation())["strict_success"])

    def test_a_missing_body_is_caught(self) -> None:
        observation = F.CREATION_TRAPS["missing_body"]
        self.assertEqual(observation["body_count"], 1)
        verdict = self._grade(observation)
        self.assertFalse(verdict["strict_success"])
        self.assertIs(verdict["checks"]["body_count"], False)
        self.assertIn(G.A_MISSING_BODY, EV.classify(observation, verdict))

    def test_an_extra_body_is_caught(self) -> None:
        observation = F.CREATION_TRAPS["extra_body"]
        verdict = self._grade(observation)
        self.assertFalse(verdict["strict_success"])
        self.assertIn(G.B_EXTRA_BODY, EV.classify(observation, verdict))

    def test_a_correct_total_is_not_a_correct_split(self) -> None:
        """THE trap the multi-body slice exists for: two disjoint solids
        fused have EXACTLY the total of the two apart."""
        observation = F.CREATION_TRAPS["unwanted_fusion"]
        total = sum(b["volume"] for b in observation["bodies"])
        self.assertAlmostEqual(total, G.CUBE_40 + G.PIN_20x30, places=6,
                               msg="the trap's total must be RIGHT")
        verdict = self._grade(observation)
        self.assertFalse(verdict["strict_success"])
        self.assertIn(G.H_UNWANTED_FUSION, EV.classify(observation, verdict))

    def test_a_right_total_at_the_right_count_is_not_a_right_split(self):
        """The sharper form of the fusion trap: the COUNT is right too, so
        nothing but a per-body comparison can fail it. A mutation sweep
        showed the total-versus-split guard surviving until this existed,
        because the only trap aimed at it failed on the body count."""
        observation = F.CREATION_TRAPS["split_wrong"]
        self.assertEqual(observation["body_count"], 2)
        total = sum(b["volume"] for b in observation["bodies"])
        self.assertAlmostEqual(total, G.CUBE_40 + G.PIN_20x30, places=6,
                               msg="the trap's TOTAL must be right")
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["body_count"], True)
        self.assertIs(verdict["checks"]["volumes"], False)
        self.assertFalse(verdict["strict_success"])
        self.assertIn(G.G_WRONG_DIMENSION, EV.classify(observation, verdict))

    def test_the_right_bodies_in_the_wrong_place_are_caught(self) -> None:
        """Graded against CR-09, whose request states both corners. Volume,
        count, declaration and disjointness are all right."""
        observation = F.CREATION_TRAPS["wrong_placement"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["volumes"], True,
                      "the trap must carry the RIGHT volumes")
        self.assertIs(verdict["checks"]["bodies_disjoint"], True)
        self.assertIs(verdict["checks"]["placements"], False)
        self.assertFalse(verdict["strict_success"])
        self.assertIn(G.F_WRONG_PLACEMENT, EV.classify(observation, verdict))

    def test_a_named_body_in_the_wrong_place_is_caught(self) -> None:
        """The other placement branch. CR-05 names the ids and places them,
        so its boxes are matched PER ID; CR-09 states coordinates without
        ids, so its are matched as an unordered set. A sweep showed a
        mutant surviving because only one branch had a trap."""
        observation = F.CREATION_TRAPS["wrong_placement_named"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["body_ids"], True)
        self.assertIs(verdict["checks"]["body_volumes"], True,
                      "the trap must carry the RIGHT volumes on the RIGHT "
                      "ids")
        self.assertIs(verdict["checks"]["placements"], False)
        self.assertIn(G.F_WRONG_PLACEMENT, EV.classify(observation, verdict))

    def test_bodies_that_interpenetrate_are_caught(self) -> None:
        observation = F.CREATION_TRAPS["not_disjoint"]
        verdict = self._grade(observation)
        self.assertIs(verdict["checks"]["bodies_disjoint"], False)
        self.assertFalse(verdict["strict_success"])

    def test_undeclared_bodies_are_caught(self) -> None:
        """Stage 71: a leftover solid and a declared body are different
        facts, and inferring the second from the first is silent."""
        verdict = self._grade(F.CREATION_TRAPS["undeclared"])
        self.assertIs(verdict["checks"]["declared_every_body"], False)
        self.assertFalse(verdict["strict_success"])

    def test_a_deterministic_answer_is_never_a_model_success(self) -> None:
        """Every geometry check passes and the run learns nothing about a
        model."""
        for trap in ("deterministic_success", "fallback_success"):
            with self.subTest(trap=trap):
                observation = F.CREATION_TRAPS[trap]
                verdict = self._grade(observation)
                self.assertIs(verdict["checks"]["model_generated"], False)
                self.assertFalse(verdict["strict_success"])
                self.assertNotEqual(EV.outcome_label(observation),
                                    G.MODEL_GENERATED)

    def test_identity_is_not_taken_from_order(self) -> None:
        """The same record with its bodies REVERSED must grade identically.
        A grader that paired the n-th body with the n-th expectation flips;
        one that keys on the id cannot tell the difference, and must not."""
        forwards = F.correct_creation_observation()
        backwards = F.correct_creation_observation()
        backwards["bodies"] = list(reversed(backwards["bodies"]))
        backwards["declared_bodies"] = list(
            reversed(backwards["declared_bodies"]))
        self.assertNotEqual([b["id"] for b in forwards["bodies"]],
                            [b["id"] for b in backwards["bodies"]])
        self.assertEqual(self._grade(forwards)["checks"],
                         self._grade(backwards)["checks"])
        self.assertTrue(self._grade(backwards)["strict_success"])

    def test_a_named_case_catches_swapped_bodies(self) -> None:
        """The multiset of volumes is exactly right and each is on the wrong
        body. Only a per-ID check can see it."""
        observation = dict(F.CREATION_TRAPS["swapped_bodies"])
        observation["case"] = "CR-02"
        observation["bodies"] = [
            {**observation["bodies"][0], "id": "base"},
            {**observation["bodies"][1], "id": "post"},
        ]
        observation["declared_bodies"] = ["base", "post"]
        verdict = self._grade(observation)
        self.assertIs(verdict["checks"]["volumes"], True,
                      "the multiset must still be right, or the trap is "
                      "not the trap it claims to be")
        self.assertIs(verdict["checks"]["body_volumes"], False)
        self.assertFalse(verdict["strict_success"])


class TheEditGraderBitesTests(unittest.TestCase):
    def test_the_control_passes(self) -> None:
        self.assertTrue(
            EV.grade_turn(F.correct_edit_observation())["strict_success"])

    def test_a_cross_body_edit_is_caught(self) -> None:
        """The requested change IS present, which is what makes it look
        right; the other body moved with it."""
        observation = F.EDIT_TRAPS["cross_body_edit"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["edit_target_changed"], True)
        self.assertIs(verdict["checks"]["other_bodies_untouched"], False)
        self.assertIn(G.E_CROSS_BODY_EDIT, EV.classify(observation, verdict))

    def test_editing_the_wrong_body_is_caught(self) -> None:
        observation = F.EDIT_TRAPS["wrong_target"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["edit_target_changed"], False)
        self.assertIn(G.D_WRONG_TARGET, EV.classify(observation, verdict))

    def test_doing_nothing_at_all_is_caught(self) -> None:
        """Every body is a real body at a real volume and the request was
        ignored."""
        verdict = EV.grade_turn(F.EDIT_TRAPS["no_change"])
        self.assertIs(verdict["checks"]["edit_target_changed"], False)
        self.assertFalse(verdict["strict_success"])

    def test_renaming_a_body_is_caught(self) -> None:
        """`revision_context` asks explicitly for the ids to be kept. A
        renamed body is a different part and every volume check can still
        pass."""
        observation = F.correct_edit_observation()
        observation["bodies"] = [
            {**observation["bodies"][0], "id": "block"},
            observation["bodies"][1],
        ]
        observation["declared_bodies"] = ["block", "pin"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["body_ids_preserved"], False)
        self.assertIn(G.C_WRONG_IDENTITY, EV.classify(observation, verdict))


class TheRefusalGraderBitesTests(unittest.TestCase):
    def test_the_control_passes(self) -> None:
        self.assertTrue(
            EV.grade_turn(F.correct_refusal_observation())["strict_success"])

    def test_a_refusal_that_did_not_refuse_is_caught(self) -> None:
        observation = F.REFUSAL_TRAPS["did_not_refuse"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["refused"], False)
        self.assertIs(verdict["checks"]["built_nothing"], False)
        self.assertIn(G.J_BAD_REFUSAL, EV.classify(observation, verdict))

    def test_a_clarification_that_asked_nothing_is_caught(self) -> None:
        """Stage 75 Phase C measured half the baseline clarifications doing
        exactly this: `needs_clarification` with an empty questions list."""
        observation = F.REFUSAL_TRAPS["asked_nothing"]
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["asked_a_question"], False)
        self.assertIn(G.K_BAD_CLARIFICATION,
                      EV.classify(observation, verdict))

    def test_naming_one_body_is_not_an_answer(self) -> None:
        verdict = EV.grade_turn(F.REFUSAL_TRAPS["named_one"])
        self.assertIs(verdict["checks"]["named_the_bodies"], False)
        self.assertEqual(verdict["metrics"]["bodies_named"], 1)
        self.assertEqual(verdict["metrics"]["bodies_expected"], 2)

    def test_the_systems_own_sentence_cannot_satisfy_the_naming_check(self):
        """Stage 75 Phase A merged `system_error` into the text searched for
        body names -- and on a refusal the SYSTEM names every body, so the
        check would have passed on every attempt."""
        observation = F.REFUSAL_TRAPS["only_the_system_named_them"]
        self.assertIn("cube", observation["system_error"])
        self.assertIn("pin", observation["system_error"])
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["named_the_bodies"], False)

    def test_a_clarification_carrying_geometry_is_caught(self) -> None:
        """The parser rejects it, so the PARSED count is zero exactly when
        the model emitted the most."""
        observation = F.REFUSAL_TRAPS["clarification_with_operations"]
        self.assertEqual(observation["operation_count"], 0)
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["emitted_no_operations"], False)

    def test_refusing_in_words_while_building_is_caught(self) -> None:
        verdict = EV.grade_turn(F.REFUSAL_TRAPS["refused_but_built"])
        self.assertIs(verdict["checks"]["built_nothing"], False)

    def test_the_missing_noun_is_required_where_the_case_pins_it(self) -> None:
        """RF-02 is Stage 75's R2 shape, whose 70.8 %% is a KNOWN FLOOR."""
        case = G.expected("RF-02")
        self.assertEqual(case["refusal_must_mention"], ("flange",))
        observation = dict(F.correct_refusal_observation())
        observation["case"] = "RF-02"
        verdict = EV.grade_turn(observation)
        self.assertIs(verdict["checks"]["addressed_the_request"], False,
                      "a reply that never says `flange` has not addressed "
                      "the request")


# ------------------------------------------- post-conditions, not blame


class PostConditionsAreNotBlamedForABuildTests(unittest.TestCase):
    """The Stage 76 handoff names this as the thing the join must get
    right: where the join goes decides whether a failed BUILD is recorded
    as a measurement failure or as what it is."""

    def _broken(self):
        observation = F.correct_creation_observation()
        observation["execution_succeeded"] = False
        observation["bodies"] = []
        observation["body_count"] = 0
        return observation

    def test_a_part_that_did_not_build_is_not_a_measurement_failure(self):
        for grade in (EV.grade_measurement, EV.grade_aggregate):
            with self.subTest(grade=grade.__name__):
                verdict = grade(self._broken(), None)
                self.assertEqual(verdict["verdict"], EV.NOT_ASSESSED)
                self.assertIn("nothing was built", verdict["why"])

    def test_a_part_that_did_not_build_is_not_an_export_failure(self) -> None:
        verdict = EV.grade_export(self._broken(), None)
        self.assertEqual(verdict["verdict"], EV.NOT_ASSESSED)

    def test_not_assessed_is_counted_apart_from_failed(self) -> None:
        attempts = [{
            "case": "CR-01", "attempt": 0, "group": G.CREATION,
            "strict_success": False,
            "turns": [{
                "observation": self._broken(),
                "verdict": EV.grade_turn(self._broken()),
                "measurement": EV.grade_measurement(self._broken(), None),
                "aggregate": EV.grade_aggregate(self._broken(), None),
                "export": EV.grade_export(self._broken(), None),
                "codes": [],
            }],
        }]
        summary = EV.summarise(attempts)
        self.assertEqual(summary["measurement"]["of"], 0)
        self.assertEqual(summary["measurement"]["not_assessed"], 1)
        self.assertEqual(summary["export"]["not_assessed"], 1)


class TheSummaryRefusesToFlatterItselfTests(unittest.TestCase):
    def test_there_is_no_combined_rate(self) -> None:
        summary = EV.summarise([])
        self.assertIsNone(summary["combined_rate"])
        self.assertIn("refuses everything", summary["why_no_combined_rate"])

    def test_the_six_denominators_are_separate(self) -> None:
        summary = EV.summarise([])
        for key in ("creation", "edit_turns", "refusal", "measurement",
                    "aggregate", "export"):
            self.assertIn(key, summary)

    def test_an_informational_code_is_not_a_failure(self) -> None:
        """`P:export_identity_unproven` records an honest limit. Counting it
        as a failure would put a floor of 0 %% on every export rate."""
        self.assertIn(G.P_EXPORT_IDENTITY_UNPROVEN, G.TAXONOMY)
        self.assertNotIn(G.P_EXPORT_IDENTITY_UNPROVEN, G.FAILURE_CODES)
        self.assertIn(G.P_EXPORT_IDENTITY_UNPROVEN, G.INFORMATIONAL)

    def test_the_taxonomy_has_no_catch_all_that_swallows_everything(self):
        """A taxonomy whose largest class is 'other' has not classified
        anything. Every trap must land on a NAMED code."""
        traps = list(F.CREATION_TRAPS.items()) + list(F.EDIT_TRAPS.items())
        for name, observation in traps:
            verdict = EV.grade_turn(observation)
            if verdict["strict_success"]:
                continue
            codes = EV.classify(observation, verdict)
            named = [c for c in codes
                     if c in G.FAILURE_CODES and c != G.Q_OTHER]
            with self.subTest(trap=name):
                if name in ("deterministic_success", "fallback_success"):
                    continue     # a source failure, deliberately unnamed
                self.assertTrue(named, f"{name} landed only on {codes}")


class NeverByPositionTests(unittest.TestCase):
    def test_no_constant_index_into_a_body_list(self) -> None:
        watched = {"bodies", "volumes", "body_ids", "declared_bodies",
                   "measured", "names_found", "volumes_read", "ids"}
        for module in ("evaluate77", "ground_truth77"):
            for node in ast.walk(_tree(module)):
                if not isinstance(node, ast.Subscript):
                    continue
                name = (getattr(node.value, "id", None)
                        or getattr(node.value, "attr", None))
                if name not in watched:
                    continue
                # An INTEGER index is positional and forbidden; a string
                # key is a field name on a mapping and is not. The guard
                # flagged `measured["volume"]` before this distinction.
                index = node.slice
                positional = (isinstance(index, ast.Constant)
                              and isinstance(index.value, int)
                              and not isinstance(index.value, bool))
                self.assertFalse(
                    positional,
                    f"{module}: {name}[{getattr(index, 'value', '?')}] "
                    f"at line {node.lineno} -- order is not meaning")

    def test_the_evaluator_reuses_the_stage_76_export_ladder(self) -> None:
        """A second copy of the rung rules would be a second opinion about
        what a rung means."""
        names = _imported_names(_tree("evaluate77"))
        self.assertIn("evaluate76", names)
        self.assertIn("observe76", names)
        source = (STAGE77 / "evaluate77.py").read_text(encoding="utf-8")
        self.assertIn("EV76.export_level", source)


# --------------------------------------------- against a real kernel


def _engines():
    from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
    for name in G.ENGINES:
        try:
            yield resolve_backend(name)
        except BackendUnavailable:
            continue


ENGINES = list(_engines())
SELFCHECK = _load("selfcheck77", STAGE77)


class TheExpectationsAgreeWithTheKernelTests(unittest.TestCase):
    """The preflight, as a test. Every expectation is checked against a real
    build of a developer-written reference plan BEFORE a model is blamed for
    disagreeing with one -- and a closed form wrong by a millimetre would
    otherwise be recorded as a model failure."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ENGINES:
            raise unittest.SkipTest("no CAD backend is available here")
        cls.rows = {engine.name: SELFCHECK.run(engine.name)
                    for engine in ENGINES}

    def test_every_reference_turn_builds_and_grades(self) -> None:
        for engine, rows in self.rows.items():
            self.assertTrue(rows, engine)
            for row in rows:
                with self.subTest(engine=engine,
                                  case=f"{row['case']}#{row['turn']}"):
                    self.assertTrue(row["ok"], row)

    def test_the_fixtures_match_their_truth(self) -> None:
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan
        engine = ENGINES[0]
        for name in G.FIXTURES:
            with self.subTest(fixture=name):
                result = execute_plan(parse_plan(F.plan_for(name)),
                                      part_name="t", backend=engine)
                got = {b.id: b.measurement.volume for b in result.bodies}
                want = G.FIXTURE_BODIES[name]
                self.assertEqual(set(got), set(want))
                for body, volume in want.items():
                    self.assertAlmostEqual(got[body] / volume, 1.0, places=9)

    def test_the_fixture_survives_the_edits_it_is_used_for(self) -> None:
        """The preflight caught this: growing the cube to 60 mm along X
        overlapped the pin at its original position, so `disjoint` on the
        edit turns was an unstated assumption. The fixture moved."""
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan
        result = execute_plan(parse_plan(F.plan_for(G.FIXTURE_CUBE_PIN)),
                              part_name="t", backend=ENGINES[0])
        pin = next(b for b in result.bodies if b.id == "pin")
        self.assertGreaterEqual(
            pin.measurement.minimum[0], 60.0,
            "the pin must stand clear of a cube grown to 60 mm along X")

    def test_a_deterministic_reference_can_never_score(self) -> None:
        """`grade_turn`'s FIRST check is the source. The preflight excludes
        it explicitly rather than pretending the plan came from a model."""
        observation = F.CREATION_TRAPS["deterministic_success"]
        self.assertIs(
            EV.grade_turn(observation)["checks"]["model_generated"], False)


class TheArenaCannotStartByItselfTests(unittest.TestCase):
    def test_live_is_required(self) -> None:
        arena = _load("arena77", STAGE77)
        with self.assertRaises(SystemExit) as raised:
            arena.main([])
        self.assertIn("--live", str(raised.exception))

    def test_the_identity_is_checked_against_the_live_route(self) -> None:
        arena = _load("arena77", STAGE77)
        self.assertEqual(arena.check_identity(), [],
                         "the live route has drifted from what this corpus "
                         "was built against")

    def test_the_arena_refuses_to_write_into_an_earlier_baseline(self) -> None:
        arena = _load("arena77", STAGE77)
        self.assertIn("stage75-multibody", arena.PROTECTED)
        self.assertIn("stage76-observation", arena.PROTECTED)
        target = BASE / "stage75-multibody" / "_stage77_guard_probe.json"
        try:
            with self.assertRaises(SystemExit):
                arena._write({}, str(target))
            self.assertFalse(target.exists(),
                             "the guard must refuse BEFORE writing")
        finally:
            # A mutation sweep DISABLES this guard to prove it bites, and a
            # test that drives a disabled safety check performs the unsafe
            # action for real -- the first sweep left a file inside Stage
            # 75's immutable baseline directory. Cleaned up here rather
            # than left for the next `git status` to find.
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
