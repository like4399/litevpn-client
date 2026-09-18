#!/bin/sh
set -eu

CONFIG_DIR="${XDG_CONFIG_HOME:-/config}/litevpn"
UI_DIR="${LITEVPN_UI_DIR:-/usr/share/litevpn/ui}"
BIND="${LITEVPN_BIND_ADDRESS:-0.0.0.0}"

mkdir -p "$CONFIG_DIR/providers"
chmod 700 "$CONFIG_DIR" 2>/dev/null || true
export CONFIG_DIR UI_DIR BIND

if [ ! -s "$CONFIG_DIR/secret" ]; then
  python3 - <<'PY'
import secrets
from pathlib import Path
import os
p = Path(os.environ["CONFIG_DIR"]) / "secret"
p.write_text(secrets.token_hex(16), encoding="utf-8")
p.chmod(0o600)
PY
fi

# Preseed geo data so first import's mihomo -t does not hang on MMDB download.
python3 - <<'PY'
import os
import urllib.request
from pathlib import Path

cfg = Path(os.environ["CONFIG_DIR"])
mirrors = [
    "https://ghfast.top/https://github.com/MetaCubeX/meta-rules-dat/releases/download/latest/",
    "https://cdn.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/",
]
files = {
    "geoip.metadb": 1_000_000,
    "geosite.dat": 1_000_000,
}
for name, min_size in files.items():
    dest = cfg / name
    if dest.is_file() and dest.stat().st_size >= min_size:
        continue
    for base in mirrors:
        url = base + name
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "litevpn-client"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            if len(data) < min_size:
                continue
            dest.write_bytes(data)
            break
        except Exception:
            continue
PY

python3 - <<'PY'
import sys
from pathlib import Path
import os

sys.path.insert(0, "/usr/share/litevpn/pack")
import merge_config

cfg_dir = Path(os.environ["CONFIG_DIR"])
ui_dir = os.environ["UI_DIR"]
bind = os.environ["BIND"]
allow_lan = "true" if bind not in ("127.0.0.1", "::1") else "false"
path = cfg_dir / "config.yaml"
if not path.is_file():
    secret = (cfg_dir / "secret").read_text(encoding="utf-8").strip()
    text = merge_config.overlay(secret, ui_dir, cfg_dir).rstrip() + "\n"
    text += (
        "\nproxies: []\n"
        "proxy-groups:\n"
        "  - name: PROXY\n"
        "    type: select\n"
        "    proxies:\n"
        "      - DIRECT\n"
        "rules:\n"
        "  - MATCH,DIRECT\n"
    )
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)

text = path.read_text(encoding="utf-8")
lines = []
for line in text.splitlines(keepends=True):
    if line.startswith("bind-address:"):
        line = f"bind-address: {bind}\n"
    elif line.startswith("allow-lan:"):
        line = f"allow-lan: {allow_lan}\n"
    elif line.startswith("external-ui:"):
        line = f'external-ui: "{ui_dir}"\n'
    lines.append(line)
path.write_text("".join(lines), encoding="utf-8")
PY

mihomo -d "$CONFIG_DIR" &
MIHOMO_PID=$!

python3 /usr/share/litevpn/pack/web_server.py &
WEB_PID=$!

term() {
  kill "$MIHOMO_PID" "$WEB_PID" 2>/dev/null || true
  wait || true
}
trap term INT TERM EXIT

while kill -0 "$MIHOMO_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 2
done
echo "a process exited; shutting down" >&2
term
exit 1
