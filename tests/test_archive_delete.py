"""Deleting a chat here archives the same conversation on arena.ai.

Evaluation sessions go through the arenaEvaluationSession Server Action
(engine probe); Arena Agent sessions through POST /api/chat/{id}/archive.
Official connector on/off switches exclude slugs from new Agent sessions.
"""

import json

import pytest

from arena.web import app as web

CHAT_ID = "12345678-1234-1234-1234-123456789abc"
EVAL_ID = "87654321-4321-4321-4321-cba987654321"
AGENT_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    chats = tmp_path / "chats"
    chats.mkdir()
    calls = {"archive_eval": [], "archive_agentic": []}
    monkeypatch.setattr(web, "CHATS_DIR", chats)
    monkeypatch.setattr(web, "_signed_in_somewhere", lambda client: True)
    monkeypatch.setattr(web, "get_client", lambda: object())
    monkeypatch.setattr(web, "_engine_on", lambda: True)
    monkeypatch.setattr(
        web.ENGINE, "archive_evaluation",
        lambda sid, timeout=40: calls["archive_eval"].append(sid) or
        {"ok": True, "error": None, "trace": []})
    monkeypatch.setattr(
        web, "_archive_agentic_on_arena",
        lambda sid: calls["archive_agentic"].append(sid) or None)
    return web.app.test_client(), chats, calls


def _make_chat(chats, **meta):
    folder = chats / CHAT_ID
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"id": CHAT_ID, "title": "Doomed", "messages": []}
    payload.update(meta)
    (folder / "chat.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


def test_delete_evaluation_chat_archives_on_arena(env):
    client, chats, calls = env
    folder = _make_chat(chats, mode="battle", eval_id=EVAL_ID)
    resp = client.post(f"/api/chats/{CHAT_ID}/delete", json={})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["arena"] == "archived"
    assert calls["archive_eval"] == [EVAL_ID]
    assert not folder.exists()


def test_delete_arena_agent_chat_archives_agentic_session(env):
    client, chats, calls = env
    _make_chat(chats, mode="agent", agent_backend="arena", eval_id=AGENT_ID,
               native={"session_id": AGENT_ID})
    resp = client.post(f"/api/chats/{CHAT_ID}/delete", json={})
    assert resp.status_code == 200
    assert resp.get_json()["arena"] == "archived"
    assert calls["archive_agentic"] == [AGENT_ID]
    assert calls["archive_eval"] == []


def test_delete_local_only_chat_skips_arena(env):
    client, chats, calls = env
    _make_chat(chats, mode="direct")
    resp = client.post(f"/api/chats/{CHAT_ID}/delete", json={})
    assert resp.status_code == 200
    assert resp.get_json()["arena"] == "none"
    assert calls["archive_eval"] == [] and calls["archive_agentic"] == []


def test_delete_missing_chat_is_404(env):
    client, _, _ = env
    resp = client.post(f"/api/chats/{CHAT_ID}/delete", json={})
    assert resp.status_code == 404


def test_archive_failure_still_deletes_locally(env, monkeypatch):
    client, chats, _ = env
    folder = _make_chat(chats, mode="battle", eval_id=EVAL_ID)
    monkeypatch.setattr(web.ENGINE, "archive_evaluation",
                        lambda sid, timeout=40: {"ok": False, "error": "no_window", "trace": []})
    resp = client.post(f"/api/chats/{CHAT_ID}/delete", json={})
    assert resp.status_code == 200
    assert resp.get_json()["arena"] == "failed"
    assert "not ready" in resp.get_json()["arena_error"]
    assert not folder.exists()


def test_cloud_chat_archive_route(env):
    client, _, calls = env
    resp = client.post(f"/api/arena-chats/{EVAL_ID}/archive", json={"type": "evaluation"})
    assert resp.status_code == 200 and resp.get_json()["arena"] == "archived"
    resp = client.post(f"/api/arena-chats/{AGENT_ID}/archive", json={"type": "agentic"})
    assert resp.status_code == 200
    assert calls["archive_eval"] == [EVAL_ID]
    assert calls["archive_agentic"] == [AGENT_ID]
    assert client.post("/api/arena-chats/not-a-uuid/archive", json={}).status_code == 400


def test_connector_off_switches_excluded_from_new_agent_sessions(monkeypatch):
    monkeypatch.setattr(web, "_fetch_arena_connectors", lambda: ([
        {"slug": "github", "connected": True},
        {"slug": "notion", "connected": True},
        {"slug": "linear", "connected": False},
    ], None, 200))
    monkeypatch.setattr(web, "load_config", lambda: {"arena_connectors_off": ["notion"]})
    assert web._native_connected_slugs() == ["github"]
    monkeypatch.setattr(web, "load_config", lambda: {})
    assert web._native_connected_slugs() == ["github", "notion"]
