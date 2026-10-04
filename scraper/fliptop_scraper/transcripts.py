"""Fetch and parse timestamped battle transcripts from Battle BARS PH.

The site is a Next.js app and a battle page carries its transcript as a JSON
array inside the React Server Components payload, keyed by YouTube video id.
Asking for the payload directly with an `RSC` header returns the same data at
roughly 40% of the full page's bytes.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import httpx

BASE_URL = "https://battlebarsph.com"
USER_AGENT = "fliptop-transcription-api/0.1 (personal research)"
DEFAULT_DELAY = 1.0

# Battle pages are /battles/<youtube id>, which is the key the API already
# stores battles under, so no title matching is needed.
_BATTLE_PATH = re.compile(r"/battles/([A-Za-z0-9_-]{11})(?:[/?#]|$)")
_SITEMAP_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)

# Server-rendered pages wrap the payload in script tags; the RSC endpoint
# returns the same flight stream unescaped.
_NEXT_PUSH = re.compile(
    r'self\.__next_f\.push\(\s*\[\s*1\s*,\s*"(.*?)"\s*\]\s*\)', re.S
)

_TIMESTAMP = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")

# Note symbols mark a section break (`🎵 Music 🎵`) but also wrap lines an
# emcee sang, so they are stripped before the filler test rather than being
# treated as filler themselves.
_MUSIC_MARK = re.compile(r"[\u266a-\u266c\U0001F3B5\U0001F3B6]")

# These transcripts are Whisper output. Over the intro beat and the outro it
# invents stock phrases from its training data, and the site keeps them.
_FILLER = frozenset(
    {
        "music",
        "intro",
        "outro",
        "intro music",
        "outro music",
        "music playing",
        "applause",
        "silence",
        "thank you for watching",
        "thanks for watching",
        "please subscribe",
    }
)

Fetcher = Callable[[str], str]


def battle_url(video_id: str, base: str = BASE_URL, *, rsc: bool = False) -> str:
    """Canonical battle URL, or the payload URL when `rsc` is set.

    Next.js 307s an `RSC` request to the same path with `?_rsc` appended, so
    asking for it directly halves the round trips.
    """
    url = urljoin(base.rstrip("/") + "/", f"battles/{video_id}")
    return f"{url}?_rsc" if rsc else url


def parse_timestamp(value: str) -> float:
    """`00:01:51,879` or `01:51.879` to seconds."""
    match = _TIMESTAMP.match((value or "").strip())
    if match is None:
        raise ValueError(f"unrecognised timestamp {value!r}")
    hours, minutes, seconds, millis = match.groups()
    total = int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds)
    if millis:
        total += int(millis.ljust(3, "0")) / 1000.0
    return float(total)


def parse_sitemap(xml: str) -> list[str]:
    """Video ids for every battle page the sitemap lists, in order, deduped."""
    seen: set[str] = set()
    ids: list[str] = []
    for loc in _SITEMAP_LOC.findall(xml or ""):
        match = _BATTLE_PATH.search(loc)
        if match is None:
            continue
        video_id = match.group(1)
        if video_id not in seen:
            seen.add(video_id)
            ids.append(video_id)
    return ids


def flight_text(body: str) -> str:
    """Normalise a page or an RSC response into one flight stream.

    A payload can straddle two `__next_f.push` calls, so the chunks are decoded
    as the JS string literals they are and joined before anything is read out
    of them.
    """
    chunks = _NEXT_PUSH.findall(body or "")
    if not chunks:
        return body or ""

    decoded: list[str] = []
    for chunk in chunks:
        try:
            decoded.append(json.loads(f'"{chunk}"'))
        except json.JSONDecodeError:
            continue
    return "".join(decoded)


def _json_array_at(text: str, key: str) -> str | None:
    """Slice the JSON array that follows `"key":` using bracket matching."""
    marker = f'"{key}":['
    start = text.find(marker)
    if start < 0:
        return None

    index = text.index("[", start)
    depth = 0
    in_string = False
    escaped = False

    for cursor in range(index, len(text)):
        char = text[cursor]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[index : cursor + 1]
    return None


def _json_value_at(text: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}":"((?:[^"\\]|\\.)*)"', text)
    if match is None:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return None


def is_filler(text: str) -> bool:
    """True for Whisper's stock phrases and bare section markers.

    A sung line also arrives wrapped in note symbols, so only the generic
    marker words count as filler once the symbols are removed.
    """
    without_marks = _MUSIC_MARK.sub(" ", text or "")
    stripped = re.sub(r"[\W_]+", " ", without_marks, flags=re.UNICODE).strip().lower()
    return not stripped or stripped in _FILLER


def clean_lines(rows: list[dict]) -> list[dict]:
    """Normalise raw site rows, dropping filler and consecutive repeats."""
    cleaned: list[dict] = []

    for row in rows:
        text = (row.get("text") or "").strip()
        if is_filler(text):
            continue
        try:
            start = parse_timestamp(row.get("startTime") or "")
            end = parse_timestamp(row.get("endTime") or "")
        except ValueError:
            continue
        # Whisper re-transcribes either side of a chunk seam, which repeats
        # the line that straddles the cut.
        if cleaned and cleaned[-1]["text"].lower() == text.lower():
            cleaned[-1]["end"] = max(cleaned[-1]["end"], end)
            continue
        cleaned.append(
            {
                "start": start,
                "end": max(end, start),
                "text": text,
                "speaker": (row.get("speaker") or None),
                "round": (row.get("round") or None),
            }
        )

    return cleaned


def parse_battle(body: str, *, video_id: str, url: str) -> dict:
    """Pull one battle's transcript out of a page or RSC response."""
    text = flight_text(body)
    raw = _json_array_at(text, "lines")
    if raw is None:
        raise LookupError(f"{video_id}: no transcript payload on the page")

    try:
        rows = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LookupError(f"{video_id}: transcript payload is not valid JSON") from exc

    lines = clean_lines([row for row in rows if isinstance(row, dict)])
    return {
        "video_id": video_id,
        "url": url,
        "title": _json_value_at(text, "battleTitle") or "",
        "line_count": len(lines),
        "dropped": len(rows) - len(lines),
        "has_speakers": any(line["speaker"] for line in lines),
        "duration": max((line["end"] for line in lines), default=0.0),
        "lines": lines,
    }


def make_fetcher(
    *,
    delay: float = DEFAULT_DELAY,
    user_agent: str = USER_AGENT,
    timeout: float = 60.0,
    rsc: bool = True,
) -> tuple[Fetcher, Callable[[], None]]:
    """A rate-limited GET, matching the official-site scraper's shape."""
    headers = {"User-Agent": user_agent}
    if rsc:
        # Asks Next.js for the flight stream instead of the rendered page.
        headers["RSC"] = "1"

    client = httpx.Client(headers=headers, follow_redirects=True, timeout=timeout)
    last = {"at": 0.0}
    gate = threading.Lock()

    def fetch(url: str) -> str:
        # Only the spacing is serialised, so concurrent workers still issue at
        # most one request per `delay` between them.
        with gate:
            elapsed = time.monotonic() - last["at"]
            if last["at"] and elapsed < delay:
                time.sleep(delay - elapsed)
            last["at"] = time.monotonic()
        response = client.get(url)
        response.raise_for_status()
        return response.text

    return fetch, client.close


def discover_battle_ids(fetch: Fetcher, base: str = BASE_URL) -> list[str]:
    """Every battle id the sitemap lists. One request, not 136 listing pages."""
    return parse_sitemap(fetch(urljoin(base.rstrip("/") + "/", "sitemap.xml")))


def _fetch_one(
    fetch: Fetcher, video_id: str, *, base: str, rsc: bool
) -> tuple[str, dict | None, str | None]:
    url = battle_url(video_id, base=base)
    try:
        body = fetch(battle_url(video_id, base=base, rsc=rsc))
        return video_id, parse_battle(body, video_id=video_id, url=url), None
    except (httpx.HTTPError, LookupError) as exc:
        return video_id, None, str(exc)


def crawl_battles(
    fetch: Fetcher,
    video_ids: list[str],
    *,
    base: str = BASE_URL,
    rsc: bool = False,
    workers: int = 1,
    progress: Callable[[str], None] | None = None,
) -> Iterator[tuple[str, dict | None, str | None]]:
    """Yield `(video_id, battle, error)` so a caller can write as it goes.

    The site's response time swings from under a second to the better part of
    ten, so a few workers overlap that wait. The fetcher still paces the
    requests, which is what keeps the crawl polite.
    """
    total = len(video_ids)
    done = 0

    def report(result: tuple[str, dict | None, str | None]) -> None:
        nonlocal done
        done += 1
        if progress is None:
            return
        video_id, battle, error = result
        if battle is None:
            progress(f"{done}/{total} {video_id} failed: {error}")
        else:
            progress(f"{done}/{total} {video_id} {battle['line_count']} lines")

    if workers <= 1:
        for video_id in video_ids:
            result = _fetch_one(fetch, video_id, base=base, rsc=rsc)
            report(result)
            yield result
        return

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_fetch_one, fetch, video_id, base=base, rsc=rsc)
            for video_id in video_ids
        ]
        try:
            for future in as_completed(futures):
                result = future.result()
                report(result)
                yield result
        except (KeyboardInterrupt, GeneratorExit):
            for future in futures:
                future.cancel()
            raise


if __name__ == "__main__":
    from fliptop_scraper.transcripts_cli import main

    raise SystemExit(main())
