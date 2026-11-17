"""Formatting for the Online section: match titles, times and player lines."""

from tclauncher.community import (count_label, player_line, relative_time,
                                  summarize, summarize_all)

MAP2 = "/Game/Maps/MP/MAP02/MP_Map02_P.MP_Map02_P"
DM = "/Game/Maps/Testmaps/TestMap_DeathMatch_P.TestMap_DeathMatch_P"


def _m(**kw):
    base = {"id": "x", "map": MAP2, "game_mode": "LOOP", "status": 0,
            "created_at": 1000, "players": [{"username": "Smokey Bacon"}]}
    base.update(kw)
    return base


def test_title_mode_and_map():
    assert summarize(_m()).title == "Solo · Crescent Falls"
    assert summarize(_m(game_mode="SQUAD_LOOP")).title == "Squad · Crescent Falls"
    assert summarize(_m(game_mode="DUO_LOOP")).title == "Duo · Crescent Falls"
    assert summarize(_m(game_mode="EVENT_GOINGDARK")).title == "Warden's Relics · Crescent Falls"


def test_deathmatch_has_no_map_name():
    assert summarize(_m(game_mode="DM", map=DM)).title == "Deathmatch"


def test_unknown_mode_and_map_never_echo_ids():
    row = summarize(_m(game_mode="SECRET_MODE", map="/Game/Maps/X/Weird_P.Weird_P"))
    assert row.title == "Match"
    assert "SECRET" not in row.title and "Weird" not in row.title


def test_row_keeps_time_and_names():
    row = summarize(_m(created_at=1234))
    assert row.created_at == 1234
    assert row.players == ["Smokey Bacon"]


def test_null_username_is_unknown_player():
    assert summarize(_m(players=[{"username": None}, {}])).players == ["Unknown player", "Unknown player"]


def test_summarize_skips_malformed():
    assert summarize(_m(created_at="soon")) is None
    assert summarize(_m(created_at=True)) is None
    assert summarize({"map": MAP2}) is None
    assert summarize("nope") is None
    assert summarize(_m(players=None)).players == []
    assert summarize(_m(players=["junk", {"username": "A"}])).players == ["A"]
    assert len(summarize_all([_m(), "junk", _m(created_at=None)])) == 1
    assert summarize_all("junk") == []
    assert summarize_all(None) == []


def test_relative_time():
    assert relative_time(1000, 1030) == "just now"
    assert relative_time(1000, 1000 + 12 * 60) == "12 min ago"
    assert relative_time(1000, 1000 + 3 * 3600 + 5) == "3 h ago"
    assert relative_time(1000, 1000 + 2 * 86400) == "2 d ago"


def test_relative_time_future_is_just_now():
    assert relative_time(5000, 4000) == "just now"


def test_player_line_truncates():
    assert player_line(["A", "B"]) == "A, B"
    assert player_line(["A", "B", "C", "D", "E", "F"]) == "A, B, C, D +2 more"
    assert player_line([]) == ""


def test_count_label():
    assert count_label(1) == "1 player"
    assert count_label(3) == "3 players"


def test_row_keeps_match_id():
    assert summarize(_m(id="abc")).id == "abc"
    assert summarize(_m(id=None)).id == ""
