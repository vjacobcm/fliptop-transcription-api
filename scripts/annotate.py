#!/usr/bin/env python3
"""Mark glossary references in stored transcripts.

Does not call Groq. Safe to re-run: confirmed and rejected mentions are kept,
detected ones are refreshed.

    python scripts/annotate.py YHUaTOiGXBI
    python scripts/annotate.py --all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Battle  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.glossary import annotate_all, annotate_battle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate transcripts with glossary hits")
    parser.add_argument("url", nargs="?", help="YouTube URL or video id")
    parser.add_argument("--all", action="store_true", help="Annotate every stored battle")
    args = parser.parse_args()

    if not args.url and not args.all:
        parser.error("give a YouTube URL or video id, or use --all")

    init_db()

    with Session(engine) as session:
        if args.all:
            counts = annotate_all(session)
        else:
            video_id = youtube.extract_video_id(args.url)
            if session.get(Battle, video_id) is None:
                print(f"{video_id} is not ingested.", file=sys.stderr)
                return 1
            counts = {video_id: annotate_battle(session, video_id)}

        if not counts:
            print("No battles to annotate.")
            return 0

        for video_id, count in counts.items():
            battle = session.get(Battle, video_id)
            title = battle.title if battle else video_id
            print(f"  {video_id}  {count:>4} mentions  {title}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
