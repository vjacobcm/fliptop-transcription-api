import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlmodel import Session, select

from app.db import get_session
from app.models import Battle, BattleStatus, Entry, Mention, MentionStatus, Segment
from app.schemas import (
    BattleOut,
    EntryOut,
    IngestRequest,
    MentionOut,
    MentionStatusIn,
    MentionsOut,
    SegmentOut,
    TranscriptOut,
)
from app.services import youtube
from app.services.glossary import mentions_for
from app.services.ingest import get_segments, ingest_battle
from app.subtitles import to_srt, to_vtt

logger = logging.getLogger(__name__)
router = APIRouter()

FORMAT_PATTERN = "^(json|srt|vtt|text)$"
STATUS_PATTERN = "^(pending|processing|ready|failed)$"


def resolve_video_id(url_or_id: str) -> str:
    try:
        return youtube.extract_video_id(url_or_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def render_transcript(battle: Battle, segments: list[Segment], fmt: str):
    if fmt == "srt":
        return PlainTextResponse(to_srt(segments), media_type="text/plain")
    if fmt == "vtt":
        return PlainTextResponse(to_vtt(segments), media_type="text/vtt")
    if fmt == "text":
        return PlainTextResponse(
            "\n".join(segment.text for segment in segments), media_type="text/plain"
        )

    return TranscriptOut(
        **battle.model_dump(),
        segments=[SegmentOut(**segment.model_dump()) for segment in segments],
    )


def stored_transcript(session: Session, video_id: str, fmt: str):
    battle = session.get(Battle, video_id)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not ingested")

    segments = get_segments(session, video_id)
    if not segments:
        raise HTTPException(
            status_code=409,
            detail=f"No segments stored (status={battle.status}, error={battle.error})",
        )

    return render_transcript(battle, segments, fmt)


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/battles", response_model=list[BattleOut])
def list_battles(
    response: Response,
    status: str | None = Query(None, pattern=STATUS_PATTERN),
    source: str | None = Query(None, description="e.g. youtube_auto, whisper_local"),
    channel: str | None = Query(None, description="Case-insensitive substring match"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> list[Battle]:
    filters = []
    if status:
        filters.append(Battle.status == status)
    if source:
        filters.append(Battle.source == source)
    if channel:
        filters.append(Battle.channel.ilike(f"%{channel}%"))

    total = session.exec(
        select(func.count()).select_from(Battle).where(*filters)
    ).one()
    response.headers["X-Total-Count"] = str(total)

    statement = (
        select(Battle)
        .where(*filters)
        .order_by(Battle.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.exec(statement))


@router.get("/youtube/{video_id}/captions")
def probe_captions(video_id: str) -> dict:
    """Inspect which caption tracks YouTube exposes, without ingesting."""
    resolved = resolve_video_id(video_id)
    try:
        info = youtube.fetch_info(resolved)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"yt-dlp failed: {exc}") from exc

    tracks = youtube.list_caption_tracks(info)
    chosen = youtube.pick_caption_track(info)

    return {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "available": tracks,
        "would_use": None
        if chosen is None
        else {"lang": chosen.lang, "ext": chosen.ext, "source": chosen.source},
    }


@router.post("/battles/ingest", response_model=BattleOut)
def ingest(
    body: IngestRequest,
    background_tasks: BackgroundTasks,
    wait: bool = Query(True, description="Run inline; set false to queue in background"),
    session: Session = Depends(get_session),
) -> Battle:
    video_id = resolve_video_id(body.url)

    if wait:
        battle = ingest_battle(
            body.url, force=body.force, allow_whisper=body.allow_whisper
        )
        if battle.status == BattleStatus.FAILED:
            raise HTTPException(status_code=502, detail=battle.error or "Ingest failed")
        return battle

    background_tasks.add_task(
        ingest_battle, body.url, force=body.force, allow_whisper=body.allow_whisper
    )

    existing = session.get(Battle, video_id)
    if existing:
        return existing

    queued = Battle(
        video_id=video_id,
        url=youtube.watch_url(video_id),
        status=BattleStatus.PENDING,
    )
    session.add(queued)
    session.commit()
    session.refresh(queued)
    return queued


@router.get("/glossary", response_model=list[EntryOut])
def list_glossary(session: Session = Depends(get_session)) -> list[Entry]:
    return list(session.exec(select(Entry).order_by(Entry.kind, Entry.name)))


@router.get("/battles/{video_id}", response_model=BattleOut)
def get_battle(video_id: str, session: Session = Depends(get_session)) -> Battle:
    battle = session.get(Battle, video_id)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not ingested")
    return battle


@router.get("/battles/{video_id}/transcript")
def get_transcript(
    video_id: str,
    format: str = Query("json", pattern=FORMAT_PATTERN),
    session: Session = Depends(get_session),
):
    return stored_transcript(session, video_id, format)


def _mention_out(mention: Mention, entry) -> MentionOut:
    return MentionOut(
        id=mention.id,
        segment_idx=mention.segment_idx,
        start=mention.start,
        end=mention.end,
        char_start=mention.char_start,
        char_end=mention.char_end,
        alias=mention.alias,
        status=mention.status,
        detector=mention.detector,
        entry=EntryOut(
            id=entry.id, slug=entry.slug, name=entry.name, kind=entry.kind
        ),
    )


@router.get("/battles/{video_id}/mentions", response_model=MentionsOut)
def get_mentions(
    video_id: str,
    at: float | None = Query(
        None, description="Playback time in seconds; only mentions covering that instant"
    ),
    include_rejected: bool = Query(False),
    session: Session = Depends(get_session),
) -> MentionsOut:
    battle = session.get(Battle, video_id)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not ingested")

    rows = mentions_for(
        session, video_id, at=at, include_rejected=include_rejected
    )
    packed = [_mention_out(mention, entry) for mention, entry in rows]
    return MentionsOut(
        video_id=video_id, at=at, count=len(packed), mentions=packed
    )


@router.patch("/mentions/{mention_id}", response_model=MentionOut)
def set_mention_status(
    mention_id: int,
    body: MentionStatusIn,
    session: Session = Depends(get_session),
) -> MentionOut:
    if body.status not in (
        MentionStatus.DETECTED,
        MentionStatus.CONFIRMED,
        MentionStatus.REJECTED,
    ):
        raise HTTPException(
            status_code=400,
            detail="status must be detected, confirmed, or rejected",
        )

    mention = session.get(Mention, mention_id)
    if mention is None:
        raise HTTPException(status_code=404, detail="Mention not found")

    entry = session.get(Entry, mention.entry_id)
    mention.status = body.status
    session.add(mention)
    session.commit()
    session.refresh(mention)
    return _mention_out(mention, entry)


@router.delete("/battles/{video_id}")
def delete_battle(video_id: str, session: Session = Depends(get_session)) -> dict:
    battle = session.get(Battle, video_id)
    if battle is None:
        raise HTTPException(status_code=404, detail="Battle not ingested")

    for mention in session.exec(select(Mention).where(Mention.video_id == video_id)):
        session.delete(mention)
    for segment in get_segments(session, video_id):
        session.delete(segment)
    session.delete(battle)
    session.commit()
    return {"deleted": video_id}
