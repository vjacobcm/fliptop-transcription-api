#!/usr/bin/env python3
"""Check which caption tracks YouTube has for a video, without ingesting.

    python scripts/probe_captions.py "https://www.youtube.com/watch?v=Xfsbnz_WTLs"
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import youtube  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe YouTube caption tracks")
    parser.add_argument("url", help="YouTube URL or video id")
    args = parser.parse_args()

    video_id = youtube.extract_video_id(args.url)
    info = youtube.fetch_info(video_id)
    tracks = youtube.list_caption_tracks(info)

    print(f"\n  {info.get('title')}")
    print(f"  duration: {info.get('duration')}s\n")

    for kind in ("manual", "automatic"):
        available = tracks[kind]
        print(f"  {kind} captions: {len(available)} language(s)")
        for lang, formats in available.items():
            print(f"    - {lang}: {', '.join(formats)}")
        print()

    chosen = youtube.pick_caption_track(info)
    if chosen:
        print(f"  would use -> {chosen.lang} ({chosen.ext}) via {chosen.source}\n")
    else:
        print("  would use -> none; ingest would fall back to Whisper\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
