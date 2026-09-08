# Build result retrieval

Status: **Stage 24 — read-only lookup of a published build by its build key.
Local development only.**

> **This endpoint retrieves successfully published derived results. It is not
> a job-status service and not a document database.**

## Purpose

Let a client come back later with only the identity a build already gave it,
and get that build's result — including the artifact ids it needs to download
bytes.

```
POST /build
     │  build_key
GET /builds/{build_key}
     │  artifacts[].logical_id
GET /artifacts/{artifact_id}
     │
STEP / IGES / STL bytes
```

Nothing new was stored to make this work. The **cache is the only source** of
a completed-build lookup: no database, no SQLite, no JSON store, no build
index, no job registry and no document store was added, and none is implied.

## The endpoint

```
GET /builds/{build_key}
```

One endpoint, because one is enough: it already returns everything a client
needs — whether the build exists, its status, the document hash, the build
key, the cache state, which outputs were requested, and every artifact's
logical id, format, storage, size, checksum and measurements. There is no
second endpoint for artifacts-of-a-build, no cache browsing route and no
document route.

### Build-key syntax

A build key is **64 lowercase hexadecimal characters** — the hex digest of the
Stage 16 build key's SHA-256. That syntax now lives beside the key itself, in
`cad_core.build_job`:

```python
BUILD_KEY_LENGTH   = hashlib.new(BUILD_KEY_ALGORITHM).digest_size * 2   # 64
BUILD_KEY_PATTERN  = re.compile(r"\A[0-9a-f]{64}\Z")
is_build_key(value) -> bool
```

`BUILD_KEY_LENGTH` is **derived from the algorithm** rather than written down,
so the two cannot drift, and a test asserts the derivation. `is_build_key` is
a **shape check, not an existence check**: it says a string could be a build
key, never that any build has that key, and it computes no hash — a test
patches `hashlib.new` to raise and asserts it still answers.

Stage 23's artifact-id pattern now derives its build-key half from
`BUILD_KEY_PATTERN` too, so one definition serves the whole system.

**No hashing is duplicated and no key is recomputed** from a guessed document
just to check the path.

## Successful response

**200**, and the body is the *same transport-contract build response* that
`POST /build` returned for that build:

```json
{"status": "succeeded",
 "succeeded": true,
 "document_hash": "2fd162f9eec5fc68abf84533d09c66bf39c514760073bba1e714c05c37cd71bc",
 "build_key": "a6cd6fa167f396db95f7bb618a98ef9867debaecc014846264b591ac30e2ca21",
 "execution_id": null,
 "cache_hit": true,
 "outputs": ["geometry", "step", "iges", "stl", "render"],
 "artifacts": [ … ],
 "error": null}
```

**No new schema.** The route calls `api_contract.build_response`, the same
mapping the build route uses, so the artifact representation is
identical — `kind`, `format`, `logical_id`, `storage`, `size_bytes`,
`checksum`, `checksum_algorithm`, `measurements` — and no `BuildDetail`
hierarchy exists. A test asserts the retrieved body equals the built body
field for field, apart from the two fields below.

### The two creation-time fields, decided explicitly

| Field | Value | Why |
|---|---|---|
| `cache_hit` | **`true`** | the artifacts came from the cache; this call built nothing. That is what the field means in a build response, so it means the same here |
| `execution_id` | **`null`** | a retrieval is not an execution. **No execution id is invented for a GET**, and none is stored to be returned |

### Status

`succeeded`, always — the cache holds nothing else. **There is no persistent
job store in this stage**, so a retrieval never claims `queued` or `running`,
and no staging directory is read to guess at a build in flight. A build is
either published and retrievable, or not available.

`outputs` is the manifest's kinds, which *are* the requested outputs: a
successful build's artifact list holds exactly what was asked for (Stage 16).

## Unknown, malformed and unavailable builds

| Case | `reason` | Status |
|---|---|---|
| the parameter is not a build key | `build_key_invalid` | **400** |
| no published build has that key | `build_not_found` | **404** |
| the entry exists but does not validate | `build_not_found` | **404** |
| retrieval failed unexpectedly | `retrieval_failed` | **500** |

A refusal body is `{"error": {"reason": ..., "message": ...}}` and nothing
else.

**A key nothing was built under and an entry that does not validate are one
answer** — one reason, one message, *"the build is not available"* — because
telling them apart would report on the cache's contents. A test corrupts an
entry two different ways and asserts both bodies are identical to each other
*and* to the body for a key that never existed. Another asserts a key one
character away from a real one is answered exactly like any other miss.

A malformed key is separated because the syntax is **public**: it is in the
transport contract and every build response returns one, so saying "that is
not a build key" reveals nothing and saves the client a guess.

`reason` is deliberately not the build taxonomy's `failure`: that classifies
builds, and a retrieval is not a build. It matches Stage 23's delivery idiom.

## What a retrieval does not do

A GET is **not a new build**. It does not start a child process, build
geometry, run an exporter, write to the cache or repair anything. Tests patch
`subprocess.Popen`, `local_cad.build_part` and `LocalBuildCache.publish` to
raise and assert a retrieval still answers; another snapshots every file in
the cache entry with its size and content digest, retrieves three times, and
asserts the snapshot is unchanged and `staging/` is empty.

**A corrupt entry is never repaired by a GET.** A test writes garbage over a
payload, retrieves twice, and asserts the garbage is still there byte for
byte. A subsequent `POST /build` rebuilds and replaces the entry through the
existing Stage 18 machinery, after which retrieval resumes — also tested.

## Application-service lookup

```python
service.find_build_by_key(build_key)  -> BuildOutcome | None
```

`find_build(request)` **cannot** serve this, and that determination is why a
second operation exists: it derives the build key *from* a document, and a
caller doing a retrieval has only the key. Overloading one method on two
argument types would have been worse than naming the two questions
separately.

The chain, with no second cache abstraction:

```
CadApplicationService.find_build_by_key(build_key)
        │   is_build_key -- the public syntax
LocalBuildBackend.find(build_key)
        │
isolated_execution.cached_execution_for_key(build_key, cache)
        │   the same function cached_execution now calls
LocalBuildCache.lookup(build_key)          the existing Stage 18 validation
```

`cached_execution_for_key` was **extracted** from `cached_execution`, which
now calls it, so "a cache hit as a structured execution" still has one
definition. The backend gained one method, `find`, the read-only counterpart
of `lookup` — its interface is now three methods, and a service given a
backend without `find` is refused.

`None` for a malformed key, an unknown key and an invalid entry alike. A
`CacheError` is a `None` too: a cache that cannot be used is not a build
result, and a retrieval reports availability rather than why something is
unavailable.

Tested directly at the service layer, not only through HTTP: known build,
unknown key, malformed keys (never reaching the cache — `LocalBuildCache.lookup`
is patched to raise), corrupt entry, missing manifest, relocation, a second
cache root, and that a retrieval builds and writes nothing.

## Relationship to the other endpoints

| | |
|---|---|
| `POST /build` | performs a build and returns its key. The **only** source of build keys — nothing enumerates them |
| `GET /builds/{build_key}` | returns that build's published result, read-only |
| `GET /artifacts/{artifact_id}` | returns one artifact's bytes, using a `logical_id` from either of the above. **Unchanged by this stage** |

The intended flow is tested end to end for the Section D plate: build,
retrieve by key, then download all three file artifacts and check each
against the checksum the retrieval reported. A retrieval's `geometry` and
`render` ids are still refused by the artifact endpoint as not downloadable —
also tested.

No URL is generated anywhere in `cad_core`: a client composes
`/artifacts/{logical_id}` itself, and the contract still invents no URL.

## Cache relationship

The cache is derived-result storage, and retrieval reads it through the
existing validation. A build is retrievable exactly when its entry validates:
manifest present, parseable, current schema, naming this build key, one record
per kind with correct logical ids and formats, every payload present with a
matching size and a **recomputed** SHA-256.

Tested corruptions, all answering "not available": removed manifest, corrupt
manifest, altered checksum, altered size, deleted payload, corrupted payload,
manifest naming another build, manifest with a wrong document hash.

**Cache relocation** works, because logical identity holds no path: a test
builds, retrieves, moves the whole cache root, points a new application at it,
and asserts the retrieved body is *identical* — and that the artifacts are
still downloadable from the new root. The service-layer test additionally
asserts every restored artifact path is inside the moved root.

A different cache root simply does not know the build.

## No document persistence

There is **no** `GET /documents/{hash}`, and there is no document store. A
document hash identifies content, and the system stores **derived build
artifacts only** — a build key's entry records the hash of the document it
came from, not the document.

A test makes the point: a document hash is, syntactically, a valid build key
(both are SHA-256 hex), and `GET /builds/{document_hash}` returns
`build_not_found`. `GET /documents/{hash}` is a 404 because no such route
exists.

## Security limitations

**Not secure for multi-user or production use.**

- **no authentication** — every request is anonymous;
- **no authorization** — anyone who can reach the port and knows a build key
  can retrieve that build's result and download its artifacts;
- **no tenant or user isolation** — one cache is shared by every caller, and a
  build key is global;
- **no rate limiting**, no audit trail.

What this stage does establish is that the endpoint is **not a cache browser**:

- the only accepted identifier is a build key matching a strict public
  syntax, checked before the cache is asked anything;
- a malformed key never becomes a filesystem path — the cache layer resolves
  the key, and a test patches `LocalBuildCache.lookup` to raise while feeding
  the retriever `/etc/passwd`, `../../secret`, `..\..\secret`, NUL bytes, CRLF
  and directory names, and asserts every one is refused without the cache
  being consulted;
- there is no listing, no enumeration, no manifest download and no directory
  route. `GET /cache`, `GET /entries`, `GET /documents` and `GET /jobs` are
  all 404, and a test asserts the OpenAPI document contains exactly five
  paths;
- nothing about the cache's layout crosses: no path, cache root, entry
  directory, manifest name, payload filename, workspace, exit code, process
  detail or traceback. Every response body in the suite is walked
  recursively and asserted free of all of them.

## Limitations

- **only published builds** are retrievable; there is no history, no listing
  and no way to discover a build key other than performing the build;
- **no job state**: a build in flight is not observable, because nothing
  records it;
- **no eviction awareness**: a build stays retrievable as long as its cache
  entry does, and the cache still grows without bound (Stage 18);
- **one cache root per application**, configured at startup;
- **no conditional requests** on this endpoint: no `ETag` and no
  `Last-Modified`. The artifact endpoint has an ETag; a build result is small
  and cheap to re-read.
