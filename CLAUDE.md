# CLAUDE.md — project handoff for Claude Code sessions

This file exists so a new session can continue the project without re-deriving
it. It describes the repository as it actually is, verified against the code,
the docs and the git history at the commit that added it. Where something is
unbuilt, unverified or unmeasured, it says so — that is the point of the file.

`docs/cad-specification.md` is the source of truth for the CAD contract. This
file summarizes; it never overrides.

**Sections 1–15 describe the stable branch.** A second branch,
`experiment/cad-operation-graph`, carries Stages 32–42 — an alternative CAD
representation and a second CAD backend — and is checked out as a worktree at
`/home/user/cad-experiment`. It is never merged. **See §17 before assuming
this file describes everything that exists.**

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

Every stage of that chain exists today **except the join between the first two
and the rest**: the AI layer produces a validated CAD document, and the browser
consumes a CAD document, but no HTTP endpoint connects them yet. A human
currently pastes a document into the page. Closing that gap is the main
remaining product step (see §10).

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
| `app.py` | The ASGI app: six routes, no CAD logic (`create_app`, `app_from_environment`) |
| `schemas.py` | Pydantic models for the **transport envelope only** (`ValidateBody`, `BuildBody`) |
| `artifacts.py` | Safe logical-id → bytes resolution (`ArtifactResolver`) |
| `builds.py` | Read-only build retrieval by build key (`BuildRetriever`) |
| `status.py` | The HTTP status mapping, in one place |
| `config.py` | `CAD_API_CACHE_ROOT`, `CAD_API_TIMEOUT_SECONDS` |

Docs: `docs/http-api.md`, `docs/artifact-delivery.md`,
`docs/build-result-retrieval.md`.

### `apps/api/src/cad_ai` — the AI interpretation layer

| Module | Purpose |
|---|---|
| `provider.py` | The provider boundary: `TextToCadModel`, `ModelRequest`, `ModelResponse`, `ProviderError`, `ProviderErrorKind` — **no vendor types** |
| `anthropic_provider.py` | Anthropic Messages API adapter (`AnthropicTextToCadModel`) |
| `gemini_provider.py` | Google `google-genai` adapter (`GeminiTextToCadModel`) |
| `prompt.py` | The single system prompt, versioned + fingerprinted (`system_prompt`, `prompt_fingerprint`) |
| `specification.py` | Model-facing view of the spec, **derived** from `docs/cad-specification.md` (`specification_text`, `response_schema`) |
| `generation.py` | `TextToCadService`: description in, `AiGenerationResult` out, validated by `cad_core` |
| `config.py` | Provider/model/timeout from the environment; **never reads a credential value** |
| `corpus.py` | The 35-case benchmark corpus (`CORPUS_VERSION = "1.0.0"`) |
| `comparison.py` | Field-level document comparison + semantic error taxonomy |
| `evaluation.py` | The benchmark harness and its CLI |

Docs: `docs/text-to-cad-ai.md`, `docs/ai-evaluation.md`,
`docs/ai-provider-comparison.md`.

### `apps/web` — the browser viewer

`main.ts`, `app.ts`, `api.ts`, `viewer.ts` (Three.js), `render-model.ts`.
Docs: `docs/web-application.md`.

---

## 5. Test status

Measured on this commit, by actually running the suites. Nothing here is
estimated.

| Suite | Result |
|---|---|
| cad-core (`unittest`) | **1481 passed** |
| API + AI (`unittest`) | **547 passed, 2 skipped** |
| Frontend (`vitest`) | **87 passed** (3 files) |
| Frontend typecheck (`tsc --noEmit`) | **clean** |

On the **experiment branch** there is a fourth, separate suite:
`tests_experimental`, **874 passed / 2 skipped** with FreeCAD present (the two
skips are its unavailable-only cases; without FreeCAD, 33 skip instead). It
never runs as part of the three above. See §17.

Run the **full** experimental suite before finishing a stage there, not just
the modules you touched: package-wide guard tests in older modules are
routinely tripped by new ones, and focused subsets have missed that more than
once.

**The 2 skips are deliberate and must stay skipped by default.** They are the
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
`PROMPT_VERSION = "2026-09-08.1"`, fingerprint
`2b3e3395ec6efee0fe252cf88207e981dcdecfdb88ea847f075ce20a5ad9ba52`. The
spec text the model sees is derived from `docs/cad-specification.md` rather
than retyped, so the two cannot drift. Every evaluation run records the
version and fingerprint, and a regression test pins them.

**Supported natural-language subset:** one part, a single `box` **or** a
single `cylinder`, with dimensions, optional position, optional axis, and
units stated in the request. Everything else — holes, cuts, fillets,
chamfers, multi-feature parts, assemblies, tolerances, materials — is
`UNSUPPORTED` by prompt policy. Note this is a **prompt policy, not a
validation rule**: a box-plus-`through_hole` document is perfectly valid V1
CAD, so the validator accepts it and the *evaluator* flags it as
`UNSUPPORTED_FEATURE_ACCEPTED`.

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

**No HTTP endpoint exposes the AI layer.** It is library + CLI only.

### Live benchmark status — read this before making any quality claim

**A complete real-model benchmark does not exist.** The best run to date
measured **20 of 35 cases**.

History, from the evaluation results and git log:

- **Anthropic: run live at Stage 40, on the experiment branch** (§17). Before
  that it never had been. The *production* 35-case corpus has still never been
  run on Anthropic — Stage 40 used its own 13-case corpus — so there is still
  no Anthropic number comparable to the Gemini figures below.
- **Gemini `gemini-2.5-pro` (the current code default): 404.** The API replied
  that the model "is no longer available to new users". The default in
  `config.py` is therefore **not usable as-is** — see §7.
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

- **Environment variable: `GEMINI_API_KEY`.**
- **Anthropic: the code reads `ANTHROPIC_API_KEY`, but this platform reserves
  that name and strips it.** A key supplied by the operator arrives as
  **`CAD_ANTHROPIC_API_KEY`** instead. Nothing in `cad_ai` reads that name, so
  a caller must bridge it into `ANTHROPIC_API_KEY` for the process it starts;
  `cad_experimental.representation_comparison.bridge_credential()` is the one
  place that does, and it never returns or logs the value. Verified working
  against `https://api.anthropic.com` with `claude-haiku-4-5-20251001`.
- **How availability is checked:** `cad_ai.config.credential_available()` reads
  only whether the variable is *present and non-empty*. The value is never
  returned, stored, logged, compared or printed. `AiConfig` has no credential
  field at all, so a config dump cannot leak one.
- **Secrets are never committed.** No key appears in source, tests, docs or
  saved evaluation results; the saved results were scanned to confirm it.
  Tests use an obviously synthetic placeholder. `.env*` is gitignored.
  **Never put a key in this file, in code, or in a commit.**
- **Current model configuration.** `DEFAULT_MODELS["gemini"]` is
  **`gemini-2.5-pro`, which returns 404** ("no longer available to new
  users"). Until that default is changed, a live Gemini run **must** override
  it: `CAD_AI_MODEL=gemini-2.5-flash`. This is a real, reproducible
  configuration bug and a good first cleanup.
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
- **Build flow:** `POST /validate` → `POST /build` → `GET
  /builds/{key}/render` → draw.
- **Export flow:** `GET /artifacts/{id}` for STEP/IGES/STL bytes.
- **Current input type:** a **JSON CAD document in a textarea**. That is the
  only editable field on the page, and a boundary test asserts it.

**The frontend is a client, not the CAD engine.** Every dimension, volume,
solid count and triangle count it displays is a field the backend sent. It
imports no kernel and computes no geometry.

**Missing for the final text-to-CAD UX:**

- a natural-language input box (the headline gap);
- an HTTP endpoint to call for interpretation — none exists yet (§9);
- a way to surface `NEEDS_CLARIFICATION` questions and collect answers;
- a way to present `UNSUPPORTED` refusals as product copy rather than errors;
- showing the generated document for review before/alongside the build;
- iteration/edit-and-rebuild, history, and any notion of a saved project.

---

## 9. HTTP API status

Six routes, from `apps/api/src/cad_api/app.py`. Docs: `docs/http-api.md`.

| Route | Purpose |
|---|---|
| `GET /health` | liveness |
| `POST /validate` | validate a CAD document; returns the validation result. Builds nothing |
| `POST /build` | validate + build a document with the requested outputs; returns the build result and artifact references |
| `GET /builds/{build_key}` | read-only retrieval of a previously published build |
| `GET /builds/{build_key}/render` | the `RenderModel` for a build, for the viewer |
| `GET /artifacts/{artifact_id}` | deliver artifact bytes (STEP / IGES / STL / geometry / render) |

Relationships: `POST /build` returns a **build key** and **artifact ids**; the
two `GET` routes exchange those identifiers for the build result and for bytes.
Artifact ids are **logical** — the resolver maps them to storage, and the
storage path is never exposed or accepted.

**There is no AI endpoint.** Natural-language interpretation is not reachable
over HTTP. Adding it is a deliberate future step, not an oversight.

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
  F and G are unmeasured. Anthropic is unmeasured *on this corpus* (it was
  measured on the experiment branch's own 13-case corpus — §17).
- **Stages 32–42 run on the experiment branch**, not here. §17.

**NEXT**

1. **Complete the 35/35 baseline** — work around the free-tier daily
   allowance (§7). Until this exists, no full quality claim is possible.
2. **Fix the unusable Gemini default model** (`gemini-2.5-pro` → an available
   Flash model).
3. **Prompt improvement**, driven by the measured failures: the A6 dimension
   swap and the B2/B3 over-clarification. Change the prompt, then re-measure —
   never change the corpus or the expected documents to improve a score.

**FUTURE**

4. **AI repair / verification loop** — deliberately absent today; would need
   its own design so it never becomes silent correction.
5. **Natural-language frontend integration** — the AI HTTP endpoint plus the
   UX for clarification questions and unsupported refusals.
6. **MVP**: description → geometry → export, end to end, in the browser.
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
- **Adversarial and semantic-edge-case behavior is entirely unmeasured.**
- **Provider availability is unreliable**: a 404 on the default Gemini model
  and a ~20-request/day free-tier allowance.
- **Anthropic has never been exercised live.**
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

The experiment branch has its own suite and its own commands — §17.

---

## 15. Repository structure

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

On the **experiment branch** only, three more trees exist:
`apps/api/src/cad_experimental/`, `apps/api/tests_experimental/` and
`apps/web-experimental/`. See §17.

---

## 16. Git / branch state

**Two branches, and they are kept apart deliberately.**

- **`claude/text-to-cad-skeleton-r946xr`** — the stable MVP. Stages 0–29 plus
  this handoff. Everything described in §1–§15 is this branch.
- **`experiment/cad-operation-graph`** — Stages 32–42, an alternative CAD
  representation and a second CAD backend. See **§17**. It is **never merged**
  into stable, and every stage has verified that stable stays byte-identical.

Both are on
`https://github.com/Atharvabhangale/AI-assisted-text-to-CAD-application`.

The experiment is checked out as a **git worktree** at `/home/user/cad-experiment`,
so both branches are on disk at once. `git worktree list` shows them.

- **History:** 35 implementation commits, Stage 0 → Stage 29, all on the stable
  branch, plus the commit that added this handoff; 13 more on the experiment
  branch.
- **Recent commits** (newest last; the handoff commit sits on top of these):
  - `18eb9ce` Stage 29: separate provider reliability from real model quality
  - `bf8518f` Stage 28A: complete and gate the Gemini provider; defer the live benchmark
  - `9a4a6af` Add a Gemini provider behind the existing model boundary
  - `dcb5751` Stage 27: real-model text-to-CAD evaluation harness
  - `4915485` Stage 26: natural language to CAD specification

For the current HEAD and state, run `git log --oneline -5` and `git status` —
they are authoritative, this list is a snapshot.

---

## 17. The experiment branch: `experiment/cad-operation-graph`

Stages 32–42. **Not merged, and not to be merged** — every stage verifies that
`packages/`, `apps/api/src/cad_api`, `apps/api/src/cad_ai`, `apps/api/tests`
and `apps/web/` stay byte-identical to stable. It is checked out as a worktree
at `/home/user/cad-experiment`.

It asks two questions the stable branch cannot: **is a different CAD
representation easier for a model to generate correctly?** and **is CadQuery
the right engine?**

Full detail: `docs/experimental-operation-plan.md` (~1100 lines, current).

### What it adds

| Path | What |
|---|---|
| `apps/api/src/cad_experimental/` | the operation-plan language, its parser, plan validator (P1–P26), V1 adapter, prompt, FastAPI app, harness, and both CAD backends |
| `apps/api/tests_experimental/` | its tests, run separately from the production suite |
| `apps/web-experimental/` | a second Vite app on port 5174, reusing the stable viewer by import |
| `docs/evaluation-baselines/stage40-*/` | the live Haiku run, raw output and diagnosis |

The **CAD operation plan** is a flatter alternative to the V1 document: no
`units`, no `schema_version`, no `features` — just ordered `operations`, each
`{id, type, target?, tools?, parameters?}`. Nine operation types; **six are
executable** (the V1 features). `sketch`, `extrude` and `revolve` are
represented and validated and then explicitly refused at the execution
boundary (`adapter.ExecutionUnsupported`) — never approximated.

### Findings that cost real money or hours to establish

**Anthropic structured output has four limits at once** (Stage 41, measured
against the live API, not documented anywhere):

| Rule | Measured |
|---|---|
| optional properties, whole document | ≤ 24 |
| optional properties, **any single object** | ≤ ~14 (a 20-optional object alone is "too complex") |
| compiled grammar size | 8 operation branches accepted; **any 9th refused**, even stripped to one field |
| `oneOf` | rejected — `anyOf` only |
| `additionalProperties: false` | **mandatory on every object**; an open object cannot be expressed |
| `exclusiveMinimum`/`maximum`/`maxItems`/`min\|maxLength` | rejected; `minItems` only 0 or 1 |
| unused `$defs` | still cost grammar budget; `$ref` does **not** shrink it |

Consequence: `plan.plan_schema()` (all nine) is refused; `plan.provider_schema()`
(the executable six) is accepted and is what `generation.py` sends.

**Live Claude Haiku 4.5, 130 calls** (Stage 40): with structured output
unavailable to one side and therefore disabled for both, **both representations
scored 0%** — every answer came back in a markdown fence, and both parsers
refuse fences by design. Replaying that recorded output through the same frozen
machinery with fence tolerance gives V1 **24.6%** and the plan **86.2%** (40/40
vs 0/40 on the buildable cases) — a *diagnostic*, not a score. V1's failures
were envelope-shaped (prose, or a document with no `status` wrapper).

**FreeCAD is installable here, but not the obvious way** (Stage 42): there is
no pip distribution and it is **not in Ubuntu 24.04**. It came from the
official AppImage (649 MB, extracts to 2.4 GB), gives **FreeCAD 1.0.0**, and
imports headlessly into this project's Python *alongside* CadQuery. Its
bundled `libssl` conflicts with the system `libcrypto`, so `LD_LIBRARY_PATH`
must be set **before Python starts** — it cannot be fixed from inside a
process. Both engines produce **bit-identical volumes** on all six golden
parts.

### Commands

```sh
cd /home/user/cad-experiment/apps/api
export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental

# the experimental suite (production's suite is separate and unaffected)
python3 -m unittest discover -s tests_experimental -t tests_experimental
python3 -m unittest tests_experimental.test_sketch                 # one module
python3 -m unittest tests_experimental.test_sketch.SketchParsingTests.test_a_sketch_parses

# fixtures, built for real by the CAD engine; calls no model
python3 -m cad_experimental.local_plan_provider --list
python3 -m cad_experimental.local_plan_provider

# the frozen V1-vs-plan comparison. --live spends money and is required
python3 -m cad_experimental.representation_comparison --check
python3 -m cad_experimental.representation_comparison --live --attempts 5 --out run.json
python3 -m cad_experimental.comparison_diagnosis run.json

# the FreeCAD half of the backend tests (they SKIP without these two vars)
export CAD_FREECAD_HOME=/home/user/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib
python3 -m unittest tests_experimental.test_cad_backends

# the experimental stack: backend 8001, frontend 5174 (stable keeps 8000/5173)
CAD_EXPERIMENTAL_CACHE_ROOT=/tmp/exp-cache python3 -m uvicorn \
  --factory cad_experimental.app:app_from_environment --port 8001
cd ../web-experimental && npx vite --port 5174 --strictPort
```

### Invariants specific to the experiment

- **`CAD_BACKEND` defaults to `cadquery` and stays that way.** FreeCAD is
  experimental. `resolve_backend()` **never falls back** — an unavailable
  backend raises, because a caller who asked for one engine and silently got
  another cannot know which engine built their part.
- **Neither parser strips markdown fences and neither repairs output.** That
  is a deliberate measurement decision on both sides, not an oversight.
- **The corpus, expected documents and scoring in `comparison_corpus.py` are a
  frozen instrument.** Fix the prompt or the code, then re-measure; never edit
  an expectation after seeing a score.
- Stage 40's harness keeps structured output **off** on purpose so its recorded
  run stays reproducible, even though Stage 41 made it available.
- A known prompt bug is **deliberately unfixed**: the Stage 37/38 wording tells
  the model an extrude "cannot be built", and Haiku reads that as *unsupported*
  and refuses (5/5 on the revolve case). Fixing it is its own stage, so the
  cause stays clean.
