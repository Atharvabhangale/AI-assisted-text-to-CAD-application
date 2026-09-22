"""The experimental ASGI application. Its own app, its own port, its own routes.

This is **not** the production application with routes added. ``cad_api`` is
untouched and still serves the stable six routes on port 8000; this is a
separate FastAPI app on 8001 that a caller must start deliberately.

Four routes:

  ``GET  /experimental/health``         is it up, and is a model configured
  ``POST /experimental/generate-plan``  description -> operation plan
  ``POST /experimental/validate-plan``  a plan (from anywhere) -> verdict
  ``POST /experimental/build-plan``     a plan -> the existing build result

``/generate`` is not touched, not proxied and not re-implemented here.

What never crosses into a response body: the model's raw text, a provider's
own error string, and anything derived from a credential. A caller gets the
structured plan and a neutral message; the detail goes to the measurement
record and the server log.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import EXPERIMENT_NAME, PLAN_SCHEMA_VERSION
from .build import build_plan
from .config import (
    ExperimentalConfig,
    config_from_environment,
    credential_available,
)
from .generation import OperationPlanService, PlanGenerationResult, PlanOutcome
from .local_plan_provider import (
    SOURCE_LABEL,
    describe_fixtures,
    fixture_plan,
    run_fixture,
    stamp,
)
from .interpretation import (
    DETERMINISTIC_NOTE,
    NO_MODEL_NOTE,
    SOURCE_DETERMINISTIC,
    deterministic_interpretation,
    interpret,
)
from .parser import PlanParseError, parse_plan
from .questions import answer as answer_from_evidence
from . import catalog as catalogue
from . import engineering as eng
from .drawing import build_drawing
from .macros import ACTIONS, MacroError, MacroStore, steps_from_language
from .session import (
    ASSISTANT,
    USER,
    Revision,
    SessionStore,
    describe_model,
    measurement_answer,
    revision_context,
)
from .plan import PlanStatus, plan_schema
from .graph import feature_graph
from .history import plan_history
from .prompt import PROMPT_VERSION, prompt_fingerprint
from .validation import validate_plan

logger = logging.getLogger("cad_experimental")

HEALTH_PATH = "/experimental/health"
GENERATE_PATH = "/experimental/generate-plan"
VALIDATE_PATH = "/experimental/validate-plan"
BUILD_PATH = "/experimental/build-plan"
#: Development only. Runs a developer-supplied plan through the real path.
#: Never a model result -- every response is stamped LOCAL_DEVELOPMENT_PLAN.
LOCAL_PLAN_PATH = "/experimental/local-plan"
LOCAL_FIXTURES_PATH = "/experimental/local-plan/fixtures"

#: The multi-turn copilot. One call per turn: the server holds the current
#: plan, so the client does not have to send the part back to modify it.
SESSION_MESSAGE_PATH = "/experimental/session/message"
SESSION_UNDO_PATH = "/experimental/session/undo"
SESSION_RESET_PATH = "/experimental/session/reset"
SESSION_STATE_PATH = "/experimental/session/state"
SESSION_EXPORT_PATH = "/experimental/session/export"
DRAWING_PATH = "/experimental/session/drawing"
ENGINEERING_PATH = "/experimental/session/engineering"
CATALOG_PATH = "/experimental/catalog/search"
MACRO_PATH = "/experimental/session/macros"
MACRO_RUN_PATH = "/experimental/session/macros/run"

#: A revision returns a COMPLETE plan for the whole part, so its reply can be
#: legitimately longer than a first answer. Raised only on that path.
REVISION_OUTPUT_TOKENS = 3072

OK_STATUS = 200
BAD_REQUEST_STATUS = 400
UNPROCESSABLE_STATUS = 422
#: A valid plan this backend has no execution path for. 501 is the accurate
#: code -- "the server does not support the functionality required" -- and it
#: is deliberately not 400: the plan is not the problem, the engine is. A
#: caller must be able to tell "you asked wrongly" from "we cannot do that
#: yet" without reading prose.
NOT_IMPLEMENTED_STATUS = 501
UNAVAILABLE_STATUS = 503

#: A description longer than this is not a part description.
MAX_DESCRIPTION_CHARS = 4_000


class DescribeBody(BaseModel):
    """The one field the generate route takes."""

    text: str = Field(..., max_length=MAX_DESCRIPTION_CHARS)


class PlanBody(BaseModel):
    """A plan, as the generate route returned it."""

    plan: Dict[str, Any]
    name: Optional[str] = Field(default=None, max_length=120)


class SessionBody(BaseModel):
    """Which workspace session a request belongs to."""

    session_id: Optional[str] = Field(default=None, max_length=80)


class DrawingBody(SessionBody):
    """Which part to draw. The session's current one, by definition."""

    part_name: Optional[str] = Field(default=None, max_length=80)


class EngineeringBody(SessionBody):
    """A question about the current part, or none for the full report."""

    text: Optional[str] = Field(default=None, max_length=MAX_DESCRIPTION_CHARS)


class CatalogBody(BaseModel):
    """A plain-language catalogue query."""

    text: str = Field(..., max_length=400)


class MacroBody(SessionBody):
    """Create or run a macro. `steps` is checked against a closed vocabulary."""

    name: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=400)
    steps: Optional[List[Dict[str, Any]]] = None
    text: Optional[str] = Field(default=None, max_length=400)


class SessionExportBody(SessionBody):
    """Which format to export the current part as."""

    format: str = Field(default="step", max_length=10)


class SessionMessageBody(SessionBody):
    """One conversational turn."""

    text: str = Field(..., max_length=MAX_DESCRIPTION_CHARS)


class LocalPlanBody(BaseModel):
    """A development request: a named fixture, or a plan supplied whole."""

    fixture: Optional[str] = Field(default=None, max_length=120)
    plan: Optional[Dict[str, Any]] = None
    build: bool = True


def create_app(
    *,
    service: Optional["CadApplicationService"] = None,
    planner: Optional[OperationPlanService] = None,
    config: Optional[ExperimentalConfig] = None,
    cache_root: Optional[str] = None,
) -> FastAPI:
    """Build the experimental application.

    Every collaborator is injectable so the tests can prove a route talks to
    the thing it claims to and to nothing else. ``planner`` is optional: with
    no credential the app still starts and still validates and builds plans;
    only generation reports that it is unavailable.
    """
    settings = config if config is not None else config_from_environment()

    app = FastAPI(
        title="Experimental CAD operation plan",
        description=(
            "An experiment in a smaller CAD representation. Not the stable "
            "text-to-CAD path, and not a production interface."
        ),
        version=PLAN_SCHEMA_VERSION,
    )

    if service is None and cache_root is not None:
        try:
            # Imported here, not at module scope: this reaches CadQuery
            # through `cad_core`, and hoisting it made the whole HTTP layer
            # unimportable on a machine that has only the other engine --
            # which is exactly the machine `CAD_BACKEND=freecad` is for.
            from cad_core.application_service import CadApplicationService

            service = CadApplicationService.local(cache_root)
        except ImportError:
            # No CadQuery here, so no V1 document path. The app still starts
            # and still builds: a plan routed to the graph executor never
            # needed this. Left as None rather than substituted, and the
            # document path says so if something asks for it.
            logger.info(
                "no V1 document service available (CadQuery absent); "
                "builds will run on the selected backend only"
            )
            service = None

    app.state.config = settings
    app.state.service = service
    app.state.planner = planner
    # Experimental, in-memory, bounded. Deliberately on the app rather than a
    # module global so a test gets a fresh store per application.
    app.state.sessions = SessionStore()
    app.state.macros = MacroStore()

    def _planner() -> Optional[OperationPlanService]:
        return app.state.planner

    def _service() -> Optional["CadApplicationService"]:
        return app.state.service

    def _backend_report() -> Dict[str, Any]:
        """Which engine this deployment would execute on, and whether it can.

        Asked of the resolver, so a misconfigured `CAD_BACKEND` surfaces as
        an unavailable backend rather than as a successful build on some
        other engine.
        """
        from .cad_backend import BackendError, resolve_backend

        try:
            engine = resolve_backend()
        except BackendError as exc:
            return {"name": None, "available": False, "error": str(exc)}
        return {
            "name": engine.name,
            "available": bool(engine.available()),
            "version": engine.version(),
        }

    @app.exception_handler(RequestValidationError)
    async def _malformed(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """A malformed request envelope. The body is never echoed back."""
        return JSONResponse(
            status_code=UNPROCESSABLE_STATUS,
            content={"error": "the request body is not in the expected shape"},
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled error handling %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=500, content={"error": "internal error"}
        )

    @app.get(HEALTH_PATH)
    async def health() -> Dict[str, Any]:
        """Up, and what is configured. Presence of a credential only."""
        return {
            "status": "ok",
            "experiment": EXPERIMENT_NAME,
            "plan_schema_version": PLAN_SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "prompt_fingerprint": prompt_fingerprint(),
            "provider": settings.provider,
            "model": settings.model,
            "model_configured": credential_available(),
            # Which engine this deployment will actually execute on, read
            # from the resolver rather than from the environment string, so
            # what is reported is what would run. Never a fallback: if the
            # selected engine is unavailable this says so.
            "backend": _backend_report(),
            # True when the V1 document path is available. A build can still
            # succeed without it -- the graph executor needs no service --
            # so this is reported separately from `backend` rather than
            # standing in for "can this deployment build at all".
            "v1_document_path_available": app.state.service is not None,
            "build_available": app.state.service is not None,
            # Development routes are always available: they need no
            # credential, and they never produce a model result.
            "local_development_plan_available": True,
            "local_development_label": SOURCE_LABEL,
        }

    @app.get("/experimental/plan-schema")
    async def schema() -> Dict[str, Any]:
        """The plan's JSON Schema, for a client that wants to show it."""
        return plan_schema()

    @app.post(GENERATE_PATH)
    async def generate(
        body: DescribeBody,
        planner: Optional[OperationPlanService] = Depends(_planner),
    ) -> JSONResponse:
        """Description in, operation plan out.

        Always 200 when a model answered -- including when the answer is
        ``unsupported``. A refusal is a result, not an error. 503 means no
        model was reachable, which is a different thing entirely.
        """
        if planner is None:
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content={
                    "status": "unavailable",
                    "error": "no interpretation model is configured",
                },
            )
        result = planner.generate(body.text)
        if result.outcome is PlanOutcome.MODEL_ERROR:
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content={
                    "status": "unavailable",
                    "error": result.error or "the model did not answer",
                    "error_kind": (
                        result.error_kind.value
                        if result.error_kind is not None
                        else None
                    ),
                },
            )
        return JSONResponse(status_code=OK_STATUS, content=_plan_payload(result))

    @app.post(VALIDATE_PATH)
    async def validate(body: PlanBody) -> JSONResponse:
        """Parse and validate a plan that came from anywhere.

        Deliberately exposed: it is the same parser and the same validator the
        generate route uses, so a client can check a hand-edited plan without
        spending a model call.
        """
        try:
            plan = parse_plan(body.plan)
        except PlanParseError as exc:
            return JSONResponse(
                status_code=OK_STATUS,
                content={
                    "valid": False,
                    "parsed": False,
                    "problems": [
                        {"code": "parse", "message": exc.message, "where": ""}
                    ],
                },
            )
        verdict = validate_plan(plan)
        return JSONResponse(
            status_code=OK_STATUS,
            content={
                "valid": verdict.valid,
                "parsed": True,
                "plan": plan.to_dict(),
                "problems": [p.to_dict() for p in verdict.problems],
                # The dependency and history graph, derived from the plan
                # alone. A chained plan's most useful question -- what is
                # this solid made of, and did anything get left behind --
                # cannot be answered from a flat list of operations. It is
                # reported, never enforced: `terminal_solids` of length two
                # is a fact, and S9 is the V1 validator's to rule on.
                "history": plan_history(plan).to_dict(),
                # The structure beside the state: nodes, role-tagged edges,
                # the deterministic execution order, and any cycle. What an
                # agent needs to answer "why was this rejected" without
                # re-deriving the plan's shape for itself.
                "graph": feature_graph(plan).to_dict(),
            },
        )

    @app.post(BUILD_PATH)
    async def build(
        body: PlanBody,
        service: Optional["CadApplicationService"] = Depends(_service),
    ) -> JSONResponse:
        """Build a plan on the selected backend. No new CAD logic.

        The service is **not** required up front any more. It belongs to the
        V1 document path, which is the CadQuery engine; a plan routed to the
        graph executor never touches it, and on a machine that has only the
        other engine there is no service to configure. Refusing every build
        here would have made `CAD_BACKEND=freecad` unusable on exactly the
        machine it exists for. `build_plan` asks for the service only on the
        path that uses one, and says so plainly when it is missing.
        """
        try:
            plan = parse_plan(body.plan)
        except PlanParseError as exc:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={"error": exc.message},
            )
        verdict = validate_plan(plan)
        if not verdict.valid:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={
                    "error": "the plan is not valid",
                    "problems": [p.to_dict() for p in verdict.problems],
                },
            )
        if plan.status is not PlanStatus.GENERATED:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={
                    "error": f"a `{plan.status.value}` plan has no geometry"
                },
            )

        result = build_plan(
            service, plan, name=body.name or "experimental-part"
        )
        if result.execution_unsupported:
            # Reported before the generic branch, and with no `document`: an
            # unsupported plan produces no V1 document at all, so there is
            # nothing to show that could be mistaken for a build.
            return JSONResponse(
                status_code=NOT_IMPLEMENTED_STATUS,
                content={
                    "error": result.error,
                    "execution_unsupported": True,
                    "unsupported_types": list(result.unsupported_types),
                    "unsupported_operations": list(result.unsupported_ids),
                },
            )
        if result.executed:
            # The graph-driven executor ran, which happens exactly when a
            # selector is richer than Section C.7 can express -- a
            # `straight`, a `circular`, or a rim's `position`. Such a plan
            # has **no V1 document and no BuildOutcome by design**
            # (`PlanBuild.execution` is never set alongside `document`), so
            # the generic `outcome is None` branch below reported every
            # Stage 47 selector as "the plan cannot be built" while the
            # build had in fact succeeded.
            #
            # Nothing is recomputed here. `ExecutionResult.to_dict()` is the
            # executor's own neutral serialization and already carries the
            # bodies with their measurements, the typed `selections` -- one
            # `Resolution` per operation, with its indices, candidates,
            # seams and R-code -- and the structured `failure`. A failed
            # execution keeps its selections too, which is why the failure
            # path returns the same shape rather than a bare message.
            execution = result.execution
            status = OK_STATUS if result.built else BAD_REQUEST_STATUS
            graph_payload: Dict[str, Any] = {
                "executed_by_graph": True,
                # Stated on every answer, never left to be inferred from
                # which field happens to be present: a caller who asked for
                # one engine must be able to read back which one ran.
                "backend": result.backend,
                "execution_path": result.execution_path,
                "execution": execution.to_dict(),
            }
            # The same neutral RenderModel the document path returns, built
            # by whichever engine executed. Its absence is not a failure --
            # the geometry is measured either way -- so it is omitted rather
            # than sent as null, exactly as the document path does.
            if result.render is not None:
                graph_payload["render"] = result.render.to_dict()
            graph_payload["bodies"] = [
                {
                    "body_id": body.id,
                    "declared": body.id in execution.declared,
                    "render": (result.renders[body.id].to_dict()
                               if body.id in result.renders else None),
                }
                for body in execution.bodies
            ]
            graph_payload["declared_bodies"] = list(execution.declared)
            return JSONResponse(status_code=status, content=graph_payload)
        if result.outcome is None:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={"error": result.error or "the plan cannot be built"},
            )
        payload: Dict[str, Any] = {
            "document": result.document,
            "build": result.outcome.to_dict(),
            "backend": result.backend,
            "execution_path": result.execution_path,
        }
        render = result.outcome.render_model
        if render is not None:
            payload["render"] = render.to_dict()
        return JSONResponse(status_code=OK_STATUS, content=payload)

    # --- the multi-turn copilot ------------------------------------------
    #
    # One route per turn, because the server is what remembers the part. It
    # composes the SAME three steps the individual routes expose -- generate,
    # validate, build -- and adds no CAD logic of its own. The canonical
    # Operation Plan stays the authoritative representation throughout; there
    # is no second CAD state anywhere in this block.

    def _facts(build: Any) -> Dict[str, Any]:
        """The measurement, from whichever path built it."""
        if build.executed and build.execution.bodies:
            # `result.part` -- the single live body, or `None` -- never
            # `bodies[0]`. A declared two-body part has no single
            # measurement, and returning the first body's would report one
            # body's volume as the part's. Per-body numbers travel in
            # `bodies`, beside this, where they say which body they are.
            part_id = build.execution.part
            if part_id is None:
                return {}
            for body in build.execution.bodies:
                if body.id == part_id:
                    measured = body.measurement
                    return (measured.to_dict()
                            if hasattr(measured, "to_dict") else {})
            return {}
        if build.outcome is not None:
            for artifact in (build.outcome.to_dict().get("manifest") or {}).get(
                "artifacts", []
            ):
                if artifact.get("kind") == "geometry":
                    detail = artifact.get("details") or {}
                    box = (detail.get("bounding_box") or {}).get("size") or {}
                    return {
                        "is_valid": True,
                        "solid_count": detail.get("solid_count"),
                        "volume": detail.get("volume_mm3"),
                        "face_count": detail.get("face_count"),
                        "edge_count": detail.get("edge_count"),
                        "size": [box.get(axis) for axis in ("x", "y", "z")],
                    }
        return {}

    def _built_payload(build: Any, session: Any) -> Dict[str, Any]:
        """What the page needs to draw and describe a successful build."""
        payload: Dict[str, Any] = {
            "backend": build.backend,
            "execution_path": build.execution_path,
            "measurement": _facts(build),
        }
        if build.executed:
            payload["execution"] = build.execution.to_dict()
            payload["declared_bodies"] = list(build.execution.declared)
        if build.render is not None:
            payload["render"] = build.render.to_dict()
        elif build.outcome is not None and build.outcome.render_model is not None:
            payload["render"] = build.outcome.render_model.to_dict()
        # One mesh per body, each saying which body it is. Always sent when
        # the executor ran -- a single-body result carries its one entry -- so
        # the page has ONE way to draw both cases instead of a special case
        # for each, and a two-body part can never arrive looking like a
        # one-body part that happens to be missing a piece.
        bodies = _body_payload(build)
        if bodies is not None:
            payload["bodies"] = bodies
        payload["session"] = session.to_dict()
        return payload

    def _body_payload(build: Any) -> Optional[List[Dict[str, Any]]]:
        """Per-body meshes and measurements, or ``None`` off the graph path."""
        if not build.executed:
            return None
        measured = {
            body.id: body.measurement for body in build.execution.bodies
        }
        rows: List[Dict[str, Any]] = []
        for body in build.execution.bodies:
            render = build.renders.get(body.id)
            measurement = measured.get(body.id)
            rows.append({
                "body_id": body.id,
                "features": list(body.features),
                "declared": body.id in build.execution.declared,
                "measurement": (measurement.to_dict()
                                if hasattr(measurement, "to_dict") else None),
                "render": render.to_dict() if render is not None else None,
            })
        return rows

    def _rebuild(plan_payload: Dict[str, Any], service: Any) -> Any:
        """Parse, validate and build one plan. Never raises for its content."""
        plan = parse_plan(plan_payload)
        verdict = validate_plan(plan)
        if not verdict.valid:
            return None, verdict, None
        return plan, verdict, build_plan(service, plan, name="experimental-part")

    def _commit_build(
        session: Any, base: Dict[str, Any], request_text: str,
        plan: Any, verdict: Any, build: Any, *,
        editing: bool, summary: Optional[str], prefix: str = "",
    ) -> Optional[JSONResponse]:
        """Advance the session, but only for a plan that actually built.

        The single place the current model moves, whichever route produced
        the plan. Returns ``None`` when the plan did not validate, had no
        execution path or failed in the kernel -- so a caller with another
        route left may try it, and one without falls through to reporting
        the failure in its own words. Nothing is committed in that case,
        which is the rule this route exists to keep.
        """
        if plan is None or build is None or not build.built:
            return None
        if build.execution_unsupported:
            return None
        session.commit(Revision(
            plan=plan.to_dict(),
            summary=summary or "the part",
            request=request_text,
            measurement=_facts(build),
            backend=build.backend,
        ))
        reply = (
            prefix
            + ("Updated " if editing else "Created ")
            + describe_model(session.current)
            + "."
        )
        session.said(ASSISTANT, reply)
        return JSONResponse(
            status_code=OK_STATUS,
            content={**base, "status": "built", "reply": reply,
                     "plan": plan.to_dict(), **_built_payload(build, session)},
        )

    @app.post(SESSION_MESSAGE_PATH)
    async def session_message(
        body: SessionMessageBody,
        planner: Optional[OperationPlanService] = Depends(_planner),
        service: Optional["CadApplicationService"] = Depends(_service),
    ) -> JSONResponse:
        """One conversational turn against the session's current part.

        The rule this route exists to keep: **a failed edit never replaces the
        model that still builds.** The session's current plan is advanced only
        after a build has actually succeeded, so a refusal, a clarification,
        an invalid plan or a kernel error all leave the previous part intact
        and on screen.
        """
        session = app.state.sessions.get(body.session_id)
        request_text = (body.text or "").strip()
        if not request_text:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={"error": "say what you would like to build or change"},
            )

        session.said(USER, request_text)

        # A question about the part we already built is answered from the
        # executor's own measurement and the plan's own declarations, not by
        # asking a model to recall geometry it cannot see. No call is made
        # and nothing is rebuilt.
        #
        # This is asked BEFORE the no-model branch below, because answering
        # it needs no planner -- it reads a build that already happened. Put
        # after, "how many holes does it have?" returned 503 "no
        # interpretation model is configured" about a part sitting in the
        # session with its holes already counted: the one question in this
        # file that provably needs no model was the one refused for want of
        # one.
        #
        # The richer answerer is asked first: it can say how many holes there
        # are, what they measure, what the part would weigh in a named
        # material -- and it says of every number whether it was MEASURED,
        # DECLARED or CALCULATED. The older one remains as the fallback for
        # the broad "tell me about it" questions it already handled.
        if session.current is not None:
            evidence = answer_from_evidence(
                session.current.plan, session.current.measurement,
                session.current.backend or "the CAD engine", request_text,
            )
            if evidence is not None:
                session.said(ASSISTANT, evidence.text)
                return JSONResponse(
                    status_code=OK_STATUS,
                    content={"session_id": session.session_id,
                             "editing": True, "status": "answered",
                             "reply": evidence.text,
                             "from_evidence": True,
                             "evidence": evidence.to_dict(),
                             "measurement": dict(session.current.measurement),
                             "session": session.to_dict()},
                )

            answered = measurement_answer(session, request_text)
            if answered is not None:
                session.said(ASSISTANT, answered)
                return JSONResponse(
                    status_code=OK_STATUS,
                    content={"session_id": session.session_id, "editing": True,
                             "status": "answered", "reply": answered,
                             "from_evidence": True,
                             "measurement": dict(session.current.measurement),
                             "session": session.to_dict()},
                )

        # No model configured is the *hardest* case of "the model was no
        # use", not a different one, so it goes to the same deterministic
        # reader rather than short-circuiting past it. Returning 503 here
        # made the provider-neutral route unreachable precisely when it was
        # the only route left -- a request this project can read without any
        # model at all was refused for want of one.
        #
        # The reader either understands the request completely or declines,
        # and a decline still ends in the 503 below. Nothing is guessed to
        # avoid an error.
        if planner is None:
            current_plan = (session.current.plan
                            if session.current is not None else None)
            reading = deterministic_interpretation(
                request_text, current_plan, note=NO_MODEL_NOTE)
            if reading.understood:
                base: Dict[str, Any] = {
                    "session_id": session.session_id,
                    "editing": session.has_model,
                    "interpreted_by": reading.to_dict(),
                }
                plan, verdict, build = _rebuild(reading.plan.to_dict(), service)
                outcome = _commit_build(
                    session, base, request_text, plan, verdict, build,
                    editing=session.has_model,
                    summary=(reading.plan.summary if reading.plan else None),
                    prefix=NO_MODEL_NOTE + " ",
                )
                if outcome is not None:
                    return outcome
            if reading.refused and reading.error:
                # The local grammar recognised the request and knows exactly
                # why it cannot be done -- a hole wider than the stock, a
                # length in inches. That is a far better thing to tell someone
                # than "no model is configured", which is true and useless:
                # configuring a model would not make the hole fit.
                session.said(ASSISTANT, reading.error)
                return JSONResponse(
                    status_code=OK_STATUS,
                    content={"session_id": session.session_id,
                             "status": "refused",
                             "interpreted_by": reading.to_dict(),
                             "reply": reading.error,
                             "session": session.to_dict()},
                )
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content={
                    "status": "unavailable",
                    "error": "no interpretation model is configured",
                    "session": session.to_dict(),
                },
            )

        # A first request is interpreted exactly as it always was. A later one
        # carries the current plan and a bounded slice of the conversation, so
        # "make it wider" has something to be wider than.
        editing = session.has_model
        context = revision_context(session, request_text) if editing else None
        result = planner.generate(
            request_text,
            context=context,
            max_output_tokens=REVISION_OUTPUT_TOKENS if editing else None,
        )

        base: Dict[str, Any] = {
            "session_id": session.session_id,
            "editing": editing,
            "metadata": result.metadata.to_dict(),
        }

        # One question, asked once, whatever the provider did: is there a
        # plan to build? A provider that answered usefully is believed; one
        # that did not hands the same request to the deterministic reader,
        # which either reads it completely or declines. Both routes end at
        # the same parser, validator and executor below -- nothing here
        # shortcuts to geometry, and `interpreted_by` says which ran.
        reading = interpret(
            request_text, result,
            session.current.plan if session.current is not None else None,
        )
        if reading.source == SOURCE_DETERMINISTIC and reading.understood:
            base["interpreted_by"] = reading.to_dict()
            plan, verdict, build = _rebuild(reading.plan.to_dict(), service)
            outcome = _commit_build(
                session, base, request_text, plan, verdict, build,
                editing=editing,
                summary=(reading.plan.summary if reading.plan else None),
                prefix=DETERMINISTIC_NOTE + " ",
            )
            if outcome is not None:
                return outcome

        if result.outcome is PlanOutcome.MODEL_ERROR:
            session.said(ASSISTANT, "I could not reach the model.")
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content={**base, "status": "unavailable",
                         "error": result.error or "the model did not answer",
                         "session": session.to_dict()},
            )

        if result.outcome is PlanOutcome.NEEDS_CLARIFICATION:
            questions = list(result.plan.questions) if result.plan else []
            reply = " ".join(questions) or "Could you be more specific?"
            session.said(ASSISTANT, reply)
            # Nothing is built and nothing is replaced.
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "needs_clarification",
                         "questions": questions, "reply": reply,
                         "session": session.to_dict()},
            )

        if result.outcome is PlanOutcome.UNSUPPORTED:
            reply = (result.plan.reason if result.plan else None) or (
                "That is outside what this CAD language can express."
            )
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "unsupported", "reply": reply,
                         "session": session.to_dict()},
            )

        if result.outcome is not PlanOutcome.GENERATED or result.plan is None:
            reply = "The model's answer was not a usable plan, so I left the part as it was."
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "invalid_model_output",
                         "reply": reply, "session": session.to_dict()},
            )

        revised = result.plan.to_dict()
        plan, verdict, build = _rebuild(revised, service)
        if plan is None:
            reply = "The revised plan did not pass validation, so I kept the previous part."
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "invalid_plan", "reply": reply,
                         "problems": [p.to_dict() for p in verdict.problems],
                         "session": session.to_dict()},
            )

        if build.execution_unsupported:
            reply = (
                "The plan is valid, but this backend cannot execute "
                f"{', '.join(build.unsupported_types)}. The part is unchanged."
            )
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "execution_unsupported",
                         "reply": reply, "session": session.to_dict()},
            )

        if not build.built:
            failure = (
                build.execution.failure if build.executed and build.execution else None
            )
            detail = failure.message if failure is not None else (build.error or "")
            reply = f"That change did not build: {detail} The previous part is unchanged."
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={**base, "status": "build_failed", "reply": reply,
                         "failure": failure.to_dict() if failure else None,
                         "session": session.to_dict()},
            )

        committed = _commit_build(
            session, base, request_text, plan, verdict, build,
            editing=editing, summary=result.plan.summary,
        )
        assert committed is not None  # every failure branch returned above
        return committed

    @app.post(SESSION_UNDO_PATH)
    async def session_undo(
        body: SessionBody,
        service: Optional["CadApplicationService"] = Depends(_service),
    ) -> JSONResponse:
        """Step back to the previous plan that built, and rebuild it.

        Server-side, from the revision stack -- never from anything the
        browser is holding. The restored plan is rebuilt rather than trusted,
        so what returns to the viewport is geometry the engine produced now.
        """
        session = app.state.sessions.get(body.session_id)
        restored = session.undo()
        if restored is None:
            reply = "There is nothing to undo."
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={"status": "nothing_to_undo", "reply": reply,
                         "session_id": session.session_id,
                         "session": session.to_dict()},
            )

        plan, verdict, build = _rebuild(restored.plan, service)
        if plan is None or not build.built:
            reply = "The previous plan no longer builds, so nothing was changed."
            session.said(ASSISTANT, reply)
            return JSONResponse(
                status_code=OK_STATUS,
                content={"status": "build_failed", "reply": reply,
                         "session_id": session.session_id,
                         "session": session.to_dict()},
            )

        reply = f"Undone. Back to {describe_model(restored)}."
        session.said(USER, "Undo")
        session.said(ASSISTANT, reply)
        return JSONResponse(
            status_code=OK_STATUS,
            content={"status": "built", "reply": reply,
                     "session_id": session.session_id,
                     "plan": restored.plan, **_built_payload(build, session)},
        )

    @app.post(SESSION_RESET_PATH)
    async def session_reset(body: SessionBody) -> JSONResponse:
        """Start a new part.

        The CAD state -- plan, evidence and revision history -- is cleared.
        The conversation is KEPT and a marker turn appended, because a thread
        that silently empties itself destroys the record of what was built;
        the next request simply has no current model, so it is interpreted as
        a first request again.
        """
        session = app.state.sessions.get(body.session_id)
        session.reset(keep_conversation=True)
        reply = "Started a new part. Describe what you would like to build."
        session.said(ASSISTANT, reply)
        return JSONResponse(
            status_code=OK_STATUS,
            content={"status": "reset", "reply": reply,
                     "session_id": session.session_id,
                     "session": session.to_dict()},
        )

    @app.post(SESSION_STATE_PATH)
    async def session_state(body: SessionBody) -> JSONResponse:
        """What this session currently holds. Reads nothing, changes nothing."""
        session = app.state.sessions.get(body.session_id)
        return JSONResponse(
            status_code=OK_STATUS,
            content={"session_id": session.session_id,
                     "session": session.to_dict()},
        )

    @app.post(SESSION_EXPORT_PATH)
    async def session_export(
        body: SessionExportBody,
        service: Optional["CadApplicationService"] = Depends(_service),
    ) -> Any:
        """Export the session's CURRENT successful part.

        Exports the plan the session committed -- never whatever was last
        asked for. A refused or failed edit leaves `current` untouched, so it
        is structurally impossible to export a part that did not build.

        The shape is produced by rebuilding that plan on the selected
        backend and handing it to the backend's own exporter. No second
        exporter exists here and no geometry is written by this module.
        """
        from fastapi.responses import Response

        session = app.state.sessions.get(body.session_id)
        if session.current is None:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={"error": "there is no built part to export"},
            )
        fmt = (body.format or "step").lower()
        if fmt not in ("step", "stl"):
            return JSONResponse(
                status_code=NOT_IMPLEMENTED_STATUS,
                content={"error": f"{fmt} export is not available",
                         "available": ["step", "stl"]},
            )

        import tempfile
        from pathlib import Path as _Path

        from .cad_backend import BackendError, resolve_backend
        from .executor import execute_plan

        try:
            engine = resolve_backend()
            plan = parse_plan(session.current.plan)
            result = execute_plan(plan, part_name="experimental-part",
                                  backend=engine)
            if not result.succeeded or not result.bodies:
                raise BackendError("the current part did not rebuild")
            if result.part is None:
                # A declared multi-body part. Exporting `bodies[0]` would
                # hand back a file silently missing the rest of the part,
                # which is the one thing this project's export semantics
                # forbid. A STEP assembly is real work with its own gate
                # (`docs/multi-body-design.md` step 5); until it exists this
                # says so rather than shipping a lie.
                raise BackendError(
                    "this part has "
                    f"{len(result.bodies)} separate bodies "
                    f"({', '.join(repr(b.id) for b in result.bodies)}), and "
                    "export writes a single solid. Exporting one of them "
                    "would silently drop the others; a multi-body export is "
                    "not implemented yet"
                )
            shape = result.shapes.get(result.part)
            with tempfile.TemporaryDirectory() as folder:
                target = _Path(folder) / f"part.{fmt}"
                if fmt == "step":
                    engine.export_step(shape, target)
                else:
                    engine.export_stl(shape, target)
                data = target.read_bytes()
        except BackendError as exc:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": str(exc)})

        return Response(
            content=data,
            media_type="application/step" if fmt == "step" else "model/stl",
            headers={"content-disposition": f'attachment; filename="part.{fmt}"',
                     "x-cad-backend": engine.name},
        )

    # --- the product surfaces --------------------------------------------
    #
    # Each one reads the session's CURRENT part. None of them holds CAD state
    # of its own, and none reaches past the backend abstraction.

    def _current_shape(session: Any) -> Any:
        """Rebuild the session's current plan and hand back the shape.

        Rebuilt rather than cached: the shape is an execution result, and the
        plan is what the session actually stores. This is the one place the
        surfaces get geometry, so they cannot disagree about what "the
        current part" is.
        """
        from .cad_backend import BackendError, resolve_backend
        from .executor import execute_plan

        engine = resolve_backend()
        plan = parse_plan(session.current.plan)
        result = execute_plan(plan, part_name="experimental-part",
                              backend=engine)
        if not result.succeeded or not result.bodies:
            raise BackendError("the current part did not rebuild")
        if result.part is None:
            # Same reason as the export route: a drawing or a measurement of
            # `bodies[0]` would describe part of the part as though it were
            # all of it.
            raise BackendError(
                f"this part has {len(result.bodies)} separate bodies "
                f"({', '.join(repr(b.id) for b in result.bodies)}); this "
                "surface describes a single body and would otherwise "
                "describe only the first"
            )
        return engine, result.shapes.get(result.part)

    @app.post(DRAWING_PATH)
    async def drawing(body: DrawingBody) -> JSONResponse:
        """A basic engineering drawing of the current part.

        Views are real projections from the backend; every dimension comes
        from the measurement of the build that succeeded. A backend that
        cannot project says so rather than returning an empty sheet.
        """
        from .cad_backend import BackendError

        session = app.state.sessions.get(body.session_id)
        if session.current is None:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": "there is no part to draw"})
        try:
            engine, shape = _current_shape(session)
            sheet = build_drawing(
                engine, shape,
                part_name=(body.part_name or session.current.summary
                           or "experimental-part"),
                measurement=session.current.measurement)
        except NotImplementedError:
            return JSONResponse(
                status_code=NOT_IMPLEMENTED_STATUS,
                content={"error": "this backend cannot project drawing views",
                         "capability": "project_edges"})
        except (BackendError, ValueError) as exc:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": str(exc)})
        return JSONResponse(status_code=OK_STATUS,
                            content={"session_id": session.session_id,
                                     "drawing": sheet.to_dict()})

    @app.post(ENGINEERING_PATH)
    async def engineering(body: EngineeringBody) -> JSONResponse:
        """Answer an engineering question, or report everything known.

        Measured and calculated values are returned in separate lists and
        each finding names which it is, so a caller cannot present a
        calculation as something the engine measured.
        """
        session = app.state.sessions.get(body.session_id)
        if session.current is None:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": "there is no part to analyse"})
        plan = session.current.plan
        measurement = session.current.measurement
        if body.text:
            answered = eng.analyse(plan, measurement, body.text)
            if answered is None:
                return JSONResponse(
                    status_code=OK_STATUS,
                    content={"session_id": session.session_id,
                             "answered": False,
                             "reply": ("I can answer questions about the "
                                       "current part's size, volume, holes, "
                                       "fillets and how much material the "
                                       "holes remove.")})
            return JSONResponse(
                status_code=OK_STATUS,
                content={"session_id": session.session_id, "answered": True,
                         "reply": eng.as_text(answered), **answered})
        return JSONResponse(status_code=OK_STATUS,
                            content={"session_id": session.session_id,
                                     "answered": True,
                                     **eng.report(plan, measurement)})

    @app.post(CATALOG_PATH)
    async def catalog_search(body: CatalogBody) -> JSONResponse:
        """Search the LOCAL reference catalogue. No network, no supplier."""
        return JSONResponse(status_code=OK_STATUS,
                            content=catalogue.search(body.text))

    @app.post(MACRO_PATH)
    async def macros(body: MacroBody) -> JSONResponse:
        """Create or list macros for this session.

        Steps are validated against a closed vocabulary before anything is
        stored, so a macro that cannot be run also cannot be saved.
        """
        session = app.state.sessions.get(body.session_id)
        store = app.state.macros
        if body.name is None and body.steps is None and body.text is None:
            return JSONResponse(
                status_code=OK_STATUS,
                content={"session_id": session.session_id,
                         "macros": [m.to_dict() for m in store.list(session.session_id)],
                         "actions": {k: v["summary"] for k, v in ACTIONS.items()}})

        steps = body.steps
        if steps is None and body.text:
            # Read the request against the same closed vocabulary. Anything
            # unrecognised is simply not included, and the caller is told.
            steps = list(steps_from_language(body.text))
        try:
            macro = store.create(session.session_id, body.name or "",
                                 body.description or "", steps or [])
        except MacroError as exc:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": str(exc),
                                         "actions": sorted(ACTIONS)})
        return JSONResponse(status_code=OK_STATUS,
                            content={"session_id": session.session_id,
                                     "macro": macro.to_dict()})

    @app.post(MACRO_RUN_PATH)
    async def macro_run(body: MacroBody) -> JSONResponse:
        """Run a stored macro against the current part.

        Every step performs an action the workspace already exposes. A step
        that fails stops the run and is reported with the steps that did
        succeed -- a macro is not a transaction, and pretending otherwise
        would hide what actually happened.
        """
        from .cad_backend import BackendError

        session = app.state.sessions.get(body.session_id)
        store = app.state.macros
        try:
            macro = store.get(session.session_id, body.name or "")
        except MacroError as exc:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": str(exc)})
        if session.current is None:
            return JSONResponse(status_code=BAD_REQUEST_STATUS,
                                content={"error": "there is no part to run this on"})

        import tempfile
        from pathlib import Path as _Path

        results: List[Dict[str, Any]] = []
        for step in macro.steps:
            entry: Dict[str, Any] = {"action": step.action}
            try:
                if step.action in ("export_step", "export_stl"):
                    engine, shape = _current_shape(session)
                    suffix = "step" if step.action == "export_step" else "stl"
                    with tempfile.TemporaryDirectory() as folder:
                        target = _Path(folder) / f"part.{suffix}"
                        if suffix == "step":
                            engine.export_step(shape, target)
                        else:
                            engine.export_stl(shape, target)
                        entry.update(ok=True, bytes=target.stat().st_size,
                                     format=suffix, backend=engine.name)
                elif step.action == "drawing":
                    engine, shape = _current_shape(session)
                    sheet = build_drawing(
                        engine, shape, part_name=session.current.summary or "part",
                        measurement=session.current.measurement)
                    entry.update(ok=True, views=len(sheet.views),
                                 scale=sheet.scale)
                elif step.action == "engineering_report":
                    payload = eng.report(session.current.plan,
                                         session.current.measurement)
                    entry.update(ok=True, findings=len(payload["findings"]))
                elif step.action == "rename":
                    entry.update(ok=True, name=step.parameters.get("name"))
                else:  # unreachable: the vocabulary is closed
                    entry.update(ok=False, error="unknown action")
            except (BackendError, NotImplementedError, ValueError) as exc:
                entry.update(ok=False, error=str(exc))
                results.append(entry)
                break
            results.append(entry)

        return JSONResponse(
            status_code=OK_STATUS,
            content={"session_id": session.session_id, "macro": macro.name,
                     "steps": results,
                     "ok": all(r.get("ok") for r in results)})

    # --- development routes ---------------------------------------------
    #
    # These exist because the Anthropic credential is unavailable here, so
    # the model call is the one step that cannot be exercised. They run a
    # developer-supplied plan through the real parser, plan validator, V1
    # adapter, existing validator, CAD engine and RenderModel -- and every
    # response is stamped LOCAL_DEVELOPMENT_PLAN, so nothing they return can
    # be read as a Claude result.

    @app.get(LOCAL_FIXTURES_PATH)
    async def local_fixtures() -> Dict[str, Any]:
        """The development fixtures, with the geometry each should produce."""
        return stamp({"fixtures": describe_fixtures()})

    @app.post(LOCAL_PLAN_PATH)
    async def local_plan(
        body: LocalPlanBody,
        service: Optional["CadApplicationService"] = Depends(_service),
    ) -> JSONResponse:
        """Run a fixture, or a supplied plan, through the whole real path.

        Not a generation route: no model is called and no credential is
        read. Exactly one of ``fixture`` or ``plan`` is given.
        """
        if (body.fixture is None) == (body.plan is None):
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content=stamp(
                    {"error": "give exactly one of `fixture` or `plan`"}
                ),
            )
        if body.build and service is None:
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content=stamp({"error": "no build service is configured"}),
            )

        plan_payload = body.plan
        if body.fixture is not None:
            try:
                plan_payload = fixture_plan(body.fixture)
            except KeyError as exc:
                return JSONResponse(
                    status_code=BAD_REQUEST_STATUS,
                    content=stamp({"error": str(exc)}),
                )

        try:
            plan = parse_plan(plan_payload)
        except PlanParseError as exc:
            return JSONResponse(
                status_code=OK_STATUS,
                content=stamp(
                    {
                        "parsed": False,
                        "plan_valid": False,
                        "error": exc.message,
                    }
                ),
            )
        verdict = validate_plan(plan)
        payload: Dict[str, Any] = {
            "parsed": True,
            "plan_valid": verdict.valid,
            "plan": plan.to_dict(),
            "problems": [problem.to_dict() for problem in verdict.problems],
        }
        if not verdict.valid or not body.build:
            return JSONResponse(status_code=OK_STATUS, content=stamp(payload))
        if plan.status is not PlanStatus.GENERATED:
            payload["error"] = f"a `{plan.status.value}` plan has no geometry"
            return JSONResponse(status_code=OK_STATUS, content=stamp(payload))

        assert service is not None
        built = build_plan(
            service, plan, name=body.fixture or "local-development-plan"
        )
        payload["v1_document"] = built.document
        payload["built"] = built.built
        # Always present, true or false, beside `built`. A client should never
        # have to tell an absent key from a false one to learn which kind of
        # "not built" it is looking at.
        payload["execution_unsupported"] = built.execution_unsupported
        payload["unsupported_types"] = list(built.unsupported_types)
        payload["unsupported_operations"] = list(built.unsupported_ids)
        if built.outcome is None:
            payload["build_error"] = built.error
            return JSONResponse(status_code=OK_STATUS, content=stamp(payload))
        payload["build"] = built.outcome.to_dict()
        render = built.outcome.render_model
        if render is not None:
            payload["render"] = render.to_dict()
        return JSONResponse(status_code=OK_STATUS, content=stamp(payload))

    return app


def _plan_payload(result: PlanGenerationResult) -> Dict[str, Any]:
    """The public shape of a generation result.

    Note what is not in it: ``raw_text`` and ``error_detail``. The first is
    model-authored text and the second can quote a provider's own message;
    neither belongs in a response body.
    """
    plan = result.plan
    payload: Dict[str, Any] = {
        "status": result.outcome.value,
        "operations": (
            [op for op in plan.to_dict()["operations"]]
            if plan is not None
            else []
        ),
        "summary": plan.summary if plan is not None else "",
        "metadata": result.metadata.to_dict(),
    }
    if plan is not None:
        if plan.reason:
            payload["reason"] = plan.reason
        if plan.questions:
            payload["questions"] = list(plan.questions)
    if result.outcome is PlanOutcome.INVALID_MODEL_OUTPUT:
        payload["error"] = result.error
        if result.plan_validation is not None:
            payload["problems"] = [
                p.to_dict() for p in result.plan_validation.problems
            ]
    return payload


def app_from_environment() -> FastAPI:
    """Factory for ``uvicorn --factory``. Reads the environment, explicitly.

    Requires ``CAD_EXPERIMENTAL_CACHE_ROOT``: like the stable application,
    this one does not invent a filesystem path. The planner is built only
    when a credential is actually present, so an unconfigured deployment
    starts and serves everything except generation.
    """
    import os

    from cad_ai.config import AiConfig

    cache_root = os.environ.get("CAD_EXPERIMENTAL_CACHE_ROOT", "").strip()
    if not cache_root:
        raise RuntimeError(
            "CAD_EXPERIMENTAL_CACHE_ROOT is not set; the experimental "
            "application needs an explicit build cache root and does not "
            "invent one"
        )
    settings = config_from_environment()
    planner = None
    if credential_available():
        from cad_ai.anthropic_provider import AnthropicTextToCadModel

        model = AnthropicTextToCadModel.from_environment(
            AiConfig(
                model=settings.model,
                provider=settings.provider,
                timeout_seconds=settings.timeout_seconds,
            )
        )
        planner = OperationPlanService(model, settings)
    return create_app(
        config=settings, planner=planner, cache_root=cache_root
    )


__all__ = [
    "BUILD_PATH",
    "GENERATE_PATH",
    "HEALTH_PATH",
    "VALIDATE_PATH",
    "app_from_environment",
    "create_app",
]
