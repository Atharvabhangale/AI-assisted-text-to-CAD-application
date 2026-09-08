# Local build cache

Status: **Stage 18 — a deterministic local filesystem cache for successful
build artifacts. Infrastructure only.**

## Purpose

Let a build that has already been done be *returned* rather than repeated,
without ever returning something that only looks like the real thing.

```
CAD document
    ↓
BuildRequest            (Stage 16)
    ↓
BuildExecutor
    ↓
ArtifactManifest        (Stage 17)
    ↓
LocalBuildCache         (this stage)
```

One direction. The cache consumes a `BuildJob`, a `BuildResult` and an
`ArtifactManifest`. It knows nothing about CAD features, validation rules,
exporters, FeatureScript or the geometry kernel, and it modifies none of them
— a boundary test reads the module's syntax tree and asserts the names
`build_part`, `export_step`, `export_iges`, `export_stl`, `select_edges`,
`validate`, `cadquery`, `OCP`, `Workplane` and `TopoDS` appear nowhere in its
code.

## The cache key

**The key is the existing build key. Nothing else.**

```
<cache_root>/entries/<build_key>/
```

No second hash scheme is introduced. The build key (Stage 16) is already the
SHA-256 of `{"document_hash": <canonical CAD document's SHA-256>, "options":
{"outputs": [...]}}`, so two requests that share a build key are the same
build by construction — the same document, the same output selection.

A lookup takes the **build key alone** as its identity; `document_hash` and
`required_kinds` are *verifications* applied to whatever the stored entry
claims, never part of the key. `cache.lookup(build_key)` with neither of them
finds the entry, and a test asserts it.

Never identity: a timestamp, an `execution_id`, a filesystem path, a filename,
a content checksum, or the cache root itself. Tests assert the key contains no
`/`, no part of the temp directory name and no execution id, and that the same
request built into two different cache roots keeps one build key and one set of
logical artifact ids.

## Directory layout

```
<cache_root>/
    entries/
        <build_key>/
            manifest.json
            artifacts/
                step.step
                iges.igs
                stl.stl
                render.json
    staging/
        <build_key>.<unique>/        scratch; never read back, never identity
```

The root is **always supplied by the caller**, and must already exist. The
cache creates only `entries/` and `staging/` inside it. It never consults a
home directory, a temporary directory, the current working directory or an
environment variable — a boundary test asserts the names `gettempdir`,
`mkdtemp`, `TemporaryDirectory`, `expanduser`, `home`, `cwd`, `getcwd`,
`environ` and `getenv` appear nowhere in its code, and that no string literal
in it is an absolute path.

Payload filenames are `<kind><extension>`: the artifact's own **logical kind**
(its identity) plus the extension the build actually produced (its content).
So a cached file's name never depends on the original build's directory or
filename, and a STEP written as `.stp` is cached as `stp` and read back with
`file_extension == ".stp"`.

The manifest records payload locations **relative** to the entry directory
(`artifacts/step.step`), so an entry survives the whole cache root being
moved, and no absolute path is ever persisted — a test asserts the temp
directory's name appears nowhere in `manifest.json`.

## The persisted manifest

```json
{
  "cache_schema_version": "1.0.0",
  "document_hash": "<64 hex>",
  "build_key": "<64 hex>",
  "artifacts": [
    {"kind": "geometry", "format": "brep-in-memory", "storage": "in_memory",
     "logical_id": "<build key>:geometry", "file_extension": null,
     "size_bytes": null, "checksum": null, "checksum_algorithm": null,
     "payload_path": null, "details": {...}},
    {"kind": "step", "format": "step", "storage": "file",
     "logical_id": "<build key>:step", "file_extension": ".step",
     "size_bytes": 15429, "checksum": "<64 hex>",
     "checksum_algorithm": "sha256", "payload_path": "artifacts/step.step",
     "details": {}}
  ]
}
```

Enough to reconstruct a successful `BuildResult`/`ArtifactManifest` **without
running the CAD engine**: document hash, build key, the artifact list with
logical ids, formats, storage kinds, checksums and sizes for everything that
has bytes, the deterministic `details` measurements, and a relative location
for each payload.

Deliberately **not** stored: an absolute path, an `execution_id`, a
traceback, a kernel or CadQuery object, the FeatureScript source, or a B-rep.
Tests assert the manifest text contains the execution id of neither build,
no `Traceback`, and none of `cadquery`, `OCP`, `TopoDS`, `Workplane` or
`object at`.

`cache_schema_version` versions **the cache entry file format** and nothing
else. It is not a CAD document version, not a render format version and not
part of any build identity; bumping it makes older entries a clean MISS
instead of something to misread.

## Cached and non-cached artifacts

| Artifact | Cached? | How |
|---|---|---|
| `step` | **yes** | the exact bytes of the build that filled the cache |
| `iges` | **yes** | likewise |
| `stl` | **yes** | likewise |
| `render` | **yes** | its existing canonical JSON bytes; the `RenderModel` is reconstructed from them |
| `geometry` | **no** | metadata only; the B-rep is never serialized |

### The render model — choice A, cached

The cached payload is exactly `artifact_registry.canonical_render_bytes(model)`
— the Stage 17 definition, which is the render model's own `to_dict()`
(Stage 8) written with the canonical document's JSON conventions plus
`sort_keys=True`. **No new serialization format was introduced.**

- **bytes**: `canonical_render_bytes(model)`, stored at
  `artifacts/render.json`. A test asserts the file's bytes equal that
  expression applied to the build's own model.
- **checksum**: SHA-256 of those bytes — the same value Stage 17's render
  artifact already carried. A test asserts the recorded checksum equals
  `file_checksum(payload)` and the recorded size equals its length.
- **reconstruction**: `artifact_registry.render_model_from_canonical_bytes`,
  the exact inverse of `canonical_render_bytes` and defined beside it so the
  two directions cannot drift. It is strict in the way Stage 15's document
  reader is strict: a missing or unknown field is an error, so a future field
  added to `RenderModel.to_dict()` fails loudly here instead of being silently
  dropped. It also re-derives `bounds.size` from the corners and refuses a
  payload where they disagree.
- **schema/version compatibility**: the payload's `format_version` must equal
  this build's `RENDER_FORMAT_VERSION` (`1.0.0`). An older or newer payload is
  refused rather than guessed at, and the refusal is a cache MISS.

The restored artifact is then **re-published** from the reconstructed model
(`publish_render_artifact`), so its checksum, size and metadata are recomputed
and only then compared with the recorded ones. A test asserts the restored
model compares equal to the original, that `to_dict()` matches, and that the
canonical bytes match — for all five test geometries.

Note that the render artifact keeps `storage = in_memory` and `path = None`
even though the cache holds a file for it. The file is the *cache's* payload
for an in-memory artifact; changing the artifact's storage kind would make a
cache hit's canonical manifest differ from a fresh build's, which is exactly
what must not happen.

### Geometry — not cached, by design

A B-rep has no canonical byte representation in this project, and one was
**not invented**. So:

- no geometry payload is written — a test asserts nothing named `geometry`
  appears in `artifacts/`;
- the geometry artifact's *measurements* are restored (part name, feature id,
  solid count, volume, bounding box, face/edge/vertex counts) — which is all
  that artifact ever contained: even in a fresh build its `checksum`,
  `size_bytes` and `path` are `None`;
- **`BuildResult.geometry` is `None` on a cache hit**, even when `GEOMETRY`
  was requested, and `CacheEntry.restores_geometry` is always `False`.

Geometry is therefore a **non-cached derived in-memory artifact in this
stage**. A caller that needs the live solid must rebuild; a test asserts the
`None`, asserts the restored measurements are identical to the build's, and
checks the restored volume against 100 × 60 × 10 mm³.

## Cache-hit validation

**The manifest is never trusted.** A hit is reported only when, in order:

1. the entry directory exists;
2. `manifest.json` exists;
3. it parses as a JSON object;
4. its fields are exactly the ones this cache writes, and
   `cache_schema_version` is the current one;
5. its `build_key` equals the requested build key;
6. its `document_hash` equals the request's document hash;
7. the artifact list is internally consistent — one artifact per kind, each
   record's fields exactly the documented set, each `logical_id` equal to
   `artifact_logical_id(build_key, kind)`, each `format` and `storage` the
   registry's own value for that kind, each payload path relative and inside
   the entry directory, and the assembled list accepted by
   `artifact_registry.build_manifest`;
8. every artifact that *has* a payload representation has its payload file
   present, and it passes the artifact layer's own publication gate —
   including, for STL, that the file's size accounts for its declared triangle
   count;
9. each payload's size on disk equals the recorded size;
10. each payload's SHA-256, **recomputed from the bytes on disk**, equals the
    recorded checksum;
11. every requested output is present in the manifest.

Identity and publication rules are **applied, not restated**: the cache calls
`artifact_logical_id`, `publish_file_artifact`, `publish_render_artifact` and
`build_manifest` rather than re-implementing them. A file artifact is
re-published from the cached file, so the checksum a hit reports is one taken
from the cached bytes in that call.

The result is a `CacheLookup`: `hit`, the `entry`, a `CacheMissReason` and a
diagnostic `detail`.

## Corruption handling

Every one of these is a **MISS**, with a reason — never an exception out of
`get_or_build`, never a partial result, and never an in-place repair:

| Damage | Reason |
|---|---|
| no entry directory | `NO_ENTRY` |
| missing `manifest.json` | `NO_MANIFEST` |
| malformed JSON, `[]`, `null`, unknown top-level field | `MANIFEST_UNREADABLE` |
| a different `cache_schema_version` | `SCHEMA_MISMATCH` |
| wrong build key | `BUILD_KEY_MISMATCH` |
| wrong document hash | `DOCUMENT_MISMATCH` |
| a record naming another entry's `logical_id` | `MANIFEST_INCONSISTENT` |
| a payload path with `..`, or an absolute one | `MANIFEST_INCONSISTENT` |
| duplicated kind, unknown kind, wrong format, wrong storage, unknown record field, empty artifact list | `MANIFEST_INCONSISTENT` |
| a `geometry` record claiming a payload, size or checksum | `MANIFEST_INCONSISTENT` |
| wrong recorded checksum algorithm or extension | `MANIFEST_INCONSISTENT` |
| a missing or emptied payload file, a directory where a payload belongs, a payload whose extension its kind does not use | `PAYLOAD_MISSING` |
| a truncated payload, or a wrong recorded size | `SIZE_MISMATCH` |
| payload bytes altered at the recorded length, or a wrong recorded checksum | `CHECKSUM_MISMATCH` |
| a requested output absent from the manifest | `OUTPUT_MISSING` |

**An extra file not named in the manifest is ignored**, and never becomes an
artifact: the manifest is the authority on what an entry contains. A test
drops a plausible `extra.step` into `artifacts/` and asserts the entry is
still a hit, that its kinds are exactly the manifested ones, and that no
artifact points at the stray file. The alternative — invalidating the entry —
was rejected because a stray editor or filesystem file would then discard a
perfectly good entry.

**A corrupt entry never reports success, and never blocks a rebuild.** On a
MISS the ordinary build runs, and its publication *replaces* the bad entry:
the corrupt directory is moved aside under `staging/` and removed only after
the complete entry has been renamed into place. Tests corrupt a STEP payload
and assert the next `get_or_build` succeeds, is not a cache hit, leaves a
valid entry, and that the following request is a hit again with an empty
`staging/`.

## Publication strategy

A new entry is assembled in `staging/<build_key>.<unique>/` and made visible
by **a single directory rename** into `entries/<build_key>`, taken only after:

1. the build succeeded (`publish` refuses anything else — a failed build is
   never cached);
2. every artifact's payload has been copied into staging, with the checksum
   taken from **the bytes actually written into the cache** rather than copied
   from the source record — so a bad copy cannot be published with a
   right-looking checksum;
3. every copied checksum matched the artifact's own;
4. the manifest was written in full.

So a partially written entry is never visible under `entries/`. A test patches
the manifest serializer to raise: the payloads have already been copied, and
still no entry exists, `staging/` is left empty, the next lookup is a
`NO_ENTRY` miss, and a clean publication afterwards works. Another test builds
a complete-looking entry under `staging/` by hand and asserts it is invisible
as a hit.

Publication is idempotent: publishing the same result twice leaves one entry
with byte-identical `manifest.json`.

### What is and is not claimed

Renaming a directory is atomic within one filesystem, and staging lives under
the same caller-supplied root, so a reader never observes a half-built entry.
Measured on this platform (Linux, POSIX): `os.rename` and `os.replace` of a
directory onto an existing **non-empty** directory fail with `ENOTEMPTY`,
while a rename onto an empty or absent one succeeds. A valid entry always
contains `manifest.json`, so a successful rename can never destroy one.

**Not claimed**: distributed atomicity, cross-filesystem atomicity, or crash
durability. Nothing is `fsync`ed, so a power loss mid-publication could leave
an incompletely durable entry; the next lookup's size and checksum checks
would report it as a MISS, which is the safe outcome, and a rebuild replaces
it. Windows fails a rename onto an existing directory rather than raising
`ENOTEMPTY`, which lands on the same "someone else won" path; that has not
been tested here.

## Concurrent writes

**One writer wins publication; the loser re-checks the final entry.** No lock
file, no lock service, no retry loop, no distributed coordination.

If the rename fails because something is already at `entries/<build_key>`:

- if that entry **validates**, it wins — the loser discards its staging
  directory and returns the published entry. There is nothing to gain by
  replacing it: both builds have the same build key and the same logical ids,
  and each holds its own equally valid bytes.
- if it does **not** validate, it is moved aside and replaced by the complete
  staged entry. If that second rename also loses to another writer, that
  writer wins and this one simply re-validates.

The invariant is the one that matters: **no caller can be handed a corrupt or
partial cache hit**, because an entry only ever becomes visible through a
rename of a fully written directory. Tests run two threads through a barrier —
once both publishing directly, once both through `get_or_build` — and assert
no exception, exactly one entry, that it validates, that both callers got the
same valid entry, and that `staging/` is empty afterwards.

## `cache_hit` semantics

**No new job status was introduced.** A cache hit is a successful build whose
artifacts came from the cache:

- `BuildJob.status` is `SUCCEEDED`, and `BuildStatus` still has exactly
  `queued`, `running`, `succeeded`, `failed`;
- `BuildResult.cache_hit` is `True`, and appears in `to_dict()`;
- it is `False` for every build that ran the engine, including one whose
  result was then published to the cache.

A cache hit is reporting metadata, never a status and never part of an
identity. A hit also gets a **fresh `execution_id`**, because it is a new
invocation — and the execution id affects nothing: the build key, the document
hash and every logical id are the same as the build that filled the cache, and
the id appears nowhere in the stored manifest. Tests assert all of that.

```python
job = get_or_build(request, cache, output_directory=directory)
job.status              # BuildStatus.SUCCEEDED
job.result.cache_hit    # True on the second identical request
job.result.manifest     # the restored ArtifactManifest
```

## Cache-hit behaviour

On a valid hit, `get_or_build`:

- runs **no** geometry and **no** exporter. Tests patch `build_part`, all
  three exporters and `build_render_model` to raise, and the hit still
  succeeds with the full artifact set;
- writes nothing to the output directory, and does not need one at all;
- returns paths **inside the caller's cache root** — never the original
  build's paths. A test resolves every returned path against the cache root
  and asserts it does not contain the build directory;
- returns the reconstructed `RenderModel`, and `geometry = None`;
- returns a manifest whose `canonical_bytes()` and `canonical_hash()` are
  identical to the original build's, and whose per-artifact format, storage,
  extension, size, checksum and `details` all match.

On a miss it runs the ordinary build and, if it succeeded, publishes it. A
miss's result is the ordinary one, with the build's own paths.

## Determinism

Verified by test:

- the same request always has the same build key;
- a hit has the same build key and document hash as the miss that filled it;
- a hit has the same logical artifact ids, and the same cached checksums on
  every subsequent hit;
- a STEP, IGES or STL hit returns the **cached bytes exactly** — asserted
  byte-for-byte against the first build's file, for all five geometries;
- the render model reconstructed from cache compares equal to the original;
- nothing about the source document is mutated.

**Not claimed**: that the cache makes STEP or IGES bytes deterministic across
independent builds. Their headers carry a timestamp and an incrementing
translator counter (measured in Stages 5 and 6). The cache records the exact
bytes of the one build it cached and returns those; a test asserts identity is
stable across two independent builds regardless of what their bytes did, and
that the cached checksum is the cached build's.

## Failure atomicity

- **a failed build is never cached** — `publish` refuses a non-`SUCCEEDED`
  result, and `get_or_build` publishes only on success. Tests drive an export
  failure and a geometry failure and assert no `entries/` directory is even
  created;
- **a corruption is never reported as success** — every validation failure is
  a MISS;
- **a MISS caused by corruption allows a fresh build to replace the bad entry
  safely**, as described above;
- an invalid CAD document never reaches the cache at all: there is no
  `BuildRequest` for it, so there is no build key.

## Limitations, and what was deliberately deferred

- **No eviction.** No TTL, no LRU, no size cap, no pruning — the cache grows
  without bound in this stage. A boundary test asserts no name in the module's
  code contains `ttl`, `lru`, `evict`, `max_size`, `maxsize` or `expire`.
- **No geometry caching.** See above; the B-rep is rebuilt or unavailable.
- **No cross-machine sharing.** An entry is only as portable as its bytes: the
  layout is machine-independent and holds no absolute paths, but nothing
  synchronizes, uploads or fetches anything.
- **No crash durability**, no `fsync`, no journal.
- **No locking**, and no coordination beyond the atomic rename.
- **No cache statistics, metrics, logging or instrumentation.**
- **No database, object store, S3/GCS/Azure, Redis, memcached, distributed
  cache, network service, HTTP API, background worker, queue, frontend, LLM or
  MCP** — and none implied. A boundary test asserts the module imports none of
  `sqlite3`, `redis`, `celery`, `kombu`, `pika`, `psycopg2`, `sqlalchemy`,
  `boto3`, `botocore`, `google.cloud`, `azure`, `requests`, `httpx`, `urllib`,
  `socket`, `http`, `flask`, `fastapi`, `django`, `diskcache`, `memcache`,
  `pymemcache`, `asyncio`, `multiprocessing`, `concurrent.futures` or
  `subprocess`, and that no import name mentions an LLM, MCP, Onshape or
  FeatureScript.

## Package boundary

The cache imports exactly four project modules — `artifact_registry`,
`build_job`, `render_model` and `serialization` (for the canonical JSON
conventions) — and a test asserts that list exactly. Nothing upstream imports
it: `model`, `validator`, `errors`, `serialization`, `featurescript`,
`local_cad`, `edge_selection`, the three exporters, `render_model`,
`artifact_registry`, `build_job` and `__init__` are all asserted not to.

It is **not** re-exported from the package root, so `import cad_core` stays
free of the geometry kernel. Import it explicitly:

```python
from cad_core.local_build_cache import LocalBuildCache, get_or_build
```

## The specification is unchanged

`docs/cad-specification.md` is untouched, and so are the validator, the
geometry kernel, the exporters, the render model and the FeatureScript
generator. The neutral CAD specification remains the source of truth; the
canonical CAD document is authoritative; geometry, FeatureScript, STEP, IGES,
STL, the render model **and now their cached copies** are derived artifacts.
