"""Open files/folders/URLs with the host's default handler, safely from a frozen build.

QDesktopServices.openUrl spawns xdg-open with the *frozen* process environment:
PyInstaller points LD_LIBRARY_PATH into the bundle (and Qt adds plugin-path
vars), so a Qt-based host handler like KDE's kde-open loads the bundled Qt and
crashes ("Could not read file ..."). Spawning xdg-open ourselves with a
scrubbed environment sidesteps that.
"""

import logging
import os
import shutil
import subprocess
import webbrowser

logger = logging.getLogger(__name__)

# Set by the frozen app for itself; must not leak to host helper processes.
_BUNDLE_ONLY_VARS = ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH")

# PyInstaller's own bookkeeping. Matched by prefix rather than by name so a new
# one in a future release cannot quietly start leaking again; `_MEIPASS2` is the
# pre-6.x spelling and has no prefix.
_PYI_STATE_PREFIX = "_PYI"
_PYI_LEGACY_VARS = ("_MEIPASS2",)


def clean_child_env(env: dict[str, str]) -> dict[str, str]:
    """Environment for host child processes: undo PyInstaller's overrides.

    PyInstaller saves the pre-launch LD_LIBRARY_PATH in LD_LIBRARY_PATH_ORIG;
    restore it (or drop the override entirely) and remove Qt plugin paths that
    only make sense inside the bundle.

    Also strips PyInstaller's internal `_PYI_*` state. `_PYI_PARENT_PROCESS_LEVEL`
    is how the bootloader decides whether it is a top-level process or a child:
    unset means top-level. Leaking it into a relaunched launcher.exe makes the
    new process think it is a worker, so it runs the bootloader's parent
    executable check, finds whatever spawned it instead of itself, and exits
    with "Security validation failure: failed to obtain executable path for
    parent process!". That check exists precisely to stop a spoofed environment
    pointing an executable at an arbitrary application directory -- we were
    tripping it by accident.
    """
    cleaned = dict(env)
    original = cleaned.pop("LD_LIBRARY_PATH_ORIG", None)
    if original:
        cleaned["LD_LIBRARY_PATH"] = original
    else:
        cleaned.pop("LD_LIBRARY_PATH", None)
    for var in _BUNDLE_ONLY_VARS + _PYI_LEGACY_VARS:
        cleaned.pop(var, None)
    for var in [k for k in cleaned if k.startswith(_PYI_STATE_PREFIX)]:
        cleaned.pop(var, None)
    return cleaned


def open_path(path: str) -> bool:
    """Open a file or directory with the desktop's default handler.

    Returns False when xdg-open is unavailable so the caller can fall back
    to QDesktopServices (fine when running from source).
    """
    xdg_open = shutil.which("xdg-open")
    if xdg_open is None:
        return False
    subprocess.Popen(
        [xdg_open, path],
        env=clean_child_env(dict(os.environ)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True


def open_url(url: str) -> bool:
    """Open a URL in the default browser.

    webbrowser.open() would spawn the browser helper with the frozen
    environment (same crash as open_path's rationale), so prefer xdg-open
    with a scrubbed environment; webbrowser is the non-frozen fallback.
    """
    xdg_open = shutil.which("xdg-open")
    if xdg_open is None:
        webbrowser.open(url)
        return True
    subprocess.Popen(
        [xdg_open, url],
        env=clean_child_env(dict(os.environ)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return True