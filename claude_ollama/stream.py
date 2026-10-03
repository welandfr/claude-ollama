"""Turns Ollama's NDJSON stream into the Anthropic SSE event sequence.

Claude Code's client is strict about the order of events: message_start, then a
start/delta/stop triple per content block, then message_delta and message_stop.
Blocks are opened lazily because Ollama does not announce what is coming.

Two things keep the connection alive while a local model is slow. message_start
is sent before the upstream request, not after it: loading a cold model into VRAM
and evaluating a 15k-token prompt can take minutes during which Ollama sends no
response headers at all, and a client that has heard nothing treats the request
as stalled and retries it — firing a second generation at an already busy GPU.
Then ping events go out during any long silence, which is what the real Anthropic
API does too.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import httpx

from .config import Config
from .translate import STOP_REASONS, ThinkFilter, new_message_id, tool_use_block

log = logging.getLogger("claude_ollama")

# Seconds of silence from Ollama before a keepalive ping goes out.
PING_INTERVAL = 15.0


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _ping() -> str:
    return sse("ping", {"type": "ping"})


def _error(message: str) -> str:
    return sse("error", {"type": "error", "error": {"type": "api_error", "message": message}})


class _RetryWithoutThink(Exception):
    """Raised when the model rejects the ``think`` field."""


async def stream_response(
    client: httpx.AsyncClient, payload: dict, cfg: Config, model: str
) -> AsyncIterator[str]:
    yield sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": new_message_id(),
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            },
        },
    )
    yield _ping()

    # A think retry is invisible to the client: the message id and model in
    # message_start are ours, so the second attempt continues the same message.
    try:
        async for chunk in _attempt(client, payload, cfg):
            yield chunk
    except _RetryWithoutThink:
        fallback = {k: v for k, v in payload.items() if k != "think"}
        async for chunk in _attempt(client, fallback, cfg):
            yield chunk


async def _attempt(
    client: httpx.AsyncClient, payload: dict, cfg: Config
) -> AsyncIterator[str]:
    """Read one /api/chat response, pinging through any silence."""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def pump() -> None:
        # Reading upstream in its own task is what frees this generator to emit
        # pings while nothing is arriving.
        try:
            async with client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    await queue.put(("status", (response.status_code, body)))
                    return
                async for line in response.aiter_lines():
                    await queue.put(("line", line))
        except Exception as exc:  # noqa: BLE001 - reported to the client as text
            await queue.put(("failed", exc))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(pump())

    next_index = 0
    text_index: int | None = None
    think = ThinkFilter(cfg.strip_thinking)
    stop_reason = "end_turn"
    usage = {"input_tokens": 0, "output_tokens": 0}
    failure: str | None = None
    text_chars = 0
    tool_count = 0
    offered_tools = len(payload.get("tools") or [])

    def open_text() -> tuple[int, str]:
        nonlocal next_index
        index = next_index
        next_index += 1
        return index, sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "text", "text": ""},
            },
        )

    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), PING_INTERVAL)
            except asyncio.TimeoutError:
                yield _ping()
                continue

            if kind == "status":
                status, body = value
                if status == 400 and "think" in payload and "think" in body:
                    raise _RetryWithoutThink
                failure = f"Ollama returned {status}: {body[:500]}"
                break

            if kind == "failed":
                failure = f"cannot reach Ollama: {value}"
                break

            if kind == "end":
                break

            line = str(value).strip()
            if not line:
                continue
            if cfg.debug:
                log.debug("ollama <- %s", line[:2000])
            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("error"):
                failure = str(event["error"])
                break

            message = event.get("message") or {}

            piece = think.feed(message.get("content") or "")
            text_chars += len(piece)
            if piece:
                if text_index is None:
                    text_index, start = open_text()
                    yield start
                yield sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": text_index,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                )

            for call in message.get("tool_calls") or []:
                block = tool_use_block(call)
                if text_index is not None:
                    yield sse(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": text_index},
                    )
                    text_index = None
                index = next_index
                next_index += 1
                # Ollama hands over whole arguments objects rather than a token
                # stream, so the block is opened, filled and closed in one go.
                yield sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block["id"],
                            "name": block["name"],
                            "input": {},
                        },
                    },
                )
                yield sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block["input"]),
                        },
                    },
                )
                yield sse("content_block_stop", {"type": "content_block_stop", "index": index})
                stop_reason = "tool_use"
                tool_count += 1

            if event.get("done"):
                usage = {
                    "input_tokens": int(event.get("prompt_eval_count") or 0),
                    "output_tokens": int(event.get("eval_count") or 0),
                }
                # prompt= against num_ctx says whether the context window is big
                # enough; tools= and text= say whether the model actually acted.
                log.info(
                    "done: prompt=%d eval=%d reason=%s tools=%d text=%d thinking=%d",
                    usage["input_tokens"],
                    usage["output_tokens"],
                    event.get("done_reason", "stop"),
                    tool_count,
                    text_chars,
                    think.stripped,
                )
                if stop_reason != "tool_use":
                    stop_reason = STOP_REASONS.get(event.get("done_reason", "stop"), "end_turn")
                break
    finally:
        task.cancel()

    if failure is not None:
        if text_index is not None:
            yield sse("content_block_stop", {"type": "content_block_stop", "index": text_index})
        yield _error(failure)
        return

    tail = think.flush()
    text_chars += len(tail)
    if tail:
        if text_index is None:
            text_index, start = open_text()
            yield start
        yield sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": text_index,
                "delta": {"type": "text_delta", "text": tail},
            },
        )

    if text_index is not None:
        yield sse("content_block_stop", {"type": "content_block_stop", "index": text_index})
    elif next_index == 0:
        if think.stripped:
            log.warning(
                "model returned %d characters of reasoning and nothing else; "
                "Claude Code will show an empty turn. Try CLAUDE_OLLAMA_STRIP_THINKING=false "
                "to see it, or CLAUDE_OLLAMA_THINKING=on so Ollama separates it out.",
                think.stripped,
            )
        else:
            log.warning("model returned no content at all")
        # Never send a message with no content blocks at all.
        index, start = open_text()
        yield start
        yield sse("content_block_stop", {"type": "content_block_stop", "index": index})

    if offered_tools and not tool_count and text_chars:
        # The commonest small-model failure: it describes the edit instead of
        # calling the tool, so the turn ends with nothing changed on disk.
        log.warning(
            "model wrote %d characters of prose but called none of the %d tools offered",
            text_chars,
            offered_tools,
        )

    yield sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": usage["output_tokens"]},
        },
    )
    yield sse("message_stop", {"type": "message_stop"})
