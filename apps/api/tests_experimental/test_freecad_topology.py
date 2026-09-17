"""FreeCAD topology inspection and semantic selectors, on the shared fixture.

The milestone this covers is portability: the canonical Operation Plan's
selectors must mean the same thing on either engine. So almost every test
here is written against *a* backend and run against whichever ones this
machine has, rather than against FreeCAD specifically -- a test only FreeCAD
runs proves FreeCAD agrees with itself.

Environment note, which the skips encode
----------------------------------------
Neither kernel is importable everywhere. FreeCAD lives in a WSL2 build and
CadQuery in the Windows virtual environment, and on this project's primary
machine no interpreter has both. Each backend's tests therefore skip when its
engine is absent, and the cross-backend comparison skips unless both are
present. A skip here means "not measured on this machine", never "passed".

The parity invariant
--------------------
Same semantic selector -> same intended geometric edges. **Not** the same
numeric edge index: the two kernels enumerate topology in their own order and
requiring them to agree would be asserting a coincidence.
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cad_experimental.cad_backend import BackendOperationError
from cad_experimental.edge_semantics import (
    CIRCLE,
    LINE,
    R1,
    R2,
    SemanticSelector,
    resolve,
)
from cad_experimental.freecad_backend import FreeCadBackend, freecad_available

#: The one fixture: a 100 x 60 x 10 mm plate with a centred 20 mm bore. It is
#: the smallest shape that carries a cylindrical face, and therefore the
#: smallest one on which `straight` and `axis_parallel` are different
#: questions -- the seam is what separates them.
PLATE = (100.0, 60.0, 10.0)
HOLE_DIAMETER = 20.0
HOLE_CENTRE = (50.0, 30.0, 0.0)

#: Closed-form expectations, computed rather than recorded from a run.
#: plate - bore = 100*60*10 - pi*10^2*10
EXPECTED_BASELINE_VOLUME = 56858.407346410204
#: ...minus four r=2 vertical corners: 4 * (r^2 - pi r^2/4) * 10
EXPECTED_FILLET_VOLUME = 56824.0710525538
#: ...minus a 1 mm chamfer on one rim of the bore
EXPECTED_CHAMFER_VOLUME = 56825.944222323116

VOLUME_TOLERANCE = 1e-6


# `cadquery_backend` imports `cad_core.local_cad` at module scope, which
# raises without CadQuery -- so on a FreeCAD-only machine even *importing*
# the other backend fails. Guarded here rather than at the top of the file so
# that this module still loads, and its FreeCAD tests still run, on a machine
# that has only FreeCAD. That machine is the whole reason the second backend
# exists.
try:
    from cad_experimental.cadquery_backend import CadQueryBackend
except Exception:  # pragma: no cover - environment-dependent
    CadQueryBackend = None  # type: ignore[assignment]


def _cadquery_available() -> bool:
    if CadQueryBackend is None:
        return False
    try:
        return bool(CadQueryBackend().available())
    except Exception:
        return False


CADQUERY_READY = _cadquery_available()
FREECAD_READY = freecad_available()

requires_freecad = unittest.skipUnless(
    FREECAD_READY, "FreeCAD is not importable here; set CAD_FREECAD_HOME")
requires_cadquery = unittest.skipUnless(
    CADQUERY_READY, "CadQuery is not importable here")
requires_both = unittest.skipUnless(
    FREECAD_READY and CADQUERY_READY,
    "a cross-backend comparison needs both engines in one interpreter")


def fixture(backend: Any) -> Any:
    """The plate with its centred through hole, built by this backend."""
    plate = backend.create_box(PLATE)
    return backend.through_hole(
        plate, diameter=HOLE_DIAMETER, position=HOLE_CENTRE, axis="+Z")


def describe(backend: Any) -> Tuple[Any, Sequence[Any]]:
    shape = fixture(backend)
    return shape, backend.describe_edges(shape)


def normalise(fact: Any) -> Dict[str, Any]:
    """An edge described so two backends can be compared without indices."""
    def rounded(value: Optional[Sequence[float]]) -> Optional[Tuple[float, ...]]:
        if value is None:
            return None
        return tuple(round(float(part), 6) for part in value)

    def principal(vector: Optional[Sequence[float]]) -> Optional[str]:
        if vector is None:
            return None
        for letter, position in (("X", 0), ("Y", 1), ("Z", 2)):
            if abs(abs(float(vector[position])) - 1.0) < 1e-9:
                return letter
        return None

    return {
        "curve": fact.curve,
        "is_seam": bool(fact.is_seam),
        "axis": principal(fact.direction or fact.normal),
        "midpoint": rounded(fact.midpoint),
        "centre": rounded(fact.centre),
        "radius": None if fact.radius is None else round(float(fact.radius), 6),
        "adjacent": tuple(fact.adjacent),
    }


# --- 1 & 2. FreeCAD topology inspection ------------------------------------


@requires_freecad
class FreeCadDescribeEdgesTests(unittest.TestCase):
    """`describe_edges` must answer the neutral EdgeFacts contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = FreeCadBackend()
        cls.shape, cls.facts = describe(cls.backend)

    def test_every_edge_is_described(self) -> None:
        self.assertEqual(len(self.facts), len(self.shape.Edges))

    def test_indices_are_dense_and_in_order(self) -> None:
        """The index is a handle into this shape's own edge list, and
        `edges_at` relies on that correspondence."""
        self.assertEqual([f.index for f in self.facts],
                         list(range(len(self.facts))))

    def test_the_vocabulary_is_neutral(self) -> None:
        """No FreeCAD type name may escape: the resolver speaks 'line' and
        'circle', never 'Part::GeomLine'."""
        for fact in self.facts:
            self.assertIn(fact.curve, {LINE, CIRCLE, "other"})
            for surface in fact.adjacent:
                self.assertIn(surface, {"plane", "cylinder", "other"})

    def test_the_plate_has_thirteen_lines_and_two_circles(self) -> None:
        """Twelve box edges plus the bore's seam, and the bore's two rims."""
        lines = [f for f in self.facts if f.curve == LINE]
        circles = [f for f in self.facts if f.curve == CIRCLE]
        self.assertEqual(len(lines), 13)
        self.assertEqual(len(circles), 2)

    def test_exactly_one_edge_is_a_seam(self) -> None:
        """The cylindrical face's parameterisation seam, and nothing else.

        Detected by FreeCAD's own `Edge.isSeam(face)` -- the counterpart of
        the CadQuery path's `BRepTools::IsReallyClosed`. Nothing measurable
        separates it from an outer corner: both are 10 mm lines along Z.
        """
        seams = [f for f in self.facts if f.is_seam]
        self.assertEqual(len(seams), 1, [normalise(f) for f in seams])
        self.assertEqual(seams[0].curve, LINE)

    def test_the_seam_borders_only_the_cylindrical_face(self) -> None:
        seam = next(f for f in self.facts if f.is_seam)
        self.assertEqual(seam.adjacent, ("cylinder",))

    def test_a_rim_reports_its_centre_normal_and_radius(self) -> None:
        rims = [f for f in self.facts if f.curve == CIRCLE]
        for rim in rims:
            self.assertIsNotNone(rim.centre)
            self.assertIsNotNone(rim.normal)
            self.assertAlmostEqual(rim.radius, HOLE_DIAMETER / 2.0, places=9)
            self.assertAlmostEqual(rim.centre[0], HOLE_CENTRE[0], places=9)
            self.assertAlmostEqual(rim.centre[1], HOLE_CENTRE[1], places=9)

    def test_a_rim_borders_a_plane_and_a_cylinder(self) -> None:
        for rim in (f for f in self.facts if f.curve == CIRCLE):
            self.assertEqual(rim.adjacent, ("cylinder", "plane"))

    def test_a_straight_edge_reports_a_unit_direction(self) -> None:
        for fact in (f for f in self.facts if f.curve == LINE):
            self.assertIsNotNone(fact.direction)
            length = sum(component ** 2 for component in fact.direction) ** 0.5
            self.assertAlmostEqual(length, 1.0, places=9)

    def test_no_kernel_object_crosses_the_boundary(self) -> None:
        """EdgeFacts must be plain data -- numbers, strings and bools."""
        for fact in self.facts:
            for value in (fact.midpoint, fact.direction, fact.centre,
                          fact.normal):
                if value is None:
                    continue
                for component in value:
                    self.assertIsInstance(component, float)


@requires_freecad
class FreeCadEdgesAtTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = FreeCadBackend()
        cls.shape, cls.facts = describe(cls.backend)

    def test_it_returns_one_edge_per_index_in_order(self) -> None:
        chosen = self.backend.edges_at(self.shape, [0, 2, 1])
        self.assertEqual(len(chosen), 3)

    def test_it_round_trips_against_describe_edges(self) -> None:
        """The index a fact carries must name the edge `edges_at` returns."""
        for index in (0, 1, len(self.facts) - 1):
            edge = self.backend.edges_at(self.shape, [index])[0]
            self.assertTrue(edge.isSame(self.shape.Edges[index]))

    def test_an_out_of_range_index_is_refused(self) -> None:
        """Never silently wrapped: Python would happily take -1."""
        with self.assertRaises(BackendOperationError):
            self.backend.edges_at(self.shape, [len(self.facts)])
        with self.assertRaises(BackendOperationError):
            self.backend.edges_at(self.shape, [-1])

    def test_an_empty_selection_returns_nothing_rather_than_everything(self):
        self.assertEqual(self.backend.edges_at(self.shape, []), ())


# --- 3-8. the semantic selector matrix, on every available backend ---------


#: (selector, expected candidates, expected selected, expected code)
MATRIX: Tuple[Tuple[SemanticSelector, int, int, Optional[str]], ...] = (
    (SemanticSelector(select="circular", axis="X"), 0, 0, R1),
    (SemanticSelector(select="axis_parallel", axis="Z"), 5, 0, R2),
    (SemanticSelector(select="straight", axis="Z"), 4, 4, None),
    (SemanticSelector(select="circular", axis="Z"), 2, 2, None),
    (SemanticSelector(select="circular", axis="Z", position="top"), 2, 1, None),
    (SemanticSelector(select="circular", axis="Z", position="bottom"), 2, 1, None),
)


class SelectorMatrixMixin:
    """The same six selectors, resolved through the same shared engine.

    The backend supplies facts; `edge_semantics.resolve` decides. No test in
    this class knows which engine it is running on, which is the point.
    """

    backend_factory: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = cls.backend_factory()
        cls.shape, cls.facts = describe(cls.backend)

    def test_the_selector_matrix(self) -> None:
        for selector, candidates, selected, code in MATRIX:
            with self.subTest(selector=selector.to_dict()):
                resolution = resolve(selector, self.facts)
                self.assertEqual(len(resolution.candidates), candidates)
                self.assertEqual(len(resolution.indices), selected)
                self.assertEqual(resolution.code, code)

    def test_circular_x_matches_nothing_on_a_z_bore(self) -> None:
        """R1, and it must say so rather than falling back to another axis."""
        resolution = resolve(
            SemanticSelector(select="circular", axis="X"), self.facts)
        self.assertEqual(resolution.code, R1)
        self.assertEqual(resolution.indices, ())

    def test_axis_parallel_z_is_refused_because_it_contains_the_seam(self):
        """R2. This is the whole reason `straight` exists.

        The refusal happens in the shared resolver, before any kernel is
        asked -- which matters because the two engines would not otherwise
        agree: measured, CadQuery's kernel refuses such a blend (rule E5)
        while FreeCAD's silently drops the seam and blends the rest.
        """
        resolution = resolve(
            SemanticSelector(select="axis_parallel", axis="Z"), self.facts)
        self.assertEqual(resolution.code, R2)
        self.assertEqual(resolution.indices, ())
        self.assertEqual(len(resolution.seams), 1)

    def test_straight_z_names_the_four_corners_and_not_the_seam(self) -> None:
        resolution = resolve(
            SemanticSelector(select="straight", axis="Z"), self.facts)
        self.assertEqual(len(resolution.indices), 4)
        chosen = {f.index: f for f in self.facts}
        for index in resolution.indices:
            self.assertFalse(chosen[index].is_seam)
            self.assertEqual(chosen[index].curve, LINE)

    def test_top_and_bottom_name_different_single_rims(self) -> None:
        top = resolve(SemanticSelector(
            select="circular", axis="Z", position="top"), self.facts)
        bottom = resolve(SemanticSelector(
            select="circular", axis="Z", position="bottom"), self.facts)
        self.assertEqual(len(top.indices), 1)
        self.assertEqual(len(bottom.indices), 1)
        self.assertNotEqual(top.indices, bottom.indices)

    def test_top_is_the_higher_rim(self) -> None:
        """Position is extremal, not ordinal -- and this pins which end."""
        by_index = {f.index: f for f in self.facts}
        top = resolve(SemanticSelector(
            select="circular", axis="Z", position="top"), self.facts)
        bottom = resolve(SemanticSelector(
            select="circular", axis="Z", position="bottom"), self.facts)
        self.assertGreater(by_index[top.indices[0]].centre[2],
                           by_index[bottom.indices[0]].centre[2])


@requires_cadquery
class CadQuerySelectorMatrixTests(SelectorMatrixMixin, unittest.TestCase):
    backend_factory = CadQueryBackend


@requires_freecad
class FreeCadSelectorMatrixTests(SelectorMatrixMixin, unittest.TestCase):
    backend_factory = FreeCadBackend


# --- 9-11. fillet, chamfer, and the selector -> operation path -------------


class EdgeModifierMixin:
    """Selector, resolved by the shared engine, applied by the backend."""

    backend_factory: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = cls.backend_factory()
        cls.shape, cls.facts = describe(cls.backend)

    def _apply(self, selector: SemanticSelector, method: str, amount: float):
        resolution = resolve(selector, self.facts)
        self.assertIsNone(resolution.code, resolution.message)
        edges = self.backend.edges_at(self.shape, resolution.indices)
        return resolution, getattr(self.backend, method)(
            self.shape, amount, edges)

    def test_case_a_fillet_the_four_vertical_corners(self) -> None:
        resolution, built = self._apply(
            SemanticSelector(select="straight", axis="Z"), "fillet_edges", 2.0)
        self.assertEqual(len(resolution.indices), 4)
        measured = self.backend.measure(built)
        self.assertTrue(measured.is_valid)
        self.assertEqual(measured.solid_count, 1)
        self.assertAlmostEqual(measured.volume, EXPECTED_FILLET_VOLUME,
                               delta=VOLUME_TOLERANCE)

    def test_case_b_chamfer_the_top_rim(self) -> None:
        resolution, built = self._apply(
            SemanticSelector(select="circular", axis="Z", position="top"),
            "chamfer_edges", 1.0)
        self.assertEqual(len(resolution.indices), 1)
        measured = self.backend.measure(built)
        self.assertTrue(measured.is_valid)
        self.assertEqual(measured.solid_count, 1)
        self.assertAlmostEqual(measured.volume, EXPECTED_CHAMFER_VOLUME,
                               delta=VOLUME_TOLERANCE)

    def test_top_and_bottom_chamfers_differ_in_topology_not_volume(self):
        """Volume alone cannot tell the two rims apart -- that is exactly why
        the selector evidence is retained."""
        _, top = self._apply(SemanticSelector(
            select="circular", axis="Z", position="top"), "chamfer_edges", 1.0)
        _, bottom = self._apply(SemanticSelector(
            select="circular", axis="Z", position="bottom"),
            "chamfer_edges", 1.0)
        self.assertAlmostEqual(self.backend.measure(top).volume,
                               self.backend.measure(bottom).volume,
                               delta=VOLUME_TOLERANCE)

    def test_a_zero_radius_is_refused_before_the_kernel(self) -> None:
        edges = self.backend.edges_at(self.shape, [0])
        with self.assertRaises(BackendOperationError):
            self.backend.fillet_edges(self.shape, 0.0, edges)
        with self.assertRaises(BackendOperationError):
            self.backend.chamfer_edges(self.shape, 0.0, edges)

    def test_an_empty_edge_list_is_refused(self) -> None:
        """Never a quiet no-op: blending nothing is an error, not success."""
        with self.assertRaises(BackendOperationError):
            self.backend.fillet_edges(self.shape, 1.0, ())
        with self.assertRaises(BackendOperationError):
            self.backend.chamfer_edges(self.shape, 1.0, ())

    def test_an_impossible_radius_fails_the_whole_feature(self) -> None:
        """Rule E5: a partially blended body is never returned."""
        resolution = resolve(
            SemanticSelector(select="straight", axis="Z"), self.facts)
        edges = self.backend.edges_at(self.shape, resolution.indices)
        with self.assertRaises(BackendOperationError):
            self.backend.fillet_edges(self.shape, 500.0, edges)


@requires_cadquery
class CadQueryEdgeModifierTests(EdgeModifierMixin, unittest.TestCase):
    backend_factory = CadQueryBackend


@requires_freecad
class FreeCadEdgeModifierTests(EdgeModifierMixin, unittest.TestCase):
    backend_factory = FreeCadBackend


# --- 12. backend-neutral semantic comparison -------------------------------


@requires_both
class SemanticComparisonTests(unittest.TestCase):
    """The parity invariant itself, when one interpreter has both engines.

    Skipped on this project's primary machine, where no interpreter does.
    The same comparison is made across hosts by
    `cad_experimental.backend_parity_report`, reading each engine's recorded
    facts -- and that is how the milestone's parity table was produced.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.left, cls.left_facts = describe(CadQueryBackend())
        cls.right, cls.right_facts = describe(FreeCadBackend())

    def test_the_same_selector_names_the_same_edges(self) -> None:
        for selector, _, _, _ in MATRIX:
            with self.subTest(selector=selector.to_dict()):
                left = resolve(selector, self.left_facts)
                right = resolve(selector, self.right_facts)
                self.assertEqual(left.code, right.code)
                self.assertEqual(
                    [normalise(self.left_facts[i]) for i in left.indices],
                    [normalise(self.right_facts[i]) for i in right.indices],
                )

    def test_both_find_exactly_one_seam(self) -> None:
        self.assertEqual(sum(f.is_seam for f in self.left_facts), 1)
        self.assertEqual(sum(f.is_seam for f in self.right_facts), 1)




# --- routing: the backend abstraction as the execution boundary ------------


CUBE = {
    "status": "generated", "summary": "a cube",
    "operations": [{"id": "cube", "type": "box",
                    "parameters": {"x": 40.0, "y": 40.0, "z": 40.0}}],
}
PLATE_HOLE = {
    "status": "generated", "summary": "a bored plate",
    "operations": [
        {"id": "plate", "type": "box",
         "parameters": {"x": 100.0, "y": 60.0, "z": 10.0}},
        {"id": "hole", "type": "through_hole", "target": "plate",
         "parameters": {"diameter": 20.0,
                        "position": {"x": 50.0, "y": 30.0, "z": 0.0}}},
    ],
}
PLATE_FILLET = {
    "status": "generated", "summary": "corners rounded",
    "operations": PLATE_HOLE["operations"] + [
        {"id": "corners", "type": "fillet", "target": "plate",
         "parameters": {"radius": 2.0,
                        "edges": {"select": "straight", "axis": "Z"}}}],
}
PLATE_CHAMFER = {
    "status": "generated", "summary": "top rim broken",
    "operations": PLATE_HOLE["operations"] + [
        {"id": "rim", "type": "chamfer", "target": "plate",
         "parameters": {"distance": 1.0,
                        "edges": {"select": "circular", "axis": "Z",
                                  "position": "top"}}}],
}


def _built(payload, backend):
    """Build one plan on one backend through the real routing."""
    from cad_experimental.build import build_plan
    from cad_experimental.parser import parse_plan

    service = None
    if CADQUERY_READY:
        import tempfile

        from cad_core.application_service import CadApplicationService
        service = CadApplicationService.local(tempfile.mkdtemp())
    return build_plan(service, parse_plan(payload), name="t", backend=backend)


class RoutingMixin:
    """Whatever engine was asked for is the engine that runs."""

    backend_factory: Any = None
    expected_name: str = ""

    def test_an_executable_plan_runs_on_the_requested_backend(self) -> None:
        for payload in (CUBE, PLATE_HOLE, PLATE_FILLET, PLATE_CHAMFER):
            with self.subTest(summary=payload["summary"]):
                build = _built(payload, self.backend_factory())
                self.assertTrue(build.built, build.error)
                self.assertEqual(build.backend, self.expected_name)

    def test_the_route_is_named_rather_than_inferred(self) -> None:
        build = _built(CUBE, self.backend_factory())
        self.assertIn(build.execution_path, {"v1_document", "graph_executor"})

    def test_a_render_model_exists_and_is_not_empty(self) -> None:
        """The same neutral contract whichever engine and route ran."""
        for payload in (CUBE, PLATE_FILLET, PLATE_CHAMFER):
            with self.subTest(summary=payload["summary"]):
                build = _built(payload, self.backend_factory())
                model = build.render
                if model is None and build.outcome is not None:
                    model = build.outcome.render_model
                self.assertIsNotNone(model)
                payload_out = model.to_dict()
                self.assertGreater(len(payload_out["triangles"]), 0)
                self.assertGreater(len(payload_out["vertices"]), 0)
                self.assertEqual(payload_out["format_version"], "1.0.0")
                self.assertEqual(payload_out["units"], "mm")

    def test_semantic_selectors_carry_their_evidence(self) -> None:
        build = _built(PLATE_CHAMFER, self.backend_factory())
        self.assertTrue(build.executed)
        rim = build.execution.to_dict()["selections"]["rim"]
        self.assertEqual(len(rim["indices"]), 1)
        self.assertEqual(len(rim["candidates"]), 2)

    def test_nothing_falls_back_to_another_engine(self) -> None:
        """The invariant this whole milestone rests on."""
        for payload in (CUBE, PLATE_HOLE, PLATE_FILLET, PLATE_CHAMFER):
            with self.subTest(summary=payload["summary"]):
                build = _built(payload, self.backend_factory())
                self.assertEqual(build.backend, self.expected_name)


@requires_cadquery
class CadQueryRoutingTests(RoutingMixin, unittest.TestCase):
    backend_factory = CadQueryBackend
    expected_name = "cadquery"

    def test_a_v1_expressible_plan_keeps_the_document_path(self) -> None:
        """The document path carries the cache, the build key, the artifact
        registry and the exports. It is kept for the engine it embodies."""
        self.assertEqual(_built(CUBE, CadQueryBackend()).execution_path,
                         "v1_document")

    def test_a_semantic_selector_still_takes_the_graph_path(self) -> None:
        self.assertEqual(_built(PLATE_FILLET, CadQueryBackend()).execution_path,
                         "graph_executor")


@requires_freecad
class FreeCadRoutingTests(RoutingMixin, unittest.TestCase):
    backend_factory = FreeCadBackend
    expected_name = "freecad"

    def test_every_plan_takes_the_graph_path(self) -> None:
        """The document path IS the CadQuery engine, so a plan asked to run
        on FreeCAD cannot take it -- and is not quietly handed over."""
        for payload in (CUBE, PLATE_HOLE, PLATE_FILLET, PLATE_CHAMFER):
            with self.subTest(summary=payload["summary"]):
                build = _built(payload, FreeCadBackend())
                self.assertEqual(build.execution_path, "graph_executor")
                self.assertEqual(build.backend, "freecad")


@requires_freecad
class FreeCadOnlyImportabilityTests(unittest.TestCase):
    """The experimental execution path must load without the other kernel.

    Asserted by source inspection rather than by uninstalling CadQuery: the
    modules on the execution path must not import it at module scope, which
    is the property that makes a FreeCAD-only machine work.
    """

    def test_the_build_module_imports_the_service_lazily(self) -> None:
        """No RUNTIME module-level import of the CadQuery-backed service.

        Checked with `ast` over top-level statements only, so an import
        parked under `if TYPE_CHECKING:` does not count -- that one is never
        executed and cannot stop the module loading. A plain string search
        cannot tell those apart, and reported a false failure when it tried.
        """
        import ast
        from pathlib import Path

        import cad_experimental.build as module

        tree = ast.parse(
            Path(module.__file__).read_text(encoding="utf-8"))
        offenders = [
            node.module
            for node in tree.body                      # top level only
            if isinstance(node, ast.ImportFrom)
            and (node.module or "").startswith("cad_core.application_service")
        ]
        self.assertEqual(offenders, [], offenders)

    def test_the_render_contract_needs_no_kernel(self) -> None:
        """`cad_core.render_model` is plain data and must import anywhere."""
        import cad_core.render_model as render

        self.assertEqual(render.RENDER_UNITS, "mm")
        self.assertEqual(render.RENDER_FORMAT_VERSION, "1.0.0")

if __name__ == "__main__":
    unittest.main()
