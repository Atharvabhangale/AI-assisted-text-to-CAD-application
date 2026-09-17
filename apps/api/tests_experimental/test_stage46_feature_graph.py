"""Stage 46: the feature graph, and `pattern` as its first native operation.

Two things are under test here and they are separable on purpose.

**The graph** (:mod:`cad_experimental.graph`) is structure only: nodes,
role-tagged edges, a derived topological order, cycles. It holds no state,
computes no geometry and returns no verdict, so every function in it is total
-- it describes a broken plan rather than refusing to.

**The pattern** is the first operation whose input is another *operation*
rather than a body or a profile, which is what the roles exist for. Its
arithmetic lives in :mod:`cad_experimental.pattern`, away from the adapter,
because where an instance goes is a fact about the representation and not
about any engine.

Nothing here builds geometry except the two cases that say so, and those
check a closed form with an explicit tolerance -- never exact equality on a
kernel value.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from cad_core.application_service import CadApplicationService
from cad_core.validator import validate

from cad_experimental import graph as G
from cad_experimental.adapter import AdapterError, plan_to_document
from cad_experimental.app import VALIDATE_PATH, create_app
from cad_experimental.build import build_plan
from cad_experimental.config import ExperimentalConfig
from cad_experimental.history import plan_history
from cad_experimental.parser import PlanParseError, parse_plan
from cad_experimental.pattern import PatternError, instance_positions
from cad_experimental.plan import (
    EXECUTABLE_TYPES,
    MAX_PATTERN_COUNT,
    MIN_PATTERN_COUNT,
    OPERATION_TYPES,
    PATTERN,
    PATTERNABLE_TYPES,
    V1_FEATURE_TYPES,
    LinearPlacement,
    Point,
    RadialPlacement,
    instance_id,
)
from cad_experimental.prompt import system_prompt
from cad_experimental.validation import validate_plan

CONFIG = ExperimentalConfig(model="stub-model")
SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src" / "cad_experimental"

#: Volumes are kernel numbers. Compared against a closed form with a relative
#: tolerance, never with equality.
VOLUME_TOLERANCE = 1e-6


# --- builders ---------------------------------------------------------------


def box(identifier, x=100.0, y=100.0, z=10.0, **fields):
    return {"id": identifier, "type": "box",
            "parameters": dict(x=x, y=y, z=z, **fields)}


def cylinder(identifier, diameter=8.0, height=40.0, **fields):
    return {"id": identifier, "type": "cylinder",
            "parameters": dict(diameter=diameter, height=height, **fields)}


def hole(identifier, target, diameter=6.0, x=85.0, y=50.0, **fields):
    return {"id": identifier, "type": "through_hole", "target": target,
            "parameters": dict(diameter=diameter,
                               position={"x": x, "y": y, "z": 0.0}, **fields)}


def subtract(identifier, target, tools):
    return {"id": identifier, "type": "subtract", "target": target,
            "tools": list(tools)}


def fillet(identifier, target, radius=2.0, axis="Z"):
    return {"id": identifier, "type": "fillet", "target": target,
            "parameters": {"radius": radius,
                           "edges": {"select": "axis_parallel", "axis": axis}}}


def chamfer(identifier, target, distance=1.0):
    return {"id": identifier, "type": "chamfer", "target": target,
            "parameters": {"distance": distance, "edges": {"select": "all"}}}


def sketch(identifier="profile", plane="XY"):
    return {"id": identifier, "type": "sketch", "parameters": {
        "plane": plane,
        "geometry": [{"id": "r1", "type": "rectangle",
                      "corner": {"x": 0.0, "y": 0.0},
                      "width": 80.0, "height": 40.0}]}}


def circle_sketch(identifier="profile", plane="XZ"):
    return {"id": identifier, "type": "sketch", "parameters": {
        "plane": plane,
        "geometry": [{"id": "c1", "type": "circle",
                      "centre": {"x": 30.0, "y": 0.0}, "radius": 5.0}]}}


def extrude(identifier, target, distance=12.0):
    return {"id": identifier, "type": "extrude", "target": target,
            "parameters": {"distance": distance}}


def revolve(identifier, target, angle=360.0, axis="+Z"):
    return {"id": identifier, "type": "revolve", "target": target,
            "parameters": {"angle": angle, "axis": axis}}


def radial(identifier="mounts", source="mount", count=4, **placement):
    return {"id": identifier, "type": PATTERN, "source": source,
            "parameters": {"count": count, "placement": dict(
                {"kind": "radial", "axis": "+Z",
                 "centre": {"x": 50.0, "y": 50.0, "z": 0.0}}, **placement)}}


def linear(identifier="mounts", source="mount", count=3, **placement):
    return {"id": identifier, "type": PATTERN, "source": source,
            "parameters": {"count": count, "placement": dict(
                {"kind": "linear", "axis": "+X", "spacing": 20.0},
                **placement)}}


def plan_of(*operations, status="generated"):
    return parse_plan({"status": status, "summary": "a part",
                       "operations": [dict(o) for o in operations]})


#: The milestone's worked example, as one plan: a plate, a central bore, one
#: mounting hole, that hole repeated on a bolt circle, then an edge break.
BOLT_CIRCLE = (
    box("plate"),
    hole("bore", "plate", diameter=20.0, x=50.0, y=50.0),
    hole("mount", "plate", diameter=6.0, x=85.0, y=50.0),
    radial(),
    chamfer("break", "plate", 1.0),
)


# --- 1. the graph: nodes and role-tagged edges ------------------------------


class NodesAndEdgesTests(unittest.TestCase):
    def setUp(self):
        self.graph = G.feature_graph(plan_of(*BOLT_CIRCLE))

    def test_every_operation_is_a_node_in_plan_order(self):
        self.assertEqual([n.id for n in self.graph.nodes],
                         ["plate", "bore", "mount", "mounts", "break"])

    def test_a_node_records_what_it_produces(self):
        self.assertEqual(
            [(n.id, n.produces) for n in self.graph.nodes],
            [("plate", G.SOLID), ("bore", G.NOTHING), ("mount", G.NOTHING),
             ("mounts", G.NOTHING), ("break", G.NOTHING)],
        )

    def test_a_sketch_produces_a_profile_and_a_sweep_a_solid(self):
        graph = G.feature_graph(plan_of(sketch(), extrude("solid", "profile")))
        self.assertEqual([(n.id, n.produces) for n in graph.nodes],
                         [("profile", G.PROFILE), ("solid", G.SOLID)])

    def test_edges_carry_the_role_they_were_named_in(self):
        roles = {n.id: [(r.id, r.role) for r in n.inputs]
                 for n in self.graph.nodes}
        self.assertEqual(roles["plate"], [])
        self.assertEqual(roles["bore"], [("plate", G.TARGET)])
        self.assertEqual(roles["mount"], [("plate", G.TARGET)])
        self.assertEqual(roles["mounts"], [("mount", G.SOURCE)])
        self.assertEqual(roles["break"], [("plate", G.TARGET)])

    def test_a_subtract_names_its_target_then_its_tools_in_order(self):
        graph = G.feature_graph(plan_of(
            box("body"), cylinder("a"), cylinder("b"),
            subtract("cut", "body", ["a", "b"]),
        ))
        self.assertEqual(
            [(r.id, r.role, r.position) for r in graph.node("cut").inputs],
            [("body", G.TARGET, 0), ("a", G.TOOL, 0), ("b", G.TOOL, 1)],
        )

    def test_a_tool_edge_knows_its_own_path(self):
        graph = G.feature_graph(plan_of(
            box("body"), cylinder("a"), cylinder("b"),
            subtract("cut", "body", ["a", "b"]),
        ))
        self.assertEqual(
            [r.path() for r in graph.node("cut").inputs],
            ["target", "tools[0]", "tools[1]"],
        )

    def test_a_pattern_names_a_source_and_never_a_target(self):
        node = self.graph.node("mounts")
        self.assertEqual([r.role for r in node.inputs], [G.SOURCE])
        self.assertNotIn(G.TARGET, [r.role for r in node.inputs])


class ExpectationTableTests(unittest.TestCase):
    """One table in place of a branch per operation type."""

    def test_a_modifier_target_must_be_a_solid(self):
        for kind in ("through_hole", "subtract", "fillet", "chamfer"):
            with self.subTest(kind=kind):
                self.assertEqual(G.expectation(kind, G.TARGET),
                                 G.EXPECT_SOLID)

    def test_a_sweep_target_must_be_a_profile(self):
        for kind in ("extrude", "revolve"):
            with self.subTest(kind=kind):
                self.assertEqual(G.expectation(kind, G.TARGET),
                                 G.EXPECT_PROFILE)

    def test_a_pattern_source_must_be_a_feature(self):
        self.assertEqual(G.expectation(PATTERN, G.SOURCE), G.EXPECT_FEATURE)

    def test_only_the_consuming_operations_have_tools(self):
        """`subtract` alone until `union` arrived. Both take a list of
        solids and use them up; nothing else takes tools at all."""
        for kind in OPERATION_TYPES:
            with self.subTest(kind=kind):
                expected = (G.EXPECT_SOLID
                            if kind in ("subtract", "union") else None)
                self.assertEqual(G.expectation(kind, G.TOOL), expected)

    def test_a_combination_that_does_not_occur_answers_none(self):
        self.assertIsNone(G.expectation("box", G.TARGET))
        self.assertIsNone(G.expectation("fillet", G.SOURCE))
        self.assertIsNone(G.expectation(None, G.TARGET))

    def test_the_repeatable_table_and_the_plan_table_are_the_same_set(self):
        self.assertEqual(G.REPEATABLE, frozenset(PATTERNABLE_TYPES))

    def test_every_type_has_an_answer_for_every_role(self):
        """Total, so a new operation type cannot be silently unhandled."""
        for kind in OPERATION_TYPES:
            for role in G.ROLES:
                with self.subTest(kind=kind, role=role):
                    answer = G.expectation(kind, role)
                    self.assertIn(answer, (None, *G.EXPECTATIONS))


# --- 2. deterministic ordering ---------------------------------------------


class TopologicalOrderTests(unittest.TestCase):
    def test_a_chain_orders_as_the_chain(self):
        graph = G.feature_graph(plan_of(*BOLT_CIRCLE))
        self.assertEqual(graph.topological_order(),
                         ("plate", "bore", "mount", "mounts", "break"))

    def test_a_valid_plan_s_list_order_is_the_topological_order(self):
        """Rule P10 makes this true, and the point is that it is now
        CHECKED against the edges instead of assumed."""
        for label, operations in (
            ("bolt circle", BOLT_CIRCLE),
            ("subtract", (box("b"), cylinder("t"), subtract("c", "b", ["t"]))),
            ("profile", (sketch(), extrude("s", "profile"), hole("h", "s"))),
        ):
            with self.subTest(label=label):
                graph = G.feature_graph(plan_of(*operations))
                self.assertTrue(graph.is_list_order())

    def test_the_order_is_stable_across_calls(self):
        graph = G.feature_graph(plan_of(*BOLT_CIRCLE))
        self.assertEqual(graph.topological_order(), graph.topological_order())

    def test_independent_nodes_keep_their_plan_order(self):
        """Ties are broken by index, so one plan gives exactly one order --
        not merely *a* valid one."""
        graph = G.feature_graph(plan_of(box("c"), box("a"), box("b")))
        self.assertEqual(graph.topological_order(), ("c", "a", "b"))

    def test_an_unknown_reference_constrains_nothing(self):
        graph = G.feature_graph(plan_of(box("body"), fillet("e", "nowhere")))
        self.assertEqual(graph.topological_order(), ("body", "e"))

    def test_an_empty_plan_orders_to_nothing(self):
        self.assertEqual(G.FeatureGraph().topological_order(), ())


class PerBodySequencingTests(unittest.TestCase):
    """The derived edges, and why the declared ones are not enough.

    A chamfer and a hole both name the plate. Nothing the plan *declares*
    puts one after the other, so a topological sort over declared edges alone
    may legitimately float the chamfer ahead of the holes -- and chamfering
    before drilling is a different part. A modifier depends on its target's
    STATE, not only on its identity.
    """

    def graph(self):
        return G.feature_graph(plan_of(*BOLT_CIRCLE))

    def test_each_feature_follows_the_last_one_on_its_body(self):
        after = {n.id: n.after for n in self.graph().nodes}
        self.assertEqual(after, {
            "plate": (), "bore": ("plate",), "mount": ("bore",),
            "mounts": ("mount",), "break": ("mounts",),
        })

    def test_only_the_immediate_predecessor_is_recorded(self):
        """The rest follows by transitivity; storing the closure would make
        the graph quadratic and say nothing new."""
        for node in self.graph().nodes:
            self.assertLessEqual(len(node.after), 1)

    def test_each_node_records_the_body_it_changes(self):
        bodies = {n.id: n.body for n in self.graph().nodes}
        self.assertEqual(bodies, {
            "plate": None, "bore": "plate", "mount": "plate",
            "mounts": "plate", "break": "plate",
        })

    def test_a_pattern_s_body_is_its_source_s_body(self):
        """One hop further than a modifier's, which is the whole reason the
        role exists."""
        self.assertEqual(self.graph().node("mounts").body, "plate")

    def test_features_on_different_bodies_are_not_sequenced_together(self):
        """The multi-body property: two independent bodies impose no order
        on each other, and only the declared edges tie them."""
        graph = G.feature_graph(plan_of(
            box("a"), box("b"), fillet("ea", "a"), fillet("eb", "b"),
        ))
        self.assertEqual(graph.node("ea").after, ("a",))
        self.assertEqual(graph.node("eb").after, ("b",))
        self.assertNotIn("ea", graph.predecessors("eb"))
        self.assertNotIn("eb", graph.predecessors("ea"))

    def test_without_the_derived_edge_the_order_would_be_wrong(self):
        """States the bug this prevents, so it cannot silently come back:
        over declared edges alone the chamfer floats to third place."""
        graph = self.graph()
        declared_only = G.FeatureGraph(nodes=tuple(
            G.FeatureNode(index=n.index, id=n.id, type=n.type,
                          inputs=n.inputs, produces=n.produces)
            for n in graph.nodes
        ))
        self.assertEqual(declared_only.topological_order(),
                         ("plate", "bore", "mount", "break", "mounts"))
        self.assertEqual(graph.topological_order(),
                         ("plate", "bore", "mount", "mounts", "break"))


class BranchingGraphTests(unittest.TestCase):
    """Two features on one parent, and one feature feeding two."""

    def test_several_features_may_share_one_live_parent(self):
        plan = plan_of(box("plate"), hole("a", "plate"),
                       hole("b", "plate", x=15.0), fillet("e", "plate"))
        self.assertTrue(validate_plan(plan).valid)
        graph = G.feature_graph(plan)
        self.assertEqual(graph.dependents("plate"), ("a", "b", "e"))

    def test_one_profile_may_feed_two_sweeps(self):
        graph = G.feature_graph(plan_of(
            circle_sketch(), extrude("a", "profile"),
            revolve("b", "profile", 360.0, "+X"),
        ))
        self.assertEqual(graph.dependents("profile"), ("a", "b"))

    def test_ancestors_follow_every_role(self):
        graph = G.feature_graph(plan_of(*BOLT_CIRCLE))
        # Declared: mounts -> mount -> plate, a source edge then a target
        # edge. Derived: mounts also follows `bore`, because both change the
        # plate and a pattern acts on the plate as the bore left it.
        self.assertEqual(graph.ancestors("mounts"),
                         ("plate", "bore", "mount"))

    def test_declared_dependencies_stay_declared(self):
        """`dependencies` is what the plan SAYS; `predecessors` is what
        orders. Keeping them apart is what lets the history report the edges
        a reader wrote without the derived ones mixed in."""
        graph = G.feature_graph(plan_of(*BOLT_CIRCLE))
        self.assertEqual(graph.dependencies("break"), ("plate",))
        self.assertEqual(graph.predecessors("break"), ("plate", "mounts"))

    def test_descendants_are_the_transitive_reverse(self):
        graph = G.feature_graph(plan_of(*BOLT_CIRCLE))
        self.assertEqual(graph.descendants("plate"),
                         ("bore", "mount", "mounts", "break"))


# --- 3. the failure taxonomy ------------------------------------------------


class ReferenceFailureTests(unittest.TestCase):
    """Six distinguishable failures, each with its own code."""

    def codes(self, *operations):
        return [p.code for p in validate_plan(plan_of(*operations)).problems]

    def test_a_valid_dependency(self):
        self.assertEqual(self.codes(*BOLT_CIRCLE), [])

    def test_a_forward_reference_is_p10(self):
        self.assertIn("P10", self.codes(fillet("e", "plate"), box("plate")))

    def test_a_reference_to_a_consumed_feature_is_p12(self):
        self.assertIn("P12", self.codes(
            box("body"), cylinder("t"), subtract("c", "body", ["t"]),
            fillet("e", "t"),
        ))

    def test_a_reference_to_a_nonexistent_feature_is_p9(self):
        self.assertIn("P9", self.codes(box("body"), fillet("e", "ghost")))

    def test_a_self_reference_is_p10(self):
        self.assertIn("P10", self.codes(box("body"), fillet("e", "e")))

    def test_an_incompatible_solid_reference_is_p11(self):
        self.assertIn("P11", self.codes(sketch(), fillet("e", "profile")))

    def test_an_incompatible_profile_reference_is_p23(self):
        self.assertIn("P23", self.codes(box("body"), extrude("s", "body")))

    def test_an_incompatible_pattern_source_is_p27(self):
        self.assertIn("P27", self.codes(box("plate"), linear(source="plate")))

    def test_the_codes_are_distinct_per_failure(self):
        """An agent must be able to tell them apart, not merely be told the
        plan is bad."""
        self.assertEqual(
            len({"P9", "P10", "P11", "P12", "P23", "P27"}), 6
        )

    def test_a_problem_names_the_exact_operation_that_caused_it(self):
        problems = validate_plan(
            plan_of(box("plate"), hole("a", "plate"), fillet("e", "ghost"))
        ).problems
        self.assertEqual([p.where for p in problems], ["operations[2].target"])


class CycleTests(unittest.TestCase):
    """Unreachable through the parser, and detected anyway."""

    def cyclic_plan(self):
        """Two fillets targeting each other, built past the parser.

        P10 makes this impossible to write down, so it is assembled from
        typed records directly -- which is exactly the case the graph must
        not depend on the validator to have prevented.
        """
        from cad_experimental.plan import FilletOperation, EdgeSelector
        from cad_experimental.plan import OperationPlan, PlanStatus

        selector = EdgeSelector(select="all")
        return OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                FilletOperation(id="a", target="b", radius=1.0, edges=selector),
                FilletOperation(id="b", target="a", radius=1.0, edges=selector),
            ),
            summary="a cycle",
        )

    def test_a_cycle_is_detected(self):
        graph = G.feature_graph(self.cyclic_plan())
        self.assertEqual(graph.cycles(), (("a", "b"),))

    def test_a_cycle_leaves_its_members_unordered(self):
        graph = G.feature_graph(self.cyclic_plan())
        self.assertEqual(graph.topological_order(), ())
        self.assertFalse(graph.is_list_order())

    def test_a_cycle_is_reported_as_p31_naming_its_members(self):
        problems = validate_plan(self.cyclic_plan()).problems
        cycle = [p for p in problems if p.code == "P31"]
        self.assertEqual(len(cycle), 1)
        self.assertIn("'a'", cycle[0].message)
        self.assertIn("'b'", cycle[0].message)

    def test_an_acyclic_plan_reports_no_cycle(self):
        self.assertEqual(G.feature_graph(plan_of(*BOLT_CIRCLE)).cycles(), ())
        self.assertNotIn("P31", [p.code for p in
                                 validate_plan(plan_of(*BOLT_CIRCLE)).problems])

    def test_a_self_loop_is_a_cycle_of_one(self):
        from cad_experimental.plan import (
            EdgeSelector, FilletOperation, OperationPlan, PlanStatus,
        )

        plan = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(FilletOperation(
                id="a", target="a", radius=1.0,
                edges=EdgeSelector(select="all")),),
            summary="a self loop",
        )
        self.assertEqual(G.feature_graph(plan).cycles(), (("a",),))


# --- 4. the feature chains the milestone names ------------------------------


class FeatureChainTests(unittest.TestCase):
    """A to F, each validated and each checked for what it leaves behind."""

    def chain(self, *operations):
        plan = plan_of(*operations)
        verdict = validate_plan(plan)
        self.assertTrue(verdict.valid, verdict.to_dict())
        return plan_history(plan)

    def test_a_box_then_a_hole(self):
        history = self.chain(box("plate"), hole("h", "plate"))
        self.assertEqual(history.terminal_solids, ("plate",))

    def test_b_box_cylinder_subtract(self):
        history = self.chain(box("body"), cylinder("t"),
                             subtract("c", "body", ["t"]))
        self.assertEqual(history.terminal_solids, ("body",))
        self.assertEqual(history.consumed, ("t",))

    def test_c_box_hole_fillet(self):
        history = self.chain(box("plate"), hole("h", "plate"),
                             fillet("e", "plate"))
        self.assertEqual(history.derivation("plate"), ("plate", "h", "e"))

    def test_d_sketch_extrude_hole(self):
        history = self.chain(sketch(), extrude("solid", "profile"),
                             hole("h", "solid", x=20.0, y=20.0))
        self.assertEqual(history.terminal_solids, ("solid",))
        self.assertEqual(history.profiles, ("profile",))

    def test_e_sketch_revolve_then_edge_breaks(self):
        history = self.chain(circle_sketch(), revolve("solid", "profile"),
                             fillet("e", "solid"), chamfer("b", "solid"))
        self.assertEqual(history.derivation("solid"),
                         ("profile", "solid", "e", "b"))

    def test_f_several_features_on_one_live_parent(self):
        history = self.chain(box("plate"), hole("a", "plate"),
                             hole("b", "plate", x=15.0),
                             hole("c", "plate", x=15.0, y=85.0),
                             fillet("e", "plate"))
        self.assertEqual(history.terminal_solids, ("plate",))
        self.assertEqual(len(history.body("plate").features), 5)


# --- 5. the pattern: arithmetic ---------------------------------------------


class PlacementArithmeticTests(unittest.TestCase):
    """Pure arithmetic, checked against hand-computed points."""

    def close(self, point, x, y, z, places=9):
        self.assertAlmostEqual(point.x, x, places=places)
        self.assertAlmostEqual(point.y, y, places=places)
        self.assertAlmostEqual(point.z, z, places=places)

    def test_instance_zero_is_the_source_itself(self):
        source = Point(10.0, 5.0, 2.0)
        for placement in (
            LinearPlacement(axis="+X", spacing=20.0),
            RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0)),
        ):
            with self.subTest(placement=type(placement).__name__):
                first = instance_positions(placement, source, 4)[0]
                self.close(first, 10.0, 5.0, 2.0)

    def test_a_linear_pattern_steps_along_its_axis(self):
        points = instance_positions(
            LinearPlacement(axis="+X", spacing=20.0), Point(10.0, 5.0, 0.0), 3)
        self.assertEqual([p.x for p in points], [10.0, 30.0, 50.0])
        self.assertEqual({p.y for p in points}, {5.0})

    def test_a_negative_axis_steps_the_other_way(self):
        points = instance_positions(
            LinearPlacement(axis="-Y", spacing=5.0), Point(0.0, 0.0, 0.0), 3)
        self.assertEqual([p.y for p in points], [0.0, -5.0, -10.0])

    def test_a_radial_pattern_of_four_lands_on_the_quarters(self):
        points = instance_positions(
            RadialPlacement(axis="+Z", centre=Point(50.0, 50.0, 0.0)),
            Point(85.0, 50.0, 0.0), 4)
        for point, (x, y) in zip(
            points, [(85.0, 50.0), (50.0, 85.0), (15.0, 50.0), (50.0, 15.0)]
        ):
            self.close(point, x, y, 0.0, places=9)

    def test_the_default_step_is_a_full_circle_divided_by_the_count(self):
        placement = RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0))
        self.assertAlmostEqual(placement.step(6), 60.0)
        self.assertAlmostEqual(placement.step(4), 90.0)

    def test_an_explicit_step_is_the_turn_between_instances(self):
        points = instance_positions(
            RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0), angle=45.0),
            Point(10.0, 0.0, 0.0), 3)
        self.close(points[1], 10.0 * math.cos(math.radians(45.0)),
                   10.0 * math.sin(math.radians(45.0)), 0.0)

    def test_the_axis_sign_mirrors_the_turn(self):
        forward = instance_positions(
            RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0), angle=90.0),
            Point(10.0, 0.0, 0.0), 2)[1]
        backward = instance_positions(
            RadialPlacement(axis="-Z", centre=Point(0.0, 0.0, 0.0), angle=90.0),
            Point(10.0, 0.0, 0.0), 2)[1]
        self.close(forward, 0.0, 10.0, 0.0)
        self.close(backward, 0.0, -10.0, 0.0)

    def test_every_axis_turns_right_handed(self):
        centre = Point(0.0, 0.0, 0.0)
        for axis, source, expected in (
            ("+Z", Point(10.0, 0.0, 0.0), (0.0, 10.0, 0.0)),   # X -> Y
            ("+X", Point(0.0, 10.0, 0.0), (0.0, 0.0, 10.0)),   # Y -> Z
            ("+Y", Point(0.0, 0.0, 10.0), (10.0, 0.0, 0.0)),   # Z -> X
        ):
            with self.subTest(axis=axis):
                point = instance_positions(
                    RadialPlacement(axis=axis, centre=centre, angle=90.0),
                    source, 2)[1]
                self.close(point, *expected)

    def test_a_radial_pattern_does_not_move_along_its_axis(self):
        points = instance_positions(
            RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0)),
            Point(10.0, 0.0, 7.5), 6)
        for point in points:
            self.assertAlmostEqual(point.z, 7.5)

    def test_instances_are_computed_from_the_source_not_each_other(self):
        """No accumulation: a 360-step pattern returns exactly to the source
        rather than drifting round the circle."""
        points = instance_positions(
            RadialPlacement(axis="+Z", centre=Point(0.0, 0.0, 0.0), angle=360.0),
            Point(10.0, 0.0, 0.0), 8)
        for point in points:
            self.close(point, 10.0, 0.0, 0.0, places=9)

    def test_an_unknown_placement_raises_rather_than_guessing(self):
        with self.assertRaises(PatternError):
            instance_positions(object(), Point(0.0, 0.0, 0.0), 2)


# --- 6. the pattern: parsing and validation ---------------------------------


class PatternParsingTests(unittest.TestCase):
    def test_a_radial_pattern_parses(self):
        operation = plan_of(box("plate"), hole("mount", "plate"),
                            radial()).operations[2]
        self.assertEqual(operation.source, "mount")
        self.assertEqual(operation.count, 4)
        self.assertIsInstance(operation.placement, RadialPlacement)
        self.assertIsNone(operation.placement.angle)

    def test_a_linear_pattern_parses(self):
        operation = plan_of(box("plate"), hole("mount", "plate"),
                            linear()).operations[2]
        self.assertIsInstance(operation.placement, LinearPlacement)
        self.assertEqual(operation.placement.spacing, 20.0)

    def test_a_pattern_needs_a_source(self):
        with self.assertRaises(PlanParseError):
            parse_plan({"status": "generated", "summary": "s", "operations": [
                {"id": "p", "type": PATTERN,
                 "parameters": {"count": 2, "placement": {
                     "kind": "linear", "axis": "+X", "spacing": 1.0}}}]})

    def test_a_pattern_may_not_carry_a_target(self):
        with self.assertRaises(PlanParseError):
            parse_plan({"status": "generated", "summary": "s", "operations": [
                box("plate"), hole("m", "plate"),
                dict(linear(), target="plate")]})

    def test_an_unknown_placement_kind_is_rejected(self):
        with self.assertRaises(PlanParseError):
            plan_of(box("plate"), hole("m", "plate"),
                    linear(kind="spiral"))

    def test_a_placement_may_not_mix_its_kinds_fields(self):
        with self.assertRaises(PlanParseError):
            plan_of(box("plate"), hole("m", "plate"),
                    linear(centre={"x": 0.0, "y": 0.0, "z": 0.0}))

    def test_a_count_must_be_a_whole_number(self):
        for bad in (4.0, "4", True, None):
            with self.subTest(bad=bad):
                with self.assertRaises(PlanParseError):
                    plan_of(box("plate"), hole("m", "plate"),
                            linear(count=bad))

    def test_a_pattern_round_trips(self):
        payload = {"status": "generated", "summary": "s", "operations": [
            box("plate"), hole("mount", "plate"), radial(angle=45.0)]}
        once = parse_plan(payload).to_dict()
        self.assertEqual(parse_plan(once).to_dict(), once)
        self.assertEqual(once["operations"][2]["source"], "mount")

    def test_an_absent_angle_stays_absent_after_a_round_trip(self):
        plan = plan_of(box("plate"), hole("m", "plate"), radial())
        self.assertNotIn("angle",
                         plan.to_dict()["operations"][2]["parameters"]["placement"])


class PatternValidationTests(unittest.TestCase):
    def codes(self, *operations):
        return [p.code for p in validate_plan(plan_of(*operations)).problems]

    def base(self, *extra):
        return (box("plate"), hole("mount", "plate")) + extra

    def test_a_valid_pattern_has_no_problems(self):
        self.assertEqual(self.codes(*self.base(radial())), [])
        self.assertEqual(self.codes(*self.base(linear())), [])

    def test_a_count_below_the_minimum_is_p28(self):
        self.assertIn("P28", self.codes(*self.base(linear(count=1))))
        self.assertIn("P28", self.codes(*self.base(linear(count=0))))
        self.assertIn("P28", self.codes(*self.base(linear(count=-3))))

    def test_the_minimum_count_is_two_because_it_includes_the_source(self):
        self.assertEqual(MIN_PATTERN_COUNT, 2)
        self.assertEqual(self.codes(*self.base(linear(count=2))), [])

    def test_a_count_above_the_maximum_is_p28(self):
        self.assertIn("P28",
                      self.codes(*self.base(linear(count=MAX_PATTERN_COUNT + 1))))
        self.assertEqual(
            self.codes(*self.base(linear(count=MAX_PATTERN_COUNT))), [])

    def test_a_source_that_is_a_solid_is_p27(self):
        self.assertIn("P27", self.codes(*self.base(linear(source="plate"))))

    def test_a_source_that_is_a_profile_is_p27(self):
        self.assertIn("P27", self.codes(
            sketch(), extrude("solid", "profile"), linear(source="profile")))

    def test_a_pattern_of_a_pattern_is_p27(self):
        self.assertIn("P27", self.codes(
            *self.base(linear(identifier="a"), linear(identifier="b",
                                                     source="a"))))

    def test_a_missing_source_is_p9_not_p27(self):
        """The shared reference checks run first: a source that names nothing
        is not a source of the wrong kind."""
        codes = self.codes(*self.base(linear(source="ghost")))
        self.assertIn("P9", codes)
        self.assertNotIn("P27", codes)

    def test_a_forward_source_is_p10(self):
        self.assertIn("P10", self.codes(
            linear(), box("plate"), hole("mount", "plate")))

    def test_a_consumed_source_cannot_be_repeated(self):
        """A hole in a tool that has since been subtracted."""
        codes = self.codes(
            box("body"), cylinder("t"), hole("m", "t", x=0.0, y=0.0),
            subtract("cut", "body", ["t"]), linear(source="m"),
        )
        self.assertEqual(codes, [])  # the HOLE was not consumed, the tool was

    def test_a_radial_axis_the_source_is_not_parallel_to_is_p29(self):
        plan = (box("plate"),
                hole("mount", "plate", axis="+X"),
                radial())
        self.assertIn("P29", self.codes(*plan))

    def test_a_radial_axis_the_source_is_parallel_to_is_accepted(self):
        for axis in ("+Z", "-Z"):
            with self.subTest(axis=axis):
                self.assertEqual(self.codes(
                    box("plate"), hole("mount", "plate", axis=axis), radial()
                ), [])

    def test_a_step_of_zero_or_a_full_turn_is_bounded_by_p29(self):
        self.assertIn("P29", self.codes(*self.base(radial(angle=0.0))))
        self.assertIn("P29", self.codes(*self.base(radial(angle=-90.0))))
        self.assertIn("P29", self.codes(*self.base(radial(angle=360.1))))
        self.assertEqual(self.codes(*self.base(radial(angle=360.0))), [])

    def test_a_non_positive_spacing_is_p4(self):
        self.assertIn("P4", self.codes(*self.base(linear(spacing=0.0))))
        self.assertIn("P4", self.codes(*self.base(linear(spacing=-5.0))))

    def test_a_derived_instance_id_may_not_collide_is_p30(self):
        self.assertIn("P30", self.codes(
            box("plate"), hole("mount", "plate"),
            linear(identifier="m", count=3),
            box("m-1", 1.0, 1.0, 1.0),
        ))

    def test_the_collision_check_uses_the_adapter_s_own_naming(self):
        self.assertEqual(instance_id("mounts", 2), "mounts-2")

    def test_nothing_may_target_a_pattern(self):
        """A pattern is not a solid, so P11 rejects a fillet on it."""
        self.assertIn("P11", self.codes(
            *self.base(radial(), fillet("e", "mounts"))))


# --- 7. the pattern: history and expansion ----------------------------------


class PatternHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = plan_history(plan_of(*BOLT_CIRCLE))

    def test_a_pattern_belongs_to_the_body_its_source_changed(self):
        self.assertEqual(self.history.owner_of("mounts"), "plate")
        self.assertEqual(self.history.step("mounts").modifies, "plate")

    def test_a_pattern_declares_no_solid(self):
        step = self.history.step("mounts")
        self.assertIsNone(step.declares_solid)
        self.assertIsNone(step.declares_profile)
        self.assertEqual(step.consumes, ())

    def test_a_pattern_does_not_consume_its_source(self):
        self.assertEqual(self.history.consumed, ())
        self.assertNotIn("mount", self.history.consumed)

    def test_the_pattern_is_in_the_body_s_derivation(self):
        self.assertEqual(self.history.derivation("plate"),
                         ("plate", "bore", "mount", "mounts", "break"))

    def test_the_plan_still_ends_with_one_solid(self):
        """Repeating a modifier is still modifying: however many instances,
        the body keeps its id and S9 is untouched."""
        self.assertEqual(self.history.terminal_solids, ("plate",))
        self.assertEqual(len(self.history.live_bodies), 1)


class PatternExpansionTests(unittest.TestCase):
    def document(self, *operations):
        plan = plan_of(*operations)
        self.assertTrue(validate_plan(plan).valid)
        return plan_to_document(plan)

    def test_a_pattern_expands_to_one_feature_per_extra_instance(self):
        document = self.document(*BOLT_CIRCLE)
        self.assertEqual([f["id"] for f in document["features"]],
                         ["plate", "bore", "mount",
                          "mounts-1", "mounts-2", "mounts-3", "break"])

    def test_the_source_is_not_emitted_twice(self):
        document = self.document(*BOLT_CIRCLE)
        positions = [f.get("position") for f in document["features"]
                     if f["type"] == "through_hole"]
        self.assertEqual(len(positions), len(set(map(str, positions))))

    def test_every_instance_is_the_source_s_feature_type_and_target(self):
        document = self.document(*BOLT_CIRCLE)
        for feature in document["features"]:
            if feature["id"].startswith("mounts-"):
                self.assertEqual(feature["type"], "through_hole")
                self.assertEqual(feature["target"], "plate")
                self.assertEqual(feature["diameter"], 6.0)

    def test_an_instance_inherits_the_source_s_axis(self):
        document = self.document(
            box("plate"), hole("mount", "plate", axis="-Z"), radial())
        for feature in document["features"]:
            if feature["id"].startswith("mounts-"):
                self.assertEqual(feature["axis"], "-Z")

    def test_the_expanded_document_is_valid_v1(self):
        self.assertTrue(validate(self.document(*BOLT_CIRCLE)).valid)

    def test_a_linear_pattern_expands_along_its_axis(self):
        document = self.document(
            box("plate"), hole("m", "plate", x=20.0, y=50.0),
            linear(identifier="row", source="m", count=4, spacing=15.0))
        xs = [f["position"]["x"] for f in document["features"]
              if f["type"] == "through_hole"]
        self.assertEqual(xs, [20.0, 35.0, 50.0, 65.0])

    def test_the_expansion_is_deterministic(self):
        first = self.document(*BOLT_CIRCLE)
        second = self.document(*BOLT_CIRCLE)
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_the_adapter_refuses_an_unrepeatable_source_rather_than_inventing(self):
        from cad_experimental.plan import (
            BoxOperation, OperationPlan, PatternOperation, PlanStatus,
        )

        plan = OperationPlan(
            status=PlanStatus.GENERATED,
            operations=(
                BoxOperation(id="b", x=1.0, y=1.0, z=1.0),
                PatternOperation(id="p", source="b", count=2,
                                 placement=LinearPlacement(axis="+X",
                                                           spacing=1.0)),
            ),
            summary="unrepeatable",
        )
        with self.assertRaises(AdapterError):
            plan_to_document(plan)

    def test_a_pattern_is_executable_but_is_not_a_v1_feature(self):
        self.assertIn(PATTERN, EXECUTABLE_TYPES)
        self.assertNotIn(PATTERN, V1_FEATURE_TYPES)


# --- 8. execution -----------------------------------------------------------


class PatternExecutionTests(unittest.TestCase):
    """Two real builds. Volumes against a closed form, with a tolerance."""

    @classmethod
    def setUpClass(cls):
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def built(self, *operations):
        plan = plan_of(*operations)
        self.assertTrue(validate_plan(plan).valid)
        result = build_plan(self.service, plan)
        self.assertTrue(result.built, result.error)
        return result

    def test_a_bolt_circle_builds_to_its_closed_form(self):
        result = self.built(
            box("plate"),
            hole("bore", "plate", diameter=20.0, x=50.0, y=50.0),
            hole("mount", "plate", diameter=6.0, x=85.0, y=50.0),
            radial(),
        )
        details = result.outcome.artifact("geometry").details
        expected = (100.0 * 100.0 * 10.0
                    - math.pi * 10.0 ** 2 * 10.0
                    - 4 * math.pi * 3.0 ** 2 * 10.0)
        self.assertEqual(details["solid_count"], 1)
        self.assertLess(
            abs(details["volume_mm3"] - expected) / expected, VOLUME_TOLERANCE
        )

    def test_a_linear_row_builds_to_its_closed_form(self):
        result = self.built(
            box("plate"),
            hole("m", "plate", diameter=6.0, x=20.0, y=50.0),
            linear(identifier="row", source="m", count=4, spacing=15.0),
        )
        details = result.outcome.artifact("geometry").details
        expected = 100.0 * 100.0 * 10.0 - 4 * math.pi * 3.0 ** 2 * 10.0
        self.assertLess(
            abs(details["volume_mm3"] - expected) / expected, VOLUME_TOLERANCE
        )

    def test_a_downstream_feature_sees_the_patterned_body(self):
        """A feature after the pattern acts on the SAME body id, and on the
        body as the pattern left it.

        The downstream feature here is a hole rather than an edge break: a
        chamfer or fillet on a drilled plate meets the unresolved seam
        problem (`docs/edge-selection.md`) and fails E5 in the kernel, which
        is a true refusal about the selector and would say nothing about
        patterns.
        """
        result = self.built(
            box("plate"),
            hole("mount", "plate", diameter=6.0, x=85.0, y=50.0),
            radial(),
            hole("centre", "plate", diameter=20.0, x=50.0, y=50.0),
        )
        details = result.outcome.artifact("geometry").details
        expected = (100.0 * 100.0 * 10.0
                    - 4 * math.pi * 3.0 ** 2 * 10.0
                    - math.pi * 10.0 ** 2 * 10.0)
        self.assertEqual(details["solid_count"], 1)
        self.assertLess(
            abs(details["volume_mm3"] - expected) / expected, VOLUME_TOLERANCE
        )


# --- 9. backend independence ------------------------------------------------


class BackendIndependenceTests(unittest.TestCase):
    def imports_of(self, name):
        tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
        return found

    def test_the_graph_and_the_placement_import_no_kernel(self):
        for name in ("graph.py", "pattern.py", "history.py"):
            with self.subTest(name=name):
                imported = self.imports_of(name)
                for forbidden in ("cadquery", "OCP", "FreeCAD", "Part",
                                  "cad_core", "cad_experimental.cad_backend",
                                  "cad_experimental.cadquery_backend",
                                  "cad_experimental.freecad_backend"):
                    self.assertNotIn(forbidden, imported)

    def test_no_backend_gains_a_pattern_method(self):
        """A pattern is expanded in the adapter and never reaches an engine,
        so no backend needs to know the word."""
        from cad_experimental import cad_backend as cb

        self.assertNotIn("pattern", cb.SUPPORTED_OPERATIONS)
        self.assertFalse(hasattr(cb.CadBackend, "pattern"))

    def test_the_graph_reads_no_computed_attribute(self):
        for name in ("graph.py", "pattern.py"):
            tree = ast.parse((SOURCE / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(
                    node.func, "id", None
                ) == "getattr":
                    with self.subTest(name=name):
                        self.assertIsInstance(node.args[1], ast.Constant)


# --- 10. existing plans still work ------------------------------------------


class NoRegressionTests(unittest.TestCase):
    """Plans that were valid before this stage must still parse and execute."""

    def test_every_pre_pattern_chain_still_validates(self):
        for label, operations in (
            ("box", (box("b"),)),
            ("hole", (box("b"), hole("h", "b"))),
            ("subtract", (box("b"), cylinder("t"), subtract("c", "b", ["t"]))),
            ("fillet", (box("b"), fillet("e", "b"))),
            ("chamfer", (box("b"), chamfer("e", "b"))),
            ("profile", (sketch(), extrude("s", "profile"))),
            ("revolve", (circle_sketch(), revolve("s", "profile"))),
        ):
            with self.subTest(label=label):
                verdict = validate_plan(plan_of(*operations))
                self.assertTrue(verdict.valid, verdict.to_dict())

    def test_a_plan_without_a_pattern_expands_one_feature_per_operation(self):
        document = plan_to_document(
            plan_of(box("b"), cylinder("t"), subtract("c", "b", ["t"])))
        self.assertEqual([f["id"] for f in document["features"]],
                         ["b", "t", "c"])

    def test_the_stage_43_schema_is_unchanged(self):
        """Its fingerprint is in a preserved baseline. Widening the
        vocabulary must not move it."""
        import hashlib

        from cad_experimental.plan import executable_schema

        canonical = json.dumps(executable_schema(), sort_keys=True,
                               separators=(",", ":"))
        self.assertEqual(
            hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16],
            "54759d1e16cfe634",
        )


# --- 11. the transport and the prompt ---------------------------------------


class TheValidateRouteReportsTheGraphTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(config=CONFIG))

    def post(self, *operations):
        response = self.client.post(VALIDATE_PATH, json={"plan": {
            "status": "generated", "summary": "s",
            "operations": [dict(o) for o in operations]}})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_the_graph_comes_back_with_the_plan(self):
        body = self.post(*BOLT_CIRCLE)
        self.assertTrue(body["valid"])
        self.assertEqual(body["graph"]["order"],
                         ["plate", "bore", "mount", "mounts", "break"])
        self.assertTrue(body["graph"]["list_order"])
        self.assertEqual(body["graph"]["cycles"], [])

    def test_the_edges_carry_their_roles_over_the_wire(self):
        body = self.post(*BOLT_CIRCLE)
        nodes = {n["id"]: n for n in body["graph"]["nodes"]}
        self.assertEqual(nodes["mounts"]["inputs"][0]["role"], "source")
        self.assertEqual(nodes["bore"]["inputs"][0]["role"], "target")

    def test_bodies_come_back_too(self):
        body = self.post(box("b"), cylinder("t"), subtract("c", "b", ["t"]))
        bodies = {entry["id"]: entry for entry in body["history"]["bodies"]}
        self.assertTrue(bodies["b"]["live"])
        self.assertFalse(bodies["t"]["live"])
        self.assertEqual(bodies["t"]["consumed_by"], "c")

    def test_the_payload_is_plain_json(self):
        body = self.post(*BOLT_CIRCLE)
        self.assertIsInstance(json.dumps(body), str)


class ThePromptDescribesThePatternTests(unittest.TestCase):
    def setUp(self):
        self.text = system_prompt()

    def test_the_pattern_section_exists(self):
        self.assertIn("## pattern", self.text)

    def test_it_says_the_count_includes_the_source(self):
        section = self.text.split("## pattern", 1)[1].split("There are no", 1)[0]
        self.assertIn("INCLUDES the source", section)

    def test_it_says_source_is_not_target(self):
        section = self.text.split("## pattern", 1)[1].split("There are no", 1)[0]
        self.assertIn("NOT called `target`", section)

    def test_it_states_the_radial_axis_restriction(self):
        section = self.text.split("## pattern", 1)[1].split("There are no", 1)[0]
        self.assertIn("about an axis the source is", section)
        self.assertIn("would tilt it onto a direction this language cannot",
                      section)

    def test_it_tells_the_model_to_repeat_rather_than_duplicate(self):
        section = self.text.split("## pattern", 1)[1].split("There are no", 1)[0]
        self.assertIn("Do not emit", section)
        self.assertIn("hand-computed positions", section)

    def test_it_does_not_hard_code_a_benchmark_case(self):
        """The corpus's own wording must not appear: teaching the rule is
        instruction, reciting the test is not."""
        for case_text in (
            "Create a rectangular 100 mm by 60 mm profile",
            "Create a circular profile with a 20 mm diameter",
            "four-cylinder engine",
        ):
            self.assertNotIn(case_text, self.text)

    def test_the_prompt_documents_every_reachable_type(self):
        for kind in OPERATION_TYPES:
            with self.subTest(kind=kind):
                self.assertIn(f"## {kind}", self.text)


if __name__ == "__main__":
    unittest.main()
