"""Unit tests for the deterministic local build cache.

Three things these tests are mostly about:

* **the key is the existing build key** -- no second hash, and nothing about a
  path, a clock or an execution id reaches the identity;
* **the manifest is never trusted** -- every hit re-verifies the cached bytes,
  and every way of corrupting an entry produces a MISS rather than a failure
  or a partial result;
* **a hit runs no CAD code** -- the engine, the three exporters and the render
  builder are patched to explode, and a hit still succeeds.

Every cached byte here comes from the real local pipeline. No fake CAD file is
used anywhere.
"""

from __future__ import annotations

import ast
import json
import threading
import unittest
import unittest.mock as mock
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cad_core import validate
from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    ArtifactKind,
    ArtifactManifest,
    ArtifactStorage,
    artifact_logical_id,
    canonical_render_bytes,
    file_checksum,
    render_model_from_canonical_bytes,
)
from cad_core.build_job import (
    BuildOptions,
    BuildOutput,
    BuildRequest,
    BuildStatus,
    execute_build,
)
from cad_core.local_build_cache import (
    CACHE_SCHEMA_VERSION,
    CACHED_PAYLOAD_KINDS,
    ENTRIES_DIRNAME,
    MANIFEST_FILENAME,
    METADATA_ONLY_KINDS,
    PAYLOAD_DIRNAME,
    RENDER_PAYLOAD_EXTENSION,
    STAGING_DIRNAME,
    CacheEntry,
    CacheError,
    CacheLookup,
    CacheMissReason,
    CachePublicationError,
    LocalBuildCache,
    get_or_build,
)
from cad_core.local_cad import GeometryOperationError, build_part
from cad_core.model import Part
from cad_core.render_model import RENDER_FORMAT_VERSION, build_render_model

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


#: The five real geometries the cache is exercised with.
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


def exploding_exporters() -> Any:
    """Patch every exporter with something that fails if it is ever called."""

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("an exporter ran during a cache hit")

    return mock.patch.dict(
        "cad_core.build_job._EXPORTERS",
        {
            BuildOutput.STEP: refuse,
            BuildOutput.IGES: refuse,
            BuildOutput.STL: refuse,
        },
    )


class CacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.cache = LocalBuildCache(self.cache_root)

    # --- fixtures ---------------------------------------------------------

    def part_from(self, doc: Dict[str, Any]) -> Part:
        result = validate(doc)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def plate(self) -> Part:
        return self.part_from(document([plate_feature()]))

    def request(self, *kinds: ArtifactKind, part: Optional[Part] = None) -> BuildRequest:
        return BuildRequest(
            part=part if part is not None else self.plate(),
            options=BuildOptions.for_outputs(*kinds),
        )

    def full_request(self, part: Optional[Part] = None) -> BuildRequest:
        return self.request(*ArtifactKind, part=part)

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir()
        return path

    def other_cache(self, name: str = "other-cache") -> LocalBuildCache:
        return LocalBuildCache(self.subdirectory(name))

    # --- helpers ----------------------------------------------------------

    def fill(self, request: BuildRequest) -> Any:
        """Populate the cache with one real build and return the miss job."""
        job = get_or_build(request, self.cache, output_directory=self.out)
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        assert job.result is not None
        self.assertFalse(job.result.cache_hit)
        return job

    def entry_directory(self, request: BuildRequest) -> Path:
        return self.cache_root / ENTRIES_DIRNAME / request.build_key

    def manifest_path(self, request: BuildRequest) -> Path:
        return self.entry_directory(request) / MANIFEST_FILENAME

    def stored_manifest(self, request: BuildRequest) -> Dict[str, Any]:
        return json.loads(self.manifest_path(request).read_bytes().decode("utf-8"))

    def rewrite_manifest(self, request: BuildRequest, payload: Any) -> None:
        self.manifest_path(request).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def record_for(self, stored: Dict[str, Any], kind: ArtifactKind) -> Dict[str, Any]:
        for record in stored["artifacts"]:
            if record["kind"] == kind.value:
                return record
        raise AssertionError(f"no {kind.value} record")

    def lookup(self, request: BuildRequest) -> CacheLookup:
        return self.cache.lookup(
            request.build_key,
            document_hash=request.document_hash,
            required_kinds=request.options.requested,
        )

    def assert_miss(
        self, request: BuildRequest, reason: CacheMissReason
    ) -> CacheLookup:
        lookup = self.lookup(request)
        self.assertFalse(lookup.hit, msg=f"expected a miss, got {lookup.to_dict()}")
        self.assertIs(lookup.reason, reason)
        self.assertIsNone(lookup.entry)
        return lookup


# --- the cache key ----------------------------------------------------------


class TestCacheKey(CacheTestCase):
    def test_the_cache_key_is_the_existing_build_key(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        self.fill(request)
        entry = self.cache.fetch(request.build_key)
        assert entry is not None
        self.assertEqual(entry.build_key, request.build_key)
        self.assertEqual(
            self.cache.entry_directory(request.build_key).name, request.build_key
        )

    def test_no_second_hash_scheme_is_introduced(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        self.fill(request)
        stored = self.stored_manifest(request)
        # every identity in the entry derives from the build key alone
        self.assertEqual(stored["build_key"], request.build_key)
        self.assertEqual(stored["document_hash"], request.document_hash)
        for record in stored["artifacts"]:
            self.assertEqual(
                record["logical_id"],
                artifact_logical_id(request.build_key, ArtifactKind(record["kind"])),
            )
        self.assertEqual(
            {record["checksum_algorithm"] for record in stored["artifacts"]},
            {CHECKSUM_ALGORITHM, None},
        )

    def test_the_lookup_identity_is_the_build_key_alone(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        self.fill(request)
        # no document hash, no required kinds: the key alone finds the entry
        bare = self.cache.lookup(request.build_key)
        self.assertTrue(bare.hit)
        assert bare.entry is not None
        self.assertEqual(bare.entry.build_key, request.build_key)

    def test_the_cache_root_is_not_part_of_the_identity(self) -> None:
        request = self.full_request()
        first = self.fill(request)
        assert first.result is not None
        elsewhere = self.other_cache()
        second = get_or_build(
            request, elsewhere, output_directory=self.subdirectory("out-2")
        )
        assert second.result is not None
        self.assertFalse(second.result.cache_hit)
        self.assertEqual(first.result.build_key, second.result.build_key)
        self.assertEqual(
            [artifact.logical_id for artifact in first.result.artifacts],
            [artifact.logical_id for artifact in second.result.artifacts],
        )
        hit = get_or_build(
            request, elsewhere, output_directory=self.subdirectory("out-3")
        )
        assert hit.result is not None
        self.assertTrue(hit.result.cache_hit)
        self.assertEqual(hit.result.build_key, request.build_key)
        self.assertNotIn(str(self.cache_root), str(elsewhere.root))

    def test_the_key_never_contains_a_path_or_an_execution_id(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        job = self.fill(request)
        key = request.build_key
        self.assertNotIn("/", key)
        self.assertNotIn(self.tmp.name, key)
        self.assertNotIn(self.out.name, key)
        self.assertNotIn(job.execution_id, key)
        self.assertEqual(len(key), 64)

    def test_a_key_that_is_not_a_plain_name_is_refused(self) -> None:
        for bad in ("", "../escape", "a/b", ".", ".."):
            with self.assertRaises(CacheError):
                self.cache.entry_directory(bad)

    def test_different_output_selections_are_independent_entries(self) -> None:
        stl_only = self.request(BuildOutput.STL)
        both = self.request(BuildOutput.STL, BuildOutput.STEP)
        self.assertNotEqual(stl_only.build_key, both.build_key)
        self.fill(stl_only)
        # the narrower entry is not a hit for the wider request
        self.assertFalse(self.lookup(both).hit)
        self.fill(both)
        self.assertTrue(self.lookup(stl_only).hit)
        self.assertTrue(self.lookup(both).hit)
        self.assertEqual(
            sorted(path.name for path in (self.cache_root / ENTRIES_DIRNAME).iterdir()),
            sorted([stl_only.build_key, both.build_key]),
        )

    def test_different_documents_are_independent_entries(self) -> None:
        first = self.request(BuildOutput.STL)
        other = self.request(
            BuildOutput.STL,
            part=self.part_from(
                document([plate_feature(size={"x": 40, "y": 40, "z": 5})], name="small")
            ),
        )
        self.assertNotEqual(first.build_key, other.build_key)
        self.fill(first)
        self.fill(other)
        self.assertTrue(self.lookup(first).hit)
        self.assertTrue(self.lookup(other).hit)
        first_entry = self.cache.fetch(first.build_key)
        other_entry = self.cache.fetch(other.build_key)
        assert first_entry is not None and other_entry is not None
        self.assertNotEqual(
            first_entry.artifact(ArtifactKind.STL).checksum,
            other_entry.artifact(ArtifactKind.STL).checksum,
        )


# --- miss, publication, hit -------------------------------------------------


class TestFirstRequestMisses(CacheTestCase):
    def test_an_empty_cache_misses(self) -> None:
        request = self.full_request()
        self.assert_miss(request, CacheMissReason.NO_ENTRY)
        self.assertFalse(self.cache.contains(request.build_key))
        self.assertIsNone(self.cache.fetch(request.build_key))

    def test_a_first_get_or_build_runs_the_engine(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        with mock.patch(
            "cad_core.build_job.build_part", wraps=build_part
        ) as engine:
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertEqual(engine.call_count, 1)
        self.assertFalse(job.result.cache_hit)

    def test_a_cache_root_must_exist_and_is_never_invented(self) -> None:
        with self.assertRaises(CacheError):
            LocalBuildCache(self.tmp / "missing")
        with self.assertRaises(CacheError):
            LocalBuildCache(self.manifest_path(self.request(BuildOutput.STL)))


class TestPublication(CacheTestCase):
    def test_a_successful_build_creates_an_entry(self) -> None:
        request = self.full_request()
        self.fill(request)
        entry_directory = self.entry_directory(request)
        self.assertTrue(entry_directory.is_dir())
        self.assertTrue((entry_directory / MANIFEST_FILENAME).is_file())
        self.assertTrue(self.cache.contains(request.build_key))

    def test_the_layout_is_the_documented_one(self) -> None:
        request = self.full_request()
        self.fill(request)
        entry = self.entry_directory(request)
        payloads = sorted(
            path.name for path in (entry / PAYLOAD_DIRNAME).iterdir()
        )
        self.assertEqual(
            payloads, ["iges.igs", "render.json", "step.step", "stl.stl"]
        )
        self.assertEqual(
            sorted(path.name for path in entry.iterdir()),
            [PAYLOAD_DIRNAME, MANIFEST_FILENAME],
        )
        self.assertEqual(
            sorted(path.name for path in self.cache_root.iterdir()),
            [ENTRIES_DIRNAME, STAGING_DIRNAME],
        )

    def test_no_geometry_payload_is_written(self) -> None:
        request = self.full_request()
        self.fill(request)
        entry = self.entry_directory(request)
        for path in (entry / PAYLOAD_DIRNAME).iterdir():
            self.assertNotIn("geometry", path.name)
        self.assertEqual(METADATA_ONLY_KINDS, (ArtifactKind.GEOMETRY,))
        self.assertEqual(
            CACHED_PAYLOAD_KINDS,
            (
                ArtifactKind.STEP,
                ArtifactKind.IGES,
                ArtifactKind.STL,
                ArtifactKind.RENDER,
            ),
        )

    def test_the_stored_manifest_holds_no_absolute_path(self) -> None:
        request = self.full_request()
        self.fill(request)
        text = self.manifest_path(request).read_text(encoding="utf-8")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(str(self.out), text)
        self.assertNotIn(self.tmp.name, text)
        for record in self.stored_manifest(request)["artifacts"]:
            relative = record["payload_path"]
            if relative is None:
                continue
            self.assertFalse(Path(relative).is_absolute())
            self.assertEqual(Path(relative).parts[0], PAYLOAD_DIRNAME)

    def test_the_stored_manifest_holds_no_execution_id_or_traceback(self) -> None:
        request = self.full_request()
        job = self.fill(request)
        text = self.manifest_path(request).read_text(encoding="utf-8")
        self.assertNotIn(job.execution_id, text)
        self.assertNotIn("Traceback", text)
        for token in ("cadquery", "OCP", "TopoDS", "Workplane", "object at"):
            self.assertNotIn(token, text)

    def test_the_stored_manifest_holds_the_documented_fields(self) -> None:
        request = self.full_request()
        self.fill(request)
        stored = self.stored_manifest(request)
        self.assertEqual(
            sorted(stored),
            ["artifacts", "build_key", "cache_schema_version", "document_hash"],
        )
        self.assertEqual(stored["cache_schema_version"], CACHE_SCHEMA_VERSION)
        for record in stored["artifacts"]:
            self.assertEqual(
                sorted(record),
                [
                    "checksum",
                    "checksum_algorithm",
                    "details",
                    "file_extension",
                    "format",
                    "kind",
                    "logical_id",
                    "payload_path",
                    "size_bytes",
                    "storage",
                ],
            )

    def test_the_recorded_sizes_and_checksums_are_the_cached_bytes(self) -> None:
        request = self.full_request()
        self.fill(request)
        entry_directory = self.entry_directory(request)
        for record in self.stored_manifest(request)["artifacts"]:
            if record["payload_path"] is None:
                self.assertEqual(record["kind"], ArtifactKind.GEOMETRY.value)
                continue
            payload = entry_directory / record["payload_path"]
            self.assertEqual(record["size_bytes"], len(payload.read_bytes()))
            self.assertEqual(record["checksum"], file_checksum(payload))

    def test_publishing_a_failed_result_is_refused(self) -> None:
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=RuntimeError("kernel exploded"),
        ):
            job = execute_build(
                self.request(BuildOutput.STEP, BuildOutput.RENDER),
                output_directory=self.out,
            )
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        with self.assertRaises(CachePublicationError):
            self.cache.publish(job.result)
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_a_failed_build_creates_no_entry(self) -> None:
        request = self.request(BuildOutput.STEP, BuildOutput.RENDER)
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=RuntimeError("kernel exploded"),
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertEqual(job.result.artifacts, ())
        self.assertFalse(self.cache.contains(request.build_key))
        self.assertFalse(self.entry_directory(request).exists())
        # ... and the next request is still an honest miss
        self.assert_miss(request, CacheMissReason.NO_ENTRY)

    def test_an_invalid_document_never_reaches_the_cache(self) -> None:
        from cad_core.build_job import request_for_document
        from cad_core.serialization import DocumentValidationError

        invalid = document([plate_feature(size={"x": 0, "y": 60, "z": 10})])
        with self.assertRaises(DocumentValidationError):
            request_for_document(invalid, BuildOptions.for_outputs(BuildOutput.STL))
        # there is no request, so there is no build key and nothing to cache
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())
        self.assertEqual(list(self.cache_root.iterdir()), [])

    def test_a_document_that_fails_geometry_creates_no_entry(self) -> None:
        request = self.request(BuildOutput.STL)
        with mock.patch(
            "cad_core.build_job.build_part",
            side_effect=GeometryOperationError(
                "the operation failed (rule E5)"
            ),
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.FAILED)
        self.assertFalse(self.cache.contains(request.build_key))
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_the_final_entry_appears_only_after_the_manifest_is_written(
        self,
    ) -> None:
        request = self.full_request()
        job = execute_build(request, output_directory=self.out)
        assert job.result is not None
        with mock.patch(
            "cad_core.local_build_cache._canonical_json",
            side_effect=RuntimeError("manifest not written"),
        ):
            with self.assertRaises(RuntimeError):
                self.cache.publish(job.result)
        # payloads were copied, but no entry exists and no staging survives
        self.assertFalse(self.entry_directory(request).exists())
        self.assertEqual(
            list((self.cache_root / STAGING_DIRNAME).iterdir()), []
        )
        self.assert_miss(request, CacheMissReason.NO_ENTRY)
        # ... and a clean publication afterwards works
        self.cache.publish(job.result)
        self.assertTrue(self.lookup(request).hit)

    def test_a_partially_staged_entry_is_invisible_as_a_hit(self) -> None:
        request = self.full_request()
        job = execute_build(request, output_directory=self.out)
        assert job.result is not None
        staging = self.cache_root / STAGING_DIRNAME / f"{request.build_key}.abc"
        (staging / PAYLOAD_DIRNAME).mkdir(parents=True)
        # a complete-looking manifest, but under staging/ rather than entries/
        records = self.cache._stage(staging, job.result)
        (staging / MANIFEST_FILENAME).write_bytes(
            json.dumps(
                {
                    "cache_schema_version": CACHE_SCHEMA_VERSION,
                    "document_hash": request.document_hash,
                    "build_key": request.build_key,
                    "artifacts": records,
                }
            ).encode("utf-8")
        )
        self.assert_miss(request, CacheMissReason.NO_ENTRY)
        self.assertFalse(self.cache.contains(request.build_key))


class TestCacheHit(CacheTestCase):
    def test_a_second_identical_request_is_a_hit(self) -> None:
        request = self.full_request()
        self.fill(request)
        job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertTrue(job.result.cache_hit)
        self.assertIsNone(job.result.error)

    def test_a_hit_does_not_invoke_the_geometry_engine(self) -> None:
        request = self.full_request()
        self.fill(request)
        with mock.patch(
            "cad_core.build_job.build_part",
            side_effect=AssertionError("build_part ran during a cache hit"),
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)

    def test_a_hit_does_not_invoke_any_exporter(self) -> None:
        request = self.full_request()
        self.fill(request)
        with exploding_exporters():
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertEqual(
            set(job.result.manifest.kinds()), set(ArtifactKind)
        )

    def test_a_hit_does_not_invoke_the_step_exporter(self) -> None:
        request = self.request(BuildOutput.STEP)
        self.fill(request)
        with mock.patch.dict(
            "cad_core.build_job._EXPORTERS",
            {BuildOutput.STEP: mock.Mock(side_effect=AssertionError("step ran"))},
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertIsNotNone(job.result.artifact(ArtifactKind.STEP))

    def test_a_hit_does_not_invoke_the_iges_exporter(self) -> None:
        request = self.request(BuildOutput.IGES)
        self.fill(request)
        with mock.patch.dict(
            "cad_core.build_job._EXPORTERS",
            {BuildOutput.IGES: mock.Mock(side_effect=AssertionError("iges ran"))},
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertIsNotNone(job.result.artifact(ArtifactKind.IGES))

    def test_a_hit_does_not_invoke_the_stl_exporter(self) -> None:
        request = self.request(BuildOutput.STL)
        self.fill(request)
        with mock.patch.dict(
            "cad_core.build_job._EXPORTERS",
            {BuildOutput.STL: mock.Mock(side_effect=AssertionError("stl ran"))},
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertIsNotNone(job.result.artifact(ArtifactKind.STL))

    def test_a_hit_does_not_regenerate_the_render_model(self) -> None:
        request = self.request(BuildOutput.RENDER)
        first = self.fill(request)
        assert first.result is not None
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=AssertionError("render generation ran"),
        ):
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertIsNotNone(job.result.render_model)
        self.assertEqual(job.result.render_model, first.result.render_model)

    def test_a_hit_writes_nothing_to_the_output_directory(self) -> None:
        request = self.full_request()
        self.fill(request)
        for path in self.out.iterdir():
            path.unlink()
        get_or_build(request, self.cache, output_directory=self.out)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_a_hit_needs_no_output_directory_at_all(self) -> None:
        request = self.full_request()
        self.fill(request)
        job = get_or_build(request, self.cache)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)

    def test_the_build_key_is_unchanged_between_miss_and_hit(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        hit = get_or_build(request, self.cache, output_directory=self.out)
        assert miss.result is not None and hit.result is not None
        self.assertEqual(miss.result.build_key, hit.result.build_key)
        self.assertEqual(hit.result.build_key, request.build_key)
        self.assertEqual(miss.result.document_hash, hit.result.document_hash)

    def test_a_hit_gets_a_fresh_execution_id_that_is_not_an_identity(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        hit = get_or_build(request, self.cache, output_directory=self.out)
        assert miss.result is not None and hit.result is not None
        self.assertNotEqual(miss.execution_id, hit.execution_id)
        self.assertEqual(hit.result.execution_id, hit.execution_id)
        # ... and none of it reaches any identity
        self.assertEqual(miss.result.build_key, hit.result.build_key)
        for artifact in hit.result.artifacts:
            self.assertNotIn(hit.execution_id, artifact.logical_id)
            self.assertNotIn(miss.execution_id, artifact.logical_id)
        self.assertNotIn(
            hit.execution_id,
            self.manifest_path(request).read_text(encoding="utf-8"),
        )

    def test_the_logical_ids_are_unchanged(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        hit = get_or_build(request, self.cache, output_directory=self.out)
        assert miss.result is not None and hit.result is not None
        self.assertEqual(
            [artifact.logical_id for artifact in miss.result.artifacts],
            [artifact.logical_id for artifact in hit.result.artifacts],
        )
        for artifact in hit.result.artifacts:
            self.assertEqual(
                artifact.logical_id,
                artifact_logical_id(request.build_key, artifact.kind),
            )

    def test_the_returned_paths_point_inside_the_cache_root(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        hit = get_or_build(request, self.cache, output_directory=self.out)
        assert miss.result is not None and hit.result is not None
        for artifact in hit.result.artifacts:
            if artifact.storage is ArtifactStorage.IN_MEMORY:
                self.assertIsNone(artifact.path)
                continue
            path = Path(artifact.path)
            self.assertTrue(path.is_file())
            path.relative_to(self.cache_root)  # raises if it escapes
            self.assertNotIn(str(self.out), str(path))
        # the original build's paths are not what a hit reports
        for kind in (ArtifactKind.STEP, ArtifactKind.IGES, ArtifactKind.STL):
            self.assertNotEqual(
                miss.result.artifact(kind).path, hit.result.artifact(kind).path
            )

    def test_a_hit_keeps_the_succeeded_status_and_adds_no_new_one(self) -> None:
        request = self.request(BuildOutput.STL)
        self.fill(request)
        job = get_or_build(request, self.cache, output_directory=self.out)
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertEqual(
            [status.value for status in BuildStatus],
            ["queued", "running", "succeeded", "failed"],
        )
        assert job.result is not None
        self.assertTrue(job.result.succeeded)
        self.assertTrue(job.result.cache_hit)
        self.assertIn("cache_hit", job.result.to_dict())
        self.assertTrue(job.result.to_dict()["cache_hit"])

    def test_a_built_result_is_not_marked_a_cache_hit(self) -> None:
        request = self.request(BuildOutput.STL)
        job = self.fill(request)
        assert job.result is not None
        self.assertFalse(job.result.cache_hit)
        self.assertFalse(job.result.to_dict()["cache_hit"])
        direct = execute_build(request, output_directory=self.out)
        assert direct.result is not None
        self.assertFalse(direct.result.cache_hit)

    def test_the_hit_result_carries_the_full_manifest_metadata(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        hit = get_or_build(request, self.cache, output_directory=self.out)
        assert miss.result is not None and hit.result is not None
        self.assertIsInstance(hit.result.manifest, ArtifactManifest)
        self.assertEqual(
            hit.result.manifest.kinds(), miss.result.manifest.kinds()
        )
        self.assertEqual(
            hit.result.manifest.canonical_bytes(),
            miss.result.manifest.canonical_bytes(),
        )
        self.assertEqual(
            hit.result.manifest.canonical_hash(),
            miss.result.manifest.canonical_hash(),
        )
        for kind in ArtifactKind:
            built = miss.result.artifact(kind)
            cached = hit.result.artifact(kind)
            self.assertEqual(built.format, cached.format)
            self.assertEqual(built.storage, cached.storage)
            self.assertEqual(built.file_extension, cached.file_extension)
            self.assertEqual(built.size_bytes, cached.size_bytes)
            self.assertEqual(built.checksum, cached.checksum)
            self.assertEqual(dict(built.details), dict(cached.details))

    def test_the_hit_result_serializes_to_plain_data(self) -> None:
        request = self.full_request()
        self.fill(request)
        job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        payload = json.loads(json.dumps(job.result.to_dict()))
        self.assertTrue(payload["cache_hit"])
        for token in ("cadquery", "OCP", "TopoDS", "Workplane", "object at"):
            self.assertNotIn(token, json.dumps(payload))


# --- what a hit restores ----------------------------------------------------


class TestCachedBytes(CacheTestCase):
    def test_a_step_hit_returns_the_cached_bytes_exactly(self) -> None:
        self.assert_cached_bytes(ArtifactKind.STEP)

    def test_an_iges_hit_returns_the_cached_bytes_exactly(self) -> None:
        self.assert_cached_bytes(ArtifactKind.IGES)

    def test_an_stl_hit_returns_the_cached_bytes_exactly(self) -> None:
        self.assert_cached_bytes(ArtifactKind.STL)

    def assert_cached_bytes(self, kind: ArtifactKind) -> None:
        request = self.request(kind)
        miss = self.fill(request)
        assert miss.result is not None
        original = Path(miss.result.artifact(kind).path).read_bytes()
        original_checksum = miss.result.artifact(kind).checksum
        with exploding_exporters():
            hit = get_or_build(request, self.cache, output_directory=self.out)
        assert hit.result is not None
        cached = hit.result.artifact(kind)
        self.assertEqual(Path(cached.path).read_bytes(), original)
        self.assertEqual(cached.checksum, original_checksum)
        self.assertEqual(cached.size_bytes, len(original))
        self.assertEqual(file_checksum(cached.path), original_checksum)

    def test_a_hit_verifies_the_recorded_size_against_the_bytes(self) -> None:
        request = self.request(BuildOutput.STL)
        self.fill(request)
        entry = self.cache.fetch(request.build_key)
        assert entry is not None
        artifact = entry.artifact(ArtifactKind.STL)
        self.assertEqual(artifact.size_bytes, len(Path(artifact.path).read_bytes()))
        self.assertEqual(
            artifact.size_bytes, Path(artifact.path).stat().st_size
        )

    def test_a_hit_verifies_the_recorded_checksum_against_the_bytes(self) -> None:
        request = self.request(BuildOutput.STEP)
        self.fill(request)
        entry = self.cache.fetch(request.build_key)
        assert entry is not None
        artifact = entry.artifact(ArtifactKind.STEP)
        self.assertEqual(artifact.checksum, file_checksum(artifact.path))
        stored = self.record_for(self.stored_manifest(request), ArtifactKind.STEP)
        self.assertEqual(stored["checksum"], artifact.checksum)

    def test_the_render_model_is_cached_as_its_canonical_json(self) -> None:
        request = self.request(BuildOutput.RENDER)
        miss = self.fill(request)
        assert miss.result is not None and miss.result.render_model is not None
        payload = (
            self.entry_directory(request) / PAYLOAD_DIRNAME
            / f"render{RENDER_PAYLOAD_EXTENSION}"
        )
        self.assertTrue(payload.is_file())
        self.assertEqual(
            payload.read_bytes(), canonical_render_bytes(miss.result.render_model)
        )
        stored = self.record_for(self.stored_manifest(request), ArtifactKind.RENDER)
        self.assertEqual(stored["checksum"], file_checksum(payload))
        self.assertEqual(stored["size_bytes"], len(payload.read_bytes()))
        # the render artifact stays an in-memory artifact despite the payload
        self.assertEqual(stored["storage"], ArtifactStorage.IN_MEMORY.value)
        self.assertIsNone(stored["file_extension"])

    def test_the_render_model_is_reconstructed_from_the_cached_bytes(self) -> None:
        request = self.request(BuildOutput.RENDER)
        miss = self.fill(request)
        assert miss.result is not None
        with mock.patch(
            "cad_core.build_job.build_render_model",
            side_effect=AssertionError("render generation ran"),
        ):
            hit = get_or_build(request, self.cache, output_directory=self.out)
        assert hit.result is not None
        restored = hit.result.render_model
        original = miss.result.render_model
        assert restored is not None and original is not None
        self.assertEqual(restored, original)
        self.assertEqual(restored.to_dict(), original.to_dict())
        self.assertEqual(
            canonical_render_bytes(restored), canonical_render_bytes(original)
        )
        self.assertEqual(restored.vertices, original.vertices)
        self.assertEqual(restored.triangles, original.triangles)
        self.assertEqual(restored.normals, original.normals)
        self.assertEqual(restored.bounds, original.bounds)
        self.assertEqual(restored.tessellation, original.tessellation)
        self.assertIs(type(restored.vertices), tuple)

    def test_the_render_payload_round_trips_for_every_geometry(self) -> None:
        for name, doc in geometry_corpus():
            with self.subTest(geometry=name):
                part = self.part_from(doc)
                model = build_render_model(build_part(part))
                payload = canonical_render_bytes(model)
                restored = render_model_from_canonical_bytes(payload)
                self.assertEqual(restored, model)
                self.assertEqual(canonical_render_bytes(restored), payload)

    def test_a_render_payload_of_another_format_version_is_refused(self) -> None:
        request = self.request(BuildOutput.RENDER)
        miss = self.fill(request)
        assert miss.result is not None and miss.result.render_model is not None
        raw = json.loads(canonical_render_bytes(miss.result.render_model))
        raw["format_version"] = "9.9.9"
        with self.assertRaises(Exception) as caught:
            render_model_from_canonical_bytes(json.dumps(raw).encode("utf-8"))
        self.assertIn("format version", str(caught.exception))
        self.assertEqual(RENDER_FORMAT_VERSION, "1.0.0")

    def test_a_render_payload_with_an_unknown_field_is_refused(self) -> None:
        request = self.request(BuildOutput.RENDER)
        miss = self.fill(request)
        assert miss.result is not None and miss.result.render_model is not None
        raw = json.loads(canonical_render_bytes(miss.result.render_model))
        raw["surprise"] = 1
        with self.assertRaises(Exception):
            render_model_from_canonical_bytes(json.dumps(raw).encode("utf-8"))
        raw.pop("surprise")
        raw.pop("normals")
        with self.assertRaises(Exception):
            render_model_from_canonical_bytes(json.dumps(raw).encode("utf-8"))

    def test_geometry_is_not_cached_and_is_absent_on_a_hit(self) -> None:
        request = self.full_request()
        miss = self.fill(request)
        assert miss.result is not None
        self.assertIsNotNone(miss.result.geometry)
        with mock.patch(
            "cad_core.build_job.build_part",
            side_effect=AssertionError("build_part ran during a cache hit"),
        ):
            hit = get_or_build(request, self.cache, output_directory=self.out)
        assert hit.result is not None
        # the documented choice: no B-rep is serialized, so none is restored
        self.assertIsNone(hit.result.geometry)
        entry = self.cache.fetch(request.build_key)
        assert entry is not None
        self.assertFalse(entry.restores_geometry)
        # ... but the geometry artifact's measurements survive intact
        geometry = hit.result.artifact(ArtifactKind.GEOMETRY)
        self.assertIsNotNone(geometry)
        self.assertIsNone(geometry.checksum)
        self.assertIsNone(geometry.size_bytes)
        self.assertIsNone(geometry.path)
        self.assertEqual(
            dict(geometry.details),
            dict(miss.result.artifact(ArtifactKind.GEOMETRY).details),
        )
        self.assertAlmostEqual(
            geometry.details["volume_mm3"],
            PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2],
            places=6,
        )

    def test_every_real_geometry_round_trips_through_the_cache(self) -> None:
        for index, (name, doc) in enumerate(geometry_corpus()):
            with self.subTest(geometry=name):
                request = self.full_request(part=self.part_from(doc))
                out = self.subdirectory(f"out-{index}")
                miss = get_or_build(request, self.cache, output_directory=out)
                assert miss.result is not None
                self.assertFalse(miss.result.cache_hit)
                originals = {
                    kind: Path(miss.result.artifact(kind).path).read_bytes()
                    for kind in (
                        ArtifactKind.STEP,
                        ArtifactKind.IGES,
                        ArtifactKind.STL,
                    )
                }
                with exploding_exporters():
                    hit = get_or_build(request, self.cache, output_directory=out)
                assert hit.result is not None
                self.assertTrue(hit.result.cache_hit)
                for kind, payload in originals.items():
                    self.assertEqual(
                        Path(hit.result.artifact(kind).path).read_bytes(), payload
                    )
                self.assertEqual(
                    hit.result.manifest.canonical_bytes(),
                    miss.result.manifest.canonical_bytes(),
                )
                self.assertEqual(
                    hit.result.render_model, miss.result.render_model
                )


# --- corruption -------------------------------------------------------------


class TestCorruption(CacheTestCase):
    def prepared(self) -> BuildRequest:
        request = self.full_request()
        self.fill(request)
        return request

    def test_a_missing_manifest_is_a_miss(self) -> None:
        request = self.prepared()
        self.manifest_path(request).unlink()
        self.assert_miss(request, CacheMissReason.NO_MANIFEST)

    def test_a_malformed_manifest_is_a_miss(self) -> None:
        request = self.prepared()
        self.manifest_path(request).write_text("{not json", encoding="utf-8")
        self.assert_miss(request, CacheMissReason.MANIFEST_UNREADABLE)

    def test_a_manifest_that_is_not_an_object_is_a_miss(self) -> None:
        request = self.prepared()
        self.manifest_path(request).write_text("[]", encoding="utf-8")
        self.assert_miss(request, CacheMissReason.MANIFEST_UNREADABLE)

    def test_a_manifest_with_unknown_fields_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["surprise"] = True
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_UNREADABLE)

    def test_a_manifest_of_another_cache_layout_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["cache_schema_version"] = "0.0.1"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.SCHEMA_MISMATCH)

    def test_a_wrong_build_key_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["build_key"] = "0" * 64
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.BUILD_KEY_MISMATCH)

    def test_a_wrong_document_hash_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["document_hash"] = "1" * 64
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.DOCUMENT_MISMATCH)

    def test_a_manifest_naming_another_cache_entry_is_a_miss(self) -> None:
        request = self.prepared()
        other = self.request(BuildOutput.STL)
        self.fill(other)
        stored = self.stored_manifest(request)
        # the artifact records now claim to belong to the other entry
        for record in stored["artifacts"]:
            record["logical_id"] = artifact_logical_id(
                other.build_key, ArtifactKind(record["kind"])
            )
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)
        # ... and the other entry is untouched
        self.assertTrue(self.lookup(other).hit)

    def test_a_payload_path_pointing_outside_the_entry_is_a_miss(self) -> None:
        request = self.prepared()
        other = self.request(BuildOutput.STL)
        self.fill(other)
        stored = self.stored_manifest(request)
        record = self.record_for(stored, ArtifactKind.STL)
        record["payload_path"] = f"../{other.build_key}/{PAYLOAD_DIRNAME}/stl.stl"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_an_absolute_payload_path_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        record = self.record_for(stored, ArtifactKind.STL)
        record["payload_path"] = str(
            self.entry_directory(request) / PAYLOAD_DIRNAME / "stl.stl"
        )
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_duplicated_artifact_kind_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["artifacts"].append(self.record_for(stored, ArtifactKind.STL))
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_an_artifact_record_with_unknown_fields_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["surprise"] = 1
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_an_unknown_artifact_kind_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["kind"] = "hologram"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_wrong_recorded_format_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["format"] = "stl-ascii"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_wrong_recorded_storage_kind_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.RENDER)["storage"] = "file"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_geometry_record_claiming_a_payload_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        record = self.record_for(stored, ArtifactKind.GEOMETRY)
        record["payload_path"] = f"{PAYLOAD_DIRNAME}/geometry.brep"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_geometry_record_claiming_a_checksum_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        record = self.record_for(stored, ArtifactKind.GEOMETRY)
        record["checksum"] = "2" * 64
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_missing_payload_file_is_a_miss(self) -> None:
        request = self.prepared()
        (self.entry_directory(request) / PAYLOAD_DIRNAME / "stl.stl").unlink()
        self.assert_miss(request, CacheMissReason.PAYLOAD_MISSING)

    def test_a_missing_render_payload_is_a_miss(self) -> None:
        request = self.prepared()
        (
            self.entry_directory(request)
            / PAYLOAD_DIRNAME
            / f"render{RENDER_PAYLOAD_EXTENSION}"
        ).unlink()
        self.assert_miss(request, CacheMissReason.PAYLOAD_MISSING)

    def test_a_truncated_payload_is_a_miss(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "step.step"
        payload.write_bytes(payload.read_bytes()[:-64])
        self.assert_miss(request, CacheMissReason.SIZE_MISMATCH)

    def test_an_emptied_payload_is_a_miss(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "step.step"
        payload.write_bytes(b"")
        self.assert_miss(request, CacheMissReason.SIZE_MISMATCH)

    def test_corrupted_payload_bytes_of_the_recorded_length_are_a_miss(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "step.step"
        original = payload.read_bytes()
        flipped = bytearray(original)
        flipped[len(flipped) // 2] ^= 0xFF
        payload.write_bytes(bytes(flipped))
        self.assertEqual(len(original), payload.stat().st_size)
        self.assert_miss(request, CacheMissReason.CHECKSUM_MISMATCH)

    def test_a_corrupted_render_payload_is_a_miss(self) -> None:
        request = self.prepared()
        payload = (
            self.entry_directory(request)
            / PAYLOAD_DIRNAME
            / f"render{RENDER_PAYLOAD_EXTENSION}"
        )
        raw = json.loads(payload.read_bytes())
        raw["part_name"] = "tampered"
        # same length is unlikely; the checksum is what catches it either way
        payload.write_bytes(json.dumps(raw).encode("utf-8"))
        lookup = self.lookup(request)
        self.assertFalse(lookup.hit)
        self.assertIn(
            lookup.reason,
            (CacheMissReason.SIZE_MISMATCH, CacheMissReason.CHECKSUM_MISMATCH),
        )

    def test_a_wrong_recorded_size_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["size_bytes"] = 1
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.SIZE_MISMATCH)

    def test_a_wrong_recorded_checksum_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["checksum"] = "3" * 64
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.CHECKSUM_MISMATCH)

    def test_a_wrong_recorded_checksum_algorithm_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STL)["checksum_algorithm"] = "md5"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_wrong_recorded_extension_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        self.record_for(stored, ArtifactKind.STEP)["file_extension"] = ".stp"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_a_payload_with_an_extension_its_kind_does_not_use_is_a_miss(
        self,
    ) -> None:
        request = self.prepared()
        entry = self.entry_directory(request)
        source = entry / PAYLOAD_DIRNAME / "step.step"
        renamed = entry / PAYLOAD_DIRNAME / "step.txt"
        source.rename(renamed)
        stored = self.stored_manifest(request)
        record = self.record_for(stored, ArtifactKind.STEP)
        record["payload_path"] = f"{PAYLOAD_DIRNAME}/step.txt"
        record["file_extension"] = ".txt"
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.PAYLOAD_MISSING)

    def test_a_missing_required_output_is_a_miss(self) -> None:
        narrow = self.request(BuildOutput.STL)
        self.fill(narrow)
        # ask the narrow entry for an output it does not hold, by build key
        lookup = self.cache.lookup(
            narrow.build_key,
            document_hash=narrow.document_hash,
            required_kinds=(ArtifactKind.STL, ArtifactKind.STEP),
        )
        self.assertFalse(lookup.hit)
        self.assertIs(lookup.reason, CacheMissReason.OUTPUT_MISSING)

    def test_a_manifest_dropping_an_artifact_is_a_miss_for_that_request(
        self,
    ) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["artifacts"] = [
            record
            for record in stored["artifacts"]
            if record["kind"] != ArtifactKind.STL.value
        ]
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.OUTPUT_MISSING)

    def test_a_manifest_with_no_artifacts_is_a_miss(self) -> None:
        request = self.prepared()
        stored = self.stored_manifest(request)
        stored["artifacts"] = []
        self.rewrite_manifest(request, stored)
        self.assert_miss(request, CacheMissReason.MANIFEST_INCONSISTENT)

    def test_an_unmanifested_extra_file_creates_no_artifact(self) -> None:
        request = self.prepared()
        stray = (
            self.entry_directory(request) / PAYLOAD_DIRNAME / "extra.step"
        )
        stray.write_bytes(b"ISO-10303-21;\nENDSEC;\nEND-ISO-10303-21;\n")
        lookup = self.lookup(request)
        self.assertTrue(lookup.hit)
        assert lookup.entry is not None
        # the manifest is the authority on what exists
        self.assertEqual(set(lookup.entry.kinds()), set(ArtifactKind))
        for artifact in lookup.entry.manifest.artifacts:
            if artifact.path is not None:
                self.assertNotEqual(Path(artifact.path).name, "extra.step")
        job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertEqual(len(job.result.artifacts), len(ArtifactKind))

    def test_a_corrupted_entry_never_reports_success(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "step.step"
        payload.write_bytes(b"garbage")
        self.assertFalse(self.cache.contains(request.build_key))
        # a corrupt entry is a miss, so the build runs again and succeeds
        job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertIs(job.status, BuildStatus.SUCCEEDED)
        self.assertFalse(job.result.cache_hit)
        self.assertIsNone(job.result.error)

    def test_a_fresh_build_replaces_a_corrupted_entry(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "step.step"
        payload.write_bytes(b"garbage")
        self.assertFalse(self.cache.contains(request.build_key))
        get_or_build(request, self.cache, output_directory=self.out)
        # the bad entry is gone, replaced by a complete one
        self.assertTrue(self.cache.contains(request.build_key))
        self.assertNotEqual(payload.read_bytes(), b"garbage")
        with exploding_exporters():
            job = get_or_build(request, self.cache, output_directory=self.out)
        assert job.result is not None
        self.assertTrue(job.result.cache_hit)
        self.assertEqual(
            list((self.cache_root / STAGING_DIRNAME).iterdir()), []
        )

    def test_a_corruption_is_never_raised_out_of_a_lookup(self) -> None:
        request = self.prepared()
        entry = self.entry_directory(request)
        for damage in (
            lambda: (entry / MANIFEST_FILENAME).write_bytes(b"\xff\xfe not json"),
            lambda: (entry / MANIFEST_FILENAME).write_text("null"),
            lambda: (entry / MANIFEST_FILENAME).write_text('{"artifacts": 3}'),
        ):
            damage()
            lookup = self.cache.lookup(request.build_key)
            self.assertFalse(lookup.hit)
            self.assertIsNotNone(lookup.reason)
            self.assertNotEqual(lookup.detail, "")

    def test_a_directory_where_a_payload_belongs_is_a_miss(self) -> None:
        request = self.prepared()
        payload = self.entry_directory(request) / PAYLOAD_DIRNAME / "stl.stl"
        payload.unlink()
        payload.mkdir()
        self.assert_miss(request, CacheMissReason.PAYLOAD_MISSING)


# --- concurrency ------------------------------------------------------------


class TestConcurrentWriters(CacheTestCase):
    def test_two_writers_of_one_key_leave_one_valid_entry(self) -> None:
        request = self.full_request()
        results = []
        errors: List[BaseException] = []
        barrier = threading.Barrier(2)

        def publish(index: int) -> None:
            try:
                out = self.subdirectory(f"race-{index}")
                job = execute_build(request, output_directory=out)
                assert job.result is not None
                barrier.wait(timeout=60)
                results.append(self.cache.publish(job.result))
            except BaseException as exc:  # recorded, then asserted on
                errors.append(exc)

        threads = [threading.Thread(target=publish, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        # exactly one entry, and it validates
        entries = list((self.cache_root / ENTRIES_DIRNAME).iterdir())
        self.assertEqual([path.name for path in entries], [request.build_key])
        lookup = self.lookup(request)
        self.assertTrue(lookup.hit, msg=lookup.detail)
        # both callers were handed the same, valid entry
        for entry in results:
            self.assertIsInstance(entry, CacheEntry)
            self.assertEqual(entry.build_key, request.build_key)
            self.assertEqual(set(entry.kinds()), set(ArtifactKind))
        self.assertEqual(
            {entry.artifact(ArtifactKind.STL).checksum for entry in results},
            {lookup.entry.artifact(ArtifactKind.STL).checksum},
        )
        self.assertEqual(
            list((self.cache_root / STAGING_DIRNAME).iterdir()), []
        )

    def test_two_get_or_build_callers_of_one_key_both_succeed(self) -> None:
        request = self.request(BuildOutput.GEOMETRY, BuildOutput.STL)
        outcomes: List[bool] = []
        errors: List[BaseException] = []
        barrier = threading.Barrier(2)

        def run(index: int) -> None:
            try:
                barrier.wait(timeout=60)
                job = get_or_build(
                    request,
                    self.cache,
                    output_directory=self.subdirectory(f"both-{index}"),
                )
                assert job.result is not None
                outcomes.append(job.status is BuildStatus.SUCCEEDED)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)

        self.assertEqual(errors, [])
        self.assertEqual(outcomes, [True, True])
        self.assertTrue(self.lookup(request).hit)

    def test_publishing_twice_is_idempotent(self) -> None:
        request = self.full_request()
        job = execute_build(request, output_directory=self.out)
        assert job.result is not None
        first = self.cache.publish(job.result)
        second = self.cache.publish(job.result)
        self.assertEqual(first.build_key, second.build_key)
        self.assertEqual(
            first.manifest.canonical_bytes(), second.manifest.canonical_bytes()
        )
        self.assertEqual(
            [path.name for path in (self.cache_root / ENTRIES_DIRNAME).iterdir()],
            [request.build_key],
        )
        self.assertEqual(
            list((self.cache_root / STAGING_DIRNAME).iterdir()), []
        )


# --- determinism ------------------------------------------------------------


class TestDeterminism(CacheTestCase):
    def test_the_same_request_always_has_the_same_build_key(self) -> None:
        part = self.plate()
        keys = {
            BuildRequest(
                part=part, options=BuildOptions.for_outputs(*ArtifactKind)
            ).build_key
            for _ in range(4)
        }
        self.assertEqual(len(keys), 1)

    def test_a_hit_reports_the_same_logical_ids_and_cached_checksums(self) -> None:
        request = self.full_request()
        self.fill(request)
        first = get_or_build(request, self.cache, output_directory=self.out)
        second = get_or_build(request, self.cache, output_directory=self.out)
        assert first.result is not None and second.result is not None
        self.assertTrue(first.result.cache_hit and second.result.cache_hit)
        self.assertEqual(
            [artifact.logical_id for artifact in first.result.artifacts],
            [artifact.logical_id for artifact in second.result.artifacts],
        )
        self.assertEqual(
            [artifact.checksum for artifact in first.result.artifacts],
            [artifact.checksum for artifact in second.result.artifacts],
        )

    def test_the_cache_does_not_make_step_bytes_deterministic(self) -> None:
        """Two independent builds may differ; the cache only preserves one."""
        request = self.request(BuildOutput.STEP)
        first = execute_build(request, output_directory=self.subdirectory("a"))
        second = execute_build(request, output_directory=self.subdirectory("b"))
        assert first.result is not None and second.result is not None
        # identity is stable whatever the bytes do ...
        self.assertEqual(
            first.result.artifact(ArtifactKind.STEP).logical_id,
            second.result.artifact(ArtifactKind.STEP).logical_id,
        )
        # ... and the cache records the bytes of the build it cached
        entry = self.cache.publish(first.result)
        cached = entry.artifact(ArtifactKind.STEP)
        self.assertEqual(
            cached.checksum, first.result.artifact(ArtifactKind.STEP).checksum
        )
        self.assertEqual(
            Path(cached.path).read_bytes(),
            Path(first.result.artifact(ArtifactKind.STEP).path).read_bytes(),
        )

    def test_the_stored_manifest_is_byte_stable_for_one_build(self) -> None:
        request = self.full_request()
        job = execute_build(request, output_directory=self.out)
        assert job.result is not None
        first = self.cache.publish(job.result)
        stored = self.manifest_path(request).read_bytes()
        again = self.cache.publish(job.result)
        self.assertEqual(self.manifest_path(request).read_bytes(), stored)
        self.assertEqual(
            first.manifest.canonical_hash(), again.manifest.canonical_hash()
        )

    def test_the_cache_does_not_mutate_the_source_document(self) -> None:
        from cad_core.serialization import part_hash, part_to_bytes

        part = self.plate()
        before_bytes = part_to_bytes(part)
        before_hash = part_hash(part)
        request = self.full_request(part=part)
        self.fill(request)
        get_or_build(request, self.cache, output_directory=self.out)
        self.assertEqual(part_to_bytes(part), before_bytes)
        self.assertEqual(part_hash(part), before_hash)


# --- package boundary -------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    def module_source(self, name: str) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "cad_core"
            / f"{name}.py"
        )
        return path.read_text(encoding="utf-8")

    def imports_of(self, name: str) -> List[str]:
        tree = ast.parse(self.module_source(name))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def test_the_cache_imports_no_infrastructure_framework(self) -> None:
        forbidden = {
            "sqlite3",
            "redis",
            "celery",
            "kombu",
            "pika",
            "psycopg2",
            "sqlalchemy",
            "boto3",
            "botocore",
            "google.cloud",
            "azure",
            "requests",
            "httpx",
            "urllib",
            "urllib.request",
            "socket",
            "http",
            "http.client",
            "flask",
            "fastapi",
            "django",
            "diskcache",
            "memcache",
            "pymemcache",
            "asyncio",
            "multiprocessing",
            "concurrent.futures",
            "subprocess",
        }
        for imported in self.imports_of("local_build_cache"):
            self.assertNotIn(imported, forbidden)
            self.assertNotIn(imported.split(".")[0], {"boto3", "azure", "redis"})

    def test_the_cache_imports_no_llm_mcp_or_frontend_module(self) -> None:
        for imported in self.imports_of("local_build_cache"):
            for token in ("llm", "openai", "anthropic", "mcp", "onshape", "featurescript"):
                self.assertNotIn(token, imported.lower())

    def test_the_cache_depends_only_on_the_layers_above_it(self) -> None:
        internal = sorted(
            imported
            for imported in self.imports_of("local_build_cache")
            if imported.startswith("cad_core")
        )
        self.assertEqual(
            internal,
            [
                "cad_core.artifact_registry",
                "cad_core.build_job",
                "cad_core.render_model",
                "cad_core.serialization",
            ],
        )

    def test_no_upstream_module_imports_the_cache(self) -> None:
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
            "__init__",
        ):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.local_build_cache", self.imports_of(name)
                )

    def test_the_cache_is_not_re_exported_from_the_package_root(self) -> None:
        self.assertNotIn(
            "cad_core.local_build_cache", self.imports_of("__init__")
        )

    def code_names(self, name: str) -> List[str]:
        """Every identifier the module's *code* uses, docstrings excluded.

        A prose mention of what the module does not do ("no TTL, no LRU") must
        not be mistaken for the thing itself, so these assertions read the
        syntax tree rather than the file's text.
        """
        tree = ast.parse(self.module_source(name))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.append(node.id)
            elif isinstance(node, ast.Attribute):
                found.append(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.append(node.name)
            elif isinstance(node, ast.arg):
                found.append(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                found.append(node.arg)
        return found

    def code_strings(self, name: str) -> List[str]:
        """Every string literal in the module's code, docstrings excluded."""
        tree = ast.parse(self.module_source(name))
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

    def test_the_cache_never_invents_a_location(self) -> None:
        names = self.code_names("local_build_cache")
        for forbidden in (
            "gettempdir",
            "mkdtemp",
            "TemporaryDirectory",
            "expanduser",
            "home",
            "cwd",
            "getcwd",
            "environ",
            "getenv",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)
        for literal in self.code_strings("local_build_cache"):
            self.assertNotIn("/tmp", literal)
            self.assertNotIn("/var/", literal)
            self.assertFalse(literal.startswith("~"))
            # a bare "/" is an f-string's path separator; an absolute
            # location would be longer than that
            self.assertFalse(len(literal) > 1 and literal.startswith("/"))

    def test_the_cache_does_not_touch_the_cad_semantics(self) -> None:
        names = self.code_names("local_build_cache")
        for forbidden in (
            "build_part",
            "export_step",
            "export_iges",
            "export_stl",
            "select_edges",
            "validate",
            "cadquery",
            "OCP",
            "Workplane",
            "TopoDS",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_no_eviction_policy_was_introduced(self) -> None:
        names = [name.lower() for name in self.code_names("local_build_cache")]
        for forbidden in ("ttl", "lru", "evict", "max_size", "maxsize", "expire"):
            with self.subTest(name=forbidden):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    msg=f"{forbidden!r} appears in the cache's code",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
