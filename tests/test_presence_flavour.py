"""Flavour text: a pool per state, re-rolled each time that state is entered."""

from tclauncher.presence import (
    FLAVOUR, LAUNCHING, PresenceSession, derive, pick_flavour, to_activity,
    with_flavour,
)

MAP = "[2026.08.26-17.31.14:600][698]LogYGameInstance: PreLoadingNewMap | new map '{}'.\n"
STATE = "[2026.08.26-17.31.14:001][699]LogYGameState: OnRep_MatchState | State: [{}]\n"
MM = ("[2026.08.26-17.31.21:585][165]LogYMatchmakingController: "
      "EnterMatchmaking | Map: '', GameMode: '{}', IsRanked: {}\n")
SQUAD = ("[2026.08.26-23.46.20:782][689]LogYSquadController: PrintSquad "
         "(context: FYSquadController::ProcessSquadUpdated) | Members:\n")
MEMBER = "Id: 5b3d8401-b3c7-43c8-9b9f-6b5e1aa1722d, State: IN_STATION\n"


# --- the pools themselves -------------------------------------------------

def test_every_pool_has_several_distinct_lines():
    assert set(FLAVOUR) >= {"in_station", "in_match", "deathmatch"}
    for pool, lines in FLAVOUR.items():
        assert len(lines) >= 2, f"{pool} needs alternatives to vary between"
        assert len(set(lines)) == len(lines), f"{pool} repeats a line"


def test_the_games_own_string_leads_each_pool():
    """Default (unrandomised) behaviour still mirrors the game's own strings."""
    assert FLAVOUR["in_station"][0] == "Doing Station things"
    assert FLAVOUR["in_match"][0] == "On my way to steal your minerals"


def test_lines_fit_discords_state_field():
    # Discord caps state at 128 bytes; the squad marker shares the line.
    for lines in FLAVOUR.values():
        for line in lines:
            assert line.strip() == line and line
            assert len(line.encode()) <= 100, line


# --- picking --------------------------------------------------------------

def test_pick_returns_a_line_from_the_pool():
    for _ in range(20):
        assert pick_flavour("in_match") in FLAVOUR["in_match"]


def test_pick_avoids_repeating_the_previous_line():
    previous = FLAVOUR["in_match"][0]
    for _ in range(20):
        assert pick_flavour("in_match", avoid=previous) != previous


def test_pick_on_an_unknown_or_absent_pool_is_empty():
    assert pick_flavour("") == ""
    assert pick_flavour("not_a_pool") == ""


def test_single_line_pool_still_returns_that_line():
    assert pick_flavour("in_match", avoid="x", rng=None) in FLAVOUR["in_match"]


# --- composing the state line --------------------------------------------

def test_with_flavour_replaces_the_state_line():
    p = derive(LAUNCHING, MAP.format("Station_P"))
    assert with_flavour(p, "Getting unfoamed").state == "Getting unfoamed"


def test_with_flavour_keeps_the_squad_marker():
    p = derive(LAUNCHING, MM.format("SQUADLOOP", 0))
    p = derive(p, MAP.format("Station_P"))
    assert with_flavour(p, "Getting unfoamed").state == "Getting unfoamed · In a squad"


def test_squad_marker_alone_has_no_leading_separator():
    p = derive(LAUNCHING, MM.format("SQUADLOOP", 0))
    p = derive(p, MAP.format("Login_P"))
    assert with_flavour(p, "").state == "In a squad"


def test_a_counted_squad_supersedes_the_marker():
    p = derive(LAUNCHING, MM.format("SQUADLOOP", 0))
    p = derive(p, SQUAD)
    for _ in range(2):
        p = derive(p, MEMBER)
    p = derive(p, MAP.format("Station_P"))
    assert with_flavour(p, "Getting unfoamed").state == "Getting unfoamed"
    assert to_activity(p, 0)["party"]["size"] == [2, 4]


def test_derive_alone_stays_deterministic():
    """derive() must not roll dice -- the golden replay depends on it."""
    first = [derive(LAUNCHING, MAP.format("Station_P")) for _ in range(10)]
    assert len(set(first)) == 1


def test_states_name_the_pool_they_draw_from():
    assert derive(LAUNCHING, MAP.format("Station_P")).flavour == "in_station"
    dropping = derive(LAUNCHING, MAP.format("MP_Map01_P"))
    assert derive(dropping, STATE.format("MatchInProgress")).flavour == "in_match"
    dm = derive(LAUNCHING, MAP.format("TestMap_DeathMatch_P"))
    assert derive(dm, STATE.format("MatchInProgress")).flavour == "deathmatch"
    assert derive(LAUNCHING, MAP.format("Login_P")).flavour == ""


# --- the session re-rolls on entering a state ----------------------------

class FakeIPC:
    def __init__(self):
        self.connected = False
        self.activities = []

    def connect(self, client_id):
        self.connected = True
        return True

    def set_activity(self, activity):
        self.activities.append(activity)
        return True

    def close(self):
        self.connected = False


def _session(tmp_path, ipc, pick):
    return PresenceSession(client_id="1", log_path=str(tmp_path / "Prospect.log"),
                           ipc=ipc, poll_interval=0.01, min_update_interval=0.0,
                           pick=pick)


def _cycler():
    """A deterministic stand-in for pick_flavour that walks each pool in turn."""
    counts: dict[str, int] = {}
    def pick(pool, avoid=""):
        if pool not in FLAVOUR:
            return ""
        i = counts.get(pool, -1) + 1
        counts[pool] = i
        return FLAVOUR[pool][i % len(FLAVOUR[pool])]
    return pick


def _run(tmp_path, lines, pick=None):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc, pick or _cycler())
    s._pump()  # opens at EOF
    for line in lines:
        with open(log, "a") as f:
            f.write(line)
        s._pump()  # one pump per line: _flush() only sends the newest state
    return s, ipc


def test_re_entering_a_state_draws_a_new_line(tmp_path):
    s, ipc = _run(tmp_path, [MAP.format("Station_P"), MAP.format("MP_Map01_P"),
                             STATE.format("MatchInProgress"), MAP.format("Station_P")])
    stations = [a["state"] for a in ipc.activities if a["details"] == "In Station"]
    assert len(stations) == 2
    assert stations[0] != stations[1]


def test_a_state_does_not_repeat_its_last_line_when_re_entered(tmp_path):
    """The pool-less states in between must not wipe the anti-repeat memory."""
    lines = []
    for _ in range(3):
        lines += [MAP.format("Station_P"), MAP.format("MP_Map01_P"),
                  STATE.format("MatchInProgress"), STATE.format("MatchEnding")]
    s, ipc = _run(tmp_path, lines, pick=pick_flavour)
    matches = [a["state"] for a in ipc.activities
               if a["details"].startswith("In Match")]
    assert len(matches) == 3
    assert all(a != b for a, b in zip(matches, matches[1:]))


def test_the_line_holds_steady_while_the_state_does(tmp_path):
    """A squad or matchmaking update must not reshuffle the text mid-state."""
    s, ipc = _run(tmp_path, [MAP.format("Station_P"), MM.format("SQUADLOOP", 0),
                             SQUAD, MEMBER])
    stations = [a["state"] for a in ipc.activities if a["details"] == "In Station"]
    assert len({t.split(" · ")[0] for t in stations}) == 1


def test_a_squad_arriving_mid_state_still_updates_the_marker(tmp_path):
    s, ipc = _run(tmp_path, [MAP.format("Station_P"), MM.format("SQUADLOOP", 0)])
    assert ipc.activities[-1]["state"].endswith("· In a squad")


def test_match_and_deathmatch_draw_from_different_pools(tmp_path):
    s, ipc = _run(tmp_path, [MAP.format("MP_Map01_P"), STATE.format("MatchInProgress"),
                             MAP.format("Station_P"),
                             MAP.format("TestMap_DeathMatch_P"),
                             STATE.format("MatchInProgress")])
    match = next(a["state"] for a in ipc.activities if a["details"].startswith("In Match"))
    dm = next(a["state"] for a in ipc.activities if a["details"] == "Playing Deathmatch")
    assert match in FLAVOUR["in_match"]
    assert dm in FLAVOUR["deathmatch"]


def test_real_picker_drives_a_session_without_error(tmp_path):
    s, ipc = _run(tmp_path, [MAP.format("Station_P"), MAP.format("MP_Map01_P"),
                             STATE.format("MatchInProgress")],
                  pick=pick_flavour)
    assert ipc.activities
    for a in ipc.activities:
        assert a["details"]
