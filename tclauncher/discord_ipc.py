"""Discord IPC transport.

Speaks the local Discord RPC protocol over a Unix socket: an 8-byte
little-endian header (opcode, payload length) followed by a JSON payload.
Knows nothing about The Cycle -- see presence.py for the game-specific half.

Every operation is best-effort. Discord not running is the normal case, not
an error: a game launch must never fail or be delayed because of presence.

Verified against arRPC (Vesktop) on 2026-08-26: handshake, SET_ACTIVITY and
clearing all behave as below, and arRPC converts `timestamps.start` from
seconds to milliseconds itself -- send seconds, never pre-multiply.
"""

import json
import logging
import os
import socket
import struct
import uuid

logger = logging.getLogger(__name__)

OP_HANDSHAKE = 0
OP_FRAME = 1

_RECV_TIMEOUT = 2.0


def _candidate_dirs() -> list[str]:
    """Directories Discord clients place their IPC socket in.

    Mirrors the multi-location probing in runner.find_steam_install_path():
    native, Flatpak and Snap installs each land somewhere different.
    """
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    return [
        base,
        os.path.join(base, "app", "com.discordapp.Discord"),
        os.path.join(base, "app", "dev.vencord.Vesktop"),
        os.path.join(base, "snap.discord"),
    ]


def find_ipc_socket() -> str | None:
    """First existing discord-ipc-N socket, or None if Discord isn't running."""
    for directory in _candidate_dirs():
        for n in range(10):
            path = os.path.join(directory, f"discord-ipc-{n}")
            if os.path.exists(path):
                return path
    return None


class DiscordIPC:
    def __init__(self):
        self.sock: socket.socket | None = None
        self.connected = False

    def connect(self, client_id: str) -> bool:
        path = find_ipc_socket()
        if path is None:
            logger.debug("No Discord IPC socket found; presence stays dormant")
            return False
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_RECV_TIMEOUT)
            sock.connect(path)
            self.sock = sock
            self._send(OP_HANDSHAKE, {"v": 1, "client_id": str(client_id)})
            self._recv()
        except (OSError, ValueError) as e:
            logger.debug(f"Discord handshake failed: {e}")
            self.close()
            return False
        self.connected = True
        logger.info(f"Discord presence connected via {path}")
        return True

    def set_activity(self, activity: dict | None) -> bool:
        """Push an activity, or None to clear it. False if it didn't land."""
        if not self.connected:
            return False
        payload = {
            "cmd": "SET_ACTIVITY",
            "args": {"pid": os.getpid(), "activity": activity},
            "nonce": str(uuid.uuid4()),
        }
        try:
            self._send(OP_FRAME, payload)
            # The reply is only an acknowledgement, and its `data` is null for a
            # clear. Read it to keep the stream aligned; never parse it.
            self._recv()
        except (OSError, ValueError) as e:
            logger.debug(f"Discord set_activity failed: {e}")
            self.close()
            return False
        return True

    def close(self) -> None:
        self.connected = False
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _send(self, op: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.sock.sendall(struct.pack("<II", op, len(body)) + body)

    def _recv(self) -> dict | None:
        header = self._recv_exactly(8)
        if header is None:
            return None
        _op, length = struct.unpack("<II", header)
        body = self._recv_exactly(length)
        if body is None:
            return None
        return json.loads(body)

    def _recv_exactly(self, n: int) -> bytes | None:
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
