# Phase A: the four measurements that were not measurements

Phase A ran 64 live calls and reported creation 31/48, refusal 0/16. Four of
its eight cases produced numbers that say nothing about the model. This file
is the record of why, kept so the Phase A baseline stays readable rather than
quietly rewritten.

**The cases themselves are preserved verbatim in `ground_truth75.py`** — the
requests, the constants and the expectations are exactly what was measured.
They are marked `retired`, `expected()` refuses them, and `arena75.py` will
not run them. Nothing was edited. A corpus that deletes its mistakes cannot
be audited, and an edited case silently changes what an old number meant.

---

## M4 — the corpus over-pinned a placement the request never states

**Measured:** 0/8, every one `F:wrong_placement`.

**Request:** *"Create a 40 mm cube and a 20 mm cylinder as separate bodies,
then make the cylinder 40 mm long."*

It says **separate bodies**. It never says *beside*. The case nonetheless set
`disjoint=True`, so the model was required to place the two solids apart on
the strength of a word that is not in the request. It put both at the origin,
which the request permits.

Everything M4 exists to test passed 8/8:

| check | result |
|---|---|
| `body_count` | 2 ✓ |
| `declared_every_body` | ✓ |
| `edited_body` (cylinder → 12566.370614) | ✓ |
| `other_body_untouched` (cube at 64000) | ✓ |
| `plan_valid`, `built` | ✓ |
| `bodies_disjoint` | **✗ — the only failure** |

This is the exact fault the ground truth's own docstring claims to avoid for
M3 and M5: *"inventing the rest and then grading against it would be the
instrument marking its own homework."* M4 did it anyway.

**Replaced by N1**, which states the separation in the request itself.

## M8 — the request is self-contradictory and the expectation unreachable

**Measured:** 0/8.

**Request:** *"Create a 40 mm cube and a 20 mm diameter cylinder 30 mm long
**beside it**, **fused together into a single body**."*

Those two clauses cannot both hold. Rule **E3** requires a union to leave one
connected solid; two disjoint solids cannot fuse into one body. The engine
said so on all eight calls:

> the fuse left 2 separate solids; the parts do not all touch, so they cannot
> form one body (rule E3)

`FUSED_VOLUME` reasons that *"a union of disjoint solids adds nothing and
removes nothing"*, so the expected volume is the plain sum. For this engine
that union does not produce a body of any volume — **it does not build at
all.** The expectation is one no kernel can satisfy.

The model answered correctly 8/8: cube at the origin, cylinder at x=50, one
union. The control case was unpassable as written.

**Replaced by N2**, whose solids overlap, so the fuse is legal.

## M6 and M7 — a broken observer, not a measurement of the model

**Measured:** 0/16 across the refusal group.

`observe()` read `generation.questions`. **`PlanGenerationResult` has no such
attribute** — the model's own words live on `result.plan`
(`summary`, `reason`, `questions`). `getattr(..., ())` returned an empty
tuple for all 64 calls, so the grader's naming check ran against an empty
string every time. Four of the sixteen refusals carried a **populated
`questions` list** in their raw answer and were scored as if they had said
nothing.

A second defect sat beside it: `observe()` recorded `result.error` — the
**validator's** message — under the key `reason`, and `grade()` folded it
into the text it searched for body names. A system sentence could have
satisfied a check about what the *model* named.

Replayed from the preserved raw text the group is **2/16**, not 0/16. That
replay is a diagnostic and was never adopted as a score.

The **cases were sound**; the instrument was not. They are restated verbatim
as **R1** and **R2** and re-measured under the corrected observer, because
the only clean way to get a number is to ask the identical question again.

---

## What Phase A did measure

These stand, and none of them depended on the broken paths:

- **M1, M2, M3: 24/24 strict.** Two declared bodies, correct per-body
  volumes, disjoint placement, bit-identical on CadQuery 2.8.0 and FreeCAD
  1.0.0.
- **structured output 64/64, fenced 0/64.**
- **Body identity, targeting and edit isolation: 0 errors** across the whole
  run — no `C`, no `D`, no `E`.
- **M5 7/8**, with one genuine failure: the model chose a 1 mm cube and a
  1 mm cylinder for a request stating no dimensions, then drilled the stated
  6 mm hole, removing everything (E2). Phase B encodes that as a coherence
  constraint — see `README.md`.

---

# Phase B: a fifth invalid case, and it was mine

**N1 — retired on its first use, 0/8.**

N1 replaced M4 and fixed M4's defect: the separation is stated in the
request. In doing so it introduced a different one. The text calls the
cylinder *"30 mm long"* and then asks to *"make the cylinder 40 mm long"* —
two lengths for one solid.

The model refused all eight and said exactly why:

> The cylinder is first specified as 30 mm long, then immediately asked to be
> made 40 mm long. These are contradictory dimensions for a single cylinder.

**M4's original wording gave no initial length and was coherent.** The
contradiction is entirely mine, introduced while fixing the placement. That
is the same lesson as M4 and M8 in a third form: a request must be checked
for what it now says, not only for what it was written to fix.

**Replaced by N3**, which states the separation *and* gives exactly one
length. N3 measures **7/8** — so M4's intent was always achievable, and
three of the four zeros in this corpus's history were the instrument, not
the model.

A test now pins that N3 mentions `40 mm long` and never `30 mm long`, so the
same request cannot go wrong a third way.
