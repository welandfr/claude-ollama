"""Startup checks. The pure logic is tested without touching the network."""

import pytest

from claude_ollama.config import Config
from claude_ollama.preflight import context_shortfall, model_present, pull_command

CFG = Config(default_model="qwen3.5:9b", num_ctx=65536)


@pytest.mark.parametrize(
    "base_url, expected",
    [
        ("http://localhost:11434", "ollama pull qwen3.5:9b"),
        ("http://127.0.0.1:8844", "ollama pull qwen3.5:9b"),
        # The common mistake: pulling to your laptop when Ollama is elsewhere.
        ("http://hem:8844", "OLLAMA_HOST=http://hem:8844 ollama pull qwen3.5:9b"),
        ("http://gpubox.lan:11434", "OLLAMA_HOST=http://gpubox.lan:11434 ollama pull qwen3.5:9b"),
    ],
)
def test_pull_command_targets_the_right_machine(base_url, expected):
    assert pull_command(base_url, "qwen3.5:9b") == expected


@pytest.mark.parametrize(
    "model, available, expected",
    [
        ("qwen3.5:9b", ["qwen3.5:9b"], True),
        ("qwen3.5:9b", ["qwen3:8b"], False),
        # A bare name means :latest to Ollama.
        ("qwen3.5", ["qwen3.5:latest"], True),
        ("qwen3.5", ["qwen3.5:9b"], True),
        ("qwen3.5:9b", ["qwen3.5:latest"], False),
        ("qwen3.5:9b", [], False),
    ],
)
def test_model_present(model, available, expected):
    assert model_present(model, available) is expected


# --- server window ---------------------------------------------------------
# Ollama's /v1/messages takes its window from the server, so a server set lower
# than CLAUDE_OLLAMA_NUM_CTX truncates in silence. /api/ps reports it per model.


def test_a_smaller_server_window_is_caught():
    ps = {"models": [{"name": "qwen3.5:9b", "context_length": 4096}]}
    problems = context_shortfall(ps, CFG)
    assert len(problems) == 1
    assert "4096" in problems[0] and "OLLAMA_CONTEXT_LENGTH=65536" in problems[0]


def test_an_equal_or_larger_server_window_is_fine():
    ps = {"models": [{"name": "a", "context_length": 65536}, {"name": "b", "context_length": 131072}]}
    assert context_shortfall(ps, CFG) == []


def test_nothing_loaded_or_no_field_decides_nothing():
    assert context_shortfall({"models": []}, CFG) == []
    assert context_shortfall({}, CFG) == []
    assert context_shortfall({"models": [{"name": "old-ollama"}]}, CFG) == []


# --- stale project settings -------------------------------------------------
# vscode-setup.sh snapshots .env into a project, and Claude Code applies that env
# block over what the launcher exports, so a snapshot from before a model change
# wins silently. This is the exact file that caused it in practice.

import json

from claude_ollama.ide import env_block
from claude_ollama.preflight import settings_drift

STALE = {
    "env": {
        # Written when Claude Code went through the proxy.
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:8765",
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "ANTHROPIC_MODEL": "qwen3:8b",
        "ANTHROPIC_SMALL_FAST_MODEL": "qwen3:8b",
        "API_TIMEOUT_MS": "600000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
}

DRIFT_CFG = Config(base_url="http://hem:11434", default_model="qwen3.5:9b", num_ctx=65536)


def write(tmp_path, name, data):
    target = tmp_path / ".claude"
    target.mkdir(exist_ok=True)
    (target / name).write_text(json.dumps(data))


def test_stale_model_in_project_settings_is_caught(tmp_path):
    write(tmp_path, "settings.local.json", STALE)
    problems = settings_drift(DRIFT_CFG, tmp_path)
    assert len(problems) == 1
    assert "ANTHROPIC_MODEL is 'qwen3:8b', .env says 'qwen3.5:9b'" in problems[0]
    assert "vscode-setup.sh" in problems[0]  # names the fix


def test_settings_still_pointing_at_the_proxy_are_caught(tmp_path):
    """A project set up in the proxy era would send Claude Code to a dead port."""
    write(tmp_path, "settings.local.json", STALE)
    problems = settings_drift(DRIFT_CFG, tmp_path)
    assert "ANTHROPIC_BASE_URL is 'http://127.0.0.1:8765', .env says 'http://hem:11434'" in problems[0]


def test_missing_context_limit_is_caught(tmp_path):
    write(tmp_path, "settings.local.json", STALE)
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS missing" in settings_drift(DRIFT_CFG, tmp_path)[0]


def test_a_freshly_generated_file_is_clean(tmp_path):
    write(tmp_path, "settings.local.json", {"env": env_block(DRIFT_CFG)})
    assert settings_drift(DRIFT_CFG, tmp_path) == []


def test_both_settings_files_are_examined(tmp_path):
    write(tmp_path, "settings.local.json", {"env": env_block(DRIFT_CFG)})
    write(tmp_path, "settings.json", STALE)
    problems = settings_drift(DRIFT_CFG, tmp_path)
    assert len(problems) == 1 and "settings.json" in problems[0]


def test_settings_without_our_variables_are_left_alone(tmp_path):
    write(tmp_path, "settings.local.json", {"permissions": {"allow": ["Bash(npm *)"]}})
    assert settings_drift(DRIFT_CFG, tmp_path) == []


def test_no_project_settings_is_not_a_problem(tmp_path):
    assert settings_drift(DRIFT_CFG, tmp_path) == []


def test_unreadable_settings_are_reported(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.local.json").write_text("{ not json")
    assert "could not be read" in settings_drift(DRIFT_CFG, tmp_path)[0]
