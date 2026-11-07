"""The setup window: a frameless pywebview surface over the installer core.

The window is deliberately not a wizard. It shows the brand, one line of
progress and one quiet row of facts, the same way a modern Mac/Windows app
installs: the user opened the file, so the only question is how far along it is.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from arena.setup import core

LOG = logging.getLogger("arena.setup")

WINDOW_W = 540
WINDOW_H = 372


def _web_root() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    if getattr(sys, "frozen", False):
        return base / "arena" / "setup" / "web"
    return Path(__file__).resolve().parent / "web"


class SetupApi:
    """JS bridge. Every method is polled or invoked from the setup page.

    ``js_api`` objects are walked recursively by pywebview to build the page's
    bridge surface, so every public attribute must be a plain value. Holding the
    window in ``_window`` is what keeps that walk away from the WinForms form and
    the WebView2 objects behind it: in 1.5.3 it was public, the walk reached the
    COM side from a background thread and the message pump deadlocked, which left
    the setup window frozen on "Starting…" (docs/REWORK.md, 1.5.4).
    """

    def __init__(self, mode: str = "install", version: str = "", silent: bool = False,
                 update_from: Optional[str] = None):
        self.mode = mode
        self.version = version
        self.silent = silent
        self.update_from = update_from
        self._window = None
        self.closed = False
        self._bridge_seen = False
        self._lock = threading.Lock()
        self._state = {
            "phase": "idle", "label": "Ready", "detail": "", "progress": 0.0,
            "error": None, "install_dir": str(core.install_dir()),
        }
        self._started = False

    # -- state ------------------------------------------------------------
    def _set(self, **kwargs) -> None:
        with self._lock:
            self._state.update(kwargs)

    def _snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _on_step(self, step: dict) -> None:
        self._set(phase="working", label=step.get("label", "Working"),
                  detail=step.get("detail", ""), progress=float(step.get("progress", 0.0)))

    # -- bridge -----------------------------------------------------------
    def info(self) -> dict:
        self._bridge_seen = True
        return {
            "mode": self.mode,
            "version": self.version,
            "silent": self.silent,
            "install_dir": str(core.install_dir()),
            "installed_version": core.installed_version(),
        }

    def state(self) -> dict:
        self._bridge_seen = True
        return self._snapshot()

    def start(self) -> dict:
        """Kick off the install/uninstall on a worker thread."""
        self._bridge_seen = True
        with self._lock:
            if self._started:
                return dict(self._state)
            self._started = True
        self._set(phase="working", label="Preparing", progress=0.01, error=None)
        worker = threading.Thread(target=self._work, daemon=True)
        worker.start()
        return self._snapshot()

    def _work(self) -> None:
        try:
            if self.mode == "uninstall":
                result = core.run_uninstall(remove_user_data=self._wipe_data, on_step=self._on_step)
                self._set(phase="done", progress=1.0, label="Removed", detail="")
                self._result = result
                if self.silent:
                    self._quit(0)
                return
            result = core.run_install(version=self.version, on_step=self._on_step, launch=False)
            self._set(phase="done", label="Installed", progress=1.0,
                      install_dir=result.get("install_dir") or str(core.install_dir()))
            if self.mode == "update" and core.install_manifest_path().exists():
                core.launch_installed()
                self._quit(0)
        except Exception as exc:  # noqa: BLE001 - surfaced in the window
            LOG.exception("setup failed")
            message = str(exc) or exc.__class__.__name__
            self._set(phase="error", error=message, label="Setup could not finish")
            if self.silent:
                self._quit(1)

    _wipe_data = False
    _result: dict = {}

    def set_options(self, wipe_data: bool = False) -> bool:
        self._bridge_seen = True
        self._wipe_data = bool(wipe_data)
        return True

    def launch(self) -> bool:
        self._bridge_seen = True
        return core.launch_installed()

    def close(self) -> bool:
        self._bridge_seen = True
        self._quit(0)
        return True

    def _quit(self, code: int) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            if self._window is not None:
                self._window.destroy()
        except Exception:
            pass
        threading.Timer(0.35, lambda: os._exit(code)).start()


def _silent_install(mode: str, version: str, wait_pid: int = 0) -> int:
    try:
        if wait_pid:
            # The app asked us to replace it once it has closed.
            core.wait_for_exit(int(wait_pid))
        result = core.run_install(version=version)
        LOG.info("silent setup done: %s", result)
        if mode == "update":
            core.launch_installed()
        return 0
    except Exception:
        LOG.exception("silent setup failed")
        return 1


def _setup_log_file() -> Optional[Path]:
    """Where the setup window writes its own log, next to the app's desktop.log."""
    try:
        from arena.config import CONFIG_DIR
        return Path(CONFIG_DIR) / "setup.log"
    except Exception:  # pragma: no cover - config import is cheap, but never fatal
        return None


def _configure_logging() -> None:
    """A windowed exe has no console, so a hang would leave no trace at all."""
    path = _setup_log_file()
    if not path:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(path), encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root = logging.getLogger()
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:  # pragma: no cover - a log file is a convenience, never a blocker
        pass


def _bridge_watchdog(api: SetupApi, seconds: float = 12.0) -> None:
    """Install even when the page never reaches the bridge.

    pywebview injects ``window.pywebview`` asynchronously, a moment after the
    page loads. 1.5.3's window hung exactly there and left the installer on
    "Starting…" forever, so after a grace period the same install runs from
    Python and the window closes itself. ``start()`` is idempotent, so a late
    bridge call can never install twice.
    """
    time.sleep(seconds)
    if api._bridge_seen or api._started:
        return
    LOG.warning("setup window never reached the bridge; installing without it")
    api.start()
    deadline = time.time() + 300
    while time.time() < deadline:
        if api._snapshot().get("phase") in ("done", "error"):
            break
        time.sleep(0.25)
    api._quit(0 if api._snapshot().get("phase") == "done" else 1)


def run_setup(mode: str = "install", version: str = "", silent: bool = False,
              wait_pid: int = 0) -> int:
    """Entry point for ``--setup`` / ``--uninstall``."""
    if not core.is_supported():
        print("The installer runs on Windows only.")
        return 2
    _configure_logging()
    if silent:
        return _silent_install(mode, version, wait_pid)

    try:
        import webview
    except Exception as exc:  # pragma: no cover - webview missing
        LOG.exception("webview unavailable")
        print(f"Cannot open the setup window: {exc}")
        return 3

    api = SetupApi(mode=mode, version=version, silent=silent)
    page = _web_root() / "index.html"
    if not page.exists():  # pragma: no cover - broken bundle
        print(f"Setup assets missing: {page}")
        return 4

    window = webview.create_window(
        "Arena Desktop Setup",
        url=page.as_uri(),
        js_api=api,
        width=WINDOW_W,
        height=WINDOW_H,
        resizable=False,
        frameless=True,
        easy_drag=True,
        on_top=True,
        background_color="#f4f1ea",
        text_select=False,
    )
    api._window = window
    LOG.info("setup window created: mode=%s version=%s page=%s", mode, version, page)
    threading.Thread(target=_bridge_watchdog, args=(api,), daemon=True).start()
    webview.start(debug=bool(os.environ.get("ARENA_DEBUG")), gui="edgechromium")
    return 0
