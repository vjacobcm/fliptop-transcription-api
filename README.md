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

## Transcribing in batches

Groq's free tier covers 8 hours of audio a day with no card, which is enough to
work through the catalog a few battles at a time. Point `.env` at it:

```
TRANSCRIPTION_BACKEND=groq
GROQ_API_KEY=gsk_...
```

Confirm the key works before spending any quota:

```bash
python scripts/check_groq.py
```

Then plan a run:

```bash
python scripts/backfill.py --upgrade --limit 2 --dry-run   # re-do auto-caption battles
python scripts/backfill.py --upgrade --limit 2
python scripts/backfill.py --playlist "<channel or playlist URL>" --limit 5
```

The runner adds up durations first and stops before it would exceed
`--budget-seconds` (the free-tier day by default), skips battles already
transcribed, and paces requests to stay under the rate limit. Uploads are split
into chunks under the size cap, and a chunk shorter than 30 seconds is folded
into the one before it so Whisper is never fed mostly-silence.

Each battle gets a prompt seeded with the emcee names parsed from its title, so
proper nouns come back spelled correctly.

## Search

Every stored segment is indexed with SQLite FTS5. This is deliberately not an
HTTP endpoint — the companion works within one battle at a time. It exists for
offline use and as the groundwork for resolving references a battle makes to
lines from earlier battles.

```bash
python scripts/search.py "lahat ng" --limit 5
```

Queries accept bare terms (all must match), `"exact phrases"`, and `prefix*`.
Each hit reports the battle and a link to the exact second. The index is
maintained by database triggers, so ingest and delete keep it current
automatically; `app.services.search.reindex()` rebuilds it if it drifts.

`GET /battles` is paginated too — `limit`, `offset`, and `status`, `source`,
`channel` filters, with the unpaginated total in the `X-Total-Count` header.