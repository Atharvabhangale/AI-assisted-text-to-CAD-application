# Stage 68 — separating benchmark ambiguity from model capability

**16 live calls** to `claude-haiku-4-5-20251001`, 8 per request, on the
**unchanged** prompt `2026-09-18.3` (`c78aaad8eacf365e`). No prompt variant
was built and no prompt was patched: the only independent variable is which
of the two golden requests is sent.

**Result: the ambiguity was almost the whole story.**

| | original (ambiguous) | explicit (companion) |
|---|--:|--:|
| structured output | 8/8 | 8/8 |
| plan valid | 5/8 | **8/8** |
| built | 5/8 | **8/8** |
| thickness = 5 mm | 2/8 | **8/8** |
| envelope = 40×20×20 | 4/8 | **8/8** |
| bores centred | 4/8 | 7/8 |
| topology 18/42 | 2/8 | 7/8 |
| volume = closed form | 0/8 | 7/8 |
| **STRICT SUCCESS** | **0/8** | **7/8** |

## The two requests

**ORIGINAL — never edited, ambiguous on purpose:**

> Make a hollow rectangular box with 40*20*5 (4)plates and 20*20 (2) plates
> with 8mm diameter holes in center of each plate

**EXPLICIT — same part, thickness and outer height stated:**

> Make a hollow rectangular enclosure with an outer size of 40 mm long, 20 mm
> wide and 20 mm high. Build it from six plates, every one of them 5 mm thick:
> four plates with a 40 x 20 face form the floor, the roof, the front wall and
> the back wall, and two plates with a 20 x 20 face form the two end walls.
> Each plate's 5 mm thickness runs along the normal of the face it covers, and
> the six plates meet to enclose a hollow interior. Then put an 8 mm diameter
> hole through the centre of each pair of opposite walls, so the finished part
> has one hole along each of the three axes.

Both describe the **same part**, so one ground truth serves both — a test
asserts `expected("original") == expected("explicit")`.

## Immutable ground truth

Constants in `ground_truth.py`, fixed before any model was called and derived
only from the request text and closed-form arithmetic:

| | |
|---|---|
| envelope | 40 × 20 × 20 |
| plate thickness | **5 mm** |
| hole diameter | 8 mm |
| plates | 6 (four 40×20 faces, two 20×20 faces) |
| bores | **3**, one per axis (+X, +Y, +Z) |
| openings | 6 — each bore pierces a facing pair |
| bore centreline | the two components **across** a bore's own axis sit at (20, 10, 10); the third is free |
| solids | 1 |
| faces / edges | **18** (12 planar + 2 cylindrical per bore) / **42** |
| volume | `(40·20·20 − 30·10·10) − 3·2π·4²·5` = **11492.035526276899** |

### Why this module exists

Stage 67 graded 80 live calls with a criterion that derived the expected
thickness **from the model's own plan** (`t = plan_facts.get("thickness")`,
stage67 `spatial.py:124`). Sixteen parts built from 4 mm plate scored correct
against a request that says 5. **A criterion that grades a part against its
own answer cannot fail it.**

`test_golden_ground_truth.py` makes that structurally unreachable, and its
guards are mutation-tested — reintroducing the Stage 67 defect (giving
`expected()` a `plan_facts` parameter) goes red on the test that names it:

| mutation | caught by |
|---|---|
| reference thickness set to 4 mm | 7 tests |
| `expected()` accepts the model's plan | `test_the_expectation_api_accepts_no_model_output` |
| ground truth imports the executor | `test_the_ground_truth_module_imports_nothing_that_carries_an_answer` |
| the ambiguous original request edited | `test_the_two_requests_are_pinned_verbatim` |

## Failure taxonomy

| code | original | explicit |
|---|--:|--:|
| **A** thickness | **6** | 0 |
| **B** envelope height | 2 | 0 |
| **C** plate placement | 0 | 0 |
| **D** opposing plate | 0 | 0 |
| **E** bore position | 4 | **1** |
| **F** bore direction | 1 | 0 |
| **G** degenerate cut | 0 | 0 |
| **H** wrong target (P11) | 3 | 0 |
| **I** other | 0 | 0 |

## What ambiguity explains — and what it does not

**Explained, and completely.** A (thickness), B (envelope height), F (bore
direction) and **H (wrong target)** all fall to zero. H is worth naming: the
P11 union-target failure that Stages 63, 65, 66 and 67 all chased appeared 3/8
on the ambiguous request and **0/8** on the explicit one, with no prompt
change at all. A model that is unsure what the part *is* also gets its
references wrong. Much of what four stages read as a targeting defect was a
comprehension defect.

**Not explained.** One failure survives disambiguation, and it is the same
defect on all three bores of one attempt:

```
hole_z  axis=+Z  position (20, 10, 0)     correct — z is the ignored along-axis component
hole_y  axis=+Y  position (20, 10, 0)     WRONG  — for +Y the pair is x,z; z must be 10
hole_x  axis=+X  position (20, 10, 0)     WRONG  — for +X the pair is y,z; z must be 10
```

The model wrote the +Z hole's triple and reused it for the other two, leaving
their centrelines in the bottom face plane where they graze rather than bore.
The plates in that attempt are **identical to the successes**. It still built:
one solid, 25 faces, 65 edges, 11351.419374 mm³ — 140.6 mm³ short.

This is the defect Stage 66 named and the current prompt already addresses
with a per-axis table. It now recurs at **1 in 8** rather than dominating.

### A gap this stage found in its own instrument

The first classifier keyed `E:bore_position` on the build failure message, so
it missed this attempt entirely — the part *built*. The evaluator now carries
a **ground-truth-derived** centre check (`CENTRE = ENVELOPE/2`, with the
along-axis component deliberately free), and both baselines were **re-graded
offline** against it. No model was re-called and no raw output changed; the
files carry a `regraded_note` saying so. Under the first classifier the same
attempt read `I:other`, which named nothing.

## Kernel evidence — MODEL_GENERATED

The seven strict successes, rebuilt from recorded raw output:

| | |
|---|---|
| thickness | **5.0 mm** on all seven |
| volume | **11492.03552627690** mm³ |
| immutable closed form | 11492.035526276899 — delta **1.82e-12** |
| topology | 1 solid, 18 faces, 42 edges, envelope (40, 20, 20) |
| CadQuery 2.8.0 | 11492.03552627690 |
| FreeCAD 1.0.0 | 11492.03552627690 — **bit-identical** |

`MODEL_GENERATED`, distinct from `DETERMINISTIC`: no fixture, no local
grammar, no fallback. The deterministic reader reaches the same number by a
different route, which is why it is the reference and not the evidence.

## Reproducing

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:<this directory>
cd apps/api
# --live is required; a credential's presence never starts a run
python3 <this directory>/arena.py --request original --calls 8 --live
python3 <this directory>/arena.py --request explicit --calls 8 --live
```

The prompt is not touched by either run; the arena contains no assignment to
`system_prompt`.

**No credential value appears in any file here.** `credential_from` records
the environment variable *name* only.
