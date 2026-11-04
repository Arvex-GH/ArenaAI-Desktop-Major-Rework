# Arena AI Desktop — Security Audit Report

**Date:** 2026-11-03
**Commit:** `23148d1` (main, tag `alpha_1_1`)
**Method:** Static source review — no execution, no binary analysis, no network capture
**Reviewer:** Internal review verifying external report from Linux port developer

---

## Summary

An independent developer building a Linux client reviewed the Arena AI Desktop source and reported 12 security findings. We verified every finding against the current source. **All 12 are confirmed.** Three are critical, three are high, six are medium/low.

The codebase already does several things well (localhost-only binding, `before_request` origin/host guards, DNS rebinding protection, SSRF blocklist on media endpoints, `0o600` on accounts file, no obfuscation or exfiltration). The issues below are design-level gaps, not malice.

---

## Findings

### CRITICAL

#### 1. `/api/terminal` — Unauthenticated Shell Endpoint

**Location:** `arena/web/app.py:1731-1755`

**Problem:** POST to `/api/terminal` runs arbitrary commands via `subprocess.run(cmd, shell=True)` with no authentication token, no session check, and no origin requirement (the `before_request` guard only rejects a *present* foreign `Origin` header — omitting it bypasses the check). The blocklist is 5 strings (`format`, `del /s`, `rd /s`, `rmdir /s`, `rm -rf /`) — trivially bypassed via `python -c`, `powershell -enc`, `certutil`, pipe chains, or any command not on the list. The working directory is `Path.home()`.

Any local process, browser tab that discovers the port, or malware with localhost access gets full shell as the current user.

**Fix:**
- **Best:** Delete the endpoint entirely. The agent tool layer (`LocalTools._t_run_cmd`) already provides command execution through the permission broker.
- **If kept for debugging:** Require a per-launch random token in a custom header (generated at startup, never logged). Require `Origin` to be present and match the app's own origin. Use `shlex.split()` + an argv allowlist with `shell=False`. Gate behind an explicit `--debug` CLI flag, off by default.

**Severity:** Critical — unauthenticated RCE from localhost

**Status:** ✅ Fixed — endpoint hardened with per-launch API token, `shell=False`, comprehensive blocklist regex, shell metacharacter rejection, cwd scoped to `PROJECTS_DIR`.

---

#### 2. Session Cookies Stored in Plaintext

**Location:** `arena/config.py:11`, `arena/auth.py:128-155`, `arena/accounts.py:56-72`

**Problem:** Supabase session cookies (`arena-auth-prod-v1.0/.1`) contain the JWT access token, email, and user ID. They are base64-encoded (not encrypted) and written to:
- `~/.arena-cli/cookies.txt` — raw cookie string
- `~/.arena-cli/accounts.json` — structured account data including tokens

`os.chmod(0o600)` at `accounts.py:60` is effectively a no-op on Windows (NTFS ACLs are not set by POSIX chmod). Any process running as the user, any backup/sync tool, or any malware with file-read access gets a full account session requiring no password.

**Fix:**
- **Best:** Don't persist sessions at all. Keep them in the WebView2 profile and re-authenticate on restart.
- **If persistence is required:** Encrypt with `CryptProtectData` (DPAPI, current-user scope) or store in Windows Credential Manager. Delete session files on exit. Provide a "Sign out and wipe local data" action. Never write raw cookies to logs, dumps, or clipboard paths.

**Severity:** Critical — credential theft from any local process

**Status:** ✅ Fixed — `arena/crypto.py` implements DPAPI encryption via `CryptProtectData`/`CryptUnprotectData`. Cookie storage now encrypts on save, decrypts on load, with transparent fallback for pre-existing plaintext data.

---

#### 3. CLI Mode Bypasses Permission Broker

**Location:** `arena/cli.py:794`

**Problem:** The web UI routes all tool calls through `PermissionBroker` (`arena/permissions.py:69+`), which can prompt the user before executing dangerous operations. The `--cli` path calls `tools.execute(tc["name"], tc["params"])` directly at line 794 with zero permission checks. This means the model can execute `RunCmdTool`, `BrowserExecJsTool`, `BrowserOpenUrlTool`, file writes, and deletes with no confirmation dialog.

**Fix:**
- Create a shared `execute_tool(name, params, broker)` function used by both the web UI and CLI entry points.
- Default CLI to `default` permission mode (confirms shell commands and writes).
- Add a test asserting CLI and UI produce identical permission decisions for the same tool call and mode.

**Severity:** Critical — full tool execution with no user confirmation

**Status:** ✅ Fixed — CLI tool execution now routes through `permissions.decide()` with user confirmation for "ask" verdicts.

---

### HIGH

#### 4. Browser Cookie Decryption + Unauthenticated CDP

**Location:** `arena/web/app.py:57-130` (DPAPI + AES-GCM decrypt), `arena/auth.py:187-215` (CDP on port 9222)

**Problem:** The auto-login path has two mechanisms, both overpowered for the intent:

1. **Direct cookie DB decryption:** Reads Chrome/Edge/Brave `Cookies` SQLite DB, decrypts values using `CryptUnprotectData` (DPAPI) and AES-GCM for v10/v11/v20 encrypted cookies. This bypasses the browser's own httpOnly protections.

2. **CDP debug port:** Launches a browser with `--remote-debugging-port=9222`. Port 9222 is a well-known target — it exposes `Network.getAllCookies` (every cookie including httpOnly across all domains) and `Runtime.evaluate` (arbitrary JS in any tab). The port has no authentication.

**Severity:** High — mechanism far exceeds intent, exposes all browser cookies to localhost

**Status:** ⚠️ Partially mitigated — per-launch API token protects endpoints from unauthorized access. CDP and browser DB decryption remain as-is for backwards compatibility with auto-login flow.

---

#### 5. Model-Generated Commands Execute via `shell=True` with Weak Guards

**Location:** `arena/agent.py:324`, `arena/tools/shell_tools.py:25-27`, `arena/permissions.py:30-66`

**Problem:** Tool calls parsed from the model's output are passed to `subprocess.run(cmd, shell=True)` with a weak blocklist regex. The `bypassPermissions` mode skips all checks.

**Severity:** High — prompt injection → code execution

**Status:** ✅ Fixed — `shell=False` + `shlex.split()` in agent.py. Comprehensive `_DANGEROUS_FLAGS` regex blocks `-c`, `-e`, `--eval`, `-enc` and 20+ interpreter/flag combinations. `bypassPermissions` mode removed entirely.

---

#### 6. "Workspace Sandbox" Covers the Entire Home Directory

**Location:** `arena/web/app.py:1621-1630` (`_safe_resolve`)

**Problem:** The sandbox accepted any path under `~`, including `.ssh/`, `.aws/`, `.gnupg/`, browser profiles, and the app's own config.

**Severity:** High — file read/write/delete across entire user profile

**Status:** ✅ Fixed — `_safe_resolve` scoped to `workspace.PROJECTS_DIR` only. `_SENSITIVE_DIRS` frozenset blocks `.ssh`, `.aws`, `.gnupg`, `.env`, `.git` and other sensitive paths. `..` traversal explicitly rejected.

---

### MEDIUM

#### 7. CDN Scripts Without Subresource Integrity

**Location:** `arena/web/static/index.html:11`

**Problem:** JavaScript libraries load from CDN without `integrity=` or `crossorigin` attributes.

**Severity:** Medium

**Status:** ⚠️ Not yet addressed — vendoring libraries is planned for a future release.

---

#### 8. Unsigned, Non-Reproducible Release Binary

**Location:** `arena-cli.spec:84-91`

**Problem:** No code signature, no SBOM, no CI build pipeline, no published checksum.

**Severity:** Medium

**Status:** ⚠️ Not yet addressed — planned for stable release.

---

#### 9. Unpinned Dependencies

**Location:** `requirements.txt`

**Problem:** Only lower bounds, no lockfile, no hashes.

**Severity:** Medium

**Status:** ⚠️ Not yet addressed — `pip-compile --generate-hashes` planned.

---

#### 10. Internal Endpoints + Auto reCAPTCHA vs README Claims

**Location:** `arena/contracts.py:19-33`

**Problem:** Automated use of arena.ai's internal API and reCAPTCHA token generation conflicts with README claims.

**Severity:** Medium

**Status:** ⚠️ Documentation alignment planned.

---

#### 11. File Uploads, Stream Dumps, and Logs Retain User Content

**Location:** `arena/web/app.py:906-932`

**Problem:** Local file contents uploaded without per-file consent. Stream dumps and logs may contain tokens.

**Severity:** Medium

**Status:** ⚠️ Not yet addressed — planned for stable release.

---

### LOW

#### 12a. Unguarded `requests.get` from Model/Service URLs

URLs originating from the model stream fetched without host allowlist or size caps.

#### 12b. Overridable Contracts File

`~/.arena-cli/contracts.json` can redirect API paths with no integrity check.

#### 12c. Clipboard Auto-Scan

`check_clipboard_for_cookies()` passively reads clipboard contents.

#### 12d. Documentation Drift

Version claims, test counts, and feature descriptions need alignment.

**Status:** ⚠️ Low priority — tracked for future releases.

---

## Settings API Hardening (Additional Fix)

**Not in original report.** The `/api/settings POST` endpoint accepted arbitrary config keys via `cfg.update(data)`, allowing injection of `permission_mode` and other internal settings. Fixed with a `_SETTINGS_ALLOWLIST` that permits only UI-safe keys.

---

## Priority Fix Order

If fixing only three things:

1. ✅ **Authenticate `/api/terminal`** — per-launch token + `shell=False` + comprehensive blocklist
2. ✅ **Stop persisting cookies in plaintext** — DPAPI encryption via `arena/crypto.py`
3. ✅ **Route all tool calls through `PermissionBroker`** — CLI now uses `permissions.decide()`

---

*Report generated 2026-11-03. Updated 2026-11-04 with fix statuses.*
*Fixes applied at commit on `main` branch.*
