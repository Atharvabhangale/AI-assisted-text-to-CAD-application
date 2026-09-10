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

from cad_core.application_service import CadApplicationService

from . import EXPERIMENT_NAME, PLAN_SCHEMA_VERSION
from .build import build_plan
from .config import (
    ExperimentalConfig,
    config_from_environment,
    credential_available,
)
from .generation import OperationPlanService, PlanGenerationResult, PlanOutcome
from .parser import PlanParseError, parse_plan
from .plan import PlanStatus, plan_schema
from .prompt import PROMPT_VERSION, prompt_fingerprint
from .validation import validate_plan

logger = logging.getLogger("cad_experimental")

HEALTH_PATH = "/experimental/health"
GENERATE_PATH = "/experimental/generate-plan"
VALIDATE_PATH = "/experimental/validate-plan"
BUILD_PATH = "/experimental/build-plan"

OK_STATUS = 200
BAD_REQUEST_STATUS = 400
UNPROCESSABLE_STATUS = 422
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


def create_app(
    *,
    service: Optional[CadApplicationService] = None,
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
        service = CadApplicationService.local(cache_root)

    app.state.config = settings
    app.state.service = service
    app.state.planner = planner

    def _planner() -> Optional[OperationPlanService]:
        return app.state.planner

    def _service() -> Optional[CadApplicationService]:
        return app.state.service

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
            "build_available": app.state.service is not None,
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
            },
        )

    @app.post(BUILD_PATH)
    async def build(
        body: PlanBody,
        service: Optional[CadApplicationService] = Depends(_service),
    ) -> JSONResponse:
        """Build a plan with the existing CAD service. No new CAD logic."""
        if service is None:
            return JSONResponse(
                status_code=UNAVAILABLE_STATUS,
                content={"error": "no build service is configured"},
            )
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
        if result.outcome is None:
            return JSONResponse(
                status_code=BAD_REQUEST_STATUS,
                content={"error": result.error or "the plan cannot be built"},
            )
        payload: Dict[str, Any] = {
            "document": result.document,
            "build": result.outcome.to_dict(),
        }
        render = result.outcome.render_model
        if render is not None:
            payload["render"] = render.to_dict()
        return JSONResponse(status_code=OK_STATUS, content=payload)

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
