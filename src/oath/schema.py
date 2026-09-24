"""Content-free structure reports of real WhatsApp data, safe to share for schema comparison.

Reports contain table/column layouts, row counts and Core Data metadata only:
no messages, names, phone numbers or keys.
"""

from __future__ import annotations

import plistlib
import re
import sqlite3
import tempfile
from pathlib import Path

from oath.android.crypt import decrypt_crypt15
from oath.android.reader import BACKUP_NAME
from oath.ios.backup import WA_DOMAIN, IOSBackup
from oath.ios.reader import CHAT_STORAGE

JID_IN_PATH = re.compile(r"\d{6,}(?:-\d+)?@(?:s\.whatsapp\.net|g\.us|lid)")


def _describe(conn: sqlite3.Connection) -> list[str]:
    lines = [f"user_version: {conn.execute('PRAGMA user_version').fetchone()[0]}"]
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for table in tables:
        cols = ", ".join(f"{r[1]} {r[2]}" for r in conn.execute(f'PRAGMA table_info("{table}")'))
        count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        lines.append(f"{table} [{count} rows]: {cols}")
    return lines


def _distinct(conn: sqlite3.Connection, table: str, column: str) -> str:
    """Distinct values of a small enum-like column with counts, e.g. message types."""
    try:
        rows = conn.execute(
            f'SELECT "{column}", COUNT(*) FROM "{table}" GROUP BY "{column}" ORDER BY "{column}"').fetchall()
    except sqlite3.Error:
        return f"{table}.{column}: unavailable"
    return f"{table}.{column}: " + ", ".join(f"{v}={n}" for v, n in rows)


def android_schema(wa_root: Path, key_hex: str) -> str:
    plain = decrypt_crypt15(Path(wa_root, "Databases", BACKUP_NAME).read_bytes(), key_hex)
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        path = Path(tmp) / "msgstore.db"
        path.write_bytes(plain)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            lines = ["# Android msgstore.db", *_describe(conn),
                     _distinct(conn, "message", "message_type"),
                     _distinct(conn, "message", "status"),
                     _distinct(conn, "jid", "server")]
        finally:
            conn.close()
    return "\n".join(lines)


def ios_schema(backup_root: Path) -> str:
    backup = IOSBackup(backup_root)
    manifest = plistlib.loads((backup.root / "Manifest.plist").read_bytes()) if (backup.root / "Manifest.plist").is_file() else {}
    lines = [f"# iPhone backup (WhatsApp domain {WA_DOMAIN})",
             f"backup version: {manifest.get('Version')}", ""]
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        path = Path(tmp) / CHAT_STORAGE
        path.write_bytes(backup.read_file(WA_DOMAIN, CHAT_STORAGE))
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            lines += ["## ChatStorage.sqlite", *_describe(conn), ""]
            lines.append("## Z_PRIMARYKEY (Z_ENT, Z_NAME, Z_SUPER, Z_MAX)")
            lines += [str(r) for r in conn.execute("SELECT Z_ENT, Z_NAME, Z_SUPER, Z_MAX FROM Z_PRIMARYKEY ORDER BY Z_ENT")]
            row = conn.execute("SELECT Z_VERSION, Z_PLIST FROM Z_METADATA").fetchone()
            if row:
                meta = plistlib.loads(row[1]) if row[1] else {}
                lines += ["", f"## Z_METADATA version {row[0]}; plist keys: {sorted(meta)}"]
                hashes = meta.get("NSStoreModelVersionHashes", {})
                lines += [f"model hash {k}: {v.hex() if isinstance(v, bytes) else v}" for k, v in sorted(hashes.items())]
            lines += ["", "## enum-like columns",
                      _distinct(conn, "ZWAMESSAGE", "ZMESSAGETYPE"),
                      _distinct(conn, "ZWAMESSAGE", "ZMESSAGESTATUS"),
                      _distinct(conn, "ZWACHATSESSION", "ZSESSIONTYPE")]
        finally:
            conn.close()

    files = backup.list_files(WA_DOMAIN)
    lines += ["", f"## files in WhatsApp domain: {len(files)}"]
    shapes: dict[str, int] = {}
    for rel in files:
        shape = JID_IN_PATH.sub("<jid>", rel)
        shape = re.sub(r"[0-9a-fA-F-]{16,}", "<id>", shape)
        shapes[shape] = shapes.get(shape, 0) + 1
    lines += [f"{n:6d}  {s}" for s, n in sorted(shapes.items())[:200]]
    return "\n".join(lines)
