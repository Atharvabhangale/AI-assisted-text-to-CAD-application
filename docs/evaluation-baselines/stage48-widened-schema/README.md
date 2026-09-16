# Stage 48 — the post-Stage-47 capability evaluation

**This directory holds no result yet.** It is where a Stage 48 live run
writes, and it is created ahead of the run so that nothing has an excuse to
write anywhere else — in particular not into `../stage40-v1-vs-operation-plan/`
or `../stage43-structured-output/`, which are immutable and which the harness
refuses to write to or through.

Everything below describes the instrument. When a run exists, its numbers go
in this file beneath the instrument description, and the raw JSON beside it.

## Why Stage 48 exists

Stage 43 measured V1 JSON against the operation plan with Anthropic
structured output on, and the operation plan won on both axes — 61.5% vs
23.1% build success, 84.6% vs 61.5% semantic correctness. That result stands
and is not superseded.

What changed is the system, not the result. Stage 43 sent
`executable_schema()`: six operation types, **no `sketch`, `extrude` or
`revolve` branch**, and two edge selectors. Stage 44 established that the
profile refusals Stage 43 recorded 5/5 were therefore **forced by the
grammar rather than chosen by the model**. Stages 45–47 then added the
feature graph and `derivation()`, `pattern`, and semantic edge selection
(`straight`, `circular`, and `position` for a rim's end).

Re-running Stage 43 today would send the **old six-type grammar** with the
**current prompt**, which tells the model to use `pattern`, `straight`,
`circular` and profile chains the grammar forbids. That is Stage 43's own
failure mode repeated, and the numbers would mean nothing.

So Stage 43 stays frozen as the comparison for the earlier capability
envelope, and Stage 48 is a separate instrument.

## What changed since Stage 43

| | Stage 43 | Stage 48 |
|---|---|---|
| Plan schema | `executable_schema()` — 6 types, 6 branches | `provider_schema()` — **10 types, 8 branches** |
| Plan schema fingerprint | `54759d1e16cfe634` | `be8ba82740aecc1d` |
| Plan prompt | `2026-09-10.7` (`5ef08dd09893b689`) | `2026-09-15.5` (`21d564ddeba05a74`) |
| Corpus | 13 cases (`comparison_corpus`, frozen) | **30 cases** (`stage48_corpus`), 13 of them the same requests |
| Build path for a plan | `plan_to_document` only | `build_plan` — document path **or** graph executor |
| A semantic selector | scored `SEMANTICALLY_INCORRECT` (none existed) | built and scored on the part |
| Groups | one comparison | **two**, never added together |
| Model, attempts, tolerances, taxonomy | — | **identical**, imported not copied |

The prompt moved for a reason that is itself a Stage-44-shaped finding:
Stage 46 added `pattern` as an operation and **left `patterns` standing in
the prompt's unsupported list**, so the prompt told the model to decline
something the language has. Prompt `2026-09-15.5` removes it and adds the
same explicit carve-out the `extrude`/`revolve` entry already carries. This
was found while building Stage 48's corpus, before any run — and a Stage 38
test was *requiring* the stale word to be there, which is how the
contradiction survived Stages 46 and 47. That test now asserts the correct
state instead.

## What remains comparable, and what does not

**Comparable within a Stage 48 run:** the `legacy` group — the thirteen
Stage 40 requests, answered by **both** representations, under one prompt
pair, one model, one attempt count and one set of scoring definitions. Their
text and expectations are read out of the frozen `comparison_corpus` object
at import rather than retyped, and a test asserts character-for-character
identity, so "the same request" is a checkable claim rather than a promise.

**Not comparable with Stage 43's numbers.** Three things moved at once — the
prompt, the schema and the scoring path for semantic selectors — and any one
of them is enough to break a like-for-like delta. Legacy cases `07` and `08`
("chamfer/fillet the vertical edges") are where this bites hardest: the
current prompt steers the model towards `straight`, which Stage 43's runner
would have scored incorrect and Stage 48 builds. **A Stage 48 legacy number
is not a Stage 43 number that moved.**

## What is newly evaluated

Seventeen `capability` cases, operation-plan only — V1 has no sketch, no
pattern and no semantic selector, so asking it would re-measure the
vocabulary gap Stage 43 already measured and report it as a model score.

| Category | Cases | What it asks |
|---|---|---|
| B modifiers | `B6` | a `subtract` the request pins (Stage 40's corpus never requires one) |
| D chains | `D1`, `D2`, `D3` | box→hole→chamfer; profile→extrude→hole; profile→revolve→fillet |
| E pattern | `E1`, `E2` | a radial bolt circle; a linear row of five |
| F selectors | `F1`, `F2`, `F3`, `F4` | corners without the seam; a hole's top rim; its bottom rim; four patterned rims at once |
| G graph | `G1`, `G2`, `G3` | a modifier after a consuming operation; three modifiers on one body; two tools into one subtract |
| H unsupported | `H4` | a helical thread, which the language does not have |
| I invalid | `I1`, `I2`, `I3` | no dimensions; no fillet radius; a hole wider than the stock |

`F2` and `F3` are the pair worth understanding: a top rim and a bottom rim
chamfer to **the same volume to the last bit**, so geometry alone cannot
tell them apart. They are scored by reading the selector the model wrote,
which is why `selector_correct` is a separate metric.

## Expected classes

Five, each with a crisp definition, and every case carries exactly one.

| Class | Correct answer | Cases |
|---|---|---|
| `build` | buildable geometry matching the closed form | 19 |
| `valid_unexecutable` | a valid plan this engine refuses (`ExecutionUnsupported`) | 4 |
| `unsupported` | the word `unsupported` | 4 |
| `clarification` | the word `needs_clarification` | 2 |
| `no_part` | **either** refusal word; a plan is wrong | 1 |

The first three are Stage 40's own constants, **imported not redefined**, so
a legacy case means exactly what it meant there. `no_part` is deliberately
lenient between the two refusal words because the language gives no basis to
prefer one for a geometrically impossible request — and it is not lenient
about producing a plan, which is always wrong for such a case.

## Scoring

Every Stage 40 metric keeps its meaning, and `SCORING_RULES` states each one
in words. Four metrics are new and are marked as new in that table:
`correct_valid_unexecutable`, `correct_clarification`, `selector_correct`
and `executed_by_graph` (a count, not a rate, and not a quality signal).

One inherited metric has its **denominator** stated rather than changed:
`render_success` is taken over document-path attempts only. A graph-executed
plan has no V1 document and therefore no RenderModel *by design*, so counting
those as failures would report Stage 47's capability as a defect. The rule
text says so and `render_scored_attempts` reports the denominator.

Never conflated, and six distinct codes prove it: a provider failure, an
unparseable answer, a plan the rules reject, a document the validator
rejects, a kernel build failure, a semantic mismatch, and a correct refusal.
**A provider error stays out of every denominator** — the project's standing
rule, and Stage 40's implementation, imported.

Two Stage-48-only failure codes: `WRONG_SELECTOR` (right part, wrong kind of
edge) and `INVENTED_MISSING_VALUE` (a plan where a required value was absent
from the request). One Stage-48-only **success** code: `CORRECT_CLARIFICATION`
— asking was the right answer and the model asked. It is deliberately not
`OK`, which means "built the part that was asked for"; reporting a question
as a built part would be the same conflation this stage exists to avoid.
`STAGE48_SUCCESS_CATEGORIES` is Stage 40's tuple **plus** that one, and Stage
40's own tuple is left untouched, because no Stage 40 or Stage 43 record can
carry the new code.

## Fingerprints

Recorded in every result, and every one of them deterministic:

| | |
|---|---|
| evaluation version | `1.0.0` |
| corpus | `1.0.0` / `96517cb979b4660a` |
| scoring | `98b1118b7b97f36b` |
| plan prompt | `2026-09-15.5` / `21d564ddeba05a74` |
| V1 prompt | `2026-09-08.1` / `2b3e3395ec6efee0` |
| plan schema (`provider`) | `be8ba82740aecc1d` |
| V1 schema | `ce2083a78e84c6d5` |
| model | `claude-haiku-4-5-20251001` |

A result also embeds the preflight it passed, so a reader can see that the
baselines were intact and the groups comparable at the moment it ran.

## The offline preflight

`--check` calls no model and answers five questions for free:

1. **Schemas.** Both satisfy every measured provider limit — optional
   properties (plan **16**, V1 8, limit 24), worst single object (4 each,
   limit 14), no rejected keyword, `additionalProperties: false` everywhere,
   `anyOf` not `oneOf`, `minItems` only 0 or 1, and the plan within the
   eight-branch ceiling.
2. **Groups.** Every legacy case's text is identical to the frozen Stage 40
   case of the same id; no capability case carries a V1 expectation; the
   capability group reaches `sketch`, `extrude`, `revolve`, `pattern`,
   `straight` and `circular`.
3. **Reference geometry.** Each buildable case carries a developer-written
   **reference plan** — one correct answer to that request. The preflight
   builds all 19 with the real kernel and checks the closed form. Seven of
   them go through the **graph executor**, which is the evidence that this
   corpus reaches past Stage 43's runner.
4. **Invalid plans.** Nine deliberately broken plans, each with the layer
   and rule code that must refuse it: `P9`, `P10`, `P12`, `P27`, `P28`,
   `P29`, `P10+P31`, and two the parser refuses outright.
5. **Baselines.** All seven Stage 40 and Stage 43 files present and matching
   their recorded SHA-256.

A reference plan is **not an answer key**: it never reaches the model — not
in the prompt, not in the schema, not in any request — and exists only so the
instrument can prove offline that the expectation it will score against is
reachable at all.

`--self-check` goes further and runs the entire loop against those reference
plans, scoring 19/19. It is stamped `is_live_model_result: false` and its
provider class carries `is_local_development = True`, because **a self-check
score is a statement about this harness and says nothing about Claude.**

## Exact local commands

Windows, from the repository root, with the venv's interpreter and a
`;`-separated `PYTHONPATH` (§15 of `CLAUDE.md`):

```powershell
$repo = "C:\Users\AtharvaBhangale\Desktop\AI-assisted-text-to-CAD-application"
$env:PYTHONPATH = "$repo\packages\cad-core\src;$repo\apps\api\src"
Set-Location "$repo\apps\api"

# free: the corpus, the preflight, the whole loop against reference plans
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --list
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --check
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation --self-check

# the credential must be exported into THIS process; a key in an interactive
# shell does not reach a spawned one, and nothing in the repo reads .env
$env:CAD_ANTHROPIC_API_KEY = "<the key>"      # never commit, never echo

# 1. schema acceptance — THREE real calls, not a benchmark
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation `
    --probe-live `
    --out "$repo\docs\evaluation-baselines\stage48-widened-schema\stage48-schema-probe.json"

# 2. the evaluation — 215 real calls at 5 attempts
& "$repo\.venv\Scripts\python.exe" -u -m cad_experimental.stage48_capability_evaluation `
    --live --attempts 5 `
    --out "$repo\docs\evaluation-baselines\stage48-widened-schema\stage48-capability-run.json"
```

If the probe reports the widened schema **refused** on grammar size, the
documented fallback is `--plan-schema compact`, which still admits every
profile operation. Nothing selects it automatically, and the choice is
recorded in the result.

Call budget at `--attempts 5`: 13 legacy cases × 2 arms (26) + 17 capability
cases × 1 arm (17) = **43 calls per attempt, 215 in total**, plus 3 for the
probe.

## MEASURED: the provider refuses both plan schemas

**Stage 48 is blocked, and the cause is no longer a hypothesis.** The
five-call diagnostic probe (`--probe-diagnostic`) returned, on **every** plan
request and in the provider's own words:

> `400 invalid_request_error` — *"The compiled grammar is too large, which
> would cause performance issues. Simplify your tool schemas or reduce the
> number of strict tools."*

| Request | Result |
|---|---|
| `v1_json` / box | **ACCEPTED**, structured output on |
| `operation_plan` / **provider** schema / box | refused, `BadRequestError` |
| `operation_plan` / **provider** schema / profile | refused, `BadRequestError` |
| `operation_plan` / **compact** schema / box | refused, `BadRequestError` |
| `operation_plan` / **compact** schema / profile | refused, `BadRequestError` |

The V1 control was accepted in the same run on the same model and credential,
which rules out model availability, authentication, transport and outage. Two
different descriptions failed identically on each schema, which rules out the
request text. **The documented `--plan-schema compact` fallback does not
work either** — that is the new fact, and it is why this is a schema problem
rather than a flag problem.

### Serialized size is only a proxy — measure the inlined form

The limit is on the **compiled** grammar. Stage 41 measured that a `$ref`
does not shrink it and that unused `$defs` still cost budget, so the honest
proxy is the schema with every `$ref` **inlined**: a definition referenced
five times is compiled five times. `compact` is 19% smaller than `provider`
in bytes and was refused just the same.

Measured bounds, inlined: **accepted at 3622**, **refused at 6190**.

### Where the size actually goes

Marginal cost of each capability, measured against the six-type base:

| capability added | inlined | nodes |
|---|---:|---:|
| `sketch` | **+2653** | +73 |
| `pattern` | +1014 | +24 |
| `extrude` | +459 | +9 |
| `revolve` | +455 | +9 |
| semantic edge selectors | +150 | +2 |
| *(of which sketch's `constraints` alone)* | *+1161* | *+33* |

**One capability dominates.** `sketch` is 73% of the growth, and its
`constraints` are 44% of `sketch`. Stages 46–47's additions are nearly free
by comparison. Critically, **the six types plus `sketch` alone already
measures 6275 inlined — above the refused point** — so no rung carrying a
full-fidelity sketch can be expected to compile.

### RESOLVED (Stage 50/51): profile encodings that compile

The blocker is **total compiled grammar size, not sketch**. Measured live:

| encoding | inlined | capabilities | result |
|---|---:|---|---|
| `profile` | 3487 | box, cylinder, sketch, extrude, revolve | **ACCEPTED** |
| `executable` | 3622 | the V1 six | **ACCEPTED** |
| `profile_hole` | 4030 | + through_hole, − revolve | **ACCEPTED** |
| `profile_union` | 4481 | seven types, sketch + both consumers | **ACCEPTED** |
| six + sketch | 4551 | — | REFUSED |
| nine types, **no sketch** | 4698 | — | REFUSED |

**Ceiling: (4481, 4551]** — a bound, not a number.

A sketch-free grammar at 4698 was refused while a sketch-carrying one at 4481
was accepted, so sketch is not a special case; the six-type solid base was
simply consuming the budget. `profile_union` is the largest grammar known to
compile and cannot take `fillet`, `chamfer` or `pattern` (adding the edge
pair measures 4741).

**Stage 48 must therefore run as two instruments, not one score.**
`profile_union` covers 10 plan-expecting cases and `executable` covers 8;
four cases (`D3`, `E1`, `E2`, `F4`) fit no proven encoding and must be
reported as *not expressible*, never as model refusals. Eight cases expect no
plan and are encoding-independent. See
`docs/experimental-operation-plan.md` → *Stage 51*.

### The schema ladder

`cad_experimental.schema_ladder` builds variants between the two measured
points so the ceiling can be located one call at a time. It changes nothing:
every variant comes from the canonical plan's own `_plan_document` through
its existing knobs, and the parser and validator remain authoritative over
all of them. See `docs/experimental-operation-plan.md` → *Stage 49*.

## Limitations

- **No Stage 48 number exists yet.** Everything here is the instrument.
- **Provider acceptance of the widened schema is no longer unverified — it
  is refused.** See above. The blocker is compiled-grammar size, stated by
  the provider, and both available plan schemas exceed it.
- **The corpus is 30 cases, not a survey.** It is built to exercise each
  capability at least once, not to characterise a distribution.
- **`pattern` is `through_hole` only** (`PATTERNABLE_TYPES`), so E and F4
  measure the one repetition the language currently allows.
- **Sketch chains remain unbuildable**, by design: `C1`, `C2`, `D2` and `D3`
  score a *valid plan* as correct and the engine still refuses it.
- **The `no_part` class has one case.** One case is a probe, not a rate.
- **FreeCAD is not exercised.** `CAD_BACKEND` defaults to `cadquery` and
  never falls back; a second-backend comparison is its own stage.

## MEASURED (Stage 52): the first real capability evaluation

270 live calls, `claude-haiku-4-5-20251001`, structured output on, three
passes. Files: `stage52-A-profile_union.json`, `stage52-B-executable.json`,
`stage52-C-v1-legacy.json`.

| Pass | Encoding | Arm | Cases | Calls | Not called (not expressible) |
|---|---|---|---:|---:|---:|
| A | `profile_union` `a5c3484f…` 4481 | plan | 18 | 90 | 12 |
| B | `executable` `54759d1e…` 3622 | plan | 23 | 115 | 7 |
| C | V1 schema | v1_json | 13 | 65 | 0 |

**Results are deliberately NOT combined into one score.** The instruments
have different capability envelopes and are not commensurable.

| | A `profile_union` | B `executable` (corrected) | C V1 arm |
|---|---|---|---|
| semantic correctness | **85.6%** | **94.1%** | 60.0% |
| build success | 40.0% | 58.8% | 21.5% |
| model output valid | 100% | 100% | 100% |

**Read the correction note.** Pass B's raw numbers (71.3% semantic, 47.0%
build) include 30 records from seven cases needing `straight`/`circular`
selectors that no proven encoding carries. The model, unable to say "the top
rim", said `select: "all"` and the build failed geometrically. Those are
encoding limits recorded as build failures, not model errors — the partition
checked operation types but not selector modes. Corrected figures exclude
them; every `BUILD_FAILED` and `SEMANTICALLY_INCORRECT` in B was that
artefact.

Geometry is measured, not inferred: 36 / 54 / 14 builds each carry volume,
solid count, triangle count and bounding box. Backend CadQuery, unchanged.

**Stage 40 and Stage 43 artifacts are untouched.** These are new files under
a new name; nothing frozen was overwritten.

## MEASURED (Stage 53): selector-capable encoding

`selector_provider_schema()` — `893a912002fb6593`, **3134 inlined**, **live
ACCEPTED**. Six solid types (box, cylinder, through_hole, subtract, fillet,
chamfer) with the full selector vocabulary (`all`, `axis_parallel`,
`straight`, `circular`, + `position` top/bottom). Smaller than `executable`
(3622) despite carrying more, because merging fillet/chamfer pays for the
selector several times over.

**Stage 52's proposal — adding selectors to a profile encoding — was wrong.**
It produces a byte-identical schema: nothing in a profile encoding selects an
edge, so the selector definition is pruned away. A selector-capable grammar
must contain `fillet` or `chamfer`.

File: `stage53-selector.json` — 30 calls, six cases, 5 attempts.

| Metric | Stage 52 (`executable`) | **Stage 53 (`selector`)** |
|---|---|---|
| build success on these six cases | 4/30 | **30/30** |
| `executed_by_graph` | 0 | **30/30** |
| selector_correct | n/a | 20/30 |
| semantic correctness | — | 13/30 |

Every one of Stage 52's 26 "build failures" on these cases was the encoding,
not the model. `render_success` is 0/30 **by design** — a graph-executed plan
has no V1 document and therefore no RenderModel.

Two real model failure modes, newly visible: it writes `circular` but leaves
`position` null (chamfering both rims, 56793.48 vs expected 56825.94), and it
picks `axis: Z` where "long edges" means `axis: X`. `selector_correct`
compares the mode only, not the axis, so it is a partial signal.

**Expressibility now checks operations AND selectors** (`case_expressibility`).
Stage 40/43 baselines untouched.

## MEASURED (Stage 54): selector failure decomposition and one A/B fix

Files: `stage54-A-baseline.json`, `stage54-B-selector-guidance.json`.
24 calls, 6 cases, 2 attempts, `selector` encoding, same model — **only the
prompt differs**.

Stage 53's `selector_correct` 20/30 hid two disjoint errors: `position`
omitted (F2/F3, 10 records) and wrong `axis` (D1/G2, 7 records). Mode was
30/30; "axis correct 30/30" was **vacuous** because the corpus pins no axis —
geometry is what catches an axis error.

| | A `2026-09-15.5` | B `2026-09-16.1` |
|---|---|---|
| semantic correctness | 6/12 | **8/12** |
| selector axis | 11/12 | **12/12** |
| selector position | 8/12 | **8/12 unchanged** |
| build / graph | 12/12 | 12/12 |

D1 and G2 each went 1/2 → 2/2. F2/F3 stayed 0/2.

**The axis clause worked; the position clause did nothing** — and the model
was not confused: on F2 its own summary says "a 1 mm chamfer on the top rim"
while the selector omits `position`. The field is sayable and the guidance is
present; under structured output the model fills required fields and omits
optional ones. This is representational, not reasoning, and the indicated
fix — making the rim end a required provider-encoding field — is documented
and deliberately left for its own stage.

Stage 53's `stage53-selector.json` was not modified. Stage 40/43 untouched.

## MEASURED (Stage 55): structural position — 0/4 → 4/4

File: `stage55-strict-selector.json`. 4 calls, F2/F3, 2 attempts each.

`strict_selector_provider_schema()` — `887718d3e5387529`, **3619 inlined**
(862 below the proven 4481). The selector becomes a discriminated union and
the circular branch **requires** `position`.

| | Stage 54 flat/optional | **Stage 55 union/required** |
|---|---|---|
| selector position correct | **0/4** | **4/4** |
| semantic correctness | **0/4** | **4/4** |
| build / graph | 4/4 | 4/4 |

Same model, same prompt `2026-09-16.1`, same operations and modes — only the
selector's structure differs. F2 → `circular+top`, F3 → `circular+bottom`,
both attempts each.

Volume 56825.944 (one rim) vs Stage 54's 56793.48 (both rims) proves one rim
was chamfered; F2 and F3 are volumetrically **identical**, so which end was
chosen is established by the selector, not the geometry.

**Structural optionality was the cause.** Under structured output the model
fills required fields and omits optional ones; prompt wording could not
change that while the field stayed optional.

The canonical language still permits a positionless circular selector (both
rims) and the parser still accepts it — the encoding is deliberately
narrower. Stage 53/54 result files unmodified; Stage 40/43 untouched.
