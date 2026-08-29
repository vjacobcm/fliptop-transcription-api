"""Full-text search across every stored transcript segment.

Not exposed over HTTP. The companion app works within a single battle, so this
exists for offline use via `scripts/search.py` and as the lookup layer for
resolving references a battle makes to lines from earlier battles.

SQLite gets a real FTS5 index over `segment.text`, kept in sync by triggers so
ingest and delete paths need no changes. If the index cannot be built (another
database engine, or a Python built without FTS5) search degrades to a LIKE
scan. That keeps the endpoint working, but it is slower, unranked, and matches
substrings rather than whole tokens, so it returns more hits than FTS5 would.
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text as sql
from sqlmodel import Session

from app.config import settings
from app.db import engine

logger = logging.getLogger(__name__)

FTS_TABLE = "segment_fts"

_CREATE_INDEX = f"""
CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(
    text,
    content='segment',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
)
"""

# Segment.id is an INTEGER PRIMARY KEY, so it doubles as the rowid the
# external-content index points at.
_TRIGGERS = (
    f"""
    CREATE TRIGGER IF NOT EXISTS segment_fts_insert AFTER INSERT ON segment BEGIN
        INSERT INTO {FTS_TABLE}(rowid, text) VALUES (new.id, new.text);
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS segment_fts_delete AFTER DELETE ON segment BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, text)
        VALUES ('delete', old.id, old.text);
    END
    """,
    f"""
    CREATE TRIGGER IF NOT EXISTS segment_fts_update AFTER UPDATE ON segment BEGIN
        INSERT INTO {FTS_TABLE}({FTS_TABLE}, rowid, text)
        VALUES ('delete', old.id, old.text);
        INSERT INTO {FTS_TABLE}(rowid, text) VALUES (new.id, new.text);
    END
    """,
)

# A quoted group is an exact phrase; anything else is a single bare term.
_TERM_RE = re.compile(r'"([^"]*)"|(\S+)')
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)

_index_ready = False


@dataclass
class SearchHit:
    video_id: str
    title: str
    channel: str | None
    source: str | None
    idx: int
    start: float
    end: float
    text: str
    snippet: str


def uses_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


def index_ready() -> bool:
    return _index_ready


def init_search() -> None:
    """Create the FTS index and its triggers, backfilling on first build."""
    global _index_ready

    if not uses_sqlite():
        logger.info("Non-SQLite database; search will use a LIKE scan")
        return

    try:
        with engine.begin() as conn:
            existed = conn.execute(
                sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": FTS_TABLE},
            ).first()

            if not existed:
                conn.execute(sql(_CREATE_INDEX))

            for trigger in _TRIGGERS:
                conn.execute(sql(trigger))

            if not existed:
                conn.execute(
                    sql(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('rebuild')")
                )
                logger.info("Built %s from existing segments", FTS_TABLE)
    except Exception:  # noqa: BLE001 - search is optional, the API still works
        logger.exception("Could not build %s; falling back to LIKE search", FTS_TABLE)
        return

    _index_ready = True


def reindex() -> int:
    """Rebuild the index from the segment table and return the row count."""
    if not _index_ready:
        raise RuntimeError("Full-text index is not available")

    with engine.begin() as conn:
        conn.execute(sql(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('rebuild')"))
        return conn.execute(sql(f"SELECT count(*) FROM {FTS_TABLE}")).scalar_one()


def to_match_expression(query: str) -> str:
    """Turn user input into an FTS5 MATCH expression.

    Every term is emitted as a quoted phrase, so FTS5 operators typed into the
    query cannot change its shape or raise a syntax error. Double quotes still
    mean an exact phrase and a trailing `*` still means prefix search.
    """
    terms: list[str] = []

    for phrase, word in _TERM_RE.findall(query or ""):
        raw = phrase or word
        prefix = bool(word) and word.endswith("*")
        cleaned = " ".join(_PUNCTUATION_RE.sub(" ", raw).split())
        if cleaned:
            terms.append(f'"{cleaned}"*' if prefix else f'"{cleaned}"')

    if not terms:
        raise ValueError("Search query has no usable terms")

    return " AND ".join(terms)


def _filters(video_id: str | None, source: str | None) -> str:
    clauses = ""
    if video_id:
        clauses += " AND s.video_id = :video_id"
    if source:
        clauses += " AND b.source = :source"
    return clauses


def _search_fts(
    session: Session,
    query: str,
    limit: int,
    offset: int,
    video_id: str | None,
    source: str | None,
) -> tuple[list[SearchHit], int]:
    params = {"match": to_match_expression(query)}
    if video_id:
        params["video_id"] = video_id
    if source:
        params["source"] = source

    body = f"""
        FROM {FTS_TABLE}
        JOIN segment s ON s.id = {FTS_TABLE}.rowid
        JOIN battle b ON b.video_id = s.video_id
        WHERE {FTS_TABLE} MATCH :match{_filters(video_id, source)}
    """

    total = session.exec(sql(f"SELECT count(*) {body}"), params=params).scalar_one()

    # bm25 is negative and better matches sort lower, so plain ASC is correct.
    rows = session.exec(
        sql(
            f"""
            SELECT s.video_id, b.title, b.channel, b.source,
                   s.idx, s."start", s."end", s.text,
                   snippet({FTS_TABLE}, 0, '<mark>', '</mark>', '…', 16) AS snippet
            {body}
            ORDER BY bm25({FTS_TABLE}), s.video_id, s.idx
            LIMIT :limit OFFSET :offset
            """
        ),
        params={**params, "limit": limit, "offset": offset},
    ).all()

    return [SearchHit(*row) for row in rows], total


def _search_like(
    session: Session,
    query: str,
    limit: int,
    offset: int,
    video_id: str | None,
    source: str | None,
) -> tuple[list[SearchHit], int]:
    needle = query.strip()
    if not needle:
        raise ValueError("Search query has no usable terms")

    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    params = {"like": f"%{escaped.lower()}%"}
    if video_id:
        params["video_id"] = video_id
    if source:
        params["source"] = source

    body = f"""
        FROM segment s
        JOIN battle b ON b.video_id = s.video_id
        WHERE lower(s.text) LIKE :like ESCAPE '\\'{_filters(video_id, source)}
    """

    total = session.exec(sql(f"SELECT count(*) {body}"), params=params).scalar_one()

    rows = session.exec(
        sql(
            f"""
            SELECT s.video_id, b.title, b.channel, b.source,
                   s.idx, s."start", s."end", s.text, s.text AS snippet
            {body}
            ORDER BY s.video_id, s.idx
            LIMIT :limit OFFSET :offset
            """
        ),
        params={**params, "limit": limit, "offset": offset},
    ).all()

    return [SearchHit(*row) for row in rows], total


def search(
    session: Session,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    video_id: str | None = None,
    source: str | None = None,
) -> tuple[list[SearchHit], int]:
    """Return matching segments and the total number of matches."""
    runner = _search_fts if _index_ready else _search_like
    return runner(session, query, limit, offset, video_id, source)
