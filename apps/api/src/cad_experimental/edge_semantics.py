"""Semantic edge selection: what the user meant, decided on neutral facts.

The problem this exists for, measured rather than assumed
---------------------------------------------------------
OpenCascade represents a cylindrical face's **parameterisation seam** as a
genuine straight edge. On a 100x60x10 plate with one d20 hole the kernel
reports 15 edges, and edge 8 is a 10 mm line running along the hole axis --
indistinguishable from an outer corner by curve type, by direction and by
length. So ``axis_parallel Z``, whose contract is "every straight edge
parallel to Z", matches it: four corners and the seam.

A blend cannot take a seam. The kernel accepts the edge and then builds no
contour for it, and since Stage 14.1 both consumers require complete
coverage -- a matched edge is never quietly dropped -- so the whole modifier
fails. The practical result was that the most ordinary mechanical chain
there is, *drill a plate and break its corners*, could not be built.

The seam is **topologically** different from every other edge, and that is
the fix. It is the edge that closes a periodic face: it appears twice in that
one face's traversal, and the kernel's own ``BRepTools::IsReallyClosed``
answers for it. Measured on a drilled plate:

===============  ==========  =======  ===========================
edge             curve       seam?    adjacent surfaces
===============  ==========  =======  ===========================
outer corner     line        no       plane, plane
**hole seam**    line        **yes**  **cylinder only**
hole rim         circle      no       cylinder, plane
===============  ==========  =======  ===========================

A rim is a circle; a seam is a line. So a selector that asks for circular
edges can never name a seam, and one that asks for straight edges can
exclude it by a topological fact rather than by a guess about position or
length.

Where this module sits
----------------------
Above every backend and below nothing. It takes :class:`EdgeFacts` -- plain
numbers and strings, no kernel objects -- and answers which of them a
selector names. **Nothing here imports a kernel, a backend or ``cad_core``**,
so the same answer is reachable from CadQuery, from FreeCAD or from a
hand-written fixture, and a test can exercise every rule without a solid.

Discovering the facts is a backend's job:
:meth:`cad_experimental.cad_backend.CadBackend.describe_edges`. Deciding what
they mean is this module's. That division is the whole design -- it is what
keeps OCC out of the Operation Plan and leaves room for a second engine.

Determinism
-----------
Selection order is **geometric**, not the kernel's enumeration order. Edges
sort by their defining point, axis coordinate first, then radius; the
kernel's own index is the final tie-break and settles only edges that are
geometrically coincident. So the same solid gives the same order whatever
order the kernel happened to enumerate it in.

No float is ever compared for equality. Parallelism uses
:data:`PARALLEL_TOLERANCE` on a dot product of unit vectors, and "at the same
level along an axis" uses :data:`LEVEL_TOLERANCE` in millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- the vocabulary ---------------------------------------------------------

#: Every edge of the solid. Section C.7's own selector, unchanged.
SELECT_ALL = "all"

#: Every straight edge parallel to the axis, seams INCLUDED. Section C.7's
#: other selector, unchanged and still the V1 contract's meaning. Kept so
#: that plans written before Stage 47 mean exactly what they meant, and
#: deliberately not redefined: silently changing what a selector names would
#: be worse than the problem it fixed.
SELECT_AXIS_PARALLEL = "axis_parallel"

#: Every straight edge parallel to the axis, seams EXCLUDED. What "the
#: vertical corners of this plate" means to a person, said explicitly rather
#: than by reinterpreting the selector above.
SELECT_STRAIGHT = "straight"

#: Every circular edge, optionally about one axis and optionally at one end.
#: A hole's rim; a cylinder's cap. It can never name a seam, because a seam
#: is a line.
SELECT_CIRCULAR = "circular"

SELECT_MODES: Tuple[str, ...] = (
    SELECT_ALL, SELECT_AXIS_PARALLEL, SELECT_STRAIGHT, SELECT_CIRCULAR,
)

#: Modes that take an axis, and must have one.
AXIS_REQUIRED: Tuple[str, ...] = (SELECT_AXIS_PARALLEL, SELECT_STRAIGHT)

#: Modes that may take an axis and work without one.
AXIS_OPTIONAL: Tuple[str, ...] = (SELECT_CIRCULAR,)

#: The end of the axis a selection is narrowed to. Extremal, not ordinal:
#: ``top`` is every candidate at the greatest coordinate along the axis, so
#: two holes drilled through one plate both have a top rim and both are
#: selected. Narrowing to "the single highest" would have to pick between
#: them, and picking is exactly what this layer must not do.
POSITION_TOP = "top"
POSITION_BOTTOM = "bottom"
POSITIONS: Tuple[str, ...] = (POSITION_TOP, POSITION_BOTTOM)

#: Modes that accept a position. A position needs an axis to be measured
#: along, so it is admissible only where an axis is.
POSITION_MODES: Tuple[str, ...] = (SELECT_CIRCULAR,)

#: Unsigned, as Section C.7 requires: parallelism has no direction.
AXES: Tuple[str, ...] = ("X", "Y", "Z")
AXIS_INDEX: Dict[str, int] = {"X": 0, "Y": 1, "Z": 2}

# --- what an edge is --------------------------------------------------------

LINE = "line"
CIRCLE = "circle"
OTHER = "other"
CURVES: Tuple[str, ...] = (LINE, CIRCLE, OTHER)

PLANE = "plane"
CYLINDER = "cylinder"

# --- tolerances -------------------------------------------------------------

#: Two unit vectors count as parallel when ``|dot|`` is within this of 1.
#: Unsigned by construction: the absolute value makes an edge and its reverse
#: the same direction, which is Section C.7's rule.
PARALLEL_TOLERANCE = 1e-9

#: Two points count as at the same level along an axis within this many
#: millimetres. Used only to group candidates into ends, never to decide
#: whether geometry is correct.
LEVEL_TOLERANCE = 1e-7

# --- resolution outcomes ----------------------------------------------------
#
# Machine-readable, and deliberately separate from the plan's P-codes and the
# specification's S/E codes. A resolution failure is neither a malformed plan
# nor a kernel refusal: it is a selector that named the wrong thing, or
# nothing, or could not be narrowed as asked.

#: The selector matched no edge at all. Rule E4's situation, reported here
#: with the candidates that were considered.
R1 = "R1"

#: The selection contains a parameterisation seam. No blend can take one:
#: the kernel accepts the edge, builds no contour for it and would leave it
#: silently unblended. Reported rather than dropped -- a matched edge is
#: never quietly skipped -- and reachable only through `all` or the legacy
#: `axis_parallel`, since `straight` excludes seams and `circular` cannot
#: name one.
R2 = "R2"

#: A position filter could not separate the candidates: they all sit at one
#: level along the axis, so `top` and `bottom` name the same edges. The
#: caller believes they narrowed the selection and they did not.
R3 = "R3"

RESOLUTION_CODES: Tuple[str, ...] = (R1, R2, R3)


@dataclass(frozen=True)
class EdgeFacts:
    """One edge, as plain data. No kernel object, no backend type.

    Every field is something a backend can answer about an edge without the
    caller knowing which backend it is. ``index`` is the backend's own handle
    for the edge and is opaque: this module orders by geometry and uses the
    index only to settle coincident edges.
    """

    index: int
    curve: str
    is_seam: bool = False

    #: Midpoint of the edge. Always present; the ordering key for a line.
    midpoint: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    #: Unit direction of a straight edge. ``None`` for anything else.
    direction: Optional[Tuple[float, float, float]] = None

    #: Centre of a circular edge, and the unit normal of its plane.
    centre: Optional[Tuple[float, float, float]] = None
    normal: Optional[Tuple[float, float, float]] = None
    radius: Optional[float] = None

    length: float = 0.0

    #: Neutral names of the surfaces meeting at this edge, sorted and
    #: de-duplicated: ``("cylinder", "plane")`` for a hole rim.
    adjacent: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "curve": self.curve,
            "is_seam": self.is_seam,
            "midpoint": list(self.midpoint),
            "direction": list(self.direction) if self.direction else None,
            "centre": list(self.centre) if self.centre else None,
            "normal": list(self.normal) if self.normal else None,
            "radius": self.radius,
            "length": self.length,
            "adjacent": list(self.adjacent),
        }


@dataclass(frozen=True)
class SemanticSelector:
    """A selector as the Operation Plan carries it. Plain data.

    Deliberately not the kernel's, not CadQuery's and not a string DSL: a
    mode from :data:`SELECT_MODES`, an unsigned axis letter, and an optional
    end of that axis. Nothing here can express an edge index, a face, or a
    query language, and that is the point -- the model never sees kernel
    topology.
    """

    select: str
    axis: Optional[str] = None
    position: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"select": self.select}
        if self.axis is not None:
            payload["axis"] = self.axis
        if self.position is not None:
            payload["position"] = self.position
        return payload


@dataclass(frozen=True)
class Resolution:
    """What a selector named, and why it did not name more.

    ``indices`` is what the backend should act on, in this module's own
    deterministic order. ``code`` is ``None`` when the selection is usable
    and one of :data:`RESOLUTION_CODES` otherwise; a failed resolution still
    reports its candidates, because "it matched these four and one of them
    is a seam" is a diagnostic and "it failed" is not.
    """

    indices: Tuple[int, ...] = ()
    code: Optional[str] = None
    message: str = ""
    candidates: Tuple[int, ...] = ()
    seams: Tuple[int, ...] = ()

    @property
    def ok(self) -> bool:
        return self.code is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "indices": list(self.indices),
            "code": self.code,
            "message": self.message,
            "candidates": list(self.candidates),
            "seams": list(self.seams),
        }


def is_parallel(
    vector: Optional[Sequence[float]], axis: str
) -> bool:
    """Whether a unit vector is parallel to an unsigned axis.

    Unsigned by taking the absolute value of the dot product, which is
    Section C.7's rule: an edge and its reverse have the same axis. Compared
    against a tolerance, never for equality.
    """
    if vector is None or axis not in AXIS_INDEX:
        return False
    component = vector[AXIS_INDEX[axis]]
    return abs(abs(component) - 1.0) <= PARALLEL_TOLERANCE


def _defining_point(fact: EdgeFacts) -> Tuple[float, float, float]:
    """What the edge is ordered by: a circle's centre, else its midpoint."""
    return fact.centre if fact.centre is not None else fact.midpoint


def order_key(fact: EdgeFacts, axis: Optional[str]) -> Tuple[float, ...]:
    """The deterministic sort key for one edge.

    Geometric first and the backend's index last. Ordering by the kernel's
    enumeration would make the answer depend on how the solid happened to be
    built; ordering by geometry makes it depend on the solid. The index
    settles only edges whose geometry is identical, where no geometric key
    could distinguish them anyway.
    """
    point = _defining_point(fact)
    if axis in AXIS_INDEX:
        index = AXIS_INDEX[axis]
        ordered = (point[index],) + tuple(
            value for position, value in enumerate(point) if position != index
        )
    else:
        ordered = tuple(point)
    return ordered + (fact.radius if fact.radius is not None else 0.0,
                      float(fact.index))


def _candidates(
    selector: SemanticSelector, facts: Sequence[EdgeFacts]
) -> List[EdgeFacts]:
    """The edges the selector's mode names, before any position filter."""
    mode = selector.select
    if mode == SELECT_ALL:
        return list(facts)
    if mode == SELECT_AXIS_PARALLEL:
        return [
            fact for fact in facts
            if fact.curve == LINE
            and is_parallel(fact.direction, selector.axis or "")
        ]
    if mode == SELECT_STRAIGHT:
        return [
            fact for fact in facts
            if fact.curve == LINE and not fact.is_seam
            and is_parallel(fact.direction, selector.axis or "")
        ]
    if mode == SELECT_CIRCULAR:
        return [
            fact for fact in facts
            if fact.curve == CIRCLE
            and (selector.axis is None
                 or is_parallel(fact.normal, selector.axis))
        ]
    return []


def resolve(
    selector: SemanticSelector, facts: Sequence[EdgeFacts]
) -> Resolution:
    """Which edges ``selector`` names, deterministically.

    Total: never raises. A selector this module does not know names nothing
    and says so, because a resolver that threw would be unusable for exactly
    the diagnostics it exists to produce.
    """
    if selector.select not in SELECT_MODES:
        return Resolution(
            code=R1,
            message=(
                f"unknown selector {selector.select!r}; this stage implements "
                f"{', '.join(SELECT_MODES)}"
            ),
        )

    chosen = sorted(
        _candidates(selector, facts),
        key=lambda fact: order_key(fact, selector.axis),
    )
    considered = tuple(fact.index for fact in chosen)

    if selector.position is not None:
        chosen, problem = _narrow(chosen, selector)
        if problem is not None:
            return Resolution(
                code=R3, message=problem, candidates=considered
            )

    if not chosen:
        return Resolution(
            code=R1,
            message=_no_match_message(selector, facts),
            candidates=considered,
        )

    seams = tuple(fact.index for fact in chosen if fact.is_seam)
    if seams:
        return Resolution(
            code=R2,
            message=(
                f"the selection includes {len(seams)} parameterisation "
                "seam(s), which no blend can take: the kernel accepts the "
                "edge, builds no contour for it and would leave it silently "
                "unblended. A seam is a cylindrical face's own closing edge, "
                f"not an edge of the part. Use {SELECT_STRAIGHT!r} for the "
                f"straight edges or {SELECT_CIRCULAR!r} for the rims"
            ),
            candidates=considered,
            seams=seams,
        )

    return Resolution(
        indices=tuple(fact.index for fact in chosen),
        candidates=considered,
        seams=(),
    )


def _narrow(
    chosen: List[EdgeFacts], selector: SemanticSelector
) -> Tuple[List[EdgeFacts], Optional[str]]:
    """Keep the candidates at one end of the axis, or say why it cannot."""
    if not chosen:
        return chosen, None
    index = AXIS_INDEX[selector.axis]
    levels = [_defining_point(fact)[index] for fact in chosen]
    lowest, highest = min(levels), max(levels)
    if highest - lowest <= LEVEL_TOLERANCE:
        return chosen, (
            f"every candidate lies at the same {selector.axis} level "
            f"({highest:g}), so {POSITION_TOP!r} and {POSITION_BOTTOM!r} name "
            "the same edges. Drop `position`, or select along an axis the "
            "candidates are actually spread along"
        )
    wanted = highest if selector.position == POSITION_TOP else lowest
    return (
        [
            fact for fact in chosen
            if abs(_defining_point(fact)[index] - wanted) <= LEVEL_TOLERANCE
        ],
        None,
    )


def _no_match_message(
    selector: SemanticSelector, facts: Sequence[EdgeFacts]
) -> str:
    """Why nothing matched, in terms of what the solid actually has."""
    census: Dict[str, int] = {}
    for fact in facts:
        key = "seam" if fact.is_seam else fact.curve
        census[key] = census.get(key, 0) + 1
    present = ", ".join(f"{count} {name}" for name, count in sorted(census.items()))
    described = selector.select
    if selector.axis:
        described += f"/{selector.axis}"
    if selector.position:
        described += f"/{selector.position}"
    return (
        f"the selector {described} matched no edge; this solid has "
        f"{present or 'no edges'}"
    )


__all__ = [
    "AXES",
    "AXIS_INDEX",
    "AXIS_OPTIONAL",
    "AXIS_REQUIRED",
    "CIRCLE",
    "CURVES",
    "CYLINDER",
    "EdgeFacts",
    "LEVEL_TOLERANCE",
    "LINE",
    "OTHER",
    "PARALLEL_TOLERANCE",
    "PLANE",
    "POSITIONS",
    "POSITION_BOTTOM",
    "POSITION_MODES",
    "POSITION_TOP",
    "R1",
    "R2",
    "R3",
    "RESOLUTION_CODES",
    "Resolution",
    "SELECT_ALL",
    "SELECT_AXIS_PARALLEL",
    "SELECT_CIRCULAR",
    "SELECT_MODES",
    "SELECT_STRAIGHT",
    "SemanticSelector",
    "is_parallel",
    "order_key",
    "resolve",
]
