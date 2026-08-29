"""Whisper transcription backends: local faster-whisper and the Groq API."""

import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.models import TranscriptSource

logger = logging.getLogger(__name__)

_local_model = None
_last_request_at = 0.0


@dataclass
class TranscriptionResult:
    segments: list[dict]
    language: str
    source: str


def _load_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        _local_model = WhisperModel(
            settings.whisper_model_size,
            device="cpu",
            compute_type=settings.whisper_compute_type,
        )
    return _local_model


def transcribe_local(audio_path: Path, prompt: str | None = None) -> TranscriptionResult:
    model = _load_local_model()

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=settings.whisper_language or None,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.whisper_vad_filter,
        initial_prompt=prompt or settings.whisper_initial_prompt or None,
    )

    segments = [
        {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
        for seg in segments_iter
        if seg.text and seg.text.strip()
    ]

    return TranscriptionResult(
        segments=clean_segments(segments, prompt or settings.whisper_initial_prompt),
        language=getattr(info, "language", settings.whisper_language),
        source=TranscriptSource.WHISPER_LOCAL,
    )


def _max_chunk_seconds() -> int:
    """Longest chunk that still fits the upload limit at our export bitrate."""
    kbps = int(settings.groq_chunk_bitrate.rstrip("k") or 64)
    bytes_per_second = kbps * 1000 / 8
    fits = int(settings.groq_max_upload_mb * 1024 * 1024 / bytes_per_second)
    return max(60, min(settings.groq_chunk_seconds, fits))


def _split_audio(audio_path: Path) -> list[tuple[Path, float]]:
    """Split into chunks that stay under Groq's upload limit, with time offsets."""
    from pydub import AudioSegment

    chunk_seconds = _max_chunk_seconds()
    size_mb = audio_path.stat().st_size / (1024 * 1024)

    audio = AudioSegment.from_file(audio_path)
    total_seconds = len(audio) / 1000.0

    if total_seconds <= chunk_seconds and size_mb <= settings.groq_max_upload_mb:
        return [(audio_path, 0.0)]

    chunk_dir = settings.audio_dir / f"{audio_path.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    boundaries = [i * chunk_seconds for i in range(math.ceil(total_seconds / chunk_seconds))]

    # Whisper pads anything under 30s with silence, which invites hallucinated
    # text, so fold a short tail back into the chunk before it.
    if len(boundaries) > 1 and total_seconds - boundaries[-1] < 30:
        boundaries.pop()

    chunks: list[tuple[Path, float]] = []
    for index, offset in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else total_seconds
        chunk_path = chunk_dir / f"{index:03d}.mp3"
        if not chunk_path.exists():
            audio[offset * 1000 : int(end * 1000)].export(
                chunk_path,
                format="mp3",
                bitrate=settings.groq_chunk_bitrate,
                parameters=["-ac", "1", "-ar", "16000"],
            )
        chunks.append((chunk_path, float(offset)))

    return chunks


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).lower().strip()


# Distinctive leftovers from older, longer prompts. Whisper still emits these
# over music even after the prompt was shortened.
_PROMPT_SIGNATURES = (
    "mga bar punchline rebuttal at flip",
    "fliptop battle rap",
    "tagalog at english",
)


def _is_prompt_echo(text: str, prompt: str | None) -> bool:
    """True when a segment is Whisper reciting the prompt, not speech."""
    normalised = _normalise(text)
    if len(normalised) < 8:
        return False

    needles = [_normalise(prompt or "")] + list(_PROMPT_SIGNATURES)
    for needle in needles:
        if not needle or len(needle) < 8:
            continue
        if normalised in needle or needle in normalised:
            return True

    words = normalised.split()
    prompt_words = set(_normalise(prompt or "").split())
    for signature in _PROMPT_SIGNATURES:
        prompt_words.update(signature.split())
    if len(words) >= 4 and prompt_words:
        overlap = sum(1 for word in words if word in prompt_words) / len(words)
        if overlap >= 0.85:
            return True

    return False


def clean_segments(segments: list[dict], prompt: str | None) -> list[dict]:
    """Drop prompt echoes and repeated lines from Whisper output.

    Over music or silence Whisper tends to emit its own prompt back as if it
    were speech, and it re-transcribes the audio either side of a chunk seam,
    which duplicates the line that straddles the cut.
    """
    cleaned: list[dict] = []

    for segment in segments:
        text = (segment["text"] or "").strip()
        if not text:
            continue

        if _is_prompt_echo(text, prompt):
            logger.info("Dropping prompt echo at %.1fs: %r", segment["start"], text)
            continue

        if cleaned and _normalise(cleaned[-1]["text"]) == _normalise(text):
            logger.info("Dropping repeat at %.1fs: %r", segment["start"], text)
            continue

        cleaned.append(segment)

    return cleaned


def _throttle() -> None:
    """Space requests out to stay inside the configured requests-per-minute."""
    global _last_request_at

    if settings.groq_requests_per_minute <= 0:
        return

    interval = 60.0 / settings.groq_requests_per_minute
    wait = interval - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _retry_after(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), 300.0)
        except ValueError:
            pass
    return min(2.0**attempt, 60.0)


def _post_chunk(
    endpoint: str, headers: dict, chunk_path: Path, data: dict
) -> httpx.Response:
    """POST one chunk, backing off on rate limits and transient failures."""
    for attempt in range(settings.groq_max_retries):
        _throttle()

        with chunk_path.open("rb") as handle:
            files = {"file": (chunk_path.name, handle, "audio/mpeg")}
            response = httpx.post(
                endpoint, headers=headers, files=files, data=data, timeout=600.0
            )

        if response.status_code == 429 or response.status_code >= 500:
            delay = _retry_after(response, attempt)
            logger.warning(
                "Groq returned %s for %s; retrying in %.1fs (attempt %d/%d)",
                response.status_code,
                chunk_path.name,
                delay,
                attempt + 1,
                settings.groq_max_retries,
            )
            time.sleep(delay)
            continue

        response.raise_for_status()
        return response

    raise RuntimeError(
        f"Groq kept rejecting {chunk_path.name} after "
        f"{settings.groq_max_retries} attempts. The free tier allows "
        f"{settings.groq_daily_audio_seconds // 3600}h of audio per day."
    )


def transcribe_groq(audio_path: Path, prompt: str | None = None) -> TranscriptionResult:
    if not settings.groq_api_key:
        raise RuntimeError(
            "TRANSCRIPTION_BACKEND=groq but GROQ_API_KEY is empty. "
            "Create a key at https://console.groq.com/keys and put it in .env"
        )

    endpoint = f"{settings.groq_api_base.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    data = {
        "model": settings.groq_model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    if settings.whisper_language:
        data["language"] = settings.whisper_language
    if prompt or settings.whisper_initial_prompt:
        data["prompt"] = prompt or settings.whisper_initial_prompt

    segments: list[dict] = []
    language = settings.whisper_language
    chunks = _split_audio(audio_path)

    for index, (chunk_path, offset) in enumerate(chunks, start=1):
        logger.info("Groq chunk %d/%d (offset %.0fs)", index, len(chunks), offset)
        payload = _post_chunk(endpoint, headers, chunk_path, data).json()
        language = payload.get("language") or language

        for seg in payload.get("segments") or []:
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            segments.append(
                {
                    "start": float(seg.get("start", 0.0)) + offset,
                    "end": float(seg.get("end", 0.0)) + offset,
                    "text": text,
                }
            )

    return TranscriptionResult(
        segments=clean_segments(segments, data.get("prompt")),
        language=language,
        source=TranscriptSource.WHISPER_GROQ,
    )


def transcribe(audio_path: Path, prompt: str | None = None) -> TranscriptionResult:
    backend = settings.transcription_backend.lower()

    if backend == "groq":
        return transcribe_groq(audio_path, prompt)
    if backend == "local":
        return transcribe_local(audio_path, prompt)

    raise RuntimeError(
        f"Transcription backend '{backend}' cannot transcribe audio. "
        "Use 'local' or 'groq'."
    )
