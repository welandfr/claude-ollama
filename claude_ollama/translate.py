"""Conversion between the Anthropic Messages API and Ollama's /api/chat.

The two formats disagree in three places that matter:

* Anthropic nests assistant output in content blocks (``text``, ``tool_use``);
  Ollama uses a flat ``content`` string plus ``tool_calls``.
* Anthropic returns tool results inside the *user* turn as ``tool_result``
  blocks keyed by ``tool_use_id``; Ollama expects separate ``tool`` messages and
  has no ids at all, so results are matched back by tool name.
* Anthropic tool arguments stream as JSON text; Ollama hands them over as an
  already-parsed object.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from .config import Config

STOP_REASONS = {"stop": "end_turn", "length": "max_tokens", "load": "end_turn"}


def new_message_id() -> str:
    return "msg_" + secrets.token_hex(12)


def new_tool_id() -> str:
    return "toolu_" + secrets.token_hex(12)


def as_blocks(content: Any) -> list[dict]:
    """Normalise a message's content to a list of blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if isinstance(content, dict):
        return [content]
    return [b for b in content if isinstance(b, dict)]


def system_text(system: Any) -> str:
    """Flatten Anthropic's string-or-blocks system field."""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    parts = [b.get("text", "") for b in as_blocks(system) if b.get("type") == "text"]
    return "\n\n".join(p for p in parts if p)


def _image_data(block: dict) -> str | None:
    """Ollama takes bare base64 strings in a message's ``images`` list."""
    source = block.get("source") or {}
    if source.get("type") == "base64" and source.get("data"):
        return source["data"]
    return None


def _result_content(block: dict) -> str:
    """Render a tool_result block as the plain text Ollama's tool role wants."""
    content = block.get("content")
    if isinstance(content, str):
        text = content
    else:
        parts = []
        for part in as_blocks(content):
            if part.get("type") == "text":
                parts.append(part.get("text", ""))
            elif part.get("type") == "image":
                # The tool role carries no images; say so rather than drop it silently.
                parts.append("[image omitted: the Ollama tool role cannot carry images]")
        text = "\n".join(p for p in parts if p)
    if block.get("is_error"):
        return f"Error: {text}" if text else "Error"
    return text


def convert_messages(messages: list[dict], system: Any = None) -> list[dict]:
    """Build an Ollama message list, preserving turn order."""
    out: list[dict] = []
    sys_text = system_text(system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})

    tool_names: dict[str, str] = {}  # tool_use_id -> tool name

    for message in messages or []:
        role = message.get("role")
        blocks = as_blocks(message.get("content"))

        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for block in blocks:
                kind = block.get("type")
                if kind == "text":
                    text_parts.append(block.get("text", ""))
                elif kind == "tool_use":
                    tool_names[block.get("id", "")] = block.get("name", "")
                    tool_calls.append(
                        {
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": block.get("input") or {},
                            }
                        }
                    )
                # `thinking` blocks are dropped: Ollama has no field to replay them into.
            entry: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
            continue

        # User turns may interleave text, images and tool results.
        pending_text: list[str] = []
        pending_images: list[str] = []

        def flush() -> None:
            if not pending_text and not pending_images:
                return
            entry: dict[str, Any] = {
                "role": "user",
                "content": "\n".join(p for p in pending_text if p),
            }
            if pending_images:
                entry["images"] = list(pending_images)
            out.append(entry)
            pending_text.clear()
            pending_images.clear()

        for block in blocks:
            kind = block.get("type")
            if kind == "tool_result":
                flush()  # keep the original ordering
                out.append(
                    {
                        "role": "tool",
                        "tool_name": tool_names.get(block.get("tool_use_id", ""), ""),
                        "content": _result_content(block),
                    }
                )
            elif kind == "text":
                pending_text.append(block.get("text", ""))
            elif kind == "image":
                data = _image_data(block)
                if data:
                    pending_images.append(data)
        flush()

    return out


def convert_tools(tools: list[dict] | None) -> list[dict]:
    """Anthropic tool definitions to Ollama's OpenAI-shaped function schemas."""
    converted = []
    for tool in tools or []:
        schema = tool.get("input_schema")
        if not schema:
            # Server-side tools (web_search, etc) have no schema and no local
            # equivalent; forwarding them would only confuse the model.
            continue
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": schema,
                },
            }
        )
    return converted


def build_request(body: dict, cfg: Config) -> dict:
    """Translate one /v1/messages body into an Ollama /api/chat payload."""
    options: dict[str, Any] = {
        "num_ctx": cfg.num_ctx,
        "temperature": float(body.get("temperature", cfg.temperature)),
    }
    if body.get("max_tokens"):
        options["num_predict"] = int(body["max_tokens"])
    if body.get("stop_sequences"):
        options["stop"] = list(body["stop_sequences"])
    if body.get("top_p") is not None:
        options["top_p"] = body["top_p"]
    if body.get("top_k") is not None:
        options["top_k"] = body["top_k"]

    payload: dict[str, Any] = {
        "model": cfg.resolve_model(body.get("model")),
        "messages": convert_messages(body.get("messages", []), body.get("system")),
        "stream": bool(body.get("stream")),
        "options": options,
    }

    tools = convert_tools(body.get("tools"))
    if tools:
        payload["tools"] = tools

    think = cfg.think_field()
    if think is not None:
        payload["think"] = think
    return payload


def tool_use_block(call: dict) -> dict:
    """An Ollama tool call as an Anthropic tool_use block."""
    function = call.get("function") or {}
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    return {
        "type": "tool_use",
        "id": new_tool_id(),
        "name": function.get("name", ""),
        "input": arguments if isinstance(arguments, dict) else {},
    }


def build_response(data: dict, model: str, cfg: Config) -> dict:
    """Translate a non-streaming /api/chat reply into a Messages API response."""
    message = data.get("message") or {}
    text = message.get("content") or ""
    if cfg.strip_thinking:
        text = ThinkFilter(True).strip(text)

    content: list[dict] = []
    if text:
        content.append({"type": "text", "text": text})
    for call in message.get("tool_calls") or []:
        content.append(tool_use_block(call))
    if not content:
        content.append({"type": "text", "text": ""})

    if any(block["type"] == "tool_use" for block in content):
        stop_reason = "tool_use"
    else:
        stop_reason = STOP_REASONS.get(data.get("done_reason", "stop"), "end_turn")

    return {
        "id": new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(data.get("prompt_eval_count") or 0),
            "output_tokens": int(data.get("eval_count") or 0),
        },
    }


def _partial_tail(text: str, token: str) -> int:
    """Length of the longest suffix of ``text`` that starts ``token``."""
    for size in range(min(len(token) - 1, len(text)), 0, -1):
        if token.startswith(text[-size:]):
            return size
    return 0


class ThinkFilter:
    """Removes <think>...</think> spans from a stream arriving in fragments.

    Reasoning models leak these into normal content even when thinking is meant
    to be off, and the tags routinely straddle chunk boundaries, so the filter
    holds back any tail that could still turn into a tag.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        if not self.enabled:
            return chunk
        self._buf += chunk
        emitted: list[str] = []
        while self._buf:
            if self._inside:
                end = self._buf.find(self.CLOSE)
                if end == -1:
                    keep = _partial_tail(self._buf, self.CLOSE)
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[end + len(self.CLOSE) :]
                self._inside = False
                continue
            start = self._buf.find(self.OPEN)
            if start == -1:
                keep = _partial_tail(self._buf, self.OPEN)
                split = len(self._buf) - keep
                emitted.append(self._buf[:split])
                self._buf = self._buf[split:]
                break
            emitted.append(self._buf[:start])
            self._buf = self._buf[start + len(self.OPEN) :]
            self._inside = True
        return "".join(emitted)

    def flush(self) -> str:
        """Anything still held back once the stream ends."""
        if not self.enabled:
            return ""
        tail = "" if self._inside else self._buf
        self._buf = ""
        return tail

    def strip(self, text: str) -> str:
        return self.feed(text) + self.flush()
