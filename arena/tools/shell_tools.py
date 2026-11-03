"""
Shell execution tool for running system commands.

Execution itself (idle watchdog, interactive-command refusal, output
trimming) lives in arena.agent.run_shell_command so the CLI and the web
agent loop share exactly one implementation.
"""

import os
from typing import Dict, Any

from arena.tools.base import BaseTool


class RunCmdTool(BaseTool):
    name = "run_cmd"
    description = "Execute a command in the local shell."

    def execute(self, params: Dict[str, Any]) -> str:
        cmd = (params.get("cmd") or "").strip()
        if not cmd:
            return "Error: No command specified."
        os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        try:
            from arena.agent import run_shell_command
            return run_shell_command(cmd, self.cwd)
        except Exception as e:  # noqa: BLE001 - tool boundary reports errors as text
            return f"Error executing command: {e}"
