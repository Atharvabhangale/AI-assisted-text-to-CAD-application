"""Tests for safe artifact delivery over HTTP.

Most of this file is about what must **not** happen. The client supplies a
logical artifact id and nothing else, so the tests spend their effort on ids
that try to be paths, on cache entries that have been tampered with, and on
what a refusal is allowed to say.

The successful downloads use the real local stack over a temporary cache root:
a real build, real cached files, and byte-for-byte comparison against them.

Nothing outside a test's own temporary directory is ever read. The traversal
tests use ``/etc/passwd`` as a *string* to prove it is refused before any
filesystem access; the file itself is never opened, and a test asserts the
refusal happens without the resolver touching a path.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi.testclient import TestClient

from cad_api.app import ARTIFACT_PATH, ARTIFACTS_PREFIX, BUILD_PATH, create_app
from cad_api.artifacts import (
    ARTIFACT_ID_PATTERN,
    DOWNLOAD_CONTENT_TYPE,
    DOWNLOADABLE_KINDS,
    NOT_FOUND_MESSAGE,
    ArtifactResolver,
    DeliveredArtifact,
    DeliveryProblem,
    DeliveryReason,
)
from cad_api.config import ApiConfig
from cad_api.status import (
    BAD_REQUEST_STATUS,
    DELIVERY_STATUS,
    INTERNAL_STATUS,
    NOT_FOUND_STATUS,
    OK_STATUS,
    STATUSES,
    status_for_delivery,
)

from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    FILE_KINDS,
    IN_MEMORY_KINDS,
    KIND_EXTENSIONS,
    ArtifactKind,
    artifact_logical_id,
)
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

#: Text a refusal must never contain.
FORBIDDEN_TEXT: Tuple[str, ...] = (
    "Traceback",
    "/tmp",
    "/etc",
    "/usr/",
    "/home/",
    "cache",
    "entries",
    "manifest",
    "artifacts/",
    "checksum",
    "sha256",
    "Errno",
    "errno",
    "OSError",
    "FileNotFound",
    "cadquery",
    "OCP",
    "site-packages",
    ".step",
    ".igs",
    ".stl",
)


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


def walk_strings(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for value in payload.values():
            yield from walk_strings(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_strings(item)
    elif isinstance(payload, str):
        yield payload


def symlinks_supported(directory: Path) -> bool:
    """Whether this platform lets the tests create a symlink."""
    probe = directory / "symlink-probe"
    target = directory / "symlink-target"
    target.write_text("x", encoding="utf-8")
    try:
        probe.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    finally:
        for path in (probe, target):
            if path.is_symlink() or path.exists():
                path.unlink()
    return True


class DeliveryTestCase(unittest.TestCase):
    """A real cache root, a real build, and the real HTTP application."""

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

    def artifact_id(self, build: Dict[str, Any], kind: str) -> str:
        for artifact in build["artifacts"]:
            if artifact["kind"] == kind:
                return artifact["logical_id"]
        raise AssertionError(f"no {kind} artifact")

    def record(self, build: Dict[str, Any], kind: str) -> Dict[str, Any]:
        for artifact in build["artifacts"]:
            if artifact["kind"] == kind:
                return artifact
        raise AssertionError(f"no {kind} artifact")

    def download(self, artifact_id: str) -> Any:
        return self.client.get(f"{ARTIFACTS_PREFIX}{artifact_id}")

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

    # --- assertions -------------------------------------------------------

    def assert_refusal(
        self, response: Any, status: int, reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """A refusal with the right status that discloses nothing."""
        self.assertEqual(response.status_code, status, msg=response.text[:300])
        self.assertIn("application/json", response.headers["content-type"])
        payload = response.json()
        error = payload["error"]
        if reason is not None:
            self.assertEqual(error["reason"], reason)
        self.assertEqual(sorted(error), ["message", "reason"])
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} leaked")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        for value in walk_strings(payload):
            self.assertFalse(value.startswith("/"))
        # a refusal carries no download headers either
        for header in ("content-disposition", "etag"):
            self.assertNotIn(header, {name.lower() for name in response.headers})
        return error

    def assert_not_available(self, response: Any) -> None:
        error = self.assert_refusal(
            response, NOT_FOUND_STATUS, DeliveryReason.ARTIFACT_NOT_FOUND.value
        )
        self.assertEqual(error["message"], NOT_FOUND_MESSAGE)


# --- the successful download ------------------------------------------------


class TestSectionDDownloads(DeliveryTestCase):
    """The four-hole plate's three file artifacts, byte for byte."""

    document = section_d_document()

    def test_the_three_file_artifacts_download(self) -> None:
        build = self.build()
        self.assertEqual(build["document_hash"], SECTION_D_HASH)
        for kind, extension in (
            ("step", ".step"),
            ("iges", ".igs"),
            ("stl", ".stl"),
        ):
            with self.subTest(kind=kind):
                record = self.record(build, kind)
                response = self.download(record["logical_id"])
                self.assertEqual(
                    response.status_code, OK_STATUS, msg=response.text[:300]
                )
                # content type
                self.assertEqual(
                    response.headers["content-type"], DOWNLOAD_CONTENT_TYPE
                )
                # deterministic disposition, from trusted metadata
                self.assertEqual(
                    response.headers["content-disposition"],
                    f'attachment; filename="{build["build_key"]}{extension}"',
                )
                # exact verified length
                self.assertEqual(
                    int(response.headers["content-length"]),
                    record["size_bytes"],
                )
                self.assertEqual(len(response.content), record["size_bytes"])
                # the entity tag is the artifact's own checksum
                self.assertEqual(
                    response.headers["etag"], f'"{record["checksum"]}"'
                )
                # the bytes are the artifact's bytes
                self.assertEqual(
                    hashlib.sha256(response.content).hexdigest(),
                    record["checksum"],
                )

    def test_the_bytes_are_the_cached_files_bytes(self) -> None:
        build = self.build()
        key = build["build_key"]
        for kind, name in (
            ("step", "step.step"),
            ("iges", "iges.igs"),
            ("stl", "stl.stl"),
        ):
            with self.subTest(kind=kind):
                cached = self.payload_path(key, name)
                self.assertTrue(cached.is_file())
                response = self.download(self.artifact_id(build, kind))
                self.assertEqual(response.content, cached.read_bytes())

    def test_no_response_header_or_body_names_a_path(self) -> None:
        build = self.build()
        for kind in ("step", "iges", "stl"):
            with self.subTest(kind=kind):
                response = self.download(self.artifact_id(build, kind))
                headers = json.dumps(dict(response.headers))
                for token in (
                    str(self.cache_root),
                    str(self.tmp),
                    self.tmp.name,
                    "Traceback",
                    ENTRIES_DIRNAME,
                    MANIFEST_FILENAME,
                    "/tmp",
                ):
                    self.assertNotIn(token, headers, msg=f"{token!r} leaked")
                # the only path-shaped thing is the filename, which is a build
                # key and an extension
                self.assertIn(
                    build["build_key"], response.headers["content-disposition"]
                )
                self.assertNotIn("/", response.headers["content-disposition"])
                self.assertNotIn("\\", response.headers["content-disposition"])

    def test_the_content_disposition_cannot_inject_a_header(self) -> None:
        build = self.build()
        for kind in ("step", "iges", "stl"):
            disposition = self.download(
                self.artifact_id(build, kind)
            ).headers["content-disposition"]
            for character in ("\r", "\n", ";", '"'):
                if character == '"':
                    # exactly the two that quote the filename
                    self.assertEqual(disposition.count('"'), 2)
                    continue
                if character == ";":
                    self.assertEqual(disposition.count(";"), 1)
                    continue
                self.assertNotIn(character, disposition)

    def test_a_download_reveals_nothing_about_the_cache(self) -> None:
        build = self.build()
        response = self.download(self.artifact_id(build, "step"))
        self.assertEqual(
            sorted(
                name.lower()
                for name in response.headers
                if name.lower() not in ("date", "server")
            ),
            ["content-disposition", "content-length", "content-type", "etag"],
        )


# --- the in-memory kinds ----------------------------------------------------


class TestNonDownloadableKinds(DeliveryTestCase):
    def test_geometry_is_not_downloadable(self) -> None:
        build = self.build()
        error = self.assert_refusal(
            self.download(self.artifact_id(build, "geometry")),
            NOT_FOUND_STATUS,
            DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE.value,
        )
        self.assertIn("geometry", error["message"])
        self.assertIn("not downloadable", error["message"])
        for absent in ("brep", "B-rep", "TopoDS", "shape", "solid"):
            self.assertNotIn(absent, error["message"])

    def test_render_is_not_downloadable(self) -> None:
        build = self.build()
        error = self.assert_refusal(
            self.download(self.artifact_id(build, "render")),
            NOT_FOUND_STATUS,
            DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE.value,
        )
        self.assertIn("render", error["message"])
        self.assertIn("not downloadable", error["message"])
        for absent in ("vertices", "normals", "triangles"):
            self.assertNotIn(absent, error["message"])

    def test_the_downloadable_kinds_are_the_file_backed_ones(self) -> None:
        self.assertEqual(DOWNLOADABLE_KINDS, FILE_KINDS)
        self.assertEqual(
            DOWNLOADABLE_KINDS,
            (ArtifactKind.STEP, ArtifactKind.IGES, ArtifactKind.STL),
        )
        for kind in IN_MEMORY_KINDS:
            self.assertNotIn(kind, DOWNLOADABLE_KINDS)

    def test_an_in_memory_kind_is_refused_without_touching_the_cache(
        self,
    ) -> None:
        """Decided from the id alone, so it discloses nothing."""
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        with mock.patch.object(
            LocalBuildCache,
            "lookup",
            side_effect=AssertionError("the cache was consulted"),
        ):
            for kind in IN_MEMORY_KINDS:
                outcome = resolver.resolve(
                    artifact_logical_id(UNKNOWN_BUILD_KEY, kind)
                )
                self.assertIsInstance(outcome, DeliveryProblem)
                self.assertIs(
                    outcome.reason, DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE
                )


# --- path traversal and malformed ids ---------------------------------------


class TestArtifactIdSafety(DeliveryTestCase):
    def test_a_path_shaped_id_never_reaches_the_filesystem(self) -> None:
        """Refused before any filesystem call, over HTTP and directly."""
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        attempts = (
            "/etc/passwd",
            "etc/passwd",
            "../../secret",
            "..\\..\\secret",
            "....//....//secret",
            "%2e%2e/secret",
            f"{UNKNOWN_BUILD_KEY}:step/../../secret",
            f"{UNKNOWN_BUILD_KEY}:step\\..\\secret",
            f"{UNKNOWN_BUILD_KEY}/step",
            f"..{os.sep}{UNKNOWN_BUILD_KEY}:step",
            "C:\\Windows\\System32",
            "\\\\server\\share",
        )
        with mock.patch(
            "cad_api.artifacts.os.path.realpath",
            side_effect=AssertionError("a path was resolved"),
        ), mock.patch(
            "cad_api.artifacts.open",
            side_effect=AssertionError("a file was opened"),
            create=True,
        ):
            for attempt in attempts:
                with self.subTest(attempt=attempt):
                    outcome = resolver.resolve(attempt)
                    self.assertIsInstance(outcome, DeliveryProblem)
                    self.assertIn(
                        outcome.reason,
                        (
                            DeliveryReason.ARTIFACT_ID_INVALID,
                            DeliveryReason.ARTIFACT_NOT_FOUND,
                        ),
                    )

    def test_a_null_byte_is_refused(self) -> None:
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        for attempt in (
            f"{UNKNOWN_BUILD_KEY}:step\x00",
            f"{UNKNOWN_BUILD_KEY}\x00:step",
            "\x00",
            f"{UNKNOWN_BUILD_KEY}:step\x00.step",
        ):
            with self.subTest(attempt=repr(attempt)):
                outcome = resolver.resolve(attempt)
                self.assertIsInstance(outcome, DeliveryProblem)
                self.assertIs(
                    outcome.reason, DeliveryReason.ARTIFACT_ID_INVALID
                )
        # and over HTTP, percent-encoded so the client will send it
        self.assert_refusal(
            self.download(f"{UNKNOWN_BUILD_KEY}:step%00"),
            BAD_REQUEST_STATUS,
            DeliveryReason.ARTIFACT_ID_INVALID.value,
        )

    def test_a_crlf_id_is_refused(self) -> None:
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        for attempt in (
            f"{UNKNOWN_BUILD_KEY}:step\r\nX-Injected: 1",
            f"{UNKNOWN_BUILD_KEY}:step\n",
            f"{UNKNOWN_BUILD_KEY}:step\r",
        ):
            with self.subTest(attempt=repr(attempt)):
                self.assertIs(
                    resolver.resolve(attempt).reason,
                    DeliveryReason.ARTIFACT_ID_INVALID,
                )

    def test_an_encoded_traversal_that_reaches_the_route_is_refused(
        self,
    ) -> None:
        """A slash the client encodes arrives as the parameter's own text."""
        for encoded in (
            "%2e%2e%2f%2e%2e%2fsecret",
            f"{UNKNOWN_BUILD_KEY}%2Fstep",
            f"{UNKNOWN_BUILD_KEY}:step%2F..%2F..%2Fsecret",
            f"{UNKNOWN_BUILD_KEY}:step%5C..%5Csecret",
        ):
            with self.subTest(encoded=encoded):
                response = self.download(encoded)
                self.assertIn(
                    response.status_code, (BAD_REQUEST_STATUS, NOT_FOUND_STATUS)
                )
                self.assert_refusal(response, response.status_code)

    def test_a_malformed_build_key_is_refused(self) -> None:
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        for key in (
            "abc",
            "",
            "g" * 64,
            UNKNOWN_BUILD_KEY.upper(),
            "f" * 63,
            "f" * 65,
            "f" * 32 + "-" * 32,
            " " * 64,
        ):
            with self.subTest(key=key[:12]):
                self.assertIs(
                    resolver.resolve(f"{key}:step").reason,
                    DeliveryReason.ARTIFACT_ID_INVALID,
                )

    def test_a_malformed_artifact_kind_is_refused(self) -> None:
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        for kind in ("", "hologram", "STEP", "step ", "st ep", "step.step", "*"):
            with self.subTest(kind=repr(kind)):
                self.assertIs(
                    resolver.resolve(f"{UNKNOWN_BUILD_KEY}:{kind}").reason,
                    DeliveryReason.ARTIFACT_ID_INVALID,
                )

    def test_a_structurally_wrong_id_is_refused(self) -> None:
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        for attempt in (
            UNKNOWN_BUILD_KEY,
            f"{UNKNOWN_BUILD_KEY}:",
            ":step",
            ":",
            f"{UNKNOWN_BUILD_KEY}:step:step",
            f"{UNKNOWN_BUILD_KEY}::step",
            f" {UNKNOWN_BUILD_KEY}:step",
            f"{UNKNOWN_BUILD_KEY}:step ",
            "f" * 200 + ":step",
            None,
            3,
            b"bytes",
            [UNKNOWN_BUILD_KEY, "step"],
        ):
            with self.subTest(attempt=repr(attempt)[:30]):
                outcome = resolver.resolve(attempt)  # type: ignore[arg-type]
                self.assertIsInstance(outcome, DeliveryProblem)
                self.assertIs(
                    outcome.reason, DeliveryReason.ARTIFACT_ID_INVALID
                )

    def test_the_id_pattern_is_a_whitelist(self) -> None:
        self.assertIsNotNone(
            ARTIFACT_ID_PATTERN.match(f"{UNKNOWN_BUILD_KEY}:step")
        )
        for bad in (
            f"{UNKNOWN_BUILD_KEY}:step\n",
            f"x{UNKNOWN_BUILD_KEY}:step",
            f"{UNKNOWN_BUILD_KEY}:step/x",
        ):
            self.assertIsNone(ARTIFACT_ID_PATTERN.match(bad))

    def test_a_wellformed_unknown_id_is_simply_unavailable(self) -> None:
        self.assert_not_available(
            self.download(f"{UNKNOWN_BUILD_KEY}:step")
        )

    def test_a_known_key_with_an_unknown_kind_is_refused(self) -> None:
        build = self.build()
        self.assert_refusal(
            self.download(f"{build['build_key']}:hologram"),
            BAD_REQUEST_STATUS,
            DeliveryReason.ARTIFACT_ID_INVALID.value,
        )

    def test_a_known_key_whose_entry_lacks_that_kind_is_unavailable(
        self,
    ) -> None:
        """Built without IGES: the id is well-formed and holds nothing."""
        self.outputs = ("geometry", "step")
        build = self.build()
        self.assert_not_available(
            self.download(f"{build['build_key']}:iges")
        )

    def test_an_empty_id_is_refused(self) -> None:
        self.assert_refusal(
            self.client.get(ARTIFACTS_PREFIX), NOT_FOUND_STATUS
        )
        self.assert_refusal(
            self.client.get(ARTIFACTS_PREFIX.rstrip("/")), NOT_FOUND_STATUS
        )

    def test_only_get_is_offered_on_the_artifact_route(self) -> None:
        build = self.build()
        target = f"{ARTIFACTS_PREFIX}{self.artifact_id(build, 'step')}"
        self.assertEqual(self.client.get(target).status_code, OK_STATUS)
        for method in ("POST", "PUT", "DELETE", "PATCH", "HEAD"):
            with self.subTest(method=method):
                # 405, not 404: the resource exists and the method does not
                self.assertEqual(
                    self.client.request(method, target).status_code, 405
                )


# --- tampered cache entries -------------------------------------------------


class TestTamperedCache(DeliveryTestCase):
    outputs = ("geometry", "step", "stl")

    def prepared(self) -> Tuple[Dict[str, Any], str]:
        build = self.build()
        return build, build["build_key"]

    def test_corrupted_payload_bytes_are_not_served(self) -> None:
        build, key = self.prepared()
        payload = self.payload_path(key, "step.step")
        original = payload.read_bytes()
        tampered = bytearray(original)
        tampered[len(tampered) // 2] ^= 0xFF
        payload.write_bytes(bytes(tampered))
        self.assertEqual(len(payload.read_bytes()), len(original))
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )
        # ... and the corrupt bytes were not served, nor repaired
        self.assertEqual(payload.read_bytes(), bytes(tampered))

    def test_a_truncated_payload_is_not_served(self) -> None:
        build, key = self.prepared()
        payload = self.payload_path(key, "stl.stl")
        payload.write_bytes(payload.read_bytes()[:-64])
        self.assert_not_available(self.download(self.artifact_id(build, "stl")))

    def test_a_missing_payload_is_not_served(self) -> None:
        build, key = self.prepared()
        self.payload_path(key, "step.step").unlink()
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_missing_manifest_makes_the_entry_unavailable(self) -> None:
        build, key = self.prepared()
        (self.entry_directory(key) / MANIFEST_FILENAME).unlink()
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_malformed_manifest_makes_the_entry_unavailable(self) -> None:
        build, key = self.prepared()
        (self.entry_directory(key) / MANIFEST_FILENAME).write_text(
            "{ not json", encoding="utf-8"
        )
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_manifest_with_a_wrong_checksum_is_refused(self) -> None:
        build, key = self.prepared()
        stored = self.stored_manifest(key)
        self.manifest_record(stored, "step")["checksum"] = "0" * 64
        self.rewrite_manifest(key, stored)
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_manifest_with_a_wrong_size_is_refused(self) -> None:
        build, key = self.prepared()
        stored = self.stored_manifest(key)
        self.manifest_record(stored, "step")["size_bytes"] = 7
        self.rewrite_manifest(key, stored)
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_manifest_with_a_wrong_format_is_refused(self) -> None:
        build, key = self.prepared()
        stored = self.stored_manifest(key)
        self.manifest_record(stored, "step")["format"] = "stl-binary"
        self.rewrite_manifest(key, stored)
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_manifest_naming_another_build_is_refused(self) -> None:
        build, key = self.prepared()
        stored = self.stored_manifest(key)
        for record in stored["artifacts"]:
            record["logical_id"] = artifact_logical_id(
                UNKNOWN_BUILD_KEY, ArtifactKind(record["kind"])
            )
        self.rewrite_manifest(key, stored)
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_manifest_pointing_outside_the_entry_is_refused(self) -> None:
        build, key = self.prepared()
        outside = self.tmp / "outside.step"
        outside.write_bytes(b"ISO-10303-21;\nOUTSIDE\nEND-ISO-10303-21;\n")
        stored = self.stored_manifest(key)
        record = self.manifest_record(stored, "step")
        record["payload_path"] = f"../../../{outside.name}"
        record["size_bytes"] = len(outside.read_bytes())
        record["checksum"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.rewrite_manifest(key, stored)
        response = self.download(self.artifact_id(build, "step"))
        self.assert_not_available(response)
        self.assertNotIn(b"OUTSIDE", response.content)

    def test_an_absolute_payload_path_in_the_manifest_is_refused(self) -> None:
        build, key = self.prepared()
        outside = self.tmp / "outside.step"
        outside.write_bytes(b"ISO-10303-21;\nOUTSIDE\nEND-ISO-10303-21;\n")
        stored = self.stored_manifest(key)
        record = self.manifest_record(stored, "step")
        record["payload_path"] = str(outside)
        record["size_bytes"] = len(outside.read_bytes())
        record["checksum"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.rewrite_manifest(key, stored)
        response = self.download(self.artifact_id(build, "step"))
        self.assert_not_available(response)
        self.assertNotIn(b"OUTSIDE", response.content)

    def test_a_payload_with_a_foreign_extension_is_refused(self) -> None:
        build, key = self.prepared()
        source = self.payload_path(key, "step.step")
        renamed = source.with_suffix(".txt")
        source.rename(renamed)
        stored = self.stored_manifest(key)
        record = self.manifest_record(stored, "step")
        record["payload_path"] = f"{PAYLOAD_DIRNAME}/{renamed.name}"
        record["file_extension"] = ".txt"
        self.rewrite_manifest(key, stored)
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_directory_where_the_payload_belongs_is_refused(self) -> None:
        build, key = self.prepared()
        payload = self.payload_path(key, "step.step")
        payload.unlink()
        payload.mkdir()
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_the_cache_is_never_repaired_by_a_download(self) -> None:
        build, key = self.prepared()
        payload = self.payload_path(key, "step.step")
        payload.write_bytes(b"garbage")
        before = sorted(
            (path.name, path.stat().st_size)
            for path in self.entry_directory(key).rglob("*")
            if path.is_file()
        )
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )
        after = sorted(
            (path.name, path.stat().st_size)
            for path in self.entry_directory(key).rglob("*")
            if path.is_file()
        )
        self.assertEqual(before, after)
        self.assertEqual(payload.read_bytes(), b"garbage")

    def test_a_rebuild_repairs_the_entry_and_delivery_resumes(self) -> None:
        build, key = self.prepared()
        payload = self.payload_path(key, "step.step")
        payload.write_bytes(b"garbage")
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )
        rebuilt = self.build()
        self.assertFalse(rebuilt["cache_hit"])
        self.assertEqual(rebuilt["build_key"], key)
        response = self.download(self.artifact_id(rebuilt, "step"))
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertEqual(
            hashlib.sha256(response.content).hexdigest(),
            self.record(rebuilt, "step")["checksum"],
        )


# --- symlinks ---------------------------------------------------------------


class TestSymlinkSafety(DeliveryTestCase):
    outputs = ("geometry", "step")

    def setUp(self) -> None:
        super().setUp()
        if not symlinks_supported(self.tmp):
            self.skipTest("this platform cannot create symlinks")

    def test_a_symlinked_payload_pointing_outside_is_refused(self) -> None:
        build = self.build()
        key = build["build_key"]
        outside = self.tmp / "outside.step"
        outside.write_bytes(b"ISO-10303-21;\nOUTSIDE SECRET\nEND-ISO-10303-21;\n")
        payload = self.payload_path(key, "step.step")
        payload.unlink()
        payload.symlink_to(outside)
        # the manifest is made to agree with the linked bytes, so only the
        # symlink itself stands between a client and a file outside the entry
        stored = self.stored_manifest(key)
        record = self.manifest_record(stored, "step")
        record["size_bytes"] = len(outside.read_bytes())
        record["checksum"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.rewrite_manifest(key, stored)
        # the cache itself accepts this entry -- the delivery layer does not
        self.assertTrue(
            LocalBuildCache(self.cache_root).contains(key),
            msg="the cache no longer accepts a symlinked payload",
        )
        response = self.download(self.artifact_id(build, "step"))
        self.assert_not_available(response)
        self.assertNotIn(b"OUTSIDE SECRET", response.content)

    def test_a_symlinked_payload_pointing_inside_is_still_refused(self) -> None:
        """A cache entry has no legitimate use for a link at all."""
        build = self.build()
        key = build["build_key"]
        payload = self.payload_path(key, "step.step")
        real = payload.with_name("real.step")
        payload.rename(real)
        payload.symlink_to(real)
        self.assertTrue(payload.is_file())
        self.assert_not_available(
            self.download(self.artifact_id(build, "step"))
        )

    def test_a_symlinked_payload_directory_is_refused(self) -> None:
        build = self.build()
        key = build["build_key"]
        entry = self.entry_directory(key)
        elsewhere = self.tmp / "elsewhere"
        shutil.move(str(entry / PAYLOAD_DIRNAME), str(elsewhere))
        (entry / PAYLOAD_DIRNAME).symlink_to(elsewhere, target_is_directory=True)
        self.assertTrue(self.payload_path(key, "step.step").is_file())
        response = self.download(self.artifact_id(build, "step"))
        self.assert_not_available(response)

    def test_the_measured_hazard_is_real(self) -> None:
        """Records why this layer checks: the naive checks all pass.

        ``Path.is_file()`` follows a symlink, and an unresolved containment
        check on ``<entry>/artifacts/step.step`` succeeds however the link
        points. Only the real path shows the escape.
        """
        entry = self.tmp / "entry"
        (entry / PAYLOAD_DIRNAME).mkdir(parents=True)
        outside = self.tmp / "target.step"
        outside.write_bytes(b"OUTSIDE")
        link = entry / PAYLOAD_DIRNAME / "step.step"
        link.symlink_to(outside)
        self.assertTrue(link.is_file())
        self.assertEqual(link.relative_to(entry).parts[0], PAYLOAD_DIRNAME)
        self.assertNotEqual(
            os.path.commonpath(
                [os.path.realpath(entry), os.path.realpath(link)]
            ),
            os.path.realpath(entry),
        )


# --- cache hits and relocation ----------------------------------------------


class TestCacheRelationship(DeliveryTestCase):
    outputs = ("geometry", "step", "iges", "stl", "render")

    def test_a_cache_hit_delivers_the_same_bytes(self) -> None:
        first = self.build()
        downloads = {
            kind: self.download(self.artifact_id(first, kind)).content
            for kind in ("step", "iges", "stl")
        }
        second = self.build()
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["build_key"], first["build_key"])
        for kind, content in downloads.items():
            with self.subTest(kind=kind):
                record_first = self.record(first, kind)
                record_second = self.record(second, kind)
                self.assertEqual(
                    record_first["logical_id"], record_second["logical_id"]
                )
                self.assertEqual(
                    record_first["checksum"], record_second["checksum"]
                )
                again = self.download(record_second["logical_id"])
                self.assertEqual(again.status_code, OK_STATUS)
                self.assertEqual(again.content, content)
                self.assertEqual(
                    again.headers["etag"], f'"{record_second["checksum"]}"'
                )

    def test_moving_the_cache_root_keeps_the_artifact_ids(self) -> None:
        """Stage 18's relative payload paths, proved from the outside."""
        build = self.build()
        step_id = self.artifact_id(build, "step")
        before = self.download(step_id)
        self.assertEqual(before.status_code, OK_STATUS)

        moved = self.tmp / "relocated-cache"
        shutil.move(str(self.cache_root), str(moved))
        self.assertFalse(self.cache_root.exists())

        relocated = TestClient(create_app(ApiConfig.for_cache_root(moved)))
        after = relocated.get(f"{ARTIFACTS_PREFIX}{step_id}")
        self.assertEqual(after.status_code, OK_STATUS, msg=after.text[:300])
        self.assertEqual(after.content, before.content)
        self.assertEqual(after.headers["etag"], before.headers["etag"])
        self.assertEqual(
            after.headers["content-disposition"],
            before.headers["content-disposition"],
        )
        # the identity never held a path, so nothing about it changed
        self.assertTrue(step_id.startswith(build["build_key"]))
        self.assertNotIn(str(self.cache_root), step_id)
        self.assertNotIn(str(moved), step_id)

    def test_a_different_output_selection_is_a_different_artifact_id(
        self,
    ) -> None:
        wide = self.build()
        self.outputs = ("step",)
        narrow = self.build()
        self.assertNotEqual(narrow["build_key"], wide["build_key"])
        self.assertNotEqual(
            self.artifact_id(narrow, "step"), self.artifact_id(wide, "step")
        )
        for build in (wide, narrow):
            response = self.download(self.artifact_id(build, "step"))
            self.assertEqual(response.status_code, OK_STATUS)

    def test_an_artifact_from_a_second_document_is_separate(self) -> None:
        first = self.build()
        second = self.build(section_d_document())
        self.assertNotEqual(first["build_key"], second["build_key"])
        for build in (first, second):
            record = self.record(build, "stl")
            response = self.download(record["logical_id"])
            self.assertEqual(response.status_code, OK_STATUS)
            self.assertEqual(
                hashlib.sha256(response.content).hexdigest(),
                record["checksum"],
            )
        self.assertNotEqual(
            self.record(first, "stl")["checksum"],
            self.record(second, "stl")["checksum"],
        )

    def test_an_artifact_is_unavailable_before_its_build(self) -> None:
        service_key = "a" * 64
        self.assert_not_available(self.download(f"{service_key}:step"))


# --- the resolver in isolation ----------------------------------------------


class TestResolverBoundary(DeliveryTestCase):
    outputs = ("geometry", "step")

    def test_a_resolver_without_a_cache_delivers_nothing(self) -> None:
        resolver = ArtifactResolver(None)
        outcome = resolver.resolve(f"{UNKNOWN_BUILD_KEY}:step")
        self.assertIsInstance(outcome, DeliveryProblem)
        self.assertIs(outcome.reason, DeliveryReason.ARTIFACT_NOT_FOUND)
        self.assertIsNone(resolver.cache)

    def test_the_resolver_refuses_a_wrong_cache_type(self) -> None:
        with self.assertRaises(TypeError):
            ArtifactResolver(object())  # type: ignore[arg-type]

    def test_the_resolver_returns_the_verified_bytes(self) -> None:
        build = self.build()
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        delivered = resolver.resolve(self.artifact_id(build, "step"))
        self.assertIsInstance(delivered, DeliveredArtifact)
        record = self.record(build, "step")
        self.assertEqual(delivered.size_bytes, record["size_bytes"])
        self.assertEqual(delivered.checksum, record["checksum"])
        self.assertEqual(delivered.checksum_algorithm, CHECKSUM_ALGORITHM)
        self.assertEqual(len(delivered.content), delivered.size_bytes)
        self.assertEqual(
            hashlib.sha256(delivered.content).hexdigest(), delivered.checksum
        )
        self.assertEqual(delivered.etag, f'"{record["checksum"]}"')
        self.assertEqual(delivered.content_type, DOWNLOAD_CONTENT_TYPE)
        self.assertEqual(
            delivered.filename, f"{build['build_key']}.step"
        )
        self.assertFalse(hasattr(delivered, "path"))

    def test_the_delivered_filename_uses_the_manifests_extension(self) -> None:
        build = self.build()
        resolver = ArtifactResolver(LocalBuildCache(self.cache_root))
        delivered = resolver.resolve(self.artifact_id(build, "step"))
        self.assertIn(
            Path(delivered.filename).suffix,
            KIND_EXTENSIONS[ArtifactKind.STEP],
        )

    def test_the_resolver_holds_the_applications_cache(self) -> None:
        self.assertIs(
            self.app.state.resolver.cache,
            self.app.state.service.backend.cache,
        )

    def test_a_service_without_a_cache_yields_an_empty_resolver(self) -> None:
        from cad_core.application_service import CadApplicationService
        from cad_core.build_job import BuildRequest

        class CachelessBackend:
            def execute(self, request: BuildRequest) -> Any:  # pragma: no cover
                raise AssertionError("not used")

            def lookup(self, request: BuildRequest) -> Any:
                return None

        app = create_app(service=CadApplicationService(CachelessBackend()))
        self.assertIsNone(app.state.resolver.cache)
        client = TestClient(app)
        response = client.get(f"{ARTIFACTS_PREFIX}{UNKNOWN_BUILD_KEY}:step")
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assertEqual(
            response.json()["error"]["reason"],
            DeliveryReason.ARTIFACT_NOT_FOUND.value,
        )

    def test_a_delivery_failure_is_a_generic_five_hundred(self) -> None:
        build = self.build()
        target = f"{ARTIFACTS_PREFIX}{self.artifact_id(build, 'step')}"
        with mock.patch.object(
            ArtifactResolver,
            "resolve",
            side_effect=RuntimeError("secret internals at /tmp/x"),
        ):
            client = TestClient(self.app, raise_server_exceptions=False)
            with self.assertLogs("cad_api", level="ERROR") as logged:
                response = client.get(target)
        self.assertIn("secret internals", "\n".join(logged.output))
        self.assertEqual(response.status_code, INTERNAL_STATUS)
        error = response.json()["error"]
        self.assertEqual(error["reason"], DeliveryReason.DELIVERY_FAILED.value)
        self.assertNotIn("secret internals", json.dumps(response.json()))
        self.assertNotIn("RuntimeError", json.dumps(response.json()))


# --- statuses and the boundary ----------------------------------------------


class TestDeliveryStatuses(unittest.TestCase):
    def test_every_reason_has_a_status(self) -> None:
        self.assertEqual(set(DELIVERY_STATUS), set(DeliveryReason))
        for reason, status in DELIVERY_STATUS.items():
            with self.subTest(reason=reason.value):
                self.assertIn(status, STATUSES)
                self.assertEqual(status_for_delivery(reason), status)

    def test_the_two_not_found_reasons_share_a_status_and_differ_by_reason(
        self,
    ) -> None:
        self.assertEqual(
            DELIVERY_STATUS[DeliveryReason.ARTIFACT_NOT_FOUND],
            DELIVERY_STATUS[DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE],
        )
        self.assertNotEqual(
            DeliveryReason.ARTIFACT_NOT_FOUND.value,
            DeliveryReason.ARTIFACT_NOT_DOWNLOADABLE.value,
        )

    def test_a_malformed_id_and_an_unknown_id_are_distinguishable(self) -> None:
        self.assertNotEqual(
            DELIVERY_STATUS[DeliveryReason.ARTIFACT_ID_INVALID],
            DELIVERY_STATUS[DeliveryReason.ARTIFACT_NOT_FOUND],
        )
        self.assertEqual(
            DELIVERY_STATUS[DeliveryReason.ARTIFACT_ID_INVALID],
            BAD_REQUEST_STATUS,
        )


class TestDeliveryBoundary(unittest.TestCase):
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

    def test_the_route_touches_no_path_and_no_checksum(self) -> None:
        names = self.names_in(self.source("app"))
        for forbidden in (
            "Path",
            "open",
            "read_bytes",
            "realpath",
            "commonpath",
            "islink",
            "lstat",
            "stat",
            "sha256",
            "iterdir",
            "rglob",
            "FileResponse",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_route_knows_no_storage_layout(self) -> None:
        source = self.source("app")
        for forbidden in (
            "ENTRIES_DIRNAME",
            "PAYLOAD_DIRNAME",
            "MANIFEST_FILENAME",
            "KIND_EXTENSIONS",
            ".step",
            ".igs",
            ".stl",
        ):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_route_imports_no_cache_module(self) -> None:
        imports = self.imports_of(self.source("app"))
        self.assertNotIn("cad_core.local_build_cache", imports)
        self.assertNotIn("cad_core.artifact_registry", imports)

    def test_the_resolver_contains_no_http(self) -> None:
        source = self.source("artifacts")
        imports = self.imports_of(source)
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
        names = self.names_in(source)
        for forbidden in ("Response", "JSONResponse", "Request", "HTTPException"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_the_resolver_reuses_the_domains_rules(self) -> None:
        imports = self.imports_of(self.source("artifacts"))
        self.assertIn("cad_core.artifact_registry", imports)
        self.assertIn("cad_core.local_build_cache", imports)
        source = self.source("artifacts")
        # identity, extensions, formats and the checksum algorithm all come
        # from the registry rather than being restated
        for expected in (
            "artifact_logical_id",
            "KIND_EXTENSIONS",
            "KIND_FORMATS",
            "CHECKSUM_ALGORITHM",
            "FILE_KINDS",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, source)

    def test_the_resolver_computes_no_logical_id_of_its_own(self) -> None:
        source = self.source("artifacts")
        # no string building of an id, and no second identity scheme
        self.assertNotIn('f"{build_key}:{', source)
        self.assertNotIn("build_key + ':'", source)

    def test_no_http_code_reached_cad_core(self) -> None:
        core = (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "cad-core"
            / "src"
            / "cad_core"
        )
        for path in sorted(core.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            imports = self.imports_of(source)
            for forbidden in ("fastapi", "starlette", "pydantic", "httpx"):
                with self.subTest(module=path.name, imported=forbidden):
                    self.assertNotIn(forbidden, imports)
            for token in ("Content-Disposition", "ETag", "octet-stream"):
                with self.subTest(module=path.name, token=token):
                    self.assertNotIn(token, source)

    def test_no_artifact_url_was_invented_in_cad_core(self) -> None:
        core = (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "cad-core"
            / "src"
            / "cad_core"
            / "api_contract.py"
        ).read_text(encoding="utf-8")
        for token in ("http://", "https://", "/artifacts/", "download_url"):
            with self.subTest(token=token):
                self.assertNotIn(token, core)

    def test_only_the_one_artifact_route_exists(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "cache"
        root.mkdir()
        client = TestClient(create_app(ApiConfig.for_cache_root(root)))
        schema = client.get("/openapi.json").json()
        self.assertEqual(
            sorted(schema["paths"]),
            sorted(["/build", "/health", "/validate", ARTIFACT_PATH]),
        )
        for absent in ("/files", "/download", "/cache", "/builds"):
            for path in schema["paths"]:
                self.assertFalse(path.startswith(absent))

    def test_the_documentation_covers_the_endpoint(self) -> None:
        docs = Path(__file__).resolve().parents[3] / "docs"
        http = " ".join((docs / "http-api.md").read_text(encoding="utf-8").split())
        self.assertIn("GET /artifacts/{artifact_id}", http)
        delivery = docs / "artifact-delivery.md"
        self.assertTrue(delivery.is_file())
        text = " ".join(delivery.read_text(encoding="utf-8").split())
        for expected in (
            "no authentication",
            "no authorization",
            "symlink",
            "logical",
            "checksum",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, text.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
