"""Where the AI layer's configuration comes from: the environment, explicitly.

Nothing is guessed and nothing is invented. In particular:

* **the credential is read from one named variable and never stored, logged,
  echoed or put in a result.** :class:`AiConfig` does not hold it -- the
  provider reads it at construction and hands it straight to the SDK, and
  :meth:`AiConfig.to_dict` has no field that could carry one;
* no ``.env`` file is read or written, and no credential file is created;
* an unset credential is not an error here. It makes the AI layer
  *unconfigured*, which is a state the tests rely on: it is how the suite
  proves no network call happens without configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

#: The default provider, and the one Stages 26-28 were written against.
PROVIDER_NAME = "anthropic"

#: The second provider. Two named implementations, chosen by configuration --
#: **not** a plugin registry: there is no discovery, no entry point and no
#: dynamic loading, and adding a third would mean writing a third class and
#: naming it here.
GEMINI_PROVIDER_NAME = "gemini"

#: Every provider this application implements, in preference order.
PROVIDER_NAMES: Tuple[str, ...] = (PROVIDER_NAME, GEMINI_PROVIDER_NAME)

#: The credential variable, which is the Anthropic SDK's own conventional
#: name. Read by the provider only, and never by anything else.
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"

#: Gemini's conventional credential variable. Read by its provider only.
GEMINI_API_KEY_VARIABLE = "GEMINI_API_KEY"

#: The credential each provider expects.
API_KEY_VARIABLES: Mapping[str, str] = {
    PROVIDER_NAME: API_KEY_VARIABLE,
    GEMINI_PROVIDER_NAME: GEMINI_API_KEY_VARIABLE,
}

#: Which provider to use. Unset means "whichever has a credential", resolved
#: by :func:`resolve_provider` in the order of :data:`PROVIDER_NAMES`.
PROVIDER_VARIABLE = "CAD_AI_PROVIDER"

#: Which model to use. Overridable so a deployment is not pinned to this file.
MODEL_VARIABLE = "CAD_AI_MODEL"

#: The request timeout, in seconds.
TIMEOUT_VARIABLE = "CAD_AI_TIMEOUT_SECONDS"

#: The default model per provider. Each is a name the installed SDK itself
#: lists as valid; no model identifier was invented here.
#:
#: Gemini's default was ``gemini-2.5-pro`` until it stopped being reachable.
#: Measured, not assumed: ``models.generate_content`` answers **404** for both
#: ``gemini-2.5-pro`` and ``gemini-2.5-flash`` -- *"is no longer available to
#: new users. Please update your code to use models/gemini-3.6-flash"* --
#: while ``models.list`` still lists them, so listing a model is not evidence
#: that it can be called. ``gemini-3.6-flash`` is Google's own named
#: replacement and was verified with a single probe request that returned a
#: valid V1 CAD document through the existing prompt and schema.
#:
#: This changes which model a run reaches and **nothing about what is asked of
#: it**: the prompt, the response schema, the supported subset and the
#: evaluation corpus are untouched. ``CAD_AI_MODEL`` still overrides it.
#: Anthropic's default is **Claude Haiku 4.5**, the model the product flow
#: runs on. Named once, here: every other layer -- the HTTP route, the browser,
#: the application service -- reaches it through
#: :func:`config_from_environment`, so no model identifier is written twice.
#: ``CAD_AI_MODEL`` still overrides it for a one-off run.
DEFAULT_MODELS: Mapping[str, str] = {
    PROVIDER_NAME: "claude-haiku-4-5-20251001",
    GEMINI_PROVIDER_NAME: "gemini-3.6-flash",
}

#: The default model for the default provider. Kept as its own name because
#: Stage 26 pinned it and a test asserts the installed SDK knows it.
DEFAULT_MODEL = DEFAULT_MODELS[PROVIDER_NAME]

#: Seconds. One interpretation call, so a generous but finite bound.
DEFAULT_TIMEOUT_SECONDS = 60.0


class AiConfigurationError(Exception):
    """The AI layer was configured with a value it cannot use."""


@dataclass(frozen=True)
class AiConfig:
    """How to reach the model. **Carries no credential.**"""

    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    provider: str = PROVIDER_NAME

    def __post_init__(self) -> None:
        if self.provider not in PROVIDER_NAMES:
            raise AiConfigurationError(
                f"unknown provider {self.provider!r}; this application "
                f"implements {', '.join(PROVIDER_NAMES)}"
            )
        if not isinstance(self.model, str) or not self.model.strip():
            raise AiConfigurationError("a model name is required")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(
            self.timeout_seconds, bool
        ):
            raise AiConfigurationError("the timeout must be a number of seconds")
        if not self.timeout_seconds > 0:
            raise AiConfigurationError(
                f"the timeout must be > 0 seconds; got {self.timeout_seconds}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Plain data. There is no credential field to leak."""
        return {
            "provider": self.provider,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
        }


def resolve_provider(environ: Optional[Dict[str, str]] = None) -> str:
    """Which provider to use.

    ``CAD_AI_PROVIDER`` decides when it is set. Otherwise the first provider
    in :data:`PROVIDER_NAMES` whose credential is present wins, and the
    default is returned when neither has one -- so an unconfigured
    application still reports a provider rather than failing here, and the
    provider itself refuses at construction.

    Presence is all that is read. No credential value is returned, stored or
    compared.
    """
    source = os.environ if environ is None else environ
    chosen = source.get(PROVIDER_VARIABLE, "").strip().lower()
    if chosen:
        if chosen not in PROVIDER_NAMES:
            raise AiConfigurationError(
                f"{PROVIDER_VARIABLE} must be one of "
                f"{', '.join(PROVIDER_NAMES)}; got {chosen!r}"
            )
        return chosen
    for name in PROVIDER_NAMES:
        if source.get(API_KEY_VARIABLES[name], "").strip():
            return name
    return PROVIDER_NAME


def config_from_environment(
    environ: Optional[Dict[str, str]] = None,
) -> AiConfig:
    """Read :class:`AiConfig` from the environment. Never reads a credential."""
    source = os.environ if environ is None else environ
    provider = resolve_provider(source)
    model = source.get(MODEL_VARIABLE, "").strip() or DEFAULT_MODELS[provider]
    raw_timeout = source.get(TIMEOUT_VARIABLE, "").strip()
    if not raw_timeout:
        timeout = DEFAULT_TIMEOUT_SECONDS
    else:
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise AiConfigurationError(
                f"{TIMEOUT_VARIABLE} must be a number of seconds; "
                f"got {raw_timeout!r}"
            ) from exc
    return AiConfig(model=model, timeout_seconds=timeout, provider=provider)


def credential_available(
    environ: Optional[Dict[str, str]] = None,
    provider: Optional[str] = None,
) -> bool:
    """Whether a credential is present, without reading its value.

    Returns a boolean and nothing else. Used to decide whether a live
    provider test runs or reports itself skipped. With no ``provider`` it
    answers for **any** implemented provider, so a live run is possible
    whenever either credential exists.
    """
    source = os.environ if environ is None else environ
    names = (
        PROVIDER_NAMES if provider is None else (provider,)
    )
    for name in names:
        variable = API_KEY_VARIABLES.get(name)
        if variable and source.get(variable, "").strip():
            return True
    return False


__all__ = [
    "API_KEY_VARIABLE",
    "API_KEY_VARIABLES",
    "DEFAULT_MODEL",
    "DEFAULT_MODELS",
    "DEFAULT_TIMEOUT_SECONDS",
    "GEMINI_API_KEY_VARIABLE",
    "GEMINI_PROVIDER_NAME",
    "MODEL_VARIABLE",
    "PROVIDER_NAME",
    "PROVIDER_NAMES",
    "PROVIDER_VARIABLE",
    "TIMEOUT_VARIABLE",
    "AiConfig",
    "AiConfigurationError",
    "config_from_environment",
    "credential_available",
    "resolve_provider",
]
