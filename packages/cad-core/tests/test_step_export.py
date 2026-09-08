"""Unit tests for STEP export and geometric round-trip verification.

Every test writes only into a temporary directory that is removed
automatically, so no artifact is ever left in the repository and no
machine-specific path is depended on.

Byte-for-byte identity of independently generated STEP files is deliberately
NOT asserted: OpenCascade stamps a timestamp into the ``FILE_NAME`` header, so
two exports of the same solid differ as bytes while being identical as
geometry. What is asserted is the geometry after a real round trip.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_core import validate
from cad_core.local_cad import LocalCadResult, Position, Size, build_part
from cad_core.step_export import (
    STEP_EXTENSIONS,
    ImportedStepSolid,
    StepExportError,
    UnsupportedExportFormatError,
    export_step,
    read_step,
)

#: Tolerance for kernel-derived lengths, in millimetres. Matches the local CAD
#: engine's tolerance; one nanometre is far below any meaningful millimetre
#: dimension while absorbing float noise from the kernel and the STEP text.
TOLERANCE_MM = 1e-6

#: Tolerance for kernel-derived volumes, in cubic millimetres.
TOLERANCE_MM3 = 1e-6

#: The stage's reference part.
EXPECTED_MINIMUM = (10.0, 20.0, 30.0)
EXPECTED_MAXIMUM = (110.0, 80.0, 40.0)
EXPECTED_DIMENSIONS = (100.0, 60.0, 10.0)
EXPECTED_VOLUME = 60000.0


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


class StepTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def reference_result(self, **overrides: Any) -> LocalCadResult:
        result = validate(box_document(**overrides))
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return build_part(result.part)

    def assertPointAlmostEqual(self, point: Position, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(point, axis), value, delta=TOLERANCE_MM)

    def assertSizeAlmostEqual(self, size: Size, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(size, axis), value, delta=TOLERANCE_MM)


# --- 1, 2, 4: export succeeds for both extensions --------------------------


class TestExportSucceeds(StepTestCase):
    def test_export_step_extension(self) -> None:
        written = export_step(self.reference_result(), self.tmp / "plate.step")
        self.assertEqual(written.suffix, ".step")
        self.assertTrue(written.is_file())

    def test_export_stp_extension(self) -> None:
        """`.stp` needs the export type passed explicitly; it must still work."""
        written = export_step(self.reference_result(), self.tmp / "plate.stp")
        self.assertEqual(written.suffix, ".stp")
        self.assertTrue(written.is_file())

    def test_output_file_is_non_empty(self) -> None:
        for extension in STEP_EXTENSIONS:
            with self.subTest(extension=extension):
                written = export_step(
                    self.reference_result(), self.tmp / f"plate{extension}"
                )
                self.assertGreater(written.stat().st_size, 0)

    def test_output_is_an_iso_10303_step_file(self) -> None:
        written = export_step(self.reference_result(), self.tmp / "plate.step")
        text = written.read_text(errors="replace")
        self.assertTrue(text.startswith("ISO-10303-21;"))
        self.assertIn("END-ISO-10303-21;", text)

    def test_export_returns_the_path_written(self) -> None:
        target = self.tmp / "plate.step"
        self.assertEqual(export_step(self.reference_result(), target), target)

    def test_extension_matching_is_case_insensitive(self) -> None:
        written = export_step(self.reference_result(), self.tmp / "plate.STEP")
        self.assertTrue(written.is_file())

    def test_string_paths_are_accepted(self) -> None:
        written = export_step(self.reference_result(), str(self.tmp / "plate.step"))
        self.assertTrue(written.is_file())


# --- 3: unsupported extensions --------------------------------------------


class TestUnsupportedExtensions(StepTestCase):
    def test_unsupported_extensions_are_rejected(self) -> None:
        for name in (
            "plate.stl", "plate.iges", "plate.igs", "plate.brep", "plate.3mf",
            "plate.dxf", "plate.svg", "plate.txt", "plate.json", "plate",
        ):
            with self.subTest(name=name):
                with self.assertRaises(UnsupportedExportFormatError):
                    export_step(self.reference_result(), self.tmp / name)

    def test_rejection_names_the_supported_formats(self) -> None:
        with self.assertRaises(UnsupportedExportFormatError) as caught:
            export_step(self.reference_result(), self.tmp / "plate.stl")
        message = str(caught.exception)
        self.assertIn(".step", message)
        self.assertIn(".stp", message)

    def test_no_file_is_written_for_a_rejected_extension(self) -> None:
        """The format is never silently switched to one that is supported."""
        target = self.tmp / "plate.stl"
        with self.assertRaises(UnsupportedExportFormatError):
            export_step(self.reference_result(), target)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_directory_fails_explicitly(self) -> None:
        target = self.tmp / "no-such-directory" / "plate.step"
        with self.assertRaises(StepExportError) as caught:
            export_step(self.reference_result(), target)
        self.assertIn("does not exist", str(caught.exception))
        self.assertFalse(target.exists())


# --- 5, 6, 7, 8, 9, 10: the geometric round trip --------------------------


class TestRoundTrip(StepTestCase):
    """The stage's reference case: 100 x 60 x 10 mm at (10, 20, 30)."""

    def round_trip(self, extension: str = ".step") -> ImportedStepSolid:
        written = export_step(self.reference_result(), self.tmp / f"plate{extension}")
        return read_step(written)

    def test_step_file_can_be_read_back(self) -> None:
        imported = self.round_trip()
        self.assertIsInstance(imported, ImportedStepSolid)
        self.assertIsNotNone(imported.shape)

    def test_imported_object_is_a_valid_solid(self) -> None:
        imported = self.round_trip()
        self.assertTrue(imported.is_solid())
        self.assertEqual(imported.shape.ShapeType(), "Solid")
        self.assertTrue(imported.shape.isValid())

    def test_imported_solid_count_is_one(self) -> None:
        self.assertEqual(self.round_trip().solid_count(), 1)

    def test_imported_topology_is_a_box(self) -> None:
        shape = self.round_trip().shape
        self.assertEqual(len(shape.Faces()), 6)
        self.assertEqual(len(shape.Edges()), 12)
        self.assertEqual(len(shape.Vertices()), 8)

    def test_imported_dimensions(self) -> None:
        self.assertSizeAlmostEqual(
            self.round_trip().bounding_box().size, EXPECTED_DIMENSIONS
        )

    def test_imported_minimum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().minimum, EXPECTED_MINIMUM
        )

    def test_imported_maximum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().maximum, EXPECTED_MAXIMUM
        )

    def test_imported_volume(self) -> None:
        self.assertAlmostEqual(
            self.round_trip().volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3
        )

    def test_round_trip_holds_for_both_extensions(self) -> None:
        for extension in STEP_EXTENSIONS:
            with self.subTest(extension=extension):
                imported = self.round_trip(extension)
                self.assertTrue(imported.is_solid())
                self.assertEqual(imported.solid_count(), 1)
                self.assertPointAlmostEqual(
                    imported.bounding_box().minimum, EXPECTED_MINIMUM
                )
                self.assertPointAlmostEqual(
                    imported.bounding_box().maximum, EXPECTED_MAXIMUM
                )
                self.assertAlmostEqual(
                    imported.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3
                )

    def test_exported_geometry_matches_the_source_result(self) -> None:
        """The round trip must not move or resize the solid."""
        source = self.reference_result()
        written = export_step(source, self.tmp / "plate.step")
        imported = read_step(written)
        source_box, imported_box = source.bounding_box(), imported.bounding_box()
        self.assertPointAlmostEqual(
            imported_box.minimum,
            (source_box.minimum.x, source_box.minimum.y, source_box.minimum.z),
        )
        self.assertPointAlmostEqual(
            imported_box.maximum,
            (source_box.maximum.x, source_box.maximum.y, source_box.maximum.z),
        )
        self.assertAlmostEqual(imported.volume(), source.volume(), delta=TOLERANCE_MM3)

    def test_origin_box_round_trips(self) -> None:
        result = self.reference_result(feature={"position": {"x": 0, "y": 0, "z": 0}})
        imported = read_step(export_step(result, self.tmp / "origin.step"))
        self.assertPointAlmostEqual(imported.bounding_box().minimum, (0, 0, 0))
        self.assertPointAlmostEqual(imported.bounding_box().maximum, (100, 60, 10))

    def test_negative_position_round_trips(self) -> None:
        result = self.reference_result(
            feature={"position": {"x": -50, "y": -30, "z": -5}}
        )
        imported = read_step(export_step(result, self.tmp / "negative.step"))
        self.assertPointAlmostEqual(imported.bounding_box().minimum, (-50, -30, -5))
        self.assertPointAlmostEqual(imported.bounding_box().maximum, (50, 30, 5))


# --- 11: repeated exports --------------------------------------------------


class TestRepeatedExports(StepTestCase):
    def test_repeated_exports_all_round_trip_to_the_same_geometry(self) -> None:
        source = self.reference_result()
        measurements = set()
        for index in range(3):
            imported = read_step(export_step(source, self.tmp / f"plate-{index}.step"))
            box = imported.bounding_box()
            measurements.add(
                (
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    (box.size.x, box.size.y, box.size.z),
                    imported.solid_count(),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1, msg=f"exports diverged: {measurements}")

    def test_repeated_exports_to_the_same_path_overwrite_cleanly(self) -> None:
        source = self.reference_result()
        target = self.tmp / "plate.step"
        for _ in range(3):
            export_step(source, target)
            imported = read_step(target)
            self.assertTrue(imported.is_solid())
            self.assertPointAlmostEqual(
                imported.bounding_box().minimum, EXPECTED_MINIMUM
            )

    def test_determinism_is_geometric_not_byte_for_byte(self) -> None:
        """Two exports of one solid differ as bytes but agree as geometry.

        OpenCascade stamps per-export metadata into the STEP header -- a
        timestamp in ``FILE_NAME`` and an incrementing translator instance
        number in the originating-system string -- so byte identity is not
        something this exporter guarantees. The geometry after a round trip is.
        """
        source = self.reference_result()
        first_path = export_step(source, self.tmp / "a.step")
        second_path = export_step(source, self.tmp / "b.step")

        # Recorded, not asserted away: the bytes genuinely differ.
        self.assertNotEqual(first_path.read_bytes(), second_path.read_bytes())
        header = first_path.read_text(errors="replace")
        self.assertRegex(header, r"FILE_NAME\(")

        # What must hold instead: identical geometry from both files.
        measurements = set()
        for path in (first_path, second_path):
            imported = read_step(path)
            box = imported.bounding_box()
            self.assertTrue(imported.is_solid())
            self.assertPointAlmostEqual(box.minimum, EXPECTED_MINIMUM)
            self.assertPointAlmostEqual(box.maximum, EXPECTED_MAXIMUM)
            self.assertAlmostEqual(imported.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3)
            measurements.add(
                (
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1)


# --- 12: the source result is not modified --------------------------------


class TestSourceIsUnmodified(StepTestCase):
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

        export_step(source, self.tmp / "plate.step")

        after = (
            source.part_name,
            source.feature_id,
            source.is_solid(),
            source.solid_count(),
            source.volume(),
        )
        self.assertEqual(before, after)
        self.assertIs(source.shape, shape_before)
        after_box = source.bounding_box()
        self.assertPointAlmostEqual(
            after_box.minimum,
            (before_box.minimum.x, before_box.minimum.y, before_box.minimum.z),
        )
        self.assertPointAlmostEqual(
            after_box.maximum,
            (before_box.maximum.x, before_box.maximum.y, before_box.maximum.z),
        )

    def test_source_can_be_exported_repeatedly(self) -> None:
        source = self.reference_result()
        for index in range(3):
            export_step(source, self.tmp / f"plate-{index}.step")
        self.assertTrue(source.is_solid())
        self.assertAlmostEqual(source.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3)


# --- 13: input boundary ----------------------------------------------------


class TestInputBoundary(StepTestCase):
    def test_raw_dictionary_is_rejected(self) -> None:
        with self.assertRaises(TypeError) as caught:
            export_step(box_document(), self.tmp / "plate.step")  # type: ignore[arg-type]
        self.assertIn("LocalCadResult", str(caught.exception))

    def test_bare_kernel_shape_is_rejected(self) -> None:
        """Only a LocalCadResult is accepted, not a loose CadQuery shape."""
        shape = self.reference_result().shape
        with self.assertRaises(TypeError):
            export_step(shape, self.tmp / "plate.step")  # type: ignore[arg-type]

    def test_other_types_are_rejected(self) -> None:
        for value in (None, 0, [], (), "solid", object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    export_step(value, self.tmp / "plate.step")  # type: ignore[arg-type]

    def test_validation_result_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            export_step(validate(box_document()), self.tmp / "p.step")  # type: ignore[arg-type]

    def test_reading_a_missing_file_fails_explicitly(self) -> None:
        with self.assertRaises(StepExportError):
            read_step(self.tmp / "absent.step")

    def test_reading_an_unsupported_extension_is_rejected(self) -> None:
        target = self.tmp / "plate.stl"
        target.write_text("not step")
        with self.assertRaises(UnsupportedExportFormatError):
            read_step(target)

    def test_reading_a_non_step_file_fails_explicitly(self) -> None:
        target = self.tmp / "bogus.step"
        target.write_text("this is not a STEP file")
        with self.assertRaises(StepExportError):
            read_step(target)


# --- package boundary ------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    def test_dependency_direction_is_one_way(self) -> None:
        """Nothing upstream of the local engine may import STEP export."""
        import ast

        source_dir = Path(__file__).resolve().parents[1] / "src" / "cad_core"
        upstream = (
            "model.py", "validator.py", "errors.py", "rules.py", "geometry.py",
            "featurescript.py", "onshape_adapter.py", "onshape_fakes.py",
            "local_cad.py", "__init__.py",
        )
        for name in upstream:
            tree = ast.parse((source_dir / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module)
                elif isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
            with self.subTest(module=name):
                self.assertNotIn("cad_core.step_export", imported)

    def test_step_export_is_not_imported_by_the_package_root(self) -> None:
        import ast

        init = Path(__file__).resolve().parents[1] / "src" / "cad_core" / "__init__.py"
        self.assertNotIn("step_export", init.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
