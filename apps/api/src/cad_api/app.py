"""The ASGI application: three routes, and no CAD logic.

```
POST /validate  ->  ValidateBody  ->  CadApiContract.validate_document_payload
POST /build     ->  BuildBody     ->  CadApiContract.build_document_payload
GET  /health    ->  {"status": "ok"}
```

Each route does four things: let Pydantic check the envelope, hand the payload
to the transport-neutral contract, choose a status from the contract's
``failure`` value, and return the payload as JSON. Nothing else. There is no
validation rule, no geometry call, no exporter call, no cache call and no
process management in this module -- tests assert it imports none of those
modules and calls none of their names.

**Not exposed**, and not implemented: editing, deleting, versioning or
comparing documents; FeatureScript; filesystem browsing; file download;
artifact bytes; job control; authentication; anything else.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cad_core.api_contract import (
    BuildDocumentResponse,
    CadApiContract,
    ErrorContract,
    ValidateDocumentResponse,
)
from cad_core.application_service import CadApplicationService, ServiceFailure

from cad_api.config import ApiConfig
from cad_api.schemas import BuildBody, ValidateBody
from cad_api.status import (
    INTERNAL_STATUS,
    OK_STATUS,
    TRANSPORT_STATUS,
    status_for_failure,
)

#: Server-side logging only. A request never carries a log line back, and CAD
#: documents are never logged: a document is user content, and logging it by
#: default would put a whole design in a server log for no reason.
logger = logging.getLogger("cad_api")

#: The stable error for an unexpected internal failure. Built from the
#: transport contract's own error type, so a 500 says the same kind of thing
#: as every other failure -- and carries no traceback, no exception text and
#: no path.
INTERNAL_ERROR = ErrorContract(
    failure=ServiceFailure.INTERNAL_ERROR.value,
    message="the server failed to handle the request",
)

#: The routes, so a failure can be answered in the shape that route's success
#: would have had.
VALIDATE_PATH = "/validate"
BUILD_PATH = "/build"
HEALTH_PATH = "/health"

#: The title FastAPI puts in the generated OpenAPI document.
API_TITLE = "Text-to-CAD build API"


def create_app(
    config: Optional[ApiConfig] = None,
    *,
    service: Optional[CadApplicationService] = None,
) -> FastAPI:
    """Build the ASGI application.

    Exactly one of ``config`` or ``service`` is given. The application service
    -- and therefore its cache and its executor -- is constructed **once**,
    here at startup, and held on ``app.state``; no request builds a service, a
    cache or a path.

    Args:
        config: Where to cache builds, and the build timeout.
        service: An already-configured service, for a caller that wants to
            wire one itself. Used by the tests to prove the route talks to the
            service and nothing else.
    """
    if (config is None) == (service is None):
        raise ValueError("create_app takes exactly one of config or service")
    if service is None:
        assert config is not None
        service = CadApplicationService.local(
            config.cache_root, timeout_seconds=config.timeout_seconds
        )

    app = FastAPI(
        title=API_TITLE,
        description=(
            "A thin HTTP transport over the CAD application service. The "
            "transport-neutral contract in cad_core.api_contract is the "
            "conceptual contract; this generated schema is not."
        ),
        # No response models are declared: the response shape is the
        # transport-neutral contract's, and duplicating it in Pydantic would
        # create a second definition of it.
    )
    app.state.service = service
    app.state.contract = CadApiContract(service)

    @app.exception_handler(RequestValidationError)
    async def _malformed_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Answer a rejected envelope in the contract's own error shape.

        FastAPI's default body is its own ``detail`` list; this maps it onto
        the transport contract so a client sees one error shape everywhere.
        Pydantic's messages describe the request's *structure* -- a missing
        field, a wrong type -- and carry nothing about the server.
        """
        return JSONResponse(
            status_code=TRANSPORT_STATUS,
            content=_failure_body(
                request,
                ErrorContract(
                    failure=ServiceFailure.INVALID_REQUEST.value,
                    message=_transport_message(exc),
                ),
            ),
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Log an unexpected failure internally, answer with a stable body.

        The traceback goes to the server's log and **never** to the client.
        """
        logger.exception(
            "unhandled error handling %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=INTERNAL_STATUS,
            content=_failure_body(request, INTERNAL_ERROR),
        )

    @app.get(HEALTH_PATH)
    async def health() -> Dict[str, str]:
        """Whether the process is up. Nothing more.

        No CAD execution, no cache inspection, no worker, no external call,
        and no version metadata -- there is no application version contract to
        report.
        """
        return {"status": "ok"}

    @app.post(VALIDATE_PATH)
    async def validate_document(
        body: ValidateBody, contract: CadApiContract = Depends(_contract)
    ) -> JSONResponse:
        """Validate a CAD document.

        Always **200** for a well-formed request, including when the answer is
        ``"valid": false``: the question was asked and answered, and the
        answer is the resource. Only a malformed *request* is a 4xx here.
        """
        return JSONResponse(
            status_code=OK_STATUS,
            content=contract.validate_document_payload(body.to_payload()),
        )

    @app.post(BUILD_PATH)
    async def build_document(
        body: BuildBody, contract: CadApiContract = Depends(_contract)
    ) -> JSONResponse:
        """Build a CAD document.

        **200** when the build succeeded. Otherwise the status comes from the
        contract's ``failure`` value alone -- see :mod:`cad_api.status`.
        """
        payload = contract.build_document_payload(body.to_payload())
        return JSONResponse(status_code=_status_of(payload), content=payload)

    return app


def app_from_environment() -> FastAPI:
    """The application configured from the environment, for an ASGI server.

    ``CAD_API_CACHE_ROOT`` must be set; nothing is guessed. Kept as a function
    rather than a module-level object so importing this module never depends
    on the environment -- a server is pointed at ``cad_api.app:app_from_environment``
    with its factory flag.
    """
    from cad_api.config import config_from_environment

    return create_app(config_from_environment())


# --- internals -------------------------------------------------------------


async def _contract(request: Request) -> CadApiContract:
    """The application's one contract object, built at startup."""
    return request.app.state.contract


def _failure_body(request: Request, error: ErrorContract) -> Dict[str, Any]:
    """A failure body in the shape that route's success would have had.

    So every ``/build`` response has one top-level shape and ``error.failure``
    is always in the same place, whether the request was rejected by the
    transport or by the application service. Composed from the transport
    contract's own response types -- no JSON is invented here.
    """
    path = request.scope.get("path", "")
    if path == BUILD_PATH:
        return BuildDocumentResponse(
            status="failed", succeeded=False, error=error
        ).to_payload()
    if path == VALIDATE_PATH:
        return ValidateDocumentResponse(valid=False, error=error).to_payload()
    return {"error": error.to_payload()}


def _status_of(payload: Dict[str, Any]) -> int:
    """The HTTP status for a build response payload.

    Reads only the contract's own ``succeeded`` flag and ``failure`` value. No
    exception class, no internal field and no guess.
    """
    if payload.get("succeeded"):
        return OK_STATUS
    error = payload.get("error") or {}
    return status_for_failure(error.get("failure", ""))


def _transport_message(exc: RequestValidationError) -> str:
    """A short description of why the envelope was rejected.

    Built from Pydantic's own error type and location, which describe the
    request's structure. The raw exception string is not used, and no
    traceback is involved.
    """
    problems = []
    for error in exc.errors()[:4]:
        location = ".".join(
            str(part) for part in error.get("loc", ()) if part != "body"
        )
        kind = error.get("type", "invalid")
        problems.append(f"{location or 'body'}: {kind}")
    if not problems:  # pragma: no cover - errors() is never empty
        return "the request body is not a valid request"
    return "the request body is not a valid request (" + "; ".join(problems) + ")"
