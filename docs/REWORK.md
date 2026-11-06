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

A calm rail, 252px wide, in the app's own voice:

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
 ◑  Demo User
    Simulation · no tokens
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
  and the thread, one above the account block, nothing else.

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

## Still to come

- **Session list presentation.** Grouping (today / this week / older), the way a
  chat row shows mode and age, and the behaviour of the list while a chat is
  open. Deferred on purpose: the rail had to be right first.
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
