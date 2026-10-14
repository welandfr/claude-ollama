"""Startup checks for the launcher.

Three things fail quietly enough to waste real time: Ollama not running, the
configured model not pulled, and a proxy still serving an older .env because it
was started before the file changed. The last one is the nastiest — the session
works, it just uses the wrong model — so all three are treated as errors with the
exact command to fix them.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import config as config_module
from . import source

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Written by vscode-setup.sh; local overrides the committed one.
SETTINGS_FILES = (".claude/settings.local.json", ".claude/settings.json")


def pull_command(base_url: str, model: str) -> str:
    """`ollama pull` aimed at the right machine, not the caller's localhost."""
    host = urlparse(base_url).hostname or ""
    if host in LOCAL_HOSTS:
        return f"ollama pull {model}"
    return f"OLLAMA_HOST={base_url} ollama pull {model}"


def model_present(model: str, available: list[str]) -> bool:
    """Ollama resolves a bare name to its :latest tag, so accept either form."""
    if model in available or f"{model}:latest" in available:
        return True
    if ":" not in model:
        return any(name.split(":", 1)[0] == model for name in available)
    return False


def proxy_mismatch(health: dict, cfg: config_module.Config, code: str | None = None) -> list[str]:
    """Differences between a running proxy and this checkout."""
    out = []
    expected = code or source.fingerprint()
    running = health.get("code")
    if running != expected:
        # A proxy old enough to predate this field reports nothing, which is
        # itself proof it is out of date.
        was = f"code {running}" if running else "code from before this check existed"
        out.append(f"{was}, checkout is {expected}")
    running_model = health.get("default_model")
    if running_model and running_model != cfg.default_model:
        out.append(f"model {running_model!r}, .env now says {cfg.default_model!r}")
    running_models = health.get("models")
    if running_models is not None and running_models != cfg.picker_models():
        # It would answer a /model pick it does not know with the default.
        out.append(f"models {running_models}, .env now says {cfg.picker_models()}")
    running_ctx = health.get("num_ctx")
    if running_ctx and int(running_ctx) != cfg.num_ctx:
        out.append(f"num_ctx {running_ctx}, .env now says {cfg.num_ctx}")
    return out


def settings_drift(cfg: config_module.Config, cwd: Path) -> list[str]:
    """Project settings left behind by an earlier .env.

    vscode-setup.sh writes a snapshot of .env into a project, and Claude Code
    applies that `env` block over the environment the launcher exports — so a
    snapshot from before a model change silently wins. Nothing else notices.
    """
    from .ide import env_block

    expected = env_block(cfg)
    problems: list[str] = []

    for relative in SETTINGS_FILES:
        path = cwd / relative
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text() or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            problems.append(f"{path} could not be read ({exc}).")
            continue

        env = data.get("env") or {}
        if not any(k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) for k in env):
            continue

        diffs = [
            f"{key} is {env[key]!r}, .env says {want!r}"
            for key, want in expected.items()
            if key in env and env[key] != want
        ]
        # Only this one is reported when absent: without it Claude Code assumes a
        # 200k window and Ollama truncates the overflow in silence.
        if "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env:
            diffs.append(
                "CLAUDE_CODE_MAX_CONTEXT_TOKENS missing, should be "
                f"{expected['CLAUDE_CODE_MAX_CONTEXT_TOKENS']!r}"
            )
        if diffs:
            problems.append(
                f"{path} is out of date, and Claude Code applies it over what the\n"
                "  launcher exports:\n"
                + "".join(f"    - {d}\n" for d in diffs)
                + f"  Regenerate it:\n    {config_module.ROOT}/vscode-setup.sh {cwd}"
            )
    return problems


def _get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as reply:
        return json.load(reply)


def check(cfg: config_module.Config) -> list[str]:
    """Returns a list of problems, each ending in what to run."""
    problems: list[str] = list(settings_drift(cfg, Path.cwd()))

    try:
        tags = _get(f"{cfg.base_url}/api/tags", 5.0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        problems.append(
            f"Ollama is not reachable at {cfg.base_url} ({exc}).\n"
            f"  Start it, or fix OLLAMA_BASE_URL in .env."
        )
        return problems

    available = [m.get("name", "") for m in tags.get("models", [])]
    wanted = cfg.picker_models() + ([cfg.small_model] if cfg.small_model else [])
    for model in dict.fromkeys(wanted):  # de-duplicated, order kept
        if not model_present(model, available):
            listed = ", ".join(available) or "none"
            problems.append(
                f"Model {model!r} is not on {cfg.base_url} (has: {listed}).\n"
                f"  {pull_command(cfg.base_url, model)}"
            )

    # Only meaningful if something is already listening; a fresh start cannot drift.
    try:
        health = _get(f"http://{cfg.host}:{cfg.port}/health", 2.0)
    except (urllib.error.URLError, OSError, ValueError):
        return problems

    drift = proxy_mismatch(health, cfg)
    if drift:
        problems.append(
            f"The proxy already running on {cfg.host}:{cfg.port} does not match this "
            "checkout:\n"
            + "".join(f"    - {d}\n" for d in drift)
            + "  It is reused rather than restarted, so this session would run the\n"
            "  old code and settings. Restart it:\n"
            "    systemctl --user restart claude-ollama   (or pkill -f claude_ollama)"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    cfg = config_module.load()
    problems = check(cfg)
    if not problems:
        return 0
    print("claude-ollama: startup checks failed\n", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}\n", file=sys.stderr)
    print(
        "  Set CLAUDE_OLLAMA_SKIP_CHECKS=1 to launch anyway.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
