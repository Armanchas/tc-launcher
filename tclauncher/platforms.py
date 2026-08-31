"""Platform dispatch.

Named `platforms` (plural) on purpose: a `platform.py` inside this package
would shadow the stdlib module for any later `import platform`.

The split is deliberately uneven. `runner` gets a real module split because
the two implementations share nothing but a class name; the one-liners (log
path, IPC endpoint) are plain dispatch here, because inventing an abstraction
for a single path string would be the wrong kind of tidy.
"""

import sys
from types import ModuleType

IS_WINDOWS = sys.platform.startswith("win")


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
