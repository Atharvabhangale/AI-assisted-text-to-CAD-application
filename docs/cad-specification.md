# CAD Specification Contract — V1

Status: **V1**
Schema version described by this document: **`1.2.0`**

"V1" names the contract's major version, `1`. Version `1.1.0` adds one feature
type, `union`, to `1.0.0` and changes nothing else: every `1.0.0` document is
still valid and still means what it meant (Section F). Version `1.1.1` narrows
the edge selectors to sharp edges (Section C.7), which only turns blends that
used to fail into blends that build. Version `1.2.0` adds optional parameters
and nothing else: a taper and a polygon cross-section on `cylinder` (Section
C.2), and a pattern that copies, turns and shifts the tools of a `subtract` or
a `union` (Sections C.4, C.8). Every default means what `1.1.1` meant.

## Purpose

This document defines the internal representation ("the CAD specification") for
a simple parametric mechanical part. It is the stable contract between three
stages of the system:

```
Natural-language interpretation
        ↓
Structured CAD specification   ← defined by this document
        ↓
Deterministic CAD engine
```

The specification is a **data document**, not a program. It is expressed as
JSON. It describes *what* the part is, never *how* to compute it.

## Scope of V1

V1 supports exactly these concepts and nothing else:

| # | Concept | Representation |
|---|---------|----------------|
| 1 | Part | the root document |
| 2 | Units | `units` field on the part |
| 3 | Primitive box | feature type `box` |
| 4 | Cylinder | feature type `cylinder` |
| 5 | Through-hole | feature type `through_hole` |
| 6 | Position | `position` object on features |
| 7 | Boolean subtraction | feature type `subtract` |
| 8 | Fillet | feature type `fillet` |
| 9 | Chamfer | feature type `chamfer` |
| 10 | Boolean union | feature type `union` (from `1.1.0`) |
| 11 | Taper and regular polygon | `top_diameter` and `sides` on `cylinder` (from `1.2.0`) |
| 12 | Tool pattern: copies of a tool, turned and shifted | `pattern_count`, `pattern_axis`, `pattern_angle`, `pattern_step` on `subtract` and `union` (from `1.2.0`) |

The following are **explicitly out of scope for V1** and MUST be rejected by a
V1 validator rather than partially interpreted: assemblies, sketches, splines,
lofts, sweeps, revolves, threads, arbitrary patterns of features, GD&T,
tolerances, materials, simulation, manufacturing rules, scripting or
expressions of any kind, and any constraint not defined in this document.

The one pattern V1 has is the **tool pattern** of `1.2.0` (Sections C.4 and
C.8): a `subtract` or a `union` may replace each of its tool solids by copies
of it, turned about and shifted along a principal axis through the origin.
Nothing else is patterned: there is no pattern of a feature, of a hole, of a
fillet or of a whole part, and no pattern along a curve.

Any unknown field or unknown feature type is an error (see rule **S3**). V1 is
deliberately strict: silent tolerance of unknown input is what makes a contract
unstable.

---

## A. Coordinate system

### A.1 Axes and handedness

A single **right-handed Cartesian** coordinate system is used, called the
**part coordinate system (PCS)**.

| Axis | Direction | Conventional meaning |
|------|-----------|----------------------|
| `+X` | to the right | width |
| `+Y` | away from the viewer (into the scene) | depth |
| `+Z` | up | height |

Right-handedness fixes the cross products: `X × Y = Z`, `Y × Z = X`,
`Z × X = Y`. There is no alternative orientation and no "up axis" setting.

### A.2 Origin

The origin of the PCS is the point `(0, 0, 0)`. There is exactly one coordinate
system per part. V1 has **no local coordinate systems and no datums** — every
position in the document is an absolute position in the PCS.

The only movement of a solid is the **tool pattern** of a `subtract` or a
`union` (from `1.2.0`, Sections C.4 and C.8): its copies of a tool are turned
about a principal axis **through the origin** and shifted along it. No feature
has a rotation of its own, and nothing else is ever moved.

### A.3 Units

The unit system is carried **inside the document**; it is never implied by
context, file name, or consumer default.

- The part declares its unit system in the required `units` field.
- **V1 accepts the single value `"mm"`** (millimetres). Other unit systems are
  reserved for a later schema version; a V1 validator MUST reject any other
  value rather than convert it (rule **S5**).
- Every length in the document — sizes, diameters, positions, radii, chamfer
  distances, pattern steps — is a number expressed in the declared unit.
- The one angular parameter, `pattern_angle` (from `1.2.0`), is in
  **degrees**, whatever the declared unit. No other parameter is an angle.

Millimetres are the default and the example unit throughout this document.

### A.4 How positions are represented

A **position** is a point in the PCS, written as a JSON object with all three
components required and no others:

```json
{ "x": 10, "y": 10, "z": 0 }
```

- Components are JSON numbers, in the part's declared unit.
- Components may be negative or zero; only *dimensions* are required to be
  positive (see Section E).
- The array form `[10, 10, 0]` is **not** valid. Named components are used so a
  malformed position cannot be silently misread as a different point.

A **size** uses the same shape, but its components are extents along each axis
and MUST be positive:

```json
{ "x": 100, "y": 60, "z": 10 }
```

An **axis** parameter is one of the six signed principal directions, as a
string: `"+X"`, `"-X"`, `"+Y"`, `"-Y"`, `"+Z"`, `"-Z"`. Arbitrary direction
vectors are not supported in V1. Two parameters name an axis **without** a
sign, as the letter `"X"`, `"Y"` or `"Z"`: the `axis_parallel` edge selector
(Section C.7) and `pattern_axis` (Sections C.4, C.8).

---

## B. Part structure

### B.1 The part document

The root of the document is a single part:

```json
{
  "schema_version": "1.1.0",
  "units": "mm",
  "name": "example-plate",
  "description": "optional free text",
  "features": [ /* ordered list of features */ ]
}
```

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `schema_version` | string | yes | Version of this contract (Section F) |
| `units` | string | yes | Unit system; `"mm"` in V1 (Section A.3) |
| `name` | string | yes | Human-readable identifier for the part |
| `description` | string | no | Free text; carries no geometric meaning |
| `features` | array | yes | Ordered feature list, at least one entry |

A part produces exactly one solid body. V1 has no assemblies and no
multi-body parts (rule **S9**).

### B.2 Features are ordered

`features` is an **ordered** list and the order is significant. The engine
evaluates features strictly from index `0` upward. Reordering features may
change the resulting geometry (for example, filleting an edge before or after
cutting a hole through it), so the order is part of the contract, not an
implementation detail.

### B.3 Feature identity

Every feature has an `id`:

- `id` is a string matching `^[A-Za-z_][A-Za-z0-9_-]*$`.
- `id` MUST be unique within the part.
- References between features use these ids. A reference may only point to a
  feature that appears **earlier** in the list (rule **S7**) — forward and
  circular references are invalid.

Every feature has this common shape:

```json
{ "id": "plate", "type": "box", "...": "type-specific parameters" }
```

### B.4 Evaluation model: the solid set

The engine maintains an ordered **solid set**. Each entry is a solid identified
by a feature id. Features fall into two categories:

**Constructive features** (`box`, `cylinder`) add a new solid to the solid set,
named by the feature's own `id`.

**Modifier features** (`through_hole`, `subtract`, `fillet`, `chamfer`,
`union`) act on an existing solid named by their `target` field. A modifier
replaces its target **in place**: the resulting solid keeps the **target's** id
and its position in the solid set. The modifier's own `id` is a label used for
traceability and error reporting; it never names a solid.

Consequently a plate with four holes keeps the id `plate` throughout, and each
hole's `target` is `plate`.

`subtract` and `union` additionally **consume** their tool solids: each id
listed in `tools` is removed from the solid set. After a `union` the joined
solid is named by the union's `target`; the tools' ids no longer name anything.

Two solids become one only through an explicit `union`. Solids that merely
overlap or touch are never merged implicitly.

After the last feature is evaluated, the solid set MUST contain exactly one
solid (rule **S9**). That solid is the part's geometry. A leftover cylinder that
was created but never subtracted or joined is therefore an error, not a second
body.

---

## C. Feature definitions

Notation used in the tables below: *length* means a JSON number in the part's
declared unit; *position* and *size* mean the objects from Section A.4; *axis*
means a signed principal direction string; *ref* means a feature id.

### C.1 `box`

An axis-aligned rectangular solid. Constructive.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"box"` | yes | — | — |
| `size` | size | yes | — | length |
| `position` | position | no | `{"x":0,"y":0,"z":0}` | length |

**Behavior.** `position` is the box's **minimum corner** — the corner with the
smallest `x`, `y` and `z`. The box occupies
`[position.x, position.x + size.x] × [position.y, position.y + size.y] ×
[position.z, position.z + size.z]`. A box is built axis-aligned: its edges are
parallel to the PCS axes. It is turned only as a tool, by a pattern (Sections
C.4, C.8) — for example a `pattern_count` of 1 with a `pattern_angle` of 30
fuses one copy of the box turned 30° about the axis through the origin.

**Validation.** `size.x`, `size.y`, `size.z` MUST each be `> 0` (rule **S10**).
`position` components are unconstrained.

### C.2 `cylinder`

A right circular cylinder of finite height. Constructive. In V1 a cylinder is
used either as the part's base solid or as a tool for `subtract` or `union`.
From `1.2.0` the same feature also makes a cone frustum, a pointed cone, a
regular polygon prism and a regular pyramid frustum.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"cylinder"` | yes | — | — |
| `diameter` | length | yes | — | length |
| `height` | length | yes | — | length |
| `position` | position | no | `{"x":0,"y":0,"z":0}` | length |
| `axis` | axis | no | `"+Z"` | — |
| `top_diameter` | length | no | equal to `diameter` | length |
| `sides` | integer | no | `0` | — |

`top_diameter` and `sides` are defined from schema version `1.2.0`; in a
document that declares an earlier version they are unknown fields (rule
**S3**, Section F).

**Behavior.** `position` is the **centre of the base**. The solid extends
`height` from the base in the direction given by `axis`. The base is the circle
of `diameter`; the far end, `height` along `axis` from `position`, is the circle
of `top_diameter`, centred on the same line.

- `top_diameter` equal to `diameter` (the default) is a right circular
  cylinder of radius `diameter / 2` — exactly the `1.1.1` cylinder.
- `top_diameter` different from `diameter` is a **taper**: a cone frustum whose
  side is straight from the base circle to the far circle. It may widen or
  narrow.
- `top_diameter` of `0` is a **pointed cone**, its apex `height` along `axis`
  from `position`.

`sides` chooses the cross-section. `0` (the default) is round. `3` to `12` is
a **regular polygon** of that many sides whose **corners** lie on the circle
of `diameter` at the base and on the circle of `top_diameter` at the far end:
a polygon prism when the two are equal, a pyramid frustum when they differ.
The flats are therefore nearer the axis than the corners: a hexagon of
`diameter` `d` measures `d` across its corners and `d · √3/2` across its flats.

A polygon's orientation is fixed: **one corner points along the first
perpendicular of the axis** —

| `axis` | first corner points toward |
|--------|----------------------------|
| `"+Z"`, `"-Z"` | `+X` |
| `"+X"`, `"-X"` | `+Y` |
| `"+Y"`, `"-Y"` | `+Z` |

So a hexagon on `+Z` has corners at `±X` and flats facing `±Y`. To turn a
polygon to another orientation, use it as a tool with a pattern of one copy
(`pattern_count` 1 and a `pattern_angle`, Sections C.4 and C.8).

**Validation.** `diameter > 0` and `height > 0` (rule **S11**). `axis` MUST be
one of the six signed principal directions (rule **S12**). `top_diameter` is a
finite number `>= 0`, and `> 0` when `sides` is not `0` — a cone may come to a
point, a pyramid may not (rule **S23**). `sides` is an integer, `0` or `3` to
`12` (rule **S24**).

### C.3 `through_hole`

A cylindrical cut that passes completely through the target solid. Modifier.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"through_hole"` | yes | — | — |
| `target` | ref | yes | — | — |
| `diameter` | length | yes | — | length |
| `position` | position | yes | — | length |
| `axis` | axis | no | `"+Z"` | — |

**Behavior.** The hole's centreline is the infinite line through `position` in
the direction of `axis`. The material removed from the target is the infinite
cylinder of the given diameter about that centreline, intersected with the
target solid — so the cut always emerges on both sides and no depth parameter
exists.

Because the cut is unbounded along the axis, the component of `position` along
`axis` has **no effect** on the result: only the two components perpendicular to
`axis` locate the hole. Writing `z: 0` for a `+Z` hole is conventional.

`through_hole` is a convenience for the common natural-language phrasing
"a through-hole". It is equivalent to subtracting an unbounded cylinder, and is
provided directly so the interpreter does not have to compute a tool length
from the target's extent.

**Validation.** `diameter > 0` (rule **S13**). `target` MUST resolve to a solid
in the solid set (rule **S6**). The centreline MUST actually intersect the
target solid, and the resulting solid MUST remain a single connected body —
both are geometric checks (rules **E1**, **E3**).

### C.4 `subtract`

General boolean subtraction. Modifier.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"subtract"` | yes | — | — |
| `target` | ref | yes | — | — |
| `tools` | array of ref | yes | — | — |
| `pattern_count` | integer | no | `1` | — |
| `pattern_axis` | `"X"` \| `"Y"` \| `"Z"` | no | `"Z"` | — |
| `pattern_angle` | number | no | `0` | degrees |
| `pattern_step` | length | no | `0` | length |

The four `pattern_*` parameters are defined from schema version `1.2.0`; in a
document that declares an earlier version they are unknown fields (rule
**S3**, Section F).

**Behavior.** Each solid listed in `tools` is removed from the target, in list
order. The result replaces the target in place (Section B.4) and every tool
solid is consumed — removed from the solid set and unavailable to later
features.

**Pattern.** Every tool `T` in `tools` is replaced by its copies `T_k`, for
`k = 1 … pattern_count`:

```
T_k = shift( turn(T, k · pattern_angle degrees about the line through the
                  origin (0,0,0) parallel to pattern_axis),
             k · pattern_step along +pattern_axis )
```

and the copies are removed from the target as if they had been listed in
`tools` individually: tool by tool in list order, and for each tool in `k`
order. A positive angle turns by the right-hand rule — counter-clockwise when
viewed from the positive end of the axis looking toward the origin (about `Z`,
`+X` turns toward `+Y`). With the defaults (`1`, `"Z"`, `0`, `0`) the one copy
`T_1` is `T` itself, so a subtract without pattern parameters means exactly
what it meant in `1.1.1`.

- **A circle of `n` copies** — a bolt circle: `pattern_count` `n`,
  `pattern_angle` `360 / n`. The copies sit at `360/n`, `2 · 360/n`, …,
  `n · 360/n = 360` degrees, so the last copy is the tool's own place and the
  tool's own position is one of the `n`. Put the tool at one of the places,
  e.g. the hole on `+X` at the bolt-circle radius.
- **A single turn** — "turn this cut by 30°": `pattern_count` `1`,
  `pattern_angle` `30`. The turn is about the axis line **through the origin**,
  not about the tool's own centre, so build the tool where it should be before
  turning — a tool centred on that axis turns in place.
- **A row of `n` copies** — slots at a spacing: `pattern_count` `n`,
  `pattern_step` `d` along a `pattern_axis`. The copies sit at `d`, `2d`, …,
  `nd` from the tool, so place the tool **one step before** the first copy
  wanted. A negative `pattern_step` runs the row toward the negative end.
- Turn and step together make a helix-like stair of copies.

The pattern copies tools only; it never copies the target or other features.
Each copy is treated exactly as a tool listed in `tools` would be, and the
result MUST be a single non-empty connected solid (rules **E2**, **E3**).

Joining solids is a separate feature, `union` (Section C.8). Boolean
intersection is not part of this contract.

**Validation.** `tools` MUST be a non-empty array (rule **S14**). Every id in
`target` and `tools` MUST resolve to a solid in the solid set (rule **S6**).
`target` MUST NOT appear in `tools`, and `tools` MUST NOT contain duplicates
(rule **S15**). `pattern_count` is an integer from `1` to `64` (rule **S25**),
`pattern_axis` is `"X"`, `"Y"` or `"Z"` (rule **S26**), `pattern_angle` and
`pattern_step` are finite and `|pattern_angle| <= 360` (rule **S27**), and no
two copies coincide (rule **S28**). The result MUST be a single non-empty
connected solid (rules **E2**, **E3**).

### C.5 `fillet`

Rounds selected edges of the target with a constant radius. Modifier.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"fillet"` | yes | — | — |
| `target` | ref | yes | — | — |
| `radius` | length | yes | — | length |
| `edges` | edge selector | yes | — | — |

**Behavior.** Every edge matched by `edges` on the target solid is replaced by a
constant-radius circular blend of radius `radius`. Only constant-radius fillets
exist in V1: no variable radius, no per-edge radius, no setback at vertices
beyond whatever the engine must do to close the surfaces.

**Validation.** `radius > 0` (rule **S16**). The selector MUST match at least
one edge (rule **E4**). The radius MUST be geometrically admissible for every
matched edge (rule **E5**).

### C.6 `chamfer`

Bevels selected edges of the target with a symmetric setback. Modifier.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"chamfer"` | yes | — | — |
| `target` | ref | yes | — | — |
| `distance` | length | yes | — | length |
| `edges` | edge selector | yes | — | — |

**Behavior.** Every edge matched by `edges` on the target solid is replaced by a
flat bevel that sets back `distance` on **both** adjoining faces (an equal-
distance chamfer). Asymmetric and angle-driven chamfers are not in V1, which is
why `chamfer` has no angle parameter.

**Validation.** `distance > 0` (rule **S17**). The selector MUST match at least
one edge (rule **E4**). The distance MUST be geometrically admissible for every
matched edge (rule **E5**).

### C.7 Edge selector

`fillet` and `chamfer` need to name edges. V1 provides two deterministic
selectors and no others; persistent named-topology selection is deferred to a
later version.

```json
{ "select": "all" }
```

Matches every **sharp** edge of the target solid.

```json
{ "select": "axis_parallel", "axis": "Z" }
```

Matches every **sharp, straight** edge of the target solid that is parallel
to the given axis. Circular and non-linear edges never match this selector, so
filleting the vertical corners of a drilled plate does not touch the hole rims.

**Only sharp edges are selected** (since `1.1.1`). An edge is *sharp* when two
different faces of the solid meet along it at an angle — a crease that can be
rounded or bevelled. Two kinds of edge are not sharp, and no selector matches
them:

- a **seam**, where a closed surface meets itself: the straight line running
  down the side of every cylinder and every hole. The same face lies on both
  sides of it;
- a **smooth edge**, where two faces meet tangentially, such as the boundary
  of a fillet. The surface turns through no angle there.

Neither can be blended, and neither is an edge a person would point at. So
`{"select": "all"}` on a cylinder matches its two rims, on a drilled plate its
twelve outer edges and the hole rims, and on an already-filleted body only the
creases that remain. A fillet propagates along edges that meet tangentially,
so rounding one edge of a tangent chain rounds the whole chain; a later
selector that finds no sharp edge left matches nothing, which is rule **E4**.

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `select` | `"all"` \| `"axis_parallel"` | yes | — |
| `axis` | `"X"` \| `"Y"` \| `"Z"` | only for `axis_parallel` | **unsigned**: parallelism has no direction |

**Validation.** `axis` MUST be present for `axis_parallel` and absent for `all`
(rule **S18**). The unsigned axis letters here are intentionally different from
the signed axis values used by `cylinder` and `through_hole`.

### C.8 `union`

Boolean union: joins solids into one body. Modifier. Defined from schema
version `1.1.0`; in a document that declares an earlier version it is an
unknown feature type (rule **S3**, Section F).

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"union"` | yes | — | — |
| `target` | ref | yes | — | — |
| `tools` | array of ref | yes | — | — |
| `pattern_count` | integer | no | `1` | — |
| `pattern_axis` | `"X"` \| `"Y"` \| `"Z"` | no | `"Z"` | — |
| `pattern_angle` | number | no | `0` | degrees |
| `pattern_step` | length | no | `0` | length |

The four `pattern_*` parameters are defined from schema version `1.2.0`, as for
`subtract`; in a document that declares an earlier version they are unknown
fields (rule **S3**).

**Behavior.** Each solid listed in `tools` is fused into the target, in list
order. The result is the one solid occupying every point that lies in the
target or in any tool; material that operands share is counted once. The result
replaces the target in place (Section B.4) — it keeps the **target's** id and
its position in the solid set — and every tool solid is consumed: removed from
the solid set and unavailable to later features. The union's own `id` never
names a solid; later features refer to the joined body by the target's id.

**Pattern.** Exactly as for `subtract` (Section C.4): every tool `T` is
replaced by its copies `T_k`, `k = 1 … pattern_count`, turned by
`k · pattern_angle` degrees about the line through the origin parallel to
`pattern_axis` and shifted `k · pattern_step` along it, and the copies are
fused into the target tool by tool, in `k` order. With the defaults the one
copy is the tool itself. Six arms around a hub on the `Z` axis are one arm
solid on `+X` that overlaps the hub, with `pattern_count` `6` and
`pattern_angle` `60`; a rib turned by 30° is `pattern_count` `1` and `pattern_angle` `30`.

Apart from a pattern, `union` moves nothing. Each operand — and each pattern
copy — stays where its `position` and the pattern put it, so whether two solids
join is decided entirely by where they already are.

The joined body is a single solid, not its operands side by side: the surfaces
where operands met disappear, and operand faces that lie on the same surface
become one face. Its edges are the edges of the joined body, so an edge selector
(Section C.7) applied after a `union` never matches a seam left over from how
the body was assembled.

**Validation.** `tools` MUST be a non-empty array (rule **S21**). Every id in
`target` and `tools` MUST resolve to a solid in the solid set (rule **S6**).
`target` MUST NOT appear in `tools`, and `tools` MUST NOT contain duplicates
(rule **S22**). The pattern parameters follow rules **S25**–**S28**, as for
`subtract`. The result MUST be a single connected solid (rule **E3**): the
target and the tools — every pattern copy included — must form one body by
**overlapping or sharing a face**.
Solids that are apart, or that touch only along an edge or at a point, are not
joined by a `union` — that is an error, never a multi-body result and never a
no-op.

---

## D. Example specification

### D.1 A plate with four holes

Natural-language request:

> "A 100 mm × 60 mm × 10 mm plate with four Ø8 mm through-holes, positioned
> 10 mm from each corner."

Interpretation: an axis-aligned plate with its minimum corner at the origin,
100 mm along X, 60 mm along Y, 10 mm thick along Z. "10 mm from each corner" is
read as each hole centre being 10 mm from both adjacent edges, giving centres at
`(10, 10)`, `(90, 10)`, `(10, 50)` and `(90, 50)`. The holes run along `+Z`
through the 10 mm thickness.

```json
{
  "schema_version": "1.0.0",
  "units": "mm",
  "name": "plate-100x60x10-4holes",
  "description": "100 x 60 x 10 mm plate with four 8 mm through-holes, 10 mm from each corner",
  "features": [
    {
      "id": "plate",
      "type": "box",
      "size": { "x": 100, "y": 60, "z": 10 },
      "position": { "x": 0, "y": 0, "z": 0 }
    },
    {
      "id": "hole_front_left",
      "type": "through_hole",
      "target": "plate",
      "diameter": 8,
      "position": { "x": 10, "y": 10, "z": 0 },
      "axis": "+Z"
    },
    {
      "id": "hole_front_right",
      "type": "through_hole",
      "target": "plate",
      "diameter": 8,
      "position": { "x": 90, "y": 10, "z": 0 },
      "axis": "+Z"
    },
    {
      "id": "hole_back_left",
      "type": "through_hole",
      "target": "plate",
      "diameter": 8,
      "position": { "x": 10, "y": 50, "z": 0 },
      "axis": "+Z"
    },
    {
      "id": "hole_back_right",
      "type": "through_hole",
      "target": "plate",
      "diameter": 8,
      "position": { "x": 90, "y": 50, "z": 0 },
      "axis": "+Z"
    }
  ]
}
```

Notes on the example:

- Each hole targets `plate`, because a modifier replaces its target in place and
  keeps the target's id (Section B.4).
- `z: 0` on the holes is conventional: a through-hole's cut is unbounded along
  its axis, so the axial component of `position` does not affect the result.
- The solid set contains exactly one solid (`plate`) at the end, as required.
- No fillet or chamfer is requested, so none appears. The specification never
  adds features that the request did not state.
- The document uses nothing newer than `1.0.0` and declares that version. It
  is unchanged by `1.1.0` and remains valid (Section F).

### D.2 Two primitives joined: an L-bracket

Natural-language request:

> "An L-bracket: a 60 mm × 40 mm base plate 5 mm thick, with a 50 mm tall
> upright 5 mm thick along one 40 mm edge."

Interpretation: the base is a box with its minimum corner at the origin. The
upright stands on the base along the edge at `x = 0`, so it is a second box
5 mm along X and 40 mm along Y. Its overall height is 50 mm measured from the
underside of the base, so it starts on the base's top face at `z = 5` and is
45 mm tall. The two boxes share the 5 mm × 40 mm face at `z = 5`, which is what
lets `union` join them.

```json
{
  "schema_version": "1.1.0",
  "units": "mm",
  "name": "l-bracket-60x40x50",
  "description": "L-bracket: 60 x 40 x 5 mm base with a 5 mm thick upright, 50 mm tall overall",
  "features": [
    {
      "id": "base",
      "type": "box",
      "size": { "x": 60, "y": 40, "z": 5 },
      "position": { "x": 0, "y": 0, "z": 0 }
    },
    {
      "id": "upright",
      "type": "box",
      "size": { "x": 5, "y": 40, "z": 45 },
      "position": { "x": 0, "y": 0, "z": 5 }
    },
    {
      "id": "join_upright",
      "type": "union",
      "target": "base",
      "tools": ["upright"]
    }
  ]
}
```

Notes on the example:

- Both boxes are constructive, so after the second feature the solid set holds
  two solids. The `union` leaves one, named `base`: `upright` is consumed, and
  `join_upright` never names a solid (Section B.4). A later hole or fillet on
  the bracket would therefore use `"target": "base"`.
- The upright could equally have been written from `z = 0` with height 50,
  overlapping the base instead of standing on it. The joined solid is the same:
  shared material is counted once.
- Leave the `union` out and the document is invalid (rule **S9**: two solids
  remain). Move the upright to `x = 70` and it is still statically valid, but
  the engine rejects it (rule **E3**): the boxes no longer touch.
- The document uses `union`, so it declares `1.1.0`.

---

## E. Validation rules

Validation is **deterministic**: the same document always produces the same
verdict and the same error list. A validator MUST report *all* violations it can
determine, not just the first.

Rules are split into two tiers by what they need in order to be checked.

### E.1 Static rules (`S`) — decidable from the document alone

| Rule | Requirement |
|------|-------------|
| **S1** | The document is a single JSON object with the fields of Section B.1; `schema_version`, `units`, `name` and `features` are present. |
| **S2** | `features` is an array with at least one element, and every element is an object with a string `id` and a string `type`. |
| **S3** | No unknown fields anywhere in the document, and `type` is one of `box`, `cylinder`, `through_hole`, `subtract`, `fillet`, `chamfer`, `union`. `union` is defined from `1.1.0`: in a document whose `schema_version` is earlier, it is an unknown type (Section F). `cylinder.top_diameter`, `cylinder.sides` and the `pattern_count`, `pattern_axis`, `pattern_angle` and `pattern_step` of `subtract` and `union` are defined from `1.2.0`: in a document whose `schema_version` is earlier, each is an unknown field. Unknown fields and unknown types are rejected, never ignored. |
| **S4** | `schema_version` is a valid version string and its major version is supported by the validator (Section F). |
| **S5** | `units` is `"mm"`. No other value is accepted or converted in V1. |
| **S6** | Every reference (`target`, each entry of `tools`) names a solid that exists in the solid set at the moment the referencing feature is evaluated — i.e. it was created earlier and has not been consumed by a `subtract` or a `union`. |
| **S7** | Every reference points to a feature that appears strictly earlier in `features`. Forward references and reference cycles are invalid. |
| **S8** | Feature ids are unique within the part and match `^[A-Za-z_][A-Za-z0-9_-]*$`. |
| **S9** | The first feature is constructive (`box` or `cylinder`), and after the last feature the solid set contains exactly one solid. No assemblies, no orphan bodies. |
| **S10** | `box.size.x`, `box.size.y`, `box.size.z` are each `> 0`. |
| **S11** | `cylinder.diameter > 0` and `cylinder.height > 0`. |
| **S12** | Every `axis` on a `cylinder` or `through_hole` is one of `"+X"`, `"-X"`, `"+Y"`, `"-Y"`, `"+Z"`, `"-Z"`. |
| **S13** | `through_hole.diameter > 0`. |
| **S14** | `subtract.tools` is a non-empty array. |
| **S15** | `subtract.target` does not appear in `subtract.tools`, and `tools` contains no duplicate ids. |
| **S16** | `fillet.radius > 0`. |
| **S17** | `chamfer.distance > 0`. |
| **S18** | An edge selector has `select` of `"all"` or `"axis_parallel"`; `axis` is present with value `"X"`, `"Y"` or `"Z"` exactly when `select` is `"axis_parallel"`. |
| **S19** | Every position and size is an object with exactly the keys `x`, `y`, `z`, each a finite JSON number. `NaN`, infinities and array forms are invalid. |
| **S20** | Every length value is a finite number. Sizes, diameters, radii and chamfer distances are positive per the rules above; position components may be negative or zero. `top_diameter` may be zero (**S23**) and `pattern_step` negative or zero (**S27**). |
| **S21** | `union.tools` is a non-empty array. |
| **S22** | `union.target` does not appear in `union.tools`, and `tools` contains no duplicate ids. |
| **S23** | `cylinder.top_diameter` is a finite number `>= 0`; when `cylinder.sides` is not `0` it is `> 0` (no pointed pyramid). |
| **S24** | `cylinder.sides` is an integer equal to `0` or from `3` to `12`. |
| **S25** | `pattern_count` (on `subtract` or `union`) is an integer from `1` to `64`. |
| **S26** | `pattern_axis` is one of `"X"`, `"Y"`, `"Z"`. |
| **S27** | `pattern_angle` and `pattern_step` are finite numbers, and `pattern_angle` is from `-360` to `360` inclusive. |
| **S28** | When `pattern_count > 1`, no two copies coincide: `pattern_angle` and `pattern_step` are not both `0`, and when `pattern_step` is `0`, no multiple `j · pattern_angle` with `1 <= j < pattern_count` is a whole number of turns (a multiple of `360`). Coinciding copies are a misread request, never a silent no-op. |

S21 and S22 were added in `1.1.0` with `union`. They state for `union` what S14
and S15 state for `subtract`, under their own codes so that a reported rule
always names the feature type it is about. No rule from `1.0.0` was renumbered
or changed in meaning.

S23–S28 were added in `1.2.0` with the taper, polygon and pattern parameters.
The pattern rules S25–S28 are shared by `subtract` and `union`, because the
pattern means the same thing on both. An **integer** is a JSON number with no
fractional part: `6` and `6.0` are the same integer, `6.5` is not one. Each of
S23–S27 covers its parameter's type and finiteness as well as its range, so
S20 is not reported for `top_diameter` or `pattern_step`. No earlier rule was
renumbered or changed in meaning.

### E.2 Geometric rules (`E`) — require evaluation by the engine

These cannot be decided by inspecting the document; the engine reports them
while building geometry. They are part of the contract so that the failure modes
are named and stable rather than surfacing as kernel crashes.

| Rule | Requirement |
|------|-------------|
| **E1** | A `through_hole` centreline actually intersects its target solid. A hole that misses the material is an error, not a no-op. |
| **E2** | A `subtract` leaves a non-empty solid. Removing all material is an error. |
| **E3** | Every modifier leaves a single connected solid. A cut that splits the body into two pieces is an error in V1, which has no multi-body parts. So is a `union` whose target and tools do not form one body: operands that are apart, or that touch only along an edge or at a point, are not joined. |
| **E4** | An edge selector matches at least one edge of its target. A fillet or chamfer that affects nothing is an error, because it indicates a misread request. |
| **E5** | A `fillet.radius` / `chamfer.distance` is admissible for every matched edge — it fits within the adjoining faces and does not consume neighbouring geometry. |

### E.3 Error reporting

An error identifies the rule, the offending feature by `id` (or the document
root), and the offending field path. Validation failure means **no geometry is
produced**: there is no partial or best-effort output.

---

## F. Versioning

The part document carries a required `schema_version` field. This document
defines version **`1.2.0`**.

| Version | What it added |
|---------|---------------|
| `1.0.0` | The initial contract: `box`, `cylinder`, `through_hole`, `subtract`, `fillet`, `chamfer`; rules S1–S20 and E1–E5. |
| `1.1.0` | The feature type `union` (Section C.8) and its rules S21 and S22. A MINOR change: no field, default, rule or meaning from `1.0.0` was altered. |
| `1.1.1` | Edge selectors match **sharp** edges only (Section C.7): seams and smooth (tangent) edges are excluded. A PATCH: every selection that included a seam or a smooth edge failed under rule E5, because the engine refuses to blend them and never skips a selected edge, so no document that built before builds differently. Documents that failed for that reason now build. |
| `1.2.0` | `cylinder` `top_diameter` and `sides` (Section C.2); the `subtract`/`union` pattern parameters `pattern_count`, `pattern_axis`, `pattern_angle`, `pattern_step` (Sections C.4, C.8); rules S23–S28. A MINOR change: every new parameter is optional, and every default preserves the `1.1.1` meaning. |

- The value is a `MAJOR.MINOR.PATCH` string.
- **MAJOR** changes when existing documents would no longer mean the same thing:
  a removed or renamed field, a changed default, a changed anchor point, a
  changed unit rule.
- **MINOR** changes when the contract gains something backward-compatible, such
  as a new feature type or a new optional parameter with a default that
  preserves current behavior.
- **PATCH** changes for wording, clarification, and fixes that do not alter how
  any valid document is interpreted.

Consumer rules:

- A consumer MUST reject a document whose MAJOR version it does not implement
  (rule **S4**). It MUST NOT attempt a partial or lenient read.
- A consumer MAY accept a document with a MINOR version higher than its own only
  if it still rejects every field and feature type it does not recognise
  (rule **S3**) — in practice this means it will reject documents that use the
  newer features, which is the intended outcome.
- A document is validated against the version it **declares**. A feature type
  or a parameter introduced in a later MINOR version is unknown to a document
  that declares an earlier one, and is rejected under rule **S3** by every
  consumer — including one that implements the later version. A document
  declaring `1.0.0` that uses `union` is therefore invalid; the correction is
  to declare `1.1.0`. Likewise a `1.1.1` document with `"sides": 6` is invalid;
  the correction is to declare `1.2.0`. This keeps a document's validity a
  property of the document, not of whichever consumer reads it.
- A consumer never rewrites `schema_version`. A `1.0.0` document stays a `1.0.0`
  document: it is not upgraded on read, so its canonical form and its content
  hash are unchanged by the existence of `1.1.0` or `1.2.0`. In particular the
  defaults of parameters a document's version does not define are never written
  into it.
- Documents never omit `schema_version` and there is no implied default. A
  missing version is an error, not "assume V1".

This is what allows V1 designs to keep meaning exactly what they meant, even
after later versions add sketches, patterns, or richer edge selection.

---

## G. Design principle

> **The LLM produces a structured CAD specification. The deterministic CAD
> engine is responsible for validating that specification and producing
> geometry. The LLM must not be trusted as the geometry kernel.**

What this rule requires in practice:

- The language model's only output is a specification document conforming to
  this contract. It never emits geometry, mesh data, kernel calls, or code.
- The engine re-validates every document it receives, from scratch, against
  Section E. It never trusts that the producer already validated it, and it
  never repairs a document to make it buildable.
- The engine is deterministic: the same specification yields the same geometry
  every time. No sampling, no heuristics, no model in the geometry path.
- Ambiguity is resolved before this boundary, not after it. If a request cannot
  be expressed in this contract, the correct outcome is an explicit failure or a
  question back to the user — never a plausible-looking guess encoded as
  geometry.
- Every part is reproducible and reviewable from its specification alone,
  because the specification is the complete input to the engine.
