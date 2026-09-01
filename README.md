# tc-launcher

A launcher for **The Cycle** community servers, for **Windows and Linux**.

It works with the same servers, logins, and mods as the old launcher, and reads
the same `config.json`, so an existing install carries its server and its login
straight over.

Both builds come from the same code, so the two platforms stay in step.

---

## Windows

### Install

1. Download `launcher.exe` from the
   [Releases page](https://github.com/Armanchas/tc-launcher/releases/latest).
   The file name has no version in it. That is deliberate, so the launcher can
   replace itself when it updates.
2. Move it into the game's `Release` folder, the one holding
   `Prospect\Binaries\Win64\Prospect-Win64-Shipping.exe`, so that `launcher.exe`
   sits next to the `Prospect` folder.
3. Double-click it. There is no installer. The launcher finds the game beside it
   and sets itself up.

Put it somewhere else and nothing breaks. The main screen shows a **Locate...**
link instead, and you point it at the `Release` folder once.

You can keep the old launcher installed alongside this one. They share the same
`config.json`, so whichever you run finds the same server and login.

### Before you press Play

**Steam has to be running and signed in.** The game gets its login ticket from
Steam, so a closed Steam client (or one still sitting on its sign-in screen)
fails with an authentication error. The launcher warns you first if it cannot
see Steam running or cannot see an account signed in.

The launcher writes a small `steam_appid.txt` next to the game executable each
time it launches, so the game folder needs to be writable. If the game lives
somewhere like `C:\Program Files`, move it or run the launcher as administrator.

### First run

1. **Select server**, using the community server's discovery URL. If you have
   used the old launcher on this PC, this is already filled in.
2. **Log in with Steam.** Your browser opens Steam's sign-in page. An existing
   session from the old launcher carries over and needs no new login.
3. **Manage mods**, if the server uses them.
4. **Press Play.** While the game runs, the button reads **Stop game**.

---

## Linux

### Requirements

- [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher) (`umu-run` on PATH)
- A Proton build, either Steam's or
  [Proton-GE](https://github.com/GloriousEggroll/proton-ge-custom). The launcher
  finds installs in Steam's `steamapps/common` and `compatibilitytools.d`.
- The **native Steam client**, running and signed in when you launch.
- The game files, in the folder holding
  `Prospect/Binaries/Win64/Prospect-Win64-Shipping.exe`.

Optional: `gamemoderun` and `mangohud` for the launch toggles.

The game signs in through Steam, and plain Wine has no Steam client for it to
talk to. Proton connects those calls to your native Linux Steam, which is why
umu and Proton are required rather than optional.

### Install

Download `TCLauncher-<version>-x86_64.AppImage` from the
[Releases page](https://github.com/Armanchas/tc-launcher/releases/latest), then:

```sh
chmod +x TCLauncher-*-x86_64.AppImage
./TCLauncher-*-x86_64.AppImage
```

The AppImage does not need to live in the game folder. You point it at the game
once with the Locate link.

### First run

1. **Locate your game files.** The folder is usually called `Release`. You only
   need this before pressing Play, and Settings can set it too.
2. **Select server**, using the community server's discovery URL.
3. **Log in with Steam** in the browser window that opens.
4. **Pick a Proton version** in Settings. Use Refresh after installing one.
5. **Press Play**, with Steam running.

> **The first launch is slow.** umu downloads the Steam Linux Runtime and Proton
> builds its prefix, which can take several minutes with no game window. The
> launcher shows a "downloading runtime" message while it works. Later launches
> are quick. Everything goes to `~/.tclauncher/game.log` if you want to watch.

Keep the native Steam client running and signed in. The launcher handles the
rest of the Steam setup itself on every launch. If sign-in fails on a brand new
Steam install, open Steam, let it finish updating, turn on Steam Play in its
settings, and restart it once.

---

## Updating

The launcher checks for a new release at startup. If one exists, it says so in
the status bar as a link. There is no pop-up and no automatic download. Click
it, confirm, and the launcher downloads the new build, closes, swaps itself, and
restarts.

- Nothing happens without your click. A failed check is silent.
- It will not update while the game is running. Close the game first.
- If the launcher cannot write to its own folder, it offers a download link
  instead of failing partway.

## Settings

- **Game directory**, where the game lives
- **Launch flags**, extra game arguments such as `-log`
- **Environment variables**, applied to the game process
- **Show game status on Discord**, on by default

Linux only, hidden on Windows where the game runs natively:

- **Proton version**, auto-detected installs plus a manual path
- **Wine prefix**, default `~/.tclauncher/prefix`
- **umu-run path**, to override the detected one
- **GameMode / MangoHud** launch wrappers

## Discord Rich Presence

While you play, the launcher shows your status on Discord: In Station, In Match
with the map, Deathmatch, your squad size, and a match timer. It reads this from
the game's own log and never publishes a server address or anyone else's
identity. Turn it off in Settings. If Discord is not running, nothing happens.

## Files and logs

Everything the launcher writes lives in `~/.tclauncher/`, which on Windows is
`C:\Users\<you>\.tclauncher\`. The Logs link in the status bar opens it.

| Path | Purpose |
| --- | --- |
| `config.json` | Settings and your saved session |
| `launcher.log` | The launcher's own log |
| `game.log` | Game output, plus a diagnostics block describing your setup |
| `mods/` | Downloaded mod archives |
| `prefix/` | Wine/Proton prefix (Linux only) |

The game's own log is at `%LOCALAPPDATA%\Prospect\Saved\Logs\Prospect.log` on
Windows, and inside the prefix under
`~/.tclauncher/prefix/drive_c/users/steamuser/AppData/Local/` on Linux.

## Troubleshooting

Start with `~/.tclauncher/game.log`. The `=== launch diagnostics ===` block at
the top records your launcher version, your Steam install, whether Steam was
running and signed in, and on Linux your Proton and prefix state. It contains no
account names, so it is safe to share.

**Login error, `SteamUnavailable`, or "conditions not met"**

Steam is not running, or no account is signed in. Start Steam, sign in, then
press Play. Check the `Steam running:` and `Steam signed in:` lines in the
diagnostics block. On Linux, if this persists on a new Steam install, see the
note at the end of the Linux section.

**`SteamAuthorizationFailed` or "Active session is expired"**

This one is not about Steam. Your launcher login has expired. Press Log in,
sign in again, then Play.

**The game closes right away**

Read `~/.tclauncher/game.log` and look for `Client API initialized 1`, which
means Steam was fine, versus `conditions not met`, which means it was not.

**Nothing happens on the first Play (Linux)**

Expected while the runtime downloads and the prefix builds. Watch
`~/.tclauncher/game.log`.

Windows may show a SmartScreen warning the first time, because the download is
not code-signed.

## Resetting

- **Switch account:** press Log out.
- **Rebuild the Proton prefix (Linux):** delete `~/.tclauncher/prefix/`. The next
  launch rebuilds it, which is slow.
- **Start over:** delete the `.tclauncher` folder in your home directory.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest                  # offline and headless
.venv/bin/python -m tclauncher    # run the GUI
```

Builds, both of which run the tests first and refuse to build if they fail:

```sh
scripts/build-appimage.sh                                            # Linux
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1   # Windows
```

The Windows artifact has to stay named exactly `launcher.exe`, because the
self-updater matches the release asset by that name.

`CLAUDE.md` is the guide for anyone changing the code, and
`docs/windows-release-checklist.md` covers cutting a Windows release.
