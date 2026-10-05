# Handoff — next session

> **SUPERSEDED IN PART BY STAGE 78 (2026-09-28).** This file was written at
> the Stage 77 checkpoint. Its §6 names the axis-versus-extent experiment as
> the next task: **that experiment has now been run and did NOT adopt.** See
> `docs/evaluation-baselines/stage78-axis-extent/README.md` for the result
> and for the exact remaining work, which is to complete a confirmation the
> provider's usage limit cut short (access returns 2026-10-01 00:00 UTC).
> Everything else below still stands, and §2's identity is unchanged: the
> committed prompt is still `2026-09-25.1` / `f265d7d1e279e95a` / 34036
> characters, because Stage 78 adopted nothing.
>
> **Re-checked 2026-09-29 12:29 UTC.** The workspace usage limit had not
> reset — one minimal probe returned the same refusal — so nothing was
> measured and nothing was substituted for a measurement. The offline
> instrument was re-run and reproduces itself exactly (gate, both scores,
> 26/26 mutants). Stage 78 remains **INCONCLUSIVE** and the prompt remains
> unchanged.
>
> **Re-checked 2026-10-05, after the reset date.** The allowance window
> did pass, but one minimal probe now returns `401 authentication_error:
> API key is invalid.` — a *different* blocker from September's 400
> allowance error, and one that waiting does not clear. The value in
> `CAD_ANTHROPIC_API_KEY` is well formed and reaches Anthropic directly
> (that host bypasses this container's proxy), so the key itself has been
> rotated, revoked or deleted. No corpus call was spent and no provider
> was substituted. Stage 78 is **blocked on a working credential**, not
> on time; it remains INCONCLUSIVE and the prompt remains unchanged.


Written at a **preservation checkpoint**, not at the end of a stage. Nothing was
built, measured or changed to produce this file: it records the state the
workspace was already in.

Everything below was read out of the repository at the commit named here. Where
a number is a *record* of an earlier measurement rather than something re-run
now, it says so.

---

## 1. Where the work is

| | |
|---|---|
| repository | `Atharvabhangale/AI-assisted-text-to-CAD-application` |
| branch | **`experiment/cad-operation-graph`** |
| HEAD | **`c877e8cf1737591ecdc3e716fad0cf37b9b1fdc5`** (`c877e8c`) |
| origin/experiment/cad-operation-graph | **the same commit** — HEAD is pushed |
| working tree | **clean** — no uncommitted or untracked work |

The seven commits newer than the previously recorded checkpoint `a44b901`:

```
c877e8c CLAUDE.md: Stage 77, and what the 240 calls actually say
f611f6d Stage 77: the live baseline -- 240 calls, and one mechanism behind every failure
55e5236 Stage 77: the offline re-grade, the kernel verifier, and a LIVE browser run
be8dd34 Stage 77: the broader multi-body corpus and its instrument
d85eda8 Stage 76: the record, the handoff, and CLAUDE.md
080e13d Stage 76: the browser layer, the mutation sweep, and the recorded evidence
48b47b2 Stage 76: the multi-body observation layer, measurement and export
```

`git log --oneline -5` and `git status` are authoritative; this is a snapshot.

---

## 2. The live-route identity

Read out of the code at this commit, not recalled. **Any number recorded
anywhere in this project is a number about these five identities**; a
measurement taken under a different prompt or a different encoding is a
different measurement and must not be pooled with them.

| | value | how it was read |
|---|---|---|
| model | `claude-haiku-4-5-20251001` | `cad_experimental.config` / every arena's recorded metadata |
| prompt version | **`2026-09-25.1`** | `prompt.PROMPT_VERSION` |
| prompt fingerprint | **`f265d7d1e279e95a04a5ac09343cef387a0688a7732f90d60a7362a271299675`** | SHA-256 of `prompt.system_prompt()` |
| prompt length | **34036** characters | `len(prompt.system_prompt())` |
| encoding sent | **`strict_selector_union_part`** | `generation.PLAN_SCHEMA_NAME` |
| encoding fingerprint | **`ef7427700af93ed7106a14863529cc9db81fe0ced26b61c84567a7a0a109247f`** | SHA-256 of the canonical schema JSON |
| encoding size | **3874** inlined characters, **6** branches | 3874 is the project's recorded inlined metric (Stage 75); the branch count was re-read here |

Vocabulary, also re-read: `OPERATION_TYPES` **11**, `DECLARATION_TYPES`
`('part',)`, `MAX_BODIES` **8**.

**Do not change any of these without treating it as its own measured stage.**

---

## 3. What has actually been completed

**There is no Stage 78. Nothing has been started on it.** The latest completed
stage is **Stage 77**, finished and pushed at `c877e8c`.

### Stage 76 — the multi-body observation layer (`DETERMINISTIC`)

`docs/evaluation-baselines/stage76-observation/`. Built the per-body
measurement and export observer that the Stage 75 handoff named as the blocker
on a broader corpus. **No live model was called and none was configured**, on
purpose — everything it produced is `DETERMINISTIC` and says nothing whatever
about a model. Seven cases on both kernels: 7/7 geometry, 7/7 measurement, 7/7
export at rung `F:verified`, cross-readable both ways. 28 mutants, 28 caught.
Three product findings measured and deliberately not patched, plus one defect
in its own observer caught by the offline gate before any live call.

### Stage 77 — the broader live multi-body corpus (`MODEL_GENERATED`)

`docs/evaluation-baselines/stage77-multibody-corpus/`. **240 live calls**, 18
active cases over 9 geometric families, up to five bodies. A **measurement**
milestone: **no prompt arm was run and no prompt was changed.**

Six denominators, never pooled (`summarise` refuses a combined number):

| quantity | rate | 95 % CI |
|---|---|---|
| creation | 100/104 = **96.2 %** | 90.5–98.5 |
| edit, per turn | 77/88 = **87.5 %** | 79.0–92.9 |
| edit, per whole chain | 53/64 = **82.8 %** | 71.8–90.1 |
| refusal | 48/48 = **100 %** | 92.6–100 |
| measurement | 96/96 = **100 %** | 96.2–100 |
| aggregate | 96/96 = **100 %** | 96.2–100 |
| export | 104/104 = **100 %**, all rung `F:verified` | 96.4–100 |

Structured output 240/240, fenced 0/240. 232 calls graded across 216 attempts;
RF-04's 8 calls excluded as retired. Sixteen of eighteen cases are **8/8**.

Per-case strict rates, re-read from `results.json` at this commit:

```
CR-01 8/8   CR-02 8/8   CR-03 8/8   CR-04 8/8   CR-05 8/8
CR-06 28/32 CR-07 8/8   CR-08 8/8   CR-09 8/8   CR-10 8/8
ED-01 8/8   ED-02 21/32 ED-03 8/8   ED-04 8/8   ED-05 8/8
RF-01 8/8   RF-02 32/32 RF-03 8/8   RF-04 retired (8 attempts excluded)
```

**All fifteen creation and edit failures are ONE mechanism: an axis is not an
extent**, and the model fails *toward the extent*. ED-02's 11/11 failures are
byte-identical — they bore at x = 110, the cylinder's outer edge, where its
axis is at 100, giving the same wrong volume every time. CR-06's 4/4 put the
cylinder's axis at x = 60, the plate's edge, so half of it lies inside. Both
are arithmetic on a body **away from the origin**. Systematic in mechanism
(11/11 and 4/4 identical), stochastic in rate (34 % and 12.5 %).

All **177** claimed successes were rebuilt from the model's own raw text and
compared against the closed forms: CadQuery 2.8.0 **177/177**, FreeCAD 1.0.0
**177/177**, agreeing body for body.

---

## 4. Live experiments already performed — do not repeat them

| run | calls | where the raw output lives |
|---|--:|---|
| Stage 77 live baseline + widened cases | **240** (232 graded, 8 retired) | `docs/evaluation-baselines/stage77-multibody-corpus/baseline.json`, `widened.json` |
| Stage 77 browser run `npm run e2e:live` | 8 steps, one attempt each | `apps/web-experimental/e2e/multibody-live.mjs` — evidence the product path carries a live multi-body answer end to end, **not a rate** |
| Stage 76 | **none** — no model called, none configured | `offline-cadquery.json`, `offline-freecad.json` (both `DETERMINISTIC`) |

Earlier stages' live runs (Stage 75 Phases A–E, Stages 63–70) are recorded
under `docs/evaluation-baselines/` and are **immutable**. Do not re-run them to
"confirm" a number; do not merge their numbers with Stage 77's.

---

## 5. What remains, and what is unverified

Nothing here has been measured. None of it may be reported as done.

1. **The axis-versus-extent prompt experiment is DESIGNED, NOT RUN.** No arm
   text exists, no adoption rule has been committed for it, and no calls have
   been spent on it. This is the next milestone.
2. **The HTTP app never bridges the credential.** Every arena calls
   `config.bridge_credential()`; `cad_experimental/app.py` does not. With the
   key under `CAD_ANTHROPIC_API_KEY` — how Claude Code Web supplies it — the
   experimental server cannot reach a model at all unless the operator exports
   `ANTHROPIC_API_KEY` by hand. **Recorded, deliberately not fixed** (changing
   the product mid-measurement would have meant the browser run tested
   something other than what the corpus measured). The fix is one line in
   `app_from_environment`.
3. **A local-model comparison has never been run.** `evaluate77` and
   `ground_truth77` import no vendor SDK, so the corpus is provider-neutral and
   this needs an adapter and a second column — but **nothing is known about any
   provider other than `claude-haiku-4-5-20251001`.**
4. **Identity binding in a STEP file is never proven, at any rung.** Level F
   proves each id reached the file as a `PRODUCT` and that the volume multiset
   is right. Which solid carries which name is not proven.
5. **`cad_backend.verify_assembly`'s body-name check is a substring scan** and
   is weak — measured accepting `SOLID`, `part`, `Open` and `cub` for `cube`.
   Not fixed; tightening it would fail `test_step_assembly.py` as written.
6. **`npm run e2e:cases` is broken**, and was before Stage 71 — it waits for
   `#description`, an element the page has not had since `641106f`. Not a
   regression, not fixed.
7. **STL still refuses a multi-body part**, pending an explicit recorded choice
   between one file per body and one multi-solid file.
8. **R2's 70.8 %** (Stage 75 Phase E) is a number about **that case**, not about
   the prompt — Stage 77's RF-02 is the same shape of request on a different
   fixture and scores 32/32, p = 0.0005. Carry neither as "the" clarification
   rate.

`CLAUDE.md` §19 carries the full limitations list; this is the short form.

---

## 6. The exact next task

**Run the axis-versus-extent prompt experiment**, as a measurement with a
protocol — not as an edit.

- **Cases:** `ED-02` (baseline **21/32**) and `CR-06` (baseline **28/32**).
- **Hypothesis:** four stages have now found that *what the model imitates is
  what the prompt SHOWS*. Every worked example in the prompt places a cylinder
  at the origin, or at a coordinate that is also its axis — so the prompt has
  never shown the model a body whose axis and extent differ.
- **Protocol, the way Stages 69, 70 and 75 were run:** a pre-registered
  adoption rule **committed before any arm text exists**; one variable per arm;
  an exploratory sample; a **fresh** confirmation run; and same-session controls
  on creation, edit and refusal so a fix for one dimension cannot quietly cost
  another.
- **Never** edit a ground-truth expectation after seeing a score.

Commands (all offline ones call no model):

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:docs/evaluation-baselines/stage76-observation:docs/evaluation-baselines/stage77-multibody-corpus
export CAD_FREECAD_HOME=/root/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib      # BEFORE python starts

cd docs/evaluation-baselines/stage77-multibody-corpus
python3 selfcheck77.py            # 18/18 reference turns, on every engine
python3 mutation_test_77.py       # 33 mutants
python3 arena77.py --check        # identity, case count, call count

# LIVE. Spends real provider calls; --live is REQUIRED.
python3 arena77.py --live --calls 8 --out <new-arm>.json
```

Two smaller things, **in order after it**: the one-line credential bridge in
`app_from_environment` (item 2 above), then the local-model comparison
(item 3).

---

## 7. Do not repeat completed work

- **Do not re-run Stage 76's observer sweep or Stage 77's baseline** to confirm
  them. Both are recorded, mutation-tested and pushed. Re-running spends real
  provider calls for a number that already exists.
- **Do not rebuild the observer.** `observe76` / `evaluate76` are complete and
  Stage 77 reuses `EV76.export_level` unchanged.
- **Do not re-derive the corpus.** `ground_truth77` is an immutable instrument:
  truth takes a case NAME and a turn INDEX and the module imports only `math`
  and `typing`. Editing a request text makes a NEW case with a new name; it
  never edits an existing one.
- **Do not re-open the prompt-tuning work on P11 or on the bore centre.** Both
  were measured to a stop, with their residuals bounded and failing closed.
- **Do not restart from an earlier stage.** Stages 32–77 are complete.
- **Do not treat a deterministic result as evidence about a model.** The five
  labels — `MODEL_GENERATED`, `DETERMINISTIC`, `FALLBACK`, `REFUSED`,
  `PROVIDER_ERROR` — are kept distinct everywhere, and every multi-body number
  recorded before Stage 77 is `DETERMINISTIC`.

---

## 8. Test state, as recorded at Stage 77

Not re-run for this checkpoint, because no code changed. Measured at
Stage 77 on Linux with FreeCAD 1.0.0 present and `CAD_FREECAD_HOME` /
`LD_LIBRARY_PATH` exported:

- `tests_experimental` — **2250 passed, 5 skipped, 0 failed** (2255 collected)
- `cad-core` — **1481 passed**
- frontend `tsc --noEmit` clean, `vite build` succeeds

The skip count is environment-dependent: 5 here, 72 when FreeCAD is absent from
the interpreter. Run the **whole** experimental suite before finishing a stage —
package-wide guard tests in older modules are routinely tripped by newer ones,
and focused subsets have missed that twice.
