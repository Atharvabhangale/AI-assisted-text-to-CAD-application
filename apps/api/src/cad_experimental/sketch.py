"""The sketch foundation: typed 2D geometry and constraints, validated only.

**A sketch cannot be built.** V1's contract lists sketches among the concepts
that are out of scope and that a validator MUST reject (Section "Scope of
V1", rule S3), and ``cad_core``'s engine implements the six V1 features and
nothing else. So there is no honest way to execute a sketch today:

* extending ``cad_core`` would modify the stable production package;
* executing it here would be a second CAD path, which the architecture
  forbids for good reason.

The remaining option is the one this module takes: represent a sketch
precisely, validate it thoroughly, and then say **explicitly** that execution
is unsupported. :class:`~cad_experimental.adapter.ExecutionUnsupported` is
that answer. Nothing here approximates, substitutes or fakes geometry.

What is deliberately *not* here
-------------------------------
No constraint solver. The constraints below are **declarative and checked**,
not solved: a ``length`` constraint that disagrees with the line it names is
reported as a conflict, never used to move the line. Solving would mean
inventing geometry the backend cannot execute anyway, and would make a
disagreement invisible instead of reported.

Shape
-----
A sketch has two ordered lists -- geometry, then constraints -- so a
constraint can never forward-reference: every geometry id it could name is
already declared. That is structural, not a rule anyone has to enforce.

Point handles are objects, not dotted strings::

    {"geometry": "l1", "point": "end"}

A dotted ``"l1.end"`` would work, but it would put a traversal-shaped string
where every other reference in this language is a plain identifier, and the
security properties of the rest of the parser rest on that. This keeps every
reference an identifier plus an enumerated handle name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

# --- vocabulary ------------------------------------------------------------

#: The three principal sketch planes, and no others. An arbitrary plane
#: needs a datum, and V1 has no datums (Section A.2).
PLANES: Tuple[str, ...] = ("XY", "XZ", "YZ")

#: The unsigned axis normal to each plane. Derived from the plane's name, and
#: written out so nothing has to parse it: "XY" spans X and Y, so its normal
#: is Z. An extrusion runs along this axis and along no other -- extruding a
#: profile sideways within its own plane is not an extrusion.
PLANE_NORMAL: Dict[str, str] = {"XY": "Z", "XZ": "Y", "YZ": "X"}

#: The two unsigned axes each plane spans. A revolve's axis must be one of
#: these: revolving a profile about its own normal sweeps nothing.
PLANE_AXES: Dict[str, Tuple[str, str]] = {
    "XY": ("X", "Y"),
    "XZ": ("X", "Z"),
    "YZ": ("Y", "Z"),
}

LINE = "line"
CIRCLE = "circle"
RECTANGLE = "rectangle"
GEOMETRY_TYPES: Tuple[str, ...] = (LINE, CIRCLE, RECTANGLE)

COINCIDENT = "coincident"
HORIZONTAL = "horizontal"
VERTICAL = "vertical"
LENGTH = "length"
RADIUS = "radius"
CONSTRAINT_TYPES: Tuple[str, ...] = (
    COINCIDENT, HORIZONTAL, VERTICAL, LENGTH, RADIUS,
)

#: Constraints that carry a numeric value, and the geometry each applies to.
DIMENSIONAL_TYPES: Tuple[str, ...] = (LENGTH, RADIUS)

#: Which geometry type each constraint may name. A `radius` on a line is a
#: category error, not a value error, and is reported as one.
CONSTRAINT_APPLIES_TO: Dict[str, Tuple[str, ...]] = {
    HORIZONTAL: (LINE,),
    VERTICAL: (LINE,),
    LENGTH: (LINE,),
    RADIUS: (CIRCLE,),
}

#: The named points each geometry type offers. Enumerated, so a handle is
#: always a known name and never a lookup.
POINT_HANDLES: Dict[str, Tuple[str, ...]] = {
    LINE: ("start", "end"),
    CIRCLE: ("centre",),
    RECTANGLE: ("corner",),
}

#: Parameters per geometry type: required, then the lengths that must be > 0.
GEOMETRY_FIELDS: Dict[str, Tuple[str, ...]] = {
    LINE: ("id", "type", "start", "end"),
    CIRCLE: ("id", "type", "centre", "radius"),
    RECTANGLE: ("id", "type", "corner", "width", "height"),
}
GEOMETRY_LENGTHS: Dict[str, Tuple[str, ...]] = {
    LINE: (),
    CIRCLE: ("radius",),
    RECTANGLE: ("width", "height"),
}

#: Fields per constraint type.
CONSTRAINT_FIELDS: Dict[str, Tuple[str, ...]] = {
    COINCIDENT: ("id", "type", "points"),
    HORIZONTAL: ("id", "type", "geometry"),
    VERTICAL: ("id", "type", "geometry"),
    LENGTH: ("id", "type", "geometry", "value"),
    RADIUS: ("id", "type", "geometry", "value"),
}

#: Bounds. A sketch is a profile, not a mesh.
MAX_GEOMETRY = 64
MAX_CONSTRAINTS = 128


# --- typed records ---------------------------------------------------------


@dataclass(frozen=True)
class Point2D:
    """A point in the sketch plane, in millimetres. Two components, not three."""

    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass(frozen=True)
class Line:
    """A straight segment between two points in the sketch plane."""

    TYPE = LINE

    id: str
    start: Point2D
    end: Point2D

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": LINE,
            "start": self.start.to_dict(), "end": self.end.to_dict(),
        }


@dataclass(frozen=True)
class Circle:
    """A full circle. ``radius``, not diameter -- and > 0."""

    TYPE = CIRCLE

    id: str
    centre: Point2D
    radius: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": CIRCLE,
            "centre": self.centre.to_dict(), "radius": self.radius,
        }


@dataclass(frozen=True)
class Rectangle:
    """An axis-aligned rectangle. ``corner`` is its minimum corner.

    Kept as one primitive rather than four constrained lines: four lines plus
    the coincidence and perpendicularity constraints to hold them together
    would need a solver, and there isn't one.
    """

    TYPE = RECTANGLE

    id: str
    corner: Point2D
    width: float
    height: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "type": RECTANGLE,
            "corner": self.corner.to_dict(),
            "width": self.width, "height": self.height,
        }


@dataclass(frozen=True)
class PointHandle:
    """A named point of one piece of geometry."""

    geometry: str
    point: str

    def to_dict(self) -> Dict[str, str]:
        return {"geometry": self.geometry, "point": self.point}


@dataclass(frozen=True)
class Constraint:
    """One declarative constraint.

    Exactly one of :attr:`geometry` or :attr:`points` is set, by type:
    ``coincident`` relates two point handles, everything else names one piece
    of geometry. :attr:`value` is set for the dimensional types only.
    """

    id: str
    type: str
    geometry: Optional[str] = None
    points: Tuple[PointHandle, ...] = ()
    value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"id": self.id, "type": self.type}
        if self.geometry is not None:
            payload["geometry"] = self.geometry
        if self.points:
            payload["points"] = [handle.to_dict() for handle in self.points]
        if self.value is not None:
            payload["value"] = self.value
        return payload


#: A parsed piece of sketch geometry.
Geometry = Any  # Line | Circle | Rectangle (3.9-compatible)


@dataclass(frozen=True)
class SketchDefinition:
    """A whole sketch: a plane, its geometry, and its constraints."""

    plane: str
    geometry: Tuple[Geometry, ...]
    constraints: Tuple[Constraint, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plane": self.plane,
            "geometry": [item.to_dict() for item in self.geometry],
            "constraints": [c.to_dict() for c in self.constraints],
        }

    def geometry_types(self) -> Dict[str, str]:
        """Geometry id -> type, for reference checking."""
        return {item.id: item.TYPE for item in self.geometry}


#: Names of the shared ``$defs`` entries this module contributes. Kept as
#: constants so the plan schema references them by name rather than by a
#: string typed twice.
POINT2D_DEF = "sketch_point"
HANDLE_DEF = "sketch_point_handle"
GEOMETRY_DEF = "sketch_geometry"
CONSTRAINT_DEF = "sketch_constraint"


def _ref(name: str) -> Dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _geometry_branches() -> List[Dict[str, Any]]:
    """One schema branch per geometry type, each with no optional field.

    A single object carrying every geometry type's fields would need seven
    optional properties; as a union of fully-required branches it needs none.
    Points are referenced rather than inlined, which is what keeps the
    compiled grammar small enough for the provider to accept.
    """
    point = _ref(POINT2D_DEF)
    shapes: Dict[str, Dict[str, Any]] = {
        LINE: {"start": point, "end": point},
        CIRCLE: {"centre": point, "radius": {"type": "number"}},
        RECTANGLE: {
            "corner": point,
            "width": {"type": "number"},
            "height": {"type": "number"},
        },
    }
    branches: List[Dict[str, Any]] = []
    for kind in GEOMETRY_TYPES:
        fields = shapes[kind]
        branches.append({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "type": {"const": kind},
                **fields,
            },
            "required": ["id", "type", *fields],
            "additionalProperties": False,
        })
    return branches


def _constraint_branches() -> List[Dict[str, Any]]:
    """One schema branch per constraint type, each with no optional field.

    ``coincident`` relates two point handles; every other type names one
    piece of geometry, and the dimensional ones carry a value. Expressed as a
    union, the ``geometry``/``points``/``value`` optionals a merged object
    would need disappear.
    """
    branches: List[Dict[str, Any]] = []
    for kind in CONSTRAINT_TYPES:
        if kind == COINCIDENT:
            fields: Dict[str, Any] = {
                "points": {"type": "array", "items": _ref(HANDLE_DEF)},
            }
        else:
            fields = {"geometry": {"type": "string"}}
            if kind in DIMENSIONAL_TYPES:
                fields["value"] = {"type": "number"}
        branches.append({
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "type": {"const": kind},
                **fields,
            },
            "required": ["id", "type", *fields],
            "additionalProperties": False,
        })
    return branches


def sketch_defs() -> Dict[str, Any]:
    """The shared ``$defs`` a sketch needs, for the plan schema to merge in.

    Every one of these appears several times in the schema. Emitting each
    once and referencing it is the difference between a grammar the provider
    compiles and one it refuses as too large -- measured, not assumed.
    """
    return {
        POINT2D_DEF: {
            "type": "object",
            "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        HANDLE_DEF: {
            "type": "object",
            "properties": {
                "geometry": {"type": "string"},
                "point": {"type": "string"},
            },
            "required": ["geometry", "point"],
            "additionalProperties": False,
        },
        GEOMETRY_DEF: {"anyOf": _geometry_branches()},
        CONSTRAINT_DEF: {"anyOf": _constraint_branches()},
    }


def sketch_schema() -> Dict[str, Any]:
    """The sketch parameter fragment. Advisory only.

    Both lists are unions of fully-required branches, so this fragment
    contributes **no optional properties of its own**. The wire format is
    unchanged -- the same objects the parser already accepts -- only the way
    the schema describes them. Requires :func:`sketch_defs` to be merged into
    the document's ``$defs``.
    """
    return {
        "plane": {"type": "string", "enum": list(PLANES)},
        "geometry": {
            "type": "array",
            "minItems": 1,
            "items": _ref(GEOMETRY_DEF),
        },
        "constraints": {"type": "array", "items": _ref(CONSTRAINT_DEF)},
    }


__all__ = [
    "CIRCLE",
    "CONSTRAINT_DEF",
    "GEOMETRY_DEF",
    "HANDLE_DEF",
    "POINT2D_DEF",
    "sketch_defs",
    "PLANE_AXES",
    "PLANE_NORMAL",
    "COINCIDENT",
    "CONSTRAINT_APPLIES_TO",
    "CONSTRAINT_FIELDS",
    "CONSTRAINT_TYPES",
    "DIMENSIONAL_TYPES",
    "GEOMETRY_FIELDS",
    "GEOMETRY_LENGTHS",
    "GEOMETRY_TYPES",
    "HORIZONTAL",
    "LENGTH",
    "LINE",
    "MAX_CONSTRAINTS",
    "MAX_GEOMETRY",
    "PLANES",
    "POINT_HANDLES",
    "RADIUS",
    "RECTANGLE",
    "VERTICAL",
    "Circle",
    "Constraint",
    "Geometry",
    "Line",
    "Point2D",
    "PointHandle",
    "Rectangle",
    "SketchDefinition",
    "sketch_schema",
]
