# Natural language → CAD specification

Status: **Stage 26 — the first AI interpretation layer. Stage 30 put it
behind `POST /generate` and wired it to the browser, so the layer is no longer
service-only. Not production-ready.**

> **The LLM does not generate executable CAD code.** It emits a CAD *data*
> document and nothing else — no Python, no CadQuery, no OpenCascade, no
> OpenSCAD, no FeatureScript, no STL, no STEP, no mesh, no code in any
> language — and nothing it returns is executed. The existing validator is
> the authority on whether that document is valid; the existing engine is the
> only thing that turns a document into geometry.

## The pipeline

```
natural-language description
  │  cad_ai.prompt.system_prompt()        one prompt, one place, one version
  │  cad_ai.provider.TextToCadModel       one call, no tools
model text (a string; not CAD)
  │  json.loads                           plain parsing, no repair
candidate response  { status, document, summary, questions, issues }
  │  CadApplicationService.validate_document
  │    -> cad_core.serialization.deserialize_part
  │    -> cad_core.validator.validate      S1-S20, unchanged
AiGenerationResult
  │  (a separate, deliberate call by the caller)
CadApplicationService.build_document      the existing build pipeline
```

### The product flow, end to end (Stage 30)

```
browser: a sentence in a text box
  │  POST /generate                        cad_api.generation.TextGenerator
Gemini (or Anthropic)                      the configured provider, backend-side only
  │  the pipeline above                    parse -> deserialize -> validate
validated CAD document                     returned to the browser
  │  POST /build                           the SAME endpoint the JSON flow uses
build / cache / isolation                  unchanged
RenderModel  +  STEP / IGES / STL
  │  GET /builds/{key}/render, GET /artifacts/{id}
browser: an interactive 3D model
```

**The AI generates CAD intent. The CAD engine generates geometry.** Nothing in
this chain asks a model for a B-rep, a mesh, a STEP file or a line of code, and
no stage of it executes anything a model returned. The document is the only
thing that crosses from the AI layer, and the validator decides whether it may.

The browser never holds a credential: only the backend talks to a provider.
The frontend names no vendor at all — it posts a sentence to `/generate` and
reads back the outcome, and a test asserts the strings `gemini`, `anthropic`,
`openai`, `claude` and `prompt` appear nowhere in its sources.

The invariant, stated as the stage requires:

```
LLM output -> CAD document -> existing validator -> existing CAD/build pipeline
```

**not**

```
LLM -> arbitrary code -> CAD kernel
```

## Provider

| | |
|---|---|
| **providers** | Anthropic Messages API (default) and Google Gemini. Two named implementations chosen by configuration — no registry, no discovery, no plugin system |
| **SDK** | `anthropic` **1.4.0**, installed and inspected in this environment |
| **default model** | `claude-sonnet-5`, overridable with `CAD_AI_MODEL`. A test asserts the default is a name the installed SDK's own `Model` literal lists, so no model identifier was invented |
| **credential** | `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`, each read by its own provider only |
| **dependency** | the optional `ai` (`anthropic>=1.4`) and `ai-gemini` (`google-genai>=2.22`) extras of `cad-api`, each **imported lazily** inside its own provider module |

Nothing else was added: no agent framework, no orchestration library, no
vector store, no RAG, no tokenizer.

**Stage 26 shipped one provider; a Gemini adapter was added in Stage 28A.**
That is a deliberate, named exception to Stage 26's "exactly one provider"
rule, not a drift into pluggability: `PROVIDER_NAMES` lists both,
`_live_model` branches on the configured name, and a third would mean writing
a third class and naming it there. Nothing above `TextToCadModel` changed —
the prompt, the schema, the outcome taxonomy and the validation flow are
shared, so a run against either provider is comparable with a run against the
other. `docs/ai-provider-comparison.md` sets the two side by side.

**Live Gemini benchmarking is deferred.** The adapter is complete and fully
tested against a mocked SDK and deterministic stubs, but no live run has been
made against either provider and no model-quality claim is made for either.
Selecting a provider does not contact it, and a credential being present is
deliberately not enough to start a run — `--live` is required, because a
benchmark spends money and must be started on purpose. Credentials are read
from the environment, handed to the SDK and stored nowhere: no serialized
config, evaluation result, log line or error message can carry one, and tests
assert that with a synthetic key.

### What the real SDK actually offers, measured

Inspected rather than assumed, from the installed 1.4.0:

```
messages.create parameters:
  max_tokens, messages, model, cache_control, container, inference_geo,
  metadata, output_config, service_tier, stop_sequences, stream, system,
  thinking, tool_choice, tools, user_profile_id, workspace_id, ...
```

#### Anthropic

* **structured output is genuine here.** `output_config` accepts
  `{"format": {"type": "json_schema", "schema": {...}}}` —
  `anthropic.types.json_output_format_param.JSONOutputFormatParam` requires
  exactly those two keys. So the model is *constrained* toward the document
  shape, not merely asked in prose. A test reads the live signature and
  asserts the parameter exists and that `response_format`, `output_format`
  and `json_schema` (names this layer might have invented) do **not**.
* **there is no `temperature`, `top_p`, `top_k` or `seed` parameter.** None is
  sent, and none can be. See *Determinism* below; this is measured, not
  assumed, and a test pins it.
* `tools` and `tool_choice` exist and are **never sent**. A test asserts the
  call this provider makes carries exactly
  `max_tokens, messages, model, output_config, system` and nothing else.
* **the schema is narrowed to this API's structured-output subset before it is
  sent.** Measured, not assumed: sending `response_schema()` unchanged returns
  **400** `invalid_request_error` — *"output_config.format.schema: For
  'number' type, property 'exclusiveMinimum' is not supported"*. The subset
  accepts the basic types, `enum`, `const`, `anyOf`, `allOf`, `$ref`/`$defs`,
  the named string formats and `additionalProperties: false`; it accepts no
  numeric bound, no string length or pattern constraint, and no array-size
  constraint. `schema_for_api` therefore removes
  `UNSUPPORTED_SCHEMA_KEYWORDS` — `exclusiveMinimum`, `minimum`, `maximum`,
  `multipleOf`, `minLength`, `maxLength`, `pattern`, `minItems`, `maxItems`,
  `uniqueItems` — at every depth, returning a copy so the shared schema the
  evaluation harness and the Gemini provider see stays whole.

  **This weakens no rule.** The schema is a decoding constraint, never an
  authority on CAD validity: the V1 validator remains the only thing that
  decides that, and it still rejects a zero-or-negative size, a malformed
  feature id and an empty feature list regardless of what the model was free
  to emit. The prompt states each requirement in prose as well, so a model
  that ignores one produces a document the validator refuses — the designed
  behaviour. Tests pin the removal, the untouched original, the surviving
  supported keywords, and that a stripped constraint is still enforced.

  Until this was found, **every** `POST /generate` on this provider failed
  with `invalid_model_output`/`model_error` before interpretation was even
  attempted. It went unnoticed because Anthropic had never been exercised
  live; the request never reached the model.

#### Gemini

Measured against the installed `google-genai` **2.22.0**:

* `client.models.generate_content` takes exactly `model`, `contents` and
  `config`.
* `GenerateContentConfig` carries `system_instruction`, `response_mime_type`
  and `response_json_schema` — the last takes a **raw JSON Schema**, which is
  what this application already has, so the same `response_schema()` object
  goes to both providers unchanged.
* **It also carries `temperature`, `top_p`, `top_k` and `seed`, which the
  Anthropic SDK does not.** None is set. Leaving the SDK's own defaults is
  what keeps a Gemini run comparable to an Anthropic one; a run records both
  that the controls exist (`deterministic_decoding: available`) and that none
  was used (`decoding_controls_used: none`), so "could not be set" and "could
  have been, was not" never read the same.
* `tools`, `tool_config` and `automatic_function_calling` are **never sent**,
  asserted the same way.
* Usage is mapped onto the same `input_tokens` / `output_tokens` names the
  other provider reports, so the harness aggregates one vocabulary.

**The default model `gemini-2.5-pro` could not be verified offline.** The
Anthropic SDK ships a `Model` literal that a test checks its default against;
the Gemini SDK ships no equivalent, and `models.list()` needs a credential.
It is overridable with `CAD_AI_MODEL` and is verified at run time by the API
itself — an unknown name is a provider error, not a silent fallback.

## The AI boundary, exactly

`apps/api/src/cad_ai/` — beside the HTTP transport, **outside `cad_core`**,
because it needs a vendor SDK and `cad_core` has none.

| Module | Responsibility |
|---|---|
| `provider.py` | `TextToCadModel`, `ModelRequest`, `ModelResponse`, `ProviderError`. No vendor type crosses this line |
| `anthropic_provider.py` | the default provider. **The only module that imports `anthropic`**, lazily |
| `gemini_provider.py` | the second provider. **The only module that imports `google.genai`**, lazily |
| `specification.py` | the model-facing view of the contract, derived from `docs/cad-specification.md` and `cad_core.model`'s constants |
| `prompt.py` | the one system prompt and its version |
| `generation.py` | `TextToCadService.generate_cad_from_text`, `AiGenerationResult`, `GenerationOutcome` |
| `config.py` | model, timeout, and a boolean "is a credential present" — **no credential field exists** |

Asserted by tests, in both directions:

* **no `cad_core` module imports an LLM SDK, or `cad_ai`, or names one.** The
  check is stronger than an import scan: the AST's identifiers, attribute
  names and non-docstring string literals are searched, so no lazy import,
  `getattr` or string can smuggle one in. `cad_core` is also run in a **child
  process with every LLM SDK blocked on import**, where it still validates
  and hashes a document.
* **`cad_ai` imports only `cad_core.application_service` and
  `cad_core.model`** — the service boundary and the contract's constants.
  Nothing else, and in particular no geometry module, exporter, cache, build
  layer or isolation layer.
* **`cad_ai` reimplements no validation.** No rule-code literal appears in it
  and it defines no `validate`; the only route to validity is
  `self._service.validate_document(...)`.
* **`cad_api` (the HTTP transport) does not import `cad_ai` or the SDK.**
* **importing `cad_ai` does not import the SDK** — proved in a child process.

## The prompt

One place: `cad_ai/prompt.py`. No prompt string exists in a route, a test or
the provider, and a test asserts that a 400-character span of the
instructions appears in no other file.

It is **assembled, not hand-maintained**:

* the instructions — what the model's job is and what it must refuse — are
  this layer's own content;
* every list in them (`box, cylinder`; `through_hole, subtract, fillet,
  chamfer`; the six axis values; the defaulted parameters; the schema
  version; the units) is interpolated from `cad_core.model`'s constants;
* the contract itself is appended **verbatim** from
  `docs/cad-specification.md` — sections A, B, C.1, C.2, E.1 and F, read from
  the file at import time. A test asserts each quoted section is a literal
  substring of the real document, so the prompt cannot drift from it, and a
  renamed heading raises `KeyError` rather than silently shrinking what the
  model is told.

Sections C.3–C.7 (`through_hole`, `subtract`, `fillet`, `chamfer`, edge
selectors) are deliberately **excluded**: they are valid V1 but outside this
stage's subset, and showing a model a feature it must not use invites it to
use one. It is told instead that they exist and are unsupported.

Assembled size: **14 943 characters** (~3.7k tokens), of which 10 459 are the
specification excerpt.

The prompt states, and a test asserts each one:

1. it translates natural language into the V1 CAD specification;
2. it outputs only the allowed document structure;
3. it never outputs Python, CadQuery, OpenCascade, OpenSCAD, FreeCAD,
   FeatureScript, a script, a shell command, STL, STEP, IGES, mesh data or
   code in any language;
4. it invents no unsupported feature;
5. feature order follows the request's order;
6. it uses a default **only** where the specification defines one;
7. it asks when required geometry information is genuinely missing.

It also tells the model it has no tools, that nothing it writes will be
executed, that a box's `position` is its **minimum corner** and a cylinder's
is the **centre of its base circle** ("do not treat either as a centroid"),
and that where the instructions and the specification appear to disagree,
**the specification wins**.

### Prompt regression

`PROMPT_VERSION = "2026-09-08.1"` and

```
prompt_fingerprint() = 2b3e3395ec6efee0fe252cf88207e981dcdecfdb88ea847f075ce20a5ad9ba52
```

are both pinned by a test. The fingerprint covers the instructions **and** the
specification sections the prompt quotes, so either changing makes the test
fail. When it does, the honest response is to bump the version and re-measure
model behaviour — not to assume old assumptions still hold. There is no prompt
version *system*: no registry, no migration, no stored history.

### Prompt 2026-09-09.2 — two gaps, measured on Claude Haiku 4.5

Both changes came from reproducing a real failing request five times against
the live provider, not from reading the prompt and guessing.

**No joining.** *"Create a simple desk stand using basic geometric primitives.
Use millimeters and make it a single solid. Choose reasonable dimensions
yourself."* produced `INVALID_MODEL_OUTPUT` **5/5**, in three shapes that look
like three bugs and are one:

| Rule | What the model did |
|---|---|
| **S9** ×2 | left three solids — `base`, `post`, `top-platform` — never joined |
| **S14** ×2 | emitted `"tools": []`, a `subtract` used as a pseudo-merge |
| **S6** ×1 | referenced a `fillet`'s id as though it named a solid |

The cause is that V1 has no union, fuse or join, and the instructions never
said so. The model assumed a join existed, could not find one, and improvised.
A specification describes what exists, so the appended excerpt could not state
the absence; the instructions now do, with the consequence: a part only
meaningful as two or more primitives joined into one body is `unsupported`.
Measured after: **`UNSUPPORTED` 5/5**, the model naming the missing operation
in its own words.

Note what was *not* wrong. "Choose reasonable dimensions yourself" was never
the problem — the model chose dimensions happily. The failure was topology,
not ambiguity.

**Locatives are answers, not gaps.** *"…with a 10 mm through hole on the top."*
returned a valid, buildable document **3/5** and a clarification request
**2/5** for byte-identical input. `through_hole.position` is required and has
no default, and nothing said whether "on the top" supplies it, so two existing
rules pulled opposite ways: *prefer asking over guessing, always* against
*never ask a question the request already answers*. The resolution is now
written down ("on the top" ⇒ centred on that face, with the arithmetic given),
so identical wording gets an identical answer. Measured after: **`GENERATED`
5/5**, every document valid, building to one solid of 285 643.805 mm³ at
120 × 80 × 30 mm.

**What was deliberately not added.** Restatements of S8, S11 or S14. The
rendered prompt was checked, and the derived specification excerpt already
states each in prose — `tools` MUST be a non-empty array (S14), the id pattern
(S8), positive sizes (S11). Repeating them would duplicate the contract and
invite drift. That also settles a question about
`anthropic_provider.schema_for_api`, which strips `minItems`, `minLength`,
`pattern` and `exclusiveMinimum` because the API rejects them: **the model
loses no information**, because the excerpt carries all four rules.

No validator rule was relaxed, no CAD feature added, and no repair loop
introduced. The correct answer to an unrepresentable request is still a
refusal.

## The output contract

The model answers with one JSON object, constrained by a derived schema:

```json
{
  "status": "document" | "needs_clarification" | "unsupported",
  "document": { /* a V1 CAD document */ } | null,
  "summary": "one sentence on how the request was read",
  "questions": ["..."],
  "issues": ["..."]
}
```

**The document is one field and the prose is others.** `summary` is carried
through to the caller and never read for meaning by anything in this system;
it is bounded to 500 characters, a non-string `summary` is dropped rather than
coerced, and a test asserts the summary is not part of the document and does
not change its hash.

The JSON Schema is **derived**, not written: every field name, enum and
default comes from `cad_core.model` — `SCHEMA_VERSION`, `SUPPORTED_UNITS`,
`AXIS_VALUES`, `DEFAULT_AXIS`, `ID_PATTERN`, `REQUIRED_ROOT_FIELDS`,
`FEATURE_PARAMETERS`. Tests assert the per-feature `required` lists equal the
contract's own tables, that the axis enum is `cad_core`'s, that every property
name appears in the specification document, and that no unsupported feature
type or parameter (`radius`, `distance`, `edges`, `tools`, `target`, `select`)
is offered at all.

**The schema is a generation aid, not a validator.** A document can satisfy it
and still be invalid — rule S9 ("exactly one solid at the end") is not
expressible in JSON Schema, and a test demonstrates a schema-satisfying
two-box document that the validator rejects. That is exactly why the validator
remains the authority.

### `AiGenerationResult`

```python
AiGenerationResult(
    outcome,             # GenerationOutcome
    message,             # the stable public sentence
    candidate_document,  # the validated document, or None
    document_hash,       # cad_core's canonical hash, or None
    summary,             # the model's prose. Explanatory only.
    questions, issues, rule_codes,
    metadata,            # provider, model, prompt_version, usage, ...
    detail,              # development only; NOT in to_dict()
)
```

`candidate_document` is the **canonical serialization of the validated part** —
so what a caller receives is a document the existing validator already
accepted, in the form the rest of the system uses. A test asserts it equals
`part_hash(deserialize_part(...))`'s input and differs from the model's raw
bytes.

## Result states

| Outcome | Meaning |
|---|---|
| `GENERATED` | a candidate was produced **and passed the existing validator**. The only outcome carrying a document |
| `NEEDS_CLARIFICATION` | required geometry information is missing. Carries questions |
| `UNSUPPORTED` | the request cannot be expressed in the supported subset |
| `MODEL_ERROR` | the provider returned no usable response. Nothing was interpreted |
| `INVALID_MODEL_OUTPUT` | the model answered, but the answer is not a valid V1 CAD document |

**"The model answered" is never conflated with "the document is valid."** The
model's own vocabulary is three statuses; the five outcomes are this layer's,
and `MODEL_ERROR` and `INVALID_MODEL_OUTPUT` are unreachable by anything the
model says about itself — a test asserts they are not in the status map. A
status the model invents (`"ok"`, `"success"`, `"generated"`, `"DOCUMENT"`, a
number, `null`) is not honoured: it becomes `INVALID_MODEL_OUTPUT`.

`UNSUPPORTED` never carries a document **even if the model sends one**.

## The supported natural-language subset

One part, made of a single `box` **or** a single `cylinder`:

* dimensions;
* `position`, where the request describes it;
* `axis`, for a cylinder, where the request describes it;
* units, stated in the request.

Worked, and tested end to end:

| Request | Document |
|---|---|
| "Create a rectangular plate 100 mm long, 60 mm wide, and 10 mm thick." | one `box`, `size {100, 60, 10}`, no `position` |
| "Create a cylinder with diameter 20 mm and height 50 mm." | one `cylinder`, `diameter 20`, `height 50` |
| "Create a 100 x 60 x 10 mm box with its minimum corner at 10, 20, 30 mm." | one `box` with `position {10, 20, 30}` |
| "Create a 20 mm diameter cylinder, 50 mm tall, starting at 10, 20, 30 mm and extending in +Z." | one `cylinder` with `position` and `axis "+Z"` |

Deliberately **not** promised: arbitrary mechanical language, multi-feature
interpretation, holes, cuts, fillets, chamfers, assemblies, sketches, lofts,
sweeps, revolves, threads, patterns, tolerances, GD&T, materials, surface
finish, manufacturing process, cost, simulation, engineering drawings, images,
or a dimension given as a formula or a range. Each of those is `UNSUPPORTED`,
and the prompt says so explicitly rather than leaving it to the model's
judgement.

## Ambiguity: ask, do not guess

The rule is derived from the contract, not invented:

> **A missing value the specification gives a default may be omitted. A
> missing value with no default must be asked about.**

For the supported types the defaulted parameters are `position` and `axis`
(read out of `FEATURE_PARAMETERS`), so:

* "a 100 × 60 × 10 mm plate" → a valid document with **no `position`**. That
  is the contract's own default (Section C.1), not an assumption. The
  existing deserializer materialises `{0, 0, 0}`; the AI layer supplies
  nothing, and a test asserts exactly that.
* "a 20 mm × 50 mm cylinder" → a valid document with **no `axis`**; the
  deserializer applies `+Z` (Section C.2).
* **"plate 100 by 60 by 10" → `NEEDS_CLARIFICATION`, asking about units.**
  `units` is required and Section A.3 says the unit system "is never implied
  by context", so an unstated unit is genuinely unknown. No semantic
  equivalence with millimetres is assumed anywhere.

A `NEEDS_CLARIFICATION` answer with no question is treated as unusable output
rather than returned as an empty ask. Empty input asks a question **without
calling the provider at all** (tested: the fixture records zero requests).

## Validation flow

Every candidate goes through the existing boundary, and only that one:

```python
validation = self._service.validate_document(dict(candidate))
```

* no rule is checked in `cad_ai`, no document is repaired, and S1–S20 are
  never bypassed;
* the reported `issues` are the **validator's own messages** and `rule_codes`
  its own codes;
* measured refusals: bad dimensions → **S10** / **S11**; a bad axis (`"Z"`,
  `"+z"`, `"up"`, `"+W"`, `"0,0,1"`) → **S12**; a forward reference → **S7**;
  a reference to nothing → **S6**; a non-`mm` unit → **S5**; an unknown field
  (`"material": "6061-T6"`) → **S3**; two solids → **S9**.

**No CAD kernel runs during generation.** With `local_cad.build_part`,
`render_model.build_render_model`, `export_step`, `export_stl`, `export_iges`
and `subprocess.Popen` each patched to raise, generation still completes — for
a valid *and* an invalid candidate. Generation also writes nothing to the
cache (asserted by a file-level snapshot).

## No automatic repair

**One generation attempt, then validate, then return.** A rejected candidate
is not sent back to the model; a test asserts the provider was called exactly
once. There is no repair loop, no retry with the validator's complaints and
no second turn. That belongs to a later stage, and leaving it out is what
makes this stage's failure behaviour measurable.

## Prompt injection, code output, and tools

**The model gets no tools.** Not shell, Python, filesystem, HTTP, the CAD
kernel, Onshape, a browser, or arbitrary function calling. `ModelRequest` has
nowhere to put one — its fields are exactly `system`, `user_text`,
`output_schema`, `max_output_tokens` — and the provider never sends `tools`.

Twelve adversarial payloads are exercised (Python as the whole answer, a
CadQuery script, FeatureScript, `__import__('os').system(...)` in the document
field, `exec(...)` as a document string, a script inside a feature, a
`featurescript` feature type, a `{"tool": "bash", "command": "curl … | sh"}`
document, an imitation `tool_use` block, "IGNORE PREVIOUS INSTRUCTIONS" in the
summary, an eval expression as a dimension, and a `__class__`/`__args__`
document). For every one of them:

* it never becomes a CAD document — the outcome is `INVALID_MODEL_OUTPUT`, or
  (for the two whose CAD document is genuinely valid and whose code sat in a
  field outside it) `GENERATED` with the code **absent** from the result;
* `eval`, `exec` and `compile` are patched to raise for the whole generation,
  and `__import__` is watched: neither `os` nor `subprocess` is imported;
* `subprocess.Popen`, `subprocess.run` and `os.system` are patched to raise;
* `socket.socket` and `socket.create_connection` are patched to raise;
* the temporary tree and the system temp directory are snapshotted
  before and after: **nothing is written**.

Injection in the *user's* text is handled the same way: whatever the request
says, the layer still demands a CAD document, and prose comes back as unusable
output. The user's text is **passed to the model unmodified** — the layer
neither sanitises nor rewrites it.

Source-level checks: no module in `cad_ai` calls `eval`, `exec`, `compile`,
`system`, `popen`, `Popen`, `run`, `spawn`, `fork` or `execv`; none imports
`subprocess`, `pickle`, `marshal`, `shelve`, `socket`, `http`, `urllib`,
`requests`, `httpx`, `ctypes`, `importlib`, `runpy`, `shutil` or `tempfile`;
and none writes a file (`specification.py`'s `read_text` of the specification
document is the only filesystem access anywhere in the layer).

## Input normalization

There is none, on purpose. The description reaches the model as written —
tested for `mm`, inch, centimetre, unitless, unicode `×`, em dashes and
untrimmed whitespace. No unit conversion exists in this layer: a test asserts
`25.4`, `304.8`, `to_mm`, `convert_units` and friends appear nowhere. An
inch-denominated document from the model is **refused (S5), not converted** —
nothing silently becomes 101.6 mm.

## Determinism

**Natural-language generation is not deterministic, and nothing here claims
it is.**

```
same prompt + same provider configuration  ≠  guaranteed identical model output
```

This is not a hedge but a measured limitation: the installed SDK's
`messages.create` accepts **no** `temperature`, `top_p`, `top_k` or `seed`
parameter, so deterministic decoding cannot even be requested at this
boundary. A test pins that observation, so if a future SDK adds one the test
fails and the claim gets re-examined. No live generation was repeated in this
environment (no credential), so no empirical statement about output stability
is made either way.

Below the document, everything stays deterministic, and the separation is
tested:

* the same candidate always yields the same `document_hash` and the same build
  key (four repetitions, one distinct value each);
* a generated document builds to the same build key every time — the first
  call a miss, the second a cache hit, with the same measured volume;
* two different model answers to the same request text give two different
  documents, which is the honest characterisation of the stochastic half.

## Observability

Recorded per generation: `provider`, `model`, `prompt_version`,
`structured_output`, `stop_reason`, token `usage`, and
`request_characters` — plus the outcome, which is the generation *and*
validation verdict in one field.

**Not** recorded: the user's description (only its length, which correlates a
result with a request without storing it), the system prompt, any credential,
any filesystem path. Tests assert a description containing `WIDGET-9` and an
email address appears nowhere in the result, and that the whole payload stays
under 4 kB. No telemetry platform, no metrics, no tracing; the layer imports
no logging module and calls no `print` or logger.

## Error handling

Public messages are five fixed sentences (`PUBLIC_MESSAGES`). Diagnostics live
in `AiGenerationResult.detail`, which is **excluded from `to_dict()`**.

Measured: a provider failure whose detail is
`"AuthenticationError: 401 from https://api.example/v1/messages x-api-key:
sk-secret-value request-id=req_123"` produces a public payload containing none
of `sk-secret`, `x-api-key`, `api.example`, `401`, `req_123` or
`AuthenticationError` — while `detail` keeps them for development. No API key,
provider header, raw provider diagnostic, filesystem path or Python traceback
reaches a caller. The provider translates its own exception types, so no vendor
exception crosses the boundary.

## HTTP: deliberately deferred

**No endpoint was added in this stage.** The decision was made on boundaries,
not convenience:

1. every existing route is a thin adapter over
   `cad_core.api_contract`, the transport-neutral contract. The AI layer
   *cannot* go there — that would put a vendor SDK in `cad_core`. So an AI
   route would be the **first** route bypassing the transport-neutral
   contract, which is an architectural asymmetry deserving its own
   deliberation rather than a tacked-on route.
2. `create_app` takes exactly one of `config` or `service` and needs only
   `CAD_API_CACHE_ROOT`. Adding AI would mean either a second required
   variable (breaking every existing startup path) or a conditional route —
   a shape the application has never had.
3. **no credential exists in this environment**, so a `POST /generate` could
   not be measured end to end. Fixing a transport's status codes and error
   bodies before a single real generation has been observed is the wrong
   order.

`TextToCadService.generate_cad_from_text` is fully exercisable without HTTP,
and its result already serializes to plain JSON, so the next stage can add a
thin route without reshaping anything. `docs/http-api.md` is therefore
unchanged, and a test asserts `cad_api` imports neither `cad_ai` nor the SDK.

## Testing strategy

Three tiers, as the stage requires:

**A. provider tests** — the real `AnthropicTextToCadModel` against a fake SDK
client. No network, no credential. They assert the call shape, that
`output_config` uses the SDK's real parameter, that no `tools` and no
`temperature` are sent, that only text blocks become the answer, that SDK
errors translate to `ProviderError` with a safe message, that construction
without a credential is refused *before* a client is built, and that the
credential is handed to the SDK and retained nowhere.

**B. interpretation tests** — fixture model responses, deterministic. Every
payload is one **this test file wrote**, labelled as such. No live provider
result is fabricated anywhere.

**C. one live integration test** — `TestLiveProvider`. It runs only when
`ANTHROPIC_API_KEY` is already present; otherwise it **skips**, reporting
`"ANTHROPIC_API_KEY is not set: no live provider test was run"`. No credential
is written by the suite, and none is hard-coded.

In this environment **no credential was available, so the live test reported
itself skipped.** No claim is made here about real model behaviour on the four
example prompts: the documents in this file are fixtures, and the pipeline
below the model is what was measured.

## Real build results for a generated box

A generated document — from the fixture answer to "Create a rectangular plate
100 mm long, 60 mm wide, and 10 mm thick." — built through the existing
application service, in the existing isolated process:

```
document_hash  44bbce7ddf4aa0836ae7070cb04ed4882d7707b1e2de1f5a91e3b7162b5b3270
build_key      75723cbab28fa7a348f5e767bf01b393d1319de4a8f7e82504d06a2e675659d3
geometry       1 solid, is_solid=True, 6 faces, 12 edges, 8 vertices
               volume 60000.0 mm³ (nominal 100x60x10, within 1e-9 relative)
               bounding box size {x: 100.0, y: 60.0, z: 10.0}
render model   format 1.0.0, mm, right_handed_z_up, 24 vertices, 12 triangles
STEP           ISO-10303-21 … END-ISO-10303-21, checksum verified
STL            12 triangles, length == 84 + 50 x triangle_count
IGES           produced
```

A generated cylinder builds too: 1 solid, volume π·10²·50 within 1e-9
relative.

## Security limitations

**This is not production-ready, and nothing here should be read as claiming
otherwise.** Beyond the limits `docs/http-api.md` already records:

* **the model's output is untrusted input.** It is treated as such — parsed,
  never executed — but the guarantee rests on this layer never gaining an
  interpreter, a tool or a code path that runs model text. The tests are
  written to make such a regression fail.
* **prompt injection is not prevented, only contained.** A sufficiently
  persuasive description may make the model emit a document the user did not
  intend. The containment is that any document must still pass S1–S20 and
  that nothing is executed — not that the model cannot be steered.
* **no cost, rate or abuse control.** Every call is a real paid API call with
  no per-caller quota, no budget cap and no request throttle. There is no
  authentication above this layer, so an exposed deployment would let anyone
  spend the credential.
* **the description is sent to a third-party API.** That is inherent to the
  design and should be disclosed to users; this stage adds no redaction, no
  data-residency control and no opt-out.
* **the credential is read from the process environment** and is only as
  protected as that environment. It is never written to disk, logged or put
  in a result by this layer.
* **no output-size or content policy** beyond `max_tokens` and the schema.

## Limitations and hallucination risks

* **Model quality is unmeasured here.** With no credential, the four example
  prompts were exercised against fixtures. Whether a real model produces those
  documents — and in particular whether it reliably asks about units instead
  of assuming millimetres — is **not established by this stage**. The prompt
  is written to make asking the easy path; that is a design intent, not a
  measurement.
* **A plausible wrong document is the dangerous failure mode.** A model that
  reads "100 × 60 × 10" as `{x:60, y:100, z:10}`, or a cylinder's `position`
  as its centroid rather than its base-circle centre, produces a *valid*
  document that is the wrong part. S1–S20 cannot catch that, and this stage
  adds no semantic cross-check. The prompt states both anchor points
  explicitly for exactly this reason.
* **The subset is very small.** One box or one cylinder. The Section D plate
  the rest of the system uses as its worked example is `UNSUPPORTED` here,
  because it needs `through_hole`.
* **One shot, no repair.** A model that returns a nearly-valid document gets
  no second chance, so the `INVALID_MODEL_OUTPUT` rate will be higher than it
  needs to be. Deliberate: measurable now, improvable later.
* **No conversational editing, no image or drawing input, no MCP, no
  Onshape, no RAG, no agent framework, no tool calling** — none of it is
  implemented, and none is implied.
