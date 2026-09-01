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


# --- Steam detection, after the first tester round -------------------------
# The tester's game.log said "Steam running: NO" while the game itself logged
# "[AppId: 480] Client API initialized 1" -- which only happens against a live
# Steam client. The process-list check was wrong, so detection moved to the
# registry key Steam maintains and Steamworks reads.

def test_steam_is_running_when_the_active_process_pid_is_set(monkeypatch):
    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 4242 if name == "pid" else 0)
    assert runner_windows.is_steam_running() is True


def test_steam_is_not_running_when_the_active_process_pid_is_zero(monkeypatch):
    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 0)
    assert runner_windows.is_steam_running() is False


def test_a_signed_out_steam_is_reported_differently_from_a_stopped_one(monkeypatch):
    """The tester's Steam was RUNNING but produced no auth ticket. 'Steam is
    not running' sent them looking in the wrong place; these must not read the
    same."""
    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 4242 if name == "pid" else 0)
    signed_out = runner_windows.steam_preflight_issue()

    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 0)
    stopped = runner_windows.steam_preflight_issue()

    assert signed_out and stopped and signed_out != stopped
    assert "does not appear to be running" in stopped.lower()
    assert "signed in" in signed_out.lower() or "logged in" in signed_out.lower()


def test_no_warning_when_steam_is_running_and_signed_in(monkeypatch):
    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 4242 if name == "pid" else 76561190000000000)
    assert runner_windows.steam_preflight_issue() is None


def test_diagnostics_report_the_signed_in_state(monkeypatch, tmp_path):
    """'Steam running: yes' alone could not distinguish the tester's failure."""
    monkeypatch.setattr(runner_windows, "_steam_registry_dword",
                        lambda name: 4242 if name == "pid" else 0)
    lines = "\n".join(runner_windows.diagnostic_lines({}, str(tmp_path)))
    assert "Steam signed in:" in lines
    assert "NO" in lines.split("Steam signed in:")[1].splitlines()[0]
