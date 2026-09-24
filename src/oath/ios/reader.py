"""Read WhatsApp's ChatStorage.sqlite out of an iPhone backup into the neutral model."""

from __future__ import annotations

import mimetypes
import sqlite3
import tempfile
from pathlib import Path

from oath.ios.backup import WA_DOMAIN, BackupError, IOSBackup
from oath.model import Chat, Media, Message, MsgType, ReadResult

APPLE_EPOCH_OFFSET = 978307200
CHAT_STORAGE = "ChatStorage.sqlite"
MEDIA_FS_PREFIX = "Message"

TYPE_FROM_IOS = {
    0: MsgType.TEXT,
    1: MsgType.IMAGE,
    2: MsgType.VIDEO,
    3: MsgType.AUDIO,
    4: MsgType.CONTACT,
    5: MsgType.LOCATION,
    6: MsgType.SYSTEM,
    8: MsgType.DOCUMENT,
    15: MsgType.STICKER,
}


def apple_to_ms(value: float) -> int:
    return round((value + APPLE_EPOCH_OFFSET) * 1000)


def ms_to_apple(ms: int) -> float:
    return ms / 1000 - APPLE_EPOCH_OFFSET


def read_chat_storage(db_path: Path, backup: IOSBackup | None = None) -> ReadResult:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    result = ReadResult(chats=[])
    unknown_types: set[int] = set()
    try:
        sessions = conn.execute(
            "SELECT Z_PK, ZCONTACTJID, ZPARTNERNAME, ZARCHIVED FROM ZWACHATSESSION "
            "WHERE ZSESSIONTYPE IN (0, 1) ORDER BY Z_PK"
        ).fetchall()
        for pk, jid, name, archived in sessions:
            chat = Chat(jid=jid, subject=name, archived=bool(archived))
            rows = conn.execute(
                """
                SELECT m.ZSTANZAID, m.ZISFROMME, m.ZMESSAGEDATE, m.ZMESSAGETYPE, m.ZTEXT,
                       gm.ZMEMBERJID, mi.ZMEDIALOCALPATH, mi.ZFILESIZE
                FROM ZWAMESSAGE m
                LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER
                LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM
                WHERE m.ZCHATSESSION = ?
                ORDER BY m.ZSORT, m.Z_PK
                """,
                (pk,),
            ).fetchall()
            for stanza, from_me, date, mtype, text, member, local_path, size in rows:
                msg_type = TYPE_FROM_IOS.get(mtype)
                if msg_type is None:
                    unknown_types.add(mtype)
                    msg_type = MsgType.UNKNOWN
                media = None
                if local_path:
                    source = None
                    if backup is not None:
                        try:
                            source = backup.path_of(WA_DOMAIN, f"{MEDIA_FS_PREFIX}/{local_path}")
                        except BackupError:
                            result.warnings.append(f"media file missing from backup: {local_path}")
                    mime = mimetypes.guess_type(local_path)[0]
                    media = Media(Path(local_path).name, mime, size or 0, source)
                chat.messages.append(Message(
                    key_id=stanza,
                    from_me=bool(from_me),
                    timestamp_ms=apple_to_ms(date),
                    type=msg_type,
                    text=text,
                    sender_jid=member,
                    media=media,
                ))
            result.chats.append(chat)
    finally:
        conn.close()
    if unknown_types:
        result.warnings.append(f"unknown ZMESSAGETYPE values: {sorted(unknown_types)}")
    return result


def load_ios(backup: IOSBackup) -> ReadResult:
    data = backup.read_file(WA_DOMAIN, CHAT_STORAGE)
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        db_path = Path(tmp) / CHAT_STORAGE
        db_path.write_bytes(data)
        return read_chat_storage(db_path, backup)
