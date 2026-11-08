import logging
import os
import time
from datetime import datetime
from urllib.parse import urlparse

from PySide6.QtCore import QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..auth import LaunchAuth, SessionCheck, SessionKeepAlive, SessionManager
from ..backend import BackendClient, ServerDiscoveryStatus
from ..config import LAUNCHER_USERDIR, PROTOCOL_VERSION, ConfigManager
from ..desktop import open_path
from ..avatar import resolve_avatar, valid_steam_id
from ..mods import ModManager
from ..oidc import Protocol
from ..platforms import game_log_path, steam_install_path
from ..playtime import PLAYTIME_FILE, PlaySession, PlayTimeStore, format_last_played, format_play_time
from ..presence import start_presence
from ..runner import GAME_LOG, GameRunner, steam_preflight_issue
from ..updater import (
    DEFAULT_REPO,
    download,
    running_bundle_path,
    stage,
    staging_path,
    update_offer,
)
from ..version import APP_VERSION
from . import icons
from .background import ArtBackground
from .settings_overlay import SettingsOverlay
from .theme import STATUS_COLORS, SURFACE_HI, TEXT, TEXT_DIM
from .workers import run_worker

logger = logging.getLogger(__name__)


SIGNIN_UNREACHABLE = "Couldn't reach the sign-in server. Try again in a moment."
MODS_NOT_CONFIRMED = "Couldn't confirm your mods with the server. Try again in a moment."
MODS_UNAVAILABLE = "Couldn't get the mod list from the server. Try again in a moment."


#: The player count refreshes this often while the game runs (5 s otherwise).
PLAYING_POLL_SECONDS = 60

AVATAR_SIZE = 40
CHIP_ICON_SIZE = 16
CHIP_DOT_SIZE = 12
COG_ICON_SIZE = 24


def account_title(state: str, steam_name: str) -> str:
    """The account block's first line. The name only exists on OIDC sessions
    (the ID token's profile claims); legacy sign-in never says who it is."""
    if state == "signed_in":
        return steam_name or "Signed in"
    if state == "expired":
        return "Session expired — sign in again"
    return "Not signed in"


def round_avatar(source: QPixmap, size: int) -> QPixmap:
    scaled = source.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    painter.end()
    return out


def placeholder_avatar(name: str, size: int) -> QPixmap:
    """A filled circle with the name's initial, or the person icon."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(SURFACE_HI))
    painter.drawEllipse(0, 0, size, size)
    if name.strip():
        painter.setPen(QColor(TEXT))
        font = painter.font()
        font.setPixelSize(int(size * 0.45))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(out.rect(), Qt.AlignCenter, name.strip()[:1].upper())
    else:
        inset = size * 0.22
        icons.paint(painter, icons.USER, TEXT,
                    QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    painter.end()
    return out


def primary_action(account_state: str, game_dir_ok: bool) -> str:
    """What the primary button does: 'login', 'locate' or 'play'.

    Logging in never requires game files, so a missing game directory only
    changes the action once the user is signed in and would otherwise Play.
    """
    if account_state != "signed_in":
        return "login"
    return "play" if game_dir_ok else "locate"


class MainWindow(QMainWindow):
    login_succeeded = Signal()
    game_exited = Signal()

    def __init__(self, config: ConfigManager):
        super().__init__()
        self.config = config
        self.backend = BackendClient(config)
        self.session_manager = SessionManager(config, self.backend)
        self.mod_manager = ModManager(config, self.backend)
        self.runner = GameRunner(config)
        self._presence = None
        self._keepalive = None
        self._mods_attention = False
        self._mods_busy = False
        self._avatar_id: str | None = None
        self.playtime = PlayTimeStore(PLAYTIME_FILE)
        self.playtime.load()
        self._play_session: PlaySession | None = None
        self._playtime_timer = QTimer(self)
        self._playtime_timer.setInterval(60_000)
        self._playtime_timer.timeout.connect(self._checkpoint_play_time)

        self._logged_in = False
        self._server_online = False
        self._status_hold = False
        self._update_info = None

        self.setWindowTitle("The Cycle Launcher")
        self.setMinimumSize(860, 500)
        self.resize(1100, 620)

        self._build_ui()
        self._refresh_play_time()

        self.login_succeeded.connect(self._on_login_success)
        self.game_exited.connect(self._on_game_exit)

        self._refresh_account_ui()
        self._set_status("Select a server to begin", "offline", population=True)

        self._last_status_poll = 0.0
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(5000)
        self.status_timer.timeout.connect(self._poll_status)
        self.status_timer.start()

        self._startup_checks()
        # Never blocks or gates a launch: a failed check is silent, and a build
        # running from source returns (None, False) without touching the network.
        run_worker(update_offer, APP_VERSION,
                   on_finished=self._on_update_checked,
                   on_failed=lambda e: logger.debug(f"Update check failed: {e}"))

    # --- layout ---

    def _build_ui(self):
        central = ArtBackground()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)

        # Top: wordmark left, chips right
        top = QHBoxLayout()
        top.setSpacing(8)
        header = QVBoxLayout()
        header.setSpacing(0)
        title = QLabel("THE CYCLE")
        title.setObjectName("title")
        subtitle = QLabel("Storm Runners")
        subtitle.setObjectName("subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        top.addLayout(header)
        top.addStretch()
        self._update_href = "#"
        self.chip_update = self._chip("", self._on_update_chip)
        self.chip_update.hide()
        top.addWidget(self.chip_update, 0, Qt.AlignTop)
        self.chip_online = self._chip("Select a server to begin",
                                      lambda: self.overlay.open_section("server"))
        icons.apply(self.chip_online, icons.DOT, TEXT_DIM, CHIP_DOT_SIZE, filled=True)
        top.addWidget(self.chip_online, 0, Qt.AlignTop)
        self.chip_mods = self._chip("Mods", lambda: self.overlay.open_section("mods"))
        top.addWidget(self.chip_mods, 0, Qt.AlignTop)
        root.addLayout(top)

        root.addStretch()

        # Game files notice — visible while no valid game directory is set
        self.game_notice = QFrame()
        self.game_notice.setObjectName("banner")
        notice_layout = QHBoxLayout(self.game_notice)
        notice_layout.setContentsMargins(14, 8, 10, 8)
        notice_layout.setSpacing(10)
        notice_dot = QLabel()
        notice_dot.setPixmap(icons.pixmap(icons.DOT, STATUS_COLORS["waiting"], 12, filled=True))
        notice_layout.addWidget(notice_dot)
        notice_text = QLabel("Game files not set — the install folder is usually named 'Release'")
        notice_text.setWordWrap(True)
        notice_layout.addWidget(notice_text, 1)
        self.btn_locate = QPushButton("Locate…")
        self.btn_locate.setObjectName("link")
        self.btn_locate.setCursor(Qt.PointingHandCursor)
        self.btn_locate.clicked.connect(self.locate_game_files)
        notice_layout.addWidget(self.btn_locate)
        self.game_notice.hide()
        root.addWidget(self.game_notice)

        # Transient status, right-aligned over the Play button
        status_row = QHBoxLayout()
        status_row.addStretch()
        self.status_line = QLabel("")
        self.status_line.setObjectName("statusLine")
        self.status_line.hide()
        status_row.addWidget(self.status_line)
        root.addLayout(status_row)

        # The bottom bar
        dock = QFrame()
        dock.setObjectName("dock")
        dock_layout = QHBoxLayout(dock)
        dock_layout.setContentsMargins(14, 10, 12, 10)
        dock_layout.setSpacing(14)

        self.avatar = QLabel()
        self.avatar.setObjectName("avatar")
        self.avatar.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        dock_layout.addWidget(self.avatar)
        account = QVBoxLayout()
        account.setSpacing(0)
        self.account_title = QLabel("Not signed in")
        self.account_title.setObjectName("accountTitle")
        account.addWidget(self.account_title)
        server_row = QHBoxLayout()
        server_row.setSpacing(4)
        self.account_server = QLabel("")
        self.account_server.setObjectName("dim")
        server_row.addWidget(self.account_server)
        self.btn_logout = QPushButton("Log out")
        self.btn_logout.setObjectName("link")
        self.btn_logout.setCursor(Qt.PointingHandCursor)
        self.btn_logout.clicked.connect(self.log_out)
        server_row.addWidget(self.btn_logout)
        server_row.addStretch()
        account.addLayout(server_row)
        dock_layout.addLayout(account)
        dock_layout.addStretch(1)

        self.play_time_value = QLabel("")
        self.last_played_value = QLabel("")
        for caption, value in (("PLAY TIME", self.play_time_value),
                               ("LAST PLAYED", self.last_played_value)):
            stat = QVBoxLayout()
            stat.setSpacing(0)
            label = QLabel(caption)
            label.setObjectName("statLabel")
            value.setObjectName("statValue")
            stat.addWidget(label)
            stat.addWidget(value)
            dock_layout.addLayout(stat)
        dock_layout.addStretch(1)

        self.btn_settings = QPushButton()
        icons.apply(self.btn_settings, icons.COG, TEXT, COG_ICON_SIZE)
        self.btn_settings.setObjectName("cog")
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.clicked.connect(self.open_settings_dialog)
        dock_layout.addWidget(self.btn_settings)
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("primary")
        # Fixed height: the pill radius in theme.py must be exactly half of it.
        self.btn_play.setMinimumWidth(220)
        self.btn_play.setFixedHeight(52)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.clicked.connect(self.on_primary_clicked)
        dock_layout.addWidget(self.btn_play)
        root.addWidget(dock)

        # Settings draw over everything; created last so it stacks on top.
        self.overlay = SettingsOverlay(self.config, self.backend, self.mod_manager, central)
        self.overlay.closed.connect(self._refresh_account_ui)
        self.overlay.server_changed.connect(self._on_server_changed)
        self.overlay.mods_checked.connect(self._set_mods_attention)
        self.overlay.mods_busy.connect(self._on_mods_busy)
        self.overlay.open_logs_requested.connect(lambda: self._open_host_path(LAUNCHER_USERDIR))
        self.overlay.open_game_logs_requested.connect(self._open_game_logs)
        self.overlay.update_requested.connect(self._on_update_chip)

    def _chip(self, text: str, slot) -> QPushButton:
        chip = QPushButton(text)
        chip.setObjectName("chip")
        chip.setCursor(Qt.PointingHandCursor)
        chip.clicked.connect(slot)
        return chip

    def _on_mods_busy(self, busy: bool):
        self._mods_busy = busy
        self._update_primary_enabled()

    def _set_mods_attention(self, attention: bool):
        self._mods_attention = attention
        if attention:
            icons.apply(self.chip_mods, icons.WARNING, STATUS_COLORS["waiting"], CHIP_ICON_SIZE)
        else:
            self.chip_mods.setIcon(QIcon())

    # --- play time ---

    def _refresh_play_time(self):
        self.play_time_value.setText(format_play_time(self.playtime.total_seconds))
        self.last_played_value.setText(
            format_last_played(self.playtime.last_played, datetime.now()))

    def _checkpoint_play_time(self):
        if self._play_session is not None:
            self._play_session.checkpoint()
        self._refresh_play_time()

    def _finish_play_time(self):
        self._playtime_timer.stop()
        if self._play_session is not None:
            self._play_session.finish()
            self._play_session = None
        self._refresh_play_time()

    # --- avatar ---

    def _refresh_avatar(self, state: str):
        """OIDC sessions carry a steam_id; legacy ones never do, and we do not
        guess (the wrong person's face is worse than a placeholder)."""
        steam_id = self.config.steam_id if state == "signed_in" else ""
        if not valid_steam_id(steam_id):
            self._avatar_id = None
            self.avatar.setPixmap(placeholder_avatar("", AVATAR_SIZE))
            return
        if self._avatar_id == steam_id:
            return
        self._avatar_id = steam_id
        self.avatar.setPixmap(placeholder_avatar(self.config.steam_name, AVATAR_SIZE))
        run_worker(resolve_avatar, steam_id, session=self.backend.session,
                   steam_dir=steam_install_path(),
                   on_finished=lambda path, sid=steam_id: self._set_avatar(sid, path))

    def _set_avatar(self, steam_id: str, path: str | None):
        if steam_id != self._avatar_id or not path:
            return
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.avatar.setPixmap(round_avatar(pixmap, AVATAR_SIZE))


    # --- startup ---

    def _startup_checks(self):
        if self.config.server_discovery_addr:
            def on_discovered(status):
                if status == ServerDiscoveryStatus.LAUNCHER_OUTDATED:
                    self._set_status("This server requires a newer launcher", "offline")
                    self._status_hold = True
                else:
                    self._poll_status()
                    self._detect_and_validate_async()

            run_worker(self.backend.discover, self.config.server_discovery_addr,
                       on_finished=on_discovered)
        self._check_launcher_update()

    def _check_launcher_update(self):
        if not self.config.server_discovery_addr:
            return

        def check():
            manifest = self.backend.launcher_update_manifest()
            if manifest and manifest.get("version") not in (None, PROTOCOL_VERSION):
                return manifest["version"]
            return None

        def notify(version):
            if version and not self._status_hold:
                self._set_status(f"Server expects launcher {version} (protocol may differ)", "waiting")

        run_worker(check, on_finished=notify)

    # --- account / session ---

    def _account_state(self) -> str:
        if self.session_manager.is_signed_in():
            return "signed_in"
        if self.session_manager.has_refresh_token():
            return "expired"
        return "signed_out"

    def _refresh_account_ui(self):
        state = self._account_state()
        game_dir_ok = self.config.has_valid_game_dir()
        self._primary_action = primary_action(state, game_dir_ok)
        self.game_notice.setVisible(not game_dir_ok)
        self.account_title.setText(account_title(state, self.config.steam_name))
        addr = self.config.server_discovery_addr
        self.account_server.setText((urlparse(addr).netloc or addr) if addr else "No server selected")
        self._refresh_avatar(state)
        if state == "signed_in":
            self._logged_in = True
            self.btn_logout.show()
            self.btn_play.setText("Play" if self._primary_action == "play"
                                  else "Locate game files…")
        else:
            self._logged_in = False
            self.btn_logout.hide()
            self.btn_play.setText("Log in with Steam")
        self._update_primary_enabled()

    def _detect_and_validate_async(self):
        """Learn which sign-in the server speaks, then confirm a stored session
        is still accepted, so the button never shows 'Play' for a session the
        server has ended. Under OIDC the check is a refresh."""
        def check():
            self.session_manager.detect_protocol()
            if self._account_state() != "signed_in":
                return None
            return self.session_manager.validate()

        def apply(result):
            # Only a definite refusal signs out, and quietly: after a server
            # switches protocol that is the one expected re-login. The probe
            # itself may have dropped a session of the other kind.
            if result is SessionCheck.REFUSED:
                self.session_manager.invalidate_session()
                self.config.save()
            self._refresh_account_ui()

        run_worker(check, on_finished=apply)

    def _on_login_success(self):
        self._status_hold = False
        self._refresh_account_ui()
        self._poll_status()

    def _release_status_hold(self):
        """End a transient status message and resume population polling."""
        if self._status_hold:
            self._status_hold = False
            self._poll_status()

    def closeEvent(self, event):
        """While an OIDC game session is kept alive, closing the launcher would
        let the server session lapse 60 s later -- before a slow-starting game
        signs in, or before it signs in again. Offer to minimise instead."""
        if self._keepalive is not None and self._keepalive.is_running():
            answer = QMessageBox.question(
                self, "Game still running",
                "The launcher keeps your sign-in alive while the game runs. "
                "If you close it now, the game may fail to sign in.\n\n"
                "Minimize the launcher instead?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
            )
            if answer == QMessageBox.Yes:
                self.showMinimized()
                event.ignore()
                return
        self._finish_play_time()
        super().closeEvent(event)

    def _release_login_hold(self):
        """Recovery if the browser login is abandoned: resume status polling."""
        if self._status_hold and not self._logged_in:
            self._status_hold = False
            self._poll_status()

    def log_out(self):
        token = self.config.refresh_token
        self.session_manager.invalidate_session()
        self.config.save()
        self._refresh_account_ui()
        if token and self.session_manager.protocol is Protocol.OIDC:
            # Best effort, never waited on: local sign-out already happened.
            run_worker(self.session_manager.revoke, token)

    # --- status bar ---

    def _set_status(self, text: str, state: str, population: bool = False):
        """Population (server reachable, players online) goes to the online
        chip; everything else is a transient message over the Play button.
        Both drive _server_online exactly as the single status bar did."""
        color = STATUS_COLORS.get(state, STATUS_COLORS["offline"])
        if population:
            self.chip_online.setText(text)
            icons.apply(self.chip_online, icons.DOT, color, CHIP_DOT_SIZE, filled=True)
            self.chip_online.setStyleSheet(f"QPushButton#chip {{ border-left: 3px solid {color}; }}")
            if not self.runner.is_running():  # "Game running…" stays up during play
                self.status_line.hide()
        else:
            self.status_line.setText(text)
            self.status_line.setStyleSheet(f"border-left: 3px solid {color};")
            self.status_line.show()
        self._server_online = state == "online"
        self._update_primary_enabled()


    def _update_primary_enabled(self):
        running = self.runner.is_running()
        # The lock-while-playing rule (spec): risky settings and Log out wait
        # until the game has closed.
        self.overlay.set_game_running(running)
        self.btn_logout.setEnabled(not running)
        if running:
            self.btn_play.setText("Stop game")
            self.btn_play.setEnabled(True)
            return
        action = getattr(self, "_primary_action", "login")
        if action == "locate":
            # Picking a folder needs neither a server nor a session.
            self.btn_play.setEnabled(True)
        elif action == "play":
            # Not mid mod-install: preflight's load_mods() would rewrite
            # mods.json under it.
            self.btn_play.setEnabled(self._server_online and not self._mods_busy)
        else:
            # Login needs a server (its OpenID URL comes from the discovery addr).
            self.btn_play.setEnabled(bool(self.config.server_discovery_addr))

    def _poll_status(self):
        if self._status_hold:
            return
        # Quieter during play: the launcher is usually minimised then.
        now = time.monotonic()
        if self.runner.is_running() and now - self._last_status_poll < PLAYING_POLL_SECONDS:
            return
        if self.config.backend_data is None:
            self._set_status("Select a server to begin", "offline", population=True)
            return
        self._last_status_poll = now

        def apply(data):
            if data is None:
                self._set_status("Server offline", "offline", population=True)
            else:
                self._set_status(f"{data['online']} players online", "online", population=True)

        run_worker(self.backend.server_status, on_finished=apply)

    # --- self-update ---

    def _on_update_checked(self, result):
        """A newer release exists. Never modal, never automatic: consent is
        explicit, because a silent update that silently fails is the worst
        outcome."""
        info, replaceable = result
        if info is None:
            return
        self._update_info = info
        if replaceable:
            # Guarded like _check_launcher_update's notify(): a hold means a
            # login or an "outdated launcher" message owns the status line.
            if not self._status_hold:
                self._set_status(
                    f"Version {info.version} is available — click to update",
                    "waiting")
            self._update_href = "#"
            text = f"Update to {info.version}"
        else:
            # Unwritable install directory: offer the download instead of
            # failing at the swap with the whole file already on disk.
            self._update_href = f"https://github.com/{DEFAULT_REPO}/releases/latest"
            text = f"Version {info.version} available"
        self.chip_update.setText(text)
        icons.apply(self.chip_update, icons.UPDATE, TEXT, CHIP_ICON_SIZE)
        self.chip_update.setVisible(True)
        self.overlay.set_update_text(text)

    def _on_update_chip(self):
        self._on_update_link(self._update_href)

    def _on_update_link(self, href: str):
        """'#' is our in-place update; a real URL goes to the browser."""
        if href and href != "#":
            QDesktopServices.openUrl(QUrl(href))
            return
        self._start_update()

    def _start_update(self):
        info = self._update_info
        if info is None:
            return
        answer = QMessageBox.question(
            self, "Update launcher",
            f"Download and install version {info.version}?\n\n"
            "The launcher will close and restart itself.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        if self.runner.is_running():
            # Quitting mid-game would orphan the running game and drop its
            # presence session -- and this feature's one rule is that it can
            # never break a launch.
            QMessageBox.information(
                self, "Update launcher",
                "Close the game first — the launcher has to restart to finish "
                "the update.")
            return
        bundle = running_bundle_path()
        if bundle is None:
            return
        # Beside the target, NOT in %TEMP%: same-volume, so the helper's move is
        # an atomic rename. can_replace() already proved this directory writable.
        dest = staging_path(bundle)
        self.chip_update.setVisible(False)
        self.overlay.set_update_text(None)
        self._status_hold = True
        self._set_status("Downloading update…", "waiting")
        run_worker(download, info.url, dest,
                   expected_sha256=info.sha256,
                   on_progress=self._update_progress,
                   on_finished=self._update_downloaded,
                   on_failed=lambda e: self._update_failed(str(e)))

    def _update_progress(self, fraction: float):
        # A ~78MB download with no feedback looks frozen, and a user who kills
        # the launcher mid-download is the case we least want to invite.
        self._set_status(f"Downloading update… {int(fraction * 100)}%", "waiting")

    def _update_downloaded(self, path: str):
        """Spawn the helper, then quit through Qt.

        NOT `updater.apply()`: this slot runs on the GUI thread, so apply()'s
        `sys.exit(0)` would raise SystemExit inside a Qt slot -- PySide6 handles
        an exception escaping a slot inconsistently (it can be printed and
        swallowed, or abort the process), and either way the event loop never
        unwinds. quit() returns from app.exec() normally, so `sys.exit(app.exec())`
        and interpreter shutdown run, and any teardown hooked to the quit path
        still gets its chance.
        """
        try:
            stage(path)
        except Exception as e:
            self._update_failed(str(e))
            return
        QApplication.instance().quit()

    def _update_failed(self, message: str):
        self._status_hold = False
        self.chip_update.setVisible(self._update_info is not None)
        QMessageBox.warning(self, "Update failed",
                            f"Could not install the update:\n\n{message}")

    # --- login / play ---

    def on_primary_clicked(self):
        if self.runner.is_running():
            answer = QMessageBox.question(
                self, "Stop game", "Stop the game process?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self.runner.stop()
            return
        action = getattr(self, "_primary_action", "login")
        if action == "login":
            self._start_login()
        elif action == "locate":
            self.locate_game_files()
        else:
            self._start_play()

    def locate_game_files(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select game directory — usually a folder named 'Release'")
        if not path:
            return
        previous = self.config.game_dir
        self.config.game_dir = path
        if not self.config.has_valid_game_dir():
            self.config.game_dir = previous
            QMessageBox.warning(
                self, "The Cycle not found",
                "That folder does not contain The Cycle game files.\n\n"
                "Pick the game folder as distributed (usually named 'Release') — "
                "it contains Prospect/Binaries/Win64/Prospect-Win64-Shipping.exe.",
            )
            return
        self.config.save()
        self._refresh_account_ui()

    def _start_login(self):
        if not self.config.server_discovery_addr:
            self.overlay.open_section("server")
            return
        # Probe on every login, not only when the startup probe failed: a
        # launcher left open across the server's switch would otherwise send
        # the browser to the deleted legacy endpoint. One GET. A failed probe
        # keeps the protocol already known, so only a never-reached server stops here.
        self._status_hold = True
        self._set_status("Contacting the server…", "waiting")

        def after_probe(protocol):
            self._status_hold = False
            if protocol is Protocol.UNKNOWN:
                self._poll_status()
                QMessageBox.information(self, "Sign-in server unreachable", SIGNIN_UNREACHABLE)
                return
            self._refresh_account_ui()
            self._begin_login()

        run_worker(self.session_manager.detect_protocol, on_finished=after_probe,
                   on_failed=lambda e: after_probe(Protocol.UNKNOWN))

    def _begin_login(self):
        self._status_hold = True
        self._set_status("Waiting for browser sign-in…", "waiting")
        QTimer.singleShot(120_000, self._release_login_hold)
        self.session_manager.initiate_login(self.login_succeeded.emit)

    def _start_play(self):
        # The compat-path override is Linux-only; the Windows implementation
        # ignores the argument.
        steam_issue = steam_preflight_issue(
            self.config.env_vars.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", "")
        )
        if steam_issue:
            answer = QMessageBox.warning(
                self, "Steam setup problem",
                steam_issue + "\n\nLaunch anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        self.btn_play.setEnabled(False)
        self._status_hold = True
        self._set_status("Checking mods and session…", "waiting")

        def preflight():
            # A fresh list is required: without one the gate below passes on
            # an empty or stale list, and the game is refused at matchmaking.
            if not self.mod_manager.load_mods():
                return "mods_unavailable", ""
            if not self.mod_manager.are_mods_installed(refresh=False):
                return "mods", ""
            # Only valid right after are_mods_installed() (see attestation()).
            digest = self.mod_manager.attestation()
            return self.session_manager.prepare_launch(digest), digest

        run_worker(preflight, on_finished=self._on_preflight_done,
                   on_failed=lambda e: self._launch_failed(str(e)))

    def _on_preflight_done(self, outcome: tuple):
        result, digest = outcome
        self._status_hold = False
        if result == "mods":
            self._set_mods_attention(True)
            self._launch_failed(None)
            QMessageBox.critical(self, "Mods verification failed",
                                 "Failed to verify mod installations. Open Mods (top right) and "
                                 "install missing mods or reinstall corrupted mods.")
            return
        if result == "mods_unavailable":
            self._launch_failed(None)
            QMessageBox.warning(self, "Mod list unavailable", MODS_UNAVAILABLE)
            return
        self._set_mods_attention(False)  # the check just passed
        if result is LaunchAuth.SIGNED_OUT:
            # The server switched sign-in; the old session was dropped. Quietly.
            self._launch_failed(None)
            self._refresh_account_ui()
            self._status_hold = True
            self._set_status("Log in with Steam to play", "waiting")
            QTimer.singleShot(10_000, self._release_status_hold)
            return
        if result is LaunchAuth.AUTH_UNREACHABLE:
            self._launch_failed(None)
            QMessageBox.warning(self, "Sign-in server unreachable", SIGNIN_UNREACHABLE)
            return
        if result is LaunchAuth.ATTESTATION_FAILED:
            self._launch_failed(None)
            QMessageBox.warning(self, "Mods not confirmed", MODS_NOT_CONFIRMED)
            return
        if result is LaunchAuth.SESSION_REFUSED:
            self.session_manager.invalidate_session()
            self.config.save()
            self._refresh_account_ui()
            self._launch_failed(None)
            QMessageBox.warning(self, "Session expired",
                                "Your game session is no longer valid. Log in with Steam again to play.")
            return

        first_run = self.runner.needs_first_run_setup()
        try:
            self.runner.launch(on_exit=self.game_exited.emit)
        except Exception as e:
            logger.exception(e)
            self._launch_failed(str(e))
            return
        self._presence = start_presence(self.config)
        if self.session_manager.protocol is Protocol.OIDC:
            self._keepalive = SessionKeepAlive(self.session_manager, digest)
            self._keepalive.start()
        self._launch_started_at = time.monotonic()
        self._play_session = PlaySession(self.playtime)
        self._play_session.start()
        self._playtime_timer.start()
        self._update_primary_enabled()
        if first_run:
            self._set_status(
                "First launch: Proton is downloading its runtime — this can take several minutes…",
                "waiting",
            )
        else:
            self._set_status("Game running…", "online")

    def _launch_failed(self, message: str | None):
        self._status_hold = False
        self._update_primary_enabled()
        self._poll_status()
        if message:
            QMessageBox.critical(self, "Launch Error", message)

    def _open_game_logs(self):
        """The folder holding Prospect.log. The game creates it on its first
        run, and opening a missing folder would silently do nothing."""
        folder = os.path.dirname(game_log_path(self.config))
        if not os.path.isdir(folder):
            QMessageBox.information(self, "Game logs",
                                    "There are no game logs yet. They appear once the game has run.")
            return
        self._open_host_path(folder)

    def _open_host_path(self, path: str):
        """Open a file/folder on the host, working around the frozen build's
        environment (see desktop.open_path); QDesktopServices as fallback."""
        if not open_path(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_game_exit(self):
        if self._presence is not None:
            self._presence.stop()
            self._presence = None
        if self._keepalive is not None:
            self._keepalive.stop()
            self._keepalive = None
        self._finish_play_time()
        self.showNormal()
        elapsed = time.monotonic() - getattr(self, "_launch_started_at", 0.0)
        self._status_hold = False
        self._poll_status()
        self._refresh_account_ui()
        # A very short session usually means the game never really started
        # (Proton/asset error) or failed Steam auth immediately.
        if elapsed < 25 and not self.runner.user_stopped:
            box = QMessageBox(
                QMessageBox.Warning, "Game closed quickly",
                f"The game exited after about {elapsed:.0f} seconds.\n\n"
                "If no game window appeared, check the log at:\n"
                f"{GAME_LOG}\n\n"
                "The most common cause is that the native Steam client was not "
                "running and logged in — the game needs it to authenticate.",
                QMessageBox.Close, self,
            )
            open_btn = box.addButton("Open log", QMessageBox.ActionRole)
            box.exec()
            if box.clickedButton() is open_btn:
                self._open_host_path(GAME_LOG)

    # --- settings overlay ---

    def _on_server_changed(self):
        # Match prospect-og: a new server means a new identity provider.
        self.session_manager.invalidate_session()
        self.config.save()
        self._refresh_account_ui()
        # A different server may speak the other sign-in protocol.
        self.session_manager.protocol = Protocol.UNKNOWN
        run_worker(self.session_manager.detect_protocol,
                   on_finished=lambda _: self._refresh_account_ui())
        self.mod_manager.remote_mods = {}  # the previous server's list
        self._set_mods_attention(False)    # unknown until the next check
        run_worker(self.mod_manager.load_mods)
        self._status_hold = False
        self._poll_status()

    def open_settings_dialog(self):
        """The cog. Closing the overlay re-runs _refresh_account_ui (the game
        directory may have changed)."""
        self.overlay.open_section("game")
