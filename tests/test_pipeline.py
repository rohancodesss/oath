import sqlite3

import pytest

from oath.android.crypt import DecryptError, decrypt_crypt15, encrypt_crypt15
from oath.android.reader import load_android
from oath.convert import android_to_ios
from oath.emulate.sample import ALICE, CAROL, fake_media_bytes, sample_chats
from oath.ios.backup import WA_DOMAIN, BackupError, IOSBackup
from oath.ios.reader import CHAT_STORAGE, load_ios
from oath.ios.writer import inject
from oath.model import MsgType


def flat(chats, *, ios=False):
    """Comparable view of chats. iOS drops filenames, quotes and starred flags for now."""
    out = []
    for c in chats:
        msgs = []
        for m in c.messages:
            media = None
            if m.media:
                media = (m.media.mime, m.media.size) if ios else (m.media.filename, m.media.mime, m.media.size)
            row = [m.key_id, m.from_me, m.timestamp_ms, m.type, m.text, m.sender_jid, media]
            if not ios:
                row += [m.quoted and (m.quoted.key_id, m.quoted.text), m.starred]
            msgs.append(tuple(row))
        out.append((c.jid, c.subject, c.archived, msgs))
    return out


def test_crypt15_roundtrip_and_wrong_key(key_hex):
    plain = b"SQLite format 3\x00" + b"x" * 500
    blob = encrypt_crypt15(plain, key_hex, iv=bytes(16))
    assert decrypt_crypt15(blob, key_hex) == plain
    with pytest.raises(DecryptError):
        decrypt_crypt15(blob, "ff" * 32)
    with pytest.raises(DecryptError):
        decrypt_crypt15(blob, "abc")


def test_android_reader_matches_sample(android_root, key_hex):
    result = load_android(android_root, key_hex)
    assert flat(result.chats) == flat(sample_chats())
    assert result.warnings == []


def test_android_reader_resolves_lid_sender(android_root, key_hex):
    group = load_android(android_root, key_hex).chats[1]
    senders = {m.sender_jid for m in group.messages if not m.from_me}
    assert CAROL in senders
    assert not any(s and s.endswith("@lid") for s in senders)


def test_android_media_bytes_readable(android_root, key_hex):
    image = load_android(android_root, key_hex).chats[0].messages[2]
    assert image.media.read() == fake_media_bytes("A3", 2048)


def test_inject_into_ios_and_read_back(ios_backup, android_root, key_hex):
    source = load_android(android_root, key_hex)
    report = inject(ios_backup, source.chats)
    assert report.messages_added == source.message_count
    assert report.media_files == source.media_count
    assert ios_backup.verify() == []

    back = load_ios(ios_backup)
    # DOCUMENT isn't a confirmed ZMESSAGETYPE (see ios/writer.py TYPE_TO_IOS), so it's written
    # as text with the real file still attached; reading back therefore reports MsgType.TEXT
    # for that one message. STICKER is confirmed (type 15) and round-trips as itself.
    expected = [(jid, subj, arch, [(*m[:3], MsgType.TEXT if m[3] is MsgType.DOCUMENT else m[3], *m[4:])
                                    for m in msgs])
                for jid, subj, arch, msgs in flat(source.chats, ios=True)]
    assert flat(back.chats, ios=True) == expected
    video = back.chats[1].messages[2]
    assert video.type is MsgType.VIDEO
    assert video.media.read() == fake_media_bytes("G3", 8192)


def test_sticker_writes_with_confirmed_type_and_real_webp_preserved(ios_backup, android_root, key_hex):
    source = load_android(android_root, key_hex)
    sticker = source.chats[0].messages[-1]
    assert sticker.type is MsgType.STICKER

    report = inject(ios_backup, source.chats)
    assert report.stickers_written == 1
    assert report.unconfirmed_type_fallback == 1  # only A6 (DOCUMENT); sticker is confirmed

    conn = sqlite3.connect(ios_backup.path_of(WA_DOMAIN, CHAT_STORAGE))
    mtype, path = conn.execute(
        "SELECT m.ZMESSAGETYPE, mi.ZMEDIALOCALPATH FROM ZWAMESSAGE m JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM "
        "WHERE m.ZSTANZAID = 'A7'").fetchone()
    assert mtype == 15  # confirmed against a real device (see ios/writer.py TYPE_TO_IOS)
    assert path.endswith(".webp")
    assert ios_backup.read_file(WA_DOMAIN, f"Message/{path}") == fake_media_bytes("A7", 512)


def test_skips_merge_into_unrecognized_session_type(ios_backup, android_root, key_hex):
    # Real devices carry WhatsApp session kinds beyond plain individual(0)/group(1) chats --
    # channels, communities, broadcast/status (confirmed against a real device 2026-09-24).
    # If a target session already exists under a chat's JID but with a different session type,
    # OATH must not guess how to write into it.
    conn = sqlite3.connect(ios_backup.path_of(WA_DOMAIN, CHAT_STORAGE))
    z_max, z_ent = conn.execute(
        "SELECT Z_MAX, Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = 'WAChatSession'").fetchone()
    pk = z_max + 1
    conn.execute(
        "INSERT INTO ZWACHATSESSION (Z_PK, Z_ENT, Z_OPT, ZCONTACTJID, ZSESSIONTYPE, ZMESSAGECOUNTER) "
        "VALUES (?, ?, 1, ?, 5, 0)", (pk, z_ent, ALICE))
    conn.execute("UPDATE Z_PRIMARYKEY SET Z_MAX = ? WHERE Z_NAME = 'WAChatSession'", (pk,))
    conn.commit()
    conn.close()

    source = load_android(android_root, key_hex)
    report = inject(ios_backup, source.chats)
    assert report.chats_skipped_unknown_session_type == 1
    assert any("unrecognized WhatsApp session type" in w for w in report.warnings)

    conn = sqlite3.connect(ios_backup.path_of(WA_DOMAIN, CHAT_STORAGE))
    session_type, counter = conn.execute(
        "SELECT ZSESSIONTYPE, ZMESSAGECOUNTER FROM ZWACHATSESSION WHERE ZCONTACTJID = ?", (ALICE,)).fetchone()
    assert session_type == 5
    assert counter == 0  # untouched: no Alice messages were written into the wrong-typed session


def test_core_data_counters_are_consistent(ios_backup, android_root, key_hex):
    inject(ios_backup, load_android(android_root, key_hex).chats)
    conn = sqlite3.connect(ios_backup.path_of(WA_DOMAIN, CHAT_STORAGE))
    entities = {"WAChatSession": "ZWACHATSESSION", "WAMessage": "ZWAMESSAGE",
                "WAMediaItem": "ZWAMEDIAITEM", "WAGroupMember": "ZWAGROUPMEMBER"}
    for name, table in entities.items():
        z_max, z_ent = conn.execute("SELECT Z_MAX, Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME = ?", (name,)).fetchone()
        top = conn.execute(f"SELECT MAX(Z_PK) FROM {table}").fetchone()[0]
        assert z_max == top
        assert {r[0] for r in conn.execute(f"SELECT DISTINCT Z_ENT FROM {table}")} == {z_ent}
    counts = dict(conn.execute("SELECT ZCONTACTJID, ZMESSAGECOUNTER FROM ZWACHATSESSION"))
    assert counts["15551230001@s.whatsapp.net"] == 7
    last = conn.execute(
        "SELECT m.ZSTANZAID FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.Z_PK = s.ZLASTMESSAGE "
        "WHERE s.ZCONTACTJID = '15551230001@s.whatsapp.net'").fetchone()[0]
    assert last == "A7"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_inject_is_idempotent(ios_backup, android_root, key_hex):
    chats = load_android(android_root, key_hex).chats
    inject(ios_backup, chats)
    again = inject(ios_backup, chats)
    assert again.messages_added == 0
    assert again.chats_added == 0 and again.chats_merged == len(chats)
    assert load_ios(ios_backup).message_count == sum(len(c.messages) for c in chats)


def test_existing_status_session_and_unrelated_files_survive(ios_backup, android_root, key_hex):
    before = ios_backup.read_file("HomeDomain", "Library/Preferences/com.example.unrelated.plist")
    inject(ios_backup, load_android(android_root, key_hex).chats)
    assert ios_backup.read_file("HomeDomain", "Library/Preferences/com.example.unrelated.plist") == before
    conn = sqlite3.connect(ios_backup.path_of(WA_DOMAIN, CHAT_STORAGE))
    assert conn.execute("SELECT COUNT(*) FROM ZWACHATSESSION WHERE ZCONTACTJID = 'status@broadcast'").fetchone()[0] == 1


def test_warnings_for_unsupported_features(ios_backup, android_root, key_hex):
    report = inject(ios_backup, load_android(android_root, key_hex).chats)
    assert any("quoted-reply" in w for w in report.warnings)
    assert any("starred" in w for w in report.warnings)


def test_convert_leaves_originals_untouched(tmp_path, android_root, ios_backup, key_hex):
    src_manifest = (ios_backup.root / "Manifest.db").read_bytes()
    out = tmp_path / "out"
    result = android_to_ios(android_root, key_hex, ios_backup.root, out)
    assert result.ok, result.problems
    assert (ios_backup.root / "Manifest.db").read_bytes() == src_manifest
    assert load_ios(ios_backup).message_count == 0
    assert load_ios(IOSBackup(out)).message_count == result.source.message_count


def test_convert_dry_run_writes_nothing(tmp_path, android_root, ios_backup, key_hex):
    result = android_to_ios(android_root, key_hex, ios_backup.root, tmp_path / "out", dry_run=True)
    assert result.write is None and not (tmp_path / "out").exists()
    assert result.source.message_count == 12


def test_convert_refuses_existing_output_and_encrypted_backup(tmp_path, android_root, ios_backup, key_hex):
    (tmp_path / "exists").mkdir()
    with pytest.raises(FileExistsError):
        android_to_ios(android_root, key_hex, ios_backup.root, tmp_path / "exists")

    import plistlib
    (ios_backup.root / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": True}))
    with pytest.raises(BackupError):
        android_to_ios(android_root, key_hex, ios_backup.root, tmp_path / "out2")


def test_verify_detects_tampering(ios_backup):
    path = ios_backup.path_of(WA_DOMAIN, "ContactsV2.sqlite")
    path.write_bytes(b"tampered")
    assert any("mismatch" in p for p in ios_backup.verify())


def test_schema_reports_contain_no_content(android_root, ios_backup, key_hex):
    from oath.schema import android_schema, ios_schema

    inject(ios_backup, load_android(android_root, key_hex).chats)
    android = android_schema(android_root, key_hex)
    ios = ios_schema(ios_backup.root)
    assert "message [12 rows]" in android and "ZWAMESSAGE [12 rows]" in ios
    for text in (android, ios):
        assert "Saturday" not in text and "15551230001" not in text and key_hex not in text
    assert "<jid>" in ios
