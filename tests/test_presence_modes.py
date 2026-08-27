"""Game mode (from matchmaking) and squad membership."""

from tclauncher.presence import LAUNCHING, derive, to_activity

MAP = "[2026.08.26-17.31.14:600][698]LogYGameInstance: PreLoadingNewMap | new map '{}'."
STATE = "[2026.08.26-17.31.14:001][699]LogYGameState: OnRep_MatchState | State: [{}]"
MM = ("[2026.08.26-17.31.21:585][165]LogYMatchmakingController: "
      "EnterMatchmaking | Map: '', GameMode: '{}', IsRanked: {}")
SQUAD = ("[2026.08.26-23.46.20:782][689]LogYSquadController: PrintSquad "
         "(context: FYSquadController::ProcessSquadUpdated) | Members:")
# Members are emitted as continuation lines with no timestamp or log category,
# one per member, as UUIDs rather than names.
MEMBER = "Id: {}, State: IN_STATION"
UUIDS = ["5b3d8401-b3c7-43c8-9b9f-6b5e1aa1722d",
         "02a3dd47-1419-4fbd-b134-e43c6a819de1",
         "e723917a-54f6-46ee-b127-a817532ae675"]


def _squad_block(n):
    """A PrintSquad header followed by n member continuation lines."""
    return [SQUAD] + [MEMBER.format(UUIDS[i]) for i in range(n)]


def _run(lines, start=LAUNCHING):
    current = start
    for line in lines:
        nxt = derive(current, line)
        if nxt is not None:
            current = nxt
    return current


def _match(mode, ranked=0, map_id="MP_Map01_P", extra=()):
    return _run([MAP.format("Station_P"), MM.format(mode, ranked), MAP.format(map_id),
                 *extra, STATE.format("MatchInProgress")])


def test_matchmaking_line_does_not_change_the_visible_state():
    station = derive(LAUNCHING, MAP.format("Station_P"))
    nxt = derive(station, MM.format("LOOP", 0))
    assert nxt is not None            # context is captured
    assert nxt.key == station.key     # but the visible state is unchanged
    assert nxt.details == station.details


def test_default_loop_mode_still_reads_in_match():
    assert _match("LOOP").details == "In Match — Bright Sands"


def test_going_dark_replaces_the_match_label():
    assert _match("EVENTGOINGDARK").details == "Going Dark — Bright Sands"


def test_low_gravity_and_quest_list():
    assert _match("EVENTLOWGRAVITY", map_id="MP_Map02_P").details == "Low Gravity — Crescent Falls"
    assert _match("LIST").details == "Quest List — Bright Sands"


def test_ranked_prefixes_a_named_mode():
    assert _match("DUO", ranked=1).details == "Ranked Duo — Bright Sands"


def test_ranked_default_mode_reads_ranked_match():
    assert _match("LOOP", ranked=1).details == "Ranked Match — Bright Sands"


def test_unknown_mode_token_falls_back_to_in_match():
    assert _match("SOMETHING_NEW").details == "In Match — Bright Sands"


def test_squad_mode_token_marks_a_squad():
    p = _match("SQUADLOOP")
    assert p.in_squad is True
    assert "In a squad" in p.state


def test_solo_mode_is_not_a_squad():
    p = _match("LOOP")
    assert p.in_squad is False
    assert "In a squad" not in p.state
    assert "party" not in to_activity(p, 0)


def test_two_member_squad_becomes_a_party_of_two_of_four():
    """The reported real case: a duo squad, cap of 4."""
    p = _match("SQUADLOOP", extra=_squad_block(2))
    assert p.squad_size == 2
    activity = to_activity(p, 0)
    assert activity["party"]["size"] == [2, 4]
    assert "In a squad" not in p.state  # the count supersedes the text marker


def test_full_squad_of_four():
    p = _match("SQUADLOOP", extra=_squad_block(3) + ["Id: 1f81a8e4-f2cf-49df-b964-000000000000, State: IN_STATION"])
    assert to_activity(p, 0)["party"]["size"] == [4, 4]


def test_squad_member_uuids_never_reach_the_payload():
    p = _match("SQUADLOOP", extra=_squad_block(3))
    blob = repr(to_activity(p, 0))
    for uid in UUIDS:
        assert uid not in blob
    assert "Id:" not in blob


def test_a_new_squad_block_replaces_the_previous_count():
    """Leaving a squad emits a header with no members; the count must reset."""
    p = _match("SQUADLOOP", extra=_squad_block(3) + [SQUAD])
    assert p.squad_size == 0
    assert "In a squad" in p.state
    assert "party" not in to_activity(p, 0)


def test_empty_squad_line_falls_back_to_text():
    p = _match("SQUADLOOP", extra=[SQUAD])
    assert p.squad_size == 0
    assert "In a squad" in p.state
    assert "party" not in to_activity(p, 0)


def test_mode_context_resets_on_return_to_station():
    after = _run([MAP.format("Station_P"), MM.format("EVENTGOINGDARK", 1),
                  MAP.format("MP_Map01_P"), STATE.format("MatchInProgress"),
                  MAP.format("Station_P"), MAP.format("MP_Map01_P"),
                  STATE.format("MatchInProgress")])
    assert after.details == "In Match — Bright Sands"
    assert after.ranked is False


def test_deathmatch_map_is_its_own_mode():
    dropping = _run([MAP.format("Station_P"), MAP.format("TestMap_DeathMatch_P")])
    assert dropping.details == "Joining Deathmatch"
    playing = derive(dropping, STATE.format("MatchInProgress"))
    assert playing.details == "Playing Deathmatch"
    assert playing.map_name == ""


def test_deathmatch_still_reads_without_artwork():
    playing = _run([MAP.format("TestMap_DeathMatch_P"), STATE.format("MatchInProgress")])
    activity = to_activity(playing, 0)
    activity.pop("assets", None)
    assert activity["details"] == "Playing Deathmatch"


def test_squad_survives_returning_to_station_but_mode_does_not():
    """Mode ends with the match; squad membership persists until you leave it."""
    after = _run([MM.format("EVENTGOINGDARK", 1), *_squad_block(2),
                  MAP.format("MP_Map01_P"), STATE.format("MatchInProgress"),
                  MAP.format("Station_P")])
    assert after.key == "in_station"
    assert after.mode == "" and after.ranked is False   # mode ended with the match
    assert after.squad_size == 2                        # still squadded
    assert to_activity(after, 0)["party"]["size"] == [2, 4]


def test_squad_marker_goes_on_the_state_line_in_the_station():
    p = _run([MM.format("SQUADLOOP", 0), MAP.format("Station_P")])
    assert p.details == "In Station"                      # details stays clean
    assert p.state == "Doing Station things · In a squad"
