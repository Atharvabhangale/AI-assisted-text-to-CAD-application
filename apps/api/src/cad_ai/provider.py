"""The LLM provider boundary: one tiny interface, no vendor types.

Everything above this module -- the prompt, the interpretation, the validation
and the result -- is written against :class:`TextToCadModel` and the two plain
records below. No vendor SDK type crosses this line, so replacing the provider
means writing one class, not editing the application.

There is deliberately **no registry and no plugin system**: exactly one
provider is implemented (:mod:`cad_ai.anthropic_provider`), and a second one
would be a second class, chosen by configuration.

What a model is allowed to do here is the whole point:

* it receives a system prompt and one user message;
* it returns **text**, which the caller expects to be a JSON CAD document;
* it gets **no tools** -- no shell, no Python, no filesystem, no HTTP, no CAD
  kernel, no function calling of any kind. The interface has nowhere to put
  them, which is why it has no such parameter;
* nothing it returns is executed. Ever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Tuple, runtime_checkable


class ProviderErrorKind(Enum):
    """Why a provider call produced no answer. Vendor-neutral.

    A benchmark has to tell "the model answered badly" from "the model never
    answered", and among the latter it has to tell a rate limit from a
    capacity shortage from a broken configuration -- because those say
    completely different things about a run. This is that classification, and
    it is deliberately coarse: each provider maps its own error space onto it,
    and no vendor status code crosses the boundary.
    """

    #: The request was refused for exceeding a quota or request rate (429).
    RATE_LIMITED = "rate_limited"

    #: The model or service was temporarily unable to serve (503, overload).
    CAPACITY = "capacity"

    #: The request exceeded the configured timeout, or the transport failed.
    TIMEOUT = "timeout"

    #: The credential was rejected (401/403).
    AUTHENTICATION = "authentication"

    #: The request or the configuration was rejected (400, 404, unknown
    #: model). A caller's fault, not a transient one.
    INVALID_REQUEST = "invalid_request"

    #: The provider answered, but with nothing usable in it.
    EMPTY_RESPONSE = "empty_response"

    #: Nothing was attempted: no SDK or no credential.
    NOT_CONFIGURED = "not_configured"

    #: Anything else. Recorded rather than forced into a bucket that would
    #: misdescribe it.
    OTHER = "other"


#: The kinds that mean "try later, the request itself was fine". Separated
#: because a run full of these has measured nothing about the model, while a
#: run full of INVALID_REQUEST has measured a broken configuration.
TRANSIENT_KINDS: Tuple[ProviderErrorKind, ...] = (
    ProviderErrorKind.RATE_LIMITED,
    ProviderErrorKind.CAPACITY,
    ProviderErrorKind.TIMEOUT,
)


class ProviderError(Exception):
    """The provider could not return a usable response.

    Raised for a transport failure, an authentication failure, a rate limit, a
    refusal, an empty completion -- anything that means "there is no model
    output to interpret".

    :attr:`message` is safe to show a caller. :attr:`detail` is for
    development only and is **never** part of a public message: it may name
    the provider's own exception class, which is internal information.

    :attr:`kind` is a vendor-neutral classification, safe to publish: it names
    a category, never a provider's own status text.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: Optional[str] = None,
        kind: "ProviderErrorKind" = None,  # type: ignore[assignment]
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.kind = kind if kind is not None else ProviderErrorKind.OTHER

    @property
    def transient(self) -> bool:
        """Whether retrying later could plausibly succeed. Nothing retries."""
        return self.kind in TRANSIENT_KINDS


class ProviderNotConfigured(ProviderError):
    """No credential or no SDK, so no provider exists to call.

    Distinguished from a call that failed, because nothing was attempted. The
    tests use it to prove that no network call happens without configuration.
    """

    def __init__(
        self,
        message: str,
        *,
        detail: Optional[str] = None,
        kind: "ProviderErrorKind" = None,  # type: ignore[assignment]
    ) -> None:
        super().__init__(
            message,
            detail=detail,
            kind=kind or ProviderErrorKind.NOT_CONFIGURED,
        )


@dataclass(frozen=True)
class ModelRequest:
    """What the application asks a model for. Text in, text out.

    A plain record, so a fake provider in a test is three lines. There is no
    field for tools, for code execution, for a file, for a URL or for a
    conversation history: this stage is a single-shot interpretation, and a
    second turn is a later stage's decision.
    """

    #: The instructions. One place, versioned -- see :mod:`cad_ai.prompt`.
    system: str

    #: The user's natural-language description, unmodified.
    user_text: str

    #: A JSON Schema the provider may use to constrain the output, when the
    #: real API supports it. Advisory: the caller validates regardless.
    output_schema: Optional[Mapping[str, Any]] = None

    #: An upper bound on response length, so a runaway generation is bounded.
    max_output_tokens: int = 4096


@dataclass(frozen=True)
class ModelResponse:
    """What a model returned. Text, plus metadata worth recording.

    :attr:`text` is **not** a CAD document. It is a string that the
    interpretation layer will try to read as one, and which may be anything at
    all -- prose, Python, an apology, or nothing. Treating it as CAD before it
    has been through the existing deserializer and validator is exactly the
    mistake this whole layer exists to prevent.
    """

    text: str

    #: Which provider answered, e.g. ``"anthropic"``.
    provider: str

    #: The model identifier the provider reports having used.
    model: str

    #: Whether the provider constrained the output with :attr:`
    #: ModelRequest.output_schema`. Recorded because it changes how much the
    #: text can be trusted to at least *parse*.
    structured_output: bool = False

    #: Why the model stopped, as the provider names it. Free-form and
    #: provider-specific; metadata only, never a decision input.
    stop_reason: Optional[str] = None

    #: Token counts, when the provider reports them. Never a prompt or a
    #: credential.
    usage: Mapping[str, int] = field(default_factory=dict)


@runtime_checkable
class TextToCadModel(Protocol):
    """A model that turns a request into a response, and nothing more.

    One method. A provider implementation owns its SDK, its credential
    handling and its error translation, and exposes none of them.
    """

    #: A short provider name, for metadata.
    name: str

    def generate(self, request: ModelRequest) -> ModelResponse:
        """Answer ``request``, or raise :class:`ProviderError`.

        Must not raise a vendor exception type: a provider translates its own
        failures so that nothing above this boundary imports its SDK to catch
        them.
        """
        ...


__all__ = [
    "TRANSIENT_KINDS",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderNotConfigured",
    "TextToCadModel",
]
