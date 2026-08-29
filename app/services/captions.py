"""Parse YouTube caption payloads into timestamped segments.

Auto-generated tracks emit rolling captions: the same phrase is re-sent with a
few extra words appended each time. Left alone that produces a transcript where
every line repeats its predecessor, so `clean_segments` collapses those.
"""

import json
import re

TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_json3(raw: str) -> list[dict]:
    data = json.loads(raw)
    segments: list[dict] = []

    for event in data.get("events") or []:
        segs = event.get("segs")
        if not segs:
            continue

        text = _norm("".join(seg.get("utf8", "") for seg in segs))
        if not text:
            continue

        start = event.get("tStartMs", 0) / 1000.0
        duration = event.get("dDurationMs", 0) / 1000.0
        segments.append({"start": start, "end": start + duration, "text": text})

    return segments


def parse_vtt(raw: str) -> list[dict]:
    segments: list[dict] = []
    current: dict | None = None

    for line in raw.splitlines():
        match = TIMESTAMP_RE.search(line)
        if match:
            if current and current["text"]:
                segments.append(current)
            h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
            current = {
                "start": h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0,
                "end": h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0,
                "text": "",
            }
            continue

        if current is None:
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith(("WEBVTT", "NOTE", "Kind:", "Language:")):
            continue

        cleaned = _norm(TAG_RE.sub("", stripped))
        if cleaned:
            current["text"] = _norm(f"{current['text']} {cleaned}")

    if current and current["text"]:
        segments.append(current)

    return segments


def clean_segments(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates and drop empties, preserving timings."""
    cleaned: list[dict] = []

    for segment in segments:
        text = _norm(segment["text"])
        if not text:
            continue

        start = float(segment["start"])
        end = max(float(segment["end"]), start)

        if cleaned:
            previous = cleaned[-1]
            # Exact repeat, or a rolling caption that extends the previous line.
            if text == previous["text"] or text.startswith(previous["text"] + " "):
                previous["text"] = text
                previous["end"] = max(previous["end"], end)
                continue
            # Previous line was a prefix fragment of this one's start.
            if previous["text"].startswith(text + " "):
                previous["end"] = max(previous["end"], end)
                continue

        cleaned.append({"start": start, "end": end, "text": text})

    for index, segment in enumerate(cleaned[:-1]):
        # Auto-captions often overlap; clamp so playback sync stays sane.
        next_start = cleaned[index + 1]["start"]
        if segment["end"] > next_start:
            segment["end"] = max(segment["start"], next_start)

    return cleaned


def parse_caption_payload(raw: str, ext: str) -> list[dict]:
    if ext == "json3":
        parsed = parse_json3(raw)
    elif ext in {"vtt", "srv1", "srv2", "srv3", "ttml", "srt"}:
        parsed = parse_vtt(raw)
    else:
        raise ValueError(f"Unsupported caption format: {ext}")

    return clean_segments(parsed)
