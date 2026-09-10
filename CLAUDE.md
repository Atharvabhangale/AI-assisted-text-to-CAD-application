# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project handoff

This file exists so a new session can continue the project without re-deriving
it. It describes the repository as it actually is, verified against the code,
the docs and the git history at the commit that added it. Where something is
unbuilt, unverified or unmeasured, it says so — that is the point of the file.

`docs/cad-specification.md` is the source of truth for the CAD contract. This
file summarizes; it never overrides.

---

## 1. Project purpose

An **AI-assisted text-to-CAD application**: describe a mechanical part in
natural language, get real CAD geometry back in a browser.

The intended long-term flow:

```
natural language
  → AI interpretation            (apps/api/src/cad_ai)
  → canonical CAD document       (docs/cad-specification.md)
  → validation                   (cad_core.validator, authoritative)
  → local CAD engine             (cad_core.local_cad, OpenCascade via CadQuery)
  → B-rep solid
  → RenderModel                  (neutral tessellation, plain data)
  → STEP / IGES / STL            (cad_core.*_export)
  → browser                      (apps/web)
```

**That chain is now joined end to end** (Stage 30). `POST /generate` exposes
the AI layer over HTTP, and the browser's primary input is a sentence: type a
description, press **Generate CAD**, and the page interprets, builds and draws
it as one action. The CAD-JSON workflow survives behind an **Advanced** section.

What is *not* solved is reliability of the interpretation itself — see §6 and
§11: the configured model returns a valid document only about half the time,
and the remaining failures are reported honestly rather than repaired.

### FeatureScript / Onshape

The original first target for turning a neutral document into geometry was
**Onshape FeatureScript**, and that path is still a plausible future backend —
browser-based, scriptable, no local kernel needed.

Its current status is **interface only, and deliberately so**:

- `cad_core.featurescript` generates FeatureScript source text for **a single
  box, and nothing else** (`docs/featurescript-generation.md`).
- `cad_core.onshape_adapter` defines the delivery boundary; `onshape_fakes`
  provides an unconfigured adapter and a recording adapter. **Nothing here has
  ever contacted Onshape.** Stage 3B was blocked at authentication (the API
  answers `401 Unauthenticated`), and no Onshape MCP tool exists in this
  environment (`docs/onshape-mcp-boundary.md`).
- The local CadQuery/OpenCascade engine, not FeatureScript, is the real engine
  today and is far ahead of it in coverage.

Do not describe FeatureScript output as verified. It has never been built in
Onshape.

---

## 2. Core architectural principles

These are the decisions the codebase is organized around. Breaking one is a
design change, not a refactor.

- **The canonical CAD document is the source of truth.** Everything else —
  B-rep, mesh, STEP, render model, artifacts — is derived and reproducible
  from it.
- **The AI generates a structured CAD document, never executable CAD code.**
  The model emits JSON data. It never emits Python, CadQuery, FeatureScript,
  STL or STEP.
- **Generated code is never executed.** There is no `eval`, no `exec`, no
  dynamic import of model output anywhere in the pipeline.
- **The validator is authoritative for document validity.** No other layer
  decides whether a document is valid, and no layer is allowed to accept a
  document the validator rejects.
- **The CAD engine is authoritative for geometry.** Static rules (S1–S20) are
  decidable from the document; geometric rules (E1–E5) require the kernel.
- **Derived artifacts are not authoritative.** A STEP file is an output, never
  an input to a decision.
- **A document hash identifies CAD content**; **a build key identifies
  document + build options.** They are different identities and are not
  interchangeable.
- **An artifact's logical identity is separate from its physical storage
  path.** Callers pass logical ids; the registry resolves storage.
- **HTTP is a transport layer, not business logic.** `cad_api` maps requests
  and status codes. It contains no CAD logic and no decisions of its own.
- **The CAD kernel is isolated from the host process.** Builds run in a child
  process so a kernel crash cannot take down the application.
- **No silent geometry or edge-operation behavior.** A hole that misses the
  material, a selector that matches no edge, a cut that splits the body — each
  is an error, never a quiet no-op.
- **Determinism is required below the stochastic AI layer.** The model is the
  only nondeterministic component. Everything beneath it must be reproducible.

---

## 3. The V1 CAD specification (summary)

Authoritative file: **`docs/cad-specification.md`** (schema version `1.0.0`).
Rule codes live in `cad_core.rules`. Read the spec before changing anything
that touches document semantics.

**Feature vocabulary — exactly six types:**

| Type | Kind | Key parameters |
|---|---|---|
| `box` | constructive | `size {x,y,z}` (all > 0), optional `position` |
| `cylinder` | constructive | `diameter`, `height` (both > 0), optional `position`, optional `axis` |
| `through_hole` | modifier | `target`, `diameter`, `position`, `axis` |
| `subtract` | modifier | `target`, `tools[]` |
| `fillet` | modifier | `target`, `radius`, edge selector |
| `chamfer` | modifier | `target`, `distance`, edge selector |

**Semantics that matter:**

- **Units:** carried inside the document, never implied. V1 accepts `"mm"`
  only — a validator must **reject** any other value, never convert it (S5).
- **Coordinate system:** one right-handed part coordinate system, Z-up,
  origin `(0,0,0)`. No local frames, no datums, no transforms, no rotations.
  Every position is absolute.
- **Positions and sizes:** objects with exactly `x`, `y`, `z`, each finite.
  The array form `[10, 10, 0]` is invalid on purpose. Sizes must be positive;
  position components may be negative or zero.
- **Axes:** one of the six signed principal directions `"+X" … "-Z"`.
  Arbitrary direction vectors are not in V1.
- **Feature ordering:** `features` is an **ordered** list evaluated in order
  against a solid set. Order is semantic, not cosmetic.
- **References:** every `target`/`tools` entry must name a solid that exists
  at that point in evaluation (S6) and appear **strictly earlier** in the list
  (S7). No forward references, no cycles. Ids are unique and match
  `^[A-Za-z_][A-Za-z0-9_-]*$` (S8).
- **Edge selectors:** `select` is `"all"` or `"axis_parallel"`; `axis`
  (`"X"`/`"Y"`/`"Z"`) is present exactly when `select` is `"axis_parallel"`
  (S18).
- **Single-solid rule (S9):** the first feature is constructive, and after the
  last feature the solid set holds **exactly one** solid. No assemblies, no
  orphan bodies, no multi-body parts.
- **Strictness (S3):** any unknown field or unknown feature type is an error.
  Nothing is silently ignored.
- **Geometric rules (E1–E5):** a through-hole must actually intersect material;
  a subtract must leave a non-empty solid; every modifier must leave one
  connected solid; a selector must match at least one edge; a fillet radius or
  chamfer distance must be admissible on every matched edge.

Out of scope for V1 and required to be rejected: assemblies, sketches, splines,
lofts, sweeps, revolves, threads, patterns, GD&T, tolerances, materials,
simulation, and expressions/scripting of any kind.

---

## 4. Implemented components

### `packages/cad-core` — the deterministic core (no HTTP, no LLM)

| Module | Purpose | Notable API | Limitations | Docs |
|---|---|---|---|---|
| `model.py` | Typed V1 document | `Part`, `Box`, `Cylinder`, `ThroughHole`, `Subtract`, `Fillet`, `Chamfer`, `Position`, `Size`, `EdgeSelector` | Types only; validity is the validator's job | `docs/cad-specification.md` |
| `validator.py` | **Authoritative** static validator | `validate(document) -> ValidationResult` | Static rules S1–S20 only; E-rules need the engine | spec §E |
| `errors.py` | Structured validation errors | `ValidationError`, `ValidationResult` | — | — |
| `rules.py` | Rule-code constants | S1–S20, E1–E5 | — | spec §E |
| `geometry.py` | Geometric rule boundary | `check_geometric_rules` | Requires a built shape | spec §E.2 |
| `serialization.py` | Canonical JSON + hashing | `serialize_part`, `part_hash`, `deserialize_part`, `parts_equivalent`, `save_part`, `load_part` | Canonical form is the hash input; don't hand-roll JSON | `docs/cad-document-serialization.md` |
| `local_cad.py` | **The CAD engine** (CadQuery/OpenCascade) | `build_part`, `shape_volume`, `shape_bounding_box`, `shape_solid_count` | Requires `cadquery`; returns kernel objects — never serialize them | `docs/local-cad-engine.md` |
| `edge_selection.py` | Deterministic edge selection | `select_edges`, `line_direction`, `is_straight_edge` | Only `all` / `axis_parallel`; seam semantics unresolved (§11) | `docs/edge-selection.md` |
| `render_model.py` | Neutral tessellation as plain data | `build_render_model`, `RenderModel`, `TessellationSettings` | Visualization only, never a geometry source | `docs/render-representation.md` |
| `step_export.py` | STEP export + round-trip check | `export_step`, `read_step` | — | `docs/step-export.md` |
| `iges_export.py` | IGES export + round-trip check | `export_iges`, `read_iges` | — | `docs/iges-export.md` |
| `stl_export.py` | Binary STL export + round-trip | `export_stl`, `read_stl`, `binary_stl_facts` | Mesh, not B-rep | `docs/stl-export.md` |
| `build_job.py` | Build/job layer, build keys | `BuildRequest`, `BuildOptions`, `build_key_for`, `execute_build`, `BuildResult` | In-memory; no persistence, no queue | `docs/build-job-layer.md` |
| `local_build_cache.py` | Deterministic filesystem cache | `LocalBuildCache`, `get_or_build`, `CacheEntry` | Local disk; single-user | `docs/local-build-cache.md` |
| `artifact_registry.py` | Derived-artifact identity + manifest | `artifact_logical_id`, `publish_file_artifact`, `build_manifest`, `ArtifactKind` | Logical id ≠ storage path — keep it that way | `docs/artifact-registry.md` |
| `isolated_execution.py` / `isolated_worker.py` | Child-process kernel isolation | `execute_isolated`, `get_or_build_isolated`, `invoke_worker` | **Crash containment, not a security sandbox** (§11) | `docs/isolated-cad-execution.md` |
| `application_service.py` | Application/domain service | `CadApplicationService`, `BuildDocumentRequest`, `BuildOutcome`, `LocalBuildBackend` | The layer transports should call | `docs/application-service.md` |
| `api_contract.py` | Transport-neutral external contract | `validate_request_from_payload`, `build_response`, `artifact_contract`, `error_contract`, `CadApiContract` | **The contract, not FastAPI's OpenAPI**, is the source of truth | `docs/api-contract.md` |
| `featurescript.py` | FeatureScript source generation | `generate_featurescript` | **Single box only**; never executed in Onshape | `docs/featurescript-generation.md` |
| `onshape_adapter.py` / `onshape_fakes.py` | Onshape delivery boundary | `OnshapeAdapter`, `deliver_part`, `RecordingOnshapeAdapter` | **Interface only — nothing contacts Onshape** | `docs/onshape-mcp-boundary.md` |

### `apps/api/src/cad_api` — HTTP transport (FastAPI)

| Module | Purpose |
|---|---|
| `app.py` | The ASGI app: seven routes, no CAD logic (`create_app`, `app_from_environment`) |
| `schemas.py` | Pydantic models for the **transport envelope only** (`ValidateBody`, `BuildBody`, `GenerateBody`) |
| `artifacts.py` | Safe logical-id → bytes resolution (`ArtifactResolver`) |
| `builds.py` | Read-only build retrieval by build key (`BuildRetriever`) |
| `status.py` | The HTTP status mapping, in one place |
| `config.py` | `CAD_API_CACHE_ROOT`, `CAD_API_TIMEOUT_SECONDS` |
| `generation.py` | `POST /generate`'s coordinator (`TextGenerator`). Holds the one `TextToCadService`, built lazily. Names **no** vendor |

Docs: `docs/http-api.md`, `docs/artifact-delivery.md`,
`docs/build-result-retrieval.md`.

### `apps/api/src/cad_ai` — the AI interpretation layer

| Module | Purpose |
|---|---|
| `provider.py` | The provider boundary: `TextToCadModel`, `ModelRequest`, `ModelResponse`, `ProviderError`, `ProviderErrorKind` — **no vendor types** |
| `anthropic_provider.py` | Anthropic Messages API adapter (`AnthropicTextToCadModel`). Also `schema_for_api` / `UNSUPPORTED_SCHEMA_KEYWORDS`: this API's structured-output compiler **rejects** `exclusiveMinimum`, `minItems`, `minLength` and `pattern`, so the model-facing schema is narrowed to its subset before it is sent. Sending the schema unchanged 400s every request. No information is lost — the appended spec excerpt states all four rules in prose |
| `gemini_provider.py` | Google `google-genai` adapter (`GeminiTextToCadModel`) |
| `prompt.py` | The single system prompt, versioned + fingerprinted (`system_prompt`, `prompt_fingerprint`) |
| `specification.py` | Model-facing view of the spec, **derived** from `docs/cad-specification.md` (`specification_text`, `response_schema`) |
| `generation.py` | `TextToCadService`: description in, `AiGenerationResult` out, validated by `cad_core` |
| `config.py` | Provider/model/timeout from the environment; **never reads a credential value** |
| `corpus.py` | The 35-case benchmark corpus (`CORPUS_VERSION = "1.0.0"`) |
| `comparison.py` | Field-level document comparison + semantic error taxonomy |
| `evaluation.py` | The benchmark harness and its CLI |
| `factory.py` | `model_from_environment` — the one provider dispatch, shared by the app and the benchmark |

Docs: `docs/text-to-cad-ai.md`, `docs/ai-evaluation.md`,
`docs/ai-provider-comparison.md`.

### `apps/web` — the browser text-to-CAD application

`main.ts`, `app.ts`, `api.ts`, `viewer.ts` (Three.js), `render-model.ts`,
`design-intent.ts` (formats a generated document into English; computes
nothing). Docs: `docs/web-application.md`.

---

## 5. Test status

Measured on this commit, by actually running the suites. Nothing here is
estimated.

| Suite | Result |
|---|---|
| cad-core (`unittest`) | **1481 passed** (Stage 32, on Windows) |
| API + AI (`unittest`) | **591 tests, 6 skipped** |
| Frontend (`vitest`) | **122 passed** (4 files) |
| Frontend typecheck (`tsc --noEmit`) | **clean** |
| Launcher (`scripts\Test-DevLauncher.ps1`) | **62 assertions passed** |

**Both Python suites were red on Windows until Stage 32, for reasons that had
nothing to do with CAD.** If a suite fails here, suspect the platform before
the code — and read §15 before "fixing" anything.

**The live-provider skips are deliberate and must stay skipped by default.** They are the
live-provider tests, gated behind `CAD_AI_LIVE_TESTS`. They exist so that the
mere presence of an API key can never cause the ordinary test suite to spend
money on real provider calls. This gate was added after an accidental live run
during Stage 28A; do not remove it.

There are no known failing tests, and no test is quarantined, skipped or
weakened to make the suite pass.

The AI/evaluation tests are part of the 547 and live in
`apps/api/tests/test_text_to_cad_ai.py`, `test_ai_evaluation.py` and
`test_gemini_provider_boundary.py`, with shared fakes in `provider_stubs.py`.
They use stub providers only — **no test result says anything about real model
quality.**

---

## 6. Current AI status

**Provider-neutral interface.** `cad_ai.provider.TextToCadModel` is a tiny
interface with no vendor types in its signatures. Both providers implement it;
each imports its SDK **lazily**, inside its own module, so `cad-core` and the
HTTP transport install and import without any LLM SDK. The SDKs are optional
extras: `ai` (`anthropic>=1.4`) and `ai-gemini` (`google-genai>=2.22`).

**Anthropic provider.** Uses `messages.create` with structured output
(`output_config` / `json_schema`). Measured against SDK 1.4.0, which exposes
**no** temperature/top_p/top_k/seed on this path.

**Gemini provider.** Uses `client.models.generate_content` with
`system_instruction`, `response_mime_type="application/json"`,
`response_json_schema` and `max_output_tokens`. Measured against
`google-genai` 2.22.0, which **does** expose temperature/top_p/top_k/seed —
none of which are set. It never sends `tools`, `tool_config` or
`automatic_function_calling`.

**Prompt architecture.** One prompt, in one place, with one version id:
`PROMPT_VERSION = "2026-09-09.2"`, fingerprint
`f9efe19ac33281ff14ab0efe665dc01971d458ea5d90e8114f6dabba0ca0874e`, length
25237 characters. **Four separate tests pin those three values** (two in
`test_text_to_cad_ai.py`, two in `test_gemini_provider_boundary.py`); when you
change the prompt on purpose, update all of them together and re-measure —
their own comments say so, and the pins are change *detectors*, not claims
that the prompt is correct. The
spec text the model sees is derived from `docs/cad-specification.md` rather
than retyped, so the two cannot drift. Every evaluation run records the
version and fingerprint, and a regression test pins them.

**Supported natural-language subset:** since prompt `2026-09-09.1`, the
**full V1 vocabulary** — one part, an ordered feature list over all six
feature types, with units stated in the request. The earlier restriction to a
single `box` or `cylinder` is gone: it was refusing parts the engine had been
able to build since Stage 14.1.

Two policies the prompt adds on top of the contract, both from measured
failures (see `docs/text-to-cad-ai.md` → *Prompt 2026-09-09.2*):

- **Nothing joins.** V1 has no `union`/`fuse`/`join`, so a part that is only
  meaningful as two or more primitives *joined into one body* — a desk stand,
  an L-bracket, a handle on a body — is `UNSUPPORTED`. Before this was
  stated, the model assumed a join existed and improvised: S9 (three solids
  left), S14 (`"tools": []` as a pseudo-merge) and S6 (a modifier's id used as
  a solid) were all one root cause.
- **Locatives are answers, not gaps.** "on the top", "centred", "through the
  centre" *supply* a required position rather than being something to ask
  about, and the prompt gives the arithmetic. Before this, identical wording
  returned a document 3/5 and a clarification 2/5.

**The corpus in `cad_ai.corpus` still encodes the old, narrower policy.** Its
E-category cases expect `UNSUPPORTED` for holes, cuts, fillets and chamfers,
which the prompt now supports, so those five cases are **stale and will score
as failures**. They are a fixed measurement instrument — do not edit them to
raise a score; treat a full re-baseline as its own stage.

**Ambiguity rule:** a missing value the spec gives a default may be omitted;
a missing value with no default must be asked about. So "a 100 × 60 × 10 mm
plate" yields a document with no `position` (the contract's default), while
"plate 100 by 60 by 10" yields `NEEDS_CLARIFICATION` about units — units are
required and are never implied.

**Five outcomes:** `GENERATED`, `NEEDS_CLARIFICATION`, `UNSUPPORTED`,
`MODEL_ERROR`, `INVALID_MODEL_OUTPUT`. The last two are unreachable by
anything the model says about itself.

**Evaluation corpus:** 35 cases, version `1.0.0`, seven categories —
A-box (6), B-cylinder (6), C-defaults (3), D-ambiguity (5), E-unsupported (5),
F-adversarial (5), G-semantic (5). Expected documents were authored
independently of the model.

**Comparison system.** Field-level diffing with a semantic error taxonomy
(`wrong_dimension`, `wrong_position`, `wrong_axis`, `wrong_feature_type`,
`wrong_feature_id`, `wrong_label`, `wrong_default`, `missing_feature`,
`extra_feature`, `ambiguity_not_asked`, `unsupported_feature_accepted`, …).
Differences are marked geometric or non-geometric, and **exact document match,
semantic correctness and geometry correctness are reported separately** — a
differing `name` or feature `id` is not a CAD error.

**No repair loop.** Invalid model output is reported, never patched. There is
no retry, no re-prompt, no automatic correction anywhere.

**Nothing the model returns is executed.** Boundary tests assert this.

**`POST /generate` exposes the AI layer** (Stage 30). It returns a validated
document and builds nothing; the client posts that document to the existing
`POST /build`. There is still no repair loop, no retry and no conversation
state.

### Live benchmark status — read this before making any quality claim

**A complete real-model benchmark still does not exist.** No 35-case corpus
run has been made against Anthropic, and the corpus is partly stale (§6).

**Anthropic IS now exercised live**, and is the default provider on
**`claude-haiku-4-5-20251001`**. What exists is *product-flow* evidence, not a
corpus score — hand-run prompts through `POST /generate`, five attempts each
where a rate was claimed:

| Request | Result |
|---|---|
| plate, plate + 4 holes, cylinder, cube + centred hole, block + chamfer, plate + fillet | all `generated`, valid, built; volumes match closed-form arithmetic exactly |
| enclosure + hole "on the top" | `generated` **5/5** after prompt `2026-09-09.2` (was 3/5) |
| "a simple desk stand … single solid" | `UNSUPPORTED` **5/5** — correct; V1 cannot join primitives |

Two honest caveats. The four-hole plate was verified down to the B-rep — one
solid, 6 planes + 4 cylindrical walls, and the exported STEP reads back
identically — but **categories F (adversarial) and G (semantic) remain
completely unmeasured on any provider**, so no robustness claim can be made.
And the model intermittently answers `status: "document"` with the `document`
field simply absent (seen once on the chamfer case, which then passed 4/4);
that is a reliability mode, not a wrong document.

- **Gemini: still never run to completion.** The 20/35 history below is
  Gemini's alone.
- **Gemini `gemini-2.5-pro`: 404**, and as of Stage 30 `gemini-2.5-flash` is
  404 too — both "no longer available to new users". The default in
  `config.py` was changed to `gemini-3.6-flash`, which is verified working —
  see §7. The old 20/35 baseline's model can therefore **no longer be
  reached**, so that run cannot be completed or extended; a new baseline on
  `gemini-3.6-flash` must be started fresh and must not be merged with it.
- **Gemini `gemini-3.8-flash`, `gemini-3.5-flash`:** partial runs during the
  free-tier pacing work; superseded.
- **Gemini `gemini-2.5-flash`, paced at 12 s, 2026-09-09:** the current
  baseline. **20/35 genuine responses**; the other 15 were HTTP 429
  rate-limits. Coverage 0.571.

**What the 20 measured cases showed** (this is the only real quality data in
the project, and it is partial):

- parse success 20/20; document validation 13/13; build success 13/13
- geometry correctness **12/15 = 0.80** on cases where a document was expected
- **exact document match 0/15 = 0.00** — entirely label/description/feature-id
  differences, not CAD errors. Reading exact match as correctness would report
  0% for a model that is 80% correct.
- ambiguity handling 3/3 correct; unsupported handling 2/2 correct
- **A6** (a box request phrased in reverse dimension order) came back with X
  and Y swapped: valid, buildable, **identical volume**, and semantically
  wrong. The evaluator caught it as a geometry error. This case is the
  standing proof that valid CAD ≠ correct CAD.
- **B2/B3** asked for clarification on explicit-axis cylinders where a
  document was expected — over-clarification on unambiguous input.

**Categories F (adversarial) and G (semantic edge cases) received zero
responses.** The recorded `boundary_violations: 0` is vacuous: nothing was
tested. **No claim about adversarial robustness can be made from this run.**

Saved runs are preserved under `docs/evaluation-baselines/`.

---

## 7. Gemini status

- **Environment variable: `GEMINI_API_KEY`.** (Anthropic: `ANTHROPIC_API_KEY`.)
- **How availability is checked:** `cad_ai.config.credential_available()` reads
  only whether the variable is *present and non-empty*. The value is never
  returned, stored, logged, compared or printed. `AiConfig` has no credential
  field at all, so a config dump cannot leak one.
- **Secrets are never committed.** No key appears in source, tests, docs or
  saved evaluation results; the saved results were scanned to confirm it.
  Tests use an obviously synthetic placeholder. `.env*` is gitignored.
  **Never put a key in this file, in code, or in a commit.**
- **Anthropic's default model** is `DEFAULT_MODELS["anthropic"] =
  "claude-haiku-4-5-20251001"`, and `resolve_provider` picks Anthropic first
  whenever its credential is present. `CAD_AI_MODEL` overrides it.
- **Current Gemini model configuration.** `DEFAULT_MODELS["gemini"]` is
  **`gemini-3.6-flash`**, changed in Stage 30 after measuring that *both*
  `gemini-2.5-pro` **and** `gemini-2.5-flash` now return 404 for this key —
  *"no longer available to new users. Please update your code to use
  models/gemini-3.6-flash"*. Google's own named replacement was verified with
  a single probe before the default was changed.
  **`models.list` is not a usability signal**: it still lists both dead models
  (and 38 others) for this key. Availability must be established by an actual
  call.
- **Known availability issues.** Beyond the 404: the free tier enforces a
  **daily per-model request allowance of about 20**, not merely a per-minute
  rate. This was established empirically — slower pacing scored *worse* than
  faster pacing, which is impossible for a rate limiter, and the 429 body
  named `generate_content_free_tier_requests, limit: 20`. **Pacing cannot buy
  requests back once the daily allowance is spent.** Any plan to reach 35/35
  must account for the allowance, not just the interval — e.g. a paid tier, or
  splitting the corpus across days, or rotating the case order so the same
  categories are not always the ones starved.
- **A run is never started by a credential's mere presence.** `--live` is
  required, and the live tests are additionally gated behind
  `CAD_AI_LIVE_TESTS`. Both gates exist because of a real accidental live run.

**How to run the benchmark later:**

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src
cd apps/api

python -m cad_ai.evaluation --check        # validate the corpus; calls no model
python -m cad_ai.evaluation --self-check --pace 0   # harness vs a stub; --pace 0
                                           # or it sleeps 12s per case
python -m cad_ai.evaluation --list         # print the corpus

# live (spends quota/money — requires --live):
CAD_AI_MODEL=gemini-2.5-flash \
  python -m cad_ai.evaluation --live --provider gemini --build --pace 12
```

---

## 8. Frontend status

**Stack:** Vite + TypeScript + Three.js, tested with Vitest + jsdom, with a
Playwright end-to-end script (`apps/web/e2e/run.mjs`).

**What it does today:** a single page where you paste or load a **canonical
CAD document**, validate it, build it, and see the tessellated result in a
WebGL viewport, with STEP / IGES / STL downloads.

- **3D viewer** (`viewer.ts`): Three.js scene rendering the mesh.
- **RenderModel usage** (`render-model.ts`): consumes the backend's neutral
  render payload. It does not tessellate anything itself.
- **Generate flow:** `POST /generate` → design intent → `POST /build` → `GET
  /builds/{key}/render` → draw, as **one** user action.
- **Build flow (advanced):** `POST /validate` → `POST /build` → `GET
  /builds/{key}/render` → draw.
- **Export flow:** `GET /artifacts/{id}` for STEP/IGES/STL bytes.
- **Current input type:** a **natural-language description**, with the CAD
  JSON kept behind an **Advanced** section. The three editable fields are all
  free text a person types — description, clarification answer, CAD JSON — and
  a boundary test asserts there is still no field that edits geometry
  directly.

**The frontend is a client, not the CAD engine.** Every dimension, volume,
solid count and triangle count it displays is a field the backend sent. It
imports no kernel and computes no geometry.

**Delivered in Stage 30:** the natural-language input box, the `/generate`
endpoint, a design-intent panel, clarification questions with a follow-up
answer box, `UNSUPPORTED` refusals as product copy, and the automatic
generate → build → draw chain. `src/design-intent.ts` formats the returned
document into English and computes nothing.

**Still missing:** history, saved projects, edit-and-rebuild of a generated
part, and any multi-turn conversation (the clarification follow-up composes
one fresh self-contained description rather than keeping state).

---

## 9. HTTP API status

Seven routes, from `apps/api/src/cad_api/app.py`. Docs: `docs/http-api.md`.

| Route | Purpose |
|---|---|
| `GET /health` | liveness |
| `POST /validate` | validate a CAD document; returns the validation result. Builds nothing |
| `POST /build` | validate + build a document with the requested outputs; returns the build result and artifact references |
| `GET /builds/{build_key}` | read-only retrieval of a previously published build |
| `GET /builds/{build_key}/render` | the `RenderModel` for a build, for the viewer |
| `GET /artifacts/{artifact_id}` | deliver artifact bytes (STEP / IGES / STL / geometry / render) |
| `POST /generate` | **(Stage 30)** interpret a natural-language description into a validated CAD document. Builds nothing |

Relationships: `POST /build` returns a **build key** and **artifact ids**; the
two `GET` routes exchange those identifiers for the build result and for bytes.
Artifact ids are **logical** — the resolver maps them to storage, and the
storage path is never exposed or accepted.

**The AI endpoint is `POST /generate`** (Stage 30). Its five outcomes map to
three statuses: `generated` / `needs_clarification` / `unsupported` are **200**
(the question was asked and answered), `invalid_model_output` is **502** (an
upstream answered badly) and `model_error` is **503**.

**Limitations — this is local-development software:**

- no authentication, no authorization, no user isolation;
- **build keys are global**: any client that knows a build key can read
  another's build result and download its artifacts;
- no rate limiting — each cache miss runs a real CAD build;
- process isolation is **crash containment, not a security sandbox**: the
  child runs as the same OS user with the same filesystem permissions, no
  seccomp, no container, no privilege dropping;
- no payload size limit, no request timeout of its own, no CORS policy, no
  TLS, no audit trail;
- FastAPI's generated OpenAPI is **not** the source of truth —
  `cad_core.api_contract` is.

---

## 10. Roadmap

Reconstructed from the git history (35 implementation commits, Stage 0 →
Stage 29, plus the commit that added this file) and the docs. Verify against
`git log` before relying on it.

**COMPLETED**

| Stages | What |
|---|---|
| 0–2 | Skeleton, the V1 specification contract, the static validator |
| 3A–3C | FeatureScript for a single box; the Onshape/MCP boundary (interface only) |
| 4–9 | Local CAD engine (box, then cylinder); STEP, IGES, STL export; the RenderModel |
| 10–14.1 | `through_hole`, `subtract`, deterministic edge selection, `fillet`, `chamfer` |
| 15–19 | Canonical serialization, build/job layer, artifact registry, build cache, process isolation |
| 20–24 | Application service, transport-neutral contract, HTTP transport, artifact delivery, build retrieval |
| 25 | Browser CAD viewer |
| 26–27 | The AI interpretation layer; the evaluation harness |
| 28A–29 | Gemini provider; reliability/quality separation and the first paced live baseline |

**IN PROGRESS**

- **Real model evaluation.** Partial: 20/35 on `gemini-2.5-flash`. Categories
  F and G are unmeasured, on any provider. Anthropic has product-flow
  evidence but no corpus run, and five corpus E-cases are stale (§6).

**NEXT**

1. **Re-baseline the corpus for the widened prompt.** Five E-cases now
   contradict prompt policy (§6), so a corpus run today mis-scores. This is
   the blocker on every quality claim, and it must be done by re-authoring
   expectations as a deliberate stage — never by editing cases to raise a
   score.
2. **Run 35/35 against Anthropic**, which has product-flow evidence but no
   corpus number. Gemini's free tier still caps at ~20 requests/day (§7).
3. ~~**Fix the unusable Gemini default model**~~ — **done**; and Anthropic on
   `claude-haiku-4-5-20251001` is now the default provider.
4. **Prompt improvement**, continuing from the measured failures: the A6
   dimension swap and the B2/B3 over-clarification remain unaddressed. The
   Stage 32 pattern is the one to follow — reproduce N times live, find the
   root cause, change the prompt, re-measure.

**FUTURE**

5. **AI repair / verification loop** — deliberately absent today; would need
   its own design so it never becomes silent correction.
5. ~~**Natural-language frontend integration**~~ — **done in Stage 30.**
6. ~~**MVP**: description → geometry → export, end to end, in the browser.~~ —
   **done in Stage 30**, and demonstrated live. The remaining gap is
   *reliability*, not capability: see §11.
7. Beyond MVP: broader feature vocabulary (a V2 schema), live Onshape
   verification, authentication and multi-user storage, production sandboxing
   and persistence.

---

## 11. Known limitations and open questions

Each of these is supported by the code, the docs or the measured results.

- **No live Onshape verification.** FeatureScript output has never been built
  in Onshape; Stage 3B was blocked at authentication and no Onshape MCP tool
  exists here.
- **AI semantic correctness is only partly measured** — 20/35 cases, one
  model, one run. The A6 X/Y swap is a confirmed real failure mode.
- **AI ambiguity handling is imperfect**: B2/B3 asked for clarification on
  unambiguous explicit-axis cylinders.
- **A model sometimes answers `status: "document"` with the `document` field
  absent.** Worst measured on `gemini-3.6-flash` (Stage 30, 13 calls: 6
  documents, 6 `invalid_model_output`, 1 provider error) — the answer carries
  36-52 output tokens against 80-106 for a success, and `stop_reason` is
  `STOP`, so it is not truncation. On `claude-haiku-4-5-20251001` it is rare
  rather than routine: seen once across the prompt work, on a case that then
  passed 4/4. Still a *prompt/schema* problem to fix by measurement, never by
  a repair loop.
- **Adversarial and semantic-edge-case behavior is entirely unmeasured**, on
  every provider.
- **Provider availability is unreliable**: a 404 on the default Gemini model
  and a ~20-request/day free-tier allowance.
- **Anthropic has no corpus benchmark**, only the hand-run product flow in §6.
- **Seam-selection semantics are unresolved** — see `docs/edge-selection.md`
  for which edges a cylinder's seam contributes and why it matters for
  `select: "all"`.
- **No security or authentication** anywhere in the stack.
- **No production sandboxing** — isolation is crash containment only.
- **No multi-user storage**: one shared cache, global build keys.
- **No production job persistence**: the build/job layer is in-memory, with no
  queue, no durable state and no background workers.
- **No repair loop**, by design, for now.
- The V1 vocabulary is small: no sketches, no revolves, no patterns, no
  assemblies.

---

## 12. Important things not to break

Treat these as invariants. If a change requires violating one, that is a
design decision to raise explicitly, not a detail to slip into a diff.

- **Do not weaken rules S1–S20 or E1–E5**, and do not make the validator
  lenient to let a model's output through.
- **Do not bypass validation.** Every document reaching the engine has passed
  the validator.
- **Do not execute model-generated code.** No `eval`, no `exec`, no writing
  model output to a file and importing it. The model emits data.
- **Do not silently repair CAD semantics.** Wrong output is reported, not
  patched.
- **Do not silently skip selected edges.** A selector matching nothing is an
  error (E4), never a no-op.
- **Do not serialize kernel objects.** OpenCascade/CadQuery shapes stay inside
  the engine and the isolation boundary; documents, render models and
  artifacts cross layers.
- **Do not put FastAPI or business logic into `cad-core`**, and do not put CAD
  decisions into `cad_api`.
- **Do not add LLM dependencies to `cad-core`.** Provider SDKs are optional
  extras of `cad-api`, imported lazily inside their own provider modules.
- **Do not introduce a duplicate CAD schema.** One contract, in
  `docs/cad-specification.md`, typed once in `cad_core.model`, exposed to the
  model via `cad_ai.specification` (derived, not retyped) and to transports via
  `cad_core.api_contract`. FastAPI's OpenAPI is not a second source of truth.
- **Do not commit secrets.** No keys in code, tests, docs, evaluation results
  or commit messages.
- **Do not change benchmark expectations to improve scores.** The corpus,
  the expected documents and the comparison logic are fixed measurement
  instruments. Fix the prompt or the code, then re-measure.
- **Do not treat valid CAD as necessarily correct CAD.** A6 is the standing
  counterexample: valid, buildable, identical volume, and wrong.
- **Do not conflate provider reliability with model quality.** A rate-limited
  case is *unmeasured*, not incorrect, and must stay out of every quality
  denominator.
- **Do not let a credential's presence start a paid run.** `--live` and
  `CAD_AI_LIVE_TESTS` exist because that already happened once.

---

## 13. How to continue

1. Read this file, then `git status`, `git log --oneline -10`, and the docs
   for whatever area you are touching.
2. Run the relevant test suite **before** changing anything, so you can tell
   your breakage from pre-existing state (§14).
3. Read the actual module before assuming an API. The docs in `docs/` are
   detailed and current; each component's row in §4 names its file.
4. **Do not recreate existing work.** Stages 0–29 are done. The deterministic
   core, the exporters, the build/cache/isolation stack, the service, the
   contract, the HTTP transport, the viewer, the AI layer and the evaluation
   harness all exist and are tested.
5. **Continue from the next unfinished stage** (§10 NEXT) — completing the
   35/35 Gemini baseline, fixing the unusable default model, then
   prompt improvement driven by measured failures. Do not restart from Stage 0.
6. Keep the working style the project has used throughout: one stage at a
   time, verify empirically rather than assume, use explicit tolerances for
   kernel values (never exact float equality), and report limitations honestly
   rather than rounding them away.

---

## 14. Useful commands

### Start everything (Windows, the normal way in)

```powershell
.\start.ps1      # backend 8000 + frontend 5173, waits for health, opens the page
.\stop.ps1       # stops what start.ps1 started
```

`start.ps1` locates the repo, uses the existing `.venv`, reads
`appspi\.env`, creates the `CAD_API_CACHE_ROOT` the app refuses to invent,
and starts the same uvicorn factory and Vite dev server. Useful switches:
`-NoBrowser`, `-BackendPort`, `-FrontendPort`, and `-DotEnvPath` (for a git
worktree that has no `.env` of its own). It **adopts** an already-running
server rather than starting a second one, and refuses an occupied port instead
of silently moving. Logs and state live in the gitignored `.dev\`.

Do not hand-roll `uvicorn`/`npm run dev` command lines when you just need the
app running. The manual sequence below still works and is still supported.

### Tests

**On Windows use `python` from `.venv` and semicolon-separated `PYTHONPATH`.**
A colon-separated `PYTHONPATH` is silently ignored there, and the editable
installs then resolve to whatever tree they were installed from — which means
you can run a whole suite against the *wrong checkout* and never notice.

```powershell
# cad-core (1481)
$env:PYTHONPATH = "$PWD\packages\cad-core\src"
.\.venv\Scripts\python.exe -m unittest discover -s packages\cad-core	ests -t packages\cad-core	ests

# API + AI (591). Run from appspi	ests: the modules import
# `provider_stubs` as a top-level module, so a named single-test run needs
# that directory on sys.path.
cd appspi	ests
$env:PYTHONPATH = "<repo>\packages\cad-core\src;<repo>ppspi\src;."
..\..\..\.venv\Scripts\python.exe -m unittest discover -s . -t .

# one class, or one test
..\..\..\.venv\Scripts\python.exe -m unittest test_text_to_cad_ai.TestAnthropicProvider
..\..\..\.venv\Scripts\python.exe -m unittest test_text_to_cad_ai.TestAnthropicProvider.test_no_tools_are_ever_sent

# frontend, and the launcher
cd apps\web; npm test; npm run typecheck
.\scripts\Test-DevLauncher.ps1
```

The POSIX forms below are the originals and remain correct on Linux/macOS.

```sh
# --- cad-core tests (1481) ---
PYTHONPATH=packages/cad-core/src \
  python3 -m unittest discover -s packages/cad-core/tests -t packages/cad-core/tests

# --- API + AI tests (547) ---
cd apps/api && PYTHONPATH=../../packages/cad-core/src:src \
  python3 -m unittest discover -s tests -t tests

# --- frontend ---
cd apps/web
npm install
npm test          # vitest, 87 tests
npm run typecheck # tsc --noEmit
npm run e2e       # real stack in a real browser (Playwright)

# --- AI evaluation (see §7 for the live form) ---
export PYTHONPATH=packages/cad-core/src:apps/api/src
cd apps/api
python -m cad_ai.evaluation --check
python -m cad_ai.evaluation --self-check --pace 0
python -m cad_ai.evaluation --list

# --- start the API ---
# CAD_API_CACHE_ROOT is REQUIRED: the app raises rather than guess a path.
pip install -e packages/cad-core -e "apps/api[test,serve]"
cd apps/api && PYTHONPATH=../../packages/cad-core/src:src \
  CAD_API_CACHE_ROOT=/tmp/cad-cache \
  python3 -m uvicorn --factory cad_api.app:app_from_environment --port 8000

# --- start the frontend (proxies /api to the backend) ---
cd apps/web && npm run dev
```

Optional install extras: `ai` (Anthropic SDK), `ai-gemini` (google-genai).
Neither is needed for the core or the HTTP transport.

---

## 15. Windows: platform traps that look like bugs

This is the primary development machine (Windows 11, **Windows PowerShell
5.1**). Every item below was a red test or a wrong conclusion, diagnosed and
fixed in Stage 32. If something fails here, check this list before changing
CAD code.

- **`PYTHONPATH` must be `;`-separated.** A `:`-separated value is silently
  ignored, and imports then fall back to the editable installs — so a suite
  can run green against a completely different checkout. This produced a
  confidently wrong conclusion once already.
- **The OpenCascade native layer aborts at interpreter shutdown.** A child
  process can do all its work, print its result, and *then* exit with
  `0xC0000374` (heap corruption) or `0xC0000005` (access violation) during
  finalisation. Tests that assert `returncode == 0` therefore flush and
  `os._exit(0)` once they have proved their point. A bare `python -c` that
  imports `cad_core` may also "segfault" after printing — the output is still
  valid.
- **`os.kill(pid, 0)` is not a liveness probe here.** `os.kill` maps onto
  `TerminateProcess` and rejects signal 0 with `WinError 87` whether or not
  the process exists. `test_isolated_execution.process_is_running()` does it
  properly (`OpenProcess` + `WaitForSingleObject`).
- **`Path.read_text()` uses the locale codepage (cp1252), not UTF-8.**
  `edge_selection.py` has box-drawing characters in a docstring diagram, so
  any test parsing source must pass `encoding="utf-8"` explicitly.
- **A child given a hand-built minimal environment needs the home
  variables.** Drop `USERPROFILE`/`HOMEDRIVE`/`HOMEPATH` and `ezdxf` — which
  CadQuery's exporters import at module scope — raises *"Could not determine
  home directory"* from `Path("~").expanduser()`. Use
  `_minimal_child_environment()` in `test_http_api.py`.
- **Never watch the shared `%TEMP%` in a test.** Any other process writing
  there fails it. Redirect `tempfile.tempdir` to a private directory instead.
- **Vite binds `::1` only; uvicorn binds `127.0.0.1`.** An IPv4-only port
  probe reports Vite's port free and starts a duplicate server. Note also
  that `New-Object System.Net.Sockets.TcpClient` with no argument creates an
  **IPv4** socket, so it can never connect to `::1`.
- **`node` and `npm` are not on `PATH` in non-interactive shells**, though
  Node is installed. Resolve them explicitly (`Resolve-NpmCommand`,
  `Resolve-NodeCommand`), and put Node's directory on the `PATH` handed to
  Vite — `npm.cmd` shells out to `node`.
- **PowerShell 5.1 has no `?:`, no `??`, and no `&&`/`||` chaining.** Scripts
  here must avoid them.

## 16. Keeping a running demo safe

The demo runs on **backend 8000 / frontend 5173**; experiments belong on
**8001 / 5174**, with the experimental frontend proxying to
`http://127.0.0.1:8001` (`start.ps1 -BackendPort 8001 -FrontendPort 5174`).
Both stacks run side by side.

When a running demo must not change, prefer a **`git worktree`** for the
experiment over checking a branch out in the main tree. A branch switch
rewrites the files the Vite dev server watches, and this project has more than
once had its running state depend on *uncommitted* files. A worktree leaves
the demo's files, branch and processes untouched by construction. Note that
`.venv` and `node_modules` are gitignored, so a fresh worktree needs
directory junctions to the main tree's copies, and `-DotEnvPath` to read the
one credential rather than copying it.

A running backend has already imported its modules: editing `prompt.py` does
not affect a live server until it restarts. That is what makes it safe to
merge backend changes while a demo is up.

---

## 17. Repository structure

```
/
├── CLAUDE.md                     this handoff
├── README.md
├── docs/                         architecture + technical documentation
│   ├── cad-specification.md      ** the authoritative V1 contract **
│   ├── local-cad-engine.md  edge-selection.md  render-representation.md
│   ├── step-export.md  iges-export.md  stl-export.md
│   ├── cad-document-serialization.md  build-job-layer.md
│   ├── local-build-cache.md  artifact-registry.md  isolated-cad-execution.md
│   ├── application-service.md  api-contract.md  http-api.md
│   ├── artifact-delivery.md  build-result-retrieval.md  web-application.md
│   ├── text-to-cad-ai.md  ai-evaluation.md  ai-provider-comparison.md
│   ├── featurescript-generation.md  onshape-mcp-boundary.md
│   └── evaluation-baselines/     preserved live benchmark runs
├── packages/cad-core/
│   ├── src/cad_core/             model, validator, rules, errors, geometry,
│   │                             serialization, local_cad, edge_selection,
│   │                             render_model, {step,iges,stl}_export,
│   │                             build_job, local_build_cache,
│   │                             artifact_registry, isolated_execution,
│   │                             isolated_worker, application_service,
│   │                             api_contract, featurescript,
│   │                             onshape_adapter, onshape_fakes
│   └── tests/                    19 test modules
├── apps/api/
│   ├── src/cad_api/              app, schemas, artifacts, builds, status, config
│   ├── src/cad_ai/               provider, anthropic_provider, gemini_provider,
│   │                             prompt, specification, generation, config,
│   │                             corpus, comparison, evaluation
│   └── tests/                    7 test modules + provider_stubs.py
├── apps/web/
│   ├── src/                      main, app, api, viewer, render-model, example
│   ├── tests/                    app, boundary, render-model + fixtures
│   └── e2e/run.mjs
└── tests/                        reserved for integration / e2e
```

---

## 18. Git / branch state

- **Branch:** `claude/text-to-cad-skeleton-r946xr`
- **Tracks:** `origin/claude/text-to-cad-skeleton-r946xr`
  (`https://github.com/Atharvabhangale/AI-assisted-text-to-CAD-application`)
- **History:** Stage 0 → Stage 32, all on this branch, pushed to origin.
- **Recent commits** (newest first):
  - `4059b87` Fix the stale identity pins, and the Windows-only test failures
  - `55c59cb` Prompt 2026-09-09.2: teach Haiku that nothing joins, and that locatives answer
  - `b94d4e9` Stage 30 and the local launcher: the working demo state
  - `950077d` Add Claude Code project handoff context
  - `18eb9ce` Stage 29: separate provider reliability from real model quality
- **`experiment/haiku-generation-reliability`** holds the same work as its own
  history (its tree is identical to this branch's); it was cherry-picked here,
  not merged, so the SHAs differ. Its worktree, if still present, sits beside
  the repo and can be removed with `git worktree remove`.
- **Stage 30's work lived only in an uncommitted working tree** until Stage 32
  committed it as `b94d4e9`. Worth knowing: this repository has had its
  running demo depend on files that existed nowhere in git (see §16).

For the current HEAD and state, run `git log --oneline -5` and `git status` —
they are authoritative, this list is a snapshot.
