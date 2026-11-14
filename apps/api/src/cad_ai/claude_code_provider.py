"""The Claude Code provider: the owner's Claude subscription, through the CLI.

The third implementation of :class:`~cad_ai.provider.TextToCadModel`. Instead
of calling the Messages API with a key, it drives the official ``claude``
command-line program (Claude Code), already installed and logged in on the
machine, so a request is billed to that person's own subscription. Selected
only by ``CAD_AI_PROVIDER=claude_code`` -- never automatically.

What one :meth:`ClaudeCodeTextToCadModel.generate` does, and the whole of it:

1. start **one** ``claude -p`` process, in an empty dedicated working folder
   (not the repository, so no ``CLAUDE.md`` is found), with
   ``--input-format stream-json --output-format stream-json --verbose``;
2. hand it the system prompt in a **file** named by the prompt's hash
   (``--system-prompt-file``), so the prompt never appears on a command line
   or in a process listing;
3. give Claude Code **no tools of its own** (``--tools ""``: no shell, no
   files, no web), load none of the user's settings, hooks, plugins, slash
   commands or other MCP servers, and register exactly one stdio MCP server --
   :mod:`cad_ai.claude_code_mcp` -- whose one tool,
   :data:`~cad_ai.prompt.ANSWER_TOOL_NAME`, is the only thing it may call;
4. write the user's text to stdin as one stream-json user message;
5. read stream-json events from stdout until the model calls that tool, and
   take the tool's **input** as the answer -- the same object the API
   provider takes from its own ``submit_answer`` call. A run that ends with no
   such call falls back to the final result text, which the generation layer
   then judges like any other text (no extra leniency here);
6. end the process and everything it started.

What it deliberately does not do: read, print or pass a credential (the
subscription login is the CLI's own business, and the API-key variables are
*removed* from the child's environment unless the owner opts in), execute
anything the model returns, retry, or interpret the answer. The MCP server
does not validate the answer either: the application's validator does.

Each call starts a fresh process (a few seconds of start-up). A long-lived
process, or a spare started ahead of the next call, would hide that cost; it
is a possible later optimisation, deliberately not built here.

Terms: this is for a person running the application **for themselves** on
their own subscription through the official CLI. It is not a way to offer
subscription logins to other people.

Windows matters here. The executable may be ``claude.exe`` or the npm shim
``claude.cmd``; it is started from its resolved path without a shell, in a
new process group and without a console window, and a stop kills the whole
tree (``taskkill /T /F``). Every pipe is UTF-8 bytes, never the locale
codepage.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Mapping, Optional, Sequence, Tuple

from cad_ai.anthropic_provider import USAGE_FIELDS, schema_for_api
from cad_ai.config import (
    API_KEY_VARIABLE,
    CLAUDE_CODE_PATH_VARIABLE,
    CLAUDE_CODE_PROVIDER_NAME,
    CLAUDE_CODE_USE_API_KEY_VARIABLE,
    FALLBACK_API_KEY_VARIABLE,
    GEMINI_API_KEY_VARIABLE,
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

#: The executable searched for on ``PATH`` when ``CAD_CLAUDE_CODE_PATH`` is
#: unset. ``shutil.which`` applies ``PATHEXT`` on Windows, so this finds
#: ``claude.exe`` or ``claude.cmd`` there.
CLI_NAME = "claude"

#: The MCP server's name in the CLI's configuration. Claude Code exposes its
#: tool to the model as ``mcp__<server>__<tool>``.
MCP_SERVER_NAME = "cad"

#: The answer tool as the CLI names it, and the only tool allowed.
ANSWER_TOOL = f"mcp__{MCP_SERVER_NAME}__{ANSWER_TOOL_NAME}"

#: A permission mode that never prompts: what is not pre-approved by
#: ``--allowedTools`` is denied. (Nothing else exists to approve anyway.)
PERMISSION_MODE = "dontAsk"

#: One model turn. The answer is the first turn's tool call; a second turn
#: would only be the model remarking on the tool's reply, at the cost of
#: re-reading the whole answer as input.
MAX_TURNS = 1

#: Variables the child never needs and never gets: another provider's
#: credential, and the markers by which a Claude Code process recognises
#: that it was started from inside another one (or from an IDE).
ALWAYS_REMOVED_VARIABLES: Tuple[str, ...] = (
    GEMINI_API_KEY_VARIABLE,
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SSE_PORT",
)

#: Every variable whose name starts with one of these is dropped as well:
#: a host Claude Code session (a cloud container, an IDE, a parent CLI) puts
#: its session ids, ingress/messaging tokens and token-file paths there, and
#: a child that inherited them could act under *that session's* login rather
#: than the CLI's own. Measured in a cloud session: ~60 such variables,
#: including CLAUDE_SESSION_INGRESS_TOKEN_FILE and CLAUDE_CODE_MESSAGING_TOKEN.
#: A normally logged-in CLI needs none of them -- its login lives in its own
#: config folder.
SESSION_VARIABLE_PREFIXES: Tuple[str, ...] = ("CLAUDE_",)

#: Kept despite the prefix: where the user keeps the CLI's own configuration
#: (and therefore its login).
KEPT_SESSION_VARIABLES: Tuple[str, ...] = ("CLAUDE_CONFIG_DIR",)

#: API credentials that would make the CLI bill a key instead of the
#: subscription login. Removed unless ``CAD_CLAUDE_CODE_USE_API_KEY=1``.
API_KEY_CHILD_VARIABLES: Tuple[str, ...] = (
    API_KEY_VARIABLE,
    FALLBACK_API_KEY_VARIABLE,
    "ANTHROPIC_AUTH_TOKEN",
)

#: Values of ``CAD_CLAUDE_CODE_USE_API_KEY`` that mean "yes".
TRUE_VALUES = ("1", "true", "yes", "on")

#: Set in the child: no claude.ai connector servers (``--strict-mcp-config``
#: already ignores every other MCP configuration; this is belt and braces).
CHILD_SETTINGS: Mapping[str, str] = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}

#: The child variable that bounds a response's length.
MAX_OUTPUT_TOKENS_VARIABLE = "CLAUDE_CODE_MAX_OUTPUT_TOKENS"

#: Characters of the child's stderr (and of any non-JSON stdout) kept for a
#: failure's ``detail``. Never part of a public message.
DETAIL_TAIL_CHARS = 4000

#: Characters a Windows ``.cmd``/``.bat`` shim would let ``cmd.exe``
#: interpret in an argument. Every argument this module builds is a flag, a
#: model name or a path it created, so one of these means something is
#: wrong; it is refused rather than handed to a shell parser.
_CMD_METACHARACTERS = frozenset('"&|<>^%!\r\n')

#: The neutral kind for each value of the CLI's ``assistant.error`` field
#: (the stream-json schema of the installed CLI). ``max_output_tokens`` is
#: absent on purpose: a cut-off answer is an unusable answer, which the
#: generation layer re-asks, not an outage.
_ASSISTANT_ERROR_KINDS: Mapping[str, ProviderErrorKind] = {
    "authentication_failed": ProviderErrorKind.AUTHENTICATION,
    "oauth_org_not_allowed": ProviderErrorKind.AUTHENTICATION,
    "account_on_hold": ProviderErrorKind.AUTHENTICATION,
    "verification_required": ProviderErrorKind.AUTHENTICATION,
    "billing_error": ProviderErrorKind.AUTHENTICATION,
    "cloud_credential_error": ProviderErrorKind.AUTHENTICATION,
    "rate_limit": ProviderErrorKind.RATE_LIMITED,
    "overloaded": ProviderErrorKind.CAPACITY,
    "server_error": ProviderErrorKind.CAPACITY,
    "invalid_request": ProviderErrorKind.INVALID_REQUEST,
    "model_not_found": ProviderErrorKind.INVALID_REQUEST,
    "unknown": ProviderErrorKind.OTHER,
}

#: Last-resort classification of a failure's text (stderr, or an error
#: result's message), in order. Words the CLI itself prints, e.g. "Not logged
#: in · Please run /login" or a usage-limit notice.
_TEXT_KINDS: Tuple[Tuple["re.Pattern[str]", ProviderErrorKind], ...] = (
    (
        re.compile(
            r"not logged in|/login|invalid api key|authenticat|unauthori[sz]ed"
            r"|\b401\b|\b403\b|oauth token|token (?:has )?expired|credit balance",
            re.IGNORECASE,
        ),
        ProviderErrorKind.AUTHENTICATION,
    ),
    (
        re.compile(
            r"usage limit|rate.?limit|hit your limit|limit reached"
            r"|too many requests|\b429\b",
            re.IGNORECASE,
        ),
        ProviderErrorKind.RATE_LIMITED,
    ),
    (
        re.compile(r"overloaded|\b529\b|\b503\b", re.IGNORECASE),
        ProviderErrorKind.CAPACITY,
    ),
    (
        re.compile(
            r"model.{0,40}not (?:found|available)|invalid model|not_found_error"
            r"|unknown option|\b404\b",
            re.IGNORECASE,
        ),
        ProviderErrorKind.INVALID_REQUEST,
    ),
)

_PUBLIC_UNAVAILABLE = "the interpretation service is unavailable"
_PUBLIC_NOT_AVAILABLE = "the interpretation service is not available"


def locate_cli(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The ``claude`` executable to run, or ``None``. Runs nothing.

    ``CAD_CLAUDE_CODE_PATH`` wins when set: a file path is used as it is, a
    bare name is looked up on ``PATH``. A configured value that names nothing
    is *not found* -- it never falls back to a different ``claude`` on
    ``PATH``, because a caller who named one executable and silently got
    another could not know which one ran.
    """
    source = os.environ if environ is None else environ
    search_path = source.get("PATH")
    configured = source.get(CLAUDE_CODE_PATH_VARIABLE, "").strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return os.path.abspath(configured)
        if os.path.dirname(configured):
            return None
        return shutil.which(configured, path=search_path)
    # Not configured: look where the owner's own shell would -- PATH, then
    # (Windows) the saved user/machine Path, then the installer's folders.
    from cad_ai.cli_discovery import CLAUDE_WELL_KNOWN_DIRS, find_program  # noqa: PLC0415

    return find_program(CLI_NAME, environ=source, well_known_dirs=CLAUDE_WELL_KNOWN_DIRS)


def child_environment(
    environ: Mapping[str, str], *, max_output_tokens: int
) -> Dict[str, str]:
    """The environment the ``claude`` child gets.

    Everything inherited (``PATH``, ``HOME``, ``USERPROFILE``, ``APPDATA``,
    ``LOCALAPPDATA``, ``SystemRoot``, ``TEMP``, proxy settings ... -- the CLI
    needs them to find its login and reach the service), minus
    :data:`ALWAYS_REMOVED_VARIABLES`, and minus
    :data:`API_KEY_CHILD_VARIABLES` unless ``CAD_CLAUDE_CODE_USE_API_KEY`` is
    on. Variables are dropped by **name**; no value is read or compared.
    """
    opt_in = (
        environ.get(CLAUDE_CODE_USE_API_KEY_VARIABLE, "").strip().lower()
        in TRUE_VALUES
    )
    removed = set(ALWAYS_REMOVED_VARIABLES)
    if not opt_in:
        removed.update(API_KEY_CHILD_VARIABLES)
    removed_upper = {name.upper() for name in removed}
    kept_upper = {name.upper() for name in KEPT_SESSION_VARIABLES}

    def dropped(name: str) -> bool:
        upper = name.upper()  # Windows environment names are case-insensitive
        if upper in removed_upper:
            return True
        return upper not in kept_upper and upper.startswith(SESSION_VARIABLE_PREFIXES)

    child = {name: value for name, value in environ.items() if not dropped(name)}
    child.update(CHILD_SETTINGS)
    child[MAX_OUTPUT_TOKENS_VARIABLE] = str(int(max_output_tokens))
    return child


def command_line(
    executable: str,
    *,
    model: str,
    prompt_file: str,
    mcp_config_file: str,
    effort: Optional[str] = None,
) -> List[str]:
    """The exact argv. Carries no prompt, no user text and no credential.

    Every flag was checked against ``claude --help`` (2.1.x) or, for the two
    the help hides (``--system-prompt-file``, ``--max-turns``), against the
    CLI's own option table.
    """
    argv = [
        executable,
        "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",  # the CLI requires it for stream-json output with -p
        "--model", model,
        "--system-prompt-file", prompt_file,
        "--tools", "",  # none of Claude Code's own tools
        "--strict-mcp-config",
        "--mcp-config", mcp_config_file,
        "--allowedTools", ANSWER_TOOL,
        "--permission-mode", PERMISSION_MODE,
        "--setting-sources", "",  # no user, project or local settings
        "--disable-slash-commands",
        "--no-session-persistence",
        "--max-turns", str(MAX_TURNS),
    ]
    if effort is not None:
        argv += ["--effort", effort]
    return argv


def user_message(text: str) -> bytes:
    """One stream-json user message, as the line written to stdin."""
    message = {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "parent_tool_use_id": None,
        "session_id": "default",
    }
    return _json_line(message)


def _json_line(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"


def default_root() -> Path:
    """Where the empty working folder, prompt files and call files live."""
    return Path(tempfile.gettempdir()) / "cad-ai-claude-code"


class ClaudeCodeTextToCadModel:
    """A :class:`~cad_ai.provider.TextToCadModel` over the ``claude`` CLI."""

    name = CLAUDE_CODE_PROVIDER_NAME

    #: After the answer arrives, how long to wait for the CLI's ``result``
    #: event (which carries the token counts) before ending the process.
    result_grace_seconds: float = 5.0

    #: How long ending the process may take before giving up on it.
    exit_wait_seconds: float = 5.0

    def __init__(
        self,
        executable: str,
        config: AiConfig,
        *,
        root: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._executable = executable
        self._config = config
        self._root = Path(root) if root is not None else default_root()
        # A snapshot is not taken: the environment is read at each call, so
        # an opt-in or a PATH change applies to the next one.
        self._environ = environ

    @classmethod
    def from_environment(
        cls, config: Optional[AiConfig] = None
    ) -> "ClaudeCodeTextToCadModel":
        """Build a provider from the environment.

        Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the
        ``claude`` executable cannot be found. Nothing is run: whether the CLI
        is logged in is learned at the first call, and reported as an
        authentication failure then.
        """
        settings = config if config is not None else config_from_environment()
        executable = locate_cli()
        if executable is None:
            raise ProviderNotConfigured(
                _PUBLIC_NOT_AVAILABLE,
                detail=(
                    f"the {CLI_NAME!r} executable was not found on PATH, in the saved user PATH, in its usual install folders or at "
                    f"{CLAUDE_CODE_PATH_VARIABLE}"
                ),
            )
        return cls(executable, settings)

    @property
    def config(self) -> AiConfig:
        return self._config

    @property
    def executable(self) -> str:
        return self._executable

    # -- files ------------------------------------------------------------

    @property
    def working_directory(self) -> Path:
        """The empty folder the CLI runs in. Nothing is ever put there."""
        return self._root / "cwd"

    def prompt_file(self, system: str) -> Path:
        """The system prompt in a file named by its SHA-256, written once."""
        data = system.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        directory = self._root / "prompts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"system-{digest}.txt"
        if path.is_file() and path.stat().st_size == len(data):
            return path
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.replace(temporary, path)
        except OSError:
            # Another call may have the file open (Windows refuses to replace
            # it then); it holds the same bytes, so it is fine as it is.
            if os.path.exists(temporary):
                os.unlink(temporary)
            if not path.is_file():
                raise
        return path

    def _write_call_files(self, directory: Path, request: ModelRequest) -> Path:
        """The tool definition and the MCP configuration for one call."""
        schema = (
            schema_for_api(dict(request.output_schema))
            if request.output_schema is not None
            else {"type": "object"}
        )
        tool_file = directory / "tool.json"
        tool_file.write_text(
            json.dumps(
                {
                    "name": ANSWER_TOOL_NAME,
                    "description": ANSWER_TOOL_DESCRIPTION,
                    "inputSchema": schema,
                }
            ),
            encoding="utf-8",
        )
        server = Path(__file__).with_name("claude_code_mcp.py")
        mcp_file = directory / "mcp.json"
        mcp_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        MCP_SERVER_NAME: {
                            "type": "stdio",
                            "command": sys.executable,
                            # -I: isolated mode -- no PYTHONPATH, no user site,
                            # no script directory on sys.path. The server is
                            # standard-library only and needs none of them.
                            "args": ["-I", str(server), str(tool_file)],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return mcp_file

    # -- the call ---------------------------------------------------------

    def generate(self, request: ModelRequest) -> ModelResponse:
        """One ``claude -p`` process. No tools of its own, no retry."""
        if not os.path.isfile(self._executable):
            raise ProviderNotConfigured(
                _PUBLIC_NOT_AVAILABLE,
                detail=f"the {CLI_NAME!r} executable is no longer at its path",
            )
        self.working_directory.mkdir(parents=True, exist_ok=True)
        prompt_path = self.prompt_file(request.system)
        calls = self._root / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        call_directory = Path(tempfile.mkdtemp(prefix="call-", dir=calls))
        try:
            mcp_file = self._write_call_files(call_directory, request)
            argv = command_line(
                self._executable,
                model=self._config.model,
                prompt_file=str(prompt_path),
                mcp_config_file=str(mcp_file),
                effort=self._config.effort,
            )
            _refuse_shell_metacharacters(argv)
            environ = os.environ if self._environ is None else self._environ
            env = child_environment(
                environ, max_output_tokens=request.max_output_tokens
            )
            return self._run(argv, env, request)
        finally:
            shutil.rmtree(call_directory, ignore_errors=True)

    def _run(
        self, argv: List[str], env: Dict[str, str], request: ModelRequest
    ) -> ModelResponse:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.working_directory),
                env=env,
                **_spawn_options(),
            )
        except FileNotFoundError as exc:
            raise ProviderNotConfigured(
                _PUBLIC_NOT_AVAILABLE,
                detail=f"the {CLI_NAME!r} executable could not be started: {exc}",
            ) from exc
        except OSError as exc:
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=f"the {CLI_NAME!r} executable could not be started: "
                f"{type(exc).__name__}: {exc}",
                kind=ProviderErrorKind.OTHER,
            ) from exc
        session = _Session(process)
        try:
            return self._converse(session, request)
        finally:
            session.end(self.exit_wait_seconds)

    def _converse(self, session: "_Session", request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        deadline = started + float(self._config.timeout_seconds)
        state = _StreamState(default_model=self._config.model)
        session.send(user_message(request.user_text))
        grace_until: Optional[float] = None
        while True:
            now = time.monotonic()
            limit = deadline if grace_until is None else min(deadline, grace_until)
            if now >= limit:
                if state.answer is None:
                    session.kill()
                    raise self._timeout(state, session)
                break  # the answer is in; the result event never came
            try:
                kind, item = session.events.get(timeout=limit - now)
            except queue.Empty:
                continue
            if kind == "eof":
                break
            if kind == "noise":
                session.note(item)
                continue
            state.observe(item)
            if item.get("type") == "control_request":
                session.send(_control_response(item))
                continue
            if state.answer is not None and grace_until is None:
                # The answer is complete. Closing stdin tells the CLI there
                # is no further turn; the result event, if it comes soon,
                # carries the token counts.
                session.close_stdin()
                grace_until = time.monotonic() + self.result_grace_seconds
            if state.result is not None:
                session.close_stdin()
                break
        returncode = session.wait_briefly()
        return self._outcome(state, session, returncode)

    # -- reading the outcome ---------------------------------------------

    def _outcome(
        self, state: "_StreamState", session: "_Session", returncode: Optional[int]
    ) -> ModelResponse:
        if state.answer is not None:
            # Re-serialised as it came, never edited -- like the API provider.
            return self._response(json.dumps(state.answer), state, "tool_use")
        if state.error is not None and state.error in _ASSISTANT_ERROR_KINDS:
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=self._detail(f"assistant error {state.error!r}", state, session),
                kind=_ASSISTANT_ERROR_KINDS[state.error],
            )
        result = state.result
        if result is not None and result.get("is_error") and state.error != "max_output_tokens":
            text = str(result.get("result") or "")
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=self._detail(
                    f"error result {result.get('subtype')!r}", state, session
                ),
                kind=_classify_text(text + "\n" + session.tail())
                or ProviderErrorKind.OTHER,
            )
        fallback = state.fallback_text()
        if fallback.strip():
            stop = None
            if result is not None:
                stop = _optional_str(result.get("stop_reason")) or _optional_str(
                    result.get("subtype")
                )
            return self._response(fallback, state, stop)
        if returncode not in (0, None):
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=self._detail(f"exit code {returncode}", state, session),
                kind=_classify_text(session.tail()) or ProviderErrorKind.OTHER,
            )
        raise ProviderError(
            "the interpretation service returned no answer",
            detail=self._detail(f"no answer; exit code {returncode}", state, session),
            kind=ProviderErrorKind.EMPTY_RESPONSE,
        )

    def _timeout(self, state: "_StreamState", session: "_Session") -> ProviderError:
        # The CLI retries rate limits and overloads itself, reporting each
        # retry as an event; a call that ran out of time while doing that was
        # limited, not slow.
        kind = ProviderErrorKind.TIMEOUT
        if state.retry_error is not None:
            kind = _ASSISTANT_ERROR_KINDS.get(state.retry_error, kind)
            if kind not in (ProviderErrorKind.RATE_LIMITED, ProviderErrorKind.CAPACITY):
                kind = ProviderErrorKind.TIMEOUT
        return ProviderError(
            _PUBLIC_UNAVAILABLE,
            detail=self._detail(
                f"no answer within {self._config.timeout_seconds:g} s", state, session
            ),
            kind=kind,
        )

    def _detail(self, what: str, state: "_StreamState", session: "_Session") -> str:
        parts = [f"claude CLI: {what}"]
        if state.retry_error is not None:
            parts.append(f"last api_retry error {state.retry_error!r}")
        if state.result is not None and state.result.get("is_error"):
            parts.append(f"result: {str(state.result.get('result') or '')[:500]!r}")
        tail = session.tail()
        if tail:
            parts.append(f"stderr: {tail}")
        return "; ".join(parts)

    def _response(
        self, text: str, state: "_StreamState", stop_reason: Optional[str]
    ) -> ModelResponse:
        return ModelResponse(
            text=text,
            provider=self.name,
            model=state.model,
            # The tool's schema is advisory to the model, not a decoding
            # constraint -- the same as the API provider's tool mode.
            structured_output=False,
            stop_reason=stop_reason,
            usage=state.usage(),
        )


class _StreamState:
    """What the stream-json events have said so far."""

    def __init__(self, *, default_model: str) -> None:
        self.model = default_model
        self.answer: Optional[Any] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.retry_error: Optional[str] = None
        self._texts: List[str] = []
        self._message_usage: Dict[str, Any] = {}

    def observe(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "system":
            if event.get("subtype") == "init" and isinstance(event.get("model"), str):
                self.model = event["model"]
            elif event.get("subtype") == "api_retry" and isinstance(event.get("error"), str):
                self.retry_error = event["error"]
        elif kind == "assistant":
            self._assistant(event)
        elif kind == "result":
            self.result = event

    def _assistant(self, event: Dict[str, Any]) -> None:
        if isinstance(event.get("error"), str):
            self.error = event["error"]
        message = event.get("message")
        if not isinstance(message, dict):
            return
        model = message.get("model")
        if isinstance(model, str) and model and not model.startswith("<"):
            # "<synthetic>" marks a message the CLI wrote itself (an error
            # notice), not one a model answered.
            self.model = model
        if isinstance(message.get("usage"), dict):
            self._message_usage = message["usage"]
        if event.get("parent_tool_use_id"):
            return  # a subagent's message; none exist here, but never the answer
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                if self.answer is None and block.get("name") == ANSWER_TOOL:
                    self.answer = block.get("input")
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                self._texts.append(block["text"])

    def fallback_text(self) -> str:
        """The final result text, else the assistant's text blocks."""
        if self.result is not None and isinstance(self.result.get("result"), str):
            return self.result["result"]
        return "".join(self._texts)

    def usage(self) -> Dict[str, int]:
        source = self._message_usage
        if self.result is not None and isinstance(self.result.get("usage"), dict):
            source = self.result["usage"]
        counts: Dict[str, int] = {}
        for name in USAGE_FIELDS:
            value = source.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                counts[name] = value
        return counts


class _Session:
    """One child process: its pipes, its reader threads, and its end."""

    def __init__(self, process: "subprocess.Popen[bytes]") -> None:
        self.process = process
        self.events: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._stderr: Deque[str] = deque()
        self._stderr_size = 0
        self._noise: Deque[str] = deque(maxlen=20)
        self._lock = threading.Lock()
        self._stdin_open = True
        self._ended = False
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read_stdout(self) -> None:
        stream = self.process.stdout
        try:
            for raw in iter(stream.readline, b""):  # type: ignore[union-attr]
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    self.events.put(("noise", line))
                    continue
                if isinstance(event, dict):
                    self.events.put(("event", event))
                else:
                    self.events.put(("noise", line))
        except (OSError, ValueError):
            pass
        finally:
            self.events.put(("eof", None))

    def _read_stderr(self) -> None:
        stream = self.process.stderr
        try:
            for raw in iter(lambda: stream.read1(4096), b""):  # type: ignore[union-attr]
                text = raw.decode("utf-8", errors="replace")
                with self._lock:
                    self._stderr.append(text)
                    self._stderr_size += len(text)
                    while self._stderr_size > DETAIL_TAIL_CHARS and len(self._stderr) > 1:
                        self._stderr_size -= len(self._stderr.popleft())
        except (OSError, ValueError):
            pass

    def note(self, line: str) -> None:
        self._noise.append(line[:500])

    def tail(self) -> str:
        with self._lock:
            text = "".join(self._stderr)
        noise = " | ".join(self._noise)
        if noise:
            text = f"{text} [stdout: {noise}]" if text else f"[stdout: {noise}]"
        return text.strip()[-DETAIL_TAIL_CHARS:]

    def send(self, data: bytes) -> None:
        if not self._stdin_open:
            return
        try:
            self.process.stdin.write(data)  # type: ignore[union-attr]
            self.process.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError, ValueError):
            # The process is gone; its exit is read from stdout's end.
            pass

    def close_stdin(self) -> None:
        if not self._stdin_open:
            return
        self._stdin_open = False
        try:
            self.process.stdin.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass

    def wait_briefly(self, seconds: float = 0.5) -> Optional[int]:
        """The exit code if the child ends soon; its stderr is then complete."""
        try:
            code = self.process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return self.process.poll()
        # stdout's end can be read before the last of stderr: let the stderr
        # reader finish, so a failure's detail carries what the CLI said.
        self._readers[1].join(timeout=1.0)
        return code

    def kill(self) -> None:
        _kill_tree(self.process)

    def end(self, wait_seconds: float) -> None:
        """Kill the tree, reap the child, close every pipe, join readers."""
        if self._ended:
            return
        self._ended = True
        self.close_stdin()
        _kill_tree(self.process)
        try:
            self.process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            pass
        # A reader sees end-of-file once the tree is gone. Its stream is
        # closed only after it has finished: closing a buffered stream that
        # another thread is still reading blocks on the stream's own lock.
        streams = (self.process.stdout, self.process.stderr)
        for reader, stream in zip(self._readers, streams):
            reader.join(timeout=wait_seconds)
            if stream is not None and not reader.is_alive():
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass


def _spawn_options() -> Dict[str, Any]:
    """A child that can be killed as a tree, and shows no window."""
    if os.name == "nt":
        return {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        }
    # POSIX: its own session, so its process-group id is its pid and the
    # MCP server it starts can be killed with it.
    return {"start_new_session": True}


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """End the child and everything it started. Never raises."""
    if os.name == "nt":
        if process.poll() is None:
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(system_root, "System32", "taskkill.exe")
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        return
    try:
        # Swept even after the leader exited: whatever it left in its group
        # (the MCP server) goes too.
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _control_response(event: Mapping[str, Any]) -> bytes:
    """The reply to a control request from the CLI.

    None is expected: the permission mode never prompts. Should a permission
    question arrive anyway, only the answer tool is allowed; anything else is
    refused, and so is every other kind of request.
    """
    request_id = event.get("request_id")
    request = event.get("request") if isinstance(event.get("request"), dict) else {}
    if request.get("subtype") == "can_use_tool":
        if request.get("tool_name") == ANSWER_TOOL:
            decision: Dict[str, Any] = {
                "behavior": "allow",
                "updatedInput": request.get("input") or {},
            }
        else:
            decision = {"behavior": "deny", "message": "only the answer tool is available"}
        return _json_line(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": decision},
            }
        )
    return _json_line(
        {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": request_id,
                "error": "not supported by this host",
            },
        }
    )


def _classify_text(text: str) -> Optional[ProviderErrorKind]:
    for pattern, kind in _TEXT_KINDS:
        if pattern.search(text):
            return kind
    return None


def _refuse_shell_metacharacters(argv: Sequence[str]) -> None:
    """Refuse an argument ``cmd.exe`` would interpret, for a batch shim.

    Only relevant when the executable is a ``.cmd``/``.bat`` file, which
    Windows runs through ``cmd.exe`` even without ``shell=True``.
    """
    if not argv[0].lower().endswith((".cmd", ".bat")):
        return
    for argument in argv[1:]:
        if any(character in _CMD_METACHARACTERS for character in argument):
            raise ProviderError(
                _PUBLIC_NOT_AVAILABLE,
                detail=(
                    "an argument for the claude shim contains a character "
                    "cmd.exe would interpret; use claude.exe, or a model name "
                    "and temporary folder without them"
                ),
                kind=ProviderErrorKind.INVALID_REQUEST,
            )


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = [
    "ALWAYS_REMOVED_VARIABLES",
    "KEPT_SESSION_VARIABLES",
    "SESSION_VARIABLE_PREFIXES",
    "ANSWER_TOOL",
    "API_KEY_CHILD_VARIABLES",
    "CLI_NAME",
    "MAX_TURNS",
    "MCP_SERVER_NAME",
    "PERMISSION_MODE",
    "ClaudeCodeTextToCadModel",
    "child_environment",
    "command_line",
    "default_root",
    "locate_cli",
    "user_message",
]
