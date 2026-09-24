# Stage 75 Phase C — clarification, measured and then fixed

**432 live calls** to `claude-haiku-4-5-20251001`. Phase A and Phase B are
untouched; this directory is Phase C's own.

Phase B left the creation path strong and the clarification path weak. The
brief asked for two metrics reported apart — **body naming** and **no
operations** — a fresh baseline on the unchanged prompt, controlled arms one
variable at a time, and an adoption rule fixed before any arm was seen.

One of those two metrics could not be computed the committed way. That is the
first finding and it had to be fixed before the baseline meant anything.

---

## 1 — the instrument was measuring the validator

`checks["emitted_no_operations"]` read `observation["operation_count"]`, which
counts operations in the **parsed** plan. A clarification carrying operations
never becomes a parsed plan: `parser.py` raises for any non-generated plan
with operations, `generation` returns `plan=None`, and the observer records
zero. So the check said *"the model emitted no operations"* **precisely when
the model emitted the most**.

Measured across every recorded refusal attempt in Phases A and B:

| | |
|---|--:|
| times the check was `False` | **0** |
| times the model actually shipped operations | **5** |

It was a guard that could not fire. Its unit test built a stand-in `Plan`
that bypasses the parser, so it passed while proving nothing about the live
route — this project's own *"a test that passes without proving its name"*,
in a new place.

`_operations_the_model_wrote` now reads the model's **own raw answer**,
falling back to the parsed plan when there is no raw answer to read: absent
is not zero, the same distinction `questions` already makes.

**Phase B stays comparable.** Re-graded with the corrected evaluator: **5 of
24** metric values corrected, **0** strict verdicts moved. The five rows it
fixes already failed, on `provider_output_valid`. The metric was wrong; the
verdict was not.

### and the rule now comes from the truth

`operations_permitted` has been on every refusal case since Phase B — R2's
own retirement note says it was added *"making that failure visible"* — and
**nothing in the repository read it**. The corpus declared the rule and the
grader kept its own hard-coded copy. They happened to agree, which is why
nobody noticed, and is exactly the condition under which a corpus edit
silently fails to take effect.

### a second defect, found while reading arm output

`outcome_label` named every outcome it did not recognise `MODEL_GENERATED`,
by falling through: `PlanOutcome` has five members and three were listed, so
`model_error` was labelled as though the model had produced a part. It never
reached a success rate — labels sit beside `strict_success`, never inside it
— but `MODEL_GENERATED` is the one label this project lets into a quality
number. It now fails closed. One label changed across 360 recorded attempts;
no verdict moved.

---

## 2 — the fresh baseline, prompt and schema unchanged

72 live calls, 24 per refusal case, one contiguous run.
`identity: MATCHES the committed instrument`; 72/72 structured output,
0 fenced.

| | n=72 | 95% CI |
|---|--:|---|
| strict success | **11/72 = 15.3 %** | 8.8 – 25.3 |
| **Metric A** — named BOTH bodies | **19/72 = 26.4 %** | 17.6 – 37.6 |
| Metric A — named ONE | 11/72 | |
| Metric A — named NEITHER | **42/72 = 58.3 %** | 46.8 – 69.0 |
| **Metric B** — no operations | **49/72 = 68.1 %** | 56.6 – 77.7 |
| Metric B — shipped operations | **23/72 = 31.9 %** | 22.3 – 43.4 |
| asked ≥ 1 question | **31/72 = 43.1 %** | 32.3 – 54.6 |
| built nothing | **72/72** | 94.9 – 100 |

Against Phase B's 7/24 the strict rate is p = 0.14 — **no evidence the two
runs differ**; this one simply has three times the calls.

**Metric B is not comparable to Phase B on its own axis.** Phase B's recorded
`emitted_no_operations` was always `True`, for the reason in §1. That is an
observer change, the same class Phase B itself had to make, so this record
quotes the corrected value rather than a delta.

### what the failure actually looked like

The brief predicted "often fails to name the bodies" and "some responses emit
operations". Both are real. The larger one it did not name: **half the
clarifications asked nothing at all** — `needs_clarification` with an empty
`questions` list and a shrug in `summary`:

```
questions=[]   summary='ambiguous request'
questions=[]   summary='ambiguous which body should be drilled'
```

---

## 3 — the arms, one variable each

`variants_c.py`. No arm edits `prompt.py`; `arena75c.py` patches
`system_prompt` for one run and records which text was used, and reads the
live route's own binding back to prove the patch took.

| arm | what changed | chars | n | strict | A: both | B: none | asked |
|---|---|--:|--:|--:|--:|--:|--:|
| **C0** baseline | nothing | 33407 | 72 | 11/72 | 19/72 | 49/72 | 31/72 |
| **CC** structural rule | the `unsupported` section's own clause, copied into the clarification section | +36 | 48 | 11/48 | 17/48 | 41/48 | 21/48 |
| **CB** no-operations example | a whole reply envelope, question naming **neither** body | +307 | 48 | 31/48 | 45/48 | 48/48 | 48/48 |
| **CA** naming example | the same envelope, question naming **both** | +352 | 48 | 31/48 | 47/48 | 48/48 | 47/48 |
| **CA confirmation** | — | +352 | **72** | **48/72** | **72/72** | **72/72** | **72/72** |

### the gap the arms were built against

Measured on the committed text: `"status"` occurred **exactly once** in
33407 characters, and so did `"questions"`, `"summary"` and `"operations"` —
all four only in the bare field list. Every one of the prompt's ~20 JSON
blocks was a bare operation object. **The model had been told the fields of a
reply and never shown one.**

There was also an asymmetry inside the prompt: `# When to say unsupported`
has always carried *"— with an empty operations list —"* in its own opening
sentence; `# When to say needs_clarification` carried no such clause and did
not mention bodies at all.

### three results the design bought, none of them guessable

**CC is the control, and it behaved as four prior stages predict.** Prose
moved operations a little (41/48, p = 0.034) and naming not at all (17/48,
p = 0.31). Prose about a rule still moves nothing.

**CB differs from CA in exactly one string** — its example's question names
neither body — and scores the same: 45/48 against 47/48, **p = 0.62**. So the
naming wording is **not** the active ingredient. *Showing an envelope at all*
is. The prose rule telling the model to list the bodies was already there, in
`# Several bodies`, and was correct and inert; the example gave it somewhere
to land. That is the fifth time this project has measured *what the model
imitates is what the prompt shows* — and the first time the thing being
imitated is the shape of the reply rather than the geometry.

**R2 is 0/24 before and 0/24 after, and that is not "no change".**

| R2 failing checks | n | |
|---|--:|---|
| baseline | 24 | `provider_output_valid` 3, `refused` 3, `named_the_bodies` 16, `asked_a_question` 7, `emitted_no_operations` 3, `question_addressed_the_request` 24 |
| CA | 16 | `question_addressed_the_request` 16 |

Six failure modes became one. Every remaining failure is the criterion R2
alone pins: echoing the user's own noun, `bracket`. **Collapsing the two
metrics into one score would have reported R2 as untouched** — which is the
brief's reason for keeping them apart, demonstrated.

---

## 4 — the pre-registered rule

`decision_rule_c.py`, committed **before any arm's prompt text was drafted**.
It takes counts only; there is no parameter an arm's wording, a transcript or
a plan could enter by. Stage 67 adopted on one favourable reading and had to
revert; Stage 70 answered that by fixing its rule first, and the rule then
rejected all four candidates including one that opened 32/32.

Seven clauses: enough calls; a significant move on at least one metric; no
trade against the other; refusal correctness preserved with `built_nothing`
**perfect**; creation preserved above `CREATION_FLOOR`; body-identity codes
not risen; and **an exploratory pass is never an adoption**.

It refused CA, CB and CC on the exploratory samples — `passes on an
EXPLORATORY sample; a fresh confirmation of at least 16 per case is required`
— which is the clause doing its job.

On the confirmation sample, with the same-session creation control:

```
ADOPT: True   moved: ['naming_both', 'operations_none']
  - naming_both:     72/72 vs 19/72, p=0.0000
  - operations_none: 72/72 vs 49/72, p=0.0000
```

### creation did not pay for it

Measured in the same session rather than against an older run, because a
number taken under different conditions is a different number:

| | creation | M1 | M2 | M3 | M5 | N2 | N3 |
|---|--:|--:|--:|--:|--:|--:|--:|
| C0 control | 43/48 | 8/8 | 8/8 | 8/8 | 4/8 | 8/8 | 7/8 |
| CA adopted | **44/48** | 8/8 | 8/8 | 8/8 | 6/8 | 7/8 | 7/8 |

Identity codes identical: `D:wrong_target` 1, `E:cross_body_edit` 1 in both.

### CD was not run

Not taste, arithmetic. CA's confirmation is **72/72 on both metrics**.
Nothing can be significantly better than 72/72, so no further arm — CD
included — can satisfy clause 2 against it. Running it could not change the
decision. The module docstring records, before any arm was run, that the
brief's "A+B" is degenerate under this design (CA's envelope already contains
CB's `operations: []`), so CD was defined as CA+CC.

---

## 5 — what was adopted

Prompt **`2026-09-24.2`** / `90ebab2c38d615fb` / **33759** characters.

Verified against the **recorded** arm fingerprint in `arm-CA-naming-example.json`
and `confirm-CA.json`, not against the generator that produced it — those files
are evidence and the generator is code that could drift.

Four pins moved together: `PROMPT_VERSION`, the canonical pin in
`test_extrude_revolve`, the multi-body pin, and `arena75`'s own identity
guard.

---

## 6 — the files

| file | what |
|---|---|
| `decision_rule_c.py` | the adoption rule, committed first |
| `variants_c.py` | the four arms, pure functions of the baseline, idempotent after adoption |
| `arena75c.py` | `arena75` with one arm patched in; adds no criterion of its own |
| `analyse_c.py` | reads the runs, applies the rule; has no threshold of its own |
| `baseline-c.json` | 72 calls, unchanged prompt |
| `arm-C*.json` | 48 calls each |
| `confirm-CA.json` | 72 calls, fresh sample |
| `creation-CA.json` / `creation-C0.json` | 48 each, the regression and its control |

---

## 7 — what is NOT fixed

- **R2 remains 0.** A clarification that names both bodies but never says the
  user's own noun matched nothing is still wrong, and it is the only
  clarification failure left. Its criterion predates Phase C and was not
  touched: *never edit an expectation after seeing a score.* This is the next
  measurable thing.
- **Metric B's baseline is not comparable to Phase B's** on its own axis, for
  the observer reason in §1.
- **One R3 call in 16 still named only one body** on the CA arm, though none
  did on the 72-call confirmation.
- **Nothing here says anything about creation quality beyond the 96 calls in
  §4**, which are a regression check and not a creation baseline.
- **Every number in this directory is `MODEL_GENERATED` or `REFUSED` from a
  real provider call.** No deterministic result is counted anywhere, and no
  run was repaired, retried or re-prompted.
