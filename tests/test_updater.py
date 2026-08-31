import os
import sys

import pytest

from tclauncher import updater

RELEASE = {
    "tag_name": "v1.0.9",
    "body": "Fixes things.",
    "assets": [
        {"name": "launcher.exe",
         "browser_download_url": "https://example/launcher.exe"},
        {"name": "TCLauncher-1.0.9-x86_64.AppImage",
         "browser_download_url": "https://example/app.AppImage"},
    ],
}


def _get(_url, **_kw):
    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return RELEASE
    return R()


def test_version_parsing_handles_tags_and_prereleases():
    assert updater.parse_version("v1.0.9") == (1, 0, 9)
    assert updater.parse_version("1.0.9") == (1, 0, 9)
    assert updater.parse_version("v1.0.9-beta.2") == (1, 0, 9)
    assert updater.parse_version("") == ()


def test_is_newer_compares_numerically_not_lexically():
    assert updater.is_newer("1.0.10", "1.0.9") is True     # would fail as strings
    assert updater.is_newer("1.0.9", "1.0.9") is False
    assert updater.is_newer("1.0.8", "1.0.9") is False
    assert updater.is_newer("v1.1.0", "1.0.99") is True
    assert updater.is_newer("2.0", "1.9.9") is True


def test_unparseable_remote_version_is_never_newer():
    assert updater.is_newer("garbage", "1.0.7") is False


def test_check_returns_the_platform_asset():
    info = updater.check("1.0.7", "owner/repo", get=_get)
    assert info is not None
    assert info.version == "1.0.9"
    assert info.notes == "Fixes things."
    expected = "launcher.exe" if updater.IS_WINDOWS else "app.AppImage"
    assert info.url.endswith(expected)


def test_check_returns_none_when_current():
    assert updater.check("1.0.9", "owner/repo", get=_get) is None


def test_check_returns_none_when_the_network_fails():
    def boom(*_a, **_kw):
        raise OSError("no network")
    assert updater.check("1.0.7", "owner/repo", get=boom) is None


def test_check_returns_none_when_the_release_has_no_matching_asset():
    def _get_empty(_url, **_kw):
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"tag_name": "v2.0.0", "body": "", "assets": []}
        return R()
    assert updater.check("1.0.7", "owner/repo", get=_get_empty) is None


def test_bundle_path_is_none_when_running_from_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert updater.running_bundle_path() is None


@pytest.mark.skipif(updater.IS_WINDOWS, reason="$APPIMAGE is a Linux-only concept")
def test_linux_bundle_path_is_appimage_not_sys_executable(monkeypatch):
    """sys.executable points inside the mounted squashfs; $APPIMAGE is the file."""
    monkeypatch.setenv("APPIMAGE", "/home/u/Apps/TCLauncher.AppImage")
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_abc/usr/bin/python")
    assert updater.running_bundle_path() == "/home/u/Apps/TCLauncher.AppImage"


def test_can_replace_is_true_for_a_writable_directory(tmp_path):
    target = tmp_path / "launcher.exe"
    target.write_text("x")
    assert updater.can_replace(str(target)) is True


@pytest.mark.skipif(
    updater.IS_WINDOWS,
    reason="POSIX directory permissions; os.chmod cannot make a directory "
           "unwritable on Windows, so os.access(dir, W_OK) stays True")
def test_can_replace_is_false_for_an_unwritable_directory(tmp_path):
    target = tmp_path / "sub" / "launcher.exe"
    target.parent.mkdir()
    target.write_text("x")
    os.chmod(target.parent, 0o500)
    try:
        assert updater.can_replace(str(target)) is False
    finally:
        os.chmod(target.parent, 0o700)


def test_can_replace_is_false_for_a_missing_directory():
    assert updater.can_replace("/nonexistent/dir/launcher.exe") is False


@pytest.mark.parametrize("payload", [
    {"tag_name": None, "assets": []},
    {"tag_name": "v9.9.9", "assets": [{"name": "launcher.exe"},
                                      {"name": "x.AppImage"}]},
    {"tag_name": "v9.9.9", "assets": None},
    [1, 2, 3],
    None,
])
def test_check_never_raises_on_a_malformed_release_payload(payload):
    """check() is wired into the GUI, so its contract is that it can only ever
    return None -- whatever GitHub sends back.

    Each payload here raises a DIFFERENT exception if the try block is narrowed
    to cover only the HTTP call: AttributeError on a null tag_name, KeyError on
    an asset with no browser_download_url, TypeError on a null assets list,
    AttributeError on a non-object body.
    """
    def _get(_url, **_kw):
        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return payload
        return R()

    assert updater.check("1.0.7", "owner/repo", get=_get) is None
