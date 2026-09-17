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

---

# FreeCAD as the selected execution backend: routing, rendering, live proof

The previous section made FreeCAD answer the topology and selector contract.
This one makes `CAD_BACKEND` actually govern execution, gives the graph path
a render model, and records what was proved live in a browser.

## What `CAD_BACKEND=freecad` now means

It selects FreeCAD for **every executable experimental Operation Plan**, not
only plans carrying a semantic selector.

Before, `resolve_backend()` had one production call site and the V1 document
path went straight to `CadApplicationService` -- which *is* the CadQuery
engine. A plain box therefore built on CadQuery whatever `CAD_BACKEND` said,
and nothing in the result revealed it. `build_plan` now routes on two
questions asked in order:

1. Does the plan need the executor (a `straight`, `circular`, or a rim's
   `position` cannot be written in Section C.7)?
2. Is the requested engine not the one the document path embodies?

Either answer sends the plan to the graph executor on the requested engine.
Nothing falls back in either direction, and every result carries `backend`
and `execution_path` so the engine never has to be inferred.

| plan | `CAD_BACKEND=cadquery` | `CAD_BACKEND=freecad` |
|---|---|---|
| 40 mm cube | `v1_document` | `graph_executor` |
| plate + hole | `v1_document` | `graph_executor` |
| + corner fillets | `graph_executor` | `graph_executor` |
| + rim chamfer | `graph_executor` | `graph_executor` |

The document path is kept for CadQuery rather than retired: it carries the
build cache, the build key, process isolation, the artifact registry and the
STEP/STL exports, none of which the executor has. Retiring it would mean
writing a second implementation of all of that.

## RenderModel

One render contract, unchanged. There is no `FreeCADRenderModel`, no second
mesh format, and the frontend cannot tell which engine produced a mesh.

`cad_core.render_model` previously imported `cad_core.local_cad`, which
raises without CadQuery -- so the render *contract*, which is plain data, was
unimportable without a kernel it does not use. Two changes fixed it:
`SUPPORTED_UNITS` now comes from `cad_core.model` (the contract's own source,
an identical `("mm",)`), and `LocalCadResult` is imported inside
`build_render_model`, the only function that needs it.

The graph path now builds its render model through `backend.render_model`,
so a semantic-selector build draws in the viewport instead of reporting a
successful build with nothing to show.

## Three claims that must not be blurred

**1. The experimental execution/build path works without CadQuery.** Proved:
on a machine with FreeCAD and no CadQuery, `cad_experimental.build` imports
and builds all four cases, producing measurements and a RenderModel.

**2. The full HTTP application still requires CadQuery to be installed.**
`app.py` imports `cad_experimental.generation`, which imports
`cad_ai.provider`; `cad_ai/__init__` eagerly imports `cad_ai.generation`,
which imports `cad_core.application_service` -> `artifact_registry` ->
`iges_export` -> `local_cad` -> CadQuery. That is the **AI layer**, shared
with the stable branch, and it was deliberately not touched. So a server
that can answer `/experimental/generate-plan` needs CadQuery importable even
when it never executes a single operation with it.

**3. Installed is not used.** In the live run below CadQuery was installed in
the WSL environment *solely* to satisfy that import chain. The execution
backend was FreeCAD, and every response says so. The two facts are separate
and neither implies the other.

## Live browser verification

Backend started in WSL with `CAD_BACKEND=freecad`, the existing experimental
page on 5174 proxying to it, real `claude-haiku-4-5-20251001` generation,
driven through the page's own buttons.

| | CASE A corner fillets | CASE B top rim chamfer |
|---|---|---|
| generation | succeeded | succeeded |
| validation | valid | valid |
| `backend` | **freecad** | **freecad** |
| `execution_path` | `graph_executor` | `graph_executor` |
| selector | `straight`/Z, **4 of 4** | `circular`/Z/`top`, **1 of 2** |
| volume mm3 | 56824.0710525538 | 56825.944222323116 |
| faces / edges | 11 / 27 | 8 / 17 |
| mesh | 1860 triangles | 1850 triangles |
| console errors | none | none |

The same two cases on `CAD_BACKEND=cadquery` give identical volumes, face
and edge counts and selector evidence, with 1032 and 898 triangles. Triangle
counts differ because the tessellators differ; that is a rendering detail,
not a geometric one, and is reported rather than compared.

## Running the FreeCAD-backed API

```sh
export CAD_FREECAD_HOME=~/freecad/squashfs-root
export LD_LIBRARY_PATH=$CAD_FREECAD_HOME/usr/lib      # before Python starts
export PYTHONPATH=$CAD_FREECAD_HOME/usr/lib:$CAD_FREECAD_HOME/usr/Ext:\
packages/cad-core/src:apps/api/src
export CAD_BACKEND=freecad
export CAD_EXPERIMENTAL_CACHE_ROOT=/tmp/fc-cache
python -m uvicorn --factory cad_experimental.app:app_from_environment \
  --host 0.0.0.0 --port 8001
```

That interpreter needs `fastapi`, `uvicorn`, `anthropic` **and** CadQuery --
the last only for the import chain in claim 2 above. `GET
/experimental/health` reports the engine that would actually run:

```json
"backend": {"name": "freecad", "available": true, "version": "1.0.0"}
```

`v1_document_path_available` is reported separately, because a build can
succeed without it.

## Status summary

**Supported on FreeCAD:** the whole executable Operation Plan -- `box`,
`cylinder`, `through_hole`, `subtract`, `fillet`, `chamfer`, `pattern`; all
semantic selectors (`all`, `axis_parallel`, `straight`, `circular`, `axis`,
`position`); topology inspection; `render_model`; full backend execution for
every executable plan.

**Experimental:** the FreeCAD runtime itself, under WSL2, from an extracted
AppImage.

**Remaining:** `sketch`, `extrude` and `revolve` are refused at the execution
boundary for *both* backends -- an adapter decision, not a kernel limit. The
full HTTP application still needs CadQuery importable (claim 2). And FreeCAD
still silently drops a seam handed to `makeFillet`, which never reaches a
caller because the shared resolver refuses such a selection first.

**CadQuery remains the default and there is no automatic fallback anywhere.**

---

# Running the experimental frontend against FreeCAD: the two-terminal workflow

Developer convenience only. Nothing here adds CAD behaviour, and nothing
here is FreeCAD-specific outside the launcher itself.

## What is and is not backend-specific

**FreeCAD is the selected execution backend.** `scripts/freecad_api.sh` sets
`CAD_BACKEND=freecad` and the ordinary resolution applies:
`resolve_backend()` returns the FreeCAD backend, contains no `except`, and
never substitutes another engine. If FreeCAD cannot be imported the
application says so and builds nothing.

**The frontend stays backend-agnostic.** It was not changed for this and
knows nothing about FreeCAD. It talks to `/api` on port 8001, consumes the
one neutral `RenderModel`, and displays the `backend` field the API reports
rather than deciding anything from it. Point the same page at a CadQuery
deployment and it behaves identically -- which is the property worth
keeping, and the reason the launcher lives in `scripts/` rather than in
`apps/web-experimental/`.

## Terminal 1 -- the FreeCAD-backed API (WSL2)

```sh
cd /mnt/c/path/to/repo
bash scripts/freecad_api.sh
```

From a Windows terminal instead:

```powershell
wsl.exe -e bash -c "cd /mnt/c/path/to/repo && bash scripts/freecad_api.sh"
```

The script derives the repository root from its own location, so there is no
absolute path and no user name written down anywhere. Everything it needs is
overridable:

| variable | default |
|---|---|
| `CAD_FREECAD_HOME` | `~/freecad/squashfs-root` |
| `CAD_FREECAD_PYTHON` | `$CAD_FREECAD_HOME/usr/bin/python` |
| `CAD_REPO` | derived from the script's location |
| `CAD_EXPERIMENTAL_PORT` | `8001` |
| `CAD_EXPERIMENTAL_HOST` | `0.0.0.0`, so Windows can reach the WSL2 server |
| `CAD_EXPERIMENTAL_CACHE_ROOT` | under `$TMPDIR` |

`CAD_FREECAD_PYTHON` usually needs setting. The API needs `fastapi`,
`uvicorn` and the Anthropic SDK, and -- until the AI layer's import chain is
decoupled -- CadQuery importable as well, none of which the bundled FreeCAD
interpreter has. A venv created *from* that interpreter satisfies both:

```sh
~/freecad/squashfs-root/usr/bin/python -m venv ~/fcvenv
~/fcvenv/bin/python -m pip install fastapi uvicorn anthropic cadquery
export CAD_FREECAD_PYTHON=~/fcvenv/bin/python
```

CadQuery being installed there is an **import** requirement, not an
execution one. FreeCAD is still what builds the geometry; the next section
is how to see that for yourself.

The launcher bridges `ANTHROPIC_API_KEY` from `apps/api/.env` if the
variable is not already set. The application itself never reads that file --
the caller is expected to export the value, and the launcher is that caller.
The key is read into the environment and never printed; the banner reports
only `present` or `absent`.

## Terminal 2 -- the experimental web frontend (Windows)

```powershell
cd apps\web-experimental
npm install      # first time only
npm run dev
```

Vite serves the page on **5174** and proxies `/api` to `127.0.0.1:8001`,
unchanged. WSL2 forwards localhost, so a server bound to `0.0.0.0` inside
WSL2 is reachable from the Windows-side browser with no extra configuration.

## Browser -- confirming FreeCAD, not CadQuery, executed the build

Three independent places say it, and none of them require trusting the
others:

1. **Ask the API directly.** `http://localhost:5174/api/experimental/health`
   in the browser (or `curl http://127.0.0.1:8001/experimental/health`):

   ```json
   "backend": {"name": "freecad", "available": true, "version": "1.0.0"}
   ```

   That is read from the resolver, so it reports the engine that *would*
   run, not the environment string that was requested.

2. **Build something and read the panel.** Generate or paste a plan and
   press Build. The measurements panel shows `backend  freecad` and
   `path  graph executor · freecad`. Under `CAD_BACKEND=freecad` every
   executable plan takes the graph path, because the V1 document path *is*
   the CadQuery engine -- so seeing `v1_document` there would itself be the
   signal that something was wrong.

3. **Check the build response.** Every `POST /experimental/build-plan`
   answer carries `backend` and `execution_path` at the top level, on both
   paths. Nothing has to be inferred from which fields are present.

A cross-check that does not rely on any label: build the 100 x 60 x 10 mm
plate with a centred d20 bore and a 1 mm chamfer on the top rim. Both
engines give `56825.944222323116` mm3 with 8 faces and 17 edges -- they sit
on the same kernel -- but the **mesh** differs, because the tessellators
differ. FreeCAD draws it with 1850 triangles and CadQuery with 898. The mesh
note under the viewport reports the count.
