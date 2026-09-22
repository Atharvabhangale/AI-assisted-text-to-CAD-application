# Stage 67 — the golden enclosure's spatial failure: measured, and nothing adopted

**80 live calls** to `claude-haiku-4-5-20251001`, nine arms, one variable at a
time. Every arm's raw model output, plan, validation result, kernel geometry
and spatial verdict is in `arm-*.json`. Nothing was repaired, retried or
re-prompted, and no validation rule was weakened.

**Outcome: NO PROMPT CHANGE IS ADOPTED.** The prompt stays `2026-09-18.3`
(`c78aaad8eacf365e`, 30481 chars).

## What the brief expected, and what is actually true

The brief said roughly 3/5 attempts still produce a "one hole per wall"
failure. **Failure mode A occurred once in 80 calls.** On the committed
prompt the model already writes three bores on 6/8 and six plates on 7/8.
Stage 66's headline defect is essentially gone; what remains is different.

## The criterion, and the mistake it was built to avoid

Stage 66 scored a build correct on envelope plus targeting, which passed an
attempt that drilled **one** hole. Stage 67 replaced that with a criterion the
kernel decides (`spatial.py`): one solid, envelope 40×20×20, **18 faces**
(12 planar + 2 cylindrical per bore), 42 edges, and volume equal to the closed
form. It was validated against ground truth before use — it accepts Stage 66's
verified build and rejects Stage 66's one-bore build.

**It was still too lenient, and an independent reviewer caught it.** It derived
the closed form from *whatever thickness the model chose*, so a part built from
4 mm plate scored correct against a request that says 5. A criterion that
grades a part against its own answer cannot fail it. Both rates are reported
below.

## The matrix

| arm | union §  | plates 6/8 | bores 3/8 | valid | built | P11 | **spatial** | **strict (t=5)** |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| `G0-baseline` (**2026-09-18.3**) | 4550 | 7 | 6 | 7 | 5 | 1 | **2/8** | **1/8** |
| `H1-envelope-from-the-face` | 4928 | 7 | 4 | 5 | 3 | 3 | 2/8 | 0/8 |
| `H2-far-plate-offset` | 4784 | 6 | 5 | 5 | 5 | 3 | 2/8 | 1/8 |
| `H3-both` | 5162 | **8** | **8** | 4 | 3 | 4 | 3/8 | 1/8 |
| `H4-mandate-last` | 5162 | 6 | 5 | 5 | 3 | 3 | 2/8 | 1/8 |
| `H5-layout-in-box-section` | 2259 | 4 | 4 | 6 | 1 | 1 | 1/8 | 0/8 |
| `H6-rules-without-example` | 4111 | 7 | 2 | 2 | 0 | **6** | **0/8** | 0/8 |
| `H7-example-through-the-holes` | 5925 | 5 | 6 | **8** | 4 | **0** | **4/8** | **0/8** |
| `H8-square-face` | 6340 | 6 | 6 | 8 | 6 | 0 | 4/8 | 0/8 |
| `confirm` (H7 as committed `.4`) | 5925 | 8 | 7 | **8** | 4 | **0** | **4/8** | **0/8** |

## The failure taxonomy (Phase 2)

| mode | occurrences / 80 | what it is |
|---|--:|---|
| **D** wrong plate placement | 17 | the far plate of a pair at `t` or at `extent`, or the envelope height invented |
| **E** missing opposing plate | 15 | fewer than six plates |
| **G** other | 15 | mostly P11, the union's own id used as a solid |
| **C** wrong position | 12 | a centreline that misses the target (usually a consequence of D) |
| **F** duplicate coaxial cut | 12 | two bores on one centreline |
| **B** wrong direction | 8 | fewer than three distinct axes |
| **A** one hole per wall | **1** | the mode the brief expected to dominate |

Two root causes dominate and are **disjoint** — no attempt failed both:

1. **The envelope height is under-determined by the prompt's own rule.** It
   derives the third extent as *"the only number the bottom did not already
   give"*. For this request the bottom is 40×20 and the ends are 20×20, so the
   end plate gives **no new number** and the procedure returns nothing. The
   model then picks: 40, 5, 25 or 60. The rule also fails on the prompt's own
   worked example (ends 30×30 on a 60×30 bottom).
2. **The far plate of a facing pair** lands at `t` or at `extent` rather than
   `extent - t`.

## What each arm settled

- **H1 / H2 alone: no effect** (2/8 each, against a 2/8 baseline). Stating the
  rules more precisely, one at a time, changed nothing.
- **H3 (both) fixed the structure completely** — six plates 8/8, three bores
  8/8, modes A and D gone — and regressed P11 from 1/8 to 4/8.
- **H4 refuted the "distance" explanation** for that regression: moving the
  `fuse` mandate to the end of the section, unchanged, made things worse (and
  introduced clarifications).
- **H5 refuted it from the other side.** Moving the layout block into `## box`
  restored P11 to 1/8 but lost the layout effect entirely (geometry 4/8).
- **H6 refuted the length reading outright.** Its union section is *shorter*
  than the baseline's (4111 vs 4550) and it produced the **worst** P11 of the
  stage, 6/8. The monotone P11-vs-length trend across H1–H5 was noise at n=8.
  What H6 does establish is that **the worked example is load-bearing**.
- **H7 was the best arm**: completing the worked example through its three
  bores took P11 to **0/8**, validation to **8/8** and spatial success to
  **4/8**, reproduced exactly on a fresh 8-call confirmation run.
- **H8 (square-face note) did not improve** the criterion (4/8), so it was not
  adopted either.

## Why H7 was rejected despite being the best arm

H7 doubled the spatial rate and eliminated P11 — and it **significantly
regressed the plate thickness**:

| | reads thickness 5 (as requested) |
|---|---|
| baseline `2026-09-18.3` | **5 / 8** |
| H7, adopted as `.4`, over 16 calls | **0 / 16** |

Fisher exact, two-sided: **p = 0.00132**. On the strict criterion — spatially
correct **and** built from the 5 mm plate the request specifies — H7 scores
**0/16** against the baseline's **1/8**. It makes a better-shaped box out of
the wrong material, and the project does not adopt a change that its own
criterion only passes because the criterion is lenient.

The prompt was reverted. `2026-09-18.4` exists nowhere in the history.

## Kernel evidence for the parts that were correct

Eight MODEL_GENERATED plans from the H7 arm and its confirmation run, rebuilt
from the recorded output on **both** backends:

| | |
|---|---|
| volume | **10185.628421022** mm³ on all eight |
| closed form (t = 4) | 10185.628421022 — delta **9.09e-12** |
| topology | 1 solid, **18 faces**, **42 edges**, envelope (40, 20, 20) |
| CadQuery 2.8.0 | 10185.628421022 |
| FreeCAD 1.0.0 | 10185.628421022 — **bit-identical** |

Labelled `MODEL_GENERATED`. These are real parts; they are simply built from
4 mm plate rather than the requested 5 mm, which is why they do not count
toward the strict rate.

## Is the golden request 5/5?

**No.** Best measured spatial rate is 4/8; best strict rate is 1/8, on the
unmodified prompt. **Multi-body work does not begin.**

## Reproducing

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:<this directory>
cd apps/api
# --live is required; a credential's presence never starts a run
python3 <this directory>/arena.py --arm G0-baseline --calls 8 --live
```

`variants.py` computes each arm as a delta from the committed baseline, and
`_baseline()` reads `prompt.py` — so with the revert in place every `H*` arm is
again a delta from `2026-09-18.3`, exactly as when it was measured.

**No credential value appears in any file here.** `credential_from` records the
environment variable *name* only.
