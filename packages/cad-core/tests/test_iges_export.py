"""Unit tests for IGES export and geometric round-trip verification.

What the IGES round trip preserves was measured, not assumed. With
``IGESControl_Writer`` in BRep mode the reference box comes back as a genuine
``Solid`` with shared topology, so these tests assert that. The faces-mode
alternative does not survive as a solid, and a test records that difference
explicitly rather than leaving it as folklore.

Every test writes only into a temporary directory that is removed
automatically; no CAD file is ever left in the repository.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_core import validate
from cad_core.iges_export import (
    IGES_BREP_MODE,
    IGES_EXTENSIONS,
    IgesExportError,
    ImportedIgesShape,
    UnsupportedIgesExtensionError,
    export_iges,
    read_iges,
)
from cad_core.local_cad import LocalCadResult, Position, Size, build_part

#: Tolerance for kernel-derived lengths, in millimetres. Matches the local CAD
#: engine and the STEP exporter, so all three stages measure alike.
TOLERANCE_MM = 1e-6

#: Tolerance for kernel-derived volumes, in cubic millimetres.
TOLERANCE_MM3 = 1e-6

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


class IgesTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def reference_result(self, **overrides: Any) -> LocalCadResult:
        result = validate(box_document(**overrides))
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return build_part(result.part)

    def round_trip(self, extension: str = ".igs") -> ImportedIgesShape:
        written = export_iges(self.reference_result(), self.tmp / f"plate{extension}")
        return read_iges(written)

    def assertPointAlmostEqual(self, point: Position, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(point, axis), value, delta=TOLERANCE_MM)

    def assertSizeAlmostEqual(self, size: Size, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(getattr(size, axis), value, delta=TOLERANCE_MM)


# --- 1, 2, 3, 5: export succeeds -------------------------------------------


class TestExportSucceeds(IgesTestCase):
    def test_export_igs_extension(self) -> None:
        written = export_iges(self.reference_result(), self.tmp / "plate.igs")
        self.assertEqual(written.suffix, ".igs")
        self.assertTrue(written.is_file())

    def test_export_iges_extension(self) -> None:
        written = export_iges(self.reference_result(), self.tmp / "plate.iges")
        self.assertEqual(written.suffix, ".iges")
        self.assertTrue(written.is_file())

    def test_extension_matching_is_case_insensitive(self) -> None:
        for name in ("plate.IGS", "plate.IGES", "plate.Igs", "plate.IgEs"):
            with self.subTest(name=name):
                self.assertTrue(
                    export_iges(self.reference_result(), self.tmp / name).is_file()
                )

    def test_output_files_are_non_empty(self) -> None:
        for extension in IGES_EXTENSIONS:
            with self.subTest(extension=extension):
                written = export_iges(
                    self.reference_result(), self.tmp / f"plate{extension}"
                )
                self.assertGreater(written.stat().st_size, 0)

    def test_export_returns_the_path_written(self) -> None:
        target = self.tmp / "plate.igs"
        self.assertEqual(export_iges(self.reference_result(), target), target)

    def test_string_paths_are_accepted(self) -> None:
        self.assertTrue(
            export_iges(self.reference_result(), str(self.tmp / "plate.igs")).is_file()
        )


# --- 6: the file is really IGES --------------------------------------------


class TestFormatIdentity(IgesTestCase):
    def test_output_is_accepted_by_the_real_iges_reader(self) -> None:
        """Format identity is established by OpenCascade's parser, not the name."""
        written = export_iges(self.reference_result(), self.tmp / "plate.igs")
        imported = read_iges(written)
        self.assertIsInstance(imported, ImportedIgesShape)
        self.assertGreater(imported.face_count(), 0)

    def test_iges_reader_rejects_a_step_file(self) -> None:
        """A STEP file renamed .igs must not pass as IGES."""
        from cad_core.step_export import export_step

        step = export_step(self.reference_result(), self.tmp / "real.step")
        disguised = self.tmp / "disguised.igs"
        disguised.write_bytes(step.read_bytes())
        with self.assertRaises(IgesExportError):
            read_iges(disguised)

    def test_iges_reader_rejects_arbitrary_text(self) -> None:
        """ReadFile alone says RetDone for junk; zero roots is what exposes it."""
        target = self.tmp / "junk.igs"
        target.write_text("this is not an IGES file\n")
        with self.assertRaises(IgesExportError) as caught:
            read_iges(target)
        self.assertIn("no transferable IGES entity", str(caught.exception))

    def test_iges_reader_rejects_an_empty_file(self) -> None:
        target = self.tmp / "empty.igs"
        target.write_text("")
        with self.assertRaises(IgesExportError):
            read_iges(target)

    def test_file_has_the_iges_section_structure(self) -> None:
        """IGES lines carry a section letter and sequence number in cols 73-80."""
        written = export_iges(self.reference_result(), self.tmp / "plate.igs")
        lines = written.read_text(errors="replace").splitlines()
        self.assertTrue(lines[0][72:].startswith("S"))
        self.assertTrue(lines[-1][72:].startswith("T"))
        self.assertEqual({line[72] for line in lines if len(line) > 72}, set("SGDPT"))


# --- 7-13: what the round trip actually preserves --------------------------


class TestRoundTripPreservation(IgesTestCase):
    """Reference case: 100 x 60 x 10 mm at (10, 20, 30), written in BRep mode."""

    def test_iges_can_be_imported_by_the_real_importer(self) -> None:
        self.assertIsNotNone(self.round_trip().shape)

    def test_imported_representation_is_a_solid(self) -> None:
        """Measured, not assumed: BRep mode does preserve solid topology."""
        imported = self.round_trip()
        self.assertEqual(imported.shape_type(), "Solid")
        self.assertTrue(imported.is_solid())
        self.assertTrue(imported.shape.isValid())

    def test_imported_solid_count(self) -> None:
        self.assertEqual(self.round_trip().solid_count(), 1)

    def test_imported_topology_is_a_stitched_box(self) -> None:
        """12 edges and 8 vertices means the faces are shared, not loose."""
        imported = self.round_trip()
        self.assertEqual(imported.face_count(), 6)
        self.assertEqual(imported.edge_count(), 12)
        self.assertEqual(imported.vertex_count(), 8)

    def test_imported_minimum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().minimum, EXPECTED_MINIMUM
        )

    def test_imported_maximum_corner(self) -> None:
        self.assertPointAlmostEqual(
            self.round_trip().bounding_box().maximum, EXPECTED_MAXIMUM
        )

    def test_imported_dimensions(self) -> None:
        self.assertSizeAlmostEqual(
            self.round_trip().bounding_box().size, EXPECTED_DIMENSIONS
        )

    def test_imported_volume(self) -> None:
        imported = self.round_trip()
        self.assertTrue(imported.is_solid(), msg="volume is only meaningful for a solid")
        self.assertAlmostEqual(imported.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3)

    def test_extents_match_the_source_result(self) -> None:
        source = self.reference_result()
        imported = read_iges(export_iges(source, self.tmp / "plate.igs"))
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

    def test_round_trip_holds_for_both_extensions(self) -> None:
        for extension in IGES_EXTENSIONS:
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

    def test_origin_box_round_trips(self) -> None:
        result = self.reference_result(feature={"position": {"x": 0, "y": 0, "z": 0}})
        imported = read_iges(export_iges(result, self.tmp / "origin.igs"))
        self.assertPointAlmostEqual(imported.bounding_box().minimum, (0, 0, 0))
        self.assertPointAlmostEqual(imported.bounding_box().maximum, (100, 60, 10))

    def test_negative_position_round_trips(self) -> None:
        result = self.reference_result(
            feature={"position": {"x": -50, "y": -30, "z": -5}}
        )
        imported = read_iges(export_iges(result, self.tmp / "negative.igs"))
        self.assertPointAlmostEqual(imported.bounding_box().minimum, (-50, -30, -5))
        self.assertPointAlmostEqual(imported.bounding_box().maximum, (50, 30, 5))

    def test_faces_mode_would_not_preserve_a_solid(self) -> None:
        """Why BRep mode is used: mode 0 loses the solid, and its volume lies.

        Recorded so the choice of IGES_BREP_MODE is evidenced rather than
        asserted. This drives OpenCascade directly to compare the modes.
        """
        from cadquery import Shape
        from OCP.IGESControl import IGESControl_Reader, IGESControl_Writer

        self.assertEqual(IGES_BREP_MODE, 1)
        target = self.tmp / "faces-mode.igs"
        writer = IGESControl_Writer("MM", 0)  # faces mode, the OCC default
        self.assertTrue(writer.AddShape(self.reference_result().shape.wrapped))
        writer.ComputeModel()
        self.assertTrue(writer.Write(str(target)))

        reader = IGESControl_Reader()
        reader.ReadFile(str(target))
        reader.TransferRoots()
        shape = Shape.cast(reader.OneShape())

        self.assertEqual(shape.ShapeType(), "Compound")   # not a Solid
        self.assertEqual(len(shape.Solids()), 0)
        self.assertEqual(len(shape.Faces()), 6)
        self.assertEqual(len(shape.Edges()), 24)          # unshared: 6 x 4
        self.assertNotAlmostEqual(shape.Volume(), EXPECTED_VOLUME, delta=1.0)


# --- 14: repeated exports --------------------------------------------------


class TestRepeatedExports(IgesTestCase):
    def test_repeated_exports_are_geometrically_equivalent(self) -> None:
        source = self.reference_result()
        measurements = set()
        for index in range(3):
            imported = read_iges(export_iges(source, self.tmp / f"plate-{index}.igs"))
            box = imported.bounding_box()
            measurements.add(
                (
                    imported.shape_type(),
                    imported.solid_count(),
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    round(imported.volume(), 9),
                )
            )
        self.assertEqual(len(measurements), 1, msg=f"exports diverged: {measurements}")

    def test_repeated_exports_to_the_same_path_overwrite_cleanly(self) -> None:
        source = self.reference_result()
        target = self.tmp / "plate.igs"
        for _ in range(3):
            export_iges(source, target)
            imported = read_iges(target)
            self.assertTrue(imported.is_solid())
            self.assertPointAlmostEqual(
                imported.bounding_box().minimum, EXPECTED_MINIMUM
            )

    def test_byte_identity_is_not_required(self) -> None:
        """IGES headers carry a timestamp, so bytes may differ; geometry may not."""
        source = self.reference_result()
        first = export_iges(source, self.tmp / "a.igs")
        second = export_iges(source, self.tmp / "b.igs")
        for path in (first, second):
            imported = read_iges(path)
            self.assertTrue(imported.is_solid())
            self.assertPointAlmostEqual(
                imported.bounding_box().minimum, EXPECTED_MINIMUM
            )
            self.assertAlmostEqual(
                imported.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3
            )


# --- 15: the source result is not mutated ----------------------------------


class TestSourceIsUnmodified(IgesTestCase):
    def test_export_does_not_mutate_the_source_result(self) -> None:
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

        export_iges(source, self.tmp / "plate.igs")

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
        )
        self.assertPointAlmostEqual(
            after_box.maximum,
            (before_box.maximum.x, before_box.maximum.y, before_box.maximum.z),
        )

    def test_one_result_can_feed_both_exporters(self) -> None:
        """IGES export must not disturb a later STEP export of the same result."""
        from cad_core.step_export import export_step, read_step

        source = self.reference_result()
        export_iges(source, self.tmp / "plate.igs")
        step_back = read_step(export_step(source, self.tmp / "plate.step"))
        self.assertTrue(step_back.is_solid())
        self.assertAlmostEqual(step_back.volume(), EXPECTED_VOLUME, delta=TOLERANCE_MM3)


# --- 4, 16: rejected input and extensions ----------------------------------


class TestRejectedInput(IgesTestCase):
    def test_unsupported_extensions_are_rejected(self) -> None:
        for name in (
            "plate.step", "plate.stp", "plate.stl", "plate.3mf", "plate.dxf",
            "plate.brep", "plate.svg", "plate.txt", "plate",
        ):
            with self.subTest(name=name):
                with self.assertRaises(UnsupportedIgesExtensionError):
                    export_iges(self.reference_result(), self.tmp / name)

    def test_a_step_extension_is_not_silently_redirected(self) -> None:
        target = self.tmp / "plate.step"
        with self.assertRaises(UnsupportedIgesExtensionError) as caught:
            export_iges(self.reference_result(), target)
        self.assertIn(".igs", str(caught.exception))
        self.assertFalse(target.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_missing_directory_fails_explicitly(self) -> None:
        target = self.tmp / "no-such-directory" / "plate.igs"
        with self.assertRaises(IgesExportError) as caught:
            export_iges(self.reference_result(), target)
        self.assertIn("does not exist", str(caught.exception))

    def test_raw_dictionary_is_rejected(self) -> None:
        with self.assertRaises(TypeError) as caught:
            export_iges(box_document(), self.tmp / "plate.igs")  # type: ignore[arg-type]
        self.assertIn("LocalCadResult", str(caught.exception))

    def test_bare_kernel_shape_is_rejected(self) -> None:
        shape = self.reference_result().shape
        with self.assertRaises(TypeError):
            export_iges(shape, self.tmp / "plate.igs")  # type: ignore[arg-type]

    def test_other_types_are_rejected(self) -> None:
        for value in (None, 0, [], (), "solid", object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    export_iges(value, self.tmp / "plate.igs")  # type: ignore[arg-type]

    def test_validation_result_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            export_iges(validate(box_document()), self.tmp / "p.igs")  # type: ignore[arg-type]

    def test_reading_a_missing_file_fails_explicitly(self) -> None:
        with self.assertRaises(IgesExportError):
            read_iges(self.tmp / "absent.igs")

    def test_reading_an_unsupported_extension_is_rejected(self) -> None:
        target = self.tmp / "plate.step"
        target.write_text("irrelevant")
        with self.assertRaises(UnsupportedIgesExtensionError):
            read_iges(target)


# --- package boundary ------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
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

    def test_the_two_exporters_are_independent(self) -> None:
        self.assertNotIn(
            "cad_core.iges_export", self.imports_of(self.SOURCE / "step_export.py")
        )
        self.assertNotIn(
            "cad_core.step_export", self.imports_of(self.SOURCE / "iges_export.py")
        )

    def test_nothing_upstream_imports_iges(self) -> None:
        for name in (
            "model.py", "validator.py", "errors.py", "rules.py", "geometry.py",
            "featurescript.py", "onshape_adapter.py", "onshape_fakes.py",
            "local_cad.py", "__init__.py",
        ):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.iges_export", self.imports_of(self.SOURCE / name)
                )

    def test_iges_export_is_not_imported_by_the_package_root(self) -> None:
        text = (self.SOURCE / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("iges_export", text)

    def test_iges_exporter_uses_opencascade_directly(self) -> None:
        """CadQuery has no IGES support; OCP.IGESControl is the real mechanism."""
        imports = self.imports_of(self.SOURCE / "iges_export.py")
        self.assertIn("OCP.IGESControl", imports)
        from cadquery import exporters, importers

        self.assertFalse(any("IGE" in n.upper() for n in dir(exporters.ExportTypes)))
        self.assertFalse(hasattr(importers, "importIges"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
