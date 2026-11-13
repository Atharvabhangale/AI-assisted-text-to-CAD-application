"""Typed representation of the V1 CAD specification.

Mirrors ``docs/cad-specification.md`` (schema version 1.2.0) one-to-one.  These
types describe a specification document; they contain no geometry and no
geometric behaviour.

Instances are produced by :func:`cad_core.validator.validate` only for a
document that passes static validation, so a constructed :class:`Part` is a
statically valid part.  Optional parameters carry the defaults from the
Section C tables, materialised at construction time: an omitted ``position``
becomes :data:`ORIGIN`, an omitted ``axis`` becomes :data:`DEFAULT_AXIS`, an
omitted ``top_diameter`` becomes the cylinder's own ``diameter``, and the
pattern parameters of ``subtract``/``union`` take :data:`PATTERN_DEFAULTS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Optional, Tuple, Union

# --- Contract constants (Sections A, B, C, F) -------------------------------

#: Schema version described by docs/cad-specification.md.
#:
#: ``1.1.0`` added the ``union`` feature type -- a backward-compatible MINOR
#: change under Section F. Every ``1.0.0`` document is still valid, means what
#: it meant, and keeps its canonical bytes and hash: a document's own
#: ``schema_version`` is data, and nothing rewrites it to this value.
#:
#: ``1.2.0`` added optional parameters only -- ``cylinder.top_diameter`` and
#: ``cylinder.sides``, and the four ``pattern_*`` parameters of ``subtract``
#: and ``union`` (:data:`PARAMETER_INTRODUCED_IN`). Every default preserves
#: the ``1.1.1`` meaning, so this too is a MINOR change.
SCHEMA_VERSION = "1.2.0"

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

#: A ``schema_version`` string (Section F, rule S4).
VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
_VERSION_RE = re.compile(VERSION_PATTERN)

#: Required and optional root fields (Section B.1, rules S1 and S3).
REQUIRED_ROOT_FIELDS: Tuple[str, ...] = ("schema_version", "units", "name", "features")
OPTIONAL_ROOT_FIELDS: Tuple[str, ...] = ("description",)

#: Fields every feature carries (Section B.3).
COMMON_FEATURE_FIELDS: Tuple[str, ...] = ("id", "type")

#: Constructive features add a solid to the solid set (Section B.4).
CONSTRUCTIVE_TYPES: Tuple[str, ...] = ("box", "cylinder")

#: Modifier features replace their target in place (Section B.4).
MODIFIER_TYPES: Tuple[str, ...] = (
    "through_hole",
    "subtract",
    "fillet",
    "chamfer",
    "union",
)

#: All V1 feature types (Scope of V1, rule S3).
FEATURE_TYPES: Tuple[str, ...] = CONSTRUCTIVE_TYPES + MODIFIER_TYPES

#: The schema version that introduced a feature type, as ``(major, minor)``,
#: for every type that is not part of ``1.0.0`` (Section F, rule S3). A type
#: absent from this table has existed since ``1.0.0``.
#:
#: A document is validated against the version *it declares*: a document that
#: declares ``1.0.0`` and uses ``union`` is rejected under rule S3, exactly as
#: a ``1.0.0`` consumer rejects it, so a document's validity never depends on
#: which consumer happens to read it.
FEATURE_INTRODUCED_IN: Mapping[str, Tuple[int, int]] = {
    "union": (1, 1),
}

#: The schema version that introduced a feature *parameter*, as
#: ``(major, minor)``, keyed by ``(feature type, parameter)``, for every
#: parameter that is not part of the version its feature type was introduced
#: in (Section F, rule S3).
#:
#: The same rule as for feature types: a document declaring an earlier version
#: that carries one of these parameters is rejected under S3 (an unknown
#: field), and the canonical form of such a document never contains them, so
#: its bytes and hash are unchanged by this version's existence.
PARAMETER_INTRODUCED_IN: Mapping[Tuple[str, str], Tuple[int, int]] = {
    ("cylinder", "top_diameter"): (1, 2),
    ("cylinder", "sides"): (1, 2),
    ("subtract", "pattern_count"): (1, 2),
    ("subtract", "pattern_axis"): (1, 2),
    ("subtract", "pattern_angle"): (1, 2),
    ("subtract", "pattern_step"): (1, 2),
    ("union", "pattern_count"): (1, 2),
    ("union", "pattern_axis"): (1, 2),
    ("union", "pattern_angle"): (1, 2),
    ("union", "pattern_step"): (1, 2),
}

#: ``cylinder.sides`` (Section C.2, rule S24): ``0`` means round; otherwise a
#: regular polygon with this many sides, from :data:`POLYGON_SIDES_MIN` to
#: :data:`POLYGON_SIDES_MAX` inclusive.
ROUND_SIDES = 0
POLYGON_SIDES_MIN = 3
POLYGON_SIDES_MAX = 12

#: The pattern parameters of ``subtract`` and ``union`` (Sections C.4, C.8),
#: in their canonical order.
PATTERN_PARAMETERS: Tuple[str, ...] = (
    "pattern_count",
    "pattern_axis",
    "pattern_angle",
    "pattern_step",
)

#: Their defaults. With these, the single copy of each tool is the tool
#: itself, so a pattern-free subtract or union means what it meant in 1.1.1.
PATTERN_DEFAULTS: Mapping[str, Any] = {
    "pattern_count": 1,
    "pattern_axis": "Z",
    "pattern_angle": 0.0,
    "pattern_step": 0.0,
}

#: ``pattern_count`` range (rule S25).
PATTERN_COUNT_MIN = 1
PATTERN_COUNT_MAX = 64

#: ``pattern_axis`` values (rule S26): the unsigned axis letters, as for the
#: ``axis_parallel`` edge selector. The pattern axis is a line through the
#: origin, so it has no direction of its own; the sign of a turn is carried by
#: ``pattern_angle`` and the sign of a shift by ``pattern_step``.
PATTERN_AXIS_VALUES: Tuple[str, ...] = ("X", "Y", "Z")

#: Largest ``|pattern_angle|``, in degrees (rule S27).
PATTERN_ANGLE_LIMIT_DEG = 360.0

#: Per-type parameters from the Section C tables, excluding the common ``id``
#: and ``type`` fields, as ``(required, optional)``.
#:
#: This is the table of the *current* version. Parameters a declared version
#: predates are listed in :data:`PARAMETER_INTRODUCED_IN`; use
#: :func:`feature_parameters` for the table a given document may use.
FEATURE_PARAMETERS: Mapping[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "box": (("size",), ("position",)),
    "cylinder": (
        ("diameter", "height"),
        ("position", "axis", "top_diameter", "sides"),
    ),
    "through_hole": (("target", "diameter", "position"), ("axis",)),
    "subtract": (("target", "tools"), PATTERN_PARAMETERS),
    "fillet": (("target", "radius", "edges"), ()),
    "chamfer": (("target", "distance", "edges"), ()),
    "union": (("target", "tools"), PATTERN_PARAMETERS),
}


def parse_schema_version(schema_version: str) -> Optional[Tuple[int, int]]:
    """``(major, minor)`` of a ``MAJOR.MINOR.PATCH`` string, or None if malformed."""
    match = _VERSION_RE.match(schema_version) if isinstance(schema_version, str) else None
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def parameter_available(
    feature_type: str, parameter: str, declared: Optional[Tuple[int, int]]
) -> bool:
    """True if a document declaring ``declared`` may use ``parameter``.

    ``declared`` of None means the version is unknown (missing or malformed,
    already reported elsewhere); nothing is then judged against it, exactly as
    for feature types.
    """
    introduced = PARAMETER_INTRODUCED_IN.get((feature_type, parameter))
    return introduced is None or declared is None or declared >= introduced


def feature_parameters(
    feature_type: str, declared: Optional[Tuple[int, int]]
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """``(required, optional)`` parameters of a type, in a declared version.

    The rows of :data:`FEATURE_PARAMETERS` with every parameter the declared
    version predates removed. The canonical form emits exactly these.
    """
    required, optional = FEATURE_PARAMETERS[feature_type]
    return (
        tuple(p for p in required if parameter_available(feature_type, p, declared)),
        tuple(p for p in optional if parameter_available(feature_type, p, declared)),
    )

#: Reference-carrying parameters per feature type (Section B.3, rules S6, S7).
FEATURE_REFERENCE_FIELDS: Mapping[str, Tuple[str, ...]] = {
    "through_hole": ("target",),
    "subtract": ("target", "tools"),
    "fillet": ("target",),
    "chamfer": ("target",),
    "union": ("target", "tools"),
}

#: Modifiers that consume their ``tools``: each listed solid is removed from
#: the solid set once the feature has been applied (Section B.4).
TOOL_CONSUMING_TYPES: Tuple[str, ...] = ("subtract", "union")


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
    """A cylinder, cone frustum, cone, polygon prism or pyramid frustum.

    ``position`` is the centre of the base. ``diameter`` is the base circle
    (or the circle through the base polygon's corners) and ``top_diameter``
    the same at the far end, ``height`` along ``axis``; ``top_diameter``
    defaults to ``diameter`` -- a right circular cylinder. ``sides`` is ``0``
    for round, else the number of sides of a regular polygon (Section C.2,
    from schema version ``1.2.0``).
    """

    TYPE: ClassVar[str] = "cylinder"

    id: str
    diameter: float
    height: float
    position: Position = ORIGIN
    axis: str = DEFAULT_AXIS
    top_diameter: Optional[float] = None
    sides: int = ROUND_SIDES

    def __post_init__(self) -> None:
        # The default is "equal to diameter", which a dataclass default cannot
        # express; it is materialised here so an omitted and an explicit
        # top_diameter equal to diameter are the same value.
        if self.top_diameter is None:
            object.__setattr__(self, "top_diameter", self.diameter)

    @property
    def is_round(self) -> bool:
        return self.sides == ROUND_SIDES

    @property
    def is_tapered(self) -> bool:
        return self.top_diameter != self.diameter


# --- Modifier features (Sections C.3 - C.6, C.8) ---------------------------


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

    The tool solids are consumed -- removed from the solid set. From schema
    version ``1.2.0`` each tool is first replaced by its ``pattern_count``
    copies (Section C.4).
    """

    TYPE: ClassVar[str] = "subtract"

    id: str
    target: str
    tools: Tuple[str, ...]
    pattern_count: int = 1
    pattern_axis: str = "Z"
    pattern_angle: float = 0.0
    pattern_step: float = 0.0

    @property
    def is_patterned(self) -> bool:
        """False exactly when each tool's one copy is the tool itself."""
        return not (
            self.pattern_count == 1
            and self.pattern_angle == 0.0
            and self.pattern_step == 0.0
        )


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


@dataclass(frozen=True)
class UnionFeature:
    """Boolean union: each solid in ``tools`` is fused into ``target``.

    The result keeps the target's id; the tool solids are consumed -- removed
    from the solid set. Introduced in schema version ``1.1.0`` (Section C.8);
    the pattern parameters, as for ``subtract``, in ``1.2.0``.

    Named ``UnionFeature`` rather than ``Union`` so it can never shadow
    :data:`typing.Union`; its specification ``type`` string is ``"union"``.
    """

    TYPE: ClassVar[str] = "union"

    id: str
    target: str
    tools: Tuple[str, ...]
    pattern_count: int = 1
    pattern_axis: str = "Z"
    pattern_angle: float = 0.0
    pattern_step: float = 0.0

    @property
    def is_patterned(self) -> bool:
        """False exactly when each tool's one copy is the tool itself."""
        return not (
            self.pattern_count == 1
            and self.pattern_angle == 0.0
            and self.pattern_step == 0.0
        )


Feature = Union[Box, Cylinder, ThroughHole, Subtract, Fillet, Chamfer, UnionFeature]


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
