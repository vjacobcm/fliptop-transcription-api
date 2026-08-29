# FlipTop Transcription API

Builds a corpus of timestamped FlipTop battle transcripts from YouTube.

Ingest is **tiered** — it takes the cheapest source that works:

1. **YouTube manual captions** (human-made, rare, best quality)
2. **YouTube auto-captions** (Filipino ASR — instant and free)
3. **Whisper** on downloaded audio (local `faster-whisper` or the Groq API)

Every battle records which source produced it, so lower-quality transcripts can
be re-ingested with Whisper later without changing anything else.

## Requirements

- Python 3.11+
- FFmpeg (`brew install ffmpeg`)

## Setup

```bash
cd ~/Projects/fliptop-transcription-api

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

## Run the API

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Interactive docs: <http://127.0.0.1:8000/docs>