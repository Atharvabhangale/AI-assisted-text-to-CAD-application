# HANDOFF — the multi-body observation layer, and what it unblocks

Written at the end of Stage 76, for whoever runs the broader multi-body
corpus. It is a fresh handoff, not an edit of Stage 75's: that one describes
the instrument for dimensions 1–7, and this describes the one for 8 and 9.

**Read `corpus-design.md` first** (`../stage75-multibody/phase-e-r2-tail/`).
It defines the nine dimensions. This says which of them are now gradeable,
with what, and under which rules.

---

## 1 — the state this was written at

| | |
|---|---|
| branch | `experiment/cad-operation-graph` |
| HEAD when Stage 76 began | `a44b901c287f7f158cee6eac4e0ece3b1e29e8ba` |
| model | **`claude-haiku-4-5-20251001`** |
| prompt | **`2026-09-25.1`** / `f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675` / **34036** chars |
| encoding sent | **`strict_selector_union_part`** / **3874** inlined / `ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f` |
| engines | CadQuery **2.8.0**, FreeCAD **1.0.0** |

**Stage 76 changed none of them**, and that is the point: it added an
instrument, not a variable. `arena75.COMMITTED` still holds those five
values and `arena75.check_identity()` still refuses a run that differs.
Verify before any live call rather than trusting this table — it is a
snapshot, the code is authoritative.

---

## 2 — what is now gradeable, and what still is not

| # | dimension | instrument | state |
|---|---|---|---|
| 1 | independent body creation | `evaluate75` | ready before Stage 76 |
| 2 | named body creation | `evaluate75` | ready before Stage 76 |
| 3 | body-targeted edits | `evaluate75` | ready before Stage 76 |
| 4 | holes on named bodies | `evaluate75` | ready before Stage 76 |
| 5 | ambiguous body references | `evaluate75` | ready; **R2's 70.8 % is a known floor** |
| 6 | missing body references | `evaluate75` | ready; carry R2's floor forward, do not re-discover it |
| 7 | explicit fusion | `evaluate75` | ready before Stage 76 |
| **8** | **body measurement** | **`evaluate76` + `observe76.measure`** | **NEW — this stage** |
| **9** | **export** | **`evaluate76` + `observe76.export`** | **NEW — this stage** |

So the honest scope of a broader corpus is no longer "dimensions 1–7".

**What is still missing, and is the next person's first decision:** a
multi-body case's measurement and export are observed with `observe76`,
whose input is an `ExecutionResult`, and a LIVE case's input is a
`PlanGenerationResult`. Joining them is a few lines — execute the model's
plan, then observe — but nobody has written them, and where that join goes
decides whether a failed build is recorded as a measurement failure or as
what it is. Write it so it cannot be the former.

---

## 3 — the files, and the one rule each enforces

| file | the rule |
|---|---|
| `ground_truth76.py` | truth takes a case **NAME**; it imports `math` and `typing` and nothing else |
| `ground_truth76.probes_to_ask` | the observer sees a probe's name, kind and text — **never its expected answer** |
| `fixtures76.py` | the deterministic plans, and the corrupted observations the grader must fail |
| `observe76.py` | records facts; forms the export rung from the **artefact**, not the writer's verdict |
| `browser76.py` | the same observation from the payload a browser receives; re-derives no product decision |
| `evaluate76.py` | **the only module that reads `expected()`** |
| `run76.py` | the offline harness. Calls no model, and cannot claim one was called |
| `mutation_test_76.py` | 28 mutants, 28 caught |

Tests: `apps/api/tests_experimental/test_stage76_observation.py`.

---

## 4 — the live command, and the gates in front of it

**There is no live command in Stage 76, on purpose.** The brief's gate was
that the observer be complete, mutation-tested and proven offline before a
single live call, and the stage stops there. What a live run will need:

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:docs/evaluation-baselines/stage76-observation
export CAD_FREECAD_HOME=/root/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib       # BEFORE python starts
export CAD_ANTHROPIC_API_KEY=...                       # into THAT process

# free, and required first:
cd docs/evaluation-baselines/stage76-observation
python3 run76.py --check            # 7/7 on every available engine
python3 mutation_test_76.py         # 28/28
```

Before spending a call:

1. **`arena75.check_identity()` returns empty.** A number measured under a
   different prompt or grammar is a different number.
2. **`run76.py --check` is 7/7 on both engines**, so a failure in the live
   run is the model's and not the instrument's.
3. **`mutation_test_76.py` is 28/28.**
4. **`--live` is explicit.** A credential's mere presence has started a paid
   run in this project once already; it may never do so again.

---

## 5 — the rules a live run is held to

- **No fallback counted as success.** `observe76.observation` raises on an
  observation labelled `MODEL_GENERATED` with no model output, and on any
  other label that carries model output. `FALLBACK`, `REFUSED`,
  `PROVIDER_ERROR` and `DETERMINISTIC` are four different facts and only
  `MODEL_GENERATED` is evidence about a model.
- **No repair, no re-prompt, no retry.** There is no repair loop in this
  project by design. A plan that fails validation is recorded as failing.
- **Creation and refusal are never pooled.** A refusal rate and a build rate
  are different quantities, and mixing them is how a model that refuses
  everything scores well. `evaluate75.summarise` refuses a combined number
  and so does `evaluate76.summarise`.
- **Measurement and export are never pooled either**, with each other or
  with the build rate. Three quantities, three rates.
- **Never edit an expectation after seeing a score.** A case whose
  expectation turns out to be wrong is RETIRED verbatim with a note and
  replaced by a NEW case with a NEW name — the way Stage 75 retired M4, M6,
  M7, M8 and N1, and the way `ground_truth76.expected` raises on a retired
  case rather than quietly skipping it.
- **The request is a variable of the experiment.** Editing a case's text
  makes a new case, never an edited one. Stage 68's two golden requests
  describe the same part and score 0/8 and 7/8.
- **Preserve every raw answer**, and re-grade offline rather than re-calling
  when an instrument defect is found. Stage 75 Phase C re-graded 360
  recorded attempts without a single new call.

---

## 6 — how measurement and export are scored

**Measurement.** One row per probe, keyed by the probe's NAME. A probe that
was never asked is a FAILURE, not an absence. Per probe:

- the outcome must be the expected one — **`answered`, `refused` and
  `declined` are three different things**, and the difference between the
  last two is the whole point: a refusal reaches the person, a decline falls
  through to a model that can read the plan but has never seen the part;
- an answered probe must be about the expected BODY, carry the expected
  PROVENANCE, carry the expected VALUE, and SAY which body it is about;
- a refused probe's own words must name every body the case requires.

**Export.** Six rungs, strictly increasing; only F passes. `file exists` is
level B at best. Not assessed is not failed: a browser response carries no
measured volume, so the level stops at D with `geometry_assessed: false` and
a reason. Identity binding is **never** claimed at any rung.

---

## 7 — the FreeCAD and CadQuery checks a run must carry

Both engines, every case, and they must **agree body for body**:

- per-body volume against the CLOSED FORM, never against each other alone —
  two engines agreeing on a wrong number is two engines agreeing;
- solid count, face count and edge count per body;
- the STEP written by each engine read back **by that engine** (count,
  names, volumes) and **by the other** — a file only one engine can read is
  not an interchange file;
- the single-body path through `export_step`, unchanged, and pinned per case
  so it cannot quietly start going through the assembly writer.

`run76.py --check` does all of this for the seven fixture cases and prints a
per-case line. It is the shape a live run's check should copy.

---

## 8 — two measured facts that will bite a live run

1. **A single-body STEP carries no body name.** Both engines write the
   translator's own product string. A single-body case therefore pins
   `export_names = ()` and `export_identity = "not_written"`, and its level F
   is a weaker claim than an assembly's. Do not write a live case that
   expects a name from that writer; `ground_truth76.Case` refuses one.
2. **The product's own body-name check is a substring scan** and accepts
   `SOLID`, `part`, `Open` and `cub` (§5.1 of `README.md`). If a live model
   names a body something that occurs in STEP boilerplate, the SERVER will
   accept the export and this observer will not. That disagreement is a real
   signal about the model's naming, not an instrument bug — record it.

---

## 9 — what Stage 76 did not do

- **No live model call**, and no evidence about any model.
- **No prompt change.** The brief: do not return to prompt tuning unless
  this work exposes evidence requiring it. It exposed three product findings
  and one defect in its own observer; none of them is about the prompt.
- **No product change.** §5.1 and §5.3 of `README.md` are findings with
  reproductions.
- **No broader corpus.** Designed in `corpus-design.md`, still not started —
  now with two fewer blockers.
