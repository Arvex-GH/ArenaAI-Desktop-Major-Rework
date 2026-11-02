"""Unit, edge-case and route tests for the desktop core: contracts, permissions,
agent tools, workspace mirroring, engine stream ordering and the Flask API."""

import json
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena import agent, contracts, permissions, workspace  # noqa: E402
from arena.api import generate_uuidv7  # noqa: E402
from arena.engine import BrowserEngine, EngineResponse  # noqa: E402

KEY = "6LeTGMcsAAAAALuIlkVwIxaAuZA8VledA6d3Nnb0"


def counter():
    it = iter(range(1000))
    return lambda: f"id-{next(it)}"


# ---------- contracts.build_chat ----------

def test_battle_new_omits_model_a_and_has_b_message():
    path, body = contracts.build_chat(eval_id="E", new=True, mode="battle", modality="chat", content="hi", ids=counter())
    assert path == contracts.PATHS["create"]
    assert body["mode"] == "battle" and body["id"] == "E"
    assert "modelAId" not in body and "modelBMessageId" in body
    assert body["recaptchaV3Token"] is None and "recaptchaV2Token" not in body
    assert body["userMessage"]["content"] == "hi"


def test_direct_new_uses_direct_battle_with_one_model():
    _, body = contracts.build_chat(eval_id="E", new=True, mode="direct-battle", modality="webdev", content="x",
                                   ids=counter(), model_a="M")
    assert body["modelAId"] == "M" and body["mode"] == "direct-battle" and body["modality"] == "webdev"
    assert "modelBMessageId" not in body and "modelBId" not in body


def test_side_by_side_needs_both_models():
    with pytest.raises(ValueError):
        contracts.build_chat(eval_id="E", new=True, mode="side-by-side", modality="chat", content="x", ids=counter(), model_a="A")
    _, body = contracts.build_chat(eval_id="E", new=True, mode="side-by-side", modality="chat", content="x",
                                   ids=counter(), model_a="A", model_b="B")
    assert body["modelAId"] == "A" and body["modelBId"] == "B" and "modelBMessageId" in body


def test_direct_without_model_is_rejected():
    with pytest.raises(ValueError):
        contracts.build_chat(eval_id="E", new=True, mode="direct-battle", modality="chat", content="x", ids=counter())


def test_follow_up_posts_to_eval_without_mode():
    path, body = contracts.build_chat(eval_id="E1", new=False, mode="battle", modality="chat", content="x", ids=counter())
    assert path == contracts.PATHS["post"].format(eval_id="E1")
    assert "mode" not in body


def test_v2_token_replaces_v3():
    _, body = contracts.build_chat(eval_id="E", new=True, mode="battle", modality="chat", content="x", ids=counter(), v2_token="T")
    assert body["recaptchaV2Token"] == "T" and "recaptchaV3Token" not in body


def test_message_ids_sort_after_eval_id():
    eval_id = generate_uuidv7()
    _, body = contracts.build_chat(eval_id=eval_id, new=True, mode="battle", modality="chat", content="x", ids=generate_uuidv7)
    assert eval_id < body["userMessageId"] < body["modelAMessageId"] < body["modelBMessageId"]


def test_ui_maps():
    assert contracts.api_mode("agent") == "direct-battle" and contracts.api_mode("direct") == "direct-battle"
    assert contracts.api_mode("battle") == "battle"
    assert contracts.api_modality("code") == "webdev" and contracts.api_modality("auto") == "auto"
    assert contracts.api_modality("video") == "video"
    with pytest.raises(ValueError):
        contracts.api_mode("nope")
    with pytest.raises(ValueError):
        contracts.api_modality("nope")


def test_ui_selection_matrix_and_legacy_normalization():
    assert contracts.validate_ui_selection("battle", "video") == ("battle", "video")
    assert contracts.validate_ui_selection("agent", "text") == ("direct-battle", "chat")
    for mode, modality in [("direct", "video"), ("side-by-side", "video"), ("agent", "image")]:
        with pytest.raises(ValueError):
            contracts.validate_ui_selection(mode, modality)
    assert contracts.normalize_ui_selection("direct", "video") == ("battle", "video")
    assert contracts.normalize_ui_selection("agent", "code") == ("agent", "text")


def test_auto_is_battle_only_and_picker_matches_arena_reference():
    assert contracts.validate_ui_selection("battle", "auto") == ("battle", "auto")
    for mode in ("direct", "side-by-side", "agent"):
        with pytest.raises(ValueError, match="Auto is available only in Battle"):
            contracts.validate_ui_selection(mode, "auto")

    # Agent wins over every stale work type; no Text-first ordering is needed.
    for modality in ("auto", "video", "image", "code"):
        assert contracts.normalize_ui_selection("agent", modality) == ("agent", "text")
    assert contracts.normalize_ui_selection("direct", "auto") == ("direct", "text")

    pick = contracts.pick_auto_modality
    assert pick(None) == "chat"
    assert pick({"text": 0.7, "image": 0.2}) == "chat"
    assert pick({"text": 0.2, "code": 0.2, "image": 0.1}) == "chat"  # Text wins ties.
    assert pick({"text": 0.1, "code": 0.25, "image": 0.65}) == "webdev"
    assert pick({"text": 0.1, "search": 0.7, "image": 0.2}) == "search"
    # With an image, Arena disables Search and renormalizes before the Code
    # threshold: 0.20 / (0.10 + 0.20) crosses the 0.25 Code threshold.
    assert pick({"text": 0.10, "code": 0.20, "search": 0.70}, has_image=True) == "webdev"
    assert pick({"text": 0.30, "search": 0.90, "image": 0.50}, has_image=True) == "image"
    assert pick({"text": "bad", "video": "0.9"}) == "video"


def test_build_chat_rejects_server_selected_or_duplicate_models():
    with pytest.raises(ValueError):
        contracts.build_chat(eval_id="E", new=True, mode="battle", modality="chat", content="x", ids=counter(), model_a="A")
    with pytest.raises(ValueError):
        contracts.build_chat(eval_id="E", new=True, mode="side-by-side", modality="chat", content="x", ids=counter(),
                             model_a="A", model_b="A")
    with pytest.raises(ValueError):
        contracts.build_chat(eval_id="E", new=True, mode="direct-battle", modality="chat", content="x", ids=counter(),
                             model_a="A", model_b="B")


# ---------- stream parsing and errors ----------

def test_parse_line_lanes_and_fallbacks():
    assert contracts.parse_line('a0:"hi"') == ("a", "text", "hi")
    assert contracts.parse_line('b0:"yo"') == ("b", "text", "yo")
    assert contracts.parse_line('0:"x"') == ("a", "text", "x")
    lane, kind, val = contracts.parse_line('b2:[{"type":"webdev"}]')
    assert (lane, kind) == ("b", "data") and val[0]["type"] == "webdev"
    assert contracts.parse_line("ag:not json") == ("a", "reasoning", "not json")
    for junk in ("", "garbage", "zz:1", "a"):
        assert contracts.parse_line(junk) is None


def test_line_splitter_handles_split_chunks():
    s = contracts.LineSplitter()
    assert s.feed('a0:"h') == []
    assert s.feed('i"\nb0') == ['a0:"hi"']
    assert s.flush() == ["b0"]
    assert s.flush() == []


@pytest.mark.parametrize("status,text,code", [
    (0, "boom", "network"),
    (403, '{"error":"recaptcha validation failed"}', "challenge"),
    (403, "<title>Just a moment...</title>", "cloudflare"),
    (401, "", "auth"),
    (429, "", "rate_limit"),
    (400, '{"error":"Cannot pass model for battle mode"}', "bad_request"),
    (404, "", "eval_gone"),
    (500, "", "server"),
    (403, "nope", "forbidden"),
])
def test_classify_error(status, text, code):
    err = contracts.classify_error(status, text)
    assert err["code"] == code and err["message"]


def test_rate_limit_suggests_wait():
    assert contracts.classify_error(429, "")["wait"] >= 30


def test_rate_limit_kinds_from_arena_body_and_headers():
    import time as _time

    daily = contracts.classify_error(
        429, json.dumps({"error": "daily spend limit reached"}),
        {"retry-after": "7200"})
    assert daily["code"] == "rate_limit" and "daily usage limit" in daily["message"]
    assert 7000 < daily["wait"] <= 7200

    video = contracts.classify_error(
        429, json.dumps({"error": "rate limit reached"}),
        {"ratelimit-modality": "video",
         "ratelimit-reset": str(int(_time.time()) + 3600)})
    assert "Video rate limit" in video["message"] and 3500 < video["wait"] <= 3600

    model = contracts.classify_error(
        429, json.dumps({"error": "rate limit reached", "modelId": "m-1"}), {})
    assert "model is rate-limited" in model["message"]


def test_account_too_new_for_video():
    err = contracts.classify_error(403, json.dumps({
        "error": "account_too_new_for_video",
        "minAccountAgeHours": 24, "accountAgeHours": 2}))
    assert err["code"] == "forbidden" and err["retry"] is False
    assert "24 hours" in err["message"] and "2 hour" in err["message"]


def test_build_chat_attachments_passthrough():
    atts = [{"name": "a.png", "contentType": "image/png",
             "url": "https://storage.example/a.png"}]
    _, body = contracts.build_chat(
        eval_id="eval-1", new=True, mode="battle", modality="chat",
        content="hi", ids=lambda: "id-x", attachments=atts)
    assert body["userMessage"]["experimental_attachments"] == atts
    _, clean = contracts.build_chat(
        eval_id="eval-1", new=True, mode="battle", modality="chat",
        content="hi", ids=lambda: "id-x")
    assert clean["userMessage"]["experimental_attachments"] == []


def test_discover_site_keys():
    html = f'<script src="https://www.google.com/recaptcha/enterprise.js?render={KEY}"></script>'
    assert contracts.discover_site_keys(html) == {"v3": KEY}
    assert contracts.discover_site_keys("<html></html>") == {}
    assert contracts.discover_site_keys(None) == {}


def test_load_overrides_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "PATHS", dict(contracts.PATHS))
    monkeypatch.setattr(contracts, "KEYS", dict(contracts.KEYS))
    f = tmp_path / "contracts.json"
    f.write_text(json.dumps({"paths": {"me": "/api/me2", "bad": "http://evil"}, "v3_key": "short", "v2_key": KEY}))
    changed = contracts.load_overrides(f)
    assert sorted(changed) == ["paths.me", "v2_key"]
    assert contracts.PATHS["me"] == "/api/me2" and "bad" not in contracts.PATHS
    assert contracts.load_overrides(tmp_path / "missing.json") == []
    (tmp_path / "list.json").write_text("[1,2]")
    assert contracts.load_overrides(tmp_path / "list.json") == []


def test_media_and_preview_discovery():
    media = contracts.find_media({"a": ["https://x.test/a.PNG?x=1", {"mimeType": "video/mp4", "url": "https://x.test/v"}]})
    assert {"url": "https://x.test/a.PNG?x=1", "kind": "image"} in media
    assert {"url": "https://x.test/v", "kind": "video"} in media
    assert contracts.find_media("https://x.test/song.mp3") == [{"url": "https://x.test/song.mp3", "kind": "audio"}]
    assert contracts.find_media({"url": "javascript:alert(1)"}) == []
    assert contracts.find_preview_url({"x": [{"previewUrl": "https://p.test"}]}) == "https://p.test"
    assert contracts.find_preview_url({"previewUrl": "ftp://no"}) is None
    assert contracts.webdev_items({"type": "webdev"}) == [{"type": "webdev"}]
    assert contracts.webdev_items([{"type": "x"}, 3, {"type": "webdev", "event": "init"}]) == [{"type": "webdev", "event": "init"}]


# ---------- permissions ----------

@pytest.mark.parametrize("mode,tool,inside,cmd,want", [
    ("default", "read_file", True, "", "allow"),
    ("default", "read_file", False, "", "ask"),
    ("bypassPermissions", "read_file", False, "", "allow"),
    ("plan", "read_file", True, "", "allow"),
    ("plan", "write_file", True, "", "deny"),
    ("plan", "run_cmd", True, "dir", "deny"),
    ("default", "write_file", True, "", "ask"),
    ("default", "delete", True, "", "ask"),
    ("acceptEdits", "write_file", True, "", "allow"),
    ("acceptEdits", "edit_file", True, "", "allow"),
    ("acceptEdits", "delete", True, "", "ask"),
    ("acceptEdits", "run_cmd", True, "dir", "ask"),
    ("auto", "delete", True, "", "allow"),
    ("auto", "write_file", False, "", "ask"),
    ("auto", "run_cmd", True, "git status", "allow"),
    ("auto", "run_cmd", True, "python -m pytest -q", "allow"),
    ("auto", "run_cmd", True, "git status && del x", "ask"),
    ("auto", "run_cmd", True, "pip install evil", "ask"),
    ("bypassPermissions", "delete", False, "", "allow"),
    ("bypassPermissions", "run_cmd", True, "rm -rf /", "deny"),
    ("bypassPermissions", "run_cmd", True, "format C:", "deny"),
    ("bypassPermissions", "run_cmd", True, "npm run build", "allow"),
    ("weird", "write_file", True, "", "ask"),
    ("auto", "made_up_tool", True, "", "ask"),
])
def test_decide_matrix(mode, tool, inside, cmd, want):
    assert permissions.decide(mode, tool, inside, cmd) == want


def test_broker_allow_deny_and_expiry():
    b = permissions.PermissionBroker()
    pid = b.ask({"tool": "write_file", "summary": "x"})
    assert [p["id"] for p in b.pending()] == [pid]
    assert b.wait(pid, 0) is None
    assert b.resolve(pid, "allow") is True
    assert b.resolve(pid, "deny") is False  # already answered
    assert b.wait(pid, 0) == "allow"
    assert b.wait(pid, 0) == "deny"  # gone
    assert b.resolve("nope", "allow") is False
    pid2 = b.ask({"tool": "delete"})
    assert b.resolve(pid2, "maybe") is False
    assert b.cancel_all() == 1 and b.wait(pid2, 0) == "deny"


def test_broker_always_is_per_kind_and_inside_only():
    b = permissions.PermissionBroker()
    pid = b.ask({"tool": "edit_file"})
    b.resolve(pid, "always")
    assert b.wait(pid, 0) == "allow"
    assert b.check("default", "write_file", True) == "allow"
    assert b.check("default", "write_file", False) == "ask"
    assert b.check("default", "delete", True) == "ask"
    assert b.check("plan", "write_file", True) == "deny"
    b.reset_always()
    assert b.check("default", "write_file", True) == "ask"


def test_broker_wait_unblocks_from_other_thread():
    b = permissions.PermissionBroker()
    pid = b.ask({"tool": "run_cmd"})
    threading.Timer(0.05, lambda: b.resolve(pid, "deny")).start()
    assert b.wait(pid, 2) == "deny"


# ---------- agent tools ----------

def test_parse_and_strip_tool_calls():
    text = ('Plan.\n<tool name="write_file"><path>a.txt</path><content>\nhello\n</content></tool>\n'
            "<TOOL name='run_cmd'><cmd>dir</cmd></TOOL> end")
    calls = agent.parse_tool_calls(text)
    assert [c["name"] for c in calls] == ["write_file", "run_cmd"]
    assert calls[0]["params"]["path"] == "a.txt" and calls[1]["params"]["cmd"] == "dir"
    assert "<tool" not in agent.strip_tool_calls(text).lower()
    assert agent.parse_tool_calls("") == [] and agent.parse_tool_calls(None) == []


def test_local_tools_file_lifecycle(tmp_path):
    t = agent.LocalTools(tmp_path)
    ok, out = t.run({"name": "write_file", "params": {"path": "src/app.py", "content": "\nprint('a')\nprint('b')\n"}})
    assert ok and (tmp_path / "src/app.py").read_text() == "print('a')\nprint('b')\n"
    assert t.run({"name": "read_file", "params": {"path": "src/app.py"}}) == (True, "print('a')\nprint('b')\n")
    ok, out = t.run({"name": "edit_file", "params": {"path": "src/app.py", "old_text": "print('b')", "new_text": "print('c')"}})
    assert ok and "print('c')" in (tmp_path / "src/app.py").read_text()
    ok, out = t.run({"name": "edit_file", "params": {"path": "src/app.py", "old_text": "print", "new_text": "x"}})
    assert not ok and "2 places" in out
    ok, out = t.run({"name": "edit_file", "params": {"path": "src/app.py", "old_text": "absent", "new_text": "x"}})
    assert not ok and "not found" in out
    assert t.run({"name": "ls", "params": {"path": "."}})[1].startswith("src/")
    assert "src/app.py:1:" in t.run({"name": "search", "params": {"pattern": "print\\('a", "path": "."}})[1]
    assert t.run({"name": "search", "params": {"pattern": "(", "path": "."}})[0]  # bad regex falls back to literal
    assert t.run({"name": "mkdir", "params": {"path": "a/b"}})[0] and (tmp_path / "a/b").is_dir()
    assert t.run({"name": "delete", "params": {"path": "src"}})[0] and not (tmp_path / "src").exists()
    ok, out = t.run({"name": "delete", "params": {"path": "."}})
    assert not ok and "refusing" in out
    assert not t.run({"name": "delete", "params": {"path": "missing.txt"}})[0]
    assert not t.run({"name": "teleport", "params": {}})[0]


def test_edit_file_keeps_crlf(tmp_path):
    f = tmp_path / "w.txt"
    f.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    ok, _ = agent.LocalTools(tmp_path).run({"name": "edit_file", "params": {"path": "w.txt", "old_text": "one\ntwo", "new_text": "1\n2"}})
    assert ok and f.read_bytes() == b"1\r\n2\r\nthree\r\n"


def test_resolve_flags_paths_outside(tmp_path):
    t = agent.LocalTools(tmp_path / "proj")
    assert t.resolve("../x")[1] is False
    assert t.resolve(str(tmp_path / "elsewhere"))[1] is False
    assert t.resolve("ok/../fine.txt")[1] is True
    assert t.describe({"name": "write_file", "params": {"path": "../x", "content": "a"}})["inside"] is False


def test_describe_shows_diff_and_command(tmp_path):
    t = agent.LocalTools(tmp_path)
    (tmp_path / "a.txt").write_text("old\n")
    d = t.describe({"name": "write_file", "params": {"path": "a.txt", "content": "new\n"}})
    assert d["summary"].startswith("Overwrite") and "-old" in d["diff"] and "+new" in d["diff"]
    assert t.describe({"name": "write_file", "params": {"path": "b.txt", "content": "x"}})["summary"].startswith("Create")
    assert t.describe({"name": "run_cmd", "params": {"cmd": "echo hi"}})["command"] == "echo hi"


def test_run_cmd_and_format_results(tmp_path):
    ok, out = agent.LocalTools(tmp_path).run({"name": "run_cmd", "params": {"cmd": "echo hello"}})
    assert ok and out.startswith("exit code 0") and "hello" in out
    text = agent.format_results([({"name": "ls", "params": {"path": "."}}, "ok", "a/"),
                                 ({"name": "run_cmd", "params": {"cmd": "x"}}, "denied by the user", "")])
    assert "[1] ls .: ok" in text and "[2] run_cmd x: denied by the user" in text


# ---------- workspace mirroring ----------

def test_plan_apply_mirror_and_manifest(tmp_path):
    root = tmp_path / "proj"
    files = [{"path": "index.html", "content": "<h1>a</h1>"}, {"path": "src/app.js", "content": "x"},
             {"path": "../evil.txt", "content": "no"}, {"path": "/abs.txt", "content": "no"}, {"path": "bin.png", "content": None}]
    plan = workspace.plan_project(root, files)
    assert sorted(plan["writes"]) == ["abs.txt", "index.html", "src/app.js"] and plan["skipped"] == 2
    assert workspace.apply_project(plan) == (3, 0)
    assert not (tmp_path / "evil.txt").exists()
    (root / "mine.txt").write_text("user file")
    plan2 = workspace.plan_project(root, [{"path": "index.html", "content": "<h1>a</h1>"}])
    assert plan2["unchanged"] == 1 and not plan2["writes"]
    assert plan2["deletes"] == ["abs.txt", "src/app.js"]
    assert workspace.apply_project(plan2, write=True, delete=False) == (0, 0)
    assert (root / "src/app.js").exists()
    plan3 = workspace.plan_project(root, [{"path": "index.html", "content": "<h1>a</h1>"}])
    assert plan3["deletes"] == ["abs.txt", "src/app.js"]  # still tracked after a declined delete
    workspace.apply_project(plan3)
    assert not (root / "src/app.js").exists() and (root / "mine.txt").exists()


def test_zip_and_listing(tmp_path, monkeypatch):
    root = workspace.project_dir("0123456789abcdef", "My App!", "a", base=tmp_path)
    assert root == (tmp_path / "my-app-01234567" / "model-a").resolve()
    workspace.apply_project(workspace.plan_project(root, [{"path": "index.html", "content": "hi"}]))
    import io
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(workspace.zip_dir(root))).namelist()
    assert names == ["index.html"]
    listed = workspace.list_projects(tmp_path)
    assert listed[0]["name"] == "my-app-01234567" and listed[0]["models"] == ["model-a"]
    assert workspace.slug("***") == "project" and workspace.webdev_files({"data": {"files": [{"path": "a"}, 3]}}) == [{"path": "a"}]


# ---------- engine ----------

def test_engine_reorders_out_of_order_pushes():
    eng = BrowserEngine()
    q = queue.Queue()
    eng._streams["r"] = {"q": q, "next": 0, "held": {}}
    eng._deliver("r", 2, "end", None)
    eng._deliver("r", 1, "chunk", "b")
    assert q.empty()
    eng._deliver("r", 0, "status", 200)
    assert [q.get_nowait() for _ in range(3)] == [("status", 200), ("chunk", "b"), ("end", None)]
    eng._deliver("unknown", 0, "status", 200)  # ignored


def test_engine_response_reads_stream():
    eng = BrowserEngine()
    q = queue.Queue()
    eng._streams["r"] = {"q": q, "next": 0, "held": {}}
    for i, item in enumerate([("status", 200), ("chunk", "ab"), ("chunk", "c"), ("end", None)]):
        eng._deliver("r", i, *item)
    r = EngineResponse(eng, "r", q, 5)
    assert r.status_code == 200 and r.ok and r.text == "abc" and "r" not in eng._streams


def test_engine_response_error_before_status():
    eng = BrowserEngine()
    q = queue.Queue()
    eng._streams["r"] = {"q": q, "next": 0, "held": {}}
    eng._deliver("r", 0, "error", "TypeError: Failed to fetch")
    r = EngineResponse(eng, "r", q, 5)
    assert r.status_code == 0 and "Failed to fetch" in r.error and r.text == ""


class _Fake:
    def __init__(self, status, text):
        self.status_code, self.text, self.error = status, text, "boom"


@pytest.mark.parametrize("status,text,user,err", [
    (200, '{"user": {"email": "a@b.c", "id": "1"}}', {"email": "a@b.c", "id": "1"}, None),
    (200, '{"user": null}', None, "Not signed in"),
    (401, "", None, "Not signed in"),
    (0, "", None, "Network error: boom"),
    (502, "", None, "HTTP 502"),
    (200, "<html>", None, "arena.ai sent an unexpected /api/me reply"),
])
def test_engine_get_me_parsing(status, text, user, err, monkeypatch):
    eng = BrowserEngine()
    eng.window, eng.loaded = object(), True
    monkeypatch.setattr(eng, "request", lambda *a, **k: _Fake(status, text))
    assert eng.get_me(timeout=1) == (user, err)


def test_engine_get_me_waits_for_challenge():
    eng = BrowserEngine()
    eng.window, eng.challenge = object(), "cloudflare"
    assert eng.get_me(timeout=0.3) == (None, "arena.ai is waiting for a cloudflare check")


# ---------- Flask API ----------

class FakeClient:
    def __init__(self):
        self.cookie_str = "arena-auth-prod-v1.0=base64-x"
        self.eval_id = None
        self.sources = []
        self.active_mode = "battle"
        self.active_modality = "chat"

    def get_me(self):
        return {"email": "t@e.st"}, None

    def sync_cookies(self):
        pass


class FakeResp:
    def __init__(self, lines=(), status=200, text=""):
        self.status_code, self._lines, self._text, self.error = status, list(lines), text, None

    def iter_text(self):
        for line in self._lines:
            yield line + "\n"

    @property
    def text(self):
        return self._text


@pytest.fixture
def web(tmp_path, monkeypatch):
    from arena.web import app as appmod
    cfg = {}
    monkeypatch.setattr(appmod, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(appmod, "save_config", lambda c: cfg.update(c))
    catalog = [
        {"id": "max-router", "name": "Arena Max", "arena": "text", "org": "boss-bandit",
         "provider": "boss-bandit", "is_router": True, "user_selectable": True,
         "capabilities": {"inputCapabilities": {"text": True}, "outputCapabilities": {"text": True}}},
        {"id": "m1", "name": "Model One", "arena": "text", "org": "test", "user_selectable": True,
         "capabilities": {"inputCapabilities": {"text": True},
                          "outputCapabilities": {"text": True, "web": True, "search": True, "image": True}}},
        {"id": "m2", "name": "Model Two", "arena": "text", "org": "test", "user_selectable": True,
         "capabilities": {"inputCapabilities": {"text": True},
                          "outputCapabilities": {"text": True, "web": True, "search": True, "image": True}}},
    ]
    monkeypatch.setattr(appmod, "sync_models", lambda force=False: [dict(m) for m in catalog])
    monkeypatch.setattr(workspace, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(appmod, "COOKIE_FILE", tmp_path / "cookies.txt")
    monkeypatch.setattr(appmod, "CHATS_DIR", tmp_path / "chats")
    monkeypatch.setattr(appmod.ENGINE, "window", None)
    monkeypatch.setattr(appmod, "_client", FakeClient())
    monkeypatch.setattr(appmod, "_ui_mode", "battle")
    monkeypatch.setattr(appmod, "_ui_modality", "auto")
    monkeypatch.setattr(appmod, "_current_chat_id", None)
    monkeypatch.setattr(appmod, "BROKER", permissions.PermissionBroker())
    monkeypatch.setattr(
        appmod,
        "CONNECTORS",
        appmod.connectors.ConnectorManager(
            lambda: tmp_path / "projects", config_path=tmp_path / "mcp.json"
        ),
    )
    monkeypatch.setattr(appmod, "_active_generations", 0)
    appmod._me_cache.update(t=0.0, user=None, err=None)
    sent = []

    def use(*responses):
        it = iter(responses)

        def fake_send(client, path, body):
            sent.append((path, body))
            return next(it)
        monkeypatch.setattr(appmod, "_send", fake_send)
    appmod.app.config["TESTING"] = True
    return appmod, appmod.app.test_client(), cfg, use, sent


def events(resp):
    return [json.loads(chunk[6:]) for chunk in resp.get_data(as_text=True).split("\n\n") if chunk.startswith("data: ")]


def test_login_rejects_garbage_and_empty_clipboard(web, monkeypatch):
    appmod, c, *_ = web
    r = c.post("/api/login", json={"method": "manual", "cookie": "hello=world"})
    assert r.status_code == 400 and "arena-auth-prod-v1" in r.get_json()["error"]
    assert c.post("/api/login", json={"method": "manual"}).status_code == 400
    monkeypatch.setattr(appmod, "get_clipboard_text", lambda: "")
    r = c.post("/api/login", json={"method": "clipboard"})
    assert r.status_code == 400 and "clipboard is empty" in r.get_json()["error"]
    r = c.post("/api/login", json={"method": "window"})
    assert r.status_code == 400 and "Arena Desktop" in r.get_json()["error"]
    assert c.get("/api/login/poll").get_json()["done"] is True


def test_guard_blocks_cross_origin_bad_host_and_non_json(web):
    _, c, *_ = web
    assert c.post("/api/new", headers={"Origin": "http://evil.test"}).status_code == 403
    assert c.post("/api/new", headers={"Origin": "http://localhost"}).status_code == 200
    assert c.get("/api/engine", headers={"Host": "evil.test"}).status_code == 403
    assert c.post("/api/settings", data="a=b", content_type="text/plain").status_code == 415


def test_status_without_engine(web):
    _, c, *_ = web
    s = c.get("/api/status").get_json()
    assert s["authenticated"] is True and s["email"] == "t@e.st" and s["desktop"] is False
    assert s["permission_mode"] == "default" and s["workspace"].endswith("workspace")


def test_permission_mode_and_answers(web):
    _, c, cfg, *_ = web
    assert c.post("/api/permission-mode", json={"mode": "bogus"}).status_code == 400
    r = c.post("/api/permission-mode", json={"mode": "auto"}).get_json()
    assert r["mode"] == "auto" and cfg["permission_mode"] == "auto"
    assert c.get("/api/permission-mode").get_json()["mode"] == "auto"
    assert c.post("/api/permissions/nope", json={"decision": "allow"}).status_code == 404
    assert c.post("/api/permissions/nope", json={"decision": "sure"}).status_code == 400


def test_chat_validation(web):
    appmod, c, *_ = web
    assert c.post("/api/chat", json={"message": "  "}).status_code == 400
    appmod._ui_mode = "direct"
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 400 and "Pick a model" in r.get_json()["error"]
    appmod._client.cookie_str = None
    assert c.post("/api/chat", json={"message": "hi", "model_id": "m"}).status_code == 401


def test_selection_endpoints_reject_unsupported_arena_pairs(web):
    appmod, c, *_ = web
    assert c.get("/api/models?mode=battle&modality=video").get_json() == []
    assert c.get("/api/models?mode=direct&modality=video").status_code == 400
    assert c.get("/api/models?mode=battle&modality=auto").get_json() == []
    assert c.get("/api/models?mode=direct&modality=auto").status_code == 400
    assert c.get("/api/models?mode=agent&modality=auto").status_code == 400
    assert c.get("/api/models?mode=agent&modality=text").get_json() == []
    assert [m["id"] for m in c.get("/api/models?mode=direct&modality=text").get_json()] == ["max-router", "m1", "m2"]
    assert [m["id"] for m in c.get("/api/models?mode=side-by-side&modality=text").get_json()] == ["m1", "m2"]

    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    assert c.post("/api/modality", json={"modality": "video"}).status_code == 400
    assert appmod._ui_modality == "text"
    assert c.post("/api/modality", json={"modality": "code"}).status_code == 200
    r = c.post("/api/mode", json={"mode": "agent"})
    assert r.status_code == 200 and r.get_json()["modality"] == "text"
    assert appmod._ui_mode == "agent" and appmod._ui_modality == "text"
    assert c.post("/api/modality", json={"modality": "text"}).status_code == 200


def test_chat_rejects_manual_models_where_arena_disables_them(web):
    appmod, c, *_ = web
    appmod._ui_mode, appmod._ui_modality = "battle", "video"
    assert c.post("/api/chat", json={"message": "hi", "model_id": "m1"}).status_code == 400
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    assert c.post("/api/chat", json={"message": "hi", "model_id": "m1"}).status_code == 400
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    assert c.post("/api/chat", json={"message": "hi", "model_id": "not-in-catalog"}).status_code == 400


def test_battle_chat_streams_both_lanes_and_saves(web):
    appmod, c, _, use, sent = web
    use(FakeResp(['a0:"Hello "', 'b0:"Hi"', 'a0:"there"', 'ad:{"finishReason":"stop"}']))
    ev = events(c.post("/api/chat", json={"message": "yo"}))
    assert ev[0]["type"] == "meta" and ev[0]["dual"] is True
    done = ev[-1]
    assert done["type"] == "done" and done["text_a"] == "Hello there" and done["text_b"] == "Hi"
    path, body = sent[0]
    assert path == contracts.PATHS["create"] and body["mode"] == "battle" and "modelAId" not in body
    # The Arena session id now lives on the chat itself (parallel chats),
    # not on the shared client.
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved and saved["eval_id"] == body["id"]
    use(FakeResp(['a0:"again"']))
    events(c.post("/api/chat", json={"message": "more", "chat_id": ev[0]["chat_id"]}))
    assert sent[1][0] == contracts.PATHS["post"].format(eval_id=body["id"]) and "mode" not in sent[1][1]


def test_battle_lane_error_does_not_stop_or_pollute_other_lane(web):
    appmod, c, _, use, _sent = web
    appmod._ui_mode, appmod._ui_modality = "battle", "text"
    use(FakeResp([
        'a3:"account rate limit"',
        'b0:"Surviving answer"',
        'bd:{"finishReason":"stop"}',
    ]))
    ev = events(c.post("/api/chat", json={"message": "compare"}))
    errors = [e for e in ev if e["type"] == "error"]
    assert len(errors) == 1 and errors[0]["model"] == "a"
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "" and ev[-1]["text_b"] == "Surviving answer"

    saved = appmod._load_chat(ev[0]["chat_id"])["messages"][-1]
    assert saved["text"] == "" and saved["text_b"] == "Surviving answer"
    assert saved["lane_errors"] == {"a": ["Model A stopped with an error: account rate limit"]}
    assert "error" not in saved  # not a whole-turn/global error


def test_challenge_without_engine_explains(web):
    _, c, _, use, _ = web
    use(FakeResp(status=403, text='{"error":"recaptcha validation failed"}'))
    ev = events(c.post("/api/chat", json={"message": "hi"}))
    err = [e for e in ev if e["type"] == "error"][0]
    assert err["code"] == "challenge" and "Arena-Desktop.exe" in err["message"]
    assert ev[-1]["type"] == "done"


def test_expired_eval_starts_new_conversation(web):
    appmod, c, _, use, sent = web
    import uuid
    cid = str(uuid.uuid4())
    appmod._save_chat(cid, title="old", messages=[], mode=appmod._ui_mode, eval_id="old-eval")
    use(FakeResp(status=404, text="not found"), FakeResp(['a0:"fresh"']))
    ev = events(c.post("/api/chat", json={"message": "hi", "chat_id": cid}))
    assert any(e["type"] == "notice" for e in ev) and ev[-1]["text_a"] == "fresh"
    assert "mode" not in sent[0][1] and sent[1][1]["mode"] == "battle"


def _tool_line(text):
    return "a0:" + json.dumps(text)


def test_agent_writes_file_in_accept_edits(web):
    appmod, c, cfg, use, sent = web
    cfg["permission_mode"] = "acceptEdits"
    appmod._ui_mode = "agent"
    use(FakeResp([_tool_line('Writing.<tool name="write_file"><path>hello.txt</path><content>\nhi\n</content></tool>')]),
        FakeResp(['a0:"Done."']))
    ev = events(c.post("/api/chat", json={"message": "make hello"}))
    root = workspace.PROJECTS_DIR / "workspace"
    assert (root / "hello.txt").read_text() == "hi\n"
    result = [e for e in ev if e["type"] == "tool_result"][0]
    assert result["ok"] and result["status"] == "ok"
    assert "User message:\nmake hello" in sent[0][1]["userMessage"]["content"] and sent[0][1]["modelAId"] == "m1"
    assert sent[1][1]["userMessage"]["content"].startswith("Tool results:") and "mode" not in sent[1][1]


def test_agent_plan_mode_blocks_writes(web):
    appmod, c, cfg, use, _ = web
    cfg["permission_mode"] = "plan"
    appmod._ui_mode = "agent"
    use(FakeResp([_tool_line('<tool name="write_file"><path>x.txt</path><content>a</content></tool>')]), FakeResp(['a0:"Plan: 1. write x"']))
    ev = events(c.post("/api/chat", json={"message": "plan"}))
    assert not (workspace.PROJECTS_DIR / "workspace" / "x.txt").exists()
    assert [e for e in ev if e["type"] == "tool_result"][0]["status"].startswith("blocked: plan mode")


def test_agent_asks_and_user_denies(web):
    appmod, c, cfg, use, _ = web
    appmod._ui_mode = "agent"
    use(FakeResp([_tool_line('<tool name="delete"><path>keep.txt</path></tool>')]), FakeResp(['a0:"ok"']))
    root = workspace.PROJECTS_DIR / "workspace"
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("x")

    def answer():
        for _ in range(100):
            pending = appmod.BROKER.pending()
            if pending:
                appmod.BROKER.resolve(pending[0]["id"], "deny")
                return
            time.sleep(0.05)
    threading.Thread(target=answer, daemon=True).start()
    ev = events(c.post("/api/chat", json={"message": "clean"}))
    assert (root / "keep.txt").exists()
    assert any(e["type"] == "permission" for e in ev)
    assert [e for e in ev if e["type"] == "tool_result"][0]["status"] == "denied by the user"


def _webdev_line(lane, content):
    item = {"type": "webdev", "event": "init", "templateKind": "vanilla",
            "files": [{"path": "index.html", "content": content}]}
    return f"{lane}2:" + json.dumps([item])


def test_code_mode_saves_files_locally(web):
    appmod, c, cfg, use, _ = web
    cfg["permission_mode"] = "acceptEdits"
    appmod._ui_modality = "code"
    use(FakeResp([_webdev_line("a", "<h1>A</h1>"), _webdev_line("b", "<h1>B</h1>")]))
    ev = events(c.post("/api/chat", json={"message": "landing page"}))
    written = {e["model"]: e for e in ev if e["type"] == "files_written"}
    assert written["a"]["saved"] and written["a"]["written"] == 1
    assert Path(written["b"]["root"], "index.html").read_text() == "<h1>B</h1>"
    assert written["a"]["preview"].startswith("http://127.0.0.1:")
    assert c.post("/api/workspace/open", json={"path": "C:/Windows"}).status_code == 403
    assert c.get("/api/workspace/zip?path=" + written["a"]["root"]).status_code == 200


def test_plan_mode_defers_code_save_until_user_clicks(web):
    appmod, c, cfg, use, _ = web
    cfg["permission_mode"] = "plan"
    appmod._ui_modality = "code"
    use(FakeResp([_webdev_line("a", "<p>x</p>")]))
    ev = events(c.post("/api/chat", json={"message": "page"}))
    fw = [e for e in ev if e["type"] == "files_written"][0]
    assert fw["saved"] is False and not Path(fw["root"], "index.html").exists()
    r = c.post("/api/workspace/save", json={"chat_id": ev[0]["chat_id"], "lane": "a"}).get_json()
    assert r["ok"] and r["written"] == 1 and Path(fw["root"], "index.html").exists()
    assert c.post("/api/workspace/save", json={"chat_id": "nope", "lane": "a"}).status_code == 404


def test_workspace_set_rules(web, tmp_path):
    _, c, cfg, *_ = web
    assert c.post("/api/workspace/set", json={"path": "relative/dir"}).status_code == 400
    assert c.post("/api/workspace/set", json={"path": str(Path.home())}).status_code == 400
    r = c.post("/api/workspace/set", json={"path": str(tmp_path / "proj")}).get_json()
    assert r["ok"] and cfg["workspace_dir"] == str((tmp_path / "proj").resolve())


def test_media_save_rules(web):
    _, c, *_ = web
    assert c.post("/api/media/save", json={"url": "http://x.test/a.png"}).status_code == 400
    assert c.post("/api/media/save", json={"url": "https://127.0.0.1/a.png"}).status_code == 400
    r = c.post("/api/media/save", json={"url": "data:image/png;base64,iVBORw0KGgo="}).get_json()
    assert r["ok"] and Path(r["path"]).read_bytes().startswith(b"\x89PNG")


def test_safe_resolve_blocks_outside_home(web):
    appmod, *_ = web
    assert appmod._safe_resolve(".") == Path.home().resolve()
    with pytest.raises(PermissionError):
        appmod._safe_resolve(str(Path.home().parent))
    with pytest.raises(PermissionError):
        appmod._safe_resolve("../")


def test_check_panel_stays_below_recaptcha_popup():
    import re
    from arena import engine
    z = int(re.search(r"z-index:(\d+)", engine._CHECK_JS).group(1))
    assert z < 2000000000  # Google's picture popup must render above the panel
    assert "arena-desktop-hide" not in engine._CHECK_JS  # nothing may delete the checkbox mid-check


def test_engine_shows_and_hides_through_dock():
    from arena.engine import BrowserEngine

    class FakeDock:
        visible = False

        def __init__(self):
            self.calls = []

        def show(self):
            self.calls.append("show")
            self.visible = True
            return True

        def hide(self):
            self.calls.append("hide")
            self.visible = False

    class NoWindow:
        def show(self):
            raise AssertionError("a docked engine must not open a second window")

        hide = show

    e = BrowserEngine()
    e.window, e.dock = NoWindow(), FakeDock()
    e._present()
    e.hide()
    assert e.dock.calls == ["show", "hide"]
    e.challenge, e.login_open, e.dock.visible = "recaptcha", True, True
    e.close_pane()
    assert e.challenge is None and e.login_open is False


def test_chats_keep_their_own_history(web):
    appmod, c, _, use, sent = web
    use(FakeResp(['a0:"one"']))
    a = events(c.post("/api/chat", json={"message": "first"}))[0]["chat_id"]
    b = c.post("/api/new").get_json()["chat_id"]
    use(FakeResp(['a0:"two"']))
    events(c.post("/api/chat", json={"message": "second", "chat_id": b}))
    assert [m["text"] for m in appmod._load_chat(b)["messages"]] == ["second", "two"]
    assert [m["text"] for m in appmod._load_chat(a)["messages"]] == ["first", "one"]


def test_failed_reply_is_saved_with_its_error(web):
    appmod, c, _, use, sent = web
    use(FakeResp(status=500, text="boom"))
    ev = events(c.post("/api/chat", json={"message": "x"}))
    last = appmod._load_chat(ev[0]["chat_id"])["messages"][-1]
    assert last["role"] == "assistant" and last["text"] == "" and last.get("error")


def test_two_model_tools_keep_each_copy_apart(tmp_path):
    (tmp_path / "keep.txt").write_bytes(b"one\ntwo\n")
    (tmp_path / "option-b").mkdir()
    (tmp_path / "option-b" / "x.txt").write_text("start")
    a = agent.LocalTools(tmp_path, tmp_path / agent.LANE_DIRS["a"])
    ok, _ = a.run({"name": "write_file", "params": {"path": "help.py", "content": "start"}})
    assert ok and (tmp_path / "option-a" / "help.py").read_text() == "start" and not (tmp_path / "help.py").exists()
    info = a.describe({"name": "edit_file", "params": {"path": "keep.txt", "old_text": "two", "new_text": "2"}})
    assert info["rel"] == "option-a/keep.txt" and "+2" in info["diff"]
    ok, _ = a.run({"name": "edit_file", "params": {"path": "keep.txt", "old_text": "two", "new_text": "2"}})
    assert ok and (tmp_path / "keep.txt").read_text() == "one\ntwo\n"
    assert a.run({"name": "read_file", "params": {"path": "keep.txt"}})[1] == "one\n2\n"
    listing = a.run({"name": "ls", "params": {"path": "."}})[1]
    assert "help.py" in listing and "keep.txt" in listing and "option-" not in listing
    found = a.run({"name": "search", "params": {"pattern": "start"}})[1]
    assert "option-a/help.py" in found and "option-b" not in found


def test_tool_tags_parse_without_quotes_and_strip_cleanly():
    calls = agent.parse_tool_calls("<tool name=ls><path>.</path></tool>")
    assert calls == [{"name": "ls", "params": {"path": "."}}]
    assert agent.strip_tool_calls('ok ```xml\n<tool name="ls"><path>.</path></tool>\n``` done') == "ok  done"


def test_battle_models_get_tools_once_a_folder_is_picked(web, tmp_path):
    appmod, c, cfg, use, sent = web
    cfg.update(permission_mode="acceptEdits", workspace_dir=str(tmp_path / "ws"))
    call = '<tool name="write_file"><path>help.py</path><content>start this right now</content></tool>'
    use(FakeResp([_tool_line(call), "b0:" + json.dumps(call.replace("start", "begin"))]),
        FakeResp(['a0:"done"', 'b0:"done"']))
    ev = events(c.post("/api/chat", json={"message": "Create help.py"}))
    ws = tmp_path / "ws"
    assert (ws / "option-a" / "help.py").read_text() == "start this right now"
    assert (ws / "option-b" / "help.py").read_text() == "begin this right now"
    assert "User message:\nCreate help.py" in sent[0][1]["userMessage"]["content"]
    second = sent[1][1]["userMessage"]["content"]
    assert second.startswith("Tool results for both replies") and "Reply A:" in second and "Reply B:" in second
    assert sorted(e["model"] for e in ev if e["type"] == "tool_result") == ["a", "b"]
    saved = appmod._load_chat(ev[0]["chat_id"])["messages"][-1]
    assert "<tool" not in saved["text"] + saved["text_b"] and {t["lane"] for t in saved["tools"]} == {"a", "b"}


def test_plain_chat_without_a_picked_folder_gets_no_tool_guide(web):
    appmod, c, cfg, use, sent = web
    use(FakeResp(['a0:"hi"', 'b0:"hello"']))
    events(c.post("/api/chat", json={"message": "hi"}))
    assert sent[0][1]["userMessage"]["content"] == "hi"


def test_generated_media_is_saved_to_the_picked_folder(web, tmp_path, monkeypatch):
    appmod, c, cfg, use, sent = web
    cfg["workspace_dir"] = str(tmp_path / "ws")

    class Got:
        content, headers = b"PNG", {"content-type": "image/png"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(appmod.requests, "get", lambda url, timeout: Got())
    out = list(appmod._save_media({"url": "https://cdn.example/img/cat.png", "kind": "image"}, "a", {"title": "A cat"}))
    saved = list((tmp_path / "ws").glob("*-a-*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == b"PNG" and "Saved to" in out[0]


def test_sandbox_ops_from_arena_tool_calls():
    from arena import contracts
    op = contracts.sandbox_op({"toolCallId": "1", "toolName": "create_file", "args": {"path": "src/a.ts", "content": "x"}})
    assert op["kind"] == "write" and op["path"] == "src/a.ts" and op["content"] == "x"
    op = contracts.sandbox_op({"toolCallId": "2", "toolName": "str_replace_based_edit_tool",
                               "args": json.dumps({"command": "str_replace", "path": "a.ts", "old_str": "x", "new_str": "y"})})
    assert op["kind"] == "edit" and (op["old"], op["new"]) == ("x", "y")
    assert contracts.sandbox_op({"toolName": "bash", "args": {"command": "npm install"}})["kind"] == "run"
    assert contracts.sandbox_op({"toolName": "read_file", "args": {"path": "package.json"}})["kind"] == "read"
    assert contracts.parse_line('aa:{"toolCallId":"1","result":"ok"}') == ("a", "tool_result", {"toolCallId": "1", "result": "ok"})
    assert contracts.parse_line('b9:{"toolCallId":"1"}')[:2] == ("b", "tool_call")


def test_code_answer_is_copied_into_the_picked_folder_and_local_edits_go_back(web, tmp_path):
    appmod, c, cfg, use, sent = web
    cfg.update(permission_mode="acceptEdits", workspace_dir=str(tmp_path / "ws"))
    appmod._ui_mode, appmod._ui_modality = "direct", "code"
    create = {"toolCallId": "t1", "toolName": "create_file", "args": {"path": "src/App.tsx", "content": "hello\nworld\n"}}
    edit = {"toolCallId": "t2", "toolName": "edit_file", "args": {"path": "src/App.tsx", "old_str": "world", "new_str": "arena"}}
    use(FakeResp(['a0:"I will create src/App.tsx."', "a9:" + json.dumps(create), 'aa:{"toolCallId":"t1","result":"ok"}',
                  "a9:" + json.dumps(edit), 'aa:{"toolCallId":"t2","result":{"url":"https://x1.arena.site/"}}', 'a0:"Done."']))
    ev = events(c.post("/api/chat", json={"message": "build", "model_id": "m1"}))
    app_tsx = tmp_path / "ws" / "src" / "App.tsx"
    assert app_tsx.read_bytes() == b"hello\narena\n"
    rows = [e for e in ev if e["type"] == "sandbox"]
    assert [r["verb"] for r in rows] == ["Created", "Edited"] and (rows[1]["add"], rows[1]["del"]) == (1, 1)
    assert any(e["type"] == "preview" and e["url"] == "https://x1.arena.site/" for e in ev)
    cid = ev[0]["chat_id"]
    assert [r["verb"] for r in appmod._load_chat(cid)["messages"][-1]["sandbox"]] == ["Created", "Edited"]
    app_tsx.write_bytes(b"hello\nmine\n")
    use(FakeResp(['a0:"ok"']))
    events(c.post("/api/chat", json={"message": "next", "model_id": "m1", "chat_id": cid}))
    content = sent[-1][1]["userMessage"]["content"]
    assert "--- src/App.tsx ---\nhello\nmine\n" in content and content.endswith("My message:\nnext")


# ---------- v4: automatic Agent, generation state, cloud history, panel API ----------

def test_status_reports_only_real_generation_as_generating(web):
    appmod, c, *_ = web
    status = c.get("/api/status").get_json()
    assert status["loading"] is False and status["engine_loading"] is False
    assert status["generating"] is False
    appmod._generation_started()
    try:
        assert c.get("/api/status").get_json()["generating"] is True
    finally:
        appmod._generation_finished()
    assert c.get("/api/status").get_json()["generating"] is False


def test_agent_runs_when_catalog_is_empty_and_never_names_a_model(web, monkeypatch):
    appmod, c, _, use, sent = web
    monkeypatch.setattr(appmod, "sync_models", lambda force=False: [])
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    use(FakeResp(['a0:"Automatic answer"']))
    ev = events(c.post("/api/chat", json={"message": "hello"}))
    assert ev[0]["names"] == {"a": "Agent", "b": None}
    assert ev[-1]["text_a"] == "Automatic answer"
    assert sent[0][1]["mode"] == "direct-battle" and "modelAId" not in sent[0][1]
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved["model_a"] is None and saved["messages"][-1]["names"] == {"a": "Agent", "b": None}


def test_agent_history_sanitizes_internal_model_and_lists_source(web):
    appmod, c, *_ = web
    chat_id = "11111111-1111-4111-8111-111111111111"
    eval_id = "22222222-2222-4222-8222-222222222222"
    appmod._save_chat(
        chat_id, title="Agent work", mode="agent", modality="text", model_a="max-router",
        eval_id=eval_id, source="arena",
        messages=[
            {"role": "user", "text": "hi"},
            {"role": "assistant", "text": "done", "text_b": "", "names": {"a": "Arena Max", "b": None}},
        ],
    )
    loaded = c.get(f"/api/chats/{chat_id}").get_json()
    assert loaded["model_a"] is None and loaded["model_b"] is None
    assert loaded["messages"][-1]["names"] == {"a": "Agent", "b": None}
    listed = {item["id"]: item for item in c.get("/api/chats").get_json()}[chat_id]
    assert listed["eval_id"] == eval_id and listed["source"] == "arena"


def test_reasoning_deltas_are_saved_verbatim_for_both_lanes(web):
    appmod, c, _, use, _ = web
    use(FakeResp([
        'ag:"First "', 'ag:"thought continues"',
        'bg:"Bee "', 'bg:"thought continues"',
        'a0:"A"', 'b0:"B"',
    ]))
    ev = events(c.post("/api/chat", json={"message": "think"}))
    reasoning = [(e["model"], e["text"]) for e in ev if e["type"] == "reasoning"]
    assert reasoning == [("a", "First "), ("a", "thought continues"), ("b", "Bee "), ("b", "thought continues")]
    reply = appmod._load_chat(ev[0]["chat_id"])["messages"][-1]
    assert reply["reasoning"] == "First thought continues"
    assert reply["reasoning_b"] == "Bee thought continues"
    assert "\n" not in reply["reasoning"] + reply["reasoning_b"]


def test_arena_cloud_history_lists_dedupes_and_imports(web, monkeypatch):
    appmod, c, *_ = web
    local_id = "11111111-1111-4111-8111-111111111111"
    known_eval = "22222222-2222-4222-8222-222222222222"
    import_eval = "33333333-3333-4333-8333-333333333333"
    agent_eval = "44444444-4444-4444-8444-444444444444"
    appmod._save_chat(local_id, title="Already local", messages=[], eval_id=known_eval)
    calls = []

    def fake_arena_get(path, timeout=30):
        calls.append(path)
        if path.startswith(appmod.contracts.PATHS["history_unified"]):
            return {
                "entries": [
                    {"id": known_eval, "type": "evaluation", "title": "Known", "mode": "direct-battle",
                     "modality": "chat", "updatedAt": "2026-01-03T00:00:00Z", "createdAt": "2026-01-03T00:00:00Z"},
                    {"id": import_eval, "type": "evaluation", "title": "Cloud import", "mode": "side-by-side",
                     "modality": "chat", "updatedAt": "2026-01-02T00:00:00Z", "createdAt": "2026-01-02T00:00:00Z"},
                    {"id": agent_eval, "type": "agentic", "title": "Native agent",
                     "updatedAt": "2026-01-01T00:00:00Z", "createdAt": "2026-01-01T00:00:00Z"},
                ],
                "pagination": {"limit": 50},
            }, None, 200
        if import_eval in path:
            return {
                "id": import_eval, "title": "Cloud import", "mode": "side-by-side",
                "maskedEvaluations": [{"modality": "chat"}],
                "messages": [
                    {"role": "user", "content": "Question", "createdAt": "2026-01-02T00:00:00Z"},
                    {"role": "assistant", "participantPosition": "a", "modelId": "m1",
                     "parentMessageIds": ["u1"], "createdAt": "2026-01-02T00:00:01Z",
                     "content": [{"type": "reasoning", "text": "A "}, {"type": "reasoning", "text": "thought"},
                                 {"type": "text", "text": "Answer A"}]},
                    {"role": "assistant", "participantPosition": "b", "modelId": "m2",
                     "parentMessageIds": ["u1"], "createdAt": "2026-01-02T00:00:02Z",
                     "reasoning": "B thought", "content": "Answer B"},
                ],
            }, None, 200
        if agent_eval in path:
            return {
                "id": agent_eval, "title": "Native agent", "lastEventId": "88",
                "messages": [
                    {"role": "user", "parts": [{"type": "text", "text": "Agent question"}],
                     "createdAt": "2026-01-01T00:00:00Z"},
                    {"role": "assistant", "parts": [
                        {"type": "step-start"},
                        {"type": "text", "text": "Agent answer"},
                    ], "createdAt": "2026-01-01T00:00:01Z"},
                ],
            }, None, 200
        raise AssertionError(f"unexpected Arena path: {path}")

    monkeypatch.setattr(appmod, "_arena_get_json", fake_arena_get)
    payload = c.get("/api/arena-chats").get_json()
    entries = {item["eval_id"]: item for item in payload["entries"]}
    assert entries[known_eval]["local_chat_id"] == local_id
    assert entries[known_eval]["mode"] == "direct" and entries[known_eval]["modality"] == "text"
    assert entries[import_eval]["local_chat_id"] is None
    assert entries[agent_eval]["mode"] == "agent"
    assert entries[agent_eval]["local_chat_id"] is None

    imported = c.post(f"/api/arena-chats/{import_eval}/open", json={"type": "evaluation"}).get_json()
    assert imported == {"ok": True, "chat_id": import_eval, "imported": True}
    saved = appmod._load_chat(import_eval)
    assert saved["source"] == "arena" and saved["mode"] == "side-by-side"
    assert saved["messages"][-1]["text"] == "Answer A"
    assert saved["messages"][-1]["text_b"] == "Answer B"
    assert saved["messages"][-1]["reasoning"] == "A thought"
    assert saved["messages"][-1]["reasoning_b"] == "B thought"

    before = len(calls)
    known = c.post(f"/api/arena-chats/{known_eval}/open", json={"type": "evaluation"}).get_json()
    assert known == {"ok": True, "chat_id": local_id, "imported": False}
    assert len(calls) == before  # already-local chats are not downloaded again

    # A cloud Arena Agent session imports as a local, continuable chat: the
    # native coordinates are the session id, the unknown stream cursor is
    # marked for draining on the first continuation.
    agent_response = c.post(f"/api/arena-chats/{agent_eval}/open", json={"type": "agentic"})
    assert agent_response.status_code == 200
    assert agent_response.get_json() == {"ok": True, "chat_id": agent_eval, "imported": True}
    agent_saved = appmod._load_chat(agent_eval)
    assert agent_saved["source"] == "arena"
    assert agent_saved["mode"] == "agent" and agent_saved["agent_backend"] == "arena"
    assert agent_saved["title"] == "Native agent"
    assert [(m["role"], m["text"]) for m in agent_saved["messages"]] == [
        ("user", "Agent question"), ("assistant", "Agent answer")]
    # The snapshot's lastEventId becomes the resume cursor: no draining.
    assert agent_saved["native"] == {"backend": "arena", "session_id": agent_eval,
                                     "trigger_address": agent_eval, "last_event_id": "88",
                                     "needs_drain": False}
    # Re-opening is a local hit (no download), and the cloud list now links
    # the agentic entry to the imported chat.
    before = len(calls)
    again = c.post(f"/api/arena-chats/{agent_eval}/open", json={"type": "agentic"}).get_json()
    assert again == {"ok": True, "chat_id": agent_eval, "imported": False}
    assert len(calls) == before
    relisted = {item["eval_id"]: item for item in c.get("/api/arena-chats").get_json()["entries"]}
    assert relisted[agent_eval]["local_chat_id"] == agent_eval
    assert relisted[agent_eval]["local_mode"] == "agent"


def test_workspace_panel_tree_and_file_are_scoped_to_panel_root(web):
    appmod, c, *_ = web
    root = workspace.PROJECTS_DIR
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "blob.bin").write_bytes(b"a\x00b")

    tree = c.get("/api/workspace/tree").get_json()
    assert tree["root"] == str(root.resolve())
    assert [entry["name"] for entry in tree["entries"]] == ["src", "blob.bin", "README.md"]
    child = c.get("/api/workspace/tree?path=src").get_json()
    assert child["entries"][0]["path"] == "src/app.py"
    assert c.get("/api/workspace/tree?path=..%2F").status_code == 403
    assert c.get("/api/workspace/file?path=%2Fetc%2Fpasswd").status_code == 403
    assert c.get("/api/workspace/file?path=src%2Fapp.py").get_json()["content"] == "print('ok')\n"
    assert c.get("/api/workspace/file?path=blob.bin").status_code == 415
    assert appmod._panel_workspace_root() == root.resolve()


def test_connector_routes_use_isolated_config_and_mask_secrets(web, tmp_path, monkeypatch):
    appmod, c, *_ = web
    payload = c.get("/api/connectors").get_json()
    assert payload["config_path"] == str(tmp_path / "mcp.json")
    assert {preset["id"] for preset in payload["presets"]} >= {"filesystem", "git", "github", "playwright"}

    assert c.post("/api/connectors/cli", json={"name": "bad name", "command": "demo", "connect": False}).status_code == 400
    created = c.post("/api/connectors/cli", json={
        "name": "demo-server", "command": "python -m demo_mcp", "connect": False,
        "env": {"API_TOKEN": "secret-value", "PLAIN": "visible"},
    })
    assert created.status_code == 200
    assert "secret-value" not in created.get_data(as_text=True)
    server = {item["name"]: item for item in created.get_json()["servers"]}["demo-server"]
    assert server["env"]["API_TOKEN"] == "••••••" and server["env"]["PLAIN"] == "visible"

    preset = c.post("/api/connectors/preset", json={"preset_id": "time", "values": {}, "connect": False}).get_json()
    assert {item["name"] for item in preset["servers"]} == {"demo-server", "time"}
    disabled = c.post("/api/connectors/time/enabled", json={"enabled": False}).get_json()
    assert {item["name"]: item for item in disabled["servers"]}["time"]["enabled"] is False

    monkeypatch.setattr(appmod.connectors, "open_config_file", lambda path: Path(path))
    opened = c.post("/api/connectors/config/open", json={}).get_json()
    assert opened == {"ok": True, "path": str(tmp_path / "mcp.json")}
    removed = c.post("/api/connectors/demo-server/delete", json={}).get_json()
    assert {item["name"] for item in removed["servers"]} == {"time"}


class _FakeConnectorManager:
    def __init__(self, appmod, annotations):
        self.appmod = appmod
        self.calls = []
        self.tools = {
            "mcp__demo__echo": {
                "alias": "mcp__demo__echo", "server": "demo", "tool": "echo",
                "description": "Echo text", "annotations": annotations,
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"}, "count": {"type": "integer"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
            }
        }

    def ensure_started(self, timeout=30):
        return dict(self.tools)

    def guide(self, tool_map):
        return self.appmod.connectors.ConnectorManager.guide(tool_map)

    def call(self, alias, arguments):
        self.calls.append((alias, arguments))
        return f"echo {arguments['text']} x{arguments['count']}"


def test_agent_calls_mcp_tool_with_schema_coerced_json(web, monkeypatch):
    appmod, c, cfg, use, sent = web
    cfg["permission_mode"] = "bypassPermissions"
    fake = _FakeConnectorManager(appmod, {"readOnlyHint": True})
    monkeypatch.setattr(appmod, "CONNECTORS", fake)
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    tool = '<tool name="mcp__demo__echo"><arguments>{"text":"hello","count":"2","enabled":"true"}</arguments></tool>'
    use(FakeResp([_tool_line(tool)]), FakeResp(['a0:"Done"']))
    ev = events(c.post("/api/chat", json={"message": "echo it"}))
    assert "mcp__demo__echo" in sent[0][1]["userMessage"]["content"]
    assert fake.calls == [("mcp__demo__echo", {"text": "hello", "count": 2, "enabled": True})]
    result = [e for e in ev if e["type"] == "tool_result"][0]
    assert result["ok"] and result["output"] == "echo hello x2"
    assert "echo hello x2" in sent[1][1]["userMessage"]["content"]


def test_agent_plan_mode_blocks_non_readonly_mcp_tool(web, monkeypatch):
    appmod, c, cfg, use, _ = web
    cfg["permission_mode"] = "plan"
    fake = _FakeConnectorManager(appmod, {})
    monkeypatch.setattr(appmod, "CONNECTORS", fake)
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    tool = '<tool name="mcp__demo__echo"><arguments>{"text":"hello","count":1}</arguments></tool>'
    use(FakeResp([_tool_line(tool)]), FakeResp(['a0:"Plan only"']))
    ev = events(c.post("/api/chat", json={"message": "echo it"}))
    assert fake.calls == []
    assert [e for e in ev if e["type"] == "tool_result"][0]["status"].startswith("blocked: plan mode")




# ---------- v4.1: explicit chat selection + Arena account connectors ----------

def test_chat_body_mode_agent_overrides_direct_global(web):
    appmod, c, _, use, sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    use(FakeResp(['a0:"Agent answer"']))
    ev = events(c.post("/api/chat", json={"message": "hi", "mode": "agent", "modality": "text"}))
    assert ev[0]["mode"] == "agent" and ev[0]["names"] == {"a": "Agent", "b": None}
    assert sent[0][1]["mode"] == "direct-battle" and sent[0][1]["modelAId"] == "m1"
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved["mode"] == "agent" and saved["modality"] == "text"
    assert appmod._ui_mode == "agent"


def test_chat_body_mode_direct_overrides_agent_global(web):
    appmod, c, _, use, sent = web
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    use(FakeResp(['a0:"Direct answer"']))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "direct", "modality": "text", "model_id": "m2"}))
    assert ev[0]["names"] == {"a": "Model Two", "b": None}
    assert sent[0][1]["modelAId"] == "m2"
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved["mode"] == "direct" and saved["model_a"] == "m2"


def test_chat_body_invalid_selection_is_rejected(web):
    appmod, c, *_ = web
    r = c.post("/api/chat", json={"message": "hi", "mode": "direct", "modality": "video"})
    assert r.status_code == 400 and r.get_json()["code"] == "unsupported_selection"
    r = c.post("/api/chat", json={"message": "hi", "mode": "nope", "modality": "text"})
    assert r.status_code == 400 and r.get_json()["code"] == "unsupported_selection"
    # The global selection is not clobbered by a rejected request.
    assert appmod._ui_mode == "battle"


def _arena_connectors_fake(appmod, monkeypatch, state):
    def fake(path, timeout=30, method="GET"):
        state.setdefault("calls", []).append((method, path))
        if path == appmod.contracts.PATHS["connectors"]:
            return {"connectors": [
                {"slug": "github", "displayName": "GitHub", "description": "Repos",
                 "logoUrl": "https://cdn.test/github.png", "authStatus": "disconnected"},
                {"slug": "notion", "displayName": "Notion", "description": "Docs",
                 "authStatus": "connected"},
                {"slug": "bad slug!", "displayName": "Bad", "authStatus": "connected"},
                {"slug": "drive", "authStatus": "weird", "logoUrl": "javascript:alert(1)"},
            ]}, None, 200
        if path == appmod.contracts.PATHS["connectors_status"]:
            return {"connectors": [
                {"slug": "github", "authStatus": "connected" if state.get("github_on") else "disconnected"},
            ]}, None, 200
        if path == appmod.contracts.PATHS["connectors_sync"] and method == "POST":
            state["github_on"] = True
            return {"connectors": [{"slug": "github", "authStatus": "connected"}]}, None, 200
        if path.endswith("/link-token") and method == "POST":
            return {"linkToken": "tok-123"}, None, 200
        if method == "DELETE":
            return {"success": True}, None, 200
        raise AssertionError(f"unexpected Arena call: {method} {path}")
    monkeypatch.setattr(appmod, "_arena_get_json", fake)


def test_arena_connectors_list_normalizes_and_merges_status(web, monkeypatch):
    appmod, c, *_ = web
    state = {"github_on": True}
    _arena_connectors_fake(appmod, monkeypatch, state)
    payload = c.get("/api/arena-connectors").get_json()
    assert payload["ok"] is True
    items = {item["slug"]: item for item in payload["connectors"]}
    assert set(items) == {"github", "notion", "drive"}  # invalid slug dropped
    assert items["github"]["connected"] is True  # status endpoint wins over catalog
    assert items["github"]["logo_url"] == "https://cdn.test/github.png"
    assert items["notion"]["connected"] is True
    assert items["drive"]["name"] == "drive"  # displayName fallback
    assert items["drive"]["auth_status"] == "disconnected"  # unknown status coerced
    assert items["drive"]["logo_url"] is None  # non-http logo dropped


def test_arena_connector_disconnect_uses_delete(web, monkeypatch):
    appmod, c, *_ = web
    state = {}
    _arena_connectors_fake(appmod, monkeypatch, state)
    r = c.post("/api/arena-connectors/notion/disconnect", json={})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert ("DELETE", "/api/connectors/notion") in state["calls"]
    assert c.post("/api/arena-connectors/bad%20slug/disconnect", json={}).status_code in (400, 404)


def test_arena_connector_connect_requires_engine(web, monkeypatch):
    appmod, c, *_ = web
    _arena_connectors_fake(appmod, monkeypatch, {})
    r = c.post("/api/arena-connectors/github/connect", json={})
    assert r.status_code == 400
    payload = r.get_json()
    assert payload["code"] == "engine_required" and payload["fallback_url"].endswith("/agent")


class _FakeLinkEngine:
    def __init__(self):
        self.window = object()
        self.challenge = None
        self.shown = False
        self.hidden = False
        self.scripts = []
        self.link_state = '{"state": "open", "error": null}'

    def ready(self):
        return True

    def wait_ready(self, timeout=30):
        return True

    def show(self):
        self.shown = True

    def hide(self):
        self.hidden = True

    def run_script(self, script):
        self.scripts.append(script)
        if "JSON.stringify(window.__apLink)" in script:
            return self.link_state
        return "started"


def test_arena_connector_connect_opens_widget_and_polls_to_connected(web, monkeypatch):
    appmod, c, *_ = web
    state = {}
    _arena_connectors_fake(appmod, monkeypatch, state)
    engine = _FakeLinkEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    appmod._connector_link_pending = None

    r = c.post("/api/arena-connectors/github/connect", json={})
    assert r.status_code == 200 and r.get_json() == {"ok": True, "pending": True, "slug": "github"}
    assert engine.shown is True
    widget = next(s for s in engine.scripts if "AgentHandlerLink" in s)
    assert "tok-123" in widget and "ah-cdn.merge.dev/initialize.js" in widget

    poll = c.get("/api/arena-connectors/github/connect/poll").get_json()
    assert poll["done"] is False and poll["connected"] is False

    engine.link_state = '{"state": "success", "error": null}'
    poll = c.get("/api/arena-connectors/github/connect/poll").get_json()
    assert poll["done"] is True and poll["connected"] is True
    assert ("POST", "/api/connectors/sync") in state["calls"]
    assert engine.hidden is True
    assert appmod._connector_link_pending is None


# ---------- v4.2: Direct tools toggle, native Arena Agent, cloud labels ----------

def _sent_content(sent, index=-1):
    return sent[index][1]["userMessage"]["content"]


def test_direct_tools_toggle_controls_agent_loop(web, monkeypatch, tmp_path):
    appmod, c, _, use, sent = web
    monkeypatch.setattr(appmod, "_chosen_ws", lambda: tmp_path)

    # Tools off: plain turn, no agent stage event, no tool guide.
    use(FakeResp(['a0:"plain"']))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "direct", "modality": "text", "model_id": "m2", "tools": False}))
    chat_id = ev[0]["chat_id"]
    assert not any(e.get("type") == "agent" for e in ev)
    assert "run_cmd" not in _sent_content(sent)

    # Enabling tools mid-conversation sends the guide exactly once.
    use(FakeResp(['a0:"with tools"']))
    ev = events(c.post("/api/chat", json={
        "message": "again", "chat_id": chat_id, "mode": "direct", "modality": "text",
        "model_id": "m2", "tools": True}))
    assert any(e.get("type") == "agent" for e in ev)
    assert "run_cmd" in _sent_content(sent)

    use(FakeResp(['a0:"third"']))
    ev = events(c.post("/api/chat", json={
        "message": "third", "chat_id": chat_id, "mode": "direct", "modality": "text",
        "model_id": "m2", "tools": True}))
    assert any(e.get("type") == "agent" for e in ev)
    assert "run_cmd" not in _sent_content(sent)

    saved = appmod._load_chat(chat_id)
    assert saved["mode"] == "direct" and saved["tools_guide"] is True


def test_direct_tools_without_folder_stay_off(web, monkeypatch):
    appmod, c, _, use, sent = web
    monkeypatch.setattr(appmod, "_chosen_ws", lambda: None)
    use(FakeResp(['a0:"plain"']))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "direct", "modality": "text", "model_id": "m2", "tools": True}))
    assert not any(e.get("type") == "agent" for e in ev)


class _FakeNativeEngine:
    def __init__(self):
        self.window = object()
        self.challenge = None
        self.paths = []
        self.scripts = []
        self.shown = False

    def ready(self):
        return True

    def wait_ready(self, timeout=30):
        return True

    def show(self):
        self.shown = True

    def open_path(self, path):
        self.paths.append(path)
        return True

    def run_script(self, script):
        self.scripts.append(script)
        return "scheduled"


def test_native_agent_requires_engine_and_message(web):
    appmod, c, *_ = web
    r = c.post("/api/agent/native", json={"message": "do it"})
    assert r.status_code == 400
    payload = r.get_json()
    assert payload["code"] == "engine_required" and payload["fallback_url"].endswith("/agent")
    r = c.post("/api/agent/native", json={"message": "  "})
    assert r.status_code == 400


def test_native_agent_opens_site_and_fills_prompt(web, monkeypatch):
    import time as _time
    appmod, c, *_ = web
    engine = _FakeNativeEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    r = c.post("/api/agent/native", json={"message": "Сделай исследование рынка"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert engine.paths == ["/agent"] and engine.shown is True
    deadline = _time.time() + 3
    while _time.time() < deadline and not engine.scripts:
        _time.sleep(0.05)
    assert engine.scripts and "Сделай исследование рынка" in engine.scripts[0]


def test_arena_chats_annotates_local_agent_mode(web, monkeypatch):
    appmod, c, *_ = web
    eval_id = "12345678-1234-1234-1234-123456789abc"
    chat_id = "87654321-4321-4321-4321-cba987654321"
    appmod._save_chat(chat_id, title="Local agent chat",
                      messages=[{"role": "user", "text": "hi"}],
                      mode="agent", modality="text", eval_id=eval_id)

    def fake(path, timeout=30, method="GET"):
        assert path.startswith(appmod.contracts.PATHS["history_unified"])
        return {"entries": [
            {"id": eval_id, "type": "evaluation", "title": "Local agent chat",
             "mode": "direct-battle", "modality": "chat", "modelAId": "m1",
             "createdAt": "2026-11-01T10:00:00Z", "updatedAt": "2026-11-01T10:05:00Z"},
            {"id": "99999999-8888-7777-6666-555555555555", "type": "evaluation",
             "title": "Plain direct", "mode": "direct-battle", "modality": "chat",
             "modelAId": "m2", "createdAt": "2026-11-01T09:00:00Z",
             "updatedAt": "2026-11-01T09:05:00Z"},
        ]}, None, 200

    monkeypatch.setattr(appmod, "_arena_get_json", fake)
    payload = c.get("/api/arena-chats").get_json()
    first, second = payload["entries"]
    assert first["local_chat_id"] == chat_id and first["local_mode"] == "agent"
    assert first["mode"] == "direct"  # Arena-side truth stays visible in the payload
    assert second["local_chat_id"] is None and second["local_mode"] is None


def test_status_reports_app_version_and_no_store_ui(web):
    appmod, c, *_ = web
    payload = c.get("/api/status").get_json()
    assert payload["app_version"] == appmod.APP_VERSION
    assert c.get("/").headers.get("Cache-Control") == "no-store"
    assert c.get("/static/app.js").headers.get("Cache-Control") == "no-store"


# ---------- v4.3: native Arena Agent (in-app) ----------

_NATIVE_SESSION_ID = "11111111-1111-1111-1111-111111111111"
_NATIVE_TRIGGER = "22222222-2222-2222-2222-222222222222"


class _FakeEngineResp:
    def __init__(self, status, text):
        self.status_code = status
        self.text = text
        self.error = None


class _FakeNativeChatEngine:
    preflight_cx = "fake-genuine-cx"

    def __init__(self):
        self.window = object()
        self.challenge = None
        self.calls = []
        self.preflights = []

    def ready(self):
        return True

    def wait_ready(self, timeout=30):
        return True

    def sync_to(self, client):
        pass

    def native_preflight(self, action, timeout=45):
        self.preflights.append(action)
        return {"cx": self.preflight_cx, "token_len": 111, "trace": ["fake"]}

    def request(self, path, body=None, method=None, timeout=180, v3_action=None, headers=None):
        import json as _json
        self.calls.append((path, body, v3_action))
        self.last_headers = headers
        if path == "/nextjs-api/stream/create-chat":
            return _FakeEngineResp(200, _json.dumps({
                "id": _NATIVE_SESSION_ID,
                "bootstrap": {
                    "version": 1, "sessionId": _NATIVE_SESSION_ID, "userId": "u1",
                    "inputId": "i1", "taskId": "chat-agent",
                    "triggerAddress": _NATIVE_TRIGGER,
                    "publicAccessToken": "pat-1", "ttlMs": 60000,
                    "feedbackType": "check_in", "customFeedbackArm": None}}))
        if path == "/api/chat/trigger-session":
            return _FakeEngineResp(200, _json.dumps({"publicAccessToken": "pat-2"}))
        if path == "/api/chat/trigger-token":
            return _FakeEngineResp(200, _json.dumps({"token": "pat-3"}))
        if path.startswith("/api/connectors"):
            return _FakeEngineResp(200, _json.dumps({"connectors": [
                {"slug": "github", "displayName": "GitHub", "description": "d",
                 "authStatus": "connected"}]}))
        return _FakeEngineResp(404, "{}")


class _FakeStreamResp:
    def __init__(self, lines, status=200, headers=None):
        self.status_code = status
        self.headers = dict(headers or {})
        self._lines = lines

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def close(self):
        pass


def _native_lines(text):
    return [
        "id: 41",
        f'data: {{"type": "text-delta", "delta": "{text}"}}',
        "",
        "id: 42",
        'data: {"type": "tool-input-available", "toolCallId": "c1", '
        '"toolName": "web_search", "input": {"q": "x"}}',
        "",
        'data: {"type": "tool-output-available", "toolCallId": "c1", "output": {"hits": 2}}',
        "",
        'data: {"type": "trigger:turn-complete"}',
        "",
    ]


def _native_create_and_stream(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    appends = []
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_lines("Привет")))
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (appends.append((url, json)), _FakeStreamResp([], 200))[1])

    class _AppendResp:
        status_code = 200
        def json(self):
            return {"seq": 7}
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (appends.append((url, json)), _AppendResp())[1])

    ev = events(c.post("/api/chat", json={
        "message": "Сделай обзор", "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    chat_id = ev[0]["chat_id"]
    assert ev[0]["names"] == {"a": "Agent", "b": None}
    assert any(e.get("type") == "text" and e.get("text") == "Привет" for e in ev)
    assert any(e.get("type") == "tool_call" and e.get("tool") == "web_search" for e in ev)
    assert any(e.get("type") == "tool_result" and e.get("ok") is True for e in ev)
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Привет"

    # Direct API creation (like Direct sends); cx is the page-minted blob.
    create = next(call for call in engine.calls if call[0] == "/nextjs-api/stream/create-chat")
    assert create[2] == "agentic_chat_submit"
    assert create[1]["message"]["parts"][0]["text"] == "Сделай обзор"
    assert create[1]["message"]["id"][14] == "7"  # UUIDv7, like the website
    assert create[1]["cx"] == "fake-genuine-cx"
    assert create[1]["enabledConnectors"] == ["github"]
    assert engine.last_headers and engine.last_headers.get("X-Agent-Browser-Attempt-Id")
    assert engine.preflights and engine.preflights[0] == "agentic_chat_submit"
    assert appends == []  # first message rides inside create-chat

    saved = appmod._load_chat(chat_id)
    assert saved["mode"] == "agent" and saved["agent_backend"] == "arena"
    assert saved["native"]["session_id"] == _NATIVE_SESSION_ID
    assert saved["native"]["trigger_address"] == _NATIVE_TRIGGER
    assert saved["native"]["last_event_id"] == "42"
    assert saved["eval_id"] == _NATIVE_SESSION_ID
    assert saved["messages"][-1]["tools"][0]["status"] == "ok"
    return chat_id, engine, appends


def test_native_agent_create_and_stream(web, monkeypatch):
    _native_create_and_stream(web, monkeypatch)


def test_native_agent_followup_appends_without_recreate(web, monkeypatch):
    chat_id, engine, appends = _native_create_and_stream(web, monkeypatch)
    appmod, c, *_ = web
    creates_before = len([1 for call in engine.calls if call[0].endswith("create-chat")])
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_lines("Продолжаю")))

    ev = events(c.post("/api/chat", json={
        "message": "Дальше", "chat_id": chat_id, "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["text_a"] == "Продолжаю"
    assert len([1 for call in engine.calls if call[0].endswith("create-chat")]) == creates_before
    assert any(call[0] == "/api/chat/trigger-session" for call in engine.calls)
    assert len(appends) == 1
    url, payload = appends[0]
    assert url.endswith(f"/ai-proxy/realtime/v1/sessions/{_NATIVE_TRIGGER}/in/append")
    assert payload["kind"] == "message"
    assert payload["payload"]["chatId"] == _NATIVE_TRIGGER
    assert payload["payload"]["message"]["parts"][0]["text"] == "Дальше"
    assert payload["payload"]["metadata"]["cx"] == "fake-genuine-cx"


def test_native_agent_requires_engine_for_create(web):
    appmod, c, *_ = web
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "agent", "modality": "text", "agent_backend": "arena"}))
    errors = [e for e in ev if e.get("type") == "error"]
    assert errors and errors[0]["code"] == "engine_required"
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved["agent_backend"] == "arena"


class _FakeLadderEngine(_FakeNativeChatEngine):
    """Rejects create bodies that still carry enabledConnectors (HTTP 400)."""

    def request(self, path, body=None, method=None, timeout=180, v3_action=None, headers=None):
        if path == "/nextjs-api/stream/create-chat" and body and "enabledConnectors" in body:
            self.calls.append((path, body, v3_action))
            return _FakeEngineResp(400, '{"error": "Message validation failed. Please refresh and try again."}')
        return super().request(path, body=body, method=method, timeout=timeout,
                               v3_action=v3_action, headers=headers)


def test_native_agent_create_ladder_keeps_cx_drops_connectors(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeLadderEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_lines("Ок")))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Ок"
    creates = [call for call in engine.calls if call[0] == "/nextjs-api/stream/create-chat"]
    assert len(creates) == 2
    assert creates[0][1]["cx"] == "fake-genuine-cx" and "enabledConnectors" in creates[0][1]
    assert creates[1][1]["cx"] == "fake-genuine-cx" and "enabledConnectors" not in creates[1][1]


class _FakeNoCxEngine(_FakeNativeChatEngine):
    preflight_cx = None


def test_native_agent_create_without_cx_when_page_cannot_mint(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeNoCxEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_lines("Ок")))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Ок"
    creates = [call for call in engine.calls if call[0] == "/nextjs-api/stream/create-chat"]
    assert len(creates) == 1 and "cx" not in creates[0][1]


def _native_batch_lines(text):
    import json as _json
    body = _json.dumps({"id": "51", "data": {"type": "text-delta", "delta": text}})
    payload = {"records": [
        {"seq_num": 51, "headers": [["x-trigger-msgtype", "chunk"]], "body": body},
        {"seq_num": 52, "headers": [["trigger-control", "turn-complete"],
                                    ["session-in-event-id", "9"]], "body": ""},
    ]}
    return ["event: batch", f"data: {_json.dumps(payload)}", ""]


def test_native_agent_batch_control_record_ends_turn(web, monkeypatch):
    # The website ends a batch (v2) turn with a record whose HEADERS carry
    # trigger-control: turn-complete and whose body is empty — there is no
    # trigger:turn-complete chunk. v4.5.0 only checked the chunk type, so
    # the turn never ended by itself and the user had to press Stop.
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_batch_lines("Готово")))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Готово"
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert saved["native"]["last_event_id"] == "52"


def test_native_agent_settled_stream_ends_turn_without_signal(web, monkeypatch):
    # Fallback: the explicit signal was missed, but the session reports
    # itself settled and a whole cycle brings nothing new after activity.
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    responses = [
        _FakeStreamResp(["id: 61", 'data: {"type": "text-delta", "delta": "Финал"}', ""]),
        _FakeStreamResp([], headers={"X-Session-Settled": "true"}),
    ]
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None: responses.pop(0))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "agent", "modality": "text",
        "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Финал"
    assert not [e for e in ev if e.get("type") == "error"]


def test_native_agent_result_is_published_before_stream_ends(web, monkeypatch):
    # The SSE generator can be closed at any moment (Stop, client
    # disconnect); the caller persists the chat from a finally-block, so
    # the shared result must already carry the session coordinates, the
    # cursor and the partial text mid-stream. Losing them made every next
    # message open a brand-new chat.
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None:
                        _FakeStreamResp(_native_lines("Часть")))
    result = {"text": "", "reasoning": "", "tools": [], "native": None, "error": None}
    gen = appmod._native_agent_run(appmod.get_client(), "Вопрос", {},
                                   "Europe/Moscow", result=result)
    for raw in gen:
        if "Часть" in raw:
            break
    gen.close()
    assert result["text"] == "Часть"
    assert result["native"]["session_id"] == _NATIVE_SESSION_ID
    assert result["native"]["last_event_id"] == "41"


def test_native_agent_imported_chat_drains_backlog_then_continues(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    appmod._save_chat(_NATIVE_SESSION_ID, title="Импорт",
                      messages=[{"role": "user", "text": "Раньше"}],
                      mode="agent", modality="text", eval_id=_NATIVE_SESSION_ID,
                      source="arena", agent_backend="arena",
                      native={"backend": "arena", "session_id": _NATIVE_SESSION_ID,
                              "trigger_address": _NATIVE_SESSION_ID,
                              "last_event_id": None, "needs_drain": True})
    responses = [
        _FakeStreamResp(["id: 10", 'data: {"type": "text-delta", "delta": "Старый ответ"}', ""]),
        _FakeStreamResp([]),
        _FakeStreamResp(_native_lines("Новый ответ")),
    ]
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, headers=None, stream=False, timeout=None: responses.pop(0))
    appends = []

    class _AppendResp:
        status_code = 200

        def json(self):
            return {"seq": 3}
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        (appends.append((url, json)), _AppendResp())[1])

    ev = events(c.post("/api/chat", json={
        "message": "Продолжи", "chat_id": _NATIVE_SESSION_ID, "mode": "agent",
        "modality": "text", "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Новый ответ"
    assert not any("Старый" in (e.get("text") or "") for e in ev)
    assert len(appends) == 1
    assert not any(call[0].endswith("create-chat") for call in engine.calls)
    saved = appmod._load_chat(_NATIVE_SESSION_ID)
    assert saved["native"]["needs_drain"] is False
    assert saved["native"]["last_event_id"] == "42"
    assert [m["text"] for m in saved["messages"]] == ["Раньше", "Продолжи", "Новый ответ"]


# ---------- v4.6: chat lock, battle vote, snapshot cursor ----------

def test_started_chat_keeps_mode_modality_and_model(web):
    # After the first message a chat is locked to its stored selection:
    # a follow-up asking for another mode/model continues the very same
    # Arena conversation with the original parameters instead.
    appmod, c, _, use, sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    use(FakeResp(['a0:"First"']))
    ev = events(c.post("/api/chat", json={
        "message": "hi", "mode": "direct", "modality": "text", "model_id": "m1"}))
    chat_id = ev[0]["chat_id"]
    eval_id = appmod._load_chat(chat_id)["eval_id"]
    use(FakeResp(['a0:"Second"']))
    ev = events(c.post("/api/chat", json={
        "message": "again", "chat_id": chat_id, "mode": "battle",
        "modality": "image", "model_id": "m2"}))
    assert ev[0]["mode"] == "direct" and ev[-1]["text_a"] == "Second"
    assert sent[-1][0].endswith(f"/post-to-evaluation/{eval_id}")
    assert sent[-1][1]["modelAId"] == "m1"
    saved = appmod._load_chat(chat_id)
    assert saved["mode"] == "direct" and saved["modality"] == "text"
    assert saved["model_a"] == "m1" and saved["eval_id"] == eval_id
    assert [m["text"] for m in saved["messages"] if m["role"] == "user"] == ["hi", "again"]


class _FakeVoteEngine(_FakeNativeChatEngine):
    def __init__(self):
        super().__init__()
        self.votes = []

    def pairwise_vote(self, args, timeout=45):
        self.votes.append(args)
        return {"ok": True, "error": None, "trace": [],
                "result": {"success": True, "error": None,
                           "modelAId": "mid-a", "modelBId": "mid-b", "arenaId": "arena-1",
                           "revealed": [{"id": "mid-a", "name": "Alpha Model"},
                                        {"id": "mid-b", "name": "Beta Model"}]}}


_BATTLE_CHAT_ID = "55555555-5555-4555-8555-555555555555"
_BATTLE_EVAL_ID = "66666666-6666-4666-8666-666666666666"


def _save_battle_chat(appmod, with_ids=True):
    message = {"role": "assistant", "text": "Ответ A", "text_b": "Ответ B",
               "names": {"a": None, "b": None}}
    if with_ids:
        message["arena_message_ids"] = {"a": "msg-a", "b": "msg-b"}
    appmod._save_chat(_BATTLE_CHAT_ID, title="Бой", mode="battle", modality="text",
                      eval_id=_BATTLE_EVAL_ID,
                      messages=[{"role": "user", "text": "Вопрос"}, message])


def test_battle_vote_reveals_models_and_persists(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeVoteEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    _save_battle_chat(appmod)
    r = c.post(f"/api/chats/{_BATTLE_CHAT_ID}/vote", json={
        "value": "model_a", "pair_index": 0, "message_a_id": "msg-a", "message_b_id": "msg-b"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] and body["vote"] == "model_a"
    assert body["names"] == {"a": "Alpha Model", "b": "Beta Model"}
    assert body["model_a"] == "mid-a" and body["model_b"] == "mid-b"
    assert engine.votes == [{"evaluationSessionId": _BATTLE_EVAL_ID,
                             "messageAId": "msg-a", "messageBId": "msg-b", "value": "model_a"}]
    saved = appmod._load_chat(_BATTLE_CHAT_ID)
    assert saved["messages"][1]["vote"] == "model_a"
    assert saved["messages"][1]["names"] == {"a": "Alpha Model", "b": "Beta Model"}
    assert saved["model_a"] == "mid-a" and saved["model_b"] == "mid-b"
    again = c.post(f"/api/chats/{_BATTLE_CHAT_ID}/vote", json={"value": "tie", "pair_index": 0})
    assert again.status_code == 409
    assert len(engine.votes) == 1


def test_battle_vote_resolves_message_ids_from_session(web, monkeypatch):
    appmod, c, *_ = web
    engine = _FakeVoteEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    _save_battle_chat(appmod, with_ids=False)

    def fake_get(path, timeout=30):
        assert _BATTLE_EVAL_ID in path
        return {"messages": [
            {"id": "u1", "role": "user", "content": "Q", "createdAt": "2026-01-01T00:00:00Z"},
            {"id": "srv-b", "role": "assistant", "participantPosition": "b",
             "parentMessageIds": ["u1"], "createdAt": "2026-01-01T00:00:02Z", "content": "B"},
            {"id": "srv-a", "role": "assistant", "participantPosition": "a",
             "parentMessageIds": ["u1"], "createdAt": "2026-01-01T00:00:01Z", "content": "A"},
        ]}, None, 200

    monkeypatch.setattr(appmod, "_arena_get_json", fake_get)
    r = c.post(f"/api/chats/{_BATTLE_CHAT_ID}/vote", json={"value": "tie", "pair_index": 0})
    assert r.status_code == 200
    assert engine.votes[0]["messageAId"] == "srv-a"
    assert engine.votes[0]["messageBId"] == "srv-b"
    saved = appmod._load_chat(_BATTLE_CHAT_ID)
    assert saved["messages"][1]["arena_message_ids"] == {"a": "srv-a", "b": "srv-b"}
    assert saved["messages"][1]["vote"] == "tie"


def test_battle_vote_rejects_bad_requests(web):
    appmod, c, *_ = web
    _save_battle_chat(appmod)
    bad_value = c.post(f"/api/chats/{_BATTLE_CHAT_ID}/vote", json={"value": "c"})
    assert bad_value.status_code == 400
    # Without the desktop engine the vote cannot be cast honestly.
    no_engine = c.post(f"/api/chats/{_BATTLE_CHAT_ID}/vote", json={"value": "model_a", "pair_index": 0})
    assert no_engine.status_code == 503
    direct_id = "77777777-7777-4777-8777-777777777777"
    appmod._save_chat(direct_id, title="D", mode="direct", modality="text", eval_id=_BATTLE_EVAL_ID,
                      messages=[{"role": "user", "text": "Q"},
                                {"role": "assistant", "text": "A", "text_b": "", "names": {"a": "M", "b": None}}])
    wrong_mode = c.post(f"/api/chats/{direct_id}/vote", json={"value": "model_a"})
    assert wrong_mode.status_code == 400


def test_native_agent_resume_uses_session_snapshot_cursor(web, monkeypatch):
    # Old/imported session with no stored cursor: the snapshot's
    # lastEventId lets the turn subscribe at the head directly — no
    # backlog draining cycles at all.
    appmod, c, *_ = web
    engine = _FakeNativeChatEngine()
    monkeypatch.setattr(appmod, "ENGINE", engine)
    monkeypatch.setattr(appmod, "_arena_get_json",
                        lambda path, timeout=30: ({"messages": [], "lastEventId": "500"}, None, 200))
    appmod._save_chat(_NATIVE_SESSION_ID, title="Старый",
                      messages=[{"role": "user", "text": "Раньше"}],
                      mode="agent", modality="text", eval_id=_NATIVE_SESSION_ID,
                      agent_backend="arena",
                      native={"backend": "arena", "session_id": _NATIVE_SESSION_ID,
                              "trigger_address": _NATIVE_SESSION_ID,
                              "last_event_id": None, "needs_drain": True})
    seen_headers = []

    def fake_get(url, headers=None, stream=False, timeout=None):
        seen_headers.append(headers or {})
        return _FakeStreamResp(_native_lines("Продолжили"))
    monkeypatch.setattr(appmod.requests, "get", fake_get)

    class _AppendResp:
        status_code = 200

        def json(self):
            return {"seq": 9}
    monkeypatch.setattr(appmod.requests, "post",
                        lambda url, headers=None, json=None, timeout=None: _AppendResp())

    ev = events(c.post("/api/chat", json={
        "message": "Дальше", "chat_id": _NATIVE_SESSION_ID, "mode": "agent",
        "modality": "text", "agent_backend": "arena", "timezone": "Europe/Moscow"}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Продолжили"
    assert len(seen_headers) == 1
    assert seen_headers[0].get("Last-Event-ID") == "500"
    saved = appmod._load_chat(_NATIVE_SESSION_ID)
    assert saved["native"]["needs_drain"] is False
    assert saved["native"]["last_event_id"] == "42"


def test_iter_records_idle_bounds_a_silent_stream(web):
    appmod, *_ = web

    class _SlowResp:
        status_code = 200
        headers = {}

        def iter_lines(self, decode_unicode=False):
            yield 'data: {"type": "text-delta", "delta": "x"}'
            yield ""
            time.sleep(3)
            yield 'data: {"type": "trigger:turn-complete"}'
            yield ""

        def close(self):
            pass

    started = time.monotonic()
    records = list(appmod._iter_records_idle(_SlowResp(), 0.3))
    assert len(records) == 1
    assert time.monotonic() - started < 2.0


class HangingResp:
    """Stream that delivers its lines, then never closes (half-open)."""

    status_code = 200
    headers = {}
    error = None

    def __init__(self, lines, gate):
        self._lines = list(lines)
        self._gate = gate
        self._text = ""

    def iter_text(self):
        for line in self._lines:
            yield line + "\n"
        self._gate.wait(timeout=30)
        yield 'a0:"too late"\n'

    @property
    def text(self):
        return self._text


def test_stream_ends_on_finish_record_without_waiting_for_close(web):
    import threading as _threading
    appmod, c, _, use, _sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    gate = _threading.Event()
    use(HangingResp(['a0:"Hello"', 'ad:{"finishReason":"stop"}'], gate))
    result = {}

    def run():
        with appmod.app.test_client() as client:
            result["ev"] = events(client.post(
                "/api/chat", json={"message": "hi", "mode": "direct", "modality": "text",
                                   "model_id": "m1", "tools": False}))

    thread = _threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=10)
    try:
        assert not thread.is_alive(), "generation hung on a half-open stream"
        assert result["ev"][-1]["type"] == "done"
        assert result["ev"][-1]["text_a"] == "Hello"
    finally:
        gate.set()
        thread.join(timeout=5)


def test_agent_empty_final_round_leaves_explaining_text(web):
    appmod, c, cfg, use, _sent = web
    cfg["permission_mode"] = "bypassPermissions"
    appmod._ui_mode = "agent"
    use(FakeResp([_tool_line('<tool name="ls"><path>.</path></tool>')]),
        FakeResp(['ad:{"finishReason":"stop"}']))
    ev = events(c.post("/api/chat", json={"message": "look around"}))
    done = ev[-1]
    assert done["type"] == "done"
    assert "finished without a final message" in done["text_a"]
    saved = appmod._load_chat(ev[0]["chat_id"])
    assert "finished without a final message" in saved["messages"][-1]["text"]


def test_invoke_style_tool_calls_are_parsed_and_stripped():
    text = ('Working.\n<function_calls><invoke name="write_file">'
            '<parameter name="path">a.txt</parameter>'
            '<parameter name="content">hi</parameter></invoke></function_calls>\nDone.')
    calls = agent.parse_tool_calls(text)
    assert calls == [{"name": "write_file", "params": {"path": "a.txt", "content": "hi"}}]
    assert agent.strip_tool_calls(text) == "Working.\n\nDone."


class _FakeDownload:
    def __init__(self, content=b""):
        self.content = content

    def raise_for_status(self):
        pass


def _bridge_events(gen):
    events, value = [], None
    while True:
        try:
            chunk = next(gen)
        except StopIteration as stop:
            value = stop.value
            break
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[6:]))
    return events, value


def test_native_file_bridge_downloads_and_dedupes(web, monkeypatch, tmp_path):
    appmod, _c, cfg, _use, _sent = web
    cfg["permission_mode"] = "bypassPermissions"
    cfg["workspace_dir"] = str(tmp_path / "picked")
    monkeypatch.setattr(appmod.requests, "get", lambda url, timeout=None: _FakeDownload(b"print('hi')\n"))
    state = {}
    files = [{"url": "https://example.test/snake.py", "title": "snake.py", "kind": "file"}]
    ev, written = _bridge_events(appmod._native_file_bridge(files, state))
    assert written == ["snake.py"]
    assert (tmp_path / "picked" / "snake.py").read_bytes() == b"print('hi')\n"
    assert [e for e in ev if e["type"] == "files_written"][0]["written"] == ["snake.py"]
    assert state["synced_files"]["snake.py"]
    # Same content next turn: nothing is written again.
    _ev2, written2 = _bridge_events(appmod._native_file_bridge(files, state))
    assert written2 == []


def test_native_file_bridge_respects_plan_and_sanitizes_names(web, monkeypatch, tmp_path):
    appmod, _c, cfg, _use, _sent = web
    cfg["workspace_dir"] = str(tmp_path / "picked")
    monkeypatch.setattr(appmod.requests, "get", lambda url, timeout=None: _FakeDownload(b"x"))
    files = [{"url": "https://example.test/evil", "title": "../../evil.txt", "kind": "file"}]
    cfg["permission_mode"] = "plan"
    plan_state = {}
    _ev, written = _bridge_events(appmod._native_file_bridge(files, plan_state))
    assert written == [] and not (tmp_path / "picked").exists()
    cfg["permission_mode"] = "bypassPermissions"
    ok_state = {}
    _ev2, written2 = _bridge_events(appmod._native_file_bridge(files, ok_state))
    assert written2 == ["evil.txt"]
    assert (tmp_path / "picked" / "evil.txt").read_bytes() == b"x"
    assert not (tmp_path.parent / "evil.txt").exists()


def test_native_file_bridge_without_folder_only_notifies(web, monkeypatch):
    appmod, _c, cfg, _use, _sent = web
    cfg.pop("workspace_dir", None)
    monkeypatch.setattr(appmod.requests, "get",
                        lambda url, timeout=None: (_ for _ in ()).throw(AssertionError("must not download")))
    ev, written = _bridge_events(appmod._native_file_bridge(
        [{"url": "https://example.test/a.txt", "title": "a.txt", "kind": "file"}], {}))
    assert written == []
    assert any("Choose a folder" in (e.get("text") or "") for e in ev)


def test_tool_loop_stops_repeating_identical_calls(web):
    appmod, c, cfg, use, _sent = web
    cfg["permission_mode"] = "bypassPermissions"
    appmod._ui_mode = "agent"
    same = FakeResp([_tool_line('<tool name="ls"><path>.</path></tool>')])
    use(same, FakeResp([_tool_line('<tool name="ls"><path>.</path></tool>')]),
        FakeResp([_tool_line('<tool name="ls"><path>.</path></tool>')]),
        FakeResp([_tool_line('<tool name="ls"><path>.</path></tool>')]))
    ev = events(c.post("/api/chat", json={"message": "keep listing"}))
    results = [e for e in ev if e["type"] == "tool_result"]
    assert len(results) == 3
    assert any("kept repeating" in (e.get("text") or "") for e in ev if e["type"] == "notice")
    assert ev[-1]["type"] == "done"


def test_empty_reply_explains_context_limit(web):
    appmod, c, _cfg, use, _sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    use(FakeResp(['ad:{"finishReason":"stop"}']))
    ev = events(c.post("/api/chat", json={"message": "continue", "mode": "direct",
                                          "modality": "text", "model_id": "m1", "tools": False}))
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == ""
    assert any("empty reply" in (e.get("text") or "") for e in ev if e["type"] == "notice")


def test_long_outputs_offload_to_artifacts(tmp_path):
    root = tmp_path / "ws"
    artifacts = root / agent.ARTIFACTS_DIR
    big = "x" * 5000
    msg = agent.format_results([({"name": "run_cmd", "params": {"cmd": "big"}}, "ok", big)],
                                artifact_dir=artifacts)
    assert len(msg) < 1200
    assert "x" * 1000 not in msg
    assert ".arena-artifacts/run_cmd-" in msg
    saved = list(artifacts.iterdir())
    assert len(saved) == 1 and saved[0].read_text() == big
    # Short outputs still travel inline, untouched.
    small = agent.format_results([({"name": "ls", "params": {"path": "."}}, "ok", "a/")],
                                 artifact_dir=artifacts)
    assert "a/" in small and len(list(artifacts.iterdir())) == 1


def test_trim_output_collapses_repeats_and_ansi():
    noisy = "\x1b[31merr\x1b[0m\n" + "same\n" * 50 + "tail"
    out = agent.trim_output(noisy)
    assert "\x1b" not in out and "err" in out and "tail" in out
    assert out.count("same") <= 3 and "same line x" in out


def test_read_file_ranges_and_large_file_hint(tmp_path):
    tools = agent.LocalTools(tmp_path)
    f = tmp_path / "a.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 101)))
    ok, out = tools.run({"name": "read_file",
                         "params": {"path": "a.txt", "start_line": "10", "end_line": "12"}})
    assert ok and out.startswith("[lines 10-12 of 100]") and "line11" in out and "line13" not in out
    big = tmp_path / "big.txt"
    big.write_text("y" * 9000)
    ok2, out2 = tools.run({"name": "read_file", "params": {"path": "big.txt"}})
    assert ok2 and len(out2) < 6500 and "start_line" in out2


class _StubConnectors:
    def ensure_started(self, timeout=30):
        return {"srv_tool": {"server": "srv", "input_schema": {}}}

    def guide(self, tools):
        return "MCPGUIDE-TEXT"


def test_mcp_guide_not_resent_when_unchanged(web, monkeypatch):
    import hashlib as _hashlib
    appmod, c, cfg, use, sent = web
    cfg["permission_mode"] = "bypassPermissions"
    monkeypatch.setattr(appmod, "CONNECTORS", _StubConnectors())
    appmod._ui_mode = "agent"
    cid = "33333333-3333-7333-8333-333333333333"
    digest = _hashlib.sha1(b"MCPGUIDE-TEXT").hexdigest()[:12]
    appmod._save_chat(cid, title="t", messages=[{"role": "user", "text": "hi"},
                                                {"role": "assistant", "text": "hi"}],
                      mode="agent", modality="text", model_a="m1",
                      eval_id="11111111-1111-7111-8111-111111111111",
                      tools_guide=True, agent_backend="local", mcp_guide_hash=digest)
    use(FakeResp(['a0:"done"']))
    events(c.post("/api/chat", json={"message": "next", "chat_id": cid}))
    assert "MCPGUIDE-TEXT" not in sent[0][1]["userMessage"]["content"]
    # A changed guide (hash mismatch) is sent again.
    appmod._save_chat(cid, title="t", messages=[{"role": "user", "text": "hi"},
                                                {"role": "assistant", "text": "hi"}],
                      mode="agent", modality="text", model_a="m1",
                      eval_id="11111111-1111-7111-8111-111111111111",
                      tools_guide=True, agent_backend="local", mcp_guide_hash="stale")
    use(FakeResp(['a0:"done"']))
    events(c.post("/api/chat", json={"message": "next", "chat_id": cid}))
    assert "MCPGUIDE-TEXT" in sent[-1][1]["userMessage"]["content"]


def test_long_chat_warns_once_when_crossing_limit(web):
    appmod, c, _cfg, use, _sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    cid = "44444444-4444-7444-8444-444444444444"
    appmod._save_chat(cid, title="t",
                      messages=[{"role": "user", "text": "q"},
                                {"role": "assistant", "text": "z" * 79_500}],
                      mode="direct", modality="text", model_a="m1",
                      eval_id="22222222-2222-7222-8222-222222222222")
    use(FakeResp(["a0:" + json.dumps("w" * 1000)]))
    ev = events(c.post("/api/chat", json={"message": "next", "chat_id": cid,
                                          "mode": "direct", "modality": "text",
                                          "model_id": "m1", "tools": False}))
    assert any("getting long" in (e.get("text") or "") for e in ev if e["type"] == "notice")


def test_memory_sent_once_per_session_and_on_change(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent = web
    ws = tmp_path / "proj"; ws.mkdir()
    (ws / "MEMORY.md").write_text("- PROJECT-FACT-42: DB is Postgres\n", encoding="utf-8")
    gmem = tmp_path / "gmem.md"
    gmem.write_text("- GLOBAL-FACT-7: short answers\n", encoding="utf-8")
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", gmem)
    cfg["workspace_dir"] = str(ws)
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    use(FakeResp(['a0:"ok"']))
    ev = events(c.post("/api/chat", json={"message": "hello", "mode": "direct",
                                          "modality": "text", "model_id": "m1", "tools": False}))
    first = sent[-1][1]["userMessage"]["content"]
    assert "PROJECT-FACT-42" in first and "GLOBAL-FACT-7" in first
    cid = next(e["chat_id"] for e in ev if e.get("chat_id"))
    # Same Arena session: memory is not resent.
    use(FakeResp(['a0:"ok2"']))
    events(c.post("/api/chat", json={"message": "again", "chat_id": cid, "mode": "direct",
                                     "modality": "text", "model_id": "m1", "tools": False}))
    assert "PROJECT-FACT-42" not in sent[-1][1]["userMessage"]["content"]
    # Edited memory: sent again with the new text.
    (ws / "MEMORY.md").write_text("- PROJECT-FACT-43: now SQLite\n", encoding="utf-8")
    use(FakeResp(['a0:"ok3"']))
    events(c.post("/api/chat", json={"message": "third", "chat_id": cid, "mode": "direct",
                                     "modality": "text", "model_id": "m1", "tools": False}))
    assert "PROJECT-FACT-43" in sent[-1][1]["userMessage"]["content"]


def test_compact_arms_digest_and_next_send_uses_it(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent = web
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", tmp_path / "none.md")
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    cid = "55555555-5555-7555-8555-555555555555"
    msgs = [{"role": "user", "text": "Build a landing page for a bakery"},
            {"role": "assistant", "text": "Created index.html with hero section",
             "tools": [{"tool": "write_file", "summary": "Write file index.html"}]},
            {"role": "user", "text": "Add a dark theme toggle"},
            {"role": "assistant", "text": "Dark theme added via CSS variables"}]
    appmod._save_chat(cid, title="Bakery landing", messages=msgs, mode="direct",
                      modality="text", model_a="m1",
                      eval_id="66666666-6666-7666-8666-666666666666")
    r = c.post(f"/api/chats/{cid}/compact", json={})
    assert r.status_code == 200
    saved = appmod._load_chat(cid)
    assert saved["eval_id"] is None
    digest = saved["pending_digest"] or ""
    assert 0 < len(digest) <= 6000
    assert "landing page for a bakery" in digest and "index.html" in digest
    assert saved["messages"][-1].get("compact") is True
    # Next send: fresh session seeded with the digest; digest then consumed.
    use(FakeResp(['a0:"continued"']))
    events(c.post("/api/chat", json={"message": "Now add a footer", "chat_id": cid,
                                     "mode": "direct", "modality": "text",
                                     "model_id": "m1", "tools": False}))
    content = sent[-1][1]["userMessage"]["content"]
    assert "continued conversation" in content and "dark theme" in content.lower()
    assert content.rstrip().endswith("Now add a footer")
    assert not (appmod._load_chat(cid).get("pending_digest") or "")


def test_compact_appends_digest_to_workspace_memory(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent = web
    ws = tmp_path / "proj2"; ws.mkdir()
    cfg["workspace_dir"] = str(ws)
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", tmp_path / "none.md")
    cid = "77777777-7777-7777-8777-777777777777"
    appmod._save_chat(cid, title="Big refactor",
                      messages=[{"role": "user", "text": "Refactor the parser module"},
                                {"role": "assistant", "text": "x" * 9000}],
                      mode="direct", modality="text", model_a="m1",
                      eval_id="88888888-8888-7888-8888-888888888888")
    r = c.post(f"/api/chats/{cid}/compact", json={})
    assert r.status_code == 200 and r.get_json()["memory_saved"] is True
    mem = (ws / "MEMORY.md").read_text(encoding="utf-8")
    assert "Session digest" in mem and "Big refactor" in mem and "parser module" in mem
    # The context meter restarts at the digest, not the 9k of old history.
    saved = appmod._load_chat(cid)
    assert appmod._history_chars(saved["messages"]) < 7000


def test_compact_guards_and_memory_endpoints(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent = web
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", tmp_path / "mem" / "memory.md")
    cid = "99999999-9999-7999-8999-999999999999"
    appmod._save_chat(cid, title="t", messages=[{"role": "user", "text": "hi"}],
                      mode="direct", modality="text", model_a="m1")
    assert c.post(f"/api/chats/{cid}/compact", json={}).status_code == 400
    r = c.post("/api/memory", json={"global": "- be brief", "enabled": True})
    assert r.status_code == 200
    got = c.get("/api/memory").get_json()
    assert got["global"] == "- be brief" and got["enabled"] is True
    # Disabled memory is never injected.
    c.post("/api/memory", json={"enabled": False})
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    use(FakeResp(['a0:"ok"']))
    events(c.post("/api/chat", json={"message": "hello", "mode": "direct",
                                     "modality": "text", "model_id": "m1", "tools": False}))
    assert "be brief" not in sent[-1][1]["userMessage"]["content"]


def _delegate_parent(web, tmp_path, monkeypatch):
    """A local-agent parent chat ready to receive a delegated turn."""
    appmod, c, cfg, use, sent = web
    ws = tmp_path / "ws"; ws.mkdir(exist_ok=True)
    cfg["workspace_dir"] = str(ws)
    cfg["permission_mode"] = "bypassPermissions"
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", tmp_path / "none.md")
    appmod._ui_mode, appmod._ui_modality = "agent", "text"
    cid = "aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    appmod._save_chat(cid, title="Parent",
                      messages=[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hi"}],
                      mode="agent", modality="text", model_a="m1",
                      eval_id="bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb",
                      tools_guide=True, agent_backend="local", delegate_guide=True)
    return appmod, c, cfg, use, sent, cid, ws


def test_subagent_delegation_flow(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent, cid, ws = _delegate_parent(web, tmp_path, monkeypatch)
    delegate = ('<tool name="delegate_task"><task>Inspect the project layout</task>'
                '<mode>research</mode><model>m2</model></tool>')
    use(FakeResp(["a0:" + json.dumps(delegate)]),
        FakeResp(['a0:"Summary: two files, entry at main.py:1"']),
        FakeResp(['a0:"Parent final answer"']))
    ev = events(c.post("/api/chat", json={"message": "delegate please", "chat_id": cid}))
    assert len(sent) == 3
    child_body = sent[1][1]
    assert child_body.get("modelAId") == "m2"  # the parent picked the model
    assert "sub-agent" in child_body["userMessage"]["content"]
    assert "Inspect the project layout" in child_body["userMessage"]["content"]
    # Only the child's summary travels back into the parent conversation.
    assert "Summary: two files" in sent[2][1]["userMessage"]["content"]
    # The child is a real chat nested under the parent.
    parent = appmod._load_chat(cid)
    step = next(t for m in parent["messages"] for t in (m.get("tools") or [])
                if t.get("tool") == "delegate_task")
    child = appmod._load_chat(step["child_id"])
    assert child["parent_id"] == cid and child["mode"] == "direct"
    listed = {c0["id"]: c0 for c0 in c.get("/api/chats").get_json()}
    assert step["child_id"] not in listed
    assert listed[cid]["children"][0]["id"] == step["child_id"]
    assert any(e.get("type") == "subagent" and e.get("status") == "done" for e in ev)


def test_subagent_research_is_read_only_and_cannot_delegate(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent, cid, ws = _delegate_parent(web, tmp_path, monkeypatch)
    delegate = '<tool name="delegate_task"><task>Try to write and delegate</task></tool>'
    child_try = ('<tool name="write_file"><path>pwned.txt</path><content>x</content></tool>'
                 '<tool name="delegate_task"><task>nested</task></tool>')
    use(FakeResp(["a0:" + json.dumps(delegate)]),
        FakeResp(["a0:" + json.dumps(child_try)]),
        FakeResp(['a0:"Child summary"']),
        FakeResp(['a0:"Parent done"']))
    events(c.post("/api/chat", json={"message": "go", "chat_id": cid}))
    assert not (ws / "pwned.txt").exists()
    child_followup = sent[2][1]["userMessage"]["content"]
    assert "read-only" in child_followup
    assert "not available inside a sub-agent" in child_followup


def test_update_check_compares_versions(web, monkeypatch):
    appmod, c, cfg, use, sent = web
    class _R:
        status_code = 200
        def __init__(self, tag):
            self._tag = tag
        def json(self):
            return {"tag_name": self._tag, "html_url": "https://example.invalid/rel"}
    appmod._update_cache.update(t=0.0, data=None)
    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: _R("v9.9.9"))
    got = c.get("/api/update-check?force=1").get_json()
    assert got["update"] is True and got["latest"] == "9.9.9"
    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: _R("v0.0.1"))
    got = c.get("/api/update-check?force=1").get_json()
    assert got["update"] is False


def test_settings_export_import_roundtrip(web, monkeypatch, tmp_path):
    appmod, c, cfg, use, sent = web
    monkeypatch.setattr(appmod, "GLOBAL_MEMORY_FILE", tmp_path / "mem" / "memory.md")
    c.post("/api/memory", json={"global": "- fact A", "enabled": True})
    bundle = c.get("/api/settings/export").get_json()
    assert bundle["app"] == "Arena Desktop" and bundle["memory_global"] == "- fact A"
    assert "cookie" not in json.dumps(bundle).lower()
    c.post("/api/memory", json={"global": "- fact B"})
    r = c.post("/api/settings/import", json=bundle)
    assert r.status_code == 200
    assert c.get("/api/memory").get_json()["global"] == "- fact A"
    backups = list((tmp_path / "backups").glob("settings-*.json"))
    assert len(backups) == 1
    assert c.post("/api/settings/import", json={"app": "Other"}).status_code == 400


def test_diagnostics_redacts_url_queries(web, tmp_path):
    appmod, c, cfg, use, sent = web
    (tmp_path / "desktop.log").write_text(
        "2026 INFO fetch https://arena.ai/api/chat?token=SECRET1 ok\n", encoding="utf-8")
    text = c.get("/api/diagnostics").get_json()["text"]
    assert "SECRET1" not in text and "arena.ai/api/chat" in text
    assert appmod.APP_VERSION in text


def test_portable_config_dir(tmp_path, monkeypatch):
    import sys as _sys
    from arena import config as cfgmod
    exe_dir = tmp_path / "app"; exe_dir.mkdir()
    (exe_dir / "portable.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(_sys, "frozen", True, raising=False)
    monkeypatch.setattr(_sys, "executable", str(exe_dir / "Arena-Desktop.exe"))
    assert cfgmod._config_dir() == exe_dir / "data"
    monkeypatch.setattr(_sys, "frozen", False, raising=False)
    assert cfgmod._config_dir().name == ".arena-personal"
