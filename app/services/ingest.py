"""Tiered ingest: YouTube captions when available, Whisper when not."""

import logging

from sqlmodel import Session, delete, select

from app.config import settings
from app.db import engine
from app.models import Battle, BattleStatus, Mention, Segment, TranscriptSource, utcnow
from app.services import captions as caption_parser
from app.services import youtube
from app.services.glossary import annotate_battle
from app.services.merge import drop_hollow, fill_gaps
from app.services.titles import whisper_prompt
from app.services.transcribe import clean_segments, transcribe

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


def _replace_segments(
    session: Session, video_id: str, segments: list[dict], source: str | None = None
) -> None:
    session.exec(delete(Mention).where(Mention.video_id == video_id))
    session.exec(delete(Segment).where(Segment.video_id == video_id))
    for index, segment in enumerate(segments):
        session.add(
            Segment(
                video_id=video_id,
                idx=index,
                start=segment["start"],
                end=segment["end"],
                text=segment["text"],
                source=segment.get("source") or source,
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


def _patch_whisper_gaps(
    segments: list[dict], info: dict, source: str, duration: float | None
) -> list[dict]:
    """Back-fill the stretches Whisper dropped using YouTube's caption track."""
    segments = clean_segments(segments, settings.whisper_initial_prompt)
    segments, hollow = drop_hollow(segments)
    if hollow:
        logger.info("Opened %d hollow Whisper windows for caption fill", hollow)

    if not settings.fill_whisper_gaps or not settings.use_youtube_captions:
        return segments

    track = youtube.pick_caption_track(info)
    if track is None:
        return segments

    try:
        raw = youtube.download_caption(track)
        captions = caption_parser.parse_caption_payload(raw, track.ext)
    except Exception:  # noqa: BLE001 - a missing patch must not fail the ingest
        logger.warning("Could not fetch captions to patch gaps", exc_info=True)
        return segments

    patched, borrowed = fill_gaps(
        segments,
        captions,
        duration=duration,
        primary_source=source,
        filler_source=track.source,
    )
    if borrowed:
        logger.info("Recovered %d segments Whisper dropped", borrowed)

    return patched


def ingest_battle(
    url_or_id: str,
    *,
    force: bool = False,
    allow_whisper: bool = True,
    prefer_whisper: bool = False,
    info: dict | None = None,
) -> Battle:
    """Ingest a battle. Pass `info` to reuse metadata already fetched by the caller.

    `prefer_whisper` skips the YouTube caption tiers entirely, which is how a
    battle already stored from auto-captions gets upgraded.
    """
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
            resolved = None if prefer_whisper else _resolve_from_captions(info)

            if resolved is None:
                if not allow_whisper or settings.transcription_backend.lower() == "none":
                    raise IngestError(
                        "No usable YouTube captions and Whisper transcription is disabled."
                    )
                logger.info("No captions for %s; transcribing audio", video_id)
                audio_path = youtube.download_audio(video_id)
                prompt = whisper_prompt(battle.title, settings.whisper_initial_prompt)
                result = transcribe(audio_path, prompt)
                segments, language, source = (
                    result.segments,
                    result.language,
                    result.source,
                )
                segments = _patch_whisper_gaps(
                    segments, info, source, battle.duration
                )
            else:
                segments, language, source = resolved

            if not segments:
                raise IngestError("Transcription produced no segments.")

            _replace_segments(session, video_id, segments, source)
            annotate_battle(session, video_id)

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


def patch_stored_gaps(video_id: str) -> Battle:
    """Re-run caption fill on a battle already stored from Whisper.

    Does not call Groq. Safe to use on the two battles that were transcribed
    before hollow windows were treated as gaps.
    """
    with Session(engine) as session:
        battle = session.get(Battle, video_id)
        if battle is None:
            raise IngestError(f"{video_id} is not ingested")

        stored = get_segments(session, video_id)
        if not stored:
            raise IngestError(f"{video_id} has no segments to patch")

        info = youtube.fetch_info(video_id)
        segments = [
            {
                "start": row.start,
                "end": row.end,
                "text": row.text,
                "source": row.source,
            }
            for row in stored
        ]
        patched = _patch_whisper_gaps(
            segments, info, battle.source or TranscriptSource.WHISPER_GROQ, battle.duration
        )
        _replace_segments(session, video_id, patched, battle.source)
        annotate_battle(session, video_id)
        battle.segment_count = len(patched)
        battle.updated_at = utcnow()
        session.add(battle)
        session.commit()
        session.refresh(battle)
        return battle
