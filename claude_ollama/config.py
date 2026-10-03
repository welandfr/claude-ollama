"""Configuration from a single .env file.

Precedence, highest first: a variable already set in the real environment, then
the .env file, then the defaults below. That ordering is what lets you keep your
usual setup in .env and still override one value for a single run:

    OLLAMA_BASE_URL=http://gpubox.lan:11434 claude-ollama
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"

TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}


def parse_env(text: str) -> dict[str, str]:
    """Read KEY=value lines, ignoring comments, blanks and a leading `export`."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


@dataclass
class Config:
    base_url: str = "http://localhost:11434"
    timeout: float = 600.0
    host: str = "127.0.0.1"
    port: int = 8765
    num_ctx: int = 65536
    temperature: float = 0.0
    thinking: str = "off"
    strip_thinking: bool = True
    default_model: str = "qwen3.5:9b"
    small_model: str = ""
    debug: bool = False

    def resolve_model(self, requested: str | None) -> str:
        """Map a model id from Claude Code onto an Ollama model name."""
        name = (requested or "").strip()
        low = name.lower()
        if self.small_model and ("haiku" in low or "small" in low):
            return self.small_model
        # A bare Ollama name (CLAUDE_OLLAMA_MODEL=qwen3.5:9b) passes straight through.
        if name and not low.startswith("claude"):
            return name
        return self.default_model or name

    def think_field(self) -> bool | None:
        return {"off": False, "on": True}.get(self.thinking)


def load(env_path: str | os.PathLike[str] | None = None) -> Config:
    target = Path(env_path or os.environ.get("CLAUDE_OLLAMA_ENV") or DEFAULT_ENV)
    from_file = parse_env(target.read_text()) if target.is_file() else {}

    def get(key: str, fallback: str) -> str:
        # The real environment wins, so a one-off override needs no file edit.
        value = os.environ.get(key)
        if value is None or value == "":
            value = from_file.get(key, "")
        return value if value != "" else fallback

    def number(key: str, fallback: float) -> float:
        try:
            return float(get(key, str(fallback)))
        except ValueError:
            return fallback

    def flag(key: str, fallback: bool) -> bool:
        value = get(key, "").lower()
        if value in TRUTHY:
            return True
        if value in FALSEY:
            return False
        return fallback

    cfg = Config(
        base_url=get("OLLAMA_BASE_URL", Config.base_url).rstrip("/"),
        timeout=number("CLAUDE_OLLAMA_TIMEOUT", Config.timeout),
        host=get("CLAUDE_OLLAMA_HOST", Config.host),
        port=int(number("CLAUDE_OLLAMA_PORT", Config.port)),
        num_ctx=int(number("CLAUDE_OLLAMA_NUM_CTX", Config.num_ctx)),
        temperature=number("CLAUDE_OLLAMA_TEMPERATURE", Config.temperature),
        thinking=get("CLAUDE_OLLAMA_THINKING", Config.thinking).lower(),
        strip_thinking=flag("CLAUDE_OLLAMA_STRIP_THINKING", Config.strip_thinking),
        default_model=get("CLAUDE_OLLAMA_MODEL", Config.default_model),
        small_model=get("CLAUDE_OLLAMA_SMALL_MODEL", ""),
        debug=flag("CLAUDE_OLLAMA_DEBUG", Config.debug),
    )
    if cfg.thinking not in {"off", "on", "auto"}:
        cfg.thinking = "auto"
    return cfg
