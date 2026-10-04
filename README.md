# FlipTop Transcription API

A catalogue of timestamped FlipTop battle transcripts for a Chrome companion
that paints subtitles and named references on the YouTube player.

Transcripts come from [Battle BARS PH](https://battlebarsph.com/transcripts/ai),
which already has timed lines (and often speaker / round). The API does not
transcribe a battle every time you watch it. Whisper and YouTube captions are
still there for videos the site does not have.

The glossary marks known people, crews, places, and events in stored lines.
The two emcees on the card are skipped. An optional Groq LLM pass can nominate
names the dictionary missed.

## Requirements

- Python 3.11+
- FFmpeg only if you fall back to Whisper (`brew install ffmpeg`)

## Setup

```bash
cd ~/Projects/fliptop-transcription-api

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
```

Add a free `GROQ_API_KEY` from <https://console.groq.com/keys> if you want the
LLM name pass. It is not required to scrape, ingest, or run the companion.

## Catalogue

```bash
cd scraper
python -m fliptop_scraper.transcripts          # snapshots in scraper/transcripts/
cd ..
python scripts/ingest_scraped.py --overwrite   # Battle + Segment rows
python scripts/annotate.py --all               # glossary mentions
```

The scrape is resumable (already-written ids are skipped). Ingest replaces a
battle already stored from captions or Whisper when `--overwrite` is set.
Details and the official-site glossary crawl live in [scraper/README.md](scraper/README.md).

The site asks for permission before reuse. `robots.txt` is open; email the
address in their footer before anything public-facing.

Optional LLM pass (free Groq chat model, `GROQ_NER_MODEL` in `.env`):

```bash
python scripts/ner.py --purge            # drop an old spaCy / junk layer
python scripts/ner.py YHUaTOiGXBI
python scripts/ner.py --all
```

## Run the API

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

Interactive docs: <http://127.0.0.1:8000/docs>

A Postman collection lives in `postman/FlipTop-API.postman_collection.json`.
Companion requests are the watch overlay; Ops is catalogue ingest and mention
review.

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

## Gaps the site does not cover

A few catalogue videos have no BattleBars page. Those still go through the old
tier: YouTube captions, then Whisper (`local` or `groq`).

```
TRANSCRIPTION_BACKEND=groq
GROQ_API_KEY=gsk_...
```

```bash
python scripts/check_groq.py
python scripts/backfill.py --catalogue --limit 5 --dry-run
python scripts/backfill.py --catalogue --only-no-captions
```

`scripts/export_all.py` writes a gitignored dump of every ready battle to
`transcripts/all_battles.txt`. Re-run it after ingest; do not edit the file.
