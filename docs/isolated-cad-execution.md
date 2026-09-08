# Isolated CAD execution

Status: **Stage 19 — a minimal process boundary around local CAD build
execution. Infrastructure only.**

> **This is process isolation for crash containment and execution boundaries,
> not a security sandbox.**

## Motivation

Stage 13 measured something specific and unpleasant: calling `.Shape()` on a
`BRepFilletAPI_MakeFillet` that is not done raises `StdFail_NotDone` **and
segfaults a later fillet in the same process**. The engine never does that —
it checks `IsDone()` first — but the fact stands: OpenCascade can end a
process, and a Python `try`/`except` cannot catch a dying process.

Every layer built so far is careful never to report a failure as a success. A
process that dies takes that guarantee with it, because there is nobody left
to report anything. Running the build in a child process restores it: the
host survives, learns *how* the child ended, and returns a structured
failure.

## Process architecture

```
host application
      │            JSON, in files inside a controlled workspace
isolated CAD process         cad_core.isolated_worker
      │
CAD kernel                   CadQuery / OpenCascade
      │
structured result
```

| | |
|---|---|
| host | `cad_core.isolated_execution` — creates the workspace, writes the request, launches the child, classifies what comes back |
| child | `cad_core.isolated_worker` — reads the request, calls the **existing** build machinery, writes one response, exits with a meaningful code |

The child holds **no CAD semantics**. It calls
`build_job.execute_build_document`, `build_job.request_for_document` and
`local_build_cache.get_or_build`; it does not validate a document itself,
build geometry itself or write an exporter, and it carries no second copy of
the CAD schema. A test asserts the names `build_part`, `export_step`,
`export_iges`, `export_stl`, `select_edges`, `validate`, `cadquery`,
`Workplane` and `TopoDS` appear nowhere in its code, and that it *does* import
`cad_core.build_job` and `cad_core.local_build_cache`.

### Invocation

```python
[sys.executable, "-m", "cad_core.isolated_worker", <workspace>,
 "--output-directory", <dir>,          # optional
 "--cache-root", <dir>]                # optional
```

An argument **list**. No shell, no `shell=True`, no command string assembled
from anything, and no `os.system`/`os.popen`. A test walks the syntax tree and
asserts there is no `shell=` keyword whose value is not `False`, and that the
names `system`, `popen`, `spawnl`, `execv` and `call` do not appear.

`PYTHONPATH` for the child is derived from `cad_core.__file__` — the package's
own location — with the host's existing `PYTHONPATH` entries appended. So it
works from a source checkout (which is how this repository is tested) and from
an installed package, with **no absolute path hard-coded** and no reliance on
the current working directory. The child's working directory is its own
workspace, which contains no Python files, so nothing can be shadowed.

Those two flags are the **only** filesystem paths the child accepts, and they
come from the host's own API. The request payload contains no path at all.

## IPC protocol

JSON only. Never `pickle`, `marshal`, `shelve`, `eval`, `exec`, `compile` or
arbitrary object deserialization — tests assert each of those by name. The
only deserializer anywhere in the boundary is `json.loads`.

The protocol lives in **files inside the workspace**, not on a stream:

| File | Written by | Content |
|---|---|---|
| `request.json` | host, before launch | the whole input |
| `response.json` | child | the whole output, published with `os.replace` so a partial write is never readable |
| `render.json` | child, when `render` was requested | the render model's canonical bytes |

**Why a file and not stdout.** OpenCascade writes diagnostics straight to the
process's file descriptors — measured in earlier stages, and visible in this
suite's own output as `**** ERR StepFile : ...`. A C-level write cannot be
kept out of a Python-managed stream, so nothing machine-readable may share one
with it. `stdout` and `stderr` are therefore **captured as diagnostics only**,
bounded to 4000 characters, and never parsed. A test runs a child that writes
`{"not":"the protocol"}` to stdout and a fake kernel error to stderr around a
correct response, and asserts the host still reads it as a success.

### Request envelope

```json
{
  "ipc_protocol_version": "1.0.0",
  "operation": "build",
  "request": {
    "document": { "schema_version": "1.0.0", "units": "mm", "...": "..." },
    "options": {"outputs": ["geometry", "step", "iges", "stl", "render"]}
  }
}
```

- `document` is the **Stage 15 canonical CAD document**, unchanged and
  untouched — no second CAD schema, and no second `schema_version`;
- `options` is exactly `BuildOptions.canonical()` — no second option schema;
- the payload's fields are exactly `document` and `options`. An unknown field
  is a protocol failure: a test sends `output_directory` in the payload and
  asserts it is refused.

### Response envelope

```json
{
  "ipc_protocol_version": "1.0.0",
  "operation": "build",
  "status": "succeeded",
  "result": { "...BuildResult.to_dict()..." },
  "error": { "...BuildError.to_dict()..." },
  "transport": {"render_payload": "render.json"}
}
```

`result` is Stage 16's `BuildResult.to_dict()` and `error` is its
`BuildError.to_dict()`, both verbatim: no parallel result schema exists.
Note that `BuildError.to_dict()` already excludes `diagnostic`, so **no
traceback crosses the boundary in the protocol** — an unhandled worker error's
traceback goes to `stderr`, where it is diagnostic.

Both envelopes are read **strictly**: the field set must be exactly the
documented one, the protocol version must match, and the operation must be one
of a closed tuple. Nothing is repaired and nothing is ignored.

## Protocol versioning

Four version numbers, four different contracts. They happen to read `1.0.0`
today; they are independent constants and are never substituted for one
another.

| Constant | Versions |
|---|---|
| the document's `schema_version` | the **V1 CAD specification** |
| `render_model.RENDER_FORMAT_VERSION` | the render representation |
| `local_build_cache.CACHE_SCHEMA_VERSION` | the cache entry file format |
| `isolated_worker.IPC_PROTOCOL_VERSION` | **this** envelope, its operations, its response fields and its exit codes |

A test asserts the envelope carries `ipc_protocol_version` and **never**
`schema_version`: the CAD document's own version travels untouched inside
`request.document`, where it belongs.

## Process exit states

| Code | Meaning |
|---|---|
| `0` | success |
| `10` | a controlled **build** failure — the engine, a geometric rule (E1–E5), an exporter, or artifact publication |
| `11` | a controlled **validation** failure — the document violates S1–S20 |
| `20` | a **protocol or input** failure — the workspace, the request file, the envelope, the version or the operation |
| `30` | an **unhandled internal error** in the worker harness |

A response file is written for every one of those except a workspace so broken
there is nowhere to write to. The host classifies on the **response first and
the exit code second**, and cross-checks them: a response claiming success
alongside a non-success exit code is a protocol error, and a test drives that
with a deliberately lying child.

**A zero exit code is never taken as success on its own.** The crash-
containment test proves why: `diagnostic:abort` calls `os._exit(0)` and writes
nothing, and the host reports `PROCESS_FAILED`.

## Failure classification

Seven host outcomes — a superset of the five the stage requires, because a
timeout and an internal worker error are genuinely not the same thing as a
crash:

| `IsolationOutcome` | When |
|---|---|
| `SUCCEEDED` | the child reported success, its exit code agreed, and every artifact it named was verified against the bytes on disk |
| `VALIDATION_FAILED` | the child reported that the document violates S1–S20 |
| `BUILD_FAILED` | the child reported a controlled build failure |
| `WORKER_ERROR` | the child's own harness failed outside the build, and said so |
| `PROTOCOL_ERROR` | a malformed or wrongly versioned envelope, a status that disagrees with the exit code, or an artifact whose bytes do not match what the child claimed |
| `PROCESS_FAILED` | the child ended without leaving a usable response — **the crash case** |
| `TIMED_OUT` | the child exceeded its timeout and was killed |

Mapped from what the child said, but never trusted: a `succeeded` claim is
honoured only after the exit code agrees, the manifest parses, every
file-backed artifact exists with a matching size and a **recomputed** SHA-256,
and the render payload matches its recorded checksum.

`IsolatedFailure.message` is the **stable public sentence** — the same
contract Stage 16 established. No traceback, no filesystem path, nothing from
the environment, and never the child's `stderr`. The streams live on
`IsolatedExecution.diagnostic`, which is excluded from `to_dict()`. Tests
assert an export failure's message contains no `/`, and that a worker error's
message contains neither the exception text nor `Traceback` while the
diagnostic does.

Measured, each its own outcome:

| Case | Outcome | Exit |
|---|---|---|
| valid five-output build | `SUCCEEDED` | 0 |
| document with a zero dimension | `VALIDATION_FAILED` (rule S10) | 11 |
| through-hole missing its target | `BUILD_FAILED` (rule E1) | 10 |
| a directory where the STEP file must go | `BUILD_FAILED` (`EXPORT_FAILED`) | 10 |
| `os._exit(0)`, no response | `PROCESS_FAILED` | 0 |
| response that is not JSON | `PROTOCOL_ERROR` | 0 |
| response naming protocol `0.0.1` | `PROTOCOL_ERROR` | 0 |
| unhandled exception in the harness | `WORKER_ERROR` | 30 |
| sleeping child, 1 s timeout | `TIMED_OUT` | — |
| noise on both streams plus a good response | `SUCCEEDED` | 0 |

### `UNSUPPORTED_GEOMETRY` is unreachable across this boundary

Measured, not assumed. `build_part` raises `UnsupportedGeometryError` for
non-mm units, an empty history, an unsupported feature type, a
non-constructive first feature and a bad axis. Over IPC the document is
validated first, and **each of those is caught by a static rule** — S5, S2,
S9/S7, S9/S6 and S12 respectively. So a document that validates can never
reach the engine's unsupported branch, and the isolated result is
`VALIDATION_FAILED`, not `BUILD_FAILED`. The classification is wired
regardless, and a test records the measurement.

## Timeout behaviour

`timeout_seconds` is an explicit parameter on every entry point, never hidden.
The default is **60 seconds**, chosen from a measurement rather than a guess:
a child costs about 2.4–2.9 s wall clock on this machine, of which ~2.05 s is
importing CadQuery and OpenCascade, while the V1 builds themselves take
~13 ms. Sixty seconds is roughly twenty times the measured cost, leaving room
for a slower machine or a heavier part.

On expiry the host kills the child, waits up to `KILL_GRACE_SECONDS` (10) to
reap it, records `TIMED_OUT` with `exit_code = None`, and removes the
workspace. Tests assert the call returns promptly with a 1 s timeout, that
`os.kill(child_pid, 0)` then raises `ProcessLookupError`, that
`child_abandoned` is `False`, that the workspace is gone and that nothing was
published.

There is **no** long-running job scheduling here: no queue, no retry, no
backoff, no priority, no progress reporting. One synchronous call, one child.

## Workspace lifecycle

One fresh directory per invocation, created by the host:

```
<workspace_root>/cad-isolated-XXXXXXXX/
    request.json
    response.json
    render.json            when render was requested
```

- `workspace_root` defaults to the system temporary directory and can be named
  by the caller. It is **never inside the build cache** — the cache holds
  published artifacts, and transient protocol scratch has no business there; a
  test asserts the workspace path is not relative to the cache root.
- The workspace is removed in a `finally`, so it goes away after a success, a
  build failure, a protocol failure, a crash and a timeout alike. Tests assert
  all five.
- `keep_workspace=True` leaves it for diagnosis; off by default.
- Build outputs do **not** go in the workspace. Requesting STEP, IGES or STL
  requires an explicit `output_directory` — the same rule `run_job` already
  applies, and for the same reason: the workspace is removed, and it is not a
  place to leave artifacts. A test asserts the output directory ends up holding
  exactly the build's three files and nothing else.
- The child's `TMPDIR`/`TEMP`/`TMP` point at the workspace, so a library's
  temporary file lands in the controlled directory and is cleaned with it.

## Artifact and cache interaction

### The B-rep does not cross the boundary

No kernel object is ever sent, and **no B-rep serialization format was
invented**. The geometry artifact's *measurements* come back — part name,
feature id, solid count, volume, bounding box, face/edge/vertex counts — which
is all that artifact ever contained (Stage 17): even in-process its
`checksum`, `size_bytes` and `path` are `None`.

The consequence, stated plainly: **the in-memory solid exists only in the
child and dies with it.** `IsolatedExecution` has no `geometry` attribute. A
host that needs a live `LocalCadResult` must build in-process. Geometry is a
non-cached, non-transferred derived in-memory artifact in this stage.

File-backed artifacts survive as files. The render model survives as its
**existing** canonical JSON — Stage 18's `canonical_render_bytes` — written to
`render.json` and reconstructed by the host with
`render_model_from_canonical_bytes`, after its SHA-256 is checked against the
render artifact's recorded checksum. No new format. The render artifact keeps
`storage = in_memory` and `path = None`, so the manifest is **byte-identical**
to an in-process build's: a test compares `canonical_bytes()` of both and of
the reconstructed render model, for all five geometries.

### The manifest is rebuilt and re-verified

The host reconstructs a real `ArtifactManifest` with
`artifact_registry.manifest_from_dict` — added in this stage as the strict
inverse of `ArtifactManifest.to_dict()`, defined beside it so the two cannot
drift. Then, because a separate process's claims are checked rather than
trusted, every file-backed artifact must exist, its size must match and its
SHA-256 is **recomputed from the bytes on disk**. A mismatch is a
`PROTOCOL_ERROR`, not a success.

### The cache, unchanged

`get_or_build_isolated(request, cache)` integrates with Stage 18 without
redesigning any of it:

```
cache miss  →  isolated build in a child  →  the child publishes  →  cache entry
cache hit   →  no child process at all    →  cached result
```

1. the **host** validates the entry for the request's build key. On a hit it
   returns immediately, `child_launched = False`. A test patches
   `subprocess.Popen` to raise and asserts the hit still succeeds — no child
   is launched, proved rather than inferred;
2. on a miss the child is given the cache root as a launch argument and calls
   `local_build_cache.get_or_build` itself. Publishing inside the child is
   what keeps the render model and the B-rep from having to cross the boundary
   for the cache's sake;
3. the host then re-validates the entry — recomputing every checksum from the
   cached bytes — and reports the **cache-resident** artifacts, whose paths
   point inside the cache root rather than at the workspace that is about to
   be removed.

A build that succeeds but whose entry does not validate is still reported as a
success, with `cache_published = False`. Nothing is ever published for a
failed, crashed or timed-out child: tests assert `entries/` is not even
created after a crash and after a build killed mid-import.

### The result stays Stage 16/17/18 vocabulary

`IsolatedExecution` carries the document hash, the build key, an
`ArtifactManifest`, the render model, cache metadata (`cache_hit`,
`cache_published`) and a structured `IsolatedFailure`. No parallel result
architecture: `BuildStatus`, `BuildFailure`, `BuildOutput`, `Artifact` and
`ArtifactManifest` are the existing types.

The child's `execution_id` travels back and is, as always, **not an
identity** — a fresh one per invocation, and no part of the build key.

## Environment

An **allowlist**. The child inherits, only if the host has them, the dynamic
loader's and the platform's variables: `PATH`, `LD_LIBRARY_PATH`,
`DYLD_LIBRARY_PATH`, `COMSPEC`, `SYSTEMROOT`, `SystemRoot`, `WINDIR`. It is
*given* `PYTHONPATH`, `PYTHONIOENCODING=utf-8`,
`PYTHONDONTWRITEBYTECODE=1` and `TMPDIR`/`TEMP`/`TMP`.

Everything else in the host's environment is dropped, so no API key, token or
cloud credential can reach the child even by accident. **No credential
handling exists in this stage and none is needed.** Tests set
`AWS_SECRET_ACCESS_KEY` and `OPENAI_API_KEY` in the host's environment and
assert neither name appears among the child's environment variables, and that
the sentinel value appears nowhere in `request.json`.

Measured: with a `PYTHONPATH`-only environment the kernel still builds real
geometry, so the allowlist is genuinely minimal rather than nominally so. The
names the child actually reports are the allowlist plus whatever the
interpreter sets for itself (`LC_CTYPE`, from CPython's own locale coercion).

## Security limitations

**This is process isolation for crash containment and execution boundaries,
not a security sandbox.**

The child runs as the **same OS user** with broadly the **same filesystem
permissions** as the host.

What it does provide:

- **crash containment** — a child that dies abnormally cannot end the host,
  and the host reports the abnormality as a structured failure;
- **process isolation** — the kernel's memory, its global state and its
  file descriptors belong to a process that goes away;
- **protocol isolation** — only strictly validated JSON crosses, never Python
  objects, never code;
- **path confinement by construction** — the child is handed at most two
  directories and the request carries no path;
- **a minimal environment** — an allowlist, so credentials cannot leak in.

What it does **not** provide, and is not claimed to:

- no syscall filtering, and no **seccomp**;
- no **container** or namespace isolation;
- no **network** isolation — the child could open a socket, and nothing stops
  it;
- no **privilege** dropping, no `setuid`, no capability reduction;
- no **filesystem jail** — no chroot, no bind mounts, no read-only root;
- no CPU, memory or file-descriptor **rlimits**;
- no protection against a **malicious** worker module; the trust boundary
  assumed here is that the code in this repository is trusted and the CAD
  *document* is data.

The host also **loads** the kernel library itself: this module imports
`build_job` and `local_build_cache`, which import CadQuery. What is isolated
is **execution** — where the measured crash hazard is — not the import.

### Arbitrary code execution is not supported

There is no operation that runs caller-supplied Python. The request carries a
**CAD document** and a list of output names; the operation is one of a closed
tuple of fixed strings; and `eval`, `exec`, `compile`, `__import__`, `pickle`,
`marshal`, `runpy` and a shell are all absent, each asserted by name in a
test. There is no way to express "run this code" in the protocol.

## Diagnostic operations

The worker accepts one production operation, `build`, and a small set of
`diagnostic:` operations that exist **only** to exercise the host's failure
classification: `abort`, `malformed`, `other-version`, `sleep`, `noise`,
`internal` and `environment`. None of them imports a CAD module, reads the CAD
document, builds anything or writes an artifact.

They are how abnormal termination is tested **without provoking the real
kernel into a segfault**, which this stage deliberately does not do. A test
asserts every non-`build` operation is prefixed `diagnostic:`, and the host's
three build entry points always send `build`.

## How this prepares us for future workers

What exists now is the shape a worker needs, at the smallest possible size:

- a **serializable request** — the canonical document plus canonical options,
  which is already all a worker would need to receive;
- a **versioned protocol** with strict envelopes on both sides, so a worker
  and a host can be upgraded independently;
- a **structured result** that distinguishes a build failure from a crash from
  a timeout from a malformed reply — the classification a scheduler needs to
  decide whether to retry;
- a **build key** that is reproducible across processes and machines, which is
  what lets a queue deduplicate work and a cache serve it;
- **execution identity kept separate from build identity**, so many
  executions of one build are already expressible;
- a **workspace lifecycle** with cleanup on every path.

What is deliberately absent, and would be the next stages' work: a worker
pool, a queue, a broker, a scheduler, retries, backpressure, concurrency
limits, progress reporting, cancellation, resource limits and any real
sandboxing. None of it is implied by what is here. A test asserts neither
module imports `celery`, `kombu`, `pika`, `redis`, `sqlite3`, `sqlalchemy`,
`psycopg2`, `boto3`, `botocore`, `google.cloud`, `azure`, `kubernetes`,
`docker`, `requests`, `httpx`, `urllib`, `http`, `socket`, `socketserver`,
`grpc`, `flask`, `fastapi`, `django`, `asyncio`, `multiprocessing`,
`concurrent.futures` or `threading`.

## The specification is unchanged

`docs/cad-specification.md` is untouched, and so are the validator, the
geometry kernel, the exporters, the render model, the build job layer and the
cache. The neutral CAD specification remains the source of truth; the
canonical CAD document is authoritative; geometry, FeatureScript, STEP, IGES,
STL, the render model, their cached copies **and now the process that produces
them** are all derived.
