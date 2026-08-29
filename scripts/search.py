#!/usr/bin/env python3
"""Search the transcript corpus without running the server.

    python scripts/search.py "bakit ka nandito"
    python scripts/search.py punchline --limit 5 --source youtube_auto
    python scripts/search.py "flip*" --video Xfsbnz_WTLs
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.search import search  # noqa: E402


def _timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Search stored transcripts")
    parser.add_argument("query", help='Terms, "exact phrase", or prefix*')
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--video", help="Restrict to one YouTube URL or video id")
    parser.add_argument("--source", help="e.g. youtube_auto, whisper_local")
    args = parser.parse_args()

    init_db()

    video_id = youtube.extract_video_id(args.video) if args.video else None

    with Session(engine) as session:
        try:
            hits, total = search(
                session,
                args.query,
                limit=args.limit,
                offset=args.offset,
                video_id=video_id,
                source=args.source,
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2

    if not hits:
        print(f"No matches for {args.query!r}.", file=sys.stderr)
        return 1

    for hit in hits:
        print(f"{hit.title or hit.video_id}  [{_timestamp(hit.start)}]")
        print(f"  {hit.text}")
        print(f"  {youtube.watch_url_at(hit.video_id, hit.start)}")
        print()

    shown = args.offset + len(hits)
    print(f"{shown} of {total} matches for {args.query!r}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
