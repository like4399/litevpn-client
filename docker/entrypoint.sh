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

python3 - <<'PY'
import sys
from pathlib import Path
import os

sys.path.insert(0, "/usr/share/litevpn/pack")
import merge_config

cfg_dir = Path(os.environ["CONFIG_DIR"])
ui_dir = os.environ["UI_DIR"]
bind = os.environ["BIND"]
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
else:
    text = path.read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines(keepends=True):
        if line.startswith("bind-address:"):
            line = f"bind-address: {bind}\n"
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
