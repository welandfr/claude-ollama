#!/usr/bin/env bash
# Points a project's Claude Code at Ollama, so editor integrations that spawn
# `claude` themselves do not fall back to a login screen.
#
# Usage: ./vscode-setup.sh [project-dir]     (default: current directory)
#        ./vscode-setup.sh --shared [dir]    (write the committed settings.json)
set -euo pipefail

SELF="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$0")"
REPO="$(dirname "$SELF")"

export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m claude_ollama.ide "$@"
