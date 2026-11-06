"""Settings toggles are switches: a QCheckBox underneath (same API, same
signals), drawn as a track and a knob."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox

from tclauncher import platforms
from tclauncher.config import ConfigManager
from tclauncher.ui.settings_sections import GameSection, ProtonSection
from tclauncher.ui.widgets import Switch


def test_switch_is_a_checkbox_and_toggles(qapp):
    switch = Switch("Run inside Gamescope")
    assert isinstance(switch, QCheckBox)
    seen = []
    switch.toggled.connect(seen.append)
    switch.resize(switch.sizeHint())
    switch.show()
    QTest.mouseClick(switch, Qt.LeftButton, pos=QPoint(10, switch.height() // 2))
    assert switch.isChecked() and seen == [True]


def test_clicking_the_label_toggles_too(qapp):
    switch = Switch("Show game status on Discord")
    switch.resize(switch.sizeHint())
    switch.show()
    QTest.mouseClick(switch, Qt.LeftButton, pos=QPoint(switch.width() - 5, switch.height() // 2))
    assert switch.isChecked()


def test_switch_paints_in_every_state(qapp):
    switch = Switch("x")
    switch.resize(switch.sizeHint())
    for checked in (False, True):
        for enabled in (True, False):
            switch.setChecked(checked)
            switch.setEnabled(enabled)
            assert not switch.grab().isNull()
    assert switch.sizeHint().width() > 40


def test_settings_toggles_are_switches(qapp, tmp_path, monkeypatch):
    config = ConfigManager(config_file=str(tmp_path / "c.json"))
    assert isinstance(GameSection(config).discord_check, Switch)
    if not platforms.IS_WINDOWS:
        monkeypatch.setattr("tclauncher.ui.settings_sections.find_proton_installs", lambda: [])
        proton = ProtonSection(config)
        for toggle in (proton.gamemode_check, proton.mangohud_check, proton.gamescope_check):
            assert isinstance(toggle, Switch)
