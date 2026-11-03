## Arena Desktop v5.5.0

### Agent Gateway: the Arena Agent can now use your local MCP servers

The genuine Arena Agent is strong, but it lives in Arena's cloud — it cannot touch Blender, Roblox Studio, or the project on your disk. The new **Agent Gateway** bridges that, built on the same local MCP layer the app already uses for Direct chats:

- **Off by default, two explicit opt-ins** in Settings → Gateway: enable the gateway, then optionally let it auto-start. You choose exactly which of your configured local MCP servers are exposed.
- **Zero-friction when on**: with auto-start enabled and Workspace Sync on, starting a new Agent chat silently opens a temporary Cloudflare tunnel session and hands the address + pairing code to the agent in its first message — nothing to copy or paste. Manual start/stop is always available, and the URL + code can also be copied for an arena.ai web Agent chat.
- **Guardrails kept from the design's origin**: pairing code is a 32-byte random bearer (never shown in your transcript), sessions hard-expire (8 h) and idle-stop (45 min), one click revokes everything, and a visible "Gateway on" chip + recent-calls log show what's happening.
- **Approvals through the app's permission system**: calls are graded like local tools (read-only tools as reads, everything else as shell), so Plan/Accept modes ask you in the app before anything mutates your machine; Bypass doesn't.
- **Honest limits**: the agent reaches the gateway by following a short protocol over HTTPS — capable, but not as reliable as native tool calling; if the tunnel restarts, its address changes and already-running agent chats keep the old one until your next message. Anyone holding the pairing code during a session can call the exposed tools — treat it like a one-time password.

### Components manager

- **Settings → Components**: the few external helpers some features need — cloudflared (Agent Gateway), Node.js (npx MCP servers), Python (Python MCP servers), WebView2 Runtime (the app window) — each with status, version, source, and an Install button. Nothing is ever installed during a normal first launch; enabling a feature that needs a component offers the install right there. cloudflared is the official Cloudflare build with a pinned version, verified by SHA-256 after download and stored in the app data folder.
- The desktop launcher now also detects a missing WebView2 Runtime before opening any window and offers Microsoft's official download page instead of failing obscurely.

> Non-affiliated community project. The gateway exposes tools *you* configured on *your* machine, only in sessions *you* start; it does not change Arena's own limits.
