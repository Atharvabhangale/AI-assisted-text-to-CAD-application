"""The baseline backend: CadQuery over OpenCascade, via the existing engine.

This is a **thin adapter**, not a second implementation. Every piece of real
behaviour comes from ``cad_core``:

* edge selection from :func:`cad_core.edge_selection.select_edges`;
* the RenderModel from :func:`cad_core.render_model.build_render_model`;
* STEP from :func:`cad_core.step_export.export_step` / ``read_step``.

Only the primitive and boolean calls are made here, and they are the same
CadQuery calls ``cad_core.local_cad`` makes, with the same Section C rules --
because this backend must remain *the baseline*. If it drifted from the
production engine, the cross-backend comparison would be measuring the wrong
thing.

Nothing in ``cad_core`` was modified to make this fit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, Tuple

from cad_core.local_cad import LocalCadResult
from cad_core.model import EdgeSelector
from cad_core.render_model import build_render_model
from cad_core.step_export import export_step, read_step

from .cad_backend import (
    CADQUERY,
    BackendOperationError,
    CadBackend,
    Measurement,
    Selector,
    UnsupportedSelector,
    axis_direction,
    axis_index,
)


def _import_cadquery() -> Any:
    try:
        import cadquery  # noqa: PLC0415 - lazy, like every other kernel import
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise ImportError("the cadquery backend requires cadquery") from exc
    return cadquery


class CadQueryBackend(CadBackend):
    """The existing engine, behind the shared interface."""

    name = CADQUERY

    def available(self) -> bool:
        try:
            _import_cadquery()
        except ImportError:
            return False
        return True

    def version(self) -> str:
        return str(getattr(_import_cadquery(), "__version__", "unknown"))

    # --- construction ----------------------------------------------------

    def create_box(self, size, position=(0.0, 0.0, 0.0)) -> Any:
        cq = _import_cadquery()
        for value, label in zip(size, "xyz"):
            if not value > 0:
                raise BackendOperationError(
                    f"a box needs a positive {label} size; got {value!r}"
                )
        return cq.Solid.makeBox(
            size[0], size[1], size[2], pnt=cq.Vector(*position)
        )

    def create_cylinder(
        self, diameter, height, position=(0.0, 0.0, 0.0), axis="+Z"
    ) -> Any:
        cq = _import_cadquery()
        if not diameter > 0 or not height > 0:
            raise BackendOperationError(
                "a cylinder needs a positive diameter and height; got "
                f"diameter={diameter!r}, height={height!r}"
            )
        return cq.Solid.makeCylinder(
            diameter / 2.0, height,
            pnt=cq.Vector(*position),
            dir=cq.Vector(*axis_direction(axis)),
        )

    # --- modification ----------------------------------------------------

    def through_hole(self, target, diameter, position, axis="+Z") -> Any:
        cq = _import_cadquery()
        if not diameter > 0:
            raise BackendOperationError(
                f"a through hole needs a positive diameter; got {diameter!r}"
            )
        index = axis_index(axis)
        low, high, margin = self._axial_span(target, index)

        centre = list(position)
        centre[index] = low - margin
        far = list(position)
        far[index] = high + margin
        direction = [0.0, 0.0, 0.0]
        direction[index] = 1.0

        # E1: the centreline must actually intersect the material. A hole that
        # misses is an error, never a quiet no-op.
        centreline = cq.Edge.makeLine(cq.Vector(*centre), cq.Vector(*far))
        if not target.intersect(centreline).Edges():
            raise BackendOperationError(
                "the hole's centreline does not intersect the target "
                "(rule E1)"
            )

        cutter = cq.Solid.makeCylinder(
            diameter / 2.0, (high - low) + 2.0 * margin,
            pnt=cq.Vector(*centre), dir=cq.Vector(*direction),
        )
        return self._cut(target, [cutter], require_removal=False)

    def subtract(self, target, tools: Sequence[Any]) -> Any:
        if not tools:
            raise BackendOperationError("a subtract needs at least one tool")
        return self._cut(target, list(tools), require_removal=True)

    def _cut(self, target, tools, *, require_removal: bool) -> Any:
        before = float(target.Volume())
        result = target
        for tool in tools:
            result = result.cut(tool)
        if result is None or not result.Solids():
            raise BackendOperationError(
                "the cut left no material (rule E2)"
            )
        # E3: one connected solid, not a scattering of fragments.
        if len(result.Solids()) != 1:
            raise BackendOperationError(
                f"the cut split the body into {len(result.Solids())} solids; "
                "a modifier must leave exactly one (rule E3)"
            )
        if require_removal and float(result.Volume()) >= before:
            raise BackendOperationError(
                "the subtract removed no material; a cut that changes nothing "
                "is an error, not a no-op"
            )
        return result

    def fillet(self, target, radius, selector: Selector) -> Any:
        if not radius > 0:
            raise BackendOperationError(
                f"a fillet needs a positive radius; got {radius!r}"
            )
        edges = self.select_edges(target, selector)
        self._require_matches(edges, selector)
        try:
            result = target.fillet(radius, list(edges))
        except Exception as exc:
            raise BackendOperationError(
                f"the kernel refused the fillet (rule E5): {exc}"
            ) from exc
        return self._require_solid(result, "fillet")

    def chamfer(self, target, distance, selector: Selector) -> Any:
        if not distance > 0:
            raise BackendOperationError(
                f"a chamfer needs a positive distance; got {distance!r}"
            )
        edges = self.select_edges(target, selector)
        self._require_matches(edges, selector)
        try:
            result = target.chamfer(distance, None, list(edges))
        except Exception as exc:
            raise BackendOperationError(
                f"the kernel refused the chamfer (rule E5): {exc}"
            ) from exc
        return self._require_solid(result, "chamfer")

    @staticmethod
    def _require_matches(edges, selector: Selector) -> None:
        # E4: a selector that matches nothing is an error, never a no-op.
        if not edges:
            raise BackendOperationError(
                f"the selector {selector.select!r}"
                + (f"/{selector.axis}" if selector.axis else "")
                + " matched no edge (rule E4)"
            )

    @staticmethod
    def _require_solid(result: Any, label: str) -> Any:
        if result is None or not result.Solids():
            raise BackendOperationError(f"the {label} produced no solid")
        if len(result.Solids()) != 1:
            raise BackendOperationError(
                f"the {label} left {len(result.Solids())} solids (rule E3)"
            )
        return result

    @staticmethod
    def _axial_span(target: Any, index: int) -> Tuple[float, float, float]:
        box = target.BoundingBox()
        low = (box.xmin, box.ymin, box.zmin)[index]
        high = (box.xmax, box.ymax, box.zmax)[index]
        span = high - low
        if span <= 0.0:
            raise BackendOperationError(
                "the target has a degenerate bounding box, so no cutting "
                "extent can be derived from it"
            )
        return low, high, max(1.0, span * 0.1)

    # --- reading out -----------------------------------------------------

    def select_edges(self, shape, selector: Selector) -> Tuple[Any, ...]:
        """Delegates to the existing selector. No second implementation."""
        selector.validate()
        try:
            native = EdgeSelector(select=selector.select, axis=selector.axis)
        except Exception as exc:
            raise UnsupportedSelector(str(exc)) from exc
        from cad_core.edge_selection import select_edges as core_select
        from cad_core.edge_selection import UnsupportedSelectorError

        try:
            return core_select(shape, native)
        except UnsupportedSelectorError as exc:
            raise UnsupportedSelector(str(exc)) from exc

    def measure(self, shape) -> Measurement:
        box = shape.BoundingBox()
        return Measurement(
            is_valid=bool(shape.isValid()),
            solid_count=len(shape.Solids()),
            volume=float(shape.Volume()),
            face_count=len(shape.Faces()),
            edge_count=len(shape.Edges()),
            minimum=(box.xmin, box.ymin, box.zmin),
            maximum=(box.xmax, box.ymax, box.zmax),
        )

    def _result(self, shape, part_name: str, feature_id: str) -> LocalCadResult:
        return LocalCadResult(
            part_name=part_name, feature_id=feature_id, shape=shape
        )

    def export_step(self, shape, path) -> Path:
        return export_step(self._result(shape, "part", "shape"), path)

    def read_step(self, path) -> Any:
        """Return the kernel shape, not the engine's wrapper.

        ``cad_core.step_export.read_step`` hands back an
        ``ImportedStepSolid``; the backend interface promises a shape that
        :meth:`measure` accepts, so the wrapper is unwrapped here rather than
        leaking a cad_core type through an interface both engines share.
        """
        return read_step(path).shape

    def render_model(self, shape, *, part_name: str, feature_id: str) -> Any:
        """The existing RenderModel, built by the existing tessellator."""
        return build_render_model(self._result(shape, part_name, feature_id))


__all__ = ["CadQueryBackend"]
