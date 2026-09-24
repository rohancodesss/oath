"""crypt15 backup encryption, delegated to wa-crypt-tools."""

from __future__ import annotations

import io
import zlib

from wa_crypt_tools.lib.db.db15 import Database15
from wa_crypt_tools.lib.db.dbfactory import DatabaseFactory
from wa_crypt_tools.lib.key.keyfactory import KeyFactory
from wa_crypt_tools.lib.props import Props

SQLITE_MAGIC = b"SQLite format 3\x00"


class DecryptError(Exception):
    pass


def _key(key_hex: str):
    key_hex = key_hex.strip().replace(" ", "").lower()
    try:
        return KeyFactory.from_hex(key_hex)
    except ValueError as exc:
        raise DecryptError(f"invalid 64-digit key: {exc}") from exc


def decrypt_crypt15(data: bytes, key_hex: str) -> bytes:
    """Decrypt and decompress a crypt15 backup into raw SQLite bytes."""
    key = _key(key_hex)
    stream = io.BufferedReader(io.BytesIO(data))
    try:
        db = DatabaseFactory.from_file(stream)
        plain = zlib.decompress(db.decrypt(key, stream.read()))
    except (zlib.error, ValueError, TypeError) as exc:
        raise DecryptError("wrong key or corrupt backup") from exc
    if not plain.startswith(SQLITE_MAGIC):
        raise DecryptError("decrypted data is not a SQLite database")
    return plain


def encrypt_crypt15(plain: bytes, key_hex: str, iv: bytes | None = None) -> bytes:
    """Compress and encrypt SQLite bytes into a crypt15 file."""
    key = _key(key_hex)
    db = Database15(key=key, iv=iv)
    return db.encrypt(key, Props(), zlib.compress(plain, 1))
