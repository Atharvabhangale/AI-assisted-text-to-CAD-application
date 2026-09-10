"""Stage 40: the fixed comparison corpus. Thirteen cases, frozen before use.

This module exists to answer one question with evidence: **is the CAD
operation plan easier for Claude Haiku to generate correctly than the
canonical V1 JSON?** It holds the cases and the expectations, and nothing
else -- no model call, no scoring, no analysis.

Freeze discipline
-----------------
Every expectation here was written from the request text and from the two
**frozen** vocabularies, before a single live model call was made. Nothing in
this file may be edited after a result is observed. A case the model fails is
a finding, not a bug in the corpus.

Two representations, sometimes two different right answers
----------------------------------------------------------
The cases that involve a sketch are the important subtlety. A sketch is out
of scope for V1, which is required to reject it, and *in* scope for the
operation plan, which can express it and then cannot build it. So the correct
answer to case 9 is ``UNSUPPORTED`` for V1 and a valid plan for the operation
plan.

That is not a thumb on the scale: it is what each frozen representation can
express. Scoring both arms against "did it build" would punish the operation
plan for a backend gap and reward V1 for refusing, and scoring both against
"did it produce a plan" would do the reverse. So each case names what a
*correct* answer looks like **per representation**, derived from the frozen
vocabulary, and the report keeps those cases visible rather than folded into
one number.

Expected geometry
-----------------
Where a part is buildable, the expectation is a **closed form**, computed
from the request's own numbers, never a measurement recorded as its own
expectation. Comparison uses relative tolerance; nothing here uses exact
float equality.

Note which expectations are position-independent. Case 5 does not say where
the two holes go, so the model chooses; the *volume* is still determined,
because two 16 mm holes through 10 mm of plate remove the same material
wherever they are, provided they lie inside the material and do not overlap.
A model that overlaps them gets a different volume and is scored incorrect,
which is the right outcome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

#: The corpus version. Bumping it means a different measurement instrument.
CORPUS_VERSION = "1.0.0"

#: Relative tolerance for a volume comparison. Kernel values are never
#: compared for exact equality (the project's standing rule).
VOLUME_RTOL = 1e-6

#: Absolute tolerance in millimetres for a bounding-box comparison.
BOUNDING_BOX_ATOL = 1e-6

# --- what a correct answer looks like --------------------------------------

#: The request is expressible and should produce buildable geometry.
EXPECT_BUILD = "build"

#: The request is expressible in this representation but **cannot be built**
#: by this backend. Only reachable for the operation plan, and only for the
#: sketch-based operations (Stages 37-38).
EXPECT_VALID_UNEXECUTABLE = "valid_unexecutable"

#: The request cannot be expressed at all, and the correct answer is to say
#: so. Refusing correctly is a success, not a failure.
EXPECT_UNSUPPORTED = "unsupported"

EXPECTATIONS: Tuple[str, ...] = (
    EXPECT_BUILD, EXPECT_VALID_UNEXECUTABLE, EXPECT_UNSUPPORTED,
)

# --- categories, as the brief names them -----------------------------------

BASIC = "basic_primitives"
MODIFIERS = "modifiers"
FEATURES = "features"
SKETCH_CHAIN = "sketch_feature_chain"
NEGATIVE = "unsupported_negative"

CATEGORIES: Tuple[str, ...] = (BASIC, MODIFIERS, FEATURES, SKETCH_CHAIN, NEGATIVE)


# --- closed forms ----------------------------------------------------------

#: The plate every plate case uses: 100 x 60 x 10 mm.
_PLATE = 100.0 * 60.0 * 10.0

#: A d20 cylinder 50 mm tall.
_ROD = math.pi * 10.0**2 * 50.0

#: A d20 hole through 10 mm of plate.
_HOLE_D20_THROUGH_10 = math.pi * 10.0**2 * 10.0

#: A d16 hole through 10 mm of plate.
_HOLE_D16_THROUGH_10 = math.pi * 8.0**2 * 10.0

#: A 50 mm cube, and a d20 bore all the way through it.
_CUBE_50 = 50.0**3
_BORE_D20_THROUGH_50 = math.pi * 10.0**2 * 50.0

#: A 2 mm chamfer removes a right triangle of area d^2/2 per unit of edge.
_BEVEL_2MM = 2.0**2 / 2.0

#: A 2 mm fillet removes r^2(1 - pi/4) per unit of edge.
_CORNER_2MM = 2.0**2 * (1.0 - math.pi / 4.0)


@dataclass(frozen=True)
class ExpectedGeometry:
    """The geometry a correct answer must produce, as a closed form."""

    volume_mm3: float
    size_x: float
    size_y: float
    size_z: float
    solid_count: int = 1

    def bounding_box(self) -> Tuple[float, float, float]:
        return (self.size_x, self.size_y, self.size_z)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "volume_mm3": self.volume_mm3,
            "bounding_box": {
                "x": self.size_x, "y": self.size_y, "z": self.size_z,
            },
            "solid_count": self.solid_count,
        }


@dataclass(frozen=True)
class ComparisonCase:
    """One request, and what each representation must do with it."""

    identifier: str
    category: str
    text: str

    #: What a correct V1 answer is, and what a correct plan answer is. They
    #: differ only where the two frozen vocabularies differ.
    expect_v1: str
    expect_plan: str

    #: The geometry a correct answer builds, when one is expected. ``None``
    #: for a refusal and for an unexecutable plan.
    geometry: Optional[ExpectedGeometry] = None

    #: Operation/feature types a correct answer must use, where the request
    #: pins them. Empty where more than one representation is legitimate --
    #: case 6 may drill or subtract, and both are correct if the geometry is.
    required_v1_features: Tuple[str, ...] = ()
    required_plan_operations: Tuple[str, ...] = ()

    #: Why this case is here. Recorded so the reader of a result knows what
    #: was being tested without re-deriving it.
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.identifier,
            "category": self.category,
            "text": self.text,
            "expect_v1": self.expect_v1,
            "expect_plan": self.expect_plan,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "required_v1_features": list(self.required_v1_features),
            "required_plan_operations": list(self.required_plan_operations),
            "note": self.note,
        }


CASES: Tuple[ComparisonCase, ...] = (
    # --- basic primitives ------------------------------------------------
    ComparisonCase(
        identifier="01-plate-worded",
        category=BASIC,
        text=(
            "Create a rectangular plate 100 mm long, 60 mm wide and 10 mm "
            "thick."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(_PLATE, 100.0, 60.0, 10.0),
        required_v1_features=("box",),
        required_plan_operations=("box",),
        note="long/wide/thick must map to x/y/z. The A6 failure mode.",
    ),
    ComparisonCase(
        identifier="02-cylinder-axis",
        category=BASIC,
        text=(
            "Create a cylinder 20 mm in diameter and 50 mm tall along the "
            "+Z axis."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(_ROD, 20.0, 20.0, 50.0),
        required_v1_features=("cylinder",),
        required_plan_operations=("cylinder",),
        note="An explicit axis. Gemini over-clarified on this shape (B2/B3).",
    ),
    ComparisonCase(
        identifier="03-box-dimensional",
        category=BASIC,
        text="Create a 100 mm by 60 mm by 10 mm box.",
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(_PLATE, 100.0, 60.0, 10.0),
        required_v1_features=("box",),
        required_plan_operations=("box",),
        note="The same solid as case 1, worded as bare dimensions.",
    ),
    # --- modifiers --------------------------------------------------------
    ComparisonCase(
        identifier="04-plate-centre-hole",
        category=MODIFIERS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with a 20 mm diameter "
            "through hole in the center."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - _HOLE_D20_THROUGH_10, 100.0, 60.0, 10.0
        ),
        required_v1_features=("box", "through_hole"),
        required_plan_operations=("box", "through_hole"),
        note="'in the center' must become (50, 30). A reference plus a position.",
    ),
    ComparisonCase(
        identifier="05-plate-two-holes",
        category=MODIFIERS,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate with two 16 mm "
            "diameter through holes."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - 2.0 * _HOLE_D16_THROUGH_10, 100.0, 60.0, 10.0
        ),
        required_v1_features=("box", "through_hole"),
        required_plan_operations=("box", "through_hole"),
        note=(
            "Positions are not given, so the model chooses. The volume is "
            "still determined unless the holes overlap or leave the material."
        ),
    ),
    ComparisonCase(
        identifier="06-cube-bore",
        category=MODIFIERS,
        text=(
            "Create a 50 mm cube with a 20 mm diameter cylindrical hole "
            "through the center."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _CUBE_50 - _BORE_D20_THROUGH_50, 50.0, 50.0, 50.0
        ),
        # Deliberately unpinned: a through_hole and a cylinder+subtract are
        # both correct expressions of this part in both representations, so
        # only the geometry decides.
        note="Drilling or subtracting are both legitimate. Geometry decides.",
    ),
    # --- features ---------------------------------------------------------
    ComparisonCase(
        identifier="07-plate-chamfer",
        category=FEATURES,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate and chamfer the "
            "vertical edges by 2 mm."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - 4.0 * 10.0 * _BEVEL_2MM, 100.0, 60.0, 10.0
        ),
        required_v1_features=("box", "chamfer"),
        required_plan_operations=("box", "chamfer"),
        note=(
            "'vertical edges' on a flat plate are the four Z-parallel ones, "
            "so the selector must be axis_parallel Z, not all."
        ),
    ),
    ComparisonCase(
        identifier="08-plate-fillet",
        category=FEATURES,
        text=(
            "Create a 100 mm by 60 mm by 10 mm plate and fillet the "
            "vertical edges with a 2 mm radius."
        ),
        expect_v1=EXPECT_BUILD,
        expect_plan=EXPECT_BUILD,
        geometry=ExpectedGeometry(
            _PLATE - 4.0 * 10.0 * _CORNER_2MM, 100.0, 60.0, 10.0
        ),
        required_v1_features=("box", "fillet"),
        required_plan_operations=("box", "fillet"),
        note="The same selector question as case 7, with a radius.",
    ),
    # --- sketch / feature chain ------------------------------------------
    #
    # The two cases where the correct answers differ. V1 must reject a
    # sketch (it is out of scope, rule S3); the operation plan can express
    # one and this backend cannot build it (Stages 37-38).
    ComparisonCase(
        identifier="09-profile-extrude",
        category=SKETCH_CHAIN,
        text=(
            "Create a rectangular 100 mm by 60 mm profile on the XY plane "
            "and extrude it 10 mm."
        ),
        expect_v1=EXPECT_UNSUPPORTED,
        expect_plan=EXPECT_VALID_UNEXECUTABLE,
        required_plan_operations=("sketch", "extrude"),
        note=(
            "V1 has no sketch and must refuse. The plan can say it, and the "
            "engine cannot build it. The part is the same plate as case 1, "
            "which is the interesting tension: a model may reasonably answer "
            "with a box instead, and that is scored as not following the "
            "request."
        ),
    ),
    ComparisonCase(
        identifier="10-profile-revolve",
        category=SKETCH_CHAIN,
        text=(
            "Create a circular profile with a 20 mm diameter on the XY "
            "plane and revolve it 180 degrees around the X axis."
        ),
        expect_v1=EXPECT_UNSUPPORTED,
        expect_plan=EXPECT_VALID_UNEXECUTABLE,
        required_plan_operations=("sketch", "revolve"),
        note=(
            "Exercises the Stage 38 known gap on purpose: a circle centred "
            "on the axis of revolution straddles it, which the plan layer "
            "does not check because deciding it needs the kernel. A valid "
            "plan is the expected answer; it still cannot be built."
        ),
    ),
    # --- unsupported / negative ------------------------------------------
    ComparisonCase(
        identifier="11-sphere",
        category=NEGATIVE,
        text="Create a sphere with a 20 mm diameter.",
        expect_v1=EXPECT_UNSUPPORTED,
        expect_plan=EXPECT_UNSUPPORTED,
        note="No sphere in either vocabulary. A short cylinder is not one.",
    ),
    ComparisonCase(
        identifier="12-union",
        category=NEGATIVE,
        text="Create a cylinder and a box joined together.",
        expect_v1=EXPECT_UNSUPPORTED,
        expect_plan=EXPECT_UNSUPPORTED,
        note=(
            "Neither vocabulary has union, and the single-solid rule (S9) "
            "forbids two unjoined solids. Refusing is the only right answer."
        ),
    ),
    ComparisonCase(
        identifier="13-engine",
        category=NEGATIVE,
        text="Create a four-cylinder engine.",
        expect_v1=EXPECT_UNSUPPORTED,
        expect_plan=EXPECT_UNSUPPORTED,
        note="An assembly, and far outside either vocabulary.",
    ),
)

CASE_IDS: Tuple[str, ...] = tuple(case.identifier for case in CASES)

CASES_BY_ID: Mapping[str, ComparisonCase] = {
    case.identifier: case for case in CASES
}


def case(identifier: str) -> ComparisonCase:
    """One case, by id."""
    if identifier not in CASES_BY_ID:
        raise KeyError(
            f"unknown case {identifier!r}; available: {', '.join(CASE_IDS)}"
        )
    return CASES_BY_ID[identifier]


def corpus_fingerprint() -> str:
    """A hash of every case and expectation.

    Recorded with each run so a result can be tied to the exact instrument
    that produced it, and so an edit to this file after a run is detectable
    rather than a matter of trust.
    """
    import hashlib
    import json

    payload = json.dumps(
        {"version": CORPUS_VERSION,
         "cases": [c.to_dict() for c in CASES]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def describe() -> str:
    """The corpus as a readable table."""
    lines = [
        f"comparison corpus {CORPUS_VERSION}  fingerprint "
        f"{corpus_fingerprint()[:16]}",
        f"{len(CASES)} cases",
        "",
    ]
    for item in CASES:
        lines.append(f"{item.identifier}  [{item.category}]")
        lines.append(f"  {item.text}")
        lines.append(
            f"  V1 expects: {item.expect_v1:20s} "
            f"plan expects: {item.expect_plan}"
        )
        if item.geometry is not None:
            box = item.geometry
            lines.append(
                f"  geometry: {box.volume_mm3:.6f} mm3, "
                f"{box.size_x} x {box.size_y} x {box.size_z}, "
                f"{box.solid_count} solid"
            )
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "BASIC",
    "BOUNDING_BOX_ATOL",
    "CASES",
    "CASES_BY_ID",
    "CASE_IDS",
    "CATEGORIES",
    "CORPUS_VERSION",
    "EXPECTATIONS",
    "EXPECT_BUILD",
    "EXPECT_UNSUPPORTED",
    "EXPECT_VALID_UNEXECUTABLE",
    "FEATURES",
    "MODIFIERS",
    "NEGATIVE",
    "SKETCH_CHAIN",
    "VOLUME_RTOL",
    "ComparisonCase",
    "ExpectedGeometry",
    "case",
    "corpus_fingerprint",
    "describe",
]
