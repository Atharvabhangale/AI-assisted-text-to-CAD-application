"""Stage 48: the capability-aware corpus. Thirty cases, frozen before use.

Stage 40's thirteen-case corpus asked one question -- *is the operation plan
easier for a model to generate correctly than the V1 document?* -- against a
vocabulary of nine types of which six were buildable and none were reachable
by a grammar-constrained decoder beyond those six. Stages 44-47 changed what
the language can say: profile operations became model-addressable, `pattern`
arrived, edge selection gained `straight` and `circular`, and chains grew a
real dependency graph. **None of that is exercised by the Stage 40 corpus**,
which is why re-running Stage 43 today would measure nothing about it.

This module holds the cases and the expectations, and nothing else -- no
model call, no scoring, no analysis. :mod:`stage48_capability_evaluation`
does that.

Two groups, and why they are separate
-------------------------------------
``LEGACY`` is the thirteen Stage 40 requests, **taken from the frozen corpus
object itself** rather than retyped: :data:`LEGACY_CASE_IDS` names them and
each one's text, geometry and required features are read out of
``comparison_corpus.CASES`` at import. They therefore cannot drift, and a
test asserts the identity. Both representations answer these, because both
can express them.

``CAPABILITY`` is seventeen requests that exercise what Stages 44-47 added.
They are **operation-plan only**, and not because V1 is being spared: V1 has
no pattern, no semantic selector and no sketch, so asking it these questions
would measure the vocabulary gap that Stage 43 already measured, dressed up
as a model result. Where a V1 answer would be interesting it is already in
the legacy group.

**The legacy group is not a continuation of Stage 43's numbers.** The prompt
moved (`2026-09-10.7` to this branch's current one), the plan schema moved
(`executable_schema` to `provider_schema`), and Stage 48 scores a semantic
selector as buildable where Stage 43's runner would have called it
incorrect. Same cases and same scoring *definitions*; different instrument.
What the legacy group buys is a like-for-like V1-vs-plan comparison **within
Stage 48**, not a delta against Stage 43. See the Stage 48 section of
``docs/experimental-operation-plan.md``.

Freeze discipline
-----------------
Every expectation here was written from the request text and from the
frozen vocabulary before any live model call was made. Nothing may be edited
after a result is observed. A case the model fails is a finding.

Expected geometry is a **closed form** computed from the request's own
numbers -- never a measurement recorded as its own expectation. Every one of
them was checked against the real CadQuery kernel before this file was
committed, by building :attr:`CapabilityCase.reference_plan` and comparing;
:func:`cad_experimental.stage48_capability_evaluation.preflight` re-runs that
check for free, so a wrong expectation is caught before a paid run rather
than by scoring every attempt incorrect.

A reference plan is **not an answer key**. It never reaches the model: it is
not in the prompt, not in the schema, and not in any request. It exists so
the instrument can prove offline that the expectation it will score against
is reachable at all. A test asserts that no reference plan text is sent to a
provider.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .comparison_corpus import (
    BOUNDING_BOX_ATOL,
    CASES_BY_ID as LEGACY_CASES_BY_ID,
    EXPECT_BUILD,
    EXPECT_UNSUPPORTED,
    EXPECT_VALID_UNEXECUTABLE,
    VOLUME_RTOL,
    ExpectedGeometry,
)

#: This corpus's version. Bumping it means a different measurement
#: instrument, and a result recorded under one version may never be merged
#: with a result recorded under another.
STAGE48_CORPUS_VERSION = "1.0.0"

# --- the expectation classes -----------------------------------------------
#
# The first three are Stage 40's own constants, imported rather than
# redefined so that a legacy case means exactly what it meant there. The
# last two are new, and each exists because a Stage 48 case needs an answer
# neither of the others describes.

#: A required value is genuinely absent and has no default. The only correct
#: answer is to ask. Producing a part means inventing a dimension.
EXPECT_CLARIFICATION = "clarification"

#: The request names a part that cannot exist -- a hole wider than the stock
#: it passes through. Nothing is missing and the vocabulary can say it, so
#: neither ``EXPECT_CLARIFICATION`` nor ``EXPECT_UNSUPPORTED`` fits; what is
#: required is that the model does **not** answer with a plan claiming to
#: build it.
#:
#: Deliberately lenient between the two refusal words: the language gives no
#: basis to prefer "unsupported" over "needs_clarification" here, so scoring
#: one of them wrong would be scoring a coin toss. It is **not** lenient
#: about producing a plan, which is always incorrect for these cases.
EXPECT_NO_PART = "no_part"

EXPECTATIONS: Tuple[str, ...] = (
    EXPECT_BUILD,
    EXPECT_VALID_UNEXECUTABLE,
    EXPECT_UNSUPPORTED,
    EXPECT_CLARIFICATION,
    EXPECT_NO_PART,
)

#: The declared outcomes each expectation accepts as correct. Read by the
#: scorer; stated here so the corpus, not the scorer, owns what "correct"
#: means for a refusal.
ACCEPTED_REFUSALS: Mapping[str, Tuple[str, ...]] = {
    EXPECT_UNSUPPORTED: ("unsupported",),
    EXPECT_CLARIFICATION: ("needs_clarification",),
    EXPECT_NO_PART: ("unsupported", "needs_clarification"),
}

# --- the two result groups -------------------------------------------------

#: Answered by **both** representations. The shared, legitimately comparable
#: subset within this run.
LEGACY = "legacy"

#: Answered by the operation plan **only**. What Stages 44-47 added.
CAPABILITY = "capability"

GROUPS: Tuple[str, ...] = (LEGACY, CAPABILITY)

# --- categories, as the brief names them -----------------------------------

A_PRIMITIVES = "A-primitives"
B_MODIFIERS = "B-modifiers"
C_PROFILES = "C-profiles"
D_CHAINS = "D-chains"
E_PATTERN = "E-pattern"
F_SELECTORS = "F-selectors"
G_GRAPH = "G-graph"
H_UNSUPPORTED = "H-unsupported"
I_INVALID = "I-invalid"

CATEGORIES: Tuple[str, ...] = (
    A_PRIMITIVES, B_MODIFIERS, C_PROFILES, D_CHAINS, E_PATTERN,
    F_SELECTORS, G_GRAPH, H_UNSUPPORTED, I_INVALID,
)

# --- closed forms ----------------------------------------------------------
#
# Every one of these is arithmetic on the request's own numbers. None is a
# kernel measurement. All were checked against CadQuery before committing --
# see the module docstring and `preflight`.

_PLATE = 100.0 * 60.0 * 10.0
_LONG_PLATE = 200.0 * 60.0 * 10.0


def _through(diameter: float, depth: float) -> float:
    """The material a round hole of ``diameter`` removes from ``depth``."""
    return math.pi * (diameter / 2.0) ** 2 * depth


def _bevel(distance: float) -> float:
    """A chamfer removes a right triangle of this area per unit of edge."""
    return distance ** 2 / 2.0


def _corner(radius: float) -> float:
    """A fillet removes this area per unit of a convex right-angled edge."""
    return radius ** 2 * (1.0 - math.pi / 4.0)


def _rim(radius: float, distance: float) -> float:
    """A chamfer of ``distance`` on a circular rim of ``radius``.

    The swept right triangle about the axis, by Pappus:
    ``2*pi*(R + d/3) * d^2/2`` -- the centroid of the triangle sits ``d/3``
    from the rim. Verified against the kernel to 1 ULP.
    """
    return 2.0 * math.pi * (radius + distance / 3.0) * _bevel(distance)


_HOLE_D20 = _through(20.0, 10.0)
_HOLE_D10 = _through(10.0, 10.0)
_HOLE_D16 = _through(16.0, 10.0)
_HOLE_D8 = _through(8.0, 10.0)
_HOLE_D6 = _through(6.0, 10.0)


# --- one case --------------------------------------------------------------


@dataclass(frozen=True)
class SelectorExpectation:
    """The edge selector a correct answer must use.

    Present only where the request names a kind of edge that the geometry
    alone cannot distinguish. A plate's top and bottom hole rims chamfer to
    **the same volume**, so "the top edge of the hole" is scorable only by
    reading the selector the model wrote. Where geometry already decides,
    this is absent and nothing about the selector is required.
    """

    #: The operation type carrying the selector.
    operation_type: str

    #: Acceptable ``select`` modes. More than one where more than one is
    #: genuinely correct -- `axis_parallel X` and `straight X` name the same
    #: edges on a part whose only cylindrical face is parallel to Z.
    select: Tuple[str, ...]

    #: The required ``position``, or ``None`` where the request does not
    #: narrow the selection to one end.
    position: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_type": self.operation_type,
            "select": list(self.select),
            "position": self.position,
        }


@dataclass(frozen=True)
class CapabilityCase:
    """One request, and what a correct operation-plan answer looks like."""

    identifier: str
    group: str
    category: str
    text: str

    #: What a correct plan answer is. One of :data:`EXPECTATIONS`.
    expect_plan: str

    #: What a correct V1 answer is, for the legacy group. ``None`` for a
    #: capability case, which V1 is never asked.
    expect_v1: Optional[str] = None

    #: The geometry a correct answer builds, as a closed form. ``None`` for
    #: a refusal and for an unexecutable plan.
    geometry: Optional[ExpectedGeometry] = None

    #: Operation types a correct answer must use, where the request pins
    #: them. Empty where more than one expression is legitimate.
    required_plan_operations: Tuple[str, ...] = ()

    #: Feature types a correct V1 answer must use. Legacy group only.
    required_v1_features: Tuple[str, ...] = ()

    #: The selector a correct answer must use, where geometry cannot tell.
    selector: Optional[SelectorExpectation] = None

    #: A developer-written plan that is **one** correct answer to this
    #: request. Never sent to a model; used offline to prove the expectation
    #: is reachable. See the module docstring.
    reference_plan: Optional[Mapping[str, Any]] = None

    #: Why this case is here.
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.identifier,
            "group": self.group,
            "category": self.category,
            "text": self.text,
            "expect_plan": self.expect_plan,
            "expect_v1": self.expect_v1,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "required_plan_operations": list(self.required_plan_operations),
            "required_v1_features": list(self.required_v1_features),
            "selector": self.selector.to_dict() if self.selector else None,
            "reference_plan": (
                json.loads(json.dumps(self.reference_plan, sort_keys=True))
                if self.reference_plan is not None else None
            ),
            "note": self.note,
        }


# --- reference-plan helpers -------------------------------------------------
#
# Small constructors so a reference plan reads as the part it describes. They
# build plain dictionaries -- the same wire shape a model would emit -- and
# nothing here is ever sent to a provider.


def _plan(summary: str, *operations: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "status": "generated",
        "summary": summary,
        "operations": list(operations),
    }


def _box(identifier: str, x: float, y: float, z: float) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "box",
        "parameters": {"x": x, "y": y, "z": z},
    }


def _cyl(
    identifier: str, diameter: float, height: float,
    position: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    parameters: Dict[str, Any] = {"diameter": diameter, "height": height}
    if position is not None:
        parameters["position"] = dict(position)
    return {"id": identifier, "type": "cylinder", "parameters": parameters}


def _hole(
    identifier: str, target: str, diameter: float, x: float, y: float,
) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "through_hole", "target": target,
        "parameters": {
            "diameter": diameter, "position": {"x": x, "y": y, "z": 0.0},
        },
    }


def _edges(
    select: str, axis: Optional[str] = None, position: Optional[str] = None,
) -> Dict[str, Any]:
    selector: Dict[str, Any] = {"select": select}
    if axis is not None:
        selector["axis"] = axis
    if position is not None:
        selector["position"] = position
    return selector


def _fillet(
    identifier: str, target: str, radius: float, edges: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "fillet", "target": target,
        "parameters": {"radius": radius, "edges": dict(edges)},
    }


def _chamfer(
    identifier: str, target: str, distance: float, edges: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "chamfer", "target": target,
        "parameters": {"distance": distance, "edges": dict(edges)},
    }


def _subtract(
    identifier: str, target: str, tools: Tuple[str, ...],
) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "subtract", "target": target,
        "tools": list(tools),
    }


def _pattern(
    identifier: str, source: str, count: int, placement: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "id": identifier, "type": "pattern", "source": source,
        "parameters": {"count": count, "placement": dict(placement)},
    }


def _radial(axis: str, x: float, y: float, z: float = 0.0) -> Dict[str, Any]:
    return {
        "kind": "radial", "axis": axis,
        "centre": {"x": x, "y": y, "z": z},
    }


def _linear(axis: str, spacing: float) -> Dict[str, Any]:
    return {"kind": "linear", "axis": axis, "spacing": spacing}


# --- the legacy group, read out of the frozen Stage 40 corpus ---------------

#: The thirteen Stage 40 cases, in Stage 40's own order. Named here; their
#: text and expectations are read from the frozen object, never retyped.
LEGACY_CASE_IDS: Tuple[str, ...] = (
    "01-plate-worded", "02-cylinder-axis", "03-box-dimensional",
    "04-plate-centre-hole", "05-plate-two-holes", "06-cube-bore",
    "07-plate-chamfer", "08-plate-fillet",
    "09-profile-extrude", "10-profile-revolve",
    "11-sphere", "12-union", "13-engine",
)

#: Which Stage 48 category each legacy case belongs to. The Stage 40 corpus
#: has its own five categories; this maps them onto the brief's, without
#: touching the frozen file.
_LEGACY_CATEGORIES: Mapping[str, str] = {
    "01-plate-worded": A_PRIMITIVES,
    "02-cylinder-axis": A_PRIMITIVES,
    "03-box-dimensional": A_PRIMITIVES,
    "04-plate-centre-hole": B_MODIFIERS,
    "05-plate-two-holes": B_MODIFIERS,
    "06-cube-bore": B_MODIFIERS,
    "07-plate-chamfer": B_MODIFIERS,
    "08-plate-fillet": B_MODIFIERS,
    "09-profile-extrude": C_PROFILES,
    "10-profile-revolve": C_PROFILES,
    "11-sphere": H_UNSUPPORTED,
    "12-union": H_UNSUPPORTED,
    "13-engine": H_UNSUPPORTED,
}

#: A reference plan for each legacy case that builds. Written here rather
#: than in the frozen corpus, which has none and must not gain one.
_LEGACY_REFERENCE_PLANS: Mapping[str, Dict[str, Any]] = {
    "01-plate-worded": _plan("plate", _box("plate", 100.0, 60.0, 10.0)),
    "02-cylinder-axis": _plan("rod", _cyl("rod", 20.0, 50.0)),
    "03-box-dimensional": _plan("plate", _box("plate", 100.0, 60.0, 10.0)),
    "04-plate-centre-hole": _plan(
        "plate with a centre hole",
        _box("plate", 100.0, 60.0, 10.0),
        _hole("bore", "plate", 20.0, 50.0, 30.0),
    ),
    "05-plate-two-holes": _plan(
        "plate with two holes",
        _box("plate", 100.0, 60.0, 10.0),
        _hole("left", "plate", 16.0, 25.0, 30.0),
        _hole("right", "plate", 16.0, 75.0, 30.0),
    ),
    "06-cube-bore": _plan(
        "bored cube",
        _box("cube", 50.0, 50.0, 50.0),
        _hole("bore", "cube", 20.0, 25.0, 25.0),
    ),
    "07-plate-chamfer": _plan(
        "chamfered plate",
        _box("plate", 100.0, 60.0, 10.0),
        _chamfer("break", "plate", 2.0, _edges("axis_parallel", "Z")),
    ),
    "08-plate-fillet": _plan(
        "filleted plate",
        _box("plate", 100.0, 60.0, 10.0),
        _fillet("round", "plate", 2.0, _edges("axis_parallel", "Z")),
    ),
}


def _legacy_case(identifier: str) -> CapabilityCase:
    """One Stage 40 case, re-expressed as a Stage 48 case.

    Every scoring-relevant field is **read from the frozen corpus object**:
    the text, both expectations, the geometry and both required-type
    tuples. Only the group, the category and the reference plan are added
    here, and none of the three can change what a correct answer is.
    """
    frozen = LEGACY_CASES_BY_ID[identifier]
    return CapabilityCase(
        identifier=identifier,
        group=LEGACY,
        category=_LEGACY_CATEGORIES[identifier],
        text=frozen.text,
        expect_plan=frozen.expect_plan,
        expect_v1=frozen.expect_v1,
        geometry=frozen.geometry,
        required_plan_operations=frozen.required_plan_operations,
        required_v1_features=frozen.required_v1_features,
        reference_plan=_LEGACY_REFERENCE_PLANS.get(identifier),
        note=frozen.note,
    )


LEGACY_CASES: Tuple[CapabilityCase, ...] = tuple(
    _legacy_case(identifier) for identifier in LEGACY_CASE_IDS
)


# --- the capability group ---------------------------------------------------
#
# Seventeen requests, operation-plan only. Each exercises something the
# Stage 40 corpus cannot reach.

CAPABILITY_CASES: Tuple[CapabilityCase, ...] = (
    # --- B: the modifier the legacy group leaves unpinned -----------------
    CapabilityCase(
        identifier="B6-plate-subtract-cylinder",
        group=CAPABILITY,
        category=B_MODIFIERS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate. Create a 20 mm "
            "diameter cylinder 10 mm tall standing at the centre of the "
            "plate, and subtract that cylinder from the plate."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(_PLATE - _HOLE_D20, 100.0, 60.0, 10.0),
        required_plan_operations=("box", "cylinder", "subtract"),
        reference_plan=_plan(
            "plate with a subtracted cylinder",
            _box("plate", 100.0, 60.0, 10.0),
            _cyl("tool", 20.0, 10.0, {"x": 50.0, "y": 30.0, "z": 0.0}),
            _subtract("cut", "plate", ("tool",)),
        ),
        note=(
            "Stage 40's case 6 accepts a drill OR a subtract, so `subtract` "
            "is never actually required anywhere in that corpus. This "
            "request names the tool-and-subtract construction explicitly, "
            "so the operation is pinned and the consumed reference is real."
        ),
    ),
    # --- D: chains ---------------------------------------------------------
    CapabilityCase(
        identifier="D1-plate-hole-chamfer-long-edges",
        group=CAPABILITY,
        category=D_CHAINS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "hole through the centre, then chamfer the four long edges by "
            "2 mm."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - 4.0 * 100.0 * _bevel(2.0),
            100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "chamfer"),
        selector=SelectorExpectation(
            "chamfer", ("straight", "axis_parallel"), None,
        ),
        reference_plan=_plan(
            "drilled plate, long edges chamfered",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _chamfer("break", "plate", 2.0, _edges("straight", "X")),
        ),
        note=(
            "box -> hole -> chamfer: a three-deep chain on one body, the "
            "shortest that has a modifier acting on an already-modified "
            "solid. The long edges run along X, and the bore's seam runs "
            "along Z, so either selector names the same four edges here -- "
            "which is why both are accepted."
        ),
    ),
    CapabilityCase(
        identifier="D2-profile-extrude-hole",
        group=CAPABILITY,
        category=D_CHAINS,
        text=(
            "Create a rectangular 80 mm by 40 mm profile on the XY plane, "
            "extrude it 12 mm, and put a 10 mm diameter hole through the "
            "middle of the result."
        ),
        expect_plan=EXPECT_VALID_UNEXECUTABLE,
        required_plan_operations=("sketch", "extrude", "through_hole"),
        note=(
            "profile -> extrude -> hole. The chain the schema could not "
            "express before Stage 44 and the engine still cannot build. A "
            "valid plan is the correct answer; `ExecutionUnsupported` is "
            "the correct refusal, and it comes from the adapter, not the "
            "model."
        ),
    ),
    CapabilityCase(
        identifier="D3-profile-revolve-fillet",
        group=CAPABILITY,
        category=D_CHAINS,
        text=(
            "Create a 30 mm by 10 mm rectangular profile on the XZ plane "
            "with its near corner 20 mm from the origin along X, revolve it "
            "a full turn about the Z axis, and fillet the resulting edges "
            "with a 1 mm radius."
        ),
        expect_plan=EXPECT_VALID_UNEXECUTABLE,
        required_plan_operations=("sketch", "revolve", "fillet"),
        note=(
            "profile -> revolve -> modifier, and a ring rather than Stage "
            "40's degenerate straddling circle: the profile is clear of the "
            "axis, so the only reason this cannot be built is the missing "
            "execution path."
        ),
    ),
    # --- E: pattern --------------------------------------------------------
    CapabilityCase(
        identifier="E1-bolt-circle-radial",
        group=CAPABILITY,
        category=E_PATTERN,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "hole through the centre, and four 6 mm diameter holes through "
            "the plate spaced evenly on a 40 mm diameter bolt circle around "
            "that centre."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - 4.0 * _HOLE_D6, 100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "pattern"),
        reference_plan=_plan(
            "plate, centre bore, four holes on a bolt circle",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _hole("mount", "plate", 6.0, 70.0, 30.0),
            _pattern("mounts", "mount", 4, _radial("+Z", 50.0, 30.0)),
        ),
        note=(
            "The canonical radial pattern. `pattern` is required: four "
            "hand-placed holes build the same solid, and the prompt tells "
            "the model not to write them out, so choosing repetition is "
            "the capability under test rather than the geometry."
        ),
    ),
    CapabilityCase(
        identifier="E2-hole-row-linear",
        group=CAPABILITY,
        category=E_PATTERN,
        text=(
            "Create a 200 mm by 60 mm by 10 mm plate with five 8 mm "
            "diameter holes through it in a row, the first 40 mm from the "
            "left-hand edge and the rest at a 30 mm pitch, all on the "
            "centreline."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _LONG_PLATE - 5.0 * _HOLE_D8, 200.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "pattern"),
        reference_plan=_plan(
            "long plate with a row of five holes",
            _box("plate", 200.0, 60.0, 10.0),
            _hole("hole", "plate", 8.0, 40.0, 30.0),
            _pattern("holes", "hole", 5, _linear("+X", 30.0)),
        ),
        note=(
            "The other placement kind. `count` includes the source, so 5 "
            "means four repeats -- the off-by-one the prompt warns about, "
            "and a wrong count changes the volume, so geometry catches it."
        ),
    ),
    # --- F: semantic edge selection ---------------------------------------
    CapabilityCase(
        identifier="F1-drilled-plate-round-corners",
        group=CAPABILITY,
        category=F_SELECTORS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "hole through the centre, then round the four outside corners "
            "with a 5 mm radius."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - 4.0 * 10.0 * _corner(5.0),
            100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "fillet"),
        selector=SelectorExpectation("fillet", ("straight",), None),
        reference_plan=_plan(
            "drilled plate with rounded corners",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _fillet("corners", "plate", 5.0, _edges("straight", "Z")),
        ),
        note=(
            "The Stage 47 root cause, as a request. The bore's seam is a "
            "genuine Z-parallel straight edge, so `axis_parallel Z` selects "
            "it and the whole fillet fails with E5. Only `straight` builds "
            "this, which is why it is the one selector accepted."
        ),
    ),
    CapabilityCase(
        identifier="F2-hole-rim-chamfer-top",
        group=CAPABILITY,
        category=F_SELECTORS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "hole through the centre and break the top edge of the hole "
            "with a 1 mm chamfer."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - _rim(10.0, 1.0), 100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "chamfer"),
        selector=SelectorExpectation("chamfer", ("circular",), "top"),
        reference_plan=_plan(
            "drilled plate, top rim broken",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _chamfer(
                "break", "plate", 1.0, _edges("circular", "Z", "top"),
            ),
        ),
        note=(
            "A rim, not a corner, and one end of it. Chamfering both rims "
            "removes twice the material, so geometry catches that; top and "
            "bottom are indistinguishable by volume, so the selector "
            "expectation is what scores the `position`."
        ),
    ),
    CapabilityCase(
        identifier="F3-hole-rim-chamfer-bottom",
        group=CAPABILITY,
        category=F_SELECTORS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "hole through the centre and break the bottom edge of the hole "
            "with a 1 mm chamfer."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - _rim(10.0, 1.0), 100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "chamfer"),
        selector=SelectorExpectation("chamfer", ("circular",), "bottom"),
        reference_plan=_plan(
            "drilled plate, bottom rim broken",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _chamfer(
                "break", "plate", 1.0, _edges("circular", "Z", "bottom"),
            ),
        ),
        note=(
            "F2's mirror, and the only reason both are here: their volumes "
            "are equal to the last bit, so a run that scored F2 correct and "
            "F3 correct on geometry alone would have proved nothing about "
            "whether the model can tell the two ends apart."
        ),
    ),
    CapabilityCase(
        identifier="F4-patterned-rims-chamfered",
        group=CAPABILITY,
        category=F_SELECTORS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with four 8 mm "
            "diameter holes through it, spaced evenly on a 40 mm diameter "
            "bolt circle centred on the plate, and break the top edge of "
            "every hole with a 1 mm chamfer."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - 4.0 * _HOLE_D8 - 4.0 * _rim(4.0, 1.0),
            100.0, 60.0, 10.0,
        ),
        required_plan_operations=(
            "box", "through_hole", "pattern", "chamfer",
        ),
        selector=SelectorExpectation("chamfer", ("circular",), "top"),
        reference_plan=_plan(
            "bolt circle with broken rims",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("mount", "plate", 8.0, 70.0, 30.0),
            _pattern("mounts", "mount", 4, _radial("+Z", 50.0, 30.0)),
            _chamfer(
                "break", "plate", 1.0, _edges("circular", "Z", "top"),
            ),
        ),
        note=(
            "Pattern and semantic selector in one part, and the case the "
            "brief calls 'pattern -> downstream feature': one selector has "
            "to find four rims the plan never names individually, because "
            "three of them exist only as pattern instances."
        ),
    ),
    # --- G: the dependency and history graph ------------------------------
    CapabilityCase(
        identifier="G1-subtract-then-chamfer",
        group=CAPABILITY,
        category=G_GRAPH,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate. Create a 20 mm "
            "diameter cylinder 10 mm tall at the centre of the plate and "
            "subtract it. Then chamfer the four long edges of the plate by "
            "2 mm."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - 4.0 * 100.0 * _bevel(2.0),
            100.0, 60.0, 10.0,
        ),
        required_plan_operations=(
            "box", "cylinder", "subtract", "chamfer",
        ),
        selector=SelectorExpectation(
            "chamfer", ("straight", "axis_parallel"), None,
        ),
        reference_plan=_plan(
            "plate, bore subtracted, long edges chamfered",
            _box("plate", 100.0, 60.0, 10.0),
            _cyl("tool", 20.0, 10.0, {"x": 50.0, "y": 30.0, "z": 0.0}),
            _subtract("cut", "plate", ("tool",)),
            _chamfer("break", "plate", 2.0, _edges("straight", "X")),
        ),
        note=(
            "A modifier acting on a body after a consuming operation. The "
            "cylinder is gone from the solid set by the time the chamfer "
            "runs, yet it is part of what the plate is made of -- which is "
            "exactly what Stage 45's `derivation()` reports and a flat "
            "operation list cannot."
        ),
    ),
    CapabilityCase(
        identifier="G2-two-holes-then-chamfer",
        group=CAPABILITY,
        category=G_GRAPH,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate. Drill a 20 mm "
            "diameter hole through its centre, drill a 10 mm diameter hole "
            "through the point 20 mm from the left-hand edge on the "
            "centreline, and then chamfer the four long edges by 2 mm."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20 - _HOLE_D10 - 4.0 * 100.0 * _bevel(2.0),
            100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "through_hole", "chamfer"),
        selector=SelectorExpectation(
            "chamfer", ("straight", "axis_parallel"), None,
        ),
        reference_plan=_plan(
            "plate, two holes, long edges chamfered",
            _box("plate", 100.0, 60.0, 10.0),
            _hole("bore", "plate", 20.0, 50.0, 30.0),
            _hole("mount", "plate", 10.0, 20.0, 30.0),
            _chamfer("break", "plate", 2.0, _edges("straight", "X")),
        ),
        note=(
            "Three modifiers, all naming `plate`. Stage 46's derived "
            "history edges exist for this shape: on declared edges alone "
            "the chamfer could sort before the second hole, which is a "
            "different part."
        ),
    ),
    CapabilityCase(
        identifier="G3-two-tool-subtract",
        group=CAPABILITY,
        category=G_GRAPH,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate. Create two 16 mm "
            "diameter cylinders 10 mm tall, one 25 mm from the left-hand "
            "edge and one 75 mm from it, both on the centreline, and "
            "subtract both of them from the plate in one operation."
        ),
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - 2.0 * _HOLE_D16, 100.0, 60.0, 10.0,
        ),
        required_plan_operations=("box", "cylinder", "subtract"),
        reference_plan=_plan(
            "plate with two cylinders subtracted at once",
            _box("plate", 100.0, 60.0, 10.0),
            _cyl("left", 16.0, 10.0, {"x": 25.0, "y": 30.0, "z": 0.0}),
            _cyl("right", 16.0, 10.0, {"x": 75.0, "y": 30.0, "z": 0.0}),
            _subtract("cut", "plate", ("left", "right")),
        ),
        note=(
            "Branching: two independent bodies converge on one operation, "
            "and both are consumed by it. The only case where `tools` "
            "carries more than one id."
        ),
    ),
    # --- H: a capability that genuinely does not exist ---------------------
    CapabilityCase(
        identifier="H4-threaded-rod",
        group=CAPABILITY,
        category=H_UNSUPPORTED,
        text=(
            "Create an M8 threaded rod 40 mm long with a proper helical "
            "thread."
        ),
        expect_plan=EXPECT_UNSUPPORTED,
        note=(
            "A helical sweep, which the language does not have and the "
            "prompt names. The interesting failure is approximation: a "
            "plain 8 mm cylinder is NOT a threaded rod, and answering with "
            "one is wrong however buildable it is."
        ),
    ),
    # --- I: invalid and ambiguous requests --------------------------------
    CapabilityCase(
        identifier="I1-underspecified-plate",
        group=CAPABILITY,
        category=I_INVALID,
        text="Create a mounting plate with some holes in it.",
        expect_plan=EXPECT_CLARIFICATION,
        note=(
            "No dimension anywhere, and no default for any of them. The "
            "answer is a question. Note that missing UNITS would not do: "
            "this language states that every number is in millimetres, so "
            "a unitless dimension is not ambiguous here the way it is in "
            "V1 -- one of the real differences between the two."
        ),
    ),
    CapabilityCase(
        identifier="I2-round-the-edges-no-radius",
        group=CAPABILITY,
        category=I_INVALID,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate and round off its "
            "edges."
        ),
        expect_plan=EXPECT_CLARIFICATION,
        note=(
            "Two things are missing and only one of them matters: a fillet "
            "radius has no default, so the request cannot be answered "
            "whatever 'its edges' turns out to mean. A plan with an "
            "invented radius is the failure this case looks for."
        ),
    ),
    CapabilityCase(
        identifier="I3-hole-wider-than-plate",
        group=CAPABILITY,
        category=I_INVALID,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 200 mm "
            "diameter hole through the centre."
        ),
        expect_plan=EXPECT_NO_PART,
        note=(
            "Expressible, fully specified, and impossible: the hole is "
            "wider than the stock, so nothing is left connected. The plan "
            "validator cannot catch it -- deciding it needs the kernel (E2/"
            "E3) -- so the only thing standing between this request and a "
            "confidently wrong part is the model declining to answer. "
            "Either refusal word is accepted; a plan is not."
        ),
    ),
)

CASES: Tuple[CapabilityCase, ...] = LEGACY_CASES + CAPABILITY_CASES

CASE_IDS: Tuple[str, ...] = tuple(item.identifier for item in CASES)

CASES_BY_ID: Mapping[str, CapabilityCase] = {
    item.identifier: item for item in CASES
}


# --- invalid plans: a model-less check of the rules that catch them ---------
#
# The brief's "invalid/ambiguous" category names bad references, incompatible
# references, ambiguous selectors and unsupported geometry semantics. Those
# are properties of a PLAN, not of a request: no wording forces a model to
# emit a dangling reference, so measuring them through a model would measure
# something else and call it this.
#
# They are checked here instead, offline and deterministically: a
# deliberately broken plan, and the rule that must reject it. Every code
# below was observed from the validator rather than recalled. This is an
# instrument self-check -- it makes no model call, contributes to no score,
# and appears in no rate.


@dataclass(frozen=True)
class InvalidPlan:
    """A plan that must be rejected, and where the rejection must come from."""

    identifier: str
    payload: Mapping[str, Any]

    #: ``"parser"`` if the allow-list parser must refuse it outright,
    #: ``"validator"`` if it must parse and then fail a P-rule.
    refused_by: str

    #: The rule codes the validator must report. Empty for a parser refusal.
    codes: Tuple[str, ...] = ()

    #: What kind of brokenness this is, in the brief's own words.
    kind: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.identifier,
            "payload": json.loads(json.dumps(self.payload, sort_keys=True)),
            "refused_by": self.refused_by,
            "codes": list(self.codes),
            "kind": self.kind,
        }


PARSER = "parser"
VALIDATOR = "validator"

_PLATE_OP = _box("plate", 100.0, 60.0, 10.0)

INVALID_PLANS: Tuple[InvalidPlan, ...] = (
    InvalidPlan(
        identifier="bad-reference",
        payload=_plan(
            "a hole in nothing",
            _PLATE_OP, _hole("bore", "nowhere", 10.0, 50.0, 30.0),
        ),
        refused_by=VALIDATOR,
        codes=("P9",),
        kind="bad reference",
    ),
    InvalidPlan(
        identifier="forward-reference",
        payload=_plan(
            "a hole before the plate it goes through",
            _hole("bore", "plate", 10.0, 50.0, 30.0), _PLATE_OP,
        ),
        refused_by=VALIDATOR,
        codes=("P10",),
        kind="bad reference",
    ),
    InvalidPlan(
        identifier="consumed-reference",
        payload=_plan(
            "drilling a tool that was already subtracted away",
            _PLATE_OP,
            _cyl("tool", 20.0, 10.0, {"x": 50.0, "y": 30.0, "z": 0.0}),
            _subtract("cut", "plate", ("tool",)),
            _hole("late", "tool", 5.0, 50.0, 30.0),
        ),
        refused_by=VALIDATOR,
        codes=("P12",),
        kind="incompatible reference",
    ),
    InvalidPlan(
        identifier="pattern-of-a-solid",
        payload=_plan(
            "repeating a body rather than a feature",
            _PLATE_OP,
            _pattern("copies", "plate", 3, _linear("+X", 20.0)),
        ),
        refused_by=VALIDATOR,
        codes=("P27",),
        kind="incompatible reference",
    ),
    InvalidPlan(
        identifier="self-referencing-subtract",
        payload=_plan(
            "an operation that consumes itself",
            _PLATE_OP, _subtract("cut", "cut", ("plate",)),
        ),
        refused_by=VALIDATOR,
        codes=("P10", "P31"),
        kind="cycle",
    ),
    InvalidPlan(
        identifier="radial-pattern-off-axis",
        payload=_plan(
            "turning a +Z hole about +X, which V1 could not write down",
            _PLATE_OP,
            _hole("mount", "plate", 6.0, 70.0, 30.0),
            _pattern("mounts", "mount", 4, _radial("+X", 50.0, 30.0)),
        ),
        refused_by=VALIDATOR,
        codes=("P29",),
        kind="unsupported geometry semantics",
    ),
    InvalidPlan(
        identifier="pattern-count-of-one",
        payload=_plan(
            "a pattern that repeats nothing",
            _PLATE_OP,
            _hole("mount", "plate", 6.0, 70.0, 30.0),
            _pattern("mounts", "mount", 1, _linear("+X", 20.0)),
        ),
        refused_by=VALIDATOR,
        codes=("P28",),
        kind="unsupported geometry semantics",
    ),
    InvalidPlan(
        identifier="selector-position-without-axis",
        payload=_plan(
            "a position with nothing to measure it along",
            _PLATE_OP,
            _chamfer(
                "break", "plate", 1.0, _edges("circular", None, "top"),
            ),
        ),
        refused_by=PARSER,
        kind="ambiguous selector",
    ),
    InvalidPlan(
        identifier="selector-straight-without-axis",
        payload=_plan(
            "straight edges along no particular direction",
            _PLATE_OP,
            _fillet("round", "plate", 2.0, _edges("straight")),
        ),
        refused_by=PARSER,
        kind="ambiguous selector",
    ),
)

INVALID_PLAN_IDS: Tuple[str, ...] = tuple(
    item.identifier for item in INVALID_PLANS
)


# --- lookups and the fingerprint -------------------------------------------


def case(identifier: str) -> CapabilityCase:
    """One case, by id."""
    if identifier not in CASES_BY_ID:
        raise KeyError(
            f"unknown case {identifier!r}; available: {', '.join(CASE_IDS)}"
        )
    return CASES_BY_ID[identifier]


def cases_in(group: str) -> Tuple[CapabilityCase, ...]:
    """Every case in one result group."""
    if group not in GROUPS:
        raise KeyError(f"unknown group {group!r}; available: {GROUPS}")
    return tuple(item for item in CASES if item.group == group)


def cases_in_category(category: str) -> Tuple[CapabilityCase, ...]:
    """Every case in one category."""
    if category not in CATEGORIES:
        raise KeyError(
            f"unknown category {category!r}; available: {CATEGORIES}"
        )
    return tuple(item for item in CASES if item.category == category)


def expectation_counts() -> Dict[str, int]:
    """How many cases expect each kind of correct answer."""
    counts = {name: 0 for name in EXPECTATIONS}
    for item in CASES:
        counts[item.expect_plan] += 1
    return counts


def corpus_fingerprint() -> str:
    """A hash of every case, expectation, reference plan and invalid plan.

    Recorded with each run so a result is tied to the exact instrument that
    produced it, and so an edit to this file after a run is detectable
    rather than a matter of trust.
    """
    payload = json.dumps(
        {
            "version": STAGE48_CORPUS_VERSION,
            "cases": [item.to_dict() for item in CASES],
            "invalid_plans": [item.to_dict() for item in INVALID_PLANS],
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def describe() -> str:
    """The corpus as a readable table."""
    lines = [
        f"stage 48 corpus {STAGE48_CORPUS_VERSION}  fingerprint "
        f"{corpus_fingerprint()[:16]}",
        f"{len(CASES)} cases "
        f"({len(cases_in(LEGACY))} legacy, {len(cases_in(CAPABILITY))} "
        f"capability), {len(INVALID_PLANS)} invalid-plan fixtures",
        "",
    ]
    for group in GROUPS:
        lines.append(f"--- {group} ---")
        for item in cases_in(group):
            lines.append(f"{item.identifier}  [{item.category}]")
            lines.append(f"  {item.text}")
            expects = f"  plan expects: {item.expect_plan}"
            if item.expect_v1 is not None:
                expects += f"   V1 expects: {item.expect_v1}"
            lines.append(expects)
            if item.geometry is not None:
                box = item.geometry
                lines.append(
                    f"  geometry: {box.volume_mm3:.6f} mm3, "
                    f"{box.size_x} x {box.size_y} x {box.size_z}, "
                    f"{box.solid_count} solid"
                )
            if item.required_plan_operations:
                lines.append(
                    f"  requires: "
                    f"{', '.join(item.required_plan_operations)}"
                )
            if item.selector is not None:
                selector = item.selector
                lines.append(
                    f"  selector: {selector.operation_type} "
                    f"{'|'.join(selector.select)}"
                    + (
                        f" position={selector.position}"
                        if selector.position else ""
                    )
                )
            lines.append("")
    lines.append("--- invalid plans (offline; no model) ---")
    for broken in INVALID_PLANS:
        lines.append(
            f"{broken.identifier:34s} {broken.refused_by:9s} "
            f"{', '.join(broken.codes) or '-':10s} [{broken.kind}]"
        )
    return "\n".join(lines)


__all__ = [
    "ACCEPTED_REFUSALS",
    "A_PRIMITIVES",
    "BOUNDING_BOX_ATOL",
    "B_MODIFIERS",
    "CAPABILITY",
    "CAPABILITY_CASES",
    "CASES",
    "CASES_BY_ID",
    "CASE_IDS",
    "CATEGORIES",
    "C_PROFILES",
    "D_CHAINS",
    "EXPECTATIONS",
    "EXPECT_BUILD",
    "EXPECT_CLARIFICATION",
    "EXPECT_NO_PART",
    "EXPECT_UNSUPPORTED",
    "EXPECT_VALID_UNEXECUTABLE",
    "E_PATTERN",
    "F_SELECTORS",
    "GROUPS",
    "G_GRAPH",
    "H_UNSUPPORTED",
    "INVALID_PLANS",
    "INVALID_PLAN_IDS",
    "I_INVALID",
    "LEGACY",
    "LEGACY_CASES",
    "LEGACY_CASE_IDS",
    "PARSER",
    "STAGE48_CORPUS_VERSION",
    "VALIDATOR",
    "VOLUME_RTOL",
    "CapabilityCase",
    "ExpectedGeometry",
    "InvalidPlan",
    "SelectorExpectation",
    "case",
    "cases_in",
    "cases_in_category",
    "corpus_fingerprint",
    "describe",
    "expectation_counts",
]
