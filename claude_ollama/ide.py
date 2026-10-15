"""Writes the proxy's environment into a project's Claude Code settings.

The `claude-ollama` launcher exports the right variables before starting Claude
Code, but anything that spawns `claude` on its own — the VS Code extension, for
instance — gets none of them and falls back to a normal login. Claude Code reads
an `env` block from settings.json whatever launched it, so writing the same
values there covers those cases.

Values come from the same loader the proxy uses, so .env stays the only place
settings are edited. Re-run this after changing .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config as config_module

# Variables the launcher sets; see the claude-ollama script for the reasoning.
def env_block(cfg: config_module.Config) -> dict[str, str]:
    main_model = cfg.default_model
    small_model = cfg.small_model or cfg.default_model
    return {
        "ANTHROPIC_BASE_URL": f"http://{cfg.host}:{cfg.port}",
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        # Cleared so a real key in the environment cannot take precedence.
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": main_model,
        "ANTHROPIC_SMALL_FAST_MODEL": small_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": main_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": main_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": small_model,
        "API_TIMEOUT_MS": str(int(cfg.timeout * 1000)),
        # Claude Code assumes 200k for a model it does not recognise; without this
        # it compacts at that and Ollama silently truncates the overflow.
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(cfg.num_ctx),
        # Or the Default row in /model switches to a "[1m]" variant and a 1M window.
        "CLAUDE_CODE_DISABLE_1M_CONTEXT": "1",
        # Its default 32000 makes Claude Code compact at half of a 64K window.
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(cfg.max_output),
        # Side channels to Anthropic, independent of where prompts go.
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        # claude.ai connectors run through Anthropic; off, and without the warning.
        "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    }


def service_unit(cfg: config_module.Config, repo: Path) -> str:
    """A systemd user unit, so the proxy is up without a terminal open."""
    return f"""[Unit]
Description=claude-ollama proxy (Anthropic API over Ollama)
After=network.target

[Service]
ExecStart={repo}/.venv/bin/python -m claude_ollama
WorkingDirectory={repo}
Restart=on-failure
Environment=PYTHONPATH={repo}

[Install]
WantedBy=default.target
"""


def status_line(repo: Path) -> dict[str, str]:
    return {"type": "command", "command": str(repo / "statusline.py")}


def model_picker(cfg: config_module.Config) -> dict:
    """The /model rows: the .env models instead of Claude's own lineup.

    Claude Code honours this only from --settings or user settings, never from a
    project's .claude/, so the launcher carries it and vscode-setup.sh cannot.
    """
    rows = [
        {
            "model": name,
            "label": name,
            "description": "CLAUDE_OLLAMA_MODEL" if name == cfg.default_model else "CLAUDE_OLLAMA_MODELS",
        }
        for name in cfg.picker_models()
    ]
    return {"replaceBuiltInOptions": True, "options": rows}


def user_settings_path() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "settings.json"


def user_model(path: Path) -> str | None:
    try:
        return json.loads(path.read_text() or "{}").get("model")
    except (OSError, json.JSONDecodeError):
        return None


def restore_user_model(path: Path, before: str | None, cfg: config_module.Config) -> bool:
    """Undo a /model pick that Claude Code saved as everyone's default.

    Enter in the picker writes the choice to user settings, where a plain
    `claude` session would then ask Anthropic for an Ollama model; picking the
    Default row instead deletes whatever model was saved. Only those two are
    reverted — any other value was set on purpose.
    """
    now = user_model(path)
    # Picking the Default row deletes the key rather than writing a name.
    ours = now is None or now.removesuffix("[1m]") in cfg.allowed_models()
    if now == before or not ours or not path.is_file():
        return False
    data = json.loads(path.read_text() or "{}")
    if before is None:
        data.pop("model", None)
    else:
        data["model"] = before
    path.write_text(json.dumps(data, indent=2) + "\n")
    return True


def write_settings(target: Path, values: dict[str, str], shared: bool) -> Path:
    name = "settings.json" if shared else "settings.local.json"
    path = target / ".claude" / name
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{path} is not valid JSON ({exc}); fix or move it first")

    # Merge rather than replace: the file may already carry hooks or permissions.
    existing["env"] = {**existing.get("env", {}), **values}
    # Only if there is none: a status line the user wrote is theirs to keep.
    existing.setdefault("statusLine", status_line(config_module.ROOT))
    path.write_text(json.dumps(existing, indent=2) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="claude-ollama-ide",
        description="Point a project's Claude Code at the local Ollama proxy.",
    )
    parser.add_argument(
        "project", nargs="?", default=".", help="project directory (default: current)"
    )
    parser.add_argument(
        "--shared",
        action="store_true",
        help="write .claude/settings.json (committed) instead of settings.local.json",
    )
    parser.add_argument(
        "--print-settings",
        action="store_true",
        help="print session settings JSON for claude --settings, and exit",
    )
    parser.add_argument(
        "--no-statusline",
        action="store_true",
        help="with --print-settings: leave the status line out",
    )
    parser.add_argument(
        "--get-user-model",
        action="store_true",
        help="print the model saved in user settings as JSON (null if none), and exit",
    )
    parser.add_argument(
        "--restore-user-model",
        metavar="JSON",
        help="put back a user-settings model recorded by --get-user-model",
    )
    parser.add_argument(
        "--print-service",
        action="store_true",
        help="print a systemd user unit for the proxy and exit",
    )
    args = parser.parse_args(argv)

    cfg = config_module.load()

    if args.print_settings:
        # Passed to `claude --settings` by the launcher, so every session gets
        # these without installing anything per project.
        settings: dict = {"modelPicker": model_picker(cfg)}
        if not args.no_statusline:
            settings["statusLine"] = status_line(config_module.ROOT)
        print(json.dumps(settings))
        return 0

    if args.get_user_model:
        print(json.dumps(user_model(user_settings_path())))
        return 0

    if args.restore_user_model is not None:
        path = user_settings_path()
        if restore_user_model(path, json.loads(args.restore_user_model), cfg):
            print(f"restored the model in {path} (a /model pick had replaced it)")
        return 0

    if args.print_service:
        print(service_unit(cfg, config_module.ROOT), end="")
        return 0

    target = Path(args.project).resolve()
    if not target.is_dir():
        raise SystemExit(f"not a directory: {target}")

    values = env_block(cfg)
    path = write_settings(target, values, args.shared)

    print(f"wrote {path}")
    if existing_status := json.loads(path.read_text()).get("statusLine"):
        print(f"  statusLine: {existing_status.get('command', existing_status)}")
    print(f"  ANTHROPIC_BASE_URL={values['ANTHROPIC_BASE_URL']}")
    print(f"  ANTHROPIC_MODEL={values['ANTHROPIC_MODEL']}")
    if not args.shared:
        gitignore = target / ".gitignore"
        ignored = gitignore.is_file() and "settings.local.json" in gitignore.read_text()
        if not ignored:
            print("  note: add .claude/settings.local.json to that project's .gitignore")
    print("\nThe proxy must be running for this to work — see README (systemd user unit).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
