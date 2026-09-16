set -u
FC="${CAD_FREECAD_HOME:-$HOME/freecad/squashfs-root}"; SIDE="${CQ_SIDE:-$HOME/freecad/cq-side}"
REPO="${CAD_REPO:?set CAD_REPO to the repository root}"
export CAD_FREECAD_HOME="$FC"; export LD_LIBRARY_PATH="$FC/usr/lib"
export PYTHONPATH="$REPO/apps/api/src:$REPO/packages/cad-core/src:$FC/usr/lib:$FC/usr/Ext:$SIDE"
"$FC/usr/bin/python" "$REPO/scripts/freecad_parity.py"
