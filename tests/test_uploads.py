"""Attachment uploads: Arena's two storage flows and their limits.

Evaluation (Battle/Direct/Side by Side): mint an upload URL, PUT the bytes,
then get a signed read URL -> experimental attachment {name, contentType, url}.
Agent Mode: content-addressed by SHA-256 -> /api/chat/workspace/cas/user/<hash>.
"""

import base64
import hashlib
import io

import pytest

from arena.web import app as web


@pytest.fixture()
def uploads(monkeypatch):
    calls = {"arena": [], "put": []}

    def fake_post_json(path, body, timeout=60):
        calls["arena"].append((path, body))
        if path.endswith("generate-agent-upload-url"):
            return {"uploadUrl": "https://storage.example/put-agent", "key": "k-a"}, None, 200
        if path.endswith("generate-upload-url"):
            return {"uploadUrl": "https://storage.example/put", "key": "k-1"}, None, 200
        if path.endswith("get-signed-url"):
            assert body == {"key": "k-1"}
            return {"url": "https://storage.example/signed/a.png"}, None, 200
        raise AssertionError(f"unexpected arena call {path}")

    def fake_put(url, data, media_type):
        calls["put"].append((url, media_type, len(data)))
        return None

    monkeypatch.setattr(web, "_arena_post_json", fake_post_json)
    monkeypatch.setattr(web, "_storage_put", fake_put)
    monkeypatch.setattr(web, "_signed_in_somewhere", lambda client: True)
    monkeypatch.setattr(web, "get_client", lambda: object())
    return web.app.test_client(), calls


def _post(client, name, data, kind="evaluation", modality=None, content_type=None):
    form = {"file": (io.BytesIO(data), name, content_type or "application/octet-stream"),
            "kind": kind}
    if modality:
        form["modality"] = modality
    return client.post("/api/uploads", data=form)


def test_evaluation_upload_flow(uploads):
    client, calls = uploads
    resp = _post(client, "cat.png", b"\x89PNG fake")
    assert resp.status_code == 200, resp.get_json()
    out = resp.get_json()
    assert out == {"ok": True, "kind": "evaluation", "name": "cat.png",
                   "contentType": "image/png",
                   "url": "https://storage.example/signed/a.png", "size": 9}
    assert calls["put"] == [("https://storage.example/put", "image/png", 9)]
    paths = [p for p, _ in calls["arena"]]
    assert paths[0].endswith("generate-upload-url") and paths[1].endswith("get-signed-url")


def test_agent_upload_is_content_addressed(uploads):
    client, calls = uploads
    data = b"hello agent"
    resp = _post(client, "notes.md", data, kind="agent")
    assert resp.status_code == 200, resp.get_json()
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
    out = resp.get_json()
    assert out["url"] == f"/api/chat/workspace/cas/user/{digest}"
    assert out["mediaType"] == "text/markdown" and out["filename"] == "notes.md"
    _, body = calls["arena"][0]
    assert body == {"hash": digest, "contentType": "text/markdown", "size": len(data)}
    assert len(digest) == 43


def test_evaluation_rejects_agent_only_types(uploads):
    client, _ = uploads
    resp = _post(client, "notes.md", b"# hi")
    assert resp.status_code == 400
    assert "PNG, JPEG, WebP and PDF" in resp.get_json()["error"]


def test_agent_sends_python_files_as_text(uploads):
    # v1.5.0 upload middleman: code files Arena refuses go up as text/plain.
    client, calls = uploads
    resp = _post(client, "script.py", b"print(1)", kind="agent")
    assert resp.status_code == 200
    out = resp.get_json()
    assert out["mediaType"] == "text/plain" and out["filename"] == "script.py.txt"


def test_agent_rejects_binary_files(uploads):
    client, _ = uploads
    resp = _post(client, "tool.exe", b"MZ\x00\x00binary", kind="agent")
    assert resp.status_code == 400
    assert "Agent Mode accepts" in resp.get_json()["error"]


def test_size_limits_per_area(uploads):
    client, _ = uploads
    big = b"x" * (16 * 1024 * 1024)
    assert _post(client, "photo.png", big).status_code == 400
    assert _post(client, "photo.png", b"x" * (11 * 1024 * 1024), modality="video").status_code == 400
    assert _post(client, "photo.png", b"x" * (11 * 1024 * 1024)).status_code == 200
    pdf = b"%PDF" + b"x" * (11 * 1024 * 1024)
    agent_pdf = _post(client, "doc.pdf", pdf, kind="agent")
    assert agent_pdf.status_code == 400
    assert "10 MB" in agent_pdf.get_json()["error"]


def test_upload_requires_sign_in(monkeypatch):
    monkeypatch.setattr(web, "_signed_in_somewhere", lambda client: False)
    monkeypatch.setattr(web, "get_client", lambda: object())
    resp = web.app.test_client().post("/api/uploads", data={})
    assert resp.status_code == 401


def test_request_attachments_sanitizes_and_caps():
    data = {"attachments": [
        {"name": "a.png", "contentType": "image/png", "url": "https://x/a.png", "size": 5, "junk": 1},
        "not-a-dict",
        {"filename": "b.pdf", "mediaType": "application/pdf", "url": "/api/chat/workspace/cas/user/x"},
    ]}
    out, err = web._request_attachments(data, native_agent_files=False)
    assert err is None
    assert out == [{"name": "a.png", "contentType": "image/png", "url": "https://x/a.png", "size": 5},
                   {"name": "b.pdf", "contentType": "application/pdf",
                    "url": "/api/chat/workspace/cas/user/x", "size": 0}]
    out, err = web._request_attachments(data, native_agent_files=True)
    assert err is None
    assert out[1] == {"filename": "b.pdf", "mediaType": "application/pdf",
                      "url": "/api/chat/workspace/cas/user/x", "size": 0}
    missing = {"attachments": [{"name": "a.png", "contentType": "image/png"}]}
    out, err = web._request_attachments(missing, native_agent_files=False)
    assert out is None and "upload link" in err


def test_media_type_prefers_extension_for_text_kinds():
    assert web._attachment_media_type("data.csv", "application/octet-stream") == "text/csv"
    assert web._attachment_media_type("page.html", "text/plain") == "text/html"
    assert web._attachment_media_type("cfg.json", "text/plain") == "application/json"
    assert web._attachment_media_type("photo.jpg", "") == "image/jpeg"
