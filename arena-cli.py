#!/usr/bin/env python
"""
Arena Desktop — an unofficial desktop client for arena.ai.

Modes:
  (no flags)            open the app
  --app                 open the app even from a file named Setup
  --cli                 the old terminal interface
  --setup [--silent]    install this build for the current user
                        (a file named ...-Setup-<version>.exe installs on
                        double-click without this flag)
  --setup --update      install over an existing copy (used by the updater)
  --uninstall [--silent] [--wipe-data]
  --apply-update        internal helper: wait for a pid, swap the file, restart
"""

import sys
import os
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure package root is in sys.path
root_dir = Path(__file__).resolve().parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def _flag_value(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    from arena.version import APP_VERSION

    if "--version" in sys.argv:
        print(f"Arena Desktop {APP_VERSION}")
        return

    if "--apply-update" in sys.argv:
        from arena import updater
        pid = int(_flag_value("--pid", "0") or 0)
        target = _flag_value("--file", "")
        if not target:
            sys.exit(2)
        sys.exit(updater.run_apply_update(pid, Path(target)))

    if "--uninstall" in sys.argv:
        from arena.setup.ui import run_setup
        silent = "--silent" in sys.argv
        if "--wipe-data" in sys.argv:
            from arena.setup import core
            core.run_uninstall(remove_user_data=True)
            return
        sys.exit(run_setup("uninstall", APP_VERSION, silent=silent))

    from arena.setup import core as setup_core

    setup_mode = setup_core.setup_mode_from_argv()
    if setup_mode:
        from arena.setup.ui import run_setup
        pid = int(_flag_value("--pid", "0") or 0)
        sys.exit(run_setup(setup_mode, APP_VERSION, silent="--silent" in sys.argv, wait_pid=pid))

    if "--cli" in sys.argv:
        from arena.cli import main as cli_main
        cli_main()
    else:
        from arena.desktop import main as desktop_main
        desktop_main()


if __name__ == "__main__":
    main()
