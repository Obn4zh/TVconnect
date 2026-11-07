import json
import socket
import struct
import threading
from pathlib import Path

import pytest

from tvconnect_desktop.protocol.constants import T_CONTROL, T_VIDEO
from tvconnect_desktop.protocol.crypto import (
    CryptoError,
    Ephemeral,
    RecordCipher,
    derive_keys,
    transcript_hash,
)
from tvconnect_desktop.protocol.framing import FramedSocket, ProtocolError, decode_json, encode_json

VECTORS = Path(__file__).resolve().parents[2] / "protocol" / "test-vectors" / "crypto.json"


def test_vectors_match():
    v = json.loads(VECTORS.read_text(encoding="utf-8"))
    th = transcript_hash(bytes.fromhex(v["hello"]), bytes.fromhex(v["tv_eph_pub"]), bytes.fromhex(v["tv_id_pub"]))
    assert th.hex() == v["transcript_hash"]
    keys = derive_keys(bytes.fromhex(v["shared"]), th)
    assert keys.d2t.hex() == v["k_d2t"] and keys.t2d.hex() == v["k_t2d"] and keys.sas == v["sas"]
    c = RecordCipher(keys.d2t)
    for r in v["records_d2t"]:
        assert c.seal(bytes.fromhex(r["plain"]), bytes.fromhex(r["aad"])).hex() == r["ct"]


def test_ecdh_agreement_and_sas():
    a, b = Ephemeral(), Ephemeral()
    assert a.exchange(b.public_raw) == b.exchange(a.public_raw)
    with pytest.raises(CryptoError):
        a.exchange(b"\x04" + b"\x00" * 64)


def test_tamper_detected():
    k = bytes(32)
    tx, rx = RecordCipher(k), RecordCipher(k)
    ct = bytearray(tx.seal(b"hello", b"aad"))
    ct[0] ^= 1
    with pytest.raises(CryptoError):
        rx.open(bytes(ct), b"aad")


def test_replay_detected():
    k = bytes(range(32))
    tx, rx = RecordCipher(k), RecordCipher(k)
    ct = tx.seal(b"one", b"x")
    assert rx.open(ct, b"x") == b"one"
    with pytest.raises(CryptoError):
        rx.open(ct, b"x")  # counter moved on


def test_framed_roundtrip_encrypted():
    s1, s2 = socket.socketpair()
    a, b = FramedSocket(s1), FramedSocket(s2)
    a.tx = RecordCipher(bytes(32))
    b.rx = RecordCipher(bytes(32))
    a.write(T_CONTROL, encode_json({"type": "ping", "t": 1}))
    a.write(T_VIDEO, b"\x01" + b"x" * 100000)
    t, p = b.read()
    assert t == T_CONTROL and decode_json(p)["type"] == "ping"
    t, p = b.read()
    assert t == T_VIDEO and len(p) == 100001
    s1.close()
    s2.close()


def test_rejects_huge_length():
    s1, s2 = socket.socketpair()
    s1.sendall(struct.pack(">I", 0x7FFFFFFF))
    with pytest.raises(ProtocolError):
        FramedSocket(s2).read()
    s1.close()
    s2.close()


def test_decode_json_requires_type():
    with pytest.raises(ProtocolError):
        decode_json(b'{"x":1}')
    with pytest.raises(ProtocolError):
        decode_json(b"\xff\xfe")
