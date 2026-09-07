"""Typed representation of the V1 CAD specification.

Mirrors ``docs/cad-specification.md`` (schema version 1.0.0) one-to-one.  These
types describe a specification document; they contain no geometry and no
geometric behaviour.

Instances are produced by :func:`cad_core.validator.validate` only for a
document that passes static validation, so a constructed :class:`Part` is a
statically valid part.  Optional parameters carry the defaults from the
Section C tables, materialised at construction time: an omitted ``position``
becomes :data:`ORIGIN` and an omitted ``axis`` becomes :data:`DEFAULT_AXIS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Mapping, Optional, Tuple, Union

# --- Contract constants (Sections A, B, C, F) -------------------------------

#: Schema version described by docs/cad-specification.md.
SCHEMA_VERSION = "1.0.0"

#: Major version this implementation accepts (Section F, rule S4).
SUPPORTED_SCHEMA_MAJOR = 1

#: Unit systems accepted by V1 (Section A.3, rule S5).
SUPPORTED_UNITS: Tuple[str, ...] = ("mm",)

#: The six signed principal directions (Section A.4, rule S12).
AXIS_VALUES: Tuple[str, ...] = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")

#: Default axis for cylinder and through_hole (Sections C.2, C.3).
DEFAULT_AXIS = "+Z"

#: Edge selector kinds (Section C.7, rule S18).
EDGE_SELECT_VALUES: Tuple[str, ...] = ("all", "axis_parallel")

#: Unsigned axis letters used by the axis_parallel selector (Section C.7).
SELECTOR_AXIS_VALUES: Tuple[str, ...] = ("X", "Y", "Z")

#: Feature id pattern (Section B.3, rule S8).
ID_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]*$"

#: Required and optional root fields (Section B.1, rules S1 and S3).
REQUIRED_ROOT_FIELDS: Tuple[str, ...] = ("schema_version", "units", "name", "features")
OPTIONAL_ROOT_FIELDS: Tuple[str, ...] = ("description",)

#: Fields every feature carries (Section B.3).
COMMON_FEATURE_FIELDS: Tuple[str, ...] = ("id", "type")

#: Constructive features add a solid to the solid set (Section B.4).
CONSTRUCTIVE_TYPES: Tuple[str, ...] = ("box", "cylinder")

#: Modifier features replace their target in place (Section B.4).
MODIFIER_TYPES: Tuple[str, ...] = ("through_hole", "subtract", "fillet", "chamfer")

#: All V1 feature types (Scope of V1, rule S3).
FEATURE_TYPES: Tuple[str, ...] = CONSTRUCTIVE_TYPES + MODIFIER_TYPES

#: Per-type parameters from the Section C tables, excluding the common ``id``
#: and ``type`` fields, as ``(required, optional)``.
FEATURE_PARAMETERS: Mapping[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "box": (("size",), ("position",)),
    "cylinder": (("diameter", "height"), ("position", "axis")),
    "through_hole": (("target", "diameter", "position"), ("axis",)),
    "subtract": (("target", "tools"), ()),
    "fillet": (("target", "radius", "edges"), ()),
    "chamfer": (("target", "distance", "edges"), ()),
}

#: Reference-carrying parameters per feature type (Section B.3, rules S6, S7).
FEATURE_REFERENCE_FIELDS: Mapping[str, Tuple[str, ...]] = {
    "through_hole": ("target",),
    "subtract": ("target", "tools"),
    "fillet": ("target",),
    "chamfer": ("target",),
}


# --- Value objects (Section A.4) -------------------------------------------


@dataclass(frozen=True)
class Position:
    """A point in the part coordinate system, in the part's declared unit."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Size:
    """Extents along each axis, in the part's declared unit. All positive."""

    x: float
    y: float
    z: float


#: The default position for features whose ``position`` is optional.
ORIGIN = Position(0.0, 0.0, 0.0)


@dataclass(frozen=True)
class EdgeSelector:
    """An edge selector (Section C.7).

    ``axis`` is set exactly when ``select`` is ``"axis_parallel"``, and uses the
    unsigned letters ``X``/``Y``/``Z``: parallelism has no direction.
    """

    select: str
    axis: Optional[str] = None


# --- Constructive features (Sections C.1, C.2) -----------------------------


@dataclass(frozen=True)
class Box:
    """An axis-aligned rectangular solid. ``position`` is the minimum corner."""

    TYPE: ClassVar[str] = "box"

    id: str
    size: Size
    position: Position = ORIGIN


@dataclass(frozen=True)
class Cylinder:
    """A right circular cylinder. ``position`` is the centre of the base circle."""

    TYPE: ClassVar[str] = "cylinder"

    id: str
    diameter: float
    height: float
    position: Position = ORIGIN
    axis: str = DEFAULT_AXIS


# --- Modifier features (Sections C.3 - C.6) --------------------------------


@dataclass(frozen=True)
class ThroughHole:
    """A cylindrical cut passing completely through ``target``.

    The cut is unbounded along ``axis``, so the component of ``position`` along
    that axis has no effect on the result.
    """

    TYPE: ClassVar[str] = "through_hole"

    id: str
    target: str
    diameter: float
    position: Position
    axis: str = DEFAULT_AXIS


@dataclass(frozen=True)
class Subtract:
    """Boolean subtraction: each solid in ``tools`` is removed from ``target``.

    The tool solids are consumed -- removed from the solid set.
    """

    TYPE: ClassVar[str] = "subtract"

    id: str
    target: str
    tools: Tuple[str, ...]


@dataclass(frozen=True)
class Fillet:
    """Constant-radius rounding of the edges of ``target`` matched by ``edges``."""

    TYPE: ClassVar[str] = "fillet"

    id: str
    target: str
    radius: float
    edges: EdgeSelector


@dataclass(frozen=True)
class Chamfer:
    """Equal-distance bevelling of the edges of ``target`` matched by ``edges``."""

    TYPE: ClassVar[str] = "chamfer"

    id: str
    target: str
    distance: float
    edges: EdgeSelector


Feature = Union[Box, Cylinder, ThroughHole, Subtract, Fillet, Chamfer]


# --- Part (Section B.1) ----------------------------------------------------


@dataclass(frozen=True)
class Part:
    """A statically valid V1 part document."""

    schema_version: str
    units: str
    name: str
    features: Tuple[Feature, ...]
    description: Optional[str] = None


def feature_type(feature: Feature) -> str:
    """Return the specification ``type`` string for a typed feature."""
    return feature.TYPE


def is_constructive(feature: Feature) -> bool:
    """True if the feature adds a solid to the solid set (Section B.4)."""
    return feature.TYPE in CONSTRUCTIVE_TYPES
