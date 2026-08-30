import os

from tclauncher.presence import PresenceSession, game_log_path

MAP_LINE = "[2026.08.25-20.11.14:600][698]LogYGameInstance: PreLoadingNewMap | new map '{}'.\n"


class FakeIPC:
    def __init__(self, connect_ok=True):
        self.connect_ok = connect_ok
        self.connected = False
        self.activities = []

    def connect(self, client_id):
        self.connected = self.connect_ok
        return self.connect_ok

    def set_activity(self, activity):
        if not self.connected:
            return False
        self.activities.append(activity)
        return True

    def close(self):
        self.connected = False


def _session(tmp_path, ipc, name="Prospect.log"):
    return PresenceSession(
        client_id="1",
        log_path=str(tmp_path / name),
        ipc=ipc,
        poll_interval=0.01,
        min_update_interval=0.0,
    )


def test_game_log_path_is_derived_from_the_prefix():
    path = game_log_path("/home/u/.tclauncher/prefix")
    assert path.startswith("/home/u/.tclauncher/prefix")
    assert path.endswith(os.path.join("Prospect", "Saved", "Logs", "Prospect.log"))


def test_missing_log_file_is_not_an_error(tmp_path):
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()  # must not raise
    assert s.current.key == "launching"


def test_existing_log_starts_at_end_of_file(tmp_path):
    """A stale log from a previous session must never replay as live state."""
    log = tmp_path / "Prospect.log"
    log.write_text(MAP_LINE.format("MP_Map01_P"))
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    assert s.current.key == "launching"

    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()
    assert s.current.key == "in_station"


def test_new_lines_push_activity(tmp_path):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()
    assert ipc.activities
    assert ipc.activities[-1]["details"] == "In Station"


def test_partial_trailing_line_is_held_until_complete(tmp_path):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    partial = MAP_LINE.format("Station_P")
    with open(log, "a") as f:
        f.write(partial[:20])
    s._pump()
    assert s.current.key == "launching"
    with open(log, "a") as f:
        f.write(partial[20:])
    s._pump()
    assert s.current.key == "in_station"


def test_recreated_log_is_reread_from_the_start(tmp_path):
    """The game recreates the log each launch, giving it a new inode."""
    log = tmp_path / "Prospect.log"
    log.write_text(MAP_LINE.format("Station_P"))
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    os.remove(log)
    log.write_text(MAP_LINE.format("MP_Map01_P"))
    s._pump()
    assert s.current.key == "dropping_in"


def test_in_place_truncation_is_detected(tmp_path):
    """Truncated in place (same inode, smaller file) must also reopen."""
    log = tmp_path / "Prospect.log"
    log.write_text(MAP_LINE.format("Station_P") * 4)
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    log.write_text("")  # truncate; size now < our read offset
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("MP_Map01_P"))
    s._pump()
    assert s.current.key == "dropping_in"


def test_absent_discord_never_raises(tmp_path):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC(connect_ok=False)
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()  # must not raise
    assert ipc.activities == []


def test_stop_clears_the_activity(tmp_path):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()
    s.stop()
    assert ipc.activities[-1] is None
    assert ipc.connected is False


def test_updates_are_coalesced_by_the_rate_limit(tmp_path):
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = PresenceSession(
        client_id="1", log_path=str(log), ipc=ipc,
        poll_interval=0.01, min_update_interval=999.0,
    )
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
        f.write(MAP_LINE.format("MP_Map01_P"))
    s._pump()
    # Only the newest state is sent, not one per transition.
    assert len(ipc.activities) == 1
    assert "Bright Sands" in ipc.activities[0]["details"]


MM_LINE = ("[2026.08.26-17.31.21:585][165]LogYMatchmakingController: "
           "EnterMatchmaking | Map: '', GameMode: '{}', IsRanked: 0\n")


def _station_then(tmp_path, line):
    """Sit in the Station, then feed one more line. Returns (session, ipc)."""
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()
    assert len(ipc.activities) == 1
    with open(log, "a") as f:
        f.write(line)
    s._pump()
    return s, ipc


def test_context_only_change_does_not_resend_or_restart_the_timer(tmp_path):
    """Matchmaking updates context but not visible text: no duplicate update."""
    s, ipc = _station_then(tmp_path, MM_LINE.format("LOOP"))
    started = s._started_at
    assert s.current.mode == "LOOP"
    assert len(ipc.activities) == 1
    assert s._started_at == started


def test_joining_a_squad_updates_the_line_without_restarting_the_timer(tmp_path):
    """A squad marker is visible text, so it sends -- but the state is the same."""
    s, ipc = _station_then(tmp_path, MM_LINE.format("SQUADLOOP"))
    assert s.current.in_squad is True
    assert len(ipc.activities) == 2
    assert ipc.activities[-1]["state"].endswith("· In a squad")
    assert ipc.activities[-1]["details"] == ipc.activities[0]["details"]
    assert (ipc.activities[-1]["timestamps"]["start"]
            == ipc.activities[0]["timestamps"]["start"])
