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

Nine, as of Stage 38 — and **six of them can be built.** That gap is the
single most important fact about the current state of this experiment, and it
is deliberate. See "The language is larger than the engine" below.

| Operation | Kind | Buildable | Fields |
|---|---|---|---|
| `box` | constructive | yes | `parameters`: `x`, `y`, `z` (all > 0); optional `position` (minimum corner) |
| `cylinder` | constructive | yes | `parameters`: `diameter`, `height` (both > 0); optional `position` (base centre), optional `axis` |
| `through_hole` | modifier | yes | `target`; `parameters`: `diameter` (> 0), `position` (**required**), optional `axis` |
| `subtract` | modifier, **consuming** | yes | `target`, `tools` (non-empty list); **no `parameters`** |
| `fillet` | modifier | yes | `target`; `parameters`: `radius` (> 0), `edges` (a selector **object**) |
| `chamfer` | modifier | yes | `target`; `parameters`: `distance` (> 0), `edges` (the same selector) |
| `sketch` | **profile** | **no** | `parameters`: `plane` (`XY`/`XZ`/`YZ`), `geometry` (non-empty), optional `constraints` |
| `extrude` | **profile → solid** | **no** | `target` (a **sketch**); `parameters`: `distance` (> 0), optional `direction` |
| `revolve` | **profile → solid** | **no** | `target` (a **sketch**); `parameters`: `angle` in (0, 360], `axis` (**required**) |

Everything else — spheres, blind holes, counterbores, unions, variable or
per-edge fillet radii, naming an individual edge, sweeps, lofts, patterns,
mirrors, assemblies — is `unsupported` by design, and the parser rejects any
operation type it does not implement rather than passing it downstream.

### The four categories

The vocabulary now divides into four kinds, and every operation is in exactly
one (a test asserts it):

* **constructive** — `box`, `cylinder`. Makes a solid from nothing, named by
  its own id.
* **modifier** — `through_hole`, `subtract`, `fillet`, `chamfer`. Acts on a
  solid named by `target`, replaces it **in place**, and the result keeps the
  **target's** id. A modifier's own id never names a solid.
* **profile** — `sketch`. Declares a profile, which is **not** a solid:
  nothing can fillet it, drill it, subtract it or consume it, and it does not
  count toward the single-solid rule.
* **profile → solid** — `extrude`, `revolve`. The only references in this
  language that point at a *sketch*. Their own ids **do** name solids, so a
  later modifier can act on an extrusion. They do **not** consume the profile:
  extruding one sketch twice is legitimate.

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
| `plan.py` | The typed plan: one record per operation, `OperationPlan`, `PlanStatus`, the four category tuples, and the plan's JSON Schema |
| `sketch.py` | The sketch vocabulary and its typed records. **No solver.** Imports only the standard library |
| `parser.py` | Untrusted model text -> typed operations. The security boundary |
| `validation.py` | Plan rules `P1`-`P26`. Separate from the V1 validator, which is untouched |
| `local_plan_provider.py` | The local development provider and its fixtures (Stage 32A) |
| `adapter.py` | Plan -> canonical V1 document. A translation, with no geometry in it. Raises `ExecutionUnsupported` for an operation the engine cannot build |
| `build.py` | Hands the document to the existing `CadApplicationService`; reports `execution_unsupported` separately from an error |
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
* the parser, plan, validator, adapter, build layer and sketch module open
  no files, and a test asserts it. The only two `open()` calls in the package
  are inside a CLI `main()`, on a path a developer typed as an `argparse`
  argument — never on model output;
* `sketch.py` imports **only** `__future__`, `dataclasses` and `typing`: no
  kernel, no `cad_core`, nothing that could execute anything;
* there is **no constraint solver**. A conflicting constraint is reported, so
  no iteration on model-supplied numbers happens anywhere.

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
| `POST /experimental/build-plan` | a plan -> the existing build result + RenderModel; **501** with `execution_unsupported` for a valid plan this backend cannot execute |
| `POST /experimental/local-plan` | run a development fixture or a supplied plan. Every response is stamped `is_live_model_result: false` |
| `GET /experimental/local-plan/fixtures` | the fixture list |

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

## `chamfer` (Stage 36)

Structurally identical to `fillet` with `distance` in place of `radius`. V1
has only the symmetric, equal-setback chamfer (Section C.6): no angle, no
asymmetric form. The parser, validator and adapter share one branch with
`fillet`, because the only difference is the name of the one length —
`EDGE_MODIFIER_LENGTH` records which, in one place, so the three layers
cannot disagree.

Measured (exact decimals, unlike a fillet's quarter-circle — a chamfer removes
a right triangle of area `d²/2` per unit of edge length):

| Case | Volume |
|---|---|
| plate, `axis_parallel Z`, d2 | `59920.0` |
| plate, `axis_parallel X`, d2 | `59200.0` |
| drilled plate, `axis_parallel X`, d2 | `56058.407346…` |

Selector behaviour on a cylinder is **identical to a fillet's**, measured
rather than assumed: `all` and `axis_parallel Z` both fail `E5` (the seam),
`axis_parallel X` fails `E4` (nothing matches). The plan route and the
cad-core route agree on volume, faces, edges, solids and bounding box.

## The sketch foundation (Stage 37)

**A sketch cannot be built, and this is the honest answer rather than a
missing feature.** V1's contract lists sketches among the concepts a
validator MUST reject, and `cad_core`'s engine implements the six V1 features
and nothing else. There were three options:

1. extend `cad_core` — that modifies the stable production package;
2. execute it in the experimental layer — that is a second CAD path, which
   the architecture forbids for good reason;
3. represent it precisely, validate it thoroughly, and say **explicitly**
   that execution is unsupported.

Stage 37 took the third. `adapter.ExecutionUnsupported` is that answer.

### Why it is a distinct answer

There are now five different "no" in this system, and collapsing any two
would misreport the state of the project:

| Failure | Means |
|---|---|
| parse | the model did not produce a plan |
| plan (`P`-rules) | the plan is malformed or incoherent |
| validation (`S`-rules) | the CAD document is invalid |
| geometric (`E`-rules) | the kernel refused the geometry |
| **execution unsupported** | **the plan is fine and the backend is not** |

`POST /experimental/build-plan` answers **501 Not Implemented**, not 400: the
plan is not the problem. The response names `execution_unsupported`, the
offending types and the offending operation ids, and carries **no document,
no build and no render model** — there is nothing that could be mistaken for
geometry. The page says "not executable here", in warn colour, not "the build
failed" in red.

The check runs over the whole plan **before a single feature is emitted**, so
a partially translated document can never escape. A plan is executable or it
is not; there is no partial build.

### Constraints are checked, never solved

A `length` of 80 on a line that measures 50 is reported as a **conflict**
(`P22`) and the line is not moved. Solving would hide the disagreement and
would invent geometry the backend cannot execute anyway. The comparison uses
a relative tolerance (`1e-9`), not float equality: a length computed from
endpoints must not need bit equality. A test pins both directions — twelve
significant figures agree, eight do not.

### Sketch vocabulary

Three primitives (`line`, `circle`, `rectangle`) and five constraints
(`coincident`, `horizontal`, `vertical`, `length`, `radius`). No splines,
arcs, ellipses or polylines. A rectangle is one primitive rather than four
constrained lines, because holding four lines together would need a solver
and there isn't one.

Point handles are **objects**, not dotted strings:

```json
{"geometry": "l1", "point": "end"}
```

A dotted `"l1.end"` would work, and it would put a traversal-shaped string
where every other reference in this language is a plain identifier. The
security properties of the rest of the parser rest on that, so every
reference stays an identifier plus an enumerated handle name.

### Rules added

| Rule | Meaning |
|---|---|
| `P18` | ids are unique within one sketch — geometry and constraints share the namespace |
| `P19` | a constraint names geometry that exists **in this sketch** |
| `P20` | the point handle is a real point of that geometry type |
| `P21` | the constraint type applies to that geometry type (a `radius` on a line is a category error) |
| `P22` | a dimensional constraint **agrees** with its geometry |

`P11` now distinguishes two mistakes that used to read alike: "that id is a
sketch, which declares a profile and not a solid" and "that id is a modifier,
whose result keeps its own target's id".

## `extrude` and `revolve` (Stage 38)

The two operations the sketch foundation existed for, and the first
references in this language that point at a **profile**. That inverts `P11`
into `P23`.

Neither can be built either — and the Stage 37 boundary needed **no change**
to say so, because it is derived from `EXECUTABLE_TYPES` rather than from a
list somebody has to remember to extend. A test asserts the adapter names
neither operation anywhere.

### Rules added

| Rule | Meaning |
|---|---|
| `P23` | the target is a **sketch** — the mirror of `P11` |
| `P24` | an extrude's `direction` is **normal** to the sketch's plane |
| `P25` | a revolve's `axis` lies **in** the sketch's plane |
| `P26` | a revolve's `angle` is in (0, 360] |

`P24` and `P25` are complementary by construction, and a test measures it:
exactly the axes an extrusion may use are the ones a revolve may not. Both
come out of `PLANE_NORMAL` and `PLANE_AXES`, which are checked against the
plane *names* rather than retyped.

### Defaults, and the one that does not exist

`direction` is **optional** on an extrude: the plane fixes the axis, so only
the sign is a choice and the positive normal is a defensible default.

`axis` is **required** on a revolve and has no default. A profile on XY
revolved about X and about Y are different parts, so defaulting one would be
inventing geometry — the same reason a `through_hole`'s `position` is
required. The axis is also **signed**, because 90° about `+Z` and about `-Z`
are mirror images; that is deliberately unlike an edge selector's unsigned
axis (Section C.7), where parallelism has no direction.

### What this layer refuses to guess

**Whether a revolved profile crosses its own axis is not checked.** A profile
straddling its axis of revolution produces self-intersecting material.
Deciding that needs the resolved 2D geometry measured against the axis line —
the kernel's kind of judgement, of the same sort as `E1`–`E5` — and there is
no engine for this operation to ask. So nothing here guesses at it: such a
plan is accepted as plan-valid, and still cannot be built. A test pins the gap
so it stays visible rather than being mistaken for an oversight.

## The language is larger than the engine

Since Stage 37 the plan language can express three things the backend cannot
build. That is a real and intentional state, and it is worth being precise
about what it does and does not mean:

* every one of the nine operations is **parsed and validated** to the same
  standard;
* three of them **cannot produce geometry of any kind** — no solid, no mesh,
  no STEP, no volume, no bounding box, no build key;
* the refusal is **explicit and machine-readable**, not an error message a
  caller has to interpret;
* **nothing is approximated.** There is no extrude-as-a-box and no
  revolve-as-a-cylinder. A test scans the adapter and build layer for exactly
  that kind of substitution.

A plan mixing buildable and unbuildable operations — a sketch, an extrude and
a fillet — is refused **as a whole**, and only the operations this backend
cannot execute are named. The fillet is not blamed for something it could
have done.

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

There are **63 fixtures** as of Stage 38: 19 that build, 30 refused by the
plan validator, 5 refused further down (the V1 validator or the engine), and
9 that are **valid and unexecutable**. The tables below list a representative
subset; `python -m cad_experimental.local_plan_provider --list` prints them
all.

The rejecting fixtures exist because a rule nothing exercises is a rule nobody
has tested. Each names the code it expects, so "rejected somehow" cannot pass
for "rejected correctly":

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

Nine fixtures are **valid and unexecutable** — the instrument for the one
answer that is neither a build nor a rejection. Each is asserted to be
plan-valid, to carry no V1 document, and to report no volume, bounding box,
build key, solid count, face count or render model:

| Fixture | What it is |
|---|---|
| `sketch-rectangle-profile` | a 100 × 60 profile on XY |
| `sketch-constrained-lines` | two lines, with agreeing horizontal, vertical, length and coincident constraints |
| `sketch-beside-a-solid` | a sketch next to a buildable box — the *whole* plan is refused |
| `extrude-rectangle-to-plate` | the box fixture's plate, as a sketch and an extrusion |
| `extrude-negative-direction` | the same, extruded along `-Z` |
| `extrude-then-fillet` | a fillet on the extruded solid — the fillet is not blamed |
| `extrude-one-profile-twice` | one profile, two thicknesses; a profile is not consumed |
| `revolve-full-turn` | an XZ profile revolved 360° about `+Z` |
| `revolve-partial-turn` | the same, 90° |

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
* **The prompt's size advantage has disappeared.** It was 4018 characters for
  two operations at Stage 32; it is **14685 for nine** at Stage 38, against
  the production prompt's **14943** for the six V1 features. See the audit
  below: this bears directly on the experiment's central hypothesis.
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

## Stage 39: audit

One checkpoint, run after Stage 38, over the whole experiment. Everything
below was measured, not estimated.

### Test and suite state

| Suite | Result |
|---|---|
| experimental (`tests_experimental`) | **681 passed** |
| production API + AI (`apps/api/tests`) | **547 passed, 2 skipped** — unchanged |
| stable frontend (`vitest`) | **87 passed** — unchanged |
| stable frontend typecheck | clean |
| experimental frontend typecheck | clean |

The two skips are production's deliberate live-provider gates and must stay
skipped. No test anywhere is quarantined, weakened or skipped to pass.

### Production isolation

`git diff --name-only` against the stable branch changes **35 files, and not
one of them is production code.** Everything under `packages/`,
`apps/api/src/cad_api`, `apps/api/src/cad_ai`, `apps/api/tests` and
`apps/web/` is byte-identical to `claude/text-to-cad-skeleton-r946xr`. The
only non-experimental change is `.gitignore` (a bare `node_modules`, because
a trailing slash does not match a symlink) and this document.

Six stages added **no** second CAD engine, validator, build path, cache,
exporter, artifact registry or RenderModel, and never touched `local_cad.py`,
`edge_selection.py` or the V1 validator.

### Rule coverage

All 26 plan rules `P1`–`P26` are raised by the validator and named in at
least one test. Two gaps were **found by this audit and fixed**:

* `P3` (unknown operation type) and `P6` (a non-finite position component)
  were raised by the validator and named in **no test in the whole suite**.
  Both are unreachable through the parser by design, which is precisely why
  nobody had checked they still fire. `test_defence_in_depth.py` now builds
  plans in code, bypassing the parser, and confirms both. They did fire.
* the "no module opens a file" guard covered `parser`, `adapter`, `plan` and
  `validation`, but not `sketch.py` or `build.py`, which were added later. It
  now covers all six.

12 of the 26 rules have a unit test but **no fixture**. Fixtures are the
run-for-real instrument, so that is a genuine, if lesser, coverage gap:
`P1`, `P2`, `P3`, `P5`, `P6`, `P7`, `P8`, `P9`, `P13`, `P15`, `P16`, `P17`.

### Security posture, re-scanned

An AST scan of all 13 modules for `eval`/`exec`/`compile`/`__import__`,
computed-name attribute access, dunder traversal
(`__class__`/`__dict__`/`__globals__`/`__subclasses__`) and the dangerous
imports (`subprocess`, `pickle`, `socket`, `importlib`, `requests`, `urllib`,
`httpx`, `shutil`, `ctypes`, `marshal`) returns **nothing**.

Two `open()` calls exist, both inside a CLI `main()`, both on a path a
developer typed as an `argparse` argument: `harness.py` writes a report where
it was told to, and `local_plan_provider.py` reads a developer-supplied plan
file. Neither touches model output. Nothing on the model's data path — the
parser, plan, validator, adapter, build layer or sketch module — opens a file.

`sketch.py` imports **only** `__future__`, `dataclasses` and `typing`: no
kernel, no `cad_core`, no numpy, nothing.

### The CAD backend: keep CadQuery/OpenCascade

Evaluated, as the audit brief asked, and **not switched**. The evidence for
keeping it:

* **It is exact.** A `d20 × 50` cylinder measures `15707.963267948966` mm³
  against `π·10²·50` at a relative error of **0.0**. A plate drilled and then
  chamfered on four X-parallel edges lands within `1.2e-16` of its closed
  form. Every geometric claim in this document is a closed-form check, not a
  measurement recorded as its own expectation.
* **It is B-rep, and the exports prove it.** A STEP file for that cylinder is
  5664 bytes and contains **one `CYLINDRICAL_SURFACE`, two `PLANE`s, zero
  `B_SPLINE`s and zero `TRIANGULATED` entities** — exact analytic surfaces,
  not an approximation of them. The same part's *mesh* is 500 triangles. A
  mesh-based backend would give up that distinction, and with it STEP and
  IGES as meaningful outputs.
* **It says no.** `E1`–`E5` exist because the kernel refuses work it cannot
  do: a hole that misses the material, a cut that splits the body, a fillet
  radius an edge cannot take. A backend that silently produced *something*
  would be worse for this project than one that fails, because the whole
  design rests on wrong output being reported rather than patched.
* **The one real friction is not the kernel's fault.** The seam behaviour
  (below) is a genuine limitation of parameterised-surface topology, not of
  this particular kernel, and would appear in any B-rep engine.

Switching would cost the exact volumes, the two B-rep exporters, the `E`-rule
guarantees and 1481 cad-core tests, to buy nothing this project needs. **The
recommendation is to keep it.**

### The experiment's central hypothesis is now in doubt

Stage 32 asked whether a flatter, smaller operation-plan language is easier
for a model to get right than the canonical V1 document. Part of the argument
was a much shorter prompt. That part has been measured away:

| Stage | Operations | Prompt |
|---|---|---|
| 32 | 2 | 4018 chars |
| 33 | 3 | 5374 |
| 34 | 4 | 6934 |
| 35 | 5 | 8846 |
| 36 | 6 | 9590 |
| 37 | 7 | 12408 |
| 38 | 9 | **14685** |

Production's prompt, for the six V1 features, is **14943**. The experimental
prompt is now **98%** of it. Whatever advantage the operation plan has, at
this vocabulary size it is **not** brevity.

This does not settle the question — the plan language is still flatter, its
references are still operation-level, and it still has no `units` field to get
wrong. But the cheap part of the hypothesis is gone, and **the question can
only be answered by a live comparison that has never been run.**

## Stage 40: the real Haiku comparison

**The first live Anthropic measurement in this project's history.** Every
earlier evaluation ran on Gemini or on a stub. The full run, its raw output
and its diagnosis are preserved under
`docs/evaluation-baselines/stage40-v1-vs-operation-plan/`.

130 real calls: 13 cases, 5 attempts, both representations, one model
(`claude-haiku-4-5-20251001`). Everything was frozen and committed before the
first call.

### The measured result

| Metric | V1 JSON | Operation Plan |
|---|---:|---:|
| Model output valid | 100.0% | 100.0% |
| Parse/structure valid | 0.0% | 0.0% |
| CAD validation valid | 0.0% | 0.0% |
| Build success | 0.0% | 0.0% |
| Semantic correctness | **0.0%** | **0.0%** |
| RenderModel success | 0.0% | 0.0% |
| Correct unsupported handling | 0.0% | 0.0% |
| Prompt characters | 14 943 | 14 685 |
| Mean latency | 2.69 s | 1.98 s |
| Provider errors | 0 | 0 |

Every call was answered and every call was rejected at parse. One cause
dominates: Haiku wrapped its JSON in a markdown fence, and **both** parsers
refuse a fence by design -- neither repairs model output, deliberately, on
both sides. The strict transport, not the representation, decided every case.

### Why structured output was unavailable -- a finding in itself

Both representations were designed to be used *with* API structured output,
which is what makes a strict parser reasonable. Measured against the live
API:

* the **V1** schema is accepted once `exclusiveMinimum` is stripped: **8**
  optional properties;
* the **operation plan** schema is refused outright: **31** optional
  properties against an API limit of **24**.

The cause is the plan's own shape. Its flat `parameters` object must hold all
fifteen parameter names of all nine operation types, and every one is
optional, because which are required depends on the sibling `type` field.
Sanitising cannot reduce that count -- a test asserts it.

So there is no configuration in which both frozen representations can use
structured output. Running V1 with it and the plan without would have handed
V1 a grammar-constrained decoder the plan cannot have, so both ran without
it. **The flat plan shape that makes the language easy to extend is the same
thing that makes it incompatible with grammar-constrained decoding.** That is
a real cost, and it was invisible until something tried to use it.

### Where the failures came from

Replaying the recorded output through the same frozen parsers, validators,
adapter and build -- after extracting the fenced JSON block, the only leniency
added -- gives the counterfactual for a fence-tolerant transport:

| | V1 JSON | Operation Plan |
|---|---:|---:|
| Would-be correct | 16 / 65 | **56 / 65** |
| Rate | 24.6% | **86.2%** |

On the eight cases that produce manufacturable geometry the operation plan
was **40/40** and V1 was **0/40**.

How each answer was framed:

| Shape | V1 | Plan |
|---|---:|---:|
| fenced JSON | 22 | **65** |
| fenced JSON + trailing prose | 21 | 0 |
| prose only, no JSON at all | **22** | 0 |

V1's failures were about its **envelope**, not about CAD: 22 answers were
English prose with no JSON, and 16 were a correct-looking document with no
`status` wrapper. The plan's flatter envelope -- `status` and `operations` at
the top level -- was followed on all 65 attempts.

The plan's own 9 failures are both traceable to this project's decisions
rather than to the model:

* **6 refusals of the sketch cases.** The Stage 37/38 prompt says an extrude
  or revolve "cannot be built" and tells the model not to offer one where a
  solid operation will do. Haiku read that as *unsupported* and refused --
  which is a defensible reading of what it was told. Case 10 was refused
  5/5 for exactly this reason.
* **3 parse rejections on `"reason": null`.** The model emitted an explicit
  null beside `status: generated`; the frozen parser accepts a string or an
  absent key, not a null.

This is a **diagnostic, not a score.** It was computed after the scores were
seen, it replaces nothing, and no prompt, parser, validator or expectation
was changed to produce it.

### What Stage 40 settles, and what it does not

Settled: the prompt-size hypothesis is dead either way (14 685 vs 14 943, and
the shorter prompt scored the same 0%). The plan's *envelope* is markedly
easier for Haiku to produce than V1's nested one. And the plan cannot use
structured output at all.

Not settled: whether the plan is better **in a configuration either could
ship in**. Both need a transport that tolerates a fence, or the plan needs a
schema shape the API will compile. Until one of those exists, no adoption
decision has evidence behind it.

## Stage 41: making the plan schema provider-compatible

Stage 40 left the operation plan unable to use Anthropic structured output at
all. Stage 41 restructured the schema and measured the provider's real
constraints, which are four rules acting at once rather than the one
documented:

| Rule | Measured |
|---|---|
| Optional properties, whole document | at most 24 |
| Optional properties, **any single object** | at most ~14 |
| Compiled grammar size | 8 operation branches accepted; any 9th refused |
| `oneOf` | rejected; `anyOf` accepted |
| `additionalProperties: false` | required on every object |
| `exclusiveMinimum` / `maximum` / `maxItems` | rejected; `minItems` only 0 or 1 |
| Unused `$defs` | still cost grammar budget |

`plan_schema()` became a **discriminated union**, one branch per operation
type, each branch derived from `OPERATION_FIELDS` and `PARAMETERS` -- the same
tables the parser reads, so schema and parser cannot drift. Optional
properties fell from **31 to 9**, and the wire format did not change at all,
so the parser, the plan validator and the V1 adapter needed no edit.

The nine-operation schema is still refused on grammar size, and `sketch` is
what breaks it. `provider_schema()` is therefore the same shape over exactly
`EXECUTABLE_TYPES` -- the six buildable operations -- and **is** accepted.
That takes nothing from the language: `plan_schema()` still describes all
nine and the parser still accepts all nine.

Live, on real Claude Haiku 4.5 with structured output on: a plate with a
centred d20 hole came back unfenced, parsed, validated, adapted, built, and
measured `56858.407346410204` against a closed form of the same -- with a
RenderModel of 520 triangles. Two further cases (a d20x50 cylinder, a
chamfered plate) also built to their exact closed forms. That is Stage 40's
markdown-fence blocker resolved for the executable subset.

Three advisory bounds left the schema because the API rejects them
(`maxItems` on tools and the sketch lists, `maximum` on a revolve angle).
None was a weakening: `MAX_TOOLS`, `MAX_GEOMETRY` and `MAX_CONSTRAINTS` are
enforced by the parser, and the angle range by rule P26.

## Stage 42: a second CAD backend (FreeCAD), for comparison

**CadQuery remains the default and the baseline.** FreeCAD is an experimental
second backend, added so the project can eventually answer *which engine is
better for the mechanical work we want to build* with evidence rather than
preference. Nothing about the stable application changed, and FreeCAD is
never selected implicitly.

### Availability, measured

FreeCAD is **not** a pip package and is **not** in Ubuntu 24.04 (`apt-cache
search freecad` returns nothing on noble). It was installed from the official
AppImage and extracted:

```sh
curl -sSL -o FreeCAD.AppImage \
  https://github.com/FreeCAD/FreeCAD/releases/download/1.0.0/FreeCAD_1.0.0-conda-Linux-x86_64-py311.AppImage
chmod +x FreeCAD.AppImage && ./FreeCAD.AppImage --appimage-extract   # ~2.4 GB
```

| | |
|---|---|
| Version | **FreeCAD 1.0.0**, revision 39109, build 2024/11/18 |
| Python module | `FreeCAD` and `Part` import into the project's own Python 3.11 |
| Headless | yes -- no GUI, no `FreeCADGui`, no `App.Document`, no recompute |
| Alongside CadQuery | yes -- both kernels load in **one process**, which is what makes the cross-backend tests possible |

One real environmental constraint: FreeCAD's bundled `libssl` conflicts with
the system `libcrypto` once the latter is loaded, so its `usr/lib` must be on
the dynamic linker's path **before Python starts**. That cannot be fixed from
inside a running process, and the backend says so precisely rather than
failing vaguely.

### How to enable it

```sh
export CAD_FREECAD_HOME=/path/to/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib
export CAD_BACKEND=freecad          # default is `cadquery`, and stays so
```

`CAD_FREECAD_HOME` is read as a **filesystem location only** -- nothing there
is executed, no subprocess is spawned.

### Architecture

```
Operation Plan -> V1 adapter -> CAD backend interface -> RenderModel
                                  |-- CadQueryBackend  (default, baseline)
                                  `-- FreeCadBackend   (experimental)
```

`cad_backend.py` holds the interface, the neutral `Measurement` and
`Selector` types, and `resolve_backend()`. `cadquery_backend.py` is a **thin
adapter over the existing engine** -- it delegates selection, the RenderModel
and STEP to `cad_core` rather than reimplementing them, so the baseline stays
exactly what it has always been. `freecad_backend.py` implements the same
Section C semantics against FreeCAD's `Part` API.

**There is no fallback.** A FreeCAD failure raises `BackendUnavailable`; it
never becomes a CadQuery result. A caller that asked for one engine and
silently received another would have no way to know which engine produced
the part it is about to manufacture.

### Supported operations

Exactly the six V1 features: `box`, `cylinder`, `through_hole`, `subtract`,
`fillet`, `chamfer`. Sketch, extrude and revolve are **not** implemented on
either backend -- no engine here executes them -- and the interface has no
methods for them.

### Cross-backend results

Same inputs, two engines, measured the same way:

| Part | CadQuery volume | FreeCAD volume | rel. diff | solids | faces |
|---|---:|---:|---:|:--:|:--:|
| box 100x60x10 | 60000.000000000 | 60000.000000000 | 0.00e+00 | 1/1 | 6/6 |
| cylinder d20 h50 | 15707.963267949 | 15707.963267949 | 0.00e+00 | 1/1 | 3/3 |
| plate + d20 through hole | 56858.407346410 | 56858.407346410 | 0.00e+00 | 1/1 | 7/7 |
| plate - d20 cylinder | 56858.407346410 | 56858.407346410 | 0.00e+00 | 1/1 | 7/7 |
| plate, fillet r2 on Z edges | 59965.663706144 | 59965.663706144 | 0.00e+00 | 1/1 | 10/10 |
| plate, chamfer d2 on Z edges | 59920.000000000 | 59920.000000000 | 0.00e+00 | 1/1 | 10/10 |

Both agree with the **closed form** for every part, and with each other to a
relative difference of zero. Comparison is geometric: volume, solid count,
bounding box and topology counts within explicit tolerances. Topology
identifiers, face and edge numbering and internal OpenCascade structure are
deliberately **not** compared -- two kernels may reach the same solid by
different routes.

STEP interoperates in both directions: a FreeCAD STEP file re-imports in
CadQuery to the same volume, and a CadQuery STEP file re-imports in FreeCAD
to the same volume. Export is verified by re-reading and re-measuring, never
by the file merely existing.

### RenderModel

FreeCAD produces **the existing** `cad_core.render_model.RenderModel` -- the
same class, the same `format_version`, `units`, `coordinate_system`,
`winding` and `normal_binding`, all imported from the existing module rather
than retyped. There is deliberately no second render format, and the frontend
cannot tell which engine produced a mesh.

The tessellations differ, as two tessellators will: for the drilled plate,
CadQuery gives 530 vertices / 520 triangles and FreeCAD 466 / 932. Both are
valid meshes of the same solid; render bounds agree within the deflection.

### Timing

Coarse, one run, no warm-up -- orders of magnitude only. Every operation on
these parts is single-digit milliseconds on both engines; FreeCAD's
tessellation was somewhat faster on this sample. No benchmark was run and
none should be read into these numbers.

### Known limitations

* FreeCAD requires a 2.4 GB extracted AppImage and an `LD_LIBRARY_PATH` set
  before process start. It is not a dependency of anything, and is not in any
  requirements file.
* Only the six V1 operations are implemented. Sketch, extrude and revolve
  remain unexecutable on **both** backends.
* The Operation Plan pipeline still runs through `cad_core` -- this stage
  added the backend boundary and proved parity, it did not reroute the
  application.
* Six parts is a small sample. Agreement here says nothing about how the two
  engines diverge on the harder geometry this project ultimately wants.
* **FreeCAD is not production-ready here, and is not proposed as such.**

## Stage 43: structured output, and a Windows worker that could not build

Stage 43 is **preparation only**. It makes the Stage 40 comparison askable for
the first time and fixes the local environment it would be measured in. **The
comparison itself has not been run.**

### Why the Stage 40 result could not answer its own question

Stage 40 ran both arms with a strict parser and no grammar constraint, because
the operation-plan schema could not be compiled at all — 31 optional
properties against an API limit of 24. Claude Haiku then fenced almost every
answer, both parsers refuse a fence by design, and the run scored 0% on both
sides. The transport decided all 130 cases; the representations were never
compared. Stage 41 removed that blocker by restructuring the plan schema.

A re-run of the frozen Stage 40 instrument on Windows reproduced the original
result exactly — 130/130 `PARSER_REJECTED`, 0% on both arms, 110 of 130
answers fenced (the plan arm 65/65). The instrument is reproducible.

### The new mode

`cad_experimental.stage43_structured_comparison` is a **separate stage**, not
an edit to Stage 40. The frozen instrument — the corpus, both prompts, both
parsers, both validators, the adapter, the build and every scoring rule — is
imported and used unchanged; `representation_comparison.STRUCTURED_OUTPUT_ENABLED`
is still `False` and a test asserts it. The one intended difference is that
both arms are constrained by their own JSON schema at the API.

Stage 40's `_TimedModel` drops `output_schema`. Rather than modify it,
`StructuredModel` sits *beneath* it and puts the schema back, choosing the arm
by **exact** system-prompt match — an unrecognised prompt raises rather than
guessing, because attaching the wrong arm's grammar would silently corrupt a
measurement. Nothing strips a fence, repairs, retries or re-prompts; guard
tests assert a fence literal may only reach `startswith`-style detection,
never `replace`/`strip`/`split`.

### Schema preflight

Offline (free) and then live (two calls, acceptance only):

| | V1 JSON | Operation Plan |
|---|---:|---:|
| Optional properties | 8 | 7 |
| Worst single object | 4 (limit 14) | 2 (limit 14) |
| Rejected keywords | none | none |
| Accepted live | **yes** | **yes** |
| Markdown fence | **no** | **no** |

**V1's raw schema carries `exclusiveMinimum` and `minLength` and must be
sanitised to compile; the plan's Stage 41 schema is already clean.** Stage 40's
own `sanitise_schema` does it, and removes only bounds — 29 and 47 properties
identical before and after, `required` lists unchanged, no property lost. The
dropped bounds are still enforced by the parser and the validator.

### The Windows build worker: two causes, both in production code

Deterministic committed fixtures — no model involved — failed identically to
the model-driven run, which is what identified this as environment, not output.

1. **The child had no home directory.** `INHERITED_ENVIRONMENT_NAMES` omitted
   `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH`. `import cadquery` loads its DXF
   exporter at module scope → `ezdxf` builds its options at import time →
   `Path("~").expanduser()`. POSIX falls back to the `pwd` database, which is
   why Stage 40 on Linux was unaffected; Windows has no fallback and raises.
   Every isolated build died before touching geometry.
2. **OpenCascade aborts at interpreter shutdown.** With (1) fixed the child
   built correctly and then exited `0xC0000374` during finalisation; the host
   classifies on the response first and the exit code second, so a correct
   build was reported as a protocol error. The worker entrypoint now flushes
   and calls `os._exit(code)`. Nothing in `cad_core` registers an `atexit`
   hook, `__del__` or finaliser, and the response is published with
   `os.replace` before `main` returns, so finalisation had no work left to do
   — only a way to fail. The exit code is passed through unchanged.

Both were documented in `CLAUDE.md` §15 as *test* traps; they were live in
production code on this branch, which forked before the stable branch's
Windows fixes.

`test_local_plan_provider` went from 21 failures and 59 errors to **45 OK**.
The chain now runs end to end on a committed fixture: parse → validate →
adapter → worker → build succeeded.

### What this licenses

Only that the two blocking questions are answered: **the transport can deliver
both representations** (unfenced, schema-constrained), and **the local
environment can build them** (deterministic fixtures pass). Which
representation Claude Haiku handles better is **still unmeasured** — that is
the run this stage prepares and deliberately did not start.

## Stage 44: why the model refused every profile case

Stage 43 measured both representations with structured output on, and the
operation plan answered `unsupported` **5/5 on both profile cases** (09
`profile-extrude`, 10 `profile-revolve`). The run recorded that as the model's
judgement. It was not one, and the recorded output is what shows it.

### The root cause

Two separate things stood between the model and a profile plan. **Either one
alone was sufficient**, which is why fixing only the obvious one would have
changed nothing.

**1. The grammar did not contain the answer.** `provider_schema()` returned
the schema over `EXECUTABLE_TYPES` — the six buildable operations. Stage 41
built that subset for a good reason (the nine-branch schema is refused on
grammar size) and it was correct for Stage 41, which ran *without* structured
output. Stage 43 turned structured output on and kept the same schema. A
decoder constrained by a union with no `sketch`, `extrude` or `revolve` branch
**cannot emit one**: refusing was the only reachable answer, and no wording in
the prompt could have produced a different one.

This is the finding worth keeping: **a schema is what the model may SAY; the
execution boundary is what the engine can BUILD.** They are different
questions. Narrowing the first to match the second looked conservative and was
in fact a silent one — it removed an answer from the model's reach and then
scored the model for not giving it.

**2. The prompt instructed the refusal.** Two paragraphs said so outright:

> IMPORTANT -- a sketch cannot be built. […] So do not offer a sketch as a way
> to make a part
>
> IMPORTANT -- neither an extrude nor a revolve can be built. […] So do not
> offer them as a way to make a part

The model quoted that back almost verbatim. Attempt 1 on case 09:

> "Extrude operations are not executable. This language can express a sketch
> and an extrude, but the backend cannot turn them into geometry. […] Use a
> box operation instead"

It read *unexecutable* as *unsupported*, which is exactly what it was told.
`CLAUDE.md` §19 had flagged this wording as a known bug left deliberately
unfixed so that Stage 40's cause would stay clean; this is the stage that
fixes it.

A third, smaller contributor showed up on case 10 only: the "when to say
unsupported" list named `sweeps`, with no exemption, two sections after
describing an extrude and a revolve as sweeps.

### What was NOT the cause

Measured before anything was changed, by putting both profile plans through
the real machinery:

| Layer | Behaviour | Verdict |
|---|---|---|
| `parser` | accepts `sketch`, `extrude`, `revolve` | correct |
| `validation` (P18–P26) | `valid=True`, no problems | correct |
| `adapter` | raises `ExecutionUnsupported('sketch','extrude')` | correct |

The plan layer built in Stages 37–38 was doing exactly its job. Nothing in it
was changed.

### The fix

**`provider_schema()` now covers the whole vocabulary — all nine operation
types — in eight branches**, by merging `fillet` and `chamfer` into one.

Nine types do not fit one-per-branch: Stage 41 measured the ceiling at eight
branches, and a ninth was refused even stripped to a single field. That pair
is the one that merges without describing anything new — same operation-level
keys, same required `edges` selector, differing only in the **name** of their
one length (`EDGE_MODIFIER_LENGTH`, a table that already existed because the
parser, validator and adapter all needed it).

The cost is stated exactly, and it is confined to the grammar: in a merged
branch the parameter properties are the **union** of the members' and
`required` is their **intersection**, because a grammar cannot condition one
property on a sibling's value. So `radius` and `distance` both become
grammatically optional, while `target` and `edges` stay required. The parser
re-derives the exact per-type requirement from `PARAMETERS` as it always has,
and a test proves the gap is real and closed: a `fillet` carrying a chamfer's
`distance` satisfies the merged branch and the parser still refuses it.

Four schemas now exist, each with one job:

| Function | Types | Branches | Chars | Purpose |
|---|---|---|---|---|
| `plan_schema()` | 9 | 9 | 6423 | the faithful description; what the API publishes. Merges nothing, so a provider will not compile it |
| `provider_schema()` | 9 | 8 | 6067 | **the default**: what a constrained decoder is pointed at |
| `compact_provider_schema()` | 9 | 8 | 4788 | the same, minus a sketch's optional `constraints` |
| `executable_schema()` | 6 | 6 | 3105 | what Stages 41–43 sent, kept under its own name |

**Nothing selects a variant automatically.** A caller that asked for one
schema and silently got another could not know what its numbers mean — the
same reason `resolve_backend()` never falls back to a different CAD engine.

And the prompt (`2026-09-15.1`, was `2026-09-10.7`) stopped telling the model
that profile operations cannot be built. It now:

- says a sketch, an extrude and a revolve are part of the language, and that a
  description naming a profile is answered with one — *"a 40 mm square profile
  on the XZ plane, extruded 5 mm" is a `sketch` and an `extrude`*;
- states plainly that **whether the engine can build a plan today is not the
  model's decision and does not change its answer**;
- leaves what a profile sweeps out to the kernel, in the same words the fillet
  section already used for feasibility — *"whether a revolved profile crosses
  its own axis of revolution"* is the engine's judgement. This is case 10's
  failure mode: the model reasoned its way to "that would be a torus, which is
  not supported" and refused;
- exempts an extrude and a revolve from the `sweeps` line in the unsupported
  list;
- keeps every existing refusal — spheres, unions, lofts, assemblies, *"do not
  approximate"* — and keeps *"do not offer a sketch when the solid operations
  already say the part: a 100 x 60 x 10 mm plate is a `box`"*.

The prompt's worked example deliberately does **not** reuse a corpus case's
wording. Nothing about the benchmark is encoded in production behaviour.

Stage 43's module is pinned to `executable_schema()` and does not follow
`provider_schema()`. Re-running Stage 43 must reproduce Stage 43: its recorded
plan-schema fingerprint `54759d1e16cfe634` still matches what the module
sends. The corpus, the scoring and the recorded baselines are untouched.

### What Stage 44 does NOT change

- **The execution boundary is exactly where it was.** A profile plan parses,
  validates, and is refused by `ExecutionUnsupported` with the offending types
  and ids named. Nothing is built, nothing is approximated, and `build_plan`
  reaches that answer without touching the application service at all.
- **No validation was weakened.** P18–P26 are unchanged. The one loosening is
  in the advisory grammar, is named above, and is covered by a test that shows
  the parser still catches what the grammar now lets through.
- **No CAD feature was added.** The engine still builds six operations.

### The limitation this stage cannot close

**It is not verified that the provider compiles `provider_schema()`.** Stage
41 measured the ceiling at eight branches and this has eight — but the eight
it measured did not include `sketch`, whose `$defs` are the largest part of
the schema (2560 of the 6423 characters). Every offline-measurable limit is
satisfied and asserted by tests: 9 optional properties against 24, worst
single object 2 against ~14, no `oneOf`, `additionalProperties: false`
everywhere, no rejected keyword, `minItems` only 1, no unused `$defs`. The
compiled-grammar size is the one limit that can only be measured by sending
it, which costs a credential and one live call — neither available where this
was written.

If the provider refuses it, `compact_provider_schema()` is the next thing to
try: a fifth smaller, because dropping a sketch's optional `constraints` drops
`sketch_constraint` and `sketch_point_handle` with it. That costs the model
nothing it needs to describe a solid — constraints are *checked, never solved*
(P21 requires a dimensional constraint to agree with the geometry it names),
so they can only restate the geometry or conflict with it. It still admits
every profile operation; a test asserts that, so the fallback can never become
a fallback to the Stage 43 behaviour.

To check, locally, with a credential:

```python
from cad_experimental.plan import provider_schema
# send one minimal request with output_schema=provider_schema();
# a 400 at request validation is the refusal, and names the reason.
```

### What is still unmeasured

**Whether the model now chooses a profile operation.** Everything above makes
it *possible* and *asked for*; only a live run shows what it does. The Stage
43 numbers (plan 61.5% build, 84.6% semantic) were measured with profile cases
forced to fail, so a re-run on the same frozen corpus is the measurement —
and the corpus, the prompts' independence and the scoring must stay exactly as
they are for it to mean anything.

**Whether merging `fillet` and `chamfer` costs accuracy.** The grammar no
longer stops a model from writing a `fillet` with a `distance`. The parser
catches it, so it becomes a parse failure rather than a wrong solid, but it is
a new way for a call to be wasted and it has not been observed.

### Tests

`tests_experimental/test_stage44_profile_addressability.py`, 31 tests, covers
the model-facing path the plan layer's own tests could not reach: that the
grammar admits a sketch, an extrude and a revolve (and that the Stage 43
schema admitted none of them), that the prompt documents exactly the types the
grammar admits, that a stub model's profile plan comes back `GENERATED` and
valid through `OperationPlanService`, that valid and invalid references land
on P9/P10/P23, and that the result is still explicitly unexecutable with
nothing built. The structural matcher it uses is checked against four negative
cases first, so a matcher that said yes to everything would fail loudly rather
than make the module vacuous.

## Stage 45: the plan as a dependency and history graph

### What was already there

Chained multi-feature plans **already worked**, and that is worth stating
plainly before describing what changed. Measured against the real machinery
before anything was edited:

```
box "body" -> cylinder "cutter" -> subtract(body, [cutter]) -> fillet(body)
```

parses, validates with **zero problems**, converts to a V1 document whose
features are `box, cylinder, subtract, fillet` in that order, and that
document passes the V1 validator. The dependency semantics were built
incrementally across Stages 33–38 and are complete:

| Semantic | Where |
|---|---|
| a reference must name an operation that exists | P9 |
| a reference must appear **strictly earlier** — so no forward references and no cycles | P10 |
| a modifier's target must be a solid | P11 |
| a consumed id may never be named again | P12 |
| a tool is neither the target nor a repeat | P13, P14 |
| a profile-solid operation's target must be a **sketch** | P23 |
| a modifier replaces its target in place and the result keeps the **target's** id | the solid-set walk |
| a subtract **consumes** its tools | the solid-set walk |
| a profile is **not** consumed by being swept | the solid-set walk |

### What was missing

The walk that enforces all of it lived inside `validate_plan` as four local
dictionaries. So a plan was *judged* as a history and could then only ever be
*reported* as a flat list. Three consequences:

1. **Nothing could answer "what is this solid made of".** The derivation of a
   part — the operations whose effect it carries, including tools that no
   longer exist — was computed and thrown away on every validation.
2. **A leftover solid in a profile chain is reported by nobody.** A plan that
   makes a solid and never consumes it is caught by S9 on the converted
   document. But a plan containing a `sketch`, an `extrude` or a `revolve` is
   refused by the adapter *before* any document exists, so S9 never runs.
   Measured: `sketch -> extrude -> box` validates clean, raises
   `ExecutionUnsupported`, and the orphan box is never mentioned.
3. **The prompt described nine operations and never the sequence.** Each
   section was written in isolation; nothing told the model how a part is
   assembled from them.

### The increment

**The walk became a module.** `cad_experimental/history.py` holds one
implementation of Section B.4's solid set, and `validation.py` *consumes* it
rather than keeping a copy. That is the architectural point: a second walk
would be a second opinion about what "consumed" means, and the two would
drift the first time an operation type was added. The consumption rule is now
written once, in `_consumed_by`, and a test asserts the validator no longer
advances the solid set itself.

`walk(operations)` yields, before each operation, the state a reference must
be judged against — `declared`, `solids`, `profiles`, `consumed`, each mapping
an id to the index that put it there. That is exactly what P9–P14 and P23
already needed, so the validator's checks were not touched.

`plan_history(plan)` builds the typed graph on top of it:

| | |
|---|---|
| `OperationStep.depends_on` | the ids this operation names — target first, then a subtract's tools **in list order**, because Section C.4 removes them in that order |
| `.consumes` | what this step took out of the solid set |
| `.declares_solid` / `.declares_profile` | what it brought into being, named by its own id |
| `.modifies` | the solid it replaced in place — the result keeps **this** id, which is why a modifier declares nothing |
| `.solids_before` / `.solids_after` | the set either side of the step |
| `PlanHistory.terminal_solids` | what is left at the end |
| `.consumed`, `.profiles` | ids taken, ids that are profiles |
| `.dependents(id)` | the reverse edges |
| `.producers(id)` | the indices that declared or changed one solid — its edit history |
| `.derivation(id)` | the transitive closure backwards: every operation whose effect is present in that solid |
| `.depth` | the longest chain any one result rests on |

`derivation` is the field that makes this a history rather than a list. For
the chain above it returns `("body", "cutter", "cut", "edges")` — **the cutter
is part of what `body` is made of**, though it was consumed two steps earlier
and names nothing now.

`depth` measures chaining, which a count of operations does not: four
unrelated boxes have depth 1 and four operations; the chain above has depth 3
and the same four operations.

**Exposed through the existing boundary.** `POST /experimental/validate-plan`
now returns a `history` object beside `valid`, `plan` and `problems`. Nothing
else changed shape.

**The prompt gained a sequence section** (`2026-09-15.2`, was `2026-09-15.1`).
It states the three rules a chain lives by — earlier-only references, a
modifier keeps its target's id, a subtract consumes its tools — adds the one
rule about how a plan ends (exactly one solid left; a solid you make and never
subtract is a leftover, not a second body), and gives the shape of nearly
every part: make the body, make what you want removed, remove it, then round
or bevel what is left. The worked example is a bracket with a slot, chosen so
it is not any corpus case; **a test parses that example out of the prompt,
validates it and converts it**, so the prompt cannot come to teach a plan the
validator rejects.

### Reported, never enforced

`terminal_solids` of length two is a **fact**, not a verdict. Stage 45 adds no
rule and no P-code: a two-solid plan is still a coherent plan and an invalid
V1 part, and S9 on the converted document is still what says so. The
alternative — a P27 mirroring S9 — was rejected for two reasons. It would
duplicate a rule that can drift from the one it copies, and it contradicts
deliberate existing behaviour: a profile may legitimately be swept twice, and
`test_a_profile_may_be_both_extruded_and_revolved` asserts that the resulting
two solids are S9's problem and not the plan validator's.

What changed is that the fact is now *visible*, including in the one case
where S9 never gets to rule on it.

### What did NOT change

- **No new operation, and no pattern/instance feature.** The vocabulary is
  still nine types. A `pattern` operation would be new CAD capability, new
  validation and new adapter work; it is not this increment.
- **The boundaries.** Parser → validator → adapter → backend is untouched.
  `history.py` imports no kernel, no `cad_core`, and computes no geometry; a
  test asserts all of that.
- **Every existing rule.** P1–P26 are unchanged, and the validator's checks
  were not edited — only where it gets the state from.
- **Stage 40 and Stage 43 methodology.** The corpus, the scoring and the
  recorded baselines are untouched.

### The next limitation

**`MAX_OPERATIONS` is 32, and a real mechanical part will reach it.** Bolt
circles, rib arrays and hole patterns are where chained plans get long, and
today each instance is a separate operation: eight holes is eight operations
plus their tools. That is the honest argument for a `pattern` operation — not
that the language cannot express a repeated feature, but that expressing one
costs a linear number of operations and the model has to keep the arithmetic
straight across all of them. That is the next increment, and it is a real
feature with its own validation, adapter and execution-boundary questions.

Two smaller ones behind it: **the corpus is single-part and shallow**, so
nothing is measured about how either representation behaves as chains get
longer — the deepest case in it is depth 2; and **`depth` is a description,
not a budget** — nothing bounds how deep a plan may be, only how many
operations it may hold.

## Stage 46: the feature graph, and `pattern`

The plan had reference and history semantics from Stage 33 onward and a
materialised history from Stage 45. What it still lacked was **structure**:
edges were re-derived in three modules, execution order was the list's rather
than the graph's, and repetition could only be written out by hand.

### Diagnosis, measured before changing anything

Two chains were traced plan → parse → validate → adapter → execution:

| Chain | Result |
|---|---|
| `box → cylinder → subtract` | valid, 3 features, **built**, one solid, closed form `43716.81` |
| `box → through_hole → fillet` | valid, 3 features, adapter fine, **kernel refused E5** |

The second failure is correct and is not this stage's: `axis_parallel Z`
matches a cylindrical hole's parameterisation seam, and the kernel refuses to
blend it. `docs/edge-selection.md` records the seam question as unresolved.
The chain itself was sound.

**What already qualified as graph semantics:** node identity (P2), typed
references (P11 solid / P23 profile), consumption (P12–P14), output identity
(a modifier keeps its target's id), and a single shared state walk with
derivation, dependents and depth.

**What was still sequence semantics:**

1. **Execution order was the list's, asserted rather than derived.** P10
   guarantees every reference appears strictly earlier, which makes list order
   *a* topological order — but nothing computed one, nothing checked the list
   against the edges, and there was no cycle detection at all.
2. **No explicit edge model.** Which key on which type is a reference was
   re-decided in `validation`, in `history` and in the adapter. **Roles** were
   implicit, so "the target must be a solid" and "the target must be a sketch"
   were two hand-written branches rather than one table.
3. **No body identity.** `live` was a flat id→index map, with nowhere to put
   feature ownership or a second body.
4. **Repetition was not expressible.** Four mounting holes meant four
   operations with hand-computed positions.

### The graph

`cad_experimental/graph.py` holds structure and nothing else — no state, no
geometry, no verdict, no kernel import. Every function is total, so it
describes a broken plan rather than refusing to, which is the point: a
diagnostic is wanted for exactly the plans that are wrong.

**Nodes** carry index, id, type, what they **produce** (`solid` / `profile` /
`nothing`) and which **body** they change.

**Edges come in two kinds, and keeping them apart is the design.**

*Declared* edges are what the plan says, each tagged with a **role**:

| Role | Names | Must be |
|---|---|---|
| `target` | a modifier's subject | a solid |
| `target` | an extrude's or revolve's subject | a profile |
| `tool` | a subtract's inputs, **ordered** | a solid |
| `source` | a pattern's subject | a repeatable feature |

The `expectation(kind, role)` table replaced the per-operation category
branches: adding an operation now means answering that question once, in one
place, instead of editing three modules and hoping they agree. `EXPECT_FEATURE`
is a third answer alongside solid and profile, and exists because a pattern is
the first operation in this language whose input is another **operation**.

*Derived* edges — `FeatureNode.after` — are the per-body feature history, and
they were not in the plan until this stage. **They fix a real bug the declared
edges could not express.** Given

```
plate → bore → mount → mounts(pattern) → break(chamfer)
```

every one of `bore`, `mount` and `break` names only `plate`. A topological sort
over declared edges alone therefore releases all three at once and produces

```
plate, bore, mount, break, mounts     ← chamfered before the holes: a different part
```

A modifier depends on its target's **state**, not merely on its identity.
Each node that changes a body now also follows whatever last changed that
body, and the order comes out right:

```
plate, bore, mount, mounts, break
```

Only the immediate predecessor is stored; the rest follows by transitivity.
A test constructs the declared-edges-only graph and pins the wrong order, so
the bug cannot silently return.

Consequently `topological_order()` equals the list order for every valid plan
— and `is_list_order()` now **checks** that rather than assuming it. Cycles
are impossible through the parser (a cycle needs a reference that is not
strictly earlier, which is P10) and are detected anyway, because the graph
must not depend on the validator having run and because P10 is a rule a later
stage may relax. A self-loop is a cycle of one.

Queries, all in plan order: `dependencies` / `dependents` (declared),
`predecessors` / `successors` (everything that orders), `ancestors` /
`descendants` (transitive), `topological_order`, `is_list_order`, `cycles`.
`POST /experimental/validate-plan` returns the whole thing beside the history.

### `pattern`

The tenth operation type, and the first that is graph-native: its input is a
feature, not a body.

```json
{"id": "mounts", "type": "pattern", "source": "mount",
 "parameters": {"count": 4,
                "placement": {"kind": "radial", "axis": "+Z",
                              "centre": {"x": 50, "y": 50, "z": 0}}}}
```

**`source`, not `target`.** Every other reference in the language names a body
or a profile; this one names the operation whose effect is repeated. Reusing
`target` would have hidden that in the wire format and in the schema, and the
roles exist precisely to keep it visible.

**Semantics, stated exactly:**

- **`count` includes the source.** Four mounting holes is `"count": 4`. The
  source is instance 0, is not moved, and is not consumed.
- **A pattern inherits its source's semantics.** Repeating a modifier is
  modifying: every instance acts on the same body, the body keeps its id, the
  pattern's own id names no solid, and S9 is untouched however many instances
  there are. Nothing can target a pattern (P11 refuses it, for free).
- **Placement is a discriminated object**, `linear` or `radial` — not a flat
  bag of optional fields, which would need every field optional and could not
  say which combination is meant. `PLACEMENT_FIELDS` is the table the parser,
  the validator and the schema all read.
- **Radial `angle` is the step between consecutive instances**, and omitting
  it means `360 / count` — a full circle divided evenly, which is what a bolt
  circle is and is the division a model most easily gets wrong by hand.
- **What may be repeated is a table**, `PATTERNABLE_TYPES`, today
  `(through_hole,)`. A pattern varies *where* a feature goes, so the feature
  must have a position. Widening it is one line with one place to audit.

**Placement arithmetic lives in `pattern.py`, not the adapter**, because where
an instance goes is a fact about the representation rather than about any
engine. Instance *k* is computed from the source and *k* alone, never from
instance *k−1*: accumulating a rotation would let error grow along the pattern
and make the eighth hole depend on how the first seven were computed. A test
repeats a 360° step eight times and lands exactly back on the source.

Rotation is right-handed about the **signed** axis, verified on all three
(`+Z`: X→Y, `+X`: Y→Z, `+Y`: Z→X), and `-Z` mirrors `+Z`.

**New rules.** P27 the source is repeatable; P28 the count is a whole number
in `[2, 64]`; P29 the placement is coherent **and expressible**; P30 the
derived instance ids do not collide; P31 the graph is acyclic. P9, P10 and P12
apply to a `source` unchanged — "is this declared, earlier and still
available" is the same question whatever the answer must *be*, and `_resolve`
asks it once for every role.

P29's expressibility check is the interesting one. A radial pattern must turn
its source about an axis the source is already parallel to: turning a `+Z`
hole about `+X` would tilt it onto a direction V1 has no word for, so the
instance could not be **written down** at all. That is a representation limit,
decided here; whether the fourth hole still meets material is E1's and the
kernel's, exactly as for a single hole.

**Execution.** The adapter expands a pattern into one V1 feature per extra
instance, with ids `{pattern-id}-{k}` from `instance_id` — the same function
P30 checks against, so the validator and the adapter cannot spell it
differently. Instance 0 is not re-emitted; the source already wrote it.

Measured: a 100×100×10 plate with a Ø20 bore and four Ø6 holes on a 35 mm bolt
circle built to `95727.43399111787` mm³ against a closed form of
`95727.43399111788` — agreement to 1 ULP, one solid.

`pattern` is **executable but is not a V1 feature**, and Stage 46 split those
two ideas apart: `V1_FEATURE_TYPES` is the six that become exactly one feature
with the same id, `EXECUTABLE_TYPES` is what the adapter can translate at all.
`executable_schema()` stays pinned to the first, so Stage 43's recorded
fingerprint `54759d1e16cfe634` is unchanged and re-running Stage 43 still
reproduces Stage 43. A test asserts that fingerprint.

The schema now carries **ten operation types in eight branches** —
`MERGED_SCHEMA_GROUPS` gained `PROFILE_SOLID_TYPES` alongside
`EDGE_MODIFIER_TYPES`. Provider acceptance remains **unverified**, exactly as
Stage 44 left it.

**No backend learned the word `pattern`.** It is expanded in the adapter and
never reaches an engine; a test asserts no backend has such a method and that
`graph.py`, `pattern.py` and `history.py` import no kernel and no `cad_core`.

### Multi-body groundwork

`PlanHistory.bodies` is a tuple of `Body` records — id, origin, origin index,
the ordered features that shaped it, whether it is still live, and what
consumed it. `owner_of(feature)` answers feature ownership; `live_bodies`
answers what is standing.

Nothing here **assumes** one body. Rule S9 still requires a plan to end with
exactly one, and that rule is the V1 validator's, unchanged and unweakened.
What changed is that "the part" is now a query rather than an assumption, that
features on different bodies are not sequenced against each other, and that a
consumed tool is a body with a history rather than a vanished id. Assemblies
are **not** implemented and are not started.

### Deliberately deferred

- **Assemblies, and cross-body references.** Groundwork only.
- **Patterning anything but a `through_hole`.** Repeating a constructive
  primitive creates one body per instance, which is multi-body work and needs
  the single-solid question answered first.
- **A pattern of a pattern**, mirrors, and patterns along a curve: P27 refuses
  the first explicitly.
- **Relaxing P10** so a plan may be written out of order and sorted. The
  machinery now exists; the rule has not changed.
- **Verifying the provider compiles the ten-type schema.** One live call, on a
  machine with a credential.

### Known limitations

- **The seam problem still bites any edge operation on a drilled solid.** A
  fillet or chamfer after a hole selects the hole's parameterisation seam and
  the kernel refuses it (E5). This is why the execution tests put a hole after
  a pattern rather than a chamfer, and it is unresolved — see
  `docs/edge-selection.md`.
- **`MAX_OPERATIONS` is still 32**, though a pattern now buys a great deal of
  that budget back: four holes cost one operation instead of four.
- **Sequencing is conservative.** Two disjoint holes in one plate are ordered
  against each other although the geometry is order-independent. The IR cannot
  know they are disjoint, the spec evaluates features in order, and a
  conservative order is the honest one.
- **Nothing is measured.** No model has been asked to produce a pattern. The
  prompt gained a section (`2026-09-15.3`) telling it to repeat rather than
  duplicate, and words in a prompt are a hypothesis.

## Stage 47: semantic edge selection, and the seam

### Root cause, measured

OpenCascade represents a cylindrical face's **parameterisation seam** as a
genuine straight edge. Measured on a 100 x 60 x 10 plate with one d20 hole --
15 edges, and one of them is the seam:

| edge | curve | seam? | adjacent surfaces |
|---|---|---|---|
| outer corner | line, 10 mm, along Z | no | plane, plane |
| **hole seam** | line, 10 mm, along Z | **yes** | **cylinder only** |
| hole rim (x2) | circle, r 10 | no | cylinder, plane |

The seam and an outer corner have the **same curve type, the same direction
and the same length**. Nothing geometric separates them. So
`axis_parallel Z`, whose contract is "every straight edge parallel to that
axis", matched five edges: four corners and the seam.

A blend cannot take a seam. `BRepFilletAPI` accepts the edge, builds **no
contour** for it, and would leave it silently unblended; since Stage 14.1 both
consumers require complete coverage -- a matched edge is never quietly dropped
-- so the whole modifier fails with rule E5. The practical result was that
*drill a plate and break its corners*, the most ordinary mechanical chain
there is, could not be built at all.

**The seam is topologically different, and that is the fix.** It is the edge
that closes a periodic face: it appears twice in that one face's traversal,
and the kernel's own `BRepTools::IsReallyClosed` answers for it. A rim is a
circle; a seam is a line. So a selector that asks for *circular* edges can
never name a seam, and one that asks for *straight* edges can exclude it by a
topological fact rather than by guessing at position or length.

### Where the semantics live

```
Operation Plan   selector: a KIND of edge, an unsigned axis, an optional end
  -> parser      shape only
  -> validation  P15-P17, P32
  -> graph/history                 (Stage 46, unchanged)
  -> executor    resolve, then act
       -> backend.describe_edges    the ONLY place OCC topology is read
       -> edge_semantics.resolve    plain numbers, no kernel anywhere
       -> backend.fillet_edges      acts on exactly the resolved edges
  -> CAD kernel
```

`cad_experimental/edge_semantics.py` imports **nothing** -- not a kernel, not
a backend, not `cad_core`, not another project module. It is arithmetic on
`EdgeFacts`: curve type, direction or centre and normal, radius, length,
whether the edge is a seam, and the neutral names of the adjoining surfaces.
A test asserts that import list is empty, and most of this stage's tests run
on hand-written facts with no solid in sight.

Discovering the facts is a backend's job (`describe_edges`); deciding what
they mean is the resolver's. That division is what keeps OCC out of the
Operation Plan and leaves room for FreeCAD or direct OCCT. **No CadQuery
selector string, no OCC enumeration and no edge index appears in the IR.**

### The selector vocabulary

Four kinds, and no query language:

| Selector | Names | Axis |
|---|---|---|
| `{"select": "all"}` | every edge | forbidden |
| `{"select": "axis_parallel", "axis": "Z"}` | straight edges along Z, **seams included** | required |
| `{"select": "straight", "axis": "Z"}` | straight edges along Z, **seams excluded** | required |
| `{"select": "circular", "axis": "Z"}` | circular edges about Z -- a hole's rim, a cylinder's cap | optional |

plus `"position": "top" \| "bottom"` on a `circular` selector, narrowing it to
one end of its axis.

**`axis_parallel` was not redefined.** It still means what Section C.7 says
and still matches the seam, so a plan written before this stage means exactly
what it meant. `straight` is the new name for what a person means by "the
corners". Silently changing what an existing selector names would have been
worse than the problem it fixed.

**`position` is extremal, not ordinal.** `top` is every candidate at the
greatest coordinate along the axis -- so two holes through one plate both have
a top rim and both are named. Narrowing to "the single highest" would have to
pick between them, and picking silently is exactly what this layer must not
do. It is admissible only on `circular`, and only with an axis to measure
along (rule P32): a position without an axis is not a position.

### Ambiguity and determinism policy

**Nothing is guessed.** Three machine-readable resolution codes, deliberately
separate from the plan's P-codes and the specification's S/E codes -- a
resolution failure is neither a malformed plan nor a kernel refusal:

| Code | Meaning |
|---|---|
| `R1` | the selector matched no edge; the message says what the solid actually has |
| `R2` | the selection contains a seam, which no blend can take |
| `R3` | a `position` could not separate the candidates -- they all lie at one level, so `top` and `bottom` name the same edges |

A failed resolution still reports its **candidates**, because "it matched
these five and one of them is a seam" is a diagnostic and "it failed" is not.
Every one names the offending operation.

`R2` is the seam's own diagnostic. It is reachable only through `all` or the
legacy `axis_parallel` -- `straight` excludes seams and `circular` cannot name
one -- and it replaces a generic E5 from the kernel with an actionable
sentence that names the two selectors to use instead.

**Ordering is geometric, not the kernel's.** Edges sort by their defining
point (a circle's centre, else the midpoint), axis coordinate first, then
radius; the backend's own index is the **final tie-break** and settles only
edges that are geometrically coincident, where no geometric key could
distinguish them anyway. A test resolves the same facts in reversed order and
gets the same answer. Nothing picks the first edge the kernel returned.

**No float is compared for equality.** Parallelism is `|dot|` within
`PARALLEL_TOLERANCE` of 1 (unsigned, which is Section C.7's rule); levels are
grouped within `LEVEL_TOLERANCE` millimetres.

### Why there are two build paths

A V1 CAD document carries exactly two edge selectors. A semantic one cannot
be written into one, and writing the nearest thing would be a silent
substitution -- `straight Z` is `axis_parallel Z` **minus the seam**, and that
difference is the whole stage. So the adapter now raises
`SelectorNotExpressible`, a third answer distinct from `ExecutionUnsupported`:
an unsupported operation means the engine is behind the language, this means
the **document format** is, and the part is perfectly buildable.

`build_plan` therefore asks one explicit question before building anything --
`plan_needs_executor(plan)` -- and takes one of two paths:

* **V1 document path** (unchanged): the adapter, then
  `CadApplicationService`, with its cache, isolation, exporters and artifacts.
  Every plan whose selectors Section C.7 can express, which is every plan the
  comparison instrument uses.
* **executor path** (`cad_experimental/executor.py`): walks Stage 46's
  `topological_order()`, holds one backend shape per live body, and calls
  `CadBackend`. For plans whose selectors are richer.

Neither is the other's fallback, and `PlanBuild.executed` says which ran. The
executor is also what finally **consumes** the deterministic order Stage 46
derived, and it introduces no second history: which body an operation touches,
what it declares and what it consumes all come from `plan_history`. The one
thing it adds is the backend shape per body, which history cannot hold.

### Graph and history integration

Unchanged and still load-bearing. A fillet or chamfer still depends on its
target's **state** through Stage 46's derived per-body edges, so an edge
operation after a hole executes after that hole -- which is exactly why
`describe_edges` sees the rim at all. A pattern's output is the body, so a
downstream selector names the body and finds every instance's rim: case 7
below chamfers four bolt-circle holes with one selector.

### Proved at the execution level

Real solids, volumes against closed forms with a relative tolerance of `1e-6`:

| # | Chain | Result |
|---|---|---|
| 1 | plate -> hole -> **fillet rim** | 2 edges, one solid |
| 2 | plate -> hole -> **chamfer rim** | closed form `2*pi*(R + d/3) * d^2/2` per rim, both rims |
| 3 | **top rim vs bottom rim** | different edges chosen; equal removal, since the plate is symmetric |
| 4 | **outer corners vs rim** | 4 straight vs 2 circular, disjoint sets |
| 5 | **seam never selected** | `straight` and `circular` never name the one seam |
| 5b | legacy `axis_parallel` | stops at `R2`, naming the operation and the two selectors to use |
| 6 | **two holes** | one top rim each, identical across runs |
| 7 | **pattern -> downstream selector** | four bolt-circle holes, one `circular/top` selector, closed form |

### Deliberate V1 limitations

- **`inner` and `outer` are not implemented.** The required cases do not need
  them: on a plate the outer edges are straight and the rims are circular, so
  curve type already separates them. A part with both an outer cylindrical
  face and a bore -- a tube -- would need it, and the measurement is recorded
  for when it is built: the two cylindrical faces carry opposite
  `TopAbs_Orientation`, and the bore's rims come back with the opposite sign
  to the outer rims'. That is one measurement on one shape and not yet a rule.
- **No face or vertex selectors**, and no named topology. There are still no
  persistent edge ids: a selector names a *kind* of edge, never a particular
  one, and nothing survives a rebuild.
- **`describe_edges` is CadQuery-only.** `FreeCadBackend` inherits the base
  method and therefore raises. That is a gap, not a fallback -- a backend that
  answered wrongly would be worse than one that says it cannot answer.
- **The executor produces a shape, a measurement and a render model**, not
  STEP, not a build cache and not artifact ids. Those live on the V1 document
  path and were not duplicated.
- **`position` has two values.** No `at`, no ordinal index, no nearest-to-a-
  point. Each would need a rule for what happens when several candidates tie,
  and the answer would have to be a guess.

### Still unmeasured

No model has been asked to produce a semantic selector. The prompt
(`2026-09-15.4`) now names the four kinds, says which to use for "round the
corners" and "break the edge of the hole", and warns that a seam exists
without naming a kernel -- but words in a prompt are a hypothesis.

## Measurement readiness, as of Stage 47

Stages 44-47 are complete, and **none of them has been put to a model.** This
section records what a live measurement would and would not tell you, so that
the next person does not run the wrong thing and believe the number.

### What is frozen and verified unchanged

Checked against the repository, not recalled:

| | |
|---|---|
| corpus fingerprint | `6e15d27042496c43`, identical to the recorded Stage 43 baseline |
| corpus version / size | `1.0.0`, 13 cases; `comparison_corpus.py` last touched at Stage 40's freeze commit |
| model | `claude-haiku-4-5-20251001` |
| scoring, parsers, validators | `representation_comparison.py` last touched at Stage 41; `comparison_diagnosis.py` at Stage 40 |
| preserved baselines | `docs/evaluation-baselines/` last touched by Stage 43's own commit |
| Stage 43's plan schema | `54759d1e16cfe634`, identical to the baseline's recorded fingerprint |

Stage 44 changed one line of `stage43_structured_comparison.py`: it pinned
`plan_schema_for_provider()` to `executable_schema()` rather than letting it
follow `provider_schema()`, **so that the module keeps sending what it sent**.
That is the opposite of a methodology change, and the fingerprint above is the
proof.

### What has deliberately moved

| | Stage 43 recorded | now |
|---|---|---|
| plan prompt | `2026-09-10.7` / `5ef08dd09893b689` | `2026-09-15.4` / `dd833a30f90f269f` |
| plan vocabulary | 9 types, 6 executable | 10 types, 7 executable |
| `provider_schema()` | 6 types, 6 branches | 10 types, 8 branches |
| selectors | `all`, `axis_parallel` | those two plus `straight`, `circular` |

### Why the existing harness cannot measure this

**`stage43_structured_comparison --live` would send the old six-type grammar
with the new prompt.** The prompt now tells the model to use `pattern`,
`straight`, `circular` and sketch-to-extrude chains; the pinned schema admits
none of them. That is precisely Stage 43's own failure -- a grammar that
cannot express the answer the prompt asks for -- repeated in a worse form,
and it would score *below* Stage 43 for reasons that say nothing about the
representation.

**The corpus does not exercise the new capability either.** Its 13 cases are
Stage 40's: no pattern, no semantic selector, no multi-feature chain. Even
with the right schema, it would measure the six operations that were already
measured.

**And the open schema question is not reachable from a CLI.** Stage 44 left
"does the provider compile the widened schema?" unanswered, needing one live
call. `--probe-live` probes `schema_for(arm)`, which is `executable_schema()`
-- already known-accepted. Nothing sends `provider_schema()`.

### What the next stage therefore is

Not a run: **a Stage 48 harness**, which should

1. send `provider_schema()` (falling back to `compact_provider_schema()` only
   if the provider refuses it, explicitly and recorded -- never silently);
2. reuse Stage 40's frozen corpus, scoring, attempts-per-case, model, token
   limit and timeout **unchanged**, so the two runs remain comparable on the
   axes that did not move;
3. extend the corpus, as its own deliberate step and in its own commit, with
   cases that actually exercise a pattern, a semantic selector and a chain --
   authored before any score is seen, never adjusted after.

Until then the honest statement is: **Stages 44-47 are built, tested and
unmeasured.**

## What is NOT known

**Real Anthropic testing happened at Stage 40, and only there.** Stages 33 to
39 were developed and tested entirely against the local development provider,
which supplies developer-written plans and calls no model. No result in this
document *outside the Stage 40 section* is a Claude result of any kind, and
the fixtures and tests remain stub-driven.

**One model, one run, 13 cases.** The Stage 40 numbers are Claude Haiku 4.5
only. Nothing is known about any other model, and the corpus is a seventh the
size of production's 35-case corpus.

**Nothing is known about how a model handles the new vocabulary.** Six
operations were added across Stages 33–38 and not one of them has been put to
a real model. *Superseded in part by Stages 40 and 43 for the six executable
operations; still true of `sketch`, `extrude` and `revolve`, whose Stage 43
refusals were forced by the schema rather than chosen — see Stage 44.* In particular it is unmeasured whether a model offers a sketch
where a `box` would do, whether it gets `P24`/`P25` plane compatibility right,
and whether it writes dimensional constraints that agree with their geometry —
all three are things the prompt now spends words on, and words in a prompt are
a hypothesis, not a result.

**Nothing is known about how an unbuildable-but-valid plan reads to a user.**
The 501 answer and the page's "not executable here" are tested as mechanism.
Whether they are *understood* has not been observed with anyone.

**No live measurement exists.** `ANTHROPIC_API_KEY` is not available in the
environment this was built in, so the five cases have not been put to real
Claude Haiku 4.5. The harness runs, its plumbing is proven against a stub, and
a stub result says nothing whatever about model quality.

So the stage's central question — is the operation plan easier for a model to
get right than the V1 document? — **remains open**. Answering it needs the live
harness run, and then a comparison against the stable path's own numbers on
equivalent prompts.

---

## Stage 48: an evaluation instrument for what Stages 44–47 built

**This stage measures nothing yet.** It is the instrument, and saying so is
the point: Stage 43's numbers are the only live comparison this branch has,
and the reason a new instrument was needed is that re-running Stage 43 would
have produced numbers that looked like a measurement and were not.

### Why Stage 43 cannot answer the current question

Stage 43 is pinned to `executable_schema()` — six operation types, six
branches, **no `sketch`, `extrude` or `revolve` branch**, and the two V1 edge
selectors. That pin is deliberate and a test asserts its fingerprint
(`54759d1e16cfe634`): a recorded result must stay attributable to the
instrument that produced it.

Since then the language grew. Stage 44 widened `provider_schema()` to all ten
types in eight branches and established that Stage 43's 5/5 profile refusals
were **forced by the grammar rather than chosen**. Stage 45 exposed the
history graph, Stage 46 added `pattern`, Stage 47 added `straight`,
`circular` and `position`.

So running `stage43_structured_comparison --live` today would send the **old
six-type grammar** with the **current prompt**, which tells the model to use
operations that grammar forbids. That is Stage 44's finding repeated on
purpose. Stage 43 therefore stays exactly as it is, and Stage 48 is a
separate module, corpus, version and output directory.

### A prompt contradiction found while building the corpus

Stage 46 added `pattern` as a full operation — documented in its own prompt
section, executable, in `EXECUTABLE_TYPES`, in eight-branch
`provider_schema()` — and **left `patterns` standing in the prompt's
unsupported list**:

```
...sweeps along a path, helical sweeps and lofts -- an extrude and a revolve
are NOT in this list, they are operations this language has;
patterns; mirrors; assemblies;
```

The prompt's `## pattern` section says *"Use a pattern whenever a description
says several of the same feature arranged in a regular way"*, and forty lines
later its refusal list says to decline one. Every pattern case in a Stage 48
run would plausibly have come back `unsupported`, and the run would have
recorded a **prompt contradiction as the model's judgement** — exactly the
Stage 44 mistake, in a new place.

Prompt `2026-09-15.5` removes the word and adds the same explicit carve-out
the `extrude`/`revolve` entry already carries:

> A `pattern` is NOT in that list either. Repeating one feature at several
> places is an operation this language has, so a bolt circle, a row of holes
> or any other regular repetition is a plan and never a refusal.

Found before any run, by reading the prompt against the vocabulary rather
than by seeing a score. The version pin in `test_extrude_revolve` moved with
it, as its own comment requires.

**A test was asserting the contradiction.** `test_sketch.py`'s
`test_the_sweeps_beyond_this_stage_are_still_unsupported` was written at
Stage 38, when `pattern` genuinely did not exist, and it pinned `patterns` as
*required* to be in the refusal list. Stage 46 added the operation and did
not update it — so the suite was actively holding the contradiction in place,
which is how it survived four stages. It now asserts the correct state, and a
mirror test (`test_the_prompt_no_longer_calls_a_pattern_unsupported`) asserts
the carve-out is present, exactly as the sketch pair above it does.

The general lesson, again: **when an operation is added, the refusal list is
part of the operation — and so is the test that pins it.** Stage 44 said a
schema is what the model may say; this says a prompt's negative space is too,
and that a guard test written against an older vocabulary can keep an error
alive rather than catch it.

### Two groups, never one number

| Group | Cases | Arms | Why |
|---|---:|---|---|
| `legacy` | 13 | V1 **and** plan | The Stage 40 requests. Both vocabularies can express them, so both are asked |
| `capability` | 17 | plan only | Sketch chains, `pattern`, semantic selectors, deeper graphs. V1 has none of them |

The legacy cases are **read out of the frozen `comparison_corpus` object at
import** — text, both expectations, geometry and both required-type tuples —
rather than retyped, and `_group_check()` plus a test assert
character-for-character identity. "Legitimately comparable" is therefore a
checkable property, not a claim in a docstring.

`summarise()` deliberately produces no combined rate, and a test asserts the
absence of an `overall` or `combined` key. One number over both groups would
be a V1 score diluted by seventeen cases V1 was never asked.

### What broke apples-to-apples with Stage 43, and why that is fine

Three things moved at once, and **any one of them is enough**:

1. **The schema.** `executable_schema()` → `provider_schema()`:
   `54759d1e16cfe634` → `be8ba82740aecc1d`, six branches → eight, six types →
   ten.
2. **The prompt.** `2026-09-10.7` → `2026-09-15.5`. Four stages of changes.
3. **The scoring path for a semantic selector** — the subtle one. Stage 40's
   `run_plan_attempt` calls `plan_to_document` directly, so a plan using
   `straight` or `circular` raises `SelectorNotExpressible` and lands in its
   generic `except Exception` arm as `SEMANTICALLY_INCORRECT`. That was right
   when no such selector existed. Today it scores a **correct, buildable**
   answer as wrong — and legacy cases `07` and `08` ("chamfer/fillet the
   vertical edges") are precisely where prompt `2026-09-15.4` onwards steers
   the model towards one.

Stage 48 therefore builds through `build_plan`, which asks
`plan_needs_executor(plan)` **before** building and sends such a plan to the
graph executor. A test pins both halves: the F1 reference plan scores `OK`
through Stage 48, and `plan_to_document` on that very plan raises.

**So a Stage 48 legacy number is not a Stage 43 number that moved.** It is a
fresh measurement whose internal V1-vs-plan comparison is fair. Every result
carries `relationship_to_stage_43` stating this in the JSON itself, for a
reader who finds the file without the docs.

### The corpus: 30 cases, nine categories, five expected classes

`stage48_corpus.py`, version `1.0.0`, fingerprint `96517cb979b4660a`.

| | A | B | C | D | E | F | G | H | I |
|---|---|---|---|---|---|---|---|---|---|
| | primitives | modifiers | profiles | chains | pattern | selectors | graph | unsupported | invalid |
| cases | 3 | 6 | 2 | 3 | 2 | 4 | 3 | 4 | 3 |

Five expected classes, each carried explicitly by every case. Three are Stage
40's own constants, **imported not redefined**, so a legacy case means what
it meant there:

- `build` (19) — buildable geometry matching a closed form;
- `valid_unexecutable` (4) — a valid plan this engine refuses;
- `unsupported` (4) — the word `unsupported`;
- `clarification` (2) — **new** — a required value is absent and has no
  default, so the only correct answer is to ask;
- `no_part` (1) — **new** — the request names a part that cannot exist (a
  200 mm hole through a 100 mm plate). Nothing is missing and the vocabulary
  can say it, so neither of the other two fits. Either refusal word is
  accepted, because the language gives no basis to prefer one; producing a
  plan is always wrong.

Two cases are worth understanding in detail.

**`F2` and `F3` — the top rim and the bottom rim.** A 1 mm chamfer on either
end of a Ø20 hole through a 10 mm plate removes `2π(R + d/3)·d²/2` — **the
same volume to the last bit**. Geometry alone cannot tell the two requests
apart, so a run that scored both correct on volume would have proved nothing.
They are scored by reading the selector the model wrote, which is what
`SelectorExpectation` and the `selector_correct` metric are for. A test
feeds `F2` the `F3` reference plan and asserts `WRONG_SELECTOR`.

**`F1` — "round the four outside corners" on a drilled plate.** The Stage 47
root cause, as a request: the bore's seam is a genuine Z-parallel straight
edge, so `axis_parallel Z` selects it and the whole fillet fails with E5.
Only `straight` builds this, which is why it is the single accepted selector
— and why the case is also the clearest demonstration that Stage 40's runner
could not have scored this corpus.

### Reference plans, and why they are not an answer key

Every buildable case carries a **reference plan**: a developer-written plan
that is *one* correct answer to that request. The preflight parses,
validates and **builds all nineteen with the real kernel** and checks each
against its closed form. Seven go through the graph executor.

That check caught nothing, which is the point — it was run before the
expectations were committed, and all nineteen closed forms agreed with
CadQuery to within 1 ULP. Had one disagreed, every attempt on that case
would have been scored incorrect and read as a model failure.

A reference plan **never reaches the model**: not in the prompt, not in the
schema, not in any request. The only other thing that reads one is
`ReferencePlanStub`, the `--self-check` provider, which is stamped
`is_local_development = True`, produces a result stamped
`is_live_model_result: false`, and is unreachable from `--live` — that path
builds `real_model()` and nothing else.

### Invalid plans: asked of the rules, not of a model

The brief's "bad references, incompatible references, ambiguous selector,
unsupported geometry semantics" are properties of a **plan**, not of a
request: no wording makes a model emit a dangling reference on demand.
Measuring them through a model would measure something else and call it
this.

They are checked offline instead — nine deliberately broken plans, each with
the layer and the rule code that must refuse it. Every code below was
**observed from the validator**, not recalled:

| Fixture | Refused by | Codes | Kind |
|---|---|---|---|
| `bad-reference` | validator | `P9` | bad reference |
| `forward-reference` | validator | `P10` | bad reference |
| `consumed-reference` | validator | `P12` | incompatible reference |
| `pattern-of-a-solid` | validator | `P27` | incompatible reference |
| `self-referencing-subtract` | validator | `P10`, `P31` | cycle |
| `radial-pattern-off-axis` | validator | `P29` | unsupported geometry semantics |
| `pattern-count-of-one` | validator | `P28` | unsupported geometry semantics |
| `selector-position-without-axis` | **parser** | — | ambiguous selector |
| `selector-straight-without-axis` | **parser** | — | ambiguous selector |

They contribute to no score and appear in no rate.

### Scoring, and the three new metrics

Every Stage 40 metric keeps its meaning, and `SCORING_RULES` states each one
in words rather than leaving it implicit in the code. `scoring_fingerprint()`
hashes that table together with the tolerances, the success categories and
the accepted-refusal map, so **changing what a metric means changes the
fingerprint** — a test proves it by mutating one definition and checking the
hash moves.

Four metrics are new, and marked as new in the table itself:

- `correct_valid_unexecutable` — Stage 40 had the *category*, never the rate;
- `correct_clarification` — over the cases where a required value is absent;
- `selector_correct` — over the cases that name a kind of edge only;
- `executed_by_graph` — a **count, not a rate**, and not a quality signal.
  It is the evidence that a run measured something Stage 43 could not.

Three new codes, none reachable by a Stage 40 or Stage 43 record, so no
existing category changed meaning to make room: `WRONG_SELECTOR` (right part,
wrong kind of edge), `INVENTED_MISSING_VALUE` (a plan where the request left a
required value out) and `CORRECT_CLARIFICATION` (asking was right and the
model asked — a **success**).

One inherited metric has its **denominator** stated rather than changed:
`render_success` is over document-path attempts only, because a
graph-executed plan has no V1 document and so no RenderModel *by design*.
Counting those as failures would report Stage 47's capability as a defect.

`INVENTED_MISSING_VALUE` is kept apart from `WRONGLY_ANSWERED` because
inventing a dimension is a different failure from confidently answering an
impossible request, and a run that folded them would lose which one happened.
`CORRECT_CLARIFICATION` is kept apart from `OK` for the mirror reason: `OK`
means *built the part that was asked for*, and reporting a question as a
built part is the same conflation in the other direction.
`STAGE48_SUCCESS_CATEGORIES` is Stage 40's tuple plus that one; **Stage 40's
own tuple is not widened**, because widening it would change what a Stage 40
record's categories mean.

**A provider error stays out of every denominator.** Stage 40's `_rate`
rule, its implementation, and a test.

### Baselines cannot be overwritten

`BASELINE_DIGESTS` records the SHA-256 of all seven Stage 40 and Stage 43
files. The preflight checks them, `run()` raises `BaselineMissing` if one is
absent or changed, `_write()` refuses any path inside
`stage40-v1-vs-operation-plan/` or `stage43-structured-output/`, and the CLI
refuses such a `--out` before doing anything at all. Four tests cover it,
including one that mutates a digest and asserts the run stops.

`BaselineMissing` is deliberately not `SchemaNotCompilable` and not
`CredentialUnavailable`: those say the run would not measure what it claims,
and that it cannot be paid for. This says the run might destroy evidence that
cannot be regenerated.

### Measured offline, on this branch

```
STAGE 48 PREFLIGHT (offline; no model call)
  v1_json          fingerprint ce2083a78e84c6d5   8 optional   worst 4   COMPILABLE
  operation_plan   fingerprint be8ba82740aecc1d  16 optional   worst 4   8 branches   COMPILABLE
  legacy cases              13   (text identical to the frozen Stage 40 corpus)
  capability cases          17
  beyond Stage 43's reach   circular, extrude, pattern, revolve, sketch, straight
  reference plans built     19   (7 by the graph executor)   none disagreeing
  invalid-plan fixtures      9   none mis-refused
  protected baselines        7   all present and unchanged
  READY: True
```

`--self-check` then runs the entire loop against the reference plans and
scores **19/19**, with the graph executor on 7 of them. That is a statement
about this harness and **says nothing about any model.**

### What is NOT known after Stage 48

- **No Stage 48 number exists.** Nothing here has been put to a model.
- **The provider has still not been asked to compile the widened schema.**
  Every offline limit is satisfied and asserted, but compiled-grammar size
  can only be measured by sending it. `--probe-live` exists to ask (three
  calls), and has not been run. `--plan-schema compact` is the documented
  fallback if it is refused; nothing selects it automatically.
- **Whether prompt `2026-09-15.5` actually reaches a pattern is untested.**
  Removing the contradiction makes the answer *reachable*; whether the model
  takes it is exactly what the E and F4 cases are for.
- **The corpus exercises each capability at least once, not a distribution.**
  Thirty cases is a probe, not a survey, and the `no_part` class has one
  case.
- **FreeCAD is not exercised**, and a second-backend comparison is its own
  stage.

## Stage 49: the provider's compiled-grammar ceiling

Stage 48 could not run. The provider refused **both** plan schemas, and said
why in its own words, on all four plan requests of the five-call diagnostic
probe:

> `400 invalid_request_error` — *"The compiled grammar is too large, which
> would cause performance issues. Simplify your tool schemas or reduce the
> number of strict tools."*

The `v1_json` control was accepted in the same run, on the same model and
credential, which rules out availability, authentication, transport and
outage; two different descriptions failed identically on each schema, which
rules out the request text. **The documented `--plan-schema compact` fallback
was refused too** — that is the new fact, and it turns a flag problem into a
schema problem.

### Serialized size is only a proxy

The limit is on the **compiled** grammar, not the payload. Stage 41 measured
that a `$ref` does not shrink it and that unused `$defs` still cost budget,
so the honest proxy is the schema with every `$ref` **inlined**: a definition
referenced five times is compiled five times. Measuring the referenced form
flatters a schema in exactly the way that led to sending two grammars the
provider could not compile — `compact` is 19% smaller than `provider` in
bytes and was refused just the same.

Measured bounds, inlined: **accepted at 3622**, **refused at 6190**. The
ceiling lies in between and nothing narrower is known.

### Where the grammar actually goes

Marginal cost of each capability, against the six-type base:

| capability added | inlined | nodes |
|---|---:|---:|
| `sketch` | **+2653** | +73 |
| `pattern` | +1014 | +24 |
| `extrude` | +459 | +9 |
| `revolve` | +455 | +9 |
| semantic edge selectors | +150 | +2 |
| *(of which sketch's `constraints`)* | *+1161* | *+33* |

**`sketch` is 73% of the growth** from the accepted grammar to the full
vocabulary, and its `constraints` are 44% of `sketch`. Stage 46's `pattern`
and Stage 47's selectors are nearly free by comparison — the selectors cost
about 150 inlined characters, roughly 2% of the budget.

Sketch's cost is structural, not incidental: `sketch_defs` contributes three
geometry branches and five constraint branches, and each is inlined wherever
referenced.

### The ladder

`cad_experimental.schema_ladder` builds variants between the two measured
points. Every variant is produced by the canonical plan's own
`_plan_document` through its existing knobs — **no operation is redefined and
nothing is written back into `plan.py`.**

| variant | inlined | branches | capabilities | prediction |
|---|---:|---:|---|---|
| `L0-executable` | 3622 | 6 | the V1 six | at/below accepted |
| `L1-sketch` | 6275 | 7 | + sketch | **at/above refused** |
| `L2-extrude` | 6734 | 8 | + extrude | at/above refused |
| `L3-revolve` | 7189 | 9 | + revolve | at/above refused |
| `L4-pattern` | 8203 | 10 | + pattern | at/above refused |
| `L5-selectors` | 8353 | 10 | + semantic selectors | at/above refused |
| `C1-merged` | 7351 | 8 | all ten | at/above refused |
| `C2-no-constraints` | 6190 | 8 | all ten | **refused (measured)** |
| `C3-lean-sketch` | 6115 | 8 | all ten | unknown |
| `C4-profiles-no-sketch` | 4698 | 7 | nine, no sketch | unknown |
| `C5-minimal-profiles` | 5010 | 7 | eight | unknown |
| `C6-sketch-floor` | 4551 | 6 | seven | unknown |

`L0` reproduces `executable_schema()` exactly and `C2` reproduces
`compact_provider_schema()` exactly, both asserted by tests — so the metric
is anchored to the two points the provider has actually ruled on.

**The ladder's headline is that `L1` is already above the refused point.** A
full-fidelity sketch cannot be bought at any rung. Only three variants land
in the unknown band, and only two of those carry a sketch at all.

### Canonical IR versus provider encoding

The provider schema is an **encoding** of the operation plan, not the plan.
It says what a model may *say*; the parser and validator decide what a plan
*means*, and they never see the schema. That is what makes a narrow encoding
safe: omitting a sketch's `constraints` from a grammar does not make
constraints illegal — a plan carrying them still parses, which a test
asserts. Expressiveness is lost; rigour is not.

This matters beyond one provider. A different model with a different
structured-output limit needs a different encoding, and none of them should
reach back into the IR. Do **not** redesign the operation plan to fit a
grammar budget.

### Commands

```powershell
# offline: measure every variant, call nothing
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.schema_ladder --list

# one variant, ONE real call. There is deliberately no --probe-all.
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.schema_ladder `
    --probe C5-minimal-profiles --out "<local-scratch>\probe.json"
```

A live probe needs `CAD_ANTHROPIC_API_KEY` exported into that process (§7).
The probe writes only where asked and refuses any path inside a protected
baseline directory.

### What is not known

- **Where the ceiling is.** Two points bound it; the band between them is
  unmeasured, and no variant in that band has been sent.
- **Whether the limit tracks characters, nodes or alternatives.** All are
  reported because a proxy that correlated once is not a model of the limit.
- **Whether a lean sketch is affordable at all.** `C6-sketch-floor` (4551) is
  the cheapest grammar that can express a sketch; if it is refused, no
  sketch-carrying grammar fits and the question becomes whether profiles can
  be reached another way.

## Stage 51: provider encodings, made explicit and measured

Stage 50 located the ceiling by probing. Stage 51 turns that result into
architecture: the shapes that compile are now **named encodings**, chosen
explicitly and recorded, and the corpus partition is derived from what each
encoding can actually express.

### The evidence, in full

Every row is a real call to `claude-haiku-4-5-20251001`. Sizes are
ref-inlined characters, because the compiler inlines.

| encoding | inlined | capabilities | result |
|---|---:|---|---|
| `profile` | 3487 | box, cylinder, sketch, extrude, revolve | **ACCEPTED** |
| `executable` (Stage 43) | 3622 | the V1 six | **ACCEPTED** |
| `profile_hole` | 4030 | box, cylinder, through_hole, sketch, extrude | **ACCEPTED** |
| `profile_union` | 4481 | + subtract, + revolve — seven types | **ACCEPTED** |
| `C6-sketch-floor` | 4551 | the six + sketch | REFUSED |
| `C4-profiles-no-sketch` | 4698 | nine types, **no sketch** | REFUSED |
| `compact` | 6190 | all ten | REFUSED |
| `provider` | 7351 | all ten | REFUSED |

**The ceiling lies in (4481, 4551].** It is a bound, not a number: it was
never published, it may move, and it must not be written down as though it
were exact.

**The blocker is total compiled grammar size, not any one operation.** `C4`
carries no sketch at all and was refused at 4698, while `profile_union`
carries a sketch and was accepted at 4481. What crossed the ceiling in
Stage 48 was the six-type solid base consuming the budget a sketch needed —
spend it the other way round and a profile pipeline fits comfortably.

### The encodings

`plan.py` gains three functions, all built from the canonical
`_plan_document` rather than duplicating any operation definition:

- `profile_provider_schema()` — the smallest proven profile pipeline
- `profile_hole_provider_schema()` — kept because it is separately proven
- `profile_union_provider_schema()` — **the one to reach for**; it dominates
  both on capability and is proven on the same evidence

All three omit a sketch's `constraints`, which is the largest single saving
and costs nothing a solid needs: constraints are *checked*, never solved
(rule P21), so they can only restate geometry already written at the size it
means.

None can express `fillet`, `chamfer` or `pattern`. That is a grammar budget,
not a judgement — adding the edge pair measures 4741, past the refusal.

### Selection is explicit, and nothing falls back

`PLAN_SCHEMAS` now carries `profile`, `profile_hole` and `profile_union`
alongside the historical names. `DEFAULT_PLAN_SCHEMA` **stays `provider`**
even though `provider` is refused: changing it silently would rewrite what
earlier runs meant, and a caller who got a different grammar than it asked
for cannot read its own numbers. A caller names an encoding; the name is
recorded in the result; nothing substitutes.

`SCHEMA_CAPABILITIES`, `PROVEN_COMPILABLE` and `PROVEN_REFUSED` state what is
measured rather than inferred, and `schema_can_express` /
`encodings_that_can_express` answer *could this case even be asked here* —
the question that must be settled before any result is scored.

### The corpus partition, derived not guessed

Of 30 cases: 8 expect no plan at all (refusals, clarifications) and are
encoding-independent. Of the 22 that expect a plan:

| home | cases |
|---|---:|
| `profile_union` | 10 |
| `executable` | 8 (every `fillet`/`chamfer` case) |
| **no proven encoding** | **4** |

The four with no home are `D3-profile-revolve-fillet` (a sketch *and* a
fillet), `E1-bolt-circle-radial`, `E2-hole-row-linear` and
`F4-patterned-rims-chamfered` (all need `pattern`). No grammar that compiles
has a word for them.

**Two instruments, never one score.** `profile_union` and `executable` have
different capability envelopes, so their results are not commensurable and
must not be added together. The four homeless cases must be reported as
*not expressible under any proven encoding* — never as model refusals. That
distinction is the whole lesson of Stage 43, where a missing grammar branch
was scored as the model's judgement.

### Architecture rule: canonical IR ≠ provider encoding

    canonical IR        one language, one meaning        plan.py
          ↓
    provider encoding   many representations, each        provider schemas
                        constrained by a grammar limit
          ↓
    parser / validator  one authoritative interpretation  parser.py
          ↓
    executor            unchanged

An encoding narrows what a model may **say**. It never narrows what a plan
**means**: the parser never sees a schema, and an operation absent from an
encoding is still legal, still parsed and still validated. Tests assert
exactly that for `pattern` and for sketch `constraints`.

**Never redesign the operation plan to fit a grammar compiler.** A different
provider will have a different limit and needs a different encoding, not a
different language.

## Stage 52: the first real capability evaluation

Stage 48 could not run at all: the provider refused its grammar. Stage 50/51
found and proved smaller encodings. Stage 52 is the first measurement made
with them — **270 live calls** to `claude-haiku-4-5-20251001`, structured
output on, no fence stripping, no retry, no repair, no fallback.

### Three passes, not one score

| Pass | Encoding | Arm | Cases | Calls |
|---|---|---|---:|---:|
| A | `profile_union` (4481, `a5c3484f…`) | plan | 18 | 90 |
| B | `executable` (3622, `54759d1e…`) | plan | 23 | 115 |
| C | — (V1 schema) | v1_json | 13 | 65 |

The V1 arm is independent of the plan encoding, so it was run **once**, not
per instrument. Cases whose operations an encoding cannot name were **not
called**: 12 under A, 7 under B, recorded as `NOT_EXPRESSIBLE_BY_ENCODING`
with the missing operations named. A question a grammar cannot phrase is not
a question the model declined.

### The headline finding is a flaw in the partition, not in the model

The partition was derived from `required_plan_operations` — operation *types*
only. It did not check **selector modes**. Both proven encodings pin
`V1_SELECT_MODES` (`all`, `axis_parallel`); seven corpus cases need Stage 47's
`straight` or `circular` selectors, which no proven encoding carries.

All 26 of pass B's `BUILD_FAILED` are exactly those cases. The model,
unable to say "the top rim of the hole", said `select: "all"` — chamfering
every edge of the plate, which fails geometrically. **That is the encoding's
limit being recorded as a build failure**, the precise error this project has
been trying not to repeat since Stage 43.

Correcting for it changes the reading completely:

| Pass B metric | raw (115) | corrected (85) |
|---|---|---|
| validation | 73.9% | 64.7% |
| build success | 47.0% | **58.8%** |
| **semantic correctness** | 71.3% | **94.1%** |

Every `BUILD_FAILED` and `SEMANTICALLY_INCORRECT` in B was a selector
artefact. None survives the correction.

### Results

**Instrument A — `profile_union`, the profile question.** 90 records:
`OK` 36, `CORRECT_UNSUPPORTED` 25, `CORRECT_VALID_UNEXECUTABLE` 11,
`CORRECT_CLARIFICATION` 5, `WRONGLY_REFUSED` 4, `BUILD_FAILED` 4,
`PLAN_VALIDATION_REJECTED` 3, `INVENTED_MISSING_VALUE` 2. Model output valid
100%, structure valid 100%, **semantic correctness 85.6%**, build 40.0%.

Build success is low *by construction*: 11 of the records are
`CORRECT_VALID_UNEXECUTABLE` — sketch chains that validate and are then
refused at the execution boundary by design, and are scored correct.

**Instrument B — `executable`, the solid question.** Corrected:
**semantic correctness 94.1%**, build 58.8%, `OK` 50, `CORRECT_UNSUPPORTED`
25, `CORRECT_CLARIFICATION` 5, `INVENTED_MISSING_VALUE` 5.

**Pass C — V1 arm.** 65 records, semantic correctness 60.0%, build 21.5%,
`WRONGLY_REFUSED` **25** — V1 refused every modifier case (`04`, `05`, `06`,
`07`, `08`, 5/5 each). This reproduces Stage 43's V1 failure mode exactly:
V1's weakness is refusal, not error.

### Geometry is real

Every build carries measured evidence: 36 / 54 / 14 successful builds, each
with a volume, a solid count, a triangle count and a bounding box, all
rendered. `01-plate-worded` measures 60000.0 mm³, 1 solid, bbox 100×60×10 —
closed-form exact. Backend is CadQuery, unchanged; FreeCAD was not involved.

### What Haiku can actually do

**Under a profile grammar:** it writes sketches, extrudes and revolves
competently — 85.6% semantically correct, 100% structurally valid output,
zero malformed answers. Its residual errors are interesting rather than
sloppy: it tried to extrude a solid (`P23`, caught by the validator) and it
refused `10-profile-revolve` 4/5 as unsupported, which is the known
deliberately-unfixed prompt wording telling it an extrude "cannot be built".

**Under the solid grammar:** 94.1% semantically correct on what the encoding
can express. The model is not the bottleneck at this vocabulary size.

### What remains outside the envelope

- **Stage 47's semantic selectors are entirely unmeasured.** They cost only
  ~150 inlined characters, but `profile_union` is at 4481 against a ceiling
  of ≤4551, so there is no room. A smaller encoding — `profile` at 3487 plus
  selectors ≈ 3637 — would fit, and that is the obvious next instrument.
- **`pattern` is unreachable** in every proven encoding.
- **`fillet`/`chamfer` cannot coexist with a sketch** in one grammar.
- `executed_by_graph` is 0 everywhere, necessarily: the graph executor is
  reached only by selectors richer than V1 can carry, and no proven encoding
  carries one.

## Stage 53: a selector-capable encoding, and the partition bug it exposed

Stage 52's central defect was that expressibility was decided on operation
**types** alone. Seven cases needing a `straight` or `circular` selector
looked answerable under a grammar that had neither; the model answered
`select: "all"`, chamfered every edge of the plate, and the kernel failed.
Twenty-six of those failures were recorded against the model.

### The Stage 52 proposal was wrong, and measurably so

Stage 52 suggested adding selectors to `profile_provider_schema()` for about
+150 inlined characters. Building it produced a **byte-identical schema** —
same fingerprint, +0 characters. The reason is structural: **a profile
encoding has nothing that selects an edge.** Only `fillet` and `chamfer`
carry an `edges` selector, and the profile encoding has neither, so
`_prune_defs` drops the selector definition and widening the modes changes
nothing. A test now pins this so the idea cannot be re-proposed.

A selector-capable grammar must therefore contain a selector-carrying
operation.

### `selector_provider_schema()` — proven

| | |
|---|---|
| Capabilities | box, cylinder, through_hole, subtract, fillet, chamfer |
| Selectors | **all, axis_parallel, straight, circular + position top/bottom** |
| Inlined | **3134** — *smaller* than `executable` (3622) |
| Fingerprint | `893a912002fb6593` |
| Live | **ACCEPTED** |

It is smaller than `executable` despite carrying strictly more, because
merging `fillet`/`chamfer` into one branch saves several times what the
wider selector costs.

### Expressibility now checks both dimensions

`SCHEMA_SELECTOR_MODES`, `schema_supports_position`, `selector_requirement`
and `case_expressibility` decide a case on **operations and selectors**, and
report the two separately — "the grammar has no chamfer" and "the grammar has
no circular selector" are different facts. A profile encoding's selector
entry is `()`, not V1's: it has no selector at all.

### The measurement: 30 calls, six cases

| Metric | Result |
|---|---|
| model output valid / structure valid / validation | **100%** |
| **build success** | **30/30 = 100%** |
| `executed_by_graph` | **30/30 — the graph executor reached for the first time** |
| selector_correct | 20/30 = 66.7% |
| semantic correctness | 13/30 = 43.3% |

**Build success went from 4/30 to 30/30** on the same six cases. Every one of
Stage 52's "build failures" was the encoding, not the model.

`render_success` is 0/30 and that is **by design**: a graph-executed plan has
no V1 document and therefore no RenderModel, which is exactly why the metric
is scored over document-path attempts only.

### What Haiku actually gets wrong, now that it can speak

| Case | OK | selector |
|---|---|---|
| `F1-drilled-plate-round-corners` | 5/5 | 5/5 |
| `G1-subtract-then-chamfer` | 5/5 | 5/5 |
| `D1-plate-hole-chamfer-long-edges` | 2/5 | 5/5 |
| `G2-two-holes-then-chamfer` | 1/5 | 5/5 |
| `F2-hole-rim-chamfer-top` | 0/5 | **0/5** |
| `F3-hole-rim-chamfer-bottom` | 0/5 | **0/5** |

Two distinct, precise failure modes — both genuinely the model's, both
invisible before this stage:

**It reaches for `circular` but never disambiguates the rim.** On `F2` and
`F3` it wrote `{"select": "circular", "axis": "Z", "position": null}` every
single time. Omitting `position` chamfers *both* rims: volume 56793.48
against an expected 56825.94. Note that top and bottom alone are
volumetrically identical, so this is caught only because chamfering both
removes more material than chamfering one — topological evidence, not volume
equality.

**It picks the wrong axis for "long edges".** On `D1` and `G2` the failures
are `{"select": "straight", "axis": "Z"}` where the correct answer is
`axis: "X"`; the passing attempts on the same cases use `X`. `selector_correct`
scores these **true** because it compares the mode and not the axis — so that
metric is a partial signal and should not be read as selector accuracy.

### What remains outside the envelope

`sketch`, `extrude`, `revolve` and `pattern` are absent from this encoding —
the six solid types plus profiles plus full selectors measures 5176, past the
4551 refusal. `F4-patterned-rims-chamfered` needs `pattern` and remains
inexpressible under every proven encoding; its selector, notably, is fine.

## Stage 54: decomposing the selector failures, and one measured fix

Stage 53 reported `selector_correct` 20/30 and semantic correctness 13/30.
Decomposing those 30 records shows the single metric was hiding two entirely
different errors, and that one of its components was vacuous.

### The decomposition

| component | Stage 53 |
|---|---|
| `selector_mode_correct` | **30/30** |
| `selector_axis_correct` | 30/30 — **vacuous**: the corpus pins no axis |
| `selector_position_correct` | 20/30 |
| `selector_fully_correct` | 20/30 |

The mode was never wrong. The whole gap is two disjoint failures:

- **`position` omitted** — F2/F3, 10 records. Every attempt wrote
  `{"select": "circular", "axis": "Z", "position": null}`.
- **wrong axis** — D1/G2, 7 records. `axis: "Z"` where "the four long edges"
  of a 100×60×10 plate run along X.

10 + 7 = 17, and 30 − 17 = 13 semantically correct. Nothing else is
happening. Note that **axis is scored by geometry, not by the selector
expectation** — the corpus does not pin one, which is why Stage 53's
"axis correct 30/30" meant nothing.

### The two prompt causes

**Axis:** no rule said what a selector's axis *is*. The only worked example
mapped a phrase to `Z`, and the model defaulted to `Z`.

**Position:** the prompt contradicted itself. Its clarification section said
*"Do not ask about `position` or `axis`: those are optional, and omitting
them is the correct answer when the description is silent."* F2/F3 are not
silent — they say "the top edge" — but the model generalised "optional,
omitting is correct" past the silent case, overriding the correct worked
example a hundred lines earlier.

### The change: prompt `2026-09-16.1` (`a1f9991c7327fb9d`)

One edit to how selector parameters are read off the request, with two
clauses that **disjoint cases exercise**, so the A/B stays attributable:

- *WHICH AXIS* — an edge's axis is the direction the edge runs, not a face
  normal; on an `x`×`y`×`z` box the long edges run along whichever of `x`
  and `y` is larger, worked through for a 100×60×10 plate.
- *WHICH END* — a named end is an instruction, and omitting `position`
  chamfers **both** rims, which is a different part.

The clarification rule was narrowed to the silent case it was meant for.

### A/B: 24 calls, 6 cases, 2 attempts, same model and schema

| | A `2026-09-15.5` | B `2026-09-16.1` |
|---|---|---|
| build success | 12/12 | 12/12 |
| `executed_by_graph` | 12/12 | 12/12 |
| selector mode | 12/12 | 12/12 |
| **selector axis** | 11/12 | **12/12** |
| **selector position** | 8/12 | **8/12 — unchanged** |
| **semantic correctness** | 6/12 | **8/12** |

Per case, D1 went 1/2 → **2/2** and G2 1/2 → **2/2**. F2 and F3 stayed
**0/2**.

### The result: one clause worked, one did nothing, and the second is the
### more interesting finding

**The axis clause is a real fix.** Steering the model on spatial reasoning
worked: told what an axis means, it computes the right one from the
dimensions in the request.

**The position clause changed nothing, and not because the model
misunderstood.** On F2 its own summary reads *"a 1 mm chamfer on the top rim
of the hole"* — it parsed the request correctly and said so in prose — and
then emitted a selector with `position` absent. The guidance is in the
prompt (asserted by test), the schema admits `position` with an
enum of `top`/`bottom` (asserted by test), and the field is simply not used.

That makes this **representational, not a reasoning failure**: under
structured output the model reliably fills required fields and omits
optional ones, even when instructed and even when its own summary shows it
understood. No amount of prompt wording looks likely to fix it.

**The indicated next step is a provider-encoding change, and it is
deliberately not implemented here:** make the end-of-rim a *required* field
in the provider encoding — `position` with values `top`/`bottom`/`both` —
decoding deterministically back to the canonical selector, where `both`
means the `position`-absent form. That keeps the canonical IR and the
parser exactly as they are and removes the optionality the decoder is
declining to use. It is a different change from this one and belongs in its
own stage, measured on its own.

### Remaining selector limitations

- `position` is unreachable in practice while it is optional.
- `selector_correct` compares the mode only, so it scores a wrong axis as
  correct. The decomposed components exist now; the original metric was
  left in place rather than redefined.
- `F4-patterned-rims-chamfered` still needs `pattern`, which no proven
  encoding carries.

## Stage 55: the end of a rim, made structural

### Hypothesis

Stage 54 left one explanation standing. The model understands "the top rim"
— its own prose says so — and omits `position` anyway, 4/4, under an
encoding where the field is optional. Prompt guidance did not move it. So
the remaining hypothesis was **not** about the model's reading of the
request but about the shape of the schema: *an optional field is one a
grammar-constrained decoder declines to use.*

Stage 55 tests exactly that, and nothing else.

### The change

The selector becomes a **discriminated union** — one branch per mode —
instead of one object with every field optional. That alone expresses what
the flat object could not, and its own code comment admitted: `axis` is
required for `straight` and `axis_parallel`, absent for `all`, optional for
`circular`. On top of that the circular branch **requires** `position`.

```
all            {select}
axis_parallel  {select, axis*}
straight       {select, axis*}
circular       {select, position*, axis}      * = required
```

`strict_selector_provider_schema()`, fingerprint `887718d3e5387529`.

**What it narrows, stated rather than hidden.** The canonical language
permits a circular selector with no position, meaning *both* rims, and the
parser still accepts exactly that — a test asserts it. This encoding cannot
say it. No corpus case needs it; a caller who does should use
`selector_provider_schema()`. Nothing decodes, translates or repairs: what
the model emits under this grammar is already canonical wire format.

### Grammar cost

| | raw | inlined | nodes | branches | defs |
|---|---:|---:|---:|---:|---:|
| `selector` (proven 3134) | 2824 | 3134 | 72 | 5 | 4 |
| **`strict_selector`** | 3309 | **3619** | 84 | 5 | 4 |

+485 inlined, **862 below the proven-accepted 4481** and 932 below the
refusal. Measured offline before any call was spent.

### The result: 4 calls, F2 and F3, 2 attempts each

| | Stage 54 (flat, optional) | **Stage 55 (union, required)** |
|---|---|---|
| `selector_mode_correct` | 4/4 | 4/4 |
| **`selector_position_correct`** | **0/4** | **4/4** |
| `selector_fully_correct` | 0/4 | **4/4** |
| **semantic correctness** | **0/4** | **4/4** |
| build success | 4/4 | 4/4 |
| `executed_by_graph` | 4/4 | 4/4 |

F2 emitted `{"select":"circular","axis":"Z","position":"top"}` both times;
F3 emitted `"bottom"` both times. Same model, same prompt
(`2026-09-16.1`), same six operations, same selector modes — **only the
selector's structure differs.**

### Geometry, and why volume alone would not have proved it

Volume is now **56825.944222323116**, exactly the expected one-rim figure,
against **56793.48** in Stage 54 when both rims were chamfered. That proves
*one* rim rather than both.

It does **not** prove *which*: F2 and F3 measure the same volume to the last
bit, which is the whole reason both cases exist. Which end was selected is
established by the selector the model wrote, scored against the case's
expectation — geometry and selector evidence answering two different
questions, neither standing in for the other.

### Conclusion

**Structural optionality was the cause.** The failure Stage 53 recorded as a
model mistake, and Stage 54 proved was not a comprehension failure, was the
schema all along: under structured output this model fills required fields
and omits optional ones, and no amount of prompt wording changed that while
the field stayed optional. Making it structural fixed it completely, on the
first attempt, in both directions.

Two attempts per direction is a small sample and the change is kept on that
basis — 0/4 → 4/4 with one variable moved is strong, but it is not a rate.
No broader benchmark was run.

### The architecture rule this establishes

```
canonical IR        a circular selector MAY omit position (both rims)
provider encoding   MAY require it, to constrain what a model can leave out
parser              authoritative and unchanged; accepts both forms
```

An encoding may be **stricter** than the language. That is legitimate
precisely because it narrows what can be *said*, never what a plan *means* —
and it is now measured as the difference between a capability the model has
and one it will actually use.

### Remaining ambiguity

- Under `strict_selector` a request genuinely meaning both rims is
  inexpressible. Deliberate, documented, and costless for this corpus.
- `selector_correct` still compares the mode only; the decomposed
  components from Stage 54 remain the honest reading.
- Whether required-ness helps elsewhere is untested — the optional
  `axis` on `circular` was left optional on purpose, so this experiment
  moved one thing.

## Stage 56: backend parity — harness built, comparison BLOCKED on this machine

### The blocker, found before any plan was made around it

**FreeCAD is not available on this Windows development machine.**
`freecad_available()` returns `False` and `CAD_FREECAD_HOME` is unset. Per
Stage 42 it was installed from the official Linux **AppImage** (649 MB,
extracting to 2.4 GB) and needs `LD_LIBRARY_PATH` set *before Python starts*
to resolve a bundled `libssl` against the system `libcrypto`. Neither the
AppImage nor `LD_LIBRARY_PATH` exists on Windows, and FreeCAD has no pip
distribution.

So the CadQuery-vs-FreeCAD comparison this stage set out to make **cannot be
measured here**. No parity number is reported, because none was obtained.

### What was established instead

**The backend abstraction is genuinely symmetric.** Both `CadQueryBackend`
and `FreeCadBackend` implement the identical 18-method `CadBackend`
protocol, with no extra methods on either side:

```
available  name  version
create_box  create_cylinder  through_hole  subtract
fillet  fillet_edges  chamfer  chamfer_edges
select_edges  describe_edges  edges_at
measure  render_model  export_step  read_step
```

`CAD_BACKEND` defaults to `cadquery` and **`resolve_backend()` contains no
fallback path** — verified by reading the source, not assumed. A caller who
names a backend gets it or gets an error.

### The parity harness, and proof that it works

Written and exercised. Each case is **one canonical operation plan** run
against every available backend; the model is not part of it. Sources are
committed fixtures plus the canonical plans recorded in the Stage 53–55
result files — a recorded plan is a plan, and using one isolates backend
behaviour from model behaviour exactly as intended.

Tolerances are explicit: volume `rtol=1e-6`, bounding box `atol=1e-6 mm`.
No exact float equality.

| # | case | source | CadQuery | volume mm³ | FreeCAD |
|---|---|---|---|---:|---|
| 1 | primitive + hole | fixture `plate-one-hole` | **OK** | 59497.345 | UNAVAILABLE |
| 2 | subtract | fixture `subtract-cube-bore` | **OK** | 109292.037 | UNAVAILABLE |
| 3 | fillet, `straight` selector | Stage 53 `F1` | **OK** | 56643.806 | UNAVAILABLE |
| 4 | chamfer, `straight` + axis X | Stage 54B `D1` | **OK** | 56058.407 | UNAVAILABLE |
| 5 | circular **top** rim | Stage 55 `F2` | **OK** | 56825.944 | UNAVAILABLE |
| 6 | circular **bottom** rim | Stage 55 `F3` | **OK** | 56825.944 | UNAVAILABLE |
| 7 | two holes, modifier ordering | Stage 53 `G2` | **OK** | 55273.009 | UNAVAILABLE |
| 8 | radial pattern | — | NO_SOURCE | — | UNAVAILABLE |

Every CadQuery figure matches the value the corresponding recorded run
measured, which is what makes the harness trustworthy rather than merely
runnable. Cases 5 and 6 return **identical** volumes, correctly reproducing
the fact that a top and a bottom rim chamfer to the same number — the
harness does not pretend geometry can separate them.

**Case 8 has no source.** No committed fixture uses a `pattern`, and no
proven provider encoding can express one, so nothing in the repository
produces such a plan. Recorded as `NO_SOURCE` rather than as a backend
result.

### Classification discipline

`UNAVAILABLE` (backend absent) is kept distinct from `UNSUPPORTED` (backend
present, capability missing), from `MISMATCH` (both executed, geometry
differs) and from `ERROR`. Only the first occurs here, and it is a fact
about this machine rather than about FreeCAD.

### What is needed to finish this stage

A Linux host — or WSL — with the FreeCAD AppImage extracted, `CAD_FREECAD_HOME`
set and `LD_LIBRARY_PATH` exported before Python starts. The harness, the
corpus and the tolerances are ready; only the second backend is missing.

## Stage 57: FreeCAD runs, and the backends agree where both can execute

Stage 56 was blocked because FreeCAD was unavailable on Windows. It is now
running under WSL, and the parity matrix exists. Setup is documented
reproducibly in `docs/freecad-wsl-setup.md`; the harness is
`scripts/freecad_parity.{py,sh}`, driven by `CAD_REPO`, `CAD_FREECAD_HOME`
and `CQ_SIDE` rather than any machine's user directory.

### Environment

| | |
|---|---|
| Host | WSL2, **Ubuntu 26.04 LTS**, x86_64, kernel 6.18.33.2 |
| System Python | 3.14.4 — **cannot host FreeCAD** |
| FreeCAD | **1.0.0, build 39109**, official `py311` AppImage, extracted (2.4 GB) |
| Interpreter used | FreeCAD's bundled **Python 3.11.9** |
| CadQuery | **2.8.0**, side-installed, coexisting in the same process |

Three findings from the setup are worth keeping, because each cost time:

**The system interpreter cannot host this backend.** `libFreeCADBase.so`
fails with `undefined symbol: _Py_PackageContext` under Python 3.14. The
AppImage's own 3.11 is mandatory.

**The FreeCAD backend cannot import without CadQuery.** It imports
`cad_core.render_model` → `cad_core.local_cad`, which requires CadQuery. So
the *other* engine must be installed for this one to load — a real coupling
in the abstraction, not a packaging accident.

**`pip install cadquery` into the bundled interpreter fails**, because
CadQuery wants a newer `vtk` than the one FreeCAD installed through
distutils, which pip cannot safely uninstall. Forcing it was rejected: a
half-uninstalled FreeCAD would execute geometry wrongly rather than fail
loudly. A `--target` side directory leaves FreeCAD's site-packages intact and
both engines then coexist.

### Smoke test — all seven pass

`freecad_available()` True · backend `freecad 1.0.0` · `create_box` ok ·
`measure` volume 6000.0, 1 solid, 6 faces, 12 edges · `render_model` returns
a `RenderModel` · `through_hole` ok · `resolve_backend()` returns
`FreeCadBackend` with no fallback.

The same through-hole, built by both engines in one process:

```
FreeCAD  volume=5151.769983530756  faces=7  edges=15
CadQuery volume=5151.769983530756  faces=7  edges=15
```

Bit-identical.

### Parity matrix

Tolerances: volume `rtol=1e-6`, bbox `atol=1e-6 mm`. Same canonical plan to
both backends; no model involved.

| case | CadQuery | FreeCAD | geometry parity | notes |
|---|---|---|---|---|
| 1 primitive + hole | OK 59497.345 | OK 59497.345 | **PASS**, Δ=0.0 | exact |
| 2 subtract | OK 109292.037 | OK 109292.037 | **PASS**, Δ=0.0 | exact |
| 3 fillet, `straight` | OK 56643.806 | UNSUPPORTED | — | needs `fillet_edges` |
| 4 chamfer, `straight`+X | OK 56058.407 | UNSUPPORTED | — | needs `chamfer_edges` |
| 5 circular **top** | OK 56825.944 | UNSUPPORTED | — | needs `chamfer_edges` |
| 6 circular **bottom** | OK 56825.944 | UNSUPPORTED | — | needs `chamfer_edges` |
| 7 two holes, ordering | OK 55273.009 | UNSUPPORTED | — | needs `chamfer_edges` |
| 8 radial pattern | NO_SOURCE | NO_SOURCE | — | no plan exists |

**2 PASS · 5 UNSUPPORTED · 0 MISMATCH · 0 ERROR · 1 NO_SOURCE.**

Where both backends execute they agree to the last bit. **No geometry
mismatch and no semantic mismatch was found.**

### The capability gap, stated precisely

**This corrects Stage 56.** That stage reported both backends implementing
"the identical 18-method protocol" — which was read off `dir()`, and `dir()`
shows *inherited* names. Checking `vars()` instead:

`FreeCadBackend` implements **14 of 18** protocol methods and inherits four
as base-class stubs that raise a bare `NotImplementedError`:

```
fillet_edges   chamfer_edges   describe_edges   edges_at
```

It *does* implement `fillet`, `chamfer` and `select_edges`. The gap is the
**explicit edge-list** family, which is the path the graph executor takes —
so every selector case is blocked while every non-selector case works.

Those five results are classified **UNSUPPORTED** (backend present,
capability absent), never ERROR or MISMATCH. The harness was corrected to
make that distinction rather than letting `NotImplementedError` masquerade as
a failure.

### Live model checkpoint — not run, and why

Phase 11 was gated on parity succeeding. It succeeded for primitive, hole and
subtract only; the five selector cases cannot be cross-executed at all. A
checkpoint would therefore have re-measured on exactly the cases Stage 52
already covered while answering nothing about cross-backend behaviour on the
interesting ones. No calls were spent.

## Stage 59: the agentic control loop

The first production-shaped loop around the existing architecture. It adds
**orchestration only** — no CAD concept, no geometry, no selector semantics,
no second representation. Every piece of CAD judgement is delegated to code
that already existed.

```
task
  → Planner.generate         a model, or a deterministic stub
  → canonical operation plan
  → validate_plan            authoritative, unchanged
  → build_plan               graph / adapter / backend, unchanged
  → inspect                  backend-neutral view
  → diagnose                 deterministic, no model
  → Reviser.revise           a model, or a deterministic stub
  → a NEW canonical plan
  → validate / build / inspect, bounded
  → VerifiedResult + trace
```

`apps/api/src/cad_experimental/agentic_loop.py`. The existing direct build
path is untouched and still works — a test asserts it.

### The seven rules it is built to keep

1. **The plan is canonical.** A revision is a *new* `PlanCandidate` with a
   parent link. Nothing is mutated, so what was tried survives the run.
2. **Nothing is silently repaired.** The loop never edits model output; a
   revision is a whole new plan, validated from scratch.
3. **Nothing falls back** — not between backends, not between planners.
4. **The taxonomy does not collapse.** "The build failed" is not an outcome.
5. **Diagnosis is deterministic.** No model decides what went wrong.
6. **The loop is bounded** — 1 attempt + 2 revisions, and no way to ask for
   more.
7. **The planner is an interface.** No vendor is named in the engine; a test
   asserts it by walking the AST for imports and identifiers rather than
   grepping prose.

### Failure taxonomy

`NO_PLAN_PRODUCED` · `PLAN_INVALID` · `PLAN_UNSUPPORTED` ·
`BACKEND_UNSUPPORTED` · **`SELECTOR_MATCHED_NOTHING` (E4)** ·
**`SELECTOR_GEOMETRY_REJECTED` (E5)** · `EXECUTION_ERROR` ·
`GEOMETRY_INVALID` · `MEASUREMENT_MISMATCH` · `SEMANTIC_MISMATCH` ·
`SUCCESS`.

Each member names a **different remedy**, which is the test for deserving to
exist. The E4/E5 split the Stage 40–55 line of work established is preserved
and carries different `requested_change` text: *matched nothing* needs a
different selector, *matched something the kernel refused* needs a different
parameter with the selector kept.

`BACKEND_UNSUPPORTED` is deliberately **not revisable**: a backend does not
grow a method because a plan changed, and asking a reviser to try would
invite something that looks like a fix and is not.

### A diagnosis defect this stage found and fixed

The first live run classified a refused 25 mm fillet as `EXECUTION_ERROR`
and therefore offered no revision. The cause was in the diagnosis, not the
model: it was matching prose heuristics while **the backend already emits the
rule code** — `"the kernel refused the fillet (rule E5): BRep_API: command
not done"`. Reading the code first is exact; the prose markers remain only as
a fallback for a backend that has not adopted the convention.

### Live checkpoint — 5 calls, `claude-haiku-4-5-20251001`

Not a benchmark. Four tasks, structured output on, no retry, no repair, no
fallback.

| task | attempts | result |
|---|---:|---|
| fillet a hole rim with a **25 mm** radius on a 10 mm plate | **2** | E5 → revised → **success** |
| chamfer the **top** rim | 1 | success |
| chamfer the four **long edges** | 1 | success |
| plate + hole, volume-checked | 1 | success |

The first task closed the whole loop live, and the revision was **targeted**:

```
attempt 1  radius 25  edges {circular, top, Z}   -> selector_geometry_rejected
           asked: "keep the selector and change the parameter the kernel refused"
attempt 2  radius  2  edges {circular, top, Z}   -> success
```

Plate and hole byte-identical between attempts; only the fillet changed. The
model kept the selector it was told was correct and changed the one parameter
it was told was not.

### The trace answers the four questions

`what did the model try` — every attempt carries its full payload.
`what failed` — a `failure_class` per attempt. `what evidence caused the
revision` — `diagnosis.facts` carries the measured particulars. `what
changed` — consecutive payloads are directly comparable. `why was the final
result accepted` — `accepted_because`, not a bare boolean.

### Limitations

- The live checkpoint is **five calls**. It proves wiring; it estimates
  nothing.
- Only one failure class was exercised live (E5). The other ten are covered
  by 33 deterministic tests, not by the model.
- E4 has **no live evidence at all** — no task in this checkpoint produced a
  selector that matched nothing.
- Diagnosis still reads error *text* for the E4/E5 split. The rule code makes
  that exact where backends emit one; a typed error would make it exact
  everywhere.
- `MEASUREMENT_MISMATCH` compares one volume. It is not a shape check.

## Stage 60: hardening the agentic runtime — typed evidence

Stage 59 decided E4 and E5 by matching words in a backend's error string.
That is brittle in the obvious way and silently wrong in a worse one: a
backend phrasing a failure differently gets a *confident misdiagnosis*.

None of it was necessary. `edge_semantics.resolve` was already a total,
typed resolver over `EdgeFacts` — it never raises, returns the indices it
chose, and reports its own codes for why it chose no more:

| code | meaning | rule |
|---|---|---|
| `R1` | the selector matched no edge | **E4** |
| `R2` | the selection contains a parameterisation seam | **E5** (seam half) |
| `R3` | a position filter could not separate top from bottom | — |

### Measured, on a 100×60×10 plate with a 20 mm bore

| selector | code | edges | seams |
|---|---|---:|---:|
| `all` | R2 | 0 | 1 |
| `axis_parallel Z` | R2 | 0 | **1** |
| `straight Z` | — | **4** | 0 |
| `circular Z` | — | **2** (both rims) | 0 |
| `circular Z top` | — | **1** | 0 |
| `circular Z bottom` | — | **1** | 0 |
| `circular X` | **R1** | **0** | 0 |

`axis_parallel` including exactly one seam while `straight` includes none is
the Stage 47 semantics, now visible as a *number*. And a top rim naming 1
edge where both name 2 is what finally separates F2 from F3 without touching
volume.

### What changed

`SelectorEvidence` carries `selected_edge_count`, `candidate_edge_count`,
`seam_count` and `resolution_code` per operation. `MeasurementEvidence`
carries expected-vs-actual per field. `FailureDiagnosis` gained `evidence`,
`selector_evidence`, `measurement_evidence`, `backend`, and **`retryable`
separate from `revision_allowed`** — a backend lacking a method is neither.
`facts`/`revisable` survive as aliases so Stage 59 traces still read.

`diagnose()` now consults, in order: **typed counts** → the backend's own
rule code → prose, and a prose-classified diagnosis records
`classified_by: "error text, not typed evidence"` so the trace never hides
which it used. A test proves typed evidence **overrides contradicting
prose**.

### Comparison levels

`MEASUREMENT` (volume + solids) → `TOPOLOGY` (+ face/edge counts) →
`SEMANTIC` (+ the selector names what the request describes). **None is
geometric equality** — F2/F3 agree on every number in the first two levels,
which is exactly why `SEMANTIC` exists. A level is reported only when its
check actually ran: a task stating no selector cannot reach `SEMANTIC`.

### A second defect this stage found

The first live run classified a model's `needs_clarification` as
`EXECUTION_ERROR`. A model asking a question is not a build failure —
nothing was executed and nothing failed. `InspectionResult` now carries
`plan_status` and such an answer is `NO_PLAN_PRODUCED`.

### Live checkpoint — 4 calls

| task | result |
|---|---|
| circular selector on a plate with no hole | model **asked for clarification** → `no_plan_produced` |
| 25 mm fillet on a 10 mm plate | `selector_geometry_rejected` → **revised → success** |
| chamfer a body never created | model built a valid plate → success |

**E5 is proven live. E4 is not** — the model sensibly asked a question
rather than emitting an impossible selector, so no live answer ever carried
one. E4 is proven deterministically, including classification with
`error_text=None`.

### Correction: the evidence was already there

A parallel read of the codebase found that this stage had **rebuilt
something the executor already does**. `executor._blend` resolves every
selector it applies and records the `Resolution` under the operation's own
id; `ExecutionResult.selections` carries that mapping, and its own comment
calls it *"the diagnostic an agent needs to see why an edge operation did
what it did"*. Crucially `failed()` returns it too, so it survives a build
that did not finish.

The first implementation here re-derived it by calling `describe_edges` on
the final shape. That was worse in three ways, all now fixed by reading
`build.execution.selections` instead:

- it needed a **final shape**, so it produced nothing on a failed build —
  exactly when a diagnosis is needed;
- it was **CadQuery-only**, because `FreeCadBackend` does not implement
  `describe_edges`;
- it reported a resolution recomputed afterwards rather than **the one
  actually used** to cut the part.

The loop now imports **no backend module at all** — not `cad_backend`, not
`edge_semantics` — and the architecture test was tightened from
"`resolve_backend` is the one seam" to "no backend module is imported",
which is the stronger claim the code now supports.

Measured on the failed E5 attempt of the live checkpoint, evidence present
on a build that did not finish:

```json
{"operation_id": "rim_fillet",
 "selector": {"select": "circular", "position": "top", "axis": "Z"},
 "selected_edge_count": 1, "candidate_edge_count": 2, "seam_count": 0}
```

One of two candidate rims named — so the *selector was right and the radius
was not*, which is E5 as data rather than as an inference from a sentence.

### Limits

- **E4 has no live proof.** Deterministic only: the model asked a question
  rather than emitting an impossible selector, so no live answer carried one.
- **E5 beyond the seam case is still not typed.** `R2` covers a seam, but a
  kernel refusal on radius surfaces only as `BackendOperationError` prose
  carrying `rule E5` — exact where a backend emits that code, with no typed
  field behind it.
- Evidence is absent for an operation the executor never reached, and is
  reported absent rather than invented.
- `TOPOLOGY` depends on face/edge counts `_measure` does not return, so runs
  report `MEASUREMENT` or `SEMANTIC`.
- Prose matching survives as a last resort and records `classified_by` when
  it is what decided.

## Live generation restored: the route was sending a grammar the provider refuses

### The defect

`POST /experimental/generate-plan` answered **503 to every request**. The
credential was valid, the model reachable and the rest of the stack healthy:
`/experimental/health` was 200, the local fixtures built, and a plain
`messages.create` to `claude-haiku-4-5-20251001` returned normally. Only this
route failed, and it failed on the provider's own words:

> `400 invalid_request_error` — *"The compiled grammar is too large, which
> would cause performance issues. Simplify your tool schemas or reduce the
> number of strict tools."*

`generation.py` was sending `provider_schema()` — the ten-type, eight-branch
encoding built in Stage 44 and **never measured against the compiler**. Stages
50/51 had since bounded the ceiling to **(4481, 4551]** inlined characters by
real calls. `provider_schema()` measures **7351**. It could never have
compiled, so from the moment structured output was turned on this route was
dead, and its 503 was a schema-selection bug wearing a provider outage as a
disguise.

Nothing about the canonical IR was involved, and nothing about it changed.

### The tradeoff, measured before any code moved

All figures are ref-inlined characters on the schema sent verbatim to
`messages.create` (the experimental path applies no sanitiser; the plan schema
is already free of the rejected keywords, and `sanitise_schema` is a proven
no-op on it).

| encoding | inlined | br | vs ceiling | operations sayable | selectors |
|---|---:|---:|---|---|---|
| `provider_schema` (was sent) | **7351** | 8 | **OVER, refused live** | all ten | full |
| `compact_provider_schema` | 6190 | 8 | OVER, refused live | all ten | full |
| `profile_union` | 4481 | 6 | accepted live | 7, no edge pair | **none** |
| 7 types + strict selector | 4633 | 6 | **OVER** (>4551) | all executable | full |
| 7 types + flat selector | 4148 | 6 | under | all executable | full but **0/4** |
| **`strict_selector` (now sent)** | **3619** | 5 | **under by 862** | 6 buildable | **full, 4/4** |
| `selector` (flat) | 3134 | 5 | under | 6 buildable | full but **0/4** |

Two rows decide it. **`pattern` cannot be afforded**: adding it to the strict
selector measures 4633, past the 4551 refusal, and the only trim that would
fit (4505) both lands in the unmeasured band and strips `axis`, which these
selectors need. And the **flat** selector, though smaller and able to carry
`pattern`, was measured by Stage 54 at **0/4** on `selector_position_correct`
— an optional field is one a grammar-constrained decoder declines to use — so
a top-rim chamfer is unreachable under it in practice. Stage 55's discriminated
union scored **4/4** on the same prompt. The extra 485 characters buy back the
capability the whole selector line of work exists for.

### What was changed

One line of behaviour: the route now sends
`strict_selector_provider_schema()`, which Stage 55 had already built,
measured and **proven live** — same fingerprint, `887718d3e5387529`. The
schema was not redesigned and the canonical IR was not touched.

Two fields were added to `PlanGenerationMetadata`, `plan_schema` and
`plan_schema_fingerprint`, so every answer says which grammar produced it. A
narrowed encoding that went unnamed would make an *inexpressible* request
indistinguishable from a *refusal*, which is exactly the Stage 44 mistake that
Stage 48 then found a second time.

### Capabilities retained and lost, stated plainly

**Retained:** all six operations the engine can build (`box`, `cylinder`,
`through_hole`, `subtract`, `fillet`, `chamfer`) and the **whole** selector
vocabulary — `all`, `axis_parallel`, `straight`, `circular`, a rim's
`position`, and `axis`.

**Lost, and not silently:**

- `sketch`, `extrude`, `revolve` — **not buildable** in any case; the
  execution boundary refuses them, so no part that could be made is now
  unreachable. They still parse and validate, and two Stage 44 tests assert
  that on the very plan the grammar cannot express.
- `pattern` — **buildable, and a real loss.** Measured unaffordable above.
- a `circular` selector with no `position`, meaning *both* rims. The parser
  still accepts it from any other source.

Stage 44's test asserting the sent grammar admits a profile was updated rather
than deleted: it now asserts the grammar admits everything the engine can
**build**, and a companion test pins the profile narrowing so that a future
encoding which buys profiles back under the ceiling will fail it and say so.

### Verification

`claude-haiku-4-5-20251001`, prompt `2026-09-16.1`
(`a1f9991c7327fb9dc028aba8f37b29a53f3751fe16855f173149249b7134e960`), schema
`strict_selector` / `887718d3e5387529` / 3619 inlined, `structured_output`
true on every answer. Five live calls: one probe, four cases.

| case | plan | selector emitted | path | volume mm3 |
|---|---|---|---|---|
| simple plate | `box` | — | V1 document | built |
| plate + hole | `box`, `through_hole` | — | V1 document | built |
| top rim chamfer | + `chamfer` | `circular` / `Z` / **`top`** | **graph executor** | **56825.944222323116** |
| vertical corner fillet | + `fillet` | **`straight`** / `Z` | **graph executor** | **59785.39816339745** |

Every plan parsed and validated with **no problems**. The rim chamfer selected
**one of two candidate rims** (`indices [9]`, `candidates [13, 9]`, no seam)
and reproduces the Stage 55 one-rim figure exactly; the corner fillet selected
`[0, 2, 4, 6]` — the four vertical corners, the seam excluded by `straight` —
against a closed form of `60000 - 4(25 - 25pi/4)(10) = 59785.398...`.

**No local fixture was involved.** These answers carry neither `source:
LOCAL_DEVELOPMENT_PLAN` nor `is_live_model_result: false`; they carry real
Anthropic provider and model metadata. The fixture route is untouched and
still stamps both.

## Stage 61: a CAD session that needs no model at all

Three commits form one milestone, and the question behind all of them is the
same: **how much of this product actually requires a hosted model?** The
honest answer turned out to be "less than the code assumed", and every place
that assumed otherwise was a place where a request this project can read was
refused for want of a credential.

The rule the stage is organised around: **a model is one route to a plan, not
the way in.** Every route ends at the same parser, the same validator, the
same feature graph and the same kernel. The deterministic route is not a
bypass and gets no shortcut — it reaches the pipeline at exactly the place a
provider's answer does, and is judged by exactly the same rules.

### `union`, so that "join these" is a sentence the language has

`union` is the eleventh operation type. It was the missing half of a
long-standing awkwardness: the prompt told the model *"there is no union in
this language"* and *"two solids cannot be joined"*, which was true and which
made every assembly request unanswerable — a hollow box out of six plates is
six solids that must become one.

Adding it cost **no grammar budget**. `provider_schema()` had eight branches
and eight is the measured ceiling (Stage 44); `subtract` and `union` differ
only in name — both take a `target` and an ordered `tools` list — so they
merge into one branch exactly as `fillet` and `chamfer` already do:

| | before | after |
|---|---:|---:|
| operation types | 10 | **11** |
| schema branches | 8 | **8** |

`executable_schema()` is untouched and still fingerprints `54759d1e16cfe634`,
because Stage 43 must keep reproducing Stage 43.

Prompt `2026-09-17.1` (`4e270378e5d6826a`) gained a `union` section and lost
both refusal sentences. **The refusal list is part of the operation** — the
Stage 44 and Stage 48 lesson, applied on purpose this time rather than found
afterwards: adding an operation and leaving it in the UNSUPPORTED list makes
the model decline something the language has, and the run then records the
contradiction as the model's judgement.

### Two provider-neutral grammars

`intent.py` reads a **counted plate assembly** — *"a hollow rectangular box
with 40\*20\*5 (4) plates and 20\*20 (2) plates with 8 mm diameter holes in
centre of each plate"* — into canonical intent, which lowers to a plan.

`normalize.py` reads **general mechanical requests** through eight readers:
`box`, `cylinder`, `corner_holes`, `pattern`, `centre_hole`,
`edge_treatment`, `remove`, `resize`.

Order matters and is not a preference. An assembly sentence says "plate" and
carries three numbers, so the general box reader matches it too — and would
build **one** of its six plates. The assembly grammar is asked first, *and*
`read_box` independently declines anything `looks_like_plate_assembly`
claims. Relying on the ordering alone would be relying on this function never
being refactored.

Either grammar reads a request **completely or not at all**. A partial
reading would be a guess dressed as a fact, and the readers are the one part
of this system with no model to blame for a guess.

### Evidence, and where a number came from

`questions.py` answers eleven kinds of question about the part that is
already built, and labels every number with how it was arrived at:

| provenance | meaning | example |
|---|---|---|
| `MEASURED` | the kernel measured it on the build that succeeded | volume, faces, edges |
| `DECLARED` | the plan asks for it; the kernel was *told* it | hole diameter, count |
| `CALCULATED` | worked out from the two above, with the working shown | mass, removed material |

The distinction is the point. "The holes are 12 mm in diameter" is a
different kind of claim from "volume 58869.027 mm³", and a session that
reports both in the same voice invites a reader to trust the weaker one as
much as the stronger. So a declared answer says so: *"That is what the plan
asks for; the kernel was told it rather than asked."*

**Mass requires a named material or a stated density.** Asked cold, it does
not pick one — it says what it needs. A density silently assumed is a
fabricated number wearing three decimal places.

### The bug this stage's own wiring introduced, and the test that pins it

The evidence answerer was wired in **after** the `planner is None` branch. So
with no model configured:

```
>>> how many holes does it have?
    HTTP 503   no interpretation model is configured
```

— about a part sitting in the session with its holes already counted. The one
question in the route that provably needs no model was the only one refused
for want of one. Answering reads a build that already happened; it is now
asked first, and `test_a_question_is_answered_with_no_model_configured`
fails if the ordering is ever restored. (Verified by reintroducing the bug:
the test goes red with exactly the 503 above, and green again on revert.)

### Measured — a whole session, no credential present

The backend was started with `ANTHROPIC_API_KEY` and `CAD_ANTHROPIC_API_KEY`
unset; `/experimental/health` reports `model_configured: false` throughout.
Backend FreeCAD 1.0.0.

| turn | status | route | volume mm³ | closed form | delta |
|---|---|---|---:|---:|---:|
| `a 100 x 60 x 10 mm plate` | built | deterministic | `60000.0` | `60000` | **0** |
| `put a 12 mm hole through the centre` | built | deterministic | `58869.02664470768` | `60000 − π·6²·10` | `7.3e-12` |
| `add four 6 mm holes 10 mm in from each corner` | built | deterministic | `57738.053289415366` | `− 4π·3²·10` | `2.2e-11` |
| `how many holes does it have?` | answered | — | — | declared | `There is 1 hole.` |
| `what size are the holes?` | answered | — | — | declared | `12 mm` |
| `what is the volume?` | answered | — | — | measured | — |
| `how much material was removed?` | answered | — | `1130.973` | `1 × π(12/2)²×10` | working shown |
| `what does it weigh in aluminium?` | answered | — | `162.0 g` | `60 cm³ × 2.7` | working shown |
| `what would it weigh in steel?` | answered | — | `471.0 g` | `60 cm³ × 7.85` | working shown |

Three refusal behaviours, kept apart on purpose:

| request | answer | why it differs |
|---|---|---|
| `put a 60 mm hole through the centre` (of a 40 mm plate) | **200 refused** — *"a 60 mm hole does not fit through a 40 mm section"* | a grammar **recognised** it and cannot honour it |
| `design me a differential gearbox` | **503 unavailable** | **no grammar claimed it** — it is a question for a model |
| `what does it weigh?` (no material) | **200 answered** | answerable, but not without a density |

Conflating the first two is what made "design me a gearbox" come back as a
confident *no* rather than as a question nobody had asked yet. `Interpretation`
now carries `refused` separately from `error` for exactly this reason: a
refusal is an answer to give the person; a non-understanding must fall
through.

### The golden assembly, through a real browser, with no model

`npm run e2e:assembly` — real Chromium, real HTTP, real WebGL surface:

| | |
|---|---|
| interpreted by | **deterministic**, `model_configured: false` |
| solids | **1**, valid |
| volume | **11492.035526276897 mm³** — matches the closed form |
| envelope | 40 × 20 × 20 mm |
| topology | 18 faces, 42 edges |
| mesh | 5544 triangles, 2764 vertices |
| backend | freecad |
| failed requests / console errors | 0 / 0 |

Six plates become **one solid** via `union`, and the six 8 mm holes were
confirmed from real kernel topology — 12 circular edges of r = 4 lying in 12
distinct planes — rather than inferred from the fact that the plan contains
hole operations.

### A guard repaired rather than excused

`test_the_plan_layer_does_not_special_case_the_seam` asserts no plan-layer
module implements edge geometry, by forbidding six names. It had started
failing on `backend_parity_probe.py` — the diagnostic that audits the backend
interface by **calling** every capability on it. Auditing a capability means
naming it.

A name-level check cannot tell these apart:

```
backend.select_edges(...)          delegation THROUGH the interface  — wanted
edge_selection.select_edges(...)   reaching PAST it, into geometry   — forbidden
```

The probe is therefore excused the three **interface method** names and
nothing else; `GeomAbs`, `BRepFilletAPI` and `edge_selection` stay forbidden
for it. A blanket file exclusion would have bought the pass by giving up the
guard — confirmed by mutation: a probe that imports `edge_selection` still
fails, and so does a plan-layer module that names `select_edges`.

### Suite

| suite | result |
|---|---|
| `tests_experimental` | **1767 passed, 72 skipped** (Linux, FreeCAD 1.0.0 present) |
| `cad-core` | **1481 passed** |
| frontend `tsc --noEmit` | clean |
| frontend `vite build` | succeeds |
| `e2e:assembly` | **PASS** |

### What this stage does NOT claim

It says **nothing about model quality**. No live provider call was made; the
credential was deliberately absent. Every number above is either the kernel's
own measurement or arithmetic on the request's own figures. That a request
can be read without a model is a statement about the *grammars*, and the
grammars are narrow by construction — they cover the vocabulary above and
decline everything else, which is why `design me a differential gearbox`
still needs one.
