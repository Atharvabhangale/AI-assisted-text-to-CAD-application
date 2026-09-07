"""Unit tests for the Onshape/MCP boundary.

Nothing here contacts Onshape, and no test asserts that Onshape accepted
anything. Networking is disabled while the workflow runs, and the boundary
modules are scanned to prove they encode no MCP tool name or wire format.
"""

from __future__ import annotations

import ast
import socket
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_core import generate_featurescript, validate
from cad_core.featurescript import UnsupportedPartError
from cad_core.model import Box, Part, Size
from cad_core.onshape_adapter import (
    HANDLE_KINDS,
    OPERATIONS,
    AdapterResult,
    DeliveryReport,
    GeneratedFeatureScript,
    Handle,
    OnshapeAdapter,
    OperationStatus,
    deliver_part,
    featurescript_for,
)
from cad_core.onshape_fakes import (
    FAKE_TOKEN_PREFIX,
    RecordingOnshapeAdapter,
    UnconfiguredOnshapeAdapter,
)

SRC = Path(__file__).resolve().parents[1] / "src" / "cad_core"
BOUNDARY_MODULES = (SRC / "onshape_adapter.py", SRC / "onshape_fakes.py")


def box_document(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
        "position": {"x": 10, "y": 20, "z": 30},
    }
    feature.update(overrides.pop("feature", {}))
    document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate-100x60x10",
        "features": [feature],
    }
    document.update(overrides)
    return document


class BoundaryTestCase(unittest.TestCase):
    def part_from(self, document: Dict[str, Any]) -> Part:
        result = validate(document)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part


# --- 1 & 2: generated FeatureScript crosses the boundary intact ------------


class TestGeneratedSourceCrossesTheBoundary(BoundaryTestCase):
    def test_generated_featurescript_can_be_passed_to_the_adapter(self) -> None:
        part = self.part_from(box_document())
        adapter = RecordingOnshapeAdapter()
        report = deliver_part(part, adapter)
        self.assertIsInstance(report, DeliveryReport)
        self.assertTrue(report.completed)
        self.assertEqual(len(report.results), len(OPERATIONS))
        self.assertEqual(len(adapter.submitted_sources), 1)

    def test_adapter_receives_exactly_the_generated_source(self) -> None:
        part = self.part_from(box_document())
        expected = generate_featurescript(part)
        adapter = RecordingOnshapeAdapter()
        deliver_part(part, adapter)
        self.assertEqual(adapter.submitted_sources[0], expected)
        self.assertEqual(
            adapter.submitted_sources[0].encode("utf-8"), expected.encode("utf-8")
        )

    def test_wrapper_carries_the_source_and_its_provenance(self) -> None:
        part = self.part_from(box_document())
        script = featurescript_for(part)
        self.assertIsInstance(script, GeneratedFeatureScript)
        self.assertEqual(script.source, generate_featurescript(part))
        self.assertEqual(script.part_name, "plate-100x60x10")
        self.assertEqual(script.feature_id, "plate")

    def test_source_is_not_altered_on_the_way_through(self) -> None:
        part = self.part_from(box_document())
        adapter = RecordingOnshapeAdapter()
        deliver_part(part, adapter)
        submitted = adapter.recorded_operations[1].script
        assert submitted is not None
        self.assertIn('"corner1" : vector(10, 20, 30) * millimeter,', submitted.source)
        self.assertIn('"corner2" : vector(110, 80, 40) * millimeter', submitted.source)


# --- the pipeline stays upstream of FeatureScript --------------------------


class TestPipelineOrder(BoundaryTestCase):
    """Raw FeatureScript must not be a normal input to the application."""

    def test_delivery_takes_a_part_not_source_text(self) -> None:
        adapter = RecordingOnshapeAdapter()
        source = generate_featurescript(self.part_from(box_document()))
        with self.assertRaises(TypeError):
            deliver_part(source, adapter)  # type: ignore[arg-type]
        self.assertEqual(adapter.recorded_operations, ())

    def test_delivery_rejects_a_raw_document(self) -> None:
        adapter = RecordingOnshapeAdapter()
        with self.assertRaises(TypeError):
            deliver_part(box_document(), adapter)  # type: ignore[arg-type]
        self.assertEqual(adapter.recorded_operations, ())

    def test_generation_happens_before_any_adapter_call(self) -> None:
        """An unsupported part must not reach the boundary at all."""
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="two-boxes",
            features=(
                Box(id="a", size=Size(1.0, 1.0, 1.0)),
                Box(id="b", size=Size(1.0, 1.0, 1.0)),
            ),
        )
        adapter = RecordingOnshapeAdapter()
        with self.assertRaises(UnsupportedPartError):
            deliver_part(part, adapter)
        self.assertEqual(adapter.recorded_operations, ())


# --- 3: deterministic recording -------------------------------------------


class TestRecordingIsDeterministic(BoundaryTestCase):
    def test_operations_are_recorded_in_the_declared_order(self) -> None:
        adapter = RecordingOnshapeAdapter()
        deliver_part(self.part_from(box_document()), adapter)
        self.assertEqual(adapter.operation_names, OPERATIONS)

    def test_identical_runs_record_identically(self) -> None:
        part = self.part_from(box_document())
        runs = []
        for _ in range(5):
            adapter = RecordingOnshapeAdapter()
            report = deliver_part(part, adapter)
            runs.append(
                (
                    adapter.recorded_operations,
                    tuple(r.status for r in report.results),
                    tuple(r.handle for r in report.results),
                    tuple(r.detail for r in report.results),
                )
            )
        self.assertEqual(len(set(runs)), 1)

    def test_handle_tokens_are_deterministic_and_plainly_synthetic(self) -> None:
        adapter = RecordingOnshapeAdapter()
        report = deliver_part(self.part_from(box_document()), adapter)
        tokens = [r.handle.token for r in report.results if r.handle is not None]
        self.assertEqual(
            tokens, ["fake-target-1", "fake-feature_studio-2", "fake-part_studio-3"]
        )
        for token in tokens:
            self.assertTrue(token.startswith(FAKE_TOKEN_PREFIX + "-"))

    def test_handles_are_passed_back_to_the_adapter(self) -> None:
        adapter = RecordingOnshapeAdapter()
        deliver_part(self.part_from(box_document()), adapter)
        records = adapter.recorded_operations
        self.assertEqual(records[1].handle, Handle("target", "fake-target-1"))
        self.assertEqual(
            records[2].handle, Handle("feature_studio", "fake-feature_studio-2")
        )
        self.assertEqual(
            records[4].handle, Handle("part_studio", "fake-part_studio-3")
        )

    def test_handle_kinds_are_the_declared_ones(self) -> None:
        adapter = RecordingOnshapeAdapter()
        report = deliver_part(self.part_from(box_document()), adapter)
        for result in report.results:
            if result.handle is not None:
                self.assertIn(result.handle.kind, HANDLE_KINDS)

    def test_target_name_defaults_to_the_part_name(self) -> None:
        adapter = RecordingOnshapeAdapter()
        deliver_part(self.part_from(box_document()), adapter)
        self.assertEqual(adapter.recorded_operations[0].name, "plate-100x60x10")

    def test_target_name_can_be_overridden(self) -> None:
        adapter = RecordingOnshapeAdapter()
        deliver_part(self.part_from(box_document()), adapter, target_name="scratch")
        self.assertEqual(adapter.recorded_operations[0].name, "scratch")


# --- 5: the not-configured state ------------------------------------------


class TestNotConfigured(BoundaryTestCase):
    def test_unconfigured_adapter_reports_not_configured(self) -> None:
        report = deliver_part(self.part_from(box_document()), UnconfiguredOnshapeAdapter())
        self.assertFalse(report.completed)
        stopped = report.stopped_at
        assert stopped is not None
        self.assertIs(stopped.status, OperationStatus.NOT_CONFIGURED)
        self.assertIn("no Onshape adapter is configured", stopped.detail)

    def test_unconfigured_adapter_stops_before_doing_anything(self) -> None:
        report = deliver_part(self.part_from(box_document()), UnconfiguredOnshapeAdapter())
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].operation, "open_target")

    def test_every_operation_reports_not_configured(self) -> None:
        adapter = UnconfiguredOnshapeAdapter()
        handle = Handle("target", "unused")
        script = featurescript_for(self.part_from(box_document()))
        results = [
            adapter.open_target("x"),
            adapter.submit_feature_studio_source(handle, script),
            adapter.commit_feature_studio(handle),
            adapter.instantiate_feature(handle),
            adapter.request_model_summary(handle),
        ]
        self.assertEqual([r.operation for r in results], list(OPERATIONS))
        for result in results:
            self.assertIs(result.status, OperationStatus.NOT_CONFIGURED)
            self.assertFalse(result.reached_onshape)
            self.assertIsNone(result.handle)


# --- 6: remote failures are distinct from validation failures -------------


class TestFailureKindsAreDistinct(BoundaryTestCase):
    def test_the_boundary_declares_the_required_states(self) -> None:
        self.assertEqual(
            {status.value for status in OperationStatus},
            {
                "succeeded",
                "not_configured",
                "unavailable",
                "authentication_required",
                "remote_failed",
            },
        )

    def test_remote_failure_is_reported_as_a_result(self) -> None:
        adapter = RecordingOnshapeAdapter(fail_at="commit_feature_studio")
        report = deliver_part(self.part_from(box_document()), adapter)
        stopped = report.stopped_at
        assert stopped is not None
        self.assertIs(stopped.status, OperationStatus.REMOTE_FAILED)
        self.assertEqual(stopped.operation, "commit_feature_studio")
        self.assertFalse(report.completed)

    def test_unavailable_and_authentication_required_are_expressible(self) -> None:
        for status in (
            OperationStatus.UNAVAILABLE,
            OperationStatus.AUTHENTICATION_REQUIRED,
        ):
            with self.subTest(status=status):
                adapter = RecordingOnshapeAdapter(
                    fail_at="open_target", failure_status=status
                )
                report = deliver_part(self.part_from(box_document()), adapter)
                stopped = report.stopped_at
                assert stopped is not None
                self.assertIs(stopped.status, status)

    def test_validation_failure_never_becomes_an_adapter_result(self) -> None:
        """An invalid specification has no route to the boundary."""
        result = validate(box_document(feature={"size": {"x": -1, "y": 60, "z": 10}}))
        self.assertFalse(result.valid)
        self.assertIsNone(result.part)
        adapter = RecordingOnshapeAdapter()
        with self.assertRaises(TypeError):
            deliver_part(result.part, adapter)  # type: ignore[arg-type]
        self.assertEqual(adapter.recorded_operations, ())

    def test_unsupported_part_raises_rather_than_reporting_a_status(self) -> None:
        from cad_core.model import Cylinder

        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(Cylinder(id="c", diameter=8.0, height=20.0),),
        )
        adapter = RecordingOnshapeAdapter()
        with self.assertRaises(UnsupportedPartError):
            deliver_part(part, adapter)
        self.assertEqual(adapter.recorded_operations, ())

    def test_failure_status_cannot_be_success(self) -> None:
        with self.assertRaises(ValueError):
            RecordingOnshapeAdapter(
                fail_at="open_target", failure_status=OperationStatus.SUCCEEDED
            )


# --- 7: no concrete MCP tool name or wire format --------------------------


class TestNoInventedMcpApi(unittest.TestCase):
    FORBIDDEN = (
        "mcp__", "tools/call", "jsonrpc", "json-rpc", "https://", "http://",
        "Bearer", "api_key", "apikey", "access_key", "secret_key", "client_secret",
        "/api/", "documentId", "workspaceId", "elementId", "partstudios",
        "featurestudios", "onshape.com", "oauth",
    )

    def test_boundary_modules_encode_no_wire_format_or_tool_name(self) -> None:
        for path in BOUNDARY_MODULES:
            text = path.read_text(encoding="utf-8")
            for token in self.FORBIDDEN:
                with self.subTest(module=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_boundary_modules_import_no_client_library(self) -> None:
        forbidden = {
            "mcp", "socket", "ssl", "http", "urllib", "urllib3", "requests",
            "httpx", "websockets", "aiohttp", "subprocess", "os",
        }
        for path in BOUNDARY_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(module=path.name):
                self.assertEqual(imported & forbidden, set())

    def test_operation_names_are_this_projects_vocabulary(self) -> None:
        self.assertEqual(
            OPERATIONS,
            (
                "open_target",
                "submit_feature_studio_source",
                "commit_feature_studio",
                "instantiate_feature",
                "request_model_summary",
            ),
        )
        for name in OPERATIONS:
            with self.subTest(name=name):
                self.assertTrue(name.isidentifier())
                self.assertFalse(name.startswith("_"))

    def test_the_protocol_is_what_callers_depend_on(self) -> None:
        """Application code depends on the abstraction, not an implementation."""
        for adapter in (RecordingOnshapeAdapter(), UnconfiguredOnshapeAdapter()):
            with self.subTest(adapter=type(adapter).__name__):
                self.assertIsInstance(adapter, OnshapeAdapter)
        for name in OPERATIONS:
            with self.subTest(name=name):
                self.assertTrue(hasattr(OnshapeAdapter, name))

    def test_handles_do_not_model_onshape_identifiers(self) -> None:
        handle = Handle(kind="target", token="anything-opaque")
        self.assertEqual(
            {f.name for f in handle.__dataclass_fields__.values()}, {"kind", "token"}
        )


# --- 8: the fakes never claim real Onshape success ------------------------


class TestNoFalseSuccessClaims(BoundaryTestCase):
    def test_no_result_claims_to_have_reached_onshape(self) -> None:
        for adapter in (RecordingOnshapeAdapter(), UnconfiguredOnshapeAdapter()):
            with self.subTest(adapter=type(adapter).__name__):
                report = deliver_part(self.part_from(box_document()), adapter)
                self.assertFalse(report.reached_onshape)
                for result in report.results:
                    self.assertFalse(result.reached_onshape)

    def test_success_details_say_no_service_was_contacted(self) -> None:
        adapter = RecordingOnshapeAdapter()
        report = deliver_part(self.part_from(box_document()), adapter)
        self.assertTrue(report.completed)
        for result in report.results:
            self.assertIn("no Onshape service was contacted", result.detail)

    def test_model_summary_reports_no_geometry_rather_than_inventing_any(self) -> None:
        adapter = RecordingOnshapeAdapter()
        report = deliver_part(self.part_from(box_document()), adapter)
        summary = report.results[-1]
        self.assertEqual(summary.operation, "request_model_summary")
        self.assertEqual(dict(summary.data), {})
        self.assertIn("no model information is available", summary.detail)
        for fabricated in ("110", "boundingBox", "minCorner", "maxCorner", "solid"):
            with self.subTest(fabricated=fabricated):
                self.assertNotIn(fabricated, summary.detail)

    def test_reached_onshape_defaults_to_false(self) -> None:
        self.assertFalse(
            AdapterResult(
                operation="open_target",
                status=OperationStatus.SUCCEEDED,
                detail="d",
            ).reached_onshape
        )


# --- 4: no network access -------------------------------------------------


class TestNoNetworkAccess(BoundaryTestCase):
    def test_full_workflow_runs_with_networking_disabled(self) -> None:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the boundary must not use the network")

        saved = (socket.socket, socket.create_connection, socket.getaddrinfo)
        socket.socket = refuse  # type: ignore[assignment]
        socket.create_connection = refuse  # type: ignore[assignment]
        socket.getaddrinfo = refuse  # type: ignore[assignment]
        try:
            adapter = RecordingOnshapeAdapter()
            report = deliver_part(self.part_from(box_document()), adapter)
            unconfigured = deliver_part(
                self.part_from(box_document()), UnconfiguredOnshapeAdapter()
            )
        finally:
            socket.socket, socket.create_connection, socket.getaddrinfo = saved  # type: ignore[assignment]
        self.assertTrue(report.completed)
        self.assertEqual(len(unconfigured.results), 1)

    def test_no_dynamic_evaluation_in_the_boundary(self) -> None:
        for path in BOUNDARY_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            with self.subTest(module=path.name):
                self.assertEqual(
                    called & {"eval", "exec", "compile", "__import__", "open"}, set()
                )

    def test_no_credentials_are_read_or_stored(self) -> None:
        """Checked on the parsed tree, so prose in docstrings cannot trip it."""
        for path in BOUNDARY_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            } | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            with self.subTest(module=path.name):
                self.assertEqual(
                    names & {"environ", "getenv", "getpass", "netrc"}, set()
                )

    def test_no_secret_shaped_string_literals(self) -> None:
        """No literal in the boundary looks like a key, token or URL."""
        for path in BOUNDARY_MODULES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            literals = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            ]
            for literal in literals:
                if literal.count("\n") > 2:  # docstrings carry prose, not secrets
                    continue
                with self.subTest(module=path.name, literal=literal[:40]):
                    self.assertNotRegex(literal, r"(?i)^(sk-|pk-|bearer |https?://)")
                    self.assertNotRegex(literal, r"^[A-Za-z0-9+/]{32,}={0,2}$")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
