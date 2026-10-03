#!/usr/bin/env bash
# Runs the proxy in the foreground. Useful for watching the request log.
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv/bin/python -m claude_ollama "$@"
