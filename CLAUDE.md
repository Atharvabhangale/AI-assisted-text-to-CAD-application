# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Which branch are you on? Run `git branch --show-current` first.

**This file is carried identically on both branches** (commit `6f32ce8`), but
it is written from the stable branch's point of view. Read it accordingly:

- **Sections 1–18 describe `claude/text-to-cad-skeleton-r946xr`** (stable).
- **Section 19 governs `experiment/cad-operation-graph`**, which forked at
  `950077d` and is a different tree with its own stage numbering.

On `experiment/cad-operation-graph` these sections of 1–18 are **wrong as
written**, and §19 is authoritative instead:

- **`start.ps1` and `stop.ps1` do not exist on this branch.** §14 presents
  them as "the normal way in"; they arrived on stable after the fork. Use the
  manual `uvicorn` / `npm run dev` sequence, or §19's commands.
- **§5's test counts are stable's.** On this branch the current measured
  figures are **`tests_experimental` 1979 passed, 5 skipped** and
  **cad-core 1481 passed**, both on Linux with FreeCAD 1.0.0 present
  (Stage 72). §5's own note carries the older Windows figures as history.
  §5's "no known failing tests" is a statement about stable only.
- **§17's structure map omits this branch's three experimental trees**:
  `apps/api/src/cad_experimental/`, `apps/api/tests_experimental/` and
  `apps/web-experimental/`. They exist here and are described in §19.
- **§6's "Nothing joins" is about V1, and V1 only.** The stable V1 document
  genuinely has no `union`, and §6 and §11 are right to say so. **This
  branch's operation plan HAS `union`** — it is the eleventh operation type,
  both CAD backends implement it, and the graph executor builds it. Do not
  carry §6's rule across; see §19.
- **§7's credential rule is superseded.** It says Claude Code Web runs only
  on a local fixture provider. The current policy is §19's **AI provider
  usage policy**: when a credential is available, a real provider is used,
  and live calls are encouraged.

Everything else in 1–18 — the CAD contract, the architectural principles, the
invariants in §12, and the Windows traps in §15 — applies to both branches.

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
| `edge_selection.py` | Deterministic edge selection | `select_edges`, `line_direction`, `is_straight_edge` | Only `all` / `axis_parallel`; seam semantics unresolved here (§11), answered on the experiment branch (§19) | `docs/edge-selection.md` |
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

**On stable** there are no known failing tests, and no test is quarantined,
skipped or weakened to make the suite pass.

**On `experiment/cad-operation-graph` the numbers above are stable's.**

**CURRENT (Stage 74, Linux, FreeCAD 1.0.0 present):** `tests_experimental`
**1979 passed, 5 skipped, 0 failed**; cad-core **1481 passed, 0 failed**.
The six cad-core errors described below are **Windows-only** and do not
reproduce on Linux.

**HISTORICAL**, kept because each figure describes a real environment:
cad-core runs **1481 tests with 6 errors** on Windows, and
`tests_experimental` was **897 passed, 33 skipped** at
Stage 43 on Windows (the skips are the FreeCAD backend, absent there) and is
**1263 passed, 33 skipped (1296 collected)** as of Stage 48, measured on a
Linux container without FreeCAD. Note for anyone reproducing that figure in a
fresh container: `cadquery` and `fastapi` are not installed by default there,
and without them six test modules fail to *import* and their tests are never
collected at all — a red suite that is an environment gap, not a regression.
The six errors
are **pre-existing, in test code, and unrelated to CAD** — they are two of
§15's own traps, in a branch that forked before the stable branch's Stage 32
Windows fixes and so never received them:

- **three layering tests** call `Path.read_text()` with no explicit encoding
  against `edge_selection.py`, whose docstring diagram has box-drawing
  characters that cp1252 cannot decode;
- **three isolation tests** use `os.kill(pid, 0)` as a liveness probe, which
  on Windows fails with `WinError 87` whether or not the process exists.

Both were fixed on stable and the fixes have not been carried across. They
are strictly fewer than before `2f4fea7` — verified by stashing that commit's
changes and re-running the same subset — so they are not a regression from
it. No test here is quarantined or weakened.

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
- **Nothing in the repository loads `apps/api/.env`.** `cad_ai/config.py` says
  so in its own module docstring, and every layer reads `os.environ` only.
  The file exists and holds the real key, but it is inert: **the caller must
  export it into the process** before a live run. On stable `start.ps1` does
  that; on `experiment/cad-operation-graph` there is no launcher, so the
  process that runs the benchmark has to set the variable itself. A key
  present in your own interactive shell does **not** reach a spawned tool
  process. Budget for this — it is the most common reason a live run reports
  "credential absent" while the file plainly contains one.
- **Local live Anthropic runs use the real key** from that file, against
  `claude-haiku-4-5-20251001`.
- **SUPERSEDED — this row used to forbid Claude Code Web a real model**,
  directing the experimental work to `cad_experimental.local_plan_provider`
  and its fixture plans instead. **That restriction is retired.** See §19's
  *AI provider usage policy*:
  where a credential is available, Claude Code Web uses a real provider, and
  live calls are encouraged for schema experiments, semantic-quality
  measurement and product verification. Kept as a pointer rather than
  deleted, because a reader who remembers the old rule needs to find out it
  changed.
- **What has NOT changed:** `local_plan_provider` still exists and is still
  the clearly labelled local development provider, serving
  developer-written fixture plans and calling no model. **Never describe a
  fixture result as a Haiku result, or as a model result of any kind** — it
  says nothing about model quality, and every fixture answer is stamped
  `source: LOCAL_DEVELOPMENT_PLAN` / `is_live_model_result: false` so it
  cannot be mistaken for one.
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

  **On `experiment/cad-operation-graph` this was also a production bug, and
  is now fixed (`2f4fea7`).** The isolated worker's entrypoint ran
  `sys.exit(main())`, so every *successful* isolated build on Windows was
  followed by the teardown abort. The host classifies on the response first
  and the exit code second, so the mismatch surfaced as `the child reported
  'succeeded' but exited with 3221226356` — a protocol error on a correct
  build. `isolated_worker.py` now flushes `stdout`/`stderr` and calls
  `os._exit(code)` with the code `main` chose, skipping finalisation. This is
  safe because nothing in `cad_core` registers an `atexit` hook, a `__del__`
  or a finaliser, and the response is already published with `os.replace`
  before `main` returns: finalisation had no work left to do, only a way to
  fail. **Do not "restore" the `sys.exit` form.**
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

  **On `experiment/cad-operation-graph` this was a production bug in the
  isolated worker, not merely a test concern, and is now fixed (`2f4fea7`).**
  `INHERITED_ENVIRONMENT_NAMES` in `isolated_execution.py` is an allowlist,
  and it did not include the home variables — so the CAD kernel's own child
  process hit exactly this failure and **every isolated build on Windows
  failed**, reported as `the worker failed with an unhandled internal error`
  before any geometry was touched. It went unnoticed because POSIX falls back
  to the `pwd` database when `HOME` is unset, so Linux was unaffected; Windows
  has no such fallback. `HOME`, `USERPROFILE`, `HOMEDRIVE` and `HOMEPATH` are
  now inherited on the same terms as `PATH` and `SYSTEMROOT`. A path to the
  user's profile is not a credential, and the allowlist still drops
  everything else, so no key can reach the child.

  Diagnostic worth reusing: **committed local fixtures failed identically to
  model-driven runs.** When a whole pipeline fails, run the deterministic
  fixtures with no model involved — if they fail the same way, the problem is
  the environment, and no amount of model output will show you that.
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
- **`experiment/cad-operation-graph`** is a *separate* line of work -- an
  alternative CAD representation and a second CAD backend, forked at
  `950077d` and never merged. **Its stages are also numbered 32+, and those
  numbers collide with this branch's.** See §19.
- **`experiment/haiku-generation-reliability`** holds the same work as its own
  history (its tree is identical to this branch's); it was cherry-picked here,
  not merged, so the SHAs differ. Its worktree, if still present, sits beside
  the repo and can be removed with `git worktree remove`.
- **Stage 30's work lived only in an uncommitted working tree** until Stage 32
  committed it as `b94d4e9`. Worth knowing: this repository has had its
  running demo depend on files that existed nowhere in git (see §16).

For the current HEAD and state, run `git log --oneline -5` and `git status` —
they are authoritative, this list is a snapshot.

---

## 19. A second experiment branch: `experiment/cad-operation-graph`

**Read the stage-number warning first.** This branch numbers its own stages
**32–64**. The stable branch *also* has a Stage 30, 31 and 32. **They are
different work and the numbers collide.** "Stage 32" on stable is the Windows
test fixes; "Stage 32" here is the first commit of an alternative CAD
representation. Nothing reconciles them — when reading a commit message, check
which branch it is on.

It **forked at `950077d`**, before Stage 30. So it does **not** contain
`POST /generate`, the natural-language frontend, `start.ps1`/`stop.ps1`, the
Windows fixes, or the `gemini-3.6-flash` change. Its stage reports say the
stable tree is "byte-identical", and that was measured against `950077d` —
**not** against current stable. Reconciling the two branches is unfinished
business.

Checked out as a git worktree beside the repo (`git worktree list`). Full
detail lives on that branch in `docs/experimental-operation-plan.md`; this
section is the index.

### VERIFIED CURRENT STATE — read this first

Everything in this subsection was **measured on the current tree**, not
recalled. Four kinds of statement are kept apart on purpose: **verified
current facts** (here), **historical milestone facts** (the per-stage
subsections below, which are a record and are not rewritten), **known
limitations** and **planned work**.

**Branch and checkpoint.** `experiment/cad-operation-graph`, Stages 32–63
complete and pushed. `git log --oneline -5` and `git status` are
authoritative; this is a snapshot.

**Operation vocabulary — eleven types.** `plan.OPERATION_TYPES` is the
authority:

| tuple | count | members |
|---|---:|---|
| `OPERATION_TYPES` | **11** | box, cylinder, through_hole, subtract, **union**, fillet, chamfer, pattern, sketch, extrude, revolve |
| `DECLARATION_TYPES` | **1** | **`part`** (Stage 71) — produces no geometry; declares one |
| `PLAN_TYPES` | **12** | the geometry vocabulary plus the declarations: what the parser accepts |
| `EXECUTABLE_TYPES` | 8 | `OPERATION_TYPES` minus sketch/extrude/revolve |
| `BUILDABLE_TYPES` | 9 | what the **executor** may carry: the executable types plus the declarations |
| `V1_FEATURE_TYPES` | 6 | the V1 document's own six — **frozen**, and `union` is deliberately not in it |
| `V1_EXPRESSIBLE_TYPES` | 7 | the six plus `pattern`, which expands into one feature per instance |
| `EXECUTOR_ONLY_TYPES` | 2 | `('union', 'part')` — **derived** from `BUILDABLE_TYPES`, not listed |
| `TOOL_MODIFIER_TYPES` | 2 | `subtract`, `union` — both consume their tools |
| `PATTERNABLE_TYPES` | 1 | `through_hole` |
| `MAX_BODIES` | 8 | the most independent bodies one plan may declare |
| `SELECT_MODES` | 4 | all, axis_parallel, straight, circular |

**`OPERATION_TYPES` is the GEOMETRY vocabulary**, and that is now load-bearing:
every provider encoding and the prompt are built from it, so `part` is
deliberately in its own tier. See Stage 71.

Plan rules are **P1–P35**. `sketch`, `extrude` and `revolve` are represented
and validated, then refused at the execution boundary rather than
approximated. `union` has **no V1 document form** (V1 cannot join solids), so
a plan using it is built by the **graph executor**.

**Architecture — provider-independent, and what that means.** The canonical
pipeline is:

```
natural language
  → provider (any) OR a deterministic reader
  → canonical intent            (intent.py, for the assembly grammar)
  → Operation Plan              (the canonical representation)
  → parser  → validator (P1-P32) → feature graph
  → backend (CadQuery | FreeCAD)
  → RenderModel
```

**Verified structurally:** none of the 19 canonical-pipeline modules imports
a vendor SDK or `cad_ai`; `edge_semantics.py` imports only the standard
library. Asserted by `test_provider_policy.py`. **Vendor-neutral core, and
live vendor calls are encouraged** — see the policy below.

**Two deterministic routes, both reaching the same kernel:**
- `intent.py` — the counted plate-assembly grammar → canonical intent →
  `lower_to_plan`;
- `normalize.py` — eight general readers (box, cylinder, corner_holes,
  pattern, centre_hole, edge_treatment, remove, resize).

Both go through the same parser and validator; neither is a shortcut.
`_body_id` reads `history.plan_history(...).live_bodies` — **the one**
solid-set walk.

**Evidence layer.** `questions.py` answers eleven kinds of question about the
built part and labels every number `MEASURED` (the kernel measured it),
`DECLARED` (the plan asks for it) or `CALCULATED` (worked out, with the
working shown). Mass **requires** a named material or stated density.

**Interpretation and refusal semantics — three distinct answers:**

| request | answer | why |
|---|---|---|
| a grammar recognised it and cannot honour it | **200 `refused`** | e.g. *"a 60 mm hole does not fit through a 40 mm section"* |
| no grammar claimed it, no model configured | **503 `unavailable`** | a question for a model |
| answerable but underspecified | **200 `answered`** | asks for what it needs (mass with no material) |

Conflating the first two was a real bug; `Interpretation.refused` is separate
from `Interpretation.error` for exactly this reason.

**Live provider — VERIFIED (Stage 63).** Model
**`claude-haiku-4-5-20251001`**, 5 attempts at the golden six-plate request:

| | |
|---|---|
| encoding sent | `strict_selector_union` |
| inlined characters | **3628** |
| fingerprint | `07ab6305e836271ec0d1b49baa62e0fb3ff312aafeefa21e8e5b0b48de119ec3` |
| branches | **5** — box, cylinder, through_hole, `subtract\|union`, `fillet\|chamfer` |
| prompt | `2026-09-18.1` / `aa0a407bd02b18e4` / 26081 chars — superseded by Stage 65 |
| grammar compiled | **YES, 5/5** — `structured_output` true, `stop_reason` `end_turn`, **0/5 fenced** |
| model used `union` | **4/5** |
| **built** | **0/5** |

**The exact failure is P11, and nothing else** — all 24 problems across the
four plans. The model pointed every hole at `assembly`, the union
operation's **own** id, instead of `plate_long_1`, the target whose id a
union keeps. The fifth attempt asked for clarification.

Stage 63 read that as a model-behaviour finding rather than a prompt gap,
because the prompt already stated the rule. **Stage 65 measured it and that
reading was wrong** — see below. Nothing was repaired and no validation was
weakened at either stage; there is no repair loop by design.

**The deterministic reader builds the same sentence correctly** (one valid
solid, 11492.035526276897 mm³). That is `DETERMINISTIC`, and it says nothing
about the model.

**Stage 65 — P11 measured, and largely removed.** **45 live calls** to
`claude-haiku-4-5-20251001`, nine arms, one variable at a time; every arm's
raw output, validation and build result is in
`docs/evaluation-baselines/stage65-union-target/`.

| | |
|---|---|
| prompt now | `2026-09-18.2` / `978976195f8fd201` / 26723 chars |
| encoding | `strict_selector_union` (3628, unchanged) |
| P11 on the golden six-plate request | **5/5 → 1/5** |
| post-union targeting correct | **0/5 → 4/5** |
| built, on a two-plate bracket | **2/5**, `MODEL_GENERATED` |
| six-plate box built | **still 0/5** |

Three plausible fixes were measured **first and changed nothing**: adding a
union worked example (0/5), rewriting the id-naming advice (0/5), and adding
`description` text to every `target` in the provider schema (0/5). A fourth
arm, `X1`, asked whether the failure was complexity rather than the rule: the
simplest possible union request failed **5/5** on the unmodified prompt, so it
was the rule.

What moved it was **removing an affordance, not adding prose**. The union
section closed with *"a `through_hole` bores through the shell, not through a
loose plate"* — naming the post-union solid with a product noun that is no
operation's id, while warning against the one legal target, since a union's
target **is** by name a loose plate. Deleting that, and mandating the union's
own id be the verb `fuse`, produced the numbers above. The controlled
confirmation is inside the run: E1's one remaining P11 is the attempt that
ignored the mandate and named its union `shell`, a body noun.

**Verified success**, rebuilt from the model's own recorded output on real
FreeCAD 1.0.0: volume **20748.67258771281** mm³ against a closed form of
20748.672587712816 (Δ **3.6e-12**), one solid, 9 faces, 21 edges.

**Not fixed, and now the open problem:** the six-plate hollow box still does
not build. With the union-target rule satisfied 4/5, the failures moved to
**rule E1 — the hole's centreline does not intersect the target**. That is
spatial arrangement, a different and harder problem, and it is unmeasured.

**An id's shape is part of the grammar the model is taught.** Three arms of
prose about the rule changed nothing; removing the noun that invited the
wrong answer changed it. `test_union_target_semantics.py` pins both the
semantic rule and the prompt text where it was measured to work.

**Stage 67 — the spatial failure measured, and nothing adopted.** **80 live
calls**, nine prompt arms, one variable at a time; every arm's raw output,
plan, kernel geometry and verdict is in
`docs/evaluation-baselines/stage67-golden-spatial/`. **The prompt is
unchanged at `2026-09-18.3`.** (Stage 69 later moved it to `2026-09-18.5`;
`2026-09-18.4`, which Stage 67 adopted and reverted, exists nowhere in the
history and the number is left burnt so this record stays unambiguous.)

The brief expected "one hole per wall" to dominate. **It occurred once in 80
calls.** On the committed prompt the model already writes three bores 6/8 and
six plates 7/8; Stage 66's defect is essentially gone. What remains is two
disjoint causes — no attempt failed both:

| | |
|---|---|
| **the envelope height** | the prompt derives the third extent as *"the only number the bottom did not already give"*. Here the bottom is 40×20 and the ends are 20×20, so the end plate gives **no new number** and the rule returns nothing. The model then picks 40, 5, 25 or 60. It fails on the prompt's own worked example too (ends 30×30 on a 60×30 bottom). |
| **the far plate of a pair** | placed at `t` or at `extent` instead of `extent - t`. |

Measured failure modes over 80 calls: D wrong plate placement 17, E missing
plate 15, G other 15, C wrong position 12, F duplicate coaxial cut 12,
B wrong direction 8, **A one hole per wall 1**.

**The best arm was rejected, and why matters.** Completing the prompt's worked
example through its three bores (`H7`) took P11 to **0/8**, validation to
**8/8** and spatial success to **4/8** from a 2/8 baseline, reproduced exactly
on a fresh confirmation run. It also **significantly regressed the plate
thickness**: the baseline reads the requested 5 mm on 5/8 attempts, H7 on
**0/16** (Fisher exact, two-sided, **p = 0.00132**). On the strict criterion —
spatially correct **and** built from the 5 mm plate the request specifies — H7
is **0/16** against the baseline's **1/8**. It makes a better-shaped box out of
the wrong material. It was adopted, measured, and reverted; `2026-09-18.4`
exists nowhere in the history.

**The stage's own criterion was too lenient, and an independent review caught
it.** Stage 66 scored a one-bore build correct because its criterion was
envelope plus targeting. Stage 67 replaced that with a kernel-decided
criterion — 1 solid, envelope 40×20×20, **18 faces** (12 planar + 2
cylindrical per bore), 42 edges, volume equal to the closed form — validated
against ground truth before use. But it derived the closed form from
*whatever thickness the model chose*, so parts built from 4 mm plate scored
correct against a request that says 5. **A criterion that grades a part
against its own answer cannot fail it.**

**Kernel evidence, MODEL_GENERATED.** The eight correct plans rebuilt from
recorded output: **10185.628421022 mm³** against the t=4 closed form (delta
**9.09e-12**), 1 solid, 18 faces, 42 edges, envelope (40,20,20) — and
**bit-identical on CadQuery 2.8.0 and FreeCAD 1.0.0**. Real parts, in the
wrong plate.

Two negative results worth their calls: `H6` removed the worked example and
produced the worst result of the stage (P11 6/8, 0/8 correct), which is how
the example is known to be **load-bearing**; and `H6`'s union section is
*shorter* than the baseline's while carrying the worst P11, which **refutes**
the P11-versus-length trend that `H4` and `H5` were built on — it was noise at
n=8.

**The golden request is NOT 5/5.** Best spatial rate 4/8; best strict rate
1/8, on the unmodified prompt. **Multi-body work does not begin.**
`test_enclosure_bores.py` pins the spatial rule in geometry and pins the
thickness lesson; its guards are mutation-tested.

**Stage 68 — the benchmark made scientific, and the ambiguity measured.**
**16 live calls**, 8 per request, on the **unchanged** prompt `2026-09-18.3`.
No prompt variant was built. The only independent variable is which golden
request is sent. Record:
`docs/evaluation-baselines/stage68-benchmark-disambiguation/`.

**The benchmark now has two requests.** `ORIGINAL` is kept verbatim and
ambiguous; `EXPLICIT` states the plate thickness and outer height it leaves
to inference and changes nothing else. Both describe the **same part**, so
one immutable ground truth serves both.

| | original | explicit |
|---|--:|--:|
| plan valid | 5/8 | **8/8** |
| built | 5/8 | **8/8** |
| thickness = 5 mm | 2/8 | **8/8** |
| envelope 40×20×20 | 4/8 | **8/8** |
| volume = closed form | 0/8 | **7/8** |
| **STRICT SUCCESS** | **0/8** | **7/8** |

**What the ambiguity explains.** A (thickness), B (envelope height) and
F (bore direction) fall to **zero** on the explicit request and have stayed
there over 160 further calls. H (wrong target) also read 0/8 here — and
**Stage 69 showed that was under-sampling.** Over 64 pooled explicit
attempts on the same prompt P11 is **6/64**; 0/8 against 3/24 is p = 0.55,
so there is no evidence those two runs differ. P11 *is* lower on the
explicit request than on the ambiguous one (6/64 vs 3/8), but that
comparison is **p = 0.082 — not significant**. So the honest statement is
that comprehension explains A, B and F completely and P11 only partly, and
that **P11 is not gone.** The reading below, written at Stage 68, is kept as
the record of what eight calls appeared to say.

**What it does not explain — and this reading was REFUTED by Stage 69.**
Exactly one failure survived, and it looked like one defect repeated across
an attempt's three bores: the model wrote the `+Z` hole's position triple
and **reused it for `+Y` and `+X`**, leaving `z = 0` on both. That attempt's
plates are identical to the successes; it built at 25 faces, 65 edges,
11351.419374 mm³, 140.6 short. **Stage 69 ran 24 more calls and saw
cross-axis triple reuse 0/24.** The real mechanism is narrower — the model
zeroes **z**, and only z, on bores that do not run along Z — and this
attempt happened to be the case where that also produces a full copy. One
sighting is not a mechanism.

**Ground truth is immutable, and cannot self-reference.** Every expectation
is a constant fixed before any model was called: envelope 40×20×20,
thickness **5 mm**, 6 plates, **3 bores**, 6 openings, bore centrelines at
(20,10,10) across their own axis with the along-axis component free, 1 solid,
18 faces, 42 edges, volume **11492.035526276899**. Stage 67's criterion
derived the expected thickness from the model's own plan
(`t = plan_facts.get("thickness")`); `test_golden_ground_truth.py` makes that
structurally unreachable — `expected()` takes a request NAME and has no
parameter a plan could enter by — and its guards are mutation-tested,
including reintroducing the Stage 67 defect itself.

**A gap this stage found in its own instrument, and fixed.** The first
classifier keyed `E:bore_position` on a build-failure message, so it missed
the surviving failure entirely, because that part *built*; it read
`I:other`, which names nothing. The evaluator now carries a
ground-truth-derived centre check (`CENTRE = ENVELOPE/2`) and both baselines
were **re-graded offline** against it — no model re-called, no raw output
changed, and the files carry a `regraded_note` saying so.

**Kernel evidence, MODEL_GENERATED.** The seven strict successes rebuilt from
recorded output: thickness **5.0**, volume **11492.03552627690** against the
immutable closed form (delta **1.82e-12**), 1 solid, 18 faces, 42 edges,
envelope (40,20,20) — **bit-identical on CadQuery 2.8.0 and FreeCAD 1.0.0**.

**The original request is still 0/8 and stays in the benchmark.** It is now
labelled as measuring comprehension of an ambiguous spec rather than CAD
capability, and the two are never summed.

**Stage 69 — the residual measured, and removed.** **224 live calls**, all on
the EXPLICIT request, one variable at a time; every arm's raw output, plan,
kernel geometry and verdict is in
`docs/evaluation-baselines/stage69-bore-axis-centre/`.

| | |
|---|---|
| prompt now | **`2026-09-18.5` / `8563c6fb821e022f` / 30917 chars** |
| encoding | `strict_selector_union` (3628, unchanged) |
| STRICT SUCCESS, explicit request | **54/64 → 91/96** (Fisher exact, two sided, **p = 0.049**) |
| `E:bore_position` | **5/64 → 1/96** (**p = 0.038**) |
| thickness / plate count | **64/64 → 96/96** — unchanged, no regression |

`2026-09-18.4` is **deliberately unused**: Stage 67 adopted that number for an
arm it then reverted, so it names a prompt that exists nowhere in the history.

**Stage 68's reading of the residual was wrong, and 24 calls said so.** Its
"the `+Z` triple is copied to all three axes" hypothesis is **refuted** —
cross-axis triple reuse 0/24. Over **64 pooled baseline attempts** (96 bores,
192 across-axis components) every one of the 9 wrong components is the **z**
of an `+X` or `+Y` bore; `x` and `y` were wrong **0** times in every
position. The along-axis component was written as `0` on **94 of 96** bores,
so the model has the "0 is safe along the axis" half of the rule and
over-applies it to the letter `z`.

**Four arms of 32 live calls each, and two of them are negative results.**

| arm | what changed | strict | `E` | wrong components |
|---|---|--:|--:|---|
| A0 baseline | nothing | 28/32 | 2 | all z |
| A1 no-letter | the axis table rewritten so no letter is paired with "may be 0" | 27/32 | **4** | all z |
| **A2 worked-triples** | one worked example whose bores carry a **nonzero z** | **31/32** | **0** | **none** |
| A3 order | the same three lines **reordered** — a pure reordering, same length, same characters | 27/32 | **4** | all z |

**A1 made it worse**, so prose about the rule moved nothing for the third
stage running. **A3 is the discriminator and it refutes the table**: if the
defect were primacy, reversing the order would have moved the error onto `x`;
all 8 wrong components were still `z`. What moved it was the **missing
example** — every hole position the prompt *showed* had `z` at 0, because
every example bore ran along `+Z`. A2 adds one worked example in the prompt's
own 60 × 30 × 30 numbers, never the golden request's. **Stage 65's and Stage
66's mechanism, found a third time: what the model imitates is what the
prompt shows, and a rule stated alongside a contradicting example loses.**

**Confirmed, not adopted on one arm.** A2 was re-run twice more at 32 calls;
pooled n = 96. No guard regressed — thickness 96/96, plate count 96/96,
envelope 92/96, built 92/96, P11 4/96, all at or better than baseline. A2's
one remaining `E` is a different code (`F`, all three bores on `+Z`): **the
z-zeroed defect is 0 in 96 attempts.**

**Kernel evidence, MODEL_GENERATED.** All **91** claimed successes rebuilt
from recorded output: one distinct volume, **11492.035526277** against the
immutable closed form 11492.035526276899 (delta **1.819e-12**), 1 solid, 18
faces, 42 edges, envelope (40,20,20), **0 mismatches**, **bit-identical on
CadQuery 2.8.0 and FreeCAD 1.0.0**.

**`test_bore_axis_centre.py` pins the rule in geometry**, where no prompt
wording can argue with it, and pins the prompt's worked example by parsing
its arithmetic out of the prompt rather than by matching a string. Its six
guards are mutation-tested, including reintroducing the measured defect. One
of them exists because the kernel contradicted the stage's first draft: an
off-centre bore that stays inside the same two walls measures **identically**
— same faces, same edges, same volume — which is the geometric justification
for the plan-level `bore_centred` check, and the reason a criterion built
only on measurement cannot catch this class of error.

**Stages 73/74 — multi-body, steps 4 and 5: MEASURING IT, AND EXPORTING IT.**
Record: `docs/multi-body-step3/`. **No live model was called.** A credential
*is* present in this container; the backend was started with it deliberately
unset, because no provider encoding admits a `part` branch, so a model could
not have produced these plans and leaving the key set would only have made
the evidence harder to read. The evidence below is `DETERMINISTIC`.

**No new operation, no new P-code, no schema change, no prompt change** — the
third multi-body stage running where the canonical plan already carried what
was needed. A body already had an id, a feature list and its own measurement;
what was missing was the surfaces asking for them.

**Per-body measurement.** `questions.answer` gained one optional `bodies`
argument, and the scope is decided ONCE, before any answerer runs, by the
same `body_reference.resolve_body` the edit readers use.

| the question | the answer |
|---|---|
| names a live body | **that body**, from that body's measurement, prefixed with its id |
| names none, one body live | **unchanged**, word for word, from before Stage 73 |
| names none, several live | **REFUSE**, listing them |
| names two at once | **REFUSE** |
| names a consumed body | **REFUSE**, and say what consumed it |
| asks for a **total** | every body, summed, `CALCULATED` |
| asks the **overall size** of several | the containing box, **`ASSUMED`** |

**Refusing is not declining, and the difference is load-bearing.** `None`
means *"not a question this module answers"* and falls through to the model,
which is safe. A question it RECOGNISES but cannot answer without picking a
body must not fall through the same way: the model can read the plan but has
never seen the part, so it would answer *"the volume is 64000"* about a part
with two bodies and nothing downstream could tell that from a right answer.
So `None` falls through and `QuestionRefused` reaches the person — the same
split `normalize` already makes between `_Decline` and `ReadingError`.

**`ASSUMED` is the fourth provenance, and there is exactly one of it.**
Volume, faces and edges genuinely add across bodies. The overall size does
not: the box round a 40 mm cube and a pin standing 10 mm away runs **0..70**
in x, and the part does not fill it — there is a 10 mm gap of air that no
kernel measured and no operation declared. It is **not** a general licence to
assume; every other answer still comes from the kernel, the plan, or
arithmetic on the two. The downgrade keys on WHICH ANSWERER produced the
answer, not on matching words in its text.

**`resolve_body` gained a `verb`, defaulting to `"change"`** so every existing
caller is byte-identical. Questions pass `"measure"`: *"say which one to
change"* in front of someone who asked what the volume is reads as a refusal
to answer. The decision stays single and shared; only the sentence belongs to
the surface.

**Two defects this exposed, both of which looked like working software.**
The BROWSER still read `execution.bodies[0].measurement` (`main.ts:201`) and
put it in the panel headed *measurements* — Stage 71 removed that read from
the server and this one survived on the client, so a two-body build showed
the cube's numbers as the part's. The numbers were real; the label was wrong.
And `POST /session/engineering` never went through `_current_shape`, so
unlike export and drawing it was **never gated**: on a two-body part it
answered **200, `answered: true`** with both bodies' holes in one list and
nothing saying which body each came from, while reporting no measured values
at all. Both are fixed; engineering's no-question path now reports every body
separately.

**A real multi-body STEP.** `/session/export` writes every body, none fused,
each under its own id, in declaration order, with `x-cad-bodies` naming what
is in the file. **The single-body path is unchanged** and still calls
`export_step`; a test asserts the two writers agree on a one-body part so
they cannot drift. **STL still refuses** — an STL carries one mesh, and
`docs/multi-body-design.md` §3.6 requires the one-file-per-body versus
one-multi-solid choice to be made explicitly and recorded, which it has not.

**The two export failures this was written against, both measured.**
FreeCAD's `Part.export`, handed raw shapes, returns and leaves a well-formed
**1 640-byte** STEP that reads back as **ZERO solids** — it exists, it is
non-empty, it parses, and every check short of counting solids passes. And
handed a compound, BOTH engines write a correct two-solid STEP whose bodies
are called `Open CASCADE STEP translator 7.9 1.1` and `1.2`: every volume,
face and edge total matches and the identity is simply gone. So
`verify_assembly` checks the solid count AND that every id reached the file.
The working writers are `cadquery.Assembly.export` and FreeCAD's
`Import.export` over document objects, whose `Label` carries the id across.

**Kernel evidence, both engines, from a round-trip READ:**

| | closed form | CadQuery 2.8.0 | FreeCAD 1.0.0 |
|---|--:|--:|--:|
| `cube` | 64000 | **63999.999999999985** | **63999.999999999985** |
| `pin` | 9424.77796076938 | **9424.777960769377** | **9424.777960769377** |
| solids in the written STEP | 2 | **2** | **2** |
| body ids in the file | — | `cube`, `pin` | `cube`, `pin` |

**Cross-readable**: FreeCAD reads the CadQuery-written assembly and returns
the same two solids at the same volumes. File sizes differ (22 673 B vs
11 926 B) because the writers emit different amounts of product structure;
the geometry and the identity do not.

**The honest limit of the name check**: it proves each id REACHED the file,
by reading the STEP as the text it is. It does not prove which solid carries
which name — that needs a per-engine assembly reader, and the two engines'
readers differ. Written down rather than papered over with a parity claim
neither engine supports.

**Drawing: what is real, and what is refused.** A **detail drawing of one
named body** is a real drawing and is implemented — true projections of that
body, dimensions from that body's own measurement, and the sheet says which
body it is of. An **assembly drawing** answers **501** with
`capability: "assembly_drawing"`. `TechDraw.projectEx` *will* project a
compound of both bodies into one outline (measured: 8 edges spanning
x 0..80), so this is a deliberate line rather than a missing capability: an
assembly drawing carries item numbers, balloons and a parts list, and its
overall dimensions are of the box that §`ASSUMED` above has already
established nothing measured.

**Browser — `npm run e2e:surfaces` PASSES**: real Chromium, real WebGL, real
FreeCAD 1.0.0, no model configured. Eight steps — two bodies at their closed
forms; *"the volume of the cylinder"* answered `MEASURED` and prefixed
`cylinder:`; *"what is the volume?"* **refused**, naming both; *"the total
volume"* answered `CALCULATED`; engineering `per_body: true`; a drawing of
`cube`; the assembly drawing **refused**; and the STEP export verified **by
its bytes** — 2 `MANIFOLD_SOLID_BREP`, both ids present — because a STEP that
quietly dropped a body is a perfectly valid file.

**Single-body regression: none.** `e2e:assembly`, `e2e:multibody` and
`e2e:bodytarget` all still pass. `test_body_measurement` compares every
single-body answer **character for character** against the same question
asked the pre-Stage-73 way.

**`test_body_measurement.py` (26) and `test_step_assembly.py` (17) — 16
guards, mutation-tested 16/16**, including answering about the first body,
letting an ambiguous question decline so the model answers it, scoping the
words but not the measurement, calling a summed total MEASURED, trusting the
writer instead of counting what it wrote, checking geometry but not names,
and exporting only the first body.

**Stage 72 — multi-body, step 2: ADDRESSING A BODY BY NAME.** Record:
`docs/multi-body-step2/`. **No live model was called and none was
configured**; the evidence is `DETERMINISTIC`.

**No operation, no P-code and no schema change.** The canonical plan could
already name a body — an operation's `target` IS a body id, and a body keeps
that id for life. What was missing was turning a sentence into one of those
ids, and refusing when the sentence does not settle it.
`cad_experimental/body_reference.py` is the **one** place that answers it:

| the request | the answer |
|---|---|
| names a live body | **that body** |
| names none, and exactly one is live | **that body** — pre-Stage-71 behaviour, unchanged |
| names none, and several are live | **REFUSE**, listing them |
| names two bodies at once | **REFUSE** — one operation changes one body |
| names a body that was consumed | **REFUSE**, and say what consumed it |

**A body is named by its id and by nothing else.** Matching on the noun of
the primitive it came from, or on a synonym list — "the block" for a body
named `plate` — was rejected: it is a guess, and a wrong guess edits the
wrong body and reports success. A refusal costs the person one word and
cannot be wrong. An id that is a whole word inside a longer one (`plate`
inside `plate-2`) is settled by the **text**, one match span containing the
other, never by preferring a longer name in general.

**No new validation rules, deliberately.** A plan naming a nonexistent body
is already P9, a modifier's id is P11, a consumed solid is P12. The refusals
above are *reader* concerns, and a P-code restating P9 would be a second
opinion about what "a body" means — the same finding as Stage 71's, where
the design's proposed P33 turned out to be P9–P12 verbatim.

**Two defects this slice had to fix to be correct.** `_envelope` took the
bounding box over EVERY constructive solid: with a 40 mm cube at the origin
and a cylinder beside it that box runs x 0..70, so "a hole through the centre
of the cube" would have been bored at x = 35 — **15 mm off the cube's own
centre**, in a plan that still validates and still builds. It is now scoped
to the named body via `_constructive_of`, which `_fused_from` also reads. And
a failed selection now names its body (`on 'cube': … matched no edge`), added
in the **executor**, because `edge_semantics` imports nothing and knows
nothing about plans or bodies and must stay that way.

**The grammar was widened, and that alone would have been a bug.**
`_GROW_WORDS` took `wider`/`width` but not the bare `wide`, so the plainest
sentence declined. Adding the adjectives without `_COMPARATIVE` would have
read "make the cube 50 mm wide" on a 40 mm cube as 40 + 50 = **90** —
measured. `"20 mm wider"` is a change; `"50 mm wide"` is a size; English
settles it with the *-er*.

**Kernel evidence, both engines.** Editing each body in turn:

| step | cube | cylinder |
|---|--:|--:|
| created | **63999.999999999985** | **9424.777960769377** |
| "make the cube 50 mm wide" | **79999.99999999999** | 9424.777960769377 — **untouched** |
| "put a 6 mm hole through the cylinder" | 79999.99999999999 — **untouched** | **8576.547944300135** |

A body-targeted bore measures **identically on CadQuery 2.8.0 and FreeCAD
1.0.0**, per body, over volume, faces and edges.

**Browser — `npm run e2e:bodytarget` PASSES**: all four steps, including the
one that matters most — "make it 20 mm wider" comes back **`refused`**, names
both bodies, and leaves the part exactly where it was. Every other step can
be checked in a unit test; that a refusal reaches the person *as an answer*,
in the product, cannot.

**`test_body_targeting.py` — 26 tests, 8 guards mutation-tested 8/8**,
including falling back to the first body, picking the first of two names, and
taking the envelope over every body again.

**Known limitation:** `read_resize` still declines for a cylinder — *"a
cylinder's 'width' is its diameter; later"* — so "make the cylinder 40 mm
long" declines. That is a dimension-semantics question, not a body-targeting
one, and doing it here would have been a second unknown in one stage.

**Stage 71 — multi-body, step 1: DISTINCT BODY IDENTITY.** The first step of
`docs/multi-body-design.md`, and only the first. Record:
`docs/multi-body-step1/`. **No live model was called and none was
configured** — this is architecture, and the evidence below is
`DETERMINISTIC`.

**One new operation, and it makes no geometry.**

```json
{"id": "body_cube", "type": "part", "target": "cube"}
```

**It is a declaration, not an inference.** Two live bodies and no `part` is
still a mistake and still fails with `multiple_solids` — inferring "they must
have meant two" from two live solids is exactly the silent behaviour Stage 62
removed. A leftover and a declared body are different facts; the plan says
which.

**`part` is deliberately NOT in `OPERATION_TYPES`, and that is the stage's
central design decision.** That tuple is the geometry vocabulary every
provider encoding and the prompt are built from, so an entry there would have
moved `plan_schema`, `provider_schema`, `compact_provider_schema` and the
**prompt** fingerprint in one edit — teaching a live model a grammar whose
semantics are not yet measured, which the design refuses outright. The
separate `DECLARATION_TYPES` tier makes "no recorded fingerprint moved" a
property of the design rather than a thing to remember. **All nine schema
fingerprints and the prompt are unchanged**, asserted by
`test_multi_body.py`, which also asserts that no encoding a provider is ever
pointed at admits a `part` branch.

**Three new rules, where the design proposed four.** Its P33 — "a `part`'s
target names a solid live at that point" — turned out to be the reference
rules P9–P12 verbatim, so it was **not added**: a declaration's target is a
reference like any other, and a fourth code restating it would have been a
second opinion about what "live" means.

| rule | says |
|---|---|
| **P33** | a body is declared at most once |
| **P34** | the declared bodies are exactly the live ones at the end |
| **P35** | at most `MAX_BODIES` (8) |

`executor.finished` keeps its gate and gains **one** exemption: more than one
live body passes only when every one is declared. `result.part` is
**unchanged** — the single live body or `None`, never `bodies[0]`.

**The two-body example**, read by a provider-neutral grammar
(`normalize.read_separate_bodies`) with no model and no SDK:

> Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two
> separate bodies.

| body | volume | closed form | solids | faces | edges |
|---|--:|--:|--:|--:|--:|
| `cube` | **63999.999999999985** | 40³ | 1 | 6 | 12 |
| `pin` | **9424.777960769377** | π·10²·30 | 1 | 3 | 3 |

**Bit-identical on CadQuery 2.8.0 and FreeCAD 1.0.0.** The total is the sum
of the two closed forms, so nothing was fused.

**Edit isolation, proved on the kernel:** a `through_hole` on `cube` leaves
`pin` identical in volume, faces, edges and solids and appears in `cube`'s
feature list only; rebuilding `cube` at 60 mm leaves `pin` identical; two
independent chains **interleaved in either order** give the same shapes; and
a `straight Z` fillet on `cube` selects the **same edge indices** whether or
not `pin` exists — selector scope asserted rather than assumed.

**Every output carries every body.** One `RenderModel` per body, each tagged
with its `body_id`, never merged — merging would be a fuse the plan did not
ask for. `PlanBuild.render` still means "the single body's mesh" and is
`None` for a multi-body part, so no existing caller silently starts drawing
the first of several. The HTTP payload gained `bodies` (per-body mesh,
measurement, features, declared flag) and `declared_bodies`, and the
part-level `measurement` is `{}` for a multi-body part rather than the first
body's.

**Three `bodies[0]` reads that were safe only because two bodies were
impossible are now fixed.** `_facts` reads `result.part`; `/session/export`
and the drawing and engineering surfaces **refuse** a multi-body part by
name rather than writing its first body. A multi-body STEP assembly is step
5 of the design and is not implemented.

**Browser — `npm run e2e:multibody` PASSES**: real Chromium, real WebGL, real
FreeCAD 1.0.0, no model configured. Two bodies in the payload, both declared,
each with its own mesh and closed-form volume, no merged mesh, no part-level
measurement claimed, the viewport note naming both bodies, 0 failed requests,
0 console errors.

**Single-body regression: none.** `npm run e2e:assembly` still passes on the
golden enclosure (11492.035526276897, 18 faces, 42 edges, 5544 triangles) and
its viewport note is byte-for-byte what it was. `test_multi_body` rebuilds
the same enclosure and asserts `declared == ()` and `part == "base"`.

**`test_multi_body.py` — 43 tests, and its 8 guards are mutation-tested**,
including putting `part` back into the geometry vocabulary, inferring the
declared set from the geometry, widening `result.part` to `bodies[0]`, and
rendering only the first body.

**Known limitation, and the next milestone.** The product cannot yet edit one
body **by name**: `normalize._body_id` returns `None` unless exactly one body
is live, so every general edit reader declines a multi-body part rather than
guessing which body was meant. That is the honest behaviour and it is what
step 2 has to address.

**Stage 70 — the P11 residual measured, bounded, and NOT prompt-fixed.**
**288 live calls**, all on the EXPLICIT request, five arms, one variable
each. **No prompt change was adopted**; the prompt is unchanged at
`2026-09-18.5`. Record:
`docs/evaluation-baselines/stage70-union-p11/`.

**An adoption rule was committed BEFORE the confirming runs** (commit
`acf9028`, `decision_rule.py`): pooled n ≥ 64, P11 below baseline at Fisher
exact two sided p < 0.05, and no regression in thickness, plate count,
envelope, bore axes or strict success. It rejected all four candidates.

**Stage 69's description of the residual was a token, not a mechanism.**
It read *"every P11 attempt targets the product noun `enclosure`"*;
**`enclosure` is what the model named the UNION.** The model gives the union
a body noun and then targets the name it just wrote. Over 144 baseline
attempts — **432 post-union targets** — every target named either the
surviving body (408) or the union's **own id** (24). A consumed tool: **0**.
An id absent from the plan: **0**. The model never invents an id.

**Fresh baseline, n = 144** (48 new calls pooled with Stage 69's arm A2,
whose text *is* the committed prompt):

| | |
|---|--:|
| STRICT SUCCESS | **135/144 = 93.8 %** (95 % CI 88.5–97.1) |
| `H:wrong_target` (P11) | **8/144 = 5.6 %** (95 % CI **2.4–10.7**) |
| thickness / plate count | **144/144** each |
| `E:bore_position` | 1/144 — Stage 69's fix holding |

**The matrix, and the one significant result.**

| arm | one variable | n | strict | P11 | union id ≠ `fuse` |
|---|---|--:|--:|--:|--:|
| B0 baseline | nothing | 144 | 135/144 | 8/144 | 8/144 |
| B1 no-product-nouns | the forbidden-noun sentence **deleted** | 96 | 85/96 | **7/96** | **0/96** |
| B2 read-it-back | a check appended; **no new noun** | 32 | 31/32 | 1/32 | 1/32 |
| B3 mandate-first | mandate moved **before** the naming rule — **pure reordering** | 32 | **21/32** | **11/32** | 10/32 |
| B4 mandate-last | mandate moved **after** the layout block — **pure reordering** | 96 | 92/96 | 3/96 | **0/96** |

**B3 regressed hard** — same length, same characters, two paragraphs
swapped: strict 135/144 → 21/32 (**p = 0.0001**), P11 → 11/32 (p < 0.0001).
**Unlike Stage 69, where the equivalent control moved nothing, position is a
live mechanism in this section.** The committed order is not cosmetic.

**The union id is a MARKER, not the cause.** On the committed prompt the
separation is perfect — P11 **0/136** when the union is named `fuse` and
**8/8** when it is not — which invites *"make it say `fuse` and P11 goes
away"*. **B1 and B4 both falsify that**: each reached **96/96** compliance
and still carried P11, at 7/96 and 3/96, every one on a union correctly
named `fuse`. A perfect correlation over one prompt is not a mechanism.
B1 is the sharper result: deleting the forbidden-noun sentence bought total
id compliance and *raised* P11, because what that sentence supplies is not
the id but the reason the union's id names no solid.

**B2, the clean prose arm, did nothing** — the fourth stage running in which
prose about a rule moves nothing.

**B4 is the arm that would have been adopted without the rule.** Its first
32 calls came back **32/32**, the first perfect arm in this project's
history on this request; its two confirmations were 29/32 and 31/32, and
pooled 3/96 against 8/144 is **p = 0.53**. Stage 67 adopted an arm on exactly
that kind of first reading and had to revert it. The rule existed before the
numbers did, which is why it did not happen twice.

**Kernel evidence, MODEL_GENERATED.** **136** claimed successes rebuilt from
recorded output: one distinct volume, **11492.035526277** against the
immutable closed form 11492.035526276899 (delta **1.819e-12**), 1 solid, 18
faces, 42 edges, envelope (40,20,20), **0 mismatches**, **bit-identical on
CadQuery 2.8.0 and FreeCAD 1.0.0**.

**THE STOPPING DECISION: stop, and carry the number.** Five arms and 288
calls produced no intervention that lowers P11 significantly, and the only
significant effect found was a way to make it worse. Separating 5.6 % from
~2 % at p < 0.05 needs several hundred more calls per arm, and three of five
arms show candidate edits trading one failure mode for another. Adding
prompt text on the strength of a non-significant arm is how a prompt bloats,
and this one is already 30917 characters. The residual is bounded, measured
and **fails closed** — a P11 plan is rejected by the validator and builds
nothing, so it is never silent and nothing is repaired.
`test_union_section_shape.py` pins the paragraph order, the sentence B1
removed, and the semantic rule stated relationally; its six guards are
mutation-tested, and one of them exists because a mutation that emptied a
loop left the test passing.

**Stage 66 — the golden request BUILDS.** **40 live calls**, eight arms, one
variable at a time; raw output, validation and build results are in
`docs/evaluation-baselines/stage66-enclosure-layout/`, whose `arena.py` is a
byte-for-byte copy of Stage 65's.

| | |
|---|---|
| prompt now | `2026-09-18.3` / `c78aaad8eacf365e` / 30481 chars |
| encoding | `strict_selector_union` (3628, unchanged) |
| six-plate box built | **0/5 → 2/5**, one of them the part asked for |
| volume | **10185.62842102151** mm³ vs closed form 10185.62842102152 (Δ **9.1e-12**) |
| topology | 1 solid, **18 faces, 42 edges** — the deterministic reference's |
| backends | CadQuery 2.8.0 and FreeCAD 1.0.0, **bit-identical** |

`MODEL_GENERATED` — no fixture, no deterministic reader, no fallback.

Four defects were measured, all of them prompt gaps: the plates were never
rotated (thickness stayed on Z 5/5, where a shell needs the same numbers
permuted onto each face normal); the outer size was invented; holes were
named for WALLS, so `hole_front` and `hole_back` became two operations on
one centreline; and `z is conventionally 0` was stated for `+Z` only and
carried to every axis.

**The prompt supplied the noun that broke it.** Across all 40 calls, a union
whose id was the mandated verb `fuse` hit P11 **6/27**; a union with any
other id hit it **11/12**. The prompt's own enclosure sentence read *"six
plates where the first is named `shell`"*, and the model gave `shell` to the
UNION rather than to the carrying plate. Renaming the carrier to `bottom` —
one sentence — took P11 **5/5 → 0/5** as a single variable.

Two negative results are kept because they cost as much to learn: an arm
that added the hole-count rule while opening with the product noun *"A
hollow box…"* **regressed** P11 to 4/5, and the layout rule alone regressed
it to 5/5 while the `shell` sentence still stood. **Prose about a rule moved
nothing; removing the noun that invited the wrong answer moved it** — Stage
65's mechanism, found twice more.

Honest limits: **1/5 fully correct**, not 5/5 — three of five attempts still
write one hole per wall despite the rule, and the rest fail rule E1. The
golden request is itself **ambiguous about plate thickness** ("(4)plates" is
a count the model reads as a dimension, and one attempt asked about it
outright); the prompt was deliberately **not** tuned to force the
deterministic reader's reading, because that is teaching to the test.
`test_enclosure_layout.py` pins the four rules, the worked example's
arithmetic, and the one measured plan's closed form; all four guards are
mutation-tested.

**Schema measurements — proven vs historical.** `PROVEN_COMPILABLE` means
*a live probe accepted this size*, never *we expect it to be accepted*:

| encoding | inlined | branches | live verdict |
|---|---:|---:|---|
| `selector` | 3134 | 5 | **PROVEN** (Stage 53) |
| `profile` | 3487 | 4 | **PROVEN** (Stage 50) |
| `strict_selector` | 3619 | 5 | **PROVEN** (Stage 55) |
| `executable` | 3622 | 6 | **PROVEN** (Stage 43) — frozen, fp `54759d1e16cfe634` |
| **`strict_selector_union`** | **3628** | **5** | **PROVEN (Stage 63)** — what the live route sends |
| `profile_hole` | 4030 | 5 | **PROVEN** (Stage 51) |
| `profile_union` | 4481 | 6 | **PROVEN** (Stage 51) — largest ever accepted |
| `compact` | 6199 | 8 | **REFUSED** |
| `provider` | 7360 | 8 | **REFUSED** |

Ceiling bracket **(4481, 4551]** — a bound, not a number — derived in
`schema_ladder` from tuples of every live verdict rather than hard-coded.
No ladder rung now sits inside it.

**Engines — both verified.** FreeCAD **1.0.0** (headless, from the AppImage)
and CadQuery **2.8.0**. On the golden six-plate assembly both produce
**11492.035526276897 mm³**, 18 faces, 42 edges — **bit-identical**.
`CAD_BACKEND` defaults to `cadquery` and `resolve_backend()` never falls
back.

**Product smoke test — 9/9 on real FreeCAD, no model configured:**

| # | flow | measured |
|---|---|---|
| 1 | create simple box | 60000.0000 mm³ |
| 2 | create cylinder | 15707.9633 = π·10²·50 |
| 3 | add centre hole | 58869.0266 |
| 4 | resize | 70869.0266 |
| 5 | remove last feature | 72000.0000 |
| 6 | geometry question | answered from evidence |
| 7 | undo | back to 70869.0266 |
| 8 | reset | clean |
| 9 | six-plate hollow assembly | 11492.0355, one solid |

**Browser E2E — PASS.** `npm run e2e:assembly`: real Chromium, real WebGL
surface, one valid solid, 18 faces, 42 edges, 5544 triangles, 0 failed
requests, 0 console errors. `npm run e2e:multibody` (Stage 71): the same
stack, two declared bodies, each with its own mesh and closed-form volume,
no merged mesh and no part-level measurement claimed.
`npm run e2e:bodytarget` (Stage 72): a four-turn conversation that edits each
body in turn and gets a **refusal** for the turn that names neither.
`npm run e2e:surfaces` (Stages 73/74): eight steps over the same two bodies --
a per-body measurement question, an ambiguous one **refused**, a total that
says it is `CALCULATED`, engineering reported per body, a detail drawing of
one body, the assembly drawing **refused** with its capability named, and a
STEP export verified by its own bytes (2 `MANIFOLD_SOLID_BREP`, both ids
present).
**`npm run e2e:cases` FAILS**, and did before Stage 71 — see the limitations
below.

**Test counts (current):** `tests_experimental` **1979 passed, 5 skipped, 0
failed** (1984 collected), measured at Stage 74 on Linux with FreeCAD 1.0.0
present and `CAD_FREECAD_HOME`/`LD_LIBRARY_PATH` exported; cad-core **1481
passed**. Frontend `tsc --noEmit` clean, `vite build` succeeds. **The skip
count depends on the environment.** The 5 here are three drawing-view
projections the CadQuery backend cannot make plus two tests that exist to
check FreeCAD's *absence* and cannot run while it is imported. It is 72 when
FreeCAD is absent from the interpreter, because the FreeCAD-dependent
modules skip as well; the deliberately gated live-provider tests are not
collected by this discovery root at all.

**Local development providers — what each proves.**
`local_intent_provider.FakeLocalProvider` is **not a model**: it holds
canonical intent, ignores the request text, imports no SDK and is
deliberately *not* wired to the deterministic reader. Verified end to end —
it reached the kernel at 11492.035526276897 mm³ through the same parser,
validator and graph. `DecliningProvider` answers `None`.
`local_plan_provider` serves developer-written fixture **plans** and stamps
every answer `LOCAL_DEVELOPMENT_PLAN` / `is_live_model_result: false`.
**Neither says anything about model quality.**

**Capability modules that exist and are covered by the passing suite:**
`drawing.py`, `catalog.py`, `engineering.py`, `macros.py`, `agentic_loop.py`,
`sketch.py`, plus 17 HTTP routes including `/session/drawing`,
`/session/export`, `/session/macros`, `/session/macros/run`,
`/catalog/search` and `/session/engineering`. **Scope of that claim:** they
import, they are exercised by `tests_experimental`, and the suite is green.
They have **not** been driven through the product in a smoke test, so no
claim is made here about drawings being engineering-grade, the catalogue
being a supplier integration, or export coverage beyond STEP/STL from the
build path.

### Where this branch stands, and what is next

Stages 32–64 are complete and pushed. The subsections below are the index,
in order; they stop at Stage 48, and **Stages 49–64 are documented only in
`docs/experimental-operation-plan.md`** — read its `## Stage NN` headings for
those. The five most recent are:

| Stage | What it settled |
|---|---|
| **56–57** | Backend parity: FreeCAD 1.0.0 runs headlessly beside CadQuery, and the two agree bit-for-bit on every golden part where both can execute. |
| **59** | The agentic control loop. |
| **60** | Hardening that loop on **typed** evidence — E4/E5 decided by `edge_semantics.resolve`'s own `R1`/`R2`/`R3` codes rather than by matching words in a backend's error string. Verified live on `claude-haiku-4-5-20251001`. |
| **61** | **A CAD session that needs no model at all**: `union` (eleventh type, still eight schema branches), two provider-neutral grammars, and an evidence answerer that labels every number `MEASURED` / `DECLARED` / `CALCULATED`. |
| **62** | The four defects Stage 61 left behind — **an operation is not one edit.** The live route could not *say* `union` (Stage 44's defect a third time); a `union` plan could lose a solid silently; `ExecutionUnsupported` named the wrong reason for it; and a recorded schema size had drifted unpinned. |
| **64** | **The AI provider usage policy, and CLAUDE.md reconciled.** The rule forbidding Claude Code Web a real credential is retired: where one is available a real provider is used, and live calls are encouraged. `CREDENTIAL_PRECEDENCE` makes the two-variable behaviour explicit instead of implicit. 22 guards now pin the policy and stop the document drifting from the code. |
| **73/74** | **Multi-body steps 4 and 5: measuring it, and exporting it.** No new operation, P-code, schema or prompt change — again. A question naming a body is answered from THAT body; one naming none of several is **REFUSED**, phrased for a question rather than an edit; a total is explicitly summed and the combined **envelope** is `ASSUMED`, the fourth provenance, because that box holds the air between the bodies too. Export writes a real STEP assembly: every body, none fused, each under its own id, verified by reading the file back and counting solids **and** checking the names survived — handed a compound BOTH engines write a geometrically perfect file whose bodies are anonymous, and no count can see that. Found and fixed the last `bodies[0]`, in the BROWSER, and an engineering route that was never gated and merged both bodies' holes into one list. A detail drawing of one body is real; an assembly drawing is refused by name. |
| **72** | **Multi-body step 2: addressing a body by name.** One resolver, `body_reference.resolve_body`, and no new operation, P-code or schema change — the plan could always name a body; turning a sentence into one of those ids was what was missing. A body is named by its **id** and nothing else; naming none of several, naming two, or naming a consumed one all **REFUSE** with the bodies listed. Fixed two defects the slice exposed: the envelope spanned every body (a bore 15 mm off centre in a plan that still built) and a failed selection did not say which body. Editing each body in turn leaves the other bit-identical on both kernels, and the browser shows the refusal as an answer. |
| **71** | **Multi-body step 1: distinct body identity.** One new operation, `part`, that DECLARES a live body is an intended body of the result — kept out of `OPERATION_TYPES` so no schema or prompt fingerprint moves. Two live bodies with no declaration still fail `multiple_solids`; the executor gains one explicit exemption. P33–P35. One RenderModel per body, never merged; `result.part` unchanged. A 40 mm cube and a Ø20×30 cylinder build as two bodies, **bit-identical on CadQuery 2.8.0 and FreeCAD 1.0.0**, and pass through a real browser with no model configured. Single-body product unchanged. |
| **70** | **The P11 residual measured, bounded and NOT prompt-fixed.** 288 live calls, five arms, an adoption rule committed before the confirming runs — which rejected all four candidates. Stage 69's "targets the product noun `enclosure`" was a token: `enclosure` is what the model named the UNION, and no target in 432 ever named a consumed tool or an absent id. A pure reordering (B3) **regressed** at p = 0.0001, so the section's order is load-bearing; two arms reached 96/96 `fuse` compliance and still carried P11, so the id is a marker, not the cause. P11 **8/144 = 5.6 %, CI [2.4, 10.7]**; strict **135/144**. Stopped deliberately. |
| **69** | **The residual bore failure measured and removed.** 224 live calls. Stage 68's "the `+Z` triple is copied" reading is refuted (0/24); the model zeroes **z**, and only z, on non-Z bores. Deleting the rule's letter-to-zero pairing made it worse and a pure reordering of the table did not move the error, so the table is not the mechanism — the missing **example** was. Strict success 54/64 → 91/96 (p = 0.049), thickness 96/96 unchanged. Prompt `2026-09-18.5`. |
| **63** | **The audit closed and `union` measured live.** All 39 findings classified (0 false positives), 20 more fixed — including a resize that silently broke the part, a second solid-set walk, three duplicated authority tables and four tests that passed without proving their name. `strict_selector_union` (3628) is **PROVEN compilable**, the model emits `union` 4/5, and 0/5 build: **P11 only**, the union's own id used as a solid. |

**A model is one route to a plan, not the way in.** Stage 61 is the rule
applied: every route ends at the same parser, validator, feature graph and
kernel, and the deterministic route gets no shortcut. A whole session —
build, edit, and nine questions answered — was driven with the credential
deliberately **absent**, building real geometry that matches its closed forms
to 2e-11, and the golden six-plate assembly passes through a real browser the
same way. That says nothing whatever about model quality; it is a statement
about the grammars, which are narrow by construction and decline everything
outside their vocabulary.

Three refusal behaviours are kept apart, and conflating the first two was a
real bug: a grammar that **recognised** a request and cannot honour it
answers **200 refused**; a request **no grammar claimed** is **503** and is a
question for a model; a request that is answerable but underspecified (mass,
with no material named) asks for what it needs rather than assuming a
density.

**Everything built between Stages 43 and 59 was unmeasured against a model**
until Stage 60, which carries a live `claude-haiku-4-5-20251001` verification
with real provider metadata and no fixture involved. The Stage 48 instrument
below still has not been run in full.

**Stage 63 measured `union` live, and it is the standing open problem.** The
widened encoding the route sends (`strict_selector_union`, 3628 inlined,
5 branches) **compiles** — `structured_output` true on 5/5 calls, 0/5
fenced — so that long-open question is answered and 3628 is recorded in
`PROVEN_COMPILABLE`. The model **emits `union` 4/5**, so the answer is
reachable. But **0/5 build**: all four plans fail validation on **P11 and
nothing else**, every hole pointed at the union operation's *own* id instead
of the target whose id a union keeps. The prompt already states that rule
explicitly in its union section, so this is a **model-behaviour** finding,
not a prompt gap — and nothing was repaired, because the project has no
repair loop by design. The deterministic reader builds the same sentence
correctly, which is the product working by the provider-neutral route and
says nothing about the model.

Stage 48 removed the two blockers that stood in the way:

1. **A harness that sends `provider_schema()` now exists.**
   `stage48_capability_evaluation` sends the widened schema and the current
   prompt. (It read "the widened ten-type schema (`be8ba82740aecc1d`)" in
   the present tense until Stage 62's audit; the vocabulary is eleven types
   since `union` and the fingerprint moved with it.) Its
   `--probe-live` asks the one question Stage 44 left open — *does the
   provider compile it?* — in three calls.
   `stage43_structured_comparison` is **untouched** and still pinned to
   `executable_schema()` (`54759d1e16cfe634`, which a test still asserts):
   re-running Stage 43 must reproduce Stage 43.
2. **A corpus that exercises the new capability now exists.**
   `stage48_corpus` is 30 cases (`96517cb979b4660a`) covering sketch chains,
   `pattern`, semantic selectors, deeper graphs, ambiguity and impossible
   parts. `comparison_corpus.py` is **untouched** and still frozen at its 13
   cases — Stage 48's `legacy` group *reads* them out of it rather than
   copying them, so the two cannot drift.

**The finding Stage 48 produced before running anything:** Stage 46 added
`pattern` as a full operation and left `patterns` standing in the prompt's
UNSUPPORTED list, so the prompt told the model to decline something the
language has. Every pattern case would have come back `unsupported` and been
recorded as the model's judgement — the Stage 44 mistake in a new place.
Prompt `2026-09-15.5` removes it. **When an operation is added, the refusal
list is part of the operation.**

Still open, and only answerable live: **does the provider compile the widened
schema?** Every offline limit is satisfied and asserted; compiled-grammar
size can only be measured by sending it. `--plan-schema compact` is the
documented fallback, and nothing selects it automatically.

### AI provider usage policy — authoritative, supersedes §7

**When a credential is available, use a real provider.** Claude Code Web is
**explicitly permitted** to use `CAD_ANTHROPIC_API_KEY` when it is present.
The previous rule here — which forbade Claude Code Web from using or
requesting that credential at all — is **retired**.

**What "provider-independent architecture" means.** It means the **core does
not depend on a vendor** — the canonical pipeline (canonical intent →
operation plan → parser → validator → feature graph → backend →
RenderModel) imports no vendor SDK and no provider module, so a different
provider is a different adapter and nothing else. Provider independence is a
statement about what the core depends on, and **not that live vendor calls
are forbidden**. The core being **vendor-neutral** is an architectural
property; avoiding Anthropic is not a goal and never was. A configured real
model is meant to be *used*.

**The order of preference:**

1. **A configured provider is the default.** If one is available, it does the
   AI interpretation and generation.
2. **No provider configured** → the deterministic readers may be used where
   they support the request.
3. **Deterministic readers are NOT the default replacement for a configured
   model.** They are narrow by construction; reaching for them while a model
   is available hides what the model would have done.

**Live calls are encouraged for:** schema-compilation experiments,
semantic-quality measurement, golden-request validation, provider regression
tests, real product-path verification, and comparison against deterministic
interpretation.

**Credential names and precedence.** Both names may be *documented*; neither
value may ever be surfaced.

| variable | who sets it | note |
|---|---|---|
| `CAD_ANTHROPIC_API_KEY` | the operator | what Claude Code Web supplies, because that platform reserves and strips the SDK's own name |
| `ANTHROPIC_API_KEY` | the SDK's convention | the **only** name the Anthropic SDK reads |

`cad_experimental.config.CREDENTIAL_PRECEDENCE` is
`("CAD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")` — **the operator-supplied
name wins when both are set**, because it is the one exported deliberately
for this run while the SDK name may be ambient. `bridge_credential()` copies
the chosen key onto the SDK's name for the life of the process and returns
the **variable name** it came from, never the value. `credential_available()`
and `credential_variable()` consider both. This is written down rather than
left to whichever code path looks first — and it is a real fix, not only
documentation: reading `ANTHROPIC_API_KEY` alone is exactly why a live run in
Web could report "credential absent" with a perfectly good key present.

**Never surface a key.** Not in source, tests, test output, browser output,
reports, screenshots, logs, or git history. Only presence and variable names
are ever read or printed.

**Never claim a live model result without a live call.** Five outcomes, kept
distinct in every report:

| label | means |
|---|---|
| `MODEL_GENERATED` | a real provider API call returned this |
| `DETERMINISTIC` | a local grammar read the request; no model involved |
| `FALLBACK` | a model was asked, was not usable, and a local grammar answered — and the note says so |
| `REFUSED` | a grammar recognised the request and cannot honour it |
| `PROVIDER_ERROR` | the call itself failed |

`interpretation.SOURCE_PROVIDER` / `SOURCE_DETERMINISTIC` record the route on
every answer, and `DETERMINISTIC_NOTE` ("the model's plan was not usable")
and `NO_MODEL_NOTE` ("no interpretation model is configured") are separate
sentences because they are separate facts.

**Do not weaken the provider abstraction to satisfy this policy.** Vendor
SDK and API code stays behind `cad_ai.provider` and the provider adapters.
`apps/api/tests_experimental/test_provider_policy.py` pins both halves — the
permissive half and the vendor-neutrality half — because the old restriction
survived only as prose and nothing noticed when it stopped being the intent.

### KNOWN LIMITATIONS AND UNRESOLVED ITEMS (current)

Separate from the historical record, and none of these is a plan.

- **The benchmark now has two golden requests, and they must never be
  summed.** On the unchanged prompt the AMBIGUOUS original scores **0/8**
  strict and the EXPLICIT companion **7/8** (Stage 68, 16 live calls). The
  original measures comprehension of an under-specified spec; the explicit
  one measures CAD capability. Both are kept; neither is edited.
- **The bore-centre defect is FIXED.** On the committed prompt
  `2026-09-18.5` the explicit request scores **135/144 = 93.8 %** strict
  (Stage 70, 95 % CI 88.5–97.1), with thickness and plate count 144/144 and
  `E:bore_position` 1/144.
- **P11 is the standing residual, at a MEASURED and BOUNDED rate:
  8/144 = 5.6 %, 95 % CI [2.4 %, 10.7 %].** Stage 70 spent 288 live calls
  over five arms and found no intervention that lowers it significantly —
  the only significant effect was a pure reordering that made it worse. The
  stage stopped deliberately rather than bloat the prompt on a
  non-significant arm. **It fails closed**: a P11 plan is rejected by the
  validator and builds nothing.
- **The union id is a marker for P11, not its cause.** On the committed
  prompt P11 is 0/136 with a union named `fuse` and 8/8 without, but two
  arms reached 96/96 compliance and still carried P11 on `fuse`-named
  unions. Do not read the correlation as a mechanism.
- **The union section's paragraph ORDER is load-bearing.** Moving the `fuse`
  mandate ahead of the carrier-naming rule — same length, same characters —
  took strict success from 135/144 to 21/32, p = 0.0001.
- **One attempt in 96 wrote all three bores on `+Z`** (`F:bore_direction`),
  a mode not seen before Stage 69. Still rare; 3/336 across Stages 69–70.
- **`comparison_corpus.py` case `12-union` is stale** — it expects
  `unsupported` for a request the language now answers. **Deliberately not
  edited**: it is a frozen instrument and Stage 48's `legacy` group reads out
  of it. Re-baselining is its own stage.
- **Selector resolution is implemented twice** — `edge_semantics.resolve` and
  the backends' own `select_edges` — and an audit claims they disagree on a
  parameterisation seam. **Not reproduced**, recorded as unverified, not
  claimed real.
- **34 of 39 audit findings from the Stage 62 sweep were never verified by
  the audit itself**; Stage 63 classified all 39 and fixed 25 in total. One
  (#35, a missing `__all__` entry) **could not be reproduced**.
- **Multi-body is DECLARED, never inferred** (Stage 71). More than one live
  body still fails with `multiple_solids` unless every one is declared with
  a `part`. What remains undone is deliberate and listed in
  `docs/multi-body-step1/` §9: no transforms, mates, joints or constraints;
  no sub-assemblies or BOM; and **no model-facing grammar**, since no
  provider encoding admits a `part` branch and the prompt does not mention
  it. (Its "**no multi-body export**" entry is **superseded by Stage 74** —
  see below.)
- **A multi-body part IS edited one body at a time** (Stage 72), by naming
  the body's id. What is still missing there: `read_resize` declines for a
  **cylinder** (its "width" is a diameter — a dimension-semantics question,
  deliberately deferred), and a body can only be named by its id, never by
  the noun of the primitive it came from.
- **A multi-body part IS measured and asked about one body at a time**
  (Stage 73). What is still missing: an id that was never in the plan cannot
  be told apart from no mention at all, so *"the volume of the sphere"* on a
  two-body part refuses as **ambiguous** rather than as **unknown body**. A
  body that exists but was **consumed** is distinguished and named, which is
  the case that actually misleads; telling the other apart would need the
  noun dictionary Stage 72 rejected on purpose.
- **A multi-body part IS exported as a STEP assembly** (Stage 74), and the
  writer verifies solid count AND body names by reading the file back.
  **STL still refuses** a multi-body part: an STL carries one mesh, and
  `docs/multi-body-design.md` §3.6 requires the one-file-per-body versus
  one-multi-solid-file choice to be made explicitly and recorded, which it
  has not been. The name check proves each id **reached the file**, not
  which solid carries it — that needs a per-engine assembly reader and the
  two engines' readers differ.
- **There is no assembly drawing, and it is refused by name** (Stage 73).
  A **detail drawing of one named body** is real and implemented. A drawing
  of all bodies at once answers **HTTP 501** with
  `capability: "assembly_drawing"`. `TechDraw.projectEx` *will* project a
  compound of both bodies into one outline — measured doing so, 8 edges
  spanning x 0..80 — so the refusal is a deliberate line, not a missing
  capability: an assembly drawing carries item numbers, balloons and a parts
  list, and its overall dimensions are of a box containing the bodies and
  the space between them.
- **`npm run e2e:cases` is broken, and was before Stage 71.** It waits for
  `#description`, an element the page has not had since `641106f`. Verified
  by running it at `8e1371c` with this stage's changes stashed, where it
  fails identically. Not a regression, not fixed here, and recorded so a
  green run of the other three browser suites is not read as covering it.
- **No live corpus benchmark exists** on this branch. Stage 48's instrument
  is complete and has never been run in full; the Stage 43 comparison is the
  only full live run, and its schema and prompt are both superseded.
- **Security and performance, on this branch as on stable:** no
  authentication, no authorization, no user isolation, no rate limiting, no
  payload size limit, no CORS policy, no TLS, no audit trail. Process
  isolation is **crash containment, not a security sandbox**. Session state
  is in-memory, bounded and thrown away when the process exits — no
  database, no persistence, no cross-process sharing. **None of this has been
  security-reviewed or load-tested**, and no measurement of throughput or
  build latency has been taken, so no performance claim is made either way.
- **`sketch`, `extrude` and `revolve` are not buildable.** Represented,
  validated, then explicitly refused. No grammar carrying a full-fidelity
  sketch is expected to compile against the measured ceiling.
- **`describe_edges` is CadQuery-only.** FreeCAD raises rather than answering
  wrongly, so semantic selectors are a CadQuery capability today.

### RECOMMENDED NEXT MILESTONE

**Stages 65 and 66 did the work this section used to call for.** Stage 65
removed the P11 union-target failure; Stage 66 measured the E1 spatial
failure it exposed, fixed four prompt defects, and the live model now builds
the golden six-plate enclosure — verified to 9.1e-12 and bit-identical on
both backends.

**Stage 67 did that work and adopted nothing.** Nine arms and 80 live calls
showed every candidate trading one defect for another, and the best of them
regressing the plate thickness at p = 0.0013.

**Stage 68 did step (1) and it changed the picture.** Disambiguating the
REQUEST, with no prompt change at all, took the strict rate from 0/8 to 7/8
and zeroed four of the five failure codes — including the P11 targeting
failure four stages had been chasing.

**Stage 69 did that work and the target is gone.** 224 live calls refuted
Stage 68's reading of the residual, isolated the real one — `z` zeroed on
non-Z bores — and removed it by supplying the example the prompt had never
shown. Strict success on the explicit request is **91/96**, with thickness
and plate count untouched at 96/96.

**Stage 70 did that work and stopped on purpose.** 288 live calls over five
arms found no intervention that lowers P11 significantly; the only
significant effect was a pure reordering that made it worse. The residual is
**8/144 = 5.6 %, 95 % CI [2.4 %, 10.7 %]**, it fails closed, and the stage
declined to add prompt text on the strength of a non-significant arm.

**The prompt-tuning line of work is CLOSED at this quality level, and the
next milestone is architectural.** Four consecutive stages now say the same
thing about this prompt: what the model imitates is what the prompt *shows*
(Stages 65, 66, 69), prose about a rule moves nothing (65, 66, 69, 70), and
the section's own structure is load-bearing in ways a reading cannot predict
(70's B3). There is no measured lever left on P11 at the sample sizes this
project can afford.

**Stage 71 did that work.** `part`, P33–P35 (the design's fourth rule turned
out to be the existing reference rules and was not duplicated), and the test
that every existing schema fingerprint and the prompt are unchanged. Two
independent bodies build, measure and draw, bit-identical on both kernels,
and the single-body product is untouched.

**Stage 72 did that work.** `body_reference.resolve_body` is the one answer
to "which body does this request mean", a body is named by its id, and every
sentence that does not settle the question is refused with the bodies listed.
Editing each body in turn leaves the other bit-identical on both kernels, and
the browser shows the refusal as an answer rather than a guess.

**Stages 73 and 74 did that work — steps 4 and 5 — and the deterministic
slice is now complete.** A body can be measured and asked about by name; a
question that does not say which body is refused rather than answered; an
aggregate is explicitly summed and the aggregate *envelope* is labelled
`ASSUMED`, because the box containing two bodies also contains the air
between them. Export writes a real STEP assembly, verified by reading the
file back and counting solids **and** checking the body names survived.
Neither needed a new operation, a new P-code or a schema change.

**Next: the model-facing multi-body grammar, and only now.** The four gates
the design set are met — per-body measurement, product surfaces, a genuinely
verified STEP assembly, and a green single-body regression. What opening it
means is a `part` branch in the encoding the live route sends and a prompt
section to match, and the rule the last four stages keep proving applies:
what the model imitates is what the prompt SHOWS, so it needs a worked
example, not prose. Measure it against a golden corpus of its own, counting
`MODEL_GENERATED` separately from `DETERMINISTIC` — every multi-body number
recorded so far is deterministic and says nothing whatever about a model.

**The model-facing grammar stays closed until the deterministic slice is
complete.** Stage 70 measured P11 on a *one*-body union at 5.6 %; adding a
`part` branch to the live encoding before that is understood would measure
two unknowns at once, which is the mistake Stage 44 and Stage 48 each found
in a different place.

### Next: multi-body semantics (design only, nothing implemented)

`docs/multi-body-design.md` defines the smallest real vertical slice, written
against the code as it stands. The shape of it:

- **`part` is a declaration**, the first operation that produces no geometry.
  A plan with no `part` means one body, exactly as today, so every existing
  plan, corpus case and fingerprint is untouched by construction.
- **Two live bodies with no `part` stays a failure.** A leftover solid and a
  declared second body are different facts; inferring the second from the
  first is the silent behaviour Stage 62 removed. New rules P33-P36, and
  `executor.finished` gains one explicit exemption rather than a weakened
  gate.
- **Identity and body-local history are largely already there** — Stage 46's
  `Body` carries id, origin, ordered features, liveness and what consumed
  it, and the executor already holds one shape per body. The work is the
  declaration, the selector scoping, and the four downstream semantics.
- **No output may fuse bodies the plan did not fuse, or drop one.** One mesh
  per body, per-body measurements, a STEP assembly, and `result.part` stays
  "the single live body or `None`" — widening it to "the first one" is the
  Stage 62 bug by another name.
- **Deliberately excluded:** transforms, mates, joints, constraints, per-body
  visibility in the plan, sub-assemblies, and any model-facing grammar until
  the slice works deterministically. Stage 63 measured the model failing P11
  on a *one*-body union 4/5; adding a `part` branch before that is understood
  would measure two unknowns at once.

Its own pre-check is run and recorded: six loose plates give a clean
`multiple_solids` refusal naming all six, with a valid plan and
`result.part is None` — so the refusal is at the execution layer, where
`part` can make the result legal without making an illegal plan legal.

### What it is

Two questions the stable branch cannot ask: **is a flatter CAD representation
easier for a model to generate correctly than the V1 document?** and **is
CadQuery the right engine?**

It adds `apps/api/src/cad_experimental/` (an operation-plan language, its
parser, a plan validator with rules P1–P32, a feature graph with role-tagged
edges, a shared solid-set walk, semantic edge selection and a graph-driven
executor, a V1 adapter, its own prompt and FastAPI app, and two CAD
backends), `apps/api/tests_experimental/`, and
`apps/web-experimental/` on port 5174. The **operation plan** drops `units`,
`schema_version` and `features` for a flat ordered `operations` list.
**Eleven** operation types; **eight are executable** — `sketch`, `extrude`
and `revolve` are represented and validated, then explicitly refused at the
execution boundary rather than approximated. Of the eight, six map
one-to-one onto a V1 feature (`V1_FEATURE_TYPES`), `pattern` expands into
one feature per instance, and **`union` has no V1 form at all**: V1 cannot
join two solids, so `plan_to_document` raises `ExecutionUnsupported` for it
and such a plan is built by the **graph executor** instead. The schema the
provider sees still has **eight branches** — the measured ceiling —
because `subtract` and `union` are shape-identical and merge into one, as
`fillet` and `chamfer` already do.

### Findings worth not re-deriving

**Anthropic structured output enforces several limits at once**, measured
against the live API and documented nowhere. These are the *rules*, and they
still hold:

| Rule | Measured |
|---|---|
| optional properties, whole document | ≤ 24 |
| optional properties, **any single object** | ≤ ~14 (a 20-optional object alone is "too complex") |
| compiled grammar size | 8 operation branches accepted; **any 9th refused**, even stripped to one field |
| `oneOf` | rejected — `anyOf` only |
| `additionalProperties: false` | **mandatory on every object** |
| `exclusiveMinimum`/`maximum`/`maxItems`/`min\|maxLength` | rejected; `minItems` only 0 or 1 |
| unused `$defs` | still cost grammar budget, and `$ref` does **not** shrink it |

**Both schemas now satisfy every one of those rules.** The "31 optional
properties against a limit of 24" that blocked the operation plan at Stage 40
is **obsolete**: Stage 41 restructured the schema, and Stage 43 verified the
current state both offline and against the live API.

| | V1 JSON | Operation Plan |
|---|---:|---:|
| Optional properties (document) | **8** | **7** |
| Worst single object | 4 | 2 |
| Rejected keywords in the **raw** schema | `exclusiveMinimum`, `minLength` | none |
| **Accepted by the live API** | **yes** | **yes** |

Those counts are of the schemas **actually sent to the API**, which is the
only count that decides whether a grammar compiles. (`plan_schema()`'s own
internal counter reports 9 for the plan; it walks the schema slightly
differently. Both numbers are far under 24, and the difference is in the
counting, not in the schema — `sanitise_schema` provably removes no property
from either arm.)

V1's raw schema must be passed through Stage 40's `sanitise_schema` to
compile; the plan's is already clean. Sanitising removes **only** numeric and
length bounds — 29 and 47 properties identical before and after, `required`
lists unchanged, no property lost — and those bounds are still enforced by the
parser and the validator downstream.

**Stage 40, live Claude Haiku 4.5, 130 calls, structured output OFF.** With
structured output unavailable to one side and therefore disabled for both,
**both representations scored 0%** — every answer arrived in a markdown fence
and both parsers refuse fences by design. Replaying that recorded output with
fence tolerance gives V1 **24.6%** and the operation plan **86.2%** — a
*diagnostic*, not a score. V1's failures were envelope-shaped: prose, or a
correct document with no `status` wrapper. This run was reproduced exactly on
Windows before Stage 43 began.

**Stage 43, same 130 calls, structured output ON — see the stage index below
for the result.** Native structured output produced **valid, unfenced JSON on
both arms, 0/130 fenced**, which removed the transport contamination entirely
and let the two representations actually be compared.

### Stage 43: the comparison Stage 40 could not make

**The one intended difference from Stage 40 is structured output.** The model
(`claude-haiku-4-5-20251001`), both prompts, the 13-case corpus, 5 attempts
per case per arm, both parsers, both validators, the adapter, the build and
every scoring rule are Stage 40's, imported and used unchanged —
`representation_comparison.STRUCTURED_OUTPUT_ENABLED` is still `False` and the
saved Stage 40 baseline is untouched. Prompt and corpus fingerprints in the
Stage 43 result match Stage 40's exactly. Nothing strips a fence, repairs,
retries or re-prompts on either side.

Stage 43 also fixed the two production Windows bugs that made geometry
scoring meaningless on this machine (§15), because until then the operation
plan's builds failed for reasons that had nothing to do with the model.

**Live result, 130 calls, 0 provider errors, 0/130 fenced answers:**

| Metric | V1 JSON | Operation Plan |
|---|---:|---:|
| Build success | 15/65 = **23.1%** | 40/65 = **61.5%** |
| Semantic correctness | 40/65 = **61.5%** | 55/65 = **84.6%** |
| Wrongly refused | **25/65** | **10/65** |
| Correct refusals | 25/65 | 15/65 |

**Conclusion: this is strong evidence that the operation plan is the better
model-facing intermediate representation for executable CAD planning.** It
beats V1 on both axes at once — nearly triple the build success and a
23-point lead on semantic correctness — on the same model, prompt discipline,
cases and scoring, with the transport contamination that decided Stage 40
removed. The operation plan was **5/5 on all eight buildable cases (40/40)**.

**V1's failure mode is refusal, not error.** All 25 of its wrong refusals are
the modifier cases — `04-plate-centre-hole`, `05-plate-two-holes`,
`06-cube-bore`, `07-plate-chamfer`, `08-plate-fillet`, each 5/5. It refused
every part carrying a modifier, despite the engine having built those since
Stage 14.1.

**The operation plan's whole remaining gap is sketches.** Its 10 wrong
refusals are exactly `09-profile-extrude` (5/5) and `10-profile-revolve`
(5/5) — nothing else. **Stage 44 found those 10 were not the model's
judgement at all**: see below.

Read `docs/experimental-operation-plan.md` → *Stage 43* for the full detail.

### Stage 44: the profile refusals were forced, not chosen

Two things stood between the model and a profile plan, **either one
sufficient on its own**:

1. **The grammar had no such branch.** `provider_schema()` returned the
   schema over `EXECUTABLE_TYPES` — the six buildable operations. Stage 41
   built that subset because the nine-branch schema is refused on grammar
   size, and it was harmless there because Stage 41 ran *without* structured
   output. Stage 43 turned structured output on and kept it. A decoder
   constrained by a union with no `sketch`, `extrude` or `revolve` branch
   **cannot emit one**, so refusing was the only reachable answer and no
   prompt could have changed it.
2. **The prompt said to refuse** — *"neither an extrude nor a revolve can be
   built […] do not offer them as a way to make a part"*. The recorded output
   quotes it back as the model's `reason`.

The lesson worth keeping: **a schema is what the model may SAY; the execution
boundary is what the engine can BUILD.** Narrowing the first to match the
second looks conservative and is silent — it removes an answer from the
model's reach and then scores the model for not giving it.

The plan layer was innocent: the parser accepts all nine types, the validator
returns `valid=True` on both profile plans, and the adapter raises
`ExecutionUnsupported` exactly as Stages 37–38 designed. None of it changed.

**The fix.** `provider_schema()` now covers all nine types in **eight
branches** — the measured ceiling — by merging `fillet` and `chamfer`, the one
pair that differs only in the name of its single length. The cost is confined
to the grammar (both lengths become grammatically optional; `target` and
`edges` stay required) and the parser still enforces the real requirement, as
a test proves. Four schemas now exist, each named for its job:
`plan_schema()` (9 branches, the faithful description), `provider_schema()`
(the default), `compact_provider_schema()` (smaller, drops a sketch's optional
`constraints`) and `executable_schema()` (what Stages 41–43 sent). **Nothing
selects a variant automatically.** Prompt `2026-09-15.1` stops calling profile
operations unbuildable and leaves buildability to the engine, in the same
words the fillet section already used.

**Unverified, and it matters:** that the provider *compiles* the new schema.
Every offline limit is satisfied and asserted, but the compiled-grammar size
can only be measured by sending it — one live call, on a machine with a
credential. If it is refused, `compact_provider_schema()` is the next thing to
try, and it still admits every profile operation.

*(What Stage 44 thought came next — re-running Stage 43 once the schema was
verified — is superseded. Stages 45–47 changed the prompt and the schema, so
the Stage 43 harness can no longer be re-run as a measurement of them: see
**Where this branch stands** above.)*

Read `docs/experimental-operation-plan.md` → *Stage 44* for the full detail.

### Stage 45: the plan as a dependency and history graph

**Chained plans already worked.** `box → cylinder → subtract → fillet` parsed,
validated with zero problems and converted to a valid V1 document before this
stage existed; P9–P14 and P23 have always judged references against a walked
solid set. What was missing was anywhere to *see* the chain — the walk lived
inside `validate_plan` as four local dictionaries, so a plan was judged as a
history and then only ever reported as a flat list.

`cad_experimental/history.py` is now the **one** implementation of Section
B.4's solid set, and `validation.py` consumes it rather than keeping a copy —
a second walk would be a second opinion about what "consumed" means.
`plan_history(plan)` builds a typed graph on it: per step `depends_on`,
`consumes`, `declares_solid`/`declares_profile`, `modifies` and the solid set
either side; per plan `terminal_solids`, `consumed`, `profiles`,
`dependents()`, `producers()`, `derivation()` and `depth`.

`derivation()` is what makes it a history rather than a list: for the chain
above it returns `("body", "cutter", "cut", "edges")` — **the cutter is part
of what `body` is made of** although it was consumed two steps earlier.
`depth` measures chaining, which an operation count does not: four unrelated
boxes have depth 1, that chain has depth 3.

**Reported, never enforced.** `terminal_solids` of length two is a fact, not a
verdict; S9 on the converted document still rules on it. No P-code was added.
This matters in one case: a plan containing a profile operation is refused by
the adapter before a document exists, **so S9 never runs and a leftover solid
is otherwise invisible**. The graph is its only witness.

`POST /experimental/validate-plan` returns a `history` object. Prompt
`2026-09-15.2` gained a sequence section stating the three chain rules and
that exactly one solid must be left; a test parses its worked example out of
the prompt and validates it, so the prompt cannot teach a plan the validator
rejects.

**No new operation was added** — no pattern, no instance. The vocabulary was
still nine types **at this stage** (it is eleven now — `pattern` arrived at
Stage 46 and `union` at Stage 61), `history.py` imports no kernel and
computes no geometry, and P1–P26 were unchanged.

Read `docs/experimental-operation-plan.md` → *Stage 45* for the full detail.

### Stage 46: the feature graph, and `pattern`

`cad_experimental/graph.py` holds **structure** — no state, no geometry, no
verdict, no kernel import, every function total so it describes a broken plan
rather than refusing to. Nodes carry what they produce (`solid` / `profile` /
`nothing`) and which body they change. Edges come in two kinds and keeping
them apart is the design:

- **Declared** edges are what the plan says, each tagged with a **role**:
  `target` (a solid, or a profile for a sweep), `tool` (ordered), `source` (a
  pattern's feature). One `expectation(kind, role)` table replaced the
  per-operation category branches, so adding an operation is one answer in one
  place instead of edits to three modules.
- **Derived** edges (`FeatureNode.after`) are the **per-body feature
  history**, and they fix a real bug the declared edges could not express. In
  `plate → bore → mount → mounts → break`, all three modifiers name only
  `plate`, so a sort over declared edges alone yields `plate, bore, mount,
  break, mounts` — chamfered before the holes, a different part. **A modifier
  depends on its target's state, not merely on its identity.** A test pins the
  wrong order so the bug cannot return.

`topological_order()` therefore equals the list order for every valid plan,
and `is_list_order()` now *checks* that rather than assuming it. Cycles are
unreachable through the parser (P10) and detected anyway; a self-loop is a
cycle of one, reported as **P31** naming its members.

**`pattern`** is the tenth type and the first graph-native one: its input is a
feature, not a body. `count` **includes the source**; the source is instance
0, unmoved and unconsumed. A pattern **inherits its source's semantics** — so
repeating a modifier is modifying, the body keeps its id, and S9 is untouched.
Placement is a discriminated object, `linear` or `radial`; radial `angle` is
the step between instances and omitting it means `360 / count`. What may be
repeated is a table (`PATTERNABLE_TYPES`, today `through_hole` only).

The arithmetic lives in `pattern.py`, **not** the adapter: where an instance
goes is a fact about the representation, not about an engine. Instance *k* is
computed from the source and *k* alone — accumulating a rotation would let
error grow along the pattern. New rules **P27** (source repeatable), **P28**
(count in `[2, 64]`), **P29** (placement coherent *and expressible* — a radial
pattern must turn its source about an axis it is already parallel to, or the
instance could not be written down in V1 at all), **P30** (derived ids do not
collide).

Measured: a plate with a Ø20 bore and four Ø6 holes on a 35 mm bolt circle
built to `95727.43399111787` mm³ against a closed form of `95727.43399111788`
— 1 ULP, one solid.

`pattern` is **executable but is not a V1 feature**. Stage 46 split those:
`V1_FEATURE_TYPES` is the six that become one feature each;
`EXECUTABLE_TYPES` is what the adapter can translate. `executable_schema()`
stays pinned to the first, so Stage 43's fingerprint `54759d1e16cfe634` is
unchanged — a test asserts it. The schema now carries **ten types in eight
branches**; provider acceptance is still unverified.

**Multi-body groundwork:** `PlanHistory.bodies` gives every body an id, an
origin, its ordered features, whether it is live and what consumed it, plus
`owner_of()` and `live_bodies`. Nothing assumes one body; S9 still requires
one and is unweakened. Assemblies are **not** started.

Read `docs/experimental-operation-plan.md` → *Stage 46* for the full detail.

### Stage 47: semantic edge selection, and the seam

**Root cause, measured.** OpenCascade represents a cylindrical face's
parameterisation seam as a genuine straight edge — on a drilled plate it has
the **same curve type, direction and length** as an outer corner, so
`axis_parallel Z` matched four corners *and* the seam. No blend can take a
seam (the kernel builds no contour for it), and since Stage 14.1 a matched
edge is never quietly dropped, so the whole modifier failed with E5. Nothing
geometric separates them; `BRepTools::IsReallyClosed` — the edge closes a
periodic face — does.

**Four selector kinds** in the experimental IR: `all` and `axis_parallel`
exactly as Section C.7 defines them (**not redefined**, so old plans mean what
they meant), plus `straight` (axis-parallel lines, seams excluded — what "the
corners" means) and `circular` (circular edges about an axis — a hole's rim),
with `"position": "top"|"bottom"` narrowing a `circular` selection to one end.
`position` is **extremal, not ordinal**: two holes through a plate both have a
top rim and both are named, because picking one would be a guess.

**`cad_experimental/edge_semantics.py` imports nothing** — no kernel, no
backend, no `cad_core`, no project module. A backend reports plain `EdgeFacts`
(curve, direction or centre and normal, radius, **is_seam**, adjacent surface
names) from `describe_edges`, the one place OCC topology is read; the resolver
decides on those numbers. No CadQuery string, OCC enumeration or edge index
ever reaches the IR.

**Three resolution codes**, separate from P- and S/E-codes: `R1` matched
nothing (and says what the solid has), `R2` the selection contains a seam
(naming the two selectors to use instead), `R3` a `position` could not
separate the candidates. A failed resolution still reports its candidates.
Ordering is **geometric** — defining point along the axis, then radius — with
the backend's index as final tie-break only, so nothing depends on the
kernel's enumeration order.

**Two build paths, chosen explicitly.** A V1 document carries only two
selectors, so the adapter raises `SelectorNotExpressible` (distinct from
`ExecutionUnsupported`: the *document format* is behind, not the engine) and
`build_plan` sends such plans to `cad_experimental/executor.py` — which walks
Stage 46's `topological_order()`, holds one backend shape per live body, and
introduces no second history. `PlanBuild.executed` says which path ran.

**Proved at the execution level** with closed forms: fillet and chamfer on a
hole rim (chamfer to `2π(R + d/3)·d²/2` per rim), top vs bottom rim, corners
vs rim disjoint, the seam never selected, two holes deterministic, and a
pattern's four bolt-circle rims chamfered by one selector.

**Deliberately deferred:** `inner`/`outer` (not needed for these cases — the
tube measurement is recorded for when it is), face and vertex selectors, named
topology, and `describe_edges` on FreeCAD, which raises rather than answering
wrongly.

**Next limitation:** no model has been asked to produce a semantic selector.
The prompt names the four kinds and which to use for "round the corners" and
"break the edge of the hole", but that is a hypothesis until it is measured.
Stage 48 built the instrument that can measure it — cases `F1`–`F4` — and it
has not been run live. (The prompt is `2026-09-15.5` since Stage 48; it was
`2026-09-15.4` here.)

### Stages 48–49: the provider's compiled-grammar ceiling blocks measurement

**Stage 48 exists and cannot run** — *as of Stage 48-49, and no longer
true.* Stages 50-51 found encodings that compile (see *RESOLVED* below), and
Stage 63 measured the one the live route sends. Read this subsection as the
record of how the ceiling was found. At the time: its instrument was
complete and the provider refused the operation-plan grammar. Measured by a five-call diagnostic probe,
in the provider's own words on all four plan requests:

> `400 invalid_request_error` — *"The compiled grammar is too large, which
> would cause performance issues. Simplify your tool schemas or reduce the
> number of strict tools."*

The `v1_json` control was accepted in the same run, which rules out model
availability, credential, transport and outage; two different descriptions
failed identically, which rules out the request text. **`--plan-schema
compact`, the documented fallback, was refused too.**

**Serialized size is only a proxy — measure the inlined schema.** The limit
is on the *compiled* grammar; a `$ref` does not shrink it and unused `$defs`
still cost budget, so a definition referenced five times is compiled five
times. `compact` is 19% smaller than `provider` in bytes and was refused
just the same. Measured bounds, inlined: **accepted 3622, refused 6190**.

**One capability dominates.** Marginal cost against the six-type base:
`sketch` **+2653**, `pattern` +1014, `extrude` +459, `revolve` +455,
semantic selectors +150 — of which sketch's `constraints` alone are +1161.
Sketch is 73% of the growth; Stage 46's and 47's additions are nearly free.
**The six types plus sketch alone already measure 6275 — above the refused
point** — so no grammar carrying a full-fidelity sketch is expected to
compile.

**RESOLVED at Stages 50–51: profile encodings that compile.** Measured
live — `profile` 3487, `executable` 3622, `profile_hole` 4030,
`profile_union` **4481 ACCEPTED**; six+sketch 4551 and nine-types-no-sketch
4698 both REFUSED. **Ceiling (4481, 4551]** — a bound, not a number. A
sketch-free grammar at 4698 was refused while a sketch-carrying one at 4481
was accepted, so **sketch is not a special case**: the six-type solid base
was consuming the budget. `plan.py` now exposes `profile_provider_schema`,
`profile_hole_provider_schema` and `profile_union_provider_schema`; the last
carries seven of ten types and is the one to reach for. None can express
`fillet`, `chamfer` or `pattern` (the edge pair measures 4741, past the
refusal). Selection is explicit via `PLAN_SCHEMAS` and **nothing falls
back** — `DEFAULT_PLAN_SCHEMA` stays `provider` even though `provider` is
refused, because changing it silently would rewrite what earlier runs meant.
**Stage 48 must run as two instruments** (`profile_union` 10 cases,
`executable` 8), with four cases expressible under neither and reported as
*not expressible* rather than as model refusals.

**`cad_experimental.schema_ladder` (Stage 49)** builds variants between the
two measured points so the ceiling can be found one call at a time. `L0`
reproduces `executable_schema()` and `C2` reproduces
`compact_provider_schema()` exactly, both test-asserted, so the metric is
anchored to the two points the provider has ruled on. Only three variants sit
in the unknown band: `C6-sketch-floor` (4551), `C4-profiles-no-sketch`
(4698) and `C5-minimal-profiles` (5010).

**The provider schema is an encoding of the IR, not the IR.** It constrains
what a model may *say*; the parser and validator decide what a plan *means*
and never see it. Omitting a sketch's `constraints` from a grammar does not
make constraints illegal — a plan carrying them still parses, and a test
asserts it. **Do not redesign the operation plan to fit one provider's
grammar budget**; a different provider needs a different encoding, not a
different IR.

```powershell
# offline, calls nothing
python -m cad_experimental.schema_ladder --list
# ONE variant, ONE call. There is deliberately no --probe-all.
python -m cad_experimental.schema_ladder --probe C5-minimal-profiles
```

**FreeCAD runs here, but not the obvious way.** No pip distribution, and it is
**not in Ubuntu 24.04**. It came from the official AppImage (649 MB, extracting
to 2.4 GB), gives **FreeCAD 1.0.0**, and imports headlessly *alongside*
CadQuery in one process. Its bundled `libssl` conflicts with the system
`libcrypto`, so `LD_LIBRARY_PATH` must be set **before Python starts**. Both
engines produce bit-identical volumes on all six golden parts.

**In Claude Code Web the Anthropic credential arrives as
`CAD_ANTHROPIC_API_KEY`,** because the platform reserves and strips
`ANTHROPIC_API_KEY` — which is what `cad_ai` reads. §7's note is right for the
Windows machine; in the cloud a caller must bridge the value across.

### Stage 48: the evaluation instrument for Stages 44-47

**This stage measures nothing yet, and that is what it says.** It is the
harness, the corpus and the discipline; the numbers are a local run away.

**Why Stage 43 could not be re-used.** It is pinned to `executable_schema()`
— six types, no `sketch`/`extrude`/`revolve` branch, two selectors — and a
test asserts that pin. Running it today would send the old grammar with the
current prompt, which tells the model to use `pattern`, `straight`,
`circular` and profile chains that grammar forbids: Stage 44's finding
repeated on purpose. **Stage 43 is untouched**, and four tests prove it.

**The finding, before any run.** Stage 46 added `pattern` as a full
operation — its own prompt section, executable, in `provider_schema()`'s
eight branches — and **left `patterns` in the prompt's UNSUPPORTED list**.
The prompt said "use a pattern for a bolt circle" and, forty lines later,
"decline patterns". Every pattern case would have returned `unsupported` and
been recorded as the model's judgement. Prompt `2026-09-15.5`
(`21d564ddeba05a74`) removes it and adds the carve-out the `extrude`/
`revolve` entry already had.

**A Stage 38 test was pinning the contradiction in place** — it *required*
`patterns` to appear in the refusal list, because at Stage 38 that was true.
Stage 46 did not update it, so the suite held the error rather than catching
it. Corrected, with a mirror test for the carve-out. **When an operation is
added, the refusal list is part of the operation — and so is the test that
pins it.**

**Two result groups, never one number.** `legacy` is the thirteen Stage 40
requests answered by **both** representations — *read out of the frozen
`comparison_corpus` object at import*, not retyped, with a test asserting
character-for-character identity. `capability` is seventeen plan-only cases
covering sketch chains, `pattern`, semantic selectors, deeper graphs,
ambiguity and an impossible part. `summarise()` produces no combined rate and
a test asserts the absence of one.

**Not a delta against Stage 43.** Three things moved at once: the schema
(`54759d1e16cfe634` → `be8ba82740aecc1d`), the prompt (`2026-09-10.7` →
`2026-09-15.5`), and — the subtle one — **the scoring path for a semantic
selector**. Stage 40's runner calls `plan_to_document` directly, so a
`straight` or `circular` selector raises `SelectorNotExpressible` and scores
`SEMANTICALLY_INCORRECT`. That was right when none existed; today it would
score a correct, buildable answer wrong, and legacy cases `07`/`08` are
exactly where the current prompt steers towards one. Stage 48 builds through
`build_plan`, which picks the document path or the graph executor
explicitly.

**Five expected classes**, three of them Stage 40's own constants imported
rather than redefined: `build` (19), `valid_unexecutable` (4), `unsupported`
(4), and two new — `clarification` (2, a required value is absent) and
`no_part` (1, the part cannot exist; either refusal word is accepted, a plan
never is).

**The offline preflight is the stage's real deliverable.** `--check` calls no
model and verifies: both schemas against every measured provider limit (plan
**16** optional properties, worst object 4, **8 branches**); that the legacy
text is identical to the frozen corpus and no capability case carries a V1
expectation; that **all 19 buildable expectations agree with the real
kernel**, 7 of them via the graph executor; that 9 deliberately broken plans
are refused by the right layer with the right P-code; and that **all seven
Stage 40/43 baseline files match their recorded SHA-256**. `--self-check`
then runs the whole loop against developer-written reference plans, 19/19 —
stamped `is_live_model_result: false`, because **that is a statement about
the harness and says nothing about any model.**

**Baselines cannot be overwritten.** `run()` raises `BaselineMissing` if a
digest moved, `_write()` refuses any path inside `stage40-v1-vs-operation-plan/`
or `stage43-structured-output/`, and the CLI refuses such an `--out` before
doing anything at all.

**Four new metrics, each marked new in `SCORING_RULES`:**
`correct_valid_unexecutable`, `correct_clarification`, `selector_correct`
(F2 and F3 chamfer to the *same volume*, so only the selector tells the top
rim from the bottom) and `executed_by_graph` (a count, not a rate, not a
quality signal). Three new codes: `WRONG_SELECTOR`,
`INVENTED_MISSING_VALUE`, and `CORRECT_CLARIFICATION` — a success, and
deliberately not `OK`, which means "built the part that was asked for".
Every Stage 40 metric keeps its meaning and Stage 40's own success tuple is
not widened; `scoring_fingerprint()` hashes the definitions so that changing
one is visible.

**Still unverified, and only answerable live:** that the provider *compiles*
the widened schema. `--probe-live` asks in three calls;
`--plan-schema compact` is the documented fallback and nothing selects it
automatically.

Full detail: `docs/experimental-operation-plan.md` → *Stage 48*, and
`docs/evaluation-baselines/stage48-widened-schema/README.md` for the exact
local commands.

### Commands

**Windows is the primary development machine, so these are the Windows
forms.** `PYTHONPATH` must be **`;`-separated** and the interpreter is the
venv's `python.exe` — a `:`-separated value is silently ignored here and the
imports then resolve to the editable installs, which means a suite can run
green against a completely different checkout (§15).

```powershell
$repo = "C:\Users\AtharvaBhangale\Desktop\AI-assisted-text-to-CAD-application"
$env:PYTHONPATH = "$repo\packages\cad-core\src;$repo\apps\api\src;$repo\apps\api\tests_experimental"

# the whole experimental suite — run from tests_experimental
Set-Location "$repo\apps\api\tests_experimental"
& "$repo\.venv\Scripts\python.exe" -u -m unittest discover -s . -t .

# one class, or one test
& "$repo\.venv\Scripts\python.exe" -u -m unittest test_sketch.SketchParsingTests.test_a_sketch_parses

# fixtures and free checks — none of these call a model
Set-Location "$repo\apps\api"
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.local_plan_provider --list
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.representation_comparison --check
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage43_structured_comparison --check

# Stage 48 — also free, and the preflight builds 19 reference parts
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --list
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --check
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --self-check
```

A live run additionally needs the credential exported into *that* process
(§7) as `CAD_ANTHROPIC_API_KEY`, which is what the harness reads. The two
Stage 48 live commands — `--probe-live` (3 calls) and `--live` (215 calls at
5 attempts) — are written out in full in
`docs/evaluation-baselines/stage48-widened-schema/README.md`.

The POSIX forms remain correct on Linux/macOS, where `python3` and a
`:`-separated `PYTHONPATH` are right, and where FreeCAD needs
`CAD_FREECAD_HOME` plus `LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib` set
**before Python starts**, or `test_cad_backends` skips.

**1979 passed, 5 skipped** (1984 collected) with FreeCAD 1.0.0 present on
Linux and its environment exported, measured at Stage 74. The +43 over
Stage 72 is exactly the two new modules: `test_body_measurement` (26) and
`test_step_assembly` (17).

**The two numbers are not in conflict**, and the pairing is worth keeping:
unittest's "Ran N tests" is the COLLECTED count and includes the skips, so
1984 collected = 1979 passed + 5 skipped + 0 failed. Both were re-measured
at `8e1371c` before this stage began, which is how the Stage 71 pair
(1915 collected / 1910 passed) was confirmed rather than corrected.

Earlier figures, each describing a real environment: 1936 at Stage 72;
1910 at Stage 71;
1867 at Stage 70; 1856 at Stage 69;
1852 at Stage 68;
1800 at Stage 64;
1778 at Stage
63 (before this stage's 22 policy and drift guards); 1188 tests, 2 skipped
at Stage 47; 897 tests, 33 skipped at Stage 43 on the Windows environment,
where the extra skips are the FreeCAD backend. `cad-core` is **1481 passed**
on Linux — §5's six errors there are Windows-only.

Run the whole suite before finishing a stage here: package-wide guard tests
in older modules are routinely tripped by newer ones, and focused subsets
have missed that twice.

Note that a `python.exe` run which imports `cad_core` may exit non-zero
(`139`, `116`, `5`) *after* printing a correct result — that is §15's
OpenCascade teardown abort in the host process, not a failure. Read the
output, not the exit code.

### Invariants that branch enforces

- **`CAD_BACKEND` defaults to `cadquery` and stays that way**, and
  `resolve_backend()` **never falls back** — a caller who asked for one engine
  and silently got another cannot know which engine built their part.
- **Neither parser strips markdown fences and neither repairs output.** A
  deliberate measurement decision on both sides.
- **`comparison_corpus.py` is a frozen instrument.** Fix the prompt or the
  code and re-measure; never edit an expectation after seeing a score.
- **A schema is what the model may say, not what the engine can build.**
  Conflating the two is what made Stage 43 record a forced refusal as the
  model's choice. `provider_schema()` covers the whole vocabulary;
  `ExecutionUnsupported` is where buildability is decided.
- **No schema variant is ever selected automatically.** `provider_schema()`,
  `compact_provider_schema()` and `executable_schema()` are chosen explicitly
  and recorded, for the same reason `resolve_backend()` never falls back.
- **Stage 43's module is pinned to `executable_schema()`** and does not follow
  `provider_schema()`. A recorded result must stay attributable to the
  instrument that produced it: re-running Stage 43 must reproduce Stage 43.
  Stage 48 is a **separate** module, corpus, version and output directory —
  it never edits Stage 43 to reach the current schema.
- **`stage48_corpus.py` is a frozen instrument too**, and its `legacy` group
  is *read out of* `comparison_corpus` rather than copied, so the two can
  never drift. Its expectations were written before any live call and must
  never be edited after a score is seen.
- **The Stage 40 and Stage 43 baseline files are immutable, and enforced.**
  Stage 48 records their SHA-256, refuses to run if one moved, and refuses
  any output path inside either directory.
- **An operation is not one edit.** It is a type, a parser rule, a validator
  rule, a backend method, an executor branch, a prompt section, a refusal
  list, **the grammar the live route actually sends**, every measurement
  instrument that names it, and every error message explaining why it cannot
  be done. Miss any one and the model is scored for an answer it could not
  give. Stage 44 found this for `sketch`, Stage 48 for `pattern`, Stage 62
  for `union` — three times, in three different places, so treat the list as
  a checklist rather than a story about past mistakes.
- **A prompt's refusal list is part of the vocabulary**, and so is the
  grammar. Adding an operation and leaving it in the UNSUPPORTED list, *or
  leaving it out of the encoding the live route sends*, makes the model
  decline something the language has, and the run records the contradiction
  as the model's judgement.
- **`PROVEN_COMPILABLE` means "a live probe accepted this size"**, never "we
  expect it to be accepted". An encoding whose compilability has not been
  measured stays out of it however far under the known ceiling it sits.
- **The graph executor needs its own S9.** The document path inherits the
  single-solid rule from the V1 validator; an executor-only plan (`union`
  today) never builds a document, so more than one live body must be refused
  there explicitly. Reported, never repaired — neither fusing the extra body
  in nor dropping it is a guess this layer may make.
- **A test that passes without proving its name is worse than a missing
  test.** Stage 63 found four: a list of "unimplemented" operations that
  were nine-tenths implemented and rejected for a different reason; an
  interface check omitting `union`; a capability tuple that let `union` be
  expressible by no schema variant at all; and a test whose name stated a
  rule `union` had falsified. Each reported coverage it did not give.
  Assert the REASON, and derive a list from its authority rather than
  hand-writing it.
- **One walk, one answer.** `history.plan_history` is the only
  implementation of the solid-set walk. A second one — even a three-line
  "first box or cylinder" — is a second opinion about what "consumed"
  means, and it silently disagreed the moment `union` arrived: holes and
  edge treatments targeted a consumed solid (P12), and a resize edited one
  plate of a fused body and reported the part resized.
- **A part assembled from several pieces has no single dimension.** Refusing
  to resize it is the honest answer; editing one piece and saying "Updated"
  is not.
- **A stale measurement case is marked, not edited.** When the language moves
  under a case, its expectation stops describing the right answer. Mark it
  stale, keep running it, report it, and hold it out of every quality
  denominator — a number mixing "the model was wrong" with "the question was
  wrong" means neither. Re-baselining is its own stage.
