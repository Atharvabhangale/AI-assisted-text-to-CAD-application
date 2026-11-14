"""Geometric comparison of two built solids, measured by the kernel.

Two documents can describe the same solid with different feature lists, and
two documents that differ by one swapped number can describe different solids
of identical volume. Comparing documents field by field cannot tell those
cases apart; comparing the **solids** can. This module does that, and nothing
else:

* :func:`shape_facts` measures one solid -- volume, area, bounding box, solid
  and shell counts, faces by surface type, its cylindrical surfaces (grouped by
  axis line and radius, each marked concave or convex and full-turn or
  partial) and its genus (the number of through-holes);
* :func:`compare_shapes` compares a *candidate* solid with a *reference*
  solid: volume ratio, extents, placement offset and the **intersection over
  union** (IoU) of the two after aligning their bounding-box minimum corners,
  for one or more orientation modes;
* :func:`run_comparison_request` is the plain-data entry point the isolated
  worker calls (operation ``compare_shapes``): CAD documents in, plain data
  out. Kernel objects never leave this module or that process.

Orientation modes
-----------------
``fixed``
    The candidate is compared as built. Use when the request fixes which way
    the part points (axis letters, "long/wide/thick", "standing on its base").
``upright``
    The best of the four turns about +Z (0, 90, 180, 270 degrees). Use when
    "up" is fixed but the heading in the XY plane is not.
``upright_any``
    "Up" is fixed and the heading may be **any** angle: the four quarter
    turns, plus every turn about Z that carries one of the candidate's
    vertical round features (bores and bosses off the centre) onto one of the
    reference's. Use for patterned round parts -- a bolt circle's angular
    phase is rarely stated, and 8 holes at 22.5 degrees are no quarter turn
    from 8 holes at 0.
``free``
    The best of the 24 proper axis-aligned rotations (signed permutation
    matrices with determinant +1; mirror images are **not** included, because
    a mirrored part is a different part). Use when the request fixes no
    orientation at all.

Every rotation is a signed permutation of the axes, so rotated coordinates
are exact in floating point and the rotated bounding box is the permuted
bounding box -- no tessellation, no sampling. The free search evaluates
rotations in order of an exact **upper bound** on their IoU (computed from the
volumes and the overlap of the aligned bounding boxes) and stops as soon as
the best IoU found reaches the next bound, so the answer is the true best over
all 24 and the search is deterministic.

What this module does not do
----------------------------
It decides nothing. It reports measurements; the thresholds that turn them
into a verdict belong to whoever asks. It never repairs, re-orients or
re-builds a candidate beyond the comparison itself, and it never reads a
document that has not passed :func:`cad_core.validator.validate`.

Determinism and tolerances
--------------------------
Booleans and mass properties are OpenCascade's, run single-threaded, so the
same two documents give the same numbers. Every comparison of kernel values
uses an explicit tolerance from this module (``*_TOLERANCE*``), never exact
float equality.

The kernel (CadQuery/OpenCascade) is imported **inside** the functions that
need it, so the plain-data types here import without it.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Version of the comparison's output shape. Bumped when a field changes
#: meaning, so a stored comparison is never read against another definition.
SHAPE_COMPARISON_VERSION = "1.0.0"

ORIENTATION_FIXED = "fixed"
ORIENTATION_UPRIGHT = "upright"
ORIENTATION_FREE = "free"
ORIENTATION_UPRIGHT_ANY = "upright_any"

#: Orientation modes.
ORIENTATION_MODES: Tuple[str, ...] = (
    ORIENTATION_FIXED,
    ORIENTATION_UPRIGHT,
    ORIENTATION_UPRIGHT_ANY,
    ORIENTATION_FREE,
)

#: ``upright_any`` tries at most this many feature-aligned turns about Z.
MAX_ALIGNING_TURNS = 64

#: Two aligning turns closer than this (degrees) are the same turn.
TURN_TOLERANCE_DEG = 1e-7

#: Two axis lines closer than this (mm) are the same line.
AXIS_LINE_TOLERANCE_MM = 1e-4

#: Two cylindrical radii closer than this (mm) are the same radius.
RADIUS_TOLERANCE_MM = 1e-4

#: A direction component within this of 1 is a principal axis.
DIRECTION_TOLERANCE = 1e-9

#: A cylindrical surface whose faces cover at least a full turn minus this
#: (radians) is a full turn: a bore or a round boss rather than a fillet.
FULL_TURN_TOLERANCE_RAD = 1e-3

#: An Euler-Poincare genus within this of an integer is that integer;
#: anything further away is reported as unknown (``None``), never rounded.
GENUS_TOLERANCE = 1e-9

#: Slack (mm^3) allowed when an intersection volume comes back marginally
#: larger than the smaller operand, as boolean tolerances can make it.
VOLUME_SLACK_MM3 = 1e-6

#: The bound search stops when the best IoU is within this of the next bound.
BOUND_TOLERANCE = 1e-12

_AXIS_NAMES = ("X", "Y", "Z")


class ShapeComparisonError(Exception):
    """The comparison itself could not be carried out (a kernel refusal).

    Distinct from a candidate that fails to build: that is a fact about the
    candidate, reported in a :class:`PairResult`, not an error here.
    """


class ComparisonRequestError(ValueError):
    """A ``compare_shapes`` request is malformed. Raised before any build."""


# --- rotations ---------------------------------------------------------------


@dataclass(frozen=True)
class AxisRotation:
    """A proper rotation that maps axes onto axes.

    Row ``i`` of the matrix has ``signs[i]`` in column ``source[i]``: the
    rotated coordinate ``i`` is ``signs[i] * original[source[i]]``. ``name``
    spells the rows, so ``"+X+Y+Z"`` is the identity and ``"-Y+X+Z"`` turns
    90 degrees about +Z.
    """

    source: Tuple[int, int, int]
    signs: Tuple[int, int, int]

    @property
    def name(self) -> str:
        return "".join(
            ("+" if sign > 0 else "-") + _AXIS_NAMES[axis]
            for axis, sign in zip(self.source, self.signs)
        )

    @property
    def upright(self) -> bool:
        """Whether +Z stays +Z (a turn about the vertical axis only)."""
        return self.source[2] == 2 and self.signs[2] > 0

    def apply_extents(self, extents: Sequence[float]) -> Tuple[float, float, float]:
        return tuple(extents[self.source[i]] for i in range(3))  # type: ignore[return-value]

    def apply_box(
        self, minimum: Sequence[float], maximum: Sequence[float]
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """The rotated box's minimum and maximum corners, exactly."""
        low: List[float] = []
        high: List[float] = []
        for i in range(3):
            axis, sign = self.source[i], self.signs[i]
            if sign > 0:
                low.append(minimum[axis])
                high.append(maximum[axis])
            else:
                low.append(-maximum[axis])
                high.append(-minimum[axis])
        return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def _determinant(source: Sequence[int], signs: Sequence[int]) -> int:
    matrix = [[0, 0, 0] for _ in range(3)]
    for row in range(3):
        matrix[row][source[row]] = signs[row]
    a, b, c = matrix
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def _all_rotations() -> Tuple[AxisRotation, ...]:
    found: List[AxisRotation] = []
    for source in itertools.permutations((0, 1, 2)):
        for signs in itertools.product((1, -1), repeat=3):
            if _determinant(source, signs) == 1:
                found.append(AxisRotation(source=tuple(source), signs=tuple(signs)))  # type: ignore[arg-type]
    return tuple(found)


#: The 24 proper axis-aligned rotations, identity first, in a fixed order.
ROTATIONS: Tuple[AxisRotation, ...] = _all_rotations()

#: The identity rotation.
IDENTITY: AxisRotation = ROTATIONS[0]


def rotations_for(orientation: str) -> Tuple[AxisRotation, ...]:
    """The rotations an orientation mode searches."""
    if orientation == ORIENTATION_FIXED:
        return (IDENTITY,)
    if orientation in (ORIENTATION_UPRIGHT, ORIENTATION_UPRIGHT_ANY):
        return tuple(rotation for rotation in ROTATIONS if rotation.upright)
    if orientation == ORIENTATION_FREE:
        return ROTATIONS
    raise ComparisonRequestError(
        f"unknown orientation {orientation!r}; expected one of "
        + ", ".join(ORIENTATION_MODES)
    )


# --- plain-data results ------------------------------------------------------


@dataclass(frozen=True)
class CylindricalSurface:
    """Cylindrical faces of a solid on one axis line, with one radius, that
    form one contiguous stretch of that line (a gap along the axis starts
    another surface).

    ``concave`` means the surface faces its own axis -- the wall of a hole or
    pocket. ``full_turn`` means the faces cover a whole revolution, which
    separates a bore or a round boss from a fillet (a quarter turn).
    """

    diameter: float
    axis: str  # "X", "Y", "Z" or "oblique"
    direction: Tuple[float, float, float]
    #: The point on the axis line closest to the origin.
    location: Tuple[float, float, float]
    concave: bool
    full_turn: bool
    #: Angular coverage in degrees, capped at 360.
    coverage_degrees: float
    #: Length of axis covered by the faces, in mm (overlaps counted once).
    length: float
    face_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "diameter": self.diameter,
            "axis": self.axis,
            "direction": list(self.direction),
            "location": list(self.location),
            "concave": self.concave,
            "full_turn": self.full_turn,
            "coverage_degrees": self.coverage_degrees,
            "length": self.length,
            "face_count": self.face_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CylindricalSurface":
        return cls(
            diameter=float(raw["diameter"]),
            axis=str(raw["axis"]),
            direction=tuple(float(v) for v in raw["direction"]),  # type: ignore[arg-type]
            location=tuple(float(v) for v in raw["location"]),  # type: ignore[arg-type]
            concave=bool(raw["concave"]),
            full_turn=bool(raw["full_turn"]),
            coverage_degrees=float(raw["coverage_degrees"]),
            length=float(raw["length"]),
            face_count=int(raw["face_count"]),
        )


@dataclass(frozen=True)
class ShapeFacts:
    """What the kernel measured about one solid. Plain data only."""

    solid_count: int
    shell_count: int
    volume: float
    area: float
    minimum: Tuple[float, float, float]
    maximum: Tuple[float, float, float]
    #: Face counts by surface type, lowercased (``plane``, ``cylinder``,
    #: ``cone``, ``torus``, ...).
    face_types: Mapping[str, int]
    cylinders: Tuple[CylindricalSurface, ...]
    #: Through-hole count (Euler-Poincare genus), or ``None`` when the
    #: topology did not give an integer -- never a rounded guess.
    genus: Optional[int]
    vertex_count: int
    edge_count: int
    face_count: int

    @property
    def extents(self) -> Tuple[float, float, float]:
        return (
            self.maximum[0] - self.minimum[0],
            self.maximum[1] - self.minimum[1],
            self.maximum[2] - self.minimum[2],
        )

    @property
    def bounding_volume(self) -> float:
        x, y, z = self.extents
        return x * y * z

    @property
    def fill_ratio(self) -> float:
        """Volume over bounding-box volume: 1 for a box, ~0.785 for a cylinder."""
        bounding = self.bounding_volume
        return self.volume / bounding if bounding > 0 else 0.0

    def bores(self) -> Tuple[CylindricalSurface, ...]:
        """Full-turn concave cylinders: holes, bores and round pockets."""
        return tuple(c for c in self.cylinders if c.concave and c.full_turn)

    def bosses(self) -> Tuple[CylindricalSurface, ...]:
        """Full-turn convex cylinders: shafts, pins, discs and round bosses."""
        return tuple(c for c in self.cylinders if not c.concave and c.full_turn)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solid_count": self.solid_count,
            "shell_count": self.shell_count,
            "volume": self.volume,
            "area": self.area,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "extents": list(self.extents),
            "fill_ratio": self.fill_ratio,
            "face_types": dict(sorted(self.face_types.items())),
            "cylinders": [item.to_dict() for item in self.cylinders],
            "genus": self.genus,
            "vertex_count": self.vertex_count,
            "edge_count": self.edge_count,
            "face_count": self.face_count,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ShapeFacts":
        genus = raw.get("genus")
        return cls(
            solid_count=int(raw["solid_count"]),
            shell_count=int(raw["shell_count"]),
            volume=float(raw["volume"]),
            area=float(raw["area"]),
            minimum=tuple(float(v) for v in raw["minimum"]),  # type: ignore[arg-type]
            maximum=tuple(float(v) for v in raw["maximum"]),  # type: ignore[arg-type]
            face_types={str(k): int(v) for k, v in dict(raw["face_types"]).items()},
            cylinders=tuple(
                CylindricalSurface.from_dict(item) for item in raw["cylinders"]
            ),
            genus=None if genus is None else int(genus),
            vertex_count=int(raw["vertex_count"]),
            edge_count=int(raw["edge_count"]),
            face_count=int(raw["face_count"]),
        )


@dataclass(frozen=True)
class OverlapMeasure:
    """The IoU of a candidate and a reference under one orientation mode."""

    orientation: str
    iou: float
    intersection_volume: float
    union_volume: float
    #: The rotation that gave the best IoU (``+X+Y+Z`` for ``fixed``; an
    #: aligning turn about Z is spelled ``Z+22.500000``).
    rotation: str
    #: How many rotations needed a boolean to establish the best.
    rotations_evaluated: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "orientation": self.orientation,
            "iou": self.iou,
            "intersection_volume": self.intersection_volume,
            "union_volume": self.union_volume,
            "rotation": self.rotation,
            "rotations_evaluated": self.rotations_evaluated,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OverlapMeasure":
        return cls(
            orientation=str(raw["orientation"]),
            iou=float(raw["iou"]),
            intersection_volume=float(raw["intersection_volume"]),
            union_volume=float(raw["union_volume"]),
            rotation=str(raw["rotation"]),
            rotations_evaluated=int(raw["rotations_evaluated"]),
        )


@dataclass(frozen=True)
class ShapeComparison:
    """A candidate solid measured against a reference solid."""

    candidate: ShapeFacts
    reference: ShapeFacts
    #: Candidate volume over reference volume.
    volume_ratio: float
    #: ``|candidate - reference| / reference``.
    volume_error: float
    #: Candidate extent minus reference extent, per axis, as built (mm).
    extent_differences: Tuple[float, float, float]
    #: Candidate minimum corner minus reference minimum corner, as built (mm).
    placement_offset: Tuple[float, float, float]
    #: One entry per requested orientation mode.
    overlaps: Mapping[str, OverlapMeasure]
    version: str = SHAPE_COMPARISON_VERSION

    def iou(self, orientation: str) -> Optional[float]:
        measure = self.overlaps.get(orientation)
        return None if measure is None else measure.iou

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "candidate": self.candidate.to_dict(),
            "reference": self.reference.to_dict(),
            "volume_ratio": self.volume_ratio,
            "volume_error": self.volume_error,
            "extent_differences": list(self.extent_differences),
            "placement_offset": list(self.placement_offset),
            "overlaps": {
                name: measure.to_dict() for name, measure in self.overlaps.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ShapeComparison":
        return cls(
            version=str(raw.get("version", SHAPE_COMPARISON_VERSION)),
            candidate=ShapeFacts.from_dict(raw["candidate"]),
            reference=ShapeFacts.from_dict(raw["reference"]),
            volume_ratio=float(raw["volume_ratio"]),
            volume_error=float(raw["volume_error"]),
            extent_differences=tuple(float(v) for v in raw["extent_differences"]),  # type: ignore[arg-type]
            placement_offset=tuple(float(v) for v in raw["placement_offset"]),  # type: ignore[arg-type]
            overlaps={
                str(name): OverlapMeasure.from_dict(item)
                for name, item in dict(raw["overlaps"]).items()
            },
        )


@dataclass(frozen=True)
class PairFailure:
    """Why one pair of a request produced no comparison."""

    #: ``candidate`` or ``reference``: which document failed; ``comparison``
    #: when both built and the kernel could not compare them.
    side: str
    #: ``validation``, ``geometry`` or ``comparison``.
    stage: str
    message: str
    rule_codes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "side": self.side,
            "stage": self.stage,
            "message": self.message,
            "rule_codes": list(self.rule_codes),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PairFailure":
        return cls(
            side=str(raw["side"]),
            stage=str(raw["stage"]),
            message=str(raw["message"]),
            rule_codes=tuple(str(code) for code in raw.get("rule_codes", ())),
        )


@dataclass(frozen=True)
class PairResult:
    """One pair of a ``compare_shapes`` request, answered.

    ``candidate`` holds the candidate's facts whenever it built, even if the
    reference did not or there was none; ``comparison`` is present only when
    both built and were compared.
    """

    candidate: Optional[ShapeFacts] = None
    comparison: Optional[ShapeComparison] = None
    failure: Optional[PairFailure] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate": None if self.candidate is None else self.candidate.to_dict(),
            "comparison": (
                None if self.comparison is None else self.comparison.to_dict()
            ),
            "failure": None if self.failure is None else self.failure.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PairResult":
        candidate = raw.get("candidate")
        comparison = raw.get("comparison")
        failure = raw.get("failure")
        return cls(
            candidate=None if candidate is None else ShapeFacts.from_dict(candidate),
            comparison=(
                None if comparison is None else ShapeComparison.from_dict(comparison)
            ),
            failure=None if failure is None else PairFailure.from_dict(failure),
        )


# --- measuring one solid -----------------------------------------------------


def _wrapped(shape: Any) -> Any:
    """The OpenCascade shape behind a CadQuery shape (or the shape itself)."""
    return getattr(shape, "wrapped", shape)


def _indexed(shape: Any, kind: Any) -> Any:
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, kind, found)
    return found


def _volume(shape: Any) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(_wrapped(shape), properties)
    return float(properties.Mass())


def _area(shape: Any) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(_wrapped(shape), properties)
    return float(properties.Mass())


def _bounds(shape: Any) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """The optimal (exact, not tessellated) axis-aligned bounding box."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(_wrapped(shape), box, False, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


_SURFACE_NAMES = {
    "GeomAbs_Plane": "plane",
    "GeomAbs_Cylinder": "cylinder",
    "GeomAbs_Cone": "cone",
    "GeomAbs_Sphere": "sphere",
    "GeomAbs_Torus": "torus",
    "GeomAbs_BezierSurface": "bezier",
    "GeomAbs_BSplineSurface": "bspline",
    "GeomAbs_SurfaceOfRevolution": "revolution",
    "GeomAbs_SurfaceOfExtrusion": "extrusion",
    "GeomAbs_OffsetSurface": "offset",
    "GeomAbs_OtherSurface": "other",
}


def _surface_name(surface_type: Any) -> str:
    raw = getattr(surface_type, "name", str(surface_type)).split(".")[-1]
    return _SURFACE_NAMES.get(raw, raw.lower())


def _canonical_direction(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """A unit direction with its first non-negligible component positive."""
    length = math.sqrt(x * x + y * y + z * z)
    x, y, z = x / length, y / length, z / length
    for component in (x, y, z):
        if abs(component) > DIRECTION_TOLERANCE:
            if component < 0:
                x, y, z = -x, -y, -z
            break
    return (x, y, z)


def _axis_name(direction: Sequence[float]) -> str:
    for index, component in enumerate(direction):
        if abs(abs(component) - 1.0) <= DIRECTION_TOLERANCE:
            return _AXIS_NAMES[index]
    return "oblique"


@dataclass
class _CylinderFace:
    radius: float
    direction: Tuple[float, float, float]
    location: Tuple[float, float, float]
    concave: bool
    span: float
    interval: Tuple[float, float]


def _cylinder_face(face: Any) -> _CylinderFace:
    """Measure one cylindrical face: radius, axis line, concavity, coverage."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Vec

    adaptor = BRepAdaptor_Surface(face)
    cylinder = adaptor.Cylinder()
    radius = float(cylinder.Radius())
    axis = cylinder.Axis()
    origin = axis.Location()
    raw_direction = axis.Direction()
    direction = _canonical_direction(
        raw_direction.X(), raw_direction.Y(), raw_direction.Z()
    )
    ox, oy, oz = origin.X(), origin.Y(), origin.Z()
    along = ox * direction[0] + oy * direction[1] + oz * direction[2]
    location = (
        ox - along * direction[0],
        oy - along * direction[1],
        oz - along * direction[2],
    )

    umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
    point = gp_Pnt()
    normal = gp_Vec()
    BRepGProp_Face(face).Normal((umin + umax) / 2.0, (vmin + vmax) / 2.0, point, normal)
    px, py, pz = point.X(), point.Y(), point.Z()
    t = (px - location[0]) * direction[0] + (py - location[1]) * direction[1] + (
        pz - location[2]
    ) * direction[2]
    radial = (
        px - (location[0] + t * direction[0]),
        py - (location[1] + t * direction[1]),
        pz - (location[2] + t * direction[2]),
    )
    outward = radial[0] * normal.X() + radial[1] * normal.Y() + radial[2] * normal.Z()

    # V on a cylinder is distance along its own axis direction from its own
    # origin; map that interval onto the canonical line coordinate.
    sign = 1.0 if (
        raw_direction.X() * direction[0]
        + raw_direction.Y() * direction[1]
        + raw_direction.Z() * direction[2]
    ) > 0 else -1.0
    a = along + sign * vmin
    b = along + sign * vmax
    return _CylinderFace(
        radius=radius,
        direction=direction,
        location=location,
        concave=outward < 0,
        span=float(umax - umin),
        interval=(min(a, b), max(a, b)),
    )


def _same_line(first: _CylinderFace, second: _CylinderFace) -> bool:
    if first.concave != second.concave:
        return False
    if abs(first.radius - second.radius) > RADIUS_TOLERANCE_MM:
        return False
    if any(
        abs(a - b) > DIRECTION_TOLERANCE * 10
        for a, b in zip(first.direction, second.direction)
    ):
        return False
    return all(
        abs(a - b) <= AXIS_LINE_TOLERANCE_MM
        for a, b in zip(first.location, second.location)
    )


def _covered_length(intervals: Iterable[Tuple[float, float]]) -> float:
    total = 0.0
    current: Optional[List[float]] = None
    for low, high in sorted(intervals):
        if current is None or low > current[1]:
            if current is not None:
                total += current[1] - current[0]
            current = [low, high]
        else:
            current[1] = max(current[1], high)
    if current is not None:
        total += current[1] - current[0]
    return total


def _group_cylinders(faces: Sequence[_CylinderFace]) -> Tuple[CylindricalSurface, ...]:
    ordered = sorted(
        faces,
        key=lambda f: (
            f.concave,
            round(f.radius, 6),
            tuple(round(v, 6) for v in f.direction),
            tuple(round(v, 4) for v in f.location),
        ),
    )
    lines: List[List[_CylinderFace]] = []
    for face in ordered:
        for line in lines:
            if _same_line(line[0], face):
                line.append(face)
                break
        else:
            lines.append([face])
    # Within one axis line, faces whose axial intervals overlap or touch are
    # one surface; a gap separates two. So a pip on each of two opposite faces
    # of a die is two bores, while a hole split by its seam, or crossed by
    # another hole, stays one.
    groups: List[List[_CylinderFace]] = []
    for line in lines:
        current: List[_CylinderFace] = []
        reach = -math.inf
        for face in sorted(line, key=lambda f: f.interval):
            if current and face.interval[0] > reach + AXIS_LINE_TOLERANCE_MM:
                groups.append(current)
                current = []
            current.append(face)
            reach = max(reach, face.interval[1]) if len(current) > 1 else face.interval[1]
        if current:
            groups.append(current)
    surfaces: List[CylindricalSurface] = []
    for group in groups:
        first = group[0]
        coverage = min(2.0 * math.pi, sum(face.span for face in group))
        surfaces.append(
            CylindricalSurface(
                diameter=2.0 * first.radius,
                axis=_axis_name(first.direction),
                direction=first.direction,
                location=first.location,
                concave=first.concave,
                full_turn=coverage >= 2.0 * math.pi - FULL_TURN_TOLERANCE_RAD,
                coverage_degrees=math.degrees(coverage),
                length=_covered_length(face.interval for face in group),
                face_count=len(group),
            )
        )
    surfaces.sort(
        key=lambda s: (s.concave is False, s.diameter, s.axis, s.location)
    )
    return tuple(surfaces)


def _genus(
    vertices: int, edges: int, faces: int, loops: int, shells: int
) -> Optional[int]:
    """Through-hole count from the Euler-Poincare formula.

    ``V - E + F - (L - F) = 2 (S - G)`` for a valid closed B-rep, where
    ``L - F`` counts the inner loops (rings) of faces. Degenerate edges (a
    cone's apex) are excluded from ``E`` by the caller.
    """
    chi = vertices - edges + faces - (loops - faces)
    genus = shells - chi / 2.0
    nearest = round(genus)
    if abs(genus - nearest) > GENUS_TOLERANCE or nearest < 0:
        return None
    return int(nearest)


def shape_facts(shape: Any) -> ShapeFacts:
    """Measure one built solid. Every number comes from the kernel."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import (
        TopAbs_EDGE,
        TopAbs_FACE,
        TopAbs_SHELL,
        TopAbs_SOLID,
        TopAbs_VERTEX,
        TopAbs_WIRE,
    )
    from OCP.TopoDS import TopoDS

    occ = _wrapped(shape)
    vertex_map = _indexed(occ, TopAbs_VERTEX)
    edge_map = _indexed(occ, TopAbs_EDGE)
    face_map = _indexed(occ, TopAbs_FACE)
    shell_map = _indexed(occ, TopAbs_SHELL)
    solid_map = _indexed(occ, TopAbs_SOLID)

    edges = sum(
        1
        for index in range(1, edge_map.Extent() + 1)
        if not BRep_Tool.Degenerated_s(TopoDS.Edge_s(edge_map.FindKey(index)))
    )

    face_types: Dict[str, int] = {}
    loops = 0
    cylinder_faces: List[_CylinderFace] = []
    for index in range(1, face_map.Extent() + 1):
        face = TopoDS.Face_s(face_map.FindKey(index))
        loops += _indexed(face, TopAbs_WIRE).Extent()
        name = _surface_name(BRepAdaptor_Surface(face).GetType())
        face_types[name] = face_types.get(name, 0) + 1
        if name == "cylinder":
            cylinder_faces.append(_cylinder_face(face))

    faces = face_map.Extent()
    shells = shell_map.Extent()
    minimum, maximum = _bounds(occ)
    return ShapeFacts(
        solid_count=solid_map.Extent(),
        shell_count=shells,
        volume=_volume(occ),
        area=_area(occ),
        minimum=minimum,
        maximum=maximum,
        face_types=dict(sorted(face_types.items())),
        cylinders=_group_cylinders(cylinder_faces),
        genus=_genus(vertex_map.Extent(), edges, faces, loops, shells),
        vertex_count=vertex_map.Extent(),
        edge_count=edges,
        face_count=faces,
    )


# --- comparing two solids ----------------------------------------------------


def _placed(shape: Any, rotation: AxisRotation, shift: Sequence[float]) -> Any:
    """``shape`` rotated by ``rotation`` and then moved by ``shift``. Exact."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf

    m = [[0.0, 0.0, 0.0] for _ in range(3)]
    for row in range(3):
        m[row][rotation.source[row]] = float(rotation.signs[row])
    transform = gp_Trsf()
    transform.SetValues(
        m[0][0], m[0][1], m[0][2], float(shift[0]),
        m[1][0], m[1][1], m[1][2], float(shift[1]),
        m[2][0], m[2][1], m[2][2], float(shift[2]),
    )
    builder = BRepBuilderAPI_Transform(_wrapped(shape), transform, True)
    return builder.Shape()


def _common_volume(first: Any, second: Any) -> float:
    """Volume of the boolean intersection of two solids."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common

    operation = BRepAlgoAPI_Common(_wrapped(first), _wrapped(second))
    if not operation.IsDone() or operation.Shape().IsNull():
        raise ShapeComparisonError(
            "the kernel could not intersect the candidate with the reference"
        )
    return _volume(operation.Shape())


def _overlap_bound(
    candidate: ShapeFacts, reference: ShapeFacts, rotation: AxisRotation
) -> float:
    """An exact upper bound on the IoU under ``rotation`` (corners aligned)."""
    extents = rotation.apply_extents(candidate.extents)
    overlap = 1.0
    for a, b in zip(extents, reference.extents):
        overlap *= max(0.0, min(a, b))
    best_intersection = min(candidate.volume, reference.volume, overlap)
    union = candidate.volume + reference.volume - best_intersection
    return best_intersection / union if union > 0 else 0.0


def _iou_under(
    candidate_shape: Any,
    candidate: ShapeFacts,
    reference_shape: Any,
    reference: ShapeFacts,
    rotation: AxisRotation,
) -> Tuple[float, float, float]:
    low, _ = rotation.apply_box(candidate.minimum, candidate.maximum)
    shift = tuple(r - c for r, c in zip(reference.minimum, low))
    moved = _placed(candidate_shape, rotation, shift)
    intersection = _common_volume(moved, reference_shape)
    smaller = min(candidate.volume, reference.volume)
    if intersection > smaller + VOLUME_SLACK_MM3:
        raise ShapeComparisonError(
            "the kernel reported an intersection larger than either solid"
        )
    intersection = min(max(intersection, 0.0), smaller)
    union = candidate.volume + reference.volume - intersection
    iou = intersection / union if union > 0 else 0.0
    return iou, intersection, union


def _vertical_round_angles(facts: ShapeFacts) -> List[float]:
    """Angles (degrees) of off-centre vertical bores and bosses about the
    vertical line through the bounding-box centre."""
    cx = (facts.minimum[0] + facts.maximum[0]) / 2.0
    cy = (facts.minimum[1] + facts.maximum[1]) / 2.0
    angles: List[float] = []
    for surface in facts.cylinders:
        if not surface.full_turn or surface.axis != "Z":
            continue
        dx, dy = surface.location[0] - cx, surface.location[1] - cy
        if math.hypot(dx, dy) <= AXIS_LINE_TOLERANCE_MM:
            continue
        angles.append(math.degrees(math.atan2(dy, dx)))
    return angles


def aligning_turns(candidate: ShapeFacts, reference: ShapeFacts) -> Tuple[float, ...]:
    """Turns about Z (degrees, in [0, 360)) that put a candidate vertical
    round feature on a reference one, excluding quarter turns. Sorted and
    capped at :data:`MAX_ALIGNING_TURNS`, so the search is deterministic."""
    turns: List[float] = []
    for a in _vertical_round_angles(reference):
        for b in _vertical_round_angles(candidate):
            turn = (a - b) % 360.0
            if min(abs(turn - q) for q in (0.0, 90.0, 180.0, 270.0, 360.0)) <= TURN_TOLERANCE_DEG:
                continue
            if all(abs(turn - seen) > TURN_TOLERANCE_DEG for seen in turns):
                turns.append(turn)
    turns.sort()
    return tuple(turns[:MAX_ALIGNING_TURNS])


def _iou_turned(
    candidate_shape: Any,
    candidate: ShapeFacts,
    reference_shape: Any,
    reference: ShapeFacts,
    degrees: float,
) -> Tuple[float, float, float]:
    """IoU after turning the candidate ``degrees`` about the Z axis through
    the origin and aligning bounding-box minimum corners."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

    turn = gp_Trsf()
    turn.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.radians(degrees))
    turned = BRepBuilderAPI_Transform(_wrapped(candidate_shape), turn, True).Shape()
    low, _ = _bounds(turned)
    shift = gp_Trsf()
    shift.SetTranslation(gp_Vec(*(r - c for r, c in zip(reference.minimum, low))))
    moved = BRepBuilderAPI_Transform(turned, shift, True).Shape()
    intersection = _common_volume(moved, reference_shape)
    smaller = min(candidate.volume, reference.volume)
    if intersection > smaller + VOLUME_SLACK_MM3:
        raise ShapeComparisonError(
            "the kernel reported an intersection larger than either solid"
        )
    intersection = min(max(intersection, 0.0), smaller)
    union = candidate.volume + reference.volume - intersection
    return (intersection / union if union > 0 else 0.0), intersection, union


def compare_shapes(
    candidate_shape: Any,
    reference_shape: Any,
    *,
    orientations: Sequence[str] = (ORIENTATION_FIXED,),
    candidate_facts: Optional[ShapeFacts] = None,
    reference_facts: Optional[ShapeFacts] = None,
) -> ShapeComparison:
    """Compare a built candidate solid with a built reference solid.

    For each requested orientation mode the IoU is the best over that mode's
    rotations, each applied about the candidate and followed by a shift that
    puts the candidate's bounding-box minimum corner on the reference's. The
    as-built placement is reported separately (``placement_offset``), so a
    caller that cares where the part is can check it.
    """
    for orientation in orientations:
        rotations_for(orientation)  # raises on an unknown mode
    candidate = candidate_facts or shape_facts(candidate_shape)
    reference = reference_facts or shape_facts(reference_shape)
    if reference.volume <= 0:
        raise ShapeComparisonError("the reference solid has no volume")

    cache: Dict[str, Tuple[float, float, float]] = {}

    def measure(rotation: AxisRotation) -> Tuple[float, float, float]:
        if rotation.name not in cache:
            cache[rotation.name] = _iou_under(
                candidate_shape, candidate, reference_shape, reference, rotation
            )
        return cache[rotation.name]

    overlaps: Dict[str, OverlapMeasure] = {}
    for orientation in orientations:
        candidates = rotations_for(orientation)
        ranked = sorted(
            enumerate(candidates),
            key=lambda item: (-_overlap_bound(candidate, reference, item[1]), item[0]),
        )
        best: Optional[Tuple[float, float, float]] = None
        best_rotation = ranked[0][1]
        evaluated = 0
        for _, rotation in ranked:
            bound = _overlap_bound(candidate, reference, rotation)
            if best is not None and best[0] >= bound - BOUND_TOLERANCE:
                break
            result = measure(rotation)
            evaluated += 1
            if best is None or result[0] > best[0]:
                best, best_rotation = result, rotation
        assert best is not None
        best_name = best_rotation.name
        if orientation == ORIENTATION_UPRIGHT_ANY:
            for degrees in aligning_turns(candidate, reference):
                result = _iou_turned(
                    candidate_shape, candidate, reference_shape, reference, degrees
                )
                evaluated += 1
                if result[0] > best[0]:
                    best, best_name = result, f"Z{degrees:+.6f}"
        overlaps[orientation] = OverlapMeasure(
            orientation=orientation,
            iou=best[0],
            intersection_volume=best[1],
            union_volume=best[2],
            rotation=best_name,
            rotations_evaluated=evaluated,
        )

    return ShapeComparison(
        candidate=candidate,
        reference=reference,
        volume_ratio=candidate.volume / reference.volume,
        volume_error=abs(candidate.volume - reference.volume) / reference.volume,
        extent_differences=tuple(  # type: ignore[arg-type]
            c - r for c, r in zip(candidate.extents, reference.extents)
        ),
        placement_offset=tuple(  # type: ignore[arg-type]
            c - r for c, r in zip(candidate.minimum, reference.minimum)
        ),
        overlaps=overlaps,
    )


# --- documents in, plain data out -------------------------------------------


class _BuildProblem(Exception):
    def __init__(self, stage: str, message: str, rule_codes: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.rule_codes = tuple(rule_codes)


def build_document_shape(document: Mapping[str, Any]) -> Any:
    """Validate a CAD document and build it with the local engine.

    The validator decides validity and the engine builds; nothing here
    re-interprets the document. Raises ``_BuildProblem`` (stage
    ``validation`` or ``geometry``) when either refuses.
    """
    from cad_core.local_cad import (
        GeometryOperationError,
        UnsupportedGeometryError,
        build_part,
    )
    from cad_core.validator import validate

    result = validate(dict(document))
    if not result.valid or result.part is None:
        raise _BuildProblem(
            "validation",
            "the document is not valid CAD",
            result.rule_codes(),
        )
    try:
        return build_part(result.part).shape
    except (GeometryOperationError, UnsupportedGeometryError) as exc:
        raise _BuildProblem("geometry", str(exc)) from None


#: Fields one pair of a ``compare_shapes`` request may carry.
PAIR_FIELDS: Tuple[str, ...] = ("candidate", "reference", "orientations")


def _pair_from(raw: Any, index: int) -> Tuple[Mapping[str, Any], Optional[Mapping[str, Any]], Tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        raise ComparisonRequestError(f"pair {index} is not an object")
    unknown = set(raw) - set(PAIR_FIELDS)
    if unknown:
        raise ComparisonRequestError(
            f"pair {index} has unknown fields: {', '.join(sorted(unknown))}"
        )
    candidate = raw.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ComparisonRequestError(f"pair {index} has no candidate document")
    reference = raw.get("reference")
    if reference is not None and not isinstance(reference, Mapping):
        raise ComparisonRequestError(f"pair {index}'s reference is not an object")
    orientations = raw.get("orientations", [ORIENTATION_FIXED])
    if not isinstance(orientations, (list, tuple)) or not all(
        isinstance(name, str) for name in orientations
    ):
        raise ComparisonRequestError(f"pair {index}'s orientations are malformed")
    for name in orientations:
        rotations_for(name)
    return candidate, reference, tuple(orientations)


def compare_pair(
    candidate_document: Mapping[str, Any],
    reference_document: Optional[Mapping[str, Any]],
    orientations: Sequence[str] = (ORIENTATION_FIXED,),
) -> PairResult:
    """Build both documents and compare them, as plain data.

    A candidate that fails to validate or build is a *result* (``failure``
    with ``side="candidate"``), not an error: that is what the caller asked
    to know. With no reference, only the candidate's facts are measured.
    """
    try:
        candidate_shape = build_document_shape(candidate_document)
    except _BuildProblem as problem:
        return PairResult(
            failure=PairFailure(
                side="candidate",
                stage=problem.stage,
                message=problem.message,
                rule_codes=problem.rule_codes,
            )
        )
    candidate = shape_facts(candidate_shape)
    if reference_document is None:
        return PairResult(candidate=candidate)
    try:
        reference_shape = build_document_shape(reference_document)
    except _BuildProblem as problem:
        return PairResult(
            candidate=candidate,
            failure=PairFailure(
                side="reference",
                stage=problem.stage,
                message=problem.message,
                rule_codes=problem.rule_codes,
            ),
        )
    try:
        comparison = compare_shapes(
            candidate_shape,
            reference_shape,
            orientations=orientations,
            candidate_facts=candidate,
        )
    except ShapeComparisonError as exc:
        return PairResult(
            candidate=candidate,
            failure=PairFailure(side="comparison", stage="comparison", message=str(exc)),
        )
    return PairResult(candidate=candidate, comparison=comparison)


#: The most pairs one request may carry.
MAX_PAIRS = 512


def run_comparison_request(payload: Any) -> Dict[str, Any]:
    """The isolated worker's ``compare_shapes`` operation: plain data in and out.

    ``payload`` is ``{"pairs": [{"candidate": doc, "reference": doc | null,
    "orientations": [...]}, ...]}``; the answer is ``{"version": ...,
    "results": [PairResult.to_dict(), ...]}`` in the same order. The whole
    request is checked before anything is built.
    """
    if not isinstance(payload, Mapping) or set(payload) != {"pairs"}:
        raise ComparisonRequestError("the request must hold exactly 'pairs'")
    raw_pairs = payload["pairs"]
    if not isinstance(raw_pairs, (list, tuple)):
        raise ComparisonRequestError("'pairs' must be a list")
    if len(raw_pairs) > MAX_PAIRS:
        raise ComparisonRequestError(f"at most {MAX_PAIRS} pairs per request")
    pairs = [_pair_from(raw, index) for index, raw in enumerate(raw_pairs)]
    return {
        "version": SHAPE_COMPARISON_VERSION,
        "results": [compare_pair(*pair).to_dict() for pair in pairs],
    }


def results_from_payload(payload: Any) -> Tuple[PairResult, ...]:
    """Read a ``compare_shapes`` answer back into :class:`PairResult` objects."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
        raise ComparisonRequestError("the answer holds no 'results' list")
    if payload.get("version") != SHAPE_COMPARISON_VERSION:
        raise ComparisonRequestError(
            f"the answer is comparison version {payload.get('version')!r}; "
            f"this reader understands {SHAPE_COMPARISON_VERSION!r}"
        )
    return tuple(PairResult.from_dict(item) for item in payload["results"])


__all__ = [
    "AXIS_LINE_TOLERANCE_MM",
    "BOUND_TOLERANCE",
    "DIRECTION_TOLERANCE",
    "FULL_TURN_TOLERANCE_RAD",
    "GENUS_TOLERANCE",
    "IDENTITY",
    "MAX_PAIRS",
    "ORIENTATION_FIXED",
    "ORIENTATION_FREE",
    "ORIENTATION_MODES",
    "ORIENTATION_UPRIGHT",
    "ORIENTATION_UPRIGHT_ANY",
    "MAX_ALIGNING_TURNS",
    "TURN_TOLERANCE_DEG",
    "aligning_turns",
    "PAIR_FIELDS",
    "RADIUS_TOLERANCE_MM",
    "ROTATIONS",
    "SHAPE_COMPARISON_VERSION",
    "VOLUME_SLACK_MM3",
    "AxisRotation",
    "ComparisonRequestError",
    "CylindricalSurface",
    "OverlapMeasure",
    "PairFailure",
    "PairResult",
    "ShapeComparison",
    "ShapeComparisonError",
    "ShapeFacts",
    "build_document_shape",
    "compare_pair",
    "compare_shapes",
    "results_from_payload",
    "rotations_for",
    "run_comparison_request",
    "shape_facts",
]
