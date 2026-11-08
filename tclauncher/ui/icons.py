"""The UI's icons: bundled Lucide SVGs (assets/icons, ISC licence) tinted per
use. An emoji or symbol character would be drawn by whatever font the player's
system has, so it looks different on every machine; these look the same.

The GitHub and Discord logos come from simple-icons (CC0), since Lucide has no
Discord; their files gain fill="currentColor" so they tint like the rest.

Adding one: copy its SVG from lucide-static into assets/icons, add a name here.
"""

import logging
import os
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton

logger = logging.getLogger(__name__)

ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icons"
)

COG = "settings"
CLOSE = "x"
USER = "user"
WARNING = "triangle-alert"
UPDATE = "circle-arrow-up"
DOT = "circle"
HEART = "heart"
WEBSITE = "globe"
LOG_FILE = "file-text"
FOLDER = "folder-open"
GITHUB = "github"
DISCORD = "discord"
NAMES = (COG, CLOSE, USER, WARNING, UPDATE, DOT, HEART, WEBSITE, LOG_FILE, FOLDER, GITHUB, DISCORD)

#: The team's logo: artwork, so not tinted and not in NAMES.
LOGO = "tc-logo"

#: Blank pixels between a button's icon and its text. QSS has no property for
#: it, so apply() paints them as part of the icon.
TEXT_GAP = 6


@lru_cache(maxsize=None)
def _svg(name: str) -> str:
    try:
        with open(os.path.join(ICON_DIR, f"{name}.svg"), encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        # A blank button still works; a crash at startup would not.
        logger.warning("Missing icon %s: %s", name, e)
        return ""


def paint(painter: QPainter, name: str, color: str, rect: QRectF, filled: bool = False):
    svg = _svg(name).replace("currentColor", color)
    if filled:
        svg = svg.replace('fill="none"', f'fill="{color}"', 1)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if renderer.isValid():
        renderer.render(painter, rect)


def _render(name: str, color: str, size: int, scale: int, filled: bool, gap: int = 0) -> QPixmap:
    out = QPixmap((size + gap) * scale, size * scale)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    paint(painter, name, color, QRectF(0, 0, size * scale, size * scale), filled)
    painter.end()
    out.setDevicePixelRatio(scale)
    return out


def pixmap(name: str, color: str, size: int, filled: bool = False, scale: int = 2) -> QPixmap:
    """For a QLabel; `size` is in logical pixels."""
    return _render(name, color, size, scale, filled)


def icon(name: str, color: str, size: int, filled: bool = False, gap: int = 0) -> QIcon:
    """For a button: sharp at 100%, 200% and 300% display scaling."""
    out = QIcon()
    for scale in (1, 2, 3):
        out.addPixmap(_render(name, color, size, scale, filled, gap))
    return out


def apply(button: QAbstractButton, name: str, color: str, size: int, filled: bool = False):
    gap = TEXT_GAP if button.text() else 0
    button.setIcon(icon(name, color, size, filled, gap))
    button.setIconSize(QSize(size + gap, size))
