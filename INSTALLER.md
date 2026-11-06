# Installing, updating and removing Arena Desktop

Arena Desktop ships as a single Windows executable that is also its own
installer. There is no separate setup framework and no per-machine service.

```
Arena-Desktop-Setup-1.6.0.exe    ← what users download and double-click
Arena-Desktop-1.6.0.exe          ← the same binary, for people who want no install
```

## Installing

1. Download `Arena-Desktop-Setup-<version>.exe`.
2. Double-click it. A single window opens: brand, one progress line.
3. When it finishes, **Open Arena Desktop** starts the installed app.

| | |
|---|---|
| Location | `%LOCALAPPDATA%\Programs\Arena Desktop` |
| Admin rights | none — per-user install |
| Shortcut | Start menu (`Arena Desktop`) |
| Apps & features | `Arena Desktop <version>`, with a working uninstaller |
| User data | `%USERPROFILE%\.arena-personal` — accounts, chats, settings, memory |

Nothing is written outside those paths, and no registry keys are created except
the uninstall entry under `HKCU`.

### Portable use

Run `Arena-Desktop-<version>.exe` from anywhere without `--setup`: it starts
like before and keeps its data in `%USERPROFILE%\.arena-personal`. Add an empty
`portable.txt` next to it to keep data in `.\data` instead.

## Updating

The app checks the release feed of the official upstream repo
(`PlayzAe/arena.ai-desktop`) at startup and every 6 hours.

```
Arena Desktop 1.6.1 is available      ← dialog, not a browser link
[Later]  [Download and install]
```

1. **Download and install** streams the published build into
   `%TEMP%\ArenaDesktopUpdate`.
2. The download is checked against the size and, when GitHub publishes one, the
   SHA-256 digest of the release asset. Links to anything other than
   `github.com` are refused.
3. The app closes, a second copy of itself waits for the process to exit,
   replaces `Arena-Desktop.exe` in place and starts the new version.
4. Chats, accounts, settings and memory are untouched — nothing has to be set
   up again.

`Later` keeps a small chip in the corner so the update is one click away.
Settings → About has the same actions plus manual *Check for updates*. Updates
can be disabled there entirely, and the beta channel includes pre-releases.

If a release publishes a Setup file, that file is run with `--setup --update
--silent`; if it publishes only the plain build, the in-place swap above is used
instead. Either way the installed copy is the one that ends up current.

### Why it survives an update

| Kept | Where |
|---|---|
| Accounts, cookies | `%USERPROFILE%\.arena-personal` (DPAPI-encrypted) |
| Chats | `…\.arena-personal\chats` |
| Settings, memory, MCP config | `…\.arena-personal` |
| Workspace folder | wherever the user chose |

The installer only writes inside the install folder, and never deletes the
config folder unless the user explicitly asks for it in the uninstaller.

## Removing

**Settings → Apps → Arena Desktop → Uninstall**, or run:

```powershell
& "$env:LOCALAPPDATA\Programs\Arena Desktop\Arena-Desktop.exe" --uninstall
```

The uninstaller offers one checkbox: *Also delete my chats, accounts and
settings*. Unchecked (the default) keeps user data. Silent removal:

```powershell
& "$env:LOCALAPPDATA\Programs\Arena Desktop\Arena-Desktop.exe" --uninstall --silent
```

## Building a release

The binary is built on Windows in CI (`.github/workflows/build.yml`):

1. Reads the version from `arena/version.py`.
2. `python -m PyInstaller arena-cli.spec` → `dist/Arena-Desktop.exe`.
3. Runs `Arena-Desktop.exe --version` as a smoke test.
4. Publishes `Arena-Desktop-Setup-<version>.exe` and `Arena-Desktop-<version>.exe`.

Push a tag to publish a release the updater can see:

```bash
# bump arena/version.py first, then
git tag v1.6.0 && git push origin v1.6.0
```

The tag must match `arena/version.py`; the workflow fails otherwise.

### Changing the update feed

The feed defaults to `PlayzAe/arena.ai-desktop`. To point a build somewhere else
without rebuilding, set it in `%USERPROFILE%\.arena-personal\config.json`:

```json
{ "update_repo": "owner/repo" }
```

or set `ARENA_UPDATE_REPO=owner/repo` in the environment.

## Code signing

The build is not signed. Windows SmartScreen will warn on first run:
**More info → Run anyway**. Signing needs a certificate; once one exists, add it
to the PyInstaller step (`signtool sign /fd sha256 …`) and SmartScreen stops
complaining.
