"""Render stored segments as SRT or WebVTT."""

from collections.abc import Sequence


def _clock(seconds: float, separator: str) -> str:
    if seconds < 0:
        seconds = 0.0
    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def to_srt(segments: Sequence) -> str:
    blocks = []
    for number, segment in enumerate(segments, start=1):
        start = _clock(segment.start, ",")
        end = _clock(segment.end, ",")
        blocks.append(f"{number}\n{start} --> {end}\n{segment.text}\n")
    return "\n".join(blocks)


def to_vtt(segments: Sequence) -> str:
    lines = ["WEBVTT", ""]
    for segment in segments:
        start = _clock(segment.start, ".")
        end = _clock(segment.end, ".")
        lines.append(f"{start} --> {end}")
        lines.append(segment.text)
        lines.append("")
    return "\n".join(lines)
