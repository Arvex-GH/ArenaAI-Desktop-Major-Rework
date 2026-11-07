"""A reply must never wait for a connector to start.

Reported from the installed app: connectors were already connected at launch,
yet the first prompt sat for a long time before the model answered — as if the
MCP servers were being started under the hood again.  They were: the turn's
``ensure_started(timeout=30)`` connected every enabled server in order, so a
prompt sent while the launch run was still spawning ``npx`` waited behind it,
and a connector that had failed was spawned again on every single turn.

The contract now:

* the launch run connects the connectors (in parallel, so it ends in about the
  time of its slowest server instead of their sum);
* a turn asks for ``tools_now()`` — what is up right now — after at most a
  courtesy second (``wait_for_startup``), and says which connector it had to go
  without;
* nothing on a reply's path spawns a process, and nothing blocks on the run.
"""

import pathlib
import re
import time

from arena import connectors


def manager(tmp_path, servers):
    path = tmp_path / "mcp.json"
    connectors.save_config({"mcpServers": servers}, path)
    return connectors.ConnectorManager(lambda: tmp_path, config_path=path)


def read_source(*parts):
    root = pathlib.Path(__file__).resolve().parents[1]
    return (root.joinpath(*parts)).read_bytes().decode("utf-8").replace("\r\n", "\n")


# ---------- what a turn does ----------

def test_tools_now_only_reports_connectors_that_are_already_up(tmp_path):
    mgr = manager(tmp_path, {"alpha": {"command": "arena-personal-no-such-mcp-command"}})
    started = time.monotonic()
    try:
        assert mgr.tools_now() == {}
        # A missing command fails fast in ensure_started; tools_now must not even
        # try. Half a second is generous for "did nothing".
        assert time.monotonic() - started < 0.5
        assert mgr._clients == {} and mgr._errors == {}
    finally:
        mgr.shutdown()


def test_tools_now_does_not_wait_for_a_launch_run_in_flight(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {"slow": {"command": "echo"}})
    release = []

    def slow_connect(name, timeout=None):
        time.sleep(2.5)  # a cold `npx -y` download, in miniature
        release.append(name)
        return {"name": name, "running": True, "tools_count": 3}

    monkeypatch.setattr(mgr, "connect", slow_connect)
    started = time.monotonic()
    mgr.start_startup_connect()
    try:
        # The launch run is mid-connect. A turn must not stand behind it.
        late = mgr.wait_for_startup(0.3)
        tools = mgr.tools_now()
        elapsed = time.monotonic() - started
    finally:
        mgr._startup_thread.join(10)
        mgr.shutdown()
    assert tools == {}
    assert late == ["slow"], "the reply must be able to name what it went without"
    assert elapsed < 1.5, f"the turn waited {elapsed:.1f}s for the launch run"


def test_wait_for_startup_returns_the_moment_the_run_finishes(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {"quick": {"command": "echo"}})
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: {
        "name": name, "running": True, "tools_count": 1})
    mgr.start_startup_connect()
    try:
        started = time.monotonic()
        late = mgr.wait_for_startup(5.0)
        elapsed = time.monotonic() - started
        assert late == []
        assert elapsed < 2.0, "a finished run must not spend the whole budget"
        assert mgr.startup_snapshot()["phase"] == "done"
        assert mgr.tools_now() == {}  # the fake connect registers no client

    finally:
        mgr.shutdown()


def test_starting_labels_only_names_work_in_progress(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "filesystem": {"command": "echo", "x-arena-preset": "filesystem"},
        "memory": {"command": "echo", "x-arena-preset": "memory", "disabled": True},
    })
    assert mgr.starting_labels() == [], "an idle app is not 'starting' anything"
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: {
        "name": name, "running": True, "tools_count": 1})
    mgr.start_startup_connect()
    mgr._startup_thread.join(10)
    try:
        assert mgr.starting_labels() == []
    finally:
        mgr.shutdown()


# ---------- how a turn gets there ----------

def test_the_turn_path_never_asks_a_connector_to_start():
    source = read_source("arena", "web", "server", "local_agent.py")
    turn = source[source.index("def _agent_run("):]
    turn = turn[:turn.index("\ndef ", 10)]
    assert ".ensure_started(" not in turn, \
        "a reply must never connect connectors itself"
    assert "tools_now()" in turn
    assert "wait_for_startup(" in turn
    # Bounded: a second and a half of patience, not a timeout per server.
    grace = re.search(r"^TURN_CONNECT_GRACE = ([0-9.]+)", source, re.M)
    assert grace and 0 < float(grace.group(1)) <= 3.0


def test_the_gateway_index_reads_the_running_connectors_only():
    source = read_source("arena", "web", "server", "turns.py")
    index = source[source.index("def _gateway_tool_index("):]
    index = index[:index.index("\ndef ", 10)]
    assert ".ensure_started(" not in index
    assert "tools_now()" in index


def test_a_tool_call_for_a_stopped_connector_fails_instead_of_spawning(tmp_path):
    mgr = manager(tmp_path, {"alpha": {"command": "arena-personal-no-such-mcp-command"}})
    try:
        try:
            mgr.call("mcp__alpha__echo", {"text": "hi"})
        except KeyError as e:
            assert "not running" in str(e)
        else:  # pragma: no cover - the call must not succeed or hang
            raise AssertionError("call() started a connector instead of reporting it")
        assert mgr._clients == {}
    finally:
        mgr.shutdown()


def test_switching_a_connector_on_starts_it_without_a_prompt(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {"alpha": {"command": "echo", "disabled": True}})
    calls = []
    monkeypatch.setattr(mgr, "connect", lambda name, timeout=None: (calls.append(name), {
        "name": name, "running": True, "tools_count": 1})[1])
    mgr.set_enabled("alpha", True)
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls == ["alpha"], "an enabled connector connects in the background"
    mgr.shutdown()


# ---------- the launch run itself ----------

def test_the_launch_run_spawns_servers_in_parallel(tmp_path, monkeypatch):
    names = ["filesystem", "memory", "sequential_thinking", "custom"]
    mgr = manager(tmp_path, {name: {"command": "echo"} for name in names})

    def slow_connect(name, timeout=None):
        time.sleep(0.4)
        return {"name": name, "running": True, "tools_count": 1}

    monkeypatch.setattr(mgr, "connect", slow_connect)
    started = time.monotonic()
    mgr.start_startup_connect()
    mgr._startup_thread.join(30)
    elapsed = time.monotonic() - started
    try:
        snap = mgr.startup_snapshot()
        assert snap["phase"] == "done"
        assert snap["connected"] == len(names)
        # Sequentially this is 1.6 s; four threads finish in about one slot.
        assert elapsed < 1.2, f"the launch run took {elapsed:.2f}s for {len(names)} servers"
    finally:
        mgr.shutdown()


# ---------- a connector that failed is retried out of sight ----------

def test_a_failed_connector_is_retried_in_the_background_throttled(tmp_path, monkeypatch):
    mgr = manager(tmp_path, {
        "alpha": {"command": "echo"},
        "beta": {"command": "echo", "disabled": True},
    })
    calls = []

    def flaky_connect(name, timeout=None):
        calls.append(name)
        return {"name": name, "running": False, "tools_count": 0, "error": "npx timed out"}

    monkeypatch.setattr(mgr, "connect", flaky_connect)
    try:
        # It failed at launch...
        mgr.start_startup_connect()
        mgr._startup_thread.join(30)
        assert mgr.startup_snapshot()["failed"] == 1
        assert calls == ["alpha"]

        # ...so a turn gives it one more chance, in the background, not inline.
        started = time.monotonic()
        assert mgr.retry_failed_in_background() == ["alpha"]
        assert time.monotonic() - started < 0.5
        assert mgr.retry_failed_in_background() == [], "one attempt a minute per connector"
        deadline = time.monotonic() + 5
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert calls == ["alpha", "alpha"]
        assert "beta" not in calls, "a switched-off connector is never retried"
    finally:
        mgr.shutdown()
