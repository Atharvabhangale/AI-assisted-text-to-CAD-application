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

#: The credential, by the SDK's own conventional name. This is the variable
#: the Anthropic SDK reads, and the only one it reads.
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"

#: The credential under the name an OPERATOR supplies it, which is what
#: Claude Code Web provides: that platform reserves and strips
#: :data:`API_KEY_VARIABLE`, so a key exported under that name never reaches
#: the process.
OPERATOR_KEY_VARIABLE = "CAD_ANTHROPIC_API_KEY"

#: Which variable wins when BOTH are set, stated rather than left to
#: whichever code path happens to look first.
#:
#: **`CAD_ANTHROPIC_API_KEY` takes precedence.** It is the one an operator
#: sets deliberately for this project, whereas `ANTHROPIC_API_KEY` may be
#: ambient in the environment for unrelated reasons. Preferring the
#: deliberate one means "the key I exported for this run" is always the key
#: that is used.
#:
#: The order is a tuple so the rule is one value, checked by one test,
#: rather than an `if` repeated per call site.
CREDENTIAL_PRECEDENCE: tuple = (OPERATOR_KEY_VARIABLE, API_KEY_VARIABLE)

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
    """Whether a credential is present. Presence only -- never the value.

    Considers **both** names in :data:`CREDENTIAL_PRECEDENCE`. It read only
    `ANTHROPIC_API_KEY` until this was widened, which meant that in Claude
    Code Web -- where the platform strips that name and the operator supplies
    `CAD_ANTHROPIC_API_KEY` instead -- a live run reported "credential
    absent" with a perfectly good key sitting in the environment. That is the
    trap §7 of CLAUDE.md already warned about; now the code handles it rather
    than the reader having to.
    """
    source = os.environ if environ is None else environ
    return any(bool(source.get(name, "").strip())
               for name in CREDENTIAL_PRECEDENCE)


def credential_variable(
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """WHICH variable supplies the credential, by name. Never the value.

    Returns the first name in :data:`CREDENTIAL_PRECEDENCE` that is set, or
    ``None``. Safe to log and safe to put in a report: it is a variable
    name, not a secret.
    """
    source = os.environ if environ is None else environ
    for name in CREDENTIAL_PRECEDENCE:
        if source.get(name, "").strip():
            return name
    return None


def bridge_credential(
    environ: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Put the operator's key where the SDK looks for it, in this process.

    The SDK reads :data:`API_KEY_VARIABLE` and nothing else. When the
    credential arrives under :data:`OPERATOR_KEY_VARIABLE` -- as it does in
    Claude Code Web -- this copies it across so a live call works without
    every caller re-implementing the same two lines.

    Returns the **name** of the variable the credential came from, or
    ``None`` when there is no credential. It never returns, logs or compares
    the value.

    Precedence is :data:`CREDENTIAL_PRECEDENCE`, so an operator-supplied key
    overwrites an ambient `ANTHROPIC_API_KEY`. That is deliberate and is the
    whole reason the order is written down: "the key I exported for this run"
    should be the key that is used.

    A no-op when the only credential present is already under the SDK's name.
    """
    target = os.environ if environ is None else environ
    source = target
    chosen = credential_variable(source)
    if chosen is None:
        return None
    if chosen != API_KEY_VARIABLE:
        target[API_KEY_VARIABLE] = source[chosen].strip()
    return chosen


__all__ = [
    "API_KEY_VARIABLE",
    "CREDENTIAL_PRECEDENCE",
    "OPERATOR_KEY_VARIABLE",
    "bridge_credential",
    "credential_variable",
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
