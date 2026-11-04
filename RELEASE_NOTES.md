## Arena Desktop v5.6.0

<p align="right"><a href="https://github.com/Maxusimlive/Arena-Desktop">📂 Repository on GitHub</a></p>

### Chats now stay in sync with arena.ai

A linked chat is no longer a one-time import. If you continue it in a browser, Arena Desktop now pulls the newer turns back into the same local conversation:

- Opening a linked chat quietly checks arena.ai for newer turns.
- **Refresh from Arena** in the chat header refreshes it on demand.
- Evaluation chats and native Arena Agent sessions are both supported.
- Merging avoids duplicates and keeps local tool details, votes, saved image choices, the locked mode/model, and the native Agent resume cursor.

### Arena Agent questions are visible and answerable

The built-in Arena Agent could previously call `ask_user` without the question ever reaching the desktop UI. That flow is now implemented end to end:

- Structured questions appear as cards with their answer options and, when Arena allows it, a free-text answer field.
- The answer is sent back to the same Arena Agent session with Arena's native `data-tool-results` message.
- Generation continues in the same run, and the question and answer remain in the saved transcript.

### Arena Agent image choices

When Arena Agent's `generate_image` offers two options, Arena Desktop now shows the real choice instead of leaving the session waiting invisibly:

- Both generated images are shown and can be opened full-size.
- **Use this image** sends the selection back to the same Arena session; **Skip choice** follows Arena's no-choice path.
- Neither image is deleted or hidden after the decision.
- After choosing, the model attribution for **both** images is revealed and saved with the chat.
- Candidate images are loaded through an authenticated same-origin app proxy, so no cookie or signed URL is stored in the chat file.

This release implements Arena Agent's `ask_user` and image-choice prompts. Other native prompt types are not faked: if Arena asks for one the app cannot answer yet, the session is saved and the app says so honestly.

All v5.5 features remain included, including Workspace Sync, the Agent Gateway and Components manager, rich workspace previews, parallel chats, official Arena connectors, and local MCP servers.

**Verification** — 302 automated tests pass (one pre-existing Linux-only test that expects Windows-installed browsers is excluded), 21/21 browser UI tests pass, and 59/59 packaged-EXE integrity checks pass.

📥 **Download `Arena-Desktop.exe`** (26,319,270 bytes) — no installation needed, just run it.
SHA-256: `e7efdf268d803face8807e42e0ae567a29a18f05a019e9cf1cf32b7def936cbe`
💬 Discord: `@.max_power._`
