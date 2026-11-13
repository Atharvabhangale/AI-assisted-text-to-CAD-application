"""Where the AI layer's configuration comes from: the environment, explicitly.

Nothing is guessed and nothing is invented. In particular:

* **the credential is read from a named variable and never stored, logged,
  echoed or put in a result.** :class:`AiConfig` does not hold it -- the
  provider reads it at construction and hands it straight to the SDK, and
  :meth:`AiConfig.to_dict` has no field that could carry one. This module
  only ever answers *which name* holds one (:func:`credential_variable`);
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

#: A second name the same credential may arrive under. Some hosted
#: environments reserve :data:`API_KEY_VARIABLE` for their own use and strip
#: it from the processes they start, so a deployment there has no way to set
#: it; this name is what such an environment can set instead. It is a
#: fallback, never a preference: :data:`API_KEY_VARIABLE` wins when both are
#: present.
FALLBACK_API_KEY_VARIABLE = "CAD_ANTHROPIC_API_KEY"

#: Gemini's conventional credential variable. Read by its provider only.
GEMINI_API_KEY_VARIABLE = "GEMINI_API_KEY"

#: The credential each provider expects, under its primary name.
API_KEY_VARIABLES: Mapping[str, str] = {
    PROVIDER_NAME: API_KEY_VARIABLE,
    GEMINI_PROVIDER_NAME: GEMINI_API_KEY_VARIABLE,
}

#: Every name each provider's credential may arrive under, in lookup order.
#: The one mapping :func:`credential_variable` reads, so the presence check
#: here and the provider's own read cannot disagree about where to look.
CREDENTIAL_VARIABLES: Mapping[str, Tuple[str, ...]] = {
    PROVIDER_NAME: (API_KEY_VARIABLE, FALLBACK_API_KEY_VARIABLE),
    GEMINI_PROVIDER_NAME: (GEMINI_API_KEY_VARIABLE,),
}

#: Which provider to use. Unset means "whichever has a credential", resolved
#: by :func:`resolve_provider` in the order of :data:`PROVIDER_NAMES`.
PROVIDER_VARIABLE = "CAD_AI_PROVIDER"

#: Which model to use. Overridable so a deployment is not pinned to this file.
MODEL_VARIABLE = "CAD_AI_MODEL"

#: The request timeout, in seconds.
TIMEOUT_VARIABLE = "CAD_AI_TIMEOUT_SECONDS"

#: How many model calls one description may cost, at most. An integer >= 1.
MAX_ATTEMPTS_VARIABLE = "CAD_AI_MAX_ATTEMPTS"

#: How hard the model should work on an answer. Unset means the model's own
#: default, and nothing is sent.
EFFORT_VARIABLE = "CAD_AI_EFFORT"

#: The effort levels a deployment may name. Sent only where the provider has a
#: parameter for it; a provider without one ignores the setting.
EFFORT_LEVELS: Tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

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
#: Anthropic's default is **Claude Haiku 5.5**, the model the product flow
#: runs on, chosen because it is the cheapest current model and a request may
#: now cost several calls. Named once, here: every other layer -- the HTTP
#: route, the browser, the application service -- reaches it through
#: :func:`config_from_environment`, so no model identifier is written twice.
#: The identifier is exact and carries no date suffix.
#:
#: ``CAD_AI_MODEL`` still overrides it. The step up, for a deployment that
#: measures Haiku falling short, is :data:`STEP_UP_MODEL`; nothing larger is
#: offered here, on cost grounds.
DEFAULT_MODELS: Mapping[str, str] = {
    PROVIDER_NAME: "claude-haiku-5-5",
    GEMINI_PROVIDER_NAME: "gemini-3.6-flash",
}

#: The documented step-up from the default Anthropic model. Never selected
#: automatically: a deployment names it in ``CAD_AI_MODEL``.
STEP_UP_MODEL = "claude-sonnet-5-5"

#: The default model for the default provider. Kept as its own name because
#: Stage 26 pinned it and a test asserts the installed SDK knows it.
DEFAULT_MODEL = DEFAULT_MODELS[PROVIDER_NAME]

#: Seconds, **per model call**. The default model reasons before it answers
#: and that reasoning counts toward the response, so a call takes longer than
#: the size of the document suggests. Generous, and still finite.
DEFAULT_TIMEOUT_SECONDS = 120.0

#: Model calls per description: the first answer plus up to two re-asks when
#: an answer is unusable, invalid or does not build. Bounded because every
#: call costs money; small because a model that has failed three times on one
#: description is unlikely to succeed on a fourth.
DEFAULT_MAX_ATTEMPTS = 3


class AiConfigurationError(Exception):
    """The AI layer was configured with a value it cannot use."""


@dataclass(frozen=True)
class AiConfig:
    """How to reach the model. **Carries no credential.**"""

    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    provider: str = PROVIDER_NAME

    #: The most model calls one description may cost. See
    #: :data:`DEFAULT_MAX_ATTEMPTS`.
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    #: One of :data:`EFFORT_LEVELS`, or ``None`` for the model's own default.
    effort: Optional[str] = None

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
        if (
            not isinstance(self.max_attempts, int)
            or isinstance(self.max_attempts, bool)
            or self.max_attempts < 1
        ):
            raise AiConfigurationError(
                "the attempt limit must be an integer >= 1; got "
                f"{self.max_attempts!r}"
            )
        if self.effort is not None and self.effort not in EFFORT_LEVELS:
            raise AiConfigurationError(
                f"the effort must be one of {', '.join(EFFORT_LEVELS)}; "
                f"got {self.effort!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Plain data. There is no credential field to leak."""
        return {
            "provider": self.provider,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_attempts": self.max_attempts,
            "effort": self.effort,
        }


def credential_variable(
    provider: str, environ: Optional[Mapping[str, str]] = None
) -> Optional[str]:
    """The **name** of the variable holding ``provider``'s credential, or None.

    The first name in :data:`CREDENTIAL_VARIABLES` that is present and not
    blank. Only the name is returned: the value is tested for emptiness and
    otherwise never returned, stored, compared or printed. A provider uses
    the name to make its own single read.
    """
    source = os.environ if environ is None else environ
    for name in CREDENTIAL_VARIABLES.get(provider, ()):
        if source.get(name, "").strip():
            return name
    return None


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
        if credential_variable(name, source) is not None:
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
    raw_attempts = source.get(MAX_ATTEMPTS_VARIABLE, "").strip()
    if not raw_attempts:
        attempts = DEFAULT_MAX_ATTEMPTS
    else:
        try:
            attempts = int(raw_attempts)
        except ValueError as exc:
            raise AiConfigurationError(
                f"{MAX_ATTEMPTS_VARIABLE} must be an integer >= 1; "
                f"got {raw_attempts!r}"
            ) from exc
    effort = source.get(EFFORT_VARIABLE, "").strip().lower() or None
    if effort is not None and effort not in EFFORT_LEVELS:
        raise AiConfigurationError(
            f"{EFFORT_VARIABLE} must be one of {', '.join(EFFORT_LEVELS)}; "
            f"got {effort!r}"
        )
    return AiConfig(
        model=model,
        timeout_seconds=timeout,
        provider=provider,
        max_attempts=attempts,
        effort=effort,
    )


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
    return any(credential_variable(name, source) is not None for name in names)


__all__ = [
    "API_KEY_VARIABLE",
    "API_KEY_VARIABLES",
    "CREDENTIAL_VARIABLES",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MODEL",
    "DEFAULT_MODELS",
    "DEFAULT_TIMEOUT_SECONDS",
    "EFFORT_LEVELS",
    "EFFORT_VARIABLE",
    "FALLBACK_API_KEY_VARIABLE",
    "GEMINI_API_KEY_VARIABLE",
    "GEMINI_PROVIDER_NAME",
    "MAX_ATTEMPTS_VARIABLE",
    "MODEL_VARIABLE",
    "PROVIDER_NAME",
    "PROVIDER_NAMES",
    "PROVIDER_VARIABLE",
    "STEP_UP_MODEL",
    "TIMEOUT_VARIABLE",
    "AiConfig",
    "AiConfigurationError",
    "config_from_environment",
    "credential_available",
    "credential_variable",
    "resolve_provider",
]
