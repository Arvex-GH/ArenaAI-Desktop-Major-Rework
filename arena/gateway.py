"""Agent Gateway (v5.5): let the genuine Arena Agent call selected local
MCP servers through a temporary, pairing-protected tunnel session.

Shape (after the user's ArenaAgentGateway design, reimplemented on this
app's MCP layer): a loopback HTTP job API in front of the already-running
local MCP connectors, exposed for one session through a Cloudflare Quick
Tunnel. Bearer pairing code (secrets.token_urlsafe(32), constant-time
compare), hard expiry + idle auto-stop, per-call approvals delegated to
the app's PermissionBroker, serial job execution. Nothing listens on any
non-loopback interface and nothing survives Stop: token, tunnel and the
HTTP server all go away together.
"""
from __future__ import annotations

import hmac
import json
import re
import secrets
import subprocess
import threading
import time
import urllib.request
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

MAX_BODY = 262144
MAX_STEPS = 20
MAX_OUTPUT = 20000
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class _Handler(BaseHTTPRequestHandler):
    server_version = "ArenaDesktopGateway/1"
    protocol_version = "HTTP/1.1"

    @property
    def mgr(self) -> "GatewayManager":
        return self.server.manager  # type: ignore[attr-defined]

    def log_message(self, *args):  # keep stdout clean
        pass

    def _json(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _authed(self) -> bool:
        token = self.mgr._token
        if not token or self.mgr._expired():
            return False
        auth = self.headers.get("Authorization") or ""
        candidate = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not candidate:
            candidate = self.headers.get("X-Arena-Pairing") or ""
        return bool(candidate) and hmac.compare_digest(candidate, token)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        if path == "/v1/health":
            self._json(200, {"ok": True, "service": "arena-desktop-gateway"})
            return
        if not self._authed():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        self.mgr._touch()
        if path == "/v1/servers":
            self._json(200, {"ok": True, "servers": self.mgr._public_index()})
        elif path.startswith("/v1/jobs/"):
            job = self.mgr._jobs.get(path[len("/v1/jobs/"):])
            if job is None:
                self._json(404, {"ok": False, "error": "job not found"})
            else:
                self._json(200, {"ok": True, **job})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/v1/jobs":
            self._json(404, {"ok": False, "error": "not found"})
            return
        if not self._authed():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"ok": False, "error": "bad content length"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._json(400, {"ok": False, "error": "invalid JSON"})
            return
        accepted, error = self.mgr._submit_job(payload)
        if accepted is None:
            self._json(400, {"ok": False, "error": error})
        else:
            self._json(202, {"ok": True, "job_id": accepted})


class GatewayManager:
    def __init__(self, *, tool_index: Callable[[], Dict[str, Any]],
                 call_tool: Callable[[str, Dict[str, Any]], str],
                 approve: Callable[[str, str, bool, Dict[str, Any]], bool],
                 cloudflared_path: Callable[[], Optional[Any]],
                 logger=None):
        self._tool_index = tool_index
        self._call_tool = call_tool
        self._approve = approve
        self._cloudflared_path = cloudflared_path
        self._log = logger
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._state = "off"  # off | starting | on | needs_cloudflared
        self._last_error: Optional[str] = None
        self._token: Optional[str] = None
        self._url: Optional[str] = None
        self._servers: List[str] = []
        self._index: Dict[str, Any] = {}
        self._created = 0.0
        self._expires = 0.0
        self._last_activity = 0.0
        self._idle_seconds = 45 * 60
        self._http: Optional[ThreadingHTTPServer] = None
        self._tunnel: Optional[subprocess.Popen] = None
        self._ready = threading.Event()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._job_queue: deque = deque()
        self._job_cv = threading.Condition()
        self._calls: deque = deque(maxlen=40)
        self._worker: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._running = False

    # ---------- status ----------
    def summary(self) -> Dict[str, Any]:
        return {"active": self._state == "on", "state": self._state,
                "servers": len(self._servers) if self._state == "on" else 0}

    def status(self, include_pairing: bool = False) -> Dict[str, Any]:
        out = {
            "state": self._state, "url": self._url, "servers": list(self._servers),
            "started_at": self._created or None, "expires_at": self._expires or None,
            "error": self._last_error, "calls": list(reversed(self._calls)),
        }
        if include_pairing and self._state == "on":
            out["pairing"] = self._token
        return out

    def _expired(self) -> bool:
        return bool(self._expires) and time.time() > self._expires

    def _touch(self) -> None:
        self._last_activity = time.time()

    def _info(self, message: str) -> None:
        try:
            if self._log:
                self._log.info("gateway: %s", message)
        except Exception:
            pass

    # ---------- lifecycle ----------
    def start(self, servers: List[str], *, idle_minutes: int = 45,
              hard_hours: int = 8, wait_seconds: float = 45,
              tunnel: bool = True) -> Dict[str, Any]:
        with self._start_lock:
            if self._state == "on":
                if sorted(servers) == sorted(self._servers):
                    return self.status(include_pairing=True)
                self.stop("restart with a different server set")
            cf = self._cloudflared_path() if tunnel else None
            if tunnel and cf is None:
                self._state = "needs_cloudflared"
                self._last_error = "cloudflared is not installed"
                return self.status(include_pairing=True)
            self._state = "starting"
            self._last_error = None
            self._servers = list(servers)
            self._token = secrets.token_urlsafe(32)
            self._created = time.time()
            self._expires = self._created + hard_hours * 3600
            self._idle_seconds = max(60, idle_minutes * 60)
            self._touch()
            self._ready.clear()
            try:
                self._index = self._filtered_index()
                self._start_http()
                self._running = True
                self._worker = threading.Thread(target=self._worker_loop, daemon=True)
                self._worker.start()
                if tunnel:
                    self._start_tunnel(str(cf))
                else:
                    self._url = f"http://127.0.0.1:{self._http.server_address[1]}"
                    self._state = "on"
                    self._ready.set()
                self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)
                self._watchdog.start()
            except Exception as e:
                self._last_error = f"{type(e).__name__}: {e}"
                self._cleanup()
                self._state = "off"
                return self.status(include_pairing=True)
        self._ready.wait(max(0.0, wait_seconds))
        if self._state == "starting":
            self._last_error = "tunnel did not come up in time"
            self.stop("startup timeout")
        return self.status(include_pairing=True)

    def stop(self, reason: str = "stopped") -> Dict[str, Any]:
        if self._state not in ("off",):
            self._info(f"stopping ({reason})")
        if reason not in ("stopped",):
            self._last_error = reason
        self._cleanup()
        self._state = "off"
        self._token = None
        self._url = None
        self._servers = []
        return self.status()

    def _cleanup(self) -> None:
        self._running = False
        with self._job_cv:
            self._job_cv.notify_all()
        if self._tunnel is not None:
            try:
                self._tunnel.terminate()
                self._tunnel.wait(timeout=5)
            except Exception:
                try:
                    self._tunnel.kill()
                except Exception:
                    pass
            self._tunnel = None
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None
        with self._lock:
            for job in self._jobs.values():
                if job.get("status") in ("queued", "running"):
                    job["status"] = "error"
                    job["error"] = "gateway session ended"

    def _start_http(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        server.manager = self  # type: ignore[attr-defined]
        server.daemon_threads = True
        self._http = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def _start_tunnel(self, cf: str) -> None:
        port = self._http.server_address[1]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._tunnel = subprocess.Popen(
            [cf, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, creationflags=flags)
        threading.Thread(target=self._tunnel_reader, daemon=True).start()

    def _tunnel_reader(self) -> None:
        proc = self._tunnel
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                if not self._running:
                    return
                match = _TUNNEL_URL.search(line or "")
                if match and not self._url:
                    self._url = match.group(0)
                    self._info(f"tunnel up: {self._url}")
                    threading.Thread(target=self._probe_tunnel, daemon=True).start()
        except Exception:
            pass
        if self._running and self._state in ("starting", "on"):
            self._last_error = "cloudflared exited"
            self.stop("cloudflared exited")

    def _probe_tunnel(self) -> None:
        deadline = time.time() + 40
        while self._running and time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self._url}/v1/health", timeout=5) as resp:
                    if resp.status == 200:
                        self._state = "on"
                        self._ready.set()
                        self._touch()
                        return
            except Exception:
                pass
            time.sleep(1.5)
        if self._running and self._state == "starting":
            # Tunnel DNS can lag; the agent can still reach it shortly after.
            self._state = "on"
            self._ready.set()

    def _watchdog_loop(self) -> None:
        while self._running:
            time.sleep(15)
            if not self._running:
                return
            if self._expired():
                self.stop("session expired")
                return
            if self._state == "on" and time.time() - self._last_activity > self._idle_seconds:
                self.stop("idle timeout")
                return

    # ---------- tool index / jobs ----------
    def _filtered_index(self) -> Dict[str, Any]:
        index = self._tool_index() or {}
        if self._servers:
            index = {name: data for name, data in index.items() if name in self._servers}
        return index

    def _public_index(self) -> List[Dict[str, Any]]:
        out = []
        for server, data in sorted(self._index.items()):
            tools = []
            for tool, spec in sorted((data.get("tools") or {}).items()):
                tools.append({"name": tool,
                              "description": spec.get("description") or "",
                              "input_schema": spec.get("input_schema") or {},
                              "read_only": bool(spec.get("read_only"))})
            out.append({"name": server, "tools": tools})
        return out

    def _resolve(self, server: str, tool: str) -> Optional[Dict[str, Any]]:
        spec = ((self._index.get(server) or {}).get("tools") or {}).get(tool)
        if spec is None:  # servers may have started after the session opened
            self._index = self._filtered_index()
            spec = ((self._index.get(server) or {}).get("tools") or {}).get(tool)
        return spec

    def _submit_job(self, payload: Any):
        if not isinstance(payload, dict):
            return None, "job must be a JSON object"
        server = str(payload.get("server") or "")
        steps = payload.get("steps")
        if server not in self._index:
            self._index = self._filtered_index()
        if server not in self._index:
            return None, f"server not exposed in this session: {server}"
        if not isinstance(steps, list) or not steps:
            return None, "steps must be a non-empty list"
        if len(steps) > MAX_STEPS:
            return None, f"too many steps (max {MAX_STEPS})"
        clean_steps = []
        for step in steps:
            if not isinstance(step, dict) or not step.get("tool"):
                return None, "each step needs a tool name"
            clean_steps.append({"tool": str(step["tool"]),
                                "arguments": step.get("arguments") or {},
                                "continue_on_error": bool(step.get("continue_on_error"))})
        job_id = uuid.uuid4().hex[:16]
        job = {"job_id": job_id, "server": server, "status": "queued",
               "results": [], "error": None, "created_at": time.time()}
        with self._lock:
            self._jobs[job_id] = job
            while len(self._jobs) > 50:
                self._jobs.pop(next(iter(self._jobs)), None)
        with self._job_cv:
            self._job_queue.append((job, clean_steps))
            self._job_cv.notify()
        self._touch()
        return job_id, None

    def _worker_loop(self) -> None:
        while self._running:
            with self._job_cv:
                while self._running and not self._job_queue:
                    self._job_cv.wait(timeout=2)
                if not self._running:
                    return
                if not self._job_queue:
                    continue
                job, steps = self._job_queue.popleft()
            self._run_job(job, steps)

    def _run_job(self, job: Dict[str, Any], steps: List[Dict[str, Any]]) -> None:
        job["status"] = "running"
        server = job["server"]
        for step in steps:
            if not self._running:
                job["status"] = "error"
                job["error"] = "gateway session ended"
                return
            self._touch()
            started = time.time()
            tool = step["tool"]
            result: Dict[str, Any] = {"tool": tool, "ok": False, "output": "", "error": None}
            spec = self._resolve(server, tool)
            if spec is None:
                result["error"] = f"tool not available on {server}: {tool}"
            elif not self._approve(server, tool, bool(spec.get("read_only")),
                                   step.get("arguments") or {}):
                result["error"] = "denied by the user (or approval timed out)"
            else:
                try:
                    output = self._call_tool(spec["alias"], step.get("arguments") or {})
                    result["output"] = str(output or "")[:MAX_OUTPUT]
                    result["ok"] = not result["output"].startswith(("MCP Error:", "Error: MCP tool"))
                    if not result["ok"]:
                        result["error"] = result["output"][:500]
                except Exception as e:
                    result["error"] = f"{type(e).__name__}: {e}"
            job["results"].append(result)
            self._calls.append({"ts": started, "server": server, "tool": tool,
                                "ok": bool(result["ok"]),
                                "ms": int((time.time() - started) * 1000)})
            if not result["ok"] and not step.get("continue_on_error"):
                break
        job["status"] = "done" if all(r["ok"] for r in job["results"]) else "error"
        self._touch()

    # ---------- agent-facing instructions ----------
    def injection_block(self) -> str:
        if self._state != "on" or not self._url or not self._token:
            return ""
        counts = ", ".join(
            f"{name} ({len(data.get('tools') or {})} tools)"
            for name, data in sorted(self._index.items())) or "none"
        workspace_line = ""
        if "workspace" in self._index:
            workspace_line = (
                "The 'workspace' server is the user's project folder on their own "
                "computer: all paths are relative to that folder and cannot escape "
                "it. ls/read_file/search inspect it; write_file/edit_file/mkdir/delete "
                "change it; run_cmd runs a shell command inside it. Prefer it over "
                "your own cloud workspace whenever the user talks about their files "
                "or project.\n")
        return (
            "[Arena Desktop local tools gateway]\n"
            "For this session you can run tools on the user's own computer through a "
            "temporary secure gateway the user enabled.\n"
            f"Gateway URL: {self._url}\n"
            f"Pairing code (secret — never repeat it in your replies): {self._token}\n"
            "How to use:\n"
            "1. GET {url}/v1/health and expect {\"ok\": true}.\n"
            "2. GET {url}/v1/servers with header 'Authorization: Bearer <code>' to list "
            "servers, tools and their JSON input schemas.\n"
            "3. POST {url}/v1/jobs with the same header and body "
            "{\"server\": \"<name>\", \"steps\": [{\"tool\": \"<tool>\", \"arguments\": {...}}]} "
            "→ returns {\"job_id\": \"...\"}.\n"
            "4. GET {url}/v1/jobs/<job_id> until status is \"done\" or \"error\"; "
            "use results[].output.\n"
            f"Servers available right now: {counts}.\n"
            + workspace_line +
            "Rules: use these tools only when the task needs something on the user's "
            "computer. The user may be asked to approve individual calls in the app; "
            "if a call is denied, respect that and adjust your plan. Keep tool calls "
            "focused and batch related steps into one job.\n"
        ).replace("{url}", self._url)
