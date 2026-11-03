## Arena Desktop v5.1.5

### Daily-limit survival kit (v5.1.5)

- **Settings → Usage**: the app now logs its own sends per account/day/modality/model plus a full 429 history, so you can see where Arena's new daily balance actually goes. (It's the app's own log — Arena's official counter may differ.)
- **Agent context diet**: repeated `ls`/`search` calls inside one turn are answered from cache until files change, and long agent chats get the Compact nudge earlier (each round re-bills the whole context).
- **Lighter subagents by default**: research subagents without an explicitly chosen model run on a light (Mini/Flash/Lite-class) model; light models are badged "Light (saves daily balance)" in the picker. Toggle in Settings → About.
- **Optional account failover (off by default)**: when the daily limit hits mid-task, the app can switch to another saved account and continue in a fresh session seeded with a summary of the chat. Disabled by default on purpose: rotating accounts around a daily cap is a fair-use grey zone and accounts may be flagged — enable only if you understand the risk.

> Pacing and this toolkit make tasks resilient to Arena's limits; they do not raise or bypass Arena's daily balance. Non-affiliated community project.
