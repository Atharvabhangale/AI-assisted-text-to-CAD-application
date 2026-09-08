"""A local, in-memory build/job layer over the canonical CAD document.

This is infrastructure, not a service. It turns a canonical CAD document into
a reproducible build result, and records enough structure that a later UI or
API could report on it without reading Python tracebacks.

```
BuildRequest  (canonical document + BuildOptions)
    |
BuildJob      (QUEUED -> RUNNING -> SUCCEEDED | FAILED)
    |
execution     (local CAD engine, then the requested exporters)
    |
BuildResult   -> ArtifactManifest -> Artifact[]
              (or one structured BuildError)
```

Since Stage 17 the artifact model lives in
:mod:`cad_core.artifact_registry`, which owns artifact identity, content
checksums and storage kind. This module orchestrates: it runs the engine and
the exporters, hands each produced output to the registry to be **published**,
and assembles the results into an :class:`~cad_core.artifact_registry.ArtifactManifest`
reachable as :attr:`BuildResult.manifest`. ``BuildOutput`` and
``BuildArtifact`` are the build layer's names for
:class:`~cad_core.artifact_registry.ArtifactKind` and
:class:`~cad_core.artifact_registry.Artifact`; nothing about the Stage 16 API
changed.

There is **no** database, queue, broker, worker pool, scheduler, HTTP surface
or persistence layer here, and none is implied. A job is an ordinary Python
object that lives as long as the caller holds it.

The document stays the source of truth
--------------------------------------
A build carries **no second copy of the CAD semantics**. A
:class:`BuildRequest` holds the validated :class:`~cad_core.model.Part` and
nothing else about the design; the document's identity is its Stage 15
canonical SHA-256 (:func:`cad_core.serialization.part_hash`), and that hash is
a *computed property* rather than a field a caller can supply, so it cannot
drift from the part it names.

Geometry, STEP, IGES, STL and render output are all derived. None of them ever
identifies a document: a build's identity is the document hash plus the build
options, never a hash of an exported file.

Build identity versus execution identity
----------------------------------------
Two different things, deliberately separate:

* :attr:`BuildRequest.build_key` -- **content-derived and reproducible**. The
  hex SHA-256 of the canonical JSON of
  ``{"document_hash": ..., "options": {"outputs": [...]}}``; see
  :func:`build_key_for`. The same document with the same options always has
  the same build key, on any machine, at any time.
* :attr:`BuildJob.execution_id` -- a random identifier for **one run**. Useful
  for telling two executions of the same build apart. It is never part of a
  build's identity, and no timestamp is used as identity anywhere.

Artifact identity versus artifact content
-----------------------------------------
An artifact's **identity** is ``<build key>:<kind>`` -- reproducible anywhere
from the document and the options. Its **content** facts (size, SHA-256) and
its **physical path** are separate, because STEP and IGES bytes are not
reproducible between runs. So two builds of the same document produce
identical canonical manifests and possibly different file checksums. The
artifact layer documents the distinction in full.

Artifact selection is explicit
------------------------------
:class:`BuildOptions` has no default output set: a caller must say what it
wants. The B-rep is always *constructed*, because every other output derives
from it, but it appears in the artifact list only when
:attr:`BuildOutput.GEOMETRY` is requested. A successful build's artifact list
holds **exactly** the requested outputs -- no more, no fewer.

Failure model
-------------
Failures are classified (:class:`BuildFailure`), never collapsed into one
string:

============================ ==========================================
``DOCUMENT_INVALID``         the document fails static validation (S1-S20)
``UNSUPPORTED_GEOMETRY``     the engine does not build this part
``GEOMETRY_FAILED``          a geometric rule failed (E1-E5) or a kernel
                             operation could not complete
``EXPORT_FAILED``            an exporter or the render builder refused
``JOB_STATE``                an illegal status transition was attempted
``INTERNAL``                 an unexpected exception
============================ ==========================================

:attr:`BuildError.message` is the **stable, public** sentence: no traceback,
no filesystem path, no environment. Unexpected exceptions are not hidden --
the exception's type name and its traceback are kept in
:attr:`BuildError.exception_type` and :attr:`BuildError.diagnostic`, which are
explicitly *diagnostic only* and not part of the contract.

Failure atomicity
-----------------
A failed build never reports success. If geometry succeeds and then an export
fails, the job is ``FAILED``, its artifact list is **empty**, and any file this
build had already written is **deleted** -- see :func:`run_job`. The outputs
that had completed are recorded by name in
:attr:`BuildError.completed_outputs`, so nothing is silently lost, but no
"success-looking" artifact survives.

Determinism
-----------
The same document and options give the same build key, the same geometry
measurements, the same requested artifact set and the same render model. STEP
and IGES **bytes** are deliberately not part of that claim: earlier stages
measured that their headers carry a timestamp and a translator counter. STL
bytes and the render model were measured stable and remain so.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple, Union

from cad_core.artifact_registry import (
    KIND_STORAGE as _STORAGE,
    Artifact,
    ArtifactKind,
    ArtifactManifest,
    ArtifactPublicationError,
    ArtifactStorage,
    KIND_DEFAULT_EXTENSION,
    build_manifest,
    publish_file_artifact,
    publish_geometry_artifact,
    publish_render_artifact,
)
from cad_core.errors import ValidationError
from cad_core.iges_export import export_iges
from cad_core.local_cad import (
    GeometryOperationError,
    LocalCadResult,
    UnsupportedGeometryError,
    build_part,
)
from cad_core.model import Part
from cad_core.render_model import RenderModel, RenderModelError, build_render_model
from cad_core.serialization import (
    CANONICAL_ENCODING,
    CANONICAL_SEPARATORS,
    DocumentValidationError,
    deserialize_part,
    part_hash,
)
from cad_core.step_export import export_step
from cad_core.stl_export import export_stl

PathLike = Union[str, "os.PathLike[str]"]

#: Hash used for a build key, from :mod:`hashlib`. The same algorithm the
#: canonical document uses, applied to a different structure.
BUILD_KEY_ALGORITHM = "sha256"


#: The requestable outputs of a build.
#:
#: **The same enum as** :class:`~cad_core.artifact_registry.ArtifactKind`: a
#: build output and the artifact it produces are one concept, and Stage 17
#: moved the definition to the artifact layer, where identity is defined.
#: ``BuildOutput`` remains as the build layer's name for it, so existing
#: callers are unaffected.
BuildOutput = ArtifactKind


#: Outputs written to the filesystem. Requesting any of these requires an
#: output directory. Defined by the artifact layer, which owns storage kind.
FILE_OUTPUTS: Tuple[BuildOutput, ...] = tuple(
    kind for kind in ArtifactKind if _STORAGE[kind] is ArtifactStorage.FILE
)

#: Outputs that stay in memory for this stage.
IN_MEMORY_OUTPUTS: Tuple[BuildOutput, ...] = tuple(
    kind for kind in ArtifactKind if _STORAGE[kind] is ArtifactStorage.IN_MEMORY
)


class BuildStatus(Enum):
    """Lifecycle state of a :class:`BuildJob`.

    There is no cancellation state: nothing in this stage can cancel a build,
    and a state that no code can reach would be speculation.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


#: The only permitted transitions. Everything else is a :class:`BuildStateError`.
ALLOWED_TRANSITIONS: Mapping[BuildStatus, Tuple[BuildStatus, ...]] = {
    BuildStatus.QUEUED: (BuildStatus.RUNNING,),
    BuildStatus.RUNNING: (BuildStatus.SUCCEEDED, BuildStatus.FAILED),
    BuildStatus.SUCCEEDED: (),
    BuildStatus.FAILED: (),
}

#: States a job can never leave.
TERMINAL_STATUSES: Tuple[BuildStatus, ...] = (
    BuildStatus.SUCCEEDED,
    BuildStatus.FAILED,
)


class BuildFailure(Enum):
    """Why a build failed. Deliberately not one bucket."""

    DOCUMENT_INVALID = "document_invalid"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    GEOMETRY_FAILED = "geometry_failed"
    EXPORT_FAILED = "export_failed"
    JOB_STATE = "job_state"
    INTERNAL = "internal"


class CadBuildError(Exception):
    """Base class for build-layer programming errors."""


class BuildRequestError(CadBuildError):
    """Raised for a request the layer cannot execute as asked.

    A usage error, not a build outcome: an empty output set, or file outputs
    requested with nowhere to write them.
    """


class BuildStateError(CadBuildError):
    """Raised when an illegal status transition is attempted.

    Includes any attempt to finalize an already terminal job, which is what
    guards against accidental double-finalization.
    """

    def __init__(self, current: BuildStatus, requested: BuildStatus) -> None:
        self.current = current
        self.requested = requested
        self.failure = BuildFailure.JOB_STATE
        permitted = ALLOWED_TRANSITIONS[current]
        allowed = ", ".join(status.value for status in permitted) or "nothing"
        super().__init__(
            f"a {current.value} build cannot become {requested.value}; "
            f"permitted from {current.value}: {allowed}"
        )


# --- options and request ----------------------------------------------------


@dataclass(frozen=True)
class BuildOptions:
    """What a build should produce. Small by design.

    Carries no CAD semantics -- the document already holds those -- and no LLM
    or UI settings. ``outputs`` has no default: artifact selection is always
    explicit.

    The canonical form deduplicates and sorts the outputs, because requesting
    the same set in a different order is the same request.
    """

    outputs: Tuple[BuildOutput, ...]

    def __post_init__(self) -> None:
        if not self.outputs:
            raise BuildRequestError(
                "a build must request at least one output; choose from "
                + ", ".join(output.value for output in BuildOutput)
            )
        for output in self.outputs:
            if not isinstance(output, BuildOutput):
                raise BuildRequestError(
                    "outputs must be BuildOutput members; got "
                    f"{type(output).__name__}"
                )

    @classmethod
    def for_outputs(cls, *outputs: BuildOutput) -> "BuildOptions":
        """Convenience constructor: ``BuildOptions.for_outputs(STEP, STL)``."""
        return cls(outputs=tuple(outputs))

    @property
    def requested(self) -> Tuple[BuildOutput, ...]:
        """The requested outputs, deduplicated and in canonical order."""
        return tuple(
            output for output in BuildOutput if output in set(self.outputs)
        )

    def wants(self, output: BuildOutput) -> bool:
        return output in set(self.outputs)

    @property
    def writes_files(self) -> bool:
        return any(self.wants(output) for output in FILE_OUTPUTS)

    def canonical(self) -> Dict[str, Any]:
        """The canonical, JSON-compatible form used in the build key."""
        return {"outputs": [output.value for output in self.requested]}


@dataclass(frozen=True)
class BuildRequest:
    """A validated part plus the outputs to produce from it.

    ``document_hash`` and ``build_key`` are computed properties, never fields:
    a caller cannot supply a hash that disagrees with the part, so the two
    cannot drift.
    """

    part: Part
    options: BuildOptions

    def __post_init__(self) -> None:
        if not isinstance(self.part, Part):
            raise BuildRequestError(
                "a build request needs a typed cad_core.model.Part, such as "
                "the 'part' of a successful validate() result; got "
                f"{type(self.part).__name__}"
            )
        if not isinstance(self.options, BuildOptions):
            raise BuildRequestError(
                f"options must be a BuildOptions; got {type(self.options).__name__}"
            )

    @property
    def document_hash(self) -> str:
        """The canonical CAD document's SHA-256 (Stage 15)."""
        return part_hash(self.part)

    @property
    def canonical_build_document(self) -> Dict[str, Any]:
        """Exactly what the build key hashes."""
        return {
            "document_hash": self.document_hash,
            "options": self.options.canonical(),
        }

    @property
    def build_key(self) -> str:
        """Content-derived, reproducible identity of this build."""
        return _hash_canonical(self.canonical_build_document)


def build_key_for(part: Part, options: BuildOptions) -> str:
    """Return the build key for ``part`` built with ``options``.

    The key is the hex SHA-256 of this UTF-8 JSON, written with the canonical
    document's own conventions (minimal separators, ``ensure_ascii=False``)::

        {"document_hash":"<64 hex>","options":{"outputs":["geometry","step"]}}

    Not a random id, not a timestamp, not a path, and not derived from any
    geometry object. It is also **not** the document hash: the two are
    different strings for the same part, because they hash different
    structures.
    """
    return BuildRequest(part=part, options=options).build_key


def request_for_document(
    document: Mapping[str, Any], options: BuildOptions
) -> BuildRequest:
    """Validate a canonical CAD document and return a build request.

    Raises:
        DocumentValidationError: if the document violates any of S1-S20. Use
            :func:`execute_build_document` instead to receive that as a
            ``FAILED`` job rather than an exception.
    """
    return BuildRequest(part=deserialize_part(document), options=options)


# --- artifacts and results --------------------------------------------------


#: Metadata for one produced output.
#:
#: **The same class as** :class:`~cad_core.artifact_registry.Artifact`. Stage
#: 17 moved the artifact model to the artifact layer, which owns identity,
#: content checksums and storage kind; ``BuildArtifact`` remains as the build
#: layer's name for it so existing callers are unaffected.
BuildArtifact = Artifact


@dataclass(frozen=True)
class BuildError:
    """Structured description of why a build failed.

    :attr:`message` is the stable, public sentence. It carries no traceback,
    no filesystem path and nothing from the environment, so it is safe to show
    a user. :attr:`exception_type` and :attr:`diagnostic` exist so an
    unexpected failure is not *hidden* during development; both are diagnostic
    only and neither is part of the contract.
    """

    failure: BuildFailure
    message: str

    #: Which stage of the build failed: ``"validation"``, ``"geometry"``, or
    #: an output's own name.
    stage: str

    #: The output being produced when the failure happened, when applicable.
    output: Optional[BuildOutput] = None

    #: Specification rule codes, for a document or geometric-rule failure.
    rule_codes: Tuple[str, ...] = ()

    #: The validator's own structured errors, for ``DOCUMENT_INVALID``.
    validation_errors: Tuple[ValidationError, ...] = ()

    #: Outputs that had completed before the failure. Recorded so nothing is
    #: silently lost; their artifacts are **not** reported as produced.
    completed_outputs: Tuple[BuildOutput, ...] = ()

    #: Class name of an unexpected exception. Diagnostic only.
    exception_type: Optional[str] = None

    #: Unstable detail for diagnosis -- an exporter's own message, or a
    #: traceback. Never the public error contract.
    diagnostic: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure": self.failure.value,
            "message": self.message,
            "stage": self.stage,
            "output": self.output.value if self.output is not None else None,
            "rule_codes": list(self.rule_codes),
            "validation_errors": [
                {
                    "rule": error.rule,
                    "message": error.message,
                    "feature_id": error.feature_id,
                    "field_path": error.field_path,
                    "feature_index": error.feature_index,
                }
                for error in self.validation_errors
            ],
            "completed_outputs": [
                output.value for output in self.completed_outputs
            ],
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True)
class BuildResult:
    """The outcome of one execution.

    On success :attr:`artifacts` holds exactly the requested outputs and
    :attr:`error` is ``None``. On failure :attr:`artifacts` is **empty** and
    :attr:`error` says why -- there is no partial success.

    :attr:`geometry` and :attr:`render_model` are in-memory references, not
    payloads to serialize. ``geometry`` holds a
    :class:`~cad_core.local_cad.LocalCadResult`, which wraps a kernel shape;
    it is deliberately excluded from :meth:`to_dict` so no CadQuery or
    OpenCascade object can reach a serialized job.
    """

    build_key: str
    document_hash: str
    execution_id: str
    status: BuildStatus
    artifacts: Tuple[BuildArtifact, ...] = ()
    error: Optional[BuildError] = None

    #: The built B-rep, when ``GEOMETRY`` was requested. In-memory only.
    geometry: Optional[LocalCadResult] = None

    #: The neutral render representation, when ``RENDER`` was requested. Plain
    #: data (Stage 8), kept as a reference rather than duplicated as JSON.
    render_model: Optional[RenderModel] = None

    @property
    def succeeded(self) -> bool:
        return self.status is BuildStatus.SUCCEEDED

    @property
    def manifest(self) -> ArtifactManifest:
        """The build-level artifact manifest.

        Lists exactly the artifacts this build published, in the canonical
        artifact order. Empty for a failed build, because a failed build
        publishes nothing. See ``docs/artifact-registry.md``.
        """
        return build_manifest(
            document_hash=self.document_hash,
            build_key=self.build_key,
            artifacts=self.artifacts,
        )

    def artifact(self, output: BuildOutput) -> Optional[BuildArtifact]:
        for artifact in self.artifacts:
            if artifact.output is output:
                return artifact
        return None

    def produced_outputs(self) -> Tuple[BuildOutput, ...]:
        return tuple(artifact.output for artifact in self.artifacts)

    def to_dict(self) -> Dict[str, Any]:
        """A neutral, JSON-compatible summary.

        Contains only plain data: no kernel object, and no render payload. A
        later UI or API can report a build from this alone.
        """
        return {
            "build_key": self.build_key,
            "document_hash": self.document_hash,
            "execution_id": self.execution_id,
            "status": self.status.value,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "manifest": self.manifest.to_dict(),
            "error": self.error.to_dict() if self.error is not None else None,
        }


# --- the job ----------------------------------------------------------------


class BuildJob:
    """One build, as a small state machine.

    Created ``QUEUED``. :meth:`start` moves it to ``RUNNING``, and exactly one
    of :meth:`succeed` or :meth:`fail` finalizes it. Any other transition
    raises :class:`BuildStateError`, including a second attempt to finalize --
    which is the guard against accidental double-finalization.

    Transitions are taken under a :class:`threading.Lock`. That is three lines
    and makes the guard atomic; it is **not** a worker pool, and no
    concurrency infrastructure exists in this stage.
    """

    def __init__(self, request: BuildRequest) -> None:
        if not isinstance(request, BuildRequest):
            raise BuildRequestError(
                f"a job needs a BuildRequest; got {type(request).__name__}"
            )
        self._request = request
        self._status = BuildStatus.QUEUED
        self._result: Optional[BuildResult] = None
        self._execution_id = uuid.uuid4().hex
        self._lock = threading.Lock()

    # --- identity ---------------------------------------------------------

    @property
    def request(self) -> BuildRequest:
        return self._request

    @property
    def options(self) -> BuildOptions:
        return self._request.options

    @property
    def document_hash(self) -> str:
        """The canonical CAD document this build is traceable to."""
        return self._request.document_hash

    @property
    def build_key(self) -> str:
        """Reproducible identity: document hash plus canonical options."""
        return self._request.build_key

    @property
    def execution_id(self) -> str:
        """Identifies this run, not this build. Never part of identity."""
        return self._execution_id

    # --- state ------------------------------------------------------------

    @property
    def status(self) -> BuildStatus:
        return self._status

    @property
    def is_terminal(self) -> bool:
        return self._status in TERMINAL_STATUSES

    @property
    def result(self) -> Optional[BuildResult]:
        """The result once terminal, otherwise ``None``."""
        return self._result

    def can_transition_to(self, status: BuildStatus) -> bool:
        return status in ALLOWED_TRANSITIONS[self._status]

    def start(self) -> None:
        """QUEUED -> RUNNING."""
        self._transition(BuildStatus.RUNNING)

    def succeed(self, result: BuildResult) -> BuildResult:
        """RUNNING -> SUCCEEDED, recording ``result``."""
        if result.status is not BuildStatus.SUCCEEDED:
            raise BuildRequestError(
                "a succeeding result must carry status SUCCEEDED; got "
                f"{result.status.value}"
            )
        self._transition(BuildStatus.SUCCEEDED, result)
        return result

    def fail(self, error: BuildError) -> BuildResult:
        """RUNNING -> FAILED, recording ``error`` and no artifacts."""
        result = BuildResult(
            build_key=self.build_key,
            document_hash=self.document_hash,
            execution_id=self._execution_id,
            status=BuildStatus.FAILED,
            artifacts=(),
            error=error,
        )
        self._transition(BuildStatus.FAILED, result)
        return result

    def _transition(
        self, status: BuildStatus, result: Optional[BuildResult] = None
    ) -> None:
        with self._lock:
            if status not in ALLOWED_TRANSITIONS[self._status]:
                raise BuildStateError(self._status, status)
            self._status = status
            if result is not None:
                self._result = result

    def to_dict(self) -> Dict[str, Any]:
        """A neutral, JSON-compatible summary of the job."""
        return {
            "build_key": self.build_key,
            "document_hash": self.document_hash,
            "execution_id": self._execution_id,
            "status": self._status.value,
            "options": self._request.options.canonical(),
            "result": self._result.to_dict() if self._result is not None else None,
        }


# --- execution --------------------------------------------------------------


def run_job(
    job: BuildJob, *, output_directory: Optional[PathLike] = None
) -> BuildResult:
    """Execute a ``QUEUED`` job and return its result.

    Drives the transitions itself: ``QUEUED -> RUNNING`` first, then exactly
    one of ``SUCCEEDED`` or ``FAILED``. The B-rep is always built, because
    every other output derives from it; the artifact list holds exactly the
    requested outputs.

    On any failure the job is ``FAILED``, no artifact is reported, and **every
    file this call had already written is deleted** -- a failed build leaves
    no success-looking output behind.

    Args:
        job: A ``QUEUED`` :class:`BuildJob`.
        output_directory: Where file outputs are written. Required when the
            options request STEP, IGES or STL. Filenames are derived from the
            build key for convenience; the path is never the artifact's
            identity.

    Raises:
        BuildRequestError: if a file output is requested without a directory,
            or the directory does not exist. A usage error, raised before the
            job starts.
        BuildStateError: if the job is not ``QUEUED``.
    """
    options = job.options
    directory = _resolve_directory(options, output_directory)

    job.start()

    written: list = []
    completed: list = []
    try:
        try:
            local = build_part(job.request.part)
        except UnsupportedGeometryError as exc:
            return job.fail(
                BuildError(
                    failure=BuildFailure.UNSUPPORTED_GEOMETRY,
                    message=str(exc),
                    stage="geometry",
                    output=BuildOutput.GEOMETRY,
                )
            )
        except GeometryOperationError as exc:
            return job.fail(
                BuildError(
                    failure=BuildFailure.GEOMETRY_FAILED,
                    message=str(exc),
                    stage="geometry",
                    output=BuildOutput.GEOMETRY,
                    rule_codes=_rule_codes_in(str(exc)),
                )
            )

        artifacts: list = []
        render: Optional[RenderModel] = None

        if options.wants(BuildOutput.GEOMETRY):
            artifacts.append(
                publish_geometry_artifact(
                    document_hash=job.document_hash,
                    build_key=job.build_key,
                    result=local,
                )
            )
            completed.append(BuildOutput.GEOMETRY)

        for output in (BuildOutput.STEP, BuildOutput.IGES, BuildOutput.STL):
            if not options.wants(output):
                continue
            assert directory is not None  # guaranteed by _resolve_directory
            path = directory / f"{job.build_key}{KIND_DEFAULT_EXTENSION[output]}"
            try:
                _EXPORTERS[output](local, path)
            except Exception as exc:  # exporter-defined; classified below
                return job.fail(
                    _export_error(output, exc, tuple(completed))
                )
            written.append(path)
            # Publication is the gate between "the exporter returned" and
            # "this artifact exists": the file must be present, non-empty and
            # checksummable. A file that cannot be published is an export
            # failure, and the write above is cleaned up like any other.
            try:
                artifact = publish_file_artifact(
                    document_hash=job.document_hash,
                    build_key=job.build_key,
                    kind=output,
                    path=path,
                )
            except ArtifactPublicationError as exc:
                return job.fail(_export_error(output, exc, tuple(completed)))
            completed.append(output)
            artifacts.append(artifact)

        if options.wants(BuildOutput.RENDER):
            try:
                render = build_render_model(local)
            except (RenderModelError, ValueError) as exc:
                return job.fail(
                    _export_error(BuildOutput.RENDER, exc, tuple(completed))
                )
            artifacts.append(
                publish_render_artifact(
                    document_hash=job.document_hash,
                    build_key=job.build_key,
                    model=render,
                )
            )
            completed.append(BuildOutput.RENDER)

        return job.succeed(
            BuildResult(
                build_key=job.build_key,
                document_hash=job.document_hash,
                execution_id=job.execution_id,
                status=BuildStatus.SUCCEEDED,
                artifacts=tuple(artifacts),
                error=None,
                geometry=local if options.wants(BuildOutput.GEOMETRY) else None,
                render_model=render,
            )
        )
    except BuildStateError:
        # A state error is a programming error in the caller's use of the job,
        # not a build outcome. It is never swallowed into a result.
        raise
    except Exception as exc:  # unexpected: captured, not hidden
        return job.fail(
            BuildError(
                failure=BuildFailure.INTERNAL,
                message=(
                    "the build failed with an unexpected internal error "
                    f"({type(exc).__name__})"
                ),
                stage="execution",
                completed_outputs=tuple(completed),
                exception_type=type(exc).__name__,
                diagnostic=traceback.format_exc(),
            )
        )
    finally:
        if job.status is BuildStatus.FAILED:
            _discard(written)


def execute_build(
    request: BuildRequest, *, output_directory: Optional[PathLike] = None
) -> BuildJob:
    """Create a job for ``request``, run it, and return the terminal job.

    The job carries the result, the status and both identities.
    """
    job = BuildJob(request)
    run_job(job, output_directory=output_directory)
    return job


def execute_build_document(
    document: Mapping[str, Any],
    options: BuildOptions,
    *,
    output_directory: Optional[PathLike] = None,
) -> BuildJob:
    """Validate a document and build it, reporting invalidity as a failed job.

    The strict boundary (:func:`request_for_document`) raises on an invalid
    document. This entry point instead returns a ``FAILED`` job carrying
    :attr:`BuildFailure.DOCUMENT_INVALID` and the validator's structured
    errors, which is what a UI or API layer will want. **No geometry is built
    for an invalid document**, and no job ever reaches ``RUNNING``.
    """
    try:
        request = request_for_document(document, options)
    except DocumentValidationError as exc:
        return _document_failure(document, options, exc)
    return execute_build(request, output_directory=output_directory)


# --- internals --------------------------------------------------------------

#: Exporter per file output. Each takes ``(LocalCadResult, path)``.
_EXPORTERS: Mapping[BuildOutput, Any] = {
    BuildOutput.STEP: export_step,
    BuildOutput.IGES: export_iges,
    BuildOutput.STL: export_stl,
}


def _resolve_directory(
    options: BuildOptions, output_directory: Optional[PathLike]
) -> Optional[Path]:
    if not options.writes_files:
        return None
    if output_directory is None:
        wanted = ", ".join(
            output.value for output in FILE_OUTPUTS if options.wants(output)
        )
        raise BuildRequestError(
            f"the requested output(s) {wanted} are written to files, so "
            "run_job needs an output_directory; nothing is written to an "
            "implicit location"
        )
    directory = Path(os.fspath(output_directory))
    if not directory.is_dir():
        raise BuildRequestError(f"{directory} is not a directory")
    return directory


def _export_error(
    output: BuildOutput, exc: Exception, completed: Tuple[BuildOutput, ...]
) -> BuildError:
    """Classify an exporter failure without leaking a path into ``message``.

    Exporter messages name the file they could not write, so the underlying
    text goes to ``diagnostic`` and the public message names only the output
    and the exception type.
    """
    return BuildError(
        failure=BuildFailure.EXPORT_FAILED,
        message=(
            f"the {output.value} output could not be produced "
            f"({type(exc).__name__})"
        ),
        stage=output.value,
        output=output,
        completed_outputs=completed,
        exception_type=type(exc).__name__,
        diagnostic=str(exc),
    )


def _document_failure(
    document: Mapping[str, Any],
    options: BuildOptions,
    exc: DocumentValidationError,
) -> BuildJob:
    """Build a terminal FAILED job for a document that never validated.

    The job cannot hold a ``BuildRequest`` -- there is no ``Part`` -- so it
    carries a request-free result. The document hash is unavailable for the
    same reason: an invalid document has no canonical form.
    """
    job = _InvalidDocumentJob(options)
    job.start()
    job.fail(
        BuildError(
            failure=BuildFailure.DOCUMENT_INVALID,
            message=(
                f"the CAD document violates {len(exc.errors)} V1 rule(s): "
                + "; ".join(sorted({error.rule for error in exc.errors}))
            ),
            stage="validation",
            rule_codes=exc.rule_codes(),
            validation_errors=exc.errors,
        )
    )
    return job


class _InvalidDocumentJob(BuildJob):
    """A job for a document that failed validation.

    An invalid document has no ``Part`` and therefore no canonical form and no
    document hash, so those read as empty rather than being invented. The
    build key is empty for the same reason: there is nothing reproducible to
    key on.
    """

    def __init__(self, options: BuildOptions) -> None:
        self._request = None  # type: ignore[assignment]
        self._options = options
        self._status = BuildStatus.QUEUED
        self._result = None
        self._execution_id = uuid.uuid4().hex
        self._lock = threading.Lock()

    @property
    def request(self) -> Optional[BuildRequest]:  # type: ignore[override]
        return None

    @property
    def options(self) -> BuildOptions:
        return self._options

    @property
    def document_hash(self) -> str:
        return ""

    @property
    def build_key(self) -> str:
        return ""


def _discard(paths: Iterable[Path]) -> None:
    """Delete files this build wrote before it failed.

    A failed build must leave no success-looking artifact. Deletion is
    best-effort: a file that cannot be removed does not turn a classified
    failure into an internal one.
    """
    for path in paths:
        try:
            path.unlink()
        except OSError:  # pragma: no cover - unlikely on a temp directory
            pass


def _rule_codes_in(message: str) -> Tuple[str, ...]:
    """Pull specification rule codes out of an engine message.

    The engine names the rule it enforced in its message text -- ``(rule
    E5)``, ``(rule S9)``. Reading them back gives a UI something structured to
    show without the engine having to grow a second error type. Best-effort:
    an absent code simply yields nothing.
    """
    found = []
    for token in message.replace("(", " ").replace(")", " ").split():
        stripped = token.strip(".,;:'\"")
        if (
            len(stripped) in (2, 3)
            and stripped[0] in "SE"
            and stripped[1:].isdigit()
        ):
            if stripped not in found:
                found.append(stripped)
    return tuple(found)


def _hash_canonical(structure: Mapping[str, Any]) -> str:
    payload = json.dumps(
        structure,
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=False,
    ).encode(CANONICAL_ENCODING)
    return hashlib.new(BUILD_KEY_ALGORITHM, payload).hexdigest()


__all__ = [
    "ALLOWED_TRANSITIONS",
    "BUILD_KEY_ALGORITHM",
    "Artifact",
    "ArtifactKind",
    "ArtifactManifest",
    "ArtifactStorage",
    "BuildArtifact",
    "BuildError",
    "BuildFailure",
    "BuildJob",
    "BuildOptions",
    "BuildOutput",
    "BuildRequest",
    "BuildRequestError",
    "BuildResult",
    "BuildStateError",
    "BuildStatus",
    "CadBuildError",
    "FILE_OUTPUTS",
    "IN_MEMORY_OUTPUTS",
    "TERMINAL_STATUSES",
    "build_key_for",
    "execute_build",
    "execute_build_document",
    "request_for_document",
    "run_job",
]
