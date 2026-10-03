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
        "ANTHROPIC_MODEL": main_model,
        "ANTHROPIC_SMALL_FAST_MODEL": small_model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": main_model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": main_model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": small_model,
        "API_TIMEOUT_MS": str(int(cfg.timeout * 1000)),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
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
        "--print-service",
        action="store_true",
        help="print a systemd user unit for the proxy and exit",
    )
    args = parser.parse_args(argv)

    cfg = config_module.load()

    if args.print_service:
        print(service_unit(cfg, config_module.ROOT), end="")
        return 0

    target = Path(args.project).resolve()
    if not target.is_dir():
        raise SystemExit(f"not a directory: {target}")

    values = env_block(cfg)
    path = write_settings(target, values, args.shared)

    print(f"wrote {path}")
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
