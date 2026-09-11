"""The experimental second backend: FreeCAD's Part kernel, headless.

FreeCAD and CadQuery both sit on OpenCascade, but they are genuinely
different stacks -- different Python bindings, different boolean and blending
entry points, different tessellators, different STEP writers. That is the
point: two independent implementations of the same Section C semantics are
what make a comparison worth running.

Locating FreeCAD
----------------
FreeCAD is not a pip package. It is found either already importable, or under
the directory named by ``CAD_FREECAD_HOME`` -- an extracted official build,
whose ``usr/lib`` and ``usr/Ext`` are put on ``sys.path``. That variable is
read as a **filesystem location only**: nothing there is executed as code by
this module, no subprocess is spawned, and no GUI is required or started.

What is deliberately absent
---------------------------
* No GUI automation, no ``FreeCADGui``, no running desktop.
* No document/recompute machinery -- shapes are built directly with
  ``Part``, which needs no ``App.Document`` and no recompute cycle.
* No generated Python. Every geometric call below is written here, in
  trusted application code. Model output never reaches FreeCAD as code, only
  as numbers that this module passes to fixed API calls.
* No fallback. If FreeCAD cannot run, :class:`BackendUnavailable` is raised
  and nothing is substituted.

Semantics
---------
The same Section C rules the CadQuery backend follows, re-implemented against
FreeCAD's API rather than translated loosely: a box positioned by its minimum
corner, a cylinder by its base-circle centre along a signed axis, a hole whose
centreline must actually intersect the material (E1), booleans that must leave
exactly one connected solid (E2/E3), and edge modifiers where a selector
matching nothing is an error (E4) and a kernel refusal fails the whole feature
rather than blending part of it (E5).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cad_core.render_model import (
    COORDINATE_SYSTEM,
    DEFAULT_ANGULAR_DEFLECTION_RAD,
    DEFAULT_LINEAR_DEFLECTION_MM,
    NORMAL_BINDING,
    RENDER_FORMAT_VERSION,
    RENDER_UNITS,
    WINDING,
    RenderBounds,
    RenderModel,
    TessellationSettings,
)

from .cad_backend import (
    FREECAD,
    FREECAD_HOME_VARIABLE,
    BackendOperationError,
    BackendUnavailable,
    CadBackend,
    Measurement,
    Selector,
    UnsupportedSelector,
    axis_direction,
    axis_index,
)

#: Angular tolerance for the parallel test, in radians. OpenCascade's own
#: ``Precision::Angular()`` is 1e-12; this matches the order of magnitude the
#: CadQuery path gets from the kernel, so the two backends agree about which
#: edges are axis-parallel rather than differing by tolerance.
ANGULAR_TOLERANCE_RAD = 1e-9

#: Sub-paths of an extracted FreeCAD distribution that must be importable.
_LIBRARY_SUBPATHS = ("usr/lib", "usr/Ext")

_MODULES: Optional[Dict[str, Any]] = None


def freecad_home() -> Optional[Path]:
    """The configured FreeCAD directory, if one is set and exists."""
    raw = os.environ.get(FREECAD_HOME_VARIABLE, "").strip()
    if not raw:
        return None
    home = Path(raw).expanduser()
    return home if home.is_dir() else None


def _load() -> Dict[str, Any]:
    """Import FreeCAD once, extending ``sys.path`` if a home is configured.

    Returns the two modules this backend uses. Raises
    :class:`BackendUnavailable` -- never falls back to another engine.
    """
    global _MODULES
    if _MODULES is not None:
        return _MODULES

    home = freecad_home()
    if home is not None:
        for sub in _LIBRARY_SUBPATHS:
            candidate = home / sub
            if candidate.is_dir() and str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))

    try:
        import FreeCAD  # noqa: PLC0415 - located at call time by design
        import Part  # noqa: PLC0415
    except ImportError as exc:
        raise BackendUnavailable(_import_hint(exc, home)) from exc

    _MODULES = {"FreeCAD": FreeCAD, "Part": Part}
    return _MODULES


def _import_hint(exc: BaseException, home: Optional[Path]) -> str:
    """Say precisely what is missing, because the two causes differ.

    A distribution that is simply absent needs a path. A distribution that is
    present but conflicts with the system's shared libraries needs
    ``LD_LIBRARY_PATH``, and that cannot be fixed from inside a running
    process -- the dynamic linker has already resolved the system copy. The
    two produce very different errors and very different remedies, so they
    are reported differently rather than as one vague failure.
    """
    detail = f"{type(exc).__name__}: {exc}"
    if home is None:
        remedy = (
            f"set {FREECAD_HOME_VARIABLE} to an extracted FreeCAD "
            "distribution -- the directory containing usr/lib"
        )
    elif "version `" in str(exc) or "symbol" in str(exc):
        remedy = (
            f"FreeCAD was found at {home}, but its bundled shared libraries "
            "conflict with the system ones already loaded. Start the process "
            f"with LD_LIBRARY_PATH={home}/usr/lib -- the dynamic linker "
            "resolves this before Python starts, so it cannot be set from "
            "here"
        )
    else:
        remedy = (
            f"FreeCAD was found at {home} but could not be imported"
        )
    return (
        f"the FreeCAD backend is unavailable: {remedy}. Nothing was "
        f"substituted -- a part must come from the engine that was asked "
        f"for. ({detail})"
    )


def freecad_available() -> bool:
    """Whether FreeCAD can be imported here. Never raises."""
    try:
        _load()
    except BackendUnavailable:
        return False
    return True


def freecad_version() -> Optional[str]:
    """The FreeCAD version string, or ``None`` when it is not available."""
    try:
        modules = _load()
    except BackendUnavailable:
        return None
    version = modules["FreeCAD"].Version()
    return ".".join(str(part) for part in version[:3])


class FreeCadBackend(CadBackend):
    """FreeCAD's ``Part`` kernel behind the shared interface."""

    name = FREECAD

    def available(self) -> bool:
        return freecad_available()

    def version(self) -> str:
        return freecad_version() or "unavailable"

    def _part(self) -> Any:
        return _load()["Part"]

    def _vector(self, xyz: Sequence[float]) -> Any:
        return _load()["FreeCAD"].Vector(float(xyz[0]), float(xyz[1]), float(xyz[2]))

    # --- construction ----------------------------------------------------

    def create_box(self, size, position=(0.0, 0.0, 0.0)) -> Any:
        """Section C.1: ``position`` is the minimum corner.

        ``Part.makeBox`` takes its point as the minimum corner too, so the
        contract maps directly with no offset arithmetic.
        """
        for value, label in zip(size, "xyz"):
            if not value > 0:
                raise BackendOperationError(
                    f"a box needs a positive {label} size; got {value!r}"
                )
        return self._part().makeBox(
            float(size[0]), float(size[1]), float(size[2]),
            self._vector(position),
        )

    def create_cylinder(
        self, diameter, height, position=(0.0, 0.0, 0.0), axis="+Z"
    ) -> Any:
        """Section C.2: ``position`` is the base circle centre."""
        if not diameter > 0 or not height > 0:
            raise BackendOperationError(
                "a cylinder needs a positive diameter and height; got "
                f"diameter={diameter!r}, height={height!r}"
            )
        return self._part().makeCylinder(
            float(diameter) / 2.0, float(height),
            self._vector(position), self._vector(axis_direction(axis)),
        )

    # --- modification ----------------------------------------------------

    def through_hole(self, target, diameter, position, axis="+Z") -> Any:
        """Section C.3, including rule E1.

        The centreline is infinite, so the axis sign is dropped: ``+Z`` and
        ``-Z`` name the same line. The cutter is finite and derived from the
        target's own bounds, so it always penetrates fully.
        """
        if not diameter > 0:
            raise BackendOperationError(
                f"a through hole needs a positive diameter; got {diameter!r}"
            )
        part = self._part()
        index = axis_index(axis)
        low, high, margin = self._axial_span(target, index)

        start = list(float(v) for v in position)
        start[index] = low - margin
        finish = list(float(v) for v in position)
        finish[index] = high + margin
        direction = [0.0, 0.0, 0.0]
        direction[index] = 1.0

        # E1, asked of the kernel rather than inferred from coordinates: the
        # centreline must actually meet the material.
        centreline = part.makeLine(
            self._vector(start), self._vector(finish)
        )
        if not target.common(centreline).Edges:
            raise BackendOperationError(
                "the hole's centreline does not intersect the target "
                "(rule E1)"
            )

        cutter = part.makeCylinder(
            float(diameter) / 2.0, (high - low) + 2.0 * margin,
            self._vector(start), self._vector(direction),
        )
        return self._cut(target, [cutter], require_removal=False)

    def subtract(self, target, tools: Sequence[Any]) -> Any:
        """Section C.4. The target survives; the tools are consumed.

        Consumption is the caller's bookkeeping -- the plan validator's P12 --
        not something a kernel can express. What this enforces is the
        geometric half: material must actually be removed, and one connected
        solid must remain.
        """
        if not tools:
            raise BackendOperationError("a subtract needs at least one tool")
        return self._cut(target, list(tools), require_removal=True)

    def _cut(self, target, tools, *, require_removal: bool) -> Any:
        before = float(target.Volume)
        result = target
        for tool in tools:
            result = result.cut(tool)
            if result is None or result.isNull():
                raise BackendOperationError(
                    "the kernel returned a null shape from the cut"
                )
        if not result.Solids:
            raise BackendOperationError("the cut left no material (rule E2)")
        if len(result.Solids) != 1:
            raise BackendOperationError(
                f"the cut split the body into {len(result.Solids)} solids; a "
                "modifier must leave exactly one (rule E3)"
            )
        if require_removal and float(result.Volume) >= before:
            raise BackendOperationError(
                "the subtract removed no material; a cut that changes nothing "
                "is an error, not a no-op"
            )
        return result

    def fillet(self, target, radius, selector: Selector) -> Any:
        """Section C.5, including rules E4 and E5."""
        if not radius > 0:
            raise BackendOperationError(
                f"a fillet needs a positive radius; got {radius!r}"
            )
        edges = self.select_edges(target, selector)
        self._require_matches(edges, selector)
        try:
            result = target.makeFillet(float(radius), list(edges))
        except Exception as exc:
            # E5: the kernel would not take every selected edge. The whole
            # feature fails; a partially blended body is never returned.
            raise BackendOperationError(
                f"the kernel refused the fillet (rule E5): {exc}"
            ) from exc
        return self._require_solid(result, "fillet")

    def chamfer(self, target, distance, selector: Selector) -> Any:
        """Section C.6: an equal setback on both adjoining faces."""
        if not distance > 0:
            raise BackendOperationError(
                f"a chamfer needs a positive distance; got {distance!r}"
            )
        edges = self.select_edges(target, selector)
        self._require_matches(edges, selector)
        try:
            result = target.makeChamfer(float(distance), list(edges))
        except Exception as exc:
            raise BackendOperationError(
                f"the kernel refused the chamfer (rule E5): {exc}"
            ) from exc
        return self._require_solid(result, "chamfer")

    @staticmethod
    def _require_matches(edges, selector: Selector) -> None:
        if not edges:
            raise BackendOperationError(
                f"the selector {selector.select!r}"
                + (f"/{selector.axis}" if selector.axis else "")
                + " matched no edge (rule E4)"
            )

    @staticmethod
    def _require_solid(result: Any, label: str) -> Any:
        if result is None or result.isNull() or not result.Solids:
            raise BackendOperationError(f"the {label} produced no solid")
        if len(result.Solids) != 1:
            raise BackendOperationError(
                f"the {label} left {len(result.Solids)} solids (rule E3)"
            )
        if not result.isValid():
            raise BackendOperationError(
                f"the {label} produced a shape the kernel reports as invalid"
            )
        return result

    @staticmethod
    def _axial_span(target: Any, index: int) -> Tuple[float, float, float]:
        box = target.BoundBox
        low = (box.XMin, box.YMin, box.ZMin)[index]
        high = (box.XMax, box.YMax, box.ZMax)[index]
        span = high - low
        if span <= 0.0:
            raise BackendOperationError(
                "the target has a degenerate bounding box, so no cutting "
                "extent can be derived from it"
            )
        return low, high, max(1.0, span * 0.1)

    # --- edge selection ---------------------------------------------------

    def select_edges(self, shape, selector: Selector) -> Tuple[Any, ...]:
        """Translate the V1 selector to FreeCAD. **No new selector language.**

        ``all`` returns every edge in the shape's own order. ``axis_parallel``
        returns the straight edges parallel to the named unsigned axis, which
        is the same test the CadQuery path makes -- a line curve, and a
        direction parallel to the axis with the sign ignored.

        A selector this cannot map is refused (:class:`UnsupportedSelector`).
        There is no nearest-edge rule and none is invented: silently choosing
        a different edge would change which edges a part was filleted on
        without anyone being told.
        """
        selector.validate()
        edges = list(shape.Edges)
        if selector.select == "all":
            return tuple(edges)

        index = axis_index(selector.axis or "")
        wanted = [0.0, 0.0, 0.0]
        wanted[index] = 1.0
        return tuple(
            edge for edge in edges if self._is_parallel_line(edge, wanted)
        )

    def _is_parallel_line(self, edge: Any, wanted: Sequence[float]) -> bool:
        """True if ``edge`` is a straight line parallel to ``wanted``.

        Parallelism is tested **unsigned**, as Section C.7 requires: an edge
        running -Z is parallel to Z. The curve type is read from the kernel,
        not guessed from vertex counts.
        """
        part = self._part()
        curve = edge.Curve
        if not isinstance(curve, part.Line):
            return False
        direction = curve.Direction
        length = (
            direction.x ** 2 + direction.y ** 2 + direction.z ** 2
        ) ** 0.5
        if length == 0.0:
            return False
        # |cross product| of two unit vectors is sin(angle); comparing it to
        # the tolerance is the unsigned parallel test.
        ux, uy, uz = (
            direction.x / length, direction.y / length, direction.z / length
        )
        wx, wy, wz = wanted
        cross = (
            uy * wz - uz * wy,
            uz * wx - ux * wz,
            ux * wy - uy * wx,
        )
        sine = (cross[0] ** 2 + cross[1] ** 2 + cross[2] ** 2) ** 0.5
        return sine <= ANGULAR_TOLERANCE_RAD

    # --- reading out -----------------------------------------------------

    def measure(self, shape) -> Measurement:
        box = shape.BoundBox
        return Measurement(
            is_valid=bool(shape.isValid()),
            solid_count=len(shape.Solids),
            volume=float(shape.Volume),
            face_count=len(shape.Faces),
            edge_count=len(shape.Edges),
            minimum=(box.XMin, box.YMin, box.ZMin),
            maximum=(box.XMax, box.YMax, box.ZMax),
        )

    def export_step(self, shape, path) -> Path:
        """Write STEP with FreeCAD's own ``Part`` writer.

        Verified by re-reading, not by the file merely existing: an exporter
        that writes an empty or unreadable file has not exported anything.
        """
        destination = Path(path)
        if destination.suffix.lower() not in (".step", ".stp"):
            raise BackendOperationError(
                f"expected a .step or .stp path; got {destination.name!r}. "
                "The format is never silently switched."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shape.exportStep(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise BackendOperationError(
                "the STEP writer returned without leaving a non-empty file"
            )
        return destination

    def read_step(self, path) -> Any:
        source = Path(path)
        if not source.is_file():
            raise BackendOperationError(f"no STEP file at {source}")
        shape = self._part().Shape()
        shape.read(str(source))
        if shape.isNull():
            raise BackendOperationError(
                f"the STEP file at {source} read back as a null shape"
            )
        return shape

    def render_model(self, shape, *, part_name: str, feature_id: str) -> Any:
        """Build the **existing** :class:`cad_core.render_model.RenderModel`.

        FreeCAD tessellates; the result is packed into the same dataclass the
        CadQuery path produces, with the same format version, units, winding
        and normal binding. The frontend cannot tell the two apart, which is
        the requirement -- there is deliberately no second render format.

        Normals are per-vertex and computed from the triangles that meet at
        each vertex, matching the existing model's ``normal_binding``.
        """
        vertices, triangles = shape.tessellate(DEFAULT_LINEAR_DEFLECTION_MM)
        if not triangles:
            raise BackendOperationError(
                "tessellation produced no triangles; there is nothing to draw"
            )

        points: Tuple[Tuple[float, float, float], ...] = tuple(
            (float(v.x), float(v.y), float(v.z)) for v in vertices
        )
        faces: List[Tuple[int, int, int]] = []
        for triangle in triangles:
            a, b, c = (int(triangle[0]), int(triangle[1]), int(triangle[2]))
            for index in (a, b, c):
                if not 0 <= index < len(points):
                    raise BackendOperationError(
                        f"tessellation produced index {index} outside the "
                        f"{len(points)}-vertex array"
                    )
            faces.append((a, b, c))

        normals = _vertex_normals(points, faces)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        zs = [p[2] for p in points]
        return RenderModel(
            format_version=RENDER_FORMAT_VERSION,
            part_name=part_name,
            feature_id=feature_id,
            # Imported from the existing model, never retyped: a second
            # engine writing its own idea of these strings is exactly how a
            # "compatible" render model stops being compatible.
            units=RENDER_UNITS,
            coordinate_system=COORDINATE_SYSTEM,
            winding=WINDING,
            normal_binding=NORMAL_BINDING,
            vertices=points,
            triangles=tuple(faces),
            normals=normals,
            bounds=RenderBounds(
                minimum=(min(xs), min(ys), min(zs)),
                maximum=(max(xs), max(ys), max(zs)),
            ),
            tessellation=TessellationSettings(
                linear_deflection_mm=DEFAULT_LINEAR_DEFLECTION_MM,
                angular_deflection_rad=DEFAULT_ANGULAR_DEFLECTION_RAD,
            ),
        )


def _vertex_normals(points, faces):
    """Area-weighted per-vertex normals, as plain tuples."""
    sums = [[0.0, 0.0, 0.0] for _ in points]
    for a, b, c in faces:
        pa, pb, pc = points[a], points[b], points[c]
        u = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        v = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        normal = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        for index in (a, b, c):
            sums[index][0] += normal[0]
            sums[index][1] += normal[1]
            sums[index][2] += normal[2]
    result = []
    for x, y, z in sums:
        length = (x * x + y * y + z * z) ** 0.5
        result.append((0.0, 0.0, 1.0) if length == 0.0
                      else (x / length, y / length, z / length))
    return tuple(result)


__all__ = [
    "ANGULAR_TOLERANCE_RAD",
    "FreeCadBackend",
    "freecad_available",
    "freecad_home",
    "freecad_version",
]
