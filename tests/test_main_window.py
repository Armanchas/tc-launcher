"""The re-laid-out main window: landscape, art-backed, bottom bar, chips, the
settings overlay, play time, and the while-playing lock. No network: every
run_worker call is recorded instead of run."""

import importlib.util
import json
import time

import pytest

from tclauncher.config import GAME_EXE_RELPATH, ConfigManager
from tclauncher.oidc import Protocol
from tclauncher.ui import main_window
from tclauncher.ui.main_window import MainWindow, account_title


def test_account_title():
    assert account_title("signed_in", "Armin") == "Armin"
    assert account_title("signed_in", "") == "Signed in"
    assert account_title("expired", "Armin") == "Session expired — sign in again"
    assert account_title("signed_out", "Armin") == "Not signed in"


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(main_window, "run_worker",
                        lambda fn, *a, **k: calls.append((fn, a, k)))
    monkeypatch.setattr(main_window, "PLAYTIME_FILE", str(tmp_path / "playtime.json"))
    config = ConfigManager(config_file=str(tmp_path / "c.json"))
    game = tmp_path / "game"
    exe = game / GAME_EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    config.game_dir = str(game)
    config.save()

    made = []

    def build(prepare=None):
        if prepare:
            prepare(config, tmp_path)
        win = MainWindow(config)
        win.worker_calls = calls
        win.show()
        made.append(win)
        return win

    yield build
    for win in made:
        win.status_timer.stop()
        win.close()


def test_window_builds_landscape(window):
    win = window()
    assert win.width() > win.height()
    for gone in ("settings_dialog", "mods_dialog", "server_dialog"):
        assert importlib.util.find_spec(f"tclauncher.ui.{gone}") is None


def test_cog_opens_overlay_on_game(window):
    win = window()
    win.btn_settings.click()
    assert win.overlay.isVisible()
    assert win.overlay.current_section() == "game"


def test_mods_chip_opens_mods_section(window):
    win = window()
    win.chip_mods.click()
    assert win.overlay.isVisible()
    assert win.overlay.current_section() == "mods"


def test_mods_chip_shows_attention(window):
    win = window()
    assert win.chip_mods.icon().isNull()
    win.overlay.mods_checked.emit(True)
    assert not win.chip_mods.icon().isNull()
    win.overlay.mods_checked.emit(False)
    assert win.chip_mods.icon().isNull()
    assert win.chip_mods.text() == "Mods"


def test_logout_disabled_and_overlay_locked_while_running(window, monkeypatch):
    win = window()
    monkeypatch.setattr(win.runner, "is_running", lambda: True)
    win._update_primary_enabled()
    assert not win.btn_logout.isEnabled()
    assert not win.overlay.game.game_dir_edit.isEnabled()
    assert win.btn_play.text() == "Stop game"
    monkeypatch.setattr(win.runner, "is_running", lambda: False)
    win._update_primary_enabled()
    assert win.btn_logout.isEnabled()
    assert win.overlay.game.game_dir_edit.isEnabled()


def test_play_time_labels_from_store(window):
    def prepare(config, tmp_path):
        (tmp_path / "playtime.json").write_text(json.dumps(
            {"total_seconds": 42 * 3600 + 17 * 60, "last_played": time.time()}))

    win = window(prepare)
    assert win.play_time_value.text() == "42 h 17 min"
    assert win.last_played_value.text() == "Today"


def test_play_time_never_without_history(window):
    win = window()
    assert win.play_time_value.text() == "0 min"
    assert win.last_played_value.text() == "Never"


def test_server_change_resets_session_and_protocol(window):
    def prepare(config, tmp_path):
        config.server_discovery_addr = "http://new"
        config.access_token, config.refresh_token = "a", "r"
        config.steam_id, config.steam_name = "76561198000000000", "Armin"

    win = window(prepare)
    win.session_manager.protocol = Protocol.OIDC
    win.mod_manager.remote_mods = {"m": {}}
    win._set_mods_attention(True)
    win.overlay.server_changed.emit()
    assert win.config.refresh_token == ""
    assert win.config.steam_id == ""
    assert win.session_manager.protocol is Protocol.UNKNOWN
    assert win.mod_manager.remote_mods == {}
    assert win.chip_mods.text() == "Mods"


def test_signed_in_shows_name_and_fetches_avatar(window):
    def prepare(config, tmp_path):
        config.server_discovery_addr = "http://server"
        config.access_token, config.refresh_token = "a", "r"
        config.steam_id, config.steam_name = "76561198000000000", "Armin"

    win = window(prepare)
    assert win.account_title.text() == "Armin"
    fetched = [c for c in win.worker_calls if c[0] is main_window.resolve_avatar]
    assert len(fetched) == 1 and fetched[0][1] == ("76561198000000000",)
    win._refresh_account_ui()
    assert len([c for c in win.worker_calls if c[0] is main_window.resolve_avatar]) == 1


def test_legacy_session_shows_placeholder_without_fetch(window):
    def prepare(config, tmp_path):
        config.server_discovery_addr = "http://server"
        config.session_id, config.refresh_token = "s", "r"
        config.exp = int(time.time()) + 600

    win = window(prepare)
    assert win.account_title.text() == "Signed in"
    assert not [c for c in win.worker_calls if c[0] is main_window.resolve_avatar]
    assert not win.avatar.pixmap().isNull()


def test_server_line_shows_host(window):
    def prepare(config, tmp_path):
        config.server_discovery_addr = "https://play.example.xyz"

    assert window().account_server.text() == "No server selected"
    assert window(prepare).account_server.text() == "play.example.xyz"


def test_transient_status_goes_to_status_line_population_to_chip(window):
    win = window()
    win._set_status("Checking mods and session…", "waiting")
    assert win.status_line.text() == "Checking mods and session…"
    assert not win.status_line.isHidden()
    win._set_status("23 players online", "online", population=True)
    assert "23 players online" in win.chip_online.text()
    assert win.status_line.isHidden()


def test_play_disabled_while_a_mod_installs(window):
    win = window()
    win._server_online = True
    win._primary_action = "play"
    win._update_primary_enabled()
    assert win.btn_play.isEnabled()
    win.overlay.mods_busy.emit(True)
    assert not win.btn_play.isEnabled()
    win.overlay.mods_busy.emit(False)
    assert win.btn_play.isEnabled()


def test_wordmark_says_storm_runners(window):
    from PySide6.QtWidgets import QLabel
    win = window()
    texts = [l.text() for l in win.findChildren(QLabel) if l.objectName() == "subtitle"]
    assert texts == ["Storm Runners"]


def test_play_button_is_a_pill(window):
    """Qt drops a border-radius larger than half the height (it does not clamp
    like CSS), so the pill needs a fixed height and a radius of exactly half."""
    import re

    from tclauncher.ui.theme import STYLESHEET

    win = window()
    radius = int(re.search(r"QPushButton#primary \{[^}]*border-radius: (\d+)px", STYLESHEET).group(1))
    assert win.btn_play.minimumHeight() == win.btn_play.maximumHeight()
    assert radius * 2 == win.btn_play.height()


def test_cog_and_close_are_icons_not_text(window):
    win = window()
    for button in (win.btn_settings, win.overlay.close_button):
        assert button.text() == ""
        assert not button.icon().isNull()


def test_status_chips_carry_their_symbol_as_an_icon(window):
    win = window()
    win._set_status("23 players online", "online", population=True)
    assert win.chip_online.text() == "23 players online"
    assert not win.chip_online.icon().isNull()


def test_placeholder_avatar_draws_the_person_icon(qapp):
    from PySide6.QtGui import QColor

    from tclauncher.ui.theme import TEXT

    image = main_window.placeholder_avatar("", 40).toImage()
    text = QColor(TEXT)
    drawn = sum(image.pixelColor(x, y).lightness() >= text.lightness() - 20
                for x in range(40) for y in range(40))
    assert drawn > 10


def _in_game(win, monkeypatch, clock):
    win.config.backend_data = {"backend_api": "http://server"}
    monkeypatch.setattr(main_window.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(win.runner, "is_running", lambda: True)
    win._set_status("Game running…", "online")
    win.worker_calls.clear()


def _status_requests(win):
    return [c for c in win.worker_calls if c[0] == win.backend.server_status]


def test_player_count_keeps_updating_while_the_game_runs(window, monkeypatch):
    win = window()
    _in_game(win, monkeypatch, [1_000_000.0])
    win._poll_status()
    assert len(_status_requests(win)) == 1
    _status_requests(win)[0][2]["on_finished"]({"online": 7})
    assert win.chip_online.text() == "7 players online"
    assert win.status_line.text() == "Game running…"
    assert not win.status_line.isHidden()


def test_in_game_the_count_refreshes_once_a_minute(window, monkeypatch):
    win = window()
    clock = [1_000_000.0]
    _in_game(win, monkeypatch, clock)
    win._poll_status()
    clock[0] += 59
    win._poll_status()
    assert len(_status_requests(win)) == 1
    clock[0] += 2
    win._poll_status()
    assert len(_status_requests(win)) == 2


def test_launcher_logs_open_the_launcher_folder(window, monkeypatch):
    win = window()
    opened = []
    monkeypatch.setattr(win, "_open_host_path", opened.append)
    win.overlay.about.logs_button.click()
    assert opened == [main_window.LAUNCHER_USERDIR]


def test_game_logs_open_the_folder_holding_prospect_log(window, monkeypatch, tmp_path):
    win = window()
    log = tmp_path / "Logs" / "Prospect.log"
    log.parent.mkdir()
    monkeypatch.setattr(main_window, "game_log_path", lambda config: str(log))
    opened = []
    monkeypatch.setattr(win, "_open_host_path", opened.append)
    win.overlay.about.game_logs_button.click()
    assert opened == [str(log.parent)]


def test_game_logs_before_the_first_run_explains_itself(window, monkeypatch, tmp_path):
    win = window()
    monkeypatch.setattr(main_window, "game_log_path",
                        lambda config: str(tmp_path / "nope" / "Prospect.log"))
    opened, told = [], []
    monkeypatch.setattr(win, "_open_host_path", opened.append)
    monkeypatch.setattr(main_window.QMessageBox, "information",
                        lambda *a, **k: told.append(a))
    win.overlay.about.game_logs_button.click()
    assert opened == []
    assert len(told) == 1
