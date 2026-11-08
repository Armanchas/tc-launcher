"""The settings overlay's sections. Each one saves as the user goes.

The logic is moved from the dialogs these replace (SettingsDialog, ModsDialog,
ServerDialog). Rules every section follows:

- A change saves when it is committed (toggle flipped, editing finished, row
  added/removed). A value that fails validation shows an inline error and is
  not saved; `revert_invalid()` (called when the overlay closes) puts the
  saved value back.
- `set_locked(True)` while the game runs disables the controls whose change
  would break the running session, and shows LOCK_NOTE. The rest stay live:
  they only take effect at the next launch.
- Windows: the Proton section is never built, so nothing here may read or
  write the Linux-only keys there.
"""

import shlex

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..backend import BackendClient, ServerDiscoveryStatus
from ..config import ConfigManager
from ..desktop import open_url
from ..mods import Mod, ModManager, ModStatus
from ..platforms import IS_WINDOWS
from ..updater import DEFAULT_REPO
from ..version import APP_VERSION
from . import icons
from .theme import ACCENT, OK, TEXT, TEXT_DIM, WARN
from .widgets import Switch
from .workers import run_worker

LOCK_NOTE = "Close the game to change this"
DISCORD_INVITE = "https://discord.gg/8hNgwXc9m2"
GITHUB_URL = f"https://github.com/{DEFAULT_REPO}"
WEBSITE_URL = "https://tc-stormrunners.xyz/"

STATUS_STYLE = {
    ModStatus.UP_TO_DATE: OK,
    ModStatus.UPDATE_AVAILABLE: WARN,
    ModStatus.CORRUPTED_INSTALLATION: "#e5533c",
    ModStatus.NOT_INSTALLED: TEXT_DIM,
}


def find_proton_installs():
    # Deferred: Windows never imports the Linux runner.
    from ..runner_linux import find_proton_installs as find
    return find()


def parse_gamescope_args(text: str) -> list[str]:
    from ..runner_linux import parse_gamescope_args as parse
    return parse(text)


class Section(QWidget):
    """Common frame: a lock note on top, then the section's form."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("section")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self.lock_note = QLabel(LOCK_NOTE)
        self.lock_note.setObjectName("lockNote")
        self.lock_note.hide()
        self._layout.addWidget(self.lock_note)
        self._lockables: list[QWidget] = []
        self._locked = False

    def finish(self):
        self._layout.addStretch()

    def field(self, label: str, widget, error: QLabel | None = None):
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        self._layout.addWidget(caption)
        if isinstance(widget, QWidget):
            self._layout.addWidget(widget)
        else:
            self._layout.addLayout(widget)
        if error is not None:
            self._layout.addWidget(error)

    def set_locked(self, locked: bool):
        self._locked = locked
        for widget in self._lockables:
            widget.setEnabled(not locked)
        self.lock_note.setVisible(locked and bool(self._lockables))

    def commit_pending(self):
        """Save text typed but not yet committed. Esc hides the overlay
        without moving focus, so editingFinished never fires for the field
        being edited; without this the field shows a value the config lacks."""

    def revert_invalid(self):
        pass


def _error_label() -> QLabel:
    label = QLabel("")
    label.setObjectName("fieldError")
    label.setWordWrap(True)
    label.hide()
    return label


def _browse_row(owner: QWidget, edit: QLineEdit, directory: bool, on_pick):
    row = QHBoxLayout()
    row.addWidget(edit, stretch=1)
    button = QPushButton("Browse…")

    def browse():
        if directory:
            path = QFileDialog.getExistingDirectory(owner, "Select directory", edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(owner, "Select file", edit.text())
        if path:
            edit.setText(path)
            on_pick()

    button.clicked.connect(browse)
    row.addWidget(button)
    return row, button


class GameSection(Section):
    def __init__(self, config: ConfigManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config

        self.game_dir_edit = QLineEdit(config.game_dir)
        self.game_dir_edit.setPlaceholderText("The folder usually named 'Release'")
        self.game_dir_edit.editingFinished.connect(self._commit_game_dir)
        row, self.game_dir_browse = _browse_row(self, self.game_dir_edit, True, self._commit_game_dir)
        self.field("Game folder", row)

        self.args_edit = QLineEdit(shlex.join(config.run_args))
        self.args_edit.setPlaceholderText("Extra game command-line flags, e.g. -log")
        self.args_edit.editingFinished.connect(self._commit_args)
        self.args_error = _error_label()
        self.field("Launch flags", self.args_edit, self.args_error)

        self.env_table = QTableWidget(0, 2)
        self.env_table.setHorizontalHeaderLabels(["Variable", "Value"])
        self.env_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.env_table.verticalHeader().hide()
        self.env_table.setMinimumHeight(110)
        for name, value in config.env_vars.items():
            self._add_env_row(name, value)
        self.env_table.itemChanged.connect(lambda _item: self._commit_env())
        buttons = QHBoxLayout()
        self.add_env_button = QPushButton("Add variable")
        self.add_env_button.clicked.connect(self._new_env_row)
        self.remove_env_button = QPushButton("Remove selected")
        self.remove_env_button.clicked.connect(self._remove_env_row)
        buttons.addWidget(self.add_env_button)
        buttons.addWidget(self.remove_env_button)
        buttons.addStretch()
        self.field("Environment variables", self.env_table)
        self._layout.addLayout(buttons)

        self.discord_check = Switch("Show game status on Discord")
        self.discord_check.setChecked(config.discord_presence)
        self.discord_check.toggled.connect(self._commit_discord)
        self._layout.addWidget(self.discord_check)

        self._lockables = [self.game_dir_edit, self.game_dir_browse]
        self.finish()

    def _commit_game_dir(self):
        value = self.game_dir_edit.text().strip()
        if value != self.config.game_dir:
            self.config.game_dir = value
            self.config.save()

    def _commit_args(self):
        try:
            run_args = shlex.split(self.args_edit.text())
        except ValueError as e:
            self.args_error.setText(f"Can't read these flags: {e}. Not saved.")
            self.args_error.show()
            return
        self.args_error.hide()
        if run_args != self.config.run_args:
            self.config.run_args = run_args
            self.config.save()

    def _add_env_row(self, name: str, value: str):
        self.env_table.blockSignals(True)
        row = self.env_table.rowCount()
        self.env_table.insertRow(row)
        self.env_table.setItem(row, 0, QTableWidgetItem(name))
        self.env_table.setItem(row, 1, QTableWidgetItem(value))
        self.env_table.blockSignals(False)

    def _new_env_row(self):
        self._add_env_row("", "")
        self.env_table.setCurrentCell(self.env_table.rowCount() - 1, 0)

    def _remove_env_row(self):
        row = self.env_table.currentRow()
        if row >= 0:
            self.env_table.removeRow(row)
            self._commit_env()

    def _commit_env(self):
        env_vars = {}
        for row in range(self.env_table.rowCount()):
            name_item = self.env_table.item(row, 0)
            value_item = self.env_table.item(row, 1)
            name = name_item.text().strip() if name_item else ""
            if name:
                env_vars[name] = value_item.text() if value_item else ""
        if env_vars != self.config.env_vars:
            self.config.env_vars = env_vars
            self.config.save()

    def _commit_discord(self, checked: bool):
        self.config.discord_presence = checked
        self.config.save()

    def commit_pending(self):
        if not self._locked:
            self._commit_game_dir()
        self._commit_args()

    def revert_invalid(self):
        if not self.args_error.isHidden():
            self.args_edit.setText(shlex.join(self.config.run_args))
            self.args_error.hide()


class ProtonSection(Section):
    """Linux only; never built on Windows."""

    def __init__(self, config: ConfigManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config

        proton_row = QHBoxLayout()
        self.proton_combo = QComboBox()
        proton_row.addWidget(self.proton_combo, stretch=1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._populate_protons)
        proton_row.addWidget(self.refresh_button)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse_proton)
        proton_row.addWidget(self.browse_button)
        self.field("Proton version", proton_row)
        self.proton_combo.currentIndexChanged.connect(lambda _i: self._commit_proton())

        self.prefix_edit = QLineEdit(config.wine_prefix)
        self.prefix_edit.editingFinished.connect(self._commit_paths)
        prefix_row, prefix_browse = _browse_row(self, self.prefix_edit, True, self._commit_paths)
        self.field("Wine prefix", prefix_row)

        self.umu_edit = QLineEdit(config.umu_path)
        self.umu_edit.setPlaceholderText("Auto-detect umu-run on PATH")
        self.umu_edit.editingFinished.connect(self._commit_paths)
        umu_row, umu_browse = _browse_row(self, self.umu_edit, False, self._commit_paths)
        self.field("umu-run path", umu_row)

        self.gamemode_check = Switch("Run with GameMode (gamemoderun)")
        self.gamemode_check.setChecked(config.use_gamemode)
        self.gamemode_check.toggled.connect(lambda v: self._set("use_gamemode", v))
        self._layout.addWidget(self.gamemode_check)
        self.mangohud_check = Switch("Enable MangoHud overlay")
        self.mangohud_check.setChecked(config.use_mangohud)
        self.mangohud_check.toggled.connect(lambda v: self._set("use_mangohud", v))
        self._layout.addWidget(self.mangohud_check)
        self.gamescope_check = Switch("Run inside Gamescope")
        self.gamescope_check.setChecked(config.use_gamescope)
        self.gamescope_check.toggled.connect(lambda v: self._set("use_gamescope", v))
        self._layout.addWidget(self.gamescope_check)

        self.gamescope_args_edit = QLineEdit(config.gamescope_args)
        self.gamescope_args_edit.setPlaceholderText("e.g. -W 1920 -H 1080 -f")
        self.gamescope_args_edit.editingFinished.connect(self._commit_gamescope_args)
        self.gamescope_args_error = _error_label()
        self.field("Gamescope arguments", self.gamescope_args_edit, self.gamescope_args_error)

        self._lockables = [self.proton_combo, self.refresh_button, self.browse_button,
                           self.prefix_edit, prefix_browse, self.umu_edit, umu_browse,
                           self.gamescope_check, self.gamescope_args_edit]
        self._populate_protons()
        self.finish()

    def _set(self, key: str, value):
        setattr(self.config, key, value)
        self.config.save()

    def _populate_protons(self):
        if IS_WINDOWS:  # load-bearing if this is ever built on Windows
            return
        self.proton_combo.blockSignals(True)
        self.proton_combo.clear()
        installs = find_proton_installs()
        current = 0
        for i, install in enumerate(installs):
            self.proton_combo.addItem(install.name, install.path)
            if install.path == self.config.proton_path:
                current = i
        if self.config.proton_path and self.config.proton_path not in [i.path for i in installs]:
            self.proton_combo.addItem(self.config.proton_path, self.config.proton_path)
            current = self.proton_combo.count() - 1
        if self.proton_combo.count() == 0:
            self.proton_combo.addItem("No Proton installations found", "")
        self.proton_combo.setCurrentIndex(current)
        self.proton_combo.blockSignals(False)

    def showEvent(self, event):
        # The old dialog saved the combo's shown choice on Save; with
        # save-as-you-go that is "the user looked at it". Without this a first
        # run shows a Proton as selected while none is saved, and Play says
        # "No Proton version selected".
        super().showEvent(event)
        if not self._locked:
            self._commit_proton()

    def _browse_proton(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton directory")
        if path:
            self.proton_combo.addItem(path, path)
            self.proton_combo.setCurrentIndex(self.proton_combo.count() - 1)

    def _commit_proton(self):
        value = self.proton_combo.currentData() or ""
        if value and value != self.config.proton_path:
            self.config.proton_path = value
            self.config.save()

    def _commit_paths(self):
        prefix, umu = self.prefix_edit.text().strip(), self.umu_edit.text().strip()
        if (prefix, umu) != (self.config.wine_prefix, self.config.umu_path):
            self.config.wine_prefix, self.config.umu_path = prefix, umu
            self.config.save()

    def _commit_gamescope_args(self):
        text = self.gamescope_args_edit.text()
        try:
            parse_gamescope_args(text)
        except ValueError as e:
            self.gamescope_args_error.setText(f"Can't read these arguments: {e}. Not saved.")
            self.gamescope_args_error.show()
            return
        self.gamescope_args_error.hide()
        if text != self.config.gamescope_args:
            self.config.gamescope_args = text
            self.config.save()

    def commit_pending(self):
        if not self._locked:
            self._commit_paths()
            self._commit_gamescope_args()

    def revert_invalid(self):
        if not self.gamescope_args_error.isHidden():
            self.gamescope_args_edit.setText(self.config.gamescope_args)
            self.gamescope_args_error.hide()


class ModsSection(Section):
    checked = Signal(bool)  # True: some mod needs installing/updating
    # True while an install runs. The old dialog was modal; now Play and a
    # server change must wait, or load_mods() rewrites mods.json under the
    # install (losing its record, or pruning files being written).
    busy = Signal(bool)

    def __init__(self, mod_manager: ModManager, config: ConfigManager,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.mod_manager = mod_manager
        self.config = config
        self.buttons: list[QPushButton] = []
        self._installing = False

        self.message = QLabel("")
        self.message.setObjectName("dim")
        self.message.setWordWrap(True)
        self._layout.addWidget(self.message)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("sectionScroll")
        self.list_container = QWidget()
        self.list_container.setObjectName("section")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.addStretch()
        scroll.setWidget(self.list_container)
        self._layout.addWidget(scroll, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.hide()
        self._layout.addWidget(self.progress_bar)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._installing:
            self.refresh()

    def refresh(self):
        if not self.config.has_valid_game_dir():
            self._clear()
            self.message.setText("Locate your game files first.")
            return
        self.message.setText("Loading mods…")
        run_worker(self.mod_manager.get_mods_with_statuses, on_finished=self._populate,
                   on_failed=lambda e: self.message.setText(f"Failed to load mods: {e}"))

    def _clear(self):
        self.buttons = []
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _populate(self, mods: list[Mod]):
        self._clear()
        self.message.setText("")
        self.checked.emit(any(m.status != ModStatus.UP_TO_DATE for m in mods))
        if not mods:
            self.message.setText("The selected server does not use any mods.")
            return
        for mod in mods:
            row = QFrame()
            row.setObjectName("modRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 10, 12, 10)
            row_layout.setSpacing(10)
            name = mod.remote["name"]
            if mod.remote["author"]:
                name += f" by {mod.remote['author']}"
            if mod.local is not None:
                name += f" ({mod.local['version']})"
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(QLabel(name))
            status_label = QLabel(mod.status.value)
            status_label.setStyleSheet(f"color: {STATUS_STYLE[mod.status]}; font-size: 11px;")
            text.addWidget(status_label)
            row_layout.addLayout(text)
            row_layout.addStretch()
            if mod.status != ModStatus.UP_TO_DATE:
                action = "Install"
                if mod.status == ModStatus.UPDATE_AVAILABLE:
                    action = f"Update to {mod.remote['version']}"
                elif mod.status == ModStatus.CORRUPTED_INSTALLATION:
                    action = "Reinstall"
                button = QPushButton(action)
                button.clicked.connect(lambda checked=False, m=mod: self._install(m))
                button.setEnabled(not self._locked and not self._installing)
                row_layout.addWidget(button)
                self.buttons.append(button)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
        self.lock_note.setVisible(self._locked and bool(self.buttons))

    def set_locked(self, locked: bool):
        self._locked = locked
        for button in self.buttons:
            button.setEnabled(not locked and not self._installing)
        self.lock_note.setVisible(locked and bool(self.buttons))

    def _install(self, mod: Mod):
        if self._installing:
            return
        self._installing = True
        self.busy.emit(True)
        for button in self.buttons:
            button.setEnabled(False)
        self.message.setText(f"Installing {mod.remote['name']}…")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        run_worker(
            self.mod_manager.install_mod, mod,
            on_finished=lambda _: self._install_done(mod, ok=True),
            on_failed=lambda e: self._install_done(mod, ok=False),
            on_progress=lambda p: self.progress_bar.setValue(int(p * 100)),
        )

    def _install_done(self, mod: Mod, ok: bool):
        self._installing = False
        self.busy.emit(False)
        self.progress_bar.hide()
        self.refresh()
        self.message.setText(f"{mod.remote['name']} installed." if ok
                             else f"Failed to install {mod.remote['name']}.")


class ServerSection(Section):
    changed = Signal()

    def __init__(self, config: ConfigManager, backend: BackendClient,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.config = config
        self.backend = backend
        row = QHBoxLayout()
        self.url_edit = QLineEdit(config.server_discovery_addr or "http://127.0.0.1:8080")
        self.url_edit.returnPressed.connect(self._save)
        row.addWidget(self.url_edit, stretch=1)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        self.error = _error_label()
        self.field("Server address", row, self.error)
        self.saved_note = QLabel("")
        self.saved_note.setObjectName("dim")
        self._layout.addWidget(self.saved_note)
        self._lockables = [self.url_edit, self.save_button]
        self.finish()

    def _fail(self, message: str):
        self.error.setText(message)
        self.error.show()
        self.saved_note.setText("")

    def _save(self):
        if self._locked:
            return
        url = self.url_edit.text().strip()
        if not url.startswith("http"):
            self._fail("Server address must start with 'http'.")
            return
        url = url.rstrip("/")
        self.error.hide()
        self.save_button.setEnabled(False)
        self.saved_note.setText("Checking the server…")
        run_worker(self.backend.discover, url,
                   on_finished=lambda status: self._on_discovered(url, status),
                   on_failed=lambda e: self._on_discovered(url, ServerDiscoveryStatus.UNKNOWN_ERROR))

    def _on_discovered(self, url: str, status: ServerDiscoveryStatus):
        self.save_button.setEnabled(not self._locked)
        if status == ServerDiscoveryStatus.LAUNCHER_OUTDATED:
            self._fail("Your launcher version is incompatible with this server.")
            return
        if status != ServerDiscoveryStatus.OK:
            self._fail("Failed to fetch server information. Make sure the address is correct.")
            return
        self.config.server_discovery_addr = url
        self.config.save()
        self.url_edit.setText(url)
        self.saved_note.setText("Saved.")
        self.changed.emit()

    def revert_invalid(self):
        self.error.hide()
        self.saved_note.setText("")
        self.url_edit.setText(self.config.server_discovery_addr or "http://127.0.0.1:8080")


class AboutSection(Section):
    logs_requested = Signal()
    game_logs_requested = Signal()
    update_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout.addStretch()

        self.logo = QLabel()
        self.logo.setPixmap(icons.pixmap(icons.LOGO, TEXT, 96))
        self._layout.addWidget(self.logo, 0, Qt.AlignHCenter)
        title = QLabel("The Cycle: Storm Runners")
        title.setObjectName("aboutTitle")
        self._layout.addWidget(title, 0, Qt.AlignHCenter)

        platform = "Windows" if IS_WINDOWS else "Linux"
        self.info = QLabel(" &nbsp;|&nbsp; ".join(
            f"{label}: <span style='color: {ACCENT}; font-weight: 600;'>{value}</span>"
            for label, value in (("Version", APP_VERSION), ("Platform", platform))))
        self.info.setTextFormat(Qt.RichText)
        self._layout.addWidget(self.info, 0, Qt.AlignHCenter)
        blurb = QLabel("A community launcher for The Cycle, on Linux and Windows.")
        blurb.setObjectName("dim")
        self._layout.addWidget(blurb, 0, Qt.AlignHCenter)

        self.update_label = QLabel("")
        self.update_label.setObjectName("link")
        self.update_label.setTextFormat(Qt.RichText)
        self.update_label.setOpenExternalLinks(False)
        self.update_label.setCursor(Qt.PointingHandCursor)
        self.update_label.linkActivated.connect(lambda _href: self.update_requested.emit())
        self.update_label.hide()
        self._layout.addWidget(self.update_label, 0, Qt.AlignHCenter)

        self._layout.addSpacing(8)
        self.github_button = self._link_button("GitHub", icons.GITHUB, GITHUB_URL)
        self.discord_button = self._link_button("Join our Discord", icons.DISCORD, DISCORD_INVITE)
        self.website_button = self._link_button("Website", icons.WEBSITE, WEBSITE_URL)
        self._centered_row(self.github_button, self.discord_button, self.website_button)

        self.logs_button = QPushButton("Launcher logs")
        icons.apply(self.logs_button, icons.LOG_FILE, TEXT, 18)
        self.logs_button.clicked.connect(self.logs_requested.emit)
        self.game_logs_button = QPushButton("Game logs")
        icons.apply(self.game_logs_button, icons.FOLDER, TEXT, 18)
        self.game_logs_button.clicked.connect(self.game_logs_requested.emit)
        self._centered_row(self.logs_button, self.game_logs_button)

        self._layout.addSpacing(8)
        built_with = QLabel("Built with")
        built_with.setObjectName("dim")
        self.heart = QLabel()
        self.heart.setPixmap(icons.pixmap(icons.HEART, ACCENT, 18))
        self.credit_after = QLabel("by the TC: Storm Runners team")
        self.credit_after.setObjectName("dim")
        self._centered_row(built_with, self.heart, self.credit_after)
        self.finish()

    def _link_button(self, text: str, icon_name: str, url: str) -> QPushButton:
        button = QPushButton(text)
        icons.apply(button, icon_name, TEXT, 18)
        button.setToolTip(url)
        # open_url, not QDesktopServices: it strips the bundle's library
        # environment before handing the URL to the host's browser.
        button.clicked.connect(lambda: open_url(url))
        return button

    def _centered_row(self, *widgets: QWidget):
        row = QHBoxLayout()
        row.addStretch()
        for widget in widgets:
            row.addWidget(widget)
        row.addStretch()
        self._layout.addLayout(row)

    def set_update_text(self, text: str | None):
        if text:
            self.update_label.setText(f"<a href='#'>{text}</a>")
            self.update_label.show()
        else:
            self.update_label.hide()
