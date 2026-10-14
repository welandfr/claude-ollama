"""End-to-end tests over a stubbed Ollama backend."""

import asyncio
import json

import httpx
import pytest

from claude_ollama import stream as stream_module
from claude_ollama.app import create_app
from claude_ollama.config import Config

CFG = Config(default_model="qwen3:8b", num_ctx=8192, thinking="off")


def ndjson(*objects: dict) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objects)


def make_client(handler) -> httpx.AsyncClient:
    """An app whose upstream Ollama is replaced by ``handler``."""
    app = create_app(CFG)
    app.state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ollama"
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        name = payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        if name:
            events.append((name, payload))
    return events


@pytest.mark.asyncio
async def test_non_streaming_roundtrip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "hello there"},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 50,
                "eval_count": 3,
            },
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "claude-opus-5", "max_tokens": 100, "messages": [{"role": "user", "content": "hi"}]},
        )

    assert reply.status_code == 200
    body = reply.json()
    assert body["content"] == [{"type": "text", "text": "hello there"}]
    assert body["stop_reason"] == "end_turn"
    assert body["usage"]["input_tokens"] == 50
    # num_ctx must reach Ollama or the system prompt gets truncated.
    assert seen["payload"]["options"]["num_ctx"] == 8192


@pytest.mark.asyncio
async def test_streaming_text_event_order():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop", "eval_count": 2},
            ),
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )

    names = [name for name, _ in parse_sse(reply.text)]
    assert names == [
        "message_start",
        "ping",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    text = "".join(
        data["delta"]["text"] for name, data in parse_sse(reply.text) if name == "content_block_delta"
    )
    assert text == "Hello"


@pytest.mark.asyncio
async def test_streaming_tool_call_produces_parseable_input():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "looking"}, "done": False},
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"function": {"name": "Read", "arguments": {"path": "a.py"}}}],
                    },
                    "done": True,
                    "done_reason": "stop",
                },
            ),
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "read"}]},
        )

    events = parse_sse(reply.text)
    starts = [d["content_block"] for n, d in events if n == "content_block_start"]
    assert [b["type"] for b in starts] == ["text", "tool_use"]
    assert starts[1]["name"] == "Read"

    # The text block must close before the tool block opens, and the accumulated
    # partial_json must parse on its own.
    tool_index = starts[1] and [d["index"] for n, d in events if n == "content_block_start"][1]
    partial = "".join(
        d["delta"]["partial_json"]
        for n, d in events
        if n == "content_block_delta" and d["index"] == tool_index and "partial_json" in d["delta"]
    )
    assert json.loads(partial) == {"path": "a.py"}

    final = [d for n, d in events if n == "message_delta"][0]
    assert final["delta"]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_thinking_is_stripped_from_the_stream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "<thi"}, "done": False},
                {"message": {"content": "nk>plan</think>answer"}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop"},
            ),
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )

    text = "".join(
        d["delta"]["text"] for n, d in parse_sse(reply.text) if n == "content_block_delta"
    )
    assert text == "answer"


@pytest.mark.asyncio
async def test_think_unsupported_falls_back():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "think" in payload:
            return httpx.Response(400, json={"error": 'model "x" does not support think'})
        return httpx.Response(200, json={"message": {"content": "ok"}, "done": True, "done_reason": "stop"})

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages", json={"model": "x", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert reply.status_code == 200
    assert reply.json()["content"][0]["text"] == "ok"
    assert len(calls) == 2 and "think" not in calls[1]


@pytest.mark.asyncio
async def test_think_unsupported_falls_back_while_streaming():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "think" in payload:
            return httpx.Response(400, json={"error": "does not support think"})
        return httpx.Response(
            200, content=ndjson({"message": {"content": "ok"}, "done": True, "done_reason": "stop"})
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )

    names = [n for n, _ in parse_sse(reply.text)]
    assert names[0] == "message_start" and "error" not in names
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_ollama_error_becomes_anthropic_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": 'model "nope" not found'})

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages", json={"model": "nope", "messages": [{"role": "user", "content": "hi"}]}
        )

    assert reply.status_code == 404
    body = reply.json()
    assert body["type"] == "error"
    assert "not found" in body["error"]["message"]


@pytest.mark.asyncio
async def test_count_tokens_and_models():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b", "modified_at": "now"}]})

    async with make_client(handler) as client:
        counted = await client.post(
            "/v1/messages/count_tokens",
            json={"model": "x", "messages": [{"role": "user", "content": "hello world"}]},
        )
        listed = await client.get("/v1/models")

    assert counted.json()["input_tokens"] > 0
    assert listed.json()["data"][0]["id"] == "qwen3:8b"


@pytest.mark.asyncio
async def test_message_start_is_sent_before_ollama_responds():
    """A slow local model must not look like a dead connection.

    Claude Code retries a request it has heard nothing about, which fires a
    second generation at an already busy GPU, so message_start has to go out
    before the upstream request rather than after it. Driven directly rather
    than through the app: ASGITransport buffers the body, which would hide the
    ordering this is checking.
    """
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        await release.wait()  # stands in for model load and prompt evaluation
        return httpx.Response(
            200, content=ndjson({"message": {"content": "hi"}, "done": True, "done_reason": "stop"})
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama")
    events = stream_module.stream_response(upstream, {"model": "x", "stream": True}, CFG, "x")

    first = await events.__anext__()
    assert "event: message_start" in first
    assert not release.is_set()

    release.set()
    rest = [chunk async for chunk in events]
    await upstream.aclose()
    assert "event: message_stop" in rest[-1]


@pytest.mark.asyncio
async def test_pings_go_out_while_ollama_is_silent(monkeypatch):
    monkeypatch.setattr(stream_module, "PING_INTERVAL", 0.05)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.4)  # several ping intervals of silence
        return httpx.Response(
            200, content=ndjson({"message": {"content": "ok"}, "done": True, "done_reason": "stop"})
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama")
    chunks = [c async for c in stream_module.stream_response(upstream, {"model": "x"}, CFG, "x")]
    await upstream.aclose()

    names = [name for name, _ in parse_sse("".join(chunks))]
    assert names[0] == "message_start"
    assert names.count("ping") >= 3, names
    assert names[-1] == "message_stop"


@pytest.mark.asyncio
async def test_all_thinking_output_is_reported_not_silent(caplog):
    """The hardest symptom to diagnose: a turn that changes nothing.

    If the model puts its whole reply inside <think>, stripping leaves an empty
    message and Claude Code shows "done" with no edit. The log has to say so.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "<think>I should edit the file</think>"}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop"},
            ),
        )

    with caplog.at_level("WARNING"):
        async with make_client(handler) as client:
            reply = await client.post(
                "/v1/messages",
                json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "edit"}]},
            )

    assert "reasoning and nothing else" in caplog.text
    assert "STRIP_THINKING" in caplog.text  # names the way out
    names = [n for n, _ in parse_sse(reply.text)]
    assert names[-1] == "message_stop"  # still a well-formed response


@pytest.mark.asyncio
async def test_prose_instead_of_a_tool_call_is_reported(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "You should change line 3 to foo."}, "done": False},
                {"message": {"content": ""}, "done": True, "done_reason": "stop"},
            ),
        )

    with caplog.at_level("WARNING"):
        async with make_client(handler) as client:
            await client.post(
                "/v1/messages",
                json={
                    "model": "x",
                    "stream": True,
                    "tools": [{"name": "Edit", "description": "e", "input_schema": {"type": "object"}}],
                    "messages": [{"role": "user", "content": "edit"}],
                },
            )

    assert "called none of the 1 tools offered" in caplog.text


@pytest.mark.asyncio
async def test_a_real_tool_call_warns_about_nothing(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {
                    "message": {
                        "content": "",
                        "tool_calls": [{"function": {"name": "Edit", "arguments": {"path": "a"}}}],
                    },
                    "done": True,
                    "done_reason": "stop",
                },
            ),
        )

    with caplog.at_level("WARNING"):
        async with make_client(handler) as client:
            await client.post(
                "/v1/messages",
                json={
                    "model": "x",
                    "stream": True,
                    "tools": [{"name": "Edit", "description": "e", "input_schema": {"type": "object"}}],
                    "messages": [{"role": "user", "content": "edit"}],
                },
            )

    assert caplog.text == "" or "tools offered" not in caplog.text


@pytest.mark.asyncio
async def test_unexpected_model_name_is_reported(caplog):
    """A stale ANTHROPIC_MODEL elsewhere would otherwise run the wrong model.

    Unrecognised names pass through verbatim so CLAUDE_OLLAMA_MODEL works, which
    means a leftover name from a settings file is honoured in silence.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": "ok"}, "done": True, "done_reason": "stop"}
        )

    with caplog.at_level("WARNING"):
        async with make_client(handler) as client:
            await client.post(
                "/v1/messages",
                json={"model": "llama3:70b", "messages": [{"role": "user", "content": "hi"}]},
            )

    assert "llama3:70b" in caplog.text
    assert "serving" in caplog.text and CFG.default_model in caplog.text


@pytest.mark.asyncio
async def test_the_configured_model_warns_about_nothing(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": "ok"}, "done": True, "done_reason": "stop"}
        )

    with caplog.at_level("WARNING"):
        async with make_client(handler) as client:
            await client.post(
                "/v1/messages",
                json={"model": CFG.default_model, "messages": [{"role": "user", "content": "hi"}]},
            )

    assert "serving" not in caplog.text


@pytest.mark.asyncio
async def test_streamed_usage_reports_input_tokens():
    """Claude Code's context display reads usage from the streamed response.

    Ollama only reports prompt_eval_count when it finishes, so message_start
    cannot carry it; if message_delta omits it too, the client sees a prompt of
    zero tokens and shows 0% context used however full the window is.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "hi"}, "done": False},
                {
                    "message": {"content": ""},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 18432,
                    "eval_count": 57,
                },
            ),
        )

    async with make_client(handler) as client:
        reply = await client.post(
            "/v1/messages",
            json={"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )

    delta = [d for n, d in parse_sse(reply.text) if n == "message_delta"][0]
    assert delta["usage"]["input_tokens"] == 18432
    assert delta["usage"]["output_tokens"] == 57


def watchdog_upstream(ps_reply, first_chunk: bool):
    """Ollama that streams one chunk (optionally) and then goes silent for good.

    ``ps_reply`` is what /api/ps answers: a list of loaded model names, or an
    exception to raise.
    """

    async def silent_stream():
        if first_chunk:
            yield ndjson({"message": {"content": "thinking about it"}, "done": False})
        await asyncio.sleep(3600)  # the request that never finishes
        yield b""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            if isinstance(ps_reply, Exception):
                raise ps_reply
            return httpx.Response(200, json={"models": [{"name": n, "model": n} for n in ps_reply]})
        return httpx.Response(200, content=silent_stream())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama")


async def run_until_quiet(upstream, seconds: float) -> list[tuple[str, dict]]:
    """Collect events for at most ``seconds``; a stream still open is cut off."""
    chunks: list[str] = []

    async def collect():
        async for chunk in stream_module.stream_response(upstream, {"model": "x:9b"}, CFG, "x:9b"):
            chunks.append(chunk)

    try:
        await asyncio.wait_for(collect(), seconds)
    except asyncio.TimeoutError:
        pass
    await upstream.aclose()
    return parse_sse("".join(chunks))


@pytest.fixture
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(stream_module, "PING_INTERVAL", 0.05)
    monkeypatch.setattr(stream_module, "WATCHDOG_INTERVAL", 0.05)


@pytest.mark.asyncio
async def test_an_unloaded_model_ends_the_turn_instead_of_hanging(fast_watchdog, caplog):
    """The model streamed, then vanished from /api/ps: nothing more will come.

    Without this, keepalive pings hold Claude Code on a dead request until the
    read timeout — ten minutes by default.
    """
    events = await run_until_quiet(watchdog_upstream([], first_chunk=True), 2.0)
    names = [name for name, _ in events]
    assert names[-1] == "error", names
    assert "unloaded x:9b" in events[-1][1]["error"]["message"]
    assert "was unloaded" in caplog.text


@pytest.mark.asyncio
async def test_a_cold_load_is_not_mistaken_for_an_unload(fast_watchdog):
    """Before any output the model may simply not be loaded yet."""
    events = await run_until_quiet(watchdog_upstream([], first_chunk=False), 0.6)
    assert "error" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_a_model_still_loaded_keeps_the_turn_waiting(fast_watchdog):
    """Long silence alone is fine: a slow generation is still a generation."""
    events = await run_until_quiet(watchdog_upstream(["x:9b"], first_chunk=True), 0.6)
    names = [name for name, _ in events]
    assert "error" not in names
    assert names.count("ping") >= 3


@pytest.mark.asyncio
async def test_an_unanswerable_check_decides_nothing(fast_watchdog):
    upstream = watchdog_upstream(httpx.ConnectError("ps unavailable"), first_chunk=True)
    events = await run_until_quiet(upstream, 0.6)
    assert "error" not in [name for name, _ in events]


@pytest.mark.asyncio
async def test_a_model_seen_loaded_then_gone_also_counts(fast_watchdog):
    """Armed by /api/ps alone, for a model that has produced no output yet."""
    loaded = ["x:9b"]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            reply = httpx.Response(200, json={"models": [{"name": n} for n in loaded]})
            loaded.clear()  # present on the first check, gone after
            return reply

        async def silent():
            await asyncio.sleep(3600)
            yield b""

        return httpx.Response(200, content=silent())

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama")
    events = await run_until_quiet(upstream, 2.0)
    assert events[-1][0] == "error"
