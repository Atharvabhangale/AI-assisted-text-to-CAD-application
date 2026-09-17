# Running the FreeCAD backend under WSL

Measured on Stage 57. Every path below comes from an environment variable;
nothing here is specific to one machine's user directory.

## What the backend requires

`cad_experimental.freecad_backend` reads **`CAD_FREECAD_HOME`** as a
filesystem location only — nothing there is executed, no subprocess is
spawned, no GUI is started. It expects an *extracted* official build whose
`usr/lib` and `usr/Ext` it puts on `sys.path`, then imports `FreeCAD` and
`Part`. It raises `BackendUnavailable` and **never falls back** to another
engine.

## Two constraints that decide the whole setup

**1. The interpreter must be FreeCAD's own.** The official AppImage is built
against Python 3.11. Importing it from a newer system Python fails at the C
ABI:

```
ImportError: .../libFreeCADBase.so: undefined symbol: _Py_PackageContext
```

Ubuntu 26.04 ships Python 3.14, so the system interpreter cannot host this
backend. Use `$CAD_FREECAD_HOME/usr/bin/python` (3.11.9).

**2. CadQuery must be installed in that same interpreter.** This is not
optional and not about running both engines: `freecad_backend` imports
`cad_core.render_model`, which imports `cad_core.local_cad`, which requires
CadQuery. The FreeCAD backend therefore cannot even *import* without the
other engine present.

## Setup

```sh
export FREECAD_VERSION="${FREECAD_VERSION:-1.0.0}"
export FREECAD_ASSET="FreeCAD_${FREECAD_VERSION}-conda-Linux-x86_64-py311.AppImage"
export FREECAD_ROOT="${FREECAD_ROOT:-$HOME/freecad}"

mkdir -p "$FREECAD_ROOT" && cd "$FREECAD_ROOT"
wget -O "$FREECAD_ASSET" \
  "https://github.com/FreeCAD/FreeCAD/releases/download/${FREECAD_VERSION}/${FREECAD_ASSET}"
chmod +x "$FREECAD_ASSET"
./"$FREECAD_ASSET" --appimage-extract      # no FUSE needed; libfuse2 is absent on WSL
```

`--appimage-extract` matters: WSL has no `libfuse2`, so mounting the AppImage
would fail. Extraction costs about 2.4 GB.

### CadQuery, without breaking FreeCAD

A plain `pip install cadquery` into the bundled interpreter **fails**:

```
error: uninstall-distutils-installed-package
× Cannot uninstall vtk 9.2.6
```

CadQuery wants a newer `vtk`; FreeCAD installed its own through distutils and
pip cannot safely remove it. Do not force it — a half-uninstalled FreeCAD
would execute geometry wrongly rather than fail loudly, which is worse for a
parity measurement. Install to a side directory instead:

```sh
export FREECAD_HOME="$FREECAD_ROOT/squashfs-root"
export CQ_SIDE="${CQ_SIDE:-$FREECAD_ROOT/cq-side}"
"$FREECAD_HOME/usr/bin/python" -m pip install --target "$CQ_SIDE" cadquery
```

Both then coexist in one process — measured: cadquery 2.8.0 and FreeCAD
1.0.0 together, with FreeCAD geometry still exact.

## Running

`LD_LIBRARY_PATH` must be set **before Python starts**: the dynamic linker
resolves the system copy first otherwise, and it cannot be fixed from inside
a running process.

```sh
export CAD_FREECAD_HOME="$FREECAD_HOME"
export LD_LIBRARY_PATH="$FREECAD_HOME/usr/lib"
export PYTHONPATH="$REPO/apps/api/src:$REPO/packages/cad-core/src:$FREECAD_HOME/usr/lib:$FREECAD_HOME/usr/Ext:$CQ_SIDE"
"$FREECAD_HOME/usr/bin/python" <your script>
```

## Verifying

```
freecad_available()  -> True
name / version       -> freecad 1.0.0
create_box((10,20,30)) -> measure volume 6000.0, 1 solid, 6 faces, 12 edges
render_model(...)    -> RenderModel
resolve_backend()    -> FreeCadBackend   (CAD_BACKEND=freecad; never falls back)
```

---

# FreeCAD as a second execution backend: topology and semantic selectors

FreeCAD was a smoke-test backend: it could build solids and measure them, but
it could not answer the topology questions the semantic selectors are built
on, so `straight`, `circular` and a rim's `position` were unreachable on it.
This section records what changed, what was measured, and what is still not
there.

**CadQuery remains the default.** `CAD_BACKEND` defaults to `cadquery`,
`resolve_backend()` never falls back, and nothing selects FreeCAD
automatically. FreeCAD is experimental and explicitly selected.

## Supported Operation Plan operations

Every executable operation the plan has: `box`, `cylinder`, `through_hole`,
`subtract`, `fillet`, `chamfer`, and `pattern` (which the executor expands
into repeated operations, so a backend inherits it without implementing
anything). `sketch`, `extrude` and `revolve` are refused at the execution
boundary for **both** backends -- that is the adapter's decision, not a
FreeCAD limitation.

## Supported semantic selectors

All of them: `all`, `axis_parallel`, `straight`, `circular`, an unsigned
`axis`, and a circular selector's `position` (`top`/`bottom`).

**No selector logic lives in the FreeCAD backend.** It answers
`describe_edges` with neutral `EdgeFacts` and
`cad_experimental.edge_semantics` decides what a selector means, exactly as
for CadQuery. A test asserts the backend's source contains no selector
vocabulary at all, because a backend that decided for itself what `top` meant
could agree with CadQuery on every number while answering a different
question.

## FreeCAD-specific topology behaviour

**Seams are found with FreeCAD's own predicate.** `Edge.isSeam(face)` is the
counterpart of the CadQuery path's `BRepTools::IsReallyClosed`, and it was
cross-checked against an independent witness -- a seam edge has exactly one
distinct ancestor face, where an outer corner has two. Both agree on the
parity fixture. This matters more than it looks: on a drilled plate the seam
and an outer corner are both 10 mm lines along Z, identical in every quantity
this project measures, and only topology separates them.

**The genuine behavioural difference, and why it never reaches a user.**
Handed a selection containing a seam, the two kernels do not behave alike:

| | CadQuery | FreeCAD |
|---|---|---|
| `fillet` over a selection containing the seam | refuses (rule E5) | **silently drops the seam and blends the rest** |

Measured: an `axis_parallel`/Z fillet on the parity plate returns FreeCAD's
four-corner result, volume `56824.0710525538`, exactly as though the seam had
never been selected. A silently skipped edge is precisely what this project
forbids.

It never reaches a caller, and **not** because the backend special-cases it.
Because `describe_edges` reports `is_seam` honestly, the shared resolver
refuses such a selection with code **R2** before any kernel is asked -- so
both backends refuse `axis_parallel`/Z identically, for the same stated
reason, and no FreeCAD-specific policy code exists.

**Edge indices are not comparable between backends.** They are opaque
handles. The resolver orders by geometry and uses an index only to settle
geometrically identical edges. The parity invariant is *same semantic
selector -> same intended edges*, never *same edge number*. On this fixture
the two happen to agree, including the seam at index 14; that is a
coincidence and nothing depends on it.

**`EdgeFacts.length` is a parameter span, not an arc length.** The CadQuery
backend reports `LastParameter - FirstParameter`, which is 6.283185 for a
diameter-20 rim rather than 62.83. FreeCAD's own `Edge.Length` would report
the arc length, so this backend reports the parameter span too and the two
agree. Nothing reads the field today; it is descriptive evidence only.

## Current parity status

Measured on the fixture **100 x 60 x 10 mm plate + centred diameter-20
through hole**, FreeCAD 1.0.0 build 39109 against CadQuery 2.8.0.

| | verdict |
|---|---|
| baseline validity / solids / volume / bbox / faces / edges | EXACT |
| `circular` + X | EXACT (0 candidates, code R1 on both) |
| `axis_parallel` + Z | EXACT (5 candidates, 0 selected, code R2, 1 seam) |
| `straight` + Z | EXACT (4 of 4) |
| `circular` + Z | EXACT (2 of 2) |
| `circular` + Z + `top` | EXACT (1 of 2) |
| `circular` + Z + `bottom` | EXACT (1 of 2) |
| CASE A fillet `straight`/Z r=2 | EXACT -- `56824.0710525538`, 11 faces, 27 edges |
| CASE B chamfer `circular`/Z/`top` d=1 | EXACT -- `56825.944222323116`, 8 faces, 17 edges |

Tally: **EXACT 9, TOLERANT 0, SEMANTIC 0, UNSUPPORTED 0, MISMATCH 0,
ERROR 0.** Both engines sit on OpenCascade, so bit-identical volumes are
expected rather than remarkable; the result that matters is that the same
canonical plan selected the same edges on both.

## Remaining unsupported capabilities

**`render_model` (ERROR on a FreeCAD-only machine).** Not a FreeCAD
limitation: `cad_core.render_model` imports `cad_core.local_cad`, which
raises without CadQuery. The FreeCAD backend's tessellation and dataclass
construction are implemented and unchanged. The module-scope import of that
package was made lazy, which is what took FreeCAD from *unimportable without
CadQuery* to 14 of 15 capabilities working; closing the last one means
decoupling `cad_core.render_model`, which is shared with the stable branch
and was left alone.

**The V1 document path does not consult `CAD_BACKEND` at all.** This is the
most important limitation on this page. `resolve_backend()` has exactly one
production call site, in `executor.py`. `build.py` sends any plan a V1
document can express to `CadApplicationService`, which is `cad_core`'s
CadQuery engine with no `CadBackend` involved. So:

* `CAD_BACKEND=freecad` governs **only** plans that carry a semantic
  selector, because only those route to the graph executor.
* A simple box, or a plate with a hole, builds on **CadQuery regardless of
  `CAD_BACKEND`**.
* On a machine with FreeCAD and no CadQuery, `cad_experimental.build` cannot
  even be imported.

FreeCAD is therefore a genuine second backend **for the graph-executed
path**, which is exactly the semantic-selector path. It is not yet an
alternative engine for the whole application, and a caller who sets
`CAD_BACKEND=freecad` and builds a plain box is served CadQuery geometry
without being told. Fixing that means routing the document path through the
backend abstraction, which is its own piece of work.

## Selecting FreeCAD explicitly

```sh
export CAD_FREECAD_HOME=~/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib   # before Python starts
export PYTHONPATH=packages/cad-core/src:apps/api/src
export CAD_BACKEND=freecad
```

`LD_LIBRARY_PATH` must be set before the interpreter starts: FreeCAD's
bundled `libssl` conflicts with the system `libcrypto`, and that cannot be
fixed from inside a running process.

## Running the parity harness

Each probe runs on one engine and writes JSON; the report compares two
records. They are separate steps because no interpreter on the primary
machine has both kernels -- FreeCAD is a WSL2 build, CadQuery a Windows
virtual environment -- and comparing records means the comparison cannot
accidentally settle a question by asking a kernel.

```sh
# on the machine that has FreeCAD
python -m cad_experimental.backend_parity_probe --backend freecad --out freecad.json
# on the machine that has CadQuery
python -m cad_experimental.backend_parity_probe --backend cadquery --out cadquery.json

# anywhere
python -m cad_experimental.backend_parity_report cadquery.json freecad.json
```

The probe also audits every interface capability and reports one of
**PASS / UNSUPPORTED / ERROR / NO_SOURCE** per capability, so a failure names
the capability and the kind of failure rather than hiding behind one broad
exception. The report adds **EXACT / TOLERANT / SEMANTIC / MISMATCH**, kept
distinct on purpose: a topology difference on an otherwise-agreeing solid is
`SEMANTIC`, never folded into `MISMATCH`.

The same two canonical plans can be executed end to end on either engine:

```sh
python -m cad_experimental.backend_plan_probe --backend freecad
python -m cad_experimental.backend_plan_probe --backend cadquery
```

## Tests

`apps/api/tests_experimental/test_freecad_topology.py` covers
`describe_edges`, `edges_at`, the six-selector matrix, `fillet_edges`,
`chamfer_edges`, the selector-to-operation path and the backend-neutral
comparison. Each backend's tests skip when its engine is absent, so a skip
means *not measured here*, never *passed*. On this machine that is 12 tests
on Windows and 26 in WSL; the 2 cross-backend tests skip in both, which is
why the JSON report above exists.

