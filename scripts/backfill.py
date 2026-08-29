#!/usr/bin/env python3
"""Transcribe battles in batches, staying inside a Groq audio-seconds budget.

The free tier allows 8 hours of audio per day, so every run adds up the
durations first and stops before it would go over.

    python scripts/backfill.py --upgrade --limit 2 --dry-run
    python scripts/backfill.py --upgrade --limit 2
    python scripts/backfill.py --playlist "https://www.youtube.com/@FlipTopBattles/videos" --limit 5
    python scripts/backfill.py https://youtu.be/Xfsbnz_WTLs
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Battle, BattleStatus, TranscriptSource  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ingest import ingest_battle  # noqa: E402


def _hms(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"


def _stored_candidates(session: Session, source: str | None) -> list[dict]:
    """Battles already in the database, for re-running through Whisper."""
    statement = select(Battle).where(Battle.status == BattleStatus.READY)
    if source:
        statement = statement.where(Battle.source == source)

    return [
        {"video_id": b.video_id, "title": b.title, "duration": b.duration}
        for b in session.exec(statement.order_by(Battle.created_at))
    ]


def _resolve_durations(candidates: list[dict]) -> None:
    """Fill in durations the flat playlist listing did not provide."""
    for candidate in candidates:
        if candidate.get("duration"):
            continue
        try:
            info = youtube.fetch_info(candidate["video_id"])
        except Exception as exc:  # noqa: BLE001 - a bad id should not stop the run
            print(f"  ! {candidate['video_id']}: {exc}", file=sys.stderr)
            continue
        candidate["duration"] = info.get("duration")
        candidate["title"] = candidate["title"] or (info.get("title") or "")


def _within_budget(candidates: list[dict], budget: float) -> tuple[list[dict], float]:
    selected: list[dict] = []
    used = 0.0

    for candidate in candidates:
        duration = candidate.get("duration") or 0
        if used + duration > budget:
            break
        selected.append(candidate)
        used += duration

    return selected, used


def build_candidates(args, session: Session) -> list[dict]:
    if args.upgrade:
        source = None if args.any_source else TranscriptSource.YOUTUBE_AUTO
        return _stored_candidates(session, source)

    if args.playlist:
        return youtube.list_playlist_videos(args.playlist, limit=args.limit)

    return [
        {"video_id": youtube.extract_video_id(url), "title": "", "duration": None}
        for url in args.urls
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-transcribe battles with Whisper")
    parser.add_argument("urls", nargs="*", help="YouTube URLs or video ids")
    parser.add_argument("--playlist", help="Playlist or channel URL to enumerate")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Re-run battles already stored from YouTube auto-captions",
    )
    parser.add_argument(
        "--any-source",
        action="store_true",
        help="With --upgrade, include battles from any source, not just auto-captions",
    )
    parser.add_argument("--limit", type=int, help="Process at most this many battles")
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=float(settings.groq_daily_audio_seconds),
        help="Audio-seconds ceiling for this run (default: the Groq free-tier day)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the plan and exit")
    args = parser.parse_args()

    if not (args.urls or args.playlist or args.upgrade):
        parser.error("give URLs, or use --playlist or --upgrade")

    backend = settings.transcription_backend.lower()
    if backend == "none":
        print("TRANSCRIPTION_BACKEND=none; nothing to do.", file=sys.stderr)
        return 2
    if backend == "groq" and not settings.groq_api_key:
        print(
            "TRANSCRIPTION_BACKEND=groq but GROQ_API_KEY is empty.\n"
            "Create a free key at https://console.groq.com/keys and add it to .env",
            file=sys.stderr,
        )
        return 2

    init_db()

    with Session(engine) as session:
        candidates = build_candidates(args, session)
        stored = {b.video_id: b for b in session.exec(select(Battle))}

    if not args.upgrade:
        candidates = [
            c
            for c in candidates
            if not (
                c["video_id"] in stored
                and stored[c["video_id"]].status == BattleStatus.READY
            )
        ]

    if args.limit:
        candidates = candidates[: args.limit]

    if not candidates:
        print("Nothing to do; everything selected is already transcribed.")
        return 0

    _resolve_durations(candidates)
    selected, total = _within_budget(candidates, args.budget_seconds)

    skipped = len(candidates) - len(selected)
    print(
        f"\n{backend} / {settings.groq_model if backend == 'groq' else settings.whisper_model_size}"
        f"  |  {len(selected)} battle(s), {_hms(total)} of audio"
        f"  |  budget {_hms(args.budget_seconds)}"
    )
    if skipped:
        print(f"{skipped} battle(s) held back to stay inside the budget.")
    print()

    for index, candidate in enumerate(selected, start=1):
        label = candidate["title"] or candidate["video_id"]
        print(f"{index:>3}. {label}  ({_hms(candidate.get('duration') or 0)})")

    if args.dry_run:
        print("\nDry run; nothing transcribed.")
        return 0

    print()
    failures = 0
    for index, candidate in enumerate(selected, start=1):
        video_id = candidate["video_id"]
        label = candidate["title"] or video_id
        print(f"[{index}/{len(selected)}] {label}")

        battle = ingest_battle(
            video_id,
            force=True,
            allow_whisper=True,
            prefer_whisper=True,
        )

        if battle.status == BattleStatus.FAILED:
            failures += 1
            print(f"      FAILED: {battle.error}", file=sys.stderr)
        else:
            print(f"      {battle.segment_count} segments via {battle.source}")

    print(f"\nDone. {len(selected) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
