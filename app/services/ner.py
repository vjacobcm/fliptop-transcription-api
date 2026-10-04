"""LLM NER on stored segments, after the glossary scan.

Groq's free chat models nominate people / crews / places / events. The
glossary stays the high-precision layer: we never create an entry for a
Tagalog function word, we skip the two emcees on the card, and we skip any
span the dictionary already marked. Confirmed and rejected mentions stay.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict

import httpx
from sqlmodel import Session, delete, select

from app.config import settings
from app.models import (
    Alias,
    Battle,
    BattleStatus,
    Entry,
    EntryKind,
    Mention,
    MentionDetector,
    MentionStatus,
    Segment,
)
from app.services.glossary import (
    SEED,
    _NOISE_ALIASES,
    _looks_like_acronym,
    _skip_ids_for_battle,
    _usable_alias,
    find_spans,
    load_site_entries,
    slugify,
    upsert_entry,
)
from app.services.titles import parse_matchup

logger = logging.getLogger(__name__)

_KINDS = {
    "person": EntryKind.PERSON,
    "event": EntryKind.EVENT,
    "place": EntryKind.PLACE,
    "location": EntryKind.PLACE,
    "loc": EntryKind.PLACE,
    "group": EntryKind.GROUP,
    "org": EntryKind.GROUP,
    "organization": EntryKind.GROUP,
    "work": EntryKind.WORK,
    "concept": EntryKind.CONCEPT,
}

_LEADING = frozenset(
    {
        "a",
        "ang",
        "kay",
        "mga",
        "ni",
        "sa",
        "shoutout",
        "si",
        "thank",
        "thanks",
        "the",
        "yung",
    }
)
_TOKEN = re.compile(r"\S+")
_JSON_ARRAY = re.compile(r"\[.*\]", re.S)

_SYSTEM = """\
You extract named entities from FlipTop battle-rap transcripts (Tagalog mixed with English).
Return JSON only: {"entities":[{"name":"...","kind":"person|event|place|group|work|concept","blurb":"..."}]}
Rules:
- Keep real names: emcees, crews, places, events, well-known references.
- Use the common name, not a phrase. "Shoutout kay Abra" → Abra.
- Do not list the battling emcees named in the user message.
- Do not list Tagalog grammar or filler (kaya, yun, wala, pag, kaso, parang, ikaw, paano, alam, yan, tsaka, palakpakan, crowd, bars, round).
- Skip anything you are not sure is a name.
- blurb is one short clause, or empty.
"""

_last_request_at = 0.0


def overlaps(start: int, end: int, claimed: list[tuple[int, int]]) -> bool:
    return any(start < other_end and end > other_start for other_start, other_end in claimed)


def model_usable(text: str, kind: str | None = None) -> bool:
    """Drop filler and all-lowercase single tokens."""
    label = (text or "").strip()
    if not _usable_alias(label, kind):
        return False
    if label.lower() in _NOISE_ALIASES:
        return False
    if len(label) > 40 or len(label.split()) > 3:
        return False
    if " " not in label and label == label.lower() and not _looks_like_acronym(label):
        return False
    return True


def trim_entity(text: str, start: int, end: int) -> tuple[str, int, int] | None:
    """Peel leading 'Shoutout kay' / 'si' so the stored alias is the name."""
    for match in _TOKEN.finditer(text[start:end]):
        token = match.group(0).strip(".,!?")
        if token.lower() in _LEADING:
            continue
        new_start = start + match.start()
        surface = text[new_start:end].strip()
        new_end = new_start + len(surface)
        if not surface:
            return None
        return surface, new_start, new_end
    return None


def parse_entities(payload: str) -> list[dict]:
    """Read {"entities":[...]} or a bare list out of an LLM reply."""
    text = (payload or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_ARRAY.search(text)
        if match is None:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    rows = data.get("entities") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []

    entities: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = (row.get("name") or "").strip()
        kind = _KINDS.get((row.get("kind") or "person").strip().lower(), EntryKind.PERSON)
        if not name or name.lower() in seen or not model_usable(name, kind):
            continue
        seen.add(name.lower())
        entities.append(
            {
                "name": name,
                "kind": kind,
                "blurb": (row.get("blurb") or "").strip(),
            }
        )
    return entities


def _throttle() -> None:
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


def groq_extract(lines: list[str], *, emcees: list[str]) -> list[dict]:
    """Ask Groq for named entities in one transcript chunk."""
    if not settings.groq_api_key:
        raise RuntimeError(
            "GROQ_API_KEY is empty. Create a free key at https://console.groq.com/keys"
        )
    if not lines:
        return []

    skip = ", ".join(emcees) if emcees else "(none)"
    user = (
        f"Battling emcees (do not list): {skip}\n\n"
        + "\n".join(lines)
    )
    endpoint = f"{settings.groq_api_base.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    body = {
        "model": settings.groq_ner_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ],
    }

    last_error: Exception | None = None
    for attempt in range(settings.groq_max_retries):
        _throttle()
        try:
            response = httpx.post(endpoint, headers=headers, json=body, timeout=120.0)
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(min(2.0**attempt, 30.0))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            delay = _retry_after(response, attempt)
            logger.warning(
                "Groq NER returned %s; retrying in %.1fs", response.status_code, delay
            )
            time.sleep(delay)
            continue
        response.raise_for_status()
        content = (
            (response.json().get("choices") or [{}])[0]
            .get("message", {})
            .get("content")
            or ""
        )
        return parse_entities(content)

    raise RuntimeError(
        f"Groq NER failed after {settings.groq_max_retries} attempts: {last_error}"
    )


def _chunk_texts(segments: list[Segment], max_chars: int = 3500) -> list[list[str]]:
    chunks: list[list[str]] = []
    buf: list[str] = []
    size = 0
    for segment in segments:
        line = (segment.text or "").strip()
        if not line:
            continue
        if size + len(line) + 1 > max_chars and buf:
            chunks.append(buf)
            buf = []
            size = 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        chunks.append(buf)
    return chunks


def _existing_entry(session: Session, name: str) -> Entry | None:
    slug = slugify(name)
    entry = session.exec(select(Entry).where(Entry.slug == slug)).first()
    if entry is not None:
        return entry
    alias = session.exec(select(Alias).where(Alias.norm == name.lower())).first()
    if alias is None:
        return None
    return session.get(Entry, alias.entry_id)


def _protected_slugs() -> set[str]:
    slugs = {slugify(row["name"]) for row in SEED}
    for row in load_site_entries():
        slugs.add(slugify(row["name"]))
    return slugs


def purge_model_layer(session: Session) -> dict[str, int]:
    """Drop auto model mentions and glossary rows they created."""
    stale = list(
        session.exec(
            select(Mention).where(
                Mention.detector == MentionDetector.MODEL,
                Mention.status == MentionStatus.DETECTED,
            )
        )
    )
    for row in stale:
        session.delete(row)
    session.commit()

    kept_ids = {
        row.entry_id
        for row in session.exec(select(Mention))
        if row.entry_id is not None
    }
    protected = _protected_slugs()
    removed = 0
    for entry in list(session.exec(select(Entry))):
        if entry.id in kept_ids:
            continue
        if entry.slug in protected:
            continue
        session.exec(delete(Alias).where(Alias.entry_id == entry.id))
        session.delete(entry)
        removed += 1
    session.commit()

    logger.info("Purged model layer: %d mentions, %d junk entries", len(stale), removed)
    return {"mentions": len(stale), "entries": removed}


def annotate_battle(session: Session, video_id: str, *, clear: bool = True) -> int:
    """Write LLM-detected mentions for one battle. Confirmed/rejected stay."""
    battle = session.get(Battle, video_id)
    if battle is None:
        raise ValueError(f"{video_id} is not ingested")

    if clear:
        session.exec(
            delete(Mention).where(
                Mention.video_id == video_id,
                Mention.detector == MentionDetector.MODEL,
                Mention.status == MentionStatus.DETECTED,
            )
        )
        session.commit()

    skip_ids = _skip_ids_for_battle(session, battle)
    emcees = parse_matchup(battle.title).emcees
    skip_names = {name.lower() for name in emcees}

    claimed: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for row in session.exec(select(Mention).where(Mention.video_id == video_id)):
        claimed[row.segment_idx].append((row.char_start, row.char_end))

    segments = list(
        session.exec(
            select(Segment).where(Segment.video_id == video_id).order_by(Segment.idx)
        )
    )
    if not segments:
        return 0

    extracted: list[dict] = []
    for chunk in _chunk_texts(segments):
        extracted.extend(groq_extract(chunk, emcees=emcees))

    aliases: list[tuple[str, int]] = []
    for entity in extracted:
        if entity["name"].lower() in skip_names:
            continue
        entry = _existing_entry(session, entity["name"])
        if entry is None:
            entry = upsert_entry(
                session,
                entity["name"],
                entity["kind"],
                blurb=entity["blurb"] or None,
            )
        elif entity["blurb"] and not (entry.blurb or "").strip():
            entry.blurb = entity["blurb"]
            session.add(entry)
            session.commit()
        if entry.id is None or entry.id in skip_ids:
            continue
        aliases.append((entity["name"], entry.id))

    written = 0
    for segment in segments:
        occupied = list(claimed[segment.idx])
        for hit in find_spans(segment.text, aliases):
            if overlaps(hit.char_start, hit.char_end, occupied):
                continue
            if hit.entry_id in skip_ids:
                continue
            session.add(
                Mention(
                    video_id=video_id,
                    segment_idx=segment.idx,
                    start=segment.start,
                    end=segment.end,
                    entry_id=hit.entry_id,
                    alias=hit.alias,
                    char_start=hit.char_start,
                    char_end=hit.char_end,
                    detector=MentionDetector.MODEL,
                    status=MentionStatus.DETECTED,
                )
            )
            occupied.append((hit.char_start, hit.char_end))
            written += 1

    session.commit()
    logger.info("LLM NER %s: %d mentions from %d names", video_id, written, len(aliases))
    return written


def annotate_all(session: Session) -> dict[str, int]:
    counts: dict[str, int] = {}
    battles = session.exec(
        select(Battle).where(Battle.status == BattleStatus.READY)
    ).all()
    for battle in battles:
        counts[battle.video_id] = annotate_battle(session, battle.video_id)
    return counts
