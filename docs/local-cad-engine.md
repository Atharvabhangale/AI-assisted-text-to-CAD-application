# Local CAD engine

Status: **Stage 4 — a single box, built as a real B-rep solid.**

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

Exactly one feature, of type **`box`** or **`cylinder`**, in **millimetres**.

Everything else is rejected with `UnsupportedGeometryError`: through-holes,
boolean subtraction, fillets, chamfers, multi-feature histories, empty
histories, and any other unit system. Nothing is partially built and no feature
is silently skipped. The engine does not repair or reinterpret a part.

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
times and assert the measurements collapse to a single value.

Determinism here means *measured values*, not object identity — each call
returns a fresh result object, and nothing relies on memory identity being
stable. Kernel-derived measurements are compared with an explicit tolerance of
**1e-6 mm**, used consistently; exact floating-point equality is never used for
a value that came from the kernel.

## Future stages

Explicitly **not** part of this stage:

- **Export is a future stage.** No STEP, IGES, STL or any other exporter.
- **Browser rendering / visualisation is a future stage.** Nothing renders.
- **The remaining V1 features are unimplemented**: cylinder, through-hole,
  boolean subtraction, fillet, chamfer.
- **FeatureScript/Onshape remains a separate backend path.** This engine does
  not generate FeatureScript, does not talk to Onshape, and does not make the
  Onshape path unnecessary — verifying the FeatureScript backend still requires
  Onshape.
- No frontend, no LLM, no MCP, no HTTP API.

## Known limitations

- One constructive feature only — a box or a cylinder. The engine has no
  vocabulary for feature history, because nothing beyond a single constructive
  feature is supported yet.
- The engine trusts the validator. A hand-built `Part` that bypasses validation
  with, say, a zero extent would reach the kernel and fail there rather than
  being caught politely — by design, since re-validating would duplicate
  Stage 2 and repairing is forbidden.
- Agreement between the two backends is untested, and cannot be tested until
  the Onshape path runs. Both are built from the same specification section,
  but that is an argument, not evidence.
