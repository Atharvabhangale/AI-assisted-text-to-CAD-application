"""Which provider and model the application uses: the environment, plus a saved choice.

The page's Settings panel lets the owner pick the AI provider and model
without editing ``apps/api/.env`` and restarting. Every decision about that
lives here, in the AI layer; the HTTP transport only maps requests and status
codes onto :class:`SettingsStore`.

**The effective settings** are the environment exactly as before
(:func:`cad_ai.config.config_from_environment`), overlaid by a saved choice
that holds **only two things**: ``provider`` and ``model``. Nothing else can
be saved:

* **no credential, no address, no executable path.** API keys, the
  OpenAI-compatible server's base URL and key, and the CLI executables'
  paths stay in the environment (``apps/api/.env``). They are never accepted
  over HTTP and never returned. A model name that *looks* like a URL, a path
  or an API key is refused.
* **no answer mode.** ``CAD_AI_ANSWER_MODE`` / ``CAD_OPENAI_ANSWER_MODE`` are
  read by the provider modules from the process environment at construction,
  and overriding them would mean mutating ``os.environ`` for the whole process
  (or editing the providers). They therefore stay ``.env``-only, and the page
  says so.
* **no Opus.** A model containing ``opus`` is refused for the two Anthropic
  routes (API key and Claude Code subscription) -- the owner's standing rule.

**Availability is presence only.** For an API-key provider: is a key variable
set (its *name* is reported, never its value) and is the SDK installed. For a
CLI provider: can the executable be found (yes or no -- never the path). For
the OpenAI-compatible provider: is a base URL set (never the URL) and is a
model named. Nothing is contacted and nothing is run to find out.

**Persistence.** The saved choice is a small JSON file,
``<CAD_API_CACHE_ROOT>/settings.json``, written atomically (a temporary file
in the same directory, then ``os.replace``). A missing file means "use the
environment". A corrupt, unreadable or no-longer-valid file is ignored with a
warning in the server log, and the environment applies. ``.env`` is never
read or written here.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from cad_ai.config import (
    ANTIGRAVITY_PROVIDER_NAME,
    CLAUDE_CODE_PROVIDER_NAME,
    CLI_PATH_VARIABLES,
    CREDENTIAL_VARIABLES,
    DEFAULT_MODELS,
    GEMINI_PROVIDER_NAME,
    MODEL_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    OPENAI_BASE_URL_VARIABLE,
    OPENAI_COMPATIBLE_PROVIDER_NAME,
    PROVIDER_NAME,
    PROVIDER_NAMES,
    PROVIDER_VARIABLE,
    STEP_UP_MODEL,
    AiConfig,
    AiConfigurationError,
    config_from_environment,
    credential_available,
    credential_variable,
    resolve_provider,
)

logger = logging.getLogger("cad_ai.settings")

#: The file the saved choice lives in, inside the build cache root.
SETTINGS_FILE_NAME = "settings.json"

#: The saved file's format version. A file with another version is ignored.
SETTINGS_FILE_VERSION = 1

#: The only fields a saved choice has.
SETTINGS_FIELDS: Tuple[str, ...] = ("provider", "model")

#: The longest model name accepted.
MAX_MODEL_CHARACTERS = 100

#: A model name: a plain identifier. Letters, digits and ``. _ : / @ + -``
#: (Ollama tags use ``:``, OpenRouter names use ``/``), starting with a letter
#: or digit. No whitespace, no quotes, no backslash.
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$")

#: The providers on which a model containing ``opus`` is refused.
OPUS_REFUSED_PROVIDERS: Tuple[str, ...] = (PROVIDER_NAME, CLAUDE_CODE_PROVIDER_NAME)

#: How each provider is shown on the page.
PROVIDER_LABELS: Mapping[str, str] = {
    PROVIDER_NAME: "Anthropic API (API key)",
    GEMINI_PROVIDER_NAME: "Google Gemini API (API key)",
    CLAUDE_CODE_PROVIDER_NAME: "Claude Code subscription",
    ANTIGRAVITY_PROVIDER_NAME: "Google account (Antigravity CLI)",
    OPENAI_COMPATIBLE_PROVIDER_NAME: "Open-weight model (OpenAI-compatible server)",
}

#: How each provider is reached: an API key, a CLI on this machine, a server.
PROVIDER_KINDS: Mapping[str, str] = {
    PROVIDER_NAME: "api_key",
    GEMINI_PROVIDER_NAME: "api_key",
    CLAUDE_CODE_PROVIDER_NAME: "cli",
    ANTIGRAVITY_PROVIDER_NAME: "cli",
    OPENAI_COMPATIBLE_PROVIDER_NAME: "server",
}

#: A short curated list of models per provider: each provider's default
#: first, then the documented step-up. **No Opus anywhere.** The
#: OpenAI-compatible provider has none: which model a server holds is the
#: deployment's own business, so its model is free text.
MODEL_SUGGESTIONS: Mapping[str, Tuple[str, ...]] = {
    PROVIDER_NAME: (DEFAULT_MODELS[PROVIDER_NAME], STEP_UP_MODEL),
    CLAUDE_CODE_PROVIDER_NAME: (
        DEFAULT_MODELS[CLAUDE_CODE_PROVIDER_NAME],
        STEP_UP_MODEL,
    ),
    GEMINI_PROVIDER_NAME: (DEFAULT_MODELS[GEMINI_PROVIDER_NAME],),
    ANTIGRAVITY_PROVIDER_NAME: (DEFAULT_MODELS[ANTIGRAVITY_PROVIDER_NAME],),
    OPENAI_COMPATIBLE_PROVIDER_NAME: (),
}

#: The SDK module an API-key provider needs, and the install extra holding it.
#: Found with :func:`importlib.util.find_spec`; never imported here.
SDK_MODULES: Mapping[str, Tuple[str, str]] = {
    PROVIDER_NAME: ("anthropic", "ai"),
    GEMINI_PROVIDER_NAME: ("google.genai", "ai-gemini"),
}

#: The executable each CLI provider runs, by its conventional name.
CLI_NAMES: Mapping[str, str] = {
    CLAUDE_CODE_PROVIDER_NAME: "claude",
    ANTIGRAVITY_PROVIDER_NAME: "agy",
}

#: Where ``.env`` is, as the owner sees it on Windows.
ENV_FILE = r"apps\api\.env"

#: Shown on the page, verbatim.
NOTES: Tuple[str, ...] = (
    "API keys, CLI executable paths and server addresses are read only from "
    + ENV_FILE
    + " and are never sent to or accepted from this page. Change them there "
    "and restart the backend.",
    "Opus models are refused for the Anthropic API and the Claude Code "
    "subscription (the owner's rule: never Opus).",
    "A change applies to the next generation. A generation already running "
    "finishes on the previous settings.",
    "The answer mode (CAD_AI_ANSWER_MODE, CAD_OPENAI_ANSWER_MODE) is not "
    "set here; it is still read from " + ENV_FILE + ".",
    "Ready means the key, executable or server address is present. Whether "
    "it works -- a valid key, a logged-in CLI, a reachable server -- is only "
    "known when a generation runs.",
)


class SettingsReason(str, Enum):
    """Why a settings change was refused. A closed vocabulary."""

    INVALID_PROVIDER = "invalid_provider"
    INVALID_MODEL = "invalid_model"
    MODEL_NOT_ALLOWED = "model_not_allowed"
    NOT_SAVED = "not_saved"


class SettingsError(Exception):
    """A settings change that was refused, or could not be saved."""

    def __init__(
        self, reason: SettingsReason, message: str, *, field: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.field = field

    def to_payload(self) -> Dict[str, Any]:
        return {
            "reason": self.reason.value,
            "message": self.message,
            "field": self.field,
        }


@dataclass(frozen=True)
class SavedSettings:
    """The saved choice. An empty ``model`` means "the provider's default"."""

    provider: str
    model: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"provider": self.provider, "model": self.model}


def check_settings(provider: Any, model: Any) -> SavedSettings:
    """Validate a requested choice; return it normalised, or raise.

    Raises :class:`SettingsError` with :attr:`SettingsReason.INVALID_PROVIDER`,
    :attr:`~SettingsReason.INVALID_MODEL` or
    :attr:`~SettingsReason.MODEL_NOT_ALLOWED`.
    """
    if not isinstance(provider, str) or provider.strip().lower() not in PROVIDER_NAMES:
        raise SettingsError(
            SettingsReason.INVALID_PROVIDER,
            "unknown provider; choose one of " + ", ".join(PROVIDER_NAMES),
            field="provider",
        )
    provider = provider.strip().lower()
    if model is None:
        model = ""
    if not isinstance(model, str):
        raise SettingsError(
            SettingsReason.INVALID_MODEL, "the model must be text", field="model"
        )
    model = model.strip()
    if model:
        if len(model) > MAX_MODEL_CHARACTERS:
            raise SettingsError(
                SettingsReason.INVALID_MODEL,
                f"the model name is longer than {MAX_MODEL_CHARACTERS} characters",
                field="model",
            )
        lowered = model.lower()
        if (
            not MODEL_PATTERN.match(model)
            or "://" in model
            or ".." in model
            or lowered.startswith("sk-")
        ):
            raise SettingsError(
                SettingsReason.INVALID_MODEL,
                "the model must be a plain model name (letters, digits and "
                ". _ : / @ + -), not a URL, a path or a key; keys, paths and "
                "server addresses belong in " + ENV_FILE,
                field="model",
            )
        if provider in OPUS_REFUSED_PROVIDERS and "opus" in lowered:
            raise SettingsError(
                SettingsReason.MODEL_NOT_ALLOWED,
                "Opus models are not allowed (the owner's rule: never Opus). "
                "Choose " + " or ".join(MODEL_SUGGESTIONS[provider]) + ".",
                field="model",
            )
    return SavedSettings(provider=provider, model=model)


def effective_config(
    environ: Mapping[str, str], saved: Optional[SavedSettings]
) -> AiConfig:
    """The configuration a generation uses: the environment, overlaid.

    With nothing saved this is :func:`config_from_environment` unchanged. With
    a saved choice, its provider replaces ``CAD_AI_PROVIDER``; its model
    replaces ``CAD_AI_MODEL``. An empty saved model means: ``CAD_AI_MODEL``
    when the environment already chose this same provider, otherwise the
    provider's own default -- an environment model meant for one provider is
    never sent to another.

    Raises :class:`~cad_ai.config.AiConfigurationError` for an environment
    setting that cannot be used (an unparseable timeout, say).
    """
    if saved is None:
        return config_from_environment(dict(environ))
    overlay = dict(environ)
    overlay[PROVIDER_VARIABLE] = saved.provider
    if saved.model:
        overlay[MODEL_VARIABLE] = saved.model
    else:
        try:
            environment_provider: Optional[str] = resolve_provider(dict(environ))
        except AiConfigurationError:
            environment_provider = None
        if environment_provider != saved.provider:
            overlay.pop(MODEL_VARIABLE, None)
    return config_from_environment(overlay)


def _sdk_installed(module: str) -> bool:
    """Whether ``module`` can be imported, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def provider_readiness(
    provider: str, environ: Mapping[str, str]
) -> Tuple[bool, Optional[str]]:
    """Whether ``provider``'s environment is in place, and if not, why.

    Presence only. The reason names variables and executables by **name**;
    it never carries a value, a path or an address.
    """
    source = dict(environ)
    if provider in SDK_MODULES:
        names = CREDENTIAL_VARIABLES[provider]
        if credential_variable(provider, source) is None:
            alternatives = (
                f" (or {', '.join(names[1:])})" if len(names) > 1 else ""
            )
            return False, (
                f"No API key: set {names[0]}{alternatives} in {ENV_FILE} and "
                "restart the backend."
            )
        module, extra = SDK_MODULES[provider]
        if not _sdk_installed(module):
            return False, (
                f"The {module} Python package is not installed: install the "
                f"backend's \"{extra}\" extra."
            )
        return True, None
    if provider in CLI_PATH_VARIABLES:
        if credential_available(source, provider):
            return True, None
        variable = CLI_PATH_VARIABLES[provider]
        executable = CLI_NAMES[provider]
        if source.get(variable, "").strip():
            return False, (
                f"{variable} is set in {ENV_FILE} but does not name an "
                f"executable that exists. Fix it, or remove it to search PATH "
                f"for {executable}."
            )
        return False, (
            f"The {executable} executable was not found on PATH. Install it "
            f"and sign it in, or set {variable} in {ENV_FILE} to the "
            "executable, then restart the backend."
        )
    if provider == OPENAI_COMPATIBLE_PROVIDER_NAME:
        if not credential_available(source, provider):
            return False, (
                f"No server address: set {OPENAI_BASE_URL_VARIABLE} in "
                f"{ENV_FILE} (and {OPENAI_API_KEY_VARIABLE} if the server "
                "needs a key), then restart the backend."
            )
        return True, None
    return False, "unknown provider"


def environment_variables(provider: str) -> List[str]:
    """The variable **names** a provider reads from ``.env``, for the page."""
    if provider in CREDENTIAL_VARIABLES:
        return list(CREDENTIAL_VARIABLES[provider])
    if provider in CLI_PATH_VARIABLES:
        return [CLI_PATH_VARIABLES[provider]]
    if provider == OPENAI_COMPATIBLE_PROVIDER_NAME:
        return [OPENAI_BASE_URL_VARIABLE, OPENAI_API_KEY_VARIABLE, MODEL_VARIABLE]
    return []


_NO_MODEL_REASON = (
    "No model named: type the model your server serves in Model (or set "
    + MODEL_VARIABLE
    + " in "
    + ENV_FILE
    + ")."
)


class SettingsStore:
    """The saved choice, its file, and the effective settings it produces.

    ``path`` is the JSON file; ``None`` keeps the choice in memory only (used
    when the application was given a service rather than a cache root).
    ``environ`` defaults to the live process environment, read on every call.
    Thread-safe: a save and a read never interleave.
    """

    def __init__(
        self,
        path: Optional[Path],
        *,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._path = None if path is None else Path(path)
        self._environ = environ
        self._lock = threading.Lock()
        self._saved = self._load()

    @classmethod
    def in_directory(
        cls, root: Path, *, environ: Optional[Mapping[str, str]] = None
    ) -> "SettingsStore":
        return cls(Path(root) / SETTINGS_FILE_NAME, environ=environ)

    # --- state ---------------------------------------------------------------

    @property
    def saved(self) -> Optional[SavedSettings]:
        with self._lock:
            return self._saved

    def environ(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def effective_config(self) -> AiConfig:
        """What the next generation uses. May raise AiConfigurationError."""
        return effective_config(self.environ(), self.saved)

    # --- changes -------------------------------------------------------------

    def save(self, provider: Any, model: Any) -> SavedSettings:
        """Validate, persist and adopt a choice. Raises :class:`SettingsError`."""
        chosen = check_settings(provider, model)
        with self._lock:
            if self._path is not None:
                try:
                    self._write(chosen)
                except OSError:
                    logger.warning(
                        "the settings could not be written to %s", self._path
                    )
                    raise SettingsError(
                        SettingsReason.NOT_SAVED,
                        "the settings could not be saved on the server",
                    ) from None
            self._saved = chosen
        return chosen

    def reset(self) -> None:
        """Forget the saved choice: the environment applies again."""
        with self._lock:
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "the settings file %s could not be removed", self._path
                    )
                    raise SettingsError(
                        SettingsReason.NOT_SAVED,
                        "the saved settings could not be removed on the server",
                    ) from None
            self._saved = None

    # --- the page's view -------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """Everything ``GET /settings`` returns. No value of any secret."""
        environ = self.environ()
        saved = self.saved
        providers = [self._provider_entry(name, environ) for name in PROVIDER_NAMES]
        return {
            "effective": self._summary(environ, saved, "saved" if saved else "environment"),
            "environment": self._summary(environ, None, "environment"),
            "saved": None if saved is None else saved.to_dict(),
            "providers": providers,
            "notes": list(NOTES),
        }

    def _summary(
        self,
        environ: Mapping[str, str],
        saved: Optional[SavedSettings],
        source: str,
    ) -> Dict[str, Any]:
        try:
            config = effective_config(environ, saved)
        except AiConfigurationError as error:
            provider = saved.provider if saved is not None else None
            return {
                "provider": provider,
                "label": PROVIDER_LABELS.get(provider or "", None),
                "model": saved.model if saved is not None else None,
                "source": source,
                "ready": False,
                "reason_if_not": (
                    f"An AI setting in {ENV_FILE} cannot be used: {error}"
                ),
            }
        ready, reason = provider_readiness(config.provider, environ)
        if ready and not config.model:
            ready, reason = False, _NO_MODEL_REASON
        return {
            "provider": config.provider,
            "label": PROVIDER_LABELS[config.provider],
            "model": config.model,
            "source": source,
            "ready": ready,
            "reason_if_not": reason,
        }

    def _provider_entry(
        self,
        name: str,
        environ: Mapping[str, str],
    ) -> Dict[str, Any]:
        available, reason = provider_readiness(name, environ)
        # What an empty Model field means if this provider is chosen.
        try:
            empty_model = effective_config(environ, SavedSettings(name)).model
        except AiConfigurationError:
            empty_model = DEFAULT_MODELS[name]
        return {
            "name": name,
            "label": PROVIDER_LABELS[name],
            "kind": PROVIDER_KINDS[name],
            "available": available,
            "reason_if_not": reason,
            "default_model": empty_model,
            "model_required": not empty_model,
            "model_suggestions": list(MODEL_SUGGESTIONS[name]),
            "env_variables": environment_variables(name),
        }

    # --- the file ----------------------------------------------------------------

    def _load(self) -> Optional[SavedSettings]:
        """Read the saved file. Anything wrong with it means "nothing saved"."""
        if self._path is None or not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as error:
            logger.warning(
                "ignoring the settings file %s: it could not be read as JSON "
                "(%s); the environment's AI settings apply",
                self._path,
                type(error).__name__,
            )
            return None
        if (
            not isinstance(raw, dict)
            or raw.get("version") != SETTINGS_FILE_VERSION
            or set(raw) != {"version", *SETTINGS_FIELDS}
        ):
            logger.warning(
                "ignoring the settings file %s: it is not a version-%d "
                "settings file; the environment's AI settings apply",
                self._path,
                SETTINGS_FILE_VERSION,
            )
            return None
        try:
            return check_settings(raw.get("provider"), raw.get("model"))
        except SettingsError as error:
            logger.warning(
                "ignoring the settings file %s: %s; the environment's AI "
                "settings apply",
                self._path,
                error.reason.value,
            )
            return None

    def _write(self, chosen: SavedSettings) -> None:
        """Write atomically: a temporary file beside it, then a replace."""
        assert self._path is not None
        directory = self._path.parent
        payload = json.dumps(
            {"version": SETTINGS_FILE_VERSION, **chosen.to_dict()},
            indent=2,
            sort_keys=True,
        )
        handle, temporary = tempfile.mkstemp(
            prefix=".settings-", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


__all__ = [
    "MAX_MODEL_CHARACTERS",
    "MODEL_SUGGESTIONS",
    "NOTES",
    "OPUS_REFUSED_PROVIDERS",
    "PROVIDER_KINDS",
    "PROVIDER_LABELS",
    "SETTINGS_FILE_NAME",
    "SavedSettings",
    "SettingsError",
    "SettingsReason",
    "SettingsStore",
    "check_settings",
    "effective_config",
    "environment_variables",
    "provider_readiness",
]
