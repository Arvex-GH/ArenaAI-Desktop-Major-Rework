"""v1.5.4: the two bugs that shipped in 1.5.3.

1. ``KeyError: 'bypassPermissions'`` — every prompt sent while the permission
   mode was "Bypass permissions" died before the model was called, because
   ``agent.MODE_NOTES`` had four of the five modes.
2. The setup window froze on "Starting…" and Windows marked it "not
   responding", because ``SetupApi.window`` was a public attribute and
   pywebview walks every public attribute of ``js_api`` to build the page's
   bridge — the walk reached the WinForms form and the WebView2 COM objects.
"""

import threading
import time

import pytest

from arena import agent, permissions
from arena.setup import core, ui


# ---------- permission modes ----------

def test_every_permission_mode_has_a_note():
    assert set(permissions.MODES) - set(agent.MODE_NOTES) == set()


@pytest.mark.parametrize("mode", sorted(permissions.MODES))
def test_mode_note_and_guide_build_for_every_mode(mode, tmp_path):
    note = agent.mode_note(mode)
    assert isinstance(note, str) and note
    # The line that raised KeyError: 'bypassPermissions' in 1.5.3.
    text = agent.guide(tmp_path, permissions.MODES[mode], note)
    assert "Permission mode:" in text


def test_mode_note_falls_back_instead_of_raising():
    assert agent.mode_note("no-such-mode") == agent.MODE_NOTES["default"]
    assert agent.mode_note(None) == agent.MODE_NOTES["default"]


def test_bypass_note_describes_what_still_asks():
    note = agent.mode_note("bypassPermissions").lower()
    assert "bypass" in note and "shell syntax" in note


# ---------- setup window ----------

def test_setup_api_public_surface_is_plain_values():
    """pywebview walks these; one object in here deadlocks the message pump."""
    api = ui.SetupApi()
    api._window = object()  # what run_setup does after create_window
    plain = (str, int, float, bool, type(None))
    for name in dir(api):
        if name.startswith("_"):
            continue
        value = getattr(api, name)
        assert callable(value) or isinstance(value, plain), f"{name} is {type(value).__name__}"


def test_setup_api_keeps_the_window_private():
    api = ui.SetupApi()
    assert api._window is None
    assert not hasattr(api, "window")
    api._window = object()
    assert api._window is not None


def test_bridge_watchdog_installs_when_the_page_never_calls(monkeypatch):
    calls, quits = [], []
    monkeypatch.setattr(core, "run_install",
                        lambda **kw: calls.append(kw) or {"install_dir": "C:/Arena"})
    api = ui.SetupApi(mode="install")
    api._quit = lambda code: quits.append(code)  # don't os._exit the test
    ui._bridge_watchdog(api, seconds=0.01)
    assert len(calls) == 1
    assert api._snapshot()["phase"] == "done"
    assert quits == [0]


def test_bridge_watchdog_stays_out_of_the_way_when_the_page_is_alive(monkeypatch):
    monkeypatch.setattr(core, "run_install", lambda **kw: pytest.fail("installed twice"))
    api = ui.SetupApi()
    api.info()  # the page reached the bridge
    ui._bridge_watchdog(api, seconds=0.01)
    assert api._snapshot()["phase"] == "idle"


def test_start_is_idempotent_so_a_late_bridge_cannot_install_twice(monkeypatch):
    calls = []
    done = threading.Event()

    def fake_install(**kw):
        calls.append(kw)
        done.wait(2)
        return {"install_dir": "C:/Arena"}

    monkeypatch.setattr(core, "run_install", fake_install)
    api = ui.SetupApi()
    api.start()
    api.start()
    done.set()
    for _ in range(200):
        if api._snapshot()["phase"] == "done":
            break
        time.sleep(0.01)
    assert len(calls) == 1
