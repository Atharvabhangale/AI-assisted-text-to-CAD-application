# HTTP API

Status: **Stage 22 — the first HTTP transport, as a thin adapter over the
transport-neutral contract. Stage 23 added artifact delivery. Local
development only.**

> **HTTP API contains no CAD business logic.**

## The four layers

| | Answers | Lives in |
|---|---|---|
| **CAD specification** | what a part *is* | `docs/cad-specification.md` |
| **Application Service** | what an operation *does* | `cad_core.application_service` |
| **Transport Contract** | what *crosses* to a client | `cad_core.api_contract` |
| **HTTP API** | *how* it crosses | `apps/api` — this document |

```
HTTP request   ->  Pydantic envelope  ->  transport contract  ->  application service
HTTP response  <-  contract payload   <-  application result   <-  build / cache / isolation
```

The HTTP layer contains **no** CAD validation logic, geometry, exporter, cache
logic, process management, document canonicalization, build-key computation,
artifact checksums, LLM or MCP. Tests assert it imports none of
`cad_core.local_cad`, `cad_core.edge_selection`, `cad_core.geometry`,
`cadquery`, `OCP`, the three exporters, `cad_core.render_model`,
`cad_core.local_build_cache` or `cad_core.isolated_worker`, and that the names
`validate`, `build_part`, `part_hash`, `serialize_part`, `deserialize_part`,
`build_key_for`, `export_step`, `export_iges`, `export_stl`,
`build_render_model`, `get_or_build`, `execute_isolated`, `file_checksum` and
`sha256` appear nowhere in its code. Its only `cad_core` imports are
`api_contract`, `application_service`, `artifact_registry` (for the checksum
algorithm name) and `isolated_execution` (for the default timeout) — asserted
exactly.

`cad_core` remains usable without FastAPI: no module in it imports `fastapi`,
`starlette`, `pydantic`, `httpx` or `httpx2`, and a test runs the contract in
a child process with all of those blocked on import.

## Measured environment

Determined from the installed environment, not assumed:

| Package | Version |
|---|---|
| FastAPI | **0.141.1** |
| Pydantic | **2.13.5** |
| pydantic-core | 2.46.5 |
| Starlette | **1.6.0** |
| httpx2 | 2.12.0 (test only) |

`fastapi` and `httpx2` are the only packages added. Starlette 1.6.0's
`TestClient` is the supported testing mechanism here, and it deprecates
`httpx` in favour of `httpx2` — measured from its own warning, which is why
the test extra names `httpx2`. `uvicorn` is declared as an optional `serve`
extra and imported by nothing.

## Endpoints

Four, and only these four.

### `GET /health`

```json
{"status": "ok"}
```

Whether the process is up. It executes no CAD, inspects no cache, starts no
worker, contacts no external service, and reports no version metadata —
there is no application version contract to report. Tests assert three
consecutive calls launch no child process and leave the cache directory
empty.

### `POST /validate`

```json
// request
{"document": {"schema_version": "1.0.0", "units": "mm", "...": "..."}}

// 200 response
{"valid": true,
 "document_hash": "2fd162f9…",
 "name": "plate-100x60x10-4holes",
 "feature_count": 5,
 "document": {"…canonical CAD document…"},
 "error": null}
```

`ValidateBody` → `CadApiContract.validate_document_payload` →
`CadApplicationService.validate_document` → the existing validator.

**Always 200 for a well-formed request, including `"valid": false`.** The
client asked whether a document is acceptable; a completed check is a
successful request, and the answer is the resource. Only a malformed
*request* is a 4xx here. This is the one place the two endpoints' status
semantics differ, and it is deliberate: `/validate` answers a question,
`/build` performs an action.

Validating never launches a child process — asserted with `Popen` patched to
raise.

### `POST /build`

```json
// request
{"document": {...}, "outputs": ["geometry", "step", "iges", "stl", "render"]}
```

`outputs` may be omitted, meaning all of them. `BuildBody` →
`CadApiContract.build_document_payload` →
`CadApplicationService.build_document` → cache, then isolated execution.

Representative 200 response for the Section D four-hole plate:

```json
{"status": "succeeded",
 "succeeded": true,
 "document_hash": "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc",
 "build_key": "a6cd6fa167f396db95f7bb618a98ef9867debaecc014846264b591ac30e2ca21",
 "execution_id": "…",
 "cache_hit": false,
 "outputs": ["geometry", "step", "iges", "stl", "render"],
 "artifacts": [
   {"kind": "geometry", "format": "brep-in-memory", "storage": "in_memory",
    "logical_id": "a6cd6fa1…:geometry", "size_bytes": null,
    "checksum": null, "checksum_algorithm": null,
    "measurements": {"solid_count": 1, "volume_mm3": 57989.3807, "…": "…"}},
   {"kind": "step", "format": "step", "storage": "file",
    "logical_id": "a6cd6fa1…:step", "size_bytes": 30084,
    "checksum": "…64 hex…", "checksum_algorithm": "sha256",
    "measurements": {}}
 ],
 "error": null}
```

The repeat of an identical request is a **cache hit**: `200`,
`"cache_hit": true`, `"execution_id": null`, the same `build_key`, and a body
otherwise identical to the first. A test asserts the cached body equals the
transport contract's own payload for that request, field for field.

### `GET /artifacts/{artifact_id}`

Deliver a file-backed artifact's bytes. The path parameter is the
**logical artifact id** a build response already returned
(`<build key>:<kind>`) — never a path, a filename or an extension.

```
GET /artifacts/a6cd6fa1…:step
200  application/octet-stream
     content-disposition: attachment; filename="a6cd6fa1….step"
     content-length: 30084
     etag: "<the artifact's SHA-256>"
     <the exact cached bytes>
```

| | |
|---|---|
| **downloadable kinds** | `step`, `iges`, `stl` — the file-backed ones, from `FILE_KINDS` |
| **not downloadable** | `geometry` and `render` have no file; both answer **404** with `reason: artifact_not_downloadable`. No B-rep serialization and no second render format is invented |
| **Content-Type** | `application/octet-stream` for all three. Python's IANA table does offer `model/step`, `model/iges` and `model/stl` — verified locally — and they are deliberately not used: these are opaque bytes to save, `Content-Disposition: attachment` says so, and octet-stream cannot tempt a browser into rendering a CAD file. No custom type is invented either way |
| **Content-Disposition** | `attachment; filename="<build key><extension>"`, from the validated manifest record. Never from the URL, never the original build directory's filename. A build key is hexadecimal and the extension comes from the registry, so CR, LF and quotes are impossible — and asserted so |
| **ETag** | the artifact's own SHA-256, quoted. Not a second hash |
| **Content-Length** | the real length of the bytes served |
| **integrity** | the payload is read once and its length and SHA-256 are checked against the manifest *after* the read, so what is delivered is exactly what was verified |
| **Range requests** | not implemented; see `docs/artifact-delivery.md` for why |
| **HEAD** | not offered. FastAPI adds none for a `GET` route and this stage adds no extra route, so `HEAD` answers **405** |

Statuses: **200** with the bytes, **400** for an id that is not an identifier,
**404** for an artifact that is not available or has no bytes, **500** if
delivery itself failed. A refusal body is
`{"error": {"reason": ..., "message": ...}}`.

`reason` is deliberately not the build taxonomy's `failure`: a download is not
a build.

**Path traversal is refused before any filesystem access.** The id is
whitelisted against `[0-9a-f]{64}:<kind>`, every path comes from the cache's
own validated manifest, real-path containment is checked with
`os.path.commonpath`, and symlinks are rejected outright. The full flow, the
symlink measurement and the security limits are in
`docs/artifact-delivery.md`.

## HTTP status mapping

The line a status code draws here is **whose fault** the failure is.

| Case | `error.failure` | Status |
|---|---|---|
| successful validation (whatever the answer) | — | **200** |
| successful build | — | **200** |
| malformed request: bad JSON, missing field, unknown field, wrong type | `invalid_request` | **422** |
| invalid output selection | `invalid_request` | **422** |
| unreadable CAD document (on `/build`) | `malformed_document` | **422** |
| invalid CAD document (on `/build`) | `invalid_document` | **422** |
| geometry failure (E1–E5, engine refusal) | `geometry_failed` | **422** |
| export or publication failure | `output_failed` | **500** |
| execution or cache failure (crash, timeout) | `execution_failed` | **503** |
| unexpected internal error | `internal_error` | **500** |
| artifact id that is not an identifier | `artifact_id_invalid` | **400** |
| artifact unavailable, or with no bytes to deliver | `artifact_not_found` / `artifact_not_downloadable` | **404** |
| artifact delivery failed unexpectedly | `delivery_failed` | **500** |
| wrong method on an existing route | — | 405 (FastAPI) |
| unknown route | — | 404 (FastAPI) |

Six statuses in total: `200`, `400`, `404`, `422`, `500`, `503`.

- **422 Unprocessable Content** for everything the client can fix — a bad
  envelope, an unbuildable document, an impossible geometry. The request was
  well-formed HTTP and JSON; it just cannot be processed as sent.
- **500** when the server failed at something that was not the client's fault:
  an exporter refusing, or an unexpected error.
- **503** for an execution or cache failure, because the same request may well
  succeed on a retry — that distinction is the reason not to fold it into 500.

A status is chosen **only** from the contract's `succeeded` flag and `failure`
value. No Python exception class reaches the mapping, and nothing collapses to
a blanket 500 — tests assert every `ServiceFailure` has a status, that client
faults are 4xx and server faults 5xx, and that `execution_failed` and
`internal_error` differ. An unrecognised `failure` value maps to 500: the
taxonomy is closed, so an unknown one means this map is out of date, which is
a server problem.

## Error responses

**Every response from a route has one shape** — the shape that route's success
would have had, with the failure under `error`. So `error.failure` is always
in the same place, whether the request was rejected by FastAPI, by the
transport contract or by the application service.

```json
// POST /build, 422
{"status": "failed", "succeeded": false,
 "document_hash": null, "build_key": null, "execution_id": null,
 "cache_hit": false, "outputs": [], "artifacts": [],
 "error": {"failure": "invalid_document",
           "message": "the CAD document violates 1 V1 rule(s): S10",
           "output": null,
           "rule_codes": ["S10"],
           "validation_errors": [{"rule": "S10", "message": "…",
                                  "feature_id": "plate",
                                  "field_path": "features[0].size.x",
                                  "feature_index": 0}]}}
```

```json
// POST /validate, 422 (malformed request)
{"valid": false, "document_hash": null, "name": null,
 "feature_count": null, "document": null,
 "error": {"failure": "invalid_request",
           "message": "the request body is not a valid request (document: missing)",
           "output": null, "rule_codes": [], "validation_errors": []}}
```

FastAPI's default `{"detail": [...]}` body is **replaced** by a
`RequestValidationError` handler that reports the same rejection in the
contract's error shape. The message is built from Pydantic's own error type
and location — which describe the request's structure — never from the raw
exception string.

### Never in a response

No traceback, `stderr`, filesystem path, temporary workspace name, exit code,
process id, kernel diagnostic, credential or environment variable. This is
enforced: every response body in the suite is walked recursively and asserted
to contain none of `Traceback`, `cadquery`, `OCP`, `TopoDS`, `Workplane`,
`object at 0x`, `cad-isolated`, `/tmp`, `/usr/`, `/home/`, `PYTHONPATH`,
`LD_LIBRARY_PATH`, `stderr`, `site-packages`, `BRepFilletAPI` or `exit code`,
nor the test's own temporary directory; that no string value starts with `/`;
and that no key at any depth is `path`, `workspace`, `directory`,
`cache_root`, `exit_code`, `child_pid`, `pid`, `traceback`, `diagnostic`,
`stderr`, `stdout`, `environment`, `detail`, `execution_outcome`,
`build_failure`, `stage`, `token`, `secret`, `password` or `credential`.

One test makes the point directly: a stand-in child writes
`Traceback: died in /tmp/secret-place (exit code 9)` to `stderr`, and the
response is a clean 503 with none of it.

For an **unexpected** internal error the traceback goes to the server log via
`logger.exception` and the client gets a fixed body with
`"failure": "internal_error"`. A test raises `RuntimeError("secret internals
at /tmp/x")` inside the service and asserts neither the message nor the
exception class name appears in the 500 body.

## Request validation, in the right layer

Three different things, kept apart:

| Problem | Rejected by | Status |
|---|---|---|
| malformed HTTP JSON or envelope schema | FastAPI / Pydantic | 422 |
| invalid CAD document | the existing V1 validator, via the service | 422 on `/build`, 200 with `valid: false` on `/validate` |
| invalid build output selection | the application service | 422 |

**S1–S20 are not reimplemented in FastAPI or Pydantic.** The Pydantic models
check that the body is a JSON object with exactly the expected fields of the
expected JSON types, and nothing more.

### No CAD value is ever coerced

The CAD document is typed `Dict[str, Any]`, so **Pydantic never looks inside
it**. Measured in this environment:

| Sent as | Reaches the validator as | Result |
|---|---|---|
| `"x": "100"` | the string `"100"` | rejected, rule S19 |
| `"x": true` | the boolean `True` | rejected, rule S19 |
| `"x": null` | `None` | rejected, rule S19 |
| `"x": [100]` | a list | rejected, rule S19 |

So a malformed CAD number cannot become a valid one in transit, and a test
asserts each case end to end over HTTP: the transport accepts the envelope,
the domain refuses the value.

The envelope itself is not coerced either — Pydantic rejects `1` and `true`
where an output name belongs rather than stringifying them, and rejects a
document that is a list, a string, a number or null.

An **omitted** `outputs` means all outputs. An **explicit `null`** is not the
same thing and is not quietly read as an omission: `model_dump(exclude_unset=True)`
keeps it, and the contract refuses it with `invalid_request`.

## Artifact exposure

The build response carries the transport contract's artifact representation
verbatim: `kind`, `format`, `logical_id`, `storage`, `size_bytes`, `checksum`,
`checksum_algorithm`, `measurements`. A test asserts those eight keys exactly.

- **no filesystem path** — an artifact's handle is its `logical_id`
  (`<build key>:<kind>`), reproducible on any machine;
- **no download URL** — none is invented, and there is no artifact-bytes
  endpoint. A later stage can define one, resolving the logical id;
- **no B-rep over HTTP** — `geometry` reports measurements with `size_bytes`,
  `checksum` and `checksum_algorithm` all `null`, and there is no B-rep
  endpoint;
- **no render payload** — the `render` artifact is described, as the contract
  already specifies. No second JSON representation of the render model
  exists, and `vertices` and `normals` appear nowhere in a response.

## Configuration

Two settings, both explicit:

```python
from cad_api.app import create_app
from cad_api.config import ApiConfig

app = create_app(ApiConfig.for_cache_root("/some/chosen/cache"))
```

| | |
|---|---|
| `cache_root` | where builds are cached. **Required**, must already exist |
| `timeout_seconds` | the isolated build timeout; defaults to the isolation layer's own |

**No default cache path is invented** — not `~/.cache`, not a temporary
directory, not the current working directory. A server that silently writes
artifacts somewhere nobody chose is the kind of surprise these layers avoid.
A non-existent root or a non-positive timeout raises `ConfigurationError`.

For launching under an ASGI server, which can only import a module-level
object, `cad_api.app:app_from_environment` reads two explicitly named
variables:

| Variable | Meaning |
|---|---|
| `CAD_API_CACHE_ROOT` | required; the build cache root |
| `CAD_API_TIMEOUT_SECONDS` | optional |

Nothing else is read from the environment — a test parses `config.py` and
asserts those two names are the only environment keys in it. **No credential,
token or cloud variable is read**, and none is needed.

The application service, its cache and its executor are constructed **once**,
at `create_app` time, and held on `app.state`. No request builds a service, a
cache or a path.

## Deliberately not exposed

- document editing, deletion, versioning or comparison;
- FeatureScript generation, in any form;
- filesystem browsing or arbitrary file download — `GET /artifacts/{id}` takes
  a logical id, never a path, and can reach nothing outside a validated cache
  entry;
- `geometry` or `render` bytes, and any B-rep endpoint;
- an artifact listing endpoint: a build response is the only source of ids;
- job control, job listing or job cancellation;
- anything Onshape or MCP;
- WebSockets, GraphQL, server-sent events, streaming or background tasks —
  tests assert no `WebSocket`, `BackgroundTasks`, `add_task` or `create_task`
  appears in the code;
- CORS and any other middleware — none is added, and a test asserts
  `add_middleware` and `CORSMiddleware` appear nowhere. When a same-origin
  development frontend needs it, it belongs in this application's
  configuration, never in domain code.

## OpenAPI

FastAPI generates `/openapi.json` automatically. No OpenAPI file is
hand-written, and **the generated schema is not the source of truth for CAD
semantics** — the transport-neutral contract is. Consequently no
`response_model` is declared: mirroring the response DTOs in Pydantic would
create a second definition of the contract, so the generated schema describes
the two request envelopes (`ValidateBody`, `BuildBody`) and leaves responses
generic. A test asserts the schema lists exactly the four routes and defines
no response, artifact, error or CAD model.

## Logging

One logger, `cad_api`, used for exactly one thing: recording an unexpected
internal error server-side. CAD documents are never logged — a document is
user content, and putting a whole design in a server log by default would be
gratuitous. No credential is logged, because none is read. No traceback ever
reaches a response. There is no observability stack, no metrics, no tracing
and no request log.

## Security limitations

**This stage is for local development. It is not production secure, and
nothing here pretends otherwise.**

The current API has:

- **no authentication** — every request is anonymous;
- **no authorization** — there are no permissions to check;
- **no rate limiting** — a client may call `/build` as fast as it likes, and
  each miss runs a real CAD build;
- **no user isolation** — one cache and one service are shared by every
  caller, and a build key is global: any client can observe another's cache
  hit, and **can download another's artifacts** if it knows the build key;
- **no production sandboxing** — Stage 19's process isolation is crash
  containment, not a security sandbox: the child runs as the same OS user with
  the same filesystem permissions, with no seccomp, container, network
  isolation or privilege dropping;
- **no payload size limit**, no request timeout of its own, no CORS policy,
  no TLS termination and no audit trail.

Anything exposed beyond a trusted local machine needs all of that added first,
in the transport layer, where a request arrives from outside.

## Not added in this stage

No Docker, no production CLI, no second ASGI server, no frontend, no LLM, no
MCP, no database, no cloud storage, no rate limiting, no background workers,
no WebSockets, no GraphQL and no authentication. Nothing in `cad_core`
changed: the geometry kernel, the exporters, the render model, the build
layer, the cache, the isolation layer, the application service and the
transport contract are all untouched, and `docs/cad-specification.md` remains
the source of truth.
