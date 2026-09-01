import json
import os
import sys

from tclauncher.config import GAME_EXE_RELPATH, ConfigManager, default_game_dir


def _same(a, b):
    """Compare two directory paths for identity, not for string equality.

    default_game_dir() reads os.getcwd(), and GitHub's Windows runners set
    TEMP to the 8.3 short form (C:\\Users\\RUNNER~1\\...) that pytest builds
    tmp_path from, while SetCurrentDirectory canonicalises to the long form.
    The two name the same directory and must compare equal. realpath() is the
    identity on Linux, so this is a no-op there.

    The empty check is load-bearing: os.path.realpath("") is the cwd, and these
    tests chdir INTO the directory under test -- so a regression that made
    default_game_dir() return "" would otherwise compare equal and pass.
    """
    return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)


def _make_game(root):
    exe = os.path.join(root, GAME_EXE_RELPATH)
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    open(exe, "w").close()


def test_adopts_the_launchers_own_folder_when_the_game_is_there(tmp_path, monkeypatch):
    _make_game(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert _same(default_game_dir(), tmp_path)


def test_returns_empty_when_no_game_is_adjacent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert default_game_dir() == ""


def test_frozen_builds_use_the_executables_folder(tmp_path, monkeypatch):
    _make_game(str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "launcher.exe"))
    assert _same(default_game_dir(), tmp_path)


def test_a_frozen_build_with_no_executable_adopts_nothing(tmp_path, monkeypatch):
    """os.path.abspath("") is the cwd, so a naive dirname() hands back the cwd's
    PARENT -- silently adopting a folder the player never chose. Plant a game in
    that parent so this test fails if the guard ever stops working."""
    _make_game(str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "")
    assert default_game_dir() == ""


def test_a_first_run_with_no_config_adopts_an_adjacent_game(tmp_path, monkeypatch):
    """The Windows first-run case: launcher unzipped into the Release folder,
    no config.json yet. Auto-detect must fire on THIS run, not the next one."""
    game = tmp_path / "Release"
    _make_game(str(game))
    monkeypatch.chdir(game)
    monkeypatch.delattr(sys, "frozen", raising=False)
    cfg = ConfigManager(config_file=str(tmp_path / "cfg" / "config.json"))
    cfg.load()
    assert _same(cfg.game_dir, game)
    assert cfg.has_valid_game_dir()


def test_an_explicit_game_dir_is_never_overwritten(tmp_path, monkeypatch):
    game = tmp_path / "Release"
    _make_game(str(game))
    monkeypatch.chdir(game)
    monkeypatch.delattr(sys, "frozen", raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"game_dir": "/somewhere/else"}))
    cfg = ConfigManager(config_file=str(path))
    cfg.load()
    assert cfg.game_dir == "/somewhere/else"
