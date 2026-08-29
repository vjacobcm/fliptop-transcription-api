#!/usr/bin/env python3
"""Ingest a battle from the command line.

    python scripts/ingest.py "https://www.youtube.com/watch?v=Xfsbnz_WTLs"
    python scripts/ingest.py "https://youtu.be/Xfsbnz_WTLs" --whisper

Most FlipTop uploads carry Filipino auto-captions, so the default tiered path
never reaches Whisper. Pass --whisper to skip captions and use the configured
Whisper backend instead.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402
from app.models import BattleStatus  # noqa: E402
from app.services.ingest import ingest_battle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a FlipTop battle")
    parser.add_argument("url", help="YouTube URL or video id")
    parser.add_argument(
        "--force", action="store_true", help="Re-ingest even if already stored"
    )
    parser.add_argument(
        "--no-whisper",
        action="store_true",
        help="Fail instead of transcribing audio when captions are missing",
    )
    parser.add_argument(
        "--whisper",
        action="store_true",
        help="Skip YouTube captions and transcribe the audio even if captions exist",
    )
    args = parser.parse_args()

    if args.whisper and args.no_whisper:
        parser.error("--whisper and --no-whisper contradict each other")

    init_db()
    battle = ingest_battle(
        args.url,
        force=args.force or args.whisper,
        allow_whisper=not args.no_whisper,
        prefer_whisper=args.whisper,
    )

    print(f"\n  video_id : {battle.video_id}")
    print(f"  title    : {battle.title}")
    print(f"  status   : {battle.status}")
    print(f"  source   : {battle.source}")
    print(f"  language : {battle.language}")
    print(f"  segments : {battle.segment_count}")
    if battle.error:
        print(f"  error    : {battle.error}")

    return 0 if battle.status == BattleStatus.READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
