# Derived artifact registry

Status: **Stage 17 — a backend-independent artifact model and manifest.
Infrastructure only; no persistent storage exists.**

## Purpose

Give derived artifacts a representation that does not depend on a filesystem
layout, and a lifecycle with a clear moment at which an artifact starts to
exist. The build layer (`docs/build-job-layer.md`) orchestrates; this layer
models.

```
Build
  │
ArtifactManifest  (document_hash, build_key, artifacts[])
  │
Artifact
  ├── logical identity      <build key>:<kind>
  ├── kind / format         geometry | step | iges | stl | render
  ├── source document       the canonical CAD document's SHA-256
  ├── build                 the build key
  ├── size                  real bytes (files) or canonical bytes (render)
  ├── checksum              SHA-256 of those bytes
  └── path                  physical, machine-specific, never identity
```

## Three things kept apart

| | What it is | Reproducible? |
|---|---|---|
| **identity** | `Artifact.logical_id` | **yes**, anywhere, any time |
| **content** | `Artifact.checksum`, `Artifact.size_bytes` | no — STEP/IGES bytes vary |
| **location** | `Artifact.path` | no — machine-specific |

This is the whole point of the stage. Merging identity and content would make
a reproducible artifact reference depend on STEP's header timestamp, which is
exactly what must not happen.

## Artifact identity

```
logical_id = f"{build_key}:{kind.value}"
```

and nothing else. **Not** a filesystem path, a filename, a file extension, an
`execution_id`, a timestamp or a content checksum.

The build key already folds in the canonical CAD document's SHA-256 and the
canonical build options (Stage 16), so **no second hash scheme was invented**
for identity — a test asserts the logical id starts with the build key.

Tested directly: the same build key and kind always give the same id; a
different build key or kind gives a different one; two builds of the same
request into different directories share logical ids while their paths differ;
a logical id never contains `/`, the temp directory name, an extension, or the
execution id.

## Artifact types

| Kind | Storage | Format string | Extensions |
|---|---|---|---|
| `GEOMETRY` | in-memory | `brep-in-memory` | — |
| `STEP` | file | `step` | `.step`, `.stp` |
| `IGES` | file | `iges-brep` | `.igs`, `.iges` |
| `STL` | file | `stl-binary` | `.stl` |
| `RENDER` | in-memory | `render-model` | — |

Exactly the outputs the project already produces; no new format. The artifact
distinguishes three separate things: the **logical type** (`kind`), the
**payload format** (`format`), and **where it lives** (`storage`).

The extension table is read from the exporters' own `STEP_EXTENSIONS`,
`IGES_EXTENSIONS` and `STL_EXTENSIONS`, so it cannot drift from what they
accept — a test asserts the identity.

### Logical format, not extension — the chosen policy

`.step` and `.stp` are two file representations of **one** logical output, and
so are `.igs` and `.iges`. **Identity follows the logical kind.** Two STEP
files of the same build written with different extensions therefore:

- share a `logical_id`;
- differ in `path`, `file_extension` and `checksum`.

A test exports the same solid to `<key>.step` and `<key>.stp`, publishes both,
and asserts exactly that.

The alternative — extension in identity — was rejected because it would make
one build option (`step`) mean two different artifacts depending on a
filename, which no build option can express. The exact extension is recorded
in `file_extension` as **content**.

## Checksum

`CHECKSUM_ALGORITHM` is **SHA-256** throughout.

| Kind | Checksum input |
|---|---|
| `STEP`, `IGES`, `STL` | the file's **actual bytes**, streamed from disk after the write completed |
| `RENDER` | `canonical_render_bytes(model)` |
| `GEOMETRY` | **none** — `checksum` is `None` |

Never the document JSON, never the geometry, never the build key, never the
filename — tests assert a STEP checksum equals none of those.

### The render checksum

`canonical_render_bytes(model)` is the model's own existing
`RenderModel.to_dict()` (Stage 8) serialized with the canonical CAD document's
JSON conventions — minimal separators, `ensure_ascii=False` — plus
`sort_keys=True`:

```python
json.dumps(model.to_dict(), separators=(",", ":"),
           ensure_ascii=False, sort_keys=True).encode("utf-8")
```

**No second render serialization was introduced**; a test asserts the bytes
equal that expression and that they parse back to `to_dict()`. Because the
render model was measured deterministic, the render checksum is stable across
builds — also tested.

`render_model_from_canonical_bytes` (added in Stage 18) is the exact inverse,
defined beside `canonical_render_bytes` so the two directions of the render
model's byte form cannot drift. It reads the render model's own existing
`to_dict()` structure — again no new format — and is strict in the way Stage
15's document reader is strict: a missing or unknown field, a `format_version`
other than the current one, or bounds whose `size` disagrees with its corners
is an error rather than something silently accepted. The local build cache
uses it to restore a render model without re-tessellating.

### Geometry has no fabricated checksum

A B-rep has no canonical byte representation here, so none is invented:
`checksum`, `size_bytes`, `path` and `file_extension` are all `None`, and
`to_dict()["checksum_algorithm"]` is `None` too. The geometry artifact carries
measurements instead — feature id, solid count, volume, bounding box,
face/edge/vertex counts.

## Size

- **file-backed** — the file's real size, taken with `stat()` after a
  successful write; a test asserts it equals `len(path.read_bytes())`.
- **render** — the length of the canonical bytes, because a canonical byte
  representation genuinely exists.
- **geometry** — `None`. Object memory is never passed off as artifact size,
  and a test compares against `sys.getsizeof` to prove it isn't.

## Metadata

`details` is deterministic, JSON-compatible measurement data. Never a CAD
kernel object, never a traceback, never anything from the environment, and
never a duplicated payload.

| Kind | `details` |
|---|---|
| `GEOMETRY` | part name, feature id, is-solid, solid count, volume, bounding box (min/max/size), face/edge/vertex counts |
| `STEP`, `IGES` | — (format, size and checksum are the fields) |
| `STL` | triangle count, structural consistency |
| `RENDER` | format version, part name, feature id, units, coordinate system, winding, normal binding, vertex and triangle counts, bounds, tessellation settings |

Tests walk the whole manifest asserting only `dict`/`list`/`str`/`int`/`float`
appear, that `cadquery`, `OCP`, `TopoDS`, `Workplane` and `object at` do not,
and that `"vertices"`, `"normals"` and `"triangles"` never appear — the render
model is *described*, not duplicated.

## Manifest

```python
ArtifactManifest(document_hash, build_key, artifacts)
```

reachable as `BuildResult.manifest`.

- **lists exactly the artifacts the build published** — empty for a failed
  build;
- **ordering is the canonical `ArtifactKind` order**, which is the same
  convention `BuildOptions` already canonicalizes its output set with. The
  constructor sorts, so the invariant cannot be bypassed by handing it a
  shuffled tuple;
- **consistency is checked**: every artifact must name the manifest's own
  build key and document hash, and at most one artifact per kind;
- **two views**:
  - `to_dict()` — the full report, including paths, sizes and checksums;
  - `canonical()` / `canonical_bytes()` / `canonical_hash()` — identity and
    deterministic properties only.

### Why the canonical manifest excludes path, size and checksum

Those are facts about one produced file. STEP and IGES headers carry a
timestamp and an incrementing translator counter (measured in Stages 5 and 6),
so their bytes — and therefore their size and checksum — are not reproducible.
Excluding them is what lets the canonical manifest be **byte-deterministic**
while the file bytes are not.

Measured, and asserted for all five test geometries: two builds of the same
document with the same options into **different directories** produce
identical `canonical_bytes()` and identical `canonical_hash()`, while the
**STEP checksums differ**. A separate test asserts the STL checksums *do*
match, because STL has no header timestamp.

`canonical_hash()` is a convenience for comparing manifests. It is **not** an
artifact identity and nothing keys on it.

## Physical location

`Artifact.path` is a plain string of a `pathlib.Path`. No storage abstraction
was introduced: this layer is *told* a path and never invents one. A test
asserts `artifact_registry.py` contains no `mkdir`, `makedirs`, `/tmp`,
`gettempdir`, `cwd()` or `home()` — it knows nothing about a filesystem
layout.

Machine-specific directories never reach the canonical manifest; a test
asserts the temp directory's name appears nowhere in `canonical()`.

## Publication rules

An artifact does not exist until it is publishable.

**File-backed** (`publish_file_artifact`), in this order:

1. the kind is file-backed;
2. the extension is one the exporter uses for that kind;
3. the file **exists**;
4. its size is **greater than zero**;
5. for STL, the file's size **accounts for its own declared triangle count**;
6. the checksum is computed from the bytes on disk;
7. only then is the `Artifact` record created.

So no artifact can carry a checksum taken before its write finished. Each
failure raises `ArtifactPublicationError`, and tests drive all five: missing
file, empty file, wrong extension, in-memory kind, and a truncated STL.

**In-memory** (`publish_geometry_artifact`, `publish_render_artifact`): the
object must have been constructed, and must be the real type — a test asserts
a string or dict is refused.

## Failure behaviour

Stage 16's failure atomicity is unchanged, and now also covers publication:

- an artifact that cannot be published is an **export failure**
  (`BuildFailure.EXPORT_FAILED`, `exception_type` `ArtifactPublicationError`);
- the build is `FAILED`, and its manifest is **empty**;
- every file the build wrote is **deleted**.

A test truncates the STL behind the exporter's back: the build fails, the
manifest is empty, and *both* the STEP file it had already written and the
truncated STL are gone — the directory is left completely empty.

## Determinism limitations

Reproducible across builds and machines:

- logical ids and manifest ordering;
- `canonical_bytes()` and `canonical_hash()`;
- geometry measurements, STL triangle count, render metadata;
- the render checksum and the STL checksum.

**Not** reproducible, by measurement rather than assumption:

- STEP file bytes, and therefore its checksum and size;
- IGES file bytes across processes (within one process its timestamp is
  captured once, so repeated exports match — Stage 9's measurement);
- physical paths.

## No persistent storage in this layer

There is **no** object store, database, cloud backend, remote storage, message
broker or storage-service interface here, and none is implied. This layer
models artifacts; it does not keep them. An artifact lives for as long as the
process holds it and for as long as its file happens to exist on disk.

The identity/content split exists so that a cache can use both concepts —
identity to look an artifact up, checksum to know whether the bytes it holds
are the ones it recorded. Stage 18 added exactly that as a separate layer
(`docs/local-build-cache.md`): a local filesystem cache keyed on the build
key, which *consumes* this model and applies its publication rules to cached
bytes. It is a downstream module — this layer does not import it, know about
it, or change because of it.

## Package boundary

```
CAD document → Build request → Build executor → Artifact manifest → Artifacts
```

One direction. The artifact layer imports only `local_cad`, `render_model`,
the three exporters and `serialization` (for the canonical JSON conventions);
it does **not** import the build layer, and the build layer imports it. Tests
assert both directions, that nothing upstream imports the artifact layer, that
it is not re-exported from the package root, and that it imports none of
`sqlite3`, `redis`, `celery`, `boto3`, `botocore`, `google.cloud`, `azure`,
`requests`, `httpx`, `urllib`, `socket`, `http`, `flask`, `fastapi`, `django`,
`sqlalchemy`, `asyncio`, `multiprocessing`, `concurrent.futures`,
`subprocess`, `shutil` or `tempfile`.
