#!/usr/bin/env python3
"""Merge a Clash subscription YAML with LiteVPN overlay keys (no PyYAML)."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from email.utils import decode_rfc2231
from pathlib import Path
from urllib.parse import unquote, urlparse

STRIP_KEYS = {
    "mixed-port",
    "port",
    "socks-port",
    "redir-port",
    "tproxy-port",
    "allow-lan",
    "bind-address",
    "mode",
    "log-level",
    "ipv6",
    "external-controller",
    "external-controller-tls",
    "external-controller-unix",
    "external-controller-cors",
    "secret",
    "external-ui",
    "external-ui-name",
    "external-ui-url",
    "dns",
    "tun",
    "unified-delay",
    "tcp-concurrent",
    "find-process-mode",
    "keep-alive-interval",
    "profile",
}

KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:")
USERINFO_RE = re.compile(
    r"(upload|download|total|expire)\s*=\s*([0-9]+)",
    re.I,
)
CLASH_UA = [
    "clash-verge/v2.4.3",
    "ClashMetaForAndroid/2.11.0.Meta",
    "clash-meta/1.19.0",
]
MAX_SUB_BYTES = 8 * 1024 * 1024
FETCH_TIMEOUT = 20


def looks_like_clash(text: str) -> bool:
    return bool(re.search(r"(?m)^(proxies|proxy-groups|proxy-providers)\s*:", text))


def decode_payload(raw: bytes) -> str:
    text = raw.decode("utf-8-sig", errors="replace").strip()
    if looks_like_clash(text):
        return text
    compact = re.sub(r"\s+", "", text)
    try:
        padded = compact + ("=" * (-len(compact) % 4))
        decoded = base64.b64decode(padded, validate=False)
        decoded_text = decoded.decode("utf-8-sig", errors="replace").strip()
        if looks_like_clash(decoded_text) or decoded_text.startswith(("proxies:", "{")):
            return decoded_text
    except Exception:
        pass
    return text


def is_top_level(line: str) -> bool:
    if not line.strip() or line.lstrip().startswith("#"):
        return False
    if line.startswith((" ", "\t")):
        return False
    return KEY_RE.match(line) is not None


def strip_keys(text: str, keys: set[str]) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if skipping:
            if is_top_level(line):
                skipping = False
            else:
                continue
        if not skipping:
            m = KEY_RE.match(line)
            if m and not line.startswith((" ", "\t")) and m.group(1) in keys:
                skipping = True
                continue
            out.append(line)
    return "".join(out).rstrip() + "\n"


def extract_top_level(text: str, key: str) -> tuple[str, str]:
    """Split a top-level YAML key block from the rest of the document."""
    lines = text.splitlines(keepends=True)
    block: list[str] = []
    rest: list[str] = []
    capturing = False
    for line in lines:
        if capturing:
            if is_top_level(line):
                capturing = False
            else:
                block.append(line)
                continue
        m = KEY_RE.match(line)
        if (not capturing) and m and not line.startswith((" ", "\t")) and m.group(1) == key:
            capturing = True
            block.append(line)
            continue
        rest.append(line)
    block_text = "".join(block).rstrip()
    if block_text:
        block_text += "\n"
    return block_text, "".join(rest)


def file_provider_entry(name: str, rel_path: str, *, prefix: str = "") -> str:
    key = _yaml_quote(name)
    path = _yaml_quote(rel_path)
    block = (
        f"  {key}:\n"
        f"    type: file\n"
        f"    path: {path}\n"
        f"    interval: 3600\n"
        f"    health-check:\n"
        f"      enable: false\n"
        f"      lazy: true\n"
        f"      url: https://www.gstatic.com/generate_204\n"
        f"      interval: 600\n"
    )
    if prefix:
        block += (
            f"    override:\n"
            f"      additional-prefix: {_yaml_quote(prefix)}\n"
        )
    return block


def build_runtime_config(
    text: str,
    secret: str,
    ui_dir: str,
    provider_name: str,
    provider_rel: str,
) -> tuple[str, str]:
    """Merge overlay + file proxy-provider. Returns (config.yaml, provider yaml)."""
    body = strip_keys(text, STRIP_KEYS)
    if not looks_like_clash(body):
        raise ValueError("subscription does not look like a Clash/Mihomo config (no proxies)")
    proxies_block, body = extract_top_level(body, "proxies")
    existing_providers, body = extract_top_level(body, "proxy-providers")
    empty_proxies = (not proxies_block) or bool(
        re.search(r"(?m)^proxies:\s*\[\s*\]\s*$", proxies_block.strip())
    )
    provider_file = ""
    entries = ""
    if not empty_proxies:
        provider_file = proxies_block if proxies_block.lstrip().startswith("proxies:") else "proxies:\n" + proxies_block
        pname = (provider_name or "subscription").strip() or "subscription"
        if pname.upper() in {"DIRECT", "REJECT", "GLOBAL", "COMPATIBLE", "PASS"}:
            pname = "sub-" + pname
        entries = file_provider_entry(pname, provider_rel, prefix=f"{pname} ")
    if existing_providers:
        rest = existing_providers.split("\n", 1)[1] if "\n" in existing_providers.rstrip() else ""
        providers_block = "proxy-providers:\n" + entries + rest
        if not providers_block.endswith("\n"):
            providers_block += "\n"
    elif entries:
        providers_block = "proxy-providers:\n" + entries + "\n"
    else:
        providers_block = ""
    if not providers_block and empty_proxies:
        return overlay(secret, ui_dir).rstrip() + "\n\n" + body.lstrip(), ""
    merged = overlay(secret, ui_dir).rstrip() + "\n\n"
    if providers_block:
        merged += providers_block.rstrip() + "\n\n"
    if proxies_block and not empty_proxies:
        merged += proxies_block.rstrip() + "\n\n"
    merged += body.lstrip()
    if not looks_like_clash(merged):
        raise ValueError("subscription does not look like a Clash/Mihomo config (no proxies)")
    return merged, provider_file


def overlay(secret: str, ui_dir: str) -> str:
    bind = os.environ.get("LITEVPN_BIND_ADDRESS", "127.0.0.1")
    return (
        f"mixed-port: 7890\n"
        f"allow-lan: false\n"
        f"bind-address: {bind}\n"
        f"mode: rule\n"
        f"log-level: info\n"
        f"ipv6: false\n"
        f"unified-delay: true\n"
        f"tcp-concurrent: true\n"
        f"external-controller: 127.0.0.1:19090\n"
        f"external-controller-cors:\n"
        f"  allow-origins:\n"
        f"    - 'http://127.0.0.1:9090'\n"
        f"    - 'http://localhost:9090'\n"
        f"  allow-private-network: true\n"
        f"external-ui: \"{ui_dir}\"\n"
        f"external-ui-name: ui\n"
        f"secret: {secret}\n"
        f"dns:\n"
        f"  enable: true\n"
        f"  ipv6: false\n"
        f"  listen: 127.0.0.1:1053\n"
        f"  enhanced-mode: fake-ip\n"
        f"  fake-ip-range: 198.18.0.1/16\n"
        f"  nameserver:\n"
        f"    - 223.5.5.5\n"
        f"    - 8.8.8.8\n"
    )


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def provider_key(item: dict) -> str:
    raw = re.sub(r"[^A-Za-z0-9_]", "", str(item.get("id") or "sub"))
    return ("p" + raw)[:24] or "psub"


def _b64_text(blob: str) -> str:
    try:
        padded = blob + ("=" * (-len(blob) % 4))
        return base64.b64decode(padded).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def decode_profile_title(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'")
    if not value:
        return ""
    low = value.lower()
    if low.startswith("base64:"):
        return _b64_text(value.split(":", 1)[1].strip())
    if low.startswith("utf-8'"):
        return unquote(value.split("'", 2)[-1])
    return value


def parse_userinfo(value: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, num in USERINFO_RE.findall(value or ""):
        out[key.lower()] = int(num)
    return out


def parse_content_disposition(value: str) -> str:
    if not value:
        return ""
    star = re.search(r"filename\*\s*=\s*([^;]+)", value, re.I)
    if star:
        raw = star.group(1).strip().strip('"')
        try:
            charset, _lang, text = decode_rfc2231(raw)
            if text:
                if isinstance(text, bytes):
                    return text.decode(charset or "utf-8", errors="replace")
                return unquote(str(text))
        except Exception:
            pass
        if "''" in raw:
            return unquote(raw.split("''", 1)[1])
    m = re.search(r'filename\s*=\s*"([^"]+)"', value, re.I)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", value, re.I)
    if m:
        return m.group(1).strip().strip("'")
    return ""


def _header_map(header_text: str) -> dict[str, str]:
    text = header_text or ""
    parts = re.split(r"(?im)^HTTP/\S+.*$", text)
    block = parts[-1] if parts else text
    found: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        k = key.strip().lower()
        v = val.strip()
        if v:
            found[k] = v
    return found


def parse_subscription_meta(header_text: str, url: str = "") -> dict:
    headers = _header_map(header_text)
    info = parse_userinfo(headers.get("subscription-userinfo", ""))
    name = decode_profile_title(headers.get("profile-title", ""))
    if not name:
        disp = parse_content_disposition(headers.get("content-disposition", ""))
        name = Path(disp).stem if disp else ""
        if name.lower() in {"subscription", "clash", "config", "proxies"}:
            name = ""
    host = ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        host = ""
    return {
        "name": name,
        "host": host,
        "upload": info.get("upload", 0),
        "download": info.get("download", 0),
        "total": info.get("total", 0),
        "expire": info.get("expire", 0),
    }


def guess_name_from_yaml(text: str) -> str:
    m = re.search(r"(?im)^#\s*(?:profile|名称|title|name)\s*[:：]\s*(.+)$", text)
    if m:
        return m.group(1).strip().strip("'\"")
    m = re.search(
        r"(?ms)^proxy-groups:\s*\n(?:\s*#[^\n]*\n)*\s*-\s*name:\s*[\"']?([^\n\"']+?)[\"']?\s*$",
        text,
    )
    if m:
        return m.group(1).strip()
    return ""


def fetch_url(url: str, timeout: int = FETCH_TIMEOUT) -> tuple[bytes, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("only http/https subscription URLs are allowed")

    def scrub(text: str) -> str:
        return (text or "").replace(url, "").strip()[:180]

    last = "download failed"
    tmpdir = Path(tempfile.gettempdir())
    hdr_path = tmpdir / f"litevpn-sub-{os.getpid()}.hdr"
    body_path = tmpdir / f"litevpn-sub-{os.getpid()}.bin"
    attempts: list[tuple[str, bool]] = [(CLASH_UA[0], False), (CLASH_UA[0], True)]
    for ua in CLASH_UA[1:]:
        attempts.append((ua, False))
    for ua, use_proxy in attempts:
        cmd = [
            "curl",
            "-4",
            "-sSL",
            "--compressed",
            "--http1.1",
            "--connect-timeout",
            "10",
            "--max-time",
            str(timeout),
            "-A",
            ua,
            "-D",
            str(hdr_path),
            "-o",
            str(body_path),
            url,
        ]
        if use_proxy:
            cmd[1:1] = ["-x", "http://127.0.0.1:7890"]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 8)
        except FileNotFoundError:
            break
        except subprocess.TimeoutExpired:
            last = "download timeout"
            continue
        if proc.returncode != 0:
            last = scrub(proc.stderr.decode("utf-8", "replace")) or f"curl exit {proc.returncode}"
            hdr_path.unlink(missing_ok=True)
            body_path.unlink(missing_ok=True)
            continue
        body = body_path.read_bytes() if body_path.is_file() else b""
        header_text = hdr_path.read_text(encoding="utf-8", errors="replace") if hdr_path.is_file() else ""
        hdr_path.unlink(missing_ok=True)
        body_path.unlink(missing_ok=True)
        if len(body) > MAX_SUB_BYTES:
            raise ValueError("subscription too large")
        if not body.strip():
            last = "empty subscription body"
            continue
        text = decode_payload(body)
        if looks_like_clash(text):
            return body, header_text
        last = "subscription is not Clash YAML"
    # urllib fallback
    try:
        from urllib.request import Request, urlopen
        import ssl

        ctx = ssl.create_default_context()
        req = Request(url, headers={"User-Agent": CLASH_UA[0]})
        with urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(MAX_SUB_BYTES + 1)
            if len(body) > MAX_SUB_BYTES:
                raise ValueError("subscription too large")
            headers = "".join(f"{k}: {v}\n" for k, v in resp.headers.items())
            text = decode_payload(body)
            if looks_like_clash(text):
                return body, headers
            last = "subscription is not Clash YAML"
    except ValueError:
        raise
    except Exception as exc:
        last = scrub(str(exc)) or last
    raise ValueError(last or "failed to download subscription")


def config_from_providers(secret: str, ui_dir: str, items: list) -> str:
    """Deprecated HTTP-provider path kept for old CLI flags."""
    head = overlay(secret, ui_dir).rstrip() + "\n"
    usable = [it for it in items if it.get("url")]
    if not usable:
        return (
            head
            + "\nproxies: []\n"
            + "proxy-groups:\n"
            + "  - name: PROXY\n"
            + "    type: select\n"
            + "    proxies:\n"
            + "      - DIRECT\n"
            + "rules:\n"
            + "  - MATCH,DIRECT\n"
        )
    blocks = ["proxy-providers:"]
    names: list[str] = []
    for it in usable:
        key = provider_key(it)
        names.append(key)
        url = str(it["url"]).strip()
        blocks.append(f"  {key}:")
        blocks.append("    type: http")
        blocks.append(f"    url: {_yaml_quote(url)}")
        blocks.append(f"    path: ./providers/{key}.yaml")
        blocks.append("    interval: 3600")
        blocks.append("    header:")
        blocks.append("      User-Agent:")
        blocks.append('        - "clash-verge/v2.4.3"')
        blocks.append("    health-check:")
        blocks.append("      enable: false")
        blocks.append("      lazy: true")
        blocks.append("      url: https://www.gstatic.com/generate_204")
        blocks.append("      interval: 600")
    use_lines = "\n".join(f"      - {n}" for n in names)
    blocks.append("")
    blocks.append("proxy-groups:")
    blocks.append("  - name: PROXY")
    blocks.append("    type: select")
    blocks.append("    use:")
    blocks.append(use_lines)
    blocks.append("    proxies:")
    blocks.append("      - DIRECT")
    blocks.append("rules:")
    blocks.append("  - MATCH,PROXY")
    blocks.append("")
    return head + "\n" + "\n".join(blocks)


def merge(raw: bytes, secret: str, ui_dir: str) -> str:
    body = decode_payload(raw)
    body = strip_keys(body, STRIP_KEYS)
    if not looks_like_clash(body):
        raise ValueError("subscription does not look like a Clash/Mihomo config (no proxies)")
    return overlay(secret, ui_dir).rstrip() + "\n\n" + body.lstrip()


def load_subs(config_dir: Path) -> dict:
    path = config_dir / "subscriptions.json"
    if not path.is_file():
        return {"active": "", "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"active": "", "items": []}
    data.setdefault("active", "")
    data.setdefault("items", [])
    return data


def save_subs(config_dir: Path, data: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.chmod(0o700)
    path = config_dir / "subscriptions.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def public_items(data: dict) -> dict:
    items = []
    for it in data.get("items", []):
        host = it.get("host") or ""
        if not host:
            try:
                host = urlparse(it.get("url") or "").hostname or ""
            except Exception:
                host = ""
        items.append(
            {
                "id": it.get("id"),
                "name": it.get("name") or host,
                "host": host,
                "updated_at": it.get("updated_at") or 0,
                "upload": int(it.get("upload") or 0),
                "download": int(it.get("download") or 0),
                "total": int(it.get("total") or 0),
                "expire": int(it.get("expire") or 0),
            }
        )
    return {"active": data.get("active") or "", "items": items}


def test_and_write(mihomo: Path, config_dir: Path, share_dir: Path, text: str) -> None:
    if not mihomo.is_file():
        raise ValueError("mihomo not found")
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "providers").mkdir(parents=True, exist_ok=True)
    tmp_yaml = config_dir / "config.yaml.tmp"
    tmp_yaml.write_text(text, encoding="utf-8")
    tmp_yaml.chmod(0o600)
    env = os.environ.copy()
    env["SAFE_PATHS"] = os.pathsep.join(
        [
            env.get("SAFE_PATHS") or str(share_dir),
            str(config_dir),
            str(share_dir),
        ]
    )
    proc = subprocess.run(
        [str(mihomo), "-t", "-d", str(config_dir), "-f", str(tmp_yaml)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if proc.returncode != 0:
        tmp_yaml.unlink(missing_ok=True)
        err = (proc.stderr or proc.stdout or "mihomo rejected the config").strip()
        raise ValueError(err.replace("\n", " ")[-240:])
    tmp_yaml.replace(config_dir / "config.yaml")


def import_subscription(
    url: str,
    name: str,
    *,
    secret: str,
    config_dir: Path,
    ui_dir: Path,
    share_dir: Path,
    mihomo: Path,
    raw: bytes | None = None,
    header_text: str = "",
) -> dict:
    if raw is None:
        raw, header_text = fetch_url(url)
    text = decode_payload(raw)
    if not looks_like_clash(text):
        raise ValueError("subscription does not look like a Clash/Mihomo config (no proxies)")
    meta = parse_subscription_meta(header_text, url)
    host = meta.get("host") or (urlparse(url).hostname or "subscription")
    display = (name or "").strip() or meta.get("name") or guess_name_from_yaml(text) or host
    data = load_subs(config_dir)
    found = None
    for it in data["items"]:
        if it.get("url") == url:
            found = it
            break
    now = int(time.time())
    if found is None:
        found = {"id": uuid.uuid4().hex, "url": url}
        data["items"].append(found)
    found["name"] = display
    found["host"] = host
    found["updated_at"] = now
    found["upload"] = meta.get("upload") or 0
    found["download"] = meta.get("download") or 0
    found["total"] = meta.get("total") or 0
    found["expire"] = meta.get("expire") or 0
    data["active"] = found["id"]
    save_subs(config_dir, data)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "providers").mkdir(parents=True, exist_ok=True)
    sub_path = config_dir / "subscription.yaml"
    sub_path.write_text(text + "\n", encoding="utf-8")
    sub_path.chmod(0o600)
    rel = f"./providers/{found['id']}.yaml"
    merged, provider_yaml = build_runtime_config(
        text, secret, str(ui_dir), found["name"] or host, rel
    )
    per = config_dir / "providers" / f"{found['id']}.yaml"
    if provider_yaml:
        per.write_text(provider_yaml if provider_yaml.endswith("\n") else provider_yaml + "\n", encoding="utf-8")
        per.chmod(0o600)
    url_file = config_dir / "subscription.url"
    url_file.write_text(url + "\n", encoding="utf-8")
    url_file.chmod(0o600)
    test_and_write(mihomo, config_dir, share_dir, merged)
    return public_items(data)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input")
    p.add_argument("--providers-json")
    p.add_argument("--import-url")
    p.add_argument("--name", default="")
    p.add_argument("--config-dir")
    p.add_argument("--share-dir")
    p.add_argument("--mihomo")
    p.add_argument("--output")
    p.add_argument("--secret", required=True)
    p.add_argument("--ui-dir", required=True)
    args = p.parse_args()
    try:
        if args.import_url:
            config_dir = Path(args.config_dir or os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "litevpn"
            if args.config_dir:
                config_dir = Path(args.config_dir)
            share_dir = Path(args.share_dir or (Path.home() / ".local" / "share" / "litevpn"))
            mihomo = Path(args.mihomo or (Path.home() / ".local" / "bin" / "mihomo"))
            import_subscription(
                args.import_url,
                args.name,
                secret=args.secret,
                config_dir=config_dir,
                ui_dir=Path(args.ui_dir),
                share_dir=share_dir,
                mihomo=mihomo,
            )
            return 0
        if args.providers_json:
            data = json.loads(Path(args.providers_json).read_text(encoding="utf-8"))
            items = data.get("items") if isinstance(data, dict) else data
            merged = config_from_providers(args.secret, args.ui_dir, items or [])
        else:
            if not args.input or not args.output:
                print("need --input/--output or --import-url", file=sys.stderr)
                return 1
            raw = Path(args.input).read_bytes()
            merged = merge(raw, args.secret, args.ui_dir)
        if not args.output:
            print("need --output", file=sys.stderr)
            return 1
        Path(args.output).write_text(merged, encoding="utf-8")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
