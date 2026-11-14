"""The OpenAI-compatible provider, proved against a fake server -- never a real one.

**No test in this file contacts a real model server.** Every request goes to
a small HTTP server started by the test on ``127.0.0.1`` at an ephemeral
port, in a thread. It records each request -- path, headers, JSON body -- and
answers with whatever the test queued: a JSON reply, a server-sent-event
stream, an HTTP error, a redirect, a reply that never comes, or a connection
dropped half-way.

What this establishes: the exact request body per answer mode (the response
schema sent unchanged, no decoding controls, the key only in the
``Authorization`` header and only when configured), how the answer is taken
in each mode (content; the tool call's arguments parsed from their JSON
string, or passed on raw when they do not parse), a cut-off answer, usage
mapping, every error mapping (including a refused connection on a closed port
and a timeout), streaming reassembly, configuration refusals, that the
provider is never selected automatically, and that no credential value
reaches an exception or a ``repr``.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import threading
import time
import unittest
import unittest.mock as mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cad_ai.config import (
    AUTOMATIC_PROVIDER_NAMES,
    DEFAULT_MODELS,
    GEMINI_API_KEY_VARIABLE,
    MODEL_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    OPENAI_BASE_URL_VARIABLE,
    OPENAI_COMPATIBLE_PROVIDER_NAME,
    PROVIDER_NAMES,
    PROVIDER_VARIABLE,
    AiConfig,
    AiConfigurationError,
    config_from_environment,
    credential_available,
    resolve_provider,
)
from cad_ai.factory import model_from_environment
from cad_ai.openai_compatible_provider import (
    ANSWER_MODE_VARIABLE,
    CONTEXT_ADVICE,
    MAX_TOKENS_VARIABLE,
    REDACTED,
    RESPONSE_FORMAT_NAME,
    STREAM_VARIABLE,
    STRICT_VARIABLE,
    OpenAICompatibleTextToCadModel,
    kind_for_status,
    normalise_base_url,
)
from cad_ai.prompt import ANSWER_TOOL_DESCRIPTION, ANSWER_TOOL_NAME
from cad_ai.provider import (
    ModelRequest,
    ProviderError,
    ProviderErrorKind,
    ProviderNotConfigured,
    TextToCadModel,
)
from cad_ai.specification import response_schema

AI_SOURCE = Path(__file__).resolve().parents[1] / "src" / "cad_ai"
MODULE = AI_SOURCE / "openai_compatible_provider.py"

#: Obviously synthetic. Never a real key; asserted never to leak.
SYNTHETIC_KEY = "sk-or-SYNTHETIC-TEST-KEY-DO-NOT-USE-000"

SYSTEM_TEXT = "SYSTEM-PROMPT-SENTINEL: answer with JSON."
USER_TEXT = "a 100 x 60 x 10 mm plate -- USER-TEXT-SENTINEL"
MODEL = "qwen3:32b"

ANSWER: Dict[str, Any] = {
    "status": "document",
    "plan": "one box",
    "assumptions": [],
    "document": {"schema_version": "1.2.0", "units": "mm", "features": []},
    "summary": "a plate",
    "questions": [],
    "issues": [],
}

#: Every variable this provider (or urllib) reads, cleared around each test so
#: the developer's own environment cannot change an outcome.
CLEARED_VARIABLES = (
    OPENAI_BASE_URL_VARIABLE,
    OPENAI_API_KEY_VARIABLE,
    ANSWER_MODE_VARIABLE,
    STRICT_VARIABLE,
    STREAM_VARIABLE,
    MAX_TOKENS_VARIABLE,
    PROVIDER_VARIABLE,
    MODEL_VARIABLE,
    "CAD_AI_EFFORT",
)


# --- the fake server ---------------------------------------------------------


Reply = Callable[[BaseHTTPRequestHandler], None]


def json_reply(payload: Any, status: int = 200) -> Reply:
    def send(handler: BaseHTTPRequestHandler) -> None:
        body = (payload if isinstance(payload, bytes) else json.dumps(payload).encode())
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    return send


def sse_reply(events: List[Any], *, done: bool = True, comments: bool = False) -> Reply:
    """A server-sent-event stream: one ``data:`` line per event."""

    def send(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        for event in events:
            if comments:
                handler.wfile.write(b": OPENROUTER PROCESSING\n\n")
            text = event if isinstance(event, str) else json.dumps(event)
            handler.wfile.write(f"data: {text}\n\n".encode())
            handler.wfile.flush()
        if done:
            handler.wfile.write(b"data: [DONE]\n\n")
        handler.wfile.flush()
        handler.close_connection = True

    return send


def redirect_reply(location: str, status: int = 302) -> Reply:
    def send(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(status)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    return send


def slow_reply(seconds: float) -> Reply:
    def send(handler: BaseHTTPRequestHandler) -> None:
        time.sleep(seconds)
        try:
            json_reply(completion("late"))(handler)
        except OSError:
            pass

    return send


def dropped_reply() -> Reply:
    """Headers promise a body that never fully arrives."""

    def send(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", "5000")
        handler.end_headers()
        handler.wfile.write(b'{"choices": [')
        handler.wfile.flush()
        handler.close_connection = True

    return send


def completion(
    content: Any = None,
    *,
    finish: Optional[str] = "stop",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    usage: Optional[Dict[str, Any]] = None,
    model: Optional[str] = "served-model-name",
    refusal: Optional[str] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    if refusal is not None:
        message["refusal"] = refusal
    payload: Dict[str, Any] = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if model is not None:
        payload["model"] = model
    if usage is not None:
        payload["usage"] = usage
    return payload


def tool_call(arguments: Any, name: str = ANSWER_TOOL_NAME) -> Dict[str, Any]:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class FakeServer:
    """A recording HTTP server on 127.0.0.1, one queued reply per request."""

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.replies: List[Reply] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = None
                owner.requests.append(
                    {
                        "method": "POST",
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                reply = owner.replies.pop(0) if owner.replies else json_reply(
                    {"error": "no reply queued"}, 500
                )
                reply(self)

            def do_GET(self) -> None:  # noqa: N802
                owner.requests.append({"method": "GET", "path": self.path})
                json_reply({"error": "GET is never expected"}, 405)(self)

            def log_message(self, *args: Any) -> None:  # silence the stdlib
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def closed_port() -> int:
    """A port nothing listens on: bound, then released."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.addCleanup(self.server.close)
        cleared = {name: "" for name in CLEARED_VARIABLES}
        patcher = mock.patch.dict(os.environ, cleared)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in CLEARED_VARIABLES:
            os.environ.pop(name, None)

    def provider(self, **options: Any) -> OpenAICompatibleTextToCadModel:
        timeout = options.pop("timeout", 10.0)
        effort = options.pop("effort", None)
        config = AiConfig(
            provider=OPENAI_COMPATIBLE_PROVIDER_NAME,
            model=options.pop("model", MODEL),
            timeout_seconds=timeout,
            effort=effort,
        )
        options.setdefault("base_url", self.server.base_url)
        return OpenAICompatibleTextToCadModel(config, **options)

    def request(self, schema: Any = "default", max_tokens: int = 32000) -> ModelRequest:
        return ModelRequest(
            system=SYSTEM_TEXT,
            user_text=USER_TEXT,
            output_schema=response_schema() if schema == "default" else schema,
            max_output_tokens=max_tokens,
        )

    def call(self, reply: Reply, **options: Any):
        self.server.replies.append(reply)
        return self.provider(**options).generate(self.request())

    def failure(self, reply: Optional[Reply], **options: Any) -> ProviderError:
        if reply is not None:
            self.server.replies.append(reply)
        with self.assertRaises(ProviderError) as caught:
            self.provider(**options).generate(self.request())
        return caught.exception

    @property
    def sent(self) -> Dict[str, Any]:
        self.assertEqual(len(self.server.requests), 1)
        return self.server.requests[0]

    def assert_no_key(self, error: BaseException) -> None:
        for text in (str(error), getattr(error, "message", ""),
                     getattr(error, "detail", "") or "", repr(error)):
            self.assertNotIn(SYNTHETIC_KEY, text)


# --- the request -------------------------------------------------------------


class TestRequestShape(ServerTestCase):
    def test_json_schema_mode_sends_the_whole_schema_unchanged(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))))
        sent = self.sent
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["method"], "POST")
        body = sent["body"]
        self.assertEqual(
            body,
            {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_TEXT},
                    {"role": "user", "content": USER_TEXT},
                ],
                "max_tokens": 32000,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": RESPONSE_FORMAT_NAME,
                        "schema": response_schema(),
                        "strict": True,
                    },
                },
            },
        )
        # Unchanged means unchanged: the keywords the Anthropic compiler
        # rejects are still there for these servers.
        self.assertIn("exclusiveMinimum", json.dumps(body))

    def test_tool_mode_offers_one_function_and_forces_it(self) -> None:
        self.call(
            json_reply(completion(None, tool_calls=[tool_call(json.dumps(ANSWER))])),
            answer_mode="tool",
        )
        body = self.sent["body"]
        self.assertNotIn("response_format", body)
        self.assertEqual(
            body["tools"],
            [
                {
                    "type": "function",
                    "function": {
                        "name": ANSWER_TOOL_NAME,
                        "description": ANSWER_TOOL_DESCRIPTION,
                        "parameters": response_schema(),
                        "strict": True,
                    },
                }
            ],
        )
        self.assertEqual(
            body["tool_choice"],
            {"type": "function", "function": {"name": ANSWER_TOOL_NAME}},
        )
        self.assertEqual(sorted(body), ["max_tokens", "messages", "model",
                                        "tool_choice", "tools"])

    def test_json_object_mode_asks_for_json_only(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), answer_mode="json_object")
        body = self.sent["body"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", body)
        self.assertNotIn("json_schema", json.dumps(body))

    def test_no_decoding_control_is_ever_sent(self) -> None:
        for mode in ("json_schema", "tool", "json_object"):
            with self.subTest(mode=mode):
                self.server.requests.clear()
                self.call(json_reply(completion(json.dumps(ANSWER))), answer_mode=mode)
                body = self.server.requests[0]["body"]
                for name in ("temperature", "top_p", "top_k", "seed",
                             "frequency_penalty", "presence_penalty", "n",
                             "stream", "stream_options", "reasoning_effort"):
                    self.assertNotIn(name, body, msg=name)

    def test_without_a_key_no_authorization_header_is_sent(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))))
        headers = self.sent["headers"]
        self.assertNotIn("authorization", headers)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertNotIn("python-urllib", headers.get("user-agent", "").lower())

    def test_a_configured_key_goes_only_into_the_authorization_header(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), api_key=SYNTHETIC_KEY)
        sent = self.sent
        self.assertEqual(sent["headers"]["authorization"], f"Bearer {SYNTHETIC_KEY}")
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(sent["body"]))
        self.assertNotIn(SYNTHETIC_KEY, sent["path"])
        others = {k: v for k, v in sent["headers"].items() if k != "authorization"}
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(others))

    def test_a_blank_key_is_no_key(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), api_key="   ")
        self.assertNotIn("authorization", self.sent["headers"])

    def test_strict_can_be_turned_off(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), strict=False)
        self.assertIs(self.sent["body"]["response_format"]["json_schema"]["strict"], False)

    def test_the_output_bound_can_be_capped_or_omitted(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), max_tokens_limit=8192)
        self.assertEqual(self.server.requests[-1]["body"]["max_tokens"], 8192)
        # A cap never raises the generation layer's own bound.
        self.server.replies.append(json_reply(completion(json.dumps(ANSWER))))
        self.provider(max_tokens_limit=64000).generate(self.request(max_tokens=1000))
        self.assertEqual(self.server.requests[-1]["body"]["max_tokens"], 1000)
        self.call(json_reply(completion(json.dumps(ANSWER))), omit_max_tokens=True)
        self.assertNotIn("max_tokens", self.server.requests[-1]["body"])

    def test_effort_is_sent_only_when_configured(self) -> None:
        self.call(json_reply(completion(json.dumps(ANSWER))), effort="high")
        self.assertEqual(self.sent["body"]["reasoning_effort"], "high")

    def test_streaming_asks_for_a_stream_with_usage(self) -> None:
        self.call(sse_reply([_delta("{}"), _finish("stop")]), stream=True)
        sent = self.sent
        self.assertIs(sent["body"]["stream"], True)
        self.assertEqual(sent["body"]["stream_options"], {"include_usage": True})
        self.assertEqual(sent["headers"]["accept"], "text/event-stream")

    def test_no_schema_means_no_answer_format(self) -> None:
        for mode in ("json_schema", "tool", "json_object"):
            with self.subTest(mode=mode):
                self.server.requests.clear()
                self.server.replies.append(json_reply(completion("{}")))
                self.provider(answer_mode=mode).generate(self.request(schema=None))
                body = self.server.requests[0]["body"]
                for name in ("response_format", "tools", "tool_choice"):
                    self.assertNotIn(name, body)

    def test_request_body_is_exactly_what_is_sent(self) -> None:
        for options in ({}, {"answer_mode": "tool"}, {"stream": True},
                        {"answer_mode": "json_object", "omit_max_tokens": True}):
            with self.subTest(options=options):
                self.server.requests.clear()
                provider = self.provider(**options)
                if options.get("stream"):
                    self.server.replies.append(sse_reply([_delta("{}"), _finish("stop")]))
                else:
                    self.server.replies.append(json_reply(completion("{}")))
                provider.generate(self.request())
                self.assertEqual(
                    self.server.requests[0]["body"], provider.request_body(self.request())
                )

    def test_a_trailing_slash_on_the_base_url_does_not_double(self) -> None:
        self.call(json_reply(completion("{}")), base_url=self.server.base_url + "/")
        self.assertEqual(self.sent["path"], "/v1/chat/completions")

    def test_one_call_is_one_request(self) -> None:
        self.call(json_reply(completion("not json at all")))
        self.assertEqual(len(self.server.requests), 1)


# --- the answer ----------------------------------------------------------------


class TestAnswerExtraction(ServerTestCase):
    def test_json_schema_mode_returns_the_content(self) -> None:
        text = json.dumps(ANSWER)
        response = self.call(
            json_reply(completion(
                text, usage={"prompt_tokens": 14000, "completion_tokens": 900,
                             "total_tokens": 14900},
            ))
        )
        self.assertEqual(response.text, text)
        self.assertEqual(response.provider, "openai_compatible")
        self.assertEqual(response.model, "served-model-name")
        self.assertTrue(response.structured_output)
        self.assertEqual(response.stop_reason, "stop")
        self.assertEqual(dict(response.usage), {"input_tokens": 14000, "output_tokens": 900})

    def test_missing_model_and_usage_fall_back_quietly(self) -> None:
        response = self.call(json_reply(completion("{}", model=None)))
        self.assertEqual(response.model, MODEL)
        self.assertEqual(dict(response.usage), {})

    def test_json_object_mode_is_not_schema_constrained(self) -> None:
        response = self.call(json_reply(completion("{}")), answer_mode="json_object")
        self.assertFalse(response.structured_output)

    def test_content_is_never_rewritten(self) -> None:
        fenced = "```json\n" + json.dumps(ANSWER) + "\n```"
        response = self.call(json_reply(completion(fenced)))
        self.assertEqual(response.text, fenced)

    def test_tool_arguments_arrive_as_a_string_and_are_parsed(self) -> None:
        arguments = json.dumps(ANSWER, indent=2)
        response = self.call(
            json_reply(completion(None, tool_calls=[tool_call(arguments)],
                                  finish="tool_calls")),
            answer_mode="tool",
        )
        self.assertEqual(json.loads(response.text), ANSWER)
        self.assertEqual(response.text, json.dumps(ANSWER))
        self.assertFalse(response.structured_output)
        self.assertEqual(response.stop_reason, "tool_calls")

    def test_unparsable_tool_arguments_pass_through_raw(self) -> None:
        broken = '{"status": "document", "plan": "cut off'
        response = self.call(
            json_reply(completion(None, tool_calls=[tool_call(broken)])),
            answer_mode="tool",
        )
        self.assertEqual(response.text, broken)

    def test_tool_arguments_sent_as_an_object_are_accepted(self) -> None:
        response = self.call(
            json_reply(completion(None, tool_calls=[tool_call(ANSWER)])),
            answer_mode="tool",
        )
        self.assertEqual(json.loads(response.text), ANSWER)

    def test_without_the_tool_call_the_content_is_the_answer(self) -> None:
        response = self.call(
            json_reply(completion("prose instead of a call")), answer_mode="tool"
        )
        self.assertEqual(response.text, "prose instead of a call")

    def test_a_call_to_another_tool_is_ignored(self) -> None:
        response = self.call(
            json_reply(completion("the text",
                                  tool_calls=[tool_call("{}", name="run_python")])),
            answer_mode="tool",
        )
        self.assertEqual(response.text, "the text")

    def test_a_cut_off_answer_is_returned_cut_off(self) -> None:
        partial = '{"status": "document", "plan": "a block'
        response = self.call(json_reply(completion(partial, finish="length")))
        self.assertEqual(response.text, partial)
        self.assertEqual(response.stop_reason, "length")

    def test_content_parts_are_joined(self) -> None:
        response = self.call(
            json_reply(completion([{"type": "text", "text": "{\"a\""},
                                   {"type": "text", "text": ": 1}"}]))
        )
        self.assertEqual(response.text, '{"a": 1}')

    def test_empty_content_is_an_empty_response(self) -> None:
        for content in (None, "", "   "):
            with self.subTest(content=content):
                error = self.failure(json_reply(completion(content, finish="length")))
                self.assertIs(error.kind, ProviderErrorKind.EMPTY_RESPONSE)
                self.assertIn("length", error.detail or "")

    def test_no_choices_is_an_empty_response(self) -> None:
        error = self.failure(json_reply({"id": "x", "choices": []}))
        self.assertIs(error.kind, ProviderErrorKind.EMPTY_RESPONSE)

    def test_a_refusal_is_refused(self) -> None:
        error = self.failure(json_reply(completion(None, refusal="I can't help")))
        self.assertIs(error.kind, ProviderErrorKind.REFUSED)
        error = self.failure(json_reply(completion(None, finish="content_filter")))
        self.assertIs(error.kind, ProviderErrorKind.REFUSED)

    def test_a_reply_that_is_not_json_is_other(self) -> None:
        error = self.failure(json_reply(b"<html>a web page</html>"))
        self.assertIs(error.kind, ProviderErrorKind.OTHER)
        self.assertIn("/v1", error.detail or "")

    def test_an_error_object_in_a_200_reply_is_mapped(self) -> None:
        error = self.failure(
            json_reply({"error": {"code": 429, "message": "upstream rate limited"}})
        )
        self.assertIs(error.kind, ProviderErrorKind.RATE_LIMITED)
        error = self.failure(json_reply({"error": {"message": "something odd"}}))
        self.assertIs(error.kind, ProviderErrorKind.OTHER)

    def test_a_reported_truncation_is_an_invalid_request(self) -> None:
        payload = completion("{}")
        payload["truncated"] = True
        error = self.failure(json_reply(payload))
        self.assertIs(error.kind, ProviderErrorKind.INVALID_REQUEST)
        self.assertIn("context", error.detail or "")


# --- streaming -----------------------------------------------------------------


def _delta(content: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    delta: Dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    delta.update(extra)
    return {"model": "streamed-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}


def _finish(reason: str) -> Dict[str, Any]:
    return {"choices": [{"index": 0, "delta": {}, "finish_reason": reason}]}


def _tool_delta(arguments: str, name: Optional[str] = None, index: int = 0) -> Dict[str, Any]:
    function: Dict[str, Any] = {"arguments": arguments}
    if name is not None:
        function["name"] = name
    call: Dict[str, Any] = {"index": index, "function": function}
    if name is not None:
        call["id"] = "call_1"
        call["type"] = "function"
    return _delta(tool_calls=[call])


class TestStreaming(ServerTestCase):
    def test_content_deltas_are_reassembled(self) -> None:
        text = json.dumps(ANSWER)
        pieces = [text[i:i + 7] for i in range(0, len(text), 7)]
        events = [_delta(piece) for piece in pieces] + [
            _finish("stop"),
            {"choices": [], "usage": {"prompt_tokens": 50, "completion_tokens": 20}},
        ]
        response = self.call(sse_reply(events, comments=True), stream=True)
        self.assertEqual(response.text, text)
        self.assertEqual(response.stop_reason, "stop")
        self.assertEqual(response.model, "streamed-model")
        self.assertEqual(dict(response.usage), {"input_tokens": 50, "output_tokens": 20})

    def test_tool_argument_fragments_are_reassembled(self) -> None:
        arguments = json.dumps(ANSWER)
        events = [_tool_delta("", name=ANSWER_TOOL_NAME)]
        events += [_tool_delta(arguments[i:i + 5]) for i in range(0, len(arguments), 5)]
        events.append(_finish("tool_calls"))
        response = self.call(sse_reply(events), stream=True, answer_mode="tool")
        self.assertEqual(json.loads(response.text), ANSWER)

    def test_a_stream_without_done_but_finished_is_complete(self) -> None:
        response = self.call(
            sse_reply([_delta("{}"), _finish("length")], done=False), stream=True
        )
        self.assertEqual(response.text, "{}")
        self.assertEqual(response.stop_reason, "length")

    def test_a_stream_that_stops_early_is_a_transport_failure(self) -> None:
        error = self.failure(sse_reply([_delta('{"status"')], done=False), stream=True)
        self.assertIs(error.kind, ProviderErrorKind.TIMEOUT)

    def test_an_error_event_mid_stream_is_mapped(self) -> None:
        error = self.failure(
            sse_reply([_delta("{"), {"error": {"code": 503, "message": "overloaded"}}]),
            stream=True,
        )
        self.assertIs(error.kind, ProviderErrorKind.CAPACITY)

    def test_an_unreadable_event_is_other(self) -> None:
        error = self.failure(sse_reply(["{not json"]), stream=True)
        self.assertIs(error.kind, ProviderErrorKind.OTHER)

    def test_an_empty_stream_is_an_empty_response(self) -> None:
        error = self.failure(sse_reply([_finish("stop")]), stream=True)
        self.assertIs(error.kind, ProviderErrorKind.EMPTY_RESPONSE)


# --- failures ------------------------------------------------------------------


class TestErrorMapping(ServerTestCase):
    def test_http_statuses_map_onto_the_neutral_kinds(self) -> None:
        expected = {
            400: ProviderErrorKind.INVALID_REQUEST,
            401: ProviderErrorKind.AUTHENTICATION,
            402: ProviderErrorKind.RATE_LIMITED,
            403: ProviderErrorKind.AUTHENTICATION,
            404: ProviderErrorKind.INVALID_REQUEST,
            408: ProviderErrorKind.TIMEOUT,
            413: ProviderErrorKind.INVALID_REQUEST,
            422: ProviderErrorKind.INVALID_REQUEST,
            429: ProviderErrorKind.RATE_LIMITED,
            500: ProviderErrorKind.CAPACITY,
            502: ProviderErrorKind.CAPACITY,
            503: ProviderErrorKind.CAPACITY,
            504: ProviderErrorKind.TIMEOUT,
        }
        for status, kind in expected.items():
            with self.subTest(status=status):
                self.assertIs(kind_for_status(status), kind)
                error = self.failure(
                    json_reply({"error": {"message": f"status {status}"}}, status)
                )
                self.assertIs(error.kind, kind)
                self.assertIn(f"status {status}", error.detail or "")
                # The server's words stay in detail, never the public message.
                self.assertNotIn(f"status {status}", error.message)
        self.assertIs(kind_for_status(418), ProviderErrorKind.INVALID_REQUEST)
        self.assertIs(kind_for_status(599), ProviderErrorKind.CAPACITY)

    def test_a_404_names_the_two_likely_causes(self) -> None:
        error = self.failure(json_reply({"error": "model 'x' not found"}, 404))
        self.assertIn(MODEL_VARIABLE, error.detail or "")
        self.assertIn("/v1", error.detail or "")

    def test_a_context_overflow_says_so(self) -> None:
        bodies = (
            # vLLM's wording
            {"error": {"message": "This model's maximum context length is 32768 "
                       "tokens. However, you requested 46000 tokens", "code": 400}},
            # llama.cpp's wording
            {"error": {"type": "exceed_context_size_error", "code": 400,
                       "message": "the request exceeds the available context size"}},
        )
        for body in bodies:
            with self.subTest(body=body):
                error = self.failure(json_reply(body, 400))
                self.assertIs(error.kind, ProviderErrorKind.INVALID_REQUEST)
                self.assertIn(CONTEXT_ADVICE, error.detail or "")

    def test_a_redirect_is_not_followed(self) -> None:
        elsewhere = FakeServer()
        self.addCleanup(elsewhere.close)
        error = self.failure(
            redirect_reply(elsewhere.base_url + "/chat/completions", 307),
            api_key=SYNTHETIC_KEY,
        )
        self.assertIs(error.kind, ProviderErrorKind.INVALID_REQUEST)
        self.assertIn("redirect", error.detail or "")
        self.assertEqual(elsewhere.requests, [])
        for status in (301, 302, 303):
            with self.subTest(status=status):
                self.failure(redirect_reply(elsewhere.base_url, status),
                             api_key=SYNTHETIC_KEY)
                self.assertEqual(elsewhere.requests, [])

    def test_a_closed_port_is_not_reachable(self) -> None:
        port = closed_port()
        error = self.failure(None, base_url=f"http://127.0.0.1:{port}/v1")
        self.assertIs(error.kind, ProviderErrorKind.TIMEOUT)
        self.assertIn("not reachable", error.message)
        self.assertIn(str(port), error.detail or "")

    def test_an_unresolvable_host_is_not_reachable(self) -> None:
        # Name resolution is made to fail here, so nothing -- not even a DNS
        # query, nor a configured proxy -- is contacted.
        unresolvable = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        without_proxy = {name: "" for name in (
            "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY",
            "all_proxy")}
        with mock.patch.dict(os.environ, without_proxy), \
             mock.patch.object(socket, "getaddrinfo", side_effect=unresolvable):
            error = self.failure(None, base_url="http://nonexistent-host.invalid/v1")
        self.assertIs(error.kind, ProviderErrorKind.TIMEOUT)
        self.assertIn("not reachable", error.message)

    def test_a_slow_server_times_out(self) -> None:
        started = time.monotonic()
        error = self.failure(slow_reply(3.0), timeout=0.5)
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertIs(error.kind, ProviderErrorKind.TIMEOUT)
        self.assertIn("CAD_AI_TIMEOUT_SECONDS", error.detail or "")

    def test_a_dropped_connection_is_a_transport_failure(self) -> None:
        error = self.failure(dropped_reply())
        self.assertIs(error.kind, ProviderErrorKind.TIMEOUT)

    def test_no_vendor_exception_escapes(self) -> None:
        for reply in (json_reply({}, 500), dropped_reply(), json_reply(b"x")):
            with self.subTest(reply=reply):
                try:
                    self.call(reply)
                except ProviderError:
                    pass

    def test_a_loopback_server_is_reached_past_a_configured_proxy(self) -> None:
        dead = f"http://127.0.0.1:{closed_port()}"
        with mock.patch.dict(os.environ, {
            "HTTP_PROXY": dead, "http_proxy": dead,
            "HTTPS_PROXY": dead, "https_proxy": dead,
            "NO_PROXY": "", "no_proxy": "",
        }):
            response = self.call(json_reply(completion("{}")))
        self.assertEqual(response.text, "{}")


# --- the credential --------------------------------------------------------------


class TestCredentialHygiene(ServerTestCase):
    def test_the_key_is_not_in_repr_or_attributes(self) -> None:
        provider = self.provider(api_key=SYNTHETIC_KEY)
        self.assertNotIn(SYNTHETIC_KEY, repr(provider))
        self.assertNotIn(SYNTHETIC_KEY, str(provider))
        self.assertNotIn(SYNTHETIC_KEY, repr(vars(provider)))
        for value in vars(provider).values():
            self.assertNotIn(SYNTHETIC_KEY, repr(value))
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(provider.config.to_dict()))

    def test_an_echoed_key_is_redacted_from_every_error(self) -> None:
        echo = {"error": {"message": f"invalid key {SYNTHETIC_KEY}"}}
        for status in (400, 401, 404, 429, 500):
            with self.subTest(status=status):
                error = self.failure(json_reply(echo, status), api_key=SYNTHETIC_KEY)
                self.assert_no_key(error)
                self.assertIn(REDACTED, error.detail or "")
        error = self.failure(json_reply(echo), api_key=SYNTHETIC_KEY)
        self.assert_no_key(error)
        error = self.failure(
            sse_reply([echo]), api_key=SYNTHETIC_KEY, stream=True
        )
        self.assert_no_key(error)

    def test_no_failure_mentions_the_key(self) -> None:
        port = closed_port()
        for reply, options in (
            (None, {"base_url": f"http://127.0.0.1:{port}/v1"}),
            (slow_reply(2.0), {"timeout": 0.3}),
            (dropped_reply(), {}),
            (json_reply(b"not json"), {}),
            (json_reply(completion(None)), {}),
        ):
            with self.subTest(options=options):
                error = self.failure(reply, api_key=SYNTHETIC_KEY, **options)
                self.assert_no_key(error)
                cause = error.__cause__
                if cause is not None:
                    self.assertNotIn(SYNTHETIC_KEY, repr(cause))
                    self.assertNotIn(SYNTHETIC_KEY, str(cause))

    def test_a_key_a_header_cannot_carry_is_refused_without_quoting_it(self) -> None:
        for bad in (SYNTHETIC_KEY + "\r\nX-Injected: 1", SYNTHETIC_KEY + "☃"):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(ProviderNotConfigured) as caught:
                    self.provider(api_key=bad)
                self.assert_no_key(caught.exception)
                self.assertIsNone(caught.exception.__cause__)


# --- configuration and selection -----------------------------------------------


class TestConfiguration(ServerTestCase):
    def environment(self, **values: str) -> Dict[str, str]:
        return {
            PROVIDER_VARIABLE: OPENAI_COMPATIBLE_PROVIDER_NAME,
            OPENAI_BASE_URL_VARIABLE: self.server.base_url,
            MODEL_VARIABLE: MODEL,
            **values,
        }

    def from_environment(self, **values: str) -> OpenAICompatibleTextToCadModel:
        environment = self.environment(**values)
        with mock.patch.dict(os.environ, environment):
            for name, value in environment.items():
                if value == "":
                    os.environ.pop(name, None)
            return OpenAICompatibleTextToCadModel.from_environment(
                config_from_environment()
            )

    def test_the_environment_builds_a_working_provider(self) -> None:
        provider = self.from_environment(**{OPENAI_API_KEY_VARIABLE: SYNTHETIC_KEY})
        self.assertIsInstance(provider, TextToCadModel)
        self.assertEqual(provider.name, "openai_compatible")
        self.assertEqual(provider.config.model, MODEL)
        self.assertEqual(provider.answer_mode, "json_schema")
        self.assertFalse(provider.stream)
        self.server.replies.append(json_reply(completion("{}")))
        provider.generate(self.request())
        self.assertEqual(self.sent["headers"]["authorization"], f"Bearer {SYNTHETIC_KEY}")

    def test_a_missing_base_url_is_not_configured(self) -> None:
        with self.assertRaises(ProviderNotConfigured) as caught:
            self.from_environment(**{OPENAI_BASE_URL_VARIABLE: ""})
        self.assertIs(caught.exception.kind, ProviderErrorKind.NOT_CONFIGURED)
        self.assertIn(OPENAI_BASE_URL_VARIABLE, caught.exception.detail or "")

    def test_a_missing_model_is_not_configured(self) -> None:
        self.assertEqual(DEFAULT_MODELS[OPENAI_COMPATIBLE_PROVIDER_NAME], "")
        with self.assertRaises(ProviderNotConfigured) as caught:
            self.from_environment(**{MODEL_VARIABLE: ""})
        self.assertIn(MODEL_VARIABLE, caught.exception.detail or "")
        self.assertEqual(self.server.requests, [])

    def test_an_empty_model_is_tolerated_only_for_this_provider(self) -> None:
        AiConfig(provider=OPENAI_COMPATIBLE_PROVIDER_NAME, model="")
        with self.assertRaises(AiConfigurationError):
            AiConfig(provider="anthropic", model="")
        with self.assertRaises(AiConfigurationError):
            AiConfig(provider=OPENAI_COMPATIBLE_PROVIDER_NAME, model="   ")

    def test_unusable_base_urls_are_refused(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/v1",
            "localhost:11434/v1",
            "http:///v1",
            "http://user:secret@example.com/v1",
            "http://example.com/v1?key=1",
            "http://example.com/v1#x",
            "http://localhost:11434/v1/chat/completions",
            "http://localhost:notaport/v1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ProviderNotConfigured) as caught:
                    normalise_base_url(url)
                self.assertNotIn("secret", caught.exception.detail or "")
        self.assertEqual(
            normalise_base_url(" HTTPS://openrouter.ai/api/v1/ "),
            "https://openrouter.ai/api/v1",
        )

    def test_unusable_settings_are_refused(self) -> None:
        for name, value in (
            (ANSWER_MODE_VARIABLE, "grammar"),
            (STRICT_VARIABLE, "maybe"),
            (STREAM_VARIABLE, "2"),
            (MAX_TOKENS_VARIABLE, "0"),
            (MAX_TOKENS_VARIABLE, "lots"),
        ):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ProviderNotConfigured) as caught:
                    self.from_environment(**{name: value})
                self.assertIn(name, caught.exception.detail or "")

    def test_settings_are_read_from_the_environment(self) -> None:
        provider = self.from_environment(**{
            ANSWER_MODE_VARIABLE: "TOOL", STRICT_VARIABLE: "0",
            STREAM_VARIABLE: "1", MAX_TOKENS_VARIABLE: "none",
        })
        body = provider.request_body(self.request())
        self.assertEqual(provider.answer_mode, "tool")
        self.assertTrue(provider.stream)
        self.assertIs(body["tools"][0]["function"]["strict"], False)
        self.assertNotIn("max_tokens", body)
        provider = self.from_environment(**{MAX_TOKENS_VARIABLE: "16384"})
        self.assertEqual(provider.request_body(self.request())["max_tokens"], 16384)

    def test_never_selected_automatically(self) -> None:
        self.assertIn(OPENAI_COMPATIBLE_PROVIDER_NAME, PROVIDER_NAMES)
        self.assertNotIn(OPENAI_COMPATIBLE_PROVIDER_NAME, AUTOMATIC_PROVIDER_NAMES)
        configured = {
            OPENAI_BASE_URL_VARIABLE: self.server.base_url,
            OPENAI_API_KEY_VARIABLE: SYNTHETIC_KEY,
            MODEL_VARIABLE: MODEL,
        }
        self.assertEqual(resolve_provider(configured), "anthropic")
        self.assertEqual(
            resolve_provider({**configured, GEMINI_API_KEY_VARIABLE: "x"}), "gemini"
        )
        # A base URL is not a credential for the ordinary suite.
        self.assertFalse(credential_available(configured))
        self.assertEqual(
            resolve_provider({PROVIDER_VARIABLE: "openai_compatible"}),
            OPENAI_COMPATIBLE_PROVIDER_NAME,
        )
        self.assertEqual(
            resolve_provider({PROVIDER_VARIABLE: "OpenAI_Compatible"}),
            OPENAI_COMPATIBLE_PROVIDER_NAME,
        )

    def test_availability_is_the_base_url_alone(self) -> None:
        name = OPENAI_COMPATIBLE_PROVIDER_NAME
        self.assertFalse(credential_available({}, provider=name))
        self.assertFalse(
            credential_available({OPENAI_API_KEY_VARIABLE: SYNTHETIC_KEY}, provider=name)
        )
        self.assertFalse(
            credential_available({OPENAI_BASE_URL_VARIABLE: "  "}, provider=name)
        )
        self.assertTrue(
            credential_available({OPENAI_BASE_URL_VARIABLE: "http://x/v1"}, provider=name)
        )

    def test_reading_configuration_contacts_nothing(self) -> None:
        with mock.patch.object(socket, "socket",
                               side_effect=AssertionError("a socket was opened")), \
             mock.patch.object(socket, "create_connection",
                               side_effect=AssertionError("a socket was opened")):
            self.from_environment(**{OPENAI_API_KEY_VARIABLE: SYNTHETIC_KEY})
            config = config_from_environment(self.environment())
            credential_available(self.environment(), provider="openai_compatible")
        self.assertEqual(config.provider, OPENAI_COMPATIBLE_PROVIDER_NAME)
        self.assertEqual(config.model, MODEL)
        self.assertEqual(self.server.requests, [])

    def test_the_factory_dispatches_to_this_provider(self) -> None:
        environment = self.environment()
        with mock.patch.dict(os.environ, environment):
            model = model_from_environment(config_from_environment())
            self.assertIsInstance(model, OpenAICompatibleTextToCadModel)
            os.environ.pop(MODEL_VARIABLE)
            with self.assertRaises(ProviderNotConfigured):
                model_from_environment(config_from_environment())
            os.environ[MODEL_VARIABLE] = MODEL
            os.environ.pop(OPENAI_BASE_URL_VARIABLE)
            with self.assertRaises(ProviderNotConfigured):
                model_from_environment(config_from_environment())


# --- the module's own boundary ---------------------------------------------------


def _imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _called(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                called.append(target.id)
            elif isinstance(target, ast.Attribute):
                called.append(target.attr)
    return called


class TestModuleBoundary(unittest.TestCase):
    STANDARD_LIBRARY = {
        "__future__", "dataclasses", "http.client", "ipaddress", "json", "os",
        "ssl", "time", "typing", "urllib.error", "urllib.parse", "urllib.request",
    }

    def test_standard_library_and_cad_ai_only(self) -> None:
        for name in _imports(MODULE):
            if name.startswith("cad_ai"):
                self.assertIn(name, ("cad_ai.config", "cad_ai.prompt", "cad_ai.provider"))
            else:
                self.assertIn(name, self.STANDARD_LIBRARY, msg=name)

    def test_no_sdk_executes_nothing_writes_nothing_says_nothing(self) -> None:
        imports = _imports(MODULE)
        for forbidden in ("openai", "anthropic", "google", "requests", "httpx",
                          "subprocess", "pickle", "logging", "tempfile", "shutil"):
            self.assertNotIn(forbidden, imports, msg=forbidden)
        called = _called(MODULE)
        for forbidden in ("eval", "exec", "compile", "system", "Popen", "run",
                          "print", "getLogger", "open_text", "write_text",
                          "write_bytes", "mkdir"):
            self.assertNotIn(forbidden, called, msg=forbidden)
        source = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("open(", source.replace("self._opener().open(", ""))

    def test_no_other_module_imports_this_provider_at_module_level(self) -> None:
        for path in sorted(AI_SOURCE.glob("*.py")):
            if path.name == MODULE.name:
                continue
            head = path.read_text(encoding="utf-8").split("\ndef ", 1)[0]
            self.assertNotIn("openai_compatible_provider import", head, msg=path.name)

    def test_the_transport_names_this_provider_nowhere(self) -> None:
        transport = AI_SOURCE.parent / "cad_api"
        for path in sorted(transport.glob("*.py")):
            source = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("openai", source, msg=path.name)


if __name__ == "__main__":
    unittest.main()
