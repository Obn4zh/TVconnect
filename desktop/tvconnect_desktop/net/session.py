"""Client side of a TVconnect session (Native Agent Mode): handshake, pairing, control loop."""

from __future__ import annotations

import base64
import logging
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..devices.trust_store import TrustStore
from ..protocol.constants import PROTO_VERSION, T_CONTROL, T_INPUT, T_VIDEO
from ..protocol.crypto import (
    CryptoError,
    Ephemeral,
    RecordCipher,
    derive_keys,
    fingerprint,
    short_fingerprint,
    transcript_hash,
    verify_identity,
)
from ..protocol.framing import ConnectionClosed, FramedSocket, ProtocolError, decode_json, encode_json

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
HANDSHAKE_TIMEOUT = 10.0
PAIRING_TIMEOUT = 90.0
IDLE_TIMEOUT = 15.0
PING_INTERVAL = 1.0

ERROR_TEXT = {
    "busy": "Телевизор уже подключён к другому компьютеру",
    "disabled": "Удалённое управление выключено в настройках TV-приложения",
    "proto_mismatch": "Несовместимая версия протокола — обновите приложения",
    "rate_limited": "Слишком много отклонённых запросов, попробуйте через минуту",
}


class SessionError(Exception):
    pass


class SessionListener(Protocol):
    def on_state(self, state: str, detail: str) -> None: ...
    def on_pairing_code(self, sas: str, fingerprint: str) -> None: ...
    def on_ready(self, info: dict[str, Any]) -> None: ...
    def on_video(self, flags: int, pts_us: int, data: bytes) -> None: ...
    def on_control(self, msg: dict[str, Any]) -> None: ...
    def on_rtt(self, ms: float) -> None: ...
    def on_closed(self, reason: str, error: bool) -> None: ...


@dataclass
class TvInfo:
    tv_id: str = ""
    name: str = ""
    model: str = ""
    fingerprint: str = ""
    screen: tuple[int, int] = (1920, 1080)
    sdk: int = 0
    caps: dict[str, Any] = field(default_factory=dict)


class NativeSession:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        client_name: str,
        trust: TrustStore,
        listener: SessionListener,
    ) -> None:
        self.host, self.port = host, port
        self.client_id, self.client_name = client_id, client_name
        self.trust = trust
        self.listener = listener
        self.info = TvInfo()
        self._fs: FramedSocket | None = None
        self._sock: socket.socket | None = None
        self._closing = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"session-{host}", daemon=True)
        self._ping_thread = threading.Thread(target=self._ping_loop, name="session-ping", daemon=True)

    # ---- public API (thread-safe) ----

    def start(self) -> None:
        self._thread.start()

    def close(self, reason: str = "user") -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        if self._ready.is_set():
            try:
                self.send_control({"type": "bye", "reason": reason})
            except OSError:
                pass
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and not self._closing.is_set()

    def send_control(self, msg: dict[str, Any]) -> None:
        if self._fs is None:
            raise SessionError("not connected")
        self._fs.write(T_CONTROL, encode_json(msg))

    def send_input(self, msg: dict[str, Any]) -> bool:
        if not self.ready or self._fs is None:
            return False
        try:
            self._fs.write(T_INPUT, encode_json(msg))
            return True
        except OSError as e:
            log.warning("send_input failed: %s", e)
            return False

    def start_video(self, max_w: int, max_h: int, fps: int, bitrate: int) -> None:
        self.send_control({"type": "start_video", "max_w": max_w, "max_h": max_h, "fps": fps, "bitrate": bitrate})

    def stop_video(self) -> None:
        self.send_control({"type": "stop_video"})

    def request_keyframe(self) -> None:
        if self.ready:
            try:
                self.send_control({"type": "request_keyframe"})
            except OSError:
                pass

    # ---- internals ----

    def _run(self) -> None:
        reason, is_error = "closed", False
        try:
            self._connect()
            self._handshake()
            self.listener.on_state("connected", f"{self.info.name} ({self.host})")
            self._ping_thread.start()
            self._loop()
        except ConnectionClosed as e:
            reason, is_error = str(e), not self._closing.is_set()
        except (SessionError, CryptoError, ProtocolError) as e:
            reason, is_error = str(e), True
            log.error("Session error: %s", e)
        except socket.timeout:
            reason, is_error = "нет ответа от телевизора (таймаут)", True
            log.error("Session timeout")
        except OSError as e:
            if self._closing.is_set():
                reason = "отключено"
            else:
                reason, is_error = f"сетевая ошибка: {e}", True
                log.error("Network error: %s", e)
        except Exception as e:  # keep the UI alive on bugs, but log them loudly
            reason, is_error = f"внутренняя ошибка: {e}", True
            log.exception("Unexpected session failure")
        finally:
            self._closing.set()
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            log.info("Session closed: %s", reason)
            self.listener.on_closed(reason, is_error)

    def _connect(self) -> None:
        self.listener.on_state("connecting", f"{self.host}:{self.port}")
        log.info("Connecting to %s:%d", self.host, self.port)
        try:
            sock = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            raise SessionError(f"не удалось подключиться к {self.host}:{self.port}: {e}") from e
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.settimeout(HANDSHAKE_TIMEOUT)
        self._sock = sock
        self._fs = FramedSocket(sock)

    def _read_control(self) -> dict[str, Any]:
        assert self._fs is not None
        while True:
            rtype, payload = self._fs.read()
            if rtype == T_CONTROL:
                msg = decode_json(payload)
                if msg["type"] == "error":
                    code = str(msg.get("code", ""))
                    raise SessionError(ERROR_TEXT.get(code, f"ошибка телевизора: {msg.get('message', code)}"))
                return msg
            log.debug("Ignoring record type %d during handshake", rtype)

    @staticmethod
    def _b64(msg: dict[str, Any], key: str) -> bytes:
        try:
            return base64.b64decode(str(msg[key]), validate=True)
        except (KeyError, ValueError) as e:
            raise ProtocolError(f"bad field {key}") from e

    def _handshake(self) -> None:
        assert self._fs is not None and self._sock is not None
        eph = Ephemeral()
        hello = encode_json(
            {
                "type": "hello",
                "proto": PROTO_VERSION,
                "client_id": self.client_id,
                "client_name": self.client_name,
                "eph_pub": base64.b64encode(eph.public_raw).decode(),
            }
        )
        self._fs.write(T_CONTROL, hello)
        ack = self._read_control()
        if ack["type"] != "hello_ack":
            raise ProtocolError(f"expected hello_ack, got {ack['type']}")
        if ack.get("proto") != PROTO_VERSION:
            raise SessionError(ERROR_TEXT["proto_mismatch"])

        tv_eph = self._b64(ack, "eph_pub")
        id_pub = self._b64(ack, "id_pub")
        sig = self._b64(ack, "sig")
        th = transcript_hash(hello, tv_eph, id_pub)
        verify_identity(id_pub, sig, th)

        info = self.info
        info.tv_id = str(ack.get("tv_id", ""))[:64]
        info.name = str(ack.get("tv_name", "Android TV"))[:64]
        info.model = str(ack.get("model", ""))[:64]
        info.fingerprint = fingerprint(id_pub)
        if not info.tv_id:
            raise ProtocolError("TV did not send tv_id")

        known = self.trust.get(info.tv_id)
        if known and known.fingerprint != info.fingerprint:
            raise SessionError(
                "Ключ телевизора изменился! Возможна подмена устройства. "
                "Если вы переустановили TV-приложение — удалите ТВ из доверенных и подключитесь снова."
            )

        keys = derive_keys(eph.exchange(tv_eph), th)
        self._fs.tx = RecordCipher(keys.d2t)
        self._fs.rx = RecordCipher(keys.t2d)
        log.info("Secure channel established with %s (%s), fp %s", info.name, info.tv_id, short_fingerprint(info.fingerprint))

        authed = False
        token = self.trust.token(info.tv_id) if known else None
        if token:
            self.listener.on_state("authenticating", info.name)
            self.send_control({"type": "auth", "token": base64.b64encode(token).decode()})
            res = self._read_control()
            if res["type"] == "auth_result" and res.get("ok") is True:
                authed = True
                self.trust.update_address(info.tv_id, self.host, self.port, info.name)
                log.info("Authenticated with stored trust token")
            else:
                log.warning("Stored token rejected (%s), pairing again", res.get("reason"))
                self.trust.forget(info.tv_id)

        if not authed:
            self.listener.on_state("pairing", info.name)
            self.listener.on_pairing_code(keys.sas, short_fingerprint(info.fingerprint))
            self.send_control({"type": "pair_request"})
            self._sock.settimeout(PAIRING_TIMEOUT)
            res = self._read_control()
            if res["type"] != "pair_result":
                raise ProtocolError(f"expected pair_result, got {res['type']}")
            if not res.get("accepted"):
                r = res.get("reason", "denied")
                raise SessionError("Подключение отклонено на телевизоре" if r == "denied" else "Запрос не подтверждён вовремя")
            tok = res.get("token")
            if tok:
                stored = self.trust.remember(
                    info.tv_id, info.name, self.host, self.port, info.fingerprint, base64.b64decode(tok)
                )
                log.info("Paired and remembered=%s", stored)
            else:
                log.info("Paired for this session only")

        self._sock.settimeout(HANDSHAKE_TIMEOUT)
        ready = self._read_control()
        if ready["type"] != "session_ready":
            raise ProtocolError(f"expected session_ready, got {ready['type']}")
        scr = ready.get("screen") or {}
        info.screen = (int(scr.get("w", 1920)), int(scr.get("h", 1080)))
        info.sdk = int(ready.get("sdk", 0))
        info.caps = dict(ready.get("caps") or {})
        self._sock.settimeout(IDLE_TIMEOUT)
        self._ready.set()
        self.listener.on_ready(
            {"name": info.name, "model": info.model, "screen": info.screen, "sdk": info.sdk, "caps": info.caps}
        )

    def _loop(self) -> None:
        assert self._fs is not None
        while not self._closing.is_set():
            rtype, payload = self._fs.read()
            if rtype == T_VIDEO:
                if len(payload) < 9:
                    raise ProtocolError("short video record")
                flags = payload[0]
                (pts,) = struct.unpack(">Q", payload[1:9])
                self.listener.on_video(flags, pts, payload[9:])
            elif rtype == T_CONTROL:
                msg = decode_json(payload)
                t = msg["type"]
                if t == "pong":
                    try:
                        self.listener.on_rtt((time.monotonic() - float(msg["t"])) * 1000.0)
                    except (KeyError, TypeError, ValueError):
                        pass
                elif t == "bye":
                    raise ConnectionClosed(f"телевизор завершил сеанс ({msg.get('reason', '')})")
                elif t == "error":
                    log.error("TV error: %s", msg)
                    self.listener.on_control(msg)
                else:
                    if t == "caps":
                        self.info.caps.update({k: v for k, v in msg.items() if k != "type"})
                    self.listener.on_control(msg)
            else:
                log.debug("Unknown record type %d", rtype)

    def _ping_loop(self) -> None:
        while not self._closing.wait(PING_INTERVAL):
            try:
                self.send_control({"type": "ping", "t": time.monotonic()})
            except (OSError, SessionError):
                return
