# Stage 75 — the multi-body instrument

**There is no result in this directory.** No model has been asked any of
these eight questions. What is here is the instrument: the corpus, its
immutable ground truth, and an evaluator that was shown to bite. The baseline
run is the next session's first act, and it should be **one contiguous run**
rather than two halves taken under different conditions.

## What the baseline must be measured against

A number measured under a different prompt, a different grammar or a
different model is a different number and must not be compared to these.

| | |
|---|---|
| model | `claude-haiku-4-5-20251001` |
| prompt | `2026-09-24.1` / `c0c4a1be0d23052f` / 33407 chars |
| encoding | `strict_selector_union_part` / **3874** inlined / `ef7427700af93ed7` |
| branches | 6 — box, cylinder, through_hole, `subtract\|union`, `fillet\|chamfer`, **`part`** |
| live verdict on the grammar | **ACCEPTED** — one probe, `structured_output` true 2/2, 0/2 fenced |
| ceiling bracket | (4481, 4551] — 3874 is well inside |

The grammar was measured **before** the prompt moved. That order is the
stage's main structural claim: had the prompt gone first, a refusal would
have been indistinguishable from an inexpressible request, which is Stage
44's defect and the one this project keeps rediscovering.

## The corpus

Eight cases. The two groups are scored **separately and never pooled** — a
refusal rate and a build rate are different quantities, and pooling them is
how a model that refuses everything scores well.

| case | group | request | what it pins |
|---|---|---|---|
| **M1** | creation | 40 mm cube + Ø20×30 cylinder beside it, "two separate bodies" | the base case: 2 bodies, both volumes, disjoint, both declared. Ids free. |
| **M2** | creation | M1 with "Name the bodies cube and pin" | M1 plus the ids — **the only case where an id is ground truth, because the only case that states one** |
| **M3** | creation | two boxes, one 40 mm wide and one 20 mm wide, beside each other | deliberately under-specified: count, separation, both prismatic, and the two stated extents. **No volume**, because none is stated |
| **M4** | creation | M1, then "make the cylinder 40 mm long" | edit isolation: the cylinder becomes 40 long **and the cube must be untouched** |
| **M5** | creation | cube + cylinder, then a 6 mm through hole through the cylinder | body-targeted cut with no dimensions anywhere: topology only — the hole went into the cylinder, and the cube still has six planar faces |
| **M6** | refusal | "Make the body 10 mm taller." | two bodies stand and "the body" names neither. Must refuse **and name both** |
| **M7** | refusal | "Make the bracket 10 mm taller." | a body that does not exist. Must refuse and say what does |
| **M8** | creation | M1 "fused together into a single body" | **the control.** One body, and `part` must NOT be declared — catches a model that has learned to declare bodies indiscriminately |

Refusal cases start from `REFUSAL_FIXTURE`, a **deterministic** two-body plan
(`block`, `rod`). If the setup were model-generated, a setup failure would be
recorded as a refusal failure and the run would be measuring two things.

## The files

| file | what it is |
|---|---|
| `ground_truth75.py` | the immutable corpus: request texts verbatim, closed-form volumes, the 12-code taxonomy, Stage 64's five outcome labels |
| `evaluate75.py` | `observe` / `grade` / `classify` / `outcome_label` / `summarise` |
| `arena75.py` | the driver. `--check` is offline; `--live` is required for a run |
| `mutation_test.py` | the 18-mutant sweep, reproducible |

`../../../apps/api/tests_experimental/test_stage75_multibody_evaluator.py`
guards both.

## The two properties the tests guard

**Truth cannot come from output.** `expected()` takes a case **name** and
nothing else — the signature is the guarantee, and it is Stage 67's defect
made structurally impossible. `grade()` takes one observation and fetches its
own truth. `ground_truth75` imports `math` and `typing` and nothing else.

**The grader bites.** 18 mutants of `evaluate75.py`, each disabling one
criterion, were applied and the focused suite re-run against every one:
**18/18 killed.** Two survived the first sweep and were real gaps —

1. a grader comparing **total** volume rather than per-body volumes. Two
   disjoint solids fused have *exactly* the total of the two apart, so this
   scores the single most important multi-body failure as a pass. Killed by
   `test_the_right_total_split_between_the_wrong_bodies_fails`, where the
   volumes sum correctly and both bodies are individually wrong;
2. a grader that stops asking whether the model actually **declined**. Killed
   by `test_a_guess_that_happened_not_to_build_is_still_not_a_refusal`, where
   a guess with an invalid plan looks like a clean refusal on every other
   signal.

The sweep is committed as `mutation_test.py` beside this file and is
reproducible. It refuses to start unless the suite is green, restores
`evaluate75.py` from memory even on a crash, and asserts byte-identity before
it exits. A mutant that SURVIVES is a gap in the tests, not noise — that is
how both of the above were found.

Stage 68's arena is deliberately **not** reused: it reads
`execution.bodies[0].measurement`, which on a two-body part grades the part by
its first body. `stage48_capability_evaluation._measure` returns only a count
when bodies != 1, and `harness.py` cannot score a graph-executed build.
Reusing any of them would manufacture successes. An AST test pins the
separation, because a substring search reports the opposite — both modules
quote `bodies[0]` in prose explaining its absence, and that produced one wrong
reading during this stage.

## What must not change before the baseline is recorded

- the prompt, the encoding and the model in the table above;
- every request text in `ground_truth75.py`, **verbatim**. Stage 68's finding
  was that the request is a variable of the experiment: changing one makes a
  **new case with a new name**, it never edits an existing one;
- every expectation in `ground_truth75.py`. Fix the prompt or the code, then
  re-measure. **Never edit an expectation after seeing a score.**

Only `MODEL_GENERATED` may contribute to a strict success rate. Every
multi-body number recorded before this stage is `DETERMINISTIC` and says
nothing whatever about a model.
