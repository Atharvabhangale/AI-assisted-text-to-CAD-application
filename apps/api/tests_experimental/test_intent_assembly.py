"""The provider-neutral assembly path: intent, lowering, and who produced it.

The golden request is one sentence a person actually typed:

    Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 (2) plates
    with 8mm diameter holes in center of each plate

Everything here is about getting from that sentence to a real six-plate
enclosure with a centred bore in every plate, **without any model being
involved at all** -- and about proving that the same pipeline serves a
provider that returns canonical intent, so the hosted model is one source
among several rather than a dependency.

No test in this module imports a provider SDK, reads a credential or opens a
socket. The kernel tests build real geometry with the default backend.
"""

from __future__ import annotations

import math
import os
import tempfile
import unittest

from cad_experimental.intent import (
    BOX_FACES,
    CENTER,
    HOLLOW_BOX,
    PLATE_ASSEMBLY,
    HoleSpec,
    IntentError,
    PlateAssemblyIntent,
    PlateGroup,
    extract_intent,
    intent_from_dict,
    looks_like_plate_assembly,
    lower_to_plan,
    plan_from_request,
)
from cad_experimental.interpretation import (
    SOURCE_DETERMINISTIC,
    SOURCE_PROVIDER,
    deterministic_interpretation,
    intent_interpretation,
    interpret,
)
from cad_experimental.local_intent_provider import (
    DecliningProvider,
    FakeLocalProvider,
    IntentProvider,
    intent_from_provider,
)
from cad_experimental.parser import parse_plan
from cad_experimental.validation import validate_plan

#: The exact sentence. Not paraphrased anywhere in this module.
GOLDEN = (
    "Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 (2) "
    "plates with 8mm diameter holes in center of each plate"
)

#: The canonical intent the golden request means. Written out rather than
#: read back from the extractor, so a change to the grammar that quietly
#: changed the meaning would fail here instead of agreeing with itself.
GOLDEN_INTENT = {
    "kind": PLATE_ASSEMBLY,
    "container": HOLLOW_BOX,
    "plate_groups": [
        {"width": 40.0, "height": 20.0, "count": 4, "thickness": 5.0},
        {"width": 20.0, "height": 20.0, "count": 2, "thickness": 5.0},
    ],
    "hole": {"diameter": 8.0, "position": CENTER,
             "count_per_plate": 1, "through": True},
}


# --- reading the request ----------------------------------------------------


class GoldenPhraseTests(unittest.TestCase):

    def setUp(self) -> None:
        self.intent = extract_intent(GOLDEN)

    def test_the_exact_golden_phrase_is_recognised(self) -> None:
        self.assertTrue(looks_like_plate_assembly(GOLDEN))

    def test_it_reads_as_the_canonical_intent(self) -> None:
        self.assertEqual(self.intent.to_dict(), GOLDEN_INTENT)

    def test_six_plates_in_total(self) -> None:
        self.assertEqual(self.intent.total_plates, BOX_FACES)
        self.assertEqual(self.intent.total_plates, 6)

    def test_four_large_plates_and_two_square_ones(self) -> None:
        large, square = self.intent.plate_groups
        self.assertEqual((large.width, large.height, large.count), (40.0, 20.0, 4))
        self.assertEqual((square.width, square.height, square.count), (20.0, 20.0, 2))

    def test_the_omitted_second_thickness_is_inherited(self) -> None:
        """"20*20" states no thickness. It takes the 5 mm that WAS stated --
        inherited from the request, never defaulted by this code."""
        large, square = self.intent.plate_groups
        self.assertEqual(large.thickness, 5.0)
        self.assertEqual(square.thickness, 5.0)

    def test_the_hole_is_eight_millimetres_through_each_centre(self) -> None:
        self.assertIsNotNone(self.intent.hole)
        self.assertEqual(self.intent.hole.diameter, 8.0)
        self.assertEqual(self.intent.hole.position, CENTER)
        self.assertEqual(self.intent.hole.count_per_plate, 1)
        self.assertTrue(self.intent.hole.through)


class WordingTests(unittest.TestCase):
    """Six ways to write the same part. All must mean the same thing."""

    WORDINGS = {
        "asterisks and bracketed counts": GOLDEN,
        "40 x 20 x 5 (4) and 20 x 20 (2)": (
            "Make a hollow rectangular box with 40 x 20 x 5 (4) plates and "
            "20 x 20 (2) plates with 8 mm diameter holes in center of each "
            "plate"
        ),
        "four and two, in words": (
            "Make a hollow box from four 40x20x5 plates and two 20x20 plates "
            "with 8mm holes in the centre of each plate"
        ),
        "bare counts before the noun": (
            "Make a hollow box with 40*20*5 4 plates and 20*20 2 plates with "
            "8mm diameter holes in center of each plate"
        ),
        "diameter sign": (
            "Make a hollow rectangular box with 40*20*5 (4)plates and "
            "20*20 (2) plates with Ø8 holes in center of each plate"
        ),
        "diameter as a phrase": (
            "hollow box, 40*20*5 (4) plates and 20*20 (2) plates, holes of "
            "8mm diameter centred on each plate"
        ),
    }

    def test_every_wording_reads_as_the_same_canonical_intent(self) -> None:
        for name, text in self.WORDINGS.items():
            with self.subTest(wording=name):
                self.assertEqual(extract_intent(text).to_dict(), GOLDEN_INTENT)

    def test_every_wording_lowers_to_the_same_plan(self) -> None:
        reference = lower_to_plan(extract_intent(GOLDEN))
        for name, text in self.WORDINGS.items():
            with self.subTest(wording=name):
                self.assertEqual(lower_to_plan(extract_intent(text)), reference)

    def test_a_word_count_cannot_leak_into_the_previous_group(self) -> None:
        """"four 40x20x5 plates and two 20x20 plates": the "two" belongs to
        the second group. A forward window that accepted a bare word would
        give the first group a count of two and build the wrong box."""
        intent = extract_intent(self.WORDINGS["four and two, in words"])
        self.assertEqual([g.count for g in intent.plate_groups], [4, 2])


class RefusalTests(unittest.TestCase):
    """A request this grammar cannot read completely is refused, not guessed."""

    def test_a_request_that_is_not_an_assembly_is_left_alone(self) -> None:
        for text in ("Create a 100 mm by 60 mm by 10 mm plate.",
                     "Make a cylinder 20 mm across and 50 tall.",
                     ""):
            with self.subTest(text=text):
                self.assertFalse(looks_like_plate_assembly(text))
                with self.assertRaises(IntentError):
                    extract_intent(text)

    def test_a_plate_count_that_does_not_close_a_box_is_refused(self) -> None:
        text = ("Make a hollow box with 40*20*5 (3) plates and 20*20 (2) "
                "plates with 8mm diameter holes in center of each plate")
        with self.assertRaises(IntentError) as raised:
            extract_intent(text)
        self.assertIn("6 plates", str(raised.exception))

    def test_a_thickness_with_nothing_to_inherit_is_refused(self) -> None:
        """Two pair-form groups and no triple: nothing states a thickness,
        so there is nothing to inherit and none is invented."""
        text = ("Make a hollow box with 40*20 (4) plates and 20*20 (2) "
                "plates with 8mm diameter holes in center of each plate")
        with self.assertRaises(IntentError) as raised:
            extract_intent(text)
        self.assertIn("thickness", str(raised.exception))

    def test_a_non_positive_dimension_is_refused(self) -> None:
        intent = PlateAssemblyIntent(
            plate_groups=(PlateGroup(40.0, 20.0, 4, 5.0),
                          PlateGroup(0.0, 20.0, 2, 5.0)))
        with self.assertRaises(IntentError) as raised:
            from cad_experimental.intent import validate_intent

            validate_intent(intent)
        self.assertIn("positive", str(raised.exception))

    def test_a_non_positive_count_is_refused(self) -> None:
        from cad_experimental.intent import validate_intent

        intent = PlateAssemblyIntent(
            plate_groups=(PlateGroup(40.0, 20.0, 8, 5.0),
                          PlateGroup(20.0, 20.0, -2, 5.0)))
        with self.assertRaises(IntentError) as raised:
            validate_intent(intent)
        self.assertIn("count", str(raised.exception))

    def test_a_hole_wider_than_its_plate_is_refused(self) -> None:
        from cad_experimental.intent import validate_intent

        intent = PlateAssemblyIntent(
            plate_groups=(PlateGroup(40.0, 20.0, 4, 5.0),
                          PlateGroup(20.0, 20.0, 2, 5.0)),
            hole=HoleSpec(diameter=25.0))
        with self.assertRaises(IntentError) as raised:
            validate_intent(intent)
        self.assertIn("does not fit", str(raised.exception))


# --- lowering ---------------------------------------------------------------


class LoweringTests(unittest.TestCase):

    def setUp(self) -> None:
        self.intent, self.payload = plan_from_request(GOLDEN)
        self.ops = self.payload["operations"]

    def test_the_plan_parses_and_validates(self) -> None:
        plan = parse_plan(self.payload)
        verdict = validate_plan(plan)
        self.assertTrue(verdict.valid, [p.code for p in verdict.problems])

    def test_six_plates_become_six_boxes(self) -> None:
        boxes = [op for op in self.ops if op["type"] == "box"]
        self.assertEqual(len(boxes), 6)
        self.assertEqual({op["id"] for op in boxes},
                         {"bottom", "top", "front", "back", "left", "right"})

    def test_the_plates_are_fused_by_one_union(self) -> None:
        unions = [op for op in self.ops if op["type"] == "union"]
        self.assertEqual(len(unions), 1)
        self.assertEqual(unions[0]["target"], "bottom")
        self.assertEqual(sorted(unions[0]["tools"]),
                         ["back", "front", "left", "right", "top"])

    def test_three_axial_holes_not_six_redundant_ones(self) -> None:
        """Each bore is coaxial through one opposing PAIR, so three
        operations open six plates. Six operations would cut each opening
        twice, which is the same part described worse."""
        holes = [op for op in self.ops if op["type"] == "through_hole"]
        self.assertEqual(len(holes), 3)
        self.assertEqual([h["parameters"]["axis"] for h in holes],
                         ["+Z", "+Y", "+X"])

    def test_each_bore_sits_on_the_axis_the_design_names(self) -> None:
        holes = {op["id"]: op["parameters"] for op in self.ops
                 if op["type"] == "through_hole"}
        self.assertEqual(holes["hole_z"]["position"],
                         {"x": 20.0, "y": 10.0, "z": 0.0})
        self.assertEqual(holes["hole_y"]["position"],
                         {"x": 20.0, "y": 0.0, "z": 10.0})
        self.assertEqual(holes["hole_x"]["position"],
                         {"x": 0.0, "y": 10.0, "z": 10.0})
        for parameters in holes.values():
            self.assertEqual(parameters["diameter"], 8.0)

    def test_every_bore_targets_the_fused_body(self) -> None:
        """A bore must cut the shell, not one loose plate."""
        for op in self.ops:
            if op["type"] == "through_hole":
                self.assertEqual(op["target"], "bottom")

    def test_the_plates_enclose_a_forty_by_twenty_by_twenty_envelope(
        self,
    ) -> None:
        boxes = [op["parameters"] for op in self.ops if op["type"] == "box"]
        lo = [min(b["position"][k] for b in boxes) for k in "xyz"]
        hi = [max(b["position"][k] + b[k] for b in boxes) for k in "xyz"]
        self.assertEqual(lo, [0.0, 0.0, 0.0])
        self.assertEqual(hi, [40.0, 20.0, 20.0])

    def test_the_union_comes_before_every_bore(self) -> None:
        kinds = [op["type"] for op in self.ops]
        self.assertLess(kinds.index("union"), kinds.index("through_hole"))


# --- provider independence --------------------------------------------------


class ProviderIndependenceTests(unittest.TestCase):

    def test_extraction_needs_no_provider_at_all(self) -> None:
        interpretation = deterministic_interpretation(GOLDEN)
        self.assertTrue(interpretation.understood)
        self.assertEqual(interpretation.source, SOURCE_DETERMINISTIC)
        self.assertEqual(interpretation.intent.to_dict(), GOLDEN_INTENT)

    def test_the_fake_local_provider_reaches_the_same_plan(self) -> None:
        provider = FakeLocalProvider(GOLDEN_INTENT)
        intent = intent_from_provider(provider, GOLDEN)
        interpretation = intent_interpretation(intent)
        self.assertTrue(interpretation.understood)
        self.assertEqual(interpretation.source, SOURCE_PROVIDER)
        self.assertTrue(validate_plan(interpretation.plan).valid)

    def test_both_routes_produce_an_identical_canonical_plan(self) -> None:
        """The invariant: same intent, same Operation Plan, whatever the
        source. If these ever diverge, "provider-independent" is a word."""
        deterministic = deterministic_interpretation(GOLDEN)
        provider = intent_interpretation(
            intent_from_provider(FakeLocalProvider(GOLDEN_INTENT), GOLDEN)
        )
        self.assertEqual(lower_to_plan(deterministic.intent),
                         lower_to_plan(provider.intent))
        self.assertEqual(
            [op.TYPE for op in deterministic.plan.operations],
            [op.TYPE for op in provider.plan.operations],
        )

    def test_the_fake_provider_does_not_read_the_request(self) -> None:
        """It is a stand-in for a model, not a second extractor. If it
        parsed the text, this whole test module would be proving the
        deterministic reader twice and the provider route never."""
        provider = FakeLocalProvider(GOLDEN_INTENT)
        first = intent_from_provider(provider, GOLDEN)
        second = intent_from_provider(provider, "something else entirely")
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(provider.requests, [GOLDEN, "something else entirely"])

    def test_a_provider_may_decline(self) -> None:
        self.assertIsNone(
            intent_from_provider(DecliningProvider(), GOLDEN)
        )

    def test_a_provider_returning_nonsense_raises_rather_than_repairs(
        self,
    ) -> None:
        broken = dict(GOLDEN_INTENT,
                      plate_groups=[{"width": 40.0, "height": 20.0,
                                     "count": 3, "thickness": 5.0}])
        with self.assertRaises(IntentError):
            intent_from_provider(FakeLocalProvider(broken), GOLDEN)

    def test_a_fake_provider_announces_itself_as_local_development(
        self,
    ) -> None:
        self.assertTrue(FakeLocalProvider.is_local_development)
        self.assertTrue(DecliningProvider.is_local_development)
        self.assertFalse(IntentProvider.is_local_development)

    def test_the_fake_provider_imports_no_sdk(self) -> None:
        """Read the IMPORTS, not the prose. A word like "requests" is an
        ordinary noun in a docstring and an attribute name on the provider
        itself; only what the module actually pulls in decides this."""
        import cad_experimental.local_intent_provider as module

        with open(module.__file__, encoding="utf-8") as handle:
            imports = [line.strip() for line in handle
                       if line.lstrip().startswith(("import ", "from "))]
        for line in imports:
            for forbidden in ("anthropic", "openai", "genai", "requests",
                              "httpx", "socket", "urllib", "boto"):
                self.assertNotIn(forbidden, line.lower(), line)
        self.assertNotIn("api_key", "".join(imports).lower())

    def test_intent_round_trips_through_its_own_serialized_form(self) -> None:
        intent = extract_intent(GOLDEN)
        self.assertEqual(intent_from_dict(intent.to_dict()).to_dict(),
                         intent.to_dict())


class AnthropicIsOptionalTests(unittest.TestCase):
    """The architecture claim, asserted rather than asserted about.

    The golden request must build with **no usable model answer at all**.
    That is what makes the hosted model one provider rather than the
    project's dependency.
    """

    GEOMETRY_CORE = (
        "intent.py", "interpretation.py", "local_intent_provider.py",
        "plan.py", "parser.py", "validation.py", "graph.py", "history.py",
        "executor.py", "adapter.py",
    )

    VENDOR_WORDS = ("anthropic", "haiku", "claude-", "openai", "gpt-",
                    "gemini", "output_config", "json_schema")

    def test_no_geometry_module_imports_a_vendor(self) -> None:
        import cad_experimental

        root = os.path.dirname(cad_experimental.__file__)
        for name in self.GEOMETRY_CORE:
            with self.subTest(module=name):
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    lines = fh.readlines()
                code = [ln for ln in lines
                        if ln.lstrip().startswith(("import ", "from "))]
                for line in code:
                    for word in self.VENDOR_WORDS:
                        self.assertNotIn(word, line.lower(),
                                         f"{name}: {line.strip()}")

    def test_the_golden_request_builds_from_an_unusable_model_answer(
        self,
    ) -> None:
        """`interpret` is given a provider result that carries no plan --
        the shape of every way a model can fail -- and the part is still
        understood."""
        from cad_experimental.generation import (
            PlanGenerationMetadata,
            PlanGenerationResult,
            PlanOutcome,
        )

        for outcome in (PlanOutcome.MODEL_ERROR,
                        PlanOutcome.INVALID_MODEL_OUTPUT,
                        PlanOutcome.UNSUPPORTED,
                        PlanOutcome.NEEDS_CLARIFICATION):
            with self.subTest(outcome=outcome.value):
                result = PlanGenerationResult(
                    outcome=outcome,
                    metadata=PlanGenerationMetadata(provider="none",
                                                    model="none"),
                    error="the model was no use",
                )
                interpretation = interpret(GOLDEN, result)
                self.assertTrue(interpretation.understood, outcome.value)
                self.assertEqual(interpretation.source, SOURCE_DETERMINISTIC)
                self.assertTrue(validate_plan(interpretation.plan).valid)

    def test_a_usable_provider_plan_is_believed_over_the_fallback(
        self,
    ) -> None:
        """The fallback is a fallback. It must not overrule a provider that
        answered well, or the provider would be decorative."""
        from cad_experimental.generation import (
            PlanGenerationMetadata,
            PlanGenerationResult,
            PlanOutcome,
        )

        plan = parse_plan(lower_to_plan(extract_intent(GOLDEN)))
        result = PlanGenerationResult(
            outcome=PlanOutcome.GENERATED, plan=plan,
            metadata=PlanGenerationMetadata(provider="any", model="any"))
        interpretation = interpret(GOLDEN, result)
        self.assertEqual(interpretation.source, SOURCE_PROVIDER)
        self.assertIs(interpretation.plan, plan)


class NoModelConfiguredTests(unittest.TestCase):
    """The strongest form of "Anthropic is optional": no model at all.

    Not a unit test of the reader -- the real ASGI app, the real session
    endpoint, the real parser, validator and kernel, with no credential in
    the environment and therefore no planner constructed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import os

        from fastapi.testclient import TestClient

        from cad_experimental.app import create_app

        cls._saved = {k: os.environ.pop(k, None)
                      for k in ("CAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")}
        os.environ.setdefault("CAD_API_CACHE_ROOT", tempfile.mkdtemp())
        cls.client = TestClient(create_app())

    @classmethod
    def tearDownClass(cls) -> None:
        import os

        for key, value in cls._saved.items():
            if value is not None:
                os.environ[key] = value

    def test_the_golden_request_builds_with_no_model_configured(self) -> None:
        response = self.client.post("/experimental/session/message",
                                    json={"text": GOLDEN})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "built")
        self.assertEqual(body["interpreted_by"]["source"], SOURCE_DETERMINISTIC)

    def test_it_says_that_no_model_was_configured_not_that_one_failed(
        self,
    ) -> None:
        """Two different true sentences. Telling a user their model answered
        badly, when they never configured one, misdescribes their setup."""
        from cad_experimental.interpretation import (
            DETERMINISTIC_NOTE,
            NO_MODEL_NOTE,
        )

        body = self.client.post("/experimental/session/message",
                                json={"text": GOLDEN}).json()
        self.assertEqual(body["interpreted_by"]["note"], NO_MODEL_NOTE)
        self.assertNotEqual(body["interpreted_by"]["note"], DETERMINISTIC_NOTE)

    def test_the_part_it_built_is_the_real_part(self) -> None:
        body = self.client.post("/experimental/session/message",
                                json={"text": GOLDEN}).json()
        measurement = body["measurement"]
        self.assertEqual(measurement["solid_count"], 1)
        self.assertTrue(measurement["is_valid"])
        shell = 40.0 * 20.0 * 20.0 - 30.0 * 10.0 * 10.0
        bores = 6 * math.pi * 4.0 ** 2 * 5.0
        self.assertTrue(math.isclose(measurement["volume"], shell - bores,
                                     rel_tol=1e-9))
        self.assertEqual([round(v, 9) for v in measurement["size"]],
                         [40.0, 20.0, 20.0])

    def test_a_request_the_reader_cannot_read_still_reports_no_model(
        self,
    ) -> None:
        """The fallback is not a licence to guess. A request outside the
        grammar gets the honest 503 it always got."""
        response = self.client.post(
            "/experimental/session/message",
            json={"text": "Design me a differential gearbox."})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "unavailable")


# --- the kernel -------------------------------------------------------------


def _service():
    from cad_core.application_service import CadApplicationService

    return CadApplicationService.local(tempfile.mkdtemp())


class GoldenGeometryTests(unittest.TestCase):
    """Builds the part for real, on whichever backend is configured."""

    @classmethod
    def setUpClass(cls) -> None:
        from cad_experimental.build import build_plan

        intent, payload = plan_from_request(GOLDEN)
        cls.plan = parse_plan(payload)
        cls.result = build_plan(_service(), cls.plan, name="golden-hollow-box")
        if not cls.result.built:
            raise AssertionError(f"the golden build failed: {cls.result.error}")
        cls.body = cls.result.execution.bodies[0]
        cls.measure = cls.body.measurement

    def test_it_builds_through_the_graph_executor(self) -> None:
        self.assertTrue(self.result.built)
        self.assertTrue(self.result.executed)

    def test_it_leaves_exactly_one_valid_solid(self) -> None:
        self.assertEqual(len(self.result.execution.bodies), 1)
        self.assertEqual(self.measure.solid_count, 1)
        self.assertTrue(self.measure.is_valid)

    def test_the_envelope_is_forty_by_twenty_by_twenty(self) -> None:
        for got, want in zip(self.measure.size, (40.0, 20.0, 20.0)):
            self.assertAlmostEqual(got, want, places=9)

    def test_the_volume_matches_an_independent_closed_form(self) -> None:
        """Outer envelope, less the cavity the plates leave, less six bores
        of the plate thickness. Arithmetic on the request's own numbers --
        never a kernel measurement recorded as its own expectation."""
        shell = 40.0 * 20.0 * 20.0 - 30.0 * 10.0 * 10.0
        bores = 6 * math.pi * 4.0 ** 2 * 5.0
        self.assertTrue(
            math.isclose(self.measure.volume, shell - bores, rel_tol=1e-9),
            f"{self.measure.volume} != {shell - bores}",
        )

    def test_the_union_fused_the_plates_rather_than_leaving_them_loose(
        self,
    ) -> None:
        """Six separate 5 mm plates would measure their own total. One fused
        shell measures the envelope minus the cavity, which is less."""
        loose = 4 * (40.0 * 20.0 * 5.0) + 2 * (20.0 * 20.0 * 5.0)
        self.assertLess(self.measure.volume, loose)
        self.assertEqual(self.measure.solid_count, 1)

    def test_the_body_carries_every_feature_in_order(self) -> None:
        self.assertEqual(list(self.body.features),
                         ["bottom", "shell", "hole_z", "hole_y", "hole_x"])


class SixOpeningTests(unittest.TestCase):
    """Six openings, established from kernel topology.

    Deliberately **not** inferred from the plan having three bores. The
    question is what the finished solid has, and the only honest way to
    answer it is to read the solid.
    """

    RADIUS = 4.0
    AXES = {0: "X", 1: "Y", 2: "Z"}

    @classmethod
    def setUpClass(cls) -> None:
        from cad_experimental.cad_backend import resolve_backend
        from cad_experimental.executor import execute_plan

        intent, payload = plan_from_request(GOLDEN)
        cls.backend = resolve_backend()
        cls.execution = execute_plan(parse_plan(payload), backend=cls.backend,
                                     part_name="golden-hollow-box")
        if not cls.execution.succeeded:
            raise AssertionError(cls.execution.failure)
        body = cls.execution.bodies[0]
        shape = cls.execution.shapes[body.id]
        cls.facts = cls.backend.describe_edges(shape)
        cls.rims = cls._rims(cls.facts)

    @classmethod
    def _rims(cls, facts):
        """Every circular edge of the bore radius, by the plane it lies in."""
        found = {}
        for fact in facts:
            if fact.curve != "circle" or fact.radius is None:
                continue
            if not math.isclose(fact.radius, cls.RADIUS, abs_tol=1e-9):
                continue
            normal = fact.normal or (0.0, 0.0, 0.0)
            axis = max(range(3), key=lambda i: abs(normal[i]))
            if not math.isclose(abs(normal[axis]), 1.0, abs_tol=1e-9):
                continue
            key = (cls.AXES[axis], round(fact.centre[axis], 6))
            found.setdefault(key, []).append(fact.index)
        return found

    def test_twelve_rim_edges_exist(self) -> None:
        """A bore through one plate leaves a rim on each of its two faces.
        Six openings is twelve rims, and no rim shared between two."""
        self.assertEqual(sum(len(v) for v in self.rims.values()), 12)

    def test_each_rim_sits_alone_in_its_own_plane(self) -> None:
        self.assertEqual(len(self.rims), 12)
        for key, indices in self.rims.items():
            self.assertEqual(len(indices), 1, key)

    def test_the_rims_pair_into_six_openings_of_one_plate_thickness(
        self,
    ) -> None:
        by_axis = {}
        for (axis, coord) in self.rims:
            by_axis.setdefault(axis, []).append(coord)
        openings = []
        for axis, coords in sorted(by_axis.items()):
            coords.sort()
            self.assertEqual(len(coords), 4, axis)
            for i in range(0, 4, 2):
                self.assertAlmostEqual(coords[i + 1] - coords[i], 5.0,
                                       places=9)
                openings.append((axis, coords[i], coords[i + 1]))
        self.assertEqual(len(openings), 6)

    def test_one_opening_per_plate_and_no_stray_bore(self) -> None:
        """The exact six the design names. An axis that drilled through an
        unrelated plate would put a rim on a plane that is not in this set,
        and the count above would exceed twelve."""
        self.assertEqual(
            sorted(self.rims),
            [("X", 0.0), ("X", 5.0), ("X", 35.0), ("X", 40.0),
             ("Y", 0.0), ("Y", 5.0), ("Y", 15.0), ("Y", 20.0),
             ("Z", 0.0), ("Z", 5.0), ("Z", 15.0), ("Z", 20.0)],
        )

    def test_every_rim_is_where_a_plate_face_is(self) -> None:
        """0 and 5 are the two faces of one plate; 35 and 40 of its
        opposite. A rim anywhere else would mean a bore in mid-air."""
        faces = {0.0, 5.0, 15.0, 20.0, 35.0, 40.0}
        for axis, coord in self.rims:
            self.assertIn(coord, faces, (axis, coord))

    def test_a_render_model_comes_out_of_the_finished_solid(self) -> None:
        body = self.execution.bodies[0]
        model = self.backend.render_model(
            self.execution.shapes[body.id], part_name="golden",
            feature_id=body.id)
        self.assertIsNotNone(model)
        counter = getattr(model, "triangle_count", None)
        self.assertTrue(callable(counter))
        self.assertGreater(int(counter()), 0)


class FakeProviderBuildsTheRealPartTests(unittest.TestCase):
    """The milestone's success condition, end to end, with no model."""

    def test_fake_local_provider_to_kernel(self) -> None:
        from cad_experimental.build import build_plan

        intent = intent_from_provider(FakeLocalProvider(GOLDEN_INTENT),
                                      "ignored by this provider")
        interpretation = intent_interpretation(intent)
        self.assertTrue(validate_plan(interpretation.plan).valid)
        result = build_plan(_service(), interpretation.plan, name="from-fake")
        self.assertTrue(result.built, result.error)
        measure = result.execution.bodies[0].measurement
        self.assertEqual(measure.solid_count, 1)
        shell = 40.0 * 20.0 * 20.0 - 30.0 * 10.0 * 10.0
        bores = 6 * math.pi * 4.0 ** 2 * 5.0
        self.assertTrue(math.isclose(measure.volume, shell - bores,
                                     rel_tol=1e-9))


class SingleSolidOnTheExecutorPathTests(unittest.TestCase):
    """The executor's own S9.

    The document path gets the single-solid rule for free: `plan_to_document`
    emits a V1 document and the V1 validator refuses two solids. A plan that
    uses `union` has NO document form, so it takes the graph executor -- and
    nothing there applied the rule. Stage 45 recorded the gap ("S9 never runs
    and a leftover solid is otherwise invisible"); `union` turned invisible
    into wrong, because such a plan builds.

    Measured before the fix: `succeeded=True`, `failure=None`, two live
    bodies, and the caller took `bodies[0]` -- so the second solid vanished
    from the render, the measurement and every export with nothing saying so.
    """

    @staticmethod
    def _box(identifier, x, y, z, px=0.0, py=0.0, pz=0.0):
        return {"id": identifier, "type": "box",
                "parameters": {"x": x, "y": y, "z": z,
                               "position": {"x": px, "y": py, "z": pz}}}

    def _run(self, operations):
        from cad_experimental.cad_backend import resolve_backend
        from cad_experimental.executor import execute_plan
        from cad_experimental.parser import parse_plan
        plan = parse_plan({"status": "generated", "summary": "t",
                           "operations": operations})
        return execute_plan(plan, backend=resolve_backend())

    def test_a_union_that_leaves_an_orphan_solid_is_refused(self):
        from cad_experimental.executor import MULTIPLE_SOLIDS

        result = self._run([
            self._box("a", 10, 10, 10),
            self._box("b", 10, 10, 10, px=10.0),
            self._box("orphan", 5, 5, 5, px=100.0, py=100.0, pz=100.0),
            {"id": "u", "type": "union", "target": "a", "tools": ["b"]},
        ])
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, MULTIPLE_SOLIDS)
        # It names the solids, because "there are two" is not actionable and
        # "'a' and 'orphan'" is.
        self.assertIn("'orphan'", result.failure.message)
        self.assertIn("'a'", result.failure.message)

    def test_nothing_is_fused_or_dropped_to_make_the_count_come_out(self):
        """Reported, never repaired.

        The two obvious "helpful" repairs -- fuse the orphan in, or discard
        it -- are both guesses about what the author meant, and both produce
        a part nobody asked for. The bodies are still all there to look at.
        """
        result = self._run([
            self._box("a", 10, 10, 10),
            self._box("b", 10, 10, 10, px=10.0),
            self._box("orphan", 5, 5, 5, px=100.0, py=100.0, pz=100.0),
            {"id": "u", "type": "union", "target": "a", "tools": ["b"]},
        ])
        self.assertEqual({body.id for body in result.bodies}, {"a", "orphan"})
        volumes = {body.id: body.measurement.volume for body in result.bodies}
        self.assertAlmostEqual(volumes["a"], 2000.0, places=6)
        self.assertAlmostEqual(volumes["orphan"], 125.0, places=6)

    def test_a_union_that_leaves_exactly_one_solid_still_builds(self):
        """The rule must not cost the capability it exists to protect."""
        result = self._run([
            self._box("a", 10, 10, 10),
            self._box("b", 10, 10, 10, px=10.0),
            {"id": "u", "type": "union", "target": "a", "tools": ["b"]},
        ])
        self.assertTrue(result.succeeded, result.failure)
        self.assertEqual(result.part, "a")
        self.assertAlmostEqual(result.bodies[0].measurement.volume,
                               20.0 * 10.0 * 10.0, places=6)
        self.assertEqual(result.bodies[0].measurement.solid_count, 1)

    def test_the_render_is_taken_from_the_one_body_not_the_first(self):
        """`build.py` asked for `bodies[0]`; it now asks for `result.part`.

        `part` is the single live body or `None`, so the render cannot be
        built from "whichever body happened to be first" even if some future
        path reaches it without the check above.
        """
        import inspect
        from cad_experimental import build as build_module

        source = inspect.getsource(build_module)
        self.assertNotIn("result.bodies[0]", source)
        self.assertIn("result.part", source)


if __name__ == "__main__":
    unittest.main()
