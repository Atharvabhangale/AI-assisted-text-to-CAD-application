"""Natural-language generation, as a transport-side coordinator.

``POST /generate`` turns a sentence into a **validated, buildable CAD
document**. The AI layer checks each answer by validating it and building it
through the existing application service, and asks the model again -- within
a bounded number of calls -- when either rejects it. The browser then sends the
returned document to the existing ``POST /build``; the verification build used
the same outputs, so that build is answered from the cache.

What this module is for, and the whole of it:

1. hold the one :class:`~cad_ai.generation.TextToCadService`, built lazily so
   the application starts with no credential configured;
2. ask the AI layer which provider is configured, and build it once;
3. answer with the AI layer's **existing**
   :class:`~cad_ai.generation.AiGenerationResult`.

What it deliberately does not do:

* **no CAD logic.** It validates nothing itself: the service hands the model's
  answer to :meth:`~cad_core.application_service.CadApplicationService.validate_document`,
  and the V1 validator remains the only thing that decides validity.
* **no geometry of its own.** Any build happens inside the AI layer's call to
  the application service, in its isolated child process.
* **no repair.** No code patches a document. A rejected answer is re-authored
  by the model when it is asked again, and every attempt is reported.
* **no second outcome taxonomy.** The five
  :class:`~cad_ai.generation.GenerationOutcome` values are the AI layer's own.
* **no credential handling.** The provider reads its own environment variable
  and hands the value straight to its SDK. Nothing here reads, stores, logs or
  returns one, and :meth:`AiGenerationResult.to_dict` has no field that could
  carry one -- its development-only ``detail`` is excluded from the payload by
  the AI layer itself.

Which provider answers is not this module's business: it asks
:func:`cad_ai.factory.model_from_environment`, which owns that one mapping for
the whole application. Nothing here names a vendor, and no SDK is imported --
a test asserts the transport package names neither provider.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

from cad_core.application_service import CadApplicationService

from cad_ai.config import AiConfigurationError, config_from_environment
from cad_ai.factory import model_from_environment
from cad_ai.generation import (
    PUBLIC_MESSAGES,
    AiGenerationResult,
    GenerationMetadata,
    GenerationOutcome,
    TextToCadService,
)
from cad_ai.provider import ProviderError, ProviderErrorKind
# The settings store is re-exported for app.py, so this module stays the
# transport's single seam into the AI layer.
from cad_ai.settings import SettingsError, SettingsStore

#: The fields ``POST /generate`` accepts. Named here rather than in
#: ``cad_core``: a description is not CAD, and the CAD contract must not learn
#: about natural language.
GENERATE_REQUEST_FIELDS: Tuple[str, ...] = ("text",)

#: The longest description accepted. A part description for the supported
#: subset is a sentence; this is generous and still bounded, so a request
#: cannot be used to push an arbitrary payload at the provider.
MAX_DESCRIPTION_CHARACTERS = 2000


def _unavailable(kind: ProviderErrorKind) -> AiGenerationResult:
    """The answer when no model could be reached at all.

    Carries the AI layer's own stable public sentence and a vendor-neutral
    error kind. It names no variable, no provider message, no exception text
    and no path -- a caller learns that interpretation is unavailable and
    nothing about this server.
    """
    return AiGenerationResult(
        outcome=GenerationOutcome.MODEL_ERROR,
        message=PUBLIC_MESSAGES[GenerationOutcome.MODEL_ERROR],
        error_kind=kind,
        metadata=GenerationMetadata(),
    )


class TextGenerator:
    """The application's one text-to-CAD entry point.

    Holds the CAD application service the AI layer validates through, and
    builds the provider-backed :class:`~cad_ai.generation.TextToCadService`
    **on first use**. Lazily, so that:

    * the server starts, and every other route works, with no credential set;
    * a credential added after startup is picked up without a restart, because
      a construction failure is reported and not cached.

    A successfully built service *is* cached, so one client is created for the
    process rather than one per request -- until :meth:`reconfigure` drops it,
    which ``PUT /settings`` and ``DELETE /settings`` do so that the **next**
    generation is built from the new effective settings.

    Which provider and model that is comes from ``settings`` -- the AI layer's
    :class:`~cad_ai.settings.SettingsStore`, the environment overlaid by the
    owner's saved choice -- or, without one, from the environment alone, as
    before. This class decides nothing about either.
    """

    def __init__(
        self,
        service: CadApplicationService,
        *,
        generator: Optional[TextToCadService] = None,
        settings: Optional[SettingsStore] = None,
    ) -> None:
        self._service = service
        self._generator = generator
        self._settings = settings
        # Guards the cached service: a swap and a read never interleave. A
        # generation takes its reference under the lock and runs outside it,
        # so one already in flight finishes on the service it started with.
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        """Whether a service has already been built. Reads no credential."""
        with self._lock:
            return self._generator is not None

    def reconfigure(self) -> None:
        """Forget the cached service; the next generation builds a new one."""
        with self._lock:
            self._generator = None

    def _build(self) -> Optional[AiGenerationResult]:
        """Build the service, or return the failure to answer with.

        Called with the lock held.
        """
        if self._generator is not None:
            return None
        try:
            config = (
                config_from_environment()
                if self._settings is None
                else self._settings.effective_config()
            )
        except AiConfigurationError:
            return _unavailable(ProviderErrorKind.NOT_CONFIGURED)
        try:
            model = model_from_environment(config)
        except ProviderError as error:
            return _unavailable(error.kind)
        self._generator = TextToCadService(
            model, self._service, max_attempts=config.max_attempts
        )
        return None

    def generate(self, text: str) -> AiGenerationResult:
        """Interpret one description, within the configured attempt limit.

        Every outcome -- including a provider that cannot be reached -- comes
        back as an :class:`~cad_ai.generation.AiGenerationResult`, so the route
        above has one shape to map and no exception to translate.
        """
        with self._lock:
            unavailable = self._build()
            generator = self._generator
        if unavailable is not None:
            return unavailable
        assert generator is not None
        return generator.generate_cad_from_text(text)


__all__ = [
    "GENERATE_REQUEST_FIELDS",
    "MAX_DESCRIPTION_CHARACTERS",
    "SettingsError",
    "SettingsStore",
    "TextGenerator",
]
