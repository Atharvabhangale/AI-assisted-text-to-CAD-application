r"""Local deterministic CAD engine: builds real B-rep geometry from a V1 part.

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
A part in millimetres whose feature history begins with a constructive feature
(``box`` or ``cylinder``) and uses the whole V1 feature set: ``box``,
``cylinder``, ``through_hole``, ``subtract``, ``fillet``, ``chamfer`` and --
from schema version ``1.1.0`` -- ``union``; from ``1.2.0`` tapered and
polygonal cylinders and patterned ``subtract``/``union`` tools. Several
constructive features are allowed, provided the extras are consumed as
``subtract`` or ``union`` tools so that exactly one solid is left (rule S9).

Evaluation model (specification Section B.4)
--------------------------------------------
Features are evaluated in order against an ordered **solid set**:

* a constructive feature adds a solid named by its own ``id``;
* a modifier (``through_hole``, ``subtract``, ``fillet``, ``chamfer``,
  ``union``) **replaces its target in place** -- the result keeps the
  *target's* id and the target's position in the set, and the modifier's own
  ``id`` never names a solid;
* a ``subtract`` or a ``union`` additionally **consumes** each solid in
  ``tools``, deleting it from the set so no later feature can use it;
* after the last feature the set must hold exactly one solid, which becomes the
  part's geometry.

So a plate with four holes stays one solid called ``plate`` throughout, and
:attr:`LocalCadResult.feature_id` is the id of that surviving solid.

Subtract semantics (specification Section C.4)
----------------------------------------------
``subtract`` removes each solid in ``tools`` from ``target`` **in list order**
(:func:`_cut_in_order`). Joining is a separate feature, ``union`` (below).
There is no intersection and no boolean expression tree.

Order is honoured, and the two things it affects are kept apart deliberately:

* the **geometry** is order-independent, by set algebra
  (``A \ (B u C) == (A \ B) \ C``) and by measurement -- cutting two tools in
  either order, or both at once, gives the same volume and topology;
* the **acceptance** is not. A tool whose material a previous tool already
  removed is a no-op in one order and not in the other, so the same feature can
  build under one ordering and be refused under the reverse.

Tools are consumed only if the whole feature succeeds. Nothing is written back
to the solid set until every cut has passed its checks, so a failed subtract
leaves no partial geometry and no half-consumed tools behind.

Union semantics (specification Section C.8)
-------------------------------------------
``union`` fuses each solid in ``tools`` into ``target`` **in list order**
(:func:`_fuse_in_order`), replaces the target in place and consumes the tools,
exactly as ``subtract`` does. It is the only way two solids become one: solids
that merely overlap or touch are never merged implicitly.

What the kernel was measured to do (CadQuery 2.8 / OCP 7.9), and what the
engine does with it:

* **Overlapping volumes, or a shared face area** -- one valid solid, with the
  exact set-union volume (overlap counted once). Accepted.
* **Apart, or touching only along an edge, along a tangent line or at a
  point** -- the kernel does not fail and does not produce a non-manifold
  solid. It returns a *valid compound holding two solids*. ``isValid()`` is
  therefore no defence here; the **solid count** is, and anything but exactly
  one solid is rule E3. The error names the target and every tool left
  detached from it, and says what would connect them.
* **The raw fuse keeps the operands' seams**: faces that lie on one plane stay
  split along the line where the operands met (two overlapping boxes fuse to
  14 faces, not 6). Left alone, a later ``fillet`` or ``chamfer`` would select
  those seam edges, which lie between coplanar faces and cannot be blended --
  so "round the edges of the joined part" would fail on edges the part does
  not really have. The result is therefore **unified** before it is returned
  (:func:`_unify_same_domain`, OpenCascade's ``ShapeUpgrade_UnifySameDomain``):
  faces on the same surface and edges on the same curve are merged, volume
  unchanged. The solid a union leaves has the edges of the joined body, not of
  its operands.

E3 is checked once, on what the feature leaves, as it is for ``subtract``. An
intermediate state may therefore be disconnected if a later tool bridges it:
``target`` and ``tools[0]`` may be apart provided ``tools[1]`` joins both.

One case has no rule, mirroring ``subtract``: an operand that contributes no
material -- a tool lying entirely inside the body it is fused into, or a body
lying entirely inside the tool. The fused geometry would simply equal one of
the operands. The engine refuses it without claiming a rule code -- see
:func:`_require_added_material`.

**Measured limit.** The kernel's own coincidence tolerance is about ``1e-7``
mm: two boxes separated by a gap of ``1e-6`` mm stay two solids (E3), while a
gap of ``1e-7`` mm or less is treated as contact and fused. The engine adds no
tolerance of its own on top of that.

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

Geometric rules E1, E2 and E3
-----------------------------
All are checked with the kernel, and a failure raises
:class:`GeometryOperationError` rather than returning a best-effort shape:

* **E1** -- the centreline must actually intersect the target. Tested by
  intersecting a line segment along the centreline with the target solid: if
  the common shape contains no edge, the centreline misses, and that is an
  error, not a silent no-op. A hole that merely grazes the material is caught
  by this too, because the test is on the *centreline*, not on whether any
  material happened to be removed.
* **E2** -- a cut must leave material. Checked after every individual cut,
  because an empty result is absorbing: once nothing is left, no later tool can
  bring anything back, and reporting at the tool that emptied the body is more
  useful than reporting at the end.
* **E3** -- the result must be a single connected solid. Checked once, on what
  the *feature* leaves, which is what Section E.2 states ("every modifier
  leaves a single connected solid"); an intermediate cut inside a multi-tool
  subtract may therefore pass through a split state if a later tool removes the
  extra pieces. Only a result holding exactly one solid is accepted, and that
  solid is unwrapped out of the compound the kernel returns, so
  :attr:`LocalCadResult.shape` is always a ``Solid``. No component is ever
  picked out of a multi-solid result.

A fourth case has no rule: a ``subtract`` tool that removes no material.
Section C.4 lists only E2 and E3 for ``subtract``, so the engine refuses it
without claiming a rule code -- see :func:`_require_overlap`. Its counterpart
for ``union`` is an operand that adds no material
(:func:`_require_added_material`).

Pattern semantics (specification Sections C.4 and C.8, schema 1.2.0)
--------------------------------------------------------------------
A ``subtract`` or ``union`` replaces every tool ``T`` by its copies ``T_k``,
``k = 1 .. pattern_count``: ``T`` turned by ``k * pattern_angle`` degrees about
the line through the origin parallel to ``pattern_axis`` (right-hand rule),
then shifted ``k * pattern_step`` along ``+pattern_axis``
(:func:`_pattern_copies`). The copies are cut or fused tool by tool, and for
each tool in ``k`` order, through the same :func:`_cut_in_order` and
:func:`_fuse_in_order` as unpatterned tools, so E2, E3 and the no-material
refusals apply to every copy unchanged.

With the defaults (one copy, no turn, no shift) the copy **is** the tool --
no transform is applied at all -- so a ``1.1.x`` subtract or union builds the
same shape, bit for bit, as before ``1.2.0``.

Each copy is a real kernel copy (``BRepBuilderAPI_Transform`` with
``copy=True``), not a shared placement of the tool's geometry. The rotation
matrix is written out directly from the turn, reduced to ``[0, 360)``
degrees, with **exact** values for whole quarter turns (``cos 90 = 0``, not
``6e-17``), so a box turned by 90 degrees has edges exactly parallel to the
axes and the last copy of a full circle (``k * angle = 360``) is the tool's own
geometry, not a copy turned by a rounding error. A residual turn within
:data:`PATTERN_TURN_SNAP_DEG` of a whole number of turns is treated as none.

Taper and polygon (specification Section C.2, schema 1.2.0)
-----------------------------------------------------------
A round cylinder whose ``top_diameter`` differs from ``diameter`` is
``cadquery.Solid.makeCone`` (``BRepPrim_Cone``) on the same base centre and
axis: a frustum, or a pointed cone when ``top_diameter`` is 0 -- measured to
have a base circle and one seam and **no** edge at the apex, so no selector
can ever pick the apex.

A polygon (``sides`` 3-12) is built from planar faces: the base and top
regular polygons (corners on the circles of ``diameter`` and
``top_diameter``) and one planar quadrilateral side per edge, sewn into a
shell and closed into a solid (:func:`_build_polygon_solid`). Every face is a
plane, whether or not it tapers -- a ruled loft between the two polygons was
measured to give B-spline side faces, which nothing downstream should have to
reason about. The solid is made along ``+Z`` with a corner on ``+X`` and moved
by an exact rotation (entries 0 and +/-1 only) whose columns are the axis's
first perpendicular, second perpendicular and the axis itself, then shifted
to ``position``.

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
    ROUND_SIDES,
    Box,
    Chamfer,
    Cylinder,
    Feature,
    Fillet,
    Part,
    Position,
    Size,
    Subtract,
    ThroughHole,
    UnionFeature,
)

try:
    import cadquery as _cq
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "the local CAD engine requires CadQuery, which is an optional "
        "dependency of cad-core. Install it with the 'local-cad' extra, e.g. "
        "`pip install cadquery`."
    ) from exc

import math

from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.gp import gp_Trsf
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

from cad_core.edge_selection import select_edges

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

#: The first perpendicular of each axis (Section C.2): where a polygon's
#: first corner points. The second perpendicular is ``axis x first``, so the
#: frame is right-handed and the move from the +Z build frame is a rotation.
POLYGON_CORNER_DIRECTIONS: Mapping[str, Tuple[float, float, float]] = {
    "+Z": (1.0, 0.0, 0.0),
    "-Z": (1.0, 0.0, 0.0),
    "+X": (0.0, 1.0, 0.0),
    "-X": (0.0, 1.0, 0.0),
    "+Y": (0.0, 0.0, 1.0),
    "-Y": (0.0, 0.0, 1.0),
}

#: A residual pattern turn this close to a whole number of turns, in degrees,
#: is no turn at all (see *Pattern semantics*).
PATTERN_TURN_SNAP_DEG = 1e-9

#: Unit systems this engine accepts (specification Section A.3).
SUPPORTED_UNITS: Tuple[str, ...] = ("mm",)


class GeometryOperationError(Exception):
    """Raised when a geometric operation fails on otherwise supported input.

    Distinct from :class:`UnsupportedGeometryError`, which reports a part this
    engine does not attempt at all. This one reports a part the engine tried
    and could not build: an unresolvable target, a centreline that misses
    (rule E1), a boolean the kernel refused, an empty result, or a result that
    is not a single connected solid (rule E3) -- which includes a union whose
    operands do not overlap or share a face.
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
    """Build real CAD geometry for a validated V1 part.

    Args:
        part: A validated :class:`~cad_core.model.Part`, as returned in
            :attr:`~cad_core.errors.ValidationResult.part`. The part is assumed
            to have passed static validation; this function does not re-run
            rules S1-S28 and will not repair or reinterpret it.

    Returns:
        A :class:`LocalCadResult` wrapping the B-rep solid.

    Raises:
        TypeError: if ``part`` is not a :class:`~cad_core.model.Part`. A raw
            specification document is not accepted.
        UnsupportedGeometryError: if the part is outside the supported subset
            -- units other than millimetres, an empty history, a feature type
            this engine does not build, or a history that does not start with
            a constructive feature. Nothing partial is built.
        GeometryOperationError: if the geometry itself cannot be built -- an
            unresolvable target or tool, a centreline that misses its target
            (E1), a failed boolean, a cut that removes everything (E2), a cut
            that splits the body (E3), a union whose operands are not joined
            into one body (E3), a subtract tool that removes nothing, a union
            operand that adds nothing, or more than one solid left after the
            last feature (S9).
    """
    _require_supported_part(part)

    solids: "Dict[str, Any]" = OrderedDict()
    for feature in part.features:
        if isinstance(feature, Box):
            solids[feature.id] = _build_box(feature)
        elif isinstance(feature, Cylinder):
            solids[feature.id] = _build_cylinder(feature)
        elif isinstance(feature, ThroughHole):
            # A modifier replaces its target in place, keeping the target's id
            # and its position in the set (Section B.4).
            solids[feature.target] = _apply_through_hole(feature, solids)
        elif isinstance(feature, Fillet):
            solids[feature.target] = _apply_fillet(feature, solids)
        elif isinstance(feature, Chamfer):
            solids[feature.target] = _apply_chamfer(feature, solids)
        elif isinstance(feature, UnionFeature):
            # A union replaces its target in place and consumes each tool,
            # like a subtract: _apply_union mutates ``solids``.
            _apply_union(feature, solids)
        else:
            # A subtract also replaces its target in place, and additionally
            # consumes each tool: _apply_subtract mutates ``solids``.
            _apply_subtract(feature, solids)

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
    # Rules E2 and E3 are enforced by the shared boolean machinery. A
    # through_hole has exactly one cutter, so "in order" is trivial here; E1
    # above is what a through_hole has instead of a no-op check.
    return _cut_in_order(
        target,
        ((cutter, f"cutting target {hole.target!r}"),),
        label=f"through_hole {hole.id!r}",
        require_material_removal=False,
    )


def _apply_subtract(subtract: Subtract, solids: "Dict[str, Any]") -> None:
    """Apply Section C.4 to ``solids`` in place.

    Removes each tool solid from the target in ``tools`` order, replaces the
    target in place (keeping its id and its position in the set), and consumes
    every tool by deleting it from the set. Nothing is written back unless the
    whole feature succeeds, so a failure leaves no partial geometry behind.
    """
    label = f"subtract {subtract.id!r}"
    target = solids.get(subtract.target)
    if target is None:
        available = ", ".join(repr(name) for name in solids) or "nothing"
        raise GeometryOperationError(
            f"{label} targets {subtract.target!r}, which is not a solid in the "
            f"solid set (rule S6); available: {available}"
        )

    if not subtract.tools:
        raise GeometryOperationError(
            f"{label} has an empty tool list (rule S14); a subtract must remove "
            "at least one solid"
        )

    # Defensive resolution of the tool list. The validator already enforces
    # S6, S14 and S15, so none of these can be reached from a validated part;
    # they exist so that inconsistent typed input is reported rather than
    # producing nonsense geometry or an IndexError.
    steps = []
    seen = []
    for position, tool_id in enumerate(subtract.tools):
        if tool_id == subtract.target:
            raise GeometryOperationError(
                f"{label} lists its own target {tool_id!r} as tools[{position}] "
                "(rule S15); a solid cannot be subtracted from itself"
            )
        if tool_id in seen:
            raise GeometryOperationError(
                f"{label} lists tool {tool_id!r} twice (rule S15); a tool is "
                "consumed by its first use and cannot be reused"
            )
        tool = solids.get(tool_id)
        if tool is None:
            available = ", ".join(repr(name) for name in solids) or "nothing"
            raise GeometryOperationError(
                f"{label} uses tool {tool_id!r} at tools[{position}], which is "
                f"not a solid in the solid set (rule S6); available: {available}"
            )
        seen.append(tool_id)
        for copy, name in _pattern_copies(subtract, tool, tool_id):
            steps.append(
                (
                    copy,
                    f"subtracting tool {name} from target {subtract.target!r}",
                )
            )

    result = _cut_in_order(
        target,
        tuple(steps),
        label=label,
        require_material_removal=True,
    )

    # Commit: the target is replaced in place, so assigning to the existing key
    # keeps both its id and its position in the ordered set (Section B.4).
    solids[subtract.target] = result
    for tool_id in seen:
        del solids[tool_id]


def _apply_union(union: UnionFeature, solids: "Dict[str, Any]") -> None:
    """Apply Section C.8 to ``solids`` in place.

    Fuses each tool solid into the target in ``tools`` order, replaces the
    target in place (keeping its id and its position in the set), and consumes
    every tool by deleting it from the set. Nothing is written back unless the
    whole feature succeeds, so a failure leaves no partial geometry and no
    half-consumed tools behind.
    """
    label = f"union {union.id!r}"
    target = solids.get(union.target)
    if target is None:
        available = ", ".join(repr(name) for name in solids) or "nothing"
        raise GeometryOperationError(
            f"{label} targets {union.target!r}, which is not a solid in the "
            f"solid set (rule S6); available: {available}"
        )

    if not union.tools:
        raise GeometryOperationError(
            f"{label} has an empty tool list (rule S21); a union must join at "
            "least one solid into its target"
        )

    # Defensive resolution of the tool list, as for a subtract. The validator
    # already enforces S6, S21 and S22, so none of these can be reached from a
    # validated part; they exist so that inconsistent typed input is reported
    # rather than producing nonsense geometry.
    steps = []
    seen = []
    for position, tool_id in enumerate(union.tools):
        if tool_id == union.target:
            raise GeometryOperationError(
                f"{label} lists its own target {tool_id!r} as tools[{position}] "
                "(rule S22); a solid cannot be joined to itself"
            )
        if tool_id in seen:
            raise GeometryOperationError(
                f"{label} lists tool {tool_id!r} twice (rule S22); a tool is "
                "consumed by its first use and cannot be reused"
            )
        tool = solids.get(tool_id)
        if tool is None:
            available = ", ".join(repr(name) for name in solids) or "nothing"
            raise GeometryOperationError(
                f"{label} uses tool {tool_id!r} at tools[{position}], which is "
                f"not a solid in the solid set (rule S6); available: {available}"
            )
        seen.append(tool_id)
        for copy, name in _pattern_copies(union, tool, tool_id):
            steps.append((copy, name, position))

    result = _fuse_in_order(
        target, tuple(steps), label=label, target_id=union.target
    )

    # Commit: the target is replaced in place, so assigning to the existing key
    # keeps both its id and its position in the ordered set (Section B.4).
    solids[union.target] = result
    for tool_id in seen:
        del solids[tool_id]


def _fuse_in_order(
    target: Any,
    steps: Tuple[Tuple[Any, str, int], ...],
    *,
    label: str,
    target_id: str,
) -> Any:
    """Fuse each tool into ``target`` in order and enforce rule E3.

    ``steps`` holds ``(tool solid, tool name, index in tools)``, where the
    name is the quoted tool id -- ``'boss'`` -- or, for a copy made by a
    pattern, ``'arm' (copy 2 of 6)``. Returns the
    single joined solid, unified (:func:`_unify_same_domain`) and unwrapped
    out of the compound the kernel returns. Raises
    :class:`GeometryOperationError` on any failure -- never a best-effort
    shape, never a multi-body result and never one component picked out of it.

    E3 is checked once, on what the feature leaves (Section E.2), so an
    intermediate fuse may be disconnected if a later tool bridges it.
    """
    body = target
    for index, (tool, name, position) in enumerate(steps):
        _require_added_material(
            body,
            tool,
            label=label,
            target_id=target_id,
            tool_name=name,
            position=position,
            is_first=index == 0,
        )
        try:
            body = body.fuse(tool)
        except Exception as exc:  # pragma: no cover - kernel refusal is rare
            raise GeometryOperationError(
                f"{label}: the boolean union failed while joining tool "
                f"{name} into target {target_id!r}: {exc}"
            ) from exc

    # E3. Measured: operands that are apart, or that touch only along an edge
    # or at a point, come back as a *valid* compound of several solids, so the
    # kernel's validity analysis cannot be the test -- the solid count is.
    remaining = body.Solids()
    if len(remaining) != 1:
        raise GeometryOperationError(
            _disconnected_union_message(
                target, steps, remaining, label=label, target_id=target_id
            )
        )

    joined = _unify_same_domain(remaining[0], label=label)
    if not (joined.isValid() and joined.Volume() > 0.0):
        raise GeometryOperationError(  # pragma: no cover - not observed
            f"{label}: joining {len(steps)} tool(s) into target {target_id!r} "
            "produced a solid the kernel's own validity analysis rejects; no "
            "geometry is produced"
        )
    return joined


def _disconnected_union_message(
    target: Any,
    steps: Tuple[Tuple[Any, str, int], ...],
    components: Any,
    *,
    label: str,
    target_id: str,
) -> str:
    """Build the rule E3 message for a union that did not make one body.

    Names the tools that ended up detached from the target, by id and by
    position in ``tools``, and says what would connect them -- the message is
    meant to be acted on, by a person or by a language model correcting its
    document.

    A tool belongs with the target when it shares volume with the component
    of the result that contains the target. Each operand is itself one
    connected solid, so it lies wholly inside exactly one component and the
    kernel's common operation decides which.
    """
    home = next(
        (part for part in components if _shares_volume(target, part)), None
    )
    detached = [
        f"{name} (tools[{position}])"
        for tool, name, position in steps
        if home is None or not _shares_volume(tool, home)
    ]
    if len(detached) == 1:
        cause = f"tool {detached[0]} is not connected to target {target_id!r}"
        fix = "Move or resize it so it overlaps or shares a face"
    elif detached:
        cause = (
            "tools " + ", ".join(detached[:-1]) + f" and {detached[-1]} are "
            f"not connected to target {target_id!r}"
        )
        fix = "Move or resize each of them so it overlaps or shares a face"
    else:  # pragma: no cover - a detached component always holds some tool
        cause = f"not every tool is connected to target {target_id!r}"
        fix = "Move or resize the tools so each overlaps or shares a face"
    return (
        f"{label}: {cause}, so the result would be {len(components)} separate "
        "bodies instead of one (rule E3). Solids joined by a union must "
        "overlap or share a face; solids that are apart, or that touch only "
        f"along an edge or at a point, are not joined. {fix} with "
        f"{target_id!r} (or with another tool that does). No geometry is "
        "produced."
    )


def _shares_volume(first: Any, second: Any) -> bool:
    """True if the kernel finds a solid common to both shapes."""
    try:
        return bool(first.intersect(second).Solids())
    except Exception:  # pragma: no cover - kernel refusal is rare
        return False


def _require_added_material(
    body: Any,
    tool: Any,
    *,
    label: str,
    target_id: str,
    tool_name: str,
    position: int,
    is_first: bool,
) -> None:
    """Refuse a fuse in which one operand contributes no material.

    **This case is not classified by the specification**, exactly as its
    counterpart for ``subtract`` is not (:func:`_require_overlap`). Section
    C.8 gives E3 as the only geometric rule for ``union``, and a tool lying
    entirely inside the body it is joined to leaves one connected solid, so
    E3 is satisfied -- but the fused geometry is simply the body, unchanged.
    The same is true the other way round, when the body lies entirely inside
    the tool: the result is the tool alone.

    The engine refuses rather than guessing, and claims no rule code -- nor
    does its message contain one, because :mod:`cad_core.build_job` reads
    rule codes back out of engine messages and would report it as a rule. The
    kernel accepts both silently and returns the larger operand's exact
    volume, so nothing downstream would notice that a requested boss, rib or
    flange is missing from the part -- which is the silent behaviour this
    engine exists to prevent.

    Containment is decided by the kernel, not by comparing volumes against a
    tolerance: ``tool.cut(body)`` yields no solid exactly when the tool adds
    nothing, and ``body.cut(tool)`` yields no solid exactly when the body adds
    nothing. A tool that lies inside the body *and* touches its faces from
    within is measured to be caught too.
    """
    # After the first tool, ``body`` is the target plus the tools already
    # joined, and the message says so rather than blaming the target alone.
    owner = (
        f"target {target_id!r}"
        if is_first
        else f"target {target_id!r} as already joined with the earlier tools"
    )
    try:
        tool_adds = bool(tool.cut(body).Solids())
        body_adds = bool(body.cut(tool).Solids())
    except Exception as exc:  # pragma: no cover - kernel refusal is rare
        raise GeometryOperationError(
            f"{label}: could not test what tool {tool_name} (tools[{position}]) "
            f"adds to target {target_id!r}: {exc}"
        ) from exc
    if not tool_adds:
        raise GeometryOperationError(
            f"{label}: tool {tool_name} (tools[{position}]) lies entirely "
            f"inside {owner}, so joining it would add no material and the "
            "result would be unchanged. The specification does not classify "
            "this case -- its one geometric requirement for 'union' is a "
            "single connected solid, which this would satisfy -- so the "
            "engine refuses it rather than accepting a union that changes "
            f"nothing. Move or resize {tool_name} so part of it lies outside "
            f"{target_id!r}"
        )
    if not body_adds:
        raise GeometryOperationError(
            f"{label}: {owner} lies entirely inside tool {tool_name} "
            f"(tools[{position}]), so the result would be {tool_name} alone "
            f"and {target_id!r} would contribute no material. The "
            "specification does not classify this case -- its one geometric "
            "requirement for 'union' is a single connected solid, which this "
            "would satisfy -- so the engine refuses it rather than accepting "
            "a union that changes nothing. Move or resize "
            f"{target_id!r} or {tool_name} so each has material outside the "
            "other"
        )


def _unify_same_domain(solid: Any, *, label: str) -> Any:
    """Merge faces on one surface and edges on one curve; return the solid.

    A raw fuse keeps the seam where its operands met: two overlapping boxes
    come back with 14 faces, the coplanar ones still split along the operands'
    old edges. Those seam edges are artefacts of *how* the body was described,
    not edges of the body, and a later ``fillet`` or ``chamfer`` would select
    them and fail (an edge between coplanar faces has nothing to blend).

    OpenCascade's ``ShapeUpgrade_UnifySameDomain`` removes them -- the same
    operation as CadQuery's ``Shape.clean()``, driven directly so the result's
    type is checked rather than assumed. Measured: volume is unchanged to the
    last bit on the test geometries, an unrelated cavity elsewhere on the
    target is left untouched, and the result is identical from run to run.
    """
    try:
        upgrader = ShapeUpgrade_UnifySameDomain(solid.wrapped, True, True, True)
        upgrader.AllowInternalEdges(False)
        upgrader.Build()
        unified = _cq.Shape.cast(upgrader.Shape())
    except Exception as exc:  # pragma: no cover - kernel refusal is rare
        raise GeometryOperationError(
            f"{label}: the kernel could not unify the joined solid's faces: "
            f"{type(exc).__name__}"
        ) from exc
    remaining = unified.Solids()
    if len(remaining) != 1:  # pragma: no cover - not observed
        raise GeometryOperationError(
            f"{label}: unifying the joined solid's faces left "
            f"{len(remaining)} solids; no geometry is produced"
        )
    return remaining[0]


def _apply_fillet(fillet: Fillet, solids: "Dict[str, Any]") -> Any:
    """Apply Section C.5 and return the replacement solid.

    Selects edges with the Stage 12 selector layer -- no selector logic is
    duplicated here -- then blends every matched edge with the one constant
    radius. Rules E4 and E5 are enforced against the kernel, and nothing is
    returned unless the whole operation produced a single valid solid.
    """
    label = f"fillet {fillet.id!r}"
    target = solids.get(fillet.target)
    if target is None:
        available = ", ".join(repr(name) for name in solids) or "nothing"
        raise GeometryOperationError(
            f"{label} targets {fillet.target!r}, which is not a solid in the "
            f"solid set (rule S6); available: {available}"
        )

    # Defensive: the validator enforces S16, so this cannot be reached from a
    # validated part. A non-positive radius must not reach the kernel.
    if not fillet.radius > 0.0:
        raise GeometryOperationError(
            f"{label} has radius {fillet.radius!r}, which must be greater than "
            "zero (rule S16)"
        )

    edges = select_edges(target, fillet.edges)

    # E4: the selector must match at least one edge, and the kernel is not
    # called otherwise. The selector layer returns an empty result rather than
    # raising, precisely so this feature can own the rule.
    if not edges:
        raise GeometryOperationError(
            f"{label}: its edge selector matched no edge of target "
            f"{fillet.target!r} (rule E4); a fillet that affects nothing "
            "indicates a misread request, so no geometry is produced"
        )

    return _blend_edges(target, fillet.radius, edges, label=label)


def _apply_chamfer(chamfer: Chamfer, solids: "Dict[str, Any]") -> Any:
    """Apply Section C.6 and return the replacement solid.

    Selects edges with the Stage 12 selector layer -- no selector logic is
    duplicated here -- then bevels every matched edge with the one symmetric
    setback distance. Rules E4 and E5 are enforced against the kernel, and
    nothing is returned unless the whole operation produced a single valid,
    non-empty solid.
    """
    label = f"chamfer {chamfer.id!r}"
    target = solids.get(chamfer.target)
    if target is None:
        available = ", ".join(repr(name) for name in solids) or "nothing"
        raise GeometryOperationError(
            f"{label} targets {chamfer.target!r}, which is not a solid in the "
            f"solid set (rule S6); available: {available}"
        )

    # Defensive: the validator enforces S17, so this cannot be reached from a
    # validated part. A non-positive distance must not reach the kernel.
    if not chamfer.distance > 0.0:
        raise GeometryOperationError(
            f"{label} has distance {chamfer.distance!r}, which must be greater "
            "than zero (rule S17)"
        )

    edges = select_edges(target, chamfer.edges)

    # E4: the selector must match at least one edge, and the kernel is not
    # called otherwise.
    if not edges:
        raise GeometryOperationError(
            f"{label}: its edge selector matched no edge of target "
            f"{chamfer.target!r} (rule E4); a chamfer that affects nothing "
            "indicates a misread request, so no geometry is produced"
        )

    return _bevel_edges(target, chamfer.distance, edges, label=label)


def _bevel_edges(
    target: Any, distance: float, edges: Tuple[Any, ...], *, label: str
) -> Any:
    """Bevel ``edges`` of ``target`` with one symmetric setback.

    Drives OpenCascade's ``BRepFilletAPI_MakeChamfer`` directly, using the
    **two-argument** ``Add(distance, edge)`` overload. That overload is the
    kernel's own symmetric chamfer: it sets back the same distance on both
    adjoining faces, which is exactly Section C.6, and it needs no reference
    face -- so there is no "which side is d1 measured from?" choice to get
    wrong. ``IsSymetric(i)`` is asserted for every contour rather than assumed.

    CadQuery's ``Solid.chamfer`` is not used. It calls the four-argument
    ``Add(d1, d2, edge, face)`` overload with a face picked from an
    edge-to-face ancestor map, and it calls ``builder.Shape()`` without
    checking ``IsDone()`` -- the same unchecked call that, for the fillet
    builder, was measured to corrupt kernel state.

    Every matched edge goes into one builder and is built once, so the bevel
    is a single atomic kernel operation over the whole selection. No subset is
    ever attempted.

    Checks, in order, all from measured kernel behaviour:

    1. **coverage** -- every selected edge must appear in one of the builder's
       contours. ``Add`` silently ignores an edge the kernel considers
       unsuitable (a parameterisation seam, or a tangent edge with no dihedral
       angle), and then reports success for the rest. Refusing is what "do not
       silently skip a selected edge" requires; it is classified as E5,
       because an edge no distance can bevel is not one the distance is
       admissible for.
    2. **E5** -- ``Build()`` raising, or ``IsDone()`` false.
    3. **E3** -- exactly one solid in the result, unwrapped out of the
       compound the builder returns.
    4. **E5 again** -- the result must pass the kernel's validity analysis and
       have a positive volume. A status flag alone is not accepted as
       success.
    """
    builder = BRepFilletAPI_MakeChamfer(target.wrapped)
    for edge in edges:
        # Two-argument overload: symmetric setback, no reference face.
        builder.Add(distance, edge.wrapped)

    uncovered = _uncovered_edges(builder, edges)
    if uncovered:
        raise GeometryOperationError(
            f"{label}: the kernel will not bevel {uncovered} of the "
            f"{len(edges)} selected edge(s) at all (rule E5) -- it treats them "
            "as unsuitable for a chamfer, which is what a parameterisation "
            "seam or a smooth tangent edge is. They are not skipped: the whole "
            "feature fails and no geometry is produced."
        )

    try:
        builder.Build()
    except Exception as exc:
        raise GeometryOperationError(
            f"{label}: the kernel refused to bevel {len(edges)} edge(s) at "
            f"distance {distance} (rule E5): {type(exc).__name__}"
        ) from exc

    if not builder.IsDone():
        # Do NOT ask a not-done builder for its shape: it raises, and the same
        # call on the fillet builder was measured to corrupt kernel state.
        raise GeometryOperationError(
            f"{label}: distance {distance} is not admissible for all "
            f"{len(edges)} selected edge(s) (rule E5); the kernel built "
            f"{builder.NbContours()} contour(s) and could not complete. No "
            "partial bevel is applied."
        )

    result = _cq.Shape.cast(builder.Shape())
    remaining = result.Solids()
    if len(remaining) != 1:
        raise GeometryOperationError(
            f"{label}: bevelling {len(edges)} edge(s) at distance {distance} "
            f"left {len(remaining)} solids (rule E3); V1 has no multi-body "
            "parts"
        )

    solid = remaining[0]
    if not (result.isValid() and solid.isValid()):
        raise GeometryOperationError(
            f"{label}: distance {distance} is not admissible for all "
            f"{len(edges)} selected edge(s) (rule E5); the kernel completed "
            "but its own validity analysis rejects the result. No partial "
            "bevel is applied."
        )
    if not solid.Volume() > 0.0:
        raise GeometryOperationError(
            f"{label}: bevelling at distance {distance} left a solid of "
            f"volume {solid.Volume()!r} (rule E5); an empty result is not a "
            "success"
        )
    return solid


def _dropped_selection(builder: Any, edges: Tuple[Any, ...]) -> Tuple[int, ...]:
    """Positions in ``edges`` that the builder took into no contour.

    Both ``BRepFilletAPI_MakeFillet`` and ``BRepFilletAPI_MakeChamfer``
    expose the same contour tables -- ``NbContours``, ``NbEdges(i)``,
    ``Edge(i, j)`` -- and both are documented to do nothing for an edge that
    does not belong to the shape. Both were also measured to do nothing for an
    edge the kernel considers unsuitable, while still reporting success for the
    rest. This is the single detection used by fillet and chamfer alike.

    Membership is decided by topological identity (``TopoDS_Shape.IsSame``),
    never by coordinates: distinct edges can share endpoints.

    The returned positions are indices into *this call's* selection. They are
    not identifiers of anything: nothing persists them, and the same edge can
    appear at a different position for a different selector.

    Note the reverse can also happen and is *not* an error: contour
    propagation follows tangency, so a contour may contain more edges than
    were selected. The kernel then modifies those neighbours too -- see
    ``docs/local-cad-engine.md``.
    """
    taken = [
        builder.Edge(contour, position)
        for contour in range(1, builder.NbContours() + 1)
        for position in range(1, builder.NbEdges(contour) + 1)
    ]
    return tuple(
        index
        for index, edge in enumerate(edges)
        if not any(edge.wrapped.IsSame(other) for other in taken)
    )


def _uncovered_edges(builder: Any, edges: Tuple[Any, ...]) -> int:
    """Number of selected edges the builder took into no contour."""
    return len(_dropped_selection(builder, edges))


def _describe_selection(
    edges: Tuple[Any, ...], positions: Tuple[int, ...]
) -> Tuple[str, ...]:
    """Describe selected edges for an error message, safely.

    Each descriptor carries the edge's position in this selection, its length
    and its start point -- geometry the caller can act on. No object identity,
    no memory address and no invented edge id appears: there is no persistent
    named topology in V1 and this does not introduce one.
    """
    described = []
    for index in positions:
        edge = edges[index]
        start = edge.startPoint()
        described.append(
            f"selection[{index}] length {edge.Length():.6g} mm from "
            f"({start.x:.6g}, {start.y:.6g}, {start.z:.6g})"
        )
    return tuple(described)


def _blend_edges(
    target: Any, radius: float, edges: Tuple[Any, ...], *, label: str
) -> Any:
    """Round ``edges`` of ``target`` with one constant radius.

    Drives OpenCascade's ``BRepFilletAPI_MakeFillet`` directly rather than
    CadQuery's ``Solid.fillet``, for two measured reasons:

    * ``Solid.fillet`` calls ``builder.Shape()`` without checking
      ``IsDone()``. Calling ``Shape()`` on a builder that is not done raises
      ``StdFail_NotDone`` **and leaves kernel state that segfaults a later
      fillet in the same process** -- reproduced, and the reason this function
      never touches ``Shape()`` unless ``IsDone()`` is true.
    * ``Solid.fillet`` wraps the builder's result as ``Solid(...)`` although
      the kernel returns a ``Compound``, producing a mislabelled object.

    Every matched edge is added to one builder and built once, so the blend is
    a single atomic kernel operation over the whole selection. No subset is
    ever attempted: if the radius does not work for all of them, the feature
    fails.

    Rules enforced, all from measured kernel behaviour:

    * **E5 (coverage)** -- every selected edge must actually be taken into one
      of the builder's contours. ``Add`` accepts an edge the kernel considers
      unsuitable -- a parameterisation seam, or a smooth tangent edge -- and
      then builds no contour for it, afterwards reporting success for the
      rest. Stage 14.1 added this check so that a matched edge is never
      silently skipped; before it, a drilled plate's ``axis_parallel Z``
      fillet quietly ignored the cavity seam.
    * **E5** -- ``Build()`` raising, ``IsDone()`` false, or a result that is
      not a valid solid. The third case is not redundant: for an over-large
      radius the kernel reports done and hands back an invalid shape whose
      volume *exceeds* the original, so ``IsDone()`` alone would accept
      corrupt geometry.
    * **E3** -- the result must hold exactly one solid, which is unwrapped out
      of the compound the builder returns. No component is ever selected from
      a multi-solid result.
    """
    builder = BRepFilletAPI_MakeFillet(target.wrapped)
    for edge in edges:
        builder.Add(radius, edge.wrapped)

    # Coverage is checked before Build, as it is for a chamfer. The contour
    # tables are populated by Add, and were measured to be identical before
    # and after Build for every geometry in the test suite -- including the
    # case where Build raises -- so checking first loses nothing and avoids
    # asking the kernel to attempt a hopeless selection.
    dropped = _dropped_selection(builder, edges)
    if dropped:
        raise GeometryOperationError(
            f"{label}: the kernel accepted {len(edges) - len(dropped)} of the "
            f"{len(edges)} selected edge(s) and did not accept "
            f"{len(dropped)} (rule E5). It treats those as unsuitable for a "
            "blend, which is what a parameterisation seam or a smooth tangent "
            "edge is. They are not skipped: the whole feature fails and no "
            "geometry is produced. Not accepted: "
            + "; ".join(_describe_selection(edges, dropped))
        )

    try:
        builder.Build()
    except Exception as exc:
        raise GeometryOperationError(
            f"{label}: the kernel refused to blend {len(edges)} edge(s) at "
            f"radius {radius} (rule E5): {type(exc).__name__}"
        ) from exc

    if not builder.IsDone():
        # Do NOT ask the builder for its shape here: it raises, and doing so
        # has been measured to corrupt kernel state for later operations.
        raise GeometryOperationError(
            f"{label}: radius {radius} is not admissible for all "
            f"{len(edges)} selected edge(s) (rule E5); the kernel reports "
            f"{builder.NbFaultyContours()} faulty contour(s) and "
            f"{builder.NbFaultyVertices()} faulty vertex/vertices out of "
            f"{builder.NbContours()} contour(s). No partial blend is applied."
        )

    result = _cq.Shape.cast(builder.Shape())
    remaining = result.Solids()
    if len(remaining) != 1:
        raise GeometryOperationError(
            f"{label}: blending {len(edges)} edge(s) at radius {radius} left "
            f"{len(remaining)} solids (rule E3); V1 has no multi-body parts"
        )

    solid = remaining[0]
    if not (result.isValid() and solid.isValid()):
        raise GeometryOperationError(
            f"{label}: radius {radius} is not admissible for all "
            f"{len(edges)} selected edge(s) (rule E5); the kernel completed "
            "but its own validity analysis rejects the result, which is what "
            "consuming neighbouring geometry looks like. No partial blend is "
            "applied."
        )
    return solid


def _cut_in_order(
    target: Any,
    steps: Tuple[Tuple[Any, str], ...],
    *,
    label: str,
    require_material_removal: bool,
) -> Any:
    """Subtract each tool from ``target`` in order and enforce E2 and E3.

    ``steps`` pairs each cutting solid with a description used in error
    messages. One implementation serves both ``through_hole`` and ``subtract``
    so there is only ever one boolean path in this engine.

    ``require_material_removal`` asks for the pre-cut overlap check that
    generic subtraction needs (see :func:`_require_overlap`). A
    ``through_hole`` passes ``False`` because rule E1 has already established
    that its centreline meets the target.

    Returns the single surviving solid, unwrapped out of the compound the
    kernel returns. Raises :class:`GeometryOperationError` on any failure --
    never a best-effort shape.
    """
    for tool, description in steps:
        if require_material_removal:
            _require_overlap(target, tool, label=label, description=description)
        try:
            target = target.cut(tool)
        except Exception as exc:  # pragma: no cover - kernel refusal is rare
            raise GeometryOperationError(
                f"{label}: the boolean cut failed while {description}: {exc}"
            ) from exc
        # E2 is checked per step because an empty result is absorbing: once no
        # material is left, no later cut can bring any back.
        if not target.Solids():
            raise GeometryOperationError(
                f"{label}: {description} left no material (rule E2)"
            )

    # E3 applies to what the modifier leaves (Section E.2), so it is checked
    # once, on the feature's final result, not after each intermediate cut.
    remaining = target.Solids()
    if len(remaining) > 1:
        raise GeometryOperationError(
            f"{label}: the cut split the body into {len(remaining)} "
            "disconnected solids (rule E3); V1 has no multi-body parts"
        )
    # A boolean returns a compound; the single solid is unwrapped so callers
    # always receive a Solid.
    return remaining[0]


def _require_overlap(
    target: Any, tool: Any, *, label: str, description: str
) -> None:
    """Refuse a subtraction whose tool removes no material.

    **This case is not classified by the V1 specification.** E1 says a
    ``through_hole`` that misses is an error and E4 says a fillet or chamfer
    that matches nothing is an error, but the rules listed for ``subtract``
    are only E2 and E3, neither of which covers a tool that removes nothing.

    The engine refuses rather than guessing, which is the narrowest reading
    consistent with the contract, and it does not fabricate a rule code for
    the refusal. Two measured facts support refusing over accepting:

    * the kernel accepts such a cut silently -- a disjoint tool returns the
      target's volume to the last bit, so nothing downstream would notice;
    * a tool that merely *touches* the target removes no volume yet still
      changes its topology (a box gains a seventh face), so accepting no-ops
      would let a tool that means nothing geometrically alter the result.

    Overlap is decided by the kernel: ``Shape.intersect`` (BRepAlgoAPI_Common)
    must yield at least one solid. Touching faces yield none, which is the
    intended answer.
    """
    try:
        common = target.intersect(tool)
    except Exception as exc:  # pragma: no cover - kernel refusal is rare
        raise GeometryOperationError(
            f"{label}: could not test overlap while {description}: {exc}"
        ) from exc
    if not common.Solids():
        raise GeometryOperationError(
            f"{label}: {description} would remove no material -- the two solids "
            "do not overlap. V1 does not classify this case (E2 and E3 are the "
            "only geometric rules given for 'subtract'), so the engine refuses "
            "it rather than accepting a subtraction that means nothing"
        )


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
    """Section C.2: ``position`` is the base centre.

    The base radius is ``diameter / 2``, the far-end radius
    ``top_diameter / 2``, and the solid extends ``height`` along the signed
    principal direction named by ``axis``. Round and untapered is the
    ``1.1.1`` cylinder, built exactly as before; round and tapered is a cone
    frustum (or a cone, when ``top_diameter`` is 0); ``sides`` 3-12 is a
    regular polygon prism or pyramid frustum.
    """
    try:
        direction = AXIS_DIRECTIONS[cylinder.axis]
    except KeyError:
        permitted = ", ".join(repr(axis) for axis in AXIS_VALUES)
        raise UnsupportedGeometryError(
            f"unsupported axis {cylinder.axis!r}; the specification permits "
            f"only {permitted}"
        ) from None
    base = _cq.Vector(cylinder.position.x, cylinder.position.y, cylinder.position.z)
    top_diameter = (
        cylinder.diameter if cylinder.top_diameter is None else cylinder.top_diameter
    )
    if cylinder.sides != ROUND_SIDES:
        return _build_polygon_solid(cylinder, top_diameter)
    if top_diameter == cylinder.diameter:
        return _cq.Solid.makeCylinder(
            cylinder.diameter / 2.0,
            cylinder.height,
            pnt=base,
            dir=_cq.Vector(*direction),
        )
    return _cq.Solid.makeCone(
        cylinder.diameter / 2.0,
        top_diameter / 2.0,
        cylinder.height,
        pnt=base,
        dir=_cq.Vector(*direction),
    )


def _build_polygon_solid(cylinder: Cylinder, top_diameter: float) -> Any:
    """A regular ``sides``-gon prism or pyramid frustum, from planar faces.

    Built along ``+Z`` from the origin with the first corner on ``+X``, then
    moved by an exact rotation onto ``axis`` -- first corner on the axis's
    first perpendicular (:data:`POLYGON_CORNER_DIRECTIONS`) -- and shifted to
    ``position``. A regular polygon with a corner on a line is symmetric
    about that line, so the second perpendicular's sign cannot matter.
    """
    sides = cylinder.sides
    if not 3 <= sides <= 12:
        raise UnsupportedGeometryError(
            f"cylinder {cylinder.id!r} has sides={sides!r}; the specification "
            "permits 0 (round) or 3 to 12 (rule S24)"
        )
    if not top_diameter > 0.0:
        raise UnsupportedGeometryError(
            f"cylinder {cylinder.id!r}: a polygon needs top_diameter > 0 "
            "(rule S23); there is no pointed pyramid"
        )

    def ring(radius: float, z: float) -> list:
        corners = []
        for index in range(sides):
            theta = 2.0 * math.pi * index / sides
            corners.append(
                _cq.Vector(radius * math.cos(theta), radius * math.sin(theta), z)
            )
        return corners

    bottom = ring(cylinder.diameter / 2.0, 0.0)
    top = ring(top_diameter / 2.0, cylinder.height)
    faces = [
        _cq.Face.makeFromWires(_cq.Wire.makePolygon(bottom, close=True)),
        _cq.Face.makeFromWires(_cq.Wire.makePolygon(top, close=True)),
    ]
    for index in range(sides):
        following = (index + 1) % sides
        faces.append(
            _cq.Face.makeFromWires(
                _cq.Wire.makePolygon(
                    [bottom[index], bottom[following], top[following], top[index]],
                    close=True,
                )
            )
        )
    try:
        local = _cq.Solid.makeSolid(_cq.Shell.makeShell(faces))
    except Exception as exc:  # pragma: no cover - not observed
        raise GeometryOperationError(
            f"cylinder {cylinder.id!r}: the kernel could not close the "
            f"{sides}-sided polygon solid: {type(exc).__name__}"
        ) from exc

    w = AXIS_DIRECTIONS[cylinder.axis]
    u = POLYGON_CORNER_DIRECTIONS[cylinder.axis]
    v = (  # w x u: a right-handed frame, so the move is a pure rotation
        w[1] * u[2] - w[2] * u[1],
        w[2] * u[0] - w[0] * u[2],
        w[0] * u[1] - w[1] * u[0],
    )
    p = cylinder.position
    trsf = gp_Trsf()
    trsf.SetValues(
        u[0], v[0], w[0], p.x,
        u[1], v[1], w[1], p.y,
        u[2], v[2], w[2], p.z,
    )
    solid = _transformed_copy(local, trsf)
    if not (solid.isValid() and solid.Volume() > 0.0):  # pragma: no cover
        raise GeometryOperationError(
            f"cylinder {cylinder.id!r}: the {sides}-sided polygon solid is not "
            "a valid solid; no geometry is produced"
        )
    return solid


def _transformed_copy(shape: Any, trsf: Any) -> Any:
    """A real copy of ``shape`` moved by ``trsf`` (geometry copied, not shared)."""
    return _cq.Shape.cast(
        BRepBuilderAPI_Transform(shape.wrapped, trsf, True).Shape()
    )


def _pattern_copies(
    feature: Any, tool: Any, tool_id: str
) -> Tuple[Tuple[Any, str], ...]:
    """The copies ``T_1 .. T_n`` of one tool, with a display name for each.

    Section C.4/C.8: ``T_k`` is ``tool`` turned by ``k * pattern_angle``
    degrees about the line through the origin parallel to ``pattern_axis``,
    then shifted ``k * pattern_step`` along ``+pattern_axis``. With the
    defaults the only copy is ``tool`` itself, untransformed, and is named as
    the tool always was, so every pre-1.2.0 message is unchanged.
    """
    count = feature.pattern_count
    angle = feature.pattern_angle
    step = feature.pattern_step
    axis = feature.pattern_axis
    if count == 1 and angle == 0.0 and step == 0.0:
        return ((tool, repr(tool_id)),)
    if axis not in ("X", "Y", "Z"):
        raise UnsupportedGeometryError(
            f"{feature.TYPE} {feature.id!r} has pattern_axis {axis!r}; the "
            "specification permits 'X', 'Y' or 'Z' (rule S26)"
        )
    copies = []
    for k in range(1, count + 1):
        trsf = _pattern_transform(axis, k * angle, k * step)
        copy = tool if trsf is None else _transformed_copy(tool, trsf)
        if count == 1:
            name = (
                f"{tool_id!r} (turned {angle:g} degrees about {axis} and "
                f"shifted {step:g} along +{axis} by the pattern)"
            )
        else:
            name = f"{tool_id!r} (copy {k} of {count})"
        copies.append((copy, name))
    return tuple(copies)


def _pattern_transform(axis: str, degrees: float, shift: float) -> Any:
    """The turn-then-shift of one pattern copy, or None for the identity.

    The turn is reduced to ``[0, 360)`` and whole quarter turns use exact
    cosines and sines, so 90, 180 and 270 degrees introduce no rounding and
    a whole turn is no turn (see *Pattern semantics*). The shift is along the
    pattern axis, which a turn about that axis leaves where it is, so the
    order of the two does not matter.
    """
    turn = math.fmod(degrees, 360.0)
    if turn < 0.0:
        turn += 360.0
    if turn < PATTERN_TURN_SNAP_DEG or 360.0 - turn < PATTERN_TURN_SNAP_DEG:
        turn = 0.0
    quarter_turns = {
        0.0: (1.0, 0.0),
        90.0: (0.0, 1.0),
        180.0: (-1.0, 0.0),
        270.0: (0.0, -1.0),
    }
    if turn in quarter_turns:
        c, s = quarter_turns[turn]
    else:
        c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    if turn == 0.0 and shift == 0.0:
        return None
    # Right-hand rule: positive turns are counter-clockwise seen from the
    # positive end of the axis -- x->y about Z, y->z about X, z->x about Y.
    if axis == "Z":
        rows = ((c, -s, 0.0, 0.0), (s, c, 0.0, 0.0), (0.0, 0.0, 1.0, shift))
    elif axis == "X":
        rows = ((1.0, 0.0, 0.0, shift), (0.0, c, -s, 0.0), (0.0, s, c, 0.0))
    else:
        rows = ((c, 0.0, s, 0.0), (0.0, 1.0, 0.0, shift), (-s, 0.0, c, 0.0))
    trsf = gp_Trsf()
    trsf.SetValues(*rows[0], *rows[1], *rows[2])
    return trsf


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

    supported = (Box, Cylinder, ThroughHole, Subtract, Fillet, Chamfer, UnionFeature)
    for position, feature in enumerate(part.features):
        if not isinstance(feature, supported):
            raise UnsupportedGeometryError(
                f"unsupported feature type {feature.TYPE!r} at features[{position}]; "
                "this engine builds 'box' and 'cylinder' and applies "
                "'through_hole', 'subtract', 'fillet', 'chamfer' and 'union' "
                "-- the whole V1 feature set"
            )

    first = part.features[0]
    if not isinstance(first, (Box, Cylinder)):
        raise UnsupportedGeometryError(
            f"the first feature must be constructive ('box' or 'cylinder'); got "
            f"{first.TYPE!r}"
        )

    # The number of constructive features is deliberately *not* restricted
    # here. Several are legitimate as long as every extra solid is consumed by
    # a 'subtract' or a 'union' (Section B.4); a leftover solid is caught by the rule S9
    # check after the last feature, which is where the specification puts it.
