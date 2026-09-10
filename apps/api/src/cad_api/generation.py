"""Natural-language generation, as a transport-side coordinator.

``POST /generate`` turns a sentence into a **validated candidate CAD
document** and stops there. It builds nothing: the browser takes the document
this returns and sends it to the existing ``POST /build``, so one build path
serves the AI flow and the JSON flow alike.

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
* **no geometry.** No kernel, no exporter, no cache, no child process. A
  document that comes back here has been validated and not built.
* **no repair.** An invalid answer is reported, never patched, and never
  retried. There is no second attempt anywhere in this path.
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
    process rather than one per request.
    """

    def __init__(
        self,
        service: CadApplicationService,
        *,
        generator: Optional[TextToCadService] = None,
    ) -> None:
        self._service = service
        self._generator = generator

    @property
    def configured(self) -> bool:
        """Whether a service has already been built. Reads no credential."""
        return self._generator is not None

    def _build(self) -> Optional[AiGenerationResult]:
        """Build the service, or return the failure to answer with."""
        if self._generator is not None:
            return None
        try:
            config = config_from_environment()
        except AiConfigurationError:
            return _unavailable(ProviderErrorKind.NOT_CONFIGURED)
        try:
            model = model_from_environment(config)
        except ProviderError as error:
            return _unavailable(error.kind)
        self._generator = TextToCadService(model, self._service)
        return None

    def generate(self, text: str) -> AiGenerationResult:
        """Interpret one description. One model call, one attempt, no retry.

        Every outcome -- including a provider that cannot be reached -- comes
        back as an :class:`~cad_ai.generation.AiGenerationResult`, so the route
        above has one shape to map and no exception to translate.
        """
        unavailable = self._build()
        if unavailable is not None:
            return unavailable
        assert self._generator is not None
        return self._generator.generate_cad_from_text(text)


__all__ = [
    "GENERATE_REQUEST_FIELDS",
    "MAX_DESCRIPTION_CHARACTERS",
    "TextGenerator",
]
