"""Replay of a real session: 4 match cycles across both maps, ending in Station."""

import os
import re

from tclauncher.presence import LAUNCHING, derive, to_activity

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "prospect_session.log")

EXPECTED = [
    ("launching", ""),
    ("signing_in", ""),
    ("in_station", ""),
    ("tutorial", ""),
    ("in_station", ""),
    ("tutorial", ""),
    ("in_station", ""),
    ("dropping_in", "Bright Sands"),
    ("in_match", "Bright Sands"),
    ("match_over", "Bright Sands"),
    ("in_station", ""),
    ("dropping_in", "Bright Sands"),
    ("in_match", "Bright Sands"),
    ("match_over", "Bright Sands"),
    ("in_station", ""),
    ("dropping_in", "Bright Sands"),
    ("in_match", "Bright Sands"),
    ("match_over", "Bright Sands"),
    ("in_station", ""),
    ("dropping_in", "Crescent Falls"),
    ("in_match", "Crescent Falls"),
    ("match_over", "Crescent Falls"),
    ("in_station", ""),
]


def _replay():
    current = LAUNCHING
    seen = [current]
    with open(FIXTURE, encoding="utf-8", errors="replace") as f:
        for line in f:
            nxt = derive(current, line)
            if nxt is not None:
                current = nxt
                seen.append(current)
    return seen


def test_golden_sequence_matches():
    assert [(p.key, p.map_name) for p in _replay()] == EXPECTED


def test_replay_emits_no_server_address_or_uuid():
    ip = re.compile(r"\d+\.\d+\.\d+\.\d+")
    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-")
    for p in _replay():
        blob = repr(to_activity(p, started_at=0))
        assert not ip.search(blob), f"server address leaked in {p.key}"
        assert not uuid_re.search(blob), f"account UUID leaked in {p.key}"


def test_replay_produces_only_known_states():
    known = {
        "launching", "signing_in", "in_station", "tutorial",
        "dropping_in", "in_match", "match_over", "in_game",
    }
    assert {p.key for p in _replay()} <= known
