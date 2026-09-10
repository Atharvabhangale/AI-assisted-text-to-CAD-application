# Stage 40 — V1 JSON vs the CAD operation plan, on real Claude Haiku 4.5

**This is the first live Anthropic measurement in this project's history.**
Every earlier evaluation ran on Gemini or on a stub; `ANTHROPIC_API_KEY` had
never been available. The credential used here was supplied by the operator
as `CAD_ANTHROPIC_API_KEY`. No credential value appears in any file in this
directory, and the files were scanned to confirm it.

## What was run

| | |
|---|---|
| Model | `claude-haiku-4-5-20251001` (served model confirmed by the API) |
| Cases | 13, from the Stage 40 brief, verbatim |
| Attempts | 5 per case per representation — **130 real calls**, equal on both arms |
| Corpus | `1.0.0`, fingerprint `6e15d270…` |
| V1 prompt | `2026-09-08.1`, `2b3e3395…`, 14 943 chars |
| Plan prompt | `2026-09-10.7`, `5ef08dd0…`, 14 685 chars |
| Tokens | 535 635 in, 20 914 out |

Both prompts, both schemas, both parsers, both validators, the corpus and the
expected geometry were frozen and committed **before** the first live call
(commit `b22e4ae`). Nothing was tuned afterwards.

## Files

| File | What it is |
|---|---|
| `comparison-run.json` | Every one of the 130 attempts, with raw model output, latency, usage, stage flags and failure category |
| `comparison-report.txt` | The measured result, as the harness printed it |
| `failure-diagnosis.json` | The replay of the recorded output through the same frozen machinery, to locate each failure |
| `failure-diagnosis.txt` | The diagnosis, as printed |

## The measured result

**0% semantic correctness on both arms.** All 130 calls were answered by the
model and all 130 were rejected at parse.

One cause dominates: Claude Haiku wrapped its JSON in a markdown fence, and
**both** frozen parsers refuse a fence by design — neither repairs model
output, deliberately, on both sides. The strict transport, not the
representation, decided every case.

Read `comparison-report.txt` as the result. It is real, and on its own it
says almost nothing about the question the stage asked.

## Why structured output was off — and why that is itself a finding

The frozen representations were designed to be used *with* API structured
output, which is what makes a strict parser reasonable. Measured against the
live API:

- the **V1** schema is accepted once `exclusiveMinimum` is stripped — 8
  optional properties;
- the **operation plan** schema is refused outright — **31** optional
  properties against an API limit of **24**, because its flat `parameters`
  object must hold all fifteen parameter names of all nine operation types,
  every one optional. No sanitising fixes that; it is the shape of the
  representation.

So there is no configuration in which both frozen representations can use
structured output. Running V1 with it and the plan without would have handed
V1 a grammar-constrained decoder the plan cannot have. Both were therefore
run without it, and the asymmetry is reported rather than hidden.

This was established before any model output was observed: the first 130
calls were rejected by request validation, so no result was seen.

## The diagnosis (a counterfactual, not the result)

Replaying the recorded output through the same frozen parsers, validators,
adapter and build — after extracting the fenced JSON block, the only leniency
added — gives what each representation would have scored under a
fence-tolerant transport:

| | V1 JSON | Operation Plan |
|---|---:|---:|
| Would-be correct | 16 / 65 | **56 / 65** |
| Rate | 24.6% | **86.2%** |

On the eight cases that produce manufacturable geometry, the operation plan
was **40/40** and V1 was **0/40**.

V1's failures were about the envelope, not the CAD: 22 answers were prose with
no JSON at all, and 16 were a correct-looking document with no `status`
wrapper. The plan's failures were 3 parse rejections on an explicit
`"reason": null`, and 6 refusals of the sketch cases — traceable to this
project's own prompt wording, which tells the model an extrude "cannot be
built", and which the model reasonably read as "unsupported".

**This is a diagnostic, not a score.** It was computed after the scores were
seen; it replaces nothing; and no expectation, prompt, parser or validator
was changed to produce it.

## What this does and does not license

It does **not** show the operation plan is production-ready. It shows that on
one model, one corpus of 13 cases and 5 attempts each, with both transports
strict and structured output unavailable to one side, neither representation
worked at all — and that under a fence-tolerant transport the plan's envelope
was followed far more reliably than V1's.

Any decision to adopt the operation plan needs a re-run in a configuration
both representations can actually use.
