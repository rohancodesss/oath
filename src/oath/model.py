"""Platform-neutral chat model. Readers produce it, writers consume it."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MsgType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    LOCATION = "location"
    CONTACT = "contact"
    SYSTEM = "system"
    STICKER = "sticker"
    UNKNOWN = "unknown"


MEDIA_TYPES = frozenset({MsgType.IMAGE, MsgType.VIDEO, MsgType.AUDIO, MsgType.DOCUMENT, MsgType.STICKER})


@dataclass
class Media:
    filename: str
    mime: str | None
    size: int
    source: Path | None = None

    def read(self) -> bytes:
        if self.source is None:
            raise FileNotFoundError(f"no source file for {self.filename}")
        return self.source.read_bytes()


@dataclass
class Quote:
    key_id: str
    text: str | None = None


@dataclass
class Message:
    key_id: str
    from_me: bool
    timestamp_ms: int
    type: MsgType = MsgType.TEXT
    text: str | None = None
    sender_jid: str | None = None
    media: Media | None = None
    quoted: Quote | None = None
    starred: bool = False


@dataclass
class Chat:
    jid: str
    subject: str | None = None
    archived: bool = False
    messages: list[Message] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return self.jid.endswith("@g.us")


@dataclass
class ReadResult:
    chats: list[Chat]
    warnings: list[str] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return sum(len(c.messages) for c in self.chats)

    @property
    def media_count(self) -> int:
        return sum(1 for c in self.chats for m in c.messages if m.media)
