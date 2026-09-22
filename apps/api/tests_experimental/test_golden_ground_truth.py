"""The golden benchmark's ground truth is immutable, and cannot self-reference.

Stage 67 graded 80 live calls with a criterion that derived the expected
plate thickness from the model's own plan:

    t = plan_facts.get("thickness")     # stage67/spatial.py:124
    expected = closed_form_volume(ENVELOPE, t, d, 3)

Sixteen parts built from 4 mm plate scored correct against a request that
says 5, and the stage's headline number was wrong until an independent review
caught it. **A criterion that grades a part against its own answer cannot
fail it.**

Stage 68 rebuilt the benchmark so that cannot recur. These tests are the
guard. They assert three separate things:

1. the ground truth is CONSTANT -- fixed before any model was called, derived
   only from the request text and closed-form arithmetic;
2. the expectation API is structurally incapable of accepting model output --
   `expected()` takes a request NAME, not a plan and not a shape;
3. a 4 mm part -- the exact thing Stage 67 accepted -- is GRADED WRONG, and
   for the right reason.

The two requests are also pinned verbatim. The original is ambiguous on
purpose and must never be edited to make a score move.
"""

from __future__ import annotations

import importlib.util
import inspect
import math
import pathlib
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
STAGE68 = REPO / "docs" / "evaluation-baselines" / "stage68-benchmark-disambiguation"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, STAGE68 / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


G = _load("ground_truth")
E = _load("evaluate")


class Measurement:
    """A stand-in for a kernel measurement, so grading can be tested offline."""

    def __init__(self, volume, solid_count=1, face_count=18, edge_count=42,
                 minimum=(0.0, 0.0, 0.0), maximum=(40.0, 20.0, 20.0)):
        self.volume = volume
        self.solid_count = solid_count
        self.face_count = face_count
        self.edge_count = edge_count
        self.minimum = minimum
        self.maximum = maximum


def bores(axes=("X", "Y", "Z"), diameter=8.0, centred=True):
    """Holes at the enclosure centre, as the ground truth requires."""
    centre = [20.0, 10.0, 10.0]
    out = []
    for axis in axes:
        position = list(centre)
        if not centred:                      # the measured defect: z left at 0
            position[2] = 0.0
        out.append({"id": f"hole_{axis.lower()}", "target": "base",
                    "diameter": diameter, "axis": axis,
                    "position": tuple(position)})
    return out


def seen(thickness, plate_count=6, bore_count=3, axes=("X", "Y", "Z"),
         duplicates=0, diameter=8.0, span=(40.0, 20.0, 20.0), centred=True):
    return {
        "boxes": [], "unions": [],
        "holes": bores(tuple(axes)[:bore_count] if bore_count <= len(axes)
                       else tuple(axes) + ("Z",) * (bore_count - len(axes)),
                       diameter, centred),
        "plate_count": plate_count, "bore_count": bore_count,
        "distinct_centrelines": bore_count, "coaxial_duplicates": duplicates,
        "axes_used": sorted(axes), "observed_thickness": thickness,
        "observed_span": span,
    }


class TheGroundTruthIsImmutableTests(unittest.TestCase):

    def test_the_two_requests_are_pinned_verbatim(self) -> None:
        """The original is ambiguous ON PURPOSE and is never edited."""
        self.assertEqual(
            G.ORIGINAL,
            "Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 "
            "(2) plates with 8mm diameter holes in center of each plate")
        for token in ("40 mm long", "20 mm wide", "20 mm high", "5 mm thick",
                      "40 x 20 face", "20 x 20 face", "8 mm diameter"):
            self.assertIn(token, G.EXPLICIT,
                          "the companion request must state what the original leaves open")

    def test_the_companion_request_describes_the_same_part(self) -> None:
        """Disambiguated, not redesigned: one ground truth serves both."""
        self.assertEqual(G.expected("original"), G.expected("explicit"))

    def test_the_constants_are_the_request_not_a_measurement(self) -> None:
        self.assertEqual(G.THICKNESS, 5.0)
        self.assertEqual(G.ENVELOPE, (40.0, 20.0, 20.0))
        self.assertEqual(G.DIAMETER, 8.0)
        self.assertEqual(G.PLATE_COUNT, 6)
        self.assertEqual(G.BORE_COUNT, 3)
        self.assertEqual(G.OPENING_COUNT, 6)
        self.assertEqual(G.SOLID_COUNT, 1)
        self.assertEqual(G.FACE_COUNT, 18)
        self.assertEqual(G.EDGE_COUNT, 42)

    def test_the_volume_is_the_closed_form_of_those_constants(self) -> None:
        x, y, z, t, r = 40.0, 20.0, 20.0, 5.0, 4.0
        expected = (x * y * z - (x - 2 * t) * (y - 2 * t) * (z - 2 * t)) \
            - 3 * (2 * math.pi * r * r * t)
        self.assertAlmostEqual(G.VOLUME, expected, places=9)
        # The value the deterministic reader builds and a live plan reproduced.
        self.assertAlmostEqual(G.VOLUME, 11492.035526276899, places=6)

    def test_the_topology_is_derived_from_the_bore_count(self) -> None:
        """18 faces is 12 planar plus two cylindrical per bore, not a magic number."""
        self.assertEqual(G.CYLINDRICAL_FACES, 2 * G.BORE_COUNT)
        self.assertEqual(G.FACE_COUNT, G.PLANAR_FACES + G.CYLINDRICAL_FACES)
        self.assertEqual(G.OPENING_COUNT, 2 * G.BORE_COUNT)


class TheEvaluatorCannotGradeAgainstItselfTests(unittest.TestCase):
    """THE Stage 67 defect, made structurally unreachable."""

    def test_the_expectation_api_accepts_no_model_output(self) -> None:
        """`expected()` takes a request NAME. There is nowhere to pass a plan.

        This is the structural guarantee: not "we remembered not to", but
        "there is no parameter for it".
        """
        parameters = list(inspect.signature(G.expected).parameters)
        self.assertEqual(len(parameters), 1)
        for forbidden in ("plan", "facts", "measurement", "shape", "thickness",
                          "observed", "result"):
            self.assertNotIn(forbidden, parameters,
                             "the expectation must not accept model output")

    def test_the_expectation_is_identical_whatever_is_asked_for(self) -> None:
        for key in (None, "original", "explicit", "anything at all"):
            self.assertEqual(G.expected(key)["thickness"], 5.0)
            self.assertEqual(G.expected(key)["volume"], G.VOLUME)

    def test_the_ground_truth_module_imports_nothing_that_carries_an_answer(self) -> None:
        """Checked on the AST, so prose in a docstring cannot pass or fail it.

        The module may import arithmetic and typing. It may not import the
        parser, the executor, a backend or anything else through which a
        model answer or a kernel measurement could reach an expectation.
        """
        import ast
        tree = ast.parse((STAGE68 / "ground_truth.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported, {"math", "typing", "__future__"},
                         f"ground truth imports {sorted(imported)}")

    def test_no_ground_truth_value_is_computed_from_a_call(self) -> None:
        """Every constant is a literal or arithmetic over literals.

        `VOLUME` is the one derived value, and it is derived from the
        constants in this module by `_closed_form()` -- which takes no
        arguments, so there is nothing a caller could inject.
        """
        import ast
        tree = ast.parse((STAGE68 / "ground_truth.py").read_text(encoding="utf-8"))
        closed_form = next(n for n in tree.body
                           if isinstance(n, ast.FunctionDef) and n.name == "_closed_form")
        self.assertEqual(len(closed_form.args.args), 0,
                         "_closed_form must take no argument")
        self.assertEqual(len(G.expected.__code__.co_varnames[:G.expected.__code__.co_argcount]), 1)

    def test_a_four_millimetre_part_is_graded_WRONG(self) -> None:
        """The exact case Stage 67 accepted 16 times.

        A 4 mm enclosure is one solid with the same 18 faces, 42 edges and the
        same 40x20x20 envelope. Only the thickness and the volume tell it
        apart -- and both are graded against the constants.
        """
        four_mm_volume = (40 * 20 * 20 - 32 * 12 * 12) - 3 * (2 * math.pi * 16 * 4)
        verdict = E.grade(seen(thickness=4.0), Measurement(four_mm_volume),
                          built=True, failure_message=None, plan_valid=True)
        self.assertFalse(verdict["STRICT_SUCCESS"])
        self.assertFalse(verdict["checks"]["thickness"])
        self.assertFalse(verdict["checks"]["volume"])
        self.assertIn(G.A_THICKNESS,
                      E.classify(seen(thickness=4.0), verdict, None))

    def test_the_stage_67_criterion_would_have_passed_it(self) -> None:
        """Characterises the defect, so the difference is not merely asserted.

        Stage 67 computed its reference from the model's own thickness, so the
        4 mm part matched its own closed form exactly. That is what made it
        pass. Here the same part is compared against the 5 mm constant.
        """
        four = (40 * 20 * 20 - 32 * 12 * 12) - 3 * (2 * math.pi * 16 * 4)
        self.assertAlmostEqual(four, 10185.628421021519, places=6)   # self-graded: passes
        self.assertGreater(abs(four - G.VOLUME), 1000.0)             # constant: fails

    def test_a_five_millimetre_part_is_graded_right(self) -> None:
        verdict = E.grade(seen(thickness=5.0), Measurement(G.VOLUME),
                          built=True, failure_message=None, plan_valid=True)
        self.assertTrue(verdict["STRICT_SUCCESS"], verdict["checks"])
        self.assertEqual(E.classify(seen(thickness=5.0), verdict, None), [])


class TheTaxonomyCoversTheFailuresTests(unittest.TestCase):

    def test_every_code_is_distinct_and_named(self) -> None:
        self.assertEqual(len(G.TAXONOMY), len(set(G.TAXONOMY)))
        self.assertEqual(len(G.TAXONOMY), 9, "A-I")
        for code in G.TAXONOMY:
            self.assertRegex(code, r"^[A-I]:[a-z_]+$")

    def test_a_wrong_envelope_is_B_not_A(self) -> None:
        verdict = E.grade(seen(thickness=5.0, span=(40.0, 20.0, 5.0)),
                          Measurement(3000.0, maximum=(40.0, 20.0, 5.0)),
                          built=True, failure_message=None, plan_valid=True)
        codes = E.classify(seen(thickness=5.0, span=(40.0, 20.0, 5.0)), verdict, None)
        self.assertIn(G.B_ENVELOPE_HEIGHT, codes)
        self.assertNotIn(G.A_THICKNESS, codes)

    def test_a_duplicate_coaxial_cut_is_G(self) -> None:
        s = seen(thickness=5.0, bore_count=4, duplicates=1)
        verdict = E.grade(s, Measurement(G.VOLUME), True, None, True)
        self.assertIn(G.G_DEGENERATE_CUT, E.classify(s, verdict, None))

    def test_an_off_centre_bore_is_E_even_when_it_builds(self) -> None:
        """The measured Stage 68 residual: a built part with grazing bores.

        The model reused the +Z hole's position triple for all three bores,
        leaving z=0 on the +Y and +X ones -- which is only meaningful for +Z,
        where z is the ignored along-axis component. The part still built, so
        a classifier keyed on the build failure message alone missed it.
        """
        s = seen(thickness=5.0, centred=False)
        verdict = E.grade(s, Measurement(11351.419374, face_count=25, edge_count=65),
                          built=True, failure_message=None, plan_valid=True)
        self.assertFalse(verdict["checks"]["bore_centred"])
        self.assertFalse(verdict["STRICT_SUCCESS"])
        self.assertIn(G.E_BORE_POSITION, E.classify(s, verdict, None))

    def test_the_free_component_along_a_bores_own_axis_is_not_graded(self) -> None:
        """A +Z bore may write any z: the cut is unbounded along its axis."""
        s = seen(thickness=5.0)
        for hole in s["holes"]:
            if hole["axis"] == "Z":
                hole["position"] = (20.0, 10.0, 999.0)
        verdict = E.grade(s, Measurement(G.VOLUME), True, None, True)
        self.assertTrue(verdict["checks"]["bore_centred"])

    def test_an_invalid_plan_is_H(self) -> None:
        s = seen(thickness=5.0)
        verdict = E.grade(s, None, False, None, plan_valid=False)
        self.assertIn(G.H_WRONG_TARGET, E.classify(s, verdict, None))


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
