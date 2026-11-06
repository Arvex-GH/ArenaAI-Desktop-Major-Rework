"""
Local tools for chats: models work on a folder on this computer through tools the app runs.

The model asks for tools with XML tags in its reply. The app runs each one
under the current permission mode and sends the results back as the next
message; arena.ai keeps the conversation, so the guide is sent only once.
In Battle and Side by Side both models get the tools, and each one writes to
its own copy of the folder (option-a, option-b) so their work never collides.
"""

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

MAX_ROUNDS = 8
OUTPUT_LIMIT = 6000
# Outputs longer than this are saved to a file in the workspace and only a
# preview travels back to the model (it can read_file the rest on demand),
# so long tool chats stop drowning the Arena-side context in raw output.
INLINE_LIMIT = 1500
PREVIEW_LIMIT = 600
ARTIFACTS_DIR = ".arena-artifacts"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_TOOL_RE = re.compile(r"<tool\s+name\s*=\s*[\"']?([\w-]+)[\"']?\s*>(.*?)</tool\s*>", re.S | re.I)
_EMPTY_FENCE = re.compile(r"```[\w-]*[ \t]*\n?\s*```")
_PARAM_RE = re.compile(r"<([A-Za-z_][\w-]*)>(.*?)</\1\s*>", re.S | re.I)
# Some models ignore the guide's tag form and answer with Anthropic-style
# invokes instead; accept those too so their work is not lost.
_INVOKE_RE = re.compile(r"<invoke\s+name\s*=\s*[\"']?([\w-]+)[\"']?\s*>(.*?)</invoke\s*>", re.S | re.I)
_INVOKE_PARAM_RE = re.compile(r"<parameter\s+name\s*=\s*[\"']?([\w-]+)[\"']?\s*>(.*?)</parameter\s*>", re.S | re.I)
_FUNCTION_CALLS_RE = re.compile(r"<function_calls>.*?</function_calls\s*>", re.S | re.I)
_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next", "dist", "build"}
_WRITES = {"write_file", "edit_file", "mkdir", "delete"}
LANE_DIRS = {"a": "option-a", "b": "option-b"}

GUIDE = """[Arena Desktop] The user is chatting through Arena Desktop, an app on their own computer. It connects you to a folder on that computer with working tools: you can list, read, search, create, edit and delete files there and run commands. Do not say you lack file access or tell the user to make the files themselves; use the tools. Your own built-in tools (a code sandbox, file or web tools) run on a remote server and cannot reach this computer, so for files here use only the tags below.
Folder: {root}
Permission mode: {mode_label}. {mode_note}{lane_note}

To use a tool, write its tag as plain text in your reply, not inside a code block. The app runs the tags in order and sends you the results in the next message.
<tool name="ls"><path>.</path></tool>
<tool name="read_file"><path>src/main.py</path></tool> (optional <start_line>40</start_line><end_line>120</end_line>)
<tool name="search"><pattern>def main</pattern><path>.</path></tool>
<tool name="write_file"><path>index.html</path><content>
FULL FILE CONTENT
</content></tool>
<tool name="edit_file"><path>index.html</path><old_text>exact text to replace</old_text><new_text>replacement</new_text></tool>
<tool name="mkdir"><path>assets/img</path></tool>
<tool name="delete"><path>old_notes.txt</path></tool>
<tool name="run_cmd"><cmd>python -m pytest -q</cmd></tool>
<tool name="run_cmd"><cmd>npm install && npm run build</cmd></tool> (commands run on this computer in the folder: {shell_name}; &&, | and > work but each such command needs the user's OK)
<tool name="run_cmd"><cmd>npm run dev</cmd><background>true</background></tool> (servers and watchers: starts it, returns its first output and URL, keeps it running)
<tool name="stop_cmd"><id>bg1</id></tool> (stop a background command)
<tool name="ask_user"><question>Which database should I use?</question><option1>SQLite</option1><option2>PostgreSQL</option2></tool>
<tool name="propose_plan"><plan>1. Inspect the project layout
2. Refactor the parser
3. Run the tests</plan></tool>
<tool name="set_todos"><todos>[x] Inspect the project layout
[~] Refactor the parser
[ ] Run the tests</todos></tool>
<tool name="read_files"><paths>src/main.py
src/util.py
README.md</paths></tool>
{extra_tools}{delegate}
Rules: paths are relative to the folder. Read a file before editing it. write_file takes the whole file. When the task is done, reply with a short summary and no tool tags. If the message does not need files, just answer it. Use ask_user only for choices that are genuinely the user's (preferences, ambiguous requirements): one clear question with 2-4 short options \u2014 the user can also type their own answer, and it comes back to you as the tool result. Never ask what you can find out from the files, and do not ask twice in a row. For work with several steps, keep a checklist with set_todos: set it before you start and update it as steps finish ([x] done, [~] in progress, [ ] pending), so the user sees live progress. Before substantial or risky changes, present the intended steps with propose_plan and wait for the user's decision; if they ask for revisions, present the updated plan.
Keep the context lean: batch independent tool calls in one reply instead of one call per round. Every extra round re-sends the whole context to the model and burns the user's daily Arena balance, so fewer, fuller rounds finish the same work for less. To read several files at once, use read_files with one path per line inside <paths>; re-reading a file that has not changed returns a short note instead of repeating the content. read_file accepts <start_line> and <end_line> — read only the part you need, and do not re-read files you just wrote. Very long tool outputs are saved under .arena-artifacts/ and you only see a preview; read_file that path if you need the full text.

Persistent memory: MEMORY.md in the workspace root is shared across chats about this project, and its content may be included above as established context. When you learn durable facts worth keeping beyond this chat \u2014 project conventions, decisions, user preferences, the current state of the work \u2014 update MEMORY.md briefly with write_file/edit_file (short bullet points; point to files instead of copying code). Do not store secrets, one-off details, or anything obvious from the code itself. If the user says \u201cremember this\u201d, that goes to MEMORY.md. If the project root has AGENTS.md, those are the maintainer's standing instructions; they may be included above as context - follow them, and do not copy them into MEMORY.md.

User message:
"""

LANE_NOTE = ("\nTwo assistants answer this chat side by side. Each one's file changes go to its own copy of the folder "
             "(option-a or option-b), so work as if you were alone. Results come back for both replies; use the ones "
             "for the tool calls you wrote.")

MODE_NOTES = {
    "plan": "Plan mode is on: only ls, read_file and search will run. Do not change anything; "
            "finish with a numbered plan and present it with the propose_plan tool so the user can approve it in one click.",
    "default": "The user approves each change and command.",
    "acceptEdits": "File edits run without asking; deletes and commands need approval.",
    "auto": "Work inside the project folder runs without asking; risky commands need approval.",
}


READ_ONLY_TOOLS = {"ls", "read_file", "read_files", "search", "ask_user", "propose_plan", "set_todos"}


def delegate_guide(models) -> str:
    """Guide section describing sub-agent delegation (shown to parents only)."""
    lines = [
        "",
        "Sub-agents: for a self-contained sub-task \u2014 exploring part of the project, "
        "researching how something works, checking several places \u2014 you may delegate it "
        "instead of doing it here. The sub-agent works in its own chat with the same folder "
        "tools and only its final summary returns to you, so your context stays clean. Write "
        "the task so it stands alone: the sub-agent does not see this conversation. "
        "<mode>research</mode> (the default) is read-only; <mode>work</mode> may also edit "
        "files and run commands. Delegates run one at a time, at most 3 per turn. You may pick "
        "the sub-agent's model when it matters (a fast model for search, the strongest for hard "
        "reasoning); omit <model> to auto-assign.",
        '<tool name="delegate_task"><task>Find everywhere the config file is parsed; report '
        'file:line for each place with a one-line note.</task><mode>research</mode></tool>',
        "Each delegate result starts with [sub-agent chat: <id>]. Once you have used a "
        "sub-agent's summary and no longer need its chat, delete it so the user's chat "
        "list stays clean: <tool name=\"delete_subagent\"><child_id>the id</child_id></tool>. "
        "You can only delete chats you delegated in this conversation.",
    ]
    if models:
        lines.append("Models you may name in <model> (use the id in brackets): "
                     + ", ".join(f"{name} [{mid}]" for mid, name in models))
    return "\n".join(lines) + "\n"


def guide(root, mode_label: str, mode_note: str, dual: bool = False, extra_tools: str = "",
          delegate: str = "") -> str:
    """The tool guide that goes in front of the first message of a conversation."""
    return GUIDE.format(root=root, mode_label=mode_label, mode_note=mode_note,
                        shell_name="Windows cmd.exe" if os_name_is_windows() else "/bin/sh",
                        lane_note=LANE_NOTE if dual else "", extra_tools=extra_tools or "",
                        delegate=delegate or "")


def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    calls = []
    found = []
    for m in _TOOL_RE.finditer(text or ""):
        params: Dict[str, Any] = {}
        for p in _PARAM_RE.finditer(m.group(2)):
            # Keep MCP argument names case-sensitive, while retaining the
            # lower-case aliases the built-in file tools have always used.
            params[p.group(1)] = p.group(2)
            params.setdefault(p.group(1).lower(), p.group(2))
        found.append((m.start(), {"name": m.group(1).lower(), "params": params}))
    for m in _INVOKE_RE.finditer(text or ""):
        params = {}
        for p in _INVOKE_PARAM_RE.finditer(m.group(2)):
            params[p.group(1)] = p.group(2)
            params.setdefault(p.group(1).lower(), p.group(2))
        found.append((m.start(), {"name": m.group(1).lower(), "params": params}))
    found.sort(key=lambda item: item[0])
    calls.extend(call for _pos, call in found)
    return calls


def coerce_mcp_arguments(call: Dict[str, Any], schema: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Turn the XML form of an MCP call into JSON arguments.

    The preferred form is ``<arguments>{...}</arguments>``.  Plain child
    elements are also accepted and coerced from strings using the JSON Schema
    advertised by the MCP server.
    """
    params = dict(call.get("params") or {})
    raw = None
    for key in ("arguments", "args", "json"):
        if isinstance(params.get(key), str) and params[key].strip():
            raw = params.pop(key).strip()
            break
    if raw is not None:
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        try:
            value = json.loads(raw)
        except ValueError as e:
            raise ValueError(f"MCP arguments are not valid JSON: {e}") from e
        if not isinstance(value, dict):
            raise ValueError("MCP arguments must be a JSON object")
        return _coerce_mcp_value(value, schema or {})

    properties = {}
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        properties = schema["properties"]
    out: Dict[str, Any] = {}
    seen_lower = set()
    for key, value in params.items():
        # parse_tool_calls adds lower-case aliases for built-in tools; do not
        # emit the same MCP property twice under two spellings.
        if key.lower() in seen_lower and key not in properties:
            continue
        seen_lower.add(key.lower())
        prop = properties.get(key) if isinstance(properties.get(key), dict) else {}
        out[key] = _coerce_mcp_value(value, prop)
    return out


def _coerce_mcp_value(value: Any, prop: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        properties = prop.get("properties") if isinstance(prop, dict) else None
        if not isinstance(properties, dict):
            return value
        return {
            key: _coerce_mcp_value(item, properties.get(key) if isinstance(properties.get(key), dict) else {})
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = prop.get("items") if isinstance(prop, dict) else None
        if not isinstance(items, dict):
            return value
        return [_coerce_mcp_value(item, items) for item in value]
    if not isinstance(value, str):
        return value
    kind = prop.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), None)
    text = value.strip()
    try:
        if kind == "integer":
            return int(float(text)) if text else 0
        if kind == "number":
            number = float(text)
            return int(number) if number.is_integer() else number
        if kind == "boolean":
            return text.lower() in ("1", "true", "yes", "on")
        if kind in ("array", "object"):
            parsed = json.loads(text)
            return _coerce_mcp_value(parsed, prop)
    except (ValueError, TypeError):
        return value
    return value


def strip_tool_calls(text: str) -> str:
    cleaned = _FUNCTION_CALLS_RE.sub("", text or "")
    cleaned = _INVOKE_RE.sub("", cleaned)
    return _EMPTY_FENCE.sub("", _TOOL_RE.sub("", cleaned))


def _body(text: str) -> str:
    """Content between tags, minus the newline that follows the opening tag."""
    if text.startswith("\r\n"):
        return text[2:]
    if text.startswith("\n"):
        return text[1:]
    return text


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        return f.read()


def _diff(old: str, new: str, name: str, limit: int = 80) -> str:
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", lineterm="", n=2))
    if len(lines) > limit:
        lines = lines[:limit] + [f"... {len(lines) - limit} more diff lines"]
    return "\n".join(lines)


def trim_output(text: str) -> str:
    """Shrink a tool output for the trip back to the model: no ANSI codes,
    repeated lines collapsed, head+tail kept when it is still too long."""
    cleaned = _ANSI_RE.sub("", str(text or ""))
    out_lines: List[str] = []
    run_line, run_count = None, 0
    for line in cleaned.splitlines():
        if line == run_line:
            run_count += 1
            continue
        if run_count > 2:
            out_lines.append(f"... (same line x{run_count})")
        elif run_count == 2:
            out_lines.append(run_line or "")
        run_line, run_count = line, 1
        out_lines.append(line)
    if run_count > 2:
        out_lines.append(f"... (same line x{run_count})")
    elif run_count == 2 and run_line is not None:
        out_lines.append(run_line)
    cleaned = "\n".join(out_lines)
    if len(cleaned) > OUTPUT_LIMIT:
        head = cleaned[: OUTPUT_LIMIT * 3 // 5]
        tail = cleaned[-(OUTPUT_LIMIT * 2 // 5):]
        cleaned = head + f"\n... [{len(cleaned) - len(head) - len(tail)} chars omitted] ...\n" + tail
    return cleaned


def _offload(output: str, call, artifact_dir, artifact_rel: str) -> str:
    """Save a long output under .arena-artifacts/ and return a preview +
    pointer instead of the whole text. Falls back to trimming on any error."""
    try:
        digest = hashlib.sha1(output.encode("utf-8", "replace")).hexdigest()[:8]
        name = f"{call.get('name') or 'tool'}-{digest}.txt"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / name).write_text(output[:200_000], encoding="utf-8")
        return (output[:PREVIEW_LIMIT]
                + f"\n... [full output ({len(output)} chars) saved to {artifact_rel}/{name}"
                  " - use read_file on it if you need the rest]")
    except OSError:
        return trim_output(output)


SHELL_IDLE_LIMIT = 30     # seconds without output -> kill (waiting on input, pager, hang)
SHELL_IDLE_LIMIT_LONG = 180  # package managers and builds can stay silent for minutes
SHELL_TOTAL_LIMIT = 600   # hard cap for a single command
BACKGROUND_WARMUP = 8     # seconds of output a background command reports before returning
_LONG_RUNNERS = re.compile(
    r"(^|[\s&|;(])(npm|npx|pnpm|yarn|bun|pip|pip3|uv|poetry|cargo|go|dotnet|mvn|gradle|"
    r"gradlew|composer|bundle|docker|flutter|tsc|vite|next|webpack)(\.cmd|\.exe)?(\s|$)", re.I)
_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?[^\s\"'<>]*", re.I)

_INTERACTIVE_FIRST = {
    "vim", "vi", "nvim", "nano", "emacs", "less", "more", "most", "top",
    "htop", "ssh", "telnet", "ftp", "sftp", "mysql", "psql", "mongo",
    "redis-cli", "watch", "edit", "notepad",
}
_BARE_SHELLS = {"cmd", "powershell", "pwsh", "bash", "sh", "zsh", "fish"}
_BARE_INTERPRETERS = {"python", "python3", "py", "node", "irb", "ruby", "php", "lua", "perl"}
_DANGEROUS_FLAGS = re.compile(r"^-[cer]$|^--eval$|^--exec$|^-enc$|^-encodedcommand$|^/c$", re.I)
_DANGEROUS_CMDS = frozenset({"mshta", "wscript", "cscript", "rundll32"})
_SAFE_BUILTINS = frozenset({"echo", "dir", "type", "ver", "cls", "set", "cd", "date", "time"})


def _program_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _interactive_refusal(cmd: str) -> str:
    """Explain (for the model) why a command cannot run here, or "" if it can."""
    tokens = re.split(r"[\s;|&]+", cmd.strip())
    tokens = [t for t in tokens if t and "=" not in t.split("/")[-1].split("\\")[-1]]
    if not tokens:
        return ""
    first = _program_name(tokens[0])
    rest = [t.lower() for t in tokens[1:]]
    why = ""
    if first in _INTERACTIVE_FIRST:
        why = f"'{first}' is an interactive program"
    elif first == "tail" and ("-f" in rest or "--follow" in rest):
        why = "'tail -f' never exits"
    elif first in _BARE_SHELLS and not any(t in ("/c", "-c", "-command", "-encodedcommand") for t in rest):
        why = f"a bare '{first}' starts an interactive shell"
    elif first in _BARE_INTERPRETERS and (not rest or "-i" in rest):
        why = f"a bare '{first}' starts a REPL"
    if not why:
        return ""
    return (f"Error: not run: {why}. run_cmd is non-interactive (stdin is closed, and a "
            "command with no output for 30 seconds is killed). Use a one-shot form instead, "
            "e.g. a script file, 'python script.py', 'git log -5', or redirect input from a file. "
            "For a dev server or watcher add <background>true</background>.")


def _shell_env() -> Dict[str, str]:
    """Environment for agent commands: no colours, pagers or prompts."""
    import os as _os
    env = dict(_os.environ)
    env.update({
        "NO_COLOR": "1", "FORCE_COLOR": "0", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "PAGER": "cat",
        "npm_config_yes": "true", "npm_config_fund": "false", "npm_config_audit": "false",
        "npm_config_update_notifier": "false",
    })
    return env


def _prepare(cmd: str):
    """Validate one command; return (popen_args, use_shell) or an error string.

    Plain commands run without a shell (argv list). Commands that use shell
    syntax (&&, |, >, $VAR ...) run through the system shell - cmd.exe on
    Windows, /bin/sh elsewhere. The permission layer never auto-approves
    those (permissions.needs_explicit_approval), so the user confirmed each
    one before it gets here.
    """
    from arena import permissions as _perm
    import shlex
    if _perm.CATASTROPHIC.search(cmd):
        return "Error: this command is never allowed."
    if _perm.needs_shell(cmd):
        return cmd, True
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return f"Error: could not parse the command: {e}"
    if not argv:
        return "Error: cmd is empty"
    if _program_name(argv[0]) in _DANGEROUS_CMDS:
        return f"Error: {argv[0]} is blocked for safety."
    if any(_DANGEROUS_FLAGS.match(a) for a in argv[1:]):
        return ("Error: inline code execution flags (-c, -e, -r, --eval, -enc, /c) are blocked for safety; "
                "write a script file and run it instead.")
    if argv[0].lower() in _SAFE_BUILTINS and not shutil.which(argv[0]):
        return ["cmd.exe", "/d", "/c"] + argv, False
    resolved = shutil.which(argv[0])
    if resolved:
        argv[0] = resolved
    return argv, False


def _kill_tree(proc) -> None:
    try:
        if os_name_is_windows():
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _spawn(args, use_shell: bool, cwd, stdout, stderr):
    kwargs = {}
    if os_name_is_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(args, shell=use_shell, cwd=str(cwd), stdin=subprocess.DEVNULL,
                            stdout=stdout, stderr=stderr, env=_shell_env(), **kwargs)


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return data.decode(locale.getpreferredencoding(False) or "utf-8", "replace")


class _Background:
    """Commands the agent left running (dev servers, watchers)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._procs: Dict[str, dict] = {}
        self._n = 0

    def start(self, cmd: str, cwd) -> str:
        prepared = _prepare(cmd)
        if isinstance(prepared, str):
            return prepared
        args, use_shell = prepared
        cwd = Path(cwd)
        log_dir = cwd / ARTIFACTS_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._n += 1
            bid = f"bg{self._n}"
        log_path = log_dir / f"{bid}.log"
        log = open(log_path, "wb")
        try:
            proc = _spawn(args, use_shell, cwd, log, subprocess.STDOUT)
        except OSError as e:
            log.close()
            return f"Error: could not start: {e}"
        with self._lock:
            self._procs[bid] = {"proc": proc, "cmd": cmd, "log": log_path, "fh": log,
                                "started": time.time()}
        deadline = time.time() + BACKGROUND_WARMUP
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.25)
            if _URL_RE.search(self._tail(log_path)):
                time.sleep(0.5)
                break
        text = self._tail(log_path)
        rel = f"{ARTIFACTS_DIR}/{bid}.log"
        if proc.poll() is not None:
            self._forget(bid)
            return (f"exit code {proc.returncode} (the background command already finished)\n"
                    + (text[-OUTPUT_LIMIT:] or "(no output)"))
        urls = sorted(set(_URL_RE.findall(text)))
        head = [f"Started in the background: id {bid}, pid {proc.pid}. It keeps running after this reply.",
                f"Full log: {rel} (read_file it to check progress). Stop it with "
                f'<tool name="stop_cmd"><id>{bid}</id></tool>.']
        if urls:
            head.append("Serving at: " + ", ".join(urls[:3]))
        return "\n".join(head) + ("\nOutput so far:\n" + text[-2000:] if text else "\n(no output yet)")

    @staticmethod
    def _tail(path: Path, size: int = 8000) -> str:
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                end = fh.tell()
                fh.seek(max(0, end - size))
                return _ANSI_RE.sub("", _decode(fh.read())).strip()
        except OSError:
            return ""

    def _forget(self, bid: str):
        with self._lock:
            item = self._procs.pop(bid, None)
        if item:
            try:
                item["fh"].close()
            except Exception:
                pass
        return item

    def stop(self, bid: str) -> str:
        bid = (bid or "").strip()
        with self._lock:
            item = self._procs.get(bid)
        if not item:
            running = ", ".join(self.ids()) or "none"
            return f"No background command with id '{bid}'. Running: {running}."
        _kill_tree(item["proc"])
        try:
            item["proc"].wait(timeout=10)
        except Exception:
            pass
        self._forget(bid)
        return f"Stopped {bid} ({item['cmd']})."

    def ids(self) -> List[str]:
        with self._lock:
            done = [b for b, it in self._procs.items() if it["proc"].poll() is not None]
        for b in done:
            self._forget(b)
        with self._lock:
            return list(self._procs)

    def list(self) -> List[dict]:
        self.ids()
        with self._lock:
            return [{"id": b, "cmd": it["cmd"], "pid": it["proc"].pid,
                     "log": str(it["log"]), "started": it["started"]} for b, it in self._procs.items()]

    def stop_all(self) -> int:
        ids = self.ids()
        for b in ids:
            self.stop(b)
        return len(ids)


BACKGROUND = _Background()
try:
    import atexit as _atexit
    _atexit.register(BACKGROUND.stop_all)
except Exception:
    pass


def run_shell_command(cmd: str, cwd, on_output=None, should_stop=None, background: bool = False) -> str:
    """Run one shell command with an idle watchdog; returns formatted output.

    Output streams through pump threads so silence can be detected: a
    command that produces nothing for the idle limit is killed (it is almost
    certainly waiting for input or hung), which previously blocked the whole
    agent loop until the user pressed Stop. ``on_output`` receives each new
    chunk of text as it arrives (live display); ``should_stop`` is polled
    and kills the command when it returns True. ``background`` starts the
    command, reports its first seconds of output and leaves it running.
    """
    cmd = (cmd or "").strip()
    if not cmd:
        raise ValueError("cmd is empty")
    if background:
        return BACKGROUND.start(cmd, cwd)
    refusal = _interactive_refusal(cmd)
    if refusal:
        return refusal
    prepared = _prepare(cmd)
    if isinstance(prepared, str):
        return prepared
    args, use_shell = prepared
    idle_limit = SHELL_IDLE_LIMIT_LONG if _LONG_RUNNERS.search(cmd) else SHELL_IDLE_LIMIT
    try:
        proc = _spawn(args, use_shell, cwd, subprocess.PIPE, subprocess.PIPE)
    except OSError as e:
        return f"Error: could not start the command: {e}"
    out_chunks: List[str] = []
    err_chunks: List[str] = []
    state = {"last": time.time()}

    def pump(stream, sink):
        try:
            reader = getattr(stream, "read1", stream.read)
            while True:
                data = reader(4096)
                if not data:
                    break
                text = _decode(data)
                sink.append(text)
                state["last"] = time.time()
                if on_output is not None:
                    try:
                        on_output(_ANSI_RE.sub("", text))
                    except Exception:
                        pass
        except Exception:
            pass

    threads = [threading.Thread(target=pump, args=(st, sink), daemon=True)
               for st, sink in ((proc.stdout, out_chunks), (proc.stderr, err_chunks)) if st is not None]
    for t in threads:
        t.start()
    started = time.time()
    killed = ""
    while proc.poll() is None:
        now = time.time()
        if should_stop is not None and should_stop():
            killed = "stopped by the user"
            break
        if now - state["last"] > idle_limit:
            killed = f"killed after {idle_limit}s without output (waiting for input or hung)"
            break
        if now - started > SHELL_TOTAL_LIMIT:
            killed = (f"killed after the {SHELL_TOTAL_LIMIT}s limit (for a server or watcher "
                      "add <background>true</background>)")
            break
        time.sleep(0.15)
    if killed:
        _kill_tree(proc)
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    for t in threads:
        t.join(timeout=2)
    stdout = _ANSI_RE.sub("", "".join(out_chunks)).replace("\r\n", "\n").strip()
    stderr = _ANSI_RE.sub("", "".join(err_chunks)).replace("\r\n", "\n").strip()
    if killed:
        merged = (stdout + ("\n" if stdout and stderr else "") + stderr)[-OUTPUT_LIMIT:]
        return (f"{killed}\n{merged}").strip() or killed
    parts = [f"exit code {proc.returncode}"]
    if stdout:
        parts.append(stdout[-OUTPUT_LIMIT:])
    if stderr:
        parts.append(f"[STDERR]\n{stderr[-1500:]}")
    if proc.returncode:
        parts.append(f"[Process exited with code {proc.returncode}]")
    elif not stdout and not stderr:
        parts.append("(command finished with no output)")
    return "\n".join(parts)


_COMPONENT_OF = {"node": "node", "npm": "node", "npx": "node", "pnpm": "node", "yarn": "node",
                 "python": "python", "python3": "python", "py": "python", "pip": "python", "pip3": "python"}
_NOT_FOUND = re.compile(r"is not recognized as an internal or external command|command not found|"
                        r"could not start the command|No such file or directory|cannot find the file", re.I)


def missing_component(cmd: str, output: str):
    """The app component (node/python) a failed command was missing, if any."""
    if not _NOT_FOUND.search(output or ""):
        return None
    for token in re.split(r"[\s&|;()]+", cmd or ""):
        comp = _COMPONENT_OF.get(_program_name(token)) if token else None
        if comp and not shutil.which(token):
            return comp
    return None


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _command_failed(output: str) -> bool:
    """A run_cmd result that the UI should mark as failed."""
    head = (output or "").lstrip()[:200]
    if head.startswith(("Error:", "killed after", "stopped by the user")):
        return True
    m = re.match(r"exit code (-?\d+)", head)
    return bool(m and m.group(1) != "0")


def os_name_is_windows() -> bool:
    import os as _os
    return _os.name == "nt"


def _result_lines(outcomes, artifact_dir=None, artifact_rel: str = ARTIFACTS_DIR) -> List[str]:
    lines = []
    for i, (call, status, output) in enumerate(outcomes, 1):
        target = call["params"].get("path") or call["params"].get("cmd") or ""
        lines.append(f"[{i}] {call['name']} {target}: {status}")
        if output:
            cleaned = _ANSI_RE.sub("", str(output))
            if (artifact_dir is not None and call.get("name") not in ("read_file", "read_files")
                    and len(cleaned) > INLINE_LIMIT):
                lines.append(_offload(cleaned, call, artifact_dir, artifact_rel))
            else:
                lines.append(trim_output(cleaned))
    return lines


_CONTINUE = "Continue the task. When it is complete, reply with a short summary and no tool tags."


def format_results(outcomes, artifact_dir=None, artifact_rel: str = ARTIFACTS_DIR) -> str:
    return "\n".join(["Tool results:"] + _result_lines(outcomes, artifact_dir, artifact_rel) + [_CONTINUE])


def format_lane_results(per_lane, artifact_dirs=None, artifact_rel: str = ARTIFACTS_DIR) -> str:
    """Results for a two-model chat: one block per reply, each listing the calls it answers."""
    parts = ["Tool results for both replies. Use only the block for the tool calls you wrote."]
    for lane, outcomes in per_lane.items():
        lane_dir = (artifact_dirs or {}).get(lane)
        parts.append(f"Reply {lane.upper()}:\n" + "\n".join(_result_lines(outcomes, lane_dir, artifact_rel)) if outcomes
                     else f"Reply {lane.upper()}: no tool calls.")
    return "\n\n".join(parts + [_CONTINUE])


class LocalTools:
    """Runs tools against one project folder. With `out` (two-model chats), changes go to that copy
    of the folder and reads prefer it, so the original files stay as they were."""

    def __init__(self, root, out=None):
        self.root = Path(root).expanduser().resolve()
        self.out = Path(out).expanduser().resolve() if out else None

    _SENSITIVE = frozenset({".ssh", ".aws", ".gnupg", ".gnupg2", ".config",
                            "appdata", ".azure", ".kube", ".docker", ".npmrc",
                            ".env", ".git"})

    def resolve(self, rel: str, write: bool = False) -> Tuple[Path, bool]:
        rel = (rel or ".").strip().strip("\"'") or "."
        if ":" in rel.split("/")[-1].split("\\")[-1] and not (len(rel) >= 2 and rel[1] == ":"):
            raise PermissionError("NTFS alternate data streams not allowed")
        p = Path(rel).expanduser()
        if p.is_symlink() or (self.root / p).is_symlink():
            real = (p if p.is_absolute() else self.root / p).resolve()
            if real != self.root and self.root not in real.parents:
                raise PermissionError("Access denied: symlink target outside workspace")
        target = (p if p.is_absolute() else self.root / p).resolve()
        inside = target == self.root or self.root in target.parents
        if inside and target != self.root:
            parts_lower = {part.lower() for part in target.relative_to(self.root).parts}
            if parts_lower & self._SENSITIVE:
                raise PermissionError(f"Access denied: sensitive directory")
        own = self._own(target) if inside else None
        if own is not None and (write or own.is_file()):
            return own, True
        return target, inside

    def _own(self, target: Path):
        """This model's copy of a path in a two-model chat; None in one-model chats."""
        if self.out is None or target == self.out or self.out in target.parents:
            return None
        try:
            return self.out / target.relative_to(self.root)
        except ValueError:
            return None

    def _walk(self, top: Path):
        """Files under a folder and this model's copy of it, minus build output and the other model's copy."""
        for d in (top, self._own(top)):
            if d is None or not d.is_dir():
                continue
            for p in d.rglob("*"):
                parts = p.relative_to(d).parts
                if not p.is_file() or _SKIP & set(parts):
                    continue
                if self.out is not None and d == self.root and parts[0] in LANE_DIRS.values():
                    continue
                yield p

    def _rel(self, target: Path) -> str:
        try:
            return target.relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(target)

    def describe(self, call: Dict[str, Any]) -> Dict[str, Any]:
        """What a call would do, for the permission card."""
        name, prm = call["name"], call["params"]
        if name == "run_cmd":
            cmd = (prm.get("cmd") or "").strip()
            background = _truthy(prm.get("background"))
            return {"tool": name, "path": str(self.out or self.root), "rel": ".", "inside": True, "command": cmd,
                    "background": background,
                    "summary": f"{'Start in background' if background else 'Run'}: {cmd}"}
        if name == "stop_cmd":
            bid = (prm.get("id") or "").strip()
            return {"tool": name, "path": str(self.out or self.root), "rel": ".", "inside": True,
                    "command": bid, "summary": f"Stop background command {bid}"}
        target, inside = self.resolve(prm.get("path", "."), write=name in _WRITES)
        src, _ = self.resolve(prm.get("path", "."))
        rel = self._rel(target)
        info = {"tool": name, "path": str(target), "rel": rel, "inside": inside, "summary": f"{name} {rel}"}
        try:
            if name == "write_file":
                new = _body(prm.get("content", ""))
                old = _read(src) if src.is_file() else ""
                verb = "Overwrite" if src.exists() else "Create"
                info["summary"] = f"{verb} {rel} ({new.count(chr(10)) + 1} lines)"
                info["diff"] = _diff(old, new, rel)
            elif name == "edit_file":
                old = _read(src) if src.is_file() else ""
                new = old.replace(prm.get("old_text", ""), prm.get("new_text", ""), 1) if prm.get("old_text") else old
                info["summary"] = f"Edit {rel}"
                info["diff"] = _diff(old, new, rel)
            elif name == "delete":
                if target.is_dir():
                    info["summary"] = f"Delete folder {rel} ({sum(1 for _ in target.rglob('*'))} items)"
                else:
                    info["summary"] = f"Delete {rel}"
            elif name == "mkdir":
                info["summary"] = f"Create folder {rel}"
            elif name in ("read_file", "ls", "search"):
                info["summary"] = {"read_file": "Read", "ls": "List", "search": "Search"}[name] + f" {rel}"
        except OSError:
            pass
        return info

    def run(self, call: Dict[str, Any], on_output=None, should_stop=None) -> Tuple[bool, str]:
        fn = getattr(self, "_t_" + call["name"], None)
        if fn is None:
            return False, (f"Unknown tool '{call['name']}'. Available: ls, read_file, search, write_file, "
                           "edit_file, mkdir, delete, run_cmd, stop_cmd.")
        try:
            if call["name"] == "run_cmd":
                out = fn(call["params"], on_output=on_output, should_stop=should_stop)
                return not _command_failed(out), out
            return True, fn(call["params"])
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    def _t_ls(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", "."))
        dirs = [d for d in (target, self._own(target)) if d is not None and d.is_dir()]
        if not dirs:
            raise NotADirectoryError(f"{self._rel(target)} is not a folder")
        kids = {}
        for d in dirs:
            for child in d.iterdir():
                if not (self.out is not None and d == self.root and child.name in LANE_DIRS.values()):
                    kids[child.name] = child
        rows = []
        for child in sorted(kids.values(), key=lambda c: (not c.is_dir(), c.name.lower()))[:300]:
            rows.append(child.name + "/" if child.is_dir() else f"{child.name}  ({child.stat().st_size} B)")
        return "\n".join(rows) or "(empty folder)"

    def _t_read_file(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", ""))
        if not target.is_file():
            raise FileNotFoundError(f"{self._rel(target)} does not exist")
        if target.stat().st_size > 1_000_000:
            raise ValueError("file is larger than 1 MB")
        text = _read(target)
        lines = text.splitlines()
        total = len(lines)
        try:
            start = int(prm.get("start_line") or 0)
        except (TypeError, ValueError):
            start = 0
        try:
            end = int(prm.get("end_line") or 0)
        except (TypeError, ValueError):
            end = 0
        if start > 0 or end > 0:
            start = max(1, start or 1)
            end = min(total, end or total)
            part = "\n".join(lines[start - 1:end])
            return f"[lines {start}-{end} of {total}]\n{part}"
        if len(text) > OUTPUT_LIMIT:
            return (text[:OUTPUT_LIMIT]
                    + f"\n... [file continues: {total} lines total - call read_file with "
                      "start_line/end_line to read a specific part]")
        return text

    def _t_search(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", "."))
        pattern = prm.get("pattern", "")
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        if target.is_file():
            files = [target]
        else:
            files = self._walk(target)
        hits = []
        for f in files:
            try:
                if f.stat().st_size > 1_000_000:
                    continue
                data = f.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:1024]:
                continue
            for n, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{self._rel(f)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n...[more matches]"
        return "\n".join(hits) or "no matches"

    def _t_write_file(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", ""), write=True)
        if not (prm.get("path") or "").strip() or target in (self.root, self.out) or target.is_dir():
            raise ValueError("write_file needs a file path")
        content = _body(prm.get("content", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        return f"Wrote {content.count(chr(10)) + 1} lines to {self._rel(target)}"

    def _t_edit_file(self, prm) -> str:
        src, _ = self.resolve(prm.get("path", ""))
        target, _ = self.resolve(prm.get("path", ""), write=True)
        if not src.is_file():
            raise FileNotFoundError(f"{self._rel(src)} does not exist")
        text = _read(src)
        old, new = prm.get("old_text", ""), prm.get("new_text", "")
        if not old:
            raise ValueError("old_text is empty")
        candidates = [(old, new), (old.strip("\r\n"), new.strip("\r\n"))]
        if "\r\n" in text:
            candidates += [(o.replace("\r\n", "\n").replace("\n", "\r\n"), n.replace("\r\n", "\n").replace("\n", "\r\n"))
                           for o, n in list(candidates)]
        for o, n in candidates:
            count = text.count(o) if o else 0
            if count == 1:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text.replace(o, n, 1), encoding="utf-8", newline="")
                return f"Edited {self._rel(target)}"
            if count > 1:
                raise ValueError(f"old_text matches {count} places; include more surrounding text")
        raise ValueError("old_text was not found; read the file and copy the exact text")

    def _t_mkdir(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", ""), write=True)
        target.mkdir(parents=True, exist_ok=True)
        return f"Folder ready: {self._rel(target)}"

    def _t_delete(self, prm) -> str:
        target, _ = self.resolve(prm.get("path", ""), write=True)
        if target in (self.root, self.out) or target in self.root.parents:
            raise PermissionError("refusing to delete the project folder itself")
        if target.is_dir():
            shutil.rmtree(target)
            return f"Deleted folder {self._rel(target)}"
        if target.exists():
            target.unlink()
            return f"Deleted {self._rel(target)}"
        raise FileNotFoundError(f"{self._rel(target)} does not exist")

    def _t_run_cmd(self, prm, on_output=None, should_stop=None) -> str:
        cwd = self.out or self.root
        cwd.mkdir(parents=True, exist_ok=True)
        return run_shell_command(prm.get("cmd") or "", cwd, on_output=on_output,
                                 should_stop=should_stop, background=_truthy(prm.get("background")))

    def _t_stop_cmd(self, prm) -> str:
        return BACKGROUND.stop(prm.get("id") or "")
