# Stage 75 Phase E — the R2 tail: characterised, not removed

**192 live calls** to `claude-haiku-4-5-20251001`. Phases A–D are untouched.

Phase D left R2 at 13/16 on a sample whose interval ran from 0.57 to 0.93,
and said in its own record that the three remaining failures were
unexplained. Phase E measured them, proved the instrument that measures
them, ran one candidate arm against the mechanism it found and one control
against the candidate — and **adopted nothing**.

| | |
|---|--:|
| fresh R2 on the committed prompt | **34/48 = 70.8 %**  [56.8, 81.8] |
| E1, the candidate | 37/48 = 77.1 % — **p = 0.64, REJECTED** |
| E2, the control | **9/48 = 18.8 %** — p < 1e-6, a 52-point collapse |
| prompt | **unchanged at `2026-09-25.1`** |

---

## 1 — the residual, measured

48 fresh calls, exact committed instrument (`2026-09-25.1` /
`f265d7d1e279e95a` / 34036; `strict_selector_union_part` / 3874), identity
MATCHES, no re-prompting, no repair, no deterministic answer counted.

| | 48 calls |
|---|--:|
| refused | 48/48 |
| `questions` field present | 48/48 |
| asked at least one question | 48/48 |
| both bodies named | 48/48 |
| **the user's noun addressed** | **34/48** |
| operations emitted | 0/48 |
| built anything | 0/48 |
| labels | `{REFUSED: 48}` |
| structured output / fenced | 48/48 / 0 |

Phase D's 13/16 and this 34/48 are the same quantity: 13/16 against 34/48 is
**p = 0.52**, no evidence the runs differ. Pooled over the committed prompt,
**47/64 = 73.4 %**. The 48-call contiguous run is the number to carry.

## 2 — the residual is ONE mechanism, and it is mechanism C

Not A. The failures are not degraded successes; they are a different,
internally complete answer, and nine of the fourteen are byte-identical.

The discriminator is the **diagnosis** each reply states, and the split is
total:

| | |
|---|--:|
| **FAIL** — *"the request does not say / specify / name **which body**"* | **14/14** |
| **PASS** — *"the request **names a body this part does not have**"* | **34/34** |

Not one failure mentions `bracket` in any form — not the token, not a
paraphrase, not a negation. Not D (naming is 48/48), not E (the question is
well-formed in both), not B in isolation (the missing noun is a symptom of
the diagnosis, not an independent slip).

**The prompt hands the model the failing sentence.** `# Several bodies`
opens its first rule with

```
When a later request does not say WHICH body it means, do not choose one.
```

which is, nearly verbatim, what all fourteen failures write back. Read
literally, a request naming a body that does not exist *does* fail to say
which body it means — so **both rules' triggers match R2**, and the first is
stated first. Stage 65's and Stage 66's mechanism a fourth time: an
affordance the prompt supplies, not a gap in it.

## 3 — the instrument, proved before the conclusion

`test_r2_instrument.py`, 18 tests, **12 mutants, 12 caught**. **No defect was
found, so nothing was re-graded and the evaluator is unchanged.**

| the brief asks | how it is answered |
|---|---|
| does the noun check read MODEL text? | the noun is put ONLY in `system_error` — the exact crossing Phase A made for the body names, which nothing had pinned for this criterion — and the case must still fail |
| does `named_the_bodies`? | same, and it was already pinned; kept for the pair |
| can it ever pass? | yes, from `summary` OR from `questions`; a check that cannot pass is not a check |
| can a refusal pass by accident? | one good reply and five single-change mutations of it: not declining, asking nothing, naming one body, carrying operations, building geometry |
| is the operation count from raw output? | the parser rejects such a plan, so the PARSED count is 0 exactly when the model wrote most; the check reads the raw answer |
| can a fallback be MODEL_GENERATED? | `outcome_label` fails closed on all seven values, `FALLBACK`/`DETERMINISTIC` are outside `COUNTS_AS_SUCCESS`, and the generation layer the arena calls imports **no** deterministic route |
| does it hold on real data? | the 48 recorded attempts are **re-graded** and the noun check partitions them exactly: no false negative, no false positive |

Two mutants missed on the first sweep, and both were weaknesses in the
**tests**, not the grader: one read recorded booleans instead of re-grading,
so no grader mutation could reach it; and one AST walk read only
`node.module`, which misses `from . import normalize` — the plainest way to
add the route it forbids.

## 4 — the arm, and the control that is the finding

One variable: the first rule's trigger clause. Nothing added, moved or
exemplified — the residual is a rule-SELECTION failure and the reply shape
is already right in both templates, so an example could not help. Phase D
measured that directly: a worked reply envelope moved 0 of 64.

| arm | the clause | chars | R2 | vs baseline |
|---|---|--:|--:|---|
| **E0** committed | *does not say WHICH body it means* | 34036 | **34/48** | |
| **E1** candidate | *uses no name at all* | −13 | 37/48 | p = **0.64** |
| **E2** CONTROL | *leaves WHICH body unclear* | −7 | **9/48** | p < **1e-6** |

**E2 is the result.** It was built to be inert — the same sentence, touched,
with the affordance intact — and instead it took R2 from 70.8 % to 18.8 %.
E1 against E2 is p = 1.4e-8.

So the mechanism is established beyond argument: **which of the two rules
the model applies is driven by how the first rule's trigger reads**, and the
committed wording sits near a local optimum. The edit that should help
helped by six points and did not reach significance; the edit that merely
rephrased the same idea destroyed the case.

This is the fifth stage in which prose about a rule moved nothing and the
second in which touching the wrong sentence moved a great deal (Stage 70's
B3 was the first, at p = 0.0001).

## 5 — the decision: nothing adopted

`decision_rule_e.py` was committed in `a80b47a`, **before either arm's text
existed**. It does not restate Phase D's rule; it imports it, and changes
exactly one thing — `MIN_N` 16 → 32, with an assertion that the floor may
only rise. Every other threshold is Phase D's, imported by name.

```
ADOPT: False
  - R2 strict: 37/48 vs 34/48, p=0.6424, gain=+0.062
  - R2 did not improve significantly (p=0.6424)
  - R2 gain +0.062 is below MIN_GAIN=0.25
```

No confirmation battery was run. A candidate that fails the significance
clause on 48 calls cannot be rescued by measuring the preservation clauses,
and separating 77 % from 71 % at p < 0.05 needs several hundred calls per
arm. Stage 70 stopped at exactly this point for exactly this reason, and
adding prompt text on a non-significant reading is how a 34 000-character
prompt becomes a 37 000-character one that measures the same.

**No trade was available to hide, either:** `refused`, `named_the_bodies`,
`asked_a_question`, `emitted_no_operations` and `built_nothing` are 48/48 on
both the baseline and E1.

## 6 — what is pinned, so the finding is not lost

`TheTriggerSentenceIsLoadBearingTests` asserts the committed clause is the
one that was measured, that **neither rejected rewrite is in the prompt**,
and that the two rules still read as a pair — because if one goes missing
there is nothing to choose between and the measurement stops describing the
prompt. It deliberately does **not** claim the clause is optimal; nothing
measured says that. It claims it is the one with a number attached, so a
future rewrite has to be a measurement rather than an edit.

Three mutants cover it — the rejected candidate, the harmful control, and
deleting the second rule. Phase D's sweep is now **26 mutants, 26 caught**.

## 7 — the files

| file | what |
|---|---|
| `decision_rule_e.py` | the rule, committed before either arm existed; Phase D's, with MIN_N raised |
| `variants_e.py` | the candidate and the control, pure functions of the prompt |
| `arena75e.py` | Phase D's runner with Phase E's arms; copies nothing |
| `characterise_e.py` | the per-attempt facts and the failure grouping; defines no criterion |
| `mutation_test_e.py` | 12 mutants over the grader, 12 caught |
| `residual-r2.json` | 48 calls, committed prompt |
| `arm-E1-…json` / `arm-E2-…json` | 48 calls each |
| `characterisation.txt` | the characterisation as it was printed |
| `corpus-design.md` | the broader multi-body corpus, DESIGNED and not run |

## 8 — what is NOT fixed

- **R2 is 34/48 = 70.8 % and Phase E did not move it.** The cause is known
  and demonstrated; no compact edit to the sentence that causes it produced
  a significant improvement, and the obvious rephrasing made it far worse.
- **The residual fails closed.** Every one of the 14 failures still refuses,
  names both bodies, asks a real question and builds nothing. What it gets
  wrong is the diagnosis it states — a worse answer, never a wrong edit.
- **Reordering the two rules was not tried**, deliberately: Stage 70's B3
  showed order is a large effect in its own right, so it is a second
  variable and would need its own arm and its own control.
- **Every number here is `MODEL_GENERATED` or `REFUSED` from a real
  provider call.** No deterministic result is counted anywhere, and no run
  was repaired, retried or re-prompted.
