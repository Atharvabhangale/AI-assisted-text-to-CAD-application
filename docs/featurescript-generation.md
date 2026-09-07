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

```
corner1 = position
corner2 = position + size
```

Lengths are emitted with an explicit FeatureScript unit constant
(`* millimeter`), so the unit system travels with the geometry rather than being
implied.

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
  by this project. Its correctness rests on the specification's box semantics
  and on Onshape's published documentation for `fCuboid`, not on observed
  behaviour. See "Known uncertainty" below.
- **No CAD kernel, no CadQuery, no OpenCascade, no STEP/IGES/STL export.**
- **The generated source is never executed** by this project — it is only
  produced as text.

The tests need no Onshape account, no network connection and no browser.

## Known uncertainty

Onshape's documentation domains were unreachable from the environment this stage
was built in (blocked by network policy), so the FeatureScript details below
rest on published documentation quoted through web search rather than on a page
read directly, and none of it has been confirmed against a live Onshape session:

1. **The pinned language version.** The generated source declares
   `FeatureScript 1890;` with a matching `version : "1890.0"` import, chosen
   because that version appears in Onshape's own documentation examples and
   because pinning an older released version is the mechanism by which
   FeatureScript keeps scripts stable across releases. If a different version is
   wanted, `FEATURESCRIPT_VERSION` in
   `packages/cad-core/src/cad_core/featurescript.py` is the single place to
   change it — the declaration and the import are generated from that one
   constant.
2. **The empty `precondition` block.** The generated custom feature takes no
   user-facing parameters, so its `precondition` block is empty. Onshape's
   documentation describes the precondition as a predicate whose failing
   statements abort the feature, which implies an empty one trivially passes,
   and a parameterless custom feature is a common pattern — but no documentation
   page was read that states outright that an empty block is valid. This is the
   one syntax detail most likely to need adjustment when the output is first
   pasted into Onshape.
3. **Very large or very small magnitudes.** A length whose shortest
   representation uses exponent notation (for example `1e-05`) would be emitted
   in that form. Whether FeatureScript accepts exponent notation in a numeric
   literal has not been verified. Ordinary millimetre dimensions are unaffected.

Verifying these against a real Feature Studio is the natural first step of
whatever stage follows.
