"""The application/domain service boundary above the build infrastructure.

```
future transport   (HTTP API, CLI, UI, AI orchestration)
        │
Application Service          <- this module
        │
Build / Cache / Isolation    build_job, local_build_cache, isolated_execution
        │
CAD engine / export / render
```

**This is not the HTTP API.** It has no notion of a request header, a status
code, a route, a session or a user. A future HTTP API should *adapt to* this
service -- take a payload, call an operation, render the result -- and hold no
business logic of its own. The same goes for a CLI, a UI or an AI
orchestration layer: they are transports, and this is the operation set they
call.

The seven questions this layer answers
--------------------------------------
1. *can I accept a CAD document?* -- :meth:`CadApplicationService.validate_document`
   and :meth:`~CadApplicationService.build_document` take a canonical CAD
   document as JSON text or as a parsed structure.
2. *can I validate it?* -- yes, through the **existing** validator. This layer
   does not implement S1-S20 and has no validation rules of its own.
3. *can I build it?* -- :meth:`~CadApplicationService.build_document`.
4. *can I request specific outputs?* -- yes, by name, converted **once** into
   the existing :class:`~cad_core.build_job.BuildOptions`.
5. *can I return a structured result?* -- :class:`BuildOutcome`, composed from
   the existing manifest and error objects.
6. *can I report failures cleanly?* -- :class:`ServiceError`, with a stable
   taxonomy that keeps a validation failure, a geometry failure, an export
   failure and a process crash four different things.
7. *can I identify the build deterministically?* -- the existing
   ``document_hash`` and ``build_key``. **No new identifier is invented.**

No kernel internals are exposed
-------------------------------
Nothing this module returns holds a CadQuery or OpenCascade object. The
in-memory B-rep does not exist in this process at all: builds run in a child
(Stage 19), and geometry is described by measurements, as it has been since
Stage 17. Every result serializes to JSON with the existing ``to_dict``
methods, and a test asserts no kernel vocabulary survives the boundary.

The input boundary is JSON
--------------------------
Deliberately one boundary, not three:

```
JSON document (text or parsed structure)
        │  cad_core.serialization
validated Part
        │  cad_core.build_job
BuildRequest
```

A typed :class:`~cad_core.model.Part` is **not** accepted here. A caller
holding one is already inside the domain and should use
:mod:`cad_core.build_job` directly; the service exists for the layers outside,
which speak JSON. No document is accepted without going through the existing
deserializer, so an arbitrary dictionary cannot slip past validation.

What is deliberately excluded
-----------------------------
No HTTP, no REST, no GraphQL, no frontend, no CLI, no LLM, no MCP, no
authentication, no authorization, no user or tenant identity, no database, no
queue, no cloud, no rate limit, no payload cap and no quota. Rate limiting,
payload limits, authentication and quotas belong to a future
transport/security layer, not here. FeatureScript is not generated: it remains
a separate derived-output path and is never part of a build's outputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

from cad_core.artifact_registry import ArtifactKind, ArtifactManifest
from cad_core.build_job import (
    BuildFailure,
    BuildOptions,
    BuildRequest,
    BuildRequestError,
    BuildStatus,
    build_key_for,
)
from cad_core.isolated_execution import (
    DEFAULT_TIMEOUT_SECONDS,
    IsolatedExecution,
    IsolationOutcome,
    cached_execution,
    get_or_build_isolated,
)
from cad_core.local_build_cache import CacheError, LocalBuildCache
from cad_core.model import Part
from cad_core.render_model import RenderModel
from cad_core.serialization import (
    DocumentParseError,
    DocumentValidationError,
    deserialize_part,
    part_from_json,
    part_hash,
    serialize_part,
)

PathLike = Union[str, "os.PathLike[str]"]

#: A document as this service accepts it: canonical JSON text, its UTF-8
#: bytes, or an already-parsed JSON structure. Never a typed ``Part``, and
#: never a filesystem path.
Document = Union[str, bytes, Mapping[str, Any]]

#: The output names a caller may ask for -- the existing artifact kinds'
#: values, and no second enumeration.
OUTPUT_NAMES: Tuple[str, ...] = tuple(kind.value for kind in ArtifactKind)

#: Terminal statuses a service call can report. A call is synchronous, so a
#: build is never left ``QUEUED`` or ``RUNNING``; these are Stage 16's own
#: statuses rather than a new enum.
SERVICE_STATUSES: Tuple[BuildStatus, ...] = (
    BuildStatus.SUCCEEDED,
    BuildStatus.FAILED,
)


class ApplicationServiceError(Exception):
    """A programming error in how the service was called.

    Reserved for things a transport layer cannot cause: a wrong Python type,
    a missing cache root. **Ordinary bad input never raises** -- a malformed
    document, an invalid document, an unknown output name and a failed build
    are all :class:`BuildOutcome` results with a :class:`ServiceError`.
    """


class ServiceFailure(Enum):
    """The application-level failure taxonomy.

    Small, and a **mapping** rather than a duplicate: every value below is
    produced from an existing :class:`~cad_core.build_job.BuildFailure` or
    :class:`~cad_core.isolated_execution.IsolationOutcome`, and the
    :class:`ServiceError` carries that original classification alongside.
    Nothing is flattened to a string.

    The four distinctions this taxonomy exists to preserve:

    * a **document** failure is not a geometry failure;
    * an **export** failure is not a document failure;
    * a **process** crash or timeout is not a normal CAD error;
    * an **unexpected** internal error is not any of the above.
    """

    #: The document is not readable JSON, or not a JSON object.
    MALFORMED_DOCUMENT = "malformed_document"

    #: The document is well-formed but violates the V1 static rules (S1-S20).
    INVALID_DOCUMENT = "invalid_document"

    #: The *request* is unusable: no outputs, or an output name that is not
    #: one of :data:`OUTPUT_NAMES`. Separate from the document's own validity.
    INVALID_REQUEST = "invalid_request"

    #: The engine refused, or a geometric rule (E1-E5) failed.
    GEOMETRY_FAILED = "geometry_failed"

    #: An exporter or artifact publication failed -- the requested *output*
    #: could not be produced, though the geometry was fine.
    OUTPUT_FAILED = "output_failed"

    #: The execution or cache infrastructure failed: the build process
    #: crashed, timed out, or spoke the protocol wrongly, or the cache could
    #: not be used. Never a CAD error.
    EXECUTION_FAILED = "execution_failed"

    #: An unexpected internal error, surfaced structurally rather than hidden.
    INTERNAL_ERROR = "internal_error"


#: How an existing :class:`~cad_core.build_job.BuildFailure` maps up.
BUILD_FAILURE_MAP: Mapping[BuildFailure, ServiceFailure] = {
    BuildFailure.DOCUMENT_INVALID: ServiceFailure.INVALID_DOCUMENT,
    BuildFailure.UNSUPPORTED_GEOMETRY: ServiceFailure.GEOMETRY_FAILED,
    BuildFailure.GEOMETRY_FAILED: ServiceFailure.GEOMETRY_FAILED,
    BuildFailure.EXPORT_FAILED: ServiceFailure.OUTPUT_FAILED,
    BuildFailure.JOB_STATE: ServiceFailure.INTERNAL_ERROR,
    BuildFailure.INTERNAL: ServiceFailure.INTERNAL_ERROR,
}

#: How an existing :class:`~cad_core.isolated_execution.IsolationOutcome`
#: maps up, when the build never got far enough to classify itself.
EXECUTION_OUTCOME_MAP: Mapping[IsolationOutcome, ServiceFailure] = {
    IsolationOutcome.VALIDATION_FAILED: ServiceFailure.INVALID_DOCUMENT,
    IsolationOutcome.BUILD_FAILED: ServiceFailure.GEOMETRY_FAILED,
    IsolationOutcome.PROCESS_FAILED: ServiceFailure.EXECUTION_FAILED,
    IsolationOutcome.TIMED_OUT: ServiceFailure.EXECUTION_FAILED,
    IsolationOutcome.PROTOCOL_ERROR: ServiceFailure.EXECUTION_FAILED,
    IsolationOutcome.WORKER_ERROR: ServiceFailure.INTERNAL_ERROR,
}


@dataclass(frozen=True)
class ServiceError:
    """Why an operation did not succeed, at the application level.

    A thin composition: :attr:`failure` is this layer's taxonomy, and
    :attr:`build_failure` and :attr:`execution_outcome` are the original
    classifications from the layers below, kept rather than discarded. The
    validator's own structured errors travel in :attr:`validation_errors`.

    :attr:`message` is the **stable public sentence**, the contract Stage 16
    established: no traceback, no filesystem path, nothing from the
    environment, and never a child process's ``stderr``.
    """

    failure: ServiceFailure
    message: str

    #: ``"document"``, ``"request"``, ``"validation"``, ``"geometry"``, an
    #: output's own name, or ``"process"``.
    stage: str = "document"

    #: The Stage 16 classification, when there was one.
    build_failure: Optional[BuildFailure] = None

    #: The Stage 19 execution outcome, when a child process was involved.
    execution_outcome: Optional[IsolationOutcome] = None

    #: The output being produced when it failed, for an output failure.
    output: Optional[ArtifactKind] = None

    #: Specification rule codes (S1-S20, E1-E5).
    rule_codes: Tuple[str, ...] = ()

    #: The validator's structured errors, as the plain data
    #: :meth:`~cad_core.build_job.BuildError.to_dict` already produces.
    validation_errors: Tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure": self.failure.value,
            "message": self.message,
            "stage": self.stage,
            "build_failure": (
                self.build_failure.value if self.build_failure else None
            ),
            "execution_outcome": (
                self.execution_outcome.value if self.execution_outcome else None
            ),
            "output": self.output.value if self.output else None,
            "rule_codes": list(self.rule_codes),
            "validation_errors": [dict(item) for item in self.validation_errors],
        }


@dataclass(frozen=True)
class DocumentValidation:
    """The result of :meth:`CadApplicationService.validate_document`.

    On success :attr:`document_hash` is the Stage 15 canonical hash and
    :attr:`document` is the canonical form of what was submitted -- which is
    what a transport should echo back, since a terse document and a fully
    materialised one share a hash but only one is canonical.
    """

    valid: bool
    document_hash: Optional[str] = None

    #: The canonical CAD document, as plain JSON-compatible data.
    document: Optional[Mapping[str, Any]] = None

    #: The part's name, for a caller that wants to label the document.
    name: Optional[str] = None

    #: How many features the document holds.
    feature_count: Optional[int] = None

    error: Optional[ServiceError] = None

    #: The validated part. In-process only, excluded from :meth:`to_dict`, and
    #: never sent anywhere -- it is here so the service need not validate
    #: twice between :meth:`validate_document` and a following build.
    part: Optional[Part] = None

    def build_key_for(self, outputs: Sequence[str]) -> str:
        """The build key this document would have for ``outputs``.

        Uses the existing :func:`~cad_core.build_job.build_key_for`; no
        second identity scheme exists. Available so a caller can learn a
        build's identity without building it.
        """
        if self.part is None:
            raise ApplicationServiceError(
                "an invalid document has no build key"
            )
        return build_key_for(self.part, _options_for(outputs))

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-compatible data. Excludes the typed part."""
        return {
            "valid": self.valid,
            "document_hash": self.document_hash,
            "name": self.name,
            "feature_count": self.feature_count,
            "document": dict(self.document) if self.document is not None else None,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True)
class BuildDocumentRequest:
    """What a caller asks the service to build.

    A CAD document and the outputs wanted. Nothing else: no LLM parameters,
    no UI preferences, no user or tenant id, no authentication data, no HTTP
    fields, and **no filesystem path** -- where artifacts physically land is
    an infrastructure concern the service's backend owns, and is never part of
    a request's logical identity.

    A dumb data holder on purpose, so a transport can construct one straight
    from a payload. The output names are validated when the request is
    executed, where a bad one becomes a structured result rather than an
    exception in a constructor.
    """

    document: Document
    outputs: Tuple[str, ...] = OUTPUT_NAMES

    @classmethod
    def for_outputs(
        cls, document: Document, *outputs: Union[str, ArtifactKind]
    ) -> "BuildDocumentRequest":
        """``BuildDocumentRequest.for_outputs(doc, "step", ArtifactKind.STL)``."""
        return cls(
            document=document,
            outputs=tuple(
                output.value if isinstance(output, ArtifactKind) else output
                for output in outputs
            ),
        )


@dataclass(frozen=True)
class BuildOutcome:
    """The result of :meth:`CadApplicationService.build_document`.

    Composition, not a parallel hierarchy: :attr:`status` is Stage 16's
    :class:`~cad_core.build_job.BuildStatus`, :attr:`manifest` is Stage 17's
    :class:`~cad_core.artifact_registry.ArtifactManifest`, :attr:`error` wraps
    Stage 16's and Stage 19's own classifications, and :attr:`cache_hit` is
    Stage 18's flag. This class adds no artifact, error or manifest schema of
    its own.
    """

    status: BuildStatus

    #: The Stage 15 canonical document hash. ``None`` when the document never
    #: validated, because an invalid document has no canonical form.
    document_hash: Optional[str] = None

    #: The Stage 16 build key: the reproducible identity of this build.
    build_key: Optional[str] = None

    #: Whether the artifacts came from the cache rather than being built.
    cache_hit: bool = False

    #: Whether this call's build was published to the cache.
    cache_published: bool = False

    #: Exactly the artifacts this build published. ``None`` on failure.
    manifest: Optional[ArtifactManifest] = None

    error: Optional[ServiceError] = None

    #: Identity of the one execution, not of the build (Stage 16). A cache hit
    #: has none, because nothing executed.
    execution_id: Optional[str] = None

    #: The neutral render representation, when ``render`` was requested. A
    #: reference to plain data, excluded from :meth:`to_dict` for the reason
    #: Stage 16 gives: the manifest already describes it, and duplicating the
    #: payload into every result is what these layers avoid.
    render_model: Optional[RenderModel] = None

    @property
    def succeeded(self) -> bool:
        return self.status is BuildStatus.SUCCEEDED

    def artifact(self, output: Union[str, ArtifactKind]):
        """One artifact by output name or kind, or ``None``."""
        if self.manifest is None:
            return None
        kind = output if isinstance(output, ArtifactKind) else ArtifactKind(output)
        return self.manifest.artifact(kind)

    def produced_outputs(self) -> Tuple[str, ...]:
        """The output names this build produced, in canonical order."""
        if self.manifest is None:
            return ()
        return tuple(kind.value for kind in self.manifest.kinds())

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-compatible data.

        ``manifest`` is :meth:`ArtifactManifest.to_dict`, and ``error`` is
        :meth:`ServiceError.to_dict`, which itself carries the lower layers'
        own classifications. No schema is redefined here.
        """
        return {
            "status": self.status.value,
            "succeeded": self.succeeded,
            "document_hash": self.document_hash,
            "build_key": self.build_key,
            "cache_hit": self.cache_hit,
            "cache_published": self.cache_published,
            "outputs": list(self.produced_outputs()),
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "error": self.error.to_dict() if self.error else None,
            "execution_id": self.execution_id,
        }


class LocalBuildBackend:
    """The one backend: the local cache over isolated local execution.

    The minimum interface the service needs is two methods -- run a build, and
    look one up -- so that is the whole interface. There is no plugin system,
    no registry and no hypothetical second backend. A future remote or
    Onshape backend would implement the same two methods and return the same
    :class:`~cad_core.isolated_execution.IsolatedExecution` shape, whose
    fields (an outcome, a build key, a manifest, cache flags, a structured
    failure) are transport-neutral and whose outcomes -- a crash, a timeout, a
    protocol error -- are exactly what any out-of-process backend reports.
    """

    def __init__(
        self,
        cache: LocalBuildCache,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(cache, LocalBuildCache):
            raise ApplicationServiceError(
                f"expected a LocalBuildCache; got {type(cache).__name__}"
            )
        self._cache = cache
        self._timeout_seconds = timeout_seconds

    @property
    def cache(self) -> LocalBuildCache:
        return self._cache

    @property
    def timeout_seconds(self) -> float:
        return self._timeout_seconds

    def execute(self, request: BuildRequest) -> IsolatedExecution:
        """Serve ``request`` from the cache, or build it in a child process."""
        return get_or_build_isolated(
            request, self._cache, timeout_seconds=self._timeout_seconds
        )

    def lookup(self, request: BuildRequest) -> Optional[IsolatedExecution]:
        """The cached result for ``request``, or ``None``. Launches nothing."""
        return cached_execution(
            request, self._cache, timeout_seconds=self._timeout_seconds
        )


class CadApplicationService:
    """The application-level operations a transport layer calls.

    Two operations, plus one lookup:

    ==========================  =============================================
    :meth:`validate_document`   accept and validate a CAD document
    :meth:`build_document`      build one, returning a structured outcome
    :meth:`find_build`          is this build already available? (no build)
    ==========================  =============================================

    Deliberately not here: ``edit_document``, ``fork_document``,
    ``delete_document``, ``version_document`` and ``compare_documents``. Those
    need storage and history, which do not exist.

    There is no ``get_build_status`` over a *job*: a call is synchronous and no
    job store exists, so there is no pending build to ask about.
    :meth:`find_build` is the meaningful version of that question -- is the
    result already available? -- and it answers it from the cache without
    launching anything.
    """

    def __init__(self, backend: LocalBuildBackend) -> None:
        if not hasattr(backend, "execute") or not hasattr(backend, "lookup"):
            raise ApplicationServiceError(
                "a backend must provide execute() and lookup(); got "
                f"{type(backend).__name__}"
            )
        self._backend = backend

    @classmethod
    def local(
        cls,
        cache_root: PathLike,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> "CadApplicationService":
        """A service over the local backend, caching under ``cache_root``.

        The cache root is the service's own infrastructure, supplied by
        whoever configures it. It is never part of a request, never part of an
        identity, and never invented here.
        """
        try:
            cache = LocalBuildCache(cache_root)
        except CacheError as exc:
            raise ApplicationServiceError(str(exc)) from None
        return cls(LocalBuildBackend(cache, timeout_seconds=timeout_seconds))

    @property
    def backend(self) -> LocalBuildBackend:
        return self._backend

    # --- operations -------------------------------------------------------

    def validate_document(self, document: Document) -> DocumentValidation:
        """Accept a CAD document and report whether it is valid.

        Validation is the **existing** validator's, reached through the
        existing deserializer: this layer implements no rule and duplicates no
        validation class. Nothing is built, and no child process runs.

        A malformed or invalid document is a :class:`DocumentValidation` with
        ``valid`` ``False`` and a structured :class:`ServiceError` -- never an
        exception, because bad input is an ordinary outcome here.
        """
        try:
            part = _part_of(document)
        except ApplicationServiceError:
            raise
        except (DocumentParseError, DocumentValidationError) as exc:
            return DocumentValidation(valid=False, error=_document_error(exc))
        return DocumentValidation(
            valid=True,
            document_hash=part_hash(part),
            document=serialize_part(part),
            name=part.name,
            feature_count=len(part.features),
            part=part,
        )

    def build_document(self, request: BuildDocumentRequest) -> BuildOutcome:
        """Build a CAD document and return a structured outcome.

        The whole path, in order:

        1. the document is deserialized and validated by the existing
           validator. **A validation failure stops here** -- no geometry runs
           and no child process starts;
        2. the output names are converted **once** into the existing
           :class:`~cad_core.build_job.BuildOptions`;
        3. a :class:`~cad_core.build_job.BuildRequest` is formed, whose
           ``document_hash`` and ``build_key`` are its own computed
           properties -- the service invents neither;
        4. the backend serves it from the cache, or builds it out of process;
        5. the result is composed into a :class:`BuildOutcome`.

        Repeating the call with the same document and the same outputs gives
        the same ``document_hash`` and the same ``build_key``; the first call
        reports ``cache_hit`` ``False`` and later ones ``True`` while the
        cache entry stays valid.
        """
        prepared = self._prepare(request)
        if isinstance(prepared, BuildOutcome):
            return prepared
        return self._compose(self._run(self._backend.execute, prepared))

    def find_build(self, request: BuildDocumentRequest) -> Optional[BuildOutcome]:
        """The already-available result for ``request``, or ``None``.

        A cache lookup: nothing is built and no child process starts. Returns
        ``None`` both when the document is invalid and when no valid cache
        entry exists -- use :meth:`validate_document` to tell those apart.
        """
        prepared = self._prepare(request)
        if isinstance(prepared, BuildOutcome):
            return None
        execution = self._run(self._backend.lookup, prepared)
        if execution is None or isinstance(execution, BuildOutcome):
            return None
        return self._compose(execution)

    # --- internals --------------------------------------------------------

    def _prepare(
        self, request: BuildDocumentRequest
    ) -> Union[BuildRequest, BuildOutcome]:
        """Validate the request, or return the outcome that says why not."""
        if not isinstance(request, BuildDocumentRequest):
            raise ApplicationServiceError(
                "expected a BuildDocumentRequest; got "
                f"{type(request).__name__}"
            )
        try:
            options = _options_for(request.outputs)
        except _RequestProblem as problem:
            return BuildOutcome(
                status=BuildStatus.FAILED,
                error=ServiceError(
                    failure=ServiceFailure.INVALID_REQUEST,
                    message=str(problem),
                    stage="request",
                ),
            )
        try:
            part = _part_of(request.document)
        except ApplicationServiceError:
            raise
        except (DocumentParseError, DocumentValidationError) as exc:
            return BuildOutcome(
                status=BuildStatus.FAILED, error=_document_error(exc)
            )
        return BuildRequest(part=part, options=options)

    def _run(self, operation: Any, request: BuildRequest) -> Any:
        """Call the backend, turning an infrastructure failure into an outcome."""
        try:
            return operation(request)
        except CacheError as exc:
            # The cache is infrastructure: its failure is not a CAD error, and
            # is never reported as one.
            return BuildOutcome(
                status=BuildStatus.FAILED,
                document_hash=request.document_hash,
                build_key=request.build_key,
                error=ServiceError(
                    failure=ServiceFailure.EXECUTION_FAILED,
                    message=f"the build cache could not be used ({type(exc).__name__})",
                    stage="cache",
                ),
            )

    def _compose(self, execution: Any) -> BuildOutcome:
        """Compose a backend execution into a :class:`BuildOutcome`."""
        if isinstance(execution, BuildOutcome):
            return execution
        if execution.outcome is IsolationOutcome.SUCCEEDED:
            return BuildOutcome(
                status=BuildStatus.SUCCEEDED,
                document_hash=execution.document_hash,
                build_key=execution.build_key,
                cache_hit=execution.cache_hit,
                cache_published=execution.cache_published,
                manifest=execution.manifest,
                render_model=execution.render_model,
                execution_id=execution.execution_id,
            )
        return BuildOutcome(
            status=BuildStatus.FAILED,
            document_hash=execution.document_hash,
            build_key=execution.build_key,
            error=_execution_error(execution),
            execution_id=execution.execution_id,
        )


# --- module-level helpers ---------------------------------------------------


class _RequestProblem(Exception):
    """The requested outputs are unusable."""


def _options_for(outputs: Iterable[Any]) -> BuildOptions:
    """Convert output names into the existing :class:`BuildOptions`, once.

    No second output enumeration exists: a name is looked up in
    :class:`~cad_core.artifact_registry.ArtifactKind`, which is what
    ``BuildOptions`` already holds.
    """
    if isinstance(outputs, (str, bytes)):
        raise _RequestProblem(
            "outputs must be a sequence of output names, not a single string"
        )
    names = list(outputs)
    if not names:
        raise _RequestProblem(
            "a build must request at least one output; choose from "
            + ", ".join(OUTPUT_NAMES)
        )
    selected = []
    for name in names:
        if isinstance(name, ArtifactKind):
            selected.append(name)
            continue
        if not isinstance(name, str):
            raise _RequestProblem(
                f"an output name is a string; got {type(name).__name__}"
            )
        try:
            selected.append(ArtifactKind(name))
        except ValueError:
            raise _RequestProblem(
                f"unknown output {name!r}; choose from "
                + ", ".join(OUTPUT_NAMES)
            ) from None
    try:
        return BuildOptions(outputs=tuple(selected))
    except BuildRequestError as exc:  # pragma: no cover - guarded above
        raise _RequestProblem(str(exc)) from None


def _part_of(document: Document) -> Part:
    """Deserialize and validate a document through the existing boundary."""
    if isinstance(document, Part):
        raise ApplicationServiceError(
            "the application service accepts a CAD document as JSON text or "
            "a parsed structure, not a typed Part; a caller holding a Part is "
            "inside the domain and can use cad_core.build_job directly"
        )
    if isinstance(document, (str, bytes, bytearray)):
        return part_from_json(document)
    if isinstance(document, Mapping):
        return deserialize_part(document)
    raise ApplicationServiceError(
        "a CAD document is JSON text, UTF-8 bytes or a parsed mapping; got "
        f"{type(document).__name__}"
    )


def _document_error(
    exc: Union[DocumentParseError, DocumentValidationError]
) -> ServiceError:
    """Map a serialization-layer failure into the service taxonomy."""
    if isinstance(exc, DocumentValidationError):
        errors = exc.errors
        return ServiceError(
            failure=ServiceFailure.INVALID_DOCUMENT,
            message=(
                f"the CAD document violates {len(errors)} V1 rule(s): "
                + "; ".join(sorted({error.rule for error in errors}))
            ),
            stage="validation",
            build_failure=BuildFailure.DOCUMENT_INVALID,
            rule_codes=exc.rule_codes(),
            validation_errors=tuple(
                {
                    "rule": error.rule,
                    "message": error.message,
                    "feature_id": error.feature_id,
                    "field_path": error.field_path,
                    "feature_index": error.feature_index,
                }
                for error in errors
            ),
        )
    return ServiceError(
        failure=ServiceFailure.MALFORMED_DOCUMENT,
        message="the CAD document is not a readable JSON object",
        stage="document",
    )


def _execution_error(execution: IsolatedExecution) -> ServiceError:
    """Map a failed execution into the service taxonomy.

    The build's own classification wins where it exists, so an export failure
    stays an output failure rather than being blurred into "the build failed";
    otherwise the execution outcome decides, which is how a crash and a
    timeout stay distinct from any CAD error.
    """
    failure = execution.failure
    build_failure = failure.failure if failure is not None else None
    if build_failure is not None and build_failure in BUILD_FAILURE_MAP:
        kind = BUILD_FAILURE_MAP[build_failure]
    else:
        kind = EXECUTION_OUTCOME_MAP.get(
            execution.outcome, ServiceFailure.INTERNAL_ERROR
        )
    if failure is None:  # pragma: no cover - every failure carries one
        return ServiceError(
            failure=kind,
            message="the build failed",
            stage="process",
            execution_outcome=execution.outcome,
        )
    return ServiceError(
        failure=kind,
        message=failure.message,
        stage=failure.stage,
        build_failure=build_failure,
        execution_outcome=execution.outcome,
        output=failure.output,
        rule_codes=failure.rule_codes,
        validation_errors=failure.validation_errors,
    )


__all__ = [
    "BUILD_FAILURE_MAP",
    "EXECUTION_OUTCOME_MAP",
    "OUTPUT_NAMES",
    "SERVICE_STATUSES",
    "ApplicationServiceError",
    "BuildDocumentRequest",
    "BuildOutcome",
    "CadApplicationService",
    "Document",
    "DocumentValidation",
    "LocalBuildBackend",
    "ServiceError",
    "ServiceFailure",
]
