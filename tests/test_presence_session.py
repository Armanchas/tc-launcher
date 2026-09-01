import os

import pytest

from tclauncher import platforms
from tclauncher.config import ConfigManager
from tclauncher.presence import GAME_LOG_RELPATH, PresenceSession, game_log_path

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


@pytest.mark.skipif(platforms.IS_WINDOWS,
                    reason="Linux resolves the log inside a wine prefix; Windows has none")
def test_game_log_path_is_derived_from_the_prefix(tmp_path):
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    config.wine_prefix = str(tmp_path / "prefix")
    path = game_log_path(config)
    assert path.startswith(str(tmp_path / "prefix"))
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


def test_log_path_ends_with_the_shared_relative_tail(tmp_path):
    """The tail after AppData/Local is identical on both platforms."""
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    path = platforms.game_log_path(config)
    assert path.endswith(os.path.join("Prospect", "Saved", "Logs", "Prospect.log"))


@pytest.mark.skipif(platforms.IS_WINDOWS,
                    reason="Linux resolves the log inside a wine prefix; Windows has none")
def test_linux_log_path_is_inside_the_configured_prefix(tmp_path):
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    config.wine_prefix = str(tmp_path / "prefix")
    path = platforms.game_log_path(config)
    assert path.startswith(str(tmp_path / "prefix"))
    assert GAME_LOG_RELPATH in path


def test_a_zero_inode_does_not_make_two_logs_look_identical(tmp_path, monkeypatch):
    """Windows synthesises st_ino from the NTFS file index and it can be 0.
    Trusting a 0 would replay a stale log as live state."""
    log = tmp_path / "Prospect.log"
    log.write_text("")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()  # opens the file at EOF -- this handle must survive the next pump
    with open(log, "a") as f:
        f.write(MAP_LINE.format("Station_P"))
    s._pump()
    assert s.current.key == "in_station"
    reopens_before = s._reopens

    real_stat = os.stat

    def zero_inode_stat(path, *a, **kw):
        st = real_stat(path, *a, **kw)
        return os.stat_result((st.st_mode, 0) + tuple(st)[2:])

    monkeypatch.setattr(os, "stat", zero_inode_stat)
    # A zero inode must read as "unknown", not as "different file". Asserting on
    # the re-read counter, not on state: a spurious restart re-reads from byte 0
    # and lands on the same state anyway, so state alone cannot catch this bug.
    with open(log, "a") as f:
        f.write(MAP_LINE.format("MP_Map01_P"))
    s._pump()
    assert s._reopens == reopens_before, "a zero inode triggered a spurious re-read"
    assert s.current.key == "dropping_in"


def test_the_tailer_holds_no_handle_between_polls(tmp_path):
    """UE rotates Prospect.log to Prospect-backup-<time>.log on EVERY launch.

    Windows refuses to rename a file that another process holds open, so a
    tailer that kept the handle would block the game's own log rotation --
    presence must never interfere with the game. Caught by Windows CI in
    test_recreated_log_is_reread_from_the_start; pinned here as the actual
    requirement rather than as a side effect of one rotation test.
    """
    log = tmp_path / "Prospect.log"
    log.write_text(MAP_LINE.format("Station_P"))
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "a") as f:
        f.write(MAP_LINE.format("MP_Map01_P"))
    s._pump()
    assert s.current.key == "dropping_in"

    # The invariant, checkable on every platform: no persistent handle exists.
    assert getattr(s, "_file", None) is None, "the tailer kept the log open"
    # The behaviour it buys. Always succeeds on POSIX; on Windows this is the
    # assertion that actually bites.
    os.rename(log, tmp_path / "Prospect-backup-2026.09.01-00.00.00.log")
    assert not log.exists()


def test_reads_strip_the_carriage_returns_a_windows_log_carries(tmp_path):
    """A Windows game writes CRLF. Reading binary -- required so offsets can be
    compared against st_size -- keeps the \r that text mode used to strip.

    Asserted on the text handed to the parser, not on derived state: every line
    type we currently parse happens to survive a trailing \r (checked), so a
    state-level assertion here could not fail. The \r still must not reach the
    buffer -- the next parser added would be the one to break, on Windows only.
    """
    log = tmp_path / "Prospect.log"
    log.write_bytes(b"")
    ipc = FakeIPC()
    s = _session(tmp_path, ipc)
    s._pump()
    with open(log, "ab") as f:
        f.write(MAP_LINE.format("Station_P").replace("\n", "\r\n").encode())
    text = s._read_new()
    assert "\r" not in text, "a CRLF log leaked carriage returns into the parser"
    assert text.endswith("'Station_P'.\n")
