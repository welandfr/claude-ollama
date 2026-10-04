# claude-ollama

Run Claude Code against a model on your own Ollama server.

Claude Code only speaks the Anthropic Messages API (`POST /v1/messages`). Ollama
speaks its own `/api/chat` and an OpenAI-shaped `/v1/chat/completions`. Neither
matches, so this repo is the translator that sits between them: point Claude Code
at it with `ANTHROPIC_BASE_URL` and it rewrites every request and streamed reply.

It talks to Ollama's native `/api/chat` rather than the OpenAI-compatible
endpoint, because that is the only one that accepts `options.num_ctx` — see
[Context size](#context-size), which is the single most important setting here.

The Ollama server itself is configured in the sibling `ollama-docker-api`
project. Two of its settings have to agree with this one: a context window at
least as large as `CLAUDE_OLLAMA_NUM_CTX`, and the KV-cache quantization that
makes that window fit. Since Ollama now serves `/v1/messages` itself, Claude Code
can also be pointed straight at it with no proxy — see that project's README for
when each route makes sense.

## Requirements

- **Linux or macOS.** The scripts (`setup.sh`, `run.sh`, `claude-ollama`,
  `vscode-setup.sh`) are bash, not POSIX sh. Developed and tested on Linux x64;
  macOS is untested.
- `bash`, `curl` and `python3` on PATH
- Python 3.9+ (3.14 works; the proxy has no version-specific dependencies)
- Ollama running somewhere you can reach, with a model that supports tool calling
- Claude Code on your PATH: `curl -fsSL https://claude.ai/install.sh | bash`

### Windows

Not supported yet. The proxy itself is portable — plain Python with no
OS-specific code — so what is missing is the shell layer: `setup.sh`, the
`claude-ollama` launcher and `vscode-setup.sh` would need PowerShell equivalents.

WSL2 works today with no changes — it is Linux as far as these scripts are
concerned. Note that Ollama running on the Windows host is reachable from WSL2
over the host's address rather than `localhost`, so `OLLAMA_BASE_URL` has to point
there.

## Setup

```sh
./setup.sh
```

`setup.sh` builds `.venv`, installs the dependencies, creates `.env` from
`.env.example` if you do not have one, symlinks `claude-ollama` onto your PATH,
and reports which `claude` binary will be used and whether Ollama
answers at the configured URL (listing its models).

The symlink goes to `~/.local/bin/claude-ollama` by default; set `BIN_DIR` to put
it elsewhere (`BIN_DIR=~/bin ./setup.sh`). If a real file already sits at that
name, setup says so and leaves it alone. Setup also tells you if the directory is
not on your PATH.

### Watching what the proxy does

The proxy logs one line per request, which is how you confirm traffic is actually
reaching Ollama:

```
claude-opus-5 -> qwen3.5:9b (3 messages, 14 tools, stream=True)
```

When `claude-ollama` starts the proxy itself it writes that log to `proxy.log` in
the checkout, *not* to your terminal — a background job inherits the terminal, and
these lines would be drawn over Claude Code's interface. Watch it from elsewhere:

```sh
tail -f proxy.log
```

Set `CLAUDE_OLLAMA_LOG` to put it somewhere else. To follow it live in a terminal
of its own instead, start the proxy in the foreground with `./run.sh` from the
checkout before launching — `claude-ollama` reuses a proxy that is already up
rather than starting a second one.

## Editor integration (VS Code, JetBrains)

The `claude-ollama` launcher does two things an editor extension does not: it sets
the environment Claude Code needs, and it starts the proxy. An extension finds
`claude` on your PATH and runs it directly, so it gets neither — Claude Code sees
no credentials and shows a login screen. Fix both halves.

### 1. Keep the proxy running

Nothing starts it for you here, so install it as a systemd user service:

```sh
./vscode-setup.sh --print-service > ~/.config/systemd/user/claude-ollama.service
systemctl --user daemon-reload
systemctl --user enable --now claude-ollama
systemctl --user status claude-ollama
```

Paths in the generated unit are absolute and taken from this checkout. After
changing `.env`, `systemctl --user restart claude-ollama`. Logs go to the journal
rather than `proxy.log`: `journalctl --user -u claude-ollama -f`.

### 2. Give the project the environment

```sh
cd ~/some/project
/path/to/claude-ollama/vscode-setup.sh
```

That writes `.claude/settings.local.json` in that project with an `env` block
pointing Claude Code at the proxy. Claude Code reads `env` from settings
regardless of what launched it, so the extension picks it up. Existing settings in
the file are merged, not replaced, and a malformed file is refused rather than
overwritten.

Values are generated from `.env` through the same loader the proxy uses, so `.env`
stays the single place settings are edited — re-run the script after changing it.
The file is a snapshot, not a live reference, so `claude-ollama` checks it against
`.env` on startup and tells you when it has gone stale rather than letting the old
values quietly win.
Add `.claude/settings.local.json` to that project's `.gitignore`; the script says
so if it is not already ignored.

Pass `--shared` to write `.claude/settings.json` instead, if you want the setting
committed for a project that is only ever used this way.

### Why per-project rather than globally

Putting this in `~/.claude/settings.json` would work everywhere, including
projects where you want the real Claude. Per-project keeps the proxy opt-in, so a
normal `claude` elsewhere is unaffected.

### Confirming it worked

The login screen disappearing is not proof the requests reach Ollama. Check the
proxy instead — `journalctl --user -u claude-ollama -f` should show a line per
request:

```
claude-opus-5 -> qwen3.5:9b (3 messages, 14 tools, stream=True)
```

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
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where Ollama listens |
| `CLAUDE_OLLAMA_TIMEOUT` | `600` | Request timeout in seconds; also becomes Claude Code's `API_TIMEOUT_MS` |
| `CLAUDE_OLLAMA_MODEL` | `qwen3.5:9b` | Model for Claude Code's main requests |
| `CLAUDE_OLLAMA_SMALL_MODEL` | unset | Model for its cheap background calls |
| `CLAUDE_OLLAMA_NUM_CTX` | `65536` | Context window requested from Ollama |
| `CLAUDE_OLLAMA_TEMPERATURE` | `0.0` | Used when the request does not specify one |
| `CLAUDE_OLLAMA_THINKING` | `off` | `off` sends `think=false`, `on` sends `think=true`, `auto` omits the field |
| `CLAUDE_OLLAMA_STRIP_THINKING` | `true` | Remove `<think>…</think>` that leaks into content |
| `CLAUDE_OLLAMA_DEBUG` | `false` | Log every raw line Ollama returns |
| `CLAUDE_OLLAMA_HOST` | `127.0.0.1` | Where the proxy listens |
| `CLAUDE_OLLAMA_PORT` | `8765` | Where the proxy listens |

`CLAUDE_OLLAMA_ENV` can point the loader at a different file, if you want to keep
several and switch between them.

**`.env` is the only thing that decides which model runs.** Whatever model id
Claude Code asks for is ignored, with one exception: an id containing `haiku` — its
cheap background calls — goes to `CLAUDE_OLLAMA_SMALL_MODEL` if you set one.

That is deliberate. Claude Code can ask for a model named in a `settings.json`
`env` block or persisted from an earlier session, and an earlier version of this
proxy passed such a name straight through — so a leftover `qwen3:8b` kept running
qwen3:8b while `.env` said `qwen3.5:9b`, with nothing in the terminal to say so.
The proxy now serves the configured model and logs the discrepancy:

```
request asked for 'qwen3:8b'; serving 'qwen3.5:9b' from .env instead. Something on
the Claude Code side still names the old model.
```

To change model, edit `.env` or set `CLAUDE_OLLAMA_MODEL` for one run — not
`ANTHROPIC_MODEL`.

### Context size

Ollama defaults to a 4096-token context and silently truncates anything longer.
Claude Code's system prompt and tool schemas are roughly 12–15k tokens *before*
your conversation starts, so at the default you would lose the tool definitions
and the agent would flail. `CLAUDE_OLLAMA_NUM_CTX` is therefore set to 65536.

**32768 is not enough either**, which is less obvious. It is large enough to hold
the system prompt and tool schemas, so the agent starts working — but those take
roughly 15k of it, and the ~17k left over is refilled by a single large `Read` or
a verbose command. Claude Code compacts, refills, compacts again, and eventually
reports `Autocompact is thrashing`. Ollama's own documentation puts the floor for
coding tools at 64k, which matches.

Claude Code is told the same number via `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, which
matters more than it looks. It has no catalog entry for an Ollama model name, so it
assumes a 200k window and auto-compacts at *that* — happily growing the
conversation to several times what Ollama accepts, which Ollama then truncates
without saying so. The launcher and `vscode-setup.sh` both derive this from
`CLAUDE_OLLAMA_NUM_CTX`, so the two windows cannot drift apart.

You will still see a warning that the model `isn't described by this version's
model catalog`. That part is unavoidable for a custom model name and is harmless
once the window is set explicitly.

The upper bound is your VRAM, not the model: `qwen3.5` is 256K-native, but
`num_ctx` allocates KV cache whether the conversation fills it or not. Raise it in
steps and watch for a sudden slowdown, which is Ollama spilling layers to CPU.

Sizing, for a 9B model at Q4_K_M (about 6.5GB of weights): the KV cache for a GQA
model of that class runs roughly 0.1MB per token at f16, so 64k costs about 8GB —
which with the weights exceeds a 16GB card. KV quantization on the server roughly
halves that, for a small quality loss, and brings 65536 within reach:

```sh
OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

As container environment variables if Ollama runs in one. Verify rather than
trust the arithmetic — `ollama ps` on the Ollama machine prints the allocated
`CONTEXT` and the `PROCESSOR` split, which must read `100% GPU`. Any CPU split
means the window is too large for the card, and the slowdown is severe rather
than fatal, so it is easy to miss.

## What gets translated

| Anthropic | Ollama |
| --- | --- |
| `system` (string or blocks) | leading `system` message |
| `tool_use` block | `message.tool_calls[].function` |
| `tool_result` block in a user turn | separate `tool` message, matched by tool name |
| `tools[].input_schema` | `tools[].function.parameters` |
| `image` block (base64) | `images[]` on the message |
| `max_tokens` / `stop_sequences` | `options.num_predict` / `options.stop` |
| SSE `content_block_delta` stream | NDJSON `message.content` chunks |
| `stop_reason` | `done_reason` (`length` → `max_tokens`, tool calls → `tool_use`) |

Also served: `/v1/messages/count_tokens` (an estimate — Ollama exposes no
tokenizer), `/v1/models` from `/api/tags`, and `/health` for a reachability check.

## Known limits

- **Tool-call ids are synthesised.** Ollama's native API has no id for a tool
  call, so results coming back are matched to the right tool by name. Two
  concurrent calls to the *same* tool in one turn can only be matched by order.
- **Images cannot ride in tool results.** Ollama's `tool` role takes no images, so
  a screenshot returned by a tool becomes a text placeholder.
- **`thinking` blocks are not replayed.** They are dropped from history, since
  Ollama has no field to put them back into.
- **Prompt caching is a no-op.** `cache_control` arrives on requests and is
  dropped; Ollama has no equivalent. Caching is deliberately left *enabled* in
  Claude Code: setting `DISABLE_PROMPT_CACHING=1` only produces a standing warning
  that requests "will be slower and cost more", which is untrue of a local model.
- **No streamed tool arguments.** Ollama delivers a whole arguments object, so each
  `tool_use` block is emitted as one `input_json_delta`.

## Slow models and timeouts

Loading a cold model into VRAM and evaluating a 15k-token prompt can take minutes
before Ollama emits its first token. A client that has heard nothing in that time
assumes the request died — Claude Code shows `Waiting for API response · will
retry in 2m 59s · check your network` and then retries, firing a *second*
generation at a GPU already busy with the first.

The proxy avoids that by sending `message_start` before it opens the upstream
request, and a `ping` event every 15 seconds (`PING_INTERVAL` in `stream.py`)
through any silence, which is what the real Anthropic API does. So Claude Code
hears from the proxy within milliseconds and keeps hearing from it while your GPU
works.

If you still see that warning, the request is exceeding Claude Code's own ceiling
rather than looking stalled: raise `CLAUDE_OLLAMA_TIMEOUT`, which also sets
`API_TIMEOUT_MS`. A smaller `CLAUDE_OLLAMA_NUM_CTX` cuts prompt-evaluation time
directly, at the cost of how much history fits.

## Network isolation

Two separate questions, often conflated.

**Where does conversation content go?** `ANTHROPIC_BASE_URL` decides, and nothing
else. Prompts, file contents, tool output and model replies go to Ollama and
nowhere else. This holds for `ollama launch claude` too — it is the same
mechanism.

**What else does Claude Code talk to?** That is independent of the base URL, and
pointing at Ollama does not change it. Claude Code recognises these, all of which
the launcher and `vscode-setup.sh` set:

| Variable | What it stops |
| --- | --- |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` | the umbrella: non-essential calls to Anthropic |
| `DISABLE_TELEMETRY=1` | usage telemetry |
| `DISABLE_ERROR_REPORTING=1` | crash and error reports |
| `DISABLE_AUTOUPDATER=1` | update checks against Anthropic's download service |

Ollama's own Claude Code documentation does not mention any of these, so
`ollama launch claude` leaves them at their defaults — conversation content is
local, the side channels are not. If you take the native route and isolation
matters, set them yourself alongside `ANTHROPIC_BASE_URL`.

Two caveats worth stating rather than implying. `DISABLE_AUTOUPDATER=1` means
sessions launched this way never self-update — a plain `claude` elsewhere still
does, so the binary is not frozen, only this path. And OpenTelemetry export is
opt-in via `CLAUDE_CODE_ENABLE_TELEMETRY` and goes to an endpoint you configure,
so it is not a leak unless you make one.

This is not an audited claim. It is what the documented switches cover; verifying
it properly means watching the process's own traffic.

## When a turn finishes but nothing changed

Claude Code says something like `Brewed for 45s · done` and no file is touched.
Three different things produce that, and the proxy log names which:

```
done: prompt=14233 eval=61 reason=stop tools=0 text=0 thinking=612
WARNING model returned 612 characters of reasoning and nothing else
```

**All output was reasoning.** The model put its whole reply inside
`<think>…</think>`, stripping removed it, and the turn ends with an empty message.
Set `CLAUDE_OLLAMA_STRIP_THINKING=false` to see what it said, or
`CLAUDE_OLLAMA_THINKING=on` so Ollama returns reasoning in its own field instead
of inline. Counter-intuitively `CLAUDE_OLLAMA_THINKING=off` does not prevent this
— a model can ignore the flag and emit the tags anyway.

```
done: prompt=14233 eval=12 reason=stop tools=0 text=32 thinking=0
WARNING model wrote 32 characters of prose but called none of the 1 tools offered
```

**It described the edit instead of making it.** The model answered in prose rather
than emitting a tool call, so Claude Code had nothing to execute. This is the
commonest small-model failure and it is not a proxy fault — see
[What to expect from small models](#what-to-expect-from-small-models). Retrying,
or a larger tool-calling model, is the only fix.

```
done: prompt=31992 eval=4001 reason=length tools=0 text=0 thinking=8800
```

**It ran out of room.** `reason=length` means the reply hit `num_predict`, and a
`prompt` close to `CLAUDE_OLLAMA_NUM_CTX` means the request itself nearly filled
the window — the tool definitions may have been truncated away before the model
ever saw them.

For anything these three do not explain, set `CLAUDE_OLLAMA_DEBUG=true` and
restart the proxy: every raw line Ollama returns is logged, so you can see exactly
what the model emitted and whether Ollama parsed a tool call out of it.

## What to expect from small models

Agentic coding leans hard on reliable tool calling, and that is where small models
break down first: they skip required arguments, invent tool names, or narrate a
tool call as prose instead of emitting one. An 8B model is enough to confirm the
plumbing works and to handle short, single-step requests. Expect to go to a larger
tool-calling model before multi-step editing feels usable.

## Tests

```sh
./.venv/bin/python -m pytest
```

The suite stubs Ollama out, so it runs with no server up. It covers the message
and tool conversions, the streamed event order, `<think>` spans split across
chunk boundaries, and the fallback for models that reject the `think` field.

## Design record

Notes for the research write-up: what was built, which decisions were forced
rather than chosen, and what is actually established versus assumed. Claims are
labelled so nothing unverified gets cited as a result.

Built 2 – 3 October 2026.

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
documented configuration point. **A translating proxy is not a workaround chosen
for convenience; it is the only available seam.**

### Why Ollama's native API, not its OpenAI-compatible one

Ollama exposes `/v1/chat/completions` (OpenAI-shaped) alongside its own
`/api/chat`. The OpenAI-compatible endpoint would have been the shorter path, but
it offers no way to set `options.num_ctx`.

That single parameter decides whether the system works at all. Ollama defaults to
a 4096-token context and truncates silently past it, while Claude Code's system
prompt plus its tool schemas occupy a large fraction of that before any
conversation begins. Truncation removes the tool definitions, after which the
model cannot emit valid tool calls and the agent fails in a way that looks like
model incompetence rather than a configuration fault. The native endpoint also
exposes `think` and returns reasoning in a separate field. *(Verified: `num_ctx`
reaches Ollama, asserted in `tests/test_app.py`. The prompt-size figure is an
estimate, not a measurement — see "Open questions".)*

### Protocol impedance mismatches

Three are structural rather than cosmetic:

1. **Tool-call identity.** Anthropic correlates a `tool_use` block with its
   `tool_result` by an explicit `tool_use_id`. Ollama's native API gives tool
   calls no identifier at all. The proxy synthesises `toolu_*` ids outbound and
   matches results back **by tool name**, carrying a name map across the
   conversation. Two concurrent calls to the *same* tool in one turn can only be
   distinguished by order. This is an information loss in the protocol, not an
   implementation shortcut.
2. **Tool results live in different turns.** Anthropic puts `tool_result` blocks
   inside the *user* message; Ollama expects separate `tool`-role messages. The
   converter flushes pending user text before each result so relative ordering
   survives.
3. **Streaming shape.** Anthropic sends typed SSE events (`message_start`,
   per-block `content_block_start`/`delta`/`stop`, `message_delta`,
   `message_stop`); Ollama sends NDJSON lines. Content blocks are opened lazily
   because Ollama does not announce what is coming. Tool arguments arrive as a
   complete object rather than a token stream, so each `tool_use` block is
   emitted as a single `input_json_delta`.

Mechanical mappings: `system` (string or blocks) to a leading system message;
`input_schema` to `function.parameters`; base64 `image` blocks to `images[]`;
`max_tokens`/`stop_sequences` to `options.num_predict`/`options.stop`;
`done_reason` to `stop_reason` (`length` to `max_tokens`, presence of tool calls
to `tool_use`). `cache_control` is dropped. *(All verified in the test suite.)*

### Findings worth reporting

**Local inference breaks an assumption built into cloud API clients.** The most
substantive finding. Claude Code treats a silent connection as a failed one: it
displays `Waiting for API response · will retry in 2m 59s · check your network`
and then retries. Against a cloud endpoint, time-to-first-byte is short and this
never fires. Against local inference, loading a cold model into VRAM and
evaluating a long prompt produces minutes of silence before the first token, and
the client's retry issues a *second* generation to a GPU already busy with the
first, while abandoning the original response — degrading throughput precisely
when the hardware is most loaded.

The first implementation inherited the fault by emitting `message_start` only
after Ollama returned response headers. Reproduced with a stub that delays its
first token by 12 seconds: no bytes whatsoever for 12s, then all seven events at
once. The fix sends `message_start` before opening the upstream request and a
`ping` every 15 seconds through any silence — the same keepalive discipline the
real Anthropic API uses. Reading Ollama happens in a separate task feeding a
queue, which is what frees the response generator to ping while nothing arrives.
*(Verified: both the failure and the fix reproduced by timing SSE events; two
regression tests assert `message_start` precedes the upstream response and that
pings appear during silence.)*

**Reasoning-token leakage straddles chunk boundaries.** Reasoning models emit
`<think>…</think>` inside ordinary content even when thinking is nominally
disabled, and the tags split across streamed fragments. Naive per-chunk filtering
fails; the filter holds back any tail that could still become a tag, and emits a
dangling partial as literal text once the stream ends. *(Verified by
parametrised tests including split-tag cases.)*

**Capability negotiation is absent, so failure must be recovered from.** Models
that do not support thinking reject the `think` field with HTTP 400. The proxy
retries once without it, invisibly: because the message id in `message_start` is
generated locally, the second attempt continues the same logical response.
*(Verified for both streaming and non-streaming paths.)*

**Prompt-caching semantics do not transfer, and suppressing them misleads.**
Setting `DISABLE_PROMPT_CACHING=1` produces a standing warning that requests
"will be slower and cost more" — untrue of a local model with no per-token
billing. Caching is left enabled and `cache_control` simply discarded.

**Hypothesis, not a result:** for agentic coding the binding constraint is
tool-calling reliability across long multi-turn contexts, not code-generation
quality, so a code-specialised model (`qwen2.5-coder:7b`) is not automatically
preferable to a newer general model trained for agentic use (`qwen3:8b`, since
superseded by `qwen3.5:9b`). Both
declare `tools` support in Ollama's library; `qwen3` additionally declares
`thinking`. *(Capability tags verified; the comparative claim is untested and
stated as a hypothesis.)*

### Open questions

- **No end-to-end result yet.** Every component is verified against stubbed
  Ollama responses. At the time of writing the stack had not been observed
  completing an agentic task against a real model, so nothing should be reported
  about task success rates.
- **The prompt-size figure is unmeasured.** The claim that Claude Code's system
  prompt and tool schemas occupy well over Ollama's 4096-token default is an
  estimate. It is now measurable: the proxy logs `done: prompt=N eval=N
  reason=X` per request, where `prompt` is Ollama's own `prompt_eval_count`.
  Read it from `proxy.log` and report the measured value.
- **Context: the limit moved from the model to the card.** With `qwen3:8b` (40K
  native) the ceiling was the model and going further needed YaRN scaling. The
  qwen35-family models are 262144-native, so the binding constraint is now VRAM.
  On 16GB, 65536 needs `OLLAMA_KV_CACHE_TYPE=q8_0` to fit alongside a 9B model.
  **32768 proved too small in practice**, not for the reason documented above
  (truncated tool schemas) but a second one: it leaves too little room after the
  ~15k baseline, so auto-compaction thrashes. Report the measured baseline from
  `proxy.log`'s `prompt=` rather than the 15k estimate.

### Environment of record

| Component | Version |
| --- | --- |
| Host OS | Linux x64 (Fedora, kernel 7.1.12). macOS untested; Windows unsupported — the shell layer is bash |
| Claude Code | installed via `claude.ai/install.sh` to `~/.local/bin`; a Bun single-file executable. **Record `claude --version` per run** — the repo does not pin it and the installer auto-updates |
| Model under test | `ornith-1.5:9b` via Ollama — qwen35 family, 9.0B, Q4_K_M, 262144 native context, digest `e5df7dcdd8a2`. Third model in three days (`qwen3:8b` → `qwen3.5:9b` → this), so **record the digest per run**; the tag alone does not identify the weights |
| Proxy runtime | Python 3.14.7, FastAPI + uvicorn + httpx |
| Proxy implementation | five modules: `translate`, `stream`, `app`, `config`, `ide` |
| Test suite | 52 tests, no Ollama required |

Reproducing a measurement run: `./run.sh` in the checkout, `claude-ollama` in the
project under test, then read `proxy.log` — one line per request giving the
requested and resolved model, message and tool counts, followed by the prompt and
eval token counts on completion.
