"""Unit tests for the derived-artifact registry and manifest.

The point of this layer is that three things stay apart: artifact identity,
artifact content, and the physical location of a file. So most of these tests
are about what identity does *not* depend on -- paths, extensions, execution
ids, clocks -- and about checksums coming from real bytes rather than from
anything convenient.

Every checksum here is taken from a file the real local pipeline produced. No
fake file is used anywhere.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cad_core import validate
from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    FILE_KINDS,
    IN_MEMORY_KINDS,
    KIND_DEFAULT_EXTENSION,
    KIND_EXTENSIONS,
    KIND_FORMATS,
    KIND_STORAGE,
    Artifact,
    ArtifactError,
    ArtifactKind,
    ArtifactManifest,
    ArtifactPublicationError,
    ArtifactStorage,
    artifact_logical_id,
    build_manifest,
    canonical_render_bytes,
    file_checksum,
    publish_file_artifact,
    publish_geometry_artifact,
    publish_render_artifact,
    render_checksum,
)
from cad_core.build_job import (
    BuildArtifact,
    BuildOptions,
    BuildOutput,
    BuildRequest,
    BuildStatus,
    execute_build,
)
from cad_core.iges_export import export_iges
from cad_core.local_cad import build_part
from cad_core.model import Part
from cad_core.render_model import build_render_model
from cad_core.serialization import part_hash
from cad_core.step_export import export_step
from cad_core.stl_export import export_stl

VOLUME_TOLERANCE_MM3 = 1e-6

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


#: The five real geometries every checksum test draws on.
def geometry_corpus() -> Tuple[Tuple[str, Dict[str, Any]], ...]:
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


class ArtifactTestCase(unittest.TestCase):
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

    def request(self, *kinds: ArtifactKind, part: Any = None) -> BuildRequest:
        return BuildRequest(
            part=part if part is not None else self.plate(),
            options=BuildOptions.for_outputs(*kinds),
        )

    def build(self, *kinds: ArtifactKind, part: Any = None, directory: Any = None):
        return execute_build(
            self.request(*kinds, part=part),
            output_directory=directory if directory is not None else self.tmp,
        )

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir()
        return path


# --- the artifact model -----------------------------------------------------


class TestArtifactModel(ArtifactTestCase):
    def test_the_kinds_are_the_existing_local_outputs(self) -> None:
        self.assertEqual(
            [kind.value for kind in ArtifactKind],
            ["geometry", "step", "iges", "stl", "render"],
        )

    def test_storage_kind_is_explicit_per_artifact_kind(self) -> None:
        self.assertEqual(
            KIND_STORAGE,
            {
                ArtifactKind.GEOMETRY: ArtifactStorage.IN_MEMORY,
                ArtifactKind.STEP: ArtifactStorage.FILE,
                ArtifactKind.IGES: ArtifactStorage.FILE,
                ArtifactKind.STL: ArtifactStorage.FILE,
                ArtifactKind.RENDER: ArtifactStorage.IN_MEMORY,
            },
        )
        self.assertEqual(
            FILE_KINDS, (ArtifactKind.STEP, ArtifactKind.IGES, ArtifactKind.STL)
        )
        self.assertEqual(
            IN_MEMORY_KINDS, (ArtifactKind.GEOMETRY, ArtifactKind.RENDER)
        )

    def test_logical_type_format_and_storage_are_separate_fields(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        artifact = job.result.artifact(ArtifactKind.STL)
        assert artifact is not None
        self.assertIs(artifact.kind, ArtifactKind.STL)
        self.assertEqual(artifact.format, "stl-binary")
        self.assertIs(artifact.storage, ArtifactStorage.FILE)
        self.assertEqual(artifact.file_extension, ".stl")

    def test_the_extension_table_comes_from_the_exporters(self) -> None:
        """So it cannot drift from what they actually accept."""
        from cad_core.iges_export import IGES_EXTENSIONS
        from cad_core.step_export import STEP_EXTENSIONS
        from cad_core.stl_export import STL_EXTENSIONS

        self.assertEqual(KIND_EXTENSIONS[ArtifactKind.STEP], STEP_EXTENSIONS)
        self.assertEqual(KIND_EXTENSIONS[ArtifactKind.IGES], IGES_EXTENSIONS)
        self.assertEqual(KIND_EXTENSIONS[ArtifactKind.STL], STL_EXTENSIONS)
        self.assertEqual(KIND_DEFAULT_EXTENSION[ArtifactKind.STEP], ".step")

    def test_no_unsupported_format_was_introduced(self) -> None:
        self.assertEqual(
            set(KIND_FORMATS.values()),
            {"brep-in-memory", "step", "iges-brep", "stl-binary", "render-model"},
        )

    def test_the_build_layer_names_are_aliases_of_the_artifact_layer(self) -> None:
        """The refactor kept the Stage 16 API rather than breaking callers."""
        self.assertIs(BuildOutput, ArtifactKind)
        self.assertIs(BuildArtifact, Artifact)


# --- 1-6, 21, 22: identity --------------------------------------------------


class TestArtifactIdentity(ArtifactTestCase):
    def test_the_logical_id_is_build_key_and_kind(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        for artifact in job.result.manifest.artifacts:
            with self.subTest(kind=artifact.kind.value):
                self.assertEqual(
                    artifact.logical_id, f"{job.build_key}:{artifact.kind.value}"
                )
                self.assertEqual(
                    artifact.logical_id,
                    artifact_logical_id(job.build_key, artifact.kind),
                )

    def test_identity_is_deterministic(self) -> None:
        for name, doc in geometry_corpus():
            part = self.part_from(doc)
            request = self.request(*ArtifactKind, part=part)
            with self.subTest(name=name):
                ids = {
                    artifact_logical_id(request.build_key, kind)
                    for _ in range(5)
                    for kind in ArtifactKind
                }
                self.assertEqual(len(ids), len(ArtifactKind))

    def test_the_same_build_key_and_kind_give_the_same_logical_id(self) -> None:
        first = self.request(*ArtifactKind)
        second = self.request(*ArtifactKind)
        self.assertEqual(first.build_key, second.build_key)
        for kind in ArtifactKind:
            with self.subTest(kind=kind.value):
                self.assertEqual(
                    artifact_logical_id(first.build_key, kind),
                    artifact_logical_id(second.build_key, kind),
                )

    def test_a_different_build_key_gives_a_different_artifact_id(self) -> None:
        keys = {
            name: self.request(*ArtifactKind, part=self.part_from(doc)).build_key
            for name, doc in geometry_corpus()
        }
        self.assertEqual(len(set(keys.values())), len(keys))
        identifiers = {
            artifact_logical_id(key, ArtifactKind.STEP) for key in keys.values()
        }
        self.assertEqual(len(identifiers), len(keys))

    def test_a_different_kind_gives_a_different_artifact_id(self) -> None:
        key = self.request(*ArtifactKind).build_key
        identifiers = {artifact_logical_id(key, kind) for kind in ArtifactKind}
        self.assertEqual(len(identifiers), len(ArtifactKind))

    def test_the_filesystem_path_does_not_affect_identity(self) -> None:
        request = self.request(*ArtifactKind)
        elsewhere = self.subdirectory("elsewhere")
        first = execute_build(request, output_directory=self.tmp)
        second = execute_build(request, output_directory=elsewhere)
        assert first.result is not None and second.result is not None
        for kind in FILE_KINDS:
            with self.subTest(kind=kind.value):
                one = first.result.artifact(kind)
                two = second.result.artifact(kind)
                self.assertNotEqual(one.path, two.path)
                self.assertEqual(one.logical_id, two.logical_id)

    def test_the_file_extension_does_not_affect_identity(self) -> None:
        """``.step`` and ``.stp`` are one logical STEP artifact.

        Identity follows the logical output, so two files of the same build
        written with different extensions share a logical id and differ only
        in content -- path, extension, checksum.
        """
        part = self.plate()
        request = self.request(ArtifactKind.STEP, part=part)
        local = build_part(part)
        long_path = self.tmp / f"{request.build_key}.step"
        short_path = self.tmp / f"{request.build_key}.stp"
        export_step(local, long_path)
        export_step(local, short_path)

        published = [
            publish_file_artifact(
                document_hash=request.document_hash,
                build_key=request.build_key,
                kind=ArtifactKind.STEP,
                path=path,
            )
            for path in (long_path, short_path)
        ]
        self.assertEqual(published[0].logical_id, published[1].logical_id)
        self.assertEqual(
            [artifact.file_extension for artifact in published], [".step", ".stp"]
        )
        self.assertNotEqual(published[0].path, published[1].path)
        # ... and both are genuinely STEP files of the same geometry.
        self.assertEqual(published[0].format, published[1].format)

    def test_the_execution_id_does_not_affect_identity(self) -> None:
        request = self.request(*ArtifactKind)
        first = execute_build(request, output_directory=self.tmp)
        second = execute_build(request, output_directory=self.subdirectory("two"))
        assert first.result is not None and second.result is not None
        self.assertNotEqual(first.execution_id, second.execution_id)
        self.assertEqual(
            [a.logical_id for a in first.result.manifest.artifacts],
            [a.logical_id for a in second.result.manifest.artifacts],
        )

    def test_a_logical_id_never_contains_an_execution_id(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        for artifact in job.result.manifest.artifacts:
            with self.subTest(kind=artifact.kind.value):
                self.assertNotIn(job.execution_id, artifact.logical_id)

    def test_a_logical_id_never_contains_a_filesystem_path(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        for artifact in job.result.manifest.artifacts:
            with self.subTest(kind=artifact.kind.value):
                self.assertNotIn("/", artifact.logical_id)
                self.assertNotIn("\\", artifact.logical_id)
                self.assertNotIn(str(self.tmp), artifact.logical_id)
                self.assertNotIn(self.tmp.name, artifact.logical_id)
                self.assertNotIn(".step", artifact.logical_id)

    def test_a_timestamp_does_not_affect_identity(self) -> None:
        request = self.request(*ArtifactKind)
        first = execute_build(request, output_directory=self.tmp)
        time.sleep(0.01)
        second = execute_build(request, output_directory=self.subdirectory("later"))
        assert first.result is not None and second.result is not None
        self.assertEqual(
            first.result.manifest.canonical_bytes(),
            second.result.manifest.canonical_bytes(),
        )

    def test_identity_uses_the_existing_build_key_scheme(self) -> None:
        """No second hash scheme was invented for identity."""
        request = self.request(ArtifactKind.STEP)
        self.assertTrue(
            artifact_logical_id(request.build_key, ArtifactKind.STEP).startswith(
                request.build_key
            )
        )
        self.assertEqual(len(request.build_key), 64)

    def test_a_bad_kind_is_refused(self) -> None:
        for bad in ("step", 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(ArtifactError):
                    artifact_logical_id("0" * 64, bad)  # type: ignore[arg-type]


# --- 7-13: checksums and sizes, from real outputs ---------------------------


class TestChecksums(ArtifactTestCase):
    def test_step_checksum_is_the_sha256_of_the_real_file(self) -> None:
        for name, doc in geometry_corpus():
            directory = self.subdirectory(f"step-{name}")
            job = self.build(
                ArtifactKind.STEP, part=self.part_from(doc), directory=directory
            )
            assert job.result is not None
            artifact = job.result.artifact(ArtifactKind.STEP)
            assert artifact is not None
            raw = Path(artifact.path).read_bytes()
            with self.subTest(name=name):
                self.assertEqual(artifact.checksum, hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifact.size_bytes, len(raw))

    def test_iges_checksum_is_the_sha256_of_the_real_file(self) -> None:
        for name, doc in geometry_corpus():
            directory = self.subdirectory(f"iges-{name}")
            job = self.build(
                ArtifactKind.IGES, part=self.part_from(doc), directory=directory
            )
            assert job.result is not None
            artifact = job.result.artifact(ArtifactKind.IGES)
            assert artifact is not None
            raw = Path(artifact.path).read_bytes()
            with self.subTest(name=name):
                self.assertEqual(artifact.checksum, hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifact.size_bytes, len(raw))

    def test_stl_checksum_is_the_sha256_of_the_real_file(self) -> None:
        for name, doc in geometry_corpus():
            directory = self.subdirectory(f"stl-{name}")
            job = self.build(
                ArtifactKind.STL, part=self.part_from(doc), directory=directory
            )
            assert job.result is not None
            artifact = job.result.artifact(ArtifactKind.STL)
            assert artifact is not None
            raw = Path(artifact.path).read_bytes()
            with self.subTest(name=name):
                self.assertEqual(artifact.checksum, hashlib.sha256(raw).hexdigest())
                self.assertEqual(artifact.size_bytes, len(raw))
                self.assertGreater(artifact.details["triangle_count"], 0)

    def test_the_checksum_helper_streams_the_real_bytes(self) -> None:
        local = build_part(self.plate())
        path = self.tmp / "direct.stl"
        export_stl(local, path)
        self.assertEqual(
            file_checksum(path), hashlib.sha256(path.read_bytes()).hexdigest()
        )

    def test_a_checksum_is_not_any_other_hash_in_the_system(self) -> None:
        """Not the document JSON, not the build key, not the filename."""
        job = self.build(ArtifactKind.STEP)
        assert job.result is not None
        artifact = job.result.artifact(ArtifactKind.STEP)
        assert artifact is not None
        self.assertNotEqual(artifact.checksum, job.document_hash)
        self.assertNotEqual(artifact.checksum, job.build_key)
        self.assertNotEqual(
            artifact.checksum,
            hashlib.sha256(Path(artifact.path).name.encode()).hexdigest(),
        )
        self.assertEqual(CHECKSUM_ALGORITHM, "sha256")

    def test_render_checksum_is_the_canonical_render_json(self) -> None:
        for name, doc in geometry_corpus():
            part = self.part_from(doc)
            job = self.build(ArtifactKind.RENDER, part=part)
            assert job.result is not None and job.result.render_model is not None
            artifact = job.result.artifact(ArtifactKind.RENDER)
            assert artifact is not None
            payload = canonical_render_bytes(job.result.render_model)
            with self.subTest(name=name):
                self.assertEqual(
                    artifact.checksum, hashlib.sha256(payload).hexdigest()
                )
                self.assertEqual(artifact.size_bytes, len(payload))
                self.assertEqual(
                    artifact.checksum, render_checksum(job.result.render_model)
                )

    def test_the_canonical_render_bytes_reuse_the_existing_serialization(self) -> None:
        """No second render serialization: ``to_dict()`` is the source."""
        model = build_render_model(build_part(self.plate()))
        expected = json.dumps(
            model.to_dict(),
            separators=(",", ":"),
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        self.assertEqual(canonical_render_bytes(model), expected)
        self.assertEqual(json.loads(canonical_render_bytes(model)), model.to_dict())

    def test_the_render_checksum_is_stable_across_builds(self) -> None:
        request = self.request(ArtifactKind.RENDER)
        first = execute_build(request)
        second = execute_build(request)
        assert first.result is not None and second.result is not None
        self.assertEqual(
            first.result.artifact(ArtifactKind.RENDER).checksum,
            second.result.artifact(ArtifactKind.RENDER).checksum,
        )

    def test_geometry_has_no_fabricated_byte_checksum(self) -> None:
        for name, doc in geometry_corpus():
            job = self.build(ArtifactKind.GEOMETRY, part=self.part_from(doc))
            assert job.result is not None
            artifact = job.result.artifact(ArtifactKind.GEOMETRY)
            assert artifact is not None
            with self.subTest(name=name):
                self.assertIsNone(artifact.checksum)
                self.assertIsNone(artifact.size_bytes)
                self.assertIsNone(artifact.path)
                self.assertIsNone(artifact.file_extension)
                self.assertIsNone(artifact.to_dict()["checksum_algorithm"])

    def test_geometry_carries_measurements_instead(self) -> None:
        job = self.build(ArtifactKind.GEOMETRY)
        assert job.result is not None
        details = job.result.artifact(ArtifactKind.GEOMETRY).details
        self.assertEqual(details["feature_id"], "plate")
        self.assertEqual(details["solid_count"], 1)
        self.assertAlmostEqual(
            details["volume_mm3"], 60000.0, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(details["face_count"], 6)
        self.assertEqual(details["edge_count"], 12)
        self.assertEqual(details["vertex_count"], 8)
        self.assertEqual(
            details["bounding_box"]["size"], {"x": 100.0, "y": 60.0, "z": 10.0}
        )

    def test_object_memory_is_never_reported_as_artifact_size(self) -> None:
        import sys

        job = self.build(ArtifactKind.GEOMETRY, ArtifactKind.RENDER)
        assert job.result is not None
        geometry = job.result.artifact(ArtifactKind.GEOMETRY)
        self.assertIsNone(geometry.size_bytes)
        render = job.result.artifact(ArtifactKind.RENDER)
        assert job.result.render_model is not None
        self.assertNotEqual(
            render.size_bytes, sys.getsizeof(job.result.render_model)
        )
        self.assertEqual(
            render.size_bytes, len(canonical_render_bytes(job.result.render_model))
        )

    def test_file_size_equals_the_real_byte_count(self) -> None:
        job = self.build(*FILE_KINDS)
        assert job.result is not None
        for artifact in job.result.manifest.artifacts:
            with self.subTest(kind=artifact.kind.value):
                path = Path(artifact.path)
                self.assertEqual(artifact.size_bytes, path.stat().st_size)
                self.assertEqual(artifact.size_bytes, len(path.read_bytes()))


# --- 14, 15: JSON compatibility --------------------------------------------


class TestJsonCompatibility(ArtifactTestCase):
    def walk(self, value: Any, path: str) -> None:
        if isinstance(value, (str, int, float)) or value is None:
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                self.walk(item, f"{path}[{index}]")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self.assertIsInstance(key, str, msg=path)
                self.walk(item, f"{path}.{key}")
            return
        self.fail(f"{path} is a {type(value).__module__}.{type(value).__name__}")

    def test_artifact_metadata_is_json_serializable(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        for artifact in job.result.manifest.artifacts:
            with self.subTest(kind=artifact.kind.value):
                payload = artifact.to_dict()
                self.walk(payload, "artifact")
                self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_the_manifest_is_json_serializable(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        payload = job.result.manifest.to_dict()
        self.walk(payload, "manifest")
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_no_kernel_object_reaches_the_metadata(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        text = json.dumps(job.result.manifest.to_dict())
        for forbidden in ("cadquery", "OCP", "TopoDS", "Workplane", "object at"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)

    def test_the_render_model_is_not_duplicated_in_metadata(self) -> None:
        job = self.build(ArtifactKind.RENDER)
        assert job.result is not None
        text = json.dumps(job.result.manifest.to_dict())
        self.assertNotIn('"vertices"', text)
        self.assertNotIn('"normals"', text)
        self.assertNotIn('"triangles"', text)
        self.assertIn("vertex_count", text)

    def test_no_traceback_or_environment_data_appears(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        text = json.dumps(job.result.manifest.to_dict())
        for forbidden in ("Traceback", "PYTHONPATH", "HOME=", "File \\\""):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


# --- 16, 17, 23: the manifest ----------------------------------------------


class TestManifest(ArtifactTestCase):
    def test_the_manifest_names_its_document_and_build(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        manifest = job.result.manifest
        self.assertEqual(manifest.document_hash, part_hash(job.request.part))
        self.assertEqual(manifest.build_key, job.build_key)

    def test_ordering_is_the_canonical_artifact_order(self) -> None:
        """Not the order the caller requested outputs in."""
        reversed_request = BuildRequest(
            part=self.plate(),
            options=BuildOptions(outputs=tuple(reversed(tuple(ArtifactKind)))),
        )
        job = execute_build(reversed_request, output_directory=self.tmp)
        assert job.result is not None
        self.assertEqual(job.result.manifest.kinds(), tuple(ArtifactKind))

    def test_ordering_is_deterministic_across_builds(self) -> None:
        first = self.build(*ArtifactKind)
        second = self.build(
            *reversed(tuple(ArtifactKind)), directory=self.subdirectory("two")
        )
        assert first.result is not None and second.result is not None
        self.assertEqual(first.result.manifest.kinds(), second.result.manifest.kinds())

    def test_the_constructor_sorts_so_the_invariant_cannot_be_bypassed(self) -> None:
        job = self.build(ArtifactKind.GEOMETRY, ArtifactKind.STL)
        assert job.result is not None
        shuffled = build_manifest(
            document_hash=job.document_hash,
            build_key=job.build_key,
            artifacts=tuple(reversed(job.result.manifest.artifacts)),
        )
        self.assertEqual(
            shuffled.kinds(), (ArtifactKind.GEOMETRY, ArtifactKind.STL)
        )

    def test_the_manifest_lists_exactly_the_successful_artifacts(self) -> None:
        for kinds in (
            (ArtifactKind.GEOMETRY,),
            (ArtifactKind.STL,),
            (ArtifactKind.GEOMETRY, ArtifactKind.RENDER),
            (ArtifactKind.STEP, ArtifactKind.IGES),
            tuple(ArtifactKind),
        ):
            directory = self.subdirectory("-".join(k.value for k in kinds))
            job = self.build(*kinds, directory=directory)
            assert job.result is not None
            with self.subTest(kinds=[k.value for k in kinds]):
                self.assertEqual(set(job.result.manifest.kinds()), set(kinds))
                self.assertEqual(len(job.result.manifest.artifacts), len(kinds))
                self.assertEqual(
                    job.result.manifest.artifacts, job.result.artifacts
                )

    def test_a_manifest_refuses_a_foreign_artifact(self) -> None:
        job = self.build(ArtifactKind.GEOMETRY)
        assert job.result is not None
        artifact = job.result.artifact(ArtifactKind.GEOMETRY)
        with self.assertRaises(ArtifactError):
            build_manifest(
                document_hash=job.document_hash,
                build_key="0" * 64,
                artifacts=(artifact,),
            )
        with self.assertRaises(ArtifactError):
            build_manifest(
                document_hash="0" * 64,
                build_key=job.build_key,
                artifacts=(artifact,),
            )

    def test_a_manifest_refuses_two_artifacts_of_one_kind(self) -> None:
        job = self.build(ArtifactKind.GEOMETRY)
        assert job.result is not None
        artifact = job.result.artifact(ArtifactKind.GEOMETRY)
        with self.assertRaises(ArtifactError):
            build_manifest(
                document_hash=job.document_hash,
                build_key=job.build_key,
                artifacts=(artifact, artifact),
            )

    def test_the_canonical_manifest_is_byte_deterministic(self) -> None:
        """Even though STEP bytes are not. This is the critical separation."""
        for name, doc in geometry_corpus():
            part = self.part_from(doc)
            request = self.request(*ArtifactKind, part=part)
            first = execute_build(
                request, output_directory=self.subdirectory(f"a-{name}")
            )
            second = execute_build(
                request, output_directory=self.subdirectory(f"b-{name}")
            )
            assert first.result is not None and second.result is not None
            with self.subTest(name=name):
                self.assertEqual(
                    first.result.manifest.canonical_bytes(),
                    second.result.manifest.canonical_bytes(),
                )
                self.assertEqual(
                    first.result.manifest.canonical_hash(),
                    second.result.manifest.canonical_hash(),
                )

    def test_the_canonical_manifest_excludes_paths_sizes_and_checksums(self) -> None:
        job = self.build(*ArtifactKind)
        assert job.result is not None
        canonical = job.result.manifest.canonical()
        text = json.dumps(canonical)
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        for artifact in canonical["artifacts"]:
            with self.subTest(kind=artifact["kind"]):
                self.assertNotIn("path", artifact)
                self.assertNotIn("size_bytes", artifact)
                self.assertNotIn("checksum", artifact)
                self.assertIn("logical_id", artifact)
                self.assertIn("details", artifact)

    def test_the_step_checksum_really_does_vary_across_builds(self) -> None:
        """The reason a checksum must not be part of identity.

        STEP headers carry a timestamp and an incrementing translator counter,
        measured in Stage 5. So the same build produces different STEP bytes
        while the canonical manifest stays identical.
        """
        request = self.request(ArtifactKind.STEP)
        first = execute_build(request, output_directory=self.subdirectory("s1"))
        second = execute_build(request, output_directory=self.subdirectory("s2"))
        assert first.result is not None and second.result is not None
        self.assertNotEqual(
            first.result.artifact(ArtifactKind.STEP).checksum,
            second.result.artifact(ArtifactKind.STEP).checksum,
        )
        self.assertEqual(
            first.result.manifest.canonical_bytes(),
            second.result.manifest.canonical_bytes(),
        )

    def test_identity_and_content_are_separate_concepts(self) -> None:
        request = self.request(ArtifactKind.STEP)
        first = execute_build(request, output_directory=self.subdirectory("c1"))
        second = execute_build(request, output_directory=self.subdirectory("c2"))
        assert first.result is not None and second.result is not None
        one = first.result.artifact(ArtifactKind.STEP)
        two = second.result.artifact(ArtifactKind.STEP)
        self.assertEqual(one.logical_id, two.logical_id)
        self.assertNotEqual(one.checksum, two.checksum)
        self.assertNotEqual(one.path, two.path)

    def test_the_stl_checksum_is_stable_because_its_bytes_are(self) -> None:
        """Measured in Stage 7 and unchanged: STL has no header timestamp."""
        request = self.request(ArtifactKind.STL)
        first = execute_build(request, output_directory=self.subdirectory("t1"))
        second = execute_build(request, output_directory=self.subdirectory("t2"))
        assert first.result is not None and second.result is not None
        self.assertEqual(
            first.result.artifact(ArtifactKind.STL).checksum,
            second.result.artifact(ArtifactKind.STL).checksum,
        )

    def test_a_failed_build_has_an_empty_manifest(self) -> None:
        request = self.request(ArtifactKind.STEP)
        (self.tmp / f"{request.build_key}.step").mkdir()
        job = execute_build(request, output_directory=self.tmp)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertEqual(job.result.manifest.artifacts, ())
        self.assertEqual(job.result.manifest.kinds(), ())
        self.assertEqual(job.result.manifest.build_key, job.build_key)


# --- 18, 19, 20: publication ------------------------------------------------


class TestPublication(ArtifactTestCase):
    def test_a_missing_file_cannot_be_published(self) -> None:
        with self.assertRaises(ArtifactPublicationError) as caught:
            publish_file_artifact(
                document_hash="0" * 64,
                build_key="1" * 64,
                kind=ArtifactKind.STEP,
                path=self.tmp / "absent.step",
            )
        self.assertIn("not written", str(caught.exception))

    def test_an_empty_file_cannot_be_published(self) -> None:
        empty = self.tmp / "empty.step"
        empty.write_bytes(b"")
        with self.assertRaises(ArtifactPublicationError) as caught:
            publish_file_artifact(
                document_hash="0" * 64,
                build_key="1" * 64,
                kind=ArtifactKind.STEP,
                path=empty,
            )
        self.assertIn("empty", str(caught.exception))

    def test_a_wrong_extension_cannot_be_published(self) -> None:
        local = build_part(self.plate())
        path = self.tmp / "model.step"
        export_step(local, path)
        with self.assertRaises(ArtifactPublicationError) as caught:
            publish_file_artifact(
                document_hash="0" * 64,
                build_key="1" * 64,
                kind=ArtifactKind.IGES,
                path=path,
            )
        self.assertIn("iges", str(caught.exception))

    def test_an_in_memory_kind_cannot_be_published_as_a_file(self) -> None:
        for kind in IN_MEMORY_KINDS:
            with self.subTest(kind=kind.value):
                with self.assertRaises(ArtifactPublicationError):
                    publish_file_artifact(
                        document_hash="0" * 64,
                        build_key="1" * 64,
                        kind=kind,
                        path=self.tmp / "x.step",
                    )

    def test_a_structurally_inconsistent_stl_cannot_be_published(self) -> None:
        """The declared triangle count must account for the file's size."""
        local = build_part(self.plate())
        path = self.tmp / "truncated.stl"
        export_stl(local, path)
        raw = path.read_bytes()
        path.write_bytes(raw[:-50])  # one triangle short of its own header
        with self.assertRaises(ArtifactPublicationError) as caught:
            publish_file_artifact(
                document_hash="0" * 64,
                build_key="1" * 64,
                kind=ArtifactKind.STL,
                path=path,
            )
        self.assertIn("triangle count", str(caught.exception))

    def test_publication_computes_the_checksum_from_the_finished_file(self) -> None:
        """Order matters: exists, size, checksum, then the record."""
        local = build_part(self.plate())
        path = self.tmp / "ordered.igs"
        export_iges(local, path)
        artifact = publish_file_artifact(
            document_hash="0" * 64,
            build_key="1" * 64,
            kind=ArtifactKind.IGES,
            path=path,
        )
        raw = path.read_bytes()
        self.assertEqual(artifact.size_bytes, len(raw))
        self.assertEqual(artifact.checksum, hashlib.sha256(raw).hexdigest())

    def test_an_unpublishable_file_fails_the_build_and_is_cleaned_up(self) -> None:
        """A file that cannot be published never enters the manifest.

        The STL is truncated behind the exporter's back, so publication
        refuses it: the build fails, the manifest is empty, and both files
        this build wrote are gone.
        """
        request = self.request(ArtifactKind.STEP, ArtifactKind.STL)
        step_path = self.tmp / f"{request.build_key}.step"
        stl_path = self.tmp / f"{request.build_key}.stl"

        real_export = export_stl

        def truncating_export(result: Any, path: Any, **kwargs: Any) -> Any:
            written = real_export(result, path, **kwargs)
            data = Path(written).read_bytes()
            Path(written).write_bytes(data[:-50])
            return written

        with unittest.mock.patch.dict(
            "cad_core.build_job._EXPORTERS",
            {ArtifactKind.STL: truncating_export},
        ):
            job = execute_build(request, output_directory=self.tmp)

        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertEqual(job.result.manifest.artifacts, ())
        self.assertIs(job.result.error.output, ArtifactKind.STL)
        self.assertEqual(job.result.error.exception_type, "ArtifactPublicationError")
        self.assertFalse(step_path.exists())
        self.assertFalse(stl_path.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_a_failed_artifact_never_enters_the_manifest(self) -> None:
        request = self.request(ArtifactKind.STEP, ArtifactKind.STL)
        (self.tmp / f"{request.build_key}.stl").mkdir()
        job = execute_build(request, output_directory=self.tmp)
        assert job.result is not None
        self.assertEqual(job.result.manifest.artifacts, ())
        self.assertIsNone(job.result.manifest.artifact(ArtifactKind.STEP))
        self.assertEqual(
            job.result.error.completed_outputs, (ArtifactKind.STEP,)
        )

    def test_stage_16_cleanup_behaviour_is_unchanged(self) -> None:
        request = self.request(ArtifactKind.STEP, ArtifactKind.STL)
        step_path = self.tmp / f"{request.build_key}.step"
        blocker = self.tmp / f"{request.build_key}.stl"
        blocker.mkdir()
        job = execute_build(request, output_directory=self.tmp)
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertFalse(step_path.exists())
        self.assertTrue(blocker.is_dir())
        self.assertEqual([p.name for p in self.tmp.iterdir()], [blocker.name])

    def test_a_geometry_artifact_needs_a_real_local_result(self) -> None:
        for bad in (None, "solid", {"shape": 1}):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(ArtifactError):
                    publish_geometry_artifact(
                        document_hash="0" * 64, build_key="1" * 64, result=bad
                    )

    def test_a_render_artifact_needs_a_real_render_model(self) -> None:
        for bad in (None, "model", {"vertices": []}):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(ArtifactError):
                    publish_render_artifact(
                        document_hash="0" * 64, build_key="1" * 64, model=bad
                    )


# --- package boundary -------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1] / "src" / "cad_core"

    def imported_modules(self, filename: str) -> set:
        tree = ast.parse((self.ROOT / filename).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_the_artifact_layer_depends_only_on_local_result_types(self) -> None:
        imported = self.imported_modules("artifact_registry.py")
        self.assertEqual(
            {name for name in imported if name.startswith("cad_core")},
            {
                "cad_core.iges_export",
                "cad_core.local_cad",
                "cad_core.render_model",
                "cad_core.serialization",
                "cad_core.step_export",
                "cad_core.stl_export",
            },
        )

    def test_the_artifact_layer_does_not_depend_on_the_build_layer(self) -> None:
        """One direction: the build layer orchestrates, the artifact layer models."""
        self.assertNotIn(
            "cad_core.build_job", self.imported_modules("artifact_registry.py")
        )
        self.assertIn(
            "cad_core.artifact_registry", self.imported_modules("build_job.py")
        )

    def test_no_storage_service_database_or_network_dependency(self) -> None:
        imported = self.imported_modules("artifact_registry.py")
        for forbidden in (
            "sqlite3",
            "redis",
            "celery",
            "boto3",
            "botocore",
            "google.cloud",
            "azure",
            "requests",
            "httpx",
            "urllib",
            "socket",
            "http",
            "flask",
            "fastapi",
            "django",
            "sqlalchemy",
            "asyncio",
            "multiprocessing",
            "concurrent.futures",
            "subprocess",
            "shutil",
            "tempfile",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(
                        name == forbidden or name.startswith(forbidden + ".")
                        for name in imported
                    ),
                    msg=f"artifact_registry.py imports {forbidden}",
                )

    def test_nothing_upstream_depends_on_the_artifact_layer(self) -> None:
        for module in (
            "model.py",
            "validator.py",
            "errors.py",
            "serialization.py",
            "featurescript.py",
            "onshape_adapter.py",
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
                    "cad_core.artifact_registry", self.imported_modules(module)
                )

    def test_it_is_not_re_exported_from_the_package_root(self) -> None:
        import cad_core

        self.assertFalse(hasattr(cad_core, "ArtifactManifest"))
        self.assertNotIn("ArtifactManifest", getattr(cad_core, "__all__", ()))

    def test_no_filesystem_layout_is_baked_into_the_artifact_layer(self) -> None:
        """It is told a path; it never invents a directory or a filename."""
        source = (self.ROOT / "artifact_registry.py").read_text(encoding="utf-8")
        for forbidden in ("mkdir", "makedirs", "/tmp", "gettempdir", "cwd()", "home()"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
