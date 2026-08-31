import json
import os
import sys

from tclauncher.config import GAME_EXE_RELPATH, ConfigManager, default_game_dir


def _make_game(root):
    exe = os.path.join(root, GAME_EXE_RELPATH)
    os.makedirs(os.path.dirname(exe), exist_ok=True)
    open(exe, "w").close()


def test_adopts_the_launchers_own_folder_when_the_game_is_there(tmp_path, monkeypatch):
    _make_game(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert default_game_dir() == str(tmp_path)


def test_returns_empty_when_no_game_is_adjacent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert default_game_dir() == ""


def test_frozen_builds_use_the_executables_folder(tmp_path, monkeypatch):
    _make_game(str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "launcher.exe"))
    assert default_game_dir() == str(tmp_path)


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
    assert cfg.game_dir == str(game)
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
