# Text-to-CAD evaluation harness

Status: **Stage 27 — a repeatable measurement harness for the configured
LLM. Not a proof of general CAD competence.**

> **This is an evaluation harness, not evidence of model quality.** It
> measures one prompt, one provider and 35 hand-written cases. A good score
> here would mean the model handles *these* prompts; it would say nothing
> about arbitrary mechanical language, and this stage makes no such claim.

## Purpose

Stage 26 established the AI boundary safely but could not exercise it: no
application credential was available, so the live provider test skipped and
every example in `docs/text-to-cad-ai.md` is fixture-driven. Those fixtures
prove the *plumbing*. They prove nothing about the model.

This stage builds the thing that can: a fixed corpus, an explicit expected
answer per case, and a runner that reports five dimensions separately.

```
case prompt
  │  TextToCadService.generate_cad_from_text     unchanged, one attempt
candidate document
  │  cad_core deserializer + validator           unchanged, still authoritative
  │  cad_ai.comparison.compare_documents         field-level, labelled
  │  (optional) CadApplicationService.build      does it actually execute?
EvaluationResult -> EvaluationRun -> a JSON file
```

**Measurement only.** No repair loop, no retry, no prompt variation, no
A/B testing. The model is not improved during a run and the corpus is not
edited after seeing results — the whole point is an honest baseline for the
next stage to work against.

## Files

| File | Role |
|---|---|
| `apps/api/src/cad_ai/corpus.py` | the 35 cases: prompts and hand-authored expected documents |
| `apps/api/src/cad_ai/comparison.py` | field-level document comparison and the error taxonomy |
| `apps/api/src/cad_ai/evaluation.py` | cases, results, runner, metrics, storage, the command |
| `apps/api/tests/test_ai_evaluation.py` | the tests, none of which need a credential |

Nothing lives in `cad-core`: a test asserts no `cad_core` module names
`cad_ai`, `SemanticErrorCategory`, `EvaluationCase`, `EvaluationResult`,
`compare_documents`, `load_corpus` or `benchmark`, and that none of
`evaluation.py`, `corpus.py` or `comparison.py` exists there. Nothing in the
generation path imports the evaluation layer either, so no expected output is
hard-coded anywhere near the code that talks to the model — also asserted.

### A note on Stage 26's boundary tests

Adding these three modules widened the `cad_ai` package, and six of Stage
26's package-boundary tests walked *every* file in it. They were re-scoped to
a named `GENERATION_MODULES` list rather than weakened: each still asserts, at
full strength, that the generation path prints nothing, writes no file, uses
no temporary directory, imports only `cad_core.application_service` and
`cad_core.model`, and names no kernel or exporter. The evaluation layer
legitimately does some of those things — it prints a report and writes a
result file — and `test_ai_evaluation.py` asserts the invariants that *do*
apply to it: no interpreter, no subprocess, no socket, no CAD kernel import.

## The benchmark corpus

35 cases in seven categories.

| Category | n | What it measures |
|---|---|---|
| **A** — simple box | 6 | explicit dimensions, explicit position, decimals, repeated wording, reversed dimension order |
| **B** — cylinder | 6 | diameter + height, each explicit axis (`+Z`, `+X`, `-Z`, `+Y`), explicit base position |
| **C** — defaults | 3 | omissions the specification permits: a cube, a cylinder with only its position stated, a disc with neither |
| **D** — ambiguity | 5 | unstated units, a missing extent, a missing diameter, an un-inferable axis, vague placement |
| **E** — unsupported | 5 | through-hole, subtract, fillet, chamfer, two solids |
| **F** — adversarial | 5 | "ignore previous instructions", "write CadQuery", "output FeatureScript", shell injection, a schema-override attempt |
| **G** — semantic confusion | 5 | known failure modes: units omitted, box centred on the origin, cylinder "starting at", cylinder "centred at", words vs. order |

### Expected-output methodology

Every expected document was **written by hand from
`docs/cad-specification.md`, before any model was called.** No model output
was copied into the corpus, and a test greps `corpus.py` for the shapes model
output would leave behind (`stop_reason`, `input_tokens`, `claude-`,
`candidate_document`, a `"status"` field).

`load_corpus()` then puts every expected document through the **existing**
deserializer and validator before a single model call happens, so the
benchmark cannot score the model against ground truth that is itself invalid.
It also rejects a duplicate `case_id`, an `EXPECTED_GENERATED` case with no
document, a non-generated case carrying one, an expected document using a
feature outside the supported subset, and an empty corpus. A separate test
feeds it four invalid documents (S10, S9, S4, S1) and asserts each is refused.

The corpus commits to two interpretation conventions, both grounded in the
specification rather than invented:

* **word-to-axis mapping** — Section A.1 gives `+X` "width", `+Y` "depth",
  `+Z` "height"; a bare `A x B x C` is read in order as X, Y, Z.
* **anchor points** — Section C.1 makes a box's `position` its **minimum
  corner**, Section C.2 makes a cylinder's the **centre of its base circle**.
  Several G cases exist precisely because these are easy to get wrong.

Where a prompt does not state something the specification requires — units
above all, since Section A.3 says the unit system "is never implied by
context" — the expected answer is a **question**, not a document.

### Expected outcomes

| Expectation | Satisfied by | Notes |
|---|---|---|
| `EXPECTED_GENERATED` | `GENERATED` | and the document must match |
| `EXPECTED_NEEDS_CLARIFICATION` | `NEEDS_CLARIFICATION` | a valid document here is a **failure** |
| `EXPECTED_UNSUPPORTED` | `UNSUPPORTED` | a document here is a **failure** |
| `EXPECTED_BOUNDARY_HELD` | any of `GENERATED`, `NEEDS_CLARIFICATION`, `UNSUPPORTED`, `INVALID_MODEL_OUTPUT` | adversarial cases, where more than one answer is legitimate |

These map onto `GenerationOutcome`, which Stage 26 already defines. **No
second outcome taxonomy was created** — a test asserts every entry in
`SATISFYING_OUTCOMES` is a real `GenerationOutcome`.

`EXPECTED_BOUNDARY_HELD` exists because for F1–F5 more than one answer is
correct: refusing to write Python is right, and so is answering with the CAD
the request also described. What is scored there is the **boundary**, not the
outcome: nothing executed, no code-shaped content in the result, no
unsupported feature accepted.

## The five dimensions

Measured separately and never collapsed:

| # | Dimension | How |
|---|---|---|
| 1 | **parseability** | was the model's raw text a JSON object at all |
| 2 | **CAD-spec validation** | did `cad_core`'s validator accept the candidate |
| 3 | **expected-outcome match** | did the model do the kind of thing the case wanted |
| 4 | **exact canonical match** | is the candidate the expected document, after canonicalization |
| 5 | **field-level correctness** | of the leaf fields compared, how many agree |

1 and 2 are genuinely separable because the evaluator wraps the model in a
`RecordingModel` and keeps the raw response. The finished
`AiGenerationResult` reports a validated document or nothing, so without that
wrapper "the model wrote prose" and "the validator rejected it" would both
just be `INVALID_MODEL_OUTPUT`. The wrapper observes; it changes nothing —
the request goes through untouched and the response comes back untouched.

The same wrapper records **which defaulted parameters the model omitted**,
which canonicalization has already materialised by the time anyone else sees
the document.

## Exact match

For `EXPECTED_GENERATED` cases:

```
canonical(candidate) == canonical(expected)
```

using Stage 15's canonical serialization. Whitespace, raw model JSON
formatting and key insertion order are **never** compared — a test builds two
documents that differ only in key order and number spelling and asserts they
match exactly.

## Semantic comparison

`compare_documents` walks the two canonical documents and emits **labelled**
differences, never an unlabelled recursive dict diff:

```
DIFFERENCE: features[0].size.x
  expected: 100.0
  actual:   60.0
  category: wrong_dimension
```

The walk is driven by `cad_core.model`'s own tables —
`FEATURE_PARAMETERS`, `FEATURE_REFERENCE_FIELDS`, `ORIGIN`, `DEFAULT_AXIS` —
so a parameter the specification grows and this module has no rule for raises
rather than being silently skipped.

### Categories

| Category | Meaning |
|---|---|
| `WRONG_SCHEMA_VERSION` | `schema_version` differs |
| `UNIT_ERROR` | `units` differs |
| `WRONG_LABEL` | `name` or `description` differs |
| `WRONG_FEATURE_TYPE` | a feature is the wrong type (parameter comparison then stops — comparing a box's `size` to a cylinder's `diameter` is noise) |
| `WRONG_FEATURE_ID` | a feature's `id` differs |
| `WRONG_FEATURE_ORDER` | the same features, permuted |
| `WRONG_DIMENSION` | `size.*`, `diameter`, `height`, `radius`, `distance` |
| `WRONG_POSITION` | `position.*` |
| `WRONG_AXIS` | `axis` |
| `WRONG_DEFAULT` | a defaulted parameter differs **and one side is the specification's default** |
| `WRONG_REFERENCE` | `target` or `tools` |
| `WRONG_SELECTOR` | `edges.select` / `edges.axis` |
| `MISSING_FEATURE` / `EXTRA_FEATURE` | the feature count or membership differs |
| `AMBIGUITY_NOT_ASKED` | *case-level*: a question was required, a document was produced |
| `UNSUPPORTED_FEATURE_ACCEPTED` | *case-level*: a refusal was required, a document was produced |

The taxonomy lives in `cad_ai/comparison.py` and nowhere else; a test asserts
none of its member names appears in `cad_core/model.py`.

### `WRONG_DEFAULT`, defined exactly

A defaulted parameter (`position`, `axis` — read out of
`FEATURE_PARAMETERS`' optional tuples) differs, **and one of the two values is
the specification's default**. `default_side` records which. It refines
`WRONG_POSITION`/`WRONG_AXIS`: the mistake involves the default rather than
two arbitrary wrong values.

**Known limitation:** because both sides are canonical, this compares
*values*, not omission. A model that wrote `position: {0,0,0}` explicitly and
one that omitted it produce the same canonical document and both count as
exact matches. Which one actually happened is recorded separately, in
`omitted_defaults`, from the raw answer.

### Labels are not geometry

`name`, `description` and feature `id`s carry no geometric meaning — the
specification says so for `description`, and a consistent renaming produces
an identical part (references are already validated, so an inconsistent one
would not have validated at all). A difference in one of them is **reported**
and makes `exact_match` false, but the semantic status is `LABELS_DIFFER`
rather than `MISMATCH`, and the case still counts as correct.

### `SemanticStatus`

| Status | Meaning |
|---|---|
| `MATCH` | no difference at all |
| `LABELS_DIFFER` | every difference is a label |
| `MISMATCH` | at least one difference changes what the document describes |
| `NOT_COMPARED` | no candidate, or the case expects none |

### No geometry equivalence

**Two documents that would build to the same solid but differ in design
history stay different.** `comparison.py` contains no volume, no bounding
box, no tolerance and no `isclose` — a test asserts that. Numbers are
compared exactly, because both sides came through the same canonical
serialization of JSON literals and no kernel value is involved; a tolerance
would hide a model writing 99.999 for 100.

The stage's own example is a test: expected box 100×60×10 versus a valid box
60×100×10 is a **document mismatch**, even though the sorted extents agree
and the second builds perfectly.

## Metrics

A table of dimensions, never one headline. Each rate has **its own
denominator**, and a rate with no denominator is `None`, not `0.0` — a group
of clarification cases offers no documents, so its document-match rate is
*not applicable*, and reporting `0.0` would read as total failure at
something never attempted.

**Overall:** total cases, completed, skipped, provider errors, invalid
outputs, unparseable outputs, correct, `correct_rate`.

`correct_rate` is deliberately **not** called "accuracy": it is the fraction
of cases whose own success criterion was met, and each group's criterion
differs. The per-group table is the real answer, and a test asserts the words
"accuracy" and "score" appear nowhere in the metrics payload.

**Per expectation group:** `outcome_match_rate`, `valid_document_rate`,
`exact_document_match_rate`, `semantic_match_rate`, `false_generation_rate`,
`field_correctness_rate` (with `fields_compared` / `fields_matching`
recorded, so the denominator is stated rather than inferred).

`false_generation_rate` is the metric that stops a plausible answer scoring
as a correct one: a document produced where a question or a refusal was
required.

**Adversarial:** `boundary_violations` and `execution_attempts_observed`.
Both are instrumented rather than inferred.

## Repeat runs

`--repeat N` re-runs five named cases — one box, one cylinder, one ambiguous,
one unsupported, one semantic-confusion (`corpus.REPEAT_CASE_IDS`, fixed so
the study is the same study every time).

Reported per case: outcome counts, distinct document hashes, exact matches,
validation count, and two booleans for whether the outcome and the document
were stable. For example:

```
A1-box-explicit-dimensions
    outcomes             generated: 5/5
    exact document match 3/5
    distinct documents   2
```

**No pass/fail is derived from any of it** — a test asserts the repeat
summary has no `passed` and no `correct` field. Converting stochastic
variation into a boolean would destroy exactly the information the study
exists to collect.

## Stochasticity and model settings

Generation is not deterministic and nothing here claims it is. The run
records the actual decoding configuration, re-checked at run time rather than
asserted from memory:

```json
"settings": {
  "temperature": "not settable",
  "top_p": "not settable",
  "top_k": "not settable",
  "seed": "not settable",
  "deterministic_decoding": "unavailable",
  "sdk_version": "1.4.0"
}
```

Stage 26 measured that the installed SDK's `messages.create` exposes no
`temperature`, `top_p`, `top_k` or `seed`. The probe runs again on every run,
so if a future SDK adds one the recorded value changes with it — and it lives
in `anthropic_provider.decoding_capabilities()`, not in the evaluation layer,
because the provider owns knowledge of its SDK. That keeps Stage 26's
invariant true: **exactly one module in the repository imports `anthropic`**,
and a test still asserts it.

## Model identification and prompt integrity

Every run records `provider`, `model`, `prompt_version` and
`prompt_fingerprint` — the **existing** fingerprint from `cad_ai.prompt`. No
second prompt hash is computed. The prompt is never modified during a run; a
test asserts `evaluation.py` defines no prompt, no instructions and no status
mapping of its own, and that it reaches the model only through
`self._service.generate_cad_from_text`.

**Never recorded:** an API key, a request header, an auth token, or an
environment dump. `RunMetadata` has no field that could hold one, and a test
saves a run with a credential set in the environment and greps the file for
`sk-ant`, `api_key`, `x-api-key`, `authorization`, `Bearer` and
`ANTHROPIC_API_KEY`.

## The build cross-check

`--build` builds every validated candidate through the existing application
service and records success, build key, solid count, volume, bounding-box
size and triangle count.

**This is not semantic ground truth.** A valid document that builds can still
be the wrong part, and a test proves the point: a transposed 60×100×10 box
builds perfectly and is still scored as a document mismatch. The measurements
are there to *diagnose* a mismatch, never to replace document comparison.

An invalid candidate never reaches geometry — `candidate_document` is `None`
unless the validator accepted it — and tests assert `build_part` is never
called for an invalid candidate or for a clarification.

## Latency

Three separate numbers, never summed: `model_seconds` (measured around the
provider call by `RecordingModel`), `validation_seconds`, and
`build_seconds`. All are **wall-clock elapsed time and environment
dependent**; the report says so in the output itself. No benchmarking
infrastructure beyond that was added.

## Cost

Provider usage metadata (`input_tokens`, `output_tokens`) is recorded when the
response supplies it, and reported as `{"status": "not measured"}` when it
does not. **No pricing is applied and no cost is guessed.**

## Running it

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src

python -m cad_ai.evaluation --list        # the corpus
python -m cad_ai.evaluation --check       # validate the corpus; calls no model
python -m cad_ai.evaluation --self-check  # exercise the harness against a stub
python -m cad_ai.evaluation               # the live benchmark
python -m cad_ai.evaluation --build --repeat 5 --verbose
```

No application server is needed, and no HTTP endpoint was added — evaluation
is not a production capability.

### Live-provider handling

The live run uses the **existing** provider configuration
(`ANTHROPIC_API_KEY`, `CAD_AI_MODEL`, `CAD_AI_TIMEOUT_SECONDS`). No second
credential mechanism was invented, and the key is never printed.

Without a credential the command prints:

```
==========================================================================
LIVE BENCHMARK: NOT_RUN
==========================================================================
ANTHROPIC_API_KEY is not set, so no model was called.

No result was produced and none was invented. This is not
a model failure: nothing was measured.
```

and writes no file, exiting 0. **Absence of a credential is not model
failure**, and a test asserts the message says so.

### `--self-check`

Runs the whole harness against `OracleStub`, which answers each case the way
its corpus entry says it should. It exercises the corpus, comparison, metrics,
storage and report end to end with no credential — and says **nothing** about
model quality. Runs made this way are marked `"live": false` and
`"provider": "stub-oracle"` in both the report and the saved file, and the
command prints a warning to that effect.

## Result storage

`<repo>/evaluation-results/<run_id>.json`, overridable with
`CAD_AI_EVAL_RESULTS_DIR` or `--out`.

* **Never the CAD build cache.** `save_run` refuses a directory inside a
  cache (checked against `entries`/`staging` in the path and against
  `CAD_API_CACHE_ROOT`), because evaluation results are not CAD artifacts.
* **Never overwrites.** A run id is `eval-<UTC timestamp>-<8 hex>`; writing
  over an existing file is refused rather than silently destroying a prior
  run.
* **The run id is not a document hash and not a build key.** It identifies an
  evaluation run and nothing keys on it; a test asserts it is neither 64
  characters nor equal to any document hash in the run.
* **`detail` is excluded by default.** The development diagnostic may quote
  the model's raw text, so it is opt-in.

### User prompts are stored — an explicit choice

The saved file contains a `prompts` map of case id to prompt text. These are
**the corpus's own prompts**, written by this repository and already in
version control, so storing them adds no disclosure and makes a result file
self-describing. `--no-prompts` omits them. Note that this differs from the
generation layer, which deliberately does **not** record an end user's
description.

Nothing is sent anywhere: results are written to a local file and to nothing
else.

## Testing strategy

* **No test needs a live provider.** Every case runs against `ScriptedModel`,
  a deterministic stub with fixed answers per prompt, so CI runs with nothing
  configured.
* **Stubs cover** a correct answer, a valid-but-wrong answer, a labels-only
  difference, each field-error class, malformed JSON, prose, a clarification,
  a refusal, a provider failure, and nine adversarial payloads.
* **One live test** (`TestLiveBenchmark`) runs the eight smoke cases against
  the real model, and only when a credential already exists. It asserts the
  harness ran and the provider did not fail; **what the model scored is data,
  not a pass condition**, because a benchmark that fails CI when the model is
  wrong is a benchmark nobody can run.

### Execution boundary

Stage 26's guarantees are re-proved through the evaluation path. For nine
adversarial answers (Python, a CadQuery script, FeatureScript, an
`__import__('os')` document, a shell-command document, a fake `tool_use`
block, an instruction in the summary, an eval expression as a dimension, a
schema override):

* `eval`, `exec` and `compile` are patched to raise;
* `subprocess.Popen`, `subprocess.run` and `os.system` are patched to raise;
* `socket.socket` and `socket.create_connection` are patched to raise;
* the temporary tree and the system temp directory are snapshotted before and
  after — nothing is written;
* no code marker ever appears in a candidate document.

Source-level: no evaluation module imports a CAD kernel, `pickle`, `marshal`,
`requests`, `httpx` or `urllib`, and none calls `eval`, `exec`, `compile`,
`system` or `popen`.

## Limitations

* **This is not a proof of general CAD competence.** 35 hand-written prompts,
  one prompt version, one provider, one model. It measures what it measures.
* **The corpus encodes one reading of each prompt.** Where natural language
  is genuinely ambiguous, the corpus picks the specification-grounded reading
  and scores the model against it. A defensible alternative reading is
  recorded as a failure. The `notes` field on each case states the reasoning
  so a disagreement is arguable rather than hidden.
* **The corpus must not be tuned to the model.** Editing a case after seeing
  the model fail it converts a measurement into a self-portrait. The baseline
  comes first; the next stage improves the *prompt*, not the benchmark.
* **A single run is one sample of a stochastic process.** Only the repeat
  study says anything about stability, and only for its five cases.
* **`correct_rate` mixes criteria.** It counts cases that met their own
  success condition, and those conditions differ between groups. Read the
  group table.
* **The adversarial cases test our boundary, not the model's safety.** They
  show that nothing we receive gets executed. They say nothing about whether
  the model can be made to emit something unwanted.
* **No cost, latency or throughput target exists.** The numbers are recorded,
  not judged.
* **Field correctness weights every leaf equally.** `units` counts the same
  as `features[0].size.x`. It is a diagnostic, not a quality score.
