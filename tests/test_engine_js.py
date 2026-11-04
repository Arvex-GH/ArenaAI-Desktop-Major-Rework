"""The engine's injected JS must actually run, not just look right.

v4.3.1 shipped a _FETCH_JS whose body referenced an `extraHeaders` variable
that was never declared as a parameter: every engine request died with a
ReferenceError before fetch() and the UI only showed a generic
"Can't reach arena.ai". Python fake engines cannot catch that class of bug,
so these tests render the JS exactly like BrowserEngine.request() does and
execute it in node against minimal page stubs.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from arena import contracts
from arena import engine as engine_mod

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is required to execute engine JS")

_HARNESS = r"""
var pushes = [];
var fetchCall = null;
global.window = { pywebview: { api: { push: function (rid, seq, kind, data) { pushes.push([kind, data]); } } } };
global.document = { querySelector: function () { return null; } };
global.fetch = function (path, opts) {
  fetchCall = { path: path, opts: opts };
  return Promise.resolve({ status: 207, body: null });
};
(async function () {
__RENDERED__
  await new Promise(function (r) { setTimeout(r, 150); });
  var out = {
    pushes: pushes,
    fetchPath: fetchCall && fetchCall.path,
    method: fetchCall && fetchCall.opts.method,
    headers: fetchCall && fetchCall.opts.headers,
    body: fetchCall && fetchCall.opts.body
  };
  console.log("RESULT " + JSON.stringify(out));
})();
"""


def _run_harness(rendered_js: str) -> dict:
    script = _HARNESS.replace("__RENDERED__", rendered_js)
    fd, name = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run([NODE, name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(name)
    assert proc.returncode == 0, proc.stderr
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[7:])
    raise AssertionError(f"no RESULT line in node output: {proc.stdout!r} {proc.stderr!r}")


def _render_fetch(body=None, headers=None, method=None, path="/api/me", cx=False) -> str:
    # Mirrors BrowserEngine.request() argument building one-to-one.
    args = ",".join(json.dumps(a) for a in (
        "test-rid", path, method or ("POST" if body is not None else "GET"),
        body, contracts.KEYS["v3"], contracts.RECAPTCHA_ACTION, headers,
        bool(cx and body is not None), engine_mod._CX_MODULE_ID))
    return engine_mod._FETCH_JS % args


def test_fetch_js_declares_every_argument_request_passes():
    m = re.search(r"\(function \(([^)]*)\)", engine_mod._FETCH_JS)
    assert m, "could not find _FETCH_JS signature"
    params = [p.strip() for p in m.group(1).split(",")]
    assert params == ["rid", "path", "method", "body", "v3key", "action",
                      "extraHeaders", "wantCx", "cxModuleId"]


def test_fetch_js_get_without_body_or_headers():
    out = _run_harness(_render_fetch())
    kinds = [k for k, _ in out["pushes"]]
    assert "error" not in kinds, out["pushes"]
    assert out["pushes"][0] == ["status", "207|{}"]
    assert out["pushes"][-1] == ["end", None]
    assert out["fetchPath"] == "/api/me"
    assert out["method"] == "GET"
    assert out["headers"] == {}
    assert out.get("body") is None


def test_fetch_js_post_merges_extra_headers():
    out = _run_harness(_render_fetch(body={"hello": "world"},
                                     headers={"X-Agent-Browser-Attempt-Id": "abc-123"}))
    kinds = [k for k, _ in out["pushes"]]
    assert "error" not in kinds, out["pushes"]
    assert out["pushes"][0] == ["status", "207|{}"]
    assert out["method"] == "POST"
    assert out["headers"]["Content-Type"] == "application/json"
    assert out["headers"]["X-Agent-Browser-Attempt-Id"] == "abc-123"
    assert json.loads(out["body"]) == {"hello": "world"}


def _run_node(script: str) -> dict:
    fd, name = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run([NODE, name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(name)
    assert proc.returncode == 0, proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    return json.loads(line[7:])


def test_fetch_js_attaches_page_cx_when_requested():
    """cx=True: the page's own client-interaction module fills body.cx.

    Uses the same fake Turbopack runtime as the preflight test; the fetch
    stub captures the final request body.
    """
    script = (_FAKE_TURBOPACK
              + r"""
var pushesF = [];
window.pywebview.api.push = function (rid, seq, kind, data) { pushesF.push([kind, data]); };
var fetchCallF = null;
global.fetch = function (path, opts) {
  fetchCallF = { path: path, opts: opts };
  return Promise.resolve({ status: 200, body: null, headers: { get: function () { return null; } } });
};
"""
              + "\n(async function () {\n"
              + _render_fetch(body={"message": "hi"}, path="/api/chat", cx=True)
              + "\nawait new Promise(function (r) { setTimeout(r, 1200); });"
              + "\nconsole.log('RESULT ' + JSON.stringify({body: fetchCallF && fetchCallF.opts.body, pushes: pushesF}));"
              + "\n})();\n")
    out = _run_node(script)
    assert json.loads(out["body"]) == {"message": "hi", "cx": "cxblob:programmatic"}
    assert out["pushes"][0] == ["status", "200|{}"]


def test_fetch_js_without_cx_flag_leaves_body_alone():
    out = _run_harness(_render_fetch(body={"hello": "world"}))
    assert json.loads(out["body"]) == {"hello": "world"}


def test_engine_response_parses_status_headers():
    import queue as queue_mod

    class FakeEngine:
        def _close(self, rid):
            pass

    q = queue_mod.Queue()
    q.put(("status", '429|{"ratelimit-modality": "video", "retry-after": "3600"}'))
    resp = engine_mod.EngineResponse(FakeEngine(), "rid", q, 5)
    assert resp.status_code == 429
    assert resp.headers == {"ratelimit-modality": "video", "retry-after": "3600"}

    q2 = queue_mod.Queue()
    q2.put(("status", 200))
    resp2 = engine_mod.EngineResponse(FakeEngine(), "rid", q2, 5)
    assert resp2.status_code == 200
    assert resp2.headers == {}


def test_request_itself_renders_runnable_js(monkeypatch):
    """Go through the real BrowserEngine.request() so its own arg building is covered."""
    captured = {}

    class FakeWindow:
        def evaluate_js(self, js):
            captured["js"] = js
            return 1

    class FakeResponse:
        def __init__(self, engine, rid, q, timeout):
            captured["rid"] = rid

    monkeypatch.setattr(engine_mod, "EngineResponse", FakeResponse)
    eng = engine_mod.BrowserEngine()
    eng.window = FakeWindow()
    eng.request("/nextjs-api/stream/create-chat", body={"x": 1}, method="POST",
                v3_action="agentic_chat_submit",
                headers={"X-Agent-Browser-Attempt-Id": "abc-123"})
    out = _run_harness(captured["js"])
    kinds = [k for k, _ in out["pushes"]]
    assert "error" not in kinds, out["pushes"]
    assert out["fetchPath"] == "/nextjs-api/stream/create-chat"
    assert out["headers"]["X-Agent-Browser-Attempt-Id"] == "abc-123"
    assert json.loads(out["body"]) == {"x": 1}


def _node_check(js: str):
    fd, name = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(js)
        proc = subprocess.run([NODE, "--check", name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(name)
    assert proc.returncode == 0, proc.stderr


def test_check_js_parses():
    _node_check(engine_mod._CHECK_JS % ",".join(
        json.dumps(a) for a in (contracts.KEYS["v2"], contracts.RECAPTCHA_ACTION)))


def test_drive_js_parses():
    """The page-drive scripts must at least be valid JS as rendered.

    They need a real DOM to execute, so node can only parse them here; the
    drive logic itself is defensive and reports diagnostics at runtime.
    """
    _node_check(engine_mod._DRIVE_HOOK_JS)
    _node_check(engine_mod._DRIVE_STATE_JS)
    _node_check(engine_mod._DRIVE_SEND_JS.replace(
        "__ARENA_TEXT__", json.dumps("Привет, агент! Собери лендинг.")))


def test_drive_hook_watches_create_and_append_only():
    js = engine_mod._DRIVE_HOOK_JS
    assert "/nextjs-api/stream/create-chat" in js
    assert "/in/append" in js
    assert "drive_event" in js
    # observation only: the wrapper must never rewrite the request
    assert "origFetch(input, init)" in js


def _render_preflight():
    opts = json.dumps({"action": "agentic_chat_submit",
                       "v3key": contracts.KEYS["v3"],
                       "moduleId": engine_mod._CX_MODULE_ID})
    return engine_mod._PREFLIGHT_JS.replace("__ARENA_OPTS__", opts)


def test_preflight_js_parses():
    _node_check(_render_preflight())


# Minimal stand-in for arena.ai's Turbopack runtime semantics, transcribed
# from the public runtime chunk: a [path, id..., factory] push registers
# factories; a [path, {otherChunks, runtimeModuleIds}] push executes the
# listed modules with a context exposing .i() for imports.
_FAKE_TURBOPACK = r"""
var events = [];
global.window = globalThis;
window.pywebview = { api: { drive_event: function (kind, payload) {
  events.push([kind, JSON.parse(payload)]);
} } };
global.document = { querySelector: function () { return null; } };
var factories = {};
var executed = {};
function makeCtx() {
  return {
    i: function (id) {
      if (id === 385144) return {
        a: function (snap) { return 'cxblob:' + snap.kind; },
        k: function () { return {kind: 'programmatic'}; }
      };
      throw new Error('unknown module ' + id);
    }
  };
}
function execModule(id) {
  if (executed[id]) return;
  executed[id] = true;
  factories[id](makeCtx());
}
globalThis.TURBOPACK = {
  push: function (t) {
    if (!Array.isArray(t)) return;
    if (t.length === 2 && t[1] && typeof t[1] === 'object' &&
        Array.isArray(t[1].runtimeModuleIds)) {
      t[1].runtimeModuleIds.forEach(execModule);
      return;
    }
    var i = 1, ids = [];
    while (i < t.length && typeof t[i] !== 'function') { ids.push(t[i]); i++; }
    if (i < t.length) ids.forEach(function (id) { factories[id] = t[i]; });
  }
};
"""


def test_preflight_acquires_cx_through_turbopack_runtime():
    script = (_FAKE_TURBOPACK
              + "\n(async function () {\n"
              + _render_preflight()
              + "\nawait new Promise(function (r) { setTimeout(r, 500); });"
              + "\nconsole.log('RESULT ' + JSON.stringify(events));"
              + "\n})();\n")
    fd, name = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run([NODE, name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(name)
    assert proc.returncode == 0, proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    events = json.loads(line[7:])
    assert len(events) == 1 and events[0][0] == "preflight"
    payload = events[0][1]
    assert payload["cx"] == "cxblob:programmatic"
    assert payload["cxLen"] == len("cxblob:programmatic")
    trace = " ".join(payload["trace"])
    assert "probe_registered" in trace and "probe_exec_pushed" in trace
    assert "cx_type=string" in trace


def _render_vote():
    opts = json.dumps({"args": {"evaluationSessionId": "sess-1", "messageAId": "a-1",
                                "messageBId": "b-1", "value": "model_a"},
                       "v3key": contracts.KEYS["v3"],
                       "moduleId": engine_mod._VOTE_MODULE_ID,
                       "actionId": engine_mod._VOTE_ACTION_ID,
                       "recaptchaAction": engine_mod._VOTE_RECAPTCHA_ACTION})
    return engine_mod._VOTE_JS.replace("__ARENA_OPTS__", opts)


def test_vote_js_parses():
    _node_check(_render_vote())


_FAKE_VOTE_RUNTIME = r"""
var events = [];
var captured = {};
global.window = globalThis;
window.pywebview = { api: { drive_event: function (kind, payload) {
  events.push([kind, JSON.parse(payload)]);
} } };
window.grecaptcha = { enterprise: {
  ready: function (cb) { cb(); },
  execute: function (key, opts) {
    captured.recaptcha = { key: key, action: opts && opts.action };
    return Promise.resolve('tok-123');
  }
} };
global.document = { querySelector: function () { return null; } };
var factories = {};
var executed = {};
function makeCtx() {
  return {
    i: function (id) {
      if (id === 29948) return {
        callServer: function () {},
        findSourceMapURL: undefined,
        createServerReference: function (actionId, callServer, unused, findMap, name) {
          captured.ref = { actionId: actionId, name: name };
          return function (args) {
            captured.args = args;
            return Promise.resolve({ success: true, data: {
              modelAId: 'mid-a', modelBId: 'mid-b', arenaId: 'arena-1',
              revealedModels: [{ id: 'mid-a', name: 'Alpha' }, { id: 'mid-b', name: 'Beta' }] } });
          };
        }
      };
      throw new Error('unknown module ' + id);
    }
  };
}
function execModule(id) {
  if (executed[id]) return;
  executed[id] = true;
  factories[id](makeCtx());
}
globalThis.TURBOPACK = {
  push: function (t) {
    if (!Array.isArray(t)) return;
    if (t.length === 2 && t[1] && typeof t[1] === 'object' &&
        Array.isArray(t[1].runtimeModuleIds)) {
      t[1].runtimeModuleIds.forEach(execModule);
      return;
    }
    var i = 1, ids = [];
    while (i < t.length && typeof t[i] !== 'function') { ids.push(t[i]); i++; }
    if (i < t.length) ids.forEach(function (id) { factories[id] = t[i]; });
  }
};
"""


def test_vote_probe_calls_pairwise_server_action_with_page_token():
    script = (_FAKE_VOTE_RUNTIME
              + "\n(async function () {\n"
              + _render_vote()
              + "\nawait new Promise(function (r) { setTimeout(r, 800); });"
              + "\nconsole.log('RESULT ' + JSON.stringify({events: events, captured: captured}));"
              + "\nprocess.exit(0);"
              + "\n})();\n")
    fd, name = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run([NODE, name], capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(name)
    assert proc.returncode == 0, proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT "))
    out = json.loads(line[7:])
    events, captured = out["events"], out["captured"]
    assert len(events) == 1 and events[0][0] == "vote"
    payload = events[0][1]
    assert payload["ok"] is True
    result = payload["result"]
    assert result["success"] is True
    assert result["modelAId"] == "mid-a" and result["modelBId"] == "mid-b"
    assert result["revealed"][0]["name"] == "Alpha"
    # The reference is the site's own Server Action, called like the site does.
    assert captured["ref"] == {"actionId": engine_mod._VOTE_ACTION_ID,
                               "name": "createPairwiseFeedback"}
    assert captured["args"]["evaluationSessionId"] == "sess-1"
    assert captured["args"]["messageAId"] == "a-1" and captured["args"]["messageBId"] == "b-1"
    assert captured["args"]["value"] == "model_a"
    assert captured["args"]["recaptchaV3Token"] == "tok-123"
    assert captured["recaptcha"]["action"] == "pairwise_feedback"


_NEW_ACTION_ID = "0123456789abcdef0123456789abcdef01234567"

_FAKE_VOTE_RUNTIME_STALE = r"""
var events = [];
var captured = { refs: [] };
global.window = globalThis;
window.pywebview = { api: { drive_event: function (kind, payload) {
  events.push([kind, JSON.parse(payload)]);
} } };
window.grecaptcha = { enterprise: {
  ready: function (cb) { cb(); },
  execute: function () { return Promise.resolve('tok'); }
} };
global.document = { querySelector: function () { return null; }, scripts: [] };
global.location = { origin: 'https://arena.ai' };
var fetchRound = 0;
global.fetch = function (url) {
  fetchRound += 1;
  // First discovery round fails (chunks unreachable): the probe must fall
  // back to the bundled action id. The second round serves the fresh
  // deployment's vote chunk with the rotated Turbopack-wrapped binding.
  if (String(url).indexOf('votechunk.js') !== -1) {
    return Promise.resolve({ text: function () {
      return Promise.resolve('(0,s.createServerReference)("__NEWID__",s.callServer,void 0,s.findSourceMapURL,"createPairwiseFeedback")');
    } });
  }
  if (fetchRound === 1) return Promise.reject(new Error('network hiccup'));
  return Promise.resolve({ text: function () {
    return Promise.resolve('<html><script src="/_next/static/immutable/chunks/votechunk.js"></script></html>');
  } });
};
var factories = {};
var executed = {};
function makeCtx() {
  return {
    i: function (id) {
      if (id === 29948) return {
        callServer: function () {},
        findSourceMapURL: undefined,
        createServerReference: function (actionId, callServer, unused, findMap, name) {
          captured.refs.push(actionId);
          return function (args) {
            if (actionId !== '__NEWID__') {
              return Promise.reject(new Error('UnrecognizedActionError: Server Action "' + actionId + '" was not found on the server.'));
            }
            return Promise.resolve({ success: true, data: {
              modelAId: 'mid-a', modelBId: 'mid-b', arenaId: 'arena-1',
              revealedModels: [{ id: 'mid-a', name: 'Alpha' }] } });
          };
        }
      };
      throw new Error('unknown module ' + id);
    }
  };
}
function execModule(id) {
  factories[id](makeCtx());
}
globalThis.TURBOPACK = {
  push: function (t) {
    if (!Array.isArray(t)) return;
    if (t.length === 2 && t[1] && typeof t[1] === 'object' &&
        Array.isArray(t[1].runtimeModuleIds)) {
      t[1].runtimeModuleIds.forEach(execModule);
      return;
    }
    var i = 1, ids = [];
    while (i < t.length && typeof t[i] !== 'function') { ids.push(t[i]); i++; }
    if (i < t.length) ids.forEach(function (id) { factories[id] = t[i]; });
  }
};
"""


def test_vote_retries_with_rediscovered_action_id_after_stale():
    """Arena redeploys rotate Server Action ids (the v4.7 symptom).

    Round 1: discovery unreachable → stale bundled id → UnrecognizedAction.
    The probe must clear its caches, rediscover the Turbopack-wrapped
    binding from the fresh chunk list, and succeed on retry.
    """
    opts = json.dumps({"args": {"evaluationSessionId": "sess-1", "messageAId": "a-1",
                                "messageBId": "b-1", "value": "model_a"},
                       "v3key": contracts.KEYS["v3"],
                       "moduleId": engine_mod._VOTE_MODULE_ID,
                       "actionId": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                       "recaptchaAction": engine_mod._VOTE_RECAPTCHA_ACTION})
    vote_js = engine_mod._VOTE_JS.replace("__ARENA_OPTS__", opts)
    runtime = _FAKE_VOTE_RUNTIME_STALE.replace("__NEWID__", _NEW_ACTION_ID)
    script = (runtime
              + "\n(async function () {\n"
              + vote_js
              + "\nawait new Promise(function (r) { setTimeout(r, 1500); });"
              + "\nconsole.log('RESULT ' + JSON.stringify({events: events, captured: captured}));"
              + "\nprocess.exit(0);"
              + "\n})();\n")
    out = _run_node(script)
    events, captured = out["events"], out["captured"]
    assert len(events) == 1 and events[0][0] == "vote"
    payload = events[0][1]
    assert payload["ok"] is True, payload
    assert payload["result"]["success"] is True
    trace = " ".join(payload["trace"])
    assert "action=fallback" in trace and "action=stale_retry" in trace and "action=discovered" in trace
    assert captured["refs"] == ["deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", _NEW_ACTION_ID]


def _render_archive(session_id="sess-9"):
    opts = json.dumps({"sessionId": session_id,
                       "moduleId": engine_mod._VOTE_MODULE_ID,
                       "actionId": engine_mod._ARCHIVE_ACTION_ID,
                       "actionName": engine_mod._ARCHIVE_ACTION_NAME})
    return engine_mod._ARCHIVE_JS.replace("__ARENA_OPTS__", opts)


def test_archive_js_parses():
    _node_check(_render_archive())


_FAKE_ARCHIVE_RUNTIME = r"""
var events = [];
var captured = {};
global.window = globalThis;
window.pywebview = { api: { drive_event: function (kind, payload) {
  events.push([kind, JSON.parse(payload)]);
} } };
global.document = { querySelector: function () { return null; }, scripts: [] };
global.location = { origin: 'https://arena.ai' };
global.fetch = function () { return Promise.reject(new Error('offline')); };
var factories = {};
function makeCtx() {
  return {
    i: function (id) {
      if (id === 29948) return {
        callServer: function () {},
        findSourceMapURL: undefined,
        createServerReference: function (actionId, callServer, unused, findMap, name) {
          captured.ref = { actionId: actionId, name: name };
          return function (sessionId) {
            captured.sessionId = sessionId;
            return Promise.resolve({ success: true });
          };
        }
      };
      throw new Error('unknown module ' + id);
    }
  };
}
globalThis.TURBOPACK = {
  push: function (t) {
    if (!Array.isArray(t)) return;
    if (t.length === 2 && t[1] && typeof t[1] === 'object' &&
        Array.isArray(t[1].runtimeModuleIds)) {
      t[1].runtimeModuleIds.forEach(function (id) { factories[id](makeCtx()); });
      return;
    }
    var i = 1, ids = [];
    while (i < t.length && typeof t[i] !== 'function') { ids.push(t[i]); i++; }
    if (i < t.length) ids.forEach(function (id) { factories[id] = t[i]; });
  }
};
"""


def test_archive_probe_calls_archive_server_action_with_session_id():
    script = (_FAKE_ARCHIVE_RUNTIME
              + "\n(async function () {\n"
              + _render_archive("sess-42")
              + "\nawait new Promise(function (r) { setTimeout(r, 900); });"
              + "\nconsole.log('RESULT ' + JSON.stringify({events: events, captured: captured}));"
              + "\nprocess.exit(0);"
              + "\n})();\n")
    out = _run_node(script)
    events, captured = out["events"], out["captured"]
    assert len(events) == 1 and events[0][0] == "archive"
    assert events[0][1]["ok"] is True
    assert captured["ref"] == {"actionId": engine_mod._ARCHIVE_ACTION_ID,
                               "name": "archiveEvaluationSession"}
    assert captured["sessionId"] == "sess-42"
