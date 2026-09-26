# Stage 77 — the broader live multi-body corpus

**240 live calls** to `claude-haiku-4-5-20251001`. Every number here is
`MODEL_GENERATED`: no fallback is counted as a success, no answer was
repaired, re-prompted or retried, and the source label is the grader's
FIRST check, so a deterministic answer fails outright.

This is a **measurement** milestone. No prompt was changed and none was
tried.

---

## 1 — what it measures that Stage 75 could not

Stage 75's multi-body corpus is nine cases against ONE two-body shape, and
its own design note says why 47/48 creation is not general reliability:

> Four of the six creation cases are the same part. […] Every case declares
> at most two bodies. `MAX_BODIES` is 8 and nothing above two has been asked
> of a model even once. […] No creation case exercises a downstream surface.

| | Stage 75 | Stage 77 |
|---|---|---|
| cases | 9 | **18** (+1 retired) |
| geometric families | 1 | **9** |
| most bodies in a case | 2 | **5** |
| bodies of identical dimension | none | **CR-05** |
| edit chains | none | **2 and 3 turns** |
| downstream surfaces | none | measurement, aggregate and export on **every** creation attempt |

---

## 2 — the identity every number is against

| | |
|---|---|
| model | `claude-haiku-4-5-20251001` |
| prompt | `2026-09-25.1` / `f265d7d1e279e95a` / 34036 chars |
| encoding | `strict_selector_union_part` / 3874 inlined / `ef7427700af93ed7` |
| backend | CadQuery 2.8.0 (arena), FreeCAD 1.0.0 (browser + verification) |

`arena77.check_identity()` compares the live route against constants in
`ground_truth77` and **refuses to run** on a difference. It matched.

---

## 3 — the result

**240 live calls · 232 graded · 8 excluded (RF-04, retired — §5).**
Structured output 240/240, fenced 0/240.

| quantity | rate | 95 % CI |
|---|---|---|
| **creation** | **100/104 = 96.2 %** | 90.5 – 98.5 |
| **edit**, per turn | **77/88 = 87.5 %** | 79.0 – 92.9 |
| **edit**, per whole chain | **53/64 = 82.8 %** | 71.8 – 90.1 |
| **refusal** | **48/48 = 100 %** | 92.6 – 100 |
| **measurement** | **96/96 = 100 %** | 96.2 – 100 |
| **aggregate** | **96/96 = 100 %** | 96.2 – 100 |
| **export** | **104/104 = 100 %**, all at rung `F:verified` | 96.4 – 100 |

**There is no combined number and `summarise` refuses to produce one.**
Six quantities, six denominators. Pooling a refusal rate with a build rate
is how a model that refuses everything scores well.

### Per dimension

| | | |
|---|---|---|
| A | independent body creation | 92/96 |
| B | explicit body naming | **16/16** |
| C | unnamed multi-body creation | 76/80 |
| **D** | **named-body edit** | **53/64 = 82.8 %** |
| **E** | **named-body feature / hole** | **45/56 = 80.4 %** |
| F | ambiguous body reference | **16/16** |
| G | nonexistent body reference | **32/32** |
| H | explicit fusion | **8/8** |
| I | body measurement | 92/96 |
| J | aggregate measurement | 92/96 |
| K | STEP export | 100/104 |
| L | export identity where measurable | **16/16** |
| M | multiple sequential edits | **16/16** |
| N | interleaved edits | **8/8** |
| O | refusal / clarification | **48/48** |

Sixteen of the eighteen cases are **8/8**. Two are not, and they share one
mechanism.

---

## 4 — THE ONE MECHANISM: an axis is not an extent

Every creation and edit failure in 240 calls — **15 of them** — is the same
confusion, and it is precise.

### ED-02 — 21/32, and 11 of 11 failures are byte-identical

> *"Put a 6 mm diameter hole all the way through the cylinder along its
> axis."*

The fixture's cylinder is Ø20, 30 long, its **axis at x = 100**, so it spans
**x 90…110**. Every one of the eleven failures wrote:

```json
{"type": "through_hole", "target": "pin",
 "parameters": {"diameter": 6.0, "position": {"x": 110.0, ...}, "axis": "+Z"}}
```

`x = 110` is the cylinder's **outer edge** — its axis plus its radius. The
bore is centred on the rim, half of it outside the material, and it cuts a
**half-moon notch** rather than a hole: 9027.7241 mm³ instead of
8576.5479. All eleven produced that identical volume. The successes write
`x = 100.0`.

### CR-06 — 28/32, and 4 of 4 failures are the same thing

> *"a 60 × 40 × 8 mm plate, a 16 mm cylinder 20 mm long beside it…"*

The plate spans x 0…60. All four failures put the Ø16 cylinder's **axis at
x = 60** — the plate's edge — so it spans 52…68 and **half of it lies
inside the plate**. Volumes, face counts, declarations and body count are
all correct; the bodies interpenetrate.

### The finding

> **The model does not reliably distinguish a body's AXIS coordinate from
> an extreme of its extent**, and it fails in the direction of the extent.
> On ED-02 it puts a bore's axis at the target's own outer edge; on CR-06
> it puts a cylinder's axis at the neighbour's edge.

Both are arithmetic on a body **away from the origin**. Every earlier
multi-body case had at most one such body; this corpus has several, which
is why the defect had not been seen before.

It is **systematic in mechanism and stochastic in rate**: 11/11 and 4/4
identical, at 34 % and 12.5 %.

### Why no prompt arm was run here

Deliberate, and the brief's own stop rule. Stage 77 is a measurement
milestone; a proper experiment is a pre-registered rule, one variable, an
exploratory arm, a fresh confirmation and same-session controls — Stage 70's
and Stage 75's protocol — and that is its own stage. The mechanism is
recorded precisely enough to design it. See §8.

---

## 5 — RF-04 is RETIRED, and the model was right 8/8

> *"Make the second one bigger."* on a plate / boss / rod part.

The case assumed an ordinal is not a body reference and required the reply
to name all three bodies. **It is wrong.** `revision_context` hands the
model the whole plan **in order**, so "the second one" picks out exactly one
body — and on all eight attempts the model resolved it to `boss`, which *is*
the second, declined, wrote no operations, built nothing, and asked the one
genuinely open question:

> *"The boss is a cylinder with diameter 16 mm and height 20 mm. Should I
> increase the diameter, the height, or both?"*

That is a better answer than the case demanded. RETIRED **verbatim** with
its note, its eight attempts preserved in `baseline.json` and excluded from
every denominator. Editing an expectation after seeing a score is how a
corpus stops measuring anything.

---

## 6 — RF-02 is 32/32, where Stage 75's equivalent was 34/48

Stage 75 Phases D and E measured the nonexistent-body clarification (R2) at
**34/48 = 70.8 %** and spent 360 calls establishing its mechanism. The same
shape of case here — a request naming `flange` on a part whose bodies are
`cube` and `pin` — is **32/32**.

**Same prompt, same schema, same model.** Fisher exact, two sided:
**p = 0.0005**.

So the difference is the **request and the fixture**, not the prompt. That
does not make Stage 75's number wrong; it makes it a number about *that*
case. Stage 68 found the same thing when its two golden requests, describing
the same part, scored 0/8 and 7/8.

**Do not read this as R2 being fixed.** It is a different fixture and a
different noun, and the honest statement is that the failure is sensitive to
both.

---

## 7 — kernel and product evidence

### Both kernels, from the model's own recorded answers

Every claimed success was parsed again from its raw text, built again, and
compared against the **closed forms** — never against the arena's verdict:

| | |
|---|---|
| rebuilt | **177** |
| CadQuery 2.8.0 matches the closed form | **177/177** |
| FreeCAD 1.0.0 matches the closed form | **177/177** |
| the two agree body for body (volume, faces, edges) | **177/177** |

### The browser, with a live model — `npm run e2e:live`

Real Chromium, real WebGL, real FreeCAD 1.0.0, and **a real provider**:
every geometry turn asserts `metadata.model` and `metadata.prompt_fingerprint`
are the identity above, so a deterministic fallback fails the step instead
of passing it. Eight steps, **all pass**, 0 server errors, 0 console errors:
two bodies created; the cube edited and the cylinder untouched; the cylinder
edited and the cube still carrying the earlier change; *"make it 20 mm
taller"* **refused** naming both; *"what is the volume of the pin?"* answered
`pin: …` and `MEASURED`; *"the total volume"* `CALCULATED` and equal to the
sum; the STEP verified **by its own bytes** (2 `MANIFOLD_SOLID_BREP`, both
ids present as `PRODUCT`s); and three bodies created.

This is **one attempt per step**, so it is evidence that the product path
carries a live multi-body answer end to end — not a rate. The rates are §3.

---

## 8 — findings that are not about the model

1. **The HTTP app never bridges the credential.** Every arena calls
   `config.bridge_credential()`; `cad_experimental/app.py` does not. In
   Claude Code Web the credential arrives as `CAD_ANTHROPIC_API_KEY` and the
   SDK reads only `ANTHROPIC_API_KEY`, so **the experimental server cannot
   reach a model at all** unless the operator exports it by hand — which is
   what this stage did to run the browser E2E. Recorded, **not fixed**:
   changing the product mid-measurement would mean the browser run tested
   something other than what the corpus measured. The fix is one line in
   `app_from_environment`.
2. **A mutation sweep that disables a safety check performs the unsafe
   action.** Mutant 32 disables the arena's refusal to write into an earlier
   baseline, and the test driving it then wrote a file into Stage 75's
   immutable directory for real. The test now cleans up after a disabled
   guard.
3. **The preflight paid for itself before any live call.** It found that
   growing the cube to 60 mm along X overlapped the pin at its fixture
   position, so `disjoint` on the edit turns was an unstated assumption. The
   fixture moved and a test pins it.

---

## 9 — how it is kept honest

- **Truth takes a case NAME and a turn INDEX.** `expected()` and
  `expected_turn()` have no parameter a plan, a shape, an answer or a file
  could enter by, and `ground_truth77` imports `math` and `typing` and
  nothing else.
- **The preflight**: every creation and edit turn has a developer-written
  reference plan, built on a real kernel, observed through the live run's
  observer and graded by the live run's grader. **18/18 on both engines.**
  Without it, every trap test could be passing because the grader rejects
  everything.
- **33 mutants, 33 caught.** Four survived the first sweep: two were real
  gaps with no trap isolating them — a right total at the right body *count*,
  and each of the two placement branches — and two were tests checking a
  neighbouring field.
- **Measurement and export are post-conditions.** A part that did not build
  is `NOT_ASSESSED`, never a measurement failure, and the two are counted
  apart.
- **`P:export_identity_unproven` is informational**, attached to all 96
  multi-body exports and excluded from every failure count. Level F proves
  each id reached the file as a `PRODUCT` and the volumes are right; which
  solid carries which name is **not** proven.
- **Provider-neutral.** `evaluate77` and `ground_truth77` import no vendor
  SDK and no provider module; only `arena77` constructs one. The same cases
  and the same scoring run against a different provider by changing the
  adapter.

---

## 10 — the commands

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:docs/evaluation-baselines/stage76-observation:docs/evaluation-baselines/stage77-multibody-corpus
export CAD_FREECAD_HOME=/root/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib      # BEFORE python starts

cd docs/evaluation-baselines/stage77-multibody-corpus
python3 selfcheck77.py                 # 18/18 on every available engine
python3 mutation_test_77.py            # 33/33
python3 arena77.py --check             # identity, case count, call count
python3 regrade77.py baseline.json widened.json --out results.json
python3 verify77.py baseline.json widened.json --out kernel-verification.json

# LIVE. Spends real provider calls; --live is required.
python3 arena77.py --live --calls 8 --out baseline.json
```

Browser, with the credential bridged into the backend's own process:

```sh
cd apps/web-experimental && npm run e2e:live
```
