"""Stage 59: the agentic loop, proved with deterministic planners.

No model is called here. Every planner and reviser is a scripted stub, which
is the point: the loop's wiring has to be provable without a stochastic
component, or a failing test can never be told from a bad sample.

What these tests hold is the loop's contract rather than any CAD behaviour:
that a revision is a NEW plan, that unaffected operations survive it, that
the bound is real, that nothing falls back, that the taxonomy does not
collapse, and that the trace can answer what was tried and why the final
answer was accepted.
"""

from __future__ import annotations

import unittest

from cad_experimental import plan as canonical
from cad_experimental.agentic_loop import (
    DEFAULT_MAX_REVISIONS,
    FailureClass,
    InspectionResult,
    PlanCandidate,
    RevisionRequest,
    Task,
    diagnose,
    revision_request_for,
    run_agentic_cad_task,
)
from cad_experimental.local_plan_provider import FIXTURES


# --- scripted planners ------------------------------------------------------


class ScriptedPlanner:
    """Returns prepared payloads in order; records how often it was asked."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def generate(self, task):
        self.calls += 1
        return self.payloads[0] if self.payloads else None


class ScriptedReviser:
    """Returns prepared revisions in order, and keeps every request."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.requests = []
        self.calls = 0

    def revise(self, task, previous, request):
        self.calls += 1
        self.requests.append(request)
        if not self.payloads:
            return None
        return self.payloads.pop(0)


def plate_with_hole():
    import json
    return json.loads(json.dumps(FIXTURES["plate-one-hole"]["plan"]))


def chamfer_plan(edges):
    """A plate + hole + chamfer, with whatever selector is given."""
    return {
        "status": "generated",
        "summary": "a plate with a hole and a chamfer",
        "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20.0,
                            "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            {"id": "break_edge", "type": "chamfer", "target": "plate",
             "parameters": {"distance": 1.0, "edges": edges}},
        ],
    }


# --- 5. a successful plan runs exactly once ---------------------------------


class SuccessfulSinglePassTests(unittest.TestCase):

    def test_a_good_plan_executes_once_and_is_not_revised(self) -> None:
        planner = ScriptedPlanner(plate_with_hole())
        reviser = ScriptedReviser()
        result = run_agentic_cad_task(
            Task("a plate with a hole", expected_solid_count=1),
            planner, reviser)
        self.assertTrue(result.success)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(reviser.calls, 0)
        self.assertIs(result.failure_class, FailureClass.SUCCESS)

    def test_a_success_says_why_it_was_accepted(self) -> None:
        result = run_agentic_cad_task(
            Task("a plate with a hole"), ScriptedPlanner(plate_with_hole()))
        self.assertTrue(result.accepted_because)
        self.assertIn("built", result.accepted_because)

    def test_the_final_plan_is_the_canonical_object(self) -> None:
        result = run_agentic_cad_task(
            Task("a plate with a hole"), ScriptedPlanner(plate_with_hole()))
        self.assertIsInstance(result.final_plan, canonical.OperationPlan)


# --- 1/2. selector failures drive targeted revision --------------------------


class SelectorRevisionTests(unittest.TestCase):
    """Cases 1 and 2 of the brief: missing position, wrong axis."""

    def test_a_missing_circular_position_is_a_semantic_mismatch(self) -> None:
        """Volume cannot see this; the selector evidence can."""
        task = Task("chamfer the top rim",
                    expected_selector={"select": "circular",
                                       "position": "top"})
        planner = ScriptedPlanner(
            chamfer_plan({"select": "circular", "axis": "Z"}))
        result = run_agentic_cad_task(task, planner)
        self.assertFalse(result.success)
        self.assertIs(result.failure_class, FailureClass.SEMANTIC_MISMATCH)
        facts = result.attempts[0].diagnosis.facts
        self.assertIn("position", facts["disagreeing_fields"])

    def test_the_revision_request_names_the_missing_field(self) -> None:
        task = Task("chamfer the top rim",
                    expected_selector={"select": "circular",
                                       "position": "top"})
        reviser = ScriptedReviser(
            chamfer_plan({"select": "circular", "axis": "Z",
                          "position": "top"}))
        result = run_agentic_cad_task(
            task,
            ScriptedPlanner(chamfer_plan({"select": "circular", "axis": "Z"})),
            reviser)
        self.assertEqual(reviser.calls, 1)
        request = reviser.requests[0]
        self.assertIs(request.failure_class, FailureClass.SEMANTIC_MISMATCH)
        self.assertIn("selector", request.requested_change)
        self.assertTrue(result.success, result.stopped_because)

    def test_a_wrong_axis_is_also_caught_by_the_selector_expectation(
        self,
    ) -> None:
        task = Task("chamfer the long edges",
                    expected_selector={"select": "straight", "axis": "X"})
        result = run_agentic_cad_task(
            task,
            ScriptedPlanner(chamfer_plan({"select": "straight", "axis": "Z"})))
        self.assertIs(result.failure_class, FailureClass.SEMANTIC_MISMATCH)
        self.assertIn("axis",
                      result.attempts[0].diagnosis.facts["disagreeing_fields"])


# --- revision discipline ----------------------------------------------------


class RevisionDisciplineTests(unittest.TestCase):

    def setUp(self) -> None:
        self.task = Task("chamfer the top rim",
                         expected_selector={"select": "circular",
                                            "position": "top"})
        self.bad = chamfer_plan({"select": "circular", "axis": "Z"})
        self.good = chamfer_plan({"select": "circular", "axis": "Z",
                                  "position": "top"})

    def test_a_revision_produces_a_new_candidate_not_a_mutation(self) -> None:
        result = run_agentic_cad_task(
            self.task, ScriptedPlanner(self.bad), ScriptedReviser(self.good))
        first, second = result.attempts[0], result.attempts[1]
        self.assertNotEqual(first.candidate.candidate_id,
                            second.candidate.candidate_id)
        self.assertEqual(second.candidate.parent_candidate_id,
                         first.candidate.candidate_id)
        self.assertEqual(second.candidate.source, "reviser")
        # the original payload is untouched and still in the trace
        self.assertEqual(first.candidate.payload["operations"][2]
                         ["parameters"]["edges"],
                         {"select": "circular", "axis": "Z"})

    def test_a_revision_preserves_the_operations_it_was_not_asked_to_change(
        self,
    ) -> None:
        result = run_agentic_cad_task(
            self.task, ScriptedPlanner(self.bad), ScriptedReviser(self.good))
        before = result.attempts[0].candidate.payload["operations"]
        after = result.attempts[1].candidate.payload["operations"]
        self.assertEqual(before[0], after[0])   # the plate
        self.assertEqual(before[1], after[1])   # the hole
        self.assertNotEqual(before[2], after[2])  # only the chamfer moved

    def test_an_attempt_links_to_its_parent(self) -> None:
        result = run_agentic_cad_task(
            self.task, ScriptedPlanner(self.bad), ScriptedReviser(self.good))
        self.assertIsNone(result.attempts[0].parent_attempt_id)
        self.assertEqual(result.attempts[1].parent_attempt_id,
                         result.attempts[0].attempt_id)

    def test_a_reviser_returning_the_same_plan_stops_the_loop(self) -> None:
        """A revision that changes nothing is not a revision."""
        result = run_agentic_cad_task(
            self.task, ScriptedPlanner(self.bad), ScriptedReviser(self.bad))
        self.assertFalse(result.success)
        self.assertIn("same plan", result.stopped_because)

    def test_no_reviser_means_one_attempt_and_an_honest_stop(self) -> None:
        result = run_agentic_cad_task(self.task, ScriptedPlanner(self.bad))
        self.assertEqual(len(result.attempts), 1)
        self.assertIn("no reviser", result.stopped_because)


# --- 8. the bound is real ---------------------------------------------------


class BoundedLoopTests(unittest.TestCase):

    def test_the_default_bound_is_one_attempt_and_two_revisions(self) -> None:
        self.assertEqual(DEFAULT_MAX_REVISIONS, 2)

    def test_a_reviser_that_never_fixes_it_stops_at_three_executions(
        self,
    ) -> None:
        task = Task("chamfer the top rim",
                    expected_selector={"select": "circular",
                                       "position": "top"})
        bad = chamfer_plan({"select": "circular", "axis": "Z"})
        # three DIFFERENT wrong plans, so the "same plan" guard cannot fire
        wrongs = [chamfer_plan({"select": "circular", "axis": axis})
                  for axis in ("X", "Y", "Z")]
        result = run_agentic_cad_task(
            task, ScriptedPlanner(bad), ScriptedReviser(*wrongs))
        self.assertFalse(result.success)
        self.assertEqual(len(result.attempts), 3)
        self.assertIn("limit", result.stopped_because)

    def test_a_tighter_bound_is_respected(self) -> None:
        task = Task("chamfer the top rim",
                    expected_selector={"select": "circular",
                                       "position": "top"})
        wrongs = [chamfer_plan({"select": "circular", "axis": a})
                  for a in ("X", "Y", "Z")]
        result = run_agentic_cad_task(
            task, ScriptedPlanner(chamfer_plan({"select": "circular"})),
            ScriptedReviser(*wrongs), max_revisions=0)
        self.assertEqual(len(result.attempts), 1)


# --- 3/4. validation and backend failures -----------------------------------


class FailureTaxonomyTests(unittest.TestCase):

    def test_an_unparseable_plan_is_not_a_cad_failure(self) -> None:
        result = run_agentic_cad_task(
            Task("nonsense"), ScriptedPlanner({"not": "a plan"}))
        self.assertIs(result.failure_class, FailureClass.NO_PLAN_PRODUCED)

    def test_no_plan_at_all_is_reported_as_such(self) -> None:
        result = run_agentic_cad_task(Task("nothing"), ScriptedPlanner(None))
        self.assertIs(result.failure_class, FailureClass.NO_PLAN_PRODUCED)
        self.assertEqual(len(result.attempts), 1)

    def test_an_invalid_dependency_is_a_plan_invalid(self) -> None:
        """Case 3: a modifier naming a target that does not exist."""
        payload = {
            "status": "generated", "summary": "dangling reference",
            "operations": [
                {"id": "plate", "type": "box",
                 "parameters": {"x": 10.0, "y": 10.0, "z": 10.0}},
                {"id": "f", "type": "fillet", "target": "nonexistent",
                 "parameters": {"radius": 1.0,
                                "edges": {"select": "all"}}},
            ],
        }
        result = run_agentic_cad_task(Task("bad ref"),
                                      ScriptedPlanner(payload))
        self.assertIn(result.failure_class,
                      (FailureClass.PLAN_INVALID,
                       FailureClass.NO_PLAN_PRODUCED))
        self.assertFalse(result.success)

    def test_backend_unsupported_is_never_offered_for_revision(self) -> None:
        """Case 4: a reviser must not be asked to fix a missing method."""
        attempt_like = type("A", (), {})()
        from cad_experimental.agentic_loop import FailureDiagnosis
        diagnosis = FailureDiagnosis(
            failure_class=FailureClass.BACKEND_UNSUPPORTED,
            summary="no implementation", revisable=False)
        attempt_like.diagnosis = diagnosis
        attempt_like.candidate = PlanCandidate("x", "planner", {})
        self.assertIsNone(
            revision_request_for(attempt_like, Task("x")))

    def test_the_taxonomy_keeps_e4_and_e5_apart(self) -> None:
        """The distinction the whole selector line of work established."""
        self.assertIsNot(FailureClass.SELECTOR_MATCHED_NOTHING,
                         FailureClass.SELECTOR_GEOMETRY_REJECTED)
        candidate = PlanCandidate("c", "planner", chamfer_plan(
            {"select": "all"}), plan=object())
        e4 = diagnose(Task("t"), candidate, None,
                      InspectionResult(built=False,
                                       error_text="the selector matched no "
                                                  "edges of its target"))
        e5 = diagnose(Task("t"), candidate, None,
                      InspectionResult(built=False,
                                       error_text="the radius is too large "
                                                  "for the adjoining faces"))
        self.assertIs(e4.failure_class, FailureClass.SELECTOR_MATCHED_NOTHING)
        self.assertIs(e5.failure_class,
                      FailureClass.SELECTOR_GEOMETRY_REJECTED)

    def test_e4_and_e5_get_different_requested_changes(self) -> None:
        """Different remedies, which is why they are different classes."""
        from cad_experimental.agentic_loop import _CHANGE_BY_CLASS
        self.assertNotEqual(
            _CHANGE_BY_CLASS[FailureClass.SELECTOR_MATCHED_NOTHING],
            _CHANGE_BY_CLASS[FailureClass.SELECTOR_GEOMETRY_REJECTED])


# --- 11. the trace answers the four questions -------------------------------


class TraceTests(unittest.TestCase):

    def trace(self):
        task = Task("chamfer the top rim",
                    expected_selector={"select": "circular",
                                       "position": "top"})
        return run_agentic_cad_task(
            task,
            ScriptedPlanner(chamfer_plan({"select": "circular", "axis": "Z"})),
            ScriptedReviser(chamfer_plan({"select": "circular", "axis": "Z",
                                          "position": "top"})),
        ).trace

    def test_what_did_the_model_try(self) -> None:
        trace = self.trace()
        for attempt in trace["attempts"]:
            self.assertIsNotNone(attempt["candidate"]["payload"])

    def test_what_failed(self) -> None:
        trace = self.trace()
        self.assertEqual(trace["attempts"][0]["diagnosis"]["failure_class"],
                         "semantic_mismatch")

    def test_what_evidence_caused_the_revision(self) -> None:
        trace = self.trace()
        facts = trace["attempts"][0]["diagnosis"]["facts"]
        self.assertIn("expected_selector", facts)
        self.assertIn("selectors", facts)

    def test_what_changed(self) -> None:
        trace = self.trace()
        first = trace["attempts"][0]["candidate"]["payload"]["operations"][2]
        second = trace["attempts"][1]["candidate"]["payload"]["operations"][2]
        self.assertNotEqual(first, second)

    def test_why_the_final_result_was_accepted(self) -> None:
        trace = self.trace()
        self.assertTrue(trace["success"])
        self.assertTrue(trace["accepted_because"])

    def test_the_trace_is_json_serialisable(self) -> None:
        import json
        json.dumps(self.trace())

    def test_every_attempt_records_its_backend(self) -> None:
        trace = self.trace()
        for attempt in trace["attempts"]:
            self.assertEqual(attempt["backend"], "cadquery")


# --- architectural guards ---------------------------------------------------


class ArchitectureTests(unittest.TestCase):

    VENDORS = ("anthropic", "haiku", "claude", "openai", "gemini", "gpt")

    def test_the_loop_imports_no_model_vendor(self) -> None:
        """Prose may name a vendor as an example; code may not reference one.

        Asserted against imports and identifiers rather than the file's
        text, so the module can explain that the live Anthropic path is *an*
        implementation supplied by the caller without that explanation
        breaking the check it is describing.
        """
        import ast
        import pathlib

        from cad_experimental import agentic_loop

        tree = ast.parse(
            pathlib.Path(agentic_loop.__file__).read_text(encoding="utf-8"))
        imported, names = [], []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        haystack = " ".join(imported + names).lower()
        for vendor in self.VENDORS:
            self.assertNotIn(vendor, haystack, vendor)

    def test_the_loop_declares_a_planner_interface_not_a_provider(
        self,
    ) -> None:
        from cad_experimental.agentic_loop import Planner, Reviser
        self.assertTrue(hasattr(Planner, "generate"))
        self.assertTrue(hasattr(Reviser, "revise"))

    def test_the_loop_never_switches_backend(self) -> None:
        """The backend is recorded and never chosen for the caller."""
        import ast
        import pathlib

        from cad_experimental import agentic_loop

        tree = ast.parse(
            pathlib.Path(agentic_loop.__file__).read_text(encoding="utf-8"))
        assigned_backends = [
            node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value in ("freecad", "cadquery")
        ]
        # The only backend literal in the module is the parameter default.
        self.assertEqual(assigned_backends, [])

    def test_the_canonical_ir_is_unchanged(self) -> None:
        self.assertEqual(len(canonical.OPERATION_TYPES), 10)
        self.assertEqual(
            len(canonical.plan_schema()["properties"]["operations"]["items"]
                ["anyOf"]),
            len(canonical.OPERATION_TYPES))

    def test_the_protected_baselines_are_intact(self) -> None:
        from cad_experimental import stage48_capability_evaluation as stage48
        check = stage48._baseline_check()
        self.assertTrue(check["intact"], check["files"])
        self.assertEqual(len(check["files"]), 7)

    def test_the_direct_build_path_still_works(self) -> None:
        """The loop is a layer above; it must not have replaced anything."""
        import tempfile
        from cad_core.application_service import CadApplicationService
        from cad_experimental.build import build_plan
        from cad_experimental.parser import parse_plan
        plan = parse_plan(plate_with_hole())
        built = build_plan(
            CadApplicationService.local(tempfile.mkdtemp()), plan)
        self.assertTrue(built.built)


if __name__ == "__main__":
    unittest.main()
