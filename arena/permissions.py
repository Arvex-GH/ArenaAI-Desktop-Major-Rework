"""
Permission modes for local actions, modelled on Claude Code.

default            ask before any change or command (reads are free)
acceptEdits        create and edit files freely; ask before deletes and commands
auto               work freely inside the project folder; ask for risky commands
                   and for anything outside it
plan               read-only: the model may look but never change anything
bypassPermissions  allow everything except catastrophic commands
"""

import re
import threading
import time
import uuid
from typing import Dict, List, Optional

MODES: Dict[str, str] = {
    "default": "Ask before edits",
    "acceptEdits": "Accept edits",
    "auto": "Auto",
    "plan": "Plan mode",
    "bypassPermissions": "Bypass permissions",
}

KIND = {"read_file": "read", "ls": "read", "search": "read",
        "write_file": "edit", "edit_file": "edit", "mkdir": "edit", "write_project": "edit",
        "delete": "delete", "run_cmd": "shell",
        # Stopping a background command the agent itself started cannot harm anything.
        "stop_cmd": "read"}

# Characters that only mean something to a real shell (chaining, pipes,
# redirection, substitution, variable expansion). A command that uses any of
# them runs through the system shell, so it is never auto-approved: the user
# confirms each one, even after "allow such actions in this session".
SHELL_OPERATORS = re.compile(r"[&|;<>`^!]|\$\(|\$\{|%[A-Za-z_][\w]*%|\$[A-Za-z_]")
# Inline-code flags (python -c, node -e, powershell -enc ...) get the same treatment.
INLINE_CODE = re.compile(r"(^|\s)(-c|-e|-r|--eval|--exec|-enc|-encodedcommand|-command|/c)(\s|$)", re.I)


def needs_shell(command: str) -> bool:
    """True when the command uses shell syntax (&&, |, >, $VAR ...)."""
    return bool(SHELL_OPERATORS.search(command or ""))


def needs_explicit_approval(command: str) -> bool:
    """Commands the user must confirm one by one, whatever the session grants."""
    return needs_shell(command) or bool(INLINE_CODE.search(command or ""))

SAFE_CMD = re.compile(
    r"^\s*(dir|ls|pwd|echo|type|cat|where|which|tree|git\s+(status|log|diff|show|branch)|"
    r"(python|py)(\s+-m)?\s+(pytest|unittest|--version)|node\s+(--version|-v)|"
    r"npm\s+(test|ls|-v|--version)|npm\s+run\s+(test|build|lint))\b", re.I)
SHELL_META = re.compile(r"[;&|<>`$]|\b(del|erase|rm|rmdir|rd|format|curl|wget|iwr|invoke-webrequest|"
                        r"powershell|pwsh|reg|shutdown|move|mv)\b", re.I)
CATASTROPHIC = re.compile(
    r"(\brm\s+-[a-z]*r[a-z]*\s+(/|~|\*|[a-z]:[\\/]?)(\s|$)|\bformat\s+[a-z]:|"
    r"\b(rd|rmdir)\s+/s\s+(/q\s+)?[a-z]:[\\/]?\s*$|\bdel\s+(/[a-z]\s+)*[a-z]:[\\/]\*|\bmkfs\b|"
    r"\bshutdown\b|:\(\)\s*\{|\bdiskpart\b|\bbcdedit\b|\bvssadmin\s+delete\b|\bcipher\s+/w)", re.I)


def normalize(mode: Optional[str]) -> str:
    return mode if mode in MODES else "default"


def decide(mode: str, tool: str, inside: bool = True, command: str = "") -> str:
    """Return 'allow', 'ask' or 'deny' for one action."""
    mode = normalize(mode)
    kind = KIND.get(tool, "shell")
    if kind == "shell" and CATASTROPHIC.search(command or ""):
        return "deny"
    # Bypass first: it answers for reads outside the folder too.
    if mode == "bypassPermissions":
        # Bypass means bypass. It used to keep asking for three kinds of work —
        # shell syntax (``&&``, ``|``, ``>``, ``%VAR%``), inline code (``-c``,
        # ``-e``) and any path outside the folder — which is most of what an
        # agent actually does, so "work fully autonomously" still stopped every
        # few steps to ask. The catastrophic list above is the only floor left,
        # and that is a refusal, not a question.
        return "allow"
    if kind == "read":
        return "allow" if inside else "ask"
    if mode == "plan":
        return "deny"
    if not inside:
        return "ask"
    if kind == "edit":
        return "allow" if mode in ("acceptEdits", "auto") else "ask"
    if kind == "delete":
        return "allow" if mode == "auto" else "ask"
    if mode == "auto" and SAFE_CMD.match(command or "") and not SHELL_META.search(command or ""):
        return "allow"
    return "ask"


class PermissionBroker:
    """Holds approval requests until the user answers them in the UI."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending: Dict[str, dict] = {}
        self._always: set = set()

    def check(self, mode: str, tool: str, inside: bool = True, command: str = "") -> str:
        verdict = decide(mode, tool, inside, command)
        kind = KIND.get(tool, "shell")
        if verdict == "ask" and inside and kind in self._always:
            if kind == "shell" and needs_explicit_approval(command):
                return "ask"
            return "allow"
        return verdict

    def ask(self, info: dict) -> str:
        pid = uuid.uuid4().hex[:12]
        with self._lock:
            self._pending[pid] = {"event": threading.Event(), "decision": None,
                                  "info": dict(info, id=pid, created=time.time())}
        return pid

    def resolve(self, pid: str, decision: str) -> bool:
        if decision not in ("allow", "always", "deny"):
            return False
        with self._lock:
            item = self._pending.get(pid)
            if not item or item["decision"]:
                return False
            item["decision"] = decision
            if decision == "always":
                self._always.add(KIND.get(item["info"].get("tool"), "shell"))
            item["event"].set()
        return True

    def wait(self, pid: str, timeout: Optional[float] = None) -> Optional[str]:
        """'allow' or 'deny' once answered; None if still waiting after timeout."""
        item = self._pending.get(pid)
        if not item:
            return "deny"
        if not item["event"].wait(timeout):
            return None
        with self._lock:
            self._pending.pop(pid, None)
        return "deny" if item["decision"] == "deny" else "allow"

    def pending(self) -> List[dict]:
        with self._lock:
            return [p["info"] for p in self._pending.values() if not p["decision"]]

    def cancel_all(self) -> int:
        ids = [info["id"] for info in self.pending()]
        for pid in ids:
            self.resolve(pid, "deny")
        return len(ids)

    def reset_always(self) -> None:
        self._always.clear()
