"""Startup checks. The pure logic is tested without touching the network."""

import pytest

from claude_ollama.config import Config
from claude_ollama.preflight import model_present, proxy_mismatch, pull_command

CFG = Config(default_model="qwen3.5:9b", num_ctx=65536, host="127.0.0.1", port=8765)


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


def test_mismatch_detects_stale_config():
    health = {"default_model": "qwen3:8b", "num_ctx": 32768, "code": "abc"}
    drift = proxy_mismatch(health, CFG, code="abc")
    assert any("qwen3:8b" in d and "qwen3.5:9b" in d for d in drift)
    assert any("32768" in d and "65536" in d for d in drift)


def test_mismatch_detects_stale_code_even_when_config_agrees():
    """A long-running systemd proxy keeps serving whatever it started with."""
    health = {"default_model": "qwen3.5:9b", "num_ctx": 65536, "code": "oldhash"}
    drift = proxy_mismatch(health, CFG, code="newhash")
    assert drift == ["code oldhash, checkout is newhash"]


def test_a_proxy_predating_the_check_counts_as_stale():
    health = {"default_model": "qwen3.5:9b", "num_ctx": 65536}
    drift = proxy_mismatch(health, CFG, code="newhash")
    assert "before this check existed" in drift[0]


def test_mismatch_detects_a_changed_model_list():
    """An old list would answer a fresh /model pick with the default."""
    health = {"default_model": "qwen3.5:9b", "models": ["qwen3.5:9b"], "num_ctx": 65536, "code": "same"}
    cfg = Config(default_model="qwen3.5:9b", models=["qwen3:8b"], num_ctx=65536)
    drift = proxy_mismatch(health, cfg, code="same")
    assert len(drift) == 1 and "qwen3:8b" in drift[0]


def test_no_mismatch_when_the_proxy_agrees():
    health = {"default_model": "qwen3.5:9b", "num_ctx": 65536, "code": "same"}
    assert proxy_mismatch(health, CFG, code="same") == []


def test_fingerprint_is_stable_and_short():
    from claude_ollama.source import fingerprint

    assert fingerprint() == fingerprint()
    assert len(fingerprint()) == 12


# --- stale project settings -------------------------------------------------
# vscode-setup.sh snapshots .env into a project, and Claude Code applies that env
# block over what the launcher exports, so a snapshot from before a model change
# wins silently. This is the exact file that caused it in practice.

import json

from claude_ollama.ide import env_block
from claude_ollama.preflight import settings_drift

STALE = {
    "env": {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:8765",
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "ANTHROPIC_MODEL": "qwen3:8b",
        "ANTHROPIC_SMALL_FAST_MODEL": "qwen3:8b",
        "API_TIMEOUT_MS": "600000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
}

DRIFT_CFG = Config(default_model="qwen3.5:9b", num_ctx=65536, host="127.0.0.1", port=8765)


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
