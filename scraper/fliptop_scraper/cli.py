"""CLI: dump FlipTop battle URLs for GL, BLKD, Loonie, and Tipsy D."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from fliptop_scraper.emcees import EMCEES
from fliptop_scraper.match import matched_emcees
from fliptop_scraper.titles import parse_matchup
from fliptop_scraper.youtube import (
    DEFAULT_CHANNEL,
    SHORTS_MAX_SECONDS,
    is_short,
    list_channel_videos,
)


def build_payload(channel: str, videos: list[dict], skipped_shorts: int) -> dict:
    battles: list[dict] = []
    by_emcee: dict[str, list[str]] = {name: [] for name in EMCEES}

    for video in videos:
        names = matched_emcees(video["title"])
        if not names:
            continue
        matchup = parse_matchup(video["title"])
        battles.append(
            {
                "url": video["url"],
                "video_id": video["video_id"],
                "title": video["title"],
                "duration": video.get("duration"),
                "emcees": names,
                "matchup": matchup.describe() if matchup.is_complete else None,
                "event": matchup.event,
            }
        )
        for name in names:
            by_emcee[name].append(video["url"])

    return {
        "channel": channel,
        "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videos_scanned": len(videos) + skipped_shorts,
        "shorts_skipped": skipped_shorts,
        "battle_count": len(battles),
        "counts": {name: len(urls) for name, urls in by_emcee.items()},
        "battles": battles,
        "by_emcee": by_emcee,
    }


def format_text(payload: dict) -> str:
    """Plain-text list of battles grouped by emcee, title then URL."""
    lines: list[str] = [
        "FlipTop battles",
        f"{payload['battle_count']} unique videos",
        "",
    ]
    counts = payload.get("counts") or {}
    lines.append("  ".join(f"{name} {counts.get(name, 0)}" for name in EMCEES))
    lines.append("")

    by_emcee: dict[str, list[dict]] = {name: [] for name in EMCEES}
    for battle in payload.get("battles") or []:
        for name in battle.get("emcees") or []:
            if name in by_emcee:
                by_emcee[name].append(battle)

    for name in EMCEES:
        battles = by_emcee[name]
        lines.append(f"{name} ({len(battles)})")
        if not battles:
            lines.append("  (none)")
            lines.append("")
            continue
        for battle in battles:
            lines.append(f"  {battle.get('title') or battle['url']}")
            lines.append(f"  {battle['url']}")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape FlipTop YouTube battle URLs for GL, BLKD, Loonie, and Tipsy D"
    )
    parser.add_argument(
        "--channel",
        default=DEFAULT_CHANNEL,
        help="Channel URL (Videos tab is used; the Shorts tab is refused)",
    )
    parser.add_argument(
        "--out",
        default="battles.json",
        help="JSON file to write (default: battles.json). A .txt sibling is written too.",
    )
    parser.add_argument(
        "--txt",
        default=None,
        help="Plain-text file to write (default: same name as --out with a .txt suffix)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of writing files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only scan the first N videos on the channel (newest first)",
    )
    parser.add_argument(
        "--min-duration",
        type=int,
        default=SHORTS_MAX_SECONDS,
        help=(
            "Drop videos this many seconds or shorter "
            f"(default: {SHORTS_MAX_SECONDS}, the YouTube Shorts cap)"
        ),
    )
    args = parser.parse_args(argv)

    def progress(count: int) -> None:
        print(f"  scanned {count} videos...", file=sys.stderr)

    print(f"Listing {args.channel}", file=sys.stderr)
    try:
        listed = list_channel_videos(args.channel, limit=args.limit, progress=progress)
    except Exception as exc:
        print(f"Failed to list channel: {exc}", file=sys.stderr)
        return 1
    if not listed:
        print("No videos returned. Check the channel URL or SSL/certs.", file=sys.stderr)
        return 1

    videos = [v for v in listed if not is_short(v, min_duration=args.min_duration)]
    skipped = len(listed) - len(videos)
    print(
        f"Scanned {len(listed)} uploads; dropped {skipped} Shorts/"
        f"sub-{args.min_duration}s clips; matching emcees...",
        file=sys.stderr,
    )

    payload = build_payload(args.channel, videos, skipped)
    payload["videos_scanned"] = len(listed)
    json_text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    txt_text = format_text(payload)

    if args.stdout:
        sys.stdout.write(json_text)
    else:
        json_path = Path(args.out)
        json_path.write_text(json_text, encoding="utf-8")
        print(f"Wrote {json_path.resolve()}", file=sys.stderr)

        txt_path = Path(args.txt) if args.txt else json_path.with_suffix(".txt")
        txt_path.write_text(txt_text, encoding="utf-8")
        print(f"Wrote {txt_path.resolve()}", file=sys.stderr)

    print(
        f"{payload['battle_count']} battles  |  "
        + ", ".join(f"{name} {n}" for name, n in payload["counts"].items()),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
