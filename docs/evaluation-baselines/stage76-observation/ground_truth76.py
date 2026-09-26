"""The immutable truth for Stage 76's multi-body OBSERVATION layer.

Stage 75 built an evaluator that can grade what a model says and what the
kernel builds. It cannot grade two of the nine dimensions the broader corpus
needs, and `phase-e-r2-tail/corpus-design.md` says exactly why:

    Dimensions 8 and 9 need a second observer, because they are not
    properties of a plan. A measurement question goes through
    `questions.answer` and an export through `/session/export`, and neither
    is visible in a `PlanGenerationResult`.

This module is that observer's truth, and it is the same discipline Stage
75's is held to, for the same reasons each of them cost a stage's headline
number when it was missed:

* **Truth takes a case NAME and nothing else.** :func:`expected` has no
  parameter a plan, a shape, a measurement, an answer or an export file
  could enter by. Stage 67 derived its expected plate thickness from the
  model's own plan and graded parts against their own answer; a criterion
  that grades a part against its own answer cannot fail it.
* **Nothing here is derived from a body count that was observed.** The
  number of bodies each case must have is a constant written down before
  anything ran, never ``len(whatever came back)``.
* **Every volume is a closed form**, computed from the dimensions in the
  case, never copied from a kernel. The kernel is the thing being checked.
* **Never edit an expectation after seeing a score.** A case whose
  expectation turns out to be wrong is RETIRED verbatim with a note and
  replaced by a new case with a new name, the way Stage 75 retired M4, M6,
  M7, M8 and N1.

IMPORTS. ``math`` and ``typing``, and nothing else. Not the product, not the
observer, not Stage 75. A truth module that imports the code it judges can
be made to agree with it, and the import list is the only guarantee of that
which a reader can check in one glance. The closed forms below deliberately
REPEAT Stage 75's arithmetic rather than importing it -- both derive from
the same dimensions, and `test_stage76_observation` asserts the two agree,
so a drift is caught without either module depending on the other.

WHAT THIS MODULE IS NOT. It is not a live corpus. The five cases here are
DETERMINISTIC fixtures whose only job is to prove the observer bites before
a single model call is spent on it, and every number they produce is
`DETERMINISTIC` and says nothing whatever about a model. The broader corpus
that will use this observer supplies its own requests and its own cases; it
reads its truth the same way, by name.
"""

from __future__ import annotations

import math
from typing import Final, Mapping, Optional, Tuple


# ---------------------------------------------------------------- identity

#: The engines this observer is proven against. Recorded because an export
#: verdict is a statement about a writer, and two writers are two facts.
ENGINES: Final[Tuple[str, ...]] = ("cadquery", "freecad")


# ------------------------------------------------------- the closed forms
#
# Every one of these is arithmetic on a dimension the case states. None was
# read off a kernel, and none may ever be replaced by a measured value -- the
# kernel agreeing with its own output proves nothing.

CUBE_EDGE: Final[float] = 40.0
CUBE_VOLUME: Final[float] = CUBE_EDGE ** 3                       # 64000.0

PIN_DIAMETER: Final[float] = 20.0
PIN_LENGTH: Final[float] = 30.0
PIN_VOLUME: Final[float] = math.pi * (PIN_DIAMETER / 2.0) ** 2 * PIN_LENGTH

#: The third body of the three-body case. A different shape AND a different
#: size from both others on purpose: a case whose bodies share a volume
#: cannot tell a per-body answer from a lucky one.
SLAB_X: Final[float] = 30.0
SLAB_Y: Final[float] = 10.0
SLAB_Z: Final[float] = 5.0
SLAB_VOLUME: Final[float] = SLAB_X * SLAB_Y * SLAB_Z             # 1500.0

#: The fused case. Two boxes that OVERLAP, so the fused volume is strictly
#: less than the sum -- which is the whole point of the control: a grader
#: that adds the two apart would get a different number, and a part that was
#: never fused would measure the sum.
FUSE_A_EDGE: Final[float] = 40.0
FUSE_B_EDGE: Final[float] = 20.0
#: The smaller box is placed so that it overlaps the larger one over a
#: 10 x 20 x 20 region: it sits at x = 30, spanning 30..50 where the big box
#: spans 0..40, and is flush in y and z over 0..20.
FUSE_B_ORIGIN_X: Final[float] = 30.0
FUSE_OVERLAP: Final[float] = 10.0 * FUSE_B_EDGE * FUSE_B_EDGE    # 4000.0
FUSE_VOLUME: Final[float] = (
    FUSE_A_EDGE ** 3 + FUSE_B_EDGE ** 3 - FUSE_OVERLAP
)                                                                # 68000.0

#: The consumed-body case: a plate with a boss fused onto it, and a pin
#: standing separately. Two bodies live, one consumed.
PLATE_X: Final[float] = 50.0
PLATE_Y: Final[float] = 50.0
PLATE_Z: Final[float] = 8.0
PLATE_VOLUME: Final[float] = PLATE_X * PLATE_Y * PLATE_Z         # 20000.0
BOSS_DIAMETER: Final[float] = 12.0
BOSS_HEIGHT: Final[float] = 25.0
#: The boss stands ON the plate -- its base is at z = PLATE_Z -- so the two
#: touch on a face and share no volume. A fuse of two solids meeting on a
#: face is the sum exactly.
BOSS_VOLUME: Final[float] = math.pi * (BOSS_DIAMETER / 2.0) ** 2 * BOSS_HEIGHT
PLATE_WITH_BOSS_VOLUME: Final[float] = PLATE_VOLUME + BOSS_VOLUME

#: Relative, never equality. The same tolerance Stage 75 uses, repeated for
#: the same reason the volumes are.
VOLUME_TOLERANCE: Final[float] = 1e-6


# ------------------------------------------------------------ probe kinds
#
# The five cases `body_reference.resolve_body` distinguishes, plus the two
# aggregate questions Stage 73 added. A probe kind is what the request DOES,
# never what the answer turns out to be.

NAMED: Final[str] = "named"                       # names one live body
UNNAMED_SINGLE: Final[str] = "unnamed_single"     # names none, one is live
UNNAMED_SEVERAL: Final[str] = "unnamed_several"   # names none, several live
TWO_AT_ONCE: Final[str] = "two_at_once"           # names two live bodies
CONSUMED: Final[str] = "consumed"                 # names a consumed body
AGGREGATE_TOTAL: Final[str] = "aggregate_total"   # asks for a sum
AGGREGATE_SIZE: Final[str] = "aggregate_size"     # asks the containing box

PROBE_KINDS: Final[Tuple[str, ...]] = (
    NAMED, UNNAMED_SINGLE, UNNAMED_SEVERAL, TWO_AT_ONCE, CONSUMED,
    AGGREGATE_TOTAL, AGGREGATE_SIZE,
)

#: The five the brief names, kept as their own tuple so a case set that
#: silently stops exercising one is visible.
RESOLVER_KINDS: Final[Tuple[str, ...]] = (
    NAMED, UNNAMED_SINGLE, UNNAMED_SEVERAL, TWO_AT_ONCE, CONSUMED,
)


# --------------------------------------------------------- probe outcomes
#
# Three, and the difference between the last two is the one Stage 73 was
# built around: REFUSED reaches the person, DECLINED falls through to a
# model. Recording them as one outcome would hide exactly the failure this
# observer exists to see.

ANSWERED: Final[str] = "answered"    # an Answer came back
REFUSED: Final[str] = "refused"      # QuestionRefused: recognised, no answer
DECLINED: Final[str] = "declined"    # None: not a question this answers
PROBE_OUTCOMES: Final[Tuple[str, ...]] = (ANSWERED, REFUSED, DECLINED)


# --------------------------------------------------------- the provenances
#
# Mirrored from `questions`, and `test_stage76_observation` asserts the two
# agree. Mirrored rather than imported because this module imports nothing
# from the product; asserted rather than assumed because a fourth provenance
# added there and not here would silently stop being gradeable.

MEASURED: Final[str] = "measured"
DECLARED: Final[str] = "declared"
CALCULATED: Final[str] = "calculated"
ASSUMED: Final[str] = "assumed"
PROVENANCE: Final[Tuple[str, ...]] = (MEASURED, DECLARED, CALCULATED, ASSUMED)


# ------------------------------------------------------- where it came from
#
# The five labels the project keeps distinct everywhere, MIRRORED here for
# the same reason the provenances are: this module imports nothing from the
# product, and `test_stage76_observation` asserts these strings equal
# `ground_truth75`'s so the two cannot drift.
#
# They matter more in this stage than in any before it, because everything
# Stage 76 produces is DETERMINISTIC. Five fixture parts built by hand with
# no model in the room say a great deal about the OBSERVER and nothing
# whatever about a model, and an observation that could be labelled
# MODEL_GENERATED without a live call would make that impossible to tell
# afterwards.

SOURCE_MODEL_GENERATED: Final[str] = "MODEL_GENERATED"
SOURCE_DETERMINISTIC: Final[str] = "DETERMINISTIC"
SOURCE_FALLBACK: Final[str] = "FALLBACK"
SOURCE_REFUSED: Final[str] = "REFUSED"
SOURCE_PROVIDER_ERROR: Final[str] = "PROVIDER_ERROR"

SOURCES: Final[Tuple[str, ...]] = (
    SOURCE_MODEL_GENERATED, SOURCE_DETERMINISTIC, SOURCE_FALLBACK,
    SOURCE_REFUSED, SOURCE_PROVIDER_ERROR,
)

#: The ONE label that is evidence about a model. A fallback is not, a
#: refusal is not, and a deterministic build is not.
COUNTS_AS_MODEL_EVIDENCE: Final[Tuple[str, ...]] = (SOURCE_MODEL_GENERATED,)


# ------------------------------------------------------- the export ladder
#
# SIX levels, strictly increasing: each one is every check below it, plus one
# more. Only the top is a success.
#
# The ladder exists because "the export worked" has at least five distinct
# ways of being false, and both of the interesting ones leave a file that
# every casual check passes. Stage 74 measured both:
#
#   * FreeCAD's `Part.export`, handed raw shapes, leaves a well-formed
#     1 640-byte STEP that reads back as ZERO solids. It exists, it is
#     non-empty, it parses. Only counting solids sees it.
#   * Handed a compound, BOTH engines write a correct two-solid STEP whose
#     bodies are called `Open CASCADE STEP translator 7.9 1.1` and `1.2`.
#     Every volume, face and edge total matches and the identity is gone.
#     No count can see that.
#
# So a file existing is level B at best, and the brief's rule -- do not make
# "file exists" equivalent to export success -- is structural here rather
# than a thing to remember.

LEVEL_A: Final[str] = "A:not_written"
LEVEL_B: Final[str] = "B:unreadable"
LEVEL_C: Final[str] = "C:wrong_solid_count"
LEVEL_D: Final[str] = "D:names_missing"
LEVEL_E: Final[str] = "E:geometry_mismatch"
LEVEL_F: Final[str] = "F:verified"

#: In order, weakest first. An observation's level is the HIGHEST rung whose
#: check and every check below it passed.
EXPORT_LEVELS: Final[Tuple[str, ...]] = (
    LEVEL_A, LEVEL_B, LEVEL_C, LEVEL_D, LEVEL_E, LEVEL_F,
)

#: What each rung means, in one sentence, so a recorded verdict can be read
#: without this module.
EXPORT_LEVEL_MEANING: Final[Mapping[str, str]] = {
    LEVEL_A: "the writer raised, or produced no file: nothing was exported",
    LEVEL_B: "bytes were written but the file cannot be read back as STEP",
    LEVEL_C: "the file reads back, but not as the number of solids that "
             "went in -- 0 means an empty file, 1 means they were fused, "
             "and anything else means a body was dropped or invented",
    LEVEL_D: "the right number of solids came back, but at least one body "
             "id never reached the file, so the bodies cannot be told apart",
    LEVEL_E: "the right number of solids came back under the right names, "
             "but a solid's volume is not the one the part was built to",
    LEVEL_F: "the right number of solids, every id present, and every "
             "volume matching its closed form",
}

#: **The honest limit of level F, stated once and carried on every
#: observation.** F proves each id REACHED the file as a `PRODUCT` and that
#: the set of volumes is right. It does NOT prove which solid carries which
#: name: that needs the PRODUCT -> SHAPE_REPRESENTATION ->
#: MANIFOLD_SOLID_BREP chain followed per engine, and the two engines'
#: assembly readers differ. Stage 74 wrote this down rather than papering
#: over it with a parity claim neither engine supports, and nothing here may
#: claim more than it.
IDENTITY_UNPROVEN: Final[str] = "unproven"

#: **The single-body writer puts NO body name in the file, and that is
#: MEASURED rather than assumed.** `export_step` on a one-body part writes
#: one product called `Open CASCADE STEP translator 7.9 1` (CadQuery 2.8.0)
#: or `... 7.7 1` (FreeCAD 1.0.0) -- the translator's own string, which is
#: exactly the anonymous product Stage 74 refuses an ASSEMBLY for.
#:
#: It is not a defect there: a file holding one solid has nothing to tell
#: apart, and Stage 74 kept that path unchanged deliberately. But it means a
#: single-body export can never reach the name rung on evidence, so a case
#: that uses that writer pins `export_names = ()` and this state, and its
#: level F is a WEAKER claim than an assembly's. Saying which is why the
#: state is recorded on every row rather than left to be inferred from an
#: empty list.
IDENTITY_NOT_WRITTEN: Final[str] = "not_written"

IDENTITY_STATES: Final[Tuple[str, ...]] = (
    IDENTITY_UNPROVEN, IDENTITY_NOT_WRITTEN,
)

#: Kept as the name the observer stamps on every row: a standing statement
#: about this INSTRUMENT ("it never proves binding"), not a verdict on one
#: file. The grader's `identity_state` is the per-case fact.
IDENTITY_BINDING: Final[str] = IDENTITY_UNPROVEN
IDENTITY_BINDING_NOTE: Final[str] = (
    "a body id found in the file is a PRODUCT of that name, and the volume "
    "multiset matches; which SOLID carries which NAME is NOT proven here. "
    "Binding them needs the PRODUCT -> SHAPE_REPRESENTATION -> "
    "MANIFOLD_SOLID_BREP chain followed per engine, and the two engines' "
    "assembly readers differ"
)

#: What an export observation may NEVER be read off. Named so a test can
#: assert the observer consults none of them.
NOT_EVIDENCE_OF_IDENTITY: Final[Tuple[str, ...]] = (
    "the file's name",
    "the HTTP x-cad-bodies header",
    "the STEP header's FILE_NAME or originating-system string",
    "the translator's own product names",
)


# ------------------------------------------------------------- the groups

MULTI: Final[str] = "multi_body"
SINGLE: Final[str] = "single_body"


class Probe:
    """One question, and what the right answer to it is. Immutable."""

    __slots__ = ("name", "kind", "text", "outcome", "about", "aggregate",
                 "provenance", "value", "must_name", "notes")

    def __init__(
        self,
        name: str,
        kind: str,
        text: str,
        outcome: str,
        *,
        about: Optional[str] = None,
        aggregate: bool = False,
        provenance: Optional[str] = None,
        value: Optional[float] = None,
        must_name: Tuple[str, ...] = (),
        notes: str = "",
    ) -> None:
        if kind not in PROBE_KINDS:
            raise ValueError(f"{name}: unknown probe kind {kind!r}")
        if outcome not in PROBE_OUTCOMES:
            raise ValueError(f"{name}: unknown outcome {outcome!r}")
        if provenance is not None and provenance not in PROVENANCE:
            raise ValueError(f"{name}: unknown provenance {provenance!r}")
        if outcome == ANSWERED and provenance is None:
            raise ValueError(
                f"{name}: an answered probe must pin its provenance -- an "
                "answer whose provenance is not checked is a number with no "
                "claim attached, which is the thing `questions` exists to "
                "prevent"
            )
        if outcome != ANSWERED and (value is not None or about is not None):
            raise ValueError(
                f"{name}: only an ANSWERED probe has a value or a body"
            )
        if outcome == REFUSED and not must_name:
            raise ValueError(
                f"{name}: a refusal that names no body is a shrug; pin what "
                "it must say"
            )
        self.name = name
        self.kind = kind
        #: The question, VERBATIM. The request is a variable of the
        #: experiment (Stage 68), so editing this text makes a NEW probe with
        #: a new name and never edits an existing one.
        self.text = text
        self.outcome = outcome
        #: Which body the answer must be ABOUT, by id. `None` for a
        #: single-body part, where the product deliberately says no id at all
        #: so its wording stays byte-for-byte what it was before Stage 73.
        self.about = about
        self.aggregate = aggregate
        self.provenance = provenance
        #: The closed form the answer must carry, or `None` when the probe
        #: pins only the shape of the answer.
        self.value = value
        #: Every body a refusal must name. Checked against the refusal's own
        #: words, and a refusal naming none of them is not an answer.
        self.must_name = must_name
        self.notes = notes


class Case:
    """One observed part: what it is, what it measures, what it exports."""

    __slots__ = ("name", "group", "body_ids", "volumes", "consumed",
                 "probes", "export_level", "export_solids", "export_names",
                 "export_volumes", "export_identity", "single_body_writer",
                 "retired", "notes")

    def __init__(
        self,
        name: str,
        group: str,
        *,
        body_ids: Tuple[str, ...],
        volumes: Tuple[float, ...],
        probes: Tuple[Probe, ...],
        consumed: Mapping[str, str] = (),
        export_level: str = LEVEL_F,
        export_names: Optional[Tuple[str, ...]] = None,
        export_volumes: Optional[Tuple[float, ...]] = None,
        export_identity: str = IDENTITY_UNPROVEN,
        single_body_writer: bool = False,
        retired: str = "",
        notes: str = "",
    ) -> None:
        if group not in (MULTI, SINGLE):
            raise ValueError(f"{name}: unknown group {group!r}")
        if len(body_ids) != len(volumes):
            raise ValueError(
                f"{name}: {len(body_ids)} bodies and {len(volumes)} volumes"
            )
        if export_level not in EXPORT_LEVELS:
            raise ValueError(f"{name}: unknown export level {export_level!r}")
        if export_identity not in IDENTITY_STATES:
            raise ValueError(
                f"{name}: unknown identity state {export_identity!r}"
            )
        if single_body_writer and export_identity != IDENTITY_NOT_WRITTEN:
            raise ValueError(
                f"{name}: the single-body writer puts no body name in the "
                "file -- measured on both engines -- so a case using it "
                "cannot expect one"
            )
        if export_identity == IDENTITY_NOT_WRITTEN and export_names:
            raise ValueError(
                f"{name}: expects body names from a writer that writes none"
            )
        self.name = name
        self.group = group
        #: The live bodies, by id, in declaration order. A CONSTANT: the
        #: number of bodies a case must have is written down before anything
        #: ran, and is never `len(what came back)`.
        self.body_ids = body_ids
        #: Per-body volumes, as an UNORDERED multiset. Order is not meaning.
        self.volumes = volumes
        #: Each consumed body, and what consumed it. Empty when none is.
        self.consumed = dict(consumed or {})
        self.probes = probes
        self.export_level = export_level
        #: What the written file must contain. Defaults to the live bodies,
        #: spelled out rather than inferred so a case can pin a file that is
        #: deliberately expected to hold something else.
        self.export_solids = len(body_ids)
        self.export_names = (
            export_names if export_names is not None else body_ids
        )
        self.export_volumes = (
            export_volumes if export_volumes is not None else volumes
        )
        #: True when the export must go through the SINGLE-body writer
        #: (`export_step`) rather than the assembly writer. Stage 74 kept
        #: that path unchanged deliberately, and a case pins it so it cannot
        #: quietly start going through the other one.
        #: Whether the written file carries the body ids at all. Pinned
        #: rather than inferred from an empty name list, so a reader of a
        #: recorded verdict is told WHY no name was checked.
        self.export_identity = export_identity
        self.single_body_writer = single_body_writer
        self.retired = retired
        self.notes = notes


# ------------------------------------------------------------- the cases
#
# FIVE, and each exists for a reason no other one covers.

_X1_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X1-named-cube", NAMED,
          "What is the volume of the cube?", ANSWERED,
          about="cube", provenance=MEASURED, value=CUBE_VOLUME,
          notes="The base per-body question. The answer must be about the "
                "body NAMED, and it must carry that body's own number."),
    Probe("X1-named-pin", NAMED,
          "What is the volume of the pin?", ANSWERED,
          about="pin", provenance=MEASURED, value=PIN_VOLUME,
          notes="The same question about the other body. Two probes whose "
                "expected values differ by a factor of seven: a grader that "
                "paired answers to bodies by VALUE would still pass here, "
                "which is why the swapped-value fixture exists."),
    Probe("X1-unnamed", UNNAMED_SEVERAL,
          "What is the volume?", REFUSED,
          must_name=("cube", "pin"),
          notes="The one that matters most. Two bodies and no name: there "
                "is no *the* volume, and answering about either one is "
                "wrong in a way nothing downstream can detect."),
    Probe("X1-two-at-once", TWO_AT_ONCE,
          "What is the volume of the cube and the pin?", REFUSED,
          must_name=("cube", "pin"),
          notes="Names two bodies. One question about one body; naming both "
                "does not make a single answer exist."),
    Probe("X1-total", AGGREGATE_TOTAL,
          "What is the total volume?", ANSWERED,
          about=None, aggregate=True, provenance=CALCULATED,
          value=CUBE_VOLUME + PIN_VOLUME,
          notes="A sum IS answerable, and it is CALCULATED rather than "
                "MEASURED: no kernel measured the two bodies together."),
    Probe("X1-overall-size", AGGREGATE_SIZE,
          "What is the overall size of the whole part?", ANSWERED,
          about=None, aggregate=True, provenance=ASSUMED,
          notes="ASSUMED, the fourth provenance and the only one of it: the "
                "box round two bodies standing apart also contains the air "
                "between them, which nothing measured and nothing declared."),
)

_X2_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X2-named-block", NAMED,
          "What is the volume of the block?", ANSWERED,
          about="block", provenance=MEASURED, value=CUBE_VOLUME),
    Probe("X2-named-rod", NAMED,
          "What is the volume of the rod?", ANSWERED,
          about="rod", provenance=MEASURED, value=PIN_VOLUME),
    Probe("X2-named-shim", NAMED,
          "What is the volume of the shim?", ANSWERED,
          about="shim", provenance=MEASURED, value=SLAB_VOLUME),
    Probe("X2-unnamed", UNNAMED_SEVERAL,
          "What is the volume?", REFUSED,
          must_name=("block", "rod", "shim"),
          notes="THREE bodies, so a refusal that names two is not an "
                "answer either. Nothing about the product is allowed to be "
                "written for exactly two."),
    Probe("X2-two-at-once", TWO_AT_ONCE,
          "What is the volume of the block and the shim?", REFUSED,
          must_name=("block", "shim")),
    Probe("X2-total", AGGREGATE_TOTAL,
          "What is the total volume?", ANSWERED,
          about=None, aggregate=True, provenance=CALCULATED,
          value=CUBE_VOLUME + PIN_VOLUME + SLAB_VOLUME),
)

_X3_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X3-unnamed", UNNAMED_SINGLE,
          "What is the volume?", ANSWERED,
          about=None, provenance=MEASURED, value=CUBE_VOLUME,
          notes="The single-body regression. One body, so naming it is "
                "optional AND the answer carries no id prefix at all -- the "
                "wording is byte-for-byte what it was before Stage 73, and "
                "`test_body_measurement` compares it character for "
                "character."),
    Probe("X3-named", NAMED,
          "What is the volume of the cube?", ANSWERED,
          about=None, provenance=MEASURED, value=CUBE_VOLUME,
          notes="Naming the only body changes NOTHING, including the "
                "wording: `scope_for` returns before it ever consults the "
                "resolver when there is one body. `about` is None here "
                "because the product says no id, not because none exists."),
    Probe("X3-overall-size", AGGREGATE_SIZE,
          "What is the overall size?", ANSWERED,
          about=None, provenance=MEASURED,
          notes="MEASURED, not ASSUMED: one body's bounding box IS "
                "measured, and it is the AGGREGATE envelope that nothing "
                "measured. A grader that downgraded every size question "
                "would pass the multi-body case for the wrong reason."),
)

_X4_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X4-named-plate", NAMED,
          "What is the volume of the plate?", ANSWERED,
          about="plate", provenance=MEASURED, value=PLATE_WITH_BOSS_VOLUME,
          notes="The union's target KEEPS its id, so `plate` still names a "
                "body -- and that body is now the plate AND the boss."),
    Probe("X4-named-pin", NAMED,
          "What is the volume of the pin?", ANSWERED,
          about="pin", provenance=MEASURED, value=PIN_VOLUME,
          notes="The body the fuse never touched. Its number must be "
                "unchanged, which is the cross-body leakage check."),
    Probe("X4-consumed", CONSUMED,
          "What is the volume of the boss?", REFUSED,
          must_name=("boss",),
          notes="The fifth resolver case, and the one that actually "
                "misleads: `boss` was a body, and is not one now. Saying so "
                "-- and saying what consumed it -- is the useful answer; "
                "'no such body' would send someone looking for a typo that "
                "is not there."),
    Probe("X4-unnamed", UNNAMED_SEVERAL,
          "What is the volume?", REFUSED,
          must_name=("plate", "pin"),
          notes="Two bodies live. The consumed one must NOT be offered as "
                "a choice."),
    Probe("X4-total", AGGREGATE_TOTAL,
          "What is the total volume?", ANSWERED,
          about=None, aggregate=True, provenance=CALCULATED,
          value=PLATE_WITH_BOSS_VOLUME + PIN_VOLUME,
          notes="The sum of the two LIVE bodies. A total that included the "
                "consumed boss would count it twice."),
)

_X5_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X5-unnamed", UNNAMED_SINGLE,
          "What is the volume?", ANSWERED,
          about=None, provenance=MEASURED, value=FUSE_VOLUME,
          notes="One body, because the plan asked for a fuse. The volume is "
                "strictly LESS than the sum of the two boxes apart, which "
                "is what makes this the control: a part that was never "
                "actually fused would measure the sum."),
    Probe("X5-consumed", CONSUMED,
          "What is the volume of the lug?", ANSWERED,
          about=None, provenance=MEASURED, value=FUSE_VOLUME,
          notes="DELIBERATELY not a refusal, and this is a limitation "
                "recorded rather than a behaviour endorsed. With ONE live "
                "body `scope_for` returns before it consults the resolver "
                "at all, so naming the consumed `lug` is answered about the "
                "only body there is. Pinned so that changing it is a "
                "decision someone takes on purpose, with this case's "
                "expectation rewritten as a NEW case rather than edited."),
)

_X6_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X6-named-left", NAMED,
          "What is the volume of the left?", ANSWERED,
          about="left", provenance=MEASURED, value=CUBE_VOLUME,
          notes="TWO BODIES OF IDENTICAL DIMENSION. Both measure exactly "
                "64000, so the NUMBER cannot say which body an answer is "
                "about and the label is the only evidence there is. An "
                "observer that paired answers to bodies by value passes "
                "every other case in this corpus and cannot tell these two "
                "apart even in principle."),
    Probe("X6-named-right", NAMED,
          "What is the volume of the right?", ANSWERED,
          about="right", provenance=MEASURED, value=CUBE_VOLUME),
    Probe("X6-unnamed", UNNAMED_SEVERAL,
          "What is the volume?", REFUSED,
          must_name=("left", "right"),
          notes="Two bodies with the same volume, and still no *the* "
                "volume: the right answer is not 64000, it is a question."),
    Probe("X6-total", AGGREGATE_TOTAL,
          "What is the total volume?", ANSWERED,
          about=None, aggregate=True, provenance=CALCULATED,
          value=2.0 * CUBE_VOLUME),
)

_X7_PROBES: Final[Tuple[Probe, ...]] = (
    Probe("X7-named-cylinder", NAMED,
          "What is the volume of the cylinder?", ANSWERED,
          about="cylinder", provenance=MEASURED, value=PIN_VOLUME,
          notes="The same question `e2e:surfaces` step 2 asks, so the two "
                "instruments agree about what the right answer is."),
    Probe("X7-named-cube", NAMED,
          "What is the volume of the cube?", ANSWERED,
          about="cube", provenance=MEASURED, value=CUBE_VOLUME),
    Probe("X7-unnamed", UNNAMED_SEVERAL,
          "What is the volume?", REFUSED,
          must_name=("cube", "cylinder"),
          notes="`e2e:surfaces` step 3, and the one the browser proves "
                "reaches a person AS AN ANSWER rather than as a guess."),
    Probe("X7-total", AGGREGATE_TOTAL,
          "What is the total volume?", ANSWERED,
          about=None, aggregate=True, provenance=CALCULATED,
          value=CUBE_VOLUME + PIN_VOLUME),
)

CASES: Final[Tuple[Case, ...]] = (
    Case(
        "X1", MULTI,
        body_ids=("cube", "pin"),
        volumes=(CUBE_VOLUME, PIN_VOLUME),
        probes=_X1_PROBES,
        notes="The base multi-body case: two disjoint bodies whose volumes "
              "differ by a factor of seven, so a per-body answer is "
              "distinguishable from a total and from the other body.",
    ),
    Case(
        "X2", MULTI,
        body_ids=("block", "rod", "shim"),
        volumes=(CUBE_VOLUME, PIN_VOLUME, SLAB_VOLUME),
        probes=_X2_PROBES,
        notes="THREE bodies. Every multi-body number this project has "
              "recorded is against a two-body shape, and code written for "
              "'the other body' passes a two-body test and fails here.",
    ),
    Case(
        "X3", SINGLE,
        body_ids=("cube",),
        volumes=(CUBE_VOLUME,),
        probes=_X3_PROBES,
        export_names=(), export_identity=IDENTITY_NOT_WRITTEN,
        single_body_writer=True,
        notes="The single-body regression, in the same instrument. Its "
              "answers must be byte-for-byte the pre-Stage-73 ones and its "
              "export must go through the unchanged single-body writer.",
    ),
    Case(
        "X4", MULTI,
        body_ids=("plate", "pin"),
        volumes=(PLATE_WITH_BOSS_VOLUME, PIN_VOLUME),
        consumed={"boss": "fuse"},
        probes=_X4_PROBES,
        notes="Two bodies live and one CONSUMED, which is the only shape "
              "in which the fifth resolver case can fire at all.",
    ),
    Case(
        "X5", SINGLE,
        body_ids=("pad",),
        volumes=(FUSE_VOLUME,),
        consumed={"lug": "fuse"},
        probes=_X5_PROBES,
        export_names=(), export_identity=IDENTITY_NOT_WRITTEN,
        single_body_writer=True,
        notes="The fusion control. One body out of two OVERLAPPING boxes, "
              "so its volume is neither box and is not the sum: a part that "
              "was quietly left unfused, or fused from bodies that do not "
              "touch, measures something else.",
    ),
    Case(
        "X6", MULTI,
        body_ids=("left", "right"),
        volumes=(CUBE_VOLUME, CUBE_VOLUME),
        probes=_X6_PROBES,
        notes="Two bodies of IDENTICAL dimension, which is the case every "
              "shortcut in this instrument would survive without. Their "
              "volumes are equal to the last digit, so nothing but the id "
              "distinguishes them -- not the value, not the bounding box "
              "size, not the face or edge count. The brief names it: an "
              "observer must never infer the body from the measurement.",
    ),
    Case(
        "X7", MULTI,
        body_ids=("cube", "cylinder"),
        volumes=(CUBE_VOLUME, PIN_VOLUME),
        probes=_X7_PROBES,
        notes="THE PRODUCT PATH. The same part as X1 and the same two "
              "closed forms, but its plan is not hand-written: it is what "
              "`normalize.read_separate_bodies` makes of the sentence the "
              "browser e2e sends, so this case travels the route a person "
              "does -- session, reader, plan, executor, questions, export. "
              "Its truth is pinned here all the same, BY NAME, so a change "
              "in the reader fails the case rather than redefining it.",
    ),
)

RETIRED: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if c.retired)
ACTIVE: Final[Tuple[str, ...]] = tuple(c.name for c in CASES if not c.retired)
CASES_BY_NAME: Final[Mapping[str, Case]] = {c.name: c for c in CASES}

MULTI_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == MULTI and not c.retired
)
SINGLE_CASES: Final[Tuple[str, ...]] = tuple(
    c.name for c in CASES if c.group == SINGLE and not c.retired
)


def probes_to_ask(case_name: str) -> Tuple[Mapping[str, str], ...]:
    """Only what to ASK, never what the answer must be.

    A NARROWED view of the same probes :func:`expected` returns: each entry
    carries `name`, `kind` and `text` and nothing else. The observer reads
    this one and never the full probe, so it is not merely disciplined about
    ignoring the expectation -- it cannot see it. An observer that could
    read the expected outcome could record it, and then the instrument would
    be agreeing with itself.

    `test_stage76_observation` asserts the key set, and asserts that
    `observe76` reaches the probes through here and through nothing else.
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
    return tuple(
        {"name": p.name, "kind": p.kind, "text": p.text} for p in case.probes
    )


def expected(case_name: str) -> dict:
    """The ground truth for one case, BY NAME.

    Takes a case name, and nothing else. Never a plan, never a shape, never
    a measurement, never an answer, never a file. **The signature is the
    guarantee**: there is no parameter by which anything a model or a kernel
    produced could reach this function, so the truth cannot drift toward the
    output. `test_stage76_observation` asserts that by inspecting the
    signature and by walking this module's AST, because a docstring saying
    so is not a guarantee.
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
        "body_ids": case.body_ids,
        "volumes": case.volumes,
        "consumed": dict(case.consumed),
        "probes": tuple(
            {
                "name": p.name,
                "kind": p.kind,
                "text": p.text,
                "outcome": p.outcome,
                "about": p.about,
                "aggregate": p.aggregate,
                "provenance": p.provenance,
                "value": p.value,
                "must_name": p.must_name,
            }
            for p in case.probes
        ),
        "export_level": case.export_level,
        "export_solids": case.export_solids,
        "export_names": case.export_names,
        "export_volumes": case.export_volumes,
        "export_identity": case.export_identity,
        "single_body_writer": case.single_body_writer,
        "volume_tolerance": VOLUME_TOLERANCE,
        "identity_binding_note": IDENTITY_BINDING_NOTE,
    }


__all__ = [
    "ACTIVE", "AGGREGATE_SIZE", "AGGREGATE_TOTAL", "ANSWERED", "ASSUMED",
    "BOSS_DIAMETER", "BOSS_HEIGHT", "BOSS_VOLUME", "CALCULATED",
    "CASES", "CASES_BY_NAME", "CONSUMED", "CUBE_EDGE", "CUBE_VOLUME",
    "Case", "DECLARED", "DECLINED", "ENGINES", "EXPORT_LEVELS",
    "EXPORT_LEVEL_MEANING", "FUSE_A_EDGE", "FUSE_B_EDGE", "FUSE_B_ORIGIN_X",
    "FUSE_OVERLAP", "FUSE_VOLUME", "IDENTITY_BINDING",
    "IDENTITY_BINDING_NOTE", "IDENTITY_NOT_WRITTEN",
    "IDENTITY_STATES", "IDENTITY_UNPROVEN", "LEVEL_A", "LEVEL_B", "LEVEL_C", "LEVEL_D",
    "LEVEL_E", "LEVEL_F", "MEASURED", "MULTI", "MULTI_CASES", "NAMED",
    "NOT_EVIDENCE_OF_IDENTITY", "PIN_DIAMETER", "PIN_LENGTH", "PIN_VOLUME",
    "PLATE_VOLUME", "PLATE_WITH_BOSS_VOLUME", "PLATE_X", "PLATE_Y",
    "PLATE_Z", "PROBE_KINDS", "PROBE_OUTCOMES", "PROVENANCE", "Probe",
    "REFUSED", "RESOLVER_KINDS", "RETIRED", "SINGLE", "SINGLE_CASES",
    "SLAB_VOLUME", "SLAB_X", "SLAB_Y", "SLAB_Z", "TWO_AT_ONCE",
    "SOURCES", "SOURCE_DETERMINISTIC", "SOURCE_FALLBACK",
    "SOURCE_MODEL_GENERATED", "SOURCE_PROVIDER_ERROR", "SOURCE_REFUSED",
    "COUNTS_AS_MODEL_EVIDENCE",
    "UNNAMED_SEVERAL", "UNNAMED_SINGLE", "VOLUME_TOLERANCE", "expected",
    "probes_to_ask",
]
