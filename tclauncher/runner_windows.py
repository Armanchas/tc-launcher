"""Launching the game on Windows.

Far simpler than the Linux path: no umu, no Proton, no prefix, no steamclient
bridge. The game talks to the native Steam client directly, exactly as it did
under prospect-og, and steam_appid.txt is the only Steam configuration needed.
"""

import logging
import os
import subprocess
import sys
import threading
from typing import Callable

from .config import GAME_LOG, ConfigManager
from .desktop import clean_child_env
from .diagnostics import steam_login_summary

logger = logging.getLogger(__name__)

STEAM_APPID = "480"

# Fallbacks if the registry read fails (unusual, but a portable/renamed install
# or a locked-down user hive can do it).
STEAM_INSTALL_DIRS = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
]


def find_steam_install_path() -> str | None:
    """The Steam client install directory, via the registry then defaults."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "SteamPath")
            if path and os.path.isdir(path):
                return os.path.normpath(path)
    except (ImportError, OSError):
        pass
    for candidate in STEAM_INSTALL_DIRS:
        if os.path.isdir(candidate):
            return candidate
    return None


def _steam_registry_dword(name: str) -> int | None:
    r"""Read HKCU\Software\Valve\Steam\ActiveProcess\<name>, or None.

    This is the key the Steam client itself maintains and Steamworks reads, so
    it answers both "is Steam up" and "is anyone signed in" without spawning a
    process. That matters: the launcher is a --noconsole build, and a console
    child there can fail in ways that are invisible from inside the app.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam\ActiveProcess") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return int(value)
    except (ImportError, OSError, TypeError, ValueError) as e:
        logger.debug(f"Steam ActiveProcess\\{name} unreadable: {e}")
        return None


def steam_active_user() -> int | None:
    """SteamID of the signed-in user; **0 means Steam is up but signed out**.

    None means we could not tell. Signed-out is the state that produces
    `Failed to acquire Steam auth session ticket` in the game's log while
    `Client API initialized 1` still succeeds — the client is reachable, it
    just has no account to issue a ticket for.
    """
    return _steam_registry_dword("ActiveUser")


def is_steam_running() -> bool:
    """True if the Steam client is up.

    Registry first. `pid` can in principle go stale if Steam is killed rather
    than closed, but a false positive only drops a warning, whereas the false
    NEGATIVE this replaced sent a tester hunting a Steam problem that was not
    there.
    """
    pid = _steam_registry_dword("pid")
    if pid is not None:
        return pid != 0
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"],
            stdin=subprocess.DEVNULL,   # --noconsole: no valid handle to inherit
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError) as e:
        # Never swallow this again: the first tester round reported
        # "Steam running: NO" against a Steam that was demonstrably running,
        # and there was nothing in the log to say why.
        logger.debug(f"Steam process check failed: {e}")
        return False
    return "steam.exe" in out.lower()


def steam_preflight_issue(config_compat: str = "") -> str | None:
    """A user-facing warning if Steam looks like it will fail auth, else None.

    Windows has no compat-path/running-install mismatch to check — that failure
    mode was umu-specific — so this is only the not-running case.
    """
    if not is_steam_running():
        return (
            "The Steam client does not appear to be running. The game "
            "authenticates through Steam, so launching without it will fail "
            "with an authentication error."
        )
    if steam_active_user() == 0:
        # Distinct from "not running" on purpose: a Steam sitting at its login
        # screen still answers SteamAPI_Init, so the game gets as far as
        # "Client API initialized 1" and only then fails to obtain a ticket.
        return (
            "Steam is running but no account is signed in. The game gets its "
            "authentication ticket from the signed-in Steam account, so "
            "launching now will fail at the login screen."
        )
    return None


def diagnostic_lines(env: dict, game_exe_dir: str) -> list[str]:
    """Windows-only diagnostic rows."""
    if hasattr(sys, "getwindowsversion"):
        lines = [f"system = Windows build {sys.getwindowsversion().build}"]
    else:
        # Reachable only when these tests run on Linux.
        lines = ["system = (not Windows)"]
    steam_path = find_steam_install_path() or ""
    lines.append(f"Steam install: {steam_path or 'NOT FOUND'}")
    lines.append(f"Steam running: {'yes' if is_steam_running() else 'NO'}")
    active = steam_active_user()
    lines.append(
        "Steam signed in: "
        + ("unknown" if active is None else ("NO (signed out)" if active == 0 else "yes"))
    )
    lines.append(f"Steam login (on-disk): {steam_login_summary(steam_path)}")
    appid_file = os.path.join(game_exe_dir, "steam_appid.txt")
    lines.append(
        f"steam_appid.txt: {appid_file} "
        f"({'present' if os.path.isfile(appid_file) else 'MISSING'})"
    )
    return lines


class GameRunner:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.process: subprocess.Popen | None = None
        self.user_stopped = False

    def is_running(self) -> bool:
        return self.process is not None

    def needs_first_run_setup(self) -> bool:
        """Always False: there is no prefix to build on Windows."""
        return False

    def write_steam_appid(self):
        appid_path = os.path.join(os.path.dirname(self.config.game_exe()),
                                  "steam_appid.txt")
        try:
            with open(appid_path, "w") as f:
                f.write(STEAM_APPID)
        except OSError as e:
            raise RuntimeError(f"Game directory is not writable: {appid_path} ({e})") from e

    def build_command(self) -> tuple[list[str], dict[str, str]]:
        """Returns (argv, env). Raises RuntimeError on missing prerequisites so
        the UI can show a precise message."""
        if self.config.backend_data is None:
            raise RuntimeError("No server selected. Use 'Select server' first.")
        if not self.config.has_valid_game_dir():
            raise RuntimeError(
                "Game directory is not set or does not contain the game executable."
            )
        env = dict(os.environ)
        if getattr(sys, "frozen", False):
            env = clean_child_env(env)
        env.update(self.config.env_vars)
        argv = [
            self.config.game_exe(),
            "-backend", self.config.backend_data["backend_game"],
            "-steam_auth", self.config.backend_data["steam_auth"],
            "-analytics", self.config.backend_data["analytics"],
        ] + self.config.run_args
        return argv, env

    def launch(self, on_exit: Callable | None = None):
        """Start the game and watch it on a background thread.

        Single pass, unlike Linux: there is no prefix to build first, so the
        game starts immediately and there is no gap for is_running() to cover.
        """
        from .diagnostics import format_launch_diagnostics

        argv, env = self.build_command()
        self.write_steam_appid()
        logger.info(f"Launching (output -> {GAME_LOG}): {argv}")

        log_file = open(GAME_LOG, "w")
        log_file.write("Launching: " + " ".join(argv) + "\n\n")
        try:
            log_file.write(
                format_launch_diagnostics(env, os.path.dirname(self.config.game_exe()))
                + "\n\n"
            )
        except Exception as e:  # diagnostics must never block a launch
            log_file.write(f"(launch diagnostics failed: {e})\n\n")
        log_file.flush()
        self.user_stopped = False

        try:
            self.process = subprocess.Popen(
                argv, env=env, cwd=self.config.game_dir,
                stdout=log_file, stderr=subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            raise

        def watch():
            try:
                self.process.wait()
            except Exception:
                logger.exception("Game watcher failed")
            finally:
                returncode = self.process.returncode if self.process else None
                self.process = None
                log_file.close()
                logger.info(f"Game process exited with code {returncode}")
                if on_exit is not None:
                    on_exit()

        threading.Thread(target=watch, daemon=True).start()

    def stop(self):
        """User-requested stop. No process tree to chase: Popen gives us the
        game directly, unlike Linux where umu wraps it."""
        process = self.process
        if process is None:
            return
        self.user_stopped = True
        logger.info("Stopping game process on user request")
        try:
            process.terminate()
        except OSError:
            pass

        def enforce():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Game did not exit after terminate; killing")
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10)
                except (OSError, subprocess.SubprocessError):
                    pass

        threading.Thread(target=enforce, daemon=True).start()
