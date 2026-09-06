#!/usr/bin/env python3
"""Transcribe battles in batches, staying inside a Groq audio-seconds budget.

The free tier allows 8 hours of audio per day, so every run adds up the
durations first and stops before it would go over.

    python scripts/backfill.py --upgrade --limit 2 --dry-run
    python scripts/backfill.py --upgrade --limit 2
    python scripts/backfill.py --playlist "https://www.youtube.com/@FlipTopBattles/videos" --limit 5
    python scripts/backfill.py https://youtu.be/Xfsbnz_WTLs

`--captions-only` builds the catalogue from YouTube's own caption tracks
instead. It never downloads audio, so it costs no Groq quota and ignores the
budget; battles that have no caption track are reported and left for a later
Whisper pass.

    python scripts/backfill.py --catalogue --captions-only --dry-run
    python scripts/backfill.py --catalogue --captions-only
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Battle, BattleStatus, TranscriptSource  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ingest import ingest_battle  # noqa: E402

CATALOGUE_PATH = Path(__file__).resolve().parent.parent / "scraper" / "battles.json"


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


def _catalogue_candidates(path: Path) -> list[dict]:
    """Battles listed by the YouTube scraper (`scraper/battles.json`)."""
    if not path.is_file():
        raise SystemExit(
            f"No catalogue at {path}. Build one with:\n"
            "  cd scraper && python -m fliptop_scraper"
        )

    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("battles") if isinstance(payload, dict) else payload
    if not rows:
        raise SystemExit(f"{path} lists no battles.")

    candidates: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        video_id = row.get("video_id") or ""
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        candidates.append(
            {
                "video_id": video_id,
                "title": row.get("title") or "",
                "duration": row.get("duration"),
            }
        )
    return candidates


def _lacks_captions(battle: Battle | None) -> bool:
    """True when a stored failure was a genuine caption miss, not a throttle."""
    if battle is None:
        return False
    return "No usable YouTube captions" in (battle.error or "")


def build_candidates(args, session: Session) -> list[dict]:
    if args.upgrade:
        source = None if args.any_source else TranscriptSource.YOUTUBE_AUTO
        return _stored_candidates(session, source)

    if args.catalogue:
        candidates = _catalogue_candidates(Path(args.catalogue_path))
        if args.only_no_captions:
            stored = {b.video_id: b for b in session.exec(select(Battle))}
            candidates = [
                c for c in candidates if _lacks_captions(stored.get(c["video_id"]))
            ]
        return candidates

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
    parser.add_argument(
        "--catalogue",
        action="store_true",
        help="Take candidates from the scraper's battles.json",
    )
    parser.add_argument(
        "--catalogue-path",
        default=str(CATALOGUE_PATH),
        help=f"Catalogue file to read (default: {CATALOGUE_PATH})",
    )
    parser.add_argument(
        "--captions-only",
        action="store_true",
        help="Use YouTube caption tracks only; never download audio or spend Groq quota",
    )
    parser.add_argument(
        "--only-no-captions",
        action="store_true",
        help=(
            "With --catalogue, keep only battles a previous run found to have no "
            "caption track, so rate-limited ones are left for a later captions pass"
        ),
    )
    parser.add_argument("--limit", type=int, help="Process at most this many battles")
    parser.add_argument(
        "--budget-seconds",
        type=float,
        default=float(settings.groq_daily_audio_seconds),
        help="Audio-seconds ceiling for this run (default: the Groq free-tier day)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Seconds to wait between battles, to go easy on YouTube",
    )
    parser.add_argument(
        "--max-throttles",
        type=int,
        default=3,
        help="Give up after this many rate-limited battles in a row (default: 3)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show the plan and exit")
    args = parser.parse_args()

    if not (args.urls or args.playlist or args.upgrade or args.catalogue):
        parser.error("give URLs, or use --playlist, --catalogue, or --upgrade")

    if args.upgrade and args.captions_only:
        parser.error("--upgrade re-runs Whisper, so it cannot be combined with --captions-only")

    backend = settings.transcription_backend.lower()
    # Captions-only never touches the transcription backend, so an unset or
    # disabled one is fine.
    if not args.captions_only:
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

    if args.captions_only:
        # No audio is processed, so the Groq budget does not apply and there is
        # no reason to spend a yt-dlp round trip resolving durations up front.
        selected = candidates
        total = sum(c.get("duration") or 0 for c in candidates)
        skipped = 0
        print(
            f"\nyoutube captions  |  {len(selected)} battle(s), {_hms(total)} of audio"
            f"  |  no Groq quota used"
        )
    else:
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
    throttled: list[str] = []
    no_captions: list[str] = []
    streak = 0
    attempted = 0
    for index, candidate in enumerate(selected, start=1):
        video_id = candidate["video_id"]
        label = candidate["title"] or video_id
        print(f"[{index}/{len(selected)}] {label}", flush=True)
        attempted += 1

        battle = ingest_battle(
            video_id,
            force=True,
            allow_whisper=not args.captions_only,
            prefer_whisper=not args.captions_only,
        )

        if battle.status == BattleStatus.FAILED:
            failures += 1
            error = battle.error or ""
            # A throttle means the track exists but went unread; only a real
            # miss should be handed on to Whisper.
            if "429" in error or "kept returning" in error:
                throttled.append(label)
                streak += 1
            else:
                streak = 0
                if args.captions_only:
                    no_captions.append(label)
            print(f"      FAILED: {error}", file=sys.stderr, flush=True)
        else:
            streak = 0
            print(
                f"      {battle.segment_count} segments via {battle.source}", flush=True
            )

        # A sustained block is an IP-level ban that no amount of waiting inside
        # one run clears, so stop instead of grinding through the rest.
        if streak >= args.max_throttles:
            print(
                f"\nStopping: {streak} battles in a row were rate-limited. "
                "YouTube has blocked this IP for caption downloads; "
                "try again in a few hours.",
                file=sys.stderr,
            )
            break

        if args.delay and index < len(selected):
            time.sleep(args.delay)

    print(f"\nDone. {attempted - failures} succeeded, {failures} failed.")
    if throttled:
        print(
            f"\n{len(throttled)} battle(s) hit YouTube's caption rate limit. "
            "Their captions are fine; just re-run with a longer --delay:\n"
            "  python scripts/backfill.py --catalogue --captions-only --delay 20"
        )
    if no_captions:
        # --upgrade only reconsiders battles already stored as READY, so these
        # have to come back through the catalogue.
        print(
            f"\n{len(no_captions)} battle(s) have no caption track at all. "
            "Transcribe those with Whisper:\n"
            "  python scripts/backfill.py --catalogue --limit 5"
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
