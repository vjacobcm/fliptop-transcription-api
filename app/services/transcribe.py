"""Whisper transcription backends: local faster-whisper and the Groq API."""

import math
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.models import TranscriptSource

_local_model = None


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


def transcribe_local(audio_path: Path) -> TranscriptionResult:
    model = _load_local_model()

    segments_iter, info = model.transcribe(
        str(audio_path),
        language=settings.whisper_language or None,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.whisper_vad_filter,
        initial_prompt=settings.whisper_initial_prompt or None,
    )

    segments = [
        {"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip()}
        for seg in segments_iter
        if seg.text and seg.text.strip()
    ]

    return TranscriptionResult(
        segments=segments,
        language=getattr(info, "language", settings.whisper_language),
        source=TranscriptSource.WHISPER_LOCAL,
    )


def _split_audio(audio_path: Path, chunk_seconds: int) -> list[tuple[Path, float]]:
    """Split into chunks that stay under Groq's upload limit, with time offsets."""
    from pydub import AudioSegment

    audio = AudioSegment.from_file(audio_path)
    total_seconds = len(audio) / 1000.0

    if total_seconds <= chunk_seconds:
        return [(audio_path, 0.0)]

    chunk_dir = settings.audio_dir / f"{audio_path.stem}_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[tuple[Path, float]] = []
    count = math.ceil(total_seconds / chunk_seconds)

    for index in range(count):
        offset = index * chunk_seconds
        piece = audio[offset * 1000 : (offset + chunk_seconds) * 1000]
        chunk_path = chunk_dir / f"{index:03d}.mp3"
        if not chunk_path.exists():
            piece.export(chunk_path, format="mp3", parameters=["-ac", "1", "-ar", "16000"])
        chunks.append((chunk_path, float(offset)))

    return chunks


def transcribe_groq(audio_path: Path) -> TranscriptionResult:
    if not settings.groq_api_key:
        raise RuntimeError(
            "TRANSCRIPTION_BACKEND=groq but GROQ_API_KEY is empty. "
            "Create a key at https://console.groq.com/keys and put it in .env"
        )

    endpoint = f"{settings.groq_api_base.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    segments: list[dict] = []
    language = settings.whisper_language

    for chunk_path, offset in _split_audio(audio_path, settings.groq_chunk_seconds):
        with chunk_path.open("rb") as handle:
            files = {"file": (chunk_path.name, handle, "audio/mpeg")}
            data = {
                "model": settings.groq_model,
                "response_format": "verbose_json",
                "temperature": "0",
            }
            if settings.whisper_language:
                data["language"] = settings.whisper_language
            if settings.whisper_initial_prompt:
                data["prompt"] = settings.whisper_initial_prompt

            response = httpx.post(
                endpoint, headers=headers, files=files, data=data, timeout=600.0
            )

        response.raise_for_status()
        payload = response.json()
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
        segments=segments, language=language, source=TranscriptSource.WHISPER_GROQ
    )


def transcribe(audio_path: Path) -> TranscriptionResult:
    backend = settings.transcription_backend.lower()

    if backend == "groq":
        return transcribe_groq(audio_path)
    if backend == "local":
        return transcribe_local(audio_path)

    raise RuntimeError(
        f"Transcription backend '{backend}' cannot transcribe audio. "
        "Use 'local' or 'groq'."
    )
