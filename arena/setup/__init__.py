"""Arena Desktop setup, uninstall and self-update.

The installer is not a separate program: the app binary starts in setup mode
(``--setup``) and shows its own branded window.  That keeps one build artifact
in circulation and means an update can simply run the new binary in setup mode.
"""

from arena.setup.core import (  # noqa: F401
    APP_ID,
    APP_NAME,
    PUBLISHER,
    apply_update,
    install_dir,
    installed_version,
    is_installed,
    is_supported,
    run_install,
    run_uninstall,
    shortcut_paths,
    uninstall_key,
)
