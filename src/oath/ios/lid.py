"""Resolve Android's phone-number-style JIDs to the @lid identifiers WhatsApp iOS
actually keys chats by (confirmed against a real device: ZWAZACCOUNT.ZIDENTIFIER is an
'<digits>@lid' string, ZPHONENUMBER is the plain digit phone number, LID.sqlite in the
WhatsApp app-group container). Only individual chats need this -- group JIDs (@g.us) are
the same numeric, server-assigned ID on both platforms and don't need translation.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from oath.ios.backup import WA_DOMAIN, IOSBackup

LID_SQLITE = "LID.sqlite"


def load_phone_to_lid(backup: IOSBackup) -> dict[str, str]:
    """{phone digits: '<digits>@lid'} from the target backup's own LID.sqlite.

    Returns {} if the file is missing or unreadable -- callers must treat that as
    'no mapping known' and fall back to the original JID, not as an error.
    """
    try:
        data = backup.read_file(WA_DOMAIN, LID_SQLITE)
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        path = Path(tmp) / LID_SQLITE
        path.write_bytes(data)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT ZPHONENUMBER, ZIDENTIFIER FROM ZWAZACCOUNT WHERE ZPHONENUMBER IS NOT NULL")
            for phone, identifier in rows:
                if phone and identifier:
                    mapping[phone] = identifier
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
    return mapping


def resolve_jid(android_jid: str, phone_to_lid: dict[str, str]) -> str:
    """Translate an Android-resolved JID to the iOS-native identifier where known."""
    if android_jid.endswith("@g.us"):
        return android_jid  # server-assigned group ID, same on both platforms
    digits = android_jid.split("@", 1)[0]
    return phone_to_lid.get(digits, android_jid)
