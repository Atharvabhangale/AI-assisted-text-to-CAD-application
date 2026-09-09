"""The Gemini provider: Google's Generative AI API behind the same boundary.

The second implementation of :class:`~cad_ai.provider.TextToCadModel`. It
exists so the benchmark can be run where a Gemini credential is available, and
it changes nothing above the boundary: the same system prompt, the same
response schema, the same one-shot call, the same
:class:`~cad_ai.provider.ModelResponse`. No vendor type crosses the line, and
nothing outside this module imports ``google.genai``.

Like :mod:`cad_ai.anthropic_provider`, the SDK import is **lazy**, so
importing :mod:`cad_ai` needs no Google SDK installed.

What this provider does, and the whole of it:

1. read the credential from the environment, hand it to the SDK, keep no copy;
2. make **one** ``models.generate_content`` call with a system instruction,
   one user message, and a JSON-Schema response format;
3. return the response text plus metadata;
4. translate its own exceptions into :class:`~cad_ai.provider.ProviderError`.

It does not pass ``tools``, enable function calling, enable code execution,
enable search grounding, or stream. The model gets no tools here either.

Measured against the installed SDK (google-genai 2.22.0), not assumed:

* ``client.models.generate_content`` takes exactly ``model``, ``contents`` and
  ``config``;
* ``GenerateContentConfig`` carries ``system_instruction``,
  ``response_mime_type``, ``response_json_schema`` (a raw JSON Schema, which is
  what this application already has) and ``max_output_tokens``;
* it **also** carries ``temperature``, ``top_p``, ``top_k`` and ``seed`` --
  unlike the Anthropic SDK, which exposes none of them. **None is set here.**
  Leaving the provider's own defaults is what keeps a Gemini baseline
  comparable to the Anthropic one and keeps this stage a measurement rather
  than a tuning exercise. That the controls exist is recorded by
  :func:`decoding_capabilities`, not acted on.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from cad_ai.config import GEMINI_API_KEY_VARIABLE, GEMINI_PROVIDER_NAME, AiConfig
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    ProviderNotConfigured,
)

#: The SDK distribution this provider is written against.
SDK_PACKAGE = "google-genai"

#: The response media type that asks for JSON, from the SDK's own contract.
JSON_MIME_TYPE = "application/json"


def _import_sdk() -> Any:
    """Import the SDK, or say plainly that it is not installed."""
    try:
        from google import genai  # noqa: PLC0415 - lazy on purpose
    except ImportError as exc:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"the {SDK_PACKAGE} package is not installed",
        ) from exc
    return genai


def _import_errors() -> Any:
    from google.genai import errors  # noqa: PLC0415 - lazy on purpose

    return errors


class GeminiTextToCadModel:
    """A :class:`~cad_ai.provider.TextToCadModel` over Gemini."""

    name = GEMINI_PROVIDER_NAME

    def __init__(self, client: Any, config: AiConfig) -> None:
        self._client = client
        self._config = config
        self._errors = _import_errors()

    @classmethod
    def from_environment(
        cls, config: Optional[AiConfig] = None
    ) -> "GeminiTextToCadModel":
        """Build a provider from the environment.

        Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the SDK is
        absent or no credential is set. The credential is read here, passed to
        the SDK, and retained by neither this object nor its
        :class:`~cad_ai.config.AiConfig`.
        """
        genai = _import_sdk()
        from cad_ai.config import config_from_environment  # local: avoid cycle

        settings = config if config is not None else config_from_environment()
        api_key = os.environ.get(GEMINI_API_KEY_VARIABLE, "").strip()
        if not api_key:
            raise ProviderNotConfigured(
                "the interpretation service is not available",
                detail=f"{GEMINI_API_KEY_VARIABLE} is not set",
            )
        client = genai.Client(
            api_key=api_key,
            http_options={"timeout": int(settings.timeout_seconds * 1000)},
        )
        return cls(client, settings)

    @property
    def config(self) -> AiConfig:
        return self._config

    def generate(self, request: ModelRequest) -> ModelResponse:
        """One call. No tools, no streaming, no retry of the interpretation."""
        genai = _import_sdk()
        from google.genai import types  # noqa: PLC0415 - lazy on purpose

        settings: Dict[str, Any] = {
            "system_instruction": request.system,
            "max_output_tokens": request.max_output_tokens,
        }
        structured = request.output_schema is not None
        if structured:
            settings["response_mime_type"] = JSON_MIME_TYPE
            settings["response_json_schema"] = dict(request.output_schema or {})
        try:
            response = self._client.models.generate_content(
                model=self._config.model,
                contents=request.user_text,
                config=types.GenerateContentConfig(**settings),
            )
        except self._errors.APIError as exc:
            # The provider's own message may name hosts, headers or request
            # ids. It stays in `detail`, which never reaches a public payload;
            # only the vendor-neutral `kind` is published.
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                kind=_classify(exc),
            ) from exc
        except Exception as exc:  # a transport or SDK fault, not an API error
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=f"{type(exc).__name__}: {exc}",
                kind=_classify(exc),
            ) from exc
        return self._response(response, structured=structured)

    def _response(self, response: Any, *, structured: bool) -> ModelResponse:
        text = _text_of(response)
        if not text.strip():
            raise ProviderError(
                "the interpretation service returned no answer",
                detail=(
                    "no text content in the response; finish_reason="
                    f"{_finish_reason(response)!r}"
                ),
                kind=ProviderErrorKind.EMPTY_RESPONSE,
            )
        return ModelResponse(
            text=text,
            provider=self.name,
            model=str(getattr(response, "model_version", None) or self._config.model),
            structured_output=structured,
            stop_reason=_finish_reason(response),
            usage=_usage_of(response),
        )


#: HTTP status -> the neutral classification. Read from the SDK error's own
#: ``code`` attribute where it has one, so no status text is parsed.
_STATUS_KINDS = {
    400: ProviderErrorKind.INVALID_REQUEST,
    401: ProviderErrorKind.AUTHENTICATION,
    403: ProviderErrorKind.AUTHENTICATION,
    404: ProviderErrorKind.INVALID_REQUEST,
    408: ProviderErrorKind.TIMEOUT,
    429: ProviderErrorKind.RATE_LIMITED,
    500: ProviderErrorKind.OTHER,
    503: ProviderErrorKind.CAPACITY,
    504: ProviderErrorKind.TIMEOUT,
}


def _classify(exc: BaseException) -> ProviderErrorKind:
    """Map one SDK exception onto the neutral classification.

    Prefers the error's own numeric ``code``; falls back to the exception
    type for transport faults. Nothing here parses a provider's prose, so a
    reworded message cannot silently change a benchmark's error counts.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int) and code in _STATUS_KINDS:
        return _STATUS_KINDS[code]
    if isinstance(exc, TimeoutError):
        return ProviderErrorKind.TIMEOUT
    name = type(exc).__name__
    if name in ("ServerError",):
        return ProviderErrorKind.CAPACITY
    if name in ("ClientError",):
        return ProviderErrorKind.INVALID_REQUEST
    return ProviderErrorKind.OTHER


def _text_of(response: Any) -> str:
    """The response's text, via the SDK's own accessor where it works.

    ``GenerateContentResponse.text`` concatenates the text parts of the first
    candidate and skips non-text parts, which is exactly what is wanted: only
    text can be a CAD document. It returns ``None`` when there is no text, and
    can raise when the response holds nothing usable, so both are handled --
    an empty string here becomes a :class:`ProviderError` above.
    """
    try:
        value = response.text
    except Exception:
        value = None
    if isinstance(value, str):
        return value
    return ""


def _finish_reason(response: Any) -> Optional[str]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None:
            return str(getattr(reason, "name", reason))
    return None


def _usage_of(response: Any) -> Dict[str, int]:
    """Token counts, when reported.

    Mapped onto the same two names the Anthropic provider reports, so the
    evaluation harness aggregates one vocabulary rather than two.
    """
    usage = getattr(response, "usage_metadata", None)
    counts: Dict[str, int] = {}
    for source, target in (
        ("prompt_token_count", "input_tokens"),
        ("candidates_token_count", "output_tokens"),
    ):
        value = getattr(usage, source, None)
        if isinstance(value, int):
            counts[target] = value
    return counts


def decoding_capabilities() -> Dict[str, str]:
    """Which decoding controls the installed SDK exposes.

    Read from ``GenerateContentConfig``'s own fields rather than from memory.
    Gemini's SDK does expose ``temperature`` and ``seed``, so a run's recorded
    settings will say ``deterministic_decoding: available`` -- **and this
    provider still sets none of them**, which the run also records.
    """
    capabilities: Dict[str, str] = {
        name: "not settable"
        for name in ("temperature", "top_p", "top_k", "seed")
    }
    capabilities["deterministic_decoding"] = "unavailable"
    capabilities["sdk_version"] = "not installed"
    capabilities["decoding_controls_used"] = "none"
    try:
        genai = _import_sdk()
        from google.genai import types  # noqa: PLC0415

        capabilities["sdk_version"] = str(
            getattr(genai, "__version__", "unknown")
        )
        fields = types.GenerateContentConfig.model_fields
    except Exception:  # pragma: no cover - an SDK we cannot introspect
        return capabilities
    for name in ("temperature", "top_p", "top_k", "seed"):
        capabilities[name] = "settable" if name in fields else "not settable"
    capabilities["deterministic_decoding"] = (
        "available"
        if any(name in fields for name in ("temperature", "seed"))
        else "unavailable"
    )
    return capabilities


__all__ = [
    "JSON_MIME_TYPE",
    "SDK_PACKAGE",
    "GeminiTextToCadModel",
    "decoding_capabilities",
]
