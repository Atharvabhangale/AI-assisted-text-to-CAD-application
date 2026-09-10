"""Configuration for the experimental path. Explicit, and separate.

The stable path's configuration (``cad_ai.config``) is untouched: this stage
pins its own model so that a change to the production default cannot silently
move the experiment, and vice versa.

As everywhere in this project, only the *presence* of a credential is ever
read. No value is returned, stored, logged or compared.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Mapping, Optional

#: The provider this experiment uses, named for the existing adapter in
#: ``cad_ai.anthropic_provider``. Gemini support is untouched and unused here.
PROVIDER_NAME = "anthropic"

#: The credential, by conventional name. Never read for its value.
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"

#: The model this stage measures, pinned. Claude Haiku 4.5.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"

#: Overrides, for a deliberate one-off run. Separate variables from the
#: production ones so exporting a production override cannot reach in here.
MODEL_VARIABLE = "CAD_EXPERIMENTAL_MODEL"
TIMEOUT_VARIABLE = "CAD_EXPERIMENTAL_TIMEOUT_SECONDS"

DEFAULT_TIMEOUT_SECONDS = 60.0

#: An upper bound on the reply. A plan is a few hundred tokens; this is
#: generous and still bounds a runaway generation.
MAX_OUTPUT_TOKENS = 1024

#: The ports this experiment runs on. The stable application keeps 8000/5173
#: and is never started by anything here.
API_PORT = 8001
WEB_PORT = 5174
STABLE_API_PORT = 8000
STABLE_WEB_PORT = 5173


class ExperimentalConfigurationError(Exception):
    """The environment asks for something impossible."""


@dataclass(frozen=True)
class ExperimentalConfig:
    """Which model to ask, and how long to wait. No credential field.

    There is deliberately nowhere to put a key: a config object that cannot
    hold one cannot leak one into a log, a payload or a saved measurement.
    """

    model: str = DEFAULT_MODEL
    provider: str = PROVIDER_NAME
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def to_dict(self) -> Dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
        }


def config_from_environment(
    environ: Optional[Mapping[str, str]] = None,
) -> ExperimentalConfig:
    """Read the configuration. Never reads a credential."""
    source = os.environ if environ is None else environ
    model = source.get(MODEL_VARIABLE, "").strip() or DEFAULT_MODEL
    raw_timeout = source.get(TIMEOUT_VARIABLE, "").strip()
    if not raw_timeout:
        timeout = DEFAULT_TIMEOUT_SECONDS
    else:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise ExperimentalConfigurationError(
                f"{TIMEOUT_VARIABLE} must be a number of seconds"
            ) from None
        if timeout <= 0:
            raise ExperimentalConfigurationError(
                f"{TIMEOUT_VARIABLE} must be positive"
            )
    return ExperimentalConfig(model=model, timeout_seconds=timeout)


def credential_available(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    """Whether a credential is present. Presence only -- never the value."""
    source = os.environ if environ is None else environ
    return bool(source.get(API_KEY_VARIABLE, "").strip())


__all__ = [
    "API_KEY_VARIABLE",
    "API_PORT",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_OUTPUT_TOKENS",
    "MODEL_VARIABLE",
    "PROVIDER_NAME",
    "STABLE_API_PORT",
    "STABLE_WEB_PORT",
    "TIMEOUT_VARIABLE",
    "WEB_PORT",
    "ExperimentalConfig",
    "ExperimentalConfigurationError",
    "config_from_environment",
    "credential_available",
]
