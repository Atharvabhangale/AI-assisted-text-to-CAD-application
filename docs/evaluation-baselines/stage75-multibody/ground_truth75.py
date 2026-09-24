"""Stage 75 immutable ground truth: the multi-body golden corpus.

Every number here is derived from the REQUEST TEXT and closed-form
arithmetic, before any model was asked anything. Nothing in this module may
ever be computed from, adjusted to, or reconciled with model output. That is
not a style rule: Stage 67 adopted nothing because its criterion moved, and
Stage 68's whole finding was that the REQUEST -- not the model -- was the
variable. A ground truth that can see a plan is not a ground truth.

The structural guarantee, inherited verbatim from Stage 68 and the one
property the instrument rests on: :func:`expected` takes a **case name** and
nothing else. It is deliberately impossible to hand it a plan, a shape, a
measurement or a response.

What is deliberately NOT pinned
-------------------------------
Body **ids** are pinned only where the request names them (M2). Everywhere
else the model may choose any id, because the request does not say -- and a
corpus that demanded `cube` when the request never said `cube` would score
vocabulary, not capability.

Likewise M3 and M5 pin only what their requests state. M3 gives one extent
per box and M5 gives no dimensions at all; inventing the rest and then
grading against it would be the instrument marking its own homework. What
those cases test is body COUNT, body SEPARATION and EDIT ISOLATION, and
those are fully determined.

Groups
------
`CREATION` cases must produce geometry. `REFUSAL` cases must produce a
refusal. They are scored separately and never pooled, because a refusal rate
and a build rate are different quantities -- mixing them is how a model that
refuses everything scores well.

Refusal cases start from a DETERMINISTIC fixture, never from a
model-generated part. If the setup were model-generated, a setup failure
would be recorded as a refusal failure and the measurement would be of two
things at once.
"""

from __future__ import annotations

import math
from typing import Final, Mapping, Optional, Tuple

# --------------------------------------------------------------- the model

#: The one model this corpus is defined against. A number measured on any
#: other model is a different number and must not be compared to these.
MODEL: Final[str] = "claude-haiku-4-5-20251001"

# ------------------------------------------------------- the request texts
#
# Verbatim and immutable. Stage 68's finding was that the request is a
# variable of the experiment, so changing one of these makes a NEW case with
# a new name -- it never edits an existing one.

M1_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it "
    "as two separate bodies."
)
M2_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it. "
    "Name the bodies cube and pin."
)
M3_TEXT: Final[str] = (
    "Create two separate boxes, one 40 mm wide and one 20 mm wide, beside "
    "each other as independent bodies."
)
M4_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm cylinder as separate bodies, then make "
    "the cylinder 40 mm long."
)
M5_TEXT: Final[str] = (
    "Create a cube and a cylinder as separate bodies, then put a 6 mm "
    "through hole through the cylinder."
)
M6_TEXT: Final[str] = "Make the body 10 mm taller."
M7_TEXT: Final[str] = "Make the bracket 10 mm taller."
M8_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it, "
    "fused together into a single body."
)

#: The deterministic two-body fixture the refusal cases start from. Written
#: as a plan, not a request: it is scaffolding, not a thing under test, and
#: it must never be model-generated (see the module docstring).
REFUSAL_FIXTURE: Final[dict] = {
    "status": "generated",
    "summary": "two independent bodies",
    "operations": [
        {"id": "block", "type": "box",
         "parameters": {"x": 30.0, "y": 30.0, "z": 30.0}},
        {"id": "rod", "type": "cylinder",
         "parameters": {"diameter": 10.0, "height": 25.0,
                        "position": {"x": 60.0, "y": 15.0, "z": 0.0}}},
        {"id": "b1", "type": "part", "target": "block"},
        {"id": "b2", "type": "part", "target": "rod"},
    ],
}
#: The body ids that fixture leaves standing. A refusal must be able to name
#: them, which is what makes it an answer rather than a shrug.
FIXTURE_BODIES: Final[Tuple[str, ...]] = ("block", "rod")

# ------------------------------------------------------- closed-form sizes

CUBE_EDGE: Final[float] = 40.0
CUBE_VOLUME: Final[float] = CUBE_EDGE ** 3                      # 64000.0

PIN_DIAMETER: Final[float] = 20.0
PIN_LENGTH: Final[float] = 30.0
PIN_VOLUME: Final[float] = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_LENGTH

#: M4's edit: the same cylinder made 40 long. The cube must not move.
PIN_LENGTH_EDITED: Final[float] = 40.0
PIN_VOLUME_EDITED: Final[float] = (
    math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_LENGTH_EDITED
)

#: M5's bore. Its depth is the cylinder's own length, which the request never
#: states, so no volume is pinned for M5 -- only topology and isolation.
BORE_DIAMETER: Final[float] = 6.0

#: M8 fuses the two M1 solids. They are disjoint ("beside it"), so the fused
#: volume is exactly the sum -- a union of disjoint solids adds nothing and
#: removes nothing.
FUSED_VOLUME: Final[float] = CUBE_VOLUME + PIN_VOLUME

#: Relative tolerance for a kernel volume against a closed form. The project
#: measures cross-kernel agreement at ~1e-11 on parts of this size; 1e-6 is
#: three orders of headroom and still far tighter than any real error.
#: Floating-point equality is never used on a kernel value.
VOLUME_TOLERANCE: Final[float] = 1e-6

#: Topology of an undrilled primitive, used for edit-isolation checks.
BOX_FACES: Final[int] = 6
CYLINDER_FACES: Final[int] = 3          # two caps and one lateral surface

# ------------------------------------------------------------- case groups

CREATION: Final[str] = "creation"
REFUSAL: Final[str] = "refusal"


class Case:
    """One golden case. Immutable, and constructed only in this module."""

    __slots__ = (
        "name", "group", "text", "bodies", "body_ids", "declaration_required",
        "volumes", "disjoint", "edited_body_volume", "unchanged_body_volume",
        "topology", "refusal_must_name", "notes",
    )

    def __init__(
        self,
        name: str,
        group: str,
        text: str,
        bodies: Optional[int],
        declaration_required: bool,
        *,
        body_ids: Optional[Tuple[str, ...]] = None,
        volumes: Optional[Tuple[float, ...]] = None,
        disjoint: bool = False,
        edited_body_volume: Optional[float] = None,
        unchanged_body_volume: Optional[float] = None,
        topology: Optional[Mapping[str, object]] = None,
        refusal_must_name: Optional[Tuple[str, ...]] = None,
        notes: str = "",
    ) -> None:
        self.name = name
        self.group = group
        self.text = text
        #: How many live bodies the finished part must have. `None` only for
        #: a refusal, where no part is produced at all.
        self.bodies = bodies
        #: Pinned ONLY where the request names them. `None` means the model
        #: may choose, and the evaluator must not care.
        self.body_ids = body_ids
        #: Whether a `part` declaration is required for the plan to be legal.
        #: True exactly when more than one body stands at the end -- rule P34.
        self.declaration_required = declaration_required
        #: Per-body volumes as an UNORDERED multiset. Order is not meaning:
        #: which body the model declares first says nothing about the part.
        self.volumes = volumes
        #: Whether the bodies must not overlap ("beside it" / "beside each
        #: other"). Checked on bounding boxes, which is sound for disjointness
        #: and deliberately not used for anything finer.
        self.disjoint = disjoint
        #: For an edit case: what the edited body must become, and what the
        #: OTHER body must still be. The second is the whole point -- an edit
        #: that changes both bodies is the failure this case exists to catch.
        self.edited_body_volume = edited_body_volume
        self.unchanged_body_volume = unchanged_body_volume
        self.topology = dict(topology) if topology else None
        #: A refusal must name these bodies to count as an answer.
        self.refusal_must_name = refusal_must_name
        self.notes = notes


CASES: Final[Tuple[Case, ...]] = (
    Case(
        "M1", CREATION, M1_TEXT, bodies=2, declaration_required=True,
        volumes=(CUBE_VOLUME, PIN_VOLUME), disjoint=True,
        notes="The base case. Two primitives, both fully dimensioned, "
              "explicitly 'two separate bodies'. Ids are free.",
    ),
    Case(
        "M2", CREATION, M2_TEXT, bodies=2, declaration_required=True,
        body_ids=("cube", "pin"),
        volumes=(CUBE_VOLUME, PIN_VOLUME), disjoint=True,
        notes="M1 with the ids named in the request. The only case where an "
              "id is ground truth, because the only case that states one.",
    ),
    Case(
        "M3", CREATION, M3_TEXT, bodies=2, declaration_required=True,
        disjoint=True,
        topology={"both_boxes": True, "extents_present": (40.0, 20.0)},
        notes="Deliberately under-specified: one extent per box and nothing "
              "else. Pins count, separation and that both are boxes with the "
              "stated extents. No volume is pinned, because none is stated.",
    ),
    Case(
        "M4", CREATION, M4_TEXT, bodies=2, declaration_required=True,
        edited_body_volume=PIN_VOLUME_EDITED,
        unchanged_body_volume=CUBE_VOLUME,
        disjoint=True,
        notes="Body-targeted edit. The cylinder becomes 40 long; the cube "
              "must be untouched. Cross-body leakage is failure code E.",
    ),
    Case(
        "M5", CREATION, M5_TEXT, bodies=2, declaration_required=True,
        topology={
            "drilled_body_min_faces": CYLINDER_FACES + 1,
            "untouched_body_faces": BOX_FACES,
            "untouched_body_is_prismatic": True,
        },
        notes="Body-targeted cut with no dimensions stated anywhere. Pins "
              "only that the hole went into the cylinder and that the cube "
              "still has six planar faces and no cylindrical one.",
    ),
    Case(
        "M6", REFUSAL, M6_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES,
        notes="'the body' with two bodies standing. Must refuse and name "
              "both. Guessing either one is failure code K, and it is the "
              "failure Stage 72 built `resolve_body` to prevent.",
    ),
    Case(
        "M7", REFUSAL, M7_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES,
        notes="A body name that does not exist. Must refuse and say what "
              "does exist. Inventing or silently substituting a body is K.",
    ),
    Case(
        "M8", CREATION, M8_TEXT, bodies=1, declaration_required=False,
        volumes=(FUSED_VOLUME,),
        notes="The control. An explicit fuse must give ONE body, so `part` "
              "must NOT be declared. Catches a model that has learned to "
              "declare bodies indiscriminately -- which is the predictable "
              "way a multi-body prompt goes wrong.",
    ),
)

CASES_BY_NAME: Final[Mapping[str, Case]] = {c.name: c for c in CASES}

CREATION_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == CREATION
)
REFUSAL_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == REFUSAL
)


def expected(case_name: str) -> dict:
    """The ground truth for one case, BY NAME.

    Takes a case name, never a plan, never a shape, never a measurement and
    never a response. The signature is the guarantee: it is not possible to
    hand this function anything a model produced, so it is not possible for
    the truth to drift toward the output.
    """
    case = CASES_BY_NAME.get(case_name)
    if case is None:
        raise KeyError(
            f"unknown case {case_name!r}; known: {', '.join(CASES_BY_NAME)}"
        )
    return {
        "name": case.name,
        "group": case.group,
        "text": case.text,
        "bodies": case.bodies,
        "body_ids": case.body_ids,
        "declaration_required": case.declaration_required,
        "volumes": case.volumes,
        "disjoint": case.disjoint,
        "edited_body_volume": case.edited_body_volume,
        "unchanged_body_volume": case.unchanged_body_volume,
        "topology": case.topology,
        "refusal_must_name": case.refusal_must_name,
        "volume_tolerance": VOLUME_TOLERANCE,
    }


# ------------------------------------------------- the failure taxonomy (P6)
#
# One code per distinguishable way a multi-body answer goes wrong. Codes are
# stable strings so a run recorded today can be compared to one recorded
# later; the letters match the Stage 75 brief.

A_MISSING_BODY = "A:missing_body"
B_EXTRA_BODY = "B:extra_body"
C_WRONG_IDENTITY = "C:wrong_body_identity"
D_WRONG_TARGET = "D:wrong_target"
E_CROSS_BODY_EDIT = "E:cross_body_edit"
F_WRONG_PLACEMENT = "F:wrong_placement"
G_WRONG_DIMENSIONS = "G:wrong_dimensions"
H_UNWANTED_FUSION = "H:unwanted_fusion"
I_DUPLICATE_DECLARATION = "I:duplicate_declaration"
J_INVALID_PLAN = "J:invalid_schema_or_plan"
K_REFUSAL_FAILURE = "K:refusal_failure"
L_OTHER = "L:other"

TAXONOMY: Final[Tuple[str, ...]] = (
    A_MISSING_BODY, B_EXTRA_BODY, C_WRONG_IDENTITY, D_WRONG_TARGET,
    E_CROSS_BODY_EDIT, F_WRONG_PLACEMENT, G_WRONG_DIMENSIONS,
    H_UNWANTED_FUSION, I_DUPLICATE_DECLARATION, J_INVALID_PLAN,
    K_REFUSAL_FAILURE, L_OTHER,
)

# ------------------------------------------------------- outcome labelling
#
# Stage 64's five labels, kept distinct in every report. A DETERMINISTIC
# result may never be counted as a MODEL_GENERATED success: every multi-body
# number recorded before Stage 75 is deterministic and says nothing whatever
# about a model.

MODEL_GENERATED: Final[str] = "MODEL_GENERATED"
DETERMINISTIC: Final[str] = "DETERMINISTIC"
FALLBACK: Final[str] = "FALLBACK"
REFUSED: Final[str] = "REFUSED"
PROVIDER_ERROR: Final[str] = "PROVIDER_ERROR"

OUTCOMES: Final[Tuple[str, ...]] = (
    MODEL_GENERATED, DETERMINISTIC, FALLBACK, REFUSED, PROVIDER_ERROR,
)

#: Only this label may contribute to a strict success rate.
COUNTS_AS_SUCCESS: Final[Tuple[str, ...]] = (MODEL_GENERATED,)


__all__ = [
    "BORE_DIAMETER", "BOX_FACES", "CASES", "CASES_BY_NAME", "COUNTS_AS_SUCCESS",
    "CREATION", "CREATION_CASES", "CUBE_EDGE", "CUBE_VOLUME",
    "CYLINDER_FACES", "Case", "DETERMINISTIC", "FALLBACK", "FIXTURE_BODIES",
    "FUSED_VOLUME", "MODEL", "MODEL_GENERATED", "OUTCOMES", "PIN_DIAMETER",
    "PIN_LENGTH", "PIN_LENGTH_EDITED", "PIN_VOLUME", "PIN_VOLUME_EDITED",
    "PROVIDER_ERROR", "REFUSAL", "REFUSAL_CASES", "REFUSAL_FIXTURE",
    "REFUSED", "TAXONOMY", "VOLUME_TOLERANCE", "expected",
    "A_MISSING_BODY", "B_EXTRA_BODY", "C_WRONG_IDENTITY", "D_WRONG_TARGET",
    "E_CROSS_BODY_EDIT", "F_WRONG_PLACEMENT", "G_WRONG_DIMENSIONS",
    "H_UNWANTED_FUSION", "I_DUPLICATE_DECLARATION", "J_INVALID_PLAN",
    "K_REFUSAL_FAILURE", "L_OTHER",
]
