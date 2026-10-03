# claude-ollama

Run Claude Code against a model on your own Ollama server.

Claude Code only speaks the Anthropic Messages API (`POST /v1/messages`). Ollama
speaks its own `/api/chat` and an OpenAI-shaped `/v1/chat/completions`. Neither
matches, so this repo is the translator that sits between them: point Claude Code
at it with `ANTHROPIC_BASE_URL` and it rewrites every request and streamed reply.

It talks to Ollama's native `/api/chat` rather than the OpenAI-compatible
endpoint, because that is the only one that accepts `options.num_ctx` — see
[Context size](#context-size), which is the single most important setting here.

## Requirements

- Python 3.9+ (3.14 works; the proxy has no version-specific dependencies)
- Ollama running somewhere you can reach, with a model that supports tool calling
- Claude Code — either already on your PATH, or fetched into this checkout (below)

## Setup

```sh
./fetch-claude.sh   # optional: put the claude binary in ./bin
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

### Keeping Claude Code inside the checkout

The official installer (`curl -fsSL https://claude.ai/install.sh | bash`) always
installs to `~/.local/share/claude/versions/` with a symlink at
`~/.local/bin/claude`; its `[target]` argument picks a *version*, not a path, and
no environment variable redirects it.

`./fetch-claude.sh` gets the same result inside the repo instead. It resolves a
version from the same release channel, downloads the same standalone binary for
your platform, verifies the same SHA256 from the release manifest, and writes it
to `./bin/claude`:

```sh
./fetch-claude.sh            # stable (default)
./fetch-claude.sh latest
./fetch-claude.sh 2.1.285    # pin an exact version
```

`bin/` is gitignored — the binary is around 240MB, so it must not be committed.
The installed version is recorded in `bin/.version`.

`claude-ollama` uses the checkout's `bin/claude` when it exists and falls back to
whatever is on your PATH, so an existing `~/.local/bin` install keeps working untouched. The
launcher also sets `DISABLE_AUTOUPDATER=1` so the in-repo copy stays at the
version you fetched; re-run `./fetch-claude.sh` to move it.

Edit `.env` so `OLLAMA_BASE_URL` and the model names match your setup, then run
it from whatever project you want to work in:

```sh
cd ~/some/other/project
claude-ollama
```

It starts the proxy if it is not already up, exports the environment Claude Code
needs, and launches it. Arguments pass straight through (`claude-ollama -p "hi"`).

`claude-ollama` resolves its own symlink to locate this checkout and deliberately
does not change directory, so Claude Code starts in the project you invoked it
from — the checkout only supplies the proxy, the config and the binary.

### Watching what the proxy does

The proxy logs one line per request, which is how you confirm traffic is actually
reaching Ollama:

```
claude-opus-5 -> qwen3:8b (3 messages, 14 tools, stream=True)
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
| `CLAUDE_OLLAMA_MODEL` | `qwen3:8b` | Model for Claude Code's main requests |
| `CLAUDE_OLLAMA_SMALL_MODEL` | unset | Model for its cheap background calls |
| `CLAUDE_OLLAMA_NUM_CTX` | `32768` | Context window requested from Ollama |
| `CLAUDE_OLLAMA_TEMPERATURE` | `0.0` | Used when the request does not specify one |
| `CLAUDE_OLLAMA_THINKING` | `off` | `off` sends `think=false`, `on` sends `think=true`, `auto` omits the field |
| `CLAUDE_OLLAMA_STRIP_THINKING` | `true` | Remove `<think>…</think>` that leaks into content |
| `CLAUDE_OLLAMA_HOST` | `127.0.0.1` | Where the proxy listens |
| `CLAUDE_OLLAMA_PORT` | `8765` | Where the proxy listens |

`CLAUDE_OLLAMA_ENV` can point the loader at a different file, if you want to keep
several and switch between them.

Model resolution has three rules, in order: a requested id containing `haiku` goes
to `CLAUDE_OLLAMA_SMALL_MODEL`; an id that is not Anthropic-shaped passes through
untouched (so `CLAUDE_OLLAMA_MODEL=qwen3:30b` just works); anything else goes to
`CLAUDE_OLLAMA_MODEL`.

### Context size

Ollama defaults to a 4096-token context and silently truncates anything longer.
Claude Code's system prompt and tool schemas are roughly 12–15k tokens *before*
your conversation starts, so at the default you would lose the tool definitions
and the agent would flail. `defaults.num_ctx` is therefore set to 32768; do not
drop it below about 16384. Raising it costs VRAM, so a model that fits
comfortably at 4k may not fit at 32k.

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
preferable to a newer general model trained for agentic use (`qwen3:8b`). Both
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
- **Context ceiling.** `num_ctx` is set to 32768, at or near the native limit of
  both candidate models; going higher needs YaRN scaling rather than a larger
  number. Confirm per model with `ollama show`.

### Environment of record

| Component | Version |
| --- | --- |
| Claude Code | 2.1.285 (stable channel), `linux-x64`, Bun 1.4.3 single-file executable |
| Model under test | `qwen3:8b` via Ollama |
| Proxy runtime | Python 3.14.7, FastAPI + uvicorn + httpx |
| Proxy implementation | 861 lines across four modules (`translate`, `stream`, `app`, `config`) |
| Test suite | 44 tests, 640 lines, no Ollama required |

Reproducing a measurement run: `./run.sh` in the checkout, `claude-ollama` in the
project under test, then read `proxy.log` — one line per request giving the
requested and resolved model, message and tool counts, followed by the prompt and
eval token counts on completion.
