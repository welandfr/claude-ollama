# claude-ollama

Run Claude Code against a model on your own Ollama server.

Ollama serves the Anthropic Messages API itself, at `/v1/messages`, so Claude Code
can talk to it directly: point `ANTHROPIC_BASE_URL` at Ollama and it works. What
this repo adds is everything around that one variable that is easy to get
wrong — a launcher that sets the right environment, startup checks that catch the
quiet failures, a `/model` picker of your Ollama models, a status line, and
editor integration.

Until 15 October 2026 this repo was a translating proxy between Claude Code and
Ollama's native API. It was removed once Ollama's own endpoint covered the same
ground; the [design record](#design-record) has why it existed and why it went.

The Ollama server itself is configured in the sibling `ollama-docker-api`
project. One of its settings has to agree with this one — see
[The Ollama side](#the-ollama-side).

## Requirements

- **Linux or macOS.** The scripts (`setup.sh`, `claude-ollama`,
  `vscode-setup.sh`) are bash, not POSIX sh. Developed and tested on Linux x64;
  macOS is untested.
- `bash`, `curl` and `python3` (3.9+) on PATH. Only the standard library is used;
  nothing is installed.
- An Ollama server you can reach, recent enough to serve `/v1/messages`, with a
  model that supports tool calling
- Claude Code on your PATH: `curl -fsSL https://claude.ai/install.sh | bash`

### Windows

Not supported yet: the launcher, `setup.sh` and `vscode-setup.sh` would need
PowerShell equivalents. The Python modules have no OS-specific code.

WSL2 works today with no changes — it is Linux as far as these scripts are
concerned. Ollama running on the Windows host is reachable from WSL2 over the
host's address rather than `localhost`, so `OLLAMA_BASE_URL` has to point there.

## Setup

```sh
./setup.sh
```

`setup.sh` creates `.env` from `.env.example` if you do not have one, symlinks
`claude-ollama` onto your PATH, and reports which `claude` binary will be used
and whether Ollama answers at the configured URL (listing its models). It also
names any `.env` keys left over from the proxy era, and a `.venv` that is no
longer needed.

The symlink goes to `~/.local/bin/claude-ollama` by default; set `BIN_DIR` to put
it elsewhere (`BIN_DIR=~/bin ./setup.sh`). If a real file already sits at that
name, setup says so and leaves it alone. Setup also tells you if the directory is
not on your PATH.

Then, from any project:

```sh
claude-ollama
```

Arguments are passed through to `claude`.

### Startup checks

Before handing over to Claude Code, the launcher checks for the failures that
otherwise surface as a confusing session rather than an error, and refuses to
start with the exact fix:

- Ollama not reachable at `OLLAMA_BASE_URL`
- a configured model not pulled (with the `ollama pull` command aimed at the right
  machine — `OLLAMA_HOST=…` prefixed when Ollama is remote)
- project settings written by an older `.env` — see
  [Editor integration](#editor-integration-vs-code-jetbrains)
- Ollama's context window smaller than `CLAUDE_OLLAMA_NUM_CTX` — see
  [The Ollama side](#the-ollama-side)

`CLAUDE_OLLAMA_SKIP_CHECKS=1` launches anyway.

## The Ollama side

**Ollama's context window is set on the server, not by this repo.**
`/v1/messages` gives a client no way to choose it, so every request gets the
server's default: `OLLAMA_CONTEXT_LENGTH` in Ollama's environment. That has to be
at least `CLAUDE_OLLAMA_NUM_CTX`, which is what Claude Code is told and compacts
against. If the server is smaller, Claude Code lets the conversation grow past
what Ollama keeps, and Ollama drops the overflow without an error.

The launcher compares the two through Ollama's `/api/ps` whenever a model is
loaded, and refuses to start on a mismatch:

```
Ollama has qwen3.5:9b loaded with a 4096-token context, but CLAUDE_OLLAMA_NUM_CTX is 65536.
  Claude Code would let the conversation outgrow it. Set
  OLLAMA_CONTEXT_LENGTH=65536 on the Ollama server, or lower
  CLAUDE_OLLAMA_NUM_CTX in .env to 4096.
```

An idle server reports no window, so that check cannot run before the first
model load. `ollama ps` on the Ollama machine shows the same figure in its
`CONTEXT` column.

The `ollama-docker-api` project sets what a 16GB card needs for 65536:

```sh
OLLAMA_CONTEXT_LENGTH=65536
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0
```

See [Context size](#context-size) for why 65536 and why the KV quantization.

## Editor integration (VS Code, JetBrains)

An editor extension finds `claude` on your PATH and runs it directly, so it never
gets the environment the launcher sets — Claude Code sees no credentials and
shows a login screen. Give the project that environment instead:

```sh
cd ~/some/project
/path/to/claude-ollama/vscode-setup.sh
```

That writes `.claude/settings.local.json` in that project with an `env` block
pointing Claude Code at Ollama. Claude Code reads `env` from settings regardless
of what launched it, so the extension picks it up. Existing settings in the file
are merged, not replaced, and a malformed file is refused rather than
overwritten.

Values are generated from `.env` through the same loader the launcher uses, so
`.env` stays the single place settings are edited — re-run the script after
changing it. The file is a snapshot, not a live reference, so `claude-ollama`
checks it against `.env` on startup and tells you when it has gone stale rather
than letting the old values quietly win. **Projects set up while this repo was a
proxy point at `http://127.0.0.1:8765`, which nothing serves any more**; the check
names exactly that, and re-running the script fixes it.

Add `.claude/settings.local.json` to that project's `.gitignore`; the script says
so if it is not already ignored. Pass `--shared` to write `.claude/settings.json`
instead, if you want the setting committed for a project that is only ever used
this way.

### Status line

`statusline.py` shows the model and context usage:

```
ornith-1.5:9b | Context: 18K/64K (28%)
```

Claude Code pipes session JSON to it and renders one line; the numbers come from
`context_window.total_input_tokens` and `context_window.context_window_size`.

It applies two ways, because the two launch routes need different mechanisms:

- **Every `claude-ollama` session, in any directory.** The launcher passes it as
  `claude --settings '{"statusLine": …}'`, so nothing is installed per project
  and your own `~/.claude/settings.json` is untouched. Flag settings outrank
  project ones, so this wins wherever you launch.
  `CLAUDE_OLLAMA_NO_STATUSLINE=1` keeps whatever status line you already have.
- **Editor sessions**, which never run the launcher, get it from the project
  settings `vscode-setup.sh` writes — and only if that file has no `statusLine`
  of its own, since one you wrote is yours to keep.

To have it in sessions that use neither route, put it in `~/.claude/settings.json`
yourself. The script is model-agnostic, so it works for a normal Claude session
too — but it would replace the status line you already have there.

```json
"statusLine": { "type": "command", "command": "/path/to/claude-ollama/statusline.py" }
```

The window it reports is worth watching. It comes from
`CLAUDE_CODE_MAX_CONTEXT_TOKENS`, so **a window of 200K means that variable is
not reaching Claude Code** and it has fallen back to its assumption for an
unrecognised model — which is the condition that silently overflows Ollama.

### Why per-project rather than globally

Putting this in `~/.claude/settings.json` would work everywhere, including
projects where you want the real Claude. Per-project keeps it opt-in, so a normal
`claude` elsewhere is unaffected.

## Configuration

One file, `.env`, and one rule: **a variable already set in your shell wins, then
`.env`, then the built-in default.** Nothing else is read — there is no second
config file and no other format.

`.env` is gitignored and holds your settings; `.env.example` is the committed
template that documents every key. `setup.sh` copies one to the other on first
run. Because the real environment takes precedence, a one-off change needs no
edit at all:

```sh
OLLAMA_BASE_URL=http://gpubox.lan:11434 claude-ollama
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens; becomes `ANTHROPIC_BASE_URL` |
| `CLAUDE_OLLAMA_TIMEOUT` | `600` | Seconds before Claude Code gives up on a request; becomes `API_TIMEOUT_MS` |
| `CLAUDE_OLLAMA_MODEL` | `ornith-1.5:9b` | Model a session starts on |
| `CLAUDE_OLLAMA_SMALL_MODEL` | unset | Model for Claude Code's cheap background calls; defaults to the main one |
| `CLAUDE_OLLAMA_MODELS` | unset | More models to offer in `/model`, comma or space separated |
| `CLAUDE_OLLAMA_NUM_CTX` | `65536` | Window Claude Code is told about; the server's must be at least this |
| `CLAUDE_OLLAMA_MAX_OUTPUT` | `8192` | Longest single reply; sets where Claude Code auto-compacts (see below) |

`CLAUDE_OLLAMA_ENV` can point the loader at a different file, if you want to keep
several and switch between them.

These are translated into Claude Code's own variables — `ANTHROPIC_MODEL`, the
`ANTHROPIC_DEFAULT_*_MODEL` aliases, `CLAUDE_CODE_MAX_CONTEXT_TOKENS`,
`CLAUDE_CODE_MAX_OUTPUT_TOKENS` and so on. Set the `.env` keys, not those: the
launcher overwrites them.

### Switching models with `/model`

List the models you want to switch between:

```sh
CLAUDE_OLLAMA_MODEL=ornith-1.5:9b
CLAUDE_OLLAMA_MODELS=qwen3.5:9b,qwen3:8b
```

`/model` then offers exactly those, in place of Claude's own lineup:

```
1. Default (recommended)   Use the default model (currently ornith-1.5:9b)
2. ornith-1.5:9b           CLAUDE_OLLAMA_MODEL
3. qwen3.5:9b              CLAUDE_OLLAMA_MODELS
4. qwen3:8b                CLAUDE_OLLAMA_MODELS
```

The launcher passes these rows as `modelPicker` in its `--settings`, and the
startup check verifies every listed model is pulled. The picked name goes to
Ollama verbatim. Things to know:

- **Every session starts on `CLAUDE_OLLAMA_MODEL`.** The launcher exports it as
  `ANTHROPIC_MODEL`, which outranks a saved `/model` choice.
- **A pick does not leak into plain `claude`.** Enter in the picker saves the
  choice to `~/.claude/settings.json` ("set as default"), where a normal Claude
  session would then ask Anthropic for `qwen3.5:9b`; picking Default deletes your
  saved model instead. The launcher records that value before starting and puts
  it back on exit, printing `restored the model in …` when it does. Pressing `s`
  instead of Enter ("this session only") avoids the write altogether. Only a
  value naming one of the `.env` models, or a deleted one, is restored; it
  cannot help if the session is killed rather than exited.
- **All models share one window** — the server's `OLLAMA_CONTEXT_LENGTH` and
  `CLAUDE_OLLAMA_NUM_CTX` — so size it for the largest.
- **Switching unloads the old model** when the server runs
  `OLLAMA_MAX_LOADED_MODELS=1` (as `ollama-docker-api` does on 16GB), so the first
  reply after a switch includes the load time.
- **Launcher only.** Claude Code ignores `modelPicker` in a project's `.claude/`,
  so `vscode-setup.sh` cannot carry it. In the editor, `/model` still shows
  Claude's lineup; Haiku maps to `CLAUDE_OLLAMA_SMALL_MODEL` and the rest to
  `CLAUDE_OLLAMA_MODEL`.

`CLAUDE_CODE_DISABLE_1M_CONTEXT=1` is set alongside: without it the Default row
resolves to `ornith-1.5:9b[1m]` and Claude Code switches to a 1M window, past
`CLAUDE_OLLAMA_NUM_CTX` — the silent-truncation case described next.

### Context size

Ollama defaults to a small context and silently truncates anything longer.
Claude Code's system prompt and tool schemas are roughly 12–15k tokens *before*
your conversation starts, so at a small default you would lose the tool
definitions and the agent would flail. The window is therefore 65536.

**32768 is not enough either**, which is less obvious. It is large enough to hold
the system prompt and tool schemas, so the agent starts working — but those take
roughly 15k of it, and the ~17k left over is refilled by a single large `Read` or
a verbose command. Claude Code compacts, refills, compacts again, and eventually
reports `Autocompact is thrashing`. Ollama's own documentation puts the floor for
coding tools at 64k, which matches.

Claude Code is told the window via `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, which matters
more than it looks. It has no catalog entry for an Ollama model name, so it
assumes a 200k window and auto-compacts at *that* — happily growing the
conversation to several times what Ollama accepts, which Ollama then truncates
without saying so. The launcher and `vscode-setup.sh` both derive this from
`CLAUDE_OLLAMA_NUM_CTX`.

You will still see a warning that the model `isn't described by this version's
model catalog`. That part is unavoidable for a custom model name and is harmless
once the window is set explicitly.

The upper bound is your VRAM, not the model: `qwen3.5` is 256K-native, but the
window allocates KV cache whether the conversation fills it or not. Raise it in
steps and watch for a sudden slowdown, which is Ollama spilling layers to CPU.

Sizing, for a 9B model at Q4_K_M (about 6.5GB of weights): the KV cache for a GQA
model of that class runs roughly 0.1MB per token at f16, so 64k costs about 8GB —
which with the weights exceeds a 16GB card. KV quantization on the server roughly
halves that, for a small quality loss, and brings 65536 within reach
(`OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`). Hybrid architectures need
far less: the qwen35-family models keep full attention in only one layer in
four, and Ollama's log shows a 1088 MiB KV buffer for `qwen3.5:9b` at 65536 with
q8_0.

Verify rather than trust the arithmetic — `ollama ps` on the Ollama machine
prints the allocated `CONTEXT` and the `PROCESSOR` split, which must read
`100% GPU`. Any CPU split means the window is too large for the card, and the
slowdown is severe rather than fatal, so it is easy to miss.

### When auto-compaction fires

Claude Code does not compact near the end of the window. It reserves room for
the longest reply it might request, capped at 20000 tokens, plus a fixed 13000,
and compacts once the conversation reaches what is left:

```
threshold = window - min(max output tokens, 20000) - 13000
```

Those reserves are sized for 200K windows. Its default output cap is 32000, so on
a 64K window it compacted at **32,536 tokens — half the window**, with the
conversation then rebuilt from a summary the local model writes. That summary is
where earlier context gets lost, so the fewer compactions the better.

`CLAUDE_OLLAMA_MAX_OUTPUT` (default 8192) is exported as
`CLAUDE_CODE_MAX_OUTPUT_TOKENS`, which moves the threshold to 44,344 (68%).
Measured with `claude --debug-file`, which logs the effective window:

| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | `max_tokens` sent | effective window | compacts at |
| --- | --- | --- | --- |
| unset (32000) | 32000 | 45,536 | 32,536 |
| 8192 | 8192 | 57,344 | 44,344 |

The reserve is not waste: Ollama's window has to hold the prompt *and* the reply,
so the threshold plus `CLAUDE_OLLAMA_MAX_OUTPUT` must stay under it. Going lower
than 8192 compacts later still, but cuts off any reply longer than that —
including a file written in a single tool call (8192 tokens is roughly 30KB of
code). The other lever is a larger window, if the card has room.

## Network isolation

Two separate questions, often conflated.

**Where does conversation content go?** `ANTHROPIC_BASE_URL` decides, and nothing
else. Prompts, file contents, tool output and model replies go to Ollama and
nowhere else.

**What else does Claude Code talk to?** That is independent of the base URL, and
pointing at Ollama does not change it. Claude Code recognises these, all of which
the launcher and `vscode-setup.sh` set:

| Variable | What it stops |
| --- | --- |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | the umbrella: non-essential calls to Anthropic |
| `DISABLE_TELEMETRY=1` | usage telemetry |
| `DISABLE_ERROR_REPORTING=1` | crash and error reports |
| `DISABLE_AUTOUPDATER=1` | update checks against Anthropic's download service |
| `ENABLE_CLAUDEAI_MCP_SERVERS=false` | claude.ai connectors, which run through Anthropic |

The last one changes no behaviour on its own: with a non-Anthropic auth token,
Claude Code already skips your claude.ai connectors. But on a machine logged in
to claude.ai it then warns, every session, that "claude.ai connectors are
disabled because ANTHROPIC_API_KEY or another auth source is set". Opting out
explicitly takes the quiet path — its debug log reads `Disabled via env var`
instead of `Disabled: API-key auth precedence active`.

Ollama's own Claude Code documentation does not mention any of these, so
`ollama launch claude` leaves them at their defaults — conversation content is
local, the side channels are not. That is the main thing this launcher adds over
it.

Two caveats worth stating rather than implying. `DISABLE_AUTOUPDATER=1` means
sessions launched this way never self-update — a plain `claude` elsewhere still
does, so the binary is not frozen, only this path. And OpenTelemetry export is
opt-in via `CLAUDE_CODE_ENABLE_TELEMETRY` and goes to an endpoint you configure,
so it is not a leak unless you make one.

Prompt caching is deliberately left *enabled*: `DISABLE_PROMPT_CACHING=1` only
produces a standing warning that requests "will be slower and cost more", which
is untrue of a local model.

This is not an audited claim. It is what the documented switches cover; verifying
it properly means watching the process's own traffic.

## Watching what happens

There is no request log on this side any more; Ollama's server log is the record.
Each request appears as an access line with its status and duration, and the
runner logs the prompt size and generation speed:

```sh
docker logs -f <ollama container> 2>&1 | grep -E 'GIN|n_tokens =|eval time'
```

```
slot   operator(): id  0 | task 33138 | new prompt, n_ctx_slot = 65536, n_keep = 4, task.n_tokens = 36890
slot print_timing: id  0 | task 33138 |        eval time =      68.40 ms /     7 tokens (   11.40 ms per token,    87.71 tokens per second)
[GIN] 2026/10/15 - 07:48:47 | 200 |  947.431603ms |      172.20.0.1 | POST     "/api/chat"
```

(Captured while the proxy was still in place, hence `/api/chat`; requests from
Claude Code now show as `/v1/messages`.)

`n_ctx_slot` is the window Ollama is really using and `task.n_tokens` the size of
the prompt it received — compare it with `CLAUDE_OLLAMA_NUM_CTX`. Ollama's log is
in UTC; correct for your clocks before lining it up with anything else.

For Claude Code's side, `claude-ollama --debug-file /tmp/cc.log` logs its own
decisions, including the effective context window it compacts against.

## What to expect from small models

Agentic coding leans hard on reliable tool calling, and that is where small models
break down first: they skip required arguments, invent tool names, or narrate a
tool call as prose instead of emitting one. The last shows up as a turn that ends
(`Brewed for 45s · done`) with nothing changed — the model described the edit
rather than making it, so Claude Code had nothing to execute. Retrying, or a
larger tool-calling model, is the only fix.

An 8B model is enough to confirm the plumbing works and to handle short,
single-step requests. Expect to go to a larger tool-calling model before
multi-step editing feels usable.

## Tests

```sh
python3 -m pytest
```

`pytest` is the only dependency, and only for the tests. The suite needs no
server: it covers `.env` loading, the startup checks (including the server-window
comparison and stale project settings), the settings `vscode-setup.sh` writes,
the `/model` picker and the restoring of a saved model, and the status line.

## Design record

Notes for the research write-up: what was built, which decisions were forced
rather than chosen, and what is actually established versus assumed. Claims are
labelled so nothing unverified gets cited as a result.

Two phases: a translating proxy, built 2 – 3 October 2026 and run until
14 October; and from 15 October, Claude Code pointed at Ollama directly with the
proxy removed. Test references in the proxy-era sections are to the suite as it
stood at commit `84859c2`, the last with the proxy.

### The constraint that shapes everything

Claude Code is proprietary and distributed only as a compiled artefact.
`LICENSE.md` in `anthropics/claude-code` reads "© Anthropic PBC. All rights
reserved. Use is subject to Anthropic's Commercial Terms of Service"; GitHub
reports no SPDX licence for the repository, and the repository contains no CLI
source — only changelog, examples, plugins and tooling. The distributed Linux
binary is a 240MB ELF executable: a Bun single-file executable (Bun 1.4.3)
wrapping bundled, minified JavaScript. *(Verified: licence text fetched, repo
contents listed, ELF header and Bun version strings read from the binary.)*

The client therefore cannot be modified to speak any other protocol. The only
intervention point is the network boundary, and `ANTHROPIC_BASE_URL` is a
documented configuration point. **Whatever serves that URL must speak the
Anthropic Messages API** — in phase one a proxy of our own, in phase two Ollama
itself.

### Phase one: why a proxy, and why Ollama's native API

When the project started, the obvious Ollama endpoint for a foreign client was
the OpenAI-shaped `/v1/chat/completions`, and Claude Code cannot speak that. So
the proxy translated Anthropic requests into Ollama's own `/api/chat`.

It used the native endpoint rather than the OpenAI-compatible one because only
the native one accepts `options.num_ctx` per request. That single parameter
decides whether the system works at all. Ollama's default context is small and
it truncates silently past it, while Claude Code's system prompt plus its tool
schemas occupy a large fraction of that before any conversation begins.
Truncation removes the tool definitions, after which the model cannot emit valid
tool calls and the agent fails in a way that looks like model incompetence rather
than a configuration fault. The native endpoint also exposes `think` and returns
reasoning in a separate field. *(Verified: `num_ctx` reached Ollama, asserted in
the proxy-era `tests/test_app.py`.)*

### Phase one: protocol impedance mismatches

Three were structural rather than cosmetic:

1. **Tool-call identity.** Anthropic correlates a `tool_use` block with its
   `tool_result` by an explicit `tool_use_id`. Ollama's native API gives tool
   calls no identifier at all. The proxy synthesised `toolu_*` ids outbound and
   matched results back **by tool name**, carrying a name map across the
   conversation. Two concurrent calls to the *same* tool in one turn could only be
   distinguished by order. This is an information loss in the protocol, not an
   implementation shortcut.
2. **Tool results live in different turns.** Anthropic puts `tool_result` blocks
   inside the *user* message; Ollama expects separate `tool`-role messages. The
   converter flushed pending user text before each result so relative ordering
   survived.
3. **Streaming shape.** Anthropic sends typed SSE events (`message_start`,
   per-block `content_block_start`/`delta`/`stop`, `message_delta`,
   `message_stop`); Ollama sends NDJSON lines. Content blocks were opened lazily
   because Ollama does not announce what is coming. Tool arguments arrive as a
   complete object rather than a token stream, so each `tool_use` block was
   emitted as a single `input_json_delta`.

Mechanical mappings: `system` (string or blocks) to a leading system message;
`input_schema` to `function.parameters`; base64 `image` blocks to `images[]`;
`max_tokens`/`stop_sequences` to `options.num_predict`/`options.stop`;
`done_reason` to `stop_reason` (`length` to `max_tokens`, presence of tool calls
to `tool_use`). `cache_control` was dropped. *(All verified in the proxy-era test
suite.)*

### Phase two: removing the proxy

Ollama now serves `/v1/messages` itself, translating to its runner internally —
the same job the proxy did, done by the server. The proxy was kept while it had
one thing the native endpoint lacked: a per-request `num_ctx`. That advantage
disappeared once the window was set server-side instead
(`OLLAMA_CONTEXT_LENGTH=65536` in `ollama-docker-api`), which every request then
gets regardless of client.

What decided it was a failure in the proxy itself. On 15 October, sessions began
hanging for long periods with the model unloaded from VRAM. The evidence placed
the stall inside the proxy, not in Ollama or the network:

- Ollama's access log, as captured, showed its last request completing normally
  (`200`, 2.7s) and **no further request in the following hour**, while the
  proxy logged Claude Code retrying the same 71-message turn at 10-minute intervals (its
  `API_TIMEOUT_MS`), then falling back to non-streaming requests. None reached
  Ollama. The model unloaded after `OLLAMA_KEEP_ALIVE` (5 minutes) of genuine
  idleness — a symptom, not the cause.
- During a recurrence, from the same VM: `GET /api/ps` straight to Ollama answered
  at once (`{"models":[]}`), while the proxy's own `/health` did not answer within
  5 seconds.
- A watchdog added the day before — end the turn if the model unloads while a
  request waits — never fired, consistent with requests that never left the
  proxy.

The root cause inside the proxy was **not diagnosed** before removal; candidates
were a wedged HTTP connection pool (its pool timeout equalled the 600-second
request timeout) or a stalled event loop. Removing the component was cheaper
than diagnosing it, and removed a whole class of failure: there is no longer a
process between Claude Code and Ollama that can stall.

What phase two gave up, deliberately: per-request logging (Ollama's own log
replaces it), the watchdog, temperature and `think` control, and stripping of
leaked `<think>` spans. What it kept: everything that configures Claude Code
rather than the wire — the window and compaction settings, the `/model` picker,
the startup checks, isolation switches and editor integration. A new check
replaced the proxy's `num_ctx`: the launcher compares the server's window (from
`/api/ps`) with the one Claude Code is told.

### Findings worth reporting

**Local inference breaks an assumption built into cloud API clients.** *(Phase
one.)* Claude Code treats a silent connection as a failed one: it displays
`Waiting for API response · will retry in 2m 59s · check your network` and then
retries. Against a cloud endpoint, time-to-first-byte is short and this never
fires. Against local inference, loading a cold model into VRAM and evaluating a
long prompt produces minutes of silence before the first token, and the client's
retry issues a *second* generation to a GPU already busy with the first, while
abandoning the original response — degrading throughput precisely when the
hardware is most loaded.

The proxy's first implementation inherited the fault by emitting `message_start`
only after Ollama returned response headers. Reproduced with a stub that delays
its first token by 12 seconds: no bytes whatsoever for 12s, then all seven events
at once. The fix sent `message_start` before opening the upstream request and a
`ping` every 15 seconds through any silence — the same keepalive discipline the
real Anthropic API uses. *(Verified: both the failure and the fix reproduced by
timing SSE events.)* Whether Ollama's own `/v1/messages` keeps the connection
alive the same way is **not yet verified** — see Open questions.

**Reasoning-token leakage straddles chunk boundaries.** *(Phase one.)* Reasoning
models emit `<think>…</think>` inside ordinary content even when thinking is
nominally disabled, and the tags split across streamed fragments. Naive per-chunk
filtering fails; the filter held back any tail that could still become a tag, and
emitted a dangling partial as literal text once the stream ended. *(Verified by
parametrised tests including split-tag cases.)*

**Capability negotiation is absent, so failure must be recovered from.** *(Phase
one.)* Models that do not support thinking reject the `think` field with HTTP
400. The proxy retried once without it, invisibly: because the message id in
`message_start` was generated locally, the second attempt continued the same
logical response. *(Verified for both streaming and non-streaming paths.)*

**Prompt-caching semantics do not transfer, and suppressing them misleads.**
Setting `DISABLE_PROMPT_CACHING=1` produces a standing warning that requests
"will be slower and cost more" — untrue of a local model with no per-token
billing. Caching is left enabled.

**The client's context window has more than one source, and each must be
pinned.** `CLAUDE_CODE_MAX_CONTEXT_TOKENS` fixes the window Claude Code assumes
for an unknown model, but the `/model` picker's Default row independently
resolves to a `[1m]` variant with a 1M window, which only
`CLAUDE_CODE_DISABLE_1M_CONTEXT` prevents. Either path, left open, lets the
conversation grow past what Ollama keeps. In phase two there is a third: the
server's own window, which the client can neither set nor see except through
`/api/ps`. Found by driving the picker against a stub server and reading the
status line, not from documentation.

**Compaction reserves are absolute, not proportional, so they halve a small
window.** Claude Code compacts at `window − min(max output, 20000) − 13000`. On
a 200K window that is 84%; on a 64K one, with its default 32000-token output cap,
it is 50% (32,536 tokens, measured from its debug log and matching a
`prompt=33559` → `20541` drop in the proxy's log). Each compaction swaps the
history for a summary written by the local model, so an early threshold costs
context as well as time. Capping output at 8192 moves it to 68%.

**A component that only translates is a liability once the server translates
too.** *(Phase two.)* As far as the evidence goes, the proxy's failure was not in
translation, which its tests covered well, but in the plumbing every
long-running network intermediary needs — connection handling under retries and
disconnects — which no test exercised. *(Inference: the root cause was not
diagnosed.)*
Its removal is the clearest result of the project for practitioners: prefer the
server's native compatibility endpoint once it exists, and keep only the
client-side configuration that the server cannot provide.

**Hypothesis, not a result:** for agentic coding the binding constraint is
tool-calling reliability across long multi-turn contexts, not code-generation
quality, so a code-specialised model (`qwen2.5-coder:7b`) is not automatically
preferable to a newer general model trained for agentic use (`qwen3:8b`, since
superseded by `qwen3.5:9b`). Both declare `tools` support in Ollama's library;
`qwen3` additionally declares `thinking`. *(Capability tags verified; the
comparative claim is untested and stated as a hypothesis.)*

### Open questions

- **Keepalive on the native endpoint.** Phase one showed Claude Code retrying
  requests that stay silent too long. Whether Ollama's `/v1/messages` sends its
  first event before a cold load and prompt evaluation finish is unverified.
  `CLAUDE_OLLAMA_TIMEOUT` (600s) is the ceiling either way; watch for
  `Waiting for API response · will retry` on the first request after a model
  load.
- **No measured task results yet.** Phase one was observed running real agentic
  sessions against `ornith-1.5:9b` and `qwen3.5:9b`, but no task success rates
  were recorded, so none should be reported.
- **The prompt-size baseline.** The ~15k figure for Claude Code's system prompt
  and tool schemas is an estimate. It is measurable from Ollama's log: the
  `task.n_tokens` of the first request in a fresh session. Prompts of 18–40k
  tokens were seen mid-session on 15 October.
- **Context: the limit moved from the model to the card.** With `qwen3:8b` (40K
  native) the ceiling was the model. The qwen35-family models are 262144-native,
  so the binding constraint is now VRAM. Whether 65536 *needs*
  `OLLAMA_KV_CACHE_TYPE=q8_0` on 16GB is open for this family: with q8_0 Ollama
  reported all 33 layers on GPU and a KV buffer of only 1088 MiB, far below the
  0.1MB-per-token estimate, because qwen35 is a hybrid architecture with full
  attention in one layer in four (`full_attention_interval = 4`) and recurrent
  state elsewhere. An f16 run would settle it. **32768 proved too
  small in practice** — not for truncated tool schemas but because it leaves too
  little room after the baseline, so auto-compaction thrashes.

### Environment of record

| Component | Version |
| --- | --- |
| Host OS | Linux x64 (Fedora, kernel 7.1.12). macOS untested; Windows unsupported — the shell layer is bash |
| Client machine | Kali Linux VM, reaching Ollama over the network |
| Ollama | in Docker on the host; NVIDIA GeForce RTX 4080 SUPER, 16GB; `OLLAMA_CONTEXT_LENGTH=65536`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=5m` |
| Claude Code | installed via `claude.ai/install.sh`; a Bun single-file executable; 2.1.291 for the picker and compaction measurements. **Record `claude --version` per run** — the repo does not pin it and the installer auto-updates |
| Models under test | `ornith-1.5:9b` (qwen35 family, 9.0B, Q4_K_M, 262144 native context, digest `e5df7dcdd8a2`) and `qwen3.5:9b`. **Record the digest per run**; the tag alone does not identify the weights |
| Phase one | Python proxy: FastAPI + uvicorn + httpx; last at commit `84859c2` |
| Phase two | bash launcher and Python standard library only; 63 tests, no server required |

Reproducing a measurement run: `claude-ollama` in the project under test, with
`docker logs -f` on the Ollama container for per-request prompt sizes, speeds and
durations, and `--debug-file` for Claude Code's own view of the window.
