# Stage 75 Phase D — R2, and the example that lost

**168 live calls** to `claude-haiku-4-5-20251001`. Phases A, B and C are
untouched; this directory is Phase D's own.

Phase C closed every clarification failure but one. R2 sends *"Make the
bracket 10 mm taller."* to a deterministic two-body fixture whose bodies are
`block` and `rod`; a correct answer declines, names both, asks a question,
writes nothing, **and says `bracket`** — the user's own word, which matches no
body. Phase C left it at 0 and named it the next measurable thing.

---

## 1 — the baseline, and why it is the finding

32 fresh calls on the exact committed instrument. No prompt change, no schema
change, nothing reused from an older count.

| | n=32 | 95 % CI |
|---|--:|---|
| strict | **0/32** | 0 – 10.7 |
| **class B** — declined, named BOTH, asked, no operations, never said `bracket` | **32/32** | 89.3 – 100 |
| classes A, C, D, E, F | **0 each** | |
| structured output / fenced | 32/32 / 0 | |

The taxonomy agrees with `evaluate75.grade` on all 32, and exactly one check
fails on every one of them: `question_addressed_the_request`.

**So the residual is not a refusal failure and not a naming failure.** Both
are already perfect, and have been since Phase C. It is a PRECISION failure,
and it is perfectly systematic — which is what justified an arm at all.

### what the prompt was doing

`# Several bodies` closes with a worked sentence of its own:

```
When a later request does not say WHICH body it means, do not choose one. Say
"needs_clarification" and ask, listing the bodies by name.
"Make it 10 mm taller" with a plate and a post standing names neither, and
guessing is worse than asking.
```

R2's request is **that sentence with a noun where the pronoun is**. Nothing
in 33759 characters addressed a name that matches nothing — searched for
`nonexistent`, `no such`, `unknown`, `not present`, `no solid named`,
`matched nothing`, `no body`; the one occurrence of "does not exist" is about
a plan-internal target id, in the union section.

The model pattern-matches R2 onto the pronoun rule and gives precisely the
answer that rule prescribes. It answers a different question, correctly. This
is **an affordance the prompt supplies**, not a gap in it — Stage 65's and
Stage 66's mechanism a third time.

---

## 2 — the arms, one variable each

| arm | what changed | chars | R2 |
|---|---|--:|--:|
| **D0** baseline | nothing | 33759 | 0/32 |
| **D1** missing-body example | a worked reply ENVELOPE for the case, appended to `# When to say needs_clarification` | +381 | **0/32** |
| **D3** the same example, moved | the SAME text, appended to `# Several bodies` | +381 | **0/32** |
| **D4** contrast | four lines of prose distinguishing the two cases, appended to `# Several bodies` | +277 | **23/32** |

D1's 32 replies are the baseline's, word for word.

### this is the first time in this project that an example lost

Five stages measured the opposite: what the model imitates is what the prompt
SHOWS, and prose about a rule moves nothing (65, 66, 69, 70, and Phase C for
the reply envelope). Here the envelope moved **0 of 64** across two sites and
prose moved it to 23/32.

The mechanism is specific, and it says why Phase C's lesson does not carry.
There the defect was the reply's SHAPE — the model had been told a reply's
fields and never shown one — and an example was exactly the missing thing.
Here the model already writes a perfect envelope; what it gets wrong is
**which case it is in**. An example of a different answer cannot say that. A
sentence contrasting the two can.

### D3 is the control, and it is a strong one

D3 is byte-identical text to D1 at D4's site: same section, **more** added
characters than D4 (+381 against +277), and 0/32. "Any addition at that place
helps" is ruled out by measurement rather than by argument. D1 against D3
isolates location at fixed text; D3 against D4 isolates form at a fixed site.

### the arm that was not run

D2 exists in `variants_d.py` and was never run. It is the control that would
have decomposed D1's effect — the same envelope with the missing name removed
from its question. D1 had no effect to decompose, so the calls would have
measured nothing. Phase C declined CD for the same kind of reason and said so.

---

## 3 — the pre-registered rule

`decision_rule_d.py`, committed in `209f6fc` **before any arm text existed**,
and it takes counts only. Eight clauses: enough calls; R2 up significantly
AND materially; no trade against the four checks R2 already passes; the
sibling refusal cases hold; creation holds per case and as a group; the
single-body golden holds; no rise in the wrong-body (C/D/E) or wrong-target
(P11/P12) codes; and an exploratory pass is never an adoption.

It refused D1 and D3 for moving nothing, and refused D4 on its exploratory
sample — *"a fresh confirmation of at least 16 calls on R2 is required"*.

### the confirmation, with same-session controls throughout

Every control was measured in the same session as its arm. Stage 70's
recorded 44/48 on the golden request was taken under prompt `2026-09-18.5`
and is not quoted as a baseline.

| | control (D0) | arm (D4) |
|---|--:|--:|
| **R2 strict** | **0/16** | **13/16** |
| R2 `refused` | 16/16 | 16/16 |
| R2 `named_the_bodies` | 16/16 | 16/16 |
| R2 `asked_a_question` | 16/16 | 16/16 |
| R2 `emitted_no_operations` | 16/16 | 16/16 |
| R2 `built_nothing` | 16/16 | 16/16 |
| R1 | 16/16 | 16/16 |
| R3 | 16/16 | 15/16 |
| creation M1 / M2 / M3 | 8/8 each | 8/8 each |
| creation M5 | 8/8 | 7/8 |
| creation N2 | 6/8 | **8/8** |
| creation N3 | 6/8 | **8/8** |
| creation, as a group | 44/48 | **47/48** |
| single-body golden (EXPLICIT) | 22/24 | **23/24** |
| golden thickness / plate count | 24/24 / 24/24 | 24/24 / 24/24 |
| `D:wrong_target`, `E:cross_body_edit` | 2, 2 | **0, 0** |
| P11 | 2 | **1** |

```
ADOPT: True   p=0.000003   gain=+0.812
  - R2 strict: 13/16 vs 0/16, p=0.0000, gain=+0.812
  - creation group: 47/48 vs 44/48
  - single-body golden: 23/24 vs 22/24
```

---

## 4 — what was adopted

Prompt **`2026-09-25.1`** / `f265d7d1e279e95a` / **34036** characters. The
encoding is unchanged: `strict_selector_union_part` / 3874 /
`ef7427700af93ed7`.

Verified against the **recorded** arm fingerprint in
`confirm-refusal-D4.json`, not against the generator that produced it — the
run file is evidence and the generator is code that can drift. Four pins
moved together: `PROMPT_VERSION`, the canonical pin in `test_extrude_revolve`,
the multi-body pin, and `arena75`'s own identity guard.

The four lines:

```
A request that DOES name one is a different question. "Make the flange 5 mm
wider", with `plate` and `post` standing, names a body this part does not
have. Say that `flange` matches nothing, and then list the bodies there are.
Do not answer it as though it had named neither.
```

`flange` and "5 mm wider" are deliberate: R2's noun is `bracket` and its
request is "10 mm taller", so the prompt carries no near-copy of a corpus
case. A test asserts it, and a mutant that swaps the noun for `bracket` is
caught.

---

## 5 — two defects in Phase D's OWN instruments

Both were found by an adversarial read of this directory before any
conclusion rested on it, and **neither moved an R2 number**.

**The taxonomy was applied to the creation runs.** It describes refusals: a
creation case has no bodies a clarification must name and no pinned noun, so
every naming test is vacuously true and a correct BUILD reads as
`E: operations emitted`. It recorded 46 of 48 that way. `classify` now raises
on a non-refusal case, the runner records the field for refusal runs only,
and the wrong letters are kept in `creation-D0.json` and `creation-D4.json`
under `classes_recorded_in_error` rather than deleted.

**It read `model_operation_count` with a `, 0` default.** Phase A's and Phase
B's records predate that key. Read with the default, five Phase B attempts
that wrote a full four-operation sequence alongside a clarification would say
the model wrote nothing — the exact defect Phase C found in
`emitted_no_operations`, in a new place. `operations_written` now re-derives
the count from the raw answer, and re-reading Phase B's baseline finds
**exactly the five** Phase C counted by hand, without either being told the
other's number.

A third, smaller one: a `model_error` row read as "it did not decline". The
class now comes from `evaluate75.outcome_label`, so there is one authority on
what a provider failure is.

**And four in the arm runners, from the same read.** `arena75.identity()`
calls `prompt.prompt_fingerprint()`, which hashes the module CONSTANT that no
patch touches — so every arm file recorded the COMMITTED prompt's fingerprint
beside the ARM's character count. The adoption was verified against
`arm_fingerprint`, which was always right, so no conclusion moves; the field
is corrected offline with a note and the runner now patches
`prompt_fingerprint` too. `golden_d`'s *"the committed prompt did not survive
the arm"* post-condition compared that same constant to itself and could
never fire; it now compares the text. Both `--out` guards were blocklists of
directories, which by construction protected neither Phase A's and Phase B's
records — loose files in `stage75-multibody/` — nor the frozen instruments
beside them; both are now allowlists of one directory. And an adopted arm,
being idempotent, silently renders the baseline, so running it by name would
record D0's numbers under the arm's name; both runners now refuse that by
comparing the TEXT.

### the rule was hardened, and no threshold moved

The same read found four clauses that could be satisfied by SILENCE — a
mapping with no `creation` key skipped the creation clause and still adopted
— plus `exploratory` defaulting to `False`, code clauses comparing counts
across different sample sizes, and an absolute epsilon in the Fisher tail.
All closed. `MIN_N`, `ALPHA`, `MIN_GAIN`, the three floors, `NO_TRADE`,
`IDENTITY_CODES` and `PLAN_CODES` are exactly what `209f6fc` committed, a
test asserts each of them, and **the verdict is identical either way**.

---

## 6 — the files

| file | what |
|---|---|
| `decision_rule_d.py` | the adoption rule, committed first, hardened later without moving a threshold |
| `classify_d.py` | the A–F taxonomy, refusal-only, agreeing with the grader by construction |
| `variants_d.py` | the arms, pure functions of the committed prompt, idempotent after adoption |
| `arena75d.py` | `arena75` with one arm patched in and a per-case filter; adds no criterion |
| `golden_d.py` | Stage 68's golden EXPLICIT request under a Phase D arm, for the single-body clause |
| `analyse_d.py` | reads the runs, applies the rule; has no threshold of its own |
| `mutation_test_d.py` | **23 mutants, 23 caught** |
| `baseline-d-r2.json` | 32 calls, committed prompt |
| `arm-D1-explore.json`, `arm-D3-…`, `arm-D4-…` | 32 calls each |
| `confirm-refusal-D0.json` / `-D4.json` | 48 each, R1+R2+R3 × 16 |
| `creation-D0.json` / `-D4.json` | 48 each |
| `golden-D0.json` / `-D4.json` | 24 each |

---

## 7 — what is NOT fixed

- **R2 is bounded, not resolved: 13/16.** Three in sixteen still give the
  pronoun answer. Whether that residual has a cause of its own, or is the
  same failure at a lower rate, is **unmeasured** — no arm was run against it,
  and Stage 70's lesson is that adding prompt text on a non-significant
  reading is how a prompt bloats.
- **R3 fell 16/16 → 15/16** on one call, inside the rule's floor. One call is
  not a trend and it was not chased.
- **Creation M5 fell 8/8 → 7/8**, exactly at the floor, on the case whose own
  failure mode is incoherent dimensions when none are stated. The creation
  group ROSE, 44/48 → 47/48.
- **The single-body check is 24 calls, not a baseline.** It is a regression
  clause, and the number it should be compared with is its own control in the
  same session.
- **Every number here is `MODEL_GENERATED` or `REFUSED` from a real provider
  call.** No deterministic result is counted anywhere, and no run was
  repaired, retried or re-prompted.
