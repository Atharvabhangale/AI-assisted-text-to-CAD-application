# Experimental: the CAD operation plan

Status: **experiment, branch `experiment/cad-operation-graph`. Not merged, not
production, and not a replacement for anything.**

## What this is

A second, deliberately tiny CAD representation, built to answer one question:

> Is an **operation plan** easier for a model to produce correctly than the
> full canonical V1 CAD document?

The stable path is untouched and remains the working MVP:

```
natural language -> Claude -> canonical V1 JSON -> validator -> engine -> RenderModel
```

The experimental path runs beside it:

```
natural language -> Claude -> operation plan -> parser -> plan validation
                    -> V1 document (adapter) -> the SAME validator, engine,
                       cache, exporters and RenderModel
```

The second path does not fork the pipeline. It **rejoins** it: the adapter's
output is an ordinary V1 document, so every existing guarantee downstream
still applies and nothing downstream needed changing.

## Operations implemented

Five, as of Stage 35. That is the entire vocabulary.

| Operation | Kind | Fields |
|---|---|---|
| `box` | constructive | `parameters`: `x`, `y`, `z` (all > 0); optional `position` (minimum corner) |
| `cylinder` | constructive | `parameters`: `diameter`, `height` (both > 0); optional `position` (base centre), optional `axis` |
| `through_hole` | modifier | `target`; `parameters`: `diameter` (> 0), `position` (**required**), optional `axis` |
| `subtract` | modifier, **consuming** | `target`, `tools` (non-empty list); **no `parameters`** |
| `fillet` | modifier | `target`; `parameters`: `radius` (> 0), `edges` (a selector **object**) |

Everything else — spheres, blind holes, counterbores, unions, **chamfers**,
variable or per-edge fillet radii, naming an individual edge, sketches,
extrudes, patterns, assemblies — is `unsupported` by design, and the parser
rejects any operation type it does not implement rather than passing it
downstream.

### How the plan differs from the V1 document

It is not the V1 document renamed. A box carries flat `x`/`y`/`z` instead of a
nested `size` object, there is no `units` field (millimetres are the only unit,
so a unit is one fewer thing to get wrong), no `schema_version`, and no
`features` array — just `operations`. Those differences are the experiment; a
pure rename would measure nothing.

```json
{
  "status": "generated",
  "summary": "a plate with one hole",
  "operations": [
    {"id": "plate", "type": "box", "parameters": {"x": 100, "y": 60, "z": 10}},
    {"id": "hole1", "type": "through_hole", "target": "plate",
     "parameters": {"diameter": 8, "position": {"x": 10, "y": 10, "z": 0}}}
  ]
}
```

## `through_hole` and references (Stage 33)

`through_hole` is the first **modifier**, and the first operation that refers
to another. Its semantics are the V1 contract's (Section C.3), not invented:

- the centreline is the infinite line through `position` along `axis`, so the
  cut always emerges on both sides and **there is no depth parameter**;
- because the cut is unbounded along the axis, the component of `position`
  *along* `axis` has **no effect**. For a `+Z` hole only `x` and `y` locate it,
  and `z: 0` is conventional rather than meaningful;
- `position` is **required**, unlike on a box or cylinder. A hole has no
  default location, so omitting it would be inventing geometry.

`target` sits **beside** `id` and `type`, not inside `parameters` — it is a
reference, not a dimension, and that is where the V1 document already keeps it.
A `target` on a box or cylinder is an unknown field and is rejected.

### The one rule that surprises people

Per Section B.4, **a modifier replaces its target in place and the result
keeps the target's id.** The modifier's own `id` is a label for traceability
and *never names a solid*.

So holes do not chain. Four holes in a plate all carry `"target": "plate"` —
never `hole1`, `hole2`, `hole3`. A plan that drills into a hole is invalid, and
the plan validator says so with `P11`.

### How references are validated

The plan validator simulates the solid set over the plan and reports at most
one problem per bad reference, most specific first:

| Code | Rule |
|---|---|
| `P8` | a modifier names a `target` |
| `P9` | the target names an operation that exists in the plan |
| `P10` | the target appears **strictly earlier** — so forward references and cycles are impossible |
| `P11` | the target is a constructive body, not another modifier |
| `P12` | the target has not been consumed (reserved for `subtract`; unreachable today, and a test asserts that) |

These do **not** replace the V1 validator's `S6`/`S7`. That validator still
runs on the converted document and remains authoritative; catching a bad
reference in the plan layer only means a clearer message, sooner. A test
corrupts a target *after* conversion to prove `S6` still fires.

Geometric rules stay with the engine: a hole that misses the material is `E1`,
and a cut that would split the body is `E3`. Neither is decidable from the
plan, and neither is guessed at here — a test drills at `(500, 500)` and
confirms the plan validates while the **build** fails.

### Conversion to V1

Almost an identity, deliberately: the plan's difference from the V1 document is
a flatter *shape*, not different semantics.

```
{"id": "hole1", "type": "through_hole", "target": "plate",
 "parameters": {"diameter": 8, "position": {...}, "axis": "+Z"}}

        ↓  cad_experimental.adapter

{"id": "hole1", "type": "through_hole", "target": "plate",
 "diameter": 8, "position": {...}, "axis": "+Z"}
```

Nothing is reordered, renamed, reinterpreted or computed. `axis` is omitted
when absent so the contract's own default applies; `position` is always
written, because Section C.3 requires it.

## The modules

All under `apps/api/src/cad_experimental/`. Nothing in `cad_ai`, `cad_api` or
`cad_core` imports any of it, and a test asserts that.

| Module | Purpose |
|---|---|
| `plan.py` | The typed plan: `BoxOperation`, `CylinderOperation`, `ThroughHoleOperation`, `OperationPlan`, `PlanStatus`, and the plan's JSON Schema |
| `parser.py` | Untrusted model text -> typed operations. The security boundary |
| `validation.py` | Plan rules `P1`-`P12`. Separate from the V1 validator, which is untouched |
| `local_plan_provider.py` | The local development provider and its fixtures (Stage 32A) |
| `adapter.py` | Plan -> canonical V1 document. A translation, with no geometry in it |
| `build.py` | Hands the document to the existing `CadApplicationService` |
| `prompt.py` | The experimental prompt, separately versioned |
| `generation.py` | Description -> plan, over the stable provider boundary |
| `config.py` | The pinned model and the ports |
| `app.py` | The experimental FastAPI app |
| `harness.py` | The five-case measurement harness |

## Security posture

The model's reply is attacker-influenced data. It is never code.

* parsed with `json.loads` — never `eval`, `exec`, `compile`, `pickle` or an
  import;
* every field checked against an allow-list. An unknown operation type or
  parameter name is an **error**, never ignored;
* numbers must be JSON numbers and finite. `"100"` is rejected rather than
  coerced, `true` is rejected (a bool is an int in Python), and `NaN`/`Infinity`
  are rejected — a NaN reaching the kernel would be a silent corruption;
* no attribute is ever looked up by a computed name, so no `__class__` walk can
  reach anything. A test asserts no `getattr` in the package takes a
  non-literal name;
* the package imports no `subprocess`, `pickle`, `socket`, `importlib`,
  `requests`, `urllib` or `httpx`, and a test asserts it;
* the parser, plan, validator and adapter open no files.

The frontend builds the plan tree with `textContent`, never `innerHTML`: the
ids and summaries are model-authored strings.

## Running it

The stable application keeps its ports and is started exactly as before. The
experiment uses different ones, so both run at once.

|  | stable | experimental |
|---|---|---|
| backend | 8000 | **8001** |
| frontend | 5173 | **5174** |

```sh
# --- experimental backend (8001) ---
cd apps/api
CAD_EXPERIMENTAL_CACHE_ROOT=/tmp/exp-cache \
  PYTHONPATH=../../packages/cad-core/src:src \
  python3 -m uvicorn --factory cad_experimental.app:app_from_environment \
    --port 8001

# --- experimental frontend (5174) ---
cd apps/web-experimental
npm install          # or link the stable app's node_modules for local work
npx vite --port 5174 --strictPort
npm run typecheck
npm run e2e          # drives the page in a real browser
```

`CAD_EXPERIMENTAL_CACHE_ROOT` is required — like the stable application, this
one does not invent a filesystem path.

### Routes

| Route | Purpose |
|---|---|
| `GET /experimental/health` | up, and whether a model is configured |
| `GET /experimental/plan-schema` | the plan's JSON Schema |
| `POST /experimental/generate-plan` | `{"text": "..."}` -> a plan |
| `POST /experimental/validate-plan` | a plan -> a verdict. Calls no model |
| `POST /experimental/build-plan` | a plan -> the existing build result + RenderModel |

`/validate`, `/build` and the rest of the stable surface are **not** served
here, and a test asserts the experimental app has not grown a copy of them.

## The model

`claude-haiku-4-5-20251001`, pinned in `cad_experimental/config.py`, reached
through the **existing** `cad_ai.anthropic_provider`. Gemini support is
untouched and unused by this path.

The credential is `ANTHROPIC_API_KEY`, and only its *presence* is ever read.
`ExperimentalConfig` has no field to hold a key, so no configuration dump can
leak one. Overrides use their own variables (`CAD_EXPERIMENTAL_MODEL`,
`CAD_EXPERIMENTAL_TIMEOUT_SECONDS`) so a production override cannot reach in.

## Measuring it

```sh
cd apps/api
PYTHONPATH=../../packages/cad-core/src:src python3 -m cad_experimental.harness --self-check
PYTHONPATH=../../packages/cad-core/src:src python3 -m cad_experimental.harness --live
```

`--live` is required for a real run: a present credential is deliberately not
sufficient, because a benchmark spends money. Without `ANTHROPIC_API_KEY` the
live run reports `NOT_RUN` and substitutes no other credential or provider.

The harness records, per case and separately: the raw model output, whether it
parsed, whether the plan validated, whether the outcome matched, whether the
geometry is **semantically** what was asked for, whether it built, the volume
against an expected volume, and the RenderModel triangle count. A provider
failure is recorded as a provider failure and never as a wrong answer.

## `subtract` and history (Stage 34)

`subtract` is the first operation with **history**: it consumes the solids it
uses, so an id's meaning depends on where in the plan you are.

```json
{"id": "cut", "type": "subtract", "target": "plate", "tools": ["tool_a", "tool_b"]}
```

Semantics are Section C.4's, not invented:

- each solid in `tools` is removed from `target`, **in list order** — so the
  order is geometry, and the adapter never reorders it;
- the result **replaces the target in place** and keeps the *target's* id, like
  every modifier. It is not a new body, and the subtract's own id names nothing;
- every tool is **consumed** — deleted from the solid set. A later operation
  may not target it, list it again, or reference it at all;
- subtraction only. There is no union or intersection in V1, so `subtract`
  never joins two solids.

It carries **no `parameters` key at all** — its whole input is two references.
Supplying one, even an empty one, is an unknown field, so a subtract has
exactly one shape rather than two.

### The history model

The plan validator simulates the solid set as it reads the plan:

- `declared` — every operation id, and where it first appeared;
- `live` — the ids that currently **name a solid**: constructive ids that have
  not been consumed;
- `consumed` — ids a `subtract` has used as tools, and where.

A constructive operation adds to `live`. A modifier adds nothing. A `subtract`
removes each of its tools from `live` and records it in `consumed`. Only tools
that actually resolved are consumed — consuming an unresolved one would invent
a second, misleading problem further down the plan.

```
box plate        live = {plate}
box tool         live = {plate, tool}
subtract         target=plate tools=[tool]
                 live = {plate}          consumed = {tool: 2}
subtract         tools=[tool]  ->  P12: already consumed at operations[2]
```

### Rules added

| Code | Rule | V1 counterpart |
|---|---|---|
| `P13` | `tools` is a non-empty list of ids | `S14` |
| `P14` | a tool is neither the target nor a repeat | `S15` |

`P9`–`P12` were not duplicated: a tool reference asks exactly the same four
questions as a target, so one implementation answers both. `P12` — "already
consumed" — existed from Stage 33 but was **unreachable** until now; a test
asserted that, and Stage 34 is what makes it fire.

Two things stay where they belong. **`S9`** (exactly one solid at the end) is
*not* duplicated in the plan layer: a plan with two unconsumed solids is a
perfectly coherent plan and an invalid part, and the existing V1 validator says
so — there is a fixture for exactly that. And a tool that **removes no
material** has no rule code at all: Section C.4 lists only `E2` and `E3`, so
the engine refuses it without claiming one.

## `fillet` and edge selection (Stage 35)

`fillet` is the first operation that names **edges**, and the first whose
feasibility the plan layer cannot judge.

```json
{"id": "round", "type": "fillet", "target": "plate",
 "parameters": {"radius": 2, "edges": {"select": "axis_parallel", "axis": "Z"}}}
```

Semantics are Section C.5's:

- every matched edge is replaced by a **constant-radius** circular blend.
  There is no variable radius and no per-edge radius in V1;
- it is a modifier: the result replaces the target in place and keeps the
  *target's* id, so the fillet's own id never names a solid;
- `radius > 0` (rule S16) is decidable from the plan. Nothing else about the
  geometry is.

### The edge selector

Exactly two selectors exist (Section C.7), and it is an **object**, never a
string — `"all"` is not a selector and is rejected rather than helpfully
interpreted:

```json
{"select": "all"}
{"select": "axis_parallel", "axis": "Z"}
```

`axis_parallel` matches every **straight** edge parallel to that axis, so a
circular hole rim never matches — the specification's own example, and a test
holds the geometry to it.

**The selector axis is unsigned** — `"X"`, `"Y"`, `"Z"`. That is deliberately a
different vocabulary from a cylinder's *signed* `"+Z"`, because parallelism has
no direction, and the contract says so explicitly. Writing `"+Z"` in a selector
is an error, not a synonym. It is the single most likely thing for a generator
to get wrong here, so it has its own rejection message and its own fixture.

`axis` is required for `axis_parallel` and forbidden for `all` (rule S18) —
both directions are enforced.

### Rules added

| Code | Rule |
|---|---|
| `P15` | the selector is one of the two supported modes |
| `P16` | `axis` is present exactly when required |
| `P17` | the selector axis is one of the unsigned letters |

`radius` reuses `P4` (a dimension must be positive), and the target reuses
`P9`–`P12` like any other reference.

### What the plan layer refuses to guess: E4 and E5

These are **geometric**, and the plan validator deliberately does not predict
them. A plan that will fail at the engine is still a valid *plan*, and a test
asserts exactly that.

| Rule | Meaning | Example |
|---|---|---|
| `E4` | the selector matched **no** edge | `axis_parallel X` on a `+Z` cylinder |
| `E5` | the selector matched edges the kernel will **not** blend | `axis_parallel Z` on a bare cylinder |

The difference matters and is tested: the cylinder case is *not* E4 — one edge
matched — it is E5, and the engine says so.

### The seam, honestly

`docs/edge-selection.md` records that a selector can legitimately match an edge
a fillet cannot accept: a cylindrical surface's **parameterisation seam**, which
the kernel represents as a genuine straight line and which V1's contract
therefore names.

Since Stage 14.1 the engine requires **complete edge coverage**: if any matched
edge is not taken into a kernel contour, the whole fillet fails with `E5` and no
geometry is produced. A matched edge is never quietly dropped.

Nothing in the plan layer special-cases this. The adapter hands the selector
over untouched — it does not exclude seams, and it does not trim a selection to
what the kernel is likely to accept. A test asserts no module in the package
so much as calls the selector machinery.

The practical consequences, measured rather than assumed:

| Selection | Result |
|---|---|
| box, `axis_parallel X`/`Y`/`Z` | builds |
| box, `all` | builds (26 faces) |
| drilled plate, `axis_parallel X`/`Y` | builds |
| drilled plate, `axis_parallel Z` | **E5** — includes the cavity seam |
| drilled plate, `all` | **E5** — accepted 14 of 15 |
| bare cylinder, `axis_parallel X`/`Y` | **E4** — matches nothing |
| bare cylinder, `axis_parallel Z` | **E5** — the seam is the only match |
| bare cylinder, `all` | **E5** — accepted 2 of 3 |

One correction to the record, found by measuring rather than reading: the
Stage 13 note in `docs/edge-selection.md` says `{"select": "all"}` on a bare
cylinder *succeeds*. Since Stage 14.1 introduced the complete-coverage rule it
no longer does — the seam is one of the three edges. That production document
was not changed here; this is the experimental layer recording what the engine
actually does today.

## Fixtures

Run them with `python -m cad_experimental.local_plan_provider`. Each records
the geometry it should produce, so a build is checked rather than observed.

| Fixture | Expected |
|---|---|
| `box-100x60x10` | 60000.0 mm³, 6 faces |
| `plate-one-hole` | plate − π·4²·10, 7 faces |
| `plate-four-holes` | plate − 4·π·4²·10, 10 faces |
| `cylinder-d20-h50-z` | π·10²·50, 3 faces |
| `cylinder-bored-d20-h50` | π·(10²−4²)·50, 4 faces — a tube |
| `cylinder-d16-h30-x` | π·8²·30, bounding box 30 × 16 × 16 |
| `cylinder-d80-h100-z` | π·40²·100 |
| `subtract-cube-bore` | 50³ − π·10²·50, 7 faces |
| `subtract-plate-bore` | plate − π·10²·10, 7 faces |
| `subtract-two-tools` | plate − 2·π·8²·10, 8 faces |
| `fillet-box-z` | plate − 4·10·r²(1−π/4), 10 faces |
| `fillet-box-x` | plate − 4·100·r²(1−π/4), 10 faces |
| `fillet-box-all` | no closed form — cross-checked against cad-core, 26 faces |
| `fillet-after-through-hole` | drilled plate − 4·100·r²(1−π/4), 11 faces |
| `fillet-after-subtract` | cut plate − 4·60·r²(1−π/4), 11 faces |

Six more fixtures exist **to be rejected**, because a rule nothing exercises is
a rule nobody has tested. Each names the code it expects, so "rejected somehow"
cannot pass for "rejected correctly":

| Fixture | Refused by | Rule |
|---|---|---|
| `reject-empty-tools` | parser | `S14` — an empty list is a malformed subtract, not a bad reference |
| `reject-target-in-tools` | plan validator | `P14` |
| `reject-future-tool` | plan validator | `P10` |
| `reject-modifier-as-tool` | plan validator | `P11` |
| `reject-consumed-tool` | plan validator | `P12` |
| `reject-two-unconsumed-solids` | **existing V1 validator** | `S9` |
| `reject-fillet-bad-selector` | parser | a selector written as a string |
| `reject-fillet-signed-axis` | parser | `"+Z"` in a selector |
| `reject-fillet-zero-radius` | plan validator | `P4` (rule S16) |
| `reject-fillet-negative-radius` | plan validator | `P4` |
| `reject-fillet-future-target` | plan validator | `P10` |
| `reject-fillet-modifier-target` | plan validator | `P11` |
| `reject-fillet-consumed-target` | plan validator | `P12` |
| `reject-fillet-no-edges` | **engine** | `E4` |
| `reject-fillet-seam` | **engine** | `E5` |

One fixture has no analytic expectation: a box with all twelve edges blended
has no clean closed form, because the corner blends interact. Recording a
*measured* number as the expectation would be circular, so it is marked
`cross_checked` and compared against a document built directly with cad-core
instead.

A hole in a **cylinder** is meaningful under the current semantics — verified,
not assumed: a coaxial bore leaves one connected solid, so `E3` is satisfied
and it builds to exactly π·(10²−4²)·50.

## Findings so far

* **The whole path works.** A plan becomes a real B-rep solid through the
  existing engine: a `100 x 60 x 10` box measures exactly `60000.0 mm³` with
  one solid and six faces, and a `d20 x 50` cylinder measures `pi*10²*50`.
  `axis: "+X"` genuinely reorients the solid (bounding box `30 x 16 x 16`).
* **The adapter needed no change downstream.** No cache, exporter, build key,
  artifact id, RenderModel or validator rule was duplicated or modified.
* **The single-solid rule still binds.** A two-box plan is a *coherent plan*
  and an *invalid part*: the experimental validator deliberately does not
  duplicate S9, and the existing validator rejects the translated document
  with `S9`. That is the intended division of labour — the plan layer judges
  plans, and the CAD contract judges CAD.
* **The prompt is much shorter** than production's, for a much smaller
  vocabulary. Whether that translates into better model accuracy is **not yet
  known** — see below.
* **A modifier and a reference cost the architecture nothing.** Stage 33 added
  `through_hole` without a second CAD engine, a second validator, a second
  build path, a second cache or a second RenderModel, and without touching
  `local_cad.py` or the V1 validator. The four-hole plate builds to
  57989.38070170254 mm³ against a closed form of 57989.38070170253 — one solid,
  ten faces, outside dimensions unchanged.
* **The holes are real openings, not a visual impression.** Checked in the
  mesh's own coordinates: 253 rim vertices per hole, zero vertices inside any
  hole, and zero top-face triangles whose centroid falls within one.
* **History cost the architecture nothing either.** Stage 34 added `subtract`
  with no second engine, validator, build path, cache or RenderModel, and
  without touching `local_cad.py` or the V1 validator. A 50 mm cube bored by a
  d20 tool measures 109292.03673205104 mm³ against a closed form of
  109292.03673205103; two tools on one plate give 55978.761403405064, matching
  exactly.
* **Two routes to one cavity agree.** The same d20 bore expressed as a
  `subtract` and as a `through_hole` produce the same volume to 1e-9 — a
  cross-check that would catch an adapter quietly changing either's meaning.
* **The prompt is now 8846 characters**, against the production prompt's
  14943, for five operations.
* **Edge selection cost the architecture nothing either.** Stage 35 added
  `fillet` with no second engine, validator, build path, cache or RenderModel,
  and without touching `local_cad.py`, `edge_selection.py` or the V1
  validator. A plate with its four vertical corners blended at r2 measures
  59965.663706143576 mm³ against a closed form of 59965.66370614359.
* **The plan route and the cad-core route produce the same solid.** The same
  filleted part expressed as an operation plan and as a hand-written canonical
  document agree on volume, face count, edge count, solid count and bounding
  box — the check that the plan layer adds nothing and loses nothing.
* **The blend is really in the mesh.** Verified by coordinate: vertices lie on
  each corner's arc at the blend radius, none remain inside it, and the mesh
  carries more than six distinct unit normals, all finite — a plain box has
  exactly six.

## What is NOT known

**Real Anthropic testing is still postponed.** Stages 33 to 35 were developed
and tested entirely against the local development provider, which supplies
developer-written plans and calls no model. No result in this document, in the
fixtures, in the tests or on the page is a Claude result of any kind, and no
claim is made about Claude Haiku's quality on any representation.

**No live measurement exists.** `ANTHROPIC_API_KEY` is not available in the
environment this was built in, so the five cases have not been put to real
Claude Haiku 4.5. The harness runs, its plumbing is proven against a stub, and
a stub result says nothing whatever about model quality.

So the stage's central question — is the operation plan easier for a model to
get right than the V1 document? — **remains open**. Answering it needs the live
harness run, and then a comparison against the stable path's own numbers on
equivalent prompts.
