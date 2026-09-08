# Canonical CAD document serialization

Status: **Stage 15 — the V1 specification, serialized to and from canonical
JSON, with a content hash. No geometry is involved.**

## The CAD document is the authoritative artifact

```
CAD document (canonical JSON)
    ├──→ local B-rep        (cad_core.local_cad)
    ├──→ FeatureScript      (cad_core.featurescript)
    ├──→ STEP / IGES / STL
    └──→ RenderModel
```

Everything below the document is **derived**. If a derived artifact is lost it
is rebuilt from the document; if the document is lost, nothing can rebuild it.
Nothing derived is authoritative, and nothing derived feeds back — a test
builds geometry from a part and asserts the document and its hash are
unchanged afterwards.

## Relationship to `docs/cad-specification.md`

The canonical CAD document **is** the serialized V1 specification, schema
version 1.0.0. This document adds no fields, no vocabulary and no version of
its own; the specification is unchanged by this stage.

The canonical field *order* is even taken from the specification's own tables
rather than restated here: a feature emits `model.COMMON_FEATURE_FIELDS`
followed by the required and then the optional columns of
`model.FEATURE_PARAMETERS`, which are the Section C tables. A test asserts
the emitted keys match that derivation exactly, so the two cannot drift apart.

## API

```python
from cad_core import (
    serialize_part, deserialize_part,     # Part ↔ JSON-compatible structure
    part_to_json, part_from_json,         # Part ↔ JSON text
    part_to_bytes, part_hash,             # canonical bytes and their digest
    parts_equivalent,                     # document identity
    save_part, load_part,                 # file persistence
)
```

`serialize_part` returns a structure containing only `dict`, `list`, `str` and
`float` — no model objects, nothing from a kernel — and it is accepted by
`json.dumps` with no custom encoder and by `cad_core.validate` unchanged.

The mapping is **explicit**. Python's generic dataclass serialization
(`dataclasses.asdict`) is deliberately not the contract: it would leak field
order from the class definitions, emit `null` for an absent `description`
(which the validator rejects), and change silently if a dataclass were
reordered.

### Typed `Part` ↔ JSON mapping

| Typed | Document |
|---|---|
| `Part.schema_version`, `.units`, `.name` | strings, always emitted |
| `Part.description` | emitted only when not `None` |
| `Part.features` (tuple) | `features` array, order preserved |
| `Position`, `Size` | object with `x`, `y`, `z`, floats |
| `EdgeSelector` | object with `select`, and `axis` only for `axis_parallel` |
| `Subtract.tools` (tuple) | array of ids, order preserved |
| feature class | `type` string from the class's `TYPE` |

## Canonicalization rules

For any valid `Part` there is exactly **one** canonical document and exactly
one canonical byte string.

1. **Object key order is fixed and explicit.** Root:
   `schema_version`, `units`, `name`, `description` (when present),
   `features`. Feature: `id`, `type`, then its required and then optional
   parameters. Vector: `x`, `y`, `z`. Selector: `select`, `axis`. JSON object
   key order carries no meaning in the contract, so fixing it is safe — and it
   is fixed by construction, not left to dictionary insertion order. Tests
   feed the same document with every object's keys reversed and then sorted,
   and get byte-identical output; another test asserts the canonical order is
   *not* alphabetical, so `sort_keys` is demonstrably not what is happening.
2. **Array order is preserved exactly, never sorted.** `features` order is the
   design (Section B.4) and `subtract.tools` order decides the order of
   subtraction (Section C.4). Tests assert that reordering either changes the
   bytes and the hash — that is the point.
3. **Numbers are JSON numbers, never strings.** Every numeric field of a
   `Part` is a Python `float` (the validator converts on the way in), so a
   length written `100` comes back `100.0`. Formatting is `json`'s own, which
   uses `repr`: the shortest text that round-trips exactly.
4. **Negative zero is normalised to `0.0`** — see below.
5. **Defaults are always materialised** — see below.
6. **Whitespace is minimal**: separators `","` and `":"`, no indentation, no
   trailing newline.
7. **UTF-8, not escapes**: `ensure_ascii=False`, so `"plaque 100x60 café"`
   keeps its real characters, and the canonical bytes are that text encoded
   UTF-8. A test writes a non-ASCII name to a file and asserts the bytes
   contain the UTF-8 sequence and no `\u` escape.

Byte-identity is asserted directly: ten serializations of the same part, and
independent validations of the same document, all produce one byte string.

## Default handling: always materialised

Section C makes `box.position`, `cylinder.position`, `cylinder.axis` and
`through_hole.axis` optional, and `cad_core.model` materialises those defaults
when a `Part` is built. A typed `Part` therefore **cannot** tell an omitted
`position` from one written `{"x":0,"y":0,"z":0}` — that information is gone
before serialization sees it.

The canonical form is therefore **fully materialised**: every optional
parameter with a default is emitted with its value. Chosen over omitting
defaults because it makes a document self-describing — a reader never needs
the Section C default table to know where a box is — and because it is the
only choice that can be checked from the typed part alone.

Consequences, all tested:

- a terse document and an explicit one **parse to the same part, serialize to
  the same bytes and share one hash**. Only the materialised form is
  canonical; the terse form is accepted *input*, not a second canonical
  representation. There is exactly one canonical JSON per part.
- `description` is not a default but an optional field with no value when
  absent. The validator rejects `"description": null` (rule S1), so it is
  omitted rather than nulled.

## Numeric handling

Measured, not assumed:

| Case | Behaviour |
|---|---|
| `100` in a source document | becomes `100.0` — the validator converts to `float` |
| `1e-9`, `1e-300`, `5e-324` (denormal) | round-trip exactly |
| `1e16`, `1e17`, `1e308` | round-trip exactly |
| `0.1`, `1/3`, `2.675` | round-trip exactly (`repr` is shortest-round-trip) |
| `-10.0`, `-0.001` | keep their sign |
| `-0.0` | **emitted as `0.0`** |
| `NaN`, `Infinity` | rejected by the validator (rule S19) |

### Why negative zero is normalised

`json.dumps(-0.0)` is `"-0.0"`, but `-0.0 == 0.0` in Python, so a `Part`
holding `-0.0` **is equal** to one holding `0.0`. Without normalisation two
equal parts would have two different canonical documents and two different
hashes, which would break the equivalence rule below. `-0.0 mm` and `0.0 mm`
are the same coordinate and rule S19 asks only that a component be finite, so
the sign of zero is dropped. Every other float is emitted unchanged.

A test proves the normalisation is doing something: it asserts the validated
part really does hold `-0.0`, that `json.dumps` really does emit `-0.0`, and
that the canonical document nonetheless contains no `-0.0`.

### Non-finite values

`json.loads` accepts the bare `NaN` and `Infinity` tokens, so they can reach
the boundary from a file. They are stopped by the **validator** (rule S19),
not by a second check here, and a test drives each literal through
`part_from_json` and asserts `S19`. `json.dumps` is called with
`allow_nan=False` so a non-finite value could never be written even if one
somehow reached a `Part`.

## Validation boundary

```
JSON text ──→ json.loads ──→ cad_core.validator.validate ──→ Part
```

Deserialization runs the **existing** validator. There is no second validation
implementation in this module and no repair of an invalid document.

| Failure | Raised |
|---|---|
| not well-formed JSON, or not a JSON object | `DocumentParseError` |
| not valid UTF-8 | `DocumentParseError` |
| violates any of S1–S20 | `DocumentValidationError` |

Both derive from `CadDocumentError`, and `DocumentParseError` is **not** a
subclass of `DocumentValidationError`: a malformed file is never reported as a
rule violation. `DocumentValidationError` carries the complete
`ValidationResult`, so every structured error — rule code, message, feature id,
field path — survives the raise; a test asserts its `rule_codes()` equals
`validate()`'s exactly. No part is returned and no geometry is generated.

Callers that prefer the project's non-raising style keep using
`cad_core.validate` directly; `deserialize_part` is the raising convenience on
top of it.

`serialize_part` refuses anything that is not a typed `Part`, so a raw
dictionary cannot enter through the serialization side either.

## Hashing

`part_hash(part)` returns the hex **SHA-256** of `part_to_bytes(part)` — the
canonical JSON text, UTF-8 encoded, with nothing prepended, appended or
normalised further. A test recomputes it with `hashlib.sha256` directly.

It hashes the document and nothing else: no kernel geometry, no FeatureScript,
no exported file. Those are not reachable from this module.

- the same part always produces the same digest (5 repeats, and across an
  independent validation of the same document);
- eight different documents in the test corpus produce eight different
  digests;
- the Section D fixture's digest is pinned in the test suite, so a change to
  the canonical form has to be a deliberate one.

No claim is made about collision resistance beyond what SHA-256 itself
provides.

## Equivalence

**Two parts are semantically equivalent iff their canonical documents are
identical** (`parts_equivalent`). Generated geometry is never compared, and no
geometric tolerance enters document equality — two documents that happen to
build the same solid by different feature histories are *not* equivalent, and
a test asserts they hash differently.

## Versioning

The only version in a CAD document is the specification's own
`schema_version`. **No serialization-format version was added**, and a test
asserts the document contains exactly one key containing "version". A second
version number would be another thing to keep in step for no benefit, since
the canonical form is a pure function of the specification's own field tables.

Application version, geometry-kernel version and exporter version are separate
concerns and appear nowhere in a document.

## File persistence

Deliberately small: two functions, no database, no index, no locking.

```python
save_part(part, "plate.json")              # refuses to overwrite
save_part(part, "plate.json", overwrite=True)
load_part("plate.json")
```

- the file holds **exactly** `part_to_bytes(part)` — canonical bytes, UTF-8,
  no trailing newline — so a file's SHA-256 *is* `part_hash(part)`, and a test
  asserts that;
- `.json` only; any other extension is refused explicitly, mirroring the
  exporters' extension discipline;
- an existing file is **not** silently overwritten: `overwrite=True` is
  required;
- a missing file, malformed JSON and an invalid document each fail with the
  matching error, and never half-write;
- no geometry is generated during save or load.

## What is deliberately not serialized

A CAD document describes engineering intent and feature history. It contains
none of:

- CadQuery or OpenCascade objects, or any kernel handle;
- meshes, triangles, vertices or normals;
- STEP, IGES or STL payloads;
- FeatureScript source or any generated code;
- cached or precomputed geometry, volumes, bounding boxes or topology counts;
- a schema of its own, a format version, or application metadata.

Enforced rather than promised: an AST check asserts `serialization.py` imports
neither `cadquery` nor `OCP` nor any of this project's geometry, export or
FeatureScript modules; a subprocess test asserts `import cad_core` and
`import cad_core.serialization` load with no kernel module in `sys.modules`;
another subprocess installs a meta-path hook that makes importing `cadquery`
or `OCP` fail, and serializes, deserializes and hashes a part anyway. A
further test scans the canonical text of an all-six-features document for
kernel, mesh, export and generated-source vocabulary and finds none.

## Relationship to the geometry, export and render layers

One-way, and the direction is the point:

```
docs/cad-specification.md  (the contract)
        │
        ▼
canonical CAD document  ──→ validate ──→ typed Part ──→ local CAD engine
                                                     ├──→ STEP / IGES / STL
                                                     ├──→ RenderModel
                                                     └──→ FeatureScript
```

`test_serialization.py` builds no geometry at all. The integration claim —
that a part recovered from a canonical document is the same part the engine
accepts — is proved separately in `test_document_geometry.py`, which
round-trips a box, the Section D plate, a cylinder, a subtract, a fillet and a
chamfer through JSON and asserts the rebuilt part produces the same solid:
same feature id, same solid count, same volume, same face count, same bounds.
It also asserts that building geometry leaves the document and its hash
untouched.
