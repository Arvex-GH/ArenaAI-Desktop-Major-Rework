# Arena Desktop web backend, part 12 of 12: updates.
# The release feed, the download/verify/apply flow, and the install
# identity that tells Settings where this copy came from.
#
# Not a standalone module: arena/web/app.py runs the parts in order
# inside its own namespace, so names from earlier parts are in scope.
# The HTTP layer lives here rather than in arena/updater.py so tests can
# patch arena.web.app.requests the same way they patch every other route.

import sys

from arena import updater as _updater

_update_cache: dict = {}


def _version_tuple(v) -> tuple:
    parts = []
    for bit in str(v or "").lstrip("v").split("."):
        try:
            parts.append(int(bit))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def _update_install_mode() -> str:
    """How this copy can take an update.

    ``helper`` — frozen Windows build: the downloaded setup runs instead of this
    process and installs over it.  ``manual`` — running from source (or not on
    Windows): the user gets the release page instead.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return "manual"
    return "helper"


def _update_info(channel: str, force: bool = False) -> dict:
    """Latest release from the configured feed, cached for 6 hours per channel."""
    now = time.time()
    slot = _update_cache.setdefault(channel, {"t": 0.0, "data": None})
    if not force and slot["data"] and now - slot["t"] < 6 * 3600:
        return dict(slot["data"], cached=True)

    mode = _update_install_mode()
    data = {
        "current": APP_VERSION, "latest": None, "update": False, "url": None,
        "error": None, "channel": channel, "prerelease": False,
        "repo": _updater.feed_repo(), "notes": "", "published_at": None,
        "installer": None, "install_mode": mode, "can_install": False,
        "installed_version": None,
    }
    try:
        from arena.setup import core as _setup_core
        data["installed_version"] = _setup_core.installed_version()
    except Exception:
        pass

    repo = f"{_updater.API_ROOT}/repos/{_updater.feed_repo()}"
    url = f"{repo}/releases?per_page=10" if channel == "beta" else f"{repo}/releases/latest"
    try:
        r = requests.get(url,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": f"Arena-Desktop/{APP_VERSION}"}, timeout=6)
        if r.status_code == 200:
            raw = r.json()
            if channel == "beta":
                payload = next((x for x in raw if isinstance(x, dict) and not x.get("draft")), {}) \
                    if isinstance(raw, list) else {}
            else:
                payload = raw if isinstance(raw, dict) else {}
            latest = str(payload.get("tag_name") or "").lstrip("v")
            data["latest"] = latest or None
            data["url"] = payload.get("html_url")
            data["prerelease"] = bool(payload.get("prerelease"))
            data["update"] = bool(latest) and _version_tuple(latest) > _version_tuple(APP_VERSION)
            meta = _updater.normalize_release(payload)
            data["notes"] = meta.get("notes") or ""
            data["published_at"] = meta.get("published_at")
            data["installer"] = meta.get("installer")
            data["can_install"] = bool(meta.get("installer")) and mode == "helper"
        elif r.status_code == 404:
            data["error"] = "No release is published yet."
        else:
            data["error"] = f"GitHub returned {r.status_code}"
    except Exception as e:  # noqa: BLE001 - reported in the dialog
        data["error"] = f"{type(e).__name__}: {e}"
    slot.update(t=now, data=data)
    return dict(data)


def _update_channel() -> str:
    return "beta" if load_config().get("beta_updates") is True else "stable"


@app.route("/api/update-check")
def api_update_check():
    """Compare APP_VERSION with the latest release of the configured feed."""
    enabled = load_config().get("update_check") is not False
    force = request.args.get("force") == "1"
    data = _update_info(_update_channel(), force=force)
    return jsonify(dict(data, enabled=enabled))


@app.route("/api/update/download", methods=["POST"])
def api_update_download():
    """Start downloading the published build in the background."""
    if load_config().get("update_check") is False:
        return jsonify({"ok": False, "error": "Automatic updates are turned off."}), 409
    info = _update_info(_update_channel(), force=bool((request.get_json(silent=True) or {}).get("force")))
    if info.get("error") and not info.get("latest"):
        return jsonify({"ok": False, "error": info["error"]}), 502
    if not info.get("update"):
        return jsonify({"ok": False, "error": "You are already on the newest version."}), 409
    if not info.get("installer"):
        return jsonify({"ok": False, "error": "This release has no Windows build attached.",
                        "url": info.get("url")}), 409
    job = _updater.JOB.start_download({"version": info.get("latest"), "installer": info["installer"]})
    if job.get("phase") == "installing":
        return jsonify({"ok": False, "error": "The update is already being installed."}), 409
    return jsonify({"ok": True, "job": job, "mode": info.get("install_mode")})


@app.route("/api/install-info")
def api_install_info():
    """Where this copy lives, and whether Windows lists it in Apps & features."""
    info = {"installed": False, "mode": _update_install_mode(), "version": APP_VERSION,
            "install_dir": None, "installed_version": None, "portable": not getattr(sys, "frozen", False)}
    try:
        from arena.setup import core as _setup_core
        info.update({
            "installed": _setup_core.is_installed(),
            "install_dir": str(_setup_core.install_dir()),
            "installed_version": _setup_core.installed_version(),
            "running_installed": _setup_core.running_installed_build(),
        })
    except Exception:
        pass
    return jsonify(info)


@app.route("/api/update/progress")
def api_update_progress():
    return jsonify({"ok": True, "job": _updater.JOB.snapshot(), "mode": _update_install_mode()})


@app.route("/api/update/cancel", methods=["POST"])
def api_update_cancel():
    _updater.JOB.cancel.set()
    return jsonify({"ok": True})


def _quit_for_update() -> None:
    """Close the app so the downloaded setup can replace its files."""
    try:
        from arena.engine import ENGINE
        ENGINE.close()
    except Exception:
        pass
    time.sleep(0.2)
    os._exit(0)


@app.route("/api/update/install", methods=["POST"])
def api_update_install():
    """Run the downloaded build and restart."""
    mode = _update_install_mode()
    job = _updater.JOB.snapshot()
    path = job.get("path")
    if mode != "helper":
        return jsonify({"ok": False, "error": "This copy cannot replace itself. Run the downloaded installer yourself."}), 409
    if job.get("phase") != "ready" or not path:
        return jsonify({"ok": False, "error": "The update has not finished downloading yet."}), 409
    target = Path(path)
    if not target.exists():
        return jsonify({"ok": False, "error": "The downloaded file disappeared. Please download it again."}), 409

    # Claim the job before anything starts, so a second click (or a stale dialog
    # in another tab) cannot begin a download of the file being installed.
    _updater.JOB.update(phase="installing", pct=100)
    name = (job.get("name") or "").lower()
    if "setup" in name or "installer" in name:
        started = _updater.launch_installer(target, os.getpid())
    else:
        started = _updater.spawn_apply_update(os.getpid(), target)
    if not started:
        return jsonify({"ok": False, "error": "Windows did not let the updater start."}), 500
    threading.Timer(1.4, _quit_for_update).start()
    return jsonify({"ok": True, "restarting": True, "version": job.get("version")})
