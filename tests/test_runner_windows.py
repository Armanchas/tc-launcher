"""The Windows runner, exercised on Linux via build_command()."""

import os

import pytest

from tclauncher import runner_windows
from tclauncher.config import ConfigManager

BACKEND = {"backend_game": "http://game.example", "steam_auth": "http://auth.example",
           "analytics": "http://an.example", "backend_api": "http://api.example"}


def _config(tmp_path, **kw):
    c = ConfigManager(config_file=str(tmp_path / "config.json"))
    c.backend_data = BACKEND
    exe = tmp_path / "Release" / "Prospect" / "Binaries" / "Win64"
    exe.mkdir(parents=True)
    (exe / "Prospect-Win64-Shipping.exe").write_text("")
    c.game_dir = str(tmp_path / "Release")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_argv_matches_prospect_og_exactly(tmp_path):
    argv, _env = runner_windows.GameRunner(_config(tmp_path)).build_command()
    assert argv[0].endswith("Prospect-Win64-Shipping.exe")
    assert argv[1:] == [
        "-backend", "http://game.example",
        "-steam_auth", "http://auth.example",
        "-analytics", "http://an.example",
    ]


def test_run_args_are_appended(tmp_path):
    config = _config(tmp_path, run_args=["-windowed", "-log"])
    argv, _ = runner_windows.GameRunner(config).build_command()
    assert argv[-2:] == ["-windowed", "-log"]


def test_no_steam_env_vars_are_invented(tmp_path):
    """prospect-og sets none; steam_appid.txt is the mechanism on Windows."""
    config = _config(tmp_path)
    _argv, env = runner_windows.GameRunner(config).build_command()
    # Exact equality, not key-presence: the environment must be os.environ plus
    # the user's own vars and nothing else. A weaker check passes on a machine
    # that already exports WINEPREFIX or STEAM_* -- i.e. this project's author.
    assert env == {**os.environ, **config.env_vars}


def test_user_env_vars_are_applied(tmp_path):
    config = _config(tmp_path, env_vars={"MY_VAR": "1"})
    _argv, env = runner_windows.GameRunner(config).build_command()
    assert env["MY_VAR"] == "1"


def test_missing_server_raises_a_precise_error(tmp_path):
    config = _config(tmp_path)
    config.backend_data = None
    with pytest.raises(RuntimeError, match="No server selected"):
        runner_windows.GameRunner(config).build_command()


def test_missing_game_dir_raises_a_precise_error(tmp_path):
    config = _config(tmp_path)
    config.game_dir = str(tmp_path / "nope")
    with pytest.raises(RuntimeError, match="Game directory"):
        runner_windows.GameRunner(config).build_command()


def test_write_steam_appid_writes_480_next_to_the_exe(tmp_path):
    config = _config(tmp_path)
    runner_windows.GameRunner(config).write_steam_appid()
    written = os.path.join(os.path.dirname(config.game_exe()), "steam_appid.txt")
    assert open(written).read().strip() == "480"


def test_first_run_setup_is_never_needed_on_windows(tmp_path):
    assert runner_windows.GameRunner(_config(tmp_path)).needs_first_run_setup() is False


def test_is_running_is_false_before_launch(tmp_path):
    r = runner_windows.GameRunner(_config(tmp_path))
    assert r.is_running() is False
    assert r.user_stopped is False


def test_stop_before_launch_is_a_no_op(tmp_path):
    runner_windows.GameRunner(_config(tmp_path)).stop()  # must not raise


def test_diagnostic_lines_mention_steam_appid_and_never_raise(tmp_path):
    lines = runner_windows.diagnostic_lines({}, str(tmp_path))
    blob = "\n".join(lines)
    assert "steam_appid.txt" in blob
    assert "Steam login" in blob
