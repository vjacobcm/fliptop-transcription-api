#!/usr/bin/env python3
"""Dump every stored transcript into one browsable file.

    python scripts/export_all.py
    python scripts/export_all.py --format timed
    python scripts/export_all.py -o /tmp/battles.txt

Re-run this after a backfill so the combined file keeps up with the database.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Battle, BattleStatus  # noqa: E402
from app.services import youtube  # noqa: E402
from app.services.ingest import get_segments  # noqa: E402

from export import render  # noqa: E402  - sibling script, same directory

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "transcripts" / "all_battles.txt"

RULE = "=" * 72
THIN_RULE = "-" * 72


def _clock(seconds: float | None) -> str:
    minutes, secs = divmod(int(seconds or 0), 60)
    return f"{minutes:02d}:{secs:02d}"


def build(session: Session, fmt: str) -> str:
    battles = list(
        session.exec(
            select(Battle)
            .where(Battle.status == BattleStatus.READY)
            .order_by(Battle.created_at)
        )
    )

    loaded = [(b, get_segments(session, b.video_id)) for b in battles]
    loaded = [(b, segs) for b, segs in loaded if segs]

    pending = session.exec(
        select(Battle).where(Battle.status != BattleStatus.READY)
    ).all()

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "FlipTop battle transcripts",
        f"Exported {stamp}",
        f"{len(loaded)} ready battle(s) with stored segments.",
    ]
    if pending:
        lines.append(
            f"{len(pending)} catalogue battle(s) are not transcribed yet and are omitted here."
        )

    lines += ["", "INDEX", THIN_RULE]
    lines += [f"  {b.video_id}  {b.title}" for b, _ in loaded]

    for index, (battle, segments) in enumerate(loaded, start=1):
        lines += [
            "",
            RULE,
            f"{index}/{len(loaded)}  ID: {battle.video_id}",
            f"Title: {battle.title}",
            f"URL: {youtube.watch_url(battle.video_id)}",
            f"Source: {battle.source}  |  language: {battle.language}"
            f"  |  duration: {_clock(battle.duration)}"
            f"  |  segments: {len(segments)}",
            RULE,
            "",
            render(battle, segments, fmt),
        ]

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export every transcript into one file")
    parser.add_argument(
        "--format",
        default="text",
        choices=("text", "timed"),
        help="Plain text, or text with timestamps and gap markers (default: text)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    init_db()
    with Session(engine) as session:
        rendered = build(session, args.format)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(f"Wrote {args.output} ({len(rendered.splitlines()) + 1} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
