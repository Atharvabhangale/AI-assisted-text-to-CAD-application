"""Unit tests for the local build/job layer.

The layer is infrastructure: it adds no CAD semantics, so these tests are
about identity, lifecycle, artifact selection and failure classification. What
the geometry and exporters produce is already covered by their own suites;
here they are only checked to be the same thing the build layer hands back.

Every file output goes to a per-test temporary directory. No test leaves an
artifact in the repository.
"""

from __future__ import annotations

import ast
import json
import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest import mock

from cad_core import validate
from cad_core.build_job import (
    ALLOWED_TRANSITIONS,
    BUILD_KEY_ALGORITHM,
    FILE_OUTPUTS,
    IN_MEMORY_OUTPUTS,
    TERMINAL_STATUSES,
    BuildArtifact,
    BuildError,
    BuildFailure,
    BuildJob,
    BuildOptions,
    BuildOutput,
    BuildRequest,
    BuildRequestError,
    BuildResult,
    BuildStateError,
    BuildStatus,
    CadBuildError,
    build_key_for,
    execute_build,
    execute_build_document,
    request_for_document,
    run_job,
)
from cad_core.local_cad import LocalCadResult, build_part
from cad_core.model import Box, Part, Position, Size
from cad_core.render_model import RenderModel, build_render_model
from cad_core.serialization import (
    DocumentValidationError,
    part_hash,
    part_to_json,
)
from cad_core.step_export import export_step, read_step
from cad_core.stl_export import binary_stl_facts

VOLUME_TOLERANCE_MM3 = 1e-6
TOLERANCE_MM = 1e-6

PLATE_SIZE = (100.0, 60.0, 10.0)


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


def drilled_document() -> Dict[str, Any]:
    return document(
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
    )


class BuildJobTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def plate(self) -> Part:
        return self.part_from(document([plate_feature()]))

    def options(self, *outputs: BuildOutput) -> BuildOptions:
        return BuildOptions.for_outputs(*outputs)

    def request(self, *outputs: BuildOutput, part: Any = None) -> BuildRequest:
        return BuildRequest(
            part=part if part is not None else self.plate(),
            options=self.options(*outputs),
        )


# --- 1-4: lifecycle ---------------------------------------------------------


class TestJobLifecycle(BuildJobTestCase):
    def job(self) -> BuildJob:
        return BuildJob(self.request(BuildOutput.GEOMETRY))

    def succeeding_result(self, job: BuildJob) -> BuildResult:
        return BuildResult(
            build_key=job.build_key,
            document_hash=job.document_hash,
            execution_id=job.execution_id,
            status=BuildStatus.SUCCEEDED,
        )

    def test_a_new_job_is_queued(self) -> None:
        job = self.job()
        self.assertIs(job.status, BuildStatus.QUEUED)
        self.assertFalse(job.is_terminal)
        self.assertIsNone(job.result)

    def test_a_new_job_has_an_execution_id(self) -> None:
        first, second = self.job(), self.job()
        self.assertEqual(len(first.execution_id), 32)
        self.assertNotEqual(first.execution_id, second.execution_id)

    def test_queued_to_running_to_succeeded(self) -> None:
        job = self.job()
        job.start()
        self.assertIs(job.status, BuildStatus.RUNNING)
        self.assertFalse(job.is_terminal)
        job.succeed(self.succeeding_result(job))
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertTrue(job.is_terminal)
        self.assertIsNotNone(job.result)

    def test_queued_to_running_to_failed(self) -> None:
        job = self.job()
        job.start()
        job.fail(
            BuildError(
                failure=BuildFailure.INTERNAL, message="boom", stage="execution"
            )
        )
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertTrue(job.is_terminal)
        assert job.result is not None
        self.assertEqual(job.result.artifacts, ())

    def test_the_transition_table_is_exactly_the_documented_one(self) -> None:
        self.assertEqual(
            ALLOWED_TRANSITIONS,
            {
                BuildStatus.QUEUED: (BuildStatus.RUNNING,),
                BuildStatus.RUNNING: (BuildStatus.SUCCEEDED, BuildStatus.FAILED),
                BuildStatus.SUCCEEDED: (),
                BuildStatus.FAILED: (),
            },
        )
        self.assertEqual(
            TERMINAL_STATUSES, (BuildStatus.SUCCEEDED, BuildStatus.FAILED)
        )

    def test_there_is_no_speculative_cancellation_state(self) -> None:
        self.assertEqual(
            {status.value for status in BuildStatus},
            {"queued", "running", "succeeded", "failed"},
        )

    def test_a_queued_job_cannot_be_finalized(self) -> None:
        job = self.job()
        with self.assertRaises(BuildStateError) as caught:
            job.succeed(self.succeeding_result(job))
        self.assertIs(caught.exception.current, BuildStatus.QUEUED)
        self.assertIs(caught.exception.requested, BuildStatus.SUCCEEDED)
        self.assertIs(job.status, BuildStatus.QUEUED)

        with self.assertRaises(BuildStateError):
            job.fail(
                BuildError(failure=BuildFailure.INTERNAL, message="x", stage="s")
            )
        self.assertIs(job.status, BuildStatus.QUEUED)

    def test_a_running_job_cannot_restart(self) -> None:
        job = self.job()
        job.start()
        with self.assertRaises(BuildStateError):
            job.start()
        self.assertIs(job.status, BuildStatus.RUNNING)

    def test_a_succeeded_job_cannot_transition_again(self) -> None:
        job = self.job()
        job.start()
        result = job.succeed(self.succeeding_result(job))
        for attempt in (
            lambda: job.start(),
            lambda: job.succeed(self.succeeding_result(job)),
            lambda: job.fail(
                BuildError(failure=BuildFailure.INTERNAL, message="x", stage="s")
            ),
        ):
            with self.subTest(attempt=attempt):
                with self.assertRaises(BuildStateError):
                    attempt()
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertIs(job.result, result)

    def test_a_failed_job_cannot_transition_again(self) -> None:
        job = self.job()
        job.start()
        job.fail(BuildError(failure=BuildFailure.INTERNAL, message="x", stage="s"))
        with self.assertRaises(BuildStateError):
            job.succeed(self.succeeding_result(job))
        with self.assertRaises(BuildStateError):
            job.fail(
                BuildError(failure=BuildFailure.INTERNAL, message="y", stage="s")
            )
        self.assertIs(job.status, BuildStatus.FAILED)
        assert job.result is not None
        self.assertEqual(job.result.error.message, "x")

    def test_double_finalization_is_impossible(self) -> None:
        """The guard against an accidental second finalize."""
        job = self.job()
        job.start()
        job.succeed(self.succeeding_result(job))
        with self.assertRaises(BuildStateError):
            job.succeed(self.succeeding_result(job))

    def test_a_state_error_reports_what_was_permitted(self) -> None:
        job = self.job()
        job.start()
        job.succeed(self.succeeding_result(job))
        with self.assertRaises(BuildStateError) as caught:
            job.start()
        message = str(caught.exception)
        self.assertIn("succeeded", message)
        self.assertIn("running", message)
        self.assertIn("nothing", message)
        self.assertIs(caught.exception.failure, BuildFailure.JOB_STATE)

    def test_can_transition_to_agrees_with_the_table(self) -> None:
        job = self.job()
        self.assertTrue(job.can_transition_to(BuildStatus.RUNNING))
        self.assertFalse(job.can_transition_to(BuildStatus.SUCCEEDED))
        job.start()
        self.assertTrue(job.can_transition_to(BuildStatus.SUCCEEDED))
        self.assertTrue(job.can_transition_to(BuildStatus.FAILED))
        self.assertFalse(job.can_transition_to(BuildStatus.RUNNING))

    def test_a_succeeding_result_must_say_succeeded(self) -> None:
        job = self.job()
        job.start()
        with self.assertRaises(BuildRequestError):
            job.succeed(
                BuildResult(
                    build_key=job.build_key,
                    document_hash=job.document_hash,
                    execution_id=job.execution_id,
                    status=BuildStatus.FAILED,
                )
            )

    def test_running_a_non_queued_job_is_refused(self) -> None:
        job = self.job()
        job.start()
        with self.assertRaises(BuildStateError):
            run_job(job)


# --- 5-8: identity ----------------------------------------------------------


class TestBuildIdentity(BuildJobTestCase):
    def corpus(self) -> Tuple[Tuple[str, Dict[str, Any]], ...]:
        return (
            ("plate", document([plate_feature()])),
            ("drilled", drilled_document()),
            ("renamed", document([plate_feature()], name="other")),
            (
                "resized",
                document([plate_feature(size={"x": 100, "y": 60, "z": 11})]),
            ),
            (
                "cylinder",
                document(
                    [{"id": "pin", "type": "cylinder", "diameter": 20, "height": 50}],
                    name="pin",
                ),
            ),
        )

    def test_a_request_carries_the_canonical_document_hash(self) -> None:
        part = self.plate()
        request = self.request(BuildOutput.GEOMETRY, part=part)
        self.assertEqual(request.document_hash, part_hash(part))
        self.assertEqual(len(request.document_hash), 64)

    def test_the_document_hash_cannot_be_supplied_by_a_caller(self) -> None:
        """It is a computed property, so it can never drift from the part."""
        request = self.request(BuildOutput.GEOMETRY)
        self.assertIsInstance(
            type(request).document_hash, property, msg="must not be a field"
        )
        with self.assertRaises(TypeError):
            BuildRequest(  # type: ignore[call-arg]
                part=self.plate(),
                options=self.options(BuildOutput.GEOMETRY),
                document_hash="0" * 64,
            )

    def test_a_job_and_its_result_report_the_same_document_hash(self) -> None:
        job = execute_build(self.request(BuildOutput.GEOMETRY))
        assert job.result is not None
        self.assertEqual(job.document_hash, job.result.document_hash)
        self.assertEqual(job.document_hash, part_hash(job.request.part))

    def test_the_same_part_and_options_give_the_same_build_key(self) -> None:
        for name, doc in self.corpus():
            with self.subTest(name=name):
                first = self.part_from(doc)
                second = self.part_from(json.loads(json.dumps(doc)))
                options = self.options(BuildOutput.GEOMETRY, BuildOutput.STL)
                self.assertEqual(
                    build_key_for(first, options), build_key_for(second, options)
                )

    def test_the_build_key_is_stable_across_repeats(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STEP)
        self.assertEqual(len({request.build_key for _ in range(10)}), 1)

    def test_different_parts_give_different_build_keys(self) -> None:
        options = self.options(BuildOutput.GEOMETRY)
        keys = {
            name: build_key_for(self.part_from(doc), options)
            for name, doc in self.corpus()
        }
        self.assertEqual(len(set(keys.values())), len(keys))

    def test_different_options_give_different_build_keys(self) -> None:
        part = self.plate()
        selections = (
            (BuildOutput.GEOMETRY,),
            (BuildOutput.STEP,),
            (BuildOutput.GEOMETRY, BuildOutput.STEP),
            (BuildOutput.GEOMETRY, BuildOutput.STEP, BuildOutput.STL),
            tuple(BuildOutput),
        )
        keys = {
            outputs: build_key_for(part, BuildOptions(outputs=outputs))
            for outputs in selections
        }
        self.assertEqual(len(set(keys.values())), len(keys))

    def test_output_request_order_does_not_change_the_build_key(self) -> None:
        """Requesting the same set differently is the same request."""
        part = self.plate()
        forward = BuildOptions.for_outputs(BuildOutput.STEP, BuildOutput.GEOMETRY)
        backward = BuildOptions.for_outputs(BuildOutput.GEOMETRY, BuildOutput.STEP)
        self.assertEqual(build_key_for(part, forward), build_key_for(part, backward))

    def test_a_repeated_output_does_not_change_the_build_key(self) -> None:
        part = self.plate()
        once = BuildOptions.for_outputs(BuildOutput.STEP)
        twice = BuildOptions.for_outputs(BuildOutput.STEP, BuildOutput.STEP)
        self.assertEqual(build_key_for(part, once), build_key_for(part, twice))

    def test_the_build_key_is_not_the_document_hash(self) -> None:
        request = self.request(BuildOutput.GEOMETRY)
        self.assertNotEqual(request.build_key, request.document_hash)
        self.assertEqual(len(request.build_key), 64)

    def test_the_build_key_is_the_documented_hash_of_the_documented_bytes(self) -> None:
        import hashlib

        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        payload = json.dumps(
            {
                "document_hash": request.document_hash,
                "options": {"outputs": ["geometry", "stl"]},
            },
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.assertEqual(BUILD_KEY_ALGORITHM, "sha256")
        self.assertEqual(request.build_key, hashlib.sha256(payload).hexdigest())

    def test_the_execution_id_is_not_part_of_the_build_key(self) -> None:
        request = self.request(BuildOutput.GEOMETRY)
        first = execute_build(request)
        second = execute_build(request)
        self.assertNotEqual(first.execution_id, second.execution_id)
        self.assertEqual(first.build_key, second.build_key)

    def test_no_timestamp_or_path_is_used_as_identity(self) -> None:
        """Two builds a moment apart, into different directories, agree."""
        request = self.request(BuildOutput.STEP)
        other = self.tmp / "second"
        other.mkdir()
        first = execute_build(request, output_directory=self.tmp)
        second = execute_build(request, output_directory=other)
        self.assertEqual(first.build_key, second.build_key)
        assert first.result is not None and second.result is not None
        self.assertNotEqual(
            first.result.artifact(BuildOutput.STEP).path,
            second.result.artifact(BuildOutput.STEP).path,
        )
        self.assertEqual(
            first.result.artifact(BuildOutput.STEP).logical_id,
            second.result.artifact(BuildOutput.STEP).logical_id,
        )


# --- BuildOptions -----------------------------------------------------------


class TestBuildOptions(BuildJobTestCase):
    def test_the_output_vocabulary_is_the_existing_local_outputs(self) -> None:
        self.assertEqual(
            [output.value for output in BuildOutput],
            ["geometry", "step", "iges", "stl", "render"],
        )
        self.assertEqual(
            FILE_OUTPUTS, (BuildOutput.STEP, BuildOutput.IGES, BuildOutput.STL)
        )
        self.assertEqual(
            IN_MEMORY_OUTPUTS, (BuildOutput.GEOMETRY, BuildOutput.RENDER)
        )

    def test_there_is_no_implicit_default_output_set(self) -> None:
        with self.assertRaises(TypeError):
            BuildOptions()  # type: ignore[call-arg]
        with self.assertRaises(BuildRequestError):
            BuildOptions(outputs=())

    def test_outputs_must_be_build_output_members(self) -> None:
        for bad in ("step", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(BuildRequestError):
                    BuildOptions(outputs=(bad,))  # type: ignore[arg-type]

    def test_the_canonical_form_is_sorted_and_deduplicated(self) -> None:
        options = BuildOptions.for_outputs(
            BuildOutput.STL, BuildOutput.GEOMETRY, BuildOutput.STL
        )
        self.assertEqual(options.canonical(), {"outputs": ["geometry", "stl"]})
        self.assertEqual(options.requested, (BuildOutput.GEOMETRY, BuildOutput.STL))

    def test_wants_and_writes_files(self) -> None:
        options = self.options(BuildOutput.GEOMETRY, BuildOutput.RENDER)
        self.assertTrue(options.wants(BuildOutput.GEOMETRY))
        self.assertFalse(options.wants(BuildOutput.STEP))
        self.assertFalse(options.writes_files)
        self.assertTrue(self.options(BuildOutput.IGES).writes_files)

    def test_options_carry_no_cad_semantics(self) -> None:
        """Only an output selection: the document holds the design."""
        import dataclasses

        fields = {field.name for field in dataclasses.fields(BuildOptions)}
        self.assertEqual(fields, {"outputs"})

    def test_a_file_output_needs_an_explicit_directory(self) -> None:
        for output in FILE_OUTPUTS:
            with self.subTest(output=output):
                with self.assertRaises(BuildRequestError) as caught:
                    execute_build(self.request(output))
                self.assertIn("output_directory", str(caught.exception))

    def test_an_in_memory_only_build_needs_no_directory(self) -> None:
        job = execute_build(self.request(BuildOutput.GEOMETRY, BuildOutput.RENDER))
        self.assertIs(job.status, BuildStatus.SUCCEEDED)

    def test_a_missing_directory_is_refused_before_the_job_starts(self) -> None:
        request = self.request(BuildOutput.STEP)
        job = BuildJob(request)
        with self.assertRaises(BuildRequestError):
            run_job(job, output_directory=self.tmp / "absent")
        self.assertIs(job.status, BuildStatus.QUEUED)


# --- 9-15, 21, 22: output selection ---------------------------------------


class TestOutputSelection(BuildJobTestCase):
    def build(self, *outputs: BuildOutput, doc: Any = None) -> BuildJob:
        part = self.part_from(doc) if doc is not None else self.plate()
        return execute_build(
            BuildRequest(part=part, options=self.options(*outputs)),
            output_directory=self.tmp,
        )

    def test_geometry_only(self) -> None:
        job = self.build(BuildOutput.GEOMETRY)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertEqual(job.result.produced_outputs(), (BuildOutput.GEOMETRY,))
        artifact = job.result.artifact(BuildOutput.GEOMETRY)
        assert artifact is not None
        self.assertTrue(artifact.in_memory)
        self.assertIsNone(artifact.path)
        self.assertEqual(artifact.details["solid_count"], 1)
        self.assertAlmostEqual(
            artifact.details["volume_mm3"], 60000.0, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertIsInstance(job.result.geometry, LocalCadResult)
        self.assertEqual(sorted(self.tmp.iterdir()), [])

    def test_step_only(self) -> None:
        job = self.build(BuildOutput.STEP)
        assert job.result is not None
        self.assertEqual(job.result.produced_outputs(), (BuildOutput.STEP,))
        artifact = job.result.artifact(BuildOutput.STEP)
        assert artifact is not None
        self.assertTrue(Path(artifact.path).is_file())
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(Path(artifact.path).suffix, ".step")
        self.assertIsNone(job.result.geometry)

    def test_iges_only(self) -> None:
        job = self.build(BuildOutput.IGES)
        assert job.result is not None
        self.assertEqual(job.result.produced_outputs(), (BuildOutput.IGES,))
        artifact = job.result.artifact(BuildOutput.IGES)
        assert artifact is not None
        self.assertEqual(artifact.format, "iges-brep")
        self.assertEqual(Path(artifact.path).suffix, ".igs")
        self.assertGreater(artifact.size_bytes, 0)

    def test_stl_only(self) -> None:
        job = self.build(BuildOutput.STL)
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.STL)
        assert artifact is not None
        self.assertEqual(artifact.format, "stl-binary")
        self.assertEqual(artifact.details["triangle_count"], 12)
        self.assertTrue(artifact.details["is_structurally_consistent"])
        self.assertEqual(
            artifact.size_bytes, binary_stl_facts(Path(artifact.path)).file_size
        )

    def test_render_only(self) -> None:
        job = self.build(BuildOutput.RENDER)
        assert job.result is not None
        self.assertEqual(job.result.produced_outputs(), (BuildOutput.RENDER,))
        artifact = job.result.artifact(BuildOutput.RENDER)
        assert artifact is not None
        self.assertTrue(artifact.in_memory)
        self.assertEqual(artifact.details["triangle_count"], 12)
        self.assertEqual(artifact.details["vertex_count"], 24)
        self.assertIsInstance(job.result.render_model, RenderModel)
        self.assertEqual(sorted(self.tmp.iterdir()), [])

    def test_the_render_artifact_holds_metadata_not_duplicated_json(self) -> None:
        job = self.build(BuildOutput.RENDER)
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.RENDER)
        assert artifact is not None
        serialized = json.dumps(artifact.to_dict())
        self.assertNotIn("vertices", serialized)
        self.assertNotIn("normals", serialized)
        self.assertIn("vertex_count", serialized)
        # ... the model itself is reachable as a reference.
        assert job.result.render_model is not None
        self.assertEqual(job.result.render_model.vertex_count(), 24)

    def test_all_outputs_together(self) -> None:
        job = self.build(*BuildOutput)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertEqual(
            job.result.produced_outputs(),
            (
                BuildOutput.GEOMETRY,
                BuildOutput.STEP,
                BuildOutput.IGES,
                BuildOutput.STL,
                BuildOutput.RENDER,
            ),
        )
        self.assertEqual(len(list(self.tmp.iterdir())), 3)

    def test_a_successful_build_holds_exactly_the_requested_artifacts(self) -> None:
        for outputs in (
            (BuildOutput.GEOMETRY,),
            (BuildOutput.STL,),
            (BuildOutput.GEOMETRY, BuildOutput.RENDER),
            (BuildOutput.STEP, BuildOutput.IGES),
            tuple(BuildOutput),
        ):
            with self.subTest(outputs=[o.value for o in outputs]):
                job = self.build(*outputs)
                assert job.result is not None
                self.assertEqual(
                    set(job.result.produced_outputs()), set(outputs)
                )
                self.assertEqual(len(job.result.artifacts), len(set(outputs)))

    def test_an_unrequested_output_is_never_produced(self) -> None:
        job = self.build(BuildOutput.GEOMETRY)
        assert job.result is not None
        for output in BuildOutput:
            if output is BuildOutput.GEOMETRY:
                continue
            with self.subTest(output=output.value):
                self.assertIsNone(job.result.artifact(output))
        self.assertIsNone(job.result.render_model)

    def test_every_artifact_identifies_the_source_document_hash(self) -> None:
        job = self.build(*BuildOutput)
        assert job.result is not None
        expected = part_hash(job.request.part)
        for artifact in job.result.artifacts:
            with self.subTest(output=artifact.output.value):
                self.assertEqual(artifact.document_hash, expected)
                self.assertEqual(artifact.build_key, job.build_key)

    def test_artifact_metadata_is_plain_json_data(self) -> None:
        """No kernel object can reach a serialized job."""
        job = self.build(*BuildOutput)
        assert job.result is not None
        payload = json.dumps(job.result.to_dict())
        for forbidden in ("cadquery", "TopoDS", "Workplane", "OCP", "object at"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, payload)
        self.assertEqual(json.loads(payload)["status"], "succeeded")

    def test_the_job_summary_is_plain_json_data(self) -> None:
        job = self.build(BuildOutput.GEOMETRY, BuildOutput.RENDER)
        summary = json.loads(json.dumps(job.to_dict()))
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual(summary["options"], {"outputs": ["geometry", "render"]})
        self.assertEqual(summary["build_key"], job.build_key)

    def test_a_drilled_plate_builds_through_the_layer(self) -> None:
        job = self.build(BuildOutput.GEOMETRY, doc=drilled_document())
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.GEOMETRY)
        assert artifact is not None
        expected = 100 * 60 * 10 - math.pi * 10.0**2 * 10
        self.assertAlmostEqual(
            artifact.details["volume_mm3"], expected, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(artifact.details["feature_id"], "plate")


# --- 23: logical versus physical identity ----------------------------------


class TestArtifactIdentity(BuildJobTestCase):
    def test_the_logical_id_is_the_build_key_and_output(self) -> None:
        job = execute_build(
            self.request(BuildOutput.STEP, BuildOutput.GEOMETRY),
            output_directory=self.tmp,
        )
        assert job.result is not None
        for artifact in job.result.artifacts:
            with self.subTest(output=artifact.output.value):
                self.assertEqual(
                    artifact.logical_id,
                    f"{job.build_key}:{artifact.output.value}",
                )

    def test_the_logical_id_contains_no_filesystem_path(self) -> None:
        job = execute_build(self.request(BuildOutput.STL), output_directory=self.tmp)
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.STL)
        assert artifact is not None
        self.assertNotIn("/", artifact.logical_id)
        self.assertNotIn(str(self.tmp), artifact.logical_id)
        self.assertNotIn(self.tmp.name, artifact.logical_id)

    def test_the_physical_path_is_test_local(self) -> None:
        job = execute_build(self.request(BuildOutput.STL), output_directory=self.tmp)
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.STL)
        assert artifact is not None
        self.assertEqual(Path(artifact.path).parent, self.tmp)

    def test_the_filename_is_derived_from_the_build_key(self) -> None:
        """A convenience, documented as such -- not the identity."""
        job = execute_build(self.request(BuildOutput.STEP), output_directory=self.tmp)
        assert job.result is not None
        artifact = job.result.artifact(BuildOutput.STEP)
        assert artifact is not None
        self.assertEqual(Path(artifact.path).stem, job.build_key)


# --- 16-20: failures --------------------------------------------------------


class TestFailureClassification(BuildJobTestCase):
    def test_an_invalid_document_is_rejected_before_any_geometry(self) -> None:
        invalid = document([plate_feature(size={"x": -1, "y": 60, "z": 10})])
        with mock.patch("cad_core.build_job.build_part") as never:
            job = execute_build_document(
                invalid, self.options(BuildOutput.GEOMETRY)
            )
        never.assert_not_called()
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertIs(job.result.error.failure, BuildFailure.DOCUMENT_INVALID)
        self.assertIn("S10", job.result.error.rule_codes)
        self.assertEqual(job.result.artifacts, ())

    def test_an_invalid_document_keeps_the_validator_s_structured_errors(self) -> None:
        invalid = document([plate_feature(colour="red")], author="someone")
        job = execute_build_document(invalid, self.options(BuildOutput.GEOMETRY))
        assert job.result is not None
        error = job.result.error
        self.assertEqual(error.rule_codes, validate(invalid).rule_codes())
        self.assertEqual(len(error.validation_errors), len(validate(invalid).errors))
        self.assertEqual(error.stage, "validation")
        payload = json.loads(json.dumps(error.to_dict()))
        self.assertEqual(payload["failure"], "document_invalid")
        self.assertIn("field_path", payload["validation_errors"][0])

    def test_the_strict_boundary_still_raises_for_an_invalid_document(self) -> None:
        with self.assertRaises(DocumentValidationError):
            request_for_document(
                document([plate_feature(colour="red")]),
                self.options(BuildOutput.GEOMETRY),
            )

    def test_unsupported_geometry_is_its_own_failure(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="in",
            name="imperial",
            features=(Box(id="plate", size=Size(*PLATE_SIZE)),),
        )
        job = execute_build(self.request(BuildOutput.GEOMETRY, part=part))
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertIs(job.result.error.failure, BuildFailure.UNSUPPORTED_GEOMETRY)
        self.assertEqual(job.result.error.stage, "geometry")
        self.assertEqual(job.result.artifacts, ())

    def test_a_geometric_rule_failure_is_its_own_failure(self) -> None:
        """A through-hole that misses its target: rule E1."""
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
        job = execute_build_document(doc, self.options(BuildOutput.GEOMETRY))
        assert job.result is not None
        self.assertIs(job.result.error.failure, BuildFailure.GEOMETRY_FAILED)
        self.assertEqual(job.result.error.rule_codes, ("E1",))
        self.assertEqual(job.result.artifacts, ())

    def test_an_export_failure_is_its_own_failure(self) -> None:
        request = self.request(BuildOutput.STEP)
        (self.tmp / f"{request.build_key}.step").mkdir()
        job = execute_build(request, output_directory=self.tmp)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        error = job.result.error
        self.assertIs(error.failure, BuildFailure.EXPORT_FAILED)
        self.assertIs(error.output, BuildOutput.STEP)
        self.assertEqual(error.stage, "step")
        self.assertEqual(error.exception_type, "StepExportError")
        self.assertEqual(job.result.artifacts, ())

    def test_an_export_failure_message_leaks_no_path(self) -> None:
        request = self.request(BuildOutput.STEP)
        (self.tmp / f"{request.build_key}.step").mkdir()
        job = execute_build(request, output_directory=self.tmp)
        assert job.result is not None
        message = job.result.error.message
        self.assertNotIn(str(self.tmp), message)
        self.assertNotIn(self.tmp.name, message)
        self.assertNotIn("/", message)
        self.assertIn("step", message)
        # ... while the underlying text stays available for diagnosis.
        self.assertIsNotNone(job.result.error.diagnostic)

    def test_an_unexpected_exception_becomes_a_structured_failure(self) -> None:
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=RuntimeError("kernel exploded"),
        ):
            job = execute_build(self.request(BuildOutput.RENDER))
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        error = job.result.error
        self.assertIs(error.failure, BuildFailure.INTERNAL)
        self.assertEqual(error.exception_type, "RuntimeError")
        self.assertEqual(error.stage, "execution")
        self.assertEqual(job.result.artifacts, ())

    def test_an_unexpected_exception_is_kept_but_is_not_the_contract(self) -> None:
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=RuntimeError("kernel exploded"),
        ):
            job = execute_build(self.request(BuildOutput.RENDER))
        assert job.result is not None
        error = job.result.error
        # the public message is stable and carries no traceback
        self.assertNotIn("Traceback", error.message)
        self.assertNotIn("kernel exploded", error.message)
        self.assertNotIn("/", error.message)
        self.assertIn("unexpected internal error", error.message)
        # ... the detail survives for diagnosis
        assert error.diagnostic is not None
        self.assertIn("Traceback", error.diagnostic)
        self.assertIn("kernel exploded", error.diagnostic)
        # ... and it is not part of the serialized contract
        self.assertNotIn("diagnostic", error.to_dict())

    def test_a_job_state_error_is_never_swallowed_into_a_result(self) -> None:
        """A caller's misuse of the state machine stays an exception."""
        job = BuildJob(self.request(BuildOutput.GEOMETRY))
        job.start()
        with self.assertRaises(BuildStateError):
            run_job(job)
        self.assertIs(job.status, BuildStatus.RUNNING)
        self.assertIsNone(job.result)

    def test_the_failure_vocabulary_is_the_documented_one(self) -> None:
        self.assertEqual(
            {failure.value for failure in BuildFailure},
            {
                "document_invalid",
                "unsupported_geometry",
                "geometry_failed",
                "export_failed",
                "job_state",
                "internal",
            },
        )

    def test_a_failed_build_reports_no_artifacts_at_all(self) -> None:
        cases = []

        request = self.request(BuildOutput.STEP)
        (self.tmp / f"{request.build_key}.step").mkdir()
        cases.append(("export", execute_build(request, output_directory=self.tmp)))

        cases.append(
            (
                "document",
                execute_build_document(
                    document([plate_feature(size={"x": 0, "y": 60, "z": 10})]),
                    self.options(BuildOutput.GEOMETRY),
                ),
            )
        )
        part = Part(
            schema_version="1.0.0",
            units="in",
            name="imperial",
            features=(Box(id="plate", size=Size(*PLATE_SIZE)),),
        )
        cases.append(
            (
                "unsupported",
                execute_build(self.request(BuildOutput.GEOMETRY, part=part)),
            )
        )

        for name, job in cases:
            with self.subTest(case=name):
                assert job.result is not None
                self.assertIs(job.status, BuildStatus.FAILED)
                self.assertFalse(job.result.succeeded)
                self.assertEqual(job.result.artifacts, ())
                self.assertEqual(job.result.produced_outputs(), ())
                self.assertIsNone(job.result.geometry)
                self.assertIsNone(job.result.render_model)
                self.assertIsNotNone(job.result.error)


# --- failure atomicity ------------------------------------------------------


class TestFailureAtomicity(BuildJobTestCase):
    def blocked_stl_build(self) -> Tuple[BuildJob, Path, Path]:
        """STEP succeeds, then STL cannot be written."""
        request = self.request(BuildOutput.STEP, BuildOutput.STL)
        step_path = self.tmp / f"{request.build_key}.step"
        stl_path = self.tmp / f"{request.build_key}.stl"
        stl_path.mkdir()
        job = execute_build(request, output_directory=self.tmp)
        return job, step_path, stl_path

    def test_a_partial_build_is_failed_not_succeeded(self) -> None:
        job, _, _ = self.blocked_stl_build()
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertFalse(job.result.succeeded)

    def test_the_failing_output_is_recorded(self) -> None:
        job, _, _ = self.blocked_stl_build()
        assert job.result is not None
        self.assertIs(job.result.error.output, BuildOutput.STL)
        self.assertEqual(job.result.error.stage, "stl")

    def test_the_outputs_that_had_completed_are_recorded(self) -> None:
        """Nothing is silently lost -- but nothing is reported as produced."""
        job, _, _ = self.blocked_stl_build()
        assert job.result is not None
        self.assertEqual(
            job.result.error.completed_outputs, (BuildOutput.STEP,)
        )
        self.assertEqual(job.result.artifacts, ())

    def test_files_written_before_the_failure_are_deleted(self) -> None:
        job, step_path, stl_path = self.blocked_stl_build()
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertFalse(step_path.exists())
        # ... and the blocking directory is left alone.
        self.assertTrue(stl_path.is_dir())
        self.assertEqual([path.name for path in self.tmp.iterdir()], [stl_path.name])

    def test_no_success_looking_artifact_survives(self) -> None:
        job, step_path, _ = self.blocked_stl_build()
        assert job.result is not None
        self.assertEqual(job.result.produced_outputs(), ())
        self.assertIsNone(job.result.artifact(BuildOutput.STEP))
        self.assertFalse(step_path.exists())

    def test_a_successful_build_after_a_failure_is_unaffected(self) -> None:
        self.blocked_stl_build()
        clean = self.tmp / "clean"
        clean.mkdir()
        job = execute_build(
            self.request(BuildOutput.STEP, BuildOutput.STL), output_directory=clean
        )
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        assert job.result is not None
        self.assertEqual(len(job.result.artifacts), 2)


# --- 24-26: nothing else changes -------------------------------------------


class TestNoSideEffects(BuildJobTestCase):
    def test_the_build_does_not_mutate_the_source_part(self) -> None:
        part = self.plate()
        before = part_to_json(part)
        job = execute_build(
            BuildRequest(part=part, options=self.options(*BuildOutput)),
            output_directory=self.tmp,
        )
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertEqual(part_to_json(part), before)
        self.assertIs(job.request.part, part)

    def test_the_build_does_not_change_the_canonical_document_or_its_hash(self) -> None:
        part = self.plate()
        before_text, before_hash = part_to_json(part), part_hash(part)
        execute_build(
            BuildRequest(part=part, options=self.options(*BuildOutput)),
            output_directory=self.tmp,
        )
        self.assertEqual(part_to_json(part), before_text)
        self.assertEqual(part_hash(part), before_hash)

    def test_a_failed_build_does_not_change_the_document_either(self) -> None:
        part = self.plate()
        before = (part_to_json(part), part_hash(part))
        request = BuildRequest(part=part, options=self.options(BuildOutput.STEP))
        (self.tmp / f"{request.build_key}.step").mkdir()
        job = execute_build(request, output_directory=self.tmp)
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertEqual((part_to_json(part), part_hash(part)), before)

    def test_the_step_output_is_the_existing_exporter_s_output(self) -> None:
        """The layer calls the exporters; it does not reimplement them."""
        part = self.plate()
        job = execute_build(
            BuildRequest(part=part, options=self.options(BuildOutput.STEP)),
            output_directory=self.tmp,
        )
        assert job.result is not None
        through_layer = read_step(job.result.artifact(BuildOutput.STEP).path)

        direct_path = self.tmp / "direct.step"
        export_step(build_part(part), direct_path)
        direct = read_step(direct_path)

        self.assertEqual(through_layer.is_solid(), direct.is_solid())
        self.assertEqual(through_layer.solid_count(), direct.solid_count())
        self.assertAlmostEqual(
            through_layer.volume(), direct.volume(), delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(
            len(through_layer.shape.Faces()), len(direct.shape.Faces())
        )

    def test_the_render_output_is_the_existing_render_model(self) -> None:
        part = self.plate()
        job = execute_build(
            BuildRequest(part=part, options=self.options(BuildOutput.RENDER))
        )
        assert job.result is not None and job.result.render_model is not None
        direct = build_render_model(build_part(part))
        self.assertEqual(
            json.dumps(job.result.render_model.to_dict(), sort_keys=True),
            json.dumps(direct.to_dict(), sort_keys=True),
        )

    def test_repeated_builds_agree_on_everything_but_bytes_and_run_id(self) -> None:
        """Determinism, with the documented STEP/IGES byte exception."""
        request = self.request(*BuildOutput)
        second_directory = self.tmp / "second"
        second_directory.mkdir()
        first = execute_build(request, output_directory=self.tmp)
        second = execute_build(request, output_directory=second_directory)
        assert first.result is not None and second.result is not None

        self.assertEqual(first.build_key, second.build_key)
        self.assertEqual(first.document_hash, second.document_hash)
        self.assertEqual(
            first.result.produced_outputs(), second.result.produced_outputs()
        )
        self.assertEqual(
            first.result.artifact(BuildOutput.GEOMETRY).details,
            second.result.artifact(BuildOutput.GEOMETRY).details,
        )
        self.assertEqual(
            first.result.artifact(BuildOutput.RENDER).details,
            second.result.artifact(BuildOutput.RENDER).details,
        )
        self.assertEqual(
            json.dumps(first.result.render_model.to_dict(), sort_keys=True),
            json.dumps(second.result.render_model.to_dict(), sort_keys=True),
        )
        # STL bytes were measured stable; STEP and IGES headers were not.
        stl_first = Path(first.result.artifact(BuildOutput.STL).path).read_bytes()
        stl_second = Path(second.result.artifact(BuildOutput.STL).path).read_bytes()
        self.assertEqual(stl_first, stl_second)
        self.assertNotEqual(first.execution_id, second.execution_id)


# --- package boundary -------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "src" / "cad_core"

    def imported_modules(self, filename: str) -> set:
        tree = ast.parse((self.ROOT / filename).read_text())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_nothing_upstream_depends_on_the_build_layer(self) -> None:
        for module in (
            "model.py",
            "validator.py",
            "errors.py",
            "serialization.py",
            "featurescript.py",
            "onshape_adapter.py",
            "onshape_fakes.py",
            "local_cad.py",
            "edge_selection.py",
            "step_export.py",
            "iges_export.py",
            "stl_export.py",
            "render_model.py",
            "__init__.py",
        ):
            with self.subTest(module=module):
                self.assertNotIn(
                    "cad_core.build_job", self.imported_modules(module)
                )

    def test_the_build_layer_may_depend_on_the_execution_backends(self) -> None:
        imported = self.imported_modules("build_job.py")
        for expected in (
            "cad_core.local_cad",
            "cad_core.step_export",
            "cad_core.iges_export",
            "cad_core.stl_export",
            "cad_core.render_model",
            "cad_core.serialization",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, imported)

    def test_the_build_layer_does_not_touch_featurescript_or_onshape(self) -> None:
        """Out of scope for this stage: local builds only."""
        imported = self.imported_modules("build_job.py")
        for forbidden in (
            "cad_core.featurescript",
            "cad_core.onshape_adapter",
            "cad_core.onshape_fakes",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, imported)

    def test_it_is_not_re_exported_from_the_package_root(self) -> None:
        """``import cad_core`` must stay free of the geometry kernel."""
        import cad_core

        self.assertNotIn("cad_core.build_job", self.imported_modules("__init__.py"))
        self.assertFalse(hasattr(cad_core, "execute_build"))
        self.assertNotIn("execute_build", getattr(cad_core, "__all__", ()))

    def test_no_database_queue_or_service_dependency_was_introduced(self) -> None:
        imported = self.imported_modules("build_job.py")
        for forbidden in (
            "sqlite3",
            "redis",
            "celery",
            "kombu",
            "pika",
            "psycopg2",
            "sqlalchemy",
            "flask",
            "fastapi",
            "django",
            "requests",
            "httpx",
            "urllib",
            "urllib.request",
            "socket",
            "multiprocessing",
            "concurrent.futures",
            "asyncio",
            "subprocess",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(
                        name == forbidden or name.startswith(forbidden + ".")
                        for name in imported
                    ),
                    msg=f"build_job.py imports {forbidden}",
                )

    def test_the_layer_adds_no_cad_semantics(self) -> None:
        """No specification vocabulary is redefined here."""
        source = (self.ROOT / "build_job.py").read_text()
        tree = ast.parse(source)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
        }
        from cad_core.model import FEATURE_TYPES

        for feature_type in FEATURE_TYPES:
            with self.subTest(feature_type=feature_type):
                self.assertNotIn(
                    feature_type.replace("_", " ").title().replace(" ", ""), defined
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
