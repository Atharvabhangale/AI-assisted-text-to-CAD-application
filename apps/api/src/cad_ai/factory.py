"""Which provider a run uses: one dispatch, in one place.

Two named implementations chosen by configuration -- **not** a plugin
registry. There is no discovery, no entry point and no dynamic loading, and
adding a third provider means writing a third class and naming it here.

Each provider class is imported **inside its own branch**, so importing this
module needs neither SDK, and a deployment that installs only one extra still
works. That keeps Stage 26's invariant intact: exactly one module imports
``anthropic`` and exactly one imports ``google.genai``, and this is neither of
them.

This exists so that callers who need a configured model -- the HTTP
application and the evaluation harness -- share one answer to "which provider
is configured?" instead of each keeping their own copy of the mapping.
"""

from __future__ import annotations

from cad_ai.config import GEMINI_PROVIDER_NAME, AiConfig
from cad_ai.provider import TextToCadModel


def model_from_environment(config: AiConfig) -> TextToCadModel:
    """The configured provider, built from the environment.

    The credential is read by the provider itself, handed to its SDK, and
    retained by neither the provider nor its :class:`~cad_ai.config.AiConfig`.
    Nothing here reads, stores, compares, logs or returns one.

    Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the chosen
    provider's SDK is absent or its credential is unset.
    """
    if config.provider == GEMINI_PROVIDER_NAME:
        from cad_ai.gemini_provider import GeminiTextToCadModel

        return GeminiTextToCadModel.from_environment(config)
    from cad_ai.anthropic_provider import AnthropicTextToCadModel

    return AnthropicTextToCadModel.from_environment(config)


__all__ = ["model_from_environment"]
