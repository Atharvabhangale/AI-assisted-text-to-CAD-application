"""Tests for ``GET /builds/{build_key}/render``.

The one endpoint Stage 25 added to the backend, and the narrowest possible
one: a build key selects a validated cache entry, and its **render artifact**
is the only thing that comes back.

Four things these tests are about:

* **no second serialization** -- the body is asserted byte-identical to
  :func:`cad_core.artifact_registry.canonical_render_bytes` of the render
  model the build published, which is the same bytes the cache stores and the
  same bytes the render artifact's checksum was taken from;
* **it is not a file server** -- there is no parameter that selects a file, a
  format, a directory or an artifact kind, and a key that is not a build key
  is refused before anything is looked up;
* **it is read-only** -- the child process, the geometry builder and the
  cache's writer are patched to explode, and the render model still arrives;
* **nothing about the cache leaks** -- a build with no render output, a build
  that does not exist and a malformed key are three stable reasons and no
  path, directory, manifest name or traceback.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi.testclient import TestClient

from cad_api.app import (
    BUILD_PATH,
    BUILD_RENDER_PATH,
    BUILDS_PREFIX,
    RENDER_CONTENT_TYPE,
    create_app,
)
from cad_api.builds import (
    BUILD_KEY_CHARACTERS,
    NOT_FOUND_MESSAGE,
    BuildRetriever,
    RenderPayload,
    RetrievalProblem,
    RetrievalReason,
)
from cad_api.config import ApiConfig
from cad_api.status import (
    BAD_REQUEST_STATUS,
    NOT_FOUND_STATUS,
    OK_STATUS,
    RETRIEVAL_STATUS,
    status_for_retrieval,
)

from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    ArtifactKind,
    canonical_render_bytes,
    render_model_from_canonical_bytes,
)
from cad_core.build_job import BUILD_KEY_LENGTH, is_build_key
from cad_core.local_build_cache import (
    ENTRIES_DIRNAME,
    MANIFEST_FILENAME,
    PAYLOAD_DIRNAME,
    LocalBuildCache,
)
from cad_core.render_model import RenderModel

#: Section D's canonical document hash, pinned since Stage 15.
SECTION_D_HASH = (
    "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
)

#: The plate the specification's Section D describes, in millimetres.
PLATE_SIZE = (100.0, 60.0, 10.0)

#: How closely a tessellated bound may sit to the exact dimension.
BOUNDS_TOLERANCE_MM = 1e-6

#: A build key that is well-formed and belongs to nothing.
UNKNOWN_BUILD_KEY = "f" * 64

#: Text a refusal must never contain.
FORBIDDEN_TEXT: Tuple[str, ...] = (
    "Traceback",
    "/tmp",
    "/usr/",
    "/home/",
    "cad-isolated",
    "entries",
    "staging",
    "manifest",
    "artifacts/",
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

#: Refusal keys that must never appear, at any depth.
FORBIDDEN_KEYS: Tuple[str, ...] = (
    "path",
    "file_path",
    "location",
    "workspace",
    "directory",
    "cache_root",
    "exit_code",
    "pid",
    "traceback",
    "diagnostic",
    "stderr",
    "stdout",
    "environment",
    "detail",
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


def walk_keys(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_keys(item)


class RenderTransportTestCase(unittest.TestCase):
    document: Dict[str, Any] = box_document()
    outputs: Tuple[str, ...] = ("geometry", "render")

    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()
        self.app = create_app(ApiConfig.for_cache_root(self.cache_root))
        self.client = TestClient(self.app)

    # --- fixtures ---------------------------------------------------------

    def build(
        self,
        document: Optional[Dict[str, Any]] = None,
        outputs: Optional[Tuple[str, ...]] = None,
    ) -> Dict[str, Any]:
        response = self.client.post(
            BUILD_PATH,
            json={
                "document": document if document is not None else self.document,
                "outputs": list(outputs if outputs is not None else self.outputs),
            },
        )
        self.assertEqual(
            response.status_code, OK_STATUS, msg=response.text[:400]
        )
        return response.json()

    def render_url(self, build_key: str) -> str:
        return f"{BUILDS_PREFIX}{build_key}/render"

    def fetch_render(self, build_key: str) -> Any:
        return self.client.get(self.render_url(build_key))

    def published_model(self, build_key: str) -> RenderModel:
        """The render model the cache holds, read through the service."""
        service = self.app.state.retriever.service
        outcome = service.find_build_by_key(build_key)
        assert outcome is not None and outcome.render_model is not None
        return outcome.render_model

    def entry_directory(self, build_key: str) -> Path:
        return self.cache_root / ENTRIES_DIRNAME / build_key

    def snapshot(self, build_key: str) -> List[Tuple[str, int, str]]:
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

    def assert_safe_refusal(self, response: Any, reason: RetrievalReason) -> None:
        payload = response.json()
        self.assertEqual(list(payload), ["error"])
        error = payload["error"]
        self.assertEqual(sorted(error), ["message", "reason"])
        self.assertEqual(error["reason"], reason.value)
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} leaked")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        for key in walk_keys(payload):
            self.assertNotIn(key.lower(), FORBIDDEN_KEYS, msg=f"key {key!r}")
        self.assertFalse(error["message"].startswith("/"))


# --- the Section D render model ---------------------------------------------


class TestSectionDRender(RenderTransportTestCase):
    """The four-hole plate's render model, fetched by its build key."""

    document = section_d_document()
    outputs = ("geometry", "step", "iges", "stl", "render")

    def setUp(self) -> None:
        super().setUp()
        self.built = self.build()
        self.key = self.built["build_key"]
        self.response = self.fetch_render(self.key)

    def test_the_render_model_is_delivered_as_json(self) -> None:
        self.assertEqual(self.built["document_hash"], SECTION_D_HASH)
        self.assertEqual(self.response.status_code, OK_STATUS)
        self.assertIn(
            RENDER_CONTENT_TYPE, self.response.headers["content-type"]
        )
        self.assertEqual(
            int(self.response.headers["content-length"]),
            len(self.response.content),
        )

    def test_the_body_is_the_render_models_own_canonical_bytes(self) -> None:
        expected = canonical_render_bytes(self.published_model(self.key))
        self.assertEqual(self.response.content, expected)

    def test_no_second_serialization_exists(self) -> None:
        # The bytes read back through the *inverse* of the one writer give the
        # model the build published, field for field.
        restored = render_model_from_canonical_bytes(self.response.content)
        self.assertEqual(
            restored.to_dict(), self.published_model(self.key).to_dict()
        )

    def test_the_entity_tag_is_the_render_artifacts_checksum(self) -> None:
        record = next(
            item
            for item in self.built["artifacts"]
            if item["kind"] == ArtifactKind.RENDER.value
        )
        self.assertEqual(record["checksum_algorithm"], CHECKSUM_ALGORITHM)
        self.assertEqual(self.response.headers["etag"], f'"{record["checksum"]}"')
        self.assertEqual(
            hashlib.sha256(self.response.content).hexdigest(),
            record["checksum"],
        )
        self.assertEqual(record["size_bytes"], len(self.response.content))

    def test_the_model_declares_the_conventions_the_viewer_expects(self) -> None:
        payload = self.response.json()
        self.assertEqual(payload["format_version"], "1.0.0")
        self.assertEqual(payload["units"], "mm")
        self.assertEqual(payload["coordinate_system"], "right_handed_z_up")
        self.assertEqual(payload["winding"], "counter_clockwise_outward")
        self.assertEqual(payload["normal_binding"], "per_vertex")
        self.assertEqual(payload["part_name"], "plate-100x60x10-4holes")

    def test_the_mesh_is_internally_consistent(self) -> None:
        payload = self.response.json()
        vertices = payload["vertices"]
        self.assertEqual(len(payload["normals"]), len(vertices))
        self.assertGreater(len(payload["triangles"]), 0)
        for triangle in payload["triangles"]:
            self.assertEqual(len(triangle), 3)
            for index in triangle:
                self.assertIsInstance(index, int)
                self.assertGreaterEqual(index, 0)
                self.assertLess(index, len(vertices))

    def test_the_bounds_are_the_plates_own_dimensions(self) -> None:
        bounds = self.response.json()["bounds"]
        for axis, expected in enumerate(PLATE_SIZE):
            self.assertAlmostEqual(
                bounds["size"][axis], expected, delta=BOUNDS_TOLERANCE_MM
            )
            self.assertAlmostEqual(
                bounds["maximum"][axis] - bounds["minimum"][axis],
                bounds["size"][axis],
                delta=BOUNDS_TOLERANCE_MM,
            )

    def test_the_render_bounds_agree_with_the_geometry_measurements(
        self,
    ) -> None:
        geometry = next(
            item
            for item in self.built["artifacts"]
            if item["kind"] == ArtifactKind.GEOMETRY.value
        )
        box = geometry["measurements"]["bounding_box"]
        bounds = self.response.json()["bounds"]
        for axis, name in enumerate("xyz"):
            self.assertAlmostEqual(
                bounds["size"][axis], box["size"][name], delta=1e-6
            )

    def test_the_response_carries_no_path_or_internal_field(self) -> None:
        payload = self.response.json()
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} leaked")
        self.assertNotIn(str(self.cache_root), text)
        self.assertNotIn(self.tmp.name, text)
        for key in walk_keys(payload):
            self.assertNotIn(key.lower(), FORBIDDEN_KEYS, msg=f"key {key!r}")

    def test_the_response_carries_no_build_or_execution_identity(self) -> None:
        # The render model is geometry, not a build record: the endpoint adds
        # no envelope around it, so it names no execution and no cache entry.
        payload = self.response.json()
        self.assertNotIn("execution_id", payload)
        self.assertNotIn("build_key", payload)
        self.assertNotIn("cache_hit", payload)
        self.assertNotIn("document_hash", payload)

    def test_repeated_requests_return_the_same_bytes_and_tag(self) -> None:
        again = self.fetch_render(self.key)
        self.assertEqual(again.content, self.response.content)
        self.assertEqual(again.headers["etag"], self.response.headers["etag"])


# --- a cache hit renders the same thing -------------------------------------


class TestRenderAcrossCacheStates(RenderTransportTestCase):
    outputs = ("geometry", "render")

    def test_a_cache_hit_returns_byte_identical_render_bytes(self) -> None:
        cold = self.build()
        self.assertFalse(cold["cache_hit"])
        first = self.fetch_render(cold["build_key"])

        warm = self.build()
        self.assertTrue(warm["cache_hit"])
        self.assertEqual(warm["build_key"], cold["build_key"])
        second = self.fetch_render(warm["build_key"])

        self.assertEqual(second.status_code, OK_STATUS)
        self.assertEqual(second.content, first.content)
        self.assertEqual(second.headers["etag"], first.headers["etag"])

    def test_the_render_model_survives_a_fresh_application(self) -> None:
        # A different process would see the same cache; a second application
        # over the same root is the closest this test can come to that.
        built = self.build()
        other = TestClient(create_app(ApiConfig.for_cache_root(self.cache_root)))
        first = self.fetch_render(built["build_key"])
        second = other.get(self.render_url(built["build_key"]))
        self.assertEqual(second.status_code, OK_STATUS)
        self.assertEqual(second.content, first.content)

    def test_two_different_documents_render_differently(self) -> None:
        plate = self.build()
        other = self.build(section_d_document())
        self.assertNotEqual(plate["build_key"], other["build_key"])
        self.assertNotEqual(
            self.fetch_render(plate["build_key"]).content,
            self.fetch_render(other["build_key"]).content,
        )


# --- refusals ---------------------------------------------------------------


class TestRenderRefusals(RenderTransportTestCase):
    def test_a_build_without_a_render_output_is_refused(self) -> None:
        built = self.build(outputs=("geometry", "step"))
        self.assertNotIn(
            ArtifactKind.RENDER.value,
            [item["kind"] for item in built["artifacts"]],
        )
        response = self.fetch_render(built["build_key"])
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assert_safe_refusal(response, RetrievalReason.RENDER_NOT_AVAILABLE)

    def test_the_missing_render_message_says_what_to_ask_for(self) -> None:
        built = self.build(outputs=("geometry",))
        message = self.fetch_render(built["build_key"]).json()["error"][
            "message"
        ]
        self.assertIn("render", message)
        # It reports on the *request*, never on the cache.
        self.assertNotIn("cache", message)
        self.assertNotIn("entry", message)

    def test_a_build_that_does_not_exist_is_refused(self) -> None:
        response = self.fetch_render(UNKNOWN_BUILD_KEY)
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assert_safe_refusal(response, RetrievalReason.BUILD_NOT_FOUND)
        self.assertEqual(
            response.json()["error"]["message"], NOT_FOUND_MESSAGE
        )

    def test_an_unknown_key_and_a_render_less_build_stay_distinguishable(
        self,
    ) -> None:
        # Two different reasons, because neither reveals anything: a build
        # response already lists the outputs a build produced.
        built = self.build(outputs=("geometry",))
        self.assertNotEqual(
            self.fetch_render(built["build_key"]).json()["error"]["reason"],
            self.fetch_render(UNKNOWN_BUILD_KEY).json()["error"]["reason"],
        )

    def test_a_malformed_key_is_refused_before_any_lookup(self) -> None:
        malformed = (
            "",
            "abc",
            "z" * BUILD_KEY_CHARACTERS,
            "A" * BUILD_KEY_CHARACTERS,
            "f" * (BUILD_KEY_CHARACTERS - 1),
            "f" * (BUILD_KEY_CHARACTERS + 1),
            "0x" + "f" * (BUILD_KEY_CHARACTERS - 2),
            "f" * (BUILD_KEY_CHARACTERS - 2) + "==",
            " " + "f" * (BUILD_KEY_CHARACTERS - 1),
            # A newline is not in this list: measured, the HTTP client refuses
            # to put one in a URL at all, so it never reaches the route --
            # the same finding Stage 23 recorded for `/etc/passwd`.
        )
        for key in malformed:
            with self.subTest(key=key):
                response = self.client.get(self.render_url(key))
                self.assertIn(
                    response.status_code,
                    (BAD_REQUEST_STATUS, NOT_FOUND_STATUS),
                    msg=response.text[:200],
                )
                if response.status_code == BAD_REQUEST_STATUS:
                    self.assert_safe_refusal(
                        response, RetrievalReason.BUILD_KEY_INVALID
                    )

    def test_the_malformed_key_message_describes_only_the_public_syntax(
        self,
    ) -> None:
        message = self.client.get(self.render_url("nope")).json()["error"][
            "message"
        ]
        self.assertIn(str(BUILD_KEY_CHARACTERS), message)
        self.assertIn("hexadecimal", message)
        self.assertNotIn("cache", message)

    def test_no_refusal_reveals_whether_a_nearby_key_exists(self) -> None:
        built = self.build()
        key = built["build_key"]
        # One character away from a real key, and a real key with no render.
        neighbour = ("0" if key[0] != "0" else "1") + key[1:]
        self.assertTrue(is_build_key(neighbour))
        response = self.fetch_render(neighbour)
        self.assertEqual(response.status_code, NOT_FOUND_STATUS)
        self.assertEqual(
            response.json(),
            self.fetch_render(UNKNOWN_BUILD_KEY).json(),
        )

    def test_a_retrieval_failure_is_reported_without_detail(self) -> None:
        built = self.build()
        with mock.patch.object(
            BuildRetriever,
            "retrieve_render",
            return_value=RetrievalProblem(
                reason=RetrievalReason.RETRIEVAL_FAILED,
                message="the build could not be retrieved",
            ),
        ):
            response = self.fetch_render(built["build_key"])
        self.assertEqual(
            response.status_code,
            status_for_retrieval(RetrievalReason.RETRIEVAL_FAILED),
        )
        self.assert_safe_refusal(response, RetrievalReason.RETRIEVAL_FAILED)

    def test_every_retrieval_reason_has_a_status(self) -> None:
        for reason in RetrievalReason:
            self.assertIn(reason, RETRIEVAL_STATUS)
        self.assertEqual(
            RETRIEVAL_STATUS[RetrievalReason.RENDER_NOT_AVAILABLE],
            NOT_FOUND_STATUS,
        )


# --- it is not a file server ------------------------------------------------


class TestRenderIsNotAFileServer(RenderTransportTestCase):
    def test_the_path_takes_one_parameter_and_it_is_the_build_key(self) -> None:
        self.assertEqual(BUILD_RENDER_PATH, "/builds/{build_key}/render")
        schema = self.client.get("/openapi.json").json()
        operation = schema["paths"][BUILD_RENDER_PATH]
        self.assertEqual(list(operation), ["get"])
        parameters = operation["get"]["parameters"]
        self.assertEqual(
            [item["name"] for item in parameters], ["build_key"]
        )
        self.assertEqual(parameters[0]["in"], "path")

    def test_no_query_parameter_selects_anything(self) -> None:
        built = self.build()
        plain = self.fetch_render(built["build_key"])
        for query in (
            "?format=stl",
            "?kind=step",
            "?file=manifest.json",
            "?path=/etc/passwd",
            "?artifact=geometry",
        ):
            with self.subTest(query=query):
                response = self.client.get(
                    self.render_url(built["build_key"]) + query
                )
                self.assertEqual(response.status_code, OK_STATUS)
                self.assertEqual(response.content, plain.content)

    def test_a_traversal_attempt_never_reaches_a_file(self) -> None:
        attempts = (
            "../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            f"{ENTRIES_DIRNAME}",
            f"{PAYLOAD_DIRNAME}",
            MANIFEST_FILENAME,
            "..",
            ".",
        )
        for attempt in attempts:
            with self.subTest(attempt=attempt):
                response = self.client.get(self.render_url(attempt))
                self.assertNotEqual(response.status_code, OK_STATUS)
                text = response.text
                self.assertNotIn("root:", text)
                self.assertNotIn(str(self.cache_root), text)
                self.assertNotIn("Traceback", text)

    def test_the_body_is_never_a_file_from_the_cache(self) -> None:
        built = self.build(outputs=("geometry", "step", "render"))
        body = self.fetch_render(built["build_key"]).content
        stored = {
            path.read_bytes()
            for path in self.entry_directory(built["build_key"]).rglob("*")
            if path.is_file()
        }
        # The render model's canonical bytes are what the cache stores for the
        # render artifact, so the body equals *that* payload and no other.
        expected = canonical_render_bytes(
            self.published_model(built["build_key"])
        )
        self.assertEqual(body, expected)
        others = {
            payload
            for payload in stored
            if payload != expected
        }
        self.assertTrue(others)
        self.assertNotIn(body, others)

    def test_only_get_is_allowed(self) -> None:
        built = self.build()
        url = self.render_url(built["build_key"])
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url)
                self.assertNotEqual(response.status_code, OK_STATUS)
                self.assertNotIn("Traceback", response.text)

    def test_there_is_no_render_listing_endpoint(self) -> None:
        for url in (
            BUILDS_PREFIX,
            f"{BUILDS_PREFIX}render",
            "/renders",
            "/render",
        ):
            with self.subTest(url=url):
                self.assertNotEqual(
                    self.client.get(url).status_code, OK_STATUS
                )
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(
            sorted(paths),
            [
                "/artifacts/{artifact_id}",
                "/build",
                "/builds/{build_key}",
                "/builds/{build_key}/render",
                # Stage 30's natural-language route. It serves no render
                # model and lists nothing.
                "/generate",
                "/health",
                "/validate",
            ],
        )


# --- it is read-only --------------------------------------------------------


class TestRenderIsReadOnly(RenderTransportTestCase):
    def test_no_child_process_is_started(self) -> None:
        built = self.build()
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            response = self.fetch_render(built["build_key"])
        self.assertEqual(response.status_code, OK_STATUS)

    def test_no_geometry_is_rebuilt(self) -> None:
        built = self.build()
        with mock.patch(
            "cad_core.local_cad.build_part",
            side_effect=AssertionError("geometry was built"),
        ):
            response = self.fetch_render(built["build_key"])
        self.assertEqual(response.status_code, OK_STATUS)

    def test_nothing_is_tessellated_again(self) -> None:
        built = self.build()
        with mock.patch(
            "cad_core.render_model.build_render_model",
            side_effect=AssertionError("the solid was tessellated again"),
        ):
            response = self.fetch_render(built["build_key"])
        self.assertEqual(response.status_code, OK_STATUS)

    def test_nothing_is_published_to_the_cache(self) -> None:
        built = self.build()
        with mock.patch.object(
            LocalBuildCache,
            "publish",
            side_effect=AssertionError("the cache was written"),
        ):
            for _ in range(3):
                self.assertEqual(
                    self.fetch_render(built["build_key"]).status_code,
                    OK_STATUS,
                )

    def test_the_cache_is_not_mutated(self) -> None:
        built = self.build()
        key = built["build_key"]
        before = self.snapshot(key)
        for _ in range(3):
            self.fetch_render(key)
        self.fetch_render(UNKNOWN_BUILD_KEY)
        self.client.get(self.render_url("not-a-key"))
        self.assertEqual(self.snapshot(key), before)
        self.assertEqual(
            sorted(path.name for path in self.cache_root.iterdir()),
            [ENTRIES_DIRNAME, "staging"],
        )

    def test_a_refused_render_creates_no_cache_entry(self) -> None:
        self.fetch_render(UNKNOWN_BUILD_KEY)
        entries = self.cache_root / ENTRIES_DIRNAME
        self.assertEqual(
            [] if not entries.exists() else list(entries.iterdir()), []
        )


# --- the layer below the route ----------------------------------------------


class TestRenderPayloadRecord(unittest.TestCase):
    """`RenderPayload` on its own: bytes in, size and tag out."""

    def payload(self, **overrides: Any) -> RenderPayload:
        fields: Dict[str, Any] = {
            "build_key": "a" * BUILD_KEY_LENGTH,
            "content": b'{"format_version":"1.0.0"}',
            "checksum": "b" * 64,
        }
        fields.update(overrides)
        return RenderPayload(**fields)

    def test_the_size_is_the_bytes_own_length(self) -> None:
        record = self.payload()
        self.assertEqual(record.size_bytes, len(record.content))

    def test_the_entity_tag_is_the_quoted_checksum(self) -> None:
        record = self.payload()
        self.assertEqual(record.etag, f'"{record.checksum}"')

    def test_a_payload_without_a_checksum_has_no_entity_tag(self) -> None:
        self.assertIsNone(self.payload(checksum=None).etag)

    def test_the_checksum_algorithm_is_the_projects_own(self) -> None:
        self.assertEqual(self.payload().checksum_algorithm, CHECKSUM_ALGORITHM)

    def test_the_record_is_frozen(self) -> None:
        record = self.payload()
        with self.assertRaises(Exception):
            record.content = b"other"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
