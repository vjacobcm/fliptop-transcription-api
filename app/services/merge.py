"""Fill the holes Whisper leaves with whatever YouTube heard there.

Whisper suppresses stretches it reads as non-speech, which on a FlipTop upload
means the intro and outro interviews — speech over a music bed — silently
vanish. YouTube's auto-captions are worse line for line but they do not drop
those sections, so they make a decent patch for exactly the gaps.
"""

import logging

logger = logging.getLogger(__name__)

# Below this a gap is just the pause between bars, not a dropout.
MIN_GAP_SECONDS = 4.0

# A filler segment must sit mostly inside the gap to be worth pulling in.
MIN_OVERLAP_RATIO = 0.5

# Whisper's internal window is 30s. When it hears music it still *covers*
# that window with two words, which hides the real speech from gap-fill.
HOLLOW_MIN_SECONDS = 15.0
HOLLOW_MAX_WORDS_PER_SECOND = 0.8


def find_gaps(
    segments: list[dict], duration: float | None, min_gap: float = MIN_GAP_SECONDS
) -> list[tuple[float, float]]:
    """Stretches of the timeline no segment covers."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0

    for segment in sorted(segments, key=lambda s: s["start"]):
        if segment["start"] - cursor >= min_gap:
            gaps.append((cursor, segment["start"]))
        cursor = max(cursor, segment["end"])

    if duration and duration - cursor >= min_gap:
        gaps.append((cursor, duration))

    return gaps


def drop_hollow(segments: list[dict]) -> tuple[list[dict], int]:
    """Remove segments that cover a long stretch with almost no words.

    Those are not pauses — Whisper assigned a 30s window to a two-word
    hallucination, and the bars that were actually said in that window are
    gone. Dropping them re-opens the span so captions can fill it.
    """
    kept: list[dict] = []
    dropped = 0

    for segment in segments:
        duration = segment["end"] - segment["start"]
        words = len((segment["text"] or "").split())
        rate = words / duration if duration > 0 else 0
        if duration >= HOLLOW_MIN_SECONDS and rate < HOLLOW_MAX_WORDS_PER_SECOND:
            logger.info(
                "Dropping hollow %.1fs segment at %.1fs (%d words): %r",
                duration,
                segment["start"],
                words,
                segment["text"][:80],
            )
            dropped += 1
            continue
        kept.append(segment)

    return kept, dropped


def fill_gaps(
    primary: list[dict],
    filler: list[dict],
    *,
    duration: float | None = None,
    primary_source: str,
    filler_source: str,
    min_gap: float = MIN_GAP_SECONDS,
) -> tuple[list[dict], int]:
    """Merge `filler` into the gaps in `primary`, tagging each segment's origin.

    Returns the merged, re-indexed segments and how many were borrowed.
    """
    merged = [{**segment, "source": primary_source} for segment in primary]

    if not filler:
        return merged, 0

    gaps = find_gaps(primary, duration, min_gap)
    if not gaps:
        return merged, 0

    borrowed = 0
    for start, end in gaps:
        for segment in filler:
            overlap = min(segment["end"], end) - max(segment["start"], start)
            length = segment["end"] - segment["start"]
            if length <= 0 or overlap / length < MIN_OVERLAP_RATIO:
                continue

            merged.append(
                {
                    "start": max(segment["start"], start),
                    "end": min(segment["end"], end),
                    "text": segment["text"],
                    "source": filler_source,
                }
            )
            borrowed += 1

    merged.sort(key=lambda s: (s["start"], s["end"]))

    if borrowed:
        recovered = sum(e - s for s, e in gaps)
        logger.info(
            "Filled %d of %.0fs of gaps with %d %s segments",
            len(gaps),
            recovered,
            borrowed,
            filler_source,
        )

    return merged, borrowed
