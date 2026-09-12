#!/usr/bin/env python3
"""litevpn-client-linux: 127.0.0.1:9090 reverse proxy + subscription import API."""
from __future__ import annotations

import hmac
import json
import os
import select
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

LISTEN_HOST = os.environ.get("LITEVPN_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("LITEVPN_LISTEN_PORT", "9090"))
MIHOMO_HOST = os.environ.get("LITEVPN_MIHOMO_HOST", "127.0.0.1")
MIHOMO_PORT = int(os.environ.get("LITEVPN_MIHOMO_PORT", "19090"))
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}


def _paths() -> dict[str, Path]:
    home = Path(os.environ.get("HOME", "/root"))
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    bin_dir = Path(os.environ.get("LITEVPN_BIN_DIR", home / ".local" / "bin"))
    config_dir = config_home / "litevpn"
    share_dir = data_home / "litevpn"
    here = Path(__file__).resolve().parent
    merge_py = share_dir / "pack" / "merge_config.py"
    if not merge_py.is_file():
        merge_py = here / "merge_config.py"
    web_html = share_dir / "pack" / "web" / "index.html"
    if not web_html.is_file():
        web_html = here / "web" / "index.html"
    web_js = share_dir / "pack" / "web" / "ui-import.js"
    if not web_js.is_file():
        web_js = here / "web" / "ui-import.js"
    return {
        "config_dir": config_dir,
        "share_dir": share_dir,
        "ui_dir": share_dir / "ui",
        "secret": config_dir / "secret",
        "cfg": config_dir / "config.yaml",
        "sub_yaml": config_dir / "subscription.yaml",
        "url_file": config_dir / "subscription.url",
        "subs_json": config_dir / "subscriptions.json",
        "merge_py": merge_py,
        "web_html": web_html,
        "web_js": web_js,
        "mihomo": bin_dir / "mihomo",
    }


P = _paths()
if str(P["merge_py"].parent) not in sys.path:
    sys.path.insert(0, str(P["merge_py"].parent))
import merge_config  # noqa: E402


def _secret() -> str:
    if not P["secret"].is_file():
        return ""
    return P["secret"].read_text(encoding="utf-8").strip()


def _auth_ok(handler: BaseHTTPRequestHandler) -> bool:
    expected = _secret()
    if not expected:
        return False
    hdr = handler.headers.get("Authorization", "")
    got = ""
    if hdr.lower().startswith("bearer "):
        got = hdr[7:].strip()
    if not got or not expected or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def _reload_mihomo() -> None:
    secret = _secret()
    body = json.dumps({"path": str(P["cfg"])}).encode()
    req = Request(
        f"http://{MIHOMO_HOST}:{MIHOMO_PORT}/configs?force=true",
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception:
        return
    skip = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "PROXY"}
    for _ in range(30):
        try:
            preq = Request(
                f"http://{MIHOMO_HOST}:{MIHOMO_PORT}/providers/proxies",
                headers={"Authorization": f"Bearer {secret}"},
            )
            with urlopen(preq, timeout=3) as resp:
                pdata = json.loads(resp.read().decode("utf-8"))
            providers = pdata.get("providers") or {}
            for name, info in providers.items():
                if name in skip or not isinstance(info, dict):
                    continue
                vtype = str(info.get("vehicleType") or info.get("vehicle_type") or "")
                if vtype.lower() in {"file", "http"}:
                    return
        except Exception:
            pass
        try:
            preq = Request(
                f"http://{MIHOMO_HOST}:{MIHOMO_PORT}/proxies",
                headers={"Authorization": f"Bearer {secret}"},
            )
            with urlopen(preq, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            names = set((data.get("proxies") or {}).keys())
            if names - skip:
                return
        except Exception:
            pass
        time.sleep(0.2)


def _upsert(url: str, name: str, raw: bytes | None = None, header_text: str = "") -> dict:
    secret = _secret()
    if not secret:
        raise ValueError("no secret; run litevpn setup")
    result = merge_config.import_subscription(
        url,
        name,
        secret=secret,
        config_dir=P["config_dir"],
        ui_dir=P["ui_dir"],
        share_dir=P["share_dir"],
        mihomo=P["mihomo"],
        raw=raw,
        header_text=header_text,
    )
    _reload_mihomo()
    return result


def _update(sub_id: str) -> dict:
    data = merge_config.load_subs(P["config_dir"])
    found = None
    for it in data["items"]:
        if it.get("id") == sub_id:
            found = it
            break
    if found is None or not found.get("url"):
        raise ValueError("subscription not found")
    return _upsert(found["url"], found.get("name") or "")


CORS_ORIGINS = {
    "http://127.0.0.1:9090",
    "http://localhost:9090",
    "http://[::1]:9090",
}


def _add_cors(handler: BaseHTTPRequestHandler) -> None:
    origin = (handler.headers.get("Origin") or "").strip()
    if origin in CORS_ORIGINS:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Credentials", "true")
        handler.send_header(
            "Access-Control-Allow-Headers",
            handler.headers.get("Access-Control-Request-Headers")
            or "Authorization, Content-Type",
        )
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        handler.send_header("Access-Control-Max-Age", "300")


def _json_bytes(handler: BaseHTTPRequestHandler, code: int, obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    _add_cors(handler)
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler, limit: int = 1024 * 1024) -> dict:
    length = int(handler.headers.get("Content-Length") or "0")
    if length < 0 or length > limit:
        raise ValueError("invalid body")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid json")
    return data


def _proxy_websocket(handler: BaseHTTPRequestHandler) -> None:
    dest = socket.create_connection((MIHOMO_HOST, MIHOMO_PORT), timeout=15)
    try:
        lines = [f"{handler.command} {handler.path} HTTP/1.1"]
        for key, val in handler.headers.items():
            lines.append(f"{key}: {val}")
        payload = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")
        dest.sendall(payload)
        client = handler.connection
        handler.close_connection = True
        while True:
            r, _, _ = select.select([client, dest], [], [], 60)
            if not r:
                break
            for sock in r:
                other = dest if sock is client else client
                data = sock.recv(65536)
                if not data:
                    return
                other.sendall(data)
    finally:
        dest.close()


def _copy_upstream_headers(handler: BaseHTTPRequestHandler, headers) -> None:
    skip = HOP_BY_HOP | {
        "content-length",
        "content-encoding",
        "access-control-allow-origin",
        "access-control-allow-headers",
        "access-control-allow-methods",
        "access-control-allow-credentials",
        "access-control-max-age",
    }
    if not headers:
        return
    for key, val in headers.items():
        if key.lower() in skip:
            continue
        handler.send_header(key, val)


INJECT_TAG = b'<script src="/ui/litevpn-import.js?v=8" defer></script>'


def _maybe_inject_html(path: str, data: bytes) -> bytes:
    if INJECT_TAG in data:
        return data
    p = urlparse(path).path
    if not (p.startswith("/ui") or p in ("/", "/index.html")):
        return data
    if b"</head>" in data:
        return data.replace(b"</head>", INJECT_TAG + b"</head>", 1)
    if b"</HEAD>" in data:
        return data.replace(b"</HEAD>", INJECT_TAG + b"</HEAD>", 1)
    return data


def _proxy_http(handler: BaseHTTPRequestHandler) -> None:
    length = int(handler.headers.get("Content-Length") or "0")
    body = handler.rfile.read(length) if length else b""
    req = Request(
        f"http://{MIHOMO_HOST}:{MIHOMO_PORT}{handler.path}",
        data=body if body else None,
        method=handler.command,
    )
    for key, val in handler.headers.items():
        if key.lower() in HOP_BY_HOP or key.lower() in {"host", "content-length"}:
            continue
        req.add_header(key, val)
    try:
        with urlopen(req, timeout=60) as resp:
            data = _maybe_inject_html(handler.path, resp.read())
            handler.send_response(resp.status)
            _copy_upstream_headers(handler, resp.headers)
            handler.send_header("Content-Length", str(len(data)))
            _add_cors(handler)
            handler.end_headers()
            handler.wfile.write(data)
    except HTTPError as exc:
        data = exc.read()
        handler.send_response(exc.code)
        _copy_upstream_headers(handler, exc.headers)
        handler.send_header("Content-Length", str(len(data)))
        _add_cors(handler)
        handler.end_headers()
        handler.wfile.write(data)
    except URLError:
        _json_bytes(handler, 502, {"error": "mihomo not reachable"})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch()

    def do_POST(self) -> None:
        self._dispatch()

    def do_PUT(self) -> None:
        self._dispatch()

    def do_PATCH(self) -> None:
        self._dispatch()

    def do_DELETE(self) -> None:
        self._dispatch()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        _add_cors(self)
        self.end_headers()

    def _dispatch(self) -> None:
        path = urlparse(self.path).path
        if path in ("/",):
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.end_headers()
            return
        if path in ("/ui/litevpn-import.js", "/litevpn-import.js"):
            js = P["web_js"].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(js)))
            self.send_header("Cache-Control", "no-store")
            _add_cors(self)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(js)
            return
        if path in ("/subs", "/subs/"):
            html = P["web_html"].read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            _add_cors(self)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(html)
            return
        if path.startswith("/api/litevpn/"):
            self._api(path)
            return
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            _proxy_websocket(self)
            return
        _proxy_http(self)

    def _api(self, path: str) -> None:
        if not _auth_ok(self):
            _json_bytes(self, 401, {"error": "Unauthorized"})
            return
        try:
            if path.rstrip("/") == "/api/litevpn/subs" and self.command == "GET":
                _json_bytes(self, 200, merge_config.public_items(merge_config.load_subs(P["config_dir"])))
                return
            if path.rstrip("/") == "/api/litevpn/import" and self.command == "POST":
                data = _read_json(self, limit=8 * 1024 * 1024)
                url = (data.get("url") or "").strip()
                name = (data.get("name") or "").strip()
                yaml_text = data.get("yaml") or data.get("content") or ""
                if isinstance(yaml_text, str):
                    yaml_text = yaml_text.strip()
                else:
                    yaml_text = ""
                if not url and not yaml_text:
                    raise ValueError("url required")
                raw = yaml_text.encode("utf-8") if yaml_text else None
                if not url:
                    url = "https://imported.local/subscription"
                result = _upsert(url, name, raw=raw)
                _json_bytes(self, 200, {"ok": True, **result})
                return
            if path.rstrip("/") == "/api/litevpn/update" and self.command == "POST":
                data = _read_json(self)
                sub_id = (data.get("id") or "").strip()
                if not sub_id:
                    raise ValueError("id required")
                result = _update(sub_id)
                _json_bytes(self, 200, {"ok": True, **result})
                return
            _json_bytes(self, 404, {"error": "not found"})
        except ValueError as exc:
            _json_bytes(self, 400, {"error": str(exc)})
        except json.JSONDecodeError:
            _json_bytes(self, 400, {"error": "invalid json"})
        except Exception as exc:
            _json_bytes(self, 500, {"error": str(exc)[:200] or "import failed"})


def main() -> int:
    httpd = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    httpd.daemon_threads = True
    print(f"litevpn-web listening on http://{LISTEN_HOST}:{LISTEN_PORT}/subs", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    threading.current_thread().name = "litevpn-web"
    raise SystemExit(main())
