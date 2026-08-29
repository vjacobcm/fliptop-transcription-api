#!/usr/bin/env python3
"""Dump a stored transcript without running the server.

    python scripts/export.py Xfsbnz_WTLs
    python scripts/export.py Xfsbnz_WTLs --format srt -o battle.srt
    python scripts/export.py --list
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Battle  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ingest import get_segments  # noqa: E402
from app.subtitles import to_srt, to_vtt  # noqa: E402


def _timestamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def render(battle: Battle, segments: list, fmt: str) -> str:
    if fmt == "srt":
        return to_srt(segments)
    if fmt == "vtt":
        return to_vtt(segments)
    if fmt == "timed":
        return "\n".join(f"[{_timestamp(s.start)}] {s.text}" for s in segments)
    if fmt == "json":
        payload = battle.model_dump(mode="json")
        payload["segments"] = [
            {"idx": s.idx, "start": s.start, "end": s.end, "text": s.text}
            for s in segments
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return "\n".join(s.text for s in segments)


def list_battles(session: Session) -> int:
    battles = list(session.exec(select(Battle).order_by(Battle.created_at.desc())))
    if not battles:
        print("No battles ingested yet. Run scripts/ingest.py first.", file=sys.stderr)
        return 1

    for battle in battles:
        print(
            f"  {battle.video_id}  {battle.status:<10} "
            f"{battle.segment_count:>5} segments  {battle.title}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a stored transcript")
    parser.add_argument("url", nargs="?", help="YouTube URL or video id")
    parser.add_argument(
        "--format",
        default="text",
        choices=("text", "timed", "json", "srt", "vtt"),
        help="Output format (default: text)",
    )
    parser.add_argument("-o", "--output", type=Path, help="Write to a file instead of stdout")
    parser.add_argument(
        "--list", action="store_true", help="List ingested battles and exit"
    )
    args = parser.parse_args()

    if not args.list and not args.url:
        parser.error("give a YouTube URL or video id, or use --list")

    init_db()

    with Session(engine) as session:
        if args.list:
            return list_battles(session)

        video_id = youtube.extract_video_id(args.url)
        battle = session.get(Battle, video_id)
        if battle is None:
            print(
                f"{video_id} is not ingested. Run --list to see what is stored.",
                file=sys.stderr,
            )
            return 1

        segments = get_segments(session, video_id)
        if not segments:
            print(
                f"{video_id} has no segments (status={battle.status}, "
                f"error={battle.error}).",
                file=sys.stderr,
            )
            return 1

        rendered = render(battle, segments, args.format)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(
            f"Wrote {len(segments)} segments as {args.format} to {args.output}",
            file=sys.stderr,
        )
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
