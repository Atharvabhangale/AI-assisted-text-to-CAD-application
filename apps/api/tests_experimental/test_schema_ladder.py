"""Stage 49: the schema ladder measures; it must not change anything.

A provider schema is an *encoding* of the operation plan, not the plan. These
tests hold that line: the canonical IR is untouched, Stage 43's recorded
instrument still produces its recorded schema, the protected baselines are
still protected, and no scoring, corpus or methodology moved. What is new is
only a set of alternative encodings and a way to measure them.

Not one test here makes a network call.
"""

from __future__ import annotations

import json
import unittest

from cad_experimental import plan as canonical
from cad_experimental import representation_comparison as stage40
from cad_experimental import schema_ladder as ladder
from cad_experimental import stage43_structured_comparison as stage43
from cad_experimental import stage48_capability_evaluation as stage48
from cad_experimental.parser import PlanParseError, parse_plan


# --- the canonical IR is untouched ------------------------------------------


class CanonicalIrIsUnchangedTests(unittest.TestCase):
    """The ladder may re-encode the plan. It may not redefine it."""

    def test_the_vocabulary_is_eleven_operations(self) -> None:
        """Ten until `union` arrived. The ladder's own variants are frozen
        at the vocabulary they were measured against -- see below."""
        self.assertEqual(len(canonical.OPERATION_TYPES), 11)

    def test_the_v1_six_are_still_the_v1_six(self) -> None:
        self.assertEqual(
            canonical.V1_FEATURE_TYPES,
            ("box", "cylinder", "through_hole", "subtract", "fillet",
             "chamfer"),
        )

    def test_the_full_plan_schema_still_has_a_branch_per_type(self) -> None:
        """The faithful description of the language is not compressed."""
        schema = canonical.plan_schema()
        branches = schema["properties"]["operations"]["items"]["anyOf"]
        self.assertEqual(len(branches), len(canonical.OPERATION_TYPES))

    def test_the_ladder_adds_no_operation_type(self) -> None:
        known = set(canonical.OPERATION_TYPES)
        for item in ladder.variants():
            self.assertTrue(
                set(item.capabilities) <= known,
                f"{item.name} invents a type",
            )


# --- Stage 43 and the baselines stay exactly as recorded --------------------


class RecordedInstrumentsAreUnchangedTests(unittest.TestCase):

    def test_stage_43_still_sends_its_recorded_schema(self) -> None:
        self.assertEqual(
            stage43.plan_schema_for_provider(),
            stage40.sanitise_schema(canonical.executable_schema()),
        )

    def test_the_l0_rung_reproduces_the_stage_43_grammar(self) -> None:
        """L0 is the known-good point, so it must BE the known-good point."""
        self.assertEqual(
            ladder.variant("L0-executable").schema(),
            canonical.executable_schema(),
        )

    def test_c2_still_reproduces_the_schema_that_was_refused(self) -> None:
        """C2 is the variant the provider actually refused; keep it exact.

        It used to be asserted equal to ``compact_provider_schema()``, and
        that held only while the vocabulary stood still. `union` made them
        diverge **correctly**: the live schema follows the language, and a
        ladder variant is a recorded instrument that must keep describing
        the object whose refusal was measured. So the check is against the
        measurement, not against today's schema.
        """
        schema = ladder.variant("C2-no-constraints").schema()
        self.assertEqual(
            ladder.grammar_metrics(schema)["inlined_characters"],
            ladder.KNOWN_REFUSED_INLINED,
        )
        kinds = set()
        for branch in schema["properties"]["operations"]["items"]["anyOf"]:
            discriminator = branch["properties"]["type"]
            kinds |= set(
                [discriminator["const"]] if "const" in discriminator
                else discriminator["enum"]
            )
        self.assertNotIn("union", kinds)
        self.assertEqual(len(kinds), 10)

    def test_the_protected_baselines_are_still_intact(self) -> None:
        check = stage48._baseline_check()
        self.assertTrue(check["intact"], check["files"])
        self.assertEqual(len(check["files"]), 7)

    def test_the_stage_48_result_kind_is_untouched(self) -> None:
        self.assertEqual(stage48.STAGE, 48)
        self.assertTrue(stage48.STRUCTURED_OUTPUT_ENABLED)

    def test_stage_40_scoring_is_untouched(self) -> None:
        self.assertFalse(stage40.STRUCTURED_OUTPUT_ENABLED)


# --- the measurements are deterministic -------------------------------------


class DeterminismTests(unittest.TestCase):

    def test_every_variant_builds_the_same_schema_twice(self) -> None:
        for item in ladder.variants():
            self.assertEqual(item.schema(), item.schema(), item.name)

    def test_every_fingerprint_is_stable_across_calls(self) -> None:
        first = {i.name: ladder.grammar_metrics(i.schema())["fingerprint"]
                 for i in ladder.variants()}
        second = {i.name: ladder.grammar_metrics(i.schema())["fingerprint"]
                  for i in ladder.variants()}
        self.assertEqual(first, second)

    def test_fingerprints_are_unique_per_distinct_schema(self) -> None:
        seen = {}
        for item in ladder.variants():
            facts = ladder.grammar_metrics(item.schema())
            key = json.dumps(item.schema(), sort_keys=True)
            if facts["fingerprint"] in seen:
                self.assertEqual(seen[facts["fingerprint"]], key)
            seen[facts["fingerprint"]] = key

    def test_the_variant_order_is_fixed(self) -> None:
        self.assertEqual(
            [i.name for i in ladder.variants()],
            [i.name for i in ladder.variants()],
        )

    def test_an_unknown_variant_raises_rather_than_guessing(self) -> None:
        with self.assertRaises(KeyError):
            ladder.variant("nope")


# --- the size proxy models the compiler, not the byte count -----------------


class SizeProxyTests(unittest.TestCase):

    def test_inlining_a_ref_grows_the_measured_size(self) -> None:
        """A $ref does not shrink the compiled grammar, so it must not here."""
        for item in ladder.variants():
            facts = ladder.grammar_metrics(item.schema())
            self.assertGreaterEqual(
                facts["inlined_characters"], facts["serialized_characters"],
                item.name,
            )

    def test_expansion_removes_every_ref(self) -> None:
        expanded = ladder.expand_refs(canonical.provider_schema())
        text = json.dumps(expanded)
        self.assertNotIn("$ref", text)
        self.assertNotIn("$defs", text)

    def test_the_known_bounds_are_the_measured_ones(self) -> None:
        """Both bounds are recorded facts, and neither may drift.

        The accepted bound is still ``executable_schema()`` exactly --
        `union` is deliberately not a V1 feature, so that schema did not
        move at all. The refused bound belongs to the C2 variant, which is
        frozen; ``compact_provider_schema()`` has since grown `union` and is
        therefore a little larger, which is checked separately rather than
        by redefining what was measured.
        """
        self.assertEqual(
            ladder.grammar_metrics(canonical.executable_schema())[
                "inlined_characters"],
            ladder.KNOWN_ACCEPTED_INLINED,
        )
        self.assertEqual(
            ladder.grammar_metrics(
                ladder.variant("C2-no-constraints").schema())[
                "inlined_characters"],
            ladder.KNOWN_REFUSED_INLINED,
        )
        self.assertGreater(
            ladder.grammar_metrics(canonical.compact_provider_schema())[
                "inlined_characters"],
            ladder.KNOWN_ACCEPTED_INLINED,
        )

    def test_a_prediction_is_only_made_against_measured_points(self) -> None:
        rows = {r["variant"]: r for r in ladder.ladder_report()["variants"]}
        self.assertEqual(
            rows["L0-executable"]["predicted"], "at_or_below_known_accepted"
        )
        self.assertEqual(
            rows["C2-no-constraints"]["predicted"],
            "at_or_above_known_refused",
        )
        self.assertEqual(
            rows["C6-sketch-floor"]["predicted"],
            "unknown_between_the_bounds",
        )


# --- capability is preserved somewhere on the ladder ------------------------


class CapabilityTests(unittest.TestCase):
    """Phase 4: every capability must remain reachable by some variant."""

    REQUIRED = (
        "box", "cylinder", "through_hole", "subtract", "fillet", "chamfer",
        "sketch", "extrude", "revolve", "pattern",
    )

    def test_every_required_operation_is_in_at_least_one_variant(
        self,
    ) -> None:
        reachable = set()
        for item in ladder.variants():
            reachable.update(item.capabilities)
        for required in self.REQUIRED:
            self.assertIn(required, reachable)

    def test_at_least_one_variant_carries_the_whole_vocabulary(self) -> None:
        self.assertTrue(any(
            set(item.capabilities) == set(self.REQUIRED)
            for item in ladder.variants()
        ))

    def test_semantic_selectors_are_reachable(self) -> None:
        self.assertTrue(any(i.semantic_selectors for i in ladder.variants()))

    def test_the_ladder_adds_exactly_one_capability_per_rung(self) -> None:
        rungs = [i for i in ladder.variants() if i.name.startswith("L")]
        for lower, higher in zip(rungs, rungs[1:]):
            added = set(higher.capabilities) - set(lower.capabilities)
            self.assertLessEqual(
                len(added), 1,
                f"{higher.name} adds {added}; a rung must isolate one cause",
            )

    def test_a_sketch_carrying_variant_exists_below_the_refused_bound(
        self,
    ) -> None:
        """If none existed, no lean-sketch question could be asked at all."""
        candidates = [
            i for i in ladder.variants()
            if "sketch" in i.capabilities
            and ladder.grammar_metrics(i.schema())["inlined_characters"]
            < ladder.KNOWN_REFUSED_INLINED
        ]
        self.assertTrue(candidates)


# --- the parser stays authoritative over every encoding ---------------------


class ParserRemainsAuthoritativeTests(unittest.TestCase):
    """A compressed encoding must not become a lenient one.

    The merged branches are the risk: ``fillet`` and ``chamfer`` share one
    schema branch, so the grammar alone cannot stop a model emitting a
    fillet carrying a chamfer's parameter. The parser must, and does.
    """

    def plan(self, operation: dict) -> dict:
        return {
            "status": "generated",
            "summary": "a test",
            "operations": [
                {"id": "b1", "type": "box",
                 "parameters": {"x": 10.0, "y": 10.0, "z": 10.0}},
                operation,
            ],
        }

    def test_a_fillet_carrying_a_chamfer_parameter_is_rejected(self) -> None:
        with self.assertRaises(PlanParseError):
            parse_plan(self.plan({
                "id": "f1", "type": "fillet", "target": "b1",
                "parameters": {"distance": 1.0,
                               "edges": {"select": "all"}},
            }))

    def test_a_chamfer_carrying_a_fillet_parameter_is_rejected(self) -> None:
        with self.assertRaises(PlanParseError):
            parse_plan(self.plan({
                "id": "c1", "type": "chamfer", "target": "b1",
                "parameters": {"radius": 1.0,
                               "edges": {"select": "all"}},
            }))

    def test_an_unknown_operation_type_is_rejected(self) -> None:
        with self.assertRaises(PlanParseError):
            parse_plan(self.plan({
                "id": "x1", "type": "loft", "parameters": {},
            }))

    def test_an_unknown_parameter_is_rejected(self) -> None:
        with self.assertRaises(PlanParseError):
            parse_plan(self.plan({
                "id": "f1", "type": "fillet", "target": "b1",
                "parameters": {"radius": 1.0, "edges": {"select": "all"},
                               "invented": 3},
            }))

    def test_a_well_formed_fillet_still_parses(self) -> None:
        """The check must reject the malformed, not everything.

        Without this, every rejection test above would pass against a parser
        that refused its input for some unrelated reason -- which is exactly
        what happened on the first draft of these tests.
        """
        parsed = parse_plan(self.plan({
            "id": "f1", "type": "fillet", "target": "b1",
            "parameters": {"radius": 1.0, "edges": {"select": "all"}},
        }))
        self.assertEqual(len(parsed.operations), 2)

    def test_omitting_constraints_from_a_schema_does_not_ban_them(
        self,
    ) -> None:
        """An encoding narrows what may be SAID, never what is legal.

        ``C2``/``C3`` omit a sketch's ``constraints`` so a grammar-driven
        decoder cannot emit one. The language is unchanged: a plan that
        carries constraints still parses, because the parser is the
        authority and it never saw the schema.
        """
        parsed = parse_plan({
            "status": "generated",
            "summary": "a sketch with a constraint",
            "operations": [{
                "id": "s1", "type": "sketch",
                "parameters": {
                    "plane": "XY",
                    "geometry": [{
                        "id": "l1", "type": "line",
                        "start": {"x": 0.0, "y": 0.0},
                        "end": {"x": 10.0, "y": 0.0},
                    }],
                    "constraints": [{
                        "id": "k1", "type": "horizontal", "geometry": "l1",
                    }],
                },
            }],
        })
        self.assertEqual(len(parsed.operations), 1)


# --- the probe spends one call, deliberately --------------------------------


class ProbeDisciplineTests(unittest.TestCase):

    def test_there_is_no_flag_that_probes_every_variant(self) -> None:
        import pathlib

        source = pathlib.Path(ladder.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--probe-all", source)
        self.assertNotIn("probe_all", source)

    def test_listing_makes_no_call_and_succeeds(self) -> None:
        self.assertEqual(ladder.main(["--list"]), 0)

    def test_an_unknown_variant_is_refused_before_any_call(self) -> None:
        self.assertEqual(ladder.main(["--probe", "not-a-variant"]), 2)

    def test_it_refuses_to_write_into_a_protected_baseline(self) -> None:
        self.assertEqual(
            ladder.main([
                "--out",
                "docs/evaluation-baselines/stage43-structured-output/x.json",
            ]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
