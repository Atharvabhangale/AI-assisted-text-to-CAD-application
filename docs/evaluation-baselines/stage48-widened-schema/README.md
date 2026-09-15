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

## Limitations

- **No Stage 48 number exists yet.** Everything here is the instrument.
- **Provider acceptance of the widened schema is still unverified.** Every
  offline limit is satisfied and asserted, but compiled-grammar size can only
  be measured by sending it. That is what `--probe-live` is for, and it has
  not been run.
- **The corpus is 30 cases, not a survey.** It is built to exercise each
  capability at least once, not to characterise a distribution.
- **`pattern` is `through_hole` only** (`PATTERNABLE_TYPES`), so E and F4
  measure the one repetition the language currently allows.
- **Sketch chains remain unbuildable**, by design: `C1`, `C2`, `D2` and `D3`
  score a *valid plan* as correct and the engine still refuses it.
- **The `no_part` class has one case.** One case is a probe, not a rate.
- **FreeCAD is not exercised.** `CAD_BACKEND` defaults to `cadquery` and
  never falls back; a second-backend comparison is its own stage.
