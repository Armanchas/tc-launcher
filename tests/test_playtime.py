"""Local play time: the store, the session checkpoints, and the labels."""

import json
from datetime import datetime

from tclauncher.playtime import PlaySession, PlayTimeStore, format_last_played, format_play_time


def test_format_play_time():
    assert format_play_time(0) == "0 min"
    assert format_play_time(59) == "0 min"
    assert format_play_time(17 * 60) == "17 min"
    assert format_play_time(42 * 3600 + 17 * 60 + 5) == "42 h 17 min"
    assert format_play_time(3600) == "1 h 0 min"


def test_format_last_played():
    now = datetime(2026, 11, 5, 21, 0)

    def ts(*a):
        return datetime(*a).timestamp()

    assert format_last_played(None, now) == "Never"
    assert format_last_played(ts(2026, 11, 5, 0, 1), now) == "Today"
    assert format_last_played(ts(2026, 11, 4, 23, 59), now) == "Yesterday"
    assert format_last_played(ts(2026, 11, 2, 12, 0), now) == "3 days ago"
    assert format_last_played(ts(2026, 10, 30, 12, 0), now) == "6 days ago"
    assert format_last_played(ts(2026, 10, 5, 12, 0), now) == "5 Oct 2026"


def test_add_and_reload_roundtrip(tmp_path):
    path = str(tmp_path / "playtime.json")
    store = PlayTimeStore(path)
    store.load()
    store.add(90, ended_at=1_700_000_000.0)
    again = PlayTimeStore(path)
    again.load()
    assert again.total_seconds == 90
    assert again.last_played == 1_700_000_000.0


def test_missing_file_reads_as_empty(tmp_path):
    store = PlayTimeStore(str(tmp_path / "nope.json"))
    store.load()
    assert store.total_seconds == 0
    assert store.last_played is None


def test_corrupt_file_reads_as_empty(tmp_path):
    path = tmp_path / "playtime.json"
    for content in ("{not json", json.dumps({"total_seconds": "x", "last_played": "y"}), "[]"):
        path.write_text(content)
        store = PlayTimeStore(str(path))
        store.load()
        assert store.total_seconds == 0
        assert store.last_played is None
    store.add(5, ended_at=10.0)
    assert json.loads(path.read_text()) == {"total_seconds": 5, "last_played": 10.0}


def test_checkpoint_persists_partial_session(tmp_path):
    path = str(tmp_path / "playtime.json")
    clock = [1000.0]
    store = PlayTimeStore(path)
    store.load()
    session = PlaySession(store, now=lambda: clock[0])
    session.start()
    clock[0] = 1060.0
    session.checkpoint()
    fresh = PlayTimeStore(path)
    fresh.load()
    assert fresh.total_seconds == 60
    clock[0] = 1090.0
    session.finish()
    fresh.load()
    assert fresh.total_seconds == 90
    assert fresh.last_played == 1090.0
    clock[0] = 2000.0
    session.finish()
    fresh.load()
    assert fresh.total_seconds == 90


def test_negative_or_zero_elapsed_is_ignored(tmp_path):
    clock = [1000.0]
    store = PlayTimeStore(str(tmp_path / "playtime.json"))
    store.load()
    session = PlaySession(store, now=lambda: clock[0])
    session.start()
    clock[0] = 900.0
    session.checkpoint()
    assert store.total_seconds == 0
