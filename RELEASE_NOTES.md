## Arena Desktop v5.6.1

<p align="right"><a href="https://github.com/Maxusimlive/Arena-Desktop">📂 Repository on GitHub</a></p>

Fix update for Arena Agent continuations and the Agent Gateway, plus one new Gateway capability.

### Fixed: continued Arena Agent chats could finish instantly, empty

After the v5.6.0 update, continuing an existing Arena Agent chat could show an empty reply that "finished" right away — no live tool activity, no text — while the same answer was visibly generating on arena.ai. The new chat-refresh could rewind the saved stream cursor to the snapshot position a few records behind actual consumption, so the next turn replayed the previous turn's end and mistook it for its own.

- The stream cursor now never moves backwards (refresh, resume) — consumption truth wins.
- A turn-complete or a settled/empty stream can no longer end a turn before that turn's own content has begun.
- Live tool calls and streaming text in continued chats behave as they did before the regression.

### Fixed: Agent Gateway did not restart for continued chats

The gateway tunnel address used to be handed to the agent only in a chat's first message. If the tunnel was stopped, idle-expired, or belonged to a previous app run, a continued chat kept dead credentials. Now, with the gateway auto-start and Workspace Sync on, every Agent turn makes sure a tunnel is running, and a continued chat is re-handed the fresh address + pairing code exactly when its session changed — not on every message.

### New: your Workspace folder through the Gateway

The Agent Gateway no longer exposes only MCP servers. Your picked Workspace folder is available to the genuine Arena Agent as a built-in `workspace` server:

- `ls`, `read_file`, `search` to inspect it; `write_file`, `edit_file`, `mkdir`, `delete` to change it; `run_cmd` for shell commands inside it.
- Paths cannot escape the folder, and calls grade under their real tool names in your Permissions mode (Accept-edits covers file edits; deletes and shell still ask, per your mode).
- It has its own switch in Settings → Gateway and turns off independently of the MCP checklist.

Everything from v5.6.0 is included: linked-chat refresh from arena.ai, visible and answerable Arena Agent `ask_user` questions, and two-image choices with both model names revealed.

**Verification** — 310 automated tests pass (one pre-existing Linux-only test that expects Windows-installed browsers is excluded), 21/21 browser UI tests pass, and 62/62 packaged-EXE integrity checks pass.

📥 **Download `Arena-Desktop.exe`** (26,323,080 bytes) — no installation needed, just run it.
SHA-256: `509d8821b0643e5160b33953c1ef8b3c94be95b8a23b3b1b2e0d9e5913535c74`
💬 Discord: `@.max_power._`
