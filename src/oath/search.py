"""Build and query a local full-text search database over a ReadResult.

Works against either platform's reader output, so search covers whatever OATH
can already read -- no separate parsing logic to keep in sync.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from oath.model import ReadResult


@dataclass
class Hit:
    date: str
    chat: str
    sender: str
    from_me: bool
    text: str


def build_search_db(result: ReadResult, db_path: Path) -> int:
    """(Re)build an FTS5 search database at db_path from a ReadResult. Returns rows indexed."""
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            chat_name TEXT,
            chat_jid TEXT,
            sender TEXT,
            from_me INTEGER,
            timestamp_ms INTEGER,
            date_text TEXT,
            text_data TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            text_data, chat_name, sender,
            content='messages', content_rowid='id'
        );
        CREATE TRIGGER messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text_data, chat_name, sender)
            VALUES (new.id, new.text_data, new.chat_name, new.sender);
        END;
    """)

    rows = []
    row_id = 0
    for chat in result.chats:
        chat_name = chat.subject or chat.jid
        for msg in chat.messages:
            row_id += 1
            sender = "me" if msg.from_me else (msg.sender_jid or chat.jid)
            date_text = datetime.fromtimestamp(msg.timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            rows.append((row_id, chat_name, chat.jid, sender, int(msg.from_me), msg.timestamp_ms,
                         date_text, msg.text or ""))

    conn.executemany(
        "INSERT INTO messages (id, chat_name, chat_jid, sender, from_me, timestamp_ms, date_text, text_data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def list_chats(db_path: Path) -> list[tuple[str, int]]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT chat_name, COUNT(*) n FROM messages GROUP BY chat_name ORDER BY n DESC"
        ).fetchall()
    finally:
        conn.close()


def search(db_path: Path, query: str, chat: str | None = None, limit: int = 300) -> list[Hit]:
    if not query:
        return []
    conn = sqlite3.connect(db_path)
    try:
        sql = """
            SELECT m.date_text, m.chat_name, m.sender, m.text_data, m.from_me
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ?
        """
        params: list = [query]
        if chat:
            sql += " AND m.chat_name = ?"
            params.append(chat)
        sql += " ORDER BY m.timestamp_ms DESC LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []
        return [Hit(date=r[0], chat=r[1], sender=r[2], text=r[3], from_me=bool(r[4])) for r in rows]
    finally:
        conn.close()
