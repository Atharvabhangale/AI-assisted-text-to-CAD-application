"""Host side of the isolated local CAD execution boundary.

```
host application
      |            JSON in a controlled workspace
isolated CAD process        (cad_core.isolated_worker)
      |
CAD kernel                  (CadQuery / OpenCascade)
      |
structured result
```

A build runs in a **child process**. If the kernel aborts that process, the
host survives, learns that it aborted, and reports a structured failure --
it never fabricates a successful build, an artifact manifest or a cache
entry. That is the whole objective.

**This is process isolation for crash containment and execution boundaries,
not a security sandbox.** The child runs as the same OS user with broadly the
same filesystem permissions as the host. There is no syscall filtering, no
seccomp, no container, no network isolation, no privilege dropping and no
filesystem jail, and none is claimed. See ``docs/isolated-cad-execution.md``.

What crosses the boundary
-------------------------
Only JSON, in files inside a workspace this module creates:

* **out** -- the canonical CAD document (Stage 15) and the canonical build
  options (Stage 16). No filesystem path, no Python object, no code. The one
  or two directories the child may write to are passed as **launch
  arguments**, from this module's own API.
* **back** -- one response envelope: a status, the child's
  :meth:`~cad_core.build_job.BuildResult.to_dict`, and its structured
  :class:`~cad_core.build_job.BuildError`.

Never used: :mod:`pickle`, ``eval``, ``exec``, ``shell=True``, a command
string built from input, or arbitrary Python object deserialization. The
child is always launched as an argument **list**
(``[sys.executable, "-m", "cad_core.isolated_worker", ...]``) and the request
is data, not a script. There is no operation that runs caller-supplied code.

The B-rep does not cross the boundary
-------------------------------------
A kernel object is never sent. The geometry artifact's *measurements* come
back -- which is all that artifact ever contained (Stage 17) -- but the
in-memory solid exists only in the child and dies with it, and **no B-rep
serialization format was invented**. File-backed artifacts survive as files;
the render model survives as its existing canonical JSON (Stage 18's
:func:`~cad_core.artifact_registry.canonical_render_bytes`) and is
reconstructed here.

Results stay Stage 16/17/18 concepts
------------------------------------
:class:`IsolatedExecution` carries the document hash, the build key, a real
:class:`~cad_core.artifact_registry.ArtifactManifest`, cache metadata and a
structured failure. No parallel result architecture: the manifest is rebuilt
with the artifact layer's own reader, and every file it names is then
**re-verified against the bytes on disk**, because a separate process's
claims are checked rather than trusted.

Kernel imports in the host
--------------------------
Stated plainly: this module imports :mod:`cad_core.build_job` and
:mod:`cad_core.local_build_cache`, which import CadQuery. So the host
*loads* the kernel library even though it does not *execute* geometry through
it. What is isolated is execution -- where the measured crash hazard is --
not the import.
"""

from __future__ import annotations

import hashlib
import os
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace as _dataclass_replace
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

import cad_core
from cad_core.artifact_registry import (
    ArtifactError,
    ArtifactKind,
    ArtifactManifest,
    ArtifactStorage,
    file_checksum,
    manifest_from_dict,
    render_model_from_canonical_bytes,
)
from cad_core.build_job import (
    FILE_OUTPUTS,
    BuildFailure,
    BuildOptions,
    BuildOutput,
    BuildRequest,
    BuildRequestError,
    build_key_of,
)
from cad_core.isolated_worker import (
    CACHE_ROOT_FLAG,
    EXIT_PROTOCOL_ERROR,
    IPC_PROTOCOL_VERSION,
    OPERATION_BUILD,
    OUTPUT_DIRECTORY_FLAG,
    REQUEST_ENVELOPE_FIELDS,
    REQUEST_FILENAME,
    RESPONSE_ENVELOPE_FIELDS,
    RESPONSE_FILENAME,
    STATUS_EXIT_CODES,
    WorkerStatus,
)
from cad_core.local_build_cache import CacheEntry, LocalBuildCache
from cad_core.model import Part
from cad_core.render_model import RenderModel
from cad_core.serialization import serialize_part

PathLike = Union[str, "os.PathLike[str]"]

#: How long an isolated build may take before the child is killed.
#:
#: Measured on this machine: a child costs about 2.4-2.9 s wall clock, of
#: which ~2.05 s is importing CadQuery and OpenCascade; the V1 builds
#: themselves take ~13 ms. Sixty seconds is therefore roughly twenty times
#: the measured cost, leaving room for a slower machine or a heavier part.
#: It is an explicit parameter everywhere and is never hidden.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: How long to wait for a killed child to be reaped before giving up on it.
KILL_GRACE_SECONDS = 10.0

#: Prefix of the per-invocation workspace directory.
WORKSPACE_PREFIX = "cad-isolated-"

#: How much captured ``stdout``/``stderr`` is kept for diagnosis. Bounded so
#: a chatty kernel cannot grow a result without limit.
MAX_DIAGNOSTIC_CHARACTERS = 4000

#: Environment variables the child inherits **if the host has them**: the
#: dynamic loader's and the platform's, nothing else.
#:
#: Everything else is dropped, so no API key, token or cloud credential can
#: reach the child even by accident. No credential handling exists here, and
#: nothing in this stage needs one.
INHERITED_ENVIRONMENT_NAMES: Tuple[str, ...] = (
    "PATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "COMSPEC",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
    # Windows has no `pwd` fallback for `Path("~").expanduser()`, so a library
    # that resolves the home directory at import time -- `ezdxf`, which
    # CadQuery imports for its DXF exporter -- raises without these. Paths,
    # like the loader variables above, and not credentials.
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
)

#: Environment variables the child is **given**, whatever the host's are.
#: ``TMPDIR``/``TEMP``/``TMP`` point at the workspace, so a library's
#: temporary file lands in the controlled directory and is cleaned with it.
ASSIGNED_ENVIRONMENT_NAMES: Tuple[str, ...] = (
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "PYTHONDONTWRITEBYTECODE",
    "TMPDIR",
    "TEMP",
    "TMP",
)


class IsolationError(Exception):
    """A host-side usage error, raised before any child is launched."""


class IsolationOutcome(Enum):
    """How one isolated invocation ended, from the host's point of view."""

    #: The child reported a successful build, its exit code agreed, and every
    #: artifact it named was verified.
    SUCCEEDED = "succeeded"

    #: The child reported that the CAD document violates the V1 static rules.
    VALIDATION_FAILED = "validation_failed"

    #: The child reported a controlled build failure: the engine, a geometric
    #: rule (E1-E5), an exporter, or artifact publication.
    BUILD_FAILED = "build_failed"

    #: The child's own harness failed outside the build, and said so.
    WORKER_ERROR = "worker_error"

    #: The request or the response could not be spoken: a malformed or
    #: wrongly versioned envelope, an inconsistent status and exit code, or an
    #: artifact the child named that does not match the bytes on disk.
    PROTOCOL_ERROR = "protocol_error"

    #: The child ended without leaving a usable response -- **the crash case**.
    PROCESS_FAILED = "process_failed"

    #: The child exceeded its timeout and was killed.
    TIMED_OUT = "timed_out"


#: Outcomes in which a build genuinely succeeded.
SUCCESSFUL_OUTCOMES: Tuple[IsolationOutcome, ...] = (IsolationOutcome.SUCCEEDED,)

#: Worker status to host outcome. A worker status is a *claim*; the host still
#: checks the exit code and the artifacts before honouring ``SUCCEEDED``.
WORKER_STATUS_OUTCOMES: Mapping[WorkerStatus, IsolationOutcome] = {
    WorkerStatus.SUCCEEDED: IsolationOutcome.SUCCEEDED,
    WorkerStatus.VALIDATION_FAILED: IsolationOutcome.VALIDATION_FAILED,
    WorkerStatus.BUILD_FAILED: IsolationOutcome.BUILD_FAILED,
    WorkerStatus.PROTOCOL_ERROR: IsolationOutcome.PROTOCOL_ERROR,
    WorkerStatus.INTERNAL_ERROR: IsolationOutcome.WORKER_ERROR,
}


@dataclass(frozen=True)
class IsolatedFailure:
    """Why an isolated invocation did not succeed.

    :attr:`message` is the **stable public sentence**, the same contract
    Stage 16 established: no traceback, no filesystem path, nothing from the
    environment, and never the child's ``stderr``. The child's streams are
    kept separately on :attr:`IsolatedExecution.diagnostic`.
    """

    outcome: IsolationOutcome
    message: str

    #: The Stage 16 classification, when the child got far enough to make one.
    failure: Optional[BuildFailure] = None

    #: Which stage failed: ``"validation"``, ``"geometry"``, an output's name,
    #: or ``"process"`` for a failure of the child itself.
    stage: str = "process"

    #: The output being produced when the build failed, when applicable.
    output: Optional[BuildOutput] = None

    #: Specification rule codes (S1-S20, E1-E5).
    rule_codes: Tuple[str, ...] = ()

    #: The validator's own structured errors, as plain data.
    validation_errors: Tuple[Mapping[str, Any], ...] = ()

    #: Class name of an unexpected exception. Diagnostic only.
    exception_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "message": self.message,
            "failure": self.failure.value if self.failure is not None else None,
            "stage": self.stage,
            "output": self.output.value if self.output is not None else None,
            "rule_codes": list(self.rule_codes),
            "validation_errors": [dict(error) for error in self.validation_errors],
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True)
class IsolatedExecution:
    """The host-side result of one isolated invocation.

    Deliberately the Stage 16/17/18 vocabulary: a document hash, a build key,
    an :class:`~cad_core.artifact_registry.ArtifactManifest`, cache metadata
    and a structured failure.
    """

    outcome: IsolationOutcome
    operation: str = OPERATION_BUILD

    #: The Stage 16 build key, as the child computed it. Empty when the
    #: document never validated, because an invalid document has none.
    build_key: Optional[str] = None

    #: The Stage 15 canonical document hash, as the child computed it.
    document_hash: Optional[str] = None

    #: Exactly the artifacts the isolated build published, rebuilt here and
    #: re-verified against the bytes on disk. ``None`` when nothing succeeded.
    manifest: Optional[ArtifactManifest] = None

    #: The render model, reconstructed from its canonical bytes when
    #: ``render`` was requested. The B-rep is **not** transferred.
    render_model: Optional[RenderModel] = None

    failure: Optional[IsolatedFailure] = None

    #: What the child said about itself, when it said anything.
    worker_status: Optional[WorkerStatus] = None

    #: The child's exit code; ``None`` if no child ran or it was killed.
    exit_code: Optional[int] = None

    #: Whether a child process was launched at all. ``False`` for a cache hit.
    child_launched: bool = False

    #: The child's process id, for checking that it really is gone. ``None``
    #: when no child ran.
    child_pid: Optional[int] = None

    #: ``True`` only if a killed child could not be reaped within
    #: :data:`KILL_GRACE_SECONDS`. Always ``False`` in practice -- a returned
    #: result means the child has been waited for and no longer exists.
    child_abandoned: bool = False

    #: A non-build operation's own result, as plain data. Diagnostic only;
    #: a build's result is carried by the fields above instead.
    worker_result: Optional[Mapping[str, Any]] = None

    #: The child's execution id -- a *new* one for every invocation, and part
    #: of no identity (Stage 16).
    execution_id: Optional[str] = None

    #: Whether the artifacts came from the Stage 18 cache without building.
    cache_hit: bool = False

    #: Whether this invocation's build was published to the cache.
    cache_published: bool = False

    #: The timeout this invocation was given, in seconds.
    timeout_seconds: Optional[float] = None

    #: The workspace this invocation used. Removed by the time this is
    #: returned unless the caller asked to keep it.
    workspace: Optional[str] = None

    #: The child's ``stdout`` and ``stderr``, truncated. **Diagnostic only**,
    #: never a public message, and never in :meth:`to_dict`.
    diagnostic: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.outcome in SUCCESSFUL_OUTCOMES

    def artifact(self, kind: ArtifactKind):
        """The artifact of one kind, or ``None``."""
        if self.manifest is None:
            return None
        return self.manifest.artifact(kind)

    def produced_kinds(self) -> Tuple[ArtifactKind, ...]:
        return () if self.manifest is None else self.manifest.kinds()

    def to_dict(self) -> Dict[str, Any]:
        """Plain JSON-compatible data. Excludes the child's streams."""
        return {
            "outcome": self.outcome.value,
            "operation": self.operation,
            "build_key": self.build_key,
            "document_hash": self.document_hash,
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "failure": self.failure.to_dict() if self.failure else None,
            "worker_status": (
                self.worker_status.value if self.worker_status else None
            ),
            "exit_code": self.exit_code,
            "child_launched": self.child_launched,
            "child_pid": self.child_pid,
            "child_abandoned": self.child_abandoned,
            "worker_result": (
                dict(self.worker_result) if self.worker_result is not None else None
            ),
            "execution_id": self.execution_id,
            "cache_hit": self.cache_hit,
            "cache_published": self.cache_published,
            "timeout_seconds": self.timeout_seconds,
        }


# --- public API -------------------------------------------------------------


def execute_isolated(
    request: BuildRequest,
    *,
    output_directory: Optional[PathLike] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    workspace_root: Optional[PathLike] = None,
    keep_workspace: bool = False,
) -> IsolatedExecution:
    """Build ``request`` in a child process and return a structured result.

    The request travels as its **canonical CAD document** and canonical
    options, so the child validates it with the existing validator exactly as
    an in-process build would.

    Args:
        request: A :class:`~cad_core.build_job.BuildRequest`.
        output_directory: Where file outputs are written. Required when the
            options ask for STEP, IGES or STL -- the same rule
            :func:`~cad_core.build_job.run_job` applies, and for the same
            reason: nothing is written to a location the caller did not name.
            It is also the only directory besides the workspace the child may
            write to.
        timeout_seconds: The child is killed after this long.
        workspace_root: Where the per-invocation workspace is created.
            Defaults to the system temporary directory. Never the cache.
        keep_workspace: Leave the workspace behind for diagnosis. Off by
            default; the workspace is otherwise always removed.

    Raises:
        IsolationError: for a usage error, before any child is launched.
    """
    if not isinstance(request, BuildRequest):
        raise IsolationError(
            f"expected a BuildRequest; got {type(request).__name__}"
        )
    return execute_isolated_document(
        serialize_part(request.part),
        request.options,
        output_directory=output_directory,
        timeout_seconds=timeout_seconds,
        workspace_root=workspace_root,
        keep_workspace=keep_workspace,
    )


def execute_isolated_document(
    document: Mapping[str, Any],
    options: BuildOptions,
    *,
    output_directory: Optional[PathLike] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    workspace_root: Optional[PathLike] = None,
    keep_workspace: bool = False,
    cache_root: Optional[PathLike] = None,
) -> IsolatedExecution:
    """Build a canonical CAD document in a child process.

    The document is sent as given: an invalid one comes back as
    :attr:`IsolationOutcome.VALIDATION_FAILED` with the validator's own
    structured errors, which mirrors
    :func:`~cad_core.build_job.execute_build_document` rather than raising.
    """
    if not isinstance(document, Mapping):
        raise IsolationError(
            f"a CAD document is a mapping; got {type(document).__name__}"
        )
    if not isinstance(options, BuildOptions):
        raise IsolationError(
            f"options must be a BuildOptions; got {type(options).__name__}"
        )
    if options.writes_files and output_directory is None:
        wanted = ", ".join(
            output.value
            for output in options.requested
            if output in FILE_OUTPUTS
        )
        raise IsolationError(
            f"the requested output(s) {wanted} are written to files, so an "
            "output_directory is required; the isolated workspace is removed "
            "after execution and is not a place to leave artifacts"
        )
    return invoke_worker(
        operation=OPERATION_BUILD,
        payload={"document": dict(document), "options": options.canonical()},
        output_directory=output_directory,
        cache_root=cache_root,
        timeout_seconds=timeout_seconds,
        workspace_root=workspace_root,
        keep_workspace=keep_workspace,
    )


def get_or_build_isolated(
    request: BuildRequest,
    cache: LocalBuildCache,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    workspace_root: Optional[PathLike] = None,
) -> IsolatedExecution:
    """Serve ``request`` from the cache, or build it in a child process.

    The Stage 18 cache is used as it is; nothing about it was redesigned.

    1. the **host** validates the cache entry for the request's build key. On
       a hit it returns immediately: **no child process is launched**, and
       :attr:`IsolatedExecution.child_launched` is ``False``;
    2. on a miss the child builds *and publishes*, so the render model and the
       B-rep never have to cross the boundary for the cache's sake;
    3. the host then re-validates the entry -- recomputing every checksum from
       the cached bytes -- and reports the **cache-resident** artifacts, whose
       paths point inside the cache root rather than at the workspace that is
       about to be removed.

    A build that succeeds but whose entry does not validate is still reported
    as a success, with ``cache_published`` ``False``.
    """
    if not isinstance(request, BuildRequest):
        raise IsolationError(
            f"expected a BuildRequest; got {type(request).__name__}"
        )
    if not isinstance(cache, LocalBuildCache):
        raise IsolationError(
            f"expected a LocalBuildCache; got {type(cache).__name__}"
        )
    build_key = request.build_key
    document_hash = request.document_hash
    required = request.options.requested
    cached = cached_execution(request, cache, timeout_seconds=timeout_seconds)
    if cached is not None:
        return cached

    workspace = _make_workspace(workspace_root)
    outputs = workspace / "outputs"
    outputs.mkdir()
    try:
        execution = invoke_worker(
            operation=OPERATION_BUILD,
            payload={
                "document": dict(serialize_part(request.part)),
                "options": request.options.canonical(),
            },
            output_directory=outputs,
            cache_root=cache.root,
            timeout_seconds=timeout_seconds,
            workspace=workspace,
            keep_workspace=True,  # removed by this function's own cleanup
        )
        if execution.outcome is not IsolationOutcome.SUCCEEDED:
            return execution
        published = cache.lookup(
            build_key, document_hash=document_hash, required_kinds=required
        )
        if not published.hit or published.entry is None:
            return _replace(
                execution, cache_hit=False, cache_published=False
            )
        return _replace(
            execution,
            manifest=published.entry.manifest,
            render_model=published.entry.render_model,
            cache_hit=False,
            cache_published=True,
        )
    finally:
        _remove_tree(workspace)


def cached_execution(
    request: BuildRequest,
    cache: LocalBuildCache,
    *,
    timeout_seconds: Optional[float] = None,
) -> Optional[IsolatedExecution]:
    """The cached result for ``request``, or ``None`` on a miss.

    A lookup and nothing more: **no child process is launched**, and no build
    is run. The Stage 18 cache re-verifies the entry -- recomputing every
    checksum from the cached bytes -- so a hit is a validated entry, never a
    transcription of a stored manifest.

    This is the first step of :func:`get_or_build_isolated`, available on its
    own so a caller can ask whether a build is already available without
    building it.
    """
    if not isinstance(request, BuildRequest):
        raise IsolationError(
            f"expected a BuildRequest; got {type(request).__name__}"
        )
    return cached_execution_for_key(
        request.build_key,
        cache,
        document_hash=request.document_hash,
        required_kinds=request.options.requested,
        timeout_seconds=timeout_seconds,
    )


def cached_execution_for_key(
    build_key: str,
    cache: LocalBuildCache,
    *,
    document_hash: Optional[str] = None,
    required_kinds: Iterable[ArtifactKind] = (),
    timeout_seconds: Optional[float] = None,
) -> Optional[IsolatedExecution]:
    """The cached result for a **build key**, or ``None`` on a miss.

    The same lookup as :func:`cached_execution`, for a caller that has a build
    key and not the document it came from -- a read-only retrieval, where the
    key *is* the whole request. One definition of "a cache hit as a structured
    execution" serves both.

    A lookup and nothing more: **no child process is launched**, no build is
    run and nothing is written. The Stage 18 cache re-verifies the entry,
    recomputing every checksum from the cached bytes, so a hit is a validated
    entry rather than a transcription of a stored manifest.

    ``document_hash`` and ``required_kinds``, when given, are verifications
    applied to whatever the entry claims -- never part of the lookup key.

    Raises:
        IsolationError: if ``cache`` is not a cache. A malformed build key is
            the cache's own :class:`~cad_core.local_build_cache.CacheError`;
            a caller taking a key from outside should check
            :func:`~cad_core.build_job.is_build_key` first.
    """
    if not isinstance(cache, LocalBuildCache):
        raise IsolationError(
            f"expected a LocalBuildCache; got {type(cache).__name__}"
        )
    lookup = cache.lookup(
        build_key,
        document_hash=document_hash,
        required_kinds=tuple(required_kinds),
    )
    if not lookup.hit or lookup.entry is None:
        return None
    if not _entry_matches_key(build_key, lookup.entry):
        return None
    return IsolatedExecution(
        outcome=IsolationOutcome.SUCCEEDED,
        build_key=build_key,
        document_hash=lookup.entry.document_hash,
        manifest=lookup.entry.manifest,
        render_model=lookup.entry.render_model,
        worker_status=None,
        exit_code=None,
        child_launched=False,
        cache_hit=True,
        cache_published=False,
        timeout_seconds=timeout_seconds,
    )


def _entry_matches_key(build_key: str, entry: CacheEntry) -> bool:
    """Whether a cache entry's own claims re-derive the key that found it.

    A build key is a **commitment** to the document hash and the canonical
    output set (Stage 16), so an entry can be checked against the key a caller
    asked for: recompute the key from the document hash and the artifact kinds
    the entry claims, and require it to match.

    This matters only for a key-only retrieval. :func:`cached_execution` has
    the request, so it passes the document hash as a verification and the
    cache checks it directly; a caller with nothing but a key has no hash to
    compare -- **except the one the key itself commits to**. Measured while
    writing Stage 24: without this, editing ``document_hash`` in a cached
    manifest made a retrieval report the edited value, because a cache
    manifest's artifact records carry no document hash of their own and the
    entry therefore could not contradict itself.

    No new hashing scheme: :func:`~cad_core.build_job.build_key_of` is the
    build key's own computation.
    """
    try:
        expected = build_key_of(entry.document_hash, entry.manifest.kinds())
    except (BuildRequestError, TypeError, ValueError):
        return False
    return expected == build_key


def invoke_worker(
    *,
    operation: str,
    payload: Any = None,
    output_directory: Optional[PathLike] = None,
    cache_root: Optional[PathLike] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    workspace_root: Optional[PathLike] = None,
    workspace: Optional[Path] = None,
    keep_workspace: bool = False,
) -> IsolatedExecution:
    """Run one worker operation in a child process. The transport primitive.

    :func:`execute_isolated`, :func:`execute_isolated_document` and
    :func:`get_or_build_isolated` all go through this, always with
    ``operation="build"``. The worker's ``diagnostic:`` operations are
    reachable only from here, and only by naming them explicitly; they exist
    to exercise this function's own failure classification.

    Never uses a shell, never builds a command string, and never sends
    anything but JSON data.
    """
    if not isinstance(operation, str) or not operation:
        raise IsolationError("an operation name is required")
    if timeout_seconds is None or timeout_seconds <= 0:
        raise IsolationError("timeout_seconds must be a positive number")
    owned = workspace is None
    area = _make_workspace(workspace_root) if owned else workspace
    try:
        _write_request(area, operation=operation, payload=payload)
        completed = _launch(
            area,
            output_directory=output_directory,
            cache_root=cache_root,
            timeout_seconds=timeout_seconds,
        )
        return _classify(
            area,
            operation=operation,
            completed=completed,
            timeout_seconds=timeout_seconds,
            keep_workspace=keep_workspace,
        )
    finally:
        if owned and not keep_workspace:
            _remove_tree(area)


# --- launching --------------------------------------------------------------


@dataclass(frozen=True)
class _Completed:
    """What the operating system told us about the child."""

    exit_code: Optional[int]
    timed_out: bool
    still_running: bool
    pid: int
    diagnostic: str


def _launch(
    workspace: Path,
    *,
    output_directory: Optional[PathLike],
    cache_root: Optional[PathLike],
    timeout_seconds: float,
) -> _Completed:
    """Run the worker as a child process, killing it if it overruns.

    The command is an argument **list** -- no shell, no string
    interpolation -- and the only paths in it are ones this module or its
    caller named.
    """
    command: List[str] = [
        sys.executable,
        "-m",
        "cad_core.isolated_worker",
        str(workspace),
    ]
    if output_directory is not None:
        command.extend([OUTPUT_DIRECTORY_FLAG, str(Path(os.fspath(output_directory)))])
    if cache_root is not None:
        command.extend([CACHE_ROOT_FLAG, str(Path(os.fspath(cache_root)))])

    process = subprocess.Popen(  # noqa: S603 - list form, no shell
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(workspace),
        env=child_environment(workspace),
        shell=False,
        close_fds=True,
    )
    timed_out = False
    try:
        out, err = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        try:
            out, err = process.communicate(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL is final
            out, err = b"", b""
    still_running = process.poll() is None
    return _Completed(
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        still_running=still_running,
        pid=process.pid,
        diagnostic=_diagnostic_text(out, err),
    )


def child_environment(workspace: Path) -> Dict[str, str]:
    """The environment the child receives. Minimal, and an allowlist.

    Loader and platform variables are inherited *if present*; everything else
    in the host's environment is dropped, so no API key, token or credential
    can reach the child. ``PYTHONPATH`` is derived from this package's own
    location, so no absolute path is hard-coded and no current-working-
    directory assumption is made. ``TMPDIR``/``TEMP``/``TMP`` point at the
    workspace.
    """
    environment: Dict[str, str] = {}
    for name in INHERITED_ENVIRONMENT_NAMES:
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    environment["PYTHONPATH"] = _child_python_path()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    for name in ("TMPDIR", "TEMP", "TMP"):
        environment[name] = str(workspace)
    return environment


def _child_python_path() -> str:
    """``PYTHONPATH`` for the child: this package's root, then the host's.

    Derived from :mod:`cad_core`'s own ``__file__``, so it works from a source
    checkout and from an installed package alike, with nothing hard-coded and
    no reliance on the current working directory.
    """
    entries: List[str] = [str(Path(cad_core.__file__).resolve().parents[1])]
    for entry in (os.environ.get("PYTHONPATH") or "").split(os.pathsep):
        if entry and entry not in entries:
            entries.append(entry)
    return os.pathsep.join(entries)


def _diagnostic_text(out: bytes, err: bytes) -> str:
    """The child's streams, labelled and bounded. Diagnostic only."""
    parts: List[str] = []
    for label, raw in (("stdout", out), ("stderr", err)):
        text = (raw or b"").decode("utf-8", "replace").strip()
        if text:
            parts.append(f"[{label}] {text}")
    joined = "\n".join(parts)
    if len(joined) > MAX_DIAGNOSTIC_CHARACTERS:
        joined = joined[:MAX_DIAGNOSTIC_CHARACTERS] + "... (truncated)"
    return joined


# --- classifying ------------------------------------------------------------


def _classify(
    workspace: Path,
    *,
    operation: str,
    completed: _Completed,
    timeout_seconds: float,
    keep_workspace: bool,
) -> IsolatedExecution:
    """Turn what the child left behind into a structured host result.

    Order matters: a timeout first, then the presence of a response, then the
    response's own shape, and only then what the child *claims*. A zero exit
    code is never taken as success on its own -- the abort case proves why.
    """
    base = dict(
        operation=operation,
        exit_code=completed.exit_code,
        child_launched=True,
        child_pid=completed.pid,
        child_abandoned=completed.still_running,
        timeout_seconds=timeout_seconds,
        workspace=str(workspace),
        diagnostic=completed.diagnostic or None,
    )

    if completed.timed_out:
        return IsolatedExecution(
            outcome=IsolationOutcome.TIMED_OUT,
            failure=IsolatedFailure(
                outcome=IsolationOutcome.TIMED_OUT,
                message=(
                    "the isolated CAD process exceeded its "
                    f"{timeout_seconds:g} second timeout and was terminated"
                ),
                stage="process",
            ),
            **base,
        )

    raw = _read_response(workspace)
    if raw is None:
        # No usable response. This is the crash case: the child may even have
        # exited zero, and it is still not a success.
        outcome = (
            IsolationOutcome.PROTOCOL_ERROR
            if completed.exit_code == EXIT_PROTOCOL_ERROR
            else IsolationOutcome.PROCESS_FAILED
        )
        return IsolatedExecution(
            outcome=outcome,
            failure=IsolatedFailure(
                outcome=outcome,
                message=(
                    "the isolated CAD process ended without a result "
                    f"(exit code {completed.exit_code})"
                ),
                stage="process",
            ),
            **base,
        )

    problem = _envelope_problem(raw, operation)
    if problem is not None:
        return _protocol_failure(problem, base)

    status = WorkerStatus(raw["status"])
    expected_exit = STATUS_EXIT_CODES[status]
    if completed.exit_code != expected_exit:
        return _protocol_failure(
            f"the child reported {status.value!r} but exited with "
            f"{completed.exit_code} rather than {expected_exit}",
            base,
            worker_status=status,
        )

    outcome = WORKER_STATUS_OUTCOMES[status]
    result = raw["result"]
    error = raw["error"]

    if outcome is not IsolationOutcome.SUCCEEDED:
        return IsolatedExecution(
            outcome=outcome,
            build_key=_text_or_none(result, "build_key"),
            document_hash=_text_or_none(result, "document_hash"),
            execution_id=_text_or_none(result, "execution_id"),
            failure=_failure_from(error, outcome),
            worker_status=status,
            **base,
        )

    if operation != OPERATION_BUILD:
        # A diagnostic operation carries no build result; whatever it did
        # report travels as plain data.
        return IsolatedExecution(
            outcome=IsolationOutcome.SUCCEEDED,
            worker_status=status,
            worker_result=result if isinstance(result, Mapping) else None,
            **base,
        )

    try:
        manifest, render, identity = _restore(workspace, raw)
    except _ResponseProblem as problem:
        return _protocol_failure(str(problem), base, worker_status=status)

    return IsolatedExecution(
        outcome=IsolationOutcome.SUCCEEDED,
        build_key=identity["build_key"],
        document_hash=identity["document_hash"],
        execution_id=identity["execution_id"],
        manifest=manifest,
        render_model=render,
        worker_status=status,
        cache_hit=bool(identity["cache_hit"]),
        **base,
    )


class _ResponseProblem(Exception):
    """The response's content did not survive verification."""


def _read_response(workspace: Path) -> Optional[Dict[str, Any]]:
    """The response envelope, or ``None`` if there is nothing usable."""
    location = workspace / RESPONSE_FILENAME
    if not location.is_file():
        return None
    try:
        raw = json.loads(location.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}  # present but unreadable: a protocol error, not a crash
    return raw if isinstance(raw, dict) else {}


def _envelope_problem(raw: Mapping[str, Any], operation: str) -> Optional[str]:
    """What is wrong with the response envelope, if anything."""
    if set(raw) != set(RESPONSE_ENVELOPE_FIELDS):
        return "the child's response is not a response envelope"
    if raw["ipc_protocol_version"] != IPC_PROTOCOL_VERSION:
        return (
            f"the child speaks IPC protocol {raw['ipc_protocol_version']!r}; "
            f"this host speaks {IPC_PROTOCOL_VERSION!r}"
        )
    if raw["operation"] != operation:
        return "the child answered a different operation"
    try:
        WorkerStatus(raw["status"])
    except ValueError:
        return f"the child reported an unknown status {raw['status']!r}"
    if not isinstance(raw["transport"], Mapping):
        return "the child's transport block is malformed"
    return None


def _restore(
    workspace: Path, raw: Mapping[str, Any]
) -> Tuple[ArtifactManifest, Optional[RenderModel], Dict[str, Any]]:
    """Rebuild the manifest and the render model, verifying both.

    The child is a separate process, so its claims are checked: every
    file-backed artifact must exist, its size must match and its SHA-256 must
    be recomputed from the bytes on disk; the render model must reconstruct
    from its canonical bytes and match its recorded checksum.
    """
    result = raw["result"]
    if not isinstance(result, Mapping):
        raise _ResponseProblem("the child reported success with no result")
    for name in ("build_key", "document_hash", "execution_id", "manifest"):
        if name not in result:
            raise _ResponseProblem(f"the child's result has no {name!r}")
    try:
        manifest = manifest_from_dict(result["manifest"])
    except ArtifactError as exc:
        raise _ResponseProblem(f"the child's manifest is not one ({exc})") from None
    if manifest.build_key != result["build_key"]:
        raise _ResponseProblem("the child's manifest names another build")
    if manifest.document_hash != result["document_hash"]:
        raise _ResponseProblem("the child's manifest names another document")

    for artifact in manifest.artifacts:
        if artifact.storage is not ArtifactStorage.FILE:
            continue
        if artifact.path is None:
            raise _ResponseProblem(
                f"the {artifact.kind.value} artifact has no path"
            )
        location = Path(artifact.path)
        if not location.is_file():
            raise _ResponseProblem(
                f"the {artifact.kind.value} artifact the child named is not there"
            )
        if location.stat().st_size != artifact.size_bytes:
            raise _ResponseProblem(
                f"the {artifact.kind.value} artifact's size is not the "
                "reported one"
            )
        if file_checksum(location) != artifact.checksum:
            raise _ResponseProblem(
                f"the {artifact.kind.value} artifact's bytes do not match the "
                "reported checksum"
            )

    render: Optional[RenderModel] = None
    relative = raw["transport"].get("render_payload")
    recorded = manifest.artifact(ArtifactKind.RENDER)
    if relative is not None:
        if not isinstance(relative, str) or ".." in Path(relative).parts:
            raise _ResponseProblem("the render payload's location is malformed")
        payload = workspace / relative
        if not payload.is_file():
            raise _ResponseProblem("the render payload is missing")
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        if recorded is None or digest != recorded.checksum:
            raise _ResponseProblem(
                "the render payload does not match its reported checksum"
            )
        try:
            render = render_model_from_canonical_bytes(payload.read_bytes())
        except ArtifactError as exc:
            raise _ResponseProblem(
                f"the render payload is not a render model ({exc})"
            ) from None
    elif recorded is not None:
        raise _ResponseProblem(
            "the child reported a render artifact but sent no render payload"
        )

    return (
        manifest,
        render,
        {
            "build_key": result["build_key"],
            "document_hash": result["document_hash"],
            "execution_id": result["execution_id"],
            "cache_hit": result.get("cache_hit", False),
        },
    )


def _failure_from(
    error: Any, outcome: IsolationOutcome
) -> IsolatedFailure:
    """Rebuild the child's structured failure, keeping its public message."""
    if not isinstance(error, Mapping):
        return IsolatedFailure(
            outcome=outcome,
            message="the isolated CAD process reported a failure",
            stage="process",
        )
    failure: Optional[BuildFailure] = None
    raw_failure = error.get("failure")
    if isinstance(raw_failure, str):
        try:
            failure = BuildFailure(raw_failure)
        except ValueError:
            failure = None
    output: Optional[BuildOutput] = None
    raw_output = error.get("output")
    if isinstance(raw_output, str):
        try:
            output = BuildOutput(raw_output)
        except ValueError:
            output = None
    message = error.get("message")
    validation = error.get("validation_errors")
    return IsolatedFailure(
        outcome=outcome,
        message=(
            message
            if isinstance(message, str) and message
            else "the isolated CAD process reported a failure"
        ),
        failure=failure,
        stage=str(error.get("stage") or "process"),
        output=output,
        rule_codes=tuple(
            code for code in (error.get("rule_codes") or ()) if isinstance(code, str)
        ),
        validation_errors=tuple(
            dict(item) for item in (validation or ()) if isinstance(item, Mapping)
        ),
        exception_type=(
            error.get("exception_type")
            if isinstance(error.get("exception_type"), str)
            else None
        ),
    )


def _protocol_failure(
    message: str,
    base: Mapping[str, Any],
    *,
    worker_status: Optional[WorkerStatus] = None,
) -> IsolatedExecution:
    return IsolatedExecution(
        outcome=IsolationOutcome.PROTOCOL_ERROR,
        failure=IsolatedFailure(
            outcome=IsolationOutcome.PROTOCOL_ERROR,
            message=message,
            stage="protocol",
        ),
        worker_status=worker_status,
        **base,
    )


# --- workspace --------------------------------------------------------------


def _make_workspace(workspace_root: Optional[PathLike]) -> Path:
    """Create a controlled workspace for one invocation.

    A fresh directory per invocation, holding the protocol files and nothing
    the caller supplied. Deliberately **not** inside the build cache: the
    cache holds published artifacts, and transient protocol scratch has no
    business there.
    """
    if workspace_root is None:
        return Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX))
    parent = Path(os.fspath(workspace_root))
    if not parent.is_dir():
        raise IsolationError(f"{parent} is not a directory")
    return Path(tempfile.mkdtemp(prefix=WORKSPACE_PREFIX, dir=str(parent)))


def _write_request(workspace: Path, *, operation: str, payload: Any) -> None:
    """Write the request envelope the child will read."""
    envelope = {
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "operation": operation,
        "request": payload,
    }
    assert set(envelope) == set(REQUEST_ENVELOPE_FIELDS)
    (workspace / REQUEST_FILENAME).write_bytes(
        json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode("utf-8")
    )


def _remove_tree(path: Path) -> None:
    """Remove a workspace. Best-effort, and never the reason a call fails."""
    shutil.rmtree(path, ignore_errors=True)


# --- internals --------------------------------------------------------------


def _text_or_none(result: Any, name: str) -> Optional[str]:
    if not isinstance(result, Mapping):
        return None
    value = result.get(name)
    return value if isinstance(value, str) and value else None


def _replace(execution: IsolatedExecution, **changes: Any) -> IsolatedExecution:
    """A copy of ``execution`` with some fields changed."""
    return _dataclass_replace(execution, **changes)


__all__ = [
    "ASSIGNED_ENVIRONMENT_NAMES",
    "DEFAULT_TIMEOUT_SECONDS",
    "INHERITED_ENVIRONMENT_NAMES",
    "KILL_GRACE_SECONDS",
    "MAX_DIAGNOSTIC_CHARACTERS",
    "SUCCESSFUL_OUTCOMES",
    "WORKER_STATUS_OUTCOMES",
    "WORKSPACE_PREFIX",
    "IsolatedExecution",
    "IsolatedFailure",
    "IsolationError",
    "IsolationOutcome",
    "cached_execution",
    "cached_execution_for_key",
    "child_environment",
    "execute_isolated",
    "execute_isolated_document",
    "get_or_build_isolated",
    "invoke_worker",
]
