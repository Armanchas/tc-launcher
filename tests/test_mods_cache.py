"""The downloaded-archive cache (~/.tclauncher/mods) keeps only what can still
be used: the archive of each mod's current version, for a repair without a
re-download. Every tc_update release used to leave another ~100 MB behind."""

import json

from tclauncher.config import ConfigManager
from tclauncher.mods import ModManager, ModStatus

M1 = {"id": "antykoko/tc_update", "name": "M", "author": "a", "version": "1.3.9",
      "hash": "h", "integrity": "i", "url": "u"}


class _StubBackend:
    def __init__(self, remote):
        self.remote = remote

    def remote_mods(self):
        return self.remote


def _manager(tmp_path, monkeypatch, remote, installed=None):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    game = tmp_path / "game"
    game.mkdir()
    (game / "mods.json").write_text(json.dumps(installed or {}))
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    config.game_dir = str(game)
    manager = ModManager(config, _StubBackend(remote))
    cache = tmp_path / "user" / "mods"
    return manager, cache


def _touch(cache, *names):
    for name in names:
        (cache / name).write_bytes(b"zip")


def test_load_mods_drops_archives_of_old_versions(tmp_path, monkeypatch):
    manager, cache = _manager(tmp_path, monkeypatch, {M1["id"]: M1},
                              {M1["id"]: {**M1, "files": []}})
    _touch(cache, "antykoko_tc_update_1.3.5.zip", "antykoko_tc_update_1.3.8.zip",
           "antykoko_tc_update_1.3.9.zip", "other_server_mod_2.0.zip")
    assert manager.load_mods() is True
    assert sorted(p.name for p in cache.iterdir()) == ["antykoko_tc_update_1.3.9.zip"]


def test_current_version_is_kept_before_it_is_installed(tmp_path, monkeypatch):
    """A download the install never finished extracting is still the one to use."""
    manager, cache = _manager(tmp_path, monkeypatch, {M1["id"]: M1})
    _touch(cache, "antykoko_tc_update_1.3.9.zip")
    manager.load_mods()
    assert (cache / "antykoko_tc_update_1.3.9.zip").exists()


def test_failed_fetch_deletes_nothing(tmp_path, monkeypatch):
    manager, cache = _manager(tmp_path, monkeypatch, None, {M1["id"]: {**M1, "files": []}})
    _touch(cache, "antykoko_tc_update_1.3.5.zip")
    assert manager.load_mods() is False
    assert (cache / "antykoko_tc_update_1.3.5.zip").exists()


def test_only_zip_archives_are_touched(tmp_path, monkeypatch):
    manager, cache = _manager(tmp_path, monkeypatch, {})
    _touch(cache, "notes.txt")
    (cache / "sub.zip").mkdir()
    manager.load_mods()
    assert (cache / "notes.txt").exists()
    assert (cache / "sub.zip").is_dir()


def test_a_file_that_cannot_be_deleted_never_breaks_load_mods(tmp_path, monkeypatch):
    manager, cache = _manager(tmp_path, monkeypatch, {})
    _touch(cache, "old_1.zip")

    def refuse(path):
        raise PermissionError("in use")

    monkeypatch.setattr("tclauncher.mods.os.remove", refuse)
    assert manager.load_mods() is True


def test_install_drops_the_previous_version(tmp_path, monkeypatch):
    old = {**M1, "version": "1.3.8"}
    manager, cache = _manager(tmp_path, monkeypatch, {M1["id"]: M1},
                              {M1["id"]: {**old, "files": []}})
    manager.load_mods()  # 1.3.8 is still installed, so its archive stays for now
    _touch(cache, "antykoko_tc_update_1.3.8.zip")
    monkeypatch.setattr(manager, "_download_and_extract", lambda remote, cb=None: [])
    mod = next(m for m in manager.get_mods_with_statuses(refresh=False))
    assert mod.status is ModStatus.UPDATE_AVAILABLE
    _touch(cache, "antykoko_tc_update_1.3.9.zip")
    manager.install_mod(mod)
    assert sorted(p.name for p in cache.iterdir()) == ["antykoko_tc_update_1.3.9.zip"]
