#!/usr/bin/env python3
"""LLM NER on stored transcripts via Groq's free chat tier.

Safe to re-run: confirmed and rejected mentions stay, model-detected ones
refresh. Glossary hits are left alone. --purge drops the old spaCy layer
and the junk glossary rows it created.

    python scripts/ner.py --purge
    python scripts/ner.py YHUaTOiGXBI
    python scripts/ner.py --all
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.models import Battle  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ner import annotate_all, annotate_battle, purge_model_layer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark LLM-detected names in stored transcripts")
    parser.add_argument("url", nargs="?", help="YouTube URL or video id")
    parser.add_argument("--all", action="store_true", help="Annotate every ready battle")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete model-detected mentions and the glossary rows they created",
    )
    args = parser.parse_args()

    if not args.purge and not args.url and not args.all:
        parser.error("give a YouTube URL or video id, or use --all / --purge")

    init_db()

    with Session(engine) as session:
        if args.purge:
            stats = purge_model_layer(session)
            print(
                f"Purged {stats['mentions']} model mentions and "
                f"{stats['entries']} junk glossary entries."
            )

        if not args.url and not args.all:
            return 0

        if not settings.groq_api_key:
            print(
                "GROQ_API_KEY is empty.\n"
                "Create a free key at https://console.groq.com/keys and add it to .env",
                file=sys.stderr,
            )
            return 2

        print(f"NER model: {settings.groq_ner_model}", file=sys.stderr)

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
            print(f"  {video_id}  {count:>4} model mentions  {title}")

        print(f"\n{sum(counts.values())} model mention(s) across {len(counts)} battle(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
