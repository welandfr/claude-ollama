"""Config loading: .env parsing and the precedence rules."""

import pytest

from claude_ollama.config import Config, load, parse_env

SAMPLE = """
# a comment
OLLAMA_BASE_URL=http://box.lan:11434/

export CLAUDE_OLLAMA_MODEL=qwen3:30b
CLAUDE_OLLAMA_PORT=9000
CLAUDE_OLLAMA_STRIP_THINKING=false
QUOTED="http://quoted.example"
TRAILING=value  # inline comment
EMPTY=
"""


def test_parse_env_handles_comments_exports_and_quotes():
    values = parse_env(SAMPLE)
    assert values["OLLAMA_BASE_URL"] == "http://box.lan:11434/"
    assert values["CLAUDE_OLLAMA_MODEL"] == "qwen3:30b"
    assert values["QUOTED"] == "http://quoted.example"
    assert values["TRAILING"] == "value"
    assert values["EMPTY"] == ""
    assert "# a comment" not in values


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text)
    return path


def test_env_file_is_read(tmp_path, monkeypatch):
    for key in list(vars(Config())):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("CLAUDE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_OLLAMA_PORT", raising=False)
    monkeypatch.delenv("CLAUDE_OLLAMA_STRIP_THINKING", raising=False)

    cfg = load(write_env(tmp_path, SAMPLE))
    assert cfg.base_url == "http://box.lan:11434"  # trailing slash trimmed
    assert cfg.default_model == "qwen3:30b"
    assert cfg.port == 9000
    assert cfg.strip_thinking is False


def test_real_environment_beats_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://override:1234")
    cfg = load(write_env(tmp_path, SAMPLE))
    assert cfg.base_url == "http://override:1234"
    # Values not overridden still come from the file.
    assert cfg.port == 9000


def test_missing_file_falls_back_to_defaults(tmp_path, monkeypatch):
    for key in ("OLLAMA_BASE_URL", "CLAUDE_OLLAMA_PORT", "CLAUDE_OLLAMA_NUM_CTX"):
        monkeypatch.delenv(key, raising=False)
    cfg = load(tmp_path / "nonexistent")
    assert cfg.base_url == Config.base_url
    assert cfg.port == Config.port
    assert cfg.num_ctx == Config.num_ctx


def test_blank_value_falls_through_to_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_OLLAMA_PORT", raising=False)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_PORT=\n"))
    assert cfg.port == Config.port


def test_unparseable_number_keeps_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_OLLAMA_NUM_CTX", raising=False)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_NUM_CTX=lots\n"))
    assert cfg.num_ctx == Config.num_ctx


@pytest.mark.parametrize(
    "value, expected", [("off", False), ("on", True), ("auto", None), ("nonsense", None)]
)
def test_thinking_values(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("CLAUDE_OLLAMA_THINKING", raising=False)
    cfg = load(write_env(tmp_path, f"CLAUDE_OLLAMA_THINKING={value}\n"))
    assert cfg.think_field() is expected


def test_example_file_matches_the_code():
    """Every key in .env.example must be one load() actually reads."""
    import pathlib

    keys = set(parse_env(pathlib.Path(".env.example").read_text()))
    known = {
        "OLLAMA_BASE_URL",
        "CLAUDE_OLLAMA_TIMEOUT",
        "CLAUDE_OLLAMA_MODEL",
        "CLAUDE_OLLAMA_SMALL_MODEL",
        "CLAUDE_OLLAMA_NUM_CTX",
        "CLAUDE_OLLAMA_TEMPERATURE",
        "CLAUDE_OLLAMA_THINKING",
        "CLAUDE_OLLAMA_STRIP_THINKING",
        "CLAUDE_OLLAMA_DEBUG",
        "CLAUDE_OLLAMA_HOST",
        "CLAUDE_OLLAMA_PORT",
    }
    assert keys == known


def test_small_model_used_for_haiku_and_passthrough_for_bare_names():
    cfg = Config(default_model="qwen3:8b", small_model="qwen3:1.7b")
    assert cfg.resolve_model("claude-haiku-4-5-20251001") == "qwen3:1.7b"
    assert cfg.resolve_model("claude-opus-5") == "qwen3:8b"
    assert cfg.resolve_model("qwen3:30b") == "qwen3:30b"
    assert cfg.resolve_model(None) == "qwen3:8b"
