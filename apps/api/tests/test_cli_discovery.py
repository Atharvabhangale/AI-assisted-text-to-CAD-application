"""cad_ai.cli_discovery: find an installed CLI where the owner's shell would."""

import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

from cad_ai.cli_discovery import (
    AGY_WELL_KNOWN_DIRS,
    CLAUDE_VERSIONED_LOCATIONS,
    CLAUDE_WELL_KNOWN_DIRS,
    VersionedLocation,
    find_program,
    newest_versioned,
    parse_version,
    program_in_folder,
)
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


class TestVersionedLocations(unittest.TestCase):
    """The newest copy in a per-version folder: editor extension, desktop app."""

    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        self.local = os.path.join(self.home, "AppData", "Local")
        patcher = mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, **extra: str) -> dict:
        return {
            "PATH": os.path.join(self.home, "empty"),
            "HOME": self.home,
            "USERPROFILE": self.home,
            "LOCALAPPDATA": self.local,
            **extra,
        }

    def extension(self, folder_name: str, editor: str = ".vscode") -> str:
        return _make_exe(
            os.path.join(self.home, editor, "extensions", folder_name, "resources", "native-binary"),
            "claude",
        )

    def desktop(self, version: str, build: str = "claude_abc", digest: str = "deadbeef") -> str:
        return _make_exe(
            os.path.join(self.local, "Claude-Data", build, "claude-code", version, digest),
            "claude",
        )

    def test_versions_parse_as_numbers(self) -> None:
        self.assertEqual(parse_version("anthropic.claude-code-2.1.322-win32-x64"), (2, 1, 322))
        self.assertEqual(parse_version("2.1.99"), (2, 1, 99))
        self.assertEqual(parse_version("anthropic.claude-code-nightly"), ())
        self.assertGreater(parse_version("x-2.1.322"), parse_version("x-2.1.99"))
        self.assertGreater(parse_version("x-2.1.99"), parse_version("x-unknown"))

    def test_the_only_claude_is_inside_the_vs_code_extension(self) -> None:
        # The owner's machine: nothing on PATH, nothing in an install folder.
        bundled = self.extension("anthropic.claude-code-2.1.322-win32-x64")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(bundled))

    def test_the_newest_extension_wins_by_number_not_by_text(self) -> None:
        old = self.extension("anthropic.claude-code-2.1.99-win32-x64")
        new = self.extension("anthropic.claude-code-2.1.322-win32-x64")
        # Make the older version the most recently written file: the version
        # number decides, not the clock and not alphabetical order.
        os.utime(new, (1_000_000, 1_000_000))
        os.utime(old, (2_000_000, 2_000_000))
        self.assertEqual(locate_cli(self.env()), os.path.abspath(new))

    def test_other_extensions_are_not_claude(self) -> None:
        self.extension("someone.claude-code-9.9.9")
        self.extension("anthropic.claude-dev-9.9.9")
        self.assertIsNone(locate_cli(self.env()))

    def test_an_extension_folder_without_the_binary_is_skipped(self) -> None:
        os.makedirs(os.path.join(self.home, ".vscode", "extensions", "anthropic.claude-code-3.0.0", "resources"))
        older = self.extension("anthropic.claude-code-2.1.322")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(older))

    def test_insiders_and_the_remote_server_are_searched_and_pooled(self) -> None:
        insiders = self.extension("anthropic.claude-code-2.1.322", editor=".vscode-insiders")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(insiders))
        self.extension("anthropic.claude-code-2.1.100", editor=".vscode")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(insiders))
        server = self.extension("anthropic.claude-code-2.2.0-linux-x64", editor=".vscode-server")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(server))

    def test_equal_and_unparseable_versions_are_ordered_by_modification_time(self) -> None:
        stable = self.extension("anthropic.claude-code-2.1.322", editor=".vscode")
        insiders = self.extension("anthropic.claude-code-2.1.322", editor=".vscode-insiders")
        os.utime(stable, (2_000_000, 2_000_000))
        os.utime(insiders, (1_000_000, 1_000_000))
        self.assertEqual(locate_cli(self.env()), os.path.abspath(stable))
        os.utime(insiders, (3_000_000, 3_000_000))
        self.assertEqual(locate_cli(self.env()), os.path.abspath(insiders))

        location = VersionedLocation(roots=("~/apps",), versioned="tool-*")
        first = _make_exe(os.path.join(self.home, "apps", "tool-alpha"), "tool")
        second = _make_exe(os.path.join(self.home, "apps", "tool-beta"), "tool")
        os.utime(first, (2_000_000, 2_000_000))
        os.utime(second, (1_000_000, 1_000_000))
        self.assertEqual(newest_versioned("tool", location, self.env()), os.path.abspath(first))
        # Any parsed version beats an unparseable one, whatever the clock says.
        numbered = _make_exe(os.path.join(self.home, "apps", "tool-0.0.1"), "tool")
        os.utime(numbered, (1, 1))
        self.assertEqual(newest_versioned("tool", location, self.env()), os.path.abspath(numbered))

    def test_the_claude_desktop_data_layout_is_found_newest_first(self) -> None:
        self.desktop("2.1.99", build="claude_one")
        new = self.desktop("2.1.322", build="claude_two", digest="0123abcd")
        self.desktop("2.0.5", build="claude_two")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(new))

    def test_the_desktop_layout_needs_localappdata(self) -> None:
        self.desktop("2.1.322")
        env = self.env()
        del env["LOCALAPPDATA"]
        self.assertIsNone(locate_cli(env))

    def test_the_extension_comes_before_the_desktop_copy_even_when_older(self) -> None:
        bundled = self.extension("anthropic.claude-code-2.1.99")
        self.desktop("2.1.322")
        self.assertEqual(locate_cli(self.env()), os.path.abspath(bundled))

    def test_order_path_then_registry_then_install_folders_then_versioned(self) -> None:
        bundled = self.extension("anthropic.claude-code-9.0.0")
        self.desktop("9.0.0")
        env = self.env()
        self.assertEqual(locate_cli(env), os.path.abspath(bundled))
        native = _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        self.assertEqual(locate_cli(env), os.path.abspath(native))
        saved = _make_exe(os.path.join(self.home, "saved"), "claude")
        with mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[os.path.join(self.home, "saved")]):
            self.assertEqual(locate_cli(env), os.path.abspath(saved))
            on_path = _make_exe(os.path.join(self.home, "onpath"), "claude")
            env = self.env(PATH=os.path.join(self.home, "onpath"))
            self.assertEqual(locate_cli(env), os.path.abspath(on_path))

    def test_a_home_folder_with_glob_characters_is_matched_literally(self) -> None:
        odd = os.path.join(self.home, "user [x]")
        bundled = _make_exe(
            os.path.join(odd, ".vscode", "extensions", "anthropic.claude-code-2.1.322", "resources", "native-binary"),
            "claude",
        )
        env = self.env(HOME=odd, USERPROFILE=odd)
        self.assertEqual(locate_cli(env), os.path.abspath(bundled))

    def test_the_answer_is_deterministic(self) -> None:
        for version in ("2.1.99", "2.1.322", "2.1.5"):
            self.extension(f"anthropic.claude-code-{version}")
        answers = {locate_cli(self.env()) for _ in range(5)}
        self.assertEqual(len(answers), 1)
        self.assertIn("2.1.322", answers.pop())

    def test_the_claude_locations_are_the_documented_ones(self) -> None:
        extension, desktop = CLAUDE_VERSIONED_LOCATIONS
        self.assertEqual(
            extension.roots,
            ("~/.vscode/extensions", "~/.vscode-insiders/extensions", "~/.vscode-server/extensions"),
        )
        self.assertEqual(extension.versioned, "anthropic.claude-code-*")
        self.assertEqual(extension.tail, "resources/native-binary")
        self.assertEqual(desktop.roots, ("%LOCALAPPDATA%/Claude-Data/claude_*/claude-code",))


class TestConfiguredPath(unittest.TestCase):
    """CAD_CLAUDE_CODE_PATH: a file, or the folder holding it; never a fallback."""

    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        patcher = mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def env(self, configured: str) -> dict:
        return {
            "PATH": os.path.join(self.home, "empty"),
            "HOME": self.home,
            "USERPROFILE": self.home,
            "LOCALAPPDATA": os.path.join(self.home, "AppData", "Local"),
            "CAD_CLAUDE_CODE_PATH": configured,
        }

    def test_a_folder_containing_claude_is_accepted(self) -> None:
        folder = os.path.join(self.home, "tools", "native-binary")
        exe = _make_exe(folder, "claude")
        self.assertEqual(locate_cli(self.env(folder)), os.path.abspath(exe))
        self.assertEqual(locate_cli(self.env(f'"{folder}"')), os.path.abspath(exe))
        self.assertEqual(locate_cli(self.env(folder + os.sep)), os.path.abspath(exe))
        self.assertEqual(program_in_folder(folder, "claude"), os.path.abspath(exe))

    def test_a_file_is_still_used_as_it_is(self) -> None:
        exe = _make_exe(os.path.join(self.home, "tools"), "claude")
        self.assertEqual(locate_cli(self.env(exe)), os.path.abspath(exe))

    def test_a_configured_value_that_resolves_to_nothing_never_falls_back(self) -> None:
        empty = os.path.join(self.home, "tools")
        os.makedirs(empty)
        # Every place discovery would look has a claude; none may be used.
        _make_exe(os.path.join(self.home, "onpath"), "claude")
        _make_exe(os.path.join(self.home, ".local", "bin"), "claude")
        _make_exe(
            os.path.join(self.home, ".vscode", "extensions", "anthropic.claude-code-2.1.322", "resources", "native-binary"),
            "claude",
        )
        _make_exe(
            os.path.join(self.home, "AppData", "Local", "Claude-Data", "claude_a", "claude-code", "2.1.322", "ff"),
            "claude",
        )
        for configured in (empty, os.path.join(self.home, "missing"), os.path.join(self.home, "missing", "claude.exe")):
            with self.subTest(configured=configured):
                env = self.env(configured)
                env["PATH"] = os.path.join(self.home, "onpath")
                self.assertIsNone(locate_cli(env))
        # The same machine, unconfigured, does find one.
        self.assertIsNotNone(locate_cli(self.env("")))

    def test_a_folder_is_not_searched_below_its_top_level(self) -> None:
        folder = os.path.join(self.home, "tools")
        _make_exe(os.path.join(folder, "deeper"), "claude")
        self.assertIsNone(locate_cli(self.env(folder)))

    def test_quotes_of_either_kind_around_the_value_are_ignored(self) -> None:
        exe = _make_exe(os.path.join(self.home, "my tools"), "claude")
        for value in (f"'{exe}'", f'"{exe}"', f"  '{os.path.dirname(exe)}'  ", f'" {exe} "'):
            with self.subTest(value=value):
                self.assertEqual(locate_cli(self.env(value)), os.path.abspath(exe))


class _Windows:
    """Stands in for ``sys`` inside cli_discovery: the Windows branches."""

    platform = "win32"


class TestWindowsBehaviour(unittest.TestCase):
    """What can be checked off Windows: names, suffixes, expansion, order."""

    def setUp(self) -> None:
        self.home = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.home, ignore_errors=True))
        patcher = mock.patch("cad_ai.cli_discovery._registry_path_dirs", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.empty = os.path.join(self.home, "empty")

    def windows(self):
        return mock.patch("cad_ai.cli_discovery.sys", _Windows)

    @staticmethod
    def touch(folder: str, filename: str) -> str:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        with open(path, "w") as handle:
            handle.write("")
        os.chmod(path, 0o755)
        return os.path.abspath(path)

    def test_the_owners_own_layout_with_a_space_in_the_user_name(self) -> None:
        profile = os.path.join(self.home, "Users", "Atharva Bhangale")
        binary = ("resources", "native-binary")
        extensions = os.path.join(profile, ".vscode", "extensions")
        self.touch(os.path.join(extensions, "anthropic.claude-code-2.1.99-win32-x64", *binary), "claude.exe")
        new = self.touch(os.path.join(extensions, "anthropic.claude-code-2.1.322-win32-x64", *binary), "claude.exe")
        # A folder with no version at all never outranks a numbered one.
        self.touch(os.path.join(extensions, "anthropic.claude-code-win32-x64", *binary), "claude.exe")
        env = {"PATH": self.empty, "USERPROFILE": profile}
        with self.windows():
            self.assertEqual(locate_cli(env), new)

    def test_the_platform_suffix_is_not_read_as_a_version(self) -> None:
        self.assertEqual(parse_version("anthropic.claude-code-win32-x64"), ())
        self.assertEqual(parse_version("anthropic.claude-code-2.1.322-win32-arm64"), (2, 1, 322))
        self.assertEqual(parse_version("anthropic.claude-code-10.0.0-win32-x64"), (10, 0, 0))
        self.assertGreater(
            parse_version("anthropic.claude-code-10.0.0-win32-x64"),
            parse_version("anthropic.claude-code-9.9.999-win32-x64"),
        )

    def test_suffixes_exe_first_then_the_npm_shim_never_the_shell_script(self) -> None:
        npm = os.path.join(self.home, "npm")
        self.touch(npm, "claude")  # npm's sh script: not runnable on Windows
        self.touch(npm, "claude.ps1")
        shim = self.touch(npm, "claude.cmd")
        with self.windows():
            self.assertEqual(program_in_folder(npm, "claude"), shim)
            exe = self.touch(npm, "claude.exe")
            self.assertEqual(program_in_folder(npm, "claude"), exe)
        # Off Windows the suffixed files are not candidates at all.
        self.assertEqual(program_in_folder(npm, "claude"), os.path.join(os.path.abspath(npm), "claude"))

    def test_variable_names_are_matched_without_regard_to_case(self) -> None:
        local = os.path.join(self.home, "AppData", "Local")
        exe = self.touch(os.path.join(local, "Claude-Data", "claude_a", "claude-code", "2.1.322", "ff"), "claude.exe")
        env = {"Path": self.empty, "UserProfile": self.home, "LocalAppData": local}
        with self.windows():
            self.assertEqual(locate_cli(env), exe)
        native = self.touch(os.path.join(self.home, ".local", "bin"), "claude.exe")
        with self.windows():
            self.assertEqual(locate_cli(env), native)  # ~ is UserProfile

    def test_a_differently_cased_path_variable_is_the_path_that_is_searched(self) -> None:
        on_path = _make_exe(os.path.join(self.home, "onpath"), "claude")
        env = {"Path": os.path.join(self.home, "onpath"), "HOME": self.empty}
        with mock.patch.dict(os.environ, {"PATH": self.empty}):
            self.assertEqual(locate_cli(env), os.path.abspath(on_path))

    def test_a_value_containing_percent_signs_is_taken_literally(self) -> None:
        profile = os.path.join(self.home, "50%off%")
        local = os.path.join(profile, "AppData", "Local")
        exe = _make_exe(os.path.join(local, "Claude-Data", "claude_a", "claude-code", "2.1.322", "ff"), "claude")
        env = {"PATH": self.empty, "HOME": profile, "LOCALAPPDATA": local, "off": "elsewhere"}
        self.assertEqual(locate_cli(env), os.path.abspath(exe))
        npm = _make_exe(os.path.join(profile, "Roaming", "npm"), "claude")
        env["APPDATA"] = os.path.join(profile, "Roaming")
        self.assertEqual(locate_cli(env), os.path.abspath(npm))

    def test_home_is_userprofile_on_windows_and_home_elsewhere(self) -> None:
        posix_home = os.path.join(self.home, "linux")
        profile = os.path.join(self.home, "windows")
        in_home = _make_exe(os.path.join(posix_home, ".local", "bin"), "claude")
        in_profile = self.touch(os.path.join(profile, ".local", "bin"), "claude.exe")
        env = {"PATH": self.empty, "HOME": posix_home, "USERPROFILE": profile}
        # A WSL shell exporting USERPROFILE must not move ~ to the Windows home.
        self.assertEqual(locate_cli(env), os.path.abspath(in_home))
        with self.windows():
            self.assertEqual(locate_cli(env), in_profile)

    def test_without_any_home_the_tilde_folders_are_skipped(self) -> None:
        self.assertIsNone(locate_cli({"PATH": self.empty}))

    def test_a_configured_path_without_its_suffix_names_the_same_file(self) -> None:
        folder = os.path.join(self.home, "native-binary")
        exe = self.touch(folder, "claude.exe")
        _make_exe(os.path.join(self.home, ".local", "bin"), "claude")  # must not be used
        env = {"PATH": self.empty, "HOME": self.home, "USERPROFILE": self.home}
        with self.windows():
            self.assertEqual(locate_cli({**env, "CAD_CLAUDE_CODE_PATH": os.path.join(folder, "claude")}), exe)
            self.assertIsNone(locate_cli({**env, "CAD_CLAUDE_CODE_PATH": os.path.join(folder, "other")}))
            self.assertIsNone(locate_cli({**env, "CAD_CLAUDE_CODE_PATH": os.path.join(self.home, "gone", "claude")}))
        # Off Windows no suffix is invented.
        self.assertIsNone(locate_cli({**env, "CAD_CLAUDE_CODE_PATH": os.path.join(folder, "claude")}))

    def test_a_folder_or_dangling_link_named_claude_is_not_an_executable(self) -> None:
        binary = ("resources", "native-binary")
        extensions = os.path.join(self.home, ".vscode", "extensions")
        os.makedirs(os.path.join(extensions, "anthropic.claude-code-9.0.0", *binary, "claude"))
        dangling = os.path.join(extensions, "anthropic.claude-code-8.0.0", *binary)
        os.makedirs(dangling)
        try:
            os.symlink(os.path.join(self.home, "nowhere"), os.path.join(dangling, "claude"))
        except (OSError, NotImplementedError):  # pragma: no cover - Windows without the privilege
            pass
        real = _make_exe(os.path.join(extensions, "anthropic.claude-code-2.1.322", *binary), "claude")
        self.assertEqual(locate_cli({"PATH": self.empty, "HOME": self.home, "USERPROFILE": self.home}), os.path.abspath(real))


if __name__ == "__main__":
    unittest.main()
