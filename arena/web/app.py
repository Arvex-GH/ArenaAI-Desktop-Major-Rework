"""
Flask web backend for Arena.ai desktop client.
Serves the web UI and proxies chat requests to arena.ai.
"""

import copy
import difflib
import hashlib
import json
import logging
import mimetypes
import os
import queue
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from flask import Flask, Response, jsonify, request, send_file, send_from_directory, stream_with_context

# Add parent to path for arena imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from arena.config import (
    BASE_URL,
    CHATS_DIR,
    COOKIE_FILE,
    ensure_config_dir,
    load_config,
    save_config,
)
from arena.api import ArenaClient, extract_bearer_token, fetch_cookies_via_cdp
from arena.models import (sync_models, get_models_for_modality, get_router_model,
                          get_auto_agent_model, ORG_COLOR_MAP)
from arena.auth import detect_installed_browsers, parse_cookie_input

import base64
import ctypes
import ctypes.wintypes
import re
import shutil
import sqlite3
import tempfile
import uuid
import codecs
import functools
import http.server
import io
import mimetypes
import urllib.parse

import requests
from datetime import datetime, timezone


def _dpapi_decrypt(data: bytes):
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD),
                     ("pbData", ctypes.POINTER(ctypes.c_char))]
    p = ctypes.create_string_buffer(data, len(data))
    bi = DATA_BLOB(len(data), p)
    bo = DATA_BLOB()
    if ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(bi), None, None, None, None, 0, ctypes.byref(bo)
    ):
        result = ctypes.string_at(bo.pbData, bo.cbData)
        ctypes.windll.kernel32.LocalFree(bo.pbData)
        return result
    return None


def _decrypt_cookie_val(enc: bytes, key: bytes):
    if len(enc) < 16:
        return None
    if enc[:3] in (b'v10', b'v11', b'v20'):
        nonce = enc[3:15]
        ct = enc[15:]
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            return AESGCM(key).decrypt(nonce, ct, None).decode('utf-8', errors='ignore')
        except Exception:
            return None
    d = _dpapi_decrypt(enc)
    return d.decode('utf-8', errors='ignore') if d else None


def _extract_browser_cookies():
    dirs = {
        'chrome': os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\User Data'),
        'edge': os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Edge\User Data'),
        'brave': os.path.expandvars(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data'),
    }
    for name, ud in dirs.items():
        if not os.path.isdir(ud):
            continue
        r = _try_chromium_cookies(ud)
        if r:
            return r
    return None


def _try_chromium_cookies(ud):
    ls_file = os.path.join(ud, 'Local State')
    if not os.path.isfile(ls_file):
        return None
    try:
        with open(ls_file, 'r', encoding='utf-8') as f:
            ls = json.load(f)
        ek = base64.b64decode(ls['os_crypt']['encrypted_key'])[5:]
        key = _dpapi_decrypt(ek)
        if not key:
            return None
    except Exception:
        return None

    for profile in ['Default'] + [f'Profile {i}' for i in range(1, 6)]:
        cdb = os.path.join(ud, profile, 'Cookies')
        if not os.path.isfile(cdb):
            continue
        tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False).name
        try:
            shutil.copy2(cdb, tmp)
            conn = sqlite3.connect(tmp)
            rows = conn.execute(
                "SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%arena.ai%'"
            ).fetchall()
            conn.close()
        except Exception:
            continue
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        cookies = {}
        for cname, cval in rows:
            if cval and len(cval) > 3:
                v = _decrypt_cookie_val(cval, key)
                if v:
                    cookies[cname] = v
        if 'arena-auth-prod-v1.0' in cookies:
            return '; '.join(f'{k}={v}' for k, v in cookies.items())
    return None

def _find_preview_url(val):
    if isinstance(val, dict):
        for k in ("sandboxUrl", "previewUrl", "deploymentUrl", "deployUrl", "webUrl"):
            if k in val and isinstance(val[k], str) and val[k].startswith("http"):
                return val[k]
        for v in val.values():
            r = _find_preview_url(v)
            if r:
                return r
    elif isinstance(val, list):
        for item in val:
            r = _find_preview_url(item)
            if r:
                return r
    return None


from arena import agent, components, connectors, contracts, history_sync, native_agent, permissions, workspace
from arena.gateway import GatewayManager
from arena.api import generate_uuidv7
from arena.auth import get_clipboard_text
from arena.engine import ENGINE

_overrides = contracts.load_overrides()
MODE_API_MAP = dict(contracts.UI_MODES)
MODALITY_API_MAP = dict(contracts.UI_MODALITIES)
VALID_API_MODES = contracts.API_MODES
VALID_API_MODALITIES = ("chat", "webdev", "search", "image", "video")
BROKER = permissions.PermissionBroker()
CONNECTORS = connectors.ConnectorManager(lambda: _panel_workspace_root())
_stop = threading.Event()
_generation_lock = threading.Lock()
_active_generations = 0
_active_chat_ids: set = set()
_chat_stops: dict = {}
_chat_setup_lock = threading.Lock()
_last_webdev: dict = {}


def _generation_started(chat_id: str = None) -> None:
    global _active_generations
    with _generation_lock:
        _active_generations += 1
        if chat_id:
            _active_chat_ids.add(str(chat_id))


def _generation_finished(chat_id: str = None) -> None:
    global _active_generations
    with _generation_lock:
        _active_generations = max(0, _active_generations - 1)
        if chat_id:
            _active_chat_ids.discard(str(chat_id))
    if chat_id:
        _notify("Arena Desktop", "A chat finished generating.", "done")


# Desktop shell (tray) installs a toast callback here: fn(title, text, kind)
# with kind "done" (a generation finished) or "question" (the model waits
# for an answer).  The web headless server leaves it None.
NOTIFY_HOOK = None


def _notify(title: str, text: str, kind: str = "done") -> None:
    hook = NOTIFY_HOOK
    if hook is None:
        return
    try:
        hook(title, text, kind)
    except Exception:
        LOG.exception("notify hook failed")


def _generation_active() -> bool:
    with _generation_lock:
        return _active_generations > 0


def _chat_generating(chat_id: str) -> bool:
    with _generation_lock:
        return str(chat_id) in _active_chat_ids


def _generating_chat_ids() -> list:
    with _generation_lock:
        return sorted(_active_chat_ids)


def _chat_stop_event(chat_id: str) -> threading.Event:
    with _generation_lock:
        event = _chat_stops.get(str(chat_id))
        if event is None:
            event = threading.Event()
            _chat_stops[str(chat_id)] = event
        return event


def _stopped() -> bool:
    """Stop check for the chat being generated on this request thread.

    The per-chat event lives in thread-local storage (set by the SSE
    generator), so parallel chats stop independently; the legacy global
    event still stops everything."""
    if getattr(_stop, 'is_set')():
        return True
    event = getattr(_tl, "stop_event", None)
    return bool(event is not None and event.is_set())

if getattr(sys, 'frozen', False):
    _base = Path(sys._MEIPASS)
else:
    _base = Path(__file__).parent
STATIC_DIR = _base / "arena" / "web" / "static" if getattr(sys, 'frozen', False) else Path(__file__).parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

APP_VERSION = "5.5.1"


@app.after_request
def _no_store_ui(resp):
    # The desktop WebView2 profile persists across EXE updates; never let it
    # serve a stale index/app.js from a previous build on the same localhost URL.
    if request.path == "/" or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# Global client instance
_client: Optional[ArenaClient] = None
_conversation_history: list = []
_ui_mode = 'battle'
_ui_modality = 'auto'
_current_chat_id: Optional[str] = None

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def _validate_chat_id(chat_id: str) -> bool:
    return bool(_UUID_RE.match(chat_id))


def _save_chat(chat_id: str, title: str, messages: list,
               mode: str = 'battle', modality: str = 'auto',
               model_a: str = None, model_b: str = None, eval_id: str = None,
               source: str = None, tools_guide: bool = None,
               agent_backend: str = None, native: dict = None,
               mcp_guide_hash: str = None, memory_hash: str = None,
               pending_digest: str = None, parent_id: str = None,
               delegate_guide: bool = None):
    if not _validate_chat_id(chat_id):
        return
    chat_dir = CHATS_DIR / chat_id
    chat_dir.mkdir(parents=True, exist_ok=True)
    chat_file = chat_dir / "chat.json"
    now = datetime.now(timezone.utc).isoformat()
    existing = {}
    if chat_file.exists():
        try:
            existing = json.loads(chat_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    data = {
        "id": chat_id,
        "title": title,
        "account_id": existing.get("account_id") or _current_account_id(),
        "created_at": existing.get("created_at", now),
        "updated_at": now,
        "mode": mode,
        "modality": modality,
        "model_a": model_a,
        "model_b": model_b,
        "eval_id": eval_id,
        "source": source or existing.get("source") or "local",
        "tools_guide": bool(tools_guide) if tools_guide is not None else bool(existing.get("tools_guide")),
        "mcp_guide_hash": mcp_guide_hash if mcp_guide_hash is not None else existing.get("mcp_guide_hash"),
        "memory_hash": memory_hash if memory_hash is not None else existing.get("memory_hash"),
        "pending_digest": pending_digest if pending_digest is not None else existing.get("pending_digest"),
        "parent_id": (parent_id or None) if parent_id is not None else existing.get("parent_id"),
        "delegate_guide": bool(delegate_guide) if delegate_guide is not None else bool(existing.get("delegate_guide")),
        "agent_backend": agent_backend or existing.get("agent_backend"),
        "native": native if native is not None else existing.get("native"),
        "messages": messages,
    }
    # Atomic replace: readers (chat list, parallel chats) never see a
    # half-written file.
    tmp_file = chat_dir / "chat.json.tmp"
    tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp_file, chat_file)


def _load_chat(chat_id: str) -> Optional[dict]:
    if not _validate_chat_id(chat_id):
        return None
    chat_file = CHATS_DIR / chat_id / "chat.json"
    if not chat_file.exists():
        return None
    try:
        return json.loads(chat_file.read_text(encoding="utf-8"))
    except Exception:
        return None


def _chat_visible(data: dict, current_id, first_id) -> bool:
    """A local chat belongs to the account that created it.  Chats saved
    before multi-account support (no account_id) belong to the first
    account ever signed in, matching how they were created."""
    owner = data.get("account_id") or first_id
    if owner is None:
        return True
    return current_id is not None and owner == current_id


def _list_chats() -> list:
    if not CHATS_DIR.exists():
        return []
    store = _accounts_load()
    current_id, first_id = store.get("current"), _first_account_id(store)
    chats = []
    for d in CHATS_DIR.iterdir():
        if not d.is_dir():
            continue
        f = d / "chat.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not _chat_visible(data, current_id, first_id):
                continue
            chats.append({
                "id": data.get("id", d.name),
                "title": data.get("title", "Untitled"),
                "updated_at": data.get("updated_at", ""),
                "mode": data.get("mode"),
                "modality": data.get("modality"),
                "eval_id": data.get("eval_id"),
                "parent_id": data.get("parent_id"),
                "source": data.get("source") or "local",
                "message_count": len(data.get("messages", [])),
            })
        except Exception:
            pass
    chats.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
    # Sub-agent child chats nest under their parent instead of flooding the
    # list; a child whose parent is gone (or on another account) stands alone.
    by_id = {c["id"]: c for c in chats}
    for c in chats:
        c["children"] = []
    top = []
    for c in chats:
        pid = c.get("parent_id")
        if pid and pid != c["id"] and pid in by_id:
            by_id[pid]["children"].append(
                {"id": c["id"], "title": c["title"], "updated_at": c["updated_at"],
                 "mode": c.get("mode")})
        else:
            top.append(c)
    return top


def _orphan_children(parent_id: str) -> None:
    """Sub-agent chats outlive a deleted parent as ordinary chats."""
    if not CHATS_DIR.exists():
        return
    for d in CHATS_DIR.iterdir():
        f = d / "chat.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("parent_id") == parent_id:
            _save_chat(data.get("id", d.name), title=data.get("title") or "Conversation",
                       messages=data.get("messages") or [], mode=data.get("mode") or "direct",
                       modality=data.get("modality") or "text", model_a=data.get("model_a"),
                       model_b=data.get("model_b"), eval_id=data.get("eval_id"),
                       tools_guide=bool(data.get("tools_guide")),
                       agent_backend=data.get("agent_backend"), parent_id="")


def _find_chat_by_eval(eval_id: str) -> Optional[dict]:
    if not eval_id:
        return None
    direct = _load_chat(eval_id)
    if direct and direct.get("eval_id") == eval_id:
        return direct
    if not CHATS_DIR.exists():
        return None
    for directory in CHATS_DIR.iterdir():
        if not directory.is_dir():
            continue
        data = _load_chat(directory.name)
        if data and data.get("eval_id") == eval_id:
            return data
    return None


def _delete_chat(chat_id: str) -> bool:
    if not _validate_chat_id(chat_id):
        return False
    chat_dir = CHATS_DIR / chat_id
    if chat_dir.exists() and chat_dir.is_dir():
        shutil.rmtree(chat_dir)
        return True
    return False


def get_client() -> ArenaClient:
    global _client
    if _client is None:
        cookie_str = None
        if COOKIE_FILE.exists():
            cookie_str = COOKIE_FILE.read_text(encoding="utf-8").strip()
        _client = ArenaClient(cookie_str=cookie_str)
    return _client


def _catalog_fetch_json():
    """Fetch the model catalog through the signed-in session.

    The anonymous endpoint answers 403, and sync used to swallow that and
    serve the disk cache forever - new models (e.g. Kimi K3.1 Pro) never
    reached the picker.  _arena_get_json uses the browser engine when
    present and the account session otherwise, exactly like the website.
    """
    data, error, status = _arena_get_json(contracts.PATHS["catalog"], timeout=20)
    if error or data is None or (status and status >= 400):
        raise RuntimeError(f"model catalog fetch failed: {error or status}")
    return data


def _catalog_models(force: bool = False) -> list:
    try:
        try:
            models = sync_models(force=force, fetch_json=_catalog_fetch_json)
        except TypeError:
            models = sync_models(force=force)
    except Exception:
        return []
    return models if isinstance(models, list) else []


def _selection_error(ui_mode: str, ui_modality: str) -> Optional[str]:
    if ui_mode not in MODE_API_MAP:
        return f"Invalid mode: {ui_mode}"
    if ui_modality not in MODALITY_API_MAP:
        return f"Invalid modality: {ui_modality}"
    try:
        contracts.validate_ui_selection(ui_mode, ui_modality)
    except ValueError as e:
        return str(e)
    return None


def _allowed_model_ids(ui_mode: str, ui_modality: str) -> set:
    api_modality = contracts.api_modality(ui_modality)
    return {str(m.get("model_id", m.get("id", "")))
            for m in get_models_for_modality(_catalog_models(), api_modality, mode=ui_mode)
            if m.get("model_id", m.get("id"))}


def _auto_agent_model(preferred_id: str = None) -> Optional[dict]:
    return get_auto_agent_model(_catalog_models(), preferred_id=preferred_id)


# ---------- Static serving ----------

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


# ---------- API routes ----------

_me_cache = {"t": 0.0, "user": None, "err": None}


def _engine_on() -> bool:
    return ENGINE.window is not None


def _perm_mode() -> str:
    # Automations run unattended on their own thread and may pin a mode.
    override = getattr(_tl, "perm_override", None)
    if override:
        return permissions.normalize(override)
    return permissions.normalize(load_config().get("permission_mode"))

# ---------- Persistent memory + conversation compaction ----------

MEMORY_FILE_NAME = "MEMORY.md"
GLOBAL_MEMORY_FILE = COOKIE_FILE.parent / "memory.md"
CONTEXT_WARN_CHARS = 80_000


def _memory_enabled() -> bool:
    return load_config().get("memory_enabled") is not False


def _read_memory_file(path: Path, cap: int) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:cap]


def _memory_block(folder) -> tuple:
    """Combined general + project memory, formatted for the model.

    Returns (block, hash); ("", None) when there is nothing to send.  The
    block travels once per Arena session and again only when it changed
    (same discipline as the MCP connector guide).
    """
    parts = []
    general = _read_memory_file(GLOBAL_MEMORY_FILE, 3_000)
    if general:
        parts.append("General memory (all projects):\n" + general)
    if folder:
        agents = _read_memory_file(Path(folder) / "AGENTS.md", 6_000)
        if agents:
            parts.append(f"Project instructions (AGENTS.md in {folder}):\n" + agents)
        project = _read_memory_file(Path(folder) / MEMORY_FILE_NAME, 6_000)
        if project:
            parts.append(f"Project memory ({MEMORY_FILE_NAME} in {folder}):\n" + project)
    if not parts:
        return "", None
    body = "\n\n".join(parts)
    block = ("[Arena Desktop memory \u2014 persistent notes shared across chats. "
             "Treat them as established facts, decisions and preferences.]\n"
             + body + "\n[End of memory]\n\n")
    return block, hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def _history_chars(messages) -> int:
    """Approximate Arena-side context size of a chat.

    Only messages after the last compaction marker count (plus the digest
    that replaced everything before it), so the meter resets after Compact
    exactly like the real server-side context does.
    """
    total = 0
    start = 0
    for i, m in enumerate(messages or []):
        if isinstance(m, dict) and m.get("compact"):
            start = i + 1
            total = int(m.get("digest_chars") or 0)
    for m in (messages or [])[start:]:
        if isinstance(m, dict):
            total += len(m.get("text") or "") + len(m.get("text_b") or "")
    return total


def _clip(text: str, cap: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= cap else text[:cap].rstrip() + " \u2026"


def _build_digest(chat: dict) -> str:
    """Deterministic summary of a chat, used to seed a fresh Arena session.

    Built locally (never by asking the model \u2014 by the time compaction is
    needed the model may already be failing in the old session): original
    task, files touched, and the most recent exchanges.
    """
    messages = [m for m in (chat.get("messages") or []) if isinstance(m, dict)]
    lines = [f"Summary of the earlier conversation \u201c{chat.get('title') or 'Conversation'}\u201d:"]
    users = [m for m in messages if m.get("role") == "user" and (m.get("text") or "").strip()]
    if users:
        lines += ["", "Original task:", _clip(users[0].get("text"), 700)]
    touched = []
    for m in messages:
        for step in (m.get("tools") or []):
            if not isinstance(step, dict):
                continue
            if step.get("tool") in ("write_file", "edit_file") and step.get("summary"):
                if step["summary"] not in touched:
                    touched.append(step["summary"])
    if touched:
        lines += ["", "File changes made along the way:"] + [f"- {t}" for t in touched[-15:]]
    picked, budget = [], 3_800
    for m in reversed(messages):
        role = m.get("role")
        if role == "user":
            text, label = _clip(m.get("text"), 350), "User"
        elif role == "assistant":
            text = _clip(agent.strip_tool_calls(m.get("text") or ""), 900)
            label = "Assistant"
        else:
            continue
        if not text:
            continue
        entry = f"{label}: {text}"
        if len(entry) > budget and picked:
            break
        picked.append(entry)
        budget -= len(entry)
        if budget <= 0:
            break
    if picked:
        lines += ["", "Most recent exchanges (oldest first):", ""]
        lines += list(reversed(picked))
    return "\n".join(lines)[:6_000]


def _digest_block(digest: str) -> str:
    return ("[Arena Desktop \u2014 continued conversation]\n"
            "This chat continues an earlier conversation whose context was compacted "
            "to stay within Arena's limits. What came before, in brief:\n\n"
            + digest +
            "\n\nThe files in the workspace are the source of truth \u2014 re-read anything "
            "you need instead of guessing. Now answer the user's message below.\n\n"
            "User message:\n")


def _append_digest_to_memory(chat: dict, digest: str):
    """Save the compaction digest into the project memory file, so the work
    is available to future chats in this folder too, not only this one."""
    folder = _chosen_ws()
    if not folder or not _memory_enabled():
        return None
    path = Path(folder) / MEMORY_FILE_NAME
    stamp = datetime.now().strftime("%Y-%m-%d")
    entry = (f"\n## Session digest \u2014 {chat.get('title') or 'Conversation'} \u2014 {stamp}\n\n"
             + digest + "\n")
    try:
        if path.exists():
            current = path.read_text(encoding="utf-8")
        else:
            current = ("# Project memory\n\nPersistent notes shared across Arena Desktop "
                       "chats in this folder, kept by the user and the models. "
                       "Session digests are appended automatically on Compact.\n")
        path.write_text(current.rstrip() + "\n" + entry, encoding="utf-8")
    except OSError:
        return None
    return str(path)



def _get_me(client: ArenaClient):
    """Ask arena.ai who is signed in. In the desktop app only the engine uses the session:
    Supabase revokes a refresh token that two clients keep rotating."""
    if _engine_on():
        user, err = ENGINE.get_me()
        ENGINE.sync_to(client)
        return user, err
    return client.get_me()


# ---------- Multi-account store ----------

ACCOUNTS_FILE = COOKIE_FILE.parent / "accounts.json"
_accounts_lock = threading.Lock()


def _accounts_load() -> dict:
    try:
        data = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("accounts"), list):
            return {"current": data.get("current"), "accounts": data["accounts"]}
    except Exception:
        pass
    return {"current": None, "accounts": []}


def _accounts_save(store: dict):
    try:
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        LOG.exception("accounts store save failed")


def _account_public(record: dict, current_id) -> dict:
    return {
        "id": record.get("id"),
        "email": record.get("email") or "",
        "name": record.get("name") or record.get("email") or "Arena account",
        "avatar_url": record.get("avatar_url"),
        "current": record.get("id") == current_id,
        "expired": bool(record.get("expired")),
    }


def _accounts_public(store: dict = None) -> list:
    store = store if store is not None else _accounts_load()
    current = store.get("current")
    return [_account_public(a, current) for a in store.get("accounts") or []]


def _current_account_id():
    return _accounts_load().get("current")


def _first_account_id(store: dict):
    accounts = store.get("accounts") or []
    return accounts[0].get("id") if accounts else None


def _register_account(user: dict, cookie: str) -> Optional[str]:
    """Remember (or refresh) the account that just signed in and make it
    current.  Identity comes from a verified /me response; the cookie is
    stored only on disk and never sent to the frontend."""
    if not isinstance(user, dict) or not cookie:
        return None
    email = (user.get("email") or "").strip()
    if not email:
        return None
    account_id = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:16]
    meta = user.get("user_metadata") or {}
    name = meta.get("full_name") or meta.get("name") or user.get("name") or email
    avatar = meta.get("avatar_url") or user.get("avatar_url") or user.get("avatarUrl")
    with _accounts_lock:
        store = _accounts_load()
        record = next((a for a in store["accounts"] if a.get("id") == account_id), None)
        now = datetime.now(timezone.utc).isoformat()
        if record is None:
            record = {"id": account_id, "added_at": now}
            store["accounts"].append(record)
        # A later /me without profile metadata must not erase a known name.
        if name == email and record.get("name"):
            name = record["name"]
        if not avatar:
            avatar = record.get("avatar_url")
        record.update({
            "email": email, "name": str(name), "avatar_url": avatar,
            "cookie": cookie, "updated_at": now, "expired": False,
        })
        store["current"] = account_id
        _accounts_save(store)
    return account_id


def _sync_current_account_cookie(cookie: str):
    """Persist a rotated session cookie for the current account."""
    if not cookie:
        return
    with _accounts_lock:
        store = _accounts_load()
        current = store.get("current")
        if not current:
            return
        for record in store["accounts"]:
            if record.get("id") == current and record.get("cookie") != cookie:
                record["cookie"] = cookie
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                record["expired"] = False
                _accounts_save(store)
            break


def _mark_account_expired(account_id: str, expired: bool = True):
    with _accounts_lock:
        store = _accounts_load()
        for record in store["accounts"]:
            if record.get("id") == account_id:
                record["expired"] = expired
                _accounts_save(store)
                return


def _remove_account(account_id: str):
    with _accounts_lock:
        store = _accounts_load()
        store["accounts"] = [a for a in store["accounts"] if a.get("id") != account_id]
        if store.get("current") == account_id:
            store["current"] = None
        _accounts_save(store)


def _user_identity(user, fallback: Optional[dict] = None) -> dict:
    """Best email/name/avatar for the signed-in user, from any shape Arena uses.

    /api/me and the Supabase session disagree on field names over time
    (avatarUrl vs user_metadata.avatar_url vs picture, fullName vs name), so
    look through the usual nests instead of trusting one spelling.  This is
    what makes the real profile picture reach the sidebar.
    """
    out = {"email": None, "full_name": None, "avatar_url": None}
    sources = []
    if isinstance(user, dict):
        sources.append(user)
        for key in ("user_metadata", "userMetadata", "profile", "metadata", "app_metadata"):
            if isinstance(user.get(key), dict):
                sources.append(user[key])
        for ident in user.get("identities") or []:
            if isinstance(ident, dict) and isinstance(ident.get("identity_data"), dict):
                sources.append(ident["identity_data"])
    if isinstance(fallback, dict):
        sources.append(fallback)

    def pick(*names):
        for src in sources:
            for name in names:
                value = src.get(name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    out["email"] = pick("email")
    out["full_name"] = pick("full_name", "fullName", "name", "displayName", "display_name")
    avatar = pick("avatar_url", "avatarUrl", "picture", "imageUrl", "image_url",
                  "profileImageUrl", "profile_image_url", "photoURL", "photo_url")
    if avatar:
        if avatar.startswith("//"):
            avatar = "https:" + avatar
        elif avatar.startswith("/"):
            avatar = contracts.ORIGIN + avatar
        if avatar.startswith(("https://", "http://", "data:image/")):
            out["avatar_url"] = avatar
    return out


def _check_session(client: ArenaClient, force: bool = False):
    if not force and time.time() - _me_cache["t"] < 120:
        return _me_cache["user"], _me_cache["err"]
    user, err = _get_me(client)
    _me_cache.update(t=time.time(), user=user, err=err)
    return user, err


def _signed_in_somewhere(client: ArenaClient) -> bool:
    # Cookie enumeration can differ between WebView2 versions; a verified /me
    # result is also proof that the active embedded browser has a session.
    verified_engine = _engine_on() and ENGINE.ready() and bool(_me_cache.get("user"))
    return bool(client.cookie_str) or verified_engine or (_engine_on() and ENGINE.ready() and ENGINE.has_session())


@app.route("/api/status")
def api_status():
    client = get_client()
    out = {
        "authenticated": False, "email": None, "full_name": None, "avatar_url": None,
        "session_error": None, "offline": False, "loading": False,
        "engine_loading": False, "generating": _generation_active(),
        "generating_chat_ids": _generating_chat_ids(),
        "accounts": _accounts_public(), "current_account_id": _current_account_id(),
        "mode": _ui_mode, "modality": _ui_modality, "eval_id": client.eval_id,
        "desktop": _engine_on(), "engine": ENGINE.state(), "permission_mode": _perm_mode(),
        "app_version": APP_VERSION, "workspace_chosen": _chosen_ws() is not None,
        "workspace": str(_workspace_dir()), "projects_dir": str(workspace.PROJECTS_DIR),
        "pending_permissions": len(BROKER.pending()),
        "gateway": _GATEWAY.summary() if _GATEWAY is not None else {"active": False, "state": "off", "servers": 0},
    }
    cookie_user = (parse_cookie_input(client.cookie_str)[2] if client.cookie_str else None) or {}
    ident = _user_identity(cookie_user)
    if _engine_on() and not ENGINE.ready():
        # arena.ai is still loading or waiting on a check; answer now, the UI polls again
        out.update(ident, loading=True, engine_loading=True, authenticated=bool(client.cookie_str))
        return jsonify(out)
    has_cookie_evidence = _signed_in_somewhere(client)
    if not has_cookie_evidence and not _engine_on():
        return jsonify(out)
    # In the desktop app, ask Arena /me even if neither the saved client nor
    # WebView2 cookie enumeration exposes an auth cookie. This adopts a
    # session already present in the persistent Arena window after restart.
    # _check_session caches the answer, so this is not a request per poll.
    user, err = _check_session(client, force=request.args.get("refresh") == "1")
    if user:
        merged = _user_identity(user, ident)
        out.update(merged, authenticated=True)
        # Adopt the verified session into the account store (first run after
        # the multi-account update, or a restore from cookies.txt) and keep
        # the stored cookie fresh as Arena rotates it.
        store = _accounts_load()
        current = next((a for a in store["accounts"] if a.get("id") == store.get("current")), None)
        email = (user.get("email") or "").lower()
        if current is None or (current.get("email") or "").lower() != email:
            _register_account(user, client.cookie_str)
        else:
            _sync_current_account_cookie(client.cookie_str)
        out["accounts"] = _accounts_public()
        out["current_account_id"] = _current_account_id()
    elif err and err.startswith("Network error"):
        out.update(ident, authenticated=has_cookie_evidence, offline=True)
    elif has_cookie_evidence or err != "Not signed in":
        out["session_error"] = err
    return jsonify(out)


def _activate_cookie(cookie_str: str, method: str):
    """Adopt a cookie only after arena.ai confirms the session; returns (response, ok)."""
    global _client
    candidate = ArenaClient(cookie_str=cookie_str)
    if _engine_on():
        try:
            imported = ENGINE.import_cookies(cookie_str)
        except Exception:
            LOG.warning("Cookie import failed in the Arena window")  # never log credential material
            imported = False
        if not imported:
            return jsonify({"ok": False, "error": "Could not import cookies. Make sure the Arena window has loaded and try again."}), False
        if not ENGINE.wait_ready(45):
            hint = f" It is waiting for a {ENGINE.challenge} check: press Show arena.ai." if ENGINE.challenge else ""
            return jsonify({"ok": False, "error": "arena.ai did not finish loading." + hint + " Try again in a moment."}), False
        user, err = ENGINE.get_me()
        if user:
            ENGINE.sync_to(candidate)
    else:
        user, err = candidate.get_me()
    if not user:
        return jsonify({"ok": False, "error": f"arena.ai rejected this session ({err}). Sign in on arena.ai again, then copy fresh cookies."}), False
    ensure_config_dir()
    COOKIE_FILE.write_text(candidate.cookie_str, encoding="utf-8")
    _client = candidate
    _me_cache.update(t=time.time(), user=user, err=None)
    _register_account(user, candidate.cookie_str)
    _, _, cookie_user = parse_cookie_input(candidate.cookie_str)
    ident = _user_identity(user, cookie_user or {})
    return jsonify({"ok": True, "method": method, **ident}), True


@app.route("/api/models")
def api_models():
    # Mode is part of availability, not a cosmetic UI filter: Battle/Video
    # and Agent have no picker. Agent's internal model is assigned without
    # exposing it, while Max remains a Direct/Text-only manual entry.
    ui_mode = request.args.get("mode", _ui_mode)
    ui_modality = request.args.get("modality", _ui_modality)
    error = _selection_error(ui_mode, ui_modality)
    if error:
        return jsonify({"ok": False, "error": error, "code": "unsupported_selection"}), 400
    api_mod = contracts.api_modality(ui_modality)
    filtered = get_models_for_modality(_catalog_models(force=request.args.get("force") == "1"),
                                        api_mod, mode=ui_mode)
    result = []
    for m in filtered:
        raw_org = m.get("org", m.get("organization", "unknown"))
        if isinstance(raw_org, dict):
            org_name = raw_org.get("name", "unknown").lower()
            org_display = raw_org.get("name", org_name)
        else:
            org_name = str(raw_org).lower()
            org_display = str(raw_org)
        result.append({
            "id": m.get("model_id", m.get("id", "")),
            "name": m.get("name", ""),
            "org": org_name,
            "org_display": org_display,
            "provider": m.get("provider", ""),
            "color": ORG_COLOR_MAP.get(org_name, "#C7C2BC"),
            "rank": m.get("rank", m.get("arena_rank")),
            "capabilities": m.get("capabilities", []),
            "arena_score": m.get("arena_score"),
            "user_selectable": bool(m.get("user_selectable")),
            "is_router": bool(m.get("is_router")),
            "icon_url": m.get("icon_url"),
            "fixed": False,
        })
    return jsonify(result)


_LOGIN_STEPS = ("In your own signed-in arena.ai tab: open DevTools Console, run copy(document.cookie), "
                "then paste the result into the Cookies field here. Or copy all arena-auth-prod-v1 rows "
                "from Application > Cookies.")


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    method = data.get("method") or ("auto" if data.get("auto") else "manual")
    if method == "window":
        if not _engine_on():
            return jsonify({"ok": False, "error": "The sign-in window is part of Arena Desktop (Arena-Desktop.exe). " + _LOGIN_STEPS}), 400
        _me_cache.update(t=0.0, user=None, err=None)
        ENGINE.open_login()
        return jsonify({"ok": True, "pending": True})
    if method == "auto":
        last_error = None
        for name, finder in (("cdp", fetch_cookies_via_cdp), ("browser_db", _extract_browser_cookies)):
            try:
                found = finder()
            except Exception as e:
                found, last_error = None, f"{name}: {e}"
            if found and contracts.AUTH_COOKIE_PREFIX in found:
                resp, ok = _activate_cookie(found, name)
                if ok:
                    return resp
                last_error = resp.get_json().get("error")
        return jsonify({"ok": False, "snippet": "copy(document.cookie)", "error": (
            last_error or "No signed-in arena.ai session was found in Chrome, Edge or Brave. "
            "Recent Chrome versions lock their cookies so other apps cannot read them.") + " " + _LOGIN_STEPS}), 400
    raw = get_clipboard_text() if method == "clipboard" else data.get("cookie")
    if raw is not None and not isinstance(raw, str):
        return jsonify({"ok": False, "error": "Cookie text must be a string."}), 400
    raw = (raw or "").strip()
    if len(raw) > 65536:
        return jsonify({"ok": False, "error": "Cookie text is too large."}), 400
    if not raw:
        where = "The clipboard is empty." if method == "clipboard" else "No cookie text was given."
        return jsonify({"ok": False, "error": f"{where} {_LOGIN_STEPS}"}), 400
    cookie_str, cookie_dict, _ = parse_cookie_input(raw)
    if not any(k.startswith(contracts.AUTH_COOKIE_PREFIX) for k in cookie_dict):
        return jsonify({"ok": False, "error": "That text has no arena-auth-prod-v1 cookie, so it is not an arena.ai "
                        "session. " + _LOGIN_STEPS}), 400
    # Do not retain unrelated website cookies pasted alongside the auth chunks.
    auth_cookies = "; ".join(f"{k}={v}" for k, v in cookie_dict.items() if k.startswith(contracts.AUTH_COOKIE_PREFIX))
    # Adding an account by cookies while signed in: keep the current
    # account's freshest (rotated) cookie so it stays switchable.
    try:
        _sync_current_account_cookie(get_client().cookie_str)
    except Exception:
        pass
    resp, ok = _activate_cookie(auth_cookies, method)
    return (resp, 200) if ok else (resp, 401)


@app.route("/api/login/poll")
def api_login_poll():
    """Sign-in window: report once the arena.ai page holds a confirmed account session."""
    if not _engine_on():
        return jsonify({"ok": False, "done": True, "error": "The sign-in window is part of the desktop app."})
    st = ENGINE.state()
    if not ENGINE.ready():
        if st["login_open"]:
            return jsonify({"ok": False, "done": False, "hint": "Finish signing in in the arena.ai window."})
        return jsonify({"ok": False, "done": True, "error": "The sign-in window was closed before sign-in finished."})
    # Check Arena's /me directly, even when WebView2 does not enumerate auth
    # cookies. Do this once more after a manual close too: the user may have
    # completed sign-in and closed the window before the previous poll ran.
    user, err = ENGINE.get_me()
    if not user or not _user_identity(user)["email"]:
        err = err or "Signed in as a guest only. Sign in with Google or email in the arena.ai window."
        login_open = ENGINE.state()["login_open"]
        return jsonify({"ok": False, "done": not login_open, "hint": err,
                        "error": None if login_open else "The sign-in window was closed before sign-in finished."})
    ENGINE.sync_to(get_client())
    _me_cache.update(t=time.time(), user=user, err=None)
    _register_account(user, get_client().cookie_str)
    ENGINE.finish_login()
    return jsonify({"ok": True, "done": True, "accounts": _accounts_public(), **_user_identity(user)})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    global _client
    current = _current_account_id()
    if current:
        _remove_account(current)
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
    if _engine_on():
        ENGINE.logout()
    _client = ArenaClient(cookie_str=None)
    _me_cache.update(t=0.0, user=None, err=None)
    return jsonify({"ok": True, "accounts": _accounts_public()})


@app.route("/api/accounts")
def api_accounts():
    store = _accounts_load()
    return jsonify({"accounts": _accounts_public(store), "current_id": store.get("current")})


@app.route("/api/accounts/switch", methods=["POST"])
def api_account_switch():
    """Switch the whole app (Arena window + API client) to a saved account."""
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("id") or "")
    store = _accounts_load()
    record = next((a for a in store["accounts"] if a.get("id") == account_id), None)
    if record is None:
        return jsonify({"ok": False, "error": "That account is not saved in this app."}), 404
    if _generation_active():
        return jsonify({"ok": False, "error": "Finish or stop the running generations before switching accounts."}), 409
    cookie = record.get("cookie") or ""
    if not cookie:
        _mark_account_expired(account_id)
        return jsonify({"ok": False, "expired": True, "accounts": _accounts_public(),
                        "error": "This account has no saved session. Sign in to it again."}), 401
    # Keep the freshest (rotated) cookie for the account we are leaving.
    try:
        _sync_current_account_cookie(get_client().cookie_str)
    except Exception:
        pass
    resp, ok = _activate_cookie(cookie, "account-switch")
    payload = resp.get_json(silent=True) or {}
    if not ok:
        _mark_account_expired(account_id)
        payload.update({"expired": True, "accounts": _accounts_public()})
        return jsonify(payload), 401
    payload["accounts"] = _accounts_public()
    return jsonify(payload)


@app.route("/api/accounts/add", methods=["POST"])
def api_account_add():
    """Open the sign-in window for a NEW account without signing the current
    one out: its stored session stays in the account list and can be
    switched back to at any time."""
    if _generation_active():
        return jsonify({"ok": False, "error": "Finish or stop the running generations before adding an account."}), 409
    if not _engine_on():
        return jsonify({"ok": False, "error": "The sign-in window is part of Arena Desktop (Arena-Desktop.exe). "
                        "Use Sign in with cookies to add another account here."}), 400
    try:
        _sync_current_account_cookie(get_client().cookie_str)
    except Exception:
        pass
    _me_cache.update(t=0.0, user=None, err=None)
    ENGINE.clear_session_cookies()
    ENGINE.open_login()
    return jsonify({"ok": True, "pending": True})


@app.route("/api/accounts/remove", methods=["POST"])
def api_account_remove():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("id") or "")
    store = _accounts_load()
    if account_id == store.get("current"):
        return jsonify({"ok": False, "error": "This is the current account. Use Sign out to remove it."}), 400
    _remove_account(account_id)
    return jsonify({"ok": True, "accounts": _accounts_public()})


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return jsonify(load_config())
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    cfg.update(data)
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/usage")
def api_usage():
    import datetime
    store = _usage()
    return jsonify({"today": datetime.date.today().isoformat(),
                    "current_account": _current_account_id(),
                    "days": store.get("days") or {},
                    "events": list(reversed(store.get("events") or []))})


@app.route("/api/gateway")
def api_gateway():
    cfg = load_config()
    available = []
    try:
        for name, srv in (connectors.configured_servers() or {}).items():
            available.append({"name": name, "enabled": bool(srv.get("enabled")),
                              "transport": srv.get("transport", "stdio")})
    except Exception:
        pass
    return jsonify({
        "enabled": _gateway_enabled(cfg),
        "auto": cfg.get("gateway_auto") is True,
        "chosen": cfg.get("gateway_servers") if isinstance(cfg.get("gateway_servers"), list) else None,
        "available": available,
        "session": _gateway().status(include_pairing=True),
    })


@app.route("/api/gateway/start", methods=["POST"])
def api_gateway_start():
    cfg = load_config()
    if not _gateway_enabled(cfg):
        return jsonify({"error": "Agent Gateway is off. Enable it in Settings -> Gateway first.",
                        "code": "gateway_off"}), 403
    servers = _gateway_servers(cfg)
    if not servers:
        return jsonify({"error": "No enabled local MCP servers to expose. Add one under Connectors (Local MCP) first.",
                        "code": "no_servers"}), 400
    status = _gateway().start(servers, wait_seconds=45)
    if status["state"] == "needs_cloudflared":
        return jsonify({"error": "cloudflared is not installed", "code": "cloudflared_missing",
                        "session": status}), 409
    if status["state"] != "on":
        return jsonify({"error": status.get("error") or "The gateway failed to start.",
                        "code": "gateway_failed", "session": status}), 502
    return jsonify({"ok": True, "session": status})


@app.route("/api/gateway/stop", methods=["POST"])
def api_gateway_stop():
    return jsonify({"ok": True, "session": _gateway().stop("stopped")})


@app.route("/api/components")
def api_components():
    payload = {"components": components.detect()}
    payload.update(components.install_status())
    return jsonify(payload)


@app.route("/api/components/install", methods=["POST"])
def api_components_install():
    data = request.get_json(silent=True) or {}
    result = components.start_install(str(data.get("id") or ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/mode", methods=["POST"])
def api_mode():
    global _ui_mode, _ui_modality
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "battle")
    if mode not in contracts.UI_MODES:
        return jsonify({"ok": False, "error": f"Invalid mode: {mode}",
                        "code": "unsupported_selection"}), 400
    # Switching mode never fails on the previously selected work type:
    # Agent simply runs as Text (its work-type selector is hidden), and an
    # Auto/Video selection only survives in Battle. An explicit modality in
    # the same request is honored after the same normalization.
    requested = data.get("modality") if isinstance(data.get("modality"), str) and data.get("modality") else _ui_modality
    mode, modality = contracts.normalize_ui_selection(mode, requested)
    api_mode_val = contracts.api_mode(mode)
    client = get_client()
    client.active_mode = api_mode_val
    client.active_modality = contracts.api_modality(modality)
    client.eval_id = None
    _ui_mode, _ui_modality = mode, modality
    return jsonify({"ok": True, "mode": mode, "api_mode": api_mode_val,
                    "modality": modality, "api_modality": client.active_modality})


@app.route("/api/modality", methods=["POST"])
def api_modality():
    global _ui_modality
    data = request.get_json(silent=True) or {}
    modality = data.get("modality", "chat")
    if _ui_mode == "agent":
        # Agent has no work-type selector; a stale modality call is a no-op
        # that lands on Text instead of an error the user cannot fix in UI.
        modality = "text"
    error = _selection_error(_ui_mode, modality)
    if error:
        return jsonify({"ok": False, "error": error, "code": "unsupported_selection"}), 400
    api_mod = contracts.api_modality(modality)
    client = get_client()
    client.active_modality = api_mod
    client.eval_id = None
    _ui_modality = modality
    return jsonify({"ok": True, "modality": modality, "api_modality": api_mod})


@app.route("/api/new", methods=["POST"])
def api_new_chat():
    global _conversation_history, _current_chat_id
    client = get_client()
    client.eval_id = None
    client.sources.clear()
    _conversation_history = []
    _current_chat_id = str(uuid.uuid4())
    return jsonify({"ok": True, "chat_id": _current_chat_id})


@app.route("/api/history")
def api_history():
    return jsonify(_conversation_history)


@app.route("/api/chats")
def api_chats_list():
    return jsonify(_list_chats())


@app.route("/api/chats/<chat_id>")
def api_chat_load(chat_id):
    global _conversation_history, _current_chat_id, _ui_mode, _ui_modality
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    # Reopening a chat must restore the server-side mode as well as the UI.
    # Otherwise a follow-up could silently be sent as a different type of chat.
    saved_mode, saved_modality = data.get("mode"), data.get("modality")
    if saved_mode in MODE_API_MAP and saved_modality in MODALITY_API_MAP:
        # Legacy chats may contain combinations Arena never supported (for
        # example Direct+Video or Agent+Code).  Normalize the live state and
        # the returned copy; do not silently restore an impossible request.
        _ui_mode, _ui_modality = contracts.normalize_ui_selection(saved_mode, saved_modality)
        if _ui_modality == "auto" and data.get("messages"):
            # Auto only exists before the first message; a started chat was
            # resolved to a concrete modality (legacy saves meant Text).
            _ui_modality = "text"
        data = dict(data, mode=_ui_mode, modality=_ui_modality)
    if data.get("mode") == "agent":
        # The internally assigned Agent model is an implementation detail and
        # is never a user-facing choice or label.  Keep the on-disk copy for
        # server-side continuation, but sanitize the copy sent to the UI.
        clean_messages = []
        for message in data.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "assistant":
                message = dict(message, names={"a": "Agent", "b": None})
            clean_messages.append(message)
        data = dict(data, model_a=None, model_b=None, messages=clean_messages)
    _current_chat_id = chat_id
    _conversation_history = data.get("messages", [])
    client = get_client()
    client.active_mode = contracts.api_mode(_ui_mode)
    client.active_modality = contracts.api_modality(_ui_modality)
    client.eval_id = data.get("eval_id")
    data = dict(data, history_chars=_history_chars(data.get("messages") or []))
    return jsonify(data)


@app.route("/api/chats/<chat_id>/compact", methods=["POST"])
def api_chat_compact(chat_id):
    """Arm a context reset: the next message starts a fresh Arena session
    seeded with a digest of everything so far.  The local transcript is
    untouched; only the server-side context restarts."""
    if not _validate_chat_id(chat_id):
        return jsonify({"error": "Invalid chat id", "code": "bad_request"}), 400
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found", "code": "not_found"}), 404
    if _chat_generating(chat_id):
        return jsonify({"error": "Wait for the current answer to finish, then compact.",
                        "code": "busy"}), 409
    if (data.get("modality") or "") in ("image", "video"):
        return jsonify({"error": "Compact is for text conversations.",
                        "code": "unsupported"}), 400
    messages = list(data.get("messages") or [])
    real = [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    if len(real) < 2:
        return jsonify({"error": "There is nothing to compact yet.",
                        "code": "empty"}), 400
    digest = _build_digest(data)
    memory_path = _append_digest_to_memory(data, digest)
    marker = {"role": "notice", "compact": True, "digest_chars": len(digest),
              "text": "Context compacted \u2014 this conversation continues in a fresh "
                      "Arena session with a summary of everything above."}
    messages.append(marker)
    try:
        _save_chat(chat_id, title=data.get("title") or "Conversation", messages=messages,
                   mode=data.get("mode") or "battle", modality=data.get("modality") or "text",
                   model_a=data.get("model_a"), model_b=data.get("model_b"),
                   eval_id=None, tools_guide=False, mcp_guide_hash="",
                   agent_backend=data.get("agent_backend"), native={},
                   memory_hash="", pending_digest=digest)
    except Exception:
        LOG.exception("compact save failed")
        return jsonify({"error": "Could not save the compacted chat.", "code": "save_failed"}), 500
    return jsonify({"ok": True, "digest_chars": len(digest),
                    "history_chars": len(digest),
                    "memory_saved": bool(memory_path), "notice": marker["text"]})


@app.route("/api/memory")
def api_memory_get():
    folder = _chosen_ws()
    return jsonify({
        "enabled": _memory_enabled(),
        "folder": str(folder) if folder else None,
        "global": _read_memory_file(GLOBAL_MEMORY_FILE, 100_000),
        "workspace": (_read_memory_file(Path(folder) / MEMORY_FILE_NAME, 100_000)
                      if folder else None),
    })


@app.route("/api/memory", methods=["POST"])
def api_memory_save():
    data = request.get_json(silent=True) or {}
    folder = _chosen_ws()
    try:
        if isinstance(data.get("global"), str):
            GLOBAL_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            GLOBAL_MEMORY_FILE.write_text(data["global"], encoding="utf-8")
        if isinstance(data.get("workspace"), str) and folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            (Path(folder) / MEMORY_FILE_NAME).write_text(data["workspace"], encoding="utf-8")
    except OSError as e:
        return jsonify({"error": f"Could not save memory: {e}", "code": "save_failed"}), 500
    if "enabled" in data:
        cfg = load_config()
        cfg["memory_enabled"] = bool(data.get("enabled"))
        save_config(cfg)
    return jsonify({"ok": True, "enabled": _memory_enabled(),
                    "folder": str(folder) if folder else None})


_update_cache = {"t": 0.0, "data": None}


def _version_tuple(v) -> tuple:
    parts = []
    for bit in str(v or "").lstrip("v").split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


@app.route("/api/update-check")
def api_update_check():
    """Compare APP_VERSION with the latest GitHub release (cached 6h).

    With the beta channel on, the newest pre-release counts too; each
    channel keeps its own cache slot."""
    cfg = load_config()
    enabled = cfg.get("update_check") is not False
    beta = cfg.get("beta_updates") is True
    channel = "beta" if beta else "stable"
    force = request.args.get("force") == "1"
    now = time.time()
    slot = _update_cache.setdefault(channel, {"t": 0.0, "data": None})
    if not force and slot["data"] and now - slot["t"] < 6 * 3600:
        return jsonify(dict(slot["data"], enabled=enabled, cached=True))
    data = {"current": APP_VERSION, "latest": None, "update": False, "url": None,
            "error": None, "channel": channel, "prerelease": False}
    repo = "https://api.github.com/repos/Maxusimlive/Arena-Desktop"
    url = f"{repo}/releases?per_page=10" if beta else f"{repo}/releases/latest"
    try:
        r = requests.get(url,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"Arena-Desktop/{APP_VERSION}"}, timeout=6)
        if r.status_code == 200:
            payload = r.json()
            if beta:
                payload = next((x for x in payload if isinstance(x, dict) and not x.get("draft")), {})
            latest = str(payload.get("tag_name") or "").lstrip("v")
            data["latest"] = latest or None
            data["url"] = payload.get("html_url")
            data["prerelease"] = bool(payload.get("prerelease"))
            data["update"] = bool(latest) and _version_tuple(latest) > _version_tuple(APP_VERSION)
        else:
            data["error"] = f"GitHub returned {r.status_code}"
    except Exception as e:
        data["error"] = f"{type(e).__name__}: {e}"
    slot.update(t=now, data=data)
    return jsonify(dict(data, enabled=enabled))


def _settings_bundle() -> dict:
    """Everything Settings export carries: config, memory files, MCP config.
    Account cookies are never part of it."""
    bundle = {"app": "Arena Desktop", "version": 1, "config": load_config(),
              "memory_global": _read_memory_file(GLOBAL_MEMORY_FILE, 100_000)}
    folder = _chosen_ws()
    bundle["memory_workspace"] = (
        _read_memory_file(Path(folder) / MEMORY_FILE_NAME, 100_000) if folder else None)
    try:
        mcp_file = connectors._config_path()
        bundle["mcp"] = json.loads(mcp_file.read_text(encoding="utf-8")) if mcp_file.exists() else None
    except Exception:
        bundle["mcp"] = None
    return bundle


@app.route("/api/settings/export")
def api_settings_export():
    return jsonify(_settings_bundle())


@app.route("/api/settings/import", methods=["POST"])
def api_settings_import():
    data = request.get_json(silent=True) or {}
    if data.get("app") != "Arena Desktop" or not isinstance(data.get("config"), dict):
        return jsonify({"error": "That file is not an Arena Desktop settings export.",
                        "code": "bad_file"}), 400
    backup_dir = COOKIE_FILE.parent / "backups"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / f"settings-{stamp}.json").write_text(
            json.dumps(_settings_bundle(), ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        return jsonify({"error": f"Could not back up the current settings first: {e}",
                        "code": "backup_failed"}), 500
    cfg = load_config()
    cfg.update(data["config"])
    save_config(cfg)
    try:
        if isinstance(data.get("memory_global"), str):
            GLOBAL_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            GLOBAL_MEMORY_FILE.write_text(data["memory_global"], encoding="utf-8")
        folder = _chosen_ws()
        if folder and isinstance(data.get("memory_workspace"), str):
            (Path(folder) / MEMORY_FILE_NAME).write_text(data["memory_workspace"], encoding="utf-8")
        if isinstance(data.get("mcp"), (dict, list)):
            mcp_file = connectors._config_path()
            mcp_file.parent.mkdir(parents=True, exist_ok=True)
            mcp_file.write_text(json.dumps(data["mcp"], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        return jsonify({"error": f"Import partially failed: {e}", "code": "save_failed"}), 500
    return jsonify({"ok": True, "backup": stamp})


_URL_RE = re.compile(r"(https?://[A-Za-z0-9.\-]+)(/[^\s'\"]*)?")


def _redact_urls(line: str) -> str:
    """Reduce URLs in a log line to scheme+host+path (never the query)."""
    return _URL_RE.sub(lambda m: m.group(1) + (m.group(2) or "").split("?")[0], line)


@app.route("/api/diagnostics")
def api_diagnostics():
    """A copyable support report: versions and state, no prompts or cookies."""
    import platform as _platform
    try:
        signed_in = COOKIE_FILE.exists() and COOKIE_FILE.stat().st_size > 0
    except OSError:
        signed_in = False
    lines = [
        f"Arena Desktop {APP_VERSION}",
        f"Platform: {_platform.platform()}",
        f"Python: {_platform.python_version()}",
        f"Packaged EXE: {bool(getattr(sys, 'frozen', False))}",
        f"Engine window: {_engine_on()}",
        f"Signed in (cookie present): {bool(signed_in)}",
        f"Permission mode: {_perm_mode()}",
        f"Workspace folder chosen: {_chosen_ws() is not None}",
        f"Memory enabled: {_memory_enabled()}",
        f"Local chats: {len(_list_chats())}",
    ]
    try:
        tail = (COOKIE_FILE.parent / "desktop.log").read_text(
            encoding="utf-8", errors="replace").splitlines()[-60:]
    except OSError:
        tail = []
    if tail:
        lines += ["", "--- desktop.log, last lines (URLs reduced to host+path) ---"]
        lines += [_redact_urls(line) for line in tail]
    return jsonify({"text": "\n".join(lines)})


_QUESTIONS: dict = {}
_questions_lock = threading.Lock()
QUESTION_TIMEOUT = 600
STREAM_IDLE_TIMEOUT = 90   # seconds of silence before a stalled model stream is closed
CHILD_TIME_BUDGET = 360    # max seconds one sub-agent (or fan-out child) may run


def _question_answer(qid):
    with _questions_lock:
        q = _QUESTIONS.get(qid)
        return (q or {}).get("answer")


def _parse_todos(raw: str) -> list:
    """Parse a model checklist: one step per line, [x]/[~]/[ ] markers."""
    items = []
    for line in str(raw or "").splitlines():
        line = line.strip().lstrip("-*").strip()
        if not line:
            continue
        status = "pending"
        marker = line[:3].lower()
        if marker in ("[x]", "[\u2713]"):
            status, line = "done", line[3:].strip()
        elif marker in ("[~]", "[>]", "[*]", "[-]"):
            status, line = "doing", line[3:].strip()
        elif marker == "[ ]":
            line = line[3:].strip()
        if line:
            items.append({"text": line[:200], "status": status})
        if len(items) >= 30:
            break
    return items


@app.route("/api/questions")
def api_questions():
    """Questions from models that are still waiting for the user."""
    with _questions_lock:
        items = [{k: q.get(k) for k in ("id", "chat_id", "kind", "question", "plan", "options", "custom", "lane", "subagent")}
                 for q in _QUESTIONS.values() if q.get("answer") is None]
    return jsonify(items)


@app.route("/api/questions/<qid>", methods=["POST"])
def api_question_answer(qid):
    data = request.get_json(silent=True) or {}
    answer = str(data.get("answer") or "").strip()
    if not answer:
        return jsonify({"error": "Write or pick an answer first.", "code": "empty"}), 400
    with _questions_lock:
        q = _QUESTIONS.get(qid)
        if not q or q.get("answer") is not None:
            return jsonify({"error": "That question is no longer waiting.", "code": "gone"}), 404
        q["answer"] = answer[:2000]
    return jsonify({"ok": True})


# ---------- v5: chat search / export / fork, workspace snapshot, Elo, automations ----------

@app.route("/api/search")
def api_search():
    """Full-text search across locally saved chats (title search stays client-side)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    ql = q.lower()
    results = []
    for summary in _list_chats():
        if not isinstance(summary, dict):
            continue
        cid = summary.get("id") or summary.get("chat_id")
        if not cid:
            continue
        data = _load_chat(str(cid))
        if not data:
            continue
        for idx, m in enumerate(data.get("messages") or []):
            matched = False
            for Field_name in ("text", "text_b"):
                text = str(m.get(Field_name) or "")
                pos = text.lower().find(ql)
                if pos >= 0:
                    start = max(0, pos - 60)
                    results.append({
                        "chat_id": str(cid),
                        "title": data.get("title") or summary.get("title") or "Chat",
                        "msg_index": idx,
                        "role": m.get("role") or "assistant",
                        "snippet": (("..." if start else "")
                                    + text[start:pos + len(q) + 60].replace("\n", " ") + "...")})
                    matched = True
                    break
            if matched and len(results) >= 40:
                break
        if len(results) >= 40:
            break
    return jsonify(results[:40])


@app.route("/api/chats/<chat_id>/export.md")
def api_chat_export(chat_id):
    """The whole chat as one Markdown file (tool steps as a compact list)."""
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    lines = [f"# {data.get('title') or 'Chat'}", "",
             f"_Exported from Arena Desktop on {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    for m in data.get("messages") or []:
        role = m.get("role")
        if role == "notice":
            lines += ["", "---", "_Context compacted here_", ""]
            continue
        if role == "user":
            lines += ["## User", "", str(m.get("text") or ""), ""]
        elif role == "assistant":
            names = m.get("names") or {}
            for key, Field_name in (("a", "text"), ("b", "text_b")):
                text = str(m.get(Field_name) or "")
                if not text:
                    continue
                who = names.get(key) or ("Assistant" if key == "a" else "Model B")
                lines += [f"## {who}", "", text, ""]
            for t in m.get("tools") or []:
                lines.append(f"- `{t.get('tool')}` - {t.get('summary') or ''} ({t.get('status') or ''})")
            if m.get("tools"):
                lines.append("")
    fname = re.sub(r"[^A-Za-z0-9._-]+", "-", str(data.get("title") or "chat")).strip("-")[:60] or "chat"
    return Response("\n".join(lines), mimetype="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.md"'})


@app.route("/api/chats/<chat_id>/fork", methods=["POST"])
def api_chat_fork(chat_id):
    """Copy the chat up to a message into a new chat with a fresh Arena session.

    The copied conversation rides into the new session as a pending digest
    (the Compact mechanism), so the model keeps the context that matters."""
    data = _load_chat(chat_id)
    if not data:
        return jsonify({"error": "Chat not found"}), 404
    body = request.get_json(silent=True) or {}
    msgs = list(data.get("messages") or [])
    try:
        idx = int(body.get("message_index"))
    except (TypeError, ValueError):
        idx = len(msgs) - 1
    idx = max(0, min(idx, len(msgs) - 1)) if msgs else -1
    copied = copy.deepcopy(msgs[:idx + 1]) if idx >= 0 else []
    new_id = generate_uuidv7()
    try:
        digest = _build_digest(dict(data, messages=copied)) if copied else ""
    except Exception:
        LOG.exception("fork digest failed")
        digest = ""
    _save_chat(new_id, title=(str(data.get("title") or "Chat") + " (fork)")[:80],
               messages=copied, mode=data.get("mode") or "direct",
               modality=data.get("modality") or "text",
               model_a=data.get("model_a"), model_b=data.get("model_b"),
               eval_id=None, native=None, memory_hash="", pending_digest=digest,
               parent_id=data.get("parent_id"))
    return jsonify({"ok": True, "chat_id": new_id})


@app.route("/api/workspace/snapshot", methods=["POST"])
def api_workspace_snapshot():
    """Concatenate workspace text files into one attachable context block."""
    ws = _chosen_ws()
    if not ws:
        return jsonify({"error": "Choose a workspace folder first.", "code": "no_workspace"}), 400
    root = Path(ws).resolve()
    body = request.get_json(silent=True) or {}
    wanted = [str(x) for x in (body.get("paths") or []) if str(x).strip()][:40]
    files: list = []
    for rel in wanted:
        try:
            target = (root / rel).resolve()
        except OSError:
            continue
        if root != target and root not in target.parents:
            continue
        if target.is_file():
            files.append(target)
        elif target.is_dir():
            for fp in sorted(target.rglob("*")):
                if len(files) >= 40:
                    break
                if fp.is_file() and ".git" not in fp.parts and ".arena-artifacts" not in fp.parts:
                    files.append(fp)
    parts = ["[Attached workspace files]"]
    total = included = 0
    for fp in files[:40]:
        try:
            raw = fp.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:2048]:
            continue
        text = raw.decode("utf-8", errors="replace")[:12000]
        parts.append(f"\n--- {fp.relative_to(root).as_posix()} ---\n{text}")
        total += len(text)
        included += 1
        if total >= 40000:
            break
    parts.append("\n[/Attached workspace files]")
    text = "\n".join(parts)
    return jsonify({"ok": True, "text": text[:44000], "chars": len(text), "files": included})


_elo_cache = {"t": 0.0, "models": {}}
ELO_TTL = 24 * 3600


def _elo_fetch():
    base = "https://raw.githubusercontent.com/oolong-tea-2026/arena-ai-leaderboards/main/data"
    headers = {"User-Agent": f"Arena-Desktop/{APP_VERSION}"}
    r = requests.get(f"{base}/latest.json", headers=headers, timeout=8)
    r.raise_for_status()
    day = str((r.json() or {}).get("path") or "")
    models: dict = {}
    for board in ("text", "code"):
        try:
            rr = requests.get(f"{base}/{day}/{board}.json", headers=headers, timeout=8)
            if rr.status_code != 200:
                continue
            for m in (rr.json() or {}).get("models") or []:
                slug = str(m.get("model") or "").strip().lower()
                if slug and slug not in models:
                    models[slug] = {"rank": m.get("rank"), "score": m.get("score"), "board": board}
        except Exception:
            continue
    return models


@app.route("/api/elo")
def api_elo():
    """Daily Arena leaderboard snapshot (oolong-tea), cached 24h. Optional."""
    if load_config().get("elo_enabled") is False:
        return jsonify({"enabled": False, "models": {}})
    now = time.time()
    cache_file = COOKIE_FILE.parent / "elo_cache.json"
    if not _elo_cache["models"] and cache_file.exists():
        try:
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            _elo_cache.update(t=float(saved.get("t") or 0), models=saved.get("models") or {})
        except Exception:
            pass
    if _elo_cache["models"] and now - _elo_cache["t"] < ELO_TTL:
        return jsonify({"enabled": True, "models": _elo_cache["models"], "cached": True})
    try:
        models = _elo_fetch()
        if models:
            _elo_cache.update(t=now, models=models)
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"t": now, "models": models}), encoding="utf-8")
            except OSError:
                pass
    except Exception as e:
        LOG.info("elo fetch failed: %s", e)
    return jsonify({"enabled": True, "models": _elo_cache["models"]})


# ---------------- automations (scheduled agent runs) ----------------

_automation_running: set = set()
_automation_lock = threading.Lock()
_scheduler_started = False
_SCHEDULE_RE = re.compile(r"^(daily\s+([01]?\d|2[0-3]):[0-5]\d|hourly|every\s+\d{1,5}m)$")


def _automation_due(item, now=None) -> bool:
    if not isinstance(item, dict) or item.get("enabled", True) is False:
        return False
    now = now or datetime.now()
    sched = str(item.get("schedule") or "").strip().lower()
    try:
        last_ts = float(item.get("last_run") or 0)
    except (TypeError, ValueError):
        last_ts = 0.0
    m = re.fullmatch(r"every\s+(\d{1,5})m", sched)
    if m:
        return time.time() - last_ts >= int(m.group(1)) * 60
    if sched == "hourly":
        return time.time() - last_ts >= 3600
    m = re.fullmatch(r"daily\s+([01]?\d|2[0-3]):([0-5]\d)", sched)
    if m:
        due = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        return now >= due and last_ts < due.timestamp()
    return False


def _run_automation(item):
    """Run one automation now (on the caller's thread); returns the chat id."""
    chat_id = generate_uuidv7()
    prompt = str(item.get("prompt") or "").strip()
    title = ("Auto: " + str(item.get("name") or "Automation"))[:60]
    _tl.chat_id = chat_id
    _tl.stop_event = threading.Event()
    _tl.error = None
    _tl.perm_override = item.get("permission") or "plan"
    model_id = str(item.get("model") or "") or None
    conv = {"eval_id": None, "new": True, "mode": contracts.api_mode("direct"),
            "modality": contracts.api_modality("text"), "model_a": model_id,
            "model_b": None, "chat_id": chat_id, "auto_model": not model_id,
            "reasoning": {"a": "", "b": ""}, "lane_errors": {"a": [], "b": []},
            "attachments": None, "guide_sent": False, "mcp_guide_hash": None,
            "memory_hash": None, "title": title, "tools": [],
            "allow_delegate": True, "delegate_sent": False}
    prefix = ""
    if _memory_enabled():
        block, _h = _memory_block(_chosen_ws())
        prefix = block or ""
    history = [{"role": "user", "text": prompt}]
    out = {"a": "", "b": ""}
    final_error = None
    _generation_started(chat_id)
    try:
        _save_chat(chat_id, title=title, messages=list(history), mode="direct",
                   modality="text", model_a=model_id, model_b=None, eval_id=None)
        gen = _agent_run(get_client(), prefix + prompt, conv)
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                out = stop.value or out
                break
    except Exception as e:
        final_error = f"{type(e).__name__}: {e}"
        LOG.exception("automation run failed")
    finally:
        _generation_finished(chat_id)
        text = agent.strip_tool_calls(out.get("a") or "").strip()
        reply = {"role": "assistant", "text": text, "text_b": "",
                 "names": {"a": _model_name(model_id) or "Automation", "b": None}}
        if conv.get("tools"):
            reply["tools"] = conv["tools"]
        ckpts = {l: (conv.get(f"_checkpoint_{l}") or {}).get("id")
                 for l in ("a", "b") if (conv.get(f"_checkpoint_{l}") or {}).get("id")}
        if ckpts:
            reply["checkpoints"] = ckpts
        if final_error and not text:
            reply["error"] = final_error
        history.append(reply)
        try:
            _save_chat(chat_id, title=title, messages=history, mode="direct",
                       modality="text", model_a=model_id, model_b=None,
                       eval_id=conv.get("eval_id") if not conv.get("new") else None,
                       tools_guide=True,
                       delegate_guide=bool(conv.get("delegate_sent")))
        except Exception:
            LOG.exception("automation save failed")
        _notify("Arena Desktop", f"Automation '{str(item.get('name') or 'run')[:40]}' finished.", "done")
    return chat_id


def _automation_worker(item):
    try:
        _run_automation(item)
    except Exception:
        LOG.exception("automation worker failed")
    finally:
        with _automation_lock:
            _automation_running.discard(str(item.get("id") or ""))
        try:
            cfg = load_config()
            for it in cfg.get("automations") or []:
                if str(it.get("id")) == str(item.get("id")):
                    it["last_run"] = int(time.time())
            save_config(cfg)
        except Exception:
            LOG.exception("automation last_run save failed")


def _spawn_automation(item) -> bool:
    aid = str(item.get("id") or "")
    with _automation_lock:
        if aid in _automation_running:
            return False
        _automation_running.add(aid)
    threading.Thread(target=_automation_worker, args=(item,), daemon=True).start()
    return True


def _automation_tick():
    for item in load_config().get("automations") or []:
        if _automation_due(item):
            _spawn_automation(item)


def _start_automation_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def loop():
        while True:
            time.sleep(30)
            try:
                _automation_tick()
            except Exception:
                LOG.exception("automation tick failed")

    threading.Thread(target=loop, daemon=True).start()


@app.route("/api/automations")
def api_automations_list():
    return jsonify({"items": load_config().get("automations") or []})


@app.route("/api/automations", methods=["POST"])
def api_automations_save():
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    prompt = str(body.get("prompt") or "").strip()
    schedule = str(body.get("schedule") or "").strip().lower()
    if not name or not prompt:
        return jsonify({"error": "Name and prompt are required.", "code": "bad_request"}), 400
    if not _SCHEDULE_RE.match(schedule):
        return jsonify({"error": "Schedule must be 'daily HH:MM', 'hourly' or 'every Nm'.",
                        "code": "bad_request"}), 400
    permission = body.get("permission") if body.get("permission") in ("plan", "bypassPermissions") else "plan"
    cfg = load_config()
    items = list(cfg.get("automations") or [])
    aid = str(body.get("id") or "") or generate_uuidv7()
    entry = {"id": aid, "name": name[:80], "prompt": prompt[:4000], "schedule": schedule,
             "enabled": body.get("enabled") is not False, "permission": permission,
             "model": str(body.get("model") or "") or None,
             "last_run": 0}
    for i, it in enumerate(items):
        if str(it.get("id")) == aid:
            entry["last_run"] = it.get("last_run") or 0
            items[i] = entry
            break
    else:
        items.append(entry)
    cfg["automations"] = items[:25]
    save_config(cfg)
    return jsonify({"ok": True, "item": entry})


@app.route("/api/automations/<aid>", methods=["DELETE"])
def api_automations_delete(aid):
    cfg = load_config()
    items = [it for it in (cfg.get("automations") or []) if str(it.get("id")) != str(aid)]
    cfg["automations"] = items
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/automations/<aid>/run", methods=["POST"])
def api_automations_run(aid):
    item = next((it for it in (load_config().get("automations") or [])
                 if str(it.get("id")) == str(aid)), None)
    if not item:
        return jsonify({"error": "Automation not found", "code": "gone"}), 404
    if not _spawn_automation(item):
        return jsonify({"error": "That automation is already running.", "code": "busy"}), 409
    return jsonify({"ok": True})


_start_automation_scheduler()


def _archive_agentic_on_arena(session_id: str) -> Optional[str]:
    """POST /api/chat/{id}/archive (Arena Agent sessions). Error or None."""
    path = contracts.PATHS["chat_archive"].format(
        session_id=urllib.parse.quote(str(session_id)))
    client = get_client()
    try:
        if _engine_on():
            if not ENGINE.ready():
                return ("arena.ai is waiting for a check" if ENGINE.challenge
                        else "arena.ai session is still loading")
            response = ENGINE.request(path, body={}, method="POST", timeout=35)
            status, detail = response.status_code, response.error
            if status in (200, 201, 204):
                ENGINE.sync_to(client)
                return None
        else:
            raw = client.session.post(contracts.ORIGIN + path, json={}, timeout=35)
            status, detail = raw.status_code, None
            if status in (200, 201, 204):
                client.sync_cookies()
                return None
    except requests.RequestException as e:
        return str(e)
    if status == 0:
        return f"Can't reach arena.ai: {detail}" if detail else "Can't reach arena.ai"
    if status in (401, 403):
        return "Arena session is not signed in or has expired"
    return f"arena.ai returned HTTP {status}"


def _archive_session_on_arena(session_id: str, agentic: bool):
    """Archive one Arena session. Returns (state, error).

    state is "archived", "failed" or "skipped" (no engine window for the
    evaluation Server Action). Mirrors arena.ai's own sidebar: evaluation
    sessions go through the archiveEvaluationSession Server Action,
    agentic sessions through POST /api/chat/{id}/archive.
    """
    session_id = str(session_id or "").strip()
    if not session_id:
        return "none", None
    if agentic:
        error = _archive_agentic_on_arena(session_id)
        return ("failed", error) if error else ("archived", None)
    if not _engine_on():
        return "skipped", "The arena.ai window is unavailable, so the chat could not be archived on Arena."
    try:
        result = ENGINE.archive_evaluation(session_id)
    except Exception as e:  # engine hiccup must not block the local delete
        return "failed", str(e)
    if result.get("ok"):
        return "archived", None
    detail = str(result.get("error") or "archive failed")
    if detail in ("no_window", "login_open", "probe_no_answer"):
        friendly = "The arena.ai window is not ready yet, so the chat could not be archived on Arena."
    elif "UnrecognizedAction" in detail or "not found on the server" in detail:
        friendly = "Arena changed its internals and refused the archive. The chat may still show on arena.ai."
    else:
        friendly = detail
    return "failed", friendly


def _chat_arena_session(chat: dict):
    """(session_id, agentic) for a stored chat, or (None, False)."""
    if not isinstance(chat, dict):
        return None, False
    native = chat.get("native") if isinstance(chat.get("native"), dict) else {}
    if chat.get("agent_backend") == "arena" or native.get("session_id"):
        session_id = native.get("session_id") or chat.get("eval_id")
        return (str(session_id), True) if session_id else (None, False)
    if chat.get("eval_id"):
        return str(chat["eval_id"]), False
    return None, False


def _delete_chat_everywhere(client, chat_id: str, chat: dict = None):
    """Delete a chat locally and archive it on Arena; returns (ok, message).

    Shared by the delete route's semantics and the delete_subagent tool.
    """
    chat = chat if isinstance(chat, dict) else (_load_chat(chat_id) or {})
    note = ""
    session_id, agentic = _chat_arena_session(chat)
    if session_id:
        if _signed_in_somewhere(client):
            _state, arena_error = _archive_session_on_arena(session_id, agentic)
            if arena_error:
                note = f" It could not be archived on Arena ({arena_error})."
        else:
            note = " It was not archived on Arena (not signed in)."
    if not _delete_chat(chat_id):
        return False, "That sub-agent chat no longer exists."
    _orphan_children(chat_id)
    return True, f"Sub-agent chat deleted.{note}"


@app.route("/api/chats/<chat_id>/delete", methods=["POST"])
def api_chat_delete(chat_id):
    """Delete locally and archive the same conversation on arena.ai."""
    if not _validate_chat_id(chat_id) or not (CHATS_DIR / chat_id).is_dir():
        return jsonify({"error": "Chat not found"}), 404
    chat = _load_chat(chat_id) or {}
    arena_state, arena_error = "none", None
    session_id, agentic = _chat_arena_session(chat)
    if session_id:
        if _signed_in_somewhere(get_client()):
            arena_state, arena_error = _archive_session_on_arena(session_id, agentic)
        else:
            arena_state, arena_error = ("skipped",
                                        "Not signed in, so the chat could not be archived on Arena.")
    if not _delete_chat(chat_id):
        return jsonify({"error": "Chat not found"}), 404
    _orphan_children(chat_id)
    return jsonify({"ok": True, "arena": arena_state, "arena_error": arena_error})


@app.route("/api/arena-chats/<session_id>/archive", methods=["POST"])
def api_arena_chat_archive(session_id):
    """Archive a cloud (not yet imported) chat directly on arena.ai."""
    if not _validate_chat_id(session_id):
        return jsonify({"ok": False, "error": "Invalid chat ID"}), 400
    if not _signed_in_somewhere(get_client()):
        return jsonify({"ok": False, "error": "Not signed in", "code": "auth"}), 401
    data = request.get_json(silent=True) or {}
    agentic = str(data.get("type") or "").lower() == "agentic"
    state_name, error = _archive_session_on_arena(session_id, agentic)
    if state_name == "archived":
        return jsonify({"ok": True, "arena": "archived"})
    return jsonify({"ok": False, "arena": state_name,
                    "error": error or "Arena refused the archive."}), 502


def _evaluation_pair_message_ids(eval_id, pair_pos):
    """Assistant message ids (a, b) of one dual turn from the Arena session.

    Used for chats saved before message ids were stored locally: the
    session groups assistant messages by their parent user message.
    """
    session, error, _ = _arena_get_json(
        contracts.PATHS["evaluation"].format(
            eval_id=urllib.parse.quote(str(eval_id))), timeout=35)
    if error or not isinstance(session, dict):
        return None, None
    raw = [m for m in (session.get("messages") or [])
           if isinstance(m, dict) and str(m.get("role") or "").lower() == "assistant"]
    indexed = list(enumerate(raw))
    indexed.sort(key=lambda pair: (str(pair[1].get("createdAt") or ""), pair[0]))
    pairs: dict = {}
    order: list = []
    for _index, msg in indexed:
        parents = msg.get("parentMessageIds") if isinstance(msg.get("parentMessageIds"), list) else []
        key = str(parents[0]) if parents else f"single:{msg.get('id')}"
        if key not in pairs:
            pairs[key] = {}
            order.append(key)
        lane = "b" if str(msg.get("participantPosition") or "a").lower() == "b" else "a"
        if msg.get("id"):
            pairs[key][lane] = str(msg["id"])
    complete = [pairs[key] for key in order if pairs[key].get("a") and pairs[key].get("b")]
    if isinstance(pair_pos, int) and 0 <= pair_pos < len(complete):
        return complete[pair_pos]["a"], complete[pair_pos]["b"]
    if complete:
        return complete[-1]["a"], complete[-1]["b"]
    return None, None


_VOTE_VALUES = ("model_a", "model_b", "tie", "both_bad")


@app.route("/api/chats/<chat_id>/vote", methods=["POST"])
def api_chat_vote(chat_id):
    """Cast the Battle pairwise vote on Arena and reveal the two models.

    The vote itself is submitted by the arena.ai page (its own Server
    Action with a genuine reCAPTCHA token, like the website's vote bar);
    the app stores the choice and the revealed model names on the chat.
    """
    if not _validate_chat_id(chat_id):
        return jsonify({"error": "Invalid chat ID"}), 400
    data = request.get_json(silent=True) or {}
    value = data.get("value")
    if value not in _VOTE_VALUES:
        return jsonify({"error": "Unknown vote value", "code": "bad_request"}), 400
    chat = _load_chat(chat_id)
    if not chat:
        return jsonify({"error": "Chat not found"}), 404
    if chat.get("mode") not in ("battle", "side-by-side"):
        return jsonify({"error": "Voting is available only in Battle and Side by Side.",
                        "code": "unsupported_selection"}), 400
    eval_id = chat.get("eval_id")
    if not eval_id:
        return jsonify({"error": "This chat has no Arena session to vote on.",
                        "code": "arena_history"}), 422
    messages = [dict(m) if isinstance(m, dict) else m for m in (chat.get("messages") or [])]
    dual_indexes = [i for i, m in enumerate(messages)
                    if isinstance(m, dict) and m.get("role") == "assistant" and "text_b" in m]
    if not dual_indexes:
        return jsonify({"error": "This chat has no paired answers to vote on.",
                        "code": "bad_request"}), 422
    req_a, req_b = data.get("message_a_id"), data.get("message_b_id")
    target = None
    if req_a and req_b:
        for i in dual_indexes:
            ids = messages[i].get("arena_message_ids") or {}
            if ids.get("a") == req_a and ids.get("b") == req_b:
                target = i
                break
    pair_pos = data.get("pair_index")
    if target is None and isinstance(pair_pos, int) and 0 <= pair_pos < len(dual_indexes):
        target = dual_indexes[pair_pos]
    if target is None:
        target = dual_indexes[-1]
    msg = messages[target]
    if msg.get("vote"):
        return jsonify({"ok": False, "error": "This answer has already been voted on.",
                        "vote": msg.get("vote"), "names": msg.get("names")}), 409
    ids = msg.get("arena_message_ids") or {}
    a_id, b_id = ids.get("a") or req_a, ids.get("b") or req_b
    if not (a_id and b_id):
        a_id, b_id = _evaluation_pair_message_ids(eval_id, dual_indexes.index(target))
    if not (a_id and b_id):
        return jsonify({"error": "Could not identify the Arena messages for the vote.",
                        "code": "arena_history"}), 422
    if not _engine_on():
        return jsonify({"error": "Voting works in the desktop app: "
                        "the vote is submitted by the arena.ai page itself.",
                        "code": "engine_required"}), 503
    res = ENGINE.pairwise_vote({"evaluationSessionId": eval_id,
                                "messageAId": a_id, "messageBId": b_id, "value": value})
    result = (res or {}).get("result") or {}
    if not res or not res.get("ok") or not result.get("success"):
        detail = str(result.get("error") or (res or {}).get("error") or "unknown error")
        LOG.info("vote failed: %s (trace=%s)", detail[:200], (res or {}).get("trace"))
        if "UnrecognizedAction" in detail or "not found on the server" in detail:
            friendly = ("Arena has just updated its website, so voting needs a fresh connection. "
                        "Wait a few seconds and press the vote again.")
        elif detail in ("no_window", "login_open", "probe_no_answer") or detail.startswith("vote_fn_missing"):
            friendly = "The arena.ai window is not ready yet. Wait until it loads, then vote again."
        elif "captcha" in detail.lower() or "recaptcha" in detail.lower():
            friendly = "Arena asked for a human check before accepting the vote. Try again in a moment."
        else:
            short = " ".join(detail.split())[:160]
            friendly = f"Arena did not accept the vote: {short}"
        return jsonify({"error": friendly, "code": "arena_vote"}), 502
    model_a_id, model_b_id = result.get("modelAId"), result.get("modelBId")
    revealed = {str(item.get("id")): item.get("name")
                for item in (result.get("revealed") or [])
                if isinstance(item, dict) and item.get("id") and item.get("name")}
    names = {"a": revealed.get(str(model_a_id)) or _model_name(model_a_id),
             "b": revealed.get(str(model_b_id)) or _model_name(model_b_id)}
    msg["vote"] = value
    msg.setdefault("arena_message_ids", {"a": a_id, "b": b_id})
    if names["a"] or names["b"]:
        # One pair of models serves the whole session: reveal everywhere.
        for i in dual_indexes:
            current = messages[i].get("names") or {}
            messages[i]["names"] = {"a": current.get("a") or names["a"],
                                    "b": current.get("b") or names["b"]}
    _save_chat(chat_id, title=chat.get("title") or "Chat", messages=messages,
               mode=chat.get("mode"), modality=chat.get("modality"),
               model_a=chat.get("model_a") or model_a_id,
               model_b=chat.get("model_b") or model_b_id,
               eval_id=eval_id, source=chat.get("source"),
               tools_guide=chat.get("tools_guide"),
               agent_backend=chat.get("agent_backend"), native=chat.get("native"))
    LOG.info("vote: chat %s value=%s models=%s/%s", chat_id[:8], value, model_a_id, model_b_id)
    return jsonify({"ok": True, "vote": value, "names": messages[target].get("names"),
                    "model_a": model_a_id, "model_b": model_b_id})


# ---------- arena.ai account history (cloud chats) ----------

def _arena_get_json(path: str, timeout: float = 30, method: str = "GET"):
    """Request one Arena API resource through the signed-in engine when present."""
    method = str(method or "GET").upper()
    client = get_client()
    detail = None
    if _engine_on():
        if not ENGINE.ready():
            return None, ("arena.ai is waiting for a check" if ENGINE.challenge
                          else "arena.ai session is still loading"), 0
        response = ENGINE.request(path, method=method, timeout=timeout)
        text = response.text
        status = response.status_code
        detail = response.error
        if status == 200:
            ENGINE.sync_to(client)
    else:
        try:
            raw = client.session.request(method, contracts.ORIGIN + path, timeout=timeout)
        except requests.RequestException as e:
            return None, str(e), 0
        text, status = raw.text, raw.status_code
        if status == 200:
            client.sync_cookies()
    if status == 0:
        return None, (f"Can't reach arena.ai: {detail}" if detail else "Can't reach arena.ai"), status
    if status in (401, 403):
        return None, "Arena session is not signed in or has expired", status
    if status != 200:
        return None, f"arena.ai returned HTTP {status}", status
    try:
        return json.loads(text), None, status
    except ValueError:
        return None, "arena.ai returned an unexpected non-JSON reply", status


def _arena_post_json(path: str, payload: dict, timeout: float = 30):
    """POST JSON to an Arena API resource; returns (data, error, http_status)."""
    client = get_client()
    detail = None
    if _engine_on():
        if not ENGINE.ready():
            return None, ("arena.ai is waiting for a check" if ENGINE.challenge
                          else "arena.ai session is still loading"), 0
        response = ENGINE.request(path, body=payload, method="POST", timeout=timeout)
        text, status = response.text, response.status_code
        detail = response.error
        if status == 200:
            ENGINE.sync_to(client)
    else:
        try:
            raw = client.session.post(contracts.ORIGIN + path, json=payload, timeout=timeout)
        except requests.RequestException as e:
            return None, str(e), 0
        text, status = raw.text, raw.status_code
        if status == 200:
            client.sync_cookies()
    if status == 0:
        return None, (f"Can't reach arena.ai: {detail}" if detail else "Can't reach arena.ai"), status
    if status in (401, 403):
        return None, "Arena session is not signed in or has expired", status
    if status != 200:
        return None, f"arena.ai returned HTTP {status}", status
    try:
        return json.loads(text), None, status
    except ValueError:
        return None, "arena.ai returned an unexpected non-JSON reply", status


def _resolve_auto_modality(message: str, has_image: bool = False) -> str:
    """Ask Arena's own auto-modality router, like Battle+Auto on the website.

    Returns an Arena API modality. Any failure falls back to chat so a router
    hiccup never blocks the message itself.
    """
    try:
        data, error, _status = _arena_post_json(
            contracts.PATHS["auto_modality"],
            {"user_prompt": message, "has_image": bool(has_image)},
            timeout=20)
    except Exception as exc:  # the router must never block the message itself
        LOG.info("auto-modality router unavailable (%s); using chat", exc)
        return "chat"
    if error or not isinstance(data, dict):
        if error:
            LOG.info("auto-modality router unavailable (%s); using chat", error)
        return "chat"
    modality = contracts.pick_auto_modality(data.get("probabilities"), has_image=has_image)
    LOG.info("auto-modality resolved to %s", modality)
    return modality


@app.route("/api/arena-chats")
def api_arena_chats():
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"ok": False, "entries": [], "error": "Not signed in", "code": "auth"}), 401
    limit_raw = request.args.get("limit", "50")
    try:
        limit = max(1, min(50, int(limit_raw)))
    except ValueError:
        limit = 50
    query = urllib.parse.urlencode({"limit": limit, "includeArchived": "false"})
    data, error, status = _arena_get_json(f"{contracts.PATHS['history_unified']}?{query}", timeout=35)
    if error:
        code = 503 if status == 0 else status
        return jsonify({"ok": False, "entries": [], "error": error, "code": "arena_history"}), code
    raw_entries = data.get("entries") if isinstance(data, dict) else []
    entries = []
    for raw in raw_entries or []:
        summary = history_sync.entry_summary(raw)
        if not summary:
            continue
        local = _find_chat_by_eval(summary["eval_id"])
        summary["local_chat_id"] = local.get("id") if local else None
        # A local Agent conversation is a direct-battle evaluation on
        # Arena's side; label it by the mode the user actually used here.
        # Native Agent chats store the Arena session id as eval_id too, so
        # agentic entries link to their local copy the same way.
        summary["local_mode"] = local.get("mode") if local else None
        entries.append(summary)
    return jsonify({
        "ok": True,
        "entries": entries,
        "pagination": data.get("pagination") if isinstance(data, dict) else None,
        "partial": bool(data.get("partial")) if isinstance(data, dict) else False,
    })


@app.route("/api/arena-chats/<eval_id>/open", methods=["POST"])
def api_arena_chat_open(eval_id):
    if not _validate_chat_id(eval_id):
        return jsonify({"error": "Invalid Arena chat ID"}), 400
    data = request.get_json(silent=True) or {}
    kind = str(data.get("type") or "evaluation")
    if kind == "agentic":
        local = _find_chat_by_eval(eval_id)
        if local:
            return jsonify({"ok": True, "chat_id": local["id"], "imported": False})
        # Import the cloud Arena Agent session as a continuable local chat.
        # The native coordinates are the session id itself (Arena's strict
        # bootstrap guarantees triggerAddress == sessionId); the stream
        # cursor is unknown, so the first continuation drains the backlog
        # before appending (needs_drain). The transcript fetch is
        # best-effort: continuation must work even if it fails.
        title = str(data.get("title") or "").strip()
        messages = []
        cursor = None
        session, error, _status = _arena_get_json(
            contracts.PATHS["chat_session"].format(
                session_id=urllib.parse.quote(str(eval_id))), timeout=35)
        if not error and isinstance(session, dict):
            title = history_sync.agentic_title(session) or title
            messages = history_sync.agentic_messages(session)
            # The snapshot carries the authoritative stream cursor; with it
            # the first continuation subscribes at the head directly and no
            # backlog draining is needed at all.
            cursor = history_sync.agentic_last_event_id(session)
        if not title:
            first_user = next((m for m in messages if m.get("role") == "user"), None)
            title = (first_user["text"][:50] if first_user else "") or "Arena Agent"
        _save_chat(eval_id, title=title, messages=messages, mode="agent", modality="text",
                   model_a=None, model_b=None, eval_id=eval_id, source="arena",
                   agent_backend="arena",
                   native={"backend": "arena", "session_id": eval_id,
                           "trigger_address": eval_id, "last_event_id": cursor,
                           "needs_drain": cursor is None})
        return jsonify({"ok": True, "chat_id": eval_id, "imported": True})
    local = _find_chat_by_eval(eval_id)
    if local:
        return jsonify({"ok": True, "chat_id": local["id"], "imported": False})
    session, error, status = _arena_get_json(
        contracts.PATHS["evaluation"].format(eval_id=urllib.parse.quote(eval_id)), timeout=45)
    if error:
        code = 503 if status == 0 else status
        return jsonify({"ok": False, "error": error, "code": "arena_history"}), code
    try:
        converted = history_sync.session_to_local(session, model_name=_model_name)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Could not import this Arena chat: {e}"}), 422
    _save_chat(eval_id, title=converted["title"], messages=converted["messages"],
               mode=converted["mode"], modality=converted["modality"],
               model_a=converted.get("model_a"), model_b=converted.get("model_b"),
               eval_id=eval_id, source="arena")
    return jsonify({"ok": True, "chat_id": eval_id, "imported": True})


LOG = logging.getLogger("arena.desktop")
_tl = threading.local()  # per request: the last error shown to the user, kept in the chat history


def _sse(obj) -> str:
    if obj.get("type") == "error" and obj.get("model") not in ("a", "b"):
        # Lane-scoped model errors are persisted per lane by the caller; the
        # thread-local is reserved for a failure of the whole turn.
        _tl.error = obj.get("message")
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _sse_wrap(gen, chat_id=None):
    """A crash must not end the stream silently: report it as an error event."""
    _generation_started(chat_id)
    # Per-chat stop: /api/stop sets this exact event for this chat only.
    _tl.stop_event = _chat_stop_event(chat_id) if chat_id else None
    _tl.chat_id = chat_id
    try:
        yield from gen
    except Exception as e:
        yield _sse({"type": "error", "code": "internal", "message": "The app hit an internal error.",
                    "detail": f"{type(e).__name__}: {e}", "retry": True})
        yield _sse({"type": "done", "text_a": "", "text_b": ""})
    finally:
        _tl.stop_event = None
        _tl.chat_id = None
        _generation_finished(chat_id)


def _workspace_dir() -> Path:
    raw = load_config().get("workspace_dir")
    return Path(raw).expanduser() if raw else workspace.PROJECTS_DIR / "workspace"


def _chosen_ws() -> Optional[Path]:
    """The folder the user picked in the composer, or None when they have not picked one."""
    raw = load_config().get("workspace_dir")
    return Path(raw).expanduser() if raw else None


def _panel_workspace_root() -> Path:
    """Folder shown in the right Workspace panel.

    With no explicitly chosen folder, show ArenaProjects itself so Code-mode
    project folders and the default Agent workspace are both visible.
    """
    chosen = _chosen_ws()
    return (chosen if chosen is not None else workspace.PROJECTS_DIR).expanduser().resolve()


class _HttpStream:
    """A requests response with the EngineResponse interface, for running without the desktop engine."""

    def __init__(self, resp=None, error=None):
        self._r, self.error = resp, error
        self.status_code = resp.status_code if resp is not None else 0
        self.headers = dict(resp.headers) if resp is not None else {}

    def iter_text(self):
        if self._r is None:
            return
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        for chunk in self._r.iter_content(chunk_size=None):
            if chunk:
                yield dec.decode(chunk)
        yield dec.decode(b"", final=True)

    @property
    def text(self) -> str:
        if not hasattr(self, "_text"):
            self._text = "".join(self.iter_text())
        return self._text


def _send(client: ArenaClient, path: str, body: dict):
    # Video Arena may take up to ten minutes.  Keep every other modality on
    # the existing three/five-minute guardrails.
    timeout = 720 if body.get("modality") == "video" else 300
    if _engine_on():
        if not ENGINE.wait_ready(30):
            return _HttpStream(error=f"arena.ai is waiting for a {ENGINE.challenge} check" if ENGINE.challenge
                               else "arena.ai window is still loading")
        # The website attaches a genuine `cx` client-interaction blob to
        # every chat submit; without it Arena treats the send as suspect
        # and rate-limits expensive modalities (Video first).
        return ENGINE.request(path, body, timeout=timeout, cx=True)
    try:
        read_idle = timeout if body.get("modality") == "video" else STREAM_IDLE_TIMEOUT
        r = client.session.post(contracts.ORIGIN + path, json=body, stream=True, timeout=(15, read_idle))
    except requests.RequestException as e:
        return _HttpStream(error=str(e))
    client.sync_cookies()
    return _HttpStream(r)


def _wait_for(check, seconds: int):
    """Poll check() once a second with keep-alive pings; returns its first truthy value."""
    for i in range(seconds):
        value = check()
        if value or _stopped():
            return value
        time.sleep(1)
        if i % 10 == 9:
            yield _sse({"type": "ping"})
    return check()


def _permit(mode: str, tool: str, info: dict):
    """Apply the permission mode to one action, asking the user when needed.
    Yields SSE events; returns "allow", "deny" (the user said no) or "blocked" (the mode forbids it)."""
    verdict = BROKER.check(mode, tool, info.get("inside", True), info.get("command", ""))
    if verdict == "allow":
        return "allow"
    if verdict == "deny":
        why = "Plan mode is read-only" if mode == "plan" else "This command is never allowed"
        yield _sse({"type": "notice", "level": "warn", "text": f"{why}: {info.get('summary')}"})
        return "blocked"
    # Tag the request with its chat so the UI can keep other chats usable
    # while this one waits for an answer.
    info = dict(info, chat_id=getattr(_tl, "chat_id", None))
    pid = BROKER.ask(dict(info, mode=mode))
    yield _sse(dict(info, type="permission", id=pid, mode=mode))
    answer = yield from _wait_for(lambda: BROKER.wait(pid, 0), 600)
    if not answer:
        BROKER.resolve(pid, "deny")
        BROKER.wait(pid, 0)
        answer = "deny"
    yield _sse({"type": "permission_done", "id": pid, "decision": answer})
    return answer


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


_preview = {"port": None}
_preview_lock = threading.Lock()


def _preview_url(root: Path):
    """Serve generated sites from a separate port (a separate origin) so their scripts cannot call this app's API."""
    if not (Path(root) / "index.html").is_file():
        return None
    base = workspace.PROJECTS_DIR.resolve()
    try:
        rel = Path(root).resolve().relative_to(base).as_posix()
    except ValueError:
        return None
    with _preview_lock:
        if _preview["port"] is None:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(base)))
            _preview["port"] = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_preview['port']}/{urllib.parse.quote(rel)}/index.html"


def _save_webdev(lane: str, files: list, conv: dict):
    """Mirror a code answer into its local project folder, asking first unless the mode allows edits."""
    root = workspace.project_dir(conv["chat_id"], conv["title"], lane, base=_chosen_ws())
    plan = workspace.plan_project(root, files)
    mode = _perm_mode()
    write = delete = False
    if plan["writes"]:
        write = (yield from _permit(mode, "write_project", {
            "tool": "write_project", "path": str(root), "rel": root.name, "inside": True,
            "summary": f"Save {len(plan['writes'])} code file(s) from model {lane.upper()} to {root}",
            "files": sorted(plan["writes"])[:50]})) == "allow"
    if plan["deletes"]:
        delete = (yield from _permit(mode, "delete", {
            "tool": "delete", "path": str(root), "rel": root.name, "inside": True,
            "summary": f"Remove {len(plan['deletes'])} file(s) the new answer dropped from {root}",
            "files": plan["deletes"][:50]})) == "allow"
    written, deleted = workspace.apply_project(plan, write=write, delete=delete) if (write or delete) else (0, 0)
    saved = write or not plan["writes"]
    yield _sse({"type": "files_written", "model": lane, "root": str(root), "written": written, "deleted": deleted,
                "unchanged": plan["unchanged"], "skipped": plan["skipped"], "saved": saved,
                "files": plan["files"], "preview": _preview_url(root) if saved else None})


def _webdev_event(lane: str, item: dict, conv: dict):
    event = item.get("event")
    if event == "title" and isinstance(item.get("title"), str):
        conv["title"] = item["title"][:80]
    yield _sse({"type": "webdev", "model": lane, "event": event, "data": item})
    files = workspace.webdev_files(item)
    if files:
        _last_webdev[(conv["chat_id"], lane)] = {"title": conv["title"], "files": files}
        yield from _save_webdev(lane, files, conv)
    url = contracts.find_preview_url(item)
    if event == "preview-ready" or url:
        yield _sse({"type": "preview", "model": lane, "url": url, "status": item.get("status"),
                    "message": item.get("message") or item.get("reason")})


# ARENA_STREAM_DUMP=1 writes every raw stream line to ~/.arena-cli/streams/<chat>.txt, to study arena's stream parts.
_DUMP_DIR = (CHATS_DIR.parent / "streams") if os.environ.get("ARENA_STREAM_DUMP") else None


def _dump_line(conv: dict, line) -> None:
    raw = line if isinstance(line, str) else line.decode("utf-8", "replace")
    try:
        _DUMP_DIR.mkdir(parents=True, exist_ok=True)
        with open(_DUMP_DIR / f"{conv['chat_id'][:8]}.txt", "a", encoding="utf-8") as f:
            f.write(raw.rstrip("\r\n") + "\n")
    except OSError:
        pass


def _save_media(m: dict, lane: str, conv: dict):
    """Keep a generated image, video or sound in the folder the user picked (when they picked one)."""
    ws = _chosen_ws()
    done = conv.setdefault("media_saved", set())
    if ws is None or m["url"] in done:
        return
    done.add(m["url"])
    try:
        r = requests.get(m["url"], timeout=120)
        r.raise_for_status()
        ext = Path(urlparse(m["url"]).path).suffix[:6] or mimetypes.guess_extension(
            (r.headers.get("content-type") or "").split(";")[0].strip()) or ".bin"
        ws.mkdir(parents=True, exist_ok=True)
        target = ws / f"{workspace.slug(conv['title'])}-{lane}-{uuid.uuid4().hex[:6]}{ext}"
        target.write_bytes(r.content)
    except (requests.RequestException, OSError) as e:
        yield _sse({"type": "notice", "model": lane, "text": f"Could not save the {m.get('kind') or 'file'}: {e}"})
        return
    yield _sse({"type": "notice", "model": lane, "text": f"Saved to {target}"})


def _code_root(chat_id: str, title: str, lane: str, mode: str) -> Path:
    """Where the files an AI builds land: the picked folder (option-a/option-b when two models answer),
    else this chat's own folder under ArenaProjects."""
    ws = _chosen_ws()
    if ws is None:
        return workspace.project_dir(chat_id, title, lane)
    return (ws / agent.LANE_DIRS[lane] if mode in ("battle", "side-by-side") else ws).expanduser().resolve()


def _sync_file(root: Path) -> Path:
    return CHATS_DIR.parent / "sync" / (hashlib.sha1(str(root).lower().encode()).hexdigest()[:16] + ".json")


def _sync_load(root: Path) -> dict:
    """What the AI last wrote to each file in a folder (path -> sha1), to spot the user's own edits."""
    try:
        return json.loads(_sync_file(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _sync_save(root: Path, man: dict) -> None:
    try:
        f = _sync_file(root)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(man), encoding="utf-8")
    except OSError:
        pass


def _digest(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _line_delta(old: str, new: str):
    """Lines added and removed between two versions of a file."""
    add = rem = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            rem += i2 - i1
        if tag in ("replace", "insert"):
            add += j2 - j1
    return add, rem


_SB_VERB = {"read": "Read", "run": "Ran", "touch": "Changed"}


def _sandbox_call(lane: str, call: dict, conv: dict):
    """arena.ai's model used one of arena's own sandbox tools. Copy file writes, edits, deletes and downloads
    into the local project folder, and show the step as an activity row like arena.ai's."""
    op = contracts.sandbox_op(call)
    LOG.info("chat %s sandbox %s %s %s", conv["chat_id"][:8], lane, op["tool"], op.get("path") or op.get("detail", ""))
    row = {"id": op["id"], "kind": op["kind"], "tool": op["tool"], "path": op.get("path"), "detail": op.get("detail"),
           "verb": _SB_VERB.get(op["kind"], op["tool"])}
    if op["kind"] in ("write", "edit") and not (op.get("content") or op.get("old")):
        # arena.ai streams each call twice: first with empty arguments, then complete. Write on the complete one.
        row.update(verb="Writing" if op["kind"] == "write" else "Editing", pending=True)
    elif op["kind"] in ("write", "edit", "delete", "download"):
        root = _code_root(conv["chat_id"], conv["title"], lane, conv["mode"])
        if op["kind"] != "delete" and conv.get("mirror_ok") is None:
            info = {"tool": "write_file", "summary": f"Copy the files the AI writes into {root}", "inside": True,
                    "path": str(root), "rel": ".", "model": lane}
            conv["mirror_ok"] = (yield from _permit(_perm_mode(), "write_file", info)) == "allow"
        try:
            if op["kind"] != "delete" and not conv["mirror_ok"]:
                raise PermissionError("not copied: the permission mode does not allow it")
            target = workspace._inside(root, op["path"])
            rel = target.relative_to(root).as_posix()
            man = _sync_load(root)
            old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            if op["kind"] == "delete":
                # A blanket approval to copy edits is not approval to delete.
                # Never remove a user's untracked or locally modified file.
                if target.is_file():
                    if rel not in man or _digest(target.read_bytes()) != man[rel]:
                        raise PermissionError("not deleted: file is untracked or has local changes")
                    info = {"tool": "delete", "summary": f"Delete mirrored file {rel}", "inside": True,
                            "path": str(target), "rel": rel, "model": lane}
                    if (yield from _permit(_perm_mode(), "delete", info)) != "allow":
                        raise PermissionError("not deleted: permission was not granted")
                    if not target.is_file() or _digest(target.read_bytes()) != man[rel]:
                        raise PermissionError("not deleted: file changed while awaiting permission")
                    target.unlink()
                man.pop(rel, None)
                row["verb"] = "Deleted"
            else:
                if target.exists() and (not target.is_file() or rel not in man or _digest(target.read_bytes()) != man[rel]):
                    raise PermissionError("not copied: file is untracked or has local changes")
                if op["kind"] == "download":
                    r = requests.get(op["url"], timeout=60)
                    r.raise_for_status()
                    data = r.content
                    row["verb"] = "Saved"
                else:
                    if op["kind"] == "edit":
                        if old is None or op["old"] not in old:
                            raise ValueError("not copied: your copy of this file differs from the AI's")
                        new = old.replace(op["old"], op["new"], 1)
                    else:
                        new = op["content"]
                    row["add"], row["del"] = _line_delta(old or "", new)
                    row["verb"] = "Created" if old is None else "Edited"
                    data = new.encode("utf-8")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                man[rel] = _digest(data)
            _sync_save(root, man)
            row.update(abs=str(target), root=str(root))
        except (OSError, ValueError, requests.RequestException) as e:
            row["error"] = str(e)[:160]
    rows = conv.setdefault("sandbox", [])
    if row["id"]:
        rows[:] = [r for r in rows if not (r.get("id") == row["id"] and r.get("model") == lane)]
    rows.append(dict(row, model=lane))
    yield _sse(dict(row, type="sandbox", model=lane))


def _sandbox_result(lane: str, res: dict, conv: dict):
    """A sandbox tool finished: tick its row, and pass on any preview link (<id>.arena.site) it carries."""
    rid = res.get("toolCallId") or res.get("id")
    out = res.get("result", res.get("output"))
    bad = isinstance(out, dict) and bool(out.get("error") or out.get("success") is False or out.get("isError"))
    for row in conv.get("sandbox", []):
        if row.get("id") == rid and row.get("model") == lane:
            row["ok"] = not bad
    yield _sse({"type": "sandbox_done", "model": lane, "id": rid, "ok": not bad})
    url = contracts.find_preview_url(out)
    if url and url not in conv.setdefault("previews", set()):
        conv["previews"].add(url)
        yield _sse({"type": "preview", "model": lane, "url": url})


def _local_edits(root: Path):
    """Files the user changed on disk since the AI last wrote them, as a note in front of the next message."""
    man = _sync_load(root)
    changed, parts, size = [], [], 0
    for rel, digest in list(man.items()):
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if _digest(data) == digest:
            continue
        changed.append(rel)
        text = data.decode("utf-8", "replace")
        if size + len(text) <= 60_000 and len(parts) < 12:
            parts.append(f"--- {rel} ---\n{text}")
            size += len(text)
        man[rel] = _digest(data)
    if not changed:
        return [], ""
    _sync_save(root, man)
    extra = [r for r in changed if not any(p.startswith(f"--- {r} ---") for p in parts)]
    note = ("I edited these files on my computer since your last change. Update your copy of each one to exactly "
            "this content before you continue:\n\n" + "\n\n".join(parts)
            + (f"\n\nAlso changed (too large to include): {', '.join(extra)}" if extra else "") + "\n\nMy message:\n")
    return changed, note


def _line_events(r, conv: dict):
    """Turn arena's stream into SSE events; returns the answer text per lane."""
    out = {"a": "", "b": ""}
    reasoning = conv.setdefault("reasoning", {"a": "", "b": ""})
    split = contracts.LineSplitter()
    expected = {"a", "b"} if conv.get("mode") in ("battle", "side-by-side") else {"a"}
    finished = set()

    def handle(line):
        if _DUMP_DIR:
            _dump_line(conv, line)
        parsed = contracts.parse_line(line)
        if not parsed:
            return
        lane, kind, value = parsed
        if kind == "finish":
            finished.add(lane)
            return
        if kind == "text":
            out[lane] += str(value)
            yield _sse({"type": "text", "model": lane, "text": str(value)})
        elif kind == "reasoning":
            reasoning[lane] += str(value)
            yield _sse({"type": "reasoning", "model": lane, "text": str(value)})
        elif kind == "source":
            yield _sse({"type": "source", "model": lane, "data": value})
        elif kind == "error":
            finished.add(lane)  # a lane that errored will not stream more
            message = f"Model {lane.upper()} stopped with an error: {value}"
            conv.setdefault("lane_errors", {"a": [], "b": []}).setdefault(lane, []).append(message)
            yield _sse({"type": "error", "code": "model", "model": lane, "retry": True,
                        "message": message})
        elif kind == "tool_call" and isinstance(value, dict):
            yield from _sandbox_call(lane, value, conv)
        elif kind == "tool_result" and isinstance(value, dict):
            yield from _sandbox_result(lane, value, conv)
        elif kind in ("data", "file", "annotation"):
            items = contracts.webdev_items(value)
            for item in items:
                yield from _webdev_event(lane, item, conv)
            if not items:
                for m in contracts.find_media(value):
                    yield _sse(dict(m, type="media", model=lane))
                    yield from _save_media(m, lane, conv)
                url = contracts.find_preview_url(value)
                if url:
                    yield _sse({"type": "preview", "model": lane, "url": url})

    def _chunks():
        # A silent stream (server hang, dead connection) raises a read
        # timeout here; turn it into a graceful end-of-turn instead of an
        # endless spinner.
        try:
            yield from r.iter_text()
        except requests.RequestException as e:
            if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                yield "\x00stalled"
                return
            raise

    for chunk in _chunks():
        if chunk == "\x00stalled":
            yield _sse({"type": "notice", "level": "warn",
                        "text": f"The model stopped sending data ({STREAM_IDLE_TIMEOUT}s) - "
                                "the stream was closed to avoid a hang. Send 'continue' to resume."})
            return out
        for line in split.feed(chunk):
            yield from handle(line)
        if _stopped():
            yield _sse({"type": "notice", "text": "Stopped."})
            return out
        if expected <= finished:
            # Every lane already sent its finish record: do not sit on a
            # half-open stream until the server decides to close it (the
            # "answer is here but generation never ends" hang).
            for line in split.flush():
                yield from handle(line)
            return out
    for line in split.flush():
        yield from handle(line)
    return out


# ----------------------------------------------------------------------
# Usage telemetry (v5.1.5): the app counts its own Arena sends per
# account/day and logs every 429, so Settings -> Usage can show where
# the daily balance goes. Best-effort: it must never break a send.
USAGE_FILE = COOKIE_FILE.parent / "usage.json"
_USAGE = None
_USAGE_SENDS_SINCE_SAVE = 0


def _usage() -> dict:
    global _USAGE
    if _USAGE is None:
        try:
            data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
            _USAGE = data if isinstance(data, dict) else {}
        except Exception:
            _USAGE = {}
        _USAGE.setdefault("days", {})
        _USAGE.setdefault("events", [])
    return _USAGE


def _usage_save() -> None:
    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(_usage(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _usage_account():
    try:
        store = _accounts_load()
        current = store.get("current")
        record = next((a for a in store.get("accounts") or [] if a.get("id") == current), None)
        if record:
            return str(record.get("id") or "current"), (record.get("email") or record.get("name") or "")
    except Exception:
        pass
    return "current", ""


def _usage_day() -> dict:
    import datetime
    days = _usage()["days"]
    today = datetime.date.today().isoformat()
    for old in sorted(days)[:-14]:
        if old != today:
            days.pop(old, None)
    return days.setdefault(today, {})


def _usage_blank(email=""):
    return {"email": email, "sends": 0, "by_modality": {}, "models": {}, "rate_limits": 0}


def _usage_note_send(conv) -> None:
    global _USAGE_SENDS_SINCE_SAVE
    try:
        account_id, email = _usage_account()
        acct = _usage_day().setdefault(account_id, _usage_blank(email))
        if email:
            acct["email"] = email
        acct["sends"] = int(acct.get("sends") or 0) + 1
        lane = _pacer_lane_key(conv)
        by_mod = acct.setdefault("by_modality", {})
        by_mod[lane] = int(by_mod.get(lane) or 0) + 1
        model = str((conv or {}).get("model_a") or "")
        if model:
            models = acct.setdefault("models", {})
            models[model] = int(models.get(model) or 0) + 1
        _USAGE_SENDS_SINCE_SAVE += 1
        if _USAGE_SENDS_SINCE_SAVE >= 10:
            _USAGE_SENDS_SINCE_SAVE = 0
            _usage_save()
    except Exception:
        pass


def _usage_note_429(conv, err) -> None:
    try:
        account_id, email = _usage_account()
        acct = _usage_day().setdefault(account_id, _usage_blank(email))
        acct["rate_limits"] = int(acct.get("rate_limits") or 0) + 1
        events = _usage()["events"]
        events.append({"ts": int(time.time()), "account": email or account_id,
                       "modality": _pacer_lane_key(conv),
                       "model": str((conv or {}).get("model_a") or ""),
                       "wait": (err or {}).get("wait"),
                       "message": str((err or {}).get("message") or "")[:200]})
        del events[:-50]
        _usage_save()
    except Exception:
        pass


# ----------------------------------------------------------------------
# Adaptive request pacing (v5.1.1): a client-side governor in front of
# every chat send. Short Arena throttles are mostly triggered by bursts
# (parallel chats, sub-agents, fan-out, retries), so sends pass a
# per-lane token bucket with a concurrency cap; observed 429 resets
# teach it to slow down and queue until the reset instead of erroring.
# Daily-quota waits (hours) are never queued - they surface as before.
_PACER_PRESETS = {
    "gentle": {"text_interval": 3.0, "media_interval": 10.0, "burst": 3, "max_wait": 90.0},
    "strict": {"text_interval": 6.0, "media_interval": 20.0, "burst": 2, "max_wait": 120.0},
}
_PACER_LOCK = threading.Lock()
_PACER_LANES: dict = {}


def _pacing_params() -> dict:
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    params = dict(_PACER_PRESETS.get(str(cfg.get("pacing_preset") or "gentle"),
                                      _PACER_PRESETS["gentle"]))
    params["enabled"] = cfg.get("pacing", True) is not False
    for key in ("text_interval", "media_interval", "burst", "max_wait"):
        value = cfg.get("pacing_" + key)
        if isinstance(value, (int, float)) and value >= 0:
            params[key] = float(value)
    return params


def _pacer_lane_key(conv) -> str:
    return "media" if str((conv or {}).get("modality") or "") in ("image", "video") else "text"


def _pacer_lane(key: str) -> dict:
    lane = _PACER_LANES.get(key)
    if lane is None:
        lane = {"tokens": None, "last_refill": 0.0, "interval": None,
                "cooldown_until": 0.0, "in_flight": 0, "last_429": 0.0}
        _PACER_LANES[key] = lane
    return lane


class _PacerPermit:
    __slots__ = ("lane", "counted", "released")

    def __init__(self, lane=None, counted=False):
        self.lane, self.counted, self.released = lane, counted, False

    def release(self):
        if self.released:
            return
        self.released = True
        if self.counted and self.lane is not None:
            with _PACER_LOCK:
                self.lane["in_flight"] = max(0, self.lane["in_flight"] - 1)


def _pacer_acquire(conv):
    """Wait for a send slot; yields a pacing notice/pings, returns a permit."""
    params = _pacing_params()
    if not params["enabled"]:
        return _PacerPermit()
    key = _pacer_lane_key(conv)
    base = params["media_interval"] if key == "media" else params["text_interval"]
    burst = max(1, int(params["burst"]))
    max_wait = float(params["max_wait"])
    concurrency = 1 if key == "media" else 2
    waited = 0.0
    noticed = False
    ping_at = 10.0
    while True:
        with _PACER_LOCK:
            lane = _pacer_lane(key)
            now = time.monotonic()
            if lane["interval"] is None:
                lane["interval"] = base
                lane["tokens"] = float(burst)
                lane["last_refill"] = now
            if lane["last_429"] and now - lane["last_429"] > 300:
                lane["interval"] = base
                lane["last_429"] = 0.0
            interval = max(0.0, float(lane["interval"]))
            if interval > 0:
                gained = (now - lane["last_refill"]) / interval
                if gained > 0:
                    lane["tokens"] = min(float(burst), (lane["tokens"] or 0.0) + gained)
                    lane["last_refill"] = now
            cooldown_left = lane["cooldown_until"] - now
            wait_needed = 0.0
            if 0 < cooldown_left <= max_wait:
                wait_needed = cooldown_left
            elif lane["in_flight"] >= concurrency:
                wait_needed = 0.2
            elif (lane["tokens"] or 0.0) >= 1.0:
                lane["tokens"] -= 1.0
                lane["in_flight"] += 1
                return _PacerPermit(lane, counted=True)
            elif interval > 0:
                wait_needed = (1.0 - (lane["tokens"] or 0.0)) * interval
            if _stopped() or waited > max_wait:
                # Never trap the user: send anyway (Stop pressed, or the
                # remaining wait is quota-scale rather than a burst window).
                lane["in_flight"] += 1
                return _PacerPermit(lane, counted=True)
        step = min(max(wait_needed, 0.05), 0.5)
        time.sleep(step)
        waited += step
        if waited > 3 and not noticed:
            noticed = True
            yield _sse({"type": "notice",
                        "text": "Pacing requests to stay under Arena's rate limits — sending in a few seconds."})
        if waited >= ping_at:
            ping_at += 10.0
            yield _sse({"type": "ping"})


def _pacer_note_429(conv, wait_seconds) -> None:
    try:
        wait = float(wait_seconds or 0)
    except (TypeError, ValueError):
        wait = 0
    key = _pacer_lane_key(conv)
    params = _pacing_params()
    base = params["media_interval"] if key == "media" else params["text_interval"]
    with _PACER_LOCK:
        lane = _pacer_lane(key)
        now = time.monotonic()
        current = lane["interval"] if lane["interval"] is not None else base
        lane["interval"] = min(max(current * 2, base), 60.0)
        lane["tokens"] = 0.0
        lane["last_refill"] = now
        if wait > 0:
            lane["cooldown_until"] = max(lane["cooldown_until"], now + min(wait, 86400.0))
        lane["last_429"] = now


def _turn(client: ArenaClient, content: str, conv: dict):
    """Send one message and stream the answer. Yields SSE events; returns {"a": text, "b": text},
    or None after sending an error event."""
    v2 = None
    for _ in range(4):
        if conv["new"] and not conv.get("eval_id"):
            conv["eval_id"] = generate_uuidv7()
        try:
            # User attachments ride only on the first send of the turn: a
            # challenge retry re-sends the same turn (keep them), but the
            # local tool loop's follow-up rounds must not re-attach them.
            path, body = contracts.build_chat(
                eval_id=conv["eval_id"], new=conv["new"], mode=conv["mode"], modality=conv["modality"],
                content=content, ids=generate_uuidv7, model_a=conv.get("model_a"), model_b=conv.get("model_b"),
                attachments=conv.get("attachments") or None,
                v2_token=v2, auto_model=bool(conv.get("auto_model")))
        except ValueError as e:
            yield _sse({"type": "error", "code": "bad_request", "message": str(e), "retry": False})
            return None
        v2 = None
        permit = yield from _pacer_acquire(conv)
        try:
            r = _send(client, path, body)
            _usage_note_send(conv)
            LOG.info("chat %s: HTTP %s (v2 token sent: %s)", conv["chat_id"][:8], r.status_code, bool(body.get("recaptchaV2Token")))
            if r.status_code == 200:
                conv["new"] = False
                conv["attachments"] = None  # sent with this turn; not again
                # The pairwise vote addresses the two assistant messages by the
                # client-generated ids from this very request body.
                conv["last_arena_ids"] = {"a": body.get("modelAMessageId"),
                                          "b": body.get("modelBMessageId")}
                out = yield from _line_events(r, conv)
                if _engine_on():
                    ENGINE.sync_to(client)
                return out
            err = contracts.classify_error(r.status_code, r.text if r.status_code else (r.error or ""),
                                           headers=getattr(r, "headers", None))
            if err["code"] == "rate_limit":
                _pacer_note_429(conv, err.get("wait"))
                _usage_note_429(conv, err)
            LOG.info("chat %s: %s - %s", conv["chat_id"][:8], err.get("code"), str(err.get("message", ""))[:200])
            if r.status_code == 0 and _engine_on() and ENGINE.challenge == "cloudflare":
                err["code"] = "cloudflare"
            if err["code"] == "challenge" and _engine_on():
                ENGINE.request_human_check()
                yield _sse({"type": "challenge", "kind": "recaptcha", "message": "arena.ai wants a quick human check. "
                            "Tick the box in the arena.ai window; your message is resent right after."})
                token = yield from _wait_for(
                    lambda: ENGINE.take_v2_token() or (ENGINE.challenge != "recaptcha" and "closed"), 300)
                if token == "closed":
                    token = ENGINE.take_v2_token()
                LOG.info("chat %s: human check %s", conv["chat_id"][:8], "passed" if token else "not finished")
                if not token:
                    ENGINE.cancel_check()
                    yield _sse({"type": "error", "code": "challenge", "retry": True,
                                "message": "The human check was not finished, so nothing was sent."})
                    return None
                yield _sse({"type": "challenge_done"})
                v2 = token
                continue
            if err["code"] == "cloudflare" and _engine_on():
                if ENGINE.ready():
                    ENGINE.show_cloudflare()
                yield _sse({"type": "challenge", "kind": "cloudflare",
                            "message": "arena.ai is showing a Cloudflare check. Finish it in the arena.ai window."})
                if not (yield from _wait_for(ENGINE.ready, 300)):
                    yield _sse({"type": "error", "code": "cloudflare", "retry": True,
                                "message": "The Cloudflare check was not finished."})
                    return None
                yield _sse({"type": "challenge_done"})
                continue
            if err["code"] == "eval_gone" and not conv["new"]:
                conv.update(new=True, eval_id=None)
                yield _sse({"type": "notice", "text": "arena.ai no longer has this conversation, so a new one was started."})
                continue
            if err["code"] in ("challenge", "cloudflare"):
                err["message"] += " Open Arena Desktop (Arena-Desktop.exe): it shows arena.ai's check so you can finish it."
            yield _sse(dict(err, type="error"))
            return None
        finally:
            permit.release()
    yield _sse({"type": "error", "code": "http", "retry": True, "message": "arena.ai kept refusing the message."})
    return None


def _sse_payload(item):
    """Parse one SSE string back into its event dict (or None)."""
    try:
        text = str(item).strip()
        if text.startswith("data:"):
            return json.loads(text[5:].strip())
    except Exception:
        return None
    return None


def _auto_continue_on() -> bool:
    try:
        return load_config().get("auto_continue", True) is not False
    except Exception:
        return True


def _account_failover_on() -> bool:
    # Opt-in only (default OFF): rotating accounts to work around a daily
    # cap is a fair-use grey zone, so the user enables this knowingly.
    try:
        return load_config().get("account_failover") is True
    except Exception:
        return False


def _failover_target():
    """Another saved account with a session, or None. Also refuses while
    anything besides the calling turn is generating: switching accounts
    re-points the whole app (engine + client)."""
    try:
        with _generation_lock:
            if _active_generations > 1:
                return None
        store = _accounts_load()
        current = store.get("current")
        return next((a for a in store.get("accounts") or []
                     if a.get("id") != current and (a.get("cookie") or "")
                     and not a.get("expired")), None)
    except Exception:
        return None


def _activate_failover(target) -> bool:
    """Perform the actual switch to `target` (blocking; engine import may
    take a few seconds). Mirrors the manual switch endpoint's internals."""
    try:
        try:
            _sync_current_account_cookie(get_client().cookie_str)
        except Exception:
            pass
        _resp, ok = _activate_cookie(target.get("cookie") or "", "account-failover")
        if not ok:
            _mark_account_expired(target.get("id"))
            return False
        _PACER_LANES.clear()  # new account: pacing starts from a clean slate
        return True
    except Exception:
        LOG.exception("account failover failed")
        return False


def _failover_seed(conv, content: str) -> str:
    """First message for the fresh session on the new account: the chat
    digest (same seeding as Compact/Fork) followed by the pending content."""
    try:
        chat = _load_chat(str((conv or {}).get("chat_id") or "")) or {}
        if chat.get("messages"):
            return _digest_block(_build_digest(chat)) + (content or "")
    except Exception:
        pass
    return content


# ----------------------------------------------------------------------
# Agent Gateway (v5.5): selected local MCP servers exposed to the genuine
# Arena Agent through a temporary Cloudflare Quick Tunnel session.
# Master opt-in (gateway_enabled, default OFF) + separate auto-start
# opt-in (gateway_auto) tied to the composer Workspace Sync tools flag.
_GATEWAY = None


def _gateway_tool_index() -> dict:
    try:
        tool_map = CONNECTORS.ensure_started(timeout=10) or {}
    except Exception:
        tool_map = {}
    index: dict = {}
    for alias, entry in tool_map.items():
        server = entry.get("server") or ""
        tool = entry.get("tool") or ""
        if not server or not tool:
            continue
        annotations = entry.get("annotations") or {}
        index.setdefault(server, {"tools": {}})["tools"][tool] = {
            "alias": alias,
            "description": entry.get("description") or "",
            "input_schema": entry.get("input_schema") or {},
            "read_only": annotations.get("readOnlyHint") is True,
        }
    return index


def _gateway_approve(server, tool, read_only, arguments) -> bool:
    # Same verdict machinery as the local tool loop: read-only calls grade
    # as reads, everything else as shell. "ask" waits on the UI prompt.
    permission_tool = "read_file" if read_only else "run_cmd"
    verdict = BROKER.check(_perm_mode(), permission_tool, inside=True)
    if verdict == "allow":
        return True
    if verdict == "blocked":
        return False
    info = {"tool": permission_tool, "summary": f"Agent Gateway MCP {server}: {tool}",
            "gateway": True, "server": server, "mcp_tool": tool, "inside": True,
            "rel": server, "path": json.dumps(arguments or {}, ensure_ascii=False)[:300]}
    pid = BROKER.ask(info)
    LOG.info("gateway: approval requested for %s:%s", server, tool)
    return BROKER.wait(pid, 180) == "allow"


def _gateway():
    global _GATEWAY
    if _GATEWAY is None:
        _GATEWAY = GatewayManager(
            tool_index=_gateway_tool_index,
            call_tool=lambda alias, args: CONNECTORS.call(alias, args),
            approve=_gateway_approve,
            cloudflared_path=components.cloudflared_path,
            logger=LOG)
    return _GATEWAY


def _gateway_enabled(cfg=None) -> bool:
    try:
        return (cfg or load_config()).get("gateway_enabled") is True
    except Exception:
        return False


def _gateway_servers(cfg=None) -> list:
    cfg = cfg or load_config()
    chosen = cfg.get("gateway_servers")
    if isinstance(chosen, list):
        return [str(s) for s in chosen]
    try:
        return [name for name, srv in (connectors.configured_servers() or {}).items()
                if srv.get("enabled") and srv.get("transport", "stdio") == "stdio"]
    except Exception:
        return []


def _gateway_injection(data, cfg=None) -> str:
    """Protocol block for a fresh native agent session (auto mode), or ''."""
    cfg = cfg or load_config()
    if not _gateway_enabled(cfg) or cfg.get("gateway_auto") is not True:
        return ""
    if (data or {}).get("tools") is False:
        return ""  # Workspace Sync is off in the composer
    servers = _gateway_servers(cfg)
    if not servers:
        return ""
    mgr = _gateway()
    if mgr.summary()["state"] != "on":
        mgr.start(servers, wait_seconds=20)
    return mgr.injection_block()


_AUTO_RETRY_CODES = {"network", "server", "model", "stalled", "empty"}
_CONTINUE_PROMPT = ("[Arena Desktop] Your previous reply was interrupted mid-stream. "
                    "Continue exactly from where it stopped. Do not repeat anything "
                    "you already wrote and do not restart the answer.")


def _turn_auto(client, content, conv):
    """_turn with automatic continuation (v5.1).

    If the model call dies (network/5xx, stream stall, lane error or an
    empty reply), the task no longer ends: the call is retried up to 2
    more times. A prose partial is kept and the model is asked to
    continue it; a partial containing tool calls is discarded and the
    same content is resent. Errors of failed attempts are held back and
    only surfaced when no attempts remain. Dual (Battle/Side by Side)
    turns only retry when nothing was produced at all, so a surviving
    lane is never re-rolled.
    """
    dual = conv.get("mode") in ("battle", "side-by-side")
    attempts_left = 2 if _auto_continue_on() else 0
    accumulated = {"a": "", "b": ""}
    send_content = content
    attempt_no = 0
    failed_over = False

    def merged(o):
        if o is None:
            return None
        return {"a": accumulated["a"] + (o.get("a") or ""),
                "b": accumulated["b"] + (o.get("b") or "")}

    while True:
        attempt_no += 1
        held = []  # (raw SSE, payload) errors / stall notice of this attempt
        saw_stall = False
        saw_artifact = False
        out = None
        gen = _turn(client, send_content, conv)
        while True:
            try:
                item = next(gen)
            except StopIteration as stop:
                out = stop.value
                break
            payload = _sse_payload(item) or {}
            ptype = payload.get("type")
            if ptype == "error":
                held.append((item, payload))
                continue
            if ptype == "notice" and "stopped sending data" in str(payload.get("text") or ""):
                saw_stall = True
                held.append((item, payload))
                continue
            if ptype in ("media", "preview", "webdev", "files_written", "files"):
                saw_artifact = True
            yield item

        failed = False
        fail_err = None
        if out is None:
            failed = True
            errs = [p for _i, p in held if p.get("type") == "error"]
            fail_err = errs[-1] if errs else {"code": "http", "retry": False}
        else:
            empty = (not (out.get("a") or "").strip() and not (out.get("b") or "").strip()
                     and not saw_artifact and not conv.get("media_saved"))
            lane_errs = [p for _i, p in held if p.get("type") == "error"]
            if dual:
                if empty:
                    failed = True
                    fail_err = lane_errs[-1] if lane_errs else {"code": "empty", "retry": True}
            elif saw_stall:
                failed, fail_err = True, {"code": "stalled", "retry": True}
            elif lane_errs and empty:
                failed, fail_err = True, lane_errs[-1]
            elif empty and conv.get("modality") in ("chat", "search"):
                failed, fail_err = True, {"code": "empty", "retry": True}

        if not failed:
            for raw_item, payload in held:
                if payload.get("type") == "error":
                    yield raw_item
            return merged(out)

        code = str((fail_err or {}).get("code") or "")
        try:
            wait = float((fail_err or {}).get("wait") or 0)
        except (TypeError, ValueError):
            wait = 0
        # With pacing on, the resend queues precisely until Arena's reset,
        # so a longer rate-limit window is worth auto-continuing; without
        # pacing only short windows are (immediate retries would just 429).
        rl_window = 95 if _pacing_params()["enabled"] else 20
        retryable = code in _AUTO_RETRY_CODES or (code == "rate_limit" and 0 < wait <= rl_window)
        if attempts_left <= 0 or not retryable or _stopped():
            # Daily-scale limit + opt-in account failover (v5.1.5): switch
            # to another saved account and continue in a digest-seeded
            # fresh session instead of dying here. Once per turn.
            target = None
            if (code == "rate_limit" and wait >= 600 and not failed_over
                    and not _stopped() and _account_failover_on()):
                target = _failover_target()
            if target is None:
                for raw_item, _p in held:
                    yield raw_item
                return merged(out)
            label = target.get("email") or target.get("name") or "another account"
            yield _sse({"type": "notice",
                        "text": f"Daily limit reached on this account — switching to {label} and continuing in a fresh session…"})
            if not _activate_failover(target):
                for raw_item, _p in held:
                    yield raw_item
                return merged(out)
            failed_over = True
            _tl.error = None
            client = get_client()
            conv.update(new=True, eval_id=None)
            yield _sse({"type": "account_switched", "email": label})
            yield _sse({"type": "auto_retry", "attempt": attempt_no, "max": 2,
                        "code": "account_switch",
                        "dropped": {"a": len((out or {}).get("a") or ""),
                                    "b": len((out or {}).get("b") or "")}})
            accumulated = {"a": "", "b": ""}
            send_content = _failover_seed(conv, content)
            continue

        attempts_left -= 1
        _tl.error = None  # the held whole-turn error is not final
        partial_a = (out or {}).get("a") or ""
        partial_b = (out or {}).get("b") or ""
        partial = partial_a + partial_b
        dropped = {"a": 0, "b": 0}
        if (partial.strip() and not dual and out is not None
                and "<tool" not in partial and "<invoke" not in partial):
            accumulated["a"] += partial_a
            accumulated["b"] += partial_b
            send_content = _CONTINUE_PROMPT
        else:
            dropped = {"a": len(partial_a), "b": len(partial_b)}
            send_content = content
        yield _sse({"type": "auto_retry", "attempt": attempt_no, "max": 2,
                    "code": code, "dropped": dropped})
        backoff = int(wait) if code == "rate_limit" and wait else 2
        yield from _wait_for(lambda: False, max(1, min(backoff, 20)))
        if _stopped():
            return merged(out)


def _native_connected_slugs():
    try:
        items, error, _ = _fetch_arena_connectors()
    except Exception:
        return []
    if error or not items:
        return []
    try:
        off = {str(slug) for slug in (load_config().get("arena_connectors_off") or [])}
    except Exception:
        off = set()
    return [item["slug"] for item in items
            if item.get("connected") and item.get("slug") and item["slug"] not in off]


def _native_pat(session_id, timezone, start=False):
    """Mint or refresh the Trigger session PAT through Arena. Returns (pat, error)."""
    if start:
        data, error, _ = _arena_post_json(
            contracts.PATHS["chat_trigger_session"],
            {"sessionId": session_id, "timezone": timezone or native_agent.DEFAULT_TIMEZONE},
            timeout=35)
        if not error and isinstance(data, dict) and data.get("publicAccessToken"):
            return data["publicAccessToken"], None
        if error:
            return None, error
    data, error, _ = _arena_post_json(
        contracts.PATHS["chat_trigger_token"], {"sessionId": session_id}, timeout=35)
    if not error and isinstance(data, dict) and data.get("token"):
        return data["token"], None
    return None, error or "arena.ai did not return an agent access token"


def _native_headers(client, base):
    headers = dict(base)
    if client.cookie_str:
        headers["Cookie"] = client.cookie_str
    headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    return headers


def _native_append(client, trigger_address, pat, payload):
    """Append one input record to the agent session. Returns (seq|None, http_status)."""
    try:
        resp = requests.post(
            native_agent.in_url(contracts.ORIGIN, trigger_address),
            headers=_native_headers(client, native_agent.append_headers(pat)),
            json=payload, timeout=30)
    except requests.RequestException:
        return None, 0
    if resp.status_code != 200:
        return None, resp.status_code
    try:
        data = resp.json()
    except ValueError:
        return None, 200
    seq = data.get("seq") if isinstance(data, dict) else None
    return (seq if isinstance(seq, int) else None), 200


def _native_create_session(message, timezone, connectors, files=None):
    """Create the Arena agentic session with a direct API call (like Direct sends).

    Generator: may yield challenge events. Returns (bootstrap|None, error|None).

    The website attaches a ``cx`` client-interaction blob to this request.
    We never synthesize it: ``ENGINE.native_preflight`` asks the page's own
    client-interaction module to compute a genuine one for this submission
    (the same ``a(k())`` pair the site's evaluation submit runs, labelled
    "programmatic" by the site itself).  Creation tries the cx-bearing
    variants first when a blob is available, then cx-less ones, advancing
    only on HTTP 400; every attempt is logged with the response head.
    """
    if not ENGINE.wait_ready(30):
        return None, "arena.ai window is still loading"
    pre = ENGINE.native_preflight(native_agent.RECAPTCHA_V3_ACTION)
    cx = pre.get("cx")
    LOG.info("native create preflight: token_len=%s cx=%s",
             pre.get("token_len"), "genuine" if cx else "unavailable")
    variants = []
    if cx:
        variants += [("genuine-cx+connectors", cx, True), ("genuine-cx", cx, False)]
    variants += [("no-cx+connectors", None, True), ("no-cx", None, False)]
    attempt_headers = {"X-Agent-Browser-Attempt-Id": native_agent.new_uuid()}
    last_error = None
    checks_used = 0  # human checks shown across all variants (keep it humane)
    for label, cx_value, with_connectors in variants:
        v2_token = None
        rounds = 0
        while True:
            body = native_agent.build_create_body(
                message, timezone, connectors if with_connectors else None,
                v2_token=v2_token, cx=cx_value, files=files)
            v2_token = None
            if not ENGINE.wait_ready(30):
                return None, "arena.ai window is still loading"
            r = ENGINE.request(contracts.PATHS["create_chat"], body=body, method="POST",
                               timeout=120, v3_action=native_agent.RECAPTCHA_V3_ACTION,
                               headers=attempt_headers)
            text = r.text
            LOG.info("native create [%s]: HTTP %s %s", label, r.status_code,
                     " ".join((text or "").split())[:300])
            if r.status_code == 200:
                try:
                    payload = json.loads(text)
                except ValueError:
                    return None, "arena.ai returned an unexpected create-chat reply"
                boot = native_agent.parse_bootstrap(payload)
                if not boot["session_id"]:
                    return None, "arena.ai did not return an agent session id"
                LOG.info("native create [%s]: session %s", label, boot["session_id"])
                return boot, None
            err = contracts.classify_error(r.status_code, text if r.status_code else (r.error or ""),
                                           headers=getattr(r, "headers", None))
            if r.status_code == 0 and ENGINE.challenge == "cloudflare":
                err["code"] = "cloudflare"
            last_error = err.get("message") or f"HTTP {r.status_code}"
            rounds += 1
            if rounds > 2:
                break
            if err["code"] in ("challenge", "forbidden") and checks_used < 2:
                checks_used += 1
                ENGINE.request_human_check()
                yield _sse({"type": "challenge", "kind": "recaptcha",
                            "message": "arena.ai wants a quick human check. Tick the box in the "
                                       "arena.ai window; your message is resent right after."})
                token = yield from _wait_for(
                    lambda: ENGINE.take_v2_token() or (ENGINE.challenge != "recaptcha" and "closed"), 300)
                if token == "closed":
                    token = ENGINE.take_v2_token()
                if not token:
                    ENGINE.cancel_check()
                    return None, "The human check was not finished, so nothing was sent."
                yield _sse({"type": "challenge_done"})
                v2_token = token
                continue
            if err["code"] == "cloudflare":
                if ENGINE.ready():
                    ENGINE.show_cloudflare()
                yield _sse({"type": "challenge", "kind": "cloudflare",
                            "message": "arena.ai is showing a Cloudflare check. Finish it in the arena.ai window."})
                if not (yield from _wait_for(ENGINE.ready, 300)):
                    return None, "The Cloudflare check was not finished."
                yield _sse({"type": "challenge_done"})
                continue
            break  # validation/auth/rate-limit: try the next body variant
    return None, last_error or "arena.ai kept refusing the agent session"


def _native_refresh_cursor(session_id):
    """Authoritative .out cursor from Arena's session snapshot.

    The website resumes a session exactly this way: GET /api/chat/{id}
    returns {messages, lastEventId, ...}.  Using the server cursor avoids
    both replaying the backlog and draining it record by record (draining
    an idle long-poll subscription is what made old chats look frozen).
    Best-effort: returns None on any problem.
    """
    data, error, _ = _arena_get_json(
        contracts.PATHS["chat_session"].format(
            session_id=urllib.parse.quote(str(session_id))), timeout=20)
    if error or not isinstance(data, dict):
        return None
    return history_sync.agentic_last_event_id(data)


def _iter_records_idle(resp, idle_seconds, should_stop=None):
    """Yield stream records, ending the cycle after `idle_seconds` of
    silence (or at once when should_stop() fires).

    The realtime endpoint may hold a caught-up subscription open with
    keepalives; a plain iter_lines loop would block the whole subscribe
    cycle — and the Stop button — on it.  Reading in a daemon thread lets
    the caller enforce an idle bound and react to Stop within a second.
    """
    q: "queue.Queue" = queue.Queue()

    def reader():
        try:
            for record in native_agent.iter_sse_records(resp.iter_lines(decode_unicode=True)):
                q.put(record)
        except Exception:
            pass
        q.put(None)

    threading.Thread(target=reader, daemon=True).start()
    idle = 0.0
    while True:
        if should_stop is not None and should_stop():
            return
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            idle += 1.0
            if idle >= idle_seconds:
                return
            continue
        if item is None:
            return
        idle = 0.0
        yield item


def _native_drain_cursor(client, session_id, trigger_address, pat, timezone, max_cycles=3):
    """Advance past the backlog of an imported session's .out stream.

    Returns (last_event_id, pat). Subscribes without a cursor and discards
    records until a cycle brings nothing new, so the next turn streams only
    fresh output. Best-effort: any failure simply returns what was
    collected — a partly replayed backlog is annoying, a blocked chat
    would be worse.
    """
    cursor = None
    for _ in range(max_cycles):
        headers = native_agent.stream_headers(pat, cursor)
        headers["Timeout-Seconds"] = "20"  # draining must stay quick
        try:
            resp = requests.get(
                native_agent.out_url(contracts.ORIGIN, trigger_address),
                headers=_native_headers(client, headers),
                stream=True, timeout=(15, 60))
        except requests.RequestException:
            break
        if resp.status_code in (401, 403):
            resp.close()
            pat, _ = _native_pat(session_id, timezone, start=False)
            if not pat:
                break
            continue
        if resp.status_code != 200:
            resp.close()
            break
        got = False
        try:
            for record in _iter_records_idle(resp, 15, _stopped):
                got = True
                if record.get("id"):
                    cursor = record["id"]
                new_pat = native_agent.refreshed_pat(record)
                if new_pat:
                    pat = new_pat
        except requests.RequestException:
            pass
        finally:
            resp.close()
        if not got:
            break  # an empty cycle means we are at the stream head
    return cursor, pat


def _native_agent_run(client, message: str, existing: dict, timezone, result=None, files=None):
    """Run one native Arena Agent turn. Yields SSE events; returns a result dict.

    The PAT lives only in memory; the persisted state is
    {backend, session_id, trigger_address, last_event_id, needs_drain}.

    ``result`` may be supplied by the caller: it is mutated as the turn
    progresses (native coordinates the moment they are known, the stream
    cursor on every record) so the caller can persist the chat from a
    finally-block even when the generator is closed early — a Stop or a
    disconnected client must never lose the session, or the next message
    would start a brand-new chat instead of continuing this one.
    """
    if result is None:
        result = {"text": "", "reasoning": "", "tools": [], "files": [], "native": None, "error": None}
    nat = (existing or {}).get("native") or {}
    session_id = nat.get("session_id")
    trigger_address = nat.get("trigger_address")
    last_event_id = nat.get("last_event_id")
    needs_drain = bool(nat.get("needs_drain"))
    connectors = _native_connected_slugs()
    pat = None

    def publish_native():
        if session_id:
            result["native"] = {
                "backend": "arena",
                "session_id": session_id,
                "trigger_address": trigger_address,
                "last_event_id": last_event_id,
                "needs_drain": needs_drain,
                # Workspace Sync file-bridge state survives across turns.
                "file_sync_ok": nat.get("file_sync_ok"),
                "synced_files": nat.get("synced_files") or {},
            }

    def fail(message_text, code="native_agent", retry=True):
        result["error"] = message_text
        return _sse({"type": "error", "code": code, "retry": retry, "message": message_text})

    if not session_id:
        if not _engine_on():
            yield fail("Native Arena Agent works in the desktop app: "
                       "creating a session needs the arena.ai window.", code="engine_required", retry=False)
            return result
        boot, error = yield from _native_create_session(message, timezone, connectors, files=files)
        if error:
            yield fail(error)
            return result
        session_id = boot["session_id"]
        trigger_address = boot["trigger_address"]
        pat = boot["pat"]
        needs_drain = False
        publish_native()  # coordinates must survive even an immediate Stop
        if not trigger_address:
            data, gerr, _ = _arena_get_json(
                contracts.PATHS["chat_session"].format(
                    session_id=urllib.parse.quote(str(session_id))), timeout=35)
            if not gerr and isinstance(data, dict):
                trigger_address = data.get("triggerAddress")
            trigger_address = trigger_address or session_id
        if not pat:
            pat, perr = _native_pat(session_id, timezone, start=True)
            if not pat:
                yield fail(perr or "Could not get an agent session token.")
                return result
        publish_native()  # trigger_address may have been resolved above
    else:
        if not trigger_address:
            trigger_address = session_id
        publish_native()
        if not _engine_on():
            yield fail("Native Arena Agent works in the desktop app: "
                       "sending needs the arena.ai window.", code="engine_required", retry=False)
            return result
        # Direct append, like the website's transport: a fresh genuine cx in
        # the turn metadata when the page can mint one, plain otherwise.
        pre = ENGINE.native_preflight(native_agent.RECAPTCHA_V3_ACTION)
        pat, perr = _native_pat(session_id, timezone, start=True)
        if not pat:
            yield fail(perr or "Could not get an agent session token.")
            return result
        if needs_drain or not last_event_id:
            # Old/imported session: resume the way the website does — take
            # the authoritative cursor from the session snapshot. Only if
            # the snapshot has none do we drain the backlog (bounded).
            yield _sse({"type": "agent", "stage": "Continuing the existing Arena session…"})
            fresh = _native_refresh_cursor(session_id)
            if fresh:
                last_event_id, needs_drain = fresh, False
                publish_native()
                LOG.info("native resume: cursor from session snapshot (%s)", fresh)
        if needs_drain and not last_event_id:
            # Imported cloud session: its .out stream still holds the whole
            # past transcript. Skip to the current head first, so old
            # records are not replayed into the new reply.
            last_event_id, pat = _native_drain_cursor(
                client, session_id, trigger_address, pat, timezone)
            needs_drain = False
            publish_native()
            LOG.info("native resume: drained imported session to cursor %s", last_event_id)
        payload = native_agent.build_append_body(message, trigger_address, timezone,
                                                 connectors, cx=pre.get("cx"), files=files)
        _, status = _native_append(client, trigger_address, pat, payload)
        if status in (401, 403):
            pat, _ = _native_pat(session_id, timezone, start=False)
            if pat:
                _, status = _native_append(client, trigger_address, pat, payload)
        if status == 404:
            pat, _ = _native_pat(session_id, timezone, start=True)
            if pat:
                _, status = _native_append(client, trigger_address, pat, payload)
        LOG.info("native append: HTTP %s (cx=%s)", status, "genuine" if pre.get("cx") else "none")
        if status != 200:
            yield fail(f"Could not send the message to the agent (HTTP {status}).")
            return result

    yield _sse({"type": "agent", "stage": "Arena Agent · session active"})
    tool_names: dict = {}
    tool_entries: dict = {}
    turn_done = False
    saw_record = False
    last_activity = time.monotonic()
    deadline = time.monotonic() + 1800
    cycles = 0
    while not turn_done and cycles < 24 and time.monotonic() < deadline:
        cycles += 1
        if _stopped():
            _native_append(client, trigger_address, pat, native_agent.STOP_BODY)
            yield _sse({"type": "notice", "text": "Stopped."})
            break
        try:
            resp = requests.get(
                native_agent.out_url(contracts.ORIGIN, trigger_address),
                headers=_native_headers(client, native_agent.stream_headers(pat, last_event_id)),
                stream=True, timeout=(15, 150))
        except requests.RequestException:
            time.sleep(2)
            continue
        if resp.status_code in (401, 403):
            resp.close()
            pat, _ = _native_pat(session_id, timezone, start=False)
            if not pat:
                yield fail("The agent session token expired and did not refresh.")
                break
            continue
        if resp.status_code == 404:
            resp.close()
            pat, _ = _native_pat(session_id, timezone, start=True)
            if not pat:
                yield fail("The agent session was not found on Arena.")
                break
            continue
        if resp.status_code != 200:
            status = resp.status_code
            resp.close()
            if cycles >= 3:
                yield fail(f"The agent stream is unavailable (HTTP {status}).")
                break
            time.sleep(2)
            continue
        settled = resp.headers.get("X-Session-Settled") == "true"
        version = resp.headers.get("X-Stream-Version") or "v1"
        got_record = False
        cycle_records = 0
        try:
            for record in _iter_records_idle(resp, 120, _stopped):
                got_record = True
                saw_record = True
                last_activity = time.monotonic()
                cycle_records += 1
                if record.get("id"):
                    last_event_id = record["id"]
                    if result.get("native"):
                        result["native"]["last_event_id"] = last_event_id
                new_pat = native_agent.refreshed_pat(record)
                if new_pat:
                    pat = new_pat
                chunk = record.get("chunk")
                if native_agent.is_turn_complete_record(record):
                    LOG.info("native stream: turn-complete record received")
                    turn_done = True
                    break
                if native_agent.is_control(chunk):
                    continue
                for event in native_agent.map_chunk(chunk, tool_names):
                    if event["type"] == "text":
                        result["text"] += event["text"]
                    elif event["type"] == "reasoning":
                        result["reasoning"] += event["text"]
                    elif event["type"] == "tool_call":
                        entry = {"tool": event["tool"], "summary": event["summary"],
                                 "lane": "a", "diff": None, "ok": True, "status": "running"}
                        result["tools"].append(entry)
                        if event.get("id"):
                            tool_entries[event["id"]] = entry
                    elif event["type"] == "tool_result":
                        entry = tool_entries.get(event.get("id") or "")
                        if entry is not None:
                            entry["ok"] = event["ok"]
                            entry["status"] = event["status"]
                    elif event["type"] == "media" and event.get("url"):
                        known = {item["url"] for item in result["files"]}
                        if event["url"] not in known:
                            result["files"].append({
                                "url": event["url"],
                                "title": event.get("title") or "Agent file",
                                "kind": event.get("kind") or "file",
                            })
                    yield _sse(event)
                if _stopped():
                    _native_append(client, trigger_address, pat, native_agent.STOP_BODY)
                    yield _sse({"type": "notice", "text": "Stopped."})
                    turn_done = True
                    break
        except requests.RequestException:
            pass  # resubscribe from the cursor below
        finally:
            resp.close()
        LOG.info("native stream: cycle %s version=%s settled=%s records=%s done=%s",
                 cycles, version, settled, cycle_records, turn_done)
        if not turn_done and settled and not got_record:
            if saw_record:
                # The session reports itself settled (nothing running) and
                # no new records arrived: the turn is over even if the
                # explicit turn-complete record was missed. The records are
                # durable and the cursor is saved, so nothing is lost.
                LOG.info("native stream: settled after activity; ending the turn")
                turn_done = True
            else:
                time.sleep(2)  # the run may still be booting
        if not turn_done and not saw_record and time.monotonic() - last_activity > 360:
            # Six minutes without a single record: do not spin subscribe
            # cycles for half an hour (the "old chat hangs forever" look).
            # The session state is saved by the caller either way.
            yield fail("The agent has not started responding in this session — the previous turn may still be "
                       "running on Arena. The session is saved; try again a little later.")
            break
    if not turn_done and not _stopped() and result["error"] is None and cycles >= 24:
        yield fail("The agent did not finish the turn in time. The session is saved — "
                   "the next message will pick it up.")
    publish_native()
    return result


def _native_file_bridge(files, native_state):
    """Workspace Sync for the native Arena Agent: download the files it
    created in Arena's cloud workspace into the folder picked on this
    computer. One-way (never deletes), hash-deduped across turns, one
    permission decision per chat. Yields SSE events; returns names written."""
    written = []
    if not files:
        return written
    root = _chosen_ws()
    if root is None:
        names = ", ".join(str(f.get("title") or "file") for f in files[:3])
        yield _sse({"type": "notice", "model": "a",
                    "text": f"Arena Agent created file(s): {names}. Choose a folder on this "
                            "computer to have them downloaded automatically (Workspace Sync)."})
        return written
    if native_state.get("file_sync_ok") is None:
        info = {"tool": "write_file", "inside": True, "path": str(root),
                "summary": f"Download {len(files)} file(s) from Arena Agent into {root.name}"}
        native_state["file_sync_ok"] = (yield from _permit(_perm_mode(), "write_file", info)) == "allow"
    if not native_state.get("file_sync_ok"):
        return written
    synced = dict(native_state.get("synced_files") or {})
    for item in files:
        name = Path(str(item.get("title") or "")).name
        if not name or name in (".", ".."):
            name = "agent-file"
        try:
            r = requests.get(item["url"], timeout=120)
            r.raise_for_status()
            content = r.content
        except (requests.RequestException, OSError) as e:
            yield _sse({"type": "notice", "model": "a",
                        "text": f"Could not download {name} from Arena Agent: {e}"})
            continue
        if len(content) > 50 * 1024 * 1024:
            yield _sse({"type": "notice", "model": "a",
                        "text": f"{name} is over 50 MB and was not downloaded."})
            continue
        digest = hashlib.sha256(content).hexdigest()
        target = root / name
        if synced.get(name) == digest and target.exists():
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        except OSError as e:
            yield _sse({"type": "notice", "model": "a",
                        "text": f"Could not save {name}: {e}"})
            continue
        synced[name] = digest
        written.append(name)
    native_state["synced_files"] = synced
    if written:
        yield _sse({"type": "files_written", "model": "a", "root": str(root),
                    "written": written, "deleted": []})
        yield _sse({"type": "notice", "model": "a",
                    "text": f"Saved {len(written)} file(s) from Arena Agent to {root}"})
    return written


_ckpt_lock = threading.Lock()


def _invalidate_read_cache(conv, info):
    conv.pop("_output_cache", None)  # listings/searches may have changed too
    cache = conv.get("_read_cache")
    if not cache:
        return
    target = str(info.get("path") or "")
    for key in [k for k in cache if k and k[0] == target]:
        cache.pop(key, None)


def _checkpoint_capture(conv, lane, info):
    """Snapshot files before the agent overwrites/deletes them (once per turn).

    Snapshots land in <base>/.arena-artifacts/checkpoints/<id>/ with a
    manifest; the reply carries the id so the UI can offer one-click Undo.
    Best-effort: any failure just means no undo for that file.
    """
    try:
        target = Path(info.get("path") or "")
        rel = str(info.get("rel") or "")
        if not rel or rel == "." or not str(target):
            return
        with _ckpt_lock:
            state = conv.get(f"_checkpoint_{lane}")
            if state is None:
                ck_id = time.strftime("%Y%m%d-%H%M%S") + "-" + generate_uuidv7()[:8]
                parts = [pt for pt in Path(rel).parts if pt not in (".", "/")]
                base = target
                if all(pt != ".." for pt in parts):
                    for _ in parts:
                        base = base.parent
                else:
                    base = target.parent
                ck_dir = base / ".arena-artifacts" / "checkpoints" / ck_id
                (ck_dir / "files").mkdir(parents=True, exist_ok=True)
                state = {"id": ck_id, "dir": str(ck_dir), "entries": [], "seen": []}
                conv[f"_checkpoint_{lane}"] = state
            targets = []
            if target.is_dir():
                for fp in target.rglob("*"):
                    if fp.is_file():
                        targets.append(fp)
                    if len(targets) >= 200:
                        break
            else:
                targets = [target]
            total = sum(int(e.get("size") or 0) for e in state["entries"])
            for fp in targets:
                key = str(fp)
                if key in state["seen"]:
                    continue
                state["seen"].append(key)
                entry = {"abs": key, "existed": fp.is_file(), "store": None}
                if entry["existed"]:
                    try:
                        size = fp.stat().st_size
                    except OSError:
                        size = 0
                    if total + size <= 20 * 1024 * 1024:
                        store = f"f{len(state['entries'])}"
                        try:
                            (Path(state["dir"]) / "files" / store).write_bytes(fp.read_bytes())
                            entry.update(store=store, size=size)
                            total += size
                        except OSError:
                            entry["skipped"] = "unreadable"
                    else:
                        entry["skipped"] = "checkpoint size cap"
                state["entries"].append(entry)
            manifest = {"id": state["id"], "lane": lane, "chat_id": conv.get("chat_id"),
                        "created": int(time.time()), "entries": state["entries"]}
            (Path(state["dir"]) / "manifest.json").write_text(
                json.dumps(manifest, indent=1), encoding="utf-8")
    except Exception:
        LOG.exception("checkpoint capture failed")


@app.route("/api/checkpoints/<cid>/restore", methods=["POST"])
def api_checkpoint_restore(cid):
    """Undo one agent turn's file changes from its checkpoint snapshot."""
    if not re.fullmatch(r"[A-Za-z0-9-]{6,80}", cid or ""):
        return jsonify({"error": "Bad checkpoint id", "code": "bad_request"}), 400
    ws = _chosen_ws()
    manifest = ck_dir = None
    if ws:
        root = Path(ws)
        for base in (root, root / "option-a", root / "option-b"):
            mpath = base / ".arena-artifacts" / "checkpoints" / cid / "manifest.json"
            if mpath.is_file():
                try:
                    manifest = json.loads(mpath.read_text(encoding="utf-8"))
                    ck_dir = mpath.parent
                except (OSError, ValueError):
                    manifest = None
                break
    if not manifest or ck_dir is None:
        return jsonify({"error": "Checkpoint not found", "code": "gone"}), 404
    restored = removed = 0
    for entry in manifest.get("entries") or []:
        dest = Path(str(entry.get("abs") or ""))
        if not dest.is_absolute():
            continue
        try:
            if entry.get("existed") and entry.get("store"):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ck_dir / "files" / str(entry["store"])).read_bytes())
                restored += 1
            elif not entry.get("existed") and dest.is_file():
                dest.unlink()
                removed += 1
        except OSError:
            LOG.exception("checkpoint restore failed for %s", dest)
    return jsonify({"ok": True, "restored": restored, "removed": removed})


def _delegate_is_research(call) -> bool:
    params = call.get("params") or {}
    return str(params.get("mode") or "research").strip().lower() != "work" or _perm_mode() == "plan"


def _parallel_partner(lane_calls, call, precomputed):
    """Next un-run research delegate_task in this round (for paired execution)."""
    if not _delegate_is_research(call):
        return None
    seen = False
    for other in lane_calls:
        if other is call:
            seen = True
            continue
        if seen and other.get("name") == "delegate_task" and id(other) not in precomputed \
                and _delegate_is_research(other):
            return other
    return None


def _fresh_client(client):
    """Independent ArenaClient for a worker thread (Sessions are not thread-safe)."""
    try:
        clone = ArenaClient(cookie_str=getattr(client, "cookie_str", None))
        clone.session.cookies.update(client.session.cookies)
        clone.active_mode = client.active_mode
        clone.active_modality = client.active_modality
        return clone
    except Exception:
        LOG.exception("client clone failed; sharing the parent client")
        return client


def _run_parallel_research(client, conv, call, partner, lane, child_id, precomputed):
    """Run two read-only sub-agents concurrently; yields SSE events.

    Returns the current call's (ok, status, output, extra); the partner's
    result is stashed in precomputed for the loop to consume when it reaches
    that call.  Work-mode children never come here: writes stay sequential.
    """
    partner_child = generate_uuidv7()
    ptask = str((partner.get("params") or {}).get("task") or "").strip()
    pinfo = {"tool": "delegate_task", "model": lane, "name": "Sub-agent",
             "summary": "Sub-agent: " + (" ".join(ptask.split())[:120] or "task"),
             "child_id": partner_child, "inside": True, "path": ptask[:400]}
    yield _sse(dict(pinfo, type="tool_call"))
    events_q: queue.Queue = queue.Queue()
    results: dict = {}
    parent_tl = {"chat_id": getattr(_tl, "chat_id", None),
                 "stop_event": getattr(_tl, "stop_event", None),
                 "perm_override": getattr(_tl, "perm_override", None)}

    def drive(key, c, cid):
        for k, v in parent_tl.items():
            setattr(_tl, k, v)
        _tl.error = None
        try:
            gen = _run_subagent(_fresh_client(client), conv, c, lane, cid)
            while True:
                try:
                    events_q.put(next(gen))
                except StopIteration as stop:
                    results[key] = stop.value
                    break
        except Exception as e:
            LOG.exception("parallel sub-agent failed")
            results[key] = (False, "error", f"{type(e).__name__}: {e}", {"child_id": cid})
        finally:
            events_q.put(None)

    threads = [threading.Thread(target=drive, args=("a", call, child_id), daemon=True),
               threading.Thread(target=drive, args=("b", partner, partner_child), daemon=True)]
    for t in threads:
        t.start()
    done = idle = 0
    while done < 2:
        try:
            ev = events_q.get(timeout=1)
        except queue.Empty:
            idle += 1
            if idle % 10 == 0:
                yield _sse({"type": "ping"})
            continue
        if ev is None:
            done += 1
        else:
            yield ev
    for t in threads:
        t.join(timeout=5)
    ok_b, status_b, output_b, extra_b = results.get("b") or (
        False, "error", "sub-agent produced no result", {"child_id": partner_child})
    precomputed[id(partner)] = {
        "ok": ok_b, "status": status_b, "output": output_b,
        "step": {"tool": "delegate_task", "summary": pinfo["summary"], "lane": lane,
                 "diff": None, "ok": ok_b, "status": status_b,
                 "child_id": extra_b.get("child_id"), "model": extra_b.get("model"),
                 "mode": extra_b.get("mode")}}
    return results.get("a") or (False, "error", "sub-agent produced no result", {"child_id": child_id})



def _run_children_pair(client, conv: dict, jobs: list, lane: str):
    """Drive two _run_subagent children concurrently, streaming both.

    jobs: exactly two (call, child_id) pairs.  Returns a 2-list of the
    children's (ok, status, output, extra) results, aligned with jobs.
    Mirrors _run_parallel_research's drive loop (fresh client per child,
    thread-local state copied in, events through a queue).
    """
    events_q: queue.Queue = queue.Queue()
    results: dict = {}
    parent_tl = {"chat_id": getattr(_tl, "chat_id", None),
                 "stop_event": getattr(_tl, "stop_event", None),
                 "perm_override": getattr(_tl, "perm_override", None)}

    def drive(index, c, cid):
        for k, v in parent_tl.items():
            setattr(_tl, k, v)
        _tl.error = None
        try:
            gen = _run_subagent(_fresh_client(client), conv, c, lane, cid)
            while True:
                try:
                    events_q.put(next(gen))
                except StopIteration as stop:
                    results[index] = stop.value
                    break
        except Exception as e:
            LOG.exception("parallel fan-out child failed")
            results[index] = (False, "error", f"{type(e).__name__}: {e}", {"child_id": cid})
        finally:
            events_q.put(None)

    threads = [threading.Thread(target=drive, args=(i, jobs[i][0], jobs[i][1]), daemon=True)
               for i in range(2)]
    for t in threads:
        t.start()
    finished = 0
    while finished < 2:
        item = events_q.get()
        if item is None:
            finished += 1
            continue
        yield item
    for t in threads:
        t.join(timeout=5)
    return [results.get(i, (False, "error", "", {})) for i in range(2)]


def _fanout_run(client, message: str, conv: dict, fanout_models: list):
    """Ask several models the same prompt, then summarize the answers.

    Each model answers in its own child chat nested under this one; this
    chat's reply composes all answers plus a short comparison written by
    one more child on Arena's automatic model.  Yields SSE; returns out.
    """
    answers = []
    models = fanout_models[:4]
    i = 0
    while i < len(models) and not _stopped():
        pair = models[i:i + 2]
        i += 2
        jobs, infos = [], []
        for mid in pair:
            child_id = generate_uuidv7()
            call = {"name": "delegate_task",
                    "params": {"task": message, "model": mid, "mode": "research", "fanout": True}}
            info = {"tool": "delegate_task", "model": "a", "name": "Sub-agent",
                    "summary": "Asking " + (_model_name(mid) or "a model"),
                    "child_id": child_id, "inside": True, "path": message[:400]}
            jobs.append((call, child_id))
            infos.append((mid, info))
            yield _sse(dict(info, type="tool_call"))
        if len(jobs) == 2:  # two models at a time - same Arena-friendly cap
            results = yield from _run_children_pair(client, conv, jobs, "a")
        else:
            results = [(yield from _run_subagent(client, conv, jobs[0][0], "a", jobs[0][1]))]
        for (mid, info), (ok, status, output, extra) in zip(infos, results):
            conv.setdefault("tools", []).append(
                {"tool": "delegate_task", "summary": info["summary"], "lane": "a", "diff": None,
                 "ok": ok, "status": status, "child_id": extra.get("child_id"),
                 "model": extra.get("model"), "mode": extra.get("mode")})
            yield _sse({"type": "tool_result", "model": "a", "tool": "delegate_task",
                        "child_id": extra.get("child_id"), "ok": ok, "status": status,
                        "output": (output or "")[:4000]})
            answers.append((extra.get("model") or _model_name(mid) or "Model", output or ""))
    if not answers:
        return {"a": "", "b": ""}
    parts = [f"**{name}**\n\n{(text or '').strip() or '(no answer)'}" for name, text in answers]
    summary = ""
    if len(answers) > 1 and not _stopped():
        child_id = generate_uuidv7()
        blob = "\n\n".join(f"--- Answer from {n} ---\n{(t or '')[:6000]}" for n, t in answers)
        task = ("Compare the answers below to the same user request. Write a brief synthesis: "
                "where they agree, where they differ, and which points look most reliable. "
                "Do not repeat the answers.\n\nUser request:\n" + message + "\n\n" + blob)
        call = {"name": "delegate_task",
                "params": {"task": task, "mode": "research", "fanout": True}}
        info = {"tool": "delegate_task", "model": "a", "name": "Sub-agent",
                "summary": "Comparing the answers", "child_id": child_id,
                "inside": True, "path": task[:400]}
        yield _sse(dict(info, type="tool_call"))
        ok, status, output, extra = yield from _run_subagent(client, conv, call, "a", child_id)
        conv.setdefault("tools", []).append(
            {"tool": "delegate_task", "summary": info["summary"], "lane": "a", "diff": None,
             "ok": ok, "status": status, "child_id": extra.get("child_id"),
             "model": extra.get("model"), "mode": extra.get("mode")})
        yield _sse({"type": "tool_result", "model": "a", "tool": "delegate_task",
                    "child_id": extra.get("child_id"), "ok": ok, "status": status,
                    "output": (output or "")[:4000]})
        summary = (output or "").strip() if ok else ""
    composed = "\n\n---\n\n".join(parts)
    if summary:
        composed += "\n\n---\n\n**Summary**\n\n" + summary
    yield _sse({"type": "text", "model": "a", "text": composed})
    return {"a": composed, "b": ""}

MAX_SUBAGENTS = 3
_subagent_lock = threading.Lock()


def _delegate_models() -> list:
    """[(id, name)] of text models a parent model may pick for a sub-agent."""
    try:
        ids = _allowed_model_ids("direct", "text")
    except Exception:
        ids = set()
    return [(mid, _model_name(mid) or mid) for mid in sorted(ids)[:14]]


_LIGHT_MODEL_TOKENS = ("mini", "flash", "lite", "haiku", "nano")


def _light_model(mid, name) -> bool:
    """Heuristic 'small/fast tier' test - such models usually burn much
    less of Arena's daily balance than frontier flagships."""
    blob = f"{mid} {name}".lower()
    return any(tok in blob for tok in _LIGHT_MODEL_TOKENS)


def _cheap_subagents_on() -> bool:
    try:
        return load_config().get("cheap_subagents", True) is not False
    except Exception:
        return True


def _resolve_delegate_model(raw, mode="research") -> tuple:
    """Resolve a parent's <model> choice to (model_id, display_name, auto)."""
    text = str(raw or "").strip()
    if text:
        low = text.lower()
        models = _delegate_models()
        for mid, name in models:
            if low == str(mid).lower() or low == name.lower():
                return mid, name, False
        for mid, name in models:
            if low in name.lower() or low in str(mid).lower():
                return mid, name, False
    if str(mode or "research").lower() != "work" and _cheap_subagents_on():
        # Research grunt work on a light model by default (v5.1.5): the
        # parent can always name a stronger model explicitly.
        for mid, name in _delegate_models():
            if _light_model(mid, name):
                return mid, name, True
    auto = _auto_agent_model()
    if auto:
        mid = auto.get("model_id", auto.get("id"))
        return mid, (_model_name(mid) or auto.get("name") or "Auto"), True
    return None, "Auto", True


def _run_subagent(client, parent_conv: dict, call: dict, lane: str, child_id: str):
    """Run one delegated sub-agent in its own child chat.

    Yields SSE events for the parent stream (permissions pass through so the
    user can answer them in the parent chat; the child's own text stays in
    the child chat) and returns (ok, status, output, extra) where output is
    the child's final summary \u2014 the only thing the parent model sees.
    Children run sequentially on the parent's thread, so the parent's Stop
    event and permission chat tagging apply to them as well.
    """
    params = call.get("params") or {}
    task = str(params.get("task") or params.get("prompt") or "").strip()
    extra = {"child_id": child_id}
    if not task:
        return False, "error", "delegate_task needs a <task> describing the sub-task.", extra
    smode = str(params.get("mode") or "research").strip().lower()
    if smode not in ("research", "work") or _perm_mode() == "plan":
        smode = "research"
    model_id, model_name, auto = _resolve_delegate_model(
        params.get("model"), "work" if params.get("fanout") else params.get("mode"))
    extra.update({"model": model_name, "mode": smode, "task": task[:300]})
    with _subagent_lock:
        ran = int(parent_conv.get("subagents_run") or 0)
        if ran >= MAX_SUBAGENTS and not (call.get("params") or {}).get("fanout"):
            return False, "error", (f"Sub-agent limit reached ({MAX_SUBAGENTS} per turn). "
                                    "Finish the work yourself or ask the user to continue."), extra
        parent_conv["subagents_run"] = ran + 1
    if params.get("fanout"):
        intro = ("[Arena Desktop] Answer the user's request below directly and completely. "
                 "Your full answer is shown to the user next to other models' answers, so do "
                 "not summarize for a main agent and do not mention being a sub-agent.\n")
    else:
        intro = ("[Arena Desktop] You are a sub-agent handling a delegated task for the main agent "
                 "in a parent chat. Work only on the task below. When you are done, finish with a "
                 "compact structured summary: what you did or found, the files involved (paths and "
                 "line references where useful), and any open questions. Only that summary is "
                 "returned to the main agent.\n")
    if smode == "research":
        intro += "You are read-only: explore and report, do not modify anything.\n"
    child_message = intro + "\nTask:\n" + task
    title = ("Fan-out: " if params.get("fanout") else "Sub-agent: ") + " ".join(task.split())[:44]
    child_history = [{"role": "user", "text": child_message}]
    child_conv = {"eval_id": None, "new": True, "mode": contracts.api_mode("direct"),
                  "modality": contracts.api_modality("text"), "model_a": model_id,
                  "model_b": None, "chat_id": child_id, "auto_model": bool(auto),
                  "reasoning": {"a": "", "b": ""}, "lane_errors": {"a": [], "b": []},
                  "attachments": None, "guide_sent": False, "mcp_guide_hash": None,
                  "memory_hash": None, "title": title, "tools": [],
                  "allow_delegate": False, "delegate_sent": True,
                  "allowed_tools": (set(agent.READ_ONLY_TOOLS) if smode == "research" else None)}
    prefix = ""
    if _memory_enabled():
        block, _hash = _memory_block(_chosen_ws())
        prefix = block or ""
    _generation_started(child_id)
    prev_error = getattr(_tl, "error", None)
    out = {"a": "", "b": ""}
    summary = ""
    child_error = None
    try:
        _save_chat(child_id, title=title, messages=list(child_history), mode="direct",
                   modality="text", model_a=model_id, model_b=None, eval_id=None,
                   parent_id=parent_conv.get("chat_id"))
        yield _sse({"type": "subagent", "status": "started", "child_id": child_id,
                    "task": task[:300], "model_name": model_name, "mode": smode, "lane": lane})
        gen = _agent_run(client, prefix + child_message, child_conv)
        deadline = time.time() + CHILD_TIME_BUDGET
        while True:
            if time.time() > deadline:
                child_error = f"time budget exceeded ({CHILD_TIME_BUDGET // 60} min)"
                try:
                    gen.close()
                except Exception:
                    pass
                break
            try:
                ev = next(gen)
            except StopIteration as stop:
                out = stop.value or out
                break
            if not isinstance(ev, str) or not ev.startswith("data: "):
                continue
            try:
                parsed = json.loads(ev[6:])
            except ValueError:
                continue
            etype = parsed.get("type")
            if etype in ("permission", "permission_done", "ping", "question", "question_done", "todos"):
                yield ev
            elif etype == "notice":
                yield _sse(dict(parsed, subagent=child_id))
            elif etype == "tool_call":
                yield _sse({"type": "subagent", "status": "tool", "child_id": child_id, "lane": lane,
                            "summary": parsed.get("summary") or parsed.get("tool") or ""})
            elif etype == "error":
                child_error = parsed.get("message") or child_error
            # text / reasoning / tool_result / round stay inside the child chat
    except Exception as e:
        child_error = f"{type(e).__name__}: {e}"
        LOG.exception("sub-agent run failed")
    finally:
        _tl.error = prev_error
        summary = agent.strip_tool_calls(out.get("a") or "").strip()
        reply = {"role": "assistant", "text": summary, "text_b": "",
                 "names": {"a": model_name, "b": None}}
        reasoning = (child_conv.get("reasoning") or {}).get("a")
        if reasoning:
            reply["reasoning"] = reasoning
        if child_conv.get("tools"):
            reply["tools"] = child_conv["tools"]
        ckpts = {l: (child_conv.get(f"_checkpoint_{l}") or {}).get("id")
                 for l in ("a", "b") if (child_conv.get(f"_checkpoint_{l}") or {}).get("id")}
        if ckpts:
            reply["checkpoints"] = ckpts
        if child_error and not summary:
            reply["error"] = child_error
        child_history.append(reply)
        try:
            _save_chat(child_id, title=title, messages=child_history, mode="direct",
                       modality="text", model_a=model_id, model_b=None,
                       eval_id=(child_conv.get("eval_id") if not child_conv.get("new") else None),
                       tools_guide=True, parent_id=parent_conv.get("chat_id"),
                       mcp_guide_hash=child_conv.get("mcp_guide_hash"))
        except Exception:
            LOG.exception("sub-agent chat save failed")
        _generation_finished(child_id)
    if summary:
        ok, status, output = True, "ok", summary
    else:
        ok, status = False, "error"
        output = (f"The sub-agent finished without a summary"
                  f"{f' ({child_error})' if child_error else ''}. "
                  f"Its chat is saved as \u201c{title}\u201d.")
    yield _sse({"type": "subagent", "status": "done", "child_id": child_id,
                "ok": ok, "model_name": model_name, "mode": smode, "lane": lane})
    if not params.get("fanout"):
        # The parent model needs the id to manage (e.g. delete) this chat.
        output = f"[sub-agent chat: {child_id}]\n{output}"
    return ok, status, output, extra


def _agent_run(client: ArenaClient, message: str, conv: dict):
    """Local tools in a text chat: each model asks for tools and the app runs them under the permission mode.
    In Battle and Side by Side each model writes to its own copy of the folder (option-a, option-b)."""
    root = _workspace_dir()
    root.mkdir(parents=True, exist_ok=True)
    lanes = ("a", "b") if conv["mode"] in ("battle", "side-by-side") else ("a",)
    dual = len(lanes) == 2
    tools = {l: agent.LocalTools(root, root / agent.LANE_DIRS[l] if dual else None) for l in lanes}
    mode = _perm_mode()
    total = {"a": "", "b": ""}
    yield _sse({"type": "agent", "root": str(root), "mode": mode, "dual": dual})
    try:
        mcp_tools = CONNECTORS.ensure_started(timeout=30)
    except Exception as e:
        mcp_tools = {}
        yield _sse({"type": "notice", "text": f"MCP connectors are unavailable for this turn: {e}"})
    mcp_guide = CONNECTORS.guide(mcp_tools)
    if mcp_tools:
        yield _sse({"type": "agent", "stage": f"MCP tools available: {len(mcp_tools)}",
                    "connectors": sorted({item['server'] for item in mcp_tools.values()})})
    mcp_hash = hashlib.sha1(mcp_guide.encode("utf-8")).hexdigest()[:12] if mcp_guide else None
    delegate_section = agent.delegate_guide(_delegate_models()) if conv.get("allow_delegate") else ""
    if conv["new"] or not conv.get("guide_sent"):
        # Also covers enabling tools mid-conversation (e.g. the Direct tools
        # toggle): the model only learns the tool protocol from this guide.
        content = agent.guide(root, permissions.MODES[mode], agent.MODE_NOTES[mode], dual,
                              extra_tools=mcp_guide, delegate=delegate_section) + message
        conv["mcp_guide_hash"] = mcp_hash
        if delegate_section:
            conv["delegate_sent"] = True
    elif mcp_guide and mcp_hash != conv.get("mcp_guide_hash"):
        # The connector tool list is only resent when it changed, not on
        # every turn of the conversation.
        content = ("[Arena Desktop] Connector tools available for this turn:\n" + mcp_guide
                   + "\nUser message:\n" + message)
        conv["mcp_guide_hash"] = mcp_hash
        if delegate_section and not conv.get("delegate_sent"):
            content = delegate_section + content
            conv["delegate_sent"] = True
    else:
        content = message
        if delegate_section and not conv.get("delegate_sent"):
            # Chats started before sub-agents existed learn about them once.
            content = ("[Arena Desktop] Sub-agent delegation is now available in this chat:\n"
                       + delegate_section + "\nUser message:\n" + message)
            conv["delegate_sent"] = True
    sig_counts: dict = {}
    key_counts: dict = {}
    loop_notes: list = []
    for rnd in range(1, agent.MAX_ROUNDS + 1):
        out = yield from _turn_auto(client, content, conv)
        if out is None:
            break
        calls = {l: agent.parse_tool_calls(out[l]) for l in lanes}
        for l in lanes:
            total[l] += out[l]
        if not any(calls.values()) or _stopped():
            break
        # Soft anti-loop (v5.1): repeated calls are marked blocked and
        # answered with a warning during execution below - the task is
        # NOT stopped, the model just gets told to change approach.
        blocked: dict = {}  # id(call) -> repeat count
        for l in lanes:
            for call in calls[l]:
                try:
                    sig = json.dumps([call.get("name"), call.get("params") or {}],
                                     sort_keys=True, ensure_ascii=False)
                except (TypeError, ValueError):
                    sig = repr(call)
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
                if sig_counts[sig] >= 4:
                    blocked[id(call)] = max(blocked.get(id(call), 0), sig_counts[sig])
                cparams = call.get("params") or {}
                target_key = (call.get("name") or "tool",
                              str(cparams.get("path") or cparams.get("pattern")
                                  or (cparams.get("cmd") or "")[:40]))
                key_counts[target_key] = key_counts.get(target_key, 0) + 1
                if key_counts[target_key] == 3:
                    loop_notes.append(
                        f"[Arena Desktop] Notice: you have called {target_key[0]} on "
                        f"'{target_key[1]}' three times. If it is not working, change "
                        "approach instead of repeating it.")
                elif key_counts[target_key] >= 5:
                    blocked[id(call)] = max(blocked.get(id(call), 0), key_counts[target_key])
        mode = _perm_mode()
        results = {}
        precomputed: dict = {}
        for l in lanes:
            results[l] = outcomes = []
            for call in calls[l]:
                if _stopped():
                    outcomes.append((call, "skipped: the user pressed Stop", ""))
                    continue
                pre = precomputed.pop(id(call), None)
                if pre is not None:
                    outcomes.append((call, pre["status"], pre["output"]))
                    conv.setdefault("tools", []).append(pre["step"])
                    yield _sse({"type": "tool_result", "model": l, "tool": "delegate_task",
                                "child_id": pre["step"].get("child_id"), "ok": pre["ok"],
                                "status": pre["status"], "output": (pre["output"] or "")[:4000]})
                    continue
                if id(call) in blocked:
                    info = {"tool": call["name"], "model": l, "inside": True,
                            "summary": f"{call['name']} (not run: repeated call)"}
                    yield _sse(dict(info, type="tool_call"))
                    n = blocked[id(call)]
                    ok, status = False, "blocked: repeated call"
                    output = (f"[Arena Desktop] This exact {call['name']} call was NOT run: "
                              f"you have already made it {n} times and the result will not "
                              "change. Stop repeating it - try a different approach, or "
                              "finish the task with what you already have.")
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": call["name"], "summary": info["summary"], "lane": l,
                         "diff": None, "ok": ok, "status": status})
                    yield _sse({"type": "tool_result", "model": l, "tool": call["name"],
                                "ok": ok, "status": status, "output": output[:4000]})
                    continue
                if call["name"] in ("ls", "search"):
                    # Diet (v5.1.5): the same listing/search repeated in one
                    # turn re-bills its whole output as context. Answer from
                    # cache until anything in this turn mutates files.
                    info = dict(tools[l].describe(call), model=l)
                    yield _sse(dict(info, type="tool_call"))
                    try:
                        out_key = json.dumps([call["name"], call.get("params") or {}],
                                             sort_keys=True, ensure_ascii=False)
                    except (TypeError, ValueError):
                        out_key = repr(call)
                    cache = conv.setdefault("_output_cache", {})
                    answer = yield from _permit(mode, call["name"], info)
                    if answer != "allow":
                        ok, output = False, ""
                        status = ("blocked: plan mode is read-only" if mode == "plan"
                                  else "blocked by safety rules") if answer == "blocked" else "denied by the user"
                    elif out_key in cache:
                        ok, status = True, "ok"
                        output = ("[Arena Desktop] Same result as earlier in this turn - "
                                  "not repeated to save context. It has not changed since.")
                    else:
                        ok, output = tools[l].run(call)
                        status = "ok" if ok else "error"
                        if ok:
                            cache[out_key] = True
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": call["name"], "summary": info.get("summary"), "lane": l,
                         "diff": info.get("diff"), "ok": ok, "status": status})
                    yield _sse({"type": "tool_result", "model": l, "tool": call["name"],
                                "rel": info.get("rel"), "ok": ok, "status": status,
                                "output": (output or "")[:4000]})
                    continue
                if call["name"] in ("read_file", "read_files"):
                    rparams = call.get("params") or {}
                    if call["name"] == "read_files":
                        raw_paths = []
                        for key in ("paths", "files"):
                            chunk = str(rparams.get(key) or "")
                            raw_paths += [ln.strip() for ln in chunk.replace(",", "\n").splitlines() if ln.strip()]
                        for i in range(1, 7):
                            v = str(rparams.get(f"path{i}") or "").strip()
                            if v:
                                raw_paths.append(v)
                        if rparams.get("path"):
                            raw_paths.insert(0, str(rparams["path"]).strip())
                        paths = list(dict.fromkeys(raw_paths))[:8]
                    else:
                        paths = [str(rparams.get("path") or "").strip()]
                    ranged = {k: rparams[k] for k in ("start_line", "end_line") if rparams.get(k)}
                    info = dict(tools[l].describe(
                        {"name": "read_file", "params": {"path": paths[0] if paths and paths[0] else "."}}), model=l)
                    info["tool"] = call["name"]
                    if len(paths) > 1:
                        info["summary"] = "Read " + ", ".join(paths)[:160]
                    yield _sse(dict(info, type="tool_call"))
                    if not paths or not paths[0]:
                        ok, status, output = False, "error", (
                            "read_files needs at least one path (one per line inside <paths>).")
                    else:
                        answer = yield from _permit(mode, "read_file", info)
                        if answer != "allow":
                            ok, output = False, ""
                            status = ("blocked: plan mode is read-only" if mode == "plan"
                                      else "blocked by safety rules") if answer == "blocked" else "denied by the user"
                        else:
                            cache = conv.setdefault("_read_cache", {})
                            segments = []
                            any_ok = False
                            for rel in paths:
                                try:
                                    target, _inside = tools[l].resolve(rel)
                                except Exception:
                                    target = None
                                sig = None
                                if target is not None:
                                    try:
                                        st = target.stat()
                                        sig = (st.st_mtime_ns, st.st_size)
                                    except OSError:
                                        sig = None
                                ckey = (str(target), str(ranged.get("start_line") or ""),
                                        str(ranged.get("end_line") or ""))
                                if sig is not None and cache.get(ckey) == sig:
                                    segments.append(f"== {rel} ==\n[unchanged since your last read - content not repeated]"
                                                    if len(paths) > 1 else
                                                    "[unchanged since your last read - content not repeated]")
                                    any_ok = True
                                    continue
                                sub = {"name": "read_file",
                                       "params": dict({"path": rel}, **(ranged if len(paths) == 1 else {}))}
                                sub_ok, sub_out = tools[l].run(sub)
                                if sub_ok and sig is not None:
                                    cache[ckey] = sig
                                any_ok = any_ok or sub_ok
                                segments.append(f"== {rel} ==\n{sub_out}" if len(paths) > 1 else sub_out)
                            ok = any_ok
                            status = "ok" if any_ok else "error"
                            output = "\n\n".join(segments)
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": call["name"], "summary": info.get("summary"), "lane": l,
                         "diff": None, "ok": ok, "status": status})
                    yield _sse({"type": "tool_result", "model": l, "tool": call["name"],
                                "rel": info.get("rel"), "ok": ok, "status": status,
                                "output": (output or "")[:4000]})
                    continue
                if call["name"] == "ask_user":
                    qparams = call.get("params") or {}
                    question = str(qparams.get("question") or "").strip()
                    options = [str(qparams.get(f"option{i}") or "").strip() for i in range(1, 5)]
                    options = [o for o in options if o][:4]
                    custom = str(qparams.get("custom") or "true").strip().lower() not in ("false", "no", "0")
                    info = {"tool": "ask_user", "model": l, "name": "Question",
                            "summary": "Question: " + (" ".join(question.split())[:120] or "…"),
                            "inside": True, "path": question[:400]}
                    yield _sse(dict(info, type="tool_call"))
                    answer_text = ""
                    if not question:
                        ok, status, output = False, "error", "ask_user needs a <question>."
                    else:
                        qid = generate_uuidv7()
                        parent_chat = getattr(_tl, "chat_id", None) or conv.get("chat_id")
                        with _questions_lock:
                            _QUESTIONS[qid] = {
                                "id": qid, "chat_id": parent_chat, "question": question,
                                "options": options, "custom": custom, "lane": l,
                                "subagent": (conv.get("chat_id") if conv.get("chat_id") != parent_chat else None),
                                "answer": None}
                        yield _sse({"type": "question", "id": qid, "lane": l, "model": l,
                                    "question": question, "options": options, "custom": custom,
                                    "chat_id": parent_chat,
                                    "subagent": _QUESTIONS[qid]["subagent"]})
                        _notify("Arena Desktop", "The model is waiting for your answer.", "question")
                        answer_text = yield from _wait_for(lambda: _question_answer(qid), QUESTION_TIMEOUT)
                        with _questions_lock:
                            _QUESTIONS.pop(qid, None)
                        yield _sse({"type": "question_done", "id": qid, "lane": l,
                                    "answer": answer_text or ""})
                        if answer_text:
                            ok, status, output = True, "ok", f'The user answered: "{answer_text}"'
                        else:
                            ok, status = False, "timeout"
                            output = ("The user did not answer in time. Proceed with your best "
                                      "judgment and state the assumption you made.")
                            answer_text = ""
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": "ask_user", "summary": info["summary"], "lane": l, "diff": None,
                         "ok": ok, "status": status, "question": question, "options": options,
                         "answer": answer_text})
                    yield _sse({"type": "tool_result", "model": l, "tool": "ask_user",
                                "ok": ok, "status": status, "output": (output or "")[:4000]})
                    continue
                if call["name"] == "propose_plan":
                    pparams = call.get("params") or {}
                    plan_text = str(pparams.get("plan") or pparams.get("content") or pparams.get("text") or "").strip()
                    first_line = " ".join((plan_text.splitlines() or [""])[0].split())[:120]
                    info = {"tool": "propose_plan", "model": l, "name": "Plan",
                            "summary": "Plan: " + (first_line or "\u2026"),
                            "inside": True, "path": plan_text[:400]}
                    yield _sse(dict(info, type="tool_call"))
                    answer_text = ""
                    if not plan_text:
                        ok, status, output = False, "error", "propose_plan needs a <plan> with the steps."
                    else:
                        qid = generate_uuidv7()
                        parent_chat = getattr(_tl, "chat_id", None) or conv.get("chat_id")
                        plan_options = ["Approve plan", "Cancel"]
                        with _questions_lock:
                            _QUESTIONS[qid] = {
                                "id": qid, "chat_id": parent_chat, "kind": "plan",
                                "question": "Proposed plan", "plan": plan_text[:4000],
                                "options": plan_options, "custom": True, "lane": l,
                                "subagent": (conv.get("chat_id") if conv.get("chat_id") != parent_chat else None),
                                "answer": None}
                        yield _sse({"type": "question", "id": qid, "lane": l, "model": l,
                                    "kind": "plan", "question": "Proposed plan", "plan": plan_text[:4000],
                                    "options": plan_options, "custom": True,
                                    "chat_id": parent_chat,
                                    "subagent": _QUESTIONS[qid]["subagent"]})
                        _notify("Arena Desktop", "The model proposed a plan for your review.", "question")
                        answer_text = yield from _wait_for(lambda: _question_answer(qid), QUESTION_TIMEOUT)
                        with _questions_lock:
                            _QUESTIONS.pop(qid, None)
                        yield _sse({"type": "question_done", "id": qid, "lane": l,
                                    "answer": answer_text or ""})
                        low = (answer_text or "").strip().lower()
                        if low == "approve plan":
                            ok, status = True, "approved"
                            output = ("The user approved your plan. Execute it now, step by step, "
                                      "keeping the set_todos checklist updated as you go.")
                        elif low == "cancel":
                            ok, status = False, "cancelled"
                            output = ("The user cancelled the plan. Make no changes; reply briefly "
                                      "and ask how they would like to proceed.")
                        elif answer_text:
                            ok, status = True, "revise"
                            output = (f'The user wants changes to the plan: "{answer_text}". '
                                      "Revise the plan accordingly and present it again with "
                                      "propose_plan - do not start editing yet.")
                        else:
                            ok, status = False, "timeout"
                            output = ("The user did not review the plan in time. Do not make changes; "
                                      "reply with the plan as plain text and finish.")
                            answer_text = ""
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": "propose_plan", "summary": info["summary"], "lane": l, "diff": None,
                         "ok": ok, "status": status, "plan": plan_text[:4000], "answer": answer_text})
                    yield _sse({"type": "tool_result", "model": l, "tool": "propose_plan",
                                "ok": ok, "status": status, "output": (output or "")[:4000]})
                    continue
                if call["name"] == "set_todos":
                    tparams = call.get("params") or {}
                    raw = str(tparams.get("todos") or tparams.get("items") or tparams.get("content") or "")
                    items = _parse_todos(raw)
                    done_n = sum(1 for it in items if it["status"] == "done")
                    info = {"tool": "set_todos", "model": l, "name": "Checklist",
                            "summary": f"Checklist: {done_n}/{len(items)} done",
                            "inside": True, "path": raw[:400]}
                    yield _sse(dict(info, type="tool_call"))
                    if not items:
                        ok, status, output = False, "error", (
                            "set_todos needs a <todos> list, one step per line: "
                            "[x] done, [~] in progress, [ ] pending.")
                    else:
                        ok, status = True, "ok"
                        output = f"Checklist updated: {done_n}/{len(items)} done."
                        conv["todos"] = items
                        yield _sse({"type": "todos", "lane": l, "model": l,
                                    "chat_id": conv.get("chat_id"), "items": items})
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": "set_todos", "summary": info["summary"], "lane": l, "diff": None,
                         "ok": ok, "status": status, "todos": items})
                    yield _sse({"type": "tool_result", "model": l, "tool": "set_todos",
                                "ok": ok, "status": status, "output": output})
                    continue
                if call["name"] == "delegate_task":
                    child_id = generate_uuidv7()
                    task_text = str((call.get("params") or {}).get("task") or "").strip()
                    info = {"tool": "delegate_task", "model": l, "name": "Sub-agent",
                            "summary": "Sub-agent: " + (" ".join(task_text.split())[:120] or "task"),
                            "child_id": child_id, "inside": True, "path": task_text[:400]}
                    yield _sse(dict(info, type="tool_call"))
                    if not conv.get("allow_delegate"):
                        ok, status = False, "error"
                        output = "delegate_task is not available inside a sub-agent; do the work directly."
                        extra = {"child_id": child_id}
                    else:
                        partner = _parallel_partner(calls[l], call, precomputed)
                        if partner is not None:
                            ok, status, output, extra = yield from _run_parallel_research(
                                client, conv, call, partner, l, child_id, precomputed)
                        else:
                            ok, status, output, extra = yield from _run_subagent(client, conv, call, l, child_id)
                    outcomes.append((call, status, output))
                    if extra.get("mode") == "work":
                        conv.pop("_output_cache", None)  # child may have edited files
                    conv.setdefault("tools", []).append(
                        {"tool": "delegate_task", "summary": info["summary"], "lane": l,
                         "diff": None, "ok": ok, "status": status,
                         "child_id": extra.get("child_id"), "model": extra.get("model"),
                         "mode": extra.get("mode")})
                    yield _sse({"type": "tool_result", "model": l, "tool": "delegate_task",
                                "child_id": extra.get("child_id"), "ok": ok, "status": status,
                                "output": (output or "")[:4000]})
                    continue
                if call["name"] == "delete_subagent":
                    dparams = call.get("params") or {}
                    target_id = str(dparams.get("child_id") or dparams.get("chat_id")
                                    or dparams.get("id") or "").strip()
                    info = {"tool": "delete_subagent", "model": l, "name": "Sub-agent",
                            "summary": "Delete sub-agent chat", "child_id": target_id,
                            "inside": True, "path": target_id[:64]}
                    yield _sse(dict(info, type="tool_call"))
                    if not conv.get("allow_delegate"):
                        ok, status = False, "error"
                        output = "delete_subagent is not available inside a sub-agent."
                    elif not target_id or not _validate_chat_id(target_id):
                        ok, status = False, "error"
                        output = ("Pass the sub-agent chat id shown at the start of the "
                                  "delegate_task result: <child_id>...</child_id>.")
                    else:
                        child = _load_chat(target_id) or {}
                        if str(child.get("parent_id") or "") != str(conv.get("chat_id") or ""):
                            ok, status = False, "error"
                            output = ("That chat is not one of your sub-agents in this "
                                      "conversation, so it was not deleted.")
                        else:
                            ok, output = _delete_chat_everywhere(client, target_id, child)
                            status = "ok" if ok else "error"
                    outcomes.append((call, status, output))
                    conv.setdefault("tools", []).append(
                        {"tool": "delete_subagent", "summary": info["summary"], "lane": l,
                         "diff": None, "ok": ok, "status": status, "child_id": target_id})
                    yield _sse({"type": "tool_result", "model": l, "tool": "delete_subagent",
                                "child_id": target_id, "ok": ok, "status": status,
                                "output": (output or "")[:4000]})
                    if ok:
                        yield _sse({"type": "chats_changed"})
                    continue
                allowed = conv.get("allowed_tools")
                if allowed is not None and call["name"] not in allowed:
                    entry_ro = mcp_tools.get(call["name"])
                    mcp_read_only = bool(entry_ro and (entry_ro.get("annotations") or {}).get("readOnlyHint") is True)
                    if not mcp_read_only:
                        info = {"tool": call["name"], "model": l, "inside": True,
                                "summary": f"{call['name']} (read-only sub-agent)"}
                        yield _sse(dict(info, type="tool_call"))
                        ok, output = False, ""
                        status = "blocked: this sub-agent is read-only (research mode)"
                        outcomes.append((call, status, output))
                        conv.setdefault("tools", []).append(
                            {"tool": call["name"], "summary": info.get("summary"), "lane": l,
                             "diff": None, "ok": ok, "status": status})
                        yield _sse({"type": "tool_result", "model": l, "tool": call["name"],
                                    "ok": ok, "status": status, "output": ""})
                        continue
                mcp_entry = mcp_tools.get(call["name"])
                mcp_args = None
                mcp_error = None
                if mcp_entry:
                    try:
                        mcp_args = agent.coerce_mcp_arguments(call, mcp_entry.get("input_schema"))
                        args_display = json.dumps(mcp_args, ensure_ascii=False)[:4000]
                    except ValueError as e:
                        mcp_error = str(e)
                        args_display = str(call.get("params") or {})[:4000]
                    info = {
                        "tool": call["name"], "model": l, "mcp": True,
                        "server": mcp_entry["server"], "mcp_tool": mcp_entry["tool"],
                        "summary": f"MCP {mcp_entry['server']}: {mcp_entry['tool']}",
                        "rel": mcp_entry["server"], "path": args_display, "inside": False,
                    }
                else:
                    info = dict(tools[l].describe(call), model=l)
                yield _sse(dict(info, type="tool_call"))
                if mcp_entry:
                    if mcp_error:
                        ok, output, status = False, mcp_error, "error"
                    else:
                        annotations = mcp_entry.get("annotations") or {}
                        permission_tool = "read_file" if annotations.get("readOnlyHint") is True else "run_cmd"
                        answer = yield from _permit(mode, permission_tool, info)
                        if answer == "allow":
                            try:
                                output = CONNECTORS.call(call["name"], mcp_args or {})
                                ok = not str(output).startswith(("MCP Error:", "Error: MCP tool"))
                            except Exception as e:
                                ok, output = False, f"{type(e).__name__}: {e}"
                            status = "ok" if ok else "error"
                        else:
                            ok, output = False, ""
                            status = ("blocked: plan mode is read-only" if mode == "plan" else "blocked by safety rules") \
                                if answer == "blocked" else "denied by the user"
                elif call["name"] not in permissions.KIND:
                    ok, output = tools[l].run(call)
                    status = "error"
                else:
                    answer = yield from _permit(mode, call["name"], info)
                    if answer == "allow":
                        if call["name"] in ("write_file", "edit_file", "delete"):
                            _checkpoint_capture(conv, l, info)
                        ok, output = tools[l].run(call)
                        status = "ok" if ok else "error"
                        if call["name"] == "run_cmd":
                            conv.pop("_output_cache", None)
                        if ok and call["name"] in ("write_file", "edit_file", "delete"):
                            _invalidate_read_cache(conv, info)
                    else:
                        ok, output = False, ""
                        status = ("blocked: plan mode is read-only" if mode == "plan" else "blocked by safety rules") \
                            if answer == "blocked" else "denied by the user"
                outcomes.append((call, status, output))
                conv.setdefault("tools", []).append({"tool": call["name"], "summary": info.get("summary"), "lane": l,
                                                     "diff": info.get("diff"), "ok": ok, "status": status})
                yield _sse({"type": "tool_result", "model": l, "tool": call["name"], "rel": info.get("rel"), "ok": ok,
                            "status": status, "output": (output or "")[:4000]})
        if _stopped():
            break
        if rnd == agent.MAX_ROUNDS:
            yield _sse({"type": "notice", "text": f"Stopped after {agent.MAX_ROUNDS} tool rounds. Send 'continue' to keep going."})
            break
        if dual:
            lane_dirs = {l: (tools[l].out or root) / agent.ARTIFACTS_DIR for l in lanes}
            content = agent.format_lane_results(results, artifact_dirs=lane_dirs)
        else:
            content = agent.format_results(results["a"], artifact_dir=root / agent.ARTIFACTS_DIR)
        if loop_notes:
            content += "\n" + "\n".join(loop_notes)
            loop_notes = []
        if rnd == agent.MAX_ROUNDS - 1:
            content += ("\n[Arena Desktop] You are near the tool-round limit for this "
                        "reply. Finish the current step now and answer with your final "
                        "summary; do not start new tool work.")
        for l in lanes:
            total[l] += "\n\n"
        yield _sse({"type": "round", "n": rnd + 1})
    # A model that only ever replied with tool tags (or whose final round
    # came back empty) must not leave a blank answer behind: say what was
    # done and how to continue, in the answer text itself.
    for l in lanes:
        used = [t.get("tool") for t in (conv.get("tools") or []) if t.get("lane") == l]
        if used and not agent.strip_tool_calls(total[l]).strip():
            names = ", ".join(dict.fromkeys(used))
            note = (f"The model finished without a final message after using tools ({names}). "
                    "Its changes are in the selected folder. Send 'continue' to ask it to finish or summarize.")
            prefix = "\n\n" if total[l].strip() else ""
            total[l] = total[l] + prefix + note
            yield _sse({"type": "text", "model": l, "text": prefix + note})
    return total


def _model_name(model_id):
    """Display name for a model id from the cached catalog (None when unknown)."""
    if not model_id:
        return None
    stack = [_catalog_models()]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if cur.get("model_id", cur.get("id")) == model_id and cur.get("name"):
                return cur["name"]
            stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


# ---------- file attachments (Arena storage) ----------

_ATTACHMENT_EXT_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".pdf": "application/pdf",
    ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
    ".html": "text/html", ".htm": "text/html", ".xml": "text/xml",
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json",
}


def _attachment_media_type(filename: str, declared: str) -> str:
    """Resolve a file's media type the way Arena's uploader does (extension wins for text kinds)."""
    ext = Path(filename or "").suffix.lower()
    by_ext = _ATTACHMENT_EXT_MIME.get(ext)
    declared = (declared or "").split(";", 1)[0].strip().lower()
    if by_ext and (not declared or declared in ("application/octet-stream", "text/plain")
                   or by_ext.startswith("text/") or by_ext == "application/json"):
        return by_ext
    return declared or by_ext or "application/octet-stream"


def _validate_upload(kind: str, media_type: str, size: int, modality: str) -> Optional[str]:
    """Mirror Arena's attachment rules; returns a friendly refusal or None."""
    if kind == "agent":
        if media_type not in contracts.AGENT_ATTACHMENT_MIME_TYPES:
            return ("Agent Mode accepts PNG, WebP, JPEG, GIF, PDF, TXT, MD, CSV, HTML, XML, CSS, JS and JSON files.")
        if media_type == "application/pdf" and size > contracts.AGENT_PDF_MAX_BYTES:
            return "PDF files can be up to 10 MB in Agent Mode."
        if size > contracts.AGENT_ATTACHMENT_MAX_BYTES:
            return "Files can be up to 25 MB in Agent Mode."
        return None
    if media_type not in contracts.EVAL_ATTACHMENT_MIME_TYPES:
        return ("This mode accepts PNG, JPEG, WebP and PDF files. "
                "Agent Mode accepts more file types.")
    limit = (contracts.VIDEO_ATTACHMENT_MAX_BYTES if modality == "video"
             else contracts.EVAL_ATTACHMENT_MAX_BYTES)
    if size > limit:
        return f"Files can be up to {limit // (1024 * 1024)} MB here."
    return None


def _storage_put(upload_url: str, data: bytes, media_type: str) -> Optional[str]:
    """PUT bytes to Arena's presigned storage URL; returns an error or None."""
    try:
        resp = requests.put(upload_url, data=data,
                            headers={"Content-Type": media_type}, timeout=180)
    except requests.RequestException as e:
        return f"Upload failed: {e}"
    if resp.status_code not in (200, 201, 204):
        return f"Arena storage refused the upload (HTTP {resp.status_code})."
    return None


@app.route("/api/uploads", methods=["POST"])
def api_uploads():
    """Upload one attachment to Arena's storage, exactly like arena.ai's composer.

    kind=evaluation: presigned PUT + signed read URL -> {name, contentType, url}.
    kind=agent: content-addressed PUT -> {filename, mediaType, url} where url
    is Arena's /api/chat/workspace/cas/user/<sha256> path the Agent reads.
    """
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"error": "Not signed in. Press Sign in first.", "code": "auth"}), 401
    kind = (request.form.get("kind") or "evaluation").strip().lower()
    if kind not in ("evaluation", "agent"):
        kind = "evaluation"
    modality = (request.form.get("modality") or "").strip().lower()
    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "No file was sent.", "code": "bad_request"}), 400
    filename = Path(upload.filename or "file").name or "file"
    data = upload.read()
    if not data:
        return jsonify({"error": "That file is empty.", "code": "bad_request"}), 400
    media_type = _attachment_media_type(filename, upload.mimetype or "")
    refusal = _validate_upload(kind, media_type, len(data), modality)
    if refusal:
        return jsonify({"error": refusal, "code": "unsupported_attachment"}), 400
    if kind == "agent":
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        minted, error, _ = _arena_post_json(
            contracts.PATHS["storage_agent_upload_url"],
            {"hash": digest, "contentType": media_type, "size": len(data)}, timeout=60)
        if error or not isinstance(minted, dict) or not minted.get("uploadUrl"):
            return jsonify({"error": error or "Arena did not accept the file.", "code": "arena_upload"}), 502
        put_error = _storage_put(minted["uploadUrl"], data, media_type)
        if put_error:
            return jsonify({"error": put_error, "code": "arena_upload"}), 502
        return jsonify({"ok": True, "kind": "agent", "filename": filename, "mediaType": media_type,
                        "url": f"/api/chat/workspace/cas/user/{digest}",
                        "size": len(data), "hash": digest})
    minted, error, _ = _arena_post_json(
        contracts.PATHS["storage_upload_url"],
        {"fileName": filename, "contentType": media_type}, timeout=60)
    if error or not isinstance(minted, dict) or not minted.get("uploadUrl") or not minted.get("key"):
        return jsonify({"error": error or "Arena did not accept the file.", "code": "arena_upload"}), 502
    put_error = _storage_put(minted["uploadUrl"], data, media_type)
    if put_error:
        return jsonify({"error": put_error, "code": "arena_upload"}), 502
    signed, error, _ = _arena_post_json(
        contracts.PATHS["storage_signed_url"], {"key": minted["key"]}, timeout=60)
    if error or not isinstance(signed, dict) or not signed.get("url"):
        return jsonify({"error": error or "Arena did not return a file link.", "code": "arena_upload"}), 502
    return jsonify({"ok": True, "kind": "evaluation", "name": filename, "contentType": media_type,
                    "url": signed["url"], "size": len(data)})


def _request_attachments(data: dict, native_agent_files: bool):
    """Sanitize client-supplied attachments into wire shape for this turn.

    Returns (attachments, error). Evaluation entries are experimental
    attachments {name, contentType, url}; native Agent entries are file
    parts {filename, mediaType, url} handled by native_agent.user_message.
    """
    raw = data.get("attachments") if isinstance(data.get("attachments"), list) else []
    out = []
    total = 0
    for item in raw[:contracts.AGENT_ATTACHMENTS_MAX_PER_TURN]:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        name = str(item.get("name") or item.get("filename") or "file")[:200]
        media = str(item.get("contentType") or item.get("mediaType") or "").split(";", 1)[0].strip().lower()
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if not isinstance(url, str) or not url or not media:
            return None, "One attachment is missing its upload link. Remove it and attach the file again."
        total += max(0, size)
        if native_agent_files:
            out.append({"filename": name, "mediaType": media, "url": url, "size": size})
        else:
            out.append({"name": name, "contentType": media, "url": url, "size": size})
    if native_agent_files and total > contracts.AGENT_ATTACHMENTS_MAX_BYTES_PER_TURN:
        return None, "Agent Mode accepts up to 50 MB of files per message."
    return out, None


@app.route("/api/chat", methods=["POST"])
def api_chat():
    # Selection resolution still touches a few module-level Fields, so the
    # short synchronous setup is serialized; the returned SSE generators
    # then run fully in parallel, one thread per chat.
    with _chat_setup_lock:
        return _api_chat_impl()


def _api_chat_impl():
    global _current_chat_id, _conversation_history, _ui_mode, _ui_modality
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"error": "Not signed in. Press Sign in first.", "code": "auth"}), 401
    chat_id = data.get("chat_id") or str(uuid.uuid4())
    if not isinstance(chat_id, str) or not _validate_chat_id(chat_id):
        return jsonify({"error": "Invalid chat ID", "code": "bad_request"}), 400
    if _chat_generating(chat_id):
        return jsonify({"error": "This chat is still working on the previous message. "
                        "Other chats can be used meanwhile.", "code": "busy"}), 409
    existing = _load_chat(chat_id) or {}
    # A started chat keeps its mode, modality and models, like on the Arena
    # website: the composer's selection only applies to new chats.  Forcing
    # the stored values here means a follow-up can never silently fork a
    # different Arena conversation or switch models mid-chat; changing any
    # of them requires a new chat.
    locked = any(isinstance(m, dict) and m.get("role") in ("user", "assistant")
                 for m in (existing.get("messages") or []))
    # The composer sends its visible selection with every request.  Honoring it
    # here makes the request self-describing: a slow status answer or a chat
    # load can no longer silently turn an "Agent" send into a Direct chat.
    # Clients that omit the Fields keep the legacy /api/mode state behavior.
    req_mode = data.get("mode") if isinstance(data.get("mode"), str) and data.get("mode") else _ui_mode
    req_modality = (data.get("modality") if isinstance(data.get("modality"), str)
                    and data.get("modality") else _ui_modality)
    if locked:
        req_mode, req_modality = contracts.normalize_ui_selection(
            existing.get("mode"), existing.get("modality"))
        if req_modality == "auto":
            # A started chat no longer has an Auto work type: it was resolved
            # when its first message was created (legacy saves meant Text).
            req_modality = "text"
    else:
        # Mode wins over a stale Auto selection for a new chat. Agent has no
        # work-type selector on Arena, and Auto is Battle-only, so neither
        # state should make the first send fail with an ordering error.
        if req_mode == "agent" or (req_modality == "auto" and req_mode != "battle") \
                or (req_modality == "auto" and existing.get("eval_id")):
            req_modality = "text"
    selection_error = _selection_error(req_mode, req_modality)
    if selection_error:
        return jsonify({"error": selection_error, "code": "unsupported_selection"}), 400
    _ui_mode, _ui_modality = req_mode, req_modality
    client.active_mode = contracts.api_mode(_ui_mode)
    client.active_modality = contracts.api_modality(_ui_modality)
    mode, modality = contracts.validate_ui_selection(_ui_mode, _ui_modality)
    if _ui_mode == "battle" and _ui_modality == "auto" and not locked and not existing.get("eval_id"):
        # Battle+Auto, exactly like the website: Arena's router picks the
        # concrete work type for this new chat, and the chat keeps it.
        raw_attachments = data.get("attachments") if isinstance(data.get("attachments"), list) else []
        has_image = any(
            isinstance(item, dict) and str(item.get("contentType") or item.get("mediaType")
                                           or item.get("type") or item.get("mimeType") or "").startswith("image/")
            for item in raw_attachments)
        modality = _resolve_auto_modality(message, has_image=has_image)
        _ui_modality = {"chat": "text", "webdev": "code"}.get(modality, modality)
        client.active_modality = modality
    model_a, model_b = data.get("model_id") or None, data.get("model_id_b") or None
    fanout_models = [str(m) for m in (data.get("fanout_models") or [])
                     if isinstance(m, (str, int)) and str(m)][0:4]
    if len(fanout_models) < 2:
        fanout_models = []
    if fanout_models and not model_a:
        model_a = fanout_models[0]
    if locked:
        if _ui_mode in ("direct", "side-by-side"):
            model_a, model_b = existing.get("model_a"), existing.get("model_b")
        elif _ui_mode in ("battle", "agent"):
            model_a = model_b = None

    if _ui_mode == "battle":
        if model_a or model_b:
            return jsonify({"error": "Battle mode does not accept chosen models; Arena selects both models.",
                            "code": "unsupported_selection"}), 400
    elif _ui_mode == "agent":
        if model_a or model_b:
            return jsonify({"error": "Agent mode does not accept a chosen model; its model is assigned automatically.",
                            "code": "unsupported_selection"}), 400
        # Never gate Agent on Max.  Prefer an ordinary selectable text model
        # internally; if the catalog is unavailable, build_chat(auto_model)
        # lets Arena assign one.  The chosen ID/name is not shown in Agent UI.
        auto = _auto_agent_model()
        model_a = auto.get("model_id", auto.get("id")) if auto else None
    else:
        if not model_a:
            return jsonify({"error": "Pick a model first: Direct and Side by Side need one.", "code": "bad_request"}), 400
        allowed_ids = _allowed_model_ids(_ui_mode, _ui_modality)
        if allowed_ids and str(model_a) not in allowed_ids:
            return jsonify({"error": "That model is not available for this mode and modality in Arena's catalog.",
                            "code": "unsupported_selection"}), 400
        if _ui_mode == "direct" and model_b:
            return jsonify({"error": "Direct mode accepts exactly one model.", "code": "unsupported_selection"}), 400
        if _ui_mode == "side-by-side":
            if not model_b:
                return jsonify({"error": "Pick a second model for Side by Side.", "code": "bad_request"}), 400
            if str(model_a) == str(model_b):
                return jsonify({"error": "Side by Side compares two different models.", "code": "unsupported_selection"}), 400
            if allowed_ids and str(model_b) not in allowed_ids:
                return jsonify({"error": "Model B is not available for this mode and modality in Arena's catalog.",
                                "code": "unsupported_selection"}), 400
    if modality == "video" and len(message) > contracts.VIDEO_MESSAGE_MAX_LENGTH:
        return jsonify({"error": f"Video prompts can be up to {contracts.VIDEO_MESSAGE_MAX_LENGTH} characters. "
                        "Shorten the prompt and send again.", "code": "too_large"}), 400
    _current_chat_id = chat_id
    _stop.clear()
    _chat_stop_event(chat_id).clear()
    agent_backend = None
    if _ui_mode == "agent":
        # A chat keeps the Agent backend it was created with; the composer
        # only chooses for new chats.  Clients that omit the Field keep the
        # historical local loop.
        stored_backend = existing.get("agent_backend") or ((existing.get("native") or {}).get("backend"))
        requested_backend = data.get("agent_backend") if data.get("agent_backend") in ("arena", "local") else None
        agent_backend = stored_backend or requested_backend or "local"
    native_turn = _ui_mode == "agent" and agent_backend == "arena"
    turn_attachments, attachment_error = _request_attachments(data, native_turn)
    if attachment_error:
        return jsonify({"error": attachment_error, "code": "unsupported_attachment"}), 400
    if _ui_mode == "agent" and agent_backend == "arena":
        tz = data.get("timezone") if isinstance(data.get("timezone"), str) else None
        title = existing.get("title") or message[:50]
        nat_history = list(existing.get("messages") or [])
        user_entry = {"role": "user", "text": message}
        if turn_attachments:
            user_entry["attachments"] = turn_attachments
        nat_history.append(user_entry)
        _conversation_history = nat_history
        # The chat exists (and shows in the sidebar) from the moment the
        # prompt is sent — not only when the agent finishes.
        try:
            _save_chat(chat_id, title=title, messages=list(nat_history), mode="agent",
                       modality="text", model_a=None, model_b=None,
                       eval_id=(existing.get("native") or {}).get("session_id") or existing.get("eval_id"),
                       agent_backend="arena", native=existing.get("native"))
        except Exception:
            LOG.exception("native chat early save failed")
        # Persistent memory + a pending compaction digest ride along with
        # the first message of a fresh Arena session (memory resends only
        # when its text changed).
        native_prefix = ""
        native_mem_hash = existing.get("memory_hash")
        native_digest = existing.get("pending_digest") or ""
        if _memory_enabled():
            block, mem_hash = _memory_block(_chosen_ws())
            fresh_native = not (existing.get("native") or {}).get("session_id")
            if block and (fresh_native or native_digest or mem_hash != native_mem_hash):
                native_prefix += block
                native_mem_hash = mem_hash
        if native_digest:
            native_prefix += _digest_block(native_digest)
        # Agent Gateway: a fresh Arena session learns the temporary tunnel
        # address + pairing in its first message (same hidden-prefix channel
        # as memory/digest). Only with both opt-ins and Workspace Sync on.
        if not (existing.get("native") or {}).get("session_id"):
            try:
                gateway_block = _gateway_injection(data)
                if gateway_block:
                    native_prefix += gateway_block + "\n"
                    LOG.info("gateway: injected session block into new native chat")
            except Exception:
                LOG.exception("gateway injection failed")
        native_message = native_prefix + message

        def native_generate():
            _tl.error = None
            yield _sse({"type": "meta", "dual": False, "mode": "agent", "modality": "text",
                        "chat_id": chat_id, "permission_mode": _perm_mode(),
                        "names": {"a": "Agent", "b": None}})
            # The chat is persisted from the finally-block below, on EVERY
            # exit path (normal end, Stop, client disconnect): the native
            # session coordinates are published into `out` as they become
            # known, so the next message always continues this very chat
            # instead of starting a new Arena session.
            out = {"text": "", "reasoning": "", "tools": [], "files": [],
                   "native": existing.get("native"), "error": None}
            saved = False
            session_persisted = bool((existing.get("native") or {}).get("session_id"))
            try:
                run = _native_agent_run(client, native_message, existing, tz, result=out,
                                        files=turn_attachments or None)
                returned = None
                while True:
                    try:
                        event = next(run)
                    except StopIteration as stop:
                        returned = stop.value
                        break
                    # Persist the Arena session coordinates the moment the
                    # session exists on Arena, so the chat is continuable
                    # even while the agent is still working.
                    if not session_persisted and (out.get("native") or {}).get("session_id"):
                        session_persisted = True
                        try:
                            _save_chat(chat_id, title=title, messages=list(nat_history), mode="agent",
                                       modality="text", model_a=None, model_b=None,
                                       eval_id=out["native"]["session_id"],
                                       agent_backend="arena", native=out["native"])
                        except Exception:
                            LOG.exception("native chat session save failed")
                    yield event
                if returned is not None:
                    out = returned
                if out.get("files") and data.get("tools") is not False:
                    bridge_state = out.get("native") or existing.get("native") or {}
                    out["native"] = bridge_state
                    yield from _native_file_bridge(out["files"], bridge_state)
            finally:
                if not saved:
                    saved = True
                    reply = {"role": "assistant", "text": out.get("text", ""), "text_b": "",
                             "names": {"a": "Agent", "b": None}}
                    if out.get("reasoning"):
                        reply["reasoning"] = out["reasoning"]
                    if out.get("tools"):
                        reply["tools"] = out["tools"]
                    if out.get("error") and not out.get("text"):
                        reply["error"] = out["error"]
                    nat_history.append(reply)
                    native_state = out.get("native") or existing.get("native")
                    try:
                        _save_chat(chat_id, title=title, messages=nat_history, mode="agent",
                                   modality="text", model_a=None, model_b=None,
                                   eval_id=(native_state or {}).get("session_id"),
                                   agent_backend="arena", native=native_state,
                                   memory_hash=native_mem_hash,
                                   pending_digest=("" if native_digest else None))
                    except Exception:
                        LOG.exception("native chat save failed")
            yield _sse({"type": "done", "text_a": out.get("text", ""), "text_b": "",
                        "dual": False, "chat_id": chat_id,
                        "history_chars": _history_chars(nat_history)})

        return Response(
            stream_with_context(_sse_wrap(native_generate(), chat_id=chat_id)),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    # Each chat keeps its own history and Arena conversation.  A started
    # chat is locked to its stored mode/modality/models above, so `same`
    # holds for every follow-up and the same Arena conversation continues.
    existing_pair = contracts.normalize_ui_selection(existing.get("mode"), existing.get("modality")) if existing else (None, None)
    if existing_pair[1] == "auto" and (existing.get("messages") or existing.get("eval_id")):
        existing_pair = (existing_pair[0], "text")
    same = bool(existing) and existing_pair == (_ui_mode, _ui_modality)
    if _ui_mode == "agent" and same:
        preferred = _auto_agent_model(existing.get("model_a"))
        if preferred:
            model_a = preferred.get("model_id", preferred.get("id"))
    eval_id = existing.get("eval_id") if same else None
    wire_attachments = [
        {key: item[key] for key in ("name", "contentType", "url") if item.get(key)}
        for item in turn_attachments
    ]
    conv = {"eval_id": eval_id, "new": not eval_id, "mode": mode, "modality": modality,
            "model_a": model_a, "model_b": model_b, "chat_id": chat_id,
            "auto_model": _ui_mode == "agent", "reasoning": {"a": "", "b": ""},
            "lane_errors": {"a": [], "b": []},
            "attachments": wire_attachments or None,
            "guide_sent": bool(existing.get("tools_guide")),
            "mcp_guide_hash": existing.get("mcp_guide_hash"),
            "memory_hash": existing.get("memory_hash"),
            "delegate_sent": bool(existing.get("delegate_guide")),
            "title": existing.get("title") or message[:50], "tools": []}
    history = list(existing.get("messages") or [])
    user_entry = {"role": "user", "text": message}
    if turn_attachments:
        user_entry["attachments"] = turn_attachments
    history.append(user_entry)
    _conversation_history = history
    # The chat (with the user's prompt and its Arena session id) is on disk
    # from the moment the prompt is sent, so it shows in the sidebar and can
    # be reopened while the answer is still generating.
    if conv["new"] and not conv.get("eval_id"):
        conv["eval_id"] = generate_uuidv7()
    try:
        _save_chat(chat_id, title=conv["title"], messages=list(history), mode=_ui_mode,
                   modality=_ui_modality, model_a=model_a, model_b=model_b,
                   eval_id=conv.get("eval_id") if not conv["new"] else None,
                   tools_guide=bool(existing.get("tools_guide")) or None,
                   agent_backend=(agent_backend if _ui_mode == "agent" else None))
    except Exception:
        LOG.exception("chat early save failed")
    # Persistent memory + a pending compaction digest prefix the first
    # message of a fresh Arena session (memory resends only when changed).
    conv["prefix"] = ""
    conv["digest_used"] = False
    if modality not in ("image", "video"):
        digest = existing.get("pending_digest") or ""
        if _memory_enabled():
            block, mem_hash = _memory_block(_chosen_ws())
            if block and (conv["new"] or digest or mem_hash != conv.get("memory_hash")):
                conv["prefix"] += block
                conv["memory_hash"] = mem_hash
        if digest:
            conv["prefix"] += _digest_block(digest)
            conv["digest_used"] = True
    ui_mode, ui_modality = _ui_mode, _ui_modality
    names = {"a": "Agent", "b": None} if _ui_mode == "agent" else {"a": _model_name(model_a), "b": _model_name(model_b)}
    dual = mode in ("battle", "side-by-side")
    # Tools: Agent always; other text modes when a folder is chosen and the
    # composer toggle is on (clients that omit "tools" keep the old rule).
    tools_req = data.get("tools")
    if data.get("tools") is False:
        conv["mirror_ok"] = False
    tools_on = (ui_mode == "agent" and tools_req is not False) or (
        ui_mode != "agent"
        and _chosen_ws() is not None and modality in ("chat", "search")
        and (tools_req is None or tools_req is True))
    # Sub-agent delegation belongs to top-level tool chats; child chats
    # (parent_id set) cannot delegate further.
    conv["allow_delegate"] = bool(tools_on) and not existing.get("parent_id")

    def generate():
        _tl.error = None
        yield _sse({"type": "meta", "dual": dual, "mode": ui_mode, "modality": ui_modality,
                    "chat_id": conv["chat_id"], "permission_mode": _perm_mode(), "names": names})
        send = (conv.get("prefix") or "") + message
        if modality == "webdev" and not dual and not conv["new"]:
            changed, note = _local_edits(_code_root(conv["chat_id"], conv["title"], "a", conv["mode"]))
            if changed:
                send = note + send
                yield _sse({"type": "notice", "text": "Sent your local edits to the AI: " + ", ".join(changed)})
        history_chars = _history_chars(history)
        if fanout_models:
            out = yield from _fanout_run(client, message, conv, fanout_models)
        elif tools_on:
            out = yield from _agent_run(client, send, conv)
        else:
            out = yield from _turn_auto(client, send, conv)
        out = out or {"a": "", "b": ""}
        # conv carries this chat's own Arena session id; the shared client
        # Field is not used, so parallel chats cannot cross their sessions.
        clean = agent.strip_tool_calls if (tools_on or fanout_models) else str
        reply = {"role": "assistant", "text": clean(out["a"]), "text_b": clean(out["b"]), "names": names}
        reasoning = conv.get("reasoning") or {}
        if reasoning.get("a"):
            reply["reasoning"] = reasoning["a"]
        if reasoning.get("b"):
            reply["reasoning_b"] = reasoning["b"]
        if conv["tools"]:
            reply["tools"] = conv["tools"]
        ckpts = {l: (conv.get(f"_checkpoint_{l}") or {}).get("id")
                 for l in ("a", "b") if (conv.get(f"_checkpoint_{l}") or {}).get("id")}
        if ckpts:
            reply["checkpoints"] = ckpts
        if conv.get("sandbox"):
            reply["sandbox"] = conv["sandbox"]
        if dual:
            arena_ids = conv.get("last_arena_ids") or {}
            if arena_ids.get("a") and arena_ids.get("b"):
                reply["arena_message_ids"] = arena_ids
        lane_errors = {key: list(values) for key, values in (conv.get("lane_errors") or {}).items() if values}
        if lane_errors:
            reply["lane_errors"] = lane_errors
        if not (out["a"] or out["b"]) and _tl.error:
            reply["error"] = _tl.error
        history.append(reply)
        _save_chat(conv["chat_id"], title=conv["title"], messages=history, mode=ui_mode,
                   modality=ui_modality, model_a=model_a, model_b=model_b,
                   eval_id=(conv.get("eval_id") if not conv["new"] else None),
                   tools_guide=bool(tools_on) or bool(existing.get("tools_guide")),
                   agent_backend=("local" if ui_mode == "agent" else None),
                   mcp_guide_hash=conv.get("mcp_guide_hash"),
                   memory_hash=conv.get("memory_hash"),
                   delegate_guide=bool(conv.get("delegate_sent")),
                   pending_digest=("" if conv.get("digest_used") else None))
        if (not reply["text"].strip() and not reply["text_b"].strip()
                and modality in ("chat", "search")
                and not conv.get("media_saved") and not conv["tools"] and not _tl.error):
            yield _sse({"type": "notice", "level": "warn",
                        "text": "The model returned an empty reply. In a long conversation this usually "
                                "means Arena's context limit for this chat was reached — press Compact "
                                "(next to Workspace Sync) to continue here with a fresh context."})
        total_chars = _history_chars(history)
        # Tool chats re-bill the whole history every round, so nudge
        # towards Compact earlier than in plain chats (v5.1.5).
        warn_at = CONTEXT_WARN_CHARS * 5 // 8 if tools_on else CONTEXT_WARN_CHARS
        if history_chars < warn_at <= total_chars:
            yield _sse({"type": "notice", "level": "warn",
                        "text": f"This conversation is getting long (~{total_chars // 1000}k characters) "
                                "and may be close to Arena's context limit. Press Compact (next to "
                                "Workspace Sync) to continue in a fresh session with a summary — "
                                "otherwise replies may start coming back empty."})
        done_event = {"type": "done", "text_a": out["a"], "text_b": out["b"], "dual": dual,
                      "chat_id": conv["chat_id"], "history_chars": total_chars,
                      "msg_index": len(history) - 1}
        if reply.get("arena_message_ids"):
            done_event["arena_message_ids"] = reply["arena_message_ids"]
        if reply.get("checkpoints"):
            done_event["checkpoints"] = reply["checkpoints"]
        yield _sse(done_event)

    return Response(
        stream_with_context(_sse_wrap(generate(), chat_id=chat_id)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- File CRUD (IDE) ----------

def _safe_resolve(rel_path: str) -> Path:
    """Resolve a user-supplied path, blocking traversal outside home."""
    home = Path.home().resolve()
    if rel_path in ("", "."):
        return home
    p = Path(rel_path).expanduser()
    p = (p if p.is_absolute() else home / p).resolve()
    if p != home and home not in p.parents:
        raise PermissionError("Access denied: outside home directory")
    return p


@app.route("/api/files")
def api_files_list():
    rel = request.args.get("path", ".")
    try:
        p = _safe_resolve(rel)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if not p.is_dir():
        return jsonify({"error": "Not a directory"}), 400
    items = []
    try:
        for child in sorted(p.iterdir()):
            try:
                items.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": child.stat().st_size if child.is_file() else 0,
                })
            except (PermissionError, OSError):
                pass
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    return jsonify({"files": items, "cwd": str(p)})


@app.route("/api/files/read")
def api_files_read():
    rel = request.args.get("path", "")
    if not rel:
        return jsonify({"error": "No path"}), 400
    try:
        p = _safe_resolve(rel)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if not p.is_file():
        return jsonify({"error": "Not a file"}), 404
    if p.stat().st_size > 2 * 1024 * 1024:
        return jsonify({"error": "File too large (>2 MB)"}), 400
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"content": content, "path": str(p)})


@app.route("/api/files/write", methods=["POST"])
def api_files_write():
    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    content = data.get("content", "")
    if not rel:
        return jsonify({"error": "No path"}), 400
    try:
        p = _safe_resolve(rel)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return jsonify({"ok": True, "path": str(p)})


@app.route("/api/files/delete", methods=["POST"])
def api_files_delete():
    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    if not rel:
        return jsonify({"error": "No path"}), 400
    try:
        p = _safe_resolve(rel)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if p.is_file():
        p.unlink()
    elif p.is_dir():
        import shutil
        shutil.rmtree(p)
    else:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/files/mkdir", methods=["POST"])
def api_files_mkdir():
    data = request.get_json(silent=True) or {}
    rel = data.get("path", "")
    if not rel:
        return jsonify({"error": "No path"}), 400
    try:
        p = _safe_resolve(rel)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    p.mkdir(parents=True, exist_ok=True)
    return jsonify({"ok": True, "path": str(p)})


# ---------- Terminal ----------

@app.route("/api/terminal", methods=["POST"])
def api_terminal():
    import subprocess
    data = request.get_json(silent=True) or {}
    cmd = data.get("command", "").strip()
    if not cmd:
        return jsonify({"error": "No command"}), 400
    blocked = ["format", "del /s", "rd /s", "rmdir /s", "rm -rf /"]
    for b in blocked:
        if b in cmd.lower():
            return jsonify({"error": f"Blocked: {b}"}), 403
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(Path.home()),
        )
        return jsonify({
            "output": result.stdout[-4000:] if result.stdout else "",
            "error": result.stderr[-2000:] if result.stderr else "",
            "code": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Command timed out (30s)"}), 408
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------- Guard, permissions, engine, diagnostics, workspace ----------

@app.before_request
def _local_same_origin_only():
    """Only this app's own page may drive the API: blocks other sites, DNS rebinding and generated previews."""
    if request.host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
        return jsonify({"error": "Unknown host"}), 403
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin")
    if origin and origin != request.host_url.rstrip("/"):
        return jsonify({"error": "Cross-origin request blocked"}), 403
    if (request.path.startswith("/api/") and request.get_data() and not request.is_json
            and request.path != "/api/uploads"):
        return jsonify({"error": "JSON body required"}), 415
    return None


@app.route("/api/permission-mode", methods=["GET", "POST"])
def api_permission_mode():
    cfg = load_config()
    if request.method == "POST":
        mode = (request.get_json(silent=True) or {}).get("mode")
        if mode not in permissions.MODES:
            return jsonify({"ok": False, "error": f"Unknown permission mode: {mode}"}), 400
        cfg["permission_mode"] = mode
        save_config(cfg)
    mode = permissions.normalize(cfg.get("permission_mode"))
    return jsonify({"ok": True, "mode": mode, "label": permissions.MODES[mode], "modes": permissions.MODES})


@app.route("/api/permissions")
def api_permissions():
    return jsonify(BROKER.pending())


@app.route("/api/permissions/<pid>", methods=["POST"])
def api_permission_answer(pid):
    decision = (request.get_json(silent=True) or {}).get("decision")
    if decision not in ("allow", "always", "deny"):
        return jsonify({"ok": False, "error": "decision must be allow, always or deny"}), 400
    if not BROKER.resolve(pid, decision):
        return jsonify({"ok": False, "error": "This request was already answered or has expired."}), 404
    return jsonify({"ok": True})


@app.route("/api/engine")
def api_engine():
    return jsonify(dict(ENGINE.state(), desktop=_engine_on()))


@app.route("/api/engine/show", methods=["POST"])
def api_engine_show():
    if not _engine_on():
        return jsonify({"ok": False, "error": "The arena.ai window exists only in the desktop app."}), 400
    ENGINE.show()
    return jsonify({"ok": True})


@app.route("/api/engine/hide", methods=["POST"])
def api_engine_hide():
    if _engine_on():
        ENGINE.close_pane()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    if chat_id:
        # Stop only this chat; other chats keep working.
        _chat_stop_event(str(chat_id)).set()
        cancelled = 0
    else:
        _stop.set()
        cancelled = BROKER.cancel_all()
    if _engine_on() and ENGINE.challenge == "recaptcha":
        ENGINE.cancel_check()
    return jsonify({"ok": True, "cancelled": cancelled})


@app.route("/api/doctor")
def api_doctor():
    """Check each outside contract the app relies on and explain failures in plain words."""
    checks = []

    def add(name, ok, detail):
        checks.append({"name": name, "ok": ok, "detail": detail})

    client = get_client()
    html = ""
    try:
        r = requests.get(contracts.ORIGIN, timeout=15, headers={"User-Agent": client.session.headers.get("User-Agent", "")})
        html = r.text
        add("arena.ai reachable", r.status_code == 200, f"HTTP {r.status_code}" if r.status_code == 200
            else contracts.classify_error(r.status_code, html[:4000])["message"])
    except requests.RequestException as e:
        add("arena.ai reachable", False, f"Network error: {e}")
    live = (ENGINE.page_site_key() if _engine_on() and ENGINE.ready() else None) or contracts.discover_site_keys(html).get("v3")
    if not live:
        add("reCAPTCHA key", None, "not found in the page; the engine falls back to the built-in key")
    else:
        add("reCAPTCHA key", True, "matches the built-in key" if live == contracts.KEYS["v3"]
            else "arena.ai uses a new key; the engine reads it from the page, so chats keep working")
    try:
        models = _catalog_models()
        add("Model catalog", bool(models), f"{len(models)} models" if models
            else "empty: arena.ai may have changed /nextjs-api/model-catalog")
    except Exception as e:
        add("Model catalog", False, str(e))
    st = ENGINE.state()
    if _engine_on():
        add("arena.ai engine", st["ready"], "ready" if st["ready"] else
            (f"waiting for a {st['challenge']} check: press Show arena.ai" if st["challenge"]
             else f"loading ({st['host'] or 'no page yet'})"))
    else:
        add("arena.ai engine", None, "off: run Arena-Desktop.exe for in-app sign-in and human checks")
    if _signed_in_somewhere(client):
        user, err = _check_session(client, force=True)
        add("Session", bool(user), (user.get("email") or "guest session") if user else (err or "not signed in"))
    else:
        add("Session", False, "not signed in")
    ws = _workspace_dir()
    try:
        ws.mkdir(parents=True, exist_ok=True)
        probe = ws / ".arena-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("Workspace writable", True, str(ws))
    except OSError as e:
        add("Workspace writable", False, f"{ws}: {e}")
    add("Contract overrides", None if _overrides else True,
        ", ".join(_overrides) if _overrides else "none (built-in contracts)")
    return jsonify({"checks": checks, "permission_mode": _perm_mode()})


def _allowed_root(raw) -> Optional[Path]:
    try:
        p = Path(str(raw or "")).expanduser().resolve()
    except (OSError, ValueError):
        return None
    for base in (workspace.PROJECTS_DIR.resolve(), _workspace_dir().resolve()):
        if p == base or base in p.parents:
            return p
    return None


@app.route("/api/workspace")
def api_workspace():
    root = _workspace_dir()
    return jsonify({"workspace": str(root), "exists": root.is_dir(), "projects_dir": str(workspace.PROJECTS_DIR),
                    "panel_root": str(_panel_workspace_root()), "projects": workspace.list_projects()})


def _workspace_panel_path(rel: str = "."):
    root = _panel_workspace_root()
    value = (rel or ".").strip()
    if value in ("", "."):
        return root, root
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        raise PermissionError("Workspace panel paths must be relative")
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("Access denied: outside the workspace folder")
    return target, root


@app.route("/api/workspace/tree")
def api_workspace_tree():
    try:
        target, root = _workspace_panel_path(request.args.get("path", "."))
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"Workspace is unavailable: {e}"}), 500
    if not target.is_dir():
        return jsonify({"error": "Not a directory"}), 400
    try:
        limit = max(1, min(500, int(request.args.get("limit", "300"))))
    except ValueError:
        limit = 300
    entries = []
    truncated = False
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    for child in children:
        if child.name == ".arena-write-test":
            continue
        if len(entries) >= limit:
            truncated = True
            break
        try:
            stat = child.stat()
            entries.append({
                "name": child.name,
                "path": child.relative_to(root).as_posix(),
                "is_dir": child.is_dir(),
                "size": stat.st_size if child.is_file() else 0,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        except OSError:
            continue
    rel = "." if target == root else target.relative_to(root).as_posix()
    return jsonify({"root": str(root), "path": rel, "entries": entries, "truncated": truncated})


@app.route("/api/workspace/file")
def api_workspace_file():
    try:
        target, root = _workspace_panel_path(request.args.get("path", ""))
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    if target.stat().st_size > 512 * 1024:
        return jsonify({"error": "File is too large for preview (512 KB limit)"}), 400
    try:
        data = target.read_bytes()
    except OSError as e:
        return jsonify({"error": str(e)}), 500
    if b"\x00" in data[:8192]:
        return jsonify({"error": "Binary files cannot be previewed as text"}), 415
    return jsonify({
        "path": target.relative_to(root).as_posix(),
        "name": target.name,
        "size": len(data),
        "content": data.decode("utf-8", errors="replace"),
    })


@app.route("/api/workspace/download")
def api_workspace_download():
    # Raw bytes for drag-from-tree-into-composer: the client wraps them in
    # a File and runs the normal attachment upload (same limits apply).
    try:
        target, _root = _workspace_panel_path(request.args.get("path", ""))
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    if target.stat().st_size > 30 * 1024 * 1024:
        return jsonify({"error": "File is too large to attach (30 MB limit)"}), 400
    return send_file(target, as_attachment=True, download_name=target.name)


@app.route("/api/workspace/preview/<path:subpath>")
def api_workspace_preview(subpath):
    # Inline serving for the workspace preview: relative assets of an HTML
    # page resolve inside the workspace like a tiny static server. Path
    # containment via _workspace_panel_path; the document itself is
    # sandboxed by CSP (opaque origin, scripts cannot reach the app API).
    try:
        target, _root = _workspace_panel_path(subpath)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    if not target.is_file():
        return jsonify({"error": "File not found"}), 404
    if target.stat().st_size > 30 * 1024 * 1024:
        return jsonify({"error": "File is too large to preview (30 MB limit)"}), 400
    import mimetypes
    mime, _enc = mimetypes.guess_type(target.name)
    resp = send_file(target, mimetype=mime or "application/octet-stream", conditional=True)
    resp.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-modals allow-popups"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@app.route("/api/workspace/set", methods=["POST"])
def api_workspace_set():
    payload = request.get_json(silent=True) or {}
    if payload.get("clear"):
        cfg = load_config()
        cfg.pop("workspace_dir", None)
        save_config(cfg)
        return jsonify({"ok": True, "workspace": str(_workspace_dir()), "cleared": True})
    raw = ((payload.get("path") or "")).strip().strip('"')
    if not raw:
        return jsonify({"ok": False, "error": "No folder given"}), 400
    root = Path(raw).expanduser()
    if not root.is_absolute():
        return jsonify({"ok": False, "error": "Use a full folder path, like C:\\Users\\you\\project"}), 400
    root = root.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        return jsonify({"ok": False, "error": "Pick a project folder, not a whole drive or your home folder."}), 400
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"ok": False, "error": f"Cannot use that folder: {e}"}), 400
    cfg = load_config()
    cfg["workspace_dir"] = str(root)
    save_config(cfg)
    return jsonify({"ok": True, "workspace": str(root)})


@app.route("/api/workspace/save", methods=["POST"])
def api_workspace_save():
    """The user pressed Save to disk on a code answer: that click is the approval."""
    data = request.get_json(silent=True) or {}
    key = (data.get("chat_id") or _current_chat_id, data.get("lane") or "a")
    item = _last_webdev.get(key)
    if not item:
        return jsonify({"ok": False, "error": "No code files from this chat to save. Send a Code message first."}), 404
    root = workspace.project_dir(key[0], item["title"], key[1], base=_chosen_ws())
    plan = workspace.plan_project(root, item["files"])
    written, deleted = workspace.apply_project(plan)
    return jsonify({"ok": True, "root": str(root), "written": written, "deleted": deleted,
                    "files": plan["files"], "preview": _preview_url(root)})


@app.route("/api/workspace/zip")
def api_workspace_zip():
    root = _allowed_root(request.args.get("path"))
    if not root or not root.is_dir():
        return jsonify({"error": "Only project folders can be zipped."}), 403
    return send_file(io.BytesIO(workspace.zip_dir(root)), mimetype="application/zip", as_attachment=True,
                     download_name=f"{root.parent.name}-{root.name}.zip")


@app.route("/api/workspace/open", methods=["POST"])
def api_workspace_open():
    root = _allowed_root((request.get_json(silent=True) or {}).get("path"))
    if not root or not root.is_dir():  # folders only: opening a file would run it
        return jsonify({"ok": False, "error": "Only project folders can be opened."}), 403
    if not hasattr(os, "startfile"):
        return jsonify({"ok": False, "error": "Opening folders works on Windows only.", "path": str(root)}), 400
    os.startfile(str(root))
    return jsonify({"ok": True})


# ---------- Arena account connectors (official, live from arena.ai) ----------
#
# arena.ai exposes its Agent Mode connectors under /api/connectors: a live
# catalog ({slug, displayName, description, logoUrl, authStatus}), a status
# endpoint, POST /:slug/link-token (Merge/AgentHandler link widget token),
# DELETE /:slug and POST /sync.  Fetching the catalog live means connectors
# Arena adds later appear here automatically.  Connecting runs the same
# AgentHandlerLink widget arena.ai's own page uses, inside the separate Arena
# window, so the authorization lands on the signed-in Arena account.  These
# account connectors serve Arena's native Agent on the website; the local
# Agent below uses local MCP servers instead.

_CONNECTOR_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_connector_link_pending: Optional[dict] = None

_LINK_WIDGET_JS = r"""
(function () {
  var TOKEN = __TOKEN__;
  window.__apLink = { state: 'loading', error: null };
  function fail(e) { window.__apLink = { state: 'error', error: String((e && e.message) || e) }; }
  function start() {
    try {
      var config = {
        linkToken: TOKEN,
        onSuccess: function () { window.__apLink = { state: 'success', error: null }; },
        onExit: function () {
          if (window.__apLink.state !== 'success') window.__apLink = { state: 'exit', error: null };
        },
        onValidationError: function (e) {
          window.__apLink = { state: 'error', error: String((e && (e.detail || e.message)) || 'validation error') };
        }
      };
      window.AgentHandlerLink.initialize(Object.assign({}, config, { onReady: function () {
        try {
          window.AgentHandlerLink.openLink(config);
          if (window.__apLink.state === 'loading') window.__apLink = { state: 'open', error: null };
        } catch (e) { fail(e); }
      }}));
    } catch (e) { fail(e); }
  }
  if (window.AgentHandlerLink) { start(); return 'started'; }
  var s = document.createElement('script');
  s.src = 'https://ah-cdn.merge.dev/initialize.js';
  s.onload = start;
  s.onerror = function () { fail('widget script failed to load'); };
  document.body.appendChild(s);
  return 'started';
})()
"""


def _valid_connector_slug(slug) -> bool:
    return isinstance(slug, str) and bool(_CONNECTOR_SLUG_RE.match(slug))


def _normalize_arena_connector(raw, statuses: Optional[dict] = None) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    slug = raw.get("slug")
    if not _valid_connector_slug(slug):
        return None
    status = raw.get("authStatus")
    if statuses and slug in statuses:
        status = statuses[slug]
    if status not in ("connected", "disconnected", "unusable"):
        status = "disconnected"
    name = raw.get("displayName")
    if not (isinstance(name, str) and name.strip()):
        name = slug
    description = raw.get("description") if isinstance(raw.get("description"), str) else ""
    logo = None
    for key in ("logoUrl", "logo_url", "iconUrl", "icon_url", "imageUrl", "image_url", "logo", "icon"):
        value = raw.get(key)
        if isinstance(value, dict):
            value = value.get("url") or value.get("src")
        if isinstance(value, str) and value.strip():
            logo = value.strip()
            break
    if logo:
        if logo.startswith("//"):
            logo = "https:" + logo
        elif logo.startswith("/"):
            logo = contracts.ORIGIN + logo
        if not logo.startswith(("https://", "http://", "data:image/")):
            logo = None
    return {"slug": slug, "name": name, "description": description,
            "logo_url": logo, "auth_status": status, "connected": status == "connected"}


def _fetch_arena_connectors():
    """Live Arena connector catalog merged with per-account statuses.

    Returns (connectors, error, http_status)."""
    data, error, status = _arena_get_json(contracts.PATHS["connectors"], timeout=35)
    if error:
        return None, error, status
    raw_list = data.get("connectors") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return None, "arena.ai returned an unexpected connectors reply", status or 502
    statuses = {}
    sdata, serr, _ = _arena_get_json(contracts.PATHS["connectors_status"], timeout=35)
    if not serr and isinstance(sdata, dict) and isinstance(sdata.get("connectors"), list):
        for item in sdata["connectors"]:
            if isinstance(item, dict) and isinstance(item.get("slug"), str):
                statuses[item["slug"]] = item.get("authStatus")
    out, seen = [], set()
    for raw in raw_list:
        norm = _normalize_arena_connector(raw, statuses)
        if norm and norm["slug"] not in seen:
            seen.add(norm["slug"])
            out.append(norm)
    return out, None, 200


def _connector_link_state() -> dict:
    raw = ENGINE.run_script("window.__apLink ? JSON.stringify(window.__apLink) : null")
    if isinstance(raw, str) and raw:
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if isinstance(data, dict):
            return data
    return {}


@app.route("/api/arena-connectors")
def api_arena_connectors():
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"ok": False, "connectors": [], "error": "Not signed in", "code": "auth"}), 401
    connectors, error, status = _fetch_arena_connectors()
    if error:
        code = 503 if status == 0 else (status or 502)
        return jsonify({"ok": False, "connectors": [], "error": error, "code": "arena_connectors"}), code
    return jsonify({"ok": True, "connectors": connectors, "source": "arena"})


@app.route("/api/arena-connectors/<slug>/connect", methods=["POST"])
def api_arena_connector_connect(slug):
    global _connector_link_pending
    if not _valid_connector_slug(slug):
        return jsonify({"ok": False, "error": "Invalid connector slug"}), 400
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"ok": False, "error": "Not signed in", "code": "auth"}), 401
    if not _engine_on():
        return jsonify({"ok": False, "code": "engine_required",
                        "error": "Connecting an Arena connector needs the desktop app: "
                                 "the authorization widget opens inside the Arena window.",
                        "fallback_url": contracts.ORIGIN + "/agent"}), 400
    if (_connector_link_pending and _connector_link_pending.get("slug") == slug
            and time.time() - _connector_link_pending.get("started", 0) < 600
            and _connector_link_state().get("state") in ("loading", "ready", "open")):
        ENGINE.show()
        return jsonify({"ok": True, "pending": True, "slug": slug})
    if not ENGINE.wait_ready(30):
        ENGINE.show()
        hint = (f"arena.ai is waiting for a {ENGINE.challenge} check" if ENGINE.challenge
                else "arena.ai window is still loading")
        return jsonify({"ok": False, "code": "engine_not_ready",
                        "error": f"{hint}. Finish it in the Arena window, then try again."}), 503
    quoted = urllib.parse.quote(slug, safe="")
    data, error, status = _arena_get_json(
        contracts.PATHS["connector_link_token"].format(slug=quoted), timeout=35, method="POST")
    if error:
        code = 503 if status == 0 else (status or 502)
        return jsonify({"ok": False, "error": error, "code": "arena_connectors"}), code
    token = data.get("linkToken") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token:
        return jsonify({"ok": False, "error": "arena.ai did not return a link token for this connector."}), 502
    ENGINE.show()
    result = ENGINE.run_script(_LINK_WIDGET_JS.replace("__TOKEN__", json.dumps(token)))
    if result not in (None, "started"):
        return jsonify({"ok": False, "code": "engine_script",
                        "error": "Could not open the authorization widget in the Arena window."}), 502
    _connector_link_pending = {"slug": slug, "started": time.time(), "synced": False}
    return jsonify({"ok": True, "pending": True, "slug": slug})


@app.route("/api/arena-connectors/<slug>/connect/poll")
def api_arena_connector_connect_poll(slug):
    global _connector_link_pending
    if not _valid_connector_slug(slug):
        return jsonify({"ok": False, "error": "Invalid connector slug"}), 400
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"ok": False, "error": "Not signed in", "code": "auth"}), 401
    pending = _connector_link_pending if _connector_link_pending and _connector_link_pending.get("slug") == slug else None
    state = _connector_link_state().get("state") if _engine_on() else None
    started = pending.get("started", 0) if pending else 0
    # arena.ai's own flow syncs accounts once the widget reports success.  A
    # delayed one-off sync also covers the state being unreadable (for example
    # the Arena page navigated while the widget was open).
    if pending and not pending.get("synced") and (state == "success" or (state is None and time.time() - started > 30)):
        _arena_get_json(contracts.PATHS["connectors_sync"], timeout=35, method="POST")
        pending["synced"] = True
    connectors, error, _status = _fetch_arena_connectors()
    entry = next((c for c in connectors or [] if c["slug"] == slug), None)
    connected = bool(entry and entry["connected"])

    def finish(payload):
        global _connector_link_pending
        _connector_link_pending = None
        if _engine_on():
            ENGINE.hide()
        return jsonify(payload)

    if connected:
        return finish({"ok": True, "done": True, "connected": True, "slug": slug,
                       "connectors": connectors or []})
    if state == "error":
        detail = _connector_link_state().get("error") or "authorization widget failed"
        return finish({"ok": False, "done": True, "connected": False, "outcome": "error",
                       "error": f"Arena connector authorization failed: {detail}"})
    if state == "exit" or not pending or time.time() - started > 600:
        outcome = "exit" if state == "exit" else "timeout"
        return finish({"ok": True, "done": True, "connected": False, "outcome": outcome, "slug": slug})
    if error:
        return jsonify({"ok": True, "done": False, "connected": False, "state": state, "warning": error})
    return jsonify({"ok": True, "done": False, "connected": False, "state": state, "slug": slug})


@app.route("/api/arena-connectors/<slug>/disconnect", methods=["POST"])
def api_arena_connector_disconnect(slug):
    if not _valid_connector_slug(slug):
        return jsonify({"ok": False, "error": "Invalid connector slug"}), 400
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"ok": False, "error": "Not signed in", "code": "auth"}), 401
    quoted = urllib.parse.quote(slug, safe="")
    _data, error, status = _arena_get_json(
        contracts.PATHS["connector"].format(slug=quoted), timeout=35, method="DELETE")
    if error:
        code = 503 if status == 0 else (status or 502)
        return jsonify({"ok": False, "error": error, "code": "arena_connectors"}), code
    connectors, _lerr, _ = _fetch_arena_connectors()
    return jsonify({"ok": True, "slug": slug, "connectors": connectors or []})


# ---------- Native Arena Agent ----------

_NATIVE_AGENT_FILL_JS = r"""
(function () {
  var text = %s;
  var tries = 0;
  function setNative(el, v) {
    var proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, v); else el.value = v;
    el.dispatchEvent(new InputEvent('input', {bubbles: true, data: v, inputType: 'insertText'}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  function attempt() {
    tries += 1;
    var el = document.querySelector('textarea:not([disabled])')
      || document.querySelector('[contenteditable="true"]');
    if (el) {
      el.focus();
      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') setNative(el, text);
      else {
        document.execCommand('selectAll', false, null);
        document.execCommand('insertText', false, text);
      }
      window.__apNativeFill = 'filled';
      return;
    }
    if (tries < 40) setTimeout(attempt, 500); else window.__apNativeFill = 'not-found';
  }
  attempt();
  return 'scheduled';
})()
"""


@app.route("/api/agent/native", methods=["POST"])
def api_agent_native():
    """Open Arena's native Agent Mode in the Arena window and pre-fill the prompt.

    The native agent runs on Arena's own runtime (separate from evaluations),
    so nothing shows up as a Direct chat and no single model is exposed.
    The local Agent loop remains available for folder tools.
    """
    client = get_client()
    if not _signed_in_somewhere(client):
        return jsonify({"error": "Not signed in. Press Sign in first.", "code": "auth"}), 401
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Empty message"}), 400
    fallback = f"{contracts.ORIGIN}/agent"
    if not _engine_on():
        return jsonify({"ok": False, "code": "engine_required", "fallback_url": fallback,
                        "error": "Native Arena Agent opens in the desktop app's arena.ai window."}), 400
    ENGINE.open_path("/agent")
    ENGINE.show()

    def _fill():
        try:
            if ENGINE.wait_ready(30):
                ENGINE.run_script(_NATIVE_AGENT_FILL_JS % json.dumps(message, ensure_ascii=False))
        except Exception:
            LOG.exception("native agent prompt fill failed")

    threading.Thread(target=_fill, daemon=True).start()
    return jsonify({"ok": True, "url": fallback})


# ---------- MCP connectors ----------

def _connector_error(e):
    if isinstance(e, KeyError):
        return jsonify({"ok": False, "error": str(e)}), 404
    if isinstance(e, ValueError):
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/connectors")
def api_connectors():
    return jsonify(CONNECTORS.list())


@app.route("/api/connectors/reload", methods=["POST"])
def api_connectors_reload():
    return jsonify(CONNECTORS.list())


@app.route("/api/connectors/config/open", methods=["POST"])
def api_connectors_config_open():
    try:
        path = connectors.open_config_file(CONNECTORS.config_path)
    except Exception as e:
        return _connector_error(e)
    return jsonify({"ok": True, "path": str(path)})


@app.route("/api/connectors/preset", methods=["POST"])
def api_connectors_preset():
    data = request.get_json(silent=True) or {}
    try:
        result = CONNECTORS.install_preset(str(data.get("preset_id") or ""), data.get("values") or {})
        server = None
        if data.get("connect", True):
            server = CONNECTORS.connect(str(data.get("preset_id") or ""), timeout=60)
            result = CONNECTORS.list()
        return jsonify(dict(result, server=server))
    except Exception as e:
        return _connector_error(e)


@app.route("/api/connectors/cli", methods=["POST"])
def api_connectors_cli():
    data = request.get_json(silent=True) or {}
    try:
        name = connectors.validate_server_name(str(data.get("name") or ""))
        result = CONNECTORS.add_cli(
            name,
            str(data.get("command") or ""),
            env=data.get("env") if isinstance(data.get("env"), dict) else {},
            cwd=str(data.get("cwd") or "") or None,
            description=str(data.get("description") or ""),
        )
        server = None
        if data.get("connect", True):
            server = CONNECTORS.connect(name, timeout=60)
            result = CONNECTORS.list()
        return jsonify(dict(result, server=server))
    except Exception as e:
        return _connector_error(e)


@app.route("/api/connectors/<name>/connect", methods=["POST"])
def api_connector_connect(name):
    try:
        server = CONNECTORS.connect(name, timeout=60)
        return jsonify(dict(CONNECTORS.list(), server=server))
    except Exception as e:
        return _connector_error(e)


@app.route("/api/connectors/<name>/test", methods=["POST"])
def api_connector_test(name):
    return api_connector_connect(name)


@app.route("/api/connectors/<name>/disconnect", methods=["POST"])
def api_connector_disconnect(name):
    CONNECTORS.disconnect(name)
    return jsonify(CONNECTORS.list())


@app.route("/api/connectors/<name>/enabled", methods=["POST"])
def api_connector_enabled(name):
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(CONNECTORS.set_enabled(name, bool(data.get("enabled"))))
    except Exception as e:
        return _connector_error(e)


@app.route("/api/connectors/<name>/delete", methods=["POST"])
def api_connector_delete(name):
    try:
        return jsonify(CONNECTORS.remove(name))
    except Exception as e:
        return _connector_error(e)


@app.route("/api/media/save", methods=["POST"])
def api_media_save():
    """Download a generated image, video or audio file into the projects folder."""
    url = str((request.get_json(silent=True) or {}).get("url") or "")
    folder = workspace.PROJECTS_DIR / "media"
    try:
        if url.startswith("data:"):
            head, _, payload = url.partition(",")
            mime = head[5:].split(";")[0]
            blob, stem = base64.b64decode(payload), "media"
        else:
            u = urllib.parse.urlparse(url)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or not host or host == "localhost" or host.startswith(("127.", "10.", "192.168.", "169.254.")):
                return jsonify({"ok": False, "error": "Only https media links can be saved."}), 400
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            blob = b""
            for chunk in r.iter_content(1 << 16):
                blob += chunk
                if len(blob) > 300 * 1024 * 1024:
                    return jsonify({"ok": False, "error": "File is larger than 300 MB."}), 413
            mime = r.headers.get("Content-Type", "").split(";")[0]
            stem = Path(u.path).stem or "media"
    except (requests.RequestException, ValueError) as e:
        return jsonify({"ok": False, "error": f"Download failed: {e}"}), 502
    ext = mimetypes.guess_extension(mime) or Path(urllib.parse.urlparse(url).path).suffix or ".bin"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{workspace.slug(stem)}-{uuid.uuid4().hex[:6]}{ext}"
    target.write_bytes(blob)
    return jsonify({"ok": True, "path": str(target), "folder": str(folder)})


def run_server(port: int = 5000, open_browser: bool = True):
    """Start the Flask server and optionally open browser."""
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
