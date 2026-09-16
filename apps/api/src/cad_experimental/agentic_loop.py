"""Stage 59: the agentic control loop around the existing architecture.

What this adds, and what it deliberately does not
-------------------------------------------------

This module adds **orchestration** and nothing else. It introduces no CAD
concept, no geometry, no selector semantics and no second representation:
the canonical operation plan stays the single source of truth, the parser
and validator stay authoritative, and the backends stay behind their
existing abstraction. Every piece of CAD judgement here is delegated to code
that already existed and is imported rather than reimplemented.

The flow it wires together::

    task
      -> Planner.generate            (a model, or a deterministic stub)
      -> canonical operation plan
      -> validate_plan               (authoritative, unchanged)
      -> build_plan                  (graph / adapter / backend, unchanged)
      -> inspect                     (backend-neutral view of the result)
      -> diagnose                    (deterministic, no model)
      -> Reviser.revise              (a model, or a deterministic stub)
      -> a NEW canonical plan
      -> validate / build / inspect  (again, bounded)
      -> VerifiedResult + Trace

Seven rules this module is built to keep
----------------------------------------

1. **The plan is canonical.** A revision is a *new* :class:`PlanCandidate`
   with a parent link. Nothing is ever mutated in place, so the history of
   what was tried survives the run.
2. **Nothing is silently repaired.** This module never edits a model's
   output. A revision is produced by a :class:`Reviser`, is a whole new
   plan, and is validated from scratch like any other.
3. **Nothing falls back.** Not between backends, not between planners. A
   backend that cannot execute an operation produces
   :data:`FailureClass.BACKEND_UNSUPPORTED` and the loop says so.
4. **The taxonomy does not collapse.** "The build failed" is not an outcome
   here; see :class:`FailureClass`. In particular the selector distinction
   the whole Stage 40-55 line of work established is preserved: *matched
   nothing* (E4) and *matched something the kernel rejects* (E5) are
   different failures with different remedies.
5. **Diagnosis is deterministic.** No model is consulted to decide what went
   wrong. A diagnosis is structured data with facts attached, not log prose.
6. **The loop is bounded.** One initial attempt and at most two revisions,
   by default. There is no unbounded retry and no way to ask for one.
7. **The planner is an interface.** No vendor is named in the engine. The
   live Anthropic path is *an* implementation, supplied by the caller.

Where it attaches
-----------------

The smallest possible insertion point: :func:`run_agentic_cad_task` calls
``validate_plan`` and ``build_plan`` exactly as the existing direct path
does. That direct path is untouched and keeps working; this is a layer
above it, not a replacement for it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .adapter import AdapterError, ExecutionUnsupported
from .build import PlanBuild, build_plan
from .parser import PlanParseError, parse_plan
from .plan import OperationPlan, PlanStatus
from .validation import PlanValidation, validate_plan

# --- the failure taxonomy ---------------------------------------------------


class FailureClass(Enum):
    """Why one attempt ended. Never collapsed into "the build failed".

    Each member names a **different remedy**, which is the test for whether
    it deserves to exist. A plan the validator rejects needs a different
    plan; a plan this backend cannot run needs a different backend or a
    different capability; a selector that matched nothing needs a different
    selector; a selector that matched an edge the kernel refused needs a
    different *parameter*. Reporting those four as one outcome would throw
    away the only information a reviser could act on.
    """

    #: The model did not produce a plan at all -- it asked a question, or
    #: refused, or returned something unparseable. Not a CAD failure.
    NO_PLAN_PRODUCED = "no_plan_produced"

    #: The plan parses but the validator rejects it (P-rules).
    PLAN_INVALID = "plan_invalid"

    #: A valid plan naming an operation the language has but this stage
    #: cannot execute -- a sketch chain, today.
    PLAN_UNSUPPORTED = "plan_unsupported"

    #: The selected backend has no implementation for a required operation.
    #: Distinct from PLAN_UNSUPPORTED: the plan is executable in principle,
    #: this engine simply lacks the method. Stage 57 measured exactly this
    #: for FreeCAD's missing ``fillet_edges``/``chamfer_edges``.
    BACKEND_UNSUPPORTED = "backend_unsupported"

    #: **E4.** An edge selector matched no edge of its target. The request
    #: was misread; a fillet that affects nothing is an error.
    SELECTOR_MATCHED_NOTHING = "selector_matched_nothing"

    #: **E5.** A selector matched edges the kernel then refused -- a radius
    #: too large for the adjoining faces, or a seam no blend can take.
    #: Deliberately NOT the same as E4: the selector was right and the
    #: parameter was not.
    SELECTOR_GEOMETRY_REJECTED = "selector_geometry_rejected"

    #: The kernel failed for some other reason.
    EXECUTION_ERROR = "execution_error"

    #: A solid came back, but it is not a well-formed single solid.
    GEOMETRY_INVALID = "geometry_invalid"

    #: The geometry is valid and does not match what the task required.
    MEASUREMENT_MISMATCH = "measurement_mismatch"

    #: The numbers agree and the *meaning* does not -- the classic case
    #: being a top rim chamfered where the bottom was asked for, which is
    #: volumetrically identical. Geometry cannot catch this; the selector
    #: evidence can.
    SEMANTIC_MISMATCH = "semantic_mismatch"

    #: Nothing is wrong.
    SUCCESS = "success"


#: Failures a revision could plausibly address. A backend that lacks a
#: method will not grow one because a plan changed, so asking a reviser to
#: try is a waste of a call and an invitation to pretend.
REVISABLE: Tuple[FailureClass, ...] = (
    FailureClass.PLAN_INVALID,
    FailureClass.SELECTOR_MATCHED_NOTHING,
    FailureClass.SELECTOR_GEOMETRY_REJECTED,
    FailureClass.GEOMETRY_INVALID,
    FailureClass.MEASUREMENT_MISMATCH,
    FailureClass.SEMANTIC_MISMATCH,
)


# --- the contract -----------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """What was asked for, and what would make an answer correct.

    ``expectations`` is deliberately loose data rather than a schema: this
    module compares what it is given and reports what it cannot check. A
    task with no expectations still runs; it simply cannot fail on
    measurement.
    """

    description: str
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    expected_volume_mm3: Optional[float] = None
    volume_rtol: float = 1e-6
    expected_solid_count: Optional[int] = 1
    expected_selector: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": self.description,
            "expected_volume_mm3": self.expected_volume_mm3,
            "volume_rtol": self.volume_rtol,
            "expected_solid_count": self.expected_solid_count,
            "expected_selector": dict(self.expected_selector or {}),
        }


@dataclass(frozen=True)
class PlanCandidate:
    """One canonical plan offered for execution, and where it came from.

    ``payload`` is the canonical wire form. ``plan`` is the parsed object
    when parsing succeeded. Both are kept because a candidate that failed to
    parse still belongs in the trace -- the thing the model actually said is
    evidence, and discarding it would make a failure unexplainable.
    """

    candidate_id: str
    source: str                      # "planner" | "reviser" | "given"
    payload: Optional[Mapping[str, Any]]
    plan: Optional[OperationPlan] = None
    parse_error: Optional[str] = None
    parent_candidate_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source": self.source,
            "parent_candidate_id": self.parent_candidate_id,
            "payload": dict(self.payload) if self.payload else None,
            "parsed": self.plan is not None,
            "parse_error": self.parse_error,
        }


@dataclass(frozen=True)
class InspectionResult:
    """A backend-neutral view of what was built.

    Every field is something a backend already reports. Nothing here is
    computed by this module, and nothing is backend-specific: that is what
    lets one diagnosis work across engines.
    """

    built: bool
    solid_count: Optional[int] = None
    volume_mm3: Optional[float] = None
    bounding_box: Optional[Mapping[str, float]] = None
    face_count: Optional[int] = None
    edge_count: Optional[int] = None
    triangle_count: Optional[int] = None
    render_available: bool = False
    executed_by_graph: bool = False
    selectors: Tuple[Mapping[str, Any], ...] = ()
    selector_evidence: Tuple[SelectorEvidence, ...] = ()
    unsupported_types: Tuple[str, ...] = ()
    backend: Optional[str] = None
    #: The model's own status when it declined to offer a plan. Set only
    #: for a non-GENERATED answer, and the reason such an answer is never
    #: reported as a build failure.
    plan_status: Optional[str] = None
    error_text: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "built": self.built,
            "solid_count": self.solid_count,
            "volume_mm3": self.volume_mm3,
            "bounding_box": dict(self.bounding_box or {}),
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "triangle_count": self.triangle_count,
            "render_available": self.render_available,
            "executed_by_graph": self.executed_by_graph,
            "selectors": [dict(s) for s in self.selectors],
            "selector_evidence": [s.to_dict() for s in self.selector_evidence],
            "unsupported_types": list(self.unsupported_types),
            "backend": self.backend,
            "plan_status": self.plan_status,
            "error_text": self.error_text,
        }


@dataclass(frozen=True)
class SelectorEvidence:
    """What a selector actually named, measured rather than inferred.

    Stage 60's central correction. Stage 59 decided E4 and E5 by matching
    words in a backend's error string, which is brittle in the obvious way
    and silently wrong in a worse one: a backend that phrases a failure
    differently gets a confident misdiagnosis.

    None of that guesswork is necessary, because
    :func:`cad_experimental.edge_semantics.resolve` is already a total,
    typed resolver over :class:`EdgeFacts`. It never raises, it returns the
    indices it chose, and it says *why* it chose no more with its own codes:

    ===== ==================================================================
    ``R1`` the selector matched no edge -- **rule E4**, exactly
    ``R2`` the selection contains a parameterisation seam, which no blend
           can take -- the seam half of **rule E5**
    ``R3`` a position filter could not separate top from bottom
    ===== ==================================================================

    So a count settles what prose was being asked to imply. On a 100x60x10
    plate with a 20 mm bore, measured: ``circular/Z`` names 2 edges, ``top``
    names 1, ``bottom`` names 1, ``circular/X`` returns ``R1`` with 0, and
    ``axis_parallel/Z`` returns ``R2`` with the one seam that mode is
    documented to include.
    """

    selector: Mapping[str, Any]
    operation_id: Optional[str] = None
    selected_edge_count: Optional[int] = None
    candidate_edge_count: Optional[int] = None
    seam_count: Optional[int] = None
    resolution_code: Optional[str] = None
    resolution_message: Optional[str] = None

    @property
    def matched_nothing(self) -> bool:
        """Rule E4, from a count rather than from a sentence."""
        return self.selected_edge_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selector": dict(self.selector),
            "operation_id": self.operation_id,
            "selected_edge_count": self.selected_edge_count,
            "candidate_edge_count": self.candidate_edge_count,
            "seam_count": self.seam_count,
            "resolution_code": self.resolution_code,
            "resolution_message": self.resolution_message,
        }


@dataclass(frozen=True)
class MeasurementEvidence:
    """What was measured against what was expected, field by field.

    Separate from :class:`SelectorEvidence` because the two answer different
    questions and a reviser acts on them differently: a wrong number is a
    parameter problem, a wrong selector is a naming problem.
    """

    expected_volume_mm3: Optional[float] = None
    volume_mm3: Optional[float] = None
    volume_within_tolerance: Optional[bool] = None
    expected_solid_count: Optional[int] = None
    solid_count: Optional[int] = None
    face_count: Optional[int] = None
    edge_count: Optional[int] = None
    comparison_level: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_volume_mm3": self.expected_volume_mm3,
            "volume_mm3": self.volume_mm3,
            "volume_within_tolerance": self.volume_within_tolerance,
            "expected_solid_count": self.expected_solid_count,
            "solid_count": self.solid_count,
            "face_count": self.face_count,
            "edge_count": self.edge_count,
            "comparison_level": self.comparison_level,
        }


@dataclass(frozen=True)
class FailureDiagnosis:
    """What went wrong, as data a reviser can act on.

    ``evidence`` carries the measured particulars as *structured* values --
    the count of edges a selector named, the volume that disagreed and by how
    much. It exists so that a revision can be **targeted**: a reviser given
    "the build failed" can only guess; one given "the circular selector on
    ``break_edge`` named 2 edges where the request describes 1" can change
    one field.

    ``retryable`` and ``revision_allowed`` are deliberately separate. A
    backend that lacks a method is neither: no amount of re-running or
    re-planning grows it one.
    """

    failure_class: FailureClass
    summary: str
    affected_operation_ids: Tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    selector_evidence: Tuple[SelectorEvidence, ...] = ()
    measurement_evidence: Optional[MeasurementEvidence] = None
    backend: Optional[str] = None
    retryable: bool = False
    revision_allowed: bool = False

    @property
    def facts(self) -> Mapping[str, Any]:
        """Backwards-compatible alias for :attr:`evidence`."""
        return self.evidence

    @property
    def revisable(self) -> bool:
        """Backwards-compatible alias for :attr:`revision_allowed`."""
        return self.revision_allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure_class": self.failure_class.value,
            "summary": self.summary,
            "affected_operation_ids": list(self.affected_operation_ids),
            "evidence": dict(self.evidence),
            "selector_evidence": [s.to_dict() for s in self.selector_evidence],
            "measurement_evidence": (
                self.measurement_evidence.to_dict()
                if self.measurement_evidence else None
            ),
            "backend": self.backend,
            "retryable": self.retryable,
            "revision_allowed": self.revision_allowed,
            # kept so a Stage 59 trace reader still finds what it expects
            "facts": dict(self.evidence),
            "revisable": self.revision_allowed,
        }


@dataclass(frozen=True)
class RevisionRequest:
    """What a reviser is asked to change, and what it must leave alone.

    ``preserved_constraints`` is the half that is easy to forget and
    expensive to lose: a revision that fixes a selector and quietly resizes
    the plate has not fixed anything, and without stating what must not move
    there is no way to notice.
    """

    failed_candidate_id: str
    failure_class: FailureClass
    diagnostic_facts: Mapping[str, Any]
    affected_operation_ids: Tuple[str, ...]
    requested_change: str
    preserved_constraints: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failed_candidate_id": self.failed_candidate_id,
            "failure_class": self.failure_class.value,
            "diagnostic_facts": dict(self.diagnostic_facts),
            "affected_operation_ids": list(self.affected_operation_ids),
            "requested_change": self.requested_change,
            "preserved_constraints": list(self.preserved_constraints),
        }


@dataclass(frozen=True)
class ExecutionAttempt:
    """One full pass: a plan, what the system did with it, and the verdict."""

    attempt_id: str
    attempt_number: int
    candidate: PlanCandidate
    backend: str
    validation: Optional[PlanValidation]
    inspection: InspectionResult
    diagnosis: FailureDiagnosis
    parent_attempt_id: Optional[str] = None
    revision_request: Optional[RevisionRequest] = None
    elapsed_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.diagnosis.failure_class is FailureClass.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "attempt_number": self.attempt_number,
            "parent_attempt_id": self.parent_attempt_id,
            "backend": self.backend,
            "candidate": self.candidate.to_dict(),
            "validation": self.validation.to_dict() if self.validation else None,
            "inspection": self.inspection.to_dict(),
            "diagnosis": self.diagnosis.to_dict(),
            "revision_request": (
                self.revision_request.to_dict()
                if self.revision_request else None
            ),
            "succeeded": self.succeeded,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


@dataclass(frozen=True)
class VerifiedResult:
    """The end of a run: what was accepted, or why nothing was.

    ``accepted_because`` exists so a success is explainable rather than
    merely asserted -- the trace has to answer "why was this accepted", and
    a boolean cannot.
    """

    task: Task
    success: bool
    failure_class: FailureClass
    attempts: Tuple[ExecutionAttempt, ...]
    final_attempt: Optional[ExecutionAttempt]
    accepted_because: str
    stopped_because: str

    @property
    def final_plan(self) -> Optional[OperationPlan]:
        return self.final_attempt.candidate.plan if self.final_attempt else None

    @property
    def trace(self) -> Dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "success": self.success,
            "failure_class": self.failure_class.value,
            "attempt_count": len(self.attempts),
            "accepted_because": self.accepted_because,
            "stopped_because": self.stopped_because,
            "attempts": [a.to_dict() for a in self.attempts],
        }


# --- the planner / reviser interfaces ---------------------------------------


class Planner(Protocol):
    """Turns a task into a canonical plan payload. Any implementation."""

    def generate(self, task: Task) -> Optional[Mapping[str, Any]]:
        ...


class Reviser(Protocol):
    """Turns a failed attempt into a NEW canonical plan payload."""

    def revise(
        self, task: Task, previous: Mapping[str, Any],
        request: RevisionRequest,
    ) -> Optional[Mapping[str, Any]]:
        ...


# --- inspection -------------------------------------------------------------


class ComparisonLevel(Enum):
    """How strongly a built shape was shown to match what was asked for.

    Three levels, and only three, because those are what both backends can
    honestly supply. Each is strictly stronger than the one above it, and
    **none of them is geometric equality** -- two different shapes can share
    a volume, a solid count and a bounding box, and Stage 55's F2/F3 pair is
    the standing proof: a top-rim and a bottom-rim chamfer agree on every
    number in this list.

    That is why :data:`SEMANTIC` exists and why selector evidence is scored
    separately from measurement. Anything beyond this -- surface sampling, a
    BREP comparison -- would need capability neither backend exposes today,
    and claiming it without that capability would be the kind of rounding-up
    this project keeps refusing to do.
    """

    #: Volume and solid count agree within tolerance.
    MEASUREMENT = "measurement_match"

    #: The above, plus face and edge counts agree. A cheap topology check:
    #: it catches a shape that reached the right volume by the wrong route.
    TOPOLOGY = "topology_match"

    #: The above, plus the selector the plan used names what the request
    #: describes. The only level that can separate a top rim from a bottom.
    SEMANTIC = "semantic_match"


def selector_evidence_for(
    build: PlanBuild, payload: Mapping[str, Any],
) -> Tuple[SelectorEvidence, ...]:
    """Ask the resolver what each selector in the plan actually named.

    This is measurement, not inference. The resolver is total and typed --
    it never raises, it returns the indices it chose, and it reports ``R1``
    for "named nothing" and ``R2`` for "contains a seam" -- so the counts
    here are facts rather than readings of a kernel's prose.

    A selector that cannot be resolved (no shape to resolve against, a
    backend without ``describe_edges``) yields evidence with ``None`` counts
    rather than a guess. Absent evidence is reported as absent.
    """
    operations = [
        (operation.get("id"), edges)
        for operation in (payload.get("operations") or ())
        if isinstance(
            edges := (operation.get("parameters") or {}).get("edges"), dict)
    ]
    if not operations:
        return ()

    # The executor already did this work and kept it. `_blend` resolves every
    # selector it applies and records the Resolution under the operation's own
    # id, and ExecutionResult carries that mapping on the failure path as well
    # as the success path -- its own comment calls it "the diagnostic an agent
    # needs to see why an edge operation did what it did".
    #
    # Reading it rather than re-resolving matters for three reasons. It is the
    # resolution that was actually *used*, not one recomputed afterwards from
    # a possibly different shape. It survives a failed build, where there is
    # no final shape to describe edges from -- which is precisely when a
    # diagnosis is needed. And it does not require ``describe_edges``, which
    # FreeCadBackend does not implement, so the evidence is available wherever
    # the executor ran rather than on one engine.
    resolutions = dict(
        getattr(getattr(build, "execution", None), "selections", None) or {})

    evidence: List[SelectorEvidence] = []
    for operation_id, edges in operations:
        resolution = resolutions.get(operation_id)
        if resolution is None:
            # The executor never reached this operation -- an earlier one
            # failed, or this build did not go through the graph path.
            # Absent evidence is reported as absent rather than invented.
            evidence.append(SelectorEvidence(
                selector=dict(edges), operation_id=operation_id))
            continue
        evidence.append(SelectorEvidence(
            selector=dict(edges),
            operation_id=operation_id,
            selected_edge_count=len(resolution.indices),
            candidate_edge_count=len(resolution.candidates),
            seam_count=len(resolution.seams),
            resolution_code=resolution.code,
            resolution_message=resolution.message or None,
        ))
    return tuple(evidence)


def inspect_build(build: PlanBuild, payload: Mapping[str, Any]) -> InspectionResult:
    """Read a completed build into the backend-neutral view.

    Measurements come from the existing harness helper rather than a second
    implementation, so a number reported here is the same number every other
    stage reported.
    """
    selectors = tuple(
        dict(edges) for operation in (payload.get("operations") or ())
        if isinstance(
            edges := (operation.get("parameters") or {}).get("edges"), dict
        )
    )
    import os

    backend = os.environ.get("CAD_BACKEND") or None
    evidence = selector_evidence_for(build, payload)
    if not build.built:
        return InspectionResult(
            built=False,
            selectors=selectors,
            selector_evidence=evidence,
            unsupported_types=tuple(build.unsupported_types or ()),
            backend=backend,
            error_text=str(build.error or getattr(build.outcome, "error", "")
                           or "") or None,
        )
    from .stage48_capability_evaluation import _measure

    volume, box, solids, triangles = _measure(build)
    return InspectionResult(
        built=True,
        solid_count=solids,
        volume_mm3=volume,
        bounding_box=box,
        triangle_count=triangles,
        render_available=bool(getattr(build.outcome, "render_model", None)),
        executed_by_graph=bool(getattr(build, "executed", False)),
        selectors=selectors,
        selector_evidence=evidence,
        backend=backend,
    )


# --- diagnosis (deterministic; no model) ------------------------------------

#: The backends already name the rule they enforced -- a refused blend comes
#: back as ``"the kernel refused the fillet (rule E5): ..."``. Reading that
#: code is exact, so it is tried first and the prose below is only a
#: fallback for a backend that has not yet adopted the convention.
_E4_CODE = "rule e4"
_E5_CODE = "rule e5"

#: Phrases a kernel uses when a selector named nothing, for backends that do
#: not yet emit a rule code. A miss degrades to EXECUTION_ERROR rather than
#: to a confident wrong answer.
_E4_MARKERS = ("matched no", "matches no", "no edges", "selected no",
               "empty selection")

#: Phrases a kernel uses when the edges were found and then refused.
_E5_MARKERS = ("too large", "not admissible", "seam", "refused the fillet",
               "refused the chamfer", "command not done")


def diagnose(
    task: Task,
    candidate: PlanCandidate,
    validation: Optional[PlanValidation],
    inspection: InspectionResult,
) -> FailureDiagnosis:
    """Decide what went wrong, from evidence only.

    Deterministic by design. A model is not consulted: a diagnosis that
    varied run to run could not be used to explain a revision, and the whole
    point of the trace is that the reason is inspectable.
    """
    def out(cls: FailureClass, summary: str, **facts: Any) -> FailureDiagnosis:
        ids = tuple(facts.pop("affected", ()) or ())
        selector_evidence = tuple(facts.pop("selector_evidence", ()) or ())
        measurement = facts.pop("measurement_evidence", None)
        return FailureDiagnosis(
            failure_class=cls, summary=summary,
            affected_operation_ids=ids, evidence=facts,
            selector_evidence=selector_evidence,
            measurement_evidence=measurement,
            backend=inspection.backend,
            retryable=False,
            revision_allowed=cls in REVISABLE,
        )

    if candidate.payload is None:
        return out(FailureClass.NO_PLAN_PRODUCED,
                   "the planner produced no plan")
    if candidate.plan is None:
        return out(FailureClass.NO_PLAN_PRODUCED,
                   "the plan could not be parsed",
                   parse_error=candidate.parse_error)
    if validation is not None and not validation.valid:
        codes = [p.code for p in validation.problems]
        return out(FailureClass.PLAN_INVALID,
                   f"the validator rejected the plan: {', '.join(codes[:3])}",
                   affected=tuple(
                       p.where for p in validation.problems if p.where),
                   problem_codes=codes,
                   problems=[p.to_dict() for p in validation.problems])

    if not inspection.built:
        if inspection.plan_status:
            return out(FailureClass.NO_PLAN_PRODUCED,
                       f"the model answered {inspection.plan_status!r} "
                       "rather than offering a plan; nothing was built "
                       "and nothing failed",
                       plan_status=inspection.plan_status)
        if inspection.unsupported_types:
            return out(FailureClass.PLAN_UNSUPPORTED,
                       "the plan is valid but this stage cannot execute "
                       f"{', '.join(inspection.unsupported_types)}",
                       unsupported_types=list(inspection.unsupported_types))
        text = (inspection.error_text or "").lower()
        if "does not implement" in text or "notimplemented" in text:
            return out(FailureClass.BACKEND_UNSUPPORTED,
                       "the selected backend has no implementation for a "
                       "required operation",
                       error_text=inspection.error_text)

        # --- typed first. The resolver already counted the edges, so E4 is
        # a fact rather than a reading of the kernel's prose.
        empty = [s for s in inspection.selector_evidence if s.matched_nothing]
        if empty:
            return out(FailureClass.SELECTOR_MATCHED_NOTHING,
                       "an edge selector matched no edge of its target "
                       "(E4): "
                       + "; ".join(
                           f"{dict(s.selector)} named 0 of "
                           f"{s.candidate_edge_count} candidate(s)"
                           for s in empty),
                       affected=tuple(
                           s.operation_id for s in empty if s.operation_id),
                       selector_evidence=tuple(empty),
                       resolution_codes=[s.resolution_code for s in empty],
                       error_text=inspection.error_text)

        seamed = [s for s in inspection.selector_evidence
                  if (s.seam_count or 0) > 0]
        if seamed:
            return out(FailureClass.SELECTOR_GEOMETRY_REJECTED,
                       "the selection contains a parameterisation seam, "
                       "which no blend can take (E5): the mode names it, so "
                       "the mode is what must change",
                       affected=tuple(
                           s.operation_id for s in seamed if s.operation_id),
                       selector_evidence=tuple(seamed),
                       resolution_codes=[s.resolution_code for s in seamed],
                       error_text=inspection.error_text)

        # --- the backend's own rule code, which is exact where it is emitted
        if _E4_CODE in text:
            return out(FailureClass.SELECTOR_MATCHED_NOTHING,
                       "the backend reported rule E4: the selector matched "
                       "no edge of its target",
                       selector_evidence=inspection.selector_evidence,
                       error_text=inspection.error_text)
        if _E5_CODE in text:
            return out(FailureClass.SELECTOR_GEOMETRY_REJECTED,
                       "the backend reported rule E5: the selector matched "
                       "edges the kernel then refused -- the selection was "
                       "right, a parameter was not",
                       affected=tuple(
                           s.operation_id for s in inspection.selector_evidence
                           if s.operation_id),
                       selector_evidence=inspection.selector_evidence,
                       error_text=inspection.error_text)

        # --- prose, last and marked as such
        if any(m in text for m in _E4_MARKERS):
            return out(FailureClass.SELECTOR_MATCHED_NOTHING,
                       "an edge selector matched no edge of its target (E4)",
                       selector_evidence=inspection.selector_evidence,
                       classified_by="error text, not typed evidence",
                       error_text=inspection.error_text)
        if any(m in text for m in _E5_MARKERS):
            return out(FailureClass.SELECTOR_GEOMETRY_REJECTED,
                       "the selector matched edges the kernel then refused "
                       "(E5): the selection was right, a parameter was not",
                       selector_evidence=inspection.selector_evidence,
                       classified_by="error text, not typed evidence",
                       error_text=inspection.error_text)
        return out(FailureClass.EXECUTION_ERROR,
                   "the build failed for a reason this layer cannot classify",
                   selector_evidence=inspection.selector_evidence,
                   error_text=inspection.error_text)

    if (task.expected_solid_count is not None
            and inspection.solid_count is not None
            and inspection.solid_count != task.expected_solid_count):
        return out(FailureClass.GEOMETRY_INVALID,
                   f"expected {task.expected_solid_count} solid(s), built "
                   f"{inspection.solid_count}",
                   expected_solid_count=task.expected_solid_count,
                   solid_count=inspection.solid_count)

    within = None
    if task.expected_volume_mm3 is not None and inspection.volume_mm3 is not None:
        want, got = task.expected_volume_mm3, inspection.volume_mm3
        within = abs(want - got) <= task.volume_rtol * max(
            abs(want), abs(got), 1.0)
        if not within:
            return out(FailureClass.MEASUREMENT_MISMATCH,
                       f"volume {got} does not match the expected {want}",
                       measurement_evidence=MeasurementEvidence(
                           expected_volume_mm3=want, volume_mm3=got,
                           volume_within_tolerance=False,
                           expected_solid_count=task.expected_solid_count,
                           solid_count=inspection.solid_count,
                           face_count=inspection.face_count,
                           edge_count=inspection.edge_count,
                           comparison_level=None),
                       expected_volume_mm3=want, volume_mm3=got,
                       difference=got - want)

    if task.expected_selector:
        want = dict(task.expected_selector)
        got = [dict(s) for s in inspection.selectors]
        if not any(_selector_agrees(want, s) for s in got):
            missing = [k for k, v in want.items()
                       if not any(s.get(k) == v for s in got)]
            affected = tuple(
                s.operation_id for s in inspection.selector_evidence
                if s.operation_id and not _selector_agrees(want, s.selector))
            return out(FailureClass.SEMANTIC_MISMATCH,
                       "the geometry is valid but the selector is not the one "
                       "the task required -- volume cannot see this",
                       affected=affected,
                       selector_evidence=inspection.selector_evidence,
                       expected_selector=want, selectors=got,
                       disagreeing_fields=missing)

    level = _comparison_level(task, inspection, within)
    return FailureDiagnosis(
        failure_class=FailureClass.SUCCESS,
        summary=("validated, built, and every stated expectation met "
                 f"({level.value})"),
        measurement_evidence=MeasurementEvidence(
            expected_volume_mm3=task.expected_volume_mm3,
            volume_mm3=inspection.volume_mm3,
            volume_within_tolerance=within,
            expected_solid_count=task.expected_solid_count,
            solid_count=inspection.solid_count,
            face_count=inspection.face_count,
            edge_count=inspection.edge_count,
            comparison_level=level.value),
        selector_evidence=inspection.selector_evidence,
        backend=inspection.backend,
        revision_allowed=False,
    )


def _comparison_level(
    task: Task, inspection: InspectionResult, volume_ok: Optional[bool],
) -> ComparisonLevel:
    """How strongly this result was shown to match -- never overstated.

    Reports the **strongest level the available evidence supports**, and no
    higher. A task that stated no selector cannot reach ``SEMANTIC``, and a
    backend that reported no face or edge counts cannot reach ``TOPOLOGY``:
    in both cases the check was not performed, and a level claimed without
    its check would be a claim about nothing.
    """
    if task.expected_selector and inspection.selector_evidence:
        return ComparisonLevel.SEMANTIC
    if inspection.face_count is not None and inspection.edge_count is not None:
        return ComparisonLevel.TOPOLOGY
    return ComparisonLevel.MEASUREMENT


def _selector_agrees(want: Mapping[str, Any], got: Mapping[str, Any]) -> bool:
    """Whether one emitted selector satisfies every stated expectation."""
    return all(got.get(key) == value for key, value in want.items())


# --- the revision request ---------------------------------------------------

_CHANGE_BY_CLASS: Mapping[FailureClass, str] = {
    FailureClass.PLAN_INVALID:
        "fix the operations the validator named; change nothing else",
    FailureClass.SELECTOR_MATCHED_NOTHING:
        "choose an edge selector that names edges this solid actually has",
    FailureClass.SELECTOR_GEOMETRY_REJECTED:
        "keep the selector and change the parameter the kernel refused",
    FailureClass.GEOMETRY_INVALID:
        "produce a plan whose result is a single well-formed solid",
    FailureClass.MEASUREMENT_MISMATCH:
        "correct the dimensions so the measured volume matches the request",
    FailureClass.SEMANTIC_MISMATCH:
        "correct the selector so it names the edges the request describes",
}


def revision_request_for(
    attempt: ExecutionAttempt, task: Task,
) -> Optional[RevisionRequest]:
    """What to ask a reviser for, or ``None`` when asking is dishonest.

    A backend that lacks a method will not acquire one because a plan
    changed, so no request is produced for
    :data:`FailureClass.BACKEND_UNSUPPORTED`. Asking anyway would invite a
    reviser to return something that looks like a fix and is not.
    """
    diagnosis = attempt.diagnosis
    if not diagnosis.revisable:
        return None
    preserved = ["every operation the diagnosis does not name",
                 "the units, dimensions and ids already agreed"]
    if diagnosis.failure_class is FailureClass.SELECTOR_GEOMETRY_REJECTED:
        preserved.append("the selector mode, which was correct")
    return RevisionRequest(
        failed_candidate_id=attempt.candidate.candidate_id,
        failure_class=diagnosis.failure_class,
        diagnostic_facts=dict(diagnosis.facts),
        affected_operation_ids=diagnosis.affected_operation_ids,
        requested_change=_CHANGE_BY_CLASS.get(
            diagnosis.failure_class, "correct the fault the diagnosis names"),
        preserved_constraints=tuple(preserved),
    )


# --- the loop ---------------------------------------------------------------

#: One initial plan and at most two revisions. Three executions, never more.
DEFAULT_MAX_REVISIONS = 2


def run_agentic_cad_task(
    task: Task,
    planner: Planner,
    reviser: Optional[Reviser] = None,
    *,
    service: Any = None,
    backend: str = "cadquery",
    max_revisions: int = DEFAULT_MAX_REVISIONS,
) -> VerifiedResult:
    """Plan, execute, inspect, diagnose, revise -- bounded and observable.

    The entry point, and deliberately the only one. The existing direct
    build path is untouched and still works; this is a layer above it.

    ``backend`` is recorded on every attempt and never changed by this
    function. There is no automatic switching: a run that could not execute
    on the engine it was asked for says so.
    """
    import os
    import tempfile

    from cad_core.application_service import CadApplicationService

    os.environ["CAD_BACKEND"] = backend
    if service is None:
        service = CadApplicationService.local(tempfile.mkdtemp())

    attempts: List[ExecutionAttempt] = []
    payload = planner.generate(task)
    candidate = _candidate_from(payload, source="planner")
    parent_attempt: Optional[str] = None
    stopped = "the first attempt succeeded"

    for number in range(1, max_revisions + 2):
        started = time.monotonic()
        validation, build, inspection = _execute(candidate, service)
        diagnosis = diagnose(task, candidate, validation, inspection)
        attempt = ExecutionAttempt(
            attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
            attempt_number=number,
            candidate=candidate,
            backend=backend,
            validation=validation,
            inspection=inspection,
            diagnosis=diagnosis,
            parent_attempt_id=parent_attempt,
            elapsed_seconds=time.monotonic() - started,
        )
        if attempt.succeeded:
            attempts.append(attempt)
            return VerifiedResult(
                task=task, success=True,
                failure_class=FailureClass.SUCCESS,
                attempts=tuple(attempts), final_attempt=attempt,
                accepted_because=diagnosis.summary,
                stopped_because=(
                    "accepted on attempt " f"{number}"),
            )

        request = revision_request_for(attempt, task)
        attempt = ExecutionAttempt(**{
            **attempt.__dict__, "revision_request": request,
        })
        attempts.append(attempt)

        if request is None:
            stopped = (
                f"{diagnosis.failure_class.value} is not something a revision "
                "can address; no reviser was asked")
            break
        if reviser is None:
            stopped = "no reviser was supplied"
            break
        if number > max_revisions:
            stopped = f"the revision limit of {max_revisions} was reached"
            break

        revised = reviser.revise(task, candidate.payload or {}, request)
        if revised is None:
            stopped = "the reviser produced no new plan"
            break
        if revised == candidate.payload:
            stopped = (
                "the reviser returned the same plan; a revision that changes "
                "nothing is not a revision")
            break
        parent_attempt = attempt.attempt_id
        candidate = _candidate_from(
            revised, source="reviser",
            parent_candidate_id=candidate.candidate_id)
    else:
        stopped = f"the revision limit of {max_revisions} was reached"

    last = attempts[-1] if attempts else None
    return VerifiedResult(
        task=task, success=False,
        failure_class=(last.diagnosis.failure_class if last
                       else FailureClass.NO_PLAN_PRODUCED),
        attempts=tuple(attempts), final_attempt=last,
        accepted_because="",
        stopped_because=stopped,
    )


def _candidate_from(
    payload: Optional[Mapping[str, Any]], *, source: str,
    parent_candidate_id: Optional[str] = None,
) -> PlanCandidate:
    """Parse a payload into a candidate, keeping a failure as evidence."""
    candidate_id = f"plan-{uuid.uuid4().hex[:8]}"
    if payload is None:
        return PlanCandidate(candidate_id, source, None,
                             parent_candidate_id=parent_candidate_id)
    try:
        import json
        plan = parse_plan(json.loads(json.dumps(dict(payload))))
    except (PlanParseError, ValueError, TypeError) as error:
        return PlanCandidate(
            candidate_id, source, payload, None,
            f"{type(error).__name__}: {error}", parent_candidate_id)
    return PlanCandidate(candidate_id, source, payload, plan,
                         parent_candidate_id=parent_candidate_id)


def _execute(
    candidate: PlanCandidate, service: Any,
) -> Tuple[Optional[PlanValidation], Optional[PlanBuild], InspectionResult]:
    """Validate then build, mapping every failure into the taxonomy's inputs."""
    if candidate.plan is None:
        return None, None, InspectionResult(built=False)
    validation = validate_plan(candidate.plan)
    if not validation.valid:
        return validation, None, InspectionResult(built=False)
    if candidate.plan.status is not PlanStatus.GENERATED:
        # The model answered with a question or a refusal rather than a
        # plan. That is NOT a build failure and must not be reported as
        # one: nothing was executed, no geometry was attempted, and a
        # reviser asked to "fix the build" would be answering a question
        # that was never asked. Stage 60 found this misclassified as
        # EXECUTION_ERROR when a live model quite reasonably asked for
        # clarification instead of inventing a selector.
        return validation, None, InspectionResult(
            built=False,
            plan_status=candidate.plan.status.value,
            error_text=(
                f"the model did not offer a plan: its status is "
                f"{candidate.plan.status.value}"))
    try:
        build = build_plan(service, candidate.plan)
    except ExecutionUnsupported as error:
        return validation, None, InspectionResult(
            built=False, unsupported_types=tuple(
                getattr(error, "types", ()) or ()),
            error_text=str(error))
    except NotImplementedError as error:
        return validation, None, InspectionResult(
            built=False,
            error_text=f"the backend does not implement it: {error}")
    except AdapterError as error:
        return validation, None, InspectionResult(
            built=False, error_text=f"{type(error).__name__}: {error}")
    return validation, build, inspect_build(build, candidate.payload or {})


__all__ = [
    "DEFAULT_MAX_REVISIONS",
    "ComparisonLevel",
    "MeasurementEvidence",
    "SelectorEvidence",
    "selector_evidence_for",
    "REVISABLE",
    "ExecutionAttempt",
    "FailureClass",
    "FailureDiagnosis",
    "InspectionResult",
    "PlanCandidate",
    "Planner",
    "RevisionRequest",
    "Reviser",
    "Task",
    "VerifiedResult",
    "diagnose",
    "inspect_build",
    "revision_request_for",
    "run_agentic_cad_task",
]
