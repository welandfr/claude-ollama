#!/usr/bin/env bash
# Points a project's Claude Code at the local proxy, so editor integrations that
# spawn `claude` themselves do not fall back to a login screen.
#
# Usage: ./vscode-setup.sh [project-dir]     (default: current directory)
#        ./vscode-setup.sh --print-service   (systemd user unit for the proxy)
set -euo pipefail

SELF="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$0")"
REPO="$(dirname "$SELF")"

if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "run $REPO/setup.sh first" >&2
  exit 1
fi

export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
exec "$REPO/.venv/bin/python" -m claude_ollama.ide "$@"
