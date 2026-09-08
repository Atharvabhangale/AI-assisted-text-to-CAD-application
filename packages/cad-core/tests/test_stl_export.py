"""Unit tests for binary STL mesh export and round-trip verification.

STL carries triangles, not CAD topology, so nothing here asserts that a round
trip recovers B-rep faces, edges, vertices or solid identity -- those are not
STL guarantees. Mesh volume is not asserted at all: the mesh was not
independently shown to be closed, so the B-rep volume remains the only volume
this project trusts.

All output goes into temporary directories that clean up automatically.
"""

from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_core import validate
from cad_core.local_cad import LocalCadResult, Position, Size, build_part
from cad_core.stl_export import (
    DEFAULT_ANGULAR_DEFLECTION_RAD,
    DEFAULT_LINEAR_DEFLECTION_MM,
    STL_COUNT_BYTES,
    STL_EXTENSIONS,
    STL_HEADER_BYTES,
    STL_TRIANGLE_BYTES,
    ImportedStlMesh,
    StlBinaryFacts,
    StlExportError,
    UnsupportedStlExtensionError,
    binary_stl_facts,
    export_stl,
    read_stl,
)

#: Tolerance for mesh coordinates, in millimetres. The mesh may deviate from
#: the true surface by up to the linear deflection, so the envelope check
#: allows that, plus 1e-6 mm of kernel float noise (the tolerance used by the
#: local engine and the STEP/IGES exporters).
KERNEL_NOISE_MM = 1e-6
MESH_TOLERANCE_MM = DEFAULT_LINEAR_DEFLECTION_MM + KERNEL_NOISE_MM

EXPECTED_MINIMUM = (10.0, 20.0, 30.0)
EXPECTED_MAXIMUM = (110.0, 80.0, 40.0)
EXPECTED_DIMENSIONS = (100.0, 60.0, 10.0)

#: A box has six planar faces, which need no refinement: two triangles each.
EXPECTED_TRIANGLES = 12


def box_document(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
        "position": {"x": 10, "y": 20, "z": 30},
    }
    feature.update(overrides.pop("feature", {}))
    document: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "units": "mm",
        "name": "plate-100x60x10",
        "features": [feature],
    }
    document.update(overrides)
    return document


class StlTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def reference_result(self, **overrides: Any) -> LocalCadResult:
        result = validate(box_document(**overrides))
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return build_part(result.part)

    def round_trip(self) -> ImportedStlMesh:
        return read_stl(export_stl(self.reference_result(), self.tmp / "plate.stl"))

    def assertPointAlmostEqual(
        self, point: Position, expected: tuple, delta: float = MESH_TOLERANCE_MM
    ) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(point, axis), value, delta=delta)

    def assertSizeAlmostEqual(
        self, size: Size, expected: tuple, delta: float = MESH_TOLERANCE_MM
    ) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(size, axis), value, delta=delta)


# --- 1, 2, 3: export succeeds ----------------------------------------------


class TestExportSucceeds(StlTestCase):
    def test_stl_export_succeeds(self) -> None:
        written = export_stl(self.reference_result(), self.tmp / "plate.stl")
        self.assertEqual(written.suffix, ".stl")

    def test_output_file_exists(self) -> None:
        self.assertTrue(
            export_stl(self.reference_result(), self.tmp / "plate.stl").is_file()
        )

    def test_output_file_is_non_empty(self) -> None:
        written = export_stl(self.reference_result(), self.tmp / "plate.stl")
        self.assertGreater(written.stat().st_size, 0)

    def test_export_returns_the_path_written(self) -> None:
        target = self.tmp / "plate.stl"
        self.assertEqual(export_stl(self.reference_result(), target), target)

    def test_extension_matching_is_case_insensitive(self) -> None:
        for name in ("plate.STL", "plate.Stl"):
            with self.subTest(name=name):
                self.assertTrue(
                    export_stl(self.reference_result(), self.tmp / name).is_file()
                )

    def test_string_paths_are_accepted(self) -> None:
        self.assertTrue(
            export_stl(self.reference_result(), str(self.tmp / "plate.stl")).is_file()
        )

    def test_explicit_tolerances_are_accepted(self) -> None:
        written = export_stl(
            self.reference_result(),
            self.tmp / "plate.stl",
            tolerance=0.05,
            angular_tolerance=0.2,
        )
        self.assertTrue(written.is_file())

    def test_non_positive_tolerances_are_rejected(self) -> None:
        for kwargs in (
            {"tolerance": 0.0}, {"tolerance": -0.01},
            {"angular_tolerance": 0.0}, {"angular_tolerance": -0.1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    export_stl(
                        self.reference_result(), self.tmp / "plate.stl", **kwargs
                    )


# --- 4, 5: binary format identity ------------------------------------------


class TestBinaryFormat(StlTestCase):
    def facts(self) -> StlBinaryFacts:
        return binary_stl_facts(
            export_stl(self.reference_result(), self.tmp / "plate.stl")
        )

    def test_output_is_binary_not_ascii(self) -> None:
        """An ASCII STL begins with 'solid'; a binary one has an opaque header."""
        facts = self.facts()
        self.assertFalse(facts.looks_ascii)
        self.assertEqual(len(facts.header), STL_HEADER_BYTES)

    def test_file_size_is_consistent_with_the_declared_triangle_count(self) -> None:
        facts = self.facts()
        self.assertTrue(facts.is_structurally_consistent)
        self.assertEqual(
            facts.file_size,
            STL_HEADER_BYTES + STL_COUNT_BYTES
            + STL_TRIANGLE_BYTES * facts.declared_triangles,
        )

    def test_declared_triangle_count_is_positive(self) -> None:
        self.assertGreater(self.facts().declared_triangles, 0)

    def test_declared_triangle_count_matches_the_parsed_mesh(self) -> None:
        written = export_stl(self.reference_result(), self.tmp / "plate.stl")
        self.assertEqual(
            binary_stl_facts(written).declared_triangles,
            read_stl(written).triangle_count(),
        )

    def test_layout_constants_match_the_binary_stl_specification(self) -> None:
        self.assertEqual((STL_HEADER_BYTES, STL_COUNT_BYTES, STL_TRIANGLE_BYTES),
                         (80, 4, 50))

    def test_extension_alone_is_not_accepted_as_evidence(self) -> None:
        """A file named .stl that is not STL must be rejected."""
        target = self.tmp / "fake.stl"
        target.write_text("solid definitely-not-binary\nendsolid\n")
        with self.assertRaises(StlExportError):
            read_stl(target)

    def test_a_truncated_file_is_rejected(self) -> None:
        written = export_stl(self.reference_result(), self.tmp / "plate.stl")
        truncated = self.tmp / "truncated.stl"
        truncated.write_bytes(written.read_bytes()[: STL_HEADER_BYTES + 20])
        with self.assertRaises(StlExportError) as caught:
            read_stl(truncated)
        self.assertIn("not a consistent binary STL", str(caught.exception))

    def test_a_file_too_short_for_a_header_is_rejected(self) -> None:
        target = self.tmp / "tiny.stl"
        target.write_bytes(b"\x00" * 10)
        with self.assertRaises(StlExportError) as caught:
            binary_stl_facts(target)
        self.assertIn("too short", str(caught.exception))

    def test_a_lying_triangle_count_is_rejected(self) -> None:
        """The declared count must account for the file length exactly."""
        written = export_stl(self.reference_result(), self.tmp / "plate.stl")
        data = bytearray(written.read_bytes())
        struct.pack_into("<I", data, STL_HEADER_BYTES, 9999)
        liar = self.tmp / "liar.stl"
        liar.write_bytes(bytes(data))
        with self.assertRaises(StlExportError):
            read_stl(liar)


# --- 6, 7, 8, 9, 10, 11: the mesh round trip -------------------------------


class TestMeshRoundTrip(StlTestCase):
    """Reference case: 100 x 60 x 10 mm at (10, 20, 30)."""

    def test_stl_can_be_read_back_by_the_real_kernel_parser(self) -> None:
        mesh = self.round_trip()
        self.assertIsInstance(mesh, ImportedStlMesh)
        self.assertIsNotNone(mesh.triangulation)

    def test_triangle_count_is_positive(self) -> None:
        self.assertGreater(self.round_trip().triangle_count(), 0)

    def test_triangle_count_is_sensible_for_a_box(self) -> None:
        """Six planar faces, two triangles each. Not a topology claim."""
        self.assertEqual(self.round_trip().triangle_count(), EXPECTED_TRIANGLES)

    def test_mesh_has_nodes(self) -> None:
        mesh = self.round_trip()
        self.assertGreater(mesh.node_count(), 0)
        self.assertEqual(len(mesh.nodes()), mesh.node_count())

    def test_mesh_minimum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().minimum, EXPECTED_MINIMUM
        )

    def test_mesh_maximum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().maximum, EXPECTED_MAXIMUM
        )

    def test_mesh_dimensions(self) -> None:
        self.assertSizeAlmostEqual(
            self.round_trip().bounding_box().size, EXPECTED_DIMENSIONS
        )

    def test_every_vertex_lies_within_the_expected_envelope(self) -> None:
        """No triangle vertex may escape the B-rep bounds by more than the
        linear deflection."""
        mesh = self.round_trip()
        for index, (x, y, z) in enumerate(mesh.nodes()):
            with self.subTest(node=index):
                self.assertGreaterEqual(x, EXPECTED_MINIMUM[0] - MESH_TOLERANCE_MM)
                self.assertGreaterEqual(y, EXPECTED_MINIMUM[1] - MESH_TOLERANCE_MM)
                self.assertGreaterEqual(z, EXPECTED_MINIMUM[2] - MESH_TOLERANCE_MM)
                self.assertLessEqual(x, EXPECTED_MAXIMUM[0] + MESH_TOLERANCE_MM)
                self.assertLessEqual(y, EXPECTED_MAXIMUM[1] + MESH_TOLERANCE_MM)
                self.assertLessEqual(z, EXPECTED_MAXIMUM[2] + MESH_TOLERANCE_MM)

    def test_mesh_envelope_matches_the_brep_bounds(self) -> None:
        source = self.reference_result()
        mesh = read_stl(export_stl(source, self.tmp / "plate.stl"))
        brep, mesh_box = source.bounding_box(), mesh.bounding_box()
        self.assertPointAlmostEqual(
            mesh_box.minimum, (brep.minimum.x, brep.minimum.y, brep.minimum.z)
        )
        self.assertPointAlmostEqual(
            mesh_box.maximum, (brep.maximum.x, brep.maximum.y, brep.maximum.z)
        )

    def test_origin_box_round_trips(self) -> None:
        result = self.reference_result(feature={"position": {"x": 0, "y": 0, "z": 0}})
        mesh = read_stl(export_stl(result, self.tmp / "origin.stl"))
        self.assertPointAlmostEqual(mesh.bounding_box().minimum, (0, 0, 0))
        self.assertPointAlmostEqual(mesh.bounding_box().maximum, (100, 60, 10))

    def test_negative_position_round_trips(self) -> None:
        result = self.reference_result(
            feature={"position": {"x": -50, "y": -30, "z": -5}}
        )
        mesh = read_stl(export_stl(result, self.tmp / "negative.stl"))
        self.assertPointAlmostEqual(mesh.bounding_box().minimum, (-50, -30, -5))
        self.assertPointAlmostEqual(mesh.bounding_box().maximum, (50, 30, 5))

    def test_stl_does_not_carry_cad_identity(self) -> None:
        """Recorded, not lamented: the mesh has no feature id or part name.

        STL preserves triangles. The B-rep result keeps the CAD identity, and
        STEP/IGES are the formats for preserving geometry as geometry.
        """
        source = self.reference_result()
        mesh = read_stl(export_stl(source, self.tmp / "plate.stl"))
        self.assertEqual(source.feature_id, "plate")
        self.assertFalse(hasattr(mesh, "feature_id"))
        self.assertFalse(hasattr(mesh, "part_name"))
        self.assertFalse(hasattr(mesh, "is_solid"))
        self.assertFalse(hasattr(mesh, "volume"))


# --- 12: repeated exports --------------------------------------------------


class TestRepeatedExports(StlTestCase):
    def test_repeated_exports_are_geometrically_equivalent(self) -> None:
        source = self.reference_result()
        measurements = set()
        for index in range(3):
            mesh = read_stl(export_stl(source, self.tmp / f"plate-{index}.stl"))
            box = mesh.bounding_box()
            measurements.add(
                (
                    mesh.triangle_count(),
                    mesh.node_count(),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                )
            )
        self.assertEqual(len(measurements), 1, msg=f"exports diverged: {measurements}")

    def test_repeated_exports_are_byte_identical(self) -> None:
        """Measured, not assumed.

        With the settings this module fixes -- binary, absolute deflection,
        single-threaded meshing -- repeated exports of the same solid came out
        byte-identical. Recorded as an empirical result for this backend and
        this geometry, not as a guarantee of the STL format.
        """
        source = self.reference_result()
        digests = set()
        for index in range(4):
            written = export_stl(source, self.tmp / f"plate-{index}.stl")
            digests.add(hashlib.sha256(written.read_bytes()).hexdigest())
        self.assertEqual(len(digests), 1)

    def test_repeated_exports_to_the_same_path_overwrite_cleanly(self) -> None:
        source = self.reference_result()
        target = self.tmp / "plate.stl"
        for _ in range(3):
            export_stl(source, target)
            self.assertTrue(binary_stl_facts(target).is_structurally_consistent)

    def test_a_coarser_tolerance_still_meshes_a_box_identically(self) -> None:
        """A box is planar, so deflection cannot change its triangulation."""
        source = self.reference_result()
        fine = read_stl(export_stl(source, self.tmp / "fine.stl", tolerance=0.001))
        coarse = read_stl(export_stl(source, self.tmp / "coarse.stl", tolerance=1.0))
        self.assertEqual(fine.triangle_count(), coarse.triangle_count())
        self.assertEqual(coarse.triangle_count(), EXPECTED_TRIANGLES)


# --- 13: the source result is not modified ---------------------------------


class TestSourceIsUnmodified(StlTestCase):
    def test_export_does_not_modify_the_source_result(self) -> None:
        source = self.reference_result()
        before = (
            source.part_name,
            source.feature_id,
            source.is_solid(),
            source.solid_count(),
            source.volume(),
        )
        before_box = source.bounding_box()
        shape_before = source.shape

        export_stl(source, self.tmp / "plate.stl")

        self.assertEqual(
            before,
            (
                source.part_name,
                source.feature_id,
                source.is_solid(),
                source.solid_count(),
                source.volume(),
            ),
        )
        self.assertIs(source.shape, shape_before)
        after_box = source.bounding_box()
        self.assertPointAlmostEqual(
            after_box.minimum,
            (before_box.minimum.x, before_box.minimum.y, before_box.minimum.z),
            delta=KERNEL_NOISE_MM,
        )
        self.assertPointAlmostEqual(
            after_box.maximum,
            (before_box.maximum.x, before_box.maximum.y, before_box.maximum.z),
            delta=KERNEL_NOISE_MM,
        )

    def test_the_brep_remains_a_solid_after_tessellation(self) -> None:
        """Tessellation attaches a triangulation; it must not replace the solid."""
        source = self.reference_result()
        export_stl(source, self.tmp / "plate.stl")
        self.assertTrue(source.is_solid())
        self.assertEqual(source.shape.ShapeType(), "Solid")
        self.assertEqual(source.solid_count(), 1)
        self.assertEqual(len(source.shape.Faces()), 6)

    def test_one_result_can_feed_all_three_exporters(self) -> None:
        from cad_core.iges_export import export_iges, read_iges
        from cad_core.step_export import export_step, read_step

        source = self.reference_result()
        export_stl(source, self.tmp / "plate.stl")
        step_back = read_step(export_step(source, self.tmp / "plate.step"))
        iges_back = read_iges(export_iges(source, self.tmp / "plate.igs"))
        self.assertTrue(step_back.is_solid())
        self.assertTrue(iges_back.is_solid())
        self.assertAlmostEqual(step_back.volume(), 60000.0, delta=KERNEL_NOISE_MM)
        self.assertAlmostEqual(iges_back.volume(), 60000.0, delta=KERNEL_NOISE_MM)


# --- 14, 15: rejected extensions and input ---------------------------------


class TestRejectedInput(StlTestCase):
    def test_unsupported_extensions_are_rejected(self) -> None:
        for name in (
            "plate.step", "plate.stp", "plate.igs", "plate.iges", "plate.3mf",
            "plate.obj", "plate.gltf", "plate.glb", "plate.ply", "plate.txt",
            "plate",
        ):
            with self.subTest(name=name):
                with self.assertRaises(UnsupportedStlExtensionError):
                    export_stl(self.reference_result(), self.tmp / name)

    def test_nothing_is_written_for_a_rejected_extension(self) -> None:
        with self.assertRaises(UnsupportedStlExtensionError):
            export_stl(self.reference_result(), self.tmp / "plate.obj")
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_directory_fails_explicitly(self) -> None:
        target = self.tmp / "no-such-directory" / "plate.stl"
        with self.assertRaises(StlExportError) as caught:
            export_stl(self.reference_result(), target)
        self.assertIn("does not exist", str(caught.exception))

    def test_raw_dictionary_is_rejected(self) -> None:
        with self.assertRaises(TypeError) as caught:
            export_stl(box_document(), self.tmp / "plate.stl")  # type: ignore[arg-type]
        self.assertIn("LocalCadResult", str(caught.exception))

    def test_bare_kernel_shape_is_rejected(self) -> None:
        shape = self.reference_result().shape
        with self.assertRaises(TypeError):
            export_stl(shape, self.tmp / "plate.stl")  # type: ignore[arg-type]

    def test_loose_mesh_data_is_rejected(self) -> None:
        """Arbitrary mesh data is not an input to this exporter."""
        triangles = [((0, 0, 0), (1, 0, 0), (0, 1, 0))]
        with self.assertRaises(TypeError):
            export_stl(triangles, self.tmp / "plate.stl")  # type: ignore[arg-type]

    def test_other_types_are_rejected(self) -> None:
        for value in (None, 0, [], (), "solid", object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    export_stl(value, self.tmp / "plate.stl")  # type: ignore[arg-type]

    def test_validation_result_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            export_stl(validate(box_document()), self.tmp / "p.stl")  # type: ignore[arg-type]

    def test_reading_a_missing_file_fails_explicitly(self) -> None:
        with self.assertRaises(StlExportError):
            read_stl(self.tmp / "absent.stl")

    def test_reading_an_unsupported_extension_is_rejected(self) -> None:
        target = self.tmp / "plate.step"
        target.write_text("irrelevant")
        with self.assertRaises(UnsupportedStlExtensionError):
            read_stl(target)


# --- package boundary and settings -----------------------------------------


class TestBoundaryAndSettings(unittest.TestCase):
    SOURCE = Path(__file__).resolve().parents[1] / "src" / "cad_core"

    @staticmethod
    def imports_of(path: Path) -> set:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    def test_the_three_exporters_are_independent(self) -> None:
        for name in ("step_export.py", "iges_export.py"):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.stl_export", self.imports_of(self.SOURCE / name)
                )
        stl = self.imports_of(self.SOURCE / "stl_export.py")
        self.assertNotIn("cad_core.step_export", stl)
        self.assertNotIn("cad_core.iges_export", stl)

    def test_nothing_upstream_imports_stl(self) -> None:
        for name in (
            "model.py", "validator.py", "errors.py", "rules.py", "geometry.py",
            "featurescript.py", "onshape_adapter.py", "onshape_fakes.py",
            "local_cad.py", "__init__.py",
        ):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.stl_export", self.imports_of(self.SOURCE / name)
                )

    def test_the_specification_gains_no_mesh_concept(self) -> None:
        """No mesh vocabulary may leak into the neutral model."""
        model = (self.SOURCE / "model.py").read_text(encoding="utf-8").lower()
        for word in ("mesh", "triangle", "tessell", "stl", "facet", "vertex buffer"):
            with self.subTest(word=word):
                self.assertNotIn(word, model)

    def test_stl_export_is_not_imported_by_the_package_root(self) -> None:
        text = (self.SOURCE / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("stl_export", text)

    def test_tessellation_settings_are_explicit_and_documented(self) -> None:
        self.assertEqual(DEFAULT_LINEAR_DEFLECTION_MM, 0.01)
        self.assertEqual(DEFAULT_ANGULAR_DEFLECTION_RAD, 0.1)
        self.assertEqual(STL_EXTENSIONS, (".stl",))

    def test_ascii_stl_is_not_offered(self) -> None:
        """Binary is the only mode; there is no ascii switch on the API."""
        import inspect

        parameters = inspect.signature(export_stl).parameters
        self.assertNotIn("ascii", parameters)
        self.assertEqual(
            sorted(parameters), ["angular_tolerance", "path", "result", "tolerance"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
