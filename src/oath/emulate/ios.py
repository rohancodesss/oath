"""Emulate an unencrypted iPhone backup of a phone with a freshly registered WhatsApp."""

from __future__ import annotations

import plistlib
import sqlite3
import tempfile
from pathlib import Path

from oath.ios.backup import WA_DOMAIN, IOSBackup
from oath.ios.reader import CHAT_STORAGE

DDL = """
CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER PRIMARY KEY, Z_NAME VARCHAR, Z_SUPER INTEGER, Z_MAX INTEGER);
CREATE TABLE Z_METADATA (Z_VERSION INTEGER PRIMARY KEY, Z_UUID VARCHAR(255), Z_PLIST BLOB);
CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZARCHIVED INTEGER,
    ZSESSIONTYPE INTEGER, ZMESSAGECOUNTER INTEGER, ZUNREADCOUNT INTEGER, ZLASTMESSAGE INTEGER,
    ZLASTMESSAGEDATE TIMESTAMP, ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR);
CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZISFROMME INTEGER,
    ZMESSAGETYPE INTEGER, ZMESSAGESTATUS INTEGER, ZSORT INTEGER, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER,
    ZMEDIAITEM INTEGER, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP, ZFROMJID VARCHAR, ZTOJID VARCHAR,
    ZSTANZAID VARCHAR, ZTEXT VARCHAR, ZPUSHNAME VARCHAR);
CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZMESSAGE INTEGER,
    ZFILESIZE INTEGER, ZMEDIALOCALPATH VARCHAR, ZTITLE VARCHAR);
CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCHATSESSION INTEGER,
    ZMEMBERJID VARCHAR, ZCONTACTNAME VARCHAR);
"""

# Entity numbers here are arbitrary; the writer always looks them up by name.
ENTITY_ROWS = [(1, "WAChatSession"), (2, "WAGroupMember"), (3, "WAMediaItem"), (4, "WAMessage")]


def _chat_storage_bytes(with_status_session: bool) -> bytes:
    with tempfile.TemporaryDirectory(prefix="oath-") as tmp:
        path = Path(tmp) / CHAT_STORAGE
        conn = sqlite3.connect(path)
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO Z_PRIMARYKEY (Z_ENT, Z_NAME, Z_SUPER, Z_MAX) VALUES (?, ?, 0, 0)", ENTITY_ROWS)
        conn.execute("INSERT INTO Z_METADATA VALUES (1, 'EMULATED-STORE-UUID', ?)",
                     (plistlib.dumps({"NSStoreModelVersionHashesVersion": 3}),))
        if with_status_session:
            conn.execute(
                "INSERT INTO ZWACHATSESSION (Z_PK, Z_ENT, Z_OPT, ZSESSIONTYPE, ZCONTACTJID, ZMESSAGECOUNTER) "
                "VALUES (1, 1, 1, 3, 'status@broadcast', 0)")
            conn.execute("UPDATE Z_PRIMARYKEY SET Z_MAX = 1 WHERE Z_NAME = 'WAChatSession'")
        conn.commit()
        conn.close()
        return path.read_bytes()


def build_ios_fixture(dest: Path, with_status_session: bool = True) -> IOSBackup:
    """Create <dest> as an unencrypted backup containing an empty, registered WhatsApp store."""
    root = Path(dest)
    root.mkdir(parents=True, exist_ok=True)
    (root / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False, "Version": "10.0"}))
    (root / "Status.plist").write_bytes(plistlib.dumps({"BackupState": "new", "IsFullBackup": True}))
    (root / "Info.plist").write_bytes(plistlib.dumps({"Device Name": "Emulated iPhone", "Product Version": "18.0"}))

    conn = sqlite3.connect(root / "Manifest.db")
    conn.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    conn.commit()
    conn.close()
    backup = IOSBackup(root)

    backup.write_file(WA_DOMAIN, CHAT_STORAGE, _chat_storage_bytes(with_status_session))
    backup.write_file(WA_DOMAIN, "ContactsV2.sqlite", b"emulated-contacts")
    backup.write_file("HomeDomain", "Library/Preferences/com.example.unrelated.plist",
                      plistlib.dumps({"untouched": True}))
    return backup

