#!/usr/bin/env bash
# Downloads the Claude Code standalone binary into ./bin/claude.
#
# The official installer (claude.ai/install.sh) always lands in
# ~/.local/share/claude with a symlink in ~/.local/bin, and its [target]
# argument selects a version, not a path. So this fetches the same binary from
# the same release channel and verifies the same SHA256, but keeps it inside the
# checkout. ./claude-ollama prefers ./bin/claude when it exists.
#
# Usage: ./fetch-claude.sh [stable|latest|X.Y.Z]   (default: stable)
set -euo pipefail

cd "$(dirname "$0")"

BASE_URL="https://downloads.claude.ai/claude-code-releases"
TARGET="${1:-stable}"
BIN_DIR="bin"

case "$(uname -s)" in
  Darwin) os="darwin" ;;
  Linux)  os="linux" ;;
  *) echo "unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64)  arch="x64" ;;
  arm64|aarch64) arch="arm64" ;;
  *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

# An x64 shell under Rosetta on an ARM Mac should still get the native build.
if [ "$os" = "darwin" ] && [ "$arch" = "x64" ] \
   && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = "1" ]; then
  arch="arm64"
fi

if [ "$os" = "linux" ] && ldd /bin/ls 2>&1 | grep -q musl; then
  platform="linux-${arch}-musl"
else
  platform="${os}-${arch}"
fi

sha256() {
  if [ "$os" = "darwin" ]; then shasum -a 256 "$1" | cut -d' ' -f1
  else sha256sum "$1" | cut -d' ' -f1; fi
}

if [[ "$TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
  version="$TARGET"
else
  version=$(curl -fsSL "$BASE_URL/$TARGET")
  if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
    echo "could not resolve '$TARGET' to a version (got: ${version:0:60})" >&2
    exit 1
  fi
fi

echo "platform $platform, version $version"

manifest=$(curl -fsSL "$BASE_URL/$version/manifest.json")
if command -v jq >/dev/null 2>&1; then
  checksum=$(echo "$manifest" | jq -r ".platforms[\"$platform\"].checksum // empty")
  size=$(echo "$manifest" | jq -r ".platforms[\"$platform\"].size // empty")
else
  flat=$(echo "$manifest" | tr -d '\n\r\t')
  [[ $flat =~ \"$platform\"[[:space:]]*:[[:space:]]*[{][^{}]*\"checksum\"[[:space:]]*:[[:space:]]*\"([a-f0-9]{64})\" ]] \
    && checksum="${BASH_REMATCH[1]}" || checksum=""
  [[ $flat =~ \"$platform\"[[:space:]]*:[[:space:]]*[{][^{}]*\"size\"[[:space:]]*:[[:space:]]*([0-9]+) ]] \
    && size="${BASH_REMATCH[1]}" || size=""
fi

if [[ ! "$checksum" =~ ^[a-f0-9]{64}$ ]]; then
  echo "platform $platform not present in the $version manifest" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
tmp="$BIN_DIR/.claude-$version.download"
trap 'rm -f "$tmp" "$tmp.zst"' EXIT

verified=false
# The zstd copy is a fraction of the size; fall back to the raw binary if
# anything about it does not check out.
if command -v zstd >/dev/null 2>&1 && [[ "$size" =~ ^[1-9][0-9]*$ ]]; then
  zst_manifest=$(curl -fsSL "$BASE_URL/$version/manifest.zst.json" 2>/dev/null || true)
  if command -v jq >/dev/null 2>&1; then
    zst_checksum=$(echo "$zst_manifest" | jq -r ".platforms[\"$platform\"].checksum // empty" 2>/dev/null || true)
  else
    flat=$(echo "$zst_manifest" | tr -d '\n\r\t')
    [[ $flat =~ \"$platform\"[[:space:]]*:[[:space:]]*[{][^{}]*\"checksum\"[[:space:]]*:[[:space:]]*\"([a-f0-9]{64})\" ]] \
      && zst_checksum="${BASH_REMATCH[1]}" || zst_checksum=""
  fi
  if [[ "${zst_checksum:-}" =~ ^[a-f0-9]{64}$ ]] \
     && curl -fsSL -o "$tmp.zst" "$BASE_URL/$version/$platform/claude.zst" \
     && [ "$(sha256 "$tmp.zst")" = "$zst_checksum" ]; then
    echo "decompressing"
    zstd -d -q -c "$tmp.zst" | head -c "$size" > "$tmp" || true
    [ "$(sha256 "$tmp")" = "$checksum" ] && verified=true
  fi
  rm -f "$tmp.zst"
fi

if [ "$verified" = false ]; then
  echo "downloading binary"
  curl -fsSL -o "$tmp" "$BASE_URL/$version/$platform/claude"
  if [ "$(sha256 "$tmp")" != "$checksum" ]; then
    echo "checksum verification failed" >&2
    exit 1
  fi
fi

chmod +x "$tmp"
mv "$tmp" "$BIN_DIR/claude"
echo "$version" > "$BIN_DIR/.version"
trap - EXIT

echo "installed $BIN_DIR/claude ($version)"
