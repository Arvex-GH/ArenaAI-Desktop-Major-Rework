"""
Arena Desktop: a native window (pywebview + Edge WebView2) over the local Flask
UI, plus a hidden arena.ai window (the engine) that sends chats from arena's
own page. Sign-in and human checks show it as a pane inside the main window.
"""

import logging
import os
import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path

LOG = logging.getLogger("arena.desktop")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_flask(port: int):
    from arena.web.app import app, start_connector_warmup
    # Local MCP servers come up with the app, in the background, without a
    # console window each: the start-up screen reports them as they connect.
    start_connector_warmup()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


def wait_port(port: int, timeout: float = 10.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def _flag_value(name):
    """Value after a --flag in argv, or None (flags: --profile, --mode, --background)."""
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
            return sys.argv[i + 1]
    return None


def _switch_profile(port, wanted):
    """--profile <id|email>: activate that account on the local server before the UI loads."""
    try:
        import requests
        data = requests.get(f"http://127.0.0.1:{port}/api/accounts", timeout=5).json()
        items = data.get("accounts") if isinstance(data, dict) else data
        for acc in items or []:
            if str(acc.get("id")) == wanted or str(acc.get("email") or "").lower() == wanted.lower():
                if acc.get("current"):
                    return
                requests.post(f"http://127.0.0.1:{port}/api/accounts/switch",
                              json={"id": acc.get("id"), "account_id": acc.get("id")}, timeout=15)
                return
        LOG.info("profile %r not found", wanted)
    except Exception:
        LOG.exception("profile switch failed")


_QUICK_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Quick ask</title>
<style>
 body{margin:0;background:#14171a;color:#e9f2ea;font-family:Segoe UI,system-ui,sans-serif}
 .wrap{padding:14px}
 h1{font-size:13px;margin:0 0 8px;font-weight:650}
 textarea{width:100%;box-sizing:border-box;height:74px;resize:none;border-radius:8px;border:1px solid #3b4140;background:#1d2221;color:#e9f2ea;padding:8px;font:inherit}
 .row{display:flex;justify-content:flex-end;gap:8px;margin-top:10px}
 button{border-radius:7px;border:1px solid #8dd39a;background:#b8e5bf;color:#17251a;padding:6px 14px;font-weight:650;cursor:pointer}
 button.q{background:#262a2b;color:#cfd8d2;border-color:#3b4140}
</style></head><body><div class="wrap">
<h1>Quick ask \u2014 starts a new chat</h1>
<textarea id="t" placeholder="Ask something\u2026 (Enter to send, Shift+Enter for a new line)"></textarea>
<div class="row"><button class="q" id="cancel">Cancel</button><button id="send">Send</button></div>
</div><script>
 const t=document.getElementById('t'); t.focus();
 const go=()=>{ const v=t.value.trim(); if(!v) return; window.pywebview.api.submit(v); };
 document.getElementById('send').onclick=go;
 document.getElementById('cancel').onclick=()=>window.pywebview.api.submit('');
 t.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();go();} if(e.key==='Escape') window.pywebview.api.submit(''); });
</script></body></html>"""


class _QuickAskApi:
    """js_api of the quick-ask window; submit('') just closes it."""

    def __init__(self, tray):
        self._tray = tray
        self._window = None

    def submit(self, text):
        self._tray._quick_submit(text)

class Tray:
    """System-tray icon + global Ctrl+Alt+A show/hide hotkey (Windows, best effort).

    Optional chrome around the window: every failure is logged and the app
    keeps running without it. With the tray on, closing the window hides it
    instead of quitting; Quit lives in the tray menu."""

    def __init__(self, window, icon_path):
        self.window = window
        self.icon_path = icon_path
        self.icon = None
        self.visible = True
        self.quitting = False
        self.quick_window = None

    def show_window(self):
        self.visible = True
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def hide_window(self):
        self.visible = False
        try:
            self.window.hide()
        except Exception:
            pass

    def toggle(self):
        self.hide_window() if self.visible else self.show_window()

    def hotkey_pressed(self):
        # Ctrl+Alt+A: hide when open; otherwise open the quick-ask window
        # (the fastest path from anywhere in Windows to a new chat).
        self.hide_window() if self.visible else self.open_quick_ask()

    def notify(self, title, text, kind="done"):
        # Questions always toast (the agent is blocked on the user);
        # finished generations only when the window is tucked away.
        if kind == "done" and self.visible:
            return
        try:
            if self.icon:
                self.icon.notify(text, title)
        except Exception:
            LOG.exception("tray notify failed")

    def open_quick_ask(self):
        try:
            import webview
            if self.quick_window is not None:
                try:
                    self.quick_window.show()
                    self.quick_window.restore()
                    return
                except Exception:
                    self.quick_window = None
            api = _QuickAskApi(self)
            self.quick_window = webview.create_window(
                "Quick ask - Arena Desktop", html=_QUICK_HTML, js_api=api,
                width=560, height=200, resizable=False, on_top=True)
            api._window = self.quick_window
        except Exception:
            LOG.exception("quick ask unavailable")
            self.show_window()

    def _quick_submit(self, text):
        text = (text or "").strip()
        if self.quick_window is not None:
            try:
                self.quick_window.destroy()
            except Exception:
                pass
            self.quick_window = None
        if not text:
            return
        self.show_window()
        try:
            import json as _json
            self.window.evaluate_js(
                "window.__quickAsk && window.__quickAsk(" + _json.dumps(text) + ")")
        except Exception:
            LOG.exception("quick ask handoff failed")

    def on_closing(self):
        if self.quitting:
            return True
        self.hide_window()
        return False  # cancel the close; the app lives in the tray

    def quit(self):
        self.quitting = True
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        os._exit(0)

    def start(self) -> bool:
        try:
            import pystray
            from PIL import Image
            image = Image.open(self.icon_path)
        except Exception:
            LOG.exception("tray unavailable (pystray/Pillow/icon)")
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Open Arena Desktop", lambda *_: self.show_window(), default=True),
            pystray.MenuItem("Quick ask...", lambda *_: self.open_quick_ask()),
            pystray.MenuItem("Hide", lambda *_: self.hide_window()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )
        self.icon = pystray.Icon("Arena Desktop", image, "Arena Desktop", menu)
        try:
            from arena.web import app as _webapp
            _webapp.NOTIFY_HOOK = lambda title, text, kind="done": self.notify(title, text, kind)
        except Exception:
            LOG.exception("notify hook wiring failed")
        threading.Thread(target=self._run_icon, daemon=True).start()
        if sys.platform == "win32":
            threading.Thread(target=self._hotkey_loop, daemon=True).start()
        return True

    def _run_icon(self):
        try:
            run_detached = getattr(self.icon, "run_detached", None)
            if run_detached:
                run_detached()
            else:
                self.icon.run()
        except Exception:
            LOG.exception("tray icon stopped")

    def _hotkey_loop(self):
        try:
            import ctypes
            from ctypes import wintypes
            MOD_ALT, MOD_CONTROL, VK_A, WM_HOTKEY = 0x0001, 0x0002, 0x41, 0x0312
            user32 = ctypes.WinDLL("user32")
            if not user32.RegisterHotKey(None, 1, MOD_CONTROL | MOD_ALT, VK_A):
                LOG.info("global hotkey Ctrl+Alt+A could not be registered")
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self.hotkey_pressed()
        except Exception:
            LOG.exception("global hotkey unavailable")


def _dialog(name: str):
    import webview
    fd = getattr(webview, "FileDialog", None)
    if fd is not None:
        return getattr(fd, name)
    return getattr(webview, f"{name}_DIALOG")


class JsBridge:
    """js_api of the main window (local UI only). pywebview exposes every public attribute to JS
    and walks it recursively, so the window reference must stay private."""

    def __init__(self):
        self._window = None

    def open_file_dialog(self, file_types=None):
        ft = tuple(file_types or ()) + ("All files (*.*)",)
        result = self._window.create_file_dialog(_dialog("OPEN"), allow_multiple=True, file_types=ft)
        return list(result or [])

    def save_file_dialog(self, filename=""):
        result = self._window.create_file_dialog(_dialog("SAVE"), save_filename=filename)
        if isinstance(result, (list, tuple)):
            result = result[0] if result else None
        return result or None

    def choose_folder(self):
        result = self._window.create_file_dialog(_dialog("FOLDER"))
        return (result[0] if result else None) if isinstance(result, (list, tuple)) else result

    def open_preview(self, url, title="Preview"):
        """Show a site preview in its own WebView2 window, like arena.ai's
        preview pane. A top-level window also loads *.arena.site pages, which
        refuse to be framed (X-Frame-Options: SAMEORIGIN). One window, reused."""
        url = str(url or "")
        if not url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            return False
        import webview
        label = f"{str(title or 'Preview')[:60]} - Arena Desktop"
        win = getattr(self, "_preview", None)
        if win is not None:
            try:
                win.load_url(url)
                win.set_title(label)
                win.show()
                win.restore()
                return True
            except Exception:
                self._preview = None
        win = webview.create_window(label, url, width=1180, height=800, background_color="#242422")
        # Closing only hides the window, like the arena.ai window: tearing a
        # WebView2 window down while the app runs can hang every window.
        win.events.closing += self._preview_closing
        win.events.closed += lambda *a: setattr(self, "_preview", None)
        self._preview = win
        return True

    def _preview_closing(self, *a):
        win = getattr(self, "_preview", None)

        def _hide_later():
            # pywebview may still be inside the close when this handler returns.
            time.sleep(0.15)
            try:
                win.hide()
                win.load_url("about:blank")  # stop the site's scripts and media
            except Exception:
                pass

        if win is not None:
            threading.Thread(target=_hide_later, daemon=True).start()
        return False  # cancel the close


def _user32():
    import ctypes
    from ctypes import wintypes as w
    u = ctypes.WinDLL("user32")  # own instance: argtypes set here do not leak into other ctypes users
    u.EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    u.EnumWindows.argtypes = [u.EnumProc, w.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
    u.GetWindowLongW.argtypes = [w.HWND, ctypes.c_int]
    u.SetWindowLongW.argtypes = [w.HWND, ctypes.c_int, ctypes.c_long]
    u.SetParent.argtypes = [w.HWND, w.HWND]
    u.SetParent.restype = w.HWND
    u.ShowWindowAsync.argtypes = [w.HWND, ctypes.c_int]
    u.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
    return u


class Dock:
    """Hosts the arena.ai window as a pane on the right of the main window (Win32 SetParent),
    like a built-in browser, so sign-in and human checks never open a second window."""

    SHARE = 0.55  # part of the main window's width the pane takes

    def __init__(self, main_win, pane_win):
        self.main, self.pane = main_win, pane_win
        self.h_main = self.h_pane = None
        self.visible = False
        self._share = None
        self._closing = False
        self.u = _user32()

    def _find(self, title):
        import ctypes
        from ctypes import wintypes
        found, pid = [], os.getpid()

        def each(h, _):
            owner = wintypes.DWORD()
            self.u.GetWindowThreadProcessId(h, ctypes.byref(owner))
            buf = ctypes.create_unicode_buffer(256)
            self.u.GetWindowTextW(h, buf, 256)
            if owner.value == pid and buf.value == title:
                found.append(h)
            return True

        self.u.EnumWindows(self.u.EnumProc(each), 0)
        return found[0] if found else None

    def _embed(self) -> bool:
        if self.h_pane:
            return True
        self.h_main = self.h_main or self._find(self.main.title)
        pane = self._find(self.pane.title)
        if not (self.h_main and pane):
            return False
        style = self.u.GetWindowLongW(pane, -16) & 0xFFFFFFFF  # GWL_STYLE
        style &= ~(0x80000000 | 0x00C00000 | 0x00040000 | 0x00080000 | 0x00020000 | 0x00010000)  # popup, caption, frame, buttons
        style |= 0x40000000 | 0x04000000  # WS_CHILD | WS_CLIPSIBLINGS
        self.u.SetWindowLongW(pane, -16, style - (1 << 32) if style >= 1 << 31 else style)
        self.u.SetParent(pane, self.h_main)
        self.h_pane = pane
        return True

    def layout(self):
        if not (self.visible and self.h_pane):
            return
        import ctypes
        from ctypes import wintypes
        r = wintypes.RECT()
        self.u.GetClientRect(self.h_main, ctypes.byref(r))
        w, h = r.right, r.bottom
        pw = min(w, max(int(w * self.SHARE), 560))
        self.u.SetWindowPos(self.h_pane, None, w - pw, 0, pw, h, 0x0040 | 0x0020)  # HWND_TOP, SWP_SHOWWINDOW | SWP_FRAMECHANGED
        share = round(pw / max(w, 1), 4)
        if share != self._share:  # resizing fires often: only tell the page when the split changes
            self._share = share
            self.main.evaluate_js("window.arenaPane && arenaPane(true, %.4f)" % share)

    def prime(self):
        """Find the main window handle early: the closing handler must not send window messages."""
        if not self.h_main:
            self.h_main = self._find(self.main.title)

    def vanish(self):
        """Close in two steps: hide the window at once, then tear WebView2 down out of sight.
        pywebview runs this on a worker while the GUI thread waits, so the work happens on a new thread."""
        if self._closing:
            return None
        self._closing = True
        threading.Thread(target=self._quit, daemon=True).start()
        return False  # cancel this close; _quit closes for real

    def _quit(self):
        try:
            self.main.hide()
        finally:
            self.main.destroy()

    def show(self) -> bool:
        try:
            if not self._embed():
                return False
            self.visible = True
            self.pane.show()
            self.layout()
            return True
        except Exception:
            logging.getLogger("arena.desktop").exception("could not dock the arena.ai pane")
            self.visible = False
            return False

    def hide(self):
        self.visible = False
        self._share = None
        self.pane.hide()
        try:
            self.main.evaluate_js("window.arenaPane && arenaPane(false)")
        except Exception:
            pass


def _ensure_webview2() -> None:
    """Pre-flight (v5.5): without the WebView2 Runtime no window can open at
    all, so detect it before any UI exists and offer Microsoft's official
    bootstrapper instead of failing obscurely inside pywebview."""
    if not sys.platform.startswith("win"):
        return
    try:
        from arena import components
        if any(c["id"] == "webview2" and c["installed"] for c in components.detect()):
            return
    except Exception:
        return  # detection problems must never block launch on their own
    try:
        import ctypes
        import webbrowser
        MB_YESNO, MB_ICONQUESTION, IDYES = 0x4, 0x20, 6
        answer = ctypes.windll.user32.MessageBoxW(
            None,
            "Arena Desktop needs the free Microsoft Edge WebView2 Runtime, "
            "which was not found on this PC.\n\nOpen the official Microsoft "
            "download page now? Install it, then start Arena Desktop again.",
            "Arena Desktop — WebView2 Runtime missing", MB_YESNO | MB_ICONQUESTION)
        if answer == IDYES:
            webbrowser.open("https://developer.microsoft.com/en-us/microsoft-edge/webview2/")
    except Exception:
        pass
    os._exit(1)


def main():
    import webview
    from arena import contracts
    from arena.config import CONFIG_DIR
    from arena.engine import ENGINE

    contracts.load_overrides()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # An update replaces the exe in place; keep the install manifest truthful
    # so Settings and the updater report the version that is really running.
    try:
        from arena.version import APP_VERSION as _APP_VERSION
        from arena.setup import core as _setup_core
        if _setup_core.is_supported():
            _setup_core.sync_installed_manifest(_APP_VERSION)
    except Exception:
        pass
    logging.basicConfig(filename=str(CONFIG_DIR / "desktop.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # keep the log to what the app does
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("arena.ai.desktop")
        except Exception:
            pass

    port = int(os.environ.get("ARENA_PORT") or 0) or find_free_port()
    threading.Thread(target=start_flask, args=(port,), daemon=True).start()
    wait_port(port)
    profile = _flag_value("--profile")
    if profile:
        _switch_profile(port, profile)

    base = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
    ico = base / "assets" / "arena-cli.ico"

    _ensure_webview2()
    webview.settings["ALLOW_DOWNLOADS"] = True  # zip and media downloads from the UI
    from arena.config import load_config as _load_cfg
    cfg = _load_cfg()
    background = "--background" in sys.argv
    start_url = f"http://127.0.0.1:{port}/"
    mode_flag = _flag_value("--mode")
    if mode_flag:
        start_url += "?mode=" + urllib.parse.quote(mode_flag)
    bridge = JsBridge()
    main_win = webview.create_window("Arena Desktop", start_url, width=1280, height=840,
                                     min_size=(860, 600), background_color="#242422", text_select=True,
                                     js_api=bridge, hidden=background)
    bridge._window = main_win
    engine_win = webview.create_window("arena.ai - Arena Desktop", contracts.ORIGIN, width=1000, height=760, x=120, y=120,
                                       hidden=True, background_color="#242422", js_api=ENGINE.bridge())
    ENGINE.attach(engine_win, main_win)
    # Keep the Arena WebView as a genuine top-level window. Login, OAuth and
    # human checks need a separate window rather than a reparented child view.
    ENGINE.dock = None
    main_win.events.closed += lambda *a: os._exit(0)
    # Optional tray life: close hides to the tray; Ctrl+Alt+A toggles the window.
    if cfg.get("tray_enabled") or background:
        tray = Tray(main_win, base / "assets" / "logo.png")
        tray.visible = not background
        if tray.start():
            main_win.events.closing += tray.on_closing
        elif background:
            main_win.show()  # no tray to bring it back from: never start invisible

    storage = CONFIG_DIR / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage),
                  icon=str(ico) if ico.exists() else None, debug=bool(os.environ.get("ARENA_DEBUG")))
    os._exit(0)


if __name__ == "__main__":
    main()
