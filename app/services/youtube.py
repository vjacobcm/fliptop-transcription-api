"""yt-dlp wrappers: metadata, caption track discovery, audio download."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import yt_dlp

from app.config import settings
from app.models import TranscriptSource

# json3 carries per-event timings directly; the others need text parsing.
FORMAT_PREFERENCE = ("json3", "vtt", "srv3", "srv1")


@dataclass
class CaptionTrack:
    lang: str
    ext: str
    url: str
    source: str  # TranscriptSource.YOUTUBE_MANUAL | YOUTUBE_AUTO


def extract_video_id(url_or_id: str) -> str:
    if "youtube.com" not in url_or_id and "youtu.be" not in url_or_id:
        return url_or_id.strip()

    parsed = urlparse(url_or_id)
    if parsed.hostname and parsed.hostname.endswith("youtu.be"):
        return parsed.path.lstrip("/")

    video_ids = parse_qs(parsed.query).get("v")
    if video_ids:
        return video_ids[0]

    raise ValueError(f"Could not extract a video id from: {url_or_id}")


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def fetch_info(video_id: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(watch_url(video_id), download=False)


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
