# FlipTop battle scraper

Lists battle URLs from the [FlipTop Battles](https://www.youtube.com/@FlipTopBattles) YouTube channel for a fixed set of emcees:

- GL (also **Sinagtala**)
- BLKD
- Loonie
- Tipsy D (also **Freak Sanchez**)

Only videos whose title is a vs-card (`vs`, `vs.`, `versus`, `v/s`) are kept. Interviews, flyers, recaps, and coming-soon posts are dropped even if they mention a name. Alter-ego battles are filed under the real emcee.

The crawl uses the channel **Videos** tab, not Shorts. Anything with a `/shorts/` URL or a duration of 3 minutes or less (YouTube's Shorts cap) is dropped, so only full-length uploads remain.

Only public video listings are scraped; nothing else is downloaded.

A second command snapshots emcee profiles from the [official site](https://www.fliptop.com.ph/emcees) so the companion glossary can highlight people, hometowns, and crews.

## Setup

Uses the same venv and `requirements.txt` as the transcription API. From the repo root:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
cd scraper
python -m fliptop_scraper
```

Writes `battles.json`. Useful flags:

```bash
python -m fliptop_scraper --out battles.json
python -m fliptop_scraper --stdout          # JSON on stdout, progress on stderr
python -m fliptop_scraper --limit 200       # first N channel videos only
python -m fliptop_scraper --txt battles.txt # optional readable list
```

The channel dump can take a few minutes. Use `--limit` to smoke-test matching.

## Official-site glossary

```bash
cd scraper
python -m fliptop_scraper.site
```

Crawls `/emcees` (paginated) and each profile. Writes `site.json` with raw emcee records plus compiled glossary `entries` (people, groups, places). The API loads that file on startup via `seed_glossary()`. Useful flags:

```bash
python -m fliptop_scraper.site --out site.json
python -m fliptop_scraper.site --limit 5          # first N profiles only
python -m fliptop_scraper.site --delay 1.0        # seconds between requests
python -m fliptop_scraper.site --stdout
```

The crawl is polite (~0.75s between requests; the site's robots.txt is open). After a fresh snapshot, re-annotate stored battles:

```bash
python scripts/annotate.py --all
```

## Battle transcripts

A third command pulls ready-made, timestamped transcripts from
[Battle BARS PH](https://battlebarsph.com/transcripts/ai), which saves
transcribing the catalogue ourselves — roughly 630 hours of audio.

```bash
cd scraper
python -m fliptop_scraper.transcripts
```

Battle pages there are `/battles/<youtube id>`, the same key the API stores
battles under, so nothing has to be matched on title. Candidates come from the
site's `sitemap.xml` (one request) rather than the 136 paginated listing pages.

```bash
python -m fliptop_scraper.transcripts --catalogue     # only battles.json battles
python -m fliptop_scraper.transcripts --limit 5 --dry-run
python -m fliptop_scraper.transcripts --delay 2.0
python -m fliptop_scraper.transcripts dQw4w9WgXcQ     # specific video ids
```

Each battle is written to `transcripts/<video_id>.json` as it arrives, and a
re-run skips files already on disk, so an interrupted crawl resumes where it
stopped. Pass `--overwrite` to re-fetch.

The pages are Next.js, and the transcript is a JSON array inside the React
Server Components payload. The scraper asks for that payload directly with an
`RSC` header, which is about 40% of the rendered page's bytes; `--full-pages`
falls back to the HTML if that ever stops working.

Lines carry `start`/`end` seconds parsed from the site's SRT-style timestamps,
and about 40% of battles also label `speaker` and `round`. Whisper's stock
phrases over the intro beat ("Outro", "Thank you for watching") are dropped,
as are consecutive duplicate lines. Note symbols are **not** treated as filler
on their own, since the site also uses them to wrap lines an emcee sang.

Parser checks (no network):

```bash
cd scraper
PYTHONPATH=. python -m unittest tests.test_transcripts
```

Load the snapshots into the API so the companion can use them:

```bash
python scripts/ingest_scraped.py --overwrite --dry-run
python scripts/ingest_scraped.py --overwrite
```

The site asks that you request permission before reusing its data; its
`robots.txt` is open and it publishes a sitemap, but email the address in the
site footer before putting any of this anywhere public.

Parser and normalize checks (no network):

```bash
cd scraper
PYTHONPATH=. python -m unittest tests.test_site
```

## Output

`battles.json` has one entry per battle (a GL vs Loonie card is not duplicated) plus a `by_emcee` map of URL lists:

```json
{
  "channel": "https://www.youtube.com/@FlipTopBattles/videos",
  "videos_scanned": 4000,
  "battles": [
    {
      "url": "https://www.youtube.com/watch?v=...",
      "video_id": "...",
      "title": "FlipTop - GL vs Abra @ ...",
      "emcees": ["GL"],
      "matchup": "GL vs Abra",
      "event": "..."
    }
  ],
  "by_emcee": {
    "GL": ["https://www.youtube.com/watch?v=..."]
  }
}
```
