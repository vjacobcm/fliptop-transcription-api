"""CLI: snapshot FlipTop official-site emcee profiles for the glossary."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fliptop_scraper.normalize import compile_entries
from fliptop_scraper.site import (
    BASE_URL,
    DEFAULT_DELAY,
    crawl_emcees,
    make_fetcher,
)


def build_payload(emcees: list[dict], *, source: str = BASE_URL) -> dict:
    prepared, entries = compile_entries(emcees)
    people = sum(1 for row in entries if row["kind"] == "person")
    groups = sum(1 for row in entries if row["kind"] == "group")
    places = sum(1 for row in entries if row["kind"] == "place")
    return {
        "source": source,
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emcee_count": len(prepared),
        "entry_count": len(entries),
        "counts": {"person": people, "group": groups, "place": places},
        "emcees": prepared,
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape FlipTop official-site emcee profiles into a glossary snapshot"
    )
    parser.add_argument(
        "--out",
        default="site.json",
        help="JSON file to write (default: site.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only fetch the first N emcee profiles (after listing)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Seconds between HTTP requests (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--base",
        default=BASE_URL,
        help=f"Site origin (default: {BASE_URL})",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of writing a file",
    )
    args = parser.parse_args(argv)

    def progress(message: str) -> None:
        print(f"  {message}", file=sys.stderr)

    fetch, close = make_fetcher(delay=args.delay)
    try:
        print(f"Listing emcees on {args.base}", file=sys.stderr)
        emcees = crawl_emcees(
            fetch, base=args.base, limit=args.limit, progress=progress
        )
    except Exception as exc:
        print(f"Failed to scrape site: {exc}", file=sys.stderr)
        return 1
    finally:
        close()

    if not emcees:
        print("No emcee profiles returned.", file=sys.stderr)
        return 1

    payload = build_payload(emcees, source=args.base)
    json_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if args.stdout:
        sys.stdout.write(json_text)
    else:
        path = Path(args.out)
        path.write_text(json_text, encoding="utf-8")
        print(f"Wrote {path.resolve()}", file=sys.stderr)

    counts = payload["counts"]
    print(
        f"{payload['emcee_count']} emcees  |  "
        f"{counts['person']} people, {counts['group']} groups, {counts['place']} places",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
