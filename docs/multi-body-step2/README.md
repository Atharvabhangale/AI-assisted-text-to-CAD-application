# Stage 72 — multi-body, step 2: addressing a body by name

Stage 71 made several bodies real and left the product unable to **edit**
one: every reader asked for "the body" and, with two live, there was no such
thing, so they declined. Declining was right, and it is not an answer.

This stage is one resolver and the four rules it applies. It adds **no
operation, no P-code and no schema change** — the canonical plan could
already name a body, because an operation's `target` *is* a body id. What
was missing was turning a sentence into one of those ids, and refusing when
the sentence does not settle it.

## 1 — the representation

**Unchanged.** A body reference in the canonical plan is what it has always
been: the id of the constructive operation that created the body, which a
body keeps for life because every modifier keeps its target's id.

What is new is `cad_experimental/body_reference.py`, the **one** place that
answers "which body does this request mean":

```python
resolve_body(text, plan) -> BodyChoice(body=..., named=..., reason=..., live=...)
```

Exactly one of `body` and `reason` is set. There is deliberately **no third
"not interested" state**: that is the reader's judgement about the *verb*,
made before it ever asks which body, and folding it in would let "not my
sentence" and "your sentence, and it is ambiguous" reach the person as the
same answer.

### Why a body is named by its id, and by nothing else

A body's id is its identity. The product shows those ids, and the reader
that creates two bodies names them for what they are (`cube`, `cylinder`).
Matching on the id is exact, stable and needs no synonym table.

Matching on the **noun of the primitive** a body came from, or on a synonym
list — "the block" for a body named `plate`, "the pin" for one named
`cylinder` — was rejected. It is a guess about what the person meant, and a
wrong guess here edits the wrong body and reports success. A refusal that
says *"this part has two bodies (cube, cylinder); say which one"* costs the
person one word and cannot be wrong.

## 2 — body-target semantics

| the request | the answer |
|---|---|
| names a live body | **that body** |
| names none, and exactly one is live | **that body** — the pre-Stage-71 behaviour, unchanged |
| names none, and several are live | **REFUSE**, listing them |
| names two bodies at once | **REFUSE** — one operation changes one body |
| names a body that was consumed | **REFUSE**, and say what consumed it |
| names something that is no body | **REFUSE**, listing what exists |

An id that is a whole word inside a longer id (`plate` inside `plate-2`,
because `-` is not a word character) is settled by the **text** — one match
span containing the other — never by preferring a longer name in general, so
it can never pick a body the request did not mention.

**No new validation rules were added, and that is deliberate.** A plan that
names a nonexistent body is already P9, one that names a modifier's id is
P11, and one that names a consumed solid is P12. The four refusals above are
*reader* concerns — turning a sentence into a plan — and a P-code restating
P9 would be a second opinion about what "a body" means. This is the same
finding as Stage 71's, where the design's proposed P33 turned out to be
P9–P12 verbatim.

## 3 — the two defects this slice had to fix to be correct

**The envelope spanned every body.** `_envelope(plan)` took the bounding box
over *all* constructive solids. With a 40 mm cube at the origin and a
cylinder beside it, that box runs x 0..70, so "a hole through the centre of
the cube" would have been bored at x = 35 — **15 mm off the cube's own
centre**, in a plan that still validates and still builds. It is now scoped
to the constructive operations the named body is made of, via
`_constructive_of`, which is also what `_fused_from` reads, so there is one
answer to "which primitives is this body".

**A failed selection did not say which body.** With one body, "matched no
edge" was unambiguous; with two it is the first question a reader asks. The
executor now prefixes the body — `on 'cube': the selector circular/Z matched
no edge…` — and the prefix is added **there** rather than inside
`edge_semantics`, which imports nothing and knows nothing about plans or
bodies and must stay that way.

## 4 — grammar widened, and the defect that widening would have introduced

`_GROW_WORDS` took the comparative and the noun (`wider`, `width`) but not
the bare adjective, so "make the cube 50 mm wide" — the plainest way to say
it — declined. The adjectives are now accepted.

That alone would have been a **bug**: `"20 mm wider"` is a change of 20,
`"50 mm wide"` is a width of 50. Same axis, opposite arithmetic. Read as a
delta, "make the cube 50 mm wide" on a 40 mm cube gives 90 — measured, on
exactly that sentence, before `_COMPARATIVE` existed. English settles it
with the *-er*, and the test that pins it names the number.

## 5 — kernel evidence, both engines

Starting from the two-body part and editing each body in turn:

| step | cube | cylinder |
|---|--:|--:|
| created | **63999.999999999985** (40³) | **9424.777960769377** (π·10²·30) |
| "make the cube 50 mm wide" | **79999.99999999999** (50·40·40) | 9424.777960769377 — **untouched** |
| "put a 6 mm hole through the cylinder" | 79999.99999999999 — **untouched** | **8576.547944300135** (− π·3²·30) |

A body-targeted bore measures **identically on CadQuery 2.8.0 and FreeCAD
1.0.0**, asserted per body over volume, face count and edge count.

## 6 — browser

`npm run e2e:bodytarget` — real Chromium, real WebGL, real FreeCAD 1.0.0,
**no model configured**. All four steps above, plus the one that matters
most:

```
4. make it 20 mm wider   (names no body)
   status: refused
   reply : this part has 2 separate bodies ('cube', 'cylinder'), and the
           request does not say which one to change. Name it and it will be done
  ok not built -- status refused
  ok the refusal names both bodies and asks which
  ok the part is exactly where it was
```

Every other step can be checked in a unit test; that a refusal reaches the
person **as an answer**, in the product, with the part left exactly where it
was, cannot.

`DETERMINISTIC` — a local grammar read every one of those sentences. This
says nothing whatever about model quality.

## 7 — regression

`tests_experimental` **1936 passed, 5 skipped, 0 failed** (1941 collected),
against a Stage 71 baseline of **1910 passed, 5 skipped** (1915 collected)
re-measured at `8e1371c` for this stage — **+26, exactly the new module**.
cad-core **1481 passed**. `e2e:assembly` and `e2e:multibody` both still
PASS; frontend `tsc --noEmit` clean on both apps and 87 vitest tests pass.

**One browser script was already broken and still is.** `npm run e2e:cases`
fails waiting for `#description`, an element the page has not had since
`641106f` — several stages before this one. Verified by stashing this
stage's changes and running it at `8e1371c`, where it fails identically. It
is **not** a Stage 72 regression, it is **not** fixed here (guessing what it
should assert against the current page is its own job), and it is recorded
so nobody reads a green run as covering it.

## 8 — files

| file | what |
|---|---|
| `mutation_test.py` | reintroduces each defect and asserts the guard goes red. **8/8 caught** |

Implementation: `cad_experimental/body_reference.py` (new),
`normalize.py`, `executor.py`. Tests:
`apps/api/tests_experimental/test_body_targeting.py`. Browser:
`apps/web-experimental/e2e/body-targeting.mjs`.

## 9 — what is NOT done

- **No cylinder resize.** `read_resize` declines for a cylinder with a
  standing comment — *"a cylinder's 'width' is its diameter; later"* — so
  "make the cylinder 40 mm long" declines. Diameter-and-length semantics are
  a dimension question, not a body-targeting one, and doing them here would
  have been a second unknown in one stage.
- **No assembly constraints, mates, or inter-body booleans**; no persistence
  change.
- **No model-facing multi-body grammar.** No provider encoding admits a
  `part` branch and the prompt does not mention it. Stage 70 measured P11 on
  a *one*-body union at 5.6 %; opening multi-body to the live model before
  that is understood would measure two unknowns at once.
- **No multi-body export** — still step 5 of `docs/multi-body-design.md`.
