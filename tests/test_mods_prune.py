"""load_mods prunes mods the server stopped offering -- but never on a failed fetch.

A failed /launcher/mods request used to come back as {}, which load_mods read
as "the server offers nothing" and deleted every installed mod's files.
"""

import json

import requests

from tclauncher.backend import BackendClient
from tclauncher.config import ConfigManager
from tclauncher.mods import ModManager


class _StubBackend:
    def __init__(self, remote):
        self.remote = remote

    def remote_mods(self):
        return self.remote


def _installed(tmp_path):
    game = tmp_path / "game"
    (game / "Prospect").mkdir(parents=True)
    pak = game / "Prospect" / "mod.pak"
    pak.write_bytes(b"pak")
    record = {"m1": {"id": "m1", "name": "M", "author": "a", "version": "1",
                     "hash": "h", "integrity": "i", "url": "u",
                     "files": ["Prospect/mod.pak"]}}
    (game / "mods.json").write_text(json.dumps(record))
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    config.game_dir = str(game)
    return config, pak, game / "mods.json", record


def test_failed_fetch_prunes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    config, pak, record_file, record = _installed(tmp_path)
    manager = ModManager(config, _StubBackend(None))
    manager.load_mods()
    assert pak.exists()
    assert json.loads(record_file.read_text()) == record
    assert manager.installed_mods == record


def test_failed_fetch_keeps_the_last_known_remote_list(tmp_path, monkeypatch):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    config, pak, _, record = _installed(tmp_path)
    backend = _StubBackend({"m1": {k: v for k, v in record["m1"].items() if k != "files"}})
    manager = ModManager(config, backend)
    manager.load_mods()
    backend.remote = None
    manager.load_mods()
    assert set(manager.remote_mods) == {"m1"}
    assert pak.exists()


def test_successful_empty_list_still_prunes(tmp_path, monkeypatch):
    """A server that really offers no mods: uninstalling is correct."""
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    config, pak, record_file, _ = _installed(tmp_path)
    manager = ModManager(config, _StubBackend({}))
    manager.load_mods()
    assert not pak.exists()
    assert json.loads(record_file.read_text()) == {}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_remote_mods_returns_none_on_failure(tmp_path, monkeypatch):
    client = BackendClient(ConfigManager(config_file=str(tmp_path / "c.json")))
    client.config.server_discovery_addr = "http://server"

    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(client.session, "get", boom)
    assert client.remote_mods() is None

    for payload in (ValueError("not json"), {"success": False}, {"items": []}):
        monkeypatch.setattr(client.session, "get", lambda *a, p=payload, **k: _FakeResponse(p))
        assert client.remote_mods() is None


def test_remote_mods_returns_items_on_success(tmp_path, monkeypatch):
    client = BackendClient(ConfigManager(config_file=str(tmp_path / "c.json")))
    client.config.server_discovery_addr = "http://server"
    monkeypatch.setattr(client.session, "get",
                        lambda *a, **k: _FakeResponse({"success": True, "items": {}}))
    assert client.remote_mods() == {}


def test_load_mods_reports_whether_the_fetch_worked(tmp_path, monkeypatch):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    config, _, _, _ = _installed(tmp_path)
    backend = _StubBackend(None)
    manager = ModManager(config, backend)
    assert manager.load_mods() is False
    backend.remote = {}
    assert manager.load_mods() is True


def test_are_mods_installed_can_reuse_the_list_just_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    config, _, _, _ = _installed(tmp_path)
    backend = _StubBackend({})
    manager = ModManager(config, backend)
    manager.load_mods()
    backend.remote = None  # a second fetch would fail; refresh=False must not make one
    calls = []
    monkeypatch.setattr(backend, "remote_mods", lambda: calls.append(1))
    assert manager.are_mods_installed(refresh=False) is True
    assert calls == []
