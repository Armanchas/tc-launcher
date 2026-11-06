"""The main window's art: cover cropping, the missing-file fallback, and that
the image actually ships in both builds (CLAUDE.md gotcha 3: from source the
file always exists, so only a guard on the build scripts catches a build that
drops it)."""

import os

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from tclauncher.ui.background import BACKGROUND_PATH, ArtBackground, cover_rect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_cover_rect_keeps_aspect_and_centres():
    rect = cover_rect(QSize(1000, 561), QSize(1100, 620))
    assert abs(rect.width() / rect.height() - 1100 / 620) < 0.01
    assert rect.left() >= 0 and rect.top() >= 0
    assert rect.right() < 1000 and rect.bottom() < 561
    assert abs(rect.left() - (1000 - rect.right() - 1)) <= 1
    assert abs(rect.top() - (561 - rect.bottom() - 1)) <= 1


def test_cover_rect_square_into_wide():
    rect = cover_rect(QSize(1000, 1000), QSize(1600, 900))
    assert rect.width() == 1000
    assert rect.height() == 562
    assert rect.top() == 219


def test_cover_rect_degenerate_sizes():
    assert cover_rect(QSize(0, 0), QSize(100, 100)).isEmpty()
    assert cover_rect(QSize(100, 100), QSize(0, 0)).isEmpty()


def test_missing_background_falls_back(qapp, tmp_path):
    widget = ArtBackground(str(tmp_path / "nope.png"))
    widget.resize(400, 300)
    assert not widget.has_art()
    assert not widget.grab().isNull()


def test_bundled_background_is_readable(qapp):
    image = QImage(BACKGROUND_PATH)
    assert not image.isNull()
    assert image.width() >= 1000
    widget = ArtBackground()
    widget.resize(1100, 620)
    assert widget.has_art()
    assert not widget.grab().isNull()


def test_background_lives_in_the_bundled_assets_dir():
    assert os.path.dirname(BACKGROUND_PATH) == os.path.join(ROOT, "tclauncher", "assets")


def test_build_scripts_bundle_assets():
    with open(os.path.join(ROOT, "scripts", "build-appimage.sh"), encoding="utf-8") as f:
        assert '--add-data "$(pwd)/tclauncher/assets:tclauncher/assets"' in f.read()
    with open(os.path.join(ROOT, "scripts", "build-windows.ps1"), encoding="utf-8") as f:
        assert '--add-data "$root\\tclauncher\\assets;tclauncher\\assets"' in f.read()


def test_top_left_is_shaded_for_the_wordmark(qapp, tmp_path):
    """Bright art must not wash out the white wordmark: the corner behind it
    is darkened, the opposite corner is left alone."""
    white = QImage(400, 225, QImage.Format_RGB32)
    white.fill(0xFFFFFF)
    path = str(tmp_path / "white.png")
    white.save(path)
    widget = ArtBackground(path)
    widget.resize(1100, 620)
    shot = widget.grab().toImage()
    behind_title = shot.pixelColor(60, 40).lightness()
    top_right = shot.pixelColor(1060, 40).lightness()
    assert behind_title < 140
    assert top_right > 240
