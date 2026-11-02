<p align="center">
  <img src="assets/logo.png" alt="Arena Desktop logo" width="96" />
</p>

<h1 align="center">Arena Desktop</h1>

<p align="center">
  <b>An unofficial desktop app for <a href="https://arena.ai">arena.ai</a> — chat, battles, a real Agent Mode,
  and models that can work on the files on your PC, all in one Windows window.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-4.17.0-8b5cf6?style=flat-square" alt="Version 4.17.0" />
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-blue?style=flat-square" alt="Windows 10/11" />
  <img src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/models-400%2B%20via%20arena.ai-10b981?style=flat-square" alt="400+ models via arena.ai" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT license" /></a>
</p>

<p align="center">
  <img src="docs/screenshots/home.png" alt="Arena Desktop home screen (demo data)" width="900" />
</p>

> [!IMPORTANT]
> Arena Desktop is an **unofficial, community-built client**. It is not affiliated with or endorsed by Arena AI Inc.
> You sign in with your own arena.ai account and are responsible for following arena.ai's terms.
> The app never solves human checks for you — when arena.ai asks for one, you complete it yourself.

---

## What is it?

Arena Desktop brings the full arena.ai experience into a native Windows app and adds a local workspace on top:

- **Every arena mode, in-app**: Battle, Side by Side, Direct and a genuine **Agent Mode** that runs
  through arena.ai's own agentic API inside the app — no browser tab, no typing into the website.
- **Every modality**: Text, Image, Web Search, Code, Video and Auto, with the same rules as the website
  (Video and Auto are Battle-only; manual model picking appears only where arena.ai allows it).
- **Models that can touch your files**: with **Workspace Sync** on, models in Direct / Side by Side /
  Battle — and the built-in Local Agent — can list, read, write and edit files and run commands in a
  folder you choose, under a permission mode you control. Results loop back to the model automatically.
- **MCP servers and official Arena connectors**: connect local MCP servers (presets, JSON or CLI command)
  for the local tool loop, and manage your account's Arena connectors (GitHub, Drive, Notion, …) with
  per-connector on/off switches for Arena Agent.
- **A proper desktop workflow**: multiple arena.ai accounts with one-click switching, several chats
  generating in parallel with live status marks, local + cloud history, attachments, resizable panels.

Arena Desktop is a **harness, not a model**: all generation happens on arena.ai with your account.
The app orchestrates chats, tools, files and permissions around it.

## Features

### Chat & modes
- Battle Mode with anonymous A/B answers and voting (**A is better / B is better / Tie / Both are bad**);
  models are revealed after your vote, and the vote is sent to arena.ai for real.
- Side by Side and Direct with arena.ai's live model catalog (400+ models, no hardcoded lists).
- Agent Mode with two backends (pick the default in **Settings → Permissions**):
  **Arena Agent** — arena.ai's own agentic mode, streamed natively in-app (research, code, web search,
  files, clarifying questions); **Local Agent** — an arena model driving tools on your computer.
- A chat's mode, modality and model lock after the first message, exactly like on the website.
- Answers stream token by token, end the moment the model finishes, and tool calls render as clean
  activity cards — never as raw markup in the text.

### Workspace Sync & permissions
- The **Workspace Sync** switch sits next to the permission selector in every mode.
  On: models get the automatic folder-tools loop (`ls`, `read_file`, `search`, `write_file`,
  `edit_file`, `mkdir`, `delete`, `run_cmd`) in the folder you selected, and files the native
  Arena Agent creates in its cloud workspace are downloaded into that folder automatically
  (hash-deduped across turns, one permission decision per chat).
  Off: models only chat, Code-mode files are not mirrored, and Arena Agent files stay in Arena's workspace.
- The loop keeps the model's context lean: long tool outputs are saved to `.arena-artifacts/` in the
  folder and only a preview travels back (the model reads the full file only if it needs to),
  repeated output lines collapse, `read_file` reads line ranges, and a model that keeps repeating
  the same tool call is stopped automatically.
- **The model can ask you**: with the `ask_user` tool the Local Agent (and its sub-agents) can pause
  mid-task and ask a clarifying question with 2–4 answer options plus a free-text field — your
  answer returns to the model and the work continues, instead of it guessing on a fork that
  matters. Unanswered questions time out with the assumption stated in the reply.
- **Sub-agents**: the Local Agent (and Direct with Workspace Sync) can delegate a self-contained
  sub-task to a child chat with its own Arena session and tool loop — research (read-only) or work —
  and pick the sub-agent's model. Only the child's summary returns to the parent, so long projects
  stop drowning in exploration noise. Child chats nest under their parent and open like any chat.
- **Compact button** next to Workspace Sync: when a conversation nears Arena's context limit
  (~80k characters — the button lights up), one click continues the same chat in a fresh Arena
  session seeded with a summary, instead of the chat dying with empty replies. The full
  transcript stays in the app; only the server-side context restarts.
- **Memory across chats**, in the style of Claude Code: a general memory plus a per-project
  `MEMORY.md` in the selected folder travel with the first message of each new conversation.
  Models can update `MEMORY.md` while they work ("remember this"), Compact appends its session
  summary there, and both files are editable in Settings → Memory.
- Desktop comforts: update checks against GitHub Releases (Settings → About), an optional system
  tray with a global Ctrl+Alt+A show/hide hotkey, settings export/import (never account cookies),
  a copyable diagnostics report, portable mode (`portable.txt` beside the EXE keeps data in `./data`),
  and launch flags (`--profile <id|email>`, `--background`, `--mode <id>`).
- Permission modes modelled on Claude Code (Plan / Ask / Accept edits / Bypass) gate every local action;
  requests are tied to the chat that asked, so a waiting permission never blocks your other chats.
- Code (webdev) answers have their created/edited files mirrored into your folder
  (Battle writes lanes into `option-a` / `option-b`).

### Connectors & MCP
- **Official Arena connectors** from your account load automatically; authorize new ones from the app
  and toggle each on/off (an off connector is really excluded from new Arena Agent sessions).
- **Local MCP servers**: add via ready-made presets, a JSON config, or a CLI command (`Add` button in
  the right panel). Their tools join the same loop and permission system as the built-in file tools.

### Accounts & history
- Sign in in a separate arena.ai window (it closes itself once verified) or by pasting cookies.
- **Multiple accounts**: add accounts, see which is current, switch in one click; sessions are kept
  per account and chat lists are separated. Switching is blocked while a chat is generating.
- Chats appear in the sidebar **the moment you send**, can be reopened while still generating, and
  continue in the same arena.ai session. Cloud-only chats from arena.ai show under “Only on arena.ai”
  and can be opened and continued.
- The sidebar marks running chats with a spinner and finished-but-unread chats with a green check.
- Deleting a chat in the app also archives it on arena.ai.
- Several chats can generate **in parallel**, each with its own Stop button.

### Attachments & polish
- `+` button, drag-and-drop and paste-from-clipboard uploads with arena.ai's real per-mode limits
  (files upload to Arena storage first; oversize/wrong-type files are rejected with a clear reason).
- Arena-matching interface: dark theme, unified composer chips, centered home composer,
  resizable sidebar/workspace panels (sizes are remembered), English-only UI.
- Friendly errors: rate limits explain which limit hit and when it resets (daily spend, per-modality,
  per-model, new-account video gate) instead of a bare “429”.

<p align="center">
  <img src="docs/screenshots/agent-mode.png" alt="Agent Mode with Workspace Sync (demo data)" width="900" />
</p>

## Quick start

### Run the EXE (easiest)

1. Download `Arena-Desktop.exe`, save it in a normal folder and double-click. No Python needed.
2. Requires 64-bit Windows 10/11 with the Microsoft Edge **WebView2 Runtime** and .NET Framework 4.8
   (Windows 11 includes both). The one-file EXE starts slowly the first time while it unpacks, and —
   being unsigned — may trigger a SmartScreen warning.
3. Click the profile area → **Sign in**, finish sign-in in the Arena window, and you're ready.

### Run from source

1. Extract the source tree to a normal folder (don't run it inside a ZIP).
2. Install **Python 3.11+** and the Edge WebView2 Runtime.
3. Double-click **`START_ARENA_DESKTOP.bat`** — first launch creates `.venv` and installs
   `requirements.txt` (internet required), then opens the app.

Manual launch:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m arena.desktop
```

There is also a terminal CLI (`python arena-cli.py`, or `start.bat`) kept from the project's origins.

App data (config, chats, accounts, logs) lives in `%USERPROFILE%\.arena-personal\`.

## Using the app

- **Pick a folder** with the workspace chip on the left of the composer, choose a permission mode,
  and turn **Workspace Sync** on if models may work in that folder.
- **Mode menu**: Battle Mode / Agent Mode / Side by Side / Direct. **Work type** menu:
  Video / Image / Web Search / Code / Text / Auto (hidden in Agent Mode).
- **Agent Mode** never asks you to pick a model — Arena routes automatically, like on the website.
- **Accounts menu** (profile, bottom-left): switch accounts, **+ Add account**, Settings, Sign out.
- **Right panel**: workspace file tree on top, **Connectors** below (official Arena connectors plus
  your local MCP servers).
- **Settings**: account/session, permissions (incl. the default Agent backend for new chats),
  workspace folder, and About.

## Build the EXE yourself

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller arena-cli.spec --noconfirm
# → dist\Arena-Desktop.exe  (single file, windowed)
```

`scripts/check_exe.py <path-to-exe>` sanity-checks a built binary (bundled modules, assets, version).

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install pytest playwright
.\.venv\Scripts\python.exe -m pytest tests --ignore=tests/test_ui_browser.py   # unit/API suite
$env:RUN_BROWSER_TESTS=1; .\.venv\Scripts\python.exe -m pytest tests/test_ui_browser.py  # UI suite (demo mode)
```

The UI has a built-in demo mode (`?demo=1`) with simulated data — handy for development without an account.

## How it works

```
┌──────────────────────────── Arena Desktop (pywebview / WebView2) ───────────────────────────┐
│  Single-page UI  ──►  local Flask API + SSE streaming  (arena/web/)                          │
│                              │                                                               │
│        ┌─────────────────────┼──────────────────────────┐                                    │
│        ▼                     ▼                          ▼                                    │
│  arena.ai engine      native Arena Agent         local tool loop                             │
│  (hidden window,      (agentic API, direct        (arena/agent.py + MCP via                  │
│   real account)        calls, resumable)           arena/connectors.py)                      │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Module | Role |
| --- | --- |
| `arena/desktop.py` | Native window shell (pywebview + Edge WebView2) |
| `arena/web/app.py` | Flask API, SSE streaming, chat orchestration, accounts |
| `arena/web/static/` | The whole UI (HTML/CSS/JS, no build step) |
| `arena/engine.py` | Hidden arena.ai window that performs signed-in requests |
| `arena/contracts.py` | arena.ai wire formats: chat bodies, stream parsing, uploads |
| `arena/native_agent.py` | Native Arena Agent Mode protocol (create/resume/complete) |
| `arena/agent.py` | Local folder-tools loop: tool guide, parsing, execution |
| `arena/connectors.py` | Local MCP server manager (config, runtime, tool calls) |
| `arena/permissions.py` | Permission modes and per-chat approval broker |
| `arena/models.py` | Dynamic model catalog from arena.ai |
| `arena/history_sync.py` | Converts arena.ai cloud history to local chats |
| `arena/cli.py`, `arena/ui/`, `arena/tools/` | Terminal CLI from the project's origins |

## Honest limitations

- **Windows only** (WebView2). macOS/Linux would need a different shell.
- **Arena's own limits apply** and cannot be bypassed from any client: daily spend caps, per-modality
  and per-model rate limits, and the new-account gate for Video. The app explains them; it can't lift them.
- **Human checks stay manual** by design: the app opens the check, you tick it.
- **Code mode is arena's sandbox**, not your PC: the app mirrors files the model created/edited, but
  the sandbox can't see arbitrary local files, and its `bash`/media steps stay in the sandbox.
  See [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) for the current list.
- The EXE is **not code-signed**, so SmartScreen may warn on first run.
- arena.ai changes its frontend regularly; server-action IDs (voting, archiving) are rediscovered by
  name at runtime, but a bigger arena.ai overhaul could still require an app update.

## Project layout

```
Arena-Desktop/
├── arena/                  # application package (see module table above)
│   ├── web/                # Flask backend + static UI
│   ├── tools/  ui/         # CLI-era tools and terminal UI
│   └── …
├── assets/                 # icon and logos
├── docs/                   # research notes and screenshots
├── scripts/check_exe.py    # built-EXE verification
├── tests/                  # pytest suites (unit/API + browser UI)
├── arena-cli.py            # CLI entry point (also the PyInstaller entry)
├── arena-cli.spec          # PyInstaller spec → single-file Arena-Desktop.exe
├── requirements.txt
├── START_ARENA_DESKTOP.bat # one-double-click source launcher
├── START_HERE.md           # short user guide
└── KNOWN_ISSUES.md
```

## Credits & license

Arena Desktop grew out of the open-source arena.ai desktop/CLI work by
[PlayzAe](https://github.com/PlayzAe) and contributors, and has since been heavily reworked
(full in-app Arena Agent Mode, Workspace Sync tool loop, MCP support, multi-account auth,
parallel chats, and a redesigned interface). Thanks to everyone whose code and research it builds on.

Released under the [MIT License](LICENSE). Not affiliated with Arena AI Inc.; “Arena” is used
descriptively to name the service this client connects to.

## Contact

Questions, ideas or feedback? Reach the maintainer on Discord:

[![Discord](https://img.shields.io/static/v1?label=Discord&message=%40.max_power._&color=5865F2&style=flat-square&logo=discord&logoColor=white)](https://discord.com/users/877543689428287489)

DMs are open — `@.max_power._`. For bugs and feature requests, [GitHub Issues](../../issues) still work best.
