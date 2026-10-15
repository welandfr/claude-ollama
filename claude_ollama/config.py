"""Configuration from a single .env file.

Precedence, highest first: a variable already set in the real environment, then
the .env file, then the defaults below. That ordering is what lets you keep your
usual setup in .env and still override one value for a single run:

    OLLAMA_BASE_URL=http://gpubox.lan:11434 claude-ollama
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"

# Settings of the proxy this project used to run between Claude Code and Ollama.
# Ignored now; listed so a leftover .env can be pointed out rather than silently
# half-applied.
OBSOLETE = {
    "CLAUDE_OLLAMA_HOST": "the proxy is gone; Claude Code talks to OLLAMA_BASE_URL directly",
    "CLAUDE_OLLAMA_PORT": "the proxy is gone; Claude Code talks to OLLAMA_BASE_URL directly",
    "CLAUDE_OLLAMA_TEMPERATURE": "Ollama's /v1/messages uses what Claude Code sends",
    "CLAUDE_OLLAMA_THINKING": "Ollama's /v1/messages maps Claude Code's thinking setting itself",
    "CLAUDE_OLLAMA_STRIP_THINKING": "Ollama returns reasoning in its own field",
    "CLAUDE_OLLAMA_DEBUG": "read Ollama's server log instead",
}


def env_file() -> Path:
    return Path(os.environ.get("CLAUDE_OLLAMA_ENV") or DEFAULT_ENV)


def obsolete_keys(path: Path | None = None) -> dict[str, str]:
    """Keys in the .env file that nothing reads any more, with why."""
    target = path or env_file()
    if not target.is_file():
        return {}
    present = parse_env(target.read_text())
    return {key: why for key, why in OBSOLETE.items() if key in present}


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
    # The window Claude Code is told about. Ollama's own window is set on the
    # server (OLLAMA_CONTEXT_LENGTH) and must be at least this large.
    num_ctx: int = 65536
    max_output: int = 8192
    default_model: str = "ornith-1.5:9b"
    small_model: str = ""
    # Extra models offered in Claude Code's /model picker, beside default_model.
    models: list[str] = field(default_factory=list)

    def picker_models(self) -> list[str]:
        """The /model rows, default first, without duplicates."""
        return list(dict.fromkeys([self.default_model, *self.models]))

    def allowed_models(self) -> set[str]:
        return {*self.picker_models(), *([self.small_model] if self.small_model else [])}


def load(env_path: str | os.PathLike[str] | None = None) -> Config:
    target = Path(env_path) if env_path else env_file()
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

    return Config(
        base_url=get("OLLAMA_BASE_URL", Config.base_url).rstrip("/"),
        timeout=number("CLAUDE_OLLAMA_TIMEOUT", Config.timeout),
        num_ctx=int(number("CLAUDE_OLLAMA_NUM_CTX", Config.num_ctx)),
        max_output=int(number("CLAUDE_OLLAMA_MAX_OUTPUT", Config.max_output)),
        default_model=get("CLAUDE_OLLAMA_MODEL", Config.default_model),
        small_model=get("CLAUDE_OLLAMA_SMALL_MODEL", ""),
        models=get("CLAUDE_OLLAMA_MODELS", "").replace(",", " ").split(),
    )
