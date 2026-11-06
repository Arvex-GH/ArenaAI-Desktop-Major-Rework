"""Installer, uninstaller and in-place update mechanics for Arena Desktop.

Everything here is plain Python so it can be unit-tested on any OS: the
Windows-only calls (registry, shortcuts, process flags) are isolated in small
functions with ``sys.platform`` guards.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Optional

APP_ID = "ArenaDesktop"
APP_NAME = "Arena Desktop"
APP_FOLDER = "Arena Desktop"
EXE_NAME = "Arena-Desktop.exe"
PUBLISHER = "Arena Desktop community build"
HOMEPAGE = "https://github.com/PlayzAe/arena.ai-desktop"
MANIFEST_NAME = "install.json"

StepFn = Optional[Callable[[dict], None]]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def is_supported() -> bool:
    """The installer only runs on Windows."""
    return sys.platform == "win32"


def _local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def install_dir() -> Path:
    """Per-user install location, no admin rights required."""
    return _local_appdata() / "Programs" / APP_FOLDER


def install_manifest_path() -> Path:
    return install_dir() / MANIFEST_NAME


def installed_exe() -> Path:
    return install_dir() / EXE_NAME


def is_installed() -> bool:
    return installed_exe().exists()


def current_exe() -> Path:
    """The executable we are running from (the frozen build, not python.exe)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return Path(sys.argv[0]).resolve()


def running_installed_build() -> bool:
    """True when this process is the copy inside the install folder."""
    try:
        return is_installed() and current_exe().samefile(installed_exe())
    except OSError:
        return False


def install_manifest() -> dict:
    try:
        return json.loads(install_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def installed_version() -> Optional[str]:
    version = install_manifest().get("version")
    if version:
        return str(version)
    value = _registry_value("DisplayVersion")
    return str(value) if value else None


# ---------------------------------------------------------------------------
# Shortcuts and registry
# ---------------------------------------------------------------------------
def _start_menu_dir() -> Path:
    raw = os.environ.get("APPDATA")
    base = Path(raw) if raw else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _desktop_dir() -> Path:
    raw = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    base = Path(raw) if raw else Path.home()
    return base / "Desktop"


def shortcut_paths() -> Dict[str, Path]:
    return {
        "start_menu": _start_menu_dir() / f"{APP_FOLDER}.lnk",
        "desktop": _desktop_dir() / f"{APP_FOLDER}.lnk",
    }


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(link: Path, target: Path, description: str = APP_NAME,
                    icon: Optional[Path] = None) -> bool:
    """Create a .lnk through the WScript.Shell COM object (no extra deps)."""
    if sys.platform != "win32":
        return False
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut({link});"
        "$s.TargetPath={target};"
        "$s.WorkingDirectory={work};"
        "$s.IconLocation={icon};"
        "$s.Description={desc};"
        "$s.Save()"
    ).format(
        link=_ps_quote(str(link)),
        target=_ps_quote(str(target)),
        work=_ps_quote(str(target.parent)),
        icon=_ps_quote(f"{icon or target},0"),
        desc=_ps_quote(description),
    )
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return link.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def uninstall_key() -> str:
    return rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"


def _registry_value(name: str):
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key()) as key:
            return winreg.QueryValueEx(key, name)[0]
    except (OSError, ImportError):
        return None


def _write_registry(version: str, exe: Path) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:
        return False
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "DisplayIcon": f"{exe},0",
        "InstallLocation": str(exe.parent),
        "UninstallString": f'"{exe}" --uninstall',
        "QuietUninstallString": f'"{exe}" --uninstall --silent',
        "NoModify": 1,
        "NoRepair": 1,
        "URLInfoAbout": HOMEPAGE,
        "InstallDate": time.strftime("%Y%m%d"),
    }
    try:
        size_kb = max(1, exe.stat().st_size // 1024)
        values["EstimatedSize"] = size_kb
    except OSError:
        pass
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, uninstall_key(), 0, winreg.KEY_WRITE) as key:
            for name, value in values.items():
                kind = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, kind, value)
        return True
    except OSError:
        return False


def _delete_registry() -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, uninstall_key())
    except (OSError, ImportError):
        pass


# ---------------------------------------------------------------------------
# Copying
# ---------------------------------------------------------------------------
def _copy_with_progress(src: Path, dst: Path, on_bytes: Optional[Callable[[int, int], None]] = None) -> None:
    total = src.stat().st_size or 1
    done = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".new")
    with src.open("rb") as fsrc, tmp.open("wb") as fdst:
        while True:
            chunk = fsrc.read(1 << 20)
            if not chunk:
                break
            fdst.write(chunk)
            done += len(chunk)
            if on_bytes:
                on_bytes(done, total)
    shutil.copystat(src, tmp, follow_symlinks=False)
    _replace_file(tmp, dst)


def _replace_file(new_file: Path, target: Path, attempts: int = 40) -> None:
    """Move ``new_file`` onto ``target``, working around a locked (running) exe."""
    last: Optional[BaseException] = None
    for i in range(attempts):
        try:
            os.replace(new_file, target)
            return
        except OSError as exc:  # typically PermissionError from a running exe
            last = exc
            if target.exists():
                stale = target.with_name(target.stem + ".old" + target.suffix)
                try:
                    if stale.exists():
                        stale.unlink()
                    os.replace(target, stale)
                    continue
                except OSError:
                    pass
            time.sleep(min(0.25 * (i + 1), 2.0))
    raise last or OSError("Could not replace the application file.")


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------
def _emit(on_step: StepFn, step: str, label: str, progress: float, detail: str = "") -> None:
    if on_step:
        on_step({"step": step, "label": label, "progress": max(0.0, min(1.0, progress)), "detail": detail})


def run_install(version: str, source_exe: Optional[Path] = None, on_step: StepFn = None,
                desktop_icon: bool = False, launch: bool = False) -> dict:
    """Install this build for the current user.

    Safe to run over an existing install: the application file is replaced and
    user data (chats, accounts, settings) is never touched.
    """
    _emit(on_step, "prepare", "Preparing", 0.02)
    target_dir = install_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    source = Path(source_exe) if source_exe else current_exe()
    target = target_dir / EXE_NAME
    if not source.exists():
        raise FileNotFoundError(f"The application file was not found: {source}")

    same_file = False
    try:
        same_file = source.exists() and target.exists() and source.samefile(target)
    except OSError:
        same_file = False

    _emit(on_step, "copy", "Copying the application", 0.08)
    if not same_file:
        def _bytes(done: int, total: int) -> None:
            _emit(on_step, "copy", "Copying the application", 0.08 + 0.62 * (done / max(total, 1)),
                  f"{done / 1e6:.0f} of {total / 1e6:.0f} MB")

        _copy_with_progress(source, target, _bytes)
    _emit(on_step, "copy", "Copying the application", 0.72, "Done")

    _emit(on_step, "shortcuts", "Creating shortcuts", 0.78)
    links = shortcut_paths()
    created = []
    if create_shortcut(links["start_menu"], target):
        created.append("start menu")
    if desktop_icon and create_shortcut(links["desktop"], target):
        created.append("desktop")
    _emit(on_step, "shortcuts", "Creating shortcuts", 0.86,
          ", ".join(created) or "skipped")

    _emit(on_step, "register", "Registering with Windows", 0.9)
    registered = _write_registry(version, target)

    manifest = {
        "app": APP_NAME,
        "version": version,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exe": str(target),
        "source": str(source),
        "previous": install_manifest().get("version"),
    }
    try:
        install_manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass
    _emit(on_step, "register", "Registering with Windows", 0.95,
          "added to Apps & features" if registered else "user data kept")

    _emit(on_step, "finish", "Finishing", 1.0)
    if launch:
        launch_installed()
    return {"ok": True, "install_dir": str(target_dir), "shortcuts": created,
            "registered": registered, "version": version}


def launch_installed() -> bool:
    exe = installed_exe()
    if not exe.exists():
        return False
    try:
        subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return True
    except OSError:
        return False


def user_data_dir() -> Path:
    try:
        from arena.config import CONFIG_DIR
        return Path(CONFIG_DIR)
    except Exception:
        return Path.home() / ".arena-personal"


def schedule_self_delete(folder: Path) -> None:
    """Delete the install folder after this process exits (Windows locks the exe)."""
    if sys.platform != "win32":
        return
    cmd = (
        "@echo off\r\n"
        "timeout /t 2 /nobreak >nul\r\n"
        f'rmdir /s /q "{folder}"\r\n'
        'del /f /q "%~f0"\r\n'
    )
    try:
        script = Path(os.environ.get("TEMP", ".")) / f"arena-desktop-uninstall-{os.getpid()}.cmd"
        script.write_text(cmd, encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(script)], close_fds=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         | getattr(subprocess, "DETACHED_PROCESS", 0))
    except OSError:
        pass


def run_uninstall(remove_user_data: bool = False, on_step: StepFn = None) -> dict:
    _emit(on_step, "shortcuts", "Removing shortcuts", 0.15)
    for link in shortcut_paths().values():
        try:
            if link.exists():
                link.unlink()
        except OSError:
            pass

    _emit(on_step, "register", "Unregistering", 0.4)
    _delete_registry()

    if remove_user_data:
        _emit(on_step, "data", "Removing chats and settings", 0.6)
        shutil.rmtree(user_data_dir(), ignore_errors=True)

    _emit(on_step, "files", "Removing the application", 0.8)
    target_dir = install_dir()
    if running_installed_build():
        schedule_self_delete(target_dir)
    else:
        shutil.rmtree(target_dir, ignore_errors=True)
    _emit(on_step, "finish", "Done", 1.0)
    return {"ok": True, "kept_user_data": not remove_user_data}


# ---------------------------------------------------------------------------
# Update helper
# ---------------------------------------------------------------------------
def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
            if not handle:
                return False
            try:
                return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_for_exit(pid: int, timeout: float = 120.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not _pid_running(pid):
            return True
        time.sleep(0.3)
    return not _pid_running(pid)


def apply_update(pid: int, new_exe: Path, relaunch: bool = True) -> dict:
    """Replace the installed exe with a freshly downloaded build and restart."""
    new_exe = Path(new_exe)
    if not new_exe.exists():
        raise FileNotFoundError("The downloaded update is missing.")
    wait_for_exit(int(pid))
    target = installed_exe()
    if not target.exists():
        # Portable copy that was never installed: just start the new build.
        if relaunch:
            subprocess.Popen([str(new_exe)], close_fds=True,
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return {"ok": True, "mode": "portable"}
    _replace_file(new_exe, target)
    try:
        manifest = install_manifest()
        manifest["previous"] = manifest.get("version")
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        install_manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass
    if relaunch:
        launch_installed()
    return {"ok": True, "mode": "installed", "path": str(target)}


def sync_installed_manifest(version: str) -> None:
    """Keep install.json truthful after an update replaced the exe in place."""
    if not is_installed():
        return
    try:
        if not running_installed_build():
            return
    except OSError:
        return
    manifest = install_manifest()
    if str(manifest.get("version")) == str(version):
        return
    manifest.setdefault("app", APP_NAME)
    manifest["previous"] = manifest.get("version")
    manifest["version"] = version
    manifest["exe"] = str(installed_exe())
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        install_manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError:
        pass
    _write_registry(version, installed_exe())
