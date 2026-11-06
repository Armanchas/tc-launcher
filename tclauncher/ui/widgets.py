"""Small custom widgets the stylesheet cannot express."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox

from .theme import ACCENT, BORDER, SURFACE_HI, TEXT, TEXT_DIM

_TRACK_W, _TRACK_H, _GAP = 36, 20, 10


class Switch(QCheckBox):
    """A toggle switch. Still a QCheckBox, so isChecked/setChecked/toggled
    and everything wired to them work unchanged; only the drawing differs.
    The whole widget, label included, is the click target."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        text_w = self.fontMetrics().horizontalAdvance(self.text())
        height = max(_TRACK_H + 4, self.fontMetrics().height() + 4)
        return QSize(_TRACK_W + (_GAP + text_w if self.text() else 0) + 2, height)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = self.isEnabled()
        on = self.isChecked()

        top = (self.height() - _TRACK_H) / 2
        track = QRectF(1, top, _TRACK_W, _TRACK_H)
        track_color = QColor(ACCENT if on else SURFACE_HI)
        if not enabled:
            track_color.setAlpha(90)
        painter.setPen(Qt.NoPen if on else QColor(BORDER))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, _TRACK_H / 2, _TRACK_H / 2)

        knob_d = _TRACK_H - 6
        knob_x = track.right() - knob_d - 3 if on else track.left() + 3
        knob = QColor("#04222a" if on else TEXT_DIM)
        if not enabled:
            knob.setAlpha(120)
        painter.setPen(Qt.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(QRectF(knob_x, top + 3, knob_d, knob_d))

        if self.text():
            color = QColor(TEXT if enabled else "#5c636d")
            painter.setPen(color)
            text_rect = self.rect().adjusted(_TRACK_W + _GAP + 1, 0, 0, 0)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
