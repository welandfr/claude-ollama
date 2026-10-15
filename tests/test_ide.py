"""Settings generation for editor-launched Claude Code."""

import json

import pytest

from claude_ollama.config import Config
from claude_ollama.ide import env_block, main, model_picker, restore_user_model, service_unit, write_settings

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
    # Without it, picking Default in /model moves to a 1M window anyway.
    assert values["CLAUDE_CODE_DISABLE_1M_CONTEXT"] == "1"
    # Claude Code's own 32000 would make it compact at half the window.
    assert values["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(Config.max_output)
    # Connectors would route through Anthropic; this also silences their warning.
    assert values["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false"
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


def test_status_line_is_added_when_absent(tmp_path):
    path = write_settings(tmp_path, env_block(CFG), shared=False)
    data = json.loads(path.read_text())
    assert data["statusLine"]["type"] == "command"
    assert data["statusLine"]["command"].endswith("statusline.py")


def test_an_existing_status_line_is_left_alone(tmp_path):
    """Someone's own status line is theirs; only fill in a missing one."""
    target = tmp_path / ".claude"
    target.mkdir()
    mine = {"type": "command", "command": "/home/me/my-statusline.sh"}
    (target / "settings.local.json").write_text(json.dumps({"statusLine": mine}))
    path = write_settings(tmp_path, env_block(CFG), shared=False)
    assert json.loads(path.read_text())["statusLine"] == mine


def test_print_settings_emits_valid_json_for_the_settings_flag(capsys):
    """The launcher passes this to `claude --settings`, so it must parse."""
    assert main(["--print-settings"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["statusLine"]["type"] == "command"
    assert data["statusLine"]["command"].endswith("statusline.py")
    assert set(data) == {"statusLine", "modelPicker"}  # nothing else leaks into every session


def test_print_settings_can_leave_the_status_line_out(capsys):
    assert main(["--print-settings", "--no-statusline"]) == 0
    assert set(json.loads(capsys.readouterr().out)) == {"modelPicker"}


def test_model_picker_lists_env_models_in_place_of_claude_ones():
    cfg = Config(default_model="a:9b", models=["b:8b", "a:9b"])
    picker = model_picker(cfg)
    assert picker["replaceBuiltInOptions"] is True
    assert [row["model"] for row in picker["options"]] == ["a:9b", "b:8b"]
    assert all(row["label"] == row["model"] for row in picker["options"])


PICK_CFG = Config(default_model="a:9b", models=["b:8b"])


def test_a_saved_pick_of_ours_is_reverted(tmp_path):
    """Enter in /model writes user settings, which plain `claude` also reads."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "b:8b", "theme": "dark"}))
    assert restore_user_model(path, "opus", PICK_CFG)
    assert json.loads(path.read_text()) == {"model": "opus", "theme": "dark"}


def test_a_pick_is_removed_when_there_was_no_model_before(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "a:9b"}))
    assert restore_user_model(path, None, PICK_CFG)
    assert json.loads(path.read_text()) == {}


def test_a_model_deleted_by_the_default_row_is_put_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "dark"}))
    assert restore_user_model(path, "opus", PICK_CFG)
    assert json.loads(path.read_text()) == {"theme": "dark", "model": "opus"}


@pytest.mark.parametrize("now", ["opus", "claude-sonnet-5-5"])
def test_models_that_are_not_ours_are_left_alone(tmp_path, now):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({} if now is None else {"model": now}))
    before = path.read_text()
    assert not restore_user_model(path, "sonnet", PICK_CFG)
    assert path.read_text() == before


def test_missing_user_settings_are_not_created(tmp_path):
    path = tmp_path / "settings.json"
    assert not restore_user_model(path, None, PICK_CFG)
    assert not path.exists()
