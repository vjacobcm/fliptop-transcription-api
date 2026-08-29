"""List videos on a YouTube channel without downloading anything."""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

DEFAULT_CHANNEL = "https://www.youtube.com/@FlipTopBattles/videos"

# YouTube Shorts currently cap at 3 minutes. FlipTop full battles run much longer.
SHORTS_MAX_SECONDS = 180


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_video_id(url_or_id: str) -> str:
    text = (url_or_id or "").strip()
    if not text:
        raise ValueError("No URL or video id given")
    if "://" not in text and "/" not in text:
        if not VIDEO_ID_RE.match(text):
            raise ValueError(f"Could not extract a video id from: {url_or_id}")
        return text

    parsed = urlparse(text if "://" in text else f"https://{text}")
    video_ids = parse_qs(parsed.query).get("v")
    if video_ids and VIDEO_ID_RE.match(video_ids[0]):
        return video_ids[0]
    raise ValueError(f"Could not extract a video id from: {url_or_id}")


def videos_tab_url(url: str) -> str:
    """Point at the Videos tab so the Shorts shelf is not crawled."""
    text = (url or "").strip().rstrip("/")
    parsed = urlparse(text)
    path = (parsed.path or "").rstrip("/")
    parts = [p for p in path.split("/") if p]
    if parts and parts[-1].lower() == "shorts":
        raise ValueError(f"Refusing to scrape the Shorts tab: {url}")
    if parts and parts[-1].lower() in {"videos", "streams", "playlists", "search"}:
        return text
    return f"{text}/videos"


def is_short(entry: dict, min_duration: int = SHORTS_MAX_SECONDS) -> bool:
    """True for YouTube Shorts URLs or anything at/under the Shorts duration cap."""
    for key in ("url", "source_url", "original_url", "webpage_url"):
        value = entry.get(key) or ""
        if "/shorts/" in value:
            return True
    duration = entry.get("duration")
    if duration is not None and duration <= min_duration:
        return True
    return False


def list_channel_videos(
    url: str = DEFAULT_CHANNEL,
    limit: int | None = None,
    progress: Callable[[int], None] | None = None,
) -> list[dict]:
    """Enumerate a channel Videos tab cheaply (titles + ids + duration)."""
    url = videos_tab_url(url)
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
    }
    if limit:
        options["playlistend"] = limit

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise RuntimeError(f"yt-dlp returned nothing for {url}")

    videos: list[dict] = []
    seen: set[str] = set()

    def collect(entries) -> None:
        for entry in entries or []:
            if limit and len(videos) >= limit:
                return
            if not entry:
                continue
            if entry.get("_type") == "playlist":
                collect(entry.get("entries"))
                continue

            video_id = entry.get("id") or ""
            if not VIDEO_ID_RE.match(video_id) or video_id in seen:
                continue

            seen.add(video_id)
            entry_url = entry.get("url") or ""
            videos.append(
                {
                    "video_id": video_id,
                    "title": entry.get("title") or "",
                    "url": watch_url(video_id),
                    "duration": entry.get("duration"),
                    "source_url": entry_url,
                }
            )
            if progress and len(videos) % 100 == 0:
                progress(len(videos))

    collect(info.get("entries") if info.get("_type") else [info])
    return videos
