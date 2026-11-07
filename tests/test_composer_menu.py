"""The composer is two icons and Send; the Agent backend is one click away.

Reported from the installed app: the toolbar spelled out six chips, which read
as a wall of buttons next to the message box, and choosing the Local agent meant
three clicks into Settings every time the app was opened. 1.5.15 moves all of it
into one "Chat options" panel behind an icon, with the two Agent backends inside
it, and takes the Permissions panel out of Settings.

`tests/js/composer_menu_dom.js` boots the real page in jsdom (skipped without
jsdom, so CI needs no npm) and clicks through it: the toolbar shape, the
tooltips, Local/Cloud agent, permissions, mode, and Settings.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "composer_menu_dom.js"


def test_composer_menu_and_toolbar():
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
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout


def test_the_old_chips_are_not_drawn():
    """The six text chips may stay in the DOM (they are the wiring), never on screen."""
    markup = (ROOT / "arena" / "web" / "static" / "index.html").read_bytes().decode("utf-8")
    css = (ROOT / "arena" / "web" / "static" / "style.css").read_bytes().decode("utf-8")
    assert 'id="composerMenuButton"' in markup and 'id="attachButton"' in markup
    stash_at = markup.index('class="toolbar-stash"')
    toolbar_at = markup.index('class="composer-toolbar"')
    assert toolbar_at < stash_at
    # Everything that used to be a chip is inside the stash.
    tail = markup[stash_at:markup.index('</div>', markup.index('id="permissionButton"'))]
    for chip in ('id="modeButton"', 'id="modalityButton"', 'id="workspaceButton"',
                 'id="permissionButton"', 'id="workspaceSyncChip"'):
        assert chip in tail, chip
    assert ".toolbar-stash { display: none !important; }" in css.replace("\r\n", "\n")


def test_the_permissions_panel_is_gone_from_settings():
    settings = (ROOT / "arena" / "web" / "static" / "js" / "12-settings.js").read_bytes().decode("utf-8")
    assert "renderPermissionsPanel" not in settings
    assert "{ id: 'permissions'" not in settings
    assert "'Agent for new chats'" not in settings
