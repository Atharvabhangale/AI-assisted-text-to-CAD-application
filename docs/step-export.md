# STEP export

Status: **Stage 5 — STEP export from the local CAD backend, verified by geometric round trip.**

## What this adds

> **The neutral CAD specification remains the source of truth. STEP is an
> output representation generated from the local CAD result.**

STEP (ISO 10303) is the project's **first interoperable CAD output**. Until now
the project produced two things: FeatureScript, which only Onshape understands,
and an in-memory OpenCascade solid, which nothing outside the process can see.
A STEP file can be opened by essentially any mechanical CAD system, so this is
the first artifact that leaves the project and remains useful.

```
CAD specification → validated Part → local CAD engine → B-rep solid → STEP file
```

## Supported extensions

| Extension | Supported | Notes |
|---|---|---|
| `.step` | **yes** | |
| `.stp` | **yes** | conventional alias; identical content |

Matching is case-insensitive, so `.STEP` and `.STP` work too.

### Why both extensions need explicit handling

CadQuery's `exporters.export` infers the format by upper-casing the file
extension and looking it up in its `ExportTypes`. `.step` resolves to `STEP`
and works; `.stp` becomes `STP`, which is **not** a member, and inference
raises `ValueError("Unknown extensions, specify export type explicitly")`.

This exporter therefore always passes `exportType="STEP"` explicitly and never
relies on that inference. Both extensions take exactly the same path, and the
lookup quirk cannot affect us.

## Unsupported formats

Everything else is rejected with `UnsupportedExportFormatError`, and **the
format is never silently switched**: `.stl`, `.iges`, `.igs`, `.brep`, `.3mf`,
`.dxf`, `.svg`, and any other extension — including a path with no extension at
all. A test asserts that a rejected export leaves the output directory empty.

IGES and STL are explicitly **not implemented** in this stage.

## Export API

```python
from cad_core.local_cad import build_part
from cad_core.step_export import export_step, read_step

result  = build_part(part)                      # part: cad_core.model.Part
written = export_step(result, "plate.step")     # -> pathlib.Path
imported = read_step(written)                   # round-trip verification
```

`export_step(result, path) -> Path`

- Accepts **only** a `LocalCadResult` from the local CAD engine. A raw
  specification dictionary, a bare CadQuery shape, a `ValidationResult` or
  anything else raises `TypeError`. There is no path for arbitrary Python or
  CadQuery source.
- Raises `UnsupportedExportFormatError` for any extension other than the two above.
- Raises `StepExportError` if the destination directory does not exist, if the
  write fails, or if the exporter returns **without leaving a non-empty file
  behind**. Success is never reported for a file that is not actually there.
- Returns the path written.
- Does not modify the source result — a test captures every measurement before
  and after and asserts they are unchanged.

`read_step(path) -> ImportedStepSolid` reads a STEP file back through
OpenCascade's `STEPControl_Reader` (via `cadquery.importers.importStep`). It
exists to **verify** an export, not as a general importer: nothing in the
pipeline consumes STEP as an input, and the specification remains the only
input format.

`ImportedStepSolid` exposes the same measurement helpers as `LocalCadResult`
(`is_solid()`, `solid_count()`, `bounding_box()`, `volume()`), sharing one
implementation so an exported shape and a re-imported one are measured
identically.

### Units

V1 fixes the specification's unit system as millimetres, so `unit="MM"` is
passed explicitly on export and the STEP file declares millimetres. No
conversion happens anywhere.

## Round-trip verification

Producing a file proves nothing about its contents, so the tests export, read
the file back through a real OpenCascade import, and measure the result:

| Check | Reference case |
|---|---|
| imported object is a valid solid | `ShapeType() == "Solid"` and `isValid()` |
| solid count | 1 |
| minimum corner | (10, 20, 30) mm |
| maximum corner | (110, 80, 40) mm |
| dimensions | 100 × 60 × 10 mm |
| volume | 60000 mm³ |
| topology | 6 faces, 12 edges, 8 vertices |

for a box of `size = (100, 60, 10)` at `position = (10, 20, 30)`. Both
extensions, the origin box, and a negative-position box are covered too.

### Tolerance

Kernel-derived measurements are compared with explicit tolerances, never with
exact floating-point equality:

- lengths: **1e-6 mm** (matching the local CAD engine)
- volumes: **1e-6 mm³**

## Determinism is geometric, not byte-for-byte

Two exports of the same solid are **not** byte-identical, and this is measured
rather than assumed. OpenCascade stamps per-export metadata into the STEP
header:

- a timestamp in `FILE_NAME`, e.g. `'2026-09-08T02:48:49'`
- an incrementing translator instance number in the originating-system string,
  e.g. `'Open CASCADE STEP translator 7.9 15'` then `'… 7.9 16'`

So the tests assert the opposite of byte identity: that the bytes differ, and
that both files nonetheless round-trip to identical geometry. That is the
guarantee this stage makes — *the same part always exports to a file describing
the same solid*, not *the same bytes*.

## Package boundary

The dependency direction is one-way:

```
CAD specification → local CAD engine → STEP exporter
```

`cad_core.step_export` imports `cad_core.local_cad`; nothing imports
`step_export`. The validator, the specification model, the FeatureScript
generator and the Onshape adapter know nothing about STEP, and no STEP or
CadQuery concept travels back up into the specification. A test walks the
syntax tree of every upstream module and asserts none of them imports
`cad_core.step_export`, and that the package root does not either — so
importing `cad_core` still requires no CAD kernel.

STEP export needs the optional `local-cad` extra, like the engine it exports from.

## Limitations

- **One box.** The exporter writes whatever the local engine built, and the
  engine still builds only a single-box part. Cylinders, through-holes,
  boolean subtraction, fillets and chamfers remain unimplemented.
- **STEP only.** No IGES, no STL, no 3MF, no BREP, no mesh formats.
- **No import pipeline.** `read_step` is verification support. STEP is not an
  input to the application and never will be on the normal path.
- **No assembly, colour, material or metadata.** The file carries a single
  solid body and nothing else. The part's name and feature id are not written
  into the STEP file; STEP metadata is not part of this stage.
- **Round-trip fidelity is verified for a box.** Planar geometry is the easiest
  case for STEP; nothing here demonstrates fidelity for curved geometry,
  because no curved geometry can be built yet.
- **Not verified against third-party CAD.** The round trip is OpenCascade
  writing and OpenCascade reading. That a file opens correctly in, say,
  SolidWorks or Fusion is untested here.
