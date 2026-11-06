"""The mod attestation digest the new server stores on the launcher's session."""

from xxhash import xxh128

from tclauncher.config import ConfigManager
from tclauncher.mods import ModManager
from tclauncher.verify import manifest_digest


def _manager(tmp_path, monkeypatch, remote):
    monkeypatch.setattr("tclauncher.mods.LAUNCHER_USERDIR", str(tmp_path / "user"))
    manager = ModManager(ConfigManager(config_file=str(tmp_path / "c.json")), backend=None)
    manager.remote_mods = remote
    return manager


def _remote(integrity):
    return {"id": "x", "name": "n", "author": "a", "version": "1", "hash": "h",
            "integrity": integrity, "url": "u"}


def test_manifest_digest_is_order_independent_and_nul_separated():
    expected = xxh128()
    for mod_id, value in sorted({"b": "2", "a": "1"}.items()):
        expected.update(mod_id.encode())
        expected.update(b"\0")
        expected.update(value.encode())
        expected.update(b"\0")
    assert manifest_digest({"b": "2", "a": "1"}) == expected.hexdigest()
    assert manifest_digest({"a": "1", "b": "2"}) == manifest_digest({"b": "2", "a": "1"})


def test_swapping_values_changes_digest():
    assert manifest_digest({"a": "1", "b": "2"}) != manifest_digest({"a": "2", "b": "1"})


def test_attestation_skips_mods_without_integrity(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, {"m1": _remote("x"), "m2": _remote("")})
    assert manager.attestation() == manifest_digest({"m1": "x"})


def test_attestation_with_no_offered_mods_is_digest_of_empty(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch, {})
    assert manager.attestation() == manifest_digest({})
