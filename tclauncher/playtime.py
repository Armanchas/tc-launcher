"""Local play time: total seconds played and when the game last ran.

Counted on this machine only, from a successful launch to the game's exit.
Kept out of config.json on purpose: prospect-og rewrites that file with only
its own keys. Nothing here may block or fail a launch, so every read and write
swallows its errors.
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Callable

from .config import LAUNCHER_USERDIR

logger = logging.getLogger(__name__)

PLAYTIME_FILE = os.path.join(LAUNCHER_USERDIR, "playtime.json")


class PlayTimeStore:
    def __init__(self, path: str = PLAYTIME_FILE):
        self.path = path
        self.total_seconds: int = 0
        self.last_played: float | None = None

    def load(self) -> None:
        self.total_seconds, self.last_played = 0, None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        total, last = data.get("total_seconds"), data.get("last_played")
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            self.total_seconds = total
        if isinstance(last, (int, float)) and not isinstance(last, bool):
            self.last_played = float(last)

    def add(self, seconds: float, ended_at: float) -> None:
        if seconds > 0:
            self.total_seconds += int(seconds)
        self.last_played = ended_at
        self._save()

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"total_seconds": self.total_seconds,
                           "last_played": self.last_played}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning("Could not save play time: %s", e)


class PlaySession:
    """One game session. checkpoint() banks the time so far, so a killed
    launcher loses at most one checkpoint interval."""

    def __init__(self, store: PlayTimeStore, now: Callable[[], float] = time.time):
        self.store = store
        self.now = now
        self._mark: float | None = None

    def start(self) -> None:
        self._mark = self.now()

    def checkpoint(self) -> None:
        if self._mark is None:
            return
        current = self.now()
        self.store.add(current - self._mark, ended_at=current)
        self._mark = current

    def finish(self) -> None:
        self.checkpoint()
        self._mark = None


def format_play_time(seconds: int) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min"


def format_last_played(ts: float | None, now: datetime) -> str:
    if ts is None:
        return "Never"
    day = datetime.fromtimestamp(ts).date()
    days = (now.date() - day).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"{day.day} {day:%b %Y}"
