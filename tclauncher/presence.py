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
import re
from dataclasses import dataclass

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
    "TestMap_DeathMatch_P": "Deathmatch",  # community pak only
}

# Maps that are their own state rather than a match.
_MAP_STATES = {
    "Login_P": ("signing_in", "Signing in", ""),
    "Station_P": ("in_station", "In Station", "Doing Station things"),
    "Tut_Sandbox_P": ("tutorial", "In the tutorial", ""),
}

# Presence text mirrors the game's own Generic_DiscordRP_* strings so the
# Windows (in-game) and Linux (launcher) producers render identically.
_IN_MATCH = ("In Match", "On my way to steal your minerals")

LARGE_IMAGE_KEY = "the_cycle"

_MAP_RE = re.compile(r"PreLoadingNewMap \| new map '([^']+)'")
_STATE_RE = re.compile(r"OnRep_MatchState \| State: \[([^\]]+)\]")


@dataclass(frozen=True)
class Presence:
    key: str
    details: str
    state: str = ""
    map_name: str = ""


LAUNCHING = Presence("launching", "Starting up")
_GENERIC = Presence("in_game", "In game")


def _for_map(map_id: str) -> Presence:
    if map_id in _MAP_STATES:
        key, details, state = _MAP_STATES[map_id]
        return Presence(key, details, state)
    name = MAPS.get(map_id)
    if not name:
        # Never echo an unrecognised id into presence -- log it instead, so a
        # newly enabled community map can be added to MAPS.
        logger.debug(f"Unrecognised map id in game log: {map_id!r}")
        return _GENERIC
    return Presence("dropping_in", f"Dropping into {name}", "", name)


def derive(current: Presence | None, line: str) -> Presence | None:
    """Next presence for this log line, or None if nothing changes."""
    current = current or LAUNCHING

    match = _MAP_RE.search(line)
    if match:
        nxt = _for_map(match.group(1))
        return None if nxt == current else nxt

    match = _STATE_RE.search(line)
    if match:
        state = match.group(1)
        if state == "MatchInProgress":
            name = current.map_name
            # The map name goes in the text, never only in artwork metadata.
            details = f"{_IN_MATCH[0]} — {name}" if name else _IN_MATCH[0]
            nxt = Presence("in_match", details, _IN_MATCH[1], name)
        elif state in ("MatchEnding", "MatchOver"):
            name = current.map_name
            details = f"Match over — {name}" if name else "Match over"
            nxt = Presence("match_over", details, "", name)
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
    return activity
