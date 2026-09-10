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

Two. That is the entire vocabulary.

| Operation | Parameters |
|---|---|
| `box` | `x`, `y`, `z` (all > 0); optional `position` (minimum corner) |
| `cylinder` | `diameter`, `height` (both > 0); optional `position` (base centre), optional `axis` (one of `+X -X +Y -Y +Z -Z`) |

Everything else — spheres, holes, subtraction, joins, fillets, chamfers,
sketches, extrudes, patterns, assemblies — is `unsupported` by design, and the
parser rejects any operation type it does not implement rather than passing it
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
  "summary": "a rectangular plate",
  "operations": [
    {"id": "body", "type": "box", "parameters": {"x": 100, "y": 60, "z": 10}}
  ]
}
```

## The modules

All under `apps/api/src/cad_experimental/`. Nothing in `cad_ai`, `cad_api` or
`cad_core` imports any of it, and a test asserts that.

| Module | Purpose |
|---|---|
| `plan.py` | The typed plan: `BoxOperation`, `CylinderOperation`, `OperationPlan`, `PlanStatus`, and the plan's JSON Schema |
| `parser.py` | Untrusted model text -> typed operations. The security boundary |
| `validation.py` | Plan rules `P1`-`P7`. Separate from the V1 validator, which is untouched |
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
* **The prompt is much shorter**: 4018 characters against the production
  prompt's 14943, for a vocabulary of two operations. Whether that translates
  into better model accuracy is **not yet known** — see below.

## What is NOT known

**No live measurement exists.** `ANTHROPIC_API_KEY` is not available in the
environment this was built in, so the five cases have not been put to real
Claude Haiku 4.5. The harness runs, its plumbing is proven against a stub, and
a stub result says nothing whatever about model quality.

So the stage's central question — is the operation plan easier for a model to
get right than the V1 document? — **remains open**. Answering it needs the live
harness run, and then a comparison against the stable path's own numbers on
equivalent prompts.
