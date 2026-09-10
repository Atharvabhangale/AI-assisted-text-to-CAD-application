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

Three, as of Stage 33. That is the entire vocabulary.

| Operation | Kind | Fields |
|---|---|---|
| `box` | constructive | `parameters`: `x`, `y`, `z` (all > 0); optional `position` (minimum corner) |
| `cylinder` | constructive | `parameters`: `diameter`, `height` (both > 0); optional `position` (base centre), optional `axis` |
| `through_hole` | **modifier** | `target` (beside `id`/`type`); `parameters`: `diameter` (> 0), `position` (**required**), optional `axis` |

Everything else — spheres, blind holes, counterbores, general subtraction,
joins, fillets, chamfers, sketches, extrudes, patterns, assemblies — is
`unsupported` by design, and the parser rejects any operation type it does not
implement rather than passing it downstream.

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
* **The prompt is much shorter**: 5374 characters against the production
  prompt's 14943, for a vocabulary of three operations. Whether that
  translates into better model accuracy is **not yet known** — see below.
* **A modifier and a reference cost the architecture nothing.** Stage 33 added
  `through_hole` without a second CAD engine, a second validator, a second
  build path, a second cache or a second RenderModel, and without touching
  `local_cad.py` or the V1 validator. The four-hole plate builds to
  57989.38070170254 mm³ against a closed form of 57989.38070170253 — one solid,
  ten faces, outside dimensions unchanged.
* **The holes are real openings, not a visual impression.** Checked in the
  mesh's own coordinates: 253 rim vertices per hole, zero vertices inside any
  hole, and zero top-face triangles whose centroid falls within one.

## What is NOT known

**Real Anthropic testing is still postponed.** Stage 33 was developed and
tested entirely against the local development provider, which supplies
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
