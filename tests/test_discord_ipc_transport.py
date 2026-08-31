"""The transport seam: framing stays shared, endpoints are per-platform."""

import json
import struct

from tclauncher import platforms
from tclauncher.discord_ipc import OP_FRAME, OP_HANDSHAKE, DiscordIPC


class FakeConn:
    """A Discord that echoes a minimal reply to every frame."""

    name = "fake"

    def __init__(self):
        self.written = b""
        self._pending = b""

    def write(self, data: bytes) -> None:
        self.written += data
        self._pending += self._reply()

    def _reply(self) -> bytes:
        body = json.dumps({"cmd": "DISPATCH", "data": None}).encode()
        return struct.pack("<II", OP_FRAME, len(body)) + body

    def read(self, n: int) -> bytes:
        chunk, self._pending = self._pending[:n], self._pending[n:]
        return chunk

    def close(self) -> None:
        pass


def _frames(blob: bytes) -> list[tuple[int, dict]]:
    out, i = [], 0
    while i < len(blob):
        op, length = struct.unpack("<II", blob[i:i + 8])
        out.append((op, json.loads(blob[i + 8:i + 8 + length])))
        i += 8 + length
    return out


def test_handshake_is_sent_with_the_client_id():
    conn = FakeConn()
    ipc = DiscordIPC(open_conn=lambda: conn)
    assert ipc.connect("123") is True
    op, payload = _frames(conn.written)[0]
    assert op == OP_HANDSHAKE
    assert payload == {"v": 1, "client_id": "123"}


def test_set_activity_sends_a_frame_with_the_activity():
    conn = FakeConn()
    ipc = DiscordIPC(open_conn=lambda: conn)
    ipc.connect("123")
    assert ipc.set_activity({"details": "In Match"}) is True
    op, payload = _frames(conn.written)[-1]
    assert op == OP_FRAME
    assert payload["cmd"] == "SET_ACTIVITY"
    assert payload["args"]["activity"] == {"details": "In Match"}
    assert isinstance(payload["args"]["pid"], int)


def test_clearing_sends_a_null_activity():
    conn = FakeConn()
    ipc = DiscordIPC(open_conn=lambda: conn)
    ipc.connect("123")
    ipc.set_activity(None)
    _op, payload = _frames(conn.written)[-1]
    assert payload["args"]["activity"] is None


def test_no_discord_is_not_an_error():
    ipc = DiscordIPC(open_conn=lambda: None)
    assert ipc.connect("123") is False
    assert ipc.connected is False
    assert ipc.set_activity({"details": "x"}) is False


def test_a_broken_connection_degrades_instead_of_raising():
    class Broken(FakeConn):
        def write(self, data):
            raise OSError("pipe closed")

    ipc = DiscordIPC(open_conn=lambda: Broken())
    assert ipc.connect("123") is False


def test_open_discord_ipc_returns_none_when_no_endpoint_exists(tmp_path, monkeypatch):
    """Must not reach the developer's live Discord: point the probe at an
    empty runtime dir so the result is deterministic on any machine."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("TMPDIR", raising=False)
    assert platforms.open_discord_ipc() is None
