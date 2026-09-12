#!/usr/bin/env bash
# Install mihomo + metacubexd + litevpn CLI (litevpn-client-linux, Ubuntu 24.04).
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "litevpn-client-linux only supports Linux (Ubuntu 24.04). Use Clash Verge on Windows." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BIN_DIR="${LITEVPN_BIN_DIR:-$HOME/.local/bin}"
SHARE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/litevpn"
UI_DIR="$SHARE_DIR/ui"
TMPDIR="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "${TMPDIR%/}/litevpn-install.XXXXXX")"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1 (install it with apt)" >&2
    exit 1
  }
}

need python3
need curl
need gzip
need tar
need install

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) GOARCH="amd64" ;;
  aarch64|arm64) GOARCH="arm64" ;;
  *)
    echo "unsupported architecture: $arch" >&2
    exit 1
    ;;
esac

mkdir -p "$BIN_DIR" "$SHARE_DIR" "$UI_DIR"

echo "downloading latest mihomo (linux-$GOARCH)..."
api_json="$(curl -fsSL -A "litevpn-client-linux" "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest")"
asset_url="$(printf '%s\n' "$api_json" | grep -oE "https://[^\"]+/mihomo-linux-${GOARCH}-v[0-9][^\"]+\\.gz" | grep -vE 'go[0-9]|compatible|cgo' | head -n1 || true)"
if [[ -z "$asset_url" ]]; then
  asset_url="$(printf '%s\n' "$api_json" | grep -oE "https://[^\"]+/mihomo-linux-${GOARCH}-v[0-9][^\"]+\\.gz" | head -n1 || true)"
fi
if [[ -z "$asset_url" ]]; then
  echo "could not find mihomo-linux-${GOARCH} asset in GitHub latest release" >&2
  exit 1
fi

curl -fL --progress-bar -o "$WORK/mihomo.gz" "$asset_url"
gzip -dc "$WORK/mihomo.gz" > "$WORK/mihomo"
chmod 755 "$WORK/mihomo"
install -m 755 "$WORK/mihomo" "$BIN_DIR/mihomo"

echo "downloading metacubexd (gh-pages)..."
curl -fL --progress-bar -o "$WORK/ui.tar.gz" \
  "https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.tar.gz"
tar -xzf "$WORK/ui.tar.gz" -C "$WORK"
ui_src="$(find "$WORK" -maxdepth 1 -type d -name 'metacubexd-*' | head -n1)"
if [[ -z "$ui_src" || ! -f "$ui_src/index.html" ]]; then
  echo "metacubexd archive did not contain index.html" >&2
  exit 1
fi
rm -rf "$UI_DIR"
mkdir -p "$UI_DIR"
# copy contents, not the wrapper directory
cp -a "$ui_src"/. "$UI_DIR"/

echo "installing litevpn CLI and pack files..."
install -m 755 "$REPO_ROOT/litevpn" "$BIN_DIR/litevpn"
rm -rf "$SHARE_DIR/pack"
mkdir -p "$SHARE_DIR/pack"
cp -a "$REPO_ROOT/pack"/. "$SHARE_DIR/pack"/
chmod 755 "$SHARE_DIR/pack/web_server.py"

echo
echo "installed:"
echo "  $BIN_DIR/mihomo"
echo "  $BIN_DIR/litevpn"
echo "  $SHARE_DIR/pack/web_server.py"
echo
if ! printf '%s' ":$PATH:" | grep -q ":$BIN_DIR:"; then
  echo "add this to ~/.bashrc then re-open the shell:"
  echo "  export PATH=\"$BIN_DIR:\$PATH\""
  echo
fi
echo "next:"
echo "  litevpn setup"
echo "  litevpn up"
echo "  open http://127.0.0.1:9090/subs  (SSH -L 9090:127.0.0.1:9090 if remote)"
