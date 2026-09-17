#!/usr/bin/env bash
#
# Start the experimental API with FreeCAD as the selected execution backend.
#
# Developer convenience only. It adds no CAD behaviour, selects the backend
# through the ordinary `CAD_BACKEND` mechanism, and does not reach past the
# backend abstraction. The frontend is unchanged and stays backend-agnostic:
# it talks to port 8001 and is never told which engine is behind it.
#
# There is no fallback. If FreeCAD cannot be imported the application says so
# and nothing is substituted -- `resolve_backend()` has no `except` in it.
#
# Every path is derived or overridable; nothing here is specific to one
# machine or one user. Conventions match `scripts/freecad_parity.sh`.
#
#   CAD_FREECAD_HOME    extracted FreeCAD build   (default ~/freecad/squashfs-root)
#   CAD_FREECAD_PYTHON  interpreter to run        (default $CAD_FREECAD_HOME/usr/bin/python)
#   CAD_REPO            repository root           (default: derived from this script)
#   CAD_EXPERIMENTAL_PORT                         (default 8001)
#   CAD_EXPERIMENTAL_HOST                         (default 0.0.0.0, so Windows can reach WSL2)
#   CAD_EXPERIMENTAL_CACHE_ROOT                   (default under $TMPDIR)
#
# Usage, from WSL2:
#     bash scripts/freecad_api.sh
#
# Usage, from a Windows terminal:
#     wsl.exe -e bash -c "cd /mnt/c/path/to/repo && bash scripts/freecad_api.sh"

set -euo pipefail

# The repository root, derived from this script's own location so that no
# absolute path -- and no user name -- is written down anywhere.
REPO="${CAD_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
FC="${CAD_FREECAD_HOME:-$HOME/freecad/squashfs-root}"
PY="${CAD_FREECAD_PYTHON:-$FC/usr/bin/python}"
PORT="${CAD_EXPERIMENTAL_PORT:-8001}"
HOST="${CAD_EXPERIMENTAL_HOST:-0.0.0.0}"

if [ ! -d "$FC" ]; then
    echo "FreeCAD not found at: $FC" >&2
    echo "Set CAD_FREECAD_HOME to an extracted FreeCAD build." >&2
    exit 2
fi
if [ ! -x "$PY" ]; then
    echo "No runnable interpreter at: $PY" >&2
    echo "Set CAD_FREECAD_PYTHON -- the API also needs fastapi, uvicorn and" >&2
    echo "the Anthropic SDK, so this is usually a venv created from the" >&2
    echo "FreeCAD interpreter rather than the bundled one." >&2
    exit 2
fi

# The backend selection, and the two variables FreeCAD itself needs.
# LD_LIBRARY_PATH must be set BEFORE the interpreter starts: FreeCAD's
# bundled libssl conflicts with the system libcrypto, and that cannot be
# fixed from inside a running process.
export CAD_BACKEND=freecad
export CAD_FREECAD_HOME="$FC"
export LD_LIBRARY_PATH="$FC/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$REPO/apps/api/src:$REPO/packages/cad-core/src:$FC/usr/lib:$FC/usr/Ext"

# The application refuses to invent a cache root, so give it one.
export CAD_EXPERIMENTAL_CACHE_ROOT="${CAD_EXPERIMENTAL_CACHE_ROOT:-${TMPDIR:-/tmp}/cad-experimental-freecad}"
mkdir -p "$CAD_EXPERIMENTAL_CACHE_ROOT"

# Bridge the credential into this process if a developer .env is present.
# Nothing in the application reads that file -- the caller is expected to
# export the value, and this launcher is that caller. The value is read into
# the environment and is never printed, logged or passed on a command line.
# Generation simply reports itself unavailable when no credential is set.
if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$REPO/apps/api/.env" ]; then
    _key="$(sed -n 's/^ANTHROPIC_API_KEY=//p' "$REPO/apps/api/.env" | head -1 | tr -d '\r\n')"
    if [ -n "$_key" ]; then
        export ANTHROPIC_API_KEY="$_key"
    fi
    unset _key
fi

echo "repo            : $REPO"
echo "FreeCAD home    : $FC"
echo "interpreter     : $PY"
echo "CAD_BACKEND     : $CAD_BACKEND"
echo "cache root      : $CAD_EXPERIMENTAL_CACHE_ROOT"
echo "credential      : $([ -n "${ANTHROPIC_API_KEY:-}" ] && echo present || echo absent)"
echo "listening on    : http://$HOST:$PORT"
echo
echo "Check which engine is actually selected:"
echo "  curl -s http://127.0.0.1:$PORT/experimental/health"
echo

cd "$REPO/apps/api"
exec "$PY" -m uvicorn \
    --factory cad_experimental.app:app_from_environment \
    --host "$HOST" --port "$PORT"
