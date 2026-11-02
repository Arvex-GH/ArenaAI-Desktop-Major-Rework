# Prompt for Code Mode - Arena Desktop Interface

This document replaces the original pre-v4.7 UI prompt. The implementation now
exists in `arena/web/static/index.html`, `arena/web/static/style.css`, and
`arena/web/static/app.js`; use those files and `README_UI.txt` as the source of
truth for the current interface.

When extending the interface:

- Keep the product name **Arena Desktop** and write all product text in English.
- Keep the existing local Flask + pywebview backend contract. Do not invent
  backend routes or call arena.ai directly from the static UI.
- The UI must run without npm, a CDN, remote fonts, or remote scripts.
- Use Arena-style mode and work-type dropdowns. Video and Auto are Battle-only;
  Agent Mode hides the work-type selector and runs as Text.
- Manual model selection exists only in Direct (one model) and Side by Side
  (two models). Battle and Agent assign models automatically and expose no
  model or Max picker in the composer.
- A started chat keeps its mode, work type, and models. A new empty chat must
  allow changing them before the first message.
- Preserve Battle voting, local/cloud history continuation, Workspace files,
  artifacts, permissions, official Arena connectors, and local MCP management.
- Local MCP setup must remain reachable from the Connectors panel's **Add**
  button: presets, pasted JSON, and a CLI command.
- Render model output safely as text/Markdown without injecting unsanitized
  model HTML. Preserve the fetch + ReadableStream SSE parser, partial UTF-8
  chunks, stop behavior, permission cards, and per-lane Battle errors.
- Use real profile, connector, and model/organization icons when the backend
  supplies them; initials are fallback only.
- Preserve resizable sidebar/Workspace/Connectors separators and persist the
  layout through backend settings.
- Demo data belongs only behind `?demo=1` and must stay visibly labeled as a
  simulation. Without demo mode, show real backend errors instead of fake data.
- Never request or store an Arena password, solve a human check, or claim an
  unsupported Arena feature works.

Before delivery, run `node --check arena/web/static/app.js`, the Python test
suite, the opt-in browser suite (`RUN_BROWSER_TESTS=1`), a PyInstaller build,
and `scripts/check_exe.py` against `Arena-Desktop.exe`.
