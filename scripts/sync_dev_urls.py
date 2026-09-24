#!/usr/bin/env python3
"""Localhost by default. Optional Cloudflare quick tunnels for MCP + api-doc + web.

`start` runs cloudflared as child processes of this script (one terminal stays
in the foreground). Ctrl+C / clean kills tunnels, closes leftover Terminal.app
windows from older runs, and resets env → localhost.

Ports:
  :8080  vepathos-mcp
  :3000  vepathos-api-doc  (OAuth / login)
  :3005  vepathos-router-client (web UI)
  :8100  smart-import (always loopback)

Commands:
  up 8080 [3000] [3005]             start those tunnels, write every repo's env, stay until Ctrl+C
  start [--mcp-only] [--terminal]   start (default: 8080+3000+3005); --mcp-only = up 8080
  clean / stop                      kill tunnels + close Terminal windows + localhost
  local                             env → localhost only
  status                            env + tunnel state
  tunnel …                          manual URLs

Ports:
  8080  MCP adapter. Required for ChatGPT / dashboard AI (OpenAI cannot hit localhost).
  3000  api-doc. Only if Google/Shopify OAuth must reach this laptop from the internet.
  3005  router UI. Only if the dashboard itself must be opened from another device.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# Marker in Terminal.app window titles (legacy --terminal mode + clean).
TERMINAL_TITLE_PREFIX = "vepathos-dev-tunnel-"

WORKSPACE = Path(__file__).resolve().parents[2]
MCP = WORKSPACE / "vepathos-mcp"
API = WORKSPACE / "vepathos-api-doc"
ROUTER = WORKSPACE / "vepathos-router-client"
SMART = WORKSPACE / "vepathos-smart-import"

MCP_ENV = MCP / ".env"
API_LOCAL = API / ".env.local"
API_ENV = API / ".env"
ROUTER_LOCAL = ROUTER / ".env.local"
SMART_ENV = SMART / ".env"

RUN_DIR = MCP / ".dev-tunnels"
STATE = RUN_DIR / "state.json"

LOCAL_MCP = "http://localhost:8080"
LOCAL_API = "http://localhost:3000"
LOCAL_WEB = "http://localhost:3005"
LOCAL_SI = "http://127.0.0.1:8100"

# Fixed metrics ports — pass these to cloudflared so we can poll /quicktunnel.
METRICS = {"mcp": "127.0.0.1:19281", "api": "127.0.0.1:19282", "web": "127.0.0.1:19283"}
ORIGINS = {"mcp": "http://127.0.0.1:8080", "api": "http://127.0.0.1:3000", "web": "http://127.0.0.1:3005"}
PORT_TO_NAME = {"8080": "mcp", "3000": "api", "3005": "web"}
NAME_TO_PORT = {"mcp": 8080, "api": 3000, "web": 3005}

_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass(frozen=True)
class Urls:
    mcp: str
    api: str
    web: str | None = None
    smart_import: str = LOCAL_SI

    @property
    def mcp_host(self) -> str:
        return urlparse(self.mcp).netloc

    @property
    def api_host(self) -> str:
        return urlparse(self.api).netloc

    @property
    def mcp_origin(self) -> str:
        p = urlparse(self.mcp)
        return f"{p.scheme}://{p.netloc}"

    @property
    def api_origin(self) -> str:
        p = urlparse(self.api)
        return f"{p.scheme}://{p.netloc}"


def die(msg: str, code: int = 2) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def normalize_origin(raw: str, *, name: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        die(f"{name} URL is empty")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        die(f"invalid {name} URL: {raw!r}")
    if parsed.path not in ("", "/"):
        die(f"{name} must be an origin (no path): {raw!r}")
    if "xxxx" in parsed.netloc:
        die(f"{name} looks like a placeholder ({raw!r}).")
    return f"{parsed.scheme}://{parsed.netloc}"


def is_loopback(host: str) -> bool:
    return host.startswith("localhost") or host.startswith("127.0.0.1")


def read_text(path: Path) -> str:
    if not path.is_file():
        die(f"missing file: {path}")
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def upsert(path: Path, updates: dict[str, str], *, create: bool = False) -> list[str]:
    if not path.is_file():
        if not create:
            print(f"  skip (missing): {path}")
            return []
        path.parent.mkdir(parents=True, exist_ok=True)
        text = ""
    else:
        text = read_text(path)

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] = lines[-1] + "\n"

    seen: set[str] = set()
    out: list[str] = []
    changed: list[str] = []

    for line in lines:
        if line.lstrip().startswith("#"):
            out.append(line)
            continue
        match = _KEY_LINE.match(line.rstrip("\r\n"))
        if not match:
            out.append(line)
            continue
        key = match.group(1)
        if key not in updates:
            out.append(line)
            continue
        value = updates[key]
        if match.group(2) != value:
            changed.append(key)
        out.append(f"{key}={value}\n")
        seen.add(key)

    missing = [key for key in updates if key not in seen]
    if missing:
        if out and out[-1].strip():
            out.append("\n")
        out.append("# --- sync-dev-urls (local only) ---\n")
        for key in missing:
            out.append(f"{key}={updates[key]}\n")
            changed.append(key)

    write_text(path, "".join(out))
    rel = path
    try:
        rel = path.relative_to(WORKSPACE)
    except ValueError:
        pass
    print(f"  {rel}: {', '.join(changed) if changed else 'unchanged'}")
    return changed


def allowed_hosts(*hosts: str) -> str:
    entries = ["localhost:*", "127.0.0.1:*"]
    for host in hosts:
        if not host or is_loopback(host):
            continue
        bare = host.split(":", 1)[0]
        if bare not in entries:
            entries.append(bare)
    return ",".join(entries)


def allowed_origins(*origins: str) -> str:
    entries = ["http://localhost:*", "http://127.0.0.1:*"]
    for origin in origins:
        if not origin:
            continue
        if origin.startswith("https://") or (
            origin.startswith("http://") and "localhost" not in origin and "127.0.0.1" not in origin
        ):
            if origin not in entries:
                entries.append(origin)
    return ",".join(entries)


def restart_hint(*, web: bool = False) -> None:
    print()
    print("Reiniciá vos (no lo hago en background):")
    print("  1. vepathos-mcp          (:8080)  — matar y: .venv/bin/python -m vepathos_mcp")
    print("  2. vepathos-api-doc       (:3000)  — reiniciar next dev")
    print("  3. vepathos-router-client (:3005) — reiniciar next dev")
    print("Smart Import (:8100) no hace falta reiniciar.")
    print()
    print("ChatGPT connector / VEPATHOS_MCP_HTTP_URL = túnel de :8080 + /mcp")
    print(f"Store callbacks = {env_get(API_LOCAL, 'INTEGRATIONS_PUBLIC_BASE_URL') or 'not configured'} (dedicated ingress when pinned)")


def env_get(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_web_url_for_report(explicit: str | None = None) -> str:
    if explicit:
        return explicit
    for key in ("NEXT_PUBLIC_APP_URL", "AUTH_URL", "NEXTAUTH_URL"):
        value = env_get(ROUTER_LOCAL, key)
        if value:
            return value
    state = load_state()
    tunnels = state.get("tunnels") or {}
    if isinstance(tunnels.get("web"), dict) and tunnels["web"].get("url"):
        return str(tunnels["web"]["url"])
    if state.get("web"):
        return str(state["web"])
    live = fetch_quicktunnel(METRICS["web"])
    if live:
        return live
    for pid, cmd in cloudflared_pids(all_tunnels=True):
        if "3005" not in cmd:
            continue
        for metrics in listen_ports_for_pid(pid):
            found = fetch_quicktunnel(metrics)
            if found:
                return found
    return LOCAL_WEB


def print_url_report(*, mcp: str, api: str, web: str | None = None) -> None:
    web_url = resolve_web_url_for_report(web)
    print()
    print("URLs:")
    print(f"  MCP  (:8080)  {mcp}")
    print(f"  API  (:3000)  {api}")
    print(f"  WEB  (:3005)  {web_url}")
    print(f"  MCP connector  {mcp.rstrip('/')}/mcp")
    print(f"  Abrí el chat/UI   {web_url}")


def print_google_internet_steps(*, api: str, web: str) -> None:
    """Quick tunnels change every start — Google needs this redirect URI registered."""

    callback = f"{api.rstrip('/')}/api/auth/callback/google"
    print()
    print("=" * 72)
    print("GOOGLE — redirect_uri_mismatch se arregla así (cada start = host nuevo)")
    print("=" * 72)
    print("1. https://console.cloud.google.com/apis/credentials")
    print("2. Tu OAuth 2.0 Client ID → Authorized redirect URIs → Add URI")
    print("3. Pegá EXACTAMENTE esta (es la API :3000, NO la WEB :3005):")
    print(f"   {callback}")
    print("4. Authorized JavaScript origins (Add):")
    print(f"   {api.rstrip('/')}")
    print(f"   {web.rstrip('/')}")
    print("5. Save → esperá ~30–60s")
    print("6. Reiniciá api-doc (:3000) y router (:3005) — NEXT_PUBLIC_* no hot-reload")
    print("7. Entrá SOLO por la WEB:")
    print(f"   {web}")
    print("8. Sign in with Google")
    print()
    print("  Dejá también: http://localhost:3000/api/auth/callback/google")
    print("=" * 72)
    try:
        subprocess.run(["pbcopy"], input=callback.encode(), check=True)
        print("  ✓ callback copiado al portapapeles")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "google-callback.txt").write_text(callback + "\n", encoding="utf-8")
    print(f"  ✓ también en {RUN_DIR / 'google-callback.txt'}")
    print("  (no abro Google Console — pegá el URI vos en Credentials → OAuth client)")


def configure_stores(raw: str) -> None:
    """Pin only store callbacks. Never use this restricted host as API/auth/MCP origin."""
    parsed = urlparse(raw.strip())
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        die("stores URL must be an HTTPS origin without credentials, port, path, query or fragment")
    origin = f"https://{parsed.hostname}"
    upsert(API_LOCAL, {"INTEGRATIONS_DEV_PUBLIC_BASE_URL": origin,
                       "INTEGRATIONS_PUBLIC_BASE_URL": origin})
    print(f"Stores only: {origin} → ingress 127.0.0.1:3011 → API :3000")
    for kind in ("mercadolibre", "shopify", "tiendanube"):
        print(f"  {kind} callback: {origin}/api/integrations/{kind}/callback")
        print(f"  {kind} webhook:  {origin}/api/integrations/{kind}/webhook")
    print("Restart api-doc if it does not reload .env.local. MCP/auth/UI settings unchanged.")
    print(f"ngrok http http://127.0.0.1:3011 --url {origin} --inspect=false")


def apply(urls: Urls) -> None:
    mode = "localhost" if is_loopback(urls.mcp_host) and is_loopback(urls.api_host) else "tunnel"
    web_for_report = urls.web or resolve_web_url_for_report()
    api_is_local = is_loopback(urls.api_host)
    web_is_local = is_loopback(urlparse(web_for_report).netloc)
    internet_login = mode == "tunnel" and not api_is_local and not web_is_local

    print(f"Mode: {mode}" + (" — acceso desde internet" if internet_login else ""))
    print(f"  MCP  (:8080)  {urls.mcp}")
    print(f"  API  (:3000)  {urls.api}" + ("  ← Google OAuth / callback" if not api_is_local else "  ← Google local"))
    print(f"  WEB  (:3005)  {web_for_report}" + ("" if urls.web else "  (no tocada)"))
    print(f"  SI   (:8100)  {urls.smart_import}")
    print()

    web_hosts: list[str] = []
    web_origins: list[str] = []
    if urls.web:
        web_hosts.append(urlparse(urls.web).netloc)
        web_origins.append(urls.web)

    # api-doc: NEXT_PUBLIC_APP_URL MUST be the API origin (Auth.js + CanonicalHostRedirect +
    # Google redirect_uri). The chat/UI origin goes in NEXT_PUBLIC_ROUTER_APP_URL (SSO/CORS).
    router_public = urls.web if urls.web else LOCAL_WEB

    upsert(
        MCP_ENV,
        {
            "MCP_PUBLIC_URL": urls.mcp,
            "MCP_ALLOWED_HOSTS": allowed_hosts(urls.mcp_host, urls.api_host, *web_hosts),
            "MCP_ALLOWED_ORIGINS": allowed_origins(urls.mcp_origin, urls.api_origin, *web_origins),
            "OAUTH_ISSUER": urls.api,
            "OAUTH_JWKS_URL": f"{LOCAL_API}/api/jwks",
            "VEPATHOS_API_BASE_URL": LOCAL_API,
        },
    )
    upsert(
        API_LOCAL,
        {
            "MCP_RESOURCE_URI": urls.mcp,
            "MCP_OAUTH_ISSUER": urls.api,
            "NEXTAUTH_URL": urls.api,
            "AUTH_URL": urls.api,
            "NEXT_PUBLIC_APP_URL": urls.api,
            "NEXT_PUBLIC_ROUTER_APP_URL": router_public,
            "SMART_IMPORT_URL": urls.smart_import,
            **({"INTEGRATIONS_PUBLIC_BASE_URL": env_get(API_LOCAL, "INTEGRATIONS_DEV_PUBLIC_BASE_URL")}
               if env_get(API_LOCAL, "INTEGRATIONS_DEV_PUBLIC_BASE_URL")
               else ({} if api_is_local else {"INTEGRATIONS_PUBLIC_BASE_URL": urls.api})),
        },
    )
    if API_ENV.is_file():
        upsert(API_ENV, {"SMART_IMPORT_URL": urls.smart_import})

    if ROUTER_LOCAL.is_file():
        router_updates: dict[str, str] = {
            "VEPATHOS_MCP_DIRECT_URL": f"{LOCAL_MCP}/mcp",
            "VEPATHOS_MCP_HTTP_URL": f"{urls.mcp}/mcp",
            "NEXT_PUBLIC_APIDOC_URL": urls.api,
            "APIDOC_URL": LOCAL_API,
            # Driver /r/ links: never leave a dead trycloudflare host after --mcp-only.
            "NEXT_PUBLIC_DRIVE_URL": urls.web if urls.web else LOCAL_WEB,
        }
        if urls.web:
            router_updates["NEXT_PUBLIC_APP_URL"] = urls.web
            router_updates["AUTH_URL"] = urls.web
            router_updates["NEXTAUTH_URL"] = urls.web
        upsert(ROUTER_LOCAL, router_updates)
    else:
        print(f"  skip (missing): {ROUTER_LOCAL}")

    if SMART_ENV.is_file():
        cors = (
            ["*"]
            if api_is_local
            else [urls.api_origin, "http://localhost:3000", "http://localhost:3005"]
        )
        if urls.web and urls.web not in cors:
            cors.insert(0, urls.web)
        upsert(SMART_ENV, {"SMART_IMPORT_CORS_ORIGINS": ",".join(cors)})
    else:
        print(f"  skip (missing): {SMART_ENV}")

    if mode == "localhost":
        print()
        print("Localhost listo.")
    elif internet_login:
        print_google_internet_steps(api=urls.api, web=web_for_report)
    elif not api_is_local:
        print()
        print(f"Google callback: {urls.api}/api/auth/callback/google")
    else:
        print()
        print("Google login en http://localhost:3000 (solo esta máquina).")

    print_url_report(mcp=urls.mcp, api=urls.api, web=urls.web or web_for_report)
    if not api_is_local and urls.mcp_origin == urls.api_origin:
        print()
        print("error: MCP y API quedaron en el MISMO host. OpenAI pegaría /mcp a Next (404).")
        print("       8080 y 3000 tienen que ser dos tunnels distintos.")



def load_state() -> dict:
    if not STATE.is_file():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(data: dict) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def cloudflared_pids(*, include_web: bool = False, all_tunnels: bool = False) -> list[tuple[int, str]]:
    """Return (pid, command) for cloudflared quick tunnels we own."""

    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    except subprocess.CalledProcessError:
        return []

    targets = ["8080", "3000", "19281", "19282"]
    if include_web or all_tunnels:
        targets.extend(["3005", "19283"])

    found: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cmd = parts[1]
        exe = cmd.split(None, 1)[0]
        # Only the cloudflared binary — never a shell wrapper that embeds the string.
        if not exe.endswith("cloudflared"):
            continue
        if "tunnel" not in cmd:
            continue
        if not all_tunnels and not any(t in cmd for t in targets):
            continue
        found.append((pid, cmd))
    return found


def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.15)
        except OSError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def close_tunnel_terminal_windows() -> int:
    """Close macOS Terminal.app windows left from our cloudflared tunnels."""

    if sys.platform != "darwin":
        return 0
    # Match window title (new) or history (older runs without title).
    markers = [
        TERMINAL_TITLE_PREFIX,
        "--metrics 127.0.0.1:19281",
        "--metrics 127.0.0.1:19282",
        "--metrics 127.0.0.1:19283",
        "cloudflared tunnel --url http://127.0.0.1:8080",
        "cloudflared tunnel --url http://127.0.0.1:3000",
        "cloudflared tunnel --url http://127.0.0.1:3005",
    ]
    # Build AppleScript that closes any window whose name/history hits a marker.
    checks = " or ".join(
        f'(winName contains {json.dumps(m)} or hist contains {json.dumps(m)})' for m in markers
    )
    script = f"""
tell application "Terminal"
  set closedCount to 0
  set wins to windows
  repeat with w in wins
    try
      set winName to name of w as text
      set hist to ""
      try
        set hist to history of selected tab of w as text
      end try
      if {checks} then
        close w
        set closedCount to closedCount + 1
      end if
    end try
  end repeat
  return closedCount as text
end tell
"""
    try:
        out = subprocess.check_output(["osascript", "-e", script], text=True, stderr=subprocess.DEVNULL)
        n = int((out or "0").strip() or "0")
        if n:
            print(f"  closed {n} Terminal window(s)")
        return n
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 0


def clean_tunnels(*, include_web: bool = False, all_tunnels: bool = False) -> int:
    """Kill matching cloudflared quick tunnels. Returns how many were signalled."""

    procs = cloudflared_pids(include_web=include_web, all_tunnels=all_tunnels)
    if not procs:
        print("  no matching cloudflared tunnel processes")
    else:
        for pid, cmd in procs:
            short = cmd if len(cmd) < 120 else cmd[:117] + "…"
            print(f"  killing pid={pid}  {short}")
            kill_pid(pid)
    close_tunnel_terminal_windows()
    return len(procs)


def fetch_quicktunnel(metrics: str, *, timeout_s: float = 0.8) -> str | None:
    url = f"http://{metrics}/quicktunnel"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
        host = (data.get("hostname") or "").strip()
        if not host:
            return None
        return normalize_origin(
            host if host.startswith("http") else f"https://{host}",
            name="tunnel",
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, SystemExit):
        return None


def listen_ports_for_pid(pid: int) -> list[str]:
    try:
        out = subprocess.check_output(
            ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    ports: list[str] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if not parts:
            continue
        name = parts[-1] if parts[-1] != "(LISTEN)" else (parts[-2] if len(parts) > 1 else "")
        if ":" not in name:
            continue
        host_port = name
        if host_port.startswith("*:") or host_port.startswith("[::]:"):
            host_port = "127.0.0.1:" + host_port.rsplit(":", 1)[-1]
        ports.append(host_port)
    return ports


def discover_url_for_origin(origin: str, preferred_metrics: str) -> str | None:
    got = fetch_quicktunnel(preferred_metrics)
    if got:
        return got
    port = origin.rsplit(":", 1)[-1]
    for pid, cmd in cloudflared_pids(all_tunnels=True):
        if port not in cmd:
            continue
        for metrics in listen_ports_for_pid(pid):
            got = fetch_quicktunnel(metrics)
            if got:
                return got
    return None


def cloudflared_command(name: str) -> str:
    return (
        f"cloudflared tunnel --url {ORIGINS[name]} --protocol http2 "
        f"--metrics {METRICS[name]} --no-autoupdate"
    )


def cloudflared_argv(name: str) -> list[str]:
    return shlex.split(cloudflared_command(name))


def start_cloudflared_children(names: list[str]) -> list[tuple[str, subprocess.Popen, object]]:
    """Run cloudflared as children of this process; logs under .dev-tunnels/."""

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    children: list[tuple[str, subprocess.Popen, object]] = []
    for name in names:
        log_path = RUN_DIR / f"{name}.log"
        log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — kept open for child life
        proc = subprocess.Popen(
            cloudflared_argv(name),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        children.append((name, proc, log_f))
        print(f"  started {name} pid={proc.pid}  log={log_path}")
    return children


def open_terminal_windows(names: list[str]) -> bool:
    """Optional: open macOS Terminal windows (visible logs). Prefer child mode."""

    if sys.platform != "darwin":
        return False
    for name in names:
        title = f"{TERMINAL_TITLE_PREFIX}{name}"
        # Set window title then exec cloudflared so the shell is replaced.
        inner = (
            f"printf '\\033]0;{title}\\007'; "
            f"exec {cloudflared_command(name)}"
        )
        script = (
            'tell application "Terminal"\n'
            "  activate\n"
            f"  do script {json.dumps(inner)}\n"
            "end tell"
        )
        try:
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True, text=True)
            print(f"  opened Terminal → {name} ({title})")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            err = ""
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                err = f" ({exc.stderr.strip()})"
            print(f"  warn: could not open Terminal for {name}{err}", file=sys.stderr)
            return False
    return True


def stop_children(children: list[tuple[str, subprocess.Popen, object]]) -> None:
    for name, proc, log_f in children:
        if proc.poll() is None:
            print(f"  stopping {name} pid={proc.pid}")
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                kill_pid(proc.pid)
        try:
            log_f.close()
        except OSError:
            pass
    deadline = time.time() + 3
    while time.time() < deadline:
        if all(p.poll() is not None for _, p, _ in children):
            break
        time.sleep(0.15)
    for name, proc, _ in children:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                kill_pid(proc.pid)


def wait_all_quicktunnels(names: list[str], *, timeout_s: float = 45.0) -> dict[str, str]:
    pending = set(names)
    urls: dict[str, str] = {}
    deadline = time.time() + timeout_s
    last_report = 0.0
    print(f"  polling {', '.join(names)} (≤{int(timeout_s)}s)…")
    while pending and time.time() < deadline:
        for name in list(pending):
            got = discover_url_for_origin(ORIGINS[name], METRICS[name])
            if got:
                urls[name] = got
                pending.remove(name)
                print(f"  {name}: {got}")
        if not pending:
            break
        now = time.time()
        if now - last_report >= 2.0:
            print(f"  …still waiting: {', '.join(sorted(pending))}")
            last_report = now
        time.sleep(0.15)
    if pending:
        die(
            "timeout waiting for: "
            + ", ".join(sorted(pending))
            + f". Check logs under {RUN_DIR}/."
        )
    for name, origin in urls.items():
        wait_hostname_resolves(origin)
        if name == "mcp":
            probe_mcp_public(origin)
    return urls


def wait_hostname_resolves(origin: str, *, timeout_s: float = 20.0) -> None:
    host = urlparse(origin).hostname or ""
    if not host or is_loopback(host):
        return
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            socket.getaddrinfo(host, 443)
            return
        except OSError:
            time.sleep(0.4)
    print(f"  warn: DNS still missing for {host} — el chat de OpenAI va a fallar hasta que resuelva")


def probe_mcp_public(origin: str) -> None:
    url = origin.rstrip("/") + "/mcp"
    req = urllib.request.Request(
        url,
        data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}',
        method="POST",
        headers={"content-type": "application/json", "accept": "application/json, text/event-stream"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            print(f"  probe {url} → {resp.status} (inesperado; debería ser 401 del adapter)")
    except urllib.error.HTTPError as err:
        snippet = err.read()[:120]
        if err.code == 401 and b"html" not in snippet.lower():
            print(f"  probe {url} → 401 JSON (adapter OK, no es Next)")
            return
        print(f"  warn: {url} → {err.code} {snippet!r}")
        if b"<!DOCTYPE" in snippet or b"<html" in snippet.lower():
            print("       Eso es HTML de Next. El túnel de MCP está pegado a :3000, no a :8080.")
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        print(f"  warn: {url} no responde todavía ({err})")


def port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def warn_origin_down(names: list[str]) -> None:
    for name in names:
        port = NAME_TO_PORT[name]
        if port_listening(port):
            continue
        print(f"  warn: nada escucha en :{port} ({name}). El tunnel va a 502 hasta que arranques el proceso.")


def print_cloudflared_commands(names: list[str]) -> None:
    print()
    for name in names:
        print(f"  # {name} → {ORIGINS[name]}")
        print(f"  {cloudflared_command(name)}")
        print()


def shutdown_to_localhost(
    *,
    children: list[tuple[str, subprocess.Popen, object]] | None = None,
    include_web: bool = True,
    reason: str = "stop",
) -> None:
    print()
    print(f"Shutting down ({reason})…")
    if children:
        stop_children(children)
    clean_tunnels(include_web=include_web, all_tunnels=True)
    save_state({"mode": "localhost", "tunnels": {}})
    print()
    print("Env → localhost")
    apply(Urls(mcp=LOCAL_MCP, api=LOCAL_API, web=LOCAL_WEB))
    restart_hint(web=True)


def cmd_clean(*, reset_env: bool = True, include_web: bool = False, all_tunnels: bool = False) -> None:
    print(
        "Clean: cerrando tunnels (mcp :8080 + api :3000"
        + (", web :3005" if include_web or all_tunnels else "")
        + ") + Terminal…"
    )
    n = clean_tunnels(include_web=include_web, all_tunnels=all_tunnels)
    print(f"  signalled {n} process(es)")
    save_state({"mode": "localhost", "tunnels": {}})
    if reset_env:
        print()
        print("Env → localhost")
        apply(
            Urls(
                mcp=LOCAL_MCP,
                api=LOCAL_API,
                web=LOCAL_WEB if include_web or all_tunnels else None,
            )
        )
        restart_hint(web=include_web or all_tunnels)


def cmd_start(*, names: list[str], use_terminal: bool) -> None:
    if subprocess.run(["which", "cloudflared"], capture_output=True).returncode != 0:
        die("cloudflared not found. brew install cloudflared")
    unknown = [name for name in names if name not in ORIGINS]
    if unknown:
        die(f"unknown tunnel(s): {', '.join(unknown)}")
    tunnel_names = names
    mcp_only = tunnel_names == ["mcp"]

    children: list[tuple[str, subprocess.Popen, object]] = []

    def _on_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    prev_int = signal.signal(signal.SIGINT, _on_signal)
    prev_term = signal.signal(signal.SIGTERM, _on_signal)

    try:
        print("1/4 Clean…")
        clean_tunnels(include_web=True, all_tunnels=True)
        time.sleep(0.5)
        save_state({"mode": "waiting", "tunnels": {}})

        print()
        if mcp_only:
            print("  Solo :8080. ChatGPT + chat del dashboard (OpenAI). Google queda en localhost:3000.")
            print("  No hace falta túnel de :3000 ni :3005 si usás esta máquina.")
        else:
            labels = ", ".join(f"{name}=:{NAME_TO_PORT[name]}" for name in tunnel_names)
            print(f"  Tunnels: {labels}")
            if "api" in tunnel_names:
                print("  Después: registrá el callback de Google (se imprime y se copia).")

        warn_origin_down(tunnel_names)

        print(f"2/4 Arrancando {len(tunnel_names)} tunnel(s)…")
        if use_terminal:
            opened = open_terminal_windows(tunnel_names)
            if not opened:
                print("  Fallback: children en este proceso")
                children = start_cloudflared_children(tunnel_names)
        else:
            children = start_cloudflared_children(tunnel_names)

        print("3/4 Esperando hostnames…")
        urls_map = wait_all_quicktunnels(tunnel_names, timeout_s=60.0)

        print()
        print("4/4 Configurando env…")
        apply(
            Urls(
                mcp=urls_map.get("mcp") or env_get(MCP_ENV, "MCP_PUBLIC_URL") or LOCAL_MCP,
                api=urls_map.get("api") or LOCAL_API,
                web=urls_map.get("web") or LOCAL_WEB,
            )
        )
        save_state(
            {
                "mode": "tunnel-" + "+".join(tunnel_names),
                "tunnels": {
                    name: {
                        "origin": ORIGINS[name],
                        "metrics": METRICS[name],
                        "url": urls_map[name],
                    }
                    for name in tunnel_names
                },
                "web": urls_map.get("web", LOCAL_WEB),
            }
        )
        restart_hint(web="web" in tunnel_names or not mcp_only)

        print()
        print("=" * 72)
        print("Tunnels activos. Dejá esta ventana abierta.")
        print("Ctrl+C → cierra tunnels + Terminal leftovers + env → localhost")
        if children:
            print(f"Logs: {RUN_DIR}/{{mcp,api,web}}.log")
        print("=" * 72)

        # Stay attached until Ctrl+C or a child dies.
        while True:
            for name, proc, _ in children:
                code = proc.poll()
                if code is not None:
                    die(f"tunnel {name} exited (code={code}). See {RUN_DIR}/{name}.log")
            # Terminal mode: children empty — just keep metrics alive.
            if not children:
                for name in tunnel_names:
                    if not fetch_quicktunnel(METRICS[name], timeout_s=0.5):
                        die(f"tunnel {name} metrics down — cloudflared murió?")
            time.sleep(1.0)

    except KeyboardInterrupt:
        shutdown_to_localhost(
            children=children,
            include_web=True,
            reason="Ctrl+C",
        )
        raise SystemExit(0) from None
    except SystemExit:
        # die() / timeout — still tear down children so we don't leave orphans.
        if children:
            stop_children(children)
            close_tunnel_terminal_windows()
        raise
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)


def cmd_local() -> None:
    apply(Urls(mcp=LOCAL_MCP, api=LOCAL_API, web=LOCAL_WEB))
    state = load_state()
    state["mode"] = "localhost"
    save_state(state)
    restart_hint(web=True)


def show_status() -> None:
    mcp = env_get(MCP_ENV, "MCP_PUBLIC_URL") or "(unset)"
    api = env_get(API_LOCAL, "NEXTAUTH_URL") or env_get(MCP_ENV, "OAUTH_ISSUER") or "(unset)"
    web = resolve_web_url_for_report()

    print("URLs:")
    print(f"  MCP  (:8080)  {mcp}")
    print(f"  API  (:3000)  {api}")
    print(f"  WEB  (:3005)  {web}")
    if mcp not in ("(unset)",):
        print(f"  MCP connector  {mcp.rstrip('/')}/mcp")
    print()
    print("Env detail:")
    print(f"  mcp  MCP_PUBLIC_URL         = {env_get(MCP_ENV, 'MCP_PUBLIC_URL') or '(unset)'}")
    print(f"  mcp  OAUTH_ISSUER           = {env_get(MCP_ENV, 'OAUTH_ISSUER') or '(unset)'}")
    print(f"  api  NEXTAUTH_URL           = {env_get(API_LOCAL, 'NEXTAUTH_URL') or '(unset)'}")
    print(f"  api  MCP_RESOURCE_URI       = {env_get(API_LOCAL, 'MCP_RESOURCE_URI') or '(unset)'}")
    print(f"  web  NEXT_PUBLIC_APP_URL    = {env_get(ROUTER_LOCAL, 'NEXT_PUBLIC_APP_URL') or '(unset)'}")
    print(f"  web  NEXT_PUBLIC_APIDOC_URL = {env_get(ROUTER_LOCAL, 'NEXT_PUBLIC_APIDOC_URL') or '(unset)'}")
    print(f"  web  VEPATHOS_MCP_HTTP_URL  = {env_get(ROUTER_LOCAL, 'VEPATHOS_MCP_HTTP_URL') or '(unset)'}")
    print(f"  web  VEPATHOS_MCP_DIRECT_URL= {env_get(ROUTER_LOCAL, 'VEPATHOS_MCP_DIRECT_URL') or '(unset)'}")
    print(f"  api  INTEGRATIONS_PUBLIC_BASE_URL = {env_get(API_LOCAL, 'INTEGRATIONS_PUBLIC_BASE_URL') or '(unset)'}")
    print()
    state = load_state()
    print(f"Managed mode: {state.get('mode', '(never run)')}")
    procs = cloudflared_pids(all_tunnels=True)
    if procs:
        print(f"cloudflared processes ({len(procs)}):")
        for pid, cmd in procs:
            short = cmd if len(cmd) < 100 else cmd[:97] + "…"
            print(f"  pid={pid}  {short}")
    else:
        print("cloudflared processes: none")
    print("quicktunnel metrics:")
    for name, metrics in METRICS.items():
        url = fetch_quicktunnel(metrics, timeout_s=1.0)
        print(f"  {name} ({metrics}): {url or 'down'}")


def parse_ports(raw: list[str]) -> list[str]:
    names: list[str] = []
    for item in raw:
        for part in item.replace(",", " ").split():
            port = part.strip().lstrip(":")
            name = PORT_TO_NAME.get(port)
            if not name:
                die(f"puerto {part!r} no. Usá 8080 (MCP), 3000 (API) o 3005 (WEB).")
            if name not in names:
                names.append(name)
    if not names:
        die("pasá al menos un puerto, ej. 8080")
    return names


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(
        description="Localhost by default; Cloudflare tunnels stay attached until Ctrl+C.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "  %(prog)s up 8080                 # MCP only (ChatGPT + dashboard AI)\n"
            "  %(prog)s up 8080 3000 3005       # MCP + Google/Shopify + UI pública\n"
            "  %(prog)s start --mcp-only        # igual que up 8080\n"
            "  %(prog)s clean\n"
            "  %(prog)s status\n"
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser(
        "start",
        help="Start tunnels (children), sync env, stay until Ctrl+C",
    )
    p_start.add_argument(
        "--mcp-only",
        action="store_true",
        help="Only MCP tunnel (ChatGPT); Google stays on localhost (this machine)",
    )
    p_start.add_argument(
        "--terminal",
        action="store_true",
        help="Also open Terminal.app windows for cloudflared logs (closed on Ctrl+C/clean)",
    )
    p_up = sub.add_parser("up", help="Start tunnels for the given ports (8080/3000/3005) and sync env")
    p_up.add_argument("ports", nargs="+", help="8080=MCP, 3000=API, 3005=WEB")
    p_up.add_argument("--terminal", action="store_true", help="Logs in Terminal.app windows")
    # Backward-compat no-ops / aliases
    p_start.add_argument("--no-open", action="store_true", help=argparse.SUPPRESS)
    p_clean = sub.add_parser("clean", aliases=["stop"], help="Kill tunnels + close Terminal + env → localhost")
    p_clean.add_argument(
        "--all",
        action="store_true",
        default=True,
        help=argparse.SUPPRESS,
    )
    p_clean.add_argument("--keep-other", action="store_true", help="Only kill mcp/api/web metrics ports")
    sub.add_parser("local", help="Env → localhost only")
    sub.add_parser("status", help="Show env + tunnel processes")

    p_tunnel = sub.add_parser("tunnel", help="Manual: set public URLs without waiting")
    p_tunnel.add_argument("mcp_url")
    p_tunnel.add_argument("api_url", nargs="?", default=None)
    p_tunnel.add_argument("--login", "--api", dest="api_url_flag", default=None, help=argparse.SUPPRESS)
    p_tunnel.add_argument("--web", dest="web_url", default=None)

    p_stores = sub.add_parser("stores", help="Pin store callback origin independently of API/MCP tunnels")
    p_stores.add_argument("url", nargs="?", default="https://uninstall-resale-bring.ngrok-free.dev")

    args = parser.parse_args(argv)
    if args.cmd == "stores":
        configure_stores(args.url)
        return

    if args.cmd == "status":
        show_status()
        return
    if args.cmd == "up":
        cmd_start(names=parse_ports(args.ports), use_terminal=args.terminal)
        return
    if args.cmd == "start":
        cmd_start(
            names=["mcp"] if args.mcp_only else ["mcp", "api", "web"],
            use_terminal=args.terminal,
        )
        return
    if args.cmd in ("clean", "stop"):
        cmd_clean(
            reset_env=True,
            include_web=True,
            all_tunnels=not args.keep_other,
        )
        return
    if args.cmd == "local":
        cmd_local()
        return

    mcp = normalize_origin(args.mcp_url, name="mcp")
    api_raw = args.api_url or args.api_url_flag
    api = normalize_origin(api_raw, name="api") if api_raw else LOCAL_API
    web = normalize_origin(args.web_url, name="web") if args.web_url else None
    apply(Urls(mcp=mcp, api=api, web=web))
    save_state({"mode": "tunnel-manual", "tunnels": {}, "mcp": mcp, "api": api, "web": web or ""})
    restart_hint(web=bool(web))


if __name__ == "__main__":
    main(sys.argv[1:])
