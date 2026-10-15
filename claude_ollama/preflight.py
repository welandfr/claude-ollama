"""Startup checks for the launcher.

These fail quietly enough to waste real time: Ollama not reachable, a configured
model not pulled, project settings left over from an older .env, and an Ollama
context window smaller than the one Claude Code is told about. The last two are
the nastiest — the session works, it just runs the wrong model or loses the
start of the conversation — so all are treated as errors with the exact fix.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from . import config as config_module

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


def context_shortfall(ps: dict, cfg: config_module.Config) -> list[str]:
    """Loaded models whose window is smaller than the one Claude Code assumes.

    Ollama's /v1/messages takes its window from the server (OLLAMA_CONTEXT_LENGTH);
    nothing on the client side can raise it. Claude Code compacts against
    CLAUDE_OLLAMA_NUM_CTX, so a smaller server window lets the conversation grow
    past what Ollama keeps, and Ollama drops the overflow without saying so.
    Only a loaded model reports its window, so an idle server cannot be checked.
    """
    out = []
    for entry in ps.get("models") or []:
        window = entry.get("context_length")
        if isinstance(window, int) and 0 < window < cfg.num_ctx:
            out.append(
                f"Ollama has {entry.get('name', '?')} loaded with a {window}-token context, "
                f"but CLAUDE_OLLAMA_NUM_CTX is {cfg.num_ctx}.\n"
                f"  Claude Code would let the conversation outgrow it. Set\n"
                f"  OLLAMA_CONTEXT_LENGTH={cfg.num_ctx} on the Ollama server, or lower\n"
                f"  CLAUDE_OLLAMA_NUM_CTX in .env to {window}."
            )
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

    try:
        problems.extend(context_shortfall(_get(f"{cfg.base_url}/api/ps", 5.0), cfg))
    except (urllib.error.URLError, OSError, ValueError):
        pass  # reachability was established above; an unanswered /api/ps decides nothing
    return problems


def main(argv: list[str] | None = None) -> int:
    for key, why in config_module.obsolete_keys().items():
        print(f"claude-ollama: {key} in .env is no longer used ({why}); remove it", file=sys.stderr)
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
