"""Local deterministic CAD engine: builds real B-rep geometry from a V1 part.

This is an **execution backend**, not a replacement for the specification. The
neutral CAD specification stays the source of truth, and two backends consume
it independently:

```
CAD specification -> local CAD engine (this module)      -> B-rep solid
CAD specification -> FeatureScript generator             -> Onshape
```

Nothing in the specification knows this module exists, and nothing here feeds
back into it: no CadQuery concept appears in :mod:`cad_core.model`.

Backend
-------
CadQuery (over OpenCascade, via ``cadquery-ocp``). The geometry produced is a
genuine B-rep solid from the kernel -- not a mesh, not triangles, not a
bounding-box stand-in.

Supported subset
----------------
A part in millimetres whose feature history is **one constructive feature**
(``box`` or ``cylinder``) optionally followed by **any number of
``through_hole`` modifiers targeting it**. Generic ``subtract``, ``fillet`` and
``chamfer`` are **not implemented**: they are rejected, never partially built
and never silently skipped.

That subset is not an arbitrary choice -- it is what the specification's
solid-set rules (Section B.4) allow with the features implemented here. A
second constructive feature would leave two solids and fail rule S9 unless a
``subtract`` consumed one, and ``subtract`` is not implemented.

Evaluation model (specification Section B.4)
--------------------------------------------
Features are evaluated in order against an ordered **solid set**:

* a constructive feature adds a solid named by its own ``id``;
* a ``through_hole`` **replaces its target in place** -- the result keeps the
  *target's* id and the target's position in the set, and the hole's own ``id``
  never names a solid;
* after the last feature the set must hold exactly one solid, which becomes the
  part's geometry.

So a plate with four holes stays one solid called ``plate`` throughout, and
:attr:`LocalCadResult.feature_id` is the id of that surviving solid.

Through-hole semantics (specification Section C.3)
--------------------------------------------------
The hole's centreline is the **infinite** line through ``position`` along
``axis``, and the material removed is the infinite cylinder of the given
diameter about that line, intersected with the target. There is no depth, no
counterbore and no taper.

Two consequences follow from the line being infinite, and both are implemented
rather than approximated:

* the component of ``position`` along the axis has no effect;
* the **sign** of the axis has no effect either. An infinite line through a
  point along ``+Z`` is the same line as along ``-Z``, so a ``+Z`` and a ``-Z``
  hole at the same position cut identically. (This is unlike a *cylinder*,
  where the sign decides which way the solid extends.) The implementation
  therefore works from the unsigned axis.

How the infinite cut is realised
--------------------------------
OpenCascade booleans need bounded solids, so the cut is performed with a finite
cylinder whose length is **derived from the target's own bounding box**, never
from a hard-coded size:

1. measure the target's bounding box;
2. take its extent ``[lo, hi]`` along the hole axis;
3. take the box's diagonal length as the margin -- necessarily at least as long
   as any single extent, and zero only for a degenerate solid;
4. build the cutting cylinder from ``lo - margin``, of length
   ``(hi - lo) + 2 * margin``.

The cutter therefore protrudes past both faces by at least the target's largest
dimension, so the cut is geometrically identical to the unbounded one while
staying a finite boolean the kernel can evaluate.

Geometric rules E1 and E3
-------------------------
Both are checked with the kernel, and a failure raises
:class:`GeometryOperationError` rather than returning a best-effort shape:

* **E1** -- the centreline must actually intersect the target. Tested by
  intersecting a line segment along the centreline with the target solid: if
  the common shape contains no edge, the centreline misses, and that is an
  error, not a silent no-op. A hole that merely grazes the material is caught
  by this too, because the test is on the *centreline*, not on whether any
  material happened to be removed.
* **E3** -- the result must be a single connected solid. A boolean returns a
  compound; if it holds no solid the cut consumed the body, and if it holds
  more than one the cut split it. Only a compound holding exactly one solid is
  accepted, and that solid is unwrapped so
  :attr:`LocalCadResult.shape` is always a ``Solid``.

Box semantics (specification Section C.1)
-----------------------------------------
``position`` is the box's **minimum corner** and the box occupies
``[position, position + size]`` on each axis, axis-aligned with no rotation.
``cadquery.Solid.makeBox`` is used with ``pnt=position``, which places the
minimum corner directly -- no centred-box default is involved, so there is no
centring transform to get wrong.

Cylinder semantics (specification Section C.2)
----------------------------------------------
``position`` is the **centre of the base circle**, the radius is
``diameter / 2``, and the cylinder extends ``height`` along the signed
principal direction given by ``axis`` (default ``"+Z"``). The base circle lies
in the plane perpendicular to that axis, so the radial extent is the radius in
each of the two perpendicular axes.

``cadquery.Solid.makeCylinder(radius, height, pnt, dir)`` is used, which builds
``BRepPrimAPI_MakeCylinder`` around ``gp_Ax2(pnt, dir)`` -- ``pnt`` is the base
circle centre and ``dir`` the axis direction, matching the specification
directly with no transform in between.

The sign of the axis is honoured, not normalised away: ``"-Z"`` extends
downward from the base centre. V1 has no arbitrary rotation, so only the six
signed principal directions exist (:data:`AXIS_DIRECTIONS`).

Units
-----
The kernel is unitless; every number handed to it is in the part's declared
unit, which V1 fixes as millimetres. A part declaring anything else is
rejected rather than converted.

Availability
------------
CadQuery is an **optional** dependency (extra ``local-cad``), so importing
:mod:`cad_core` does not require it: the specification, the validator and the
FeatureScript generator stay dependency-free. This module is therefore not
re-exported from the package root -- import it explicitly:

```python
from cad_core.local_cad import build_part
```
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict
from typing import Any, Dict, Mapping, Tuple

from cad_core.model import (
    AXIS_VALUES,
    Box,
    Cylinder,
    Feature,
    Part,
    Position,
    Size,
    ThroughHole,
)

try:
    import cadquery as _cq
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the local CAD engine requires CadQuery, which is an optional "
        "dependency of cad-core. Install it with the 'local-cad' extra, e.g. "
        "`pip install cadquery`."
    ) from exc

#: Name of the geometry backend this module drives.
BACKEND_NAME = "cadquery"

#: Version of that backend, read from the installed package.
BACKEND_VERSION = _cq.__version__

#: The specification's six signed principal directions (Section A.4) as unit
#: vectors. V1 has no arbitrary rotation, so this table is the whole of the
#: orientation vocabulary. The sign is meaningful and is never normalised away.
AXIS_DIRECTIONS: Mapping[str, Tuple[float, float, float]] = {
    "+X": (1.0, 0.0, 0.0),
    "-X": (-1.0, 0.0, 0.0),
    "+Y": (0.0, 1.0, 0.0),
    "-Y": (0.0, -1.0, 0.0),
    "+Z": (0.0, 0.0, 1.0),
    "-Z": (0.0, 0.0, -1.0),
}

#: Unit systems this engine accepts (specification Section A.3).
SUPPORTED_UNITS: Tuple[str, ...] = ("mm",)


class GeometryOperationError(Exception):
    """Raised when a geometric operation fails on otherwise supported input.

    Distinct from :class:`UnsupportedGeometryError`, which reports a part this
    engine does not attempt at all. This one reports a part the engine tried
    and could not build: an unresolvable target, a centreline that misses
    (rule E1), a boolean the kernel refused, an empty result, or a result that
    is not a single connected solid (rule E3).
    """


class UnsupportedGeometryError(Exception):
    """Raised when a part is outside the subset this engine can build.

    Deliberately distinct from
    :class:`cad_core.featurescript.UnsupportedPartError`: the two backends are
    independent execution paths and their supported subsets are free to
    diverge, so neither imports the other's error.
    """


@dataclass(frozen=True)
class BoundingBox:
    """Extents of a built shape, measured by the kernel.

    Expressed with the specification's own :class:`~cad_core.model.Position`
    and :class:`~cad_core.model.Size` types, so callers never need a CadQuery
    type to read a measurement.
    """

    minimum: Position
    maximum: Position

    @property
    def size(self) -> Size:
        return Size(
            x=self.maximum.x - self.minimum.x,
            y=self.maximum.y - self.minimum.y,
            z=self.maximum.z - self.minimum.z,
        )


@dataclass(frozen=True)
class LocalCadResult:
    """One built part: the B-rep shape plus the identity it came from.

    ``shape`` is the kernel object (a ``cadquery.Shape``), exposed because
    later stages -- export, visualisation -- need it. The measurement helpers
    exist so ordinary callers can inspect the result without touching CadQuery
    directly.
    """

    part_name: str
    feature_id: str
    shape: Any

    def is_solid(self) -> bool:
        """True if the kernel reports a topologically valid solid."""
        return shape_is_solid(self.shape)

    def solid_count(self) -> int:
        """Number of solids in the result, as enumerated by the kernel."""
        return shape_solid_count(self.shape)

    def bounding_box(self) -> BoundingBox:
        """Kernel-measured bounding box of the result."""
        return shape_bounding_box(self.shape)

    def volume(self) -> float:
        """Kernel-computed volume, in the cube of the part's declared unit."""
        return shape_volume(self.shape)


# --- kernel measurements -----------------------------------------------------
#
# Module-level so that anything holding a kernel shape can measure it the same
# way -- notably the STEP exporter, which measures a re-imported shape to verify
# a round trip. All four ask OpenCascade; none infers anything from the shape
# merely existing.


def shape_is_solid(shape: Any) -> bool:
    """True if the kernel reports a topologically valid solid.

    Both checks come from OpenCascade: ``ShapeType`` is the shape's real
    topological type, and ``isValid`` runs the kernel's own validity analysis.
    """
    return shape.ShapeType() == "Solid" and bool(shape.isValid())


def shape_solid_count(shape: Any) -> int:
    """Number of solids in a shape, as enumerated by the kernel."""
    return len(shape.Solids())


def shape_bounding_box(shape: Any) -> BoundingBox:
    """Kernel-measured bounding box of a shape."""
    box = shape.BoundingBox()
    return BoundingBox(
        minimum=Position(x=box.xmin, y=box.ymin, z=box.zmin),
        maximum=Position(x=box.xmax, y=box.ymax, z=box.zmax),
    )


def shape_volume(shape: Any) -> float:
    """Kernel-computed volume of a shape."""
    return float(shape.Volume())


def build_part(part: Part) -> LocalCadResult:
    """Build real CAD geometry for a single-box part.

    Args:
        part: A validated :class:`~cad_core.model.Part`, as returned in
            :attr:`~cad_core.errors.ValidationResult.part`. The part is assumed
            to have passed static validation; this function does not re-run
            rules S1-S20 and will not repair or reinterpret it.

    Returns:
        A :class:`LocalCadResult` wrapping the B-rep solid.

    Raises:
        TypeError: if ``part`` is not a :class:`~cad_core.model.Part`. A raw
            specification document is not accepted.
        UnsupportedGeometryError: if the part is outside the supported subset
            -- units other than millimetres, an empty history, a feature type
            this engine does not build, or a history shape it does not
            evaluate. Nothing partial is built.
        GeometryOperationError: if the geometry itself cannot be built -- an
            unresolvable target, a centreline that misses its target (E1), a
            failed boolean, an empty result, or a result that is not a single
            connected solid (E3).
    """
    _require_supported_part(part)

    solids: "Dict[str, Any]" = OrderedDict()
    for feature in part.features:
        if isinstance(feature, Box):
            solids[feature.id] = _build_box(feature)
        elif isinstance(feature, Cylinder):
            solids[feature.id] = _build_cylinder(feature)
        else:
            # A through_hole replaces its target in place, keeping the
            # target's id and its position in the set (Section B.4).
            solids[feature.target] = _apply_through_hole(feature, solids)

    if len(solids) != 1:
        remaining = ", ".join(repr(name) for name in solids)
        raise GeometryOperationError(
            "after the last feature the solid set must contain exactly one "
            f"solid (rule S9); it contains {len(solids)} ({remaining})"
        )

    solid_id, shape = next(iter(solids.items()))
    return LocalCadResult(part_name=part.name, feature_id=solid_id, shape=shape)


def _apply_through_hole(hole: ThroughHole, solids: "Dict[str, Any]") -> Any:
    """Cut ``hole`` through its target and return the replacement solid.

    Implements Section C.3 with a finite cutter derived from the target's own
    bounds, and enforces rules E1 and E3 against the kernel.
    """
    target = solids.get(hole.target)
    if target is None:
        available = ", ".join(repr(name) for name in solids) or "nothing"
        raise GeometryOperationError(
            f"through_hole {hole.id!r} targets {hole.target!r}, which is not a "
            f"solid in the solid set (rule S6); available: {available}"
        )

    if hole.axis not in AXIS_DIRECTIONS:
        permitted = ", ".join(repr(axis) for axis in AXIS_VALUES)
        raise UnsupportedGeometryError(
            f"through_hole {hole.id!r} has unsupported axis {hole.axis!r}; the "
            f"specification permits only {permitted}"
        )
    # The sign is deliberately dropped here: the centreline is an infinite
    # line, so "+Z" and "-Z" name the same one (Section C.3).
    axis_index = "XYZ".index(hole.axis[1])

    low, high, margin = _axial_span(target, axis_index)
    if margin <= 0.0:
        raise GeometryOperationError(
            f"through_hole {hole.id!r}: target {hole.target!r} has a degenerate "
            "bounding box, so no cutting extent can be derived from it"
        )

    centre = [hole.position.x, hole.position.y, hole.position.z]
    centre[axis_index] = low - margin
    direction = [0.0, 0.0, 0.0]
    direction[axis_index] = 1.0
    length = (high - low) + 2.0 * margin

    # E1: the centreline must actually intersect the target.
    far = list(centre)
    far[axis_index] = high + margin
    centreline = _cq.Edge.makeLine(_cq.Vector(*centre), _cq.Vector(*far))
    if not target.intersect(centreline).Edges():
        raise GeometryOperationError(
            f"through_hole {hole.id!r}: its centreline does not intersect "
            f"target {hole.target!r} (rule E1); a hole that misses the material "
            "is an error, not a no-op"
        )

    cutter = _cq.Solid.makeCylinder(
        hole.diameter / 2.0,
        length,
        pnt=_cq.Vector(*centre),
        dir=_cq.Vector(*direction),
    )
    try:
        result = target.cut(cutter)
    except Exception as exc:  # pragma: no cover - kernel refusal is rare
        raise GeometryOperationError(
            f"through_hole {hole.id!r}: the boolean cut of target "
            f"{hole.target!r} failed: {exc}"
        ) from exc

    # E3: exactly one connected solid must remain. A boolean returns a
    # compound, so the single solid is unwrapped for the caller.
    remaining = result.Solids()
    if not remaining:
        raise GeometryOperationError(
            f"through_hole {hole.id!r}: cutting target {hole.target!r} left no "
            "material (rule E2)"
        )
    if len(remaining) > 1:
        raise GeometryOperationError(
            f"through_hole {hole.id!r}: cutting target {hole.target!r} split it "
            f"into {len(remaining)} disconnected solids (rule E3); V1 has no "
            "multi-body parts"
        )
    return remaining[0]


def _axial_span(target: Any, axis_index: int) -> Tuple[float, float, float]:
    """Return the target's extent along one axis, plus a derived margin.

    The margin is the bounding box's diagonal length, so the cutter always
    protrudes past the target by at least its largest dimension. Derived from
    the target rather than fixed, so it scales with the part.
    """
    box = target.BoundingBox()
    low = (box.xmin, box.ymin, box.zmin)[axis_index]
    high = (box.xmax, box.ymax, box.zmax)[axis_index]
    diagonal = (box.xlen**2 + box.ylen**2 + box.zlen**2) ** 0.5
    return low, high, diagonal


def _build_box(box: Box) -> Any:
    """Section C.1: ``position`` is the minimum corner."""
    return _cq.Solid.makeBox(
        box.size.x,
        box.size.y,
        box.size.z,
        pnt=_cq.Vector(box.position.x, box.position.y, box.position.z),
    )


def _build_cylinder(cylinder: Cylinder) -> Any:
    """Section C.2: ``position`` is the base circle centre.

    The radius is ``diameter / 2`` and the solid extends ``height`` along the
    signed principal direction named by ``axis``.
    """
    try:
        direction = AXIS_DIRECTIONS[cylinder.axis]
    except KeyError:
        permitted = ", ".join(repr(axis) for axis in AXIS_VALUES)
        raise UnsupportedGeometryError(
            f"unsupported axis {cylinder.axis!r}; the specification permits "
            f"only {permitted}"
        ) from None
    return _cq.Solid.makeCylinder(
        cylinder.diameter / 2.0,
        cylinder.height,
        pnt=_cq.Vector(
            cylinder.position.x, cylinder.position.y, cylinder.position.z
        ),
        dir=_cq.Vector(*direction),
    )


def _require_supported_part(part: Any) -> None:
    """Accept only a part whose whole history this engine evaluates."""
    if not isinstance(part, Part):
        raise TypeError(
            "build_part requires a typed cad_core.model.Part, such as the "
            "'part' of a successful validate() result; got "
            f"{type(part).__name__}. Raw specification documents are not accepted."
        )

    if part.units not in SUPPORTED_UNITS:
        supported = ", ".join(repr(unit) for unit in SUPPORTED_UNITS)
        raise UnsupportedGeometryError(
            f"unsupported units {part.units!r}; this engine builds {supported} "
            "geometry and does not convert other unit systems"
        )

    if not part.features:
        raise UnsupportedGeometryError(
            "this engine needs at least one feature to build; the history is empty"
        )

    for position, feature in enumerate(part.features):
        if not isinstance(feature, (Box, Cylinder, ThroughHole)):
            raise UnsupportedGeometryError(
                f"unsupported feature type {feature.TYPE!r} at features[{position}]; "
                "this engine builds 'box' and 'cylinder' and applies "
                "'through_hole'. Generic boolean subtraction, fillets and "
                "chamfers are not implemented."
            )

    first = part.features[0]
    if not isinstance(first, (Box, Cylinder)):
        raise UnsupportedGeometryError(
            f"the first feature must be constructive ('box' or 'cylinder'); got "
            f"{first.TYPE!r}"
        )

    constructive = sum(
        1 for feature in part.features if isinstance(feature, (Box, Cylinder))
    )
    if constructive != 1:
        raise UnsupportedGeometryError(
            f"this engine evaluates exactly one constructive feature; got "
            f"{constructive}. Two solids could only be reduced to one by a "
            "'subtract', which is not implemented."
        )
