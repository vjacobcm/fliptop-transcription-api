"""yt-dlp wrappers: metadata, caption track discovery, audio download."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

from app.config import settings
from app.models import TranscriptSource

# json3 carries per-event timings directly; the others need text parsing.
FORMAT_PREFERENCE = ("json3", "vtt", "srv3", "srv1")

# Not formally guaranteed by YouTube, but stable for years and what yt-dlp
# itself matches on. Without it, any junk string passes as a video id.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

WATCH_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)
SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})

# /shorts/<id>, /embed/<id>, /live/<id>, /v/<id>
PATH_PREFIXES = ("shorts", "embed", "live", "v")


@dataclass
class CaptionTrack:
    lang: str
    ext: str
    url: str
    source: str  # TranscriptSource.YOUTUBE_MANUAL | YOUTUBE_AUTO


def _validated(candidate: str, original: str) -> str:
    if not VIDEO_ID_RE.match(candidate):
        raise ValueError(f"Could not extract a video id from: {original}")
    return candidate


def extract_video_id(url_or_id: str) -> str:
    """Resolve a YouTube link or bare id to an 11-character video id.

    Raises ValueError for anything that is not recognisably a YouTube video,
    so bad input fails here rather than reaching yt-dlp or the database.
    """
    text = (url_or_id or "").strip()
    if not text:
        raise ValueError("No URL or video id given")

    if "://" not in text and "/" not in text:
        return _validated(text, url_or_id)

    # Tolerate links pasted without a scheme, e.g. "youtu.be/<id>".
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    host_with_www = (parsed.hostname or "").lower()

    if host_with_www in SHORT_HOSTS or host in SHORT_HOSTS:
        return _validated(parsed.path.lstrip("/").split("/")[0], url_or_id)

    if host_with_www not in WATCH_HOSTS and host not in WATCH_HOSTS:
        raise ValueError(f"Not a YouTube URL: {url_or_id}")

    video_ids = parse_qs(parsed.query).get("v")
    if video_ids:
        return _validated(video_ids[0], url_or_id)

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in PATH_PREFIXES:
        return _validated(parts[1], url_or_id)

    raise ValueError(f"Could not extract a video id from: {url_or_id}")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def watch_url_at(video_id: str, seconds: float) -> str:
    """Deep link that starts playback at a given point in the battle."""
    return f"{watch_url(video_id)}&t={max(0, int(seconds))}s"


def fetch_info(video_id: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(watch_url(video_id), download=False)


def list_playlist_videos(url: str, limit: int | None = None) -> list[dict]:
    """Enumerate a playlist or channel cheaply, without per-video metadata calls.

    Channel URLs come back as a playlist of playlists, so entries are
    flattened one level. Duration is present for most YouTube entries but not
    guaranteed, so callers must tolerate None.
    """
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    if limit:
        options["playlistend"] = limit

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

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
            videos.append(
                {
                    "video_id": video_id,
                    "title": entry.get("title") or "",
                    "duration": entry.get("duration"),
                }
            )

    collect(info.get("entries") if info.get("_type") else [info])
    return videos


def _summarise(tracks: dict) -> dict[str, list[str]]:
    return {
        lang: sorted({fmt.get("ext") for fmt in formats if fmt.get("ext")})
        for lang, formats in sorted(tracks.items())
    }


def list_caption_tracks(info: dict) -> dict[str, dict[str, list[str]]]:
    """Report what YouTube offers, for the /captions probe endpoint."""
    return {
        "manual": _summarise(info.get("subtitles") or {}),
        "automatic": _summarise(info.get("automatic_captions") or {}),
    }


def _select_format(formats: list[dict]) -> dict | None:
    for preferred in FORMAT_PREFERENCE:
        for fmt in formats:
            if fmt.get("ext") == preferred and fmt.get("url"):
                return fmt
    return next((fmt for fmt in formats if fmt.get("url")), None)


def pick_caption_track(info: dict, langs: list[str] | None = None) -> CaptionTrack | None:
    """Prefer human-made captions, then auto-generated, in language priority order."""
    langs = langs or settings.caption_lang_list

    candidates = (
        (info.get("subtitles") or {}, TranscriptSource.YOUTUBE_MANUAL),
        (info.get("automatic_captions") or {}, TranscriptSource.YOUTUBE_AUTO),
    )

    for tracks, source in candidates:
        for lang in langs:
            # YouTube uses both bare ("fil") and regional ("en-US") codes.
            matches = [key for key in tracks if key == lang or key.startswith(f"{lang}-")]
            for key in matches:
                chosen = _select_format(tracks[key])
                if chosen:
                    return CaptionTrack(
                        lang=key,
                        ext=chosen["ext"],
                        url=chosen["url"],
                        source=source,
                    )

    return None


def download_caption(track: CaptionTrack) -> str:
    response = httpx.get(track.url, timeout=60.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def download_audio(video_id: str) -> Path:
    """Download bestaudio and normalise to 16 kHz mono mp3 for transcription."""
    target = settings.audio_dir / f"{video_id}.mp3"
    if target.exists():
        return target

    options = {
        "format": "bestaudio/best",
        "outtmpl": str(settings.audio_dir / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }
        ],
        "postprocessor_args": ["-ac", "1", "-ar", "16000"],
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([watch_url(video_id)])

    if not target.exists():
        raise RuntimeError(f"Audio download did not produce {target}")

    return target
