# Stage 78 — axis versus extent, measured

Stage 77 ran 240 live calls and found **one mechanism behind all fifteen**
of its creation and edit failures: the model writes a **boundary**
coordinate where a **centreline** coordinate is required, and it fails
*toward the extent*. Stage 78 is the controlled prompt experiment on that
mechanism.

Stage 77's artifacts are **untouched**. `ground_truth77` and `evaluate77`
are reused unmodified; this directory adds a narrower truth over two cases
and layers it on top.

---

## The mechanism, recomputed from the recorded bytes

Not recalled — recomputed, twice, from the raw plans.

**ED-02.** The fixture is deterministic and is built and verified before any
model call: a d20 × 30 cylinder whose `position` is `(100, 20, 0)`. A
cylinder's position **is** its axis, so:

| | |
|---|---|
| axis (centreline) | x = **100** |
| minimum extent | x = 90 |
| **maximum extent** | x = **110** |
| geometric centre | (100, 20, 15) |

The request is *"a hole all the way through the cylinder along its axis"*.
Stage 77: **11 of 11 failures bored at x = 110** — the outer face.
Stage 78's fresh baseline: **17 of 18**.

> The Stage 77 handoff calls those eleven "byte-identical". They are not:
> there are six distinct SHA-256 prefixes among the raw texts. The **defect**
> is identical — every one chose 110.0 — and that is the claim that holds.

**CR-06.** A creation case: three bodies, *"beside"* each other, no
placement stated. Stage 77: **4 of 4** failures put a d16 cylinder's axis at
x = 60, the plate's max extent, so it spanned 52…68 and lay half inside.
Stage 78's fresh baseline: **6 of 6**.

**23 of 24 fresh failures are this one mechanism.**

## Why the prompt produces it

The committed prompt states the distinction in prose **exactly once** —
a cylinder's position is *"The CENTRE of the base circle"*, a box's is
*"The MINIMUM corner"* — and then **every worked example in 34 036
characters is arranged so the two are indistinguishable**:

- every body that is **drilled** sits at the origin with no `position`, so
  `size/2` and `(min+max)/2` are the same arithmetic;
- every along-axis coordinate shown is literally `0`, which for an origin
  body is its minimum boundary;
- the one body offset in space (a post at x = 80) is a cylinder whose
  position *is* an axis — but its extents are never stated and it is
  **never bored**, so the difference is never made visible.

The prompt has never shown the model a drilled body whose axis and extents
are different numbers.

## The instrument

| file | what it is |
|---|---|
| `ground_truth78.py` | the axis truth. `axis_truth()` takes a case **name**; imports `math` and `typing` only |
| `evaluate78.py` | the axis criterion, layered over Stage 77's verdict |
| `regrade78.py` | **the gate**: re-grades Stage 77's own recorded calls offline |
| `arena78.py` | runs arms interleaved; reuses `arena77.run` unmodified |
| `variants78.py` | the arm texts |
| `decision_rule_78.py` | the adoption rule, committed before any arm existed |
| `regression78.py` | the focused regression sample |
| `score78.py` | applies the rule to a recorded run, offline |
| `mutation_test_78.py` | 23 mutants |

**Absolute where the request fixes it, relational where it does not.**
ED-02's fixture is deterministic and its request says "along its axis", so
exactly one centreline is correct and the corpus names it. CR-06 states no
placement at all, so **none is pinned** — what is pinned is that the bodies
stand apart. Pinning a coordinate CR-06 never states is precisely what
retired M4 and M8 in Stage 75.

**The layering.** `stage78_strict == stage77_strict AND axis_ok`, so a case
can never score higher here than in Stage 77, and an arm that "improved" a
case by breaking something Stage 77 checks is caught rather than rewarded.

**The gate.** Re-grading Stage 77's recorded bytes reproduces **ED-02 21/32
and CR-06 28/32 exactly**, with all fifteen failures carrying a named
mechanism. Until that held, no live call was spent.

## Three defects in this stage's own instrument

All three were caught before or without spending calls on a wrong number,
and all three are now mutants.

1. **`P:export_identity_unproven` read as fatal.** Stage 77 records it as
   informational and excludes it from `FAILURE_CODES`. Treating it as a
   failure scored **all 32** of CR-06's calls as losses. Caught by the
   offline gate.
2. **An axis merely *sharing* a coordinate flagged as a defect.** Two bodies
   side by side in x legitimately share a y coordinate; the unqualified test
   scored **five correct** Stage 77 calls as failures. The finding is now a
   *diagnosis* of an actual overlap, not a criterion. Caught by the offline
   gate.
3. **An omitted `axis` read as a wrong direction.** The field is optional
   and the prompt states the default is `+Z`. Two **perfect** S1 answers —
   bored at x = 100, y = 20, the pin's own axis — were scored as
   `X5:wrong_direction`, *in the arm the stage was about to recommend*.
   Caught by inspecting the exploration before trusting it; re-graded
   offline from preserved bytes, no new calls.

A fourth, smaller one: the mutation sweep showed a guard in `observe_cr06`
was **redundant** — no test could tell it from the one beside it — so it was
removed rather than left as decoration.

## The baseline moved, and that is why controls are not ceremonial

The same committed prompt, re-run:

| case | Stage 77 (recorded) | Stage 78 (fresh) | p |
|---|---|---|---|
| ED-02 | 21/32 = 0.656 | **14/32 = 0.438** | 0.13 |
| CR-06 | 28/32 = 0.875 | **26/32 = 0.812** | 0.73 |

Neither difference is distinguishable from sampling, so **no drift is
claimed**. But a −0.219 swing on the same prompt is exactly the size that
could be mistaken for an arm's effect. Every arm here is therefore compared
against a baseline **interleaved with it in the same session**, never
against the historical number.

## The rule, fixed before any arm text existed

Committed at `316c60c`, on a tree where `variants78.CANDIDATES` is the empty
tuple — checkable with `git show`.

| | |
|---|---|
| primary metric | `stage78_strict`, per case, **never pooled** |
| exploration | n = 32 per arm per case; significance **not** a gate |
| confirmation | n = **128**, fresh |
| minimum improvement | +0.125 |
| significance | Fisher exact, two sided, α = 0.05 |
| mechanism | must at least **halve** |
| regression | nothing in `MUST_PRESERVE` broken; nothing at 100 % moved off it |

**A defect in the rule's own first draft, fixed before any arm existed:** it
demanded +0.125 *and* p < 0.05 at n = 32, and at that n nothing below +0.250
can satisfy both. The confirmation n is **derived**, not chosen:

| n | smallest resolvable improvement |
|---|---|
| 32 | +0.250 |
| 48 | +0.208 |
| 64 | +0.172 |
| 96 | +0.146 |
| **128** | **+0.125** |

## The arms

All three insert at the same unique anchor — the end of `## cylinder`,
before `## through_hole` — and **nothing else is touched**; a test asserts
the prompt is byte-identical either side of the inserted block. No arm uses
corpus wording, and a test enforces that too.

| arm | Δ chars | what it is |
|---|---:|---|
| `S1-rule-only` | +487 | the semantic rule, in prose. **No example.** |
| `S2-worked-example` | +775 | one worked example: a shaft away from the origin, bored on its own axis |
| `S3-example-reordered` | +775 | S2's material, narration first. **The order control** — a test asserts it carries exactly S2's words |

S2 is the first place in the prompt where a drilled body's axis (70), its
minimum extent (58) and its maximum extent (82) appear as three different
numbers together.

## Exploration — 256 interleaved calls

| arm | ED-02 | CR-06 | ED-02 mechanism |
|---|---|---|---|
| S0-baseline | 13/32 = 0.406 | 25/32 = 0.781 | 18 |
| S1-rule-only | **32/32 = 1.000** | 29/32 = 0.906 | **0** |
| S2-worked-example | **32/32 = 1.000** | 28/32 = 0.875 | **0** |
| S3-example-reordered | **32/32 = 1.000** | 28/32 = 0.875 | **0** |

ED-02 **+0.594, p = 8.0 × 10⁻⁸**, mechanism **18 → 0**. CR-06 +0.09 to
+0.13, not significant at this n.

**The control is the finding.** S3 is a pure reordering of S2 and scores
identically — so unlike Stage 70, where a pure reordering regressed at
p = 0.0001, **order is not load-bearing here**. And S1, prose alone with no
example at +487 characters, matches both. The effective ingredient is the
semantic content, and the *minimal* form carries all of it.

All three were scored **INCONCLUSIVE** by the rule, correctly: they
qualified on exploratory calls, and adoption requires a fresh confirmation
sample.

## Confirmation — attempted, and NOT completed

768 calls were sent (3 arms × 2 cases × 128). The provider **rate-limited
hard**: 96 to 128 calls *per arm* came back empty, with no `stop_reason`.
A paced top-up of a further 576 calls returned **zero** measured calls, and
a direct probe gave the reason:

> `You have reached your specified workspace API usage limits. You will
> regain access on 2026-10-01 at 00:00 UTC.`

Scored on **measured calls only** — a rate-limited call is unmeasured, not
a failure:

| arm | case | strict | rate | mechanism | improvement | p |
|---|---|---|---|---|---|---|
| S0-baseline | ED-02 | 49/80 | 0.613 | 31 | — | — |
| S0-baseline | CR-06 | 53/80 | 0.662 | 27 | — | — |
| **S1-rule-only** | ED-02 | **80/80** | **1.000** | **0** | +0.387 | 2.5 × 10⁻¹¹ |
| **S1-rule-only** | CR-06 | **71/73** | **0.973** | 2 | +0.310 | 5.3 × 10⁻⁷ |
| S2-worked-example | ED-02 | 64/64 | 1.000 | 0 | +0.387 | 6.2 × 10⁻¹⁰ |
| S2-worked-example | CR-06 | 55/64 | 0.859 | 9 | +0.197 | 7.1 × 10⁻³ |

**Verdict: INCONCLUSIVE. The prompt is UNCHANGED.**

Every arm clears the improvement threshold, clears significance, and at
least halves the mechanism — on **64 to 80 measured calls against a
pre-registered floor of 128**. The rule requires the floor and the floor
was set *before* any arm text existed, precisely so that a large, pleasing
effect could not talk its way past a thin sample. It did not.

This is the rule working, not the rule failing. What is *not* claimed:
that S1 is adopted, that the defect is fixed, or that any of these rates
is the arm's rate.

## Kernel evidence

Every call recorded as a Stage 78 strict success, rebuilt from the model's
own raw text against the closed forms — **591 successes**:

| engine | matched | mismatched | failed |
|---|---|---|---|
| CadQuery 2.8.0 | **591** | 0 | 0 |
| FreeCAD 1.0.0 | **591** | 0 | 0 |

**Cross-kernel: 591/591 bit-identical, body for body.** Offline; no
provider calls.

## What the next session must do

1. **Complete the confirmation** after 2026-10-01 00:00 UTC. Needs, in
   *measured* calls, per arm per case: S1 ED-02 +48, CR-06 +55; S2 ED-02
   +64, CR-06 +64; baseline +48 each. **Pace it** — `--pace 6` was still
   too fast once the daily allowance was gone, and the allowance, not the
   rate, is what ran out.
2. **Do not pool exploration into confirmation.** The rule forbids it and
   it would still fall short.
3. **Run the regression gate** (`regression78.py`, 9 cases × 8 calls × 2
   arms = 144 calls). It has never been run — no candidate reached the
   point of needing it.
4. Only then apply the rule. If it says ADOPT, adopt **S1-rule-only**: it
   is the smallest change (+487 characters against S2's +775) and it
   matched or beat S2 on both cases at every sample size.

## The order control, and what it settled

S3 is a pure reordering of S2 — a test asserts it carries exactly S2's
words and is exactly S2's length — and at n = 32 it scored **identically**:
32/32 and 28/32. So **order is not load-bearing here**, unlike Stage 70
where a pure reordering regressed at p = 0.0001.

Taken with S1 (prose alone, no example) matching the worked example, the
exploratory reading is that **the semantic content is the whole of the
effect** and the minimal form carries it. That reading rests on 32 calls
per arm and is not confirmed.

---

## Re-verified 2026-09-29, and still INCONCLUSIVE

A later session resumed here and re-ran the hard start. Nothing about the
result changed; what follows is the record that it was checked rather than
assumed.

**The provider limit has not reset.** One minimal probe — four output
tokens, not a corpus case, not scored — returned the same refusal:

> `400 invalid_request_error: You have reached your specified workspace API
> usage limits. You will regain access on 2026-10-01 at 00:00 UTC.`

At the time of the probe (2026-09-29 12:29 UTC) that is about 35 hours
out. **No call was substituted, simulated or reinterpreted as a measured
call**, and the confirmation deficits in *What the next session must do*
stand exactly as written.

**The instrument still reproduces itself, offline:**

| check | result |
|---|---|
| `regrade78.py` (the gate) | ED-02 **21/32**, CR-06 **28/32** — AGREES with Stage 77 |
| `score78.py explore78.json` | S1/S2/S3 all ED-02 32/32, p = 8.0 × 10⁻⁸ — INCONCLUSIVE (exploratory) |
| `score78.py confirm78.json topup78.json --confirmation` | S1 80/80 and 71/73, S2 64/64 and 55/64 — **INCONCLUSIVE**, short of the floor of 128 |
| `mutation_test_78.py` | **26/26 killed**, every module restored byte-identical |
| committed prompt identity | `2026-09-25.1` / `f265d7d1…` / **34036** chars — unchanged |
| files Stage 78 touched | `git diff cad3325..a3e40d0` names **no** file under `apps/api/src/` and **no** Stage 77 artifact |

**One artifact was not actually preserved.** `confirm78.log` and
`topup78.log` are matched by `.gitignore`'s `*.log`, so the operator-side
consoles of the two live runs existed only inside an ephemeral container.
They are copied here as `confirm78-console.txt` and `topup78-console.txt`
and committed. They are the record that the top-up's 576 calls returned
**zero** measured answers; the `.log` originals remain ignored.
