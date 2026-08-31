"""Backwards-compatible surface for the platform runners.

Existing imports (`from ..runner import GAME_LOG, GameRunner,
steam_preflight_issue`) keep working; the implementation lives in
runner_linux.py / runner_windows.py behind platforms.py.
"""

from .config import GAME_LOG  # noqa: F401
from .platforms import get_runner, steam_preflight_issue  # noqa: F401

__all__ = ["GAME_LOG", "GameRunner", "get_runner", "steam_preflight_issue"]


def GameRunner(config):  # noqa: N802 - kept callable-compatible with the class
    """Construct the platform's runner. Callable like the old class."""
    return get_runner(config)
