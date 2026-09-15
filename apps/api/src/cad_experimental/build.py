"""Build an operation plan, by handing it to the machinery that already works.

There is almost nothing here, and that is the point. The plan becomes a
canonical V1 document (``adapter``) and then goes straight into the existing
:class:`~cad_core.application_service.CadApplicationService`, which already
owns validation, the build cache, process isolation, the CAD engine, the
exporters, the artifact registry and the RenderModel.

Nothing in this module duplicates any of that. No cache, no exporter, no
tessellation, no build key, no artifact id: every one of those is the
existing implementation's, reached through its existing public API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from cad_core.application_service import (
    BuildDocumentRequest,
    BuildOutcome,
    CadApplicationService,
)

from .adapter import AdapterError, ExecutionUnsupported, plan_to_document
from .executor import execute_plan, plan_needs_executor
from .plan import OperationPlan

#: What an experimental build asks for. The render model is the point (the
#: page draws it); the mesh and the solid come with it from one build.
DEFAULT_OUTPUTS: Tuple[str, ...] = ("geometry", "render", "stl", "step")


@dataclass(frozen=True)
class PlanBuild:
    """The outcome of building one plan.

    ``outcome`` is the existing service's own :class:`BuildOutcome`, passed
    through unchanged -- this layer adds no CAD judgement of its own and
    reformats nothing that the stable path would report differently.
    """

    document: Optional[Dict[str, Any]]
    outcome: Optional[BuildOutcome] = None
    error: Optional[str] = None

    #: Set when the plan is sound but this backend has no execution path for
    #: some of its operations -- a sketch, today. It is reported separately
    #: from :attr:`error` because it is a different fact about a different
    #: thing: ``error`` says the plan is wrong, this says the engine is
    #: behind. A caller that showed them identically would be telling the
    #: user their plan was bad when it was not.
    unsupported_types: Tuple[str, ...] = ()
    unsupported_ids: Tuple[str, ...] = ()

    #: Set when the plan was built by the graph-driven executor rather than
    #: by way of a V1 document -- which happens exactly when a selector is
    #: richer than Section C.7 can express. Never set at the same time as
    #: :attr:`document`, so a caller can always tell which path ran.
    execution: Optional[Any] = None

    @property
    def executed(self) -> bool:
        """Whether the graph-driven executor produced this result."""
        return self.execution is not None

    @property
    def built(self) -> bool:
        """Whether the existing service reports a successful build.

        Delegates to :attr:`BuildOutcome.succeeded` rather than deciding
        anything: success is the build layer's word, not this layer's.
        """
        if self.execution is not None:
            return bool(self.execution.succeeded)
        return self.outcome is not None and self.outcome.succeeded

    @property
    def execution_unsupported(self) -> bool:
        """Whether the plan was refused for want of an execution path.

        Never true at the same time as :attr:`built`: an unsupported plan is
        not built at all, not built approximately.
        """
        return bool(self.unsupported_types)


def build_plan(
    service: CadApplicationService,
    plan: OperationPlan,
    *,
    name: str = "experimental-part",
    outputs: Tuple[str, ...] = DEFAULT_OUTPUTS,
) -> PlanBuild:
    """Translate ``plan`` and build it with the existing service.

    A plan that cannot be translated at all (a refusal, or no operations) is
    reported as an error and nothing is built. Everything else -- including a
    plan that translates into an *invalid* V1 document -- is handed to the
    service, because deciding that is the existing validator's job, not this
    module's.

    A plan whose operations this backend has no execution path for is
    reported as :attr:`PlanBuild.execution_unsupported`, with the offending
    types named, and **nothing is built or approximated**. That case is
    caught before the generic one because it is the more specific fact.
    """
    # One explicit question, asked before anything is built: can a V1
    # document carry this plan's selectors? A plan that needs a semantic one
    # executes directly on the backend. Not because the document path
    # failed -- it is never tried -- but because the two paths answer
    # different questions. See `cad_experimental.executor`.
    if plan_needs_executor(plan):
        return _executed(plan, name=name)

    try:
        document = plan_to_document(plan, name=name)
    except ExecutionUnsupported as exc:
        return PlanBuild(
            document=None,
            error=str(exc),
            unsupported_types=exc.operation_types,
            unsupported_ids=exc.operation_ids,
        )
    except AdapterError as exc:
        return PlanBuild(document=None, error=str(exc))

    outcome = service.build_document(
        BuildDocumentRequest.for_outputs(document, *outputs)
    )
    return PlanBuild(document=document, outcome=outcome)


def _executed(plan: OperationPlan, *, name: str) -> PlanBuild:
    """Build through the graph-driven executor, and report it as one.

    No V1 document exists for such a plan, so :attr:`PlanBuild.document` is
    ``None`` and :attr:`PlanBuild.execution` carries the result. A caller
    that only asks :attr:`PlanBuild.built` gets the right answer either way.
    """
    result = execute_plan(plan, part_name=name)
    if not result.succeeded:
        return PlanBuild(
            document=None,
            error=f"[{result.failure.code}] {result.failure.message}",
            execution=result,
        )
    return PlanBuild(document=None, execution=result)


__all__ = ["DEFAULT_OUTPUTS", "PlanBuild", "build_plan"]
