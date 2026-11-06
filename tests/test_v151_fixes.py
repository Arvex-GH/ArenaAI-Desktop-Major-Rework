"""v1.5.1 fixes: per-chat folder pin, no resend after a broken stream,
session-full and human-check errors, paragraph breaks, refresh merge
repair, preview links, status.arena.ai notices, and the 1,500-line limit
for backend files."""
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_core import FakeResp, events, web  # noqa: E402,F401  (web is a fixture)

from arena import contracts, history_sync  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def _drain(gen):
    while True:
        try:
            next(gen)
        except StopIteration as stop:
            return stop.value


def test_backend_files_stay_under_1500_lines():
    too_long = {}
    for path in (REPO / "arena").rglob("*.py"):
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > 1500:
            too_long[str(path.relative_to(REPO))] = count
    assert not too_long, too_long


def test_build_bundles_every_backend_part():
    from arena.web import app as appmod
    spec = (REPO / "arena-cli.spec").read_text(encoding="utf-8")
    for part in appmod.PARTS:
        assert (REPO / "arena" / "web" / "server" / f"{part}.py").exists()
        assert f"'arena.web.server.{part}'" in spec


def test_parts_share_one_module_namespace(web):
    appmod = web[0]
    # a patch on arena.web.app reaches functions defined in any part
    assert appmod._turn_auto.__globals__ is vars(appmod)
    assert appmod.run_server.__module__ == "arena.web.app"


def test_reply_keeps_the_folder_it_started_with(web, tmp_path):
    appmod, _c, cfg, _use, _sent = web
    first, second = tmp_path / "first", tmp_path / "second"
    cfg["workspace_dir"] = str(second)
    seen = []

    def reply():
        seen.append(appmod._workspace_dir())
        cfg["workspace_dir"] = str(tmp_path / "third")  # another chat picks a folder
        seen.append(appmod._chosen_ws())
        yield "x"

    list(appmod._sse_wrap(reply(), chat_id=None, workspace_dir=first))
    assert seen == [first, first]
    assert appmod._chosen_ws() == tmp_path / "third"  # the pin ends with the reply


def test_broken_stream_reads_the_reply_instead_of_resending(web, monkeypatch):
    appmod, c, _cfg, use, sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    monkeypatch.setattr(appmod, "_wait_for", lambda check, seconds: ())
    asked = []

    def remote(eval_id, prompt=""):
        asked.append(prompt)
        return "Hello there, the full answer.", True, None

    monkeypatch.setattr(appmod, "_remote_last_reply", remote)
    use(FakeResp(['a0:"Hello there"'], finish=False))
    ev = events(c.post("/api/chat", json={"message": "hi", "mode": "direct", "modality": "text",
                                          "model_id": "m1", "tools": False}))
    assert len(sent) == 1  # arena took it once; it is never sent twice
    assert "hi" in asked[0]
    assert any(e["type"] == "text_replace" and e["text"] == "Hello there, the full answer." for e in ev)
    assert ev[-1]["type"] == "done" and ev[-1]["text_a"] == "Hello there, the full answer."


def test_recovery_never_takes_the_reply_to_an_older_prompt(web, monkeypatch):
    appmod = web[0]
    session = {"messages": [
        {"role": "user", "content": "old question", "createdAt": "1"},
        {"role": "assistant", "content": "old answer", "status": "success", "createdAt": "2"}]}
    monkeypatch.setattr(appmod, "_arena_get_json", lambda *a, **k: (session, None, 200))
    assert appmod._remote_last_reply("e1", "new question") == ("", False, None)
    assert appmod._remote_last_reply("e1", "old question") == ("old answer", True, None)


def test_session_limit_and_human_check_are_named():
    full = contracts.classify_error(400, '{"error":"This session has reached its token usage limit."}')
    assert full["code"] == "session_full"
    assert contracts.classify_error(200, "Please verify you are human")["code"] == "challenge"
    assert contracts.classify_error(403, "I'm not a robot")["code"] == "challenge"


def test_full_session_continues_in_a_fresh_one(web):
    appmod, c, _cfg, use, sent = web
    appmod._ui_mode, appmod._ui_modality = "direct", "text"
    cid = str(uuid.uuid4())
    appmod._save_chat(cid, title="old", mode="direct", modality="text", model_a="m1", eval_id="full-eval",
                      messages=[{"role": "user", "text": "earlier"}, {"role": "assistant", "text": "reply"}])
    use(FakeResp(status=400, text='{"error":"This session has reached its token usage limit."}'),
        FakeResp(['a0:"fresh answer"']))
    ev = events(c.post("/api/chat", json={"message": "next", "chat_id": cid, "mode": "direct",
                                          "modality": "text", "model_id": "m1", "tools": False}))
    assert any("session is full" in (e.get("text") or "") for e in ev if e["type"] == "notice")
    assert ev[-1]["text_a"] == "fresh answer"
    assert len(sent) == 2 and sent[1][0] != sent[0][0]  # the second send opened a new session


def test_stream_text_after_a_step_starts_a_new_paragraph(web):
    appmod = web[0]
    resp = FakeResp(['a0:"Setup done."', 'ae:{"finishReason":"tool-calls"}', 'a0:"Next step."'])
    out = _drain(appmod._line_events(resp, {"mode": "direct"}))
    assert out["a"] == "Setup done.\n\nNext step."


def test_history_text_between_tool_parts_gets_paragraph_breaks():
    msg = {"role": "assistant", "parts": [
        {"type": "text", "text": "Setting up."},
        {"type": "tool-invocation", "toolInvocation": {"toolName": "bash"}},
        {"type": "text", "text": "I will now read the file."}]}
    assert history_sync.message_text(msg) == "Setting up.\n\nI will now read the file."


def test_refresh_fills_a_cut_off_reply_in_place():
    local = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello th", "error": "cut off"}]
    remote = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello there, friend."}]
    merged, added = history_sync.merge_messages(local, remote)
    assert [m["text"] for m in merged] == ["hi", "Hello there, friend."] and added == 0
    assert "error" not in merged[1]


def test_refresh_repairs_a_reply_glued_after_another():
    local = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello th"},
             {"role": "assistant", "text": "Hello there, friend."}]
    remote = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Hello there, friend."}]
    merged, _added = history_sync.merge_messages(local, remote)
    assert [m["text"] for m in merged] == ["hi", "Hello there, friend."]


def test_sandbox_preview_links_are_found(web):
    appmod = web[0]
    text = "Live at https://3000-im6z8nnfdqmy82bsq2fp2.e2b.app/ and https://demo-x.arena.site/page."
    assert appmod._site_links(text) == ["https://3000-im6z8nnfdqmy82bsq2fp2.e2b.app/",
                                        "https://demo-x.arena.site/page"]


def test_status_page_incident_is_reported(web, monkeypatch):
    appmod = web[0]

    class Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"status": {"indicator": "major", "description": "Partial System Outage"},
                    "incidents": [{"name": "Website degraded", "status": "investigating"}]}

    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: Resp())
    monkeypatch.setitem(appmod._STATUS_CACHE, "at", 0)
    assert appmod._arena_status_note() == "status.arena.ai reports: Partial System Outage (Website degraded)."


def test_outage_shows_in_diagnostics_and_login_status(web, monkeypatch):
    import urllib.request
    appmod, c = web[0], web[1]

    class Down:
        status_code = 200

        @staticmethod
        def json():
            return {"status": {"indicator": "critical", "description": "Partial System Outage"},
                    "incidents": [{"name": "Arena is currently experiencing a service disruption. ",
                                   "status": "investigating"}]}

    monkeypatch.setattr(appmod.requests, "get", lambda *a, **k: Down())
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)  # front page answers
    monkeypatch.setitem(appmod._STATUS_CACHE, "at", 0)
    monkeypatch.setitem(appmod._STATUS_CACHE, "info", None)
    monkeypatch.setattr(appmod, "_signed_in_somewhere", lambda client: True)
    monkeypatch.setattr(appmod, "_check_session", lambda client, force=False: (None, "HTTP 503"))
    rows = {r["name"]: r for r in c.get("/api/diagnostics/run").get_json()["results"]}
    assert rows["Arena.ai connection"]["ok"] is True
    assert rows["Arena.ai service status"]["ok"] is False
    assert rows["Arena.ai service status"]["detail"] == (
        "Partial System Outage: Arena is currently experiencing a service disruption")
    assert rows["Account health"]["ok"] is None  # arena.ai did not answer: not a sign-out
    assert rows["Encryption (DPAPI)"]["ok"] is True
    # the login poll carries the outage and does not call the session expired
    data = c.get("/api/status").get_json()
    assert data["arena_status"]["indicator"] == "critical"
    assert data["arena_unavailable"] == "HTTP 503" and data["session_error"] is None


def test_login_poll_never_waits_on_the_status_page(web, monkeypatch):
    import time
    appmod, c = web[0], web[1]
    calls = []
    monkeypatch.setattr(appmod, "_refresh_arena_status", lambda: calls.append(1) or {})
    monkeypatch.setitem(appmod._STATUS_CACHE, "at", 0)
    monkeypatch.setitem(appmod._STATUS_CACHE, "info", None)
    monkeypatch.setitem(appmod._STATUS_CACHE, "busy", False)
    assert c.get("/api/status").get_json()["arena_status"] == {}  # answered from the cache
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert calls == [1]  # the page is read in the background


def test_run_on_this_pc_installs_then_starts(web, monkeypatch, tmp_path):
    appmod, c, _cfg, _use, _sent = web
    proj = tmp_path / "site"
    proj.mkdir()
    (proj / "package.json").write_text('{"scripts": {"dev": "next dev"}}', encoding="utf-8")
    calls = []

    class FakeBackground:
        _tail = staticmethod(appmod.agent._Background._tail)

        def start(self, cmd, cwd):
            calls.append((cmd, Path(cwd)))
            log = Path(cwd) / appmod.agent.ARTIFACTS_DIR / "bg7.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("ready - Local: http://localhost:3000\n", encoding="utf-8")
            return "Started in the background: id bg7, pid 1. It keeps running after this reply."

        def ids(self):
            return ["bg7"]

        def stop(self, bid):
            calls.append(("stop", bid))
            return "Stopped"

    monkeypatch.setattr(appmod.agent, "BACKGROUND", FakeBackground())
    data = c.post("/api/workspace/run-local", json={"path": str(tmp_path)}).get_json()
    # the project one level down is found; packages are always installed first
    assert data["ok"] and calls[0] == ("npm install && npm run dev", proj.resolve())
    assert data["running"] and data["urls"] == ["http://localhost:3000"]
    assert c.delete("/api/workspace/run-local/bg7").get_json()["ok"]
    assert ("stop", "bg7") in calls


def test_dev_server_address_reaches_the_chat(web):
    appmod = web[0]
    conv = {}
    output = ("Started in the background: id bg3, pid 9. It keeps running after this reply.\n"
              "Serving at: http://0.0.0.0:5173/, http://localhost:5173/")
    events_out = list(appmod._dev_server_events(conv, "a", output))
    urls = [json.loads(e[len("data: "):])["url"] for e in events_out]
    assert urls == ["http://localhost:5173/"] and conv["_bg_ids"] == ["bg3"]


def test_sandbox_steps_keep_their_output(web):
    appmod = web[0]
    conv = {"sandbox": [{"id": "t1", "model": "a", "tool": "bash", "kind": "run", "detail": "npm test"}]}
    res = {"toolCallId": "t1", "result": {"stdout": "4 passed\n", "stderr": "", "exitCode": 0}}
    sent = list(appmod._sandbox_result("a", res, conv))
    assert conv["sandbox"][0]["output"] == "exit code 0\n4 passed\n"  # saved with the chat
    assert json.loads(sent[0][len("data: "):])["output"] == "exit code 0\n4 passed\n"  # shown live


def test_opening_a_chat_keeps_its_place_in_the_list(web):
    appmod = web[0]
    cid = str(uuid.uuid4())
    turns = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "Setting up.I will read"}]
    appmod._save_chat(cid, title="t", messages=turns, mode="direct", modality="text")
    path = appmod.CHATS_DIR / cid / "chat.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["updated_at"] = "2026-10-31T10:00:00.000000+00:00"
    path.write_text(json.dumps(data), encoding="utf-8")
    # the refresh on open only repaired formatting: not new activity
    fixed = [turns[0], {"role": "assistant", "text": "Setting up.\n\nI will read"}]
    appmod._save_chat(cid, title="t", messages=fixed, mode="direct", modality="text", touch=False)
    assert appmod._load_chat(cid)["updated_at"] == "2026-10-31T10:00:00.000000+00:00"
    # with nothing local beyond arena's copy, the chat takes arena.ai's own time
    session = {"messages": [{"role": "user", "createdAt": "2026-10-30T08:00:00Z"},
                            {"role": "assistant", "createdAt": "2026-10-30T08:00:05.5Z"}]}
    stamp = appmod._synced_stamp(session, fixed, fixed)
    assert stamp == "2026-10-30T08:00:05.500000+00:00"
    assert appmod._synced_stamp(session, fixed + [turns[0]], fixed) is None
    appmod._save_chat(cid, title="t", messages=fixed, mode="direct", modality="text", touch=False, stamp=stamp)
    assert appmod._load_chat(cid)["updated_at"] == stamp
    # a real new turn still moves it up
    appmod._save_chat(cid, title="t", messages=fixed + [{"role": "user", "text": "more"}], mode="direct", modality="text")
    assert appmod._load_chat(cid)["updated_at"] > stamp
