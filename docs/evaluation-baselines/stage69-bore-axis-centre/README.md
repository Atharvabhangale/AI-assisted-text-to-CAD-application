# Stage 69 — the residual bore failure measured, and removed

**224 live calls** to `claude-haiku-4-5-20251001`, all on the **EXPLICIT**
golden request, one variable at a time. Every arm's raw output, plan,
validation, kernel geometry and verdict is in this directory.

| | |
|---|---|
| prompt before | `2026-09-18.3` / `c78aaad8eacf365e` / 30481 chars |
| **prompt now** | **`2026-09-18.5` / `8563c6fb821e022f` / 30917 chars** |
| encoding | `strict_selector_union` (3628 inlined, 5 branches) — unchanged |
| backends | CadQuery 2.8.0 and FreeCAD 1.0.0 |

`2026-09-18.4` is **deliberately unused**: Stage 67 adopted that number for an
arm it then reverted, so it names a prompt that exists nowhere in the history.

## 1 — the Stage 68 reading was wrong, and 24 calls said so

Stage 68 saw one failure in eight on the explicit request and read it as the
`+Z` bore's position triple being copied to the other two axes. Phase 1 ran
**24 fresh calls on the unchanged prompt**:

| | |
|---|--:|
| cross-axis triple reuse | **0/24** |
| all three triples identical | **0/24** |
| `E:bore_position` | 2/24 |
| `H:wrong_target` (P11) | **3/24** |

The triple-copy hypothesis is **refuted**. Stage 68's single sighting happened
to be a full copy; the general mechanism is narrower.

## 2 — what the defect actually is

Over **64 pooled baseline attempts** (Stage 68's 8 + Phase 1's 24 + arm A0's
32 — the same prompt, request and encoding throughout), 96 bores and 192
across-axis components:

| bore axis | X component | Y component | Z component |
|---|--:|--:|--:|
| **+X** | — | **0/64** | **5/64** |
| **+Y** | **0/64** | — | **4/64** |
| **+Z** | **0/64** | **0/64** | — |

Every one of the 9 wrong components is the **z** of a bore that does not run
along Z. `x` and `y` were never wrong, in any position. The along-axis
component was written as `0` on **94 of 96** bores, so the model has the
"0 is safe along the axis" half of the rule and over-applies it to the
letter `z`.

## 3 — the matrix: two negative results and one fix

Four arms, **32 live calls each**, one variable at a time. 8 calls per arm —
what the brief allowed — cannot separate a 8 % effect from zero, so each arm
is 32 and the baseline is 64.

| arm | what changed | strict | `E` | `H` | wrong components |
|---|---|--:|--:|--:|---|
| **A0** baseline | nothing | 28/32 | 2 | 3 | all z |
| **A1** no-letter | the axis table rewritten so no letter is paired with "may be 0"; nothing added | 27/32 | **4** | 1 | all z |
| **A2** worked-triples | one worked example whose bores carry a **nonzero z** | **31/32** | **0** | 1 | **none** |
| **A3** order | the same three table lines **reordered** so `+X` is first — a pure reordering, same length, same characters | 27/32 | **4** | 1 | all z |

**A1 is a negative result:** deleting the letter-to-zero pairing made it
worse, not better. Prose about the rule moved nothing, for the third stage
running.

**A3 is the discriminator, and it refutes the table.** If the defect were
positional — the first line a reader meets pairing `z` with a zero — reversing
the order would have moved the error onto `x`. It did not: all 8 wrong
components were still `z`. The table is not the mechanism.

**What moved it was the missing EXAMPLE.** Every hole position the prompt
*showed* had `z` at 0, because every example bore ran along `+Z`. A2 adds one
worked example in the prompt's own 60 × 30 × 30 numbers — never the golden
request's, so nothing teaches to the test — two of whose three bores carry a
nonzero `z`.

This is Stage 65's and Stage 66's mechanism found a third time: **what the
model imitates is what the prompt shows, and a rule stated alongside a
contradicting example loses.**

## 4 — the confirmation, and the regression guards

A2 was re-run twice more at 32 calls each. Pooled A2 **n = 96** against the
pooled baseline **n = 64** (Fisher exact, two sided):

| | baseline | A2 | p |
|---|--:|--:|--:|
| **STRICT SUCCESS** | 54/64 (0.844) | **91/96 (0.948)** | **0.049** |
| `E:bore_position` | 5/64 (0.078) | **1/96 (0.010)** | **0.038** |
| bores centred | 59/64 | **95/96** | **0.038** |
| topology 18/42 | 54/64 | 91/96 | 0.049 |
| built | 58/64 | 92/96 | 0.200 |
| envelope 40×20×20 | 58/64 | 92/96 | 0.200 |
| `H:wrong_target` (P11) | 6/64 | 4/96 | 0.200 |
| **thickness = 5 mm** | **64/64** | **96/96** | 1.000 |
| **plate count = 6** | **64/64** | **96/96** | 1.000 |

**No guard regressed.** Stage 67 rejected its best arm because it traded the
spatial structure for the plate thickness; A2 costs nothing anywhere.

A2's single remaining `E` is **not** this defect: that attempt wrote all three
bores on axis `+Z`, which is `F:bore_direction`. **The z-zeroed defect is 0 in
96 attempts.**

## 5 — kernel evidence, MODEL_GENERATED

All **91** claimed successes rebuilt from recorded output — no model called,
no fixture, no deterministic reader:

| | |
|---|---|
| distinct volumes | **11492.035526277** (one value, across all 91) |
| closed form | 11492.035526276899 |
| max &#124;delta&#124; | **1.819e-12** |
| solids / faces / edges | 1 / **18** / **42** |
| envelope | 40 × 20 × 20 |
| mismatches | **0** |
| backends | CadQuery **2.8.0** and FreeCAD **1.0.0**, **bit-identical** |

## 6 — what remains open

**P11 is not gone.** Stage 68 reported it 0/8 on the explicit request and
CLAUDE.md read that as disambiguation having removed it. Over 64 baseline
attempts it is **6/64**, and 0/8 versus 3/24 is p = 0.55 — no evidence they
differ. It is *lower* on the explicit request than on the ambiguous one
(6/64 vs 3/8) but that comparison is p = 0.082, **not significant**. Under A2
it is 4/96, and every one of those attempts targets the product noun
`enclosure`. It is now the dominant residual and is Stage 70's target.

**One attempt in 96 wrote all three bores on `+Z`** (`F:bore_direction`),
which had not been seen before. One sighting; not a rate.

## Files

| file | what |
|---|---|
| `axis_analysis.py` | the per-bore reading. Adds to Stage 68's evaluator; replaces nothing |
| `variants.py` | the four arms, each a pure function of the baseline. Never edits `prompt.py` |
| `arena69.py` | one arm, N live calls. Imports Stage 68's `arena.py`, `evaluate.py` and `ground_truth.py` **unmodified** |
| `prompt_guard.py` | the committed prompt's identity, read before and after every arm |
| `verify.py` | Phase 4: rebuilds recorded successes on a named backend. Calls no model |
| `mutation_test.py` | reintroduces each defect and asserts the guard goes red. 6/6 caught |
| `phase1-baseline.json` | 24 calls, committed prompt, unpatched |
| `arm-A0-baseline.json` … `arm-A3-order.json` | the matrix, 32 calls each |
| `arm-A2-confirm-1.json`, `arm-A2-confirm-2.json` | 64 confirmation calls |
| `verify-cadquery.json`, `verify-freecad.json` | the kernel rebuilds |

**Stage 68's directory is untouched.** `arena69.py` refuses an `--out` inside
it, and its three modules are imported rather than copied, so the two cannot
drift.

**No credential value appears in any file here.** `credential_from` records
the environment variable *name* only.

## Reproducing

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:\
docs/evaluation-baselines/stage68-benchmark-disambiguation:\
docs/evaluation-baselines/stage69-bore-axis-centre
cd apps/api
D=../../docs/evaluation-baselines/stage69-bore-axis-centre

# offline; calls nothing
python3 $D/variants.py

# live; --live is required, a credential's presence never starts a run
python3 $D/arena69.py --calls 32 --request explicit --variant A0-baseline --live
python3 $D/arena69.py --calls 32 --request explicit --variant A1-no-letter --live
python3 $D/arena69.py --calls 32 --request explicit --variant A2-worked-triples --live
python3 $D/arena69.py --calls 32 --request explicit --variant A3-order --live

# offline; rebuilds recorded output
CAD_BACKEND=cadquery python3 $D/verify.py $D/arm-A2-*.json
CAD_BACKEND=freecad  python3 $D/verify.py $D/arm-A2-*.json
```

Note that `variants.py` now reports **A2 ADOPTED**: A2's text *is* the
committed prompt, so `_a2()` is a no-op against it. That is the correct
post-adoption state, and the module says which world it is in rather than
asserting a difference adoption removed.
