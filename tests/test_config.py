"""Config loading: .env parsing and the precedence rules."""

from claude_ollama.config import OBSOLETE, Config, load, obsolete_keys, parse_env

SAMPLE = """
# a comment
OLLAMA_BASE_URL=http://box.lan:11434/

export CLAUDE_OLLAMA_MODEL=qwen3:30b
CLAUDE_OLLAMA_NUM_CTX=32768
QUOTED="http://quoted.example"
TRAILING=value  # inline comment
EMPTY=
"""

KEYS = ("OLLAMA_BASE_URL", "CLAUDE_OLLAMA_MODEL", "CLAUDE_OLLAMA_NUM_CTX", "CLAUDE_OLLAMA_MAX_OUTPUT")


def clear(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


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
    clear(monkeypatch)
    cfg = load(write_env(tmp_path, SAMPLE))
    assert cfg.base_url == "http://box.lan:11434"  # trailing slash trimmed
    assert cfg.default_model == "qwen3:30b"
    assert cfg.num_ctx == 32768


def test_real_environment_beats_the_file(tmp_path, monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://override:1234")
    cfg = load(write_env(tmp_path, SAMPLE))
    assert cfg.base_url == "http://override:1234"
    # Values not overridden still come from the file.
    assert cfg.num_ctx == 32768


def test_missing_file_falls_back_to_defaults(tmp_path, monkeypatch):
    clear(monkeypatch)
    cfg = load(tmp_path / "nonexistent")
    assert cfg.base_url == Config.base_url
    assert cfg.num_ctx == Config.num_ctx
    assert cfg.max_output == Config.max_output


def test_blank_value_falls_through_to_the_default(tmp_path, monkeypatch):
    clear(monkeypatch)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_NUM_CTX=\n"))
    assert cfg.num_ctx == Config.num_ctx


def test_unparseable_number_keeps_the_default(tmp_path, monkeypatch):
    clear(monkeypatch)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_NUM_CTX=lots\n"))
    assert cfg.num_ctx == Config.num_ctx


def test_example_file_matches_the_code():
    """Every key in .env.example must be one load() actually reads."""
    import pathlib

    keys = set(parse_env(pathlib.Path(".env.example").read_text()))
    known = {
        "OLLAMA_BASE_URL",
        "CLAUDE_OLLAMA_TIMEOUT",
        "CLAUDE_OLLAMA_MODEL",
        "CLAUDE_OLLAMA_SMALL_MODEL",
        "CLAUDE_OLLAMA_MODELS",
        "CLAUDE_OLLAMA_NUM_CTX",
        "CLAUDE_OLLAMA_MAX_OUTPUT",
    }
    assert keys == known
    assert not keys & set(OBSOLETE)


def test_models_list_is_parsed_default_first(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_OLLAMA_MODELS", raising=False)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_MODEL=a:9b\nCLAUDE_OLLAMA_MODELS=b:8b, c:4b a:9b\n"))
    assert cfg.picker_models() == ["a:9b", "b:8b", "c:4b"]


def test_no_extra_models_means_just_the_default(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("CLAUDE_OLLAMA_MODELS", raising=False)
    cfg = load(write_env(tmp_path, "CLAUDE_OLLAMA_MODEL=a:9b\n"))
    assert cfg.picker_models() == ["a:9b"]


def test_allowed_models_include_the_small_one():
    cfg = Config(default_model="a:9b", small_model="s:1b", models=["b:8b"])
    assert cfg.allowed_models() == {"a:9b", "b:8b", "s:1b"}


def test_leftover_proxy_settings_are_pointed_out(tmp_path):
    """An .env from the proxy era still parses; its dead keys are named."""
    path = write_env(tmp_path, "CLAUDE_OLLAMA_PORT=8765\nCLAUDE_OLLAMA_THINKING=off\nCLAUDE_OLLAMA_MODEL=a:9b\n")
    assert set(obsolete_keys(path)) == {"CLAUDE_OLLAMA_PORT", "CLAUDE_OLLAMA_THINKING"}


def test_a_current_env_has_nothing_obsolete(tmp_path):
    assert obsolete_keys(write_env(tmp_path, "CLAUDE_OLLAMA_MODEL=a:9b\n")) == {}
    assert obsolete_keys(tmp_path / "missing") == {}
