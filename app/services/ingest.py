"""Tiered ingest: YouTube captions when available, Whisper when not."""

import logging

from sqlmodel import Session, delete, select

from app.config import settings
from app.db import engine
from app.models import Battle, BattleStatus, Segment, utcnow
from app.services import captions as caption_parser
from app.services import youtube
from app.services.transcribe import transcribe

logger = logging.getLogger(__name__)


class IngestError(RuntimeError):
    pass


def _upsert_battle(session: Session, video_id: str, info: dict) -> Battle:
    battle = session.get(Battle, video_id)
    if battle is None:
        battle = Battle(video_id=video_id, url=youtube.watch_url(video_id))

    battle.title = info.get("title") or battle.title
    battle.channel = info.get("uploader") or info.get("channel") or battle.channel
    battle.duration = info.get("duration") or battle.duration
    battle.status = BattleStatus.PROCESSING
    battle.error = None
    battle.updated_at = utcnow()

    session.add(battle)
    session.commit()
    session.refresh(battle)
    return battle


def _replace_segments(session: Session, video_id: str, segments: list[dict]) -> None:
    session.exec(delete(Segment).where(Segment.video_id == video_id))
    for index, segment in enumerate(segments):
        session.add(
            Segment(
                video_id=video_id,
                idx=index,
                start=segment["start"],
                end=segment["end"],
                text=segment["text"],
            )
        )
    session.commit()


def _resolve_from_captions(info: dict) -> tuple[list[dict], str, str] | None:
    if not settings.use_youtube_captions:
        return None

    track = youtube.pick_caption_track(info)
    if track is None:
        return None

    logger.info("Using YouTube captions (%s, %s, %s)", track.lang, track.ext, track.source)
    raw = youtube.download_caption(track)
    segments = caption_parser.parse_caption_payload(raw, track.ext)

    if not segments:
        logger.warning("Caption track %s parsed to zero segments", track.lang)
        return None

    return segments, track.lang, track.source


def ingest_battle(
    url_or_id: str,
    *,
    force: bool = False,
    allow_whisper: bool = True,
    info: dict | None = None,
) -> Battle:
    """Ingest a battle. Pass `info` to reuse metadata already fetched by the caller."""
    video_id = youtube.extract_video_id(url_or_id)

    with Session(engine) as session:
        existing = session.get(Battle, video_id)
        if existing and existing.status == BattleStatus.READY and not force:
            logger.info("Battle %s already ingested; skipping", video_id)
            return existing

        if info is None:
            info = youtube.fetch_info(video_id)
        battle = _upsert_battle(session, video_id, info)

        try:
            resolved = _resolve_from_captions(info)

            if resolved is None:
                if not allow_whisper or settings.transcription_backend.lower() == "none":
                    raise IngestError(
                        "No usable YouTube captions and Whisper transcription is disabled."
                    )
                logger.info("No captions for %s; transcribing audio", video_id)
                audio_path = youtube.download_audio(video_id)
                result = transcribe(audio_path)
                segments, language, source = (
                    result.segments,
                    result.language,
                    result.source,
                )
            else:
                segments, language, source = resolved

            if not segments:
                raise IngestError("Transcription produced no segments.")

            _replace_segments(session, video_id, segments)

            battle.status = BattleStatus.READY
            battle.source = source
            battle.language = language
            battle.segment_count = len(segments)
            battle.error = None

        except Exception as exc:  # noqa: BLE001 - persist failure for the API to report
            logger.exception("Ingest failed for %s", video_id)
            battle.status = BattleStatus.FAILED
            battle.error = str(exc)

        battle.updated_at = utcnow()
        session.add(battle)
        session.commit()
        session.refresh(battle)
        return battle


def get_segments(session: Session, video_id: str) -> list[Segment]:
    statement = select(Segment).where(Segment.video_id == video_id).order_by(Segment.idx)
    return list(session.exec(statement))
