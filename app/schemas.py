from datetime import datetime

from pydantic import BaseModel


class IngestRequest(BaseModel):
    url: str
    force: bool = False
    allow_whisper: bool = True


class SegmentOut(BaseModel):
    idx: int
    start: float
    end: float
    text: str


class BattleOut(BaseModel):
    video_id: str
    url: str
    title: str
    channel: str | None
    duration: float | None
    status: str
    source: str | None
    language: str | None
    segment_count: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class TranscriptOut(BattleOut):
    segments: list[SegmentOut]


class EntryOut(BaseModel):
    id: int
    slug: str
    name: str
    kind: str


class MentionOut(BaseModel):
    id: int
    segment_idx: int
    start: float
    end: float
    char_start: int
    char_end: int
    alias: str
    status: str
    detector: str
    entry: EntryOut


class MentionsOut(BaseModel):
    video_id: str
    at: float | None = None
    count: int
    mentions: list[MentionOut]


class MentionStatusIn(BaseModel):
    status: str
