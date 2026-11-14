"""The OpenAI-compatible provider: open-weight models behind any server that
speaks the Chat Completions HTTP API.

The fifth implementation of :class:`~cad_ai.provider.TextToCadModel`. Ollama,
vLLM, llama.cpp's ``llama-server``, LM Studio and hosted routers such as
OpenRouter all serve ``POST {base_url}/chat/completions`` in the OpenAI shape,
so one small HTTP client reaches every one of them. Selected **only** by
``CAD_AI_PROVIDER=openai_compatible`` -- a base URL being set is not a
decision to send this application's requests to it.

**Standard library only.** ``urllib.request``, ``json`` and ``ssl``: no SDK,
no new dependency, nothing imported lazily because nothing optional is
imported at all. ``urllib`` honours the standard proxy variables
(``HTTPS_PROXY``, ``HTTP_PROXY``, ``NO_PROXY``) for remote servers, and the
TLS context is :func:`ssl.create_default_context`, i.e. the system CA bundle
(and ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` where OpenSSL reads them).

What one :meth:`OpenAICompatibleTextToCadModel.generate` does, and the whole
of it:

1. build one request body -- the system prompt and the user text as two
   messages, the configured model, an output bound, and the answer format the
   configured *answer mode* asks for (below);
2. POST it to ``{base_url}/chat/completions``, once, with the API key -- when
   one is configured -- as a bearer ``Authorization`` header and nowhere else;
3. read the reply (one JSON object, or a server-sent-event stream when
   ``CAD_OPENAI_STREAM=1``), and return its text plus metadata;
4. translate every failure -- HTTP status, refused connection, timeout,
   unreadable reply -- into :class:`~cad_ai.provider.ProviderError` with a
   neutral kind.

The three answer modes (``CAD_OPENAI_ANSWER_MODE``):

* ``json_schema`` (default): ``response_format`` of type ``json_schema``
  carrying the **whole** response schema, unchanged. vLLM, llama.cpp, LM
  Studio and Ollama constrain decoding to it; these are not the Anthropic
  compiler, so :func:`cad_ai.anthropic_provider.schema_for_api` is not
  applied.
* ``tool``: one function tool, :data:`~cad_ai.prompt.ANSWER_TOOL_NAME`, with
  the schema as its parameters, and ``tool_choice`` naming it. The answer is
  that call's ``arguments`` -- a JSON *string* in this API -- parsed and
  re-serialised, never edited. Arguments that do not parse are passed on as
  the raw string, so the generation layer reports them unusable and asks
  again. A reply with no such call falls back to its text.
* ``json_object``: ``response_format`` of type ``json_object`` only, for a
  server without schema support. JSON, but not held to the schema.

What it deliberately does not do: set a temperature, ``top_p``, ``top_k`` or
seed (an open model's decoding settings belong to the server that serves it:
a Modelfile, ``--generation-config``, ``--temp``), follow a redirect (that
would carry the key to another address), retry, repair, strip a code fence,
or interpret the answer. Nothing it returns is executed. The validator
decides validity, as for every provider.

The credential, when there is one, is read once, at construction, from
``CAD_OPENAI_API_KEY``. It is held only inside a closure that adds the
header, so it is not an attribute, not in :func:`repr`, not in any exception
message or ``detail`` -- a server that echoes it back has it replaced before
the text goes anywhere -- and never printed or logged (nothing here prints or
logs).
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from cad_ai.config import (
    MODEL_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    OPENAI_BASE_URL_VARIABLE,
    OPENAI_COMPATIBLE_PROVIDER_NAME,
    AiConfig,
    config_from_environment,
)
from cad_ai.prompt import ANSWER_TOOL_DESCRIPTION, ANSWER_TOOL_NAME
from cad_ai.provider import (
    ModelRequest,
    ModelResponse,
    ProviderError,
    ProviderErrorKind,
    ProviderNotConfigured,
)

#: Appended to the base URL. The base URL is the API root, ending in ``/v1``
#: for every server named above.
CHAT_COMPLETIONS_PATH = "/chat/completions"

#: How the answer is requested. See the module docstring.
ANSWER_MODES: Tuple[str, ...] = ("json_schema", "tool", "json_object")
ANSWER_MODE_VARIABLE = "CAD_OPENAI_ANSWER_MODE"
DEFAULT_ANSWER_MODE = "json_schema"

#: The ``json_schema.name`` sent with the response format. Required by the
#: OpenAI shape; it names the format, and carries no meaning.
RESPONSE_FORMAT_NAME = "cad_answer"

#: ``strict`` on the response format (and on the answer tool's function).
#: ``1`` by default: where a server honours it, it asks for the schema to be
#: followed exactly; vLLM and llama.cpp constrain to the schema either way.
#: ``0`` sends ``false``, for a hosted route whose strict mode refuses this
#: schema.
STRICT_VARIABLE = "CAD_OPENAI_STRICT"

#: ``1``: request a server-sent-event stream instead of one JSON reply. Off by
#: default. Useful when something between the application and the server
#: closes a connection that has been idle for a while: a non-streamed answer
#: sends nothing until it is complete, which for a large part on a local GPU
#: can be many minutes.
STREAM_VARIABLE = "CAD_OPENAI_STREAM"

#: The output bound actually sent. Unset: the generation layer's bound
#: (32000 tokens). An integer: the smaller of the two -- for a server that
#: rejects an output bound which, added to the prompt, exceeds its context
#: (vLLM does). ``none``: send no ``max_tokens`` at all, and let the server use
#: whatever context is left.
MAX_TOKENS_VARIABLE = "CAD_OPENAI_MAX_TOKENS"

#: The words that mean "on" and "off" for the boolean variables above.
_TRUE_WORDS = ("1", "true", "yes", "on")
_FALSE_WORDS = ("0", "false", "no", "off")

#: A reply larger than this is refused rather than held in memory. A complete
#: answer for a 128-feature engine is under 100 kB.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024

#: How much of a server's error body is kept in ``detail``.
DETAIL_EXCERPT_CHARACTERS = 1000

#: Sent as ``User-Agent``. Generic: it names this client, nobody's identity.
USER_AGENT = "text-to-cad-openai-compatible/1"

#: What replaces the API key wherever a server echoed it back.
REDACTED = "[redacted]"

#: The usage counters, mapped onto the names every other provider reports.
USAGE_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("prompt_tokens", "input_tokens"),
    ("completion_tokens", "output_tokens"),
)

#: HTTP status -> the neutral classification. Statuses not listed here are
#: classified by range in :func:`kind_for_status`.
_STATUS_KINDS: Mapping[int, ProviderErrorKind] = {
    400: ProviderErrorKind.INVALID_REQUEST,
    401: ProviderErrorKind.AUTHENTICATION,
    # OpenRouter: the key is out of credit. A quota, like a 429.
    402: ProviderErrorKind.RATE_LIMITED,
    403: ProviderErrorKind.AUTHENTICATION,
    # An unknown model, or a base URL that is not the API root.
    404: ProviderErrorKind.INVALID_REQUEST,
    408: ProviderErrorKind.TIMEOUT,
    413: ProviderErrorKind.INVALID_REQUEST,
    422: ProviderErrorKind.INVALID_REQUEST,
    429: ProviderErrorKind.RATE_LIMITED,
    # A gateway gave up waiting for the model: a timeout, not a shortage.
    504: ProviderErrorKind.TIMEOUT,
}

#: Words in a 400 body that mean "the prompt does not fit the context". Used
#: only to make ``detail`` say so plainly; the kind is INVALID_REQUEST either
#: way. Matched case-insensitively against the server's own message.
_CONTEXT_WORDS: Tuple[str, ...] = (
    "context length",
    "context size",
    "context window",
    "maximum context",
    "max_model_len",
    "n_ctx",
    "exceed",
    "too long",
    "too many tokens",
    "max_tokens",
)

#: Said in ``detail`` when a server reports the prompt and output bound do
#: not fit its context.
CONTEXT_ADVICE = (
    "the prompt and the output bound do not fit the server's context: raise "
    "the model's context length (at least 32768 tokens; see "
    "docs/text-to-cad-ai.md) or lower " + MAX_TOKENS_VARIABLE
)


def kind_for_status(status: int) -> ProviderErrorKind:
    """The neutral kind for one HTTP status. Numbers only; no prose parsed."""
    if status in _STATUS_KINDS:
        return _STATUS_KINDS[status]
    if 300 <= status < 400:
        # Redirects are not followed (see _NoRedirect): a wrong base URL.
        return ProviderErrorKind.INVALID_REQUEST
    if 500 <= status < 600:
        return ProviderErrorKind.CAPACITY
    if 400 <= status < 500:
        return ProviderErrorKind.INVALID_REQUEST
    return ProviderErrorKind.OTHER


def normalise_base_url(value: str) -> str:
    """The base URL, checked, without a trailing slash.

    Refused -- as :class:`~cad_ai.provider.ProviderNotConfigured` -- when it
    is not ``http``/``https`` (``urllib`` would happily open ``file:`` or
    ``ftp:``), has no host, carries a user name or password (the key belongs
    in ``CAD_OPENAI_API_KEY``, where it is kept out of every message), carries
    a query or fragment, or already ends in ``/chat/completions`` (the base
    URL is the API root; the path is appended here).
    """
    text = (value or "").strip()
    if not text:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"{OPENAI_BASE_URL_VARIABLE} is not set",
        )
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"{OPENAI_BASE_URL_VARIABLE} is not a valid URL",
        ) from exc
    del port
    problem: Optional[str] = None
    if parts.scheme.lower() not in ("http", "https"):
        problem = "must start with http:// or https://"
    elif not parts.hostname:
        problem = "names no host"
    elif parts.username is not None or parts.password is not None:
        problem = (
            "must not contain a user name or password; put an API key in "
            f"{OPENAI_API_KEY_VARIABLE}"
        )
    elif parts.query or parts.fragment:
        problem = "must not contain a query or a fragment"
    elif parts.path.rstrip("/").endswith(CHAT_COMPLETIONS_PATH):
        problem = (
            "is the API root (for example http://localhost:11434/v1), "
            f"without {CHAT_COMPLETIONS_PATH}"
        )
    if problem is not None:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"{OPENAI_BASE_URL_VARIABLE} {problem}",
        )
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc, parts.path.rstrip("/"), "", "")
    )


def _is_loopback(host: str) -> bool:
    """Whether ``host`` names this machine. A proxy can never reach it."""
    name = host.strip("[]").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    raise ProviderNotConfigured(
        "the interpretation service is not available",
        detail=f"{name} must be 1 or 0",
    )


def _max_tokens_setting(environ: Mapping[str, str]) -> Tuple[Optional[int], bool]:
    """``(limit, omit)`` from :data:`MAX_TOKENS_VARIABLE`."""
    raw = environ.get(MAX_TOKENS_VARIABLE, "").strip().lower()
    if not raw:
        return None, False
    if raw == "none":
        return None, True
    try:
        limit = int(raw)
    except ValueError:
        limit = 0
    if limit < 1:
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"{MAX_TOKENS_VARIABLE} must be an integer >= 1 or 'none'",
        )
    return limit, False


def _credential(api_key: Optional[str]) -> Tuple[
    Callable[[Dict[str, str]], None], Callable[[str], str]
]:
    """Two closures over the key: one adds the header, one scrubs text.

    The key lives only in these closures' cells. It is not an attribute of
    the provider, so ``vars()``, ``repr()`` and a debugger's object view do not
    show it.
    """
    key = (api_key or "").strip()
    if not key:
        return (lambda headers: None), (lambda text: text)
    if any(ord(character) < 32 or ord(character) == 127 for character in key):
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=f"{OPENAI_API_KEY_VARIABLE} contains a control character",
        )
    try:
        key.encode("latin-1")
    except UnicodeEncodeError:
        # `from None`: the encoding error's own text quotes the key.
        raise ProviderNotConfigured(
            "the interpretation service is not available",
            detail=(
                f"{OPENAI_API_KEY_VARIABLE} contains characters an HTTP "
                "header cannot carry"
            ),
        ) from None
    header = "Bearer " + key

    def authorize(headers: Dict[str, str]) -> None:
        headers["Authorization"] = header

    def redact(text: str) -> str:
        return text.replace(key, REDACTED)

    return authorize, redact


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Follow no redirect. urllib would otherwise re-send the
    ``Authorization`` header to whatever address the server named. The 3xx
    then surfaces as an ``HTTPError`` and is reported as a configuration
    problem."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


@dataclass
class _Completion:
    """One reply, reduced to what is used. Built from either reply shape."""

    content: str = ""
    refusal: str = ""
    finish_reason: Optional[str] = None
    model: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    #: The answer tool's arguments: a string (the API's form) or, from a
    #: server that sends them decoded, an object. ``None``: no such call.
    tool_arguments: Any = None


class OpenAICompatibleTextToCadModel:
    """A :class:`~cad_ai.provider.TextToCadModel` over Chat Completions."""

    name = OPENAI_COMPATIBLE_PROVIDER_NAME

    def __init__(
        self,
        config: AiConfig,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        answer_mode: str = DEFAULT_ANSWER_MODE,
        strict: bool = True,
        stream: bool = False,
        max_tokens_limit: Optional[int] = None,
        omit_max_tokens: bool = False,
    ) -> None:
        if answer_mode not in ANSWER_MODES:
            raise ProviderNotConfigured(
                "the interpretation service is not available",
                detail=(
                    f"{ANSWER_MODE_VARIABLE} must be one of "
                    f"{', '.join(ANSWER_MODES)}"
                ),
            )
        if not isinstance(config.model, str) or not config.model.strip():
            raise ProviderNotConfigured(
                "the interpretation service is not available",
                detail=(
                    f"{MODEL_VARIABLE} is required for the "
                    f"{OPENAI_COMPATIBLE_PROVIDER_NAME} provider: there is no "
                    "default open model. Name the model exactly as the server "
                    "lists it (GET {base_url}/models), e.g. qwen3:32b for "
                    "Ollama or Qwen/Qwen3-32B for vLLM"
                ),
            )
        if max_tokens_limit is not None and max_tokens_limit < 1:
            raise ValueError("max_tokens_limit must be >= 1")
        self._config = config
        self._base_url = normalise_base_url(base_url)
        self._endpoint = self._base_url + CHAT_COMPLETIONS_PATH
        parts = urllib.parse.urlsplit(self._base_url)
        self._loopback = _is_loopback(parts.hostname or "")
        self._authorize, self._redact = _credential(api_key)
        self._answer_mode = answer_mode
        self._strict = bool(strict)
        self._stream = bool(stream)
        self._max_tokens_limit = max_tokens_limit
        self._omit_max_tokens = bool(omit_max_tokens)

    @classmethod
    def from_environment(
        cls, config: Optional[AiConfig] = None
    ) -> "OpenAICompatibleTextToCadModel":
        """Build a provider from the environment.

        Raises :class:`~cad_ai.provider.ProviderNotConfigured` when
        ``CAD_OPENAI_BASE_URL`` is unset or unusable, when no model is named
        (``CAD_AI_MODEL``; there is no default), or when an optional setting
        has a value it cannot use. Contacts nothing.

        ``CAD_OPENAI_API_KEY`` is optional -- a local server needs none. When
        present it is read here, once, and kept only inside the closure that
        adds the ``Authorization`` header.
        """
        settings = config if config is not None else config_from_environment()
        environ = os.environ
        mode = (
            environ.get(ANSWER_MODE_VARIABLE, "").strip().lower()
            or DEFAULT_ANSWER_MODE
        )
        limit, omit = _max_tokens_setting(environ)
        return cls(
            settings,
            base_url=environ.get(OPENAI_BASE_URL_VARIABLE, ""),
            api_key=environ.get(OPENAI_API_KEY_VARIABLE),
            answer_mode=mode,
            strict=_flag(environ, STRICT_VARIABLE, True),
            stream=_flag(environ, STREAM_VARIABLE, False),
            max_tokens_limit=limit,
            omit_max_tokens=omit,
        )

    def __repr__(self) -> str:
        # Deliberately written out: no credential, and nothing that holds one.
        return (
            f"{type(self).__name__}(model={self._config.model!r}, "
            f"base_url={self._base_url!r}, answer_mode={self._answer_mode!r}, "
            f"stream={self._stream!r})"
        )

    @property
    def config(self) -> AiConfig:
        return self._config

    @property
    def answer_mode(self) -> str:
        return self._answer_mode

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def stream(self) -> bool:
        return self._stream

    # --- the request ---------------------------------------------------------

    def request_body(self, request: ModelRequest) -> Dict[str, Any]:
        """The exact JSON body one :meth:`generate` sends. Contacts nothing."""
        body: Dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user_text},
            ],
        }
        if not self._omit_max_tokens:
            bound = int(request.max_output_tokens)
            if self._max_tokens_limit is not None:
                bound = min(bound, self._max_tokens_limit)
            body["max_tokens"] = bound
        schema = request.output_schema
        if schema is not None:
            if self._answer_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": RESPONSE_FORMAT_NAME,
                        "schema": dict(schema),
                        "strict": self._strict,
                    },
                }
            elif self._answer_mode == "tool":
                body["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": ANSWER_TOOL_NAME,
                            "description": ANSWER_TOOL_DESCRIPTION,
                            "parameters": dict(schema),
                            "strict": self._strict,
                        },
                    }
                ]
                body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": ANSWER_TOOL_NAME},
                }
            else:
                body["response_format"] = {"type": "json_object"}
        if self._config.effort is not None:
            # Only when configured. Ollama and llama.cpp document the field;
            # a server that does not know it may ignore or refuse it.
            body["reasoning_effort"] = self._config.effort
        if self._stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return body

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if self._stream else "application/json",
            "User-Agent": USER_AGENT,
        }
        self._authorize(headers)
        return headers

    def _opener(self) -> urllib.request.OpenerDirector:
        handlers: List[urllib.request.BaseHandler] = [
            _NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        ]
        if self._loopback:
            # A server on this machine is reached directly even when a proxy
            # is configured for everything else: no proxy can reach it.
            handlers.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*handlers)

    # --- one call ------------------------------------------------------------

    def generate(self, request: ModelRequest) -> ModelResponse:
        """One HTTP request. No retry, no repair."""
        body = self.request_body(request)
        data = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(
            self._endpoint, data=data, headers=self._headers(), method="POST"
        )
        timeout = float(self._config.timeout_seconds)
        deadline = time.monotonic() + timeout
        try:
            with self._opener().open(http_request, timeout=timeout) as reply:
                if self._stream:
                    completion = self._read_stream(reply, deadline)
                else:
                    completion = self._read_whole(reply, deadline)
        except ProviderError:
            raise
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from exc
        except urllib.error.URLError as exc:
            # urllib wraps a failure to connect (refused, unresolvable,
            # unreachable, TLS) in URLError.
            raise self._transport_error(exc.reason, connected=False) from exc
        except (OSError, http.client.HTTPException) as exc:
            # Raised after the connection was made: dropped, reset, cut off.
            raise self._transport_error(exc, connected=True) from exc
        except ValueError as exc:
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=self._clean(f"{type(exc).__name__}: {exc}"),
                kind=ProviderErrorKind.OTHER,
            ) from exc
        return self._response(completion, schema_sent=request.output_schema is not None)

    # --- reading a reply -----------------------------------------------------

    def _read_whole(self, reply: Any, deadline: float) -> _Completion:
        chunks: List[bytes] = []
        size = 0
        while True:
            self._check_deadline(deadline)
            chunk = reply.read(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise self._oversized()
            chunks.append(chunk)
        raw = b"".join(chunks)
        declared = _declared_length(reply)
        if declared is not None and len(raw) < declared:
            # http.client returns a short body without complaint when the
            # connection closes early; a cut-off reply is a transport failure.
            raise ProviderError(
                "the connection to the model server failed",
                detail=(
                    f"the connection closed after {len(raw)} of {declared} "
                    "bytes of the reply"
                ),
                kind=ProviderErrorKind.TIMEOUT,
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProviderError(
                "the interpretation service returned an unreadable reply",
                detail=self._clean(
                    "the server's reply is not JSON (is the base URL the API "
                    "root, ending in /v1?): " + _excerpt(raw)
                ),
                kind=ProviderErrorKind.OTHER,
            ) from None
        if not isinstance(payload, dict):
            raise ProviderError(
                "the interpretation service returned an unreadable reply",
                detail="the server's reply is not a JSON object",
                kind=ProviderErrorKind.OTHER,
            )
        self._raise_for_body_error(payload)
        self._raise_for_truncated_prompt(payload)
        completion = _Completion(
            model=_optional_str(payload.get("model")),
            usage=_usage_of(payload.get("usage")),
        )
        choice = _first_choice(payload)
        if choice is None:
            return completion
        message = choice.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            completion.content = content
        elif isinstance(content, list):
            # Some servers send content parts; only text parts are text.
            completion.content = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        refusal = message.get("refusal")
        if isinstance(refusal, str):
            completion.refusal = refusal
        completion.finish_reason = _optional_str(choice.get("finish_reason"))
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if (
                isinstance(function, dict)
                and function.get("name") == ANSWER_TOOL_NAME
            ):
                completion.tool_arguments = function.get("arguments", "")
                break
        return completion

    def _read_stream(self, reply: Any, deadline: float) -> _Completion:
        """Reassemble a server-sent-event stream into one completion.

        Content deltas are concatenated; tool-call argument fragments are
        concatenated per call ``index``. Nothing is interpreted before the
        stream has ended. A stream that stops before either a
        ``finish_reason`` or ``[DONE]`` is a transport failure, not an answer.
        """
        completion = _Completion()
        content: List[str] = []
        refusal: List[str] = []
        calls: Dict[int, Dict[str, Any]] = {}
        data_lines: List[str] = []
        size = 0
        done = False

        def dispatch() -> bool:
            if not data_lines:
                return False
            data = "\n".join(data_lines)
            data_lines.clear()
            if data.strip() == "[DONE]":
                return True
            try:
                event = json.loads(data)
            except ValueError:
                raise ProviderError(
                    "the interpretation service returned an unreadable reply",
                    detail=self._clean(
                        "a stream event is not JSON: " + _excerpt(data.encode())
                    ),
                    kind=ProviderErrorKind.OTHER,
                ) from None
            if not isinstance(event, dict):
                return False
            self._raise_for_body_error(event)
            self._raise_for_truncated_prompt(event)
            if isinstance(event.get("model"), str) and event["model"]:
                completion.model = event["model"]
            usage = _usage_of(event.get("usage"))
            if usage:
                completion.usage = usage
            choice = _first_choice(event)
            if choice is None:
                return False
            delta = choice.get("delta")
            delta = delta if isinstance(delta, dict) else {}
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            if isinstance(delta.get("refusal"), str):
                refusal.append(delta["refusal"])
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                index = call.get("index", 0)
                index = index if isinstance(index, int) else 0
                entry = calls.setdefault(index, {"name": "", "parts": [], "object": None})
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                if isinstance(name, str) and name and not entry["name"]:
                    entry["name"] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    entry["parts"].append(arguments)
                elif isinstance(arguments, (dict, list)):
                    entry["object"] = arguments
            if choice.get("finish_reason"):
                completion.finish_reason = str(choice["finish_reason"])
            return False

        while not done:
            self._check_deadline(deadline)
            raw_line = reply.readline()
            if not raw_line:
                break
            size += len(raw_line)
            if size > MAX_RESPONSE_BYTES:
                raise self._oversized()
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                done = dispatch()
                continue
            if line.startswith(":"):
                continue  # a comment: a keep-alive (OpenRouter sends these)
            name, _, value = line.partition(":")
            if name == "data":
                data_lines.append(value[1:] if value.startswith(" ") else value)
        if not done:
            done = dispatch()
        if not done and completion.finish_reason is None:
            raise ProviderError(
                "the interpretation service is unavailable",
                detail="the stream ended before the answer was complete",
                kind=ProviderErrorKind.TIMEOUT,
            )
        completion.content = "".join(content)
        completion.refusal = "".join(refusal)
        for index in sorted(calls):
            entry = calls[index]
            if entry["name"] == ANSWER_TOOL_NAME:
                completion.tool_arguments = (
                    entry["object"]
                    if entry["object"] is not None
                    else "".join(entry["parts"])
                )
                break
        return completion

    # --- turning a reply into a response -------------------------------------

    def _response(self, completion: _Completion, *, schema_sent: bool) -> ModelResponse:
        text = ""
        if completion.tool_arguments is not None:
            text = _tool_answer_text(completion.tool_arguments)
        if not text.strip():
            text = completion.content
        if not text.strip():
            if completion.refusal.strip() or completion.finish_reason == "content_filter":
                raise ProviderError(
                    "the interpretation service declined the request",
                    detail=(
                        f"finish_reason={completion.finish_reason!r}; the "
                        "model returned a refusal"
                    ),
                    kind=ProviderErrorKind.REFUSED,
                )
            raise ProviderError(
                "the interpretation service returned no answer",
                detail=(
                    "no text content in the response; finish_reason="
                    f"{completion.finish_reason!r}"
                ),
                kind=ProviderErrorKind.EMPTY_RESPONSE,
            )
        # finish_reason "length": the text is returned as it is, cut off. The
        # generation layer cannot parse it and asks again; nothing here
        # completes or repairs it.
        return ModelResponse(
            text=text,
            provider=self.name,
            model=completion.model or self._config.model,
            structured_output=schema_sent and self._answer_mode == "json_schema",
            stop_reason=completion.finish_reason,
            usage=dict(completion.usage),
        )

    # --- failures ------------------------------------------------------------

    def _clean(self, text: str) -> str:
        """``text`` with the API key, if any, replaced."""
        return self._redact(text)

    def _check_deadline(self, deadline: float) -> None:
        if time.monotonic() > deadline:
            raise ProviderError(
                "the interpretation service did not answer in time",
                detail=(
                    "no complete answer within "
                    f"{self._config.timeout_seconds:g} s (CAD_AI_TIMEOUT_SECONDS)"
                ),
                kind=ProviderErrorKind.TIMEOUT,
            )

    def _oversized(self) -> ProviderError:
        return ProviderError(
            "the interpretation service returned an unreadable reply",
            detail=f"the reply exceeded {MAX_RESPONSE_BYTES} bytes",
            kind=ProviderErrorKind.OTHER,
        )

    def _http_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        status = int(getattr(exc, "code", 0) or 0)
        try:
            body = exc.read(DETAIL_EXCERPT_CHARACTERS * 4) or b""
        except Exception:  # an unreadable error body is still an error
            body = b""
        kind = kind_for_status(status)
        excerpt = _excerpt(body)
        detail = f"HTTP {status} from the model server: {excerpt}"
        if 300 <= status < 400:
            detail = (
                f"HTTP {status}: the server redirected, and redirects are not "
                "followed (they would carry the API key elsewhere); set "
                f"{OPENAI_BASE_URL_VARIABLE} to the final address"
            )
        elif status == 404:
            detail = (
                f"HTTP 404 from the model server (an unknown model -- check "
                f"{MODEL_VARIABLE} against GET {{base_url}}/models -- or a "
                "base URL that is not the API root, usually ending in /v1): "
                + excerpt
            )
        elif status in (400, 413, 422) and _mentions_context(excerpt):
            detail = f"HTTP {status}: {CONTEXT_ADVICE}. The server said: {excerpt}"
        return ProviderError(
            _public_message(kind), detail=self._clean(detail), kind=kind
        )

    def _transport_error(self, reason: Any, *, connected: bool) -> ProviderError:
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            return ProviderError(
                "the interpretation service did not answer in time",
                detail=self._clean(
                    "no answer within "
                    f"{self._config.timeout_seconds:g} s (CAD_AI_TIMEOUT_SECONDS): "
                    f"{type(reason).__name__}"
                ),
                kind=ProviderErrorKind.TIMEOUT,
            )
        if isinstance(reason, ssl.SSLError):
            return ProviderError(
                "the interpretation service is unavailable",
                detail=self._clean(
                    f"TLS failed for {self._base_url}: {type(reason).__name__}: "
                    f"{reason}"
                ),
                kind=ProviderErrorKind.INVALID_REQUEST,
            )
        # Refused, reset, unresolvable, unreachable: the server is not there
        # (or not yet), or it dropped the connection. Classified TIMEOUT --
        # "the transport failed" -- as the Anthropic provider classifies its
        # own connection errors: it is transient (a local server may still be
        # starting), and it says nothing about the model.
        if connected:
            return ProviderError(
                "the connection to the model server failed",
                detail=self._clean(
                    f"the connection to {self._base_url} failed during the "
                    f"call: {type(reason).__name__}: {reason}"
                ),
                kind=ProviderErrorKind.TIMEOUT,
            )
        return ProviderError(
            "the model server is not reachable at the configured base URL",
            detail=self._clean(
                f"could not reach {self._base_url}: {type(reason).__name__}: "
                f"{reason}"
            ),
            kind=ProviderErrorKind.TIMEOUT,
        )

    def _raise_for_body_error(self, payload: Mapping[str, Any]) -> None:
        """A 200 reply (or a stream event) that carries an ``error`` object.

        OpenRouter reports some upstream failures this way, and a stream that
        fails after its headers were sent can only report it in an event.
        """
        error = payload.get("error")
        if error is None or payload.get("choices"):
            return
        code = error.get("code") if isinstance(error, dict) else None
        status = code if isinstance(code, int) and not isinstance(code, bool) else 0
        kind = kind_for_status(status) if status else ProviderErrorKind.OTHER
        text = json.dumps(error)[: DETAIL_EXCERPT_CHARACTERS]
        detail = f"the model server reported an error: {text}"
        if _mentions_context(text):
            kind = ProviderErrorKind.INVALID_REQUEST
            detail = f"{CONTEXT_ADVICE}. The server said: {text}"
        raise ProviderError(_public_message(kind), detail=self._clean(detail), kind=kind)

    def _raise_for_truncated_prompt(self, payload: Mapping[str, Any]) -> None:
        """A server that *reports* it cut the input to fit its context.

        Only an explicit report counts (llama.cpp's ``truncated: true``); no
        token count is second-guessed. Ollama's OpenAI-compatible endpoint
        truncates silently and reports nothing -- see the docs.
        """
        if payload.get("truncated") is True:
            raise ProviderError(
                "the interpretation service is unavailable",
                detail=f"the server reports the input was truncated; {CONTEXT_ADVICE}",
                kind=ProviderErrorKind.INVALID_REQUEST,
            )


def _public_message(kind: ProviderErrorKind) -> str:
    if kind is ProviderErrorKind.TIMEOUT:
        return "the interpretation service did not answer in time"
    return "the interpretation service is unavailable"


def _tool_answer_text(arguments: Any) -> str:
    """The answer tool's arguments as text: parsed and re-serialised, or raw.

    The API sends ``arguments`` as a JSON string. Parsed and dumped again it
    is the same object, so the generation layer sees what the model wrote.
    When it does not parse, the raw string is passed on unchanged -- the
    generation layer then reports it unusable and asks again. Nothing is
    repaired.
    """
    if isinstance(arguments, (dict, list)):
        return json.dumps(arguments)
    if not isinstance(arguments, str):
        return ""
    try:
        return json.dumps(json.loads(arguments))
    except ValueError:
        return arguments


def _first_choice(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    return first if isinstance(first, dict) else None


def _usage_of(usage: Any) -> Dict[str, int]:
    """Token counts, under the names every provider reports. A counter the
    reply does not carry is left out rather than reported as zero."""
    counts: Dict[str, int] = {}
    if not isinstance(usage, dict):
        return counts
    for source, target in USAGE_FIELDS:
        value = usage.get(source)
        if isinstance(value, int) and not isinstance(value, bool):
            counts[target] = value
    return counts


def _mentions_context(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in _CONTEXT_WORDS)


def _excerpt(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    if len(text) > DETAIL_EXCERPT_CHARACTERS:
        text = text[:DETAIL_EXCERPT_CHARACTERS] + "..."
    return text or "(empty body)"


def _declared_length(reply: Any) -> Optional[int]:
    headers = getattr(reply, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def decoding_capabilities() -> Dict[str, str]:
    """What this provider sets, for a run's recorded settings.

    The wire format has ``temperature``, ``top_p`` and ``seed``, and
    most servers honour them; this provider sends none, and leaves decoding
    to the server's own configuration of the model.
    """
    return {
        "temperature": "settable",
        "top_p": "settable",
        "top_k": "server-specific",
        "seed": "settable",
        "deterministic_decoding": "server-dependent",
        "sdk_version": "none (standard library HTTP)",
        "decoding_controls_used": "none",
    }


__all__ = [
    "ANSWER_MODES",
    "ANSWER_MODE_VARIABLE",
    "CHAT_COMPLETIONS_PATH",
    "CONTEXT_ADVICE",
    "DEFAULT_ANSWER_MODE",
    "MAX_RESPONSE_BYTES",
    "MAX_TOKENS_VARIABLE",
    "REDACTED",
    "RESPONSE_FORMAT_NAME",
    "STREAM_VARIABLE",
    "STRICT_VARIABLE",
    "USAGE_FIELDS",
    "OpenAICompatibleTextToCadModel",
    "decoding_capabilities",
    "kind_for_status",
    "normalise_base_url",
]
