"""Self-update against GitHub Releases.

NOT prospect-og's /launcher/check_update: that endpoint is keyed to
prospect-og's version numbering and its urls.win points at prospect-og's exe.
This launcher splits what prospect-og fuses (PROTOCOL_VERSION vs APP_VERSION),
so comparing against that manifest gives a wrong answer in a different way each
time -- and the failure mode is downloading prospect-og over ourselves.

GitHub Releases is where release.yml already publishes, so the CI that builds
the artifact also publishes the manifest.
"""

import logging
import os
import re
import sys
from dataclasses import dataclass

from .platforms import IS_WINDOWS

logger = logging.getLogger(__name__)

# The CURRENT repo name. The rename to tc-launcher happens later; GitHub 301s
# the old API path to the new one and requests follows redirects, so this
# survives the rename -- whereas the new name would 404 until it happens.
DEFAULT_REPO = "Armanchas/tc-launcher-linux"

_API = "https://api.github.com/repos/{repo}/releases/latest"
_TIMEOUT = 5
_NUM_RE = re.compile(r"\d+")

_HTTP = None


def _session():
    """Our own session, not BackendClient's.

    Two reasons: BackendClient's session is an instance attribute, not a
    module-level object; and its User-Agent is `TCL/<PROTOCOL_VERSION>`, which
    belongs to the community backend, not GitHub. GitHub rejects requests with
    no User-Agent outright (HTTP 403), so one must be set.
    """
    global _HTTP
    if _HTTP is None:
        import requests
        from .version import APP_VERSION
        _HTTP = requests.Session()
        _HTTP.headers.update({"User-Agent": f"TCLauncher/{APP_VERSION}"})
    return _HTTP


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    notes: str


def parse_version(tag: str) -> tuple[int, ...]:
    """(1, 0, 9) from 'v1.0.9'. Pre-release suffixes are ignored, so
    '1.0.9-beta.2' compares equal to '1.0.9'."""
    return tuple(int(x) for x in _NUM_RE.findall(tag.split("-")[0]))


def is_newer(remote: str, current: str) -> bool:
    """True if `remote` is a later version than `current`.

    Numeric, not lexical: '1.0.10' > '1.0.9' is false as strings.
    """
    r, c = parse_version(remote), parse_version(current)
    if not r:
        return False
    width = max(len(r), len(c))
    return r + (0,) * (width - len(r)) > c + (0,) * (width - len(c))


def pick_asset(assets: list[dict]) -> dict | None:
    """The release asset for this platform."""
    for asset in assets:
        name = asset.get("name", "")
        if IS_WINDOWS and name == "launcher.exe":
            return asset
        if not IS_WINDOWS and name.endswith(".AppImage"):
            return asset
    return None


def check(current_version: str, repo: str = DEFAULT_REPO, get=None) -> UpdateInfo | None:
    """The newest release if it is newer than `current_version`, else None.

    Never raises: a failed check is silent, exactly like a failed status poll.
    """
    try:
        if get is None:
            get = _session().get
        res = get(_API.format(repo=repo), timeout=_TIMEOUT)
        res.raise_for_status()
        data = res.json()
        tag = data.get("tag_name", "")
        if not is_newer(tag, current_version):
            return None
        asset = pick_asset(data.get("assets", []))
        if asset is None:
            logger.debug(f"Release {tag} has no asset for this platform")
            return None
        return UpdateInfo(version=tag.lstrip("vV"),
                          url=asset["browser_download_url"],
                          notes=data.get("body", "") or "")
    except Exception as e:
        logger.debug(f"Update check failed: {e}")
        return None


def running_bundle_path() -> str | None:
    """The file to replace, or None when running from source.

    On Linux this MUST be $APPIMAGE: sys.executable points inside the mounted
    squashfs, and writing there is the classic "it updated and I'm still on the
    old version" bug. AppImageLauncher relocating AppImages into ~/Applications
    makes trusting sys.executable worse still.
    """
    if IS_WINDOWS:
        if getattr(sys, "frozen", False) and sys.executable:
            return os.path.abspath(sys.executable)
        return None
    return os.environ.get("APPIMAGE") or None


def can_replace(path: str) -> bool:
    """True if we could swap `path` in place. Checked BEFORE offering the
    update, so we degrade to a download link instead of failing after 100MB."""
    try:
        directory = os.path.dirname(os.path.abspath(path))
        return os.path.isdir(directory) and os.access(directory, os.W_OK)
    except Exception as e:
        logger.debug(f"can_replace({path!r}) failed: {e}")
        return False


def download(url: str, dest: str, on_progress=None, get=None) -> str:
    """Stream `url` to `dest`, reporting 0.0-1.0 progress. Returns dest."""
    if get is None:
        get = _session().get
    res = get(url, stream=True, timeout=30)
    res.raise_for_status()
    total = int(res.headers.get("content-length", 0))
    written = 0
    with open(dest, "wb") as f:
        for chunk in res.iter_content(1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            written += len(chunk)
            if on_progress is not None and total > 0:
                on_progress(min(written / total, 1.0))
    if written == 0:
        raise RuntimeError("Downloaded file was empty; update aborted.")
    if total and written != total:
        # Swapping a truncated binary over the running launcher is the one
        # unrecoverable failure this module exists to avoid: the broken
        # launcher is also the thing that would have delivered the fix.
        # urllib3 normally raises on a short body when Content-Length is set;
        # this is the belt to that braces.
        raise RuntimeError(
            f"Downloaded file was incomplete ({written} of {total} bytes); "
            "update aborted.")
    return dest


def apply(new_path: str) -> None:
    """Hand off to the swap helper and exit. Does not return.

    The swap happens from a detached helper after we exit, because a running
    AppImage is FUSE-mounted and a running exe is locked -- neither can be
    overwritten in place.
    """
    from .platforms import update_swap

    old = running_bundle_path()
    if old is None:
        raise RuntimeError("Not running as a bundled build; cannot self-update.")
    update_swap(os.getpid(), old, new_path)
    logger.info("Update helper launched; exiting for the swap.")
    sys.exit(0)
