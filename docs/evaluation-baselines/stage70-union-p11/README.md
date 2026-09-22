# Stage 70 — the P11 residual measured, bounded, and NOT prompt-fixed

**288 live calls** to `claude-haiku-4-5-20251001`, all on the **EXPLICIT**
golden request, five arms, one variable each. **No prompt change was
adopted.** The prompt is unchanged at `2026-09-18.5` / `8563c6fb821e022f`.

An adoption rule was written and **committed before the confirming runs**
(`decision_rule.py`, commit `acf9028`). It rejected all four candidates.

## 1 — the brief's premise, corrected

Stage 69 recorded the residual as *"every P11 attempt targets the product
noun `enclosure`"*. True as a token, misleading as a description:
**`enclosure` is what the model named the UNION.** The model gives the union
a body noun and then targets the name it just wrote.

Over the 144 baseline attempts — **432 post-union targets** — every target
named one of exactly two things:

| role the target names | count |
|---|--:|
| the surviving body (**correct**) | 408 |
| the union operation's **own id** (P11) | 24 |
| a consumed tool | **0** |
| an id absent from the plan | **0** |

The model never invents an id and never names something the union ate.
`target_analysis.py` classifies **relationally**, against the plan's own
structure, so no future noun can make this description wrong again.

## 2 — the fresh baseline

48 new calls on the committed prompt, pooled with Stage 69's arm A2 (96
calls, whose text *is* `2026-09-18.5`): **n = 144**.

| | |
|---|--:|
| STRICT SUCCESS | **135/144 = 93.8 %** (95 % CI 88.5–97.1) |
| `H:wrong_target` (P11) | **8/144 = 5.6 %** (95 % CI **2.4–10.7**) |
| thickness = 5 mm | **144/144** |
| plate count = 6 | **144/144** |
| `E:bore_position` | 1/144 — Stage 69's fix holding |
| P11 given union named `fuse` | **0/136** |
| P11 given any other union id | **8/8** |

## 3 — the matrix

| arm | one variable changed | n | strict | P11 | union id ≠ `fuse` |
|---|---|--:|--:|--:|--:|
| **B0** baseline | nothing | 144 | 135/144 | 8/144 | 8/144 |
| **B1** no-product-nouns | the forbidden-noun sentence **deleted**, rule and mandate kept | 96 | 85/96 | **7/96** | **0/96** |
| **B2** read-it-back | one check appended to the mandate; **no new noun** | 32 | 31/32 | 1/32 | 1/32 |
| **B3** mandate-first | the mandate moved **before** the naming rule — a **pure reordering** | 32 | **21/32** | **11/32** | 10/32 |
| **B4** mandate-last | the mandate moved **after** the layout block — also a **pure reordering** | 96 | 92/96 | 3/96 | **0/96** |

### The one significant result: order is load-bearing

**B3 regressed hard.** Same length, same characters, two paragraphs swapped:
strict 135/144 → 21/32 (**p = 0.0001**), P11 8/144 → 11/32 (**p < 0.0001**).
Unlike Stage 69 — where the equivalent control moved nothing — position is a
live mechanism in this section. The committed order is not cosmetic, and
`test_union_section_shape.py` pins it.

### The union id is a MARKER, not the cause

The perfect separation in §2 invites the reading *"make it say `fuse` and
P11 goes away"*. **B1 and B4 both falsify it.** Each reached **96/96**
`fuse` compliance — and each still carried P11, at 7/96 and 3/96, every one
of them on a union correctly named `fuse`. A perfect correlation over one
prompt is not a mechanism.

**B1 is the sharper negative result.** Deleting the forbidden-noun sentence
bought total id compliance and *raised* P11. What that sentence supplies is
not the id but the reason the union's id names no solid; removing the
explanation moved the failure onto `fuse` itself.

**B2, the clean prose arm, did nothing** — 1/32, indistinguishable from
baseline. The fourth stage running in which prose about a rule moves nothing.

## 4 — the pre-registered rule, applied

An arm is adopted only with pooled **n ≥ 64**, P11 below baseline at Fisher
exact two-sided **p < 0.05**, and no regression in thickness, plate count,
envelope, bore axes or strict success.

| arm | adopt | P11 p | why not |
|---|---|--:|---|
| B1 | **no** | 0.597 | P11 not lower; envelope, bore axes and strict all regressed |
| B2 | **no** | 1.000 | n = 32 < 64; not significant |
| B3 | **no** | <0.001 | significantly **worse** |
| B4 | **no** | 0.533 | not significant; bore axes 95/96 vs 143/144 |

**B4 is the one that would have been adopted without the rule.** Its first
32 calls came back **32/32 — a perfect arm, the first in this project's
history on this request.** Its two confirmation runs were 29/32 and 31/32.
Pooled 3/96 against 8/144 is **p = 0.53**. Stage 67 adopted an arm on exactly
this kind of first reading and had to revert it; the rule existed here before
the numbers did, which is why the same thing did not happen twice.

## 5 — kernel evidence, MODEL_GENERATED

**136** claimed successes (baseline's 44 + B4's 92) rebuilt from recorded
output — no model called, no fixture, no deterministic reader:

| | |
|---|---|
| distinct volumes | **11492.035526277** (one value, across all 136) |
| closed form | 11492.035526276899 |
| max &#124;delta&#124; | **1.819e-12** |
| solids / faces / edges | 1 / **18** / **42** |
| envelope | 40 × 20 × 20 |
| mismatches | **0** |
| backends | CadQuery **2.8.0** and FreeCAD **1.0.0**, **bit-identical** |

## 6 — the stopping decision

**Stop here, and carry the number.** P11 is **5.6 %, CI [2.4 %, 10.7 %]** on
the committed prompt. Five arms and 288 calls produced no intervention that
lowers it significantly; the only significant effect found was a way to make
it **worse**. Distinguishing 5.6 % from, say, 2 % at p < 0.05 needs several
hundred more calls per arm, and three of the five arms here show the
candidate edits trading one failure mode for another.

Adding prompt text on the strength of a non-significant arm is how a prompt
bloats, and this one is already 30917 characters. The residual is bounded,
measured, reported honestly, and **fails closed**: a P11 plan is rejected by
the validator and builds nothing. It is not silent, and nothing is repaired.

**The system is ready for the next architectural milestone** — the
multi-body slice, step 1 only.

## Files

| file | what |
|---|---|
| `decision_rule.py` | the adoption rule, **committed before the confirming runs** |
| `target_analysis.py` | relational classification of every post-union target |
| `variants70.py` | the five arms, each a pure function of the baseline. Never edits `prompt.py` |
| `arena70.py` | one arm, N live calls. Imports Stage 68's `arena.py` / `evaluate.py` / `ground_truth.py` and Stage 69's `axis_analysis.py` / `prompt_guard.py` **unmodified** |
| `mutation_test.py` | reintroduces each defect and asserts the guard goes red. 6/6 caught |
| `arm-B0-baseline.json` … `arm-B4-confirm-2.json` | the nine runs |
| `verify-cadquery.json`, `verify-freecad.json` | the kernel rebuilds |

**Stages 65–69's directories are untouched.** `arena70.py` refuses an `--out`
inside any of them, and the modules it reuses are imported rather than
copied.

**No credential value appears in any file here.** `credential_from` records
the environment variable *name* only.

## Reproducing

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:\
docs/evaluation-baselines/stage68-benchmark-disambiguation:\
docs/evaluation-baselines/stage69-bore-axis-centre:\
docs/evaluation-baselines/stage70-union-p11
cd apps/api
D=../../docs/evaluation-baselines/stage70-union-p11

python3 $D/variants70.py                      # offline; calls nothing

python3 $D/arena70.py --calls 48 --variant B0-baseline       --request explicit --live
python3 $D/arena70.py --calls 32 --variant B1-no-product-nouns --request explicit --live
python3 $D/arena70.py --calls 32 --variant B2-read-it-back   --request explicit --live
python3 $D/arena70.py --calls 32 --variant B3-mandate-first  --request explicit --live
python3 $D/arena70.py --calls 32 --variant B4-mandate-last   --request explicit --live

# offline; rebuilds recorded output on a named backend
CAD_BACKEND=cadquery python3 ../../docs/evaluation-baselines/stage69-bore-axis-centre/verify.py $D/arm-B*.json
CAD_BACKEND=freecad  python3 ../../docs/evaluation-baselines/stage69-bore-axis-centre/verify.py $D/arm-B*.json
```
