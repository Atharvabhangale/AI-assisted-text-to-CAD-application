# Stage 43 — V1 JSON vs the CAD operation plan, with structured output ON

**The first run in which the two representations were actually comparable.**
Stage 40 ran the same 130 calls with structured output off — because the plan
schema could not be compiled at the time — and scored 0% on both arms, every
answer having arrived in a markdown fence that both parsers refuse by design.
The transport decided all 130 cases. Stage 41 made the plan schema
provider-compatible; Stage 43 turned structured output on for both arms and
changed nothing else.

No credential value appears in any file in this directory, and the files were
scanned to confirm it — the only `token` strings present are `input_tokens`
and `output_tokens` usage counters.

## What was run

| | |
|---|---|
| Model | `claude-haiku-4-5-20251001` (live) |
| Cases | 13, the frozen Stage 40 corpus, verbatim |
| Attempts | 5 per case per representation — **130 real calls**, equal on both arms |
| Structured output | **ON for both arms**, natively (`output_config` / `json_schema`) |
| Corpus | `1.0.0`, fingerprint `6e15d270…` |
| V1 prompt | `2026-09-08.1`, `2b3e3395…`, 14 943 chars |
| Plan prompt | `2026-09-10.7`, `5ef08dd0…`, 14 685 chars |
| Provider errors | 0 |
| Tokens | 772 820 in, 13 284 out |

**Every one of those prompt and corpus fingerprints is identical to Stage
40's.** The parsers, validators, adapter, build and scoring rules are Stage
40's, imported and used unchanged;
`representation_comparison.STRUCTURED_OUTPUT_ENABLED` is still `False` in the
frozen module, and a test asserts it. Nothing here strips a fence, repairs,
retries or re-prompts.

## Files

| File | What it is |
|---|---|
| `stage43-structured-run.json` | Every one of the 130 attempts, with raw model output, latency, usage, stage flags, category, and the `stage43_state` block recording both schema fingerprints |

## The measured result

**Structured output eliminated the transport failure completely: 0 of 130
answers were fenced,** against 110 of 130 in Stage 40. `schema_attached_per_arm`
records 65 / 65, so both arms really were constrained by their own grammar.

| Metric | V1 JSON | Operation Plan |
|---|---:|---:|
| Build success | 15/65 = **23.1%** | 40/65 = **61.5%** |
| Semantic correctness | 40/65 = **61.5%** | 55/65 = **84.6%** |
| Wrongly refused | **25/65** | **10/65** |
| Correct refusals | 25/65 | 15/65 |

The operation plan was **5/5 on all eight buildable cases (40/40)**.

**Both sides fail by refusing, not by producing bad geometry, and both
clusters are exact.** V1's 25 wrong refusals are the five modifier cases —
`04-plate-centre-hole`, `05-plate-two-holes`, `06-cube-bore`,
`07-plate-chamfer`, `08-plate-fillet` — 5/5 each; it refused every part
carrying a modifier, despite the engine having built those since Stage 14.1.
The plan's 10 are `09-profile-extrude` and `10-profile-revolve`, 5/5 each, and
nothing else — consistent with the prompt bug this branch deliberately leaves
unfixed, whose wording tells the model an extrude "cannot be built".

## What this does and does not license

It **does** support the conclusion that the operation plan is the better
model-facing intermediate representation for executable CAD planning: it wins
on both axes at once, on the same model, prompts, cases and scoring, with the
transport contamination removed.

It does **not** show either representation is production-ready, and it is
still one model, one corpus of 13 shallow single-part cases, and 5 attempts
each. Categories equivalent to the production corpus's adversarial and
semantic edge cases do not exist here at all.

## This does not replace the Stage 40 baseline

`../stage40-v1-vs-operation-plan/` stays exactly as measured. Its 0%/0% is a
true result about a configuration in which only one representation *could*
have used structured output, and running the other with it would have handed
V1 a grammar-constrained decoder the plan could not have. Do not merge the two
runs, do not present Stage 43 as a correction to Stage 40, and do not quote
one without the other: **the pair is the evidence** that Stage 40's result was
decided by the transport rather than by either representation.

Both are immutable records. Do not edit them, and do not overwrite either with
a later run.
