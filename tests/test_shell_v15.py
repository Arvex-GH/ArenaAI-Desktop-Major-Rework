"""v1.5.0: local commands - shell syntax, live output, background processes."""

import sys
import time

import pytest

from arena import agent, permissions


# ---------- permissions ----------

@pytest.mark.parametrize("cmd,shell,explicit", [
    ("npm install", False, False),
    ("npm run build -- --mode production", False, False),
    ("npm install && npm run build", True, True),
    ("git log | head", True, True),
    ("echo hi > out.txt", True, True),
    ("echo %PATH%", True, True),
    ("echo $HOME", True, True),
    ("node -e 1", False, True),
    ("python -c pass", False, True),
])
def test_shell_classification(cmd, shell, explicit):
    assert permissions.needs_shell(cmd) is shell
    assert permissions.needs_explicit_approval(cmd) is explicit


def test_always_never_covers_compound_commands():
    b = permissions.PermissionBroker()
    pid = b.ask({"tool": "run_cmd"})
    b.resolve(pid, "always")
    b.wait(pid, 0)
    assert b.check("default", "run_cmd", True, "npm run build") == "allow"
    assert b.check("default", "run_cmd", True, "npm i && npm run build") == "ask"
    assert b.check("default", "run_cmd", True, "node -e 1") == "ask"
    assert b.check("bypassPermissions", "run_cmd", True, "npm i && curl x") == "ask"
    assert b.check("default", "run_cmd", True, "rm -rf / && echo") == "deny"


def test_stop_cmd_is_harmless():
    assert permissions.decide("default", "stop_cmd", True, "bg1") == "allow"


# ---------- running ----------

def test_compound_command_runs_through_shell(tmp_path):
    out = agent.run_shell_command("echo one && echo two", tmp_path)
    assert out.startswith("exit code 0")
    assert "one" in out and "two" in out


def test_redirect_writes_file(tmp_path):
    agent.run_shell_command("echo hello > made.txt", tmp_path)
    assert (tmp_path / "made.txt").read_text().strip() == "hello"


def test_catastrophic_command_refused_even_in_shell(tmp_path):
    assert "never allowed" in agent.run_shell_command("echo x && format c:", tmp_path)


def test_live_output_and_stop(tmp_path):
    script = tmp_path / "ticker.py"
    script.write_text("import time\nfor i in range(100):\n    print('tick', i, flush=True)\n    time.sleep(0.1)\n")
    seen = []
    started = time.time()
    out = agent.run_shell_command(f'"{sys.executable}" ticker.py', tmp_path,
                                  on_output=seen.append,
                                  should_stop=lambda: time.time() - started > 1.2)
    assert out.startswith("stopped by the user")
    assert "tick 0" in "".join(seen)
    assert time.time() - started < 8


def test_nonzero_exit_marks_tool_failed(tmp_path):
    tools = agent.LocalTools(tmp_path)
    (tmp_path / "fail.py").write_text("raise SystemExit(3)\n")
    ok, out = tools.run({"name": "run_cmd", "params": {"cmd": f'"{sys.executable}" fail.py'}})
    assert ok is False and out.startswith("exit code 3")
    ok, out = tools.run({"name": "run_cmd", "params": {"cmd": "echo fine"}})
    assert ok is True


def test_background_start_and_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "BACKGROUND_WARMUP", 3)
    script = tmp_path / "server.py"
    script.write_text("import time\nprint('Local: http://localhost:5173/', flush=True)\n"
                      "while True:\n    time.sleep(0.2)\n")
    tools = agent.LocalTools(tmp_path)
    ok, out = tools.run({"name": "run_cmd",
                         "params": {"cmd": f'"{sys.executable}" server.py', "background": "true"}})
    assert ok, out
    assert "Started in the background" in out and "http://localhost:5173/" in out
    bid = out.split("id ", 1)[1].split(",", 1)[0]
    assert bid in agent.BACKGROUND.ids()
    ok, out = tools.run({"name": "stop_cmd", "params": {"id": bid}})
    assert ok and out.startswith(f"Stopped {bid}")
    assert bid not in agent.BACKGROUND.ids()
    assert "No background command" in agent.BACKGROUND.stop(bid)


def test_describe_marks_background(tmp_path):
    info = agent.LocalTools(tmp_path).describe(
        {"name": "run_cmd", "params": {"cmd": "npm run dev", "background": "true"}})
    assert info["background"] is True and info["summary"].startswith("Start in background")


# ---------- titles and component prompts ----------

@pytest.mark.parametrize("message,want", [
    ("make a website", "Make a website"),
    ("Can you please build a landing page for an engineering student!", "Build a landing page for an engineering student"),
    ("hey, i want you to fix the login bug. it crashes on submit", "Fix the login bug"),
    ("", "New chat"),
])
def test_smart_title(message, want):
    import arena.web.app as web
    assert web._smart_title(message) == want


def test_smart_title_caps_long_prompts():
    import arena.web.app as web
    title = web._smart_title("write a detailed step by step guide on how to deploy a flask app to azure")
    assert title.endswith("\u2026") and len(title.split()) == 8


def test_missing_component_detection(monkeypatch):
    monkeypatch.setattr(agent.shutil, "which", lambda name: None)
    out = "'npm' is not recognized as an internal or external command,\noperable program or batch file."
    assert agent.missing_component("npm install && npm run build", out) == "node"
    assert agent.missing_component("python app.py", "Error: could not start the command: x") == "python"
    assert agent.missing_component("git status", out) is None
    assert agent.missing_component("npm install", "exit code 1\nnpm ERR! 404") is None


# ---------- upload middleman ----------

def _bmp_bytes():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 6), (200, 30, 30)).save(buf, "BMP")
    return buf.getvalue()


@pytest.mark.parametrize("kind", ["agent", "evaluation"])
def test_bmp_becomes_png(kind):
    import arena.web.app as web
    name, data, mime, note = web._convert_attachment("shot.bmp", _bmp_bytes(), "image/bmp", kind)
    assert (name, mime) == ("shot.png", "image/png") and data[:8] == b"\x89PNG\r\n\x1a\n" and note


def test_code_file_becomes_text_in_agent_mode_only():
    import arena.web.app as web
    src = b"def main():\n    return 1\n"
    assert web._convert_attachment("tool.py", src, "application/octet-stream", "agent")[1:3] == (src, "text/plain")
    assert web._convert_attachment("tool.py", src, "application/octet-stream", "evaluation")[2] == "application/octet-stream"


def test_supported_and_heic_files():
    import arena.web.app as web
    assert web._convert_attachment("a.png", b"x", "image/png", "evaluation") == ("a.png", b"x", "image/png", None)
    with pytest.raises(ValueError):
        web._convert_attachment("IMG_1.HEIC", b"x", "image/heic", "agent")


# ---------- Arena Agent workspace sync, preview links, titles ----------

def _drain(gen):
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def test_native_mirror_writes_edits_and_protects_local_changes(tmp_path, monkeypatch):
    import arena.web.app as web
    monkeypatch.setattr(web, "_perm_mode", lambda: "auto")
    nat = {}
    _, saved = _drain(web._native_mirror("write_file", {"path": "/home/user/site/index.html", "content": "<h1>hi</h1>"}, nat, tmp_path))
    assert saved == ["site/index.html"] and (tmp_path / "site/index.html").read_text() == "<h1>hi</h1>"
    assert nat["file_sync_ok"] is True
    _drain(web._native_mirror("str_replace", {"path": "/home/user/site/index.html", "old_str": "hi", "new_str": "yo"}, nat, tmp_path))
    assert (tmp_path / "site/index.html").read_text() == "<h1>yo</h1>"
    cmd = "cd /home/user/site && cat > app.js << 'EOF'\nconsole.log(`${1}`)\nEOF"
    _, saved = _drain(web._native_mirror("bash", {"command": cmd}, nat, tmp_path))
    assert saved == ["site/app.js"] and "${1}" in (tmp_path / "site/app.js").read_text()
    (tmp_path / "site/index.html").write_text("mine")  # changed on this computer
    events, saved = _drain(web._native_mirror("write_file", {"path": "/home/user/site/index.html", "content": "x"}, nat, tmp_path))
    assert saved == [] and (tmp_path / "site/index.html").read_text() == "mine"
    assert any("left as is" in e for e in events)
    _, saved = _drain(web._native_mirror("write_file", {"path": "/etc/hosts", "content": "x"}, nat, tmp_path))
    assert saved == []


def test_native_mirror_respects_plan_mode(tmp_path, monkeypatch):
    import arena.web.app as web
    monkeypatch.setattr(web, "_perm_mode", lambda: "plan")
    nat = {}
    _, saved = _drain(web._native_mirror("write_file", {"path": "/home/user/a.txt", "content": "x"}, nat, tmp_path))
    assert saved == [] and nat["file_sync_ok"] is False and not (tmp_path / "a.txt").exists()


def test_preview_link_serves_page_and_relative_assets(tmp_path, monkeypatch):
    import arena.web.app as web
    (tmp_path / "index.html").write_text("<link href='style.css'>")
    (tmp_path / "style.css").write_text("body{}")
    monkeypatch.setattr(web, "_workspace_panel_path", lambda sub: (tmp_path / sub, tmp_path))
    client = web.app.test_client()
    raw = web.app.test_client_class.__mro__[1](web.app)  # plain client: no API token header
    assert raw.get(f"/preview/{web._PREVIEW_TOKEN}/style.css").status_code == 200
    assert raw.get(f"/preview/{web._PREVIEW_TOKEN}/index.html").status_code == 200
    assert raw.get("/preview/wrong/index.html").status_code == 403
    assert raw.get("/api/workspace/preview/index.html").status_code == 403
    assert client.get("/").get_data(as_text=True).count('name="preview-token"') == 1


def test_injected_titles_are_replaced():
    import arena.web.app as web
    from arena import history_sync
    assert history_sync.clean_title("[Arena Desktop] The user is chatting") == "Arena Desktop chat"
    assert history_sync.clean_title("[System] You are Arena CLI, an expert") == "Arena Desktop chat"
    assert history_sync.clean_title("Fix the login bug") == "Fix the login bug"
    chat = {"title": "[Arena Desktop] The user is", "messages": [{"role": "user", "text": "build me a todo app"}]}
    assert web._chat_title(chat) == "Build me a todo app"


def test_refresh_merge_ignores_injected_copies_and_keeps_order():
    from arena import history_sync
    import arena.web.app as web
    local = [{"role": "user", "text": "build me a landing page"}, {"role": "assistant", "text": "Done."}]
    remote = [{"role": "user", "text": "[Arena Desktop] Memory: likes dark themes.\n[End of memory]\n\nbuild me a landing page"},
              {"role": "assistant", "text": "Done."},
              {"role": "user", "text": "[Arena Desktop] Tool results: wrote index.html"},
              {"role": "assistant", "text": "<write_file>...</write_file>"}]
    merged, added = history_sync.merge_messages(local, remote)
    assert added == 0 and [m["text"] for m in merged] == ["build me a landing page", "Done."]
    assert web._chat_changed(local, [dict(m) for m in local]) is False
    assert web._chat_changed(local, local + [{"role": "user", "text": "more"}]) is True
