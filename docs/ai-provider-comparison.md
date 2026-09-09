# AI provider adapters

Status: **Stage 28A — two provider adapters implemented and tested. No live
comparison has been run, and none is claimed.**

> **Neither provider is claimed to be better than the other.** No live
> benchmark has been run against either one. This document describes the
> adapters, not their output quality.

## The shape

```
                      cad_ai.provider.TextToCadModel
                    (one method: generate(ModelRequest))
                                   │
                ┌──────────────────┴──────────────────┐
        AnthropicTextToCadModel              GeminiTextToCadModel
          (anthropic SDK)                     (google-genai SDK)
                └──────────────────┬──────────────────┘
                                   │  ModelResponse — plain text + metadata
                        cad_ai.generation.TextToCadService
                                   │  the same prompt, the same schema
                        cad_core deserializer + validator
                                   │  S1–S20, unchanged
                        cad_ai.evaluation — the same 35-case corpus
```

Everything below `ModelResponse` is shared. A provider decides *how* to ask a
model for JSON; nothing else about the system changes with that choice.

## What the two adapters have in common

| | |
|---|---|
| **interface** | `TextToCadModel`: one `generate(ModelRequest) -> ModelResponse` |
| **system prompt** | byte-identical. `PROMPT_VERSION 2026-09-08.1`, fingerprint `2b3e3395…9ba52`. Neither adapter reformats, wraps or appends to it |
| **output schema** | one `response_schema()`, derived from `cad_core.model`'s constants. **There is no Gemini-specific CAD schema**, and a test asserts none exists |
| **CAD contract** | the same V1 document, judged by the same validator |
| **outcomes** | the same five: `GENERATED`, `NEEDS_CLARIFICATION`, `UNSUPPORTED`, `MODEL_ERROR`, `INVALID_MODEL_OUTPUT`. No `GEMINI_ERROR` and no provider-specific outcome exists |
| **evaluator** | the same harness, the same 35 cases, the same comparison and metrics |
| **tools** | none. Neither adapter sends tools, function declarations, code execution, search grounding or URL context |
| **turns** | one. One call per generation; no repair, no retry, no conversation |
| **credential** | read once from the environment, handed to the SDK, retained nowhere. Absent from every serialized config, result and error message |
| **SDK import** | lazy, inside the adapter. Importing `cad_ai` needs neither SDK |

## Where they necessarily differ

Everything here is **measured from the installed SDK**, not assumed.

| | Anthropic | Gemini |
|---|---|---|
| SDK | `anthropic` 1.4.0 | `google-genai` 2.22.0 |
| extra | `cad-api[ai]` | `cad-api[ai-gemini]` |
| credential | `ANTHROPIC_API_KEY` | `GEMINI_API_KEY` |
| call | `client.messages.create(...)` | `client.models.generate_content(model, contents, config)` |
| system prompt | `system=` parameter | `GenerateContentConfig.system_instruction` |
| user text | `messages=[{role, content}]` | `contents=` |
| structured output | `output_config={"format": {"type": "json_schema", "schema": …}}` | `GenerateContentConfig.response_mime_type="application/json"` + `response_json_schema=` |
| token bound | `max_tokens` | `GenerateContentConfig.max_output_tokens` |
| response text | concatenated `text` content blocks | `GenerateContentResponse.text` |
| stop reason | `stop_reason` | `candidates[0].finish_reason.name` |
| usage | `usage.input_tokens` / `output_tokens` | `usage_metadata.prompt_token_count` / `candidates_token_count`, **mapped onto the same two names** so the harness aggregates one vocabulary |
| error base | `anthropic.AnthropicError` | `google.genai.errors.APIError` |
| default model | `claude-sonnet-5` | `gemini-2.5-pro` |

### The difference that matters for measurement

**Anthropic's SDK exposes no decoding controls. Gemini's exposes four.**

| | `temperature` | `top_p` | `top_k` | `seed` |
|---|---|---|---|---|
| `anthropic` 1.4.0 | — | — | — | — |
| `google-genai` 2.22.0 | ✓ | ✓ | ✓ | ✓ |

**Neither adapter sets any of them.** Leaving each SDK's own defaults is what
keeps a run against one comparable with a run against the other, and it keeps
a benchmark a measurement rather than a tuning exercise. A run records the two
facts separately:

```json
"deterministic_decoding": "available",   // the controls exist here
"decoding_controls_used": "none"         // and none was used
```

so *"could not be set"* and *"could have been set, was not"* never read the
same. This is the one place a future cross-provider comparison needs care: a
gap between two runs is not automatically a gap between two models.

### Model-name verification

The Anthropic SDK ships a `Model` literal, and a test asserts the default
appears in it. **The Gemini SDK ships no equivalent**, and `models.list()`
needs a credential, so `gemini-2.5-pro` **has not been verified offline**. It
is overridable with `CAD_AI_MODEL` and is verified at run time by the API
itself — an unknown name is a provider error, not a silent fallback. No claim
is made here that the name is live-valid.

## Choosing a provider

```sh
CAD_AI_PROVIDER=gemini            # explicit
python -m cad_ai.evaluation --provider gemini   # per-run, overrides the above
```

With neither set, the first provider in `("anthropic", "gemini")` whose
credential is present wins; with neither credential present, the default
`anthropic` is reported and the provider itself refuses at construction.

This is **two named implementations, not a plugin system**: `PROVIDER_NAMES`
lists both, `_live_model` branches on the name, and a third would mean writing
a third class and naming it there. No discovery, no entry points, no dynamic
loading.

## Live benchmarking is deferred

**No live run has been made against either provider.** Selecting a provider
does not contact it, and a credential being present is deliberately **not**
sufficient to start a run:

```
==========================================================================
GEMINI BENCHMARK: NOT_RUN
==========================================================================
No model was called: a live run requires --live.

configured provider : gemini
configured model    : gemini-2.5-pro
credential present  : yes
```

A benchmark spends money and must be started deliberately, so `--live` is
required. Tests assert that the default path builds no client, opens no
socket and constructs no provider even with a credential in the environment.

When a live run is wanted:

```sh
python -m cad_ai.evaluation --live --provider gemini --build
```

## How the adapters are tested without credentials

The whole suite runs with **both** credentials absent, and behaves identically
when they are present — verified by running it both ways.

**A credential in the environment never causes a live call in the ordinary
suite.** The one live benchmark test is gated behind an explicit
`CAD_AI_LIVE_TESTS` opt-in *as well as* a credential, so CI cannot start
spending money because a key happens to be configured. It skips with a message
saying so.

* **The real Gemini adapter is tested against a mocked SDK client**, not only
  through a fake provider: the verified method, the model, the structured
  output configuration, the system prompt, the user text, the token bound,
  the absence of tools, and the response conversion are each asserted.
* **A deterministic stub** (`apps/api/tests/provider_stubs.py`) supplies 14
  response shapes — valid box, valid cylinder, malformed JSON, prose, empty,
  whitespace, `null`, a JSON array, a markdown-fenced document, valid JSON
  that is invalid CAD, an out-of-subset document, a clarification, a refusal
  and an unknown status — plus provider failures. Each is mapped through the
  **existing** generation service onto the **existing** five outcomes.
* **A synthetic credential** — a clearly non-real constant defined in the test
  file — proves a key never reaches a message, a `repr`, a serialized config,
  an evaluation result, or any shipped source file. The test also asserts that
  no file under `src/` or `docs/` contains anything shaped like a Google API
  key, which is why this document does not quote one.
* **No network, no writes, no execution**: sockets, `eval`/`exec`/`compile`,
  `subprocess` and `os.system` are patched to raise across every response
  shape, and the temp trees are snapshotted before and after.

## One thing worth knowing

**The supported-feature subset is a prompt policy, not a validation rule.** A
model that ignores the prompt and emits a `through_hole` produces a document
the validator *accepts*, because a drilled plate is legitimate V1 CAD. Neither
adapter invents a rule to reject it. That judgement belongs to the evaluation
layer, which scores the case as `UNSUPPORTED_FEATURE_ACCEPTED` when the
request required a refusal. Keeping the two judgements in different layers is
deliberate, and a test covers both halves.
