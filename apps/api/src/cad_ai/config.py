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
from typing import Any, Dict, Optional

#: The provider this stage implements. One, deliberately.
PROVIDER_NAME = "anthropic"

#: The credential variable, which is the Anthropic SDK's own conventional
#: name. Read by the provider only, and never by anything else.
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"

#: Which model to use. Overridable so a deployment is not pinned to this file.
MODEL_VARIABLE = "CAD_AI_MODEL"

#: The request timeout, in seconds.
TIMEOUT_VARIABLE = "CAD_AI_TIMEOUT_SECONDS"

#: The default model. A name the installed SDK itself lists as valid (see
#: ``docs/text-to-cad-ai.md``); no model identifier was invented here.
DEFAULT_MODEL = "claude-sonnet-5"

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


def config_from_environment(
    environ: Optional[Dict[str, str]] = None,
) -> AiConfig:
    """Read :class:`AiConfig` from the environment. Never reads a credential."""
    source = os.environ if environ is None else environ
    model = source.get(MODEL_VARIABLE, "").strip() or DEFAULT_MODEL
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
    return AiConfig(model=model, timeout_seconds=timeout)


def credential_available(environ: Optional[Dict[str, str]] = None) -> bool:
    """Whether a credential is present, without reading its value.

    Returns a boolean and nothing else. Used to decide whether a live
    provider test runs or reports itself skipped.
    """
    source = os.environ if environ is None else environ
    return bool(source.get(API_KEY_VARIABLE, "").strip())


__all__ = [
    "API_KEY_VARIABLE",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MODEL_VARIABLE",
    "PROVIDER_NAME",
    "TIMEOUT_VARIABLE",
    "AiConfig",
    "AiConfigurationError",
    "config_from_environment",
    "credential_available",
]
