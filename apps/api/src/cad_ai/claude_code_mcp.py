"""The one tool Claude Code is given: a tiny stdio MCP server for the answer.

:mod:`cad_ai.claude_code_provider` starts the official ``claude`` CLI with
**none** of its own tools (no shell, no files, no web) and registers exactly
one MCP server -- this module -- which offers exactly one tool,
:data:`cad_ai.prompt.ANSWER_TOOL_NAME`. The model answers by calling it once
with the whole answer object as the tool input.

This server does nothing with that input. It does not validate it, store it,
execute it or forward it: it replies "Answer received." and that is all. The
provider reads the same input from the CLI's own ``stream-json`` output, and
the application's validator -- the only authority on validity -- judges it
there. The tool exists so the model has a well-defined place to put one JSON
object, nothing more.

**Standard library only, and no import from** :mod:`cad_ai`. The provider runs
it by file path in isolated mode (``python -I .../claude_code_mcp.py
<tool-file>``), so it starts in milliseconds and needs no ``PYTHONPATH``;
``python -m cad_ai.claude_code_mcp <tool-file>`` works too, but imports the
whole package first. The tool's name, description and input schema arrive in
a JSON file whose path is the only argument, so nothing about the tool -- and
no schema -- appears on a command line.

Transport: MCP stdio framing. Each JSON-RPC 2.0 message is one line of UTF-8
JSON terminated by ``\\n``, with no embedded newline; nothing but protocol
messages is ever written to stdout. The server exits when stdin closes, so a
killed CLI never leaves it behind.
"""

from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, Dict, Mapping, Optional

#: How this server names itself in ``initialize``.
SERVER_NAME = "cad-answer"
SERVER_VERSION = "1.0.0"

#: Protocol revisions this server can speak. It uses only ``initialize``,
#: ``tools/list``, ``tools/call`` and ``ping``, which every one of them has in
#: the same shape. A client asking for one of these gets it back; any other
#: request gets :data:`DEFAULT_PROTOCOL_VERSION`, as the specification says a
#: server should answer a version it does not support.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

#: The whole of what a tool call returns to the model.
ANSWER_RECEIVED = "Answer received."

#: Used when the tool file carries no input schema: any object.
PERMISSIVE_SCHEMA: Dict[str, Any] = {"type": "object"}

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def load_tool(path: str) -> Dict[str, Any]:
    """The tool definition written by the provider: name, description, schema."""
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        raise ValueError("the tool file must be an object with a string 'name'")
    schema = raw.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = dict(PERMISSIVE_SCHEMA)
    return {
        "name": raw["name"],
        "description": str(raw.get("description", "")),
        "inputSchema": schema,
    }


def _result(message_id: Any, result: Mapping[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": dict(result)}


def _error(message_id: Any, code: int, text: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def handle(message: Any, tool: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The reply to one decoded message, or ``None`` when none is due.

    A pure function, so the protocol can be tested without a process.
    Notifications (no ``id``) and responses (no ``method``) get no reply.
    """
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "a message must be a JSON object")
    method = message.get("method")
    if "id" not in message:
        return None  # a notification, e.g. notifications/initialized
    message_id = message.get("id")
    if not isinstance(method, str):
        if "result" in message or "error" in message:
            return None  # a response to a request this server never sends
        return _error(message_id, INVALID_REQUEST, "no method")
    params = message.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        return _result(
            message_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(
            message_id,
            {
                "tools": [
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                    }
                ]
            },
        )
    if method == "tools/call":
        if params.get("name") != tool["name"]:
            return _error(
                message_id, INVALID_PARAMS, f"unknown tool: {params.get('name')!r}"
            )
        # Deliberately not validated, stored or acted on: see the module text.
        return _result(
            message_id,
            {"content": [{"type": "text", "text": ANSWER_RECEIVED}], "isError": False},
        )
    return _error(message_id, METHOD_NOT_FOUND, f"method not found: {method}")


def _encode(reply: Mapping[str, Any]) -> bytes:
    # Compact separators and no indent: one message, one line, no embedded
    # newline (json escapes any newline inside a string).
    return json.dumps(reply, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"


def serve(stdin: BinaryIO, stdout: BinaryIO, tool: Mapping[str, Any]) -> int:
    """Answer newline-delimited JSON-RPC on ``stdin`` until it closes.

    Binary streams on purpose: no locale codepage and no ``\\r\\n``
    translation on Windows -- the framing is UTF-8 and ``\\n``.
    """
    for raw in stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            reply: Optional[Dict[str, Any]] = _error(None, PARSE_ERROR, "parse error")
        else:
            reply = handle(message, tool)
        if reply is not None:
            stdout.write(_encode(reply))
            stdout.flush()
    return 0


def main(argv: Optional[list] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        sys.stderr.write("usage: claude_code_mcp <tool-definition.json>\n")
        return 2
    try:
        tool = load_tool(arguments[0])
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"claude_code_mcp: cannot read the tool file: {exc}\n")
        return 2
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer, tool)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ANSWER_RECEIVED",
    "DEFAULT_PROTOCOL_VERSION",
    "PERMISSIVE_SCHEMA",
    "SERVER_NAME",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "handle",
    "load_tool",
    "main",
    "serve",
]
