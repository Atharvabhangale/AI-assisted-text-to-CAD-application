"""Tests for read-only build-result retrieval.

Three things these tests are about:

* **a retrieval is not a build** -- the child process, the exporters and the
  cache's writers are all patched to explode, and a lookup still answers;
* **the response is the build response** -- for the same build, the body of
  ``GET /builds/{key}`` is asserted equal to the body ``POST /build``
  returned, field for field, apart from the two fields that only make sense
  at creation time;
* **nothing about the cache leaks** -- a key nothing was built under, a
  corrupt entry, a missing manifest and a tampered checksum are one answer,
  and no body carries a path, a directory, a manifest name or a traceback.

The complete client flow is driven end to end: ``POST /build`` for a build
key, ``GET /builds/{key}`` for the artifact list, then
``GET /artifacts/{logical_id}`` for the bytes.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi.testclient import TestClient

from cad_api.app import (
    ARTIFACTS_PREFIX,
    BUILD_LOOKUP_PATH,
    BUILD_PATH,
    BUILDS_PREFIX,
    HEALTH_PATH,
    VALIDATE_PATH,
    create_app,
)
from cad_api.builds import (
    BUILD_KEY_CHARACTERS,
    NOT_FOUND_MESSAGE,
    BuildRetriever,
    RetrievalProblem,
    RetrievalReason,
)
from cad_api.config import ApiConfig
from cad_api.status import (
    BAD_REQUEST_STATUS,
    INTERNAL_STATUS,
    NOT_FOUND_STATUS,
    OK_STATUS,
    RETRIEVAL_STATUS,
    STATUSES,
    status_for_retrieval,
)

from cad_core.application_service import BuildOutcome, CadApplicationService
from cad_core.artifact_registry import ArtifactKind
from cad_core.build_job import BUILD_KEY_LENGTH, BuildStatus, is_build_key
from cad_core.local_build_cache import (
    ENTRIES_DIRNAME,
    MANIFEST_FILENAME,
    PAYLOAD_DIRNAME,
    LocalBuildCache,
)

#: Section D's canonical document hash, pinned since Stage 15.
SECTION_D_HASH = (
    "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
)

PLATE_SIZE = (100.0, 60.0, 10.0)

#: A build key that is well-formed and belongs to nothing.
UNKNOWN_BUILD_KEY = "f" * 64

#: Text a response must never contain.
FORBIDDEN_TEXT: Tuple[str, ...] = (
    "Traceback",
    "/tmp",
    "/usr/",
    "/home/",
    "cad-isolated",
    "entries",
    "manifest",
    "cache_root",
    "workspace",
    "exit code",
    "PYTHONPATH",
    "site-packages",
    "cadquery",
    "OCP",
    "TopoDS",
    "Workplane",
    "object at 0x",
    "stderr",
)

#: Response keys that must never appear, at any depth.
FORBIDDEN_KEYS: Tuple[str, ...] = (
    "path",
    "paths",
    "file_path",
    "location",
    "workspace",
    "directory",
    "cache_root",
    "exit_code",
    "child_pid",
    "pid",
    "traceback",
    "diagnostic",
    "stderr",
    "stdout",
    "environment",
    "detail",
    "execution_outcome",
    "build_failure",
    "stage",
    "worker_status",
    "child_launched",
)

#: The one key that legitimately contains "path": the validator's own name for
#: a position inside the CAD document.
PERMITTED_PATH_KEYS: Tuple[str, ...] = ("field_path",)


def section_d_document() -> Dict[str, Any]:
    holes = (
        ("hole_front_left", 10, 10),
        ("hole_front_right", 90, 10),
        ("hole_back_left", 10, 50),
        ("hole_back_right", 90, 50),
    )
    return {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate-100x60x10-4holes",
        "description": (
            "100 x 60 x 10 mm plate with four 8 mm through-holes, 10 mm from "
            "each corner"
        ),
        "features": [
            {
                "id": "plate",
                "type": "box",
                "size": {"x": 100, "y": 60, "z": 10},
                "position": {"x": 0, "y": 0, "z": 0},
            }
        ]
        + [
            {
                "id": name,
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": x, "y": y, "z": 0},
                "axis": "+Z",
            }
            for name, x, y in holes
        ],
    }


def box_document() -> Dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate",
        "features": [
            {
                "id": "plate",
                "type": "box",
                "size": {
                    "x": PLATE_SIZE[0],
                    "y": PLATE_SIZE[1],
                    "z": PLATE_SIZE[2],
                },
                "position": {"x": 0, "y": 0, "z": 0},
            }
        ],
    }


def walk_keys(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_keys(item)


def walk_strings(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for value in payload.values():
            yield from walk_strings(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_strings(item)
    elif isinstance(payload, str):
        yield payload


class RetrievalTestCase(unittest.TestCase):
    document: Dict[str, Any] = box_document()
    outputs: Tuple[str, ...] = ("geometry", "step", "iges", "stl", "render")

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()
        self.app = create_app(ApiConfig.for_cache_root(self.cache_root))
        self.client = TestClient(self.app)

    # --- fixtures ---------------------------------------------------------

    def build(self, document: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.client.post(
            BUILD_PATH,
            json={
                "document": document if document is not None else self.document,
                "outputs": list(self.outputs),
            },
        )
        self.assertEqual(
            response.status_code, OK_STATUS, msg=response.text[:400]
        )
        return response.json()

    def retrieve(self, build_key: str) -> Any:
        return self.client.get(f"{BUILDS_PREFIX}{build_key}")

    def entry_directory(self, build_key: str) -> Path:
        return self.cache_root / ENTRIES_DIRNAME / build_key

    def payload_path(self, build_key: str, name: str) -> Path:
        return self.entry_directory(build_key) / PAYLOAD_DIRNAME / name

    def stored_manifest(self, build_key: str) -> Dict[str, Any]:
        return json.loads(
            (self.entry_directory(build_key) / MANIFEST_FILENAME).read_bytes()
        )

    def rewrite_manifest(self, build_key: str, payload: Any) -> None:
        (self.entry_directory(build_key) / MANIFEST_FILENAME).write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def manifest_record(
        self, stored: Dict[str, Any], kind: str
    ) -> Dict[str, Any]:
        for record in stored["artifacts"]:
            if record["kind"] == kind:
                return record
        raise AssertionError(f"no {kind} record")

    def snapshot(self, build_key: str) -> List[Tuple[str, int, str]]:
        """Every file in a cache entry, with its size and content digest."""
        return sorted(
            (
                str(path.relative_to(self.entry_directory(build_key))),
                path.stat().st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in self.entry_directory(build_key).rglob("*")
            if path.is_file()
        )

    # --- assertions -------------------------------------------------------

    def assert_safe(self, response: Any) -> str:
        payload = response.json()
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} leaked")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn(str(self.cache_root), text)
        for key in walk_keys(payload):
            lowered = key.lower()
            if lowered in PERMITTED_PATH_KEYS:
                continue
            self.assertNotIn(lowered, FORBIDDEN_KEYS, msg=f"key {key!r} crossed")
        for value in walk_strings(payload):
            self.assertFalse(
                value.startswith("/"), msg=f"{value!r} looks like a path"
            )
        return text

    def assert_not_available(self, response: Any) -> None:
        self.assertEqual(
            response.status_code, NOT_FOUND_STATUS, msg=response.text[:300]
        )
        error = response.json()["error"]
        self.assertEqual(sorted(error), ["message", "reason"])
        self.assertEqual(error["reason"], RetrievalReason.BUILD_NOT_FOUND.value)
        self.assertEqual(error["message"], NOT_FOUND_MESSAGE)
        self.assert_safe(response)


# --- the Section D retrieval ------------------------------------------------


class TestSectionDRetrieval(RetrievalTestCase):
    """The four-hole plate, built and then retrieved by its build key."""

    document = section_d_document()

    def setUp(self) -> None:
        super().setUp()
        self.built = self.build()
        self.key = self.built["build_key"]
        self.response = self.retrieve(self.key)

    def test_the_build_succeeded_and_can_be_retrieved(self) -> None:
        self.assertEqual(self.built["document_hash"], SECTION_D_HASH)
        self.assertEqual(self.response.status_code, OK_STATUS)
        self.assertIn(
            "application/json", self.response.headers["content-type"]
        )

    def test_the_document_hash_is_the_same(self) -> None:
        self.assertEqual(
            self.response.json()["document_hash"], SECTION_D_HASH
        )
        self.assertEqual(
            self.response.json()["document_hash"], self.built["document_hash"]
        )

    def test_the_build_key_is_the_same(self) -> None:
        self.assertEqual(self.response.json()["build_key"], self.key)
        self.assertEqual(len(self.key), BUILD_KEY_LENGTH)
        self.assertTrue(is_build_key(self.key))

    def test_the_status_is_succeeded(self) -> None:
        payload = self.response.json()
        self.assertEqual(payload["status"], BuildStatus.SUCCEEDED.value)
        self.assertTrue(payload["succeeded"])
        self.assertIsNone(payload["error"])

    def test_the_artifact_list_matches_the_original_build(self) -> None:
        payload = self.response.json()
        self.assertEqual(payload["outputs"], self.built["outputs"])
        self.assertEqual(
            payload["outputs"],
            ["geometry", "step", "iges", "stl", "render"],
        )
        self.assertEqual(payload["artifacts"], self.built["artifacts"])

    def test_the_logical_ids_checksums_sizes_and_measurements_match(
        self,
    ) -> None:
        retrieved = {
            artifact["kind"]: artifact
            for artifact in self.response.json()["artifacts"]
        }
        for original in self.built["artifacts"]:
            with self.subTest(kind=original["kind"]):
                mirror = retrieved[original["kind"]]
                self.assertEqual(mirror["logical_id"], original["logical_id"])
                self.assertEqual(mirror["checksum"], original["checksum"])
                self.assertEqual(
                    mirror["checksum_algorithm"], original["checksum_algorithm"]
                )
                self.assertEqual(mirror["size_bytes"], original["size_bytes"])
                self.assertEqual(mirror["format"], original["format"])
                self.assertEqual(mirror["storage"], original["storage"])
                self.assertEqual(
                    mirror["measurements"], original["measurements"]
                )

    def test_the_response_is_the_build_response_apart_from_two_fields(
        self,
    ) -> None:
        """The same transport-contract mapping, so the same shape."""
        retrieved = dict(self.response.json())
        built = dict(self.built)
        self.assertEqual(sorted(retrieved), sorted(built))
        for creation_only in ("cache_hit", "execution_id"):
            retrieved.pop(creation_only)
            built.pop(creation_only)
        self.assertEqual(retrieved, built)

    def test_a_retrieval_reports_the_cache_and_no_execution(self) -> None:
        payload = self.response.json()
        # the artifacts came from the cache ...
        self.assertTrue(payload["cache_hit"])
        # ... and no execution happened, so none is invented
        self.assertIsNone(payload["execution_id"])
        self.assertIsNotNone(self.built["execution_id"])

    def test_no_path_or_execution_internal_crosses(self) -> None:
        text = self.assert_safe(self.response)
        for absent in (
            ENTRIES_DIRNAME,
            MANIFEST_FILENAME,
            "step.step",
            "iges.igs",
            "stl.stl",
        ):
            self.assertNotIn(absent, text)
        # PAYLOAD_DIRNAME is "artifacts", which is also the response's own
        # field name -- the payload *filenames* above are the real signal
        self.assertEqual(PAYLOAD_DIRNAME, "artifacts")
        payload = self.response.json()
        for absent in ("vertices", "normals", "render_model", "geometry_shape"):
            self.assertNotIn(absent, json.dumps(payload))

    def test_the_response_is_json_compatible(self) -> None:
        payload = self.response.json()
        self.assertEqual(json.loads(json.dumps(payload)), payload)


# --- a retrieval builds nothing ---------------------------------------------


class TestRetrievalIsReadOnly(RetrievalTestCase):
    outputs = ("geometry", "step", "stl")

    def test_a_retrieval_starts_no_child_process(self) -> None:
        built = self.build()
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched for a retrieval"),
        ):
            response = self.retrieve(built["build_key"])
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertTrue(response.json()["succeeded"])

    def test_a_retrieval_regenerates_no_artifact(self) -> None:
        built = self.build()
        with mock.patch(
            "cad_core.local_cad.build_part",
            side_effect=AssertionError("geometry was built"),
        ):
            response = self.retrieve(built["build_key"])
        self.assertEqual(response.status_code, OK_STATUS)

    def test_a_retrieval_publishes_nothing_to_the_cache(self) -> None:
        built = self.build()
        with mock.patch.object(
            LocalBuildCache,
            "publish",
            side_effect=AssertionError("the cache was written"),
        ):
            for _ in range(3):
                self.assertEqual(
                    self.retrieve(built["build_key"]).status_code, OK_STATUS
                )

    def test_a_retrieval_does_not_mutate_the_cache(self) -> None:
        built = self.build()
        key = built["build_key"]
        before = self.snapshot(key)
        for _ in range(3):
            self.retrieve(key)
        self.assertEqual(self.snapshot(key), before)
        self.assertEqual(
            sorted(path.name for path in self.cache_root.iterdir()),
            ["entries", "staging"],
        )
        self.assertEqual(
            list((self.cache_root / "staging").iterdir()), []
        )

    def test_repeated_retrievals_are_identical(self) -> None:
        built = self.build()
        first = self.retrieve(built["build_key"]).json()
        second = self.retrieve(built["build_key"]).json()
        self.assertEqual(first, second)
        # no execution id is minted for either
        self.assertIsNone(first["execution_id"])
        self.assertIsNone(second["execution_id"])

    def test_a_retrieval_does_not_alter_the_builds_execution_id(self) -> None:
        built = self.build()
        self.retrieve(built["build_key"])
        again = self.client.post(
            BUILD_PATH,
            json={"document": self.document, "outputs": list(self.outputs)},
        ).json()
        self.assertTrue(again["cache_hit"])
        self.assertEqual(again["build_key"], built["build_key"])
        # a cache hit has no execution either, and the first build's id was
        # never stored to be altered
        self.assertIsNone(again["execution_id"])
        self.assertIsNotNone(built["execution_id"])


# --- unknown and malformed keys ---------------------------------------------


class TestUnknownAndMalformedKeys(RetrievalTestCase):
    outputs = ("geometry", "step")

    def test_an_unknown_build_key_is_not_available(self) -> None:
        self.assert_not_available(self.retrieve(UNKNOWN_BUILD_KEY))

    def test_an_unknown_key_reveals_nothing_about_a_known_one(self) -> None:
        built = self.build()
        known = self.retrieve(built["build_key"])
        unknown = self.retrieve(UNKNOWN_BUILD_KEY)
        self.assertEqual(known.status_code, OK_STATUS)
        self.assert_not_available(unknown)
        # a near-miss of a real key is answered exactly like any other miss
        neighbour = built["build_key"][:-1] + (
            "0" if built["build_key"][-1] != "0" else "1"
        )
        self.assertTrue(is_build_key(neighbour))
        second = self.retrieve(neighbour)
        self.assert_not_available(second)
        self.assertEqual(unknown.json(), second.json())

    def test_a_malformed_build_key_is_refused(self) -> None:
        for key in (
            "abc",
            "f" * 63,
            "f" * 65,
            UNKNOWN_BUILD_KEY.upper(),
            "g" * 64,
            "f" * 32 + "-" * 32,
            f"{UNKNOWN_BUILD_KEY}:step",
            "%20" * 21,
        ):
            with self.subTest(key=key[:16]):
                response = self.retrieve(key)
                self.assertEqual(response.status_code, BAD_REQUEST_STATUS)
                error = response.json()["error"]
                self.assertEqual(
                    error["reason"], RetrievalReason.BUILD_KEY_INVALID.value
                )
                self.assertIn(str(BUILD_KEY_CHARACTERS), error["message"])
                self.assert_safe(response)

    def test_a_path_shaped_key_never_reaches_the_cache(self) -> None:
        """Refused by syntax, before the cache is asked anything."""
        retriever = BuildRetriever(
            CadApplicationService.local(self.cache_root)
        )
        with mock.patch.object(
            LocalBuildCache,
            "lookup",
            side_effect=AssertionError("the cache was consulted"),
        ):
            for key in (
                "/etc/passwd",
                "../../secret",
                "..\\..\\secret",
                f"..{'/'}{UNKNOWN_BUILD_KEY}",
                f"{UNKNOWN_BUILD_KEY}/../{UNKNOWN_BUILD_KEY}",
                f"{UNKNOWN_BUILD_KEY}\x00",
                f"{UNKNOWN_BUILD_KEY}\r\nX-Injected: 1",
                "entries",
                ENTRIES_DIRNAME,
                MANIFEST_FILENAME,
                "",
                None,
                3,
            ):
                with self.subTest(key=repr(key)[:24]):
                    outcome = retriever.retrieve(key)  # type: ignore[arg-type]
                    self.assertIsInstance(outcome, RetrievalProblem)
                    self.assertIs(
                        outcome.reason, RetrievalReason.BUILD_KEY_INVALID
                    )

    def test_a_traversal_url_is_refused_without_leaking(self) -> None:
        for key in ("%2e%2e%2f%2e%2e%2fsecret", f"{UNKNOWN_BUILD_KEY}%2Fx", "%00"):
            with self.subTest(key=key):
                response = self.retrieve(key)
                self.assertIn(
                    response.status_code,
                    (BAD_REQUEST_STATUS, NOT_FOUND_STATUS),
                )
                self.assert_safe(response)

    def test_an_empty_key_is_not_available(self) -> None:
        for target in (BUILDS_PREFIX, BUILDS_PREFIX.rstrip("/")):
            with self.subTest(target=target):
                response = self.client.get(target)
                self.assertEqual(response.status_code, NOT_FOUND_STATUS)
                self.assertEqual(
                    response.json()["error"]["reason"],
                    RetrievalReason.BUILD_NOT_FOUND.value,
                )

    def test_only_get_is_offered(self) -> None:
        built = self.build()
        target = f"{BUILDS_PREFIX}{built['build_key']}"
        self.assertEqual(self.client.get(target).status_code, OK_STATUS)
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD"):
            with self.subTest(method=method):
                self.assertEqual(
                    self.client.request(method, target).status_code, 405
                )


# --- corrupt entries --------------------------------------------------------


class TestCorruptEntries(RetrievalTestCase):
    outputs = ("geometry", "step", "stl")

    def prepared(self) -> Tuple[Dict[str, Any], str]:
        built = self.build()
        self.assertEqual(self.retrieve(built["build_key"]).status_code, OK_STATUS)
        return built, built["build_key"]

    def test_a_removed_manifest_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        (self.entry_directory(key) / MANIFEST_FILENAME).unlink()
        self.assert_not_available(self.retrieve(key))

    def test_a_corrupt_manifest_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        (self.entry_directory(key) / MANIFEST_FILENAME).write_text(
            "{ not json", encoding="utf-8"
        )
        self.assert_not_available(self.retrieve(key))

    def test_an_altered_checksum_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        stored = self.stored_manifest(key)
        self.manifest_record(stored, "step")["checksum"] = "0" * 64
        self.rewrite_manifest(key, stored)
        self.assert_not_available(self.retrieve(key))

    def test_an_altered_size_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        stored = self.stored_manifest(key)
        self.manifest_record(stored, "step")["size_bytes"] = 7
        self.rewrite_manifest(key, stored)
        self.assert_not_available(self.retrieve(key))

    def test_a_deleted_artifact_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        self.payload_path(key, "step.step").unlink()
        self.assert_not_available(self.retrieve(key))

    def test_a_corrupted_artifact_makes_the_build_unavailable(self) -> None:
        built, key = self.prepared()
        payload = self.payload_path(key, "stl.stl")
        original = payload.read_bytes()
        tampered = bytearray(original)
        tampered[len(tampered) // 2] ^= 0xFF
        payload.write_bytes(bytes(tampered))
        self.assert_not_available(self.retrieve(key))

    def test_a_manifest_naming_another_build_is_unavailable(self) -> None:
        built, key = self.prepared()
        stored = self.stored_manifest(key)
        stored["build_key"] = UNKNOWN_BUILD_KEY
        self.rewrite_manifest(key, stored)
        self.assert_not_available(self.retrieve(key))

    def test_a_manifest_with_a_wrong_document_hash_is_unavailable(self) -> None:
        """The entry's own consistency, not a hash the client supplied."""
        built, key = self.prepared()
        stored = self.stored_manifest(key)
        stored["document_hash"] = "1" * 64
        self.rewrite_manifest(key, stored)
        # the artifacts still name the original document, so the entry
        # contradicts itself and is refused
        self.assert_not_available(self.retrieve(key))

    def test_no_partial_artifact_information_is_returned(self) -> None:
        built, key = self.prepared()
        self.payload_path(key, "step.step").unlink()
        response = self.retrieve(key)
        self.assert_not_available(response)
        payload = response.json()
        self.assertEqual(sorted(payload), ["error"])
        for absent in ("artifacts", "outputs", "build_key", "document_hash"):
            self.assertNotIn(absent, payload)

    def test_the_corruption_reason_is_never_disclosed(self) -> None:
        built, key = self.prepared()
        damage = (
            lambda: (self.entry_directory(key) / MANIFEST_FILENAME).unlink(),
            lambda: self.payload_path(key, "step.step").write_bytes(b"garbage"),
        )
        bodies = []
        for apply in damage:
            self.build()  # restore a good entry
            apply()
            response = self.retrieve(key)
            self.assert_not_available(response)
            bodies.append(response.json())
        # two different corruptions, one indistinguishable answer
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[0], self.retrieve(UNKNOWN_BUILD_KEY).json())

    def test_a_retrieval_does_not_repair_the_cache(self) -> None:
        built, key = self.prepared()
        payload = self.payload_path(key, "step.step")
        payload.write_bytes(b"garbage")
        before = self.snapshot(key)
        self.assert_not_available(self.retrieve(key))
        self.assert_not_available(self.retrieve(key))
        self.assertEqual(self.snapshot(key), before)
        self.assertEqual(payload.read_bytes(), b"garbage")

    def test_a_rebuild_after_corruption_restores_retrieval(self) -> None:
        built, key = self.prepared()
        self.payload_path(key, "step.step").write_bytes(b"garbage")
        self.assert_not_available(self.retrieve(key))
        rebuilt = self.build()
        self.assertFalse(rebuilt["cache_hit"])
        self.assertEqual(rebuilt["build_key"], key)
        response = self.retrieve(key)
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertEqual(response.json()["artifacts"], rebuilt["artifacts"])


# --- the full client flow ---------------------------------------------------


class TestClientFlow(RetrievalTestCase):
    document = section_d_document()

    def test_build_then_retrieve_then_download(self) -> None:
        """POST /build -> GET /builds/{key} -> GET /artifacts/{logical_id}."""
        built = self.build()
        key = built["build_key"]

        retrieved = self.retrieve(key)
        self.assertEqual(retrieved.status_code, OK_STATUS)

        for kind, extension in (
            ("step", ".step"),
            ("iges", ".igs"),
            ("stl", ".stl"),
        ):
            with self.subTest(kind=kind):
                record = next(
                    artifact
                    for artifact in retrieved.json()["artifacts"]
                    if artifact["kind"] == kind
                )
                download = self.client.get(
                    f"{ARTIFACTS_PREFIX}{record['logical_id']}"
                )
                self.assertEqual(download.status_code, OK_STATUS)
                self.assertEqual(
                    len(download.content), record["size_bytes"]
                )
                self.assertEqual(
                    hashlib.sha256(download.content).hexdigest(),
                    record["checksum"],
                )
                self.assertEqual(
                    download.headers["etag"], f'"{record["checksum"]}"'
                )
                self.assertEqual(
                    download.headers["content-disposition"],
                    f'attachment; filename="{key}{extension}"',
                )

    def test_the_retrieved_ids_are_the_built_ids(self) -> None:
        built = self.build()
        retrieved = self.retrieve(built["build_key"]).json()
        self.assertEqual(
            [artifact["logical_id"] for artifact in retrieved["artifacts"]],
            [artifact["logical_id"] for artifact in built["artifacts"]],
        )

    def test_an_in_memory_artifact_from_a_retrieval_is_not_downloadable(
        self,
    ) -> None:
        built = self.build()
        retrieved = self.retrieve(built["build_key"]).json()
        for kind in ("geometry", "render"):
            with self.subTest(kind=kind):
                record = next(
                    artifact
                    for artifact in retrieved["artifacts"]
                    if artifact["kind"] == kind
                )
                download = self.client.get(
                    f"{ARTIFACTS_PREFIX}{record['logical_id']}"
                )
                self.assertEqual(download.status_code, NOT_FOUND_STATUS)
                self.assertEqual(
                    download.json()["error"]["reason"],
                    "artifact_not_downloadable",
                )

    def test_a_retrieval_needs_no_document(self) -> None:
        """The whole point: a build key is enough."""
        built = self.build()
        key = built["build_key"]
        fresh = TestClient(create_app(ApiConfig.for_cache_root(self.cache_root)))
        response = fresh.get(f"{BUILDS_PREFIX}{key}")
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertEqual(response.json()["document_hash"], SECTION_D_HASH)


# --- relocation -------------------------------------------------------------


class TestCacheRelocation(RetrievalTestCase):
    outputs = ("geometry", "step", "stl")

    def test_moving_the_cache_root_preserves_retrieval(self) -> None:
        built = self.build()
        key = built["build_key"]
        before = self.retrieve(key).json()

        moved = self.tmp / "relocated-cache"
        shutil.move(str(self.cache_root), str(moved))
        self.assertFalse(self.cache_root.exists())

        relocated = TestClient(create_app(ApiConfig.for_cache_root(moved)))
        after = relocated.get(f"{BUILDS_PREFIX}{key}")
        self.assertEqual(after.status_code, OK_STATUS, msg=after.text[:300])
        self.assertEqual(after.json(), before)
        # the identity never held a path, so nothing about it changed
        self.assertNotIn(str(self.cache_root), json.dumps(after.json()))
        self.assertNotIn(str(moved), json.dumps(after.json()))
        # and the artifacts are still downloadable from the new root
        record = next(
            artifact
            for artifact in after.json()["artifacts"]
            if artifact["kind"] == "step"
        )
        download = relocated.get(f"{ARTIFACTS_PREFIX}{record['logical_id']}")
        self.assertEqual(download.status_code, OK_STATUS)
        self.assertEqual(
            hashlib.sha256(download.content).hexdigest(), record["checksum"]
        )

    def test_a_second_cache_root_does_not_know_the_build(self) -> None:
        built = self.build()
        other = self.tmp / "other-cache"
        other.mkdir()
        elsewhere = TestClient(create_app(ApiConfig.for_cache_root(other)))
        response = elsewhere.get(f"{BUILDS_PREFIX}{built['build_key']}")
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assertEqual(
            response.json()["error"]["reason"],
            RetrievalReason.BUILD_NOT_FOUND.value,
        )


# --- the retriever in isolation --------------------------------------------


class TestRetrieverBoundary(RetrievalTestCase):
    outputs = ("geometry", "step")

    def test_the_retriever_returns_the_services_outcome(self) -> None:
        built = self.build()
        retriever = BuildRetriever(
            CadApplicationService.local(self.cache_root)
        )
        outcome = retriever.retrieve(built["build_key"])
        self.assertIsInstance(outcome, BuildOutcome)
        self.assertIs(outcome.status, BuildStatus.SUCCEEDED)
        self.assertEqual(outcome.build_key, built["build_key"])
        self.assertTrue(outcome.cache_hit)
        self.assertIsNone(outcome.execution_id)

    def test_the_retriever_refuses_a_wrong_service_type(self) -> None:
        with self.assertRaises(TypeError):
            BuildRetriever(object())  # type: ignore[arg-type]

    def test_the_retriever_holds_the_applications_service(self) -> None:
        self.assertIs(self.app.state.retriever.service, self.app.state.service)

    def test_a_retrieval_failure_is_a_generic_five_hundred(self) -> None:
        built = self.build()
        with mock.patch.object(
            BuildRetriever,
            "retrieve",
            side_effect=RuntimeError("secret internals at /tmp/x"),
        ):
            client = TestClient(self.app, raise_server_exceptions=False)
            with self.assertLogs("cad_api", level="ERROR") as logged:
                response = client.get(f"{BUILDS_PREFIX}{built['build_key']}")
        self.assertIn("secret internals", "\n".join(logged.output))
        self.assertEqual(response.status_code, INTERNAL_STATUS)
        error = response.json()["error"]
        self.assertEqual(
            error["reason"], RetrievalReason.RETRIEVAL_FAILED.value
        )
        self.assertNotIn("secret internals", json.dumps(response.json()))
        self.assertNotIn("RuntimeError", json.dumps(response.json()))


# --- statuses ---------------------------------------------------------------


class TestRetrievalStatuses(unittest.TestCase):
    def test_every_reason_has_a_status(self) -> None:
        self.assertEqual(set(RETRIEVAL_STATUS), set(RetrievalReason))
        for reason, status in RETRIEVAL_STATUS.items():
            with self.subTest(reason=reason.value):
                self.assertIn(status, STATUSES)
                self.assertEqual(status_for_retrieval(reason), status)

    def test_a_malformed_key_and_an_unknown_key_are_distinguishable(
        self,
    ) -> None:
        self.assertEqual(
            RETRIEVAL_STATUS[RetrievalReason.BUILD_KEY_INVALID],
            BAD_REQUEST_STATUS,
        )
        self.assertEqual(
            RETRIEVAL_STATUS[RetrievalReason.BUILD_NOT_FOUND],
            NOT_FOUND_STATUS,
        )

    def test_no_status_reveals_corruption(self) -> None:
        """Unknown and unavailable share one status and one reason."""
        self.assertEqual(len(set(RETRIEVAL_STATUS.values())), 3)
        self.assertEqual(
            RetrievalReason.BUILD_NOT_FOUND.value, "build_not_found"
        )


# --- the boundary -----------------------------------------------------------


class TestRetrievalBoundary(unittest.TestCase):
    def source(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[1] / "src" / "cad_api" / f"{name}.py"
        ).read_text(encoding="utf-8")

    def names_in(self, source: str) -> List[str]:
        tree = ast.parse(source)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        return names

    def imports_of(self, source: str) -> List[str]:
        tree = ast.parse(source)
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def test_the_retriever_contains_no_http(self) -> None:
        imports = self.imports_of(self.source("builds"))
        for forbidden in (
            "fastapi",
            "starlette",
            "pydantic",
            "httpx",
            "httpx2",
            "urllib",
            "http",
            "socket",
        ):
            with self.subTest(imported=forbidden):
                self.assertNotIn(forbidden, imports)

    def test_the_retriever_touches_no_file_or_path(self) -> None:
        names = self.names_in(self.source("builds"))
        for forbidden in (
            "Path",
            "open",
            "read_bytes",
            "realpath",
            "islink",
            "lstat",
            "stat",
            "sha256",
            "iterdir",
            "rglob",
            "lookup",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_retriever_reuses_the_domains_key_syntax(self) -> None:
        source = self.source("builds")
        self.assertIn("is_build_key", source)
        self.assertIn("BUILD_KEY_LENGTH", source)
        # no second regex and no hashing of its own
        self.assertNotIn("re.compile", source)
        self.assertNotIn("hashlib", source)
        self.assertNotIn("[0-9a-f]", source)

    def test_the_retriever_adds_no_store(self) -> None:
        imports = self.imports_of(self.source("builds"))
        for forbidden in (
            "sqlite3",
            "sqlalchemy",
            "shelve",
            "pickle",
            "dbm",
            "json",
            "redis",
            "boto3",
        ):
            with self.subTest(imported=forbidden):
                self.assertNotIn(forbidden, imports)

    def test_the_route_reads_no_file_and_parses_no_manifest(self) -> None:
        names = self.names_in(self.source("app"))
        for forbidden in (
            "Path",
            "open",
            "read_bytes",
            "loads",
            "realpath",
            "lstat",
            "sha256",
            "iterdir",
            "rglob",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_route_reuses_the_transport_contracts_mapping(self) -> None:
        source = self.source("app")
        self.assertIn("build_response", source)
        # and defines no response of its own
        tree = ast.parse(source)
        classes = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]
        self.assertEqual(classes, [])

    def test_no_second_artifact_or_build_schema_was_created(self) -> None:
        for module in ("builds", "app", "artifacts", "schemas"):
            tree = ast.parse(self.source(module))
            classes = [
                node.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ClassDef)
            ]
            for forbidden in (
                "Artifact",
                "ArtifactContract",
                "BuildDetail",
                "BuildDocumentResponse",
                "BuildResult",
                "ArtifactManifest",
            ):
                with self.subTest(module=module, name=forbidden):
                    self.assertNotIn(forbidden, classes)

    def test_no_cache_or_document_endpoint_exists(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "cache"
        root.mkdir()
        client = TestClient(create_app(ApiConfig.for_cache_root(root)))
        schema = client.get("/openapi.json").json()
        self.assertEqual(
            sorted(schema["paths"]),
            sorted(
                [
                    "/artifacts/{artifact_id}",
                    BUILD_LOOKUP_PATH,
                    BUILD_LOOKUP_PATH + "/render",
                    BUILD_PATH,
                    # Stage 30's natural-language route. It is still not a
                    # cache or document endpoint: it reads nothing stored and
                    # returns no identifier that can be exchanged for one.
                    "/generate",
                    HEALTH_PATH,
                    VALIDATE_PATH,
                ]
            ),
        )
        for absent in (
            "/cache",
            "/entries",
            "/documents",
            "/files",
            "/download",
            "/jobs",
            "/manifests",
        ):
            with self.subTest(absent=absent):
                for path in schema["paths"]:
                    self.assertFalse(path.startswith(absent))
        for absent in ("/cache", "/entries", "/documents", "/jobs"):
            self.assertEqual(client.get(absent).status_code, NOT_FOUND_STATUS)

    def test_the_document_hash_is_not_a_lookup_key(self) -> None:
        """There is no document store, so a hash retrieves nothing."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "cache"
        root.mkdir()
        client = TestClient(create_app(ApiConfig.for_cache_root(root)))
        built = client.post(
            BUILD_PATH,
            json={"document": box_document(), "outputs": ["geometry"]},
        ).json()
        self.assertEqual(client.get(f"/documents/{built['document_hash']}").status_code, NOT_FOUND_STATUS)
        # the document hash is well-formed as a key, and names no build
        self.assertTrue(is_build_key(built["document_hash"]))
        response = client.get(f"{BUILDS_PREFIX}{built['document_hash']}")
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assertEqual(
            response.json()["error"]["reason"],
            RetrievalReason.BUILD_NOT_FOUND.value,
        )

    def test_the_documentation_describes_the_endpoint(self) -> None:
        docs = Path(__file__).resolve().parents[3] / "docs"
        http = " ".join((docs / "http-api.md").read_text(encoding="utf-8").split())
        self.assertIn("GET /builds/{build_key}", http)
        retrieval = docs / "build-result-retrieval.md"
        self.assertTrue(retrieval.is_file())
        text = " ".join(
            retrieval.read_text(encoding="utf-8").replace(">", " ").split()
        )
        self.assertIn(
            "This endpoint retrieves successfully published derived results. "
            "It is not a job-status service and not a document database.",
            text,
        )
        for expected in ("no authentication", "no authorization", "build key"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
