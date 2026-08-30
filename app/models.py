from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BattleStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class TranscriptSource:
    YOUTUBE_MANUAL = "youtube_manual"
    YOUTUBE_AUTO = "youtube_auto"
    WHISPER_LOCAL = "whisper_local"
    WHISPER_GROQ = "whisper_groq"


class Battle(SQLModel, table=True):
    video_id: str = Field(primary_key=True)
    url: str
    title: str = ""
    channel: str | None = None
    duration: float | None = None

    status: str = Field(default=BattleStatus.PENDING, index=True)
    source: str | None = None
    language: str | None = None
    segment_count: int = 0
    error: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Segment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    video_id: str = Field(index=True, foreign_key="battle.video_id")
    idx: int
    start: float
    end: float
    text: str
    # Whisper drops speech under music, so a battle can mix sources: most
    # segments from Whisper, the gaps back-filled from YouTube captions.
    source: str | None = None


class EntryKind:
    PERSON = "person"
    EVENT = "event"
    PLACE = "place"
    GROUP = "group"
    WORK = "work"
    CONCEPT = "concept"


class MentionStatus:
    DETECTED = "detected"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class MentionDetector:
    GLOSSARY = "glossary"
    HUMAN = "human"


class Entry(SQLModel, table=True):
    """A named thing the companion can mark in a transcript."""

    __tablename__ = "glossary_entry"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    kind: str = Field(index=True)
    blurb: str = ""


class Alias(SQLModel, table=True):
    __tablename__ = "glossary_alias"

    id: int | None = Field(default=None, primary_key=True)
    entry_id: int = Field(index=True, foreign_key="glossary_entry.id")
    # Lowercased unique key so "GL" and "gl" cannot point at two people.
    norm: str = Field(index=True, unique=True)
    label: str


class Mention(SQLModel, table=True):
    """One glossary hit inside a stored segment."""

    id: int | None = Field(default=None, primary_key=True)
    video_id: str = Field(index=True, foreign_key="battle.video_id")
    segment_idx: int
    start: float
    end: float
    entry_id: int = Field(index=True, foreign_key="glossary_entry.id")
    alias: str
    char_start: int
    char_end: int
    detector: str = MentionDetector.GLOSSARY
    status: str = Field(default=MentionStatus.DETECTED, index=True)
    line_gloss: str | None = None
