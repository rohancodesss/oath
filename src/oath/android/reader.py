"""Read an Android msgstore.db (or a crypt15 backup of it) into the neutral model."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from oath.android.crypt import decrypt_crypt15
from oath.model import Chat, Media, Message, MsgType, Quote, ReadResult

BACKUP_NAME = "msgstore.db.crypt15"

TYPE_FROM_ANDROID = {
    0: MsgType.TEXT,
    1: MsgType.IMAGE,
    2: MsgType.AUDIO,
    3: MsgType.VIDEO,
    4: MsgType.CONTACT,
    5: MsgType.LOCATION,
    7: MsgType.SYSTEM,
    9: MsgType.DOCUMENT,
    # 20 confirmed against a real device: 100% media (image/webp or application/was),
    # is_animated_sticker set on a subset, zero rows without a linked media file.
    20: MsgType.STICKER,
}


class _Jids:
    """Resolves jid row ids to phone-number JIDs, following the LID map."""

    def __init__(self, conn: sqlite3.Connection):
        self.raw = dict(conn.execute("SELECT _id, raw_string FROM jid"))
        lid_to_phone: dict[int, int] = {}
        if _has_table(conn, "jid_map"):
            lid_to_phone = dict(conn.execute("SELECT lid_row_id, jid_row_id FROM jid_map"))
        self.lid_to_phone = lid_to_phone

    def resolve(self, row_id: int | None) -> str | None:
        if row_id is None:
            return None
        return self.raw.get(self.lid_to_phone.get(row_id, row_id))


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def read_msgstore(db_path: Path, media_root: Path | None = None) -> ReadResult:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for table in ("jid", "chat", "message"):
            if not _has_table(conn, table):
                raise ValueError(f"unsupported msgstore schema: missing table '{table}'")
        return _read(conn, media_root)
    finally:
        conn.close()


def _read(conn: sqlite3.Connection, media_root: Path | None) -> ReadResult:
    jids = _Jids(conn)
    result = ReadResult(chats=[])
    unknown_types: set[int] = set()

    chat_rows = conn.execute("SELECT _id, jid_row_id, subject, archived FROM chat ORDER BY _id").fetchall()
    for chat_id, jid_row, subject, archived in chat_rows:
        jid = jids.resolve(jid_row)
        if jid is None:
            result.warnings.append(f"chat {chat_id}: unresolvable JID, skipped")
            continue
        chat = Chat(jid=jid, subject=subject, archived=bool(archived))
        rows = conn.execute(
            """
            SELECT m._id, m.key_id, m.from_me, m.timestamp, m.message_type, m.text_data, m.starred,
                   m.sender_jid_row_id, mm.file_path, mm.file_size, mm.mime_type, q.key_id, q.text_data
            FROM message m
            LEFT JOIN message_media mm ON mm.message_row_id = m._id
            LEFT JOIN message_quoted q ON q.message_row_id = m._id
            WHERE m.chat_row_id = ?
            ORDER BY m.sort_id, m._id
            """,
            (chat_id,),
        ).fetchall()
        for (_, key_id, from_me, ts, mtype, text, starred, sender_row,
             file_path, file_size, mime, quoted_id, quoted_text) in rows:
            msg_type = TYPE_FROM_ANDROID.get(mtype)
            if msg_type is None:
                unknown_types.add(mtype)
                msg_type = MsgType.UNKNOWN
            media = None
            if file_path:
                source = (media_root / file_path) if media_root else None
                media = Media(Path(file_path).name, mime, file_size or 0, source)
            sender = jids.resolve(sender_row) if chat.is_group and not from_me else None
            chat.messages.append(Message(
                key_id=key_id,
                from_me=bool(from_me),
                timestamp_ms=ts,
                type=msg_type,
                text=text,
                sender_jid=sender,
                media=media,
                quoted=Quote(quoted_id, quoted_text) if quoted_id else None,
                starred=bool(starred),
            ))
        result.chats.append(chat)

    if unknown_types:
        result.warnings.append(f"unknown message_type values: {sorted(unknown_types)}")
    return result


def load_android(wa_root: Path, key_hex: str) -> ReadResult:
    """Decrypt <wa_root>/Databases/msgstore.db.crypt15 and read it, with media from <wa_root>."""
    backup = wa_root / "Databases" / BACKUP_NAME
    plain = decrypt_crypt15(backup.read_bytes(), key_hex)
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        db_path = Path(tmp) / "msgstore.db"
        db_path.write_bytes(plain)
        return read_msgstore(db_path, wa_root)
