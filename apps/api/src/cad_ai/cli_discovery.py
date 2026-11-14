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
3. a short list of well-known install folders for that program;
4. versioned install locations (:class:`VersionedLocation`): places where an
   application keeps one copy per version in a folder named after it -- an
   editor extension, a desktop app's private download. The **newest** copy
   wins, each location in the order given.

Only used when the owner did **not** name the executable: a configured path
is used as it is or not at all (see the providers' ``locate_cli``), never
silently replaced by another find.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

#: Executable suffixes tried in a well-known folder on Windows, in order.
WINDOWS_SUFFIXES: Sequence[str] = (".exe", ".cmd", ".bat")


_VARIABLE = re.compile(r"%([^%]+)%")


def _lookup(environ: Mapping[str, str], name: str) -> Optional[str]:
    """``environ[name]``, matching the name without regard to case.

    Windows environment names are case-insensitive (``Path``,
    ``LocalAppData``), and a mapping that is not ``os.environ`` keeps
    whatever spelling it was built with. An exact match wins.
    """
    value = environ.get(name)
    if value is not None:
        return value
    upper = name.upper()
    for key in sorted(environ):
        if key.upper() == upper:
            return environ[key]
    return None


def _home(environ: Mapping[str, str]) -> Optional[str]:
    """The home folder ``~`` stands for: ``USERPROFILE`` on Windows, ``HOME``
    elsewhere (a WSL shell may export ``USERPROFILE`` as the *Windows* home,
    which is not where a Linux ``~/.local/bin`` lives); the other one only
    as a fallback."""
    order = ("USERPROFILE", "HOME") if sys.platform == "win32" else ("HOME", "USERPROFILE")
    for name in order:
        value = _lookup(environ, name)
        if value:
            return value
    return None


def _expand(value: str, environ: Mapping[str, str], *, escape: bool = False) -> Optional[str]:
    """``%VAR%`` and ``~`` expansion against ``environ``, not the process.

    ``None`` when the pattern names a variable (or a home folder) the
    environment does not define. Each ``%VAR%`` is replaced once, in a single
    pass, so a value that itself contains ``%`` is taken literally.

    With ``escape`` the substituted values are glob-escaped, so that a home
    folder containing ``[`` or ``*`` is matched literally when the result is
    used as a glob pattern.
    """
    quote = glob.escape if escape else (lambda text: text)
    missing = False

    def substitute(match: "re.Match[str]") -> str:
        nonlocal missing
        found = _lookup(environ, match.group(1))
        if not found:
            missing = True
            return ""
        return quote(found)

    head, rest = ("~", value[1:]) if value.startswith("~") else ("", value)
    out = _VARIABLE.sub(substitute, rest)
    if head:
        home = _home(environ)
        if not home:
            return None
        out = quote(home) + out
    if missing or not out:
        return None
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


def program_in_folder(folder: str, name: str) -> Optional[str]:
    """``name`` (with a Windows suffix where that applies) directly inside
    ``folder``, as an absolute path, or ``None``. Runs nothing."""
    for candidate in _candidates_in(folder, name):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


@dataclass(frozen=True)
class VersionedLocation:
    """A place that holds one copy of a program per installed version.

    The copies live at ``<root>/<versioned>/<tail>/<program>``:

    * ``roots`` -- folders to look in; each may contain ``%VAR%``, ``~`` and
      glob wildcards. All roots of one location are pooled.
    * ``versioned`` -- a glob for the folder whose **name** carries the
      version (``anthropic.claude-code-*``, or just ``*``).
    * ``tail`` -- the path from that folder to the folder holding the
      executable; may contain wildcards, may be empty.

    The newest copy wins: see :func:`parse_version`.
    """

    roots: Tuple[str, ...]
    versioned: str
    tail: str = ""


_VERSION = re.compile(r"\d+(?:\.\d+)+")


def parse_version(folder_name: str) -> Tuple[int, ...]:
    """The first dotted number in a folder name as a tuple of integers.

    ``anthropic.claude-code-2.1.322-win32-x64`` is ``(2, 1, 322)``, which
    compares above ``(2, 1, 99)`` (a text comparison would not). A name with
    no dotted number is ``()``, which sorts below every version.
    """
    match = _VERSION.search(folder_name)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(0).split("."))


def _modified(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def newest_versioned(
    name: str, location: VersionedLocation, environ: Mapping[str, str]
) -> Optional[str]:
    """The newest copy of ``name`` in ``location``, or ``None``. Runs nothing.

    Newest is the highest :func:`parse_version` of the versioned folder's
    name; equal or unparseable versions are ordered by the executable's
    modification time, and then by path so the answer is always the same.
    """
    found: List[Tuple[Tuple[int, ...], float, str]] = []
    for raw in location.roots:
        root = _expand(raw, environ, escape=True)
        if root is None:
            continue  # a variable the environment does not define
        for base in sorted(glob.glob(root)):
            try:
                entries = sorted(os.listdir(base))
            except OSError:
                continue
            for entry in entries:
                if not fnmatch.fnmatch(entry, location.versioned):
                    continue
                holder = os.path.join(glob.escape(os.path.join(base, entry)), location.tail)
                for folder in sorted(glob.glob(holder)):
                    path = program_in_folder(folder, name)
                    if path is not None:
                        found.append((parse_version(entry), _modified(path), path))
    if not found:
        return None
    return max(found)[2]


def find_program(
    name: str,
    *,
    environ: Optional[Mapping[str, str]] = None,
    well_known_dirs: Sequence[str] = (),
    versioned_locations: Sequence[VersionedLocation] = (),
    use_registry: bool = True,
) -> Optional[str]:
    """The absolute path of ``name``, or ``None``. Runs nothing.

    ``well_known_dirs`` may contain ``%VAR%`` and ``~``; they are expanded
    against ``environ``. The first existing file wins. After them come the
    ``versioned_locations``, in order, each giving its newest copy.
    """
    source = os.environ if environ is None else environ
    found = shutil.which(name, path=_lookup(source, "PATH"))
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
        if folder is None:
            continue  # a variable the environment does not define
        path = program_in_folder(folder, name)
        if path is not None:
            return path
    for location in versioned_locations:
        path = newest_versioned(name, location, source)
        if path is not None:
            return path
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

#: Where applications that bundle ``claude`` keep it, one copy per version.
#: Searched after everything above, the newest copy of each in this order.
CLAUDE_VERSIONED_LOCATIONS: Sequence[VersionedLocation] = (
    # The Claude Code editor extension (VS Code, Insiders, remote server):
    # <extensions>/anthropic.claude-code-<version>[-<platform>]/resources/
    # native-binary/claude[.exe]
    VersionedLocation(
        roots=(
            "~/.vscode/extensions",
            "~/.vscode-insiders/extensions",
            "~/.vscode-server/extensions",
        ),
        versioned="anthropic.claude-code-*",
        tail="resources/native-binary",
    ),
    # The Claude desktop app's own download (Windows):
    # %LOCALAPPDATA%/Claude-Data/claude_*/claude-code/<version>/<hash>/claude.exe
    VersionedLocation(
        roots=("%LOCALAPPDATA%/Claude-Data/claude_*/claude-code",),
        versioned="*",
        tail="*",
    ),
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
    "CLAUDE_VERSIONED_LOCATIONS",
    "CLAUDE_WELL_KNOWN_DIRS",
    "VersionedLocation",
    "WINDOWS_SUFFIXES",
    "find_program",
    "newest_versioned",
    "parse_version",
    "program_in_folder",
]
