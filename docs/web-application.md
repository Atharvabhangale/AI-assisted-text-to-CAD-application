# Browser text-to-CAD application

Status: **Stage 25 — the first visible browser application, a CAD-document
viewer. Stage 30 made natural language the primary input and demoted the raw
CAD JSON to an advanced section. A later change made the interpreter's
assumptions and its attempts visible, taught the design-intent panel the
`union` feature, and added example descriptions. The Settings panel chooses
the AI provider and model from the page. Still not an editor.**

## What it is

A single page where you describe a part in a sentence, press one button, and
see real CAD geometry. The CAD-JSON workflow it began as is still there,
behind **Advanced: CAD JSON**.

```
browser (apps/web)
  │  POST /generate            a sentence -> a validated CAD document
  │  POST /build               that document + the outputs to produce
  │  GET  /builds/{key}/render the RenderModel to draw
  │  GET  /artifacts/{id}      STEP / IGES / STL bytes
  │  GET|PUT|DELETE /settings  which AI provider and model generation uses
apps/api            thin HTTP transport (Stage 22-24, 30)
  │
cad_ai (interpretation)   CadApplicationService → cache / isolation → CAD engine
```

### The one-action flow

Pressing **Generate CAD** does the whole thing; there is no second Build click
on the natural-language path.

```
type a description           (or click an example, which only fills the box)
  -> "Generating CAD..."      POST /generate, exactly once
  -> design intent shown      read from the document the backend returned
  -> assumptions listed       the backend's own strings, verbatim
  -> attempts reported        only if an answer was rejected along the way
  -> "Building geometry..."   POST /build, with that document unchanged
  -> "Ready."                 the mesh, the measurements, the export buttons
```

Three of the five outcomes stop the flow before any build, each with its own
state and its own wording: a **clarification** (with the model's question and
a box to answer it), an **unsupported** refusal, and an unusable **model
answer**. None of them shows a traceback, a path or a provider message.

**The page interprets nothing.** It sends the description verbatim, shows the
document the backend validated, and posts that same document back to be built
without editing it — a test asserts the object sent to `/build` is deeply
equal to the one `/generate` returned. It performs no repair, no retry and no
completion of missing fields.

**One click is exactly one `POST /generate`.** The backend may check its own
answer and ask the model again inside that one request; the page never does.
There is no timer, no polling and no automatic resubmission anywhere in it,
because every `/generate` is a paid model call. `tests/boundary.test.ts`
asserts the source has a single call site for it and no timer, and
`tests/interpretation.test.ts` counts the requests for every kind of answer.

**The dependency runs one way.** The browser knows the transport contract and
nothing below it. It never imports CadQuery or OpenCascade — it cannot: the
kernel is Python, and there is no WebAssembly build here.

## What it deliberately is not

No LLM client, no provider SDK, no prompt, no MCP, no Onshape, no
authentication, no database, no cloud storage, no collaboration, no multi-user
features, no CAD editor and no arbitrary geometry editing. Natural language is
*input*, not intelligence: the page posts a sentence to the backend and knows
nothing about which model answers it.

Outside the Settings panel, the three editable fields are all free text a
person types — the description, the clarification answer, and the advanced
CAD JSON. There is still no field that edits geometry directly: no number
spinner, no dimension input, no feature dropdown. Tests assert all of this
(`tests/boundary.test.ts`), including that the sources name no vendor and
carry no credential.

**The Settings panel is the one deliberate exception, pinned exactly**: inside
`#settings-panel`, one `<select id="settings-provider">` and one
`<input id="settings-model" type="text">`, both labelled, and nothing else.
They choose *which interpreter* the backend uses, not geometry. The boundary
test asserts there are exactly those two fields, that the panel has no
password, URL, number or file input and no field named for a key, token or
address, that the request sends exactly `{ provider, model }`, that the panel
cannot call `/generate`, and that no provider name is written in the page or
its sources (names and labels come from the backend).

The example chips under the description box are buttons, not fields. Each one
copies its own text into the description box and does nothing else — it does
not generate. The same test asserts that every button on the page is either
one of the thirteen named controls (nine, plus the four Settings buttons) or
an example chip, that each is a plain
`type="button"`, and that the page contains no form to submit.

**The browser never sees a credential.** `GEMINI_API_KEY` is read by the
backend's provider and by nothing else; it is not in the page, not in the
bundle, not in `vite.config.ts`, and not in browser storage.

## Settings: choosing the AI provider and model

The **⚙ Settings** button at the top right opens a panel (a disclosure, not a
modal: `aria-expanded` / `aria-controls` on the button, Escape or **Close**
closes it and returns focus to the button). It reads `GET /settings` once
when the page loads — no model call — and shows:

- **AI provider** — a dropdown of the backend's providers, each shown by the
  backend's label with "ready" or "not ready" (Anthropic API, Google Gemini
  API, Claude Code subscription, Google account via Antigravity, open-weight
  model via an OpenAI-compatible server). Under it, for the selected one:
  "Ready." or "Not ready: <the backend's reason>", and **which variables to
  set in `apps\api\.env`, by name only** (e.g. `CAD_CLAUDE_CODE_PATH`).
- **Model** — a text field with the provider's suggestions as a `datalist`
  (Haiku and Sonnet for the two Anthropic routes, the current default for the
  Google routes, none for an open-weight server, whose model must be typed).
  Empty means the provider's default, shown as the placeholder.
- **Save** (`PUT /settings`), **Reset to .env defaults** (`DELETE /settings`)
  and **Close**, then a status line with the result, and the backend's notes:
  keys, executable paths and server addresses stay in `apps\api\.env` and
  are never sent to or accepted from the page; Opus is refused; a change
  applies to the next generation; the answer mode stays in `.env`.

Under the description box one line always says what is in force, e.g.
`Using: Claude Code subscription · claude-haiku-5-5 (chosen in Settings)`, or
`(from apps\api\.env)`, followed by "— not ready" when the backend says the
provider's setup is missing.

A refusal (an Opus model, a URL typed as a model, an unknown provider) is
shown verbatim as "Not saved: …" with `role="alert"`, the model field marked
`aria-invalid`, and nothing changes. Choosing a provider that is not ready is
allowed: the page says so, and the next **Generate CAD** reports the
unavailable interpreter as it always has. Everything the panel shows — labels,
readiness, reasons, suggestions, defaults — is a field the backend sent; the
panel decides nothing and computes nothing (`src/settings.ts`).

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
  index.html          the page: description, intent, viewport, result,
                      exports, and the advanced CAD JSON section
  vite.config.ts      the dev/preview proxy and the test environment
  src/api.ts          the API calls (generate, validate, build, render,
                      and the three settings calls) and one URL helper.
                      No CAD logic.
  src/settings.ts     the Settings panel: shows GET /settings, sends
                      PUT / DELETE /settings. Names no provider.
  src/app.ts          state, DOM, the generate flow and the build flow.
                      No CAD logic.
  src/design-intent.ts  formats a generated document into English. Reads
                      and formats only: no arithmetic, no defaulting, no
                      unit conversion, no imports.
  src/render-model.ts the RenderModel type, its checks, and the pure
                      conversion to WebGL buffers. Imports nothing.
  src/viewer.ts       the Three.js viewport
  src/example.ts      Section D, as a data literal
  src/main.ts         entry point: collect, wire, start
  tests/              191 Vitest tests, against the real page and real fixtures
  e2e/run.mjs         the end-to-end run: real backend, real browser
```

## Design intent

After a successful generation the page shows what the model decided, read out
of the returned document rather than out of the user's sentence:

```
Design intent
  Created a 100 mm x 60 mm x 10 mm rectangular plate using a box feature.
  Box — plate
    Length (X)  100 mm
    Width (Y)   60 mm
    Height (Z)  10 mm
    Position    0, 0, 0 mm
```

Features are listed **in document order**, because in the V1 specification the
order is semantic. A field the document does not carry is omitted rather than
guessed — an absent `position` means the specification's default applied, and
saying so is the backend's job.

A `union` is read the same way as a `subtract` — two rows naming the solids by
their ids, and nothing computed:

```
  Subtract — pocketed          Union — bracket
    From    block                Join    upright
    Remove  pocket               Onto    base
```

A feature type the panel does not know is still listed, under its own type
name, so a generated feature is never silently dropped.

Schema 1.2.0's parameters get a row only when they change something: a
cylinder with `sides` reads as "a hexagonal prism" (by name for 3-12 sides), a
`top_diameter` different from `diameter` as "tapering from 40 mm to 10 mm" (or
"to a point", and the shape "a cone"), and a subtract or union pattern as
"6 copies, 60° apart about Z", "4 copies, 20 mm apart along X" or, for a
single copy, "turned 30° about Z". Neutral values (`sides` 0, `top_diameter`
equal to `diameter`, `pattern_count` 1 with no angle or step) read exactly as
a 1.1 feature. Every number shown is the document's own; the panel still does
no arithmetic.

## Assumptions and attempts

`POST /generate` reports two things about *how* it reached its answer, and the
page shows both rather than letting either be silent.

```ts
assumptions: string[]          // choices the description did not state
attempts: {                    // one per model call made for this request
  number: number               // 1-based
  accepted: boolean
  stage: "answer" | "validation" | "geometry" | null
  rule_codes: string[]
  issues: string[]
}[]
```

Both are optional on the wire. An older backend sends neither, and a missing,
`null` or malformed field is read as an empty list — the captured fixtures
carry neither field, and the suite asserts they still build.

**Assumptions** appear in the design-intent panel under the heading
**Assumptions made**, above the feature list, one list item per string. The
strings are the backend's and are shown verbatim, as text: the page does not
parse a dimension out of them or act on them. An empty list shows nothing.

**Attempts** appear in the status panel, and only when there is something to
tell — a single accepted attempt says nothing.

| Situation | What the page says |
|---|---|
| accepted after one rejection | `Accepted on attempt 2 of 2 — attempt 1 was rejected by the geometry check (E5).` |
| accepted after several | `Accepted on attempt 3 of 3 — 2 earlier answers were rejected.` |
| a question or refusal after a rejection | `Answered after 2 attempts — attempt 1 was rejected by the document validator (S9).` |
| nothing accepted | `None of the 3 attempts was accepted.` (or `The one attempt made was not accepted.`) |

Under the note, **What each attempt returned** lists every attempt in order —
`Attempt 1 — rejected by the geometry check (E5)` followed by that attempt's
reasons, `Attempt 2 — accepted`. It is collapsed under a part that was built
and **open under a failure**, where the reasons are the result. The stage
wording is a mapping of the backend's own `stage` values (`answer` →
"rejected as an unusable answer", `validation` → "rejected by the document
validator", `geometry` → "rejected by the geometry check"); a stage the page
has never heard of is shown as plain "rejected" rather than guessed at. The
rule codes are the backend's, joined and shown as they came.

One display decision is the page's own: under a failure, a top-level `issues`
entry that is word-for-word a reason already listed under an attempt is not
printed a second time. Anywhere the attempt list is collapsed, every top-level
reason stays in plain view.

## Example descriptions

Under the description box is a row of example chips — an L-bracket, a mug with
a handle, a gaming mouse, a desk phone stand, a plate with four holes. A click
puts the chip's text in the description box, replacing what was there, and
focuses it. **It sends nothing.** Generation stays behind **Generate CAD**,
and the help line under the chips says so.

The examples are descriptions, not promises: whether a given one is generated,
questioned or refused is the backend's answer on the day.

## Testing

### Unit and DOM tests — `npm test` in `apps/web`

191 tests, in seven files:

- `tests/app.test.ts` — the build flow: loading, the request shape, success,
  a cache hit, each failure classification, the identity and measurement
  rows, the export buttons, and the forbidden-token sweeps.
- `tests/generate.test.ts` — the text-to-CAD flow, replayed from captured
  `/generate` answers: one action, the document built unchanged, the design
  intent, and the three answers that stop before a build.
- `tests/interpretation.test.ts` — assumptions, attempts, the `union` intent
  text and the example chips, including that a click on a chip makes no
  request and that every kind of answer costs exactly one `/generate`.
- `tests/render-model.test.ts` — the conversion, the normals, the per-face
  vertices and the camera fit, on the real Section D mesh.
- `tests/design-intent.test.ts` — the 1.2.0 wordings, and that a 1.1
  document and a 1.2.0 document with neutral values read identically.
- `tests/settings.test.ts` — the Settings panel: opening and closing,
  providers with their readiness, the not-ready reason and `.env` variable
  names, a save sending exactly `{provider, model}`, a refusal shown without
  change, reset, and an unreachable API. Its `/settings` answers are
  hand-authored to the documented shape, not captured.
- `tests/boundary.test.ts` — the source-level assertions above.

Two things make these tests worth something:

1. **The DOM under test is `index.html` itself**, read from disk. A renamed
   or removed element fails the suite instead of passing against a
   hand-written copy of the markup. `collectElements` is the same function the
   entry point uses.
2. **Every fixture came from the real backend over the real HTTP API** — see
   `tests/fixtures/README.md`. No fixture was written by hand, so the
   frontend cannot drift away from the transport contract without a test
   noticing.

**One exception, stated plainly.** No fixture carries `assumptions` or
`attempts`: every capture predates them. `tests/interpretation.test.ts`
therefore layers hand-authored values for those two fields over the captured
answers, written to the contract above while the backend producing them was
still being built. Those tests show the page handles the shape. They are not
evidence of what the backend sends, and they should be replaced with captured
answers once it can be recorded. The end-to-end run does not cover the two
fields either.

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

- **The assumptions and attempts display has only been tested against
  hand-authored responses**, not against a backend that sends them (see
  *Testing*). If the backend names a field differently from the contract
  above, the page shows nothing for it rather than failing, and an unknown
  `stage` value is worded as plain "rejected" — safe, but it also means a
  mismatch would be quiet.
- **Assumptions are shown, not checked.** The page cannot tell whether the
  list is complete or whether a listed value is the one in the document; both
  come from the backend and are displayed side by side.
- **A failed generation leaves the previous part in the viewport.** The
  result rows, the exports and the design intent are cleared; the viewer has
  no way to be emptied, so the last mesh stays drawn until the next build.

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
