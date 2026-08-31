"""Speaks the local Discord RPC protocol over a platform connection.

An 8-byte little-endian header (opcode, payload length) followed by a JSON
payload. Knows nothing about The Cycle -- see presence.py for the
game-specific half.

Every operation is best-effort. Discord not running is the normal case, not
an error: a game launch must never fail or be delayed because of presence.

Verified against arRPC (Vesktop) on 2026-08-26: handshake, SET_ACTIVITY and
clearing all behave as below, and arRPC converts `timestamps.start` from
seconds to milliseconds itself -- send seconds, never pre-multiply.
"""

import json
import logging
import os
import struct
import uuid

logger = logging.getLogger(__name__)

OP_HANDSHAKE = 0
OP_FRAME = 1


def _default_open_conn():
    from .platforms import open_discord_ipc
    return open_discord_ipc()


class DiscordIPC:
    def __init__(self, open_conn=None):
        # Injected so tests can drive the protocol without a real Discord.
        self._open_conn = open_conn or _default_open_conn
        self.conn = None
        self.connected = False

    def connect(self, client_id: str) -> bool:
        conn = None
        try:
            conn = self._open_conn()
        except Exception as e:
            logger.debug(f"Discord connect failed: {e}")
            return False
        if conn is None:
            logger.debug("No Discord IPC endpoint found; presence stays dormant")
            return False
        self.conn = conn
        try:
            self._send(OP_HANDSHAKE, {"v": 1, "client_id": str(client_id)})
            self._recv()
        except (OSError, ValueError) as e:
            logger.debug(f"Discord handshake failed: {e}")
            self.close()
            return False
        self.connected = True
        logger.info(f"Discord presence connected via {conn.name}")
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
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _send(self, op: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.conn.write(struct.pack("<II", op, len(body)) + body)

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
        chunks, remaining = [], n
        while remaining > 0:
            chunk = self.conn.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
