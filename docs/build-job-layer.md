# Local build job layer

Status: **Stage 16 — a local, in-memory build/job abstraction. Infrastructure
only.**

## Purpose

Turn a canonical CAD document into a **reproducible build result**, with
enough structure that a later UI or API could report on a build without
reading a Python traceback.

```
BuildRequest   (validated Part + BuildOptions)
      │
BuildJob       (QUEUED → RUNNING → SUCCEEDED | FAILED)
      │
execution      (local CAD engine, then the requested exporters)
      │
BuildResult    (artifact metadata, or one structured BuildError)
```

## The document stays the source of truth

A build carries **no second copy of the CAD semantics**. A `BuildRequest`
holds the validated `Part` and an output selection — nothing else about the
design. The document's identity is its Stage 15 canonical SHA-256
(`serialization.part_hash`), and on a request that hash is a **computed
property**, not a field: a caller cannot pass a hash that disagrees with the
part, so the two cannot drift. A test asserts the property-ness and that
passing `document_hash=` raises.

Geometry, STEP, IGES, STL and the render model are all **derived**. None of
them ever identifies a document: a build's identity is the document hash plus
the build options, never a hash of an exported file or of a kernel object.

## Job lifecycle and status transitions

| From | To | Allowed |
|---|---|---|
| `QUEUED` | `RUNNING` | ✓ |
| `QUEUED` | `SUCCEEDED` / `FAILED` | ✗ |
| `RUNNING` | `SUCCEEDED` | ✓ |
| `RUNNING` | `FAILED` | ✓ |
| `RUNNING` | `RUNNING` | ✗ |
| `SUCCEEDED` | anything | ✗ |
| `FAILED` | anything | ✗ |

`SUCCEEDED` and `FAILED` are terminal. Any other transition raises
`BuildStateError`, which carries the current and requested states and lists
what was permitted. Attempting to finalize an already terminal job is exactly
that error — **that is the guard against accidental double-finalization**, and
a test drives it.

**There is no cancellation state.** Nothing in this stage can cancel a build,
and a state no code can reach would be speculation.

Transitions are taken under a `threading.Lock`. That is three lines, and it
makes the guard atomic rather than merely sequential. It is **not** a worker
pool: no concurrency infrastructure exists here.

## Build identity vs execution identity

Two separate things:

| | What it is | Derived from |
|---|---|---|
| `BuildRequest.build_key` | reproducible identity of *this build* | document hash + canonical options |
| `BuildJob.execution_id` | identity of *one run* | random (`uuid4().hex`) |

The execution id is never part of a build's identity, and no timestamp is used
as identity anywhere. A test builds the same request twice into different
directories and asserts the build keys match while the execution ids and the
paths differ.

### The deterministic build key

The hex **SHA-256** of this UTF-8 JSON, written with the canonical document's
own conventions (minimal separators, `ensure_ascii=False`):

```json
{"document_hash":"<64 hex>","options":{"outputs":["geometry","step"]}}
```

That is the whole input — nothing else is hashed. Not a random UUID, not a
timestamp, not a filesystem path, not any geometry object.

The key is **not** the document hash: the two are different strings for the
same part, because they hash different structures. A test asserts that, and
another recomputes the key with `hashlib.sha256` from the documented bytes.

Because the canonical options deduplicate and sort the outputs, requesting the
same set in a different order — or twice — yields the same key.

## Artifact selection

`BuildOptions` has one field, `outputs`, and **no default**: a caller must say
what it wants. An empty selection raises `BuildRequestError`.

| Output | Produces | Where |
|---|---|---|
| `GEOMETRY` | the local B-rep + neutral measurements | in memory |
| `STEP` | a STEP file | file |
| `IGES` | an IGES file (BRep mode) | file |
| `STL` | a binary STL file | file |
| `RENDER` | the neutral render representation | in memory |

No new export format is introduced.

The B-rep is **always constructed**, because every other output derives from
it — but it appears in the artifact list only when `GEOMETRY` is requested. A
successful build's artifact list holds **exactly** the requested outputs, no
more and no fewer; tests check five different selections.

Requesting any file output requires an explicit `output_directory`. There is
no implicit temp location: writing files somewhere the caller did not name
would be exactly the kind of surprise this layer avoids. A missing directory,
or a file output with no directory, raises `BuildRequestError` **before** the
job starts, so the job stays `QUEUED`.

`BuildOptions` carries no CAD semantics (the document holds the design), no
LLM settings and no UI settings. Tessellation tolerances are the exporters'
documented defaults and are deliberately not options in this stage — exposing
them would mean putting them in the build key too.

## Artifact metadata

`BuildArtifact` describes an output; it never holds its bytes.

| Field | Meaning |
|---|---|
| `output` | which `BuildOutput` this is |
| `format` | what the payload is: `brep-in-memory`, `step`, `iges-brep`, `stl-binary`, `render-model` |
| `document_hash` | the CAD document this came from |
| `build_key` | the build this came from |
| `path` | **physical** location, for file outputs only; `None` in memory |
| `size_bytes` | size on disk, for file outputs only |
| `details` | neutral measurements — plain JSON data |

**Logical identity is separated from the physical path.** The identity is
`logical_id` = `<build key>:<output>`, which is reproducible on any machine;
`path` is machine-specific and is never identity. Tests assert the logical id
contains no path separator and no part of the temp directory name, and that
two builds into different directories share logical ids while their paths
differ.

Filenames are `<build key>.<ext>` inside the given directory. That is a
convenience — deterministic and collision-free across documents and option
sets — not the artifact's identity.

`details` holds real measurements: for geometry the feature id, solid count,
volume, bounding box and face/edge/vertex counts; for STL the triangle count
and structural consistency; for render the counts, bounds, winding, normal
binding and tessellation settings. All plain data — a test serializes a whole
result to JSON and asserts no kernel vocabulary appears.

### The render artifact

The render artifact carries **metadata**, and the `RenderModel` itself is a
**reference** on `BuildResult.render_model`. Its raw JSON is deliberately not
duplicated into the artifact: the render model is already plain, deterministic
data (Stage 8), so serializing it into the job as well would be the
duplication this layer exists to avoid. A test asserts the artifact's
`to_dict()` contains `vertex_count` but neither `vertices` nor `normals`.

Similarly `BuildResult.geometry` is an in-memory `LocalCadResult` — the B-rep
stays local for this stage — and it is excluded from `to_dict()`, so no
CadQuery or OpenCascade object can reach a serialized job.

## Failure model

Failures are classified, never collapsed into one string:

| `BuildFailure` | Cause |
|---|---|
| `DOCUMENT_INVALID` | the document fails static validation (S1–S20) |
| `UNSUPPORTED_GEOMETRY` | the engine does not build this part |
| `GEOMETRY_FAILED` | a geometric rule failed (E1–E5), or a kernel operation could not complete |
| `EXPORT_FAILED` | an exporter or the render builder refused |
| `JOB_STATE` | an illegal transition was attempted |
| `INTERNAL` | an unexpected exception |

`BuildError` carries the failure, a **stable public message**, the stage, the
output being produced, specification `rule_codes`, the validator's own
`validation_errors`, and the outputs that had completed.

Two rules about the message:

- **`message` is the public contract**: no traceback, no filesystem path,
  nothing from the environment. Exporter messages name the file they could not
  write, so for an export failure the public message names only the output and
  the exception type, and the exporter's own text goes to `diagnostic`. A test
  asserts the message contains no `/` and no part of the temp path.
- **an unexpected exception is not hidden**: its class name goes to
  `exception_type` and its traceback to `diagnostic`. Both are explicitly
  diagnostic-only, and `diagnostic` is **not** in `to_dict()` — a test asserts
  that too.

Two entry points, deliberately:

- `request_for_document(document, options)` is the strict boundary and
  **raises** `DocumentValidationError` for an invalid document (Stage 15
  behaviour);
- `execute_build_document(document, options, ...)` returns a **`FAILED` job**
  carrying `DOCUMENT_INVALID` and the structured validation errors instead.
  **No geometry is built** for an invalid document — a test patches
  `build_part` and asserts it is never called — and no job reaches `RUNNING`.

A `BuildStateError` is never swallowed into a result: misuse of the state
machine is a programming error, so it propagates.

## Failure atomicity

**A failed build never reports success.** If geometry succeeds and an export
then fails:

- the job is `FAILED`;
- `result.artifacts` is **empty** — nothing is reported as produced;
- `result.geometry` and `result.render_model` are `None`;
- **every file this build had already written is deleted**;
- the outputs that had completed are recorded by name in
  `error.completed_outputs`, so nothing is silently lost.

Chosen over keeping partial files: there is then no such thing as a
success-looking artifact from a failed build. Deletion is best-effort — a file
that cannot be removed does not turn a classified failure into an internal
one.

Measured example (a test): STEP and STL requested, the STL path blocked. STEP
is written, STL fails, the job is `FAILED` with `error.output == STL` and
`error.completed_outputs == (STEP,)`, the STEP file is gone, the artifact list
is empty, and a subsequent clean build into another directory succeeds
normally.

## Determinism

The same document and the same options give the same:

- build key and document hash;
- geometry measurements (the `GEOMETRY` artifact's `details` compare equal);
- requested artifact set;
- render model (byte-identical `json.dumps(..., sort_keys=True)`);
- STL bytes.

**Not** claimed: STEP and IGES byte equality. Earlier stages measured that
their headers carry a timestamp and an incrementing translator counter, and
that has not changed. A test asserts all of the above at once, including that
the STL bytes match and the execution ids differ.

Nothing about a build mutates its input: tests assert the source `Part`'s
canonical JSON and hash are unchanged after a full five-output build, and
after a failed one.

## Scope: local and in-memory

What this stage **is**: a `BuildOptions`/`BuildRequest`/`BuildJob`/
`BuildResult` model and a synchronous local executor. A job is an ordinary
Python object that lives as long as the caller holds it.

What this stage explicitly **does not** contain, and does not imply:

- no database, no file-backed job store, no index;
- no queue, broker, Redis, Celery or scheduler;
- no worker pool, thread pool, process pool or async execution;
- no HTTP API, no frontend, no LLM, no MCP;
- no retry, backoff, priority or cancellation machinery;
- no new export format, and no FeatureScript or Onshape involvement — the
  build layer does not import those modules, and a test asserts it.

A boundary test also asserts `build_job.py` imports none of `sqlite3`,
`redis`, `celery`, `kombu`, `pika`, `psycopg2`, `sqlalchemy`, `flask`,
`fastapi`, `django`, `requests`, `httpx`, `urllib`, `socket`,
`multiprocessing`, `concurrent.futures`, `asyncio` or `subprocess`.

## Package boundary

```
CAD document
    ↓
BuildRequest / BuildOptions
    ↓
BuildExecutor (run_job)
    ↓
local CAD engine  ·  STEP / IGES / STL  ·  RenderModel
```

One way only. The build layer may depend on the execution backends; nothing
upstream depends on it. A test asserts that `model.py`, `validator.py`,
`errors.py`, `serialization.py`, `featurescript.py`, the Onshape modules,
`local_cad.py`, `edge_selection.py`, the three exporters, `render_model.py`
and `__init__.py` none of them import `cad_core.build_job`.

It is **not** re-exported from the package root, so `import cad_core` stays
free of the geometry kernel — the property Stage 15 established and tests.
Import it explicitly:

```python
from cad_core.build_job import BuildOptions, BuildOutput, execute_build_document
```
