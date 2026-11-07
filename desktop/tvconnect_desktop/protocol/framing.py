"""Length-prefixed records over a TCP socket, optionally encrypted (see docs/PROTOCOL.md §1)."""

from __future__ import annotations

import json
import socket
import struct
import threading
from typing import Any

from .constants import GCM_TAG, MAX_RECORD
from .crypto import RecordCipher


class ProtocolError(Exception):
    pass


class ConnectionClosed(Exception):
    pass


def encode_json(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json(payload: bytes, key: str = "type") -> dict[str, Any]:
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"malformed JSON message: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get(key), str):
        raise ProtocolError(f"message without '{key}' field")
    return obj


class FramedSocket:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.tx: RecordCipher | None = None
        self.rx: RecordCipher | None = None
        self._wlock = threading.Lock()

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray(n)
        view = memoryview(buf)
        got = 0
        while got < n:
            r = self.sock.recv_into(view[got:], n - got)
            if r == 0:
                raise ConnectionClosed("connection closed by peer")
            got += r
        return bytes(buf)

    def read(self) -> tuple[int, bytes]:
        header = self._recv_exact(4)
        (length,) = struct.unpack(">I", header)
        if length < 1 or length > MAX_RECORD:
            raise ProtocolError(f"invalid record length {length}")
        body = self._recv_exact(length)
        if self.rx is not None:
            body = self.rx.open(body, header)
            if not body:
                raise ProtocolError("empty record")
        return body[0], body[1:]

    def write(self, rtype: int, payload: bytes) -> None:
        plain = bytes([rtype]) + payload
        with self._wlock:
            if self.tx is not None:
                header = struct.pack(">I", len(plain) + GCM_TAG)
                data = header + self.tx.seal(plain, header)
            else:
                data = struct.pack(">I", len(plain)) + plain
            if len(data) - 4 > MAX_RECORD:
                raise ProtocolError("record too large")
            self.sock.sendall(data)
