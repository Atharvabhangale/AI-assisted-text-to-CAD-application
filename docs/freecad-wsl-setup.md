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
