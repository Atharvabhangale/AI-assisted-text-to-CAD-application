# Stage 71 — multi-body, step 1: distinct body identity

The first step of `docs/multi-body-design.md`, and only the first. It adds one
operation — **`part`** — that declares a live body is an intended body of the
result, and the semantics that follow from taking that seriously. It is not an
assembly system: no transforms, no mates, no constraints, no BOM, no
inter-body booleans.

## 1 — the architecture, and the one decision that shaped it

**`part` is a declaration, not an inference.** Two live bodies and no `part`
is still a mistake and still fails with `multiple_solids`. Inferring "they
must have meant two" from two live solids is precisely the silent behaviour
Stage 62 removed. A leftover and a declared body are different facts; the
plan says which, and a reader can see it.

**`part` is deliberately NOT in `OPERATION_TYPES`.** That tuple is the
**geometry vocabulary** every provider encoding and the prompt are built
from. An entry there would have moved `plan_schema`, `provider_schema`,
`compact_provider_schema` and the **prompt** fingerprint in one edit —
teaching a live model a grammar whose semantics are not yet measured, which
the design refuses outright. So the vocabulary has two tiers:

| tuple | members | what it is |
|---|---|---|
| `OPERATION_TYPES` | 11 | the geometry language. Schemas and the prompt derive from it |
| `DECLARATION_TYPES` | `('part',)` | produces no geometry; declares something about the result |
| `PLAN_TYPES` | 12 | what the parser accepts |
| `BUILDABLE_TYPES` | `EXECUTABLE_TYPES + DECLARATION_TYPES` | what the executor may carry |
| `EXECUTOR_ONLY_TYPES` | `('union', 'part')` | **derived** — no V1 document form, so routing needs no second edit |

Every recorded fingerprint is therefore unchanged **by construction**, and
`test_multi_body.py` pins all nine of them plus the prompt.

## 2 — the canonical representation

```json
{"id": "body_cube", "type": "part", "target": "cube"}
```

One reference and nothing else — no `parameters` key, like `subtract` and
`union`. The result keeps the target's id, as every reference in this
language does, and the declaration's own id names nothing.

| concern | answer |
|---|---|
| **body identity** | the id of the constructive operation that made it, for life. Already true since Stage 46 |
| **feature ownership** | `Body.features`. A `part` joins **no** body's feature list — it changes nothing |
| **body-local history** | Stage 46's derived edges, now asserted **across** bodies |
| **live-body set** | `PlanHistory.live_bodies`, from the one solid-set walk. No second walk was added |
| **declared set** | `PlanHistory.declared_bodies` — reported, never judged, exactly like `terminal_solids` |
| **selection scope** | the executor holds one shape per body and asks the backend about the target's shape alone. Now **asserted**, not assumed |
| **visibility** | **not in the plan.** The payload carries every body; the viewer decides |
| **RenderModel** | **one mesh per body**, each tagged with its `body_id`. Never merged |
| **export boundary** | a multi-body part is **refused**, by name, rather than exported as its first body |

### The rules

`P33`–`P35`. The design proposed four; the first — "a `part`'s target names
a solid live at that point" — turned out to be the reference rules `P9`–`P12`
verbatim, so it was **not added**. A declaration's target is a reference like
any other, and a fourth code restating it would have been a second opinion
about what "live" means.

| rule | says |
|---|---|
| **P33** | a body is declared at most once |
| **P34** | the declared bodies are exactly the live ones at the end — neither an undeclared body left standing, nor a declaration of something later consumed |
| **P35** | at most `MAX_BODIES` (8) |

`executor.finished` keeps its gate and gains **one** exemption: more than one
live body passes only when every one is declared.

## 3 — the two-body example

> Create a 40 mm cube and a 20 mm cylinder 30 mm long beside it as two
> separate bodies.

Read by `normalize.read_separate_bodies` — a provider-neutral grammar, no
model, no SDK — into four operations: `box`, `cylinder`, `part`, `part`.

## 4 — kernel results, both engines

| body | volume | closed form | solids | faces | edges | envelope |
|---|--:|--:|--:|--:|--:|---|
| `cube` | **63999.999999999985** | 40³ = 64000 | 1 | 6 | 12 | (0,0,0)–(40,40,40) |
| `pin` | **9424.777960769377** | π·10²·30 = 9424.777960769379 | 1 | 3 | 3 | (50,10,0)–(70,30,30) |

**Bit-identical on CadQuery 2.8.0 and FreeCAD 1.0.0.** The total is the sum
of the two closed forms, so nothing was fused; the cylinder sits clear of the
cube (cube ends at x=40, gap 10, cylinder starts at 50).

## 5 — edit isolation

Proved on the kernel, not asserted in prose:

- a `through_hole` on `cube` leaves `pin` identical in volume, faces, edges
  and solid count, and appears in `cube`'s feature list only;
- rebuilding `cube` at 60 mm leaves `pin` identical;
- two independent chains **interleaved in either order** produce the same
  shapes — the real test of body-locality;
- a `straight Z` fillet on `cube` selects the **same edge indices** whether
  or not `pin` exists in the plan.

The product does not yet let you *name* a body in an edit request:
`normalize._body_id` returns `None` unless exactly one body is live, so every
general edit reader declines a multi-body part rather than guessing which
body was meant. That is the honest behaviour and it is the next milestone.

## 6 — browser

`npm run e2e:multibody` — real Chromium, real WebGL, real FreeCAD 1.0.0,
**no model configured**:

```
bodies      : cube, cylinder
ok the payload carries two bodies      ok both bodies are declared in the plan
ok cube: one solid                     ok cube: volume 63999.999999999985
ok cylinder: one solid                 ok cylinder: volume 9424.777960769377
ok no single part-level measurement is claimed
ok no merged mesh was sent             ok each body carries its own mesh
ok the mesh note names cube            ok the mesh note names cylinder
ok the viewport has a live WebGL surface
ok no failed requests                  ok no console errors
MULTI-BODY E2E: PASS
```

`DETERMINISTIC` — a local grammar read the request. **This says nothing
whatever about model quality**; it is a statement about the architecture.

## 7 — single-body regression

`npm run e2e:assembly` still passes on the golden enclosure: volume
**11492.035526276897**, 18 faces, 42 edges, 5544 triangles, and the mesh note
reads exactly as it did before. `test_multi_body` rebuilds the same enclosure
and asserts `declared == ()` and `part == "base"`.

## 8 — files

| file | what |
|---|---|
| `mutation_test.py` | reintroduces each defect and asserts the guard goes red. **8/8 caught** |

The implementation is in `cad_experimental/{plan,parser,history,validation,executor,build,normalize,app}.py`,
the frontend in `apps/web/src/viewer.ts` (`showBodies`) and
`apps/web-experimental/src/{api,main}.ts`, and the tests in
`apps/api/tests_experimental/test_multi_body.py`.

## 9 — what is NOT done

- **no transforms, mates, joints or constraints**, no sub-assemblies, no BOM,
  no interference checking, no exploded views;
- **no multi-body export.** STEP-assembly and the explicit STL choice are
  step 5 of the design; until then `/session/export` and the drawing and
  engineering surfaces **refuse** a multi-body part by name rather than
  silently writing its first body;
- **no model-facing grammar.** No encoding a provider is pointed at admits a
  `part` branch, and the prompt does not mention it. Stage 70 measured P11 on
  a *one*-body union at 5.6 %; adding a `part` branch before that is
  understood would measure two unknowns at once;
- **no way to edit one body by name** — see §5.
