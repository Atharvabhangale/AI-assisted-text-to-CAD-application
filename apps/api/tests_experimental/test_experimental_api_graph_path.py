"""The HTTP build route must serve graph-executed plans, not reject them.

Found by running the app. `POST /experimental/build-plan` assumed every
successful build produces a `BuildOutcome`, and returned
``400 {"error": "the plan cannot be built"}`` when it did not. But a plan
whose selector is richer than Section C.7 can express -- a `straight`, a
`circular`, a rim's `position` -- is built by the graph-driven executor,
which produces an `ExecutionResult` and **no** `BuildOutcome` by design.

So every capability Stages 47-60 built around semantic selectors was
unreachable over HTTP while building perfectly underneath. No test caught
it because the agentic loop calls `build_plan` directly and never goes
through the route.

These tests pin all three paths: the V1 document path unchanged, the graph
path served, and a graph *failure* keeping its structured diagnosis instead
of collapsing to the generic message.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from fastapi.testclient import TestClient

from cad_core.application_service import CadApplicationService

from cad_experimental.app import BUILD_PATH, create_app
from cad_experimental.config import ExperimentalConfig

CONFIG = ExperimentalConfig()


def plan_with(edges, *, operation="chamfer", size=1.0):
    """A plate, a bore, and one edge treatment with the given selector."""
    length = "radius" if operation == "fillet" else "distance"
    return {
        "status": "generated",
        "summary": "a plate with a hole and an edge treatment",
        "operations": [
            {"id": "plate", "type": "box",
             "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
            {"id": "hole", "type": "through_hole", "target": "plate",
             "parameters": {"diameter": 20.0,
                            "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
            {"id": "rim", "type": operation, "target": "plate",
             "parameters": {length: size, "edges": edges}},
        ],
    }


class GraphExecutedBuildRouteTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.service = CadApplicationService.local(tempfile.mkdtemp())

    def client(self) -> TestClient:
        return TestClient(create_app(config=CONFIG, service=self.service))

    def build(self, payload):
        return self.client().post(BUILD_PATH, json={"plan": payload})

    # --- (a) the V1 path is untouched ---------------------------------------

    def test_a_v1_selector_still_returns_a_document(self) -> None:
        response = self.build(plan_with({"select": "all"}))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("document", body)
        self.assertIn("build", body)
        self.assertNotIn("executed_by_graph", body)

    def test_the_v1_document_is_still_a_canonical_v1_document(self) -> None:
        body = self.build(plan_with({"select": "all"})).json()
        document = body["document"]
        self.assertEqual(document["schema_version"], "1.0.0")
        self.assertEqual(document["units"], "mm")

    # --- (b) the graph path is served ---------------------------------------

    def test_a_circular_top_selector_now_builds_over_http(self) -> None:
        """The defect, as a test: this returned 400 before the fix."""
        response = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"}))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["executed_by_graph"])
        self.assertTrue(body["execution"]["succeeded"])

    def test_the_graph_response_carries_typed_selector_resolutions(
        self,
    ) -> None:
        """The evidence must survive the transport, not just the build."""
        body = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"})).json()
        selections = body["execution"]["selections"]
        self.assertIn("rim", selections)
        resolution = selections["rim"]
        # one of the bore's two rims -- which is what `top` means, and what
        # volume alone could never show
        self.assertEqual(len(resolution["indices"]), 1)
        self.assertEqual(len(resolution["candidates"]), 2)

    def test_top_and_bottom_name_different_edges_over_http(self) -> None:
        top = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"})).json()
        bottom = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "bottom"})).json()
        self.assertNotEqual(
            top["execution"]["selections"]["rim"]["indices"],
            bottom["execution"]["selections"]["rim"]["indices"])

    def test_the_graph_response_carries_measured_geometry(self) -> None:
        body = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"})).json()
        bodies = body["execution"]["bodies"]
        self.assertEqual(len(bodies), 1)
        measurement = bodies[0]["measurement"]
        self.assertEqual(measurement["solid_count"], 1)
        self.assertGreater(measurement["volume"], 0)

    def test_the_graph_response_names_the_backend_and_the_order(self) -> None:
        body = self.build(plan_with(
            {"select": "straight", "axis": "Z"})).json()
        execution = body["execution"]
        self.assertEqual(execution["backend"], "cadquery")
        self.assertEqual(execution["order"], ["plate", "hole", "rim"])

    def test_no_v1_document_is_invented_for_a_graph_plan(self) -> None:
        """`execution` and `document` are never both present, by design."""
        body = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"})).json()
        self.assertNotIn("document", body)

    # --- (c) a graph failure keeps its structured diagnosis -----------------

    def test_a_graph_failure_returns_structured_information(self) -> None:
        """Not the generic "the plan cannot be built"."""
        response = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"},
            operation="fillet", size=25.0))
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertTrue(body["executed_by_graph"])
        self.assertFalse(body["execution"]["succeeded"])
        self.assertNotIn("error", body)

    def test_a_graph_failure_names_the_rule_and_the_operation(self) -> None:
        body = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"},
            operation="fillet", size=25.0)).json()
        failure = body["execution"]["failure"]
        self.assertEqual(failure["operation"], "rim")
        self.assertIn("E5", failure["message"])

    def test_a_failed_execution_still_carries_its_selections(self) -> None:
        """The proof that the selector was right and the radius was not."""
        body = self.build(plan_with(
            {"select": "circular", "axis": "Z", "position": "top"},
            operation="fillet", size=25.0)).json()
        resolution = body["execution"]["selections"]["rim"]
        self.assertEqual(len(resolution["indices"]), 1)
        self.assertEqual(len(resolution["candidates"]), 2)

    def test_the_http_layer_reruns_no_geometry(self) -> None:
        """The route serialises what the executor returned, nothing more."""
        import inspect

        from cad_experimental import app as module

        source = inspect.getsource(module.create_app)
        marker = source.index("executed_by_graph")
        branch = source[marker:marker + 900]
        for forbidden in ("execute_plan(", "describe_edges", "resolve(",
                          "build_plan("):
            self.assertNotIn(forbidden, branch, forbidden)


if __name__ == "__main__":
    unittest.main()
