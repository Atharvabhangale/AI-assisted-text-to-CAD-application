"""Stage 42: two CAD engines, one contract, compared on the same geometry.

The point of this module is the **cross-backend golden tests**: the same six
parts built by CadQuery and by FreeCAD, measured the same way, and compared
geometrically. What is asserted is volume, solid count, bounding box and
topology counts within explicit tolerances -- never topology identifiers,
never face or edge numbering, never internal OpenCascade structure. Two
engines are allowed to arrive at the same solid by different routes.

Running the FreeCAD half
------------------------
FreeCAD is not a pip package and is not in Ubuntu 24.04. These tests find it
through ``CAD_FREECAD_HOME``, and its bundled libraries must be on the
dynamic linker's path *before Python starts*::

    CAD_FREECAD_HOME=/home/user/freecad/squashfs-root \\
    LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib \\
    python3 -m unittest tests_experimental.test_cad_backends

Without that, every FreeCAD test **skips** and says so. A skip is an honest
"not measured"; none of them silently passes by falling back to CadQuery.
"""

from __future__ import annotations

import ast
import math
import pathlib
import tempfile
import time
import unittest

from cad_core.render_model import RenderModel

from cad_experimental import cad_backend as cb
from cad_experimental.cad_backend import (
    BackendOperationError,
    BackendUnavailable,
    Measurement,
    Selector,
    UnsupportedSelector,
)
from cad_experimental.cadquery_backend import CadQueryBackend
from cad_experimental.freecad_backend import FreeCadBackend, freecad_available

SOURCE = pathlib.Path(cb.__file__).resolve().parent

#: Volumes are compared relatively. Two independent kernels will not agree to
#: the bit, and demanding that would be testing the wrong thing.
VOLUME_RTOL = 1e-9

#: Bounding-box extents, in millimetres.
LENGTH_ATOL = 1e-9

FREECAD_READY = freecad_available()
requires_freecad = unittest.skipUnless(
    FREECAD_READY,
    "FreeCAD is not importable here; set CAD_FREECAD_HOME and "
    "LD_LIBRARY_PATH (see this module's docstring). NOT a fallback -- the "
    "FreeCAD half of the comparison is simply not measured.",
)

# --- the six golden parts, as closed forms ---------------------------------

PLATE = 100.0 * 60.0 * 10.0
ROD = math.pi * 10.0 ** 2 * 50.0
HOLE_D20_THROUGH_10 = math.pi * 10.0 ** 2 * 10.0
CORNER_R2 = 2.0 ** 2 * (1.0 - math.pi / 4.0)
BEVEL_D2 = 2.0 ** 2 / 2.0


def build_golden(backend, name):
    """Build one golden part with ``backend``. Identical inputs either side."""
    if name == "box":
        return backend.create_box((100.0, 60.0, 10.0))
    if name == "cylinder":
        return backend.create_cylinder(20.0, 50.0)
    if name == "through_hole":
        plate = backend.create_box((100.0, 60.0, 10.0))
        return backend.through_hole(plate, 20.0, (50.0, 30.0, 0.0), "+Z")
    if name == "subtract":
        plate = backend.create_box((100.0, 60.0, 10.0))
        tool = backend.create_cylinder(20.0, 10.0, (50.0, 30.0, 0.0), "+Z")
        return backend.subtract(plate, [tool])
    if name == "fillet":
        plate = backend.create_box((100.0, 60.0, 10.0))
        return backend.fillet(plate, 2.0, Selector("axis_parallel", "Z"))
    if name == "chamfer":
        plate = backend.create_box((100.0, 60.0, 10.0))
        return backend.chamfer(plate, 2.0, Selector("axis_parallel", "Z"))
    raise AssertionError(f"unknown golden part {name!r}")


#: Expected volume per golden part, computed from the request's own numbers.
EXPECTED = {
    "box": PLATE,
    "cylinder": ROD,
    "through_hole": PLATE - HOLE_D20_THROUGH_10,
    "subtract": PLATE - HOLE_D20_THROUGH_10,
    "fillet": PLATE - 4.0 * 10.0 * CORNER_R2,
    "chamfer": PLATE - 4.0 * 10.0 * BEVEL_D2,
}
EXPECTED_SIZE = {
    "box": (100.0, 60.0, 10.0),
    "cylinder": (20.0, 20.0, 50.0),
    "through_hole": (100.0, 60.0, 10.0),
    "subtract": (100.0, 60.0, 10.0),
    "fillet": (100.0, 60.0, 10.0),
    "chamfer": (100.0, 60.0, 10.0),
}
GOLDEN = tuple(EXPECTED)


# --- 1. the interface ------------------------------------------------------


class InterfaceTests(unittest.TestCase):
    def test_both_backends_implement_the_interface(self):
        for backend in (CadQueryBackend(), FreeCadBackend()):
            with self.subTest(backend=backend.name):
                self.assertIsInstance(backend, cb.CadBackend)
                for method in (
                    "create_box", "create_cylinder", "through_hole",
                    "subtract", "fillet", "chamfer", "measure",
                    "select_edges", "export_step", "read_step",
                    "render_model", "available", "version",
                ):
                    self.assertTrue(
                        callable(getattr(backend, method)), method
                    )

    def test_the_supported_set_is_exactly_the_six_v1_features(self):
        self.assertEqual(
            set(cb.SUPPORTED_OPERATIONS),
            {"box", "cylinder", "through_hole", "subtract", "fillet",
             "chamfer"},
        )

    def test_the_interface_offers_nothing_for_unbuilt_features(self):
        """No method for a feature no engine here executes."""
        for absent in ("sketch", "extrude", "revolve", "sweep", "loft",
                       "pattern", "mirror", "assembly"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, cb.SUPPORTED_OPERATIONS)
                self.assertFalse(hasattr(cb.CadBackend, absent))

    def test_the_two_backend_names(self):
        self.assertEqual(cb.BACKEND_NAMES, ("cadquery", "freecad"))

    def test_a_measurement_is_plain_numbers(self):
        backend = CadQueryBackend()
        measurement = backend.measure(backend.create_box((1.0, 2.0, 3.0)))
        self.assertIsInstance(measurement, Measurement)
        for value in (*measurement.minimum, *measurement.maximum,
                      measurement.volume):
            self.assertIsInstance(value, float)


class SelectorTests(unittest.TestCase):
    def test_all_takes_no_axis(self):
        Selector("all").validate()
        with self.assertRaises(UnsupportedSelector):
            Selector("all", "Z").validate()

    def test_axis_parallel_needs_an_unsigned_axis(self):
        Selector("axis_parallel", "Z").validate()
        for bad in (None, "+Z", "z", "W", ""):
            with self.subTest(axis=bad), self.assertRaises(UnsupportedSelector):
                Selector("axis_parallel", bad).validate()

    def test_an_unknown_mode_is_refused_not_guessed(self):
        for bad in ("nearest", "longest", "vertical", ""):
            with self.subTest(mode=bad), self.assertRaises(UnsupportedSelector):
                Selector(bad).validate()

    def test_no_backend_offers_a_nearest_edge_heuristic(self):
        """The contract has two selectors. A third would silently change
        which edges a part was filleted on."""
        for module in ("cad_backend.py", "cadquery_backend.py",
                       "freecad_backend.py"):
            text = (SOURCE / module).read_text(encoding="utf-8").lower()
            for word in ("nearest", "closest", "best_match", "fuzzy"):
                with self.subTest(module=module, word=word):
                    # allowed only where the code explains it does NOT do this
                    if word in text:
                        self.assertIn("no nearest-edge", text)


# --- 2. backend selection --------------------------------------------------


class SelectionTests(unittest.TestCase):
    def test_the_default_is_cadquery(self):
        self.assertEqual(cb.DEFAULT_BACKEND, "cadquery")

    def test_no_environment_means_cadquery(self):
        import os

        previous = os.environ.pop(cb.BACKEND_VARIABLE, None)
        try:
            self.assertEqual(cb.resolve_backend().name, "cadquery")
        finally:
            if previous is not None:
                os.environ[cb.BACKEND_VARIABLE] = previous

    def test_the_environment_selects_a_backend(self):
        import os

        previous = os.environ.get(cb.BACKEND_VARIABLE)
        try:
            os.environ[cb.BACKEND_VARIABLE] = "cadquery"
            self.assertEqual(cb.resolve_backend().name, "cadquery")
        finally:
            if previous is None:
                os.environ.pop(cb.BACKEND_VARIABLE, None)
            else:
                os.environ[cb.BACKEND_VARIABLE] = previous

    def test_an_unknown_backend_is_an_error_not_a_default(self):
        with self.assertRaises(BackendUnavailable) as raised:
            cb.resolve_backend("solidworks")
        self.assertIn("unknown CAD backend", str(raised.exception))

    def test_an_unavailable_backend_never_falls_back(self):
        """The single most important behaviour in this stage."""
        import os

        previous = os.environ.get(cb.FREECAD_HOME_VARIABLE)
        import cad_experimental.freecad_backend as fb

        cached = fb._MODULES
        try:
            fb._MODULES = None
            os.environ[cb.FREECAD_HOME_VARIABLE] = "/nonexistent-freecad"
            if freecad_available():
                self.skipTest(
                    "FreeCAD is already imported in this process, so its "
                    "absence cannot be simulated here"
                )
            with self.assertRaises(BackendUnavailable) as raised:
                cb.resolve_backend("freecad")
            self.assertIn("substituted", str(raised.exception).lower())
        finally:
            fb._MODULES = cached
            if previous is None:
                os.environ.pop(cb.FREECAD_HOME_VARIABLE, None)
            else:
                os.environ[cb.FREECAD_HOME_VARIABLE] = previous

    def test_resolve_never_mentions_a_fallback(self):
        source = (SOURCE / "cad_backend.py").read_text(encoding="utf-8")
        self.assertIn("Never falls back", source)
        for word in ("fallback to", "fall back to cadquery"):
            self.assertNotIn(word, source.lower().replace("must never quietly", ""))

    def test_the_report_lists_both_backends(self):
        report = cb.backend_report()
        self.assertEqual(report["default"], "cadquery")
        self.assertEqual(set(report["backends"]), {"cadquery", "freecad"})
        self.assertTrue(report["backends"]["cadquery"]["available"])


# --- 3. per-backend geometry ----------------------------------------------


class BackendGeometryMixin:
    """The same assertions, run against whichever backend the subclass names."""

    backend_factory = None

    def setUp(self):
        self.backend = self.backend_factory()

    def test_every_golden_part_builds(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                shape = build_golden(self.backend, name)
                measurement = self.backend.measure(shape)
                self.assertTrue(measurement.is_valid)
                self.assertEqual(measurement.solid_count, 1)
                self.assertGreater(measurement.volume, 0.0)

    def test_every_golden_volume_matches_its_closed_form(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                volume = self.backend.measure(
                    build_golden(self.backend, name)
                ).volume
                self.assertTrue(
                    math.isclose(volume, EXPECTED[name], rel_tol=VOLUME_RTOL),
                    f"{name}: {volume} vs {EXPECTED[name]}",
                )

    def test_every_golden_bounding_box_matches(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                size = self.backend.measure(
                    build_golden(self.backend, name)
                ).size
                for got, want in zip(size, EXPECTED_SIZE[name]):
                    self.assertAlmostEqual(got, want, delta=LENGTH_ATOL)

    def test_a_box_is_positioned_by_its_minimum_corner(self):
        shape = self.backend.create_box((10.0, 20.0, 30.0), (5.0, 6.0, 7.0))
        measurement = self.backend.measure(shape)
        for got, want in zip(measurement.minimum, (5.0, 6.0, 7.0)):
            self.assertAlmostEqual(got, want, delta=LENGTH_ATOL)

    def test_a_cylinder_is_positioned_by_its_base_centre(self):
        shape = self.backend.create_cylinder(20.0, 50.0, (5.0, 6.0, 7.0), "+Z")
        measurement = self.backend.measure(shape)
        for got, want in zip(measurement.minimum, (-5.0, -4.0, 7.0)):
            self.assertAlmostEqual(got, want, delta=1e-6)

    def test_a_cylinder_axis_reorients_the_solid(self):
        shape = self.backend.create_cylinder(16.0, 30.0, (0.0, 0.0, 0.0), "+X")
        size = self.backend.measure(shape).size
        for got, want in zip(size, (30.0, 16.0, 16.0)):
            self.assertAlmostEqual(got, want, delta=1e-6)

    def test_a_hole_that_misses_the_material_is_an_error(self):
        """Rule E1: never a quiet no-op."""
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        with self.assertRaises(BackendOperationError) as raised:
            self.backend.through_hole(plate, 8.0, (500.0, 500.0, 0.0), "+Z")
        self.assertIn("E1", str(raised.exception))

    def test_a_subtract_that_removes_nothing_is_an_error(self):
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        away = self.backend.create_cylinder(
            10.0, 10.0, (500.0, 500.0, 0.0), "+Z"
        )
        with self.assertRaises(BackendOperationError):
            self.backend.subtract(plate, [away])

    def test_a_selector_matching_nothing_is_an_error(self):
        """Rule E4: never a no-op, and never a different edge."""
        rod = self.backend.create_cylinder(20.0, 50.0)
        with self.assertRaises(BackendOperationError) as raised:
            self.backend.fillet(rod, 1.0, Selector("axis_parallel", "X"))
        self.assertIn("E4", str(raised.exception))

    def test_axis_parallel_selects_exactly_the_four_vertical_edges(self):
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        edges = self.backend.select_edges(
            plate, Selector("axis_parallel", "Z")
        )
        self.assertEqual(len(edges), 4)

    def test_all_selects_every_edge_of_a_box(self):
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        self.assertEqual(len(self.backend.select_edges(plate, Selector("all"))), 12)

    def test_an_unsigned_selector_axis_is_required(self):
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        with self.assertRaises(UnsupportedSelector):
            self.backend.select_edges(plate, Selector("axis_parallel", "+Z"))

    def test_a_non_positive_dimension_is_refused(self):
        for call in (
            lambda: self.backend.create_box((0.0, 60.0, 10.0)),
            lambda: self.backend.create_box((-1.0, 60.0, 10.0)),
            lambda: self.backend.create_cylinder(0.0, 50.0),
            lambda: self.backend.create_cylinder(20.0, -1.0),
        ):
            with self.subTest(call=call), self.assertRaises(BackendOperationError):
                call()

    def test_a_zero_radius_fillet_is_refused(self):
        plate = self.backend.create_box((100.0, 60.0, 10.0))
        with self.assertRaises(BackendOperationError):
            self.backend.fillet(plate, 0.0, Selector("axis_parallel", "Z"))

    # --- RenderModel ---

    def test_every_golden_part_reaches_the_existing_render_model(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                model = self.backend.render_model(
                    build_golden(self.backend, name),
                    part_name="golden", feature_id=name,
                )
                self.assertIsInstance(model, RenderModel)
                self.assertGreater(model.triangle_count(), 0)
                self.assertGreater(model.vertex_count(), 0)
                self.assertEqual(model.units, "mm")
                self.assertEqual(model.format_version, "1.0.0")

    def test_the_render_model_is_json_serialisable(self):
        import json

        model = self.backend.render_model(
            build_golden(self.backend, "through_hole"),
            part_name="p", feature_id="plate",
        )
        self.assertIsInstance(json.dumps(model.to_dict()), str)

    def test_every_render_index_is_inside_the_vertex_array(self):
        model = self.backend.render_model(
            build_golden(self.backend, "fillet"),
            part_name="p", feature_id="f",
        )
        for triangle in model.triangles:
            for index in triangle:
                self.assertTrue(0 <= index < model.vertex_count())

    def test_there_is_one_normal_per_vertex(self):
        model = self.backend.render_model(
            build_golden(self.backend, "box"), part_name="p", feature_id="b",
        )
        self.assertEqual(model.normal_binding, "per_vertex")
        self.assertEqual(len(model.normals), model.vertex_count())

    # --- STEP ---

    def test_step_export_round_trips_with_the_same_geometry(self):
        """Not merely that a file exists: it is re-read and re-measured."""
        for name in ("box", "through_hole", "fillet"):
            with self.subTest(part=name):
                shape = build_golden(self.backend, name)
                before = self.backend.measure(shape)
                path = pathlib.Path(tempfile.mkdtemp()) / f"{name}.step"
                written = self.backend.export_step(shape, path)
                self.assertTrue(written.is_file())
                self.assertGreater(written.stat().st_size, 0)

                after = self.backend.measure(self.backend.read_step(written))
                self.assertTrue(
                    math.isclose(after.volume, before.volume,
                                 rel_tol=VOLUME_RTOL),
                    f"{name}: {after.volume} vs {before.volume}",
                )
                self.assertEqual(after.solid_count, before.solid_count)

    def test_a_non_step_extension_is_refused(self):
        shape = build_golden(self.backend, "box")
        path = pathlib.Path(tempfile.mkdtemp()) / "part.txt"
        with self.assertRaises(Exception):
            self.backend.export_step(shape, path)


class CadQueryGeometryTests(BackendGeometryMixin, unittest.TestCase):
    backend_factory = CadQueryBackend


@requires_freecad
class FreeCadGeometryTests(BackendGeometryMixin, unittest.TestCase):
    backend_factory = FreeCadBackend


# --- 4. the cross-backend golden comparison -------------------------------


@requires_freecad
class CrossBackendTests(unittest.TestCase):
    """The same inputs, two engines, compared geometrically.

    Explicitly NOT compared: topology identifiers, face and edge numbering,
    internal OpenCascade structure. Two kernels may reach the same solid by
    different routes and still both be right.
    """

    @classmethod
    def setUpClass(cls):
        cls.cq = CadQueryBackend()
        cls.fc = FreeCadBackend()
        cls.results = {}
        for name in GOLDEN:
            cls.results[name] = {
                "cadquery": cls.cq.measure(build_golden(cls.cq, name)),
                "freecad": cls.fc.measure(build_golden(cls.fc, name)),
            }

    def test_volumes_agree_between_the_backends(self):
        for name in GOLDEN:
            left = self.results[name]["cadquery"].volume
            right = self.results[name]["freecad"].volume
            with self.subTest(part=name):
                self.assertTrue(
                    math.isclose(left, right, rel_tol=VOLUME_RTOL),
                    f"{name}: cadquery {left} vs freecad {right}",
                )

    def test_both_agree_with_the_closed_form(self):
        for name in GOLDEN:
            for engine in ("cadquery", "freecad"):
                with self.subTest(part=name, engine=engine):
                    self.assertTrue(math.isclose(
                        self.results[name][engine].volume,
                        EXPECTED[name], rel_tol=VOLUME_RTOL,
                    ))

    def test_solid_counts_agree(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                self.assertEqual(
                    self.results[name]["cadquery"].solid_count,
                    self.results[name]["freecad"].solid_count,
                )

    def test_bounding_boxes_agree(self):
        for name in GOLDEN:
            left = self.results[name]["cadquery"]
            right = self.results[name]["freecad"]
            with self.subTest(part=name):
                for a, b in zip(left.minimum, right.minimum):
                    self.assertAlmostEqual(a, b, delta=1e-6)
                for a, b in zip(left.maximum, right.maximum):
                    self.assertAlmostEqual(a, b, delta=1e-6)

    def test_both_report_a_valid_single_solid(self):
        for name in GOLDEN:
            for engine in ("cadquery", "freecad"):
                with self.subTest(part=name, engine=engine):
                    self.assertTrue(self.results[name][engine].is_valid)
                    self.assertEqual(
                        self.results[name][engine].solid_count, 1
                    )

    def test_face_counts_agree_on_these_parts(self):
        """Recorded because they happen to agree here, and a divergence
        would be worth knowing about -- not because the contract requires
        identical topology."""
        for name in GOLDEN:
            with self.subTest(part=name):
                self.assertEqual(
                    self.results[name]["cadquery"].face_count,
                    self.results[name]["freecad"].face_count,
                )

    def test_the_same_selector_picks_the_same_number_of_edges(self):
        for selector, expected in (
            (Selector("all"), 12), (Selector("axis_parallel", "Z"), 4),
            (Selector("axis_parallel", "X"), 4),
            (Selector("axis_parallel", "Y"), 4),
        ):
            left = len(self.cq.select_edges(
                self.cq.create_box((100.0, 60.0, 10.0)), selector))
            right = len(self.fc.select_edges(
                self.fc.create_box((100.0, 60.0, 10.0)), selector))
            with self.subTest(selector=selector):
                self.assertEqual(left, right)
                self.assertEqual(left, expected)

    def test_both_backends_produce_the_same_render_model_type(self):
        for name in GOLDEN:
            left = self.cq.render_model(
                build_golden(self.cq, name), part_name="p", feature_id=name)
            right = self.fc.render_model(
                build_golden(self.fc, name), part_name="p", feature_id=name)
            with self.subTest(part=name):
                self.assertIs(type(left), type(right))
                for field in ("format_version", "units", "coordinate_system",
                              "winding", "normal_binding", "part_name",
                              "feature_id"):
                    self.assertEqual(
                        getattr(left, field), getattr(right, field), field
                    )

    def test_render_bounds_agree_within_the_tessellation_tolerance(self):
        """A mesh lies on or inside the surface, so its bounds may differ by
        up to the deflection -- but not more."""
        for name in GOLDEN:
            left = self.cq.render_model(
                build_golden(self.cq, name), part_name="p", feature_id=name)
            right = self.fc.render_model(
                build_golden(self.fc, name), part_name="p", feature_id=name)
            with self.subTest(part=name):
                for a, b in zip(left.bounds.size, right.bounds.size):
                    self.assertAlmostEqual(a, b, delta=0.05)

    def test_both_backends_export_step_that_reimports_to_the_same_volume(self):
        for name in GOLDEN:
            with self.subTest(part=name):
                volumes = {}
                for engine, backend in (("cadquery", self.cq),
                                        ("freecad", self.fc)):
                    shape = build_golden(backend, name)
                    path = pathlib.Path(tempfile.mkdtemp()) / f"{name}.step"
                    backend.export_step(shape, path)
                    volumes[engine] = backend.measure(
                        backend.read_step(path)
                    ).volume
                self.assertTrue(math.isclose(
                    volumes["cadquery"], volumes["freecad"],
                    rel_tol=VOLUME_RTOL,
                ), f"{name}: {volumes}")

    def test_a_freecad_step_file_is_readable_by_cadquery(self):
        """Real interoperability, not two isolated stacks."""
        shape = build_golden(self.fc, "through_hole")
        path = pathlib.Path(tempfile.mkdtemp()) / "fc.step"
        self.fc.export_step(shape, path)
        crossed = self.cq.measure(self.cq.read_step(path))
        self.assertTrue(math.isclose(
            crossed.volume, EXPECTED["through_hole"], rel_tol=1e-6))
        self.assertEqual(crossed.solid_count, 1)

    def test_a_cadquery_step_file_is_readable_by_freecad(self):
        shape = build_golden(self.cq, "through_hole")
        path = pathlib.Path(tempfile.mkdtemp()) / "cq.step"
        self.cq.export_step(shape, path)
        crossed = self.fc.measure(self.fc.read_step(path))
        self.assertTrue(math.isclose(
            crossed.volume, EXPECTED["through_hole"], rel_tol=1e-6))
        self.assertEqual(crossed.solid_count, 1)


@requires_freecad
class TimingTests(unittest.TestCase):
    """Coarse timings, recorded because they are free to collect here.

    Not a benchmark: one run, one machine, no warm-up and no repetition. Read
    them as orders of magnitude and nothing finer.
    """

    def test_record_basic_timings(self):
        rows = []
        for engine, backend in (("cadquery", CadQueryBackend()),
                                ("freecad", FreeCadBackend())):
            for name in GOLDEN:
                start = time.perf_counter()
                shape = build_golden(backend, name)
                built = time.perf_counter() - start

                start = time.perf_counter()
                backend.render_model(
                    shape, part_name="p", feature_id=name)
                rendered = time.perf_counter() - start

                start = time.perf_counter()
                backend.export_step(
                    shape, pathlib.Path(tempfile.mkdtemp()) / "t.step")
                exported = time.perf_counter() - start
                rows.append((engine, name, built, rendered, exported))
                self.assertGreater(built, 0.0)
        print("\n  timing (s): backend    part           build   render   step")
        for engine, name, built, rendered, exported in rows:
            print(f"    {engine:<10}{name:<14}{built:7.3f}{rendered:9.3f}"
                  f"{exported:8.3f}")


# --- 5. availability, reported honestly ------------------------------------


class AvailabilityTests(unittest.TestCase):
    def test_availability_is_a_boolean_and_never_raises(self):
        self.assertIsInstance(freecad_available(), bool)
        self.assertIsInstance(FreeCadBackend().available(), bool)

    def test_the_version_string_says_unavailable_when_it_is(self):
        backend = FreeCadBackend()
        if backend.available():
            self.assertRegex(backend.version(), r"^\d+\.\d+")
        else:
            self.assertEqual(backend.version(), "unavailable")

    def test_cadquery_is_always_available_here(self):
        self.assertTrue(CadQueryBackend().available())

    @unittest.skipIf(FREECAD_READY, "FreeCAD is available in this run")
    def test_when_unavailable_the_backend_refuses_rather_than_pretends(self):
        with self.assertRaises(BackendUnavailable):
            cb.resolve_backend("freecad")


# --- 6. security -----------------------------------------------------------


class SecurityTests(unittest.TestCase):
    """The plan stays data. FreeCAD is a trusted internal backend."""

    def modules(self):
        for name in ("cad_backend.py", "cadquery_backend.py",
                     "freecad_backend.py"):
            path = SOURCE / name
            yield path, ast.parse(path.read_text(encoding="utf-8"))

    def test_nothing_executes_generated_content(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    self.assertNotIn(
                        getattr(node.func, "id", None),
                        ("eval", "exec", "compile", "__import__"),
                        path.name,
                    )

    def test_no_backend_spawns_a_process(self):
        """No FreeCADCmd, no shelling out. The kernel is imported in-process."""
        for path, tree in self.modules():
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(module=path.name):
                for forbidden in ("subprocess", "socket", "requests",
                                  "urllib", "httpx", "pty", "shlex"):
                    self.assertNotIn(forbidden, imported)

    def test_no_computed_attribute_traversal(self):
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) in ("getattr", "setattr")
                    and len(node.args) >= 2
                    and not isinstance(node.args[1], ast.Constant)
                ):
                    self.fail(f"{path.name} traverses a computed attribute")

    def test_no_gui_module_is_imported(self):
        text = (SOURCE / "freecad_backend.py").read_text(encoding="utf-8")
        self.assertNotIn("import FreeCADGui", text)
        self.assertNotIn("FreeCADGui.", text)

    def test_the_freecad_home_variable_is_only_a_path(self):
        """It names a directory to import from -- never a command to run."""
        tree = ast.parse(
            (SOURCE / "freecad_backend.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None)
                self.assertNotIn(name, ("system", "popen", "spawn"))

    def test_the_backends_open_no_file_outside_step_io(self):
        """STEP export and import touch the filesystem; nothing else does."""
        for path, tree in self.modules():
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "id", None) == "open"):
                    self.fail(f"{path.name} calls open() directly")


if __name__ == "__main__":
    unittest.main()
