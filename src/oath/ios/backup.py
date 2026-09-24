"""An unencrypted iTunes/Finder-style iPhone backup: Manifest.db plus hashed blobs."""

from __future__ import annotations

import hashlib
import plistlib
import sqlite3
from pathlib import Path, PurePosixPath

WA_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"

FLAG_FILE = 1
FLAG_DIR = 2
MODE_FILE = 33188
MODE_DIR = 16877


class BackupError(Exception):
    pass


def file_id(domain: str, relative_path: str) -> str:
    return hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()


def mbfile_plist(relative_path: str, flags: int, size: int = 0, digest: bytes | None = None) -> bytes:
    """A simplified NSKeyedArchiver MBFile record.

    Digest, when present, must be a UID reference to its own $objects entry, not the raw
    bytes inlined directly into the record dict. Inlining it is what plistlib reads back fine
    (and what OATH's own verify()/_record() correctly resolve), but Apple's real device-side
    NSKeyedUnarchiver rejects it during restore: decodeObjectForKey: requires an object
    reference for every key, and a bare data literal there fails with "value for key (Digest)
    is not an object" -- confirmed against a real device 2026-09-24, where this was the actual
    root cause of a backup-wide "ErrorCode 205: Multiple errors downloading files" restore
    failure affecting every one of the 11543 files OATH had written (every injected WhatsApp
    message attachment/media file, plus the rewritten ChatStorage.sqlite itself).

    File records (not directories) also need ProtectionClass set. Every real file in a real
    WhatsApp backup carries ProtectionClass=3 (NSFileProtectionCompleteUntilFirstUserAuthentication;
    confirmed by sampling 500 real WhatsApp files on a real device 2026-09-24 -- all 500 use
    class 3, while real WhatsApp directory records all use class 0). Without it, the device's
    restore-time staging defaults to setting protection class 0 on the file, which fails with
    POSIX EINVAL and surfaces as the same "Multiple errors downloading files" restore failure
    -- confirmed against a real device 2026-09-24 after fixing the Digest issue above still
    reproduced this failure, traced via the device's own syslog to "Setting protection class
    to 0" immediately preceding "Failed to open ... NSPOSIXErrorDomain Code=22" for every
    OATH-written file.
    """
    record = {
        "$class": plistlib.UID(2),
        "RelativePath": plistlib.UID(3),
        "Size": size,
        "Mode": MODE_DIR if flags == FLAG_DIR else MODE_FILE,
        "Flags": flags,
        "UserID": 501,
        "GroupID": 501,
    }
    if flags == FLAG_FILE:
        record["ProtectionClass"] = 3
    objects = ["$null", record, {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]}, relative_path]
    if digest is not None:
        record["Digest"] = plistlib.UID(len(objects))
        objects.append(digest)
    archive = {
        "$archiver": "NSKeyedArchiver",
        "$version": 100000,
        "$top": {"root": plistlib.UID(1)},
        "$objects": objects,
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


def _record(blob: bytes) -> dict:
    """The file record from an NSKeyedArchiver plist, with UID field references resolved.

    plistlib does not dereference NSKeyedArchiver UID objects (confirmed against a real
    device's Manifest.db 2026-09-24: real records store Digest as objects[N] and the record's
    'Digest' key as a bare UID(N) pointer to it, unlike OATH's own hand-built fixture plists,
    which inline the digest bytes directly at the same fixed index).
    """
    objects = plistlib.loads(blob)["$objects"]
    record = objects[1]
    return {k: (objects[v] if isinstance(v, plistlib.UID) else v) for k, v in record.items()}


class IOSBackup:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not (self.root / "Manifest.db").is_file():
            raise BackupError(f"no Manifest.db in {self.root}")
        manifest = self.root / "Manifest.plist"
        if manifest.is_file() and plistlib.loads(manifest.read_bytes()).get("IsEncrypted"):
            raise BackupError("encrypted backups are not supported yet; create an unencrypted backup")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.root / "Manifest.db")

    def blob_path(self, fid: str) -> Path:
        return self.root / fid[:2] / fid

    def has(self, domain: str, relative_path: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM Files WHERE fileID = ?", (file_id(domain, relative_path),)
            ).fetchone()
        return row is not None

    def path_of(self, domain: str, relative_path: str) -> Path:
        path = self.blob_path(file_id(domain, relative_path))
        if not path.is_file():
            raise BackupError(f"missing in backup: {domain}/{relative_path}")
        return path

    def read_file(self, domain: str, relative_path: str) -> bytes:
        return self.path_of(domain, relative_path).read_bytes()

    def write_file(self, domain: str, relative_path: str, data: bytes) -> None:
        """Add or replace a file, keeping the manifest, size and digest consistent."""
        fid = file_id(domain, relative_path)
        blob = self.blob_path(fid)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(data)
        record = mbfile_plist(relative_path, FLAG_FILE, len(data), hashlib.sha1(data).digest())
        with self._conn() as conn:
            self._ensure_dirs(conn, domain, relative_path)
            conn.execute(
                "INSERT OR REPLACE INTO Files (fileID, domain, relativePath, flags, file) VALUES (?, ?, ?, ?, ?)",
                (fid, domain, relative_path, FLAG_FILE, record),
            )

    def _ensure_dirs(self, conn: sqlite3.Connection, domain: str, relative_path: str) -> None:
        parents = [str(p) for p in PurePosixPath(relative_path).parents if str(p) != "."]
        for parent in [*parents, ""]:
            fid = file_id(domain, parent)
            conn.execute(
                "INSERT OR IGNORE INTO Files (fileID, domain, relativePath, flags, file) VALUES (?, ?, ?, ?, ?)",
                (fid, domain, parent, FLAG_DIR, mbfile_plist(parent, FLAG_DIR)),
            )

    def list_files(self, domain: str) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT relativePath FROM Files WHERE domain = ? AND flags = ? ORDER BY relativePath",
                (domain, FLAG_FILE),
            ).fetchall()
        return [r[0] for r in rows]

    def repair_metadata(self) -> list[str]:
        """Patch Size/Digest in place for records that don't match the real bytes on disk.

        Some files in a real backup are captured mid-write (active SQLite journals/caches),
        so Manifest.db's recorded Size can legitimately drift from what's actually on disk.
        OATH's own verify() treats this as informational, but Apple's real device-side restore
        validates it strictly and refuses the *whole* backup as corrupt over even one such
        file (confirmed against a real device 2026-09-24: a backup with pre-existing,
        unrelated-to-WhatsApp size mismatches failed "Could not restore ... because the backup
        was corrupt" in Finder). This patches only the Size/Digest fields of the existing
        NSKeyedArchiver record in place -- it must NOT rebuild the record from scratch (an
        earlier version of this method did, via mbfile_plist(), and silently dropped fields
        like ProtectionClass/ExtendedAttributes/InodeNumber that the device's own restore
        needs to correctly stage certain files; that caused restore to fail differently, with
        a linkat() "No such file or directory" error on the file whose record had been rebuilt
        -- confirmed against a real device 2026-09-24). It never changes file content. Returns
        the list of repaired relativePaths.
        """
        repaired = []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT fileID, domain, relativePath, file FROM Files WHERE flags = ?", (FLAG_FILE,)
            ).fetchall()
            for fid, domain, rel, blob in rows:
                path = self.blob_path(fid)
                if not path.is_file():
                    continue
                data = path.read_bytes()
                record = _record(blob)
                real_digest = hashlib.sha1(data).digest()
                if record.get("Size") == len(data) and record.get("Digest", real_digest) == real_digest:
                    continue
                archive = plistlib.loads(blob)
                objects = archive["$objects"]
                raw_record = objects[1]
                raw_record["Size"] = len(data)
                if "Digest" in raw_record:
                    digest_ref = raw_record["Digest"]
                    if isinstance(digest_ref, plistlib.UID):
                        objects[digest_ref] = real_digest
                    else:
                        raw_record["Digest"] = real_digest
                new_blob = plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)
                conn.execute("UPDATE Files SET file = ? WHERE fileID = ?", (new_blob, fid))
                repaired.append(f"{domain}/{rel}")
        return repaired

    def remove_file(self, domain: str, relative_path: str) -> None:
        """Drop a file from the manifest and delete its blob, if present.

        For files the device's own restore can't stage even after repair_metadata() (confirmed
        against a real device 2026-09-24: a linkat() staging failure on a third-party app's
        internal cache file, unrelated to WhatsApp, blocked the whole restore) -- dropping the
        entry means the restore simply skips it rather than failing outright. Safe only for
        files whose loss is a non-issue (regenerable app caches), never for WhatsApp's own data.
        """
        fid = file_id(domain, relative_path)
        with self._conn() as conn:
            conn.execute("DELETE FROM Files WHERE fileID = ?", (fid,))
        blob = self.blob_path(fid)
        if blob.is_file():
            blob.unlink()

    def verify(self) -> list[str]:
        """Return a list of integrity problems; empty means consistent."""
        problems = []
        with self._conn() as conn:
            rows = conn.execute("SELECT fileID, domain, relativePath, flags, file FROM Files").fetchall()
        for fid, domain, rel, flags, blob in rows:
            if fid != file_id(domain, rel):
                problems.append(f"{domain}/{rel}: fileID does not match domain and path")
            if flags != FLAG_FILE:
                continue
            path = self.blob_path(fid)
            if not path.is_file():
                problems.append(f"{domain}/{rel}: blob missing")
                continue
            data = path.read_bytes()
            record = _record(blob)
            if record.get("Size") != len(data):
                problems.append(f"{domain}/{rel}: size mismatch")
            # Many real records carry no Digest at all (confirmed against a real device's
            # Manifest.db 2026-09-24 -- the large majority of files there have no Digest key);
            # there is nothing to check in that case, so only flag an actual mismatch.
            if "Digest" in record and record["Digest"] != hashlib.sha1(data).digest():
                problems.append(f"{domain}/{rel}: digest mismatch")
        return problems
