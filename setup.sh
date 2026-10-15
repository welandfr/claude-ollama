#!/usr/bin/env bash
# Creates .env, puts claude-ollama on PATH and checks the surrounding pieces.
# Nothing is installed: the launcher needs only bash, curl and python3's
# standard library.
set -euo pipefail

cd "$(dirname "$0")"

python3 - <<'PY'
import sys
if sys.version_info < (3, 9):
    sys.exit(f"Python 3.9+ required; found {sys.version.split()[0]}")
PY

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example -- edit it to match your Ollama"
fi

chmod +x claude-ollama vscode-setup.sh statusline.py

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

base_url=$(python3 -c 'from claude_ollama import config; print(config.load().base_url)')
if curl -fsS -m 5 "$base_url/api/tags" >/dev/null 2>&1; then
  echo "ollama:  reachable at $base_url"
  python3 - "$base_url" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(f"{sys.argv[1]}/api/tags", timeout=5) as r:
    for m in json.load(r).get("models", []):
        print("           -", m["name"])
PY
else
  echo "ollama:  NOT reachable at $base_url -- fix OLLAMA_BASE_URL in .env"
fi

python3 - <<'PY'
from claude_ollama import config
for key, why in config.obsolete_keys().items():
    print(f".env:    {key} is no longer used ({why}); remove it")
PY

# Left over from when this project ran a proxy; nothing uses it now.
if [ -d .venv ]; then
  echo "venv:    .venv is no longer needed; remove it with: rm -rf $PWD/.venv"
fi

case ":$PATH:" in
  *":$LINK_DIR:"*) echo "path:    $LINK_DIR is on your PATH" ;;
  *) echo "path:    $LINK_DIR is NOT on your PATH -- add it to your shell profile:"
     echo "           export PATH=\"$LINK_DIR:\$PATH\"" ;;
esac

echo
echo "done. Run claude-ollama from any project directory."
