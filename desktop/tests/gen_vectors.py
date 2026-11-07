"""Generates protocol/test-vectors/crypto.json, consumed by both Python and Kotlin unit tests.

Run: python tests/gen_vectors.py
"""

import json
import struct
from pathlib import Path

from tvconnect_desktop.protocol.crypto import RecordCipher, derive_keys, transcript_hash

OUT = Path(__file__).resolve().parents[2] / "protocol" / "test-vectors" / "crypto.json"


def main() -> None:
    hello = b'{"type":"hello","proto":1,"client_id":"00112233445566778899aabbccddeeff"}'
    eph = bytes([0x04]) + bytes(range(1, 65))
    id_pub = bytes(range(100, 191))
    th = transcript_hash(hello, eph, id_pub)
    shared = bytes(range(32, 64))
    keys = derive_keys(shared, th)
    cipher = RecordCipher(keys.d2t)
    records = []
    for i, text in enumerate([b"\x01{\"type\":\"ping\",\"t\":1}", b"\x03{\"t\":\"key\",\"code\":4}"]):
        aad = struct.pack(">I", len(text) + 16)
        records.append({"counter": i, "plain": text.hex(), "aad": aad.hex(), "ct": cipher.seal(text, aad).hex()})
    data = {
        "hello": hello.hex(),
        "tv_eph_pub": eph.hex(),
        "tv_id_pub": id_pub.hex(),
        "transcript_hash": th.hex(),
        "shared": shared.hex(),
        "k_d2t": keys.d2t.hex(),
        "k_t2d": keys.t2d.hex(),
        "sas": keys.sas,
        "records_d2t": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
