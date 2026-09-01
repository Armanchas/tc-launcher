from tclauncher import platforms
from tclauncher.config import ConfigManager
from tclauncher.presence import start_presence


def _config(tmp_path, **kw):
    c = ConfigManager(str(tmp_path / "config.json"))
    c.wine_prefix = str(tmp_path / "prefix")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_disabled_by_setting_starts_nothing(tmp_path):
    c = _config(tmp_path, discord_presence=False, discord_client_id="123")
    assert start_presence(c) is None


def test_missing_client_id_starts_nothing(tmp_path):
    c = _config(tmp_path, discord_presence=True, discord_client_id="")
    assert start_presence(c) is None


def test_enabled_with_client_id_starts_a_session(tmp_path):
    c = _config(tmp_path, discord_presence=True, discord_client_id="123")
    session = start_presence(c)
    assert session is not None
    try:
        # The session must tail the log this platform actually writes, not a
        # default. On Linux that is inside the configured wine prefix; Windows
        # has no prefix and resolves under %LOCALAPPDATA%, so the prefix half
        # of the assertion is Linux-only.
        assert session.log_path == platforms.game_log_path(c)
        if not platforms.IS_WINDOWS:
            assert session.log_path.startswith(str(tmp_path / "prefix"))
    finally:
        session.stop()


def test_start_never_raises_when_discord_is_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "empty"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "empty"))
    c = _config(tmp_path, discord_presence=True, discord_client_id="123")
    session = start_presence(c)
    assert session is not None
    session.stop()
