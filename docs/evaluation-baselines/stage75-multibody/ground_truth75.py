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

# ---------------------------------------------------- Phase B: new cases
#
# M4 and M8 are RETIRED, not edited. Phase A measured them 0/8 each and in
# both cases the model was right and the case was wrong; they stay here
# verbatim, in `RETIRED`, because a corpus that deletes its mistakes cannot
# be audited. Nothing scores them any more.
#
# Editing either in place would also have silently changed what a recorded
# Phase A number means, which is the thing this corpus exists to prevent.

# N3 is N1 done properly, and N1 is retired on its FIRST USE. Writing the
# cylinder as "30 mm long" and then asking to make it "40 mm long" states
# two lengths for one solid, and the model correctly refused all 8 -- "the
# cylinder cannot simultaneously be 30 mm long and 40 mm long". M4's own
# wording gave no initial length and was coherent; the defect is entirely
# mine, introduced while fixing M4's placement. Same lesson as M4 and M8 in
# a third form: a request must be checked for what it now says, not only for
# what it was meant to fix.
N3_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm diameter cylinder as two separate "
    "bodies, standing beside each other and not touching. Then make the "
    "cylinder 40 mm long."
)

N1_TEXT: Final[str] = (
    "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long as two "
    "separate bodies, with the cylinder standing beside the cube and not "
    "touching it. Then make the cylinder 40 mm long."
)
# N2 states the placement COMPLETELY, on purpose. "overlaps the cube by
# 10 mm" alone would not: an overlap through a side face is a different
# solid from an overlap through the top, with a different volume, and both
# are legal readings. Pinning a volume the request does not determine is
# exactly what made M4 and M8 invalid, so the request names the face, the
# axis and the depth, and the expected volume follows from arithmetic.
N2_TEXT: Final[str] = (
    "Create a 40 mm cube at the origin and a 20 mm diameter cylinder 30 mm "
    "long on the +Z axis, centred on the cube's top face and sunk 10 mm "
    "into it, then fuse them into a single body."
)

# Three refusal cases against the same deterministic two-body fixture. R1
# restates M6 on the corrected observer; R2 restates M7; R3 is new and tests
# the failure the corrected observer can finally see -- a "clarification"
# that carries operations, which Phase A's M7 hit twice by accident.
R1_TEXT: Final[str] = "Make the body 10 mm taller."
R2_TEXT: Final[str] = "Make the bracket 10 mm taller."
R3_TEXT: Final[str] = "Put a hole through it."

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
#:
#: **That reasoning is WRONG for this engine, and Phase A proved it 8/8.**
#: Rule E3 requires a union to leave ONE connected solid, so a union of two
#: disjoint solids does not produce a body with the summed volume -- it does
#: not build at all. M8's request is self-contradictory: "beside it" and
#: "fused together into a single body" cannot both hold. The constant and
#: the case are kept verbatim as the record of that mistake; N2 is the
#: buildable replacement. See `RETIRED`.
FUSED_VOLUME: Final[float] = CUBE_VOLUME + PIN_VOLUME

# ------------------------------------------- Phase B: N2's buildable fuse
#
# The request states an OVERLAP, so the fuse is legal and the volume is the
# sum MINUS the shared material -- which is the whole point of replacing M8
# with a case whose expectation a kernel can actually meet.
#
# The cylinder is d20 x h30 on +Z, overlapping the 40 cube by 10 mm. Reading
# the request the way it is written: the cylinder rises from inside the
# cube's top region, so 10 mm of its length is buried in the cube and 20 mm
# stands proud. The buried part is a full cylinder of length 10 as long as
# the cylinder's footprint lies within the cube's 40x40 plan, which the
# request's "overlaps the cube" implies and which any centred placement
# satisfies.
N2_OVERLAP_DEPTH: Final[float] = 10.0
N2_BURIED_VOLUME: Final[float] = (
    math.pi * (PIN_DIAMETER / 2.0) ** 2 * N2_OVERLAP_DEPTH
)
#: 64000 + 9424.777960769379 - 3141.592653589793 = 70283.18530717959
N2_FUSED_VOLUME: Final[float] = CUBE_VOLUME + PIN_VOLUME - N2_BURIED_VOLUME

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
        "topology", "refusal_must_name", "refusal_question_must_mention",
        "operations_permitted", "retired", "notes",
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
        refusal_question_must_mention: Optional[Tuple[str, ...]] = None,
        operations_permitted: bool = True,
        retired: str = "",
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
        #: Words the clarification must contain to show it addressed THIS
        #: request rather than emitting a generic question. Separate from
        #: `refusal_must_name` because naming the bodies and answering the
        #: question asked are different things.
        self.refusal_question_must_mention = refusal_question_must_mention
        #: Whether the answer may carry operations at all. False for every
        #: refusal case: a "clarification" carrying geometry is an edit
        #: wearing a question's label, and Phase A could not see it.
        self.operations_permitted = operations_permitted
        #: Non-empty when the case is RETIRED: why it was invalid. A retired
        #: case is never scored and never edited -- it is the record.
        self.retired = retired
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
        retired="Phase A, 0/8, all F:wrong_placement -- and the MODEL WAS "
                "RIGHT. The request says only `as separate bodies`; it never "
                "says beside. The case pinned disjoint=True anyway, so two "
                "bodies at the origin failed a criterion the request does "
                "not state. Every check the case exists for passed 8/8: two "
                "bodies, both declared, the cylinder edited to 12566.370614 "
                "and the cube untouched at 64000. Replaced by N1, which "
                "states the separation. Kept verbatim, never edited.",
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
            # Phase B, from a measured failure. NOT a hidden expected size:
            # no diameter is pinned and the model may still choose any. What
            # is pinned is a constraint the REQUEST ITSELF entails -- a body
            # with a 6 mm hole through it must be wider than 6 mm, or the
            # sentence describes nothing. Phase A's one M5 failure chose a
            # 1 mm cube and a 1 mm cylinder and then drilled the stated
            # 6 mm hole, which removed all the material (E2). That is
            # incoherent dimensioning, and the corpus should say so rather
            # than score it as a build accident.
            "bore_diameter": BORE_DIAMETER,
        },
        notes="Body-targeted cut with no dimensions stated anywhere. Pins "
              "that the hole went into the cylinder, that the cube still "
              "has six planar faces and no cylindrical one, and -- Phase B "
              "-- that the drilled body is physically able to contain the "
              "6 mm hole the request asks for. No diameter is pinned.",
    ),
    Case(
        "M6", REFUSAL, M6_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES,
        retired="Phase A, 0/16 on the refusal group -- but that number was produced by a BROKEN OBSERVER and is not a measurement of the model. `observe` read `generation.questions`, a field `PlanGenerationResult` does not have, so every call recorded an empty tuple including four whose raw answer carried a populated `questions` list. The CASE was sound; the instrument was not. Restated verbatim as R1 under the corrected observer. The Phase A score stays historical and is never quoted as current.",
        notes="'the body' with two bodies standing. Must refuse and name "
              "both. Guessing either one is failure code K, and it is the "
              "failure Stage 72 built `resolve_body` to prevent.",
    ),
    Case(
        "M7", REFUSAL, M7_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES,
        retired="Phase A, 0/16 -- same broken observer as M6, and additionally 2/8 returned `needs_clarification` carrying operations, which the old grader had no check for. Restated as R2, with `operations_permitted=False` making that failure visible.",
        notes="A body name that does not exist. Must refuse and say what "
              "does exist. Inventing or silently substituting a body is K.",
    ),
    Case(
        "M8", CREATION, M8_TEXT, bodies=1, declaration_required=False,
        retired="Phase A, 0/8 -- the request is SELF-CONTRADICTORY and the "
                "expectation is unreachable. `beside it` and `fused together "
                "into a single body` cannot both hold: rule E3 refuses a "
                "union leaving two separate solids, so the fuse does not "
                "build at all and FUSED_VOLUME (the plain sum) is a volume "
                "no kernel can produce. The model answered correctly 8/8 -- "
                "cube at the origin, cylinder at x=50, one union -- and the "
                "kernel correctly refused. Replaced by N2, whose overlap "
                "makes the fuse buildable. Kept verbatim, never edited.",
        volumes=(FUSED_VOLUME,),
        notes="The control. An explicit fuse must give ONE body, so `part` "
              "must NOT be declared. Catches a model that has learned to "
              "declare bodies indiscriminately -- which is the predictable "
              "way a multi-body prompt goes wrong.",
    ),

    # ----------------------------------------------- Phase B replacements
    Case(
        "N1", CREATION, N1_TEXT, bodies=2, declaration_required=True,
        retired="Phase B, 0/8 -- and the MODEL WAS RIGHT AGAIN. The request "
                "calls the cylinder `30 mm long` and then asks to make it "
                "`40 mm long`, which states two lengths for one solid. The "
                "model refused 8/8 and said exactly that: `the cylinder "
                "cannot simultaneously be 30 mm long and 40 mm long`. M4's "
                "original wording gave NO initial length and was coherent; "
                "the contradiction was introduced while fixing M4's "
                "placement. Retired on first use, kept verbatim, replaced "
                "by N3.",
        edited_body_volume=PIN_VOLUME_EDITED,
        unchanged_body_volume=CUBE_VOLUME,
        disjoint=True,
        notes="M4 done properly. Same intent -- a body-targeted edit that "
              "must not touch the other body -- but the request now STATES "
              "the separation (`beside the cube and not touching it`), so "
              "disjoint=True is something the request determines rather "
              "than something the corpus assumed. Dimensions and the edit "
              "are unchanged from M4, so the two are comparable on every "
              "criterion except the one M4 got wrong.",
    ),
    Case(
        "N2", CREATION, N2_TEXT, bodies=1, declaration_required=False,
        volumes=(N2_FUSED_VOLUME,),
        notes="M8's control, made buildable. An explicit fuse must give ONE "
              "body and must NOT declare a `part` -- unchanged -- but the "
              "solids now OVERLAP, so rule E3 is satisfied and the fuse can "
              "actually build. The request names the face, the axis and the "
              "depth, so the expected volume is arithmetic rather than "
              "assumption: 64000 + 9424.777960769 - 3141.592653590 = "
              "70283.185307180.",
    ),
    Case(
        "N3", CREATION, N3_TEXT, bodies=2, declaration_required=True,
        edited_body_volume=PIN_VOLUME_EDITED,
        unchanged_body_volume=CUBE_VOLUME,
        disjoint=True,
        notes="M4's intent, finally stated coherently: the separation is in "
              "the request (M4's was not) and only ONE length is given for "
              "the cylinder (N1's gave two). Tests what M4 always meant to "
              "-- a body-targeted edit that must not touch the other body.",
    ),
    Case(
        "R1", REFUSAL, R1_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES, operations_permitted=False,
        notes="M6 verbatim, under the corrected observer. `the body` with "
              "two bodies standing names neither. Must decline, name both, "
              "build nothing and carry no operations.",
    ),
    Case(
        "R2", REFUSAL, R2_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES,
        refusal_question_must_mention=("bracket",),
        operations_permitted=False,
        notes="M7 verbatim, under the corrected observer. A body that does "
              "not exist. `bracket` is pinned because it is the USER's own "
              "word -- a clarification that never mentions what was asked "
              "for is answering some other question. Nothing else about the "
              "wording is pinned; M4 and M8 are the lesson about that.",
    ),
    Case(
        "R3", REFUSAL, R3_TEXT, bodies=None, declaration_required=False,
        refusal_must_name=FIXTURE_BODIES, operations_permitted=False,
        notes="New in Phase B, and it tests what the corrected observer can "
              "finally see. `Put a hole through it` is an ambiguous "
              "reference AND an instruction to cut, so the tempting wrong "
              "answer is a question with a through_hole attached. Phase A's "
              "M7 hit that twice by accident and had no check for it; here "
              "it is the point of the case.",
    ),
)

#: Retired: measured, found invalid, and kept verbatim as the record. Never
#: scored, never edited. A corpus that deletes its mistakes cannot be
#: audited, and an edited case silently changes what an old number meant.
RETIRED: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if c.retired)

#: What Phase B actually scores.
ACTIVE: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if not c.retired)

CASES_BY_NAME: Final[Mapping[str, Case]] = {c.name: c for c in CASES}

#: ACTIVE only. A retired case is not scored, so it is not in the
#: denominator either -- pooling a retired case into a rate would be
#: quoting a number the corpus has already said is invalid.
CREATION_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == CREATION and not c.retired
)
REFUSAL_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == REFUSAL and not c.retired
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
    if case.retired:
        raise ValueError(
            f"{case_name} is RETIRED and must not be scored again: "
            f"{case.retired}"
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
        "refusal_question_must_mention": case.refusal_question_must_mention,
        "operations_permitted": case.operations_permitted,
        "volume_tolerance": VOLUME_TOLERANCE,
    }


# ------------------------------------------------- the failure taxonomy (P6)
#
# One code per distinguishable way a multi-body answer goes wrong. Codes are
# stable strings so a run recorded today can be compared to one recorded
# later; the letters match the Stage 75 brief.

A_MISSING_BODY = "A:missing_body"
B_EXTRA_BODY = "B:extra_body"
C_WRONG_IDENTITY = "C:wrong_identity"
D_WRONG_TARGET = "D:wrong_target"
E_CROSS_BODY_EDIT = "E:cross_body_edit"
F_WRONG_PLACEMENT = "F:wrong_placement"
G_WRONG_DIMENSIONS = "G:incoherent_dimensions"
H_UNWANTED_FUSION = "H:unwanted_fusion"
#: Phase B splits Phase A's single `K:refusal_failure`. They are different
#: failures with different fixes: `I` is the refusal itself being wrong --
#: it guessed, it built something, or it carried operations alongside the
#: question. `J` is a correct refusal whose clarification does not do its
#: job -- it names no body, or leaves the `questions` field empty. Phase A
#: reported both as one code and so could not tell a guess from a shrug.
I_BAD_REFUSAL = "I:bad_refusal"
J_BAD_CLARIFICATION = "J:bad_clarification"
K_INVALID_PLAN = "K:invalid_plan"
L_OTHER = "L:other"

#: Phase A's names, kept so a Phase A record stays readable. They are NOT in
#: `TAXONOMY` and nothing emits them: an old result says `K:refusal_failure`
#: and must keep saying it, because that is what was measured.
PHASE_A_CODES: Final[Tuple[str, ...]] = (
    "I:duplicate_declaration", "J:invalid_schema_or_plan",
    "K:refusal_failure", "C:wrong_body_identity", "G:wrong_dimensions",
)

TAXONOMY: Final[Tuple[str, ...]] = (
    A_MISSING_BODY, B_EXTRA_BODY, C_WRONG_IDENTITY, D_WRONG_TARGET,
    E_CROSS_BODY_EDIT, F_WRONG_PLACEMENT, G_WRONG_DIMENSIONS,
    H_UNWANTED_FUSION, I_BAD_REFUSAL, J_BAD_CLARIFICATION, K_INVALID_PLAN,
    L_OTHER,
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
    "ACTIVE", "CREATION", "CREATION_CASES", "CUBE_EDGE", "CUBE_VOLUME",
    "N2_FUSED_VOLUME", "N2_BURIED_VOLUME", "N2_OVERLAP_DEPTH", "RETIRED",
    "CYLINDER_FACES", "Case", "DETERMINISTIC", "FALLBACK", "FIXTURE_BODIES",
    "FUSED_VOLUME", "MODEL", "MODEL_GENERATED", "OUTCOMES", "PIN_DIAMETER",
    "PIN_LENGTH", "PIN_LENGTH_EDITED", "PIN_VOLUME", "PIN_VOLUME_EDITED",
    "PROVIDER_ERROR", "REFUSAL", "REFUSAL_CASES", "REFUSAL_FIXTURE",
    "REFUSED", "TAXONOMY", "VOLUME_TOLERANCE", "expected",
    "A_MISSING_BODY", "B_EXTRA_BODY", "C_WRONG_IDENTITY", "D_WRONG_TARGET",
    "E_CROSS_BODY_EDIT", "F_WRONG_PLACEMENT", "G_WRONG_DIMENSIONS",
    "H_UNWANTED_FUSION", "I_BAD_REFUSAL", "J_BAD_CLARIFICATION",
    "K_INVALID_PLAN", "L_OTHER", "PHASE_A_CODES",
]
