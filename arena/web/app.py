"""
Flask web backend for Arena.ai desktop client.
Serves the web UI and proxies chat requests to arena.ai.

The code lives in arena/web/server/, split by topic into files of at most
1,500 lines. The parts run in order inside this module's namespace, so
together they are one module: callers and tests keep using
arena.web.app.<name>, patching arena.web.app.<name> reaches every part, and
module globals such as _ui_mode stay shared. Add new code to the part that
fits its topic; a part that grows past 1,500 lines gets split again.
"""

import importlib.util as _importlib_util

PARTS = (
    "core",
    "accounts",
    "chats",
    "library",
    "stream",
    "turns",
    "arena_agent",
    "local_agent",
    "send",
    "workspace",
    "connectors",
)


def _load_parts():
    for part in PARTS:
        spec = _importlib_util.find_spec(f"arena.web.server.{part}")
        code = spec.loader.get_code(spec.name) if spec and spec.loader else None
        if code is None:
            raise ImportError(f"arena.web.server.{part} is missing from this build")
        exec(code, globals())  # noqa: S102 - our own source, one shared namespace


_load_parts()
