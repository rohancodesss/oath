"""Emulate an Android WhatsApp data directory: msgstore.db, crypt15 backup and Media/."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from oath.android.crypt import encrypt_crypt15
from oath.android.reader import BACKUP_NAME
from oath.emulate.sample import CAROL, DEFAULT_KEY_HEX, fake_media_bytes, sample_chats
from oath.model import MEDIA_TYPES, Chat, MsgType

DDL = """
CREATE TABLE jid (_id INTEGER PRIMARY KEY AUTOINCREMENT, user TEXT NOT NULL, server TEXT NOT NULL,
                  agent INTEGER, device INTEGER, type INTEGER, raw_string TEXT);
CREATE UNIQUE INDEX jid_raw ON jid (raw_string);
CREATE TABLE jid_map (lid_row_id INTEGER PRIMARY KEY, jid_row_id INTEGER NOT NULL);
CREATE TABLE chat (_id INTEGER PRIMARY KEY AUTOINCREMENT, jid_row_id INTEGER UNIQUE, subject TEXT,
                   created_timestamp INTEGER, archived INTEGER, last_message_row_id INTEGER,
                   unseen_message_count INTEGER);
CREATE TABLE message (_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_row_id INTEGER NOT NULL,
                      from_me INTEGER NOT NULL, key_id TEXT NOT NULL, sender_jid_row_id INTEGER,
                      status INTEGER, message_type INTEGER, text_data TEXT, timestamp INTEGER,
                      starred INTEGER, sort_id INTEGER);
CREATE TABLE message_media (message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER, file_path TEXT,
                            file_size INTEGER, mime_type TEXT, media_duration INTEGER, file_hash TEXT);
CREATE TABLE message_quoted (message_row_id INTEGER PRIMARY KEY, chat_row_id INTEGER,
                             parent_message_chat_row_id INTEGER, key_id TEXT, sender_jid_row_id INTEGER,
                             text_data TEXT);
"""

TYPE_TO_ANDROID = {
    MsgType.TEXT: 0,
    MsgType.IMAGE: 1,
    MsgType.AUDIO: 2,
    MsgType.VIDEO: 3,
    MsgType.CONTACT: 4,
    MsgType.LOCATION: 5,
    MsgType.SYSTEM: 7,
    MsgType.DOCUMENT: 9,
    MsgType.STICKER: 20,  # confirmed against a real device: see android/reader.py TYPE_FROM_ANDROID
}

MEDIA_DIRS = {
    MsgType.IMAGE: "WhatsApp Images",
    MsgType.VIDEO: "WhatsApp Video",
    MsgType.AUDIO: "WhatsApp Voice Notes",
    MsgType.DOCUMENT: "WhatsApp Documents",
    MsgType.STICKER: "WhatsApp Stickers",
}

LID_USER = "98765432109876"


def _jid_row(conn: sqlite3.Connection, raw: str) -> int:
    row = conn.execute("SELECT _id FROM jid WHERE raw_string = ?", (raw,)).fetchone()
    if row:
        return row[0]
    user, server = raw.split("@")
    kind = 1 if server == "g.us" else 0
    return conn.execute(
        "INSERT INTO jid (user, server, agent, device, type, raw_string) VALUES (?, ?, 0, 0, ?, ?)",
        (user, server, kind, raw),
    ).lastrowid


def write_msgstore(db_path: Path, chats: list[Chat], wa_root: Path, lid_for: str | None = CAROL) -> None:
    """Write chats into a modern-schema msgstore.db. `lid_for` is stored as an @lid sender."""
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    lid_row = None
    if lid_for:
        phone_row = _jid_row(conn, lid_for)
        lid_row = _jid_row(conn, f"{LID_USER}@lid")
        conn.execute("INSERT INTO jid_map (lid_row_id, jid_row_id) VALUES (?, ?)", (lid_row, phone_row))

    sort = 0
    for chat in chats:
        chat_row = conn.execute(
            "INSERT INTO chat (jid_row_id, subject, created_timestamp, archived, unseen_message_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (_jid_row(conn, chat.jid), chat.subject, chat.messages[0].timestamp_ms, int(chat.archived)),
        ).lastrowid
        last = None
        for msg in chat.messages:
            sort += 1
            sender = None
            if chat.is_group and not msg.from_me and msg.sender_jid:
                sender = lid_row if msg.sender_jid == lid_for else _jid_row(conn, msg.sender_jid)
            last = conn.execute(
                "INSERT INTO message (chat_row_id, from_me, key_id, sender_jid_row_id, status, message_type, "
                "text_data, timestamp, starred, sort_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_row, int(msg.from_me), msg.key_id, sender, 13 if msg.from_me else 0,
                 TYPE_TO_ANDROID[msg.type], msg.text, msg.timestamp_ms, int(msg.starred), sort),
            ).lastrowid
            if msg.type in MEDIA_TYPES and msg.media:
                rel = f"Media/{MEDIA_DIRS[msg.type]}/{msg.media.filename}"
                target = wa_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(fake_media_bytes(msg.key_id, msg.media.size))
                conn.execute(
                    "INSERT INTO message_media (message_row_id, chat_row_id, file_path, file_size, mime_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (last, chat_row, rel, msg.media.size, msg.media.mime),
                )
            if msg.quoted:
                conn.execute(
                    "INSERT INTO message_quoted (message_row_id, chat_row_id, parent_message_chat_row_id, key_id, text_data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (last, chat_row, chat_row, msg.quoted.key_id, msg.quoted.text),
                )
        conn.execute("UPDATE chat SET last_message_row_id = ? WHERE _id = ?", (last, chat_row))
    conn.commit()
    conn.close()


def build_android_fixture(dest: Path, key_hex: str = DEFAULT_KEY_HEX, chats: list[Chat] | None = None,
                          iv: bytes | None = None) -> Path:
    """Create <dest>/WhatsApp/{Databases,Media} like /sdcard/Android/media/com.whatsapp/WhatsApp."""
    wa_root = Path(dest) / "WhatsApp"
    (wa_root / "Databases").mkdir(parents=True, exist_ok=True)
    plain = wa_root / "Databases" / "msgstore.db"
    plain.unlink(missing_ok=True)
    write_msgstore(plain, chats if chats is not None else sample_chats(), wa_root)
    (wa_root / "Databases" / BACKUP_NAME).write_bytes(encrypt_crypt15(plain.read_bytes(), key_hex, iv))
    plain.unlink()
    return wa_root
