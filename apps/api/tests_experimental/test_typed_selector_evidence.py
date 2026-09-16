"""Stage 60: E4 and E5 made real, from typed evidence rather than prose.

Stage 59 decided E4 and E5 by matching words in a backend's error string.
That is brittle in the obvious way and silently wrong in a worse one: a
backend that phrases a failure differently gets a confident misdiagnosis.

None of it was necessary. ``edge_semantics.resolve`` is already a total,
typed resolver -- it never raises, it returns the indices it chose, and it
reports its own codes for why it chose no more. So these tests assert
*counts*, and every number below was measured on a 100x60x10 plate with a
20 mm bore through the centre.

The decisive tests are the two that classify a failure with
``error_text=None``: if those pass, the diagnosis is not reading prose.
"""

from __future__ import annotations

import json
import unittest

from cad_experimental.agentic_loop import (
    ComparisonLevel,
    FailureClass,
    InspectionResult,
    PlanCandidate,
    SelectorEvidence,
    Task,
    diagnose,
    run_agentic_cad_task,
)

from test_agentic_loop import (
    ScriptedPlanner,
    ScriptedReviser,
    chamfer_plan,
    plate_with_hole,
)


class ResolverEvidenceTests(unittest.TestCase):
    """What the resolver actually names, on real geometry."""

    @classmethod
    def setUpClass(cls) -> None:
        from cad_experimental import cadquery_backend, edge_semantics

        cls.semantics = edge_semantics
        backend = cadquery_backend.CadQueryBackend()
        holed = backend.through_hole(
            backend.create_box((100.0, 60.0, 10.0)),
            20.0, (50.0, 30.0, 0.0), "+Z")
        cls.facts = backend.describe_edges(holed)

    def resolve(self, **selector):
        return self.semantics.resolve(
            self.semantics.SemanticSelector(**selector), self.facts)

    # --- E4 -----------------------------------------------------------------

    def test_e4_a_selector_naming_nothing_is_code_r1(self) -> None:
        """The bore runs along Z, so no circular edge exists about X."""
        resolution = self.resolve(select="circular", axis="X")
        self.assertEqual(resolution.code, self.semantics.R1)
        self.assertEqual(len(resolution.indices), 0)

    def test_e4_is_a_count(self) -> None:
        resolution = self.resolve(select="circular", axis="X")
        evidence = SelectorEvidence(
            selector={"select": "circular", "axis": "X"},
            operation_id="break_edge",
            selected_edge_count=len(resolution.indices),
            candidate_edge_count=len(resolution.candidates),
            seam_count=len(resolution.seams),
            resolution_code=resolution.code)
        self.assertTrue(evidence.matched_nothing)

    # --- E5 -----------------------------------------------------------------

    def test_e5_a_seam_bearing_selection_is_code_r2(self) -> None:
        """`axis_parallel` includes the bore seam, which no blend can take."""
        resolution = self.resolve(select="axis_parallel", axis="Z")
        self.assertEqual(resolution.code, self.semantics.R2)
        self.assertEqual(len(resolution.seams), 1)

    def test_e4_and_e5_are_different_codes(self) -> None:
        """The distinction Stages 40-55 established, preserved end to end."""
        e4 = self.resolve(select="circular", axis="X")
        e5 = self.resolve(select="axis_parallel", axis="Z")
        self.assertNotEqual(e4.code, e5.code)
        self.assertEqual(len(e4.seams), 0)
        self.assertEqual(len(e5.seams), 1)

    # --- the seam distinction, and the counts that separate the rims --------

    def test_straight_excludes_the_seam_axis_parallel_includes_it(
        self,
    ) -> None:
        straight = self.resolve(select="straight", axis="Z")
        axis_parallel = self.resolve(select="axis_parallel", axis="Z")
        self.assertEqual(len(straight.indices), 4)
        self.assertEqual(len(straight.seams), 0)
        self.assertEqual(len(axis_parallel.seams), 1)

    def test_one_rim_each_and_two_for_both(self) -> None:
        """Volume cannot tell a top rim from a bottom. A count can."""
        self.assertEqual(
            len(self.resolve(select="circular", axis="Z").indices), 2)
        self.assertEqual(
            len(self.resolve(select="circular", axis="Z",
                             position="top").indices), 1)
        self.assertEqual(
            len(self.resolve(select="circular", axis="Z",
                             position="bottom").indices), 1)

    def test_top_and_bottom_name_different_edges(self) -> None:
        top = self.resolve(select="circular", axis="Z", position="top")
        bottom = self.resolve(select="circular", axis="Z", position="bottom")
        self.assertNotEqual(set(top.indices), set(bottom.indices))


class DiagnosisWithoutErrorTextTests(unittest.TestCase):
    """The decisive pair: classify a failure with no error string at all."""

    def candidate(self, edges):
        return PlanCandidate("c", "planner", chamfer_plan(edges),
                             plan=object())

    def test_e4_is_classified_with_error_text_none(self) -> None:
        evidence = SelectorEvidence(
            selector={"select": "circular", "axis": "X"},
            operation_id="break_edge", selected_edge_count=0,
            candidate_edge_count=0, seam_count=0, resolution_code="R1")
        diagnosis = diagnose(
            Task("t"), self.candidate({"select": "circular", "axis": "X"}),
            None,
            InspectionResult(built=False, selector_evidence=(evidence,),
                             error_text=None))
        self.assertIs(diagnosis.failure_class,
                      FailureClass.SELECTOR_MATCHED_NOTHING)
        self.assertEqual(
            diagnosis.selector_evidence[0].selected_edge_count, 0)
        self.assertIn("break_edge", diagnosis.affected_operation_ids)

    def test_a_seam_selection_is_classified_with_error_text_none(self) -> None:
        evidence = SelectorEvidence(
            selector={"select": "axis_parallel", "axis": "Z"},
            operation_id="round_it", selected_edge_count=2,
            candidate_edge_count=5, seam_count=1, resolution_code="R2")
        diagnosis = diagnose(
            Task("t"),
            self.candidate({"select": "axis_parallel", "axis": "Z"}), None,
            InspectionResult(built=False, selector_evidence=(evidence,),
                             error_text=None))
        self.assertIs(diagnosis.failure_class,
                      FailureClass.SELECTOR_GEOMETRY_REJECTED)
        self.assertEqual(diagnosis.selector_evidence[0].seam_count, 1)
        self.assertIn("round_it", diagnosis.affected_operation_ids)

    def test_typed_evidence_beats_contradicting_error_text(self) -> None:
        """Typed evidence is consulted first, so prose cannot override it."""
        evidence = SelectorEvidence(
            selector={"select": "circular", "axis": "X"},
            operation_id="break_edge", selected_edge_count=0,
            candidate_edge_count=0, seam_count=0, resolution_code="R1")
        diagnosis = diagnose(
            Task("t"), self.candidate({"select": "circular", "axis": "X"}),
            None,
            InspectionResult(
                built=False, selector_evidence=(evidence,),
                error_text="the radius is too large for the adjoining faces"))
        self.assertIs(diagnosis.failure_class,
                      FailureClass.SELECTOR_MATCHED_NOTHING)

    def test_a_text_classified_diagnosis_says_so(self) -> None:
        """When prose IS the only evidence, the trace admits it."""
        diagnosis = diagnose(
            Task("t"), self.candidate({"select": "all"}), None,
            InspectionResult(built=False, selector_evidence=(),
                             error_text="the selector matched no edges"))
        self.assertIs(diagnosis.failure_class,
                      FailureClass.SELECTOR_MATCHED_NOTHING)
        self.assertIn("classified_by", diagnosis.evidence)


class ComparisonLevelTests(unittest.TestCase):
    """A level is claimed only when its check was actually performed."""

    def test_there_are_exactly_three_levels(self) -> None:
        self.assertEqual(len({level.value for level in ComparisonLevel}), 3)

    def test_a_task_with_no_selector_cannot_reach_semantic(self) -> None:
        result = run_agentic_cad_task(
            Task("a plate with a hole"), ScriptedPlanner(plate_with_hole()))
        self.assertTrue(result.success, result.stopped_because)
        self.assertNotEqual(
            result.final_attempt.diagnosis.measurement_evidence
            .comparison_level, ComparisonLevel.SEMANTIC.value)

    def test_a_selector_task_reaches_semantic(self) -> None:
        result = run_agentic_cad_task(
            Task("chamfer the top rim",
                 expected_selector={"select": "circular", "position": "top"}),
            ScriptedPlanner(chamfer_plan(
                {"select": "circular", "axis": "Z", "position": "top"})))
        self.assertTrue(result.success, result.stopped_because)
        self.assertEqual(
            result.final_attempt.diagnosis.measurement_evidence
            .comparison_level, ComparisonLevel.SEMANTIC.value)

    def test_no_level_claims_geometric_equality(self) -> None:
        """F2/F3 agree on every number here. The module must admit it."""
        import pathlib

        from cad_experimental import agentic_loop

        source = pathlib.Path(
            agentic_loop.__file__).read_text(encoding="utf-8").lower()
        self.assertIn("none of them is geometric equality", source)


class EvidenceSerialisationTests(unittest.TestCase):
    """Evidence must survive the trace as data, not as prose."""

    def result(self):
        return run_agentic_cad_task(
            Task("chamfer the top rim",
                 expected_selector={"select": "circular", "position": "top"}),
            ScriptedPlanner(chamfer_plan({"select": "circular", "axis": "Z"})),
            ScriptedReviser(chamfer_plan(
                {"select": "circular", "axis": "Z", "position": "top"})))

    def test_selector_evidence_appears_in_the_trace(self) -> None:
        trace = self.result().trace
        first = trace["attempts"][0]
        self.assertIn("selector_evidence", first["inspection"])
        self.assertIn("selector_evidence", first["diagnosis"])

    def test_the_trace_round_trips_through_json(self) -> None:
        trace = self.result().trace
        self.assertEqual(json.loads(json.dumps(trace)), trace)

    def test_the_inspection_records_its_backend(self) -> None:
        trace = self.result().trace
        self.assertEqual(
            trace["attempts"][0]["inspection"]["backend"], "cadquery")

    def test_retryable_and_revision_allowed_are_separate(self) -> None:
        diagnosis = self.result().trace["attempts"][0]["diagnosis"]
        self.assertIn("retryable", diagnosis)
        self.assertIn("revision_allowed", diagnosis)

    def test_stage_59_trace_readers_still_find_their_keys(self) -> None:
        """`facts` and `revisable` are kept as aliases, not removed."""
        diagnosis = self.result().trace["attempts"][0]["diagnosis"]
        self.assertIn("facts", diagnosis)
        self.assertIn("revisable", diagnosis)


class BackendIndependenceTests(unittest.TestCase):
    """The orchestration layer must not know which engine it is driving."""

    def source(self):
        import pathlib

        from cad_experimental import agentic_loop

        return pathlib.Path(
            agentic_loop.__file__).read_text(encoding="utf-8")

    def test_the_loop_imports_no_backend_implementation(self) -> None:
        """cadquery_backend / freecad_backend must never be imported here."""
        import ast

        tree = ast.parse(self.source())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        joined = " ".join(imported)
        self.assertNotIn("cadquery_backend", joined)
        self.assertNotIn("freecad_backend", joined)

    def test_the_loop_touches_no_backend_module_at_all(self) -> None:
        """Stronger than it was: the loop needs no backend seam whatsoever.

        Selector evidence is read from the ExecutionResult the executor
        already produced, so this module no longer calls a backend even
        indirectly. It was previously reaching for ``resolve_backend`` to
        re-describe edges; that is gone, and with it the dependency on
        ``describe_edges`` -- which FreeCadBackend does not implement.
        """
        import ast

        tree = ast.parse(self.source())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
        for forbidden in ("cad_backend", "cadquery_backend",
                          "freecad_backend", "edge_semantics"):
            self.assertFalse(
                any(forbidden in module for module in modules), forbidden)

    def test_resolve_backend_itself_still_never_falls_back(self) -> None:
        """The project-wide rule, asserted where it lives."""
        import inspect

        from cad_experimental.cad_backend import resolve_backend

        self.assertNotIn("except", inspect.getsource(resolve_backend))

    def test_diagnosis_consumes_only_neutral_types(self) -> None:
        """A diagnosis can be produced from a hand-built InspectionResult."""
        diagnosis = diagnose(
            Task("t"),
            PlanCandidate("c", "planner", chamfer_plan({"select": "all"}),
                          plan=object()),
            None,
            InspectionResult(built=False, backend="a-fake-engine",
                             error_text="anything"))
        self.assertEqual(diagnosis.backend, "a-fake-engine")

    def test_the_backend_name_is_carried_not_chosen(self) -> None:
        """A run records the engine it was asked for, unchanged."""
        result = run_agentic_cad_task(
            Task("a plate with a hole"), ScriptedPlanner(plate_with_hole()),
            backend="cadquery")
        self.assertEqual(
            result.final_attempt.inspection.backend, "cadquery")

    def test_no_backend_literal_is_assigned_inside_the_module(self) -> None:
        """The only backend string is the parameter default."""
        import ast

        tree = ast.parse(self.source())
        assigned = [
            node.value.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and node.value.value in ("cadquery", "freecad")
        ]
        self.assertEqual(assigned, [])


if __name__ == "__main__":
    unittest.main()
