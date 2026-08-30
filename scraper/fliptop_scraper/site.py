"""Fetch and parse FlipTop official-site emcee pages."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.fliptop.com.ph"
USER_AGENT = "fliptop-transcription-api/0.1 (glossary research)"
DEFAULT_DELAY = 0.75

_EMCEE_PATH = re.compile(r"^/emcees/([^/]+)/?$")
_PAGE_RE = re.compile(r"[?&]page=(\d+)")
_YEAR_RE = re.compile(r"\d{4}")
_SKIP_SLUGS = frozenset({"division"})

Fetcher = Callable[[str], str]


def emcee_url(slug: str, base: str = BASE_URL) -> str:
    return urljoin(base.rstrip("/") + "/", f"emcees/{slug}")


def _emcee_slug(href: str) -> str | None:
    path = urlparse(href).path.rstrip("/")
    match = _EMCEE_PATH.match(path)
    if match is None:
        return None
    slug = match.group(1)
    if slug.lower() in _SKIP_SLUGS:
        return None
    return slug


def _clean(text: str) -> str:
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(text.split()).strip()


def parse_emcee_index(html: str) -> tuple[list[dict], int]:
    """Return (listing cards, last page number) from an /emcees index page."""
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict] = []
    seen: set[str] = set()
    for link in soup.select("a[href]"):
        if link.select_one(".emcee-card") is None:
            continue
        slug = _emcee_slug(link.get("href") or "")
        if not slug or slug in seen:
            continue
        name = ""
        image = link.select_one("img[title]")
        if image and image.get("title"):
            name = _clean(image["title"])
        if not name:
            heading = link.select_one("h4")
            if heading:
                name = _clean(heading.get_text(" ", strip=True))
        seen.add(slug)
        cards.append({"slug": slug, "name": name})

    last_page = 1
    for link in soup.select(".pagination a.page-link"):
        href = link.get("href") or ""
        match = _PAGE_RE.search(href)
        if match:
            last_page = max(last_page, int(match.group(1)))
    return cards, last_page


def _field_map(details) -> dict[str, str]:
    fields: dict[str, str] = {}
    if details is None:
        return fields
    labels = ("Hometown", "Reppin", "Division", "Year Joined")
    for item in details.select("li"):
        text = _clean(item.get_text(" ", strip=True))
        for label in labels:
            prefix = f"{label}:"
            if text.lower().startswith(label.lower()) and ":" in text:
                fields[label] = _clean(text.split(":", 1)[1])
                break
    return fields


def _titles(details) -> list[str]:
    if details is None:
        return []
    titles: list[str] = []
    for badge in details.select("a.badge"):
        text = _clean(badge.get_text(" ", strip=True))
        if text:
            titles.append(text)
    return titles


def _first_bio(soup: BeautifulSoup) -> str:
    heading = soup.select_one(".ft-emcee")
    parent = heading.find_parent("div", class_="col-md-8") if heading else None
    if parent is None:
        parent = soup.select_one(".ft-emcee-details")
        parent = parent.parent if parent else None
    if parent is None:
        return ""
    for paragraph in parent.find_all("p"):
        text = _clean(paragraph.get_text(" ", strip=True))
        if len(text) > 40:
            return text
    return ""


def parse_emcee_profile(html: str, *, slug: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one(".ft-emcee h1") or soup.select_one("h1")
    name = _clean(heading.get_text(" ", strip=True)) if heading else slug
    details = soup.select_one(".ft-emcee-details")
    fields = _field_map(details)
    year_raw = fields.get("Year Joined") or ""
    year_match = _YEAR_RE.search(year_raw)
    return {
        "slug": slug,
        "name": name,
        "hometown": fields.get("Hometown") or "",
        "reppin_raw": fields.get("Reppin") or "",
        "division": fields.get("Division") or "",
        "year_joined": int(year_match.group(0)) if year_match else None,
        "titles": _titles(details),
        "bio": _first_bio(soup),
        "url": url,
    }


def make_fetcher(
    *,
    delay: float = DEFAULT_DELAY,
    user_agent: str = USER_AGENT,
    timeout: float = 30.0,
) -> tuple[Fetcher, Callable[[], None]]:
    client = httpx.Client(
        headers={"User-Agent": user_agent},
        follow_redirects=True,
        timeout=timeout,
    )
    last = {"at": 0.0}

    def fetch(url: str) -> str:
        elapsed = time.monotonic() - last["at"]
        if last["at"] and elapsed < delay:
            time.sleep(delay - elapsed)
        response = client.get(url)
        last["at"] = time.monotonic()
        response.raise_for_status()
        return response.text

    return fetch, client.close


def list_emcee_cards(fetch: Fetcher, base: str = BASE_URL) -> list[dict]:
    first = fetch(f"{base.rstrip('/')}/emcees")
    cards, last_page = parse_emcee_index(first)
    by_slug = {card["slug"]: card for card in cards}
    for page in range(2, last_page + 1):
        html = fetch(f"{base.rstrip('/')}/emcees?page={page}")
        more, _ = parse_emcee_index(html)
        for card in more:
            by_slug.setdefault(card["slug"], card)
    return list(by_slug.values())


def crawl_emcees(
    fetch: Fetcher,
    *,
    base: str = BASE_URL,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[dict]:
    cards = list_emcee_cards(fetch, base=base)
    if limit is not None:
        cards = cards[: max(limit, 0)]
    if progress:
        progress(f"listed {len(cards)} emcees")

    profiles: list[dict] = []
    for index, card in enumerate(cards, start=1):
        url = emcee_url(card["slug"], base=base)
        if progress:
            progress(f"fetching {index}/{len(cards)} {card['slug']}")
        profiles.append(parse_emcee_profile(fetch(url), slug=card["slug"], url=url))
    return profiles


if __name__ == "__main__":
    from fliptop_scraper.site_cli import main

    raise SystemExit(main())
