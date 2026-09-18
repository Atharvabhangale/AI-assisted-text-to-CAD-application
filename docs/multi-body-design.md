# Multi-body semantics — the smallest real vertical slice

**Status: design only. Nothing here is implemented.** Written at Stage 63,
against the code as it actually stands, so every "already exists" below is a
fact you can check rather than a plan.

The point of writing it before building it: multi-body is the first change
that makes the language describe something the V1 document cannot express at
all, and the tempting shortcuts — fusing bodies to keep the count at one,
or picking `bodies[0]` — are both silent. Stage 62 found the second one
already in the code.

---

## 1. Why this is not "just remove the single-solid rule"

**A part is exactly one solid today, and three separate layers say so.** Any
design that ignores one of them produces a plan that passes here and fails
there.

| layer | rule | where |
|---|---|---|
| V1 document | **S9** — after the last feature, exactly one solid | `cad_core.validator`, on the converted document |
| graph executor | **`multiple_solids`** — more than one live body is a failure | `executor.finished` (added Stage 62) |
| render / export | `result.part` is the single live body **or `None`** | `build.py` |

The executor gate is *three days old* and was added for a good reason: before
it, a `union` plan that left an orphan reported `succeeded=True` and
everything downstream kept only `bodies[0]`, so the extra solid vanished from
the render, the measurement and every export with nothing saying so.

**So the slice must not weaken that gate. It must give the plan a way to
SAY "two bodies is intended", and refuse exactly as before when it does
not.** A leftover solid and a declared second body are different facts, and
the whole design turns on keeping them apart.

---

## 2. What already exists

Stage 46 built the groundwork and deliberately stopped short of using it.
`history.Body` is already per-body and already complete enough:

```
Body.id            str              the body's identity
Body.origin        str              the constructive operation that created it
Body.origin_index  int              where that was in the plan
Body.features      Tuple[str, ...]  its ordered feature history
Body.live          bool             still standing?
Body.consumed_by   Optional[str]    what consumed it, if anything
```

`PlanHistory` already offers `bodies`, `live_bodies`, `body(id)`,
`owner_of(operation_id)`, `terminal_solids` and `consumed`. **Nothing in the
walk assumes one body** — it has always been able to describe two. What is
missing is permission for two, and semantics for everything downstream of
that.

`executor.ExecutionResult` already holds `bodies: Tuple[ExecutedBody, ...]`
and `shapes: Dict[str, Any]` — one shape per live body. The executor is
already multi-body internally. Only `finished()` and `part` collapse it.

**This is why the slice is small.** Identity and body-local history are
largely done; the work is the declaration, the scoping, and the four
downstream semantics.

---

## 3. The slice

Six concerns, in dependency order. Each states the smallest thing that is
*real* — no placeholders, no fields that mean nothing yet.

### 3.1 Distinct body identity — `part` as a declaration

A body is already identified by the id of the constructive operation that
created it. The gap is that nothing says a body is *meant* to be separate.

**Add one operation: `part`.** It declares that a named live body is an
intended, independent body of the result.

```json
{"id": "declare_lid", "type": "part", "target": "lid"}
```

- It produces no geometry and consumes nothing. It is a **declaration**, the
  first operation in the language that is purely one.
- `target` must name a live solid at that point (the existing reference
  rules apply unchanged).
- **A plan with no `part` operation means exactly what it means today: one
  body.** So every existing plan, corpus case and baseline is untouched by
  construction — the change is additive, and a test should assert that the
  eleven-type schemas and every recorded fingerprint are unaffected until a
  plan actually uses it.

**Why a declaration rather than inferring from "two live bodies":** because
inferring is exactly the silent behaviour Stage 62 removed. Two live bodies
with no `part` is a **mistake** — a leftover — and must keep failing with
`multiple_solids`. Two live bodies where both are declared is a **part with
two bodies**. The plan must be able to state which it meant, and the
difference must be visible in the plan text.

**New rules** (the numbering continues from P32):

| rule | says |
|---|---|
| **P33** | a `part`'s `target` names a solid that is live at that point |
| **P34** | a body is declared at most once |
| **P35** | if any `part` is declared, **every** live body at the end must be declared. Mixing declared and undeclared bodies is the ambiguous case, and it is refused rather than guessed |
| **P36** | bodies in `[1, 8]`. A cap for the same reason `count` is capped at 64: an experiment, not a production assembly tool |

`executor.finished` then refuses more than one live body **unless** each is
declared — same gate, one exemption, and the exemption is explicit in the
plan.

### 3.2 Body-local history — already true, now asserted

`Body.features` is already the per-body feature history, and Stage 46's
*derived* edges already order modifiers per body rather than per plan (the
`plate → bore → mount → mounts → break` bug). Multi-body needs nothing new
here; it needs the property **tested** across bodies:

- a modifier on body A must not appear in body B's `features`;
- `topological_order()` must stay a valid order for the whole plan while each
  body's own features stay in their relative order;
- two bodies built from independent chains must produce the same shapes
  whichever order the plan interleaves them in. **This is the real test of
  body-locality** and it is cheap to write.

### 3.3 Placement — absolute, and nothing new

**Nothing is added.** Every position in this language is already absolute in
one right-handed part coordinate system, and a second body is placed by
putting its constructive operation where it belongs — exactly as the six
plates of the hollow box already are.

**Explicitly deferred:** body-local frames, transforms, `move`/`rotate`
operations, mates, joints, assembly constraints. Each is a real feature and
each needs its own stage. The slice is not an assembly system, and saying so
here is what stops it becoming one by accident.

### 3.4 Selection scope — the one genuinely new hazard

Today a selector is resolved against *the* body's edges. With two bodies,
`{"select": "straight", "axis": "Z"}` on body A must never match an edge of
body B.

This is already structurally safe and must be **asserted, not assumed**:
`executor` holds one backend shape per body and calls
`backend.describe_edges(target)` on the target body's shape alone, so the
facts handed to `edge_semantics.resolve` cannot contain another body's edges.

What the slice adds:

- a test that a selector on A matching *n* edges still matches *n* when an
  unrelated body B is added to the plan — the selector's result must not
  depend on what else exists;
- **R1's message must name the body.** "matched no edge" is unhelpful when
  there are two bodies; "matched no edge on `lid`" is actionable. The
  resolution codes stay as they are; only the message gains the body.

### 3.5 Visibility — deliberately NOT in the slice

Visibility is a **view** concern, not a document concern. Putting a
`visible` flag in the plan would make the canonical representation carry
presentation state, and the plan is the source of truth for *what the part
is*.

The slice therefore: the render payload carries **every** declared body, each
tagged with its body id, and the viewer decides what to show. If per-body
visibility is later wanted as saved state, it belongs in the session, not the
plan — and that is a separate decision to make deliberately.

### 3.6 Export and render — the semantics that must not be silent

This is where `bodies[0]` lives, and where the damage would be.

| output | one body (today) | declared multi-body |
|---|---|---|
| **RenderModel** | one mesh | **one mesh per body**, each carrying its `body_id`. Never a merged mesh — merging is a fuse the plan did not ask for |
| **measurement** | one `Measurement` | **per body**, plus explicit totals. `volume` must never silently become "the first body's volume" |
| **STL** | one mesh | one file **per body**, or one multi-solid file — chosen explicitly and recorded, the way `resolve_backend` and the schema variants already are. Never a default that fuses |
| **STEP** | one solid | a STEP **assembly** of the declared bodies. STEP supports this natively; a single-solid STEP of a two-body part would be a lie about the geometry |
| `result.part` | the body, or `None` | **stays exactly as it is** — the single live body or `None`. Callers wanting all bodies ask for `bodies`. Widening `part` to mean "the first one" is the Stage 62 bug by another name |

**The rule under all five rows: no output may fuse bodies the plan did not
fuse, and no output may drop one.** A `union` is the only thing that joins
solids, and it is written in the plan.

---

## 4. What the slice deliberately does not do

- no transforms, mates, joints or constraints;
- no per-body visibility in the plan;
- no body groups, sub-assemblies or nesting;
- no interference or clearance checking;
- no bill of materials;
- no change to any recorded fingerprint, corpus, or baseline;
- **no new model-facing grammar until the slice works deterministically.**
  Stage 63 measured the model failing P11 on a *one*-body union 4/5; adding a
  `part` branch to the live encoding before that is understood would be
  measuring two unknowns at once.

---

## 5. Order of work, and the gate on each step

1. `part` in the plan vocabulary, parser, and P33–P36 — **plus a test that
   every existing schema fingerprint is unchanged.**
2. `PlanHistory` reports `declared_bodies`; `executor.finished` gains its one
   exemption. Gate: a leftover solid still fails `multiple_solids`, proven by
   the Stage 62 orphan test passing untouched.
3. Body-local history tests (§3.2) and selector-scope tests (§3.4). Gate:
   interleaving two independent chains changes no shape.
4. Per-body measurement and RenderModel. Gate: a two-body part reports two
   meshes and two measurements, and closed-form volumes for both.
5. STEP assembly and the explicit STL choice. Gate: a round-trip read of the
   STEP returns two solids.
6. Only then: the deterministic reader, and only then the prompt and schema.

**Each step is committable on its own and leaves the single-body product
working unchanged** — which is the test of whether the slice really is a
slice.

---

## 6. The one measurement that would justify starting

Nothing above is worth building if the product cannot already describe a
two-body part deterministically. The cheapest check, before step 1:

> take the six-plate hollow box, delete the `union`, and ask the executor to
> build it.

**Run at Stage 63, and it passes:**

```
operations      : bottom, top, front, back, left, right  (six boxes)
plan validates  : True   (no P-code -- the PLAN is fine)
live bodies     : ['bottom', 'top', 'front', 'back', 'left', 'right']
executor built  : False
failure code    : multiple_solids
failure message : the plan leaves 6 separate solids ('bottom', 'top',
                  'front', 'back', 'left', 'right'); a part is exactly one.
                  Join them with a `union`, or remove the ones that are not
                  part of it
names every body: True
result.part     : None
```

Three things that matter for the slice, all confirmed rather than assumed:

- the **plan** is valid and only the **execution** refuses, which is the
  right layer — being six bodies is a fact about the built result, not a
  malformed plan, and `part` will make it a legal result rather than making
  an illegal plan legal;
- the refusal **names every body**, so the message needs no work when a
  declaration is added beside it;
- `result.part` is `None` rather than `'bottom'`, so nothing downstream is
  already quietly taking the first body.

This is exactly the behaviour the slice must **preserve** for the undeclared
case. Step 1 is safe to start.
