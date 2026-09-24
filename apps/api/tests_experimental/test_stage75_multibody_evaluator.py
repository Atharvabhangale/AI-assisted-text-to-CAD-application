"""Stage 75's multi-body instrument: immutable truth, and a grader that bites.

Two separate properties are guarded here, and they fail for different
reasons.

**The truth cannot come from the output.** This is Stage 67's defect, which
Stage 68 built its ground truth to make structurally impossible and which
this stage inherits verbatim: a criterion that grades a part against its own
answer cannot fail it. So `expected()` takes a case NAME and nothing else,
the evaluator holds no expectation of its own, and the corpus module imports
nothing from the code under test.

**The grader must actually bite.** A guard that no wrong answer can trip is
worse than no guard, because it reports a rate. So the mutation tests below
each take an observation that IS a strict success, change exactly one thing,
and require the grade to flip -- and to flip with the right failure code. A
test that asserts a mutated case fails is only meaningful if the unmutated
case passes, so :meth:`MutationTests.test_the_baselines_are_strict_successes`
runs first in spirit: without it every mutation test is vacuous.

The sharpest of them is
:meth:`MutationTests.test_two_bodies_fused_into_one_is_not_a_pass`. Two
disjoint solids fused have EXACTLY the total volume of the two apart, so a
grader that looks at total volume scores the single most important
multi-body failure as a success. The Stage 75 brief names this one
explicitly; this is the test that says it does not happen here.

Stage 68's arena is deliberately not reused. It reads
``execution.bodies[0].measurement``, which on a two-body part grades the part
by its first body -- so reusing it would manufacture passes. The AST tests in
:class:`StructureTests` pin that separation rather than trusting a comment.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import math
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
STAGE75 = REPO / "docs" / "evaluation-baselines" / "stage75-multibody"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, STAGE75 / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


G = _load("ground_truth75")
E = _load("evaluate75")
A = _load("arena75")


# ------------------------------------------------------------- stand-ins
#
# Deliberately plain objects rather than the real generation/execution types:
# these tests are about the GRADER, and building a kernel solid to test a
# comparison would measure the kernel instead.


class Measurement:
    def __init__(self, volume, *, face_count=6, edge_count=12,
                 minimum=(0.0, 0.0, 0.0), maximum=(40.0, 40.0, 40.0),
                 solid_count=1, is_valid=True):
        self.volume = volume
        self.face_count = face_count
        self.edge_count = edge_count
        self.minimum = minimum
        self.maximum = maximum
        self.solid_count = solid_count
        self.is_valid = is_valid


class Body:
    def __init__(self, ident, measurement, features=()):
        self.id = ident
        self.measurement = measurement
        self.features = tuple(features)


class Execution:
    def __init__(self, bodies, succeeded=True):
        self.bodies = list(bodies)
        self.succeeded = succeeded
        self.failure = None


class Operation:
    def __init__(self, ident, type_, target=None):
        self.id = ident
        self.TYPE = type_
        if target is not None:
            self.target = target


class Plan:
    def __init__(self, operations):
        self.operations = list(operations)


class Validation:
    def __init__(self, valid=True, problems=()):
        self.valid = valid
        self.problems = tuple(problems)


class Outcome:
    def __init__(self, value):
        self.value = value


class Metadata:
    structured_output = True


class Generation:
    def __init__(self, operations=(), outcome="generated", valid=True,
                 questions=(), error=None):
        self.plan = Plan(operations)
        self.plan_validation = Validation(valid)
        self.outcome = Outcome(outcome)
        self.metadata = Metadata()
        self.questions = tuple(questions)
        self.error = error


def observe(case, generation, execution):
    return E.observe(
        case_name=case,
        generation=generation,
        execution=execution,
        schema_fingerprint="ef7427700af93ed7",
        schema_name="strict_selector_union_part",
        prompt_version="2026-09-24.1",
        prompt_fingerprint="c0c4a1be0d23052f",
    )


# --------------------------------------------------------- correct answers
#
# One helper per case, each producing what the request actually asks for.
# The mutation tests start from these, so they are the thing that makes the
# mutations meaningful -- and they are asserted to pass first.

CUBE_BOX = dict(face_count=G.BOX_FACES, minimum=(0.0, 0.0, 0.0),
                maximum=(40.0, 40.0, 40.0))
PIN_BOX = dict(face_count=G.CYLINDER_FACES, minimum=(50.0, 0.0, 0.0),
               maximum=(70.0, 20.0, 30.0))


def two_body_generation(ids=("solid1", "solid2")):
    a, b = ids
    return Generation(operations=[
        Operation(a, "box"),
        Operation(b, "cylinder"),
        Operation("d1", "part", a),
        Operation("d2", "part", b),
    ])


def m1_correct():
    return observe("M1", two_body_generation(), Execution([
        Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
        Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
    ]))


def m2_correct():
    return observe("M2", two_body_generation(("cube", "pin")), Execution([
        Body("cube", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
        Body("pin", Measurement(G.PIN_VOLUME, **PIN_BOX)),
    ]))


def m3_correct():
    return observe("M3", two_body_generation(("big", "small")), Execution([
        Body("big", Measurement(40.0 ** 3, face_count=G.BOX_FACES,
                                minimum=(0.0, 0.0, 0.0),
                                maximum=(40.0, 40.0, 40.0))),
        Body("small", Measurement(20.0 ** 3, face_count=G.BOX_FACES,
                                  minimum=(50.0, 0.0, 0.0),
                                  maximum=(70.0, 20.0, 20.0))),
    ]))


def m4_correct():
    return observe("M4", two_body_generation(), Execution([
        Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
        Body("solid2", Measurement(G.PIN_VOLUME_EDITED,
                                   face_count=G.CYLINDER_FACES,
                                   minimum=(50.0, 0.0, 0.0),
                                   maximum=(70.0, 20.0, 40.0))),
    ]))


def m5_correct():
    return observe("M5", two_body_generation(), Execution([
        Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
        Body("solid2", Measurement(1000.0, face_count=G.CYLINDER_FACES + 1,
                                   minimum=(50.0, 0.0, 0.0),
                                   maximum=(70.0, 20.0, 30.0))),
    ]))


def m8_correct():
    generation = Generation(operations=[
        Operation("cube", "box"),
        Operation("pin", "cylinder"),
        Operation("fuse", "union", "cube"),
    ])
    return observe("M8", generation, Execution([
        Body("fuse", Measurement(G.FUSED_VOLUME, face_count=8,
                                 minimum=(0.0, 0.0, 0.0),
                                 maximum=(70.0, 40.0, 40.0))),
    ]))


def refusal_correct(case):
    generation = Generation(
        operations=[],
        outcome="needs_clarification",
        questions=("Two bodies stand: block and rod. Which do you mean?",),
    )
    return observe(case, generation, None)


CORRECT = {
    "M1": m1_correct, "M2": m2_correct, "M3": m3_correct, "M4": m4_correct,
    "M5": m5_correct, "M8": m8_correct,
    "M6": lambda: refusal_correct("M6"),
    "M7": lambda: refusal_correct("M7"),
}


def scored(observation):
    graded = E.grade(observation)
    return graded, E.classify(observation, graded)


# ------------------------------------------------------------ the corpus


class CorpusTests(unittest.TestCase):
    """The corpus is a fixed instrument and says what it was built to say."""

    def test_the_corpus_is_eight_cases_split_six_and_two(self) -> None:
        self.assertEqual(len(G.CASES), 8)
        self.assertEqual(G.CREATION_CASES, ("M1", "M2", "M3", "M4", "M5", "M8"))
        self.assertEqual(G.REFUSAL_CASES, ("M6", "M7"))

    def test_every_request_text_is_pinned_verbatim(self) -> None:
        """Stage 68's finding was that the REQUEST is a variable of the
        experiment. Changing one of these makes a new case with a new name;
        it never edits an existing one, because a rate measured on one
        sentence is not a rate measured on another.
        """
        self.assertEqual(
            G.CASES_BY_NAME["M1"].text,
            "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long "
            "beside it as two separate bodies.",
        )
        self.assertEqual(G.CASES_BY_NAME["M6"].text, "Make the body 10 mm taller.")
        self.assertEqual(
            G.CASES_BY_NAME["M7"].text, "Make the bracket 10 mm taller."
        )
        for case in G.CASES:
            self.assertTrue(case.text.strip(), f"{case.name} has no request")

    def test_the_volumes_are_closed_form_and_recomputed_here(self) -> None:
        """Recomputed independently rather than read back, so a typo in the
        corpus is a failure rather than a shared assumption."""
        self.assertAlmostEqual(G.CUBE_VOLUME, 64000.0, places=9)
        self.assertAlmostEqual(G.PIN_VOLUME, math.pi * 100.0 * 30.0, places=9)
        self.assertAlmostEqual(
            G.PIN_VOLUME_EDITED, math.pi * 100.0 * 40.0, places=9)
        # Disjoint solids: a union adds nothing and removes nothing.
        self.assertAlmostEqual(
            G.FUSED_VOLUME, G.CUBE_VOLUME + G.PIN_VOLUME, places=9)

    def test_only_the_case_that_states_ids_pins_them(self) -> None:
        """A corpus that demanded `cube` where the request never said `cube`
        would be scoring vocabulary, not capability."""
        self.assertEqual(G.CASES_BY_NAME["M2"].body_ids, ("cube", "pin"))
        for name in ("M1", "M3", "M4", "M5", "M8"):
            self.assertIsNone(G.CASES_BY_NAME[name].body_ids,
                              f"{name} pins an id its request never states")

    def test_the_declaration_is_required_exactly_where_bodies_remain(self) -> None:
        for case in G.CASES:
            if case.group == G.REFUSAL:
                self.assertFalse(case.declaration_required)
                continue
            self.assertEqual(case.declaration_required, case.bodies > 1,
                             f"{case.name}")

    def test_the_refusal_fixture_is_deterministic_and_two_bodied(self) -> None:
        """If the setup were model-generated, a setup failure would be
        recorded as a refusal failure and the run would measure two things."""
        types = [op["type"] for op in G.REFUSAL_FIXTURE["operations"]]
        self.assertEqual(types.count("part"), 2)
        declared = [op["target"] for op in G.REFUSAL_FIXTURE["operations"]
                    if op["type"] == "part"]
        self.assertEqual(tuple(declared), G.FIXTURE_BODIES)

    def test_only_model_generated_may_count_as_success(self) -> None:
        """Every multi-body number recorded before this stage is
        deterministic, and says nothing about a model."""
        self.assertEqual(G.COUNTS_AS_SUCCESS, (G.MODEL_GENERATED,))
        self.assertIn(G.DETERMINISTIC, G.OUTCOMES)
        self.assertNotIn(G.DETERMINISTIC, G.COUNTS_AS_SUCCESS)
        self.assertNotIn(G.FALLBACK, G.COUNTS_AS_SUCCESS)

    def test_the_tolerance_is_relative_and_never_equality(self) -> None:
        self.assertGreater(G.VOLUME_TOLERANCE, 0.0)
        self.assertTrue(E._close(G.CUBE_VOLUME * (1 + 1e-12), G.CUBE_VOLUME))
        self.assertFalse(E._close(G.CUBE_VOLUME * 1.01, G.CUBE_VOLUME))


# ---------------------------------------------------------- the structure


class StructureTests(unittest.TestCase):
    """Properties of the code, checked by AST rather than by substring.

    A substring search over these modules reports the opposite of the truth,
    because both docstrings quote the thing they forbid -- ``bodies[0]`` and
    the arena's name appear in prose explaining why they are absent. That
    already produced one wrong reading during this stage, so the checks parse.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.truth_tree = ast.parse(
            (STAGE75 / "ground_truth75.py").read_text(encoding="utf-8"))
        cls.eval_tree = ast.parse(
            (STAGE75 / "evaluate75.py").read_text(encoding="utf-8"))

    @staticmethod
    def _imports(tree):
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_expected_takes_a_case_name_and_nothing_else(self) -> None:
        """The structural guarantee. It is not possible to hand this
        function anything a model produced, so the truth cannot drift."""
        parameters = list(inspect.signature(G.expected).parameters)
        self.assertEqual(parameters, ["case_name"])

    def test_grade_takes_one_observation_and_fetches_its_own_truth(self) -> None:
        parameters = list(inspect.signature(E.grade).parameters)
        self.assertEqual(parameters, ["observation"])
        source = inspect.getsource(E.grade)
        self.assertIn("G.expected(observation[\"case\"])", source)

    def test_the_ground_truth_imports_nothing_from_the_code_under_test(self) -> None:
        self.assertEqual(self._imports(self.truth_tree) - {"__future__"},
                         {"math", "typing"})

    def test_the_evaluator_imports_only_the_truth_and_the_stdlib(self) -> None:
        self.assertEqual(self._imports(self.eval_tree) - {"__future__"},
                         {"math", "typing", "ground_truth75"})

    def test_neither_module_reuses_the_one_body_arena(self) -> None:
        """Stage 68's arena reads ``bodies[0]``. On a two-body part that
        grades the part by its first body and reports a pass -- so reusing
        it here would manufacture successes."""
        for tree in (self.truth_tree, self.eval_tree):
            for name in self._imports(tree):
                self.assertNotIn(
                    name, {"arena", "harness", "stage48_capability_evaluation",
                           "cad_experimental"})

    def test_the_evaluator_never_indexes_a_body_by_position(self) -> None:
        """Order is not meaning. Which body the model declared first says
        nothing about the part, so no constant index into a body list may
        appear anywhere in the grader."""
        for node in ast.walk(self.eval_tree):
            if not isinstance(node, ast.Subscript):
                continue
            value = node.value
            name = getattr(value, "id", None) or getattr(value, "attr", None)
            if name not in ("bodies", "volumes", "measured", "truth_volumes"):
                continue
            index = node.slice
            self.assertNotIsInstance(
                index, ast.Constant,
                f"{name}[{getattr(index, 'value', '?')}] at line {node.lineno}",
            )


# ----------------------------------------------------------- the mutations


class ArenaTests(unittest.TestCase):
    """The driver: it cannot start a run by itself, and it knows what the
    corpus was built against.

    Nothing here calls a model. `--check` is offline by construction and the
    `--live` gate is asserted rather than assumed, because a credential's
    mere presence starting a paid run has already happened once in this
    project's history.
    """

    def test_it_refuses_to_run_without_the_live_flag(self) -> None:
        self.assertEqual(A.main(["--calls", "1"]), 2)

    def test_check_is_offline_and_reports_a_matching_identity(self) -> None:
        self.assertEqual(A.main(["--check"]), 0)

    def test_the_committed_identity_is_the_live_route(self) -> None:
        """The arena's own record of what the corpus was built against must
        be the route that will answer it. A run under a different prompt or
        grammar is a different number, so the arena refuses rather than
        recording one that looks like the others."""
        self.assertEqual(A.check_identity(), [])
        from cad_experimental import generation
        from cad_experimental.prompt import PROMPT_VERSION, prompt_fingerprint
        self.assertEqual(A.COMMITTED["schema_name"], generation.PLAN_SCHEMA_NAME)
        self.assertEqual(A.COMMITTED["schema_inlined"],
                         generation.PLAN_SCHEMA_INLINED)
        self.assertEqual(A.COMMITTED["schema_fingerprint"],
                         generation.PLAN_SCHEMA_FINGERPRINT)
        self.assertEqual(A.COMMITTED["prompt_version"], PROMPT_VERSION)
        self.assertEqual(A.COMMITTED["prompt_fingerprint"], prompt_fingerprint())

    def test_a_drifted_identity_stops_a_run_before_anything_is_asked(self) -> None:
        """Checked on the SOURCE, not by calling `run`.

        Calling `run` to prove it stops would, on a machine that has a
        credential and a guard that had been removed, spend money to show
        that it does not. So this asserts the shape instead: the drift check
        is the first statement in the function, before the provider is even
        imported.
        """
        original = dict(A.COMMITTED)
        A.COMMITTED["prompt_fingerprint"] = "0" * 64
        try:
            self.assertTrue(A.check_identity())
        finally:
            A.COMMITTED.clear()
            A.COMMITTED.update(original)
        self.assertEqual(A.check_identity(), [])

        tree = ast.parse((STAGE75 / "arena75.py").read_text(encoding="utf-8"))
        run = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "run")
        first = run.body[0]
        self.assertIsInstance(first, ast.Assign)
        self.assertEqual(getattr(first.value.func, "id", None), "check_identity")
        raises = run.body[1]
        self.assertIsInstance(raises, ast.If)
        self.assertIsInstance(raises.body[0], ast.Raise)

    def test_it_holds_no_expectation_of_its_own(self) -> None:
        """Every number the arena reports comes from the ground truth or the
        evaluator. An expectation here would be a second source of truth."""
        tree = ast.parse((STAGE75 / "arena75.py").read_text(encoding="utf-8"))
        floats = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]
        self.assertEqual(floats, [], f"arena75 states its own numbers: {floats}")

    def test_it_will_not_write_into_an_earlier_baseline(self) -> None:
        self.assertIn("stage68-benchmark-disambiguation", A.PROTECTED)
        self.assertIn("stage69-bore-axis-centre", A.PROTECTED)
        self.assertNotIn("stage75-multibody", A.PROTECTED)


class MutationTests(unittest.TestCase):
    """Each takes a correct answer, changes one thing, and requires a fail.

    Without the first test these are all vacuous, so it is not optional and
    it is not folded into the others.
    """

    def test_the_baselines_are_strict_successes(self) -> None:
        for name, build in CORRECT.items():
            graded, codes = scored(build())
            self.assertTrue(
                graded["strict_success"],
                f"{name} baseline does not pass, so every mutation test on "
                f"it is vacuous: {graded['checks']}",
            )
            self.assertEqual(codes, ())

    # --- the volume trap --------------------------------------------------

    def test_two_bodies_fused_into_one_is_not_a_pass(self) -> None:
        """The failure the whole instrument exists for.

        Two disjoint solids fused have EXACTLY the total volume of the two
        apart. A grader that reads total volume scores this -- the single
        most important multi-body failure -- as a success.
        """
        generation = Generation(operations=[
            Operation("cube", "box"),
            Operation("pin", "cylinder"),
            Operation("fuse", "union", "cube"),
        ])
        fused = observe("M1", generation, Execution([
            Body("fuse", Measurement(G.CUBE_VOLUME + G.PIN_VOLUME,
                                     minimum=(0.0, 0.0, 0.0),
                                     maximum=(70.0, 40.0, 40.0))),
        ]))
        graded, codes = scored(fused)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.H_UNWANTED_FUSION, codes)
        # The COUNT is checked in its own right. Asserted separately because
        # the declaration check happens to catch this case too, and a mutant
        # that stopped counting bodies survived every other test here until
        # this line was added.
        self.assertIs(graded["checks"]["body_count"], False)
        # and the total volume really is right, which is what makes it a trap
        self.assertAlmostEqual(
            sum(b["volume"] for b in fused["bodies"]),
            sum(G.expected("M1")["volumes"]),
            places=6,
        )

    # --- counting ---------------------------------------------------------

    def test_a_missing_body_fails_as_a_missing_body(self) -> None:
        one = observe("M1", Generation(operations=[
            Operation("solid1", "box"),
        ]), Execution([Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX))]))
        graded, codes = scored(one)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.A_MISSING_BODY, codes)
        self.assertNotIn(G.H_UNWANTED_FUSION, codes)

    def test_a_third_body_fails_as_an_extra_body(self) -> None:
        generation = two_body_generation()
        generation.plan.operations.append(Operation("d3", "part", "solid3"))
        three = observe("M1", generation, Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
            Body("solid3", Measurement(1.0, minimum=(200.0, 0.0, 0.0),
                                       maximum=(201.0, 1.0, 1.0))),
        ]))
        graded, codes = scored(three)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.B_EXTRA_BODY, codes)
        self.assertIs(graded["checks"]["body_count"], False)

    # --- the declaration --------------------------------------------------

    def test_two_bodies_with_no_declaration_is_not_a_pass(self) -> None:
        """P34: more than one body standing requires a `part` declaration.
        The geometry here is perfect and the plan is still not legal."""
        undeclared = observe("M1", Generation(operations=[
            Operation("solid1", "box"), Operation("solid2", "cylinder"),
        ]), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
        ]))
        graded, _ = scored(undeclared)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["declared_every_body"], False)

    def test_declaring_a_body_twice_is_caught(self) -> None:
        generation = two_body_generation()
        generation.plan.operations[-1] = Operation("d2", "part", "solid1")
        duplicated = observe("M1", generation, Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
        ]))
        graded, codes = scored(duplicated)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.I_DUPLICATE_DECLARATION, codes)

    def test_declaring_a_body_on_the_one_body_control_is_caught(self) -> None:
        """M8 exists to catch a model that has learned to declare bodies
        indiscriminately -- the predictable way a multi-body prompt goes
        wrong, and invisible to any check that only counts bodies."""
        generation = Generation(operations=[
            Operation("cube", "box"), Operation("pin", "cylinder"),
            Operation("fuse", "union", "cube"),
            Operation("d1", "part", "fuse"),
        ])
        over = observe("M8", generation, Execution([
            Body("fuse", Measurement(G.FUSED_VOLUME, face_count=8,
                                     minimum=(0.0, 0.0, 0.0),
                                     maximum=(70.0, 40.0, 40.0))),
        ]))
        graded, codes = scored(over)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.B_EXTRA_BODY, codes)

    # --- identity, placement, dimensions ----------------------------------

    def test_the_named_ids_are_required_only_where_named(self) -> None:
        wrong = observe("M2", two_body_generation(("body1", "body2")),
                        Execution([
                            Body("body1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
                            Body("body2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
                        ]))
        graded, codes = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.C_WRONG_IDENTITY, codes)
        # the same ids on M1, whose request names none, are fine
        free = observe("M1", two_body_generation(("body1", "body2")),
                       Execution([
                           Body("body1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
                           Body("body2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
                       ]))
        self.assertTrue(E.grade(free)["strict_success"])

    def test_bodies_sitting_inside_each_other_fail_placement(self) -> None:
        """"beside it" is part of the request, so two right-sized bodies in
        the same place are still the wrong part."""
        overlapping = observe("M1", two_body_generation(), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME,
                                       face_count=G.CYLINDER_FACES,
                                       minimum=(10.0, 10.0, 0.0),
                                       maximum=(30.0, 30.0, 30.0))),
        ]))
        graded, codes = scored(overlapping)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.F_WRONG_PLACEMENT, codes)

    def test_touching_faces_are_not_an_overlap(self) -> None:
        """Disjointness is checked on bounding boxes and used for nothing
        finer, so a shared face must not read as interference."""
        touching = observe("M1", two_body_generation(), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME,
                                       face_count=G.CYLINDER_FACES,
                                       minimum=(40.0, 0.0, 0.0),
                                       maximum=(60.0, 20.0, 30.0))),
        ]))
        self.assertTrue(E.grade(touching)["strict_success"])

    def test_a_wrong_size_fails_even_with_the_right_body_count(self) -> None:
        wrong = observe("M1", two_body_generation(), Execution([
            Body("solid1", Measurement(30.0 ** 3, face_count=G.BOX_FACES,
                                       minimum=(0.0, 0.0, 0.0),
                                       maximum=(30.0, 30.0, 30.0))),
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
        ]))
        graded, codes = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.G_WRONG_DIMENSIONS, codes)

    def test_the_right_total_split_between_the_wrong_bodies_fails(self) -> None:
        """The volume trap again, one level down.

        Both bodies exist, both are declared, they are disjoint and the
        volumes SUM to exactly the right total -- and the part is wrong,
        because the cube is too small and the pin too large by the same
        amount. Only a per-body comparison can see this; a grader that adds
        the volumes up cannot, and a mutant that does exactly that survived
        every other test in this class until this one was written.
        """
        shifted = 5000.0
        wrong = observe("M1", two_body_generation(), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME - shifted, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME + shifted, **PIN_BOX)),
        ]))
        graded, codes = scored(wrong)
        self.assertAlmostEqual(
            sum(b["volume"] for b in wrong["bodies"]),
            sum(G.expected("M1")["volumes"]),
            places=6,
        )
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["volumes"], False)
        self.assertIn(G.G_WRONG_DIMENSIONS, codes)

    def test_the_stated_extents_are_required_on_the_underspecified_case(self) -> None:
        """M3 states one extent per box and nothing else. Those two numbers
        are ground truth; everything else is the model's to choose."""
        wrong = observe("M3", two_body_generation(("big", "small")), Execution([
            Body("big", Measurement(25.0 ** 3, face_count=G.BOX_FACES,
                                    minimum=(0.0, 0.0, 0.0),
                                    maximum=(25.0, 25.0, 25.0))),
            Body("small", Measurement(15.0 ** 3, face_count=G.BOX_FACES,
                                      minimum=(50.0, 0.0, 0.0),
                                      maximum=(65.0, 15.0, 15.0))),
        ]))
        graded, codes = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.G_WRONG_DIMENSIONS, codes)

    def test_a_cylinder_where_a_box_was_asked_for_fails(self) -> None:
        wrong = observe("M3", two_body_generation(("big", "small")), Execution([
            Body("big", Measurement(40.0 ** 3, face_count=G.BOX_FACES,
                                    minimum=(0.0, 0.0, 0.0),
                                    maximum=(40.0, 40.0, 40.0))),
            Body("small", Measurement(20.0 ** 3, face_count=G.CYLINDER_FACES,
                                      minimum=(50.0, 0.0, 0.0),
                                      maximum=(70.0, 20.0, 20.0))),
        ]))
        graded, _ = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["both_bodies_prismatic"], False)

    # --- isolation --------------------------------------------------------

    def test_an_edit_that_touches_the_other_body_fails(self) -> None:
        """The whole reason M4 exists. Both bodies grew; the edited one is
        right, and the part is wrong."""
        leaked = observe("M4", two_body_generation(), Execution([
            Body("solid1", Measurement(40.0 * 40.0 * 50.0, face_count=G.BOX_FACES,
                                       minimum=(0.0, 0.0, 0.0),
                                       maximum=(40.0, 40.0, 50.0))),
            Body("solid2", Measurement(G.PIN_VOLUME_EDITED,
                                       face_count=G.CYLINDER_FACES,
                                       minimum=(50.0, 0.0, 0.0),
                                       maximum=(70.0, 20.0, 40.0))),
        ]))
        graded, codes = scored(leaked)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["other_body_untouched"], False)
        self.assertIn(G.E_CROSS_BODY_EDIT, codes)

    def test_editing_the_wrong_body_fails_as_a_wrong_target(self) -> None:
        wrong = observe("M4", two_body_generation(), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
        ]))
        graded, codes = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["edited_body"], False)
        self.assertIn(G.D_WRONG_TARGET, codes)

    def test_a_hole_drilled_into_the_wrong_body_fails(self) -> None:
        """M5 states no dimensions at all, so this is pinned on topology:
        the cube must still have six planar faces and no cylindrical one."""
        wrong = observe("M5", two_body_generation(), Execution([
            Body("solid1", Measurement(G.CUBE_VOLUME - 500.0,
                                       face_count=G.BOX_FACES + 1,
                                       minimum=(0.0, 0.0, 0.0),
                                       maximum=(40.0, 40.0, 40.0))),
            Body("solid2", Measurement(1000.0, face_count=G.CYLINDER_FACES,
                                       minimum=(50.0, 0.0, 0.0),
                                       maximum=(70.0, 20.0, 30.0))),
        ]))
        graded, _ = scored(wrong)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["untouched_body_intact"], False)

    # --- refusals ---------------------------------------------------------

    def test_guessing_a_body_instead_of_asking_is_a_refusal_failure(self) -> None:
        """"the body" with two bodies standing. A guess is worse than a
        wrong answer, because it is a wrong answer that looks like a right
        one -- the failure Stage 72 built `resolve_body` to prevent."""
        guessed = observe("M6", Generation(operations=[
            Operation("block", "box"),
        ], outcome="generated"), Execution([
            Body("block", Measurement(30.0 * 30.0 * 40.0, face_count=G.BOX_FACES)),
        ]))
        graded, codes = scored(guessed)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.K_REFUSAL_FAILURE, codes)

    def test_a_guess_that_happened_not_to_build_is_still_not_a_refusal(self) -> None:
        """Declining is checked in its own right.

        Here the model did NOT decline -- it answered `generated` and
        guessed -- but its plan was invalid, so nothing was built and it
        named both bodies in passing. Every other signal a refusal case
        looks at therefore reads like a clean refusal, and only the fact
        that the model did not decline separates the two. A mutant that
        stopped asking survived every other refusal test until this one
        existed, and it scored a guess as a correct refusal.
        """
        guessed = observe("M6", Generation(
            operations=[Operation("block", "box")],
            outcome="generated",
            valid=False,
            questions=("Taller: applying to block rather than rod.",),
        ), None)
        graded, codes = scored(guessed)
        self.assertTrue(graded["checks"]["named_the_bodies"])
        self.assertTrue(graded["checks"]["built_nothing"])
        self.assertIs(graded["checks"]["refused"], False)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.K_REFUSAL_FAILURE, codes)

    def test_a_refusal_that_names_no_body_is_a_shrug_and_fails(self) -> None:
        vague = observe("M6", Generation(
            operations=[], outcome="needs_clarification",
            questions=("Which one did you mean?",),
        ), None)
        graded, codes = scored(vague)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["named_the_bodies"], False)
        self.assertIn(G.K_REFUSAL_FAILURE, codes)

    def test_a_refusal_that_names_only_one_body_still_fails(self) -> None:
        half = observe("M7", Generation(
            operations=[], outcome="needs_clarification",
            questions=("There is no bracket. Did you mean block?",),
        ), None)
        graded, _ = scored(half)
        self.assertFalse(graded["strict_success"])
        self.assertIs(graded["checks"]["named_the_bodies"], False)

    def test_a_refusal_may_be_unsupported_rather_than_a_question(self) -> None:
        """Both are declining. The corpus grades the ANSWER, not the label
        the model chose to put on it."""
        refused = observe("M7", Generation(
            operations=[], outcome="unsupported",
            error="No body named bracket; block and rod are what exist.",
        ), None)
        self.assertTrue(E.grade(refused)["strict_success"])

    # --- ordering and labelling -------------------------------------------

    def test_the_order_the_bodies_come_back_in_is_not_meaning(self) -> None:
        swapped = observe("M1", two_body_generation(), Execution([
            Body("solid2", Measurement(G.PIN_VOLUME, **PIN_BOX)),
            Body("solid1", Measurement(G.CUBE_VOLUME, **CUBE_BOX)),
        ]))
        self.assertTrue(E.grade(swapped)["strict_success"])

    def test_an_unparseable_answer_is_a_provider_error_not_a_wrong_part(self) -> None:
        broken = observe("M1", Generation(
            operations=[], outcome="invalid_model_output"), None)
        graded, codes = scored(broken)
        self.assertFalse(graded["strict_success"])
        self.assertIn(G.J_INVALID_PLAN, codes)
        self.assertEqual(E.outcome_label(broken), G.PROVIDER_ERROR)

    def test_every_code_the_classifier_can_emit_is_in_the_taxonomy(self) -> None:
        emitted = set()
        for build in CORRECT.values():
            observation = build()
            emitted.update(E.classify(observation, E.grade(observation)))
        for case in ("M1", "M4", "M6"):
            observation = CORRECT[case]()
            observation["body_count"] = 99
            observation["bodies"] = []
            emitted.update(E.classify(observation, E.grade(observation)))
        self.assertTrue(emitted <= set(G.TAXONOMY), emitted - set(G.TAXONOMY))

    def test_the_two_groups_are_never_pooled(self) -> None:
        rows = [
            {"case": "M1", "strict_success": True, "codes": (), "label": G.MODEL_GENERATED},
            {"case": "M6", "strict_success": False, "codes": (G.K_REFUSAL_FAILURE,),
             "label": G.MODEL_GENERATED},
        ]
        summary = E.summarise(rows)
        self.assertEqual(summary["per_group"][G.CREATION]["rate"], 1.0)
        self.assertEqual(summary["per_group"][G.REFUSAL]["rate"], 0.0)
        self.assertNotIn("overall", summary)


if __name__ == "__main__":
    unittest.main()
