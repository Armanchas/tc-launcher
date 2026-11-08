"""The UI's icons are bundled SVGs, so they look the same on every machine.
An emoji or symbol character is drawn by whatever font the system has."""

import ast
import os
import re

import pytest
from PySide6.QtGui import QColor

from tclauncher.ui import icons

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "tclauncher", "ui")

SYMBOLS = re.compile("[←-⯿\U0001f000-\U0001faff]")


def _painted(image, color: QColor) -> int:
    """How many pixels are visibly `color`."""
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() > 200 and abs(pixel.red() - color.red()) < 10 \
                    and abs(pixel.green() - color.green()) < 10 \
                    and abs(pixel.blue() - color.blue()) < 10:
                count += 1
    return count


@pytest.mark.parametrize("name", icons.NAMES)
def test_every_icon_renders_in_the_requested_colour(qapp, name):
    color = QColor("#f1c40f")
    assert _painted(icons.pixmap(name, color.name(), 32).toImage(), color) > 0


def test_filled_paints_the_inside(qapp):
    color = QColor("#2ecc71")
    outline = _painted(icons.pixmap(icons.DOT, color.name(), 32).toImage(), color)
    filled = _painted(icons.pixmap(icons.DOT, color.name(), 32, filled=True).toImage(), color)
    assert filled > outline * 2


def test_icon_is_usable_on_a_button(qapp):
    assert not icons.icon(icons.COG, "#ffffff", 22).isNull()


def test_a_missing_file_gives_a_blank_icon_not_a_crash(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(icons, "ICON_DIR", str(tmp_path))
    icons._svg.cache_clear()
    try:
        assert _painted(icons.pixmap(icons.COG, "#ffffff", 32).toImage(), QColor("#ffffff")) == 0
    finally:
        icons._svg.cache_clear()


def test_the_license_ships_with_the_icons():
    assert os.path.isfile(os.path.join(icons.ICON_DIR, "LICENSE"))


def test_source_installs_include_the_icons():
    """package-data's assets/* does not reach into a subdirectory."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        assert "assets/icons/*" in f.read()


def test_the_ui_has_no_symbol_characters_left():
    """ast, not grep: it sees "\\U0001F464" as the character it is."""
    found = []
    for name in os.listdir(UI_DIR):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(UI_DIR, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and SYMBOLS.search(node.value):
                found.append((name, node.lineno, node.value[:40]))
    assert found == []


def test_the_logo_keeps_its_own_colours_and_renders(qapp):
    """It is artwork with embedded images, unlike the tinted icons."""
    image = icons.pixmap(icons.LOGO, "#ffffff", 96).toImage()
    opaque = sum(image.pixelColor(x, y).alpha() > 200
                 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2))
    assert opaque > 100
    assert _painted(image, QColor("#ffffff")) < opaque


def test_a_button_with_text_reserves_a_gap_after_its_icon(qapp):
    from PySide6.QtWidgets import QPushButton

    labelled = QPushButton("GitHub")
    icons.apply(labelled, icons.GITHUB, "#ffffff", 18)
    assert labelled.iconSize().width() == 18 + icons.TEXT_GAP
    assert labelled.iconSize().height() == 18
    size = labelled.iconSize()
    drawn = labelled.icon().pixmap(size).toImage()
    assert drawn.width() == size.width()
    # the gap is empty: nothing is painted in the last columns
    assert all(drawn.pixelColor(drawn.width() - 1 - i, y).alpha() == 0
               for i in range(icons.TEXT_GAP - 1) for y in range(drawn.height()))


def test_an_icon_only_button_stays_centred(qapp):
    from PySide6.QtWidgets import QPushButton

    bare = QPushButton()
    icons.apply(bare, icons.COG, "#ffffff", 24)
    assert bare.iconSize().width() == 24
