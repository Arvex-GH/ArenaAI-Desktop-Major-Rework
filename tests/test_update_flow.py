"""The update dialog and the update job: forward-only progress, one click one job.

Reported from the installed app, after 1.5.16:

    "before releasing, please also fix the update installer because currently he
     always gives up an error saying that there already is an update installed.
     and the progress bar also progresses and then regresses and then progresses
     until she reaches the end."

Both symptoms came from the primary button of the update dialog having **two**
handlers: `setPrimary()` assigns `primary.onclick` on every state change, and a
second `primary.addEventListener('click', () => startOrOpen())` ran as well. One
click therefore did two things:

* on "Download and install" it started two downloads of the same file into the
  same `.part` file, and the second job reset the first one — the bar went back
  to 1% and the phase flipped under the dialog;
* on "Restart and update" it started the update *and* began downloading again,
  so `POST /api/update/install` could land while the job had been reset to
  `downloading` and answer 409 "The update has not finished downloading yet." —
  a red error about the state of an update the user had just finished.

The bar is also clamped now in all three places that drive it (the job, the
dialog, the setup window), so a late or reordered answer cannot walk it back.

`tests/js/update_dialog.js` boots the real dialog in jsdom with a scripted job
that regresses on purpose and asserts: one click, one download; installing does
not download again; the bar reads 5 → 38 → 75 → 100; and "You are already on the
newest version." is shown as a calm note with a Close button, not as an error.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "update_dialog.js"


def _js(name: str) -> str:
    return (ROOT / "arena" / "web" / "static" / "js" / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# The job: bytes only arrive, so the percentage can only grow.
# ---------------------------------------------------------------------------
def test_progress_never_goes_backwards():
    from arena import updater

    job = updater.Job()
    job.reset(phase="downloading", total=1000)
    for received in (100, 400, 250, 900, 1000):
        job.update(received=received)
    assert job.snapshot()["pct"] == 100

    job.reset(phase="downloading", total=1000)
    job.update(received=400)
    job.update(received=250)          # a reordered answer
    assert job.snapshot()["pct"] == 40

    job.reset(phase="downloading", total=0)
    assert job.snapshot()["pct"] == 0  # a new download starts from the start


def test_a_download_does_not_start_while_the_update_is_installing():
    from arena import updater

    job = updater.Job()
    job.reset(phase="ready", pct=100, path="/tmp/setup.exe")
    job.update(phase="installing", pct=100)
    snapshot = job.start_download({"installer": {"name": "x.exe", "size": 1,
                                                 "url": "https://github.com/x/y.exe"}})
    assert snapshot["phase"] == "installing"
    assert job.snapshot()["phase"] == "installing"


# ---------------------------------------------------------------------------
# The dialog: one handler, forward-only bar, and a calm "already newest".
# ---------------------------------------------------------------------------
def test_the_primary_button_has_one_handler():
    source = _js("15-update.js")
    assert "primary.addEventListener('click'" not in source
    assert source.count("primary.onclick = onClick") == 1


def test_the_bar_is_clamped_in_the_dialog():
    source = _js("15-update.js")
    assert "const value = Math.max(shownPct, Math.min(100, Number(pct) || 0));" in source
    assert "if (polling) return;" in source
    assert ".finally(() => { polling = false; });" in source


def test_the_download_rechecks_the_feed_and_takes_already_newest_calmly():
    source = _js("15-update.js")
    assert "await API.updateDownload(true)" in source
    assert "already on the newest|newest version|no update" in source
    assert "This build is the newest release on the feed. Nothing to install." in source


def test_the_setup_window_bar_is_clamped_too():
    html = (ROOT / "arena" / "setup" / "web" / "index.html").read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert "const value = Math.max(shownPct, Math.max(0, Math.min(100, Math.round(pct))));" in html
    assert "function resetBar()" in html


def test_the_install_endpoint_claims_the_job_first():
    source = (ROOT / "arena" / "web" / "server" / "updates.py").read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert source.index('_updater.JOB.update(phase="installing", pct=100)') < source.index("_updater.launch_installer")
    assert "The update is already being installed." in source


def test_the_dialog_harness():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    env = dict(os.environ)
    extra = os.environ.get("ARENA_JSDOM_PATH") or "/tmp/pagetest/node_modules"
    if Path(extra).is_dir():
        env["NODE_PATH"] = extra
    result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True,
                            timeout=240, cwd=str(ROOT), env=env)
    if result.returncode == 3:
        pytest.skip("jsdom is not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
    assert "FAIL" not in result.stdout
