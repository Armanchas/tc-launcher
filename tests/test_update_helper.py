"""The swap helper, exercised for real on Linux with a dummy 'app'."""

import os
import subprocess
import sys
import time

import pytest

from tclauncher import platforms, updater

HELPER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "scripts", "update-helper.sh")


@pytest.mark.skipif(platforms.IS_WINDOWS, reason="POSIX helper")
def test_helper_waits_for_the_pid_then_swaps_and_relaunches(tmp_path):
    old = tmp_path / "app"
    new = tmp_path / "app.new"
    marker = tmp_path / "relaunched"
    old.write_text("#!/bin/sh\nexit 0\n")
    new.write_text(f"#!/bin/sh\ntouch {marker}\n")
    old.chmod(0o755)
    new.chmod(0o755)

    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    subprocess.Popen(["sh", HELPER, str(victim.pid), str(old), str(new)])

    victim.wait()
    deadline = time.time() + 15
    while time.time() < deadline and not marker.exists():
        time.sleep(0.2)

    assert not new.exists(), "the downloaded file should have been moved, not copied"
    assert marker.exists(), "the helper did not relaunch the swapped binary"
    assert os.access(old, os.X_OK), "the swapped binary lost its exec bit"


@pytest.mark.skipif(platforms.IS_WINDOWS, reason="POSIX helper")
def test_helper_does_not_swap_while_the_pid_is_alive(tmp_path):
    old = tmp_path / "app"
    new = tmp_path / "app.new"
    old.write_text("old")
    new.write_text("new")

    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    helper = subprocess.Popen(["sh", HELPER, str(victim.pid), str(old), str(new)])
    time.sleep(1.0)
    assert old.read_text() == "old", "helper swapped before the app exited"
    # Without this, the test passes when the helper is absent or crashed --
    # nothing swapped either way. It was green during the RED run for exactly
    # that reason. Proving the helper is still ALIVE is what distinguishes
    # "correctly waiting" from "not running at all".
    assert helper.poll() is None, "the helper exited instead of waiting for the pid"
    victim.kill()
    victim.wait()
    helper.wait(timeout=15)


def test_helper_script_exists_for_this_platform():
    assert os.path.isfile(platforms.helper_script())


def test_update_swap_runs_a_copy_not_the_bundled_script(tmp_path, monkeypatch):
    """The bundle vanishes the moment the launcher exits -- a onefile _MEIPASS
    dir is deleted and an AppImage unmounts -- while the helper is still in its
    wait loop. So the helper must never be executed from inside the bundle.

    Running from source hides this: helper_script() then points at the repo's
    scripts/ dir, which is stable. Hence the fake bundle path here.
    """
    bundled = tmp_path / "bundle" / "update-helper.sh"
    bundled.parent.mkdir()
    bundled.write_text("#!/bin/sh\nexit 0\n")
    monkeypatch.setattr(platforms, "helper_script", lambda: str(bundled))

    spawned = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned["argv"] = argv

    monkeypatch.setattr(platforms.subprocess, "Popen", FakePopen)
    platforms.update_swap(123, "/old", "/new")

    assert str(bundled) not in spawned["argv"], "helper ran from inside the bundle"
    copies = [a for a in spawned["argv"]
              if a.endswith(os.path.basename(str(bundled)))]
    assert copies, "no helper path in the spawned argv"
    assert os.path.isfile(copies[0]), "the helper copy does not exist"


def test_download_streams_to_the_destination_and_reports_progress(tmp_path):
    chunks = [b"a" * 10, b"b" * 10]

    class R:
        headers = {"content-length": "20"}
        def raise_for_status(self): pass
        def iter_content(self, _n): return iter(chunks)

    seen = []
    dest = tmp_path / "out.bin"
    result = updater.download("https://example/x", str(dest),
                              on_progress=seen.append,
                              get=lambda *_a, **_kw: R())
    assert result == str(dest)
    assert dest.read_bytes() == b"a" * 10 + b"b" * 10
    assert seen and seen[-1] == 1.0


def test_download_of_an_empty_body_raises(tmp_path):
    class R:
        headers = {}
        def raise_for_status(self): pass
        def iter_content(self, _n): return iter([])

    with pytest.raises(RuntimeError, match="empty"):
        updater.download("https://example/x", str(tmp_path / "o.bin"),
                         get=lambda *_a, **_kw: R())


def test_windows_swap_prefixes_call_so_cmd_does_not_eat_the_quotes(monkeypatch):
    """tempfile puts the helper under %TEMP%, inside the user profile, so a
    username with a space makes list2cmdline quote the first token after /c.
    Per `cmd /?`, a line that begins with a quote and holds more than two has
    its leading and trailing quote stripped -- the command name then parses as
    'C:\\Users\\John' and the update silently does nothing. The unquoted
    `call` keeps the line from starting with a quote.
    """
    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    spawned = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned["argv"] = argv

    monkeypatch.setattr(platforms.subprocess, "Popen", FakePopen)
    platforms.update_swap(1, "C:\\a.exe", "C:\\b.new")
    assert spawned["argv"][:3] == ["cmd", "/c", "call"]


def test_download_of_a_truncated_body_raises(tmp_path):
    """Content-Length promises 20 bytes, the body delivers 10."""
    class R:
        headers = {"content-length": "20"}
        def raise_for_status(self): pass
        def iter_content(self, _n): return iter([b"a" * 10])

    with pytest.raises(RuntimeError, match="incomplete"):
        updater.download("https://example/x", str(tmp_path / "o.bin"),
                         get=lambda *_a, **_kw: R())
