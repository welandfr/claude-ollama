"""Settings generation for editor-launched Claude Code."""

import json

import pytest

from claude_ollama.config import Config
from claude_ollama.ide import env_block, main, service_unit, write_settings

CFG = Config(host="127.0.0.1", port=9999, default_model="qwen3:8b", small_model="qwen3:1.7b", timeout=300.0)


def test_env_block_points_at_the_proxy():
    values = env_block(CFG)
    assert values["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9999"
    assert values["ANTHROPIC_MODEL"] == "qwen3:8b"
    assert values["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen3:1.7b"
    assert values["API_TIMEOUT_MS"] == "300000"
    # Must match num_ctx, or Claude Code compacts at its assumed 200k and Ollama
    # truncates the overflow without saying so.
    assert values["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(Config.num_ctx)
    # A non-empty token is what keeps Claude Code from showing a login screen.
    assert values["ANTHROPIC_AUTH_TOKEN"]


def test_small_model_falls_back_to_the_main_one():
    values = env_block(Config(default_model="only", small_model=""))
    assert values["ANTHROPIC_SMALL_FAST_MODEL"] == "only"


def test_writes_local_settings_by_default(tmp_path):
    path = write_settings(tmp_path, env_block(CFG), shared=False)
    assert path == tmp_path / ".claude" / "settings.local.json"
    assert json.loads(path.read_text())["env"]["ANTHROPIC_BASE_URL"].endswith(":9999")


def test_shared_flag_writes_the_committed_file(tmp_path):
    path = write_settings(tmp_path, env_block(CFG), shared=True)
    assert path.name == "settings.json"


def test_existing_settings_are_merged_not_replaced(tmp_path):
    target = tmp_path / ".claude"
    target.mkdir()
    (target / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(npm *)"]}, "env": {"MINE": "keep"}})
    )
    path = write_settings(tmp_path, env_block(CFG), shared=False)
    data = json.loads(path.read_text())
    assert data["permissions"] == {"allow": ["Bash(npm *)"]}
    assert data["env"]["MINE"] == "keep"
    assert "ANTHROPIC_BASE_URL" in data["env"]


def test_malformed_settings_are_refused_rather_than_overwritten(tmp_path):
    target = tmp_path / ".claude"
    target.mkdir()
    broken = target / "settings.local.json"
    broken.write_text("{ not json")
    with pytest.raises(SystemExit, match="not valid JSON"):
        write_settings(tmp_path, env_block(CFG), shared=False)
    assert broken.read_text() == "{ not json"  # untouched


def test_main_rejects_a_missing_directory(tmp_path):
    with pytest.raises(SystemExit, match="not a directory"):
        main([str(tmp_path / "nope")])


def test_service_unit_uses_absolute_paths(tmp_path):
    unit = service_unit(CFG, tmp_path)
    assert f"ExecStart={tmp_path}/.venv/bin/python -m claude_ollama" in unit
    assert f"Environment=PYTHONPATH={tmp_path}" in unit
