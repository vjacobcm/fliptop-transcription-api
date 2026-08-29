"""Glossary matching for companion highlights.

Phase 1 only answers "there is a reference here." Aliases are matched in
segment text, longest first; the two emcees in the current battle are skipped
so the companion does not light up the names already on the thumbnail.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlmodel import Session, delete, select

from app.models import (
    Alias,
    Battle,
    Entry,
    EntryKind,
    Mention,
    MentionDetector,
    MentionStatus,
    Segment,
)
from app.services.titles import parse_matchup

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Two-letter aliases are almost always noise except well-known emcee tags.
_SHORT_OK = frozenset({"gl"})

# Recurring FlipTop furniture plus names that show up in the stored battles.
# Aliases are extra spellings; the canonical name is always an alias too.
SEED: tuple[dict, ...] = (
    {
        "name": "Isabuhay",
        "kind": EntryKind.EVENT,
        "aliases": ("Isabuhay",),
    },
    {
        "name": "Dos Por Dos",
        "kind": EntryKind.EVENT,
        "aliases": ("Dos Por Dos", "2 por 2"),
    },
    {
        "name": "Apolo",
        "kind": EntryKind.PLACE,
        "aliases": ("Apolo", "Apolo Wilts", "Rap Apolo", "Rapolo"),
    },
    {
        "name": "Floodway",
        "kind": EntryKind.PLACE,
        "aliases": ("Floodway",),
    },
    {
        "name": "GL",
        "kind": EntryKind.PERSON,
        "aliases": ("GL",),
    },
    {
        "name": "Hazky",
        "kind": EntryKind.PERSON,
        "aliases": ("Hazky", "Hasky"),
    },
    {
        "name": "Ruffian",
        "kind": EntryKind.PERSON,
        "aliases": ("Ruffian",),
    },
    {
        "name": "Sinagtala",
        "kind": EntryKind.PERSON,
        "aliases": ("Sinagtala",),
    },
    {
        "name": "1ce Water",
        "kind": EntryKind.PERSON,
        "aliases": ("1ce Water", "Ice Water"),
    },
    {
        "name": "Sayadd",
        "kind": EntryKind.PERSON,
        "aliases": ("Sayadd",),
    },
    {
        "name": "Loonie",
        "kind": EntryKind.PERSON,
        "aliases": ("Loonie",),
    },
    {
        "name": "Abra",
        "kind": EntryKind.PERSON,
        "aliases": ("Abra",),
    },
    {
        "name": "Batas",
        "kind": EntryKind.PERSON,
        "aliases": ("Batas",),
    },
    {
        "name": "Shernan",
        "kind": EntryKind.PERSON,
        "aliases": ("Shernan",),
    },
    {
        "name": "Shehyee",
        "kind": EntryKind.PERSON,
        "aliases": ("Shehyee",),
    },
    {
        "name": "BLKD",
        "kind": EntryKind.PERSON,
        "aliases": ("BLKD",),
    },
    {
        "name": "Sinio",
        "kind": EntryKind.PERSON,
        "aliases": ("Sinio",),
    },
)


@dataclass(frozen=True)
class SpanHit:
    entry_id: int
    alias: str
    char_start: int
    char_end: int


def slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    return slug or "entry"


def _usable_alias(alias: str) -> bool:
    text = alias.strip()
    if len(text) < 2:
        return False
    if len(text) == 2:
        return text.lower() in _SHORT_OK
    return True


def _bounded(text: str, start: int, end: int) -> bool:
    """Reject hits that sit inside a longer token (GL in ANGLE, Ice in Twice)."""
    if start > 0 and text[start - 1].isalnum():
        return False
    if end < len(text) and text[end].isalnum():
        return False
    return True


def find_spans(text: str, aliases: list[tuple[str, int]]) -> list[SpanHit]:
    """Greedy longest-alias-wins scan. `aliases` is (label, entry_id)."""
    if not text or not aliases:
        return []

    ranked = sorted(
        ((label, entry_id) for label, entry_id in aliases if _usable_alias(label)),
        key=lambda item: (-len(item[0]), item[0].lower()),
    )
    lower = text.lower()
    claimed = [False] * len(text)
    hits: list[SpanHit] = []

    for label, entry_id in ranked:
        needle = label.lower()
        cursor = 0
        while True:
            index = lower.find(needle, cursor)
            if index < 0:
                break
            end = index + len(needle)
            if _bounded(text, index, end) and not any(claimed[index:end]):
                for pos in range(index, end):
                    claimed[pos] = True
                hits.append(
                    SpanHit(
                        entry_id=entry_id,
                        alias=text[index:end],
                        char_start=index,
                        char_end=end,
                    )
                )
            cursor = index + 1

    hits.sort(key=lambda hit: (hit.char_start, hit.char_end))
    return hits


def upsert_entry(
    session: Session, name: str, kind: str, aliases: list[str] | None = None
) -> Entry:
    slug = slugify(name)
    entry = session.exec(select(Entry).where(Entry.slug == slug)).first()
    if entry is None:
        entry = Entry(slug=slug, name=name, kind=kind)
        session.add(entry)
        session.commit()
        session.refresh(entry)
    elif not entry.kind:
        entry.kind = kind
        session.add(entry)
        session.commit()

    labels = {name, *(aliases or [])}
    for label in labels:
        if not _usable_alias(label):
            continue
        norm = label.lower()
        existing = session.exec(select(Alias).where(Alias.norm == norm)).first()
        if existing is None:
            session.add(Alias(entry_id=entry.id, norm=norm, label=label))
        elif existing.entry_id != entry.id:
            logger.warning(
                "Alias %r already belongs to entry %s; not attaching to %s",
                label,
                existing.entry_id,
                entry.slug,
            )
    session.commit()
    session.refresh(entry)
    return entry


def seed_glossary(session: Session) -> int:
    """Insert the hand list. Idempotent."""
    before = session.exec(select(Entry)).all()
    for row in SEED:
        upsert_entry(session, row["name"], row["kind"], list(row.get("aliases") or ()))
    after = session.exec(select(Entry)).all()
    return len(after) - len(before)


def seed_from_title(session: Session, title: str) -> list[int]:
    """Ensure emcees/event from a battle title exist; return their entry ids."""
    matchup = parse_matchup(title)
    ids: list[int] = []
    for name in matchup.emcees:
        ids.append(upsert_entry(session, name, EntryKind.PERSON).id)
    if matchup.event:
        # "Isabuhay 2018 Semifinals" → still attach to Isabuhay when we can.
        event_name = matchup.event
        for row in SEED:
            if row["kind"] == EntryKind.EVENT and row["name"].lower() in event_name.lower():
                event_name = row["name"]
                break
        ids.append(upsert_entry(session, event_name, EntryKind.EVENT).id)
    return [entry_id for entry_id in ids if entry_id is not None]


def _skip_ids_for_battle(session: Session, battle: Battle) -> set[int]:
    """The people already on the poster are not 'references' in this video."""
    matchup = parse_matchup(battle.title)
    skip: set[int] = set()
    for name in matchup.emcees:
        slug = slugify(name)
        entry = session.exec(select(Entry).where(Entry.slug == slug)).first()
        if entry and entry.id is not None:
            skip.add(entry.id)
        # Title spelling may differ from the seeded slug (Ice Water vs 1ce Water).
        norm = name.lower()
        alias = session.exec(select(Alias).where(Alias.norm == norm)).first()
        if alias:
            skip.add(alias.entry_id)
    return skip


def _alias_catalog(session: Session, skip_ids: set[int]) -> list[tuple[str, int]]:
    rows = session.exec(select(Alias)).all()
    return [(row.label, row.entry_id) for row in rows if row.entry_id not in skip_ids]


def annotate_battle(session: Session, video_id: str) -> int:
    """Replace auto-detected mentions for one battle. Confirmed/rejected stay.

    Returns the number of new detected mentions written.
    """
    battle = session.get(Battle, video_id)
    if battle is None:
        raise ValueError(f"{video_id} is not ingested")

    seed_glossary(session)
    seed_from_title(session, battle.title)

    session.exec(
        delete(Mention).where(
            Mention.video_id == video_id,
            Mention.detector == MentionDetector.GLOSSARY,
            Mention.status == MentionStatus.DETECTED,
        )
    )
    session.commit()

    kept = list(
        session.exec(select(Mention).where(Mention.video_id == video_id))
    )
    blocked: set[tuple[int, int, int, int]] = {
        (row.segment_idx, row.entry_id, row.char_start, row.char_end) for row in kept
    }

    skip_ids = _skip_ids_for_battle(session, battle)
    catalog = _alias_catalog(session, skip_ids)
    segments = list(
        session.exec(
            select(Segment).where(Segment.video_id == video_id).order_by(Segment.idx)
        )
    )

    written = 0
    for segment in segments:
        for hit in find_spans(segment.text, catalog):
            key = (segment.idx, hit.entry_id, hit.char_start, hit.char_end)
            if key in blocked:
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
                    detector=MentionDetector.GLOSSARY,
                    status=MentionStatus.DETECTED,
                )
            )
            blocked.add(key)
            written += 1

    session.commit()
    logger.info(
        "Annotated %s: %d mentions (skipped %d battling-emcee entries)",
        video_id,
        written,
        len(skip_ids),
    )
    return written


def annotate_all(session: Session) -> dict[str, int]:
    seed_glossary(session)
    counts: dict[str, int] = {}
    battles = session.exec(select(Battle)).all()
    for battle in battles:
        counts[battle.video_id] = annotate_battle(session, battle.video_id)
    return counts


def mentions_for(
    session: Session,
    video_id: str,
    *,
    at: float | None = None,
    include_rejected: bool = False,
) -> list[tuple[Mention, Entry]]:
    statement = (
        select(Mention, Entry)
        .where(Mention.entry_id == Entry.id)
        .where(Mention.video_id == video_id)
        .order_by(Mention.start, Mention.char_start)
    )
    if not include_rejected:
        statement = statement.where(Mention.status != MentionStatus.REJECTED)
    if at is not None:
        statement = statement.where(Mention.start <= at, Mention.end >= at)

    rows = list(session.exec(statement))
    if at is None:
        return rows

    # Overlapping segments can carry the same name; the companion only needs
    # one mark per entry at the playhead.
    seen: set[int] = set()
    unique: list[tuple[Mention, Entry]] = []
    for mention, entry in rows:
        if entry.id in seen:
            continue
        seen.add(entry.id)
        unique.append((mention, entry))
    return unique
