"""Settings, drawn over the main window instead of in a separate window.

A full-window dimming layer with a centred panel: sections listed on the left,
one section shown on the right. Closes with the X button, Esc, or a click on the dim area
outside the panel; closing is never refused, and a field still holding an
invalid value is put back to its saved value (see settings_sections).
"""

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..backend import BackendClient
from ..config import ConfigManager
from ..mods import ModManager
from ..platforms import IS_WINDOWS
from . import icons
from .settings_sections import AboutSection, GameSection, ModsSection, ProtonSection, ServerSection
from .theme import TEXT

TITLES = {"game": "Game", "proton": "Proton", "mods": "Mods", "server": "Server", "about": "About"}


class SettingsOverlay(QWidget):
    closed = Signal()
    server_changed = Signal()
    mods_checked = Signal(bool)
    open_logs_requested = Signal()
    open_game_logs_requested = Signal()
    mods_busy = Signal(bool)
    update_requested = Signal()

    def __init__(self, config: ConfigManager, backend: BackendClient,
                 mod_manager: ModManager, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("overlayDim")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.game = GameSection(config)
        self.proton = None if IS_WINDOWS else ProtonSection(config)
        self.mods = ModsSection(mod_manager, config)
        self.server = ServerSection(config, backend)
        self.about = AboutSection()
        self._sections: dict[str, QWidget] = {"game": self.game}
        if self.proton is not None:
            self._sections["proton"] = self.proton
        self._sections.update(mods=self.mods, server=self.server, about=self.about)

        self.panel = QFrame(self)
        self.panel.setObjectName("overlayPanel")
        panel_layout = QHBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)

        side = QFrame()
        side.setObjectName("overlaySide")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 16, 12, 12)
        heading = QLabel("SETTINGS")
        heading.setObjectName("overlayHeading")
        side_layout.addWidget(heading)
        self.section_list = QListWidget()
        self.section_list.setObjectName("sectionList")
        self.section_list.addItems([TITLES[k] for k in self._sections])
        side_layout.addWidget(self.section_list, 1)
        side.setFixedWidth(180)
        panel_layout.addWidget(side)

        main = QWidget()
        main.setObjectName("overlayMain")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(22, 16, 16, 16)
        header = QHBoxLayout()
        self.title = QLabel("")
        self.title.setObjectName("sectionTitle")
        header.addWidget(self.title)
        header.addStretch()
        self.close_button = QPushButton()
        icons.apply(self.close_button, icons.CLOSE, TEXT, 16)
        self.close_button.setToolTip("Close")
        self.close_button.setObjectName("closeButton")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button)
        main_layout.addLayout(header)
        self.stack = QStackedWidget()
        for section in self._sections.values():
            if section is self.mods:  # the mods list scrolls on its own
                self.stack.addWidget(section)
                continue
            scroll = QScrollArea()
            scroll.setObjectName("sectionScroll")
            scroll.setWidgetResizable(True)
            scroll.setWidget(section)
            self.stack.addWidget(scroll)
        main_layout.addWidget(self.stack, 1)
        panel_layout.addWidget(main, 1)

        self.section_list.currentRowChanged.connect(self._show_row)
        self.server.changed.connect(self.server_changed.emit)
        self.mods.checked.connect(self.mods_checked.emit)
        self.mods.busy.connect(self._on_mods_busy)
        self._running = False
        self._installing = False
        self.about.logs_requested.connect(self.open_logs_requested.emit)
        self.about.game_logs_requested.connect(self.open_game_logs_requested.emit)
        self.about.update_requested.connect(self.update_requested.emit)

        parent.installEventFilter(self)
        self.setGeometry(parent.rect())
        self.hide()

    # --- public ---

    def section_keys(self) -> list[str]:
        return list(self._sections)

    def current_section(self) -> str:
        return self.section_keys()[self.section_list.currentRow()]

    def open_section(self, key: str = "game") -> None:
        keys = self.section_keys()
        row = keys.index(key) if key in keys else 0
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        if self.section_list.currentRow() == row:
            self._show_row(row)
        else:
            self.section_list.setCurrentRow(row)
        self.setFocus()

    def close(self) -> None:
        if self.isHidden():
            return
        for section in self._sections.values():
            section.commit_pending()
            section.revert_invalid()
        self.hide()
        self.closed.emit()

    def set_game_running(self, running: bool) -> None:
        self._running = running
        for section in self._sections.values():
            section.set_locked(running)
        self._lock_server()

    def _on_mods_busy(self, busy: bool):
        self._installing = busy
        self._lock_server()
        self.mods_busy.emit(busy)

    def _lock_server(self):
        # A server change prunes the previous server's mods: never mid-install.
        self.server.set_locked(self._running or self._installing)

    def set_update_text(self, text: str | None) -> None:
        self.about.set_update_text(text)

    # --- internals ---

    def _show_row(self, row: int):
        if row < 0:
            return
        self.stack.setCurrentIndex(row)
        self.title.setText(TITLES[self.section_keys()[row]])

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        mx, my = max(16, int(w * 0.08)), max(12, int(h * 0.07))
        self.panel.setGeometry(mx, my, w - 2 * mx, h - 2 * my)

    def mousePressEvent(self, event):
        if not self.panel.geometry().contains(event.position().toPoint()):
            self.close()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
