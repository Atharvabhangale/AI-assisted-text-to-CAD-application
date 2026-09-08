"""Unit tests for the local CadQuery/OpenCascade CAD engine.

Kernel-derived measurements are compared with an explicit tolerance, never with
exact floating-point equality. Object identity is never relied on: repeated
builds are compared by measured values.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from cad_core import validate
from cad_core.local_cad import (
    BACKEND_NAME,
    BACKEND_VERSION,
    BoundingBox,
    GeometryOperationError,
    LocalCadResult,
    UnsupportedGeometryError,
    build_part,
)
from cad_core.model import (
    Box,
    Chamfer,
    Cylinder,
    EdgeSelector,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
    ThroughHole,
)

#: Tolerance for kernel-derived measurements, in millimetres. One nanometre is
#: far below any meaningful millimetre-scale dimension while still absorbing
#: floating-point noise from the geometry kernel. Used for every geometric
#: comparison in this file.
TOLERANCE_MM = 1e-6


def box_document(**overrides: Any) -> Dict[str, Any]:
    feature: Dict[str, Any] = {
        "id": "plate",
        "type": "box",
        "size": {"x": 100, "y": 60, "z": 10},
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


class LocalCadTestCase(unittest.TestCase):
    def part_from(self, document: Dict[str, Any]) -> Part:
        result = validate(document)
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return result.part

    def assertPointAlmostEqual(self, point: Position, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(
                    getattr(point, axis), value, delta=TOLERANCE_MM
                )

    def assertSizeAlmostEqual(self, size: Size, expected: tuple) -> None:
        for axis, value in zip("xyz", expected):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(
                    getattr(size, axis), value, delta=TOLERANCE_MM
                )


# --- 1: origin box ---------------------------------------------------------


class TestOriginBox(LocalCadTestCase):
    """size = 100 x 60 x 10, position = (0, 0, 0)."""

    def build(self) -> LocalCadResult:
        return build_part(
            self.part_from(box_document(feature={"position": {"x": 0, "y": 0, "z": 0}}))
        )

    def test_build_succeeds_and_returns_a_result(self) -> None:
        result = self.build()
        self.assertIsInstance(result, LocalCadResult)
        self.assertEqual(result.part_name, "plate-100x60x10")
        self.assertEqual(result.feature_id, "plate")

    def test_result_contains_a_real_cad_shape(self) -> None:
        result = self.build()
        self.assertIsNotNone(result.shape)
        # A kernel shape, not a stand-in: it answers real topology queries.
        self.assertEqual(result.shape.ShapeType(), "Solid")
        self.assertEqual(len(result.shape.Faces()), 6)
        self.assertEqual(len(result.shape.Edges()), 12)
        self.assertEqual(len(result.shape.Vertices()), 8)

    def test_shape_is_a_solid(self) -> None:
        result = self.build()
        self.assertTrue(result.is_solid())
        self.assertTrue(result.shape.isValid())
        self.assertEqual(result.solid_count(), 1)

    def test_bounding_dimensions(self) -> None:
        self.assertSizeAlmostEqual(self.build().bounding_box().size, (100, 60, 10))

    def test_occupies_the_specified_extents(self) -> None:
        box = self.build().bounding_box()
        self.assertPointAlmostEqual(box.minimum, (0, 0, 0))
        self.assertPointAlmostEqual(box.maximum, (100, 60, 10))

    def test_volume_matches_the_specified_dimensions(self) -> None:
        self.assertAlmostEqual(self.build().volume(), 100 * 60 * 10, delta=TOLERANCE_MM)

    def test_omitted_position_defaults_to_the_origin(self) -> None:
        """The specification's default position is the origin (Section C.1)."""
        implicit = build_part(self.part_from(box_document())).bounding_box()
        self.assertPointAlmostEqual(implicit.minimum, (0, 0, 0))


# --- 2: offset box ---------------------------------------------------------


class TestOffsetBox(LocalCadTestCase):
    """size = 100 x 60 x 10, position = (10, 20, 30)."""

    def build(self) -> LocalCadResult:
        return build_part(
            self.part_from(
                box_document(feature={"position": {"x": 10, "y": 20, "z": 30}})
            )
        )

    def test_build_succeeds(self) -> None:
        self.assertIsInstance(self.build(), LocalCadResult)

    def test_shape_is_a_solid(self) -> None:
        result = self.build()
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)

    def test_minimum_corner_is_the_specified_position(self) -> None:
        self.assertPointAlmostEqual(self.build().bounding_box().minimum, (10, 20, 30))

    def test_maximum_corner_is_position_plus_size(self) -> None:
        self.assertPointAlmostEqual(self.build().bounding_box().maximum, (110, 80, 40))

    def test_dimensions_are_unchanged_by_the_offset(self) -> None:
        self.assertSizeAlmostEqual(self.build().bounding_box().size, (100, 60, 10))

    def test_box_is_not_centred_on_its_position(self) -> None:
        """A centred box would straddle the position; this one must not."""
        box = self.build().bounding_box()
        self.assertGreater(box.minimum.x, 0.0)
        self.assertGreater(box.minimum.y, 0.0)
        self.assertGreater(box.minimum.z, 0.0)
        for axis, centred_min in (("x", 10 - 50), ("y", 20 - 30), ("z", 30 - 5)):
            with self.subTest(axis=axis):
                self.assertNotAlmostEqual(
                    getattr(box.minimum, axis), centred_min, delta=TOLERANCE_MM
                )

    def test_negative_position_is_honoured(self) -> None:
        box = build_part(
            self.part_from(
                box_document(feature={"position": {"x": -50, "y": -30, "z": -5}})
            )
        ).bounding_box()
        self.assertPointAlmostEqual(box.minimum, (-50, -30, -5))
        self.assertPointAlmostEqual(box.maximum, (50, 30, 5))


# --- 3: repeated deterministic builds -------------------------------------


class TestDeterministicBuilds(LocalCadTestCase):
    def test_repeated_builds_measure_identically(self) -> None:
        part = self.part_from(
            box_document(feature={"position": {"x": 10, "y": 20, "z": 30}})
        )
        measurements = set()
        for _ in range(5):
            result = build_part(part)
            box = result.bounding_box()
            measurements.add(
                (
                    (box.minimum.x, box.minimum.y, box.minimum.z),
                    (box.maximum.x, box.maximum.y, box.maximum.z),
                    (box.size.x, box.size.y, box.size.z),
                    result.solid_count(),
                )
            )
        self.assertEqual(len(measurements), 1, msg=f"builds diverged: {measurements}")

    def test_placement_is_stable_within_tolerance(self) -> None:
        part = self.part_from(
            box_document(feature={"position": {"x": 10, "y": 20, "z": 30}})
        )
        first = build_part(part).bounding_box()
        for _ in range(4):
            later = build_part(part).bounding_box()
            self.assertPointAlmostEqual(
                later.minimum, (first.minimum.x, first.minimum.y, first.minimum.z)
            )
            self.assertPointAlmostEqual(
                later.maximum, (first.maximum.x, first.maximum.y, first.maximum.z)
            )

    def test_solid_count_is_stable(self) -> None:
        part = self.part_from(box_document())
        self.assertEqual({build_part(part).solid_count() for _ in range(5)}, {1})

    def test_volume_is_stable(self) -> None:
        part = self.part_from(box_document())
        volumes = [build_part(part).volume() for _ in range(5)]
        for volume in volumes[1:]:
            self.assertAlmostEqual(volume, volumes[0], delta=TOLERANCE_MM)

    def test_each_build_returns_a_fresh_result_object(self) -> None:
        """Identity is not what determinism means here; measurements are."""
        part = self.part_from(box_document())
        self.assertIsNot(build_part(part), build_part(part))


# --- 4: unsupported features ----------------------------------------------


class TestUnsupportedFeatures(LocalCadTestCase):
    @staticmethod
    def single_feature_part(feature: Any) -> Part:
        return Part(
            schema_version="1.0.0", units="mm", name="p", features=(feature,)
        )

    def test_through_hole_is_rejected(self) -> None:
        part = self.single_feature_part(
            ThroughHole(
                id="h", target="plate", diameter=8.0, position=Position(10.0, 10.0, 0.0)
            )
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("through_hole", str(caught.exception))

    def test_subtract_is_rejected(self) -> None:
        part = self.single_feature_part(
            Subtract(id="cut", target="plate", tools=("pin",))
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("subtract", str(caught.exception))

    def test_fillet_is_rejected(self) -> None:
        part = self.single_feature_part(
            Fillet(
                id="f", target="plate", radius=2.0, edges=EdgeSelector(select="all")
            )
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("fillet", str(caught.exception))

    def test_chamfer_is_rejected(self) -> None:
        part = self.single_feature_part(
            Chamfer(
                id="c", target="plate", distance=1.0, edges=EdgeSelector(select="all")
            )
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("chamfer", str(caught.exception))

    def test_a_box_with_a_through_hole_now_builds(self) -> None:
        """Stage 10 widened the engine to constructive + through_hole."""
        document = box_document()
        document["features"].append(
            {
                "id": "hole",
                "type": "through_hole",
                "target": "plate",
                "diameter": 8,
                "position": {"x": 10, "y": 10, "z": 0},
            }
        )
        result = build_part(self.part_from(document))
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")  # target identity kept

    def test_two_constructive_features_now_build_when_one_is_consumed(self) -> None:
        """Stage 11 added 'subtract', so a second solid may be a tool."""
        document = box_document()
        document["features"].extend(
            [
                {
                    "id": "tool",
                    "type": "cylinder",
                    "diameter": 20,
                    "height": 20,
                    "position": {"x": 30, "y": 40, "z": -5},
                },
                {"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool"]},
            ]
        )
        result = build_part(self.part_from(document))
        self.assertTrue(result.is_solid())
        self.assertEqual(result.solid_count(), 1)
        self.assertEqual(result.feature_id, "plate")  # target identity kept

    def test_an_unconsumed_second_solid_is_rejected(self) -> None:
        """Two solids and no subtract: rule S9, checked after the last feature.

        Before Stage 11 the engine refused this up front as unsupported. Now
        several constructive features are legal *when consumed*, so the refusal
        moves to where the specification puts it -- the end of evaluation.
        """
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="a", size=Size(10.0, 10.0, 10.0)),
                Box(id="b", size=Size(10.0, 10.0, 10.0), position=Position(20.0, 0.0, 0.0)),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        message = str(caught.exception)
        self.assertIn("S9", message)
        self.assertIn("'a'", message)
        self.assertIn("'b'", message)

    def test_two_boxes_are_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="a", size=Size(1.0, 1.0, 1.0)),
                Box(id="b", size=Size(1.0, 1.0, 1.0)),
            ),
        )
        with self.assertRaises(GeometryOperationError):
            build_part(part)

    def test_empty_feature_history_is_rejected(self) -> None:
        part = Part(schema_version="1.0.0", units="mm", name="p", features=())
        with self.assertRaises(UnsupportedGeometryError):
            build_part(part)

    def test_unsupported_units_are_rejected(self) -> None:
        part = Part(
            schema_version="1.0.0",
            units="in",
            name="p",
            features=(Box(id="a", size=Size(1.0, 1.0, 1.0)),),
        )
        with self.assertRaises(UnsupportedGeometryError) as caught:
            build_part(part)
        self.assertIn("'in'", str(caught.exception))

    def test_rejection_builds_nothing_partial(self) -> None:
        """An unsupported part yields an exception, never a partial shape."""
        part = self.single_feature_part(
            ThroughHole(
                id="h", target="plate", diameter=8.0, position=Position(0.0, 0.0, 0.0)
            )
        )
        try:
            build_part(part)
        except UnsupportedGeometryError:
            pass
        else:  # pragma: no cover - the assertions above already guard this
            self.fail("a through-hole must be rejected")

    def test_the_supported_set_is_box_and_cylinder(self) -> None:
        """Stage 9 widened the subset; everything else is still refused."""
        self.assertIsNotNone(build_part(self.part_from(box_document())).shape)
        supported = self.single_feature_part(
            Cylinder(id="c", diameter=8.0, height=20.0)
        )
        self.assertTrue(build_part(supported).is_solid())


# --- 5: typed API boundary -------------------------------------------------


class TestTypedApiBoundary(LocalCadTestCase):
    def test_raw_dictionary_is_not_accepted(self) -> None:
        with self.assertRaises(TypeError) as caught:
            build_part(box_document())  # type: ignore[arg-type]
        self.assertIn("Part", str(caught.exception))

    def test_other_types_are_not_accepted(self) -> None:
        for value in (None, 0, [], (), "box", object(), Box(id="b", size=Size(1, 1, 1))):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    build_part(value)  # type: ignore[arg-type]

    def test_validation_result_is_not_accepted_in_place_of_a_part(self) -> None:
        with self.assertRaises(TypeError):
            build_part(validate(box_document()))  # type: ignore[arg-type]


# --- 6: invalid geometry boundary -----------------------------------------


class TestInvalidSpecificationCannotReachTheKernel(LocalCadTestCase):
    def test_invalid_box_never_yields_a_part(self) -> None:
        """A negative extent fails rule S10, so no Part is produced."""
        result = validate(box_document(feature={"size": {"x": -1, "y": 60, "z": 10}}))
        self.assertFalse(result.valid)
        self.assertIsNone(result.part)
        self.assertIn("S10", result.rule_codes())

    def test_invalid_specification_cannot_be_built(self) -> None:
        result = validate(box_document(feature={"size": {"x": 0, "y": 60, "z": 10}}))
        self.assertFalse(result.valid)
        with self.assertRaises(TypeError):
            build_part(result.part)  # type: ignore[arg-type]

    def test_invalid_specification_cannot_be_smuggled_in_as_a_document(self) -> None:
        document = box_document(feature={"size": {"x": -1, "y": 60, "z": 10}})
        self.assertFalse(validate(document).valid)
        with self.assertRaises(TypeError):
            build_part(document)  # type: ignore[arg-type]


# --- backend identity -----------------------------------------------------


class TestBackend(unittest.TestCase):
    def test_backend_is_cadquery_over_opencascade(self) -> None:
        self.assertEqual(BACKEND_NAME, "cadquery")
        self.assertTrue(BACKEND_VERSION)
        import cadquery

        self.assertEqual(BACKEND_VERSION, cadquery.__version__)

    def test_opencascade_bindings_are_importable(self) -> None:
        import OCP  # noqa: F401  the kernel behind CadQuery

    def test_importing_cad_core_does_not_require_cadquery(self) -> None:
        """The validator and generator stay dependency-free.

        ``cad_core/__init__.py`` must not import the local engine, or importing
        the package at all would need the optional CadQuery extra.
        """
        import ast
        from pathlib import Path

        init = Path(__file__).resolve().parents[1] / "src" / "cad_core" / "__init__.py"
        tree = ast.parse(init.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
        self.assertNotIn("cad_core.local_cad", imported)
        self.assertNotIn("cadquery", imported)

    def test_bounding_box_uses_specification_types(self) -> None:
        box = BoundingBox(minimum=Position(0, 0, 0), maximum=Position(1, 2, 3))
        self.assertIsInstance(box.size, Size)
        self.assertEqual((box.size.x, box.size.y, box.size.z), (1, 2, 3))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
