#!/usr/bin/env bash
# Installs the proxy into a local virtualenv and checks the surrounding pieces.
set -euo pipefail

cd "$(dirname "$0")"

python3 - <<'PY'
import sys
if sys.version_info < (3, 11):
    sys.exit(f"Python 3.11+ required (tomllib); found {sys.version.split()[0]}")
PY

# A venv carries absolute paths, so one built elsewhere (another checkout path,
# a container) is dead on arrival. Testing that bin/python merely runs is not
# enough: it symlinks to a system interpreter that works fine while sys.prefix
# still points elsewhere. Checking that pip imports and the prefix is ours
# catches both a relocated venv and a half-copied one.
if [ -e .venv ] && ! ./.venv/bin/python -c \
     "import sys, pip; sys.exit(0 if sys.prefix == '$PWD/.venv' else 1)" >/dev/null 2>&1; then
  echo "existing .venv is unusable here; rebuilding"
  rm -rf .venv
fi

if [ ! -x .venv/bin/python ]; then
  echo "creating .venv"
  python3 -m venv .venv
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example -- edit it to match your Ollama"
fi

echo "installing dependencies"
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

chmod +x run.sh claude-ollama vscode-setup.sh

# Put claude-ollama on PATH so it can be used from any project directory. The
# symlink points back here, and the launcher resolves it to find this checkout.
LINK_DIR="${BIN_DIR:-$HOME/.local/bin}"
LINK="$LINK_DIR/claude-ollama"
TARGET="$PWD/claude-ollama"

mkdir -p "$LINK_DIR"
if [ -L "$LINK" ] || [ ! -e "$LINK" ]; then
  ln -sfn "$TARGET" "$LINK"
  echo "linked $LINK -> $TARGET"
elif [ "$LINK" = "$TARGET" ]; then
  : # setup.sh was run from the link directory itself
else
  echo "NOT linking: $LINK already exists and is not a symlink; remove it or set BIN_DIR" >&2
fi

echo
echo "--- checks ---"

if command -v claude >/dev/null 2>&1; then
  echo "claude:  $(command -v claude) ($(claude --version 2>/dev/null || echo 'version unknown'))"
else
  echo "claude:  NOT FOUND. Install it with:"
  echo "           curl -fsSL https://claude.ai/install.sh | bash"
fi

base_url=$(./.venv/bin/python -c 'from claude_ollama import config; print(config.load().base_url)')
if curl -fsS -m 5 "$base_url/api/tags" >/dev/null 2>&1; then
  echo "ollama:  reachable at $base_url"
  ./.venv/bin/python - <<PY
import json, urllib.request
with urllib.request.urlopen("$base_url/api/tags", timeout=5) as r:
    for m in json.load(r).get("models", []):
        print("           -", m["name"])
PY
else
  echo "ollama:  NOT reachable at $base_url -- fix OLLAMA_BASE_URL in .env"
fi

case ":$PATH:" in
  *":$LINK_DIR:"*) echo "path:    $LINK_DIR is on your PATH" ;;
  *) echo "path:    $LINK_DIR is NOT on your PATH -- add it to your shell profile:"
     echo "           export PATH=\"$LINK_DIR:\$PATH\"" ;;
esac

echo
echo "done. Run claude-ollama from any project directory."
