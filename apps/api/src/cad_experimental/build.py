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

    @property
    def built(self) -> bool:
        """Whether the existing service reports a successful build.

        Delegates to :attr:`BuildOutcome.succeeded` rather than deciding
        anything: success is the build layer's word, not this layer's.
        """
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


__all__ = ["DEFAULT_OUTPUTS", "PlanBuild", "build_plan"]
