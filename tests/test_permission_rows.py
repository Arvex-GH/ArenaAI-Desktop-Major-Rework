"""A granted approval must not echo itself into the thread.

The tool row for a command already carries the command and its exit code, so an
extra `Allowed <command> allowed` line underneath was pure noise — the thread in
1.5.5 showed it once per approved command. A refusal is the one verdict worth
keeping, and it belongs on the command's own row in red ("rejected by user").

`tests/js/permission_rows.js` checks both the pure helpers and the render source;
this wires it into pytest and skips on a machine without node.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "permission_rows.js"


def run_harness() -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=120, cwd=str(ROOT))


def test_no_echo_for_granted_approvals_and_red_only_on_refusal():
    result = run_harness()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout
