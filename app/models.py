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
