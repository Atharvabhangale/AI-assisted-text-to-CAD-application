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

## What is NOT known

**Real Anthropic testing is still postponed.** Stages 33 to 39 were developed
and tested entirely against the local development provider, which supplies
developer-written plans and calls no model. No result in this document, in the
fixtures, in the tests or on the page is a Claude result of any kind, and no
claim is made about Claude Haiku's quality on any representation.

**Nothing is known about how a model handles the new vocabulary.** Six
operations were added across Stages 33–38 and not one of them has been put to
a real model. In particular it is unmeasured whether a model offers a sketch
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
