"""The transport-neutral external contract over the application service.

```
future transport      HTTP · CLI · UI · AI orchestration
        │
Transport contract    cad_core.api_contract          <- this module
        │
Application Service   cad_core.application_service
        │
Build / Cache / Isolation
        │
CAD engine / export / render
```

Four boundaries, four different things, and this stage adds the third:

===================  =======================================================
CAD specification    what a part *is* (``docs/cad-specification.md``)
application service  what an operation *does* (``docs/application-service.md``)
transport contract   what crosses to a client -- **this module**
HTTP                 how it crosses. **Does not exist yet.**
===================  =======================================================

**No transport is implemented here.** There is no HTTP, no REST, no GraphQL,
no WebSocket, no socket, no URL and no endpoint. What this module defines is
the data that will cross such a boundary once one exists, so the eventual
transport is a thin adapter with nothing to decide.

No second implementation
------------------------
Every operation delegates to :class:`~cad_core.application_service.CadApplicationService`.
This module maps data and nothing else: it validates no document, builds no
geometry, computes no hash and holds no CAD semantics. The build request DTO
*is* the application service's, aliased rather than copied, and the artifact
kinds, build statuses and failure taxonomy are the existing ones.

What deliberately does not cross
--------------------------------
A response carries only information that is safe for a client:

* **no filesystem path** -- not an artifact's, not a workspace's, not the
  cache's. An artifact's stable handle is its ``logical_id``;
* **no traceback, exception object or ``stderr``**;
* **no environment information**;
* **no temporary workspace name**;
* **no process detail** -- a client learns that execution failed, not that a
  child process was killed at a timeout;
* **no kernel object**, and no B-rep in any form;
* **no render payload** -- the render artifact is described, not embedded.

Diagnostics stay internal. The application service already keeps them off its
public error message, and this layer drops the remaining internal fields.

Serialization
-------------
Requests and responses map to **JSON-compatible Python structures**, not to
canonical bytes. There is exactly one canonical byte form in this system --
the CAD document's (Stage 15) -- and it stays where it is: a document travels
inside a payload untouched, and only :meth:`CadApiContract.validate_document`
returns its canonical form, because that is the application service's answer,
not this layer's doing. See ``docs/api-contract.md``.

Versioning
----------
**No API version is introduced.** There is no wire, no deployed client and no
compatibility window to manage, so a version number now would be one nobody
could honour; the contract is a Python module versioned in source control. The
four version numbers that *do* exist -- the CAD document's ``schema_version``,
the render model's ``format_version``, the cache's ``cache_schema_version``
and the worker's ``ipc_protocol_version`` -- are unrelated to each other and
to this, and none of them appears in a payload.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from cad_core.application_service import (
    OUTPUT_NAMES,
    BuildDocumentRequest as _BuildDocumentRequest,
    BuildOutcome,
    CadApplicationService,
    Document,
    DocumentValidation,
    ServiceError,
    ServiceFailure,
)
from cad_core.artifact_registry import CHECKSUM_ALGORITHM, Artifact
from cad_core.build_job import BuildStatus

#: The build request DTO **is** the application service's: a document and
#: output names, and nothing else -- exactly what an external build request
#: needs. Aliased rather than copied, the way ``BuildOutput`` aliases
#: ``ArtifactKind``, so the two cannot drift.
BuildDocumentRequest = _BuildDocumentRequest

#: The output names a client may ask for: the existing artifact kinds' values.
#: No parallel enumeration exists.
OUTPUTS: Tuple[str, ...] = OUTPUT_NAMES

#: The statuses a build response can carry, from the existing
#: :class:`~cad_core.build_job.BuildStatus`. A call is synchronous, so these
#: are the only two, and **a cache hit is not a status** -- it is
#: :attr:`BuildDocumentResponse.cache_hit`.
STATUSES: Tuple[str, ...] = (BuildStatus.SUCCEEDED.value, BuildStatus.FAILED.value)

#: The failure classifications a client can receive: the existing application
#: taxonomy, unflattened.
FAILURES: Tuple[str, ...] = tuple(failure.value for failure in ServiceFailure)

#: Fields a validate-document payload carries. Exactly these.
VALIDATE_REQUEST_FIELDS: Tuple[str, ...] = ("document",)

#: Fields a build-document payload carries. Exactly these -- no user id, no
#: tenant id, no auth data, no header, no filesystem path, no LLM setting, no
#: UI setting and no backend selection.
BUILD_REQUEST_FIELDS: Tuple[str, ...] = ("document", "outputs")

#: Client-facing messages for the failures whose internal message names
#: mechanics a client must not be told about.
#:
#: The application service's public message is safe -- no traceback, no path,
#: nothing from the environment -- but for these two it still describes *how*
#: the system works: "the isolated CAD process ended without a result (exit
#: code 0)", "exceeded its 0.4 second timeout and was terminated", "the build
#: cache could not be used (CacheError)". An exit code, a killed child and an
#: exception class name are **process details**, and a client can act on none
#: of them. So exactly these two classifications carry a fixed sentence
#: instead, and the underlying message stays available internally for
#: diagnosis.
#:
#: Every other failure keeps the service's own message verbatim: a rule
#: violation, a geometric failure and an exporter's refusal all describe the
#: *request*, which is what a client needs.
SUBSTITUTED_MESSAGES: Mapping[str, str] = {
    ServiceFailure.EXECUTION_FAILED.value: (
        "the build could not be executed; no result was produced"
    ),
    ServiceFailure.INTERNAL_ERROR.value: (
        "the build failed with an internal error"
    ),
}


class ContractError(Exception):
    """A payload is not a payload of this contract.

    Raised by the ``*_from_payload`` functions for an envelope that is the
    wrong shape: not a mapping, a missing or unknown field, a wrong type.
    **Not** for content the application service judges -- an unknown output
    name and an invalid CAD document are ordinary responses carrying
    :attr:`ServiceFailure.INVALID_REQUEST` and
    :attr:`ServiceFailure.INVALID_DOCUMENT`.

    So the split is: this layer checks the *envelope*, the service checks the
    *content*. :meth:`CadApiContract.build_document_payload` catches this and
    returns an ``invalid_request`` response, so a transport that works at the
    payload level never has to.
    """


@dataclass(frozen=True)
class ValidateDocumentRequest:
    """Ask whether a CAD document is acceptable.

    One field, because that is the whole question. The document is a canonical
    CAD document as a parsed JSON structure or as JSON text -- the application
    service's own input boundary, unchanged.
    """

    document: Document


@dataclass(frozen=True)
class ValidationErrorContract:
    """One structured validation error, as the validator reported it.

    The V1 rule code, a human-readable message, and where in the document the
    problem is. Nothing is added and nothing is dropped: these are the
    validator's own five fields.
    """

    rule: str
    message: str
    feature_id: Optional[str] = None
    field_path: Optional[str] = None
    feature_index: Optional[int] = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "message": self.message,
            "feature_id": self.feature_id,
            "field_path": self.field_path,
            "feature_index": self.feature_index,
        }


@dataclass(frozen=True)
class ErrorContract:
    """Why an operation failed, as a client sees it.

    :attr:`failure` is the stable classification -- one of :data:`FAILURES` --
    and it is what a client should branch on. The six distinctions the
    application layer established survive intact: a malformed document, an
    invalid document, an invalid request, a geometry failure, an output
    failure and an execution failure are six different values, never one
    message.

    :attr:`message` is the application service's **stable public sentence**:
    no traceback, no filesystem path, nothing from the environment, and never
    a child process's ``stderr``.

    Deliberately absent, and documented as such:

    * the internal ``stage``, and the ``execution_outcome`` behind an
      execution failure. That a build was killed at a timeout rather than
      crashing is a **process detail**; a client learns that execution failed;
    * the internal ``build_failure``, which :attr:`failure` already
      classifies -- keeping it would be duplicating ``BuildError`` internals
      for no gain;
    * every diagnostic field.
    """

    failure: str
    message: str

    #: Which output could not be produced, for an output failure.
    output: Optional[str] = None

    #: Specification rule codes (S1-S20 for a document, E1-E5 for geometry).
    rule_codes: Tuple[str, ...] = ()

    #: The validator's structured errors, for an invalid document.
    validation_errors: Tuple[ValidationErrorContract, ...] = ()

    def to_payload(self) -> Dict[str, Any]:
        return {
            "failure": self.failure,
            "message": self.message,
            "output": self.output,
            "rule_codes": list(self.rule_codes),
            "validation_errors": [
                error.to_payload() for error in self.validation_errors
            ],
        }


@dataclass(frozen=True)
class ArtifactContract:
    """One derived artifact, as a client sees it.

    Identity, format and content facts. **Never a path**: an artifact's stable
    transport-neutral handle is its :attr:`logical_id`
    (``<build key>:<kind>``), which is reproducible on any machine and is
    already the artifact layer's identity. No URL is invented -- a future
    transport will decide how bytes are fetched, and the logical id is the
    reference it will resolve.

    :attr:`storage` says whether bytes exist at all: ``file`` for STEP, IGES
    and STL, ``in_memory`` for the render model and the B-rep.

    The produced file's extension is **not** exposed: it is a local storage
    detail, and :attr:`format` already names the logical format.
    """

    kind: str
    format: str
    logical_id: str
    storage: str
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    checksum_algorithm: Optional[str] = None

    #: The artifact layer's own deterministic measurements -- solid count,
    #: volume and bounding box for geometry; triangle count for STL; counts,
    #: bounds and tessellation for the render model. Plain JSON data, never a
    #: payload and never a kernel object.
    measurements: Mapping[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "format": self.format,
            "logical_id": self.logical_id,
            "storage": self.storage,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum,
            "checksum_algorithm": self.checksum_algorithm,
            "measurements": dict(self.measurements),
        }


@dataclass(frozen=True)
class ValidateDocumentResponse:
    """Whether a CAD document is acceptable, and what it is.

    :attr:`valid` is the explicit success indicator -- a validation response
    has no build status, because nothing was built.

    On success :attr:`document` is the **canonical** form of what was
    submitted (Stage 15): a terse document and a fully materialised one share
    a hash, but only one is canonical, and returning it lets a client see
    exactly what was understood.
    """

    valid: bool
    document_hash: Optional[str] = None
    name: Optional[str] = None
    feature_count: Optional[int] = None
    document: Optional[Mapping[str, Any]] = None
    error: Optional[ErrorContract] = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "document_hash": self.document_hash,
            "name": self.name,
            "feature_count": self.feature_count,
            "document": dict(self.document) if self.document is not None else None,
            "error": self.error.to_payload() if self.error is not None else None,
        }


@dataclass(frozen=True)
class BuildDocumentResponse:
    """The outcome of a build, as a client sees it.

    Identity is borrowed, never invented:

    ==================  ====================================================
    :attr:`document_hash`  identifies the **design** -- Stage 15's canonical
                           hash of the CAD document
    :attr:`build_key`      identifies the **build** -- the document hash plus
                           the canonical output selection. Reproducible
                           anywhere
    :attr:`execution_id`   identifies **one run**, and no build. ``None`` on a
                           cache hit, because nothing ran
    ==================  ====================================================

    :attr:`cache_hit` says the artifacts came from the cache. It is **not** a
    status: :attr:`status` is ``succeeded`` either way.
    """

    status: str
    succeeded: bool
    document_hash: Optional[str] = None
    build_key: Optional[str] = None
    execution_id: Optional[str] = None
    cache_hit: bool = False
    outputs: Tuple[str, ...] = ()
    artifacts: Tuple[ArtifactContract, ...] = ()
    error: Optional[ErrorContract] = None

    def artifact(self, kind: str) -> Optional[ArtifactContract]:
        for artifact in self.artifacts:
            if artifact.kind == kind:
                return artifact
        return None

    def to_payload(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "succeeded": self.succeeded,
            "document_hash": self.document_hash,
            "build_key": self.build_key,
            "execution_id": self.execution_id,
            "cache_hit": self.cache_hit,
            "outputs": list(self.outputs),
            "artifacts": [artifact.to_payload() for artifact in self.artifacts],
            "error": self.error.to_payload() if self.error is not None else None,
        }


# --- requests: payload <-> DTO ---------------------------------------------


def validate_request_from_payload(
    payload: Mapping[str, Any]
) -> ValidateDocumentRequest:
    """Read a validate-document payload.

    ``{"document": {...}}``, and nothing else. Strict: an unknown or missing
    field raises :class:`ContractError` rather than being ignored.
    """
    _require_fields(payload, VALIDATE_REQUEST_FIELDS, "validate")
    return ValidateDocumentRequest(document=_document_of(payload["document"]))


def validate_request_to_payload(
    request: ValidateDocumentRequest,
) -> Dict[str, Any]:
    """Write a validate-document payload.

    The document travels **exactly as supplied**: this layer does not
    canonicalize it, because Stage 15 owns that and a second canonical form is
    precisely what must not exist.
    """
    if not isinstance(request, ValidateDocumentRequest):
        raise ContractError(
            f"expected a ValidateDocumentRequest; got {type(request).__name__}"
        )
    return {"document": _copy_document(request.document)}


def build_request_from_payload(
    payload: Mapping[str, Any]
) -> BuildDocumentRequest:
    """Read a build-document payload.

    ``{"document": {...}, "outputs": ["geometry", "step"]}``. ``outputs`` may
    be omitted, which means all of them.

    Strict about the **envelope** only: a missing ``document``, an unknown
    field or a non-list ``outputs`` raises :class:`ContractError`. The output
    *names* are not judged here -- the application service classifies an
    unknown one as ``invalid_request``, so there is one place that decides.
    """
    _require_fields(payload, BUILD_REQUEST_FIELDS, "build", optional=("outputs",))
    outputs = payload.get("outputs", OUTPUTS)
    if not isinstance(outputs, (list, tuple)):
        raise ContractError(
            "'outputs' is a list of output names; choose from "
            + ", ".join(OUTPUTS)
        )
    return BuildDocumentRequest(
        document=_document_of(payload["document"]), outputs=tuple(outputs)
    )


def build_request_to_payload(request: BuildDocumentRequest) -> Dict[str, Any]:
    """Write a build-document payload. The document is not canonicalized."""
    if not isinstance(request, BuildDocumentRequest):
        raise ContractError(
            f"expected a BuildDocumentRequest; got {type(request).__name__}"
        )
    return {
        "document": _copy_document(request.document),
        "outputs": list(request.outputs),
    }


# --- responses: application result -> DTO ----------------------------------


def validation_response(result: DocumentValidation) -> ValidateDocumentResponse:
    """Map a :class:`DocumentValidation` to its external response.

    Drops the typed ``Part`` -- a domain object never crosses -- and keeps
    everything a client needs to know what was understood or what was wrong.
    """
    if not isinstance(result, DocumentValidation):
        raise ContractError(
            f"expected a DocumentValidation; got {type(result).__name__}"
        )
    return ValidateDocumentResponse(
        valid=result.valid,
        document_hash=result.document_hash,
        name=result.name,
        feature_count=result.feature_count,
        document=(
            dict(result.document) if result.document is not None else None
        ),
        error=error_contract(result.error) if result.error is not None else None,
    )


def build_response(outcome: BuildOutcome) -> BuildDocumentResponse:
    """Map a :class:`BuildOutcome` to its external response.

    Drops the ``RenderModel`` reference and the ``cache_published`` flag: the
    render artifact is described rather than embedded, and whether this call
    happened to fill the cache is cache mechanics, not the client's business.
    """
    if not isinstance(outcome, BuildOutcome):
        raise ContractError(
            f"expected a BuildOutcome; got {type(outcome).__name__}"
        )
    artifacts = (
        tuple(
            artifact_contract(artifact)
            for artifact in outcome.manifest.artifacts
        )
        if outcome.manifest is not None
        else ()
    )
    return BuildDocumentResponse(
        status=outcome.status.value,
        succeeded=outcome.succeeded,
        document_hash=outcome.document_hash,
        build_key=outcome.build_key,
        execution_id=outcome.execution_id,
        cache_hit=outcome.cache_hit,
        outputs=tuple(artifact.kind for artifact in artifacts),
        artifacts=artifacts,
        error=(
            error_contract(outcome.error) if outcome.error is not None else None
        ),
    )


def artifact_contract(artifact: Artifact) -> ArtifactContract:
    """Map an :class:`~cad_core.artifact_registry.Artifact` to its external form.

    The path is dropped. The logical id -- ``<build key>:<kind>`` -- is the
    handle that crosses, because it is reproducible and machine-independent
    while a path is neither.
    """
    if not isinstance(artifact, Artifact):
        raise ContractError(
            f"expected an Artifact; got {type(artifact).__name__}"
        )
    return ArtifactContract(
        kind=artifact.kind.value,
        format=artifact.format,
        logical_id=artifact.logical_id,
        storage=artifact.storage.value,
        size_bytes=artifact.size_bytes,
        checksum=artifact.checksum,
        checksum_algorithm=(
            CHECKSUM_ALGORITHM if artifact.checksum is not None else None
        ),
        measurements=dict(artifact.details),
    )


def error_contract(error: ServiceError) -> ErrorContract:
    """Map a :class:`ServiceError` to its external form.

    Keeps the stable classification, the failing output and the specification
    rule codes. Drops the internal ``stage``, the ``build_failure`` that
    :attr:`ErrorContract.failure` already classifies, and the
    ``execution_outcome`` that would tell a client how the process died.

    The message is the service's own, except for the two classifications in
    :data:`SUBSTITUTED_MESSAGES`, whose internal wording names an exit code, a
    killed process or an exception class.
    """
    if not isinstance(error, ServiceError):
        raise ContractError(
            f"expected a ServiceError; got {type(error).__name__}"
        )
    failure = error.failure.value
    return ErrorContract(
        failure=failure,
        message=SUBSTITUTED_MESSAGES.get(failure, error.message),
        output=error.output.value if error.output is not None else None,
        rule_codes=tuple(error.rule_codes),
        validation_errors=tuple(
            ValidationErrorContract(
                rule=str(item.get("rule", "")),
                message=str(item.get("message", "")),
                feature_id=item.get("feature_id"),
                field_path=item.get("field_path"),
                feature_index=item.get("feature_index"),
            )
            for item in error.validation_errors
        ),
    )


def request_error_response(exc: ContractError) -> BuildDocumentResponse:
    """The response for a payload that was not a payload of this contract."""
    return BuildDocumentResponse(
        status=BuildStatus.FAILED.value,
        succeeded=False,
        error=ErrorContract(
            failure=ServiceFailure.INVALID_REQUEST.value, message=str(exc)
        ),
    )


# --- the boundary object ---------------------------------------------------


class CadApiContract:
    """The transport-neutral boundary a future transport will hold.

    Two operations, each of which calls the application service and maps the
    result. **No business logic:** no validation, no geometry, no hashing, no
    caching decision -- every one of those belongs to a layer below, and a
    test asserts this module imports neither the validator nor the kernel.

    ```python
    contract = CadApiContract(CadApplicationService.local(cache_root))
    payload  = contract.build_document_payload({"document": {...},
                                                "outputs": ["step"]})
    ```

    The ``*_payload`` methods are the complete payload-in, payload-out
    operations: they are what a transport calls, and they turn a
    :class:`ContractError` into an ``invalid_request`` response so the
    transport never has to catch anything.
    """

    def __init__(self, service: CadApplicationService) -> None:
        if not isinstance(service, CadApplicationService):
            raise ContractError(
                "expected a CadApplicationService; got "
                f"{type(service).__name__}"
            )
        self._service = service

    @property
    def service(self) -> CadApplicationService:
        return self._service

    # --- typed operations -------------------------------------------------

    def validate_document(
        self, request: ValidateDocumentRequest
    ) -> ValidateDocumentResponse:
        """Validate a document and return its external response."""
        if not isinstance(request, ValidateDocumentRequest):
            raise ContractError(
                "expected a ValidateDocumentRequest; got "
                f"{type(request).__name__}"
            )
        return validation_response(
            self._service.validate_document(request.document)
        )

    def build_document(
        self, request: BuildDocumentRequest
    ) -> BuildDocumentResponse:
        """Build a document and return its external response."""
        if not isinstance(request, BuildDocumentRequest):
            raise ContractError(
                "expected a BuildDocumentRequest; got "
                f"{type(request).__name__}"
            )
        return build_response(self._service.build_document(request))

    # --- payload operations -----------------------------------------------

    def validate_document_payload(
        self, payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Payload in, payload out. A bad envelope is an invalid request."""
        try:
            request = validate_request_from_payload(payload)
        except ContractError as exc:
            return ValidateDocumentResponse(
                valid=False,
                error=ErrorContract(
                    failure=ServiceFailure.INVALID_REQUEST.value,
                    message=str(exc),
                ),
            ).to_payload()
        return self.validate_document(request).to_payload()

    def build_document_payload(
        self, payload: Mapping[str, Any]
    ) -> Dict[str, Any]:
        """Payload in, payload out. A bad envelope is an invalid request."""
        try:
            request = build_request_from_payload(payload)
        except ContractError as exc:
            return request_error_response(exc).to_payload()
        return self.build_document(request).to_payload()


# --- internals -------------------------------------------------------------


def _require_fields(
    payload: Any,
    fields: Tuple[str, ...],
    operation: str,
    *,
    optional: Tuple[str, ...] = (),
) -> None:
    """Check a payload's envelope. Strict: unknown fields are refused."""
    if not isinstance(payload, Mapping):
        raise ContractError(
            f"a {operation} payload is an object; got {type(payload).__name__}"
        )
    required = set(fields) - set(optional)
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - set(fields))
    if missing or unknown:
        raise ContractError(
            f"a {operation} payload holds "
            + ", ".join(fields)
            + (f"; missing {missing}" if missing else "")
            + (f"; unknown {unknown}" if unknown else "")
        )


def _document_of(document: Any) -> Document:
    """Accept a document in the forms the application service accepts.

    A mapping is **deep-copied**: the contract holds its own structure rather
    than aliasing the caller's, so neither side can surprise the other by
    mutating a nested list later.
    """
    if isinstance(document, Mapping):
        return copy.deepcopy(dict(document))
    if isinstance(document, (str, bytes)):
        return document
    raise ContractError(
        "a CAD document is a JSON object or JSON text; got "
        f"{type(document).__name__}"
    )


def _copy_document(document: Document) -> Any:
    """The document as supplied, deep-copied but never canonicalized."""
    if isinstance(document, Mapping):
        return copy.deepcopy(dict(document))
    if isinstance(document, (bytes, bytearray)):
        return bytes(document).decode("utf-8")
    return document


__all__ = [
    "BUILD_REQUEST_FIELDS",
    "FAILURES",
    "OUTPUTS",
    "STATUSES",
    "SUBSTITUTED_MESSAGES",
    "VALIDATE_REQUEST_FIELDS",
    "ArtifactContract",
    "BuildDocumentRequest",
    "BuildDocumentResponse",
    "CadApiContract",
    "ContractError",
    "ErrorContract",
    "ValidateDocumentRequest",
    "ValidateDocumentResponse",
    "ValidationErrorContract",
    "artifact_contract",
    "build_request_from_payload",
    "build_request_to_payload",
    "build_response",
    "error_contract",
    "request_error_response",
    "validate_request_from_payload",
    "validate_request_to_payload",
    "validation_response",
]
