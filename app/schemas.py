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
