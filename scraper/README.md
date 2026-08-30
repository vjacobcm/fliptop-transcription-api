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

Uses the same venv as the transcription API (`yt-dlp` is already in the root `requirements.txt`). From the repo root:

```bash
source .venv/bin/activate
pip install -r scraper/requirements.txt
```

## Run

```bash
cd scraper
python -m fliptop_scraper
```

Writes `battles.json` and a readable `battles.txt` (titles + links, grouped by emcee). Useful flags:

```bash
python -m fliptop_scraper --out battles.json   # also writes battles.txt
python -m fliptop_scraper --stdout          # JSON on stdout, progress on stderr
python -m fliptop_scraper --limit 200       # first N channel videos only
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

Parser and normalize checks (no network):

```bash
cd scraper
PYTHONPATH=. python -m unittest tests.test_site
```

## Output

`battles.txt` is the readable list: each emcee, then title + URL.

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
