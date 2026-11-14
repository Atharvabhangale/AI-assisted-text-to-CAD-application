"""Tests for :mod:`cad_ai.settings`: the provider/model choice behind the Settings panel.

What these tests are about:

* **validation** -- only a known provider and a plain model name are accepted;
  a URL, a path or a key-shaped string is refused as a model; **Opus is
  refused** on the two Anthropic routes (the owner's standing rule);
* **the overlay** -- the effective configuration is the environment, with the
  saved provider and model on top, and an environment model meant for one
  provider is never sent to another;
* **persistence** -- an atomic JSON file; a corrupt, foreign or no-longer-valid
  file is ignored with a warning and the environment applies;
* **availability is presence only** -- synthetic key, path and address values
  are set, and none of them appears anywhere in what the store describes.

Nothing here contacts a provider, runs a CLI or reads ``.env``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict
from unittest import mock

from cad_ai.config import (
    DEFAULT_MODELS,
    PROVIDER_NAMES,
    STEP_UP_MODEL,
    AiConfigurationError,
)
from cad_ai.settings import (
    MAX_MODEL_CHARACTERS,
    MODEL_SUGGESTIONS,
    OPUS_REFUSED_PROVIDERS,
    PROVIDER_LABELS,
    SETTINGS_FILE_NAME,
    SavedSettings,
    SettingsError,
    SettingsReason,
    SettingsStore,
    check_settings,
    effective_config,
)

#: Synthetic values. Each is distinctive, so finding it in a response body
#: can only mean it leaked. None of them is a real credential.
SYNTHETIC_ANTHROPIC_KEY = "sk-ant-SYNTHETIC-settings-test-0000"
SYNTHETIC_FALLBACK_KEY = "SYNTHETIC-fallback-key-1111"
SYNTHETIC_GEMINI_KEY = "SYNTHETIC-gemini-key-2222"
SYNTHETIC_OPENAI_KEY = "SYNTHETIC-openai-key-3333"
SYNTHETIC_BASE_URL = "http://synthetic-host.invalid:9999/v1"


def executable(directory: Path, name: str) -> Path:
    """A file that ``shutil.which`` accepts as an executable. Never run."""
    path = directory / (name + (".exe" if sys.platform == "win32" else ""))
    path.write_text("not a real program\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class SettingsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.empty_path = self.tmp / "empty-path"
        self.empty_path.mkdir()

    def environ(self, **values: str) -> Dict[str, str]:
        """An environment with no provider configured, plus ``values``."""
        base = {"PATH": str(self.empty_path)}
        base.update(values)
        return base

    def store(self, **values: str) -> SettingsStore:
        return SettingsStore.in_directory(self.tmp, environ=self.environ(**values))


# --- validation ---------------------------------------------------------------


class TestCheckSettings(unittest.TestCase):
    def test_every_provider_is_accepted_with_an_empty_model(self) -> None:
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                self.assertEqual(check_settings(name, ""), SavedSettings(name, ""))
                self.assertEqual(check_settings(name, None), SavedSettings(name, ""))

    def test_the_provider_is_normalised(self) -> None:
        self.assertEqual(check_settings(" Gemini ", "").provider, "gemini")

    def test_an_unknown_provider_is_refused(self) -> None:
        for bad in ("", "openai", "bedrock", "claude", None, 3, ["gemini"]):
            with self.subTest(provider=bad), self.assertRaises(SettingsError) as raised:
                check_settings(bad, "")
            self.assertIs(raised.exception.reason, SettingsReason.INVALID_PROVIDER)
            self.assertEqual(raised.exception.field, "provider")

    def test_plain_model_names_are_accepted(self) -> None:
        for model in (
            "claude-haiku-5-5",
            "gemini-3.6-flash",
            "qwen2.5-coder:32b",
            "meta-llama/llama-3.3-70b-instruct",
            "model@2026+exp",
        ):
            with self.subTest(model=model):
                self.assertEqual(check_settings("openai_compatible", model).model, model)

    def test_the_model_is_trimmed(self) -> None:
        self.assertEqual(
            check_settings("anthropic", "  claude-sonnet-5-5 ").model,
            "claude-sonnet-5-5",
        )

    def test_a_url_path_key_or_odd_text_is_not_a_model(self) -> None:
        for bad in (
            "http://localhost:11434/v1",
            "https://example.invalid",
            "C:\\tools\\claude.exe",
            "/usr/local/bin/claude",
            "../etc/passwd",
            "./claude",
            "sk-ant-abc123",
            "SK-proj-abc",
            "two words",
            "quote\"d",
            "semi;colon",
            "x" * (MAX_MODEL_CHARACTERS + 1),
            "-leading-dash",
        ):
            with self.subTest(model=bad), self.assertRaises(SettingsError) as raised:
                check_settings("openai_compatible", bad)
            self.assertIs(raised.exception.reason, SettingsReason.INVALID_MODEL)
            self.assertEqual(raised.exception.field, "model")
            # The refusal never echoes what was sent.
            self.assertNotIn(bad, raised.exception.message)

    def test_a_non_text_model_is_refused(self) -> None:
        with self.assertRaises(SettingsError) as raised:
            check_settings("gemini", 5)
        self.assertIs(raised.exception.reason, SettingsReason.INVALID_MODEL)

    def test_opus_is_refused_on_both_anthropic_routes(self) -> None:
        self.assertEqual(set(OPUS_REFUSED_PROVIDERS), {"anthropic", "claude_code"})
        for provider in OPUS_REFUSED_PROVIDERS:
            for model in ("claude-opus-5-5", "Claude-OPUS-4", "opus", "my-opus-alias"):
                with self.subTest(provider=provider, model=model):
                    with self.assertRaises(SettingsError) as raised:
                        check_settings(provider, model)
                    self.assertIs(
                        raised.exception.reason, SettingsReason.MODEL_NOT_ALLOWED
                    )
                    self.assertIn("never Opus", raised.exception.message)

    def test_no_suggestion_anywhere_is_opus(self) -> None:
        for provider, models in MODEL_SUGGESTIONS.items():
            for model in models:
                self.assertNotIn("opus", model.lower(), msg=provider)
                # And every suggestion passes the provider's own check.
                check_settings(provider, model)

    def test_the_suggestions_are_the_curated_lists(self) -> None:
        self.assertEqual(
            MODEL_SUGGESTIONS["anthropic"], ("claude-haiku-5-5", STEP_UP_MODEL)
        )
        self.assertEqual(MODEL_SUGGESTIONS["claude_code"], MODEL_SUGGESTIONS["anthropic"])
        self.assertEqual(MODEL_SUGGESTIONS["gemini"], (DEFAULT_MODELS["gemini"],))
        self.assertEqual(
            MODEL_SUGGESTIONS["antigravity"], (DEFAULT_MODELS["antigravity"],)
        )
        self.assertEqual(MODEL_SUGGESTIONS["openai_compatible"], ())

    def test_every_provider_has_a_label_and_suggestions(self) -> None:
        self.assertEqual(set(PROVIDER_LABELS), set(PROVIDER_NAMES))
        self.assertEqual(set(MODEL_SUGGESTIONS), set(PROVIDER_NAMES))
        self.assertEqual(PROVIDER_LABELS["claude_code"], "Claude Code subscription")


# --- the overlay ----------------------------------------------------------------


class TestEffectiveConfig(unittest.TestCase):
    def test_nothing_saved_is_the_environment_unchanged(self) -> None:
        config = effective_config({}, None)
        self.assertEqual((config.provider, config.model), ("anthropic", "claude-haiku-5-5"))
        config = effective_config(
            {"CAD_AI_PROVIDER": "gemini", "CAD_AI_MODEL": "gemini-x"}, None
        )
        self.assertEqual((config.provider, config.model), ("gemini", "gemini-x"))

    def test_a_saved_model_wins(self) -> None:
        config = effective_config(
            {"CAD_AI_PROVIDER": "anthropic", "CAD_AI_MODEL": "claude-haiku-5-5"},
            SavedSettings("claude_code", "claude-sonnet-5-5"),
        )
        self.assertEqual((config.provider, config.model), ("claude_code", "claude-sonnet-5-5"))

    def test_an_empty_saved_model_uses_the_environment_for_the_same_provider(
        self,
    ) -> None:
        config = effective_config(
            {"CAD_AI_PROVIDER": "anthropic", "CAD_AI_MODEL": "claude-sonnet-5-5"},
            SavedSettings("anthropic", ""),
        )
        self.assertEqual(config.model, "claude-sonnet-5-5")

    def test_an_environment_model_never_crosses_to_another_provider(self) -> None:
        config = effective_config(
            {"CAD_AI_PROVIDER": "anthropic", "CAD_AI_MODEL": "claude-sonnet-5-5"},
            SavedSettings("gemini", ""),
        )
        self.assertEqual((config.provider, config.model), ("gemini", DEFAULT_MODELS["gemini"]))

    def test_the_other_environment_settings_are_kept(self) -> None:
        config = effective_config(
            {"CAD_AI_TIMEOUT_SECONDS": "12", "CAD_AI_MAX_ATTEMPTS": "2"},
            SavedSettings("gemini", ""),
        )
        self.assertEqual(config.timeout_seconds, 12.0)
        self.assertEqual(config.max_attempts, 2)

    def test_a_bad_environment_provider_does_not_block_a_saved_one(self) -> None:
        config = effective_config(
            {"CAD_AI_PROVIDER": "nonsense"}, SavedSettings("gemini", "")
        )
        self.assertEqual(config.provider, "gemini")
        with self.assertRaises(AiConfigurationError):
            effective_config({"CAD_AI_PROVIDER": "nonsense"}, None)

    def test_the_environment_mapping_is_not_modified(self) -> None:
        environ = {"CAD_AI_PROVIDER": "anthropic", "CAD_AI_MODEL": "claude-sonnet-5-5"}
        before = dict(environ)
        effective_config(environ, SavedSettings("gemini", ""))
        self.assertEqual(environ, before)


# --- persistence ------------------------------------------------------------------


class TestPersistence(SettingsTestCase):
    def test_nothing_saved_means_the_environment(self) -> None:
        store = self.store()
        self.assertIsNone(store.saved)
        self.assertFalse((self.tmp / SETTINGS_FILE_NAME).exists())
        self.assertEqual(store.describe()["effective"]["source"], "environment")

    def test_a_save_is_written_and_read_back(self) -> None:
        self.store().save("gemini", "gemini-3.6-flash")
        path = self.tmp / SETTINGS_FILE_NAME
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"version": 1, "provider": "gemini", "model": "gemini-3.6-flash"},
        )
        reloaded = self.store()
        self.assertEqual(reloaded.saved, SavedSettings("gemini", "gemini-3.6-flash"))
        self.assertEqual(reloaded.describe()["effective"]["source"], "saved")
        self.assertEqual(reloaded.effective_config().provider, "gemini")

    def test_the_write_leaves_no_temporary_file(self) -> None:
        store = self.store()
        store.save("gemini", "")
        store.save("anthropic", "claude-sonnet-5-5")
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["empty-path", SETTINGS_FILE_NAME])

    def test_a_refused_save_changes_nothing(self) -> None:
        store = self.store()
        store.save("gemini", "")
        with self.assertRaises(SettingsError):
            store.save("anthropic", "claude-opus-5-5")
        self.assertEqual(store.saved, SavedSettings("gemini", ""))
        self.assertEqual(self.store().saved, SavedSettings("gemini", ""))

    def test_a_failed_write_is_reported_and_keeps_the_old_choice(self) -> None:
        store = self.store()
        store.save("gemini", "")
        with mock.patch("cad_ai.settings.os.replace", side_effect=OSError("disk full")):
            with self.assertLogs("cad_ai.settings", "WARNING"):
                with self.assertRaises(SettingsError) as raised:
                    store.save("anthropic", "")
        self.assertIs(raised.exception.reason, SettingsReason.NOT_SAVED)
        self.assertEqual(store.saved, SavedSettings("gemini", ""))
        self.assertEqual(self.store().saved, SavedSettings("gemini", ""))
        self.assertEqual(
            sorted(p.name for p in self.tmp.iterdir()), ["empty-path", SETTINGS_FILE_NAME]
        )

    def test_reset_removes_the_file(self) -> None:
        store = self.store()
        store.save("claude_code", "")
        store.reset()
        self.assertIsNone(store.saved)
        self.assertFalse((self.tmp / SETTINGS_FILE_NAME).exists())
        store.reset()  # resetting twice is fine

    def test_a_corrupt_or_foreign_file_is_ignored_with_a_warning(self) -> None:
        path = self.tmp / SETTINGS_FILE_NAME
        for content in (
            "{not json",
            "[]",
            "null",
            json.dumps({"provider": "gemini", "model": ""}),  # no version
            json.dumps({"version": 2, "provider": "gemini", "model": ""}),
            json.dumps({"version": 1, "provider": "gemini", "model": "", "api_key": "x"}),
            json.dumps({"version": 1, "provider": "nope", "model": ""}),
            json.dumps({"version": 1, "provider": "anthropic", "model": "claude-opus-5"}),
            json.dumps({"version": 1, "provider": "gemini", "model": "http://x"}),
        ):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                with self.assertLogs("cad_ai.settings", "WARNING") as logs:
                    store = self.store()
                self.assertIsNone(store.saved)
                self.assertEqual(store.describe()["effective"]["source"], "environment")
                self.assertEqual(store.effective_config().provider, "anthropic")
                # The file's contents are never copied into the log.
                self.assertNotIn("api_key", "\n".join(logs.output))
                self.assertNotIn("opus", "\n".join(logs.output))

    def test_undecodable_bytes_are_ignored_too(self) -> None:
        (self.tmp / SETTINGS_FILE_NAME).write_bytes(b"\xff\xfe\x00garbage")
        with self.assertLogs("cad_ai.settings", "WARNING"):
            self.assertIsNone(self.store().saved)

    def test_a_memory_only_store_writes_nothing(self) -> None:
        store = SettingsStore(None, environ=self.environ())
        store.save("gemini", "")
        self.assertEqual(store.saved, SavedSettings("gemini", ""))
        store.reset()
        self.assertIsNone(store.saved)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["empty-path"])

    def test_dot_env_is_never_written(self) -> None:
        env_file = self.tmp / ".env"
        env_file.write_text("CAD_AI_PROVIDER=anthropic\n", encoding="utf-8")
        store = self.store()
        store.save("gemini", "")
        store.reset()
        self.assertEqual(env_file.read_text(encoding="utf-8"), "CAD_AI_PROVIDER=anthropic\n")


# --- availability -------------------------------------------------------------------


class TestAvailability(SettingsTestCase):
    def providers(self, store: SettingsStore) -> Dict[str, Dict[str, object]]:
        return {entry["name"]: entry for entry in store.describe()["providers"]}

    def configured_environment(self) -> Dict[str, str]:
        bin_dir = self.tmp / "synthetic-secret-bin-dir"
        bin_dir.mkdir()
        claude = executable(bin_dir, "claude-synthetic")
        agy = executable(bin_dir, "agy-synthetic")
        return self.environ(
            ANTHROPIC_API_KEY=SYNTHETIC_ANTHROPIC_KEY,
            CAD_ANTHROPIC_API_KEY=SYNTHETIC_FALLBACK_KEY,
            GEMINI_API_KEY=SYNTHETIC_GEMINI_KEY,
            CAD_CLAUDE_CODE_PATH=str(claude),
            CAD_ANTIGRAVITY_PATH=str(agy),
            CAD_OPENAI_BASE_URL=SYNTHETIC_BASE_URL,
            CAD_OPENAI_API_KEY=SYNTHETIC_OPENAI_KEY,
        )

    def test_nothing_configured_means_nothing_ready_and_says_why(self) -> None:
        store = self.store()
        with mock.patch("cad_ai.settings._sdk_installed", return_value=True):
            entries = self.providers(store)
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                self.assertFalse(entries[name]["available"])
                self.assertTrue(entries[name]["reason_if_not"])
        self.assertIn("ANTHROPIC_API_KEY", entries["anthropic"]["reason_if_not"])
        self.assertIn("GEMINI_API_KEY", entries["gemini"]["reason_if_not"])
        self.assertIn("CAD_CLAUDE_CODE_PATH", entries["claude_code"]["reason_if_not"])
        self.assertIn("CAD_ANTIGRAVITY_PATH", entries["antigravity"]["reason_if_not"])
        self.assertIn(
            "CAD_OPENAI_BASE_URL", entries["openai_compatible"]["reason_if_not"]
        )
        effective = store.describe()["effective"]
        self.assertFalse(effective["ready"])
        self.assertIn("ANTHROPIC_API_KEY", effective["reason_if_not"])

    def test_everything_configured_is_ready_and_no_value_leaks(self) -> None:
        environ = self.configured_environment()
        store = SettingsStore.in_directory(self.tmp, environ=environ)
        with mock.patch("cad_ai.settings._sdk_installed", return_value=True):
            described = store.describe()
            store.save("openai_compatible", "llama3.3")
            after_save = store.describe()
        entries = {entry["name"]: entry for entry in described["providers"]}
        for name in PROVIDER_NAMES:
            with self.subTest(provider=name):
                self.assertTrue(entries[name]["available"], entries[name]["reason_if_not"])
        self.assertTrue(after_save["effective"]["ready"])
        for body in (described, after_save):
            text = json.dumps(body)
            for value in (
                SYNTHETIC_ANTHROPIC_KEY,
                SYNTHETIC_FALLBACK_KEY,
                SYNTHETIC_GEMINI_KEY,
                SYNTHETIC_OPENAI_KEY,
                SYNTHETIC_BASE_URL,
                "synthetic-host",
                "synthetic-secret-bin-dir",
                "claude-synthetic",
                "agy-synthetic",
                str(self.tmp),
            ):
                self.assertNotIn(value, text)
        # And nothing was written but the choice itself.
        saved = json.loads((self.tmp / SETTINGS_FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"version", "provider", "model"})

    def test_a_configured_path_that_names_nothing_is_reported_by_name_only(
        self,
    ) -> None:
        missing = str(self.tmp / "synthetic-missing-dir" / "claude.exe")
        store = self.store(CAD_CLAUDE_CODE_PATH=missing)
        entry = self.providers(store)["claude_code"]
        self.assertFalse(entry["available"])
        self.assertIn("CAD_CLAUDE_CODE_PATH is set", entry["reason_if_not"])
        self.assertNotIn("synthetic-missing-dir", json.dumps(store.describe()))

    def test_a_cli_found_on_path_is_ready(self) -> None:
        executable(self.empty_path, "claude")
        entry = self.providers(self.store())["claude_code"]
        self.assertTrue(entry["available"])
        self.assertIsNone(entry["reason_if_not"])

    def test_a_missing_sdk_is_reported(self) -> None:
        store = self.store(GEMINI_API_KEY=SYNTHETIC_GEMINI_KEY)
        with mock.patch("cad_ai.settings._sdk_installed", return_value=False):
            entry = self.providers(store)["gemini"]
        self.assertFalse(entry["available"])
        self.assertIn("ai-gemini", entry["reason_if_not"])

    def test_the_open_weight_provider_needs_a_model(self) -> None:
        store = self.store(CAD_OPENAI_BASE_URL=SYNTHETIC_BASE_URL)
        entry = self.providers(store)["openai_compatible"]
        self.assertTrue(entry["available"])
        self.assertTrue(entry["model_required"])
        self.assertEqual(entry["model_suggestions"], [])
        store.save("openai_compatible", "")
        effective = store.describe()["effective"]
        self.assertFalse(effective["ready"])
        self.assertIn("No model named", effective["reason_if_not"])
        store.save("openai_compatible", "qwen2.5-coder:32b")
        self.assertTrue(store.describe()["effective"]["ready"])

    def test_the_variable_names_are_listed_for_the_page(self) -> None:
        entries = self.providers(self.store())
        self.assertEqual(
            entries["anthropic"]["env_variables"],
            ["ANTHROPIC_API_KEY", "CAD_ANTHROPIC_API_KEY"],
        )
        self.assertEqual(entries["claude_code"]["env_variables"], ["CAD_CLAUDE_CODE_PATH"])
        self.assertEqual(entries["antigravity"]["env_variables"], ["CAD_ANTIGRAVITY_PATH"])
        self.assertIn("CAD_OPENAI_BASE_URL", entries["openai_compatible"]["env_variables"])

    def test_the_default_model_is_what_an_empty_field_means(self) -> None:
        entries = self.providers(self.store())
        for name in ("anthropic", "gemini", "claude_code", "antigravity"):
            self.assertEqual(entries[name]["default_model"], DEFAULT_MODELS[name])
        self.assertEqual(entries["openai_compatible"]["default_model"], "")

    def test_the_notes_say_where_secrets_live_and_that_answer_mode_stays(
        self,
    ) -> None:
        notes = " ".join(self.store().describe()["notes"])
        self.assertIn(r"apps\api\.env", notes)
        self.assertIn("never sent to or accepted from this page", notes)
        self.assertIn("CAD_AI_ANSWER_MODE", notes)
        self.assertIn("never Opus", notes)

    def test_describe_reads_the_environment_live(self) -> None:
        environ: Dict[str, str] = self.environ()
        store = SettingsStore.in_directory(self.tmp, environ=environ)
        with mock.patch("cad_ai.settings._sdk_installed", return_value=True):
            self.assertFalse(self.providers(store)["gemini"]["available"])
            environ["GEMINI_API_KEY"] = SYNTHETIC_GEMINI_KEY
            self.assertTrue(self.providers(store)["gemini"]["available"])

    def test_a_bad_environment_setting_is_reported_not_raised(self) -> None:
        store = self.store(CAD_AI_TIMEOUT_SECONDS="soon")
        effective = store.describe()["effective"]
        self.assertFalse(effective["ready"])
        self.assertIn("CAD_AI_TIMEOUT_SECONDS", effective["reason_if_not"])

    def test_the_settings_module_reads_no_credential_value(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "src" / "cad_ai" / "settings.py"
        ).read_text(encoding="utf-8")
        # Presence goes through config's helpers; the module itself indexes no
        # credential variable and imports no provider SDK.
        for forbidden in (
            "os.environ[",
            "import anthropic",
            "import google",
            "getenv(",
        ):
            self.assertFalse(forbidden in source, msg=forbidden)
        # The only file it opens is its own, through the temporary-file handle.
        self.assertFalse("open(" in source.replace("fdopen(", ""), msg="open(")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
