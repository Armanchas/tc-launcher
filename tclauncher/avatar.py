"""The signed-in player's Steam avatar, for the account block.

Order: the local Steam client's own avatar cache (no network), then the public
profile's XML (no API key) with the image cached under ~/.tclauncher/avatars,
else None (the UI draws a placeholder). Only OIDC sessions know the steam_id;
legacy sign-in never says who signed in, and we do not guess.

Everything here blocks on disk or network: call it through run_worker.
Nothing raises.
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

from .backend import REQUEST_TIMEOUT
from .config import LAUNCHER_USERDIR

logger = logging.getLogger(__name__)

AVATAR_DIR = os.path.join(LAUNCHER_USERDIR, "avatars")
MAX_AGE = 7 * 24 * 3600   # refetch a downloaded avatar weekly
MAX_BYTES = 1024 * 1024   # Steam's full avatar is ~10-50 KB
PROFILE_URL = "https://steamcommunity.com/profiles/{}?xml=1"

_STEAM_ID = re.compile(r"\d{17}")


def valid_steam_id(steam_id: str) -> bool:
    """A SteamID64: the id becomes a file name and part of a URL."""
    return isinstance(steam_id, str) and bool(_STEAM_ID.fullmatch(steam_id))


def steam_cache_avatar(steam_dir: str | None, steam_id: str) -> str | None:
    if not steam_dir or not valid_steam_id(steam_id):
        return None
    path = os.path.join(steam_dir, "config", "avatarcache", f"{steam_id}.png")
    return path if os.path.isfile(path) else None


def parse_profile_xml(text: str) -> str | None:
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None
    url = (root.findtext("avatarFull") or "").strip()
    return url if url.startswith("https://") else None


def fetch_avatar(session: requests.Session, steam_id: str, *,
                 dest_dir: str = AVATAR_DIR, now: float | None = None) -> str | None:
    if not valid_steam_id(steam_id):
        return None
    now = time.time() if now is None else now
    path = os.path.join(dest_dir, f"{steam_id}.img")
    cached = path if os.path.isfile(path) else None
    if cached is not None:
        try:
            if now - os.path.getmtime(cached) < MAX_AGE:
                return cached
        except OSError:
            pass
    try:
        res = session.get(PROFILE_URL.format(steam_id), timeout=REQUEST_TIMEOUT)
        if res.status_code != 200:
            return cached
        url = parse_profile_xml(res.text)
        if url is None:
            return cached
        img = session.get(url, timeout=REQUEST_TIMEOUT)
        if img.status_code != 200 or not img.content or len(img.content) > MAX_BYTES:
            return cached
        os.makedirs(dest_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(img.content)
        os.replace(tmp, path)
        return path
    except Exception as e:  # network, disk, anything: the placeholder is fine
        logger.info("Steam avatar unavailable: %s", e)
        return cached


def resolve_avatar(steam_id: str, *, session: requests.Session,
                   steam_dir: str | None) -> str | None:
    if not valid_steam_id(steam_id):
        return None
    return steam_cache_avatar(steam_dir, steam_id) or fetch_avatar(session, steam_id)
