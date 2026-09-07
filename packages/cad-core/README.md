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

## What this package does not do

It builds no geometry. There is no CAD kernel, no exporter, and no LLM
integration, and it has **no runtime dependencies** beyond the standard library.

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
