"""Platform dispatch.

Named `platforms` (plural) on purpose: a `platform.py` inside this package
would shadow the stdlib module for any later `import platform`.

The split is deliberately uneven. `runner` gets a real module split because
the two implementations share nothing but a class name; the one-liners (log
path, IPC endpoint) are plain dispatch here, because inventing an abstraction
for a single path string would be the wrong kind of tidy.
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")

_IPC_RANGE = range(10)


def _runner_module() -> ModuleType:
    if IS_WINDOWS:
        from . import runner_windows
        return runner_windows
    from . import runner_linux
    return runner_linux


def get_runner(config):
    """The GameRunner for this platform."""
    return _runner_module().GameRunner(config)


def steam_preflight_issue(config_compat: str = "") -> str | None:
    """A user-facing warning if Steam looks like it will fail auth, else None."""
    return _runner_module().steam_preflight_issue(config_compat)


def diagnostic_lines(env: dict, game_exe_dir: str) -> list[str]:
    """Platform-only rows for the game.log diagnostics block."""
    return _runner_module().diagnostic_lines(env, game_exe_dir)


# The path after AppData/Local is identical on both platforms; only the prefix
# differs. Kept in presence.py as GAME_LOG_RELPATH's tail.
_LOG_TAIL = os.path.join("Prospect", "Saved", "Logs", "Prospect.log")


def game_log_path(config) -> str:
    """The game's UE log for this platform."""
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local")
        )
        return os.path.join(base, _LOG_TAIL)
    prefix = os.path.expanduser(config.wine_prefix)
    return os.path.join(prefix, "drive_c", "users", "steamuser",
                        "AppData", "Local", _LOG_TAIL)


class _SocketConn:
    """Discord IPC over a Unix socket (Linux/macOS)."""

    def __init__(self, sock, name: str):
        self._sock = sock
        self.name = name

    def write(self, data: bytes) -> None:
        self._sock.sendall(data)

    def read(self, n: int) -> bytes:
        return self._sock.recv(n)

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _PipeConn:
    """Discord IPC over a Windows named pipe.

    No read timeout: a pipe file object has no settimeout() without overlapped
    I/O via ctypes, which is a lot of machinery for this. Discord always
    replies promptly, and the tailer runs on a daemon thread, so a hung read
    degrades presence without touching the launcher or blocking exit.
    """

    def __init__(self, handle, name: str):
        self._f = handle
        self.name = name

    def write(self, data: bytes) -> None:
        self._f.write(data)
        self._f.flush()

    def read(self, n: int) -> bytes:
        return self._f.read(n)

    def close(self) -> None:
        try:
            self._f.close()
        except OSError:
            pass


def _discord_socket_dirs() -> list[str]:
    """Directories Discord clients place their IPC socket in: native, Flatpak
    and Snap installs each land somewhere different."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return [
        base,
        os.path.join(base, "app", "com.discordapp.Discord"),
        os.path.join(base, "app", "dev.vencord.Vesktop"),
        os.path.join(base, "snap.discord"),
    ]


def open_discord_ipc():
    """Connect to a running Discord, or None if there isn't one.

    Discord not running is the normal case, not an error.
    """
    if IS_WINDOWS:
        for n in _IPC_RANGE:
            path = rf"\\.\pipe\discord-ipc-{n}"
            try:
                return _PipeConn(open(path, "r+b", buffering=0), path)
            except OSError:
                continue
        return None
    import socket
    for directory in _discord_socket_dirs():
        for n in _IPC_RANGE:
            path = os.path.join(directory, f"discord-ipc-{n}")
            if not os.path.exists(path):
                continue
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(path)
                return _SocketConn(sock, path)
            except OSError:
                continue
    return None


def helper_script() -> str:
    """Path to the bundled swap helper for this platform."""
    name = "update-helper.bat" if IS_WINDOWS else "update-helper.sh"
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "scripts", name)


def update_swap(pid: int, old: str, new: str) -> None:
    """Spawn the detached helper that waits for `pid`, swaps, and relaunches.

    The helper is copied OUT of the bundle first, and the copy is what runs.
    Both bundles vanish the moment we exit -- PyInstaller deletes a onefile
    `_MEIPASS` directory, and a running AppImage's squashfs is unmounted --
    while the helper is still sitting in its wait loop. `cmd.exe` re-reads a
    .bat file as it executes each line, so a helper running from inside the
    bundle can lose its own script halfway through the swap. Running from a
    stable copy removes the question on both platforms.

    The copy is deliberately not cleaned up: deleting it would put us back to a
    script trying to remove itself while running. It is one small file per
    update, in the OS temp directory.
    """
    helper = helper_script()
    stable = os.path.join(tempfile.mkdtemp(prefix="tclauncher-update-"),
                          os.path.basename(helper))
    shutil.copy2(helper, stable)
    if not IS_WINDOWS:
        os.chmod(stable, 0o755)
    helper = stable
    if IS_WINDOWS:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        # `call` is load-bearing, not decoration. tempfile puts the helper
        # under %TEMP%, i.e. inside the user profile, so any username with a
        # space in it ("John Doe") makes list2cmdline quote the FIRST token
        # after /c. Per `cmd /?`, a command line that begins with a quote and
        # holds more than two quotes has its leading quote and its last quote
        # stripped -- the line is then mangled and the command name parses as
        # "C:\Users\John". The unquoted word `call` in front means the line no
        # longer begins with a quote, so that rule never fires.
        subprocess.Popen(
            ["cmd", "/c", "call", helper, str(pid), old, new],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
    else:
        subprocess.Popen(["sh", helper, str(pid), old, new],
                         start_new_session=True, close_fds=True)
