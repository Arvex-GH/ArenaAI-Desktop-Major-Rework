"""Launch-time MCP connect: hidden processes, one run, visible progress.

Reported from the installed app: opening it opened a console window per local
MCP server (three of them) and connected none of them — each had to be started
by hand from a panel that is hidden, or by prompting the AI so a turn called
``ensure_started``. The servers are background processes now, they connect as
the app opens, and ``/api/connectors/startup`` reports the progress the start-up
screen draws.
"""

import subprocess

import pytest

from arena import connectors
from arena.tools import mcp


# ---------- the console window ----------

def test_windowless_spawn_kwargs_hide_the_console():
    kwargs = mcp.windowless_spawn_kwargs(True)
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    info = kwargs.get("startupinfo")
    if hasattr(subprocess, "STARTUPINFO"):
        assert info is not None
        assert info.dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert info.wShowWindow == subprocess.SW_HIDE


def test_windowless_spawn_kwargs_are_empty_off_windows():
    assert mcp.windowless_spawn_kwargs(False) == {}


def test_mcp_client_spawns_with_the_windowless_kwargs(monkeypatch):
    seen = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop before a real process starts")

    monkeypatch.setattr(mcp.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(mcp.shutil, "which", lambda name: "/bin/echo")
    monkeypatch.setattr(mcp, "windowless_spawn_kwargs", lambda nt=None: {"creationflags": 1234})

    client = mcp.McpStdioClient(command=["echo", "hi"])
    assert client.start(timeout=0.2) is False
    assert seen.get("creationflags") == 1234
    assert seen.get("stdin") == subprocess.PIPE


# ---------- the launch-time run ----------

def manager(tmp_path, servers):
    path = tmp_path / "mcp.json"
    connectors.save_config({"mcpServers": servers}, path)
    return connectors.ConnectorManager(lambda: tmp_path, config_path=path)


def run_to_end(mgr, timeout=5.0):
    mgr.start_startup_connect()
    thread = mgr._startup_thread
    if thread is not None:
        thread.join(timeout)
    return mgr.startup_snapshot()


def test_snapshot_is_idle_before_anything_runs(tmp_path):
    snap = manager(tmp_path, {}).startup_snapshot()
    assert snap["phase"] == "idle"
    assert snap["items"] == [] and snap["total"] == 0


def test_every_enabled_server_connects_by_itself(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "alpha": {"command": "echo", "args": ["a"], "description": "Alpha tools"},
        "beta": {"command": "echo", "args": ["b"]},
        "off": {"command": "echo", "disabled": True},
    })
    called = []

    def fake_connect(name, timeout=None):
        called.append(name)
        if name == "beta":
            return {"name": name, "running": False, "tools_count": 0, "error": "server exited with code 1"}
        return {"name": name, "running": True, "tools_count": 3, "error": None}

    monkeypatch.setattr(mgr, "connect", fake_connect)
    snap = run_to_end(mgr)

    # Both are started, in whatever order their threads got scheduled: the
    # launch run spawns every server at once now.
    assert sorted(called) == ["alpha", "beta"], "disabled servers are never started"
    assert snap["phase"] == "done"
    assert snap["total"] == 2 and snap["done"] == 2
    assert snap["connected"] == 1 and snap["failed"] == 1
    assert snap["disabled"] == 1
    statuses = {item["id"]: item["status"] for item in snap["items"]}
    # The switched-off one is listed as Off, not left out silently.
    assert statuses == {"alpha": "connected", "beta": "error", "off": "disabled"}
    alpha = snap["items"][0]
    assert alpha["label"] == "alpha" and alpha["tools"] == 3
    assert alpha["description"] == "Alpha tools"
    assert alpha["detail"] == "echo a"
    beta = snap["items"][1]
    assert "exited with code 1" in beta["error"]


def test_a_broken_entry_does_not_stop_the_others(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "broken": {"command": "nope"},
        "fine": {"command": "echo"},
    })

    def fake_connect(name, timeout=None):
        if name == "broken":
            raise ValueError("Connector is disabled in mcp.json")
        return {"name": name, "running": True, "tools_count": 1}

    monkeypatch.setattr(mgr, "connect", fake_connect)
    snap = run_to_end(mgr)
    statuses = {item["id"]: item["status"] for item in snap["items"]}
    assert statuses == {"broken": "error", "fine": "connected"}
    assert snap["phase"] == "done"


def test_the_run_happens_once_and_retry_repeats_it(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {"alpha": {"command": "echo"}})
    called = []
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: (called.append(name), {
        "name": name, "running": True, "tools_count": 2})[1])

    run_to_end(mgr)
    assert called == ["alpha"]
    mgr.start_startup_connect()
    mgr._startup_thread.join(5)
    assert called == ["alpha"], "a second launch does not reconnect what is running"
    mgr.start_startup_connect(force=True)
    mgr._startup_thread.join(5)
    assert called == ["alpha", "alpha"]


def test_a_remote_transport_is_reported_instead_of_started(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "remote": {"url": "https://example.test/mcp"},
        "local": {"command": "echo"},
    })
    called = []
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: (called.append(name), {
        "name": name, "running": True, "tools_count": 0})[1])
    snap = run_to_end(mgr)
    assert called == ["local"]
    statuses = {item["id"]: item["status"] for item in snap["items"]}
    assert statuses == {"remote": "unsupported", "local": "connected"}
    assert snap["failed"] == 1


def test_an_empty_config_finishes_immediately(tmp_path):
    snap = manager(tmp_path, {}).start_startup_connect()
    assert snap["phase"] == "done" and snap["total"] == 0
    assert snap["connected"] == 0 and snap["items"] == []


# ---------- the route the start-up screen polls ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from arena.web import app as appmod
    mgr = manager(tmp_path, {"alpha": {"command": "echo", "description": "Alpha tools"}})
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: {
        "name": name, "running": True, "tools_count": 4})
    monkeypatch.setattr(appmod, "CONNECTORS", mgr)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_startup_route_reports_progress_and_starts_the_run(client):
    payload = client.get("/api/connectors/startup").get_json()
    assert payload["ok"] is True
    assert payload["phase"] in ("connecting", "done")
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == "alpha"


def test_startup_retry_route_restarts_the_run(client):
    assert client.get("/api/connectors/startup").status_code == 200
    second = client.post("/api/connectors/startup/retry")
    assert second.status_code == 200
    assert second.get_json()["phase"] in ("connecting", "done")


# ---------- end to end, against a real STDIO server ----------

FAKE_SERVER = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "serverInfo": {"name": "fake-mcp", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "ping", "description": "Say pong", "inputSchema": {"type": "object"}}]}})
    else:
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
'''


def test_a_real_server_is_up_and_its_tools_are_registered(tmp_path, monkeypatch):
    import sys as _sys
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    mgr = manager(tmp_path, {
        "fake": {"command": _sys.executable, "args": [str(script)], "description": "Fake tools"},
    })
    monkeypatch.setattr(connectors, "STARTUP_TIMEOUT", 30.0)
    snap = run_to_end(mgr, timeout=30)
    try:
        assert snap["phase"] == "done"
        item = snap["items"][0]
        assert item["status"] == "connected", item.get("error")
        assert item["tools"] == 1
        assert "mcp__fake__ping" in mgr.tool_map()
    finally:
        mgr.shutdown()


# ---------- the app's own presets are ordinary connectors ----------

def test_installed_presets_start_with_the_app_and_keep_their_names(tmp_path, monkeypatch):
    """Filesystem, Memory and Sequential Thinking install into mcp.json as plain
    entries, so the launch run must pick them up like any other connector."""
    mgr = manager(tmp_path, {})
    mgr.install_preset("filesystem", {"path": str(tmp_path)})
    mgr.install_preset("memory", {})
    mgr.install_preset("sequential_thinking", {})
    called = []
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: (called.append(name), {
        "name": name, "running": True, "tools_count": 2})[1])

    snap = run_to_end(mgr)

    assert sorted(called) == ["filesystem", "memory", "sequential_thinking"]
    assert snap["total"] == 3 and snap["connected"] == 3
    labels = {item["id"]: item["label"] for item in snap["items"]}
    assert labels == {"filesystem": "Filesystem", "memory": "Memory",
                      "sequential_thinking": "Sequential Thinking"}
    for item in snap["items"]:
        assert item["preset_id"] == item["id"]
        assert item["enabled"] is True


def test_a_switched_off_connector_is_off_not_missing(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "filesystem": {"command": "npx", "x-arena-preset": "filesystem"},
        "memory": {"command": "npx", "x-arena-preset": "memory", "disabled": True},
    })
    called = []
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: (called.append(name), {
        "name": name, "running": True, "tools_count": 1})[1])
    snap = run_to_end(mgr)
    assert called == ["filesystem"]
    by_id = {item["id"]: item for item in snap["items"]}
    assert by_id["memory"]["status"] == "disabled"
    assert by_id["memory"]["label"] == "Memory"
    assert snap["total"] == 1 and snap["disabled"] == 1
    assert snap["phase"] == "done"


def test_all_connectors_off_finishes_instantly(tmp_path):
    mgr = manager(tmp_path, {"memory": {"command": "npx", "disabled": True}})
    snap = mgr.start_startup_connect()
    assert snap["phase"] == "done"
    assert snap["total"] == 0 and snap["disabled"] == 1
    assert snap["items"][0]["status"] == "disabled"
