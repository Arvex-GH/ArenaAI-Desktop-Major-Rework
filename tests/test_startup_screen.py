"""The start-up screen and the saved-account list in Settings.

`tests/js/startup_screen.js` holds the checks — the screen polls the connector
route, draws the progress bar, names each connector, offers a retry, and never
sits in front of the app; Settings lists the saved accounts and switches them
without signing out. This wires the harness into pytest and skips when node is
missing.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "startup_screen.js"


def test_startup_screen_and_account_switcher_contract():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout


DOM_HARNESS = Path(__file__).resolve().parent / "js" / "startup_screen_dom.js"


def test_startup_screen_renders_in_a_real_dom():
    """The screen as the user sees it: jsdom, index.html and app.js, a stubbed API.

    Skipped where jsdom is missing (CI needs no npm); run it locally with
    ``npm i jsdom`` or ARENA_JSDOM_PATH pointing at a node_modules with jsdom.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ)
    extra = os.environ.get("ARENA_JSDOM_PATH") or "/tmp/pagetest/node_modules"
    if Path(extra).is_dir():
        env["NODE_PATH"] = extra
    result = subprocess.run([node, str(DOM_HARNESS)], capture_output=True, text=True,
                            timeout=180, cwd=str(ROOT), env=env)
    if result.returncode == 3:
        pytest.skip(result.stdout.strip() or "jsdom is not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout
