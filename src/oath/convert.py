"""Android -> iPhone conversion orchestration. Originals are never modified."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from oath.android.reader import load_android
from oath.ios.backup import IOSBackup
from oath.ios.writer import WriteReport, inject
from oath.model import ReadResult


@dataclass
class ConvertResult:
    source: ReadResult
    write: WriteReport | None = None
    output: Path | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def android_to_ios(wa_root: Path, key_hex: str, ios_backup: Path, out_dir: Path | None,
                   dry_run: bool = False) -> ConvertResult:
    """Read the Android backup, then inject into a *copy* of the iPhone backup at out_dir."""
    IOSBackup(ios_backup)  # validates: exists, unencrypted
    source = load_android(wa_root, key_hex)
    result = ConvertResult(source=source)
    if dry_run:
        return result
    if out_dir is None:
        raise ValueError("out_dir is required unless dry_run is set")
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"{out_dir} already exists; refusing to overwrite")
    shutil.copytree(ios_backup, out_dir)

    target = IOSBackup(out_dir)
    result.write = inject(target, source.chats)
    result.output = out_dir
    result.problems = target.verify()

    # Message-conservation check via the write report's own accounting, not a re-read: load_ios()
    # only recognises ZSESSIONTYPE 0/1 (confirmed against a real device 2026-09-24 that other
    # session kinds -- channels, communities, broadcast/status -- legitimately exist and hold
    # messages), so it undercounts a real backup's total regardless of anything OATH wrote.
    w = result.write
    expected_written = source.message_count - w.messages_skipped_existing - w.messages_skipped_unknown_session_type
    if w.messages_added != expected_written:
        result.problems.append(
            f"verification: {w.messages_added} messages written, expected {expected_written} "
            f"(= {source.message_count} source - {w.messages_skipped_existing} already present "
            f"- {w.messages_skipped_unknown_session_type} skipped for unrecognized session types)")
    return result
