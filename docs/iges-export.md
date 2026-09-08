# IGES export

Status: **Stage 6 — IGES export from the local CAD backend, verified by geometric round trip.**

> The neutral CAD specification remains the source of truth. IGES is an output
> representation generated from the local CAD result.

## Relationship to STEP export

IGES is the project's **second** interchange format. The two exporters are
siblings, not layers:

```
                                    ┌─→ STEP exporter  (docs/step-export.md)
CAD specification → local CAD engine ┤
                                    └─→ IGES exporter  (this document)
```

Neither imports the other, neither can redirect to the other, and a test
asserts both directions of that independence. STEP behaviour is unchanged by
this stage.

Where they differ in substance:

| | STEP | IGES |
|---|---|---|
| Mechanism | CadQuery `exporters.export(..., exportType="STEP")` | **OpenCascade directly** — CadQuery has no IGES support |
| Extensions | `.step`, `.stp` | `.igs`, `.iges` |
| Solid preserved on round trip | yes | yes — **but only in BRep mode** (see below) |
| Errors | `UnsupportedExportFormatError`, `StepExportError` | `UnsupportedIgesExtensionError`, `IgesExportError` |

The error types are parallel but deliberately distinct classes, so catching one
never silently catches the other.

## Supported extensions

| Extension | Supported |
|---|---|
| `.igs` | **yes** |
| `.iges` | **yes** |

Case-insensitive: `.IGS`, `.IGES`, `.Igs` all work.

**Unsupported formats** are rejected with `UnsupportedIgesExtensionError` and
nothing is written: `.step`, `.stp`, `.stl`, `.3mf`, `.dxf`, `.brep`, `.svg`,
`.txt`, and paths with no extension. An IGES request is **never silently
redirected to STEP**. STL, 3MF and DXF are not implemented.

## Exporter API

```python
from cad_core.local_cad import build_part
from cad_core.iges_export import export_iges, read_iges

result   = build_part(part)                    # part: cad_core.model.Part
written  = export_iges(result, "plate.igs")    # -> pathlib.Path
imported = read_iges(written)                  # round-trip verification
```

`export_iges(result, path) -> Path` accepts **only** a `LocalCadResult`; a raw
document, a bare kernel shape, a `ValidationResult` or anything else raises
`TypeError`. It raises `IgesExportError` if the directory is missing, if
OpenCascade refuses the shape or the write, or if the writer returns without
leaving a non-empty file — success is never reported for a file that is not
there. It does not mutate the source result.

`read_iges(path) -> ImportedIgesShape` is verification support, not a general
importer. IGES is never an input to the pipeline.

`ImportedIgesShape` deliberately reports what it *found* rather than presuming:
`shape_type()`, `is_solid()`, `solid_count()`, `face_count()`, `edge_count()`,
`vertex_count()`, `bounding_box()`, `volume()`.

## Mechanism: OpenCascade directly, because CadQuery has no IGES

**CadQuery cannot write or read IGES.** Verified in the installed version:
`exporters.ExportTypes` offers AMF, BIN, BREP, DXF, STEP, STL, SVG, THREEMF,
TJS, VRML, VTP — no IGES — and `cadquery.importers` exposes only `importStep`,
`importDXF`, `importBrep`, `importBin` and `importShape`. A test asserts both
absences, so if a future CadQuery adds IGES we will find out rather than
silently keeping the lower-level path.

This module therefore drives OpenCascade through `OCP.IGESControl`:

**Write** — `IGESControl_Writer(unit="MM", theModecr=1)` → `AddShape(shape.wrapped)`
→ `ComputeModel()` → `Write(path)`

**Read** — `IGESControl_Reader()` → `ReadFile(path)` → `TransferRoots()` → `OneShape()`

## What the round trip actually preserves

Measured on the reference box (100 × 60 × 10 mm at (10, 20, 30)), not assumed.
**The writer mode decides the answer**, and the difference is large:

| Property | `theModecr=0` (faces — the OCC default) | `theModecr=1` (BRep — used here) |
|---|---|---|
| imported `ShapeType` | `Compound` | **`Solid`** |
| `isValid()` | True | True |
| solids / shells | 0 / 0 | **1 / 1** |
| faces | 6 | 6 |
| edges / vertices | 24 / 24 (unshared) | **12 / 8 (shared)** |
| bounding box | exact | exact |
| dimensions | exact | exact |
| `Volume()` | **15200.0 — meaningless** | **60000.0 — correct** |

In faces mode the six faces come back unstitched — 24 edges instead of 12,
because no edge is shared between neighbouring faces — so there is no solid at
all. Critically, `Volume()` *still returns a number* in that case, and that
number is wrong. A caller who trusted it would get 15200 mm³ for a 60000 mm³
box.

This module writes with `IGES_BREP_MODE = 1`, so **for this exporter the round
trip does preserve a solid**, with correct topology and volume. A test drives
faces mode explicitly and asserts the degraded result, so the choice of mode is
evidenced rather than folklore.

### Preserved, for the reference box

| Property | Preserved | Value |
|---|---|---|
| spatial placement | yes | min (10, 20, 30), max (110, 80, 40) |
| bounding dimensions | yes | 100 × 60 × 10 mm |
| surface geometry | yes | 6 planar faces |
| solid topology | yes | 1 solid, 1 shell, 12 edges, 8 vertices, `isValid()` |
| volume | yes | 60000 mm³ |

`volume()` is documented as meaningful **only when `is_solid()` is true**, and
the test that checks it asserts `is_solid()` first.

### Drilled parts survive the BRep-mode round trip

Stage 10 added the `through_hole` modifier, so the exported body can carry an
internal cylindrical wall. That is a fair question for IGES, which is
surface-oriented: measured for a 100 × 60 × 10 mm plate at the origin with one
Ø20 `+Z` hole at (20, 20), written in BRep mode:

| Property | Value |
|---|---|
| imported `ShapeType` | `Solid` |
| `isValid()` | True |
| solids | 1 |
| topology | 7 faces, 15 edges, 10 vertices — unchanged by the round trip |
| bounding box | (0, 0, 0) → (100, 60, 10) mm |
| volume | 56858.407346410204 mm³, **delta 0.0** from `100·60·10 − π·10²·10` |
| file size | 17658 bytes |

So the hole comes back as topology, and the volume comes back bit-exact — which
is notable because the same solid through STEP came back 2.18e-10 mm³ off (see
`docs/step-export.md`). No claim is made about *why*; it is one measurement of
one solid, recorded rather than explained.

## Verifying the file is really IGES

Extension is not evidence, and neither is a textual "looks like IGES" check.
Format identity is established with the real OpenCascade parser — but a
successful read is **not** sufficient on its own, which was discovered by
testing:

| Input | `ReadFile` status | Roots transferred |
|---|---|---|
| genuine IGES | `RetDone` | 1 |
| a STEP file | `RetError` | — |
| arbitrary text | **`RetDone`** | **0** |
| empty file | **`RetDone`** | **0** |

`IGESControl_Reader.ReadFile` reports `RetDone` for junk and for empty files.
What actually distinguishes an IGES file is **at least one transferred root**,
so `read_iges` requires that and raises `IgesExportError` otherwise. Tests
cover a renamed STEP file, arbitrary text, and an empty file.

As a secondary structural check, one test verifies the IGES section layout:
columns 73-80 of every line carry a section letter, the file starts with an `S`
line and ends with a `T` line, and the set of section letters used is exactly
`{S, G, D, P, T}`.

## Tolerances

Kernel-derived measurements are compared with explicit tolerances, never exact
floating-point equality — the same values used by the local CAD engine and the
STEP exporter, so all three measure alike:

- lengths: **1e-6 mm**
- volumes: **1e-6 mm³**

## Why byte determinism is not required

Two IGES exports of the same solid are not required to be byte-identical, and
Stage 9 measured what actually happens rather than leaving it asserted:

| Comparison | Bytes identical? |
|---|---|
| repeated exports **within one process** | **yes** |
| exports from **separate processes** | **no** |

The IGES global section carries a creation timestamp (e.g.
`20260908.055551`), and it is captured once per process rather than per write —
so within a run the bytes come out stable, and across runs the timestamp
advances and they diverge. (An earlier version of this document asserted the
bytes "differ between runs" as though that applied within a run too; the
measurement above corrects it.)

Either way, byte equality is not what is asserted: it would test the clock, not
the geometry.

What is asserted instead: repeated exports all round-trip to identical
geometry — same shape type, same solid count, same bounding box, same volume.
The guarantee is *the same part always exports to a file describing the same
solid*, not *the same bytes*. This matches the position taken for STEP.

## Limitations

- **Whatever the local engine can build, and no more.** That is currently one
  box or cylinder plus any number of through-holes. Generic boolean
  subtraction, fillets and chamfers remain unimplemented.
- **Faces mode is not offered.** The mode is fixed at BRep. Exposing mode 0
  would mean offering an export whose round trip silently loses the solid and
  misreports volume.
- **IGES is an older, looser format than STEP.** It carries no assembly
  structure, no colour, no material, and no metadata here; the file holds one
  body and nothing else. The part's name and feature id are not written into it.
- **Round-trip fidelity is verified for planar and cylindrical geometry only** —
  boxes, cylinders and cylindrical hole walls. IGES is a surface-oriented format
  and curved-surface fidelity is where it is most likely to differ, so those
  cases now carry measurements; freeform surfaces remain untested because V1
  cannot build any.
- **Not verified against third-party CAD.** The round trip is OpenCascade
  writing and OpenCascade reading. Whether the file opens correctly in
  SolidWorks, Fusion or NX is untested here — and IGES interoperability in
  practice varies more between vendors than STEP does.
- **No import pipeline.** `read_iges` exists to verify exports. IGES is not an
  input format for the application.
