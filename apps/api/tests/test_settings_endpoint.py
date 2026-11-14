"""Tests for ``GET`` / ``PUT`` / ``DELETE /settings``: the Settings panel's routes.

* **the routes are thin** -- every decision is :mod:`cad_ai.settings`'; the
  routes map its answers and refusals to statuses;
* **a PUT changes the next generation** -- the provider ``POST /generate``
  builds after a PUT is the one the PUT named, and a generation already in
  flight finishes on the provider it started with;
* **no secret crosses** -- synthetic key, path and address values are set in
  the environment and never appear in a response; a body carrying a key, a
  URL, a path or an answer mode is refused before anything reads it.

**No test here contacts a provider.** The provider factory is replaced with a
recorder that hands back the suite's stub providers, or -- for the not-ready
case -- the real factory refuses at construction, as it does with nothing
configured.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, List
from unittest import mock

from fastapi.testclient import TestClient

from cad_api.app import GENERATE_PATH, SETTINGS_PATH, create_app
from cad_api.config import ApiConfig
from cad_api.generation import TextGenerator
from cad_api.status import (
    INTERNAL_STATUS,
    OK_STATUS,
    SETTINGS_STATUS,
    UNAVAILABLE_STATUS,
    UNPROCESSABLE_STATUS,
)

from cad_ai.config import AiConfig, PROVIDER_NAMES
from cad_ai.settings import SETTINGS_FILE_NAME, SettingsReason, SettingsStore
from cad_core.application_service import CadApplicationService

from provider_stubs import StubTextToCadModel

SYNTHETIC_KEY = "sk-ant-SYNTHETIC-endpoint-test-9999"
SYNTHETIC_GEMINI_KEY = "SYNTHETIC-endpoint-gemini-8888"
SYNTHETIC_OPENAI_KEY = "SYNTHETIC-endpoint-openai-7777"
SYNTHETIC_BASE_URL = "http://synthetic-endpoint-host.invalid:1234/v1"

#: Every variable the AI layer reads, cleared so the host's own configuration
#: (a real key in this container, say) cannot leak into what a test sees.
AI_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "CAD_ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "CAD_AI_PROVIDER",
    "CAD_AI_MODEL",
    "CAD_AI_TIMEOUT_SECONDS",
    "CAD_AI_MAX_ATTEMPTS",
    "CAD_AI_EFFORT",
    "CAD_CLAUDE_CODE_PATH",
    "CAD_ANTIGRAVITY_PATH",
    "CAD_OPENAI_BASE_URL",
    "CAD_OPENAI_API_KEY",
)


class Recorder:
    """Stands in for ``model_from_environment``: records each config it got."""

    def __init__(self, shape: str = "needs_clarification") -> None:
        self.configs: List[AiConfig] = []
        self._shape = shape

    def __call__(self, config: AiConfig) -> StubTextToCadModel:
        self.configs.append(config)
        return StubTextToCadModel(self._shape, name=config.provider, model=config.model)

    @property
    def providers(self) -> List[str]:
        return [config.provider for config in self.configs]


class SettingsEndpointCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.cache_root = self.tmp / "cache"
        self.cache_root.mkdir()
        self.empty_path = self.tmp / "empty-path"
        self.empty_path.mkdir()
        clean = {
            key: value for key, value in os.environ.items() if key not in AI_VARIABLES
        }
        clean["PATH"] = str(self.empty_path)
        patcher = mock.patch.dict(os.environ, clean, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(create_app(ApiConfig.for_cache_root(self.cache_root)))

    def settings_file(self) -> Path:
        return self.cache_root / SETTINGS_FILE_NAME


class TestGetSettings(SettingsEndpointCase):
    def test_the_shape(self) -> None:
        response = self.client.get(SETTINGS_PATH)
        self.assertEqual(response.status_code, OK_STATUS)
        body = response.json()
        self.assertEqual(
            set(body), {"effective", "environment", "saved", "providers", "notes"}
        )
        self.assertEqual(
            set(body["effective"]),
            {"provider", "label", "model", "source", "ready", "reason_if_not"},
        )
        self.assertEqual(body["effective"]["source"], "environment")
        self.assertIsNone(body["saved"])
        self.assertEqual([entry["name"] for entry in body["providers"]], list(PROVIDER_NAMES))
        for entry in body["providers"]:
            self.assertEqual(
                set(entry),
                {
                    "name",
                    "label",
                    "kind",
                    "available",
                    "reason_if_not",
                    "default_model",
                    "model_required",
                    "model_suggestions",
                    "env_variables",
                },
            )
        self.assertTrue(body["notes"])

    def test_a_get_writes_nothing(self) -> None:
        self.client.get(SETTINGS_PATH)
        self.assertFalse(self.settings_file().exists())


class TestPutSettings(SettingsEndpointCase):
    def test_a_valid_choice_is_saved_and_reported(self) -> None:
        response = self.client.put(
            SETTINGS_PATH, json={"provider": "claude_code", "model": "claude-sonnet-5-5"}
        )
        self.assertEqual(response.status_code, OK_STATUS)
        effective = response.json()["effective"]
        self.assertEqual(effective["provider"], "claude_code")
        self.assertEqual(effective["label"], "Claude Code subscription")
        self.assertEqual(effective["model"], "claude-sonnet-5-5")
        self.assertEqual(effective["source"], "saved")
        self.assertEqual(
            json.loads(self.settings_file().read_text(encoding="utf-8")),
            {"version": 1, "provider": "claude_code", "model": "claude-sonnet-5-5"},
        )
        # And a GET agrees, as does a new application on the same cache root.
        self.assertEqual(self.client.get(SETTINGS_PATH).json()["effective"], effective)
        fresh = TestClient(create_app(ApiConfig.for_cache_root(self.cache_root)))
        self.assertEqual(fresh.get(SETTINGS_PATH).json()["effective"], effective)

    def test_the_model_may_be_omitted(self) -> None:
        response = self.client.put(SETTINGS_PATH, json={"provider": "gemini"})
        self.assertEqual(response.status_code, OK_STATUS)
        self.assertEqual(response.json()["effective"]["model"], "gemini-3.6-flash")

    def test_opus_is_refused_with_a_clear_message(self) -> None:
        for provider in ("anthropic", "claude_code"):
            with self.subTest(provider=provider):
                response = self.client.put(
                    SETTINGS_PATH, json={"provider": provider, "model": "claude-opus-5-5"}
                )
                self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
                error = response.json()["error"]
                self.assertEqual(error["reason"], "model_not_allowed")
                self.assertEqual(error["field"], "model")
                self.assertIn("never Opus", error["message"])
        self.assertFalse(self.settings_file().exists())

    def test_an_unknown_provider_or_bad_model_is_422(self) -> None:
        for body, reason in (
            ({"provider": "bedrock", "model": ""}, "invalid_provider"),
            ({"provider": "gemini", "model": "http://evil.invalid/v1"}, "invalid_model"),
            ({"provider": "openai_compatible", "model": "C:\\bin\\agy.exe"}, "invalid_model"),
        ):
            with self.subTest(body=body):
                response = self.client.put(SETTINGS_PATH, json=body)
                self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
                self.assertEqual(response.json()["error"]["reason"], reason)
        self.assertFalse(self.settings_file().exists())

    def test_no_key_url_path_or_answer_mode_is_accepted(self) -> None:
        for extra in (
            {"api_key": SYNTHETIC_KEY},
            {"ANTHROPIC_API_KEY": SYNTHETIC_KEY},
            {"token": SYNTHETIC_KEY},
            {"base_url": SYNTHETIC_BASE_URL},
            {"CAD_OPENAI_BASE_URL": SYNTHETIC_BASE_URL},
            {"cli_path": "/usr/bin/claude"},
            {"CAD_CLAUDE_CODE_PATH": "/usr/bin/claude"},
            {"answer_mode": "schema"},
        ):
            with self.subTest(extra=list(extra)):
                response = self.client.put(
                    SETTINGS_PATH, json={"provider": "gemini", "model": "", **extra}
                )
                self.assertEqual(response.status_code, UNPROCESSABLE_STATUS)
                text = response.text
                self.assertNotIn(SYNTHETIC_KEY, text)
                self.assertNotIn(SYNTHETIC_BASE_URL, text)
        self.assertFalse(self.settings_file().exists())

    def test_a_malformed_body_is_422(self) -> None:
        for body in ({}, {"provider": ""}, {"provider": 3}, {"provider": "gemini", "model": 5}):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.put(SETTINGS_PATH, json=body).status_code,
                    UNPROCESSABLE_STATUS,
                )

    def test_an_unavailable_provider_is_saved_and_reported_not_ready(self) -> None:
        response = self.client.put(
            SETTINGS_PATH, json={"provider": "openai_compatible", "model": "llama3.3"}
        )
        self.assertEqual(response.status_code, OK_STATUS)
        effective = response.json()["effective"]
        self.assertEqual(effective["provider"], "openai_compatible")
        self.assertFalse(effective["ready"])
        self.assertIn("CAD_OPENAI_BASE_URL", effective["reason_if_not"])
        # The next generation fails the way an unconfigured provider always
        # has: the real factory refuses at construction; nothing is contacted.
        generated = self.client.post(GENERATE_PATH, json={"text": "a plate"})
        self.assertEqual(generated.status_code, UNAVAILABLE_STATUS)
        self.assertEqual(generated.json()["outcome"], "model_error")
        self.assertEqual(generated.json()["error_kind"], "not_configured")

    def test_a_failed_write_is_500(self) -> None:
        with mock.patch("cad_ai.settings.os.replace", side_effect=OSError("disk")):
            with self.assertLogs("cad_ai.settings", "WARNING"):
                response = self.client.put(SETTINGS_PATH, json={"provider": "gemini"})
        self.assertEqual(response.status_code, INTERNAL_STATUS)
        self.assertEqual(response.json()["error"]["reason"], "not_saved")
        self.assertNotIn(str(self.tmp), response.text)

    def test_every_settings_reason_has_a_status(self) -> None:
        self.assertEqual(set(SETTINGS_STATUS), set(SettingsReason))


class TestDeleteSettings(SettingsEndpointCase):
    def test_reset_returns_to_the_environment(self) -> None:
        self.client.put(SETTINGS_PATH, json={"provider": "gemini"})
        self.assertTrue(self.settings_file().exists())
        response = self.client.delete(SETTINGS_PATH)
        self.assertEqual(response.status_code, OK_STATUS)
        body = response.json()
        self.assertIsNone(body["saved"])
        self.assertEqual(body["effective"]["source"], "environment")
        self.assertEqual(body["effective"]["provider"], "anthropic")
        self.assertFalse(self.settings_file().exists())
        # Twice is fine.
        self.assertEqual(self.client.delete(SETTINGS_PATH).status_code, OK_STATUS)

    def test_a_corrupt_file_is_ignored_at_startup(self) -> None:
        self.settings_file().write_text("{broken", encoding="utf-8")
        with self.assertLogs("cad_ai.settings", "WARNING"):
            client = TestClient(create_app(ApiConfig.for_cache_root(self.cache_root)))
        body = client.get(SETTINGS_PATH).json()
        self.assertEqual(body["effective"]["source"], "environment")


class TestNoSecretCrosses(SettingsEndpointCase):
    def test_get_put_and_delete_never_return_a_key_path_or_url(self) -> None:
        bin_dir = self.tmp / "synthetic-endpoint-bin"
        bin_dir.mkdir()
        claude = bin_dir / ("claude-x" + (".exe" if sys.platform == "win32" else ""))
        claude.write_text("not a program\n", encoding="utf-8")
        claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
        os.environ.update(
            {
                "ANTHROPIC_API_KEY": SYNTHETIC_KEY,
                "GEMINI_API_KEY": SYNTHETIC_GEMINI_KEY,
                "CAD_OPENAI_API_KEY": SYNTHETIC_OPENAI_KEY,
                "CAD_OPENAI_BASE_URL": SYNTHETIC_BASE_URL,
                "CAD_CLAUDE_CODE_PATH": str(claude),
                "CAD_ANTIGRAVITY_PATH": str(bin_dir / "agy-missing"),
            }
        )
        responses = [
            self.client.get(SETTINGS_PATH),
            self.client.put(SETTINGS_PATH, json={"provider": "claude_code"}),
            self.client.put(
                SETTINGS_PATH, json={"provider": "openai_compatible", "model": "llama3"}
            ),
            self.client.put(SETTINGS_PATH, json={"provider": "anthropic", "model": "opus"}),
            self.client.delete(SETTINGS_PATH),
        ]
        for response in responses:
            text = response.text
            for value in (
                SYNTHETIC_KEY,
                SYNTHETIC_GEMINI_KEY,
                SYNTHETIC_OPENAI_KEY,
                SYNTHETIC_BASE_URL,
                "synthetic-endpoint-host",
                "synthetic-endpoint-bin",
                str(self.tmp),
            ):
                self.assertNotIn(value, text)
        providers = {entry["name"]: entry for entry in responses[0].json()["providers"]}
        self.assertTrue(providers["claude_code"]["available"])
        self.assertTrue(providers["openai_compatible"]["available"])
        self.assertFalse(providers["antigravity"]["available"])


class TestPutChangesTheNextGeneration(SettingsEndpointCase):
    def test_the_next_generate_builds_the_provider_the_put_named(self) -> None:
        recorder = Recorder()
        with mock.patch("cad_api.generation.model_from_environment", recorder):
            first = self.client.post(GENERATE_PATH, json={"text": "a plate"})
            again = self.client.post(GENERATE_PATH, json={"text": "a plate"})
            self.client.put(SETTINGS_PATH, json={"provider": "gemini"})
            after_put = self.client.post(GENERATE_PATH, json={"text": "a plate"})
            self.client.put(
                SETTINGS_PATH,
                json={"provider": "claude_code", "model": "claude-sonnet-5-5"},
            )
            after_second = self.client.post(GENERATE_PATH, json={"text": "a plate"})
            self.client.delete(SETTINGS_PATH)
            after_reset = self.client.post(GENERATE_PATH, json={"text": "a plate"})
        for response in (first, again, after_put, after_second, after_reset):
            self.assertEqual(response.status_code, OK_STATUS)
            self.assertEqual(response.json()["outcome"], "needs_clarification")
        # Built once, cached for the second request, rebuilt after each change.
        self.assertEqual(
            recorder.providers, ["anthropic", "gemini", "claude_code", "anthropic"]
        )
        self.assertEqual(recorder.configs[1].model, "gemini-3.6-flash")
        self.assertEqual(recorder.configs[2].model, "claude-sonnet-5-5")
        self.assertEqual(after_put.json()["metadata"]["provider"], "gemini")
        self.assertEqual(after_second.json()["metadata"]["model"], "claude-sonnet-5-5")

    def test_a_refused_put_keeps_the_built_service(self) -> None:
        recorder = Recorder()
        with mock.patch("cad_api.generation.model_from_environment", recorder):
            self.client.post(GENERATE_PATH, json={"text": "a plate"})
            self.client.put(SETTINGS_PATH, json={"provider": "anthropic", "model": "opus-x"})
            self.client.post(GENERATE_PATH, json={"text": "a plate"})
        self.assertEqual(recorder.providers, ["anthropic"])

    def test_the_environment_settings_still_apply_underneath(self) -> None:
        os.environ["CAD_AI_MAX_ATTEMPTS"] = "2"
        recorder = Recorder()
        with mock.patch("cad_api.generation.model_from_environment", recorder):
            self.client.put(SETTINGS_PATH, json={"provider": "gemini"})
            self.client.post(GENERATE_PATH, json={"text": "a plate"})
        self.assertEqual(recorder.configs[0].max_attempts, 2)


class TestInFlightGeneration(unittest.TestCase):
    """A generation already running finishes on the provider it started with."""

    def test_reconfigure_does_not_touch_a_running_generation(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = CadApplicationService.local(Path(directory.name))
        store = SettingsStore(None, environ={})
        started = threading.Event()
        release = threading.Event()

        class Blocking(StubTextToCadModel):
            def generate(self, request: Any) -> Any:
                started.set()
                release.wait(10)
                return super().generate(request)

        built: List[str] = []

        def factory(config: AiConfig) -> StubTextToCadModel:
            built.append(config.provider)
            if config.provider == "anthropic":
                return Blocking("needs_clarification", name="anthropic")
            return StubTextToCadModel("needs_clarification", name=config.provider)

        generator = TextGenerator(service, settings=store)
        results: List[Any] = []
        with mock.patch("cad_api.generation.model_from_environment", factory):
            worker = threading.Thread(
                target=lambda: results.append(generator.generate("a plate"))
            )
            worker.start()
            self.assertTrue(started.wait(10))
            store.save("gemini", "")
            generator.reconfigure()
            # The next generation builds the new provider while the old one
            # is still running.
            second = generator.generate("a plate")
            release.set()
            worker.join(10)
        self.assertEqual(built, ["anthropic", "gemini"])
        self.assertEqual(results[0].metadata.provider, "anthropic")
        self.assertEqual(second.metadata.provider, "gemini")

    def test_without_a_store_the_environment_alone_decides(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = CadApplicationService.local(Path(directory.name))
        recorder = Recorder()
        with mock.patch.dict(
            os.environ, {"CAD_AI_PROVIDER": "gemini"}, clear=False
        ), mock.patch("cad_api.generation.model_from_environment", recorder):
            TextGenerator(service).generate("a plate")
        self.assertEqual(recorder.providers, ["gemini"])


class TestAppWithAService(unittest.TestCase):
    def test_settings_are_memory_only_when_given_a_service(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        client = TestClient(create_app(service=CadApplicationService.local(root)))
        self.assertEqual(
            client.put(SETTINGS_PATH, json={"provider": "gemini"}).status_code, OK_STATUS
        )
        self.assertFalse((root / SETTINGS_FILE_NAME).exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
