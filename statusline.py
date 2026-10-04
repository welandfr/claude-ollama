#!/usr/bin/env python3
"""Status line for Claude Code: model name and context usage.

Claude Code pipes session JSON to this on stdin and renders one line of output.
Wired up by vscode-setup.sh, or by hand:

    "statusLine": { "type": "command", "command": "/path/to/statusline.py" }

The window it reports comes from CLAUDE_CODE_MAX_CONTEXT_TOKENS, which the
launcher derives from CLAUDE_OLLAMA_NUM_CTX — so a window of 200K showing up
here means that variable is not reaching Claude Code and it has fallen back to
its assumption for an unrecognised model.
"""

from __future__ import annotations

import json
import sys


def human(tokens: float) -> str:
    """Token counts in K.

    Context windows are binary (num_ctx 65536 is understood as 64K), while token
    counts are decimal (480000 reads as 480K), so divide by whichever the value
    is actually a round multiple of.
    """
    value = int(tokens)
    if value and value % 1024 == 0:
        return f"{value // 1024}K"
    if value >= 1000:
        return f"{round(value / 1000)}K"
    return str(value)


def render(data: dict) -> str:
    model = (data.get("model") or {}).get("display_name") or (
        data.get("model") or {}
    ).get("id") or "unknown model"

    window = data.get("context_window") or {}
    size = window.get("context_window_size")
    used = window.get("total_input_tokens")
    percent = window.get("used_percentage")

    if not size or used is None:
        # Before the first API response there is no usage to report.
        return model

    parts = f"{human(used)}/{human(size)}"
    if percent is not None:
        parts += f" ({percent:.0f}%)"
    return f"{model} | Context: {parts}"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never let a status line break the session
    print(render(data if isinstance(data, dict) else {}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
