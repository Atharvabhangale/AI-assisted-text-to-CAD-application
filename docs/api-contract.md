# Transport-neutral API contract

Status: **Stage 21 — the data that will cross to a client, defined before any
transport exists. No HTTP.**

> CAD specification ≠ application service ≠ transport contract ≠ HTTP

## Purpose

Fix the request and response shapes a client will exchange with the
application service, so that when a transport is added it is a thin adapter
with nothing left to decide.

```
future transport      HTTP · CLI · UI · AI orchestration     (does not exist)
        │
Transport contract    cad_core.api_contract                   ← this layer
        │
Application Service   cad_core.application_service
        │
Build / Cache / Isolation
        │
CAD engine / export / render
```

### The four boundaries

| | Answers |
|---|---|
| **CAD specification** | what a part *is* — `docs/cad-specification.md` |
| **application service** | what an operation *does* — `docs/application-service.md` |
| **transport contract** | what *crosses* to a client — this document |
| **HTTP** | *how* it crosses. **Not implemented.** |

No transport is implemented here: no HTTP, REST, GraphQL, WebSocket, socket,
URL or endpoint, and this document deliberately names no endpoint. A test
asserts the module imports none of `fastapi`, `starlette`, `flask`, `django`,
`pydantic`, `graphene`, `strawberry`, `graphql`, `requests`, `httpx`,
`urllib`, `http`, `socket`, `socketserver`, `ssl`, `websocket`, `websockets`,
`aiohttp`, `tornado`, `uvicorn`, `grpc`, `sqlite3`, `sqlalchemy`, `redis`,
`boto3`, `jwt`, `hashlib` or `asyncio` — and another asserts this document
names no verb-and-path endpoint and contains no absolute web address.

**Pydantic is deliberately absent.** Plain frozen dataclasses are sufficient
for typed structures with `to_payload()` methods, and adding a validation
library now — for a wire that does not exist — would put a second schema
definition beside the ones the project already has.

## No second implementation

Every operation delegates to `CadApplicationService`. This layer validates no
document, builds no geometry, computes no hash and holds no CAD semantics. A
test walks its syntax tree and asserts the names `validate`, `build_part`,
`part_hash`, `serialize_part`, `deserialize_part`, `build_key_for`,
`export_step`, `sha256` and `new` appear nowhere in its code, and that it
imports only `application_service`, `artifact_registry` and `build_job`.

Existing types are referenced, not copied:

| Concept | Source |
|---|---|
| the build request DTO | **is** `application_service.BuildDocumentRequest`, aliased |
| `OUTPUTS` | `tuple(kind.value for kind in ArtifactKind)` |
| `STATUSES` | `BuildStatus.SUCCEEDED.value`, `BuildStatus.FAILED.value` |
| `FAILURES` | `tuple(f.value for f in ServiceFailure)` |
| document identity | `document_hash` — Stage 15's canonical hash |
| build identity | `build_key` — Stage 16's |

## Request types

### `ValidateDocumentRequest`

```json
{"document": {"schema_version": "1.0.0", "units": "mm", "...": "..."}}
```

One field, because that is the whole question.

### `BuildDocumentRequest`

```json
{"document": {...}, "outputs": ["geometry", "step", "iges", "stl", "render"]}
```

`outputs` may be omitted, meaning all of them. **This DTO is the application
service's own** — a document and output names, which is exactly what an
external build request needs — aliased the way `BuildOutput` aliases
`ArtifactKind` so the two cannot drift. A test asserts the identity and that
its fields are exactly `document` and `outputs`.

Absent by design, and asserted: no user id, tenant id, auth data, HTTP header,
filesystem path, LLM setting, UI setting or backend selection. A payload
carrying any extra field is refused.

`document` is a parsed JSON object or JSON text — the application service's own
input boundary, unchanged.

### Strictness: envelope here, content there

```python
validate_request_from_payload(payload) -> ValidateDocumentRequest
validate_request_to_payload(request)   -> dict
build_request_from_payload(payload)    -> BuildDocumentRequest
build_request_to_payload(request)      -> dict
```

`*_from_payload` is strict about the **envelope**: a payload that is not a
mapping, a missing `document`, an unknown field, a non-list `outputs` or a
document of the wrong type raises `ContractError`.

Output *names* are **not** judged here — the application service classifies an
unknown one as `invalid_request`, so exactly one layer decides what an output
is. `CadApiContract.build_document_payload` catches `ContractError` and returns
the same `invalid_request` response, so a transport working at the payload
level never has to catch anything and both kinds of client mistake come back
the same way.

`*_to_payload` emits the document **exactly as supplied**: a string stays a
string, a mapping is deep-copied. The contract holds its own structure rather
than aliasing the caller's, so neither side can surprise the other by mutating
a nested list later — and it never canonicalizes a document, see below.

## Response types

### `ValidateDocumentResponse`

```json
{"valid": true,
 "document_hash": "2fd162f9…",
 "name": "plate-100x60x10-4holes",
 "feature_count": 5,
 "document": {"…canonical CAD document…"},
 "error": null}
```

`valid` is the explicit success indicator: a validation response has no build
status, because nothing was built.

`document` is the **canonical** form of what was submitted (Stage 15). A terse
document and a fully materialised one share a hash but only one is canonical,
and returning it lets a client see exactly what was understood. That is the
service's answer, not this layer's doing.

### `BuildDocumentResponse`

```json
{"status": "succeeded",
 "succeeded": true,
 "document_hash": "2fd162f9…",
 "build_key": "a6cd6fa1…",
 "execution_id": "…",
 "cache_hit": false,
 "outputs": ["geometry", "step", "iges", "stl", "render"],
 "artifacts": [ … ],
 "error": null}
```

`status` is `BuildStatus`'s own value; a call is synchronous, so `succeeded`
and `failed` are the only two. `succeeded` is the same fact as a boolean, for
clients that would rather branch on that.

What a client can therefore learn: whether validation succeeded, the
structured validation errors, the document's identity, the build's identity,
whether it came from the cache, which artifacts exist, their formats, their
sizes, their checksums and algorithm, whether the operation failed, and the
stable failure classification.

Dropped from the application result on purpose:

- the `RenderModel` reference — the render artifact is *described*, not
  embedded (see below);
- `cache_published` — whether this call happened to fill the cache is cache
  mechanics, not the client's business. `cache_hit` is what a client needs.

## Identity fields

| Field | Identifies | Notes |
|---|---|---|
| `document_hash` | the **design** | Stage 15's canonical SHA-256 |
| `build_key` | the **build** | document hash + canonical outputs; reproducible anywhere |
| `execution_id` | **one run** | `None` on a cache hit, because nothing ran |
| `logical_id` | **one artifact** | `<build key>:<kind>` |

Nothing is invented: no random document UUID, no database id, no user id. A
test asserts the three build-level identities stay distinct and that the
execution id is part of neither of the others.

## Cache-hit semantics

`cache_hit` is a boolean on a successful response. **A cache hit is not a
status**: `status` is `succeeded` either way, and no status was added for it.
On a hit `execution_id` is `None`, because nothing executed.

Measured on the Section D document: apart from `cache_hit` and `execution_id`,
the built response payload and the cached one are **equal dictionaries** — a
test pops those two fields and compares the rest.

## Artifact representation

```json
{"kind": "step",
 "format": "step",
 "logical_id": "a6cd6fa1…:step",
 "storage": "file",
 "size_bytes": 30084,
 "checksum": "d8b91ee1…",
 "checksum_algorithm": "sha256",
 "measurements": {}}
```

| Field | Meaning |
|---|---|
| `kind` | the logical output — one of `OUTPUTS` |
| `format` | the payload's shape: `brep-in-memory`, `step`, `iges-brep`, `stl-binary`, `render-model` |
| `logical_id` | **the stable reference**: `<build key>:<kind>` |
| `storage` | `file` or `in_memory` — whether bytes exist at all |
| `size_bytes` | real bytes; `null` for the B-rep |
| `checksum` / `checksum_algorithm` | SHA-256 of those bytes, or both `null` |
| `measurements` | the artifact layer's deterministic measurements |

**No path crosses.** Not an artifact's, not the workspace's, not the cache's.
An artifact's stable transport-neutral handle is its `logical_id`, which is
reproducible on any machine while a path is machine-specific — the separation
Stage 17 exists to maintain. A test asserts the artifact payload has exactly
the eight keys above, that `path` is not among them, that the logical id
contains no `/`, and that it starts with the build key.

**No URL is invented.** The stage's allowance was that a reference be absent
or a logical artifact id; the logical id already *is* a stable
transport-neutral reference, so it is the reference and no dead `reference`
field was added. A future transport decides how bytes are fetched and will
resolve the logical id.

**The file extension is not exposed.** It is a local storage detail, and
`format` already names the logical format — a transport can derive a filename
from that if it needs one.

### In-memory artifacts

| | |
|---|---|
| `render` | `storage: "in_memory"`, with its canonical-JSON checksum, its size, and its measurements: format version, part name, units, coordinate system, winding, normal binding, vertex and triangle counts, bounds, tessellation. **The vertices, normals and triangles do not cross.** A future transport can define its own retrieval or rendering path; this contract stays metadata-oriented. |
| `geometry` | `storage: "in_memory"`, `size_bytes`, `checksum` and `checksum_algorithm` all `null` — a B-rep has no canonical byte form here and none was invented. Its measurements do cross: solid count, volume, bounding box, face/edge/vertex counts. **No kernel object, in any form.** |

A test asserts the strings `vertices`, `normals`, `brep_bytes` and `shape`
appear nowhere in a full Section D response, and that the geometry artifact's
measured volume matches the analytic value.

## Error representation

```json
{"failure": "geometry_failed",
 "message": "through_hole 'bore': its centreline does not intersect …",
 "output": null,
 "rule_codes": ["E1"],
 "validation_errors": []}
```

`failure` is the stable classification a client branches on — one of
`FAILURES`, which is the application taxonomy verbatim. The six distinctions
established internally survive as six distinct values:

| `failure` | Cause |
|---|---|
| `malformed_document` | the document is not readable JSON, or not an object |
| `invalid_document` | it violates the V1 static rules — `rule_codes` and `validation_errors` say how |
| `invalid_request` | no outputs, an unknown output name, or a payload that is not a payload |
| `geometry_failed` | the engine refused, or a geometric rule failed — `rule_codes` carries E1–E5 |
| `output_failed` | an exporter or publication refused — `output` names which |
| `execution_failed` | the build could not be executed, or the cache could not be used |
| `internal_error` | an unexpected internal error |

Nothing is flattened to a message, and tests assert the malformed, invalid,
invalid-request, geometry, output and execution cases are all different
values.

`message` is the application service's **stable public sentence**: no
traceback, no filesystem path, nothing from the environment, and never a child
process's `stderr`.

Two classifications are the exception, and it was a measured one. The
service's messages for them are safe but still describe *how the system
works* — "the isolated CAD process ended without a result (exit code 0)",
"exceeded its 0.4 second timeout and was terminated", "the build cache could
not be used (CacheError)". An exit code, a killed child and an exception class
name are process details a client can do nothing with, so `execution_failed`
and `internal_error` carry a fixed client-facing sentence from
`SUBSTITUTED_MESSAGES` instead, and the underlying message stays available
internally for diagnosis. Every other failure keeps the service's own message
verbatim, because a rule violation, a geometric failure and an exporter's
refusal all describe the *request* — which is exactly what a client needs.

`validation_errors` are the validator's own five fields — `rule`, `message`,
`feature_id`, `field_path`, `feature_index` — neither added to nor dropped. A
test compares them field by field against `cad_core.validate`'s own errors.

### What the error deliberately omits

The application service's `ServiceError` keeps more than this, and the
contract drops it on purpose:

| Dropped | Why |
|---|---|
| `execution_outcome` | **a process detail.** That a build was killed at a timeout rather than crashing is not a client's business. A test asserts a crash and a timeout come back as the *same* `execution_failed`, and that the payload names no outcome, child, exit code or termination |
| `build_failure` | `failure` already classifies it; keeping it would duplicate `BuildError` internals for no gain |
| `stage` | its values include `process` and `cache`, which describe how the system is built. `failure` says what went wrong and `output`/`rule_codes` say where |
| every diagnostic field | see below |

## Diagnostic exclusions

Diagnostics stay internal, and none of the following ever crosses:

- a traceback or an exception object;
- a filesystem path — an artifact's, the workspace's or the cache's;
- a child process's `stdout` or `stderr`;
- environment information;
- a temporary workspace name;
- an exit code or any other process detail;
- a CAD kernel object.

This is enforced, not merely intended. Every response payload in the test
suite is walked recursively and asserted to contain none of `Traceback`,
`cadquery`, `CadQuery`, `OCP`, `TopoDS`, `Workplane`, `object at 0x`,
`cad-isolated`, `/tmp`, `gettempdir`, `stderr` or `BRepFilletAPI`, nor the
test's own temporary directory or cache root; that no string value starts with
`/`; and that no key at any depth contains `user`, `token`, `auth`,
`credential`, `secret`, `password`, `tenant`, `session`, `quota`,
`rate_limit`, `api_key`, `prompt`, `llm`, `mcp`, `openai`, `anthropic`,
`traceback`, `diagnostic`, `stderr`, `stdout`, `path`, `workspace`,
`directory`, `exception`, `api_version` or `contract_version`.

One test makes the point directly: a stand-in child writes
`Traceback: the child died in /tmp/secret-place` to `stderr`, and neither the
word nor the path reaches the payload.

## Serialization approach

**Requests and responses map to JSON-compatible Python structures, not to
canonical bytes.**

There is exactly one canonical byte form in this system — the CAD document's
(Stage 15) — and it stays where it is. A second canonical format is precisely
what must not exist, so:

- a document travels inside a payload **untouched**: this layer does not
  reorder its keys, materialise its defaults or re-encode it. A test submits a
  terse box and asserts the request payload still lacks the `position` the
  canonical form would have;
- only `validate_document` returns a canonical document, and that is the
  application service's answer, not this layer's doing;
- no payload is hashed, so byte-exactness would buy nothing, and fixing key
  order would imply a canonical form this layer does not own.

What *is* guaranteed is **structural determinism**: the same application
result maps to an equal payload every time, and `json.dumps(payload,
sort_keys=True)` of two mappings of one result is byte-identical. A test
asserts both. A transport chooses its own encoder.

Each DTO carries a `to_payload()` returning plain `dict`/`list`/`str`/`int`/
`float`/`bool`/`None`, and every payload in the suite round-trips through
`json.dumps`/`json.loads` unchanged.

## Versioning policy

**No API version is introduced, and none is needed yet.**

There is no wire, no deployed client and no compatibility window to manage, so
a version number now would be one nobody could honour — and a wrong version
number is worse than none. The contract is a Python module versioned in source
control; a change to it is a change to the source tree, visible in a diff.

When a transport exists and a client is deployed against it, a wire-level
version will be justified — at that point there is something to be compatible
*with*. The reason will be exactly that, and it belongs to the transport
layer, which is where a client's expectations actually live.

The version numbers that do exist are unrelated to each other and to this:

| Constant | Versions |
|---|---|
| the document's `schema_version` | the **V1 CAD specification** |
| `RENDER_FORMAT_VERSION` | the render representation |
| `CACHE_SCHEMA_VERSION` | the cache entry file format |
| `IPC_PROTOCOL_VERSION` | the worker envelope |

All four read `1.0.0` today by coincidence. Tests assert this module defines no
constant ending in `_VERSION`, that no payload key other than the CAD
document's own `schema_version` contains "version", and that the document's
version travels untouched inside the document.

## Relationship to CAD document serialization

**Transport serialization is separate from CAD document serialization.**

| | |
|---|---|
| CAD document | Stage 15 canonical JSON: fixed key order, materialised defaults, minimal whitespace, UTF-8, **byte-exact and hashed**. The `document_hash` is the SHA-256 of those bytes |
| transport payload | a JSON-compatible structure, deterministic in content, **not hashed and not byte-defined** |

A document inside a request payload is the client's own bytes, unchanged. The
`document_hash` in every response is computed by Stage 15 from the canonical
form — the contract never computes it, and a test asserts the module never
calls `part_hash`.

## Deliberately excluded

- **no HTTP, REST, GraphQL, gRPC or WebSocket** — and no endpoint in this
  document;
- **no frontend**;
- **no authentication or authorization**, no user, tenant, session, token or
  API key — a test asserts no name in the module's code contains any of those,
  nor `header`, `endpoint`, `route`, `status_code` or `url`;
- **no rate limits, payload caps or quotas** — these belong to the future
  transport/security layer, where a request actually arrives from outside;
- **no LLM, no MCP**, no prompt handling;
- **no database or storage service**;
- **no network library of any kind**;
- **no Pydantic**;
- **no API version**;
- **no pagination, no streaming, no partial results, no cancellation, no
  progress reporting** — a call is synchronous and returns once.

## Future HTTP mapping

What a transport will have to do, and no more:

1. read a body and hand it to `build_document_payload` or
   `validate_document_payload` — payload in, payload out;
2. map `failure` to its own protocol's vocabulary. That mapping is the
   transport's business, which is why this contract does not name one; the
   taxonomy is deliberately shaped so it can be made (a client mistake, an
   input problem and a system failure are already distinguishable);
3. decide how artifact bytes are fetched, resolving a `logical_id`;
4. add its own concerns — authentication, rate limits, payload caps, request
   ids, logging, content negotiation — **around** the contract, never inside
   it.

Everything a transport needs is already stable: a content-derived `build_key`
to cache and deduplicate on, a `document_hash` that identifies a design, a
failure taxonomy detailed enough to answer "whose fault is this?", artifact
handles that do not depend on a filesystem, and payloads that are plain JSON.

## The specification is unchanged

`docs/cad-specification.md` is untouched, and so are the validator, the
geometry kernel, the exporters, the render model, the build layer, the cache,
the isolation layer and the application service. The neutral CAD
specification remains the source of truth; the canonical CAD document is
authoritative; everything else — geometry, FeatureScript, STEP, IGES, STL, the
render model, cached copies, the isolated process, the service's results **and
this contract's payloads** — is derived.
