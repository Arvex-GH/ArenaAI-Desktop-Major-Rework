"""Optional external components (v5.5): detection + guided installation.

The app itself is a single EXE and needs none of these for normal chats.
They unlock specific features and are installed only on explicit request:

- cloudflared  - Agent Gateway (temporary Cloudflare Quick Tunnel).
                 One official .exe, version + SHA-256 pinned below,
                 verified after download, kept in <config>/bin.
- Node.js      - local MCP servers started with npx. Detection + winget
                 or the official download page.
- Python       - local MCP servers run as Python scripts. Same deal.
- WebView2     - the app window itself. Detection + Microsoft bootstrapper.

Nothing here ever downloads or runs anything silently.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from arena.config import CONFIG_DIR

BIN_DIR = CONFIG_DIR / "bin"

CLOUDFLARED_VERSION = "2026.10.7"
CLOUDFLARED_URL = (f"https://github.com/cloudflare/cloudflared/releases/download/"
                   f"{CLOUDFLARED_VERSION}/cloudflared-windows-amd64.exe")
CLOUDFLARED_SHA256 = "2493dbc168923bb3e59e5e7716f71deced92992eea9acd6e9d74f84ac7751343"
CLOUDFLARED_SIZE = 55390512

WEBVIEW2_BOOTSTRAPPER_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
NODE_DOWNLOAD_URL = "https://nodejs.org/en/download"
PYTHON_DOWNLOAD_URL = "https://www.python.org/downloads/windows/"

_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}
_version_cache: Dict[str, Any] = {}


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def cloudflared_path() -> Optional[Path]:
    candidate = BIN_DIR / ("cloudflared.exe" if _is_windows() else "cloudflared")
    return candidate if candidate.is_file() else None


def _run_version(args: List[str], cache_key: str) -> Optional[str]:
    if cache_key in _version_cache:
        return _version_cache[cache_key]
    version = None
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=8)
        text = (proc.stdout or proc.stderr or "").strip().splitlines()
        if text:
            version = text[0].strip()[:80]
    except Exception:
        version = None
    _version_cache[cache_key] = version
    return version


def _webview2_version() -> Optional[str]:
    if not _is_windows():
        return None
    try:
        import winreg
    except Exception:
        return None
    client = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (0, getattr(winreg, "KEY_WOW64_32KEY", 0)):
            try:
                with winreg.OpenKey(root, client, 0, winreg.KEY_READ | view) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                    if value and str(value) != "0.0.0.0":
                        return str(value)
            except OSError:
                continue
    return None


def detect() -> List[Dict[str, Any]]:
    """Current status of every known component (cheap enough for a settings tab)."""
    cf = cloudflared_path()
    cf_version = _run_version([str(cf), "--version"], "cloudflared") if cf else None
    node = shutil.which("node")
    python = shutil.which("py") or shutil.which("python")
    webview2 = _webview2_version()
    winget = bool(shutil.which("winget")) if _is_windows() else False
    return [
        {
            "id": "cloudflared", "name": "cloudflared (Cloudflare Tunnel)",
            "purpose": "Agent Gateway: the temporary secure tunnel that lets the Arena "
                       "Agent reach MCP servers on this computer.",
            "installed": cf is not None,
            "version": (cf_version or "").replace("cloudflared version ", "") or None,
            "path": str(cf) if cf else None,
            "install_kind": "download" if _is_windows() else "unsupported",
            "size_mb": round(CLOUDFLARED_SIZE / 1048576),
            "source": "github.com/cloudflare/cloudflared (official release, SHA-256 verified)",
            "note": "Single file, no administrator rights. Some antivirus tools flag "
                    "cloudflared because tunnel tools are dual-use; this copy is the "
                    "official Cloudflare build and its checksum is verified.",
        },
        {
            "id": "node", "name": "Node.js",
            "purpose": "Local MCP servers installed with npx (most presets).",
            "installed": node is not None,
            "version": _run_version([node, "--version"], "node") if node else None,
            "path": node,
            "install_kind": "winget" if winget else "link",
            "source": "nodejs.org" if not winget else "winget (OpenJS.NodeJS.LTS)",
            "note": "Only needed if you add npx-based MCP servers.",
        },
        {
            "id": "python", "name": "Python",
            "purpose": "Local MCP servers that run as Python scripts.",
            "installed": python is not None,
            "version": _run_version([python, "--version"], "python") if python else None,
            "path": python,
            "install_kind": "winget" if winget else "link",
            "source": "python.org" if not winget else "winget (Python.Python.3.12)",
            "note": "The app carries its own Python internally; user MCP scripts "
                    "use the system one.",
        },
        {
            "id": "webview2", "name": "Microsoft Edge WebView2 Runtime",
            "purpose": "Renders the app window and the arena.ai session window.",
            "installed": webview2 is not None,
            "version": webview2,
            "path": None,
            "install_kind": "bootstrapper" if _is_windows() else "unsupported",
            "source": "Microsoft (Evergreen bootstrapper)",
            "note": "Comes with Windows 11 and Microsoft Edge; almost always present.",
        },
    ]


def install_status() -> Dict[str, Any]:
    with _lock:
        return {"jobs": {cid: dict(job) for cid, job in _jobs.items()}}


def _set_job(cid: str, **updates: Any) -> None:
    with _lock:
        job = _jobs.setdefault(cid, {"id": cid, "state": "idle", "progress": 0, "error": None})
        job.update(updates)


def _download(url: str, dest: Path, cid: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "ArenaDesktop-Components"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total:
                    _set_job(cid, progress=min(99, int(done * 100 / total)))


def _install_cloudflared() -> None:
    cid = "cloudflared"
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = BIN_DIR / "cloudflared.download"
        _download(CLOUDFLARED_URL, tmp, cid)
        digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
        if digest != CLOUDFLARED_SHA256:
            tmp.unlink(missing_ok=True)
            _set_job(cid, state="error", progress=0,
                     error="Downloaded file failed SHA-256 verification and was deleted.")
            return
        target = BIN_DIR / "cloudflared.exe"
        os.replace(tmp, target)
        _version_cache.pop("cloudflared", None)
        _set_job(cid, state="done", progress=100, error=None)
    except Exception as e:  # network/disk/AV problems: report, never crash
        _set_job(cid, state="error", progress=0, error=f"{type(e).__name__}: {e}")


def _spawn_detached(args: List[str]) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) if _is_windows() else 0
    subprocess.Popen(args, creationflags=flags, close_fds=True)


def _install_winget(cid: str, package: str) -> None:
    try:
        _spawn_detached(["winget", "install", "-e", "--id", package,
                         "--accept-source-agreements", "--accept-package-agreements"])
        _set_job(cid, state="installer_started", progress=100, error=None,
                 hint="Complete the installer window; this page detects it afterwards.")
    except Exception as e:
        _set_job(cid, state="error", progress=0, error=f"{type(e).__name__}: {e}")


def _install_webview2() -> None:
    cid = "webview2"
    try:
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        dest = BIN_DIR / "MicrosoftEdgeWebview2Setup.exe"
        _download(WEBVIEW2_BOOTSTRAPPER_URL, dest, cid)
        _spawn_detached([str(dest)])
        _set_job(cid, state="installer_started", progress=100, error=None,
                 hint="Complete the Microsoft installer, then restart Arena Desktop.")
    except Exception as e:
        _set_job(cid, state="error", progress=0, error=f"{type(e).__name__}: {e}")


def start_install(cid: str) -> Dict[str, Any]:
    """Kick off installation; returns immediately with the job + optional URL action."""
    components = {c["id"]: c for c in detect()}
    comp = components.get(cid)
    if comp is None:
        return {"ok": False, "error": f"Unknown component: {cid}"}
    if comp["installed"]:
        return {"ok": True, "already": True}
    kind = comp["install_kind"]
    if kind == "link":
        url = NODE_DOWNLOAD_URL if cid == "node" else PYTHON_DOWNLOAD_URL
        return {"ok": True, "open_url": url}
    if kind == "unsupported":
        return {"ok": False, "error": "Automatic installation is only supported on Windows."}
    with _lock:
        current = _jobs.get(cid) or {}
        if current.get("state") == "downloading":
            return {"ok": True, "already": True}
    _set_job(cid, state="downloading", progress=0, error=None)
    if cid == "cloudflared":
        threading.Thread(target=_install_cloudflared, daemon=True).start()
    elif cid == "webview2":
        threading.Thread(target=_install_webview2, daemon=True).start()
    elif cid == "node":
        _install_winget(cid, "OpenJS.NodeJS.LTS")
    elif cid == "python":
        _install_winget(cid, "Python.Python.3.12")
    else:
        return {"ok": False, "error": f"No installer for: {cid}"}
    return {"ok": True}
