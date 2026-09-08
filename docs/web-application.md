# Browser CAD viewer

Status: **Stage 25 — the first visible browser application. A viewer, not an
editor, and not the AI interface.**

## What it is

A single page that posts a canonical CAD document to the existing HTTP API,
shows the build result, and draws the tessellated mesh the backend returns.

```
browser (apps/web)
  │  POST /validate            the document, judged by the backend
  │  POST /build               the document + the outputs to produce
  │  GET  /builds/{key}/render the RenderModel to draw
  │  GET  /artifacts/{id}      STEP / IGES / STL bytes
apps/api            thin HTTP transport (Stage 22-24)
  │
CadApplicationService → cache / isolation → CAD engine
```

**The dependency runs one way.** The browser knows the transport contract and
nothing below it. It never imports CadQuery or OpenCascade — it cannot: the
kernel is Python, and there is no WebAssembly build here.

## What it deliberately is not

No natural-language input, no LLM, no MCP, no Onshape, no authentication, no
database, no cloud storage, no collaboration, no multi-user features, no CAD
editor and no arbitrary geometry editing. The only editable field on the page
is the document textarea; a test asserts that (`tests/boundary.test.ts`).

Stage 25 is the viewer. **Natural language is a later stage.**

## The frontend is a client, not the CAD engine

Every engineering number on the page is a field the backend sent:

| Shown | Where it comes from |
|---|---|
| dimensions | `geometry` artifact → `measurements.bounding_box.size` |
| solids | `measurements.solid_count` |
| volume | `measurements.volume_mm3` |
| faces | `measurements.face_count` |
| triangles | `render` artifact → `measurements.triangle_count` |
| build key, document hash | the build response's own fields |
| mesh | the `RenderModel`'s `vertices`, `normals`, `triangles`, `bounds` |

Nothing is measured, validated, hashed, re-tessellated or recomputed in the
browser. `tests/boundary.test.ts` asserts this at the source level, because a
frontend that recomputed a volume would still show *a* number: the UI modules
contain no `Math.`, no arithmetic on measurements, no rule code, no
`createHash`, no geometry constructor, no mesh loader, and no import other
than Three.js and their own siblings.

## Retrieving the RenderModel: the one endpoint added

Stage 23's artifact endpoint intentionally serves **file-backed** artifacts
only; the render model is an in-memory artifact and was deliberately not
downloadable there. Three options were considered:

| Option | Verdict |
|---|---|
| return the mesh inside the `POST /build` body | rejected — it would make every build response ~180 kB whether or not a viewer wants the mesh, and a cache-hit retrieval could not serve it without re-deriving |
| make the render artifact downloadable through `GET /artifacts/{id}` | rejected — that endpoint's whole safety argument is that it serves *files* whose bytes it read and verified; feeding it an in-memory artifact would blur exactly the distinction Stage 23 established |
| **a dedicated render endpoint, keyed on the build key** | **chosen** |

```
GET /builds/{build_key}/render
```

Narrowly scoped, by construction:

- **one path parameter**, and it is the build key. A test reads the OpenAPI
  schema and asserts the operation has exactly that parameter.
- **no query parameter selects anything.** `?format=`, `?kind=`, `?file=`,
  `?path=` and `?artifact=` are all measured to change nothing.
- **the render artifact is the only thing returned.** There is no argument
  that names a file, a directory, a format or another artifact kind, so this
  is not a generic JSON or file-serving endpoint and cannot be used as one.
- **no second serialization.** The body is exactly Stage 17's
  `canonical_render_bytes(model)` — the same bytes the cache stores for the
  render artifact and the same bytes its SHA-256 was taken from. A test
  asserts byte equality, and a second test round-trips the body back through
  `render_model_from_canonical_bytes` and compares `to_dict()`.
- **the ETag is the render artifact's own checksum**, quoted. Nothing new is
  hashed to produce it.
- **read-only.** With `subprocess.Popen`, `local_cad.build_part`,
  `render_model.build_render_model` and `LocalBuildCache.publish` each patched
  to raise, the render model still arrives, and a file-level snapshot of the
  cache entry is unchanged across repeated requests.

### Refusals

| Situation | Status | `reason` |
|---|---|---|
| not a build key | 400 | `build_key_invalid` |
| no published build with that key | 404 | `build_not_found` |
| the build published no render output | 404 | `render_not_available` |
| retrieval failed | 500 | `retrieval_failed` |

`render_not_available` is a *new* reason, and it discloses nothing new:
`GET /builds/{key}` already lists the outputs a build produced. A key one
character away from a real one returns a body byte-identical to a key nothing
was ever built under, so the endpoint reveals nothing about what the cache
holds. No refusal carries a path, a directory name, a manifest filename, an
exit code or a traceback.

## Same origin instead of CORS

**No CORS was added to the backend.** The page addresses the API at the
*path* `/api`, and Vite's dev and preview servers proxy that prefix to the
backend, stripping it before forwarding:

```ts
// vite.config.ts
const apiOrigin = process.env.CAD_API_ORIGIN ?? "http://127.0.0.1:8000";
proxy: { "/api": { target: apiOrigin, rewrite: (p) => p.replace(/^\/api/, "") } }
```

From the browser's point of view the page and its API calls are one origin, so
no preflight and no `Access-Control-Allow-Origin` are involved. The
end-to-end test asserts both halves: `GET /api/health` through the dev server
succeeds, **and** the response carries no CORS header.

No production domain, machine-specific address or credential is hard-coded.
`CAD_API_ORIGIN` names the backend for development and defaults to localhost;
`VITE_API_BASE` can override the path at build time. A test asserts no
source file contains a URL scheme, an IPv4 literal, `localhost`, or any
credential-shaped token.

## Rendering

Three.js `0.185.1` — the smallest thing that draws an indexed mesh with
orbit controls.

Two conventions the backend documents are **honoured, not converted**:

- **Z up.** The render model is right-handed Z-up; Three.js defaults to Y-up.
  The *camera's* `up` vector is set to Z, so the vertices drawn are the
  vertices sent. The geometry is never rotated.
- **Counter-clockwise outward** is already Three.js's front-face convention,
  so the material draws `FrontSide` and no index is reversed.

The `RenderModel` is converted to buffers and nothing else happens to it:

```
model.vertices  → Float32Array position, 3 per vertex, same order
model.normals   → Float32Array normal,   3 per vertex, never recomputed
model.triangles → Uint32Array index,      3 per triangle, verbatim
model.bounds    → the camera fit
```

**Per-face vertices are preserved.** Stage 8 emits duplicate positions on
purpose so a box keeps sharp edges; merging them here would smooth them.
Tests assert the vertex count out equals the vertex count in, that the mesh
has strictly fewer distinct positions than vertices, and that at least one
position carries more than one normal — which is exactly what a merged mesh
would not have.

`computeVertexNormals` is never called. A test asserts the string does not
appear in any source file.

Fit-to-view is computed from the model's **own bounds** — centre from the
corners, distance from the bounding sphere's radius over the half-angle's
sine, with a margin. No coordinate and no camera distance is hard-coded, and
a test shows the distance scales by 1000× when the model does.

## Error display

The page maps the transport contract's own `failure` values to states, so a
document problem stays distinguishable from a geometry problem, an export
problem and an infrastructure problem:

| `failure` | state |
|---|---|
| `malformed_document`, `invalid_document`, `invalid_request` | `validation-error` |
| `geometry_failed` | `build-error` |
| `output_failed` | `output-error` |
| `execution_failed`, `internal_error` | `execution-error` |

plus `network-error` for a request that never reached the API. The state is
also written to `#status[data-state]`, which is what the tests assert against.

Rule codes and the validator's own messages are shown — they are the useful
part. **Nothing internal is:** no raw stack trace, no filesystem path, no
cache internals, and not even the underlying transport exception (a
`connect ECONNREFUSED 127.0.0.1:8000` becomes "the API could not be
reached"). Tests sweep the whole visible page for a list of forbidden tokens
after a success, a validation failure, a geometry failure and a network
failure.

## Files

```
apps/web/
  index.html          the page: textarea, viewport, status, result, exports
  vite.config.ts      the dev/preview proxy and the test environment
  src/api.ts          the four calls and one URL helper. No CAD logic.
  src/app.ts          state, DOM and the build flow. No CAD logic.
  src/render-model.ts the RenderModel type, its checks, and the pure
                      conversion to WebGL buffers. Imports nothing.
  src/viewer.ts       the Three.js viewport
  src/example.ts      Section D, as a data literal
  src/main.ts         entry point: collect, wire, start
  tests/              87 Vitest tests, against the real page and real fixtures
  e2e/run.mjs         the end-to-end run: real backend, real browser
```

## Testing

### Unit and DOM tests — `npm test` in `apps/web`

87 tests, in three files:

- `tests/app.test.ts` — the build flow: loading, the request shape, success,
  a cache hit, each failure classification, the identity and measurement
  rows, the export buttons, and the forbidden-token sweeps.
- `tests/render-model.test.ts` — the conversion, the normals, the per-face
  vertices and the camera fit, on the real Section D mesh.
- `tests/boundary.test.ts` — the source-level assertions above.

Two things make these tests worth something:

1. **The DOM under test is `index.html` itself**, read from disk. A renamed
   or removed element fails the suite instead of passing against a
   hand-written copy of the markup. `collectElements` is the same function the
   entry point uses.
2. **Every fixture came from the real backend over the real HTTP API** — see
   `tests/fixtures/README.md`. No response body in the suite was written by
   hand, so the frontend cannot drift away from the transport contract
   without a test noticing.

No screenshot is compared, pixel-for-pixel or otherwise.

### End-to-end — `npm run e2e` in `apps/web`

Starts the real backend under `uvicorn` against a throwaway cache root, starts
the real Vite dev server pointed at it, and drives Chromium through the page.
Requires `pip install 'apps/api[serve]'` (or just `uvicorn`) and the `playwright`
dev dependency, whose version is pinned to the Chromium build present in the
environment.

Measured, on the Section D four-hole plate:

```
  ok   the dev server proxies /api to the backend
  ok   the backend sends no CORS header (none was added)
  ok   the example is Section D
  ok   the build succeeded
  ok   the document hash is Section D's
  ok   the result is one solid
  ok   the plate measures 100 mm in x / 60 mm in y / 10 mm in z
  ok   the volume is the plate less four through-holes
  ok   the page shows the backend's volume verbatim
  ok   the browser and a direct retrieval agree on the volume
  ok   the render model loaded (1.0.0, mm, right_handed_z_up)
  ok   the drawn mesh contains four cylindrical walls
  ok   a hole is centred at (10, 10) / (90, 10) / (10, 50) / (90, 50) mm
  ok   per-face vertices are preserved
  ok   the canvas has a WebGL context
  ok   the STEP / IGES / STL button carries the artifact id
  ok   the downloaded file is the size the backend published
  ok   the second build was a cache hit, and renders identically
  ok   an invalid document is a validation error, showing S10
  ok   the page shows no traceback and no path
  ok   the browser reported no errors
```

**The four holes are verified structurally, not visually.** A screenshot
comparison would assert a rendering, not a geometry. Instead the test reads
the mesh the browser actually drew, keeps the vertices whose normals are
horizontal (a cylindrical wall, not a flat face), projects each one along its
inward normal by the hole radius to recover the centreline it circles, and
clusters the results. Four clusters come out, at (10, 10), (90, 10), (10, 50)
and (90, 50) mm — the corner offsets Section D specifies — within a 0.2 mm
tolerance, which is the tessellator's chord error plus a margin. Tessellated
geometry is never compared for exact equality.

## Limitations, honestly

- **Development configuration only.** The Vite proxy is a dev/preview
  convenience. A deployment would put the API and the page behind one origin
  some other way, or add CORS deliberately — this stage did neither.
- **The backend remains single-user.** Stage 23's warning stands: the artifact
  endpoint is not hardened for multi-user production use, and the render
  endpoint inherits the same posture. Adding a browser did not add
  authentication, authorization, rate limiting or per-user isolation, and
  nothing here should be read as claiming otherwise.
- **The whole render model is sent as one JSON body.** 180 kB for the Section
  D plate. There is no streaming, no compression beyond whatever the server
  applies, no level of detail and no progressive load. That is adequate for a
  plate and would not be for a large assembly.
- **The mesh is drawn as one uncoloured solid.** No per-face colouring, no
  edge display, no section view, no measurement tools, no picking.
- **No WebGL is survivable, not equivalent.** The page still validates,
  builds, shows every measurement and offers every export without a WebGL
  context; it just shows no picture.
- **The viewer refuses rather than guesses.** A render model whose
  `format_version`, coordinate system, winding or normal binding differs from
  the documented one is reported as an output error and not drawn, because
  drawing it would be undefined behaviour rather than a different picture.
