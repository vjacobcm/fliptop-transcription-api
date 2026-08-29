#!/usr/bin/env python3
"""Fill holes in an already-stored Whisper transcript using YouTube captions.

Does not call Groq. Use this after a Whisper ingest that dropped speech under
music, or that covered 30s windows with a two-word hallucination.

    python scripts/patch_gaps.py QfNRBbL65Uk
    python scripts/patch_gaps.py --all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Battle, TranscriptSource  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ingest import patch_stored_gaps  # noqa: E402


WHISPER_SOURCES = {TranscriptSource.WHISPER_GROQ, TranscriptSource.WHISPER_LOCAL}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Patch Whisper dropouts with YouTube captions"
    )
    parser.add_argument("url", nargs="?", help="YouTube URL or video id")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Patch every battle stored from Whisper",
    )
    args = parser.parse_args()

    if not args.url and not args.all:
        parser.error("give a YouTube URL or video id, or use --all")

    init_db()

    if args.all:
        with Session(engine) as session:
            ids = [
                battle.video_id
                for battle in session.exec(select(Battle))
                if battle.source in WHISPER_SOURCES
            ]
    else:
        ids = [youtube.extract_video_id(args.url)]

    if not ids:
        print("No Whisper transcripts to patch.")
        return 0

    failures = 0
    for video_id in ids:
        try:
            battle = patch_stored_gaps(video_id)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  {video_id}  FAILED: {exc}", file=sys.stderr)
            continue
        print(
            f"  {battle.video_id}  {battle.segment_count} segments  {battle.title}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
