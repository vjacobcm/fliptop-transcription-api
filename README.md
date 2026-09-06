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

A Postman collection lives in `postman/FlipTop-API.postman_collection.json`.
Import that file in Postman (Import → file). Companion requests are the watch
overlay; Ops is catalogue ingest and mention review.

## Chrome companion

`extension/` is an unpacked Manifest V3 extension. On a YouTube watch page it
asks the API for that video — battle, transcript, mentions — and paints timed
subtitles on the player. It never ingests. If the video is not in the catalogue
the page stays untouched.

1. Start the API (`uvicorn app.main:app --reload`).
2. Chrome → `chrome://extensions` → Developer mode → Load unpacked → `extension/`.
3. Open an ingested battle, e.g. `https://www.youtube.com/watch?v=YHUaTOiGXBI`.
4. Use the toolbar popup to turn the overlay on or off. Alt+F also toggles it.

Turn off YouTube's own captions if they sit on top of the companion lines.
The popup talks to `http://127.0.0.1:8000` by default.

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

## Building the catalogue

`--catalogue` takes its candidates from `scraper/battles.json` instead of a
playlist, and `--captions-only` restricts the run to YouTube's own caption
tracks. That combination downloads no audio and spends no Groq quota, so it is
the cheapest way to grow the catalogue:

```bash
python scripts/backfill.py --catalogue --captions-only --dry-run
python scripts/backfill.py --catalogue --captions-only --delay 8
```

Battles already stored as `ready` are skipped, so the command is safe to re-run
and will pick up wherever the last one left off.

Two things go wrong on a large run, and they need different responses:

- **`429 Too Many Requests`.** YouTube rate-limits the caption endpoint after a
  few dozen downloads and blocks the IP for hours. Neither a browser
  User-Agent nor yt-dlp's own downloader gets around it. Caption downloads
  retry with exponential backoff, and the run aborts once `--max-throttles`
  battles fail in a row rather than grinding through the rest. The captions are
  fine — re-run later with a larger `--delay`.
- **No caption track at all.** Some battles have never had captions generated,
  so no amount of waiting helps and they need Whisper. Transcribe just those,
  leaving the rate-limited ones for a later captions pass:

  ```bash
  python scripts/backfill.py --catalogue --only-no-captions
  ```

Audio downloads and Groq are unaffected by a caption block, so the Whisper path
keeps working while one is in effect.

After a backfill, refresh the combined transcript dump so it matches the
database:

```bash
python scripts/export_all.py           # transcripts/all_battles.txt
python scripts/export_all.py --format timed
```

It writes an index followed by every ready battle, and reports how many
catalogue battles are still untranscribed. The file is derived from the
database and is gitignored; re-run the script rather than editing it.

### Disk use

Whisper runs cache downloaded audio in `data/audio/`, at roughly 25 MB per
battle. Groq needs that split into upload chunks, which costs about as much
again, so the chunks are deleted once every chunk of a battle has come back —
a failed run keeps them so a retry does not re-encode. Set
`KEEP_GROQ_CHUNKS=true` to hold on to them when debugging a bad transcript.

Source mp3s are kept, since they make a re-transcribe free. They are safe to
delete whenever the transcript is stored; the next run just downloads again:

```bash
rm -f data/audio/*.mp3
```
