"""A run of tool calls can be read as one line.

Reported from the installed app: a busy turn was a wall of rows — read, read,
write, command — with no way to see it as one paragraph of work. Consecutive
calls now form a group: one row that counts what it did ("2 reads, 1 write, 1
command"), open by default, folded by a click, and never spanning a thought or a
paragraph of the answer.

`tests/js/tool_groups.js` runs the real `renderLaneFlow` and `renderToolGroup` in
jsdom (with the rows and the markdown stubbed) and clicks a group closed and open
again; it is skipped without jsdom, so CI needs no npm.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "tool_groups.js"


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ)
    extra = os.environ.get("ARENA_JSDOM_PATH") or "/tmp/pagetest/node_modules"
    if Path(extra).is_dir():
        env["NODE_PATH"] = extra
    result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True,
                            timeout=180, cwd=str(ROOT), env=env)
    if result.returncode == 3:
        pytest.skip("jsdom is not installed")
    return result


def test_tool_groups():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout


def test_a_lone_call_is_not_a_group():
    """A group of one would say "1 read" above a row that already says "Read x"."""
    source = (ROOT / "arena" / "web" / "static" / "js" / "07-render.js").read_bytes().decode("utf-8")
    assert "if (run.length > 1) nodes.push(renderToolGroup(run));" in source.replace("\r\n", "\n")
    assert "else nodes.push(run[0].node);" in source.replace("\r\n", "\n")


def test_the_group_is_open_until_it_is_closed():
    source = (ROOT / "arena" / "web" / "static" / "js" / "07-render.js").read_bytes().decode("utf-8")
    flat = source.replace("\r\n", "\n")
    assert "const open = waiting || openGroups.has(id) || !closedGroups.has(id);" in flat
    assert "closedGroups.add(groupId);" in flat and "openGroups.add(groupId);" in flat


def test_a_pending_approval_is_never_folded_away():
    """A view may hide work; it may not hide a question addressed to the user."""
    source = (ROOT / "arena" / "web" / "static" / "js" / "07-render.js").read_bytes().decode("utf-8")
    flat = source.replace("\r\n", "\n")
    assert "const waiting = items.some((item) => rowHasClass(item.node, 'is-wait'));" in flat
    assert "{ class: 'tool-meta is-wait', text: 'waiting' }" in flat
