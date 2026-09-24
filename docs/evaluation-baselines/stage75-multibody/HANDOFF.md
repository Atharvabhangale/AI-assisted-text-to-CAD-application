# Stage 75 handoff — begin with the live baseline

**Read this first, then `README.md` beside it, then `CLAUDE.md` §19 → "Stage
75".**

This session stopped deliberately at a clean prompt + evaluator checkpoint.
The instrument is finished and committed; **no model has been asked any
multi-body question.** The next session's first act should be the live
baseline, run as **one contiguous measurement** rather than split across two
sittings — a run taken in halves under different conditions is two runs.

---

## 1. Current HEAD

| | |
|---|---|
| repository | `Atharvabhangale/AI-assisted-text-to-CAD-application` |
| branch | `experiment/cad-operation-graph` |
| worktree | `/home/user/cad-experiment` (the stable branch lives in `/home/user/AI-assisted-text-to-CAD-application`) |
| HEAD | `<<HEAD_SHA>>` |
| origin | `<<HEAD_SHA>>` — identical; working tree clean |

**Do not touch `claude/text-to-cad-skeleton-r946xr`.** Stage numbers collide
between the two branches; "Stage 32" means different work on each.

Verify before doing anything:

```sh
cd /home/user/cad-experiment
git branch --show-current && git status --short
git rev-parse HEAD && git rev-parse origin/experiment/cad-operation-graph
```

## 2. Prompt: version and fingerprint

| | |
|---|---|
| version | **`2026-09-24.1`** |
| fingerprint | `c0c4a1be0d23052fa8b2f36d0e1c3722eeb6b52f904d0cf56a6db43426af3e49` |
| characters | **33407** |
| file | `apps/api/src/cad_experimental/prompt.py` |

What Stage 75 added: a top-level **`# Several bodies`** section before
`# Units`, with a worked example in the prompt's own numbers (a 50×50×8
`plate`, a Ø12×25 `post`, two `part` declarations, and a `bore` that targets
the plate and only the plate); a `part` reply skeleton; and five rules —
declare every standing body exactly once; a body is named by the solid's own
id, never the declaration's; **one body means no `part` at all**; an id names
the body, not the product; and "fuse" is a `union` and ONE body — closing
with the rule that a later request naming no body must ask rather than
choose, because **guessing is worse than asking**. The single-solid rule is **qualified, not
deleted**: its pinned phrase `exactly ONE solid left` is unchanged.

**Two tests pin the exact version and fingerprint, and both must move
together** if the prompt ever changes on purpose:
`test_extrude_revolve.PromptTests.test_the_version_moved_with_the_vocabulary`
(the canonical pin) and
`test_multi_body.TheDeclarationTierChangesNoRecordedIdentityTests.test_the_prompt_now_teaches_the_declaration`.
Four further tests in `test_multi_body.py` pin the *content* of the new
section rather than its hash — that it shows `part` rather than describing
it, that it does not recommend `part` for one body, that it refuses to guess
which body, and that the single-solid rule is qualified rather than deleted.

## 3. Schema: name, size and fingerprint

| | |
|---|---|
| encoding | **`strict_selector_union_part`** |
| inlined characters | **3874** |
| fingerprint | `ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f` |
| branches | **6** — box, cylinder, through_hole, `subtract\|union`, `fillet\|chamfer`, **`part`** |
| live verdict | **ACCEPTED** — one probe, `structured_output` true 2/2, 0/2 fenced |
| ceiling bracket | (4481, 4551]; 3874 is well inside |
| builder | `plan.strict_selector_union_part_provider_schema()` |
| the live route | `generation.PLAN_SCHEMA_NAME` — switched from `strict_selector_union` (3628) in this stage |

**The grammar was measured accepted BEFORE the prompt moved.** That order is
the stage's main structural claim and the reason it is safe to measure now:
had the prompt gone first, a refusal would have been indistinguishable from
an inexpressible request — Stage 44's defect, which Stages 48, 62 and 63 each
found again elsewhere.

**Nine recorded provider fingerprints did not move**, because `part` stays
out of `OPERATION_TYPES` and lives in its own `DECLARATION_TYPES` tier:
`plan_schema 34b6391fa9ce4700`, `provider_schema 838aba85e5fa7587`,
`compact_provider_schema c5936b06e6acba86`, `executable_schema
54759d1e16cfe634` (Stage 43's frozen instrument) among them.

## 4. The multi-body corpus

Eight cases in `ground_truth75.py`. **Six creation, two refusal, scored
separately and never pooled** — a refusal rate and a build rate are different
quantities, and pooling them is how a model that refuses everything scores
well.

| case | group | request (verbatim) |
|---|---|---|
| **M1** | creation | *Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it as two separate bodies.* |
| **M2** | creation | *Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it. Name the bodies cube and pin.* |
| **M3** | creation | *Create two separate boxes, one 40 mm wide and one 20 mm wide, beside each other as independent bodies.* |
| **M4** | creation | *Create a 40 mm cube and a 20 mm cylinder as separate bodies, then make the cylinder 40 mm long.* |
| **M5** | creation | *Create a cube and a cylinder as separate bodies, then put a 6 mm through hole through the cylinder.* |
| **M6** | refusal | *Make the body 10 mm taller.* |
| **M7** | refusal | *Make the bracket 10 mm taller.* |
| **M8** | creation | *Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long beside it, fused together into a single body.* |

M8 is **the control**: an explicit fuse must give ONE body and must NOT
declare a `part`. It catches a model that has learned to declare bodies
indiscriminately, which is the predictable way a multi-body prompt goes
wrong and is invisible to any check that only counts bodies.

The refusal cases are sent as a **revision** of `REFUSAL_FIXTURE`, a
deterministic two-body plan (`block` 30³, `rod` Ø10×25) that `arena75.py`
builds itself. **Verified in this session**: it builds on CadQuery 2.8.0 to
`block` 27000.000000 mm³ / 6 faces and `rod` 1963.495408 mm³ / 3 faces, and
the revision context names both. It is never model-generated — if it were, a
setup failure would be recorded as a refusal failure.

## 5. The immutable ground truth

`ground_truth75.py`. Every number derived from the request text and
closed-form arithmetic, before any model was asked anything.

| constant | value |
|---|---|
| `CUBE_VOLUME` | 64000.0 |
| `PIN_VOLUME` | 9424.77796076938 (π·10²·30) |
| `PIN_VOLUME_EDITED` | 12566.370614359173 (π·10²·40) |
| `FUSED_VOLUME` | 73424.77796076938 — the sum, because the solids are disjoint |
| `VOLUME_TOLERANCE` | 1e-6, **relative**; float equality is never used on a kernel value |
| `BOX_FACES` / `CYLINDER_FACES` | 6 / 3 |
| `MODEL` | `claude-haiku-4-5-20251001` |

Twelve failure codes, A–L: `missing_body`, `extra_body`,
`wrong_body_identity`, `wrong_target`, `cross_body_edit`, `wrong_placement`,
`wrong_dimensions`, `unwanted_fusion`, `duplicate_declaration`,
`invalid_schema_or_plan`, `refusal_failure`, `other`. Stage 64's five outcome
labels are kept distinct and **only `MODEL_GENERATED` may contribute to a
strict success rate.**

**The structural guarantee:** `expected()` takes a case **name** and nothing
else — never a plan, a shape, a measurement or a response. `grade()` takes
one observation and fetches its own truth. `ground_truth75` imports `math`
and `typing` and nothing else. This is Stage 67's defect made impossible; it
cost that stage its headline number.

**What is deliberately NOT pinned:** body ids everywhere except M2 (the only
request that states them); volumes on M3 (states one extent per box) and M5
(states no dimensions at all). Inventing the rest and grading against it
would be the instrument marking its own homework.

## 6. Exact live measurement commands

```sh
cd /home/user/cad-experiment/apps/api
export PYTHONPATH=../../packages/cad-core/src:src:tests_experimental
ARENA=../../docs/evaluation-baselines/stage75-multibody/arena75.py

# 0. offline. Calls nothing. Confirms the live route is still the instrument
#    the corpus was built against, and prints all eight requests.
python3 $ARENA --check

# 1. the baseline. `--live` is REQUIRED -- a credential's presence never
#    starts a run, and that gate exists because an accidental live run has
#    already happened in this project.
python3 $ARENA --live --calls 8 --out \
  ../../docs/evaluation-baselines/stage75-multibody/baseline.json

# one group, or one case, if the run has to be segmented (prefer not to --
# a segmented run is not one measurement):
OUT=../../docs/evaluation-baselines/stage75-multibody
python3 $ARENA --live --calls 8 --group creation --out $OUT/creation.json
python3 $ARENA --live --calls 8 --case M1 --case M8 --out $OUT/m1-m8.json
```

**Credential.** In Claude Code Web the key arrives as
`CAD_ANTHROPIC_API_KEY`, because the platform reserves and strips
`ANTHROPIC_API_KEY`. `arena75.py` calls `bridge_credential()`, which copies it
across and returns the **variable name only** — never the value. If it
returns `None` the arena prints "no credential in this process; nothing
attempted" and exits 2. It never logs, stores or compares a key.

**8 calls × 8 cases = 64 live calls** for a full baseline. `arena75.py`
refuses to start if the prompt, the model or the grammar is not what §2 and
§3 say — it raises rather than recording a number that looks like the others.

## 7. Expected output artifacts

| artifact | what it holds |
|---|---|
| `stage75-multibody/baseline.json` | the run: identity block, the corpus with its expectations, the deterministic fixture and its measurements, one row per attempt (raw text, usage, `structured_output`, `stop_reason`, fenced, operations, plan problems, per-body measurements, checks, strict verdict, codes, label), and the summary |
| `summary.per_case` | per-case strict count, rate, code histogram, label histogram |
| `summary.per_group` | creation and refusal, **separately — there is deliberately no pooled total** |
| `summary.labels` | the five Stage 64 labels |
| a new `## Stage 75` block in `README.md` | the measured result, written after the run, never before |
| a `CLAUDE.md` update | the numbers, and the honest scope of what they cover |

The arena refuses to write into any earlier baseline directory
(`PROTECTED`), so a run cannot overwrite Stage 68's, 69's or 70's evidence.

## 8. What must NOT be changed before the baseline is recorded

Changing any of these makes the run unrepeatable and the number
incomparable:

1. **The prompt** — `2026-09-24.1` / `c0c4a1be0d23052f` / 33407 chars.
2. **The encoding** — `strict_selector_union_part` / 3874 / `ef7427700af93ed7`,
   and `generation.PLAN_SCHEMA_NAME` pointing at it.
3. **The model** — `claude-haiku-4-5-20251001`.
4. **Every request text in `ground_truth75.py`, verbatim.** Stage 68's
   finding was that the REQUEST is a variable of the experiment: changing one
   makes a **new case with a new name**; it never edits an existing one.
5. **Every expectation in `ground_truth75.py`.** Fix the prompt or the code,
   then re-measure. **Never edit an expectation after seeing a score** — that
   is the one rule this project treats as inviolable, and Stage 67 is why.
6. **`evaluate75.py`'s criteria.** If one is wrong, that is its own finding,
   recorded as such, not a quiet adjustment mid-run.
7. **The nine recorded provider fingerprints**, and `part`'s place outside
   `OPERATION_TYPES`.
8. **Stages 68–70's baseline directories** — evidence, not working files.

Also unchanged by policy: do not start assembly constraints, mates, BOM,
exploded views or complex inter-body booleans; do not add a repair loop; do
not hard-code a corpus case into production behaviour.

## 9. Remaining Stage 75 phases

| phase | what |
|---|---|
| **A — the live baseline** | 64 calls, one contiguous run, `--live`. This is the next session's first act. |
| **B — read it** | per-case and per-group rates with the failure taxonomy; `MODEL_GENERATED` counted separately from every other label. Write the result into `README.md` and `CLAUDE.md`, including what it does **not** cover. |
| **C — one variable at a time** | whatever the measured failures say to change, the way Stages 65, 66 and 69 were run: reproduce N times live, find the root cause, change ONE thing, re-measure, and adopt only against a rule fixed before the confirming run. The predicted failure modes are the ones the corpus was shaped around — over-declaring `part` on M8, guessing a body on M6/M7, and cross-body leakage on M4 — but **they are predictions, not findings.** |

**Stop before C if B's numbers are thin.** A rate from 8 calls per case has
wide intervals; Stages 69 and 70 needed 224 and 288 calls to separate a cause
from a coincidence.

---

## State of the tests at this checkpoint

| suite | result |
|---|---|
| `test_stage75_multibody_evaluator` | **46 passed** |
| full `tests_experimental` (no FreeCAD) | **1991 tests**, 77 skipped, 0 failures |
| mutation sweep of `evaluate75.py` | **18/18 mutants killed** |

The mutation sweep is the reason to trust the grader. Two mutants survived
the first pass and were real gaps, not noise:

1. a grader comparing **total** volume rather than per-body volumes. Two
   disjoint solids fused have *exactly* the total of the two apart, so this
   scores the single most important multi-body failure as a pass;
2. a grader that stops asking whether the model actually **declined**.

Both now have a test that isolates them, and the sweep is committed and
reproducible:

```sh
cd /home/user/cad-experiment/apps/api
PYTHONPATH=../../packages/cad-core/src:src:tests_experimental \
  python3 ../../docs/evaluation-baselines/stage75-multibody/mutation_test.py
```

It refuses to start unless the suite is green, restores `evaluate75.py` from
memory even on a crash, and asserts byte-identity before it exits. A mutant
that SURVIVES is a gap in the tests, not noise — that is how both of the
above were found.
