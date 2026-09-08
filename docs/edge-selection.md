# Edge selection

Status: **Stage 12 — deterministic edge selection. No geometry is modified.**
Consumed since Stage 13 by the `fillet` modifier (`docs/local-cad-engine.md`).

## What this layer is for

`fillet` and `chamfer` need to name edges, and Section C.7 of the
specification gives exactly two ways to do it. This layer turns one of those
selectors plus a B-rep shape into a stable sequence of kernel edges:

```
B-rep solid ──┐
              ├─ select_edges ─→ deterministic tuple of kernel edges
EdgeSelector ─┘
```

It is infrastructure, deliberately built and tested **before** the modifiers
that use it, so that "which edges?" is settled and measured separately from
"what happens to them?".

Nothing here builds, moves or changes geometry. Stage 13 added the first
consumer — `fillet` calls `select_edges` and owns rules E4 and E5 itself — and
that stage changed nothing in this layer. `chamfer` remains **unimplemented**.

## API

```python
from cad_core.edge_selection import select_edges
from cad_core.model import EdgeSelector

edges = select_edges(result.shape, EdgeSelector(select="axis_parallel", axis="Z"))
len(edges)          # the matched count, which is all rule E4 will need
```

Three helpers exist alongside it, exposing exactly the facts the selector
itself decides on so a caller or a test can check the same thing rather than
re-deriving it from coordinates:

| Helper | Answers |
|---|---|
| `edge_curve_type(edge)` | the kernel's curve type, e.g. `"GeomAbs_Line"` |
| `is_straight_edge(edge)` | is the kernel's curve type `GeomAbs_Line`? |
| `line_direction(edge)` | the unit direction of a straight edge, as plain floats |

`line_direction` raises `EdgeSelectionError` for a curve: there is no direction
to report and none is invented.

### Input types

- **shape** — a `cadquery.Shape` from the local engine: in practice
  `LocalCadResult.shape`, or a solid held in the engine's solid set
  mid-evaluation, which is what a future `fillet` will have in hand. Anything
  else raises `TypeError`, including a `LocalCadResult` itself.
- **selector** — a typed `cad_core.model.EdgeSelector`. **A raw dictionary is
  refused with `TypeError`**, the same way the local engine refuses a raw
  specification document.

An unusable selector raises `UnsupportedSelectorError` — a distinct type,
because a selector this layer cannot evaluate is not a failed geometric
operation. Rejected: an unknown `select`, an unknown `axis`, a missing `axis`
on `axis_parallel`, an `axis` present on `all` (rule S18), and the **signed**
forms `"+X"`/`"-Z"` — C.7's unsigned letters are deliberately different from
the signed values `cylinder` and `through_hole` use, and the error message says
so. A validated part can never reach any of these checks; they are there so
that inconsistent typed input is reported rather than selecting the wrong
edges.

## `{"select": "all"}`

Every edge of the shape, in `shape.Edges()` order.

`Edges()` contains each topological edge exactly **once** — measured against
the kernel's own map: for the two-hole plate `Edges()` returns 18 and a
`TopTools_IndexedMapOfShape` over `TopAbs_EDGE` also reports extent 18. No
deduplication of the kernel's answer is needed or done.

## `{"select": "axis_parallel", "axis": "X" | "Y" | "Z"}`

Every **straight** edge whose direction is parallel to that axis, with the
axis treated as **unsigned**.

### Straightness is a kernel fact

Each edge is wrapped in OpenCascade's **`BRepAdaptor_Curve`** and asked
`GetType()`. A straight edge is one whose type is `GeomAbs_Line`. Everything
else — `GeomAbs_Circle`, `GeomAbs_Ellipse`, the spline and Bézier types,
`GeomAbs_OtherCurve` — is not straight and never matches.

Not used, deliberately: vertex counts, bounding-box shapes, `repr` strings,
endpoint arithmetic, or position in the edge list.

### Direction is a kernel fact too

For a line edge, `BRepAdaptor_Curve.Line().Direction()` gives the underlying
line's direction as a `gp_Dir`.

Two measured properties matter:

- **It is location-aware.** A box rotated 45° about Z reports directions
  `(±0.707107, 0.707107, 0)` and `(0, 0, 1)`; a box carrying a `TopLoc`
  rotation of 30° reports `(0.866025, 0.5, 0)`, `(-0.5, 0.866025, 0)` and
  `(0, 0, 1)`. So the selector reads real placed geometry and is not coupled
  to axis-aligned primitives — even though V1 has no rotation to exercise
  that. Endpoint differences are never used to infer a direction.
- **It ignores topological orientation.** A `TopAbs_REVERSED` edge reports the
  same direction as its `FORWARD` twin — measured on a plain box, whose edges
  are a mix of both. The kernel does not encode traversal sense in the
  geometry, which is one reason parallelism must be tested unsigned even for a
  box.

### Unsigned parallelism

Decided by **`gp_Dir.IsParallel(other, tolerance)`**, the kernel's own test,
which is unsigned: measured true for identical directions *and* for opposite
ones, false for perpendicular. A `+Z` edge and a `-Z` edge both match `"Z"`.

No hand-rolled dot product, no `abs()` on components, no sign normalisation —
the specification's rule and the kernel's predicate are the same rule.

## Tolerance

`ANGULAR_TOLERANCE_RAD` is OpenCascade's own `Precision::Angular()` —
**1e-12 rad**. It is read from the kernel, not chosen here, and a test asserts
that it still equals `Precision.Angular_s()`.

How much of it V1 geometry needs was measured rather than assumed. Across the
box, the cylinder, the drilled plate, the two-hole plate and the two-tool
subtract, **every** axis-parallel line direction came back as an exact unit
vector — worst deviation from exact `0.0`, not merely small. The tolerance is
therefore headroom for generality, not a fudge factor, and floating-point
equality is still never used for the comparison.

The consequence of a tolerance this tight is worth stating: an edge off-axis by
more than 1e-12 rad is **not** selected. For V1 that cannot arise; if a later
feature produces near-axis edges, excluding them is the conservative answer and
a looser tolerance would be a deliberate decision to make with its own
measurement.

Tests use separate, looser tolerances for their own assertions — 1e-6 mm for
lengths, 1e-9 for direction components — because those check the *result*, not
the kernel's parallelism predicate.

## Ordering and determinism

The order is `shape.Edges()` order, **preserved exactly**. The result is a
filtered view of that list, and a test asserts position-by-position (by
topological identity) that the selected edges appear in their original relative
order.

That was measured before being relied on:

| Measurement | Result |
|---|---|
| repeated `select_edges` calls on one shape (5×, all four selectors, five geometries) | identical |
| repeated `Edges()` on one shape | identical |
| repeated **builds** of the same part | identical edge order |
| separate **processes** | identical (same SHA-256 of the edge fingerprint) |

So no canonical re-sorting was introduced, and that is a decision rather than
an omission. Sorting would have to invent a key: the box's four X-parallel
edges have equal length and no unique scalar to order them by, so any
float-coordinate key would need arbitrary tie-breaking, and a compound key
would be a new contract with no measured need behind it. Preserving a measured
stable order is less machinery and less risk.

Object identity and memory order are relied on nowhere. Determinism is asserted
on a fingerprint of curve type, endpoints and length, and identity on the
kernel's `IsSame`.

## Measured selector results

Every count below was measured first and is recorded as a **backend
observation**, not as part of the neutral specification.

### Box — 100 × 60 × 10

| Selector | Edges | Notes |
|---|---|---|
| `all` | 12 | all `GeomAbs_Line` |
| `axis_parallel X` | 4 | each 100 mm |
| `axis_parallel Y` | 4 | each 60 mm |
| `axis_parallel Z` | 4 | each 10 mm |

The three axis selections **partition** the 12 edges — 4 + 4 + 4, with no edge
matching two axes (checked by topological identity, not by arithmetic).

### Cylinder — Ø20 × 50, axis `+Z`

The critical case, and the one that most needed measuring rather than
assuming. The kernel represents a full cylinder with **three** edges:

| Selector | Edges | Notes |
|---|---|---|
| `all` | 3 | 2 × `GeomAbs_Circle` (the caps) + 1 × `GeomAbs_Line` |
| `axis_parallel X` | 0 | empty result, not an error |
| `axis_parallel Y` | 0 | empty result, not an error |
| `axis_parallel Z` | **1** | the seam |

**The straight edge is the seam** — the cylindrical surface's
parameterisation seam, 50 mm long, running along the axis. It is a genuine
`GeomAbs_Line` in the kernel's representation, so the specification's "every
straight edge of the target solid that is parallel to the given axis" **does**
match it. That is reported, not special-cased: excluding it would be inventing
a rule the contract does not contain. See **Findings for the fillet stage**
below.

There are **no** straight radial edges on a cylinder: the caps are single
circular edges, not polygons.

The seam follows whichever axis the cylinder uses, with the sign discarded —
measured for all six signed axis values:

| Cylinder `axis` | X | Y | Z |
|---|---|---|---|
| `+X`, `-X` | 1 | 0 | 0 |
| `+Y`, `-Y` | 0 | 1 | 0 |
| `+Z`, `-Z` | 0 | 0 | 1 |

### Drilled plate — 100 × 60 × 10 with one Ø20 `+Z` hole

| Selector | Edges | Notes |
|---|---|---|
| `all` | 15 | 13 lines + 2 circles |
| `axis_parallel X` | 4 | the outer edges |
| `axis_parallel Y` | 4 | the outer edges |
| `axis_parallel Z` | **5** | 4 outer corners **+ the cavity seam** |

The two circular **hole rims are never selected** by any axis selector —
exactly the specification's own example, that filleting the vertical corners
must not touch the rims. Verified by topological identity against the rims
rather than by counting.

The fifth Z edge is the cavity's seam: a 10 mm straight edge sitting at radius
10 from the hole axis, so it is distinguishable from an outer corner by
position, not by length (both are 10 mm).

### Two-hole plate — Ø20 at (20, 20) and Ø10 at (80, 40)

| Selector | Edges | Notes |
|---|---|---|
| `all` | 18 | 14 lines + 4 circles |
| `axis_parallel X` | 4 | the outer edges |
| `axis_parallel Y` | 4 | the outer edges |
| `axis_parallel Z` | **6** | 4 outer corners + one seam per cavity |

All four rims stay excluded; the two seams are present and topologically
distinct.

### Multi-tool subtract — the same plate via Stage 11's `subtract`

Identical to the two-hole plate on every count: same curve-type census, same
selector counts, same total. Selection operates on the **final B-rep** and has
no idea which feature produced a cavity. Both cavities remain represented —
circle lengths give radii 5.0 and 10.0 — and every axis-parallel match is
straight.

## Empty results, and rule E4

A selector that matches nothing returns an **empty tuple**. That is a result,
not an error, and this layer never raises E4.

The reasoning: "matched nothing" is a fact about a selector, while "a fillet
that affects nothing is an error" is a fact about a fillet. `len()` of the
result is all a modifier needs to enforce E4, and it should own that error so
it can name its own feature id and field path the way Section E.3 requires.

Stage 13 confirmed the split works: `_apply_fillet` checks `if not edges` and
raises E4 naming its own feature, and the kernel is never called for an empty
selection. A test asserts the selector still returns `()` rather than raising.

## What this layer does not do

- **No geometry is created, modified or moved.** The returned objects are the
  shape's own edges; a test compares a full fingerprint of the shape — edge
  list, volume, face/edge/vertex counts, shape type, validity — before and
  after selection and requires them equal.
- **No persistent edge ids and no named topology.** Nothing here assigns an
  identifier to an edge, and nothing stores one. Persistent named-topology
  selection is explicitly deferred by Section C.7 to a later specification
  version, and this stage did not start it.
- **No general topology query language.** The only questions that can be asked
  are the two selectors the specification defines. There is no filter by face,
  by length, by position, by tangency or by anything else.
- **No raw OpenCascade objects in the neutral specification.** Kernel edges are
  returned to kernel-side callers only. `cad_core.model` and
  `docs/cad-specification.md` gain no CadQuery or OCP vocabulary, and this
  module is not re-exported from the package root, so importing `cad_core`
  still does not require CadQuery.
- **Nothing about what happens to a selected edge.** Whether a radius fits, or
  what a blend looks like, is the modifier's business — see
  `docs/local-cad-engine.md`. This layer would return the same edges for a
  radius of 0.5 mm and 500 mm.

## Package boundary

The dependency runs one way:

```
validated V1 EdgeSelector + B-rep  ─→  edge_selection  ─→  kernel edges
```

Tests assert this from the source, not from prose: `model.py`,
`validator.py`, `featurescript.py`, the three exporters and `render_model.py`
do not import `cad_core.edge_selection`, and `edge_selection.py` imports none
of them (it imports only `cad_core.model`, CadQuery and OCP).

Since Stage 13 `local_cad.py` **does** import it, which is the one direction
that was always intended. The exporters already depend on `local_cad`, so they
now depend on this layer transitively; they still do not import it themselves,
and nothing in this layer knows they exist.

## The seam findings, and what Stage 13 measured

Stage 12 recorded two open questions about selected seams. Stage 13 answered
both by measurement, without changing this layer:

1. **`axis_parallel Z` on a drilled plate includes the cavity seam** — and
   filleting that selection **succeeds**. The blend is a geometric no-op on the
   seam: for a 100 × 60 × 10 plate with one Ø20 hole at radius 2, the result's
   volume is the drilled volume minus exactly the four corner blends, and the
   face count goes 7 → 11, so the seam produced neither material change nor a
   new face. That is consistent with a cylindrical face being smooth across its
   own parameterisation seam. The specification's stated intent — that the hole
   *rims* are untouched — holds: the rims are circles and are never selected.
2. **`axis_parallel` on a plain cylinder matches exactly one edge, the seam** —
   and filleting *that* alone **fails**: `BRepFilletAPI_MakeFillet.Build()`
   raises `Standard_Failure` at every radius tried. So it is not an E4 failure
   (one edge matched) but an E5 one, and the engine reports it as such rather
   than succeeding vacuously. Curiously, `{"select": "all"}` on the same
   cylinder — which includes the seam *and* both rims — succeeds.

So a selected seam is sometimes harmless and sometimes fatal, and which one
depends on the surrounding geometry. That is a property of the kernel, reported
rather than reinterpreted. Neither case is a contradiction in the
specification, so `docs/cad-specification.md` was not changed; if V1 ever wants
seams excluded, that is a change to C.7 and a decision for the specification,
not for this layer.
