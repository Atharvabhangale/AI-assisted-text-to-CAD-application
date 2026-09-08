# Local CAD engine

Status: **Stage 10 — one constructive primitive (box or cylinder) plus any
number of `through_hole` modifiers, built as a real B-rep solid.**

## Why a local backend exists

Up to this point the project could describe a part and translate it into
FeatureScript, but it could not *build* anything. Verifying that translation
needs Onshape, which is an external, authenticated service — and as of writing
that path is blocked (see `docs/featurescript-generation.md` and
`docs/onshape-mcp-boundary.md`).

A local geometry backend removes that dependency for development. It turns the
CAD specification into an actual solid in-process, deterministically, with no
account, no network, and no service to be blocked by. That gives the project
something it did not have: a way to check that a specification means what we
think it means, using a real geometry kernel.

## Architecture: an execution backend, not a replacement

The neutral CAD specification remains the source of truth. There are now **two
independent execution backends** consuming it:

```
                      ┌─→ local CAD engine  ─→ B-rep solid        (this document)
CAD specification ────┤
                      └─→ FeatureScript generator ─→ Onshape      (docs/featurescript-generation.md)
```

Both read the same validated `Part`. Neither is upstream of the other, and
neither is authoritative over the specification:

- **The specification does not depend on CadQuery.** No CadQuery or
  OpenCascade concept appears in `cad_core.model`, `cad_core.validator`, or the
  specification document. A test asserts that importing `cad_core` does not
  even load CadQuery.
- **The local engine is not a replacement for the specification.** It is one
  interpreter of it. If the two backends ever disagree about what a
  specification means, the specification decides — not the kernel.
- **The two backends are deliberately decoupled.** They do not share an error
  type: `local_cad.UnsupportedGeometryError` and
  `featurescript.UnsupportedPartError` are separate, so the subsets each
  backend supports are free to diverge.

## Geometry backend

**CadQuery** (`cadquery` 2.8.0) over **OpenCascade** (via `cadquery-ocp`
7.9.3.1.1). The result is a genuine B-rep solid produced by the kernel — not a
mesh, not triangles, not a placeholder, and not a bounding-box stand-in. The
built shape answers real topology queries (6 faces, 12 edges, 8 vertices for a
box) and real kernel measurements (volume, bounding box).

CadQuery is an **optional** dependency, declared as the `local-cad` extra. The
specification, the validator and the FeatureScript generator have no
dependencies at all, and importing `cad_core` does not require CadQuery.
`cad_core.local_cad` is therefore not re-exported from the package root.

## Supported V1 subset

A part in **millimetres** whose feature history is:

1. exactly one **constructive** feature — `box` or `cylinder` — first, and
2. then **any number of `through_hole` modifiers**, each targeting a solid in
   the set.

Everything else is rejected with `UnsupportedGeometryError`: generic
`subtract`, `fillet`, `chamfer`, a second constructive feature, a history that
does not begin with a constructive feature, an empty history, and any other
unit system. Nothing is partially built and no feature is silently skipped. The
engine does not repair or reinterpret a part.

That shape of history is not an arbitrary cut-off. It is what the
specification's own solid-set rules (Section B.4) permit given the features
implemented here: a second constructive feature would leave two solids in the
set and fail rule S9, and the only V1 feature that could consume one of them is
`subtract`, which is not implemented.

## The solid set (Section B.4)

The engine evaluates features in order against an ordered **solid set**, a
`collections.OrderedDict` keyed by solid id:

- a constructive feature **adds** a solid named by its own `id`;
- a `through_hole` **replaces its target in place** — the surviving solid keeps
  the *target's* id and the target's position in the set, and the hole's own
  `id` never names a solid;
- after the last feature the set must hold **exactly one** solid (rule S9),
  which becomes the part's geometry.

So a plate with four holes is one solid called `plate` from beginning to end,
and `LocalCadResult.feature_id` is the id of that surviving solid — `"plate"`,
not `"hole4"`. Rebuilding the specification's own Section D worked example (a
100 × 60 × 10 plate with four Ø8 holes) gives one solid, 10 faces, 24 edges,
16 vertices, and a volume of 57989.38070170254 mm³ against the hand-computed
`100·60·10 − 4·π·4²·10 = 57989.38070170253` mm³.

If a `through_hole` names a target that is not in the set,
`GeometryOperationError` is raised naming rule S6. That is a defensive check,
not a substitute for validation: a statically valid part cannot reach it,
because rule S6 is enforced in Stage 2.

## Box coordinate semantics

Section C.1 of the specification defines `position` as the box's **minimum
corner**, with the box occupying `[position, position + size]` on each axis,
axis-aligned and unrotated. The engine implements exactly that:

```
minimum corner = ( position.x,          position.y,          position.z          )
maximum corner = ( position.x + size.x, position.y + size.y, position.z + size.z )
```

So `size = (100, 60, 10)` at `position = (10, 20, 30)` occupies:

| Axis | Extent |
|------|--------|
| X | 10 → 110 mm |
| Y | 20 → 80 mm |
| Z | 30 → 40 mm |

**No centred-box default is involved.** `cadquery.Solid.makeBox` is called with
`pnt=position`, which places the minimum corner directly, so there is no
centring transform that could be got wrong. A test asserts the box does *not*
straddle its position the way a centred box would.

## Cylinder coordinate semantics

Section C.2 of the specification defines a cylinder by `diameter`, `height`,
`position` and `axis`, where **`position` is the centre of the base circle**,
the radius is `diameter / 2`, and the solid extends `height` along the signed
principal direction named by `axis` (default `"+Z"`). The base circle lies in
the plane perpendicular to that axis, so the radial extent is the radius in
each of the two perpendicular axes.

`cadquery.Solid.makeCylinder(radius, height, pnt, dir)` is used, which builds
`BRepPrimAPI_MakeCylinder` around `gp_Ax2(pnt, dir)` — `pnt` is the base circle
centre and `dir` the axis direction. That maps onto the specification directly,
with no transform in between and no centring convention to get wrong.

**The sign of the axis is honoured, not normalised away.** `"-Z"` extends
*downward* from the base centre; the base circle stays at `position` in every
case. V1 has no arbitrary rotation vectors, so the six signed principal
directions in `AXIS_DIRECTIONS` are the whole orientation vocabulary. A part
carrying any other axis string is rejected rather than guessed at.

For a cylinder of diameter 20, height 50, base centre (10, 20, 30), every
bounding box below was derived by hand from those semantics and then measured:

| Axis | minimum | maximum | dimensions |
|---|---|---|---|
| `+X` | (10, 10, 20) | (60, 30, 40) | (50, 20, 20) |
| `-X` | (−40, 10, 20) | (10, 30, 40) | (50, 20, 20) |
| `+Y` | (0, 20, 20) | (20, 70, 40) | (20, 50, 20) |
| `-Y` | (0, −30, 20) | (20, 20, 40) | (20, 50, 20) |
| `+Z` | (0, 10, 30) | (20, 30, 80) | (20, 20, 50) |
| `-Z` | (0, 10, −20) | (20, 30, 30) | (20, 20, 50) |

Volume is checked against **π r² h = 15707.963267948966 mm³**, computed
mathematically rather than read back from the kernel. The kernel agreed to
0.000e+00 for all six axes.

### Cylinder topology is a kernel observation

OpenCascade builds a full cylinder as **3 faces** (the side plus two caps),
**3 edges** (a seam plus two circles) and **2 vertices**. This is recorded as a
property of this backend, **not** part of the neutral CAD specification, and
nothing else in the project depends on it.

### Units

The kernel is unitless. Every number handed to it is in the part's declared
unit, which V1 fixes as millimetres, so a measured volume of `60000` is
60000 mm³. A part declaring any other unit system is rejected rather than
converted.

## Through-hole semantics

Section C.3 of the specification defines a `through_hole` by `target`,
`position`, `diameter` and `axis`. Its centreline is the **infinite** line
through `position` along `axis`, and the material removed is the infinite
cylinder of that diameter about that line, intersected with the target. There
is no depth, no counterbore and no taper — those are not V1 concepts.

Two consequences follow from the line being infinite, and the engine implements
both rather than approximating them:

- **The axial component of `position` has no effect.** For a 60 mm cube drilled
  along `+Z`, `position.z` of −1000, 0, 5, 30, 60 and 1000 all produce the same
  volume, 197150.44407846124 mm³, to the last bit.
- **The sign of the axis has no effect either.** An infinite line through a
  point along `+Z` is the same line as along `-Z`, so `+Z` and `-Z` holes at the
  same position cut identically — measured identical, not assumed. This is
  *unlike* a `cylinder`, where the sign decides which way the solid extends;
  the through-hole implementation therefore works from the **unsigned** axis.

### How the infinite cut is realised

OpenCascade booleans need bounded solids, so the cut uses a finite cylinder
whose length is **derived from the target's own bounding box**, never from a
hard-coded size:

1. measure the target's bounding box with the kernel;
2. take its extent `[lo, hi]` along the hole axis;
3. take the box's **diagonal length** as the margin — necessarily at least as
   long as any single extent of the box, and zero only for a degenerate solid;
4. build the cutting cylinder from `lo - margin`, of length
   `(hi - lo) + 2 * margin`, along the positive axis direction.

The cutter therefore protrudes past both faces by at least the target's largest
dimension, so the result is geometrically identical to the unbounded cut while
staying a finite boolean the kernel can evaluate. There is no magic constant and
no "sufficiently huge" number anywhere in the module.

### Boolean operation

`cadquery.Shape.cut(cutter)`, which is OpenCascade's `BRepAlgoAPI_Cut`. A
kernel failure is not swallowed: `cut` raising, or returning something that
fails the checks below, produces a `GeometryOperationError`, never a
best-effort shape.

`cut` returns a **`Compound`**, not a `Solid`. After the single-solid check
below the surviving solid is unwrapped, so `LocalCadResult.shape` is always a
`Solid` and `is_solid()` stays meaningful for a drilled part exactly as it is
for a box.

### Rule E1 — the centreline must intersect the target

Checked with the kernel rather than by special-casing geometry: a line segment
along the centreline (spanning the same derived length as the cutter) is
intersected with the target via `Shape.intersect` (`BRepAlgoAPI_Common`), and
the common shape must contain at least one edge. If it contains none, the
centreline misses the material and the build fails:

```
GeometryOperationError: through_hole 'h': its centreline does not intersect
target 'b' (rule E1); a hole that misses the material is an error, not a no-op
```

Testing the *centreline* rather than "did any material disappear" is
deliberate: a hole whose centreline lies outside the body but whose radius
still clips a corner is caught by this check, which is what E1 says.

### Rules E2 and E3 — the result must be one connected solid

Both come from the kernel's own count of solids in the boolean result:

| Solids in result | Meaning | Behaviour |
|---|---|---|
| 0 | the cut consumed the whole body | `GeometryOperationError`, rule E2 |
| 1 | valid | unwrapped and kept |
| > 1 | the cut split the body | `GeometryOperationError`, rule E3 |

Measured examples: a Ø20 hole through a Ø10 × 20 cylinder leaves nothing —
`cutting target 'c' left no material (rule E2)`; a Ø40 hole through the middle
of a 100 × 20 × 10 bar leaves two pieces — `cutting target 'bar' split it into
2 disconnected solids (rule E3); V1 has no multi-body parts`. Neither is
repaired, and neither returns a shape.

### Topology is a kernel observation

Drilling one Ø20 hole through a 100 × 60 × 10 plate gives **7 faces, 15 edges,
10 vertices**: the box's six planes plus one cylindrical wall, and the hole's
two circular edges plus the cylinder's seam. Four Ø8 holes give 10 faces, 24
edges, 16 vertices. These counts are recorded as properties of *this backend*,
not as part of the neutral specification, and nothing else in the project
depends on them.

The bounding box is **unchanged** by a through-hole whose material lies wholly
inside the body — a hole removes interior material, it does not shrink the
envelope. A test asserts that rather than assuming it.

One caveat, measured rather than assumed: `bounding_box()` on a shape that has
since been **tessellated** (by STL export or the render model) loosens outward,
and for a drilled part the loosening is about 3.1e-3 mm — far larger than the
~1e-7 mm a plain box shows. It is a property of OpenCascade's bounding-box
query over an attached triangulation, not of the cut. Measure the B-rep before
meshing if a 1e-6 mm answer is wanted; `docs/render-representation.md` records
the numbers.

## Input / output boundary

**Input** is the typed `Part` from `cad_core.model` — in practice the `part` of
a successful `cad_core.validate()` result. A raw specification dictionary is
not accepted and raises `TypeError`; there is no code-execution path and no way
to pass arbitrary Python or CadQuery source into the engine.

The engine **assumes static validation has already run**. It does not re-check
rules S1-S20, and it will not fix a malformed part. An invalid specification
never produces a `Part`, so it cannot reach the kernel — a test demonstrates
this end to end with a negative extent (rule S10).

**Output** is a small `LocalCadResult`:

```python
from cad_core.local_cad import build_part

result = build_part(part)          # part: cad_core.model.Part

result.shape                       # the kernel B-rep (a cadquery.Shape)
result.part_name, result.feature_id
result.is_solid()                  # kernel ShapeType + kernel validity analysis
result.solid_count()
result.bounding_box()              # BoundingBox of model.Position / model.Size
result.volume()
```

`shape` is exposed because later stages need it. The measurement helpers exist
so ordinary callers can inspect a result without touching CadQuery, and they
report in the specification's own `Position`/`Size` types.

`is_solid()` uses two real kernel facts — the shape's topological `ShapeType`
and OpenCascade's own validity analysis via `isValid()`. Existence of an object
is never taken as evidence that geometry is sound.

## Deterministic builds

The same `Part` built repeatedly must measure identically: same bounding box,
same placement, same solid count, same volume. Tests build the same part five
times and assert the measurements collapse to a single value. This holds for
drilled parts too: five builds of the drilled plate collapse to one
`(volume, bounding box, solid count, face count, edge count)` tuple, so the
boolean does not introduce run-to-run variation.

Determinism here means *measured values*, not object identity — each call
returns a fresh result object, and nothing relies on memory identity being
stable. Kernel-derived measurements are compared with an explicit tolerance of
**1e-6 mm**, used consistently; exact floating-point equality is never used for
a value that came from the kernel.

## Future stages

Explicitly **not** part of this stage:

- **The remaining V1 features are unimplemented**: generic `subtract`,
  `fillet`, `chamfer`. There are no hole patterns either — four holes are four
  `through_hole` features, and the engine has no pattern concept.
- **No generalized feature graph.** The evaluator is an ordered dictionary of
  solids walked once, which is exactly what Section B.4 describes. It is not a
  dependency graph, a rollback stack, or a re-orderable history.
- **FeatureScript/Onshape remains a separate backend path.** This engine does
  not generate FeatureScript, does not talk to Onshape, and does not make the
  Onshape path unnecessary — verifying the FeatureScript backend still requires
  Onshape. The FeatureScript generator still supports **only a single box**, so
  the two backends' supported subsets have now diverged; that is what the
  separate error types are for.
- No frontend, no LLM, no MCP, no HTTP API.

## Known limitations

- **One constructive feature.** Multi-body parts, assemblies, and any history
  with two primitives are rejected. This follows from rule S9 plus the missing
  `subtract`, not from a limit of the kernel.
- **Only `through_hole` cuts.** There is no generic boolean: the tool is always
  a cylinder derived from a `through_hole`, never an arbitrary solid, and no
  arbitrary boolean operation is exposed.
- **A through-hole always replaces its target.** There is no way to keep both
  the original and the drilled body, because V1 has no vocabulary for it.
- The engine trusts the validator. A hand-built `Part` that bypasses validation
  with, say, a zero extent would reach the kernel and fail there rather than
  being caught politely — by design, since re-validating would duplicate
  Stage 2 and repairing is forbidden. The rule S6 target check in the
  evaluator is the one exception, and exists only because the evaluator cannot
  proceed without a target.
- **E4 and E5 are still not implemented.** Only E1, E2 and E3 are enforced, and
  only for `through_hole`; E4 and E5 concern fillets and chamfers, which do not
  exist here yet.
- **Tangency is not classified.** A centreline that passes exactly through a
  face or edge of the target is left to the kernel's own tolerance rather than
  being detected and reported as a distinct condition. The specification does
  not define that case for V1, and the engine does not invent an answer.
- Agreement between the two backends is untested, and cannot be tested until
  the Onshape path runs. Both are built from the same specification section,
  but that is an argument, not evidence.
