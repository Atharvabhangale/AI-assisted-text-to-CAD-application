# Preserved evaluation baselines

Live benchmark runs, copied out of the gitignored `evaluation-results/`
directory so they survive the machine that produced them.

`evaluation-results/` stays gitignored: it is where local runs land, and a
working directory of scratch measurements does not belong in git. What is kept
here is different — it is the only real-model data the project has, it cost a
day's free-tier allowance or real money to produce, and it cannot be
regenerated on demand. Everything here was scanned before being committed: no
API key, no authorization header, no environment dump, no credential of any
kind appears in any of it.

Two kinds of thing live here: the four loose `eval-*.json` files below, which
are Gemini corpus runs from the stable branch's Stage 29, and the two
**directories**, which hold the `experiment/cad-operation-graph` branch's
representation comparisons:

| Directory | Branch / stage | What it is |
|---|---|---|
| `stage40-v1-vs-operation-plan/` | experiment, Stage 40 | The **frozen baseline**: V1 vs the operation plan, structured output **off**. Both arms 0% |
| `stage43-structured-output/` | experiment, Stage 43 | The same comparison with structured output **on** — the first run in which the two representations were actually comparable |

**Stage 43 does not replace Stage 40.** They are different configurations of
the same instrument and both are kept; see the Stage 43 section at the end of
this file.

They are **immutable records**. Do not edit them, and do not overwrite one
with a later run. `cad_ai.evaluation.save_run` refuses to overwrite an
existing run id for the same reason.

| File | Provider / model | Paced | Genuine responses | What it records |
|---|---|---|---|---|
| `eval-20260909T031125Z-2df4bd94.json` | gemini / `gemini-2.5-pro` | no | 0 / 35 | The **404**: "no longer available to new users". This is the model still set as `DEFAULT_MODELS["gemini"]` in `cad_ai/config.py` |
| `eval-20260909T031712Z-48ceed89.json` | gemini / `gemini-3.8-flash` | no | 1 / 35 | Model-availability probing during the Stage 28 live attempt |
| `eval-20260909T032207Z-bd4d5d23.json` | gemini / `gemini-3.5-flash` | no | 14 / 35 | The run whose 429 body identified the real constraint: `generate_content_free_tier_requests, limit: 20` |
| `eval-20260909T034721Z-7a953503.json` | gemini / `gemini-2.5-flash` | 12 s | **20 / 35** | **The Stage 29 baseline** — the current best real-model measurement |

The first three predate the Stage 29 evaluator changes and so carry no
`provider_reliability` block. **Do not read their `correct_rate` as model
quality.** That field divides by all 35 cases, including the ones the provider
never answered — which is exactly the conflation Stage 29 removed. For
`gemini-3.5-flash` it reads `0.3714` off 14 genuine responses; for
`gemini-2.5-pro` it reads `0.0` off zero responses, for a model that was never
reached at all.

## Reading the Stage 29 baseline

Run id `eval-20260909T034721Z-7a953503`, prompt `2026-09-08.1`
(`2b3e3395…9ba52`), corpus `1.0.0`, 35 cases, one attempt each, no retries,
no concurrency.

**Provider reliability and model quality are separate numbers and must stay
separate.** Coverage was 20/35 — fifteen cases returned HTTP 429 and are
recorded as *unmeasured*, not as wrong answers. Folding them into the quality
score would report 17/35 = 0.486 for a model that scored 0.85 on what it
actually answered.

On the 20 answered cases: parse 20/20, validation 13/13, build 13/13,
geometry correctness 12/15, and exact document match 0/15 — the exact-match
zero is entirely label, description and feature-id differences, not CAD
errors. Categories F (adversarial) and G (semantic edge cases) received **no
responses at all**, so this run says nothing about either; the recorded
`boundary_violations: 0` is vacuous.

Case `A6-box-reversed-dimension-order` is worth reading directly: the model
returned a valid, buildable box with **the same volume as the expected
answer** and X/Y swapped. It is the project's standing proof that valid CAD is
not necessarily correct CAD.

See `docs/ai-evaluation.md` for the harness and metric definitions, and
`CLAUDE.md` §6–7 for the current status and how to run a live benchmark.

## Reading the Stage 43 structured-output comparison

`stage43-structured-output/stage43-structured-run.json`. This is on the
`experiment/cad-operation-graph` branch and has nothing to do with the Gemini
corpus runs above — different corpus, different question, different harness.

| | |
|---|---|
| Model | **live Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) |
| Calls | **130 real calls** — 13 cases × 5 attempts × 2 arms, equal by construction |
| Structured output | **ON, natively, for both arms** (`output_config` / `json_schema`) |
| Compared | **V1 JSON vs the CAD operation plan** |
| Provider errors | 0 |
| Fenced answers | **0 / 130** |

**The instrument is Stage 40's, unchanged.** Prompt and corpus fingerprints in
this run match the frozen baseline exactly — V1 prompt `2026-09-08.1`
(`2b3e3395…`), plan prompt `2026-09-10.7` (`5ef08dd0…`), corpus `1.0.0`
(`6e15d270…`). Same cases, same attempts, same parsers, same validators, same
adapter, same build, same scoring. Nothing strips a fence, repairs, retries or
re-prompts on either side. The run's own `schema_attached_per_arm` records
65 / 65, so both arms really were schema-constrained.

**Result:**

| Metric | V1 JSON | Operation Plan |
|---|---:|---:|
| Build success | 15/65 = **23.1%** | 40/65 = **61.5%** |
| Semantic correctness | 40/65 = **61.5%** | 55/65 = **84.6%** |
| Wrongly refused | **25/65** | **10/65** |
| Correct refusals | 25/65 | 15/65 |

The operation plan was 5/5 on all eight buildable cases. Both sides' failures
are refusals rather than bad geometry, and both are perfectly clustered: V1
refused every modifier case (`04`–`08`, 5/5 each), and the plan refused only
the two sketch cases (`09-profile-extrude` and `10-profile-revolve`, 5/5
each).

**This is not a replacement for the Stage 40 frozen baseline, and must not be
merged with it or presented as a correction to it.** Stage 40 measured both
representations over a strict transport with no grammar constraint, because
at that time the plan schema could not be compiled at all; its 0%/0% is a real
result about that configuration and stays on the record. Stage 43 changed
exactly one thing — the API constrains the output format — which is what made
the two representations comparable for the first time. Keep both: the pair is
the evidence that the Stage 40 result was decided by the transport rather than
by either representation.

Like every file here this is an **immutable record**. Do not edit it, and do
not overwrite it with a later run.

See `docs/experimental-operation-plan.md` → *Stage 43* and `CLAUDE.md` §19.
