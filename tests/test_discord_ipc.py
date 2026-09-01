import json
import os
import socket
import struct
import threading

import pytest

from tclauncher import platforms
from tclauncher.discord_ipc import DiscordIPC

# This file drives the real Linux endpoint: it binds an AF_UNIX socket inside a
# monkeypatched XDG_RUNTIME_DIR and asserts open_discord_ipc() finds and speaks
# to it. The Windows endpoint is a named pipe under \\.\pipe\, which has no
# equivalent redirection -- pointing the probe at a temp directory does nothing,
# so these tests would either fail or reach a live Discord. The framing they
# also cover is asserted platform-neutrally in test_discord_ipc_transport.py
# against a fake connection.
pytestmark = pytest.mark.skipif(
    platforms.IS_WINDOWS,
    reason="binds a real AF_UNIX Discord socket in XDG_RUNTIME_DIR; the "
           "Windows endpoint is a named pipe that cannot be redirected",
)


def _serve(path, captured, replies=2):
    """Minimal fake Discord: accept one client, echo back a frame per request."""
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        for _ in range(replies):
            header = conn.recv(8)
            if len(header) < 8:
                break
            op, length = struct.unpack("<II", header)
            payload = conn.recv(length)
            captured.append((op, json.loads(payload)))
            body = json.dumps({"evt": "READY"}).encode()
            conn.sendall(struct.pack("<II", 1, len(body)) + body)
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_open_discord_ipc_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert platforms.open_discord_ipc() is None


def test_open_discord_ipc_connects_to_an_existing_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = str(tmp_path / "discord-ipc-0")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    try:
        conn = platforms.open_discord_ipc()
        assert conn is not None
        assert conn.name == path
        conn.close()
    finally:
        srv.close()


def test_connect_sends_handshake_and_set_activity(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = str(tmp_path / "discord-ipc-0")
    captured = []
    _serve(path, captured)

    ipc = DiscordIPC()
    assert ipc.connect("123456789") is True
    assert ipc.set_activity({"details": "In Station"}) is True
    ipc.close()

    assert captured[0][0] == 0
    assert captured[0][1] == {"v": 1, "client_id": "123456789"}
    assert captured[1][0] == 1
    assert captured[1][1]["cmd"] == "SET_ACTIVITY"
    assert captured[1][1]["args"]["activity"] == {"details": "In Station"}
    assert captured[1][1]["args"]["pid"] == os.getpid()
    assert captured[1][1]["nonce"]


def test_absent_socket_is_dormant_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    ipc = DiscordIPC()
    assert ipc.connect("123456789") is False
    assert ipc.connected is False
    # Must not raise: presence is best-effort.
    assert ipc.set_activity({"details": "x"}) is False
    ipc.close()


def test_set_activity_none_clears(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = str(tmp_path / "discord-ipc-0")
    captured = []
    _serve(path, captured)

    ipc = DiscordIPC()
    ipc.connect("1")
    ipc.set_activity(None)
    ipc.close()

    assert captured[1][1]["args"]["activity"] is None
