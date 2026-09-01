# tc-launcher

A launcher for **The Cycle** community servers, for **Windows and Linux**. It is
protocol-compatible with the original Windows launcher (prospect-og), so you get
the same server discovery, Steam OpenID login, mod management, and game
arguments — and it reads the same `config.json`, so an existing prospect-og
install carries its server and its login straight over.

- **Windows** runs the game directly, the way prospect-og does. It adds a
  settings dialog, per-mod status, a Stop-game button, Discord Rich Presence,
  and self-update.
- **Linux** runs the Windows game through **umu-launcher + Proton**, and adds
  the Proton picker, prefix management, launch flags, environment variables, and
  GameMode/MangoHud toggles.

Both builds come from the same code, so the two never drift apart.

---

## Windows

### Install

1. **Download `launcher.exe`** from the
   [Releases page](https://github.com/Armanchas/tc-launcher/releases/latest).
   The file name has **no version in it** — that is on purpose, so the launcher
   can replace itself when it updates. It will land in your Downloads folder
   simply as `launcher.exe`; the version is on the release page you took it
   from.
2. **Move it into the game's `Release` folder** — the one that contains
   `Prospect\Binaries\Win64\Prospect-Win64-Shipping.exe`, so that `launcher.exe`
   sits directly beside the `Prospect` folder. This is where prospect-og lives
   too, and putting it there means there is nothing to configure.
3. **Double-click it.** There is no installer and no setup wizard. The launcher
   notices the game beside it and adopts that folder by itself.

If you put it somewhere else, nothing breaks and you get no error: the main
screen shows a notice with a **Locate…** link instead, and you point it at the
`Release` folder once. You can also set the path later under **Settings → Game
directory**.

You can keep prospect-og installed alongside it. They share
`C:\Users\<you>\.tclauncher\config.json`, so whichever one you run finds the
same server and the same login.

### Before you press Play

**Steam has to be running and logged in.** The game authenticates through Steam
and hands that ticket to the community server, so a closed Steam client — or a
Steam window still sitting on its login screen — fails with an authentication
error. The launcher warns you before it launches if it cannot see Steam running.

The launcher writes a small `steam_appid.txt` next to the game executable on
each launch, so the game folder has to be writable. If the game sits somewhere
like `C:\Program Files`, move it or run the launcher as an administrator.

### First run

1. **Select server** — enter the community server's discovery URL, the same one
   you would use with prospect-og. If you already used prospect-og on this PC,
   this is already filled in.
2. **Log in with Steam** — your browser opens Steam's OpenID page. Sign in and
   come back to the launcher. An existing prospect-og session carries over and
   needs no new login.
3. **Manage mods**, if the server uses them.
4. **Press Play**, with Steam running. While the game is running the button
   reads **Stop game**.

---

## Linux

### Requirements

- [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher) (`umu-run` on PATH)
- A Proton build (Steam's Proton or [Proton-GE](https://github.com/GloriousEggroll/proton-ge-custom)).
  The launcher auto-detects installs in Steam's `steamapps/common` and `compatibilitytools.d`.
- The **native Steam client**, installed, running, and logged in when you launch the game
- The Cycle game files (the folder with `Prospect/Binaries/Win64/Prospect-Win64-Shipping.exe`)

Optional: `gamemoderun` (Feral GameMode) and `mangohud` for the launch toggles.

### Install

Grab `TCLauncher-<version>-x86_64.AppImage` from the
[Releases page](https://github.com/Armanchas/tc-launcher/releases/latest), make
it executable, and run it:

```sh
chmod +x TCLauncher-*-x86_64.AppImage
./TCLauncher-*-x86_64.AppImage
```

Unlike the Windows build, the AppImage does not need to live in the game folder;
you point it at the game once with the Locate link.

Prefer running from source? See [Development](#development).

### Why Proton and not plain Wine?

The game authenticates through the Steamworks API (`steam_api64.dll`, appid 480).
Plain Wine has no Windows Steam client for the game to talk to, so it fails with
an authentication error at launch. Proton bridges those Steamworks calls to your
**native Linux Steam client**, which lets the game get its auth ticket. That is
why the original launcher does not work under Wine, and why this one uses
umu/Proton.

### First run

1. **Locate your game files.** While no game directory is set, the main screen
   shows a notice with a Locate link. The install folder is usually named
   `Release` and contains `Prospect/Binaries/Win64/Prospect-Win64-Shipping.exe`.
   You only need this before you press Play, and you can also set it in Settings.
2. **Select server.** Enter the community server's discovery URL, the same one
   you would use with the Windows launcher.
3. **Log in.** Your browser opens Steam's OpenID page. Sign in and return to the
   launcher.
4. **In Settings, pick a Proton version.** Use Refresh after installing one.
5. **Press Play**, with Steam running first. While the game is running the
   button reads Stop game if you need to close it from the launcher.

> First launch is slow. The first time you press Play, umu downloads the Steam
> Linux Runtime and Proton builds its prefix. This can take several minutes with
> no game window, and the launcher shows a "downloading runtime" message while it
> works. Later launches are fast. All game and Proton output goes to
> `~/.tclauncher/game.log`, so check there if the game never appears or closes
> right away.

### How Steam authentication works on Linux

The game authenticates through Steamworks (appid 480) against your native Linux
Steam client, then presents that ticket to the community server. Two things have
to be true for this to work, and the launcher handles both automatically:

1. umu has to report the right Steam appid, so the launcher sets `GAMEID=umu-480`.
2. The Proton prefix needs Steam's `steamclient` bridge files (`steamclient64.dll`
   and friends). Proton is supposed to copy these in from your Steam client, but
   umu does not pass along the path Proton needs for that, so the launcher copies
   them into the prefix itself on every launch. Without them the game's
   `SteamAPI_Init` fails with "conditions not met" and login returns
   `SteamUnavailable`.

All you have to do is keep the native Steam client running and logged in.

Those bridge files come from your Steam client's own Proton support files. If you
hit a login error on a brand-new Steam install, open Steam, let it finish
updating, enable Steam Play in its settings, and restart it once so it downloads
them.

None of this applies on Windows, where the game talks to Steam directly.

---

## Updating

The launcher checks GitHub Releases once at startup. When a newer release exists
it says so in the status bar — a link, never a pop-up, and never an automatic
download. Click it, confirm, and the launcher downloads the new build (with a
percentage), closes, swaps itself, and restarts on the new version.

- Nothing happens without your click, and a failed check is silent.
- It refuses to update while the game is running: close the game first.
- If the launcher sits in a folder it cannot write to, it offers you the
  download link instead of trying and failing.
- Running from source, it never tries to update itself.

## Settings

- **Game directory** — where the game lives
- **Launch flags** — extra game command-line arguments (e.g. `-log`)
- **Environment variables** — per-launch environment (e.g. `DXVK_HUD=fps`)
- **Show game status on Discord** — Rich Presence, on by default

Linux only, and hidden on Windows because the game runs natively there:

- **Proton version** — auto-detected installs plus manual path entry
- **Wine prefix** — where the game's prefix lives (default `~/.tclauncher/prefix`)
- **umu-run path** — override the auto-detected one
- **GameMode / MangoHud** — wrap the launch command with `gamemoderun` or `mangohud`

## Discord Rich Presence

While you play, the launcher shows your status on Discord — In Station, In Match
with the map name, Deathmatch, your squad size, and a match timer — by reading
the game's own log. It never publishes a server address or another player's
identity. Turn it off in Settings; if Discord is not running, nothing happens and
nothing breaks.

## Account status

The account card at the top shows where you stand:

- Not signed in: the main button reads Log in with Steam.
- Signed in: the button reads Play, and a Log out link lets you switch account or
  force a fresh login.
- Session expired: the card says so and the button switches back to Log in with
  Steam.

On startup the launcher checks your saved session against the server, so it will
not offer Play for a session the server has already expired.

## Files and data

Everything the launcher writes lives under `~/.tclauncher/` — on Windows, that is
`C:\Users\<you>\.tclauncher\`. The Logs link in the status bar opens that folder,
and the launcher's version is shown next to it.

| Path | Purpose |
| --- | --- |
| `config.json` | All settings and the saved session |
| `launcher.log` | The launcher's own log |
| `game.log` | The game's output, plus a diagnostics block describing your setup |
| `mods/` | Cached downloaded mod archives |
| `prefix/` | Default Wine/Proton prefix (Linux only) |

The game's own log — useful when the game itself misbehaves rather than the
launcher — is at `%LOCALAPPDATA%\Prospect\Saved\Logs\Prospect.log` on Windows,
and inside the prefix at
`~/.tclauncher/prefix/drive_c/users/steamuser/AppData/Local/Prospect/Saved/Logs/Prospect.log`
on Linux.

A `config.json` written by the original Windows launcher loads without changes.
Keys shared with it include `server_discovery_addr`, `backend_data`,
`session_id`, `refresh_token`, `exp`, and `run_args`. Keys this launcher adds are
`game_dir`, `env_vars`, `discord_presence`, `discord_client_id`, and the
Linux-only `proton_path`, `wine_prefix`, `umu_path`, `use_gamemode`, and
`use_mangohud`. The Linux-only keys are kept, not cleared, when you save settings
on Windows, so one config file survives being used on both.

## Troubleshooting

Start with `~/.tclauncher/game.log`. The block at the top of it —
`=== launch diagnostics ===` — records your launcher version, your Steam install,
whether Steam was running, and (on Linux) your Proton and prefix state. It is
written on every launch and is the fastest way to tell what went wrong. It
deliberately contains no account names.

**Login error, `SteamUnavailable`, or "conditions not met" in the log**

- Steam is not running, or is not logged in. Start Steam, sign in, then press
  Play. Check the `Steam running:` line in the diagnostics block.
- On Linux only: if it persists on a brand-new Steam install, see the note above
  about Steam's Proton support files. The diagnostics block reports
  `prefix Steam bridge:` — `MISSING` there is that problem.

**`SteamAuthorizationFailed`, or "Active session is expired"**

Not a Steam problem: your *launcher* login has expired. Press Log in to sign in
with Steam again, then Play. The game's login is tied to an active launcher
session.

**The game closes immediately**

Read `~/.tclauncher/game.log`. Look for `Client API initialized 1` (Steam is
fine) versus `conditions not met` (Steam was not reachable).

**Nothing happens on the first Play (Linux)**

Expected while the runtime downloads and the prefix builds. Watch
`~/.tclauncher/game.log` for progress.

**Windows: what is not yet confirmed**

The Windows build is new, and no one has yet run a released build on a real
Windows machine. Two things we expect but have not seen: Windows SmartScreen
warning about an unsigned downloaded executable, and the game folder needing to
be writable for `steam_appid.txt`. If you hit anything else, send
`~/.tclauncher/game.log` — the diagnostics block at the top is designed to
answer these questions without a second machine to compare against.

## Resetting

- **Log out or switch account:** press Log out, or delete `session_id`,
  `refresh_token`, and `exp` from `config.json`.
- **Rebuild the Proton prefix (Linux):** delete `~/.tclauncher/prefix/`. The next
  launch rebuilds it, which is slow because it downloads the runtime again.
- **Full reset:** delete the `.tclauncher` folder in your home directory.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                  # 226 tests, all offline and headless
.venv/bin/python -m tclauncher    # run the GUI
```

The suite runs on Linux and Windows on every push
(`.github/workflows/ci.yml`). In CI it reports `222 passed, 4 skipped`:
`tests/test_verify_compat.py` checks that file hashing stays byte-compatible with
the original launcher's `FileVerifier` (servers compare its `integrity` values)
by loading that class straight out of a `../prospect-og` checkout, which no CI
runner has.

Builds:

```sh
scripts/build-appimage.sh                                            # Linux
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1   # Windows
```

Both run the test suite first and refuse to build if it fails. The Windows
artifact must stay named exactly `launcher.exe` — the self-updater matches the
release asset by that name.

`CLAUDE.md` is the guide for anyone (human or agent) changing the code, and
`docs/windows-release-checklist.md` covers cutting a Windows release: getting a
build out of CI without publishing anything, the Wine smoke test, and the tester
checklist.
