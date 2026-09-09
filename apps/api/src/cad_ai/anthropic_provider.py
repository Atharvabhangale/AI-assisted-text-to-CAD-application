"""The one provider: Anthropic's Messages API.

The only module in the repository that imports the ``anthropic`` SDK, and it
imports it **lazily** -- inside the factory -- so that importing
:mod:`cad_ai` needs no SDK and the interpretation tests run without one. That
mirrors how :mod:`cad_core.local_cad` keeps CadQuery optional.

What this provider does, and the whole of it:

1. read the credential from the environment, hand it to the SDK, keep no copy;
2. make **one** ``messages.create`` call with a system prompt, one user
   message, and a JSON-Schema output format;
3. return the response text plus metadata as a :class:`~cad_ai.provider.
   ModelResponse`;
4. translate its own exceptions into :class:`~cad_ai.provider.ProviderError`.

What it deliberately does not do: pass ``tools``, enable code execution,
enable any server tool, stream, retry the interpretation, or interpret the
text it returns. ``tools`` is never sent, so the model has no function to
call; a test asserts the keyword appears in no call this module makes.

Measured against the installed SDK (see ``docs/text-to-cad-ai.md``):
``messages.create`` accepts ``output_config`` with a ``json_schema`` format --
so structured output is genuine here, not a request in the prompt. It accepts
no ``temperature``, ``top_p`` or ``top_k`` parameter, so no decoding
temperature is set, and none can be.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from cad_ai.config import (
    API_KEY_VARIABLE,
    PROVIDER_NAME,
    AiConfig,
    config_from_environment,
)
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    ProviderNotConfigured,
)

#: The SDK distribution this provider is written against.
SDK_PACKAGE = "anthropic"

#: The output-format discriminator the installed SDK requires. Verified
#: against ``anthropic.types.json_output_format_param.JSONOutputFormatParam``.
JSON_SCHEMA_FORMAT = "json_schema"


def _import_sdk() -> Any:
    """Import the SDK, or say plainly that it is not installed."""
    try:
        import anthropic  # noqa: PLC0415 - lazy on purpose
    except ImportError as exc:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"the {SDK_PACKAGE} package is not installed",
        ) from exc
    return anthropic


class AnthropicTextToCadModel:
    """A :class:`~cad_ai.provider.TextToCadModel` over the Messages API."""

    name = PROVIDER_NAME

    def __init__(self, client: Any, config: AiConfig) -> None:
        self._client = client
        self._config = config
        self._sdk = _import_sdk()

    @classmethod
    def from_environment(
        cls, config: Optional[AiConfig] = None
    ) -> "AnthropicTextToCadModel":
        """Build a provider from the environment.

        Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the SDK is
        absent or no credential is set -- so an unconfigured deployment fails
        at construction, loudly, rather than at the first request.

        The credential is read here, passed to the SDK, and not retained by
        this object or by :class:`~cad_ai.config.AiConfig`.
        """
        sdk = _import_sdk()
        settings = config if config is not None else config_from_environment()
        api_key = os.environ.get(API_KEY_VARIABLE, "").strip()
        if not api_key:
            raise ProviderNotConfigured(
                "the interpretation service is not available",
                detail=f"{API_KEY_VARIABLE} is not set",
            )
        client = sdk.Anthropic(
            api_key=api_key, timeout=settings.timeout_seconds
        )
        return cls(client, settings)

    @property
    def config(self) -> AiConfig:
        return self._config

    def generate(self, request: ModelRequest) -> ModelResponse:
        """One call. No tools, no streaming, no retry of the interpretation."""
        parameters: Dict[str, Any] = {
            "model": self._config.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user_text}],
        }
        structured = request.output_schema is not None
        if structured:
            parameters["output_config"] = {
                "format": {
                    "type": JSON_SCHEMA_FORMAT,
                    "schema": dict(request.output_schema or {}),
                }
            }
        try:
            message = self._client.messages.create(**parameters)
        except self._sdk.AnthropicError as exc:
            # The provider's own message may name hosts, headers or request
            # ids. It stays in `detail`, which never reaches a public payload;
            # only the vendor-neutral `kind` is published.
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                kind=self._classify(exc),
            ) from exc
        except Exception as exc:  # a transport or SDK fault, not an API error
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                kind=self._classify(exc),
            ) from exc
        return self._response(message, structured=structured)

    def _classify(self, exc: BaseException) -> ProviderErrorKind:
        """Map one SDK exception onto the neutral classification.

        Uses the SDK's own exception classes, which are its stable contract,
        rather than parsing status text.
        """
        sdk = self._sdk
        pairs = (
            ("RateLimitError", ProviderErrorKind.RATE_LIMITED),
            ("OverloadedError", ProviderErrorKind.CAPACITY),
            ("ServiceUnavailableError", ProviderErrorKind.CAPACITY),
            ("APITimeoutError", ProviderErrorKind.TIMEOUT),
            ("DeadlineExceededError", ProviderErrorKind.TIMEOUT),
            ("AuthenticationError", ProviderErrorKind.AUTHENTICATION),
            ("PermissionDeniedError", ProviderErrorKind.AUTHENTICATION),
            ("NotFoundError", ProviderErrorKind.INVALID_REQUEST),
            ("BadRequestError", ProviderErrorKind.INVALID_REQUEST),
            ("UnprocessableEntityError", ProviderErrorKind.INVALID_REQUEST),
            ("APIConnectionError", ProviderErrorKind.TIMEOUT),
        )
        for name, kind in pairs:
            candidate = getattr(sdk, name, None)
            if isinstance(candidate, type) and isinstance(exc, candidate):
                return kind
        if isinstance(exc, TimeoutError):
            return ProviderErrorKind.TIMEOUT
        return ProviderErrorKind.OTHER

    def _response(self, message: Any, *, structured: bool) -> ModelResponse:
        text = _text_of(message)
        if not text.strip():
            raise ProviderError(
                "the interpretation service returned no answer",
                detail=(
                    "no text content in the response; stop_reason="
                    f"{getattr(message, 'stop_reason', None)!r}"
                ),
                kind=ProviderErrorKind.EMPTY_RESPONSE,
            )
        return ModelResponse(
            text=text,
            provider=self.name,
            model=str(getattr(message, "model", self._config.model)),
            structured_output=structured,
            stop_reason=_optional_str(getattr(message, "stop_reason", None)),
            usage=_usage_of(message),
        )


def decoding_capabilities() -> Dict[str, str]:
    """Which decoding controls the installed SDK actually exposes.

    Read from the live ``messages.create`` signature, not from memory, so a
    caller recording what a generation was configured with records what is
    true today rather than what was true when this was written. Lives here
    because the provider owns knowledge of its SDK: nothing else in the
    repository imports ``anthropic``, and a test asserts that.
    """
    capabilities: Dict[str, str] = {
        name: "not settable"
        for name in ("temperature", "top_p", "top_k", "seed")
    }
    capabilities["deterministic_decoding"] = "unavailable"
    capabilities["sdk_version"] = "not installed"
    try:
        sdk = _import_sdk()
    except ProviderNotConfigured:
        return capabilities
    capabilities["sdk_version"] = str(getattr(sdk, "__version__", "unknown"))
    try:
        import inspect  # noqa: PLC0415 - only needed for this probe

        parameters = inspect.signature(
            sdk.Anthropic(api_key="unused-placeholder").messages.create
        ).parameters
    except Exception:  # pragma: no cover - an SDK we cannot introspect
        return capabilities
    for name in ("temperature", "top_p", "top_k", "seed"):
        capabilities[name] = "settable" if name in parameters else "not settable"
    capabilities["deterministic_decoding"] = (
        "available"
        if any(name in parameters for name in ("temperature", "seed"))
        else "unavailable"
    )
    return capabilities


def _text_of(message: Any) -> str:
    """Concatenate the response's text blocks, ignoring every other block.

    A block that is not text -- thinking, a tool use, anything the API may add
    later -- is dropped rather than coerced. Only text can be a CAD document.
    """
    blocks = getattr(message, "content", None) or []
    parts: List[str] = []
    for block in blocks:
        if getattr(block, "type", None) != "text":
            continue
        value = getattr(block, "text", None)
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts)


def _usage_of(message: Any) -> Dict[str, int]:
    """Token counts, when reported. Never a prompt and never a credential."""
    usage = getattr(message, "usage", None)
    counts: Dict[str, int] = {}
    for name in ("input_tokens", "output_tokens"):
        value = getattr(usage, name, None)
        if isinstance(value, int):
            counts[name] = value
    return counts


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = [
    "JSON_SCHEMA_FORMAT",
    "SDK_PACKAGE",
    "AnthropicTextToCadModel",
    "decoding_capabilities",
]
