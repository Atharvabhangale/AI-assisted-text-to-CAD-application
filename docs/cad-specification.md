# CAD Specification Contract — V1

Status: **V1 (initial)**
Schema version described by this document: **`1.0.0`**

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

The following are **explicitly out of scope for V1** and MUST be rejected by a
V1 validator rather than partially interpreted: assemblies, sketches, splines,
lofts, sweeps, revolves, threads, patterns, GD&T, tolerances, materials,
simulation, manufacturing rules, scripting or expressions of any kind, and any
constraint not defined in this document.

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
system per part. V1 has **no local coordinate systems, no datums, no
transforms, and no rotations** — every position in the document is an absolute
position in the PCS.

### A.3 Units

The unit system is carried **inside the document**; it is never implied by
context, file name, or consumer default.

- The part declares its unit system in the required `units` field.
- **V1 accepts the single value `"mm"`** (millimetres). Other unit systems are
  reserved for a later schema version; a V1 validator MUST reject any other
  value rather than convert it (rule **S5**).
- Every length in the document — sizes, diameters, positions, radii, chamfer
  distances — is a number expressed in the declared unit.
- V1 contains no angular parameters, so no angular unit is defined.

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
vectors are not supported in V1.

---

## B. Part structure

### B.1 The part document

The root of the document is a single part:

```json
{
  "schema_version": "1.0.0",
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

**Modifier features** (`through_hole`, `subtract`, `fillet`, `chamfer`) act on
an existing solid named by their `target` field. A modifier replaces its target
**in place**: the resulting solid keeps the **target's** id and its position in
the solid set. The modifier's own `id` is a label used for traceability and
error reporting; it never names a solid.

Consequently a plate with four holes keeps the id `plate` throughout, and each
hole's `target` is `plate`.

`subtract` additionally **consumes** its tool solids: each id listed in `tools`
is removed from the solid set.

After the last feature is evaluated, the solid set MUST contain exactly one
solid (rule **S9**). That solid is the part's geometry. A leftover cylinder that
was created but never subtracted is therefore an error, not a second body.

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
[position.z, position.z + size.z]`. Box edges are always parallel to the PCS
axes; there is no rotation in V1.

**Validation.** `size.x`, `size.y`, `size.z` MUST each be `> 0` (rule **S10**).
`position` components are unconstrained.

### C.2 `cylinder`

A right circular cylinder of finite height. Constructive. In V1 a cylinder is
used either as the part's base solid or as a tool for `subtract`.

| Parameter | Type | Required | Default | Unit |
|-----------|------|----------|---------|------|
| `id` | string | yes | — | — |
| `type` | `"cylinder"` | yes | — | — |
| `diameter` | length | yes | — | length |
| `height` | length | yes | — | length |
| `position` | position | no | `{"x":0,"y":0,"z":0}` | length |
| `axis` | axis | no | `"+Z"` | — |

**Behavior.** `position` is the **centre of the base circle**. The cylinder
extends `height` from that circle in the direction given by `axis`. Its radius
is `diameter / 2`.

**Validation.** `diameter > 0` and `height > 0` (rule **S11**). `axis` MUST be
one of the six signed principal directions (rule **S12**).

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

**Behavior.** Each solid listed in `tools` is removed from the target, in list
order. The result replaces the target in place (Section B.4) and every tool
solid is consumed — removed from the solid set and unavailable to later
features.

Only subtraction is defined in V1. Union and intersection are not part of this
contract.

**Validation.** `tools` MUST be a non-empty array (rule **S14**). Every id in
`target` and `tools` MUST resolve to a solid in the solid set (rule **S6**).
`target` MUST NOT appear in `tools`, and `tools` MUST NOT contain duplicates
(rule **S15**). The result MUST be a single non-empty connected solid (rules
**E2**, **E3**).

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
why no angle parameter and no unit for angles exist.

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

Matches every edge of the target solid.

```json
{ "select": "axis_parallel", "axis": "Z" }
```

Matches every **straight** edge of the target solid that is parallel to the
given axis. Circular and non-linear edges never match this selector, so
filleting the vertical corners of a drilled plate does not touch the hole rims.

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `select` | `"all"` \| `"axis_parallel"` | yes | — |
| `axis` | `"X"` \| `"Y"` \| `"Z"` | only for `axis_parallel` | **unsigned**: parallelism has no direction |

**Validation.** `axis` MUST be present for `axis_parallel` and absent for `all`
(rule **S18**). The unsigned axis letters here are intentionally different from
the signed axis values used by `cylinder` and `through_hole`.

---

## D. Example specification

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
| **S3** | No unknown fields anywhere in the document, and `type` is one of `box`, `cylinder`, `through_hole`, `subtract`, `fillet`, `chamfer`. Unknown fields and unknown types are rejected, never ignored. |
| **S4** | `schema_version` is a valid version string and its major version is supported by the validator (Section F). |
| **S5** | `units` is `"mm"`. No other value is accepted or converted in V1. |
| **S6** | Every reference (`target`, each entry of `tools`) names a solid that exists in the solid set at the moment the referencing feature is evaluated — i.e. it was created earlier and has not been consumed by a `subtract`. |
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
| **S20** | Every length value is a finite number. Sizes, diameters, radii and chamfer distances are positive per the rules above; position components may be negative or zero. |

### E.2 Geometric rules (`E`) — require evaluation by the engine

These cannot be decided by inspecting the document; the engine reports them
while building geometry. They are part of the contract so that the failure modes
are named and stable rather than surfacing as kernel crashes.

| Rule | Requirement |
|------|-------------|
| **E1** | A `through_hole` centreline actually intersects its target solid. A hole that misses the material is an error, not a no-op. |
| **E2** | A `subtract` leaves a non-empty solid. Removing all material is an error. |
| **E3** | Every modifier leaves a single connected solid. A cut that splits the body into two pieces is an error in V1, which has no multi-body parts. |
| **E4** | An edge selector matches at least one edge of its target. A fillet or chamfer that affects nothing is an error, because it indicates a misread request. |
| **E5** | A `fillet.radius` / `chamfer.distance` is admissible for every matched edge — it fits within the adjoining faces and does not consume neighbouring geometry. |

### E.3 Error reporting

An error identifies the rule, the offending feature by `id` (or the document
root), and the offending field path. Validation failure means **no geometry is
produced**: there is no partial or best-effort output.

---

## F. Versioning

The part document carries a required `schema_version` field. This document
defines version **`1.0.0`**.

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
