<p align="center">
  <img src="assets/logo.svg" alt="Arena Desktop logo" width="96" />
</p>

<h1 align="center">Arena Desktop</h1>

<p align="center">
  <b>An unofficial desktop app for <a href="https://arena.ai">arena.ai</a> — chat, battles, a real Agent Mode,
  and models that can work on the files on your machine, all in one window.</b>
</p>

<div align="center">
  <a href="https://github.com/Arvex-GH/ArenaAI-Desktop-Major-Rework/stargazers"><img src="https://img.shields.io/github/stars/Arvex-GH/ArenaAI-Desktop-Major-Rework?style=flat-square&color=crimson" alt="GitHub stars" /></a>
  <a href="https://github.com/Arvex-GH/ArenaAI-Desktop-Major-Rework/releases/latest"><img src="https://img.shields.io/github/v/release/Arvex-GH/ArenaAI-Desktop-Major-Rework?style=flat-square" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/version-1.5.4-brightgreen?style=flat-square" alt="v1.5.4" />
  <img src="https://img.shields.io/badge/models-400%2B-10B981?style=flat-square" alt="400+ models" />
  <img src="https://img.shields.io/badge/Platform-Windows_(macOS%20%26%20Linux%20planned)-blue?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT license" /></a>
</div>

<p align="center">
  <img src="docs/screenshots/home.png" alt="Arena Desktop home screen" width="900" />
</p>

> [!IMPORTANT]
> Arena Desktop is an **unofficial, community-built client**. It is not affiliated with or endorsed by Arena AI Inc.
> This is for **educational Purposes ONLY**. 
> You sign in with your own arena.ai account and are responsible for following arena.ai's terms.
> The app never solves human checks for you — when arena.ai asks for one, you complete it yourself.

---

## Contents

- [Download](#download)
- [What's new in v1.5.4](#whats-new-in-v154)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Run from source](#run-from-source)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Roadmap: macOS and Linux](#roadmap-macos-and-linux)
- [Translations](#translations)

## What's new in v1.5.4

### Bypass permissions stops killing the reply

With *Bypass permissions* selected, every message died before the model was
called: **KeyError: 'bypassPermissions'**. The agent guide knew four of the five
permission modes, and the fifth — the one whose whole point is "run it now" —
was the missing one, so the turn crashed while it was still being built. The
mode is in the guide now, an unknown mode falls back to the default text
instead of raising, and a test keeps `permissions.MODES` and the guide in step.

### The Setup window no longer freezes

1.5.3's installer opened its window, printed "Starting…", and then hung until
Windows greyed it out — the install never began. pywebview builds a page's
bridge by walking every public attribute of the `js_api` object; the setup
window kept its window reference in a public `window` attribute, so the walk
went from the bridge into the WinForms form and the WebView2 objects behind it
and deadlocked the message pump. The app itself already knew this rule (its
bridge documents it and keeps `_window`), which is why only the installer froze.
The reference is private now, and two safety nets came with it: a watchdog that
starts the install from Python if the page has not called the bridge within
twelve seconds, and `%USERPROFILE%\.arena-personal\setup.log`, so a setup
window that ever misbehaves leaves a trace.

### Previous: v1.5.3 — the Setup file installs

<details>
<summary>1.5.3: the double-clicked Setup file installs, and a failed reply explains itself</summary>

### The Setup download installs

1.5.2 shipped both files but only `--setup` opened the installer, and a
double-click passes no arguments: the file called *Setup* simply started the
app. Nothing went into `%LOCALAPPDATA%\Programs\Arena Desktop`, no Start-menu
shortcut appeared, *Apps & features* knew nothing about it, and Windows search
found a stray exe instead of an app.

The file's own name is the switch now. **Double-click
`Arena-Desktop-Setup-<version>.exe` and it installs** — per-user, no admin
rights, Start-menu shortcut, uninstaller, and from then on the app updates
itself. `Arena-Desktop-<version>.exe` still just runs, which is what the
portable download is for. `--app` forces the app from either file; running from
source never installs.

### A failed reply explains itself

A crash inside a reply used to say only *The app hit an internal error.* — no
exception, and no traceback anywhere. The row now shows the exception under the
message with *Copy details* beside it, and the full stack goes to
`%USERPROFILE%\.arena-personal\desktop.log`. Two unguarded calls into the
WebView2 bridge that could kill an Agent turn before its first character now
read as "the arena.ai window is not ready" instead.

</details>

### Previous: v1.5.2 — the rework itself

<details>
<summary>The first release of the rework: the rail, the one-line tool rows, the Settings row, the installer</summary>

The interface was rebuilt around the things you actually do — pick a mode, type,
watch the work happen. What is deliberately still missing is listed in
[docs/REWORK.md](docs/REWORK.md).

### A quieter sidebar

One column of rows instead of boxes: **New chat**, **Search**, **Projects**,
**Artifacts**, then your chats. Small caps for section labels, hairlines for
structure, no cards. The rail collapses to 76px of icons and comes back.

The account block and the footer's icon row are gone; both live behind a single
**⚙ Settings** row pinned to the bottom of the rail, where the chat list always
ends in the same place. *Terms of Use*, *Privacy Policy* and the cookie policy
moved into **Settings → About** next to the GitHub link, so the rail does not
end in small print.

### The conversation: one line per call

A tool call is one silver line — `Bash npm install && npm run build`,
`Writing src/hero.js` — and nothing else. It expands while it runs, folds itself
when it finishes, and can be opened by hand at any point. No parentheses, no
boxed cards, no wall of command output in the middle of an answer.

Before a call is written, the model announces it with a marker line
(`~~~command`, `~~~write`, …). The app hides that block from its first character
and shows the one-line row in its place, so a half-written tool block never
scrolls past.

### A real installer, and updates that install themselves

`Arena-Desktop-Setup-<version>.exe` installs for the current user without admin
rights: Start-menu shortcut, an entry under *Apps & features*, a working
uninstaller, and no wizard — one window with the brand and a single progress
line. The app then keeps itself current: it checks the official release feed,
offers one dialog (*Download and install*), verifies the download against the
published size and SHA-256, replaces its own executable and restarts. Accounts,
chats, settings and memory are never touched, so nothing has to be set up again.
See [INSTALLER.md](INSTALLER.md).

### The right-hand column is gone

The Workspace/Connectors panel is removed for this rework. Choosing a folder
still works from the composer chip and Settings → Folder.

### Reliability fixes from 1.5.1

- **Chats stay apart.** A reply keeps the folder it started in, opening a chat switches back to its folder, and an approval request shows only in its own chat.
- **No double sends.** When the connection drops after arena.ai took a message, the app reads the reply from arena.ai's copy of the chat instead of sending the prompt again. Sending twice used up arena.ai's per-chat token limit.
- **Clear errors.** A full arena.ai session, an "I'm not a robot" check and an arena.ai outage (from status.arena.ai) are named as such. Text chats continue in a fresh session when one fills up.
- **Run on this PC.** The ▶ button in the Workspace panel installs a Code-mode project's packages, starts its dev server, shows the address and stops it on request. Dev servers the agent starts show their address in the chat.
- Cleaner text between tool steps, and Refresh from Arena fixes replies that were cut off or glued together.
- **Account menu.** The menu shows who is signed in, then the saved accounts to switch to. Switching, signing out and signing in with cookies show each step as it happens.
- **Old chats read like new ones.** Chats from before 1.5 show their tool calls as rows with their output, instead of raw tags, and Code-mode steps keep their output.
- **Chats stay in place.** Opening or importing a chat no longer moves it to the top of the list.
- **Previews open in the app.** The Preview button on a live site opens the app's preview window. Before, an error sent it to the web browser instead. Closing the window only hides it.
- **arena.ai outages are shown.** The app reads status.arena.ai. When arena.ai reports trouble, a bar at the top names the problem and links to the status page. While arena.ai fails, the sign-in check no longer says your session expired. Settings → About → Quick diagnostics has a new "Arena.ai service status" row, and failed checks now show why. The encryption check no longer always fails.

</details>

### Previous: v1.5.0

<details>
<summary>What was new in v1.5.0</summary>

#### Arena Agent files land on your computer

With Workspace Sync on, every file the Arena Agent writes, edits or deletes in arena.ai's cloud workspace is copied into your chosen folder as it happens. This covers its file tools and `cat > file << 'EOF'` heredocs in shell commands. It asks once per chat, follows the permission mode, and never overwrites or deletes a file you changed on this computer. Dependency folders (`node_modules`, `.git`) are skipped. An HTML file it saves gets a live local preview.

### Site previews

Preview links are now `/preview/<key>/index.html`, using a read-only key separate from the API token. Linked CSS, JS and images load too, and "Open preview" no longer ends in `Missing or invalid API token`. arena.site links from Arena (Code mode and Agent) show as a "Live site" card with Preview, Copy link and Browser. In the desktop app, Preview opens its own WebView2 window: arena.site pages refuse to load inside frames, so an in-page iframe cannot show them.

### Smaller fixes

- Opening a chat no longer pulls Arena's copy of your injected memory, tool guide and tool results into it. Those used to show up as extra turns, so chats looked mixed together. Opening a chat no longer moves it to the top of the list.
- Cold start shows "Connecting to arena.ai…" with your saved account, then loads history and models by itself. You no longer need to refresh.
- Chat titles Arena copied from the injected guide ("[Arena Desktop] The user is…", "[System] You are Arena CLI…") are replaced by a summary of your first message.
- The first-run setup checks your account against the Direct model list. Battle mode has no model list, so the old check was wrong.
- Connectors: Arena connectors and local MCP servers are separate groups, and `mcp.json` sits next to the local group.
- The message jump control stays inside the chat column and no longer duplicates "back to bottom".
- Tests run against a temporary config folder (`ARENA_CONFIG_DIR`). They used to add fake accounts to the real `~/.arena-personal`.

### Claude Code–style tool rows

Agent tool calls render as compact terminal rows instead of boxed cards: `● Bash(npm install && npm run build)` with the result underneath (`⎿ added 214 packages…`). Green dot = done, red = failed (with the exit code), amber = waiting for your approval, blinking = running. Long output folds to 4 lines with "+N lines (click to expand)"; reads, listings and searches show a one-line summary ("Read 120 lines", "Found 8 matches"); edits show a coloured diff. Arena Agent's JSON tool input/output (`{"command": …}`, `{stdout, exitCode}`) is unwrapped into the same format. The header status reads `✻ Running command… 12s` / `✻ Thinking… 3s`, and the tool input and output are now saved in chat history, so reopened chats keep them.

### Run npm / cmd commands on your computer

In Agent mode with a local folder, `run_cmd` now supports full shell syntax through `cmd.exe` (or `/bin/sh`): `npm install && npm run build`, pipes, redirects and `%VAR%`. Output streams live into the tool row while the command runs, and Stop kills the whole process tree. Dev servers and watchers can run in the background (`<background>true</background>`): the agent gets the first output and the local URL, the process keeps running, and `stop_cmd` (or closing the app) ends it. Package managers and builds get a 180 s silence limit instead of 30 s.

Safety: a command that uses shell syntax or inline code (`-c`, `-e`, `--eval`) is never auto-approved. You confirm each one, even after "Allow such actions in this session" and in every permission mode. Catastrophic commands stay blocked.

### Usage bar

Live usage indicator above the composer shows how much of your daily arena.ai limit is used. Piggybacks on ratelimit headers — zero extra API calls. Three color states: blue (normal), amber (70%+), red (90%+). Reset countdown shows when limits refresh.

### Typewriter loading phrases

The typewriter animation ("asking Terry", "beam searching", etc.) now reliably shows during the loading window before text arrives, with a minimum 1.8s display window to prevent flicker. The animation used to stop before typing a single letter (it checked that it was on the page before it had been attached); it now survives the chat re-rendering on every stream event.

### Modular JS codebase

`app.js` split from one 11,316-line file into 14 source modules in `js/`, each under 1,500 lines. Concatenated into `app.js` by `python scripts/build-js.py`. Same IIFE, zero runtime change. Only the frontend is split so far: the backend `arena/web/app.py` (about 8,000 lines: Flask routes, chat and agent loops, the Arena Agent stream, uploads, workspace) is still one module, and splitting it into smaller files is planned for the next release.

</details>

### Previous: v1.4.0

<details>
<summary>What was new in v1.4.0</summary>

- Shell builtins (`echo`, `dir`, `ver`) now work in the terminal
- 8 security findings patched from a full code audit
- 46 feature tests + 22 security verification tests all pass
- **CRITICAL:** Settings import filtered — crafted settings files can no longer inject `permission_mode` or `gateway_auto`
- **HIGH:** Upload and workspace panel block sensitive dirs (`.git/`, `.ssh/`, `.env/`)
- **MEDIUM:** SSRF hardened, terminal scoped to workspace, agent symlink check added
- **LOW:** Error messages sanitized

</details>

### Previous: v1.3.0

<details>
<summary>What was new in v1.3.0</summary>

- **Workspace file reading** — AI models can read, list, browse files in workspace
- **Typewriter loading animation** — 30 random phrases with typing/erasing effect
- **Rate limit reference** — all 8 known arena.ai rate limits documented in Settings
- **Chat replay fixed** — system prompts filtered from visible messages
- **Send button arrow** — missing CSS class fixed
- **About dialog logo** — colosseum logo instead of star
- Upload endpoint unblocked, caps enforced, null bytes stripped, preview CSP updated

</details>

### Previous security work (v1.2.0-alpha → beta, carried forward)

<details>
<summary>22 findings fixed in v1.2.0</summary>

- `/api/token` removed — secret injected via `<meta>` tag instead
- DPAPI encryption for cookies and credentials (Windows; other platforms planned)
- Agent sandbox blocks `.ssh`, `.aws`, `.gnupg`, `.env`, `.git`, NTFS ADS, symlink escapes
- `contracts.json` locked to existing API path keys only
- SVG data-URI XSS blocked (raster-only)
- "Always allow" resets per chat
- Automations locked to plan mode
- Per-launch API token on all `/api/*` endpoints
- Workspace scoped to projects directory
- Settings allowlist
- Inline code blocking on agent commands
- `shell=False` on all subprocess calls
- `/api/terminal` hardened — `shell=False`, `shlex.split`, 30s timeout, workspace-scoped, token-gated
- Security headers on index and static routes

</details>

> [!NOTE]
> Report issues on the [issues page](https://github.com/Arvex-GH/ArenaAI-Desktop-Major-Rework/issues).

## Download

Every build is on the [releases page](https://github.com/Arvex-GH/ArenaAI-Desktop-Major-Rework/releases/latest).

| Platform | Download | Notes |
| --- | --- | --- |
| Windows 10 / 11 | `Arena-Desktop-Setup-<version>.exe` | Installs for the current user (no admin rights) and keeps itself up to date. |
| Windows 10 / 11 | `Arena-Desktop-<version>.exe` | Portable — run it from anywhere, nothing is installed. |

Both need the Edge WebView2 runtime (built into Windows 11).

**Install** — double-click the Setup file, wait for the one progress line, press
*Open Arena Desktop*. It lands in `%LOCALAPPDATA%\Programs\Arena Desktop`, adds a
Start-menu shortcut and a proper entry under *Apps & features*.

**Updates** — the app checks the official releases itself and offers a single
dialog: *Download and install*. It replaces itself in place and restarts; chats,
accounts and settings stay exactly where they are. Nothing has to be set up again.

**Uninstall** — *Apps & features → Arena Desktop → Uninstall*, with an opt-in
checkbox if you also want your chats and settings removed.

See [INSTALLER.md](INSTALLER.md) for the full picture (silent flags, update
feed, signing, release process).

If Windows SmartScreen warns: click **More info → Run anyway** (the build is not code-signed).

## What it does

- **Every arena mode in-app** — I built in Battle, Side by Side, Direct, and Agent Mode so you don't need a browser tab.
- **Every modality** — Text, Image, Web Search, Code, Video, Auto (same rules as the website).
- **Workspace Sync** — models can list, read, write, edit files and run commands in a folder you choose, under a permission mode you control.
- **Files land on your PC** — files the Arena Agent writes in its cloud sandbox are copied into your folder as it works, and `arena.site` links open in an in-app preview.
- **MCP servers + Arena connectors** — local MCP servers (presets, JSON, CLI) and official Arena connectors with per-connector toggles.
- **Multi-account** — switch arena.ai accounts in one click, parallel chats, local + cloud history.
- **Agent Gateway** — the genuine Arena Agent can use your local MCP servers and workspace folder through a pairing-protected tunnel.
- **Sub-agents, plan approval, memory, automations, compact context, undo.**

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/agent-tools.png" alt="Agent Mode tool rows: a file read, npm output, an edit diff and a failing test" /><br /><sub><b>Agent Mode</b>: Claude Code–style tool rows with live command output, diffs and failures.</sub></td>
    <td width="50%"><img src="docs/screenshots/agent-result.png" alt="Agent answer with a code block and the permission note" /><br /><sub><b>Answers</b>: code blocks with copy buttons, and a plain note of each permission decision.</sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/battle.png" alt="Battle Mode with two anonymous answers and vote buttons" /><br /><sub><b>Battle Mode</b>: two anonymous models answer, then you vote.</sub></td>
    <td width="50%"><img src="docs/screenshots/settings.png" alt="Settings window: account, permissions, folder, memory, automations, usage, gateway, components and about" /><br /><sub><b>Settings</b>: account, permissions, folders, memory, gateways and components in one window.</sub></td>
  </tr>
</table>

## How it works

```
┌──────────────── Arena Desktop (pywebview / WebView2) ────────────────┐
│  Single-page UI  ──►  local Flask API + SSE streaming  (arena/web/)  │
│                              │                                       │
│        ┌─────────────────────┼──────────────────────┐                │
│        ▼                     ▼                      ▼                │
│  arena.ai engine      native Arena Agent     local tool loop         │
│  (hidden window,      (agentic API,          (arena/agent.py +       │
│   real account)        resumable)             MCP connectors)        │
└──────────────────────────────────────────────────────────────────────┘
```

| Module | Role |
| --- | --- |
| `arena/desktop.py` | Native window shell (pywebview + WebView2) |
| `arena/web/app.py` | Flask API, SSE streaming, chat orchestration, accounts |
| `arena/web/static/` | The whole UI (HTML/CSS/JS) |
| `arena/web/static/js/` | JS source modules (14 files, each <1500 lines) |
| `scripts/build-js.py` | Concatenates js/ parts into app.js |
| `arena/engine.py` | Hidden arena.ai window for signed-in requests |
| `arena/native_agent.py` | Native Arena Agent protocol |
| `arena/agent.py` | Local tool loop: parsing, execution, permissions |
| `arena/connectors.py` | Local MCP server manager |
| `arena/permissions.py` | Permission modes and per-chat approval broker |

## Repository layout

```
Arena-Desktop/
├── arena/                  # application package
│   ├── web/                # Flask backend + static UI
│   └── tools/  ui/         # CLI-era tools and terminal UI
├── assets/                 # icon and logos
├── docs/                   # screenshots
├── scripts/                # build verification
├── tests/                  # pytest suites (unit/API + browser UI)
├── arena-cli.spec          # PyInstaller spec → Arena-Desktop.exe
├── requirements.txt
├── START_ARENA_DESKTOP.bat # one-click source launcher
├── KNOWN_ISSUES.md
├── HANDOFF.md
└── SECURITY_AUDIT.md
```

## Run from source

1. Install **Python 3.11+** and the Edge WebView2 Runtime.
2. Double-click **`START_ARENA_DESKTOP.bat`** — creates `.venv`, installs deps, opens the app.

Or manually:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m arena.desktop
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_ui_browser.py   # unit/API
$env:RUN_BROWSER_TESTS=1; .\.venv\Scripts\python.exe -m pytest tests/test_ui_browser.py  # UI (demo mode)
```

## Troubleshooting

- **SmartScreen warning**: click More info → Run anyway.
- **429 rate limit**: arena.ai's daily/per-modality cap, not the app. I show you which limit hit and when it resets.
- **Human check popup**: complete it manually in the Arena window.
- **Logs**: `%USERPROFILE%\.arena-personal\desktop.log`

## Roadmap: macOS and Linux

Arena Desktop is Windows-only right now because it depends on WebView2. I want to bring it to macOS and Linux — the backend (Flask + SSE) is platform-agnostic, so the main work is swapping the pywebview/WebView2 shell for something cross-platform (e.g. webview bindings for GTK/Cocoa, or an Electron shell). The DPAPI encryption layer would also need a platform-specific backend (Keychain on macOS, Secret Service on Linux).

An independent researcher already did a thorough code review and started building a separate Linux client (Wayland/KDE, Native Messaging + browser extension). Their security audit helped shape several of the v1.2.0-beta fixes. I don't have their contact info but want to publicly thank them for the work — if you're reading this, reach out so I can credit you properly.

If you're interested in helping with a port, PRs are welcome. The architecture section above shows what needs to change.

## Limitations

- **Windows only** — WebView2 dependency. macOS and Linux are on the roadmap.
- **Arena's limits apply** — daily caps, rate limits, new-account video gate. The app shows you which limit hit and when it resets, but I can't lift them.
- **Human checks stay manual** by design.
- **Code mode uses arena's sandbox**, not your machine. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- **Not code-signed** — SmartScreen will warn on first run.

## Why Arena probably won't ship an official desktop app

Based on Arena's [Terms of Use](https://help.arena.ai/articles/5629909088-terms-of-use) (last updated February 2026):

**Do they sell your data?** Not directly, but close. Section 3.3 grants Arena a perpetual, irrevocable, worldwide, sublicensable license to use everything you type — inputs and outputs — for "marketing and promotional purposes" and to "develop, enhance, evolve and improve" their products. Section 3.4 adds that your content goes to third-party AI providers who "may not be required to maintain the confidentiality" of it. Your conversations are training data.

**Five reasons an official desktop client is unlikely:**

1. **They're a benchmarking platform, not a productivity tool.** Arena exists to compare AI models side by side and collect votes for their leaderboard (§1). A desktop app built for daily use would compete with their own AI provider partners.

2. **They need your data on their servers.** The perpetual, sublicensable license to Your Content (§3.3) — including for labeling, training, and marketing — depends on server-side collection. A desktop client that caches locally or skips telemetry would undermine their data pipeline.

3. **They don't control the AI services.** Section 10.1.3 disclaims all responsibility for AI output, operation, and security. They're a routing layer, not a provider. Maintaining native desktop code for 400+ models they don't own is high cost with no revenue upside.

4. **The service is free with no business model yet.** Section 8: "Company currently offers the Service free of charge." No revenue means no budget for desktop engineering, testing, code signing, or platform-specific support.

5. **Their TOS bans programmatic access.** Section 5(vi) prohibits accessing "the Services through programmatic or automated means." They want you on the website where they control the experience, collect votes, and track usage.

This is why community clients like this one exist.

### Is this project violating Arena's TOS?

No. Arena Desktop does **not** violate Arena's Terms of Use:

- **No programmatic or automated access (§5(vi)).** Arena Desktop embeds the actual arena.ai website inside a WebView2 browser window. It does not scrape APIs, auto-query models, or bypass any endpoint. Every request goes through the same web interface a normal browser uses.
- **No leaderboard manipulation (§5(ii)).** The app does not submit votes, influence rankings, or interact with the leaderboard in any automated way. You vote manually, same as on the website.
- **No bot or crawler activity (§5(vii)).** There is no scraping, spidering, or automated data extraction. The app renders the same pages your browser would.
- **Human checks stay manual by design.** When arena.ai presents a CAPTCHA or verification, you complete it yourself in the embedded window. The app never solves or bypasses these.
- **Your account, your responsibility (§2.4).** You sign in with your own arena.ai credentials. The app adds local tools on top (workspace sync, MCP servers, multi-account switching) but never modifies what arena.ai sees or sends on your behalf beyond what you initiate.

Arena Desktop is a browser wrapper with local developer tools — the same class of software as any Electron-based web app. The TOS targets bots, scrapers, and vote manipulators, not desktop clients that render the website as-is.

## Translations

The UI is English-only right now. I'd love help translating it to other languages — if you're interested, open a PR or reach out on Discord. Priority languages: Spanish, Portuguese, Chinese, Japanese, Korean, French, German, Russian, Arabic and Hindi, but any language is welcome.

## Credits & license

Built by [PlayzAe](https://github.com/PlayzAe) and contributors. Major contributions from [Maxusimlive](https://github.com/Maxusimlive) on the desktop UI, chat system, workspace features and native Arena Agent integration. Thanks to the anonymous Linux researcher whose independent security audit shaped several of the v1.2.0-beta security fixes.

Released under the [MIT License](LICENSE). Not affiliated with Arena AI Inc.

## Contact

[![Discord](https://img.shields.io/static/v1?label=Discord&message=%40playzae&color=5865F2&style=flat-square&logo=discord&logoColor=white)](https://discord.com)

DMs open — `@playzae`. For bugs and features: [GitHub Issues](../../issues).

[![Star History Chart](https://app.repohistory.com/api/svg?repo=Arvex-GH/ArenaAI-Desktop-Major-Rework&type=Date&background=0D1117&color=f8d862)](https://app.repohistory.com/star-history)
