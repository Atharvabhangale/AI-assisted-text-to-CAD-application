"""Deterministic edge selection for the V1 edge selectors (Section C.7).

This is infrastructure, not a feature. It answers one question -- *which
kernel edges of this shape does this selector name?* -- and answers it in a
stable order. It builds nothing, changes nothing and measures nothing else.

```
B-rep solid ──┐
              ├─ select_edges ─→ deterministic tuple of kernel edges
EdgeSelector ─┘
```

Where it sits
-------------
Downstream of the neutral specification and of the local CAD engine, upstream
of nothing yet. ``fillet`` and ``chamfer`` are the intended consumers and are
**not implemented**; when they are, they will call this layer and own rules E4
and E5 themselves. Nothing here raises E4: a selector that matches nothing
returns an empty tuple, because "matched nothing" is a fact about a selector
while "a fillet that affects nothing is an error" is a fact about a fillet.

The dependency runs one way only. The validator, the FeatureScript generator
and the exporters do not import this module, and this module does not import
them.

Supported selectors
-------------------
Exactly the two the specification defines, and no others:

* ``{"select": "all"}`` -- every **sharp** edge of the shape;
* ``{"select": "axis_parallel", "axis": "X" | "Y" | "Z"}`` -- every
  **sharp, straight** edge whose direction is parallel to that axis, where the
  axis is **unsigned**: a ``+X`` edge and a ``-X`` edge both match ``"X"``.

Sharp edges only (schema 1.1.1)
-------------------------------
An edge is **sharp** when two *different* faces meet along it at an angle --
a crease a person could see and round. Two kinds of kernel edge are not, and
no selector matches them:

* a **seam**: the line where a closed surface meets itself, such as the
  straight line down the side of every cylinder and every hole. Only one face
  lies on both sides of it;
* a **smooth edge**: where two faces meet tangentially, such as the boundary
  of a fillet. The surface turns through no angle there.

Until 1.1.1 both were matched, and the kernel refuses to blend either, so any
fillet or chamfer whose selection included one failed with rule E5 -- which
made every cylinder, every part with a hole and every part already blended
impossible to round with ``all``. No document that built before builds
differently now: a selection containing a seam or a smooth edge never built.

Classification is read from the kernel: the faces adjoining an edge come from
``TopExp.MapShapesAndAncestors``, and tangency is the angle between the two
faces' outward normals, evaluated on each face's own parametric curve of the
edge at three points along it. :data:`SMOOTH_ANGLE_TOLERANCE_RAD` decides
"tangent"; see its comment for why it is safe.

The input is the typed :class:`~cad_core.model.EdgeSelector`, not a dictionary.
A raw selector dictionary is refused, the same way the local engine refuses a
raw specification document.

How an edge's geometry is read
------------------------------
Through the kernel, never through coordinates. Each edge is wrapped in
OpenCascade's ``BRepAdaptor_Curve``, which reports:

* ``GetType()`` -- the real curve type, so a straight edge is one whose type is
  ``GeomAbs_Line``. Circles report ``GeomAbs_Circle`` and never match an
  axis-parallel selector. No vertex counting, bounding-box guessing or
  string matching is involved.
* ``Line().Direction()`` -- the underlying line's direction as a ``gp_Dir``.

``BRepAdaptor_Curve`` is **location-aware**: it applies the edge's placement,
so a rotated or relocated shape reports rotated directions (measured on a box
rotated 45 degrees about Z and on one carrying a ``TopLoc`` rotation of 30
degrees). The selector is therefore not coupled to axis-aligned primitives,
even though V1 has no rotation to exercise that yet. Endpoint differences are
never used to infer a direction.

Parallelism is decided by ``gp_Dir.IsParallel(other, tolerance)``, which is
the kernel's own test and is **unsigned** -- it answers true for opposite
directions as well as identical ones (measured). That is exactly the
specification's unsigned-axis rule, so no hand-rolled dot product or sign
normalisation appears here.

Tolerance
---------
:data:`ANGULAR_TOLERANCE_RAD` is OpenCascade's own ``Precision::Angular()``,
1e-12 rad. It is not a number this project chose: it is the kernel's notion of
angular confusion, used unmodified.

How much of it V1 geometry actually needs was measured: across a box, a
cylinder, a drilled plate, a two-hole plate and a two-tool subtract, every
axis-parallel line direction came back as an exact unit vector -- deviation
**0.0**, not merely small. The tolerance is therefore headroom for generality,
not a fudge factor, and floating-point equality is still not used.

Ordering
--------
The order is ``shape.Edges()`` order, preserved exactly. That order was
measured to be stable across repeated calls on one shape, across repeated
builds of the same part, and across separate processes, so re-sorting would add
risk without adding determinism -- there is no unique key to sort a box's four
parallel edges by, and any float-coordinate key would need arbitrary
tie-breaking. See ``docs/edge-selection.md`` for the measurements.

``Edges()`` also contains each topological edge exactly once: for the two-hole
plate it returns 18 edges, matching the 18 entries of the kernel's own
``TopTools_IndexedMapOfShape`` over ``TopAbs_EDGE``.

What this layer does not do
---------------------------
No geometry is created, modified or moved -- the returned objects are the
shape's own edges, and the shape is untouched. There are no persistent edge
ids, no named topology and no general topology query language: the only
questions that can be asked are the two the specification defines.
"""

from __future__ import annotations

from typing import Any, List, Tuple

from cad_core.model import (
    EDGE_SELECT_VALUES,
    SELECTOR_AXIS_VALUES,
    EdgeSelector,
)

try:
    import cadquery as _cq
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "edge selection requires CadQuery, which is an optional dependency of "
        "cad-core. Install it with the 'local-cad' extra, e.g. "
        "`pip install cadquery`."
    ) from exc

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_CurveType
from OCP.gp import gp_Dir, gp_Pnt, gp_Vec
from OCP.Precision import Precision
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

#: OpenCascade's own angular confusion, in radians (``Precision::Angular()``).
#: Read from the kernel rather than chosen here.
ANGULAR_TOLERANCE_RAD: float = Precision.Angular_s()

#: Two faces whose outward normals differ by less than this along an edge
#: meet **tangentially**, so the edge is smooth rather than sharp. Chosen, not
#: read from the kernel, and measured from both sides: every blend boundary
#: measured (fillets on boxes, on joined boxes, on Z-then-X blends) has an
#: angle of exactly 0.0, while the smallest crease V1 geometry produces is the
#: 45 degrees of a chamfer. One micro-radian sits between them by many orders
#: of magnitude on each side.
SMOOTH_ANGLE_TOLERANCE_RAD: float = 1e-6

#: Where along an edge tangency is tested, as fractions of its parameter range.
#: An edge counts as smooth only if it is tangent at every one of them.
_TANGENCY_SAMPLES: Tuple[float, ...] = (0.25, 0.5, 0.75)

#: What an edge is, for selection. Only ``SHARP`` edges are ever selected.
SHARP = "sharp"
SEAM = "seam"
SMOOTH = "smooth"
DEGENERATE = "degenerate"

#: The unsigned selector axes of Section C.7, as kernel directions. Unsigned is
#: not enforced by this table but by ``gp_Dir.IsParallel``, which treats
#: opposite directions as parallel.
_AXIS_DIRECTIONS = {
    "X": gp_Dir(1.0, 0.0, 0.0),
    "Y": gp_Dir(0.0, 1.0, 0.0),
    "Z": gp_Dir(0.0, 0.0, 1.0),
}


class EdgeSelectionError(Exception):
    """Base class for edge-selection failures."""


class UnsupportedSelectorError(EdgeSelectionError):
    """Raised for a selector this layer cannot evaluate.

    Deliberately distinct from the geometry engine's errors: an unusable
    selector is not a failed geometric operation. A selector that is valid but
    matches nothing is **not** an error here -- it returns an empty tuple, and
    rule E4 belongs to whichever modifier asked.
    """


def select_edges(shape: Any, selector: EdgeSelector) -> Tuple[Any, ...]:
    """Return the edges of ``shape`` named by ``selector``, in a stable order.

    Args:
        shape: A kernel shape from the local CAD engine -- in practice
            :attr:`~cad_core.local_cad.LocalCadResult.shape`, or a solid held
            in the engine's solid set mid-evaluation.
        selector: A validated :class:`~cad_core.model.EdgeSelector`. A raw
            dictionary is not accepted.

    Returns:
        A tuple of the shape's own **sharp** ``cadquery.Edge`` objects, in
        ``shape.Edges()`` order. Seams and smooth edges are never returned
        (see the module docstring). Empty if nothing matches; that is a
        result, not an error.

    Raises:
        TypeError: if ``shape`` is not a kernel shape, or ``selector`` is not
            an :class:`~cad_core.model.EdgeSelector`.
        UnsupportedSelectorError: if the selector's ``select`` or ``axis`` is
            not one the specification defines, or if ``axis`` is present or
            absent when it should not be (rule S18). Never guessed at.
    """
    kind, axis = _require_selector(selector)
    edges = sharp_edges(shape)

    if kind == "all":
        return edges

    direction = _AXIS_DIRECTIONS[axis]
    return tuple(
        edge for edge in edges if _is_parallel_line(edge, direction)
    )


def sharp_edges(shape: Any) -> Tuple[Any, ...]:
    """Every sharp edge of ``shape``, in ``shape.Edges()`` order."""
    adjacency = _face_adjacency(_require_shape(shape))
    return tuple(
        edge
        for edge in shape.Edges()
        if _classify(edge, adjacency) == SHARP
    )


def edge_kind(shape: Any, edge: Any) -> str:
    """Classify one edge of ``shape``: :data:`SHARP`, :data:`SEAM`,
    :data:`SMOOTH` or :data:`DEGENERATE`.

    Exposed so a caller or a test can check the same fact the selectors
    decide on.
    """
    adjacency = _face_adjacency(_require_shape(shape))
    return _classify(_require_edge(edge), adjacency)


def _face_adjacency(shape: Any) -> TopTools_IndexedDataMapOfShapeListOfShape:
    adjacency = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(
        shape.wrapped, TopAbs_EDGE, TopAbs_FACE, adjacency
    )
    return adjacency


def _classify(edge: Any, adjacency: TopTools_IndexedDataMapOfShapeListOfShape) -> str:
    kernel_edge = TopoDS.Edge_s(edge.wrapped)
    if BRep_Tool.Degenerated_s(kernel_edge):
        return DEGENERATE
    faces: List[Any] = []
    if adjacency.Contains(kernel_edge):
        for face in adjacency.FindFromKey(kernel_edge):
            if not any(face.IsSame(known) for known in faces):
                faces.append(face)
    if len(faces) == 1:
        # The same face on both sides: a closed surface's seam.
        return SEAM
    if len(faces) == 2 and _is_tangent(
        kernel_edge, TopoDS.Face_s(faces[0]), TopoDS.Face_s(faces[1])
    ):
        return SMOOTH
    # Two faces at an angle. An edge with no face (a bare wire, never part of
    # a V1 solid) has no surface to be a seam of or tangent across, so it is
    # not excluded either.
    return SHARP


def _is_tangent(edge: Any, first: Any, second: Any) -> bool:
    curve = BRepAdaptor_Curve(edge)
    start, end = curve.FirstParameter(), curve.LastParameter()
    for fraction in _TANGENCY_SAMPLES:
        parameter = start + fraction * (end - start)
        angle = _outward_normal(edge, first, parameter).Angle(
            _outward_normal(edge, second, parameter)
        )
        if angle >= SMOOTH_ANGLE_TOLERANCE_RAD:
            return False
    return True


def _outward_normal(edge: Any, face: Any, parameter: float) -> Any:
    """The face's outward unit normal at ``parameter`` along ``edge``.

    Read on the face's own parametric curve of the edge, so the point is
    exactly on both the edge and that face's surface.
    """
    pcurve = BRep_Tool.CurveOnSurface_s(edge, face, 0.0, 0.0)
    uv = pcurve.Value(parameter)
    point, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    BRepAdaptor_Surface(face).D1(uv.X(), uv.Y(), point, du, dv)
    normal = du.Crossed(dv)
    if face.Orientation() == TopAbs_REVERSED:
        normal.Reverse()
    return normal


def edge_curve_type(edge: Any) -> str:
    """Return the kernel's curve type for ``edge``, e.g. ``"GeomAbs_Line"``.

    Exposed because it is what the selector itself decides on, so a caller or
    a test can check the same fact the selector checked instead of inferring
    it from coordinates.
    """
    return str(BRepAdaptor_Curve(_require_edge(edge).wrapped).GetType()).rsplit(
        ".", 1
    )[-1]


def is_straight_edge(edge: Any) -> bool:
    """True if the kernel reports ``edge`` as a line.

    A circle, ellipse, spline or any other curve is not straight, and this is
    read from ``BRepAdaptor_Curve.GetType()`` rather than from vertex counts
    or endpoint arithmetic.
    """
    return (
        BRepAdaptor_Curve(_require_edge(edge).wrapped).GetType()
        == GeomAbs_CurveType.GeomAbs_Line
    )


def line_direction(edge: Any) -> Tuple[float, float, float]:
    """Return the unit direction of a straight ``edge`` as plain floats.

    The direction comes from the edge's underlying line, with the edge's
    placement applied, and is independent of the edge's ``TopAbs`` orientation
    -- a ``REVERSED`` edge reports the same direction as its ``FORWARD``
    twin, which is why parallelism must be tested unsigned.

    Raises:
        EdgeSelectionError: if the edge is not a line. There is no direction
            to report for a curve, and none is invented.
    """
    curve = BRepAdaptor_Curve(_require_edge(edge).wrapped)
    if curve.GetType() != GeomAbs_CurveType.GeomAbs_Line:
        raise EdgeSelectionError(
            "only a straight edge has a line direction; the kernel reports "
            f"this edge as {str(curve.GetType()).rsplit('.', 1)[-1]!r}"
        )
    direction = curve.Line().Direction()
    return (direction.X(), direction.Y(), direction.Z())


def _is_parallel_line(edge: Any, direction: gp_Dir) -> bool:
    """True if ``edge`` is a line parallel to ``direction``, sign ignored."""
    curve = BRepAdaptor_Curve(edge.wrapped)
    if curve.GetType() != GeomAbs_CurveType.GeomAbs_Line:
        return False
    # gp_Dir.IsParallel is the kernel's own test and is unsigned: it holds for
    # opposite directions too, which is the specification's rule for this
    # selector.
    return bool(
        curve.Line().Direction().IsParallel(direction, ANGULAR_TOLERANCE_RAD)
    )


def _require_shape(shape: Any) -> Any:
    if not isinstance(shape, _cq.Shape):
        raise TypeError(
            "select_edges requires a kernel shape from the local CAD engine, "
            "such as LocalCadResult.shape; got "
            f"{type(shape).__name__}"
        )
    return shape


def _require_edge(edge: Any) -> Any:
    if not isinstance(edge, _cq.Edge):
        raise TypeError(
            f"expected a cadquery Edge; got {type(edge).__name__}"
        )
    return edge


def _require_selector(selector: Any) -> Tuple[str, Any]:
    """Validate the selector defensively and return ``(select, axis)``.

    The validator already enforces rule S18, so a selector reaching here from
    a validated part is well formed. These checks exist so that inconsistent
    typed input is reported rather than silently selecting the wrong edges,
    and so that a dictionary cannot be used as the primary API.
    """
    if not isinstance(selector, EdgeSelector):
        raise TypeError(
            "select_edges requires a typed cad_core.model.EdgeSelector, as "
            "carried by a validated fillet or chamfer feature; got "
            f"{type(selector).__name__}. Raw selector dictionaries are not "
            "accepted."
        )

    if selector.select not in EDGE_SELECT_VALUES:
        permitted = ", ".join(repr(value) for value in EDGE_SELECT_VALUES)
        raise UnsupportedSelectorError(
            f"unsupported selector {selector.select!r}; the specification "
            f"defines only {permitted}"
        )

    if selector.select == "all":
        if selector.axis is not None:
            raise UnsupportedSelectorError(
                "selector 'all' must not carry an 'axis' (rule S18); got "
                f"{selector.axis!r}"
            )
        return "all", None

    if selector.axis is None:
        raise UnsupportedSelectorError(
            "selector 'axis_parallel' requires an 'axis' (rule S18); none was "
            "given"
        )
    if selector.axis not in SELECTOR_AXIS_VALUES:
        permitted = ", ".join(repr(value) for value in SELECTOR_AXIS_VALUES)
        raise UnsupportedSelectorError(
            f"unsupported selector axis {selector.axis!r}; the specification "
            f"defines only the unsigned letters {permitted}. The signed forms "
            "used by 'cylinder' and 'through_hole' are deliberately different."
        )
    return "axis_parallel", selector.axis
