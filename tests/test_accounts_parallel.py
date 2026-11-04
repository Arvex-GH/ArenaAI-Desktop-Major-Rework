"""Multi-account store/switching and parallel per-chat generations (v4.10).

Accounts: verified sign-ins are remembered by email, cookies never reach the
frontend, switching re-activates the stored session and marks dead ones
expired, and local chats belong to the account that created them.

Parallel chats: a chat is persisted with the user's prompt before the
answer starts streaming, the same chat rejects a second concurrent send,
Stop is per chat, and two chats can stream at the same time without
crossing their Arena session ids.
"""

import json
import threading

import pytest

from arena import contracts
from arena.web import app as web


@pytest.fixture()
def env(tmp_path, monkeypatch):
    chats = tmp_path / "chats"
    chats.mkdir()
    monkeypatch.setattr(web, "CHATS_DIR", chats)
    monkeypatch.setattr(web, "ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(web, "_signed_in_somewhere", lambda client: True)
    monkeypatch.setattr(web, "load_config", lambda: {})
    monkeypatch.setattr(web, "save_config", lambda cfg: None)
    web._active_chat_ids.clear()
    web._chat_stops.clear()
    return web.app.test_client(), chats


def test_register_account_upserts_by_email_and_hides_cookie(env):
    client, _chats = env
    first = web._register_account({"email": "alice@local.invalid", "user_metadata": {"full_name": "Alice A"}}, "ck-1")
    second = web._register_account({"email": "bob@local.invalid"}, "ck-2")
    again = web._register_account({"email": "ALICE@local.invalid"}, "ck-1-rotated")
    assert first == again and first != second
    payload = client.get("/api/accounts").get_json()
    assert [a["email"] for a in payload["accounts"]] == ["ALICE@local.invalid", "bob@local.invalid"]
    assert payload["current_id"] == first
    assert payload["accounts"][0]["name"] == "Alice A"
    assert payload["accounts"][0]["current"] is True
    assert all("cookie" not in a for a in payload["accounts"])
    store = web._accounts_load()
    assert store["accounts"][0]["cookie"] == "ck-1-rotated"


def test_switch_account_reactivates_stored_cookie(env, monkeypatch):
    client, _chats = env
    alice = web._register_account({"email": "alice@local.invalid"}, "alice-cookie")
    bob = web._register_account({"email": "bob@local.invalid"}, "bob-cookie")
    seen = []

    def fake_activate(cookie, method):
        seen.append((cookie, method))
        email = "alice@local.invalid" if cookie == "alice-cookie" else "bob@local.invalid"
        web._register_account({"email": email}, cookie)
        return web.jsonify({"ok": True, "method": method}), True

    monkeypatch.setattr(web, "_activate_cookie", fake_activate)
    monkeypatch.setattr(web, "get_client", lambda: type("C", (), {"cookie_str": "bob-cookie"})())
    resp = client.post("/api/accounts/switch", json={"id": alice})
    assert resp.status_code == 200, resp.get_json()
    assert seen == [("alice-cookie", "account-switch")]
    payload = resp.get_json()
    assert payload["accounts"][0]["current"] is True
    assert web._current_account_id() == alice
    assert bob != alice


def test_switch_with_dead_cookie_marks_account_expired(env, monkeypatch):
    client, _chats = env
    alice = web._register_account({"email": "alice@local.invalid"}, "dead-cookie")
    monkeypatch.setattr(
        web, "_activate_cookie",
        lambda cookie, method: (web.jsonify({"ok": False, "error": "rejected"}), False))
    resp = client.post("/api/accounts/switch", json={"id": alice})
    assert resp.status_code == 401
    payload = resp.get_json()
    assert payload["expired"] is True
    assert payload["accounts"][0]["expired"] is True


def test_switch_blocked_while_generation_runs(env):
    client, _chats = env
    alice = web._register_account({"email": "alice@local.invalid"}, "ck")
    web._generation_started("some-chat")
    try:
        resp = client.post("/api/accounts/switch", json={"id": alice})
        assert resp.status_code == 409
    finally:
        web._generation_finished("some-chat")


def test_chats_belong_to_their_account(env):
    client, chats = env
    alice = web._register_account({"email": "alice@local.invalid"}, "ck-a")
    web._save_chat("11111111-1111-4111-8111-111111111111", title="Alice chat",
                   messages=[{"role": "user", "text": "hi"}])
    bob = web._register_account({"email": "bob@local.invalid"}, "ck-b")
    web._save_chat("22222222-2222-4222-8222-222222222222", title="Bob chat",
                   messages=[{"role": "user", "text": "yo"}])
    # Legacy chat without an owner belongs to the first account (Alice).
    folder = chats / "33333333-3333-4333-8333-333333333333"
    folder.mkdir()
    (folder / "chat.json").write_text(json.dumps(
        {"id": folder.name, "title": "Legacy", "messages": []}), encoding="utf-8")

    bob_titles = {c["title"] for c in client.get("/api/chats").get_json()}
    assert bob_titles == {"Bob chat"}
    store = web._accounts_load()
    store["current"] = alice
    web._accounts_save(store)
    alice_titles = {c["title"] for c in client.get("/api/chats").get_json()}
    assert alice_titles == {"Alice chat", "Legacy"}
    assert bob != alice


def test_remove_current_account_rejected(env):
    client, _chats = env
    alice = web._register_account({"email": "alice@local.invalid"}, "ck")
    bob = web._register_account({"email": "bob@local.invalid"}, "ck2")  # Bob becomes current
    resp = client.post("/api/accounts/remove", json={"id": bob})
    assert resp.status_code == 400
    resp = client.post("/api/accounts/remove", json={"id": alice})
    assert resp.status_code == 200
    assert [a["email"] for a in resp.get_json()["accounts"]] == ["bob@local.invalid"]


class SlowResp:
    """A streaming response that waits for the test before each line."""

    status_code = 200
    headers = {}

    def __init__(self, lines, gate):
        self._lines = lines
        self._gate = gate
        self.text = ""
        self.error = None

    def iter_text(self):
        for line in self._lines:
            self._gate.wait(timeout=10)
            yield line + "\n"


def _events(resp):
    out = []
    for chunk in resp.response:
        for line in chunk.decode().splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def test_chat_is_saved_with_prompt_before_streaming_starts(env, monkeypatch):
    _client, chats = env
    gate = threading.Event()
    sent = []
    monkeypatch.setattr(web, "get_client", lambda: type("C", (), {"cookie_str": "x", "eval_id": None})())
    monkeypatch.setattr(web, "_send", lambda client, path, body: sent.append((path, body)) or SlowResp(['a0:"ok"'], gate))
    monkeypatch.setattr(web, "_allowed_model_ids", lambda *a: set())

    result = {}

    def run():
        with web.app.test_client() as c:
            resp = c.post("/api/chat", json={
                "message": "early please", "mode": "battle", "modality": "text", "tools": False})
            result["events"] = _events(resp)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    # While the (fake) Arena has not produced anything, the chat must
    # already exist on disk with the user's prompt.
    saved = None
    for _ in range(100):
        folders = [d for d in chats.iterdir() if (d / "chat.json").exists()]
        if folders:
            try:
                saved = json.loads((folders[0] / "chat.json").read_text(encoding="utf-8"))
            except Exception:
                saved = None  # caught mid-write; poll again
            if saved and saved.get("messages"):
                break
        threading.Event().wait(0.05)
    assert saved and saved["messages"][0]["text"] == "early please"
    chat_id = saved["id"]
    assert web._chat_generating(chat_id)
    # The same chat rejects a concurrent second send…
    with web.app.test_client() as c:
        resp = c.post("/api/chat", json={
            "message": "again", "chat_id": chat_id, "mode": "battle", "modality": "text"})
        assert resp.status_code == 409
    gate.set()
    thread.join(timeout=15)
    assert result["events"][-1]["type"] == "done"
    assert not web._chat_generating(chat_id)
    final = web._load_chat(chat_id)
    assert final["eval_id"] == sent[0][1]["id"]


def test_two_chats_stream_in_parallel_without_crossing_sessions(env, monkeypatch):
    _client, _chats = env
    gates = {"a": threading.Event(), "b": threading.Event()}
    sent = []
    sent_lock = threading.Lock()

    def send(client, path, body):
        with sent_lock:
            sent.append((path, dict(body)))
        key = "a" if body.get("message") and "first" in json.dumps(body) else "b"
        return SlowResp(['a0:"reply"'], gates[key])

    monkeypatch.setattr(web, "get_client", lambda: type("C", (), {"cookie_str": "x", "eval_id": None})())
    monkeypatch.setattr(web, "_send", send)
    monkeypatch.setattr(web, "_allowed_model_ids", lambda *a: set())

    results = {}

    def run(name, message):
        with web.app.test_client() as c:
            resp = c.post("/api/chat", json={
                "message": message, "mode": "battle", "modality": "text", "tools": False})
            results[name] = _events(resp)

    t1 = threading.Thread(target=run, args=("one", "first chat prompt"), daemon=True)
    t2 = threading.Thread(target=run, args=("two", "second chat prompt"), daemon=True)
    t1.start()
    t2.start()
    for _ in range(200):
        if len(web._generating_chat_ids()) == 2:
            break
        threading.Event().wait(0.05)
    assert len(web._generating_chat_ids()) == 2
    # Stop only the first chat; the second must finish normally.
    ids = web._generating_chat_ids()
    with web.app.test_client() as c:
        assert c.post("/api/stop", json={"chat_id": ids[0]}).status_code == 200
    assert web._chat_stop_event(ids[0]).is_set()
    assert not web._chat_stop_event(ids[1]).is_set()
    gates["a"].set()
    gates["b"].set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert results["one"][-1]["type"] == "done"
    assert results["two"][-1]["type"] == "done"
    eval_ids = {body["id"] for _path, body in sent if body.get("id")}
    assert len(eval_ids) == 2
    chat_ids = {results["one"][0]["chat_id"], results["two"][0]["chat_id"]}
    saved_evals = {web._load_chat(cid)["eval_id"] for cid in chat_ids}
    assert saved_evals == eval_ids


def test_status_lists_generating_chats(env):
    client, _chats = env
    web._generation_started("chat-x")
    try:
        payload = client.get("/api/status").get_json()
        assert payload["generating"] is True
        assert payload["generating_chat_ids"] == ["chat-x"]
        assert "accounts" in payload
    finally:
        web._generation_finished("chat-x")
