"""The Antigravity provider: the owner's Google account, through the ``agy`` CLI.

The fourth implementation of :class:`~cad_ai.provider.TextToCadModel`, and
the Google counterpart of :mod:`cad_ai.claude_code_provider`. Instead of
calling the Gemini API with a key, it drives Google's official **Antigravity
CLI** (``agy``), already installed and signed in on the machine, so a request
runs on that person's own Google account (the free tier, or their plan).
Selected only by ``CAD_AI_PROVIDER=antigravity`` -- never automatically.

What one :meth:`AntigravityTextToCadModel.generate` does, and the whole of it:

1. make a fresh **per-call workspace** folder (never the repository) holding
   exactly one file, ``.agents/mcp_config.json`` -- the documented
   workspace-level MCP configuration -- which registers one stdio MCP server,
   :mod:`cad_ai.claude_code_mcp`, offering one tool,
   :data:`~cad_ai.prompt.ANSWER_TOOL_NAME`, and nothing else
   (``enabledTools``). The tool definition and the capture file live in a
   sibling folder *outside* the workspace;
2. start **one** ``agy`` process there in its documented streaming headless
   mode, ``--input-format stream-json --output-format stream-json``, with
   ``--model`` and ``--mode accept-edits`` (see :func:`command_line`);
3. write **one** stream-json user message to stdin -- the system prompt, a
   separator, then the user's text (:func:`compose_message`) -- and close
   stdin, which the CLI documents as "end the session after this turn".
   Nothing about the request is on the command line;
4. wait until the MCP server has **captured** the tool call's arguments to
   its capture file (``--capture``), then briefly for the CLI's ``result``
   record (token counts), then end the process tree. The captured object is
   the answer -- the same object the API provider takes from its own
   ``submit_answer`` call. A run that ends with no capture falls back to the
   result's ``response`` text, else any plain stdout text, which the
   generation layer then judges like any other text (no extra leniency here).

Why the answer is captured by the MCP server rather than read from stdout:
the CLI's stream-json records are not documented to carry a tool call's
arguments, and older releases wrote nothing to a piped stdout at all. The
server sees the call itself, so the capture does not depend on either.

**What the CLI cannot be told, so the provider cannot promise it** (all from
the official docs and Google's own issue tracker, never measured here):

* there is **no system-prompt option**. A workspace rules file (``AGENTS.md``
  / ``GEMINI.md``) is truncated far below the prompt's ~53k characters
  (12,000 characters, later 24,000 bytes, per file), silently, so the prompt
  travels in the user message instead, on stdin;
* there is **no switch that removes the CLI's own tools**. They are confined
  instead: the workspace is an empty throwaway folder, files outside it need
  approval, and a headless run cannot obtain approval -- anything needing it
  is *soft-denied*. ``--mode accept-edits`` keeps the CLI out of Auto mode
  (its default since 1.5.7), in which a reviewer model may let sandboxed
  shell commands run;
* there is **no per-run permission grant**. An MCP tool asks for approval by
  default, so headless it is denied unless the owner has once added the rule
  :data:`ALLOW_RULE` to ``permissions.allow`` in
  ``~/.gemini/antigravity-cli/settings.json``. This provider never writes
  that file (or any other user configuration); a run that was denied the
  tool is reported as a configuration error naming the rule;
* the user's **global** rules (``~/.gemini/GEMINI.md``) and global MCP
  servers still load: moving ``HOME`` would also lose the sign-in.

What it deliberately does not do: read, print or pass a credential (the
sign-in is the CLI's own business, and the Google API-key variables are
*removed* from the child's environment unless the owner opts in), execute
anything the model returns, retry, or interpret the answer.

Terms: this is for a person running the application **for themselves** on
their own Google account through the official CLI. It is not a way to offer
that account, or this application backed by it, to other people.

Windows matters here. The executable is normally ``agy.exe``; a ``.cmd``
shim is also handled. It is started from its resolved path without a shell,
in a new process group and without a console window, and a stop kills the
whole tree (``taskkill /T /F``). Every pipe is UTF-8 bytes, never the locale
codepage.
"""

from __future__ import annotations

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

from cad_ai.config import (
    ANTIGRAVITY_PATH_VARIABLE,
    ANTIGRAVITY_PROVIDER_NAME,
    ANTIGRAVITY_USE_API_KEY_VARIABLE,
    API_KEY_VARIABLE,
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

#: The executable searched for on ``PATH`` when ``CAD_ANTIGRAVITY_PATH`` is
#: unset. ``shutil.which`` applies ``PATHEXT`` on Windows, so this finds
#: ``agy.exe`` (or an ``agy.cmd`` shim) there.
CLI_NAME = "agy"

#: The MCP server's name in the workspace configuration. Deliberately
#: distinctive: when a global and a workspace server share a name, the CLI
#: keeps whichever is already running, and that must never be someone else's.
MCP_SERVER_NAME = "text_to_cad"

#: The one permission rule the owner adds, once, so a headless run may call
#: the answer tool. The documented form is ``mcp(<server>/<tool>)``; the
#: shorter ``mcp(<server>)`` is accepted by the CLI and silently never
#: matches (Google's issue #916).
ALLOW_RULE = f"mcp({MCP_SERVER_NAME}/{ANSWER_TOOL_NAME})"

#: Where that rule goes. Named for messages and docs; never written here.
SETTINGS_FILE_HINT = "~/.gemini/antigravity-cli/settings.json"

#: The workspace-level MCP configuration file, relative to the working folder.
WORKSPACE_MCP_CONFIG = Path(".agents") / "mcp_config.json"

#: ``--mode``: the documented mode that edits files without asking and asks
#: before everything else -- which, headless, means everything else is
#: soft-denied. Chosen to keep the CLI out of Auto mode, not for the edits.
AGENT_MODE = "accept-edits"

#: The streaming headless protocol: one JSON object per line each way.
STREAM_FORMAT = "stream-json"

#: Google credential variables. Removed from the child unless
#: ``CAD_ANTIGRAVITY_USE_API_KEY=1``, so the CLI uses its own sign-in rather
#: than a key (or Application Default Credentials) found in the environment.
GOOGLE_CREDENTIAL_VARIABLES: Tuple[str, ...] = (
    GEMINI_API_KEY_VARIABLE,
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "AGY_API_KEY",
    "AGY_ADC_AUTH",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
)

#: Variables the child never gets: another provider's credential, and the
#: host Claude Code session's markers and credentials.
ALWAYS_REMOVED_VARIABLES: Tuple[str, ...] = (
    API_KEY_VARIABLE,
    FALLBACK_API_KEY_VARIABLE,
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
)

#: Name patterns the child never gets, whatever follows the prefix: every
#: Claude Code / Claude session variable and any Claude token. Matched on
#: the upper-cased **name**; no value is read.
ALWAYS_REMOVED_PATTERNS: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"^CLAUDE_CODE_"),
    re.compile(r"^CLAUDE_SESSION_"),
    re.compile(r"^CLAUDE_.*TOKEN"),
)

#: Values of ``CAD_ANTIGRAVITY_USE_API_KEY`` that mean "yes".
TRUE_VALUES = ("1", "true", "yes", "on")

#: Characters of the child's stderr (and of any non-JSON stdout) kept for a
#: failure's ``detail``. Never part of a public message.
DETAIL_TAIL_CHARS = 4000

#: The line the system prompt and the request are separated by, in the one
#: user message. The CLI has no system-prompt option (see the module text).
INSTRUCTIONS_HEADER = "=== INSTRUCTIONS FOR THIS REQUEST ==="
REQUEST_HEADER = "=== THE REQUEST ==="
TOOL_NOTE = (
    f"Answer only by calling the `{ANSWER_TOOL_NAME}` tool once. Do not read, "
    "write or run anything else: no file, command or web tool is needed."
)

#: Characters a Windows ``.cmd``/``.bat`` shim would let ``cmd.exe``
#: interpret in an argument. Every argument this module builds is a flag or
#: a model name, so one of these means something is wrong.
_CMD_METACHARACTERS = frozenset('"&|<>^%!\r\n')

#: The CLI's structured ``AGY_ERROR: {...}`` stderr line (since 1.2.6).
_AGY_ERROR = re.compile(r"AGY_ERROR:?\s*(\{.*\})")

#: Canonical status names in ``AGY_ERROR`` (gRPC / Google API style).
_STATUS_KINDS: Mapping[str, ProviderErrorKind] = {
    "UNAUTHENTICATED": ProviderErrorKind.AUTHENTICATION,
    "PERMISSION_DENIED": ProviderErrorKind.AUTHENTICATION,
    "RESOURCE_EXHAUSTED": ProviderErrorKind.RATE_LIMITED,
    "UNAVAILABLE": ProviderErrorKind.CAPACITY,
    "INTERNAL": ProviderErrorKind.CAPACITY,
    "DEADLINE_EXCEEDED": ProviderErrorKind.TIMEOUT,
    "INVALID_ARGUMENT": ProviderErrorKind.INVALID_REQUEST,
    "NOT_FOUND": ProviderErrorKind.INVALID_REQUEST,
    "FAILED_PRECONDITION": ProviderErrorKind.INVALID_REQUEST,
}

#: Last-resort classification of a failure's text, in order.
_TEXT_KINDS: Tuple[Tuple["re.Pattern[str]", ProviderErrorKind], ...] = (
    (
        re.compile(
            r"authentication required|authentication failed|unauthenticated"
            r"|not (?:signed|logged) in|sign.?in (?:required|expired)|/login"
            r"|permission_denied|\b401\b|\b403\b|credential",
            re.IGNORECASE,
        ),
        ProviderErrorKind.AUTHENTICATION,
    ),
    (
        re.compile(
            r"resource.?exhausted|quota|rate.?limit|too many requests|\b429\b",
            re.IGNORECASE,
        ),
        ProviderErrorKind.RATE_LIMITED,
    ),
    (
        re.compile(r"overloaded|unavailable|\b503\b|\b529\b|capacity", re.IGNORECASE),
        ProviderErrorKind.CAPACITY,
    ),
    (
        re.compile(
            r"model.{0,40}not (?:found|available|supported)|unknown model"
            r"|flag provided but not defined|unknown (?:flag|option)"
            r"|invalid.?argument|\b400\b|\b404\b",
            re.IGNORECASE,
        ),
        ProviderErrorKind.INVALID_REQUEST,
    ),
)

#: A headless soft-denial notice (stderr): a tool needed an approval the run
#: could not ask for.
_SOFT_DENIAL = re.compile(r"auto-denied|soft-denied|cannot prompt", re.IGNORECASE)

_PUBLIC_UNAVAILABLE = "the interpretation service is unavailable"
_PUBLIC_NOT_AVAILABLE = "the interpretation service is not available"


def locate_cli(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The ``agy`` executable to run, or ``None``. Runs nothing.

    ``CAD_ANTIGRAVITY_PATH`` wins when set: a file path is used as it is, a
    bare name is looked up on ``PATH``. A configured value that names nothing
    is *not found* -- it never falls back to a different ``agy`` on ``PATH``,
    because a caller who named one executable and silently got another could
    not know which one ran.
    """
    source = os.environ if environ is None else environ
    search_path = source.get("PATH")
    configured = source.get(ANTIGRAVITY_PATH_VARIABLE, "").strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return os.path.abspath(configured)
        if os.path.dirname(configured):
            return None
        return shutil.which(configured, path=search_path)
    return shutil.which(CLI_NAME, path=search_path)


def child_environment(environ: Mapping[str, str]) -> Dict[str, str]:
    """The environment the ``agy`` child gets.

    Everything inherited (``PATH``, ``HOME``, ``USERPROFILE``, ``APPDATA``,
    ``LOCALAPPDATA``, ``SystemRoot``, ``TEMP``, proxy settings ... -- the CLI
    needs them to find its sign-in in the system keyring and reach the
    service), minus :data:`ALWAYS_REMOVED_VARIABLES` and anything matching
    :data:`ALWAYS_REMOVED_PATTERNS`, and minus
    :data:`GOOGLE_CREDENTIAL_VARIABLES` unless ``CAD_ANTIGRAVITY_USE_API_KEY``
    is on. Variables are dropped by **name**; no value is read or compared.
    """
    opt_in = (
        environ.get(ANTIGRAVITY_USE_API_KEY_VARIABLE, "").strip().lower()
        in TRUE_VALUES
    )
    removed = {name.upper() for name in ALWAYS_REMOVED_VARIABLES}
    if not opt_in:
        removed.update(name.upper() for name in GOOGLE_CREDENTIAL_VARIABLES)

    def kept(name: str) -> bool:
        # Compared case-insensitively: Windows environment names are.
        upper = name.upper()
        if upper in removed:
            return False
        return not any(pattern.search(upper) for pattern in ALWAYS_REMOVED_PATTERNS)

    return {name: value for name, value in environ.items() if kept(name)}


def command_line(
    executable: str, *, model: str, effort: Optional[str] = None
) -> List[str]:
    """The exact argv. Carries no prompt, no user text, no path, no credential.

    The flags are the ones the official headless documentation and the CLI
    reference name: ``--input-format stream-json`` (it requires
    ``--output-format stream-json``, and drops any ``-p`` prompt), ``--model``,
    ``--mode`` and ``--disable-slash-commands``. No ``-p``: in this mode the
    prompt is read from stdin. Never ``--dangerously-skip-permissions``.
    """
    argv = [
        executable,
        "--input-format", STREAM_FORMAT,
        "--output-format", STREAM_FORMAT,
        "--model", model,
        "--mode", AGENT_MODE,
        "--disable-slash-commands",
    ]
    if effort is not None:
        argv += ["--effort", effort]
    return argv


def compose_message(system: str, user_text: str) -> str:
    """The one user message: the instructions, a separator, the request.

    The CLI offers no system-prompt option and truncates a rules file long
    before the prompt's length, so the prompt rides in the message itself.
    """
    return (
        f"{INSTRUCTIONS_HEADER}\n\n{system}\n\n{TOOL_NOTE}\n\n"
        f"{REQUEST_HEADER}\n\n{user_text}"
    )


def user_message(text: str) -> bytes:
    """One stream-json user record, as the line written to stdin."""
    return _json_line({"event": "user", "message": {"content": text}})


def _json_line(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n"


def default_root() -> Path:
    """Where the per-call workspaces and files live."""
    return Path(tempfile.gettempdir()) / "cad-ai-antigravity"


class AntigravityTextToCadModel:
    """A :class:`~cad_ai.provider.TextToCadModel` over the ``agy`` CLI."""

    name = ANTIGRAVITY_PROVIDER_NAME

    #: After the answer is captured, how long to wait for the CLI's ``result``
    #: record (which carries the token counts) before ending the process.
    result_grace_seconds: float = 5.0

    #: How often the capture file is looked for while nothing else happens.
    poll_seconds: float = 0.1

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
        # Read at each call, so an opt-in or a PATH change applies to the next.
        self._environ = environ

    @classmethod
    def from_environment(
        cls, config: Optional[AiConfig] = None
    ) -> "AntigravityTextToCadModel":
        """Build a provider from the environment.

        Raises :class:`~cad_ai.provider.ProviderNotConfigured` when the
        ``agy`` executable cannot be found. Nothing is run: whether the CLI is
        signed in is learned at the first call, and reported then.
        """
        settings = config if config is not None else config_from_environment()
        executable = locate_cli()
        if executable is None:
            raise ProviderNotConfigured(
                _PUBLIC_NOT_AVAILABLE,
                detail=(
                    f"the {CLI_NAME!r} executable was not found on PATH or at "
                    f"{ANTIGRAVITY_PATH_VARIABLE}"
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

    def _write_call_files(
        self, call_directory: Path, request: ModelRequest
    ) -> Tuple[Path, Path]:
        """The workspace (with its MCP config) and the capture path."""
        private = call_directory / "private"
        private.mkdir()
        workspace = call_directory / "workspace"
        (workspace / WORKSPACE_MCP_CONFIG).parent.mkdir(parents=True)
        schema = (
            dict(request.output_schema)
            if request.output_schema is not None
            else {"type": "object"}
        )
        tool_file = private / "tool.json"
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
        capture = private / "answer.json"
        server = Path(__file__).with_name("claude_code_mcp.py")
        (workspace / WORKSPACE_MCP_CONFIG).write_text(
            json.dumps(
                {
                    "mcpServers": {
                        MCP_SERVER_NAME: {
                            "command": sys.executable,
                            # -I: isolated mode -- no PYTHONPATH, no user site,
                            # no script directory on sys.path. The server is
                            # standard-library only and needs none of them.
                            "args": [
                                "-I",
                                str(server),
                                str(tool_file),
                                "--capture",
                                str(capture),
                            ],
                            "enabledTools": [ANSWER_TOOL_NAME],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return workspace, capture

    # -- the call ---------------------------------------------------------

    def generate(self, request: ModelRequest) -> ModelResponse:
        """One ``agy`` process, in a workspace of its own. No retry."""
        if not os.path.isfile(self._executable):
            raise ProviderNotConfigured(
                _PUBLIC_NOT_AVAILABLE,
                detail=f"the {CLI_NAME!r} executable is no longer at its path",
            )
        argv = command_line(
            self._executable, model=self._config.model, effort=self._config.effort
        )
        _refuse_shell_metacharacters(argv)
        calls = self._root / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        call_directory = Path(tempfile.mkdtemp(prefix="call-", dir=calls))
        try:
            workspace, capture = self._write_call_files(call_directory, request)
            environ = os.environ if self._environ is None else self._environ
            env = child_environment(environ)
            message = user_message(compose_message(request.system, request.user_text))
            return self._run(argv, env, workspace, capture, message)
        finally:
            shutil.rmtree(call_directory, ignore_errors=True)

    def _run(
        self,
        argv: List[str],
        env: Dict[str, str],
        workspace: Path,
        capture: Path,
        message: bytes,
    ) -> ModelResponse:
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(workspace),
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
            return self._converse(session, capture, message)
        finally:
            session.end(self.exit_wait_seconds)

    def _converse(
        self, session: "_Session", capture: Path, message: bytes
    ) -> ModelResponse:
        deadline = time.monotonic() + float(self._config.timeout_seconds)
        state = _StreamState(default_model=self._config.model)
        # One message, then end-of-input: the documented way to say "this
        # turn is the whole session". Also what stops an idle-stdin hang.
        session.send(message)
        session.close_stdin()
        grace_until: Optional[float] = None
        eof = False
        while True:
            if state.answer is None:
                state.answer = _read_capture(capture)
                if state.answer is not None:
                    grace_until = time.monotonic() + self.result_grace_seconds
            if state.answer is not None and (state.result is not None or eof):
                break
            now = time.monotonic()
            limit = deadline if grace_until is None else min(deadline, grace_until)
            if now >= limit:
                if state.answer is None:
                    session.kill()
                    raise self._timeout(state, session)
                break  # the answer is in; the result record never came
            if eof:
                # stdout is finished: only the capture can still matter, and
                # only for the moment the server takes to exit after the CLI.
                if session.wait_briefly(self.poll_seconds) is not None:
                    state.answer = _read_capture(capture)
                    break
                continue
            try:
                kind, item = session.events.get(
                    timeout=min(self.poll_seconds, max(limit - now, 0.0))
                )
            except queue.Empty:
                continue
            if kind == "eof":
                eof = True
            elif kind == "noise":
                session.note(item)
            else:
                state.observe(item)
        returncode = session.wait_briefly()
        return self._outcome(state, session, returncode)

    # -- reading the outcome ---------------------------------------------

    def _outcome(
        self, state: "_StreamState", session: "_Session", returncode: Optional[int]
    ) -> ModelResponse:
        if state.answer is not None:
            # Re-serialised as it came, never edited -- like the API provider.
            return self._response(json.dumps(state.answer.value), state, "tool_use")
        stderr = session.stderr_text()
        if _answer_tool_was_denied(stderr, state):
            raise ProviderError(
                _PUBLIC_NOT_AVAILABLE,
                detail=self._detail(
                    "the answer tool was denied: a headless run cannot ask for "
                    f"approval. Add {ALLOW_RULE!r} to permissions.allow in "
                    f"{SETTINGS_FILE_HINT} (once)",
                    state,
                    session,
                ),
                kind=ProviderErrorKind.INVALID_REQUEST,
            )
        agy_error = _agy_error_kind(stderr)
        result = state.result
        failed_result = result is not None and str(result.get("status", "")).upper() not in (
            "",
            "SUCCESS",
        )
        if failed_result or agy_error is not None:
            text = "\n".join(
                (str(result.get("error") or "") if result else "", stderr)
            )
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=self._detail(
                    f"result status {result.get('status')!r}" if failed_result
                    else f"exit code {returncode}",
                    state,
                    session,
                ),
                kind=agy_error or _classify_text(text) or ProviderErrorKind.OTHER,
            )
        if returncode not in (0, None):
            raise ProviderError(
                _PUBLIC_UNAVAILABLE,
                detail=self._detail(f"exit code {returncode}", state, session),
                kind=_classify_text(session.tail()) or ProviderErrorKind.OTHER,
            )
        fallback = state.fallback_text() or session.plain_stdout()
        if fallback.strip():
            stop = None
            if result is not None:
                stop = _optional_str(result.get("finished_reason")) or _optional_str(
                    result.get("status")
                )
            return self._response(fallback, state, stop)
        # Exit 0 and nothing at all. Before 1.1.1 (and on Windows before
        # 1.0.15) this is how a quota or sign-in failure looked, the answer
        # going to a transcript file instead of a piped stdout. Transcripts
        # are not read: they are the CLI's private store, not an interface.
        kind = _classify_text(stderr) or ProviderErrorKind.EMPTY_RESPONSE
        raise ProviderError(
            "the interpretation service returned no answer",
            detail=self._detail(
                f"no answer and no output; exit code {returncode} (an agy "
                "release older than 1.1.1 can end like this -- update it)",
                state,
                session,
            ),
            kind=kind,
        )

    def _timeout(self, state: "_StreamState", session: "_Session") -> ProviderError:
        # A run that ran out of time while the CLI reported a quota or
        # capacity error was limited, not slow.
        stderr = session.stderr_text()
        kind = _agy_error_kind(stderr) or _classify_text(stderr)
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
        parts = [f"agy CLI: {what}"]
        if state.result is not None and state.result.get("error"):
            parts.append(f"result error: {str(state.result.get('error'))[:500]!r}")
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


class _Captured:
    """A captured answer; a box, so a captured JSON ``null`` is still one."""

    def __init__(self, value: Any) -> None:
        self.value = value


def _read_capture(path: Path) -> Optional[_Captured]:
    """The captured arguments, once the server has written them."""
    try:
        data = path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return None  # not yet (or, on Windows, mid-rename)
    except OSError:
        return None
    try:
        return _Captured(json.loads(data))
    except ValueError:
        return None  # never expected: the server writes atomically


#: Token-count names the CLI's ``usage`` object may use, mapped onto the two
#: names the other providers report. The CLI's documentation names the
#: ``usage`` field but not its members, so several spellings are accepted.
_USAGE_NAMES: Mapping[str, str] = {
    "input_tokens": "input_tokens",
    "inputTokens": "input_tokens",
    "prompt_tokens": "input_tokens",
    "prompt_token_count": "input_tokens",
    "promptTokenCount": "input_tokens",
    "output_tokens": "output_tokens",
    "outputTokens": "output_tokens",
    "completion_tokens": "output_tokens",
    "candidates_token_count": "output_tokens",
    "candidatesTokenCount": "output_tokens",
}


class _StreamState:
    """What the stream-json records have said so far.

    A record names its kind in ``event`` and, per the CLI's documentation,
    carries its payload under a key of the same name; a payload laid out at
    the top level is accepted too.
    """

    def __init__(self, *, default_model: str) -> None:
        self.model = default_model
        self.answer: Optional[_Captured] = None
        self.result: Optional[Dict[str, Any]] = None

    def observe(self, record: Dict[str, Any]) -> None:
        kind = record.get("event", record.get("type"))
        payload = record.get(kind) if isinstance(kind, str) else None
        if not isinstance(payload, dict):
            payload = record
        if kind == "init":
            model = payload.get("model", record.get("model"))
            if isinstance(model, str) and model:
                self.model = model
        elif kind == "result":
            self.result = payload

    def fallback_text(self) -> str:
        if self.result is not None and isinstance(self.result.get("response"), str):
            return self.result["response"]
        return ""

    def denied_actions(self) -> List[str]:
        if self.result is None or not isinstance(self.result.get("denied_actions"), list):
            return []
        return [json.dumps(item) if not isinstance(item, str) else item
                for item in self.result["denied_actions"]]

    def usage(self) -> Dict[str, int]:
        source = self.result.get("usage") if self.result is not None else None
        counts: Dict[str, int] = {}
        if not isinstance(source, dict):
            return counts
        for name, target in _USAGE_NAMES.items():
            value = source.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                counts.setdefault(target, value)
        return counts


class _Session:
    """One child process: its pipes, its reader threads, and its end."""

    def __init__(self, process: "subprocess.Popen[bytes]") -> None:
        self.process = process
        self.events: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._stderr: Deque[str] = deque()
        self._stderr_size = 0
        self._noise: Deque[str] = deque(maxlen=200)
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
                    record = json.loads(line)
                except ValueError:
                    self.events.put(("noise", line))
                    continue
                if isinstance(record, dict):
                    self.events.put(("event", record))
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
        with self._lock:
            self._noise.append(line)

    def plain_stdout(self) -> str:
        """Non-JSON stdout lines: an older CLI's plain-text answer, if any."""
        with self._lock:
            return "\n".join(self._noise)

    def stderr_text(self) -> str:
        with self._lock:
            return "".join(self._stderr)

    def tail(self) -> str:
        text = self.stderr_text()
        with self._lock:
            noise = " | ".join(line[:500] for line in list(self._noise)[-20:])
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
            pass  # the process is gone; its exit is read from stdout's end

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
    """End the child and everything it started. Never raises.

    On Windows a killed CLI can otherwise leave its MCP server -- and, per
    Google's issue tracker, a lock on its app data -- behind, so the whole
    tree goes with ``taskkill /T /F``.
    """
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


def _answer_tool_was_denied(stderr: str, state: _StreamState) -> bool:
    """Whether the run was refused the answer tool for want of approval."""
    names = (ANSWER_TOOL_NAME, MCP_SERVER_NAME)
    for action in state.denied_actions():
        if any(name in action for name in names):
            return True
    for line in stderr.splitlines():
        if _SOFT_DENIAL.search(line) and (
            '"mcp"' in line or "mcp(" in line or any(name in line for name in names)
        ):
            return True
    return False


def _agy_error_kind(stderr: str) -> Optional[ProviderErrorKind]:
    """The kind an ``AGY_ERROR`` stderr line names, if there is one."""
    match = None
    for match in _AGY_ERROR.finditer(stderr):
        pass
    if match is None:
        return None
    try:
        error = json.loads(match.group(1))
    except ValueError:
        return _classify_text(match.group(1)) or ProviderErrorKind.OTHER
    if not isinstance(error, dict):
        return ProviderErrorKind.OTHER
    status = str(error.get("status") or "").upper()
    if status in _STATUS_KINDS:
        return _STATUS_KINDS[status]
    code = error.get("error_code")
    if isinstance(code, int) and not isinstance(code, bool):
        if code == 429:
            return ProviderErrorKind.RATE_LIMITED
        if code in (401, 403):
            return ProviderErrorKind.AUTHENTICATION
        if code in (400, 404):
            return ProviderErrorKind.INVALID_REQUEST
        if code >= 500:
            return ProviderErrorKind.CAPACITY
    return _classify_text(match.group(1)) or ProviderErrorKind.OTHER


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
                    "an argument for the agy shim contains a character cmd.exe "
                    "would interpret; use agy.exe, or a model name without them"
                ),
                kind=ProviderErrorKind.INVALID_REQUEST,
            )


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


__all__ = [
    "AGENT_MODE",
    "ALLOW_RULE",
    "ALWAYS_REMOVED_PATTERNS",
    "ALWAYS_REMOVED_VARIABLES",
    "CLI_NAME",
    "GOOGLE_CREDENTIAL_VARIABLES",
    "MCP_SERVER_NAME",
    "WORKSPACE_MCP_CONFIG",
    "AntigravityTextToCadModel",
    "child_environment",
    "command_line",
    "compose_message",
    "default_root",
    "locate_cli",
    "user_message",
]
