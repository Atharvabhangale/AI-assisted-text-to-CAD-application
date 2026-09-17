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
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from .adapter import AdapterError, ExecutionUnsupported, plan_to_document
from .cad_backend import CADQUERY, CadBackend, resolve_backend
from .executor import execute_plan, plan_needs_executor
from .plan import OperationPlan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cad_core.application_service import BuildOutcome, CadApplicationService

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
    outcome: Optional["BuildOutcome"] = None
    error: Optional[str] = None

    #: Which engine actually executed this build, and by which route. Always
    #: set, never inferred by a caller from which other field is populated:
    #: a build whose engine had to be guessed is a build nobody can attribute.
    backend: str = ""
    execution_path: str = ""

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

    #: The neutral :class:`cad_core.render_model.RenderModel` for a
    #: graph-executed build. The document path gets its render model from the
    #: service, inside :attr:`outcome`; this is the same contract, built by
    #: whichever backend ran, so a caller draws both the same way.
    render: Optional[Any] = None

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


#: The two routes a build can take, named so a caller never has to infer
#: which one ran from which field happens to be populated.
DOCUMENT_PATH = "v1_document"
GRAPH_PATH = "graph_executor"


def build_plan(
    service: "CadApplicationService",
    plan: OperationPlan,
    *,
    name: str = "experimental-part",
    outputs: Tuple[str, ...] = DEFAULT_OUTPUTS,
    backend: Optional[CadBackend] = None,
) -> PlanBuild:
    """Translate ``plan`` and build it on the selected backend.

    A plan that cannot be translated at all (a refusal, or no operations) is
    reported as an error and nothing is built. Everything else -- including a
    plan that translates into an *invalid* V1 document -- is handed on,
    because deciding that is the existing validator's job, not this
    module's.

    A plan whose operations this backend has no execution path for is
    reported as :attr:`PlanBuild.execution_unsupported`, with the offending
    types named, and **nothing is built or approximated**.

    Which route runs, and why
    ------------------------
    Two questions decide it, in this order, and both are asked before
    anything is built:

    1. **Can a V1 document carry this plan's selectors?** A `straight`, a
       `circular` or a rim's `position` cannot be written down in Section
       C.7, so such a plan goes to the graph executor. It always did.
    2. **Which engine did the caller ask for?** This is new, and it closes a
       real hole. The document path is `CadApplicationService`, which *is*
       the CadQuery engine -- it reaches `cad_core.local_cad` directly and
       has no :class:`CadBackend` in it anywhere. So while the document path
       served every V1-expressible plan, `CAD_BACKEND=freecad` governed only
       plans carrying a semantic selector, and a caller who asked for
       FreeCAD and built a plain box was handed CadQuery geometry with
       nothing in the result to say so. That is the silent backend switch
       this project forbids.

       Now a plan is routed to the graph executor whenever the requested
       engine is not the one the document path embodies. Nothing falls back
       in either direction: each route runs the engine that was asked for,
       and :attr:`PlanBuild.backend` and :attr:`PlanBuild.execution_path`
       record which, on every result.

    The document path is kept for CadQuery rather than retired because it
    carries genuinely different output: the build cache, the build key,
    process isolation, the artifact registry and the STEP/STL exports. The
    executor has none of those and inventing them here would be the second
    execution implementation this module exists to avoid.
    """
    engine = backend if backend is not None else resolve_backend()

    if plan_needs_executor(plan) or engine.name != CADQUERY:
        return _executed(plan, name=name, backend=engine)

    try:
        document = plan_to_document(plan, name=name)
    except ExecutionUnsupported as exc:
        return PlanBuild(
            document=None,
            error=str(exc),
            unsupported_types=exc.operation_types,
            unsupported_ids=exc.operation_ids,
            backend=engine.name,
            execution_path=DOCUMENT_PATH,
        )
    except AdapterError as exc:
        return PlanBuild(document=None, error=str(exc),
                         backend=engine.name, execution_path=DOCUMENT_PATH)

    if service is None:
        # The document path needs the service and there is none. Reported as
        # itself rather than by quietly executing on some other engine: a
        # caller who asked for CadQuery must not be handed FreeCAD geometry
        # any more than the reverse.
        return PlanBuild(
            document=document,
            error=(
                "no build service is configured, and this plan takes the V1 "
                "document path, which needs one"
            ),
            backend=engine.name,
            execution_path=DOCUMENT_PATH,
        )

    # Imported here, not at module scope: the service reaches CadQuery
    # through `cad_core`, and hoisting this made the whole module -- routing
    # included -- unimportable on a machine that has only the other engine.
    from cad_core.application_service import BuildDocumentRequest

    outcome = service.build_document(
        BuildDocumentRequest.for_outputs(document, *outputs)
    )
    return PlanBuild(document=document, outcome=outcome,
                     backend=engine.name, execution_path=DOCUMENT_PATH)


def _executed(
    plan: OperationPlan, *, name: str, backend: CadBackend
) -> PlanBuild:
    """Build through the graph-driven executor, and report it as one.

    No V1 document exists for such a plan, so :attr:`PlanBuild.document` is
    ``None`` and :attr:`PlanBuild.execution` carries the result. A caller
    that only asks :attr:`PlanBuild.built` gets the right answer either way.
    """
    result = execute_plan(plan, part_name=name, backend=backend)
    if not result.succeeded:
        return PlanBuild(
            document=None,
            error=f"[{result.failure.code}] {result.failure.message}",
            execution=result,
            backend=backend.name,
            execution_path=GRAPH_PATH,
        )

    # The same neutral RenderModel the document path produces, built by
    # whichever engine ran. Without it a graph-executed build measured
    # correctly and drew nothing, so the page reported a successful build it
    # could not show. A backend that cannot render says so and the build
    # still stands: the geometry is real either way.
    render = None
    try:
        shape = result.shapes.get(result.bodies[0].id) if result.bodies else None
        if shape is not None:
            render = backend.render_model(
                shape, part_name=name, feature_id=result.bodies[0].id)
    except Exception:  # noqa: BLE001 - a missing mesh is not a failed build
        render = None

    return PlanBuild(document=None, execution=result, render=render,
                     backend=backend.name, execution_path=GRAPH_PATH)


__all__ = [
    "DEFAULT_OUTPUTS",
    "DOCUMENT_PATH",
    "GRAPH_PATH",
    "PlanBuild",
    "build_plan",
]
