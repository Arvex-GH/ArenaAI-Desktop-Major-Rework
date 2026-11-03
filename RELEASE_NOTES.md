## Arena Desktop v5.5.1

<p align="right"><a href="https://github.com/Maxusimlive/Arena-Desktop">📂 Repositories on GitHub</a></p>

### Rich workspace previews

Clicking a file in the Workspace panel now shows it the way it is meant to be seen — not just
as raw text:

- **HTML / SVG** renders in a sandboxed frame — relative CSS, images and scripts from the
  same folder load too, so a saved page looks like the page.
- **Markdown** renders as a formatted document.
- **Images, audio, video and PDF** open inline with real players/viewers.
- **Text / code** keeps the classic read-only view with **Copy** and **Download**;
  anything unrecognized offers **Download**.

Previews stay strictly inside the workspace folder, are served read-only with a CSP sandbox,
and HTML previews cannot reach the app or the network-facing API beyond their own files.

### Arena Agent media

Audio the Arena Agent produces (e.g. voice narration) now plays in the chat instead of
falling through as a broken image. Under the hood the native agent stream now also *logs*
unrecognized media-choice chunk shapes — groundwork for full image/voice pick support,
which needs the actual Arena protocol captured from a live session before it can be
implemented honestly.

All v5.5.0 features remain unchanged, including the Arena Agent Gateway and the Components
manager.

**Verification** — 294 automated tests pass (1 known Linux-only failure), 20/20 browser UI
tests including the new preview flows, and all 56 packaged-EXE integrity checks.

📥 **Download `Arena-Desktop.exe`** — no installation needed, just run it.
💬 Discord: `@.max_power._`
