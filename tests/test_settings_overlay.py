"""The in-window settings overlay: opening and closing, save-as-you-go, the
inline validation rule, the while-playing lock, and the Windows trap (no Linux
rows, Linux keys left alone)."""

import shlex

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from tclauncher import platforms
from tclauncher.backend import ServerDiscoveryStatus
from tclauncher.config import GAME_EXE_RELPATH, ConfigManager
from tclauncher.mods import Mod, ModStatus
from tclauncher.ui import settings_overlay, settings_sections
from tclauncher.ui.settings_overlay import SettingsOverlay


class FakeBackend:
    def __init__(self, status=ServerDiscoveryStatus.OK):
        self.status = status
        self.discovered = []

    def discover(self, url):
        self.discovered.append(url)
        return self.status


def _mod(status):
    remote = {"id": "m1", "author": "a", "name": "Mod One", "version": "2",
              "hash": "h", "integrity": "i", "url": "u"}
    return Mod("m1", None, remote, status)


class FakeMods:
    def __init__(self, mods=()):
        self.mods = list(mods)

    def get_mods_with_statuses(self):
        return self.mods

    def install_mod(self, mod, on_progress=None):
        return None


@pytest.fixture
def sync_workers(monkeypatch):
    """run_worker, but synchronous, so a test sees the result at once."""
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
def config(tmp_path):
    config = ConfigManager(config_file=str(tmp_path / "c.json"))
    game = tmp_path / "game"
    exe = game / GAME_EXE_RELPATH
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    config.game_dir = str(game)
    config.run_args = ["-log"]
    config.server_discovery_addr = "http://old"
    config.save()
    return config


@pytest.fixture
def make(qapp, config, monkeypatch, sync_workers):
    monkeypatch.setattr(settings_sections, "find_proton_installs", lambda: [], raising=False)
    made = []

    def build(backend=None, mods=None):
        parent = QWidget()
        parent.resize(1100, 620)
        overlay = SettingsOverlay(config, backend or FakeBackend(), mods or FakeMods(), parent)
        parent.show()
        made.append(parent)
        return overlay

    yield build
    for parent in made:
        parent.close()


def _saved(config):
    again = ConfigManager(config_file=config.config_file)
    again.load()
    return again


def test_opens_on_requested_section_and_esc_closes(make):
    overlay = make()
    assert not overlay.isVisible()
    closed = []
    overlay.closed.connect(lambda: closed.append(1))
    overlay.open_section("about")
    assert overlay.isVisible()
    assert overlay.current_section() == "about"
    QTest.keyClick(overlay, Qt.Key_Escape)
    assert not overlay.isVisible()
    assert closed == [1]


def test_covers_its_parent(make):
    overlay = make()
    overlay.open_section()
    assert overlay.geometry() == overlay.parentWidget().rect()
    overlay.parentWidget().resize(900, 520)
    assert overlay.geometry() == overlay.parentWidget().rect()


def test_click_outside_panel_closes(make):
    overlay = make()
    overlay.open_section()
    QTest.mouseClick(overlay, Qt.LeftButton, pos=QPoint(3, 3))
    assert not overlay.isVisible()


def test_click_inside_panel_stays_open(make):
    overlay = make()
    overlay.open_section()
    QTest.mouseClick(overlay, Qt.LeftButton, pos=overlay.panel.geometry().center())
    assert overlay.isVisible()


def test_close_button_closes(make):
    overlay = make()
    overlay.open_section()
    QTest.mouseClick(overlay.close_button, Qt.LeftButton)
    assert not overlay.isVisible()


def test_toggle_saves_immediately(make, config):
    overlay = make()
    overlay.open_section("game")
    before = config.discord_presence
    overlay.game.discord_check.click()
    assert _saved(config).discord_presence is (not before)


def test_valid_launch_flags_save_on_commit(make, config):
    overlay = make()
    overlay.game.args_edit.setText('-dx12 "-a b"')
    overlay.game.args_edit.editingFinished.emit()
    assert _saved(config).run_args == ["-dx12", "-a b"]


def test_invalid_launch_flags_not_saved_and_error_shown(make, config):
    overlay = make()
    overlay.open_section("game")
    overlay.game.args_edit.setText('-a "b')
    overlay.game.args_edit.editingFinished.emit()
    assert _saved(config).run_args == ["-log"]
    assert overlay.game.args_error.isVisible()
    assert overlay.game.args_error.text()


def test_close_reverts_invalid_launch_flags(make, config):
    overlay = make()
    overlay.open_section("game")
    overlay.game.args_edit.setText('-a "b')
    overlay.game.args_edit.editingFinished.emit()
    overlay.close()
    assert overlay.game.args_edit.text() == shlex.join(["-log"])
    assert overlay.game.args_error.isHidden()
    assert _saved(config).run_args == ["-log"]


def test_env_vars_save_as_edited(make, config):
    overlay = make()
    game = overlay.game
    game.add_env_button.click()
    row = game.env_table.rowCount() - 1
    game.env_table.item(row, 0).setText("DXVK_HUD")
    game.env_table.item(row, 1).setText("fps")
    assert _saved(config).env_vars == {"DXVK_HUD": "fps"}
    game.env_table.setCurrentCell(row, 0)
    game.remove_env_button.click()
    assert _saved(config).env_vars == {}


def test_game_folder_saves_on_commit(make, config, tmp_path):
    overlay = make()
    overlay.game.game_dir_edit.setText(str(tmp_path / "elsewhere"))
    overlay.game.game_dir_edit.editingFinished.emit()
    assert _saved(config).game_dir == str(tmp_path / "elsewhere")


def _locked_widgets(overlay):
    widgets = [overlay.game.game_dir_edit, overlay.game.game_dir_browse,
               overlay.server.url_edit, overlay.server.save_button]
    if not platforms.IS_WINDOWS:
        p = overlay.proton
        widgets += [p.proton_combo, p.refresh_button, p.browse_button, p.prefix_edit,
                    p.umu_edit, p.gamescope_check, p.gamescope_args_edit]
    return widgets


def _free_widgets(overlay):
    widgets = [overlay.game.args_edit, overlay.game.env_table, overlay.game.discord_check]
    if not platforms.IS_WINDOWS:
        widgets += [overlay.proton.gamemode_check, overlay.proton.mangohud_check]
    return widgets


def test_locked_controls_while_game_running(make):
    overlay = make()
    overlay.set_game_running(True)
    overlay.open_section("game")
    assert all(not w.isEnabled() for w in _locked_widgets(overlay))
    assert all(w.isEnabled() for w in _free_widgets(overlay))
    assert overlay.game.lock_note.isVisible()
    assert overlay.game.lock_note.text() == "Close the game to change this"


def test_lock_lifts_when_game_stops(make):
    overlay = make()
    overlay.open_section("game")
    overlay.set_game_running(True)
    overlay.set_game_running(False)
    assert all(w.isEnabled() for w in _locked_widgets(overlay))
    assert overlay.game.lock_note.isHidden()


def test_mod_buttons_locked_while_running(make):
    overlay = make(mods=FakeMods([_mod(ModStatus.NOT_INSTALLED)]))
    overlay.set_game_running(True)
    overlay.open_section("mods")
    assert overlay.mods.buttons and all(not b.isEnabled() for b in overlay.mods.buttons)
    overlay.set_game_running(False)
    assert all(b.isEnabled() for b in overlay.mods.buttons)


@pytest.mark.skipif(platforms.IS_WINDOWS, reason="the Proton section is Linux-only")
def test_gamescope_args_validated_inline(make, config):
    overlay = make()
    overlay.open_section("proton")
    overlay.proton.gamescope_args_edit.setText('-W "1920')
    overlay.proton.gamescope_args_edit.editingFinished.emit()
    assert _saved(config).gamescope_args == ""
    assert overlay.proton.gamescope_args_error.isVisible()
    overlay.close()
    assert overlay.proton.gamescope_args_edit.text() == ""
    overlay.proton.gamescope_args_edit.setText("-f")
    overlay.proton.gamescope_args_edit.editingFinished.emit()
    assert _saved(config).gamescope_args == "-f"


def test_windows_has_no_proton_section(make, config, monkeypatch):
    monkeypatch.setattr(settings_sections, "IS_WINDOWS", True)
    monkeypatch.setattr(settings_overlay, "IS_WINDOWS", True)
    config.proton_path = "/opt/proton"
    config.save()
    overlay = make()
    assert "proton" not in overlay.section_keys()
    assert overlay.proton is None
    overlay.open_section("game")
    overlay.game.discord_check.click()
    assert _saved(config).proton_path == "/opt/proton"


def test_server_section_emits_on_success(make, config):
    backend = FakeBackend(ServerDiscoveryStatus.OK)
    overlay = make(backend=backend)
    changed = []
    overlay.server_changed.connect(lambda: changed.append(1))
    overlay.open_section("server")
    overlay.server.url_edit.setText("http://new/")
    overlay.server.save_button.click()
    assert backend.discovered == ["http://new"]
    assert changed == [1]
    assert _saved(config).server_discovery_addr == "http://new"


@pytest.mark.parametrize("status", [ServerDiscoveryStatus.LAUNCHER_OUTDATED,
                                    ServerDiscoveryStatus.UNKNOWN_ERROR])
def test_server_section_inline_error(make, config, status):
    overlay = make(backend=FakeBackend(status))
    changed = []
    overlay.server_changed.connect(lambda: changed.append(1))
    overlay.open_section("server")
    overlay.server.url_edit.setText("http://new")
    overlay.server.save_button.click()
    assert changed == []
    assert overlay.server.error.isVisible()
    assert _saved(config).server_discovery_addr == "http://old"


def test_server_url_must_be_http(make):
    backend = FakeBackend()
    overlay = make(backend=backend)
    overlay.open_section("server")
    overlay.server.url_edit.setText("ftp://x")
    overlay.server.save_button.click()
    assert backend.discovered == []
    assert "http" in overlay.server.error.text()


def test_mods_section_reports_attention(make):
    overlay = make(mods=FakeMods([_mod(ModStatus.NOT_INSTALLED)]))
    seen = []
    overlay.mods_checked.connect(seen.append)
    overlay.open_section("mods")
    assert seen == [True]


def test_mods_section_all_up_to_date(make):
    overlay = make(mods=FakeMods([_mod(ModStatus.UP_TO_DATE)]))
    seen = []
    overlay.mods_checked.connect(seen.append)
    overlay.open_section("mods")
    assert seen == [False]
    assert overlay.mods.buttons == []


def test_mods_section_without_game_dir(make, config, tmp_path):
    config.game_dir = str(tmp_path / "missing")
    overlay = make(mods=FakeMods([_mod(ModStatus.NOT_INSTALLED)]))
    seen = []
    overlay.mods_checked.connect(seen.append)
    overlay.open_section("mods")
    assert seen == []
    assert overlay.mods.message.text() == "Locate your game files first."


def test_about_signals(make):
    overlay = make()
    logs, updates = [], []
    overlay.open_logs_requested.connect(lambda: logs.append(1))
    overlay.update_requested.connect(lambda: updates.append(1))
    overlay.open_section("about")
    overlay.about.logs_button.click()
    assert logs == [1]
    assert overlay.about.update_label.isHidden()
    overlay.set_update_text("Update to 1.2.1")
    assert overlay.about.update_label.isVisible()
    overlay.about.update_label.linkActivated.emit("#")
    assert updates == [1]


@pytest.mark.skipif(platforms.IS_WINDOWS, reason="the Proton section is Linux-only")
def test_shown_proton_is_adopted_when_none_saved(make, config, monkeypatch):
    """The old dialog saved the combo's visible choice on Save; showing the
    section is now that moment, or Play would say no Proton is selected while
    the section shows one."""
    from types import SimpleNamespace

    monkeypatch.setattr(settings_sections, "find_proton_installs",
                        lambda: [SimpleNamespace(name="GE-Proton10", path="/p/ge10")])
    config.proton_path = ""
    config.save()
    overlay = make()
    assert _saved(config).proton_path == ""  # building alone saves nothing
    overlay.set_game_running(True)
    overlay.open_section("proton")
    assert _saved(config).proton_path == ""  # locked: no change
    overlay.close()
    overlay.set_game_running(False)
    overlay.open_section("proton")
    assert _saved(config).proton_path == "/p/ge10"


# --- final review fixes ---

def _type_then_esc(overlay, edit, text):
    overlay.window().activateWindow()
    edit.setFocus()
    edit.selectAll()
    QTest.keyClicks(edit, text)
    QTest.keyClick(edit, Qt.Key_Escape)


def test_esc_commits_typed_launch_flags(make, config):
    overlay = make()
    overlay.open_section("game")
    _type_then_esc(overlay, overlay.game.args_edit, "-dx12")
    assert not overlay.isVisible()
    assert _saved(config).run_args == ["-dx12"]
    assert overlay.game.args_edit.text() == "-dx12"


def test_esc_commits_typed_game_folder(make, config, tmp_path):
    overlay = make()
    overlay.open_section("game")
    target = str(tmp_path / "other")
    _type_then_esc(overlay, overlay.game.game_dir_edit, target)
    assert _saved(config).game_dir == target


@pytest.mark.skipif(platforms.IS_WINDOWS, reason="the Proton section is Linux-only")
def test_esc_commits_typed_proton_paths(make, config):
    overlay = make()
    overlay.open_section("proton")
    _type_then_esc(overlay, overlay.proton.prefix_edit, "/x/prefix")
    assert _saved(config).wine_prefix == "/x/prefix"
    overlay.open_section("proton")
    _type_then_esc(overlay, overlay.proton.gamescope_args_edit, "-f")
    assert _saved(config).gamescope_args == "-f"


@pytest.fixture
def held_install(monkeypatch):
    """run_worker that holds install_mod (so a test sees 'installing') and runs
    everything else synchronously."""
    held = []

    def run(fn, *args, on_finished=None, on_failed=None, on_progress=None, **kwargs):
        if getattr(fn, "__name__", "") == "install_mod":
            held.append(on_finished)
            return
        result = fn(*args, **kwargs)
        if on_finished:
            on_finished(result)

    monkeypatch.setattr(settings_sections, "run_worker", run)
    return held


def test_install_locks_server_and_reports_busy(make, held_install):
    overlay = make(mods=FakeMods([_mod(ModStatus.NOT_INSTALLED), _mod(ModStatus.NOT_INSTALLED)]))
    busy = []
    overlay.mods_busy.connect(busy.append)
    overlay.open_section("mods")
    overlay.mods.buttons[0].click()
    assert busy == [True]
    assert not overlay.server.save_button.isEnabled()
    assert not overlay.server.url_edit.isEnabled()
    # A list refresh landing mid-install must not re-arm the buttons.
    overlay.mods._populate([_mod(ModStatus.NOT_INSTALLED)])
    assert overlay.mods.buttons and all(not b.isEnabled() for b in overlay.mods.buttons)
    held_install[0](None)
    assert busy == [True, False]
    assert overlay.server.save_button.isEnabled()


def test_install_busy_survives_game_lock_changes(make, held_install):
    overlay = make(mods=FakeMods([_mod(ModStatus.NOT_INSTALLED)]))
    overlay.open_section("mods")
    overlay.mods.buttons[0].click()
    overlay.set_game_running(False)  # e.g. a refresh of the primary button
    assert not overlay.server.save_button.isEnabled()
    held_install[0](None)
    assert overlay.server.save_button.isEnabled()


def test_about_has_discord_invite(make, monkeypatch):
    opened = []
    monkeypatch.setattr(settings_sections, "open_url", lambda url: opened.append(url) or True)
    overlay = make()
    overlay.open_section("about")
    overlay.about.discord_button.click()
    assert opened == ["https://discord.gg/8hNgwXc9m2"]


@pytest.mark.parametrize("button, url", [
    ("github_button", "https://github.com/Armanchas/tc-launcher"),
    ("discord_button", "https://discord.gg/8hNgwXc9m2"),
    ("website_button", "https://tc-stormrunners.xyz/"),
])
def test_about_links_open_in_the_browser(make, monkeypatch, button, url):
    opened = []
    monkeypatch.setattr(settings_sections, "open_url", opened.append)
    overlay = make()
    getattr(overlay.about, button).click()
    assert opened == [url]


def test_about_game_logs_button(make):
    overlay = make()
    game = []
    overlay.open_game_logs_requested.connect(lambda: game.append(1))
    overlay.about.game_logs_button.click()
    assert game == [1]


def test_about_says_what_is_running_and_who_made_it(make):
    from tclauncher.version import APP_VERSION

    overlay = make()
    assert APP_VERSION in overlay.about.info.text()
    assert ("Windows" if settings_sections.IS_WINDOWS else "Linux") in overlay.about.info.text()
    assert overlay.about.credit_after.text() == "by the TC: Storm Runners team"
    assert not overlay.about.heart.pixmap().isNull()


def test_about_shows_the_team_logo_and_only_version_and_platform(make):
    overlay = make()
    assert not overlay.about.logo.pixmap().isNull()
    assert "Protocol" not in overlay.about.info.text()
    assert "Version" in overlay.about.info.text()
    assert "Platform" in overlay.about.info.text()
