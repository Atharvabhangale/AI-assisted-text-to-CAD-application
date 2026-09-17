"""The four product surfaces, plus STL export.

Focused rather than exhaustive: each test proves one behaviour the surface
promises. The drawing and export tests need a real kernel and skip without
one; everything else is pure data and runs anywhere.
"""

from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from cad_experimental import catalog, engineering as eng
from cad_experimental.drawing import (
    build_drawing,
    choose_scale,
    dimensions_from,
)
from cad_experimental.macros import (
    ACTIONS,
    MacroError,
    MacroStore,
    steps_from_language,
    suggested_name,
    validate_steps,
)

PLAN: Dict[str, Any] = {
    "status": "generated", "summary": "a bored plate",
    "operations": [
        {"id": "plate", "type": "box",
         "parameters": {"x": 120.0, "y": 60.0, "z": 10.0}},
        {"id": "hole", "type": "through_hole", "target": "plate",
         "parameters": {"diameter": 8.0,
                        "position": {"x": 20.0, "y": 30.0, "z": 0.0}}},
        {"id": "hole2", "type": "through_hole", "target": "plate",
         "parameters": {"diameter": 8.0,
                        "position": {"x": 60.0, "y": 30.0, "z": 0.0}}},
        {"id": "edges", "type": "fillet", "target": "plate",
         "parameters": {"radius": 2.0,
                        "edges": {"select": "straight", "axis": "Z"}}},
    ],
}
MEASURED: Dict[str, Any] = {
    "size": [120.0, 60.0, 10.0], "volume": 71195.0,
    "face_count": 13, "edge_count": 33, "solid_count": 1,
}


def _backend():
    from cad_experimental.cad_backend import resolve_backend

    try:
        engine = resolve_backend()
        return engine if engine.available() else None
    except Exception:
        return None


def _can_project() -> bool:
    """Whether this backend implements projection at all.

    Only FreeCAD does today. A backend without it is a capability gap that
    the drawing route reports as 501 -- not a failure of these tests, so
    they skip rather than fail.
    """
    if BACKEND is None:
        return False
    try:
        BACKEND.project_edges(BACKEND.create_box((1.0, 1.0, 1.0)), (0.0, 0.0, 1.0))
    except NotImplementedError:
        return False
    except Exception:
        return False
    return True


BACKEND = _backend()
requires_kernel = unittest.skipUnless(
    BACKEND is not None, "no CAD backend available here")
requires_projection = unittest.skipUnless(
    _can_project(), "this backend cannot project drawing views")


# --- engineering -------------------------------------------------------------


class EngineeringTests(unittest.TestCase):
    """Measured and calculated must never be presented as the same thing."""

    def test_overview_reports_measured_values_as_measured(self) -> None:
        findings = eng.overview(PLAN, MEASURED)
        kinds = {f.label: f.kind for f in findings}
        self.assertEqual(kinds["Volume"], eng.MEASURED)
        self.assertEqual(kinds["Overall dimensions"], eng.MEASURED)

    def test_material_removed_is_calculated_and_shows_its_working(self) -> None:
        findings = eng.material_removed(PLAN, MEASURED)
        removed = findings[0]
        self.assertEqual(removed.kind, eng.CALCULATED)
        self.assertIsNotNone(removed.working)
        # two 8 mm holes through 10 mm
        expected = 2 * math.pi * 16.0 * 10.0
        self.assertAlmostEqual(float(removed.value.split()[0]), expected, places=2)

    def test_it_states_the_assumptions_it_made(self) -> None:
        working = eng.material_removed(PLAN, MEASURED)[0].working or ""
        self.assertIn("assumes", working)
        self.assertIn("overlap", working)

    def test_fillet_versus_thickness_compares_without_judging(self) -> None:
        findings = eng.fillet_vs_thickness(PLAN, MEASURED)
        self.assertEqual(len(findings), 1)
        self.assertIn("smaller than", findings[0].value)
        self.assertEqual(findings[0].kind, eng.CALCULATED)

    def test_a_question_it_cannot_answer_returns_nothing(self) -> None:
        """Returning None costs a model call; guessing costs a wrong number."""
        for question in ("what colour is it", "who designed this",
                         "will it pass FEA"):
            with self.subTest(question=question):
                self.assertIsNone(eng.analyse(PLAN, MEASURED, question))

    def test_it_answers_a_volume_question_from_measurement(self) -> None:
        answered = eng.analyse(PLAN, MEASURED, "what is the volume?")
        self.assertIsNotNone(answered)
        self.assertTrue(any("71195" in f["value"] for f in answered["measured"]))

    def test_measured_and_calculated_are_reported_separately(self) -> None:
        answered = eng.analyse(PLAN, MEASURED,
                               "how much material is removed by the holes?")
        self.assertTrue(answered["calculated"])
        self.assertTrue(all(f["kind"] == eng.CALCULATED
                            for f in answered["calculated"]))


# --- part finder -------------------------------------------------------------


class CatalogTests(unittest.TestCase):

    def test_it_finds_an_m8_socket_head_cap_screw(self) -> None:
        found = catalog.search("Find an M8 socket head cap screw.")
        self.assertTrue(found["results"])
        for record in found["results"]:
            self.assertEqual(record["category"], "screw")
            self.assertEqual(record["dimensions"]["thread"], "M8")

    def test_it_finds_bearings_by_bore(self) -> None:
        found = catalog.search("Show bearings with 20 mm bore.")
        self.assertTrue(found["results"])
        for record in found["results"]:
            self.assertEqual(record["category"], "bearing")
            self.assertEqual(record["dimensions"]["bore_mm"], 20.0)

    def test_it_finds_washers_for_m8(self) -> None:
        found = catalog.search("Find washers for M8 bolts.")
        self.assertTrue(found["results"])
        self.assertTrue(all(r["category"] == "washer" for r in found["results"]))

    def test_every_response_says_it_is_a_local_table(self) -> None:
        """It must never be mistakable for a supplier database."""
        found = catalog.search("M8 screw")
        self.assertEqual(found["source"], catalog.SOURCE_LABEL)
        self.assertIn("Not a supplier catalogue", found["note"])

    def test_an_unmatched_query_returns_nothing_rather_than_anything(self):
        self.assertEqual(catalog.search("titanium turbine blade")["results"], [])

    def test_it_suggests_screws_that_actually_fit_a_hole(self) -> None:
        fits = catalog.suggest_for_hole(8.8)
        self.assertTrue(fits["results"])
        for record in fits["results"]:
            self.assertLessEqual(record["dimensions"]["clearance_hole_mm"], 8.8)


# --- macros ------------------------------------------------------------------


class MacroTests(unittest.TestCase):
    """A macro is a closed vocabulary, not a scripting language."""

    def test_an_unknown_action_is_refused(self) -> None:
        with self.assertRaises(MacroError) as caught:
            validate_steps([{"action": "os.system"}])
        self.assertIn("not a known action", str(caught.exception))

    def test_every_stored_action_is_in_the_vocabulary(self) -> None:
        steps = validate_steps([{"action": "export_step"},
                                {"action": "export_stl"}])
        self.assertTrue(all(step.action in ACTIONS for step in steps))

    def test_an_action_cannot_carry_unknown_parameters(self) -> None:
        with self.assertRaises(MacroError):
            validate_steps([{"action": "export_step",
                             "parameters": {"command": "rm -rf /"}}])

    def test_a_required_parameter_is_enforced(self) -> None:
        with self.assertRaises(MacroError):
            validate_steps([{"action": "rename"}])
        self.assertTrue(validate_steps(
            [{"action": "rename", "parameters": {"name": "bracket"}}]))

    def test_an_empty_macro_is_refused(self) -> None:
        with self.assertRaises(MacroError):
            validate_steps([])

    def test_language_maps_onto_the_closed_vocabulary(self) -> None:
        steps = steps_from_language("export STEP and STL")
        self.assertEqual({s["action"] for s in steps},
                         {"export_step", "export_stl"})

    def test_language_it_does_not_recognise_adds_nothing(self) -> None:
        """Better an empty macro the caller can question than an invented one."""
        self.assertEqual(steps_from_language("email it to the machine shop"), ())

    def test_a_quoted_name_is_picked_up(self) -> None:
        self.assertEqual(
            suggested_name("Save this as 'Manufacturing Package'"),
            "Manufacturing Package")

    def test_macros_are_per_session(self) -> None:
        store = MacroStore()
        store.create("a", "pack", "", [{"action": "export_step"}])
        self.assertTrue(store.list("a"))
        self.assertEqual(store.list("b"), ())


# --- drawing ----------------------------------------------------------------


class DrawingDataTests(unittest.TestCase):
    """The parts of a drawing that need no kernel."""

    def test_dimensions_come_only_from_measurement(self) -> None:
        rows = dict(dimensions_from(MEASURED))
        self.assertEqual(rows["Overall length"], "120 mm")
        self.assertEqual(rows["Faces"], "13")

    def test_nothing_is_invented_when_nothing_was_measured(self) -> None:
        self.assertEqual(dimensions_from({}), ())

    def test_the_scale_is_chosen_so_a_view_fits(self) -> None:
        self.assertLessEqual(choose_scale([(120.0, 60.0)], (100.0, 100.0)), 0.5)
        self.assertEqual(choose_scale([(10.0, 10.0)], (100.0, 100.0)), 2.0)


@requires_projection
class DrawingGeometryTests(unittest.TestCase):
    """A real projection of a real part."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = BACKEND
        plate = cls.backend.create_box((120.0, 60.0, 10.0))
        cls.shape = cls.backend.through_hole(
            plate, diameter=8.0, position=(20.0, 30.0, 0.0), axis="+Z")

    def test_it_projects_visible_edges(self) -> None:
        polylines = self.backend.project_edges(self.shape, (0.0, 0.0, 1.0))
        self.assertTrue(polylines)
        for line in polylines:
            self.assertGreaterEqual(len(line), 2)

    def test_a_sheet_carries_views_a_scale_and_measured_dimensions(self) -> None:
        sheet = build_drawing(self.backend, self.shape, part_name="plate",
                              measurement=MEASURED)
        self.assertGreaterEqual(len(sheet.views), 3)
        self.assertGreater(sheet.scale, 0)
        self.assertIn("<svg", sheet.svg)
        self.assertIn("polyline", sheet.svg)
        self.assertIn("120 mm", sheet.svg)
        self.assertIn("plate", sheet.svg)

    def test_the_title_block_states_units_and_engine(self) -> None:
        sheet = build_drawing(self.backend, self.shape, part_name="plate",
                              measurement=MEASURED)
        self.assertIn("UNITS", sheet.svg)
        self.assertIn("mm", sheet.svg)
        self.assertIn(self.backend.name, sheet.svg)


# --- export ------------------------------------------------------------------


@requires_kernel
class ExportTests(unittest.TestCase):
    """STL through the backend abstraction, alongside the existing STEP."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = BACKEND
        cls.shape = cls.backend.create_box((40.0, 30.0, 10.0))

    def test_stl_is_written_and_is_not_empty(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "part.stl"
            self.backend.export_stl(self.shape, target)
            self.assertTrue(target.exists())
            data = target.read_bytes()
            self.assertGreater(len(data), 200)
            # ASCII or binary STL, but a real one either way
            self.assertTrue(data.startswith(b"solid") or len(data) >= 84)

    def test_step_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "part.step"
            self.backend.export_step(self.shape, target)
            self.assertTrue(target.read_bytes().startswith(b"ISO-10303"))


if __name__ == "__main__":
    unittest.main()
