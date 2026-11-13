"""The Claude Code provider, proved against a fake ``claude`` -- never the real one.

**No test in this file runs the real Claude Code CLI or contacts any model.**
Every call goes to a small fake ``claude`` written to a temporary folder by
the test: a Python script behind a shim (a ``#!`` line on POSIX, a ``.cmd``
file on Windows) that speaks the CLI's stream-json protocol according to a
mode the test chooses, and records what it was given -- its argv, its working
folder, which environment variable *names* it saw, and the first line on its
stdin. In its ``tool`` mode it also acts as the MCP client: it starts the
real :mod:`cad_ai.claude_code_mcp` server exactly as the provider's MCP
configuration file says, and lists and calls its tool.

What this establishes: the command line (every flag present, no prompt, no
user text and no credential on it), the environment the child gets (both API
key variables removed unless opted in), how the answer is taken (the tool
call's input; the result text as a fallback), that a timeout kills the child
**and what it started**, how failures map onto the neutral error kinds, and
that the provider is only ever selected by name.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock as mock
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from cad_ai import claude_code_mcp
from cad_ai.anthropic_provider import schema_for_api
from cad_ai.claude_code_provider import (
    ANSWER_TOOL,
    ClaudeCodeTextToCadModel,
    child_environment,
    command_line,
    locate_cli,
)
from cad_ai.config import (
    API_KEY_VARIABLE,
    AUTOMATIC_PROVIDER_NAMES,
    CLAUDE_CODE_PATH_VARIABLE,
    CLAUDE_CODE_PROVIDER_NAME,
    CLAUDE_CODE_USE_API_KEY_VARIABLE,
    DEFAULT_MODELS,
    FALLBACK_API_KEY_VARIABLE,
    GEMINI_API_KEY_VARIABLE,
    PROVIDER_NAMES,
    PROVIDER_VARIABLE,
    AiConfig,
    config_from_environment,
    credential_available,
    resolve_provider,
)
from cad_ai.factory import model_from_environment
from cad_ai.prompt import ANSWER_TOOL_DESCRIPTION, ANSWER_TOOL_NAME
from cad_ai.provider import (
    ModelRequest,
    ProviderError,
    ProviderErrorKind,
    ProviderNotConfigured,
)

AI_SOURCE = Path(__file__).resolve().parents[1] / "src" / "cad_ai"

#: Obviously synthetic. Never a real key; asserted never to reach argv.
SYNTHETIC_KEY = "sk-ant-SYNTHETIC-TEST-KEY-DO-NOT-USE-000"

#: A system prompt for the tests: distinctive, so its absence from argv
#: means something.
SYSTEM_TEXT = "SYSTEM-PROMPT-SENTINEL: answer with the tool."
USER_TEXT = "a 100 x 60 x 10 mm plate -- USER-TEXT-SENTINEL"

ANSWER: Dict[str, Any] = {
    "status": "document",
    "plan": "one box",
    "assumptions": [],
    "document": {"schema_version": "1.2.0", "units": "mm", "features": []},
    "summary": "a plate",
    "questions": [],
    "issues": [],
}

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["document", "unsupported"]},
        "plan": {"type": "string", "minLength": 1},
    },
    "required": ["status"],
    "additionalProperties": False,
}

#: The fake ``claude``. Reads its mode and its record path from the
#: environment (both pass through the provider's environment untouched).
FAKE_CLAUDE = r'''
import json, os, subprocess, sys, time

MODE = os.environ.get("FAKE_CLAUDE_MODE", "tool")
RECORD = os.environ["FAKE_CLAUDE_RECORD"]
ANSWER = json.loads(os.environ.get("FAKE_CLAUDE_ANSWER", "{}"))
TOOL = "mcp__cad__submit_answer"
WATCHED = ("ANTHROPIC_API_KEY", "CAD_ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "GEMINI_API_KEY", "CLAUDECODE", "PATH", "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
           "ENABLE_CLAUDEAI_MCP_SERVERS")
record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "cwd_entries": sorted(os.listdir(os.getcwd())),
    "pid": os.getpid(),
    "env_present": {name: name in os.environ for name in WATCHED},
    "max_output_tokens": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
}

def save():
    with open(RECORD, "w", encoding="utf-8") as handle:
        json.dump(record, handle)

def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()

def option(name):
    argv = sys.argv[1:]
    return argv[argv.index(name) + 1] if name in argv else None

def mcp_exchange(arguments):
    """Act as the MCP client against the configured server."""
    config = json.load(open(option("--mcp-config"), encoding="utf-8"))
    server = config["mcpServers"]["cad"]
    child = subprocess.Popen([server["command"]] + server["args"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    replies = []
    def call(message, expect_reply=True):
        child.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        child.stdin.flush()
        if expect_reply:
            replies.append(json.loads(child.stdout.readline().decode("utf-8")))
    call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "fake", "version": "0"}}})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"}, False)
    call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": replies[1]["result"]["tools"][0]["name"],
                     "arguments": arguments}})
    child.stdin.close()
    record["mcp_replies"] = replies
    record["mcp_returncode"] = child.wait(timeout=30)
    record["mcp_config"] = server

first = sys.stdin.readline()
record["stdin_first_line"] = first
save()

init = {"type": "system", "subtype": "init", "model": "claude-haiku-5-5-from-init",
        "tools": [TOOL], "mcp_servers": [{"name": "cad", "status": "connected"}]}
usage = {"input_tokens": 11, "output_tokens": 22, "cache_read_input_tokens": 33,
         "cache_creation_input_tokens": 44, "service_tier": "standard"}

def assistant(content, error=None, model="claude-haiku-5-5-from-message"):
    event = {"type": "assistant", "parent_tool_use_id": None, "session_id": "s",
             "message": {"model": model, "role": "assistant",
                         "content": content, "usage": {"input_tokens": 1, "output_tokens": 2}}}
    if error:
        event["error"] = error
    return event

def wait_for_eof():
    for line in sys.stdin:
        record.setdefault("stdin_later_lines", []).append(line)
        save()

if MODE == "tool":
    emit(init)
    mcp_exchange(ANSWER)
    save()
    emit(assistant([{"type": "thinking", "thinking": "..."},
                    {"type": "tool_use", "id": "t0", "name": "mcp__other__submit_answer", "input": {"wrong": True}},
                    {"type": "tool_use", "id": "t1", "name": TOOL, "input": ANSWER}]))
    emit({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "Answer received."}]}})
    emit({"type": "result", "subtype": "error_max_turns", "is_error": True,
          "num_turns": 1, "usage": usage})
    wait_for_eof()
    sys.exit(1)
elif MODE == "text":
    emit(init)
    emit(assistant([{"type": "text", "text": "partial "}]))
    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": "plain text answer", "usage": usage, "stop_reason": "end_turn"})
    wait_for_eof()
elif MODE == "control":
    emit(init)
    emit({"type": "control_request", "request_id": "r1",
          "request": {"subtype": "can_use_tool", "tool_name": TOOL, "input": ANSWER}})
    record["control_reply"] = json.loads(sys.stdin.readline())
    emit({"type": "control_request", "request_id": "r2",
          "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls"}}})
    record["control_reply_2"] = json.loads(sys.stdin.readline())
    save()
    emit(assistant([{"type": "tool_use", "id": "t1", "name": TOOL, "input": ANSWER}]))
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "", "usage": usage})
    wait_for_eof()
elif MODE == "answer_then_hang":
    emit(assistant([{"type": "tool_use", "id": "t1", "name": TOOL, "input": ANSWER}]))
    time.sleep(120)
elif MODE == "hang":
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    record["grandchild_pid"] = grandchild.pid
    save()
    emit(init)
    time.sleep(120)
elif MODE == "retry_then_hang":
    emit(init)
    emit({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10,
          "retry_delay_ms": 500, "error_status": 429, "error": "rate_limit"})
    time.sleep(120)
elif MODE == "auth":
    emit(init)
    emit(assistant([{"type": "text", "text": "Not logged in · Please run /login"}],
                   error="authentication_failed", model="<synthetic>"))
    emit({"type": "result", "subtype": "success", "is_error": True,
          "result": "Not logged in · Please run /login"})
    sys.exit(1)
elif MODE == "login_result":
    emit({"type": "result", "subtype": "success", "is_error": True,
          "result": "Not logged in · Please run /login"})
    sys.exit(1)
elif MODE == "truncated":
    emit(assistant([{"type": "text", "text": "API Error: response exceeded the output token maximum"}],
                   error="max_output_tokens", model="<synthetic>"))
    emit({"type": "result", "subtype": "success", "is_error": True,
          "result": "API Error: response exceeded the output token maximum"})
    sys.exit(1)
elif MODE == "crash":
    sys.stderr.write("TypeError: something internal went wrong at cli.js:1:2\n")
    sys.exit(3)
elif MODE == "usage_limit":
    sys.stderr.write("Claude AI usage limit reached|1767225600\n")
    sys.exit(1)
elif MODE == "silent":
    sys.exit(0)
'''


def process_is_running(pid: int) -> bool:
    """Whether ``pid`` is a live process (a zombie counts as gone)."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            state = stat.read_text().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            return False
        return state not in ("Z", "X")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class FakeClaudeTestCase(unittest.TestCase):
    """A temporary folder holding a fake ``claude`` and the provider's files."""

    timeout_seconds = 30.0

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.tmp = Path(self._directory.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        script = self.tmp / "fake_claude.py"
        script.write_text(textwrap.dedent(FAKE_CLAUDE), encoding="utf-8")
        if os.name == "nt":  # pragma: no cover - exercised on Windows only
            self.executable = self.bin / "claude.cmd"
            self.executable.write_text(
                f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
        else:
            self.executable = self.bin / "claude"
            self.executable.write_text(
                f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self.executable.chmod(0o755)
        self.record_path = self.tmp / "record.json"
        self.root = self.tmp / "provider-root"

    def environ(self, mode: str, **extra: str) -> Dict[str, str]:
        environ = {
            name: value
            for name, value in os.environ.items()
            if name
            not in (
                API_KEY_VARIABLE,
                FALLBACK_API_KEY_VARIABLE,
                CLAUDE_CODE_USE_API_KEY_VARIABLE,
                CLAUDE_CODE_PATH_VARIABLE,
            )
        }
        environ.update(
            {
                "FAKE_CLAUDE_MODE": mode,
                "FAKE_CLAUDE_RECORD": str(self.record_path),
                "FAKE_CLAUDE_ANSWER": json.dumps(ANSWER),
            }
        )
        environ.update(extra)
        return environ

    def provider(
        self,
        mode: str,
        *,
        timeout: Optional[float] = None,
        effort: Optional[str] = None,
        **extra: str,
    ) -> ClaudeCodeTextToCadModel:
        config = AiConfig(
            provider=CLAUDE_CODE_PROVIDER_NAME,
            model=DEFAULT_MODELS[CLAUDE_CODE_PROVIDER_NAME],
            timeout_seconds=timeout or self.timeout_seconds,
            effort=effort,
        )
        return ClaudeCodeTextToCadModel(
            str(self.executable),
            config,
            root=self.root,
            environ=self.environ(mode, **extra),
        )

    def request(self, schema: Optional[Mapping[str, Any]] = SCHEMA) -> ModelRequest:
        return ModelRequest(
            system=SYSTEM_TEXT,
            user_text=USER_TEXT,
            output_schema=schema,
            max_output_tokens=12345,
        )

    def record(self) -> Dict[str, Any]:
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def assert_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 10
        while process_is_running(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(process_is_running(pid), f"process {pid} was left running")


# --- the command line --------------------------------------------------------


class TestCommandLine(FakeClaudeTestCase):
    def test_every_flag_is_present_and_carries_its_value(self) -> None:
        self.provider("tool").generate(self.request())
        argv: List[str] = self.record()["argv"]

        def value(flag: str) -> str:
            self.assertIn(flag, argv)
            return argv[argv.index(flag) + 1]

        self.assertEqual(argv[0], "-p")
        self.assertEqual(value("--input-format"), "stream-json")
        self.assertEqual(value("--output-format"), "stream-json")
        self.assertIn("--verbose", argv)
        self.assertEqual(value("--model"), "claude-haiku-5-5")
        self.assertEqual(value("--tools"), "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(value("--allowedTools"), "mcp__cad__submit_answer")
        self.assertEqual(value("--permission-mode"), "dontAsk")
        self.assertEqual(value("--setting-sources"), "")
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--no-session-persistence", argv)
        self.assertEqual(value("--max-turns"), "1")
        self.assertNotIn("--effort", argv)
        # Never: a shell, the dangerous permission bypass, or --bare (which
        # would ignore the subscription login and demand an API key).
        for forbidden in (
            "--dangerously-skip-permissions",
            "--allow-dangerously-skip-permissions",
            "--bare",
            "--system-prompt",
            "--append-system-prompt",
        ):
            self.assertNotIn(forbidden, argv)

    def test_the_prompt_is_in_a_file_named_by_its_hash_and_not_on_the_line(self) -> None:
        self.provider("tool").generate(self.request())
        argv: List[str] = self.record()["argv"]
        prompt_path = Path(argv[argv.index("--system-prompt-file") + 1])
        self.assertEqual(prompt_path.read_text(encoding="utf-8"), SYSTEM_TEXT)
        digest = hashlib.sha256(SYSTEM_TEXT.encode("utf-8")).hexdigest()
        self.assertIn(digest, prompt_path.name)
        joined = "\n".join(argv)
        self.assertNotIn("SYSTEM-PROMPT-SENTINEL", joined)
        self.assertNotIn("USER-TEXT-SENTINEL", joined)

    def test_no_credential_reaches_the_command_line(self) -> None:
        provider = self.provider(
            "tool",
            **{API_KEY_VARIABLE: SYNTHETIC_KEY, FALLBACK_API_KEY_VARIABLE: SYNTHETIC_KEY},
        )
        provider.generate(self.request())
        record = self.record()
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(record["argv"]))
        # Nor the MCP configuration the CLI reads.
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(record["mcp_config"]))

    def test_the_user_text_arrives_on_stdin_as_one_stream_json_message(self) -> None:
        self.provider("tool").generate(self.request())
        line = self.record()["stdin_first_line"]
        self.assertTrue(line.endswith("\n"))
        message = json.loads(line)
        self.assertEqual(message["type"], "user")
        self.assertEqual(message["message"]["role"], "user")
        self.assertEqual(
            message["message"]["content"], [{"type": "text", "text": USER_TEXT}]
        )

    def test_it_runs_in_an_empty_folder_of_its_own(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        self.assertEqual(
            Path(record["cwd"]).resolve(), (self.root / "cwd").resolve()
        )
        self.assertEqual(record["cwd_entries"], [])
        repo = Path(__file__).resolve().parents[3]
        self.assertNotIn(str(repo), record["cwd"])

    def test_effort_is_passed_only_when_configured(self) -> None:
        self.provider("tool", effort="low").generate(self.request())
        argv = self.record()["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "low")

    def test_the_pure_builder_matches(self) -> None:
        argv = command_line(
            "/x/claude", model="m", prompt_file="/p", mcp_config_file="/c"
        )
        self.assertEqual(argv[0], "/x/claude")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], "/c")
        self.assertEqual(argv[argv.index("--system-prompt-file") + 1], "/p")

    def test_call_files_are_removed_and_the_prompt_is_kept(self) -> None:
        provider = self.provider("tool")
        provider.generate(self.request())
        provider.generate(self.request())
        self.assertEqual(list((self.root / "calls").iterdir()), [])
        self.assertEqual(len(list((self.root / "prompts").glob("system-*.txt"))), 1)


# --- the environment ---------------------------------------------------------


class TestEnvironment(FakeClaudeTestCase):
    KEYS = (API_KEY_VARIABLE, FALLBACK_API_KEY_VARIABLE, "ANTHROPIC_AUTH_TOKEN")

    def test_both_api_key_variables_are_removed_by_default(self) -> None:
        extra = {name: SYNTHETIC_KEY for name in self.KEYS}
        extra[GEMINI_API_KEY_VARIABLE] = SYNTHETIC_KEY
        extra["CLAUDECODE"] = "1"
        self.provider("tool", **extra).generate(self.request())
        present = self.record()["env_present"]
        for name in self.KEYS + (GEMINI_API_KEY_VARIABLE, "CLAUDECODE"):
            self.assertFalse(present[name], f"{name} reached the child")
        self.assertTrue(present["PATH"])
        self.assertEqual(self.record()["max_output_tokens"], "12345")
        self.assertTrue(present["ENABLE_CLAUDEAI_MCP_SERVERS"])

    def test_the_opt_in_keeps_the_api_key_variables(self) -> None:
        extra = {name: SYNTHETIC_KEY for name in self.KEYS}
        extra[GEMINI_API_KEY_VARIABLE] = SYNTHETIC_KEY
        extra[CLAUDE_CODE_USE_API_KEY_VARIABLE] = "1"
        self.provider("tool", **extra).generate(self.request())
        present = self.record()["env_present"]
        for name in self.KEYS:
            self.assertTrue(present[name], f"{name} was removed despite the opt-in")
        # Another provider's credential is never needed by the CLI.
        self.assertFalse(present[GEMINI_API_KEY_VARIABLE])

    def test_the_pure_function_drops_by_name_case_insensitively(self) -> None:
        source = {
            "Path": "/bin",
            "anthropic_api_key": SYNTHETIC_KEY,
            CLAUDE_CODE_USE_API_KEY_VARIABLE: "0",
        }
        child = child_environment(source, max_output_tokens=10)
        self.assertNotIn("anthropic_api_key", child)
        self.assertEqual(child["Path"], "/bin")
        self.assertEqual(child["CLAUDE_CODE_MAX_OUTPUT_TOKENS"], "10")
        # The caller's mapping is not modified.
        self.assertIn("anthropic_api_key", source)


# --- the answer --------------------------------------------------------------


class TestAnswer(FakeClaudeTestCase):
    def test_the_answer_is_the_tool_calls_input(self) -> None:
        response = self.provider("tool").generate(self.request())
        self.assertEqual(json.loads(response.text), ANSWER)
        self.assertEqual(response.text, json.dumps(ANSWER))
        self.assertEqual(response.provider, "claude_code")
        self.assertEqual(response.model, "claude-haiku-5-5-from-message")
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertFalse(response.structured_output)
        # Token counts from the result event, known fields only.
        self.assertEqual(
            dict(response.usage),
            {
                "input_tokens": 11,
                "output_tokens": 22,
                "cache_read_input_tokens": 33,
                "cache_creation_input_tokens": 44,
            },
        )

    def test_the_real_mcp_server_offered_the_tool_and_acknowledged_it(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        initialize, listing, call = record["mcp_replies"]
        self.assertEqual(initialize["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("tools", initialize["result"]["capabilities"])
        tools = listing["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], ANSWER_TOOL_NAME)
        self.assertEqual(tools[0]["description"], ANSWER_TOOL_DESCRIPTION)
        # The same schema the API provider gives its tool.
        self.assertEqual(tools[0]["inputSchema"], schema_for_api(SCHEMA))
        self.assertEqual(
            call["result"],
            {"content": [{"type": "text", "text": "Answer received."}], "isError": False},
        )
        self.assertEqual(record["mcp_returncode"], 0)
        # Started with this interpreter, isolated, by file path.
        config = record["mcp_config"]
        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(config["args"][0], "-I")
        self.assertTrue(config["args"][1].endswith("claude_code_mcp.py"))

    def test_with_no_schema_the_tool_takes_any_object(self) -> None:
        self.provider("tool").generate(self.request(schema=None))
        tools = self.record()["mcp_replies"][1]["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"], {"type": "object"})

    def test_with_no_tool_call_the_result_text_is_the_fallback(self) -> None:
        response = self.provider("text").generate(self.request())
        self.assertEqual(response.text, "plain text answer")
        self.assertEqual(response.stop_reason, "end_turn")
        self.assertEqual(response.usage["output_tokens"], 22)

    def test_a_cut_off_answer_is_returned_as_text_for_the_re_ask(self) -> None:
        # Not a provider error: the generation layer reports unusable text
        # and asks again, exactly as for any other unusable answer.
        response = self.provider("truncated").generate(self.request())
        self.assertIn("output token maximum", response.text)

    def test_the_process_is_ended_once_the_answer_is_in(self) -> None:
        provider = self.provider("answer_then_hang")
        provider.result_grace_seconds = 0.5
        started = time.monotonic()
        response = provider.generate(self.request())
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(json.loads(response.text), ANSWER)
        self.assert_gone(self.record()["pid"])

    def test_a_permission_question_allows_only_the_answer_tool(self) -> None:
        response = self.provider("control").generate(self.request())
        self.assertEqual(json.loads(response.text), ANSWER)
        record = self.record()
        allowed = record["control_reply"]["response"]
        self.assertEqual(allowed["request_id"], "r1")
        self.assertEqual(allowed["response"]["behavior"], "allow")
        denied = record["control_reply_2"]["response"]
        self.assertEqual(denied["response"]["behavior"], "deny")


# --- failures ----------------------------------------------------------------


class TestFailures(FakeClaudeTestCase):
    def assert_public_message_is_clean(self, error: ProviderError) -> None:
        for leak in ("stderr", "cli.js", "login", "TypeError", "usage limit"):
            self.assertNotIn(leak, error.message)

    def test_a_timeout_kills_the_child_and_what_it_started(self) -> None:
        provider = self.provider("hang", timeout=2.0)
        started = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.request())
        self.assertLess(time.monotonic() - started, 20)
        self.assertIs(caught.exception.kind, ProviderErrorKind.TIMEOUT)
        self.assertNotIsInstance(caught.exception, ProviderNotConfigured)
        record = self.record()
        self.assert_gone(record["pid"])
        self.assert_gone(record["grandchild_pid"])

    def test_a_timeout_during_rate_limit_retries_is_a_rate_limit(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("retry_then_hang", timeout=1.5).generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.RATE_LIMITED)
        self.assert_gone(self.record()["pid"])

    def test_not_logged_in_is_an_authentication_failure(self) -> None:
        for mode in ("auth", "login_result"):
            with self.subTest(mode=mode):
                with self.assertRaises(ProviderError) as caught:
                    self.provider(mode).generate(self.request())
                self.assertIs(caught.exception.kind, ProviderErrorKind.AUTHENTICATION)
                self.assert_public_message_is_clean(caught.exception)

    def test_a_crash_is_other_with_the_stderr_tail_in_the_detail_only(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("crash").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.OTHER)
        self.assertIn("something internal went wrong", caught.exception.detail or "")
        self.assertIn("exit code 3", caught.exception.detail or "")
        self.assert_public_message_is_clean(caught.exception)

    def test_a_usage_limit_is_rate_limited(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("usage_limit").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.RATE_LIMITED)
        self.assert_public_message_is_clean(caught.exception)

    def test_nothing_at_all_is_an_empty_response(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("silent").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.EMPTY_RESPONSE)

    def test_an_executable_that_vanished_is_not_configured(self) -> None:
        provider = self.provider("tool")
        self.executable.unlink()
        with self.assertRaises(ProviderNotConfigured):
            provider.generate(self.request())


# --- finding the CLI, configuration and dispatch ------------------------------


class TestSelection(FakeClaudeTestCase):
    def test_a_missing_cli_is_not_configured(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        environment = {"PATH": str(empty)}
        with mock.patch.dict(os.environ, environment, clear=True):
            self.assertIsNone(locate_cli())
            with self.assertRaises(ProviderNotConfigured) as caught:
                ClaudeCodeTextToCadModel.from_environment(
                    AiConfig(provider=CLAUDE_CODE_PROVIDER_NAME)
                )
            self.assertIs(caught.exception.kind, ProviderErrorKind.NOT_CONFIGURED)
            self.assertFalse(credential_available(provider=CLAUDE_CODE_PROVIDER_NAME))

    def test_the_cli_is_found_on_path_or_at_the_configured_path(self) -> None:
        on_path = {"PATH": str(self.bin)}
        self.assertEqual(
            Path(locate_cli(on_path) or "").resolve(), self.executable.resolve()
        )
        configured = {"PATH": "", CLAUDE_CODE_PATH_VARIABLE: str(self.executable)}
        self.assertEqual(
            Path(locate_cli(configured) or "").resolve(), self.executable.resolve()
        )

    def test_a_wrong_configured_path_never_falls_back_to_path(self) -> None:
        environment = {
            "PATH": str(self.bin),
            CLAUDE_CODE_PATH_VARIABLE: str(self.tmp / "nowhere" / "claude"),
        }
        self.assertIsNone(locate_cli(environment))
        self.assertFalse(
            credential_available(environment, provider=CLAUDE_CODE_PROVIDER_NAME)
        )

    def test_resolve_provider_never_picks_claude_code_by_itself(self) -> None:
        self.assertNotIn(CLAUDE_CODE_PROVIDER_NAME, AUTOMATIC_PROVIDER_NAMES)
        self.assertIn(CLAUDE_CODE_PROVIDER_NAME, PROVIDER_NAMES)
        # The CLI is installed and no credential is set: still not chosen.
        installed = {"PATH": str(self.bin)}
        self.assertEqual(resolve_provider(installed), "anthropic")
        self.assertEqual(
            resolve_provider({**installed, GEMINI_API_KEY_VARIABLE: "x"}), "gemini"
        )
        # An installed CLI is not a credential for the ordinary suite.
        self.assertFalse(credential_available(installed))
        # Only by name.
        named = {PROVIDER_VARIABLE: "claude_code"}
        self.assertEqual(resolve_provider(named), CLAUDE_CODE_PROVIDER_NAME)
        config = config_from_environment(named)
        self.assertEqual(config.provider, CLAUDE_CODE_PROVIDER_NAME)
        self.assertEqual(config.model, "claude-haiku-5-5")

    def test_the_factory_dispatches_to_the_cli_provider(self) -> None:
        config = AiConfig(provider=CLAUDE_CODE_PROVIDER_NAME)
        with mock.patch.dict(
            os.environ, {CLAUDE_CODE_PATH_VARIABLE: str(self.executable)}
        ):
            model = model_from_environment(config)
        self.assertIsInstance(model, ClaudeCodeTextToCadModel)
        self.assertEqual(model.name, "claude_code")
        self.assertEqual(Path(model.executable).resolve(), self.executable.resolve())
        with mock.patch.dict(
            os.environ,
            {CLAUDE_CODE_PATH_VARIABLE: str(self.tmp / "missing" / "claude")},
        ):
            with self.assertRaises(ProviderNotConfigured):
                model_from_environment(config)


# --- the MCP server on its own -----------------------------------------------


class TestMcpServer(unittest.TestCase):
    TOOL = {
        "name": ANSWER_TOOL_NAME,
        "description": ANSWER_TOOL_DESCRIPTION,
        "inputSchema": {"type": "object", "properties": {"x": {"type": "number"}}},
    }

    def test_the_protocol_without_a_process(self) -> None:
        handle = claude_code_mcp.handle
        reply = handle(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "1999-01-01"}},
            self.TOOL,
        )
        self.assertEqual(
            reply["result"]["protocolVersion"], claude_code_mcp.DEFAULT_PROTOCOL_VERSION
        )
        self.assertIsNone(
            handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.TOOL)
        )
        self.assertEqual(handle({"jsonrpc": "2.0", "id": 7, "method": "ping"}, self.TOOL),
                         {"jsonrpc": "2.0", "id": 7, "result": {}})
        unknown_tool = handle(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "Bash"}},
            self.TOOL,
        )
        self.assertEqual(unknown_tool["error"]["code"], -32602)
        unknown = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, self.TOOL)
        self.assertEqual(unknown["error"]["code"], -32601)
        self.assertEqual(handle([1, 2], self.TOOL)["error"]["code"], -32600)
        # A response from the client gets no reply.
        self.assertIsNone(handle({"jsonrpc": "2.0", "id": 9, "result": {}}, self.TOOL))

    def test_the_server_over_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tool_file = Path(directory) / "tool.json"
            tool_file.write_text(json.dumps(self.TOOL), encoding="utf-8")
            server = AI_SOURCE / "claude_code_mcp.py"
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                            "clientInfo": {"name": "t", "version": "0"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": ANSWER_TOOL_NAME,
                            "arguments": {"x": 1, "text": "line one\nline two ·"}}},
                {"jsonrpc": "2.0", "id": 4, "method": "ping"},
            ]
            payload = b"".join(
                json.dumps(message).encode("utf-8") + b"\n" for message in messages
            ) + b"this is not json\n"
            completed = subprocess.run(
                [sys.executable, "-I", str(server), str(tool_file)],
                input=payload,
                capture_output=True,
                timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.endswith(b"\n"))
        self.assertNotIn(b"\r\n", completed.stdout)
        lines = completed.stdout.decode("utf-8").splitlines()
        replies = [json.loads(line) for line in lines]
        # One reply per request; none for the notification.
        self.assertEqual([reply.get("id") for reply in replies], [1, 2, 3, 4, None])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "cad-answer")
        self.assertEqual(replies[1]["result"]["tools"], [self.TOOL])
        self.assertEqual(
            replies[2]["result"]["content"], [{"type": "text", "text": "Answer received."}]
        )
        self.assertEqual(replies[4]["error"]["code"], -32700)

    def test_a_bad_tool_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tool.json"
            path.write_text("[]", encoding="utf-8")
            with mock.patch.object(sys, "stderr", new=io.StringIO()):
                self.assertEqual(claude_code_mcp.main([str(path)]), 2)
                self.assertEqual(claude_code_mcp.main([]), 2)


# --- what the modules import -------------------------------------------------


def _imports(path: Path) -> List[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


class TestModuleBoundaries(unittest.TestCase):
    def test_the_mcp_server_is_standard_library_only(self) -> None:
        names = _imports(AI_SOURCE / "claude_code_mcp.py")
        self.assertTrue(set(names) <= {"__future__", "json", "sys", "typing"}, names)

    def test_the_provider_uses_no_sdk_and_no_shell(self) -> None:
        source = (AI_SOURCE / "claude_code_provider.py").read_text(encoding="utf-8")
        names = _imports(AI_SOURCE / "claude_code_provider.py")
        for forbidden in ("anthropic", "google", "google.genai", "openai"):
            self.assertNotIn(forbidden, names)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                if isinstance(target, ast.Name):
                    self.assertNotIn(target.id, ("eval", "exec", "compile"))
                elif isinstance(target, ast.Attribute):
                    self.assertNotIn(target.attr, ("system", "popen", "startfile"))
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        self.fail("a subprocess is started through a shell")

    def test_the_answer_tool_name_is_the_prompts(self) -> None:
        self.assertEqual(ANSWER_TOOL, f"mcp__cad__{ANSWER_TOOL_NAME}")


if __name__ == "__main__":
    unittest.main()
