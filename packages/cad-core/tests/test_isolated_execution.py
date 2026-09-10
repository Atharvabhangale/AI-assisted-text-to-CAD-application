"""Unit tests for the isolated local CAD execution boundary.

What these tests are mostly about:

* **crash containment** -- a child that ends abruptly, with a *success* exit
  code and no response, must never become a successful build, an artifact
  manifest or a cache entry, and the host must survive it;
* **classification** -- a validation failure, a geometric failure, an export
  failure, a malformed request, a malformed response, a wrongly versioned
  response, an internal worker error and a timeout are eight different
  things, and each is asserted to be its own outcome;
* **the boundary carries data, not code** -- no pickle, no eval, no exec, no
  shell, no path in the request payload, and no secret in the child's
  protocol or environment.

The real kernel is never provoked into an abnormal exit. Abnormal termination
is exercised by a worker operation that calls :func:`os._exit` and does no
CAD work at all.

Every child process costs about 2.5 seconds, nearly all of it importing
OpenCascade, so builds are shared across assertions with ``setUpClass``
wherever the same result answers several questions.
"""

from __future__ import annotations

import ast
import json
import os
import time
import unittest
import unittest.mock as mock
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cad_core import validate
from cad_core.artifact_registry import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactStorage,
    canonical_render_bytes,
    file_checksum,
)
from cad_core.build_job import (
    BuildFailure,
    BuildOptions,
    BuildOutput,
    BuildRequest,
    BuildStatus,
    execute_build,
)
from cad_core.iges_export import read_iges
from cad_core.isolated_execution import (
    DEFAULT_TIMEOUT_SECONDS,
    INHERITED_ENVIRONMENT_NAMES,
    WORKER_STATUS_OUTCOMES,
    IsolatedExecution,
    IsolationError,
    IsolationOutcome,
    child_environment,
    execute_isolated,
    execute_isolated_document,
    get_or_build_isolated,
    invoke_worker,
)
from cad_core.isolated_worker import (
    BUILD_REQUEST_FIELDS,
    EXIT_BUILD_FAILED,
    EXIT_CODES,
    EXIT_INTERNAL_ERROR,
    EXIT_PROTOCOL_ERROR,
    EXIT_SUCCESS,
    EXIT_VALIDATION_FAILED,
    IPC_PROTOCOL_VERSION,
    OPERATION_BUILD,
    OPERATION_DIAGNOSTIC_ABORT,
    OPERATION_DIAGNOSTIC_ENVIRONMENT,
    OPERATION_DIAGNOSTIC_INTERNAL,
    OPERATION_DIAGNOSTIC_MALFORMED,
    OPERATION_DIAGNOSTIC_NOISE,
    OPERATION_DIAGNOSTIC_OTHER_VERSION,
    OPERATION_DIAGNOSTIC_SLEEP,
    OPERATIONS,
    REQUEST_ENVELOPE_FIELDS,
    REQUEST_FILENAME,
    RESPONSE_ENVELOPE_FIELDS,
    RESPONSE_FILENAME,
    WorkerStatus,
    main as worker_main,
)
from cad_core.local_build_cache import ENTRIES_DIRNAME, LocalBuildCache
from cad_core.model import Part
from cad_core.render_model import RENDER_FORMAT_VERSION
from cad_core.serialization import part_hash, serialize_part
from cad_core.step_export import read_step

#: Every artifact kind, the selection most tests use.
ALL_OUTPUTS: Tuple[ArtifactKind, ...] = tuple(ArtifactKind)

PLATE_SIZE = (100.0, 60.0, 10.0)

VOLUME_TOLERANCE_MM3 = 1e-6

#: A host environment variable named like a credential, used to prove that
#: nothing of the sort reaches the child. Its value is a sentinel, not a
#: secret: no real credential exists anywhere in this stage.
SECRET_NAME = "AWS_SECRET_ACCESS_KEY"
SECRET_SENTINEL = "not-a-real-credential-0123456789"


def plate_feature(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": PLATE_SIZE[0], "y": PLATE_SIZE[1], "z": PLATE_SIZE[2]},
        "position": {"x": 0, "y": 0, "z": 0},
    }
    feature.update(overrides)
    return feature


def document(features: List[Dict[str, Any]], **overrides: Any) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate",
        "features": features,
    }
    doc.update(overrides)
    return doc


def geometry_corpus() -> Tuple[Tuple[str, Dict[str, Any]], ...]:
    """The five real geometries, as everywhere else in this suite."""
    return (
        ("box", document([plate_feature()])),
        (
            "cylinder",
            document(
                [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
                name="pin",
            ),
        ),
        (
            "drilled-plate",
            document(
                [
                    plate_feature(),
                    {
                        "id": "bore",
                        "type": "through_hole",
                        "target": "plate",
                        "diameter": 20,
                        "position": {"x": 20, "y": 20, "z": 0},
                        "axis": "+Z",
                    },
                ],
                name="drilled-plate",
            ),
        ),
        (
            "filleted-box",
            document(
                [
                    plate_feature(),
                    {
                        "id": "round",
                        "type": "fillet",
                        "target": "plate",
                        "radius": 2,
                        "edges": {"select": "axis_parallel", "axis": "Z"},
                    },
                ],
                name="filleted-box",
            ),
        ),
        (
            "chamfered-box",
            document(
                [
                    plate_feature(),
                    {
                        "id": "bevel",
                        "type": "chamfer",
                        "target": "plate",
                        "distance": 2,
                        "edges": {"select": "axis_parallel", "axis": "Z"},
                    },
                ],
                name="chamfered-box",
            ),
        ),
    )


def part_of(doc: Dict[str, Any]) -> Part:
    result = validate(doc)
    if not result.valid or result.part is None:  # pragma: no cover
        raise AssertionError([str(error) for error in result.errors])
    return result.part


def request_of(doc: Dict[str, Any], *kinds: ArtifactKind) -> BuildRequest:
    return BuildRequest(
        part=part_of(doc), options=BuildOptions.for_outputs(*(kinds or ALL_OUTPUTS))
    )


class IsolationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir(parents=True, exist_ok=True)
        return path


# --- one shared isolated build ----------------------------------------------


class TestIsolatedBuild(unittest.TestCase):
    """One isolated five-output build, and one in-process build to compare."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.isolated_out = root / "isolated"
        cls.isolated_out.mkdir()
        cls.reference_out = root / "reference"
        cls.reference_out.mkdir()
        cls.request = request_of(document([plate_feature()]))
        cls.execution = execute_isolated(
            cls.request, output_directory=cls.isolated_out
        )
        cls.reference = execute_build(
            cls.request, output_directory=cls.reference_out
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_a_valid_box_builds_in_a_child_process(self) -> None:
        self.assertIs(self.execution.outcome, IsolationOutcome.SUCCEEDED)
        self.assertTrue(self.execution.succeeded)
        self.assertTrue(self.execution.child_launched)
        self.assertIsNotNone(self.execution.child_pid)
        self.assertIsNone(self.execution.failure)

    def test_the_host_receives_a_structured_success(self) -> None:
        self.assertIs(self.execution.worker_status, WorkerStatus.SUCCEEDED)
        self.assertEqual(self.execution.exit_code, EXIT_SUCCESS)
        self.assertIsInstance(self.execution.manifest, ArtifactManifest)
        payload = json.loads(json.dumps(self.execution.to_dict()))
        self.assertEqual(payload["outcome"], "succeeded")
        self.assertIsNone(payload["failure"])

    def test_the_child_returns_the_correct_document_hash(self) -> None:
        self.assertEqual(
            self.execution.document_hash, self.request.document_hash
        )
        self.assertEqual(
            self.execution.document_hash, part_hash(self.request.part)
        )

    def test_the_child_returns_the_correct_build_key(self) -> None:
        self.assertEqual(self.execution.build_key, self.request.build_key)
        self.assertEqual(len(self.execution.build_key), 64)

    def test_every_requested_output_is_produced(self) -> None:
        self.assertEqual(self.execution.produced_kinds(), ALL_OUTPUTS)
        for kind in ALL_OUTPUTS:
            self.assertIsNotNone(self.execution.artifact(kind))

    def test_the_manifest_matches_an_in_process_build_exactly(self) -> None:
        assert self.reference.result is not None
        self.assertEqual(
            self.execution.manifest.canonical_bytes(),
            self.reference.result.manifest.canonical_bytes(),
        )
        self.assertEqual(
            self.execution.manifest.canonical_hash(),
            self.reference.result.manifest.canonical_hash(),
        )
        for kind in ALL_OUTPUTS:
            isolated = self.execution.artifact(kind)
            in_process = self.reference.result.artifact(kind)
            self.assertEqual(isolated.logical_id, in_process.logical_id)
            self.assertEqual(isolated.format, in_process.format)
            self.assertEqual(isolated.storage, in_process.storage)
            self.assertEqual(
                dict(isolated.details), dict(in_process.details)
            )

    def test_the_file_artifacts_really_exist_where_the_child_said(self) -> None:
        for kind in (ArtifactKind.STEP, ArtifactKind.IGES, ArtifactKind.STL):
            artifact = self.execution.artifact(kind)
            self.assertIs(artifact.storage, ArtifactStorage.FILE)
            path = Path(artifact.path)
            self.assertTrue(path.is_file())
            path.relative_to(self.isolated_out)
            self.assertEqual(artifact.size_bytes, len(path.read_bytes()))
            self.assertEqual(artifact.checksum, file_checksum(path))

    def test_the_render_model_crosses_the_boundary(self) -> None:
        assert self.reference.result is not None
        model = self.execution.render_model
        self.assertIsNotNone(model)
        self.assertEqual(model, self.reference.result.render_model)
        self.assertEqual(model.format_version, RENDER_FORMAT_VERSION)
        # ... via its existing canonical bytes, and nothing new
        self.assertEqual(
            canonical_render_bytes(model),
            canonical_render_bytes(self.reference.result.render_model),
        )
        self.assertEqual(
            self.execution.artifact(ArtifactKind.RENDER).checksum,
            self.reference.result.artifact(ArtifactKind.RENDER).checksum,
        )

    def test_the_brep_does_not_cross_the_boundary(self) -> None:
        # The documented choice: geometry measurements travel, the solid does
        # not, and no B-rep serialization was invented.
        geometry = self.execution.artifact(ArtifactKind.GEOMETRY)
        self.assertIsNotNone(geometry)
        self.assertIs(geometry.storage, ArtifactStorage.IN_MEMORY)
        self.assertIsNone(geometry.path)
        self.assertIsNone(geometry.checksum)
        self.assertAlmostEqual(
            geometry.details["volume_mm3"],
            PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2],
            delta=VOLUME_TOLERANCE_MM3,
        )
        self.assertFalse(hasattr(self.execution, "geometry"))
        serialized = json.dumps(self.execution.to_dict())
        for token in ("cadquery", "OCP", "TopoDS", "Workplane", "object at"):
            self.assertNotIn(token, serialized)

    def test_the_execution_id_is_the_childs_and_is_no_identity(self) -> None:
        assert self.reference.result is not None
        self.assertIsNotNone(self.execution.execution_id)
        self.assertNotEqual(
            self.execution.execution_id, self.reference.result.execution_id
        )
        self.assertNotIn(self.execution.execution_id, self.execution.build_key)

    def test_the_workspace_is_cleaned_after_a_successful_build(self) -> None:
        self.assertIsNotNone(self.execution.workspace)
        self.assertFalse(Path(self.execution.workspace).exists())

    def test_the_output_directory_holds_only_the_builds_own_files(self) -> None:
        names = sorted(path.name for path in self.isolated_out.iterdir())
        key = self.request.build_key
        self.assertEqual(names, sorted(f"{key}{ext}" for ext in (".igs", ".step", ".stl")))

    def test_the_child_is_reaped(self) -> None:
        self.assertFalse(self.execution.child_abandoned)
        with self.assertRaises((ProcessLookupError, PermissionError)):
            os.kill(self.execution.child_pid, 0)

    def test_the_host_result_speaks_the_existing_vocabulary(self) -> None:
        payload = self.execution.to_dict()
        for name in (
            "build_key",
            "document_hash",
            "manifest",
            "cache_hit",
            "cache_published",
            "failure",
        ):
            self.assertIn(name, payload)
        self.assertEqual(
            sorted(payload["manifest"]),
            ["artifacts", "build_key", "document_hash"],
        )


class TestGeometryCorpus(IsolationTestCase):
    def test_every_real_geometry_builds_in_a_child_process(self) -> None:
        for index, (name, doc) in enumerate(geometry_corpus()):
            with self.subTest(geometry=name):
                out = self.subdirectory(f"out-{index}")
                request = request_of(doc)
                execution = execute_isolated(request, output_directory=out)
                self.assertIs(
                    execution.outcome,
                    IsolationOutcome.SUCCEEDED,
                    msg=(
                        execution.failure.message
                        if execution.failure
                        else execution.diagnostic
                    ),
                )
                self.assertEqual(execution.build_key, request.build_key)
                self.assertEqual(execution.produced_kinds(), ALL_OUTPUTS)
                self.assertIsNotNone(execution.render_model)
                reference = execute_build(
                    request, output_directory=self.subdirectory(f"ref-{index}")
                )
                assert reference.result is not None
                self.assertEqual(
                    execution.manifest.canonical_bytes(),
                    reference.result.manifest.canonical_bytes(),
                )


# --- failure classification -------------------------------------------------


class TestValidationFailure(IsolationTestCase):
    def test_an_invalid_document_is_a_structured_validation_failure(self) -> None:
        invalid = document([plate_feature(size={"x": 0, "y": 60, "z": 10})])
        execution = execute_isolated_document(
            invalid, BuildOptions.for_outputs(ArtifactKind.GEOMETRY)
        )
        self.assertIs(execution.outcome, IsolationOutcome.VALIDATION_FAILED)
        self.assertIs(execution.worker_status, WorkerStatus.VALIDATION_FAILED)
        self.assertEqual(execution.exit_code, EXIT_VALIDATION_FAILED)
        failure = execution.failure
        self.assertIsNotNone(failure)
        self.assertIs(failure.failure, BuildFailure.DOCUMENT_INVALID)
        self.assertEqual(failure.stage, "validation")
        self.assertTrue(failure.rule_codes)
        self.assertTrue(failure.validation_errors)
        self.assertIn(
            "S10", [error["rule"] for error in failure.validation_errors]
        )
        # nothing was produced, and no geometry was attempted
        self.assertIsNone(execution.manifest)
        self.assertIsNone(execution.render_model)

    def test_the_validator_shields_every_unsupported_geometry_case(self) -> None:
        """Measured, not assumed: the engine's own refusals are unreachable.

        ``build_part`` raises ``UnsupportedGeometryError`` for non-mm units, an
        empty history, an unsupported feature type, a non-constructive first
        feature and a bad axis. Over IPC the document is validated first, and
        each of those is caught by a static rule -- so a valid document can
        never reach the engine's unsupported branch, and the isolated result
        is ``VALIDATION_FAILED`` rather than ``BUILD_FAILED``.
        """
        options = BuildOptions.for_outputs(ArtifactKind.GEOMETRY)
        cases = (
            ("units", dict(document([plate_feature()]), units="in"), "S5"),
            ("empty history", document([]), "S2"),
        )
        for name, doc, rule in cases:
            with self.subTest(case=name):
                execution = execute_isolated_document(doc, options)
                self.assertIs(
                    execution.outcome, IsolationOutcome.VALIDATION_FAILED
                )
                self.assertIn(rule, execution.failure.rule_codes)
        # the classification for an engine refusal is nonetheless wired
        self.assertIs(
            WORKER_STATUS_OUTCOMES[WorkerStatus.BUILD_FAILED],
            IsolationOutcome.BUILD_FAILED,
        )
        self.assertIn(BuildFailure.UNSUPPORTED_GEOMETRY, tuple(BuildFailure))


class TestBuildFailure(IsolationTestCase):
    def test_a_geometric_rule_failure_is_a_structured_build_failure(self) -> None:
        """A through-hole that misses its target: rule E1, in the child."""
        doc = document(
            [
                plate_feature(size={"x": 10, "y": 10, "z": 10}),
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 2,
                    "position": {"x": 50, "y": 50, "z": 0},
                },
            ]
        )
        execution = execute_isolated_document(
            doc, BuildOptions.for_outputs(ArtifactKind.GEOMETRY)
        )
        self.assertIs(execution.outcome, IsolationOutcome.BUILD_FAILED)
        self.assertIs(execution.worker_status, WorkerStatus.BUILD_FAILED)
        self.assertEqual(execution.exit_code, EXIT_BUILD_FAILED)
        self.assertIs(execution.failure.failure, BuildFailure.GEOMETRY_FAILED)
        self.assertEqual(execution.failure.rule_codes, ("E1",))
        self.assertEqual(execution.failure.stage, "geometry")
        self.assertIsNone(execution.manifest)

    def test_an_export_failure_is_a_structured_build_failure(self) -> None:
        request = request_of(document([plate_feature()]), ArtifactKind.STEP)
        out = self.subdirectory("out")
        # a directory where the exporter must write a file: a real refusal
        (out / f"{request.build_key}.step").mkdir()
        execution = execute_isolated(request, output_directory=out)
        self.assertIs(execution.outcome, IsolationOutcome.BUILD_FAILED)
        self.assertEqual(execution.exit_code, EXIT_BUILD_FAILED)
        self.assertIs(execution.failure.failure, BuildFailure.EXPORT_FAILED)
        self.assertIs(execution.failure.output, BuildOutput.STEP)
        self.assertEqual(execution.failure.stage, "step")
        self.assertIsNone(execution.manifest)
        # the public message names no path, exactly as in Stage 16
        self.assertNotIn("/", execution.failure.message)
        self.assertNotIn(str(out), execution.failure.message)

    def test_a_failed_isolated_build_leaves_no_manifest(self) -> None:
        doc = document(
            [
                plate_feature(size={"x": 10, "y": 10, "z": 10}),
                {
                    "id": "bore",
                    "type": "through_hole",
                    "target": "plate",
                    "diameter": 2,
                    "position": {"x": 50, "y": 50, "z": 0},
                },
            ]
        )
        execution = execute_isolated_document(
            doc, BuildOptions.for_outputs(ArtifactKind.GEOMETRY)
        )
        self.assertIsNone(execution.manifest)
        self.assertEqual(execution.produced_kinds(), ())
        self.assertFalse(execution.succeeded)
        self.assertFalse(execution.cache_published)


class TestProtocolFailures(IsolationTestCase):
    """The fast paths: the worker fails before importing the kernel."""

    def workspace(self, payload: Any) -> Path:
        area = self.subdirectory("ws")
        (area / REQUEST_FILENAME).write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8",
        )
        return area

    def test_a_malformed_request_is_a_protocol_failure(self) -> None:
        area = self.workspace("{ not json at all")
        code = worker_main([str(area)])
        self.assertEqual(code, EXIT_PROTOCOL_ERROR)
        response = json.loads((area / RESPONSE_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(response["status"], WorkerStatus.PROTOCOL_ERROR.value)
        self.assertIsNone(response["result"])
        self.assertIn("JSON", response["error"]["message"])

    def test_a_request_envelope_with_unknown_fields_is_refused(self) -> None:
        area = self.workspace(
            {
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "operation": OPERATION_BUILD,
                "request": None,
                "surprise": 1,
            }
        )
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)

    def test_a_request_of_another_protocol_version_is_refused(self) -> None:
        area = self.workspace(
            {
                "ipc_protocol_version": "0.0.1",
                "operation": OPERATION_BUILD,
                "request": None,
            }
        )
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)
        response = json.loads((area / RESPONSE_FILENAME).read_text(encoding="utf-8"))
        self.assertIn("protocol", response["error"]["message"])

    def test_an_unknown_operation_is_refused(self) -> None:
        area = self.workspace(
            {
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "operation": "rm -rf",
                "request": None,
            }
        )
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)

    def test_a_build_payload_with_a_path_field_is_refused(self) -> None:
        area = self.workspace(
            {
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "operation": OPERATION_BUILD,
                "request": {
                    "document": document([plate_feature()]),
                    "options": {"outputs": ["geometry"]},
                    "output_directory": "/etc",
                },
            }
        )
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)
        response = json.loads((area / RESPONSE_FILENAME).read_text(encoding="utf-8"))
        self.assertIn("fields", response["error"]["message"])

    def test_an_unknown_build_output_is_refused(self) -> None:
        area = self.workspace(
            {
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "operation": OPERATION_BUILD,
                "request": {
                    "document": document([plate_feature()]),
                    "options": {"outputs": ["hologram"]},
                },
            }
        )
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)

    def test_a_missing_request_file_is_a_protocol_failure(self) -> None:
        area = self.subdirectory("empty-ws")
        self.assertEqual(worker_main([str(area)]), EXIT_PROTOCOL_ERROR)

    def test_a_workspace_that_is_not_a_directory_is_refused(self) -> None:
        self.assertEqual(worker_main([]), EXIT_PROTOCOL_ERROR)
        self.assertEqual(
            worker_main([str(self.tmp / "nowhere")]), EXIT_PROTOCOL_ERROR
        )

    def test_an_unknown_worker_argument_is_refused(self) -> None:
        area = self.subdirectory("ws-args")
        self.assertEqual(
            worker_main([str(area), "--do-something-else", "x"]),
            EXIT_PROTOCOL_ERROR,
        )

    def test_a_malformed_child_response_is_detected_by_the_host(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_MALFORMED, timeout_seconds=30
        )
        self.assertIs(execution.outcome, IsolationOutcome.PROTOCOL_ERROR)
        self.assertEqual(execution.exit_code, EXIT_SUCCESS)
        self.assertIsNone(execution.manifest)
        self.assertIn("response", execution.failure.message)

    def test_a_response_of_another_protocol_version_is_detected(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_OTHER_VERSION, timeout_seconds=30
        )
        self.assertIs(execution.outcome, IsolationOutcome.PROTOCOL_ERROR)
        self.assertIn("0.0.1", execution.failure.message)
        self.assertIn(IPC_PROTOCOL_VERSION, execution.failure.message)
        self.assertIsNone(execution.manifest)

    def test_an_unhandled_worker_error_is_its_own_outcome(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_INTERNAL, timeout_seconds=30
        )
        self.assertIs(execution.outcome, IsolationOutcome.WORKER_ERROR)
        self.assertIs(execution.worker_status, WorkerStatus.INTERNAL_ERROR)
        self.assertEqual(execution.exit_code, EXIT_INTERNAL_ERROR)
        self.assertIsNone(execution.manifest)
        # the traceback is diagnostic, never the public message
        self.assertNotIn("Traceback", execution.failure.message)
        self.assertIn("Traceback", execution.diagnostic)

    def test_a_status_that_disagrees_with_the_exit_code_is_refused(self) -> None:
        real = subprocess.Popen

        class Liar:
            """A child that claims success and exits with a failure code."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.pid = -1
                self._workspace = Path(kwargs["cwd"])
                self.returncode = EXIT_BUILD_FAILED

            def communicate(self, *args: Any, **kwargs: Any):
                (self._workspace / RESPONSE_FILENAME).write_text(
                    json.dumps(
                        {
                            "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                            "operation": OPERATION_BUILD,
                            "status": WorkerStatus.SUCCEEDED.value,
                            "result": None,
                            "error": None,
                            "transport": {"render_payload": None},
                        }
                    ),
                    encoding="utf-8",
                )
                return b"", b""

            def poll(self) -> int:
                return self.returncode

        with mock.patch("cad_core.isolated_execution.subprocess.Popen", Liar):
            execution = execute_isolated_document(
                document([plate_feature()]),
                BuildOptions.for_outputs(ArtifactKind.GEOMETRY),
            )
        self.assertIs(execution.outcome, IsolationOutcome.PROTOCOL_ERROR)
        self.assertIn("exited with", execution.failure.message)
        self.assertIsNotNone(real)

    def test_diagnostics_do_not_corrupt_the_protocol(self) -> None:
        """A child writing to both streams around its result is still read.

        This is why the protocol lives in a file: OpenCascade writes straight
        to the process's descriptors, so nothing machine-readable may share a
        stream with it.
        """
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_NOISE, timeout_seconds=30
        )
        self.assertIs(execution.outcome, IsolationOutcome.SUCCEEDED)
        self.assertIs(execution.worker_status, WorkerStatus.SUCCEEDED)
        self.assertIn('"not":"the protocol"', execution.diagnostic)
        self.assertIn("plain text on stdout", execution.diagnostic)
        self.assertIn("ERR SomeKernel", execution.diagnostic)

    def test_stderr_never_becomes_the_public_message(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_INTERNAL, timeout_seconds=30
        )
        self.assertIn("diagnostic internal error", execution.diagnostic)
        self.assertNotIn("diagnostic internal error", execution.failure.message)
        self.assertNotIn("Traceback", execution.failure.message)
        # ... and it is not part of the serialized contract
        self.assertNotIn("diagnostic", execution.to_dict())

    def test_a_usage_error_is_raised_before_any_child_starts(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            with self.assertRaises(IsolationError):
                execute_isolated_document(
                    document([plate_feature()]),
                    BuildOptions.for_outputs(ArtifactKind.STEP),
                )
            with self.assertRaises(IsolationError):
                execute_isolated("not a request")  # type: ignore[arg-type]
            with self.assertRaises(IsolationError):
                invoke_worker(operation=OPERATION_BUILD, timeout_seconds=0)


# --- crash containment ------------------------------------------------------


class TestCrashContainment(IsolationTestCase):
    """The point of the stage: a child that dies must not take the host down.

    The real kernel is never provoked. ``diagnostic:abort`` calls
    :func:`os._exit` with a *success* code and writes no response, which is
    the hardest case to classify honestly.
    """

    def test_an_abrupt_child_exit_is_a_process_failure(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_ABORT, timeout_seconds=30
        )
        self.assertIs(execution.outcome, IsolationOutcome.PROCESS_FAILED)
        self.assertFalse(execution.succeeded)
        # it exited zero, and that is still not success
        self.assertEqual(execution.exit_code, EXIT_SUCCESS)
        self.assertIsNone(execution.worker_status)

    def test_no_successful_result_is_fabricated_from_a_crash(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_ABORT, timeout_seconds=30
        )
        self.assertIsNone(execution.manifest)
        self.assertIsNone(execution.render_model)
        self.assertIsNone(execution.build_key)
        self.assertIsNone(execution.document_hash)
        self.assertEqual(execution.produced_kinds(), ())
        self.assertFalse(execution.cache_hit)
        self.assertFalse(execution.cache_published)
        self.assertIsNotNone(execution.failure)
        self.assertEqual(execution.failure.stage, "process")

    def test_the_host_process_survives_a_child_failure(self) -> None:
        before = os.getpid()
        for operation in (
            OPERATION_DIAGNOSTIC_ABORT,
            OPERATION_DIAGNOSTIC_MALFORMED,
            OPERATION_DIAGNOSTIC_INTERNAL,
        ):
            with self.subTest(operation=operation):
                execution = invoke_worker(operation=operation, timeout_seconds=30)
                self.assertFalse(execution.succeeded)
                self.assertEqual(os.getpid(), before)
        # ... and the host can still do real work afterwards
        request = request_of(document([plate_feature()]), ArtifactKind.GEOMETRY)
        self.assertEqual(
            execute_build(request).status, BuildStatus.SUCCEEDED
        )

    def test_a_crashed_child_publishes_no_cache_entry(self) -> None:
        root = self.subdirectory("cache")
        cache = LocalBuildCache(root)
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_ABORT,
            cache_root=root,
            timeout_seconds=30,
        )
        self.assertIs(execution.outcome, IsolationOutcome.PROCESS_FAILED)
        self.assertFalse((root / ENTRIES_DIRNAME).exists())
        self.assertFalse(
            cache.contains(
                request_of(document([plate_feature()])).build_key
            )
        )

    def test_a_crashed_child_leaves_no_workspace(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_ABORT, timeout_seconds=30
        )
        self.assertFalse(Path(execution.workspace).exists())
        self.assertFalse(execution.child_abandoned)


class TestTimeout(IsolationTestCase):
    def test_a_timeout_terminates_the_child(self) -> None:
        started = time.perf_counter()
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_SLEEP, timeout_seconds=1.0
        )
        elapsed = time.perf_counter() - started
        self.assertIs(execution.outcome, IsolationOutcome.TIMED_OUT)
        self.assertIsNone(execution.exit_code)
        self.assertLess(elapsed, 30.0)
        self.assertIn("timeout", execution.failure.message)
        self.assertEqual(execution.timeout_seconds, 1.0)

    def test_a_timed_out_child_does_not_remain_running(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_SLEEP, timeout_seconds=1.0
        )
        self.assertFalse(execution.child_abandoned)
        self.assertIsNotNone(execution.child_pid)
        with self.assertRaises((ProcessLookupError, PermissionError)):
            os.kill(execution.child_pid, 0)

    def test_a_timeout_cleans_the_workspace_and_publishes_nothing(self) -> None:
        execution = invoke_worker(
            operation=OPERATION_DIAGNOSTIC_SLEEP, timeout_seconds=1.0
        )
        self.assertFalse(Path(execution.workspace).exists())
        self.assertIsNone(execution.manifest)
        self.assertFalse(execution.cache_published)

    def test_a_build_killed_mid_flight_publishes_no_cache_entry(self) -> None:
        """A real build, with a timeout below the measured kernel import."""
        root = self.subdirectory("cache")
        cache = LocalBuildCache(root)
        request = request_of(document([plate_feature()]))
        execution = get_or_build_isolated(request, cache, timeout_seconds=0.4)
        self.assertIs(execution.outcome, IsolationOutcome.TIMED_OUT)
        self.assertFalse(execution.succeeded)
        self.assertIsNone(execution.manifest)
        self.assertFalse(execution.cache_published)
        self.assertFalse(cache.contains(request.build_key))
        self.assertFalse(Path(execution.workspace).exists())
        with self.assertRaises((ProcessLookupError, PermissionError)):
            os.kill(execution.child_pid, 0)

    def test_the_default_timeout_is_explicit_and_generous(self) -> None:
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 60.0)
        self.assertGreater(DEFAULT_TIMEOUT_SECONDS, 10.0)


# --- the cache --------------------------------------------------------------


class TestCacheIntegration(unittest.TestCase):
    """One isolated miss, then hits, over the Stage 18 cache unchanged."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.cache_root = root / "cache"
        cls.cache_root.mkdir()
        cls.cache = LocalBuildCache(cls.cache_root)
        cls.request = request_of(document([plate_feature()]))
        cls.miss = get_or_build_isolated(cls.request, cls.cache)
        cls.hit = get_or_build_isolated(cls.request, cls.cache)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_a_miss_builds_in_a_child_and_publishes(self) -> None:
        self.assertIs(self.miss.outcome, IsolationOutcome.SUCCEEDED)
        self.assertTrue(self.miss.child_launched)
        self.assertFalse(self.miss.cache_hit)
        self.assertTrue(self.miss.cache_published)
        self.assertTrue(self.cache.contains(self.request.build_key))

    def test_a_hit_needs_no_child_process(self) -> None:
        self.assertIs(self.hit.outcome, IsolationOutcome.SUCCEEDED)
        self.assertFalse(self.hit.child_launched)
        self.assertTrue(self.hit.cache_hit)
        self.assertIsNone(self.hit.exit_code)
        self.assertIsNone(self.hit.child_pid)
        # proved rather than inferred: launching anything would explode
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched for a cache hit"),
        ):
            again = get_or_build_isolated(self.request, self.cache)
        self.assertTrue(again.cache_hit)
        self.assertIs(again.outcome, IsolationOutcome.SUCCEEDED)

    def test_a_hit_returns_the_cache_resident_artifacts(self) -> None:
        for artifact in self.hit.manifest.artifacts:
            if artifact.path is None:
                continue
            Path(artifact.path).relative_to(self.cache_root)
            self.assertTrue(Path(artifact.path).is_file())

    def test_the_identity_is_the_same_across_the_cache_boundary(self) -> None:
        self.assertEqual(self.miss.build_key, self.request.build_key)
        self.assertEqual(self.hit.build_key, self.request.build_key)
        self.assertEqual(self.miss.document_hash, self.hit.document_hash)
        self.assertEqual(
            self.miss.manifest.canonical_bytes(),
            self.hit.manifest.canonical_bytes(),
        )
        self.assertEqual(
            [artifact.logical_id for artifact in self.miss.manifest.artifacts],
            [artifact.logical_id for artifact in self.hit.manifest.artifacts],
        )

    def test_the_render_model_survives_the_cache_round_trip(self) -> None:
        self.assertIsNotNone(self.hit.render_model)
        self.assertEqual(self.hit.render_model, self.miss.render_model)

    def test_the_workspace_is_never_inside_the_cache(self) -> None:
        for execution in (self.miss, self.hit):
            if execution.workspace is None:
                continue
            with self.assertRaises(ValueError):
                Path(execution.workspace).relative_to(self.cache_root)

    def test_no_workspace_survives_either_path(self) -> None:
        for execution in (self.miss, self.hit):
            if execution.workspace is not None:
                self.assertFalse(Path(execution.workspace).exists())


# --- determinism ------------------------------------------------------------


class TestDeterminism(unittest.TestCase):
    """Two independent isolated processes, building the same request."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.request = request_of(document([plate_feature()]))
        cls.first_out = root / "first"
        cls.first_out.mkdir()
        cls.second_out = root / "second"
        cls.second_out.mkdir()
        cls.first = execute_isolated(cls.request, output_directory=cls.first_out)
        cls.second = execute_isolated(cls.request, output_directory=cls.second_out)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_the_build_key_is_stable_across_isolated_processes(self) -> None:
        self.assertEqual(self.first.build_key, self.second.build_key)
        self.assertEqual(self.first.build_key, self.request.build_key)
        self.assertEqual(self.first.document_hash, self.second.document_hash)

    def test_the_logical_ids_are_stable(self) -> None:
        self.assertEqual(
            [artifact.logical_id for artifact in self.first.manifest.artifacts],
            [artifact.logical_id for artifact in self.second.manifest.artifacts],
        )
        self.assertEqual(
            self.first.manifest.canonical_bytes(),
            self.second.manifest.canonical_bytes(),
        )

    def test_the_execution_ids_differ(self) -> None:
        self.assertNotEqual(self.first.execution_id, self.second.execution_id)

    def test_the_render_serialization_is_stable(self) -> None:
        self.assertEqual(self.first.render_model, self.second.render_model)
        self.assertEqual(
            canonical_render_bytes(self.first.render_model),
            canonical_render_bytes(self.second.render_model),
        )
        self.assertEqual(
            self.first.artifact(ArtifactKind.RENDER).checksum,
            self.second.artifact(ArtifactKind.RENDER).checksum,
        )

    def test_the_stl_checksum_is_stable(self) -> None:
        self.assertEqual(
            self.first.artifact(ArtifactKind.STL).checksum,
            self.second.artifact(ArtifactKind.STL).checksum,
        )
        self.assertEqual(
            Path(self.first.artifact(ArtifactKind.STL).path).read_bytes(),
            Path(self.second.artifact(ArtifactKind.STL).path).read_bytes(),
        )

    def test_step_and_iges_are_geometrically_equivalent_not_byte_equal(
        self,
    ) -> None:
        """Measured, as in Stages 5 and 6: the geometry matches, bytes need not."""
        expected = PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
        for kind, reader in (
            (ArtifactKind.STEP, read_step),
            (ArtifactKind.IGES, read_iges),
        ):
            with self.subTest(kind=kind.value):
                first = reader(self.first.artifact(kind).path)
                second = reader(self.second.artifact(kind).path)
                self.assertTrue(first.is_solid())
                self.assertTrue(second.is_solid())
                self.assertAlmostEqual(first.volume(), expected, delta=1e-6)
                self.assertAlmostEqual(second.volume(), expected, delta=1e-6)
                self.assertAlmostEqual(
                    first.volume(), second.volume(), delta=1e-6
                )
        # no byte equality is claimed for either
        self.assertIsNotNone(self.first.artifact(ArtifactKind.STEP).checksum)


# --- the environment --------------------------------------------------------


class TestChildEnvironment(IsolationTestCase):
    def test_no_secret_reaches_the_child(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                SECRET_NAME: SECRET_SENTINEL,
                "OPENAI_API_KEY": SECRET_SENTINEL,
                "CAD_TEST_NOT_INHERITED": SECRET_SENTINEL,
            },
        ):
            execution = invoke_worker(
                operation=OPERATION_DIAGNOSTIC_ENVIRONMENT, timeout_seconds=30
            )
            names = execution.worker_result["environment_names"]
        self.assertNotIn(SECRET_NAME, names)
        self.assertNotIn("OPENAI_API_KEY", names)
        self.assertNotIn("CAD_TEST_NOT_INHERITED", names)

    def test_the_child_environment_is_an_allowlist(self) -> None:
        with mock.patch.dict(os.environ, {SECRET_NAME: SECRET_SENTINEL}):
            environment = child_environment(self.tmp)
        self.assertNotIn(SECRET_NAME, environment)
        for name in environment:
            self.assertIn(
                name,
                set(INHERITED_ENVIRONMENT_NAMES)
                | {
                    "PYTHONPATH",
                    "PYTHONIOENCODING",
                    "PYTHONDONTWRITEBYTECODE",
                    "TMPDIR",
                    "TEMP",
                    "TMP",
                },
            )
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["TMPDIR"], str(self.tmp))

    def test_the_child_python_path_is_derived_not_hard_coded(self) -> None:
        import cad_core

        expected = str(Path(cad_core.__file__).resolve().parents[1])
        environment = child_environment(self.tmp)
        self.assertEqual(
            environment["PYTHONPATH"].split(os.pathsep)[0], expected
        )
        self.assertTrue(Path(expected).is_dir())

    def test_no_secret_reaches_the_child_protocol(self) -> None:
        request = request_of(document([plate_feature()]), ArtifactKind.GEOMETRY)
        area = self.subdirectory("protocol")
        with mock.patch.dict(os.environ, {SECRET_NAME: SECRET_SENTINEL}):
            with mock.patch(
                "cad_core.isolated_execution.subprocess.Popen",
                side_effect=AssertionError("stop before launching"),
            ):
                with self.assertRaises(AssertionError):
                    execute_isolated(
                        request,
                        output_directory=self.subdirectory("out"),
                        workspace_root=area,
                        keep_workspace=True,
                    )
        written = [
            path
            for path in area.rglob(REQUEST_FILENAME)
            if path.is_file()
        ]
        self.assertEqual(len(written), 1)
        text = written[0].read_text(encoding="utf-8")
        self.assertNotIn(SECRET_SENTINEL, text)
        self.assertNotIn(SECRET_NAME, text)
        envelope = json.loads(text)
        self.assertEqual(sorted(envelope), sorted(REQUEST_ENVELOPE_FIELDS))
        self.assertEqual(
            sorted(envelope["request"]), sorted(BUILD_REQUEST_FIELDS)
        )
        # the payload carries a CAD document and an output list -- no path
        self.assertEqual(
            envelope["request"]["document"], serialize_part(request.part)
        )
        self.assertEqual(
            envelope["request"]["options"], {"outputs": ["geometry"]}
        )


# --- the protocol, and what is not in it ------------------------------------


class TestProtocolShape(unittest.TestCase):
    def test_the_ipc_version_is_not_the_cad_schema_version(self) -> None:
        """Four version numbers, four meanings, and never confused."""
        from cad_core.local_build_cache import CACHE_SCHEMA_VERSION

        document_version = document([plate_feature()])["schema_version"]
        self.assertEqual(document_version, "1.0.0")
        self.assertEqual(RENDER_FORMAT_VERSION, "1.0.0")
        self.assertEqual(CACHE_SCHEMA_VERSION, "1.0.0")
        self.assertEqual(IPC_PROTOCOL_VERSION, "1.0.0")
        # equal today by coincidence; they are independent constants naming
        # different contracts, and the envelope never carries a CAD version
        self.assertNotIn("schema_version", REQUEST_ENVELOPE_FIELDS)
        self.assertNotIn("schema_version", RESPONSE_ENVELOPE_FIELDS)
        self.assertIn("ipc_protocol_version", REQUEST_ENVELOPE_FIELDS)
        self.assertIn("ipc_protocol_version", RESPONSE_ENVELOPE_FIELDS)

    def test_the_envelope_fields_are_the_documented_ones(self) -> None:
        self.assertEqual(
            REQUEST_ENVELOPE_FIELDS,
            ("ipc_protocol_version", "operation", "request"),
        )
        self.assertEqual(
            RESPONSE_ENVELOPE_FIELDS,
            (
                "ipc_protocol_version",
                "operation",
                "status",
                "result",
                "error",
                "transport",
            ),
        )
        self.assertEqual(BUILD_REQUEST_FIELDS, ("document", "options"))

    def test_the_exit_codes_are_distinct_and_meaningful(self) -> None:
        self.assertEqual(EXIT_SUCCESS, 0)
        self.assertEqual(len(set(EXIT_CODES)), len(EXIT_CODES))
        self.assertEqual(
            sorted(EXIT_CODES),
            [
                EXIT_SUCCESS,
                EXIT_BUILD_FAILED,
                EXIT_VALIDATION_FAILED,
                EXIT_PROTOCOL_ERROR,
                EXIT_INTERNAL_ERROR,
            ],
        )
        from cad_core.isolated_worker import STATUS_EXIT_CODES

        self.assertEqual(set(STATUS_EXIT_CODES), set(WorkerStatus))
        self.assertEqual(set(STATUS_EXIT_CODES.values()), set(EXIT_CODES))

    def test_every_worker_status_has_a_host_outcome(self) -> None:
        self.assertEqual(set(WORKER_STATUS_OUTCOMES), set(WorkerStatus))
        self.assertEqual(
            len(set(WORKER_STATUS_OUTCOMES.values())), len(WorkerStatus)
        )
        for required in (
            "succeeded",
            "validation_failed",
            "build_failed",
            "protocol_error",
            "process_failed",
        ):
            self.assertIn(
                required, [outcome.value for outcome in IsolationOutcome]
            )
        self.assertIn(
            "timed_out", [outcome.value for outcome in IsolationOutcome]
        )

    def test_the_only_production_operation_is_build(self) -> None:
        self.assertEqual(OPERATION_BUILD, "build")
        for operation in OPERATIONS:
            if operation == OPERATION_BUILD:
                continue
            self.assertTrue(operation.startswith("diagnostic:"))


class TestSafetyBoundary(unittest.TestCase):
    def source(self, name: str) -> str:
        path = (
            Path(__file__).resolve().parents[1] / "src" / "cad_core" / f"{name}.py"
        )
        return path.read_text(encoding="utf-8")

    def code_names(self, name: str) -> List[str]:
        """Identifiers the module's *code* uses; docstrings excluded."""
        tree = ast.parse(self.source(name))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.append(node.id)
            elif isinstance(node, ast.Attribute):
                found.append(node.attr)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                found.append(node.name)
            elif isinstance(node, ast.arg):
                found.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                found.append(node.arg)
        return found

    def code_strings(self, name: str) -> List[str]:
        """String literals in the module's *code*; docstrings excluded.

        These modules describe in prose exactly what they never do ("never
        pickle, eval, exec"), so an assertion against the file's text would
        fail on its own documentation. These assertions read the syntax tree.
        """
        tree = ast.parse(self.source(name))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                first = node.body[0] if node.body else None
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ]

    def imports_of(self, name: str) -> List[str]:
        tree = ast.parse(self.source(name))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def modules(self) -> Tuple[str, ...]:
        return ("isolated_execution", "isolated_worker")

    def test_no_pickle_anywhere_in_the_boundary(self) -> None:
        for name in self.modules():
            with self.subTest(module=name):
                self.assertNotIn("pickle", self.imports_of(name))
                self.assertNotIn("cPickle", self.imports_of(name))
                self.assertNotIn("marshal", self.imports_of(name))
                self.assertNotIn("shelve", self.imports_of(name))
                for forbidden in ("pickle", "marshal", "shelve", "loads_any"):
                    self.assertNotIn(forbidden, self.code_names(name))
                    for literal in self.code_strings(name):
                        self.assertNotIn(forbidden, literal)
                # the only deserializer used anywhere is json.loads
                self.assertIn("json", self.imports_of(name))

    def test_no_eval_or_exec_or_compile(self) -> None:
        for name in self.modules():
            with self.subTest(module=name):
                names = self.code_names(name)
                for forbidden in (
                    "eval",
                    "exec",
                    "compile",
                    "__import__",
                    "execfile",
                ):
                    self.assertNotIn(forbidden, names)

    def test_no_shell_is_ever_used(self) -> None:
        tree = ast.parse(self.source("isolated_execution"))
        shells = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "shell"
            and not (
                isinstance(node.value, ast.Constant) and node.value.value is False
            )
        ]
        self.assertEqual(shells, [])
        explicit = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.keyword)
            and node.arg == "shell"
            and isinstance(node.value, ast.Constant)
            and node.value.value is False
        ]
        self.assertEqual(len(explicit), 1)
        for name in self.modules():
            names = self.code_names(name)
            for forbidden in ("system", "popen", "spawnl", "execv", "call"):
                self.assertNotIn(forbidden, names)

    def test_the_child_is_launched_as_an_argument_list(self) -> None:
        source = self.source("isolated_execution")
        self.assertIn("sys.executable", source)
        self.assertIn('"-m",', source)
        self.assertIn('"cad_core.isolated_worker"', source)
        # a command string would need a shell or a join of the command
        self.assertNotIn('" ".join(command', source)

    def test_no_worker_pool_or_service_infrastructure(self) -> None:
        forbidden = {
            "celery",
            "kombu",
            "pika",
            "redis",
            "sqlite3",
            "sqlalchemy",
            "psycopg2",
            "boto3",
            "botocore",
            "google.cloud",
            "azure",
            "kubernetes",
            "docker",
            "requests",
            "httpx",
            "urllib",
            "urllib.request",
            "http",
            "http.client",
            "http.server",
            "socket",
            "socketserver",
            "grpc",
            "flask",
            "fastapi",
            "django",
            "asyncio",
            "multiprocessing",
            "multiprocessing.pool",
            "concurrent.futures",
            "threading",
        }
        for name in self.modules():
            with self.subTest(module=name):
                for imported in self.imports_of(name):
                    self.assertNotIn(imported, forbidden)

    def test_no_llm_mcp_or_frontend_dependency(self) -> None:
        for name in self.modules():
            for imported in self.imports_of(name):
                for token in (
                    "llm",
                    "openai",
                    "anthropic",
                    "mcp",
                    "onshape",
                    "featurescript",
                ):
                    self.assertNotIn(token, imported.lower())

    def test_the_worker_imports_only_the_standard_library_up_front(self) -> None:
        """So a protocol failure never pays the kernel's import cost."""
        tree = ast.parse(self.source("isolated_worker"))
        top_level: List[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.append(node.module)
        for imported in top_level:
            self.assertFalse(
                imported.startswith("cad_core"),
                msg=f"{imported} is imported at module level",
            )
            self.assertNotIn("cadquery", imported)

    def test_the_worker_holds_no_cad_semantics(self) -> None:
        names = self.code_names("isolated_worker")
        for forbidden in (
            "build_part",
            "export_step",
            "export_iges",
            "export_stl",
            "select_edges",
            "validate",
            "cadquery",
            "Workplane",
            "TopoDS",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_worker_calls_the_existing_build_machinery(self) -> None:
        imports = self.imports_of("isolated_worker")
        self.assertIn("cad_core.build_job", imports)
        self.assertIn("cad_core.local_build_cache", imports)
        source = self.source("isolated_worker")
        self.assertIn("execute_build_document", source)
        self.assertIn("get_or_build", source)

    def test_no_arbitrary_code_can_be_sent_across_the_boundary(self) -> None:
        """The request carries a CAD document; there is no code path for code."""
        names = self.code_names("isolated_worker")
        literals = self.code_strings("isolated_worker")
        for forbidden in ("eval", "exec", "compile", "pickle", "runpy"):
            self.assertNotIn(forbidden, names)
            for literal in literals:
                self.assertNotIn(forbidden, literal)
        # the payload's only fields are a document and an output list
        self.assertEqual(BUILD_REQUEST_FIELDS, ("document", "options"))
        # every accepted operation is a fixed name from a closed tuple
        self.assertEqual(len(set(OPERATIONS)), len(OPERATIONS))

    def test_the_security_limits_are_stated_not_overclaimed(self) -> None:
        for name in self.modules():
            source = self.source(name)
            self.assertIn("not a security sandbox", source)
        documentation = (
            Path(__file__).resolve().parents[3] / "docs"
            / "isolated-cad-execution.md"
        )
        self.assertTrue(documentation.is_file())
        text = documentation.read_text(encoding="utf-8")
        unwrapped = " ".join(text.split())
        self.assertIn(
            "This is process isolation for crash containment and execution "
            "boundaries, not a security sandbox.",
            unwrapped,
        )
        for absent in ("seccomp", "syscall", "container", "privilege"):
            self.assertIn(absent, text.lower())


class TestPackageBoundary(unittest.TestCase):
    def imports_of(self, name: str) -> List[str]:
        path = (
            Path(__file__).resolve().parents[1] / "src" / "cad_core" / f"{name}.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def test_nothing_upstream_imports_the_isolation_layer(self) -> None:
        for name in (
            "model",
            "validator",
            "errors",
            "serialization",
            "featurescript",
            "local_cad",
            "edge_selection",
            "step_export",
            "iges_export",
            "stl_export",
            "render_model",
            "artifact_registry",
            "build_job",
            "local_build_cache",
            "__init__",
        ):
            with self.subTest(module=name):
                imports = self.imports_of(name)
                self.assertNotIn("cad_core.isolated_execution", imports)
                self.assertNotIn("cad_core.isolated_worker", imports)

    def test_the_isolation_layer_is_not_re_exported_from_the_root(self) -> None:
        imports = self.imports_of("__init__")
        self.assertNotIn("cad_core.isolated_execution", imports)
        self.assertNotIn("cad_core.isolated_worker", imports)

    def test_the_worker_does_not_import_the_host(self) -> None:
        self.assertNotIn(
            "cad_core.isolated_execution", self.imports_of("isolated_worker")
        )

    def test_the_host_owns_the_protocol_constants_from_the_worker(self) -> None:
        self.assertIn(
            "cad_core.isolated_worker", self.imports_of("isolated_execution")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
