# The 1.6.0 rework

What has changed in this fork so far, what the sidebar becomes, and what is
still to come. Written to be read in a year, so it names the files it talks
about.

Arena Desktop started as a copy of the community client and is being rebuilt
into a distribution of its own: a normal Windows installer, updates that
install themselves, and an interface with a hand-made feel instead of the
generic assistant look. **The bar is the Claude desktop app, not a template
copied from it.**

---

## 1. Installer

`Arena-Desktop-Setup-<version>.exe` is the same binary as the portable build,
started with `--setup`. Double-click it and one 540x372 window appears: the
brand, one sentence, one hairline progress bar. It asks nothing.

- **Per-user.** Installs to `%LOCALAPPDATA%\Programs\Arena Desktop`, no
  administrator prompt.
- **Registered like a normal app.** Start-menu shortcut, an entry under *Apps &
  features*, a working uninstaller at `UNINSTALLER\HKCU`.
- **Writes `install.json`** (`version`, `previous`, `app`) next to the exe, so
  the app knows what it is and where it came from.
- **Never touches user data.** `%USERPROFILE%\.arena-personal` is left exactly
  where it was: accounts, chats, settings, memory, MCP config. `portable.txt`
  and `ARENA_CONFIG_DIR` are still honoured.

Code: `arena/setup/core.py` (install, uninstall, shortcuts, registry),
`arena/setup/ui.py` (the pywebview window), `arena/setup/web/index.html` (the
page inside it, which also renders the update and uninstall states),
`arena-cli.py --setup | --uninstall | --apply-update`.

The window is cream paper (`#f4f1ea`), ink (`#14161a`), one green accent
(`#1e9e63`), and a serif wordmark. It is deliberately not a wizard and
deliberately not the app's dark theme.

## 2. Updates that install themselves

- The app checks the release feed at startup and every 6 hours, per channel
  (stable / beta), cached for 6 hours per channel.
- When something newer exists, a single dialog says so: *Version 1.6.1 is
  available*, what you have against what is offered, the release notes, the
  date and the size, then *Later* or *Download and install*.
- The download is verified against the published size and, when GitHub provides
  it, the SHA-256 digest. It is then run in place of the app; the app closes,
  the setup installs over it and the app comes back. **Nothing is
  reconfigured.**
- Running from source, or on anything that is not Windows, the same dialog
  offers the release page instead of pretending it can self-replace.

Code: `arena/updater.py` (feed, download, verify, apply),
`arena/web/server/updates.py` (the HTTP routes), `arena/web/static/js/15-update.js`
(dialog and island).

**The feed points at this fork** — `Arvex-GH/ArenaAI-Desktop-Major-Rework` — not
at the upstream project, so a build of this rework never installs the upstream
binary over itself. `ARENA_UPDATE_REPO` and `update_repo` in config still
override it.

## 3. The right column is gone (for now)

The Workspace/Connectors panel was removed rather than restyled.
`WORKSPACE_PANEL_ENABLED = false` in `js/01-core.js` gates it, the grid's third
column collapses, and the `.app` layout no longer reserves space for it.
Folder choice still works from the composer chip and Settings → Folder.

## 4. Upstream v1.5.1 merged in

The maintainer's reliability release shipped a different backend shape:

- `arena/web/app.py` (8.4k lines) is now a 40-line loader that runs the parts
  in `arena/web/server/` inside one shared namespace.
- The arena.ai page script moved to `arena/engine_js.py`.
- The frontend was reworked (status banner, run-on-this-PC, account menu,
  named errors).

What that meant here:

- Our update routes moved into their own part,
  `arena/web/server/updates.py`, registered in `PARTS`. The old
  `/api/update-check` in `chats.py` was deleted, so the feed is defined once.
- **Version has one source.** `arena/version.py` is read by `core.py`,
  `arena/__init__.py`, the installer and CI; nobody hardcodes it any more.
- `arena-cli.spec` collects `arena/web/server/` by folder
  (`collect_submodules`), because the parts are loaded by name. Without that,
  the next new part silently produces an exe that dies on launch.
- The fork's history was squashed on import and shared no ancestry with
  upstream, so it was re-parented onto `723c4ba` before merging. Future syncs
  are now a plain `git fetch upstream main && git merge upstream/main`.

## 5. How this is checked

- `tests/` — 385 tests, including installer, updater and route coverage.
- `.github/workflows/build.yml` — runs the suite **before** PyInstaller, builds
  the exe, runs `Arena-Desktop.exe --version` as a smoke test, and only
  publishes a release on a `v*` tag that matches `arena/version.py`.
- `docs/screenshots/preview-1.6.0/` — the seven frames captured from the
  merged build, including the three states of the setup window.

---

## The sidebar

### What was rejected

The version shipped before this rework had a **big New chat button** filling a
whole row, and under it two loose rows — *Models* with a count, *Search* — that
read as buttons with no structure. Against the Claude app the difference is not
colour, it is hierarchy: there, the rail is a list of quiet rows where the
current chat is the only thing that looks selected.

### What it becomes

A calm rail, 252px wide, in the app's own voice. Captured from this build:

![The rail on the home screen](screenshots/sidebar/rail-home.png)

![The rail on its own](screenshots/sidebar/rail-crop.png)

```
 ⛬ Arena  DESKTOP                      ⌄
 ─────────────────────────────────────
 +  New chat
 ─────────────────────────────────────
 ⌕  Search                    Ctrl+K
 ▤  Projects
 ◈  Artifacts
 ⚙  Customize
 ─────────────────────────────────────
 CHATS                             ⟳
   Landing page for a photo studio
   Check approach to tests
   ...
 ─────────────────────────────────────
 Terms  Privacy  Cookies
```

- **New chat is a row**, same height as its neighbours, plus glyph and label. It
  is the first thing in the list, not a call to action filling the rail.
- **The five rows keep their names** — New, Search, Projects, Artifacts,
  Customize — so the shape matches the reference without pretending the app has
  features it does not:
  - *Search* opens the chat filter that already existed.
  - *Projects* opens the folder chooser (`data-action="workspace"`).
  - *Artifacts* is wired but has nothing to show until the workspace panel
    returns; it says so instead of doing nothing quietly.
  - *Customize* opens Settings.
- **The Models row is gone.** Choosing a model belongs to the composer, where
  you already pick a model per message. (The count that lived in the rail is
  now only in the composer's picker.)
- **Hierarchy by typography, not boxes.** The section label is a 10px
  letterspaced micro-caps line; chat titles are the only 13px text; metadata
  drops to 10px and only appears on the row you are pointing at.
- **Hairlines instead of borders and shadows.** One 1px rule between the rail
  and the thread, one above the footer, nothing else.
- **The wordmark is a serif**, the same voice as the setup window, so the app
  and its installer read as one product. The "Desktop" tag lost its pill.
- **The rail header is 52px**, matching the topbar, so the two share one
  horizontal line across the window.
- **The collapse control works.** It had been drawing nothing: icon spans that
  were written straight into `index.html` (no `.icon` class) had no box, so the
  panel glyph collapsed to zero width — the same reason the footer's globe,
  settings and help buttons looked empty. One `:where([data-icon])` rule gives
  every icon span a box without touching component rules, and the control now
  flips between `panel-left` and `panel-right` as the rail opens and closes.
- **Collapsed, the same rows keep their icons.** Nothing is re-invented for the
  76px state:

![The collapsed rail](screenshots/sidebar/rail-collapsed.png)

### Both themes

The rail is built on tokens so it has a **paper-light** counterpart — warm off
white, ink text, hairlines at 8% — alongside the current dark. The choice lives
in **Settings → Appearance** and is stored in config, so it survives updates.

Light is the default for a new install: it matches the installer, which already
speaks that language. Dark is the same rail with the ink inverted, not a second
design.

This is the first slice of the theme work. The rest of the app still uses the
dark palette, so the switch is not offered in the UI until the thread, composer
and dialogs follow in the next step.

---

## Modes: Chat and Agent

The four modes used to sit in one flat list, as if Battle and Agent were the same
kind of choice. They are not. The mode picker now has two sections:

![The mode picker, grouped](screenshots/chat/modes.png)

- **Chat** — Direct, Battle Mode, Side by Side. Talking to models, with a work
  type (text, image, video, search, code) on top.
- **Agent** — the mode that works in thinking steps and runs tools itself.

The composer chip says where you are: `Chat · Direct`, `Chat · Battle`,
`Agent`. One label, two levels.

### What is intended, and not built yet

- **Files in Chat mode.** Chat should be allowed to read and edit the chosen
  folder too, with the difference being behaviour rather than permission: Chat
  answers and edits when asked, Agent plans in steps and works through them.
  Today the local file tools are reachable only from Agent Mode, so this is a
  backend change to the tool loop, not a switch: it ships when the loop is
  safe to run outside Agent Mode.
- **Arena's account connectors in Agent Mode.** The connectors a user has linked
  on arena.ai should be available to the agent, fetched from the account rather
  than configured in the app. This waits for the better implementation that is
  planned, so nothing is half-wired here.

## The composer: confirmation next to send

Bypass used to paint a red alert bar across the composer, which read as "the app
is broken" for a setting one click away. It is now a red dot beside the send
button, and the confirmation selector lives there too instead of being lost
between the folder and work-type chips.

![Bypass: a red dot, not a red screen](screenshots/chat/bypass.png)

- `Ask` · `Accept edits` · `Auto` · `Plan only` · `Bypass` — unchanged
  behaviour, new home.
- In bypass the chip turns red and a 7px dot sits between it and send. Nothing
  else on screen changes.

## The conversation: one line per call

A tool call is part of the model's reply, not a card dropped into the
conversation. It is one silver line — `Bash npm run build` — in mono, sitting at
the exact place in the answer where the model made it, with the outcome at the
end of the line (`done`, `exit 0`, `exit 1`, `running`). No box, no fill, no
shadow, no radius, and no parentheses: the tool name and what it acted on are
two weights of the same line, not a function call.

The rows are no longer collected above the answer. Each one records where in the
reply it happened, and the renderer splits the text at those points, so a
sentence, its command, and the sentence after it read in order:

![Calls folded to one line, in the order they happened](screenshots/chat/tool-done.png)

**Nothing is open by default.** A finished call is one line. While a command
runs, that one row opens by itself and streams its newest output under a
hairline; the moment it ends the detail folds away again — a 420 ms collapse,
not a snap — leaving the single line and its exit code:

![The same rows while a build runs](screenshots/chat/tool-running.png)

Clicking a finished row opens it again for inspection. Clicking any row closes
it — including one that is still running, which stays closed until it ends.
A row is a label you can open, never a panel you cannot.

### Approval is a pop-up

A confirmation is transient, so it does not live in the thread. The window dims
and a small sheet asks one question: the command, the folder, a line about what
it will do, and *Allow once* / *Allow for this session* / *Deny*. `Escape`
denies. The sheet is anchored above the composer, so the eye does not have to
travel to the middle of a long conversation to answer it:

![Approval, asked over the conversation instead of inside it](screenshots/chat/approval.png)

The thread keeps only the receipt — the same silver line, `Allowed python -m
pytest -q  allowed` — because that is a fact about the conversation, while the
question was a moment. The two toasts that used to fire around approvals (one
when it arrived, one when it was answered) are gone: the pop-up is the
notification, the receipt is the record.

### A chat is named after the prompt

The title is the user's first message, trimmed to 52 characters, set the moment
it is sent rather than after the reply. It is stored against the chat id in
`arenaPersonal.chatTitles.v1`, so a refresh from arena.ai — which still calls
the chat "New chat" — cannot rename it underneath you. Chat rows carry
`data-chat-id`, so the mapping is easy to check from the console.

### The model announces a call before writing it

The reply used to carry the raw block: a `<tool name="write_file">` with a whole
file body inside it streamed into the chat, and the app only tidied it away once
the backend had executed the call and posted a row. For a second or two the
conversation showed the file.

Now every call is announced first. The guide asks the model for a marker line
before the call and a bare `~~~` after it:

```
~~~write
<tool name="write_file"><path>src/hero.js</path><content>
…the whole file…
</content></tool>
~~~
```

The marker carries the kind, and the kind is what the chat shows while the block
streams — one silver line, breathing, with the argument appearing as soon as it
arrives:

![A command being written](screenshots/chat/tool-pending-bash.png)

![A file being written](screenshots/chat/tool-pending-write.png)

Nothing of the block underneath reaches the chat: not the tag, not the path
attribute, not one line of the file. When the closing `~~~` arrives the pending
line is removed and the real row appears once the backend has run the call, with
its exit code and its fold-away detail.

The vocabulary, one marker each: `~~~command` (run_cmd, stop_cmd), `~~~write`,
`~~~edit`, `~~~read`, `~~~search`, `~~~list`, `~~~delete`, `~~~plan`, `~~~todos`,
`~~~ask`, and `~~~tool` for connector tools.

Two rules keep it safe:

- **A closing `~~~` only counts after the call's own tag has closed.** A file
  being written may legitimately contain a `~~~` fence (Markdown), so the
  envelope cannot end inside the content.
- **Markers are display only.** The backend strips them from stored history and
  from the visible answer, and never lets one reach a tool parameter — the tag
  inside runs exactly as it did before.

If a reply stops mid-call, the pending line stays and reads `stopped` instead of
`working`, so the interruption is visible rather than silent.

## Still to come

- **The account block returns.** Sign-in state, avatar and the account menu are
  out of the rail for now; they come back with the session work and a design
  that fits the new rows.
- **Session list presentation.** Grouping (today / this week / older), the way a
  chat row shows mode and age, and the behaviour of the list while a chat is
  open. Deferred on purpose: the rail had to be right first.
- **Chat mode gets the file tools**, with Agent Mode keeping the step-by-step
  loop and gaining arena.ai's account connectors.
- **The return of the right bar** — workspace files and connectors — and with
  it the real *Artifacts* view.
- **The light theme across the app**, not just the rail, with the Settings
  switch.
- **Republishing the release.** The feed and the installer are in place; no
  `v*` tag has been pushed yet, so the updater correctly reports "up to date".
  Pushing `v1.6.0` publishes `Arena-Desktop-Setup-1.6.0.exe` and switches the
  loop on for everyone.

The rest is coming. This document will grow with each step rather than being
rewritten.
