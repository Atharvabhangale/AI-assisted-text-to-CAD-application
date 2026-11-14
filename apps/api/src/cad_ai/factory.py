"""Which provider a run uses: one dispatch, in one place.

Named implementations chosen by configuration -- **not** a plugin registry.
There is no discovery, no entry point and no dynamic loading, and adding a
provider means writing a class and naming it here. The third,
``claude_code`` (the owner's subscription through the ``claude`` CLI), and
the fourth, ``antigravity`` (the owner's Google account through the ``agy``
CLI), are reached only when ``CAD_AI_PROVIDER`` names them.

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

from cad_ai.config import (
    ANTIGRAVITY_PROVIDER_NAME,
    CLAUDE_CODE_PROVIDER_NAME,
    GEMINI_PROVIDER_NAME,
    AiConfig,
)
from cad_ai.provider import TextToCadModel


def model_from_environment(config: AiConfig) -> TextToCadModel:
    """The configured provider, built from the environment.

    The credential is read by the provider itself, handed to its SDK, and
    retained by neither the provider nor its :class:`~cad_ai.config.AiConfig`.
    Nothing here reads, stores, compares, logs or returns one.

    Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the chosen
    provider's SDK is absent or its credential is unset -- or, for
    ``claude_code`` / ``antigravity``, when the ``claude`` / ``agy``
    executable cannot be found.
    """
    if config.provider == GEMINI_PROVIDER_NAME:
        from cad_ai.gemini_provider import GeminiTextToCadModel

        return GeminiTextToCadModel.from_environment(config)
    if config.provider == CLAUDE_CODE_PROVIDER_NAME:
        from cad_ai.claude_code_provider import ClaudeCodeTextToCadModel

        return ClaudeCodeTextToCadModel.from_environment(config)
    if config.provider == ANTIGRAVITY_PROVIDER_NAME:
        from cad_ai.antigravity_provider import AntigravityTextToCadModel

        return AntigravityTextToCadModel.from_environment(config)
    from cad_ai.anthropic_provider import AnthropicTextToCadModel

    return AnthropicTextToCadModel.from_environment(config)


__all__ = ["model_from_environment"]
