"""Inject neutral-model chats into the iPhone's own ChatStorage.sqlite inside a backup.

The target database is never rebuilt: rows are added to the store WhatsApp itself created,
so the Core Data model metadata stays valid. Only columns that exist are written.
"""

from __future__ import annotations

import mimetypes
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from oath.ios.backup import WA_DOMAIN, IOSBackup
from oath.ios.lid import load_phone_to_lid, resolve_jid
from oath.ios.reader import CHAT_STORAGE, MEDIA_FS_PREFIX, ms_to_apple
from oath.model import MEDIA_TYPES, Chat, Message, MsgType

# ZMESSAGETYPE values. Confidence levels, from live-device verification on 2026-09-24
# (see docs/RESEARCH.md): confirmed = cross-referenced against real ZTEXT/ZMEDIAITEM/file
# extension evidence; inferred = a strong pattern match with no direct counter-example.
# Anything not in this dict falls back to the safe TEXT+placeholder path below rather than
# assert an unverified number -- a lesson from STICKER, which was wrongly guessed as 1
# (image) before being confirmed as 15 against real data.
TYPE_TO_IOS = {
    MsgType.TEXT: 0,     # confirmed: 6/6 real messages, real text, no media
    MsgType.IMAGE: 1,    # confirmed: real .jpg attached
    MsgType.VIDEO: 2,    # inferred: fits the confirmed 0,1,_,3 sequence; no direct test
    MsgType.AUDIO: 3,    # confirmed: real .opus (voice note) attached
    MsgType.SYSTEM: 6,   # inferred: matches the group-event signature (ZGROUPEVENTTYPE
                         # populated, never from_me, mostly no text/media)
    MsgType.STICKER: 15,  # confirmed: real .webp attached
}

ENTITIES = {
    "ZWACHATSESSION": "WAChatSession",
    "ZWAMESSAGE": "WAMessage",
    "ZWAMEDIAITEM": "WAMediaItem",
    "ZWAGROUPMEMBER": "WAGroupMember",
}

MEDIA_NAMESPACE = uuid.UUID("6f0f6a6e-0a7c-4a53-9a55-0a7e0a7e0a7e")
UNSUPPORTED_TEXT = "[message type not supported by OATH]"


class SchemaError(Exception):
    pass


@dataclass
class WriteReport:
    chats_added: int = 0
    chats_merged: int = 0
    chats_skipped_unknown_session_type: int = 0
    messages_added: int = 0
    messages_skipped_existing: int = 0
    messages_skipped_unknown_session_type: int = 0
    media_files: int = 0
    stickers_written: int = 0
    lid_resolved: int = 0
    lid_unresolved: int = 0
    unconfirmed_type_fallback: int = 0
    warnings: list[str] = field(default_factory=list)


class _Store:
    """Thin Core Data-aware wrapper: primary key allocation and column-safe inserts."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.ent: dict[str, int] = {}
        self.next_pk: dict[str, int] = {}
        self.columns: dict[str, set[str]] = {}
        self.dropped_columns: set[str] = set()
        for table, name in ENTITIES.items():
            row = conn.execute("SELECT Z_ENT, Z_MAX FROM Z_PRIMARYKEY WHERE Z_NAME = ?", (name,)).fetchone()
            if row is None:
                raise SchemaError(f"Z_PRIMARYKEY has no entity '{name}'; unsupported ChatStorage schema")
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not cols:
                raise SchemaError(f"table {table} missing; unsupported ChatStorage schema")
            top = conn.execute(f"SELECT COALESCE(MAX(Z_PK), 0) FROM {table}").fetchone()[0]
            self.ent[table] = row[0]
            self.next_pk[table] = max(row[1] or 0, top) + 1
            self.columns[table] = cols

    def insert(self, table: str, values: dict) -> int:
        pk = self.next_pk[table]
        self.next_pk[table] += 1
        row = {"Z_PK": pk, "Z_ENT": self.ent[table], "Z_OPT": 1, **values}
        kept = {}
        for col, val in row.items():
            if col in self.columns[table]:
                kept[col] = val
            else:
                self.dropped_columns.add(f"{table}.{col}")
        names = ", ".join(kept)
        marks = ", ".join("?" for _ in kept)
        self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(kept.values()))
        return pk

    def update(self, table: str, pk: int, values: dict) -> None:
        kept = {c: v for c, v in values.items() if c in self.columns[table]}
        sets = ", ".join(f"{c} = ?" for c in kept)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE Z_PK = ?", [*kept.values(), pk])

    def finish(self) -> None:
        for table, name in ENTITIES.items():
            self.conn.execute(
                "UPDATE Z_PRIMARYKEY SET Z_MAX = ? WHERE Z_NAME = ?", (self.next_pk[table] - 1, name)
            )


def _media_paths(jid: str, msg: Message) -> tuple[str, str]:
    """(ZMEDIALOCALPATH, path inside the group container) for a media message."""
    ext = Path(msg.media.filename).suffix or mimetypes.guess_extension(msg.media.mime or "") or ""
    name = uuid.uuid5(MEDIA_NAMESPACE, f"{jid}/{msg.key_id}").hex
    local = f"Media/{jid}/{name[0]}/{name[1]}/{name}{ext}"
    return local, f"{MEDIA_FS_PREFIX}/{local}"


def _add_chat(store: _Store, chat: Chat, backup: IOSBackup, phone_to_lid: dict[str, str],
              report: WriteReport) -> None:
    conn = store.conn
    # Individual chats: WhatsApp iOS keys these by @lid, not the phone-number JID Android
    # resolves to (confirmed against a real device 2026-09-24). Groups keep their @g.us JID
    # unchanged -- it's the same server-assigned ID on both platforms.
    jid = chat.jid
    if not chat.is_group:
        resolved = resolve_jid(chat.jid, phone_to_lid)
        if resolved != chat.jid:
            report.lid_resolved += 1
            jid = resolved
        else:
            report.lid_unresolved += 1

    expected_session_type = 1 if chat.is_group else 0
    row = conn.execute(
        "SELECT Z_PK, ZSESSIONTYPE FROM ZWACHATSESSION WHERE ZCONTACTJID = ?", (jid,)).fetchone()
    if row and row[1] != expected_session_type:
        # The target already has a same-JID session, but of a WhatsApp session kind (channel,
        # community, broadcast/status, or another type not yet seen -- confirmed against a real
        # device 2026-09-24 that types other than 0/1 exist and are populated) OATH hasn't
        # confirmed is safe to write plain chat messages into. Skip rather than guess, same
        # policy as an unconfirmed ZMESSAGETYPE.
        report.chats_skipped_unknown_session_type += 1
        report.messages_skipped_unknown_session_type += len(chat.messages)
        report.warnings.append(
            f"{jid}: target already has a session here of an unrecognized WhatsApp session "
            f"type ({row[1]}, expected {expected_session_type}); {len(chat.messages)} messages "
            "skipped here rather than risk writing into a channel/broadcast/other chat kind "
            "OATH hasn't confirmed")
        return
    if row:
        session = row[0]
        existing = {r[0] for r in conn.execute("SELECT ZSTANZAID FROM ZWAMESSAGE WHERE ZCHATSESSION = ?", (session,))}
        sort = conn.execute("SELECT COALESCE(MAX(ZSORT), 0) FROM ZWAMESSAGE WHERE ZCHATSESSION = ?", (session,)).fetchone()[0]
        report.chats_merged += 1
    else:
        session = store.insert("ZWACHATSESSION", {
            "ZCONTACTJID": jid,
            "ZPARTNERNAME": chat.subject,
            "ZSESSIONTYPE": 1 if chat.is_group else 0,
            "ZARCHIVED": int(chat.archived),
            "ZUNREADCOUNT": 0,
            "ZMESSAGECOUNTER": 0,
        })
        existing, sort = set(), 0
        report.chats_added += 1

    members: dict[str, int] = {}
    if chat.is_group:
        members = {r[0]: r[1] for r in conn.execute(
            "SELECT ZMEMBERJID, Z_PK FROM ZWAGROUPMEMBER WHERE ZCHATSESSION = ?", (session,))}

    last_pk, last_ms, added = None, 0, 0
    for msg in sorted(chat.messages, key=lambda m: m.timestamp_ms):
        if msg.key_id in existing:
            report.messages_skipped_existing += 1
            continue
        sort += 1
        member_pk = None
        if chat.is_group and not msg.from_me and msg.sender_jid:
            sender = resolve_jid(msg.sender_jid, phone_to_lid)
            if sender not in members:
                members[sender] = store.insert(
                    "ZWAGROUPMEMBER", {"ZCHATSESSION": session, "ZMEMBERJID": sender})
            member_pk = members[sender]

        mtype = TYPE_TO_IOS.get(msg.type)
        text = msg.text
        if mtype is None:
            mtype, text = 0, text or UNSUPPORTED_TEXT
            report.unconfirmed_type_fallback += 1
        date = ms_to_apple(msg.timestamp_ms)
        msg_pk = store.insert("ZWAMESSAGE", {
            "ZCHATSESSION": session,
            "ZISFROMME": int(msg.from_me),
            "ZMESSAGETYPE": mtype,
            "ZMESSAGESTATUS": 8 if msg.from_me else 0,
            "ZSORT": sort,
            "ZGROUPMEMBER": member_pk,
            "ZMESSAGEDATE": date,
            "ZSENTDATE": date if msg.from_me else None,
            "ZFROMJID": None if msg.from_me else jid,
            "ZTOJID": jid if msg.from_me else None,
            "ZSTANZAID": msg.key_id,
            "ZTEXT": text,
        })
        if msg.type in MEDIA_TYPES and msg.media:
            local, fs_path = _media_paths(jid, msg)
            if msg.media.source is not None and msg.media.source.is_file():
                backup.write_file(WA_DOMAIN, fs_path, msg.media.read())
                report.media_files += 1
            else:
                report.warnings.append(f"{jid}/{msg.key_id}: media file not found, placeholder only")
            media_pk = store.insert("ZWAMEDIAITEM", {
                "ZMESSAGE": msg_pk, "ZFILESIZE": msg.media.size, "ZMEDIALOCALPATH": local})
            store.update("ZWAMESSAGE", msg_pk, {"ZMEDIAITEM": media_pk})
        if msg.type is MsgType.STICKER:
            report.stickers_written += 1
        last_pk, last_ms, added = msg_pk, msg.timestamp_ms, added + 1
        report.messages_added += 1

    if added:
        total = conn.execute("SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZCHATSESSION = ?", (session,)).fetchone()[0]
        store.update("ZWACHATSESSION", session, {
            "ZMESSAGECOUNTER": total,
            "ZLASTMESSAGE": last_pk,
            "ZLASTMESSAGEDATE": ms_to_apple(last_ms),
        })


def inject(backup: IOSBackup, chats: list[Chat]) -> WriteReport:
    """Add chats to the backup's ChatStorage.sqlite. The backup is modified in place."""
    report = WriteReport()
    dropped_quotes = sum(1 for c in chats for m in c.messages if m.quoted)
    dropped_stars = sum(1 for c in chats for m in c.messages if m.starred)
    if dropped_quotes:
        report.warnings.append(f"{dropped_quotes} quoted-reply links are not carried over yet")
    if dropped_stars:
        report.warnings.append(f"{dropped_stars} starred flags are not carried over yet")

    phone_to_lid = load_phone_to_lid(backup)
    if not phone_to_lid:
        report.warnings.append(
            "no phone-to-@lid mapping found in the target backup's LID.sqlite; individual "
            "chats will be written under Android's phone-number JID and may not merge with "
            "matching contacts already on the iPhone")

    template = backup.read_file(WA_DOMAIN, CHAT_STORAGE)
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        db_path = Path(tmp) / CHAT_STORAGE
        db_path.write_bytes(template)
        conn = sqlite3.connect(db_path)
        try:
            store = _Store(conn)
            for chat in chats:
                _add_chat(store, chat, backup, phone_to_lid, report)
            store.finish()
            conn.commit()
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise SchemaError(f"integrity_check failed after injection: {integrity}")
            if report.lid_unresolved:
                report.warnings.append(
                    f"{report.lid_unresolved} individual chats had no @lid match in the target's LID.sqlite "
                    "and were written under the Android phone-number JID; may show as separate/new chats")
            if report.unconfirmed_type_fallback:
                report.warnings.append(
                    f"{report.unconfirmed_type_fallback} messages used a message type OATH hasn't confirmed "
                    "against a real device (contact cards, locations, documents); written as text with the "
                    "real file attached where applicable, so nothing is lost, but they won't render with "
                    "their native bubble style")
            if store.dropped_columns:
                report.warnings.append(f"columns absent from target schema were skipped: {sorted(store.dropped_columns)}")
        finally:
            conn.close()
        backup.write_file(WA_DOMAIN, CHAT_STORAGE, db_path.read_bytes())
    return report
