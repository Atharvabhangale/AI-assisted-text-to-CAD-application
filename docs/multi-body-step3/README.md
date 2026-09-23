# Stages 73/74 — multi-body, steps 4 and 5: measuring it, and exporting it

Steps 4 and 5 of `docs/multi-body-design.md`, which are the last two before
the model-facing grammar could open. Stage 71 made several bodies real and
Stage 72 made them addressable by name for an **edit**. What was missing was
the rest of the product: a part with two bodies could be built and edited but
not **asked about**, and not **exported** at all.

**No live model was called and none was configured.** The evidence below is
`DETERMINISTIC`. A credential *is* present in this container; the backend was
started with it deliberately unset, because no provider encoding admits a
`part` branch, so a model could not have produced these plans and leaving the
key set would only have made the evidence harder to read.

---

## 1 — what was added, and what was deliberately not

**No new operation, no new P-code, no schema change, no prompt change.** The
same as Stage 72, and for the same reason: the canonical plan already carries
everything needed. A body already had an id, a feature list and its own
measurement; what was missing was the surfaces asking for them.

| added | where |
|---|---|
| `questions.Scope`, `scope_for`, `scope_of_body`, `QuestionRefused` | which body a question is about |
| `questions.ASSUMED` | a fourth provenance, used for exactly one thing |
| `Revision.bodies` | each live body's own measurement, in the session |
| `CadBackend.export_step_assembly` / `read_step_solids` | a real multi-body STEP |
| `cad_backend.ordered_bodies` / `verify_assembly` | shared, engine-neutral export rules |
| `DrawingBody.body` | which body a detail drawing is of |

---

## 2 — per-body measurement semantics

`questions.answer` gained one optional argument, `bodies`, mapping each live
body's id to that body's own measurement. The scope is decided **once**,
before any answerer runs, by the same `body_reference.resolve_body` that the
edit readers use — a second implementation here would be a second opinion
about what "the cube" means, and the two would disagree the first time an id
gained a hyphen.

| the question | the answer |
|---|---|
| names a live body | **that body**, from that body's measurement, prefixed with its id |
| names none, and one body is live | **unchanged**, word for word, from before Stage 73 |
| names none, and several are live | **REFUSE**, listing them |
| names two bodies at once | **REFUSE** |
| names a body that was consumed | **REFUSE**, and say what consumed it |
| asks for a **total** | every body, summed, labelled `CALCULATED` |
| asks for the **overall size** of several bodies | every body's box, labelled **`ASSUMED`** |

### Refusing is not declining, and the difference is load-bearing

`questions.answer` returning `None` means *"not a question this module
answers"*, and the request falls through to the model. That is safe.

A question this module **recognises** but cannot answer without picking a
body must not fall through the same way. The model can read the plan but has
never seen the part, so it would answer *"the volume is 64000"* about a part
that has two bodies — and nothing downstream could tell that apart from a
right answer. So the two travel differently: `None` falls through,
`QuestionRefused` reaches the person as a `refused` status.

This mirrors what `normalize` already does for edits, where `_Decline` falls
through and `ReadingError` reaches the person.

### The refusal is phrased for the question it refuses

`resolve_body` gained a `verb` parameter, defaulting to `"change"` so every
existing caller is byte-identical. Questions pass `verb="measure"`. *"Say
which one to change"* in front of someone who asked what the volume is reads
as a refusal to answer rather than as a request to name a body. The
**decision** stays single and shared; only the sentence belongs to the
surface.

### Why `ASSUMED` exists, and why there is exactly one of it

Volume, faces, edges and solids genuinely **add** across bodies: separate
bodies do not touch, so each contributes its own, and the total is
`CALCULATED` with the working shown.

The overall size does not add. The box containing a 40 mm cube and a pin
standing 10 mm away runs **0..70** in x, and the part does not fill it —
there is a 10 mm gap of air in the middle that no kernel measured and no
operation declared. Reporting that as `MEASURED` asserts a solid that is not
there. It is the one number in the module that is neither measured, declared
nor calculated, and it is labelled so.

`ASSUMED` is **not** a general licence to assume. Every other answer still
comes from the kernel, the plan, or arithmetic on the two.

---

## 3 — what this exposed, and had to fix

**The browser still read `bodies[0]`.** `main.ts:201` took
`execution.bodies[0].measurement` and put it in the panel headed
*measurements*. Stage 71 removed that read from the server and this one
survived on the client, so a two-body build showed the **cube's** volume,
envelope and face count as the part's, with nothing saying it was one body's.
The numbers were real; the label was wrong, which is the harder kind of wrong
to notice. The panel now lists each body under its own id.

**Engineering was not refused, and was already wrong.**
`POST /experimental/session/engineering` never went through `_current_shape`,
so unlike the drawing and export routes it was never gated. On a two-body
part it answered **HTTP 200, `answered: true`** with both bodies' holes in one
list and nothing saying which body each came from — and with `measurement`
empty it reported no measured values at all while still claiming an answer.
It is now scoped through the same resolver, and the no-question "tell me
everything" path reports **every body separately** rather than merging them.

---

## 4 — drawing: what is real, and what is refused

A **detail drawing of one named body** is a real drawing and is implemented.
Every view is a true projection of that body, and every dimension comes from
that body's own measurement. `POST /session/drawing` takes a `body`, and the
sheet says which body it is of — a detail drawing that does not name its body
is indistinguishable from a drawing of the whole part.

An **assembly drawing** is refused by name, `HTTP 501`, with
`capability: "assembly_drawing"`.

This is a deliberate line rather than a missing feature. `TechDraw.projectEx`
will happily project a compound of both bodies and return one combined
outline — it was measured doing so, 8 edges spanning x 0..80 — so an
"assembly drawing" was buildable. It would not have been one. An assembly
drawing carries item numbers, balloons and a parts list, and its overall
dimensions are of a box containing the bodies **and the space between them**,
which §2 has already established nothing measured. Shipping a combined
silhouette under that name would have been inventing the semantics this stage
was told not to invent.

---

## 5 — multi-body STEP export

`POST /session/export` no longer refuses a multi-body part for STEP. It
writes a real assembly: every body, none fused, each under its own id, in
**declaration order** so two runs of one plan write the same file. The
response carries `x-cad-bodies` naming what is actually in it.

**STL still refuses**, deliberately. An STL carries one mesh, and
`docs/multi-body-design.md` §3.6 requires the one-file-per-body-versus-one-
multi-solid-file choice to be made *explicitly and recorded*. It has not
been, so this refuses rather than fusing the bodies into one mesh or quietly
writing the first, and the refusal points at STEP, which holds every body.

**The single-body STEP path is unchanged.** It still calls `export_step`, the
proven one. Routing it through the new writer would have been a behaviour
change bought for nothing; a test asserts the two writers agree on a one-body
part so they cannot drift apart.

### The two failures this had to be written against

Both were measured, not imagined.

**1. A file that exists is not an export.** FreeCAD's `Part.export`, handed
raw `Part` shapes, returns and leaves a well-formed **1 640-byte** STEP that
reads back as **zero solids**. It exists, it is non-empty, it parses. Every
check short of counting solids passes on it. The working path is FreeCAD's
`Import.export` over `App::DocumentObject`s, whose `Label` carries the body
id into the file.

**2. A geometrically perfect assembly can still have lost every name.**
Handed a compound, *both* engines write a correct two-solid STEP whose bodies
are called `Open CASCADE STEP translator 7.9 1.1` and `1.2`. Solid count,
volumes, face and edge totals all match. The identity this entire multi-body
slice is about is simply gone, and no count can see it.

So `verify_assembly` reads the written file back and checks **both** the
solid count and that every body id reached the file. An export that lost
either is refused rather than delivered.

### What the name check does and does not prove

It proves each id **reached the file**, by reading the STEP as the text it
is. It does **not** prove which solid carries which name — that needs a
per-engine assembly reader, and the two engines' readers differ. That is the
honest limit, stated here rather than papered over with a parity claim
neither engine supports.

---

## 6 — kernel evidence, both engines

The canonical two-body example: a 40 mm cube and a Ø20 × 30 cylinder beside
it.

| | closed form | CadQuery 2.8.0 | FreeCAD 1.0.0 |
|---|--:|--:|--:|
| `cube` | 64000 | **63999.999999999985** | **63999.999999999985** |
| `pin` | 9424.77796076938 | **9424.777960769377** | **9424.777960769377** |

Written as a STEP assembly and **read back**:

| | CadQuery | FreeCAD |
|---|--:|--:|
| solids in the file | **2** | **2** |
| volumes | 9424.777960769, 64000.0 | 9424.777960769, 64000.0 |
| body ids in the file | `cube`, `pin` | `cube`, `pin` |
| file size | 22 673 B | 11 926 B |

**Cross-readable**: FreeCAD reads the CadQuery-written assembly and returns
the same two solids at the same volumes. The file sizes differ because the
two writers emit different amounts of product structure; the geometry and the
identity do not.

Refusals, both engines: an empty body list, two bodies sharing an id, a body
with no shape, and a non-`.step` path are each refused by name.

---

## 7 — browser

`npm run e2e:surfaces` — real Chromium, real WebGL, real FreeCAD 1.0.0, no
model configured. Eight steps:

1. create two bodies → both match their closed forms
2. *"What is the volume of the cylinder?"* → answered, **MEASURED**, prefixed `cylinder:`
3. *"What is the volume?"* → **REFUSED**, naming both bodies
4. *"What is the total volume?"* → answered, **CALCULATED**
5. engineering with no question → `per_body: true`, both bodies reported separately
6. drawing of `cube` → a real sheet, naming its body
7. drawing of the whole part → **refused**, `capability: assembly_drawing`
8. export STEP → `x-cad-bodies: cube, cylinder`, and the response **bytes**
   are re-read in the browser and counted: **2 `MANIFOLD_SOLID_BREP`**, both
   ids present

Step 8 is verified by the bytes rather than by the download succeeding,
because a STEP that quietly dropped a body is a perfectly valid file.

---

## 8 — files

| file | what |
|---|---|
| `cad_experimental/questions.py` | scope, `ASSUMED`, `QuestionRefused` |
| `cad_experimental/body_reference.py` | `verb`, so a refusal fits its surface |
| `cad_experimental/session.py` | `Revision.bodies` |
| `cad_experimental/cad_backend.py` | the assembly protocol and its shared rules |
| `cad_experimental/cadquery_backend.py` | `Assembly.export` |
| `cad_experimental/freecad_backend.py` | `Import.export`, not `Part.export` |
| `cad_experimental/app.py` | export, drawing, engineering, question routes |
| `apps/web-experimental/src/main.ts` | the last `bodies[0]` |
| `tests_experimental/test_body_measurement.py` | 26 tests |
| `tests_experimental/test_step_assembly.py` | 17 tests |
| `apps/web-experimental/e2e/body-surfaces.mjs` | the eight steps above |
| `docs/multi-body-step3/mutation_test.py` | every guard, reintroduced |

---

## 9 — what is NOT done

- **No assembly drawing.** §4 — refused by name, with the reason.
- **No multi-body STL.** §5 — the one-file-per-body versus one-multi-solid
  choice has not been made explicitly, so it is refused rather than guessed.
- **A body is still named by its id and by nothing else.** "The block" for a
  body named `plate` is a guess, and Stage 72's reasoning is unchanged.
- **An id that was never in the plan cannot be distinguished from no mention
  at all.** The resolver knows the plan's ids, so *"the volume of the
  sphere"* on a two-body part refuses as *ambiguous* rather than as *unknown
  body*. A body that exists but was **consumed** is distinguished and named,
  which is the case that actually misleads. Fixing the other would need a
  noun dictionary, which Stage 72 rejected on purpose.
- **No cylinder resize** — still `read_resize`'s standing limitation.
- **No assembly constraints, mates, or inter-body booleans**; no persistence
  change.
- **No model-facing multi-body grammar.** No provider encoding admits a
  `part` branch and the prompt does not mention it. Stage 70 measured P11 on
  a *one*-body union at 5.6 %; opening multi-body to the live model before
  that is understood would measure two unknowns at once.
