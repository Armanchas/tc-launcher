"""Launch diagnostics written to the top of game.log.

The point is that a failing tester's log alone pins down the problem, instead
of needing a second machine to diff against. That matters more on Windows than
Linux, because the tester loop is slow.

Everything here is platform-neutral; platform-only rows come from
platforms.diagnostic_lines().
"""

import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

_DIAG_ENV_PREFIXES = ("STEAM", "PROTON", "PRESSURE_VESSEL", "UMU", "DXVK",
                      "VKD3D", "WINE")
_DIAG_ENV_KEYS = ("GAMEID", "STORE", "XDG_RUNTIME_DIR", "LD_PRELOAD",
                  "MANGOHUD", "ENABLE_GAMEMODE", "LANG")


def relevant_env(env: dict) -> list[str]:
    """Sorted 'KEY=VALUE' lines for allowlisted vars present in `env`.

    Allowlisted, never a full os.environ dump: testers share this log.
    """
    keys = {k for k in _DIAG_ENV_KEYS if k in env}
    for k in env:
        if any(k.startswith(p) for p in _DIAG_ENV_PREFIXES):
            keys.add(k)
    return [f"{k}={env[k]}" for k in sorted(keys)]


def steam_login_summary(steam_path: str) -> str:
    """Best-effort read of loginusers.vdf: account count, most-recent flag, and
    a warning if any account has WantsOfflineMode=1 (offline mode blocks auth).
    Deliberately logs no account/persona names (PII).

    The file sits at <steam>/config/loginusers.vdf on both platforms, which is
    why this is shared rather than per-platform.
    """
    if not steam_path:
        return "unknown (no Steam path)"
    vdf = os.path.join(steam_path, "config", "loginusers.vdf")
    try:
        with open(vdf, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return "no loginusers.vdf (Steam never logged in on this install?)"
    compact = re.sub(r"\s+", "", text)
    n = len(re.findall(r'"\d{17}"', text))
    most_recent = "yes" if '"MostRecent""1"' in compact else "no"
    parts = [f"{n} account(s)", f"most-recent set: {most_recent}"]
    if '"WantsOfflineMode""1"' in compact:
        parts.append("WARNING: an account has WantsOfflineMode=1 (blocks auth)")
    return ", ".join(parts)


def format_launch_diagnostics(env: dict, game_exe_dir: str) -> str:
    """The diagnostics block: shared header, platform rows, allowlisted env."""
    from .platforms import diagnostic_lines
    from .version import APP_VERSION

    build = "frozen" if getattr(sys, "frozen", False) else "source"
    lines = ["=== launch diagnostics ==="]
    lines.append(f"launcher = TCLauncher {APP_VERSION} ({build})")
    try:
        lines.extend(diagnostic_lines(env, game_exe_dir))
    except Exception as e:  # diagnostics must never block a launch
        lines.append(f"(platform diagnostics failed: {e})")
    lines.append("relevant env:")
    for line in relevant_env(env):
        lines.append(f"  {line}")
    lines.append("=== end diagnostics ===")
    return "\n".join(lines)
