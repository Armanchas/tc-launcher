import sys

from tclauncher import platforms
from tclauncher.config import ConfigManager


def test_is_windows_matches_the_interpreter():
    assert platforms.IS_WINDOWS == sys.platform.startswith("win")


def test_get_runner_returns_a_runner_with_the_full_contract(tmp_path):
    config = ConfigManager(config_file=str(tmp_path / "config.json"))
    runner = platforms.get_runner(config)
    for name in ("is_running", "launch", "stop", "write_steam_appid",
                 "build_command", "needs_first_run_setup"):
        assert callable(getattr(runner, name)), f"runner is missing {name}()"
    assert runner.user_stopped is False


def test_runner_module_matches_the_platform():
    expected = "runner_windows" if platforms.IS_WINDOWS else "runner_linux"
    assert platforms._runner_module().__name__.endswith(expected)
