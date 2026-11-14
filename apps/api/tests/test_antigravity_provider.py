"""The Antigravity provider, proved against a fake ``agy`` -- never the real one.

**No test in this file runs the real Antigravity CLI or contacts any model.**
Every call goes to a small fake ``agy`` written to a temporary folder by the
test: a Python script behind a shim (a ``#!`` line on POSIX, a ``.cmd`` file
on Windows) that speaks the CLI's documented stream-json records according
to a mode the test chooses, and records what it was given -- its argv, its
working folder and what is in it, which environment variable *names* it saw,
and what arrived on its stdin. In its tool modes it also acts as the MCP
client: it reads the workspace's ``.agents/mcp_config.json`` exactly as the
CLI would, starts the real :mod:`cad_ai.claude_code_mcp` server from it, and
lists and calls its tool -- so the answer reaches the provider through the
server's **capture file**, end to end.

What this establishes: the command line (the documented flags, and no
prompt, user text, path or credential on it), the one stdin record, the
per-call workspace and its MCP configuration, the environment the child gets
(Google keys removed unless opted in; Anthropic keys and every Claude session
variable always removed), how the answer is taken (the capture; the result
text, then plain stdout, as fallbacks), that a timeout kills the child **and
what it started**, how failures map onto the neutral error kinds, and that
the provider is only ever selected by name.
"""

from __future__ import annotations

import ast
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
from cad_ai.antigravity_provider import (
    AGENT_MODE,
    ALLOW_RULE,
    MCP_SERVER_NAME,
    AntigravityTextToCadModel,
    child_environment,
    command_line,
    compose_message,
    locate_cli,
)
from cad_ai.config import (
    ANTIGRAVITY_PATH_VARIABLE,
    ANTIGRAVITY_PROVIDER_NAME,
    ANTIGRAVITY_USE_API_KEY_VARIABLE,
    API_KEY_VARIABLE,
    AUTOMATIC_PROVIDER_NAMES,
    CLI_PATH_VARIABLES,
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
SYNTHETIC_KEY = "AIza-SYNTHETIC-TEST-KEY-DO-NOT-USE-000"

SYSTEM_TEXT = "SYSTEM-PROMPT-SENTINEL: answer with the tool."
USER_TEXT = "a 100 x 60 x 10 mm plate -- USER-TEXT-SENTINEL"

ANSWER: Dict[str, Any] = {
    "status": "document",
    "plan": "one box",
    "assumptions": [],
    "document": {"schema_version": "1.2.0", "units": "mm", "features": []},
    "summary": "a plate · ünïcode",
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

#: Variable names the fake reports the presence of. Values are never read.
WATCHED = (
    "PATH",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "AGY_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ANTHROPIC_API_KEY",
    "CAD_ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_SESSION_ID",
    "CLAUDE_REMOTE_ACCESS_TOKEN",
    "UNRELATED_SETTING",
)

#: The fake ``agy``. Reads its mode and its record path from the environment
#: (both pass through the provider's environment untouched).
FAKE_AGY = r'''
import json, os, subprocess, sys, time

MODE = os.environ.get("FAKE_AGY_MODE", "tool")
RECORD = os.environ["FAKE_AGY_RECORD"]
ANSWER = json.loads(os.environ.get("FAKE_AGY_ANSWER", "{}"))
WATCHED = json.loads(os.environ["FAKE_AGY_WATCHED"])

def tree(root):
    found = []
    for directory, _, files in os.walk(root):
        for name in files:
            found.append(os.path.relpath(os.path.join(directory, name), root).replace(os.sep, "/"))
    return sorted(found)

record = {
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "cwd_files": tree(os.getcwd()),
    "pid": os.getpid(),
    "env_present": {name: name in os.environ for name in WATCHED},
}

def save():
    with open(RECORD, "w", encoding="utf-8") as handle:
        json.dump(record, handle)

def emit(record_):
    sys.stdout.buffer.write((json.dumps(record_) + "\n").encode("utf-8"))
    sys.stdout.flush()

def mcp_exchange(arguments, calls=1):
    """Act as the CLI's MCP client against the workspace's configured server."""
    config = json.load(open(os.path.join(".agents", "mcp_config.json"), encoding="utf-8"))
    record["mcp_config"] = config
    server = config["mcpServers"]["text_to_cad"]
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
                     "clientInfo": {"name": "fake-agy", "version": "0"}}})
    call({"jsonrpc": "2.0", "method": "notifications/initialized"}, False)
    call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    for index in range(calls):
        payload = arguments if index == 0 else {"second": True}
        call({"jsonrpc": "2.0", "id": 3 + index, "method": "tools/call",
              "params": {"name": replies[1]["result"]["tools"][0]["name"],
                         "arguments": payload}})
    record["mcp_replies"] = replies
    return child

def finish_mcp(child):
    child.stdin.close()
    record["mcp_returncode"] = child.wait(timeout=30)

first = sys.stdin.buffer.readline().decode("utf-8")
record["stdin_first_line"] = first
rest = sys.stdin.buffer.read()  # returns only once the provider closes stdin
record["stdin_closed_after_one_record"] = rest == b""
save()

init = {"event": "init", "conversation_id": "c-1",
        "init": {"cwd": os.getcwd(), "permission_mode": "accept-edits",
                 "model": "gemini-3.8-flash-from-init"}}
usage = {"input_tokens": 11, "output_tokens": 22, "thoughts_tokens": 5}

def step(index, kind, state):
    return {"event": "step_update", "step_update": {"conversation_id": "c-1",
            "step_index": index, "step_type": kind, "state": state}}

def result(status="SUCCESS", response="Done.", **extra):
    payload = {"conversation_id": "c-1", "status": status, "response": response,
               "num_turns": 1, "usage": usage}
    payload.update(extra)
    return {"event": "result", "result": payload}

if MODE in ("tool", "tool_twice"):
    emit(init)
    emit(step(0, "user_input", "DONE"))
    emit(step(1, "tool", "ACTIVE"))
    child = mcp_exchange(ANSWER, calls=2 if MODE == "tool_twice" else 1)
    emit(step(1, "tool", "DONE"))
    emit(step(2, "agent_response", "DONE"))
    emit(result())
    finish_mcp(child)
    save()
    sys.exit(0)
elif MODE == "tool_then_hang":
    emit(init)
    child = mcp_exchange(ANSWER)
    record["mcp_pid"] = child.pid
    save()
    time.sleep(120)
elif MODE == "text":
    emit(init)
    # A payload laid out at the top level is accepted as well.
    emit({"event": "result", "status": "SUCCESS", "response": "plain text answer",
          "usage": {"promptTokenCount": 7, "candidatesTokenCount": 8}})
elif MODE == "plain_stdout":
    sys.stdout.write("an old CLI's plain text answer\n")
    sys.stdout.flush()
elif MODE == "silent":
    sys.exit(0)
elif MODE == "denied":
    emit(init)
    sys.stderr.write('Tool "mcp_text_to_cad_submit_answer" required the "mcp" permission '
                     'that headless mode cannot prompt for, so it was auto-denied. Add an '
                     'allow-rule under permissions.allow in settings.json (e.g. mcp(<target>)) '
                     'or re-run with --dangerously-skip-permissions.\n')
    sys.stderr.flush()
    emit(result(response="I could not submit the answer.",
                denied_actions=["mcp(text_to_cad/submit_answer)"]))
elif MODE == "auth":
    sys.stderr.write("Error: authentication required\n")
    sys.exit(1)
elif MODE == "auth_result":
    emit({"event": "init", "conversation_id": "", "init": {"cwd": os.getcwd()}})
    emit(result(status="ERROR", response="", error="authentication failed or timed out"))
elif MODE == "quota":
    emit(init)
    sys.stderr.write("Individual quota reached. Resets in 3h12m.\n")
    sys.stderr.write('AGY_ERROR: {"status":"RESOURCE_EXHAUSTED","error_code":429,'
                     '"code_kind":"http","retryable":true,"error_id":"x"}\n')
    sys.stderr.flush()
    emit(result(status="ERROR", response="partial", error="model API failure",
                finished_reason="error"))
    sys.exit(3)
elif MODE == "quota_then_hang":
    emit(init)
    sys.stderr.write("429 Too Many Requests: RESOURCE_EXHAUSTED, retrying\n")
    sys.stderr.flush()
    time.sleep(120)
elif MODE == "hang":
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    record["grandchild_pid"] = grandchild.pid
    save()
    emit(init)
    time.sleep(120)
elif MODE == "unknown_flag":
    sys.stderr.write("flag provided but not defined: -mode\nUsage of agy:\n")
    sys.exit(2)
elif MODE == "crash":
    sys.stderr.write("panic: something internal went wrong at agent.go:12\n")
    sys.exit(5)
'''


def process_is_running(pid: int) -> bool:
    """Whether ``pid`` is a live process (a zombie counts as gone)."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows only
        import ctypes

        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
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


class FakeAgyTestCase(unittest.TestCase):
    """A temporary folder holding a fake ``agy`` and the provider's files."""

    timeout_seconds = 30.0

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.tmp = Path(self._directory.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        script = self.tmp / "fake_agy.py"
        script.write_text(textwrap.dedent(FAKE_AGY), encoding="utf-8")
        if os.name == "nt":  # pragma: no cover - exercised on Windows only
            self.executable = self.bin / "agy.cmd"
            self.executable.write_text(
                f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
            )
        else:
            self.executable = self.bin / "agy"
            self.executable.write_text(
                f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self.executable.chmod(0o755)
        self.record_path = self.tmp / "record.json"
        self.root = self.tmp / "provider-root"
        self.home = self.tmp / "home"
        self.home.mkdir()

    def environ(self, mode: str, **extra: str) -> Dict[str, str]:
        environ = {
            name: value
            for name, value in os.environ.items()
            if name
            not in (
                ANTIGRAVITY_USE_API_KEY_VARIABLE,
                ANTIGRAVITY_PATH_VARIABLE,
            )
        }
        environ.update(
            {
                "FAKE_AGY_MODE": mode,
                "FAKE_AGY_RECORD": str(self.record_path),
                "FAKE_AGY_ANSWER": json.dumps(ANSWER),
                "FAKE_AGY_WATCHED": json.dumps(WATCHED),
                # A home of its own: proves nothing is written to the user's
                # global configuration.
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
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
    ) -> AntigravityTextToCadModel:
        config = AiConfig(
            provider=ANTIGRAVITY_PROVIDER_NAME,
            model=DEFAULT_MODELS[ANTIGRAVITY_PROVIDER_NAME],
            timeout_seconds=timeout or self.timeout_seconds,
            effort=effort,
        )
        return AntigravityTextToCadModel(
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


# --- the command line and what is sent ----------------------------------------


class TestCommandLine(FakeAgyTestCase):
    def test_the_documented_flags_and_nothing_else(self) -> None:
        self.provider("tool").generate(self.request())
        argv: List[str] = self.record()["argv"]
        self.assertEqual(
            argv,
            [
                "--input-format", "stream-json",
                "--output-format", "stream-json",
                "--model", "gemini-3.8-flash",
                "--mode", AGENT_MODE,
                "--disable-slash-commands",
            ],
        )
        self.assertEqual(AGENT_MODE, "accept-edits")
        # Never: a prompt flag (dropped in this mode, and visible to `ps`),
        # the permission bypass, or Auto mode.
        for forbidden in ("-p", "--print", "--prompt", "--dangerously-skip-permissions",
                          "auto", "--sandbox"):
            self.assertNotIn(forbidden, argv)

    def test_no_prompt_user_text_path_or_credential_on_the_line(self) -> None:
        extra = {GEMINI_API_KEY_VARIABLE: SYNTHETIC_KEY, "GOOGLE_API_KEY": SYNTHETIC_KEY}
        self.provider("tool", **extra).generate(self.request())
        record = self.record()
        joined = "\n".join(record["argv"])
        for leak in ("SYSTEM-PROMPT-SENTINEL", "USER-TEXT-SENTINEL", SYNTHETIC_KEY,
                     str(self.root), str(self.tmp)):
            self.assertNotIn(leak, joined)
        # Nor in the MCP configuration the CLI reads.
        self.assertNotIn(SYNTHETIC_KEY, json.dumps(record["mcp_config"]))

    def test_effort_is_passed_only_when_configured(self) -> None:
        self.provider("tool", effort="high").generate(self.request())
        argv = self.record()["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_one_stream_json_record_carries_prompt_then_request_and_stdin_closes(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        line = record["stdin_first_line"]
        self.assertTrue(line.endswith("\n"))
        message = json.loads(line)
        self.assertEqual(message, {"event": "user", "message": {"content": message["message"]["content"]}})
        content = message["message"]["content"]
        self.assertEqual(content, compose_message(SYSTEM_TEXT, USER_TEXT))
        self.assertLess(content.index(SYSTEM_TEXT), content.index(USER_TEXT))
        self.assertTrue(content.endswith(USER_TEXT))
        self.assertIn(ANSWER_TOOL_NAME, content)
        self.assertTrue(record["stdin_closed_after_one_record"])

    def test_the_pure_builder(self) -> None:
        argv = command_line("/x/agy", model="m")
        self.assertEqual(argv[0], "/x/agy")
        self.assertEqual(argv[argv.index("--model") + 1], "m")
        self.assertNotIn("--effort", argv)


# --- the workspace and its MCP configuration ----------------------------------


class TestWorkspace(FakeAgyTestCase):
    def test_a_fresh_workspace_holding_only_the_mcp_config(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        self.assertEqual(record["cwd_files"], [".agents/mcp_config.json"])
        cwd = Path(record["cwd"]).resolve()
        self.assertTrue(str(cwd).startswith(str((self.root / "calls").resolve())))
        repo = Path(__file__).resolve().parents[3]
        self.assertNotIn(str(repo), record["cwd"])

    def test_the_mcp_config_registers_one_server_with_one_tool(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        servers = record["mcp_config"]["mcpServers"]
        self.assertEqual(list(servers), [MCP_SERVER_NAME])
        server = servers[MCP_SERVER_NAME]
        self.assertEqual(server["command"], sys.executable)
        self.assertEqual(server["args"][0], "-I")
        self.assertTrue(server["args"][1].endswith("claude_code_mcp.py"))
        self.assertEqual(server["args"][3], claude_code_mcp.CAPTURE_OPTION)
        self.assertEqual(server["enabledTools"], [ANSWER_TOOL_NAME])
        # The tool file and the capture live outside the workspace.
        workspace = Path(record["cwd"]).resolve()
        for path in (server["args"][2], server["args"][4]):
            self.assertNotIn(str(workspace), str(Path(path).resolve()))
        self.assertEqual(ALLOW_RULE, f"mcp({MCP_SERVER_NAME}/{ANSWER_TOOL_NAME})")

    def test_the_real_server_offered_the_tool_with_the_schema(self) -> None:
        self.provider("tool").generate(self.request())
        record = self.record()
        initialize, listing, call = record["mcp_replies"]
        self.assertIn("tools", initialize["result"]["capabilities"])
        tools = listing["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], ANSWER_TOOL_NAME)
        self.assertEqual(tools[0]["description"], ANSWER_TOOL_DESCRIPTION)
        # Gemini takes the response schema as it is; it is not narrowed.
        self.assertEqual(tools[0]["inputSchema"], SCHEMA)
        self.assertEqual(
            call["result"],
            {"content": [{"type": "text", "text": "Answer received."}], "isError": False},
        )
        self.assertEqual(record["mcp_returncode"], 0)

    def test_with_no_schema_the_tool_takes_any_object(self) -> None:
        self.provider("tool").generate(self.request(schema=None))
        tools = self.record()["mcp_replies"][1]["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"], {"type": "object"})

    def test_call_files_are_removed_and_no_user_configuration_is_written(self) -> None:
        provider = self.provider("tool")
        provider.generate(self.request())
        provider.generate(self.request())
        self.assertEqual(list((self.root / "calls").iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])


# --- the environment ---------------------------------------------------------


class TestEnvironment(FakeAgyTestCase):
    GOOGLE = (
        GEMINI_API_KEY_VARIABLE,
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_API_KEY",
        "AGY_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    )
    ALWAYS = (
        API_KEY_VARIABLE,
        FALLBACK_API_KEY_VARIABLE,
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDECODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_SESSION_ID",
        "CLAUDE_REMOTE_ACCESS_TOKEN",
    )

    def extra(self) -> Dict[str, str]:
        values = {name: SYNTHETIC_KEY for name in self.GOOGLE + self.ALWAYS}
        values["UNRELATED_SETTING"] = "kept"
        return values

    def test_google_keys_and_claude_session_variables_are_removed(self) -> None:
        self.provider("tool", **self.extra()).generate(self.request())
        present = self.record()["env_present"]
        for name in self.GOOGLE + self.ALWAYS:
            self.assertFalse(present[name], f"{name} reached the child")
        self.assertTrue(present["PATH"])
        self.assertTrue(present["UNRELATED_SETTING"])

    def test_the_opt_in_keeps_google_keys_and_never_the_claude_ones(self) -> None:
        extra = self.extra()
        extra[ANTIGRAVITY_USE_API_KEY_VARIABLE] = "1"
        self.provider("tool", **extra).generate(self.request())
        present = self.record()["env_present"]
        for name in self.GOOGLE:
            self.assertTrue(present[name], f"{name} was removed despite the opt-in")
        for name in self.ALWAYS:
            self.assertFalse(present[name], f"{name} reached the child")

    def test_the_pure_function_drops_by_name_case_insensitively(self) -> None:
        source = {
            "Path": "/bin",
            "gemini_api_key": SYNTHETIC_KEY,
            "Claude_Code_Something": "x",
            "claude_session_ingress_token": "x",
            ANTIGRAVITY_USE_API_KEY_VARIABLE: "0",
        }
        child = child_environment(source)
        self.assertEqual(child, {"Path": "/bin", ANTIGRAVITY_USE_API_KEY_VARIABLE: "0"})
        self.assertIn("gemini_api_key", source)  # the caller's mapping is untouched


# --- the answer --------------------------------------------------------------


class TestAnswer(FakeAgyTestCase):
    def test_the_answer_is_the_captured_tool_call(self) -> None:
        response = self.provider("tool").generate(self.request())
        self.assertEqual(json.loads(response.text), ANSWER)
        self.assertEqual(response.text, json.dumps(ANSWER))
        self.assertEqual(response.provider, "antigravity")
        self.assertEqual(response.model, "gemini-3.8-flash-from-init")
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertFalse(response.structured_output)
        self.assertEqual(dict(response.usage), {"input_tokens": 11, "output_tokens": 22})

    def test_only_the_first_call_is_captured(self) -> None:
        response = self.provider("tool_twice").generate(self.request())
        self.assertEqual(json.loads(response.text), ANSWER)

    def test_the_process_and_its_server_end_once_the_answer_is_in(self) -> None:
        provider = self.provider("tool_then_hang")
        provider.result_grace_seconds = 0.5
        started = time.monotonic()
        response = provider.generate(self.request())
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(json.loads(response.text), ANSWER)
        record = self.record()
        self.assert_gone(record["pid"])
        self.assert_gone(record["mcp_pid"])

    def test_with_no_capture_the_result_response_is_the_fallback(self) -> None:
        response = self.provider("text").generate(self.request())
        self.assertEqual(response.text, "plain text answer")
        self.assertEqual(response.stop_reason, "SUCCESS")
        self.assertEqual(dict(response.usage), {"input_tokens": 7, "output_tokens": 8})

    def test_plain_stdout_is_the_last_fallback(self) -> None:
        response = self.provider("plain_stdout").generate(self.request())
        self.assertEqual(response.text, "an old CLI's plain text answer")

    def test_an_empty_stdout_is_an_empty_response(self) -> None:
        # How a release before 1.1.1 ended when its answer went to a
        # transcript file instead of the pipe. Transcripts are not read.
        with self.assertRaises(ProviderError) as caught:
            self.provider("silent").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.EMPTY_RESPONSE)
        self.assertIn("1.1.1", caught.exception.detail or "")


# --- failures ----------------------------------------------------------------


class TestFailures(FakeAgyTestCase):
    def assert_public_message_is_clean(self, error: ProviderError) -> None:
        for leak in ("stderr", "agent.go", "authentication", "quota", "AGY_ERROR",
                     "settings.json"):
            self.assertNotIn(leak, error.message)

    def test_a_denied_answer_tool_is_a_configuration_error_naming_the_rule(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("denied").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.INVALID_REQUEST)
        self.assertIn(ALLOW_RULE, caught.exception.detail or "")
        self.assertIn("permissions.allow", caught.exception.detail or "")
        self.assert_public_message_is_clean(caught.exception)

    def test_not_signed_in_is_an_authentication_failure(self) -> None:
        for mode in ("auth", "auth_result"):
            with self.subTest(mode=mode):
                with self.assertRaises(ProviderError) as caught:
                    self.provider(mode).generate(self.request())
                self.assertIs(caught.exception.kind, ProviderErrorKind.AUTHENTICATION)
                self.assertNotIsInstance(caught.exception, ProviderNotConfigured)
                self.assert_public_message_is_clean(caught.exception)

    def test_a_quota_is_rate_limited(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("quota").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.RATE_LIMITED)
        self.assertIn("RESOURCE_EXHAUSTED", caught.exception.detail or "")
        self.assert_public_message_is_clean(caught.exception)

    def test_a_timeout_kills_the_child_and_what_it_started(self) -> None:
        provider = self.provider("hang", timeout=2.0)
        started = time.monotonic()
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.request())
        self.assertLess(time.monotonic() - started, 20)
        self.assertIs(caught.exception.kind, ProviderErrorKind.TIMEOUT)
        record = self.record()
        self.assert_gone(record["pid"])
        self.assert_gone(record["grandchild_pid"])

    def test_a_timeout_while_rate_limited_is_a_rate_limit(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("quota_then_hang", timeout=1.5).generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.RATE_LIMITED)
        self.assert_gone(self.record()["pid"])

    def test_an_unknown_flag_is_an_invalid_request(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("unknown_flag").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.INVALID_REQUEST)

    def test_a_crash_is_other_with_the_stderr_tail_in_the_detail_only(self) -> None:
        with self.assertRaises(ProviderError) as caught:
            self.provider("crash").generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.OTHER)
        self.assertIn("something internal went wrong", caught.exception.detail or "")
        self.assertIn("exit code 5", caught.exception.detail or "")
        self.assert_public_message_is_clean(caught.exception)

    def test_an_executable_that_vanished_is_not_configured(self) -> None:
        provider = self.provider("tool")
        self.executable.unlink()
        with self.assertRaises(ProviderNotConfigured):
            provider.generate(self.request())

    def test_a_shim_refuses_a_cmd_metacharacter(self) -> None:
        shim = self.bin / "agy.cmd"
        shim.write_text("@echo off\r\n", encoding="utf-8")
        config = AiConfig(provider=ANTIGRAVITY_PROVIDER_NAME, model="a&b")
        provider = AntigravityTextToCadModel(str(shim), config, root=self.root, environ={})
        with self.assertRaises(ProviderError) as caught:
            provider.generate(self.request())
        self.assertIs(caught.exception.kind, ProviderErrorKind.INVALID_REQUEST)
        self.assertFalse((self.root / "calls").exists())


# --- finding the CLI, configuration and dispatch ------------------------------


class TestSelection(FakeAgyTestCase):
    def test_a_missing_cli_is_not_configured(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"PATH": str(empty)}, clear=True):
            self.assertIsNone(locate_cli())
            with self.assertRaises(ProviderNotConfigured) as caught:
                AntigravityTextToCadModel.from_environment(
                    AiConfig(provider=ANTIGRAVITY_PROVIDER_NAME)
                )
            self.assertIs(caught.exception.kind, ProviderErrorKind.NOT_CONFIGURED)
            self.assertFalse(credential_available(provider=ANTIGRAVITY_PROVIDER_NAME))

    def test_the_cli_is_found_on_path_or_at_the_configured_path(self) -> None:
        on_path = {"PATH": str(self.bin)}
        self.assertEqual(
            Path(locate_cli(on_path) or "").resolve(), self.executable.resolve()
        )
        configured = {"PATH": "", ANTIGRAVITY_PATH_VARIABLE: str(self.executable)}
        self.assertEqual(
            Path(locate_cli(configured) or "").resolve(), self.executable.resolve()
        )
        self.assertTrue(
            credential_available(configured, provider=ANTIGRAVITY_PROVIDER_NAME)
        )

    def test_a_wrong_configured_path_never_falls_back_to_path(self) -> None:
        environment = {
            "PATH": str(self.bin),
            ANTIGRAVITY_PATH_VARIABLE: str(self.tmp / "nowhere" / "agy"),
        }
        self.assertIsNone(locate_cli(environment))
        self.assertFalse(
            credential_available(environment, provider=ANTIGRAVITY_PROVIDER_NAME)
        )

    def test_never_selected_automatically(self) -> None:
        self.assertNotIn(ANTIGRAVITY_PROVIDER_NAME, AUTOMATIC_PROVIDER_NAMES)
        self.assertIn(ANTIGRAVITY_PROVIDER_NAME, PROVIDER_NAMES)
        self.assertEqual(CLI_PATH_VARIABLES[ANTIGRAVITY_PROVIDER_NAME], "CAD_ANTIGRAVITY_PATH")
        # The CLI is installed and no credential is set: still not chosen.
        installed = {"PATH": str(self.bin)}
        self.assertEqual(resolve_provider(installed), "anthropic")
        self.assertEqual(
            resolve_provider({**installed, GEMINI_API_KEY_VARIABLE: "x"}), "gemini"
        )
        # An installed CLI is not a credential for the ordinary suite.
        self.assertFalse(credential_available(installed))
        # Only by name.
        named = {PROVIDER_VARIABLE: "antigravity"}
        self.assertEqual(resolve_provider(named), ANTIGRAVITY_PROVIDER_NAME)
        config = config_from_environment(named)
        self.assertEqual(config.provider, ANTIGRAVITY_PROVIDER_NAME)
        self.assertEqual(config.model, "gemini-3.8-flash")
        overridden = config_from_environment({**named, "CAD_AI_MODEL": "gemini-4-hydrogen"})
        self.assertEqual(overridden.model, "gemini-4-hydrogen")

    def test_the_factory_dispatches_to_the_cli_provider(self) -> None:
        config = AiConfig(provider=ANTIGRAVITY_PROVIDER_NAME)
        with mock.patch.dict(os.environ, {ANTIGRAVITY_PATH_VARIABLE: str(self.executable)}):
            model = model_from_environment(config)
        self.assertIsInstance(model, AntigravityTextToCadModel)
        self.assertEqual(model.name, "antigravity")
        self.assertEqual(Path(model.executable).resolve(), self.executable.resolve())
        with mock.patch.dict(
            os.environ, {ANTIGRAVITY_PATH_VARIABLE: str(self.tmp / "missing" / "agy")}
        ):
            with self.assertRaises(ProviderNotConfigured):
                model_from_environment(config)


# --- the MCP server's capture, on its own --------------------------------------


class TestMcpCapture(unittest.TestCase):
    TOOL = {
        "name": ANSWER_TOOL_NAME,
        "description": ANSWER_TOOL_DESCRIPTION,
        "inputSchema": {"type": "object"},
    }

    def run_server(self, directory: Path, messages: List[Dict[str, Any]],
                   extra_args: List[str]) -> subprocess.CompletedProcess:
        tool_file = directory / "tool.json"
        tool_file.write_text(json.dumps(self.TOOL), encoding="utf-8")
        payload = b"".join(json.dumps(m).encode("utf-8") + b"\n" for m in messages)
        return subprocess.run(
            [sys.executable, "-I", str(AI_SOURCE / "claude_code_mcp.py"), str(tool_file)]
            + extra_args,
            input=payload,
            capture_output=True,
            timeout=60,
        )

    @staticmethod
    def call(message_id: int, name: str, arguments: Any) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": message_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}

    def test_the_first_answer_call_is_captured_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            capture = directory / "answer.json"
            arguments = {"x": 1, "text": "line one\nline two ·"}
            completed = self.run_server(
                directory,
                [
                    self.call(1, "Bash", {"no": 1}),  # an unknown tool: not captured
                    self.call(2, ANSWER_TOOL_NAME, arguments),
                    self.call(3, ANSWER_TOOL_NAME, {"second": True}),
                ],
                [claude_code_mcp.CAPTURE_OPTION, str(capture)],
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(capture.read_text(encoding="utf-8")), arguments)
            # No temporary file is left beside it.
            self.assertEqual(sorted(p.name for p in directory.iterdir()),
                             ["answer.json", "tool.json"])
        replies = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32602)
        self.assertEqual(replies[1]["result"]["content"][0]["text"], "Answer received.")
        self.assertEqual(replies[2]["result"]["content"][0]["text"], "Answer received.")

    def test_without_the_option_nothing_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            completed = self.run_server(
                directory, [self.call(1, ANSWER_TOOL_NAME, {"x": 1})], []
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual([p.name for p in directory.iterdir()], ["tool.json"])

    def test_an_unwritable_capture_is_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            capture = directory / "missing-folder" / "answer.json"
            completed = self.run_server(
                directory,
                [self.call(1, ANSWER_TOOL_NAME, {"x": 1})],
                [claude_code_mcp.CAPTURE_OPTION, str(capture)],
            )
        self.assertEqual(completed.returncode, 0)
        reply = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(reply["result"]["content"][0]["text"], "Answer received.")
        self.assertIn(b"cannot write the capture", completed.stderr)

    def test_the_in_process_serve_and_the_argument_forms(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            capture = Path(raw) / "a.json"
            stdin = io.BytesIO(
                json.dumps(self.call(1, ANSWER_TOOL_NAME, [1, 2])).encode("utf-8") + b"\n"
            )
            stdout = io.BytesIO()
            self.assertEqual(
                claude_code_mcp.serve(stdin, stdout, self.TOOL, str(capture)), 0
            )
            self.assertEqual(json.loads(capture.read_text(encoding="utf-8")), [1, 2])
            tool_file = Path(raw) / "tool.json"
            tool_file.write_text(json.dumps(self.TOOL), encoding="utf-8")
            with mock.patch.object(sys, "stderr", new=io.StringIO()):
                for bad in (
                    [str(tool_file), claude_code_mcp.CAPTURE_OPTION],
                    [str(tool_file), "--other", str(capture)],
                    [str(tool_file), claude_code_mcp.CAPTURE_OPTION, ""],
                    [str(tool_file), "x", "y", "z"],
                ):
                    self.assertEqual(claude_code_mcp.main(bad), 2, bad)


# --- what the module imports ---------------------------------------------------


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
    def test_the_provider_is_standard_library_only_and_uses_no_shell(self) -> None:
        path = AI_SOURCE / "antigravity_provider.py"
        names = _imports(path)
        for name in names:
            root = name.split(".")[0]
            self.assertTrue(
                root == "cad_ai" or root in sys.stdlib_module_names or root == "__future__",
                name,
            )
        for name in names:
            self.assertNotIn(name, ("cad_ai.anthropic_provider", "cad_ai.gemini_provider",
                                    "cad_ai.claude_code_provider"))
        tree = ast.parse(path.read_text(encoding="utf-8"))
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

    def test_the_mcp_server_stays_standard_library_only(self) -> None:
        names = _imports(AI_SOURCE / "claude_code_mcp.py")
        self.assertTrue(set(names) <= {"__future__", "json", "os", "sys", "typing"}, names)


if __name__ == "__main__":
    unittest.main()
