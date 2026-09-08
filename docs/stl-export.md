# STL mesh export

Status: **Stage 7 — binary STL export from the local CAD backend, verified by mesh round trip.**

## STL's role: mesh output, at the edge of the system

STL is a **mesh** interchange format, not a B-rep CAD format, and that
distinction is architectural rather than cosmetic:

```
CAD specification → local B-rep CAD engine → B-rep solid → tessellation → STL
```

Tessellation happens at the very last step, and the triangles never travel back
inward. The B-rep solid remains the internal geometry representation; STL is
never a substitute for it. A test asserts that no mesh vocabulary — `mesh`,
`triangle`, `tessell`, `stl`, `facet` — appears anywhere in the neutral
specification model.

STL is the third independent output path from the same `LocalCadResult`:

```
                                    ┌─→ STEP exporter   (docs/step-export.md)
CAD specification → local CAD engine ├─→ IGES exporter   (docs/iges-export.md)
                                    └─→ STL exporter    (this document)
```

None imports another. A test asserts all three directions of that
independence, and another exports one result through all three in sequence to
confirm they do not interfere.

## What STL does and does not preserve

An STL file is a bag of triangles approximating the boundary of a solid. It
does **not** carry:

- CAD feature history
- parametric dimensions
- feature ids or part names
- B-rep topology (faces, edges, vertices as CAD entities)
- design intent

A round trip recovers triangles and their coordinates, and nothing else. This
is not a defect being worked around — it is what the format is. STEP and IGES
are the formats for preserving geometry *as geometry*; STL is for consumption
by mesh tools.

Accordingly, nothing in the tests asserts that a round trip reproduces exact
CAD topology, face/edge/vertex counts, or solid identity, and the imported mesh
type deliberately exposes no `is_solid()`, `volume()`, `feature_id` or
`part_name` — a test asserts those attributes are absent.

### Mesh volume is not reported

The mesh was **not** independently shown to be a closed manifold, so no mesh
volume is computed or asserted. The B-rep volume (60000 mm³ for the reference
box, from `LocalCadResult.volume()`) remains the only volume this project
trusts. Had a mesh volume been reported, it would have needed to be labelled
distinctly from the exact B-rep volume; not reporting it avoids the ambiguity
entirely.

## Binary, not ASCII

Binary STL is the only mode offered. `Shape.exportStl`'s `ascii` parameter
defaults to `False`, so binary is both the library default and the natural
choice: it is compact, has a strictly specified layout that can be validated
structurally, and is what mesh consumers expect.

ASCII STL is not implemented, and `export_stl` has no `ascii` switch — a test
asserts the parameter is absent from the signature, so the mode cannot be
reached by accident.

## Tessellation settings

All four settings are passed explicitly; none is left to a library default.

| Setting | Value | Why |
|---|---|---|
| `tolerance` (linear deflection) | **0.01 mm** | Maximum distance between the true surface and its triangulation. 1/10000 of the reference part's 100 mm largest dimension — visually exact at millimetre scale, finer than 3D-printing or visualisation needs, without producing needlessly dense meshes. |
| `angularTolerance` | **0.1 rad** (~5.7°) | Limits the angle between adjacent facets, which is what governs smoothness on curved surfaces. |
| `relative` | **False** | Deflection is an absolute length in mm. The library default `True` scales tolerance by each edge's size, which would make a documented "0.01 mm" not actually mean 0.01 mm. |
| `parallel` | **False** | Single-threaded meshing. Byte-identical output was measured under both settings, so this is a conservative choice removing a potential source of ordering nondeterminism at no measured cost. Worth revisiting if models grow large enough for meshing time to matter. |

**For the current single-box part, every one of these is immaterial.** A box
has only planar faces, which need no refinement, so it tessellates to exactly
12 triangles (two per face) at any tolerance — measured from 0.001 mm to
1.0 mm, with an identical result. The values are chosen and documented for the
curved geometry the engine will eventually build, where they will matter a
great deal.

## Exporter API

```python
from cad_core.local_cad import build_part
from cad_core.stl_export import export_stl, read_stl, binary_stl_facts

result  = build_part(part)                        # part: cad_core.model.Part
written = export_stl(result, "plate.stl")         # -> pathlib.Path
facts   = binary_stl_facts(written)               # structural validation
mesh    = read_stl(written)                       # kernel-parsed triangles
```

```python
export_stl(result, path, *, tolerance=0.01, angular_tolerance=0.1) -> Path
```

- Accepts **only** a `LocalCadResult`. A raw document, a bare kernel shape,
  **loose mesh data** (a list of triangles), a `ValidationResult`, or anything
  else raises `TypeError`. Arbitrary mesh data is not an input to this
  exporter, and there is no path for Python or CadQuery source.
- Rejects non-positive tolerances with `ValueError`.
- `.stl` only, case-insensitive; anything else raises
  `UnsupportedStlExtensionError` and writes nothing.
- Raises `StlExportError` if the directory is missing, the writer reports
  failure, or it returns without leaving a non-empty file behind.
- Does not modify the source result. Tessellation attaches a triangulation to
  the shape; a test confirms the B-rep is still a valid one-solid `Solid` with
  six faces afterwards.

`read_stl(path) -> ImportedStlMesh` exposes `triangle_count()`, `node_count()`,
`nodes()` and `bounding_box()`. `binary_stl_facts(path) -> StlBinaryFacts`
exposes `file_size`, `header`, `declared_triangles`, `expected_size`,
`is_structurally_consistent` and `looks_ascii`.

Both are verification support, not a general importer. STL is never an input to
the pipeline and a mesh can never become the internal representation.

## Backend API used

- **Write** — `cadquery.Shape.exportStl(fileName, tolerance, angularTolerance,
  ascii, relative, parallel)`, which tessellates with OpenCascade's
  `BRepMesh_IncrementalMesh` and writes via `StlAPI_Writer`.
- **Read** — `OCP.RWStl.RWStl.ReadFile_s(OSD_Path(path))`, OpenCascade's own
  mesh reader, which returns a `Poly_Triangulation`: the triangles themselves.

CadQuery has **no** STL importer (`cadquery.importers` offers only
`importStep`, `importDXF`, `importBrep`, `importBin`, `importShape`), so the
read path goes to OCP directly. `StlAPI_Reader` also exists but converts every
triangle into a B-rep face, which would fabricate topology the format never
carried; `RWStl` is the honest reader for a mesh.

## File validation

The `.stl` extension is not treated as evidence. Binary STL has a strict
layout, and it is checked against the file's actual bytes:

| Field | Size |
|---|---|
| header | 80 bytes |
| triangle count | 4 bytes, little-endian unsigned |
| each triangle | 50 bytes (normal + 3 vertices as 12 floats, plus a 2-byte attribute) |

`read_stl` requires `file_size == 84 + 50 × declared_triangles` exactly and
raises otherwise. Tests cover a file named `.stl` that is actually ASCII text,
a truncated file, a file too short to hold a header, and a file whose declared
triangle count has been tampered with to 9999 — all rejected. A further test
confirms the declared count matches what the kernel parser actually reads.

The `looks_ascii` check (an ASCII STL begins with `solid`) confirms the output
is binary.

## Round-trip methodology

Export, validate the file structurally, read it back with the real kernel
parser, and measure. For the reference box — 100 × 60 × 10 mm at (10, 20, 30):

| Check | Result |
|---|---|
| triangles | 12 (positive, and sensible for six planar faces) |
| mesh nodes | 8 (the reader merges coincident coordinates) |
| file size | 684 bytes = 84 + 50 × 12 ✓ |
| binary, not ASCII | ✓ |
| mesh minimum corner | (10.0, 20.0, 30.0) |
| mesh maximum corner | (110.0, 80.0, 40.0) |
| mesh dimensions | (100.0, 60.0, 10.0) |
| every vertex inside the envelope | ✓ |

### Tolerance

Mesh coordinates are compared with **0.010001 mm** — the linear deflection
(0.01 mm) plus 1e-6 mm of kernel float noise. A tessellation is *allowed* to
sit inside the true surface by up to the deflection, so the envelope check must
permit exactly that and no more. Measurements of the unchanged B-rep use the
tighter 1e-6 mm shared with the local engine and the STEP/IGES exporters.

## Determinism: measured, not assumed

Repeated exports of the same solid came out **byte-identical** — four exports,
one SHA-256 digest — and identical under both `parallel=True` and
`parallel=False`. This is recorded as an **empirical result for this backend,
these settings and this geometry**, not as a guarantee of the STL format or of
OpenCascade. Unlike STEP and IGES, binary STL has no timestamp in its header,
which is the plausible reason it comes out stable where those do not.

Tests assert both: byte identity (so a regression would be noticed) and
geometric equivalence (which is the property that actually matters).

## Why STL matters later for browser visualisation

A browser renderer wants triangles. WebGL draws triangle meshes, not B-rep
surfaces, so any future in-browser preview will need tessellated geometry —
and STL is the simplest, most universally supported way to carry it. Producing
it here means the visualisation stage can consume a file the pipeline already
generates and verifies, rather than needing its own tessellation path. That is
why STL exists in the project despite discarding everything that makes the CAD
specification valuable: it is the format for *looking at* geometry, not for
*preserving* it.

## Limitations

- **One box.** The exporter meshes whatever the local engine built, and the
  engine still builds only a single-box part. Cylinders, through-holes, boolean
  subtraction, fillets and chamfers remain unimplemented.
- **Binary only.** No ASCII STL.
- **No mesh volume, no closure check.** The mesh is not verified to be a closed
  manifold, so no volume is derived from it.
- **Curved geometry is now exercised** (Stage 9 added the cylinder), which
  changed what the settings mean in practice. For a cylinder of diameter 20 and
  height 50 the exporter produces **500 triangles / 25084 bytes**, the mesh is
  inscribed in the true surface (worst vertex 9.13e-7 mm outside the exact
  radius, i.e. on it within numerical noise), and the worst radial
  approximation error is **3.109e-3 mm** — comfortably inside the 0.01 mm
  deflection budget. One finding: at this size the **angular** deflection is
  the binding constraint, not the linear one. Coarsening the linear tolerance
  from 0.01 mm to 0.5 mm leaves the count at 500; tightening it to 0.005 mm
  raises it to 560.
- **No 3MF, OBJ, glTF or GLB.** Not implemented.
- **Not verified in third-party mesh tools.** The round trip is OpenCascade
  writing and OpenCascade reading; whether the file loads correctly in a slicer
  or a mesh editor is untested here.
- **No import pipeline.** `read_stl` exists to verify exports. STL is not an
  input format, and a mesh must never become the internal representation.
