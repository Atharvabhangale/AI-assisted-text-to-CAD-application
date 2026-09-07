# Onshape / MCP boundary

Status: **Stage 3C — interface only. Nothing here contacts Onshape.**

## Why this boundary exists

Stage 3A produces FeatureScript source text. Something eventually has to carry
that text to Onshape, create a Feature Studio, commit it, build the part, and
report back what was made. That "something" does not exist yet, and cannot be
written responsibly right now:

- **There is no Onshape MCP tool in this environment.** A tool search returns
  nothing, and a search of the MCP connector registry for Onshape returns an
  empty list.
- **Stage 3B is blocked at authentication.** Onshape is reachable
  (`cad.onshape.com` → HTTP 200), but every operation is refused:
  `{"message":"Unauthenticated API request", "status":401}`. No account, no
  credentials.

So the wire-level vocabulary of any future Onshape MCP service — its tool names,
request and response shapes, authentication model, and document operations — is
**unknown**. Writing a client against a guessed API would produce code that
looks finished, passes its own invented tests, and is wrong in ways nothing here
could detect.

What this stage does instead is fix the *shape of the conversation* the
application needs, in the application's own vocabulary, so that:

1. the rest of `cad-core` depends on an internal abstraction rather than on any
   particular remote implementation, and
2. when a real service becomes available, the work is confined to one new class
   that implements the protocol — nothing upstream changes.

```
Natural language
      ↓
CAD specification            (docs/cad-specification.md)
      ↓
static validation            (cad_core.validator, rules S1-S20)
      ↓
FeatureScript generation     (cad_core.featurescript)
      ↓
Onshape/MCP adapter          ← this boundary
      ↓
Onshape                      (not implemented)
```

The neutral CAD specification stays upstream of FeatureScript. That ordering is
the architectural point of the whole project and this boundary does not
short-circuit it: see "Raw FeatureScript is not an input" below.

## What the application needs from Onshape

Five operations, which is the whole of what the one-box workflow requires. They
are named in **this project's** vocabulary:

| Operation | What the application needs |
|---|---|
| `open_target` | Create or open the place the work will live |
| `submit_feature_studio_source` | Put generated FeatureScript into a Feature Studio |
| `commit_feature_studio` | Commit it so its feature becomes usable |
| `instantiate_feature` | Use the generated custom feature to build the part |
| `request_model_summary` | Ask for information about the resulting model, for verification |

Each returns an `AdapterResult` carrying a status, a human-readable detail
string, an optional opaque `Handle`, and `reached_onshape`.

### Result states

The boundary distinguishes five outcomes:

| Status | Meaning |
|---|---|
| `SUCCEEDED` | The operation did what the adapter promises. For every adapter that exists today that means "recorded locally", never "Onshape accepted it" |
| `NOT_CONFIGURED` | No adapter is configured. **The application's default state today** |
| `UNAVAILABLE` | An adapter is configured but the remote service cannot be reached |
| `AUTHENTICATION_REQUIRED` | The remote service needs credentials that are not present |
| `REMOTE_FAILED` | The remote service was reached and rejected or failed the operation |

**Validation failures are not in this list, deliberately.** A specification that
breaks rules S1-S20, or a part outside the generator's supported subset, never
reaches an adapter: `deliver_part` generates first, so `UnsupportedPartError` or
`TypeError` is raised before any operation is attempted. An upstream problem can
therefore never masquerade as a remote one. Tests assert that a rejected part
leaves the adapter with zero recorded operations.

### `reached_onshape`

Every `AdapterResult` carries `reached_onshape`, and **every implementation in
this package sets it `False`**. Only a future client that genuinely contacted
Onshape may set it `True`. This exists so that no test, log line, or report can
mistake a local success for evidence that Onshape accepted anything.

## What is currently implemented

- **`cad_core.onshape_adapter`** — the boundary: `OnshapeAdapter` (a
  `typing.Protocol`), `OperationStatus`, `AdapterResult`, the opaque `Handle`,
  `GeneratedFeatureScript`, `featurescript_for`, `DeliveryReport`, and
  `deliver_part`, which runs the five operations in order and stops at the first
  non-success.
- **`cad_core.onshape_fakes`** —
  - `UnconfiguredOnshapeAdapter`: the honest default. Every operation reports
    `NOT_CONFIGURED`.
  - `RecordingOnshapeAdapter`: a deterministic in-memory double for unit tests.
    It accepts generated FeatureScript, records every operation in order,
    returns predictable results, and mints handle tokens from a per-instance
    counter (`fake-target-1`, `fake-feature_studio-2`, `fake-part_studio-3`) so
    a synthetic reference cannot be mistaken for a real Onshape identifier. Its
    `fail_at` argument lets a test exercise any failure status.

Neither implementation touches the network, reads credentials, or models Onshape
geometry. `request_model_summary` on the recording adapter returns an **empty**
`data` mapping and says that no model information is available, rather than
fabricating a bounding box — a test asserts no geometry-shaped values appear in
it. The purpose of the fake is to exercise application flow, not to simulate a
CAD kernel.

### Raw FeatureScript is not an input

An adapter accepts a `GeneratedFeatureScript`, not a bare `str`, and the
supported way to obtain one is `featurescript_for(part)`, which runs the Stage
3A generator on a validated `Part`. `deliver_part` likewise takes a `Part`.
Passing source text or a raw specification dictionary raises `TypeError`.
Hand-written FeatureScript entering the application is therefore not the normal
workflow — the specification remains the input to the pipeline.

## What is deliberately NOT implemented

- **No MCP client, and no MCP tool name, endpoint, URL, header, or payload
  format anywhere in the boundary.** A test scans both modules for such tokens
  and for client-library imports, and fails if any appear. The five operation
  names above are this project's own; they are not a guess at anyone's API.
- **No authentication of any kind.** No API keys are created, stored, read, or
  referenced. No `.env` file, no credential lookup, no environment reads — a
  test asserts, on the parsed syntax tree, that the boundary never touches
  `environ`/`getenv`, and that no string literal is shaped like a key, token, or
  URL.
- **No network calls.** Tests run the full workflow with `socket.socket`,
  `socket.create_connection`, and `socket.getaddrinfo` patched to raise.
- **No Onshape document, workspace, or element model.** `Handle` is opaque and
  carries only `kind` and `token`. It is explicitly *not* modelled on Onshape's
  identifiers, whose required shape has not been established.
- **No geometry, no verification of geometry.** Nothing here can confirm what
  Onshape would build; Stage 3B remains the only route to that, and it is
  blocked.
- **No LLM workflow.** The top of the pipeline is still future work.

## Future integration work

Live authentication and MCP execution are **not done**. When an Onshape MCP
service (or an authenticated API route) becomes available, the work is:

1. Write one new class implementing `OnshapeAdapter`, translating these five
   operations into whatever that service actually requires — verified against
   its real documentation, not guessed.
2. Have it set `reached_onshape=True` only when it genuinely contacted Onshape,
   and map real errors onto `UNAVAILABLE` / `AUTHENTICATION_REQUIRED` /
   `REMOTE_FAILED`.
3. Keep credentials out of the repository: they belong in the environment's
   configuration, never in source, and never in a committed `.env`.

Nothing upstream of the boundary should need to change when that happens. If it
does, this boundary was drawn in the wrong place.

## Known limitations

- The boundary is shaped by the **one-box** workflow only. It has no vocabulary
  for multi-feature parts, updating an existing document, or deleting anything,
  because Stage 3A generates only a single box.
- `AdapterResult.data` is typed as an opaque mapping and is empty everywhere
  today. Its contents are deliberately unspecified: defining a response shape
  would mean guessing one.
- The five operations are a *hypothesis* about what a real service will need,
  informed by the manual procedure in `docs/featurescript-generation.md`. First
  contact with a real service may show the split is wrong; that is the expected
  cost of not guessing an API, and the fix is confined to this boundary.
