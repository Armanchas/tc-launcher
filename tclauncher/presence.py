"""Discord Rich Presence derived from the game's own log.

The Cycle ships Discord Rich Presence, but under Proton the Game SDK cannot
reach the host's Unix socket, so it never connects. Instead the launcher reads
the game's log and publishes presence itself.

Only two log lines matter:

    LogYGameInstance: PreLoadingNewMap | new map 'Station_P'.
    LogYGameState:    OnRep_MatchState | State: [MatchInProgress]

The `LoadMap:` line is deliberately NOT parsed: it carries the live server IP
and port. `PreLoadingNewMap` is address-free.
"""

import logging
import os
import random
import re
import threading
import time
from dataclasses import dataclass, replace

from .discord_ipc import DiscordIPC

logger = logging.getLogger(__name__)

# Map id -> display name. "" means the map has its own presence state rather
# than being a match on a named map.
#
# Extend this in one line when the community pak enables another map; ids that
# aren't here are logged at debug level so they surface instead of vanishing.
#
# Outpost_P is deliberately absent: the community team confirmed it is not
# player-reachable.
MAPS: dict[str, str] = {
    "Login_P": "",
    "Station_P": "",
    "Tut_Sandbox_P": "",
    "MP_Map01_P": "Bright Sands",          # MS_MapTitle_Map01
    "MP_Map02_P": "Crescent Falls",        # MS_MapTitle_Map02
}

# Maps that are their own state rather than a match, as
# (presence key, details, flavour pool). An empty pool means the state has no
# flavour line of its own.
_MAP_STATES = {
    "Login_P": ("signing_in", "Signing in", ""),
    "Station_P": ("in_station", "In Station", "in_station"),
    "Tut_Sandbox_P": ("tutorial", "In the tutorial", ""),
}

# Maps that name a *mode* rather than a place. The community deathmatch map has
# no in-fiction name, so presence reads "Playing Deathmatch" with no map name.
# Note EYMatchmakeGameModeType has no DEATHMATCH member -- this mode is not
# matchmade, so the map is the only signal for it.
_MODE_MAPS = {
    "TestMap_DeathMatch_P": "Deathmatch",
}

# EYMatchmakeGameModeType -> display label. "" means the ordinary mode, which
# reads as plain "In Match". Unlisted tokens fall back to the same default, so
# a mode we have not seen degrades instead of leaking a raw enum name.
_MODE_NAMES = {
    "LOOP": "",
    "SQUADLOOP": "",
    "LIST": "Quest List",
    "SQUADLIST": "Quest List",
    "LOOPEXTRACT": "Extraction",
    "SQUADLOOPEXTRACT": "Extraction",
    "SQUADLOOPTOURNAMENT": "Tournament",
    "DUO": "Duo",
    "EVENT": "Event",
    "EVENTGOINGDARK": "Going Dark",
    "EVENTLOWGRAVITY": "Low Gravity",
}

_SQUAD_MODES = {
    "SQUADLOOP", "SQUADLIST", "SQUADLOOPEXTRACT", "SQUADLOOPTOURNAMENT",
}

# Squads hold four. The cap is data-driven in the game (m_maxSquadMembers,
# m_setSquadSizeOverride), so this is the observed default rather than a
# hard guarantee.
SQUAD_MAX = 4

_IN_MATCH_LABEL = "In Match"

# The second presence line ("state") is drawn from a pool, re-rolled each time
# the player enters that state, so a session does not read the same all evening.
# The game's own Generic_DiscordRP_* string leads each pool, so the default --
# what derive() produces on its own, and what a future Windows producer would
# show -- is unchanged. Community-written lines follow.
#
# Keep lines short: Discord caps "state" at 128 bytes and the squad marker
# shares the line.
FLAVOUR: dict[str, tuple[str, ...]] = {
    "in_station": (
        "Doing Station things",
        "Talking to NPCs",
        "Vibing to the Korolev theme",
        "Getting unfoamed",
        "Going out of bounds",
    ),
    "in_match": (
        "On my way to steal your minerals",
        "In a little game -_-",
        "We need more minerals than before",
        "Looking for a pactmate",
        "Investing in refiners",
    ),
    "deathmatch": (
        "No minerals, just violence",
        "Settling this the old-fashioned way",
        "Back in a second",
        "Working on the K/D",
    ),
}

LARGE_IMAGE_KEY = "the_cycle"

_MAP_RE = re.compile(r"PreLoadingNewMap \| new map '([^']+)'")
_STATE_RE = re.compile(r"OnRep_MatchState \| State: \[([^\]]+)\]")
# Logged at ordinary Log level (not Verbose) when the player queues, so the
# mode is known before the match map loads.
_MM_RE = re.compile(r"EnterMatchmaking \| Map: '[^']*', GameMode: '([^']*)', IsRanked: (\d+)")
# PrintSquad writes a header, then one continuation line per member with no
# timestamp or log category:
#     ... PrintSquad (context: ...) | Members:
#     Id: 5b3d8401-...-6b5e1aa1722d, State: IN_STATION
# The header resets the count and each member line increments it, so leaving a
# squad (a header with no members) correctly resets to zero.
_SQUAD_RE = re.compile(r"PrintSquad .*\| Members:")
_SQUAD_MEMBER_RE = re.compile(r"^Id: [0-9a-fA-F-]{36}, State: [A-Z_]+")


@dataclass(frozen=True)
class Presence:
    key: str
    details: str
    state: str = ""
    map_name: str = ""
    # Context carried across states: captured at matchmaking, applied when the
    # match map loads, cleared on returning to a hub map.
    mode: str = ""
    ranked: bool = False
    in_squad: bool = False
    squad_size: int = 0
    # Set when the map itself names the mode (e.g. Deathmatch), meaning there
    # is no place name to show.
    mode_map: str = ""
    # Which FLAVOUR pool this state's second line is drawn from ("" = none).
    # Set by derive(); the actual line is chosen by the session, so derive()
    # itself stays deterministic and the golden replay keeps working.
    flavour: str = ""


LAUNCHING = Presence("launching", "Starting up")
_GENERIC = Presence("in_game", "In game")


def _context(p: Presence) -> dict:
    """The matchmaking context to carry from one state into the next."""
    return {"mode": p.mode, "ranked": p.ranked,
            "in_squad": p.in_squad, "squad_size": p.squad_size,
            "mode_map": p.mode_map}


def _squad_marker(p: Presence) -> str:
    """Text marker for squad play, used when no member count is available."""
    return "In a squad" if p.in_squad and p.squad_size < 2 else ""


def _state_line(flavour_text: str, p: Presence) -> str:
    """The second presence line: the flavour text plus any squad marker."""
    return " · ".join(part for part in (flavour_text, _squad_marker(p)) if part)


def _default_flavour(pool: str) -> str:
    """The game's own line for `pool` -- what derive() uses on its own."""
    lines = FLAVOUR.get(pool)
    return lines[0] if lines else ""


def pick_flavour(pool: str, avoid: str = "", rng=None) -> str:
    """A random line from `pool`, never `avoid` unless it is the only one."""
    lines = FLAVOUR.get(pool)
    if not lines:
        return ""
    choices = [line for line in lines if line != avoid] or list(lines)
    return (rng or random).choice(choices)


def with_flavour(p: Presence, text: str) -> Presence:
    """`p` with its state line rebuilt from `text` and the current squad."""
    return replace(p, state=_state_line(text, p))


def _match_label(p: Presence) -> str:
    """"In Match" or the mode's own name, with a Ranked prefix when applicable."""
    name = _MODE_NAMES.get(p.mode, "")
    if p.ranked:
        return f"Ranked {name}" if name else "Ranked Match"
    return name or _IN_MATCH_LABEL


def _for_map(map_id: str, current: Presence) -> Presence:
    if map_id in _MAP_STATES:
        # Hub maps end a match: drop the mode so the next match cannot inherit
        # a stale one. Squad membership is not part of a match -- it persists
        # until the player actually leaves, which PrintSquad reports.
        key, details, pool = _MAP_STATES[map_id]
        nxt = Presence(key, details, "", flavour=pool,
                       in_squad=current.in_squad, squad_size=current.squad_size)
        return with_flavour(nxt, _default_flavour(pool))
    mode_name = _MODE_MAPS.get(map_id)
    if mode_name:
        # The map *is* the mode; it has no place name to show.
        nxt = Presence("dropping_in", f"Joining {mode_name}", "", "",
                       **{**_context(current), "mode_map": mode_name})
        return with_flavour(nxt, "")
    name = MAPS.get(map_id)
    if not name:
        # Never echo an unrecognised id into presence -- log it instead, so a
        # newly enabled community map can be added to MAPS.
        logger.debug(f"Unrecognised map id in game log: {map_id!r}")
        return _GENERIC
    return Presence("dropping_in", f"Dropping into {name}", "", name,
                    **{**_context(current), "mode_map": ""})


def derive(current: Presence | None, line: str) -> Presence | None:
    """Next presence for this log line, or None if nothing changes."""
    current = current or LAUNCHING

    match = _MAP_RE.search(line)
    if match:
        nxt = _for_map(match.group(1), current)
        return None if nxt == current else nxt

    match = _MM_RE.search(line)
    if match:
        mode = match.group(1).upper()
        nxt = replace(current, mode=mode, ranked=match.group(2) != "0",
                      in_squad=mode in _SQUAD_MODES)
        return None if nxt == current else nxt

    if _SQUAD_RE.search(line):
        # New squad listing: start counting again from zero.
        nxt = replace(current, squad_size=0)
        return None if nxt == current else nxt

    if _SQUAD_MEMBER_RE.match(line):
        # Count only. Members are account UUIDs belonging to other players and
        # must never reach a payload -- treated exactly like the server address.
        nxt = replace(current, squad_size=current.squad_size + 1)
        return None if nxt == current else nxt

    match = _STATE_RE.search(line)
    if match:
        state = match.group(1)
        if state == "MatchInProgress":
            name = current.map_name
            mode_name = current.mode_map
            if mode_name:
                details = f"Playing {mode_name}"
                pool = "deathmatch" if mode_name == "Deathmatch" else ""
            else:
                # The map name goes in the text, never only in artwork metadata.
                label = _match_label(current)
                details = f"{label} — {name}" if name else label
                pool = "in_match"
            nxt = with_flavour(
                Presence("in_match", details, "", name,
                         flavour=pool, **_context(current)),
                _default_flavour(pool))
        elif state in ("MatchEnding", "MatchOver"):
            name = current.map_name
            details = f"Match over — {name}" if name else "Match over"
            nxt = Presence("match_over", details, "", name, **_context(current))
        else:
            # MatchIntro adds nothing over the map load; DisconnectedPlayers
            # trails MatchOver and carries no presence meaning.
            return None
        return None if nxt == current else nxt

    return None


def to_activity(p: Presence, started_at: int) -> dict:
    """Discord activity payload. Must read correctly with images removed."""
    activity: dict = {"details": p.details}
    if p.state:
        activity["state"] = p.state
    activity["timestamps"] = {"start": started_at}
    activity["assets"] = {
        "large_image": LARGE_IMAGE_KEY,
        "large_text": p.map_name or "The Cycle",
    }
    if p.squad_size >= 2:
        # Renders as "(2 of 3)" on the profile. Only the count is ever sent --
        # squad members' account names stay out of the payload entirely.
        activity["party"] = {"size": [min(p.squad_size, SQUAD_MAX), SQUAD_MAX]}
    return activity


GAME_LOG_RELPATH = os.path.join(
    "drive_c", "users", "steamuser", "AppData", "Local",
    "Prospect", "Saved", "Logs", "Prospect.log",
)


def game_log_path(wine_prefix: str) -> str:
    """The game's UE log inside the configured prefix."""
    return os.path.join(os.path.expanduser(wine_prefix), GAME_LOG_RELPATH)


class PresenceSession:
    """Tails the game log on a daemon thread and publishes Discord presence.

    Best-effort throughout: every failure path degrades to no presence, never
    to a failed or delayed game launch.
    """

    def __init__(self, client_id, log_path, ipc=None, poll_interval=1.0,
                 min_update_interval=15.0, pick=pick_flavour):
        self.client_id = client_id
        # Injected so tests can make the flavour line deterministic.
        self._pick = pick
        self._flavour_text = ""
        # Last line drawn from each pool, so re-entering a state does not
        # repeat it. Kept per pool: the pool-less states in between (dropping
        # in, match over) would otherwise wipe the memory every time.
        self._last_flavour: dict[str, str] = {}
        self.log_path = log_path
        self.ipc = ipc if ipc is not None else DiscordIPC()
        self.poll_interval = poll_interval
        # Discord drops updates faster than ~1 per 15s, so hold the newest
        # state and flush on a timer rather than sending per transition.
        self.min_update_interval = min_update_interval
        self.current = LAUNCHING
        self._file = None
        self._inode = None
        self._buffer = ""
        self._started_at = int(time.time())
        self._last_sent = None  # None = nothing sent yet, so never rate-limited
        self._last_activity = None
        self._pending = False
        self._thread = None
        self._stop = threading.Event()

    def start(self) -> None:
        # Connection is established lazily in _flush(), so Discord started
        # after the game still picks presence up.
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.ipc.set_activity(None)
            self.ipc.close()
        except Exception:
            logger.debug("Discord teardown failed", exc_info=True)
        self._close_file()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._pump()
            except Exception:
                # Presence must never take the launcher down.
                logger.debug("Presence pump failed", exc_info=True)
            self._stop.wait(self.poll_interval)

    def _pump(self) -> None:
        self._ensure_open()
        if self._file is not None:
            self._buffer += self._file.read()
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                nxt = derive(self.current, line)
                if nxt is not None:
                    # Only a change of visible state restarts the elapsed
                    # timer; matchmaking and squad lines update context alone.
                    if nxt.key != self.current.key:
                        self._started_at = int(time.time())
                    # Draw a new flavour line only when the state actually
                    # changes pool, so it cannot reshuffle mid-match; then
                    # rebuild the line so a squad joining or filling shows up.
                    if nxt.flavour != self.current.flavour:
                        self._flavour_text = self._pick(
                            nxt.flavour,
                            avoid=self._last_flavour.get(nxt.flavour, ""))
                        if nxt.flavour:
                            self._last_flavour[nxt.flavour] = self._flavour_text
                    self.current = with_flavour(nxt, self._flavour_text)
                    self._pending = True
        self._flush()

    def _ensure_open(self) -> None:
        try:
            stat = os.stat(self.log_path)
        except OSError:
            return  # not created yet; keep polling
        if self._file is None:
            self._file = open(self.log_path, "r", encoding="utf-8", errors="replace")
            # Start at EOF so a stale log from a previous session is never
            # replayed as live state.
            self._file.seek(0, os.SEEK_END)
            self._inode = stat.st_ino
            return
        # The game recreates or truncates the log each launch.
        if stat.st_ino != self._inode or stat.st_size < self._file.tell():
            self._close_file()
            self._file = open(self.log_path, "r", encoding="utf-8", errors="replace")
            self._inode = stat.st_ino
            self._buffer = ""

    def _flush(self) -> None:
        if not self._pending:
            return
        now = time.monotonic()
        if self._last_sent is not None and now - self._last_sent < self.min_update_interval:
            return  # coalesce: hold the newest state, send on the next tick
        if not self.ipc.connected:
            try:
                if not self.ipc.connect(self.client_id):
                    return  # Discord isn't running; try again next tick
            except Exception:
                logger.debug("Discord connect failed", exc_info=True)
                return
        activity = to_activity(self.current, self._started_at)
        if activity == self._last_activity:
            self._pending = False  # nothing visible changed; don't resend
            return
        try:
            if self.ipc.set_activity(activity):
                self._last_sent = now
                self._last_activity = activity
                self._pending = False
        except Exception:
            logger.debug("Discord set_activity failed", exc_info=True)
            self._pending = False

    def _close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None


def start_presence(config) -> "PresenceSession | None":
    """Start a presence session for this launch, or None if it's off.

    Never raises: presence is best-effort and must not affect the launch.
    """
    if not getattr(config, "discord_presence", False):
        return None
    client_id = getattr(config, "discord_client_id", "")
    if not client_id:
        logger.debug("Discord presence enabled but no client id configured")
        return None
    try:
        session = PresenceSession(client_id, game_log_path(config.wine_prefix))
        session.start()
        return session
    except Exception:
        logger.debug("Could not start Discord presence", exc_info=True)
        return None
