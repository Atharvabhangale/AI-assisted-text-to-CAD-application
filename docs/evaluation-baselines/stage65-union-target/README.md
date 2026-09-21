# Stage 65 — eliminating the live-model P11 union-target failure

**45 live calls** to `claude-haiku-4-5-20251001`, nine arms, one variable at
a time. Every arm's raw model output, validation result and build result is
in `arm-*.json`. Nothing here is recalled; nothing was repaired, retried or
re-prompted.

## The failure

The model gave the `union` a product-sounding id (`assembly`, `box_assembly`,
`shell`) and then pointed every later `through_hole` at **that** id instead of
at the union's `target` — the solid that actually survives. Validation
refused it with **P11**, every time. The union's own `target` and `tools`
were **correct on every attempt**; only the later reference was wrong.

## The matrix

| arm | request | n | compiled | union | P11 | **targets surviving body** | built | PASS |
|---|---|--:|--:|--:|--:|--:|--:|--:|
| `A0-baseline` | six-plate box | 5 | 5 | 5 | **5** | 0 | 0 | 0 |
| `A1-worked-example` | six-plate box | 5 | 5 | 5 | **5** | 0 | 0 | 0 |
| `B1-step-naming` | six-plate box | 5 | 5 | 4 | 4 | 0 | 0 | 0 |
| `C1-schema-target-description` | six-plate box | 5 | 5 | 4 | 4 | 0 | 0 | 0 |
| `D1-repair-contradiction` | six-plate box | 5 | 5 | 5 | 4 | 0 | 1 | 0 |
| `D2-everything` | six-plate box | 5 | 5 | 5 | **5** | 0 | 0 | 0 |
| **`E1-mandate-verb-id`** | six-plate box | 5 | 5 | 5 | **1** | **4** | 0 | 0 |
| `X1-two-plate-diagnostic` | two-plate bracket | 5 | 5 | 5 | **5** | 0 | 0 | 0 |
| **`E1-simple-request`** | two-plate bracket | 5 | 5 | 5 | 3 | **2** | **2** | **2** |

`PASS` = built **and** the post-union feature targets the surviving body
**and** the result is not a degenerate slab.

## What each arm tested, and what it showed

- **A1 — the missing worked example.** The prompt's only worked example used
  `subtract`; there was no union example at all. Adding one, with an action
  id and an explicit "NOT the union's id" note: **no effect, 0/5.**
- **B1 — the id-naming advice.** "give each operation an id that says what it
  is" produces a product noun on a union. Rewriting it to name modifiers for
  their step: **no effect on the target error, 0/5.**
- **C1 — say it in the schema.** The provider schema carries **zero**
  descriptions: `id` and `target` are the same bare `$ref`, so at the moment
  the decoder writes a target the grammar says nothing. Adding a `description`
  to every `target` (3628 → 4207 inlined, still under the 4481 accepted
  ceiling): **no effect, 0/5.**
- **D1 — the contradiction.** The union section closed with *"a `through_hole`
  bores through the shell, **not through a loose plate**"* — naming the
  post-union solid with a product noun that is no operation's id, and warning
  against the **one legal target**, since the union's target is by name a
  loose plate. Removing it produced the run's **first build**, but that build
  put every hole *before* the union and, with no positions, fused six
  coincident plates into a single 40×20×5 slab: **valid, buildable, and not
  the part asked for.** 0/5 correct.
- **D2 — all of the above together.** **0/5.** More prompt did not help.
- **E1 — remove the affordance.** D1's repair **plus** mandating the union's
  own id be the verb `fuse`. **P11 5/5 → 1/5; post-union targeting 0/5 →
  4/5.**
- **X1 — is it the rule or the complexity?** The simplest possible union
  request — two plates, one hole — on the **unmodified** prompt: **5/5 P11.**
  So it was never complexity. It was the rule.

## The controlled confirmation inside the run

E1's single remaining P11 is the attempt that **ignored the mandate** and
named its union `shell` — a body noun — and regressed immediately. The four
that wrote `fuse` all targeted correctly. The id's *shape* is the mechanism.

## The verified success

`E1-simple-request`, attempts 2 and 3, identical plans:

```
base   box
wall   box
fuse   union         target=base  tools=[wall]
hole   through_hole  target=base        <-- the surviving body
```

Rebuilt from the model's own recorded output on **real FreeCAD 1.0.0**:

| | |
|---|---|
| validates | **True**, no problems |
| built | **True**, part `base` |
| volume | **20748.67258771281** mm³ |
| closed form | `60·40·5 + 60·5·30 − π·4²·5` = 20748.672587712816 |
| delta | **3.6e-12** |
| solids / faces / edges | 1 / 9 / 21 |

`MODEL_GENERATED` — not deterministic, not a fixture.

## What is NOT fixed

The **six-plate hollow box still does not build.** With E1 the union-target
rule is satisfied 4/5, and the failures moved to a different and harder
problem: **rule E1 — "the hole's centreline does not intersect the target"**.
The model is not positioning six plates into a closed shell correctly. That
is a spatial-arrangement failure, not the union-target failure this stage
set out to remove, and it is unmeasured territory.

## Reproducing

```sh
export PYTHONPATH=apps/api/src:packages/cad-core/src:<this directory>
cd apps/api
# --live is required; a credential's presence never starts a run
python3 arena.py --arm A0-baseline --calls 5 --live
python3 arena.py --arm E1-mandate-verb-id --calls 5 --live
```

`variants.py` computes each arm as a delta from the **baseline** prompt. Now
that E1 is committed as prompt `2026-09-18.2`, the A–E deltas no longer apply
to the current baseline; they are kept as the record of what was measured.

**No credential value appears in any file here.** `credential_from` records
the variable *name* only.
