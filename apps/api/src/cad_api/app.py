"""The ASGI application: seven routes, and no CAD logic.

```
POST /validate                     ->  ValidateBody  ->  CadApiContract.validate_document_payload
POST /build                        ->  BuildBody     ->  CadApiContract.build_document_payload
POST /generate                     ->  GenerateBody  ->  TextGenerator.generate
GET  /builds/{build_key}           ->  BuildRetriever.retrieve         ->  the published result
GET  /builds/{build_key}/render    ->  BuildRetriever.retrieve_render  ->  canonical render JSON
GET  /artifacts/{id}               ->  ArtifactResolver.resolve        ->  verified bytes
GET  /health                       ->  {"status": "ok"}
```

``POST /generate`` is the natural-language entry point, and it is a *sibling*
of ``POST /build`` rather than a replacement for it: it returns a validated
CAD document and builds nothing, so the client sends that document to
``POST /build`` exactly as it would one a human wrote. There is one build
path, one validator and one CAD contract; the AI adds an interpreter in front
of them and changes none of them.

The two JSON routes each do four things: let Pydantic check the envelope,
hand the payload to the transport-neutral contract, choose a status from the
contract's ``failure`` value, and return the payload as JSON.

The build-retrieval route does three things: hand the key to
:class:`~cad_api.builds.BuildRetriever`, choose a status from the retriever's
``reason``, and render the result with the **same** transport-contract mapping
the build response uses. It is read-only: nothing is built, nothing is
written.

The render route does the same three things as the retrieval route, narrowed
to one artifact: it hands the key to
:meth:`~cad_api.builds.BuildRetriever.retrieve_render` and returns the bytes
it was given as JSON, with the render artifact's own SHA-256 as the entity
tag. It defines no format -- the bytes are the render model's existing
canonical serialization -- and takes no parameter but the build key, so it is
not a general JSON or file endpoint. Read-only too.

The artifact route does four things: hand the id to
:class:`~cad_api.artifacts.ArtifactResolver`, choose a status from the
resolver's ``reason``, set the headers, return the bytes it was given. It
**never** touches a path, a directory, a manifest or a checksum -- the
resolver (``docs/artifact-delivery.md``) owns all of that, and tests assert
this module names none of those operations.

There is no validation rule, no geometry call, no exporter call, no cache call
and no process management in this module -- tests assert it imports none of
those modules and calls none of their names.

**Not exposed**, and not implemented: editing, deleting, versioning or
comparing documents; document retrieval by hash (there is no document store);
cache or entry browsing; manifest download; FeatureScript; filesystem
browsing; arbitrary file download; B-rep bytes in any form; the render model
as a *download* (``/artifacts/{id}`` still refuses it -- the render route
above returns it as JSON instead); job control; authentication; anything
else.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from cad_core.api_contract import (
    BuildDocumentResponse,
    CadApiContract,
    ErrorContract,
    ValidateDocumentResponse,
    build_response,
)
from cad_core.application_service import CadApplicationService, ServiceFailure

from cad_api.artifacts import (
    NOT_FOUND_MESSAGE,
    ArtifactResolver,
    DeliveredArtifact,
    DeliveryProblem,
    DeliveryReason,
)
from cad_api.builds import (
    NOT_FOUND_MESSAGE as BUILD_NOT_FOUND_MESSAGE,
    BuildRetriever,
    RetrievalProblem,
    RetrievalReason,
)
from cad_api.config import ApiConfig
from cad_api.generation import TextGenerator
from cad_api.schemas import BuildBody, GenerateBody, ValidateBody
from cad_api.status import (
    INTERNAL_STATUS,
    NOT_FOUND_STATUS,
    OK_STATUS,
    TRANSPORT_STATUS,
    status_for_delivery,
    status_for_failure,
    status_for_outcome,
    status_for_retrieval,
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
GENERATE_PATH = "/generate"
HEALTH_PATH = "/health"
ARTIFACTS_PREFIX = "/artifacts/"
ARTIFACT_PATH = ARTIFACTS_PREFIX + "{artifact_id}"
BUILDS_PREFIX = "/builds/"
BUILD_LOOKUP_PATH = BUILDS_PREFIX + "{build_key}"
BUILD_RENDER_PATH = BUILD_LOOKUP_PATH + "/render"

#: The media type of the render model: it is JSON, and the bytes are the
#: render model's own canonical serialization.
RENDER_CONTENT_TYPE = "application/json"

#: The error for a retrieval this layer did not expect.
RETRIEVAL_FAILED = RetrievalProblem(
    reason=RetrievalReason.RETRIEVAL_FAILED,
    message="the build could not be retrieved",
)

#: The error for a delivery failure this layer did not expect. Its own small
#: shape, deliberately: a download is not a build, so it carries a delivery
#: ``reason`` rather than the build taxonomy's ``failure``.
DELIVERY_FAILED = DeliveryProblem(
    reason=DeliveryReason.DELIVERY_FAILED,
    message="the artifact could not be delivered",
)

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
    # The resolver is given the one cache the service was configured with, so
    # artifact delivery and building see the same entries. A service whose
    # backend exposes no cache delivers nothing rather than guessing at one.
    app.state.resolver = ArtifactResolver(
        getattr(getattr(service, "backend", None), "cache", None)
    )
    app.state.retriever = BuildRetriever(service)
    # The AI entry point shares the one application service, so a generated
    # document is validated by exactly the validator every other route uses.
    # Its provider is built on first use, not here: the server must start, and
    # every CAD route must work, with no credential configured.
    app.state.generator = TextGenerator(service)

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

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        """Answer an artifact URL that matched no route in the delivery shape.

        An id containing a URL path separator never reaches the route -- it
        matches nothing, and the framework answers 404. That is already safe,
        but its body is the framework's ``detail`` shape, so for this one
        prefix it is replaced by the stable delivery error. Every other path
        keeps FastAPI's own behaviour.
        """
        path = request.scope.get("path", "")
        # Only a *not found* is rewritten. A wrong method still answers 405,
        # because turning that into "not available" would misreport it: HEAD
        # is not offered on this route (FastAPI adds no HEAD of its own, and
        # this stage adds no extra route), and saying so is more useful than
        # pretending the artifact is missing.
        if exc.status_code == NOT_FOUND_STATUS:
            if path.startswith(ARTIFACTS_PREFIX) or path == ARTIFACTS_PREFIX.rstrip(
                "/"
            ):
                return JSONResponse(
                    status_code=status_for_delivery(
                        DeliveryReason.ARTIFACT_NOT_FOUND
                    ),
                    content={
                        "error": {
                            "reason": DeliveryReason.ARTIFACT_NOT_FOUND.value,
                            "message": NOT_FOUND_MESSAGE,
                        }
                    },
                )
            if path.startswith(BUILDS_PREFIX) or path == BUILDS_PREFIX.rstrip("/"):
                return JSONResponse(
                    status_code=status_for_retrieval(
                        RetrievalReason.BUILD_NOT_FOUND
                    ),
                    content={
                        "error": {
                            "reason": RetrievalReason.BUILD_NOT_FOUND.value,
                            "message": BUILD_NOT_FOUND_MESSAGE,
                        }
                    },
                )
        return await http_exception_handler(request, exc)

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

    @app.get(BUILD_LOOKUP_PATH)
    async def get_build(
        build_key: str, retriever: BuildRetriever = Depends(_retriever)
    ) -> JSONResponse:
        """Retrieve a build that has already been published.

        Read-only: **no geometry runs, no exporter runs, no child process
        starts and nothing is written.** The response is the same
        transport-contract build response ``POST /build`` returns for that
        same build, with ``cache_hit`` true and ``execution_id`` null --
        a retrieval is not an execution.

        **200** with the result, **400** for a key that is not a build key,
        **404** for a build that is not available, **500** if retrieval
        itself failed.
        """
        outcome = retriever.retrieve(build_key)
        if isinstance(outcome, RetrievalProblem):
            return JSONResponse(
                status_code=status_for_retrieval(outcome.reason),
                content={"error": dict(outcome.to_payload())},
            )
        return JSONResponse(
            status_code=OK_STATUS, content=build_response(outcome).to_payload()
        )

    @app.get(BUILD_RENDER_PATH)
    async def get_build_render(
        build_key: str, retriever: BuildRetriever = Depends(_retriever)
    ) -> Response:
        """Deliver a build's render model, for a viewer to draw.

        Narrowly scoped to **a build key and its render artifact**: there is
        no way to ask for another artifact, another file or arbitrary JSON.
        Read-only, like every other GET here -- nothing is built and nothing
        is written.

        The body is the render model's **existing canonical JSON** (Stage 17's
        ``canonical_render_bytes``), so no second serialization exists. The
        entity tag is the render artifact's own SHA-256.

        **200** with the model, **400** for a key that is not a build key,
        **404** for a build that is not available or that holds no render
        output, **500** if retrieval failed.
        """
        outcome = retriever.retrieve_render(build_key)
        if isinstance(outcome, RetrievalProblem):
            return JSONResponse(
                status_code=status_for_retrieval(outcome.reason),
                content={"error": dict(outcome.to_payload())},
            )
        headers = {"content-length": str(outcome.size_bytes)}
        if outcome.etag is not None:
            headers["etag"] = outcome.etag
        return Response(
            content=outcome.content,
            media_type=RENDER_CONTENT_TYPE,
            headers=headers,
        )

    @app.get(ARTIFACT_PATH)
    async def download_artifact(
        artifact_id: str, resolver: ArtifactResolver = Depends(_resolver)
    ) -> Response:
        """Deliver a file-backed artifact's bytes.

        The path parameter is a **logical artifact id** and nothing else. It
        is handed to the resolver as text; this route builds no path, opens no
        file and computes no checksum.

        **200** with the bytes, **400** for an id that is not an identifier,
        **404** for an artifact that is not available or has no bytes to
        deliver, **500** if delivery itself failed.
        """
        outcome = resolver.resolve(artifact_id)
        if isinstance(outcome, DeliveryProblem):
            return JSONResponse(
                status_code=status_for_delivery(outcome.reason),
                content={"error": dict(outcome.to_payload())},
            )
        return _artifact_response(outcome)

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

    @app.post(GENERATE_PATH)
    async def generate_document(
        body: GenerateBody, generator: TextGenerator = Depends(_generator)
    ) -> JSONResponse:
        """Interpret a natural-language description into a CAD document.

        **Generation only.** No geometry runs here: the answer carries a
        document the validator already accepted, and the client sends that
        document to ``POST /build`` like any other. One model call, one
        attempt, no retry and no repair.

        **200** for all three *answers* -- a document, a clarification
        question, or a refusal -- because each is the service doing its job;
        **502** when the model's answer was not a valid CAD document;
        **503** when no model could be reached. See :mod:`cad_api.status`.

        The body is the AI layer's own result payload, which excludes its
        development-only diagnostic. No credential, header, provider message,
        traceback or path can reach a client through it.
        """
        result = generator.generate(body.text)
        _log_generation(result)
        return JSONResponse(
            status_code=status_for_outcome(result.outcome), content=result.to_dict()
        )

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


async def _resolver(request: Request) -> ArtifactResolver:
    """The application's one artifact resolver, built at startup."""
    return request.app.state.resolver


async def _retriever(request: Request) -> BuildRetriever:
    """The application's one build retriever, built at startup."""
    return request.app.state.retriever


async def _generator(request: Request) -> TextGenerator:
    """The application's one text-to-CAD entry point, built at startup."""
    return request.app.state.generator


def _artifact_response(artifact: DeliveredArtifact) -> Response:
    """The bytes the resolver verified, with safe headers.

    Every header value comes from the resolver's trusted metadata: the
    filename is ``<build key><extension>`` from the validated manifest record,
    never from the URL; the length is the real length of these bytes; and the
    entity tag is the artifact's own SHA-256, not a second hash.
    """
    return Response(
        content=artifact.content,
        media_type=artifact.content_type,
        headers={
            "content-disposition": f'attachment; filename="{artifact.filename}"',
            "content-length": str(artifact.size_bytes),
            "etag": artifact.etag,
        },
    )


def _log_generation(result: Any) -> None:
    """Record that a generation failed, server-side and never to the client.

    **Only this application's own closed vocabularies are logged**: the
    outcome, the vendor-neutral error kind, and any specification rule codes
    the validator produced. Each names a class of failure, and every possible
    value is a constant defined in this repository.

    Three things are deliberately *not* logged, at any level:

    * **the AI layer's ``detail``** -- it may quote the provider's own
      exception text, and a provider diagnostic is exactly where a credential
      or an authorization header turns up. A log file is a place secrets go to
      be forgotten about; the safe amount to write there is none. A test
      asserts this by failing the model with a key-shaped diagnostic and
      grepping the log for it.
    * **the description** -- user content, for the same reason the CAD routes
      never log a document.
    * **the generated document** -- a whole design in a server log, for
      nothing.

    A successful generation logs nothing at all.
    """
    outcome = getattr(result, "outcome", None)
    if outcome is None or getattr(outcome, "value", "") == "generated":
        return
    kind = getattr(result, "error_kind", None)
    logger.warning(
        "generation did not produce a document: outcome=%s kind=%s rules=%s",
        getattr(outcome, "value", "unknown"),
        getattr(kind, "value", None),
        ",".join(getattr(result, "rule_codes", ()) or ()) or "-",
    )


def _failure_body(request: Request, error: ErrorContract) -> Dict[str, Any]:
    """A failure body in the shape that route's success would have had.

    So every ``/build`` response has one top-level shape and ``error.failure``
    is always in the same place, whether the request was rejected by the
    transport or by the application service. Composed from the transport
    contract's own response types -- no JSON is invented here.
    """
    path = request.scope.get("path", "")
    if path.startswith(ARTIFACTS_PREFIX):
        return {"error": dict(DELIVERY_FAILED.to_payload())}
    if path.startswith(BUILDS_PREFIX):
        return {"error": dict(RETRIEVAL_FAILED.to_payload())}
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
