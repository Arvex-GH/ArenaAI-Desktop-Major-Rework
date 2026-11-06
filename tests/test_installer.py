"""Installer, uninstall and in-app update behaviour.

These tests never touch the real install folder, the registry or the network:
the Windows calls are replaced and every download is a fake response.
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

from arena import updater
from arena.setup import core


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def install_root(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home" / "Desktop").mkdir(parents=True)
    return local


@pytest.fixture
def source_exe(tmp_path):
    exe = tmp_path / "download" / "Arena-Desktop.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ" + b"\x00" * 4096)
    return exe


@pytest.fixture
def quiet_windows(monkeypatch):
    """No shortcuts, no registry, no process starts."""
    monkeypatch.setattr(core, "create_shortcut", lambda *a, **k: True)
    monkeypatch.setattr(core, "_write_registry", lambda *a, **k: True)
    monkeypatch.setattr(core, "launch_installed", lambda: True)
    popped = []

    class _Proc:
        pass

    def fake_popen(args, **kwargs):
        popped.append(args)
        return _Proc()

    monkeypatch.setattr(core.subprocess, "Popen", fake_popen)
    return popped


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
def test_install_copies_app_and_writes_manifest(install_root, source_exe, quiet_windows):
    steps = []
    result = core.run_install(version="1.6.0", source_exe=source_exe, on_step=steps.append)

    target = install_root / "Programs" / "Arena Desktop" / "Arena-Desktop.exe"
    assert target.exists()
    assert target.read_bytes() == source_exe.read_bytes()
    assert result["version"] == "1.6.0"

    manifest = json.loads(core.install_manifest_path().read_text())
    assert manifest["version"] == "1.6.0"
    assert manifest["app"] == "Arena Desktop"
    assert core.installed_version() == "1.6.0"
    assert core.is_installed() is True

    # Progress only ever moves forward and ends at 1.0.
    values = [step["progress"] for step in steps]
    assert values[-1] == 1.0
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_reinstall_keeps_previous_version_and_never_touches_user_data(
        install_root, source_exe, quiet_windows):
    core.run_install(version="1.5.0", source_exe=source_exe)
    data = core.user_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "config.json").write_text('{"permission_mode": "acceptEdits"}', encoding="utf-8")

    newer = source_exe.with_name("Arena-Desktop-Setup-1.6.0.exe")
    newer.write_bytes(b"MZ" + b"\x01" * 8192)
    core.run_install(version="1.6.0", source_exe=newer)

    manifest = json.loads(core.install_manifest_path().read_text())
    assert manifest["version"] == "1.6.0"
    assert manifest["previous"] == "1.5.0"
    assert json.loads((data / "config.json").read_text())["permission_mode"] == "acceptEdits"


def test_install_refuses_missing_source(install_root, tmp_path, quiet_windows):
    with pytest.raises(FileNotFoundError):
        core.run_install(version="1.6.0", source_exe=tmp_path / "nope.exe")


def test_install_from_inside_the_install_folder_skips_the_copy(install_root, quiet_windows):
    core.run_install(version="1.6.0", source_exe=Path(__file__))  # any readable file
    target = core.installed_exe()
    before = target.stat().st_mtime_ns
    core.run_install(version="1.6.0", source_exe=target)
    assert target.stat().st_mtime_ns == before


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------
def test_uninstall_removes_the_app_but_keeps_data_by_default(install_root, source_exe, quiet_windows):
    core.run_install(version="1.6.0", source_exe=source_exe)
    data = core.user_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "cookies.txt").write_text("secret", encoding="utf-8")

    result = core.run_uninstall(remove_user_data=False)

    assert result["kept_user_data"] is True
    assert not core.install_dir().exists()
    assert (data / "cookies.txt").exists()


def test_uninstall_can_wipe_user_data(install_root, source_exe, quiet_windows):
    core.run_install(version="1.6.0", source_exe=source_exe)
    data = core.user_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "cookies.txt").write_text("secret", encoding="utf-8")

    core.run_uninstall(remove_user_data=True)
    assert not data.exists()


# ---------------------------------------------------------------------------
# Updating in place
# ---------------------------------------------------------------------------
def test_apply_update_replaces_the_installed_build(install_root, source_exe, quiet_windows):
    core.run_install(version="1.6.0", source_exe=source_exe)
    new_build = source_exe.with_name("new.exe")
    new_build.write_bytes(b"MZ" + b"\x02" * 512)

    result = core.apply_update(pid=0, new_exe=new_build)

    assert result["ok"] is True and result["mode"] == "installed"
    assert core.installed_exe().read_bytes()[:3] == b"MZ\x02"
    assert new_build.exists() is False  # moved, not copied


def test_apply_update_on_a_portable_copy_starts_the_new_build(install_root, source_exe, quiet_windows):
    new_build = source_exe.with_name("portable-new.exe")
    new_build.write_bytes(b"MZ" + b"\x03" * 32)

    result = core.apply_update(pid=0, new_exe=new_build)

    assert result["mode"] == "portable"
    assert quiet_windows and str(new_build) in quiet_windows[-1]


def test_apply_update_needs_a_file(install_root, tmp_path):
    with pytest.raises(FileNotFoundError):
        core.apply_update(pid=0, new_exe=tmp_path / "gone.exe")


def test_manifest_follows_a_replaced_exe(install_root, source_exe, quiet_windows, monkeypatch):
    core.run_install(version="1.6.0", source_exe=source_exe)
    monkeypatch.setattr(core, "running_installed_build", lambda: True)
    monkeypatch.setattr(core, "is_installed", lambda: True)

    core.sync_installed_manifest("1.6.1")

    manifest = json.loads(core.install_manifest_path().read_text())
    assert manifest["version"] == "1.6.1"
    assert manifest["previous"] == "1.6.0"


# ---------------------------------------------------------------------------
# Release feed
# ---------------------------------------------------------------------------
def _release(tag="v1.6.1", assets=None, prerelease=False):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/PlayzAe/arena.ai-desktop/releases/tag/{tag}",
        "published_at": "2026-11-05T10:00:00Z",
        "body": "## What changed\n\n- Faster start\n",
        "prerelease": prerelease,
        "assets": assets if assets is not None else [
            {"name": "Arena-Desktop-Setup-1.6.1.exe", "size": 100,
             "browser_download_url": "https://github.com/x/setup.exe", "digest": "sha256:abc"},
            {"name": "Arena-Desktop-1.6.1.exe", "size": 90,
             "browser_download_url": "https://github.com/x/build.exe"},
        ],
    }


def test_feed_defaults_to_our_repo(monkeypatch):
    # Builds update from our own releases, not from the fork's upstream source.
    monkeypatch.delenv("ARENA_UPDATE_REPO", raising=False)
    assert updater.feed_repo() == "Arvex-GH/ArenaAI-Desktop-Major-Rework"


def test_version_comparison():
    assert updater.is_newer("1.6.1", "1.6.0") is True
    assert updater.is_newer("1.6.0", "1.6.0") is False
    assert updater.is_newer("1.5.9", "1.6.0") is False
    assert updater.is_newer("v2.0", "1.9.9") is True


def test_release_meta_prefers_the_installer_asset():
    meta = updater.normalize_release(_release())
    assert meta["version"] == "1.6.1"
    assert meta["installer"]["name"] == "Arena-Desktop-Setup-1.6.1.exe"
    assert meta["installer"]["kind"] == "installer"


def test_release_meta_falls_back_to_the_plain_build():
    release = _release(assets=[{"name": "Arena-Desktop-1.6.1.exe", "size": 90,
                                "browser_download_url": "https://github.com/x/build.exe"}])
    meta = updater.normalize_release(release)
    assert meta["installer"]["kind"] == "build"


def test_release_meta_without_assets():
    meta = updater.normalize_release({"tag_name": "v1.0.0"})
    assert meta["installer"] is None
    assert meta["version"] == "1.0.0"


def test_update_links_must_come_from_the_feed():
    assert updater.is_allowed_url("https://github.com/a/b.exe") is True
    assert updater.is_allowed_url("http://github.com/a/b.exe") is False
    assert updater.is_allowed_url("https://evil.example/b.exe") is False
    assert updater.is_allowed_url("file:///C:/b.exe") is False


class _FakeResponse:
    def __init__(self, payload=b"", status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}

    def iter_content(self, size):
        for i in range(0, len(self._payload), size):
            yield self._payload[i:i + size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_download_verifies_size_and_digest(tmp_path, monkeypatch):
    payload = b"MZ" + b"\x04" * 1000
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(updater, "download_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResponse(payload, headers={"Content-Length": str(len(payload))}))
    progress = []

    path = updater.download_asset({
        "name": "Arena-Desktop-Setup-1.6.1.exe",
        "url": "https://github.com/PlayzAe/arena.ai-desktop/releases/download/v1.6.1/a.exe",
        "size": len(payload), "digest": f"sha256:{digest}",
    }, on_progress=lambda done, total: progress.append((done, total)))

    assert path.read_bytes() == payload
    assert progress[-1][0] == len(payload)


def test_download_rejects_a_bad_checksum(tmp_path, monkeypatch):
    payload = b"MZ" + b"\x05" * 100
    monkeypatch.setattr(updater, "download_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResponse(payload, headers={"Content-Length": str(len(payload))}))
    with pytest.raises(IOError):
        updater.download_asset({
            "name": "bad.exe", "url": "https://github.com/a/b.exe", "size": len(payload),
            "digest": "sha256:" + "0" * 64,
        })
    assert not list(tmp_path.glob("*.exe"))


def test_download_rejects_an_incomplete_file(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "download_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.requests, "get",
                        lambda *a, **k: _FakeResponse(b"MZ", headers={"Content-Length": "999"}))
    with pytest.raises(IOError):
        updater.download_asset({"name": "short.exe", "url": "https://github.com/a/b.exe", "size": 999})


def test_download_rejects_foreign_hosts(tmp_path):
    with pytest.raises(ValueError):
        updater.download_asset({"name": "x.exe", "url": "https://evil.example/x.exe", "size": 1})


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@pytest.fixture
def api(monkeypatch):
    import arena.web.app as web
    web._update_cache.clear()
    updater.JOB.reset()
    return web, web.app.test_client()


def test_update_check_reports_the_installer(api, monkeypatch):
    web, client = api
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": lambda self: _release()})())
    data = client.get("/api/update-check?force=1").get_json()
    assert data["update"] is True and data["latest"] == "1.6.1"
    assert data["installer"]["name"] == "Arena-Desktop-Setup-1.6.1.exe"
    assert data["install_mode"] in {"manual", "helper"}
    assert data["repo"] == updater.feed_repo()


def test_update_check_handles_a_missing_release(api, monkeypatch):
    web, client = api
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: type(
        "R", (), {"status_code": 404, "json": lambda self: {}})())
    data = client.get("/api/update-check?force=1").get_json()
    assert data["update"] is False
    assert "No release" in (data["error"] or "")


def test_update_download_is_refused_when_up_to_date(api, monkeypatch):
    web, client = api
    monkeypatch.setattr(web.requests, "get", lambda *a, **k: type(
        "R", (), {"status_code": 200, "json": lambda self: _release(tag="v0.0.1")})())
    got = client.post("/api/update/download", json={"force": True}).get_json()
    assert got["ok"] is False
    assert "newest" in got["error"]


def test_update_install_is_refused_without_a_download(api):
    _web, client = api
    got = client.post("/api/update/install", json={}).get_json()
    assert got["ok"] is False


def test_update_progress_and_cancel(api):
    _web, client = api
    got = client.get("/api/update/progress").get_json()
    assert got["ok"] is True and got["job"]["phase"] == "idle"
    assert client.post("/api/update/cancel", json={}).get_json()["ok"] is True


def test_install_info_shape(api):
    _web, client = api
    got = client.get("/api/install-info").get_json()
    assert got["version"]
    assert "installed" in got and "mode" in got
