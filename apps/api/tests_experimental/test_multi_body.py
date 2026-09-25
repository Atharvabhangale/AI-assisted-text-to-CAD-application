"""Distinct body identity: several independent bodies, as first-class things.

Stage 71, the first step of `docs/multi-body-design.md`. What it adds is one
operation -- `part` -- that DECLARES a live body is an intended body of the
result, and the semantics that follow from taking that seriously.

THE TWO FACTS THAT SHAPE EVERY TEST BELOW.

1. **A declaration, not an inference.** Two live bodies and no `part` is
   still a mistake and still fails with `multiple_solids`. Inferring "they
   must have meant two bodies" from two live solids is precisely the silent
   behaviour Stage 62 removed, when a plan that fused two boxes and left a
   third standing reported success and everything downstream kept
   `bodies[0]`. A leftover and a declared body are different facts; the plan
   says which, and a reader can see it.

2. **No recorded fingerprint moves, by construction.** `part` is deliberately
   NOT in `OPERATION_TYPES`: that tuple is the geometry vocabulary every
   provider encoding and the prompt are built from, so an entry there would
   have changed `plan_schema`, `provider_schema`, `compact_provider_schema`
   and the PROMPT in one edit -- teaching a live model a grammar whose
   semantics are not yet measured, which the design refuses outright. The
   separate `DECLARATION_TYPES` tier makes that a property of the design
   rather than a thing to remember, and the first class below is what would
   catch its loss.
"""

from __future__ import annotations

import hashlib
import json
import math
import unittest

from cad_experimental import plan as plan_module
from cad_experimental import prompt as prompt_module
from cad_experimental import schema_ladder
from cad_experimental.build import build_plan
from cad_experimental.cad_backend import (
    BackendUnavailable, CadBackend, resolve_backend,
)
from cad_experimental.executor import MULTIPLE_SOLIDS, execute_plan
from cad_experimental.history import plan_history
from cad_experimental.normalize import Refusal, read_request
from cad_experimental.parser import PlanParseError, parse_plan_text
from cad_experimental.plan import (
    BUILDABLE_TYPES,
    DECLARATION_TYPES,
    EXECUTABLE_TYPES,
    EXECUTOR_ONLY_TYPES,
    MAX_BODIES,
    OPERATION_TYPES,
    PART,
    PLAN_TYPES,
    is_declaration,
)
from cad_experimental.validation import validate_plan

# --- the canonical two-body example, and its closed forms -------------------

#: A 40 mm cube, and a 20 mm diameter cylinder 30 mm long standing beside it.
CUBE_SIDE = 40.0
PIN_DIAMETER, PIN_HEIGHT = 20.0, 30.0
GAP = 10.0

CUBE_VOLUME = CUBE_SIDE ** 3
PIN_VOLUME = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_HEIGHT
TOLERANCE = 1e-6


def cube(identifier: str = "cube", side: float = CUBE_SIDE) -> dict:
    return {"id": identifier, "type": "box",
            "parameters": {"x": side, "y": side, "z": side}}


def pin(identifier: str = "pin") -> dict:
    return {"id": identifier, "type": "cylinder",
            "parameters": {"diameter": PIN_DIAMETER, "height": PIN_HEIGHT,
                           "position": {"x": CUBE_SIDE + GAP
                                             + PIN_DIAMETER / 2.0,
                                        "y": CUBE_SIDE / 2.0, "z": 0}}}


def declare(identifier: str, target: str) -> dict:
    return {"id": identifier, "type": PART, "target": target}


def plan_of(*operations: dict, summary: str = "two bodies") -> dict:
    return {"status": "generated", "summary": summary,
            "operations": list(operations)}


def parsed(*operations: dict):
    return parse_plan_text(json.dumps(plan_of(*operations)))


def two_bodies() -> tuple:
    return (cube(), pin(), declare("body_cube", "cube"),
            declare("body_pin", "pin"))


def built(*operations: dict, backend: CadBackend = None):
    plan = parsed(*operations)
    verdict = validate_plan(plan)
    assert verdict.valid, [(p.code, p.message) for p in verdict.problems]
    return execute_plan(plan, backend=backend or resolve_backend())


def measured(result, body_id: str):
    for body in result.bodies:
        if body.id == body_id:
            return body.measurement
    raise AssertionError(f"no body {body_id!r} in {[b.id for b in result.bodies]}")


class TheDeclarationTierChangesNoRecordedIdentityTests(unittest.TestCase):
    """Why `part` is not the twelfth entry in `OPERATION_TYPES`.

    Every number below was recorded by an earlier stage against a live
    provider or a live model, and the whole value of those records is that
    they still identify the thing that produced them.
    """

    #: Fingerprints and inlined sizes as Stages 43-70 measured them.
    RECORDED = {
        "executable_schema": ("54759d1e16cfe634", 3622),
        "strict_selector_union_provider_schema": ("07ab6305e836271e", 3628),
        "strict_selector_provider_schema": ("887718d3e5387529", 3619),
        "selector_provider_schema": ("893a912002fb6593", 3134),
        "profile_union_provider_schema": ("a5c3484f182bc0c7", 4481),
        "pattern_provider_schema": ("d54f6543afe0547a", 4454),
        "provider_schema": ("838aba85e5fa7587", 7360),
        "compact_provider_schema": ("c5936b06e6acba86", 6199),
        "plan_schema": ("34b6391fa9ce4700", 8710),
    }

    def test_part_is_not_in_the_geometry_vocabulary(self) -> None:
        self.assertNotIn(PART, OPERATION_TYPES)
        self.assertEqual(len(OPERATION_TYPES), 11)
        self.assertEqual(DECLARATION_TYPES, (PART,))
        self.assertEqual(PLAN_TYPES, OPERATION_TYPES + DECLARATION_TYPES)

    def test_no_schema_fingerprint_moved(self) -> None:
        for name, (fingerprint, inlined) in self.RECORDED.items():
            with self.subTest(schema=name):
                metrics = schema_ladder.grammar_metrics(
                    getattr(plan_module, name)())
                self.assertEqual(metrics["fingerprint"][:16], fingerprint)
                self.assertEqual(metrics["inlined_characters"], inlined)

    def test_no_schema_can_express_a_declaration(self) -> None:
        """The other half of the same guarantee, checked by reading them.

        A fingerprint match already implies this, but only to a reader who
        knows what the fingerprint covers. This says it outright: no encoding
        a provider is ever pointed at admits a `part` branch, so no
        grammar-constrained model can emit one, so nothing measured about a
        model's behaviour is affected by this stage.
        """
        for name in self.RECORDED:
            with self.subTest(schema=name):
                text = json.dumps(getattr(plan_module, name)())
                self.assertNotIn(f'"{PART}"', text)

    def test_the_prompt_now_teaches_the_declaration(self) -> None:
        """Stage 75 deliberately opened what Stages 71-74 kept shut.

        This test was `test_the_prompt_is_untouched`, and it asserted the
        opposite: version 2026-09-18.5, its fingerprint, and that "`part`"
        appeared nowhere in the prompt. That was correct for the
        deterministic slice, whose whole discipline was that the declaration
        tier moved NO recorded identity.

        The slice is complete (Stages 71-74) and the gates the design set are
        met, so Stage 75 teaches it. The schemas above are STILL pinned
        byte-for-byte -- only the prompt moved, and only on purpose.

        `2026-09-18.5` -> `2026-09-24.1` (Stage 75) ->
        `2026-09-24.2` (Phase C, the worked reply example).
        """
        self.assertEqual(prompt_module.PROMPT_VERSION, "2026-09-25.1")
        self.assertEqual(
            prompt_module.prompt_fingerprint(),
            "f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675",
        )
        text = prompt_module.system_prompt()
        self.assertIn("# Several bodies", text)
        self.assertIn('"type": "part"', text)

    def test_the_prompt_shows_a_whole_REPLY_not_only_operations(self) -> None:
        """Phase C's adopted change, and the gap it closed.

        Before it, `"status"` occurred EXACTLY ONCE in 33407 characters --
        in the bare field list -- and so did `"questions"`. Every one of the
        prompt's ~20 JSON blocks was a bare operation object, so the model
        had been told the fields of a reply and never shown one. Measured on
        72 fresh live calls: half the clarifications left `questions` empty
        and put a shrug in `summary` instead.

        One worked envelope took naming every body from 26% to 100% and
        carrying no operations from 68% to 100%, both p < 0.0001, on a
        confirmation sample, against a rule committed before any arm existed.
        """
        text = prompt_module.system_prompt()
        section = text.split("# When to say needs_clarification", 1)[1]
        section = section.split("# What you never do", 1)[0]
        for shown in ('"status"', '"summary"', '"questions"', '"operations"'):
            self.assertIn(shown, section,
                          f"the clarification example must show {shown}")
        self.assertIn('"operations": []', section)
        self.assertIn("needs_clarification", section)

    def test_the_clarification_example_names_its_bodies(self) -> None:
        """The example's question names two bodies by id.

        CB measured that a question naming NEITHER body works just as well
        (45/48 against CA's 47/48, p = 0.62), so this is not the active
        ingredient -- showing an envelope at all is. It is pinned anyway
        because it is what was measured and adopted, and an example that
        drifted to naming nothing would be a different prompt than the one
        the 72-call confirmation was run against.
        """
        text = prompt_module.system_prompt()
        section = text.split("# When to say needs_clarification", 1)[1]
        section = section.split("# What you never do", 1)[0]
        self.assertIn("`plate`", section)
        self.assertIn("`post`", section)

    def test_the_clarification_example_builds_nothing(self) -> None:
        """An example that shipped geometry would teach the failure it
        exists to remove. 23 of 72 baseline clarifications wrote a full
        four-operation sequence alongside the question."""
        text = prompt_module.system_prompt()
        section = text.split("# When to say needs_clarification", 1)[1]
        section = section.split("# What you never do", 1)[0]
        self.assertIn('"operations": []', section)
        for never in ('"type": "box"', '"type": "cylinder"',
                      '"type": "through_hole"', '"type": "part"'):
            self.assertNotIn(never, section,
                             "the clarification example must carry no operation")

    def test_the_prompt_shows_part_rather_than_describing_it(self) -> None:
        """Four stages measured the same thing: what the model imitates is
        what the prompt SHOWS. Stages 65, 66, 69 and 70 each found prose
        about a rule moving nothing. So the section must carry a worked
        example, and that example must be a complete, legal plan."""
        section = prompt_module.system_prompt().split("# Several bodies", 1)[1]
        section = section.split("# Units", 1)[0]
        for shown in ('"type": "part"', '"target": "plate"',
                      '"type": "through_hole"', '"type": "cylinder"'):
            self.assertIn(shown, section)

    def test_the_prompt_does_not_recommend_part_for_one_body(self) -> None:
        """`part` is a ROUTING switch: EXECUTOR_ONLY_TYPES is ('union',
        'part'), so any plan carrying one leaves the V1-document path and its
        process isolation. A prompt that offered `part` as general hygiene
        would move the whole product off that path silently."""
        section = prompt_module.system_prompt().split("# Several bodies", 1)[1]
        section = section.split("# Units", 1)[0]
        self.assertIn("ONE body means NO `part` at all", section)
        self.assertIn("Most parts are one body", section)

    def test_the_prompt_refuses_to_guess_which_body(self) -> None:
        section = prompt_module.system_prompt().split("# Several bodies", 1)[1]
        section = section.split("# Units", 1)[0]
        self.assertIn("do not choose one", section)
        self.assertIn("guessing is worse than asking", section)

    def test_the_single_solid_rule_is_qualified_not_deleted(self) -> None:
        """The default is unchanged and still stated. Deleting it would have
        taught the model that two standing solids are always fine, which is
        what rule P34 exists to refuse."""
        text = prompt_module.system_prompt()
        self.assertIn("exactly ONE solid left", text)
        self.assertIn("SEVERAL SEPARATE BODIES", text)

    def test_the_executable_set_still_means_what_it_says(self) -> None:
        """`part` builds nothing, so it is not executable -- it is buildable.

        Folding a declaration into `EXECUTABLE_TYPES` would have made that
        tuple's name a lie and forced a test that pins it to be weakened.
        """
        self.assertNotIn(PART, EXECUTABLE_TYPES)
        self.assertIn(PART, BUILDABLE_TYPES)
        self.assertEqual(
            set(BUILDABLE_TYPES),
            set(EXECUTABLE_TYPES) | set(DECLARATION_TYPES),
        )

    def test_the_executor_only_set_derives_the_declaration(self) -> None:
        """So routing needs no second edit anywhere.

        `part` has no V1 document form either -- V1 is single-body by rule
        S9 -- and being derived here is what sends a multi-body plan to the
        graph executor and makes the adapter refuse it with the right reason.
        """
        self.assertIn(PART, EXECUTOR_ONLY_TYPES)
        self.assertEqual(
            set(EXECUTOR_ONLY_TYPES),
            set(BUILDABLE_TYPES) - set(plan_module.V1_EXPRESSIBLE_TYPES),
        )


class TheDeclarationParsesAndRoundTripsTests(unittest.TestCase):

    def test_a_part_parses(self) -> None:
        operations = parsed(*two_bodies()).operations
        self.assertEqual([op.TYPE for op in operations],
                         ["box", "cylinder", PART, PART])
        self.assertTrue(is_declaration(operations[2]))
        self.assertEqual(operations[2].target, "cube")

    def test_it_round_trips_without_a_parameters_key(self) -> None:
        """A `part` carries one reference and nothing else.

        The old `operation_to_dict` wrote `parameters` unless the operation
        had tools, which was right for every type that existed and wrong for
        the first type with neither -- it would have emitted
        `{"parameters": {}}`, which this module's own parser rejects.
        """
        plan = parsed(*two_bodies())
        payload = plan.to_dict()["operations"][2]
        self.assertEqual(payload, {"id": "body_cube", "type": PART,
                                   "target": "cube"})
        self.assertEqual(len(parse_plan_text(json.dumps(
            plan.to_dict())).operations), 4)

    def test_it_requires_a_target(self) -> None:
        with self.assertRaises(PlanParseError):
            parsed(cube(), {"id": "d", "type": PART})

    def test_it_refuses_parameters(self) -> None:
        with self.assertRaises(PlanParseError):
            parsed(cube(), {"id": "d", "type": PART, "target": "cube",
                            "parameters": {}})


class BodyIdentityAndOwnershipTests(unittest.TestCase):
    """Identity, and whose feature is whose."""

    def setUp(self) -> None:
        self.history = plan_history(parsed(*two_bodies()))

    def test_both_bodies_are_live_and_declared(self) -> None:
        self.assertEqual([b.id for b in self.history.live_bodies],
                         ["cube", "pin"])
        self.assertEqual(self.history.declared_bodies, ("cube", "pin"))
        self.assertTrue(self.history.declares_bodies)
        self.assertTrue(self.history.is_declared("pin"))

    def test_a_declaration_is_nobody_s_feature(self) -> None:
        """It changes nothing, so it belongs to no body's history."""
        for body in self.history.bodies:
            self.assertNotIn("body_cube", body.features)
            self.assertNotIn("body_pin", body.features)
        self.assertIsNone(self.history.owner_of("body_cube"))

    def test_a_plan_that_declares_nothing_is_unchanged(self) -> None:
        history = plan_history(parsed(cube()))
        self.assertEqual(history.declared_bodies, ())
        self.assertFalse(history.declares_bodies)

    def test_features_stay_with_their_own_body(self) -> None:
        history = plan_history(parsed(
            cube(), pin(),
            {"id": "bore", "type": "through_hole", "target": "cube",
             "parameters": {"diameter": 8, "axis": "+Z",
                            "position": {"x": 20, "y": 20, "z": 0}}},
            declare("body_cube", "cube"), declare("body_pin", "pin"),
        ))
        self.assertEqual(history.body("cube").features, ("cube", "bore"))
        self.assertEqual(history.body("pin").features, ("pin",))
        self.assertEqual(history.owner_of("bore"), "cube")


class TheDeclarationRulesTests(unittest.TestCase):
    """P33-P35, and the rules that were deliberately NOT added."""

    def problems(self, *operations: dict):
        return [(p.code, p.message) for p
                in validate_plan(parsed(*operations)).problems]

    def test_two_declared_bodies_are_valid(self) -> None:
        self.assertEqual(self.problems(*two_bodies()), [])

    def test_two_undeclared_bodies_are_a_valid_PLAN(self) -> None:
        """Being several bodies is a fact about the RESULT, not the plan.

        This is the behaviour the Stage 63 pre-check recorded and the slice
        had to preserve: the plan is fine and only the execution refuses, so
        `part` makes an illegal RESULT legal without making an illegal plan
        legal.
        """
        self.assertEqual(self.problems(cube(), pin()), [])

    def test_declaring_a_body_twice_is_P33(self) -> None:
        codes = [c for c, _ in self.problems(
            *two_bodies(), declare("again", "cube"))]
        self.assertEqual(codes, ["P33"])

    def test_an_undeclared_body_left_standing_is_P34(self) -> None:
        codes = [c for c, _ in self.problems(
            cube(), pin(), declare("body_cube", "cube"))]
        self.assertEqual(codes, ["P34"])

    def test_a_declared_body_that_is_later_consumed_is_P34(self) -> None:
        """A declaration describes the finished result, not a stage of it."""
        found = self.problems(
            cube(), pin(), declare("body_pin", "pin"),
            {"id": "fuse", "type": "union", "target": "cube",
             "tools": ["pin"]},
            declare("body_cube", "cube"),
        )
        self.assertEqual([c for c, _ in found], ["P34"])
        self.assertIn("consumed it", found[0][1])

    def test_more_than_the_cap_is_P35(self) -> None:
        boxes = [cube(f"b{i}", 5) for i in range(MAX_BODIES + 1)]
        for index, box in enumerate(boxes):
            box["parameters"]["position"] = {"x": index * 10, "y": 0, "z": 0}
        declarations = [declare(f"d{i}", f"b{i}")
                        for i in range(MAX_BODIES + 1)]
        codes = [c for c, _ in self.problems(*boxes, *declarations)]
        self.assertIn("P35", codes)

    def test_the_target_is_judged_by_the_reference_rules(self) -> None:
        """P33 in the design doc turned out to be P9-P12, so it was not added.

        A declaration's target is a reference like every other reference in
        this language. Restating "names a live solid" as a fourth code would
        have been a second opinion about what `live` means -- the exact thing
        the one-walk-one-answer invariant exists to stop.
        """
        codes = [c for c, _ in self.problems(
            cube(), declare("d", "nothing_like_that"))]
        self.assertIn("P9", codes)

        codes = [c for c, _ in self.problems(
            cube(), pin(),
            {"id": "fuse", "type": "union", "target": "cube",
             "tools": ["pin"]},
            declare("d", "fuse"))]
        self.assertIn("P11", codes)


class TheExecutorGateKeepsItsTeethTests(unittest.TestCase):
    """One exemption, and the refusal it does not weaken."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    def test_two_declared_bodies_build(self) -> None:
        result = built(*two_bodies())
        self.assertTrue(result.succeeded, result.failure)
        self.assertEqual([b.id for b in result.bodies], ["cube", "pin"])
        self.assertEqual(result.declared, ("cube", "pin"))

    def test_each_body_measures_its_own_closed_form(self) -> None:
        result = built(*two_bodies())
        self.assertAlmostEqual(measured(result, "cube").volume,
                               CUBE_VOLUME, delta=TOLERANCE)
        self.assertAlmostEqual(measured(result, "pin").volume,
                               PIN_VOLUME, delta=TOLERANCE)
        for body_id in ("cube", "pin"):
            self.assertEqual(measured(result, body_id).solid_count, 1)

    def test_nothing_is_fused(self) -> None:
        """Two bodies, two solids, and the total is the sum -- not a union.

        A fuse of two shapes that do not touch would leave a compound; a fuse
        of two that do would lose volume to the overlap. Neither happened.
        """
        result = built(*two_bodies())
        self.assertEqual(len(result.bodies), 2)
        total = sum(measured(result, b.id).volume for b in result.bodies)
        self.assertAlmostEqual(total, CUBE_VOLUME + PIN_VOLUME,
                               delta=TOLERANCE)

    def test_undeclared_bodies_still_fail(self) -> None:
        plan = parsed(cube(), pin())
        result = execute_plan(plan, backend=resolve_backend())
        self.assertFalse(result.succeeded)
        self.assertEqual(result.failure.code, MULTIPLE_SOLIDS)
        for name in ("cube", "pin"):
            self.assertIn(repr(name), result.failure.message)
        self.assertEqual(result.declared, ())

    def test_part_stays_the_single_body_or_none(self) -> None:
        """Widening it to `bodies[0]` is the Stage 62 bug by another name."""
        self.assertIsNone(built(*two_bodies()).part)
        self.assertEqual(built(cube(), declare("d", "cube")).part, "cube")
        self.assertEqual(built(cube()).part, "cube")


class EditsDoNotLeakBetweenBodiesTests(unittest.TestCase):
    """The property multi-body exists to guarantee, checked on the kernel."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))
        self.baseline = built(*two_bodies())

    def bore(self, target: str) -> dict:
        return {"id": "bore", "type": "through_hole", "target": target,
                "parameters": {"diameter": 8, "axis": "+Z",
                               "position": {"x": 20, "y": 20, "z": 0}}}

    def assertUnchanged(self, result, body_id: str) -> None:
        was, now = measured(self.baseline, body_id), measured(result, body_id)
        self.assertAlmostEqual(now.volume, was.volume, delta=TOLERANCE)
        self.assertEqual(now.face_count, was.face_count)
        self.assertEqual(now.edge_count, was.edge_count)
        self.assertEqual(now.solid_count, was.solid_count)

    def test_a_feature_on_one_body_leaves_the_other_identical(self) -> None:
        result = built(cube(), pin(), self.bore("cube"),
                       declare("body_cube", "cube"), declare("body_pin", "pin"))
        self.assertUnchanged(result, "pin")
        self.assertLess(measured(result, "cube").volume, CUBE_VOLUME)

    def test_the_feature_belongs_to_one_body_s_history_only(self) -> None:
        history = plan_history(parsed(
            cube(), pin(), self.bore("cube"),
            declare("body_cube", "cube"), declare("body_pin", "pin")))
        self.assertIn("bore", history.body("cube").features)
        self.assertNotIn("bore", history.body("pin").features)

    def test_resizing_one_body_leaves_the_other_identical(self) -> None:
        """A resize in this language is the constructive operation restated."""
        result = built(cube(side=60.0), pin(),
                       declare("body_cube", "cube"), declare("body_pin", "pin"))
        self.assertUnchanged(result, "pin")
        self.assertAlmostEqual(measured(result, "cube").volume, 60.0 ** 3,
                               delta=TOLERANCE)

    def test_interleaving_two_chains_changes_no_shape(self) -> None:
        """The real test of body-locality, and it is cheap.

        Two independent chains, written in two different orders. If anything
        leaked between bodies, the order would matter.
        """
        grouped = built(
            cube(), self.bore("cube"), pin(),
            declare("body_cube", "cube"), declare("body_pin", "pin"))
        interleaved = built(
            cube(), pin(), self.bore("cube"),
            declare("body_pin", "pin"), declare("body_cube", "cube"))
        for body_id in ("cube", "pin"):
            self.assertAlmostEqual(measured(interleaved, body_id).volume,
                                   measured(grouped, body_id).volume,
                                   delta=TOLERANCE)
            self.assertEqual(measured(interleaved, body_id).face_count,
                             measured(grouped, body_id).face_count)

    def test_a_selector_on_one_body_cannot_see_the_other(self) -> None:
        """Scope, asserted rather than assumed.

        The executor holds one shape per body and asks the backend about the
        target's shape alone, so another body's edges cannot reach the
        resolver. A selector that matched *n* edges must still match *n* when
        an unrelated body is added -- the result must not depend on what else
        exists in the plan.
        """
        alone = built(cube(), {"id": "round", "type": "fillet",
                               "target": "cube",
                               "parameters": {"radius": 3,
                                              "edges": {"select": "straight",
                                                        "axis": "Z"}}})
        together = built(
            cube(), pin(),
            {"id": "round", "type": "fillet", "target": "cube",
             "parameters": {"radius": 3,
                            "edges": {"select": "straight", "axis": "Z"}}},
            declare("body_cube", "cube"), declare("body_pin", "pin"))
        self.assertEqual(len(alone.selections["round"].indices),
                         len(together.selections["round"].indices))
        self.assertEqual(alone.selections["round"].indices,
                         together.selections["round"].indices)
        self.assertAlmostEqual(measured(together, "cube").volume,
                               measured(alone, "cube").volume,
                               delta=TOLERANCE)
        self.assertUnchanged(together, "pin")


class EveryBodyReachesTheOutputTests(unittest.TestCase):
    """No output may fuse bodies the plan did not fuse, or drop one."""

    def setUp(self) -> None:
        try:
            self.backend = resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))
        self.build = build_plan(None, parsed(*two_bodies()),
                                name="two-body", backend=self.backend)

    def test_the_build_succeeds_on_the_graph_path(self) -> None:
        self.assertTrue(self.build.built)
        self.assertTrue(self.build.executed)

    def test_one_mesh_per_body_each_naming_its_body(self) -> None:
        self.assertEqual(sorted(self.build.renders), ["cube", "pin"])
        for body_id, render in self.build.renders.items():
            self.assertEqual(render.to_dict()["feature_id"], body_id)

    def test_no_merged_mesh_is_offered(self) -> None:
        """`render` means "the single body's mesh" and there is not one.

        A caller that drew it would otherwise put one body on screen and
        report a successful build of a two-body part.
        """
        self.assertIsNone(self.build.render)

    def test_a_single_body_build_still_fills_both(self) -> None:
        """So a caller has one way to draw both cases, not a special case."""
        one = build_plan(None, parsed(cube(), declare("d", "cube")),
                         name="one-body", backend=self.backend)
        self.assertIsNotNone(one.render)
        self.assertEqual(sorted(one.renders), ["cube"])


class BothKernelsAgreeTests(unittest.TestCase):
    """Bit-for-bit parity on the two-body part, per body."""

    def engines(self):
        found = []
        for name in ("cadquery", "freecad"):
            try:
                found.append(resolve_backend(name))
            except BackendUnavailable:
                continue
        return found

    def test_the_two_bodies_measure_the_same_on_every_engine(self) -> None:
        engines = self.engines()
        if len(engines) < 2:
            self.skipTest("only one backend is available in this interpreter")
        seen = {}
        for engine in engines:
            result = built(*two_bodies(), backend=engine)
            self.assertTrue(result.succeeded, result.failure)
            seen[engine.name] = {
                body.id: (body.measurement.volume, body.measurement.solid_count,
                          body.measurement.face_count,
                          body.measurement.edge_count)
                for body in result.bodies
            }
        names = list(seen)
        self.assertEqual(seen[names[0]], seen[names[1]])
        for body_id, expected in (("cube", CUBE_VOLUME), ("pin", PIN_VOLUME)):
            self.assertAlmostEqual(seen[names[0]][body_id][0], expected,
                                   delta=TOLERANCE)


class TheProviderNeutralRouteReachesItTests(unittest.TestCase):
    """A local grammar produces the canonical two-body plan. No model, no SDK."""

    REQUEST = ("Create a 40 mm cube and a 20 mm cylinder 30 mm long beside "
               "it as two separate bodies.")

    def test_the_reader_declares_both_bodies(self) -> None:
        reading = read_request(self.REQUEST)
        self.assertIsNotNone(reading)
        self.assertNotIsInstance(reading, Refusal)
        self.assertEqual(reading.reader, "separate_bodies")
        kinds = [op["type"] for op in reading.plan["operations"]]
        self.assertEqual(kinds, ["box", "cylinder", PART, PART])

    def test_it_goes_through_the_same_parser_and_validator(self) -> None:
        reading = read_request(self.REQUEST)
        plan = parse_plan_text(json.dumps(reading.plan))
        self.assertTrue(validate_plan(plan).valid)
        self.assertEqual(plan_history(plan).declared_bodies,
                         ("cube", "cylinder"))

    def test_it_declines_the_same_shapes_without_a_separateness_phrase(self):
        """Two shapes in one sentence is not a statement about bodies.

        It could equally mean a union, and a grammar that decided from a
        conjunction would be making a CAD decision out of English.
        """
        self.assertIsNone(read_request(
            "a 40 mm cube and a 20 mm cylinder 30 mm long beside it"))

    def test_a_missing_length_is_refused_rather_than_invented(self) -> None:
        refusal = read_request(
            "Create a 40 mm cube and a 20 mm cylinder beside it as two "
            "separate bodies.")
        self.assertIsInstance(refusal, Refusal)
        self.assertIn("length", refusal.reason)

    def test_the_reader_imports_no_vendor_sdk(self) -> None:
        import pathlib

        import cad_experimental.normalize as module
        source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("anthropic", "cad_ai", "openai", "google"):
            self.assertNotIn(forbidden, source)


class TheSingleBodyProductIsUnchangedTests(unittest.TestCase):
    """Backward compatibility, stated as the thing that would break."""

    def setUp(self) -> None:
        try:
            resolve_backend()
        except BackendUnavailable as exc:      # pragma: no cover
            self.skipTest(str(exc))

    #: The golden enclosure: six plates, one union, three bores.
    ENVELOPE = (40.0, 20.0, 20.0)
    THICKNESS = 5.0
    VOLUME = 11492.035526276899

    def enclosure(self) -> tuple:
        t, (x, y, z) = self.THICKNESS, self.ENVELOPE
        def plate(name, size, position=(0.0, 0.0, 0.0)):
            return {"id": name, "type": "box",
                    "parameters": {"x": size[0], "y": size[1], "z": size[2],
                                   "position": {"x": position[0],
                                                "y": position[1],
                                                "z": position[2]}}}
        def bore(name, axis, position):
            return {"id": name, "type": "through_hole", "target": "base",
                    "parameters": {"diameter": 8.0, "axis": axis,
                                   "position": {"x": position[0],
                                                "y": position[1],
                                                "z": position[2]}}}
        return (
            plate("base", (x, y, t)), plate("lid", (x, y, t), (0, 0, z - t)),
            plate("front", (x, t, z)), plate("back", (x, t, z), (0, y - t, 0)),
            plate("left", (t, y, z)), plate("right", (t, y, z), (x - t, 0, 0)),
            {"id": "fuse", "type": "union", "target": "base",
             "tools": ["lid", "front", "back", "left", "right"]},
            bore("hole_z", "+Z", (x / 2, y / 2, 0)),
            bore("hole_y", "+Y", (x / 2, 0, z / 2)),
            bore("hole_x", "+X", (0, y / 2, z / 2)),
        )

    def test_the_golden_enclosure_is_untouched(self) -> None:
        result = built(*self.enclosure())
        self.assertTrue(result.succeeded, result.failure)
        self.assertEqual(len(result.bodies), 1)
        self.assertEqual(result.part, "base")
        self.assertEqual(result.declared, ())
        body = measured(result, "base")
        self.assertAlmostEqual(body.volume, self.VOLUME, delta=TOLERANCE)
        self.assertEqual((body.face_count, body.edge_count), (18, 42))

    def test_a_plain_single_body_plan_declares_nothing(self) -> None:
        result = built(cube())
        self.assertEqual(result.declared, ())
        self.assertEqual(result.part, "cube")
        self.assertTrue(validate_plan(parsed(cube())).valid)


if __name__ == "__main__":
    unittest.main()
