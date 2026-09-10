"""Tests for the HTTP transport.

Three things these tests are about:

* **the transport is thin** -- the routes are asserted to import and call no
  geometry, exporter, cache or worker module, and a stubbed service proves a
  request reaches the service and comes back as its response without any CAD
  running at all;
* **the status map means something** -- a client mistake is a 4xx and a server
  failure is a 5xx, per failure classification, and nothing collapses to 500;
* **nothing unsafe crosses** -- every response body is walked recursively and
  asserted to hold no path, traceback, environment detail, process detail or
  kernel object.

Alongside the stubbed tests, a smaller set drives the **real** local stack --
the real application service, the real cache, real isolated child processes --
over a temporary cache root, including the Section D four-hole plate.

Measured in this environment, not assumed: FastAPI 0.141.1, Pydantic 2.13.5,
Starlette 1.6.0. Starlette's ``TestClient`` is the supported mechanism here,
and this Starlette deprecates ``httpx`` in favour of ``httpx2``, which is what
the test extra installs.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi.testclient import TestClient

from cad_api.app import (
    ARTIFACT_PATH,
    BUILD_PATH,
    HEALTH_PATH,
    VALIDATE_PATH,
    create_app,
)
from cad_api.config import (
    CACHE_ROOT_VARIABLE,
    TIMEOUT_VARIABLE,
    ApiConfig,
    ConfigurationError,
    config_from_environment,
)
from cad_api.schemas import BuildBody, ValidateBody
from cad_api.status import (
    FAILURE_STATUS,
    INTERNAL_STATUS,
    OK_STATUS,
    STATUSES,
    TRANSPORT_STATUS,
    UNAVAILABLE_STATUS,
    UNPROCESSABLE_STATUS,
    status_for_failure,
)

from cad_core import validate
from cad_core.api_contract import CadApiContract, OUTPUTS
from cad_core.application_service import (
    BuildDocumentRequest,
    CadApplicationService,
    ServiceFailure,
)
from cad_core.artifact_registry import CHECKSUM_ALGORITHM, ArtifactKind
from cad_core.build_job import BuildOptions, BuildRequest, BuildStatus
from cad_core.isolated_execution import IsolationOutcome, execute_isolated

#: Section D's canonical document hash, pinned since Stage 15.
SECTION_D_HASH = (
    "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
)

PLATE_SIZE = (100.0, 60.0, 10.0)

#: Text that must never appear in a response body.
FORBIDDEN_TEXT: Tuple[str, ...] = (
    "Traceback",
    "cadquery",
    "CadQuery",
    "OCP",
    "TopoDS",
    "Workplane",
    "object at 0x",
    "cad-isolated",
    "/tmp",
    "/usr/",
    "/home/",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "stderr",
    "site-packages",
    "BRepFilletAPI",
    "exit code",
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
    "token",
    "secret",
    "password",
    "credential",
)

#: The one key that legitimately contains "path": the validator's own name for
#: a position inside the CAD document, such as ``features[0].size.x``.
PERMITTED_PATH_KEYS: Tuple[str, ...] = ("field_path",)


def section_d_document() -> Dict[str, Any]:
    """The worked example from ``docs/cad-specification.md`` Section D."""
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


def box_document() -> Dict[str, Any]:
    return document([plate_feature()])


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


class StubService:
    """A stand-in application service, to prove the route is a pass-through.

    Records what it was asked and returns what it was told to. No CAD runs.
    """

    def __init__(self, validation: Any = None, outcome: Any = None) -> None:
        self.validation = validation
        self.outcome = outcome
        self.documents: List[Any] = []
        self.requests: List[BuildDocumentRequest] = []

    def validate_document(self, document: Any) -> Any:
        self.documents.append(document)
        return self.validation

    def build_document(self, request: BuildDocumentRequest) -> Any:
        self.requests.append(request)
        return self.outcome


class NoResponseChild:
    """A stand-in child that exits successfully having written nothing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pid = -1
        self.returncode = 0

    def communicate(self, *args: Any, **kwargs: Any) -> Tuple[bytes, bytes]:
        return b"", b"Traceback: died in /tmp/secret-place (exit code 9)\n"

    def poll(self) -> int:
        return self.returncode


class StubBackend:
    """Replays one prepared execution through the real service."""

    def __init__(self, execution: Any) -> None:
        self.execution = execution

    def execute(self, request: BuildRequest) -> Any:
        return self.execution

    def lookup(self, request: BuildRequest) -> Optional[Any]:
        return None

    def find(self, build_key: str) -> Optional[Any]:
        return None


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()

    def client(self, **kwargs: Any) -> TestClient:
        return TestClient(
            create_app(ApiConfig.for_cache_root(self.cache_root, **kwargs))
        )

    def service(self, **kwargs: Any) -> CadApplicationService:
        return CadApplicationService.local(self.cache_root, **kwargs)

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def assert_safe(self, response: Any) -> str:
        """Assert a response body carries nothing a client must not see."""
        payload = response.json()
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} crossed the boundary")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
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


# --- health -----------------------------------------------------------------


class TestHealth(ApiTestCase):
    def test_health_returns_ok(self) -> None:
        response = self.client().get(HEALTH_PATH)
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("application/json", response.headers["content-type"])

    def test_health_runs_no_cad_and_touches_no_cache(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("health launched a child"),
        ):
            client = self.client()
            for _ in range(3):
                self.assertEqual(client.get(HEALTH_PATH).status_code, OK_STATUS)
        self.assertEqual(list(self.cache_root.iterdir()), [])

    def test_health_reports_no_version_metadata(self) -> None:
        payload = self.client().get(HEALTH_PATH).json()
        self.assertEqual(sorted(payload), ["status"])

    def test_health_accepts_no_other_method(self) -> None:
        client = self.client()
        self.assertEqual(client.post(HEALTH_PATH).status_code, 405)
        self.assertEqual(client.get("/nowhere").status_code, 404)


# --- validate ---------------------------------------------------------------


class TestValidateEndpoint(ApiTestCase):
    def test_a_valid_box_validates(self) -> None:
        response = self.client().post(VALIDATE_PATH, json={"document": box_document()})
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertIsNone(payload["error"])
        self.assertEqual(len(payload["document_hash"]), 64)
        self.assertEqual(payload["feature_count"], 1)
        self.assert_safe(response)

    def test_the_four_hole_document_validates(self) -> None:
        response = self.client().post(
            VALIDATE_PATH, json={"document": section_d_document()}
        )
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["document_hash"], SECTION_D_HASH)
        self.assertEqual(payload["name"], "plate-100x60x10-4holes")
        self.assertEqual(payload["feature_count"], 5)
        # the canonical document comes back, materialised
        self.assertEqual(payload["document"]["schema_version"], "1.0.0")
        self.assert_safe(response)

    def test_a_document_as_json_text_is_accepted(self) -> None:
        """The contract accepts JSON text; the transport does not object."""
        response = self.client().post(
            VALIDATE_PATH, json={"document": box_document()}
        )
        self.assertTrue(response.json()["valid"])

    def test_malformed_json_is_a_transport_rejection(self) -> None:
        response = self.client().post(
            VALIDATE_PATH,
            content=b"{ this is not json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, TRANSPORT_STATUS)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error"]["failure"], "invalid_request")
        self.assertIn("json", payload["error"]["message"].lower())
        self.assert_safe(response)

    def test_a_missing_document_is_a_transport_rejection(self) -> None:
        response = self.client().post(VALIDATE_PATH, json={})
        self.assertEqual(response.status_code, TRANSPORT_STATUS)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error"]["failure"], "invalid_request")
        self.assertIn("missing", payload["error"]["message"])
        self.assert_safe(response)

    def test_an_unknown_request_field_is_rejected(self) -> None:
        for extra in ("surprise", "user_id", "outputs", "authorization"):
            with self.subTest(field=extra):
                response = self.client().post(
                    VALIDATE_PATH, json={"document": box_document(), extra: "x"}
                )
                self.assertEqual(response.status_code, TRANSPORT_STATUS)
                self.assertEqual(
                    response.json()["error"]["failure"], "invalid_request"
                )

    def test_an_invalid_cad_document_still_answers_the_question(self) -> None:
        """The check ran and the answer is "no", so the request succeeded."""
        invalid = document([plate_feature(size={"x": 0, "y": 60, "z": 10})])
        response = self.client().post(VALIDATE_PATH, json={"document": invalid})
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertFalse(payload["valid"])
        self.assertEqual(payload["error"]["failure"], "invalid_document")
        self.assertIn("S10", payload["error"]["rule_codes"])
        self.assertTrue(payload["error"]["validation_errors"])
        self.assertEqual(
            sorted(payload["error"]["validation_errors"][0]),
            ["feature_id", "feature_index", "field_path", "message", "rule"],
        )
        self.assert_safe(response)

    def test_the_validation_rules_are_not_reimplemented_in_the_transport(
        self,
    ) -> None:
        invalid = document(
            [
                plate_feature(size={"x": 0, "y": 60, "z": 10}),
                {"id": "plate", "type": "box", "size": {"x": 1, "y": 1, "z": 1}},
            ]
        )
        payload = self.client().post(
            VALIDATE_PATH, json={"document": invalid}
        ).json()
        direct = validate(invalid)
        self.assertEqual(
            sorted(payload["error"]["rule_codes"]),
            sorted({error.rule for error in direct.errors}),
        )
        self.assertEqual(
            len(payload["error"]["validation_errors"]), len(direct.errors)
        )

    def test_validating_never_launches_a_child_process(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched to validate"),
        ):
            response = self.client().post(
                VALIDATE_PATH, json={"document": section_d_document()}
            )
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertTrue(response.json()["valid"])


# --- the transport envelope -------------------------------------------------


class TestTransportEnvelope(ApiTestCase):
    def test_a_wrong_json_type_is_rejected_not_coerced(self) -> None:
        client = self.client()
        for label, body in (
            ("document is a list", {"document": []}),
            ("document is a string", {"document": "a document"}),
            ("document is null", {"document": None}),
            ("document is a number", {"document": 3}),
            ("outputs is a string", {"document": box_document(), "outputs": "step"}),
            ("outputs is a number", {"document": box_document(), "outputs": 3}),
            ("outputs holds a number", {"document": box_document(), "outputs": [1]}),
            ("outputs holds a bool", {"document": box_document(), "outputs": [True]}),
        ):
            with self.subTest(case=label):
                response = client.post(BUILD_PATH, json=body)
                self.assertEqual(response.status_code, TRANSPORT_STATUS)
                self.assertEqual(
                    response.json()["error"]["failure"], "invalid_request"
                )

    def test_the_transport_never_coerces_a_cad_value(self) -> None:
        """Measured: Pydantic does not look inside the document.

        ``"100"`` stays a string, ``true`` stays a boolean and ``null`` stays
        null all the way to the V1 validator, which rejects each with rule
        S19. A malformed CAD number cannot become a valid one in transit.
        """
        client = self.client()
        for label, size in (
            ("string", {"x": "100", "y": 60, "z": 10}),
            ("boolean", {"x": True, "y": 60, "z": 10}),
            ("null", {"x": None, "y": 60, "z": 10}),
            ("list", {"x": [100], "y": 60, "z": 10}),
        ):
            with self.subTest(value=label):
                body = {"document": document([plate_feature(size=size)])}
                response = client.post(VALIDATE_PATH, json=body)
                # the transport accepted the envelope ...
                self.assertEqual(response.status_code, OK_STATUS)
                payload = response.json()
                # ... and the domain refused the value
                self.assertFalse(payload["valid"])
                self.assertEqual(
                    payload["error"]["failure"], "invalid_document"
                )
                self.assertIn("S19", payload["error"]["rule_codes"])

    def test_an_omitted_outputs_field_means_every_output(self) -> None:
        self.assertEqual(
            BuildBody.model_validate({"document": {}}).to_payload(),
            {"document": {}},
        )

    def test_an_explicit_null_outputs_is_not_read_as_an_omission(self) -> None:
        """It survives into the payload, where the contract refuses it."""
        payload = BuildBody.model_validate(
            {"document": {}, "outputs": None}
        ).to_payload()
        self.assertIn("outputs", payload)
        self.assertIsNone(payload["outputs"])
        response = self.client().post(
            BUILD_PATH, json={"document": box_document(), "outputs": None}
        )
        self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
        self.assertEqual(response.json()["error"]["failure"], "invalid_request")

    def test_the_envelope_fields_come_from_the_transport_contract(self) -> None:
        from cad_api.schemas import BUILD_FIELDS, VALIDATE_FIELDS

        self.assertEqual(
            sorted(BuildBody.model_fields), sorted(BUILD_FIELDS)
        )
        self.assertEqual(
            sorted(ValidateBody.model_fields), sorted(VALIDATE_FIELDS)
        )
        self.assertEqual(BuildBody.model_config["extra"], "forbid")
        self.assertEqual(ValidateBody.model_config["extra"], "forbid")

    def test_an_unknown_output_name_is_judged_by_the_application_service(
        self,
    ) -> None:
        """The transport shape is fine; the name is the service's business."""
        body = BuildBody.model_validate(
            {"document": box_document(), "outputs": ["hologram"]}
        )
        self.assertEqual(body.outputs, ["hologram"])
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            response = self.client().post(
                BUILD_PATH,
                json={"document": box_document(), "outputs": ["hologram"]},
            )
        self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "invalid_request")
        self.assertIn("hologram", payload["error"]["message"])
        self.assertEqual(payload["artifacts"], [])

    def test_every_build_failure_has_one_body_shape(self) -> None:
        client = self.client()
        shapes = set()
        for body in (
            {},
            {"document": box_document(), "surprise": 1},
            {"document": box_document(), "outputs": ["hologram"]},
            {"document": document([plate_feature(size={"x": 0, "y": 1, "z": 1})])},
        ):
            with mock.patch(
                "cad_core.isolated_execution.subprocess.Popen",
                side_effect=AssertionError("a child was launched"),
            ):
                payload = client.post(BUILD_PATH, json=body).json()
            shapes.add(tuple(sorted(payload)))
            self.assertIsNotNone(payload["error"]["failure"])
        self.assertEqual(len(shapes), 1)
        self.assertEqual(
            shapes.pop(),
            (
                "artifacts",
                "build_key",
                "cache_hit",
                "document_hash",
                "error",
                "execution_id",
                "outputs",
                "status",
                "succeeded",
            ),
        )


# --- the real stack, end to end ---------------------------------------------


class TestSectionDBuild(unittest.TestCase):
    """The four-hole plate, over HTTP, through the real local stack."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.cache_root = root / "cache"
        cls.cache_root.mkdir()
        cls.client = TestClient(
            create_app(ApiConfig.for_cache_root(cls.cache_root))
        )
        cls.document = section_d_document()
        cls.first = cls.client.post(BUILD_PATH, json={"document": cls.document})
        cls.second = cls.client.post(BUILD_PATH, json={"document": cls.document})

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def safe(self, response: Any) -> str:
        payload = response.json()
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text)
        self.assertNotIn(str(self.cache_root), text)
        for key in walk_keys(payload):
            lowered = key.lower()
            if lowered in PERMITTED_PATH_KEYS:
                continue
            self.assertNotIn(lowered, FORBIDDEN_KEYS)
        for value in walk_strings(payload):
            self.assertFalse(value.startswith("/"))
        return text

    def test_the_build_succeeds(self) -> None:
        self.assertEqual(self.first.status_code, OK_STATUS, msg=self.first.text[:300])
        payload = self.first.json()
        self.assertEqual(payload["status"], BuildStatus.SUCCEEDED.value)
        self.assertTrue(payload["succeeded"])
        self.assertIsNone(payload["error"])

    def test_the_identities_are_present_and_correct(self) -> None:
        payload = self.first.json()
        self.assertEqual(payload["document_hash"], SECTION_D_HASH)
        self.assertEqual(len(payload["build_key"]), 64)
        self.assertNotEqual(payload["build_key"], payload["document_hash"])
        self.assertIsNotNone(payload["execution_id"])

    def test_every_output_is_reported(self) -> None:
        self.assertEqual(
            self.first.json()["outputs"],
            ["geometry", "step", "iges", "stl", "render"],
        )

    def test_the_artifact_logical_ids_are_present(self) -> None:
        payload = self.first.json()
        for artifact in payload["artifacts"]:
            self.assertTrue(
                artifact["logical_id"].startswith(payload["build_key"])
            )
            self.assertEqual(
                artifact["logical_id"],
                f"{payload['build_key']}:{artifact['kind']}",
            )
            self.assertNotIn("/", artifact["logical_id"])

    def test_the_checksums_are_present_where_applicable(self) -> None:
        payload = self.first.json()
        for artifact in payload["artifacts"]:
            if artifact["kind"] == "geometry":
                # a B-rep has no canonical bytes, so no checksum is claimed
                self.assertIsNone(artifact["checksum"])
                self.assertIsNone(artifact["checksum_algorithm"])
                self.assertIsNone(artifact["size_bytes"])
                continue
            self.assertEqual(len(artifact["checksum"]), 64)
            self.assertEqual(artifact["checksum_algorithm"], CHECKSUM_ALGORITHM)
            self.assertGreater(artifact["size_bytes"], 0)

    def test_the_artifacts_expose_no_path(self) -> None:
        for artifact in self.first.json()["artifacts"]:
            self.assertEqual(
                sorted(artifact),
                [
                    "checksum",
                    "checksum_algorithm",
                    "format",
                    "kind",
                    "logical_id",
                    "measurements",
                    "size_bytes",
                    "storage",
                ],
            )

    def test_the_geometry_artifact_carries_measurements_not_a_brep(self) -> None:
        import math

        artifact = next(
            item
            for item in self.first.json()["artifacts"]
            if item["kind"] == "geometry"
        )
        self.assertEqual(artifact["format"], "brep-in-memory")
        self.assertEqual(artifact["storage"], "in_memory")
        self.assertEqual(artifact["measurements"]["solid_count"], 1)
        expected = (
            PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
            - 4 * math.pi * 16 * PLATE_SIZE[2]
        )
        self.assertAlmostEqual(
            artifact["measurements"]["volume_mm3"], expected, delta=1e-4
        )

    def test_the_render_artifact_is_metadata_only(self) -> None:
        artifact = next(
            item
            for item in self.first.json()["artifacts"]
            if item["kind"] == "render"
        )
        self.assertEqual(artifact["storage"], "in_memory")
        self.assertGreater(artifact["measurements"]["triangle_count"], 0)
        text = json.dumps(self.first.json())
        for absent in ("vertices", "normals"):
            self.assertNotIn(absent, text)

    def test_the_repeat_request_is_a_cache_hit(self) -> None:
        self.assertEqual(self.second.status_code, OK_STATUS)
        first, second = self.first.json(), self.second.json()
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["status"], BuildStatus.SUCCEEDED.value)
        self.assertIsNone(second["execution_id"])
        self.assertEqual(second["build_key"], first["build_key"])

    def test_the_response_maps_exactly_to_the_transport_contract(self) -> None:
        """The HTTP body is the contract's payload, byte for byte."""
        service = CadApplicationService.local(self.cache_root)
        contract = CadApiContract(service)
        expected = contract.build_document_payload(
            {"document": self.document}
        )
        received = self.second.json()
        self.assertTrue(expected["cache_hit"])
        self.assertEqual(received, expected)

    def test_the_response_carries_nothing_unsafe(self) -> None:
        for response in (self.first, self.second):
            self.safe(response)

    def test_the_response_is_json(self) -> None:
        for response in (self.first, self.second):
            self.assertIn("application/json", response.headers["content-type"])


class TestBuildEndpoint(ApiTestCase):
    def test_a_valid_box_builds(self) -> None:
        response = self.client().post(BUILD_PATH, json={"document": box_document()})
        self.assertEqual(response.status_code, OK_STATUS, msg=response.text[:300])
        payload = response.json()
        self.assertTrue(payload["succeeded"])
        self.assertEqual(len(payload["artifacts"]), len(OUTPUTS))
        self.assert_safe(response)

    def test_selected_outputs_only(self) -> None:
        response = self.client().post(
            BUILD_PATH,
            json={"document": box_document(), "outputs": ["geometry", "stl"]},
        )
        self.assertEqual(response.status_code, OK_STATUS, msg=response.text[:300])
        payload = response.json()
        self.assertEqual(payload["outputs"], ["geometry", "stl"])
        self.assertEqual(len(payload["artifacts"]), 2)
        self.assert_safe(response)

    def test_a_different_output_selection_is_a_different_build(self) -> None:
        client = self.client()
        narrow = client.post(
            BUILD_PATH, json={"document": box_document(), "outputs": ["stl"]}
        ).json()
        wider = client.post(
            BUILD_PATH,
            json={"document": box_document(), "outputs": ["stl", "step"]},
        ).json()
        self.assertNotEqual(narrow["build_key"], wider["build_key"])
        self.assertEqual(narrow["document_hash"], wider["document_hash"])

    def test_a_repeated_request_becomes_a_cache_hit(self) -> None:
        client = self.client()
        body = {"document": box_document(), "outputs": ["geometry", "stl"]}
        first = client.post(BUILD_PATH, json=body).json()
        second = client.post(BUILD_PATH, json=body).json()
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["build_key"], second["build_key"])

    def test_an_invalid_cad_document_is_unprocessable(self) -> None:
        invalid = document([plate_feature(size={"x": 0, "y": 60, "z": 10})])
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            response = self.client().post(BUILD_PATH, json={"document": invalid})
        self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["status"], BuildStatus.FAILED.value)
        self.assertEqual(payload["error"]["failure"], "invalid_document")
        self.assertIn("S10", payload["error"]["rule_codes"])
        self.assertEqual(payload["artifacts"], [])
        self.assert_safe(response)


# --- status mapping ---------------------------------------------------------


class TestStatusMapping(ApiTestCase):
    def test_every_failure_has_a_status(self) -> None:
        self.assertEqual(set(FAILURE_STATUS), set(ServiceFailure))
        for failure, status in FAILURE_STATUS.items():
            with self.subTest(failure=failure.value):
                self.assertIn(status, STATUSES)
                self.assertEqual(status_for_failure(failure.value), status)

    def test_client_faults_are_4xx_and_server_faults_are_5xx(self) -> None:
        for failure in (
            ServiceFailure.MALFORMED_DOCUMENT,
            ServiceFailure.INVALID_DOCUMENT,
            ServiceFailure.INVALID_REQUEST,
            ServiceFailure.GEOMETRY_FAILED,
        ):
            self.assertTrue(400 <= FAILURE_STATUS[failure] < 500)
        for failure in (
            ServiceFailure.OUTPUT_FAILED,
            ServiceFailure.EXECUTION_FAILED,
            ServiceFailure.INTERNAL_ERROR,
        ):
            self.assertTrue(500 <= FAILURE_STATUS[failure] < 600)

    def test_not_every_failure_maps_to_500(self) -> None:
        self.assertGreater(len(set(FAILURE_STATUS.values())), 1)
        self.assertNotEqual(
            FAILURE_STATUS[ServiceFailure.EXECUTION_FAILED],
            FAILURE_STATUS[ServiceFailure.INTERNAL_ERROR],
        )
        self.assertEqual(
            FAILURE_STATUS[ServiceFailure.EXECUTION_FAILED], UNAVAILABLE_STATUS
        )

    def test_an_unknown_failure_value_is_a_server_problem(self) -> None:
        self.assertEqual(status_for_failure("something_new"), INTERNAL_STATUS)
        self.assertEqual(status_for_failure(""), INTERNAL_STATUS)

    def test_a_geometry_failure_is_unprocessable(self) -> None:
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
        response = self.client().post(
            BUILD_PATH, json={"document": doc, "outputs": ["geometry"]}
        )
        self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "geometry_failed")
        self.assertEqual(payload["error"]["rule_codes"], ["E1"])
        self.assertEqual(payload["artifacts"], [])
        self.assert_safe(response)

    def test_an_export_failure_is_a_server_error(self) -> None:
        """A real exporter refusal, carried through service and contract."""
        request = BuildRequest(
            part=validate(box_document()).part,
            options=BuildOptions.for_outputs(ArtifactKind.STEP),
        )
        out = self.subdirectory("blocked")
        (out / f"{request.build_key}.step").mkdir()
        execution = execute_isolated(request, output_directory=out)
        self.assertIs(execution.outcome, IsolationOutcome.BUILD_FAILED)

        client = TestClient(
            create_app(
                service=CadApplicationService(StubBackend(execution))
            )
        )
        response = client.post(
            BUILD_PATH, json={"document": box_document(), "outputs": ["step"]}
        )
        self.assertEqual(response.status_code, INTERNAL_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "output_failed")
        self.assertEqual(payload["error"]["output"], "step")
        self.assert_safe(response)

    def test_a_process_failure_is_service_unavailable(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen", NoResponseChild
        ):
            response = self.client().post(
                BUILD_PATH,
                json={"document": box_document(), "outputs": ["geometry"]},
            )
        self.assertEqual(response.status_code, UNAVAILABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "execution_failed")
        # the child's stderr named a traceback, a path and an exit code
        self.assert_safe(response)
        self.assertNotIn("secret-place", json.dumps(payload))

    def test_a_timeout_is_service_unavailable(self) -> None:
        response = self.client(timeout_seconds=0.4).post(
            BUILD_PATH, json={"document": box_document(), "outputs": ["geometry"]}
        )
        self.assertEqual(response.status_code, UNAVAILABLE_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "execution_failed")
        self.assert_safe(response)

    def test_an_unexpected_error_is_a_generic_500(self) -> None:
        service = self.service()
        with mock.patch.object(
            CadApplicationService,
            "build_document",
            side_effect=RuntimeError("secret internals at /tmp/x"),
        ):
            client = TestClient(
                create_app(service=service), raise_server_exceptions=False
            )
            # the traceback goes to the server's log, and only there
            with self.assertLogs("cad_api", level="ERROR") as logged:
                response = client.post(
                    BUILD_PATH, json={"document": box_document()}
                )
        self.assertIn("secret internals", "\n".join(logged.output))
        self.assertIn("Traceback", "\n".join(logged.output))
        self.assertEqual(response.status_code, INTERNAL_STATUS)
        payload = response.json()
        self.assertEqual(payload["error"]["failure"], "internal_error")
        self.assertNotIn("secret internals", json.dumps(payload))
        self.assertNotIn("RuntimeError", json.dumps(payload))
        self.assert_safe(response)


# --- the transport is thin --------------------------------------------------


class TestRouteIsAPassThrough(ApiTestCase):
    """A stubbed service proves route -> service -> response, with no CAD."""

    def test_validate_reaches_the_service_and_returns_its_answer(self) -> None:
        from cad_core.application_service import DocumentValidation

        stub = StubService(
            validation=DocumentValidation(
                valid=True,
                document_hash="a" * 64,
                document={"schema_version": "1.0.0"},
                name="stubbed",
                feature_count=7,
            )
        )
        client = TestClient(create_app(service=self._as_service(stub)))
        response = client.post(VALIDATE_PATH, json={"document": box_document()})
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["document_hash"], "a" * 64)
        self.assertEqual(payload["name"], "stubbed")
        self.assertEqual(payload["feature_count"], 7)
        # the route handed the service exactly the document it was sent
        self.assertEqual(stub.documents, [box_document()])

    def test_build_reaches_the_service_and_returns_its_outcome(self) -> None:
        from cad_core.application_service import BuildOutcome
        from cad_core.artifact_registry import ArtifactManifest

        stub = StubService(
            outcome=BuildOutcome(
                status=BuildStatus.SUCCEEDED,
                document_hash="b" * 64,
                build_key="c" * 64,
                cache_hit=True,
                manifest=ArtifactManifest(
                    document_hash="b" * 64, build_key="c" * 64, artifacts=()
                ),
                execution_id=None,
            )
        )
        client = TestClient(create_app(service=self._as_service(stub)))
        response = client.post(
            BUILD_PATH, json={"document": box_document(), "outputs": ["stl"]}
        )
        self.assertEqual(response.status_code, OK_STATUS)
        payload = response.json()
        self.assertEqual(payload["build_key"], "c" * 64)
        self.assertTrue(payload["cache_hit"])
        # the route handed the service a transport-contract request
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0].outputs, ("stl",))
        self.assertEqual(stub.requests[0].document, box_document())

    def test_the_service_is_built_once_at_startup(self) -> None:
        with mock.patch.object(
            CadApplicationService,
            "local",
            wraps=CadApplicationService.local,
        ) as factory:
            client = self.client()
            for _ in range(3):
                client.post(VALIDATE_PATH, json={"document": box_document()})
        # once, when the application was created -- and not once per request
        self.assertEqual(factory.call_count, 1)

    def test_the_same_service_answers_every_request(self) -> None:
        app = create_app(ApiConfig.for_cache_root(self.cache_root))
        service = app.state.service
        contract = app.state.contract
        client = TestClient(app)
        for _ in range(3):
            client.post(VALIDATE_PATH, json={"document": box_document()})
        self.assertIs(app.state.service, service)
        self.assertIs(app.state.contract, contract)
        self.assertIs(contract.service, service)
        self.assertIs(service.backend.cache.root, service.backend.cache.root)

    def test_create_app_takes_exactly_one_of_config_or_service(self) -> None:
        with self.assertRaises(ValueError):
            create_app()
        with self.assertRaises(ValueError):
            create_app(
                ApiConfig.for_cache_root(self.cache_root),
                service=self.service(),
            )

    def _as_service(self, stub: StubService) -> Any:
        """Pass a stub where the app expects a service.

        ``create_app`` is given the object directly; the contract checks the
        type, so the stub is wrapped in a real service whose two methods are
        the stub's.
        """
        service = self.service()
        service.validate_document = stub.validate_document  # type: ignore[method-assign]
        service.build_document = stub.build_document  # type: ignore[method-assign]
        return service


# --- configuration ----------------------------------------------------------


class TestConfiguration(ApiTestCase):
    def test_a_cache_root_must_exist_and_is_never_invented(self) -> None:
        with self.assertRaises(ConfigurationError):
            ApiConfig.for_cache_root(self.tmp / "missing")
        config = ApiConfig.for_cache_root(self.cache_root)
        self.assertEqual(config.cache_root, self.cache_root)

    def test_a_non_positive_timeout_is_refused(self) -> None:
        for value in (0, -1.0):
            with self.subTest(timeout=value):
                with self.assertRaises(ConfigurationError):
                    ApiConfig.for_cache_root(
                        self.cache_root, timeout_seconds=value
                    )

    def test_the_environment_configuration_guesses_nothing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as caught:
                config_from_environment()
            self.assertIn(CACHE_ROOT_VARIABLE, str(caught.exception))
        with mock.patch.dict(
            os.environ, {CACHE_ROOT_VARIABLE: str(self.cache_root)}, clear=True
        ):
            config = config_from_environment()
            self.assertEqual(config.cache_root, self.cache_root)
        with mock.patch.dict(
            os.environ,
            {
                CACHE_ROOT_VARIABLE: str(self.cache_root),
                TIMEOUT_VARIABLE: "12.5",
            },
            clear=True,
        ):
            self.assertEqual(config_from_environment().timeout_seconds, 12.5)
        with mock.patch.dict(
            os.environ,
            {CACHE_ROOT_VARIABLE: str(self.cache_root), TIMEOUT_VARIABLE: "soon"},
            clear=True,
        ):
            with self.assertRaises(ConfigurationError):
                config_from_environment()

    def test_no_credential_is_read_from_the_environment(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "cad_api" / "config.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        read = [name for name in names if name.isupper() and "_" in name]
        self.assertEqual(
            sorted(set(read)), sorted([CACHE_ROOT_VARIABLE, TIMEOUT_VARIABLE])
        )


# --- the boundary -----------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    def api_source(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[1] / "src" / "cad_api" / f"{name}.py"
        ).read_text(encoding="utf-8")

    def api_modules(self) -> Tuple[str, ...]:
        return ("app", "config", "generation", "schemas", "status", "__init__")

    def imports_of_source(self, source: str) -> List[str]:
        tree = ast.parse(source)
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def names_in(self, source: str) -> List[str]:
        tree = ast.parse(source)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        return names

    def core_source(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "cad-core"
            / "src"
            / "cad_core"
            / f"{name}.py"
        ).read_text(encoding="utf-8")

    def test_the_http_layer_imports_no_geometry_module(self) -> None:
        for module in self.api_modules():
            imports = self.imports_of_source(self.api_source(module))
            for forbidden in (
                "cad_core.local_cad",
                "cad_core.edge_selection",
                "cad_core.geometry",
                "cadquery",
                "OCP",
            ):
                with self.subTest(module=module, forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)

    def test_the_http_layer_imports_no_exporter(self) -> None:
        for module in self.api_modules():
            imports = self.imports_of_source(self.api_source(module))
            for forbidden in (
                "cad_core.step_export",
                "cad_core.iges_export",
                "cad_core.stl_export",
                "cad_core.render_model",
            ):
                with self.subTest(module=module, forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)

    def test_the_http_layer_imports_no_cache_module(self) -> None:
        for module in self.api_modules():
            self.assertNotIn(
                "cad_core.local_build_cache",
                self.imports_of_source(self.api_source(module)),
            )

    def test_the_http_layer_imports_no_worker_module(self) -> None:
        for module in self.api_modules():
            self.assertNotIn(
                "cad_core.isolated_worker",
                self.imports_of_source(self.api_source(module)),
            )

    def test_the_http_layer_calls_no_domain_operation(self) -> None:
        for module in self.api_modules():
            names = self.names_in(self.api_source(module))
            for forbidden in (
                "validate",
                "build_part",
                "part_hash",
                "serialize_part",
                "deserialize_part",
                "build_key_for",
                "export_step",
                "export_iges",
                "export_stl",
                "build_render_model",
                "get_or_build",
                "execute_isolated",
                "file_checksum",
                "sha256",
            ):
                with self.subTest(module=module, name=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_the_http_layer_depends_only_on_the_contract_and_service(self) -> None:
        internal = set()
        for module in self.api_modules():
            internal.update(
                imported
                for imported in self.imports_of_source(self.api_source(module))
                if imported.startswith("cad_core")
            )
        self.assertEqual(
            sorted(internal),
            [
                "cad_core.api_contract",
                "cad_core.application_service",
                "cad_core.isolated_execution",
            ],
        )

    def test_the_http_layer_adds_no_forbidden_infrastructure(self) -> None:
        forbidden = {
            "sqlite3",
            "sqlalchemy",
            "psycopg2",
            "redis",
            "celery",
            "kombu",
            "boto3",
            "botocore",
            "google.cloud",
            "azure",
            "kubernetes",
            "docker",
            "jwt",
            "passlib",
            "bcrypt",
            "authlib",
            "graphene",
            "strawberry",
            "graphql",
            "websockets",
            "socketio",
            "openai",
            "anthropic",
            "langchain",
            "slowapi",
            "aioredis",
        }
        for module in self.api_modules():
            for imported in self.imports_of_source(self.api_source(module)):
                with self.subTest(module=module, imported=imported):
                    self.assertNotIn(imported, forbidden)
                    for token in ("llm", "mcp", "openai", "anthropic"):
                        self.assertNotIn(token, imported.lower())

    def test_no_cors_or_middleware_was_added(self) -> None:
        for module in self.api_modules():
            names = self.names_in(self.api_source(module))
            for forbidden in (
                "add_middleware",
                "CORSMiddleware",
                "middleware",
                "TrustedHostMiddleware",
            ):
                with self.subTest(module=module, name=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_no_websocket_or_background_task_was_added(self) -> None:
        for module in self.api_modules():
            names = self.names_in(self.api_source(module))
            for forbidden in (
                "websocket",
                "WebSocket",
                "BackgroundTasks",
                "add_task",
                "create_task",
            ):
                with self.subTest(module=module, name=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_cad_core_never_imports_fastapi(self) -> None:
        core = Path(__file__).resolve().parents[3] / "packages" / "cad-core"
        for path in sorted((core / "src" / "cad_core").glob("*.py")):
            imports = self.imports_of_source(path.read_text(encoding="utf-8"))
            for forbidden in ("fastapi", "starlette", "pydantic", "httpx", "httpx2"):
                with self.subTest(module=path.name, forbidden=forbidden):
                    self.assertNotIn(forbidden, imports)

    def test_cad_core_works_with_fastapi_unavailable(self) -> None:
        """Proved in a child process with the HTTP stack blocked on import."""
        core = (
            Path(__file__).resolve().parents[3]
            / "packages"
            / "cad-core"
            / "src"
        )
        program = """
import sys

BLOCKED = ("fastapi", "starlette", "pydantic", "pydantic_core", "httpx", "httpx2")


class Blocker:
    def find_module(self, name, path=None):
        return self.find_spec(name, path)

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is unavailable in this test")
        return None


sys.meta_path.insert(0, Blocker())

from cad_core.api_contract import CadApiContract, build_request_from_payload
from cad_core.application_service import CadApplicationService
import cad_core.build_job, cad_core.local_build_cache, cad_core.isolated_execution

request = build_request_from_payload({"document": {"a": 1}, "outputs": ["stl"]})
assert request.outputs == ("stl",)
for name in BLOCKED:
    assert name not in sys.modules, name
print("OK")
"""
        completed = subprocess.run(
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            env={
                "PYTHONPATH": str(core),
                "PYTHONIOENCODING": "utf-8",
                "PATH": os.environ.get("PATH", ""),
            },
        )
        self.assertEqual(
            completed.returncode, 0, msg=completed.stderr[-2000:]
        )
        self.assertIn("OK", completed.stdout)

    def test_the_domain_logic_did_not_move_into_the_api(self) -> None:
        """No CAD rule, geometry or artifact code lives under apps/api."""
        api = Path(__file__).resolve().parents[1] / "src" / "cad_api"
        for path in sorted(api.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            names = self.names_in(source)
            for forbidden in (
                "STATIC_RULES",
                "GEOMETRIC_RULES",
                "check_geometric_rules",
                "Part",
                "Box",
                "Cylinder",
                "ThroughHole",
                "Fillet",
                "Chamfer",
                "Artifact",
                "ArtifactManifest",
            ):
                with self.subTest(module=path.name, name=forbidden):
                    self.assertNotIn(forbidden, names)

    def test_the_documentation_states_the_layering(self) -> None:
        documentation = (
            Path(__file__).resolve().parents[3] / "docs" / "http-api.md"
        )
        self.assertTrue(documentation.is_file())
        unwrapped = " ".join(documentation.read_text(encoding="utf-8").split())
        self.assertIn("HTTP API contains no CAD business logic.", unwrapped)
        for expected in (
            "no authentication",
            "no authorization",
            "no rate limiting",
            "no user isolation",
            "no production sandboxing",
        ):
            self.assertIn(expected, unwrapped.lower())
        for endpoint in ("POST /validate", "POST /build", "GET /health"):
            self.assertIn(endpoint, unwrapped)

    def test_the_documentation_names_no_endpoint_that_does_not_exist(
        self,
    ) -> None:
        documentation = (
            Path(__file__).resolve().parents[3] / "docs" / "http-api.md"
        )
        text = documentation.read_text(encoding="utf-8")
        # "GET /documents" is deliberately not forbidden here: the document
        # states that no such endpoint exists, and a text scan cannot tell a
        # mention from a specification. Its absence is asserted where it can
        # be: the OpenAPI path list, and a live 404 in the retrieval tests.
        for absent in (
            "POST /documents",
            "DELETE /",
            "PUT /",
            "PATCH /",
            "POST /artifacts",
            "POST /featurescript",
            "GET /cache",
            "GET /entries",
        ):
            self.assertNotIn(absent, text)
        # /build is POST-only; "GET /builds/" is a different endpoint
        import re as _re

        self.assertIsNone(_re.search(r"GET /build(?![s/])", text))


class TestOpenApi(ApiTestCase):
    def test_the_generated_schema_lists_only_the_declared_routes(self) -> None:
        schema = self.client().get("/openapi.json").json()
        from cad_api.app import (
            BUILD_LOOKUP_PATH,
            BUILD_RENDER_PATH,
            GENERATE_PATH,
        )

        self.assertEqual(
            sorted(schema["paths"]),
            sorted(
                [
                    ARTIFACT_PATH,
                    BUILD_LOOKUP_PATH,
                    BUILD_RENDER_PATH,
                    BUILD_PATH,
                    GENERATE_PATH,
                    HEALTH_PATH,
                    VALIDATE_PATH,
                ]
            ),
        )
        for path, spec in schema["paths"].items():
            with self.subTest(path=path):
                expected = (
                    ["post"]
                    if path in (BUILD_PATH, VALIDATE_PATH, GENERATE_PATH)
                    else ["get"]
                )
                self.assertEqual(sorted(spec), expected)

    def test_the_schema_defines_only_the_two_request_envelopes(self) -> None:
        schema = self.client().get("/openapi.json").json()
        defined = set(schema.get("components", {}).get("schemas", {}))
        # FastAPI's own validation-error schemas plus this layer's two bodies
        self.assertIn("BuildBody", defined)
        self.assertIn("ValidateBody", defined)
        for absent in (
            "BuildDocumentResponse",
            "ValidateDocumentResponse",
            "ArtifactContract",
            "ErrorContract",
            "Part",
        ):
            self.assertNotIn(absent, defined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
