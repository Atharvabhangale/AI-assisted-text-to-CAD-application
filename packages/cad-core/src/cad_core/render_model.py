"""Neutral render representation: visualization geometry as plain Python data.

A future browser viewer or HTTP API must not receive CadQuery or OpenCascade
objects. They are not serializable, they carry a heavy native dependency, and
they are an implementation detail of one execution backend. This module is the
seam where kernel geometry becomes an application-level data contract:

```
CAD specification -> local CAD engine -> B-rep -> render representation -> future browser / API
```

Everything a :class:`RenderModel` exposes is a dataclass, tuple, float, int or
string. No ``cadquery.Shape``, no ``TopoDS_*``, no ``Workplane``, no
OpenCascade handle survives the boundary -- a test asserts that by walking the
model recursively.

Two separate contracts
----------------------
``docs/cad-specification.md`` describes *engineering* geometry: features,
parameters, design intent. This module describes *visualization* geometry:
triangles for a renderer. They are deliberately separate, and neither knows
about the other -- no mesh concept appears in the specification, and this model
contains no feature history.

The geometry is authoritative-by-derivation
-------------------------------------------
A render model is built by tessellating the **B-rep solid** the local CAD
engine produced. It is never derived from the specification directly, from a
bounding box, from a hand-built mesh, or from an STL file. The B-rep is the
authority; this is a view of it.

What it preserves, and what it does not
---------------------------------------
Measured on the reference box, not assumed:

* **Vertices are not shared across faces.** CadQuery tessellates face by face
  and concatenates each face's nodes, so a box yields 24 vertices, not 8. This
  is what a renderer wants: shared vertices would average normals across an
  edge and round off what should be a crease.
* **Triangles are indexed** into that vertex array.
* **Normals are per-vertex**, and are *computed*, not kernel-supplied --
  OpenCascade reports ``HasNormals() == False`` after meshing. Each vertex
  normal is the normalised sum of the geometric normals of the triangles that
  reference it. Within a planar face that is exactly the face normal; within a
  curved face it smooths across the tessellation; across faces it stays sharp,
  because vertices are not shared.
* **Winding is counter-clockwise seen from outside**, so the right-hand rule
  gives an outward normal. CadQuery reverses the index order for faces whose
  ``TopAbs_Orientation`` is ``REVERSED``, which is what makes this consistent;
  verified by checking all 12 triangles of the reference box point away from
  its centre.
* **Face and feature identity are discarded.** There is no face id, no feature
  id per triangle, no CAD topology. This is a visualization representation and
  claims nothing about preserving topology.

Tessellation settings
---------------------
The linear and angular deflections are the same values the STL exporter uses
(0.01 mm and 0.1 rad), so the two outputs describe the same approximation. One
difference was found by reading the source and is documented rather than
papered over: ``Shape.mesh`` -- which ``tessellate`` calls -- passes
``relative=True`` to ``BRepMesh_IncrementalMesh``, whereas
:mod:`cad_core.stl_export` passes ``relative=False``. For the current
all-planar geometry this is immaterial (both produce 12 triangles), and
``tessellate`` is used regardless because it implements the per-face winding
correction, which is not worth reimplementing. It will need revisiting when
curved geometry exists.

A related subtlety: ``Shape.mesh`` reuses an existing triangulation if one is
already attached at the requested tolerance, so exporting STL before building a
render model may cause the render model to inherit that mesh. Immaterial for
planar geometry; noted so it is not a surprise later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from cad_core.local_cad import SUPPORTED_UNITS, LocalCadResult

#: Version of this render contract, independent of the CAD specification's
#: schema version. Bump it when the emitted structure changes meaning.
RENDER_FORMAT_VERSION = "1.0.0"

#: Linear deflection in millimetres -- the same value the STL exporter uses.
DEFAULT_LINEAR_DEFLECTION_MM = 0.01

#: Angular deflection in radians -- the same value the STL exporter uses.
DEFAULT_ANGULAR_DEFLECTION_RAD = 0.1

#: Unit of the emitted coordinates. The local CAD engine builds millimetre
#: parts only, so any LocalCadResult that exists is in millimetres; this is
#: read from that constraint rather than assumed.
RENDER_UNITS = SUPPORTED_UNITS[0]

#: The specification's part coordinate system (Section A.1): right-handed,
#: +Z up. Stated so a viewer does not have to guess an up-axis.
COORDINATE_SYSTEM = "right_handed_z_up"

#: Triangle winding: indices run counter-clockwise seen from outside the solid,
#: so the right-hand rule yields an outward-facing normal.
WINDING = "counter_clockwise_outward"

#: Normals are supplied once per vertex, parallel to ``vertices``.
NORMAL_BINDING = "per_vertex"

Vector3 = Tuple[float, float, float]
Triangle = Tuple[int, int, int]


class RenderModelError(Exception):
    """Raised when a render model cannot be built from a result."""


@dataclass(frozen=True)
class TessellationSettings:
    """The deflection settings a render model was generated with."""

    linear_deflection_mm: float
    angular_deflection_rad: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "linear_deflection_mm": self.linear_deflection_mm,
            "angular_deflection_rad": self.angular_deflection_rad,
        }


@dataclass(frozen=True)
class RenderBounds:
    """Axis-aligned bounds, measured from the render vertices themselves.

    Deliberately *not* copied from the B-rep: a tessellation can only lie on or
    inside the true surface, so a render model's bounds are a property of the
    triangles it actually contains. The B-rep's own measurement stays available
    from :meth:`cad_core.local_cad.LocalCadResult.bounding_box`.
    """

    minimum: Vector3
    maximum: Vector3

    @property
    def size(self) -> Vector3:
        return (
            self.maximum[0] - self.minimum[0],
            self.maximum[1] - self.minimum[1],
            self.maximum[2] - self.minimum[2],
        )

    def to_dict(self) -> Dict[str, List[float]]:
        return {
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "size": list(self.size),
        }


@dataclass(frozen=True)
class RenderModel:
    """Visualization geometry for one built part, as plain Python data."""

    format_version: str
    part_name: str
    feature_id: str
    units: str
    coordinate_system: str
    winding: str
    normal_binding: str
    vertices: Tuple[Vector3, ...]
    triangles: Tuple[Triangle, ...]
    normals: Tuple[Vector3, ...]
    bounds: RenderBounds
    tessellation: TessellationSettings

    def vertex_count(self) -> int:
        return len(self.vertices)

    def triangle_count(self) -> int:
        return len(self.triangles)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible structure.

        Only dicts, lists, floats, ints and strings -- ``json.dumps`` accepts
        the result directly, with no custom encoder.
        """
        return {
            "format_version": self.format_version,
            "part_name": self.part_name,
            "feature_id": self.feature_id,
            "units": self.units,
            "coordinate_system": self.coordinate_system,
            "winding": self.winding,
            "normal_binding": self.normal_binding,
            "vertices": [list(vertex) for vertex in self.vertices],
            "triangles": [list(triangle) for triangle in self.triangles],
            "normals": [list(normal) for normal in self.normals],
            "bounds": self.bounds.to_dict(),
            "tessellation": self.tessellation.to_dict(),
        }


def build_render_model(
    result: LocalCadResult,
    *,
    tolerance: float = DEFAULT_LINEAR_DEFLECTION_MM,
    angular_tolerance: float = DEFAULT_ANGULAR_DEFLECTION_RAD,
) -> RenderModel:
    """Tessellate a built part into a neutral render representation.

    Args:
        result: A :class:`~cad_core.local_cad.LocalCadResult` from
            :func:`~cad_core.local_cad.build_part`. Raw dictionaries, bare
            kernel shapes and loose mesh data are not accepted.
        tolerance: Linear deflection in millimetres. Must be positive.
        angular_tolerance: Angular deflection in radians. Must be positive.

    Returns:
        A :class:`RenderModel` containing only plain Python data.

    Raises:
        TypeError: if ``result`` is not a ``LocalCadResult``.
        ValueError: if either tolerance is not positive.
        RenderModelError: if tessellation produced no triangles, or produced
            an index outside the vertex array.
    """
    if not isinstance(result, LocalCadResult):
        raise TypeError(
            "build_render_model requires a cad_core.local_cad.LocalCadResult, "
            f"as returned by build_part(); got {type(result).__name__}."
        )
    if not tolerance > 0:
        raise ValueError(f"tolerance must be positive, got {tolerance!r}")
    if not angular_tolerance > 0:
        raise ValueError(
            f"angular_tolerance must be positive, got {angular_tolerance!r}"
        )

    raw_vertices, raw_triangles = result.shape.tessellate(tolerance, angular_tolerance)
    vertices: Tuple[Vector3, ...] = tuple(
        (float(vertex.x), float(vertex.y), float(vertex.z)) for vertex in raw_vertices
    )
    triangles: Tuple[Triangle, ...] = tuple(
        (int(a), int(b), int(c)) for a, b, c in raw_triangles
    )

    if not triangles:
        raise RenderModelError(
            f"tessellating {result.part_name!r} produced no triangles"
        )
    _require_valid_indices(vertices, triangles)

    return RenderModel(
        format_version=RENDER_FORMAT_VERSION,
        part_name=result.part_name,
        feature_id=result.feature_id,
        units=RENDER_UNITS,
        coordinate_system=COORDINATE_SYSTEM,
        winding=WINDING,
        normal_binding=NORMAL_BINDING,
        vertices=vertices,
        triangles=triangles,
        normals=_vertex_normals(vertices, triangles),
        bounds=_bounds_of(vertices),
        tessellation=TessellationSettings(
            linear_deflection_mm=float(tolerance),
            angular_deflection_rad=float(angular_tolerance),
        ),
    )


def _require_valid_indices(
    vertices: Sequence[Vector3], triangles: Sequence[Triangle]
) -> None:
    """Every triangle index must address a vertex that exists."""
    limit = len(vertices)
    for position, triangle in enumerate(triangles):
        for index in triangle:
            if not 0 <= index < limit:
                raise RenderModelError(
                    f"triangle {position} references vertex index {index}, "
                    f"outside the {limit}-vertex array"
                )


def _bounds_of(vertices: Sequence[Vector3]) -> RenderBounds:
    """Bounds of the render vertices, not of the B-rep."""
    xs, ys, zs = zip(*vertices)
    return RenderBounds(
        minimum=(min(xs), min(ys), min(zs)),
        maximum=(max(xs), max(ys), max(zs)),
    )


def _triangle_normal(
    a: Vector3, b: Vector3, c: Vector3
) -> Tuple[float, float, float]:
    """Geometric normal of one triangle, by the right-hand rule.

    Returns the unnormalised cross product, whose magnitude is twice the
    triangle's area -- so summing these weights each face by its area, and a
    degenerate triangle contributes nothing.
    """
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    return (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)


def _vertex_normals(
    vertices: Sequence[Vector3], triangles: Sequence[Triangle]
) -> Tuple[Vector3, ...]:
    """Per-vertex normals, computed from triangle geometry.

    OpenCascade does not store node normals after meshing
    (``Poly_Triangulation.HasNormals()`` is False), so these are derived: each
    vertex accumulates the area-weighted normals of the triangles referencing
    it, and the sum is normalised.

    A vertex referenced only by degenerate triangles ends up with a zero
    normal. That is reported as ``(0.0, 0.0, 0.0)`` rather than replaced with a
    fabricated direction.
    """
    sums: List[List[float]] = [[0.0, 0.0, 0.0] for _ in vertices]
    for a, b, c in triangles:
        nx, ny, nz = _triangle_normal(vertices[a], vertices[b], vertices[c])
        for index in (a, b, c):
            sums[index][0] += nx
            sums[index][1] += ny
            sums[index][2] += nz

    normals: List[Vector3] = []
    for nx, ny, nz in sums:
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length == 0.0:
            normals.append((0.0, 0.0, 0.0))
        else:
            normals.append((nx / length, ny / length, nz / length))
    return tuple(normals)
