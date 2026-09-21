# Stage 66 — the golden six-plate enclosure, built by the live model

**40 live calls** to `claude-haiku-4-5-20251001`, eight arms, one variable at
a time. Every arm's raw model output, validation result and build result is
in `arm-*.json`. Nothing was repaired, retried or re-prompted, and no
validation rule was weakened.

`arena.py` is a **byte-for-byte copy** of Stage 65's
(`sha256 99c101068a8922d476f92e431a0ad5b7e8f480f7c4b18548b26c3a19a76e0fc9`),
so the instrument is the same one. Stage 65's arms are neither re-run nor
edited.

## The result

**The live model built the golden six-plate hollow enclosure**, for the first
time in this project. `arm-F7-name-the-line.json`, attempt 5:

| | |
|---|---|
| plan | 6 plates → `fuse` (union, target `bottom`) → `hole_z`, `hole_x`, `hole_y` |
| validates | **True**, no problems |
| volume | **10185.62842102151** mm³ |
| closed form | `(40·20·20 − 32·12·12) − 3·2π·4²·4` = 10185.62842102152 |
| delta | **9.1e-12** |
| solids / faces / edges | **1 / 18 / 42** — the same topology as the deterministic reference |
| CadQuery 2.8.0 | 10185.62842102151 |
| FreeCAD 1.0.0 | 10185.62842102151 — **bit-identical** |

Labelled `MODEL_GENERATED`. No fixture, no deterministic reader, no fallback.

## The matrix

| arm | P11 | validated | built | enclosure envelope | what it tested |
|---|--:|--:|--:|--:|---|
| `F0-baseline` | 2/5 | 3/5 | 2/5 | **0/5** | control: prompt `2026-09-18.2` |
| `F1-enclosure-arithmetic` | 1/5 | 4/5 | 0/5 | 0/5 | the permutation rule |
| `F2-one-hole-per-axis` | **4/5** | 1/5 | 0/5 | 0/5 | the hole-count rule — **regressed** |
| `F3-both` | 4/5 | 1/5 | 0/5 | 0/5 | F1 + F2 |
| `F4-layout-corrected` | **5/5** | 0/5 | 0/5 | 0/5 | F1 fixed, + envelope derivation |
| `F5-carrier-is-a-plate` | **0/5** | **5/5** | 0/5 | 0/5 | rename the carrier — one variable |
| `F6-all-three` | 0/5 | 5/5 | **1/5** | **1/5** | F4 + F2ʹ + F5 |
| **`F7-name-the-line`** | 1/5 | 4/5 | **2/5** | **2/5** | F6 + hole naming and position |

`F0` "built 2/5" is the trap this stage exists to avoid: both builds were
valid one-solid parts of the **wrong envelope** — stacked slabs, not
enclosures. Valid CAD is not correct CAD.

Of F7's two builds, **one is the part that was asked for**. The other drilled
a single hole instead of three. So the honest reading is **1/5 fully
correct**, 2/5 a correct enclosure envelope, against a baseline of 0/5.

## Four defects, each measured

Stage 65 left the request failing on rule E1 — *the hole's centreline does not
intersect the target*. Reading the five recorded plans, then measuring:

1. **The plates were never rotated.** Every baseline attempt wrote all six
   plates as `40 × 20 × 5`, thickness on Z, stacking slabs. A shell needs the
   same three numbers **permuted** onto each face's normal: `40 × 5 × 20` and
   `5 × 20 × 20`. F1's permutation table fixed this **5/5**.
2. **The outer size was invented** — heights of 25, 20 and 5 on three
   attempts, because nothing said the plate faces give it.
3. **Holes were named for walls.** `hole_front` and `hole_back` are two
   operations on one centreline, and the second removes nothing. The one
   attempt that wrote `hole_x`/`hole_y`/`hole_z` wrote three and built.
4. **`z is conventionally 0` was stated for `+Z` only** and carried to `+X`
   and `+Y`, dropping those centrelines into the bottom face plane. This is
   why F6's build came out at 9843.95 mm³ with 31 faces: a valid, one-solid
   enclosure of exactly the right envelope that is **not the part asked for**.

## The finding: the prompt supplied the noun that broke it

Across all 40 calls, sorted by the id the model gave its union:

| union's id | P11 |
|---|--:|
| `fuse` (the mandated verb) | **6 / 27** |
| anything else — `shell`, `fuse_all`, … | **11 / 12** |

The committed prompt's own enclosure sentence read *"six plates where the
first is named `shell`"*. On a hollow-enclosure request the model took
`shell` for the **union** rather than for the carrying plate, and then
targeted it. Renaming the carrier to `bottom` — **one sentence, nothing
else** — took P11 from 5/5 to **0/5** (`F5`).

This is Stage 65's mechanism found a second and a third time: **an id's shape
decides what the model does with it.** Prose about the rule moved nothing;
removing the noun that invited the wrong answer moved it, and so did renaming
holes after their line instead of their wall.

Two negative results are worth as much and are kept:

- **`F2` regressed P11 to 4/5.** Its text opened *"A hollow box with a hole
  through the centre of each of its six plates…"* — reintroducing a product
  noun next to the hole rule. Rewritten without the noun (`F6`), the same
  rule helped.
- **`F4` regressed P11 to 5/5** while fixing the envelope, because the layout
  section talks about "an enclosure" throughout and the `shell` sentence was
  still standing. Only with `F5` applied did the layout rule pay off.

**An example's numbers are read as data.** `F1`'s example said "4 mm plate",
and the request says "(4)plates" where 4 is a **count** — every `F1` attempt
built plates 4 thick. The thickness is now 6, which appears nowhere in the
request, and the example says so explicitly. The model still chose 4, so the
ambiguity is in the **request**, not the example: one `F6` attempt asked
*"does '5' refer to the thickness or one of the face dimensions?"*, which is
a fair question. **The prompt was not tuned to force 5** — that would be
teaching to the test.

## What is NOT fixed

- **1/5, not 5/5.** Three of five attempts still write one hole per wall
  despite the rule, and the remaining failures are all rule E1.
- **The request is genuinely ambiguous** about plate thickness, and the model
  is right to be unsure. A re-worded golden request would measure the layout
  rules without that confound; this stage deliberately did not change it.
- **No corpus benchmark.** This is one request, measured well. Stage 48's
  instrument is still unrun.

## Reproducing

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:<this directory>
cd apps/api
# --live is required; a credential's presence never starts a run
python3 <this directory>/arena.py --arm F0-baseline --calls 5 --live
python3 <this directory>/arena.py --arm F7-name-the-line --calls 5 --live
```

`variants.py` computes each arm as a delta from the **baseline at the time**.
`F7` is now committed as prompt `2026-09-18.3`
(`c78aaad8eacf365ed1e557f6e7325513e3cd8b80592c39c319ae7cb5a156a521`, 30481
characters), verified byte-identical to the arm that was measured, so the
F-series deltas no longer apply to the current baseline. They are kept as the
record of what was measured.

**No credential value appears in any file here.** `credential_from` records
the environment variable *name* only.
