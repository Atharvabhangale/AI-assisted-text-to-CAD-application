# cad-core

The V1 CAD specification contract, in code: a typed representation of a
specification document and a deterministic **static** validator for it.

The contract itself is `docs/cad-specification.md` at the repository root; that
document is authoritative and this package implements schema version `1.0.0`.

## What this package does

- `cad_core.model` — the typed representation: `Part`, `Position`, `Size`,
  `EdgeSelector`, and the six V1 feature types (`Box`, `Cylinder`,
  `ThroughHole`, `Subtract`, `Fillet`, `Chamfer`), plus the contract constants.
- `cad_core.validator` — `validate(document) -> ValidationResult`, implementing
  the static rules **S1-S20** of Section E.1.
- `cad_core.errors` — `ValidationError` and `ValidationResult` records.
- `cad_core.rules` — rule codes and their requirement text.
- `cad_core.geometry` — the boundary for the geometric rules **E1-E5**.
- `cad_core.featurescript` — `generate_featurescript(part) -> str`, which
  renders Onshape FeatureScript source text for the supported subset (a single
  box). See `docs/featurescript-generation.md`.
- `cad_core.onshape_adapter` — the boundary FeatureScript will eventually be
  delivered through, and `cad_core.onshape_fakes` for the implementations that
  exist today (a not-configured default and a deterministic recording double).
  **Neither contacts Onshape.** See `docs/onshape-mcp-boundary.md`.
- `cad_core.local_cad` — `build_part(part) -> LocalCadResult`, the local
  execution backend, which builds a real B-rep solid with CadQuery over
  OpenCascade. Requires the optional `local-cad` extra and is **not** imported
  by the package root. See `docs/local-cad-engine.md`.
- `cad_core.step_export` — `export_step(result, path) -> Path`, writing a
  `LocalCadResult` to a STEP file (`.step` / `.stp`), plus `read_step` for
  round-trip verification. See `docs/step-export.md`.
- `cad_core.iges_export` — `export_iges(result, path) -> Path`, writing a
  `LocalCadResult` to an IGES file (`.igs` / `.iges`) via OpenCascade directly,
  plus `read_iges`. Independent of the STEP exporter. See
  `docs/iges-export.md`.
- `cad_core.stl_export` — `export_stl(result, path) -> Path`, tessellating a
  `LocalCadResult` to a binary STL mesh (`.stl`), plus `read_stl` and
  `binary_stl_facts`. A mesh output path, not a CAD format; independent of the
  STEP and IGES exporters. See `docs/stl-export.md`.

## What this package does not do

There is no exporter and no LLM integration. The specification, the validator
and the FeatureScript generator have **no runtime dependencies** beyond the
standard library; geometry building lives behind the optional `local-cad`
extra, so importing `cad_core` never requires a CAD kernel.

FeatureScript generation produces *source text only*: nothing connects to
Onshape, and the generated source is never executed. It has not been verified
against a live Onshape account.

The geometric rules E1-E5 require an evaluated solid and are **not
implemented**: `cad_core.geometry.check_geometric_rules` raises
`NotImplementedError` so that "not checked" can never be mistaken for "checked
and passed". A document that passes `validate` is *statically* valid, which is
not a guarantee that it can be built.

## Usage

```python
import json
from cad_core import validate

with open("part.json") as handle:
    result = validate(json.load(handle))

if result.valid:
    part = result.part          # a typed cad_core.model.Part
else:
    for error in result.errors: # every violation, deterministically ordered
        print(error)            # e.g. "S10 at features[0].size.x: ..."
```

`validate` takes a parsed JSON document, not a JSON string. It reports every
violation it can determine rather than stopping at the first, and it is strict:
unknown fields and unknown feature types are rejected, never ignored.

## Tests

Run from the repository root. Nothing needs to be installed:

```sh
PYTHONPATH=packages/cad-core/src python3 -m unittest discover \
    -s packages/cad-core/tests -t packages/cad-core/tests
```
