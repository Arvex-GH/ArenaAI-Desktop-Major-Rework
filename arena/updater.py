"""In-app updates.

The app checks the official release feed, downloads the published build to a
temp folder, checks its size (and SHA-256 when GitHub provides a digest), then
hands over to a short-lived helper process that waits for the app to exit,
swaps the application file and starts the new version again.

User data (chats, accounts, settings) lives in the config folder and is never
part of the swap, so an update never asks the user to set anything up again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from arena.version import APP_VERSION

LOG = logging.getLogger("arena.updater")

DEFAULT_REPO = "Arvex-GH/ArenaAI-Desktop-Major-Rework"
API_ROOT = "https://api.github.com"
ALLOWED_HOSTS = {"github.com", "api.github.com", "objects.githubusercontent.com",
                 "github-releases.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_ASSET_BYTES = 400 * 1024 * 1024
CHUNK = 1 << 16

ProgressFn = Optional[Callable[[int, int], None]]


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------
def feed_repo() -> str:
    """owner/name of the release feed (config ``update_repo`` can override)."""
    override = (os.environ.get("ARENA_UPDATE_REPO") or "").strip()
    if override:
        return override
    try:
        from arena.config import load_config
        value = str(load_config().get("update_repo") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return DEFAULT_REPO


def _version_tuple(value: str) -> tuple:
    parts = []
    for bit in str(value or "").lstrip("v").split("."):
        digits = re.match(r"\d+", bit)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def _asset_kind(name: str) -> int:
    """Lower is better: 0 = setup/installer, 1 = plain build."""
    low = (name or "").lower()
    if not low.endswith(".exe"):
        return 9
    if "setup" in low or "installer" in low:
        return 0
    if low.startswith("arena-desktop"):
        return 1
    return 5


def pick_asset(release: dict) -> Optional[dict]:
    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    assets = [a for a in assets if (a.get("name") or "").lower().endswith(".exe")]
    if not assets:
        return None
    assets.sort(key=lambda a: (_asset_kind(a.get("name") or ""), -(a.get("size") or 0)))
    return assets[0]


def normalize_release(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    tag = str(payload.get("tag_name") or "").lstrip("v")
    asset = pick_asset(payload)
    installer = None
    if asset:
        installer = {
            "name": asset.get("name"),
            "url": asset.get("browser_download_url") or asset.get("url"),
            "size": int(asset.get("size") or 0),
            "digest": (asset.get("digest") or "").strip() or None,
            "kind": "installer" if _asset_kind(asset.get("name") or "") == 0 else "build",
        }
    return {
        "version": tag or None,
        "tag": payload.get("tag_name"),
        "name": payload.get("name") or "",
        "notes": (payload.get("body") or "").strip(),
        "page_url": payload.get("html_url"),
        "published_at": payload.get("published_at"),
        "prerelease": bool(payload.get("prerelease")),
        "installer": installer,
        "repo": feed_repo(),
    }


def fetch_latest(channel: str = "stable", timeout: float = 6.0) -> dict:
    """One GitHub API call; returns the normalised release or raises."""
    repo = feed_repo()
    if channel == "beta":
        url = f"{API_ROOT}/repos/{repo}/releases?per_page=10"
    else:
        url = f"{API_ROOT}/repos/{repo}/releases/latest"
    resp = requests.get(
        url,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"Arena-Desktop/{APP_VERSION}"},
        timeout=timeout,
    )
    if resp.status_code == 404:
        return {"version": None, "error": "No release is published yet.", "repo": repo}
    if resp.status_code != 200:
        return {"version": None, "error": f"GitHub returned {resp.status_code}", "repo": repo}
    payload = resp.json()
    if channel == "beta" and isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict) and not item.get("draft")), {})
    if not isinstance(payload, dict):
        return {"version": None, "error": "Unexpected release format.", "repo": repo}
    return normalize_release(payload)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "Arena-Desktop.exe").strip("-")
    return cleaned or "Arena-Desktop.exe"


def download_dir() -> Path:
    base = os.environ.get("TEMP") or tempfile.gettempdir()
    path = Path(base) / "ArenaDesktopUpdate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def is_allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "") in ALLOWED_HOSTS


def download_asset(asset: dict, on_progress: ProgressFn = None,
                   cancel: Optional[threading.Event] = None,
                   timeout: float = 60.0) -> Path:
    url = str(asset.get("url") or "")
    if not is_allowed_url(url):
        raise ValueError("The update link is not from the release feed.")
    expected_size = int(asset.get("size") or 0)
    if expected_size > MAX_ASSET_BYTES:
        raise ValueError("The update file is larger than expected.")

    target = download_dir() / _safe_filename(asset.get("name") or "Arena-Desktop.exe")
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    received = 0

    with requests.get(url, stream=True, timeout=timeout, allow_redirects=True,
                      headers={"User-Agent": f"Arena-Desktop/{APP_VERSION}"}) as resp:
        if resp.status_code != 200:
            raise IOError(f"Download failed (HTTP {resp.status_code}).")
        total = int(resp.headers.get("Content-Length") or expected_size or 0)
        with partial.open("wb") as handle:
            for chunk in resp.iter_content(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Update cancelled.")
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if received > MAX_ASSET_BYTES:
                    raise ValueError("The update file is larger than expected.")
                if on_progress:
                    on_progress(received, total or expected_size)

    if expected_size and received != expected_size:
        partial.unlink(missing_ok=True)
        raise IOError("The update file was incomplete.")

    declared = str(asset.get("digest") or "")
    if declared.startswith("sha256:"):
        if declared.split(":", 1)[1].lower() != digest.hexdigest().lower():
            partial.unlink(missing_ok=True)
            raise IOError("The update file did not match its published checksum.")

    os.replace(partial, target)
    return target


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------
def spawn_apply_update(pid: int, downloaded: Path) -> bool:
    """Start a second copy of this build whose only job is the file swap."""
    exe = Path(sys.executable if getattr(sys, "frozen", False) else sys.argv[0]).resolve()
    if not exe.exists():
        return False
    try:
        subprocess.Popen(
            [str(exe), "--apply-update", "--pid", str(int(pid)), "--file", str(downloaded)],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return True
    except OSError:
        LOG.exception("could not start the update helper")
        return False


def run_apply_update(pid: int, downloaded: Path) -> int:
    """``--apply-update`` entry point: wait, swap, restart."""
    from arena.setup import core
    try:
        result = core.apply_update(pid, Path(downloaded))
        LOG.info("update applied: %s", result)
        return 0
    except Exception:
        LOG.exception("update helper failed")
        return 1


def launch_installer(path: Path, pid: int = 0) -> bool:
    """Run a published setup file without its window.

    Our own builds are the setup program themselves, so they take ``--setup``.
    Anything else falls back to the usual Inno Setup switches.
    """
    name = (path.name or "").lower()
    if "arena-desktop" in name:
        args = [str(path), "--setup", "--update", "--silent"]
        if pid:
            args += ["--pid", str(int(pid))]
    else:
        args = [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
    try:
        subprocess.Popen(args, close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True
    except OSError:
        LOG.exception("could not start the published installer")
        return False


# ---------------------------------------------------------------------------
# Shared job state for the local API
# ---------------------------------------------------------------------------
class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.state = {
            "phase": "idle", "received": 0, "total": 0, "pct": 0,
            "error": None, "path": None, "version": None, "name": None,
        }

    def reset(self, **kwargs) -> dict:
        with self.lock:
            self.state = {"phase": "idle", "received": 0, "total": 0, "pct": 0,
                          "error": None, "path": None, "version": None, "name": None}
            self.state.update(kwargs)
            self.cancel = threading.Event()
            return dict(self.state)

    def update(self, **kwargs) -> None:
        with self.lock:
            self.state.update(kwargs)
            if "received" in kwargs and self.state.get("total"):
                pct = int(min(100, 100 * kwargs["received"] / max(self.state["total"], 1)))
                # Bytes only ever arrive, so within one download the percentage
                # can only grow. Clamping here means a late or reordered
                # `received` can never walk the bar backwards; a new download
                # clears it in reset().
                self.state["pct"] = max(int(self.state.get("pct") or 0), pct)

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start_download(self, release: dict) -> dict:
        asset = (release or {}).get("installer")
        # An update that is already being installed owns the file: a second
        # download would rewrite it underneath the setup that is copying it.
        with self.lock:
            if self.state.get("phase") == "installing":
                return dict(self.state)
        if not asset:
            return self.reset(phase="error", error="This release has no download for Windows.")
        self.reset(phase="downloading", version=release.get("version"), total=int(asset.get("size") or 0),
                   name=asset.get("name"))
        cancel = self.cancel

        def on_progress(received: int, total: int) -> None:
            # The first answer decides the total: a later one (a redirect that
            # reports nothing, a chunked response) must not shrink it, because
            # that is what makes a full bar jump back to the middle.
            settled = int(self.state.get("total") or 0)
            self.update(received=received, total=max(settled, total or 0) or received)

        def work() -> None:
            try:
                path = download_asset(asset, on_progress=on_progress, cancel=cancel)
                self.update(phase="ready", path=str(path), pct=100, received=self.state.get("received", 0))
            except InterruptedError:
                self.reset(phase="idle")
            except Exception as exc:  # noqa: BLE001 - shown in the dialog
                LOG.exception("update download failed")
                self.update(phase="error", error=str(exc) or exc.__class__.__name__)

        threading.Thread(target=work, daemon=True).start()
        return self.snapshot()


JOB = Job()
_last_check = {"t": 0.0, "data": None}
