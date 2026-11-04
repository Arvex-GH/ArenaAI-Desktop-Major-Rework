<p align="center">
  <img src="assets/logo.png" alt="Arena Desktop logo" width="96" />
</p>

<h1 align="center">Arena Desktop</h1>

<p align="center">
  <b>An unofficial desktop app for <a href="https://arena.ai">arena.ai</a> — chat, battles, a real Agent Mode,
  and models that can work on the files on your machine, all in one window.</b>
</p>

<div align="center">
  <a href="https://github.com/PlayzAe/arena.ai-desktop/stargazers"><img src="https://img.shields.io/github/stars/PlayzAe/arena.ai-desktop?style=flat-square&color=crimson" alt="GitHub stars" /></a>
  <a href="https://github.com/PlayzAe/arena.ai-desktop/releases/latest"><img src="https://img.shields.io/github/v/release/PlayzAe/arena.ai-desktop?style=flat-square" alt="Latest release" /></a>
  <img src="https://img.shields.io/badge/version-1.2.0--beta-orange?style=flat-square" alt="v1.2.0-beta" />
  <img src="https://img.shields.io/badge/models-400%2B-10B981?style=flat-square" alt="400+ models" />
  <img src="https://img.shields.io/badge/Platform-Windows_(macOS%20%26%20Linux%20planned)-blue?style=flat-square" alt="Platform" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT license" /></a>
</div>

<p align="center">
  <img src="docs/screenshots/home.png" alt="Arena Desktop home screen" width="900" />
</p>

> [!IMPORTANT]
> Arena Desktop is an **unofficial, community-built client**. It is not affiliated with or endorsed by Arena AI Inc.
> You sign in with your own arena.ai account and are responsible for following arena.ai's terms.
> The app never solves human checks for you — when arena.ai asks for one, you complete it yourself.

---

## Contents

- [Download](#download)
- [What's new in v1.2.0-beta](#whats-new-in-v120-beta)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Repository layout](#repository-layout)
- [Run from source](#run-from-source)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Roadmap: macOS and Linux](#roadmap-macos-and-linux)

## What's new in v1.2.0-beta

I ran two full security audits on the codebase — 22 findings total. Every critical and high item is fixed. Here's what changed:

### Security (v1.2.0-alpha → beta)

- **`/api/token` removed** — the per-launch secret is no longer exposed on an open endpoint. I inject it into the served HTML via a `<meta>` tag so external processes can't grab it.
- **DPAPI encryption everywhere** — cookies in `accounts.json` and `cookies.txt` are now encrypted with Windows Data Protection API. Old plaintext data migrates transparently on first read.
- **Agent sandbox hardened** — `resolve()` blocks `.ssh`, `.aws`, `.gnupg`, `.env`, `.git` and NTFS alternate data streams. Symlinks that escape the workspace root are rejected.
- **`contracts.json` locked down** — overrides can only replace existing API path keys. No new keys, no `//` prefix, no `:` in paths.
- **SVG data-URI XSS blocked** — only raster image data URIs (PNG, JPEG, WebP, GIF) are allowed now. The old regex-based SVG filter was bypassable.
- **"Always allow" resets per chat** — permission grants no longer carry across new conversations.
- **Automations locked to plan mode** — no more `auto` permission for unattended runs.
- **Workspace preview sandboxed** — `allow-scripts` removed from the CSP directive. Workspace HTML can't execute JavaScript.

### Security (v1.2.0-alpha, carried forward)

- Per-launch API token on all `/api/*` endpoints
- Workspace scoped to projects directory only (no home-directory traversal)
- Settings allowlist (no mass assignment)
- Inline code blocking (20+ interpreter/flag combos)
- `shell=False` on every subprocess call
- `/api/terminal` deleted entirely
- Security headers on all responses

### UI improvements

- Better markdown rendering — larger text, improved spacing for paragraphs, lists, and code blocks
- "Thinking" replaces "Reflections" for the reasoning section
- Cleaner answer footnotes

> [!WARNING]
> This is a **beta** release. All 22 security findings have been addressed, but I still want more testing before calling it stable. Report issues on the [issues page](https://github.com/PlayzAe/arena.ai-desktop/issues).

## Download

Every build is on the [releases page](https://github.com/PlayzAe/arena.ai-desktop/releases/latest).

| Platform | Download | Notes |
| --- | --- | --- |
| Windows 10 / 11 | `Arena-Desktop.exe` | Standalone, no installer. Requires Edge WebView2 (built into Windows 11). |

If Windows SmartScreen warns: click **More info → Run anyway** (the EXE is not code-signed).

## What it does

- **Every arena mode in-app** — I built in Battle, Side by Side, Direct, and Agent Mode so you don't need a browser tab.
- **Every modality** — Text, Image, Web Search, Code, Video, Auto (same rules as the website).
- **Workspace Sync** — models can list, read, write, edit files and run commands in a folder you choose, under a permission mode you control.
- **MCP servers + Arena connectors** — local MCP servers (presets, JSON, CLI) and official Arena connectors with per-connector toggles.
- **Multi-account** — switch arena.ai accounts in one click, parallel chats, local + cloud history.
- **Agent Gateway** — the genuine Arena Agent can use your local MCP servers and workspace folder through a pairing-protected tunnel.
- **Sub-agents, plan approval, memory, automations, compact context, undo.**

<p align="center">
  <img src="docs/screenshots/agent-mode.png" alt="Agent Mode with Workspace Sync" width="900" />
</p>

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
| `arena/web/static/` | The whole UI (HTML/CSS/JS, no build step) |
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

If you're interested in helping with a port, PRs are welcome. The architecture section above shows what needs to change.

## Limitations

- **Windows only** — WebView2 dependency. macOS and Linux are on the roadmap.
- **Arena's limits apply** — daily caps, rate limits, new-account video gate. The app shows you which limit hit and when it resets, but I can't lift them.
- **Human checks stay manual** by design.
- **Code mode uses arena's sandbox**, not your machine. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- **Not code-signed** — SmartScreen will warn on first run.

## Credits & license

Built by [PlayzAe](https://github.com/PlayzAe) and contributors. Major contributions from [Maxusimlive](https://github.com/Maxusimlive) on the desktop UI, chat system, workspace features and native Arena Agent integration.

Released under the [MIT License](LICENSE). Not affiliated with Arena AI Inc.

## Contact

[![Discord](https://img.shields.io/static/v1?label=Discord&message=%40playzae&color=5865F2&style=flat-square&logo=discord&logoColor=white)](https://discord.com)

DMs open — `@playzae`. For bugs and features: [GitHub Issues](../../issues).
