"""Child-process entrypoint for one isolated local CAD build.

Run as a module, never imported for its side effects::

    python -m cad_core.isolated_worker <workspace> [--output-directory DIR]
                                                   [--cache-root DIR]

The host side is :mod:`cad_core.isolated_execution`
(``docs/isolated-cad-execution.md``). This module is the other half of that
boundary: it reads one request, runs the **existing** build machinery, writes
one structured response and exits with a meaningful code.

**This is not a security sandbox.** The child runs as the same OS user with
broadly the same filesystem permissions as the host. What it provides is
crash containment, a process boundary and a protocol boundary -- nothing
about syscalls, containers, networks, privileges or a filesystem jail. See
the host module and the documentation.

No CAD semantics live here
--------------------------
This module parses a protocol envelope and calls
:func:`cad_core.build_job.execute_build_document`,
:func:`cad_core.build_job.request_for_document` and
:func:`cad_core.local_build_cache.get_or_build`. It does not validate a
document itself, does not build geometry itself, does not write an exporter,
and holds no second copy of the CAD schema. It never executes
caller-supplied Python: there is no ``eval``, no ``exec``, no ``pickle``, no
shell, and the request carries a **CAD document**, not code.

Channels
--------
The protocol lives in **files inside the workspace**, not on a stream:

===================  =======================================================
``request.json``     written by the host before launch; the whole input
``response.json``    written by this process; the whole output, published
                     with :func:`os.replace` so a partial write is never
                     readable
``render.json``      the render model's canonical bytes, when ``render`` was
                     requested; see :mod:`cad_core.isolated_execution`
===================  =======================================================

``stdout`` and ``stderr`` are **diagnostic only**. OpenCascade writes
directly to the process's file descriptors, so nothing machine-readable may
share a stream with it -- a measured constraint, not a precaution.

Exit codes
----------
=====  ====================================================================
``0``  success
``10`` a controlled build failure (the engine, a geometric rule, an
       exporter, or an artifact that could not be published)
``11`` a controlled validation failure (the document violates S1-S20)
``20`` a protocol or input failure (the workspace, the request file, the
       envelope, the protocol version or the operation)
``30`` an unhandled internal error in this harness
=====  ====================================================================

A response file is written for every one of those except a workspace so
broken there is nowhere to write it. The host classifies on the response
first and the exit code second, and never treats ``0`` alone as success.

Imports
-------
Only the standard library is imported at module level. The build machinery --
and therefore CadQuery and OpenCascade, measured at about two seconds of
import time -- is imported inside the build operation, so a protocol failure
or a diagnostic operation costs milliseconds and never loads the kernel.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: Version of the **IPC protocol** in this module -- the envelope shape, the
#: operation names, the response fields and the exit codes.
#:
#: Deliberately **not** the CAD document's ``schema_version`` (the V1
#: specification, ``1.0.0``), not the render model's ``format_version``, and
#: not the cache's ``cache_schema_version``. Four independent things; this one
#: describes how two processes talk. The document's own version travels
#: untouched inside ``request.document``.
IPC_PROTOCOL_VERSION = "1.0.0"

#: The request file the host writes into the workspace.
REQUEST_FILENAME = "request.json"

#: The response file this process writes into the workspace.
RESPONSE_FILENAME = "response.json"

#: Where a partial response is written before being published atomically.
PARTIAL_RESPONSE_FILENAME = "response.json.partial"

#: The render model's canonical bytes, when ``render`` was requested.
RENDER_PAYLOAD_FILENAME = "render.json"

#: Where build outputs go when the host named no output directory.
WORKSPACE_OUTPUT_DIRNAME = "artifacts"

#: The one production operation.
OPERATION_BUILD = "build"

#: Prefix of the operations that exist **only** to exercise the host's
#: failure classification. None of them touches a CAD module, reads the
#: request payload, or produces an artifact.
DIAGNOSTIC_PREFIX = "diagnostic:"

#: Exit abruptly with :func:`os._exit`, writing no response at all.
OPERATION_DIAGNOSTIC_ABORT = "diagnostic:abort"

#: Write a response file that is not JSON.
OPERATION_DIAGNOSTIC_MALFORMED = "diagnostic:malformed"

#: Write a well-formed response naming a different protocol version.
OPERATION_DIAGNOSTIC_OTHER_VERSION = "diagnostic:other-version"

#: Sleep past any sane timeout.
OPERATION_DIAGNOSTIC_SLEEP = "diagnostic:sleep"

#: Write noise to stdout and stderr, then a correct response.
OPERATION_DIAGNOSTIC_NOISE = "diagnostic:noise"

#: Raise an unhandled exception inside the harness.
OPERATION_DIAGNOSTIC_INTERNAL = "diagnostic:internal"

#: Report the environment variable **names** this process received. Names
#: only: a value is never reported, so this cannot leak one.
OPERATION_DIAGNOSTIC_ENVIRONMENT = "diagnostic:environment"

#: Every operation this worker accepts.
OPERATIONS: Tuple[str, ...] = (
    OPERATION_BUILD,
    OPERATION_DIAGNOSTIC_ABORT,
    OPERATION_DIAGNOSTIC_MALFORMED,
    OPERATION_DIAGNOSTIC_OTHER_VERSION,
    OPERATION_DIAGNOSTIC_SLEEP,
    OPERATION_DIAGNOSTIC_NOISE,
    OPERATION_DIAGNOSTIC_INTERNAL,
    OPERATION_DIAGNOSTIC_ENVIRONMENT,
)

#: How long ``diagnostic:sleep`` sleeps. Longer than any timeout a test uses,
#: and the host kills it long before this elapses.
DIAGNOSTIC_SLEEP_SECONDS = 300.0

EXIT_SUCCESS = 0
EXIT_BUILD_FAILED = 10
EXIT_VALIDATION_FAILED = 11
EXIT_PROTOCOL_ERROR = 20
EXIT_INTERNAL_ERROR = 30

#: Exit codes, in one place, so the host and the documentation agree.
EXIT_CODES: Tuple[int, ...] = (
    EXIT_SUCCESS,
    EXIT_BUILD_FAILED,
    EXIT_VALIDATION_FAILED,
    EXIT_PROTOCOL_ERROR,
    EXIT_INTERNAL_ERROR,
)

#: Fields a request envelope carries. Exactly these -- an unknown or missing
#: field is a protocol failure, never something silently ignored.
REQUEST_ENVELOPE_FIELDS: Tuple[str, ...] = (
    "ipc_protocol_version",
    "operation",
    "request",
)

#: Fields a response envelope carries.
RESPONSE_ENVELOPE_FIELDS: Tuple[str, ...] = (
    "ipc_protocol_version",
    "operation",
    "status",
    "result",
    "error",
    "transport",
)

#: Fields a build request payload carries: the canonical CAD document and the
#: canonical build options. **No filesystem path**, ever -- paths reach this
#: process as launch arguments the host controls, never as request data.
BUILD_REQUEST_FIELDS: Tuple[str, ...] = ("document", "options")

#: Command-line flags this worker accepts, and nothing else.
OUTPUT_DIRECTORY_FLAG = "--output-directory"
CACHE_ROOT_FLAG = "--cache-root"


class WorkerStatus(Enum):
    """What this process concluded. Written into the response envelope."""

    #: The build succeeded; ``result`` holds it.
    SUCCEEDED = "succeeded"

    #: The document violates the V1 static rules; ``result`` holds the failed
    #: job and ``error`` its structured rule codes.
    VALIDATION_FAILED = "validation_failed"

    #: The build failed for a geometric, engine, export or publication
    #: reason.
    BUILD_FAILED = "build_failed"

    #: The workspace, the request file, the envelope, the protocol version or
    #: the operation was unusable. No build was attempted.
    PROTOCOL_ERROR = "protocol_error"

    #: This harness itself failed unexpectedly, outside the build.
    INTERNAL_ERROR = "internal_error"


#: Status to exit code.
STATUS_EXIT_CODES: Mapping[WorkerStatus, int] = {
    WorkerStatus.SUCCEEDED: EXIT_SUCCESS,
    WorkerStatus.VALIDATION_FAILED: EXIT_VALIDATION_FAILED,
    WorkerStatus.BUILD_FAILED: EXIT_BUILD_FAILED,
    WorkerStatus.PROTOCOL_ERROR: EXIT_PROTOCOL_ERROR,
    WorkerStatus.INTERNAL_ERROR: EXIT_INTERNAL_ERROR,
}


class _ProtocolError(Exception):
    """The request could not be understood. Never a build outcome."""


def main(argv: Optional[List[str]] = None) -> int:
    """Run one isolated build and return this process's exit code."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        workspace, output_directory, cache_root = _parse_arguments(arguments)
    except _ProtocolError as exc:
        # There is no usable workspace, so there is nowhere to write a
        # response. The exit code and stderr are all the host gets, and the
        # host classifies that as a protocol failure rather than a success.
        sys.stderr.write(f"isolated worker: {exc}\n")
        return EXIT_PROTOCOL_ERROR

    operation = OPERATION_BUILD
    try:
        envelope = _read_request(workspace)
        operation = envelope["operation"]
        payload = envelope["request"]
    except _ProtocolError as exc:
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.PROTOCOL_ERROR,
            error={"kind": "protocol", "message": str(exc)},
        )
    except Exception:  # pragma: no cover - defensive
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.INTERNAL_ERROR,
            error={
                "kind": "internal",
                "message": "the worker failed while reading the request",
            },
            diagnostic=traceback.format_exc(),
        )

    try:
        if operation != OPERATION_BUILD:
            return _run_diagnostic(workspace, operation)
        return _run_build(
            workspace,
            payload=payload,
            output_directory=output_directory,
            cache_root=cache_root,
        )
    except _ProtocolError as exc:
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.PROTOCOL_ERROR,
            error={"kind": "protocol", "message": str(exc)},
        )
    except BaseException:
        # An unhandled failure in this harness is reported, not hidden, and
        # is never allowed to look like a build result.
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.INTERNAL_ERROR,
            error={
                "kind": "internal",
                "message": (
                    "the worker failed with an unhandled internal error"
                ),
            },
            diagnostic=traceback.format_exc(),
        )


# --- arguments and request --------------------------------------------------


def _parse_arguments(arguments: List[str]) -> Tuple[Path, Optional[Path], Optional[Path]]:
    """Read the workspace and the at most two paths this worker may write to.

    Hand-parsed rather than delegated, so an unknown flag is a protocol
    failure with this module's own exit code instead of a library's. These
    flags are the **only** filesystem paths the child accepts, and they come
    from the host's own API -- never from the request payload.
    """
    if not arguments:
        raise _ProtocolError("a workspace directory is required")
    workspace = Path(arguments[0])
    if not workspace.is_dir():
        raise _ProtocolError(f"the workspace {arguments[0]!r} is not a directory")
    output_directory: Optional[Path] = None
    cache_root: Optional[Path] = None
    index = 1
    while index < len(arguments):
        flag = arguments[index]
        if flag not in (OUTPUT_DIRECTORY_FLAG, CACHE_ROOT_FLAG):
            raise _ProtocolError(f"unknown argument {flag!r}")
        if index + 1 >= len(arguments):
            raise _ProtocolError(f"{flag} needs a value")
        value = Path(arguments[index + 1])
        if not value.is_dir():
            raise _ProtocolError(f"{flag} must name an existing directory")
        if flag == OUTPUT_DIRECTORY_FLAG:
            output_directory = value
        else:
            cache_root = value
        index += 2
    return workspace, output_directory, cache_root


def _read_request(workspace: Path) -> Dict[str, Any]:
    """Read and check the request envelope. Strict about every field."""
    location = workspace / REQUEST_FILENAME
    if not location.is_file():
        raise _ProtocolError(f"no {REQUEST_FILENAME} in the workspace")
    try:
        raw = json.loads(location.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _ProtocolError(
            f"the request is not readable UTF-8 JSON ({type(exc).__name__})"
        ) from exc
    if not isinstance(raw, dict):
        raise _ProtocolError("the request envelope is not a JSON object")
    if set(raw) != set(REQUEST_ENVELOPE_FIELDS):
        raise _ProtocolError(
            "the request envelope's fields are not "
            + ", ".join(REQUEST_ENVELOPE_FIELDS)
        )
    if raw["ipc_protocol_version"] != IPC_PROTOCOL_VERSION:
        raise _ProtocolError(
            f"the request speaks IPC protocol {raw['ipc_protocol_version']!r}; "
            f"this worker speaks {IPC_PROTOCOL_VERSION!r}"
        )
    if raw["operation"] not in OPERATIONS:
        raise _ProtocolError(f"unknown operation {raw['operation']!r}")
    return raw


# --- the build operation ----------------------------------------------------


def _run_build(
    workspace: Path,
    *,
    payload: Any,
    output_directory: Optional[Path],
    cache_root: Optional[Path],
) -> int:
    """Run the existing build pipeline and emit its structured result."""
    document, outputs = _build_payload(payload)

    # Imported here, not at module level: this is where the two seconds of
    # CadQuery/OpenCascade import time is spent, and a protocol failure or a
    # diagnostic operation must not pay it.
    from cad_core.build_job import (
        BuildFailure,
        BuildOptions,
        BuildOutput,
        BuildRequestError,
        BuildStatus,
        execute_build_document,
        request_for_document,
    )
    from cad_core.serialization import DocumentValidationError

    try:
        selected = tuple(BuildOutput(name) for name in outputs)
    except ValueError as exc:
        raise _ProtocolError(f"unknown build output ({exc})") from None
    try:
        options = BuildOptions(outputs=selected)
    except BuildRequestError as exc:
        raise _ProtocolError(str(exc)) from None

    directory = output_directory
    if directory is None and options.writes_files:
        directory = workspace / WORKSPACE_OUTPUT_DIRNAME
        directory.mkdir(exist_ok=True)

    if cache_root is None:
        job = execute_build_document(document, options, output_directory=directory)
    else:
        try:
            request = request_for_document(document, options)
        except DocumentValidationError:
            # Reuse the existing structured-failure path rather than shaping a
            # second one here.
            job = execute_build_document(
                document, options, output_directory=directory
            )
        else:
            from cad_core.local_build_cache import LocalBuildCache, get_or_build

            job = get_or_build(
                request,
                LocalBuildCache(cache_root),
                output_directory=directory,
            )

    result = job.result
    if result is None:  # pragma: no cover - run_job always produces one
        raise _ProtocolError("the build produced no result")

    transport: Dict[str, Any] = {"render_payload": None}
    if job.status is BuildStatus.SUCCEEDED and result.render_model is not None:
        from cad_core.artifact_registry import canonical_render_bytes

        (workspace / RENDER_PAYLOAD_FILENAME).write_bytes(
            canonical_render_bytes(result.render_model)
        )
        transport["render_payload"] = RENDER_PAYLOAD_FILENAME

    if job.status is BuildStatus.SUCCEEDED:
        status = WorkerStatus.SUCCEEDED
    elif (
        result.error is not None
        and result.error.failure is BuildFailure.DOCUMENT_INVALID
    ):
        status = WorkerStatus.VALIDATION_FAILED
    else:
        status = WorkerStatus.BUILD_FAILED

    return _emit(
        workspace,
        operation=OPERATION_BUILD,
        status=status,
        result=result.to_dict(),
        error=result.error.to_dict() if result.error is not None else None,
        transport=transport,
    )


def _build_payload(payload: Any) -> Tuple[Mapping[str, Any], List[str]]:
    """Check a build payload and return the document and the output names.

    The document is handed on untouched: this worker has no opinion about CAD
    semantics, and the existing validator is the only thing that judges it.
    """
    if not isinstance(payload, dict):
        raise _ProtocolError("the build request is not a JSON object")
    if set(payload) != set(BUILD_REQUEST_FIELDS):
        raise _ProtocolError(
            "the build request's fields are not "
            + ", ".join(BUILD_REQUEST_FIELDS)
        )
    document = payload["document"]
    if not isinstance(document, dict):
        raise _ProtocolError("the CAD document is not a JSON object")
    options = payload["options"]
    if not isinstance(options, dict) or set(options) != {"outputs"}:
        raise _ProtocolError("the build options must hold exactly 'outputs'")
    outputs = options["outputs"]
    if not isinstance(outputs, list) or not all(
        isinstance(name, str) for name in outputs
    ):
        raise _ProtocolError("'outputs' must be a list of output names")
    return document, list(outputs)


# --- diagnostic operations --------------------------------------------------


def _run_diagnostic(workspace: Path, operation: str) -> int:
    """Operations that exist only to exercise the host's classification.

    None of them imports a CAD module, reads the CAD document, builds
    anything or writes an artifact. They are how the host's crash, timeout,
    malformed-response and noise handling is tested without ever provoking
    the real kernel into an abnormal exit.
    """
    if operation == OPERATION_DIAGNOSTIC_ABORT:
        # An abrupt end with no response and a *success* exit code, which is
        # the case the host must not mistake for success.
        sys.stderr.write("isolated worker: diagnostic abort\n")
        sys.stderr.flush()
        os._exit(EXIT_SUCCESS)
    if operation == OPERATION_DIAGNOSTIC_MALFORMED:
        (workspace / RESPONSE_FILENAME).write_text(
            "{ this is not json", encoding="utf-8"
        )
        return EXIT_SUCCESS
    if operation == OPERATION_DIAGNOSTIC_OTHER_VERSION:
        (workspace / RESPONSE_FILENAME).write_text(
            json.dumps(
                {
                    "ipc_protocol_version": "0.0.1",
                    "operation": operation,
                    "status": WorkerStatus.SUCCEEDED.value,
                    "result": None,
                    "error": None,
                    "transport": {"render_payload": None},
                }
            ),
            encoding="utf-8",
        )
        return EXIT_SUCCESS
    if operation == OPERATION_DIAGNOSTIC_SLEEP:
        time.sleep(DIAGNOSTIC_SLEEP_SECONDS)
        return EXIT_SUCCESS  # pragma: no cover - the host kills this first
    if operation == OPERATION_DIAGNOSTIC_NOISE:
        # Exactly the hazard the file-based protocol exists for: a library
        # writing to both streams around the machine-readable result.
        sys.stdout.write('{"not":"the protocol"}\nplain text on stdout\n')
        sys.stdout.flush()
        sys.stderr.write("**** ERR SomeKernel : diagnostic noise ****\n")
        sys.stderr.flush()
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.SUCCEEDED,
            result=None,
            error=None,
        )
    if operation == OPERATION_DIAGNOSTIC_INTERNAL:
        raise RuntimeError("diagnostic internal error")
    if operation == OPERATION_DIAGNOSTIC_ENVIRONMENT:
        return _emit(
            workspace,
            operation=operation,
            status=WorkerStatus.SUCCEEDED,
            # Names only. A value is never reported, so a secret that did
            # reach the child could not leak through this operation either.
            result={"environment_names": sorted(os.environ)},
            error=None,
        )
    raise _ProtocolError(f"unhandled operation {operation!r}")  # pragma: no cover


# --- the response -----------------------------------------------------------


def _emit(
    workspace: Path,
    *,
    operation: str,
    status: WorkerStatus,
    result: Optional[Mapping[str, Any]] = None,
    error: Optional[Mapping[str, Any]] = None,
    transport: Optional[Mapping[str, Any]] = None,
    diagnostic: Optional[str] = None,
) -> int:
    """Publish the response atomically and return the matching exit code.

    Written to a partial name and then :func:`os.replace`d, so the host can
    never read half a response -- the same discipline the build cache uses.
    A traceback goes to ``stderr`` for diagnosis and is never part of the
    response.
    """
    envelope = {
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "operation": operation,
        "status": status.value,
        "result": dict(result) if result is not None else None,
        "error": dict(error) if error is not None else None,
        "transport": dict(transport) if transport is not None else {
            "render_payload": None
        },
    }
    if diagnostic is not None:
        sys.stderr.write(diagnostic if diagnostic.endswith("\n") else diagnostic + "\n")
        sys.stderr.flush()
    payload = json.dumps(envelope, ensure_ascii=False, allow_nan=False).encode(
        "utf-8"
    )
    partial = workspace / PARTIAL_RESPONSE_FILENAME
    partial.write_bytes(payload)
    os.replace(partial, workspace / RESPONSE_FILENAME)
    return STATUS_EXIT_CODES[status]


__all__ = [
    "BUILD_REQUEST_FIELDS",
    "CACHE_ROOT_FLAG",
    "DIAGNOSTIC_PREFIX",
    "DIAGNOSTIC_SLEEP_SECONDS",
    "EXIT_BUILD_FAILED",
    "EXIT_CODES",
    "EXIT_INTERNAL_ERROR",
    "EXIT_PROTOCOL_ERROR",
    "EXIT_SUCCESS",
    "EXIT_VALIDATION_FAILED",
    "IPC_PROTOCOL_VERSION",
    "OPERATIONS",
    "OPERATION_BUILD",
    "OPERATION_DIAGNOSTIC_ABORT",
    "OPERATION_DIAGNOSTIC_ENVIRONMENT",
    "OPERATION_DIAGNOSTIC_INTERNAL",
    "OPERATION_DIAGNOSTIC_MALFORMED",
    "OPERATION_DIAGNOSTIC_NOISE",
    "OPERATION_DIAGNOSTIC_OTHER_VERSION",
    "OPERATION_DIAGNOSTIC_SLEEP",
    "OUTPUT_DIRECTORY_FLAG",
    "PARTIAL_RESPONSE_FILENAME",
    "RENDER_PAYLOAD_FILENAME",
    "REQUEST_ENVELOPE_FIELDS",
    "REQUEST_FILENAME",
    "RESPONSE_ENVELOPE_FIELDS",
    "RESPONSE_FILENAME",
    "STATUS_EXIT_CODES",
    "WORKSPACE_OUTPUT_DIRNAME",
    "WorkerStatus",
    "main",
]


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    # `os._exit`, not `sys.exit`: the response envelope is already committed to
    # disk by `_emit` (written, then `os.replace`d into place), so nothing is
    # lost by skipping interpreter teardown -- and teardown is precisely where
    # OpenCascade's Python bindings corrupt the heap on Windows, replacing this
    # worker's own exit code with 0xC0000374. The protocol requires the exit
    # code to agree with the reported status, so ending here keeps that
    # invariant a statement about the build rather than about a third-party
    # library's destructors. Streams are flushed first, since `os._exit` runs
    # no atexit handler and flushes no buffer.
    _code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_code)
