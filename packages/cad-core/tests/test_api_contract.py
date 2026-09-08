"""Unit tests for the transport-neutral external contract.

What these tests are mostly about:

* **what crosses** -- every payload is walked recursively and asserted to
  contain no filesystem path, no traceback, no workspace name, no kernel
  vocabulary, and no key that looks like a credential, an auth field or an
  LLM setting;
* **what survives** -- the six failure classifications, the validator's
  structured errors, the artifact identities, sizes and checksums, and the
  three separate identity fields;
* **that nothing is reimplemented** -- the build request DTO *is* the
  application service's, the output names *are* the artifact kinds, the
  statuses *are* ``BuildStatus``, and the contract module imports neither the
  validator nor the geometry kernel.

The Section D four-hole plate is mapped end to end: canonical document →
request DTO → application service → build outcome → response DTO.
"""

from __future__ import annotations

import ast
import json
import unittest
import unittest.mock as mock
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from cad_core import validate
from cad_core.api_contract import (
    BUILD_REQUEST_FIELDS,
    FAILURES,
    OUTPUTS,
    STATUSES,
    SUBSTITUTED_MESSAGES,
    VALIDATE_REQUEST_FIELDS,
    ArtifactContract,
    BuildDocumentRequest,
    BuildDocumentResponse,
    CadApiContract,
    ContractError,
    ErrorContract,
    ValidateDocumentRequest,
    ValidateDocumentResponse,
    ValidationErrorContract,
    artifact_contract,
    build_request_from_payload,
    build_request_to_payload,
    build_response,
    error_contract,
    validate_request_from_payload,
    validate_request_to_payload,
    validation_response,
)
from cad_core.application_service import (
    BuildDocumentRequest as ServiceBuildRequest,
    CadApplicationService,
    ServiceFailure,
)
from cad_core.artifact_registry import (
    CHECKSUM_ALGORITHM,
    ArtifactKind,
    ArtifactManifest,
    ArtifactStorage,
)
from cad_core.build_job import (
    BuildOptions,
    BuildRequest,
    BuildStatus,
    build_key_for,
)
from cad_core.isolated_execution import IsolationOutcome, execute_isolated
from cad_core.local_build_cache import ENTRIES_DIRNAME
from cad_core.render_model import RENDER_FORMAT_VERSION
from cad_core.serialization import part_from_json, part_hash

#: Section D's canonical document hash, pinned since Stage 15.
SECTION_D_HASH = (
    "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc"
)

PLATE_SIZE = (100.0, 60.0, 10.0)

HOLE_DIAMETER = 8.0

#: Substrings that must never appear in a payload's text.
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
    "gettempdir",
    "stderr",
    "BRepFilletAPI",
)

#: Substrings that must never appear in a payload key, at any depth.
#:
#: ``path`` is deliberately **not** here: ``field_path`` is the validator's
#: own name for a position inside the CAD document (``features[0].size.x``),
#: which is not a filesystem path. Path-shaped keys are checked exactly, in
#: :data:`FORBIDDEN_KEYS`.
FORBIDDEN_KEY_TOKENS: Tuple[str, ...] = (
    "user",
    "token",
    "auth",
    "credential",
    "secret",
    "password",
    "tenant",
    "session",
    "quota",
    "rate_limit",
    "api_key",
    "apikey",
    "prompt",
    "llm",
    "mcp",
    "openai",
    "anthropic",
    "traceback",
    "diagnostic",
    "stderr",
    "stdout",
    "workspace",
    "directory",
    "exception",
    "api_version",
    "contract_version",
)

#: Keys that must never appear exactly, at any depth.
FORBIDDEN_KEYS: Tuple[str, ...] = (
    "path",
    "paths",
    "file_path",
    "filepath",
    "location",
    "output_directory",
    "cache_root",
    "file_extension",
    "exit_code",
    "child_pid",
    "execution_outcome",
    "build_failure",
    "stage",
)

#: The one key that legitimately contains "path".
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


def walk_keys(payload: Any) -> Iterator[str]:
    """Every mapping key in a payload, at any depth."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_keys(item)


def walk_strings(payload: Any) -> Iterator[str]:
    """Every string value in a payload, at any depth."""
    if isinstance(payload, dict):
        for value in payload.values():
            yield from walk_strings(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from walk_strings(item)
    elif isinstance(payload, str):
        yield payload


class NoResponseChild:
    """A stand-in child that exits successfully having written nothing."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pid = -1
        self.returncode = 0

    def communicate(self, *args: Any, **kwargs: Any) -> Tuple[bytes, bytes]:
        return b"", b"Traceback: the child died in /tmp/secret-place\n"

    def poll(self) -> int:
        return self.returncode


class StubBackend:
    """Replays one prepared execution, to map a real lower-layer failure."""

    def __init__(self, execution: Any) -> None:
        self.execution = execution

    def execute(self, request: BuildRequest) -> Any:
        return self.execution

    def lookup(self, request: BuildRequest) -> Optional[Any]:
        return None


class ContractTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()

    def api(self, **kwargs: Any) -> CadApiContract:
        return CadApiContract(
            CadApplicationService.local(self.cache_root, **kwargs)
        )

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def assert_safe(self, payload: Any) -> str:
        """Assert a payload carries nothing a client must not see."""
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text, msg=f"{token!r} crossed the boundary")
        self.assertNotIn(str(self.tmp), text)
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn(str(self.cache_root), text)
        for key in walk_keys(payload):
            lowered = key.lower()
            self.assertNotIn(lowered, FORBIDDEN_KEYS, msg=f"key {key!r} crossed")
            if lowered in PERMITTED_PATH_KEYS:
                continue
            for token in FORBIDDEN_KEY_TOKENS:
                self.assertNotIn(
                    token, lowered, msg=f"key {key!r} looks like {token!r}"
                )
        for value in walk_strings(payload):
            self.assertFalse(
                value.startswith("/"), msg=f"{value!r} looks like a path"
            )
        return text


# --- requests ---------------------------------------------------------------


class TestRequestContract(ContractTestCase):
    def test_a_validate_payload_round_trips(self) -> None:
        payload = {"document": section_d_document()}
        request = validate_request_from_payload(payload)
        self.assertIsInstance(request, ValidateDocumentRequest)
        self.assertEqual(request.document, section_d_document())
        self.assertEqual(validate_request_to_payload(request), payload)

    def test_a_build_payload_round_trips(self) -> None:
        payload = {"document": section_d_document(), "outputs": ["geometry", "step"]}
        request = build_request_from_payload(payload)
        self.assertIsInstance(request, BuildDocumentRequest)
        self.assertEqual(request.outputs, ("geometry", "step"))
        self.assertEqual(build_request_to_payload(request), payload)

    def test_the_build_request_is_the_application_services_own(self) -> None:
        self.assertIs(BuildDocumentRequest, ServiceBuildRequest)
        self.assertEqual(
            sorted(BuildDocumentRequest.__dataclass_fields__),
            ["document", "outputs"],
        )
        self.assertEqual(BUILD_REQUEST_FIELDS, ("document", "outputs"))
        self.assertEqual(VALIDATE_REQUEST_FIELDS, ("document",))

    def test_a_build_payload_may_omit_the_outputs(self) -> None:
        request = build_request_from_payload({"document": section_d_document()})
        self.assertEqual(request.outputs, OUTPUTS)

    def test_json_text_is_accepted_as_a_document(self) -> None:
        text = json.dumps(section_d_document())
        request = build_request_from_payload({"document": text})
        self.assertEqual(request.document, text)
        self.assertEqual(
            build_request_to_payload(request), {"document": text, "outputs": list(OUTPUTS)}
        )

    def test_the_document_is_not_canonicalized_by_the_contract(self) -> None:
        """Stage 15 owns canonicalization; a second form must not exist."""
        terse = document(
            [{"id": "plate", "type": "box", "size": {"x": 1, "y": 2, "z": 3}}]
        )
        payload = build_request_to_payload(
            build_request_from_payload({"document": terse})
        )
        self.assertEqual(payload["document"], terse)
        self.assertNotIn("position", payload["document"]["features"][0])

    def test_the_contract_does_not_alias_the_callers_document(self) -> None:
        """A deep copy, so neither side can surprise the other later."""
        payload = {"document": section_d_document(), "outputs": ["step"]}
        before = json.dumps(payload, sort_keys=True)
        request = build_request_from_payload(payload)
        request.document["features"].clear()  # type: ignore[union-attr]
        self.assertEqual(json.dumps(payload, sort_keys=True), before)
        # ... and the other direction
        request = build_request_from_payload(
            {"document": section_d_document(), "outputs": ["step"]}
        )
        emitted = build_request_to_payload(request)
        emitted["document"]["features"].clear()
        self.assertEqual(len(request.document["features"]), 5)  # type: ignore[index]

    def test_a_payload_with_an_unknown_field_is_refused(self) -> None:
        for payload in (
            {"document": section_d_document(), "surprise": 1},
            {"document": section_d_document(), "user_id": "u1"},
            {"document": section_d_document(), "outputs": ["step"], "auth": "x"},
        ):
            with self.subTest(payload=sorted(payload)):
                with self.assertRaises(ContractError):
                    build_request_from_payload(payload)

    def test_a_payload_without_a_document_is_refused(self) -> None:
        for payload in ({}, {"outputs": ["step"]}):
            with self.subTest(payload=sorted(payload)):
                with self.assertRaises(ContractError):
                    build_request_from_payload(payload)
                with self.assertRaises(ContractError):
                    validate_request_from_payload(payload)

    def test_a_payload_that_is_not_an_object_is_refused(self) -> None:
        for payload in ([], "document", 3, None):
            with self.subTest(payload=str(payload)):
                with self.assertRaises(ContractError):
                    build_request_from_payload(payload)

    def test_a_document_of_the_wrong_type_is_refused(self) -> None:
        for value in (3, None, [1, 2], True):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ContractError):
                    build_request_from_payload({"document": value})

    def test_outputs_must_be_a_list(self) -> None:
        for outputs in ("step", 3, {"step": True}):
            with self.subTest(outputs=str(outputs)):
                with self.assertRaises(ContractError):
                    build_request_from_payload(
                        {"document": section_d_document(), "outputs": outputs}
                    )

    def test_an_unknown_output_name_is_judged_by_the_service(self) -> None:
        """The envelope is this layer's business, the content the service's."""
        request = build_request_from_payload(
            {"document": section_d_document(), "outputs": ["hologram"]}
        )
        self.assertEqual(request.outputs, ("hologram",))
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            response = self.api().build_document(request)
        self.assertFalse(response.succeeded)
        self.assertEqual(response.error.failure, "invalid_request")
        self.assertIn("hologram", response.error.message)

    def test_a_bad_envelope_becomes_an_invalid_request_response(self) -> None:
        payload = self.api().build_document_payload(
            {"document": section_d_document(), "surprise": 1}
        )
        self.assertEqual(payload["status"], BuildStatus.FAILED.value)
        self.assertFalse(payload["succeeded"])
        self.assertEqual(payload["error"]["failure"], "invalid_request")
        self.assert_safe(payload)
        validated = self.api().validate_document_payload({"nope": 1})
        self.assertFalse(validated["valid"])
        self.assertEqual(validated["error"]["failure"], "invalid_request")

    def test_the_output_names_are_the_existing_artifact_kinds(self) -> None:
        self.assertEqual(OUTPUTS, tuple(kind.value for kind in ArtifactKind))
        self.assertEqual(
            OUTPUTS, ("geometry", "step", "iges", "stl", "render")
        )

    def test_a_request_carries_no_transport_or_identity_field(self) -> None:
        payload = build_request_to_payload(
            BuildDocumentRequest(document=section_d_document(), outputs=("step",))
        )
        self.assertEqual(sorted(payload), ["document", "outputs"])
        self.assert_safe({"outputs": payload["outputs"]})


# --- validation responses ---------------------------------------------------


class TestValidationResponse(ContractTestCase):
    def test_a_valid_document_maps_to_a_success_response(self) -> None:
        response = self.api().validate_document(
            ValidateDocumentRequest(document=section_d_document())
        )
        self.assertIsInstance(response, ValidateDocumentResponse)
        self.assertTrue(response.valid)
        self.assertIsNone(response.error)
        self.assertEqual(response.document_hash, SECTION_D_HASH)
        self.assertEqual(response.name, "plate-100x60x10-4holes")
        self.assertEqual(response.feature_count, 5)
        payload = response.to_payload()
        self.assertEqual(
            sorted(payload),
            ["document", "document_hash", "error", "feature_count", "name", "valid"],
        )
        self.assert_safe(payload)

    def test_the_canonical_document_comes_back(self) -> None:
        terse = document(
            [{"id": "plate", "type": "box", "size": {"x": 1, "y": 2, "z": 3}}]
        )
        response = self.api().validate_document(
            ValidateDocumentRequest(document=terse)
        )
        self.assertEqual(
            response.document["features"][0]["position"],
            {"x": 0.0, "y": 0.0, "z": 0.0},
        )
        self.assertEqual(
            response.document_hash, part_hash(part_from_json(json.dumps(terse)))
        )

    def test_malformed_json_maps_to_a_malformed_document_response(self) -> None:
        response = self.api().validate_document(
            ValidateDocumentRequest(document="{ broken")
        )
        self.assertFalse(response.valid)
        self.assertIsNone(response.document_hash)
        self.assertIsNone(response.document)
        self.assertEqual(response.error.failure, "malformed_document")
        self.assertEqual(response.error.rule_codes, ())
        self.assertEqual(response.error.validation_errors, ())
        self.assert_safe(response.to_payload())

    def test_an_invalid_document_maps_with_its_structured_errors(self) -> None:
        invalid = document(
            [
                plate_feature(size={"x": 0, "y": 60, "z": 10}),
                {"id": "plate", "type": "box", "size": {"x": 1, "y": 1, "z": 1}},
            ]
        )
        response = self.api().validate_document(
            ValidateDocumentRequest(document=invalid)
        )
        self.assertFalse(response.valid)
        self.assertEqual(response.error.failure, "invalid_document")
        direct = validate(invalid)
        self.assertEqual(
            sorted(response.error.rule_codes),
            sorted({error.rule for error in direct.errors}),
        )
        self.assertEqual(
            len(response.error.validation_errors), len(direct.errors)
        )
        for reported, original in zip(
            response.error.validation_errors, direct.errors
        ):
            self.assertIsInstance(reported, ValidationErrorContract)
            self.assertEqual(reported.rule, original.rule)
            self.assertEqual(reported.message, original.message)
            self.assertEqual(reported.feature_id, original.feature_id)
            self.assertEqual(reported.field_path, original.field_path)
            self.assertEqual(reported.feature_index, original.feature_index)
        payload = response.to_payload()
        self.assertEqual(
            sorted(payload["error"]["validation_errors"][0]),
            ["feature_id", "feature_index", "field_path", "message", "rule"],
        )
        self.assert_safe(payload)

    def test_the_typed_part_never_crosses(self) -> None:
        result = CadApplicationService.local(self.cache_root).validate_document(
            section_d_document()
        )
        self.assertIsNotNone(result.part)
        response = validation_response(result)
        self.assertNotIn("part", response.to_payload())
        self.assertFalse(hasattr(response, "part"))

    def test_validating_maps_without_building(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            payload = self.api().validate_document_payload(
                {"document": section_d_document()}
            )
        self.assertTrue(payload["valid"])
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())


# --- the Section D document, end to end -------------------------------------


class TestSectionDContract(unittest.TestCase):
    """Canonical document → request DTO → service → outcome → response DTO."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.cache_root = root / "cache"
        cls.cache_root.mkdir()
        cls.api = CadApiContract(CadApplicationService.local(cls.cache_root))
        cls.document = section_d_document()
        cls.request = build_request_from_payload(
            {"document": json.dumps(cls.document)}
        )
        cls.first = cls.api.build_document(cls.request)
        cls.second = cls.api.build_document(cls.request)
        cls.first_payload = cls.first.to_payload()
        cls.second_payload = cls.second.to_payload()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def safe(self, payload: Any) -> str:
        text = json.dumps(payload)
        for token in FORBIDDEN_TEXT:
            self.assertNotIn(token, text)
        self.assertNotIn(str(self.cache_root), text)
        self.assertNotIn(self.cache_root.parent.name, text)
        for key in walk_keys(payload):
            lowered = key.lower()
            self.assertNotIn(lowered, FORBIDDEN_KEYS)
            if lowered in PERMITTED_PATH_KEYS:
                continue
            for token in FORBIDDEN_KEY_TOKENS:
                self.assertNotIn(token, lowered)
        for value in walk_strings(payload):
            self.assertFalse(value.startswith("/"))
        return text

    def test_the_build_succeeds(self) -> None:
        self.assertIsInstance(self.first, BuildDocumentResponse)
        self.assertTrue(
            self.first.succeeded,
            msg=self.first.error.message if self.first.error else "",
        )
        self.assertEqual(self.first.status, BuildStatus.SUCCEEDED.value)
        self.assertIn(self.first.status, STATUSES)
        self.assertIsNone(self.first.error)

    def test_the_document_hash_is_the_existing_canonical_hash(self) -> None:
        self.assertEqual(self.first.document_hash, SECTION_D_HASH)
        self.assertEqual(
            self.first.document_hash,
            part_hash(part_from_json(json.dumps(self.document))),
        )

    def test_the_build_key_is_stable_and_is_the_existing_one(self) -> None:
        expected = build_key_for(
            part_from_json(json.dumps(self.document)),
            BuildOptions.for_outputs(*ArtifactKind),
        )
        self.assertEqual(self.first.build_key, expected)
        self.assertEqual(self.second.build_key, expected)
        self.assertNotEqual(self.first.build_key, self.first.document_hash)

    def test_the_first_call_is_not_a_cache_hit(self) -> None:
        self.assertFalse(self.first.cache_hit)
        self.assertIsNotNone(self.first.execution_id)
        self.assertEqual(self.first.status, BuildStatus.SUCCEEDED.value)

    def test_the_repeat_call_is_a_cache_hit_with_the_same_status(self) -> None:
        self.assertTrue(self.second.cache_hit)
        self.assertEqual(self.second.status, BuildStatus.SUCCEEDED.value)
        self.assertTrue(self.second.succeeded)
        # nothing executed, so there is no execution to identify
        self.assertIsNone(self.second.execution_id)
        # and a cache hit is not a status of its own
        self.assertEqual(STATUSES, ("succeeded", "failed"))

    def test_the_three_identities_stay_separate(self) -> None:
        self.assertNotEqual(self.first.document_hash, self.first.build_key)
        self.assertNotIn(self.first.execution_id, self.first.build_key)
        self.assertNotIn(self.first.execution_id, self.first.document_hash)
        self.assertEqual(self.second.document_hash, self.first.document_hash)
        self.assertEqual(self.second.build_key, self.first.build_key)

    def test_the_artifacts_are_exactly_the_requested_outputs(self) -> None:
        self.assertEqual(
            self.first.outputs, ("geometry", "step", "iges", "stl", "render")
        )
        self.assertEqual(
            tuple(artifact.kind for artifact in self.first.artifacts),
            self.first.outputs,
        )
        self.assertEqual(self.second.outputs, self.first.outputs)

    def test_the_step_artifact_is_represented(self) -> None:
        artifact = self.first.artifact("step")
        self.assertEqual(artifact.format, "step")
        self.assertEqual(artifact.storage, ArtifactStorage.FILE.value)
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(len(artifact.checksum), 64)
        self.assertEqual(artifact.checksum_algorithm, CHECKSUM_ALGORITHM)
        self.assertEqual(
            artifact.logical_id, f"{self.first.build_key}:step"
        )

    def test_the_iges_artifact_is_represented(self) -> None:
        artifact = self.first.artifact("iges")
        self.assertEqual(artifact.format, "iges-brep")
        self.assertEqual(artifact.storage, ArtifactStorage.FILE.value)
        self.assertGreater(artifact.size_bytes, 0)
        self.assertEqual(artifact.checksum_algorithm, CHECKSUM_ALGORITHM)
        self.assertEqual(artifact.logical_id, f"{self.first.build_key}:iges")

    def test_the_stl_artifact_is_represented(self) -> None:
        artifact = self.first.artifact("stl")
        self.assertEqual(artifact.format, "stl-binary")
        self.assertEqual(artifact.storage, ArtifactStorage.FILE.value)
        self.assertGreater(artifact.measurements["triangle_count"], 0)
        self.assertTrue(artifact.measurements["is_structurally_consistent"])
        self.assertEqual(artifact.checksum_algorithm, CHECKSUM_ALGORITHM)

    def test_the_render_artifact_is_metadata_only(self) -> None:
        artifact = self.first.artifact("render")
        self.assertEqual(artifact.format, "render-model")
        self.assertEqual(artifact.storage, ArtifactStorage.IN_MEMORY.value)
        self.assertEqual(
            artifact.measurements["format_version"], RENDER_FORMAT_VERSION
        )
        self.assertGreater(artifact.measurements["triangle_count"], 0)
        self.assertGreater(artifact.measurements["vertex_count"], 0)
        self.assertEqual(artifact.measurements["units"], "mm")
        # the payload itself does not cross
        payload = json.dumps(artifact.to_payload())
        for absent in ("vertices", "normals", "triangles\":"):
            self.assertNotIn(absent, payload)
        self.assertEqual(artifact.checksum_algorithm, CHECKSUM_ALGORITHM)

    def test_the_geometry_artifact_exposes_no_kernel_object(self) -> None:
        artifact = self.first.artifact("geometry")
        self.assertEqual(artifact.format, "brep-in-memory")
        self.assertEqual(artifact.storage, ArtifactStorage.IN_MEMORY.value)
        # no bytes exist for a B-rep, so none are claimed
        self.assertIsNone(artifact.size_bytes)
        self.assertIsNone(artifact.checksum)
        self.assertIsNone(artifact.checksum_algorithm)
        # measurements only, and they are the real ones
        import math

        expected = (
            PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
            - 4 * math.pi * (HOLE_DIAMETER / 2.0) ** 2 * PLATE_SIZE[2]
        )
        self.assertEqual(artifact.measurements["solid_count"], 1)
        self.assertTrue(artifact.measurements["is_solid"])
        self.assertAlmostEqual(
            artifact.measurements["volume_mm3"], expected, delta=1e-4
        )
        self.safe(artifact.to_payload())

    def test_no_artifact_exposes_a_filesystem_path(self) -> None:
        for artifact in self.first.artifacts:
            payload = artifact.to_payload()
            self.assertNotIn("path", payload)
            self.assertEqual(
                sorted(payload),
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
            self.assertNotIn("/", artifact.logical_id)
            self.assertTrue(artifact.logical_id.startswith(self.first.build_key))

    def test_the_response_payload_carries_nothing_unsafe(self) -> None:
        for payload in (self.first_payload, self.second_payload):
            self.safe(payload)

    def test_the_response_payload_is_json_compatible(self) -> None:
        for payload in (self.first_payload, self.second_payload):
            self.assertEqual(json.loads(json.dumps(payload)), payload)
        self.assertEqual(
            sorted(self.first_payload),
            [
                "artifacts",
                "build_key",
                "cache_hit",
                "document_hash",
                "error",
                "execution_id",
                "outputs",
                "status",
                "succeeded",
            ],
        )

    def test_the_cached_response_matches_the_built_one_apart_from_the_cache(
        self,
    ) -> None:
        first = dict(self.first_payload)
        second = dict(self.second_payload)
        for volatile in ("cache_hit", "execution_id"):
            first.pop(volatile)
            second.pop(volatile)
        self.assertEqual(first, second)

    def test_the_render_payload_and_the_brep_never_cross(self) -> None:
        text = json.dumps(self.first_payload)
        for absent in ("vertices", "normals", "brep_bytes", "shape"):
            self.assertNotIn(absent, text)


# --- failure classification -------------------------------------------------


class TestFailureContract(ContractTestCase):
    def test_the_taxonomy_is_the_application_services_own(self) -> None:
        self.assertEqual(
            FAILURES, tuple(failure.value for failure in ServiceFailure)
        )
        for required in (
            "malformed_document",
            "invalid_document",
            "invalid_request",
            "geometry_failed",
            "output_failed",
            "execution_failed",
        ):
            self.assertIn(required, FAILURES)
        self.assertEqual(len(set(FAILURES)), len(FAILURES))

    def test_malformed_and_invalid_documents_are_different(self) -> None:
        api = self.api()
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            malformed = api.build_document_payload({"document": "{ broken"})
            invalid = api.build_document_payload(
                {
                    "document": document(
                        [plate_feature(size={"x": 0, "y": 1, "z": 1})]
                    )
                }
            )
            bad_request = api.build_document_payload(
                {"document": section_d_document(), "outputs": ["hologram"]}
            )
        self.assertEqual(malformed["error"]["failure"], "malformed_document")
        self.assertEqual(invalid["error"]["failure"], "invalid_document")
        self.assertEqual(bad_request["error"]["failure"], "invalid_request")
        self.assertEqual(
            len({
                malformed["error"]["failure"],
                invalid["error"]["failure"],
                bad_request["error"]["failure"],
            }),
            3,
        )
        for payload in (malformed, invalid, bad_request):
            self.assertFalse(payload["succeeded"])
            self.assertEqual(payload["artifacts"], [])
            self.assertEqual(payload["outputs"], [])
            self.assert_safe(payload)

    def test_a_geometry_failure_keeps_its_classification_and_rules(self) -> None:
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
        payload = self.api().build_document_payload(
            {"document": doc, "outputs": ["geometry"]}
        )
        self.assertEqual(payload["status"], BuildStatus.FAILED.value)
        self.assertEqual(payload["error"]["failure"], "geometry_failed")
        self.assertEqual(payload["error"]["rule_codes"], ["E1"])
        # the engine was producing the geometry when it refused, and the
        # contract keeps that -- it names an output, never a path
        self.assertEqual(payload["error"]["output"], "geometry")
        self.assertEqual(payload["error"]["validation_errors"], [])
        # not a document failure
        self.assertNotEqual(payload["error"]["failure"], "invalid_document")
        self.assert_safe(payload)

    def test_an_export_failure_keeps_its_own_classification(self) -> None:
        """A real exporter refusal, mapped through service and contract."""
        request = BuildRequest(
            part=validate(document([plate_feature()])).part,
            options=BuildOptions.for_outputs(ArtifactKind.STEP),
        )
        out = self.subdirectory("blocked")
        (out / f"{request.build_key}.step").mkdir()
        execution = execute_isolated(request, output_directory=out)
        self.assertIs(execution.outcome, IsolationOutcome.BUILD_FAILED)

        api = CadApiContract(
            CadApplicationService(StubBackend(execution))
        )
        payload = api.build_document_payload(
            {"document": document([plate_feature()]), "outputs": ["step"]}
        )
        self.assertEqual(payload["error"]["failure"], "output_failed")
        self.assertEqual(payload["error"]["output"], "step")
        # distinct from geometry and document failures
        self.assertNotEqual(payload["error"]["failure"], "geometry_failed")
        self.assertNotEqual(payload["error"]["failure"], "invalid_document")
        self.assert_safe(payload)

    def test_a_process_failure_becomes_an_execution_failure(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen", NoResponseChild
        ):
            payload = self.api().build_document_payload(
                {"document": document([plate_feature()]), "outputs": ["geometry"]}
            )
        self.assertEqual(payload["error"]["failure"], "execution_failed")
        self.assertEqual(payload["status"], BuildStatus.FAILED.value)
        self.assertEqual(payload["artifacts"], [])
        # the child's stderr said "Traceback" and named a path; neither crossed
        self.assert_safe(payload)
        text = json.dumps(payload)
        self.assertNotIn("secret-place", text)
        # and no process detail is disclosed: not the outcome, not the exit
        # code, not the child. The service's own message named an exit code,
        # so the contract substitutes a client-facing sentence.
        for absent in ("process_failed", "exit code", "child", "isolated"):
            self.assertNotIn(absent, text)
        self.assertEqual(
            payload["error"]["message"],
            SUBSTITUTED_MESSAGES[ServiceFailure.EXECUTION_FAILED.value],
        )

    def test_a_timeout_becomes_an_execution_failure(self) -> None:
        payload = self.api(timeout_seconds=0.4).build_document_payload(
            {"document": document([plate_feature()]), "outputs": ["geometry"]}
        )
        self.assertEqual(payload["error"]["failure"], "execution_failed")
        self.assert_safe(payload)
        # a client learns execution failed, not that a child was killed
        text = json.dumps(payload)
        for absent in ("timed_out", "timeout", "child", "terminated", "second"):
            self.assertNotIn(absent, text)
        self.assertEqual(
            payload["error"]["message"],
            SUBSTITUTED_MESSAGES[ServiceFailure.EXECUTION_FAILED.value],
        )

    def test_a_process_failure_and_a_timeout_are_one_class_to_a_client(
        self,
    ) -> None:
        """Both are execution failures; how the process died stays internal."""
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen", NoResponseChild
        ):
            crashed = self.api().build_document_payload(
                {"document": document([plate_feature()]), "outputs": ["geometry"]}
            )
        timed_out = self.api(timeout_seconds=0.4).build_document_payload(
            {"document": document([plate_feature()]), "outputs": ["geometry"]}
        )
        self.assertEqual(
            crashed["error"]["failure"], timed_out["error"]["failure"]
        )
        self.assertEqual(crashed["error"]["failure"], "execution_failed")
        self.assertEqual(
            crashed["error"]["message"], timed_out["error"]["message"]
        )

    def test_the_error_payload_holds_only_the_documented_fields(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            payload = self.api().build_document_payload(
                {
                    "document": document(
                        [plate_feature(size={"x": 0, "y": 1, "z": 1})]
                    )
                }
            )
        self.assertEqual(
            sorted(payload["error"]),
            ["failure", "message", "output", "rule_codes", "validation_errors"],
        )
        # the internal fields the service kept are not in the contract
        for absent in ("build_failure", "execution_outcome", "stage"):
            self.assertNotIn(absent, payload["error"])

    def test_only_the_process_shaped_messages_are_substituted(self) -> None:
        """Every other failure keeps the service's own sentence verbatim."""
        self.assertEqual(
            sorted(SUBSTITUTED_MESSAGES),
            sorted(
                [
                    ServiceFailure.EXECUTION_FAILED.value,
                    ServiceFailure.INTERNAL_ERROR.value,
                ]
            ),
        )
        service = CadApplicationService.local(self.cache_root)
        for outputs, doc, failure in (
            (("geometry",), document([plate_feature(size={"x": 0, "y": 1, "z": 1})]),
             "invalid_document"),
            (("geometry",), "{ broken", "malformed_document"),
        ):
            with self.subTest(failure=failure):
                outcome = service.build_document(
                    ServiceBuildRequest(document=doc, outputs=outputs)
                )
                external = error_contract(outcome.error)
                self.assertEqual(external.failure, failure)
                self.assertEqual(external.message, outcome.error.message)
                self.assertNotIn(external.message, SUBSTITUTED_MESSAGES.values())

    def test_the_internal_error_fields_exist_but_do_not_cross(self) -> None:
        """The service keeps them; the contract drops them deliberately."""
        service = CadApplicationService.local(self.cache_root)
        outcome = service.build_document(
            ServiceBuildRequest(
                document=document([plate_feature(size={"x": 0, "y": 1, "z": 1})]),
                outputs=("geometry",),
            )
        )
        self.assertIsNotNone(outcome.error.build_failure)
        self.assertEqual(outcome.error.stage, "validation")
        external = error_contract(outcome.error)
        self.assertFalse(hasattr(external, "build_failure"))
        self.assertFalse(hasattr(external, "stage"))
        self.assertFalse(hasattr(external, "execution_outcome"))
        self.assertEqual(external.failure, "invalid_document")
        self.assertEqual(external.message, outcome.error.message)


# --- mapping properties -----------------------------------------------------


class TestMappingProperties(ContractTestCase):
    def test_the_same_outcome_maps_to_the_same_representation(self) -> None:
        service = CadApplicationService.local(self.cache_root)
        outcome = service.build_document(
            ServiceBuildRequest(
                document=document([plate_feature()]), outputs=("geometry", "stl")
            )
        )
        self.assertTrue(outcome.succeeded, msg=outcome.error and outcome.error.message)
        first = build_response(outcome)
        second = build_response(outcome)
        self.assertEqual(first, second)
        self.assertEqual(first.to_payload(), second.to_payload())
        self.assertEqual(
            json.dumps(first.to_payload(), sort_keys=True),
            json.dumps(second.to_payload(), sort_keys=True),
        )

    def test_the_same_validation_maps_to_the_same_representation(self) -> None:
        service = CadApplicationService.local(self.cache_root)
        result = service.validate_document(section_d_document())
        self.assertEqual(
            validation_response(result).to_payload(),
            validation_response(result).to_payload(),
        )

    def test_the_mappers_refuse_the_wrong_type(self) -> None:
        for mapper in (validation_response, build_response, artifact_contract, error_contract):
            with self.subTest(mapper=mapper.__name__):
                with self.assertRaises(ContractError):
                    mapper(object())  # type: ignore[arg-type]

    def test_an_artifact_maps_without_its_path(self) -> None:
        service = CadApplicationService.local(self.cache_root)
        outcome = service.build_document(
            ServiceBuildRequest(
                document=document([plate_feature()]), outputs=("geometry", "stl")
            )
        )
        for artifact in outcome.manifest.artifacts:
            external = artifact_contract(artifact)
            self.assertIsInstance(external, ArtifactContract)
            self.assertEqual(external.kind, artifact.kind.value)
            self.assertEqual(external.format, artifact.format)
            self.assertEqual(external.logical_id, artifact.logical_id)
            self.assertEqual(external.storage, artifact.storage.value)
            self.assertEqual(external.size_bytes, artifact.size_bytes)
            self.assertEqual(external.checksum, artifact.checksum)
            self.assertEqual(dict(external.measurements), dict(artifact.details))
            if artifact.path is not None:
                self.assertNotIn(artifact.path, json.dumps(external.to_payload()))

    def test_a_failed_build_maps_to_no_artifacts(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            response = self.api().build_document(
                BuildDocumentRequest(document="{ broken")
            )
        self.assertEqual(response.artifacts, ())
        self.assertEqual(response.outputs, ())
        self.assertIsNone(response.artifact("step"))
        self.assertFalse(response.succeeded)

    def test_the_contract_holds_no_service(self) -> None:
        with self.assertRaises(ContractError):
            CadApiContract(object())  # type: ignore[arg-type]
        api = self.api()
        self.assertIsInstance(api.service, CadApplicationService)


# --- versioning -------------------------------------------------------------


class TestVersioning(ContractTestCase):
    def test_no_api_version_is_introduced(self) -> None:
        import cad_core.api_contract as contract

        for name in dir(contract):
            lowered = name.lower()
            if "version" in lowered:
                self.assertNotIn("api", lowered)
                self.assertNotIn("contract", lowered)
                self.assertNotIn("protocol", lowered)

    def test_no_payload_carries_a_version_of_its_own(self) -> None:
        api = self.api()
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            payloads = [
                api.validate_document_payload({"document": section_d_document()}),
                api.build_document_payload({"document": "{ broken"}),
                build_request_to_payload(
                    BuildDocumentRequest(document=section_d_document())
                ),
            ]
        for payload in payloads:
            with self.subTest(payload=sorted(payload)):
                for key in walk_keys(payload):
                    if key == "schema_version":
                        continue  # the CAD document's own, inside the document
                    self.assertNotIn("version", key.lower())

    def test_the_cad_documents_own_version_travels_untouched(self) -> None:
        response = self.api().validate_document(
            ValidateDocumentRequest(document=section_d_document())
        )
        self.assertEqual(response.document["schema_version"], "1.0.0")
        # ... and nothing else in the payload names a version
        payload = response.to_payload()
        payload["document"] = None
        for key in walk_keys(payload):
            self.assertNotIn("version", key.lower())

    def test_the_four_existing_version_numbers_stay_separate(self) -> None:
        from cad_core.isolated_worker import IPC_PROTOCOL_VERSION
        from cad_core.local_build_cache import CACHE_SCHEMA_VERSION
        from cad_core.model import SCHEMA_VERSION

        for value in (
            SCHEMA_VERSION,
            RENDER_FORMAT_VERSION,
            CACHE_SCHEMA_VERSION,
            IPC_PROTOCOL_VERSION,
        ):
            self.assertEqual(value, "1.0.0")
        # equal today by coincidence, and none of them is this contract's:
        # this contract has no version number at all
        import cad_core.api_contract as contract

        self.assertFalse(
            [name for name in dir(contract) if name.endswith("_VERSION")]
        )


# --- the boundary -----------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    def source(self, name: str) -> str:
        return (
            Path(__file__).resolve().parents[1] / "src" / "cad_core" / f"{name}.py"
        ).read_text(encoding="utf-8")

    def imports_of(self, name: str) -> List[str]:
        tree = ast.parse(self.source(name))
        found: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.append(node.module)
        return found

    def test_the_contract_imports_the_application_service(self) -> None:
        imports = self.imports_of("api_contract")
        self.assertIn("cad_core.application_service", imports)

    def test_the_contract_imports_no_transport_library(self) -> None:
        forbidden = {
            "fastapi",
            "starlette",
            "flask",
            "django",
            "pydantic",
            "graphene",
            "strawberry",
            "graphql",
            "requests",
            "httpx",
            "urllib",
            "urllib.request",
            "urllib.parse",
            "http",
            "http.client",
            "http.server",
            "socket",
            "socketserver",
            "ssl",
            "websocket",
            "websockets",
            "aiohttp",
            "tornado",
            "uvicorn",
            "grpc",
            "sqlite3",
            "sqlalchemy",
            "redis",
            "boto3",
            "jwt",
            "hashlib",
            "asyncio",
        }
        for imported in self.imports_of("api_contract"):
            self.assertNotIn(imported, forbidden)

    def test_the_contract_imports_no_llm_or_mcp_library(self) -> None:
        for imported in self.imports_of("api_contract"):
            for token in ("llm", "openai", "anthropic", "mcp"):
                self.assertNotIn(token, imported.lower())

    def test_the_contract_does_not_depend_on_the_kernel(self) -> None:
        imports = self.imports_of("api_contract")
        for forbidden in (
            "cadquery",
            "OCP",
            "cad_core.local_cad",
            "cad_core.edge_selection",
            "cad_core.step_export",
            "cad_core.iges_export",
            "cad_core.stl_export",
            "cad_core.validator",
            "cad_core.rules",
            "cad_core.geometry",
            "cad_core.featurescript",
            "cad_core.onshape_adapter",
        ):
            with self.subTest(module=forbidden):
                self.assertNotIn(forbidden, imports)

    def test_the_contract_depends_only_on_the_layers_below_it(self) -> None:
        internal = sorted(
            imported
            for imported in self.imports_of("api_contract")
            if imported.startswith("cad_core")
        )
        self.assertEqual(
            internal,
            [
                "cad_core.application_service",
                "cad_core.artifact_registry",
                "cad_core.build_job",
            ],
        )

    def test_no_lower_layer_depends_on_the_contract(self) -> None:
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
            "isolated_execution",
            "isolated_worker",
            "application_service",
            "__init__",
        ):
            with self.subTest(module=name):
                self.assertNotIn("cad_core.api_contract", self.imports_of(name))

    def test_the_contract_is_not_re_exported_from_the_package_root(self) -> None:
        self.assertNotIn("cad_core.api_contract", self.imports_of("__init__"))

    def test_the_contract_implements_no_business_logic(self) -> None:
        tree = ast.parse(self.source("api_contract"))
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        for forbidden in (
            "validate",
            "build_part",
            "part_hash",
            "serialize_part",
            "deserialize_part",
            "build_key_for",
            "export_step",
            "sha256",
            "new",
        ):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, names)

    def test_no_authentication_or_quota_concept_appears(self) -> None:
        tree = ast.parse(self.source("api_contract"))
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.append(node.attr.lower())
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                names.append(node.name.lower())
            elif isinstance(node, ast.arg):
                names.append(node.arg.lower())
        for forbidden in (
            "user",
            "token",
            "auth",
            "tenant",
            "session",
            "quota",
            "rate_limit",
            "api_key",
            "header",
            "endpoint",
            "route",
            "status_code",
            "url",
        ):
            with self.subTest(name=forbidden):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    msg=f"{forbidden!r} appears in the contract's code",
                )

    def test_the_documentation_separates_the_four_boundaries(self) -> None:
        documentation = (
            Path(__file__).resolve().parents[3] / "docs" / "api-contract.md"
        )
        self.assertTrue(documentation.is_file())
        unwrapped = " ".join(documentation.read_text(encoding="utf-8").split())
        self.assertIn(
            "CAD specification ≠ application service ≠ transport contract ≠ HTTP",
            unwrapped,
        )
        for expected in (
            "no api version",
            "logical_id",
            "cache_hit",
        ):
            self.assertIn(expected, unwrapped.lower())

    def test_the_documentation_names_no_http_endpoint(self) -> None:
        documentation = (
            Path(__file__).resolve().parents[3] / "docs" / "api-contract.md"
        )
        text = documentation.read_text(encoding="utf-8")
        for absent in ("POST /", "GET /", "PUT /", "DELETE /", "http://", "https://"):
            self.assertNotIn(absent, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
