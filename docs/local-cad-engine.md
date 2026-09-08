# Local CAD engine

Status: **Stage 14 — the complete V1 feature set (`box`, `cylinder`,
`through_hole`, `subtract`, `fillet`, `chamfer`), built as real B-rep
solids.**

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

A part in **millimetres** whose feature history:

1. begins with a **constructive** feature — `box` or `cylinder` — and
2. uses any of the six V1 feature types.

Several constructive features are allowed, provided the extras are consumed as
`subtract` tools so that exactly one solid is left. **No V1 feature type is
rejected as unimplemented any more.** What is still rejected with
`UnsupportedGeometryError`: a history that does not begin with a constructive
feature, an empty history, any unit system other than millimetres, and a
non-`Part` input.
Nothing is partially built and no feature is silently skipped. The engine does
not repair or reinterpret a part.

The number of constructive features is deliberately **not** capped up front.
Whether extra solids are legitimate depends on whether something consumes
them, which is only known once the history has been evaluated — so a leftover
solid is caught by the rule S9 check after the last feature, which is where
the specification puts it. Stage 10's up-front "exactly one constructive
feature" restriction was correct only while `subtract` was missing.

## The solid set (Section B.4)

The engine evaluates features in order against an ordered **solid set**, a
`collections.OrderedDict` keyed by solid id:

- a constructive feature **adds** a solid named by its own `id`;
- a modifier (`through_hole`, `subtract`, `fillet`, `chamfer`) **replaces its
  target in place** — the surviving solid keeps the *target's* id and the
  target's position in the set, and the modifier's own `id` never names a
  solid;
- a `subtract` additionally **consumes** each solid named in `tools`, deleting
  it from the set so no later feature can resolve it;
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

### Rules E2 and E3

Handled by the shared boolean machinery described under
[One boolean implementation](#one-boolean-implementation). A through-hole has
exactly one cutter, so the per-cut and per-feature checks coincide.

Measured examples: a Ø20 hole through a Ø10 × 20 cylinder leaves nothing —
`through_hole 'h': cutting target 'c' left no material (rule E2)`; a Ø40 hole
through the middle of a 100 × 20 × 10 bar leaves two pieces —
`through_hole 'slot': the cut split the body into 2 disconnected solids
(rule E3); V1 has no multi-body parts`. Neither is repaired, and neither
returns a shape.

Rule E1 is what a through-hole has *instead of* the no-op check generic
subtraction needs: if the centreline meets the target, material is removed.

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

## Subtract semantics (Section C.4)

`subtract` takes a `target` and a non-empty list of `tools`, and removes each
tool solid from the target **in `tools` order**. Only subtraction exists in V1:
there is no union, no intersection, and no boolean expression tree. A tool is
an ordinary solid already in the set — usually a `cylinder` — not a special
"cutting tool" concept.

### Target replacement and tool consumption

```
plate ─┐
        ├─ subtract(target=plate, tools=[toolA, toolB]) ─→ plate
toolA ─┤                                                   (toolA, toolB gone)
toolB ─┘
```

- the result is written back to the **target's key**, so it keeps the target's
  id and its position in the ordered set;
- each tool id is **deleted** from the set, so no later feature can resolve it
  (the validator reports that as rule S6);
- the subtract's own `id` never names a solid — it appears only in error
  messages;
- **nothing is written back unless the whole feature succeeds.** The
  replacement and all the deletions happen after the last cut has passed its
  checks, so a failed subtract leaves no partial geometry and no half-consumed
  tools. A test drives a two-tool subtract whose second tool is unresolvable
  and confirms the failure is reported against the subtract, not later as a
  confusing rule S9 error about a set that was quietly mutated.

### Order is honoured, and what it actually affects

Two different things are kept apart deliberately:

**The geometry is order-independent.** Subtracting a set of tools removes their
union, and `A \ (B ∪ C) = (A \ B) \ C = (A \ C) \ B`. So the claim is not
that order changes the shape — it is set algebra, and it was measured too:
cutting two disjoint cylinders from the plate in either order, or both at once
with `cut(first, second)`, gives volume agreement to 0.0 and identical
topology (8 faces, 18 edges, 12 vertices).

**The acceptance is order-dependent.** Because each tool is applied to the
*current* target, a tool whose material an earlier tool already removed
removes nothing itself. Measured on a plate with a Ø20 tool and a coaxial Ø10
tool inside it:

| `tools` | Outcome |
|---|---|
| `["wide", "narrow"]` | **refused** — `'narrow'` would remove no material |
| `["narrow", "wide"]` | builds; volume `56858.407346410204` mm³, identical to the wide tool alone |

That is the evidence that tools really are applied in list order, and the
reason the specification's "in list order" is not a redundant phrase.

## One boolean implementation

`through_hole` and `subtract` share a single boolean path
(`_cut_in_order`), so there is only ever one implementation of "cut and check"
in this engine. A through-hole passes one derived cutter; a subtract passes its
tools in order.

### Boolean API

`cadquery.Shape.cut(tool)` → OpenCascade **`BRepAlgoAPI_Cut`** (via CadQuery's
`_bool_op`, which sets the target as the argument list and the tools as the
tool list). Cuts are applied **sequentially**, one `cut` call per tool, which
is what "in list order" asks for and what makes per-tool error attribution
possible. CadQuery's `cut` also accepts several tools in one call; that path
was measured to agree exactly with the sequential one and is not used, because
it cannot say which tool caused a failure.

A kernel failure is not swallowed: `cut` raising, or returning something that
fails the checks below, produces a `GeometryOperationError`, never a
best-effort shape.

### Return type

`cut` returns a **`Compound`**, not a `Solid` — measured, not assumed. Only
after the single-solid check does the engine unwrap that one solid, so
`LocalCadResult.shape` is always a `Solid` and `is_solid()` means the same
thing for a cut part as for a box. A multi-solid result is never reduced by
picking a component.

### Rule E2 — a cut must leave material

Checked from the kernel's solid count **after every individual cut**, because
an empty result is absorbing: once no material is left, no later tool can
bring any back, and naming the tool that emptied the body is more useful than
reporting at the end. Measured: cutting the plate with a 200 mm box gives a
compound with **0 solids and 0 faces**, and `Volume()` still answers `0.0`
rather than failing — which is exactly why the classification is on the solid
count and not on volume.

```
GeometryOperationError: subtract 'cut': subtracting tool 'swallow' from target
'plate' left no material (rule E2)
```

### Rule E3 — the result must be one connected solid

Checked **once, on what the feature leaves**. Section E.2 says "every modifier
leaves a single connected solid", so the subject is the modifier's result, not
each intermediate cut:

| Solids in the feature's result | Behaviour |
|---|---|
| 0 | `GeometryOperationError`, rule E2 (reported at the cut that emptied it) |
| 1 | unwrapped and kept |
| > 1 | `GeometryOperationError`, rule E3 |

```
GeometryOperationError: subtract 'cut': the cut split the body into 2
disconnected solids (rule E3); V1 has no multi-body parts
```

**A consequence, chosen deliberately and recorded here:** a multi-tool subtract
may pass *through* a split state if a later tool removes the extra pieces. A
100 × 20 × 10 bar severed by a Ø40 cylinder and then trimmed of its right-hand
piece builds successfully, leaving one solid of 6173.554090037926 mm³ — which
matches the analytic value `(55·20 − (100 + 100√3 + 200π/3))·10` to
3.8e-8 mm³. Checking each intermediate instead would reject it. That reading
would be a stricter rule than E3 states, so the engine does not apply it; the
alternative is noted under **Known limitations** as an open question for a
future specification revision.

### The unclassified case: a tool that removes nothing

**V1 does not classify this.** Section C.4 lists only E2 and E3 for
`subtract`. The specification says a no-op is an error where it means it —
E1 for a through-hole that misses, E4 for a selector that matches nothing —
and it does not say it here.

The engine **refuses** such a subtraction, which is the narrowest reading
consistent with the contract, and the message says plainly that the case is
unclassified rather than claiming a rule code it does not have:

```
GeometryOperationError: subtract 'cut': subtracting tool 'far' from target
'plate' would remove no material -- the two solids do not overlap. V1 does not
classify this case (E2 and E3 are the only geometric rules given for
'subtract'), so the engine refuses it rather than accepting a subtraction that
means nothing
```

Two measurements support refusing over accepting:

- **the kernel accepts it silently.** A disjoint tool returns the target's
  volume to the last bit and the same six faces, so nothing downstream —
  volume, bounds, topology, mesh — would notice.
- **a *touching* tool removes no volume yet still changes topology.** A
  cylinder tangent to the plate's +X face leaves the volume at exactly
  60000.0 but the box comes back with a **seventh face**: the kernel imprints
  the contact. Accepting no-ops would let a tool that means nothing
  geometrically alter the result.

Overlap is decided by the kernel, not by comparing volumes:
`Shape.intersect` (`BRepAlgoAPI_Common`) must yield at least one **solid**.
Touching faces yield none — measured — which is the intended answer.

## Fillet semantics (Section C.5)

`fillet` takes a `target`, one constant `radius` and an edge selector. Every
edge the selector matches is replaced by a circular blend of that radius, and
the target is replaced in place — the surviving solid keeps the target's id,
and the fillet's own `id` appears only in error messages.

There is **no per-edge radius** in V1, no variable radius, no angle-driven
blend and no face blend. One radius, one selection, one operation.

### Selector integration

Edge selection is delegated entirely to the Stage 12 layer
(`docs/edge-selection.md`): `select_edges(target, fillet.edges)`. No selector
logic is duplicated here, and the sequence passed to the kernel is exactly the
sequence the selector returned — nothing is filtered, reordered or dropped.
That includes edges a human might not have meant, such as a cylindrical
face's parameterisation seam. Excluding them would be a change to the selector
contract, and this stage did not make one.

### Fillet API

**`BRepFilletAPI_MakeFillet`**, driven directly: one builder per feature, every
matched edge added with `Add(radius, edge)`, then a single `Build()`. So the
blend is one atomic kernel operation over the whole selection — no subset is
ever attempted, and there is no path by which some edges get rounded and
others silently do not.

CadQuery's own `Solid.fillet(radius, edgeList)` is **not** used, for two
measured reasons:

- it calls `builder.Shape()` without checking `IsDone()`. Asking a not-done
  builder for its shape raises `StdFail_NotDone` **and leaves kernel state
  that segfaults a later fillet in the same process** — reproduced twice, and
  the reason this engine never touches `Shape()` unless `IsDone()` is true;
- it wraps the result as `Solid(...)` although the kernel returns a
  `Compound`, producing a mislabelled object whose `ShapeType()` is
  `"Compound"`.

Like the booleans, the builder returns a **`Compound`**; the single solid is
unwrapped after the checks below, so `LocalCadResult.shape` is always a
`Solid`.

### Rule E4 — the selector must match at least one edge

Checked before the kernel is called at all. An empty selection raises

```
GeometryOperationError: fillet 'round': its edge selector matched no edge of
target 'pin' (rule E4); a fillet that affects nothing indicates a misread
request, so no geometry is produced
```

The selector layer deliberately returns an empty tuple rather than raising, so
that this feature owns the rule and can name its own id and target.

### Rule E5 — the radius must be admissible for every selected edge

Three distinct kernel outcomes were measured, and **all three** are E5:

| Kernel outcome | Example | Detection |
|---|---|---|
| `Build()` raises `Standard_Failure` | filleting a cylinder's seam alone | the exception |
| `IsDone()` is false | radius 30 on the plate's vertical edges | `IsDone()`, plus `NbFaultyContours()` / `NbFaultyVertices()` in the message |
| done, but the result is not a valid solid | radius 6 on all twelve box edges | `isValid()` on the compound **and** on the unwrapped solid |

The third row is the important one. For radius 6 the kernel reports
`IsDone() == True`, returns one solid, and that solid has a volume of
**75513.93 mm³ — larger than the 60000 mm³ box it came from** — with a
bounding box spilling 0.09 mm outside the original. `IsDone()` alone would
have accepted corrupt geometry, which is exactly what E5 means by a radius
that "consumes neighbouring geometry".

No edge is ever dropped to make a radius work, and no partial blend is
applied: the whole feature fails and no geometry is produced.

Rule **E3** is checked too — the result must hold exactly one solid — and no
component is ever picked out of a multi-solid result.

### Failure atomicity

Measured rather than assumed of CadQuery. The source shape's full fingerprint
— shape type, volume, face/edge/vertex counts, validity, and every edge's
length and start point — is **unchanged** after:

- a successful build,
- a build that ends `IsDone() == False`,
- a build whose `Build()` raises.

On top of that, the engine only writes the result into the solid set after
every check has passed, and it re-evaluates the whole history from scratch on
each `build_part`, so a failed fillet leaves nothing behind anywhere. A test
interleaves failing and succeeding fillets three times over, which also guards
the crash hazard described above.

### Measured results

Primary case — 100 × 60 × 10 plate, radius 2, `axis_parallel Z`:

| Measurement | Value |
|---|---|
| selected edges | 4 (the vertical corners) |
| result | one valid `Solid` |
| volume | `59965.663706143576` mm³ |
| analytic reference `60000 − 4(r² − πr²/4)·10` | `59965.66370614359`, Δ 1.46e-11 |
| bounding box | (0,0,0) → (100,60,10), unchanged |
| topology | 10 faces, 24 edges, 16 vertices |
| surfaces | 6 planes + **4 cylinders of radius exactly 2.0**, axes along Z at (2,2), (2,58), (98,2), (98,58) |

The four corners really were rounded, and that is established from geometry,
not from the volume difference: the blend surfaces are cylinders of the
requested radius whose axes sit exactly `r` inside each corner, and a point at
(0.1, 0.1, 5) classifies **IN** on the plain box and **OUT** on the filleted
one, while points just inside each face stay IN.

Other measured cases:

| Case | Outcome |
|---|---|
| box, `all`, r = 2 | one valid solid; 26 faces = 6 planes + 12 cylinders + 8 spheres, every radius exactly 2.0; volume `59426.99687870704`, matching the Minkowski closed form for a rounded box **exactly** (Δ 0.0) |
| box, `axis_parallel Z`, r = 29 | succeeds. r = 30 fails. A measured bracket for this geometry, **not** a rule |
| box, `all`, r = 5 | `IsDone()` false → E5 |
| box, `all`, r ≥ 5.01 | done but invalid → E5 |
| 100 × 60 × 3 plate, `all`, r = 2 | E5 — a genuine mixed selection: `axis_parallel Z` alone succeeds at r = 2, `axis_parallel X` and `Y` alone each fail |
| drilled plate, `axis_parallel Z`, r = 2 | succeeds with the cavity seam in the selection; volume is the drilled volume minus exactly the four corner blends, and faces go 7 → 11, so the seam contributed nothing. **Stage 14 found the mechanism: the kernel builds no contour for the seam and silently ignores it** — see the note below |
| drilled plate, `all`, r = 1 | succeeds; 29 faces = 6 planes + 13 cylinders + 8 spheres + **2 tori** (the hole rims are blended too) |
| cylinder, `axis_parallel Z`, r = 0.5 or 2 | **`Build()` raises `Standard_Failure`** → E5. The seam alone cannot be blended |
| cylinder, `all`, r = 1 | succeeds — 5 faces (2 planes, 1 cylinder, 2 tori). With the rims in the selection the seam resolves, which is a kernel observation, not a rule |

### Supported histories

Tested and supported: `box → fillet`, `box → through_hole → fillet`,
`box → cylinder → subtract → fillet`, `cylinder → fillet`, and two fillets in
sequence (the second sees the first, because each replaces the target in
place). These all fall out of the existing ordered evaluator — no second
mechanism was introduced — but only the combinations above are tested, and
nothing broader is claimed.

### Known defect found in Stage 14: fillet can silently drop a matched edge

`BRepFilletAPI_MakeFillet.Add` accepts an edge the kernel considers unsuitable
— a parameterisation seam, or a smooth tangent edge — and then builds **no
contour** for it, reporting success for the rest. Measured coverage, comparing
the selected edges against the builder's own contour tables by topological
identity:

| Shape / selector | selected | taken into a contour | dropped |
|---|---|---|---|
| box, `all` / `axis_parallel Z` | 12 / 4 | 12 / 4 | 0 |
| drilled plate, `axis_parallel Z` | 5 | 4 | **1** (the cavity seam) |
| drilled plate, `all` | 15 | 14 | **1** |
| cylinder, `all` | 3 | 2 | **1** (the seam) |
| cylinder, `axis_parallel Z` | 1 | 0 | **1** |
| Z-filleted box, `all` | 24 | 16 | **8** (the tangency edges) |

So the Stage 13 fillet **does** silently skip a matched edge in those cases.
The geometric facts reported for Stage 13 are unchanged and correct — the
volume and face counts really are the drilled volume minus four corner blends
— but the explanation ("smooth across its own seam") was the wrong mechanism:
the edge was ignored, not blended to no effect.

Stage 14's `chamfer` **refuses** this case (see below), because "do not
silently skip a selected edge" was an explicit requirement of that stage.
`fillet` was left as delivered rather than changed outside its own stage, so
the two modifiers currently differ on identical input: a drilled plate's
`axis_parallel Z` fillet succeeds, and the same chamfer is refused with E5.
That inconsistency is a **known defect awaiting a decision**, not a design:
either fillet should adopt the coverage check (making drilled-plate corner
filleting impossible with the V1 selectors), or chamfer should relax it, or
Section C.7 should say what happens to a matched-but-unsuitable edge. It is a
gap in the contract, not a contradiction, so `docs/cad-specification.md` was
not changed.

## Chamfer semantics (Section C.6)

`chamfer` takes a `target`, one constant `distance` and an edge selector. Every
matched edge is replaced by a flat bevel that sets back `distance` on **both**
adjoining faces, and the target is replaced in place — the surviving solid
keeps the target's id, and the chamfer's own `id` appears only in error
messages.

V1 has no angle-driven chamfer, no asymmetric chamfer, no per-edge distance and
no face-based chamfer. One distance, one selection, one operation.

### Chamfer API

**`BRepFilletAPI_MakeChamfer`**, driven directly, using the **two-argument**
`Add(distance, edge)` overload. That overload is the kernel's own *symmetric*
chamfer: equal setback on both adjoining faces, and no reference face
argument — so there is no "which side is d1 measured from?" decision to get
wrong. The builder confirms it: `IsSymetric(i)` is true and `GetDist(i)`
returns the requested distance for every contour, both asserted in tests
rather than assumed.

CadQuery's `Solid.chamfer(length, length2, edgeList)` is **not** used:

- it calls the four-argument `Add(d1, d2, edge, face)` overload with a face
  taken from an edge→face ancestor map (`.First()`), introducing a reference
  face this contract does not need;
- it calls `builder.Shape()` without checking `IsDone()` — the same unchecked
  call that, on the *fillet* builder, was measured to corrupt kernel state;
- it wraps the result as `Solid(...)` although the builder returns a
  `Compound`.

Like the fillet and the booleans, the builder returns a **`Compound`**; the
single solid is unwrapped after the checks below.

### Validation strategy

Six checks, in order. A kernel status flag alone is never accepted as success:

| # | Check | Failure |
|---|---|---|
| 1 | target resolves in the solid set | S6 |
| 2 | `distance > 0` | S17 |
| 3 | the selector matched at least one edge | **E4** — the kernel is not called |
| 4 | **every** matched edge was taken into a contour | **E5** |
| 5 | `Build()` does not raise, and `IsDone()` is true | **E5** |
| 6 | exactly one solid, valid per the kernel's own analysis, with positive volume | **E3** / **E5** |

Check 4 is the one fillet does not have. `Add` silently ignores an edge the
kernel deems unsuitable, so coverage is verified from the builder's own
`NbContours`/`NbEdges`/`Edge` tables, with membership decided by topological
identity (`IsSame`) rather than coordinates. Any dropped edge fails the whole
feature as E5 — an edge no distance can bevel is not one the distance is
admissible for — and the message says how many were dropped and why.

The reverse direction is **not** an error and is recorded rather than
enforced: contour propagation follows tangency, so a contour may contain *more*
edges than were selected, and the kernel then bevels those neighbours too.
Measured on a Z-filleted box, an `axis_parallel X` selection of 4 edges
produced 2 contours spanning 16 edges.

### Rule E4

```
GeometryOperationError: chamfer 'bevel': its edge selector matched no edge of
target 'pin' (rule E4); a chamfer that affects nothing indicates a misread
request, so no geometry is produced
```

### Rule E5, and how it differs from fillet's

Three measured failure modes, all E5 — and the set is **not** the same as
fillet's, which is why they were measured rather than assumed:

| Kernel outcome | Example | Detection |
|---|---|---|
| an edge is taken into no contour | the cavity seam of a drilled plate | the coverage check (before `Build()`) |
| `IsDone()` is false | distance 30 on the plate's vertical edges; distance ≥ 5 on all twelve | `IsDone()`, with `NbContours()` in the message |
| `Build()` raises `Standard_Failure` | a selection made only of unsuitable edges — the kernel's message is *"There are no suitable edges for chamfer or fillet"* | the exception |

**The difference from fillet:** the fillet builder has a *done-but-invalid*
regime — above a threshold it reports success and returns a solid whose volume
exceeds the original. No chamfer distance tried produced that: 5.0, 5.01, 6.0
and 8.0 on all twelve edges all report `IsDone() == False`. The validity check
is kept anyway, because a status flag is not evidence.

The chamfer builder also has **no** `NbFaultyContours()` / `NbFaultyVertices()`
— those are fillet-only — so the not-done message carries the contour count
instead.

### Failure atomicity

Measured for chamfer directly, not inherited. The source shape's full
fingerprint — shape type, volume, face/edge/vertex counts, validity, and every
edge's length and start point — is unchanged after a successful build, after a
not-done build, and after a build that raises.

**The fillet crash hazard was not reproduced for chamfer.** Asking a not-done
`BRepFilletAPI_MakeFillet` for its shape segfaults a later fillet in the same
process; the same sequence on `BRepFilletAPI_MakeChamfer` raised
`StdFail_NotDone` four times and then completed further chamfers normally. The
engine applies the same discipline regardless — it never calls `Shape()` unless
`IsDone()` — and a test interleaves failing and succeeding chamfers three times
over.

### Measured results

Primary case — 100 × 60 × 10 plate, distance 2, `axis_parallel Z`:

| Measurement | Value |
|---|---|
| selected edges | 4 (the vertical corners) |
| result | one valid `Solid` |
| volume | `59920.00000000001` mm³ |
| analytic reference `60000 − 4(d²/2)·10` | `59920.0`, Δ 7.3e-12 |
| bounding box | (0,0,0) → (100,60,10), unchanged |
| topology | 10 faces, 24 edges, 16 vertices |
| surfaces | **10 planes** — a chamfer never introduces curvature |
| bevel planes | 4, normals exactly (±1, ±1, 0)/√2 (all four sign combinations), area `28.284271247` = 2√2 × 10 each |
| setback | bevel vertices exactly at (0, 2) and (2, 0) per corner, i.e. `d` on **both** faces |

The corners really were bevelled, and that is established from geometry: the
bevel planes bisect the two faces they join, their vertices sit exactly `d`
from the original corner along each axis, and a point at (0.1, 0.1, 5) is IN on
the plain box and OUT on the chamfered one, while points just inside each face
stay IN.

Other measured cases:

| Case | Outcome |
|---|---|
| box, `all`, d = 2 | one valid solid; **26 planes** = 6 original + 12 bevels + **8 corner triangles**, 48 edges, 24 vertices |
| corner triangles | on the plane `x+y+z = 2d` in corner-local coordinates — vertices exactly (0,d,d), (d,0,d), (d,d,0), normal (±1,±1,±1)/√3 |
| box, `all` volume | matches the derived closed form `abc − 2d²(a+b+c) + (16/3)d³` at d = 0.5, 1, 2, 3 and 4, worst Δ 7.3e-12 |
| box, `axis_parallel Z`, d = 29 | succeeds. d = 30 fails. A measured bracket for this geometry, **not** a rule |
| 100 × 60 × 3 plate, `all`, d = 2 | E5 — a genuine mixed selection: `axis_parallel Z` alone succeeds at d = 2, `X` and `Y` alone each fail. d = 1 succeeds for `all` |
| drilled plate, `axis_parallel Z` or `all` | **E5** — the cavity seam is matched and the kernel will not bevel it |
| drilled plate, `axis_parallel X` / `Y`, d = 2 | succeed; volume is the drilled volume minus `4(d²/2)·100` (or ·60) exactly, and the Ø20 hole survives |
| cylinder, `axis_parallel Z` (the seam) | **E5** at every distance — the kernel builds no contour for it |
| cylinder, `all`, d = 1 | **E5** by the coverage check. The kernel alone *would* have succeeded, bevelling both rims into conical faces (5 faces: 1 cylinder, 2 cones, 2 planes) and ignoring the seam. Refusing is the cost of the no-silent-skip rule, and it is recorded, not hidden |

The derivation of the `all` closed form: twelve wedges of triangular section
`d²/2` (`2d²(a+b+c)`), less three pairwise overlaps of `d³/3` per corner, plus
the triple overlap `d³/4` per corner, plus the corner tetrahedron `d³/12` per
corner that the `x+y+z = 2d` face removes — which collapses to
`2d²(a+b+c) − (16/3)d³`.

### Supported histories

Tested: `box → chamfer`, `box → through_hole → chamfer`,
`box → cylinder → subtract → chamfer`, two chamfers in sequence, and
`box → chamfer → fillet`. One document exercising **all six V1 feature types**
builds to a single solid.

One measured ordering asymmetry, recorded rather than explained: on the same
body, `chamfer X → fillet Y` builds while `fillet Y → chamfer X` fails E5. The
first operation changes the edges the second selects, and modifier order is
therefore significant even when the selections look disjoint.

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

- **The V1 feature set is complete.** Nothing in Section C is unimplemented.
  There are still no hole patterns — four holes are four `through_hole`
  features, and the engine has no pattern concept — and nothing beyond V1
  exists: no sketches, lofts, sweeps, threads, assemblies, materials or
  simulation.
- **Only subtraction.** V1 defines no union and no intersection, and neither is
  implemented. `Shape.fuse` and `Shape.intersect` exist in CadQuery; the latter
  is used *internally* to decide overlap, and neither is reachable as a
  feature.
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

- **One solid at the end.** Multi-body parts and assemblies are rejected.
  Several primitives may exist *during* evaluation, but rule S9 requires
  exactly one solid after the last feature, so every extra one must be
  consumed as a `subtract` tool.
- **A modifier always replaces its target, and a tool is always consumed.**
  There is no way to keep both the original and the modified body, or to reuse
  a tool for a second cut — V1 has no vocabulary for either. A part that needs
  the same shape removed twice must declare two tools.
- **Subtraction only, one target at a time.** No union, no intersection, no
  boolean expression tree, and no multi-target operation.
- The engine trusts the validator. A hand-built `Part` that bypasses validation
  with, say, a zero extent would reach the kernel and fail there rather than
  being caught politely — by design, since re-validating would duplicate
  Stage 2 and repairing is forbidden. The rule S6 target check in the
  evaluator is the one exception, and exists only because the evaluator cannot
  proceed without a target.
- **All five geometric rules are now enforced**, each for the features it
  applies to: E1 for `through_hole`, E2 and E3 for the cutting features, and
  E3, E4 and E5 for both `fillet` and `chamfer`.
- **Fillet and chamfer limitations.** One constant radius or distance per
  feature; no per-edge value, no variable radius, no angle-driven chamfer, no
  asymmetric chamfer, no face-based blend, and no way to exclude an edge the
  selector matched. The two V1 selectors are the only vocabulary, so "round
  these three corners but not that one" is not expressible.
- **A chamfer cannot be applied to any selection containing a
  parameterisation seam.** That currently rules out `axis_parallel Z` and
  `all` on a drilled plate, and any axis selector on a bare cylinder. It
  follows from refusing to skip a matched edge, and it is the strongest
  argument that Section C.7 needs a sentence about matched-but-unsuitable
  edges.
- **An admissible radius or distance is not predicted, only tested.** The
  engine asks the kernel and reports E5 when it objects; it computes no
  maximum, and the measured brackets in this document are observations about
  particular geometries, not rules.
- **Modifier order matters and is not analysed.** `chamfer X → fillet Y`
  builds where `fillet Y → chamfer X` fails; the engine does not reorder,
  warn, or explain.
- **A kernel crash hazard exists and is avoided rather than fixed.** Asking a
  not-done `BRepFilletAPI_MakeFillet` for its shape corrupts kernel state and
  segfaults a later fillet in the same process. This engine never does it, and
  a test interleaves failing and succeeding fillets to keep that true — but
  the hazard is in OpenCascade (and reachable through CadQuery's own
  `Solid.fillet`), not something this project can repair.
- **Two open questions in the contract, both recorded rather than resolved
  quietly.** (1) V1 does not classify a `subtract` tool that removes no
  material; the engine refuses it, and a future revision should either add a
  rule or state that it is permitted. (2) V1 does not say whether E3 applies to
  each intermediate cut inside a multi-tool `subtract` or only to the feature's
  result; the engine reads it as the feature's result, which is what E.2 says,
  so a subtract that severs the body and then trims away the extra piece is
  accepted. Both readings are documented above with the measurements that
  distinguish them. Neither is a contradiction in the specification — they are
  gaps — so `docs/cad-specification.md` was not changed.
- **Tangency is not classified.** A centreline that passes exactly through a
  face or edge of the target is left to the kernel's own tolerance rather than
  being detected and reported as a distinct condition. The specification does
  not define that case for V1, and the engine does not invent an answer.
- Agreement between the two backends is untested, and cannot be tested until
  the Onshape path runs. Both are built from the same specification section,
  but that is an argument, not evidence.
