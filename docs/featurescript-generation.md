# FeatureScript generation

Status: **Stage 3A — a single box, source text only.**

## Why FeatureScript

The CAD specification (`docs/cad-specification.md`) is deliberately neutral: it
describes a part without naming any CAD system. At some point that neutral
description has to become real geometry in a real modelling kernel, and the
first target chosen for that is **Onshape FeatureScript**.

Onshape is browser-based and its modelling operations are scriptable in
FeatureScript, so a part can be produced from generated source text without
installing a CAD kernel locally. That makes FeatureScript a good first target
for proving the translation step: the specification is the input, FeatureScript
source is the output, and a human can see the result in Onshape.

This stage exists to prove exactly one claim — that the neutral specification
carries enough information to be translated into a coherent FeatureScript
document — for the simplest possible part.

## The pipeline

```
Validated CAD specification (a typed Part)
        ↓
FeatureScript generator          ← this stage
        ↓
FeatureScript source text
        ↓
Human pastes it into an Onshape Feature Studio
```

## Currently supported subset

Exactly one part shape is supported:

- **one** feature,
- of type **`box`**,
- with `units` of **`"mm"`**,
- with a feature `id` matching the specification's id pattern.

Nothing else. A cylinder, a through-hole, a boolean subtraction, a fillet, a
chamfer, a multi-feature history, an empty history, or any other unit system is
**rejected explicitly** with `UnsupportedPartError`. The generator never
repairs a part, never reinterprets one, and never silently skips a feature it
cannot express.

### Box semantics

Section C.1 of the specification defines `position` as the box's **minimum
corner**, with the box occupying `[position, position + size]` on each axis,
axis-aligned and unrotated.

The generated source uses the standard-library primitive `fCuboid`, which
creates a rectangular prism between **two opposite corners**. That choice is
deliberate: because the operation takes corners rather than a centre and
extents, the absolute placement is expressed directly and no centring
convention has to be assumed anywhere in the translation.

### Coordinate mapping

Stated per component, this is the whole of the translation:

```
corner1 = ( position.x,            position.y,            position.z           )
corner2 = ( position.x + size.x,   position.y + size.y,   position.z + size.z  )
```

So a 100 x 60 x 10 mm box at position (10, 20, 30) becomes
`corner1 = (10, 20, 30)` and `corner2 = (110, 80, 40)`.

Nothing is centred, offset, scaled or rotated on the way through. `fCuboid`
places the prism between the two absolute corners it is given, so the generated
geometry represents exactly the box the V1 specification defines — no implicit
centring is relied on anywhere.

Lengths are emitted with an explicit FeatureScript unit constant
(`* millimeter`), so the unit system travels with the geometry rather than being
implied. The specification's `units` field is the source of that constant; V1
declares `"mm"`.

## Generated structure

The output is one complete FeatureScript document, in this order:

| Part | Content |
|------|---------|
| 1 | Version declaration — `FeatureScript 2960;` on line 1 |
| 2 | Standard library import — `import(path : "onshape/std/geometry.fs", version : "2960.0");` on line 2, which is what reaches `fCuboid` |
| 3 | A `//` comment header naming the specification, part, feature id and unit system, and stating that Onshape execution is not implemented |
| 4 | `annotation { "Feature Type Name" : ... }` followed by `export const cadCoreBox = defineFeature(function(context is Context, id is Id, definition is map)` |
| 5 | An empty `precondition` block — the feature takes no user-facing parameters, because the geometry is fully determined by the specification |
| 6 | A body containing exactly one call: `fCuboid(context, id + "box", { "corner1" : ..., "corner2" : ... })` |
| 7 | Close with `});` — the one-argument `defineFeature` form |

To use it, a human pastes the document into an Onshape Feature Studio, commits
it, and inserts the resulting custom feature in a Part Studio. `fCuboid` creates
the solid body directly, so no further operation has to be added by hand.

## Input / output boundary

**Input** is the typed `Part` from `cad_core.model` — in practice the `part` of
a successful `cad_core.validate()` result:

```python
from cad_core import generate_featurescript, validate

result = validate(document)
if result.valid:
    source = generate_featurescript(result.part)
```

The public surface is one function, `generate_featurescript(part) -> str`, plus
the `UnsupportedPartError` it raises.

- A raw dictionary, JSON text, or anything else that is not a `Part` raises
  `TypeError`. Unvalidated input cannot reach the generator, because the only
  route to a `Part` is a successful validation.
- The generator **assumes** the `Part` it receives is statically valid. It does
  not re-run rules S1-S20 and will not fix a malformed part.
- A part outside the supported subset raises `UnsupportedPartError`. There is no
  partial output: either a complete document is returned, or an exception is
  raised.

**Output** is a string containing a complete FeatureScript document: the version
declaration, the standard-library import, a header comment identifying the
specification it came from, and one exported custom feature that creates the
box.

## Determinism

The same `Part` always produces byte-identical source text. The generated
document contains no timestamps, no random or generated identifiers, no UUIDs,
no hostnames, and no filesystem paths. Integral lengths are written as integers
(`100`, not `100.0`) and fractional lengths use Python's shortest round-trip
representation, so formatting cannot drift between runs.

Two details are pinned to constants rather than derived, for determinism and
safety:

- The exported feature is always named `cadCoreBox`, and the id component passed
  to `fCuboid` is always the literal `"box"`. The specification's feature id is
  carried in the feature's display name and header comment instead — this
  project has not verified which characters are valid inside an Onshape id
  component, and the specification permits hyphens in ids.
- A part's `name` is free text, so any control characters in it are replaced
  with spaces before it is written into a comment line. Without that, a newline
  in a part name could end the comment and corrupt the generated source.

## What this stage does NOT do

- **Onshape execution is not implemented.** Nothing in this project connects to
  Onshape. There is no API client, no authentication, no document or workspace
  handling, and no upload.
- **No MCP integration.** Not implemented in this stage.
- **The generated source has not been verified against a live Onshape account.**
  It has not been pasted into a Feature Studio, compiled, or rebuilt by Onshape
  by this project. Every construct it uses is verified against the Onshape
  standard library source (see "Verified FeatureScript evidence" below), but
  that is not the same claim as Onshape having accepted it.
- **No CAD kernel, no CadQuery, no OpenCascade, no STEP/IGES/STL export.**
- **The generated source is never executed** by this project — it is only
  produced as text.
- **Stage 3A generates source text only.** Producing that string is the entire
  deliverable of this stage. Nothing downstream of the text exists yet.
- **Live Onshape compilation and execution have NOT been verified by this
  environment.** Onshape's endpoints are unreachable here, so the document has
  never been compiled, inserted, or rebuilt by Onshape.

The tests need no Onshape account, no network connection and no browser.

## Verified FeatureScript evidence

Onshape's own documentation and forum domains are unreachable from the build
environment (blocked by network policy, confirmed against the egress proxy), so
none of this rests on those. Instead every construct emitted by the generator
was read directly from the **Onshape FeatureScript standard library source** at
version **2960** — MIT licensed, "Copyright (c) 2013-Present PTC Inc." — via the
auto-updating mirror
[`javawizard/onshape-std-library-mirror`](https://github.com/javawizard/onshape-std-library-mirror),
which is reachable. The library source is the authoritative definition of the
API it declares.

| Item | Evidence | Source |
|------|----------|--------|
| Version declaration | `FeatureScript 2960;` on line 1 — the declaration takes the bare number | `geometry.fs:1` |
| Standard library import | `export import(path : "onshape/std/common.fs", version : "2960.0");` — an import takes `"<number>.0"`. The module documents itself: "New Feature Studios begin with an import of this module" | `geometry.fs:17`, `geometry.fs:10-11` |
| `fCuboid` reachable via `geometry.fs` | `geometry.fs` re-exports `common.fs`, which re-exports `primitives.fs`, where `fCuboid` is defined — so the single `geometry.fs` import suffices | `geometry.fs:17`, `common.fs:61` |
| Box creation | "Create a simple rectangular prism between two specified corners", `@field corner1 {Vector}`, `@field corner2 {Vector}`, `@eg vector(0, 0, 0) * inch` | `primitives.fs:101-118` |
| Placement is absolute, not centred | The body sketches the rectangle on `XY_PLANE` moved to `min(corner1[2], corner2[2])` and extrudes by `abs(corner2[2] - corner1[2])` — the solid spans exactly the two world-space corners | `primitives.fs:119-141` |
| `defineFeature` structure | `export const fCuboid = defineFeature(function(context is Context, id is Id, definition is map) precondition { ... } { ... });` | `primitives.fs:111` |
| Empty `precondition` is valid | `dummyFeature` is a real exported feature whose precondition block is empty; and a documented "minimal example following good practices" is written `precondition {}` with a body that calls `fCuboid` | `feature.fs` (`dummyFeature`), `context.fs` |
| One-argument `defineFeature` | `export function defineFeature(feature is function) returns function` — the trailing defaults map is optional, so closing with `});` is correct | `feature.fs:120` |
| `millimeter` unit constant | `export const millimeter = 0.001 * meter;` | `units.fs:157` |

`fCuboid`'s own precondition requires `corner1[dim] != corner2[dim]` on all three
axes. Specification rule **S10** (every `size` component `> 0`) guarantees that
for any valid part, so the generator does not need to check it — the two
contracts line up.

A test class pins the generated source to each of these constructs, including
one that asserts every identifier in the generated code (comments excluded)
belongs to the verified set, so no unverified construct can be introduced
without a test failing.

## Known limitations

**Scope limits** (deliberate, enforced by `UnsupportedPartError`):

1. **One box, nothing else.** A cylinder, through-hole, boolean subtraction,
   fillet, chamfer, multi-feature history or empty history is rejected. The
   Section D example plate in `docs/cad-specification.md` is a *valid* V1 part
   but is rejected here, because it carries four through-holes; a test asserts
   that, so no box-only source can be emitted for it.
2. **Millimetres only.** Any other unit system is rejected rather than
   converted.
3. **No user-facing parameters.** The generated feature is a fixed part, not a
   parametric Onshape feature. Changing a dimension means regenerating from the
   specification.
4. **Feature ids are not carried into identifiers.** The exported feature is
   always `cadCoreBox` and the `fCuboid` id component always the literal
   `"box"`; the specification's feature id appears only in the display name and
   header comment. Every id component observed in the standard library source is
   alphanumeric (`"cuboid1"`, `"sketch"`), and specification rule S8 permits
   hyphens, so the valid character set is not established — hence the constant.

**Verification limits** — neither is a syntax guess:

1. **The output has never been run.** It has not been pasted into a Feature
   Studio, compiled, or rebuilt by Onshape. Every construct is verified against
   the library source, but "reads correctly against the source" is not the same
   claim as "Onshape accepted it". Pasting it into a real Feature Studio is the
   one check this project cannot perform.
2. **Exponent notation for extreme magnitudes.** A length whose shortest Python
   representation uses an exponent (for example `1e-05`) would be emitted in
   that form, and the library source gave no example of exponent notation in a
   numeric literal, so it is unverified. Ordinary millimetre dimensions are
   unaffected — a test asserts integral values are written plainly (`100`, not
   `100.0`).

The pinned version is `2960` because that is the version of the library source
the evidence above was read from. It is a single constant,
`FEATURESCRIPT_VERSION`, in
`packages/cad-core/src/cad_core/featurescript.py`; the declaration and the
import are both generated from it.
