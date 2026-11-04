# Arena Desktop v5.6.0 - Start Here

Arena Desktop is an unofficial Windows client for arena.ai. It is not affiliated with or endorsed by Arena AI Inc.

## Run the EXE

1. Save `Arena-Desktop.exe` in a normal folder and double-click it. No Python installation is required.
2. Use 64-bit Windows 10/11 with Microsoft Edge WebView2 Runtime and .NET Framework 4.8. Windows 11 normally includes both.
3. The one-file EXE can start slowly the first time because it unpacks embedded components into a temporary folder. It is not code-signed, so Windows SmartScreen may show a warning.
4. Click the profile/sign-in area and choose **Sign in**. Complete sign-in in the Arena window. After the account is verified, the window hides automatically and the app refreshes.

Application log: `%USERPROFILE%\.arena-personal\desktop.log`

## Run from source

1. Extract the full source tree to a normal folder. Do not run it inside a ZIP file.
2. Install Python 3.11 or newer and Microsoft Edge WebView2 Runtime.
3. Run `START_ARENA_DESKTOP.bat`. The first launch creates `.venv` and installs `requirements.txt` (internet required).

Manual PowerShell launch:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m arena.desktop
```

## Sign in with cookies

Use only cookies from your own Arena account. Cookies can grant access to the session, so never send them to another person or paste them into chats, screenshots, or bug reports.

1. Sign in at `https://arena.ai` in a regular browser.
2. In Arena Desktop, open the account/sign-in settings and find the cookies field.
3. On the signed-in Arena tab, open DevTools -> Console, run `copy(document.cookie)`, and paste the result into the app. If the browser blocks pasting into the console, type `allow pasting` first.
4. If the result does not include `arena-auth-prod-v1`, open DevTools -> Application -> Cookies -> `https://arena.ai` and copy all `arena-auth-prod-v1.*` rows in `name=value; name2=value2` form.
5. Click **Sign in with cookies**. The app imports the Arena auth cookies, verifies the account, and stores the session locally.

The local session file `%USERPROFILE%\.arena-personal\cookies.txt` may be unencrypted. Signing out removes it.

## Main features

- Arena-style mode and work-type dropdowns: Battle Mode, Agent Mode, Side by Side, Direct; Video, Image, Web Search, Code, Text, and Auto.
- Video and Auto are Battle-only. Agent Mode hides the work-type selector and runs as Text.
- Manual models only where Arena allows them: one in Direct and two in Side by Side.
- Battle voting (A, B, tie, both bad), with model names revealed after a successful vote.
- Arena Agent inside the app through direct API calls; a separate Local Agent remains available and its default for new chats is selected in Settings -> Permissions.
- **Workspace Sync** switch (right of the permission selector, shown in every mode): when on, models can run commands and use tools in the folder selected on this computer, with results returned to them automatically — in Direct, Side by Side, Battle (Text/Web Search) and the Local Agent; files the native Arena Agent creates in its cloud workspace are downloaded into the selected folder too. When off, models only chat, Code files are not mirrored and Arena Agent files stay in Arena's workspace.
- Local and cloud Arena history, continuation of existing Arena sessions, Workspace files, media, and permissions. Deleting a chat here also archives it on arena.ai.
- Multiple Arena accounts: click the profile to switch between saved accounts or add another one (sign-in window or cookies). Each account keeps its own chat list; an account whose session Arena no longer accepts is marked "Session expired: sign in". Saved sessions live in `%USERPROFILE%\.arena-personal\accounts.json` (unencrypted, like cookies.txt — treat it as a password). Switching is blocked while a generation is running.
- Parallel chats: a chat appears in the sidebar as soon as you send the prompt, and other chats stay fully usable while one is generating. A spinning circle marks a working chat; a green check marks a chat that finished while you were elsewhere. Stop acts on the open chat only.
- File attachments with the **+** button, drag and drop onto the message box, or paste from the clipboard. Battle/Direct/Side by Side accept PNG/JPEG/WebP/PDF (15 MB, Video 10 MB); Agent Mode also accepts GIF/TXT/MD/CSV/HTML/XML/CSS/JS/JSON (25 MB per file, PDF 10 MB, 50 MB per message).
- Official Arena account connectors (Agent Mode only, with per-connector on/off switches) plus separate local MCP servers. In Connectors, **Add** opens ready-made presets (including Playwright), JSON configuration, and CLI-command setup.
- Real profile, connector, and model/organization icons where Arena supplies them; initials are only a fallback.
- Drag the sidebar, Workspace, and files/Connectors separators to resize the layout. The layout is saved between launches. On the home screen the message box sits in the center; inside a chat it docks at the bottom.

## Limits

- Arena controls model availability, work types, quotas, and rate limits for the signed-in account. Rate limits can be per work type (Video is the strictest), per model, or a daily account cap; the app now shows Arena's own reset time. Video may also require an account of a minimum age.
- Human checks are never solved automatically. Complete them manually in the Arena window.
- A started chat keeps the mode, work type, and models it began with. Create a new chat to change them.
- A local folder is not uploaded wholesale to Arena's remote sandbox; files and previews come from actual server events.
- Existing local data remains under `%USERPROFILE%\.arena-personal` for compatibility with earlier versions.

## Build and test

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
$env:RUN_BROWSER_TESTS='1'; .\.venv\Scripts\python.exe -m pytest -q tests/test_ui_browser.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean arena-cli.spec
.\.venv\Scripts\python.exe scripts/check_exe.py dist/Arena-Desktop.exe
```
