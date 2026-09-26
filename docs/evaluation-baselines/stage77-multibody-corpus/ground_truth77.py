"""The immutable truth for Stage 77's broader live multi-body corpus.

Stage 75's multi-body corpus is nine cases against ONE two-body shape, and
`stage75-multibody/phase-e-r2-tail/corpus-design.md` says in its own words
why its 47/48 creation rate is not general reliability:

    Four of the six creation cases are the same part. [...] Every case
    declares at most two bodies. `MAX_BODIES` is 8 and nothing above two has
    been asked of a model even once. [...] No creation case exercises a
    downstream surface.

This corpus answers those three. Several geometric families, bodies from one
to five, and every creation attempt carries MEASUREMENT and EXPORT
post-conditions, which Stage 76 built the observer for.

THE DISCIPLINE, and every line of it was paid for by a stage that missed it:

* **Truth takes a case NAME and a turn INDEX, and nothing else.**
  :func:`expected` and :func:`expected_turn` have no parameter a plan, a
  shape, an answer or a file could enter by. Stage 67 derived its expected
  plate thickness from the model's own plan and graded parts against their
  own answer.
* **Never edit an expectation after seeing a score.** A case whose
  expectation turns out to be wrong is RETIRED verbatim with a note and
  replaced by a NEW case with a NEW name -- the way Stage 75 retired M4, M6,
  M7, M8 and N1.
* **The request is a variable of the experiment.** Stage 68's two golden
  requests describe the same part and score 0/8 and 7/8. Editing a request
  makes a new case, never an edited one.
* **No case may depend on an unstated assumption.** Every dimension, every
  placement and every id this corpus checks is stated IN the request. Where
  a request says only "beside it", the truth pins DISJOINTNESS and not a
  coordinate, because a coordinate would be grading the model against a
  number nobody gave it.
* **Every volume is a closed form**, arithmetic on a dimension the request
  states. None was read off a kernel.

IMPORTS: ``math`` and ``typing``, and nothing else. Not the product, not the
evaluator, not Stage 75 or 76. A truth module that imports the code it
judges can be made to agree with it.
"""

from __future__ import annotations

import math
from typing import Final, Mapping, Optional, Tuple


# ---------------------------------------------------------------- identity
#
# Recorded so a reader of a result file never has to look anywhere else for
# what it was measured against. `arena77` ASSERTS these against the live
# route and refuses to run when they differ; it does not read them from here
# as a default.

MODEL: Final[str] = "claude-haiku-4-5-20251001"
PROMPT_VERSION: Final[str] = "2026-09-25.1"
PROMPT_FINGERPRINT: Final[str] = (
    "f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675"
)
PROMPT_CHARACTERS: Final[int] = 34036
SCHEMA_NAME: Final[str] = "strict_selector_union_part"
SCHEMA_INLINED: Final[int] = 3874
SCHEMA_FINGERPRINT: Final[str] = (
    "ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f"
)

#: The engines a MODEL_GENERATED success must be rebuilt on.
ENGINES: Final[Tuple[str, ...]] = ("cadquery", "freecad")


# ------------------------------------------------------- the closed forms

def _cyl(diameter: float, height: float) -> float:
    return math.pi * (diameter / 2.0) ** 2 * height


CUBE_40: Final[float] = 40.0 ** 3                              # 64000
CUBE_30: Final[float] = 30.0 ** 3                              # 27000
CUBE_20: Final[float] = 20.0 ** 3                              # 8000
CUBE_10: Final[float] = 10.0 ** 3                              # 1000

PIN_20x30: Final[float] = _cyl(20.0, 30.0)                     # 9424.778
PIN_20x50: Final[float] = _cyl(20.0, 50.0)                     # 15707.963
CYL_30x20: Final[float] = _cyl(30.0, 20.0)                     # 14137.167
CYL_10x50: Final[float] = _cyl(10.0, 50.0)                     # 3926.991
CYL_16x20: Final[float] = _cyl(16.0, 20.0)                     # 4021.239
CYL_10x40: Final[float] = _cyl(10.0, 40.0)                     # 3141.593

PLATE_50x40x10: Final[float] = 50.0 * 40.0 * 10.0              # 20000
PLATE_60x40x8: Final[float] = 60.0 * 40.0 * 8.0                # 19200

#: A 50 x 50 x 10 plate with an 8 mm bore right through it.
PLATE_BORED: Final[float] = 50.0 * 50.0 * 10.0 - _cyl(8.0, 10.0)

#: The fusion control: a 40 mm cube and a 20 mm box whose corner is at
#: x = 30, so they share a 10 x 20 x 20 region. Strictly LESS than the sum,
#: which is what makes it a control -- a part that was never actually fused
#: measures the sum.
FUSE_OVERLAP: Final[float] = 10.0 * 20.0 * 20.0                # 4000
FUSED: Final[float] = CUBE_40 + CUBE_20 - FUSE_OVERLAP         # 68000

#: The edit chain, on the cube-and-pin fixture.
CUBE_50x40x40: Final[float] = 50.0 * 40.0 * 40.0               # 80000
CUBE_60x40x40: Final[float] = 60.0 * 40.0 * 40.0               # 96000
CUBE_60_BORED: Final[float] = CUBE_60x40x40 - _cyl(10.0, 40.0)
PIN_BORED: Final[float] = PIN_20x30 - _cyl(6.0, 30.0)

#: Relative, never equality.
VOLUME_TOLERANCE: Final[float] = 1e-6
#: Placements are stated in whole millimetres; a kernel bounding box is exact
#: to far better than this.
PLACEMENT_TOLERANCE: Final[float] = 1e-6

BOX_FACES: Final[int] = 6
CYLINDER_FACES: Final[int] = 3


# ------------------------------------------------------------ the groups

CREATION: Final[str] = "creation"
EDIT: Final[str] = "edit"
REFUSAL: Final[str] = "refusal"
GROUPS: Final[Tuple[str, ...]] = (CREATION, EDIT, REFUSAL)

#: The post-condition families. Scored with their OWN denominators and never
#: pooled with the groups above or with each other: a build rate, a
#: measurement rate and an export rate are three different quantities.
MEASUREMENT: Final[str] = "measurement"
AGGREGATE: Final[str] = "aggregate"
EXPORT: Final[str] = "export"
POST_CONDITIONS: Final[Tuple[str, ...]] = (MEASUREMENT, AGGREGATE, EXPORT)


# ------------------------------------------------------- the dimensions
#
# The brief's A-O. Recorded per case so a result can be read by dimension
# rather than only by case, and so a dimension that quietly stops being
# exercised is visible.

#: NOTE the two F's, which are different letters in different alphabets and
#: must not be conflated: dimension **F** is *an ambiguous body reference*,
#: and taxonomy code **F** is *wrong placement*. PLACEMENT is not a
#: dimension of its own -- it is part of A, checked on the cases whose
#: request states coordinates (CR-05, CR-09, CR-10) and deliberately not
#: checked on the ones that say only "beside it".
DIMENSIONS: Final[Mapping[str, str]] = {
    "A": "independent body creation",
    "B": "explicit body naming",
    "C": "unnamed multi-body creation",
    "D": "named-body edit",
    "E": "named-body feature/hole operation",
    "F": "ambiguous body reference",
    "G": "nonexistent body reference",
    "H": "explicit fusion",
    "I": "body measurement",
    "J": "aggregate measurement",
    "K": "STEP export",
    "L": "body-specific export identity where measurable",
    "M": "multiple sequential edits",
    "N": "interleaved edits on different bodies",
    "O": "refusal / clarification",
}


# ------------------------------------------------------ the fixture names
#
# The deterministic parts an EDIT or REFUSAL case starts from. Named here
# and built by `fixtures77`, never model-generated: Stage 75 Phase A
# recorded setup failures as refusal failures, and a case that starts from a
# model-made part measures two things at once.

FIXTURE_CUBE_PIN: Final[str] = "cube_pin"
FIXTURE_TRIO: Final[str] = "trio"
FIXTURES: Final[Tuple[str, ...]] = (FIXTURE_CUBE_PIN, FIXTURE_TRIO)

#: Each fixture's live bodies and their closed forms. The TRUTH for what the
#: fixture is; `fixtures77` builds a plan and `arena77` asserts the built
#: part matches this before a single model call is made against it.
FIXTURE_BODIES: Final[Mapping[str, Mapping[str, float]]] = {
    FIXTURE_CUBE_PIN: {"cube": CUBE_40, "pin": PIN_20x30},
    FIXTURE_TRIO: {"plate": PLATE_60x40x8, "boss": CYL_16x20,
                   "rod": CYL_10x40},
}


class Turn:
    """One model call, and what the part must be after it.

    A creation case has exactly one; an edit case has one per request in the
    chain. Every expectation is on the FINAL state of the part after this
    turn, because `revision_context` asks for a complete revised plan rather
    than a patch -- the canonical IR has no patch form.
    """

    __slots__ = (
        "request", "bodies", "body_ids", "body_volumes",
        "declaration_required", "volumes", "placements", "disjoint",
        "changed", "unchanged", "topology", "notes",
    )

    def __init__(
        self,
        request: str,
        *,
        bodies: Optional[int] = None,
        body_ids: Optional[Tuple[str, ...]] = None,
        body_volumes: Optional[Mapping[str, float]] = None,
        declaration_required: bool = False,
        volumes: Optional[Tuple[float, ...]] = None,
        placements: Optional[Mapping[str, Tuple[Tuple[float, float, float],
                                                Tuple[float, float, float]]]] = None,
        disjoint: bool = False,
        changed: Optional[Mapping[str, float]] = None,
        unchanged: Optional[Mapping[str, float]] = None,
        topology: Optional[Mapping[str, object]] = None,
        notes: str = "",
    ) -> None:
        #: The request text, VERBATIM. Editing it makes a NEW case.
        self.request = request
        #: How many live bodies the part must have after this turn. A
        #: CONSTANT written before anything ran, never `len(what came back)`.
        self.bodies = bodies
        #: Pinned ONLY where the request names the ids. `None` means the
        #: model may choose and the evaluator must not care.
        self.body_ids = body_ids
        #: id -> volume, pinned ONLY where the request both names the ids
        #: and says which is which. `volumes` above is an unordered multiset
        #: and deliberately says nothing about the pairing; this says it, so
        #: the pairing is TRUTH rather than an accident of tuple order that
        #: a reader would have to infer. It is what lets a measurement probe
        #: about `base` be checked against a closed form instead of only
        #: against the kernel's own answer.
        self.body_volumes = dict(body_volumes) if body_volumes else None
        if body_volumes and (body_ids is None
                             or set(body_volumes) != set(body_ids)):
            raise ValueError(
                f"{request[:40]!r}: body_volumes must cover exactly the "
                "pinned ids")
        self.declaration_required = declaration_required
        #: Per-body volumes as an UNORDERED multiset. Which body the model
        #: declares first says nothing about the part.
        self.volumes = volumes
        #: id -> (minimum, maximum), pinned ONLY where the request states
        #: coordinates. A request that says "beside it" gets `disjoint`
        #: instead, because pinning a coordinate nobody gave is grading the
        #: model against a number it was never told.
        self.placements = dict(placements) if placements else None
        self.disjoint = disjoint
        #: An EDIT's two halves, and the second is the point: `changed` is
        #: what the named body must become, `unchanged` is what every OTHER
        #: body must still be, to the last digit. An edit that changes both
        #: bodies is the failure these cases exist to catch.
        self.changed = dict(changed) if changed else None
        self.unchanged = dict(unchanged) if unchanged else None
        self.topology = dict(topology) if topology else None
        self.notes = notes


class Case:
    """One golden case. Immutable, and constructed only in this module."""

    __slots__ = (
        "name", "group", "family", "dimensions", "fixture", "turns",
        "refusal_must_name", "refusal_must_mention", "operations_permitted",
        "measure", "export", "export_names_pinned", "retired", "notes",
    )

    def __init__(
        self,
        name: str,
        group: str,
        *,
        family: str,
        dimensions: Tuple[str, ...],
        turns: Tuple[Turn, ...],
        fixture: Optional[str] = None,
        refusal_must_name: Tuple[str, ...] = (),
        refusal_must_mention: Tuple[str, ...] = (),
        operations_permitted: bool = True,
        measure: bool = False,
        export: bool = False,
        export_names_pinned: bool = False,
        retired: str = "",
        notes: str = "",
    ) -> None:
        if group not in GROUPS:
            raise ValueError(f"{name}: unknown group {group!r}")
        if not turns:
            raise ValueError(f"{name}: a case needs at least one turn")
        unknown = [d for d in dimensions if d not in DIMENSIONS]
        if unknown:
            raise ValueError(f"{name}: unknown dimensions {unknown}")
        if group == CREATION and fixture is not None:
            raise ValueError(
                f"{name}: a creation case is sent COLD -- a fixture would "
                "make it an edit, and a setup failure would be recorded as "
                "a creation failure"
            )
        if group in (EDIT, REFUSAL) and fixture not in FIXTURES:
            raise ValueError(
                f"{name}: an {group} case must start from a DETERMINISTIC "
                f"fixture; got {fixture!r}"
            )
        if group == REFUSAL:
            if len(turns) != 1:
                raise ValueError(f"{name}: a refusal case is one turn")
            if not refusal_must_name:
                raise ValueError(
                    f"{name}: a refusal that names no body is a shrug; pin "
                    "what it must say"
                )
            if operations_permitted:
                raise ValueError(
                    f"{name}: a refusal may not carry operations -- a "
                    "'clarification' carrying geometry is an edit wearing a "
                    "question's label"
                )
        if export_names_pinned and not export:
            raise ValueError(f"{name}: pins export names without exporting")
        if export_names_pinned and turns[-1].body_ids is None:
            raise ValueError(
                f"{name}: export names can only be pinned where the REQUEST "
                "names the ids; otherwise the model chooses them and truth "
                "has nothing to compare against"
            )
        self.name = name
        self.group = group
        #: The geometric family, so a result can be read by shape as well as
        #: by dimension. Four of Stage 75's six creation cases were the same
        #: part, which is why this is recorded rather than assumed.
        self.family = family
        self.dimensions = dimensions
        self.fixture = fixture
        self.turns = turns
        self.refusal_must_name = refusal_must_name
        #: Words the clarification must contain to show it addressed THIS
        #: request -- the user's own noun. Separate from naming the bodies,
        #: because those are different things and Stage 75 Phase C found
        #: collapsing them reported a case as untouched when five of its six
        #: checks had moved.
        self.refusal_must_mention = refusal_must_mention
        self.operations_permitted = operations_permitted
        #: Post-conditions, which cost NO live call: the measurement probes
        #: go through `questions.answer` and the export through the backend's
        #: own writer, both on the part the model just built.
        self.measure = measure
        self.export = export
        self.export_names_pinned = export_names_pinned
        self.retired = retired
        self.notes = notes

    @property
    def calls(self) -> int:
        """Live calls one attempt at this case costs."""
        return len(self.turns)


# =========================================================== the cases
#
# TEN creation, FIVE edit, FOUR refusal. Every request states every
# dimension, id and coordinate the truth below checks.


_CREATION: Final[Tuple[Case, ...]] = (
    Case(
        "CR-01", CREATION, family="box+cylinder", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long "
            "beside it, as two separate bodies.",
            bodies=2, declaration_required=True,
            volumes=(CUBE_40, PIN_20x30), disjoint=True,
        ),),
        measure=True, export=True,
        notes="Continuity with Stage 75's M1 family, and the only case here "
              "that repeats it. 'Beside it' states no coordinate, so the "
              "truth pins DISJOINTNESS and not a placement.",
    ),
    Case(
        "CR-02", CREATION, family="box+cylinder", dimensions=("A", "B", "I", "J", "K", "L"),
        turns=(Turn(
            'Create two separate bodies: a 40 mm cube with the id "base", '
            'and a 20 mm diameter cylinder 30 mm long with the id "post" '
            "standing beside the cube.",
            bodies=2, body_ids=("base", "post"),
            body_volumes={"base": CUBE_40, "post": PIN_20x30},
            declaration_required=True,
            volumes=(CUBE_40, PIN_20x30), disjoint=True,
        ),),
        measure=True, export=True, export_names_pinned=True,
        notes="The same part as CR-01 with the ids STATED. The pair is the "
              "controlled comparison for dimension B: one variable, whether "
              "the request names the bodies.",
    ),
    Case(
        "CR-03", CREATION, family="cylinder+cylinder", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create two separate bodies: a 30 mm diameter cylinder 20 mm "
            "long, and a 10 mm diameter cylinder 50 mm long standing beside "
            "it.",
            bodies=2, declaration_required=True,
            volumes=(CYL_30x20, CYL_10x50), disjoint=True,
            topology={"both_round": True},
        ),),
        measure=True, export=True,
        notes="No box anywhere. A model that has learnt 'a body is a box' "
              "passes every Stage 75 case and fails here.",
    ),
    Case(
        "CR-04", CREATION, family="box+box", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create two separate bodies: a 50 x 40 x 10 mm plate, and a "
            "20 mm cube beside it.",
            bodies=2, declaration_required=True,
            volumes=(PLATE_50x40x10, CUBE_20), disjoint=True,
            topology={"both_boxes": True},
        ),),
        measure=True, export=True,
        notes="Two boxes of very different proportions, so a body cannot be "
              "identified by being 'the round one'.",
    ),
    Case(
        "CR-05", CREATION, family="identical boxes", dimensions=("A", "B", "I", "J", "K", "L"),
        turns=(Turn(
            'Create two separate bodies, both 30 mm cubes: one with the id '
            '"left" and its corner at the origin, and one with the id '
            '"right" and its corner at (60, 0, 0).',
            bodies=2, body_ids=("left", "right"),
            body_volumes={"left": CUBE_30, "right": CUBE_30},
            declaration_required=True,
            volumes=(CUBE_30, CUBE_30), disjoint=True,
            placements={
                "left": ((0.0, 0.0, 0.0), (30.0, 30.0, 30.0)),
                "right": ((60.0, 0.0, 0.0), (90.0, 30.0, 30.0)),
            },
        ),),
        measure=True, export=True, export_names_pinned=True,
        notes="THE IDENTITY TRAP. Both bodies measure 27000 to the last "
              "digit, so nothing but the id and the placement distinguishes "
              "them -- not the volume, not the face count, not the size. A "
              "grader that paired answers to bodies by value cannot fail "
              "this even in principle, and a model that swapped the two "
              "would be invisible to every volume check.",
    ),
    Case(
        "CR-06", CREATION, family="three shapes", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create three separate bodies: a 60 x 40 x 8 mm plate, a 16 mm "
            "diameter cylinder 20 mm long beside it, and a 10 mm diameter "
            "cylinder 40 mm long beside that.",
            bodies=3, declaration_required=True,
            volumes=(PLATE_60x40x8, CYL_16x20, CYL_10x40), disjoint=True,
        ),),
        measure=True, export=True,
        notes="THREE bodies. Code and prompts written for 'the other body' "
              "pass every two-body test and fail here.",
    ),
    Case(
        "CR-07", CREATION, family="feature + untouched", dimensions=("A", "C", "E", "I", "J", "K"),
        turns=(Turn(
            "Create two separate bodies: a 50 x 50 x 10 mm plate with an "
            "8 mm diameter hole all the way through its centre, and a 20 mm "
            "cube standing beside it.",
            bodies=2, declaration_required=True,
            volumes=(PLATE_BORED, CUBE_20), disjoint=True,
            topology={"untouched_body_faces": BOX_FACES,
                      "drilled_body_min_faces": BOX_FACES + 1,
                      "bore_diameter": 8.0},
        ),),
        measure=True, export=True,
        notes="A feature on ONE body at creation time. The cube must come "
              "out a plain six-faced box: a bore that landed in the wrong "
              "body is a correct total volume and the wrong part.",
    ),
    Case(
        "CR-08", CREATION, family="fusion control", dimensions=("H", "K"),
        turns=(Turn(
            "Create ONE body by fusing a 40 mm cube at the origin with a "
            "20 x 20 x 20 mm box whose corner is at (30, 0, 0).",
            bodies=1, declaration_required=False, volumes=(FUSED,),
        ),),
        export=True,
        notes="THE CONTROL, and the predictable way a multi-body prompt goes "
              "wrong. One body means NO `part` declaration at all. The fused "
              "volume is strictly less than the sum of the two boxes, so a "
              "part left unfused, or fused from boxes that do not overlap, "
              "measures something else. No measurement probes: `scope_for` "
              "returns before it consults the resolver when one body is "
              "live, so this case cannot exercise attribution.",
    ),
    Case(
        "CR-09", CREATION, family="box+box placed", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create two separate bodies: a 50 x 40 x 10 mm box with its "
            "corner at the origin, and a 20 x 20 x 20 mm box with its "
            "corner at (70, 0, 0).",
            bodies=2, declaration_required=True,
            volumes=(PLATE_50x40x10, CUBE_20), disjoint=True,
            placements={
                "__any__": (((0.0, 0.0, 0.0), (50.0, 40.0, 10.0)),
                            ((70.0, 0.0, 0.0), (90.0, 20.0, 20.0))),
            },
            topology={"both_boxes": True},
        ),),
        measure=True, export=True,
        notes="PLACEMENT, stated. CR-01 and CR-04 say 'beside it' and get a "
              "disjointness check; this says where, so the bounding boxes "
              "are pinned. The ids are NOT pinned, so the placements are "
              "matched as an unordered set under `__any__`.",
    ),
    Case(
        "CR-10", CREATION, family="five bodies", dimensions=("A", "C", "I", "J", "K"),
        turns=(Turn(
            "Create five separate bodies: five 10 mm cubes in a row along "
            "X, with their corners at x = 0, 20, 40, 60 and 80, all at "
            "y = 0 and z = 0.",
            bodies=5, declaration_required=True,
            volumes=(CUBE_10,) * 5, disjoint=True,
            placements={
                "__any__": tuple(
                    ((float(20 * k), 0.0, 0.0), (float(20 * k + 10), 10.0, 10.0))
                    for k in range(5)
                ),
            },
        ),),
        measure=True, export=True,
        notes="FIVE bodies, where `MAX_BODIES` is 8 and nothing above two "
              "has ever been asked of a model. Ten operations: five boxes "
              "and five declarations. Its placements are pinned because the "
              "request gives every coordinate.",
    ),
)


_EDIT: Final[Tuple[Case, ...]] = (
    Case(
        "ED-01", EDIT, family="box+cylinder", dimensions=("D",),
        fixture=FIXTURE_CUBE_PIN,
        turns=(Turn(
            "Change the cube to 50 mm along X, keeping it 40 mm in Y and Z. "
            "Leave the cylinder unchanged.",
            bodies=2, declaration_required=True,
            volumes=(CUBE_50x40x40, PIN_20x30), disjoint=True,
            changed={"cube": CUBE_50x40x40}, unchanged={"pin": PIN_20x30},
        ),),
        notes="The plainest named-body edit. Both halves are checked: the "
              "cube must become 80000 AND the pin must still be "
              "9424.77796076938 to the last digit.",
    ),
    Case(
        "ED-02", EDIT, family="box+cylinder", dimensions=("D", "E"),
        fixture=FIXTURE_CUBE_PIN,
        turns=(Turn(
            "Put a 6 mm diameter hole all the way through the cylinder "
            "along its axis. Leave the cube unchanged.",
            bodies=2, declaration_required=True,
            volumes=(CUBE_40, PIN_BORED), disjoint=True,
            changed={"pin": PIN_BORED}, unchanged={"cube": CUBE_40},
            topology={"untouched_body_faces": BOX_FACES},
        ),),
        notes="A FEATURE on a named body, and the named body is the second "
              "one. Stage 72 measured this deterministically at "
              "8576.547944300135; this asks a model for it.",
    ),
    Case(
        "ED-05", EDIT, family="box+cylinder", dimensions=("D",),
        fixture=FIXTURE_CUBE_PIN,
        turns=(Turn(
            "Make the cylinder 50 mm long, keeping its diameter at 20 mm. "
            "Leave the cube unchanged.",
            bodies=2, declaration_required=True,
            volumes=(CUBE_40, PIN_20x50), disjoint=True,
            changed={"pin": PIN_20x50}, unchanged={"cube": CUBE_40},
        ),),
        notes="Edits the SECOND body, by name, with no feature involved. "
              "Paired with ED-01 it isolates 'which body' from 'what kind "
              "of change'.",
    ),
    Case(
        "ED-03", EDIT, family="box+cylinder", dimensions=("D", "E", "M"),
        fixture=FIXTURE_CUBE_PIN,
        turns=(
            Turn(
                "Change the cube to 60 mm along X, keeping it 40 mm in Y "
                "and Z. Leave the cylinder unchanged.",
                bodies=2, declaration_required=True,
                volumes=(CUBE_60x40x40, PIN_20x30), disjoint=True,
                changed={"cube": CUBE_60x40x40},
                unchanged={"pin": PIN_20x30},
            ),
            Turn(
                "Now put a 10 mm diameter hole all the way through the "
                "centre of that same cube along Z. Leave the cylinder "
                "unchanged.",
                bodies=2, declaration_required=True,
                volumes=(CUBE_60_BORED, PIN_20x30), disjoint=True,
                changed={"cube": CUBE_60_BORED},
                unchanged={"pin": PIN_20x30},
            ),
        ),
        notes="TWO SEQUENTIAL EDITS on the same body. The second turn must "
              "keep the first turn's change: a model that rebuilds the part "
              "from the original request writes a 40 mm cube with a hole "
              "and fails on volume, not on the hole.",
    ),
    Case(
        "ED-04", EDIT, family="box+cylinder", dimensions=("D", "E", "M", "N"),
        fixture=FIXTURE_CUBE_PIN,
        turns=(
            Turn(
                "Change the cube to 60 mm along X, keeping it 40 mm in Y "
                "and Z. Leave the cylinder unchanged.",
                bodies=2, declaration_required=True,
                volumes=(CUBE_60x40x40, PIN_20x30), disjoint=True,
                changed={"cube": CUBE_60x40x40},
                unchanged={"pin": PIN_20x30},
            ),
            Turn(
                "Now make the cylinder 50 mm long, keeping its diameter at "
                "20 mm. Leave the cube unchanged.",
                bodies=2, declaration_required=True,
                volumes=(CUBE_60x40x40, PIN_20x50), disjoint=True,
                changed={"pin": PIN_20x50},
                unchanged={"cube": CUBE_60x40x40},
            ),
            Turn(
                "Now put a 10 mm diameter hole all the way through the "
                "centre of the cube along Z. Leave the cylinder unchanged.",
                bodies=2, declaration_required=True,
                volumes=(CUBE_60_BORED, PIN_20x50), disjoint=True,
                changed={"cube": CUBE_60_BORED},
                unchanged={"pin": PIN_20x50},
            ),
        ),
        notes="INTERLEAVED. Each turn names a different body from the one "
              "before, and every turn must preserve BOTH earlier changes. "
              "Three turns is the shortest chain in which a model can lose "
              "an edit it made two turns ago.",
    ),
)


_REFUSAL: Final[Tuple[Case, ...]] = (
    Case(
        "RF-01", REFUSAL, family="box+cylinder", dimensions=("F", "O"),
        fixture=FIXTURE_CUBE_PIN, operations_permitted=False,
        refusal_must_name=("cube", "pin"),
        turns=(Turn("Make it 20 mm taller."),),
        notes="Names NEITHER body. Guessing is worse than asking, and a "
              "wrong guess edits the wrong body and reports success.",
    ),
    Case(
        "RF-02", REFUSAL, family="box+cylinder", dimensions=("G", "O"),
        fixture=FIXTURE_CUBE_PIN, operations_permitted=False,
        refusal_must_name=("cube", "pin"),
        refusal_must_mention=("flange",),
        turns=(Turn("Make the flange 5 mm thicker."),),
        notes="Names a body this part does NOT have. Stage 75 Phases D and "
              "E measured this shape at 34/48 = 70.8 %% on the committed "
              "prompt and proved the cause. **That rate is carried forward "
              "as a KNOWN FLOOR, not re-discovered**: a result near it here "
              "is a reproduction, and only a result far from it is news.",
    ),
    Case(
        "RF-03", REFUSAL, family="three shapes", dimensions=("F", "O"),
        fixture=FIXTURE_TRIO, operations_permitted=False,
        refusal_must_name=("plate", "boss", "rod"),
        turns=(Turn("Make it 10 mm wider."),),
        notes="The ambiguous reference generalised past TWO bodies. A "
              "refusal that names two of the three is not an answer either, "
              "and nothing about the product may be written for exactly "
              "two.",
    ),
    Case(
        "RF-04", REFUSAL, family="three shapes", dimensions=("F", "O"),
        fixture=FIXTURE_TRIO, operations_permitted=False,
        refusal_must_name=("plate", "boss", "rod"),
        turns=(Turn("Make the second one bigger."),),
        notes="An ORDINAL reference. 'The second one' is not an id and the "
              "plan's order is not a name the person can be assumed to "
              "share, so this must ask rather than count.",
    ),
)


CASES: Final[Tuple[Case, ...]] = _CREATION + _EDIT + _REFUSAL

RETIRED: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if c.retired)
ACTIVE: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if not c.retired)
CASES_BY_NAME: Final[Mapping[str, Case]] = {c.name: c for c in CASES}

CREATION_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == CREATION and not c.retired)
EDIT_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == EDIT and not c.retired)
REFUSAL_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == REFUSAL and not c.retired)

#: Live calls ONE attempt at the whole corpus costs. An edit case with three
#: turns costs three.
CALLS_PER_ATTEMPT: Final[int] = sum(
    c.calls for c in CASES if not c.retired)


# ------------------------------------------------- the failure taxonomy
#
# The brief's A-Q, one code per distinguishable way a multi-body answer goes
# wrong. Stable strings, so a run recorded today compares to one recorded
# later. There is deliberately NO generic "model failure": a taxonomy whose
# largest class is "other" has not classified anything.

A_MISSING_BODY = "A:missing_body"
B_EXTRA_BODY = "B:extra_body"
C_WRONG_IDENTITY = "C:wrong_identity"
D_WRONG_TARGET = "D:wrong_target"
E_CROSS_BODY_EDIT = "E:cross_body_edit"
F_WRONG_PLACEMENT = "F:wrong_placement"
G_WRONG_DIMENSION = "G:wrong_dimension"
H_UNWANTED_FUSION = "H:unwanted_fusion"
I_INVALID_PLAN = "I:invalid_plan"
J_BAD_REFUSAL = "J:bad_refusal"
K_BAD_CLARIFICATION = "K:bad_clarification"
L_MEASUREMENT_ATTRIBUTION = "L:measurement_attribution"
M_AGGREGATE_ERROR = "M:aggregate_measurement"
N_EXPORT_OMISSION = "N:export_omission"
O_EXPORT_GEOMETRY = "O:export_geometry"
P_EXPORT_IDENTITY_UNPROVEN = "P:export_identity_unproven"
Q_OTHER = "Q:other"

TAXONOMY: Final[Tuple[str, ...]] = (
    A_MISSING_BODY, B_EXTRA_BODY, C_WRONG_IDENTITY, D_WRONG_TARGET,
    E_CROSS_BODY_EDIT, F_WRONG_PLACEMENT, G_WRONG_DIMENSION,
    H_UNWANTED_FUSION, I_INVALID_PLAN, J_BAD_REFUSAL, K_BAD_CLARIFICATION,
    L_MEASUREMENT_ATTRIBUTION, M_AGGREGATE_ERROR, N_EXPORT_OMISSION,
    O_EXPORT_GEOMETRY, P_EXPORT_IDENTITY_UNPROVEN, Q_OTHER,
)

#: **P is a STATE, not a failure**, and is excluded from every failure
#: count. Stage 76 measured that which SOLID carries which NAME cannot be
#: proven without a per-engine assembly reader the two engines do not share,
#: so every export carries this code and none is penalised for it. Recording
#: it as a failure would make an honest limit look like a defect and would
#: put a floor of 0 % on every export rate.
INFORMATIONAL: Final[Tuple[str, ...]] = (P_EXPORT_IDENTITY_UNPROVEN,)
FAILURE_CODES: Final[Tuple[str, ...]] = tuple(
    code for code in TAXONOMY if code not in INFORMATIONAL)


# --------------------------------------------------- the outcome labels
#
# Mirrored from the project's five, and asserted against `ground_truth75`'s
# rather than assumed. Only the first is evidence about a model.

MODEL_GENERATED: Final[str] = "MODEL_GENERATED"
DETERMINISTIC: Final[str] = "DETERMINISTIC"
FALLBACK: Final[str] = "FALLBACK"
REFUSED: Final[str] = "REFUSED"
PROVIDER_ERROR: Final[str] = "PROVIDER_ERROR"
OUTCOMES: Final[Tuple[str, ...]] = (
    MODEL_GENERATED, DETERMINISTIC, FALLBACK, REFUSED, PROVIDER_ERROR)
COUNTS_AS_MODEL_EVIDENCE: Final[Tuple[str, ...]] = (MODEL_GENERATED,)


def _turn_payload(turn: Turn) -> dict:
    return {
        "request": turn.request,
        "bodies": turn.bodies,
        "body_ids": turn.body_ids,
        "body_volumes": dict(turn.body_volumes) if turn.body_volumes else None,
        "declaration_required": turn.declaration_required,
        "volumes": turn.volumes,
        "placements": dict(turn.placements) if turn.placements else None,
        "disjoint": turn.disjoint,
        "changed": dict(turn.changed) if turn.changed else None,
        "unchanged": dict(turn.unchanged) if turn.unchanged else None,
        "topology": dict(turn.topology) if turn.topology else None,
    }


def expected(case_name: str) -> dict:
    """The ground truth for one case, BY NAME.

    Takes a case name, and nothing else. Never a plan, never a shape, never
    a measurement, never an answer, never a file. **The signature is the
    guarantee**: there is no parameter by which anything a model or a kernel
    produced could reach this function, so the truth cannot drift toward the
    output. A test asserts it by inspecting the signature and by walking
    this module's AST, because a docstring saying so is not a guarantee.
    """
    case = CASES_BY_NAME.get(case_name)
    if case is None:
        raise KeyError(
            f"unknown case {case_name!r}; known: {', '.join(CASES_BY_NAME)}")
    if case.retired:
        raise ValueError(
            f"{case_name} is RETIRED and must not be scored again: "
            f"{case.retired}")
    return {
        "name": case.name,
        "group": case.group,
        "family": case.family,
        "dimensions": case.dimensions,
        "fixture": case.fixture,
        "turns": tuple(_turn_payload(t) for t in case.turns),
        "calls": case.calls,
        "refusal_must_name": case.refusal_must_name,
        "refusal_must_mention": case.refusal_must_mention,
        "operations_permitted": case.operations_permitted,
        "measure": case.measure,
        "export": case.export,
        "export_names_pinned": case.export_names_pinned,
        "volume_tolerance": VOLUME_TOLERANCE,
        "placement_tolerance": PLACEMENT_TOLERANCE,
    }


def expected_turn(case_name: str, index: int) -> dict:
    """One turn's expectations, by case NAME and turn INDEX.

    An integer index is not model output: it says WHICH request was sent,
    not what came back.
    """
    truth = expected(case_name)
    turns = truth["turns"]
    if not 0 <= index < len(turns):
        raise IndexError(
            f"{case_name} has {len(turns)} turn(s); asked for index {index}")
    return dict(turns[index])


def requests_to_send(case_name: str) -> Tuple[str, ...]:
    """Only what to ASK, in order. Never what the answer must be.

    A NARROWED view, so the runner cannot read an expectation even by
    accident -- the same guarantee `ground_truth76.probes_to_ask` gives the
    Stage 76 observer, for the same reason.
    """
    return tuple(t["request"] for t in expected(case_name)["turns"])


__all__ = [
    "ACTIVE", "AGGREGATE", "BOX_FACES", "CALLS_PER_ATTEMPT", "CASES",
    "CASES_BY_NAME", "COUNTS_AS_MODEL_EVIDENCE", "CREATION",
    "CREATION_CASES", "CUBE_10", "CUBE_20", "CUBE_30", "CUBE_40",
    "CUBE_50x40x40", "CUBE_60_BORED", "CUBE_60x40x40", "CYLINDER_FACES",
    "CYL_10x40", "CYL_10x50", "CYL_16x20", "CYL_30x20", "Case",
    "DETERMINISTIC", "DIMENSIONS", "EDIT", "EDIT_CASES", "ENGINES", "EXPORT",
    "FAILURE_CODES", "FALLBACK", "FIXTURES", "FIXTURE_BODIES",
    "FIXTURE_CUBE_PIN", "FIXTURE_TRIO", "FUSED", "FUSE_OVERLAP", "GROUPS",
    "INFORMATIONAL", "MEASUREMENT", "MODEL", "MODEL_GENERATED", "OUTCOMES",
    "PIN_20x30", "PIN_20x50", "PIN_BORED", "PLACEMENT_TOLERANCE",
    "PLATE_50x40x10", "PLATE_60x40x8", "PLATE_BORED", "POST_CONDITIONS",
    "PROMPT_CHARACTERS", "PROMPT_FINGERPRINT", "PROMPT_VERSION",
    "PROVIDER_ERROR", "REFUSAL", "REFUSAL_CASES", "REFUSED", "RETIRED",
    "SCHEMA_FINGERPRINT", "SCHEMA_INLINED", "SCHEMA_NAME", "TAXONOMY",
    "Turn", "VOLUME_TOLERANCE", "expected", "expected_turn",
    "requests_to_send",
]
