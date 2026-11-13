"""The one provider: Anthropic's Messages API.

The only module in the repository that imports the ``anthropic`` SDK, and it
imports it **lazily** -- inside the factory -- so that importing
:mod:`cad_ai` needs no SDK and the interpretation tests run without one. That
mirrors how :mod:`cad_core.local_cad` keeps CadQuery optional.

What this provider does, and the whole of it:

1. read the credential from the environment, hand it to the SDK, keep no copy;
2. make **one** ``messages.create`` call per :meth:`generate` with a system
   prompt, one user message, and a JSON-Schema output format;
3. return the response text plus metadata as a :class:`~cad_ai.provider.
   ModelResponse`;
4. translate its own exceptions, and a model refusal, into
   :class:`~cad_ai.provider.ProviderError`.

What it deliberately does not do: pass ``tools``, enable code execution,
enable any server tool, stream, retry the interpretation, or interpret the
text it returns. ``tools`` is never sent, so the model has no function to
call; a test asserts the keyword appears in no call this module makes.
Re-asking after a rejected answer is :mod:`cad_ai.generation`'s decision; each
re-ask reaches this module as one more ordinary call.

Measured against the installed SDK (see ``docs/text-to-cad-ai.md``):
``messages.create`` accepts ``output_config`` with a ``json_schema`` format --
so structured output is genuine here, not a request in the prompt. It accepts
no ``temperature``, ``top_p`` or ``top_k`` parameter, so no decoding
temperature is set, and none can be.

What the default model needs from a request, and why nothing more is sent:

* **No ``thinking`` parameter.** The default model reasons before it answers
  without being asked, and rejects a fixed reasoning budget outright. The
  reasoning arrives as non-text content blocks, which :func:`_text_of` drops;
  its tokens count toward ``max_tokens``, which is why the generation layer's
  bound is large.
* **``output_config.effort`` only when configured.** ``CAD_AI_EFFORT`` unset
  sends nothing and the model uses its own default.
* **The system prompt is marked cacheable.** It is one content block carrying
  ``cache_control``, so a re-ask within a request, and the next request,
  re-read it at the cache rate instead of paying for it in full. The marker
  sits on the system block and *not* at the top level of the request: a
  top-level marker lands after the user's description, which differs on every
  call, so each call would pay to write an entry no later call could read.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from cad_ai.config import (
    CREDENTIAL_VARIABLES,
    PROVIDER_NAME,
    AiConfig,
    config_from_environment,
    credential_variable,
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

#: The stop reason that means the model declined the request. Verified against
#: ``anthropic.types.stop_reason.StopReason``.
REFUSAL_STOP_REASON = "refusal"

#: The cache marker put on the system prompt block. Verified against
#: ``anthropic.types.cache_control_ephemeral_param``.
CACHE_CONTROL = {"type": "ephemeral"}

#: The usage counters reported when present. The two cache counters say how
#: much of the prompt was re-read from the cache and how much was written to
#: it, which is the only evidence that the marker above is doing anything.
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

#: JSON-Schema keywords this API's structured-output compiler rejects.
#:
#: Measured, not assumed. Sending the model-facing schema unchanged returned
#: **400** ``invalid_request_error``: *"output_config.format.schema: For
#: 'number' type, property 'exclusiveMinimum' is not supported"*. The
#: documented subset accepts the basic types, ``enum``, ``const``, ``anyOf``,
#: ``allOf``, ``$ref``/``$defs``, the named string formats and
#: ``additionalProperties: false``; it accepts no numeric bound, no string
#: length or pattern constraint and no array-size constraint.
#:
#: Removing these costs **no validity**. The schema is a *decoding
#: constraint*, never an authority on what a CAD document may be: the V1
#: validator remains the only thing that decides that, and it still rejects a
#: zero-or-negative size (S11), an empty feature list, a malformed id (S8) and
#: every other rule regardless of what the model was free to emit. What is
#: lost is a hint, and the prompt states each of these requirements in prose
#: anyway -- so a model that ignores one produces a document the validator
#: refuses, which is exactly the designed behaviour.
UNSUPPORTED_SCHEMA_KEYWORDS = (
    "exclusiveMaximum",
    "exclusiveMinimum",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "pattern",
    "uniqueItems",
)


def schema_for_api(schema: Dict[str, Any]) -> Dict[str, Any]:
    """The schema as this API's structured-output compiler will accept it.

    Returns a copy with every keyword in :data:`UNSUPPORTED_SCHEMA_KEYWORDS`
    removed at every depth. The argument is never mutated, so the caller's
    schema -- and the one the evaluation harness records -- stays whole.

    This lives in the provider because it is knowledge about one vendor's
    schema dialect, and the provider is the only module allowed to hold that.
    Nothing about the CAD contract changes here.
    """
    if isinstance(schema, dict):
        return {
            key: schema_for_api(value)
            for key, value in schema.items()
            if key not in UNSUPPORTED_SCHEMA_KEYWORDS
        }
    if isinstance(schema, list):
        return [schema_for_api(item) for item in schema]  # type: ignore[return-value]
    return schema


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

        The credential is looked up under each name in
        :data:`~cad_ai.config.CREDENTIAL_VARIABLES`, in order; the first one
        present is read here, once, passed to the SDK, and not retained by
        this object or by :class:`~cad_ai.config.AiConfig`.
        """
        sdk = _import_sdk()
        settings = config if config is not None else config_from_environment()
        variable = credential_variable(PROVIDER_NAME)
        if variable is None:
            raise ProviderNotConfigured(
                "the interpretation service is not available",
                detail=(
                    " and ".join(CREDENTIAL_VARIABLES[PROVIDER_NAME])
                    + " are not set"
                ),
            )
        client = sdk.Anthropic(
            api_key=os.environ[variable].strip(),
            timeout=settings.timeout_seconds,
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
            "system": [
                {
                    "type": "text",
                    "text": request.system,
                    "cache_control": dict(CACHE_CONTROL),
                }
            ],
            "messages": [{"role": "user", "content": request.user_text}],
        }
        structured = request.output_schema is not None
        output_config: Dict[str, Any] = {}
        if structured:
            output_config["format"] = {
                "type": JSON_SCHEMA_FORMAT,
                "schema": schema_for_api(dict(request.output_schema or {})),
            }
        if self._config.effort is not None:
            output_config["effort"] = self._config.effort
        if output_config:
            parameters["output_config"] = output_config
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
        if getattr(message, "stop_reason", None) == REFUSAL_STOP_REASON:
            # Checked before the content is read: a refusal is a complete
            # answer with nothing in it to interpret, and whatever text came
            # with it is not a CAD document. The category the API attaches is
            # development detail; only the neutral kind is published.
            details = getattr(message, "stop_details", None)
            raise ProviderError(
                "the interpretation service declined the request",
                detail=(
                    "stop_reason='refusal'; category="
                    f"{getattr(details, 'category', None)!r}"
                ),
                kind=ProviderErrorKind.REFUSED,
            )
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
    """Token counts, when reported. Never a prompt and never a credential.

    A counter the response does not carry is left out rather than reported as
    zero, so "not reported" and "none" stay different things.
    """
    usage = getattr(message, "usage", None)
    counts: Dict[str, int] = {}
    for name in USAGE_FIELDS:
        value = getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[name] = value
    return counts


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = [
    "CACHE_CONTROL",
    "JSON_SCHEMA_FORMAT",
    "REFUSAL_STOP_REASON",
    "SDK_PACKAGE",
    "USAGE_FIELDS",
    "UNSUPPORTED_SCHEMA_KEYWORDS",
    "AnthropicTextToCadModel",
    "decoding_capabilities",
    "schema_for_api",
]
