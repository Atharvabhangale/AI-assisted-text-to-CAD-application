"""Unit tests for the application/domain service boundary.

What these tests are mostly about:

* **the boundary holds** -- a document arrives as JSON, leaves as a structured
  result, and nothing in between exposes a kernel object, a validation rule of
  its own, or a new identifier;
* **failures stay four different things** -- a document failure, a geometry
  failure, an output failure and an execution failure (a crash or a timeout)
  each map to their own application failure, carrying the lower layers' own
  classification rather than replacing it;
* **identity is borrowed, never invented** -- the same document and outputs
  give the same document hash and build key, and the second call is a cache
  hit that launches no child process.

The Section D document from the specification is built end to end, through the
service, the cache and an isolated child process.
"""

from __future__ import annotations

import ast
import copy
import json
import time
import unittest
import unittest.mock as mock
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cad_core.application_service import (
    BUILD_FAILURE_MAP,
    EXECUTION_OUTCOME_MAP,
    OUTPUT_NAMES,
    SERVICE_STATUSES,
    ApplicationServiceError,
    BuildDocumentRequest,
    BuildOutcome,
    CadApplicationService,
    DocumentValidation,
    LocalBuildBackend,
    ServiceError,
    ServiceFailure,
)
from cad_core.artifact_registry import ArtifactKind, ArtifactManifest, ArtifactStorage
from cad_core.build_job import (
    BuildFailure,
    BuildOptions,
    BuildRequest,
    BuildStatus,
    build_key_for,
)
from cad_core.isolated_execution import (
    IsolatedExecution,
    IsolationOutcome,
    execute_isolated,
)
from cad_core.local_build_cache import ENTRIES_DIRNAME, LocalBuildCache
from cad_core.model import Part
from cad_core.render_model import RenderModel
from cad_core.serialization import part_from_json, part_hash, serialize_part
from cad_core.step_export import read_step
from cad_core.iges_export import read_iges
from cad_core.stl_export import read_stl
from cad_core import validate

PLATE_SIZE = (100.0, 60.0, 10.0)

HOLE_DIAMETER = 8.0

#: Section D's four hole centres.
HOLE_CENTRES = ((10.0, 10.0), (90.0, 10.0), (10.0, 50.0), (90.0, 50.0))

VOLUME_TOLERANCE_MM3 = 1e-4


def section_d_document() -> Dict[str, Any]:
    """The worked example from ``docs/cad-specification.md`` Section D."""
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
            },
            {
                "id": "hole_front_left",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
                "axis": "+Z",
            },
            {
                "id": "hole_front_right",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 90, "y": 10, "z": 0},
                "axis": "+Z",
            },
            {
                "id": "hole_back_left",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 50, "z": 0},
                "axis": "+Z",
            },
            {
                "id": "hole_back_right",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 90, "y": 50, "z": 0},
                "axis": "+Z",
            },
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


class StubBackend:
    """A backend that replays one prepared execution.

    Used to feed the service a **real** lower-layer failure object that is
    awkward to provoke through the local backend, and, in passing, to show
    that the service depends on the two-method interface rather than on the
    local backend's identity.
    """

    def __init__(self, execution: Optional[IsolatedExecution]) -> None:
        self.execution = execution
        self.requests: List[BuildRequest] = []

    def execute(self, request: BuildRequest) -> IsolatedExecution:
        self.requests.append(request)
        assert self.execution is not None
        return self.execution

    def lookup(self, request: BuildRequest) -> Optional[IsolatedExecution]:
        return None


class NoResponseChild:
    """A stand-in child that exits successfully having written nothing.

    The crash case, without provoking the real kernel: the host must classify
    it as a process failure, and the service as an execution failure.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.pid = -1
        self.returncode = 0

    def communicate(self, *args: Any, **kwargs: Any) -> Tuple[bytes, bytes]:
        return b"", b"child died\n"

    def poll(self) -> int:
        return self.returncode


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()

    def service(self, **kwargs: Any) -> CadApplicationService:
        return CadApplicationService.local(self.cache_root, **kwargs)

    def subdirectory(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir(parents=True, exist_ok=True)
        return path


# --- validation -------------------------------------------------------------


class TestValidateDocument(ServiceTestCase):
    def test_a_valid_canonical_json_document_validates(self) -> None:
        result = self.service().validate_document(
            json.dumps(section_d_document())
        )
        self.assertIsInstance(result, DocumentValidation)
        self.assertTrue(result.valid)
        self.assertIsNone(result.error)
        self.assertEqual(result.name, "plate-100x60x10-4holes")
        self.assertEqual(result.feature_count, 5)
        self.assertEqual(len(result.document_hash), 64)

    def test_the_document_hash_is_the_existing_canonical_hash(self) -> None:
        text = json.dumps(section_d_document())
        result = self.service().validate_document(text)
        self.assertEqual(result.document_hash, part_hash(part_from_json(text)))
        # the Section D hash pinned in Stage 15
        self.assertEqual(
            result.document_hash,
            "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc",
        )

    def test_a_parsed_document_structure_is_accepted(self) -> None:
        result = self.service().validate_document(section_d_document())
        self.assertTrue(result.valid)
        self.assertEqual(
            result.document, serialize_part(part_from_json(json.dumps(section_d_document())))
        )

    def test_the_returned_document_is_the_canonical_form(self) -> None:
        terse = document([{"id": "plate", "type": "box", "size": {"x": 1, "y": 2, "z": 3}}])
        result = self.service().validate_document(terse)
        self.assertTrue(result.valid)
        # defaults are materialised, as Stage 15 requires
        self.assertEqual(
            result.document["features"][0]["position"],
            {"x": 0.0, "y": 0.0, "z": 0.0},
        )

    def test_invalid_json_is_rejected_as_malformed(self) -> None:
        result = self.service().validate_document("{ not json at all")
        self.assertFalse(result.valid)
        self.assertIsNone(result.document_hash)
        self.assertIsNone(result.part)
        self.assertIs(result.error.failure, ServiceFailure.MALFORMED_DOCUMENT)
        self.assertEqual(result.error.stage, "document")
        self.assertEqual(result.error.rule_codes, ())

    def test_json_that_is_not_an_object_is_rejected_as_malformed(self) -> None:
        for text in ("[]", "null", "3", '"a document"'):
            with self.subTest(text=text):
                result = self.service().validate_document(text)
                self.assertFalse(result.valid)
                self.assertIs(
                    result.error.failure, ServiceFailure.MALFORMED_DOCUMENT
                )

    def test_a_document_violating_the_specification_is_rejected(self) -> None:
        invalid = document([plate_feature(size={"x": 0, "y": 60, "z": 10})])
        result = self.service().validate_document(invalid)
        self.assertFalse(result.valid)
        self.assertIs(result.error.failure, ServiceFailure.INVALID_DOCUMENT)
        self.assertEqual(result.error.stage, "validation")
        self.assertIs(result.error.build_failure, BuildFailure.DOCUMENT_INVALID)
        self.assertIn("S10", result.error.rule_codes)
        self.assertTrue(result.error.validation_errors)
        self.assertEqual(
            result.error.validation_errors[0]["rule"],
            result.error.rule_codes[0],
        )

    def test_the_validator_is_the_existing_one(self) -> None:
        """Not a second implementation: the same errors, from one validator."""
        invalid = document(
            [
                plate_feature(size={"x": 0, "y": 60, "z": 10}),
                {"id": "plate", "type": "box", "size": {"x": 1, "y": 1, "z": 1}},
            ]
        )
        direct = validate(invalid)
        self.assertFalse(direct.valid)
        result = self.service().validate_document(invalid)
        self.assertEqual(
            sorted(result.error.rule_codes),
            sorted({error.rule for error in direct.errors}),
        )
        self.assertEqual(
            len(result.error.validation_errors), len(direct.errors)
        )

    def test_validating_never_builds_anything(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched to validate"),
        ):
            self.assertTrue(
                self.service().validate_document(section_d_document()).valid
            )
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_a_typed_part_is_not_the_service_boundary(self) -> None:
        part = validate(section_d_document()).part
        self.assertIsInstance(part, Part)
        with self.assertRaises(ApplicationServiceError) as caught:
            self.service().validate_document(part)
        self.assertIn("JSON", str(caught.exception))

    def test_a_document_of_the_wrong_type_is_a_usage_error(self) -> None:
        for value in (3, None, [1, 2], object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ApplicationServiceError):
                    self.service().validate_document(value)

    def test_the_build_key_is_available_without_building(self) -> None:
        result = self.service().validate_document(section_d_document())
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            key = result.build_key_for(OUTPUT_NAMES)
        self.assertEqual(
            key,
            build_key_for(result.part, BuildOptions.for_outputs(*ArtifactKind)),
        )

    def test_an_invalid_document_has_no_build_key(self) -> None:
        result = self.service().validate_document("{")
        with self.assertRaises(ApplicationServiceError):
            result.build_key_for(OUTPUT_NAMES)

    def test_a_validation_result_serializes_to_json(self) -> None:
        for candidate in (section_d_document(), "{", document([]),):
            with self.subTest(candidate=str(candidate)[:20]):
                payload = json.loads(
                    json.dumps(self.service().validate_document(candidate).to_dict())
                )
                self.assertIn("valid", payload)
                self.assertNotIn("part", payload)


# --- the Section D full document --------------------------------------------


class TestSectionDBuild(unittest.TestCase):
    """The specification's worked example, end to end through the service."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name)
        cls.cache_root = root / "cache"
        cls.cache_root.mkdir()
        cls.service = CadApplicationService.local(cls.cache_root)
        cls.document = section_d_document()
        cls.request = BuildDocumentRequest(document=json.dumps(cls.document))
        cls.first = cls.service.build_document(cls.request)
        cls.second = cls.service.build_document(cls.request)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._directory.cleanup()

    def test_the_document_builds(self) -> None:
        self.assertIs(
            self.first.status,
            BuildStatus.SUCCEEDED,
            msg=self.first.error.message if self.first.error else "",
        )
        self.assertTrue(self.first.succeeded)
        self.assertIsNone(self.first.error)

    def test_the_document_hash_is_returned(self) -> None:
        self.assertEqual(
            self.first.document_hash,
            "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc",
        )
        self.assertEqual(
            self.first.document_hash,
            part_hash(part_from_json(json.dumps(self.document))),
        )

    def test_the_build_key_is_returned(self) -> None:
        self.assertEqual(len(self.first.build_key), 64)
        self.assertEqual(
            self.first.build_key,
            build_key_for(
                part_from_json(json.dumps(self.document)),
                BuildOptions.for_outputs(*ArtifactKind),
            ),
        )
        self.assertNotEqual(self.first.build_key, self.first.document_hash)

    def test_the_manifest_is_returned(self) -> None:
        self.assertIsInstance(self.first.manifest, ArtifactManifest)
        self.assertEqual(
            self.first.produced_outputs(),
            ("geometry", "step", "iges", "stl", "render"),
        )
        self.assertEqual(self.first.manifest.build_key, self.first.build_key)
        self.assertEqual(
            self.first.manifest.document_hash, self.first.document_hash
        )

    def test_the_geometry_is_a_single_solid_with_four_holes(self) -> None:
        details = self.first.artifact("geometry").details
        self.assertTrue(details["is_solid"])
        self.assertEqual(details["solid_count"], 1)
        import math

        expected = (
            PLATE_SIZE[0] * PLATE_SIZE[1] * PLATE_SIZE[2]
            - 4 * math.pi * (HOLE_DIAMETER / 2.0) ** 2 * PLATE_SIZE[2]
        )
        self.assertAlmostEqual(
            details["volume_mm3"], expected, delta=VOLUME_TOLERANCE_MM3
        )
        self.assertEqual(
            details["bounding_box"]["size"],
            {"x": PLATE_SIZE[0], "y": PLATE_SIZE[1], "z": PLATE_SIZE[2]},
        )

    def test_the_step_output_is_a_real_solid(self) -> None:
        artifact = self.first.artifact("step")
        self.assertIs(artifact.storage, ArtifactStorage.FILE)
        imported = read_step(artifact.path)
        self.assertTrue(imported.is_solid())
        self.assertAlmostEqual(
            imported.volume(),
            self.first.artifact("geometry").details["volume_mm3"],
            delta=1e-3,
        )

    def test_the_iges_output_reads_back(self) -> None:
        artifact = self.first.artifact("iges")
        imported = read_iges(artifact.path)
        self.assertTrue(imported.is_solid())
        self.assertAlmostEqual(
            imported.volume(),
            self.first.artifact("geometry").details["volume_mm3"],
            delta=1e-3,
        )

    def test_the_stl_output_is_a_closed_mesh(self) -> None:
        artifact = self.first.artifact("stl")
        mesh = read_stl(artifact.path)
        self.assertEqual(
            artifact.details["triangle_count"], mesh.triangle_count()
        )
        self.assertGreater(mesh.triangle_count(), 0)

    def test_the_render_model_is_returned(self) -> None:
        self.assertIsInstance(self.first.render_model, RenderModel)
        details = self.first.artifact("render").details
        self.assertEqual(
            details["triangle_count"], self.first.render_model.triangle_count()
        )
        self.assertEqual(
            details["vertex_count"], self.first.render_model.vertex_count()
        )
        self.assertEqual(details["units"], "mm")

    def test_the_first_build_is_a_cache_miss_and_publishes(self) -> None:
        self.assertFalse(self.first.cache_hit)
        self.assertTrue(self.first.cache_published)
        self.assertIsNotNone(self.first.execution_id)

    def test_the_repeated_call_is_a_cache_hit(self) -> None:
        self.assertTrue(self.second.cache_hit)
        self.assertFalse(self.second.cache_published)
        self.assertIs(self.second.status, BuildStatus.SUCCEEDED)
        self.assertEqual(self.second.build_key, self.first.build_key)
        self.assertEqual(self.second.document_hash, self.first.document_hash)

    def test_the_artifacts_survive_the_service_boundary(self) -> None:
        for name in ("step", "iges", "stl"):
            with self.subTest(output=name):
                built = self.first.artifact(name)
                cached = self.second.artifact(name)
                self.assertEqual(built.logical_id, cached.logical_id)
                self.assertEqual(built.checksum, cached.checksum)
                self.assertEqual(built.size_bytes, cached.size_bytes)
                self.assertEqual(built.format, cached.format)
                self.assertEqual(built.file_extension, cached.file_extension)
                self.assertTrue(Path(cached.path).is_file())
                Path(cached.path).relative_to(self.cache_root)
        self.assertEqual(
            self.first.manifest.canonical_bytes(),
            self.second.manifest.canonical_bytes(),
        )

    def test_the_result_serializes_to_json(self) -> None:
        for outcome in (self.first, self.second):
            payload = json.loads(json.dumps(outcome.to_dict()))
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["build_key"], outcome.build_key)
            self.assertEqual(
                payload["manifest"], outcome.manifest.to_dict()
            )
            self.assertIsNone(payload["error"])
            # the render payload is described, not duplicated (Stage 16)
            self.assertNotIn("render_model", payload)
            self.assertNotIn("vertices", json.dumps(payload))

    def test_the_result_holds_no_kernel_object(self) -> None:
        serialized = json.dumps(self.first.to_dict())
        for token in ("cadquery", "OCP", "TopoDS", "Workplane", "object at"):
            self.assertNotIn(token, serialized)

    def test_no_absolute_path_appears_in_a_logical_identity(self) -> None:
        payload = self.first.to_dict()
        identities = [payload["build_key"], payload["document_hash"]] + [
            record["logical_id"] for record in payload["manifest"]["artifacts"]
        ]
        for identity in identities:
            self.assertNotIn("/", identity)
            self.assertNotIn(str(self.cache_root), identity)
            self.assertNotIn(self.cache_root.name, identity)
            self.assertNotIn(".step", identity)
        for record in payload["manifest"]["artifacts"]:
            self.assertTrue(record["logical_id"].startswith(payload["build_key"]))

    def test_find_build_reports_the_available_result(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched for a lookup"),
        ):
            found = self.service.find_build(self.request)
        self.assertIsNotNone(found)
        self.assertTrue(found.cache_hit)
        self.assertEqual(found.build_key, self.first.build_key)
        self.assertEqual(
            found.manifest.canonical_bytes(), self.first.manifest.canonical_bytes()
        )


# --- outputs, identity and idempotency --------------------------------------


class TestOutputSelection(ServiceTestCase):
    def test_only_the_requested_outputs_are_produced(self) -> None:
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry", "stl"
        )
        outcome = self.service().build_document(request)
        self.assertIs(
            outcome.status,
            BuildStatus.SUCCEEDED,
            msg=outcome.error.message if outcome.error else "",
        )
        self.assertEqual(outcome.produced_outputs(), ("geometry", "stl"))
        self.assertIsNone(outcome.artifact("step"))
        self.assertIsNone(outcome.artifact("iges"))
        self.assertIsNone(outcome.render_model)

    def test_the_output_names_are_the_existing_artifact_kinds(self) -> None:
        self.assertEqual(
            OUTPUT_NAMES, ("geometry", "step", "iges", "stl", "render")
        )
        self.assertEqual(
            OUTPUT_NAMES, tuple(kind.value for kind in ArtifactKind)
        )

    def test_an_unknown_output_is_a_structured_request_failure(self) -> None:
        outcome = self.service().build_document(
            BuildDocumentRequest(
                document=document([plate_feature()]), outputs=("hologram",)
            )
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.INVALID_REQUEST)
        self.assertEqual(outcome.error.stage, "request")
        self.assertIn("hologram", outcome.error.message)
        self.assertIsNone(outcome.manifest)

    def test_an_empty_output_selection_is_a_structured_request_failure(self) -> None:
        outcome = self.service().build_document(
            BuildDocumentRequest(document=document([plate_feature()]), outputs=())
        )
        self.assertIs(outcome.error.failure, ServiceFailure.INVALID_REQUEST)

    def test_a_bad_request_never_launches_a_child(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            for outputs in (("hologram",), (), ("step", 3)):
                with self.subTest(outputs=outputs):
                    outcome = self.service().build_document(
                        BuildDocumentRequest(
                            document=document([plate_feature()]), outputs=outputs
                        )
                    )
                    self.assertIs(
                        outcome.error.failure, ServiceFailure.INVALID_REQUEST
                    )

    def test_a_single_string_is_not_an_output_selection(self) -> None:
        outcome = self.service().build_document(
            BuildDocumentRequest(
                document=document([plate_feature()]), outputs="step"
            )
        )
        self.assertIs(outcome.error.failure, ServiceFailure.INVALID_REQUEST)


class TestIdentityAndIdempotency(ServiceTestCase):
    def key_for(self, doc: Any, *outputs: str) -> str:
        result = self.service().validate_document(doc)
        self.assertTrue(result.valid)
        return result.build_key_for(outputs or OUTPUT_NAMES)

    def test_the_same_input_and_options_give_the_same_build_key(self) -> None:
        doc = section_d_document()
        keys = {self.key_for(doc) for _ in range(4)}
        self.assertEqual(len(keys), 1)
        # ... and the same key from JSON text as from the parsed structure
        self.assertEqual(self.key_for(json.dumps(doc)), self.key_for(doc))

    def test_a_different_output_selection_gives_a_different_build_key(self) -> None:
        doc = document([plate_feature()])
        self.assertNotEqual(
            self.key_for(doc, "stl"), self.key_for(doc, "stl", "step")
        )
        # ... while requesting the same set in another order does not
        self.assertEqual(
            self.key_for(doc, "step", "stl"), self.key_for(doc, "stl", "step")
        )

    def test_a_different_document_gives_a_different_build_key(self) -> None:
        self.assertNotEqual(
            self.key_for(document([plate_feature()])),
            self.key_for(document([plate_feature(size={"x": 1, "y": 2, "z": 3})])),
        )

    def test_the_service_invents_no_identifier(self) -> None:
        outcome = self.service().build_document(
            BuildDocumentRequest.for_outputs(document([plate_feature()]), "geometry")
        )
        payload = outcome.to_dict()
        self.assertEqual(
            sorted(
                name
                for name in payload
                if name.endswith(("_id", "_key", "_hash"))
            ),
            ["build_key", "document_hash", "execution_id"],
        )
        # the execution id is the child's, and is part of no identity
        self.assertNotIn(payload["execution_id"], payload["build_key"])
        self.assertNotIn(payload["execution_id"], payload["document_hash"])


class TestNoMutation(ServiceTestCase):
    def test_the_source_document_structure_is_not_mutated(self) -> None:
        doc = section_d_document()
        before = copy.deepcopy(doc)
        service = self.service()
        service.validate_document(doc)
        service.build_document(
            BuildDocumentRequest.for_outputs(doc, "geometry")
        )
        self.assertEqual(doc, before)

    def test_the_canonical_json_text_is_not_mutated(self) -> None:
        text = json.dumps(section_d_document())
        service = self.service()
        service.validate_document(text)
        service.build_document(
            BuildDocumentRequest.for_outputs(text, "geometry")
        )
        self.assertEqual(text, json.dumps(section_d_document()))
        self.assertEqual(
            part_hash(part_from_json(text)),
            service.validate_document(text).document_hash,
        )

    def test_a_request_is_reusable(self) -> None:
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry"
        )
        service = self.service()
        first = service.build_document(request)
        second = service.build_document(request)
        self.assertEqual(request.outputs, ("geometry",))
        self.assertEqual(first.build_key, second.build_key)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)


# --- cache behaviour --------------------------------------------------------


class TestCacheBehaviour(ServiceTestCase):
    def test_the_first_build_misses_and_the_second_hits(self) -> None:
        service = self.service()
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry", "stl"
        )
        first = service.build_document(request)
        self.assertTrue(first.succeeded, msg=first.error and first.error.message)
        self.assertFalse(first.cache_hit)
        self.assertTrue(first.cache_published)
        second = service.build_document(request)
        self.assertTrue(second.cache_hit)
        self.assertFalse(second.cache_published)

    def test_a_cache_hit_executes_no_child_process(self) -> None:
        service = self.service()
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry", "stl"
        )
        service.build_document(request)
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched for a cache hit"),
        ):
            hit = service.build_document(request)
        self.assertTrue(hit.cache_hit)
        self.assertTrue(hit.succeeded)
        self.assertEqual(hit.produced_outputs(), ("geometry", "stl"))

    def test_find_build_returns_none_before_a_build(self) -> None:
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry"
        )
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched for a lookup"),
        ):
            self.assertIsNone(self.service().find_build(request))

    def test_find_build_returns_none_for_an_invalid_document(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            self.assertIsNone(
                self.service().find_build(
                    BuildDocumentRequest(document="{ broken")
                )
            )

    def test_a_cache_failure_is_an_execution_failure_not_a_cad_error(self) -> None:
        service = self.service()
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry"
        )
        with mock.patch.object(
            LocalBuildCache,
            "lookup",
            side_effect=__import__(
                "cad_core.local_build_cache", fromlist=["CacheError"]
            ).CacheError("the cache root vanished"),
        ):
            outcome = service.build_document(request)
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.EXECUTION_FAILED)
        self.assertEqual(outcome.error.stage, "cache")
        self.assertIsNone(outcome.error.build_failure)
        self.assertIsNone(outcome.manifest)

    def test_the_cache_root_is_the_services_own_infrastructure(self) -> None:
        with self.assertRaises(ApplicationServiceError):
            CadApplicationService.local(self.tmp / "does-not-exist")
        service = self.service()
        self.assertEqual(service.backend.cache.root, self.cache_root)
        # ... and it is nowhere in a request
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry"
        )
        self.assertNotIn("cache", str(request.outputs))
        self.assertEqual(
            sorted(request.__dataclass_fields__), ["document", "outputs"]
        )


# --- failure mapping --------------------------------------------------------


class TestFailureMapping(ServiceTestCase):
    def test_a_validation_failure_stops_before_any_child_process(self) -> None:
        invalid = document([plate_feature(size={"x": 0, "y": 1, "z": 1})])
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            outcome = self.service().build_document(
                BuildDocumentRequest(document=invalid)
            )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.INVALID_DOCUMENT)
        self.assertIn("S10", outcome.error.rule_codes)
        self.assertIsNone(outcome.manifest)
        self.assertIsNone(outcome.build_key)
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_malformed_json_stops_before_any_child_process(self) -> None:
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen",
            side_effect=AssertionError("a child was launched"),
        ):
            outcome = self.service().build_document(
                BuildDocumentRequest(document="{ broken")
            )
        self.assertIs(outcome.error.failure, ServiceFailure.MALFORMED_DOCUMENT)

    def test_a_geometry_failure_is_a_structured_geometry_failure(self) -> None:
        """A through-hole that misses its target: rule E1, in a child."""
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
        outcome = self.service().build_document(
            BuildDocumentRequest.for_outputs(doc, "geometry")
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.GEOMETRY_FAILED)
        self.assertIs(outcome.error.build_failure, BuildFailure.GEOMETRY_FAILED)
        self.assertIs(outcome.error.execution_outcome, IsolationOutcome.BUILD_FAILED)
        self.assertEqual(outcome.error.rule_codes, ("E1",))
        self.assertEqual(outcome.error.stage, "geometry")
        self.assertIsNone(outcome.manifest)
        # distinguishable from a document failure
        self.assertIsNot(outcome.error.failure, ServiceFailure.INVALID_DOCUMENT)
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_an_export_failure_is_a_structured_output_failure(self) -> None:
        """A real exporter refusal, mapped through the service.

        The failure object is produced by a genuine isolated export into a
        directory where a directory already occupies the file's name; only the
        plumbing that hands it to the service is stubbed, because the local
        backend keeps its build directory private.
        """
        request = BuildRequest(
            part=validate(document([plate_feature()])).part,
            options=BuildOptions.for_outputs(ArtifactKind.STEP),
        )
        out = self.subdirectory("blocked")
        (out / f"{request.build_key}.step").mkdir()
        execution = execute_isolated(request, output_directory=out)
        self.assertIs(execution.outcome, IsolationOutcome.BUILD_FAILED)

        service = CadApplicationService(StubBackend(execution))
        outcome = service.build_document(
            BuildDocumentRequest.for_outputs(document([plate_feature()]), "step")
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.OUTPUT_FAILED)
        self.assertIs(outcome.error.build_failure, BuildFailure.EXPORT_FAILED)
        self.assertIs(outcome.error.output, ArtifactKind.STEP)
        self.assertEqual(outcome.error.stage, "step")
        # distinguishable from a validation failure and from a geometry failure
        self.assertIsNot(outcome.error.failure, ServiceFailure.INVALID_DOCUMENT)
        self.assertIsNot(outcome.error.failure, ServiceFailure.GEOMETRY_FAILED)
        # the public message still carries no path
        self.assertNotIn("/", outcome.error.message)

    def test_a_process_failure_is_a_structured_execution_failure(self) -> None:
        request = BuildDocumentRequest.for_outputs(
            document([plate_feature()]), "geometry"
        )
        with mock.patch(
            "cad_core.isolated_execution.subprocess.Popen", NoResponseChild
        ):
            outcome = self.service().build_document(request)
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.EXECUTION_FAILED)
        self.assertIs(
            outcome.error.execution_outcome, IsolationOutcome.PROCESS_FAILED
        )
        self.assertIsNone(outcome.error.build_failure)
        self.assertIsNone(outcome.manifest)
        # not a CAD error, and not a document error
        self.assertIsNot(outcome.error.failure, ServiceFailure.GEOMETRY_FAILED)
        self.assertIsNot(outcome.error.failure, ServiceFailure.INVALID_DOCUMENT)
        # the child's stderr is not the public message
        self.assertNotIn("child died", outcome.error.message)
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_a_timeout_is_a_structured_execution_failure(self) -> None:
        service = self.service(timeout_seconds=0.4)
        outcome = service.build_document(
            BuildDocumentRequest.for_outputs(
                document([plate_feature()]), "geometry"
            )
        )
        self.assertIs(outcome.status, BuildStatus.FAILED)
        self.assertIs(outcome.error.failure, ServiceFailure.EXECUTION_FAILED)
        self.assertIs(outcome.error.execution_outcome, IsolationOutcome.TIMED_OUT)
        self.assertEqual(outcome.error.stage, "process")
        self.assertIn("timeout", outcome.error.message)
        self.assertIsNone(outcome.manifest)
        self.assertFalse((self.cache_root / ENTRIES_DIRNAME).exists())

    def test_the_four_distinctions_are_four_different_failures(self) -> None:
        self.assertEqual(
            len(
                {
                    ServiceFailure.INVALID_DOCUMENT,
                    ServiceFailure.GEOMETRY_FAILED,
                    ServiceFailure.OUTPUT_FAILED,
                    ServiceFailure.EXECUTION_FAILED,
                }
            ),
            4,
        )

    def test_every_lower_level_classification_maps(self) -> None:
        self.assertEqual(set(BUILD_FAILURE_MAP), set(BuildFailure))
        self.assertEqual(
            set(EXECUTION_OUTCOME_MAP),
            set(IsolationOutcome) - {IsolationOutcome.SUCCEEDED},
        )
        # the map is a mapping, not a rename: several sources share a target
        self.assertIs(
            BUILD_FAILURE_MAP[BuildFailure.UNSUPPORTED_GEOMETRY],
            BUILD_FAILURE_MAP[BuildFailure.GEOMETRY_FAILED],
        )
        self.assertIs(
            BUILD_FAILURE_MAP[BuildFailure.EXPORT_FAILED],
            ServiceFailure.OUTPUT_FAILED,
        )

    def test_a_failure_keeps_the_lower_layers_own_classification(self) -> None:
        outcome = self.service().build_document(
            BuildDocumentRequest(
                document=document([plate_feature(size={"x": 0, "y": 1, "z": 1})])
            )
        )
        payload = json.loads(json.dumps(outcome.to_dict()))
        error = payload["error"]
        self.assertEqual(error["failure"], "invalid_document")
        self.assertEqual(error["build_failure"], "document_invalid")
        self.assertTrue(error["validation_errors"])
        self.assertNotIn("Traceback", json.dumps(error))
        self.assertNotIn("diagnostic", error)

    def test_the_statuses_are_the_existing_terminal_ones(self) -> None:
        self.assertEqual(
            SERVICE_STATUSES, (BuildStatus.SUCCEEDED, BuildStatus.FAILED)
        )
        for outcome in (
            self.service().build_document(
                BuildDocumentRequest(document="{ broken")
            ),
        ):
            self.assertIn(outcome.status, SERVICE_STATUSES)


# --- the whole corpus -------------------------------------------------------


class TestGeometryCorpus(ServiceTestCase):
    def test_the_service_builds_every_real_geometry(self) -> None:
        service = self.service()
        for name, doc in geometry_corpus():
            with self.subTest(geometry=name):
                request = BuildDocumentRequest(document=json.dumps(doc))
                outcome = service.build_document(request)
                self.assertIs(
                    outcome.status,
                    BuildStatus.SUCCEEDED,
                    msg=outcome.error.message if outcome.error else "",
                )
                self.assertEqual(
                    outcome.produced_outputs(),
                    ("geometry", "step", "iges", "stl", "render"),
                )
                self.assertEqual(
                    outcome.artifact("geometry").details["solid_count"], 1
                )
                self.assertIsNotNone(outcome.render_model)
                self.assertFalse(outcome.cache_hit)
                json.dumps(outcome.to_dict())
                again = service.build_document(request)
                self.assertTrue(again.cache_hit)
                self.assertEqual(again.build_key, outcome.build_key)


# --- the boundary -----------------------------------------------------------


class TestBackendIndependence(ServiceTestCase):
    def test_the_service_depends_on_two_methods_only(self) -> None:
        backend = StubBackend(None)
        service = CadApplicationService(backend)
        self.assertIs(service.backend, backend)
        with self.assertRaises(ApplicationServiceError):
            CadApplicationService(object())

    def test_the_backend_receives_the_existing_build_request(self) -> None:
        request = BuildRequest(
            part=validate(document([plate_feature()])).part,
            options=BuildOptions.for_outputs(ArtifactKind.GEOMETRY),
        )
        backend = StubBackend(
            IsolatedExecution(
                outcome=IsolationOutcome.SUCCEEDED,
                build_key=request.build_key,
                document_hash=request.document_hash,
                manifest=ArtifactManifest(
                    document_hash=request.document_hash,
                    build_key=request.build_key,
                    artifacts=(),
                ),
            )
        )
        outcome = CadApplicationService(backend).build_document(
            BuildDocumentRequest.for_outputs(
                document([plate_feature()]), "geometry"
            )
        )
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(backend.requests), 1)
        sent = backend.requests[0]
        self.assertIsInstance(sent, BuildRequest)
        self.assertEqual(sent.build_key, request.build_key)
        self.assertEqual(
            sent.options, BuildOptions.for_outputs(ArtifactKind.GEOMETRY)
        )

    def test_the_local_backend_is_the_only_one_implemented(self) -> None:
        service = self.service()
        self.assertIsInstance(service.backend, LocalBuildBackend)
        self.assertIsInstance(service.backend.cache, LocalBuildCache)
        self.assertGreater(service.backend.timeout_seconds, 0)


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

    def test_no_transport_or_service_framework_is_imported(self) -> None:
        forbidden = {
            "fastapi",
            "starlette",
            "flask",
            "django",
            "graphene",
            "strawberry",
            "graphql",
            "aiohttp",
            "tornado",
            "uvicorn",
            "gunicorn",
            "pydantic",
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
            "hashlib",
        }
        for imported in self.imports_of("application_service"):
            self.assertNotIn(imported, forbidden)

    def test_no_llm_mcp_or_frontend_dependency(self) -> None:
        for imported in self.imports_of("application_service"):
            for token in ("llm", "openai", "anthropic", "mcp"):
                self.assertNotIn(token, imported.lower())

    def test_the_service_does_not_touch_featurescript_or_onshape(self) -> None:
        """Both are named in the docstrings and reached by no code.

        Asserted from the syntax tree, because the module's prose says
        precisely that FeatureScript is not generated and that Onshape is a
        future backend -- a text match would fail on its own documentation.
        """
        imports = self.imports_of("application_service")
        self.assertNotIn("cad_core.featurescript", imports)
        self.assertNotIn("cad_core.onshape_adapter", imports)
        self.assertNotIn("cad_core.onshape_fakes", imports)
        tree = ast.parse(self.source("application_service"))
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.append(node.attr.lower())
        for forbidden in ("featurescript", "onshape", "deliver_part"):
            with self.subTest(name=forbidden):
                self.assertFalse(any(forbidden in name for name in names))

    def test_featurescript_is_not_a_build_output(self) -> None:
        self.assertNotIn("featurescript", OUTPUT_NAMES)
        self.assertEqual(
            OUTPUT_NAMES, tuple(kind.value for kind in ArtifactKind)
        )

    def test_the_service_depends_only_on_the_layers_below_it(self) -> None:
        internal = sorted(
            imported
            for imported in self.imports_of("application_service")
            if imported.startswith("cad_core")
        )
        self.assertEqual(
            internal,
            [
                "cad_core.artifact_registry",
                "cad_core.build_job",
                "cad_core.isolated_execution",
                "cad_core.local_build_cache",
                "cad_core.model",
                "cad_core.render_model",
                "cad_core.serialization",
            ],
        )

    def test_nothing_below_imports_the_application_service(self) -> None:
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
            "__init__",
        ):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.application_service", self.imports_of(name)
                )

    def test_the_service_is_not_re_exported_from_the_package_root(self) -> None:
        self.assertNotIn(
            "cad_core.application_service", self.imports_of("__init__")
        )

    def test_the_service_implements_no_validation_rule(self) -> None:
        source = self.source("application_service")
        tree = ast.parse(source)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
        for forbidden in ("STATIC_RULES", "check_geometric_rules", "validate"):
            self.assertNotIn(forbidden, names)
        self.assertNotIn("cad_core.validator", self.imports_of("application_service"))
        self.assertNotIn("cad_core.rules", self.imports_of("application_service"))
        self.assertNotIn("cad_core.geometry", self.imports_of("application_service"))

    def test_the_service_defines_no_artifact_or_manifest_schema(self) -> None:
        tree = ast.parse(self.source("application_service"))
        classes = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]
        for forbidden in ("Artifact", "ArtifactManifest", "BuildError", "BuildResult"):
            self.assertNotIn(forbidden, classes)
        self.assertEqual(
            sorted(classes),
            [
                "ApplicationServiceError",
                "BuildDocumentRequest",
                "BuildOutcome",
                "CadApplicationService",
                "DocumentValidation",
                "LocalBuildBackend",
                "ServiceError",
                "ServiceFailure",
                "_RequestProblem",
            ],
        )

    def test_no_authentication_or_quota_concept_appears(self) -> None:
        tree = ast.parse(self.source("application_service"))
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
            "permission",
            "tenant",
            "quota",
            "rate_limit",
            "session",
            "api_key",
        ):
            with self.subTest(name=forbidden):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    msg=f"{forbidden!r} appears in the service's code",
                )

    def test_the_documentation_states_it_is_not_the_http_api(self) -> None:
        documentation = (
            Path(__file__).resolve().parents[3] / "docs" / "application-service.md"
        )
        self.assertTrue(documentation.is_file())
        unwrapped = " ".join(
            documentation.read_text(encoding="utf-8").split()
        )
        self.assertIn("Application Service ≠ HTTP API", unwrapped)
        self.assertIn("not the HTTP API", unwrapped)
        for expected in ("rate limit", "quota", "authentication"):
            self.assertIn(expected, unwrapped.lower())
        self.assertIn("not a security sandbox", self.source("isolated_execution"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
