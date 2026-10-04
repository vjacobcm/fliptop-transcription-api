#!/usr/bin/env python3
"""Load scraped Battle BARS PH transcripts into the catalogue.

The Chrome companion only sees rows in fliptop.db. This walks
scraper/transcripts/*.json, writes Battle + Segment rows, then runs the
glossary matcher. LLM NER is a separate pass (`scripts/ner.py`).

    python scripts/ingest_scraped.py --dry-run
    python scripts/ingest_scraped.py --overwrite
    python scripts/ingest_scraped.py --overwrite YHUaTOiGXBI
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import Battle, BattleStatus, TranscriptSource  # noqa: E402
from app.services.glossary import annotate_battle  # noqa: E402
from app.services.ingest import IngestError, ingest_snapshot  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "scraper" / "transcripts"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest scraped Battle BARS PH transcripts into the database"
    )
    parser.add_argument(
        "video_ids",
        nargs="*",
        help="Specific video ids (default: every JSON file in --dir)",
    )
    parser.add_argument(
        "--dir",
        default=str(DEFAULT_DIR),
        help=f"Snapshot directory (default: {DEFAULT_DIR})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace battles already stored from captions or Whisper",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Ingest at most this many files",
    )
    parser.add_argument(
        "--skip-annotate",
        action="store_true",
        help="Write segments only; skip the glossary pass",
    )
    parser.add_argument("--dry-run", action="store_true", help="List files and exit")
    args = parser.parse_args()

    source = Path(args.dir)
    if not source.is_dir():
        print(f"No snapshot directory at {source}", file=sys.stderr)
        return 2

    paths = sorted(source.glob("*.json"))
    if args.video_ids:
        wanted = set(args.video_ids)
        paths = [path for path in paths if path.stem in wanted]
        missing = wanted - {path.stem for path in paths}
        for video_id in sorted(missing):
            print(f"  missing snapshot: {video_id}", file=sys.stderr)

    if args.limit:
        paths = paths[: args.limit]

    if not paths:
        print("Nothing to ingest.")
        return 0

    init_db()

    with Session(engine) as session:
        stored = {b.video_id: b for b in session.exec(select(Battle))}

    pending = []
    skipped = 0
    for path in paths:
        battle = stored.get(path.stem)
        if battle and battle.status == BattleStatus.READY and not args.overwrite:
            skipped += 1
            continue
        pending.append(path)

    print(
        f"{len(paths)} snapshot(s), {skipped} already ready, {len(pending)} to write"
        f"{' (overwrite)' if args.overwrite else ''}"
    )
    if args.dry_run:
        for path in pending[:20]:
            print(f"  {path.stem}")
        if len(pending) > 20:
            print(f"  ... {len(pending) - 20} more")
        print("Dry run; nothing ingested.")
        return 0

    written = 0
    lines = 0
    ingested: list[str] = []
    failures: list[tuple[str, str]] = []
    for index, path in enumerate(pending, start=1):
        try:
            battle = ingest_snapshot(path, force=True, annotate=False)
        except IngestError as exc:
            failures.append((path.stem, str(exc)))
            print(f"  {index}/{len(pending)} {path.stem} failed: {exc}", file=sys.stderr)
            continue
        written += 1
        lines += battle.segment_count
        ingested.append(battle.video_id)
        if index == 1 or index % 50 == 0 or index == len(pending):
            print(
                f"  {index}/{len(pending)} {battle.video_id}  "
                f"{battle.segment_count} lines  {battle.title}"
            )

    if ingested and not args.skip_annotate:
        print(f"Annotating glossary mentions on {len(ingested)} battle(s)...")
        with Session(engine) as session:
            for video_id in ingested:
                annotate_battle(session, video_id)

    print(f"\n{written} battle(s), {lines:,} lines via {TranscriptSource.BATTLEBARS}")
    if failures:
        print(f"{len(failures)} failed:", file=sys.stderr)
        for video_id, error in failures[:15]:
            print(f"  {video_id}: {error}", file=sys.stderr)
    return 1 if failures and not written else 0


if __name__ == "__main__":
    raise SystemExit(main())
