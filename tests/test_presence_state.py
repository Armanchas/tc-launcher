from tclauncher.presence import LAUNCHING, Presence, derive, to_activity

MAP_LINE = "[2026.08.25-20.11.14:600][698]LogYGameInstance: PreLoadingNewMap | new map '{}'."
STATE_LINE = "[2026.08.25-20.18.14:001][699]LogYGameState: OnRep_MatchState | State: [{}]"


def _drive(lines, start=LAUNCHING):
    """Feed lines through derive(), returning the sequence of distinct states."""
    current = start
    seen = [current]
    for line in lines:
        nxt = derive(current, line)
        if nxt is not None:
            current = nxt
            seen.append(current)
    return seen


def test_login_map_gives_signing_in():
    p = derive(LAUNCHING, MAP_LINE.format("Login_P"))
    assert p.key == "signing_in"
    assert p.details


def test_station_map_uses_the_games_own_strings():
    p = derive(LAUNCHING, MAP_LINE.format("Station_P"))
    assert p.key == "in_station"
    assert p.details == "In Station"
    assert p.state == "Doing Station things"


def test_tutorial_map():
    p = derive(LAUNCHING, MAP_LINE.format("Tut_Sandbox_P"))
    assert p.key == "tutorial"


def test_match_map_gives_dropping_in_with_display_name():
    p = derive(LAUNCHING, MAP_LINE.format("MP_Map01_P"))
    assert p.key == "dropping_in"
    assert p.map_name == "Bright Sands"
    assert "Bright Sands" in p.details


def test_second_map_display_name():
    p = derive(LAUNCHING, MAP_LINE.format("MP_Map02_P"))
    assert p.map_name == "Crescent Falls"


def test_community_deathmatch_map_is_recognised():
    p = derive(LAUNCHING, MAP_LINE.format("TestMap_DeathMatch_P"))
    assert p.key == "dropping_in"
    assert p.map_name == "Deathmatch"


def test_match_in_progress_keeps_the_map_name():
    dropping = derive(LAUNCHING, MAP_LINE.format("MP_Map01_P"))
    p = derive(dropping, STATE_LINE.format("MatchInProgress"))
    assert p.key == "in_match"
    assert p.map_name == "Bright Sands"
    # The map name must live in the TEXT, not only in artwork metadata --
    # artwork is never load-bearing.
    assert p.details == "In Match — Bright Sands"
    assert p.state == "On my way to steal your minerals"


def test_match_ending_and_over_are_one_state():
    dropping = derive(LAUNCHING, MAP_LINE.format("MP_Map01_P"))
    playing = derive(dropping, STATE_LINE.format("MatchInProgress"))
    ending = derive(playing, STATE_LINE.format("MatchEnding"))
    assert ending.key == "match_over"
    assert derive(ending, STATE_LINE.format("MatchOver")) is None


def test_disconnected_players_is_ignored():
    dropping = derive(LAUNCHING, MAP_LINE.format("MP_Map01_P"))
    playing = derive(dropping, STATE_LINE.format("MatchInProgress"))
    over = derive(playing, STATE_LINE.format("MatchEnding"))
    assert derive(over, STATE_LINE.format("DisconnectedPlayers")) is None


def test_match_intro_after_map_load_changes_nothing():
    dropping = derive(LAUNCHING, MAP_LINE.format("MP_Map01_P"))
    assert derive(dropping, STATE_LINE.format("MatchIntro")) is None


def test_repeated_identical_line_changes_nothing():
    p = derive(LAUNCHING, MAP_LINE.format("Station_P"))
    assert derive(p, MAP_LINE.format("Station_P")) is None


def test_unknown_map_falls_back_to_generic_and_logs(caplog):
    with caplog.at_level("DEBUG"):
        p = derive(LAUNCHING, MAP_LINE.format("Outpost_P"))
    assert p.key == "in_game"
    assert "Outpost_P" not in p.details
    assert "Outpost_P" not in p.state
    assert "Outpost_P" in caplog.text  # surfaced for the map table


def test_loadmap_line_is_never_parsed():
    """The LoadMap line carries the live server IP and must be ignored."""
    line = (
        "[2026.08.25-20.17.57:001][698]LogLoad: LoadMap: "
        "145.239.1.165:39869//Game/Maps/MP/MAP01/MP_Map01_P"
    )
    assert derive(LAUNCHING, line) is None


def test_irrelevant_lines_change_nothing():
    assert derive(LAUNCHING, "LogTemp: Found Discord DLL in Binaries directory") is None
    assert derive(LAUNCHING, "") is None


def test_every_state_reads_without_artwork():
    """Artwork is never load-bearing (spec constraint)."""
    lines = [
        MAP_LINE.format("Login_P"),
        MAP_LINE.format("Station_P"),
        MAP_LINE.format("Tut_Sandbox_P"),
        MAP_LINE.format("MP_Map01_P"),
        STATE_LINE.format("MatchInProgress"),
        STATE_LINE.format("MatchEnding"),
        MAP_LINE.format("Outpost_P"),
    ]
    for p in _drive(lines):
        activity = to_activity(p, started_at=1000)
        activity.pop("assets", None)  # everything artwork lives under "assets"
        assert activity.get("details"), f"{p.key} has no details without artwork"
        blob = " ".join(str(v) for v in activity.values())
        assert p.key != "in_match" or "Bright Sands" in blob


def test_to_activity_sets_the_start_timestamp():
    p = derive(LAUNCHING, MAP_LINE.format("Station_P"))
    activity = to_activity(p, started_at=1724000000)
    assert activity["timestamps"]["start"] == 1724000000
