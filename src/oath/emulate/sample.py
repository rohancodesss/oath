"""A small, deterministic set of conversations covering the cases the converters must handle."""

from __future__ import annotations

import hashlib

from oath.model import Chat, Media, Message, MsgType, Quote

BASE_MS = 1_768_000_000_000  # 2026-01-09
ALICE = "15551230001@s.whatsapp.net"
BOB = "15551230002@s.whatsapp.net"
CAROL = "15551230003@s.whatsapp.net"
GROUP = "15551230001-1700000000@g.us"

DEFAULT_KEY_HEX = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


def fake_media_bytes(key_id: str, size: int) -> bytes:
    """Deterministic pseudo content so tests can compare bytes after a round trip."""
    out = b""
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{key_id}:{counter}".encode()).digest()
        counter += 1
    return out[:size]


def _t(minutes: int) -> int:
    return BASE_MS + minutes * 60_000


def sample_chats() -> list[Chat]:
    alice = Chat(jid=ALICE, subject=None, messages=[
        Message("A1", False, _t(0), text="Hey! Are we still on for Saturday?"),
        Message("A2", True, _t(1), text="Yes 🙌\nSee you at 6", starred=True),
        Message("A3", False, _t(2), MsgType.IMAGE, text="the venue",
                media=Media("IMG-20260109-WA0001.jpg", "image/jpeg", 2048)),
        Message("A4", True, _t(3), text="Looks great", quoted=Quote("A3", "the venue")),
        Message("A5", False, _t(4), MsgType.AUDIO,
                media=Media("PTT-20260109-WA0002.ogg", "audio/ogg", 1024)),
        Message("A6", True, _t(5), MsgType.DOCUMENT, text="tickets",
                media=Media("tickets.pdf", "application/pdf", 4096)),
        Message("A7", False, _t(6), MsgType.STICKER,
                media=Media("STK-20260109-WA0004.webp", "image/webp", 512)),
    ])
    group = Chat(jid=GROUP, subject="Weekend plans ✨", messages=[
        Message("G1", True, _t(10), text="Welcome everyone"),
        Message("G2", False, _t(11), text="Thanks!", sender_jid=BOB),
        Message("G3", False, _t(12), MsgType.VIDEO, text="clip",
                sender_jid=CAROL, media=Media("VID-20260109-WA0003.mp4", "video/mp4", 8192)),
        Message("G4", False, _t(13), text="日本語のテスト and ünïcode", sender_jid=BOB),
    ])
    archived = Chat(jid=BOB, subject=None, archived=True, messages=[
        Message("B1", True, _t(30), text="old thread"),
    ])
    return [alice, group, archived]
