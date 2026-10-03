"""Translation tests. These need no Ollama server."""

import json

import pytest

from claude_ollama.config import Config
from claude_ollama.translate import (
    ThinkFilter,
    build_request,
    build_response,
    convert_messages,
    convert_tools,
    system_text,
)

CFG = Config(default_model="qwen3:8b", small_model="qwen3:1.7b", num_ctx=8192)


def test_system_accepts_string_and_blocks():
    assert system_text("be terse") == "be terse"
    blocks = [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
    assert system_text(blocks) == "one\n\ntwo"


def test_system_becomes_leading_message():
    out = convert_messages([{"role": "user", "content": "hi"}], system="rules")
    assert out[0] == {"role": "system", "content": "rules"}
    assert out[1] == {"role": "user", "content": "hi"}


def test_tool_use_becomes_ollama_tool_call():
    messages = [
        {"role": "user", "content": "read a file"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "sure"},
                {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "a.py"}},
            ],
        },
    ]
    out = convert_messages(messages)
    assert out[-1]["tool_calls"] == [
        {"function": {"name": "Read", "arguments": {"path": "a.py"}}}
    ]


def test_tool_result_becomes_tool_message_matched_by_name():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu_1", "name": "Read", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "file body"}],
        },
    ]
    out = convert_messages(messages)
    assert out[-1] == {"role": "tool", "tool_name": "Read", "content": "file body"}


def test_tool_error_is_marked():
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "boom", "is_error": True}
            ],
        },
    ]
    assert convert_messages(messages)[-1]["content"] == "Error: boom"


def test_user_text_before_tool_result_keeps_its_place():
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "Grep", "input": {}}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "note first"},
                {"type": "tool_result", "tool_use_id": "t", "content": "hits"},
            ],
        },
    ]
    roles = [m["role"] for m in convert_messages(messages)]
    assert roles == ["assistant", "user", "tool"]


def test_images_move_to_the_images_field():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
            ],
        }
    ]
    out = convert_messages(messages)
    assert out[0]["images"] == ["QUJD"]


def test_tools_without_schema_are_dropped():
    tools = [
        {"name": "Read", "description": "read", "input_schema": {"type": "object"}},
        {"type": "web_search_20250305", "name": "web_search"},
    ]
    converted = convert_tools(tools)
    assert len(converted) == 1
    assert converted[0]["function"]["name"] == "Read"


def test_build_request_sets_context_and_limits():
    body = {
        "model": "claude-opus-5",
        "max_tokens": 2048,
        "stop_sequences": ["STOP"],
        "messages": [{"role": "user", "content": "hi"}],
    }
    payload = build_request(body, CFG)
    assert payload["model"] == "qwen3:8b"
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["num_predict"] == 2048
    assert payload["options"]["stop"] == ["STOP"]
    assert payload["think"] is False


def test_haiku_requests_use_the_small_model():
    payload = build_request({"model": "claude-haiku-4-5-20251001", "messages": []}, CFG)
    assert payload["model"] == "qwen3:1.7b"


def test_response_with_tool_call_stops_for_tool_use():
    data = {
        "message": {"content": "", "tool_calls": [{"function": {"name": "Read", "arguments": {"p": 1}}}]},
        "done_reason": "stop",
        "prompt_eval_count": 120,
        "eval_count": 8,
    }
    out = build_response(data, "qwen3:8b", CFG)
    assert out["stop_reason"] == "tool_use"
    assert out["content"][0]["type"] == "tool_use"
    assert out["content"][0]["id"].startswith("toolu_")
    assert out["usage"] == {"input_tokens": 120, "output_tokens": 8}


def test_response_maps_length_to_max_tokens():
    data = {"message": {"content": "cut off"}, "done_reason": "length"}
    assert build_response(data, "m", CFG)["stop_reason"] == "max_tokens"


def test_response_is_never_empty():
    out = build_response({"message": {"content": ""}}, "m", CFG)
    assert out["content"] == [{"type": "text", "text": ""}]


def test_string_arguments_are_parsed():
    data = {"message": {"tool_calls": [{"function": {"name": "X", "arguments": '{"a": 1}'}}]}}
    assert build_response(data, "m", CFG)["content"][0]["input"] == {"a": 1}


@pytest.mark.parametrize(
    "chunks, expected",
    [
        (["plain text"], "plain text"),
        (["<think>hidden</think>shown"], "shown"),
        (["a<th", "ink>b</thi", "nk>c"], "ac"),
        (["<think>only reasoning</think>"], ""),
        (["keep < this"], "keep < this"),
        # A dangling partial tag was never a tag, so it survives as literal text.
        (["trailing <thi"], "trailing <thi"),
    ],
)
def test_think_filter(chunks, expected):
    f = ThinkFilter(True)
    assert "".join(f.feed(c) for c in chunks) + f.flush() == expected


def test_think_filter_disabled_is_a_passthrough():
    f = ThinkFilter(False)
    assert f.feed("<think>x</think>") == "<think>x</think>"


def test_cache_control_is_ignored_rather_than_forwarded():
    """Claude Code marks blocks for prompt caching; Ollama has no equivalent.

    The launcher leaves caching enabled, so these keys arrive on real requests
    and must simply be dropped.
    """
    body = {
        "model": "claude-opus-5",
        "max_tokens": 32,
        "system": [{"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}],
        "tools": [
            {
                "name": "Read",
                "description": "read",
                "input_schema": {"type": "object"},
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}
                ],
            }
        ],
    }
    payload = build_request(body, CFG)
    assert payload["messages"] == [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "hi"},
    ]
    assert payload["tools"][0]["function"] == {
        "name": "Read",
        "description": "read",
        "parameters": {"type": "object"},
    }
    assert "cache_control" not in str(payload)
