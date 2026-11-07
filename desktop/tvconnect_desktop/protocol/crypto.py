"""Handshake cryptography: ECDH P-256 + HKDF-SHA256 + AES-256-GCM records + ECDSA identity."""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

LABEL = b"TVconnect-v1"
HKDF_INFO = b"tvc1 session keys"


class CryptoError(Exception):
    pass


@dataclass(frozen=True)
class SessionKeys:
    d2t: bytes
    t2d: bytes
    sas: str


class Ephemeral:
    def __init__(self) -> None:
        self._priv = ec.generate_private_key(ec.SECP256R1())
        self.public_raw = self._priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    def exchange(self, peer_raw: bytes) -> bytes:
        if len(peer_raw) != 65 or peer_raw[0] != 0x04:
            raise CryptoError("invalid peer public key")
        try:
            peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), peer_raw)
        except ValueError as e:
            raise CryptoError(f"invalid peer public key: {e}") from e
        return self._priv.exchange(ec.ECDH(), peer)


def transcript_hash(hello: bytes, tv_eph_pub: bytes, tv_id_pub: bytes) -> bytes:
    h = hashlib.sha256(LABEL)
    for part in (hello, tv_eph_pub, tv_id_pub):
        h.update(struct.pack(">I", len(part)))
        h.update(part)
    return h.digest()


def derive_keys(shared: bytes, th: bytes) -> SessionKeys:
    okm = HKDF(algorithm=hashes.SHA256(), length=68, salt=th, info=HKDF_INFO).derive(shared)
    sas_num = struct.unpack(">I", okm[64:68])[0] % 1_000_000
    return SessionKeys(d2t=okm[0:32], t2d=okm[32:64], sas=f"{sas_num:06d}")


def verify_identity(id_pub_der: bytes, signature: bytes, th: bytes) -> None:
    try:
        key = serialization.load_der_public_key(id_pub_der)
    except ValueError as e:
        raise CryptoError(f"invalid TV identity key: {e}") from e
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise CryptoError("TV identity key is not P-256")
    try:
        key.verify(signature, th, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as e:
        raise CryptoError("TV identity signature is invalid") from e


def fingerprint(id_pub_der: bytes) -> str:
    return hashlib.sha256(id_pub_der).hexdigest()


def short_fingerprint(fp_hex: str) -> str:
    return ":".join(fp_hex[i : i + 4] for i in range(0, 16, 4)).upper()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class RecordCipher:
    """AES-256-GCM with a 64-bit per-direction counter nonce."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("key must be 32 bytes")
        self._aead = AESGCM(key)
        self._counter = 0

    def _nonce(self) -> bytes:
        if self._counter >= 2**64 - 1:
            raise CryptoError("nonce counter exhausted")
        n = b"\x00\x00\x00\x00" + struct.pack(">Q", self._counter)
        self._counter += 1
        return n

    def seal(self, plaintext: bytes, aad: bytes) -> bytes:
        return self._aead.encrypt(self._nonce(), plaintext, aad)

    def open(self, ciphertext: bytes, aad: bytes) -> bytes:
        try:
            return self._aead.decrypt(self._nonce(), ciphertext, aad)
        except InvalidTag as e:
            raise CryptoError("record authentication failed") from e
