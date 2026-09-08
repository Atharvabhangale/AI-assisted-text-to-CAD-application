"""Unit tests for the neutral render representation.

The render model is a visualization contract, so nothing here asserts that it
preserves CAD topology. What is asserted: it contains only plain Python data,
it serializes to JSON with no custom encoder, its triangles index valid
vertices, its bounds come from its own vertices, and its geometry matches the
B-rep it was tessellated from within the documented tolerance.
"""

from __future__ import annotations

import dataclasses
import json
import math
import unittest
from typing import Any, Dict

from cad_core import validate
from cad_core.local_cad import (
    SUPPORTED_UNITS,
    GeometryOperationError,
    LocalCadResult,
    UnsupportedGeometryError,
    build_part,
)
from cad_core.model import Box, Part, Position, Size
from cad_core.render_model import (
    COORDINATE_SYSTEM,
    DEFAULT_ANGULAR_DEFLECTION_RAD,
    DEFAULT_LINEAR_DEFLECTION_MM,
    NORMAL_BINDING,
    RENDER_FORMAT_VERSION,
    RENDER_UNITS,
    WINDING,
    RenderModel,
    TessellationSettings,
    build_render_model,
)

#: Tolerance for render coordinates, in millimetres: the linear deflection a
#: tessellation is permitted to deviate by, plus kernel float noise. Same basis
#: as the STL exporter's envelope tolerance.
KERNEL_NOISE_MM = 1e-6
RENDER_TOLERANCE_MM = DEFAULT_LINEAR_DEFLECTION_MM + KERNEL_NOISE_MM

#: Tolerance for unit-length normals (dimensionless).
NORMAL_TOLERANCE = 1e-9

EXPECTED_MINIMUM = (10.0, 20.0, 30.0)
EXPECTED_MAXIMUM = (110.0, 80.0, 40.0)
EXPECTED_DIMENSIONS = (100.0, 60.0, 10.0)

#: Six planar faces, tessellated face by face: 4 nodes and 2 triangles each.
EXPECTED_VERTICES = 24
EXPECTED_TRIANGLES = 12

BOX_CENTRE = (60.0, 50.0, 35.0)


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


class RenderTestCase(unittest.TestCase):
    def reference_result(self, **overrides: Any) -> LocalCadResult:
        result = validate(box_document(**overrides))
        self.assertTrue(result.valid, msg=f"{[str(e) for e in result.errors]}")
        assert result.part is not None
        return build_part(result.part)

    def reference_model(self, **overrides: Any) -> RenderModel:
        return build_render_model(self.reference_result(**overrides))

    def assertTripleAlmostEqual(
        self, actual: tuple, expected: tuple, delta: float = RENDER_TOLERANCE_MM
    ) -> None:
        for axis, (got, want) in enumerate(zip(actual, expected)):
            with self.subTest(axis="xyz"[axis]):
                self.assertAlmostEqual(got, want, delta=delta)


# --- 1: a valid box produces a RenderModel ---------------------------------


class TestModelConstruction(RenderTestCase):
    def test_a_valid_box_produces_a_render_model(self) -> None:
        model = self.reference_model()
        self.assertIsInstance(model, RenderModel)
        self.assertEqual(model.part_name, "plate-100x60x10")
        self.assertEqual(model.feature_id, "plate")

    def test_contract_metadata_is_present(self) -> None:
        model = self.reference_model()
        self.assertEqual(model.format_version, RENDER_FORMAT_VERSION)
        self.assertEqual(model.units, "mm")
        self.assertEqual(model.units, RENDER_UNITS)
        self.assertEqual(model.coordinate_system, COORDINATE_SYSTEM)
        self.assertEqual(model.winding, WINDING)
        self.assertEqual(model.normal_binding, NORMAL_BINDING)

    def test_units_come_from_the_local_engine_constraint(self) -> None:
        """The engine builds mm parts only, so the unit is not a guess."""
        self.assertEqual(RENDER_UNITS, SUPPORTED_UNITS[0])
        self.assertEqual(SUPPORTED_UNITS, ("mm",))

    def test_tessellation_settings_are_recorded(self) -> None:
        model = self.reference_model()
        self.assertIsInstance(model.tessellation, TessellationSettings)
        self.assertEqual(
            model.tessellation.linear_deflection_mm, DEFAULT_LINEAR_DEFLECTION_MM
        )
        self.assertEqual(
            model.tessellation.angular_deflection_rad, DEFAULT_ANGULAR_DEFLECTION_RAD
        )

    def test_explicit_tolerances_are_recorded(self) -> None:
        model = build_render_model(
            self.reference_result(), tolerance=0.05, angular_tolerance=0.2
        )
        self.assertEqual(model.tessellation.linear_deflection_mm, 0.05)
        self.assertEqual(model.tessellation.angular_deflection_rad, 0.2)

    def test_non_positive_tolerances_are_rejected(self) -> None:
        for kwargs in (
            {"tolerance": 0.0}, {"tolerance": -0.01},
            {"angular_tolerance": 0.0}, {"angular_tolerance": -0.1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    build_render_model(self.reference_result(), **kwargs)


# --- 2: no CadQuery/OCP objects escape -------------------------------------


class TestNoKernelObjects(RenderTestCase):
    ALLOWED = (str, int, float, bool, type(None))

    def walk(self, value: Any, path: str = "model") -> None:
        """Recursively assert every value is application-neutral."""
        if isinstance(value, self.ALLOWED):
            return
        if isinstance(value, (tuple, list)):
            for index, item in enumerate(value):
                self.walk(item, f"{path}[{index}]")
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self.walk(key, f"{path}.key")
                self.walk(item, f"{path}[{key!r}]")
            return
        if dataclasses.is_dataclass(value):
            module = type(value).__module__
            self.assertTrue(
                module.startswith("cad_core"),
                msg=f"{path} is a {module}.{type(value).__name__}",
            )
            for field in dataclasses.fields(value):
                self.walk(getattr(value, field.name), f"{path}.{field.name}")
            return
        self.fail(f"{path} is a non-neutral {type(value).__module__}.{type(value).__name__}")

    def test_render_model_contains_only_neutral_data(self) -> None:
        self.walk(self.reference_model())

    def test_no_kernel_type_names_appear_anywhere(self) -> None:
        model = self.reference_model()
        forbidden = ("TopoDS", "Workplane", "cadquery", "OCP", "Poly_Triangulation",
                     "gp_Pnt", "Vector")
        for field in dataclasses.fields(model):
            value = getattr(model, field.name)
            for name in forbidden:
                with self.subTest(field=field.name, forbidden=name):
                    self.assertNotIn(name, type(value).__name__)

    def test_the_model_has_no_shape_attribute(self) -> None:
        model = self.reference_model()
        for attribute in ("shape", "wrapped", "workplane", "solid", "triangulation"):
            with self.subTest(attribute=attribute):
                self.assertFalse(hasattr(model, attribute))

    def test_vertices_are_plain_float_triples(self) -> None:
        model = self.reference_model()
        for index, vertex in enumerate(model.vertices):
            with self.subTest(vertex=index):
                self.assertIsInstance(vertex, tuple)
                self.assertEqual(len(vertex), 3)
                for component in vertex:
                    self.assertIs(type(component), float)

    def test_triangles_are_plain_int_triples(self) -> None:
        model = self.reference_model()
        for index, triangle in enumerate(model.triangles):
            with self.subTest(triangle=index):
                self.assertIsInstance(triangle, tuple)
                self.assertEqual(len(triangle), 3)
                for component in triangle:
                    self.assertIs(type(component), int)


# --- 3, 4: serialization ---------------------------------------------------


class TestSerialization(RenderTestCase):
    def test_to_dict_returns_json_compatible_data(self) -> None:
        data = self.reference_model().to_dict()
        self.assertIsInstance(data, dict)
        TestNoKernelObjects().walk(data, "dict")

    def test_json_serialization_succeeds_without_a_custom_encoder(self) -> None:
        text = json.dumps(self.reference_model().to_dict())
        self.assertGreater(len(text), 0)

    def test_json_round_trips_to_equal_data(self) -> None:
        data = self.reference_model().to_dict()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_serialized_structure_has_the_documented_keys(self) -> None:
        data = self.reference_model().to_dict()
        self.assertEqual(
            sorted(data),
            sorted(
                [
                    "format_version", "part_name", "feature_id", "units",
                    "coordinate_system", "winding", "normal_binding",
                    "vertices", "triangles", "normals", "bounds", "tessellation",
                ]
            ),
        )
        self.assertEqual(sorted(data["bounds"]), ["maximum", "minimum", "size"])
        self.assertEqual(
            sorted(data["tessellation"]),
            ["angular_deflection_rad", "linear_deflection_mm"],
        )

    def test_serialized_arrays_are_lists_of_lists(self) -> None:
        data = self.reference_model().to_dict()
        for key in ("vertices", "triangles", "normals"):
            with self.subTest(key=key):
                self.assertIsInstance(data[key], list)
                self.assertIsInstance(data[key][0], list)
                self.assertEqual(len(data[key][0]), 3)

    def test_serialized_counts_agree_with_the_model(self) -> None:
        model = self.reference_model()
        data = model.to_dict()
        self.assertEqual(len(data["vertices"]), model.vertex_count())
        self.assertEqual(len(data["triangles"]), model.triangle_count())
        self.assertEqual(len(data["normals"]), model.vertex_count())


# --- 5, 6, 7, 8, 9: geometry -----------------------------------------------


class TestGeometry(RenderTestCase):
    def test_triangle_count_is_positive(self) -> None:
        self.assertGreater(self.reference_model().triangle_count(), 0)

    def test_counts_are_as_measured_for_a_box(self) -> None:
        """Per-face tessellation: 6 faces x 4 nodes, 6 x 2 triangles."""
        model = self.reference_model()
        self.assertEqual(model.vertex_count(), EXPECTED_VERTICES)
        self.assertEqual(model.triangle_count(), EXPECTED_TRIANGLES)

    def test_vertices_are_not_shared_across_faces(self) -> None:
        """Measured, and desirable: shared vertices would round off the edges."""
        model = self.reference_model()
        self.assertEqual(model.vertex_count(), EXPECTED_VERTICES)
        self.assertEqual(len(set(model.vertices)), 8)  # only 8 distinct positions

    def test_every_triangle_references_valid_vertex_indices(self) -> None:
        model = self.reference_model()
        limit = model.vertex_count()
        for position, triangle in enumerate(model.triangles):
            with self.subTest(triangle=position):
                for index in triangle:
                    self.assertGreaterEqual(index, 0)
                    self.assertLess(index, limit)

    def test_no_triangle_has_out_of_range_indices(self) -> None:
        model = self.reference_model()
        flat = [index for triangle in model.triangles for index in triangle]
        self.assertEqual(min(flat), 0)
        self.assertLess(max(flat), model.vertex_count())

    def test_no_triangle_is_degenerate(self) -> None:
        model = self.reference_model()
        for position, triangle in enumerate(model.triangles):
            with self.subTest(triangle=position):
                self.assertEqual(len(set(triangle)), 3)

    def test_every_vertex_lies_within_the_expected_bounds(self) -> None:
        model = self.reference_model()
        for index, (x, y, z) in enumerate(model.vertices):
            with self.subTest(vertex=index):
                for value, low, high in (
                    (x, EXPECTED_MINIMUM[0], EXPECTED_MAXIMUM[0]),
                    (y, EXPECTED_MINIMUM[1], EXPECTED_MAXIMUM[1]),
                    (z, EXPECTED_MINIMUM[2], EXPECTED_MAXIMUM[2]),
                ):
                    self.assertGreaterEqual(value, low - RENDER_TOLERANCE_MM)
                    self.assertLessEqual(value, high + RENDER_TOLERANCE_MM)

    def test_render_bounds_match_the_expected_box(self) -> None:
        bounds = self.reference_model().bounds
        self.assertTripleAlmostEqual(bounds.minimum, EXPECTED_MINIMUM)
        self.assertTripleAlmostEqual(bounds.maximum, EXPECTED_MAXIMUM)
        self.assertTripleAlmostEqual(bounds.size, EXPECTED_DIMENSIONS)

    def test_render_bounds_are_derived_from_the_render_vertices(self) -> None:
        """Not copied from the B-rep: recomputed from the vertices themselves."""
        model = self.reference_model()
        xs, ys, zs = zip(*model.vertices)
        self.assertEqual(model.bounds.minimum, (min(xs), min(ys), min(zs)))
        self.assertEqual(model.bounds.maximum, (max(xs), max(ys), max(zs)))

    def test_render_bounds_match_the_brep_bounds_within_tolerance(self) -> None:
        """And report the difference, which for planar geometry is zero."""
        source = self.reference_result()
        model = build_render_model(source)
        brep = source.bounding_box()
        brep_min = (brep.minimum.x, brep.minimum.y, brep.minimum.z)
        brep_max = (brep.maximum.x, brep.maximum.y, brep.maximum.z)
        self.assertTripleAlmostEqual(model.bounds.minimum, brep_min)
        self.assertTripleAlmostEqual(model.bounds.maximum, brep_max)
        difference = max(
            abs(a - b)
            for a, b in list(zip(model.bounds.minimum, brep_min))
            + list(zip(model.bounds.maximum, brep_max))
        )
        self.assertLessEqual(difference, RENDER_TOLERANCE_MM)

    def test_render_bounds_are_tighter_than_the_post_mesh_brep_query(self) -> None:
        """A measured subtlety, recorded so it cannot surprise anyone later.

        OpenCascade's ``BoundingBox()`` bounds an attached triangulation with a
        small gap, so querying the B-rep *after* tessellation returns bounds
        inflated outward by about 1e-7 mm. The render vertices stay exact. This
        is why render bounds are computed from the vertices rather than copied
        from the B-rep, and it is not specific to this module -- STL export
        meshes the shape and has the same effect.
        """
        source = self.reference_result()
        before = source.bounding_box().minimum.x
        self.assertAlmostEqual(before, 10.0, delta=KERNEL_NOISE_MM)

        model = build_render_model(source)
        after = source.bounding_box().minimum.x

        self.assertEqual(model.bounds.minimum[0], 10.0)          # exact
        self.assertLessEqual(after, before)                      # inflated outward
        self.assertAlmostEqual(after, 10.0, delta=KERNEL_NOISE_MM)  # still tiny
        self.assertAlmostEqual(source.volume(), 60000.0, delta=KERNEL_NOISE_MM)

    def test_origin_box(self) -> None:
        model = self.reference_model(feature={"position": {"x": 0, "y": 0, "z": 0}})
        self.assertTripleAlmostEqual(model.bounds.minimum, (0, 0, 0))
        self.assertTripleAlmostEqual(model.bounds.maximum, (100, 60, 10))

    def test_negative_position(self) -> None:
        model = self.reference_model(
            feature={"position": {"x": -50, "y": -30, "z": -5}}
        )
        self.assertTripleAlmostEqual(model.bounds.minimum, (-50, -30, -5))
        self.assertTripleAlmostEqual(model.bounds.maximum, (50, 30, 5))


# --- 12: normals -----------------------------------------------------------


class TestNormals(RenderTestCase):
    def test_there_is_one_normal_per_vertex(self) -> None:
        model = self.reference_model()
        self.assertEqual(len(model.normals), model.vertex_count())
        self.assertEqual(model.normal_binding, "per_vertex")

    def test_normals_are_finite(self) -> None:
        for index, normal in enumerate(self.reference_model().normals):
            with self.subTest(normal=index):
                for component in normal:
                    self.assertTrue(math.isfinite(component))

    def test_normals_are_unit_length(self) -> None:
        for index, (x, y, z) in enumerate(self.reference_model().normals):
            with self.subTest(normal=index):
                self.assertAlmostEqual(
                    math.sqrt(x * x + y * y + z * z), 1.0, delta=NORMAL_TOLERANCE
                )

    def test_box_normals_are_the_six_axis_directions(self) -> None:
        distinct = {
            tuple(round(component, 9) for component in normal)
            for normal in self.reference_model().normals
        }
        self.assertEqual(
            distinct,
            {
                (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
                (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
            },
        )

    def test_winding_yields_outward_normals(self) -> None:
        """The documented convention, checked against the box's centre."""
        model = self.reference_model()
        for position, (a, b, c) in enumerate(model.triangles):
            with self.subTest(triangle=position):
                va, vb, vc = (model.vertices[i] for i in (a, b, c))
                ux, uy, uz = (vb[i] - va[i] for i in range(3))
                vx, vy, vz = (vc[i] - va[i] for i in range(3))
                nx = uy * vz - uz * vy
                ny = uz * vx - ux * vz
                nz = ux * vy - uy * vx
                centroid = tuple((va[i] + vb[i] + vc[i]) / 3 for i in range(3))
                outward = tuple(centroid[i] - BOX_CENTRE[i] for i in range(3))
                self.assertGreater(
                    nx * outward[0] + ny * outward[1] + nz * outward[2], 0.0
                )

    def test_vertex_normals_agree_with_their_triangles(self) -> None:
        """Per-vertex normals must point the same way as the faces they bound."""
        model = self.reference_model()
        for position, (a, b, c) in enumerate(model.triangles):
            va, vb, vc = (model.vertices[i] for i in (a, b, c))
            ux, uy, uz = (vb[i] - va[i] for i in range(3))
            vx, vy, vz = (vc[i] - va[i] for i in range(3))
            face = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
            for index in (a, b, c):
                with self.subTest(triangle=position, vertex=index):
                    normal = model.normals[index]
                    self.assertGreater(
                        sum(face[i] * normal[i] for i in range(3)), 0.0
                    )


# --- 10, 11: determinism ---------------------------------------------------


class TestDeterminism(RenderTestCase):
    def test_repeated_generation_is_geometrically_equivalent(self) -> None:
        source = self.reference_result()
        models = [build_render_model(source) for _ in range(4)]
        signatures = {
            (
                model.vertex_count(),
                model.triangle_count(),
                model.bounds.minimum,
                model.bounds.maximum,
            )
            for model in models
        }
        self.assertEqual(len(signatures), 1)

    def test_repeated_generation_is_byte_deterministic(self) -> None:
        """Measured, not assumed.

        CadQuery's tessellate walks faces in a stable order and OpenCascade's
        mesher is deterministic for this geometry, so the serialized JSON came
        out identical across repeated builds. Recorded as an empirical result
        for this backend and geometry.
        """
        source = self.reference_result()
        payloads = {
            json.dumps(build_render_model(source).to_dict(), sort_keys=True)
            for _ in range(4)
        }
        self.assertEqual(len(payloads), 1)

    def test_separate_builds_of_the_same_part_agree(self) -> None:
        """A freshly built B-rep must tessellate to the same render model."""
        first = json.dumps(self.reference_model().to_dict(), sort_keys=True)
        second = json.dumps(self.reference_model().to_dict(), sort_keys=True)
        self.assertEqual(first, second)

    def test_vertex_and_triangle_order_is_stable(self) -> None:
        source = self.reference_result()
        first = build_render_model(source)
        for _ in range(3):
            later = build_render_model(source)
            self.assertEqual(later.vertices, first.vertices)
            self.assertEqual(later.triangles, first.triangles)
            self.assertEqual(later.normals, first.normals)


# --- 13: the source is not mutated -----------------------------------------


class TestSourceIsUnmodified(RenderTestCase):
    def test_the_source_result_is_not_mutated(self) -> None:
        source = self.reference_result()
        before = (
            source.part_name,
            source.feature_id,
            source.is_solid(),
            source.solid_count(),
            source.volume(),
        )
        shape_before = source.shape
        build_render_model(source)
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

    def test_the_brep_remains_a_solid_after_tessellation(self) -> None:
        source = self.reference_result()
        build_render_model(source)
        self.assertEqual(source.shape.ShapeType(), "Solid")
        self.assertTrue(source.is_solid())
        self.assertEqual(len(source.shape.Faces()), 6)
        self.assertAlmostEqual(source.volume(), 60000.0, delta=KERNEL_NOISE_MM)

    def test_the_render_model_is_immutable(self) -> None:
        model = self.reference_model()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            model.part_name = "other"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            model.bounds.minimum = (0.0, 0.0, 0.0)  # type: ignore[misc]


# --- 14, 15: input boundary ------------------------------------------------


class TestInputBoundary(RenderTestCase):
    def test_a_raw_dictionary_cannot_build_a_render_model(self) -> None:
        with self.assertRaises(TypeError) as caught:
            build_render_model(box_document())  # type: ignore[arg-type]
        self.assertIn("LocalCadResult", str(caught.exception))

    def test_a_bare_kernel_shape_is_rejected(self) -> None:
        shape = self.reference_result().shape
        with self.assertRaises(TypeError):
            build_render_model(shape)  # type: ignore[arg-type]

    def test_loose_mesh_data_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            build_render_model(
                {"vertices": [[0, 0, 0]], "triangles": [[0, 0, 0]]}  # type: ignore[arg-type]
            )

    def test_other_types_are_rejected(self) -> None:
        for value in (None, 0, [], (), "solid", object()):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(TypeError):
                    build_render_model(value)  # type: ignore[arg-type]

    def test_a_validation_result_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            build_render_model(validate(box_document()))  # type: ignore[arg-type]

    def test_unsupported_geometry_is_still_rejected_by_the_backend(self) -> None:
        """Rendering adds no route around the local engine's supported subset."""
        from cad_core.model import ThroughHole

        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                ThroughHole(
                    id="h",
                    target="plate",
                    diameter=8.0,
                    position=Position(0.0, 0.0, 0.0),
                ),
            ),
        )
        with self.assertRaises(UnsupportedGeometryError):
            build_part(part)

    def test_an_orphan_solid_cannot_reach_the_renderer(self) -> None:
        """Two disjoint boxes and no subtract: rule S9 leaves nothing to render.

        Stage 11 made several constructive features legal *when consumed*, so
        the engine now refuses this at the end of evaluation (S9) rather than
        up front. Either way no geometry escapes to the render model.
        """
        part = Part(
            schema_version="1.0.0",
            units="mm",
            name="p",
            features=(
                Box(id="a", size=Size(1.0, 1.0, 1.0)),
                Box(id="b", size=Size(1.0, 1.0, 1.0), position=Position(2.0, 0.0, 0.0)),
            ),
        )
        with self.assertRaises(GeometryOperationError) as caught:
            build_part(part)
        self.assertIn("S9", str(caught.exception))

    def test_an_invalid_specification_cannot_reach_the_renderer(self) -> None:
        result = validate(box_document(feature={"size": {"x": -1, "y": 60, "z": 10}}))
        self.assertFalse(result.valid)
        with self.assertRaises(TypeError):
            build_render_model(result.part)  # type: ignore[arg-type]


# --- package boundary ------------------------------------------------------


class TestPackageBoundary(unittest.TestCase):
    from pathlib import Path as _Path

    SOURCE = _Path(__file__).resolve().parents[1] / "src" / "cad_core"

    @staticmethod
    def imports_of(path: Any) -> set:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
        return found

    def test_nothing_upstream_depends_on_the_render_representation(self) -> None:
        for name in (
            "model.py", "validator.py", "errors.py", "rules.py", "geometry.py",
            "featurescript.py", "onshape_adapter.py", "onshape_fakes.py",
            "local_cad.py", "step_export.py", "iges_export.py", "stl_export.py",
            "__init__.py",
        ):
            with self.subTest(module=name):
                self.assertNotIn(
                    "cad_core.render_model", self.imports_of(self.SOURCE / name)
                )

    def test_the_render_model_does_not_depend_on_the_exporters(self) -> None:
        imports = self.imports_of(self.SOURCE / "render_model.py")
        for module in (
            "cad_core.step_export", "cad_core.iges_export", "cad_core.stl_export",
            "cad_core.validator", "cad_core.featurescript",
        ):
            with self.subTest(module=module):
                self.assertNotIn(module, imports)
        self.assertIn("cad_core.local_cad", imports)

    def test_the_specification_gains_no_mesh_or_render_concept(self) -> None:
        model = (self.SOURCE / "model.py").read_text(encoding="utf-8").lower()
        for word in ("mesh", "triangle", "tessell", "render", "normal", "webgl"):
            with self.subTest(word=word):
                self.assertNotIn(word, model)

    def test_render_model_is_not_imported_by_the_package_root(self) -> None:
        text = (self.SOURCE / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("render_model", text)

    def test_no_browser_or_frontend_dependency_was_added(self) -> None:
        """Checked on the parsed imports, so prose in docstrings cannot trip it."""
        imports = {
            module.split(".")[0]
            for module in self.imports_of(self.SOURCE / "render_model.py")
        }
        forbidden = {
            "three", "threejs", "react", "flask", "fastapi", "django", "http",
            "urllib", "requests", "httpx", "websockets", "aiohttp", "socket",
            "pygltflib", "trimesh", "numpy",
        }
        self.assertEqual(imports & forbidden, set())

    def test_the_render_model_uses_only_the_standard_library_and_cad_core(self) -> None:
        """Beyond cad_core, only stdlib: the contract needs no third party."""
        imports = {
            module.split(".")[0]
            for module in self.imports_of(self.SOURCE / "render_model.py")
        }
        self.assertEqual(
            imports - {"cad_core"}, {"__future__", "math", "dataclasses", "typing"}
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
