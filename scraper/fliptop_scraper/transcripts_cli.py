"""CLI: snapshot timestamped battle transcripts from Battle BARS PH."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fliptop_scraper.transcripts import (
    BASE_URL,
    DEFAULT_DELAY,
    crawl_battles,
    discover_battle_ids,
    make_fetcher,
)

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "transcripts"
CATALOGUE = Path(__file__).resolve().parents[1] / "battles.json"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def catalogue_ids(path: Path) -> list[str]:
    """Video ids from the YouTube scraper's battles.json."""
    if not path.is_file():
        raise SystemExit(
            f"No catalogue at {path}. Build one with:\n"
            "  cd scraper && python -m fliptop_scraper"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("battles") if isinstance(payload, dict) else payload
    return [row["video_id"] for row in rows or [] if row.get("video_id")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape timestamped battle transcripts into one JSON file per battle"
    )
    parser.add_argument(
        "video_ids",
        nargs="*",
        help="Specific YouTube video ids (default: every battle in the sitemap)",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Directory to write into (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--catalogue",
        action="store_true",
        help="Only scrape battles listed in scraper/battles.json",
    )
    parser.add_argument(
        "--catalogue-path",
        default=str(CATALOGUE),
        help=f"Catalogue file to read (default: {CATALOGUE})",
    )
    parser.add_argument("--limit", type=int, help="Scrape at most this many battles")
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between requests (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-fetch battles already written (default: skip and resume)",
    )
    parser.add_argument(
        "--base",
        default=BASE_URL,
        help=f"Site origin (default: {BASE_URL})",
    )
    parser.add_argument(
        "--full-pages",
        action="store_true",
        help="Fetch rendered HTML instead of the lighter RSC payload",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel requests, to ride out the site's slow responses (default: 4)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be scraped and exit",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    fetch, close = make_fetcher(delay=args.delay, rsc=not args.full_pages)

    try:
        if args.video_ids:
            video_ids = args.video_ids
        elif args.catalogue:
            video_ids = catalogue_ids(Path(args.catalogue_path))
            print(f"{len(video_ids)} battle(s) in the catalogue", file=sys.stderr)
        else:
            print(f"Listing battles on {args.base}", file=sys.stderr)
            video_ids = discover_battle_ids(fetch, base=args.base)
            print(f"{len(video_ids)} battle(s) in the sitemap", file=sys.stderr)

        if not args.overwrite:
            done = {path.stem for path in out_dir.glob("*.json")}
            pending = [vid for vid in video_ids if vid not in done]
            if done:
                print(
                    f"{len(video_ids) - len(pending)} already written, "
                    f"{len(pending)} to go",
                    file=sys.stderr,
                )
            video_ids = pending

        if args.limit:
            video_ids = video_ids[: args.limit]

        if not video_ids:
            print("Nothing to scrape.", file=sys.stderr)
            return 0

        if args.dry_run:
            for video_id in video_ids:
                print(video_id)
            print(f"\nDry run; {len(video_ids)} battle(s) not fetched.", file=sys.stderr)
            return 0

        out_dir.mkdir(parents=True, exist_ok=True)

        def progress(message: str) -> None:
            print(f"  {message}", file=sys.stderr, flush=True)

        written = 0
        lines = 0
        with_speakers = 0
        failures: list[tuple[str, str]] = []

        for video_id, battle, error in crawl_battles(
            fetch,
            video_ids,
            base=args.base,
            rsc=not args.full_pages,
            workers=max(1, args.workers),
            progress=progress,
        ):
            if battle is None:
                failures.append((video_id, error or "unknown error"))
                continue
            battle["scraped_at"] = _stamp()
            # Written one battle at a time so an interrupted run resumes.
            (out_dir / f"{video_id}.json").write_text(
                json.dumps(battle, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            written += 1
            lines += battle["line_count"]
            with_speakers += 1 if battle["has_speakers"] else 0
    except KeyboardInterrupt:
        print("\nStopped; re-run to resume.", file=sys.stderr)
        return 130
    finally:
        close()

    print(
        f"\n{written} battle(s), {lines:,} lines, "
        f"{with_speakers} with speaker labels  ->  {out_dir.resolve()}",
        file=sys.stderr,
    )
    if failures:
        print(f"\n{len(failures)} failed:", file=sys.stderr)
        for video_id, error in failures[:20]:
            print(f"  {video_id}: {error}", file=sys.stderr)
        print("Re-run to retry just these.", file=sys.stderr)
    return 1 if failures and not written else 0


if __name__ == "__main__":
    raise SystemExit(main())
