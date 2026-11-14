# Natural language → CAD specification

Status: **Stage 26 — the first AI interpretation layer. Stage 30 put it
behind `POST /generate` and wired it to the browser. A later, owner-approved
design change (prompt `2026-11-12.1`) turned it from "refuse or ask" into
"build, and say what was assumed", and added a bounded verify-and-retry loop
(see *Verified generation* below). Sections dated before that change are kept
as history where marked. Not production-ready.**

> **The LLM does not generate executable CAD code.** It emits a CAD *data*
> document and nothing else — no Python, no CadQuery, no OpenCascade, no
> OpenSCAD, no FeatureScript, no STL, no STEP, no mesh, no code in any
> language — and nothing it returns is executed. The existing validator is
> the authority on whether that document is valid; the existing engine is the
> only thing that turns a document into geometry.

## The pipeline

```
natural-language description
  │  cad_ai.prompt.system_prompt()        one prompt, one place, one version
  │  cad_ai.provider.TextToCadModel       one call, no tools
model text (a string; not CAD)
  │  json.loads                           plain parsing, no repair
candidate response  { status, plan, assumptions, document, summary,
  │                   questions, issues }
  │  CadApplicationService.validate_document
  │    -> cad_core.serialization.deserialize_part
  │    -> cad_core.validator.validate      S1-S20, unchanged
  │  CadApplicationService.build_document  the existing build pipeline, in its
  │                                        isolated child process: E1-E5
  │  unusable / rejected / unbuildable --> ask the model again with the
  │    reasons (compose_retry_text), at most CAD_AI_MAX_ATTEMPTS calls
AiGenerationResult                        document + assumptions + attempts
```

Before the verify-and-retry change the pipeline stopped at the validator and
building was "a separate, deliberate call by the caller". It no longer is: by
default a `GENERATED` result has been validated **and** built.

### The product flow, end to end (Stage 30)

```
browser: a sentence in a text box
  │  POST /generate                        cad_api.generation.TextGenerator
Anthropic (or Gemini)                      the configured provider, backend-side only
  │  the pipeline above                    parse -> validate -> build -> (re-ask)
validated, built CAD document              returned to the browser, with its assumptions
  │  POST /build                           the SAME endpoint the JSON flow uses;
build / cache / isolation                  a cache hit, same outputs as the
                                           verification build
RenderModel  +  STEP / IGES / STL
  │  GET /builds/{key}/render, GET /artifacts/{id}
browser: an interactive 3D model
```

**The AI generates CAD intent. The CAD engine generates geometry.** Nothing in
this chain asks a model for a B-rep, a mesh, a STEP file or a line of code, and
no stage of it executes anything a model returned. The document is the only
thing that crosses from the AI layer, and the validator decides whether it may.

The browser never holds a credential: only the backend talks to a provider.
The frontend names no vendor at all — it posts a sentence to `/generate` and
reads back the outcome, and a test asserts the strings `gemini`, `anthropic`,
`openai`, `claude` and `prompt` appear nowhere in its sources.

The invariant, stated as the stage requires:

```
LLM output -> CAD document -> existing validator -> existing CAD/build pipeline
```

**not**

```
LLM -> arbitrary code -> CAD kernel
```

## Provider

| | |
|---|---|
| **providers** | Anthropic Messages API (default), Google Gemini, `claude_code` — the owner's Claude subscription through the official `claude` CLI, selected only by `CAD_AI_PROVIDER=claude_code` (see *Claude Code* below), `antigravity` (see *Antigravity*), and `openai_compatible` — open-weight models behind any OpenAI-compatible server (Ollama, vLLM, llama.cpp, LM Studio, OpenRouter), selected only by `CAD_AI_PROVIDER=openai_compatible` (see *Open-weight models* below). Named implementations chosen by configuration — no registry, no discovery, no plugin system |
| **SDK** | `anthropic` **1.4.0**, installed and inspected in this environment |
| **default model** | `claude-haiku-5-5`, overridable with `CAD_AI_MODEL`. `claude-sonnet-5-5` (`STEP_UP_MODEL`) is the documented step-up for a deployment that measures Haiku falling short; it is never selected automatically and no larger model is offered. (The Stage 26 text that said `claude-sonnet-5` is superseded; Stage 30 ran on `claude-haiku-4-5-20251001`.) |
| **credential** | `ANTHROPIC_API_KEY` (falling back to `CAD_ANTHROPIC_API_KEY`, because some hosted environments reserve and strip the former) / `GEMINI_API_KEY`, each read by its own provider only |
| **timeout** | 360 s **per model call** by default (`CAD_AI_TIMEOUT_SECONDS`); the default model reasons before it answers |
| **dependency** | the optional `ai` (`anthropic>=1.4`) and `ai-gemini` (`google-genai>=2.22`) extras of `cad-api`, each **imported lazily** inside its own provider module |

Nothing else was added: no agent framework, no orchestration library, no
vector store, no RAG, no tokenizer.

**Stage 26 shipped one provider; a Gemini adapter was added in Stage 28A.**
That is a deliberate, named exception to Stage 26's "exactly one provider"
rule, not a drift into pluggability: `PROVIDER_NAMES` lists both,
`_live_model` branches on the configured name, and a third would mean writing
a third class and naming it there. Nothing above `TextToCadModel` changed —
the prompt, the schema, the outcome taxonomy and the validation flow are
shared, so a run against either provider is comparable with a run against the
other. `docs/ai-provider-comparison.md` sets the two side by side.

**Live Gemini benchmarking is deferred.** The adapter is complete and fully
tested against a mocked SDK and deterministic stubs, but no live run has been
made against either provider and no model-quality claim is made for either.
Selecting a provider does not contact it, and a credential being present is
deliberately not enough to start a run — `--live` is required, because a
benchmark spends money and must be started on purpose. Credentials are read
from the environment, handed to the SDK and stored nowhere: no serialized
config, evaluation result, log line or error message can carry one, and tests
assert that with a synthetic key.

### What the real SDK actually offers, measured

Inspected rather than assumed, from the installed 1.4.0:

```
messages.create parameters (messages.stream takes the same, minus `stream`):
  max_tokens, messages, model, cache_control, container, inference_geo,
  metadata, output_config, service_tier, stop_sequences, stream, system,
  thinking, tool_choice, tools, user_profile_id, workspace_id, ...
```

#### Anthropic

* **structured output is genuine here.** `output_config` accepts
  `{"format": {"type": "json_schema", "schema": {...}}}` —
  `anthropic.types.json_output_format_param.JSONOutputFormatParam` requires
  exactly those two keys. So the model is *constrained* toward the document
  shape, not merely asked in prose. A test reads the live signature and
  asserts the parameter exists and that `response_format`, `output_format`
  and `json_schema` (names this layer might have invented) do **not**.
* **there is no `temperature`, `top_p`, `top_k` or `seed` parameter.** None is
  sent, and none can be. See *Determinism* below; this is measured, not
  assumed, and a test pins it.
* `tools` and `tool_choice` exist and are **never sent**. A test asserts the
  call this provider makes carries exactly
  `max_tokens, messages, model, output_config, system` and nothing else.
* **the system prompt goes as one text block with
  `cache_control: {"type": "ephemeral"}`**, so a re-ask within a request, and
  the next request, read the prompt from the provider's cache. The prompt is
  byte-identical on every call, which is what makes that valid. Cache usage
  counters (`cache_read_input_tokens`, `cache_creation_input_tokens`) are
  carried into `metadata.usage`.
* **`max_tokens` is 32000** (`MAX_OUTPUT_TOKENS`; raised from 16000, then 20000, after a detailed engine's first answer was cut off at the cap; the provider streams via `messages.stream` and collects `get_final_message()`, so the SDK's ~21.3k non-streaming ceiling does not apply), because the default model's
  own reasoning counts against it. No `thinking` parameter is sent. The
  optional `CAD_AI_EFFORT` (`low`, `medium`, `high`, `xhigh`, `max`) is sent as
  `output_config.effort` only when set.
* **a `refusal` stop reason becomes `ProviderErrorKind.REFUSED`**, so it is a
  `MODEL_ERROR` and is never parsed as an answer or re-asked.
* **the schema is narrowed to this API's structured-output subset before it is
  sent.** Measured, not assumed: sending `response_schema()` unchanged returns
  **400** `invalid_request_error` — *"output_config.format.schema: For
  'number' type, property 'exclusiveMinimum' is not supported"*. The subset
  accepts the basic types, `enum`, `const`, `anyOf`, `allOf`, `$ref`/`$defs`,
  the named string formats and `additionalProperties: false`; it accepts no
  numeric bound, no string length or pattern constraint, and no array-size
  constraint. `schema_for_api` therefore removes
  `UNSUPPORTED_SCHEMA_KEYWORDS` — `exclusiveMinimum`, `minimum`, `maximum`,
  `multipleOf`, `minLength`, `maxLength`, `pattern`, `minItems`, `maxItems`,
  `uniqueItems` — at every depth, returning a copy so the shared schema the
  evaluation harness and the Gemini provider see stays whole.

  **This weakens no rule.** The schema is a decoding constraint, never an
  authority on CAD validity: the V1 validator remains the only thing that
  decides that, and it still rejects a zero-or-negative size, a malformed
  feature id and an empty feature list regardless of what the model was free
  to emit. The prompt states each requirement in prose as well, so a model
  that ignores one produces a document the validator refuses — the designed
  behaviour. Tests pin the removal, the untouched original, the surviving
  supported keywords, and that a stripped constraint is still enforced.

  Until this was found, **every** `POST /generate` on this provider failed
  with `invalid_model_output`/`model_error` before interpretation was even
  attempted. It went unnoticed because Anthropic had never been exercised
  live; the request never reached the model.

#### Gemini

Measured against the installed `google-genai` **2.22.0**:

* `client.models.generate_content` takes exactly `model`, `contents` and
  `config`.
* `GenerateContentConfig` carries `system_instruction`, `response_mime_type`
  and `response_json_schema` — the last takes a **raw JSON Schema**, which is
  what this application already has, so the same `response_schema()` object
  goes to both providers unchanged.
* **It also carries `temperature`, `top_p`, `top_k` and `seed`, which the
  Anthropic SDK does not.** None is set. Leaving the SDK's own defaults is
  what keeps a Gemini run comparable to an Anthropic one; a run records both
  that the controls exist (`deterministic_decoding: available`) and that none
  was used (`decoding_controls_used: none`), so "could not be set" and "could
  have been, was not" never read the same.
* `tools`, `tool_config` and `automatic_function_calling` are **never sent**,
  asserted the same way.
* Usage is mapped onto the same `input_tokens` / `output_tokens` names the
  other provider reports, so the harness aggregates one vocabulary.

**The default model `gemini-2.5-pro` could not be verified offline.** The
Anthropic SDK ships a `Model` literal that a test checks its default against;
the Gemini SDK ships no equivalent, and `models.list()` needs a credential.
It is overridable with `CAD_AI_MODEL` and is verified at run time by the API
itself — an unknown name is a provider error, not a silent fallback.

### Claude Code: your own subscription, through the official CLI

> **Measured live once, in the Linux container** (CLI 2.1.323, with the
> API-key opt-in because no subscription login exists there, in a scrubbed
> environment with a throwaway HOME): "an M10 hex nut" in 9 s and "a
> hexacopter drone with six arms" in 76 s, both generated, valid and built
> on the first call, no processes left behind. The hexacopter took 13.9k
> output tokens against 7.8k through the API provider. A real subscription
> login and the Windows paths have not been exercised yet.

A third provider, `claude_code` (`cad_ai.claude_code_provider`), runs the
application on the owner's **Claude subscription** instead of an API key. It
drives the official `claude` program (Claude Code), already installed and
logged in on the machine. It uses only the Python standard library. It adds no
dependency and no SDK.

**Enabling it (Windows, PowerShell):**

```powershell
claude --version            # installed?  (claude.exe, or the npm shim claude.cmd)
claude                      # once, interactively: /login with your subscription
$env:CAD_AI_PROVIDER = "claude_code"          # the only way it is ever selected
# optional: $env:CAD_CLAUDE_CODE_PATH = "C:\Users\you\.local\bin\claude.exe"
# optional: $env:CAD_AI_MODEL = "claude-haiku-5-5"   (the default, as for the API)
.\start.ps1
```

`CAD_AI_PROVIDER=claude_code` is required. `resolve_provider` never picks
this provider by itself: an installed CLI is not a decision to spend a
subscription on this application. It is listed in `PROVIDER_NAMES` but not in
`AUTOMATIC_PROVIDER_NAMES`. Without `CAD_CLAUDE_CODE_PATH`, `claude` is looked
up on `PATH`. When that variable names a file that does not exist, the
provider is *not configured*. It never falls back to a different `claude`.

**What one call sends.** Each `generate` starts one process in an empty
folder of its own (`%TEMP%\cad-ai-claude-code\cwd`). It is never the
repository, so no `CLAUDE.md` is found there. The command line is:

```
claude -p --input-format stream-json --output-format stream-json --verbose
       --model claude-haiku-5-5
       --system-prompt-file %TEMP%\cad-ai-claude-code\prompts\system-<sha256>.txt
       --tools "" --strict-mcp-config --mcp-config <per-call>\mcp.json
       --allowedTools mcp__cad__submit_answer --permission-mode dontAsk
       --setting-sources "" --disable-slash-commands --no-session-persistence
       --max-turns 1            [--effort <CAD_AI_EFFORT>]
```

* **The system prompt is in a file** named by its SHA-256 and written once.
  It never appears on a command line, in a process listing or in a log. The
  user's description goes to **stdin** as one stream-json user message.
* **Claude Code has no tools of its own** (`--tools ""`), so it has no shell,
  no files and no web. It loads none of the user's settings, hooks or plugins
  (`--setting-sources ""`), no slash commands or skills, and no other MCP
  server (`--strict-mcp-config`, plus `ENABLE_CLAUDEAI_MCP_SERVERS=false` in
  its environment).
* **It has one MCP server, with one tool.** `cad_ai.claude_code_mcp` is a
  standard-library stdio server (newline-delimited JSON-RPC 2.0: `initialize`,
  `tools/list`, `tools/call`, `ping`). It offers `submit_answer`, which Claude
  Code exposes as `mcp__cad__submit_answer`. Its input schema is the response
  schema, narrowed by `schema_for_api` exactly as the API provider's own
  `submit_answer` tool is. A call gets back "Answer received." and nothing
  else. The server validates nothing: the application's validator does. It is
  started as `python -I <path>\claude_code_mcp.py <tool.json>` with the
  application's own interpreter. Isolated mode by file path starts in
  milliseconds and needs no `PYTHONPATH`. `python -m cad_ai.claude_code_mcp`
  also works, but it imports the whole package first, which takes about 1.4 s.
* **The answer is the tool call's input.** It is read from the CLI's own
  stream-json `assistant` event and re-serialised unedited, as the API
  provider does. When a run ends with no such call, the final result text is
  returned instead, and the generation layer judges it like any other text
  (unusable, so it re-asks). No parsing leniency is added. `--max-turns 1`
  stops the CLI after the turn that called the tool, so the answer is not
  read back as input for a second turn the application has no use for. Once
  the answer is in, the provider waits briefly for the `result` event, which
  carries the token counts, and then ends the process.

**What it never does.** It never reads, prints or passes a credential. The
subscription login is the CLI's own business. The child inherits the
environment it needs (`PATH`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`,
`SystemRoot`, `TEMP`, proxy settings, …). It does **not** get
`ANTHROPIC_API_KEY`, `CAD_ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN`, so the
CLI uses the subscription login rather than billing a key. With
`CAD_CLAUDE_CODE_USE_API_KEY=1` it keeps them, which is useful for testing
without a subscription. `GEMINI_API_KEY` and Claude Code's own nesting markers
(`CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `CLAUDE_CODE_SSE_PORT`) are always
removed. Variables are dropped by name, and no value is read. The provider
also never executes anything the model returns, never uses `--bare` (which
ignores the subscription login and demands an API key), and never bypasses
permissions.

**Errors** map onto the same neutral kinds as the other providers:

| Condition | Kind |
|---|---|
| no `claude` found | `ProviderNotConfigured` |
| not logged in, or the login is rejected | `AUTHENTICATION` |
| a subscription usage limit, or a rate limit | `RATE_LIMITED` |
| overloaded or a server error | `CAPACITY` |
| `CAD_AI_TIMEOUT_SECONDS` exceeded | `TIMEOUT` (`RATE_LIMITED`/`CAPACITY` when the CLI was retrying such an error) |
| any other non-zero exit | `OTHER` |

The CLI's stderr tail goes only into `detail`, never into the public message.
On every path the child is ended **with everything it started**. On Windows
it runs in a new process group with no console window, and it is stopped with
`taskkill /PID <pid> /T /F`. On POSIX it gets its own session, and the group
is killed. Every pipe is closed. The MCP server also exits by itself when its
stdin closes.

**Windows notes.** `claude` may be `claude.exe` (the native installer) or
`claude.cmd` (npm). Both are started from their resolved path, without
`shell=True`. Windows still runs a `.cmd` through `cmd.exe`, so the provider
refuses to pass `cmd.exe` metacharacters (`" & | < > ^ % !`) to a `.cmd`
shim. Only the model name and the temporary paths could carry them, and
`claude.exe` is the better choice in any case. All stream-json and MCP traffic
is UTF-8 bytes, never the locale codepage, and the MCP server writes `\n`, not
`\r\n`.

**Terms.** This is for one person running the application **for
themselves**, on their own subscription, through the official CLI. It is not
a way to offer subscription logins, or this application backed by them, to
other people. A service for others belongs on an API key.

**Not built yet.** Each call pays a few seconds to start the CLI. A
long-lived process, or a spare started ahead of the next call, would hide
that cost. That is a possible later optimisation and is deliberately not part
of this provider.

**Not verified without a live run:** that the model calls
`mcp__cad__submit_answer` (the prompt names the tool `submit_answer`), how
`--max-turns 1` reports after the tool runs, and the exact text the CLI writes
when it is not logged in or has reached a usage limit. The tests use a fake
`claude` that speaks stream-json and drives the real MCP server.

### Antigravity: your own Google account, through the official CLI

> **Never run against a real `agy`.** Everything below was built from
> Google's documentation and its public issue tracker and tested against a
> fake `agy`. The real CLI, a real sign-in, Windows and the exact wording of
> its errors have not been exercised. Read *Not verified* before relying on
> it.

A fourth provider, `antigravity` (`cad_ai.antigravity_provider`), is the
Google counterpart of `claude_code`. It runs the application on the owner's
**Google account** (the free tier, or their plan) instead of an API key. It
drives Google's official **Antigravity CLI** (`agy`) in its headless mode,
already installed and signed in on the machine. It uses only the Python
standard library and adds no dependency.

**Enabling it (Windows, PowerShell):**

```powershell
irm https://antigravity.google/cli/install.ps1 | iex    # Google's installer (README)
agy                        # once, interactively: sign in with your Google account
agy models                 # which model names your account can use
# once: allow the answer tool in a headless run (see "The one setting" below)
$env:CAD_AI_PROVIDER = "antigravity"            # the only way it is ever selected
# optional: $env:CAD_ANTIGRAVITY_PATH = "$env:LOCALAPPDATA\agy\bin\agy.exe"
# optional: $env:CAD_AI_MODEL = "gemini-3.8-flash"   (the default)
.\start.ps1
```

From `cmd.exe` the installer is `curl -fsSL
https://antigravity.google/cli/install.cmd -o install.cmd && install.cmd &&
del install.cmd`. On macOS and Linux it is `curl -fsSL
https://antigravity.google/cli/install.sh | bash`. Use a recent release
(1.5.x when this was written). The stream-json protocol needs 1.1.15 or
later. Releases before 1.1.1 lost a piped answer (see *Errors*). A model name
without a reasoning suffix needs 1.3.4 or later.

`CAD_AI_PROVIDER=antigravity` is required. Like `claude_code`, the provider
is in `PROVIDER_NAMES` and not in `AUTOMATIC_PROVIDER_NAMES`. Without
`CAD_ANTIGRAVITY_PATH`, `agy` is looked up on `PATH`. When that variable
names a file that does not exist, the provider is *not configured*. It never
falls back to a different `agy`.

**The default model** is `gemini-3.8-flash`. Antigravity's model table offers
Gemini 3.8 Flash on the free plan, and a Flash model is what the Gemini API
route uses too. It is the base name, without a reasoning suffix such as
`-medium` or `-high`. `CAD_AI_EFFORT` adds `--effort`, and `CAD_AI_MODEL`
overrides the model (`gemini-4-hydrogen` is the other free-plan Gemini).
A headless run with a model the account cannot use exits with an error. It
does not quietly switch models.

**The one setting the owner adds by hand.** An MCP tool asks for approval by
default. A headless run cannot ask, so the CLI *soft-denies* the call, and
the answer never arrives. Google documents no per-run or per-folder grant
(issue #627 on its tracker), so the grant goes in the CLI's own global
settings, `%USERPROFILE%\.gemini\antigravity-cli\settings.json`. Add this
once:

```json
{ "permissions": { "allow": ["mcp(text_to_cad/submit_answer)"] } }
```

Merge it into the file's existing `permissions.allow` list if there is one.
It allows exactly one tool of one server. Use the `server/tool` form shown:
the CLI also accepts `mcp(text_to_cad)`, which never matches (issue #916).
**The provider never writes this file**, or any other user configuration.
When a run was denied the tool, it reports a configuration error
(`INVALID_REQUEST`) whose `detail` names this rule. It does not re-ask.

**What one call sends.** Each `generate` makes a fresh workspace,
`%TEMP%\cad-ai-antigravity\calls\call-<random>\workspace`. It is never the
repository. The workspace holds exactly one file,
`.agents\mcp_config.json`, the documented workspace-level MCP configuration.
A fresh folder per call is needed for three reasons. The per-call MCP
configuration has to live inside the workspace. Concurrent calls must not
share one. And a reused folder may resume the previous conversation, as
reported on issue #76. The command line is:

```
agy --input-format stream-json --output-format stream-json
    --model gemini-3.8-flash --mode accept-edits --disable-slash-commands
    [--effort <CAD_AI_EFFORT>]
```

* **No prompt, user text, path or credential is on it.** There is no `-p`.
  In this mode the CLI reads the prompt from stdin and drops a `-p` prompt.
  The provider writes **one** record, `{"event": "user", "message":
  {"content": "..."}}`, and closes stdin. The CLI documents closing stdin as
  ending the session after that turn. Closing it also avoids the idle-stdin
  hang reported on the CLI's issue tracker.
* **The system prompt travels in that message.** `agy` has no system-prompt
  option. A workspace rules file (`AGENTS.md`, `GEMINI.md`) is silently cut
  off at 12,000 characters, or 24,000 bytes per file in later releases. That
  is a fraction of the prompt's ~53k characters. So the message is
  `=== INSTRUCTIONS FOR THIS REQUEST ===`, the system prompt, one line
  telling the model to answer only through `submit_answer`, then
  `=== THE REQUEST ===` and the user's text. The prompt is never on a command
  line and never in a file the provider writes. The CLI does keep the
  conversation in its own store under `%USERPROFILE%\.gemini\antigravity-cli`.
* **One MCP server, one tool.** The workspace configuration registers
  `text_to_cad`, which is `python -I <path>\claude_code_mcp.py <tool.json>
  --capture <answer.json>`, with `"enabledTools": ["submit_answer"]`. The
  name is deliberately distinctive. When a global and a workspace server
  share a name, the CLI keeps whichever is already running. The tool's
  input schema is the response schema **unchanged**: Gemini takes that
  schema as it is, and the Gemini API provider already sends it. The tool
  file and the capture file sit beside the workspace, not inside it, where
  the agent's own file tools could reach them.
* **The answer is captured by the MCP server.** The CLI's stream-json
  records are not documented to carry a tool call's arguments, and old
  releases wrote nothing to a piped stdout. So `claude_code_mcp` takes
  `--capture <file>`. On the first successful `tools/call` of the tool it
  writes the call's `arguments` to that file atomically (a temporary file,
  then a rename), before it replies. The provider polls for the file. Once
  the file appears, the provider waits up to 5 s for the CLI's `result`
  record, which carries `usage`, and then ends the process tree. The
  captured object is re-serialised unedited, as the other providers do.
  When a run ends with no capture, the provider returns the `result`
  record's `response` text, or failing that any plain stdout text. The
  generation layer judges that text like any other (unusable, so it
  re-asks). No parsing leniency is added. Without `--capture` the server
  behaves exactly as before, and `claude_code` does not pass it.
* **`--mode accept-edits`** keeps the CLI out of Auto mode. Auto has been
  the default since 1.5.7, and in it a reviewer model may let sandboxed shell
  commands run. Accept-edits applies file edits without asking and asks
  before everything else, which in a headless run means everything else is
  soft-denied.

**What it cannot switch off, and says so.** `agy` has no option that removes
its own tools (Claude Code's `--tools ""` has no equivalent). It also has no
option that skips the user's global rules (`~/.gemini/GEMINI.md`) or global
MCP servers (`~/.gemini/config/mcp_config.json`). Moving `HOME` would also
lose the sign-in, which lives in the system keyring. The built-in tools are
confined instead. The workspace is an empty throwaway folder. Files outside
it need approval. And a headless run can approve nothing, so it gets no
shell commands and no web tools. Within the workspace the agent could still
read or write files. The folder is deleted after the call.

**What it never does.** It never reads, prints or passes a credential. The
child inherits the environment it needs (`PATH`, `USERPROFILE`, `APPDATA`,
`LOCALAPPDATA`, `SystemRoot`, `TEMP`, proxy settings, …). It does **not**
get `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_API_KEY`,
`AGY_API_KEY`, `AGY_ADC_AUTH`, `GOOGLE_APPLICATION_CREDENTIALS` or
`GOOGLE_GENAI_USE_VERTEXAI`, so the CLI uses its own sign-in. With
`CAD_ANTIGRAVITY_USE_API_KEY=1` it keeps them. The CLI's documented key route
also needs `"modelProvider": "gemini"` in its settings. Some variables are
**always** removed, opt-in or not: `ANTHROPIC_API_KEY`,
`CAD_ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDECODE`, and every
variable named `CLAUDE_CODE_*`, `CLAUDE_SESSION_*` or `CLAUDE_*TOKEN*`. A
host Claude Code session's credentials must never reach the child.
Variables are dropped by name, and no value is read. The provider never
passes `--dangerously-skip-permissions` and never executes anything the
model returns.

**Errors** map onto the same neutral kinds as the other providers:

| Condition | Kind |
|---|---|
| no `agy` found | `ProviderNotConfigured` |
| `authentication required`, `authentication failed`, `UNAUTHENTICATED`, 401/403 | `AUTHENTICATION` |
| `RESOURCE_EXHAUSTED`, quota, 429 | `RATE_LIMITED` |
| `UNAVAILABLE`, overloaded, 5xx | `CAPACITY` |
| the answer tool soft-denied (the allow rule is missing) | `INVALID_REQUEST`, with the rule in `detail` |
| an unknown flag or model, `INVALID_ARGUMENT`, 400/404 | `INVALID_REQUEST` |
| `CAD_AI_TIMEOUT_SECONDS` exceeded | `TIMEOUT` (`RATE_LIMITED`/`CAPACITY` when stderr already showed one) |
| exit 0 with no output at all | `EMPTY_RESPONSE` |
| any other failure | `OTHER` |

The CLI's structured `AGY_ERROR: {...}` stderr line (exit 3, since 1.2.6) is
read first: its `status`, then its `error_code`. A `result` record whose
`status` is not `SUCCESS` is a failure even when the exit code is 0. The CLI
can end a streamed run that way. The stderr tail goes only into `detail`,
never into the public message. On every path the child is ended **with
everything it started**. On Windows it runs in a new process group with no
console window, and it is stopped with `taskkill /PID <pid> /T /F`. A killed
`agy` can otherwise leave its MCP server, and a lock on its app data, behind.
On POSIX it gets its own session, and the group is killed.

An empty stdout with exit 0 is how releases before 1.1.1 ended. On Windows
before 1.0.15 the answer went to the console instead of the pipe. Quota and
sign-in failures were swallowed. That case is reported as `EMPTY_RESPONSE`,
with an update hint in `detail`. The CLI's transcript files are **not** read:
they are its private store, not an interface.

**Windows notes.** Google's installer places a native `agy.exe` (reported as
`%LOCALAPPDATA%\agy\bin`). It is started from its resolved path without
`shell=True`. A `.cmd` shim is handled like `claude.cmd`: the provider
refuses to pass `cmd.exe` metacharacters to it. Only the model name could
carry them, since no path is on the command line. All stream-json and MCP
traffic is UTF-8 bytes. Auto mode, and with it the terminal sandbox, exists
only on Windows 11 24H2 and later. The provider does not use it anyway.

**Terms.** This is for one person running the application **for
themselves**, on their own Google account, through Google's official CLI. It
is not a way to offer that account, or this application backed by it, to
other people. A service for others belongs on an API key. Google's forum has
open questions about scripting `agy` from other tools on an individual
account. The owner should read the Antigravity terms
(antigravity.google/terms) before relying on this.

**Not verified** (and why). The documentation site `antigravity.google` was
blocked from the build environment. The facts here come from search excerpts
of that site, Google's `antigravity-cli` README and changelog on GitHub, and
its issue tracker. The following are therefore unconfirmed:

* the exact output record layout. The provider accepts the payload both
  under a key named like the `event` and at the top level;
* the names inside `usage`. Several spellings are mapped onto
  `input_tokens`/`output_tokens`;
* whether a workspace MCP server needs the folder to be trusted first. If
  it does, trust `%TEMP%\cad-ai-antigravity` once;
* whether `accept-edits` behaves as described in every release;
* the exact text of the sign-in and quota errors;
* whether Gemini through `agy` accepts the full response schema as a tool
  schema;
* that the model calls the tool at all.

A Linux smoke test with an API key is possible (see `CAD_ANTIGRAVITY_USE_API_KEY`).
It has not been run.

**An alternative not taken.** `agy` has its own structured output,
`--json-schema <file>`, with the answer in the `result` record's
`structured_output`. That needs no MCP server and no allow rule. It was not
used, so that this provider answers through the same `submit_answer` tool as
the others. It is the obvious fallback if the MCP route proves unreliable.

### Open-weight models through an OpenAI-compatible server

> **Never run against a real server.** Everything below was built from the
> servers' own documentation and tested against a fake HTTP server on
> `127.0.0.1` (`apps/api/tests/test_openai_compatible_provider.py`). No open
> model has produced a CAD document through this route yet, so **no quality
> claim of any kind is made for one**. Read *Confirmed and assumed* before
> relying on a server-specific detail.

A fifth provider, `openai_compatible` (`cad_ai.openai_compatible_provider`),
runs the application on **open-weight models**. Ollama, vLLM, llama.cpp's
`llama-server`, LM Studio and hosted routers such as OpenRouter all serve
`POST {base_url}/chat/completions` in the OpenAI shape, so one small client
reaches every one of them. It uses only the Python standard library
(`urllib.request`, `json`, `ssl`): no SDK, no new dependency, no extra to
install.

**Configuration.**

| variable | |
|---|---|
| `CAD_AI_PROVIDER=openai_compatible` | **required** — the only way it is ever selected. It is in `PROVIDER_NAMES` and not in `AUTOMATIC_PROVIDER_NAMES`: a base URL being set is not a decision to send requests there |
| `CAD_OPENAI_BASE_URL` | **required** — the API root, e.g. `http://localhost:11434/v1`. `http`/`https` only; no user name or password in it, no query, and not ending in `/chat/completions` (that is appended). Refused as *not configured* otherwise |
| `CAD_AI_MODEL` | **required** — there is no default open model (`DEFAULT_MODELS["openai_compatible"]` is empty on purpose). The name exactly as the server lists it at `GET {base_url}/models` |
| `CAD_OPENAI_API_KEY` | optional — local servers need none. Read once, sent only as `Authorization: Bearer …`, never stored as an attribute, logged, printed or put in a message (an echoed key is replaced by `[redacted]`) |
| `CAD_OPENAI_ANSWER_MODE` | `json_schema` (default), `tool` or `json_object` — see below |
| `CAD_OPENAI_STRICT` | `1` (default) sends `strict: true` on the schema; `0` sends `false`, for a hosted route whose strict mode refuses this schema |
| `CAD_OPENAI_STREAM` | `1` asks for a server-sent-event stream; off by default (see *Streaming*) |
| `CAD_OPENAI_MAX_TOKENS` | unset: the generation layer's bound (32000) is sent as `max_tokens`. An integer: the smaller of the two. `none`: no `max_tokens` at all (the server uses the context that is left) |
| `CAD_AI_TIMEOUT_SECONDS` | per call, as for every provider. **Raise it for local models**: a large part is 10–20k output tokens, which at 20–40 tokens/s is 5–15 minutes. `1800` is a reasonable start on a single consumer GPU |
| `CAD_AI_EFFORT` | when set, sent as `reasoning_effort` (Ollama and llama.cpp document the field; other servers may ignore or refuse it). Unset, nothing is sent |

`credential_available(provider="openai_compatible")` answers whether the base
URL is set — the key is optional. As for the CLI providers, it answers only
when asked by name, so a base URL never makes the ordinary suite think a
credential is present.

**What is sent.** One `POST {base_url}/chat/completions` per call, with
`Content-Type: application/json`, a generic `User-Agent`, and
`Authorization` only when a key is configured:

```jsonc
// CAD_OPENAI_ANSWER_MODE=json_schema (default)
{"model": "<CAD_AI_MODEL>",
 "messages": [{"role": "system", "content": "<the system prompt>"},
              {"role": "user",   "content": "<the description, and any reference designs>"}],
 "max_tokens": 32000,
 "response_format": {"type": "json_schema",
                     "json_schema": {"name": "cad_answer",
                                     "schema": <response_schema(), unchanged>,
                                     "strict": true}}}

// CAD_OPENAI_ANSWER_MODE=tool
{"model": …, "messages": […], "max_tokens": 32000,
 "tools": [{"type": "function",
            "function": {"name": "submit_answer",
                         "description": "<ANSWER_TOOL_DESCRIPTION>",
                         "parameters": <response_schema(), unchanged>,
                         "strict": true}}],
 "tool_choice": {"type": "function", "function": {"name": "submit_answer"}}}

// CAD_OPENAI_ANSWER_MODE=json_object
{"model": …, "messages": […], "max_tokens": 32000,
 "response_format": {"type": "json_object"}}

// CAD_OPENAI_STREAM=1 adds, in any mode:
 "stream": true, "stream_options": {"include_usage": true}
```

The schema is the **whole** `response_schema()`. These servers are not the
Anthropic structured-output compiler, so `schema_for_api` is not applied: no
keyword is stripped. No `temperature`, `top_p`, `top_k`, `seed` or penalty is
sent. An open model's decoding settings belong to the server that serves it
(an Ollama Modelfile `PARAMETER temperature`, vLLM's `--generation-config`,
`llama-server --temp`), where the model's own recommended values usually
already live. A temperature variable here would duplicate that, and would
make a run's settings depend on two places.

**What is read.** `choices[0].message.content` is the answer. In `tool` mode
the answer is the `submit_answer` call's `arguments`. That is a JSON
**string** in this API, so it is parsed and re-serialised, never edited.
Arguments that do not parse are passed on as the raw string, so the
generation layer reports them unusable and asks again. With no such call the
content is the answer, as for the Anthropic tool mode. `finish_reason`
becomes `stop_reason`. `usage.prompt_tokens`/`completion_tokens` become
`input_tokens`/`output_tokens`, and `model` comes from the reply.
`finish_reason: "length"` returns the cut-off text unchanged: it does not
parse, and the generation layer asks again. No content is `EMPTY_RESPONSE`.
A `refusal`, or `finish_reason: "content_filter"` with no content, is
`REFUSED`. Nothing strips a code fence or a `<think>` block. Configure the
server's reasoning parser instead (vLLM `--reasoning-parser`, llama.cpp
`--reasoning-format`), so reasoning arrives in its own field and the content
is only the answer.

**Answer modes, which to use.**

* `json_schema` — the default, and the one to use with Ollama, vLLM,
  llama.cpp and LM Studio. Each constrains decoding to the schema, so the
  answer always parses. Whether it is *right* is still the validator's call.
* `tool` — for a server or route that handles function calling better than
  response formats. Ollama's compatibility page lists `tool_choice` as **not
  supported**, so the model may simply answer in text there. vLLM refuses a
  named `tool_choice` with HTTP 400 unless it was started with
  `--enable-auto-tool-choice --tool-call-parser <parser>`.
* `json_object` — for a server with no schema support at all. It gives JSON,
  not held to the schema.

**Streaming.** Off by default. A non-streamed answer sends nothing until it
is complete, and a large part on a local GPU can take many minutes. A reverse
proxy, a corporate proxy or a load balancer may close a connection idle that
long. `CAD_OPENAI_STREAM=1` requests SSE, reassembles content deltas and
tool-call argument fragments (per call `index`), takes `usage` from the
final chunk, ignores `:` keep-alive comments, and reports a mid-stream
`error` event like an HTTP error. A stream that ends with neither a
`finish_reason` nor `[DONE]` is a transport failure, not an answer. Both
paths enforce `CAD_AI_TIMEOUT_SECONDS` as a total deadline, checked between
reads. A single read can therefore overrun it by at most one socket timeout.

**Context length — the setting that matters most.** The system prompt is
about 53k characters (~14k tokens). A matching reference design adds up to
~4k tokens, and a re-ask carries the rejected document and the reasons. Then
comes the answer: 1–3k tokens for a simple part, 10–20k for an engine.

* **Minimum: 32768 tokens**, with `CAD_OPENAI_MAX_TOKENS` around 12000 so
  that prompt plus output bound fits.
* **Recommended: 65536** for complex parts and re-asks. Ollama's own
  context-length page says the same for coding and agent tasks.

Many local setups default far lower. **Ollama** uses 4096 tokens below
24 GiB of VRAM (its FAQ says 4096; its context-length page says 4k/32k/256k
by VRAM). Its OpenAI-compatible endpoint **cannot set the context per
request**. Raise it on the server instead:

```sh
OLLAMA_CONTEXT_LENGTH=32768 ollama serve      # every model, or the app's slider
# or one model, with a Modelfile:
#   FROM qwen3:32b
#   PARAMETER num_ctx 32768
ollama create qwen3-32b-32k -f Modelfile      # then CAD_AI_MODEL=qwen3-32b-32k
ollama ps                                     # the CONTEXT column shows what is allocated
```

Ollama **truncates an over-long prompt silently** on this endpoint and
returns an answer anyway. It reports no error, so this provider cannot see
it. It does not second-guess token counts either: Ollama's prompt count
shrinks when its prompt cache is reused, so a heuristic would misfire. Check
`ollama ps`. A model that ignores the instructions and the examples
completely is the symptom.

Where a server *does* report the problem, it surfaces as `INVALID_REQUEST`.
The `detail` then says the prompt and output bound do not fit the context,
and quotes the server's words. That covers vLLM and llama.cpp (an HTTP 400
whose message mentions the context) and a reply carrying `truncated: true`.

**Recipes** (Windows PowerShell shown; on Linux/macOS use `export`):

```powershell
# Ollama — answer mode json_schema (the default)
ollama pull qwen3:32b
$env:CAD_AI_PROVIDER = "openai_compatible"
$env:CAD_OPENAI_BASE_URL = "http://localhost:11434/v1"
$env:CAD_AI_MODEL = "qwen3:32b"                   # or your num_ctx copy
$env:CAD_AI_TIMEOUT_SECONDS = "1800"
.\start.ps1
```

```sh
# vLLM — json_schema; the server rejects max_tokens + prompt > --max-model-len
vllm serve Qwen/Qwen3-32B --max-model-len 65536   # add --api-key K to require a key
export CAD_AI_PROVIDER=openai_compatible CAD_OPENAI_BASE_URL=http://localhost:8000/v1
export CAD_AI_MODEL=Qwen/Qwen3-32B CAD_OPENAI_MAX_TOKENS=24000 CAD_AI_TIMEOUT_SECONDS=1800
# tool mode needs: --enable-auto-tool-choice --tool-call-parser <parser for the model>
```

```sh
# llama.cpp — json_schema (llama-server also accepts json_object)
llama-server -m model.gguf -c 65536 --port 8080 --alias local-model   # --jinja is on by default
export CAD_AI_PROVIDER=openai_compatible CAD_OPENAI_BASE_URL=http://localhost:8080/v1
export CAD_AI_MODEL=local-model CAD_AI_TIMEOUT_SECONDS=1800
```

```sh
# LM Studio — json_schema; set the context length when loading the model
lms server start                     # or the Developer tab; serves http://localhost:1234/v1
export CAD_AI_PROVIDER=openai_compatible CAD_OPENAI_BASE_URL=http://localhost:1234/v1
export CAD_AI_MODEL=<the identifier LM Studio shows> CAD_AI_TIMEOUT_SECONDS=1800
```

```sh
# OpenRouter — hosted; a key is required
export CAD_AI_PROVIDER=openai_compatible CAD_OPENAI_BASE_URL=https://openrouter.ai/api/v1
export CAD_OPENAI_API_KEY=…        # never in a file in this repository
export CAD_AI_MODEL=qwen/qwen3-coder
```

On OpenRouter, structured-output support is per *endpoint*, not per model.
An endpoint that does not support it ignores `response_format` unless the
request sets `provider.require_parameters`, which this provider does not
send, to keep the body server-neutral. Pick a model whose endpoints list
`structured_outputs`, or use `CAD_OPENAI_ANSWER_MODE=tool`. If strict mode
on a route refuses the schema, set `CAD_OPENAI_STRICT=0`.

**Network.** A server on this machine (`localhost`, `*.localhost`,
`127.0.0.0/8`, `::1`) is always reached directly, even with a proxy
configured: no proxy can reach your loopback. Any other host goes through the
standard `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` variables, as `urllib` reads
them. TLS uses the system CA bundle (`ssl.create_default_context`, which also
honours `SSL_CERT_FILE`). **Redirects are not followed.** urllib would
re-send the `Authorization` header to wherever the server pointed, so a 3xx
is reported as `INVALID_REQUEST` instead: set the base URL to the final
address. A key sent to a plain-`http` address that is not on this machine
crosses the network unencrypted. That is the deployment's choice, but use
`https` for anything remote.

**Errors**, mapped onto the neutral kinds. The server's body goes only into
`detail` (truncated to 1000 characters, key redacted), never into the
public message:

| failure | kind |
|---|---|
| connection refused, host unresolvable or unreachable | `TIMEOUT`, with the message "the model server is not reachable at the configured base URL". *Transport failed*, like the Anthropic provider's connection errors: transient (a local server may still be starting), and it says nothing about the model |
| connection dropped mid-reply, short body, stream cut off | `TIMEOUT` |
| socket timeout, or the total deadline passed | `TIMEOUT` |
| HTTP 401 / 403 | `AUTHENTICATION` |
| HTTP 402 (OpenRouter: out of credit) / 429 | `RATE_LIMITED` |
| HTTP 404 | `INVALID_REQUEST`; `detail` names the two usual causes, an unknown model or a base URL that is not the API root |
| HTTP 400 / 413 / 422, any other 4xx, a 3xx | `INVALID_REQUEST`; a context overflow says so in `detail` |
| HTTP 408 / 504 | `TIMEOUT` |
| HTTP 500–503 and other 5xx | `CAPACITY` |
| TLS verification failure | `INVALID_REQUEST` (a configuration problem, not an outage) |
| a 200 reply carrying an `error` object | its numeric `code` mapped as above, else `OTHER` |
| a reply that is not JSON | `OTHER` (`detail` asks whether the base URL is the API root) |
| no content, no tool call | `EMPTY_RESPONSE` |
| a refusal | `REFUSED` |

**Open models worth trying — untested here.** None has been run through
this application. Context limits are the model cards' as far as known when
this was written. Check the card before relying on them, and set the server's
context explicitly either way.

| model | size | native context | names |
|---|---|---|---|
| Qwen3-Coder-30B-A3B-Instruct | 30B MoE (3B active) | 256k | `qwen3-coder:30b` (Ollama), `Qwen/Qwen3-Coder-30B-A3B-Instruct` |
| Qwen3-32B | 32B dense | 32k (128k with YaRN) | `qwen3:32b`, `Qwen/Qwen3-32B` |
| gpt-oss-20b / gpt-oss-120b | 21B / 117B MoE | 128k | `gpt-oss:20b`, `openai/gpt-oss-20b` |
| Mistral Small 3.2 24B Instruct | 24B dense | 128k | `mistral-small3.2:24b` |
| Gemma 3 27B | 27B dense | 128k | `gemma3:27b` |
| Llama 3.3 70B Instruct | 70B dense | 128k | `llama3.3:70b` |

The models below about 20B parameters are unlikely to cope with a 14k-token
prompt of strict rules. LM Studio's own documentation warns that models below
7B are often not capable of structured output at all. A coder or
instruction-tuned model with a long native context is the place to start.

**Confirmed and assumed.** The docs sites `docs.ollama.com`, `docs.vllm.ai`,
`lmstudio.ai` and `openrouter.ai` were not reachable from the build
environment. The same documentation was read from the projects' GitHub
sources instead (OpenRouter's from a web search over its docs).

* *Confirmed from the docs:*
  * **Ollama.** `/v1/chat/completions` supports `response_format`, `tools`,
    `stream_options.include_usage`, `max_tokens` and `reasoning_effort`, and
    **not** `tool_choice`. Structured outputs work through the
    OpenAI-compatible `response_format`. The default context is 4096. The
    OpenAI API cannot set the context, so use a Modelfile `num_ctx` or
    `OLLAMA_CONTEXT_LENGTH`.
  * **vLLM.** `response_format` `json_schema` (xgrammar/guidance backends),
    and named `tool_choice`, which needs `--enable-auto-tool-choice` and
    `--tool-call-parser`, else 400. Named calls are constrained to the
    parameter schema.
  * **llama.cpp.** `response_format` `json_object`, `json_schema`, and the
    OpenAI wrapper form `{"type":"json_schema","json_schema":{name,strict,schema}}`.
    OpenAI-style tool calling, with Jinja enabled by default. `-c 0` means
    the model's own context.
  * **LM Studio.** `json_schema` structured output on `/v1/chat/completions`,
    with `strict` (llama.cpp grammars for GGUF, Outlines for MLX).
  * **OpenRouter.** `json_schema` with `name`, optional `strict` and
    `schema`. Support is per endpoint. `provider.require_parameters`
    restricts routing to endpoints that support every parameter sent.
* *Assumed, not confirmed:*
  * the exact wording of vLLM's and llama.cpp's context-overflow errors;
  * that vLLM rejects `max_tokens` + prompt > `--max-model-len` rather than
    clamping it;
  * llama.cpp's `truncated` field on the chat endpoint;
  * which JSON-Schema keywords each grammar engine supports. The full
    response schema uses `anyOf`, `enum`, `exclusiveMinimum`, `pattern` and
    `additionalProperties: false`. An engine may ignore or refuse a keyword;
    a refusal arrives as a 400 with its words in `detail`;
  * LM Studio's support for `json_object`, `tools` and `tool_choice`, and
    how it reports context overflow;
  * that every server streams tool-call arguments as fragments indexed like
    OpenAI's;
  * OpenRouter's 402 for exhausted credit, and its mid-stream error events.

**Limitations.**

* **Small models and a 53k-character prompt.** The prompt was written and
  measured against Claude models. A small open model may follow the schema
  (the grammar sees to that) and still produce geometry that does not
  validate or build. The verify-and-re-ask loop then spends up to
  `CAD_AI_MAX_ATTEMPTS` long local calls on it.
* **Templates help.** A matching reference design gives the model a
  complete, verified answer to adapt, and that matters more for a weaker
  model.
* **Future work:**
  * a compact prompt profile for small contexts;
  * a fine-tune on validated documents;
  * measuring any open model on the corpus;
  * restoring `through_hole` in the schema for servers that have no grammar
    ceiling.
* **The benchmark harness** accepts `--provider openai_compatible` because
  its choices come from `PROVIDER_NAMES`. But `evaluation.py`'s NOT_RUN
  message names the wrong variable for this provider when the base URL is
  unset (it falls back to `CAD_CLAUDE_CODE_PATH`). Its decoding-settings
  record reads the Anthropic SDK's capabilities. Both are for whoever next
  edits that file.

## The AI boundary, exactly

`apps/api/src/cad_ai/` — beside the HTTP transport, **outside `cad_core`**,
because it needs a vendor SDK and `cad_core` has none.

| Module | Responsibility |
|---|---|
| `provider.py` | `TextToCadModel`, `ModelRequest`, `ModelResponse`, `ProviderError`. No vendor type crosses this line |
| `anthropic_provider.py` | the default provider. **The only module that imports `anthropic`**, lazily |
| `gemini_provider.py` | the second provider. **The only module that imports `google.genai`**, lazily |
| `specification.py` | the model-facing view of the contract, derived from `docs/cad-specification.md` and `cad_core.model`'s constants |
| `prompt.py` | the one system prompt and its version |
| `generation.py` | `TextToCadService.generate_cad_from_text` (the verify-and-retry loop), `AiGenerationResult`, `GenerationOutcome`, `GenerationAttempt`, `AttemptStage`, `compose_retry_text` |
| `config.py` | model, timeout, `CAD_AI_MAX_ATTEMPTS`, `CAD_AI_EFFORT`, and a boolean "is a credential present" — **no credential field exists** |

Asserted by tests, in both directions:

* **no `cad_core` module imports an LLM SDK, or `cad_ai`, or names one.** The
  check is stronger than an import scan: the AST's identifiers, attribute
  names and non-docstring string literals are searched, so no lazy import,
  `getattr` or string can smuggle one in. `cad_core` is also run in a **child
  process with every LLM SDK blocked on import**, where it still validates
  and hashes a document.
* **`cad_ai` imports only `cad_core.application_service` and
  `cad_core.model`** — the service boundary and the contract's constants.
  Nothing else, and in particular no geometry module, exporter, cache, build
  layer or isolation layer.
* **`cad_ai` reimplements no validation and no geometry.** No rule-code
  literal appears in it and it defines no `validate`; the only route to
  validity is `self._service.validate_document(...)` and the only route to
  geometric validity is `self._service.build_document(...)`.
* **`cad_api` (the HTTP transport) does not import `cad_ai` or the SDK.**
* **importing `cad_ai` does not import the SDK** — proved in a child process.

## The prompt

One place: `cad_ai/prompt.py`. No prompt string exists in a route, a test or
the provider, and a test asserts that a 400-character span of the
instructions appears in no other file.

It is **assembled, not hand-maintained**:

* the instructions — what the model's job is and what it must refuse — are
  this layer's own content;
* every list in them (`box, cylinder`; `through_hole, subtract, fillet,
  chamfer`; the six axis values; the defaulted parameters; the schema
  version; the units) is interpolated from `cad_core.model`'s constants;
* the contract itself is appended **verbatim** from
  `docs/cad-specification.md` — sections A, B, C.1, C.2, E.1 and F, read from
  the file at import time. A test asserts each quoted section is a literal
  substring of the real document, so the prompt cannot drift from it, and a
  renamed heading raises `KeyError` rather than silently shrinking what the
  model is told.

Sections C.3–C.7 (`through_hole`, `subtract`, `fillet`, `chamfer`, edge
selectors) are deliberately **excluded**: they are valid V1 but outside this
stage's subset, and showing a model a feature it must not use invites it to
use one. It is told instead that they exist and are unsupported.

*(Historical, Stage 26.)* Assembled size then: 14 943 characters. The current
prompt (`2026-11-12.1`) is **33 084 characters** (pinned by a test), because it
now embeds three worked examples and the measured buildability rules, and
because the specification excerpt now includes the modifier sections. The
statement above that C.3-C.7 are excluded and "unsupported" is also historical:
since `2026-09-09.1` the full vocabulary is offered.

The prompt states, and a test asserts each one:

1. it translates natural language into the V1 CAD specification;
2. it outputs only the allowed document structure;
3. it never outputs Python, CadQuery, OpenCascade, OpenSCAD, FreeCAD,
   FeatureScript, a script, a shell command, STL, STEP, IGES, mesh data or
   code in any language;
4. it invents no unsupported feature;
5. feature order follows the request's order;
6. it uses a default **only** where the specification defines one;
7. it asks when required geometry information is genuinely missing.
   *(Superseded by `2026-11-12.1`: it now assumes and lists the assumption. The
   tests that pinned items 4 and 7 were rewritten with the prompt.)*

It also tells the model it has no tools, that nothing it writes will be
executed, that a box's `position` is its **minimum corner** and a cylinder's
is the **centre of its base circle** ("do not treat either as a centroid"),
and that where the instructions and the specification appear to disagree,
**the specification wins**.

### Prompt regression

`PROMPT_VERSION = "2026-11-12.1"`, length 33 084 characters, and

```
prompt_fingerprint() = cf2291a5836b32fb117a0ef2a4d443a478d42872ca1fd63212056751b4250808
```

are pinned by tests (the Stage 26 values were `2026-09-08.1` /
`2b3e3395…`; `2026-09-09.2` was `f9efe19a…`). The fingerprint covers the instructions **and** the
specification sections the prompt quotes, so either changing makes the test
fail. When it does, the honest response is to bump the version and re-measure
model behaviour — not to assume old assumptions still hold. There is no prompt
version *system*: no registry, no migration, no stored history.

### Prompt 2026-09-09.2 — two gaps, measured on Claude Haiku 4.5

Both changes came from reproducing a real failing request five times against
the live provider, not from reading the prompt and guessing.

**No joining.** *"Create a simple desk stand using basic geometric primitives.
Use millimeters and make it a single solid. Choose reasonable dimensions
yourself."* produced `INVALID_MODEL_OUTPUT` **5/5**, in three shapes that look
like three bugs and are one:

| Rule | What the model did |
|---|---|
| **S9** ×2 | left three solids — `base`, `post`, `top-platform` — never joined |
| **S14** ×2 | emitted `"tools": []`, a `subtract` used as a pseudo-merge |
| **S6** ×1 | referenced a `fillet`'s id as though it named a solid |

The cause is that V1 has no union, fuse or join, and the instructions never
said so. The model assumed a join existed, could not find one, and improvised.
A specification describes what exists, so the appended excerpt could not state
the absence; the instructions now do, with the consequence: a part only
meaningful as two or more primitives joined into one body is `unsupported`.
Measured after: **`UNSUPPORTED` 5/5**, the model naming the missing operation
in its own words.

Note what was *not* wrong. "Choose reasonable dimensions yourself" was never
the problem — the model chose dimensions happily. The failure was topology,
not ambiguity.

**Locatives are answers, not gaps.** *"…with a 10 mm through hole on the top."*
returned a valid, buildable document **3/5** and a clarification request
**2/5** for byte-identical input. `through_hole.position` is required and has
no default, and nothing said whether "on the top" supplies it, so two existing
rules pulled opposite ways: *prefer asking over guessing, always* against
*never ask a question the request already answers*. The resolution is now
written down ("on the top" ⇒ centred on that face, with the arithmetic given),
so identical wording gets an identical answer. Measured after: **`GENERATED`
5/5**, every document valid, building to one solid of 285 643.805 mm³ at
120 × 80 × 30 mm.

**What was deliberately not added.** Restatements of S8, S11 or S14. The
rendered prompt was checked, and the derived specification excerpt already
states each in prose — `tools` MUST be a non-empty array (S14), the id pattern
(S8), positive sizes (S11). Repeating them would duplicate the contract and
invite drift. That also settles a question about
`anthropic_provider.schema_for_api`, which strips `minItems`, `minLength`,
`pattern` and `exclusiveMinimum` because the API rejects them: **the model
loses no information**, because the excerpt carries all four rules.

No validator rule was relaxed, no CAD feature added, and no repair loop
introduced. The correct answer to an unrepresentable request is still a
refusal.

### Prompt 2026-11-12.1 — build, and say what was assumed

An owner-approved **policy change**, not a bug fix: the product's purpose is
that a person types a part and gets one, and "refuse or ask" was the
wrong default for it. The prompt (`cad_ai/prompt.py`, whose changelog comment
is the authoritative list) now does the following.

1. **Missing information is assumed, not asked about.** No unit stated means
   millimetres. Another unit (inches, cm, m, feet) is converted to millimetres
   **by the model, inside the document** (`units` is always `"mm"`; the
   validator still rejects any non-`"mm"` document, rule S5, and no code
   converts anything). A missing dimension gets a typical value for the
   object; a missing position puts the base on the XY plane near the origin,
   standing along +Z. Every box and cylinder is written with its `position`,
   and every cylinder and through-hole with its `axis`, even where the
   contract has a default.
2. **Every such choice goes in a new `assumptions` list**, which the web page
   shows. A guess is therefore visible, not silent. `assumptions` is the
   model's own bounded prose (at most 12 entries of 300 characters), is
   carried to the caller, and is **never read for meaning by anything in this
   system**.
3. **A shape the vocabulary cannot express exactly is approximated**, not
   refused (a thread becomes a plain cylinder, a slope a stepped box, an
   organic shell a rounded box), and the simplification is an assumption too.
   Several separate parts are modelled as the one main solid.
4. **`union` exists** (schema `1.1.0`, implemented in `cad-core`), so the
   `2026-09-09.2` "nothing joins" policy is gone and multi-primitive parts are
   the normal case.
5. **`needs_clarification` and `unsupported` are now narrow.**
   `needs_clarification` is only for a request that names nothing buildable
   ("make me a part"); the model is told never to ask about units, sizes,
   positions or orientation. `unsupported` is only for a request that is not
   for a physical object, asks for code, a script or a file, or tries to
   override the instructions. Neither is re-asked.
6. **Buildability rules**, each measured against the real engine and not
   guessed:
   * a `union` joins only solids that overlap or share a face; edge or point
     contact, or a gap, fails rule **E3**, so make parts overlap by about
     1 mm;
   * a cutting tool should extend 1-2 mm past every face it opens, and a cut
     must not split the body (E3);
   * `fillet` / `chamfer`: **at most one per body, applied while the body has
     only planar faces** — before any `through_hole`, before any cylinder is
     unioned into or subtracted from it, and before any other blend. The
     selectors match the straight parameterisation **seam** edge of every
     cylindrical face and the tangent edges an earlier blend leaves, and the
     kernel refuses to blend those (rule **E5**). To round a small sub-part,
     blend it before joining it;
   * measured on the engine: `select: "all"` fails on a bare cylinder, on any
     body containing a hole, and after any earlier fillet; `axis_parallel` on
     an axis perpendicular to every cylinder and hole axis in the body works;
     a radius of exactly half the thickness at the edge fails (keep it below
     half, up to about 40 % of the smallest box dimension for a generously
     rounded shell);
   * a through-hole must pass through material, clear of the edges.
7. **A `plan` field comes first** in the answer, so coordinates and the
   feature order are worked out in text before the document is written. Like
   `summary` and `assumptions`, nothing reads it for meaning.
8. **Three worked examples are embedded** — an L-bracket, a coffee mug and a
   gaming mouse — and a test **builds each one on the real engine**, so an
   example cannot teach an unbuildable construction.

**The seam question is still open.** `docs/edge-selection.md` records the
unresolved cylinder-seam semantics, and this prompt *works around* it by
ordering and by forbidding the selections the kernel refuses; it does not
resolve it. The recommended next step is a spec change (C.7) that excludes
seams and tangent edges from the selectors, after which rules 4-6 above could
be relaxed. (Done in schema `1.1.1`; see *Prompt 2026-11-13.1* below.)

What did **not** change: the model emits a data document and never code, the
validator is the only authority on validity, anchor-point and axis semantics,
and order is meaning. No validator rule was relaxed.

**Findings that shaped the response schema** (live, on the real API):

* The first live call after adding `union` returned Anthropic
  `400: The compiled grammar is too large` (billed nothing). Seven feature
  branches plus optional properties exceeded the structured-output grammar
  limit. Fixed by grouping `subtract` and `union` — identical parameter tables
  — into **one** branch with `type` as an enum, and by making every document
  parameter required: **6 branches and 2 optional properties** (the edge
  selector's `axis`) remain. Grouping loses nothing, because `type` still
  names exactly one feature and the validator still checks every rule.
* The measured "`status: "document"` with the `document` field absent" failure
  is addressed by making **every response field required** (next section).
  That this removes the failure on the live model is not yet separately
  measured: the five live generations below all carried a document.

**Live evidence for this prompt, measured on Claude Haiku 5.5 through the real
service — product-flow evidence, not a benchmark.** Six live requests; the
first was the grammar 400 above, and the other five were:

| Request | Result |
|---|---|
| "a desk phone stand" | generated, accepted on attempt 1, 15.3 s; usage input 13, output 1767, cache_creation 16605 |
| "a 2 inch cube with a 10 mm hole through the center and rounded edges" | generated, attempt 1, 5.7 s; assumptions included "Converted 2 in to 50.8 mm"; cache_read 16605 |
| "a pipe flange with four bolt holes" | generated, attempt 1, 10.3 s (two cylinders, a union, a bore, four bolt holes) |
| "a hex nut for an M10 bolt" | generated, attempt 1, 7.9 s; approximated the hexagon as a cylinder and said so |
| "a pencil holder with three round slots", in the real browser page (Vite + uvicorn + Chromium) | generated, built and drawn; the page's own build reported "served from cache" |

Five generated parts, **5/5 accepted on the first attempt**. What this does
*not* show: no corpus run on this prompt exists; categories F (adversarial)
and G (semantic) remain unmeasured; and **the retry loop has not been
exercised live** — every live answer was accepted first time, so its behaviour
on a real rejection is covered by stub-provider tests only.


### Prompt 2026-11-13.1 — blending after the sharp-edge fix

Schema `1.1.1` made edge selectors match sharp edges only (seams and blend
boundaries are excluded; `docs/cad-specification.md` C.7). The 2026-11-12.1
rules had forbidden blending cylinders, drilled parts and blended parts
because those selections always failed; they now build, so rules 4–6 were
rewritten from new engine measurements: blend simple pieces before joining
them (a final `all` over a large joined body — a mug with cavity and handle,
an engine — still fails E5), a blend spreads along tangent edges, and `all` on
a cylinder means its rims. The prompt also says how to model a complex product
(an engine, a gearbox) as one solid of overlapping major components.

Live, Claude Haiku 5.5, first attempt each: "an engine" (block, crankcase,
head, oil pan, four bores, flywheel and pulley), "a coffee mug with a rounded
rim and a handle" (fillet on a cylinder), "a cylindrical knob with chamfered
edges and a 6 mm hole through it" (chamfer on a cylinder), and "a 4 cylinder
car engine" typed into the web page and drawn. Version `2026-11-13.1`,
fingerprint `90b4cb5054e25cf2…`, 35199 characters.
## The output contract

The model answers with one JSON object, constrained by a derived schema. The
fields, in **emission order** (`RESPONSE_FIELDS`), are:

```json
{
  "status": "document" | "needs_clarification" | "unsupported",
  "plan": "terse working notes, written before the document",
  "assumptions": ["each choice the request did not state"],
  "document": { /* a V1 CAD document */ } | null,
  "summary": "one sentence on how the request was read",
  "questions": ["..."],
  "issues": ["..."]
}
```

**Every field is required.** A field that does not apply is `null` or an empty
array/string rather than absent. That is deliberate: with optional fields the
model could answer `status: "document"` and never emit `document` — a measured
failure — and a schema-constrained decoder cannot skip a required field. The
document schema likewise requires every parameter (`position`, `axis` and
`description` included), which leaves only the edge selector's `axis` optional.
The feature schema has 6 branches, `subtract` and `union` sharing one (see the
grammar-size finding above); `optional_property_counts` measures this.

**The document is one field and the prose is others.** `summary` is carried
through to the caller and never read for meaning by anything in this system;
it is bounded to 500 characters, a non-string `summary` is dropped rather than
coerced, and a test asserts the summary is not part of the document and does
not change its hash.

The JSON Schema is **derived**, not written: every field name, enum and
default comes from `cad_core.model` — `SCHEMA_VERSION`, `SUPPORTED_UNITS`,
`AXIS_VALUES`, `DEFAULT_AXIS`, `ID_PATTERN`, `REQUIRED_ROOT_FIELDS`,
`FEATURE_PARAMETERS`. Tests assert the per-feature `required` lists equal the
contract's own tables, that the axis enum is `cad_core`'s, that every property
name appears in the specification document, and that no unsupported feature
type or parameter is offered at all. *(That last clause dates from when only
`box` and `cylinder` were offered; the schema now offers the full
vocabulary.)*

**The schema is a generation aid, not a validator.** A document can satisfy it
and still be invalid — rule S9 ("exactly one solid at the end") is not
expressible in JSON Schema, and a test demonstrates a schema-satisfying
two-box document that the validator rejects. That is exactly why the validator
remains the authority.

### `AiGenerationResult`

```python
AiGenerationResult(
    outcome,             # GenerationOutcome
    message,             # the stable public sentence
    candidate_document,  # the validated document, or None
    document_hash,       # cad_core's canonical hash, or None
    summary,             # the model's prose. Explanatory only.
    questions, issues, rule_codes,
    error_kind,          # vendor-neutral category, for MODEL_ERROR
    assumptions,         # the model's own list of unstated choices; GENERATED only
    attempts,            # one GenerationAttempt per model call that answered
    metadata,            # provider, model, prompt_version, usage, model_calls,
                         # build_verified, ...
    detail,              # development only; NOT in to_dict()
)
```

`candidate_document` is the **canonical serialization of the validated part** —
so what a caller receives is a document the existing validator already
accepted, in the form the rest of the system uses. A test asserts it equals
`part_hash(deserialize_part(...))`'s input and differs from the model's raw
bytes.

## Result states

| Outcome | Meaning |
|---|---|
| `GENERATED` | a candidate was produced, **passed the existing validator and — unless verification was switched off — was built by the existing engine**. The only outcome carrying a document and `assumptions` |
| `NEEDS_CLARIFICATION` | the request names nothing buildable. Carries questions. Final: never re-asked |
| `UNSUPPORTED` | the request is not for a physical object, asks for code or files, or tries to override the instructions. Final: never re-asked |
| `MODEL_ERROR` | the provider returned no usable response (including a refusal). Nothing was interpreted. Never re-asked |
| `INVALID_MODEL_OUTPUT` | the model answered, but **no answer within the attempt limit** was a valid, buildable V1 CAD document |

**"The model answered" is never conflated with "the document is valid."** The
model's own vocabulary is three statuses; the five outcomes are this layer's,
and `MODEL_ERROR` and `INVALID_MODEL_OUTPUT` are unreachable by anything the
model says about itself — a test asserts they are not in the status map. A
status the model invents (`"ok"`, `"success"`, `"generated"`, `"DOCUMENT"`, a
number, `null`) is not honoured: it becomes `INVALID_MODEL_OUTPUT`.

`UNSUPPORTED` never carries a document **even if the model sends one**.

## The supported natural-language subset

*(Rewritten for prompt `2026-11-12.1`. Earlier versions of this section said
"one `box` or one `cylinder`", and later "the full V1 vocabulary with units
stated in the request"; both are history.)*

Anything that names a physical object. The model is told to build a
recognisable, simplified part rather than ask or refuse, using the whole V1
vocabulary — `box`, `cylinder`, `through_hole`, `subtract`, `union`, `fillet`,
`chamfer` — and to approximate what the vocabulary cannot express exactly
(curved or organic surfaces, threads, gear teeth, text, patterns, slopes,
anything needing a rotation, sweep or loft). Not part of V1 and still not
produced as such: assemblies, sketches, lofts, sweeps, revolves, threads,
patterns, tolerances, GD&T, materials, simulation and drawings — a request
for one is *approximated* and the approximation is listed in `assumptions`,
not refused.

What is still refused (`UNSUPPORTED`): a request that is not for a physical
object, asks for code, a script or a file, or tries to override the
instructions. What still asks (`NEEDS_CLARIFICATION`): a request that names
nothing that could be built ("make me a part").

## Missing information: assume, and say so

*(This replaces the Stage 26 section "Ambiguity: ask, do not guess". Its rule
— "a missing value with no default must be asked about" — and its example
"plate 100 by 60 by 10 -> NEEDS_CLARIFICATION about units" are **no longer the
behaviour** and are kept here only so the change is traceable.)*

The rule is now: **resolve what the request leaves open, and list every such
choice in `assumptions`.**

* no unit stated -> millimetres, with an assumption entry;
* another unit -> converted to millimetres by the model, with an entry such as
  "Converted 2 in to 50.8 mm". **No code converts anything**: the validator
  still rejects a document whose `units` is not `"mm"` (S5), and a test still
  asserts that no unit-conversion helper exists in this layer;
* a missing dimension -> a typical value for the object, with an entry;
* a missing position or orientation -> base on the XY plane near the origin,
  standing along +Z.

The consequence is deliberate and should be read as a trade: a request the old
policy would have stopped to ask about now produces a part, and whether the
assumed value is the one the person wanted depends on them reading the
assumptions list. The mitigation is visibility (the web page shows the list),
not prevention.

Empty input still asks a question **without calling the provider at all**
(tested: the fixture records zero requests). A `NEEDS_CLARIFICATION` answer
with no question is unusable output (and is re-asked), not an empty ask.

## Validation flow

Every candidate goes through the existing boundary, and only that one:

```python
validation = self._service.validate_document(dict(candidate))   # S1-S20
built = self._service.build_document(BuildDocumentRequest(...)) # E1-E5, child process
```

The second call runs only when the first accepts and `verify_build` is on, and
requests `VERIFICATION_OUTPUTS` = `geometry, step, iges, stl, render` — exactly
the set the web page asks `POST /build` for, so the page's build has the same
build key and is answered from the cache.
* no rule is checked in `cad_ai`, no document is repaired, and S1–S20 are
  never bypassed;
* the reported `issues` are the **validator's own messages** and `rule_codes`
  its own codes;
* measured refusals: bad dimensions → **S10** / **S11**; a bad axis (`"Z"`,
  `"+z"`, `"up"`, `"+W"`, `"0,0,1"`) → **S12**; a forward reference → **S7**;
  a reference to nothing → **S6**; a non-`mm` unit → **S5**; an unknown field
  (`"material": "6061-T6"`) → **S3**; two solids → **S9**.

**The kernel now runs during generation, but not in this process.** Before the
verify-and-retry change, generation built nothing and a test proved it by
patching `build_part`, the exporters and `subprocess.Popen` to raise. That
property no longer holds by design: the verification build goes through
`CadApplicationService.build_document` and so runs in the existing isolated
child process, and its result is cached like any other build. `cad_ai` itself
still imports no geometry module and holds no kernel object. A caller that
wants the old behaviour constructs the service with `verify_build=False`
(which is what the evaluation harness does).

## Verified generation: re-ask, never repair

*(Replaces the Stage 26 section "No automatic repair" — "one generation
attempt, then validate, then return" — which is no longer true.)*

`generate_cad_from_text` makes at most **`CAD_AI_MAX_ATTEMPTS`** model calls
(default **3**, an integer >= 1; `DEFAULT_MAX_ATTEMPTS`). Each answer ends the
request or earns one more call:

| What happened | Result |
|---|---|
| the provider raises (outage, auth, timeout, rate limit, refusal) | `MODEL_ERROR` at once. **Never re-asked**: asking again does not fix an outage, and every call costs money |
| `needs_clarification` or `unsupported` | returned as it is. An answer, not a rejection |
| an unusable answer (not one JSON object, an unknown `status`, `"document"` with no document, a clarification with no question) | recorded at stage `answer`; re-asked |
| the validator rejects the document | recorded at stage `validation`, with rule codes; re-asked |
| the document validates but the engine cannot build it (E-rules) | recorded at stage `geometry`; re-asked |
| a build fails for a reason that is not geometry (crash, timeout, cache, exporter) | recorded at stage `geometry`, but **ends the loop**: no other document would fare better |
| validates and builds | `GENERATED` |
| attempts exhausted | `INVALID_MODEL_OUTPUT`, carrying the last attempt's issues and rule codes |

**The re-ask** (`compose_retry_text`) sends the **same system prompt,
byte for byte** — which is what lets the provider cache it — and a new user
text made of: the user's description verbatim and first; a fixed marker line
(`RETRY_MARKER`) saying that what follows is feedback and not a request; the
rejected document as JSON, when there was one; each reason with its rule code,
feature id and field path; and an instruction to return a **whole corrected
answer, not a patch**, and — if a feature cannot be made to build — to leave it
out and say so in `assumptions` rather than send it again unchanged. There is
no conversation state: the provider interface carries one system prompt and
one user text.

**This is not silent repair, and the project invariant stands.** No code
patches, defaults, trims or re-orders a document to make it pass; the only
thing that changes a rejected document is the model, asked again with the
reasons. The failure is not hidden either: every model call that returned an
answer is a public **`attempts`** record,

```json
{"number": 1, "accepted": false, "stage": "validation" | "geometry" | "answer" | null,
 "rule_codes": ["S9"], "issues": ["..."]}
```

(`stage` is `null` for the accepted attempt and for a final clarification or
refusal; `issues` hold the validator's or engine's own messages or fixed
sentences, never the model's raw text). A call that raised is not a record —
the result is then `MODEL_ERROR` with whatever earlier records exist — which is
why `metadata.model_calls` counts calls separately. Usage is **summed across
calls**, including the cache counters.

A re-ask is more cost and latency per request, up to the attempt limit, and an
answer that the loop eventually accepts is still only *valid and buildable*,
never verified as *the part the person meant* (A6 remains the standing
counterexample). The loop has not been exercised against a real rejection:
see the live-evidence note under *Prompt 2026-11-12.1*.

## Schema 1.2.0: tapers, polygons, patterns (prompt 2026-11-13.7)

The model can write a cone or taper (`top_diameter` on a cylinder), a regular
polygon prism (`sides` 3-12) and a pattern on `subtract`/`union`
(`pattern_count`, `pattern_axis`, `pattern_angle`, `pattern_step`): copy k of
each tool is turned k x angle about the axis line through the origin and moved
k x step along it. n around a circle is `count` n with `angle` 360/n; a single
turn is `count` 1; a row is a `step`. The prompt gives that arithmetic and a
worked flange with a 6-hole bolt circle and a hex nut.

**The response schema could not simply grow.** Measured live, the six-branch
schema plus any new branch -- or plus as few as four extra scalar fields --
is refused with `400: The compiled grammar is too large`, on Haiku 5.5 and
Sonnet 5.5 alike: it is the structured-output compiler's limit, not a model's.
So the new capabilities are parameters on existing features, and two things
were removed from what the *model* is offered to make room:

* **`through_hole`.** The model cuts every hole as a cylinder tool and a
  `subtract` (which a pattern turns into a bolt circle). `through_hole`
  remains valid V1 CAD: the specification, validator and engine accept it,
  and the prompt says it is not available rather than leaving it unexplained.
* **The nullable `document`.** It is always an object; when the status is not
  "document" the model writes a placeholder, which is never read.

Live, one call each and no retries: a hexacopter with six evenly spaced arms
(one arm patterned 6 x 60 degrees), an M10 nut as a true hexagon (19.6 mm
corner diameter for 17 mm across flats) and a six-spoke steering wheel with
no template (one tapered spoke patterned about Y) -- all generated, valid and
built on the first call.

## Reference designs (template library)

Since prompt `2026-11-13.6`, `cad_ai.templates` holds complete, verified
answers for common objects -- engine, electric motor, rocket, drone, toy car,
wheel, shaft coupling, pillow block, electronics enclosure, table. Each is
built by hand from parameters, validates, and builds as one solid; a test
holds all of that.

- **Selection is code, not the model.** `select_templates(description)`
  counts whole-word keyword matches, drops any template whose `excludes`
  words appear, and returns at most two, best first, ties in
  `TEMPLATE_MODULES` order. A request that names none gets none, and its
  user text is exactly the description.
- **The references ride in the user text**, after the description and a
  fixed marker, never in the system prompt -- so the system prompt, and its
  prompt cache, are the same for every request. A retry keeps them.
- **The model is told to follow a matching reference** and change only what
  the request asks for, and to list what it kept in `assumptions`. What it
  returns is validated and built like any other answer; a template is
  guidance, never a shortcut past the validator.
- **The benchmark switches them off** (`use_templates=False`) so a corpus
  score measures the prompt, not the library.

Live on Haiku 5.5, one call each: the engine (as Example 4 under prompt
`2026-11-13.5`), and under `2026-11-13.6` a six-arm hexacopter from the
four-arm drone, a three-fin rocket from the four-fin one, and a stepper motor
that took a smaller NEMA 17 form instead of copying the induction motor -- all
generated, valid and built on the first call. The remaining templates
(toy car, wheel, coupling, pillow block, enclosure, table) are verified on the
engine and in selection tests only.

## Prompt injection, code output, and tools

**The model gets no tools.** Not shell, Python, filesystem, HTTP, the CAD
kernel, Onshape, a browser, or arbitrary function calling. `ModelRequest` has
nowhere to put one — its fields are exactly `system`, `user_text`,
`output_schema`, `max_output_tokens` — and the provider never sends `tools`.

Twelve adversarial payloads are exercised (Python as the whole answer, a
CadQuery script, FeatureScript, `__import__('os').system(...)` in the document
field, `exec(...)` as a document string, a script inside a feature, a
`featurescript` feature type, a `{"tool": "bash", "command": "curl … | sh"}`
document, an imitation `tool_use` block, "IGNORE PREVIOUS INSTRUCTIONS" in the
summary, an eval expression as a dimension, and a `__class__`/`__args__`
document). For every one of them:

* it never becomes a CAD document — the outcome is `INVALID_MODEL_OUTPUT`, or
  (for the two whose CAD document is genuinely valid and whose code sat in a
  field outside it) `GENERATED` with the code **absent** from the result;
* `eval`, `exec` and `compile` are patched to raise for the whole generation,
  and `__import__` is watched: neither `os` nor `subprocess` is imported;
* `subprocess.Popen`, `subprocess.run` and `os.system` are patched to raise;
* `socket.socket` and `socket.create_connection` are patched to raise;
* the temporary tree and the system temp directory are snapshotted
  before and after: **nothing is written**.

Injection in the *user's* text is handled the same way: whatever the request
says, the layer still demands a CAD document, and prose comes back as unusable
output. The user's text is **passed to the model unmodified** — the layer
neither sanitises nor rewrites it.

Source-level checks: no module in `cad_ai` calls `eval`, `exec`, `compile`,
`system`, `popen`, `Popen`, `run`, `spawn`, `fork` or `execv`; none imports
`subprocess`, `pickle`, `marshal`, `shelve`, `socket`, `http`, `urllib`,
`requests`, `httpx`, `ctypes`, `importlib`, `runpy`, `shutil` or `tempfile`;
and none writes a file (`specification.py`'s `read_text` of the specification
document is the only filesystem access anywhere in the layer).

## Input normalization

There is none, on purpose. The description reaches the model as written —
tested for `mm`, inch, centimetre, unitless, unicode `×`, em dashes and
untrimmed whitespace. No unit conversion exists in this layer: a test asserts
`25.4`, `304.8`, `to_mm`, `convert_units` and friends appear nowhere. An
inch-denominated *document* from the model is **refused (S5), not converted**.
Since `2026-11-12.1` the *model* is told to convert a stated unit to
millimetres itself and to record the conversion in `assumptions`; the document
it sends must already say `"mm"`. The conversion is the model's arithmetic,
visible to the user, and nothing in this codebase performs it.

## Determinism

**Natural-language generation is not deterministic, and nothing here claims
it is.**

```
same prompt + same provider configuration  ≠  guaranteed identical model output
```

This is not a hedge but a measured limitation: the installed SDK's
`messages.create` accepts **no** `temperature`, `top_p`, `top_k` or `seed`
parameter, so deterministic decoding cannot even be requested at this
boundary. A test pins that observation, so if a future SDK adds one the test
fails and the claim gets re-examined. No live generation was repeated in this
environment (no credential), so no empirical statement about output stability
is made either way.

Below the document, everything stays deterministic, and the separation is
tested:

* the same candidate always yields the same `document_hash` and the same build
  key (four repetitions, one distinct value each);
* a generated document builds to the same build key every time — the first
  call a miss, the second a cache hit, with the same measured volume;
* two different model answers to the same request text give two different
  documents, which is the honest characterisation of the stochastic half.

## Observability

Recorded per generation: `provider`, `model`, `prompt_version`,
`structured_output`, `stop_reason`, token `usage`, `request_characters`,
**`model_calls`** (how many calls the request made, including one that
failed) and **`build_verified`** (whether an accepted document was built before
being returned) — plus the outcome, which is the generation *and* validation
verdict in one field, and the `attempts` list. `provider`, `model`,
`structured_output` and `stop_reason` describe the **last** call that
answered; `usage` and `model_calls` are totals over every call, and `usage`
includes the provider's cache counters when it reports them.

**Not** recorded: the user's description (only its length, which correlates a
result with a request without storing it), the system prompt, any credential,
any filesystem path. Tests assert a description containing `WIDGET-9` and an
email address appears nowhere in the result, and that the whole payload stays
under 4 kB. No telemetry platform, no metrics, no tracing; the layer imports
no logging module and calls no `print` or logger.

## Error handling

Public messages are five fixed sentences (`PUBLIC_MESSAGES`). Diagnostics live
in `AiGenerationResult.detail`, which is **excluded from `to_dict()`**.

Measured: a provider failure whose detail is
`"AuthenticationError: 401 from https://api.example/v1/messages x-api-key:
sk-secret-value request-id=req_123"` produces a public payload containing none
of `sk-secret`, `x-api-key`, `api.example`, `401`, `req_123` or
`AuthenticationError` — while `detail` keeps them for development. No API key,
provider header, raw provider diagnostic, filesystem path or Python traceback
reaches a caller. The provider translates its own exception types, so no vendor
exception crosses the boundary.

## HTTP

*(Replaces the Stage 26 section "HTTP: deliberately deferred", whose reasons —
the AI layer cannot live in `cad_core`, `create_app` needs only a cache root,
no credential exists to measure against — were resolved in Stage 30.)*

`POST /generate` exposes this layer (`docs/http-api.md`). `cad_api` still
imports no provider SDK and names no vendor; it asks `cad_ai.factory` which
provider is configured and passes `config.max_attempts` to the service. The
route **validates and builds** by default (see *Verified generation*), and
returns the document, `assumptions` and `attempts`; the client then posts the
document to the existing `POST /build`, which is a cache hit.

## Testing strategy

Three tiers, as the stage requires:

**A. provider tests** — the real `AnthropicTextToCadModel` against a fake SDK
client. No network, no credential. They assert the call shape, that
`output_config` uses the SDK's real parameter, that no `tools` and no
`temperature` are sent, that only text blocks become the answer, that SDK
errors translate to `ProviderError` with a safe message, that construction
without a credential is refused *before* a client is built, and that the
credential is handed to the SDK and retained nowhere.

**B. interpretation tests** — fixture model responses, deterministic. Every
payload is one **this test file wrote**, labelled as such. No live provider
result is fabricated anywhere.

**C. one live integration test** — `TestLiveProvider`. It runs only when
`ANTHROPIC_API_KEY` is already present; otherwise it **skips**, reporting
`"ANTHROPIC_API_KEY is not set: no live provider test was run"`. No credential
is written by the suite, and none is hard-coded.

In this environment **no credential was available, so the live test reported
itself skipped.** No claim is made here about real model behaviour on the four
example prompts: the documents in this file are fixtures, and the pipeline
below the model is what was measured.

## Real build results for a generated box

A generated document — from the fixture answer to "Create a rectangular plate
100 mm long, 60 mm wide, and 10 mm thick." — built through the existing
application service, in the existing isolated process:

```
document_hash  44bbce7ddf4aa0836ae7070cb04ed4882d7707b1e2de1f5a91e3b7162b5b3270
build_key      75723cbab28fa7a348f5e767bf01b393d1319de4a8f7e82504d06a2e675659d3
geometry       1 solid, is_solid=True, 6 faces, 12 edges, 8 vertices
               volume 60000.0 mm³ (nominal 100x60x10, within 1e-9 relative)
               bounding box size {x: 100.0, y: 60.0, z: 10.0}
render model   format 1.0.0, mm, right_handed_z_up, 24 vertices, 12 triangles
STEP           ISO-10303-21 … END-ISO-10303-21, checksum verified
STL            12 triangles, length == 84 + 50 x triangle_count
IGES           produced
```

A generated cylinder builds too: 1 solid, volume π·10²·50 within 1e-9
relative.

## Security limitations

**This is not production-ready, and nothing here should be read as claiming
otherwise.** Beyond the limits `docs/http-api.md` already records:

* **the model's output is untrusted input.** It is treated as such — parsed,
  never executed — but the guarantee rests on this layer never gaining an
  interpreter, a tool or a code path that runs model text. The tests are
  written to make such a regression fail.
* **prompt injection is not prevented, only contained.** A sufficiently
  persuasive description may make the model emit a document the user did not
  intend. The containment is that any document must still pass S1–S20 and
  that nothing is executed — not that the model cannot be steered.
* **no cost, rate or abuse control.** Every call is a real paid API call with
  no per-caller quota, no budget cap and no request throttle. There is no
  authentication above this layer, so an exposed deployment would let anyone
  spend the credential.
* **the description is sent to a third-party API.** That is inherent to the
  design and should be disclosed to users; this stage adds no redaction, no
  data-residency control and no opt-out.
* **the credential is read from the process environment** and is only as
  protected as that environment. It is never written to disk, logged or put
  in a result by this layer.
* **no output-size or content policy** beyond `max_tokens` and the schema.

## Limitations and hallucination risks

* **Model quality is largely unmeasured.** The only evidence for prompt
  `2026-11-12.1` is five hand-run live requests (5/5 accepted on the first
  attempt on Claude Haiku 5.5). That is product-flow evidence, not a
  benchmark: no corpus run exists on this prompt, categories F and G are
  unmeasured on any provider, and the retry loop has never been exercised live.
* **The policy trades correctness for completeness.** Because the model
  assumes rather than asks, a request that was ambiguous yields a plausible
  part with guessed dimensions. The assumptions list makes that visible; it
  does not make the guess right.
* **A plausible wrong document is the dangerous failure mode.** A model that
  reads "100 × 60 × 10" as `{x:60, y:100, z:10}`, or a cylinder's `position`
  as its centroid rather than its base-circle centre, produces a *valid*
  document that is the wrong part. S1–S20 cannot catch that, and this stage
  adds no semantic cross-check. The prompt states both anchor points
  explicitly for exactly this reason.
* **The vocabulary is still small.** Six feature types plus `union`'s
  grouping: no sketches, revolves, patterns or assemblies. Shapes beyond it are
  *approximated* (and say so), so a hex nut is a cylinder.
* **Blend rules are a workaround.** As of prompt 2026-11-12.1, fillet/chamfer
  was limited to one per body, on planar-faced bodies, because of the then
  unresolved cylinder-seam semantics (`docs/edge-selection.md`; resolved by
  schema `1.1.1`). Some parts a person expects ("rounded, with a
  hole") are only expressible in one order, and some cannot be rounded at all.
* **A bounded retry, not a guarantee.** Up to `CAD_AI_MAX_ATTEMPTS` calls, then
  `INVALID_MODEL_OUTPUT`. Re-asks cost latency and money, and the model can
  fail the same way twice. A request can also exhaust the attempt limit and
  return nothing.
* **No conversational editing, no image or drawing input, no MCP, no
  Onshape, no RAG, no agent framework, no tool calling** — none of it is
  implemented, and none is implied.
