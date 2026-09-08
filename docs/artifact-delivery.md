# Artifact delivery

Status: **Stage 23 — safe resolution of a logical artifact id into
deliverable bytes. Local development only.**

## Purpose

Turn the identifier a build response already hands out into the file it names,
without ever letting a client name a path.

```
POST /build
     │  artifacts[].logical_id                    <build key>:<kind>
GET /artifacts/{artifact_id}
     │
STEP / IGES / STL bytes
```

The mechanism lives in `apps/api/src/cad_api/artifacts.py` — an
**application-level service**, not HTTP. It imports no FastAPI, Starlette,
Pydantic or HTTP library (asserted), so it is exercised directly as well as
through the route, and no HTTP concept reached `cad_core`.

## Resolution flow

```
artifact logical id            from the client, and nothing else
     │   whitelisted; never string-to-path
build key + artifact kind      [0-9a-f]{64} and an ArtifactKind member
     │   cad_core.local_build_cache — the existing Stage 18 validation
validated cache entry          manifest parsed, sizes and checksums recomputed
     │
manifest record                the authority on what exists and where
     │   containment + symlink gate
verified regular file inside the entry
     │   read once, then length and SHA-256 checked against those bytes
deliverable bytes
```

The API for it is small:

```python
resolver = ArtifactResolver(cache)                  # the app's one cache
outcome  = resolver.resolve(artifact_id)            # -> DeliveredArtifact | DeliveryProblem
```

`DeliveredArtifact` carries `content`, `size_bytes`, `checksum`,
`checksum_algorithm`, `filename`, `content_type`, `etag`, plus the identity it
resolved (`artifact_id`, `build_key`, `kind`, `format`). **It carries no
path** — there is nothing left to open. `DeliveryProblem` carries a `reason`
and a message that is safe to show.

Ordinary refusals are results, not exceptions, in the style the application
service already uses.

## Manifest authority

The manifest inside the validated cache entry decides **what exists and
where**. Nothing else is consulted: not the filename, not the extension in
the URL, not a directory listing, and never a path from the caller.

Resolution goes through `LocalBuildCache.lookup`, so Stage 18's rules apply
unchanged and are not restated here: the manifest must parse, carry the
current schema version, name this build key, hold one record per kind with
correct logical ids and formats, keep every payload path relative and inside
the entry, and every payload must exist with a matching size and a
**recomputed** SHA-256. A miss for any reason is simply "not available".

Nothing about identity, formats, extensions, checksum algorithm or cache
validation is reimplemented. `artifact_logical_id`, `KIND_EXTENSIONS`,
`KIND_FORMATS`, `CHECKSUM_ALGORITHM` and `FILE_KINDS` all come from
`cad_core.artifact_registry`; a test asserts each name appears in the module,
and that no logical id is built by string concatenation.

## Logical versus physical identity

| | |
|---|---|
| **logical** | `<build key>:<kind>` — what the client sends, reproducible on any machine, in the build response |
| **physical** | a file inside a cache entry — never in a request, never in a response, never in a header |

Because Stage 18 records payload locations **relative** to the entry, a cache
root can be moved and the same artifact ids keep working. A test builds,
downloads, moves the whole cache root to a new directory, points a new
application at it, and asserts the same id returns byte-identical content with
the same ETag and the same `Content-Disposition`.

## What the client may supply

Only the logical id. Never a path, a relative path, a filename, an extension,
a directory or a cache root.

An id is **whitelisted**, not sanitised:

1. it must be a non-empty string of at most 128 characters;
2. it must contain no NUL, `/`, `\`, `..`, OS separator, CR or LF — checked
   explicitly as well as by the pattern, so the intent is on the record;
3. it must match `\A[0-9a-f]{64}:[a-z_]{1,16}\Z`;
4. the kind must be an existing `ArtifactKind` value;
5. the parsed pair, put back through `artifact_logical_id`, must equal the id
   that arrived.

Anything else is `artifact_id_invalid` (**400**) before the cache is touched.
A test patches `os.path.realpath` and `open` to raise and asserts a dozen
path-shaped ids — `/etc/passwd`, `../../secret`, `..\..\secret`,
`....//....//secret`, `%2e%2e/secret`, `C:\Windows\System32`,
`\\server\share`, ids with embedded slashes and backslashes — are all refused
**without either being called**. `/etc/passwd` is used as a string to prove it
is refused; the file is never opened.

An id containing a URL path separator usually never reaches the route at all —
it matches no route, and the framework answers 404 — but the checks above do
not depend on that.

## Path containment

After the cache has validated the entry, the delivery layer answers a
different question: *is this file safe to hand out?* That is the one thing
this layer adds, and it is why it exists.

1. the payload must not be a **symlink** (`os.path.islink`, which does not
   follow);
2. the entry directory must not be a symlink;
3. no directory between the entry and the payload may be a symlink;
4. the payload's **real path** must be inside the entry's real path, by
   `os.path.commonpath` on both — never a prefix comparison, so
   `/cache/entry-2` is not inside `/cache/entry`;
5. the payload must be a **regular file**, from `lstat` rather than a call
   that follows links;
6. its extension must be one `KIND_EXTENSIONS` lists for that kind, and its
   recorded format must be `KIND_FORMATS[kind]`;
7. `lstat` size must equal the recorded size;
8. the bytes are read, and **their** length and SHA-256 must equal the
   recorded ones.

Any failure is "not available", with no reason given to the client.

## Symlink handling

**Refused, always.** A cache entry has no legitimate use for a symlink, so
both an escaping link and an internal one are rejected.

This matters because of something measured rather than assumed: a payload
replaced by a symlink pointing outside its entry **still satisfies**
`Path.is_file()`, and the cache's own containment check runs on the
*unresolved* path, so the link passes it. Only `os.path.realpath` shows the
escape. A test records exactly that — `is_file()` true, unresolved
`relative_to` succeeding, `commonpath` on the real paths failing — so the
reason for this gate cannot be lost.

Three tests then drive it end to end, each after making the manifest agree
with the linked bytes so that the symlink is the *only* thing standing between
a client and a file outside the entry:

- a payload symlinked to a file outside the cache — refused, and the outside
  bytes do not appear in the response. The test also asserts the cache still
  considers the entry valid, so it is demonstrably this layer that refuses;
- a payload symlinked to a real file inside its own entry — also refused;
- the whole `artifacts/` directory replaced by a symlink — refused.

They are skipped only if the platform cannot create a symlink at all, probed
at runtime.

## Checksum and size validation

The payload is **read once**, and the size and SHA-256 are checked against the
manifest record *after* the read. What is delivered is exactly what was
verified: there is no window between the check and the send, and no second
hash scheme — the checksum is the artifact layer's own.

`Content-Length` is the real length of those bytes, not the manifest's claim
and not a `stat` taken earlier.

The trade-off is deliberate: a payload is buffered whole. For V1 artifacts
that is 30 KB of STEP, 30 KB of IGES and 102 KB of STL (measured on the
Section D plate). Starlette's `FileResponse` would stream and would give byte
ranges for free — verified present in this version — but it re-opens the path
after validation, which reintroduces exactly the window this design removes.
Streaming with range support is therefore **deferred**, and it needs a design
that verifies as it streams rather than before it.

## In-memory artifacts

`geometry` and `render` have no file, and this endpoint invents neither a
B-rep serialization nor a second render format.

Both are refused as `artifact_not_downloadable` (**404**), decided **from the
id alone before any cache access**, so the refusal reveals nothing about what
the cache holds — a test patches `LocalBuildCache.lookup` to raise and
asserts the refusal still happens. The message names the kind and says the
build response describes it; tests assert it mentions no B-rep, shape, solid,
vertex or normal.

## Refusals, and what they may say

| Reason | Status | When |
|---|---|---|
| `artifact_id_invalid` | **400** | the id is not a `<build key>:<kind>` identifier |
| `artifact_not_found` | **404** | unknown build key, invalid or corrupt entry, missing kind, unsafe payload |
| `artifact_not_downloadable` | **404** | `geometry` or `render` |
| `delivery_failed` | **500** | the delivery layer failed unexpectedly |

`artifact_not_found` is deliberately **one reason and one message** — *"the
artifact is not available"* — for an unknown build key, an invalid entry, a
corrupt payload, a missing kind and an unsafe path. Distinguishing them would
report on the cache's contents. A malformed id is separated from an unknown
one because the id format is public in the transport contract, so saying "that
is not an identifier" leaks nothing and helps a client.

A refusal body is `{"error": {"reason": ..., "message": ...}}` and nothing
else. Tests assert it contains no `Traceback`, no `/tmp`, `/etc`, `/usr/` or
`/home/`, no mention of a cache, entry, manifest or checksum, no `errno`, no
exception class, no artifact extension, no test temporary directory, no string
beginning with `/`, and neither a `Content-Disposition` nor an `ETag`.

The `reason` field is deliberately **not** the transport contract's `failure`
taxonomy: that classifies *builds*, and a download is not a build. Naming it
differently keeps the two from being confused.

An unexpected error is logged server-side with `logger.exception` and answered
with `delivery_failed`; a test raises `RuntimeError("secret internals at
/tmp/x")` inside the resolver and asserts the traceback is in the log and
neither the message nor the exception class name is in the 500 body.

## Cache relationship

The resolver is given the **same cache** the application service builds
through — a test asserts `app.state.resolver.cache is app.state.service.backend.cache` —
so an artifact is downloadable exactly when its build is a valid cache entry.

- **cache hit**: building the same request again changes no logical id and no
  checksum, and a second download returns byte-identical content with the same
  ETag. Tested.
- **corruption**: a corrupted, truncated or missing payload makes the artifact
  unavailable. **A download never repairs the cache** — a test compares the
  entry's files before and after two failed downloads and asserts nothing
  changed, and that the garbage is still there. A subsequent `POST /build`
  rebuilds and replaces the bad entry through the existing Stage 18
  machinery, after which delivery resumes; also tested.
- **no build**: a well-formed id for a build that never happened is simply not
  available.
- A service whose backend exposes no cache gets a resolver with none, and
  every id is not available.

## Route responsibility

The route hands the id to the resolver, picks a status from the `reason`, sets
the headers, and returns the bytes it was given. It **never** touches a path,
a directory, a manifest or a checksum: a test asserts the names `Path`,
`open`, `read_bytes`, `realpath`, `commonpath`, `islink`, `lstat`, `stat`,
`sha256`, `iterdir`, `rglob` and `FileResponse` appear nowhere in `app.py`,
that it names no storage-layout constant and no artifact extension, and that
it imports neither the cache nor the artifact registry.

## Limitations

- **whole-payload buffering**, and therefore no streaming and no byte ranges —
  see above;
- **no HEAD**: FastAPI adds none for a `GET` route, and this stage adds no
  extra route, so `HEAD` answers **405**. That is more honest than a 404;
- **no caching headers beyond `ETag`**: no `Cache-Control`, no
  `Last-Modified`, no conditional-request handling, and no CDN semantics. A
  client may compare the ETag with a checksum it already has;
- **no artifact listing endpoint**: a build response is the only place ids
  come from;
- **no `geometry` or `render` bytes**, ever, by design;
- **one cache root per application**, configured at startup.

## Security limitations

**This is not secure for multi-user or production use, and nothing here
pretends otherwise.**

- **no authentication** — every request is anonymous;
- **no authorization** — anyone who can reach the port can download any
  artifact whose build key they know;
- **no tenant or user isolation** — one cache is shared by every caller, and a
  build key is global. A client that guesses or is given a build key can
  fetch that build's artifacts;
- **no rate limiting**, no payload cap, no audit trail.

What this stage *does* establish is that a client cannot make the server read
a file of the client's choosing: the only identifier accepted is a logical id
matching a strict whitelist, every path is produced by the cache's own
validated manifest, and nothing outside a validated cache entry can be
delivered — symlinks included. Guessing a build key gets you that build's
artifacts and nothing else.
