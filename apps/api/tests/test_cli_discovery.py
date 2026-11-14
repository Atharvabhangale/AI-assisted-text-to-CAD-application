"""cad_ai.cli_discovery: find an installed CLI where the owner's shell would."""

import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from cad_ai.cli_discovery import AGY_WELL_KNOWN_DIRS, CLAUDE_WELL_KNOWN_DIRS, find_program
from cad_ai.claude_code_provider import locate_cli


def _make_exe(folder: str, name: str) -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + (".exe" if sys.platform == "win32" else ""))
    with open(path, "w") as handle:
        handle.write("#!/bin/sh\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class TestFindProgram(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        # Never let the real registry or a real install decide a test.
        patcher = mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, **extra: str) -> dict:
        return {"PATH": os.path.join(self.home, "empty"), "HOME": self.home, "USERPROFILE": self.home, **extra}

    def test_the_process_path_comes_first(self) -> None:
        on_path = _make_exe(os.path.join(self.home, "onpath"), "claude")
        _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        env = self.env(PATH=os.path.join(self.home, "onpath"))
        self.assertEqual(find_program("claude", environ=env, well_known_dirs=CLAUDE_WELL_KNOWN_DIRS), os.path.abspath(on_path))

    def test_the_native_installer_folder_is_found_when_path_misses_it(self) -> None:
        native = _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        self.assertEqual(find_program("claude", environ=self.env(), well_known_dirs=CLAUDE_WELL_KNOWN_DIRS), os.path.abspath(native))

    def test_a_variable_based_folder_expands_against_the_given_environment(self) -> None:
        appdata = os.path.join(self.home, "Roaming")
        npm = _make_exe(os.path.join(appdata, "npm"), "claude")
        env = self.env(APPDATA=appdata)
        self.assertEqual(find_program("claude", environ=env, well_known_dirs=CLAUDE_WELL_KNOWN_DIRS), os.path.abspath(npm))

    def test_the_saved_user_path_is_searched_before_the_install_folders(self) -> None:
        saved = _make_exe(os.path.join(self.home, "saved"), "claude")
        _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        with mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[os.path.join(self.home, "saved")]):
            self.assertEqual(find_program("claude", environ=self.env(), well_known_dirs=CLAUDE_WELL_KNOWN_DIRS), os.path.abspath(saved))

    def test_nothing_found_is_none_and_undefined_variables_are_skipped(self) -> None:
        self.assertIsNone(find_program("claude", environ=self.env(), well_known_dirs=CLAUDE_WELL_KNOWN_DIRS))
        self.assertIsNone(find_program("agy", environ=self.env(), well_known_dirs=AGY_WELL_KNOWN_DIRS))

    def test_a_configured_path_is_never_replaced_by_a_discovered_one(self) -> None:
        _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        env = self.env(CAD_CLAUDE_CODE_PATH=os.path.join(self.home, "missing", "claude.exe"))
        self.assertIsNone(locate_cli(env))

    def test_the_provider_finds_the_native_install_without_configuration(self) -> None:
        native = _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(native))


if __name__ == "__main__":
    unittest.main()
