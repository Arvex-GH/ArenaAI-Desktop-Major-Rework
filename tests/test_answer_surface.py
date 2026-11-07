"""The answer's surface: no outline around a reply, no avatar in its head.

Reported from the app: every answer sat in an outlined card — most visible while
it streamed, when the border darkened — with a square avatar in front of the model
name. Only the user's own prompt is outlined now; the icon stays in the model
picker, where choosing a model needs something to aim at.

`tests/js/answer_surface.js` checks the rules and the render source, and resolves
the real cascade in jsdom when it is available (skipped otherwise, so CI needs no
npm).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "answer_surface.js"


def test_answer_surface_rules_and_render():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ)
    extra = os.environ.get("ARENA_JSDOM_PATH") or "/tmp/pagetest/node_modules"
    if Path(extra).is_dir():
        env["NODE_PATH"] = extra
    result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True,
                            timeout=120, cwd=str(ROOT), env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout
    assert "jsdom is not installed" in result.stdout or "computed:" in result.stdout
