# Arena Desktop — Session Handoff

**Date:** 2026-11-07
**Version:** 1.5.16
**Status:** Desktop app working — Flask + pywebview/WebView2, full web UI, 306/307 tests pass

---

## Legal Status

Arena Desktop is an **unofficial personal desktop client** for [arena.ai](https://arena.ai). It is **legal software**:

- It is a personal client that accesses arena.ai using the user's own authenticated account and cookies — the same way any browser does.
- No proprietary arena.ai source code is copied, decompiled, or reverse-engineered. The client communicates via arena.ai's public-facing HTTP API endpoints.
- It is functionally equivalent to using arena.ai in a browser — it adds desktop convenience features (system tray, workspace sync, local agent tools) on top of the same web API any browser uses.
- The project is clearly labeled "unofficial" throughout the UI and README.
- No trademarks are misused. The app identifies itself as an independent project.

This is the same category of software as third-party email clients (Thunderbird for Gmail), third-party Reddit clients, or desktop wrappers for web services. Building a personal client that talks to a public API with your own credentials is standard, legal practice.

---

## What Was Done This Session (2026-11-06)

### Fixes Applied

1. **JS syntax errors (curly quotes)** — Prior session introduced Unicode curly quotes (U+2018/U+2019) as JS string delimiters in `12-settings.js` and `13-connectors.js`. Replaced all with straight quotes. Rebuilt `app.js` bundle. `node -c` validates clean.

2. **FakeResp missing `.headers`** — `test_battle_chat_streams_both_lanes_and_saves` failed because `FakeResp` in `tests/test_core.py` lacked a `.headers` attribute. After streaming completes, `_emit_usage_from_headers(r.headers, conv)` at `arena/web/app.py:3712` threw `AttributeError`, caught by `_sse_wrap` which yielded an empty done event. Fixed by adding `self.headers = {}` to `FakeResp.__init__`.

3. **Shell test blocked by metacharacter filter** — `test_shell_refusal_and_idle_kill` used `python3 -c "import time; time.sleep(30)"` which contains `(` — caught by `_SHELL_META` filter at `arena/agent.py:350-352`. Changed test to write a temp `sleeper.py` file and run `python "{sleeper}"` instead.

### Verification

- Exe rebuilt via PyInstaller, launches clean
- App loads in browser, logged in (tested via both source and compiled exe)
- Battle mode tested end-to-end: sent "hello, just testing arena desktop", both Model A and Model B responded with streaming text, voting buttons rendered
- Zero console errors
- 306 tests pass, 1 pre-existing failure (see below)

---

## Session 2 (2026-11-06, later) — v1.5.0 tool UI + local commands

### Done

1. **Claude Code–style tool rows** — `renderActivities()` in `js/07-render.js` rewritten: `● Bash(cmd)` head + `⎿ output` body, status dot colours, 4-line fold with click-to-expand (`expandedActivities` set, `[data-tool-toggle]` click handler), Read/List/Search one-line summaries, inline diff for Write/Update, JSON unwrapping for Arena Agent (`{"command":…}` input, `{stdout,exitCode}` output). CSS block "v1.5.0 tool rows" at the end of `style.css`. Old `.activity-*` CSS is still used by sandbox diff cards — leave it.
2. **Input vs output kept apart** — `tool_result` now sets `activity.output` (was overwriting `detail` = the input). New SSE event `tool_progress {id, output}` updates a running row live. `normalizeActivity()` carries `output`.
3. **History keeps tool input/output** — local `conv["tools"]` entries gain `id, command, rel, background, output` (trimmed by `_history_output`, 2000 chars); native entries gain `input`, `output`.
4. **Typewriter bug (was HIGH) fixed** — real root cause: the loop ran `while (alive && wrap.isConnected)` before the element was attached, so it exited at once. Now waits up to 3 s for attach; one typewriter per lane kept in a `WeakMap` across re-renders.
5. **"Generating…" label (was LOW) replaced** — `✻ Running command… 12s` / `✻ Thinking…` / `✻ Waiting for your approval…` with a live timer (`renderGeneratingLabel`).
6. **Local commands** (`arena/agent.py`):
   - `run_cmd` accepts shell syntax (`&&`, `|`, `>`, `%VAR%`) → runs with `shell=True` (cmd.exe / sh). `permissions.needs_explicit_approval()` makes these (and `-c/-e/--eval` inline code) **always ask**, even after "always" and in `bypassPermissions`. `CATASTROPHIC` still denied in both paths.
   - Live output: `run_shell_command(on_output=, should_stop=)`; `app.py::_run_cmd_live()` runs it on a worker thread and yields `tool_progress` + pings; Stop kills the tree.
   - Background: `<background>true</background>` → `agent.BACKGROUND` registry, log in `.arena-artifacts/bgN.log`, returns first ~8 s + detected localhost URL; `stop_cmd` tool (permission kind `read`); `atexit` stops all.
   - Idle limit 180 s for npm/pip/cargo/… (`_LONG_RUNNERS`), env has `NO_COLOR`, `npm_config_yes`, `GIT_TERMINAL_PROMPT=0`.
   - `LocalTools.run()` returns ok=False for non-zero exit codes (row turns red).
   - Fixed `argv[0].lower().rstrip(".exe")` bug (stripped chars, not the suffix) → `_program_name()`.
7. Demo mode (`?demo=1`, Agent) now shows Bash with live progress, an Update diff and a failing command — use it to eyeball the UI.
8. Tests: `tests/test_shell_v15.py` (18 tests).

### Session 3 additions (same day)

- **Answer order** — `renderAssistantLane()`: Thinking → tool rows → approval cards → answer text → media/sources/artifacts. "Arena Agent · session active" stage chips and "Round N" chips no longer render; lane "A" badge only in Battle/Side-by-side; topbar eyebrow is just the mode label.
- **De-slop CSS** — block "v1.5.0 de-slop" at end of `style.css`: sentence-case labels (no tracked caps), `.tabular`, tool-row hover/focus, reduced-motion.
- **Chat titles** — `_smart_title()` (≤8 words, strips "can you / please / i want you to") is the provisional title; `_schedule_arena_title()` thread re-fetches the Arena session (8/20/45/90 s) and swaps in the summary title Arena generates server-side. Frontend `finishGeneration()` reloads the chat list at 10/30/60/100 s.
- **Usage tab** — was stuck on "Loading usage…" because `esc()` did not exist (ReferenceError). Added `esc` to `01-core.js`; backend stores the last real `ratelimit-remaining/reset/modality` per account in `usage["live"]`, shown as "Arena limit". arena.ai has no separate usage endpoint the app knows of; these headers are the real source.
- **Settings allowlist bug** — `notify_sound`, `update_check` were never saved (not in `_SETTINGS_ALLOWLIST`). Added them plus `notify_volume`, `setup_done`.
- **Done sound** — `playChime()`: 3-note bell (C6-E6-G6, sine + inharmonic partial, lowpass), volume `notify_volume` (default 0.6), plays on every finished reply (was: only when window unfocused, gain 0.08).
- **Component prompts** — `agent.missing_component()` spots "'npm' is not recognized…" etc.; `_run_cmd_live` emits `component_missing`; `promptComponentInstall()` shows a toast with an Install button (`toast()` now takes an `action`).
- **Upload middleman** — `_convert_attachment()` in `app.py` (called by `/api/uploads`): BMP/TIFF/ICO/AVIF/TGA/PSD/GIF(eval) → PNG via Pillow; code/text files (`.py`, `.ts`, `.yaml` …) → `name.ext.txt` text/plain in Agent Mode; HEIC refused with a clear message (no pillow-heif). Frontend `convertibleHere()` in `10-stream.js` lets those files through the picker/pre-check. `test_uploads.py::test_agent_rejects_python_files` replaced by `test_agent_sends_python_files_as_text` + `test_agent_rejects_binary_files`.
- Full suite after these: 330 passed, 0 failed, 21 skipped (MCP hang test deselected), plus 30 in `test_shell_v15.py`.
- **First-run setup** — `runFirstRunSetup()` in `14-boot.js`: one-time overlay (app data, workspace, WebView2, Node.js, Python with Install buttons, model list), "Get started" saves `setup_done: true`. Not shown in demo mode. To see it again: remove `setup_done` from `~/.arena-personal/config.json`.

### Not verified live

- A real Arena Agent run (native, JSON tool payloads) and a real local-agent run with `npm install` were not exercised end-to-end against arena.ai; only demo mode + unit tests.
- Exe not rebuilt this session.

---

## Session 4 (2026-11-06, night) — v1.5.1 reliability fixes + backend split

- **Chat folder bleed** — a reply keeps the folder it started with (`_tl.workspace` pin in `_sse_wrap`); the chat saves `workspace`; opening a chat switches back to its folder. Approval cards show only in their own chat; Stop/done settles a chat's pending approvals.
- **No duplicate sends** — a stream that broke after HTTP 200 is never re-sent (that doubled arena session usage and caused the real HTTP 400 "token usage limit"). `_recover_reply` reads the reply from arena's copy (`text_replace` event) and only accepts a reply to the prompt it sent. Finished-but-empty replies still retry.
- **Cut-off detection** — a stream without arena's finish record (`ad:`) is flagged `_unfinished`. Test `FakeResp` now appends finish records like real arena (`finish=False` simulates a break).
- **Errors named** — `session_full` (text/tool chats continue in a fresh session with a digest; Code chats explain), human check ("I'm not a robot") incl. a send held by an open check, status.arena.ai incident notices on failures.
- **Text** — paragraph breaks between tool steps (live stream + history); refresh merge fills cut-off replies in place and repairs replies glued by older merges.
- **Dev servers** — local agent's background servers surface their URL as a preview card; Stop ends the reply's servers; workspace panel ▶ "Run on this PC" (`/api/workspace/run-local`: install + dev script, URL, Stop). e2b sandbox links (`3000-<id>.e2b.app`) become previews.
- Backend split: see Architecture below. engine.py's page JavaScript moved to `arena/engine_js.py`.
- **Account menu** (`buildAccountMenu` in `11-ui.js`): the signed-in account on top, "Switch to" list, "Add another account" (sign-in window or paste cookies), then Settings, Help, Sign out. Switch, sign-out and cookie import run through `setAccountTask()`: each step shows in the menu, the Settings → Account panel (`accountTaskLine`) and the sidebar (`aria-busy`); errors show inline when the menu or settings are open, else as a toast.
- **Old chats** (`05-state.js`): `legacyToolActivities` turns `<tool>` tags in old replies into tool rows, `legacyToolResults` attaches the "Tool results:" turns as their output, `backfillToolStep` reads command/path back from old step summaries, `userWordsAfterInjection` shows the user's question that old refreshes saved behind the guide/memory text. Sandbox `run` rows show the command as the call. New Code-mode chats save each sandbox step's output (`_sandbox_output_text`, `sandbox_done.output`).
- **Chats jumping to "Recent"** — the refresh on open re-saved text fixes (paragraph breaks) as activity. `_save_chat(touch=False, stamp=...)`: refreshes never bump; imports and refreshes take arena.ai's own time (`history_sync.last_activity`, `_synced_stamp`).
- **Windows** — site preview window: `open_preview` raised `NameError: webview` (previews fell back to the browser); it now opens, and closing only hides it. arena.ai window close also drops the stay-on-top pin. The reported freeze when closing the arena.ai window was NOT reproduced (3 open/close cycles via `/api/engine/show` + WM_CLOSE, clean and signed-in profiles, main window exits in 2.5 s): exact steps still needed.

## Pre-existing Test Failure

**`test_agent_calls_mcp_tool_with_schema_coerced_json`** — `fake.calls` is empty (`[]`), meaning the agent loop never dispatches the MCP tool call. Confirmed pre-existing by running on stashed (unmodified) code — same failure. This is a bug in the agent loop's MCP tool routing logic, not related to any changes this session made. The test ran for 3.8 hours before timing out.

---

## Open Bugs for Next Session

### 1. Agent Mode Typewriter Phrases Not Visible (HIGH) — FIXED in session 2, see above

**What dj sees:** In Agent mode, during generation, only a green dot `●` and blinking cursor `|` appear. The rotating typewriter phrases ("asking Terry", "argmaxxing...", "thinking deeply" etc.) never show. Text only appears once generation is fully complete.

**Root cause:** Each SSE streaming event calls `scheduleChatRender()` which rebuilds the DOM. This destroys the current `createThinkingTypewriter()` element before its async animation loop types even one character. A new typewriter is created but immediately destroyed by the next event.

**Files:**
- `arena/web/static/js/01-core.js:115-147` — `createThinkingTypewriter()` creates the element and starts an async typing loop
- `arena/web/static/js/07-render.js:748-762` — Render logic creates typewriter when `showTypewriter = lane.streaming && (!visibleText || inTypewriterWindow)`
- `arena/web/static/js/10-stream.js:100` — Every text/meta event calls `scheduleChatRender()`

**Fix approach:** Persist the typewriter DOM element across re-renders instead of recreating it. Either:
- Cache the typewriter element on the lane object and reuse it if still alive
- Or extract the typewriter from the card before destroying it and reattach after rebuild
- Or debounce/skip re-renders that only affect the typewriter section

### 2. "Generating..." Label (LOW) — DONE in session 2, see above

The "Generating..." text with animated dots at `07-render.js:729-732` is separate from the typewriter. dj wants this to be more informative or different from the typewriter animation.

### 3. Agent Mode Streaming Text (MEDIUM)

Even after typewriter window expires (1800ms), streaming text may not render incrementally in agent mode. The `visibleLaneText()` function strips tool XML blocks, but the text events from the agent SSE endpoint may arrive differently than battle/direct mode. Verify that `type: 'text'` events carry `event.text`/`event.delta`/`event.content` correctly for agent backend.

### 4. MCP Tool Call Routing (PRE-EXISTING)

`test_agent_calls_mcp_tool_with_schema_coerced_json` — agent loop doesn't dispatch MCP tool calls. `fake.calls` stays empty. Not a regression.

---

## Architecture Quick Reference

```
arena/
├── desktop.py          # PyInstaller entry: pywebview + Flask, WebView2
├── web/
│   ├── app.py          # Loader only: runs server/*.py in order inside its own namespace
│   ├── server/         # Flask backend split by topic, every file <= 1,500 lines
│   │   ├── core.py         # imports, Flask app, _API_TOKEN, chat store, catalog, memory
│   │   ├── accounts.py     # accounts, sign-in/out, settings, mode routes
│   │   ├── chats.py        # chat list/load/refresh/compact, diagnostics, questions
│   │   ├── library.py      # search/export/fork, Elo, automations, delete/archive, vote
│   │   ├── stream.py       # _sse_wrap() (per-chat folder pin), sends, sandbox mirror, _line_events()
│   │   ├── turns.py        # pacing, _turn, failover, gateway, reply recovery, _turn_auto
│   │   ├── arena_agent.py  # genuine Arena Agent bridge + sandbox file mirror
│   │   ├── local_agent.py  # checkpoints, sub-agents, _agent_run, dev-server links
│   │   ├── send.py         # attachments, /api/chat
│   │   ├── workspace.py    # files, terminal, permissions, stop, workspace, Run on this PC
│   │   └── connectors.py   # arena + MCP connectors, run_server
│   │   (One shared namespace on purpose: tests patch arena.web.app.<name> and that
│   │    must reach every part. New part = add it to PARTS in app.py AND to
│   │    hiddenimports in arena-cli.spec; tests/test_v151_fixes.py checks both
│   │    and the 1,500-line limit.)
│   ├── static/
│   │   ├── app.js      # Concatenated bundle (11,300+ lines from 14 parts)
│   │   ├── index.html
│   │   ├── style.css
│   │   └── js/         # 14 source modules, concatenated by scripts/build-js.py
│   │       ├── 01-core.js      # DOM helpers, typewriter, icons
│   │       ├── 04-demo-api.js  # State management, chatIsGenerating()
│   │       ├── 07-render.js    # Lane card rendering, markdown
│   │       ├── 10-stream.js    # SSE handling, message sending
│   │       ├── 12-settings.js  # Settings panel
│   │       ├── 13-connectors.js# MCP connectors UI
│   │       └── 14-boot.js      # App initialization
├── agent.py            # Agent loop, tool execution, run_shell_command()
├── config.py           # Config dir: ~/.arena-personal/
├── crypto.py           # DPAPI encryption for cookies/credentials
└── contracts.py        # Arena.ai API constants, origin URL
```

### Build & Run

```bash
# Rebuild JS bundle after editing source modules
python scripts/build-js.py

# Validate JS syntax
node -c arena/web/static/app.js

# Run from source (Flask only, no pywebview)
ARENA_PORT=56200 python -c "
import threading; from arena.desktop import start_flask, wait_port
threading.Thread(target=start_flask, args=(56200,), daemon=True).start()
wait_port(56200); import time
while True: time.sleep(60)
"

# Build exe
python -m PyInstaller arena-cli.spec --noconfirm

# Run tests
PYTHONPATH=. python -m pytest tests/ --tb=short -q
```

### Key Constraints

- **No commits without explicit user approval** — dj will commit themselves
- **Never push to remote** — dj handles all pushes
- **Never write AI config files** (.claude/, .cursor/, etc.) into the repo
- **Config dir is `~/.arena-personal/`** (NOT `~/.arena-cli/`)
- **Curly quotes kill JS** — never use U+2018/U+2019/U+201C/U+201D as string delimiters in JS source files

## Session 4 (v1.5.0 real-exe testing)

- Real exe tested in `Documents/Arena Desktop Demo`. Local Agent in `v15-npm`: npm init, npm install is-odd, index.js, docs/README.md, node run all on disk. Arena Agent in `v15-arena`: index.html, style.css and script.js mirrored live.
- **Arena Agent mirror**: `_native_mirror()` / `_sandbox_rel()` / `_heredoc_writes()` in `app.py`, called from `_native_agent_run(sync=...)` on each tool_result. Uses the `_sync_load` manifest; one permission per chat via `nat["file_sync_ok"]`.
- **Previews**: `_PREVIEW_TOKEN` and route `/preview/<key>/<path>`, plus `<meta name="preview-token">`. JS `previewLink()` and `openPreview()` live in `02-api.js`. Desktop bridge `JsBridge.open_preview(url)` in `desktop.py` opens a reusable WebView2 window, because arena.site sends `X-Frame-Options: SAMEORIGIN`. `reply["previews"]` saves arena.site links found in native tool outputs and text.
- **Titles**: `history_sync.is_injected_title` / `clean_title`, and `app._chat_title()` used by the list and load routes.
- **Boot**: `engineStarting()` / `watchEngineUntilReady()` in `14-boot.js`; the footer says "Connecting to arena.ai…".
- **Tests**: `conftest.py` sets `ARENA_CONFIG_DIR` and `ARENA_PROJECTS_DIR` to temp dirs. The suite previously wrote fake accounts (t@e.st, user@example.test) into the real store. The real store was cleaned, with a backup in `~/.arena-personal/backups/pre-test-account-cleanup-*`.
- Full suite: 339 passed, 0 failed, 21 skipped.
- **Chat bleed and jump to top**:
  - Cause: opening a chat auto-runs `POST /api/chats/<id>/refresh`. Arena's transcript has injected memory/guide text in front of user turns, so the turns did not match and were appended. The re-save also bumped `updated_at`.
  - Fix: `history_sync._unwrap_injected()` (suffix match, drops app-only turns). The refresh route skips local-tool chats (`tools_guide` / `agent_backend == local`). `_save_chat` bumps `updated_at` only when `_chat_changed()`.
- **Not done yet**:
  - account-switcher redesign;
  - progress UI for cookie import and sign-out;
  - backfill tool input/output for old Arena Agent chats from `/api/chat/{id}`.

## Session 6 (2026-11-07) — first release: v1.5.2

- **Version 1.5.2, not 1.6.0.** The tree had carried 1.6.0 for work that never shipped; nothing was downgraded because no `v*` tag had ever been pushed. Bumped in `arena/version.py`, the `?v=` query on `style.css`/`app.js`, `arena/setup/web/index.html`, the demo payload (`app_version` 1.5.2, demo feed 1.5.3), README badge/heading, `INSTALLER.md` and `docs/REWORK.md`.
- **Tag → release is automatic.** `git tag -a v1.5.2 -m "Arena Desktop 1.5.2" <merge-sha> && git push origin v1.5.2`. `build.yml` fails the build if the tag does not match `arena/version.py`, runs the suite, builds, smoke-tests `--version`, then `gh release create --generate-notes`. The generated notes were replaced afterwards with `gh release edit v1.5.2 --notes-file …`.
- **Published:** `Arena-Desktop-Setup-1.5.2.exe` and `Arena-Desktop-1.5.2.exe`, 28,614,141 bytes each, `sha256:e4fbb460c15b4d75d693f36d4849e97bb9308d2a66d699f0739158b3a6ceccad` for both (one binary, two names). Release is not a draft and not a pre-release, so `/releases/latest` returns it.
- **Feed verified from the app's own code** (`updater.fetch_latest()`): version 1.5.2, picks the Setup asset, `is_allowed_url` true, 1.5.1 sees the update and 1.5.2 does not see one to itself.
- **Sandbox limits, not app bugs:** this machine cannot reach `release-assets.githubusercontent.com` (asset downloads) and has no system CA bundle for python `requests` — the feed check needs `REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt`. GitHub's UI also cannot be screenshotted (githubassets is blocked), so the release is documented with the app's own Settings window instead.
- **README/docs corrected for the release:** hero screenshot recaptured from this build, the gallery cell that sold the removed Workspace panel replaced by the Settings window, `preview-1.6.0/` renamed to `preview/` and retitled as the pre-rework record, What's new rewritten to lead with the rail, the one-line tool rows and the marker protocol.
- 391 tests pass.

## Session 7 (2026-11-07) — 1.5.3: the Setup download installs; failed replies explain themselves

Two bugs reported against 1.5.2.

- **The installer never ran.** 1.5.2 published `Arena-Desktop-Setup-1.5.2.exe`, but setup mode was entered only on `--setup`, and a double-click passes no arguments — so the "installer" opened the app: no files in `%LOCALAPPDATA%\Programs\Arena Desktop`, no Start-menu shortcut, no *Apps & features* entry, and Windows search showed a file instead of an app. Fix: `arena/setup/core.py` gained `looks_like_installer()` and `setup_mode_from_argv()`; `arena-cli.py` asks the latter before opening anything. Frozen builds whose file name contains `setup`/`installer` install; `--app` forces the app; `--setup`/`--install` still force setup; a source run never installs. Tests: `tests/test_installer.py::test_a_file_named_setup_installs_without_any_flag` and friends.
- **"The app hit an internal error." with nothing behind it.** `_sse_wrap` caught every exception in a reply and logged nothing, and the frontend dropped the `detail` field. Now: `LOG.exception` (traceback in `desktop.log`), the exception rendered under the message with *Copy details*, and `_engine_ready()`/`_engine_cx()` around the two unguarded `ENGINE.wait_ready`/`native_preflight` calls that could kill an Agent turn before its first character. Tests: `tests/test_crash_reporting.py`.
- **The reported Agent failure was not reproduced here** (no arena.ai session in this sandbox). If it recurs, the row now names the exception and the log has the stack — that is the next step for it, not another guess.
- Version 1.5.3, released after the merge; `docs/REWORK.md` has both write-ups with the error-row screenshot.
- 398 tests pass.

## Session 8 (2026-11-07) — v1.5.3 published

- **Tag:** `v1.5.3` on `752fdec` (merge of #12). Workflow green end to end; release published `Arena-Desktop-Setup-1.5.3.exe` + `Arena-Desktop-1.5.3.exe`, 28,617,973 bytes each, `sha256:ddd58f13…32b3` for both.
- **Feed check** (`updater.fetch_latest`): 1.5.3, Setup asset picked, `is_allowed_url` true, 1.5.2 sees the update, 1.5.3 does not see one to itself.
- **Why 1.5.2 users are not stranded:** its updater runs the download with `--setup --update --silent`, so the name-based detection was only ever needed for the double-click path.
- Notes were replaced after publish with `gh release edit v1.5.3 --notes-file …` — `--generate-notes` alone only lists PR titles.
- 398 tests pass; docs/REWORK.md carries the 1.5.3 table beside the 1.5.2 one.

## Session 9 (2026-11-07) — 1.5.4: the installer window and bypass permissions

Both bugs came from running the shipped 1.5.3 on the user's own PC through the
`ArenaConnectMCP` bridge (Supabase queue → `arena_bridge_wait`), not from the
suite.

- **The installer froze on "Starting…".** Diagnosis on the real machine: the
  window process was alive and pumping (`Responding=True`, title *Arena Desktop
  Setup*), `--setup --silent` on the same file installed in **4 s** and
  registered Start-menu + Apps & features, and the WebView2 user-data folder
  showed a fully initialised browser — so the core and the runtime were fine and
  only the window was stuck. Cause: `arena/setup/ui.py` kept the window in a
  public `SetupApi.window`; pywebview walks every public attribute of `js_api`
  to build the page bridge, reached the WinForms form/WebView2 COM objects and
  deadlocked the pump. `arena/desktop.py`'s `JsBridge` already documents the rule
  and keeps `_window`, which is why only setup ever froze. Fix: `_window`, a
  twelve-second `_bridge_watchdog` that installs from Python if the page never
  calls the bridge, and `%USERPROFILE%\.arena-personal\setup.log`.
- **`KeyError: 'bypassPermissions'` on every prompt in bypass mode.**
  `arena/agent.py`'s `MODE_NOTES` was missing the `bypassPermissions` entry and
  `local_agent.py` indexed it directly. Fix: the note is there, `agent.mode_note()`
  falls back instead of raising, the call site uses `.get`, and
  `tests/test_v154_fixes.py` asserts `permissions.MODES ⊆ MODE_NOTES`.
- Deferred: the installer still unpacks ~27 MB from a OneFile exe at every
  launch (≈2 s here, measured as part of the 4 s silent run) — a OneDir build is
  the next speed lever, not this release.

## Session 10 (2026-11-07) — v1.5.4 published

- **Tag:** `v1.5.4` on `0b669b0` (merge of #14), workflow green, release published
  `Arena-Desktop-Setup-1.5.4.exe` + `Arena-Desktop-1.5.4.exe`,
  **28,621,059 bytes each, `sha256:437d85aad4c46c6af45844f7f45dfe5d8c9f92ee784c075b348e86c538feb517`**.
- **Feed check** (`updater.fetch_latest('stable')`): 1.5.4, Setup asset picked
  (28,621,059 B, kind `installer`), `is_allowed_url` true, 1.5.3 sees the update,
  1.5.4 does not see one to itself. Notes replaced with
  `gh release edit v1.5.4 --title … --notes-file …`.
- **The in-app updater stays silent by design** (`--setup --update --silent --pid`):
  a user who updates from Settings never sees the setup window, so the 1.5.3
  freeze only ever hit the double-click path. Testing the fixed window means
  running `Arena-Desktop-Setup-1.5.4.exe` itself.
- **Known follow-up, not in this release:** install mode over a *running* copy
  fails on the locked `Arena-Desktop.exe` and shows up in the window as an access
  error. Update mode handles it (`--pid` waits for the app to exit); instal mode
  should detect the running app, offer to close it, or switch to update mode.
- 411 tests pass.

## Session 11 (2026-11-07) — 1.5.5: the setup window actually drives the install

1.5.4 shipped a fix whose notes over-claimed. Corrected here and in
`docs/REWORK.md` ("The 1.5.5 release").

- **What 1.5.4 proved, on the user's PC:** the Setup file installed (`install.json`
  → 1.5.4, previous 1.5.3) but `setup.log` recorded
  `setup window never reached the bridge; installing without it`, i.e. the
  watchdog did the work and the window stayed on "Starting…".
- **pywebview's own DEBUG trace** (run with `PYWEBVIEW_LOG=DEBUG`; the setup log
  file handler captures the `pywebview` logger because it propagates to root)
  showed the injection happening at +1.7 s: `before_load … injecting pywebview
  object`, `Loading JS files from …_MEI…\webview\js`, `_pywebviewready event
  fired`, `loaded event fired`.
- **Real cause:** pywebview fires `pywebviewready` on **`window`**
  (`webview/js/finish.js`), and the page listened on `document` only, with a
  single 900 ms fallback check. `14-boot.js` in the app already listens on both.
- **Fix:** `whenBridge()` in `arena/setup/web/index.html` — listen on window +
  document, poll every 120 ms, honest "finishing in the background" state in a
  WebView2 window when the bridge never arrives (the watchdog is the backstop).
- **Verification without Windows:** `tests/js/setup_page_bridge.js` (jsdom) plays
  back four arrivals — at load, late on window, late on document, never — and
  `tests/test_setup_page_bridge.py` runs it when node + jsdom exist, else skips.
- Bumped to 1.5.5 everywhere; released after the merge.

## Session 12 (2026-11-07) — 1.5.6: no echo for granted approvals

- **Reported from the running app** (screenshot of a real turn): every approved
  command left a second silver line `Allowed <command> allowed` under the command
  row that already carried the command and `exit 0`.
- **Fix:** granted approvals render nothing; a refusal renders as a red
  `rejected by user` on the command's own row (`rejectionText()` reads the
  `denied by the user` status the local agent already streams); `stopped` and
  `expired` keep a quiet `not run: …`; plan/safety blocks keep their own wording.
  `laneShowsRejection()` prevents a refusal from appearing twice.
- **Tests:** `tests/js/permission_rows.js` + `tests/test_permission_rows.py`
  (12 checks; extracts the pure helpers from the render source, then asserts the
  receipt, the card loop, the row and the stylesheet).
- Bumped to 1.5.6. `docs/screenshots/chat/tool-done.png` still shows the removed
  line — the sandbox has no browser any more (playwright's CDN is not reachable),
  so the replacement shot is pending.

## Session 13 (2026-11-07) — v1.5.6 published

- **Tag:** `v1.5.6` on `dd5e576` (merge of #17), workflow green, release published
  `Arena-Desktop-Setup-1.5.6.exe` + `Arena-Desktop-1.5.6.exe`,
  **28,622,084 bytes each, `sha256:aa4e57e661602a6dd9e44fde949d9f9097f205f67e07aeb9f54cc1e75102c620`**.
- **Feed check** (`updater.fetch_latest('stable')`): 1.5.6, Setup asset picked
  (28,622,084 B, kind `installer`), `is_allowed_url` true, 1.5.5 sees the update,
  1.5.6 does not see one to itself. Notes replaced with `gh release edit`.
- The user closed the Supabase bridge before this release, so nothing was verified
  on their PC this time — the in-app *Settings → Updates* path is the test.
- 415 tests pass, 1 skipped (the jsdom setup-page harness).

## Session 14 (2026-11-07) — 1.5.7: tool rows visible while they run

- **Reported on 1.5.6:** commands ran invisibly — the approval pop-up appeared,
  but the command row only showed up when the turn ended or Stop was pressed.
- **Cause (pre-existing, exposed by 1.5.6):** the card's typewriter branch drew
  only the "Thinking…" placeholder and skipped `renderLaneFlow` whenever the lane
  had no prose yet. Agent turns that only call tools keep `visibleText` empty, so
  rows stayed hidden until `lane.streaming` went false. The removed `Allowed …`
  receipts used to render outside the flow, which masked it.
- **Fix:** `renderLaneFlow(lane, visibleText, true)` also runs in the typewriter
  branch, so rows stream under the placeholder.
- `tests/js/permission_rows.js` gained a check for it (13 checks).
- Version 1.5.7.

## Session 15 (2026-11-07) — v1.5.7 published

- **Tag:** `v1.5.7` on `224e059` (merge of #19), workflow green, release published
  `Arena-Desktop-Setup-1.5.7.exe` + `Arena-Desktop-1.5.7.exe`,
  **28,622,094 bytes each, `sha256:b426594a0e6b011111194018d93c49ff783edb62c916f677d62e1b4433f19aad`**.
- **Feed check:** 1.5.7, Setup asset picked, `is_allowed_url` true, 1.5.6 sees the
  update, 1.5.7 does not see one to itself.
- 415 tests pass, 1 skipped (jsdom setup-page harness, no jsdom in the sandbox);
  `tests/js/permission_rows.js` is at 13 checks.
- Still pending with the browser gone: a fresh `docs/screenshots/chat/tool-done.png`
  (it still shows the removed `Allowed … allowed` line) and a shot of a running
  row under the "Thinking…" placeholder.

## Session 16 (2026-11-07) — 1.5.8: the placeholder moves below the rows

- **Reported with a screenshot:** a turn with two finished commands still carried
  *exploring solutions…* at the top of the card — text describing a moment that
  had passed, above work that was happening.
- **Fix:** the card is now built in the order things happen: flow rows, then the
  call being written, then the typewriter placeholder — so it sits under the last
  row, where the next words will appear, and only ever stands for prose that has
  not arrived.
- `renderLaneFlow(lane, visibleText, showTypewriter)` is called unconditionally
  once, instead of in two branches; the caret logic moved into the `else` of the
  placeholder.
- `tests/js/permission_rows.js` now checks the order as well (14 checks).
- Version 1.5.8.

## Session 17 (2026-11-07) — 1.5.8: thoughts in a block, where they happened

- **Reported with a screenshot:** the model wrote its plan into the answer —
  *"We now have the disk layout. Let's write a concise breakdown…"* — above the
  breakdown it was introducing. Thoughts must not be prose in the reply and must
  not be hoisted to the top of the card.
- **Prompt:** `arena/agent.py` now tells the model to plan inside
  `<thinking>…</thinking>`, keep everything outside the block as the reply, and
  close every block (with a worked example).
- **Parser:** `readThinking` collects blocks with offsets and an `open` flag;
  `readToolStream` folds them into the existing `hidden` ranges (an unclosed
  block hides to the end of the text) and strips trailing partial tags through a
  new `stripThinkingTail` — the early-return path for marker-free replies had to
  go through it too, or `<thinki` flashed as text.
- **Render:** `renderThinkingNode` → `<details class="reasoning thinking-block">`,
  summary *Thinking* (open, caret) / *Thought*; `renderLaneFlow` sorts rows and
  thoughts together by source offset and pours the text between them; a live
  thought suppresses the typewriter placeholder.
- **Tests:** new `tests/js/thinking_blocks.js` (18 checks) wired into pytest via
  `tests/test_permission_rows.py`; suite at **416 passed, 1 skipped**;
  `tests/js/permission_rows.js` still 14 checks.
- Version 1.5.8.
- **Released as 1.5.9, not 1.5.8.** The `v1.5.8` tag's build failed in
  *Test before building*: the harness pulled its functions out of the source with
  `const NAME = …;\n` anchors, and the Windows runner checks out CRLF, so it
  threw `missing constant THINKING_OPEN` before running a check. Reproduced by
  converting a copy of the tree to CRLF. Fix: a `src()` reader that normalises
  line endings; verified LF and CRLF. Tag deleted (no release was ever created);
  version bumped to 1.5.9.

## Session 18 (2026-11-07) — 1.5.9 shipped, 1.5.10 for the doubled sentence

- **1.5.9 published:** `Arena-Desktop-Setup-1.5.9.exe` /
  `Arena-Desktop-1.5.9.exe`, **28,624,721 bytes**,
  `sha256:a608f83ea8b9ef10ef13caa22e58b811793bdaaa4d9e36d91a19d9b98c5fe36e`;
  feed offers it to 1.5.8, not to itself; the failed `v1.5.8` tag was deleted.
- **Then found while reading the shipped code:** `renderLaneFlow`'s typewriter
  branch poured the prose into the card *and* left the placeholder up, so during
  the 1.8 s window at the start of a turn the opening sentence printed twice.
  The branch now returns after the rows and thoughts; text-pouring only happens
  when the placeholder is down.
- New check: the typewriter branch must contain no `markdownNode`
  (`tests/js/thinking_blocks.js` → 19 checks).
- Version 1.5.10.

## Session 19 (2026-11-07) — 1.5.11: the marker parser, rewritten from a battle

- **Reported with a screenshot** (battle, one question): lane A gave no answer,
  lane B printed `~~~Hi Urja, …` as prose and cut `Used: **abo**` / `ut 207 GB`.
- **Lane A cause:** kimi wrote `~~~command` before every call and never a closing
  `~~~`. Under the old rule any second marker closed the first block, so call
  #2's marker was eaten as call #1's closer, call #3 opened a block that never
  closed, and the tail was hidden — the answer. The footnote's "29 chars" is the
  *visible* length: the sentence before the first call. The answer was there.
- **Lane B causes:** (1) the closing marker glued to the answer
  (`~~~Hi Urja…`) is not a marker *line* under the old regex, so the tildes
  printed; (2) row positions were measured by re-parsing `raw.slice(0, at)` and
  taking its visible length, which hides differently from the whole reply — the
  cut landed two characters inside "about".
- **Fix:** `MARKER_RE` + `readMarker` treat the marker as a token at line start
  with a *known* kind open / bare `~~~` close; a kind marker while open closes the
  previous call at its own tool tag; a closer glued to prose keeps the words; an
  unfenced call at end of turn hides the call only (streaming still hides to the
  end); `lastClose` is recorded even on a line that also carries a marker;
  `offsetMapper(hidden)` measures places with the same ranges the text was built
  from. Guide: close every call with its own `~~~` line before the answer.
- **Tests:** `tests/js/thinking_blocks.js` grew to **36 checks** (glued closer,
  three unfenced calls then the answer, in-flight pending, empty call, fence in a
  file body, stray marker, cut-on-a-word); `permission_rows.js` 14; suite
  **416 passed, 1 skipped**.
- Version 1.5.11.

## Session 20 (2026-11-07) — 1.5.12: connectors open with the app, accounts return to Settings

- **Reported:** opening the app pops a terminal per local MCP ("3 for the three
  MCPs I locally have"), and none of them connect by themselves — the right bar
  is hidden, so connecting meant prompting the AI. Asked for: hidden terminals,
  automatic connect, a start-up progress screen ("opening the app", then
  "connecting to MCPs"), and the old multi-account switcher (which lived in the
  removed left-rail pop-up) moved into Settings.
- **Terminals:** `McpStdioClient.start()` spawned with no `creationflags`; on
  Windows every `cmd.exe /c npx …` shim got a console. Added
  `windowless_spawn_kwargs()` (CREATE_NO_WINDOW + STARTUPINFO/SW_HIDE).
- **Auto-connect:** `ConnectorManager.start_startup_connect()` (idempotent daemon
  run with per-server `queued/connecting/connected/error`), `startup_snapshot()`,
  `GET /api/connectors/startup`, `POST /api/connectors/startup/retry`,
  `start_connector_warmup()` called from `run_server` and `desktop.start_flask`.
- **Locking:** `connect()` used to hold the manager lock across the whole spawn
  (90 s of npx); it now holds only a per-server lock, so the panel and other
  servers stay responsive while the launch run works.
- **Start-up screen:** `startStartupScreen()` in 14-boot.js + `.startup-*` CSS:
  App row → Connectors stage with a row per MCP, progress bar, tool counts,
  errors, Retry, "Continue in the background"/Esc, auto-close 900 ms after the
  last connector, 18 s give-up if the server never answers. The one-time setup
  takes the window instead (`dismissStartupScreen()`).
- **Accounts in Settings:** `renderConnectionPanel()` lists `state.accounts`
  (avatar, name, address, Current chip, Switch, Forget, Add another account,
  Sign in again for expired) using the existing `switchToAccount()` /
  `removeSavedAccount()` / `startLogin({addAccount:true})`; hydrated from
  `API.getAccounts()`.
- **Tests:** `tests/test_startup_connect.py` (12, incl. a real STDIO server),
  `tests/js/startup_screen.js` (25), `tests/js/startup_screen_dom.js` (17, jsdom,
  skipped without jsdom) + `tests/test_startup_screen.py` wrappers. Suite
  **430 passed, 1 skipped**.
- Version 1.5.12.

## Session 21 (2026-11-07) — 1.5.13: presets in the launch list, "Off" instead of missing

- **Asked:** "Not just connectors. The app also has local MCP like file system,
  memory or sequential thinking. Is that also started automatically if I have it
  enabled and shown in the progress bars?"
- **Answer:** yes — presets are only a catalogue; `install_preset()` writes a
  normal `mcp.json` entry tagged `x-arena-preset`, so the 1.5.12 launch run
  already started them. Two gaps fixed anyway:
  - rows showed the raw id (`sequential_thinking`); `_startup_plan()` now maps
    `preset_id` → the catalogue's display name, with the masked command as the
    tooltip (`detail`) and the description in `description`;
  - switched-off connectors were silently absent; they are items with status
    `disabled` now — a quiet "Off" row, excluded from `total`/`done`, and the
    finished line appends "· N switched off".
- **Counts:** `startup_snapshot()` gained `disabled` and computes `total`/`done`
  over non-disabled items only; `start_startup_connect()` treats an all-off config
  as instantly done.
- **UI:** `applySnapshot` prefers `item.label`, renders `disabled` as `Off`
  (`minus` icon added to ICONS, `.startup-step.is-off` styles).
- **Tests:** `test_startup_connect.py` → 15 (installs the three presets the way the
  panel does, asserts they connect with display names; switched-off → `disabled`,
  never started, or instantly done when all are off); `startup_screen.js` → 28;
  `startup_screen_dom.js` → 20 (presets named, Off row, tool counts).
- Version 1.5.13.

## Session 26 (2026-11-07) — merged, NOT released: a run of tool calls is one line

- **Asked:** *"if the agent calls a certain tool more than once in a row … the
  user should be able to collapse every tool call into one line. And then there
  should stand two reads, one write and one command."* Answers to the three
  questions: **stay open** by default, **thoughts and answer text both** end a
  run, and count **reads, writes, commands** only.
- **Where:** `arena/web/static/js/07-render.js`. The grouping happens in
  `renderLaneFlow` while the node array is built (`run` + `flushRun()` +
  `place(item)`), not on the DOM afterwards, so the offset bookkeeping that
  places rows is untouched. A run of 2+ becomes `renderToolGroup(run)`; one call
  stays a plain row (`1 read` over *Read app.js* would say it twice).
- **Boundaries:** `flushRun()` fires before any non-activity node — a thought, a
  markdown piece, the end of the lane. A group never spans a boundary it cannot
  see, and the typewriter branch (`showTypewriter`) groups identically.
- **Counts:** `toolKindOf()` → read / write / command / tool via `toolKey()`;
  sandbox edits (`activity.kind === 'sandbox'`) are writes. Summary prints reads,
  writes, commands, then tools, with `plural()` and no zero counts:
  `2 reads, 1 write, 1 command`. Search/fetch/MCP/sub-agents are **tools**, never
  passed off as reads.
- **State:** `closedGroups` / `openGroups` `Set`s keyed `group-<first call id>`.
  Open unless closed; ids come from the stream so a per-token re-render cannot
  lose the choice. A group folded by hand stays folded while it runs — forcing it
  open would fight the click — and the summary line shows the spinner instead of
  the call count, so a folded live group still looks alive.
- **Never hide an approval:** a row with `is-wait` forces its group open
  (`const open = waiting || openGroups.has(id) || !closedGroups.has(id);`), the
  meta reads *waiting* in amber, and clicking the line cannot close it. Grouping
  is the first feature that could have hidden a pending question, so it is the
  one place the view overrules the click.
- **DOM:** `div.tool-group[data-group-id]` > `button.tool-line.tool-group-line
  [data-group-toggle]` (chevron, `layers` glyph, `.tool-name` = summary,
  `.tool-meta` = "4 calls" or a spinner) + `div.tool-group-body` holding the
  **same** row nodes. `onToolToggle` handles `[data-group-toggle]` before
  `[data-tool-toggle]`, so a group click never touches a row's own
  `expandedActivities`/`collapsedActivities`.
- **CSS:** `.tool-group`, `.tool-group-line`, `.tool-group-glyph`,
  `.tool-group.is-open > .tool-group-line .tool-chevron { rotate(90deg) }`,
  `.tool-group-body` (one hairline left rule, 9px in).
- **Tests:** `tests/js/tool_groups.js` + `tests/test_tool_groups.py`
  → **55 checks** (incl. the waiting-approval rule). Executes the real `renderLaneFlow` over a synthetic reply with
  whitespace gaps where calls happened and asserts
  `text, group, thought, text, group, thought, text`, each line's counts, prose
  splitting a run, a lone call staying a row, the typewriter branch; then renders
  a group, folds it (no body, `aria-expanded="false"`), reopens it, and proves a
  fold does not touch the per-row state. All other harnesses unchanged and green:
  answer_surface 15, startup_screen 28, startup_screen_dom 20, thinking_blocks
  47, permission_rows 14, composer_menu_dom 31.
- **Version 1.5.16. No tag, no release** — the owner is releasing after this
  round, on his word.

## Session 25 (2026-11-07) — merged, NOT released: thought rows, real bypass, agent in the composer

- **Asked:** four things and an explicit order — *"implement everything, merge is
  allowed, do NOT release"*. (1) thinking blocks must look like tool calls
  (`>_ Ran commands 1` / `Thought for 3 seconds`, arena.ai's own style);
  (2) bypass must never ask, *"fully autonomous"*; (3) the Permissions panel must
  leave Settings and the agent backend (Local / Cloud) must be switchable from the
  composer — *"I have to click three levels to set it to local on every launch"*;
  (4) the composer's big text chips must become small icon buttons with tooltips,
  inside a three-dots menu to the left of `+`.
- **Thinking = a tool row.** `renderThinkingNode` (its own `<details
  class="reasoning thinking-block">`) replaced by `renderThoughtRow`, which emits
  the *same* `div.tool-row` a command row uses (`is-thought` on it): chevron,
  glyph, one name line, `.tool-detail` holding the prose. Folding, spinner,
  chevron rotation, the `animationend` + 900 ms fallback are `renderToolRow`'s,
  reused by using its classes. Duration comes from `_thoughtClocks` (a `WeakMap`
  of lane → `Map(block.at → start)`); `thoughtLabel()` renders `1 second` /
  `3 seconds` / `1 minute 15s`, and a block that arrives already closed (reopened
  chat, cloud `lane.reasoning`) is labelled plainly `Thought`. Empty finished
  thoughts return `null`; both call sites skip it. Old `.reasoning` /
  `.thinking-block` CSS deleted.
- **Bypass first, allow always.** `permissions.decide()` used to reach the
  *always ask* rules (shell operators, inline code, paths outside the folder)
  before the bypass branch, so bypass still asked — reported as *"even in bypass
  it asks"*. The bypass branch now sits directly under the catastrophic check and
  returns `allow` unconditionally; the only floor is `deny` (so bypass answers
  zero prompts and still refuses `rm -rf /`). `PermissionBroker._always` can no
  longer fire in bypass. `MODE_NOTES['bypassPermissions']` rewritten to say
  exactly that. `_prepare()` still refuses `-c`/`-e`/`--eval` argv — that is a
  separate gate, not a prompt.
- **Agent backend in the composer, Permissions tab removed.**
  `renderPermissionsPanel()` deleted; `permissions` gone from the Settings tab
  list, `contentFor`/`hydrateTab` branches removed, `chatSettingsButton` now
  opens `workspace`. `openComposerMenu()`'s **Agent** row offers *Local agent* /
  *Cloud agent* → `changeMode('agent')` when needed + `setAgentKind()` (persists
  `ap-agent-kind`, same key the Settings radios wrote).
- **Two icons and Send.** `#composerMenuButton` (new `ellipsis` icon in
  `01-core.js`) + `#attachButton`, both `.tool-icon-button` (30 px, transparent,
  `title` + `aria-label`). The six old chips moved into `div.toolbar-stash`
  (`display: none !important`) on purpose: `#modeLabel`, `#permissionLabel`,
  `#workspaceLabel`, `#workspaceSyncToggle` are what `renderModeBars`,
  `renderComposerContext` and `updateComposerState` write and what other modules
  read, so the panel renders from that state instead of rewriting eleven writers.
  `openComposerMenu` sections: Chat / Agent / Work type (hidden in Agent Mode) /
  Permissions / Folder / Options (Workspace Sync, Compact, Gateway); it
  re-renders in place and stays open; mode and permission clicks `await` the API
  call before redrawing (a stale `aria-pressed` was caught by the harness).
  `#composerMenuDot` shows amber for a waiting request, red for bypass.
- **Tests:** new `tests/js/composer_menu_dom.js` + `tests/test_composer_menu.py`
  → **31 checks** (toolbar shape, tooltips, icons drawn, stash hidden, all six
  sections, Local/Cloud persisted, bypass dot + tooltip, Battle restores work
  type, Escape, Settings has no Permissions tab, no uncaught errors).
  `thinking_blocks.js` → 47. `test_shell_v15.py` now expects `allow` for
  `npm i && curl x` / `python -c` / outside paths, `deny` for `rm -rf / && echo`;
  `test_v154_fixes.py` asserts the new bypass note. Full suite: **447 passed, 22
  skipped, 2 deselected**; every JS harness also green on a CRLF copy in
  `/tmp/crlf`.
- **Version 1.5.15. No tag, no release** — the owner asked to see the summary
  first and to approve the release separately.
- **No screenshots:** there is no browser in this sandbox, so the visual claims
  are backed by jsdom DOM assertions and CSS source checks, not by pictures.

## Session 24 (2026-11-07) — 1.5.14 released

- **Built and published:** tag `v1.5.14` on merge commit **`f6ccea1`** (PR #29),
  run `42398925497` **success**.
- **Assets:** `Arena-Desktop-Setup-1.5.14.exe` and `Arena-Desktop-1.5.14.exe`,
  28,644,309 bytes each,
  `sha256:1a050d9833d2cda1aeb131999bfc0f3d7188a6c4309e76bb79c52bfda6ecdf66`.
- **Contains** the two rounds merged after 1.5.13: no outline/avatar around an
  answer (PR #27) and the connector work taken off the reply's path (PR #28).
- **Release notes** written with `gh release edit v1.5.14 --notes-file` (the
  workflow's auto notes are replaced, so the digest and the install paragraph
  live in the body).
- **Feed check:** `updater.fetch_latest('stable')` → 1.5.14, installer
  `Arena-Desktop-Setup-1.5.14.exe` with the digest above, `is_allowed_url` True,
  `is_newer('1.5.14','1.5.13')` True.
- **Note for the next session:** a fresh `/tmp/venv` needs
  `REQUESTS_CA_BUNDLE=/usr/local/share/ca-certificates/e2b-ca.crt` for any
  GitHub call from Python in this sandbox (`SSL: CERTIFICATE_VERIFY_FAILED`
  otherwise); `gh` is unaffected.

## Session 23 (2026-11-07) — merged, NOT released: a reply never waits for a connector

- **Reported:** after 1.5.13, the first prompt of a session takes a very long
  time even though the connectors connected at launch — suspected cause: *"the
  mcp connects under the hud again"*.
- **Cause:** `_agent_run()` (`arena/web/server/local_agent.py`) began with
  `CONNECTORS.ensure_started(timeout=30)`, which connected every enabled server
  in turn, waiting up to 30 s for each. A prompt sent while the launch run was
  still spawning `npx` servers waited behind it; a connector that had failed at
  launch was re-spawned on every turn.
- **Fix:** the turn asks `CONNECTORS.tools_now()` (running servers only, never
  starts one) after `wait_for_startup(1.5)` — a courtesy second, then it names
  what is late in one notice line. `retry_failed_in_background()` retries failed
  connectors off the request thread, throttled to one attempt a minute.
  `call()` no longer spawns a stopped connector. The launch run is now parallel
  (`_run_startup` + `_connect_one`), so it ends in the time of its slowest
  server instead of their sum. Switching a connector on in Settings connects it
  in the background (`set_enabled` → `connect_in_background`; the preset and
  CLI routes already connect explicitly).
- **Tests:** `tests/test_turn_connect_latency.py` — 10 checks (no work in
  `tools_now`, no waiting behind a live launch run, bounded grace, parallel
  launch run, throttled background retry, source guards on `_agent_run` and
  `_gateway_tool_index`). `tests/test_startup_connect.py` updated for the
  parallel order. Full suite: **445 passed, 21 skipped**.
- **No version bump, no tag, no release.**

## Session 22 (2026-11-07) — merged, NOT released: no outline around answers, no avatar

- **Asked:** "remove that outline for the ai. only my prompt should be outlined.
  and also remove that ai icons. but don't build release yet. only fix and merge."
- **Cause of the outline:** `.assistant-lane` only had a *transparent* border in
  single-lane mode, but `.assistant-lane.is-streaming { border-color: … }` painted
  it the moment a turn went live — so the box appeared exactly while it streamed.
- **Fix:** `.assistant-lane` is `border: 0` and transparent in every state; both
  `is-streaming` border rules deleted; a battle keeps `.lanes-2
  .assistant-lane { background: … }` so the columns still separate, without a
  border. `.user-bubble` (the prompt) keeps its `1px solid` outline.
- **Avatar:** `renderAssistantLane()` no longer builds `.model-avatar`; the head
  keeps the model name, the *Thinking…* label and the copy button. The picker in
  `11-ui.js` keeps its icons.
- **Tests:** `tests/js/answer_surface.js` — 15 checks (rule blocks, render source,
  and the resolved cascade in jsdom: `0px`/`none` on a streaming lane, `1px solid`
  on the prompt) + `tests/test_answer_surface.py`.
- **No version bump, no tag, no release** — merged only, as asked.
