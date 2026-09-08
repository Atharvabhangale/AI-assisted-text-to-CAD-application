"""Explicit configuration for the HTTP application.

Two settings, both explicit. **No default cache path is invented** -- not
``~/.cache``, not a temporary directory, not the current working directory --
because a server that silently writes build artifacts somewhere nobody chose
is exactly the surprise these layers avoid. The caller names the location.

No configuration framework, no settings file, no secret. Nothing here reads a
credential, a token or a cloud variable, and nothing in this stage needs one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from cad_core.isolated_execution import DEFAULT_TIMEOUT_SECONDS

PathLike = Union[str, "os.PathLike[str]"]

#: The environment variable naming the build cache root, for launching the app
#: with an ASGI server that can only import a module-level object. Named
#: explicitly rather than guessed, and it is not a secret.
CACHE_ROOT_VARIABLE = "CAD_API_CACHE_ROOT"

#: Optional override of the isolated build timeout, in seconds.
TIMEOUT_VARIABLE = "CAD_API_TIMEOUT_SECONDS"


class ConfigurationError(Exception):
    """The application was not given the configuration it needs."""


@dataclass(frozen=True)
class ApiConfig:
    """Where builds are cached, and how long one may take.

    ``cache_root`` must already exist: the application service refuses a root
    it was not given, and this layer does not create one either.
    """

    cache_root: Path
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        root = Path(os.fspath(self.cache_root))
        object.__setattr__(self, "cache_root", root)
        if not root.is_dir():
            raise ConfigurationError(
                f"the cache root {root} is not a directory; the HTTP "
                "application is always told where to cache and never invents a "
                "location"
            )
        if self.timeout_seconds <= 0:
            raise ConfigurationError("timeout_seconds must be positive")

    @classmethod
    def for_cache_root(
        cls, cache_root: PathLike, *, timeout_seconds: Optional[float] = None
    ) -> "ApiConfig":
        return cls(
            cache_root=Path(os.fspath(cache_root)),
            timeout_seconds=(
                DEFAULT_TIMEOUT_SECONDS
                if timeout_seconds is None
                else timeout_seconds
            ),
        )


def config_from_environment() -> ApiConfig:
    """Read the configuration from the two documented variables.

    For launching under an ASGI server, which can only import a module-level
    application object. Raises if the cache root is unset -- **no path is
    guessed**.
    """
    root = os.environ.get(CACHE_ROOT_VARIABLE)
    if not root:
        raise ConfigurationError(
            f"{CACHE_ROOT_VARIABLE} is not set; the HTTP application needs an "
            "explicit build cache root and does not invent one"
        )
    raw_timeout = os.environ.get(TIMEOUT_VARIABLE)
    timeout: Optional[float] = None
    if raw_timeout:
        try:
            timeout = float(raw_timeout)
        except ValueError:
            raise ConfigurationError(
                f"{TIMEOUT_VARIABLE} must be a number of seconds"
            ) from None
    return ApiConfig.for_cache_root(root, timeout_seconds=timeout)
