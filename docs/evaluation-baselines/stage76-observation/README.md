# Stage 76 — the multi-body observation layer

**Nothing here is a result about a model.** No live call was made, none was
configured, and every number below is `DETERMINISTIC`: seven parts built
from plans written by hand (one of them by the product's own deterministic
reader), measured and exported by two real kernels. That says a great deal
about the **observer** and nothing whatever about a model.

What this stage delivers is the instrument `corpus-design.md` §4 said the
broader multi-body corpus could not start without:

> Dimensions **8 and 9 need a second observer**, because they are not
> properties of a plan. A measurement question goes through
> `questions.answer` and an export through `/session/export`, and neither is
> visible in a `PlanGenerationResult`. Building that observer is the first
> piece of work the broader corpus needs, and it should be built and
> mutation-tested **before** any live call, exactly as Stage 75's was.

---

## 1 — what is here

| file | what it is | what it may import |
|---|---|---|
| `ground_truth76.py` | the immutable truth: closed forms, probes, export ladder | `math`, `typing`, **nothing else** |
| `fixtures76.py` | the deterministic plans, and the corrupted observations (the traps) | the truth, and the reader for X7 |
| `observe76.py` | the observer: asks the probes, writes the file, inspects the artefact | the product, and `probes_to_ask` only |
| `browser76.py` | the same observation read off the payload a **browser** receives | nothing from the product at all |
| `evaluate76.py` | **the only module that reads `expected()`** | the truth |
| `run76.py` | the offline harness. Calls no model | all of the above |
| `mutation_test_76.py` | 28 mutants, each disabling one guard | — |
| `offline-cadquery.json`, `offline-freecad.json` | the recorded evidence | — |

Tests: `apps/api/tests_experimental/test_stage76_observation.py` (67).

```sh
export PYTHONPATH=packages/cad-core/src:apps/api/src:docs/evaluation-baselines/stage76-observation
export CAD_FREECAD_HOME=/root/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib      # BEFORE python starts

cd docs/evaluation-baselines/stage76-observation
python3 run76.py --check                 # both engines, writes nothing
python3 run76.py --engine cadquery --out offline-cadquery.json
python3 mutation_test_76.py              # 28/28
python3 mutation_test_76.py --list       # what it would do, changing nothing
```

---

## 2 — the four sections, and why they are kept apart

`observe76.observation` produces one object with four sections and the
SOURCE on top. It refuses to build an observation labelled
`MODEL_GENERATED` that carries no model output, and refuses one labelled
anything else that does — because everything this stage produces is
deterministic, and the single most damaging thing the instrument could do is
let one of its own fixture runs be read afterwards as evidence about a
model.

| section | what it holds | `None` means |
|---|---|---|
| **MODEL OUTPUT** | what a model said, when one was asked | no model was asked — **not** "a model said nothing" |
| **GEOMETRY** | per-body measurement from the kernel, keyed by id | — |
| **MEASUREMENT** | one row per probe: outcome, which body, provenance, value, the words | — |
| **EXPORT** | the artefact: bytes, solids, product names, volumes | the route cannot export |

---

## 3 — measurement: the five resolver cases, asked of a real product

Every probe goes through `questions.answer` with the arguments `app.py`
passes, and which body an answer is about is read from
`questions.scope_for` — the **same call** `answer` makes. It is never
inferred from the number, and never from the order.

| probe kind | what must happen |
|---|---|
| names a live body | answered **about that body**, with that body's number |
| names none, one live | answered, **no id prefix at all** — the pre-Stage-73 wording |
| names none, several live | **REFUSED**, naming every live body |
| names two at once | **REFUSED** |
| names a consumed body | **REFUSED**, saying what consumed it |
| asks a total | answered, **`CALCULATED`** — no kernel weighed them together |
| asks the overall size | answered, **`ASSUMED`** — that box holds the air between the bodies |

### The seven cases

| case | what it is | why no other case covers it |
|---|---|---|
| **X1** | cube + pin, disjoint | the base: two volumes a factor of seven apart |
| **X2** | block + rod + shim | **three** bodies — code written for "the other body" passes a two-body test and fails here |
| **X3** | one cube | the single-body regression, byte-for-byte |
| **X4** | plate+boss fused, pin standing | **two live and one consumed** — the only shape in which the fifth resolver case can fire at all |
| **X5** | two overlapping boxes fused | the control: its volume is neither box and is **not** their sum |
| **X6** | two **identical** cubes | value cannot disambiguate them even in principle |
| **X7** | the product path | its plan is what `normalize.read_separate_bodies` makes of the browser e2e's own sentence |

---

## 4 — export: six rungs, and only the top is a success

`file exists` is **level B at best**. That is structural, not a rule to
remember.

| rung | means |
|---|---|
| **A** `not_written` | the writer raised, or left no file |
| **B** `unreadable` | bytes on disk that are not a readable STEP |
| **C** `wrong_solid_count` | 0 = an empty well-formed file, 1 = fused, else dropped or invented |
| **D** `names_missing` | right count, at least one body id never reached the file |
| **E** `geometry_mismatch` | right count and names, and a solid is not the one built |
| **F** `verified` | right count, every id present, every volume matching its closed form |

**The observer forms the rung from the ARTEFACT, not from the writer's own
verdict.** A `verify_assembly` that passed is the product agreeing with
itself; a file on disk is evidence. The writer is still run, and whether it
raised is recorded beside the rung rather than instead of it.

**Not assessed is not failed.** A browser response carries the solids and
the names and nothing that measures, so rung E cannot be *asked* there. The
level stops at D and the verdict says `geometry_assessed: false` with the
reason. `browser76.measure_bytes_with` is the honest way past it: the
artefact under test is still the bytes the person downloaded, and only the
instrument reading them is a kernel — recorded as `volumes_measured_by`.

---

## 5 — measured findings, none of them assumed

### 5.1 The product's body-name check is a substring scan, and it is weak

`cad_backend.verify_assembly` tests each body id with `name not in
written_text` — over the whole STEP file, boilerplate included. Measured on
real two-body files from **both** engines, that check **accepts** every one
of these in place of `cube`:

```
cadquery: verify_assembly ACCEPTS bogus id 'SOLID'
cadquery: verify_assembly ACCEPTS bogus id 'part'
cadquery: verify_assembly ACCEPTS bogus id 'Open'
cadquery: verify_assembly ACCEPTS bogus id 'cub'
freecad : identical — all four accepted
```

`'SOLID'` is in `MANIFOLD_SOLID_BREP`; `'part'` and `'Open'` are in the
header; `'cub'` is a prefix of `cube`. A body legitimately named `part`
would satisfy a check that proves nothing.

**The observer does not inherit it.** It reads the file's own `PRODUCT`
structure instead — measured, both engines write the body id as a product
name:

```
cadquery: PRODUCT('cube','cube',…)  PRODUCT('pin','pin',…)  + a UUID root
freecad : PRODUCT('cube','cube',…)  PRODUCT('pin','pin',…)  + 'cad_experimental_assembly'
```

Both lists are recorded on every export row — `names_found` (strict) beside
`names_found_by_substring` (what the product would see) — so the gap is
visible **in the data** rather than only in this paragraph.

**Not fixed here, deliberately.** Stage 76 is an instrumentation stage; the
product change is a separate decision with its own regression surface, and
`test_step_assembly.py` exports bodies named `a`, `b`, `c` — single letters
that are substrings of any STEP file — so that test's identity property is
vacuous today and tightening the check would fail it. Recorded, with a
reproduction, rather than changed in passing.

### 5.2 The single-body writer puts NO body name in the file

Measured on both engines:

```
cadquery X3  part=cube  products=('Open CASCADE STEP translator 7.9 1',)
freecad  X3  part=cube  products=('Open CASCADE STEP translator 7.7 1',)
```

That is the translator's own string — exactly the anonymous product Stage 74
refuses an *assembly* for. It is not a defect there: a file holding one solid
has nothing to tell apart. But it means **a single-body export can never
reach the name rung on evidence**, so those cases pin `export_names = ()` and
`export_identity = "not_written"`, and their level F is a **weaker claim**
than an assembly's. The verdict says which, on every row.

### 5.3 The answered reply carries no `bodies` list; the refused one does

Measured on `/experimental/session/message`. A `refused` reply carries
`bodies`; an `answered` reply's keys are
`editing, evidence, from_evidence, measurement, reply, session, session_id,
status`. It costs the browser nothing — it already has the list from the
build — but a single answered reply is **not self-describing**, so an
observer reading one in isolation cannot tell a legitimate body prefix from
any other text before a colon. `browser76` threads the vocabulary from the
build payload, which is what the browser has. **Recorded, not patched.**

### 5.4 A defect in this stage's own observer, caught by the offline gate

`_first_number` read the first number in the answer's text. The aggregate
label is `all 2 bodies`, so on *"all 2 bodies: Volume 73424.778 mm3."* it
returned the **body count**. Every per-body probe passed — `cube` and `pin`
carry no digits — and only the three aggregate totals failed: a defect that
fires exactly on the multi-body case the observer exists for, and on nothing
else.

It was caught by running the observer against a real kernel **before any
live call**, which is the whole reason that gate is in the brief. The label
now comes off before the number is read, and the mutation sweep reinstates
the defect as mutant 3.

---

## 6 — kernel evidence, both engines

`DETERMINISTIC`. Seven cases, CadQuery 2.8.0 and FreeCAD 1.0.0:

| | geometry | measurement | export |
|---|--:|--:|--:|
| CadQuery 2.8.0 | **7/7** | **7/7** | **7/7** all `F:verified` |
| FreeCAD 1.0.0 | **7/7** | **7/7** | **7/7** all `F:verified` |

Per-body volumes against their closed forms, identical on both engines:

| body | closed form | measured |
|---|--:|--:|
| `cube` / `block` / `left` / `right` | 64000 | 63999.999999999985 |
| `pin` / `rod` / `cylinder` | 9424.77796076938 | 9424.777960769377 |
| `shim` | 1500 | 1500.0 |
| `plate` (plate ∪ boss) | 22827.433388230813 | 22827.433388230813 |
| `pad` (two boxes fused) | 68000 | 68000.0 |

**Cross-readable both ways**: every file written by one engine reads back
in the other at the same solid count and the same volumes.

**There is no combined rate, on purpose.** `summarise` produces none:
measurement and export are different quantities measured by different means,
and a number that mixes them is one nobody can act on. Stage 75's
`summarise` refuses to pool creation and refusal for the same reason.

---

## 7 — the mutation sweep

**28 mutants, 28 caught.** Each disables exactly one guard and names the
test that must fail; the driver refuses to apply an edit whose anchor is not
present exactly once, so a mutant that silently no-ops fails loudly instead
of surviving.

**Six survived the first sweep, and every one of them was a weak TEST rather
than a missing guard** — the same finding Stage 75 Phase E made twice:

| survivor | why it survived | what was added |
|---|---|---|
| the body inferred from its VALUE | every trap graded a corrupted dictionary; nothing drove `observe76` | a test that runs `measure` on X6, whose volumes are equal |
| names found by SUBSTRING | same | a real export of one body renamed `SOLID` |
| the answer never checked for its body's name | the product never omits the prefix, so the guard could not say no | `questions.answer` patched to return an unprefixed answer |
| per-body volumes compared as a TOTAL | the trap was in the measurement rows, not the geometry section | a geometry observation with the right total and the wrong split |
| the outcome check widened | T6/T7 asserted only that the grade failed — and it failed for the *naming* reason | assert **which** check failed |
| a test-file mutant nothing could catch | weakening an assertion is invisible by construction | replaced with retiring X2, the only three-body case |

---

## 8 — what is NOT here

- **No live model call**, and no evidence about any model. The brief's gate
  is explicit and this stage stops at it.
- **No broader corpus run.** `corpus-design.md`'s nine dimensions are still
  designed and not started; what changes is that **8 and 9 are now
  gradeable**, so the honest scope is no longer "dimensions 1–7".
- **No product change.** §5.1 and §5.3 are findings with reproductions, not
  patches.
- **No Chromium.** `npm run e2e:surfaces` remains the real-browser proof.
  What Stage 76 adds is that the payload a browser receives is graded by the
  same truth and the same grader as everything else, instead of by regexes
  in a script — and that its export check is strictly stronger than that
  script's substring test.
- **Identity binding is never claimed.** Level F proves each id reached the
  file as a `PRODUCT` and that the volume multiset is right. **Which solid
  carries which name is not proven**, and the caveat rides on every row and
  every verdict.
