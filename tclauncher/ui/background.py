"""The main window's background art, painted to cover the window.

Swapping the art means replacing assets/background.png. The whole assets
directory ships in both builds (the --add-data lines in scripts/build-*), and
tests/test_background.py fails if either script stops shipping it.
"""

import os

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from .theme import BG

BACKGROUND_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "background.png"
)

#: Fraction of the height, from the bottom, that the readability gradient covers.
_SHADE = 0.45


def cover_rect(image: QSize, target: QSize) -> QRect:
    """The part of `image` to draw into `target`: aspect kept, centred, and
    filling the target completely (CSS `background-size: cover`)."""
    if image.isEmpty() or target.isEmpty():
        return QRect()
    scale = max(target.width() / image.width(), target.height() / image.height())
    w = min(image.width(), int(target.width() / scale))
    h = min(image.height(), int(target.height() / scale))
    return QRect((image.width() - w) // 2, (image.height() - h) // 2, w, h)


class ArtBackground(QWidget):
    def __init__(self, path: str = BACKGROUND_PATH, parent: QWidget | None = None):
        super().__init__(parent)
        self._image = QImage(path) if os.path.isfile(path) else QImage()
        self._cache: QPixmap | None = None
        self._cache_size = QSize()

    def has_art(self) -> bool:
        return not self._image.isNull()

    def _scaled(self) -> QPixmap:
        if self._cache is None or self._cache_size != self.size():
            source = self._image.copy(cover_rect(self._image.size(), self.size()))
            self._cache = QPixmap.fromImage(source.scaled(
                self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            self._cache_size = QSize(self.size())
        return self._cache

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self.has_art() or self.width() <= 0 or self.height() <= 0:
            painter.fillRect(self.rect(), QColor(BG))
            return
        painter.drawPixmap(0, 0, self._scaled())
        top = int(self.height() * (1 - _SHADE))
        gradient = QLinearGradient(0, top, 0, self.height())
        gradient.setColorAt(0.0, QColor(5, 8, 15, 0))
        gradient.setColorAt(1.0, QColor(5, 8, 15, 217))
        painter.fillRect(QRect(0, top, self.width(), self.height() - top), gradient)
