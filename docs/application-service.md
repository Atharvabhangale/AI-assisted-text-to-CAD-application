# Application service

Status: **Stage 20 — the application/domain service boundary above the build
infrastructure. No transport of any kind.**

> **Application Service ≠ HTTP API**

## Purpose

Define the application-level operations a future transport can call, so that
the transport contains no business logic.

```
future transport      HTTP API · CLI · UI · AI orchestration
        │
Application Service   cad_core.application_service        ← this layer
        │
Build / Cache / Isolation   build_job · local_build_cache · isolated_execution
        │
CAD engine / export / render
```

**This layer is not the HTTP API and must not become one.** It has no notion
of a route, a header, a status code, a session or a user. When an HTTP API is
eventually written it should *adapt to* this service — take a payload,
construct a request, call an operation, render `to_dict()` — and hold no
business logic of its own. The same is true of a CLI, a UI or an AI
orchestration layer: those are transports, and this is the operation set they
call.

Nothing here is HTTP-shaped, and a test asserts the module imports none of
`fastapi`, `starlette`, `flask`, `django`, `graphene`, `strawberry`,
`graphql`, `aiohttp`, `tornado`, `uvicorn`, `gunicorn`, `pydantic`,
`requests`, `httpx`, `urllib`, `http`, `socket`, `socketserver` or `grpc`.

## The seven questions

| Question | Answer |
|---|---|
| Can I accept a CAD document? | `validate_document`, `build_document` — JSON in |
| Can I validate it? | yes, through the **existing** validator |
| Can I build it? | `build_document` |
| Can I request specific outputs? | yes, by name, converted once to `BuildOptions` |
| Can I return a structured result? | `BuildOutcome` |
| Can I report failures cleanly? | `ServiceError`, seven kinds, nothing flattened |
| Can I identify the build deterministically? | the existing `document_hash` and `build_key` |

## Public API

```python
from cad_core.application_service import (
    CadApplicationService, BuildDocumentRequest, BuildOutcome,
    DocumentValidation, ServiceError, ServiceFailure, OUTPUT_NAMES,
)

service = CadApplicationService.local(cache_root)          # the local backend

check   = service.validate_document(json_text_or_mapping)  # -> DocumentValidation
outcome = service.build_document(request)                  # -> BuildOutcome
found   = service.find_build(request)                      # -> BuildOutcome | None
```

Three entries, deliberately. Two operations plus one lookup:

| | |
|---|---|
| `validate_document(document)` | accept and validate a CAD document. Builds nothing, launches nothing. |
| `build_document(request)` | build it, returning a structured outcome. |
| `find_build(request)` | is the result already available? Answered from the cache; launches nothing. |

**Not** here: `edit_document`, `fork_document`, `delete_document`,
`version_document`, `compare_documents`. Those need storage and history, and
neither exists.

### There is no `get_build_status` over a job

A service call is synchronous and no job store exists, so there is no pending
build to ask about — a status query over a *job* would be a fiction.
`find_build` is the meaningful form of the question ("is this result already
available?") and it answers it from the cache without launching anything.
`BuildOutcome.status` reports the terminal status of the call itself.

## Input boundary

**One boundary: JSON.**

```
JSON document (text, UTF-8 bytes, or a parsed mapping)
        │  cad_core.serialization — part_from_json / deserialize_part
validated Part
        │  cad_core.build_job
BuildRequest
```

A typed `Part` is deliberately **not** accepted: a caller holding one is
already inside the domain and should use `cad_core.build_job` directly. The
service exists for the layers outside, which speak JSON. Passing a `Part`
raises `ApplicationServiceError` with that explanation, and a test asserts it.

No document is accepted without going through the existing deserializer, so an
arbitrary dictionary cannot slip past validation. A path is never accepted as
a document — loading files is `serialization.load_part`'s job, not this
layer's.

`validate_document` returns the **canonical** form of what was submitted, not
the submitted text: a terse document and a fully materialised one share a hash
but only one is canonical (Stage 15), and a transport should echo back the
canonical one. A test submits a terse box and asserts the returned document
carries the materialised `position`.

## Validation boundary

Validation is the existing validator's, reached through the existing
deserializer. **This layer implements no rule.** A test asserts the module
imports neither `cad_core.validator`, `cad_core.rules` nor `cad_core.geometry`,
and that the names `STATIC_RULES`, `check_geometric_rules` and `validate`
appear nowhere in its code — while another test feeds a two-error document to
`cad_core.validate` and to the service and asserts the same rule codes and the
same error count come back.

A validation failure is a **result, not an exception**, and it stops the
pipeline: no geometry runs, no child process starts, and nothing is cached. A
test patches `subprocess.Popen` to raise and asserts an invalid document still
comes back as a structured failure.

## Build boundary

`build_document` in order:

1. the output names are converted **once** into the existing `BuildOptions`;
2. the document is deserialized and validated by the existing validator — a
   failure stops here;
3. a `BuildRequest` is formed, whose `document_hash` and `build_key` are its
   own computed properties. **The service invents neither**;
4. the backend serves it from the cache or builds it out of process;
5. the execution is composed into a `BuildOutcome`.

### The request model

```python
@dataclass(frozen=True)
class BuildDocumentRequest:
    document: str | bytes | Mapping[str, Any]
    outputs: Tuple[str, ...] = OUTPUT_NAMES
```

A CAD document and the outputs wanted. **Nothing else** — no LLM parameters,
no UI preferences, no user or tenant id, no authentication data, no HTTP
fields, and no filesystem path. Where artifacts physically land is the
backend's concern and is never part of a request's logical identity; a test
asserts the request's fields are exactly `document` and `outputs`.

A dumb data holder on purpose, so a transport can build one straight from a
payload. Output names are checked when the request is *executed*, so a bad one
is a structured result rather than an exception thrown inside a constructor.

### Build options: reused, not re-enumerated

`OUTPUT_NAMES` is `tuple(kind.value for kind in ArtifactKind)` — the existing
artifact kinds, asserted equal by a test. There is no second output
enumeration. Names are converted once, in one function, into the Stage 16
`BuildOptions`; an unknown name, a non-string, a bare string instead of a
sequence, or an empty selection is a `ServiceFailure.INVALID_REQUEST` result.

`featurescript` is **not** an output name. The FeatureScript generator remains
a separate derived-output path: it is never executed by a build and never
mixed into a build's outputs. A test asserts the service imports neither
`cad_core.featurescript` nor the Onshape modules and that no code in it
mentions either.

## Result composition

```python
@dataclass(frozen=True)
class BuildOutcome:
    status: BuildStatus                      # Stage 16
    document_hash: Optional[str]             # Stage 15
    build_key: Optional[str]                 # Stage 16
    cache_hit: bool                          # Stage 18
    cache_published: bool                    # Stage 18
    manifest: Optional[ArtifactManifest]     # Stage 17
    error: Optional[ServiceError]            # this layer, wrapping the below
    execution_id: Optional[str]              # Stage 16
    render_model: Optional[RenderModel]      # Stage 8, a reference
```

Every field's owner is named above. **This class defines no artifact, manifest,
error or result schema of its own** — a test parses the module and asserts no
class named `Artifact`, `ArtifactManifest`, `BuildError` or `BuildResult` is
declared in it, and lists the eight classes that are.

| `to_dict()` field | Comes from |
|---|---|
| `status`, `succeeded` | `BuildStatus` |
| `document_hash` | `serialization.part_hash`, via `BuildRequest` |
| `build_key` | `build_job.build_key_for`, via `BuildRequest` |
| `cache_hit`, `cache_published` | the cache / the isolated execution |
| `outputs` | `ArtifactManifest.kinds()` |
| `manifest` | `ArtifactManifest.to_dict()` verbatim, which is `Artifact.to_dict()` per artifact |
| `error` | `ServiceError.to_dict()`, carrying `BuildFailure` and `IsolationOutcome` values |
| `execution_id` | `BuildJob.execution_id`, from the child |

`status` reuses Stage 16's `BuildStatus`; only `SUCCEEDED` and `FAILED` occur,
because a call is synchronous. `SERVICE_STATUSES` names those two.

`render_model` is a **reference to plain data**, excluded from `to_dict()` for
the reason Stage 16 gives: the manifest already describes it — format version,
counts, bounds, winding, tessellation, checksum — and duplicating the payload
into every result is exactly what these layers avoid. A test asserts
`vertices` appears nowhere in a serialized outcome.

**No absolute filesystem path becomes a logical identity.** A test collects
the build key, the document hash and every `logical_id` from a serialized
outcome and asserts none contains a `/`, the cache directory's name or a file
extension, and that every logical id starts with the build key. Artifact
`path` fields do hold real locations — that is content, not identity, as Stage
17 established.

Nothing returned holds a kernel object. It cannot: builds run in a child
process, so no B-rep exists in the service's process at all. A test serializes
a full outcome and asserts `cadquery`, `OCP`, `TopoDS`, `Workplane` and
`object at` appear nowhere.

## Error mapping

`ServiceFailure` is a **mapping**, not a duplicate. Every value is produced
from an existing `BuildFailure` or `IsolationOutcome`, and the `ServiceError`
carries that original classification alongside its own, so nothing is lost and
nothing is flattened to a string.

| `ServiceFailure` | Produced by | Stage |
|---|---|---|
| `MALFORMED_DOCUMENT` | `DocumentParseError` — not readable JSON, or not an object | 15 |
| `INVALID_DOCUMENT` | `DocumentValidationError`, `BuildFailure.DOCUMENT_INVALID`, `IsolationOutcome.VALIDATION_FAILED` | 15/16/19 |
| `INVALID_REQUEST` | no outputs, or an output name that is not an artifact kind | this layer |
| `GEOMETRY_FAILED` | `BuildFailure.UNSUPPORTED_GEOMETRY`, `BuildFailure.GEOMETRY_FAILED`, `IsolationOutcome.BUILD_FAILED` | 16/19 |
| `OUTPUT_FAILED` | `BuildFailure.EXPORT_FAILED` — an exporter or publication refused | 16/17 |
| `EXECUTION_FAILED` | `IsolationOutcome.PROCESS_FAILED`, `TIMED_OUT`, `PROTOCOL_ERROR`, and a `CacheError` | 18/19 |
| `INTERNAL_ERROR` | `BuildFailure.INTERNAL`, `BuildFailure.JOB_STATE`, `IsolationOutcome.WORKER_ERROR` | 16/19 |

Against the taxonomy the stage asks for: `INVALID_DOCUMENT` is the brief's
`INVALID_DOCUMENT` (with `MALFORMED_DOCUMENT` split off because the
serialization layer already distinguishes unreadable JSON from an invalid
document, and flattening that would lose information); `GEOMETRY_FAILED` is
its `BUILD_FAILED`; `OUTPUT_FAILED` and `INTERNAL_ERROR` are its own; and
`EXECUTION_FAILED` is its `CACHE_OR_EXECUTION_FAILURE`. `INVALID_REQUEST` is
the one addition, so "your document is wrong" and "your request is wrong" stay
apart.

Two tests keep the mapping honest: one asserts every `BuildFailure` and every
non-success `IsolationOutcome` has an entry, another that several sources
legitimately share a target.

### The four distinctions that must survive

| | |
|---|---|
| a **validation** failure | `INVALID_DOCUMENT` / `MALFORMED_DOCUMENT`, `stage="validation"` |
| a **geometry/kernel** failure | `GEOMETRY_FAILED`, `stage="geometry"`, with rule codes |
| an **export** failure | `OUTPUT_FAILED`, `stage=<output name>`, with the output |
| a **crash or timeout** | `EXECUTION_FAILED`, `stage="process"`, with the isolation outcome |

Each is asserted to be its own failure, and each is asserted *not* to be the
others. Measured examples:

| Case | `failure` | `build_failure` | `execution_outcome` |
|---|---|---|---|
| `{ broken` | `malformed_document` | — | — |
| zero dimension | `invalid_document` (S10) | `document_invalid` | — |
| `outputs=("hologram",)` | `invalid_request` | — | — |
| through-hole missing target | `geometry_failed` (E1) | `geometry_failed` | `build_failed` |
| directory where the STEP file goes | `output_failed` | `export_failed` | `build_failed` |
| child exits with no response | `execution_failed` | — | `process_failed` |
| 0.4 s timeout | `execution_failed` | — | `timed_out` |
| cache raises | `execution_failed`, `stage="cache"` | — | — |

`ServiceError.message` is the **stable public sentence**, the contract Stage 16
established: no traceback, no filesystem path, nothing from the environment,
and never a child's `stderr`. Tests assert an export failure's message
contains no `/` and that a crashed child's `stderr` text is not in the
message.

### Exceptions versus results

**Ordinary bad input never raises.** A malformed document, an invalid
document, an unknown output name, a geometry failure, an export failure, a
crash, a timeout and a cache failure are all `BuildOutcome` results.

`ApplicationServiceError` is raised only for a programming error a transport
cannot cause: a `Part` or a wrong Python type where a document belongs, a
backend without the two methods, a cache root that does not exist. Unexpected
exceptions are **not** swallowed — there is no blanket `except Exception`
anywhere in the module, so a genuine bug still surfaces as itself.
`INTERNAL_ERROR` is reachable only by *mapping* a classification the layers
below already made structurally.

## Idempotency

`build_document(same_document, same_outputs)` gives:

- the **same** `document_hash` — Stage 15's canonical hash;
- the **same** `build_key` — Stage 16's, from the document hash and the
  canonical options;
- `cache_hit` `False` on the first call and `True` on later ones while the
  cache entry stays valid;
- `cache_published` `True` exactly on the call that built it.

Requesting the same outputs in a different order gives the same key, because
`BuildOptions` canonicalizes; requesting a different set gives a different
key. JSON text and the equivalent parsed structure give the same key. A
request object is reusable, and building twice from it does not change it.

`DocumentValidation.build_key_for(outputs)` gives a build's identity **without
building it**, via the existing `build_key_for`.

### Identity is borrowed, never invented

Only `document_hash`, `build_key` and `execution_id` exist. No random project
id, no UUID document id, no database id, no user id — a test asserts the
serialized outcome's only identity-shaped fields are exactly those three, and
that the execution id is part of neither the build key nor the document hash.

The module imports no `hashlib`: it computes no hash of its own, because the
layers below already define the two that matter. A test asserts that.

## Cache behaviour

Stage 18's cache, unchanged and not redesigned. The service's backend calls
`get_or_build_isolated`, so:

```
first call   → cache miss → isolated child build → published → cache entry
later calls  → cache hit  → no child process     → cached artifacts
```

A cache hit's artifact paths point **inside the cache root**, and the entry is
re-verified on every hit (Stage 18 recomputes each checksum from the cached
bytes), so a hit is a validated entry rather than a stored claim. A test
patches `subprocess.Popen` to raise and asserts a hit still succeeds — no
child, proved rather than inferred.

A `CacheError` is `EXECUTION_FAILED` with `stage="cache"`, never a CAD error.

The cache root is the **service's own infrastructure**, supplied to
`CadApplicationService.local(cache_root)` by whoever configures it. It is
never part of a request, never part of an identity, and never invented here.

## Backend independence

One backend, `LocalBuildBackend`: the local cache over isolated local
execution. The interface is the two methods the service actually needs —

```python
execute(request: BuildRequest) -> IsolatedExecution
lookup(request: BuildRequest)  -> IsolatedExecution | None
```

— and no more. **No plugin system, no registry, no hypothetical second
backend.** The word "local" appears nowhere in the service's public
semantics: `build_document` does not say where or how a build happens.

A future remote, cloud-worker or Onshape/MCP backend would implement the same
two methods and return the same shape. `IsolatedExecution` is reused rather
than wrapped in a third result type because its fields are transport-neutral —
an outcome, a build key, a manifest, cache flags, a structured failure — and
its outcomes (a crash, a timeout, a protocol error) are exactly what any
out-of-process backend reports. None of those backends is implemented, and
Onshape is neither called nor configured.

Two tests demonstrate the independence rather than asserting it in prose: one
constructs the service with a stub backend and checks it receives the existing
`BuildRequest` with the existing `BuildOptions`; another uses a stub to replay
a real export failure that the local backend's private build directory makes
awkward to provoke.

## Geometry across the boundary

Unchanged from Stage 19: the in-memory B-rep is never transferred and no
serialization for it was invented. The geometry artifact's measurements —
solid count, volume, bounding box, face/edge/vertex counts — travel, which is
all that artifact ever contained. `BuildOutcome` has no `geometry` field. The
render model travels as its existing canonical JSON and is reconstructed, so
`BuildOutcome.render_model` is a real `RenderModel`.

## Deliberately excluded

- **No HTTP, REST, GraphQL, gRPC** — and no transport of any kind;
- **no frontend**, no CLI (the service is exercised through its Python API);
- **no LLM, no MCP**, no prompt, no natural-language handling;
- **no authentication, authorization, user, tenant or session** — a test walks
  the syntax tree and asserts no name in the module's code contains `user`,
  `token`, `auth`, `permission`, `tenant`, `quota`, `rate_limit`, `session` or
  `api_key`;
- **no rate limits, payload limits or quotas** — these belong to a future
  transport/security layer, where the request actually arrives from outside;
  the service is called in-process and has no request to throttle;
- **no database**, no job store, no persistence beyond Stage 18's file cache;
- **no queue, broker, worker pool or scheduler**;
- **no cloud infrastructure**;
- **no FeatureScript generation** and **no Onshape** call or configuration.

## Future transport layers

What a transport has to do, and no more:

1. read a payload and construct a `BuildDocumentRequest` — a document and a
   list of output names;
2. call `validate_document`, `build_document` or `find_build`;
3. render `to_dict()`, and map `ServiceFailure` to whatever its own protocol
   uses (an HTTP status, a CLI exit code, a UI banner). That mapping is the
   transport's business, which is why this layer does not name one;
4. add its own concerns — authentication, rate limits, payload caps, request
   ids, logging — **around** the service, never inside it.

The pieces a transport will need already exist and are stable: a
content-derived `build_key` it can deduplicate and cache on, a
`document_hash` that identifies a design, a failure taxonomy detailed enough
to answer "whose fault is this?", and results that serialize to plain JSON.

## The specification is unchanged

`docs/cad-specification.md` is untouched, and so are the validator, the
geometry kernel, the exporters, the render model, the build layer, the cache
and the isolation layer. The neutral CAD specification remains the source of
truth; the canonical CAD document is authoritative; geometry, FeatureScript,
STEP, IGES, STL, the render model, their cached copies, the process that
produces them **and this service's results** are all derived.
