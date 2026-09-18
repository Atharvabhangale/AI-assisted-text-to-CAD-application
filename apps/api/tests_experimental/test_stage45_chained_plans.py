"""Stage 45: the plan as a dependency and history graph.

The chain semantics were already there. Rules P9-P14 and P23 have always
judged a reference against a walked solid set, so `box -> cylinder ->
subtract -> fillet` validated correctly and converted to a valid V1 document
before this stage existed.

What did not exist was anywhere to *see* the chain. The walk lived inside
`validate_plan` as four local dictionaries, so a plan could be judged as a
history and then only ever reported as a flat list -- and one consequence of
that was invisible: a leftover solid in a plan containing a profile operation
is caught by nobody, because the adapter refuses such a plan before any
document exists and S9 therefore never runs.

This module covers the graph itself, the fact that there is exactly one walk
rather than two, and the chain behaviour the graph makes visible.
"""

from __future__ import annotations

import ast
import json
import pathlib
import unittest

from fastapi.testclient import TestClient

from cad_experimental.adapter import ExecutionUnsupported, plan_to_document
from cad_experimental.app import VALIDATE_PATH, create_app
from cad_experimental.config import ExperimentalConfig
from cad_experimental.history import PlanHistory, plan_history, walk
from cad_experimental.parser import parse_plan
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

CONFIG = ExperimentalConfig(model="stub-model")
SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "cad_experimental"


def op(identifier, kind, **fields):
    return dict(id=identifier, type=kind, **fields)


def box(identifier, x=20.0, y=20.0, z=20.0, **fields):
    return op(identifier, "box", parameters=dict(x=x, y=y, z=z, **fields))


def cylinder(identifier, diameter=10.0, height=40.0, **fields):
    return op(identifier, "cylinder",
              parameters=dict(diameter=diameter, height=height, **fields))


def subtract(identifier, target, tools):
    return op(identifier, "subtract", target=target, tools=list(tools))


def fillet(identifier, target, radius=2.0):
    return op(identifier, "fillet", target=target, parameters={
        "radius": radius, "edges": {"select": "axis_parallel", "axis": "Z"}})


def chamfer(identifier, target, distance=1.0):
    return op(identifier, "chamfer", target=target, parameters={
        "distance": distance, "edges": {"select": "all"}})


def hole(identifier, target, diameter=6.0, x=0.0, y=0.0):
    return op(identifier, "through_hole", target=target, parameters={
        "diameter": diameter, "position": {"x": x, "y": y, "z": 0.0}})


def sketch(identifier="profile", plane="XY"):
    return op(identifier, "sketch", parameters={
        "plane": plane,
        "geometry": [{"id": "r1", "type": "rectangle",
                      "corner": {"x": 0.0, "y": 0.0},
                      "width": 80.0, "height": 40.0}]})


def extrude(identifier, target, distance=12.0):
    return op(identifier, "extrude", target=target,
              parameters={"distance": distance})


def plan_of(*operations, status="generated"):
    return parse_plan({
        "status": status, "summary": "a chained part",
        "operations": [dict(o) for o in operations],
    })


#: The canonical chain: make a body, make a cutter, remove it, round what is
#: left. Written once and reused, so every test below describes the same
#: part.
BASE_CHAIN = (
    box("body", 60.0, 40.0, 8.0),
    cylinder("cutter"),
    subtract("cut", "body", ["cutter"]),
    fillet("edges", "body"),
)


# --- 1. the graph ----------------------------------------------------------


class TheHistoryGraphTests(unittest.TestCase):
    def setUp(self):
        self.history = plan_history(plan_of(*BASE_CHAIN))

    def test_every_operation_gets_a_step_in_plan_order(self):
        self.assertEqual([s.id for s in self.history.steps],
                         ["body", "cutter", "cut", "edges"])
        self.assertEqual([s.index for s in self.history.steps], [0, 1, 2, 3])

    def test_a_constructive_operation_declares_a_solid_and_depends_on_nothing(self):
        step = self.history.step("body")
        self.assertEqual(step.declares_solid, "body")
        self.assertIsNone(step.modifies)
        self.assertEqual(step.depends_on, ())

    def test_a_subtract_depends_on_its_target_then_its_tools(self):
        """Target first, tools in list order: Section C.4 removes them in
        that order, so the edges are not a set."""
        self.assertEqual(self.history.step("cut").depends_on,
                         ("body", "cutter"))

    def test_a_modifier_declares_nothing_and_names_what_it_changed(self):
        for identifier in ("cut", "edges"):
            with self.subTest(identifier=identifier):
                step = self.history.step(identifier)
                self.assertIsNone(step.declares_solid)
                self.assertEqual(step.modifies, "body")

    def test_the_solid_set_evolves_step_by_step(self):
        self.assertEqual(
            [(s.solids_before, s.solids_after) for s in self.history.steps],
            [
                ((), ("body",)),
                (("body",), ("body", "cutter")),
                (("body", "cutter"), ("body",)),
                (("body",), ("body",)),
            ],
        )

    def test_the_subtract_is_the_step_that_consumes(self):
        self.assertEqual(self.history.step("cut").consumes, ("cutter",))
        for identifier in ("body", "cutter", "edges"):
            with self.subTest(identifier=identifier):
                self.assertEqual(self.history.step(identifier).consumes, ())

    def test_the_plan_ends_with_one_solid(self):
        self.assertEqual(self.history.terminal_solids, ("body",))
        self.assertEqual(self.history.consumed, ("cutter",))

    def test_a_consumed_tool_is_not_a_terminal_solid(self):
        self.assertNotIn("cutter", self.history.terminal_solids)

    def test_dependents_are_the_reverse_edges(self):
        self.assertEqual(self.history.dependents("body"), ("cut", "edges"))
        self.assertEqual(self.history.dependents("cutter"), ("cut",))
        self.assertEqual(self.history.dependents("edges"), ())

    def test_the_derivation_includes_a_tool_that_no_longer_exists(self):
        """The property that makes this a history rather than a list: the
        cutter is part of what `body` is made of, though it was consumed two
        steps ago and names nothing now."""
        self.assertEqual(self.history.derivation("body"),
                         ("body", "cutter", "cut", "edges"))

    def test_the_derivation_of_an_unknown_id_is_empty(self):
        self.assertEqual(self.history.derivation("nothing"), ())

    def test_producers_are_the_edit_history_of_one_solid(self):
        self.assertEqual(self.history.producers("body"), (0, 2, 3))
        self.assertEqual(self.history.producers("cutter"), (1,))


class DepthTests(unittest.TestCase):
    """Depth measures chaining, which a count of operations does not."""

    def test_a_single_operation_has_depth_one(self):
        self.assertEqual(plan_history(plan_of(box("a"))).depth, 1)

    def test_unrelated_operations_stay_at_depth_one(self):
        """Four boxes is a bag, not a chain, however long the list is."""
        history = plan_history(
            plan_of(box("a"), box("b"), box("c"), box("d"))
        )
        self.assertEqual(len(history.steps), 4)
        self.assertEqual(history.depth, 1)

    def test_a_chain_is_as_deep_as_it_is_long(self):
        self.assertEqual(plan_history(plan_of(*BASE_CHAIN)).depth, 3)

    def test_each_further_modifier_adds_one(self):
        history = plan_history(plan_of(*BASE_CHAIN,
                                       chamfer("bevel", "body")))
        self.assertEqual(history.depth, 4)

    def test_an_empty_plan_has_depth_zero(self):
        self.assertEqual(PlanHistory().depth, 0)


# --- 2. one walk, not two --------------------------------------------------


class OneSimulationTests(unittest.TestCase):
    """The validator and the graph must not each keep their own idea of
    what a subtract consumed."""

    def test_the_validator_uses_the_shared_walk(self):
        source = (SOURCE / "validation.py").read_text(encoding="utf-8")
        self.assertIn("from .history import walk", source)
        self.assertIn("for index, operation, state in walk(", source)

    def test_the_validator_no_longer_advances_the_solid_set_itself(self):
        """The four lines that used to be a second copy of Section B.4."""
        source = (SOURCE / "validation.py").read_text(encoding="utf-8")
        for gone in ("live.setdefault(", "live.pop(", "consumed[tool] ="):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, source)

    def test_the_walk_and_the_graph_agree_on_every_chain(self):
        """Belt and braces: whatever the walk ends with is what the graph
        reports, on plans that consume, modify and sweep."""
        for label, operations in (
            ("solid chain", BASE_CHAIN),
            ("two cutters", (box("body"), cylinder("a"), cylinder("b"),
                             subtract("cut", "body", ["a", "b"]))),
            ("profile chain", (sketch(), extrude("solid", "profile"),
                               hole("drill", "solid"))),
            ("leftover", (box("body"), box("spare"))),
        ):
            with self.subTest(label=label):
                plan = plan_of(*operations)
                final = None
                for _, _, state in walk(plan.operations):
                    final = state
                # `state` is the walk's own mapping and has advanced past the
                # last operation by the time the loop ends.
                self.assertEqual(
                    tuple(sorted(final.solids)),
                    tuple(sorted(plan_history(plan).terminal_solids)),
                )

    def test_the_consumption_rule_is_written_once(self):
        source = (SOURCE / "history.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("def _consumed_by("), 1)


# --- 3. longer chains still validate and convert ---------------------------


class LongerChainsTests(unittest.TestCase):
    def test_a_five_operation_solid_chain_is_valid(self):
        plan = plan_of(
            box("body", 60.0, 40.0, 8.0),
            hole("drill", "body", 6.0, 10.0, 10.0),
            cylinder("cutter"),
            subtract("cut", "body", ["cutter"]),
            fillet("edges", "body"),
        )
        verdict = validate_plan(plan)
        self.assertTrue(verdict.valid, verdict.to_dict())
        history = plan_history(plan)
        self.assertEqual(history.terminal_solids, ("body",))
        self.assertEqual(history.depth, 4)

    def test_every_modifier_keeps_naming_the_original_id(self):
        """The rule a chain lives or dies by: a modifier's own id names no
        solid, so `cut` and `edges` are never valid targets."""
        plan = plan_of(*BASE_CHAIN)
        history = plan_history(plan)
        for identifier in ("cut", "edges"):
            with self.subTest(identifier=identifier):
                self.assertIsNone(history.step(identifier).declares_solid)
                self.assertNotIn(identifier, history.terminal_solids)

    def test_targeting_a_modifier_is_still_rejected(self):
        verdict = validate_plan(plan_of(*BASE_CHAIN, fillet("more", "cut")))
        self.assertFalse(verdict.valid)
        self.assertIn("P11", [p.code for p in verdict.problems])

    def test_reusing_a_consumed_tool_is_still_rejected(self):
        verdict = validate_plan(
            plan_of(*BASE_CHAIN, subtract("again", "body", ["cutter"]))
        )
        self.assertFalse(verdict.valid)
        self.assertIn("P12", [p.code for p in verdict.problems])

    def test_a_chain_converts_to_a_valid_v1_document(self):
        from cad_core.validator import validate

        document = plan_to_document(plan_of(*BASE_CHAIN))
        self.assertEqual([f["type"] for f in document["features"]],
                         ["box", "cylinder", "subtract", "fillet"])
        self.assertTrue(validate(document).valid)

    def test_a_profile_chain_carries_its_history_too(self):
        plan = plan_of(sketch(), extrude("solid", "profile"),
                       hole("drill", "solid"), fillet("edges", "solid"))
        self.assertTrue(validate_plan(plan).valid)
        history = plan_history(plan)
        self.assertEqual(history.profiles, ("profile",))
        self.assertEqual(history.terminal_solids, ("solid",))
        self.assertEqual(history.derivation("solid"),
                         ("profile", "solid", "drill", "edges"))

    def test_a_profile_is_not_consumed_by_being_swept(self):
        plan = plan_of(sketch(), extrude("a", "profile"),
                       extrude("b", "profile"))
        history = plan_history(plan)
        self.assertEqual(history.profiles, ("profile",))
        self.assertEqual(history.consumed, ())
        self.assertEqual(history.terminal_solids, ("a", "b"))


# --- 4. what the graph makes visible ---------------------------------------


class LeftoverSolidsTests(unittest.TestCase):
    """Reported as a fact. S9 still rules on it -- where S9 runs."""

    def test_a_leftover_solid_appears_in_the_terminal_set(self):
        history = plan_history(plan_of(box("body"), box("spare")))
        self.assertEqual(history.terminal_solids, ("body", "spare"))

    def test_the_graph_does_not_rule_on_it(self):
        """Not a plan problem: a two-solid plan is a coherent plan and an
        invalid V1 part, and the V1 validator is the one that says so."""
        self.assertTrue(validate_plan(plan_of(box("body"), box("spare"))).valid)

    def test_s9_still_catches_it_on_a_solid_chain(self):
        from cad_core.validator import validate

        document = plan_to_document(plan_of(box("body"), box("spare")))
        result = validate(document)
        self.assertFalse(result.valid)
        self.assertIn("S9", [e.rule for e in result.errors])

    def test_on_a_profile_chain_the_graph_is_the_only_witness(self):
        """The case that motivated exposing this. A profile plan never
        becomes a document, so S9 never runs and the leftover is otherwise
        invisible."""
        plan = plan_of(sketch(), extrude("solid", "profile"), box("spare"))
        self.assertTrue(validate_plan(plan).valid)
        with self.assertRaises(ExecutionUnsupported):
            plan_to_document(plan)
        self.assertEqual(plan_history(plan).terminal_solids,
                         ("solid", "spare"))


class SafeOnBadPlansTests(unittest.TestCase):
    """The graph describes; it never raises and never judges."""

    def test_a_forward_reference_does_not_crash_the_graph(self):
        plan = plan_of(fillet("edges", "body"), box("body"))
        self.assertFalse(validate_plan(plan).valid)
        history = plan_history(plan)
        self.assertEqual(history.step("edges").depends_on, ("body",))
        self.assertEqual(history.terminal_solids, ("body",))

    def test_a_self_reference_terminates(self):
        plan = plan_of(box("body"), fillet("edges", "edges"))
        self.assertFalse(validate_plan(plan).valid)
        self.assertEqual(plan_history(plan).derivation("edges"), ("edges",))

    def test_an_unresolved_tool_is_not_recorded_as_consumed(self):
        plan = plan_of(box("body"), subtract("cut", "body", ["missing"]))
        self.assertFalse(validate_plan(plan).valid)
        history = plan_history(plan)
        self.assertEqual(history.consumed, ())
        self.assertEqual(history.step("cut").consumes, ())

    def test_an_empty_plan_gives_an_empty_history(self):
        history = plan_history(plan_of(status="unsupported"))
        self.assertEqual(history.steps, ())
        self.assertEqual(history.terminal_solids, ())


class NoGeometryTests(unittest.TestCase):
    def test_the_history_module_imports_no_kernel(self):
        tree = ast.parse((SOURCE / "history.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("cadquery", "OCP", "FreeCAD", "Part", "cad_core"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_the_history_module_reads_no_computed_attribute(self):
        """The package-wide rule: no `getattr` with a name built at runtime."""
        tree = ast.parse((SOURCE / "history.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(
                node.func, "id", None
            ) == "getattr":
                self.assertIsInstance(node.args[1], ast.Constant)


# --- 5. the transport, and the prompt --------------------------------------


class TheValidateRouteReportsTheHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(config=CONFIG))

    def post(self, *operations):
        response = self.client.post(VALIDATE_PATH, json={"plan": {
            "status": "generated", "summary": "s",
            "operations": [dict(o) for o in operations]}})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_a_chain_comes_back_with_its_graph(self):
        body = self.post(*BASE_CHAIN)
        self.assertTrue(body["valid"])
        history = body["history"]
        self.assertEqual(history["terminal_solids"], ["body"])
        self.assertEqual(history["consumed"], ["cutter"])
        self.assertEqual(history["depth"], 3)
        self.assertEqual([s["id"] for s in history["steps"]],
                         ["body", "cutter", "cut", "edges"])

    def test_the_payload_is_json_and_carries_no_object(self):
        body = self.post(*BASE_CHAIN)
        self.assertIsInstance(json.dumps(body["history"]), str)

    def test_a_leftover_is_visible_to_a_client(self):
        body = self.post(box("body"), box("spare"))
        self.assertEqual(body["history"]["terminal_solids"], ["body", "spare"])


class ThePromptTeachesTheChainTests(unittest.TestCase):
    def setUp(self):
        self.text = system_prompt()

    def test_the_sequence_section_exists(self):
        self.assertIn("# Building a part as a sequence", self.text)

    def test_it_states_the_three_chain_rules(self):
        section = self.text.split("# Building a part as a sequence", 1)[1]
        section = section.split("# Units", 1)[0]
        self.assertIn("EARLIER", section)
        self.assertIn("keeps its TARGET's id", section)
        # Prompt 2026-09-18.1 widened this from "a subtract CONSUMES its
        # tools" to name BOTH consuming operations, because the rule as
        # stated was incomplete for `union` -- a model could have read it as
        # leaving a union's tools live, which is a leftover solid and a plan
        # the validator rejects. Asserted on both names rather than on the
        # old phrase, so a third consuming operation fails this test.
        self.assertIn("CONSUME their tools", section)
        for consuming in ("subtract", "union"):
            self.assertIn(consuming, section)
        self.assertIn("exactly ONE solid left", section)

    def test_the_worked_example_is_itself_a_valid_chain(self):
        """A prompt that taught a plan the validator rejects would be worse
        than saying nothing. This is the example transcribed from the
        prompt, and it must survive the whole path."""
        from cad_core.validator import validate

        plan = plan_of(
            box("body", 60.0, 40.0, 8.0),
            box("slot", 20.0, 50.0, 20.0,
                position={"x": 20.0, "y": -5.0, "z": -6.0}),
            subtract("cut", "body", ["slot"]),
            op("edges", "fillet", target="body", parameters={
                "radius": 3.0,
                "edges": {"select": "axis_parallel", "axis": "Z"}}),
        )
        self.assertTrue(validate_plan(plan).valid)
        self.assertEqual(plan_history(plan).terminal_solids, ("body",))
        self.assertTrue(validate(plan_to_document(plan)).valid)

    def test_the_example_appears_in_the_prompt_as_tested(self):
        """Guards the test above from drifting away from the real text."""
        section = self.text.split("# Building a part as a sequence", 1)[1]
        self.assertIn('"id": "cut",   "type": "subtract", "target": "body", '
                      '"tools": ["slot"]', section)

    def test_the_prompt_contains_no_unexpanded_placeholder(self):
        self.assertNotIn("{PlanStatus", self.text)
        self.assertNotIn("{{", self.text)


if __name__ == "__main__":
    unittest.main()
