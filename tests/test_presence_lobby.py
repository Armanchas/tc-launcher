"""The pre-match lobby, and the seamless travel that follows it.

With the lobby on, the match map arrives by seamless travel, which logs no
PreLoadingNewMap -- only `LogWorld: SeamlessTravel to: <path>`. Without
reading that line the whole match read a bare "In Match".
"""

import os

from tclauncher.presence import LAUNCHING, derive, to_activity

MAP = "[2026.11.15-01.12.04:883][150]LogYGameInstance: PreLoadingNewMap | new map '{}'."
STATE = "[2026.11.15-01.12.06:128][237]LogYGameState: OnRep_MatchState | State: [{}]"
MM = ("[2026.11.15-01.11.33:289][391]LogYMatchmakingController: "
      "EnterMatchmaking | Map: '', GameMode: '{}', IsRanked: {}")
TRAVEL = "[2026.11.15-01.13.15:188][394]LogWorld: SeamlessTravel to: {}"
SQUAD = ("[2026.08.26-23.46.20:782][689]LogYSquadController: PrintSquad "
         "(context: FYSquadController::ProcessSquadUpdated) | Members:")
MEMBER = "Id: {}, State: IN_STATION"
UUIDS = ["5b3d8401-b3c7-43c8-9b9f-6b5e1aa1722d", "02a3dd47-1419-4fbd-b134-e43c6a819de1"]
MAP02 = "/Game/Maps/MP/MAP02/MP_Map02_P"
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "prospect_lobby_session.log")


def _run(lines, start=LAUNCHING):
    current = start
    for line in lines:
        nxt = derive(current, line)
        if nxt is not None:
            current = nxt
    return current


def _lobby(mode="LOOP", ranked=0, extra=()):
    return _run([MAP.format("Station_P"), *extra, MM.format(mode, ranked),
                 MAP.format("Station_Lobby")])


def test_lobby_reads_the_same_for_every_mode():
    # The user's call: the mode shows once the match starts, not in the lobby.
    for mode, ranked in (("LOOP", 0), ("EVENTGOINGDARK", 0), ("DUO", 0), ("LOOP", 1)):
        lobby = _lobby(mode, ranked)
        assert lobby.key == "in_lobby"
        assert lobby.details == "In Pre-Match Lobby"
        assert lobby.mode == mode  # still carried to the match


def test_lobby_without_matchmaking_context():
    assert derive(LAUNCHING, MAP.format("Station_Lobby")).details == "In Pre-Match Lobby"


def test_lobby_states_do_not_change_it():
    lobby = _lobby()
    assert _run([STATE.format("PreMatch"), STATE.format("LaunchingMatch")], lobby) == lobby


def test_travel_then_match_keeps_mode_and_map():
    lobby = _lobby("EVENTGOINGDARK")
    dropping = derive(lobby, TRAVEL.format(MAP02))
    assert dropping.key == "dropping_in"
    assert dropping.details == "Dropping into Crescent Falls"
    match = _run([STATE.format("SeamlessTravel"), STATE.format("MatchIntro"),
                  STATE.format("MatchInProgress")], dropping)
    assert match.key == "in_match"
    assert match.details == "Warden's Relics — Crescent Falls"


def test_travel_map_id_is_the_last_segment():
    lobby = _lobby()
    for target in (MAP02 + ".MP_Map02_P", MAP02 + "?listen?Game=X", MAP02 + ".MP_Map02_P?listen"):
        assert derive(lobby, TRAVEL.format(target)).details == "Dropping into Crescent Falls"


def test_unknown_travel_target_is_never_echoed():
    lobby = _lobby()
    for target in ("/Game/Maps/X/Secret_P", "1.2.3.4:7777//Game/Maps/MP/MAP02/Weird_P",
                   "/Game/Maps/Bad-Name!"):
        nxt = _run([TRAVEL.format(target)], lobby)
        text = f"{nxt.details} {nxt.state} {nxt.map_name}"
        assert "Secret" not in text and "1.2.3.4" not in text
        assert "Weird" not in text and "Bad" not in text


def test_unknown_travel_map_keeps_the_mode():
    # A map the allowlist does not know yet: no map name, but the match
    # still reads as the mode that was queued for.
    lobby = _lobby("EVENTGOINGDARK")
    match = _run([TRAVEL.format("/Game/Maps/MP/MAP03/MP_Map03_P"),
                  STATE.format("MatchInProgress")], lobby)
    assert match.details == "Warden's Relics"
    assert "Map03" not in f"{match.details} {match.state} {match.map_name}"


def test_lobby_that_never_launches_returns_to_station():
    after = _run([STATE.format("PreMatch"), STATE.format("LaunchingMatch"),
                  MAP.format("Station_P")], _lobby("EVENTGOINGDARK"))
    assert after.key == "in_station"
    assert after.mode == ""


def test_squad_survives_the_lobby():
    squad = [SQUAD, MEMBER.format(UUIDS[0]), MEMBER.format(UUIDS[1])]
    lobby = _lobby("SQUADLOOP", extra=squad)
    assert lobby.in_squad and lobby.squad_size == 2
    match = _run([TRAVEL.format(MAP02), STATE.format("MatchInProgress")], lobby)
    assert match.in_squad and match.squad_size == 2


def test_solo_squad_marker_shows_in_the_lobby():
    lobby = _lobby("SQUADLOOP")
    assert lobby.state == "In a squad"


def test_lobby_reads_without_assets():
    activity = to_activity(_lobby("EVENTGOINGDARK"), 0)
    activity.pop("assets")
    assert activity["details"] == "In Pre-Match Lobby"


def _replay():
    seen, current = [], None
    with open(FIXTURE, encoding="utf-8") as f:
        for line in f:
            nxt = derive(current, line.rstrip("\n"))
            if nxt is not None:
                current = nxt
                if not seen or (seen[-1][0], seen[-1][1]) != (nxt.key, nxt.details):
                    seen.append((nxt.key, nxt.details))
    return seen


# The whole replay as (key, details), one entry per visible change.
GOLDEN = [
    ("signing_in", "Signing in"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # LOOP
    ("dropping_in", "Dropping into Crescent Falls"),
    ("in_match", "In Match — Crescent Falls"),
    ("match_over", "Match over — Crescent Falls"),
    ("in_station", "In Station"),
    ("tutorial", "In the tutorial"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # SQUADLOOP
    ("dropping_in", "Dropping into Bright Sands"),
    ("in_match", "In Match — Bright Sands"),
    ("match_over", "Match over — Bright Sands"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # never launched
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # LOOP
    ("dropping_in", "Dropping into Bright Sands"),
    ("in_match", "In Match — Bright Sands"),
    ("match_over", "Match over — Bright Sands"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # EVENTGOINGDARK
    ("dropping_in", "Dropping into Crescent Falls"),
    ("in_match", "Warden's Relics — Crescent Falls"),
    ("match_over", "Match over — Crescent Falls"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # DUO
    ("dropping_in", "Dropping into Bright Sands"),
    ("in_match", "Duo — Bright Sands"),
    ("match_over", "Match over — Bright Sands"),
    ("in_station", "In Station"),
    ("dropping_in", "Joining Deathmatch"),                 # no lobby: loaded directly
    ("in_match", "Playing Deathmatch"),
    ("match_over", "Match over"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # LOOP
    ("dropping_in", "Dropping into Bright Sands"),
    ("in_match", "In Match — Bright Sands"),
    ("match_over", "Match over — Bright Sands"),
    ("in_station", "In Station"),
    ("in_lobby", "In Pre-Match Lobby"),                    # LOOP, 15 Nov
    ("dropping_in", "Dropping into Crescent Falls"),
    ("in_match", "In Match — Crescent Falls"),
    ("in_station", "In Station"),
]


def test_lobby_golden_replay():
    assert _replay() == GOLDEN
