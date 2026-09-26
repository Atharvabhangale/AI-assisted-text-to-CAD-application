"""Stage 76: the multi-body OBSERVATION layer, and the ten traps it must fail.

`corpus-design.md` §4 says two of the broader corpus's nine dimensions --
per-body MEASUREMENT and EXPORT -- cannot be graded by any existing
instrument, "because they are not properties of a plan", and that the
observer for them must be built and mutation-tested BEFORE any live call.
This is that proof.

**Nothing here calls a model, and nothing here is evidence about one.** The
six fixture parts are built from plans written by hand; every number they
produce is `DETERMINISTIC` and says something about the OBSERVER only.

WHY A SUITE THIS ADVERSARIAL. Every shortcut an observer can take passes
the obvious test. A grader that pairs answers to bodies by VALUE grades
five of the six cases correctly. One that reads `bodies[0]` grades every
single-body case correctly and reports a pass on every multi-body one. One
that calls an export successful because a file exists passes every case that
ever worked. So the tests below are mostly CORRUPTED observations: each has
exactly one thing wrong, chosen so that a grader taking the named shortcut
reports a pass, and the assertion is that this grader does not.

TWO RULES THESE TESTS ARE HELD TO, both from failures earlier in Stage 75:

* **A test that can pass vacuously must prove it exercised the thing.**
  Every multi-body assertion below first asserts that more than one body
  was actually present. Phase D applied a refusal taxonomy to creation runs
  and 46 correct builds read as a failure code; nothing noticed, because
  every assertion still passed.
* **Drive the guard, do not restate it.** A test that asserts a predicate
  it computes itself passes when the guard is deleted. Each test below
  calls the real `grade` on a real observation shape.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import math
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
STAGE75 = REPO / "docs" / "evaluation-baselines" / "stage75-multibody"
STAGE76 = REPO / "docs" / "evaluation-baselines" / "stage76-observation"


def _load(name: str, folder: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, folder / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


G = _load("ground_truth76", STAGE76)
F = _load("fixtures76", STAGE76)
O = _load("observe76", STAGE76)
EV = _load("evaluate76", STAGE76)
G75 = _load("ground_truth75", STAGE75)

from cad_experimental import questions  # noqa: E402


def _tree(name: str) -> ast.Module:
    return ast.parse((STAGE76 / f"{name}.py").read_text(encoding="utf-8"))


def _imported_names(tree: ast.Module):
    """Every module name imported, flattened to its last segment.

    Collects `ImportFrom` nodes whose `module` is `None` too -- the
    `from . import x` form. Stage 75 Phase E lost a mutant to an AST walk
    that read only `node.module` and so never saw that shape.
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


def _observation(case: str, rows, export=None, *, source=None,
                 geometry=None, model_output=None):
    """A complete observation around the rows under test.

    Built through the real :func:`observe76.observation`, so the source
    checks it performs are exercised rather than bypassed.
    """
    truth = G.expected(case)
    if geometry is None:
        geometry = {
            "backend": "cadquery", "succeeded": True,
            "body_ids": tuple(truth["body_ids"]),
            "declared": tuple(truth["body_ids"])
            if len(truth["body_ids"]) > 1 else (),
            "single_live_body": (truth["body_ids"][0]
                                 if len(truth["body_ids"]) == 1 else None),
            "bodies": {
                body: {"volume": volume, "is_valid": True, "solid_count": 1}
                for body, volume in zip(truth["body_ids"], truth["volumes"])
            },
            "failure": None,
        }
    return O.observation(
        case_name=case,
        source=source or G.SOURCE_DETERMINISTIC,
        model_output=model_output,
        geometry_section=geometry,
        measurement_section=rows,
        export_section=export if export is not None else F.correct_export_row(),
    )


# ------------------------------------------------- truth cannot drift


class TheTruthCannotComeFromOutputTests(unittest.TestCase):
    """Stage 67 derived its expected plate thickness from the model's own
    plan and graded parts against their own answer. A criterion that grades
    a part against its own answer cannot fail it, and it cost that stage its
    headline number."""

    def test_expected_takes_a_name_and_nothing_else(self) -> None:
        for function in (G.expected, G.probes_to_ask):
            signature = inspect.signature(function)
            self.assertEqual(list(signature.parameters), ["case_name"],
                             f"{function.__name__} must take a NAME only")
            # `from __future__ import annotations` makes this the STRING
            # "str", not the type. Compared as written, which is what a
            # reader of the source sees.
            self.assertEqual(
                signature.parameters["case_name"].annotation, "str",
                "a parameter annotated as anything but `str` is a door a "
                "plan, a shape or an answer could come through")

    def test_the_truth_module_imports_nothing_it_judges(self) -> None:
        """A truth module that imports the code it judges can be made to
        agree with it, and the import list is the only guarantee of that a
        reader can check in one glance."""
        allowed = {"math", "typing", "__future__", "annotations", "Final",
                   "Mapping", "Optional", "Tuple"}
        self.assertEqual(_imported_names(_tree("ground_truth76")) - allowed,
                         set())

    def test_no_expectation_mentions_a_measured_or_observed_value(self) -> None:
        """Every volume is arithmetic on a dimension the case states."""
        self.assertEqual(G.CUBE_VOLUME, G.CUBE_EDGE ** 3)
        self.assertEqual(
            G.PIN_VOLUME,
            math.pi * (G.PIN_DIAMETER / 2.0) ** 2 * G.PIN_LENGTH)
        self.assertEqual(G.SLAB_VOLUME, G.SLAB_X * G.SLAB_Y * G.SLAB_Z)
        self.assertEqual(
            G.FUSE_VOLUME,
            G.FUSE_A_EDGE ** 3 + G.FUSE_B_EDGE ** 3 - G.FUSE_OVERLAP)
        # The fuse must be LESS than the sum, or the case is not a control:
        # a part that was never fused would measure the sum.
        self.assertLess(G.FUSE_VOLUME, G.FUSE_A_EDGE ** 3 + G.FUSE_B_EDGE ** 3)

    def test_the_closed_forms_agree_with_stage_75(self) -> None:
        """Repeated rather than imported, and asserted rather than assumed:
        both derive from the same dimensions, so a drift is a bug in one of
        them and neither module depends on the other."""
        self.assertEqual(G.CUBE_VOLUME, G75.CUBE_VOLUME)
        self.assertEqual(G.PIN_VOLUME, G75.PIN_VOLUME)
        self.assertEqual(G.VOLUME_TOLERANCE, G75.VOLUME_TOLERANCE)

    def test_the_mirrored_vocabularies_agree_with_their_sources(self) -> None:
        """A fourth provenance added in `questions` and not here would
        silently stop being gradeable."""
        self.assertEqual(G.PROVENANCE, questions.PROVENANCE)
        self.assertEqual(G.MEASURED, questions.MEASURED)
        self.assertEqual(G.CALCULATED, questions.CALCULATED)
        self.assertEqual(G.ASSUMED, questions.ASSUMED)
        self.assertEqual(G.SOURCES, G75.OUTCOMES)
        self.assertEqual(G.COUNTS_AS_MODEL_EVIDENCE, G75.COUNTS_AS_SUCCESS)

    def test_a_retired_case_is_never_scored(self) -> None:
        for name in G.RETIRED:
            with self.assertRaises(ValueError):
                G.expected(name)
            with self.assertRaises(ValueError):
                G.probes_to_ask(name)

    def test_every_resolver_case_is_exercised_somewhere(self) -> None:
        """A corpus that quietly stops exercising one of the five is a
        corpus that measures four."""
        covered = {probe["kind"]
                   for name in G.ACTIVE
                   for probe in G.expected(name)["probes"]}
        self.assertEqual(set(G.RESOLVER_KINDS) - covered, set())


class TheObserverCannotSeeTheExpectationTests(unittest.TestCase):
    """Disciplined ignorance is not a guarantee. The observer reaches the
    probes through a NARROWED view that carries no expectation at all, so
    it could not record one if it tried."""

    def test_probes_to_ask_carries_no_expectation(self) -> None:
        for name in G.ACTIVE:
            for probe in G.probes_to_ask(name):
                self.assertEqual(set(probe), {"name", "kind", "text"},
                                 f"{name}: a probe the observer can see "
                                 "must carry no answer")

    def test_the_observer_never_reaches_expected(self) -> None:
        source = (STAGE76 / "observe76.py").read_text(encoding="utf-8")
        tree = _tree("observe76")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                self.assertNotEqual(
                    node.attr, "expected",
                    "the observer must not read the truth it is observed "
                    "against")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "expected")
        # And the narrowed view IS the door it uses.
        self.assertIn("probes_to_ask", source)

    def test_the_grader_is_the_only_module_that_reads_truth(self) -> None:
        self.assertIn("expected", {
            node.attr for node in ast.walk(_tree("evaluate76"))
            if isinstance(node, ast.Attribute)
        })


# ------------------------------------------------ never by position


class NeverByPositionTests(unittest.TestCase):
    """Stage 62's bug, and the three `bodies[0]` reads Stages 71-74 each
    found somewhere new -- the last of them in the BROWSER, a stage after
    the server had been fixed."""

    def test_no_constant_index_into_a_body_list(self) -> None:
        watched = {"bodies", "volumes", "body_ids", "ordered", "measured",
                   "asked_for", "names_found", "volumes_read",
                   "export_volumes", "export_names", "live"}
        for module in ("observe76", "evaluate76", "ground_truth76"):
            for node in ast.walk(_tree(module)):
                if not isinstance(node, ast.Subscript):
                    continue
                name = (getattr(node.value, "id", None)
                        or getattr(node.value, "attr", None))
                if name not in watched:
                    continue
                self.assertNotIsInstance(
                    node.slice, ast.Constant,
                    f"{module}: {name}[{getattr(node.slice, 'value', '?')}] "
                    f"at line {node.lineno} -- order is not meaning")

    def test_the_part_measurement_comes_from_the_canonical_part(self) -> None:
        """`ExecutionResult.part` is "the single live body, or None". Stage
        71 deliberately did not widen it to "the first one", and a part
        measurement read off `bodies[0]` is that widening by another name."""
        execution = _FakeExecution([("cube", 64000.0), ("pin", 9424.0)])
        self.assertGreater(len(execution.bodies), 1,
                           "vacuous unless more than one body is present")
        self.assertIsNone(execution.part)
        self.assertEqual(O.part_measurement(execution), {},
                         "a multi-body part has NO part-level measurement; "
                         "returning the first body's is the Stage 62 bug")

    def test_bodies_are_keyed_by_id_not_by_order(self) -> None:
        forwards = O.bodies_by_id(
            _FakeExecution([("cube", 64000.0), ("pin", 9424.0)]))
        backwards = O.bodies_by_id(
            _FakeExecution([("pin", 9424.0), ("cube", 64000.0)]))
        self.assertEqual(len(forwards), 2)
        self.assertEqual(forwards, backwards)


class _FakeMeasurement:
    def __init__(self, volume):
        self.volume = volume

    def to_dict(self):
        return {"volume": self.volume, "is_valid": True, "solid_count": 1,
                "face_count": 6, "edge_count": 12,
                "minimum": [0.0, 0.0, 0.0], "maximum": [40.0, 40.0, 40.0]}


class _FakeBody:
    def __init__(self, body_id, volume):
        self.id = body_id
        self.features = [body_id]
        self.measurement = _FakeMeasurement(volume)


class _FakeExecution:
    """Deliberately plain. These tests are about the OBSERVER, and building
    a kernel solid to test a dictionary would measure the kernel."""

    def __init__(self, pairs, declared=None):
        self.bodies = tuple(_FakeBody(i, v) for i, v in pairs)
        self.declared = tuple(declared or (i for i, _ in pairs))
        self.backend = "cadquery"
        self.failure = None
        self.shapes = {}
        self.succeeded = True

    @property
    def part(self):
        return self.bodies[0].id if len(self.bodies) == 1 else None


# ------------------------------------------------------- the ten traps


#: The ten shortcuts an observer can take that pass the obvious test. Each
#: is a fixture with exactly one thing wrong and a test below that proves
#: the grader fails it. Named here so the set is auditable rather than
#: scattered.
TRAPS = (
    "T1  the first body read as the part",
    "T2  the body inferred from the measurement VALUE",
    "T3  the body inferred from ORDER",
    "T4  a correct total taken as proof of a correct split",
    "T5  two bodies of identical dimension, where value cannot disambiguate",
    "T6  an ambiguous question ANSWERED instead of refused",
    "T7  an ambiguous question DECLINED, so it falls through to a model",
    "T8  a refusal that names only one of the bodies",
    "T9  a file that exists, is well formed, and holds nothing",
    "T10 identity claimed from a substring or from translator metadata",
)


class TheTenTrapsTests(unittest.TestCase):
    """Every one of these observations is one edit from a genuine CadQuery
    2.8.0 run of `X1_PLAN`, and a grader taking the named shortcut passes
    it."""

    def _assert_multi_body(self, case: str) -> None:
        """No trap below is meaningful on one body. Proved, not assumed."""
        self.assertGreater(len(G.expected(case)["body_ids"]), 1,
                           f"{case} must have several bodies or this test "
                           "passes without exercising anything")

    def test_there_are_ten_and_each_has_a_test(self) -> None:
        self.assertEqual(len(TRAPS), 10)

    def test_the_base_observation_passes(self) -> None:
        """The control. Without it every assertion below could be passing
        because the grader rejects everything."""
        self._assert_multi_body("X1")
        verdict = EV.grade(_observation("X1", F.correct_measurement_rows()))
        self.assertTrue(verdict["measurement"]["passed"])
        self.assertTrue(verdict["export"]["passed"])
        self.assertTrue(verdict["passed"])

    def test_T1_the_first_body_is_not_the_part(self) -> None:
        execution = _FakeExecution([("cube", 64000.0), ("pin", 9424.0)])
        self.assertGreater(len(execution.bodies), 1)
        self.assertEqual(O.part_measurement(execution), {})

    def test_T2_swapped_values_are_caught(self) -> None:
        """The multiset of numbers is exactly right and each is attached to
        the wrong body. A grader that paired answers to bodies by value
        reports a clean pass."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["swapped_values"]
        values = sorted(r["value"] for r in rows if r["value"] is not None)
        base = sorted(r["value"] for r in F.correct_measurement_rows()
                      if r["value"] is not None)
        self.assertEqual(values, base,
                         "the trap must carry the RIGHT numbers, or it is "
                         "not the trap it claims to be")
        self.assertFalse(EV.grade(_observation("X1", rows))["measurement"]
                         ["passed"])

    def test_T2b_swapped_labels_are_caught(self) -> None:
        self._assert_multi_body("X1")
        self.assertFalse(
            EV.grade(_observation("X1", F.MEASUREMENT_TRAPS["swapped_labels"])
                     )["measurement"]["passed"])

    def test_T3_order_is_not_meaning(self) -> None:
        """The CONTROL of the set: reversing the rows must change NOTHING.
        A grader that failed this would be wrong in the other direction."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_CONTROLS["reversed_order"]
        self.assertNotEqual([r["probe"] for r in rows],
                            [r["probe"] for r in F.correct_measurement_rows()],
                            "the control must actually be reordered")
        self.assertTrue(
            EV.grade(_observation("X1", rows))["measurement"]["passed"])

    def test_T4_a_right_total_is_not_a_right_split(self) -> None:
        """Two bodies of equal volume summing to exactly the right total.
        Two disjoint solids fused have exactly the total of the two apart,
        which is why the grader compares per body and never sums."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["total_right_split_wrong"]
        per_body = [r["value"] for r in rows
                    if r["kind"] == G.NAMED and r["value"] is not None]
        self.assertAlmostEqual(sum(per_body), G.CUBE_VOLUME + G.PIN_VOLUME,
                               places=6,
                               msg="the trap's total must be RIGHT")
        self.assertFalse(
            EV.grade(_observation("X1", rows))["measurement"]["passed"])

    def test_T4b_a_right_geometry_total_is_not_a_right_split(self) -> None:
        """The same trap one layer down, in the GEOMETRY section. Two bodies
        of equal volume summing to exactly the right total: a grader that
        compared the TOTAL passes the one part it exists to fail, because
        two disjoint solids fused have exactly the total of the two apart."""
        self._assert_multi_body("X1")
        half = (G.CUBE_VOLUME + G.PIN_VOLUME) / 2.0
        geometry = {
            "backend": "cadquery", "succeeded": True,
            "body_ids": ("cube", "pin"), "declared": ("cube", "pin"),
            "single_live_body": None,
            "bodies": {"cube": {"volume": half}, "pin": {"volume": half}},
            "failure": None,
        }
        self.assertAlmostEqual(half * 2, G.CUBE_VOLUME + G.PIN_VOLUME,
                               places=6, msg="the trap's total must be RIGHT")
        verdict = EV.grade(_observation("X1", F.correct_measurement_rows(),
                                        geometry=geometry))["geometry"]
        self.assertFalse(verdict["passed"])
        self.assertIs(verdict["checks"]["volumes"], False)

    def test_T5_identical_dimensions_are_told_apart_by_id_alone(self) -> None:
        """X6's two bodies measure the same to the last digit. Nothing but
        the id distinguishes them -- not the value, not the bounding box,
        not the face or edge count."""
        self._assert_multi_body("X6")
        truth = G.expected("X6")
        self.assertEqual(truth["volumes"][0], truth["volumes"][1],
                         "the case is pointless unless the volumes are equal")
        good = [
            {"probe": "X6-named-left", "kind": G.NAMED, "outcome": G.ANSWERED,
             "about": "left", "aggregate": False, "provenance": G.MEASURED,
             "value": G.CUBE_VOLUME, "said": "left: Volume 64000.000 mm3.",
             "label_is_prefix": True},
            {"probe": "X6-named-right", "kind": G.NAMED, "outcome": G.ANSWERED,
             "about": "right", "aggregate": False, "provenance": G.MEASURED,
             "value": G.CUBE_VOLUME, "said": "right: Volume 64000.000 mm3.",
             "label_is_prefix": True},
            {"probe": "X6-unnamed", "kind": G.UNNAMED_SEVERAL,
             "outcome": G.REFUSED, "about": None, "aggregate": False,
             "provenance": None, "value": None,
             "said": "this part has 2 separate bodies ('left', 'right'), and "
                     "the request does not say which one to measure",
             "label_is_prefix": None},
            {"probe": "X6-total", "kind": G.AGGREGATE_TOTAL,
             "outcome": G.ANSWERED, "about": None, "aggregate": True,
             "provenance": G.CALCULATED, "value": 2.0 * G.CUBE_VOLUME,
             "said": "all 2 bodies: Volume 128000.000 mm3.",
             "label_is_prefix": True},
        ]
        self.assertTrue(
            EV.grade(_observation("X6", good))["measurement"]["passed"])
        # Swap ONLY the labels. Every number stays right; a value-matching
        # grader cannot fail this even in principle.
        swapped = [dict(row) for row in good]
        swapped[0]["about"] = "right"
        swapped[1]["about"] = "left"
        self.assertEqual([r["value"] for r in swapped],
                         [r["value"] for r in good],
                         "the trap must change nothing but the label")
        self.assertFalse(
            EV.grade(_observation("X6", swapped))["measurement"]["passed"])

    def test_T6_answering_an_ambiguous_question_fails(self) -> None:
        """The failure the whole measurement slice exists to prevent, and it
        looks exactly like a success: a real number about a real body."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["answered_the_ambiguous"]
        answered = next(r for r in rows if r["probe"] == "X1-unnamed")
        self.assertEqual(answered["outcome"], G.ANSWERED)
        self.assertEqual(answered["value"], G.CUBE_VOLUME,
                         "the trap answers with a number the part really has")
        graded = EV.grade(_observation("X1", rows))["measurement"]
        self.assertFalse(graded["passed"])
        # WHICH check failed, not merely that one did. Asserting only the
        # verdict let a mutant that neutered the outcome check survive: the
        # probe still failed, for the NAMING reason, and the test could not
        # tell the two apart.
        self.assertIs(graded["probes"]["X1-unnamed"]["checks"]["outcome"],
                      False)

    def test_T7_declining_an_ambiguous_question_fails(self) -> None:
        """Distinct from T6 and separately fatal. `None` falls through to a
        model, which can read the plan but has never seen the part, so it
        would answer "the volume is 64000" about a two-body part and nothing
        downstream could tell that from a right answer."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["declined_the_ambiguous"]
        self.assertEqual(
            next(r for r in rows if r["probe"] == "X1-unnamed")["outcome"],
            G.DECLINED)
        graded = EV.grade(_observation("X1", rows))["measurement"]
        self.assertFalse(graded["passed"])
        self.assertIs(graded["probes"]["X1-unnamed"]["checks"]["outcome"],
                      False)

    def test_T8_a_refusal_must_name_every_body(self) -> None:
        """It refused, so nothing was guessed. It is still not an answer:
        the person cannot act on it."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["refusal_names_one"]
        said = next(r for r in rows if r["probe"] == "X1-unnamed")["said"]
        self.assertIn("cube", said)
        self.assertNotIn("pin", said)
        self.assertFalse(
            EV.grade(_observation("X1", rows))["measurement"]["passed"])

    def test_T9_a_file_that_exists_is_not_an_export(self) -> None:
        """FreeCAD's `Part.export`, handed raw shapes, leaves a well-formed
        1 640-byte STEP that reads back as ZERO solids. It exists, it is
        non-empty, it parses, and every check short of counting passes."""
        self._assert_multi_body("X1")
        row = F.FILE_EXISTS_BUT_EMPTY
        self.assertTrue(row["wrote_file"])
        self.assertGreater(row["bytes"], 1000)
        self.assertTrue(row["readable"])
        verdict = EV.grade(_observation(
            "X1", F.correct_measurement_rows(), export=row))["export"]
        self.assertEqual(verdict["level"], G.LEVEL_C)
        self.assertFalse(verdict["passed"])

    def test_T10_identity_is_never_claimed_from_a_substring(self) -> None:
        """`cad_backend.verify_assembly` tests each id with `name not in
        written_text` -- a substring scan over the whole file, boilerplate
        included. MEASURED on real two-body files from both engines, it
        accepts `'SOLID'`, `'part'`, `'Open'` and `'cub'`. An observer that
        reported what it reports would inherit that."""
        self._assert_multi_body("X1")
        row = F.EXPORT_TRAPS["D_substring_fooled"]
        self.assertEqual(sorted(row["names_found_by_substring"]),
                         sorted(row["asked_for"]),
                         "the trap must be one the substring scan PASSES")
        self.assertEqual(row["names_found"], [],
                         "and one the product-name check fails")
        # Graded against X1's truth, whose names are `cube` and `pin`: the
        # file names neither, whatever its boilerplate contains.
        verdict = EV.grade(_observation(
            "X1", F.correct_measurement_rows(), export=row))["export"]
        self.assertEqual(verdict["level"], G.LEVEL_D)

    def test_a_missing_probe_is_a_failure_not_an_absence(self) -> None:
        """Five correct answers out of six looks like a good run, and the
        question that was not asked is the one that would have failed."""
        self._assert_multi_body("X1")
        rows = F.MEASUREMENT_TRAPS["missing_probe"]
        self.assertEqual(len(rows), len(F.correct_measurement_rows()) - 1)
        graded = EV.grade(_observation("X1", rows))["measurement"]
        self.assertFalse(graded["passed"])
        self.assertFalse(graded["probes"]["X1-unnamed"]["checks"]["asked"])

    def test_an_aggregate_may_not_claim_to_be_measured(self) -> None:
        """The number is right to the last digit and no kernel measured it:
        nothing ever put the two bodies on a scale together."""
        self._assert_multi_body("X1")
        for trap in ("total_claimed_measured", "size_claimed_measured"):
            with self.subTest(trap=trap):
                self.assertFalse(
                    EV.grade(_observation("X1", F.MEASUREMENT_TRAPS[trap])
                             )["measurement"]["passed"])


# ------------------------------------------------------ the export ladder


class TheExportLadderTests(unittest.TestCase):
    """Six rungs, strictly increasing, and only the top is a success."""

    def _level(self, case, row):
        return EV.grade(_observation(
            case, F.correct_measurement_rows() if case == "X1" else [],
            export=row))["export"]["level"]

    def test_there_are_six_and_they_are_ordered(self) -> None:
        self.assertEqual(len(G.EXPORT_LEVELS), 6)
        self.assertEqual(G.EXPORT_LEVELS[0], G.LEVEL_A)
        self.assertEqual(G.EXPORT_LEVELS[-1], G.LEVEL_F)
        self.assertEqual(set(G.EXPORT_LEVEL_MEANING), set(G.EXPORT_LEVELS))

    def test_each_rung_is_reached_by_its_own_observation(self) -> None:
        for level, row in (
            (G.LEVEL_A, F.EXPORT_TRAPS[G.LEVEL_A]),
            (G.LEVEL_B, F.EXPORT_TRAPS[G.LEVEL_B]),
            (G.LEVEL_C, F.EXPORT_TRAPS["C_empty"]),
            (G.LEVEL_C, F.EXPORT_TRAPS["C_fused"]),
            (G.LEVEL_D, F.EXPORT_TRAPS[G.LEVEL_D]),
            (G.LEVEL_E, F.EXPORT_TRAPS[G.LEVEL_E]),
            (G.LEVEL_F, F.EXPORT_TRAPS[G.LEVEL_F]),
        ):
            with self.subTest(level=level):
                self.assertEqual(self._level("X1", row), level)

    def test_a_fused_export_is_not_a_success(self) -> None:
        """Two bodies in, one solid out: a join the plan never asked for,
        carrying exactly the total of the two apart."""
        row = F.EXPORT_TRAPS["C_fused"]
        self.assertEqual(row["solids_read"], 1)
        self.assertAlmostEqual(row["volumes_read"][0],
                               G.CUBE_VOLUME + G.PIN_VOLUME, places=6)
        self.assertEqual(self._level("X1", row), G.LEVEL_C)

    def test_the_top_rung_never_claims_identity_binding(self) -> None:
        """F proves each id reached the file and that the volumes are right.
        Which SOLID carries which NAME is not proven, and the caveat rides
        on every verdict so a recorded run cannot be over-read."""
        verdict = EV.grade(_observation("X1", F.correct_measurement_rows())
                           )["export"]
        self.assertEqual(verdict["level"], G.LEVEL_F)
        self.assertEqual(verdict["identity_state"], G.IDENTITY_UNPROVEN)
        self.assertIn("NOT proven", verdict["identity_note"])

    def test_a_single_body_export_says_it_carries_no_name(self) -> None:
        """MEASURED: `export_step` on a one-body part writes one product
        called `Open CASCADE STEP translator 7.9 1`. So an F there is a
        WEAKER claim than an F on an assembly, and the verdict says which
        rather than leaving it to be inferred from an empty list."""
        truth = G.expected("X3")
        self.assertEqual(truth["export_names"], ())
        self.assertEqual(truth["export_identity"], G.IDENTITY_NOT_WRITTEN)
        row = dict(F.correct_export_row())
        row.update(case="X3", writer="single", asked_for=["cube"],
                   solids_read=1, volumes_read=[G.CUBE_VOLUME],
                   names_found=[], names_missing=["cube"],
                   product_names=["Open CASCADE STEP translator 7.9 1"])
        verdict = EV.grade(_observation("X3", [], export=row))["export"]
        self.assertEqual(verdict["level"], G.LEVEL_F)
        self.assertEqual(verdict["identity_state"], G.IDENTITY_NOT_WRITTEN)
        self.assertIn("no body name", verdict["identity_note"])

    def test_a_case_may_not_expect_names_from_a_writer_that_writes_none(self):
        with self.assertRaises(ValueError):
            G.Case("bad", G.SINGLE, body_ids=("a",), volumes=(1.0,),
                   probes=(), single_body_writer=True)

    def test_the_writer_is_pinned_per_case(self) -> None:
        """Stage 74 kept the single-body path unchanged deliberately. A case
        pins which writer must run, so it cannot quietly start going through
        the other one."""
        row = dict(F.correct_export_row())
        row.update(case="X3", writer="assembly", asked_for=["cube"],
                   solids_read=1, volumes_read=[G.CUBE_VOLUME],
                   names_found=[], names_missing=[])
        verdict = EV.grade(_observation("X3", [], export=row))["export"]
        self.assertFalse(verdict["writer_right"])
        self.assertFalse(verdict["passed"])

    def test_the_weak_substring_result_is_recorded_beside_the_strict_one(self):
        """The gap between the two lists is the product's weakness, visible
        in the data rather than only in prose."""
        row = F.EXPORT_TRAPS["D_substring_fooled"]
        self.assertNotEqual(row["names_found"],
                            row["names_found_by_substring"])


# ------------------------------------------------- what a run may claim


class ADeterministicRunIsNeverAModelResultTests(unittest.TestCase):
    """Everything Stage 76 produces is DETERMINISTIC. The single most
    damaging thing this instrument could do is let one of its own fixture
    runs be read afterwards as evidence about a model."""

    def test_model_generated_requires_model_output(self) -> None:
        with self.assertRaises(ValueError):
            O.observation(case_name="X1", source=G.SOURCE_MODEL_GENERATED,
                          model_output=None, geometry_section={},
                          measurement_section=[], export_section=None)

    def test_a_non_model_source_may_not_carry_model_output(self) -> None:
        for source in (G.SOURCE_DETERMINISTIC, G.SOURCE_FALLBACK,
                       G.SOURCE_REFUSED, G.SOURCE_PROVIDER_ERROR):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    O.observation(case_name="X1", source=source,
                                  model_output={"outcome": "generated"},
                                  geometry_section={}, measurement_section=[],
                                  export_section=None)

    def test_an_unknown_source_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            O.observation(case_name="X1", source="LOOKS_FINE",
                          model_output=None, geometry_section={},
                          measurement_section=[], export_section=None)

    def test_a_deterministic_observation_says_so_on_its_face(self) -> None:
        observed = _observation("X1", F.correct_measurement_rows())
        self.assertIs(observed["is_live_model_result"], False)
        self.assertIsNone(observed["model_output"])
        self.assertIs(EV.grade(observed)["is_live_model_result"], False)

    def test_the_summary_counts_live_results_and_refuses_a_combined_rate(self):
        verdicts = [EV.grade(_observation("X1", F.correct_measurement_rows()))]
        summary = EV.summarise(verdicts)
        self.assertEqual(summary["live_model_results"], 0)
        self.assertIsNone(summary["combined_rate"])
        self.assertIn("measurement", summary)
        self.assertIn("export", summary)
        # Measurement and export are different quantities. Stage 75's
        # `summarise` refuses to pool creation and refusal for the same
        # reason, and a combined number is how an instrument flatters itself.
        for key, value in summary.items():
            if isinstance(value, dict) and "passed" in value:
                self.assertLessEqual(value["passed"], value["of"])



# ------------------------------------------------ against a real kernel
#
# Everything above grades a corrupted dictionary, which proves the GRADER.
# These prove the OBSERVER: that what it records of a real build is what
# the product really said and really wrote. An observer that has never met
# a kernel is a hypothesis, and the brief's gate is that it meet one before
# a live call is spent on it.


def _engines():
    from cad_experimental.cad_backend import BackendUnavailable, resolve_backend
    for name in G.ENGINES:
        try:
            yield resolve_backend(name)
        except BackendUnavailable:
            continue


ENGINES = list(_engines())
ENGINE_NAMES = [engine.name for engine in ENGINES]
RUN76 = _load("run76", STAGE76)


class AgainstARealKernelTests(unittest.TestCase):
    """Six fixture parts, built and measured and exported for real.

    `DETERMINISTIC`: the plans were written by hand and no model was called
    or configured. These numbers say something about the observer and
    nothing whatever about a model.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not ENGINES:
            raise unittest.SkipTest("no CAD backend is available here")
        cls.records = {}
        for engine in ENGINES:
            cls.records[engine.name] = RUN76.run(engine.name)

    def test_at_least_one_engine_actually_ran(self) -> None:
        """Vacuity guard: the whole class skips without an engine, and a
        class that silently ran zero cases would report the same green."""
        self.assertTrue(self.records)
        for name, record in self.records.items():
            self.assertEqual(len(record["verdicts"]), len(G.ACTIVE), name)

    def test_every_case_passes_on_every_available_engine(self) -> None:
        for name, record in self.records.items():
            for verdict in record["verdicts"]:
                with self.subTest(engine=name, case=verdict["case"]):
                    self.assertTrue(verdict["geometry"]["passed"],
                                    verdict["geometry"]["checks"])
                    failed = {probe: row["checks"] for probe, row
                              in verdict["measurement"]["probes"].items()
                              if not row["passed"]}
                    self.assertTrue(verdict["measurement"]["passed"], failed)
                    self.assertEqual(verdict["export"]["level"], G.LEVEL_F)
                    self.assertTrue(verdict["passed"])

    def test_the_multi_body_cases_really_had_several_bodies(self) -> None:
        """Every assertion above would pass on a corpus of one-body parts."""
        for name, record in self.records.items():
            for observation in record["observations"]:
                if observation["case"] not in G.MULTI_CASES:
                    continue
                with self.subTest(engine=name, case=observation["case"]):
                    bodies = observation["geometry"]["bodies"]
                    self.assertGreater(len(bodies), 1)
                    self.assertIsNone(
                        observation["geometry"]["single_live_body"],
                        "a multi-body part has no single live body")
        # And at least one case has THREE, so nothing is written for two.
        self.assertEqual(len(G.expected("X2")["body_ids"]), 3)

    def test_nothing_recorded_here_claims_to_be_a_model_result(self) -> None:
        for name, record in self.records.items():
            self.assertIs(record["is_live_model_result"], False, name)
            self.assertEqual(record["source"], G.SOURCE_DETERMINISTIC)
            self.assertEqual(record["summary"]["live_model_results"], 0)
            for observation in record["observations"]:
                self.assertIsNone(observation["model_output"])

    def test_the_trap_fixtures_are_one_edit_from_a_real_observation(self):
        """A trap built from an imagined observation stops being a test of
        the product the day the product's wording changes. Every graded
        field of the base is compared against a freshly observed X1."""
        record = self.records.get("cadquery")
        if record is None:
            self.skipTest("the fixture base was recorded on CadQuery")
        observed = next(o for o in record["observations"]
                        if o["case"] == "X1")["measurement"]
        by_probe = {row["probe"]: row for row in observed}
        graded = ("outcome", "about", "aggregate", "provenance", "value",
                  "said", "label_is_prefix")
        for base in F.correct_measurement_rows():
            with self.subTest(probe=base["probe"]):
                live = by_probe[base["probe"]]
                for key in graded:
                    self.assertEqual(base[key], live[key],
                                     f"{base['probe']}.{key}")

    def test_the_export_base_matches_a_real_write(self) -> None:
        record = self.records.get("cadquery")
        if record is None:
            self.skipTest("the fixture base was recorded on CadQuery")
        live = next(o for o in record["observations"]
                    if o["case"] == "X1")["export"]
        base = F.correct_export_row()
        for key in ("writer", "asked_for", "wrote_file", "readable",
                    "solids_read", "names_found", "names_missing"):
            self.assertEqual(base[key], live[key], key)
        self.assertEqual(len(base["volumes_read"]), len(live["volumes_read"]))
        # The root product's name is a UUID and changes every write, so the
        # COUNT is what is pinned: two bodies plus the writer's own root.
        self.assertEqual(len(live["product_names"]), 3)
        self.assertIn("cube", live["product_names"])
        self.assertIn("pin", live["product_names"])

    def test_a_step_written_by_one_engine_is_read_by_the_other(self) -> None:
        """A file only one engine can read is not an interchange file."""
        if len(ENGINES) < 2:
            self.skipTest("only one engine is available here")
        for name, record in self.records.items():
            crossed = [row for row in record["cross_reads"]
                       if "unavailable" not in row]
            self.assertTrue(crossed, name)
            for row in crossed:
                with self.subTest(written_by=name, case=row["case"]):
                    truth = G.expected(row["case"])
                    self.assertTrue(row["readable"])
                    self.assertEqual(row["solids_read"],
                                     truth["export_solids"])
                    self.assertTrue(
                        EV._match_multiset(row["volumes_read"],
                                           truth["export_volumes"],
                                           truth["volume_tolerance"]),
                        f"{row['volumes_read']} vs {truth['export_volumes']}")

    def test_the_two_engines_agree_body_for_body(self) -> None:
        if len(ENGINES) < 2:
            self.skipTest("only one engine is available here")
        per_engine = {}
        for name, record in self.records.items():
            per_engine[name] = {
                observation["case"]: {
                    body: round(measured["volume"], 6)
                    for body, measured
                    in observation["geometry"]["bodies"].items()
                }
                for observation in record["observations"]
            }
        first, *rest = list(per_engine)
        for other in rest:
            self.assertEqual(per_engine[first], per_engine[other],
                             f"{first} and {other} disagree per body")


class SingleBodySemanticsAreUnchangedTests(unittest.TestCase):
    """Stage 73 kept the one-body wording byte-for-byte, and
    `test_body_measurement` compares it character for character. This
    instrument must not be the thing that changes it."""

    @classmethod
    def setUpClass(cls) -> None:
        if not ENGINES:
            raise unittest.SkipTest("no CAD backend is available here")

    def test_a_one_body_answer_carries_no_id_prefix(self) -> None:
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan

        engine = ENGINES[0]
        for case in G.SINGLE_CASES:
            with self.subTest(case=case, engine=engine.name):
                plan_dict = F.plan_for(case)
                execution = execute_plan(parse_plan(plan_dict),
                                         part_name="stage76", backend=engine)
                self.assertEqual(len(execution.bodies), 1)
                rows = O.measure(case, plan_dict, execution,
                                 backend_name=engine.name)
                for row in rows:
                    if row["outcome"] != G.ANSWERED:
                        continue
                    self.assertEqual(row["label"], "")
                    self.assertIsNone(row["about"])
                    self.assertFalse(row["aggregate"])
                    self.assertNotIn(": ", row["said"].split(" ")[0])

    def test_the_one_body_answer_is_what_it_was_before_stage_73(self) -> None:
        """The same question asked the pre-Stage-73 way -- with no `bodies`
        argument at all -- must give a character-for-character identical
        answer."""
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan

        engine = ENGINES[0]
        plan_dict = F.plan_for("X3")
        execution = execute_plan(parse_plan(plan_dict), part_name="stage76",
                                 backend=engine)
        part = O.part_measurement(execution)
        self.assertTrue(part, "vacuous without a part-level measurement")
        for probe in G.probes_to_ask("X3"):
            with self.subTest(probe=probe["name"]):
                before = questions.answer(plan_dict, part, engine.name,
                                          probe["text"])
                after = questions.answer(plan_dict, part, engine.name,
                                         probe["text"],
                                         bodies=O.bodies_by_id(execution))
                self.assertIsNotNone(before)
                self.assertEqual(before.text, after.text)
                self.assertEqual(before.provenance, after.provenance)



class TheObserverItselfIsDrivenTests(unittest.TestCase):
    """Three guards that live in the OBSERVER and cannot be reached by
    grading a dictionary.

    Every trap above hands `grade` a corrupted row, which proves the
    GRADER. A mutation sweep found that three observer guards -- how the
    body an answer is about is decided, how a body name is looked for in a
    STEP file, and whether an answer was checked for carrying its body's
    name -- survived every one of them, because nothing drove
    `observe76` itself. Each of these does.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not ENGINES:
            raise unittest.SkipTest("no CAD backend is available here")
        cls.engine = ENGINES[0]

    def _built(self, case: str):
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan
        plan_dict = F.plan_for(case)
        return plan_dict, execute_plan(parse_plan(plan_dict),
                                       part_name="stage76",
                                       backend=self.engine)

    def test_the_body_is_read_from_the_scope_never_from_the_value(self) -> None:
        """X6's two bodies measure the same to the last digit, so an
        observer that inferred the body from the number cannot tell them
        apart even in principle -- it would attribute both answers to
        whichever body it happened to look at first."""
        plan_dict, execution = self._built("X6")
        self.assertEqual(len(execution.bodies), 2)
        volumes = {row["volume"] for row in
                   O.bodies_by_id(execution).values()}
        self.assertEqual(len(volumes), 1,
                         "vacuous unless the two volumes really are equal")
        rows = {row["probe"]: row for row in
                O.measure("X6", plan_dict, execution,
                          backend_name=self.engine.name)}
        self.assertEqual(rows["X6-named-left"]["about"], "left")
        self.assertEqual(rows["X6-named-right"]["about"], "right")

    def test_an_answer_that_omits_its_body_is_recorded_as_omitting_it(self):
        """The check that an answer carries its body's name has to be able
        to say NO, and the product never omits it -- so the only way to
        drive the guard is to hand the observer an answer that does."""
        plan_dict, execution = self._built("X1")
        self.assertGreater(len(execution.bodies), 1)
        original = questions.answer

        def unprefixed(plan, measurement, backend, text, bodies=None):
            found = original(plan, measurement, backend, text, bodies=bodies)
            if found is None:
                return None
            stripped = found.text.split(": ", 1)[-1]
            return questions.Answer(text=stripped,
                                    provenance=found.provenance,
                                    working=found.working,
                                    source=found.source)

        questions.answer = unprefixed
        try:
            rows = O.measure("X1", plan_dict, execution,
                             backend_name=self.engine.name)
        finally:
            questions.answer = original
        labelled = [row for row in rows if row["label"]]
        self.assertTrue(labelled, "vacuous without a scoped answer")
        for row in labelled:
            self.assertIs(row["label_is_prefix"], False, row["probe"])

    def test_a_body_name_is_looked_for_as_a_product_not_a_substring(self):
        """`SOLID` occurs in every STEP file's boilerplate
        (`MANIFOLD_SOLID_BREP`). A body of that name is therefore "found"
        by the substring scan `cad_backend.verify_assembly` performs, in a
        file that names no body at all -- and this proves the observer does
        not do the same."""
        _, execution = self._built("X3")
        only = execution.part
        self.assertIsNotNone(only)
        renamed = _RelabelledExecution(execution, "SOLID")
        with tempfile.TemporaryDirectory() as folder:
            row = O.export("X3", renamed, self.engine,
                           pathlib.Path(folder), filename="boilerplate.step")
            self.assertTrue(row["wrote_file"])
            self.assertEqual(row["writer"], "single")
            self.assertEqual(row["names_found_by_substring"], ["SOLID"],
                             "vacuous unless the substring scan is fooled")
            self.assertEqual(
                row["names_found"], [],
                "no PRODUCT carries that name, so the observer must not "
                "report the body as present")
            self.assertEqual(row["names_missing"], ["SOLID"])

    def test_the_boilerplate_words_are_not_product_names(self) -> None:
        """MEASURED on real two-body files from both engines:
        `verify_assembly` ACCEPTS `SOLID`, `part`, `Open` and `cub` as body
        names. None of them is a PRODUCT."""
        _, execution = self._built("X1")
        with tempfile.TemporaryDirectory() as folder:
            O.export("X1", execution, self.engine, pathlib.Path(folder),
                     filename="names.step")
            text = (pathlib.Path(folder) / "names.step").read_text(
                errors="ignore")
        products = O.step_product_names(text)
        self.assertIn("cube", products)
        self.assertIn("pin", products)
        for bogus in ("SOLID", "Open", "cub"):
            with self.subTest(bogus=bogus):
                self.assertIn(bogus, text,
                              "vacuous unless the substring really is there")
                self.assertNotIn(bogus, products)


class _RelabelledExecution:
    """One real body under a different id. Reached through `part`, never by
    indexing the body tuple."""

    def __init__(self, execution, new_id):
        only = execution.part
        if only is None:
            raise ValueError("this adapter is for a single-body execution")
        volume = O.bodies_by_id(execution)[only]["volume"]
        self.bodies = (_FakeBody(new_id, volume),)
        self.declared = ()
        self.backend = execution.backend
        self.failure = None
        self.succeeded = True
        self.shapes = {new_id: execution.shapes[only]}
        self._part = new_id

    @property
    def part(self):
        return self._part



# --------------------------------------------- the route a person uses
#
# Everything above reaches `questions.answer` and the backend's exporter
# directly, which is the right level for proving the instrument and is not
# the level a person uses. Between the two sit the HTTP routes, the session
# and a payload that carries three different per-body shapes at once -- and
# the last `bodies[0]` of the whole multi-body slice was found in the
# BROWSER, a stage after the server had been fixed.
#
# This drives case X7 through the real application, with NO planner
# configured, so the deterministic reader answers exactly as it does when a
# person types the sentence with no model available. Real Chromium stays
# `npm run e2e:surfaces`'s job; what this adds is that the payload a browser
# receives is graded by the SAME truth and the SAME grader as everything
# else, instead of by regexes in a script.


class ThroughTheRealRoutesTests(unittest.TestCase):
    """The product path: session -> deterministic reader -> plan -> executor
    -> questions -> export, graded by `evaluate76`."""

    CASE = "X7"

    @classmethod
    def setUpClass(cls) -> None:
        if not ENGINES:
            raise unittest.SkipTest("no CAD backend is available here")
        try:
            from fastapi.testclient import TestClient
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"fastapi is not installed here: {exc}")
        from cad_core.application_service import CadApplicationService

        from cad_experimental.app import create_app
        from cad_experimental.config import ExperimentalConfig

        cls.B = _load("browser76", STAGE76)
        service = CadApplicationService.local(tempfile.mkdtemp())
        # NO planner: the deterministic reader answers, exactly as it does
        # for a person with no model configured. Nothing here is evidence
        # about a model and the observation says so on its face.
        cls.client = TestClient(create_app(config=ExperimentalConfig(),
                                           service=service))
        cls.session = "stage76"
        cls.built = cls._say(F.X7_REQUEST)
        cls.replies = {probe["name"]: cls._say(probe["text"])
                       for probe in G.probes_to_ask(cls.CASE)}
        exported = cls.client.post(
            "/experimental/session/export",
            json={"session_id": cls.session, "format": "step"})
        cls.export_status = exported.status_code
        cls.export_text = exported.text
        cls.export_headers = dict(exported.headers)

    @classmethod
    def _say(cls, text: str):
        response = cls.client.post(
            "/experimental/session/message",
            json={"session_id": cls.session, "text": text})
        return response.json()

    def _observation(self):
        geometry = self.B.geometry_from_payload(self.built)
        rows = self.B.probe_rows(self.CASE, self.replies,
                                 geometry["body_ids"])
        export = self.B.export_from_bytes(
            self.CASE, self.export_text,
            asked_for=list(geometry["body_ids"]),
            status=self.export_status, headers=self.export_headers,
            writer="assembly")
        return O.observation(
            case_name=self.CASE, source=G.SOURCE_DETERMINISTIC,
            model_output=None, geometry_section=geometry,
            measurement_section=rows, export_section=export,
            note="driven through the real HTTP routes with no planner "
                 "configured; the deterministic reader answered")

    def test_the_part_really_built_with_two_bodies(self) -> None:
        self.assertEqual(self.built.get("status"), "built",
                         self.built.get("reply"))
        self.assertEqual(len(self.built.get("bodies") or ()), 2)
        self.assertEqual(self.built.get("measurement"), {},
                         "a multi-body part has NO part-level measurement, "
                         "and the browser once showed the first body's "
                         "numbers under a heading that said measurements")
        self.assertIsNone(self.built.get("render"),
                          "there is no single mesh for a multi-body part")

    def test_nothing_on_this_route_came_from_a_model(self) -> None:
        source = (self.built.get("interpreted_by") or {}).get("source")
        self.assertEqual(source, "deterministic")
        self.assertIs(self._observation()["is_live_model_result"], False)

    def test_the_route_grades_against_the_same_truth(self) -> None:
        verdict = EV.grade(self._observation())
        self.assertTrue(verdict["geometry"]["passed"],
                        verdict["geometry"]["checks"])
        failed = {name: row["checks"] for name, row
                  in verdict["measurement"]["probes"].items()
                  if not row["passed"]}
        self.assertTrue(verdict["measurement"]["passed"], failed)

    def test_an_ambiguous_question_is_refused_to_the_person(self) -> None:
        """The step that cannot be checked in a unit test: that a refusal
        arrives as an ANSWER, in the product, rather than as a guess."""
        reply = self.replies["X7-unnamed"]
        self.assertEqual(reply.get("status"), "refused")
        said = str(reply.get("reply") or "")
        self.assertIn("cube", said)
        self.assertIn("cylinder", said)
        self.assertNotIn("to change", said,
                         "someone who asked a question is being told to "
                         "name what to CHANGE")

    def test_a_browser_export_stops_at_the_names_rung_and_says_why(self):
        """A browser response is a string of bytes. It carries the solids
        and the names and nothing that measures, so the top rung is not
        FAILED there, it is not ASSESSED -- and the verdict says which."""
        verdict = EV.grade(self._observation())["export"]
        self.assertEqual(verdict["level"], G.LEVEL_D)
        self.assertFalse(verdict["geometry_assessed"])
        self.assertIn("carry solids and names",
                      verdict["why_geometry_not_assessed"])

    def test_re_reading_those_same_bytes_with_a_kernel_reaches_the_top(self):
        """The honest way past it: the artefact under test is still the one
        the person downloaded; only the instrument reading it is a kernel."""
        observation = self._observation()
        with tempfile.TemporaryDirectory() as folder:
            observation["export"] = self.B.measure_bytes_with(
                ENGINES[0], self.export_text, observation["export"], folder)
        verdict = EV.grade(observation)["export"]
        self.assertEqual(observation["export"]["volumes_measured_by"],
                         ENGINES[0].name,
                         "which engine measured is a different provenance "
                         "from the rest of the row and must be recorded")
        self.assertTrue(verdict["geometry_assessed"])
        self.assertEqual(verdict["level"], G.LEVEL_F)
        self.assertTrue(verdict["passed"])

    def test_the_body_ids_reached_the_file_as_products(self) -> None:
        export = self._observation()["export"]
        self.assertEqual(export["solids_read"], 2,
                         "counted the way `e2e:surfaces` counts them")
        self.assertEqual(sorted(export["names_found"]), ["cube", "cylinder"])
        self.assertEqual(export["names_missing"], [])

    def test_the_export_header_is_recorded_and_never_consulted(self) -> None:
        """`x-cad-bodies` is built from the executor's body list BEFORE the
        file is read back, so it says what was asked for and never what
        landed. Checking it would be asking the writer whether the writer
        succeeded."""
        export = self._observation()["export"]
        self.assertEqual(export["header_x_cad_bodies"], "cube, cylinder")
        source = (STAGE76 / "evaluate76.py").read_text(encoding="utf-8")
        self.assertNotIn("x-cad-bodies", source)
        self.assertNotIn("header_x_cad_bodies", source)
        # And the names really do come from the FILE: a header that claims
        # a body the bytes do not contain must not put it in `names_found`.
        lying = self.B.export_from_bytes(
            self.CASE, self.export_text,
            asked_for=["cube", "cylinder", "flange"],
            status=200,
            headers={"x-cad-bodies": "cube, cylinder, flange",
                     "x-cad-backend": ENGINES[0].name},
            writer="assembly")
        self.assertEqual(sorted(lying["names_found"]), ["cube", "cylinder"])
        self.assertEqual(lying["names_missing"], ["flange"],
                         "a body the header claims and the file does not "
                         "carry is MISSING, whatever the header says")

    def test_a_prefix_that_is_not_a_body_never_becomes_one(self) -> None:
        """The label vocabulary is CLOSED. Treating whatever precedes the
        first colon as a body id would let a reworded answer invent one,
        and an observation that invents a body is worse than one that finds
        none -- it would report a part with a body nobody built."""
        row = self.B.measurement_row(
            {"name": "probe", "kind": G.NAMED, "text": "what?"},
            {"status": "answered", "reply": "flange: Volume 64000.000 mm3.",
             "evidence": {"provenance": G.MEASURED}},
            ("cube", "cylinder"))
        self.assertEqual(row["outcome"], G.ANSWERED)
        self.assertIsNone(row["about"], "`flange` is not a body of this part")
        self.assertEqual(row["label"], "")
        self.assertIsNone(row["label_is_prefix"])

    def test_the_browser_module_re_derives_no_product_decision(self) -> None:
        """Which body a question is about is decided by the server, once.
        This module reads the answer; it must not import a reader, a
        resolver, a validator or a parser and work it out again."""
        tree = ast.parse((STAGE76 / "browser76.py").read_text(
            encoding="utf-8"))
        for name in _imported_names(tree):
            self.assertNotIn(name, {"normalize", "body_reference", "questions",
                                    "validation", "parser", "executor",
                                    "cad_experimental", "intent"})

    def test_the_three_per_body_shapes_agree(self) -> None:
        """The payload carries the bodies three times: `bodies` (keyed
        `body_id`), `execution.bodies` (keyed `id`, no declared flag) and
        `session.current.bodies` (a plain id -> measurement map). An
        observer that read one and a surface that read another is how the
        browser came to show a two-body part's first body as the part."""
        geometry = self.B.geometry_from_payload(self.built)
        self.assertEqual(sorted(geometry["body_ids"]),
                         sorted(geometry["execution_body_ids"]))
        self.assertEqual(sorted(geometry["body_ids"]),
                         sorted(geometry["session_body_ids"]))
        self.assertEqual(len(geometry["body_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
