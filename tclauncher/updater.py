"""Self-update against GitHub Releases.

NOT prospect-og's /launcher/check_update: that endpoint is keyed to
prospect-og's version numbering and its urls.win points at prospect-og's exe.
This launcher splits what prospect-og fuses (PROTOCOL_VERSION vs APP_VERSION),
so comparing against that manifest gives a wrong answer in a different way each
time -- and the failure mode is downloading prospect-og over ourselves.

GitHub Releases is where release.yml already publishes, so the CI that builds
the artifact also publishes the manifest.
"""

import hashlib
import logging
import os
import re
import sys
from dataclasses import dataclass

from .platforms import IS_WINDOWS

logger = logging.getLogger(__name__)

# The repo was renamed from tc-launcher-linux before the first release that
# carried this updater, so nothing shipped ever pointed at the old path and no
# installed launcher depends on GitHub's rename redirect. Keep it that way: if
# this is ever renamed again, releases already in the wild WILL depend on that
# redirect, and the redirect dies the moment anyone creates a repo at the old
# name.
DEFAULT_REPO = "Armanchas/tc-launcher"

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
    # GitHub publishes "sha256:<hex>" on each release asset. Empty when the
    # release predates that field, or uses an algorithm we do not know.
    #
    # This is an INTEGRITY check, not a security boundary: the digest arrives
    # from the same API, over the same connection, as the file it describes, so
    # it cannot defend against a compromised origin. What it does catch is CDN
    # or storage corruption and a partially-replaced asset -- turning those into
    # a clean abort instead of an irreversible swap of a bad binary.
    sha256: str = ""


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
        digest = asset.get("digest") or ""
        prefix = "sha256:"
        # An unknown algorithm (a future "sha512:...") must read as "no digest",
        # never be compared as though it were sha256.
        sha256 = digest[len(prefix):] if digest.startswith(prefix) else ""
        return UpdateInfo(version=tag.lstrip("vV"),
                          url=asset["browser_download_url"],
                          notes=data.get("body", "") or "",
                          sha256=sha256)
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


def _discard(dest: str) -> None:
    """Remove a rejected download so `apply()` can never swap it in.

    Best-effort: failing to clean up must not mask the error that caused it.
    """
    try:
        os.unlink(dest)
    except OSError:
        logger.debug(f"Could not remove the rejected download at {dest}")


def download(url: str, dest: str, on_progress=None, get=None,
             expected_sha256: str = "") -> str:
    """Stream `url` to `dest`, reporting 0.0-1.0 progress. Returns dest.

    Verifies `expected_sha256` when given -- see `UpdateInfo.sha256` for what
    that does and does not guarantee. Any rejected download is deleted rather
    than left on disk.
    """
    if get is None:
        get = _session().get
    res = get(url, stream=True, timeout=30)
    res.raise_for_status()
    total = int(res.headers.get("content-length", 0))
    written = 0
    digest = hashlib.sha256()
    with open(dest, "wb") as f:
        for chunk in res.iter_content(1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            if on_progress is not None and total > 0:
                on_progress(min(written / total, 1.0))
    if written == 0:
        _discard(dest)
        raise RuntimeError("Downloaded file was empty; update aborted.")
    if expected_sha256:
        actual = digest.hexdigest()
        if actual != expected_sha256.strip().lower():
            _discard(dest)
            raise RuntimeError(
                "Downloaded file failed its checksum; update aborted. "
                f"Expected {expected_sha256.strip().lower()}, got {actual}.")
    if total and written != total:
        # Swapping a truncated binary over the running launcher is the one
        # unrecoverable failure this module exists to avoid: the broken
        # launcher is also the thing that would have delivered the fix.
        # urllib3 normally raises on a short body when Content-Length is set;
        # this is the belt to that braces.
        _discard(dest)
        raise RuntimeError(
            f"Downloaded file was incomplete ({written} of {total} bytes); "
            "update aborted.")
    return dest


def update_offer(current_version: str, repo: str = DEFAULT_REPO,
                 get=None) -> tuple[UpdateInfo | None, bool]:
    """(info, replaceable). Silent (None, False) when running from source.

    Guarded to frozen builds so a dev checkout never tries to replace itself.
    `replaceable` is decided BEFORE anything is downloaded, so an install in an
    unwritable directory degrades to a download link instead of failing at the
    swap with 100MB already on disk.
    """
    bundle = running_bundle_path()
    if bundle is None:
        return None, False
    info = check(current_version, repo, get=get)
    if info is None:
        return None, False
    return info, can_replace(bundle)


def staging_path(bundle: str) -> str:
    """Where to download the new build: BESIDE the target, never in %TEMP%.

    %TEMP% is routinely on a different volume from the install directory, and
    the helper's `move`/`mv` then degrades from an atomic rename to copy+delete.
    An interruption during that copy leaves a TRUNCATED launcher -- the one
    unrecoverable outcome in this whole feature, because the broken launcher is
    also the thing that would have delivered the fix. Same directory means same
    volume means rename. `can_replace()` has already proved it writable.

    The path is returned beside `bundle` exactly as given -- not normalised
    against the cwd, which would move it off that directory entirely for a
    path this platform does not read as absolute. `stage()` is the single
    place that refuses a relative path.
    """
    return bundle + ".new"


def stage(new_path: str) -> None:
    """Hand off to the swap helper and RETURN.

    The swap happens from a detached helper after we exit, because a running
    AppImage is FUSE-mounted and a running exe is locked -- neither can be
    overwritten in place.

    Split out of `apply()` for GUI callers: `apply()` ends in `sys.exit(0)`, and
    a `SystemExit` raised inside a Qt slot is handled inconsistently by PySide6
    -- it can be swallowed or abort the process, and the event loop never
    unwinds either way. The GUI calls this, then quits through Qt so
    `app.exec()` returns and shutdown runs normally.
    """
    from .platforms import update_swap

    if not os.path.isabs(new_path):
        # The .bat resolves %~f3 against the cwd the helper inherits, so a
        # relative path silently resolves somewhere else entirely.
        raise ValueError(f"Staged update path must be absolute: {new_path!r}")
    old = running_bundle_path()
    if old is None:
        raise RuntimeError("Not running as a bundled build; cannot self-update.")
    update_swap(os.getpid(), old, new_path)
    logger.info("Update helper launched.")


def apply(new_path: str) -> None:
    """`stage()` then exit. Does not return. For non-GUI callers only."""
    stage(new_path)
    logger.info("Exiting for the swap.")
    sys.exit(0)
