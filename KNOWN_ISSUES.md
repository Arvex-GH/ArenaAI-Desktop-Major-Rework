# Known issues

Things that are broken or unfinished in Arena Desktop. Fixes and pull requests are welcome.

## Code mode (arena.ai's sandbox)

- **Your local files are not in arena's sandbox.** The model builds in arena.ai's own sandbox, so it only sees files it made there. Arena Desktop copies what it creates and edits into your folder, and sends back the files *it* wrote that you changed. Files it never wrote (for example an old `kimi-k3.txt`) are not sent, so asking it to edit one makes it create a new file instead.
- **No file tree, editor or "Building..." view yet.** arena.ai shows a file tree, a code editor and a live preview while it builds. The app only shows the activity rows ("Created src/App.tsx") and the preview link.
- **Commands and media stay in the sandbox.** `bash` steps (like `npm install`) and `fetch_media` images run only in arena's sandbox. Run `npm install` in your folder yourself.
- **Preview link:** the `<id>.arena.site` link is picked up when arena.ai sends it in the stream; when arena finishes building after the stream ends, the link may not appear yet.
- **Battle in Code mode** writes each model's files to `option-a` and `option-b`. Not tested live yet.

## Chats

- Switching models inside a chat now keeps the same arena.ai conversation, like arena's own follow-up box. Not tested live yet; if arena.ai refuses it, the app starts a new conversation.
- Some models (seen with glm-5.3-flash and in Battle) use arena.ai's built-in tools instead of the app's file tools. Their file writes are copied from the stream into your folder; reads of your local files still need the app's tools.

## Workspace

- **Tree does not auto-refresh.** Changing files outside the app (e.g. in VS Code) does not update the workspace panel until you manually navigate or refresh.
- **Workspace preview runs scripts in a sandbox.** As of v1.3.0, `allow-scripts` is re-enabled with an opaque origin. Scripts run but cannot access the app's cookies or API token.

## Security (v1.4.0)

- **CDN scripts still load without subresource integrity.** Planned for a future release.
- **Release binary is unsigned.** No code signature, no SBOM, no CI pipeline yet. SmartScreen will warn.
- **Dependencies are unpinned.** `requirements.txt` uses lower bounds only. `pip-compile --generate-hashes` planned.
- **Cookie plaintext fallback.** If DPAPI fails, cookies are stored unencrypted. Low risk on single-user Windows desktops.
- **No rate limiting on localhost API.** Mitigated by per-launch random token and localhost binding.
- **SSRF TOCTOU gap.** DNS rebinding can bypass the `ipaddress` check between resolve and fetch. Full fix requires connecting to the resolved IP directly.

## Testing

- 33 test failures in `test_tools`, `test_uploads`, and `test_v4` suites. Upload status code assertions and MCP stdio client pagination need updating.
- The live test scripts close `arena-ai.exe` and change the shared folder setting while they run. Close the app before running them.

## Logs

- `%USERPROFILE%\.arena-cli\desktop.log`: every send, its HTTP status, human checks and each sandbox step.
- Start the app with `ARENA_STREAM_DUMP=1` to save every raw stream line to `%USERPROFILE%\.arena-cli\streams\`.
