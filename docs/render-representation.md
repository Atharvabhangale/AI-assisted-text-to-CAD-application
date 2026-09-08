# Neutral render representation

Status: **Stage 8 — visualization geometry as plain, serializable Python data.**

## Purpose

A browser viewer or HTTP API cannot be handed a `cadquery.Shape`. Kernel
objects are not serializable, they drag a heavy native dependency behind them,
and they are an implementation detail of one execution backend. This module is
the seam where kernel geometry becomes an application-level data contract:

```
CAD specification → local CAD engine → B-rep → render representation → future browser / API
```

Everything a `RenderModel` exposes is a dataclass, tuple, float, int or string.
No `cadquery.Shape`, no `TopoDS_*`, no `Workplane`, no OpenCascade handle
crosses the boundary — a test walks the model recursively and asserts every
value is one of those neutral types.

## Two separate contracts

`docs/cad-specification.md` describes **engineering** geometry: features,
parameters, design intent. This document describes **visualization** geometry:
triangles for a renderer. They are deliberately separate contracts, with
separate version numbers, and neither knows about the other.

- No mesh, triangle, tessellation, render or normal concept appears in the
  specification — a test asserts that against `model.py`.
- The render model contains no feature history, no parameters and no design
  intent.

The render contract carries its own `format_version` (`1.0.0`), independent of
the specification's `schema_version`.

## B-rep versus render representation

| | B-rep (`LocalCadResult`) | Render model (`RenderModel`) |
|---|---|---|
| What it is | The authoritative solid | A view of it, for display |
| Geometry | Exact surfaces | Triangles approximating them |
| Topology | Faces, edges, vertices, shells | Discarded |
| Identity | Feature id, part name | Part name and feature id kept as labels only |
| Volume | Exact (60000 mm³) | Not reported |
| Serializable | No | Yes, to JSON |

The render model is **derived by tessellating the B-rep**. It is never built
from the specification directly, from a bounding box, from a hand-made mesh, or
from an STL file. The B-rep stays the authority.

## Input / output boundary

```python
from cad_core.local_cad import build_part
from cad_core.render_model import build_render_model

result = build_part(part)                 # part: cad_core.model.Part
model  = build_render_model(result)       # -> RenderModel
data   = model.to_dict()                  # -> JSON-compatible dict
json.dumps(data)                          # no custom encoder needed
```

```python
build_render_model(result, *, tolerance=0.01, angular_tolerance=0.1) -> RenderModel
```

Accepts **only** a `LocalCadResult`. A raw specification dictionary, loose mesh
data, a bare kernel shape or a `ValidationResult` all raise `TypeError`, so a
render model cannot be conjured from arbitrary data through the primary API.
Non-positive tolerances raise `ValueError`. It does not mutate the source — a
test confirms the B-rep is still a valid one-solid `Solid` with six faces and
volume 60000 mm³ afterwards.

Rendering adds no route around the engine's supported subset: a cylinder or a
multi-feature part is still rejected by `build_part` before a render model
could exist.

## Data fields

| Field | Type | Meaning |
|---|---|---|
| `format_version` | `str` | Version of this render contract (`"1.0.0"`) |
| `part_name` | `str` | Label from the source part |
| `feature_id` | `str` | Label from the source feature |
| `units` | `str` | `"mm"` |
| `coordinate_system` | `str` | `"right_handed_z_up"` |
| `winding` | `str` | `"counter_clockwise_outward"` |
| `normal_binding` | `str` | `"per_vertex"` |
| `vertices` | `tuple[tuple[float, float, float], ...]` | Positions |
| `triangles` | `tuple[tuple[int, int, int], ...]` | Indices into `vertices` |
| `normals` | `tuple[tuple[float, float, float], ...]` | One per vertex, parallel to `vertices` |
| `bounds` | `RenderBounds` | `minimum`, `maximum`, `size` |
| `tessellation` | `TessellationSettings` | `linear_deflection_mm`, `angular_deflection_rad` |

The conventions (`coordinate_system`, `winding`, `normal_binding`) are fields
rather than documentation-only facts, so a consumer can read them off the
payload instead of trusting a spec it may not have.

### Serialization structure

`to_dict()` returns exactly those twelve keys, with tuples flattened to lists:

```json
{
  "format_version": "1.0.0",
  "part_name": "plate-100x60x10",
  "feature_id": "plate",
  "units": "mm",
  "coordinate_system": "right_handed_z_up",
  "winding": "counter_clockwise_outward",
  "normal_binding": "per_vertex",
  "vertices": [[10.0, 20.0, 30.0], ...],
  "triangles": [[0, 1, 2], ...],
  "normals": [[-1.0, 0.0, 0.0], ...],
  "bounds": {"minimum": [...], "maximum": [...], "size": [...]},
  "tessellation": {"linear_deflection_mm": 0.01, "angular_deflection_rad": 0.1}
}
```

Vertices are emitted as triples rather than a flat array. A viewer that wants
WebGL buffers flattens them in one line; triples keep the payload
self-describing and verifiable.

## Coordinate system and units

Coordinates are in the specification's part coordinate system (Section A.1):
right-handed, +Z up, absolute. Units are millimetres — not assumed, but read
from the fact that `local_cad.build_part` accepts millimetre parts only, so any
`LocalCadResult` that exists is in millimetres. A test pins
`RENDER_UNITS == local_cad.SUPPORTED_UNITS[0]`.

## Tessellation settings

| Setting | Value |
|---|---|
| linear deflection | **0.01 mm** |
| angular deflection | **0.1 rad** |

The same values the STL exporter uses, so both outputs describe the same
approximation. Both are recorded in the model itself.

**One difference was found by reading the source, and is documented rather than
papered over.** `Shape.mesh` — which `tessellate` calls — passes
`relative=True` to `BRepMesh_IncrementalMesh`, whereas `cad_core.stl_export`
passes `relative=False`. For the current all-planar geometry this is immaterial
(both produce 12 triangles), and `tessellate` is used anyway because it
implements the per-face winding correction described below, which is not worth
reimplementing. It will need revisiting when curved geometry exists.

A related subtlety: `Shape.mesh` reuses an existing triangulation if one is
already attached at the requested tolerance, so exporting STL before building a
render model may cause the render model to inherit that mesh. Immaterial for
planar geometry; recorded so it is not a surprise later.

## Tessellation API used

`cadquery.Shape.tessellate(tolerance, angularTolerance)`, which returns
`(list[Vector], list[tuple[int, int, int]])`. It meshes via
`BRepMesh_IncrementalMesh`, then walks `Shape.Faces()`, reading each face's
`Poly_Triangulation` through `BRep_Tool.Triangulation_s` and applying the
face's location transform.

## Vertices, indexing, normals and winding

All measured on the reference box, not assumed.

**Vertices are not shared across faces.** CadQuery tessellates face by face and
concatenates each face's nodes with an offset, so the box yields **24 vertices,
not 8** — only 8 *distinct positions*, each appearing three times, once per
adjoining face. This is what a renderer wants: sharing them would average
normals across an edge and round off a crease that should be sharp.

**Triangles are indexed** into that vertex array. Every index is validated at
construction; an out-of-range index raises `RenderModelError`.

**Normals are per-vertex and computed, not kernel-supplied.** OpenCascade
reports `Poly_Triangulation.HasNormals() == False` after meshing, so there is
nothing to read. Each vertex normal is the normalised sum of the geometric
normals of the triangles referencing it, where each triangle's contribution is
the unnormalised cross product — whose magnitude is twice the triangle's area,
so the sum is area-weighted and a degenerate triangle contributes nothing.
Within a planar face this is exactly the face normal; within a curved face it
smooths across the tessellation; across faces it stays sharp, because vertices
are not shared. A vertex referenced only by degenerate triangles would get
`(0, 0, 0)` — reported rather than replaced with a fabricated direction.

**Winding is counter-clockwise seen from outside**, so the right-hand rule
gives an outward normal. This holds because CadQuery reverses the index order
for faces whose `TopAbs_Orientation` is `REVERSED`, and the box's faces are a
mix of `FORWARD` and `REVERSED`. Verified by checking all 12 triangles of the
reference box: the cross-product normal points away from the box centre in
every case.

**Face and feature identity are discarded.** There is no per-triangle face id,
no CAD topology, no design intent. The render model claims nothing about
preserving topology.

### Internal surfaces: hole-wall normals point *inward*

Stage 10 added `through_hole`, which puts the first **internal** surface into
the render model. Measured on the drilled plate — 100 × 60 × 10 mm at the
origin, one Ø20 `+Z` hole at (20, 20), giving **530 vertices / 520 triangles**:

| Vertex group | Count | Normal |
|---|---|---|
| on the cylindrical hole wall | 254 | dot with the *outward radial* direction between −1.0 and −0.99969 — i.e. pointing **at the hole axis** |
| on the two circular rings, belonging to the top/bottom faces | 252 | ±Z, radial dot exactly 0.0 |
| on the top face (`z = 10`) | 130 | exactly `(0, 0, 1)` |
| on the bottom face (`z = 0`) | 130 | exactly `(0, 0, −1)` |
| on each of the four sides | 4 each | exactly the outward axis direction |

The inward hole-wall normals are **correct, not a bug**: a surface normal points
out of the material, and for a hole that direction is into the void. "Outward"
in the `winding` field means *out of the solid*, which on an internal surface
means towards the axis. A renderer that assumed "away from the part centre"
would shade the hole wrong.

That also breaks the cheap winding check used for the box and the cylinder. A
drilled plate is not star-shaped, so "does the triangle normal point away from
the centroid?" is no longer a valid test. The tests therefore verify winding
with the kernel instead: step a short distance along each triangle's normal
from its centroid and classify that point with
`BRepClass3d_SolidClassifier` — it must land **outside** the solid. That works
for the hole wall and the outer faces alike, with no assumption about shape.

The 530 vertices for 260 distinct mesh nodes are the usual per-face
duplication (`stl_export` reports 260 nodes for the same geometry, because the
STL reader merges coincident coordinates and this model deliberately does not).

The same holds for a cavity produced by Stage 11's general `subtract` rather
than by a `through_hole`: measured, the two routes give the same 530 vertices
and 520 triangles, and — for the same part name and target id — a
byte-identical `json.dumps(..., sort_keys=True)` payload. The render model is
built from `LocalCadResult`, so it never sees which feature made the cavity.

### Convex blends: fillet normals point *outward*

Stage 13 added `fillet`, giving the mirror image of the hole wall above.
Measured on a 100 × 60 × 10 plate with radius 2 on its four vertical edges —
**544 vertices / 524 triangles**:

| Vertex group | Normal |
|---|---|
| on a corner blend's cylindrical surface | dot with the *outward radial* direction from the blend axis between 0.99970 and 1.0 — pointing **away** from the axis |
| on the top and bottom faces | exactly `(0, 0, ±1)` |
| on the four side faces | exactly the outward axis direction |

Both cases follow the same rule — a normal points out of the material — and
they come out opposite because a hole wall is concave and a fillet is convex.
Nothing in the model distinguishes them, so a consumer that hard-codes either
"towards the axis" or "away from the axis" will be wrong on some parts.

The blend is genuinely curved in the mesh: the blend-face vertices carry more
than four distinct normals rather than one flat facet normal per corner, which
a test asserts.

### Planar bevels: a finite set of exact normals

Stage 14 added `chamfer`, which is the simplest case the render model has —
and a useful control for the two curved ones above. Measured on a
100 × 60 × 10 plate bevelled 2 mm on its four vertical edges — **48 vertices /
28 triangles**:

- exactly **ten distinct vertex normals**: the six original face directions
  plus four bevel directions at (±1, ±1, 0)/√2. Nothing is curved, so there is
  nothing for the area-weighted averaging to smooth, and every normal is one
  of ten exact values;
- the rendered normals match the B-rep's own plane axes **up to sign** — a
  plane's axis follows the surface parameterisation, which is reversed for a
  `TopAbs_REVERSED` face, while a render normal always points out of the
  material. The axis is the shared fact, the sign is not;
- winding verified for every triangle with the kernel classifier, as for the
  curved cases.

So the three modifiers give three different normal patterns, all from the same
rule: a hole wall's normals point at its axis (concave), a fillet's point away
from its axis (convex), and a chamfer's are a fixed bisector direction per
bevel. A consumer should not assume any of them.

## Bounds

`bounds` is computed **from the render vertices**, not copied from the B-rep —
a tessellation can only lie on or inside the true surface, so a render model's
bounds are a property of the triangles it actually contains. The B-rep's own
measurement stays separately available from `LocalCadResult.bounding_box()`.

For the reference box the two agree exactly, because the geometry is planar:

| | minimum | maximum | dimensions |
|---|---|---|---|
| B-rep | (10, 20, 30) | (110, 80, 40) | (100, 60, 10) |
| render | (10.0, 20.0, 30.0) | (110.0, 80.0, 40.0) | (100.0, 60.0, 10.0) |
| difference | 0.0 | 0.0 | — |

A test compares them and asserts the difference is within the documented
tolerance of **0.010001 mm** — the linear deflection plus 1e-6 mm of kernel
float noise. Normals are checked to unit length within **1e-9**.

For a **drilled** part the render bounds are exactly the envelope the V1
semantics predict — (0, 0, 0) → (100, 60, 10) — because a through-hole removes
interior material and does not touch the envelope. Two consequences:

- Bounds are **no evidence at all** that a hole is present. A plate with the
  hole and a plate without it measure identically; only the triangle count and
  the B-rep volume tell them apart.
- They match the B-rep's bounding box **as measured before meshing**. After
  meshing they do not — see below, where the drilled part makes that gap much
  larger than the box did.

### A measured subtlety: the B-rep query shifts after meshing

Querying the B-rep's bounding box *after* tessellation returns bounds inflated
outward. For the reference box the inflation is about **1e-7 mm**:

| | minimum x |
|---|---|
| B-rep, before tessellation | `10.0` |
| B-rep, after tessellation | `9.9999999` |
| render vertices | `10.0` |

OpenCascade's `BoundingBox()` bounds an attached triangulation with a gap
rather than the analytic surface, so once a mesh exists the query loosens.
Volume and solidity are unaffected, and 1e-7 mm is well inside every tolerance
this project uses.

**Stage 10 found the gap is not always negligible.** For the drilled plate the
post-mesh query loosens along the drilled axis by **3.108e-3 mm** — four orders
of magnitude more than the box, and the same magnitude as the chord sag of the
tessellated hole wall:

| | minimum z | maximum z |
|---|---|---|
| B-rep, before tessellation | `0.0` | `10.0` |
| B-rep, after tessellation | `-0.0031082799918424` | `10.0031082799918` |
| render vertices | `0.0` | `10.0` |

That is still comfortably inside the 0.010001 mm mesh tolerance, but it is well
outside the 1e-6 mm tolerance used for B-rep lengths — so a test that measured
the B-rep *after* building a render model and compared with 1e-6 mm would fail.
A test now pins the direction (outward only) and the bound (no more than the
mesh tolerance) rather than a specific number, and confirms the render bounds
stay exact either way.

Two things follow. First, this is **not** specific to the render model — STL
export meshes the shape and has exactly the same effect, so the behaviour
predates this stage. Second, it is a concrete vindication of deriving render
bounds from the render vertices: those stay exact, while the post-mesh B-rep
query does not. A test records the behaviour so it stays monitored rather than
lurking.

## Determinism findings

Measured, not assumed:

- **Vertex and triangle ordering is stable.** Four repeated `tessellate` calls
  produced identical sequences.
- **The serialized model is byte-identical across repeated builds.** Four
  builds of the same `LocalCadResult` produced one `json.dumps(..., sort_keys=True)`
  string.

No canonical re-sorting was applied. Ordering comes from CadQuery walking
`Shape.Faces()` in a stable order and OpenCascade's mesher being deterministic
for this geometry — sorting floating-point vertices would have destroyed the
index correspondence with triangles for no benefit. Recorded as an empirical
result for this backend and this geometry, not as a guarantee.

## Limitations

- **Curved geometry is now exercised** (Stage 9 added the cylinder). Measured
  for a cylinder of diameter 20, height 50: **566 vertices / 560 triangles**,
  bounds within 2.5e-3 mm of the true surface, all normals unit-length, and
  curved-side normals that genuinely vary — 284 sampled side vertices produced
  **249 distinct normals**, each aligned with the exact radial direction to
  better than 0.99975. End-cap normals come out exactly ±1.0 in Z. This is the
  first geometry for which the smooth-normal path does anything at all.

- **The `relative` difference with STL is now material.** Stage 8 recorded that
  `Shape.mesh` (used by `tessellate`) passes `relative=True` while
  `stl_export` passes `relative=False`, and predicted it would matter once
  curves existed. It does: for the same cylinder the render model has 560
  triangles and the STL 500, with slightly different mesh bounds. Both stay
  well inside tolerance of the true surface, and each is self-consistent and
  deterministic — but they are **not the same mesh**. Anything that needs the
  render model and the STL to agree triangle-for-triangle would have to unify
  those parameters first. Stage 10 sharpened this: for the **drilled plate**
  both paths happen to produce **520 triangles**. Equal counts for one solid are
  a coincidence of that geometry, not agreement between the two settings — the
  cylinder case above shows they diverge — so the divergence stands as a
  limitation regardless.
- **Internal surfaces need care from the consumer.** Hole walls are present and
  correctly oriented, but their normals point at the hole axis (see above).
  Nothing in the model marks a triangle as "internal", so a viewer cannot
  distinguish a hole wall from an outer face except geometrically.
- **No topology, no feature identity per triangle.** By design.
- **No volume, no mass properties, no material, no colour.** A viewer that
  needs them should ask the B-rep, not the render model.
- **No level-of-detail, no compression, no binary encoding.** JSON with float
  triples is fine for a 24-vertex box and will not be fine for a large
  assembly; a flat-array or binary form is a future concern.
- **Not consumed by anything yet.** No viewer exists. The contract is designed
  against what WebGL needs, but nothing has yet proven it renders correctly —
  that is the next stage's job, not a claim this one can make.
