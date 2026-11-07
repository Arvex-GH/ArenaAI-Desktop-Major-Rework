"""The setup page must pick up the pywebview bridge however late it arrives.

The wall this hit in 1.5.3/1.5.4: pywebview fires `pywebviewready` on `window`
and injects `window.pywebview.api` a second and a half after the page loads,
while the page listened on `document` and checked once at 900 ms — so the setup
window never started the install and Windows called it unresponsive.

`tests/js/setup_page_bridge.js` replays that in jsdom. Here it is wired into
pytest: the static assertions always run, and the browser-level replay runs when
node and jsdom are installed (skipped otherwise, so CI needs no npm).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parent.parent / "arena" / "setup" / "web" / "index.html"
HARNESS = Path(__file__).resolve().parent / "js" / "setup_page_bridge.js"


def page_source() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_the_page_listens_on_window_not_only_document():
    src = page_source()
    assert 'window.addEventListener("pywebviewready", check)' in src
    assert 'document.addEventListener("pywebviewready", check)' in src


def test_the_page_polls_for_a_late_bridge():
    src = page_source()
    assert "function whenBridge(" in src
    assert "setInterval(" in src
    # The 1.5.4 shape: one document listener and a single 900 ms check.
    assert 'document.addEventListener("pywebviewready", init, { once: true })' not in src


def test_a_missing_bridge_is_admitted_not_animated_in_a_real_window():
    src = page_source()
    assert "function waitingForBridge(" in src
    assert "Finishing the install in the background" in src


def _run_harness() -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ)
    extra = os.environ.get("ARENA_JSDOM_PATH")
    if extra:
        env["NODE_PATH"] = extra
    return subprocess.run([node, str(HARNESS), str(PAGE)],
                          capture_output=True, text=True, timeout=120, env=env)


def test_bridge_arrival_modes_in_a_dom():
    result = _run_harness()
    if "Cannot find module 'jsdom'" in (result.stderr or ""):
        pytest.skip("jsdom is not installed (npm i jsdom)")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    for label in ("bridge at load", "late, event on window", "late, event on document", "never arrives"):
        assert label in result.stdout
    never = [line for line in result.stdout.splitlines() if line.startswith("never arrives")][0]
    payload = json.loads(never.split(":", 1)[1].strip())
    assert payload["calls"] == []
