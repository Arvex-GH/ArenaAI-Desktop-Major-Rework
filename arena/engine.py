"""
Embedded arena.ai engine.

A hidden WebView2 window stays on https://arena.ai. Chats are sent from that
page, so arena's own reCAPTCHA and Cloudflare checks run in a real browser with
the user's real session. When arena asks for a visible check (reCAPTCHA
checkbox, Cloudflare "Just a moment..."), the window is shown to the human.
Nothing here solves, mints or fakes a challenge.

Rotation resilience: only endpoint paths from arena.contracts are used, and the
reCAPTCHA key is read from the live page's <script src="...render=KEY"> tag.
"""

import json
import logging
import queue
import re
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

from arena import contracts
from arena.engine_js import (  # page scripts and action ids
    _FETCH_JS, _CHECK_JS, _KEY_JS,
    _CX_MODULE_ID, _VOTE_MODULE_ID, _VOTE_ACTION_ID,
    _VOTE_RECAPTCHA_ACTION, _ARCHIVE_ACTION_ID, _ARCHIVE_ACTION_NAME,
    _PREFLIGHT_JS, _VOTE_JS, _ARCHIVE_JS,
    _DRIVE_HOOK_JS, _DRIVE_STATE_JS, _DRIVE_SEND_JS,
)

LOG = logging.getLogger("arena.desktop")


class _Bridge:
    """js_api of the engine window. arena.ai's page can call only these methods."""

    def __init__(self, engine):
        self._engine = engine

    def push(self, rid, seq, kind, data):
        self._engine._deliver(rid, seq, kind, data)

    def challenge_solved(self, token):
        self._engine._on_v2(token)

    def drive_event(self, kind, payload):
        self._engine._on_drive_event(kind, payload)


class EngineResponse:
    """Duck-types the parts of requests.Response the app uses."""

    def __init__(self, engine, rid: str, q: "queue.Queue", timeout: float):
        self._engine, self._rid, self._q, self._timeout = engine, rid, q, timeout
        self._done = False
        self.error: Optional[str] = None
        self.headers: Dict[str, str] = {}
        kind, data = self._next(min(timeout, 90))
        if kind == "status":
            # The page pushes "429|{\"ratelimit-modality\": \"video\", ...}".
            text = str(data)
            status_text, _, headers_text = text.partition("|")
            try:
                self.status_code = int(status_text)
            except ValueError:
                self.status_code = 0
            if headers_text:
                try:
                    parsed = json.loads(headers_text)
                    if isinstance(parsed, dict):
                        self.headers = {str(k): str(v) for k, v in parsed.items()}
                except ValueError:
                    pass
        else:
            self.status_code = 0
            self.error = str(data)
            self._finish()

    def _next(self, timeout=None):
        try:
            return self._q.get(timeout=timeout or self._timeout)
        except queue.Empty:
            return ("error", "timed out waiting for arena.ai")

    def _finish(self):
        self._done = True
        self._engine._close(self._rid)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def iter_text(self):
        while not self._done:
            kind, data = self._next()
            if kind == "chunk":
                yield str(data)
            else:
                if kind == "error":
                    self.error = str(data)
                self._finish()

    def iter_content(self, chunk_size=None):
        for text in self.iter_text():
            yield text.encode("utf-8")

    @property
    def text(self) -> str:
        if not hasattr(self, "_text"):
            self._text = "".join(self.iter_text())
        return self._text

    def json(self):
        return json.loads(self.text)


class BrowserEngine:
    def __init__(self):
        self.window = None
        self.loaded = False
        self.host = ""
        self.challenge: Optional[str] = None  # None | "recaptcha" | "cloudflare"
        self.v2_token: Optional[str] = None
        self.login_open = False
        self.visible = False
        self._streams: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._imported = False
        self._drive_lock = threading.Lock()
        self._drive_queue: Optional["queue.Queue"] = None

    def bridge(self) -> _Bridge:
        return _Bridge(self)

    def attach(self, window, main=None):
        self.window = window
        self.main = main
        window.events.loaded += lambda *a: threading.Thread(target=self._after_load, daemon=True).start()
        window.events.closing += self._on_closing

    def ready(self) -> bool:
        return self.window is not None and self.loaded

    def wait_ready(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        while time.time() < end and not self.ready():
            time.sleep(0.25)
        return self.ready()

    def state(self) -> dict:
        return {"attached": self.window is not None, "ready": self.ready(), "challenge": self.challenge,
                "host": self.host, "login_open": self.login_open, "open": self.visible,
                "separate_window": getattr(self, "dock", None) is None}

    # -- window lifecycle -------------------------------------------------
    def _on_closing(self, *a):
        self.visible = False
        if self.challenge == "recaptcha":
            self.challenge = None
        self.login_open = False
        def _deferred_hide():
            import time
            time.sleep(0.15)
            try:
                # hide() also drops the stay-on-top pin a human check sets;
                # left on, the reopened window covered the app.
                self.hide()
            except Exception:
                pass
        threading.Thread(target=_deferred_hide, daemon=True).start()
        return False

    def close_pane(self):
        """The user closed the arena.ai pane: drop any open check or sign-in and hide it."""
        if self.challenge == "recaptcha":
            self.challenge = None
        self.login_open = False
        threading.Thread(target=self.hide, daemon=True).start()

    def _after_load(self):
        try:
            title = str(self.window.evaluate_js("document.title") or "")
            self.host = str(self.window.evaluate_js("location.host") or "")
        except Exception:
            title = ""
        if "just a moment" in title.lower():
            self.loaded = False
            self.challenge = "cloudflare"
            self._present()
            return
        if not self.host.endswith("arena.ai"):
            self.loaded = False  # e.g. Google's sign-in page during login
            return
        if self.challenge == "cloudflare":
            self.challenge = None
            if not self.login_open:
                self.hide()
        if not self._imported and not self.has_session():
            self._imported = True
            from arena.config import COOKIE_FILE
            try:
                saved = COOKIE_FILE.read_text(encoding="utf-8").strip()
            except OSError:
                saved = ""
            if contracts.AUTH_COOKIE_PREFIX in saved and self.import_cookies(saved):
                return  # reloads; this handler runs again
        self._imported = True
        self.loaded = True
        self._patch_popups()

    def _patch_popups(self):
        """Redirect window.open() calls to the current window so OAuth works in WebView2."""
        if not self.window:
            return
        try:
            self.window.evaluate_js(
                "(function(){"
                "if(window.__arenaPopupPatched)return;"
                "window.__arenaPopupPatched=true;"
                "var _open=window.open;"
                "window.open=function(url){"
                "if(url&&(url.indexOf('accounts.google.com')!==-1"
                "||url.indexOf('supabase')!==-1"
                "||url.indexOf('auth')!==-1)){"
                "location.href=url;return null;}"
                "return _open.apply(window,arguments);};"
                "})();1"
            )
        except Exception:
            pass

    def _present(self):
        """Show the arena.ai page: as a pane inside the app window when docking works, else over it."""
        dock = getattr(self, "dock", None)
        if dock is not None and dock.show():
            self.visible = True
            return
        w, m = self.window, getattr(self, "main", None)
        spot = None
        if m is not None:
            try:
                spot = (int(m.x) + 40, int(m.y) + 40)
                w.resize(max(int(m.width) - 80, 720), max(int(m.height) - 80, 560))
                w.move(*spot)
            except Exception:
                spot = None
        w.show()
        self.visible = True
        if spot:
            try:
                w.move(*spot)  # the first show() can re-center the window on screen
            except Exception:
                pass
        try:
            w.restore()
        except Exception:
            pass
        if self.challenge:
            # A human check is blocking the send. pywebview's show() alone
            # can open the window behind the main one on Windows, so pin
            # the checkbox window on top until the check is over (hide()
            # clears it again).
            try:
                w.on_top = True
            except Exception:
                pass

    def show(self):
        if self.window:
            self._present()

    def hide(self):
        self.visible = False
        if self.window:
            try:
                self.window.on_top = False
            except Exception:
                pass
        dock = getattr(self, "dock", None)
        if dock is not None and dock.visible:
            dock.hide()
        elif self.window:
            self.window.hide()

    def open_login(self):
        self.login_open = True
        self._present()
        if not self.host.endswith("arena.ai"):
            self.window.load_url(contracts.ORIGIN)

    def finish_login(self):
        self.login_open = False
        self.hide()

    def open_path(self, path: str):
        """Show one arena.ai path in the separate engine window."""
        if not self.window or not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            return False
        self.loaded = False
        self.window.load_url(contracts.ORIGIN + path)
        self._present()
        return True

    def show_cloudflare(self):
        self.loaded = False
        self.challenge = "cloudflare"
        self.window.load_url(contracts.ORIGIN)
        self._present()

    def run_script(self, script: str):
        """Evaluate JS inside the arena.ai page; returns its value or None."""
        if not self.window:
            return None
        try:
            return self.window.evaluate_js(script)
        except Exception:
            return None

    def page_site_key(self) -> Optional[str]:
        try:
            key = self.window.evaluate_js(_KEY_JS)
        except Exception:
            return None
        return key if isinstance(key, str) and contracts.SITE_KEY_RE.fullmatch(key) else None

    # -- cookies ----------------------------------------------------------
    def cookies(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        try:
            for jar in self.window.get_cookies() or []:
                for name, morsel in jar.items():
                    domain = (morsel.get("domain") or "arena.ai").lstrip(".")
                    if domain == "arena.ai" or domain.endswith(".arena.ai"):
                        out[name] = morsel.value
        except Exception:
            pass
        # Some WebView2 builds do not enumerate the page cookies through
        # pywebview. Arena's auth chunks are readable by the page itself, so
        # use document.cookie as a fallback without leaving arena.ai.
        if not any(name.startswith(contracts.AUTH_COOKIE_PREFIX) for name in out):
            host = (self.host or "").lower()
            if self.window and (host == "arena.ai" or host.endswith(".arena.ai")):
                try:
                    raw = self.window.evaluate_js("document.cookie")
                except Exception:
                    raw = None
                if isinstance(raw, str):
                    for part in raw.split(";"):
                        if "=" not in part:
                            continue
                        name, value = part.strip().split("=", 1)
                        if name.startswith(contracts.AUTH_COOKIE_PREFIX):
                            out[name] = value
        return out

    def has_session(self) -> bool:
        return any(k.startswith(contracts.AUTH_COOKIE_PREFIX) for k in self.cookies())

    def import_cookies(self, cookie_str: str) -> bool:
        """Copy auth cookies into the engine, drop stale auth chunks, then reload arena.ai.

        arena's Supabase SSR cookies are not HttpOnly, so document.cookie can set them.
        """
        pairs = [p.strip() for p in (cookie_str or "").split(";")
                 if "=" in p and p.strip().startswith(contracts.AUTH_COOKIE_PREFIX)]
        if not pairs or not self.window:
            return False
        if not self.host.endswith("arena.ai"):
            self.window.load_url(contracts.ORIGIN)
            end = time.time() + 20
            while time.time() < end and not self.host.endswith("arena.ai"):
                time.sleep(0.25)
        if not (self.host == "arena.ai" or self.host.endswith(".arena.ai")):
            return False  # Never inject account cookies into a different site.
        names = [p.split("=", 1)[0] for p in pairs]
        js = ("(function(keep,pairs,prefix){document.cookie.split(';').forEach(function(c){"
              "var n=c.split('=')[0].trim();if(n.indexOf(prefix)===0&&keep.indexOf(n)<0)"
              "document.cookie=n+'=; path=/; max-age=0; secure';});"
              "pairs.forEach(function(p){document.cookie=p+'; path=/; max-age=34560000; secure; samesite=lax';});"
              "location.reload();return 1})(%s,%s,%s)") % (
            json.dumps(names), json.dumps(pairs), json.dumps(contracts.AUTH_COOKIE_PREFIX))
        self._imported = True
        self.loaded = False
        self.window.evaluate_js(js)
        return True

    def sync_to(self, client):
        """The engine owns the refresh-token family: copy its rotated auth cookies back to the app."""
        auth = {k: v for k, v in self.cookies().items() if k.startswith(contracts.AUTH_COOKIE_PREFIX)}
        if not auth:
            return
        cookie_str = "; ".join(f"{k}={v}" for k, v in sorted(auth.items()))
        if cookie_str != client.cookie_str:
            client.set_cookie(cookie_str)
            from arena.config import save_cookie
            save_cookie(cookie_str)

    def clear_session_cookies(self):
        """Drop auth cookies locally only (no server sign-out).

        Used when adding or switching accounts: the previous account's
        stored session must stay valid on Arena's side, so nothing is
        revoked — the window simply forgets it and reloads as a guest.
        """
        if not self.window:
            return
        self._imported = True
        self.loaded = False
        js = ("(function(){document.cookie.split(';').forEach(function(c){var n=c.split('=')[0].trim();"
              "if(n.indexOf(%s)===0)document.cookie=n+'=; path=/; max-age=0; secure';});location.reload()})();1"
              ) % (json.dumps(contracts.AUTH_COOKIE_PREFIX))
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    def logout(self):
        if not self.window:
            return
        self._imported = True
        self.loaded = False
        js = ("(async function(){try{await fetch(%s,{method:'POST',credentials:'include'})}catch(e){}"
              "document.cookie.split(';').forEach(function(c){var n=c.split('=')[0].trim();"
              "if(n.indexOf(%s)===0)document.cookie=n+'=; path=/; max-age=0; secure';});location.reload()})();1"
              ) % (json.dumps(contracts.PATHS["sign_out"]), json.dumps(contracts.AUTH_COOKIE_PREFIX))
        try:
            self.window.evaluate_js(js)
        except Exception:
            pass

    # -- requests ---------------------------------------------------------
    def request(self, path: str, body=None, method: Optional[str] = None, timeout: float = 180,
                v3_action: Optional[str] = None, headers: Optional[dict] = None,
                cx: bool = False) -> EngineResponse:
        """Run fetch() inside arena.ai's page; returns once the status line arrives.

        ``cx=True`` asks the page's own client-interaction module for a
        genuine ``cx`` blob and attaches it to the body — the same field
        the website's chat submit sends — before the fetch runs.  When the
        module is unavailable the request goes out without it, as before.
        """
        rid = uuid.uuid4().hex
        q: "queue.Queue" = queue.Queue()
        with self._lock:
            self._streams[rid] = {"q": q, "next": 0, "held": {}}
        args = ",".join(json.dumps(a) for a in (rid, path, method or ("POST" if body is not None else "GET"),
                                               body, contracts.KEYS["v3"], v3_action or contracts.RECAPTCHA_ACTION,
                                               headers, bool(cx and body is not None), _CX_MODULE_ID))
        run = getattr(self.window, "run_js", None) or self.window.evaluate_js
        try:
            run(_FETCH_JS % args)
        except Exception as e:
            self._deliver(rid, 0, "error", f"engine script failed: {e}")
        return EngineResponse(self, rid, q, timeout)

    # -- native Agent preflight ----------------------------------------------
    def native_preflight(self, action: str, timeout: float = 45) -> dict:
        """Ask the arena.ai page for submission prerequisites.

        Returns {"cx": str|None, "token_len": int, "trace": [str, ...]} where
        ``cx`` is a genuine client-interaction blob computed by the page's own
        module for this submission and ``token_len`` proves reCAPTCHA v3
        minting works in this page for ``action`` (length only; the probe
        token itself is discarded, the real request mints its own).

        If the interaction module is not loaded on the current page, the
        engine navigates to /agent (where it definitely is) and retries once.
        """
        result = {"cx": None, "token_len": 0, "trace": []}
        if not self.window:
            result["trace"] = ["no_window"]
            return result
        if self.login_open:
            result["trace"] = ["login_open"]
            return result
        with self._drive_lock:
            for attempt in range(2):
                data = self._run_preflight_probe(action, timeout)
                if data is None:
                    result["trace"].append("probe_no_answer")
                    return result
                result["cx"] = data.get("cx") or None
                try:
                    result["token_len"] = int(data.get("tokenLen") or 0)
                except (TypeError, ValueError):
                    result["token_len"] = 0
                result["trace"] = [str(t) for t in (data.get("trace") or [])]
                if result["cx"] or attempt == 1:
                    return result
                state = self._drive_state()
                if (state.get("path") or "").startswith("/agent"):
                    return result
                LOG.info("preflight: no cx on %s; navigating to /agent to load the module",
                         state.get("path"))
                self.loaded = False
                self.window.load_url(contracts.ORIGIN + "/agent")
                if not self.wait_ready(60):
                    result["trace"].append("agent_nav_timeout")
                    return result
        return result

    def _run_preflight_probe(self, action: str, timeout: float) -> Optional[dict]:
        if not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"action": action, "v3key": contracts.KEYS["v3"],
                               "moduleId": _CX_MODULE_ID})
            self.run_script(_PREFLIGHT_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "preflight":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- Battle pairwise vote --------------------------------------------------
    def pairwise_vote(self, args: dict, timeout: float = 45) -> dict:
        """Submit one Battle pairwise vote through the arena.ai page.

        The page's own Server Action (createPairwiseFeedback) is called
        with a reCAPTCHA v3 token the page mints for the
        "pairwise_feedback" action — the exact submission the website's
        vote bar performs.  ``args`` carries evaluationSessionId,
        messageAId, messageBId and value (model_a|model_b|tie|both_bad).

        Returns {"ok": bool, "result": {success, error, modelAId,
        modelBId, arenaId, revealed:[{id, name, org}]} | None,
        "error": str|None, "trace": [...]}.  The reCAPTCHA token never
        leaves the page.
        """
        result: dict = {"ok": False, "result": None, "error": None, "trace": []}
        if not self.window:
            result["error"] = "no_window"
            return result
        if self.login_open:
            result["error"] = "login_open"
            return result
        with self._drive_lock:
            data = self._run_vote_probe(args, timeout)
            if data is None:
                result["error"] = "probe_no_answer"
                return result
            result["ok"] = bool(data.get("ok"))
            result["result"] = data.get("result") if isinstance(data.get("result"), dict) else None
            result["error"] = data.get("error")
            result["trace"] = [str(t) for t in (data.get("trace") or [])]
            return result

    def _run_vote_probe(self, args: dict, timeout: float) -> Optional[dict]:
        if not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"args": args, "v3key": contracts.KEYS["v3"],
                               "moduleId": _VOTE_MODULE_ID, "actionId": _VOTE_ACTION_ID,
                               "recaptchaAction": _VOTE_RECAPTCHA_ACTION})
            self.run_script(_VOTE_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "vote":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- chat archive --------------------------------------------------------
    def archive_evaluation(self, session_id: str, timeout: float = 40) -> dict:
        """Archive one evaluation session on arena.ai, like its sidebar does.

        Calls the page's own archiveEvaluationSession Server Action with
        the session id.  Returns {"ok": bool, "error": str|None,
        "trace": [...]}.
        """
        result: dict = {"ok": False, "error": None, "trace": []}
        if not self.window:
            result["error"] = "no_window"
            return result
        if self.login_open:
            result["error"] = "login_open"
            return result
        with self._drive_lock:
            data = self._run_archive_probe(str(session_id or ""), timeout)
            if data is None:
                result["error"] = "probe_no_answer"
                return result
            result["ok"] = bool(data.get("ok"))
            result["error"] = data.get("error")
            result["trace"] = [str(t) for t in (data.get("trace") or [])]
            return result

    def _run_archive_probe(self, session_id: str, timeout: float) -> Optional[dict]:
        if not session_id or not self.wait_ready(30):
            return None
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        try:
            opts = json.dumps({"sessionId": session_id,
                               "moduleId": _VOTE_MODULE_ID,
                               "actionId": _ARCHIVE_ACTION_ID,
                               "actionName": _ARCHIVE_ACTION_NAME})
            self.run_script(_ARCHIVE_JS.replace("__ARENA_OPTS__", opts))
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    continue
                if kind == "archive":
                    return data
            return None
        finally:
            self._drive_queue = None

    # -- native Agent page drive -------------------------------------------
    def _on_drive_event(self, kind, payload):
        try:
            data = json.loads(payload) if isinstance(payload, str) else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if kind == "preflight":
            # The cx value itself is never logged, only its length and the trace.
            LOG.info("preflight: tokenLen=%s cxLen=%s trace=%s",
                     data.get("tokenLen"), data.get("cxLen"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:500])
        elif kind == "vote":
            slim = data.get("result") or {}
            LOG.info("vote: ok=%s success=%s modelA=%s modelB=%s err=%s trace=%s",
                     data.get("ok"), slim.get("success"), slim.get("modelAId"),
                     slim.get("modelBId"), data.get("error") or slim.get("error"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:300])
        elif kind == "archive":
            LOG.info("archive: ok=%s err=%s trace=%s",
                     data.get("ok"), data.get("error"),
                     "; ".join(str(t) for t in (data.get("trace") or []))[:300])
        elif kind == "create":
            sid = None
            try:
                parsed = json.loads(data.get("text") or "{}")
                if isinstance(parsed, dict):
                    sid = parsed.get("id") or parsed.get("sessionId")
            except ValueError:
                pass
            # The create JSON carries a public access token: log ids only.
            LOG.info("drive create fetch: HTTP %s session=%s", data.get("status"), sid)
        elif kind == "append":
            LOG.info("drive append fetch: HTTP %s", data.get("status"))
        elif kind == "composer":
            LOG.info("drive composer: %s", {k: data.get(k) for k in
                     ("ok", "set", "via", "cleared", "stage", "path")})
        q = self._drive_queue
        if q is not None:
            q.put((kind, data))

    def _drive_state(self) -> dict:
        raw = self.run_script(_DRIVE_STATE_JS)
        if isinstance(raw, str):
            try:
                state = json.loads(raw)
                if isinstance(state, dict):
                    return state
            except ValueError:
                pass
        return {}

    def drive_agent_send(self, text: str, chat_id: Optional[str] = None,
                         on_event=None, timeout: float = 240) -> dict:
        """Send one native Agent message through the real /agent UI in this window.

        The website's own client builds the whole submission (client-
        interaction blob, reCAPTCHA, tokens); the drive only types into its
        composer, clicks its send button and observes the create/append
        responses.  ``chat_id=None`` creates a new session on the agent
        landing; otherwise the existing chat page is driven.

        Returns {"ok": True, "payload": <create-chat JSON|None>} or
        {"ok": False, "error": str, "status": int|None, "stage": str}.
        ``on_event(kind, payload)`` receives ("stage", text) progress notes
        and ("challenge", "recaptcha"|"cloudflare") when the human is needed.
        """
        notify = on_event or (lambda kind, payload: None)
        if not self.window:
            return {"ok": False, "stage": "window", "status": None,
                    "error": "The arena.ai window is unavailable."}
        with self._drive_lock:
            return self._drive_send_locked(text, chat_id, notify, timeout)

    def _drive_send_locked(self, text, chat_id, notify, timeout) -> dict:
        q: "queue.Queue" = queue.Queue()
        self._drive_queue = q
        presented = False
        challenge_kind = None

        def present_for(kind):
            nonlocal presented, challenge_kind
            if kind == "recaptcha":
                self.challenge = "recaptcha"
            if not presented:
                presented = True
                self._present()
            if challenge_kind != kind:
                challenge_kind = kind
                notify("challenge", kind)

        def finish(result):
            if result.get("ok") and presented:
                if self.challenge == "recaptcha":
                    self.challenge = None
                if not self.login_open:
                    threading.Thread(target=self.hide, daemon=True).start()
            return result

        try:
            target = "/agent" + (f"/{chat_id}" if chat_id else "")
            state = self._drive_state()
            if (state.get("path") or "").rstrip("/") != target:
                notify("stage", "Arena Agent · opening the agent page")
                LOG.info("drive: navigating %s -> %s", state.get("path"), target)
                self.loaded = False
                self.window.load_url(contracts.ORIGIN + target)
                if not self.wait_ready(60):
                    if self.challenge == "cloudflare":
                        presented = True  # _after_load already showed the window
                        challenge_kind = "cloudflare"
                        notify("challenge", "cloudflare")
                        if not self.wait_ready(240):
                            return {"ok": False, "stage": "load", "status": None,
                                    "error": "The Cloudflare check in the Arena window is not finished."}
                    else:
                        return {"ok": False, "stage": "load", "status": None,
                                "error": "The arena.ai page did not load in the engine window."}
            self.run_script(_DRIVE_HOOK_JS)
            deadline = time.time() + 60
            state = {}
            found = False
            while time.time() < deadline:
                state = self._drive_state()
                if (state.get("textareas") or 0) + (state.get("editables") or 0) > 0:
                    found = True
                    break
                time.sleep(1)
            if not found:
                LOG.info("drive: composer not found, last state %s", state)
                return {"ok": False, "stage": "composer", "status": None,
                        "error": ("Could not find the input field on the Arena Agent page "
                                  f"({state.get('path') or '…'}). Take a look at the Arena window.")}
            if not state.get("hooked"):
                self.run_script(_DRIVE_HOOK_JS)
            notify("stage", "Arena Agent · sending the message")
            self.run_script(_DRIVE_SEND_JS.replace("__ARENA_TEXT__", json.dumps(text)))

            deadline = time.time() + timeout
            grace_deadline = None
            last_create = None
            composer = None
            sent_at = time.time()
            next_probe = sent_at + 3
            while time.time() < deadline:
                if grace_deadline and time.time() > grace_deadline:
                    break
                try:
                    kind, data = q.get(timeout=2)
                except queue.Empty:
                    kind, data = None, {}
                now = time.time()
                if kind == "composer":
                    composer = data
                    if not data.get("ok"):
                        return {"ok": False, "stage": "composer", "status": None,
                                "error": ("Could not find the input field on the Arena Agent page. "
                                          "Take a look at the Arena window.")}
                    if chat_id and data.get("cleared"):
                        return finish({"ok": True, "payload": None})
                elif kind == "create":
                    status = data.get("status")
                    if status == 200:
                        try:
                            payload = json.loads(data.get("text") or "{}")
                        except ValueError:
                            payload = {}
                        if isinstance(payload, dict) and (payload.get("id") or payload.get("sessionId")):
                            return finish({"ok": True, "payload": payload})
                        last_create = (status, "unexpected create reply")
                    else:
                        snippet = " ".join(str(data.get("text") or "").split())[:300]
                        last_create = (status, snippet)
                        if status in (401, 422, 429):
                            return {"ok": False, "stage": "create", "status": status,
                                    "error": f"arena.ai returned HTTP {status}: {snippet}"}
                        # The page itself may now show a human check and retry;
                        # give that flow a bounded grace period before failing.
                        if grace_deadline is None:
                            grace_deadline = now + 150
                elif kind == "append" and chat_id and data.get("status") == 200:
                    return finish({"ok": True, "payload": None})

                if now >= next_probe:
                    next_probe = now + 3
                    state = self._drive_state()
                    path = state.get("path") or ""
                    if not chat_id:
                        m = re.fullmatch(r"/agent/([0-9a-fA-F-]{36})", path)
                        if m:
                            LOG.info("drive: session from URL %s", m.group(1))
                            return finish({"ok": True, "payload": {"id": m.group(1)}})
                    if chat_id and composer and composer.get("ok") and state.get("composerEmpty"):
                        return finish({"ok": True, "payload": None})
                    if state.get("v2"):
                        present_for("recaptcha")
                    elif self.challenge == "cloudflare":
                        present_for("cloudflare")
                    elif not presented and now - sent_at > 25 and composer:
                        # Nothing confirmed for a while: show the page so the
                        # human can resolve whatever the site is showing.
                        present_for("recaptcha")
            if last_create:
                status, snippet = last_create
                return {"ok": False, "stage": "create", "status": status,
                        "error": f"arena.ai returned HTTP {status}: {snippet}"}
            return {"ok": False, "stage": "timeout", "status": None,
                    "error": ("Arena did not confirm the send in time. Take a look at the "
                              "Arena window — there may be a check or an error message.")}
        finally:
            self._drive_queue = None

    def get_me(self, timeout: float = 25) -> Tuple[Optional[dict], Optional[str]]:
        if not self.wait_ready(timeout):
            return None, (f"arena.ai is waiting for a {self.challenge} check" if self.challenge
                          else "arena.ai window is still loading")
        r = self.request(contracts.PATHS["me"], method="GET", timeout=timeout)
        body = r.text
        if r.status_code == 0:
            return None, f"Network error: {r.error}"
        if r.status_code == 200:
            try:
                data = json.loads(body)
            except ValueError:
                return None, "arena.ai sent an unexpected /api/me reply"
            user = data.get("user") if isinstance(data, dict) and "user" in data else data
            if isinstance(user, dict) and (user.get("email") or user.get("id")):
                return user, None
            return None, "Not signed in"
        if r.status_code == 401:
            return None, "Not signed in"
        return None, f"HTTP {r.status_code}"

    def _deliver(self, rid, seq, kind, data):
        with self._lock:
            st = self._streams.get(rid)
            if not st:
                return
            st["held"][int(seq)] = (kind, data)
            while st["next"] in st["held"]:
                st["q"].put(st["held"].pop(st["next"]))
                st["next"] += 1

    def _close(self, rid):
        with self._lock:
            self._streams.pop(rid, None)

    # -- human verification -----------------------------------------------
    def request_human_check(self):
        """Show arena.ai with its reCAPTCHA v2 checkbox; the token lands in self.v2_token."""
        if not self.window:
            return
        self.v2_token = None
        self.challenge = "recaptcha"
        LOG.info("arena.ai asked for a human check")
        self._present()
        self.window.evaluate_js(_CHECK_JS % ",".join(json.dumps(a) for a in (contracts.KEYS["v2"], contracts.RECAPTCHA_ACTION)))

    def cancel_check(self):
        self.challenge = None
        try:
            self.window.evaluate_js("(function(){var b=document.getElementById('arena-desktop-check');if(b)b.remove();return 1})()")
        except Exception:
            pass
        if not self.login_open:
            threading.Thread(target=self.hide, daemon=True).start()

    def _on_v2(self, token):
        self.v2_token = str(token or "") or None
        LOG.info("human check finished in the page, token %s", "received" if self.v2_token else "empty")
        self.challenge = None
        if not self.login_open:
            threading.Thread(target=self.hide, daemon=True).start()

    def take_v2_token(self) -> Optional[str]:
        token, self.v2_token = self.v2_token, None
        return token


ENGINE = BrowserEngine()
