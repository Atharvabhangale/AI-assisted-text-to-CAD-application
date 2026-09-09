# Preserved evaluation baselines

Live benchmark runs, copied out of the gitignored `evaluation-results/`
directory so they survive the machine that produced them.

`evaluation-results/` stays gitignored: it is where local runs land, and a
working directory of scratch measurements does not belong in git. These four
files are different — they are the only real-model data the project has, they
cost a day's free-tier allowance to produce, and they cannot be regenerated on
demand. Each was scanned before being committed: no API key, no authorization
header, no environment dump, no credential of any kind appears in them.

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
