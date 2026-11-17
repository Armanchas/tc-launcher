"""The Online section: who is online and the latest matches, read-only."""

import pytest

from tclauncher.config import ConfigManager
from tclauncher.ui import settings_sections
from tclauncher.ui.settings_sections import OnlineSection

NOW = 1_800_000_000
MAP2 = "/Game/Maps/MP/MAP02/MP_Map02_P.MP_Map02_P"


class FakeBackend:
    def __init__(self, players=(), matches=()):
        self.players = None if players is None else list(players)
        self.matches = None if matches is None else list(matches)
        self.calls = 0

    def players_online(self):
        self.calls += 1
        return self.players

    def recent_matches(self, count=10):
        assert count == OnlineSection.MATCH_COUNT
        return self.matches


@pytest.fixture
def sync_workers(monkeypatch):
    def run(fn, *args, on_finished=None, on_failed=None, on_progress=None, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            if on_failed:
                on_failed(str(e))
            return
        if on_finished:
            on_finished(result)

    monkeypatch.setattr(settings_sections, "run_worker", run)


@pytest.fixture
def make(qapp, tmp_path, sync_workers):
    made = []

    def build(backend, server=True):
        config = ConfigManager(config_file=str(tmp_path / "c.json"))
        config.backend_data = {"backend_api": "http://s"} if server else None
        section = OnlineSection(config, backend)
        section._clock = lambda: NOW
        made.append(section)
        return section

    yield build
    for section in made:
        section.hide()
        section.deleteLater()


def _match(**kw):
    base = {"id": "m", "map": MAP2, "game_mode": "LOOP", "status": 0,
            "created_at": NOW - 720, "players": [{"username": "Smokey Bacon"}]}
    base.update(kw)
    return base


def test_shows_sorted_players_and_matches(make):
    section = make(FakeBackend(["bob", "Alice"], [_match()]))
    section.show()
    assert section.shown_players() == ["Alice", "bob"]
    assert "· 2" in section.players_heading.text()
    text = " | ".join(section.shown_matches())
    assert "Solo · Crescent Falls" in text
    assert "12 min ago" in text
    assert "1 player" in text and "Smokey Bacon" in text


def test_empty_lists_say_so(make):
    section = make(FakeBackend([], []))
    section.refresh()
    assert section.shown_players() == ["Nobody's online right now."]
    assert section.shown_matches() == ["No matches yet."]


def test_no_server_makes_no_request(make):
    backend = FakeBackend(["A"], [])
    section = make(backend, server=False)
    section.refresh()
    assert backend.calls == 0
    assert section.message.text() == "Select a server to see who's online."


def test_failed_first_load_says_couldnt_load(make):
    section = make(FakeBackend(None, None))
    section.refresh()
    assert any("Couldn't load" in t for t in section.shown_players())
    assert any("Couldn't load" in t for t in section.shown_matches())


def test_failed_refresh_keeps_last_lists(make):
    backend = FakeBackend(["Alice"], [_match()])
    section = make(backend)
    section.refresh()
    backend.players, backend.matches = None, None
    section.refresh()
    assert section.shown_players() == ["Alice"]
    assert any("Solo" in t for t in section.shown_matches())
    assert "Couldn't refresh" in section.message.text()


def test_success_clears_the_failure_note(make):
    backend = FakeBackend(["Alice"], [])
    section = make(backend)
    section.refresh()
    backend.players = None
    section.refresh()
    backend.players = ["Alice"]
    section.refresh()
    assert section.message.text() == ""


def test_timer_runs_only_while_visible(make):
    section = make(FakeBackend())
    assert section.timer.interval() == 60_000
    section.show()
    assert section.timer.isActive()
    section.hide()
    assert not section.timer.isActive()


def test_times_rerender_on_tick(make):
    section = make(FakeBackend([], [_match()]))
    section.refresh()
    section._clock = lambda: NOW + 3600
    section._tick()
    assert any("1 h ago" in t for t in section.shown_matches())


def test_no_overlapping_fetch(make, monkeypatch):
    recorded = []
    monkeypatch.setattr(settings_sections, "run_worker",
                        lambda fn, *a, **kw: recorded.append(fn))
    section = make(FakeBackend())
    section.refresh()
    section.refresh()
    assert len(recorded) == 1


def test_refresh_button_fetches(make):
    backend = FakeBackend()
    section = make(backend)
    section.refresh_button.click()
    assert backend.calls == 1


def test_never_locked(make):
    # isHidden, not isVisible: the section is never shown here, so isVisible
    # is False whatever the lock note does.
    section = make(FakeBackend())
    section.set_locked(True)
    assert section.refresh_button.isEnabled()
    assert section.lock_note.isHidden()


def test_server_text_is_never_rich_text(make):
    # Steam persona names are player-chosen: markup must show as typed.
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    section = make(FakeBackend(["<b>Bob</b>"], [_match(players=[{"username": "<i>Eve</i>"}])]))
    section.refresh()
    labels = [w for w in section.findChildren(QLabel) if "<" in w.text()]
    assert len(labels) == 2
    assert all(label.textFormat() == Qt.PlainText for label in labels)


def test_first_load_shows_loading(make, monkeypatch):
    monkeypatch.setattr(settings_sections, "run_worker", lambda fn, *a, **kw: None)
    section = make(FakeBackend())
    section.refresh()
    assert section.shown_players() == ["Loading…"]
    assert section.shown_matches() == ["Loading…"]


def _cards(section):
    from tclauncher.ui.settings_sections import MatchCard
    return section.findChildren(MatchCard)


def _names(n):
    return [{"username": f"P{i}"} for i in range(n)]


def test_small_match_shows_all_names_without_expander(make):
    section = make(FakeBackend([], [_match(players=_names(4))]))
    section.refresh()
    [card] = _cards(section)
    assert card.expand_button is None
    assert card.names.text() == "P0, P1, P2, P3"


def test_big_match_expands_and_collapses(make):
    section = make(FakeBackend([], [_match(id="big", players=_names(6))]))
    section.refresh()
    [card] = _cards(section)
    assert card.names.text() == "P0, P1, P2, P3 +2 more"
    card.expand_button.click()
    [card] = _cards(section)
    assert card.names.text() == "P0, P1, P2, P3, P4, P5"
    card.expand_button.click()
    [card] = _cards(section)
    assert card.names.text().endswith("+2 more")


def test_expanded_card_survives_the_minute_rerender(make):
    section = make(FakeBackend([], [_match(id="big", players=_names(6))]))
    section.refresh()
    _cards(section)[0].expand_button.click()
    section._tick()
    [card] = _cards(section)  # the rebuilt card, not the one clicked
    assert card.names.text() == "P0, P1, P2, P3, P4, P5"


def test_chevron_does_not_make_the_card_taller(make, qapp):
    from tclauncher.ui.theme import apply_theme
    apply_theme(qapp)
    section = make(FakeBackend([], [_match(id="s", players=_names(4)),
                                    _match(id="b", players=_names(6))]))
    section.refresh()
    small, big = _cards(section)
    assert big.expand_button is not None
    assert big.sizeHint().height() == small.sizeHint().height()


def test_reset_drops_the_old_servers_lists(make):
    backend = FakeBackend(["Alice"], [_match()])
    section = make(backend)
    section.refresh()
    section.reset()
    backend.players, backend.matches = None, None
    section.refresh()
    assert "Alice" not in section.shown_players()
    assert not any("Solo" in t for t in section.shown_matches())
    assert "Couldn't refresh" not in section.message.text()


def test_an_answer_from_before_a_reset_is_ignored(make, monkeypatch):
    pending = []
    monkeypatch.setattr(settings_sections, "run_worker",
                        lambda fn, *a, on_finished=None, **kw: pending.append((fn, on_finished)))
    section = make(FakeBackend(["Old"], []))
    section.refresh()
    section.reset()
    fn, done = pending.pop()
    done(fn())  # the old server answers late
    assert "Old" not in section.shown_players()
    section.refresh()
    assert len(pending) == 1  # a reset never leaves the section stuck loading


def test_no_server_stays_a_clean_message(make):
    backend = FakeBackend(["Alice"], [_match()])
    section = make(backend)
    section.refresh()
    section.config.backend_data = None
    section.refresh()
    section._tick()
    assert section.message.text() == "Select a server to see who's online."
    assert section.shown_players() == []
    assert section.shown_matches() == []


def test_partial_first_load_does_not_claim_a_last_list(make):
    section = make(FakeBackend(["Alice"], None))
    section.refresh()
    assert section.shown_players() == ["Alice"]
    assert any("Couldn't load" in t for t in section.shown_matches())
    assert section.message.text() == ""


def test_recent_open_match_reads_ongoing(make):
    section = make(FakeBackend([], [_match(created_at=NOW - 300),
                                    _match(id="done", created_at=NOW - 300, status=1),
                                    _match(id="old", created_at=NOW - 1500)]))
    section.refresh()
    cards = section.shown_matches()
    assert len(cards) == 3
    assert "Ongoing" in cards[0]
    assert "Ongoing" not in cards[1]
    assert "Ongoing" not in cards[2]
