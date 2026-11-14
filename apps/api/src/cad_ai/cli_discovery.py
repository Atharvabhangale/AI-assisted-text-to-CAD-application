"""Find an installed command-line program the way a user's own shell would.

The CLI providers (:mod:`cad_ai.claude_code_provider`,
:mod:`cad_ai.antigravity_provider`) run an official CLI that the owner
installed and signed in to. A backend started from a launcher script often
does not see the same ``PATH`` as the owner's terminal -- on Windows a
non-interactive shell routinely misses user-level entries (CLAUDE.md §15
records the same trap for ``node``) -- so "it works in my terminal" and "the
backend cannot find it" were both true at once. This module looks where a
shell would, in a fixed order, and runs nothing:

1. the process's own ``PATH``;
2. on Windows, the user's and the machine's **saved** ``Path`` from the
   registry (what a freshly opened terminal would get);
3. a short list of well-known install folders for that program.

Only used when the owner did **not** name the executable: a configured path
is used as it is or not at all (see the providers' ``locate_cli``), never
silently replaced by another find.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Iterable, List, Mapping, Optional, Sequence

#: Executable suffixes tried in a well-known folder on Windows, in order.
WINDOWS_SUFFIXES: Sequence[str] = (".exe", ".cmd", ".bat")


def _expand(value: str, environ: Mapping[str, str]) -> str:
    """``%VAR%`` and ``~`` expansion against ``environ``, not the process."""
    out = value
    if sys.platform == "win32" or "%" in out:
        for name, val in environ.items():
            out = out.replace(f"%{name}%", val).replace(f"%{name.upper()}%", val)
    if out.startswith("~"):
        home = environ.get("USERPROFILE") or environ.get("HOME") or ""
        if home:
            out = home + out[1:]
    return out


def _registry_path_dirs() -> List[str]:
    """The saved user and machine ``Path`` entries (Windows only)."""
    if sys.platform != "win32":
        return []
    try:
        import winreg  # noqa: PLC0415 - Windows only
    except ImportError:  # pragma: no cover
        return []
    keys = (
        (winreg.HKEY_CURRENT_USER, r"Environment"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )
    dirs: List[str] = []
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        if isinstance(value, str):
            dirs.extend(os.path.expandvars(part) for part in value.split(os.pathsep) if part)
    return dirs


def _candidates_in(folder: str, name: str) -> Iterable[str]:
    if sys.platform == "win32":
        for suffix in WINDOWS_SUFFIXES:
            yield os.path.join(folder, name + suffix)
    yield os.path.join(folder, name)


def find_program(
    name: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    well_known_dirs: Sequence[str] = (),
    use_registry: bool = True,
) -> Optional[str]:
    """The absolute path of ``name``, or ``None``. Runs nothing.

    ``well_known_dirs`` may contain ``%VAR%`` and ``~``; they are expanded
    against ``environ``. The first existing file wins.
    """
    source = os.environ if environ is None else environ
    found = shutil.which(name, path=source.get("PATH"))
    if found:
        return os.path.abspath(found)
    if use_registry:
        extra = os.pathsep.join(_registry_path_dirs())
        if extra:
            found = shutil.which(name, path=extra)
            if found:
                return os.path.abspath(found)
    for raw in well_known_dirs:
        folder = _expand(raw, source)
        if not folder or "%" in folder or folder.startswith("~"):
            continue  # a variable the environment does not define
        for candidate in _candidates_in(folder, name):
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


#: Where the official installers put ``claude``.
CLAUDE_WELL_KNOWN_DIRS: Sequence[str] = (
    "~/.local/bin",  # native installer (Windows: %USERPROFILE%\.local\bin)
    "~/.claude/local",  # Claude Code's own local install
    "%APPDATA%/npm",  # npm -g on Windows
    "%LOCALAPPDATA%/Microsoft/WinGet/Links",  # winget
    "%LOCALAPPDATA%/Programs/claude",
    "~/.npm-global/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)

#: Where the official installer puts ``agy``.
AGY_WELL_KNOWN_DIRS: Sequence[str] = (
    "%LOCALAPPDATA%/agy/bin",  # the Windows installer
    "~/.local/bin",
    "~/.agy/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
)


__all__ = [
    "AGY_WELL_KNOWN_DIRS",
    "CLAUDE_WELL_KNOWN_DIRS",
    "WINDOWS_SUFFIXES",
    "find_program",
]
