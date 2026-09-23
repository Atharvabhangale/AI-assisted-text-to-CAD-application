"""Stage 51: provider encodings are explicit, proven, and never automatic.

The canonical operation plan is one language with one meaning. A provider
encoding is one *representation* of it, narrowed to fit a grammar compiler
that refuses the whole vocabulary. These tests hold the distinction: the IR
does not move, an encoding never substitutes itself for another, and an
operation a grammar cannot name is still perfectly legal in a plan.

Every size and verdict asserted here was measured against the live API in
Stage 50/51. Not one test makes a network call.
"""

from __future__ import annotations

import unittest

from cad_experimental import plan as canonical
from cad_experimental import schema_ladder as ladder
from cad_experimental import stage48_capability_evaluation as stage48
from cad_experimental import stage48_corpus as corpus
from cad_experimental.parser import PlanParseError, parse_plan


class MeasuredFingerprintTests(unittest.TestCase):
    """Each encoding must stay byte-identical to the shape that was probed.

    A recorded acceptance is attributable to one exact schema. If the
    fingerprint moves, the evidence no longer describes what the code emits
    and the encoding must be re-probed before it is trusted again.
    """

    EXPECTED = {
        "profile": ("85135abe3d3eaab3", 3487),
        "profile_hole": ("8b48ec4c96177614", 4030),
        "profile_union": ("a5c3484f182bc0c7", 4481),
    }

    def facts(self, name: str):
        return ladder.grammar_metrics(stage48.PLAN_SCHEMAS[name]())

    def test_each_encoding_keeps_its_probed_fingerprint(self) -> None:
        for name, (fingerprint, _) in self.EXPECTED.items():
            self.assertTrue(
                self.facts(name)["fingerprint"].startswith(fingerprint), name
            )

    def test_each_encoding_keeps_its_measured_size(self) -> None:
        for name, (_, inlined) in self.EXPECTED.items():
            self.assertEqual(
                self.facts(name)["inlined_characters"], inlined, name
            )

    def test_a_fingerprint_is_deterministic(self) -> None:
        for name in self.EXPECTED:
            self.assertEqual(
                self.facts(name)["fingerprint"],
                self.facts(name)["fingerprint"],
            )

    def test_the_proven_sizes_agree_with_the_schemas(self) -> None:
        for name, inlined in stage48.PROVEN_COMPILABLE.items():
            self.assertEqual(
                ladder.grammar_metrics(
                    stage48.PLAN_SCHEMAS[name]())["inlined_characters"],
                inlined, name,
            )

    def test_every_proven_encoding_is_below_the_proven_refusal(self) -> None:
        """4551 was refused. Nothing claimed compilable may reach it."""
        for name, inlined in stage48.PROVEN_COMPILABLE.items():
            self.assertLess(inlined, 4551, name)

    def test_the_refused_encodings_are_recorded_as_refused(self) -> None:
        self.assertEqual(
            set(stage48.PROVEN_REFUSED), {"provider", "compact"}
        )
        self.assertFalse(
            set(stage48.PROVEN_REFUSED) & set(stage48.PROVEN_COMPILABLE)
        )


class CanonicalIrIsUnchangedTests(unittest.TestCase):

    def test_the_language_has_eleven_operations(self) -> None:
        """Ten until `union` was implemented for the plate assembly.

        A count, spelled out, so growing the vocabulary is always a
        deliberate edit here and never a silent one.
        """
        self.assertEqual(len(canonical.OPERATION_TYPES), 11)
        self.assertIn("union", canonical.OPERATION_TYPES)

    def test_plan_schema_still_describes_every_one(self) -> None:
        schema = canonical.plan_schema()
        self.assertEqual(
            len(schema["properties"]["operations"]["items"]["anyOf"]),
            len(canonical.OPERATION_TYPES),
        )

    #: What `provider` and `compact` measured before `union` existed. Kept
    #: as history rather than overwritten: Stage 49 refused both at these
    #: sizes, and a recorded measurement describes the object that was
    #: measured, not whatever the name points at later.
    PRE_UNION_SIZES = {"compact": 6190, "provider": 7351}

    def test_the_frozen_encoding_is_untouched(self) -> None:
        """`executable` is the one that may never move.

        Stage 43 was run against it and a recorded result must stay
        attributable to the instrument that produced it. `union` is
        deliberately **not** in ``V1_FEATURE_TYPES``, so this encoding is
        byte-identical across the whole assembly milestone -- which is the
        check, not a hope.
        """
        self.assertEqual(
            ladder.grammar_metrics(
                canonical.executable_schema())["inlined_characters"], 3622)
        self.assertNotIn("union", canonical.V1_FEATURE_TYPES)

    def test_every_proven_compilable_encoding_is_untouched(self) -> None:
        """None of the six may move either: each was accepted at its size."""
        for name, inlined in stage48.PROVEN_COMPILABLE.items():
            self.assertEqual(
                ladder.grammar_metrics(
                    stage48.PLAN_SCHEMAS[name]())["inlined_characters"],
                inlined, name,
            )

    #: Encodings whose size is recorded in their own docstring but which are
    #: NOT in `PROVEN_COMPILABLE`, because no live probe has accepted them.
    #: Pinned all the same: a figure written down as "measured" must keep
    #: describing the object it measured, and `pattern_provider_schema`
    #: drifted 4445 -> 4454 unnoticed when `union` joined EXECUTABLE_TYPES
    #: precisely because nothing here pinned it.
    DOCUMENTED_SIZES = {
        "pattern_provider_schema": 4454,
        "strict_selector_union_provider_schema": 3628,
    }

    def test_the_documented_but_unproven_sizes_are_pinned(self) -> None:
        for name, inlined in self.DOCUMENTED_SIZES.items():
            with self.subTest(name):
                schema = getattr(canonical, name)()
                self.assertEqual(
                    ladder.grammar_metrics(schema)["inlined_characters"],
                    inlined, name,
                )
                # Each states its own size in its docstring. If the two ever
                # disagree the docstring is the thing that lied, so assert
                # the number is actually written there.
                self.assertIn(str(inlined), getattr(canonical, name).__doc__,
                              f"{name} docstring does not state {inlined}")

    def test_the_live_route_can_express_union(self) -> None:
        """The grammar the live path sends must admit what the prompt teaches.

        Prompt 2026-09-17.1 teaches `union` as how a multi-plate part is
        built and removed it from the UNSUPPORTED list. Decoding that against
        a grammar with no union branch makes every multi-plate request a
        FORCED refusal, which a run then records as the model's judgement --
        Stage 44's defect for `sketch`, Stage 48's for `pattern`, and this
        one for `union`.

        Asserted against `generation`'s own choice rather than a name typed
        here, so repointing the route at a narrower grammar fails this test.
        """
        from cad_experimental import generation, prompt

        schema = stage48.PLAN_SCHEMAS[generation.PLAN_SCHEMA_NAME]()
        kinds = set()
        for branch in schema["properties"]["operations"]["items"]["anyOf"]:
            spec = branch["properties"]["type"]
            if "const" in spec:
                kinds.add(spec["const"])
            kinds.update(spec.get("enum", ()))
        self.assertIn("union", kinds,
                      f"{generation.PLAN_SCHEMA_NAME} cannot say `union`")

        # ...and the prompt really does teach it, so this test keeps testing
        # something if the prompt ever goes back.
        self.assertIn("union", prompt.system_prompt())

    def test_the_two_refused_encodings_grew_only_slightly(self) -> None:
        """`provider` and `compact` cover the WHOLE vocabulary, so a new
        operation necessarily changes them -- but not by a branch.

        Adding `union` took both to nine branches, one past the ceiling
        Stage 41 measured, which would have made the grammar refuse for a
        new reason. Merging `subtract` with `union` brought them back to
        eight: the two share an operation-level shape exactly, so the
        merged branch describes each of them as its own branch did.

        Eleven operation types in eight branches, then, and the cost is a
        handful of characters rather than a branch.
        """
        for name in stage48.PROVEN_REFUSED:
            schema = stage48.PLAN_SCHEMAS[name]()
            now = ladder.grammar_metrics(schema)["inlined_characters"]
            self.assertGreater(now, self.PRE_UNION_SIZES[name], name)
            self.assertLess(now, self.PRE_UNION_SIZES[name] + 50, name)
            self.assertEqual(
                len(schema["properties"]["operations"]["items"]["anyOf"]),
                8, name)
            self.assertIn("union", stage48.SCHEMA_CAPABILITIES[name], name)

    def test_no_encoding_invents_an_operation(self) -> None:
        """An encoding may only name something the LANGUAGE has.

        Against `PLAN_TYPES`, not `OPERATION_TYPES`, since Stage 75. The
        intent is unchanged and is the point of the test: a capability list
        must not invent a type the parser would refuse. What changed is that
        the language has a second tier -- `part` is a DECLARATION, in
        `DECLARATION_TYPES` and therefore in `PLAN_TYPES`, and the parser
        gates on `PLAN_TYPES` (parser.py: `if kind not in PLAN_TYPES`).
        Keeping the assertion at `OPERATION_TYPES` would have forced `part`
        into that tuple to register an encoding, which moves nine recorded
        fingerprints and the prompt in one edit -- exactly what the
        declaration tier exists to prevent.
        """
        known = set(canonical.PLAN_TYPES)
        self.assertEqual(
            known - set(canonical.OPERATION_TYPES),
            set(canonical.DECLARATION_TYPES),
            "PLAN_TYPES may widen by declarations only",
        )
        for name, caps in stage48.SCHEMA_CAPABILITIES.items():
            self.assertTrue(set(caps) <= known, name)

    def test_only_the_stage75_encoding_can_say_part(self) -> None:
        """The declaration reaches exactly one grammar, on purpose.

        Every other encoding is a recorded measurement point, and a recorded
        measurement describes the object that was measured.
        """
        able = {
            name for name, caps in stage48.SCHEMA_CAPABILITIES.items()
            if canonical.PART in caps
        }
        self.assertEqual(able, {"strict_selector_union_part"})


class NoSilentFallbackTests(unittest.TestCase):
    """A caller gets the encoding it named, or an error. Never a substitute."""

    def test_the_default_is_unchanged_even_though_it_is_refused(self) -> None:
        """Changing it silently would rewrite what old runs meant."""
        self.assertEqual(stage48.DEFAULT_PLAN_SCHEMA, "provider")
        self.assertIn("provider", stage48.PROVEN_REFUSED)

    def test_every_name_maps_to_its_own_schema(self) -> None:
        seen = {}
        for name, builder in stage48.PLAN_SCHEMAS.items():
            fingerprint = ladder.grammar_metrics(builder())["fingerprint"]
            self.assertNotIn(
                fingerprint, seen,
                f"{name} and {seen.get(fingerprint)} are the same schema",
            )
            seen[fingerprint] = name

    def test_an_unknown_encoding_raises_rather_than_defaulting(self) -> None:
        with self.assertRaises(KeyError):
            stage48.schema_can_express("no_such_encoding", "box")

    def test_no_fallback_wording_exists_in_the_selector(self) -> None:
        import pathlib

        source = pathlib.Path(
            stage48.__file__).read_text(encoding="utf-8").lower()
        for phrase in ("except keyerror:\n        return provider_schema",
                       "fall back to", "falls back to"):
            self.assertNotIn(phrase, source)


class CapabilityEnvelopeTests(unittest.TestCase):

    def test_the_union_encoding_carries_seven_operations(self) -> None:
        self.assertEqual(
            set(stage48.SCHEMA_CAPABILITIES["profile_union"]),
            {"box", "cylinder", "through_hole", "subtract", "sketch",
             "extrude", "revolve"},
        )

    def test_the_union_dominates_the_two_narrower_profile_encodings(
        self,
    ) -> None:
        union = set(stage48.SCHEMA_CAPABILITIES["profile_union"])
        for name in ("profile", "profile_hole"):
            self.assertTrue(set(stage48.SCHEMA_CAPABILITIES[name]) <= union)

    def test_no_proven_encoding_can_express_a_pattern(self) -> None:
        """Measured: adding the edge pair reaches 4741, past the refusal."""
        for name in stage48.PROVEN_COMPILABLE:
            self.assertFalse(stage48.schema_can_express(name, "pattern"))

    def test_no_proven_encoding_carries_both_a_sketch_and_a_fillet(
        self,
    ) -> None:
        for name in stage48.PROVEN_COMPILABLE:
            self.assertFalse(
                stage48.schema_can_express(name, "sketch")
                and stage48.schema_can_express(name, "fillet"),
                name,
            )


class PartitionTests(unittest.TestCase):
    """The partition is derived from the corpus, never guessed."""

    def operations(self, case) -> tuple:
        return tuple(case.required_plan_operations or ())

    def test_a_case_needing_a_pattern_fits_no_proven_encoding(self) -> None:
        for identifier in ("E1-bolt-circle-radial", "E2-hole-row-linear"):
            case = next(
                c for c in corpus.CASES if c.identifier == identifier)
            self.assertEqual(
                stage48.encodings_that_can_express(self.operations(case)), ()
            )

    def test_a_profile_case_fits_the_profile_encodings(self) -> None:
        case = next(
            c for c in corpus.CASES if c.identifier == "09-profile-extrude")
        fits = stage48.encodings_that_can_express(self.operations(case))
        self.assertIn("profile_union", fits)
        self.assertNotIn("executable", fits)

    def test_an_edge_treatment_case_fits_exactly_the_fillet_encodings(
        self,
    ) -> None:
        """Every proven grammar that has a fillet, and no other.

        Derived rather than listed: this assertion was written as
        ``("executable",)``, outgrown when Stage 53 added ``selector`` and
        again when Stage 55 added ``strict_selector``. The claim worth
        holding is not *which* encodings there are but that a fillet case
        fits exactly those carrying a fillet -- notably never a profile
        encoding, which is the part that matters.
        """
        case = next(
            c for c in corpus.CASES if c.identifier == "08-plate-fillet")
        expected = {
            name for name in stage48.PROVEN_COMPILABLE
            if stage48.schema_can_express(name, "fillet")
        }
        self.assertEqual(
            set(stage48.encodings_that_can_express(self.operations(case))),
            expected,
        )
        for name in ("profile", "profile_hole", "profile_union"):
            self.assertNotIn(name, expected)

    def test_a_refused_encoding_is_never_offered_as_a_home(self) -> None:
        for case in corpus.CASES:
            fits = stage48.encodings_that_can_express(
                self.operations(case))
            self.assertFalse(set(fits) & set(stage48.PROVEN_REFUSED))

    def test_including_refused_encodings_is_opt_in(self) -> None:
        fits = stage48.encodings_that_can_express(
            ("pattern",), proven_only=False)
        self.assertIn("provider", fits)

    def test_two_instruments_cover_every_expressible_case(self) -> None:
        """profile_union plus executable, and what is left over."""
        uncovered = []
        for case in corpus.CASES:
            operations = self.operations(case)
            if not operations:
                continue
            fits = set(stage48.encodings_that_can_express(operations))
            if not fits & {"profile_union", "executable"}:
                uncovered.append(case.identifier)
        self.assertEqual(
            sorted(uncovered),
            ["D3-profile-revolve-fillet", "E1-bolt-circle-radial",
             "E2-hole-row-linear", "F4-patterned-rims-chamfered"],
        )


class ParserRemainsAuthoritativeTests(unittest.TestCase):
    """An encoding narrows what may be SAID, never what is legal."""

    def test_an_operation_absent_from_an_encoding_still_parses(self) -> None:
        """`pattern` is in no proven encoding, and is still a legal plan."""
        self.assertFalse(
            stage48.schema_can_express("profile_union", "pattern"))
        parsed = parse_plan({
            "status": "generated",
            "summary": "a plate with a row of holes",
            "operations": [
                {"id": "p", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
                {"id": "h", "type": "through_hole", "target": "p",
                 "parameters": {"diameter": 6.0, "axis": "+Z",
                                "position": {"x": 20.0, "y": 30.0, "z": 0.0}}},
                {"id": "row", "type": "pattern", "source": "h",
                 "parameters": {
                     "count": 3,
                     "placement": {"kind": "linear", "axis": "+X",
                                   "spacing": 20.0}}},
            ],
        })
        self.assertEqual(len(parsed.operations), 3)

    def test_a_constraint_omitted_from_every_profile_encoding_is_legal(
        self,
    ) -> None:
        parsed = parse_plan({
            "status": "generated",
            "summary": "a constrained sketch",
            "operations": [{
                "id": "s1", "type": "sketch",
                "parameters": {
                    "plane": "XY",
                    "geometry": [{
                        "id": "l1", "type": "line",
                        "start": {"x": 0.0, "y": 0.0},
                        "end": {"x": 10.0, "y": 0.0}}],
                    "constraints": [{
                        "id": "k1", "type": "horizontal",
                        "geometry": "l1"}],
                },
            }],
        })
        self.assertEqual(len(parsed.operations), 1)

    def test_a_malformed_plan_is_still_rejected(self) -> None:
        with self.assertRaises(PlanParseError):
            parse_plan({
                "status": "generated", "summary": "bad",
                "operations": [{
                    "id": "b", "type": "box",
                    "parameters": {"x": 1.0, "y": 1.0, "z": 1.0,
                                   "invented": 2}}],
            })


class BaselinesUntouchedTests(unittest.TestCase):

    def test_the_protected_baselines_are_intact(self) -> None:
        check = stage48._baseline_check()
        self.assertTrue(check["intact"], check["files"])
        self.assertEqual(len(check["files"]), 7)


class SelectorExpressibilityTests(unittest.TestCase):
    """Stage 53: expressibility is decided on operations AND selectors.

    Stage 52 checked operation types only. Six cases whose chamfer needed a
    ``circular`` or ``straight`` selector therefore looked answerable under a
    grammar that had neither; the model said ``all``, the kernel failed, and
    the failure was recorded against the model. These tests pin the fix.
    """

    def case(self, identifier):
        return next(c for c in corpus.CASES if c.identifier == identifier)

    def test_the_selector_encoding_carries_every_mode(self) -> None:
        from cad_experimental.plan import SELECT_MODES
        self.assertEqual(
            set(stage48.SCHEMA_SELECTOR_MODES["selector"]), set(SELECT_MODES)
        )

    def test_a_profile_encoding_carries_no_selector_at_all(self) -> None:
        """Nothing in it selects an edge, so the modes are empty, not V1's."""
        for name in ("profile", "profile_hole", "profile_union"):
            self.assertEqual(stage48.SCHEMA_SELECTOR_MODES[name], ())

    def test_position_is_sayable_only_where_a_circular_mode_exists(
        self,
    ) -> None:
        self.assertTrue(stage48.schema_supports_position("selector"))
        self.assertFalse(stage48.schema_supports_position("executable"))
        self.assertFalse(stage48.schema_supports_position("profile_union"))

    def test_the_stage_52_mislabelled_cases_are_now_inexpressible(
        self,
    ) -> None:
        """Under `executable` these six looked answerable. They were not."""
        for identifier in ("D1-plate-hole-chamfer-long-edges",
                           "F1-drilled-plate-round-corners",
                           "F2-hole-rim-chamfer-top",
                           "F3-hole-rim-chamfer-bottom",
                           "G1-subtract-then-chamfer",
                           "G2-two-holes-then-chamfer"):
            report = stage48.case_expressibility(
                "executable", self.case(identifier))
            self.assertFalse(report["expressible"], identifier)
            self.assertEqual(report["missing_operations"], [], identifier)
            self.assertTrue(report["missing_selector_modes"], identifier)

    def test_the_selector_encoding_can_express_those_six(self) -> None:
        for identifier in ("D1-plate-hole-chamfer-long-edges",
                           "F1-drilled-plate-round-corners",
                           "F2-hole-rim-chamfer-top",
                           "F3-hole-rim-chamfer-bottom",
                           "G1-subtract-then-chamfer",
                           "G2-two-holes-then-chamfer"):
            self.assertTrue(
                stage48.schema_can_express_case(
                    "selector", self.case(identifier)),
                identifier,
            )

    def test_an_operation_limit_and_a_selector_limit_stay_separate(
        self,
    ) -> None:
        """F4 needs a pattern; the selector is fine. Report both truthfully."""
        report = stage48.case_expressibility(
            "selector", self.case("F4-patterned-rims-chamfered"))
        self.assertFalse(report["expressible"])
        self.assertEqual(report["missing_operations"], ["pattern"])
        self.assertEqual(report["missing_selector_modes"], [])

    def test_a_case_with_no_selector_is_unaffected(self) -> None:
        report = stage48.case_expressibility(
            "selector", self.case("01-plate-worded"))
        self.assertTrue(report["expressible"])
        self.assertEqual(report["required_selector_modes"], [])

    def test_the_selector_schema_keeps_its_probed_fingerprint(self) -> None:
        facts = ladder.grammar_metrics(stage48.PLAN_SCHEMAS["selector"]())
        self.assertTrue(facts["fingerprint"].startswith("893a912002fb6593"))
        self.assertEqual(facts["inlined_characters"], 3134)

    def test_widening_selectors_on_a_profile_encoding_is_a_no_op(self) -> None:
        """The premise Stage 52 proposed, disproved rather than assumed."""
        from cad_experimental.plan import (
            SELECT_MODES, MERGED_SCHEMA_GROUPS, _plan_document,
            profile_provider_schema)
        widened = _plan_document(
            ("box", "cylinder", "sketch", "extrude", "revolve"),
            merged=MERGED_SCHEMA_GROUPS,
            omit_parameters=("constraints",),
            selector_modes=SELECT_MODES)
        self.assertEqual(widened, profile_provider_schema())


class SelectorGuidanceTests(unittest.TestCase):
    """Stage 54: the prompt must derive selector parameters from the words.

    Stage 53 measured two failure modes and Stage 54 separated them. The
    axis rule fixed one; the position rule did not move the other, and the
    reason is recorded here so the clause is not mistaken for dead text and
    deleted.
    """

    def prompt(self) -> str:
        from cad_experimental.prompt import system_prompt
        return system_prompt()

    def test_the_axis_rule_states_that_an_axis_is_the_edge_direction(
        self,
    ) -> None:
        text = self.prompt()
        self.assertIn("WHICH AXIS", text)
        self.assertIn("direction the EDGE RUNS", text)

    def test_the_axis_rule_works_the_long_edge_case_from_dimensions(
        self,
    ) -> None:
        """D1/G2 chose Z for 'long edges'; the rule names the arithmetic."""
        text = self.prompt()
        self.assertIn("the long edges", text)
        self.assertIn("100 by 60 by 10", text)

    def test_the_position_rule_says_a_named_end_is_an_instruction(
        self,
    ) -> None:
        text = self.prompt()
        self.assertIn("WHICH END", text)
        self.assertIn("chamfers BOTH rims", text)

    def test_the_clarification_rule_no_longer_reads_as_omit_always(
        self,
    ) -> None:
        """The counter-instruction Stage 54 found and narrowed."""
        text = self.prompt()
        self.assertNotIn("those are optional, and omitting them", text)
        self.assertIn("When the description NAMES", text)

    def test_the_selector_schema_still_admits_position(self) -> None:
        """The field the model declines to use is genuinely sayable."""
        from cad_experimental.plan import selector_provider_schema
        schema = selector_provider_schema()
        selector = [
            value for value in schema["$defs"].values()
            if "select" in (value.get("properties") or {})
        ][0]
        self.assertIn("position", selector["properties"])
        self.assertEqual(
            selector["properties"]["position"]["enum"], ["top", "bottom"]
        )
        self.assertNotIn("position", selector["required"])


class SelectorComponentDiagnosticTests(unittest.TestCase):
    """Selector correctness decomposes; the single metric hides which half."""

    def components(self, expected_modes, expected_position, got):
        mode = got.get("select") in expected_modes
        position = got.get("position") == expected_position
        return {"mode": mode, "position": position,
                "fully": mode and position}

    def test_a_missing_position_is_a_position_failure_not_a_mode_failure(
        self,
    ) -> None:
        """Exactly what F2/F3 do: right mode, absent end."""
        result = self.components(
            ("circular",), "top",
            {"select": "circular", "axis": "Z", "position": None})
        self.assertTrue(result["mode"])
        self.assertFalse(result["position"])
        self.assertFalse(result["fully"])

    def test_a_correct_selector_scores_on_every_component(self) -> None:
        result = self.components(
            ("circular",), "top",
            {"select": "circular", "axis": "Z", "position": "top"})
        self.assertTrue(result["fully"])

    def test_top_and_bottom_are_distinguished(self) -> None:
        """They are volumetrically identical, so the selector must score."""
        self.assertFalse(self.components(
            ("circular",), "top",
            {"select": "circular", "position": "bottom"})["position"])

    def test_the_corpus_pins_no_axis_so_axis_is_scored_by_geometry(
        self,
    ) -> None:
        """Why Stage 53's 'axis correct 30/30' was vacuous."""
        case = next(c for c in corpus.CASES
                    if c.identifier == "D1-plate-hole-chamfer-long-edges")
        self.assertIsNone(getattr(case.selector, "axis", None))


class StrictSelectorEncodingTests(unittest.TestCase):
    """Stage 55: the end of a rim made structural in the provider encoding.

    The canonical language still permits a circular selector with no
    position. This encoding cannot say it. That asymmetry is the whole
    point, and these tests pin both halves of it.
    """

    def schema(self):
        from cad_experimental.plan import strict_selector_provider_schema
        return strict_selector_provider_schema()

    def branches(self):
        selector = [
            value for value in self.schema()["$defs"].values()
            if "anyOf" in value
        ]
        return {b["properties"]["select"]["const"]: b
                for b in selector[0]["anyOf"]}

    def test_the_selector_is_a_discriminated_union(self) -> None:
        self.assertEqual(
            set(self.branches()),
            {"all", "axis_parallel", "straight", "circular"},
        )

    def test_a_circular_selector_must_name_its_end(self) -> None:
        circular = self.branches()["circular"]
        self.assertIn("position", circular["required"])
        self.assertEqual(
            circular["properties"]["position"]["enum"], ["top", "bottom"]
        )

    def test_both_ends_are_representable(self) -> None:
        enum = self.branches()["circular"]["properties"]["position"]["enum"]
        self.assertIn("top", enum)
        self.assertIn("bottom", enum)

    def test_an_axis_is_required_for_straight_and_axis_parallel(self) -> None:
        for mode in ("straight", "axis_parallel"):
            self.assertIn("axis", self.branches()[mode]["required"], mode)

    def test_all_carries_neither_axis_nor_position(self) -> None:
        properties = self.branches()["all"]["properties"]
        self.assertEqual(set(properties), {"select"})

    def test_an_axis_stays_optional_on_circular(self) -> None:
        """The language's own rule: omit it to mean any axis."""
        circular = self.branches()["circular"]
        self.assertIn("axis", circular["properties"])
        self.assertNotIn("axis", circular["required"])

    def test_the_canonical_language_still_allows_a_positionless_circular(
        self,
    ) -> None:
        """The encoding is narrower than the IR. Prove the IR did not move."""
        parsed = parse_plan({
            "status": "generated",
            "summary": "both rims broken",
            "operations": [
                {"id": "p", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
                {"id": "h", "type": "through_hole", "target": "p",
                 "parameters": {"diameter": 20.0,
                                "position": {"x": 50.0, "y": 30.0,
                                             "z": 0.0}}},
                {"id": "b", "type": "chamfer", "target": "p",
                 "parameters": {"distance": 1.0,
                                "edges": {"select": "circular",
                                          "axis": "Z"}}},
            ],
        })
        self.assertEqual(len(parsed.operations), 3)

    def test_the_parser_accepts_a_named_end_too(self) -> None:
        parsed = parse_plan({
            "status": "generated",
            "summary": "top rim broken",
            "operations": [
                {"id": "p", "type": "box",
                 "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
                {"id": "h", "type": "through_hole", "target": "p",
                 "parameters": {"diameter": 20.0,
                                "position": {"x": 50.0, "y": 30.0,
                                             "z": 0.0}}},
                {"id": "b", "type": "chamfer", "target": "p",
                 "parameters": {"distance": 1.0,
                                "edges": {"select": "circular", "axis": "Z",
                                          "position": "top"}}},
            ],
        })
        self.assertEqual(len(parsed.operations), 3)

    def test_the_wire_format_is_unchanged(self) -> None:
        """Nothing decodes or translates: the branch IS canonical form."""
        circular = self.branches()["circular"]
        self.assertEqual(
            set(circular["properties"]), {"select", "position", "axis"}
        )

    def test_the_flat_selector_encoding_is_left_alone(self) -> None:
        """`selector` is a recorded instrument; Stage 53 must reproduce."""
        facts = ladder.grammar_metrics(
            stage48.PLAN_SCHEMAS["selector"]())
        self.assertEqual(facts["inlined_characters"], 3134)
        self.assertTrue(facts["fingerprint"].startswith("893a912002fb6593"))

    def test_the_strict_encoding_is_below_the_proven_ceiling(self) -> None:
        facts = ladder.grammar_metrics(self.schema())
        self.assertEqual(facts["inlined_characters"], 3619)
        self.assertLess(facts["inlined_characters"], 4481)

    def test_it_is_registered_as_its_own_named_encoding(self) -> None:
        self.assertIn("strict_selector", stage48.PLAN_SCHEMAS)
        self.assertEqual(
            stage48.SCHEMA_SELECTOR_MODES["strict_selector"],
            stage48.SCHEMA_SELECTOR_MODES["selector"],
        )


if __name__ == "__main__":
    unittest.main()
