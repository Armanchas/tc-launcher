"""Display rows for the Online section, from the server's public match history.

UI-free. Every id the server sends resolves through an allowlist, so an
unrecognised mode or map reads as a generic "Match" instead of a raw id, and a
malformed entry is dropped rather than raised: the payload comes off the
network and the section must never break on it.
"""

from dataclasses import dataclass, field

from .presence import MAPS

# The server's game_mode values, as stored in prospect_matches_history.
MODES = {
    "LOOP": "Solo",
    "DUO_LOOP": "Duo",
    "SQUAD_LOOP": "Squad",
    "DM": "Deathmatch",
    # The community's Warden's Relics mode runs on the Going Dark event queue.
    "EVENT_GOINGDARK": "Warden's Relics",
}
UNKNOWN_MODE = "Match"
UNKNOWN_PLAYER = "Unknown player"
SEPARATOR = " · "


@dataclass(frozen=True)
class MatchRow:
    title: str
    created_at: int
    players: list[str] = field(default_factory=list)
    id: str = ""  # keeps a card's expanded state across the minute's re-render


def _map_id(path: str) -> str:
    # "/Game/Maps/MP/MAP02/MP_Map02_P.MP_Map02_P" -> "MP_Map02_P"
    return path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]


def _title(game_mode, map_path) -> str:
    mode = MODES.get(game_mode, UNKNOWN_MODE) if isinstance(game_mode, str) else UNKNOWN_MODE
    place = MAPS.get(_map_id(map_path), "") if isinstance(map_path, str) else ""
    return SEPARATOR.join(part for part in (mode, place) if part)


def _names(players) -> list[str]:
    if not isinstance(players, list):
        return []
    names = []
    for player in players:
        if not isinstance(player, dict):
            continue
        name = player.get("username")
        names.append(name if isinstance(name, str) and name else UNKNOWN_PLAYER)
    return names


def summarize(match) -> MatchRow | None:
    """One display row, or None for an entry that is not a usable match."""
    try:
        if not isinstance(match, dict):
            return None
        created_at = match.get("created_at")
        if not isinstance(created_at, int) or isinstance(created_at, bool):
            return None
        match_id = match.get("id")
        return MatchRow(_title(match.get("game_mode"), match.get("map")),
                        created_at, _names(match.get("players")),
                        match_id if isinstance(match_id, str) else "")
    except Exception:
        return None


def summarize_all(matches) -> list[MatchRow]:
    if not isinstance(matches, list):
        return []
    return [row for row in map(summarize, matches) if row is not None]


def relative_time(created_at: int, now: float) -> str:
    # A server clock ahead of ours gives a negative age: "just now", not "-3 min".
    age = int(now - created_at)
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{age // 60} min ago"
    if age < 86400:
        return f"{age // 3600} h ago"
    return f"{age // 86400} d ago"


NAMES_SHOWN = 4


def player_line(names: list[str], limit: int = NAMES_SHOWN) -> str:
    shown = ", ".join(names[:limit])
    extra = len(names) - limit
    return f"{shown} +{extra} more" if extra > 0 else shown


def count_label(n: int) -> str:
    return f"{n} player" if n == 1 else f"{n} players"
