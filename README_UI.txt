ARENA DESKTOP - WEB INTERFACE NOTES
===================================

Arena Desktop is an unofficial Windows client for arena.ai. It is not
affiliated with or endorsed by Arena AI Inc.

The distributed application is a single file: Arena-Desktop.exe.
The local Flask + pywebview backend serves these interface files:

  arena/web/static/index.html
  arena/web/static/style.css
  arena/web/static/app.js

The interface uses relative same-origin API URLs only. It does not need npm,
a CDN build step, remote fonts, or remote scripts.

CURRENT INTERFACE
-----------------
- Arena-style dropdowns for mode and work type:
  Battle Mode, Agent Mode, Side by Side, and Direct.
- Work types: Video, Image, Web Search, Code, Text, and Auto.
  Video and Auto are available only in Battle Mode. Agent Mode hides the
  work-type selector and runs as Text, matching arena.ai.
- Manual model selection only where Arena allows it: one model in Direct and
  two models in Side by Side. Battle and Agent use automatic assignment.
- Battle voting for Model A, Model B, tie, or both bad. Model names are
  revealed after a successful vote. A started chat keeps its mode, work type,
  and models; create a new chat to change them.
- Arena Agent runs inside the application through direct API calls. The local
  Agent remains a separate backend; its default for new chats is selected in
  Settings -> Permissions, not in the composer.
- Local and cloud Arena history, continuation of existing Arena sessions,
  workspace files, artifacts, media, permissions, and tool activity.
- Official Arena account connectors are loaded from the signed-in account.
  Local MCP servers are separate and can be added from the Connectors panel
  with the Add button: ready-made presets (including Playwright), a pasted
  JSON configuration, or a CLI command.
- Model, connector, and profile icons use server-provided URLs when Arena
  supplies them. Models also use organization logos, connectors use service
  favicons, and profiles use the account avatar. Initials are only a fallback.
- The sidebar, Workspace panel, and the split between files and Connectors can
  be resized by dragging their separators. Double-click a separator to reset
  that dimension. The layout is saved in the local application settings.
- Demo mode is available only with ?demo=1. Demo accounts, chats, models,
  files, connectors, media, permissions, and events are simulated locally and
  are labeled as demo data.

LOGIN AND SAFETY
----------------
- Sign in in the separate Arena window or by importing your own Arena cookies.
  The separate window closes after a successful sign-in and the application
  refreshes immediately.
- The application never asks for an Arena password and never solves a human
  check. Complete any check manually in the Arena window.
- Cookies can grant account access. Do not share them or paste them into a
  chat. Local profile data remains under %USERPROFILE%\.arena-personal for
  compatibility with earlier versions.
- Local tools run only under the selected permission mode. Bypass permissions
  is dangerous and should be used only in an isolated folder or environment.

LIMITS
------
- Arena availability, model catalogs, modalities, quotas, and rate limits are
  controlled by arena.ai and by the signed-in account. An HTTP 429 response
  is an Arena rate limit, not a local UI error.
- A local folder is not uploaded wholesale to Arena's remote sandbox. Files
  and previews appear from actual backend or Arena events.
- Only one generation can run at a time.
- User attachments are shown and handled only where the current backend and
  Arena request format support them; the UI does not claim unsupported upload
  features.
